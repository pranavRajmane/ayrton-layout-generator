import pytest
import time
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
PLATE = Path(__file__).resolve().parents[1] / "reference_parts" / "plate" / "design.json"


@pytest.fixture(autouse=True)
def plate_copy(tmp_path, monkeypatch):
    """Designs save themselves after every change: tests edit a copy, never the reference part."""
    import shutil
    d = tmp_path / "plate"
    shutil.copytree(PLATE.parent, d)
    monkeypatch.setitem(globals(), "PLATE_COPY", d / "design.json")


def open_plate():
    r = client.post("/api/designs/open", json={"path": str(globals()["PLATE_COPY"])})
    assert r.status_code == 200, r.text
    return r.json()


def test_elements_listed():
    ids = [e["id"] for e in client.get("/api/elements").json()]
    assert "dummy_box" in ids


def test_open_edit_undo_meshes():
    s = open_plate()
    sid = s["id"]
    assert {m["name"] for m in s["meshes"]} == {"part", "gating.sprue_1"}
    glb = client.get(s["meshes"][1]["url"])
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"

    t = time.perf_counter()
    r = client.post(f"/api/designs/{sid}/patch",
                    json=[{"op": "replace", "path": "/elements/0/params/length", "value": 120.0}])
    dt = time.perf_counter() - t
    assert r.status_code == 200
    st = r.json()
    assert st["elements"][0]["params"]["length"] == 120.0 and st["can_undo"]
    assert dt < 2.0, f"edit -> regenerated meshes took {dt:.2f} s"

    st = client.post(f"/api/designs/{sid}/undo").json()
    assert st["elements"][0]["params"]["length"] == 80.0 and st["can_redo"]


def test_bad_edit_refused():
    sid = open_plate()["id"]
    r = client.post(f"/api/designs/{sid}/patch",
                    json=[{"op": "replace", "path": "/units/length", "value": "inch"}])
    assert r.status_code == 422


def test_out_of_range_param_is_an_issue_not_a_crash():
    sid = open_plate()["id"]
    st = client.post(f"/api/designs/{sid}/patch",
                     json=[{"op": "replace", "path": "/elements/0/params/length", "value": -3.0}]).json()
    assert [i["rule"] for i in st["issues"] if i["severity"] == "error"] == ["element.params"]
    assert st["elements"][0]["built"] is False


def test_export(tmp_path):
    sid = open_plate()["id"]
    r = client.post(f"/api/designs/{sid}/export", json={"out_dir": str(tmp_path)})
    assert r.status_code == 200 and (tmp_path / "project/geometry/cast.stl").exists()


def _glb_json(raw: bytes) -> dict:
    import json
    import struct
    n = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20:20 + n])


def test_gltf_nodes_carry_only_the_axis_change():
    """Placed solids must reach the viewer with their placement in the vertices
    (OCCT's writer misplaces located shapes)."""
    s = open_plate()
    for m in s["meshes"]:
        g = _glb_json(client.get(m["url"]).content)
        for node in g["nodes"]:
            assert node.get("translation", [0, 0, 0]) == [0, 0, 0]
            assert node.get("rotation") in (None, [-0.7071067811865475, 0, 0, 0.7071067811865475])


def test_orient_moves_gate_ports_along():
    s = open_plate()
    sid = s["id"]
    before = s["part"]["ports"]["gate_face"]
    st = client.post(f"/api/designs/{sid}/part/orient", json={"op": "rotate", "axis": "z", "angle_deg": 90}).json()
    after = st["part"]["ports"]["gate_face"]
    assert after["normal"] == before["normal"]                       # a turn about Z keeps the face on top
    assert [i["rule"] for i in st["issues"] if i["severity"] == "error"] == []
    st = client.post(f"/api/designs/{sid}/part/orient", json={"op": "parting_at", "z": 10.0}).json()
    assert st["part"]["bbox"][1][2] == 0.0 or abs(st["part"]["bbox"][1][2]) < 1e-9
    assert st["part"]["ports"]["gate_face"]["origin"][2] == 0.0 or abs(st["part"]["ports"]["gate_face"]["origin"][2]) < 1e-9
    st = client.post(f"/api/designs/{sid}/undo").json()
    assert abs(st["part"]["bbox"][1][2] - 10.0) < 1e-9


