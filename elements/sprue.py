"""A tapered round sprue, straight down from the top of the cope.  Geometry
only: its diameters are typed by the user (or driven by the calculators); it
carries no foundry rules."""
import math

from build123d import Cone, Location

from core.element import Element, Param, Port, PortType, Section, Severity, ValidationIssue, register_element
from core.frames import Frame


@register_element
class Sprue(Element):
    id = "sprue"
    version = "0.1"
    category = "entry"
    gating_role = "sprue"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("d_top", unit="mm", default=30.0, min=1.0, max=500.0, label="top diameter"),
        Param("d_bottom", unit="mm", default=25.0, min=1.0, max=500.0, label="bottom diameter",
              driven_by="calculators.choke_area"),
        Param("length", unit="mm", default=200.0, min=1.0, max=5000.0,
              description="cope top to the sprue's bottom (set by the box and the runner it drops into)"),
    ]

    # Local frame: flow along +X (straight down once placed) from the inlet at the origin.
    def ports(self, p):
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), Section.round(p.d_top)),
                Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)),
                     Section.round(p.d_bottom))]

    def _r(self, p, x):
        return (p.d_top + (p.d_bottom - p.d_top) * x / p.length) / 2

    def derived(self, p):
        r1, r2 = p.d_top / 2, p.d_bottom / 2
        return {"volume_mm3": math.pi * p.length * (r1 * r1 + r1 * r2 + r2 * r2) / 3,
                "top_area_mm2": math.pi * r1 * r1, "bottom_area_mm2": math.pi * r2 * r2}

    def build(self, p):
        e = p.embed
        r0, r1 = self._r(p, -e), self._r(p, p.length + e)     # the taper carried on across both embeds
        cone = Cone(r0, r1, p.length + 2 * e, align=None)       # along +Z from z = 0
        return cone.moved(Location((-e, 0, 0), (0, 1, 0), 90))

    def validate(self, p, context):
        if self._r(p, p.length + p.embed) <= 0:
            return [ValidationIssue("sprue.taper", Severity.ERROR, "the taper closes the sprue before its bottom",
                                    instances=(context.instance_id,))]
        return []

    def size_for_area(self, p, area_mm2):
        return {"d_bottom": math.sqrt(4 * area_mm2 / math.pi)}

    def tags(self, instance_id):
        return {"solid": f"gating.{instance_id}", "faces": {"inlet": "pouring", "outlet": "sprue_base"}}
