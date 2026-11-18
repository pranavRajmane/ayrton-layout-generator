"""A runner drawn on the parting plane: a trapezoid swept along the user's
points, hanging into the drag (below the plane) or standing in the cope
(above it).  Geometry only: the sizes are typed by the user (or driven by the
calculators).

Typed as a bottom width, a taper % and a height, its top face is the narrower
one on either side of the plane (taper_pct records what was typed; the two
widths are what is built).  level: how far its parting-plane face (its top in
the drag, its bottom in the cope) sits from the parting plane, deeper into its
own half: 0 or below in the drag, 0 or above in the cope.  extension: how far it runs on past its last ingate
(a dead end that keeps the first, dirty metal out of the ingates); 0: it ends
where it is drawn."""
from core.element import Param, Section, Severity, ValidationIssue, register_element
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
        # how it was drawn, and the ingate put where it runs into a part: kept so it can be cut and
        # gated again when a part moves (waypoints are the cut-back runner)
        Param("drawn", unit="mm", default=(), kind="points", description="the points as drawn, before the cut"),
        Param("gate_width", unit="mm", default=0.0, min=0.0, max=500.0),
        Param("gate_thickness", unit="mm", default=0.0, min=0.0, max=200.0),
        Param("gate_length", unit="mm", default=0.0, min=0.0, max=1000.0),
        Param("gate_side", choices=("drag", "cope"), default="drag"),
        Param("taper_pct", unit="%", default=0.0, min=0.0, max=100.0,
              description="how much narrower the top face is than the bottom; 0: widths typed directly"),
        Param("extension", unit="mm", default=0.0, min=0.0, max=2000.0,
              description="runs this far past its last ingate (ingates drawn off its side); 0: as drawn"),
        Param("level", unit="mm", default=0.0, min=-5000.0, max=5000.0,
              description="height of its parting-plane face: 0 on the parting plane, below it (negative) "
                          "in the drag, above it in the cope; its points are at this height"),
    ]

    def section(self, p):
        if p.side == "drag":
            return Section.trapezoid(p.w_parting, p.w_far, p.height)
        return Section.trapezoid(p.w_far, p.w_parting, p.height)

    def rise(self, p):
        return p.height / 2 if p.side == "cope" else -p.height / 2

    def validate(self, p, context):
        lv = float(p.level)
        if (p.side == "drag" and lv > 1e-6) or (p.side == "cope" and lv < -1e-6):
            return [ValidationIssue("runner.level_half", Severity.ERROR,
                                    f"a runner in the {p.side} cannot sit {abs(lv):g} mm "
                                    f"{'above' if lv > 0 else 'below'} the parting plane, in the other half",
                                    value=lv, instances=(context.instance_id,))]
        return []

    def size_for_area(self, p, area_mm2):
        k = area_mm2 / self.section(p).area           # keep the shape, scale the widths
        return {"w_parting": p.w_parting * k, "w_far": p.w_far * k}
