import numpy as np
import pytest

from core.frames import Frame, invert, is_rigid, placement_onto, rigid


def test_frame_orthonormalises_up():
    f = Frame((0, 0, 0), (2, 0, 0), (0.3, 0, 5))
    assert np.allclose(f.normal, (1, 0, 0))
    assert np.allclose(f.up, (0, 0, 1))
    assert np.allclose(f.side, (0, 1, 0))
    assert is_rigid(f.matrix())


def test_parallel_up_rejected():
    with pytest.raises(ValueError):
        Frame((0, 0, 0), (0, 0, 1), (0, 0, -3))


def test_matrix_round_trip():
    f = Frame((1, 2, 3), (0, 1, 0), (1, 0, 1))
    g = Frame.from_matrix(f.matrix())
    assert np.allclose(g.origin, f.origin) and np.allclose(g.normal, f.normal) and np.allclose(g.up, f.up)


def test_invert():
    m = Frame((5, -2, 7), (0, 0, -1), (1, 1, 0)).matrix()
    assert np.allclose(m @ invert(m), np.eye(4))


def test_placement_lays_port_on_target():
    local = Frame((10, 0, 0), (1, 0, 0), (0, 0, 1))
    target = Frame((100, 50, -20), (0, 0, -1), (0, 1, 0))
    t = placement_onto(local, target)
    placed = local.transformed(t)
    d, a, tw = placed.gap(target)
    assert d < 1e-9 and a < 1e-6 and tw < 1e-6


def test_dict_round_trip_and_gap():
    f = Frame((0, 0, 0), (0, 0, 1), (1, 0, 0))
    assert Frame.from_dict(f.to_dict()) == f
    d, a, t = f.gap(f.offset(3.0).reversed())
    assert d == pytest.approx(3.0) and a == pytest.approx(180.0) and t == pytest.approx(0.0)


def test_rigid_translation_only():
    assert np.allclose(rigid(translation=(1, 2, 3))[:3, 3], (1, 2, 3))
