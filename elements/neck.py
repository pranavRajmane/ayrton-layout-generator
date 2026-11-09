"""A side feeder's neck: a rectangle swept straight from inside the feeder to the
part, flat face on the parting plane, in the cope or the drag.  Geometry only."""
from core.element import Param, Section, register_element
from core.path import PathElement


@register_element
class Neck(PathElement):
    id = "neck"
    version = "0.1"
    category = "distribution"
    gating_role = None
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
    ]

    def section(self, p):
        return Section.rectangle(p.width, p.thickness)

    def rise(self, p):
        return p.thickness / 2 if p.side == "cope" else -p.thickness / 2

    def tags(self, instance_id):
        return {"solid": f"feeder.{instance_id}", "faces": {}}
