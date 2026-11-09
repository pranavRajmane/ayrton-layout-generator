import numpy as np
import pytest
from build123d import Box, Location, export_step

from core import moldcheck, orient
from core.document import DesignDocument, empty
from core.frames import apply_point
from core.part import load_part
from core.pick import gate_port, pick_face


def part_doc(tmp_path, shape, **part):
    export_step(shape, tmp_path / "p.step")
    d = empty("t")
    d["part"] = {"source": "p.step", **part}
    return DesignDocument(d, tmp_path)


def test_rotation_basics():
    r = orient.rotation("z", 90)
    assert np.allclose(r @ (1, 0, 0), (0, 1, 0))
    for v in [(1, 0, 0), (0, 0, -1), (0.3, -0.2, 0.9), (0, 0, 1)]:
        a = orient.aligning(v, (0, 0, 1))
        assert np.allclose(a @ (np.asarray(v) / np.linalg.norm(v)), (0, 0, 1))
        assert np.allclose(a.T @ a, np.eye(3))


def test_up_vector_and_face_down_keep_the_centre():
    c = (10, 20, 30)
    t = orient.up_vector(None, (1, 0, 0), c)          # part +X becomes up
    m = orient.to_matrix(t)
    assert np.allclose(m[:3, :3] @ (1, 0, 0), (0, 0, 1))
    assert np.allclose(apply_point(m, c), c)
    t = orient.face_down(t, (0, 1, 0), c)
    m2 = orient.to_matrix(t)
    assert np.allclose(m2[:3, :3] @ m[:3, :3].T @ (0, 1, 0), (0, 0, -1))


def test_parting_at_shifts_down():
    t = orient.parting_at({"translation": [0, 0, 5]}, 12.0)
    assert t["translation"] == [0, 0, -7]


def test_pick_on_a_turned_part(tmp_path):
    rot = orient.rotation("x", 90).tolist()               # part +Y -> mold +Z
    doc = part_doc(tmp_path, Box(100, 40, 20), transform={"rotation": rot, "translation": [0, 0, 0]})
    pm = load_part(doc)
    p = pick_face(pm, (0, 0, 20.3))                       # just above the part's +Y face, now on top
    assert p["kind"] == "plane" and p["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert p["point"] == pytest.approx([0, 0, 20]) and p["area_mm2"] == pytest.approx(2000)
    assert len(p["mesh"]) % 9 == 0 and p["mesh"]
    port = gate_port(p, "g1")
    assert port["frame"]["normal"] == pytest.approx([0, 0, -1]) and port["face"] == p["face"]


def test_plate_split_at_mid_height(tmp_path):
    pm = load_part(part_doc(tmp_path, Box(100, 80, 20)))  # z from -10 to 10: split through the middle
    res = moldcheck.check(pm, min_draft_deg=1.0)
    walls = [f for f in res["faces"] if f["side"] == "cope/drag"]
    assert len(walls) == 4 and all(f["min_draft_deg"] == pytest.approx(0, abs=1e-6) for f in walls)
    assert {i.rule for i in res["issues"]} == {"mold.draft"} and len(res["issues"]) == 4
    top = [f for f in res["faces"] if f["side"] == "cope" and f["min_draft_deg"] > 89]
    assert len(top) == 1 and top[0]["undercut_fraction"] == 0


def test_mushroom_overhang_is_undercut(tmp_path):
    shape = Box(20, 20, 30).moved(Location((0, 0, 15))) + Box(60, 60, 10).moved(Location((0, 0, 35)))
    pm = load_part(part_doc(tmp_path, shape))
    res = moldcheck.check(pm)                             # no draft limit: undercuts only
    under = sorted((f for f in res["faces"] if f["undercut_fraction"] > 0), key=lambda f: f["min_draft_deg"])
    # the cap's underside, and the stem's four walls: sand beside the stem is trapped under the cap
    assert len(under) == 5 and under[0]["min_draft_deg"] == pytest.approx(-90)
    assert all(f["undercut_fraction"] == 1.0 for f in under) and all("mesh" in f for f in under)
    assert {i.rule for i in res["issues"]} == {"mold.undercut"}
