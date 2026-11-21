"""Phase 4: sizing and symmetric runner routing, on hand-built layouts of a 100 x 60 x 40 block."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest
from build123d import Box, export_step

from calculators.hydraulics import G as GRAV
from layouts import ingates as ig
from layouts import modulus
from layouts import recipe as rc
from layouts import runners as rn
from layouts.job import GatingJob, new

RECIPES = Path(__file__).parent / "recipes"
FLASK = {"length": 1000.0, "width": 800.0, "cope_height": 200.0, "drag_height": 200.0}


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    out = tmp_path_factory.mktemp("blk")
    part = out / "block.step"
    export_step(Box(100, 60, 40), str(part))
    job = GatingJob(new("blk", part, "green_sand", "grey_iron", FLASK), out / "blk.job.json")
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=RECIPES))
    modulus.run(job)
    job.save()
    ig.run(job)
    job.save()
    return job, rn.context(job)


def gate(job):
    """The candidate in the middle of the -y face."""
    return next(c for c in job.data["ingate_candidates"]["candidates"]
                if c["normal"] == [0.0, -1.0] and abs(c["location"][0]) < 1e-6)


def cav(i, rot, t, orbit=0):
    return {"index": i, "rotation_deg": float(rot % 360), "mirrored": False,
            "translation": [float(t[0]), float(t[1])], "orbit": orbit}


def half_turn(c, centre, index):
    """The cavity turned 180 degrees about a point."""
    t = 2 * np.asarray(centre, float) - np.asarray(c["translation"])
    return cav(index, c["rotation_deg"] + 180, t, c["orbit"])


def layout(job, lid, template, cavs, sym="c2", n=None, ingate=None):
    return {"id": lid, "n": n or len(cavs), "family": {"straight": "row", "radial": "radial"}.get(template, "grid"),
            "arrangement": {}, "symmetry": sym, "rotation_deg": 0, "ingate": ingate or gate(job)["id"],
            "sprue": [0.0, 0.0], "template": template, "cavities": cavs, "key": lid}


def straight2(job):
    a = cav(0, 0, (60, 90))                  # 60 mm along the runner: its ingate clears the sprue
    return layout(job, "straight2", "straight", [a, half_turn(a, (0, 0), 1)])


def h4(job):
    a = cav(0, 0, (150, 100))
    b = half_turn(a, (150, 0), 1)
    b["orbit"] = 1
    return layout(job, "h4", "h", [a, b, half_turn(a, (0, 0), 2), half_turn(b, (0, 0), 3)])


def tree8(job):
    """An H-tree: S -> (+-240, 0) -> (+-240, +-120) -> two cavities each, their ingates continuing the
    last arms (cavities turned 90 degrees so the ingate faces along x)."""
    a = cav(0, 90, (120, 120))
    cs = [a, half_turn(a, (240, 120), 1)]
    cs += [half_turn(c, (240, 0), 2 + i) for i, c in enumerate(cs)]
    cs += [half_turn(c, (0, 0), 4 + i) for i, c in enumerate(cs)]
    for i, c in enumerate(cs):
        c["index"], c["orbit"] = i, i % 4
    return layout(job, "tree8", "tree", cs)


def radial4(job, r=120.0):
    return layout(job, "radial4", "radial", [cav(i, 90 * i, rn._rot((0, r), np.zeros(2), 90 * i)) for i in range(4)],
                  sym="c4")


def route(ctx, lay):
    return rn.route(lay, ctx)


# ---- sizing against the hand formulas ------------------------------------------------------------
def test_sizing_matches_the_hand_calculation(base):
    job, ctx = base
    rec = route(ctx, straight2(job))
    s = rec["sizing"]
    v = job.data["recipe"]["values"]
    rho, cd = v["density_kg_m3"], v["discharge_coefficient"]
    W = 2 * 100 * 60 * 40 * 1e-9 * rho
    assert s["castings_kg"] == pytest.approx(W)
    G = s["poured_kg"]
    t = 2.0 * math.sqrt(G)                                           # the test recipe's formula
    assert s["pour_time_s"] == pytest.approx(t, rel=1e-6)
    H = 200 + 40 + 2.5                                               # cope + basin - gate level (-t/2)
    P, C = 20 + 2.5, 40.0
    assert s["head_mm"] == pytest.approx(H) and s["effective_head_mm"] == pytest.approx(H - P ** 2 / (2 * C))
    choke = G / (rho * t * cd * math.sqrt(2 * GRAV * s["effective_head_mm"] / 1000)) * 1e6
    assert s["choke_mm2"] == pytest.approx(choke, rel=1e-6)
    assert s["sprue_area_mm2"] == pytest.approx(choke, rel=1e-6)
    assert s["runner_area_mm2"] == pytest.approx(choke * 1.3, rel=1e-6)
    assert s["ingate_area_mm2"] == pytest.approx(choke * 1.1, rel=1e-6)
    # G is the castings plus what the routed gating holds
    segs = {x["id"]: x for x in rec["segments"]}
    run_vol = sum(np.linalg.norm(np.subtract(x["points"][1], x["points"][0])) * x["section"]["area"]
                  for x in rec["segments"] if x["role"] == "runner")
    gate_vol = sum(np.linalg.norm(np.subtract(x["points"][1], x["points"][0])) * x["section"]["area"]
                   for x in rec["segments"] if x["role"] == "ingate")
    a_b, a_t = s["sprue"]["bottom"]["area"], s["sprue"]["top"]["area"]
    sprue_vol = 200 / 3 * (a_b + a_t + math.sqrt(a_b * a_t))
    assert G == pytest.approx(W + rho * 1e-9 * (run_vol + gate_vol + sprue_vol), rel=1e-5)
    # sections: two arms, A_r / 2 each; ingate 5 mm thick, width from its share
    assert s["runner_levels"][0]["section"]["area"] == pytest.approx(s["runner_area_mm2"] / 2, rel=1e-6)
    assert s["ingate"]["dims"]["height"] == 5.0
    assert s["ingate"]["dims"]["width"] * 5.0 == pytest.approx(s["ingate_area_mm2"] / 2, rel=1e-6)
    tr = s["runner_levels"][0]["section"]["dims"]
    # a drag runner: widest on the parting plane (its top), 15 % narrower at its bottom, as high as wide
    assert tr["height"] == pytest.approx(tr["top"]) and tr["bottom"] == pytest.approx(0.85 * tr["top"])
    assert "sprue" in segs


def test_runner_area_is_shared_at_every_level(base):
    job, ctx = base
    s = route(ctx, tree8(job))["sizing"]
    assert [lv["branches"] for lv in s["runner_levels"]] == [2, 4, 8]
    for lv in s["runner_levels"]:
        assert lv["branches"] * lv["section"]["area"] == pytest.approx(s["runner_area_mm2"], rel=1e-6)


# ---- templates --------------------------------------------------------------------------------
@pytest.mark.parametrize("make, n_arms", [(straight2, 2), (h4, 6), (tree8, 14), (radial4, 4)])
def test_templates_route_symmetric_and_pass(base, make, n_arms):
    job, ctx = base
    rec = route(ctx, make(job))
    assert all(c["passed"] for c in rec["validation"]), [c for c in rec["validation"] if not c["passed"]]
    runners = [x for x in rec["segments"] if x["role"] == "runner"]
    assert len(runners) == n_arms
    paths = rec["sizing"]["path_lengths_mm"]
    assert max(paths) - min(paths) <= 0.005 * min(paths)
    codes = {c["code"] for c in rec["validation"]}
    assert {"runner.path_lengths", "runner.clear_of_cavities", "runner.no_crossing", "runner.symmetric",
            "gating.ratio", "sprue.clear_of_cavities", "runner.inside_flask", "ingate.freezes_first"} <= codes


def test_ingates_meet_the_cavities_at_the_candidate(base):
    job, ctx = base
    lay = h4(job)
    rec = route(ctx, lay)
    g = gate(job)
    for x in (x for x in rec["segments"] if x["role"] == "ingate"):
        c = lay["cavities"][x["cavity"]]
        q = rn.fp.place_point(g["location"], ctx.scale, c["rotation_deg"], c["translation"])
        assert np.allclose(x["points"][1][:2], q, atol=1e-6)


# ---- rejections -------------------------------------------------------------------------------
def test_an_ingate_under_the_sprue_is_rejected(base):
    job, ctx = base
    lay = dict(straight2(job), cavities=[cav(0, 0, (0, 90)), cav(1, 180, (0, -90))])
    bad = {c["code"] for c in route(ctx, lay)["validation"] if not c["passed"]}
    assert "ingate.clear_of_sprue" in bad


def test_off_centre_ingates_on_one_straight_runner_are_unequal(base):
    job, ctx = base
    g = next(c for c in job.data["ingate_candidates"]["candidates"]
             if c["normal"] == [0.0, -1.0] and c["location"][0] > 10)
    a, b = cav(0, 0, (-80, 120)), cav(1, 0, (80, 120))
    lay = layout(job, "row4", "straight", [a, b, half_turn(a, (0, 0), 2), half_turn(b, (0, 0), 3)], ingate=g["id"])
    rec = route(ctx, lay)
    bad = {c["code"] for c in rec["validation"] if not c["passed"]}
    assert bad == {"runner.path_lengths"}


@pytest.mark.parametrize("edit, code", [
    (lambda job: dict(straight2(job), cavities=[cav(0, 0, (60, 40)), cav(1, 180, (-60, -40))]), "runner.too_close"),
    (lambda job: dict(h4(job), n=2, cavities=h4(job)["cavities"][:2]), "runner.h_count"),
    (lambda job: dict(h4(job), cavities=[dict(c, translation=[c["translation"][0] + (7 if c["index"] == 0 else 0),
                                                                c["translation"][1]]) for c in h4(job)["cavities"]]),
     "runner.unbalanced"),
    (lambda job: dict(radial4(job), cavities=[dict(c, rotation_deg=(c["rotation_deg"] + 90) % 360)
                                              for c in radial4(job)["cavities"]]), "runner.ingate_not_facing_sprue"),
])
def test_unroutable_layouts_are_rejected_with_a_reason(base, edit, code):
    job, ctx = base
    with pytest.raises(rn.Reject) as e:
        route(ctx, edit(job))
    assert e.value.code == code and e.value.message


def test_failed_checks_reject_in_the_phase(base, tmp_path):
    job0, ctx = base
    job = GatingJob(job0.data, tmp_path / "j.json")
    near = radial4(job, r=60.0)                     # the cavities crowd the sprue
    near["id"] = near["key"] = "near"
    small = straight2(job)
    small["cavities"] = [cav(0, 0, (460, 90)), cav(1, 180, (-460, -90))]   # the runner runs on into the wall
    small["id"] = small["key"] = "wall"
    job.finish("layouts", [straight2(job), h4(job), tree8(job), radial4(job), near, small])
    ok, rej = rn.run(job, ctx)
    assert [r["layout"] for r in ok] == ["straight2", "h4", "tree8", "radial4"]
    why = {r["id"]: {x["code"] for x in r["reasons"]} for r in rej}
    assert "sprue.clear_of_cavities" in why["near"]
    assert "runner.inside_flask" in why["wall"]
    job.save()
    again = GatingJob.load(tmp_path / "j.json")
    assert again.counts() == {"enumerated": 6, "geometric_ok": 6, "rejected": 2, "accepted": 4}


def test_deterministic(base):
    job, ctx = base
    a = json.dumps(route(ctx, tree8(job)), sort_keys=True)
    b = json.dumps(route(ctx, copy.deepcopy(tree8(job))), sort_keys=True)
    assert a == b
