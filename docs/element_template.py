# elements/<snake_case_id>.py  -- copy this file to start a new element.
# Write docs/elements/<snake_case_id>.md (the spec sheet) first; tests/elements/test_contract.py then
# checks this element automatically, and tests/elements/test_<snake_case_id>.py holds its own cases.
from build123d import Part  # noqa: F401  (OCCT via build123d)

from core.element import (  # noqa: F401
    Element, Param, Port, PortType, Section, SectionPolicy, Severity, ValidationIssue, register_element,
)
from core.frames import Frame  # noqa: F401


# @register_element             # uncomment once ports() and build() work
class MyElement(Element):
    id = "my_element"            # must match the file name and the spec sheet
    version = "0.1"
    category = "distribution"    # entry | junction | distribution | cleaning | venting | feeding
    processes = ["green_sand"]
    alloys = ["grey_iron", "sg_iron"]

    # ---- parameters: what the UI panel shows (the framework adds `embed`) ------------------------
    params = [
        Param("length", unit="mm", default=100.0, min=10.0, max=2000.0),
        Param("area", unit="mm2", default=400.0, min=10.0, driven_by="calculators.choke_area"),
        # Param("section", choices=("trapezoid", "rectangle", "round"), default="trapezoid"),
    ]

    # ---- ports: planes where metal enters or leaves, in the LOCAL frame ---------------------------
    # Frame(origin, normal = flow direction, up).  Policy: DRIVES / FOLLOWS (implement adopt_section) / OWN.
    def ports(self, p):
        sec = Section.rectangle(20.0, p.area / 20.0)
        return [
            Port("inlet", PortType.INLET, Frame((0, 0, 0), (1, 0, 0), (0, 0, 1)), sec, SectionPolicy.OWN),
            Port("outlet", PortType.OUTLET, Frame((p.length, 0, 0), (1, 0, 0), (0, 0, 1)), sec, SectionPolicy.OWN),
        ]

    # ---- derived values: computed, read-only in the UI --------------------------------------------
    def derived(self, p):
        return {"volume_mm3": p.length * p.area}

    # ---- geometry: ONE solid in the local frame; run it `p.embed` past every port plane ------------
    def build(self, p):
        raise NotImplementedError

    # ---- element rules (from the spec sheet); limits come from context/presets, never invented ----
    def validate(self, p, context):
        return []

    # ---- solver tags ------------------------------------------------------------------------------
    def tags(self, instance_id):
        return {"solid": f"gating.{instance_id}", "faces": {"inlet": "inlet", "outlet": "outlet"}}

    # ---- path elements only (is_path = True): taps along the path ---------------------------------
    # def tap_frame(self, p, s, side): ...
    # def tap_section(self, p, s, side): ...
