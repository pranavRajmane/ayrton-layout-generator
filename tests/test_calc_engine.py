"""The calculator engine on a whole gating system, against hand calculations.
The preset here is TEST DATA written for this file, not foundry practice."""
import json
import math

import pytest
from build123d import Box, export_step

import calculators.presets as presets
from calculators.apply import propagation_patch
from core.document import DesignDocument, empty
from core.regen import regenerate

G = 9.80665
TEST_PRESET = {
    "id": "test_iron", "process": "green_sand", "alloy": "grey_iron",
    "density_kg_m3": 7000.0, "viscosity_pa_s": 0.005, "pouring_temp_c": {"default": None, "min": None, "max": None},
    "shrinkage_pct": None, "discharge_coefficient": 0.8, "pour_time_formula": "1 + 0.5 * W + 0.1 * t",
    "gating_ratios": {"default": [1, 2, 2], "options": {"pressurized": [2, 2, 1], "unpressurized": [1, 2, 2]}},
    "limits": {"max_ingate_velocity_m_s": 0.5, "max_runner_velocity_m_s": None, "max_joint_angle_deg": 45.0},
    "source": "test data for tests/test_calc_engine.py",
}


def F(o, n, u):
    return {"origin": list(o), "normal": list(n), "up": list(u)}


def con(cid, a, ap, b, bp, joint="butt", **kw):
    return {"id": cid, "from": {"instance": a, "port": ap, **kw.pop("frm", {})}, "to": {"instance": b, "port": bp},
            "joint": joint, **kw}


@pytest.fixture
def system(tmp_path, monkeypatch):
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "test_iron.json").write_text(json.dumps(TEST_PRESET))
    monkeypatch.setattr(presets, "PRESET_DIR", tmp_path / "presets")
    export_step(Box(200, 150, 20), tmp_path / "plate.step")
    d = empty("system")
    d["process"] = {"process": "green_sand", "alloy": "grey_iron", "preset": "test_iron", "gating_system": "unpressurized",
                    "section_thickness_mm": 20.0, "pour_time_s": None, "basin_head_mm": 50.0, "head_method": "top"}
    d["part"] = {"source": "plate.step", "ports": [
        {"id": "g1", "frame": F((-50, -40, 10), (0, 0, -1), (1, 0, 0))},
        {"id": "g2", "frame": F((50, -40, 10), (0, 0, -1), (1, 0, 0))}]}
    d["elements"] = [
        {"id": "sprue", "type": "test_sprue", "params": {"length": 100.0, "d": 20.0},
         "anchor": {"port": "inlet", "frame": F((-220, -40, 152.5), (0, 0, -1), (1, 0, 0))}},
        {"id": "well", "type": "test_well", "params": {"d_in": 20.0, "width": 30.0, "height": 25.0}},
        {"id": "runner", "type": "test_runner", "params": {"waypoints": [[150, -40, 40]], "top": 30.0, "bottom": 30.0}},
        {"id": "ingate_1", "type": "test_ingate", "params": {"length": 17.5}},
        {"id": "ingate_2", "type": "test_ingate", "params": {"length": 17.5}},
        {"id": "ext", "type": "dummy_box", "params": {"length": 30.0, "width": 30.0, "height": 25.0}},
    ]
    d["connections"] = [
        con("c0", "sprue", "outlet", "well", "inlet"), con("c1", "well", "outlet", "runner", "inlet"),
        con("t1", "runner", "tap_1", "ingate_1", "inlet", "branch", frm={"side": "bottom"}),
        con("t2", "runner", "tap_2", "ingate_2", "inlet", "branch", frm={"side": "bottom"}),
        con("p1", "ingate_1", "outlet", "part", "g1", "part"), con("p2", "ingate_2", "outlet", "part", "g2", "part"),
        con("x1", "runner", "outlet", "ext", "inlet", "blind")]
    return DesignDocument(d, tmp_path)


def test_system_regenerates_clean_of_geometry_errors(system):
    r = regenerate(system)
    errs = [(i.rule, i.instances) for i in r.issues if i.severity.value == "error"]
    assert errs == []
    assert r.built["runner"].derived["length_mm"] == pytest.approx(355.0)


