"""A box ingate whose outlet must lie on the parting plane and point
horizontally: exercises port constraints."""
from core.element import Port, PortType, Section, register_element
from core.frames import Frame
from elements.dummy_box import DummyBox


@register_element
class TestPartingGate(DummyBox):
    id = "test_parting_gate"
    version = "0.1"
    category = "test"

    def ports(self, p):
        sec = Section.rectangle(p.width, p.height)
        return [Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec),
                Port("outlet", PortType.PART_FACE, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec,
                     constraints={"on_parting_plane": True, "normal": "horizontal"})]
