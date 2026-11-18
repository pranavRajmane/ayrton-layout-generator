"""A feeder (riser): a closed round cylinder in the cope.  A top feeder stands
on the part's top face; a side feeder stands on the parting plane beside the
part and is joined to it by a neck.  Geometry only: diameter and height typed."""
import math

from build123d import Cylinder, Location

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class Feeder(Element):
    id = "feeder"
    version = "0.1"
    category = "feeding"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("kind", choices=("top", "side"), default="top"),
        Param("d", unit="mm", default=60.0, min=1.0, max=1000.0, label="diameter"),
        Param("height", unit="mm", default=90.0, min=1.0, max=2000.0),
        Param("embed", unit="mm", default=1.0, min=0.0, max=2000.0,
              description="how far it reaches down into the part below its base: 1 mm on a flat top, more "
                          "where the top falls away under it (set when it is placed)"),
    ]

    # Local frame: its axis along +X (straight up once placed) from its base at the origin.
    def ports(self, p):
        sec = Section.round(p.d)
        base = Port("base", PortType.PART_FACE if p.kind == "top" else PortType.OUTLET,
                    Frame((0, 0, 0), (-1, 0, 0), (0, 0, 1)), sec)        # facing the part (top) or the neck (side)
        return [base, Port("top", PortType.BLIND, Frame((p.height, 0, 0), (1, 0, 0), (0, 0, 1)), sec)]

    def derived(self, p):
        return {"volume_mm3": math.pi * p.d ** 2 / 4 * p.height,
                "modulus_mm": (p.d / 2 * p.height) / (2 * p.height + p.d / 2)}

    def build(self, p):
        e = p.embed
        L = p.height + e
        return Cylinder(p.d / 2, L).moved(Location((L / 2 - e, 0, 0), (0, 1, 0), 90))

    def tags(self, instance_id):
        return {"solid": f"feeder.{instance_id}", "faces": {}}
