"""The sprue well: a round chamber under the sprue (under its filter, if it has
one) that the runners leave from, reaching below the deepest runner's floor.
Geometry only: diameter and depth below the runners are typed."""
import math

from build123d import Cylinder, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class SprueWell(Element):
    id = "sprue_well"
    version = "0.1"
    category = "junction"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("d", unit="mm", default=50.0, min=1.0, max=1000.0, label="diameter"),
        Param("depth", unit="mm", default=20.0, min=0.0, max=1000.0, label="depth below the runners"),
        Param("length", unit="mm", default=45.0, min=1.0, max=5000.0,
              description="top to bottom (set from the runners and the filter)"),
    ]

    # Local frame: flow along +X (straight down once placed) from the top at the origin.
    def ports(self, p):
        sec = Section.round(p.d)
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec),
                Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def derived(self, p):
        return {"volume_mm3": math.pi * p.d ** 2 / 4 * p.length}

    def build(self, p):
        e = p.embed
        L = p.length + 2 * e
        return Cylinder(p.d / 2, L).moved(Location((L / 2 - e, 0, 0), (0, 1, 0), 90))
