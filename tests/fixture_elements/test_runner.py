"""A trapezoidal runner for framework tests: exercises path solving, bends and
taps.  Not a library element (its numbers are test geometry, not foundry
practice)."""
from core.element import Param, Section, register_element
from core.path import PathElement


@register_element
class TestRunner(PathElement):
    id = "test_runner"
    version = "0.1"
    category = "test"
    gating_role = "runner"

    params = [
        Param("top", unit="mm", default=20.0, min=1.0, max=500.0),
        Param("bottom", unit="mm", default=30.0, min=1.0, max=500.0),
        Param("height", unit="mm", default=25.0, min=1.0, max=500.0),
        Param("waypoints", unit="mm", default=((0.0, 0.0, 0.0), (200.0, 0.0, 0.0)), kind="points"),
        Param("bend_radius", unit="mm", default=0.0, min=0.0, max=1000.0),
    ]

    def section(self, p):
        return Section.trapezoid(p.top, p.bottom, p.height)

    def size_for_area(self, p, area_mm2):
        k = area_mm2 / self.section(p).area           # keep the shape, scale the widths
        return {"top": p.top * k, "bottom": p.bottom * k}
