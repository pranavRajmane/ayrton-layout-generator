"""A straight round sprue for framework tests: flow straight down its local
axis; role 'sprue' and a diameter that the ratio propagation can drive."""
import math

from build123d import Cylinder, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class TestSprue(Element):
    id = "test_sprue"
    version = "0.1"
    category = "test"
    gating_role = "sprue"

    params = [Param("length", unit="mm", default=100.0, min=1.0, max=3000.0),
              Param("d", unit="mm", default=20.0, min=1.0, max=500.0)]

    def ports(self, p):
        sec = Section.round(p.d)
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec),
                Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def build(self, p):
        L = p.length + 2 * p.embed
        return Cylinder(p.d / 2, L).moved(Location((L / 2 - p.embed, 0, 0), (0, 1, 0), 90))

    def size_for_area(self, p, area_mm2):
        return {"d": math.sqrt(4 * area_mm2 / math.pi)}
