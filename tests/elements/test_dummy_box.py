import pytest

from core.element import Section
from core.registry import REGISTRY

REGISTRY.discover("elements")
DummyBox = REGISTRY.get("dummy_box")

CASES = [
    # (params, expected_volume_mm3 incl. 1 mm embed at both ends, expected_outlet_area_mm2)
    ({"length": 100.0, "width": 20.0, "height": 20.0}, 102 * 400.0, 400.0),
    ({"length": 50.0, "width": 10.0, "height": 30.0, "embed": 2.0}, 54 * 300.0, 300.0),
]


@pytest.mark.parametrize("params,vol,area", CASES)
def test_geometry(params, vol, area):
    el = DummyBox()
    p = el.resolve_params(params)
    solid = el.build(p)
    assert solid.is_valid
    assert solid.volume == pytest.approx(vol, rel=0.01)
    assert el.ports(p)[-1].section.area == pytest.approx(area, rel=0.01)


def test_param_limits_rejected():
    with pytest.raises(ValueError):
        DummyBox().resolve_params({"length": -5})


def test_adopt_section():
    el = DummyBox()
    p = el.adopt_section(el.resolve_params({"inlet_policy": "follows"}), "inlet", Section.rectangle(30, 15))
    assert (p.width, p.height) == (30, 15)
    with pytest.raises(NotImplementedError):
        el.adopt_section(p, "inlet", Section.round(10))
