"""A round bar for framework tests (transition joints, round sections)."""
from build123d import Cylinder, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class TestRoundBar(Element):
    id = "test_round_bar"
    version = "0.1"
    category = "test"

    params = [
        Param("length", unit="mm", default=60.0, min=1.0, max=2000.0),
        Param("d", unit="mm", default=20.0, min=1.0, max=500.0),
    ]

    def ports(self, p):
        sec = Section.round(p.d)
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec),
                Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def build(self, p):
        L = p.length + 2 * p.embed
        return Cylinder(p.d / 2, L).moved(Location((L / 2 - p.embed, 0, 0), (0, 1, 0), 90))
