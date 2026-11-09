"""Calculators against hand calculations (exit criterion: within 2%; these are exact)."""
import math

import pytest

from calculators import hydraulics as h
from calculators.formula import FormulaError, evaluate


def test_bernoulli_velocity_by_hand():
    # H = 200 mm: v = sqrt(2 * 9.80665 * 0.2) = 1.98057 m/s
    assert h.free_fall_velocity(200) == pytest.approx(1.98057, rel=1e-5)


def test_choke_area_by_hand():
    # W 50 kg, t 10 s, rho 7000 kg/m3, C 0.8, H 200 mm:
    # A = 50 / (7000 * 10 * 0.8 * 1.98057) = 4.5080e-4 m2 = 450.80 mm2
    assert h.choke_area(50, 10, 7000, 0.8, 200) == pytest.approx(450.80, rel=1e-4)


def test_effective_heads_by_hand():
    assert h.effective_head("top", 200) == 200
    assert h.effective_head("bottom", 200, casting_height_mm=100) == 150
    assert h.effective_head("parting", 200, height_above_gate_mm=50, casting_height_mm=100) == 187.5
    with pytest.raises(ValueError):
        h.effective_head("guess", 200, 1, 1)


def test_sprue_taper_by_hand():
    # 400 mm2 at the bottom, 50 mm of metal over the top, 200 mm sprue: 400 * sqrt(250/50) = 894.43 mm2
    assert h.sprue_top_area(400, 50, 200) == pytest.approx(894.43, rel=1e-5)


def test_flow_velocity_reynolds_by_hand():
    q = h.flow_rate(7_142_857, 10)                       # 7.142857e-3 m3 in 10 s
    assert q == pytest.approx(7.142857e-4)
    assert h.velocity(q, 450.8) == pytest.approx(1.5845, rel=1e-4)
    dh = h.hydraulic_diameter(400, 80)                   # 20 x 20 square: 20 mm
    assert dh == 20
    assert h.reynolds(7000, 1.0, dh, 0.005) == pytest.approx(28_000)


def test_ratio_areas():
    assert h.ratio_areas(400, [1, 2, 2]) == [400, 800, 800]
    assert h.ratio_areas(400, [4, 8, 3]) == pytest.approx([533.33, 1066.67, 400], rel=1e-4)


def test_formula_evaluation_and_safety():
    assert evaluate("2.08 + 2.84 * sqrt(W)", W=100) == pytest.approx(30.48)   # a made-up test expression
    assert evaluate("k * t + W / 10", W=50, t=20, k=0.5) == 15
    for bad in ("__import__('os')", "W.__class__", "[x for x in (1,)]", "open('x')", "W +"):
        with pytest.raises(FormulaError):
            evaluate(bad, W=1)
    with pytest.raises(FormulaError):
        evaluate("log(0 * W)", W=1)
