"""Phase 1: modulus field, feeding zones, hot spots, zone adjacency.

Analytic bodies against their moduli, junction benchmarks (zones = members + junction node, the
engineer's choice of 2026-11-19), the voxeliser, the thinnest-wall estimate, determinism, and the
phase run on a GatingJob.  Convergence (halving the voxel size) is in test_modulus_convergence.py."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from layouts import recipe as rc
from layouts.job import GatingJob, canonical_json, new
from layouts.modulus import analyse, run, triangulate, voxelize

from . import shapes as S

ST = rc.settings()["modulus"]
TOL = ST["analytic_tolerance_pct"] / 100
ROOT = Path(__file__).resolve().parents[2]


def _faces(shape, pred):
    return [i for i, f in enumerate(shape.faces()) if pred(f)]


def _one_zone(res):
    zs = res.section["zones"]
    assert len(zs) == 1, [(z["id"], z["modulus"]) for z in zs]
    return zs[0]


# ---- analytic bodies ----------------------------------------------------------------------
@pytest.mark.parametrize("name, shape, expect", [
    ("cube a=60: a/6", S.cube(60.0), 60.0 / 6),
    ("sphere d=60: d/6", S.sphere(60.0), 60.0 / 6),
])
def test_compact_bodies(name, shape, expect):
    z = _one_zone(analyse(shape, ST))
    assert z["modulus"] == pytest.approx(expect, rel=TOL), name


def test_long_cylinder_d_over_4():
    """Ends do not cool (they stand for the rest of an infinitely long bar)."""
    cyl = S.cylinder(40.0, 400.0)
    ends = _faces(cyl, lambda f: abs(abs(f.center().X - 0.3) - 200.0) < 1e-6)
    assert len(ends) == 2
    z = _one_zone(analyse(cyl, ST, skip_faces=ends))
    assert z["modulus"] == pytest.approx(40.0 / 4, rel=TOL)


def test_finite_cylinder_matches_its_own_v_over_a():
    r, length = 20.0, 400.0
    z = _one_zone(analyse(S.cylinder(2 * r, length), ST))
    exact = (math.pi * r * r * length) / (2 * math.pi * r * length + 2 * math.pi * r * r)
    assert z["modulus"] == pytest.approx(exact, rel=TOL)
    assert exact == pytest.approx(10.0 * 0.952, rel=1e-3)       # 4.8 % short of d/4: why ends are cut


def test_large_plate_t_over_2():
    pl = S.plate(20.0, 400.0)
    edges = _faces(pl, lambda f: abs(f.normal_at().Z) < 0.5)
    assert len(edges) == 4
    z = _one_zone(analyse(pl, ST, skip_faces=edges))
    assert z["modulus"] == pytest.approx(20.0 / 2, rel=TOL)


def test_finite_plate_matches_its_own_v_over_a():
    t, a = 20.0, 400.0
    z = _one_zone(analyse(S.plate(t, a), ST))
    assert z["modulus"] == pytest.approx(a * a * t / (2 * a * a + 4 * a * t), rel=TOL)


def test_r_over_3_is_a_map_not_a_modulus():
    """The local field r/3 is exact for the sphere and short in a plate: zones use V/A."""
    res = analyse(S.sphere(60.0), ST)
    hs = res.section["hot_spots"][0]
    assert hs["local_modulus"] == pytest.approx(hs["radius"] / 3)


# ---- junction benchmarks ------------------------------------------------------------------
JUNCTION_CASES = [(k, deg) for k in S.JUNCTIONS for deg in (0.0, 30.0)]


@pytest.mark.parametrize("kind, deg", JUNCTION_CASES, ids=[f"{k}-{int(d)}deg" for k, d in JUNCTION_CASES])
def test_junction_zones_and_hot_spot(kind, deg):
    fn, n_expected = S.JUNCTIONS[kind]
    shape = fn() if deg == 0 else S.turned(fn(), deg)
    res = analyse(shape, ST)
    sec = res.section
    assert len(sec["zones"]) == n_expected, [(z["id"], round(z["modulus"], 2)) for z in sec["zones"]]
    # the hottest spot sits at the junction (the thick half of the stepped plate)
    jx, jy = S.JUNCTION_POINT[kind]
    c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    jx, jy = c * jx - s_ * jy, s_ * jx + c * jy
    hs = sec["hot_spots"][0]
    tol = 40.0 if kind == "step" else 10.0
    assert math.hypot(hs["position"][0] - jx, hs["position"][1] - jy) < tol, hs
    # ... and in the zone holding the junction, which has the largest modulus
    hot_zone = next(z for z in sec["zones"] if z["id"] == hs["zone"])
    assert hot_zone["modulus"] == max(z["modulus"] for z in sec["zones"])
    # arms of the same section agree with each other
    if kind in ("L", "T", "X"):
        arms = sorted(z["modulus"] for z in sec["zones"] if z["id"] != hs["zone"])
        assert arms[-1] / arms[0] < 1 + TOL * 2


def test_zone_graph_is_consistent():
    sec = analyse(S.t_junction(), ST).section
    ids = {z["id"] for z in sec["zones"]}
    hub = sec["hot_spots"][0]["zone"]
    area = {(e["a"], e["b"]): e["interface_area"] for e in sec["adjacency"]}
    assert all(a in ids and b in ids and a < b for a, b in area)
    # every arm touches the junction; arms touch each other at most in a sliver at an inside corner,
    # where the junction's sphere only just reaches
    to_hub = {z: area[(min(z, hub), max(z, hub))] for z in ids - {hub}}
    for (a, b), x in area.items():
        if hub not in (a, b):
            assert x < 0.15 * min(to_hub[a], to_hub[b]), (a, b, x, to_hub)
    # volumes add up to the exact solid
    assert sum(z["volume"] for z in sec["zones"]) == pytest.approx(sec["volume"], rel=1e-9)


def test_stepped_plate_moduli():
    """Thick half 160 x 200 x 40 cooling on top, bottom and its three free edges; thin half likewise."""
    sec = analyse(S.stepped_plate(), ST).section
    thick, thin = sorted(sec["zones"], key=lambda z: -z["modulus"])
    v1, a1 = 160 * 200 * 40, 2 * 160 * 200 + 2 * 160 * 40 + 200 * 40
    assert thick["modulus"] == pytest.approx(v1 / a1, rel=0.05)
    assert thin["modulus"] < thick["modulus"]


# ---- voxels, walls, determinism -----------------------------------------------------------
def test_voxeliser_volume_and_parting_plane():
    shape = S.sphere(60.0)
    surf = triangulate(shape, ST["mesh_relative_deflection"], ST["mesh_angular_deflection_rad"])
    for h in (3.0, 1.5):
        vp = voxelize(surf, h)
        assert vp.volume_mm3 == pytest.approx(shape.volume, rel=0.03)
        assert abs(vp.origin[2] / h - round(vp.origin[2] / h)) < 1e-9          # z = 0 on a voxel boundary


def test_voxeliser_ray_through_shared_edges():
    """A box meshed as two triangles a face: columns through the diagonals count once (no streaks)."""
    surf = triangulate(S.cube(60.0), 0.1, 0.2)
    vp = voxelize(surf, 2.0)
    filled = vp.metal.sum(axis=2)
    cols = filled[filled > 0]
    assert cols.min() == cols.max() == 30


def test_thinnest_wall():
    assert analyse(S.plate(20.0, 400.0), ST).section["min_wall"] == pytest.approx(20.0, abs=0.05)


def test_bores_do_not_fake_thin_walls():
    from core.part import load_source
    res = analyse(load_source(ROOT / "tests/reference_parts/cored_part/cored_part.step").shape, ST)
    assert res.section["min_wall"] == pytest.approx(10.0, abs=0.3)          # the bridge over the bore
    assert not res.issues
    a, b = res.section["zones"]                                             # two walls, mirror images
    assert a["modulus"] == pytest.approx(b["modulus"], rel=0.01)
    assert a["volume"] == pytest.approx(b["volume"], rel=0.01)


def test_deterministic():
    a = analyse(S.l_junction(), ST).section
    b = analyse(S.l_junction(), ST).section
    assert canonical_json(a) == canonical_json(b)


# ---- the phase on a job -------------------------------------------------------------------
def test_phase_1_on_a_job(tmp_path):
    part = ROOT / "tests/reference_parts/flanged_housing/flanged_housing.step"
    data = new("hub", part, "green_sand", "grey_iron",
               {"length": 600.0, "width": 600.0, "cope_height": 200.0, "drag_height": 200.0})
    job = GatingJob(data, tmp_path / "hub.job.json")
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=Path(__file__).parent / "recipes"))
    res = run(job)
    job.save()
    again = GatingJob.load(tmp_path / "hub.job.json")
    assert again.status()["modulus"] == "done"
    sec = again.data["modulus"]
    assert sec["field"] == "hub.modulus.npz"
    f = np.load(tmp_path / sec["field"])
    assert f["zone"].shape == tuple(sec["grid"]["shape"])
    assert int(f["zone"].max()) == len(sec["zones"])
    assert sec["volume"] == pytest.approx(916057, rel=1e-3)
    assert again.data["phases"]["modulus"]["counts"]["zones"] == len(res.section["zones"])