def test_readouts_pour_head_choke_by_hand(system):
    c = regenerate(system).calc
    ro, out = c.readouts, c.outputs
    assert ro["casting_kg"] == pytest.approx(0.6e-3 * 7000)                       # 600 cm3 of plate
    assert ro["yield_pct"] == pytest.approx(100 * ro["casting_volume_mm3"] / ro["poured_volume_mm3"])
    t = 1 + 0.5 * ro["casting_kg"] + 0.1 * 20                                       # the test formula
    assert out["pour_time_s"] == pytest.approx(t)
    assert out["head_mm"] == pytest.approx(152.5 + 50 - 10)                         # pouring inlet + basin - gates
    A = ro["poured_kg"] / (7000 * t * 0.8 * math.sqrt(2 * G * 0.1925)) * 1e6
    assert out["required_choke_mm2"] == pytest.approx(A, rel=0.02)                  # exit criterion: within 2%
    assert out["required_choke_mm2"] == pytest.approx(A, rel=1e-9)


def test_planes_flow_split_velocity(system):
    c = regenerate(system).calc
    q = c.outputs["flow_m3_s"]
    rows = {r["name"]: r for r in c.planes}
    assert rows["sprue.inlet"]["flow_share"] == 1.0
    g1 = rows["ingate_1.outlet"]
    assert g1["flow_share"] == pytest.approx(0.5) and g1["role"] == "ingate"
    assert g1["velocity_m_s"] == pytest.approx(q * 0.5 / 400e-6)
    assert g1["reynolds"] == pytest.approx(7000 * g1["velocity_m_s"] * 0.020 / 0.005)    # 20 x 20: Dh 20 mm
    assert rows["runner.outlet"]["flow_share"] == 0.0                                  # the blind extension


def test_paths_choke_and_ratio(system):
    c = regenerate(system).calc
    p = c.paths[0]
    assert p["elements"][:3] == ["sprue", "well", "runner"] and p["ends_at_part"]
    assert p["choke_mm2"] == pytest.approx(math.pi * 100) and p["choke_role"] == "sprue"
    assert p["ratio"] == pytest.approx([1, 750 / (math.pi * 100), 400 / (math.pi * 100)], abs=1e-3)


def test_rules_use_the_preset_limits(system):
    r = regenerate(system)
    rules = {i.rule for i in r.issues}
    v = r.calc.outputs["flow_m3_s"] * 0.5 / 400e-6
    assert ("system.ingate_velocity" in rules) == (v > 0.5)
    angle = [i for i in r.issues if i.rule == "system.joint_angle"]
    # the branches turn 90 deg (limit 45, a test value); the well turns the flow inside itself, not at a joint
    assert {i.connection for i in angle} == {"t1", "t2"}


def test_ratio_propagation_sizes_the_members(system):
    r = regenerate(system)
    req = r.calc.outputs["required_choke_mm2"]
    patch, issues = propagation_patch(r)
    assert issues == []
    system.apply(patch)
    r2 = regenerate(system)
    sec = lambda i, port: r2.built[i].port(port).section.area                       # noqa: E731
    assert sec("sprue", "outlet") == pytest.approx(req, rel=1e-9)                   # 1 : 2 : 2, choke at the sprue
    assert sec("ingate_1", "outlet") == pytest.approx(2 * req / 2, rel=1e-9)        # two ingates share 2 x choke
    assert r2.built["runner"].element.section(r2.built["runner"].params).area == pytest.approx(2 * req, rel=1e-9)
    assert system.undo()                                                            # one undo step


def test_missing_preset_values_are_listed_not_guessed(system, tmp_path):
    blank = dict(TEST_PRESET, density_kg_m3=None, discharge_coefficient=None)
    (tmp_path / "presets" / "test_iron.json").write_text(json.dumps(blank))
    c = regenerate(system).calc
    assert "required_choke_mm2" not in c.outputs and "casting_kg" not in c.readouts
    missing = {i.value for i in c.issues if i.rule == "preset.missing"}
    assert {"density_kg_m3", "discharge_coefficient"} <= missing
    assert c.readouts["yield_pct"] is not None                                      # yield needs no density
