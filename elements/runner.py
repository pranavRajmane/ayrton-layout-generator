"""A runner drawn on the parting plane: a trapezoid swept along the user's
points, hanging into the drag (below the plane) or standing in the cope
(above it).  Its wide face lies on the parting plane.  Geometry only: the
sizes are typed by the user (or driven by the calculators)."""
from core.element import Param, Section, register_element
from core.path import PathElement


@register_element
class Runner(PathElement):
    id = "runner"
    version = "0.1"
    category = "distribution"
    gating_role = "runner"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("side", choices=("drag", "cope"), default="drag",
              description="drag: below the parting plane; cope: above it"),
        Param("w_parting", unit="mm", default=30.0, min=1.0, max=500.0, label="width at the parting plane"),
        Param("w_far", unit="mm", default=20.0, min=1.0, max=500.0, label="width away from the parting plane"),
        Param("height", unit="mm", default=25.0, min=1.0, max=500.0),
        Param("waypoints", unit="mm", default=((0.0, 0.0, 0.0), (200.0, 0.0, 0.0)), kind="points",
              description="the points the runner runs through on the parting plane, after its start"),
        Param("bend_radius", unit="mm", default=0.0, min=0.0, max=1000.0),
    ]

    def section(self, p):
        if p.side == "drag":
            return Section.trapezoid(p.w_parting, p.w_far, p.height)
        return Section.trapezoid(p.w_far, p.w_parting, p.height)

    def rise(self, p):
        return p.height / 2 if p.side == "cope" else -p.height / 2

    def size_for_area(self, p, area_mm2):
        k = area_mm2 / self.section(p).area           # keep the shape, scale the widths
        return {"w_parting": p.w_parting * k, "w_far": p.w_far * k}
