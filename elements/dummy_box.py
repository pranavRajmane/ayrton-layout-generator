"""A rectangular bar: the dummy element that proves the pipeline.  Not a
foundry element; it carries no rules a method engineer would rely on."""
from build123d import Align, Box

from core.element import (
    Element, Param, Port, PortType, Section, SectionPolicy, Severity, ValidationIssue, register_element,
)
from core.frames import Frame


@register_element
class DummyBox(Element):
    id = "dummy_box"
    version = "0.1"
    category = "test"
    processes = ["green_sand", "no_bake"]
    alloys = ["grey_iron", "sg_iron", "steel"]

    params = [
        Param("length", unit="mm", default=100.0, min=1.0, max=2000.0, description="inlet to outlet"),
        Param("width", unit="mm", default=20.0, min=1.0, max=500.0),
        Param("height", unit="mm", default=20.0, min=1.0, max=500.0),
        Param("inlet_policy", choices=("own", "follows"), default="own",
              description="follows: take width and height from the element upstream"),
    ]

    # Local frame: flow along +X from the inlet at the origin, up is +Z.
    def ports(self, p):
        sec = Section.rectangle(p.width, p.height)
        return [
            Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec,
                 SectionPolicy(p.inlet_policy)),
            Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec,
                 SectionPolicy.OWN),
        ]

    def derived(self, p):
        return {"volume_mm3": p.length * p.width * p.height, "section_area_mm2": p.width * p.height}

    def build(self, p):
        e = p.embed
        box = Box(p.length + 2 * e, p.width, p.height, align=(Align.MIN, Align.CENTER, Align.CENTER))
        return box.translate((-e, 0, 0))

    def adopt_section(self, p, port_id, section):
        if port_id != "inlet" or section.shape != "rectangle":
            return super().adopt_section(p, port_id, section)
        return p.replace(width=section.d["width"], height=section.d["height"])

    def validate(self, p, context):
        issues = []
        if p.embed > p.length / 4:
            issues.append(ValidationIssue(
                rule="dummy_box.embed_vs_length", severity=Severity.WARNING,
                message="embed is more than a quarter of the length", value=p.embed, limit=p.length / 4,
                instances=(context.instance_id,)))
        return issues

    def tags(self, instance_id):
        return {"solid": f"gating.{instance_id}", "faces": {"inlet": "inlet", "outlet": "outlet"}}
