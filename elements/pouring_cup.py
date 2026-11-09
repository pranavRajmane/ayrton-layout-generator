"""A conical pouring cup cut into the top of the cope, over the sprue: the metal is
poured into its open top and leaves at its bottom into the sprue.  Geometry
only: the top diameter and height are typed; the bottom is the sprue's top."""
import math

from build123d import Cone, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class PouringCup(Element):
    id = "pouring_cup"
    version = "0.1"
    category = "entry"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("d_top", unit="mm", default=80.0, min=1.0, max=1000.0, label="top diameter"),
        Param("d_bottom", unit="mm", default=30.0, min=1.0, max=500.0, label="bottom diameter",
              description="the sprue's top diameter (kept equal to it)"),
        Param("height", unit="mm", default=60.0, min=1.0, max=1000.0),
    ]

    # Local frame: flow along +X (straight down once placed) from the open top at the origin.
    def ports(self, p):
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), Section.round(p.d_top)),
                Port("outlet", PortType.OUTLET, Frame((p.height, 0, 0), (1, 0, 0), (0, 0, 1)),
                     Section.round(p.d_bottom))]

    def _r(self, p, x):
        return (p.d_top + (p.d_bottom - p.d_top) * x / p.height) / 2

    def derived(self, p):
        r1, r2 = p.d_top / 2, p.d_bottom / 2
        return {"volume_mm3": math.pi * p.height * (r1 * r1 + r1 * r2 + r2 * r2) / 3}

    def build(self, p):
        e = p.embed
        cone = Cone(self._r(p, -e), self._r(p, p.height + e), p.height + 2 * e, align=None)
        return cone.moved(Location((-e, 0, 0), (0, 1, 0), 90))
