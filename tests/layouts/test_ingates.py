"""Phase 2: ingate candidates on the parting line."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from build123d import Axis, Box, Cylinder, Pos, Vertex, chamfer

from layouts import ingates as ig
from layouts import recipe as rc
from layouts.job import GatingJob, JobError, new
from layouts.modulus import analyse

ROOT = Path(__file__).resolve().parents[2]
RECIPES = Path(__file__).parent / "recipes"
ST = rc.settings()
VALUES = json.loads((RECIPES / "green_sand_grey_iron.json").read_text())
TOL = ST["ingates"]["parting_tolerance_mm"]


def values(**ingate):
    v = copy.deepcopy(VALUES)
    v["layout"]["ingate"].update(ingate)
    return v


def plate_boss():
    """A 200 x 120 x 30 plate across the parting plane, a boss on top, a bore through both (a core)."""
    return Box(200, 120, 30) + Pos(0, 0, 35) * Cylinder(25, 40) - Cylinder(10, 200)


def bore_near_edge():
    """A bore 25 mm in from the -y side: straight ahead of an ingate there, within reach."""
    return Box(200, 120, 30) - Pos(0, -35, 0) * Cylinder(8, 100)


def slot_behind_wall():
    """A through slot leaving a 4 mm wall behind the -y face."""
    return Box(200, 120, 30) - Pos(0, -46, 0) * Box(60, 20, 100)


def chamfered():
    """Plate with its four upright corners chamfered 8 mm: four faces too short for an ingate."""
    b = Box(200, 120, 30)
    return chamfer(b.edges().filter_by(Axis.Z), 8)


@lru_cache(maxsize=None)
def analysed(name):
    shape = {"plate_boss": plate_boss, "bore": bore_near_edge, "slot": slot_behind_wall,
             "chamfered": chamfered}[name]()
    res = analyse(shape, ST["modulus"])
    return shape, res


def find(name, vals=None, tags=()):
    shape, res = analysed(name)
    return ig.find(shape, res.section, ig.field_of(res), vals or VALUES, ST["ingates"], tags)


# ---- where candidates are --------------------------------------------------------------------
def test_candidates_lie_on_the_parting_line_with_outward_normals():
    shape, _ = analysed("plate_boss")
    cands, disc, issues = find("plate_boss")
    assert len(cands) >= 20 and not disc
    assert any("cores" in m for m in issues)                    # the bore gives no candidates
    loops = ig.cut(shape, -TOL, ST["ingates"]["outline_step_mm"])
    for c in cands:
        x, y, z = c["location"]
        assert z == 0.0
        assert shape.distance_to(Vertex(x, y, 0.0)) <= TOL + 1e-6, c["id"]
        n = np.array(c["normal"])
        assert abs(np.linalg.norm(n) - 1) < 1e-6
        p = np.array([x, y])
        assert not ig.inside(p + 1.0 * n, loops).any() and ig.inside(p - 1.0 * n, loops).any(), c["id"]
    sides = {tuple(np.round(c["normal"], 6)) for c in cands}
    assert sides == {(0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)}
    assert all(np.hypot(*c["location"][:2]) > 50 for c in cands)        # none at the bore


def test_merged_candidates_are_at_least_the_merge_distance_apart():
    cands, _, _ = find("plate_boss")
    d = VALUES["layout"]["ingate"]["merge_distance_mm"]
    loc = np.array([c["location"][:2] for c in cands])
    gaps = np.linalg.norm(loc[:, None] - loc[None], axis=2) + np.eye(len(loc)) * 1e9
    assert gaps.min() >= d - 1e-6
    merged = [c for c in cands if c["merged_from"]]
    assert merged and all(len(c["merged_from"]) >= 2 for c in merged)   # the corners


def test_ingate_freezes_first_everywhere():
    cands, _, _ = find("plate_boss")
    assert all(c["ingate_modulus"] < c["zone_modulus"] for c in cands)
    sec = cands[0]["section"]
    w, t = sec["dims"]["width"], sec["dims"]["height"]
    assert (t, w) == (5.0, 20.0)                                  # thickness, aspect x thickness
    assert cands[0]["ingate_modulus"] == pytest.approx(w * t / (2 * (w + t)))


def test_too_heavy_an_ingate_is_discarded_with_its_reason():
    cands, disc, _ = find("plate_boss", values(thickness_mm=28.0, aspect=4.0))   # bar modulus 11.2
    assert not cands
    assert disc and all(d["reasons"] == ["ingate_not_thinner"] for d in disc)


def test_tagged_faces_give_no_candidates():
    shape, _ = analysed("plate_boss")
    cands, _, _ = find("plate_boss")
    face = next(c["contact_face"] for c in cands if c["normal"] == [0.0, -1.0])
    again, disc, _ = find("plate_boss", tags=[{"face": face, "tag": "machined"}])
    assert all(c["contact_face"] != face for c in again)
    assert any(d["reasons"] == ["tagged_face"] and d["contact_face"] == face for d in disc)
    assert len(again) < len(cands)


def test_short_faces_are_discarded():
    cands, disc, _ = find("chamfered")
    short = [d for d in disc if "short_contact" in d["reasons"]]
    assert len(short) == 4 and all(d["contact_length"] < 15 for d in short)
    assert all(c["contact_length"] >= 15 for c in cands)


# ---- flags and rules -------------------------------------------------------------------------
def test_a_core_straight_ahead_is_flagged():
    cands, _, _ = find("bore")
    front = [c for c in cands if c["normal"] == [0.0, -1.0]]
    w = VALUES["layout"]["ingate"]["aspect"] * VALUES["layout"]["ingate"]["thickness_mm"]
    hit = [c for c in front if abs(c["location"][0]) < 8 + w / 2]          # the stream's width meets the bore
    miss = [c for c in front if abs(c["location"][0]) > 8 + w / 2]
    assert hit and all(c["flags"]["impinges_core"] for c in hit)
    assert miss and not any(c["flags"]["impinges_core"] for c in miss)
    assert not any(c["flags"]["impinges_core"] for c in cands if c["normal"] != [0.0, -1.0])


def test_a_thin_section_straight_ahead_is_flagged():
    # the 17 mm ligament between the -y face and the bore reads 21-26 mm in the thickness field at
    # 3 mm voxels (a sphere centred beside the bore, where the ligament widens, covers it), the plate
    # 30 mm: against a 25 mm limit
    cands, _, _ = find("bore", values(thin_wall_mm=25.0))
    front = [c for c in cands if c["normal"] == [0.0, -1.0]]
    w = VALUES["layout"]["ingate"]["aspect"] * VALUES["layout"]["ingate"]["thickness_mm"]
    ahead = [c for c in front if abs(c["location"][0]) < 8 + w / 4]          # the stream's middle half meets the bore
    clear = [c for c in front if abs(c["location"][0]) > 8 + w / 4 + 10]
    assert ahead and all(c["flags"]["impinges_thin_wall"] for c in ahead)
    assert clear and not any(c["flags"]["impinges_thin_wall"] for c in clear)
    plain, _, _ = find("bore")                                       # 6 mm limit: nothing is that thin
    assert not any(c["flags"]["impinges_thin_wall"] for c in plain)


def test_a_wall_lighter_than_the_ingate_gets_none():
    # a 4 mm wall in front of a slot is its own zone, lighter than the 20 x 5 ingate: no candidate there
    cands, disc, _ = find("slot")
    assert not [c for c in cands if c["normal"] == [0.0, -1.0] and abs(c["location"][0]) < 30]
    assert [d for d in disc if d["reasons"] == ["ingate_not_thinner"] and abs(d["location"][0]) < 30]


def test_rule_tags_come_from_the_recipe():
    _, res = analysed("bore")
    cands, _, _ = find("bore", values(rules=["feeds_heaviest_zone", "no_impingement"]))
    heavy = max(z["modulus"] for z in res.section["zones"])
    for c in cands:
        assert ("feeds_heaviest_zone" in c["rule_tags"]) == (c["zone_modulus"] >= heavy - 1e-9)
        clean = not (c["flags"]["impinges_core"] or c["flags"]["impinges_thin_wall"])
        assert ("no_impingement" in c["rule_tags"]) == clean
    none, _, _ = find("bore", values(rules=[]))
    assert all(c["rule_tags"] == [] for c in none)
    with pytest.raises(ValueError, match="ingate rule"):
        find("bore", values(rules=["feeds_by_magic"]))


def test_cope_side_ingates():
    cands, _, _ = find("plate_boss", values(side="cope"))
    assert cands and all(c["location"][2] == 0.0 for c in cands)
    s = ig.stub(cands[0], values(side="cope"))
    bb = s.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-6) and bb.max.Z == pytest.approx(5.0, abs=1e-6)


def test_no_metal_on_the_ingate_side():
    part = Pos(0, 0, -15) * Box(200, 120, 30)                         # wholly in the drag
    res = analyse(part, ST["modulus"])
    cands, disc, issues = ig.find(part, res.section, ig.field_of(res), values(side="cope"), ST["ingates"])
    assert not cands and not disc and "no metal" in issues[0]


# ---- the stub --------------------------------------------------------------------------------
def test_every_stub_unions_cleanly_with_the_part():
    shape, _ = analysed("plate_boss")
    cands, _, _ = find("plate_boss")
    for c in cands:
        assert ig.stub_problems(shape, ig.stub(c, VALUES)) == [], c["id"]


def test_deterministic():
    a = find("bore")
    _, res = analysed("bore")
    b = ig.find(bore_near_edge(), res.section, ig.field_of(res), VALUES, ST["ingates"])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---- the phase on a job ----------------------------------------------------------------------
def test_phase_2_on_a_job(tmp_path):
    from layouts import modulus

    part = ROOT / "tests/reference_parts/flanged_housing/flanged_housing.step"
    lowered = {"rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], "translation": [0.0, 0.0, -20.0]}
    data = new("hub", part, "green_sand", "grey_iron",
               {"length": 600.0, "width": 600.0, "cope_height": 200.0, "drag_height": 200.0},
               part_transform=lowered)                    # the housing spans z 0..100: part of it into the drag
    job = GatingJob(data, tmp_path / "hub.job.json")
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=RECIPES))
    with pytest.raises(JobError, match="needs modulus"):
        ig.run(job)
    modulus.run(job)
    job.save()
    cands, disc = ig.run(job)
    job.save()
    again = GatingJob.load(tmp_path / "hub.job.json")
    sec = again.data["ingate_candidates"]
    assert again.status()["ingate_candidates"] == "done"
    assert sec["candidates"] == cands and sec["discarded"] == disc
    counts = again.data["phases"]["ingate_candidates"]["counts"]
    assert counts["candidates"] == len(cands) and counts["discarded"] == len(disc)
    zones = {z["id"] for z in again.data["modulus"]["zones"]}
    assert cands and all(c["target_zone"] in zones for c in cands)
