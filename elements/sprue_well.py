"""The sprue well: a round chamber under the sprue (under its filter, if it has
one) that the runners leave from, reaching below the deepest runner's floor.
Geometry only: diameter and depth below the runners are typed.  With draft,
its wall leans in toward the bottom (``d`` is the top diameter)."""
import math

from build123d import Cone, Cylinder, Location

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
        Param("draft_deg", unit="deg", default=0.0, min=0.0, max=30.0,
              description="wall draft: narrower toward the bottom"),
    ]

    def _d_at(self, p, x: float) -> float:
        return p.d - 2 * x * math.tan(math.radians(p.draft_deg))

    # Local frame: flow along +X (straight down once placed) from the top at the origin.
    def ports(self, p):
        sec = Section.round(p.d)
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec),
                Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def derived(self, p):
        r1, r2 = p.d / 2, self._d_at(p, p.length) / 2
        return {"volume_mm3": math.pi * p.length * (r1 * r1 + r1 * r2 + r2 * r2) / 3}

    def build(self, p):
        e = p.embed
        L = p.length + 2 * e
        if p.draft_deg <= 0:
            return Cylinder(p.d / 2, L).moved(Location((L / 2 - e, 0, 0), (0, 1, 0), 90))
        if self._d_at(p, p.length + e) <= 0:
            raise ValueError(f"{p.draft_deg:g} degrees of draft closes the well before its bottom")
        cone = Cone(self._d_at(p, -e) / 2, self._d_at(p, p.length + e) / 2, L, align=None)
        return cone.moved(Location((-e, 0, 0), (0, 1, 0), 90))
