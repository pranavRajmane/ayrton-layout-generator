"""The rule-based seed generator on synthetic parts (values in tests/seeds/synthetic.py are test values)."""
import json

import pytest

from seeds.config import ConfigError, FoundryProfile, load_knobs
from seeds.feeding import build_feeding_graph
from seeds.generate import Ctx, _hot_spots, generate, generate_seeds
from seeds import validity
from seeds.report import diversity, format_report
from seeds.voxel import VoxelPart
from tests.seeds.synthetic import KNOBS, PARTS, knobs, profile, write_step

H = 2.0


@pytest.fixture(scope="module")
def parts():
    out = {}
    for name in PARTS:
        vp = VoxelPart.from_boxes(PARTS[name], H, part_id=name)
        out[name] = (vp, build_feeding_graph(vp))
    return out


def risers(d):
    return [e for e in d["elements"] if e["type"] == "riser"]


# ---- 1. one thick block ------------------------------------------------------------

def test_block_one_hot_spot_and_riser_modulus(parts):
    vp, g = parts["block"]
    assert len(g.hot_spots()) == 1
    zone = g.zones[g.hot_spots()[0]]
    assert zone.modulus_mm == pytest.approx(10.0, rel=0.02)          # 60 mm cube: a / 6
    designs = generate_seeds(vp, g, profile(), 12, seed=3, knobs=knobs())
    assert designs
    lo = KNOBS["modulus_factor"]["range"][0]
    for d in designs:
        rs = risers(d)
        assert len(rs) >= 1
        for r in rs:
            assert r["params"]["modulus_mm"] >= zone.modulus_mm * lo - 1e-9
            assert r["params"]["modulus_mm"] >= r["params"]["target_modulus_mm"] - 1e-9


# ---- 2. two blocks and a thin plate ----------------------------------------------------

def test_two_blocks_two_hot_spots_two_demands(parts):
    vp, g = parts["two_blocks"]
    hot = g.hot_spots()
    assert len(hot) == 2
    plate = next(z for z in g.zones.values() if z.id not in hot)
    assert plate.modulus_mm < min(g.zones[h].modulus_mm for h in hot)
    demand = {h: g.fed_volume(h) for h in hot}
    for h in hot:                                     # each block feeds itself and half the plate
        assert demand[h] == pytest.approx(g.zones[h].volume_mm3 + plate.volume_mm3 / 2, rel=1e-9)
    run = generate(vp, g, profile(), 15, seed=5, knobs=knobs(merge_shared_risers={"range": [0.0, 0.0]}))
    assert run.designs
    for d in run.designs:
        rs = risers(d)
        assert len(rs) == 2
        assert sorted(z for r in rs for z in r["attached_zones"]) == sorted(f"c0.{h}" for h in hot)
        for r in rs:
            (z,) = r["attached_zones"]
            assert r["params"]["fed_volume_mm3"] == pytest.approx(demand[z.split(".")[1]])


# ---- 3. two bosses close together ---------------------------------------------------------

def test_bosses_some_shared_some_not(parts):
    vp, g = parts["bosses"]
    assert len(g.hot_spots()) == 2
    designs = generate_seeds(vp, g, profile(), 20, seed=1, knobs=knobs())
    shared = [d for d in designs if any(len(r["attached_zones"]) == 2 for r in risers(d))]
    assert 0 < len(shared) < len(designs)
    for d in shared:                                  # sized for the combined demand
        r = next(r for r in risers(d) if len(r["attached_zones"]) == 2)
        assert r["params"]["fed_volume_mm3"] == pytest.approx(sum(g.fed_volume(h) for h in g.hot_spots()))


def test_shared_feeder_across_neighbouring_cavities(parts, tmp_path):
    from core.registry import REGISTRY
    from seeds.build import build_design
    REGISTRY.discover("elements")
    vp, g = parts["slab"]
    prof = profile(cavity_count=2, flask_size_mm=[900.0, 600.0, 300.0])
    k = knobs(merge_shared_risers={"range": [1.0, 1.0]}, riser_style={"choices": ["side"]},
              cavity_gap_mm={"range": [110.0, 140.0]}, feeding_distance_factor={"range": [4.0, 5.0]},
              neck_aspect={"range": [1.5, 2.5]})
    run = generate(vp, g, prof, 2, seed=2, knobs=k, max_attempts=300)
    across = [(d, r) for d in run.designs for r in risers(d) if len(r["params"]["cavity"]) == 2]
    assert across, format_report(run.report)
    step = write_step("slab", tmp_path)
    for d, r in across:
        assert sorted(z.split(".")[0] for z in r["attached_zones"]) == ["c0", "c1"]
        assert sum(e["type"] == "neck" and e["params"]["riser"] == r["id"] for e in d["elements"]) == 2
        _, res = build_design(d, step, FoundryProfile.from_dict(prof), tmp_path)
        assert res.ok, [i.message for i in res.issues if i.severity.value == "error"]


