"""Phase 3: cavity layouts in the flask, on a 100 x 60 x 40 block (mirror-symmetric) and the same block
with an off-centre boss (not)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
import shapely
from build123d import Box, Pos, export_step

from layouts import arrange as ar
from layouts import footprint as fp
from layouts import ingates as ig
from layouts import modulus
from layouts import recipe as rc
from layouts import runners as rn
from layouts.job import GatingJob, new

RECIPES = Path(__file__).parent / "recipes"
FLASK = {"length": 1000.0, "width": 800.0, "cope_height": 200.0, "drag_height": 200.0}


def make_job(out: Path, name: str, shape, flask=FLASK) -> GatingJob:
    part = out / f"{name}.step"
    export_step(shape, str(part))
    job = GatingJob(new(name, part, "green_sand", "grey_iron", flask), out / f"{name}.job.json")
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=RECIPES))
    modulus.run(job)
    job.save()
    ig.run(job)
    job.save()
    return job


@pytest.fixture(scope="module")
def block(tmp_path_factory):
    job = make_job(tmp_path_factory.mktemp("block"), "block", Box(100, 60, 40))
    return job, rn.context(job)


@pytest.fixture(scope="module")
def bossed(tmp_path_factory):
    job = make_job(tmp_path_factory.mktemp("bossed"), "bossed", Box(100, 60, 40) + Pos(25, 5, 30) * Box(30, 30, 20))
    return job, rn.context(job)


def gate(job, x=0.0):
    """The candidate on the -y face at x."""
    return next(c["id"] for c in job.data["ingate_candidates"]["candidates"]
                if c["normal"] == [0.0, -1.0] and abs(c["location"][0] - x) < 1e-6)


def run(job, ctx, **limits):
    lim = dict(job.data["inputs"]["limits"])
    lim.update(limits)
    job.set_inputs(limits=lim)
    return ar.run(job, ctx)


def placed(job, ctx, lay):
    return [fp.place(ctx.outline, ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"])
            for c in lay["cavities"]]


# ---- counts against a hand count ----------------------------------------------------------------
def test_row_counts_match_the_hand_count(block):
    """Rows of the block (scaled 101 x 60.6) along a straight runner, ingate in the middle of the long
    face: the pitch is 101 + 25 (cavity clearance), so a row of k spans 126 (k - 1) + 101.  Inside the
    flask less its 30 mm wall: runner along x, 940 long -> k <= 7; runner along y, 740 -> k <= 6.  So
    N = 1, 2, 4 ... 14 with the runner along x and 1, 2 ... 12 along y; turning the part 180 or 270
    degrees gives the same layouts (a half-turn of the flask)."""
    job, ctx = block
    lays, rej = run(job, ctx, families=["row"], ingates=[gate(job)], n_min=None, n_max=None)
    by_rot = {}
    for lay in lays:
        by_rot.setdefault(lay["rotation_deg"], []).append(lay["n"])
    assert sorted(by_rot) == [0, 90]
    assert sorted(by_rot[0]) == [1, 2, 4, 6, 8, 10, 12, 14]
    assert sorted(by_rot[90]) == [1, 2, 4, 6, 8, 10, 12]
    walls = sorted(r["params"]["n"] for r in rej if r["reasons"][0]["code"] == "flask.wall")
    assert walls == [14, 16]                                    # the first row too long each way
    pitches = [lay["arrangement"]["pitch_mm"] for lay in lays if lay["n"] >= 4]
    assert all(126.0 <= p <= 126.0 + 0.7 for p in pitches)       # + the search's conservative outline
    assert job.data["phases"]["layouts"]["counts"]["duplicates"] > 0


def test_every_layout_holds_its_clearances_on_the_exact_outlines(block):
    job, ctx = block
    lays, _ = run(job, ctx, families=["row", "grid", "radial"], ingates=[gate(job)], n_max=8)
    v = job.data["recipe"]["values"]["layout"]["clearance"]
    inner = shapely.box(-500, -400, 500, 400).buffer(-v["flask_wall_mm"], join_style="mitre")
    fams = set()
    for lay in lays:
        fams.add(lay["family"])
        polys = placed(job, ctx, lay)
        assert inner.contains(shapely.union_all(polys)), lay["id"]
        for i in range(len(polys)):
            for j in range(i + 1, len(polys)):
                assert polys[i].distance(polys[j]) >= v["cavity_cavity_mm"] - 1e-6, (lay["id"], i, j)
        assert ar.symmetry_error(lay, shapely.union_all(polys)) <= 0.01, lay["id"]
    assert fams == {"row", "grid", "radial"}
    assert len({lay["key"] for lay in lays}) == len(lays)


def test_phase_4_routes_what_phase_3_places(block):
    job, ctx = block
    run(job, ctx, families=["grid", "radial"], ingates=[gate(job)], n_max=8)
    ok, rej = rn.run(job, ctx)
    templates = {r["template"] for r in ok}
    assert {"h", "tree", "radial"} <= templates
    assert not rej, [(r["id"], r["reasons"]) for r in rej]


# ---- what does not fit ---------------------------------------------------------------------------
def test_a_part_that_does_not_fit_gives_no_layouts_and_says_why(tmp_path):
    shallow = dict(FLASK, cope_height=40.0)                 # the block rises 20.2 mm; 50 mm of sand above
    job = make_job(tmp_path, "shallow", Box(100, 60, 40), shallow)
    lays, rej = ar.run(job, rn.context(job))
    assert lays == []
    assert [x["code"] for r in rej for x in r["reasons"]] == ["flask.cope_height"]
    narrow = make_job(tmp_path, "narrow", Box(100, 60, 40), dict(FLASK, length=150.0, width=110.0))
    lays, rej = ar.run(narrow, rn.context(narrow))
    assert lays == [] and rej
    assert all(any(x["code"] == "flask.wall" for x in r["reasons"]) for r in rej)


# ---- mirrors ---------------------------------------------------------------------------------------
def test_mirrored_layouts_only_for_a_mirror_symmetric_part(block, bossed):
    job, ctx = block
    lays, _ = run(job, ctx, families=["row"], ingates=[gate(job, 20.0)], rotations_deg=[0], n_max=2)
    assert {lay["symmetry"] for lay in lays} == {"c1", "c2", "d1"}
    d1 = next(lay for lay in lays if lay["symmetry"] == "d1")
    assert sum(c["mirrored"] for c in d1["cavities"]) == 1
    ok, rej = rn.run(job, ctx)
    assert not rej
    job2, ctx2 = bossed
    lays2, _ = run(job2, ctx2, families=["row"], ingates=[job2.data["ingate_candidates"]["candidates"][0]["id"]],
                   rotations_deg=[0], n_max=2)
    assert lays2 and not any(lay["symmetry"].startswith("d") or any(c["mirrored"] for c in lay["cavities"])
                             for lay in lays2)
    assert "not mirror-symmetric" in job2.data["phases"]["layouts"]["issues"][0]


# ---- control -----------------------------------------------------------------------------------------
def test_too_many_combinations_stop_with_the_count(block):
    job, ctx = block
    with pytest.raises(ar.CountExceeded) as e:
        run(job, ctx, families=None, ingates=None, rotations_deg=None, n_max=None, count_threshold=10)
    assert e.value.count > 10 and e.value.by
    assert job.status()["layouts"] == "failed"
    assert str(e.value.count) in job.data["phases"]["layouts"]["error"]


def test_deterministic(block):
    job, ctx = block
    a, ra = run(job, ctx, families=["row"], ingates=[gate(job)], n_max=6, count_threshold=None)
    b, rb = run(job, ctx, families=["row"], ingates=[gate(job)], n_max=6, count_threshold=None)
    assert json.dumps([a, ra], sort_keys=True) == json.dumps([b, rb], sort_keys=True)
