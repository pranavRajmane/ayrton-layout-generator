"""The dummy box as an ingate (role 'ingate'), sized for an area by its width."""
from core.element import register_element
from elements.dummy_box import DummyBox


@register_element
class TestIngate(DummyBox):
    id = "test_ingate"
    version = "0.1"
    category = "test"
    gating_role = "ingate"

    def size_for_area(self, p, area_mm2):
        return {"width": area_mm2 / p.height}
