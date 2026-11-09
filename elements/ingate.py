"""An ingate drawn on the parting plane: a rectangle swept straight from a point
on a runner to the part, flat face on the plane, in the drag (below) or the
cope (above).  Geometry only: the width and thickness are typed by the user."""
from core.element import Param, Section, register_element
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
    ]

    def section(self, p):
        return Section.rectangle(p.width, p.thickness)

    def rise(self, p):
        return p.thickness / 2 if p.side == "cope" else -p.thickness / 2

    def size_for_area(self, p, area_mm2):
        return {"width": area_mm2 / p.thickness}
