"""Phase 0: the GatingJob contract (schema, references, phase order, staleness, determinism)
and the recipe set (offered alloys, overrides, what each phase needs)."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest

from layouts import recipe as rc
from layouts.job import ORDER, GatingJob, JobError, canonical_json, new, problems

ROOT = Path(__file__).resolve().parents[2]
PART = ROOT / "tests/reference_parts/plate/plate.step"
TEST_RECIPES = Path(__file__).parent / "recipes"
FLASK = {"length": 600.0, "width": 500.0, "cope_height": 150.0, "drag_height": 150.0}
I3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def make(alloy="grey_iron", **kw) -> GatingJob:
    data = new("job1", PART, "green_sand", alloy, FLASK, **kw)
    job = GatingJob(data)
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=TEST_RECIPES))
    return job


# ---- tiny but valid phase outputs ----------------------------------------------------------
def sec_rect(w, h):
    return {"shape": "rectangle", "dims": {"width": w, "height": h}, "area": w * h}


MODULUS = {
    "voxel_size": 1.0, "min_wall": 10.0, "grid": {"origin": [0.0, 0.0, -5.0], "shape": [100, 50, 10]},
    "field": None, "volume": 50000.0,
    "zones": [{"id": "z0", "volume": 30000.0, "cooling_area": 7000.0, "modulus": 30000 / 7000,
               "centroid": [30.0, 25.0, 0.0], "max_distance": 5.0},
              {"id": "z1", "volume": 20000.0, "cooling_area": 5000.0, "modulus": 4.0,
               "centroid": [80.0, 25.0, 0.0], "max_distance": 5.0}],
    "hot_spots": [{"id": "h0", "position": [30.0, 25.0, 0.0], "radius": 5.0, "local_modulus": 5 / 3, "zone": "z0"}],
    "adjacency": [{"a": "z0", "b": "z1", "interface_area": 500.0}],
}
INGATES = [{"id": "g0", "part": "part", "location": [0.0, 25.0, 0.0], "contact_face": 3, "normal": [-1.0, 0.0],
            "target_zone": "z0", "zone_modulus": 30000 / 7000, "contact_length": 50.0, "section": sec_rect(20, 5),
            "ingate_modulus": 20 * 5 / (2 * 25), "flags": {"impinges_core": False, "impinges_thin_wall": False},
            "rule_tags": ["feeds_heaviest_zone"], "merged_from": []}]
ING = {"candidates": INGATES, "discarded": []}


def layout(lid, n=2, key=None):
    return {"id": lid, "n": n, "family": "row", "arrangement": {"rows": 1, "cols": n}, "symmetry": "c2",
            "rotation_deg": 0, "ingate": "g0", "sprue": [0.0, 0.0], "template": "straight",
            "cavities": [{"index": i, "rotation_deg": 180.0 * i, "mirrored": False,
                          "translation": [(-1) ** i * 150.0, 0.0], "orbit": 0} for i in range(n)],
            "key": key or lid}


def runner(lid):
    return {"layout": lid, "template": "straight",
            "segments": [{"id": "s0", "role": "sprue", "kind": "polyline", "points": [[0, 0, 150], [0, 0, -20]],
                          "section": {"shape": "round", "dims": {"d": 20.0}, "area": math.pi * 100}, "cavity": None}],
            "junctions": [], "sizing": {}, "validation": [{"code": "path_length", "passed": True, "value": 0.0}]}


def rejection(lid, code="flask.wall"):
    return {"id": lid, "params": {"n": 4}, "reasons": [{"code": code, "message": "cavity too close to the wall"}]}


def run_all(job: GatingJob) -> GatingJob:
    job.finish("modulus", MODULUS, runtime_s=0.1)
    job.finish("ingate_candidates", ING)
    job.finish("layouts", [layout("l0"), layout("l1", key="k1")], rejected=[rejection("l2")],
               counts={"enumerated": 3})
    job.finish("runners", [runner("l0")], rejected=[rejection("l1", "runner.crosses_cavity")])
    job.finish("metrics", [{"layout": "l0", "poured_weight": 12.0, "yield_pct": 70.0, "runner_length": 300.0,
                            "min_clearance": 25.0, "path_length_error_pct": 0.0}])
    return job


# ---- schema and inputs ---------------------------------------------------------------------
def test_new_job_is_valid_and_pending():
    job = make()
    d = job.data
    assert d["units"] == {"length": "mm", "mass": "kg", "time": "s"}
    assert job.status() == {p: "pending" for p in ORDER}
    assert len(d["inputs"]["part"]["sha256"]) == 64
    assert d["recipe"]["id"] == "green_sand_grey_iron"


@pytest.mark.parametrize("edit, msg", [
    (lambda d: d["inputs"]["flask"].pop("width"), "width"),
    (lambda d: d["inputs"]["flask"].update(length=-1), "length"),
    (lambda d: d["inputs"].update(alloy="Grey Iron"), "alloy"),
    (lambda d: d.update(schema_version="0.9"), "schema"),
    (lambda d: d.update(extra=1), "extra"),
    (lambda d: d["inputs"]["sprue"].update(mode="fixed"), "fixed sprue needs a position"),
    (lambda d: d["inputs"]["limits"].update(n_min=4, n_max=2), "n_min 4 > n_max 2"),
    (lambda d: d["inputs"]["limits"].update(rotations_deg=[45]), "rotations_deg"),
    (lambda d: d["inputs"]["face_tags"].extend([{"face": 1, "tag": "machined"}, {"face": 1, "tag": "cosmetic"}]),
     "tagged twice"),
])
def test_schema_and_input_rules_refuse(edit, msg):
    d = new("job1", PART, "green_sand", "grey_iron", FLASK)
    edit(d)
    p = problems(d)
    assert p and any(msg in x for x in p), p
    with pytest.raises(JobError):
        GatingJob(d)


def test_save_load_round_trip(tmp_path):
    job = run_all(make())
    f = job.save(tmp_path / "job.json")
    again = GatingJob.load(f)
    assert again.to_json() == job.to_json()
    assert again.status() == job.status()
    assert not list(tmp_path.glob(".*.tmp"))


# ---- recipe sets ---------------------------------------------------------------------------
@pytest.mark.parametrize("alloy, process, msg", [
    ("steel", "green_sand", "alloy 'steel' is not offered"),
    ("aluminium", "green_sand", "alloy 'aluminium' is not offered"),
    ("grey_iron", "investment", "process 'investment' is not offered"),
])
def test_only_ci_and_sg_iron_are_offered(alloy, process, msg):
    d = new("job1", PART, process, alloy, FLASK)
    assert any(msg in x for x in rc.input_problems(d["inputs"]))
    with pytest.raises(JobError):
        rc.resolve(d["inputs"])


def test_offered_alloys_come_from_settings_not_code():
    st = rc.settings()
    assert set(st["alloys"]) == {"grey_iron", "sg_iron"}


def test_every_offered_preset_has_every_required_key():
    """The real presets carry every value the phases read (null until the engineer fills them)."""
    st = rc.settings()
    keys = [k["path"] for ks in rc.required_keys().values() for k in ks]
    for proc in st["processes"]:
        for alloy in st["alloys"]:
            preset = json.loads((ROOT / "presets" / f"{proc}_{alloy}.json").read_text())
            for path in keys:
                holder = preset
                for part in path.split("."):
                    assert isinstance(holder, dict) and part in holder, f"{proc}_{alloy} lacks {path}"
                    holder = holder[part]


def test_blank_presets_report_what_is_missing_per_phase():
    d = new("job1", PART, "green_sand", "sg_iron", FLASK)
    snap = rc.resolve(d["inputs"])                         # the real, blank preset
    miss = rc.missing_all(snap["values"])
    assert "modulus" not in miss                           # Phase 1 needs no foundry number
    assert {m["path"] for m in miss["layouts"]} >= {"layout.clearance.flask_wall_mm", "shrinkage_pct"}
    assert {m["path"] for m in miss["runners"]} >= {"pour_time_formula", "gating_ratios.default"}


def test_test_recipes_are_complete_and_marked():
    for alloy in ("grey_iron", "sg_iron"):
        d = new("job1", PART, "green_sand", alloy, FLASK)
        snap = rc.resolve(d["inputs"], preset_dir=TEST_RECIPES)
        assert rc.missing_all(snap["values"]) == {}
        assert snap["values"]["source"].startswith("TEST FIXTURE ONLY")


def test_overrides_apply_and_unknown_paths_refuse():
    job = make(overrides={"layout.clearance.flask_wall_mm": 45.0})
    assert job.data["recipe"]["values"]["layout"]["clearance"]["flask_wall_mm"] == 45.0
    d = new("job1", PART, "green_sand", "grey_iron", FLASK, overrides={"layout.clearance.typo_mm": 1.0})
    assert any("typo_mm" in x for x in rc.input_problems(d["inputs"], TEST_RECIPES))


# ---- phases, references, staleness ---------------------------------------------------------
def test_full_run_counts_add_up():
    job = run_all(make())
    assert job.status() == {p: "done" for p in ORDER}
    c = job.counts()
    assert c == {"enumerated": 3, "geometric_ok": 2, "rejected": 2, "accepted": 1}
    assert c["accepted"] + c["rejected"] == c["enumerated"]
    assert [x["id"] for x in job.accepted()] == ["l0"]
    assert {r["phase"] for r in job.data["rejected"]} == {"layouts", "runners"}


def test_phases_run_in_order():
    job = make()
    with pytest.raises(JobError, match="needs modulus"):
        job.finish("ingate_candidates", ING)
    job.finish("modulus", MODULUS)
    with pytest.raises(JobError, match="needs ingate_candidates"):
        job.finish("layouts", [])
    with pytest.raises(JobError, match="does not reject"):
        job.finish("ingate_candidates", ING, rejected=[rejection("x")])


def test_no_phase_without_a_recipe():
    job = GatingJob(new("job1", PART, "green_sand", "grey_iron", FLASK))
    with pytest.raises(JobError, match="recipe"):
        job.finish("modulus", MODULUS)


@pytest.mark.parametrize("phase, bad, msg", [
    ("ingate_candidates", {"candidates": [dict(INGATES[0], target_zone="z9")], "discarded": []}, "targets no zone"),
    ("ingate_candidates", {"candidates": [dict(INGATES[0], normal=[1.0, 1.0])], "discarded": []}, "not a unit vector"),
    ("layouts", [dict(layout("l0"), ingate="g9")], "no ingate candidate"),
    ("layouts", [layout("l0", key="same"), layout("l1", key="same")], "duplicate layout"),
    ("layouts", [dict(layout("l0"), n=3)], "expected 0..2"),
])
def test_cross_references_are_checked(phase, bad, msg):
    job = make()
    job.finish("modulus", MODULUS)
    if phase == "layouts":
        job.finish("ingate_candidates", ING)
    with pytest.raises(JobError, match=msg):
        job.finish(phase, bad)


def test_every_layout_is_routed_or_rejected():
    job = make()
    job.finish("modulus", MODULUS)
    job.finish("ingate_candidates", ING)
    job.finish("layouts", [layout("l0"), layout("l1", key="k1")])
    with pytest.raises(JobError, match="neither routed nor rejected"):
        job.finish("runners", [runner("l0")])
    with pytest.raises(JobError, match="no layout 'l7'"):
        job.finish("runners", [runner("l0"), runner("l7")], rejected=[rejection("l1")])


def test_rerun_clears_downstream_only():
    job = run_all(make())
    job.set_assembly("l0", {"step": "l0.step", "input_hash": "x", "validation": []})
    job.start("layouts")
    st = job.status()
    assert st["modulus"] == st["ingate_candidates"] == "done"
    assert st["layouts"] == "running" and st["runners"] == st["metrics"] == "pending"
    d = job.data
    assert d["layouts"] is None and d["rejected"] == [] and d["assemblies"] == {}
    assert d["modulus"] == MODULUS


@pytest.mark.parametrize("change, stale_from", [
    ({"flask": dict(FLASK, length=700.0)}, "layouts"),
    ({"limits": dict(GatingJob(new("j", PART, "green_sand", "grey_iron", FLASK)).data["inputs"]["limits"],
                     disabled_ingates=["g0"])}, "layouts"),
    ({"face_tags": [{"face": 2, "tag": "machined"}]}, "ingate_candidates"),
    ({"parting": {"part_transform": {"rotation": I3, "translation": [0.0, 0.0, -3.0]}, "source": "user"}},
     "modulus"),
    ({"seed": 7}, "layouts"),
])
def test_changed_inputs_make_the_right_phases_stale(change, stale_from):
    job = run_all(make())
    stale = job.set_inputs(**change)
    assert stale == list(ORDER[ORDER.index(stale_from):])
    assert job.first_stale() == stale_from


def test_changed_recipe_override_leaves_modulus_valid():
    job = run_all(make())
    job.set_inputs(overrides={"layout.clearance.flask_wall_mm": 45.0})
    assert job.first_stale() is None                     # overrides act through the snapshot
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=TEST_RECIPES))
    assert job.first_stale() == "ingate_candidates"


def test_changed_part_file_makes_everything_stale(tmp_path):
    part = tmp_path / "plate.step"
    shutil.copy(PART, part)
    data = new("job1", "plate.step", "green_sand", "grey_iron", FLASK, base_dir=tmp_path)
    job = GatingJob(data, tmp_path / "job.json")
    job.set_recipe(rc.resolve(data["inputs"], preset_dir=TEST_RECIPES))
    run_all(job)
    assert not job.part_changed()
    part.write_bytes(part.read_bytes() + b"\n")
    assert job.part_changed()
    assert job.refresh_part() == list(ORDER)


# ---- determinism ---------------------------------------------------------------------------
def test_same_inputs_same_json():
    a, b = run_all(make()), run_all(make())
    assert a.to_json() == b.to_json()
    assert a.results_fingerprint() == b.results_fingerprint()
    b.set_inputs(seed=1)
    assert a.results_fingerprint() == b.results_fingerprint()     # results don't change until re-run


def test_canonical_json_ignores_key_order_and_refuses_nan():
    assert canonical_json({"b": 1, "a": [1.5, {"d": 0, "c": 2}]}) == canonical_json({"a": [1.5, {"c": 2, "d": 0}], "b": 1})
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_a_1_0_job_loads_as_1_1(tmp_path):
    d = new("old", PART, "green_sand", "grey_iron", FLASK)
    d["schema_version"] = "1.0"
    f = tmp_path / "old.job.json"
    f.write_text(json.dumps(d))
    assert GatingJob.load(f).data["schema_version"] == "1.1"


def test_discarded_ids_are_unique_and_apart_from_candidates():
    bad = {"id": "g0", "part": "part", "location": [0.0, 0.0, 0.0], "contact_face": 1, "contact_length": 3.0,
           "reasons": ["short_contact"]}
    job = make()
    job.finish("modulus", MODULUS)
    with pytest.raises(JobError, match="both a candidate and discarded"):
        job.finish("ingate_candidates", {"candidates": INGATES, "discarded": [bad]})
    with pytest.raises(JobError, match="discarded id 'd0' is used twice"):
        job.finish("ingate_candidates", {"candidates": INGATES, "discarded": [dict(bad, id="d0")] * 2})