def test_pick_add_port_and_moldcheck():
    sid = open_plate()["id"]
    pk = client.post(f"/api/designs/{sid}/part/pick", json={"point": [100.2, 0, 0]}).json()
    assert pk["normal"] == [1.0, 0.0, 0.0] or abs(pk["normal"][0] - 1) < 1e-9
    st = client.post(f"/api/designs/{sid}/part/gate_port", json={"point": [100.2, 0, 0]}).json()
    assert "gate_1" in st["part"]["ports"]
    mc = client.post(f"/api/designs/{sid}/part/moldcheck").json()
    assert mc["faces"] and mc["min_draft_deg"] is None


def test_calc_block_and_element_removal():
    s = open_plate()
    assert s["calc"]["readouts"]["yield_pct"] is not None
    st = client.post(f"/api/designs/{s['id']}/elements/remove", json={"instance": "sprue_1"}).json()
    assert st["elements"] == [] and st["document"]["connections"] == []
    st = client.post(f"/api/designs/{s['id']}/undo").json()
    assert [e["instance"] for e in st["elements"]] == ["sprue_1"]


def test_mirror_endpoint():
    s = open_plate()
    st = client.post(f"/api/designs/{s['id']}/ops/mirror",
                     json={"instances": ["sprue_1"], "origin": [0, 0, 0], "normal": [1, 0, 0]}).json()
    assert {e["instance"] for e in st["elements"]} == {"sprue_1", "sprue_1_m"}


def test_guided_setup_from_a_part_file(tmp_path):
    import shutil
    part = shutil.copy(PLATE.with_name("plate.step"), tmp_path / "plate.step")       # autosave writes beside it
    st = client.post("/api/designs/new", json={"part": str(part)}).json()
    sid = st["id"]
    assert st["box"] is None and st["path"].endswith("plate.gating.json")
    st = client.post(f"/api/designs/{sid}/workflow/box",
                     json={"length": 600, "width": 400, "height": 300, "parting": 150}).json()
    assert st["box"]["top"] == 150 and st["part"]["bbox"][0][2] == -st["part"]["bbox"][1][2]   # centred
    st = client.post(f"/api/designs/{sid}/workflow/move_part", json={"delta": [0, 0, -10]}).json()
    assert abs(st["part"]["bbox"][1][2]) < 1e-6                                     # top on the parting plane
    bad = client.post(f"/api/designs/{sid}/workflow/sprue", json={"x": 900, "y": 0, "d_top": 30, "d_bottom": 24})
    assert bad.status_code == 422 and "outside the box" in bad.text
    client.post(f"/api/designs/{sid}/workflow/sprue", json={"x": -200, "y": 0, "d_top": 30, "d_bottom": 24})
    st = client.post(f"/api/designs/{sid}/workflow/runner",
                     json={"side": "cope", "w_parting": 30, "w_far": 20, "height": 25,
                           "points": [[-120, 0, 0]]}).json()
    assert {m["name"] for m in st["meshes"]} >= {"gating.sprue", "gating.runner"}
    sprue = next(e for e in st["elements"] if e["instance"] == "sprue")
    assert sprue["params"]["length"] == 150                                          # cope runner: to the parting plane
    # a hand edit of the runner height keeps the sprue on its floor
    i = [e["id"] for e in st["document"]["elements"]].index("runner")
    st = client.post(f"/api/designs/{sid}/patch",
                     json=[{"op": "replace", "path": f"/elements/{i}/params/side", "value": "drag"}]).json()
    sprue = next(e for e in st["elements"] if e["instance"] == "sprue")
    assert sprue["params"]["length"] == 175
    assert not [x for x in st["issues"] if x["rule"].startswith("joint.")]