# ---- 4. seeds ----------------------------------------------------------------------------

def test_same_seed_same_designs_other_seed_other_designs(parts):
    vp, g = parts["two_blocks"]
    a = generate_seeds(vp, g, profile(), 6, seed=42, knobs=knobs())
    b = generate_seeds(vp, g, profile(), 6, seed=42, knobs=knobs())
    c = generate_seeds(vp, g, profile(), 6, seed=43, knobs=knobs())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(a, sort_keys=True) != json.dumps(c, sort_keys=True)


def test_designs_are_json_and_record_their_knobs(parts):
    vp, g = parts["block"]
    for d in generate_seeds(vp, g, profile(), 4, seed=9, knobs=knobs()):
        json.dumps(d)
        prov = d["provenance"]
        assert prov["generator"] == "rules" and prov["seed"] == 9
        assert set(prov["knob_values"]) == set(load_knobs(knobs()))


# ---- 5. valid, and built by the CAD module ---------------------------------------------------

@pytest.mark.parametrize("name,cavities", [("block", 1), ("two_blocks", 1), ("bosses", 1), ("slab", 1), ("block", 2)])
def test_designs_pass_validity_and_build(parts, tmp_path, name, cavities):
    from core.registry import REGISTRY
    from seeds.build import build_design
    from seeds.generate import Ctx, _hot_spots
    from seeds.attach import Places
    from seeds.moldmap import MoldMap
    import numpy as np
    REGISTRY.discover("elements")
    vp, g = parts[name]
    prof = FoundryProfile.from_dict(profile(cavity_count=cavities,
                                            flask_size_mm=[900.0, 600.0, 300.0] if cavities > 1 else [600.0, 450.0, 300.0]))
    places = Places(vp)
    run = generate(vp, g, prof, 4, seed=11, knobs=knobs(), places=places)
    assert len(run.designs) == 4, format_report(run.report)
    step = write_step(name, tmp_path)
    for d in run.designs:
        ctx = Ctx(vp, g, places, prof, d["provenance"]["knob_values"], np.random.default_rng(0))
        ctx.offsets = d["cavity_layout"]["offsets"]
        ctx.mold = MoldMap(vp, ctx.offsets, prof.flask_lo, prof.flask_hi)
        _hot_spots(ctx)
        assert validity.check(d, ctx) == []
        _, res = build_design(d, step, prof, tmp_path)
        errors = [i.message for i in res.issues if i.severity.value == "error"]
        assert res.ok, (d["id"], errors)
        types = sorted(e["type"] for e in res.doc.data["elements"])
        assert types.count("feeder") == len(risers(d))
        assert types.count("ingate") == sum(e["type"] == "ingate" for e in d["elements"])


# ---- config and report ---------------------------------------------------------------------------

def test_shipped_knobs_are_blank_and_refused():
    with pytest.raises(ConfigError) as e:
        load_knobs()
    assert any("modulus_factor" in p for p in e.value.problems)


def test_profile_needs_every_number():
    p = profile()
    p["clearances"] = {**p["clearances"], "riser_spacing_mm": None}
    p["density_kg_m3"] = None
    with pytest.raises(ConfigError) as e:
        FoundryProfile.from_dict(p)
    assert "profile: density_kg_m3 not set" in e.value.problems
    assert "profile: clearances.riser_spacing_mm not set" in e.value.problems


def test_report_warns_on_a_dominant_bucket(parts):
    vp, g = parts["block"]
    run = generate(vp, g, profile(), 5, seed=3, knobs=knobs())
    assert run.report["by"]["risers"] == {"1": 5}
    assert any("risers = 1" in w for w in run.report["warnings"])
    assert "WARNING" in format_report(run.report)
