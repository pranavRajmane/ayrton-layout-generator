"""What the 2026-11-11 review found, each as the user would meet it, through the API on copies of the plate."""
import json
import shutil
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
PLATE = Path(__file__).resolve().parents[1] / "reference_parts" / "plate" / "plate.step"    # 200 x 150 x 20
BOX = {"length": 600, "width": 400, "height": 300, "parting": 150}
GATE = {"width": 40, "thickness": 8, "length": 20, "side": "drag"}


def post(sid, ep, **body):
    r = client.post(f"/api/designs/{sid}/workflow/{ep}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def errors(st):
    return [(i["rule"], i["message"]) for i in st["issues"] if i["severity"] in ("error", "warning")
            and i["rule"] != "system.pouring_inlet"]


def el(st, inst):
    return next(e for e in st["document"]["elements"] if e["id"] == inst)


@pytest.fixture
def plate(tmp_path):
    part = shutil.copy(PLATE, tmp_path / "plate.step")
    sid = client.post("/api/designs/new", json={"part": str(part)}).json()["id"]
    post(sid, "box", **BOX)                                                  # plate z -10..10, centred
    return sid, Path(part)


# ---- saving ---------------------------------------------------------------------------------------

def test_an_unreadable_design_is_kept_not_overwritten(plate):
    sid, part = plate
    saved = part.with_name("plate.gating.json")
    good = saved.read_text()
    saved.write_text(good[:200])                                             # cut off mid-write
    st = client.post("/api/designs/new", json={"part": str(part)}).json()
    assert not st["resumed"] and "could not be read" in st["notes"][0]
    kept = list(part.parent.glob("plate.gating.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_text() == good[:200]
    post(st["id"], "box", **BOX)
    assert json.loads(saved.read_text())["mold"]["flask"]                    # the new design saves as usual


def test_first_save_keeps_the_design_as_opened(plate):
    sid, part = plate
    saved = part.with_name("plate.gating.json")
    before = saved.read_text()
    sid2 = client.post("/api/designs/new", json={"part": str(part)}).json()["id"]
    post(sid2, "sprue", x=-250, y=0, d_top=30, d_bottom=24)
    assert part.with_name("plate.gating.json.bak").read_text() == before
    assert not list(part.parent.glob(".plate.gating.json.*.tmp"))           # no temporary file left


def test_starting_over_keeps_the_old_design(plate):
    sid, part = plate
    before = part.with_name("plate.gating.json").read_text()
    st = client.post("/api/designs/new", json={"part": str(part), "fresh": True}).json()
    assert part.with_name("plate.gating.previous.json").read_text() == before and st["notes"]


def test_unwritable_save_and_export_are_reported(plate, tmp_path):
    sid, _ = plate
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    r = client.post(f"/api/designs/{sid}/save", json={"path": str(blocker / "x.json")})
    assert r.status_code == 422 and "could not save" in r.text
    post(sid, "sprue", x=-250, y=0, d_top=30, d_bottom=24)
    post(sid, "runner", side="drag", w_parting=30, w_far=20, height=25, points=[[0, 0, 0]], gate=GATE)
    r = client.post(f"/api/designs/{sid}/export", json={"out_dir": str(blocker / "case")})
    assert r.status_code == 422 and "could not write" in r.text


def test_export_drops_regions_of_removed_elements(plate, tmp_path):
    sid, _ = plate
    post(sid, "sprue", x=-250, y=0, d_top=30, d_bottom=24)
    post(sid, "runner", side="drag", w_parting=30, w_far=20, height=25, points=[[0, 0, 0]], gate=GATE)
    post(sid, "feeder", kind="top", x=50, y=0, d=40, height=80)
    out = tmp_path / "case"
    assert client.post(f"/api/designs/{sid}/export", json={"out_dir": str(out)}).status_code == 200
    assert (out / "project" / "regions" / "feeder.feeder_1.stl").exists()
    post(sid, "remove", instance="feeder_1")
    assert client.post(f"/api/designs/{sid}/export", json={"out_dir": str(out)}).status_code == 200
    assert not (out / "project" / "regions" / "feeder.feeder_1.stl").exists()
    assert (out / "project" / "regions" / "gating.runner.stl").exists()


def test_overlapping_requests_on_one_design(plate):
    sid, _ = plate
    codes = []
    ts = [threading.Thread(target=lambda dx=dx: codes.append(
        client.post(f"/api/designs/{sid}/workflow/move_part", json={"delta": [dx, 0, 0]}).status_code))
        for dx in (5, -5, 5, -5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes == [200] * 4
    st = client.get(f"/api/designs/{sid}").json()
    assert st["parts"][0]["bbox"][0][0] == pytest.approx(-100, abs=1e-6)    # the moves summed, none lost


# ---- what sits on a part goes with it -----------------------------------------------------------

def test_top_feeder_and_vent_follow_their_part(plate):
    sid, _ = plate
    post(sid, "feeder", kind="top", x=50, y=0, d=40, height=80)
    post(sid, "vent", x=-50, y=40, d=10)
    st = post(sid, "move_part", delta=[30, 0, 15])
    assert el(st, "feeder_1")["anchor"]["frame"]["origin"] == pytest.approx([80, 0, 25])
    v = el(st, "vent_1")
    assert v["anchor"]["frame"]["origin"] == pytest.approx([-20, 40, 25])
    assert v["params"]["length"] == pytest.approx(150 - 25)                 # still to the box top
    assert not errors(st)
    st = client.post(f"/api/designs/{sid}/undo").json()                      # one undo step
    assert el(st, "feeder_1")["anchor"]["frame"]["origin"] == pytest.approx([50, 0, 10])


def test_side_feeder_and_neck_follow_their_part(plate):
    sid, _ = plate
    post(sid, "feeder", kind="side", x=0, y=150, d=60, height=90, neck={"width": 30, "thickness": 10, "side": "cope"})
    st = post(sid, "move_part", delta=[20, -30, 0])
    assert el(st, "feeder_1")["anchor"]["frame"]["origin"] == pytest.approx([20, 120, 0])
    assert el(st, "neck_1")["params"]["waypoints"][1] == pytest.approx([20, 45, 0], abs=0.01)
    assert not errors(st)


def test_only_what_sits_on_the_moved_part_goes(tmp_path):
    part = shutil.copy(PLATE, tmp_path / "plate.step")
    sid = client.post("/api/designs/new", json={"part": str(part)}).json()["id"]
    post(sid, "box", length=900, width=400, height=300, parting=150)
    st = post(sid, "add_copy")
    cid, bb = st["parts"][1]["id"], st["parts"][1]["bbox"]
    cx = (bb[0][0] + bb[1][0]) / 2
    post(sid, "feeder", kind="top", x=0, y=0, d=40, height=80)               # on the main plate
    post(sid, "feeder", kind="top", x=cx, y=0, d=40, height=80)              # on the copy
    st = post(sid, "move_part", delta=[0, 20, 0], cavity=cid)
    assert el(st, "feeder_1")["anchor"]["frame"]["origin"][:2] == pytest.approx([0, 0])
    assert el(st, "feeder_2")["anchor"]["frame"]["origin"][:2] == pytest.approx([cx, 20])
    st = post(sid, "remove_part", cavity=cid)                                # it takes its feeder with it
    assert {e["id"] for e in st["document"]["elements"]} == {"feeder_1"}


def test_removing_a_part_takes_the_ingate_drawn_to_it(tmp_path):
    part = shutil.copy(PLATE, tmp_path / "plate.step")
    sid = client.post("/api/designs/new", json={"part": str(part)}).json()["id"]
    post(sid, "box", length=900, width=400, height=300, parting=150)
    st = post(sid, "add_copy")
    cid, bb = st["parts"][1]["id"], st["parts"][1]["bbox"]
    post(sid, "sprue", x=-300, y=0, d_top=30, d_bottom=24)
    post(sid, "runner", side="drag", w_parting=30, w_far=20, height=25, points=[[-300, 150, 0], [bb[0][0] + 100, 150, 0]])
    post(sid, "ingate", runner="runner", start=[bb[0][0] + 100, 150, 0], end=[bb[0][0] + 100, 75, 0],
         side="drag", width=30, thickness=8)
    st = post(sid, "remove_part", cavity=cid)
    assert not [e for e in st["document"]["elements"] if e["type"] == "ingate"]


# ---- seated on curved tops -----------------------------------------------------------------------

def test_feeder_and_vent_off_the_crown_of_a_round_part(tmp_path):
    from build123d import Cylinder, Location, export_step
    f = tmp_path / "bar.step"
    export_step(Cylinder(60, 200).moved(Location((0, 0, 0), (0, 1, 0), 90)), str(f))   # lying along X
    sid = client.post("/api/designs/new", json={"part": str(f)}).json()["id"]
    post(sid, "box", **BOX)
    st = post(sid, "feeder", kind="top", x=0, y=30, d=40, height=80)
    fe = el(st, "feeder_1")
    top = (60 ** 2 - 10 ** 2) ** 0.5                                         # highest point under it, at y = 10
    assert fe["anchor"]["frame"]["origin"][2] == pytest.approx(top, abs=0.05)
    assert fe["params"]["embed"] == pytest.approx(top - (60 ** 2 - 50 ** 2) ** 0.5 + 1, abs=0.1)
    st = post(sid, "vent", x=-60, y=-35, d=10)
    assert not errors(st)


def test_a_feeder_over_the_edge_is_told(plate):
    sid, _ = plate
    st = post(sid, "feeder", kind="top", x=95, y=0, d=40, height=80)          # plate ends at x = 100
    assert any("hangs over" in n for n in st["notes"])


# ---- ingate size for every runner ----------------------------------------------------------------

def test_ingate_size_for_every_runner_in_one_step(plate):
    sid, _ = plate
    post(sid, "sprue", x=-250, y=0, d_top=30, d_bottom=24)
    post(sid, "runner", side="drag", w_parting=30, w_far=20, height=25, points=[[0, 0, 0]], gate=GATE)
    post(sid, "runner", side="drag", w_parting=30, w_far=20, height=25, points=[[-250, 120, 0], [0, 120, 0], [0, 0, 0]],
         gate=GATE)
    st = post(sid, "gate", gate={**GATE, "width": 50, "length": 30})
    gates = [e for e in st["document"]["elements"] if e["type"] == "ingate"]
    assert len(gates) == 2 and all(g["params"]["width"] == 50 for g in gates)
    assert all(e["params"]["gate_length"] == 30 for e in st["document"]["elements"] if e["type"] == "runner")
    st = client.post(f"/api/designs/{sid}/undo").json()
    assert all(e["params"]["width"] == 40 for e in st["document"]["elements"] if e["type"] == "ingate")
    st = post(sid, "gate", gate=None)
    assert not [e for e in st["document"]["elements"] if e["type"] == "ingate"]
    assert all(e["params"]["gate_length"] == 0 for e in st["document"]["elements"] if e["type"] == "runner")