def test_upload_a_part_and_open_it(tmp_path, monkeypatch):
    import api.main
    monkeypatch.setattr(api.main, "UPLOADS", tmp_path / "parts")
    data = PLATE.with_name("plate.step").read_bytes()
    r = client.post("/api/upload?name=plate.step", content=data, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    p = r.json()["path"]
    assert p.endswith("plate.step")
    st = client.post("/api/designs/new", json={"part": p}).json()
    assert st["part"]["volume_mm3"] > 0
    assert client.post("/api/upload?name=notes.txt", content=b"x").status_code == 422


def test_runner_gated_where_the_part_reaches_out_below_the_plane(tmp_path):
    """A step under the parting plane sticks out 30 mm further than the part at the plane: a drag runner
    running in meets the step first, and the ingate goes there."""
    from build123d import Box, Location, export_step
    part = Box(100, 100, 20).moved(Location((0, 0, 10))) + Box(160, 100, 5).moved(Location((0, 0, -2.5)))   # step z -5..0
    export_step(part, tmp_path / "stepped.step")
    st = client.post("/api/designs/new", json={"part": str(tmp_path / "stepped.step")}).json()
    sid = st["id"]
    client.post(f"/api/designs/{sid}/workflow/box", json={"length": 500, "width": 300, "height": 300, "parting": 150})
    # the box centres the part: put it back with the step's top 2.5 mm under the parting plane
    st = client.get(f"/api/designs/{sid}").json()
    lo, hi = st["part"]["bbox"]
    client.post(f"/api/designs/{sid}/workflow/move_part", json={"delta": [0, 0, -7.5 - lo[2]]})   # step -7.5..-2.5
    client.post(f"/api/designs/{sid}/workflow/sprue", json={"x": -200, "y": 0, "d_top": 30, "d_bottom": 24})
    st = client.post(f"/api/designs/{sid}/workflow/runner",
                     json={"side": "drag", "w_parting": 30, "w_far": 20, "height": 25, "points": [[0, 0, 0]],
                           "gate": {"width": 40, "thickness": 8, "length": 20, "side": "drag"}}).json()
    g = next(e for e in st["document"]["elements"] if e["type"] == "ingate")
    assert g["params"]["waypoints"][1][0] == pytest.approx(-80, abs=0.01)      # the step's edge, not the plate's (-50)


def test_autosave_and_reopen(tmp_path):
    import json, shutil
    part = shutil.copy(PLATE.with_name("plate.step"), tmp_path / "plate.step")
    st = client.post("/api/designs/new", json={"part": str(part)}).json()
    saved = tmp_path / "plate.gating.json"
    assert not saved.exists() and st["saved"] is None                       # nothing changed yet
    st = client.post(f"/api/designs/{st['id']}/workflow/box",
                     json={"length": 400, "width": 300, "height": 200, "parting": 100}).json()
    assert saved.exists() and st["saved"] and json.loads(saved.read_text())["mold"]["flask"]["size_mm"] == [400, 300, 200]
    st = client.post(f"/api/designs/{st['id']}/undo").json()                 # undo is saved too
    assert json.loads(saved.read_text())["mold"]["flask"] is None
    st = client.post(f"/api/designs/{st['id']}/redo").json()
    again = client.post("/api/designs/new", json={"part": str(part)}).json()  # the same part: its design comes back
    assert again["resumed"] and again["box"]["size"] == [400, 300, 200]
    fresh = client.post("/api/designs/new", json={"part": str(part), "fresh": True}).json()
    assert not fresh["resumed"] and fresh["box"] is None and json.loads(saved.read_text())["mold"]["flask"] is None


def test_ingates_follow_their_part(tmp_path):
    """A gated runner into the plate; the plate moves 30 mm along the runner: the runner is cut and gated
    again where it now meets the plate. The sprue stays where it is."""
    import shutil
    part = shutil.copy(PLATE.with_name("plate.step"), tmp_path / "plate.step")
    sid = client.post("/api/designs/new", json={"part": str(part)}).json()["id"]
    client.post(f"/api/designs/{sid}/workflow/box", json={"length": 600, "width": 400, "height": 300, "parting": 150})
    client.post(f"/api/designs/{sid}/workflow/sprue", json={"x": -250, "y": 0, "d_top": 30, "d_bottom": 24})
    gate = {"width": 40, "thickness": 8, "length": 20, "side": "drag"}
    st = client.post(f"/api/designs/{sid}/workflow/runner",
                     json={"side": "drag", "w_parting": 30, "w_far": 20, "height": 25, "points": [[0, 0, 0]],
                           "gate": gate}).json()
    g = next(e for e in st["document"]["elements"] if e["type"] == "ingate")["params"]["waypoints"]
    assert g[1][0] == pytest.approx(-100, abs=0.01)                          # the plate's left edge
    st = client.post(f"/api/designs/{sid}/workflow/move_part", json={"delta": [30, 0, 0]}).json()
    g = next(e for e in st["document"]["elements"] if e["type"] == "ingate")["params"]["waypoints"]
    assert g[1][0] == pytest.approx(-70, abs=0.01) and g[0][0] == pytest.approx(-90, abs=0.01)
    sprue = next(e for e in st["document"]["elements"] if e["id"] == "sprue")
    assert sprue["anchor"]["frame"]["origin"][0] == -250
    assert not [i for i in st["issues"] if i["severity"] == "error"]
    st = client.post(f"/api/designs/{sid}/undo").json()                      # the move and the re-gating: one step
    g = next(e for e in st["document"]["elements"] if e["type"] == "ingate")["params"]["waypoints"]
    assert g[1][0] == pytest.approx(-100, abs=0.01)
