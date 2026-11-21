"""An ingate drawn on the parting plane: a rectangle swept straight from a point
on a runner to the part, flat face on the plane, in the drag (below) or the
cope (above).  Geometry only: the width and thickness are typed by the user.
With draft, its sides lean in away from the parting plane (the pattern draws
out of the sand): a trapezoid of the same area, ``width`` its mean width."""
import math

from core.element import Param, Section, Severity, ValidationIssue, register_element
from core.path import PathElement


@register_element
class Ingate(PathElement):
    id = "ingate"
    version = "0.1"
    category = "distribution"
    gating_role = "ingate"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("side", choices=("drag", "cope"), default="drag",
              description="drag: below the parting plane; cope: above it"),
        Param("width", unit="mm", default=30.0, min=0.5, max=500.0),
        Param("thickness", unit="mm", default=8.0, min=0.5, max=200.0, description="through the parting plane"),
        Param("waypoints", unit="mm", default=((0.0, 0.0, 0.0), (50.0, 0.0, 0.0)), kind="points",
              description="start (on the runner) and end (at the part), on the parting plane"),
        Param("bend_radius", unit="mm", default=0.0, min=0.0, max=0.0),
        Param("placed", choices=("drawn", "auto"), default="drawn",
              description="auto: put where its runner runs into the part, and moved with the runner"),
        Param("draft_deg", unit="deg", default=0.0, min=0.0, max=30.0,
              description="side draft: wider at the parting plane, the same area"),
    ]

    def _lean(self, p) -> float:
        return p.thickness * math.tan(math.radians(p.draft_deg))

    def section(self, p):
        if p.draft_deg <= 0:
            return Section.rectangle(p.width, p.thickness)
        d = self._lean(p)
        wide, narrow = p.width + d, max(p.width - d, 1e-6)
        if p.side == "drag":                     # the parting plane is its top face
            return Section.trapezoid(wide, narrow, p.thickness)
        return Section.trapezoid(narrow, wide, p.thickness)

    def validate(self, p, context):
        if p.draft_deg > 0 and p.width - self._lean(p) <= 0:
            return [ValidationIssue("ingate.draft_too_steep", Severity.ERROR,
                                    f"{p.draft_deg:g} degrees of draft over {p.thickness:g} mm leaves no width at "
                                    f"the far face of a {p.width:g} mm ingate", value=p.draft_deg,
                                    instances=(context.instance_id,))]
        return []

    def rise(self, p):
        return p.thickness / 2 if p.side == "cope" else -p.thickness / 2

    def size_for_area(self, p, area_mm2):
        return {"width": area_mm2 / p.thickness}
