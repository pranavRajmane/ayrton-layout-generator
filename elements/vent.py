"""A round vent from the part straight up through the cope to its top.
Geometry only: the diameter is typed, the length runs to the top of the box."""
import math

from build123d import Cylinder, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class Vent(Element):
    id = "vent"
    version = "0.1"
    category = "venting"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("d", unit="mm", default=10.0, min=0.5, max=200.0, label="diameter"),
        Param("length", unit="mm", default=100.0, min=0.5, max=5000.0,
              description="from the part to the top of the box (set from them)"),
    ]

    # Local frame: its axis along +X (straight up once placed) from the part at the origin.
    def ports(self, p):
        sec = Section.round(p.d)
        return [Port("base", PortType.PART_FACE, Frame((0, 0, 0), (-1, 0, 0), (0, 0, 1)), sec),
                Port("top", PortType.BLIND, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def derived(self, p):
        return {"volume_mm3": math.pi * p.d ** 2 / 4 * p.length}

    def build(self, p):
        e = p.embed
        L = p.length + e
        return Cylinder(p.d / 2, L).moved(Location((L / 2 - e, 0, 0), (0, 1, 0), 90))

    def tags(self, instance_id):
        return {"solid": f"vent.{instance_id}", "faces": {}}
