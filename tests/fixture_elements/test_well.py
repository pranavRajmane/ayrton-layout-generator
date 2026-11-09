"""A box sprue well for framework tests: metal comes down into its top and
leaves sideways (a junction that turns the flow)."""
from build123d import Align, Box

from core.element import Element, Param, Port, PortType, Section, register_element
from core.frames import Frame


@register_element
class TestWell(Element):
    id = "test_well"
    version = "0.1"
    category = "test"

    params = [Param("d_in", unit="mm", default=20.0, min=1.0, max=500.0),
              Param("width", unit="mm", default=30.0, min=1.0, max=500.0),
              Param("height", unit="mm", default=25.0, min=1.0, max=500.0)]

    # Local frame: box centred on the origin; inlet on top looking down, outlet on +X.
    def ports(self, p):
        return [Port("inlet", PortType.INLET, Frame((0, 0, p.height / 2), (0, 0, -1), (1, 0, 0)), Section.round(p.d_in)),
                Port("outlet", PortType.OUTLET, Frame((p.width / 2, 0, 0), (1, 0, 0), (0, 0, 1)),
                     Section.rectangle(p.width, p.height))]

    def build(self, p):
        e = p.embed
        return Box(p.width + e, p.width, p.height + e, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (e / 2, 0, e / 2))
