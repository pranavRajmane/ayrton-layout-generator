"""A ceramic foam filter lying across the bottom of the sprue, on the parting
plane, over the sprue well: metal comes down through it.  Its own region
(filter.<id>) so the solver can treat it as a porous medium.  Geometry only."""
from build123d import Box, Cylinder, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class Filter(Element):
    id = "filter"
    version = "0.1"
    category = "cleaning"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("shape", choices=("square", "round"), default="square"),
        Param("size", unit="mm", default=50.0, min=1.0, max=500.0, description="side (square) or diameter (round)"),
        Param("thickness", unit="mm", default=20.0, min=1.0, max=200.0),
    ]

    def _section(self, p):
        return Section.rectangle(p.size, p.size) if p.shape == "square" else Section.round(p.size)

    # Local frame: flow along +X (straight down once placed) from its top face at the origin.
    def ports(self, p):
        sec = self._section(p)
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec),
                Port("outlet", PortType.OUTLET, Frame((p.thickness, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def derived(self, p):
        return {"volume_mm3": self._section(p).area * p.thickness, "face_area_mm2": self._section(p).area}

    def build(self, p):
        e = p.embed
        L = p.thickness + 2 * e
        if p.shape == "square":
            return Box(L, p.size, p.size).moved(Location((L / 2 - e, 0, 0)))
        return Cylinder(p.size / 2, L).moved(Location((L / 2 - e, 0, 0), (0, 1, 0), 90))

    def tags(self, instance_id):
        return {"solid": f"filter.{instance_id}", "faces": {"inlet": "filter_in", "outlet": "filter_out"}}
