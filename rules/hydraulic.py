"""Rules on the calculator results.  Limits come from the process preset; a
rule whose limit is not set says nothing (the calculators already list what is
missing)."""
import numpy as np

from core.element import Severity
from core.rules import Rule, register_rule


@register_rule
class IngateVelocity(Rule):
    id = "system.ingate_velocity"
    severity = Severity.WARNING

    def check(self, design, context):
        lim = design.preset.get("limits.max_ingate_velocity_m_s") if design.preset else None
        if lim is None:
            return []
        return [self.issue(f"{r['name']} runs at {r['velocity_m_s']:.2f} m/s against a {lim} m/s limit",
                           [r["name"].split(".")[0]], round(r["velocity_m_s"], 4), lim, r["connection"])
                for r in context.planes
                if r.get("velocity_m_s") is not None and r["role"] == "ingate" and r["velocity_m_s"] > lim]


@register_rule
class RunnerVelocity(Rule):
    id = "system.runner_velocity"
    severity = Severity.WARNING

    def check(self, design, context):
        lim = design.preset.get("limits.max_runner_velocity_m_s") if design.preset else None
        if lim is None:
            return []
        return [self.issue(f"{r['name']} runs at {r['velocity_m_s']:.2f} m/s against a {lim} m/s limit",
                           [r["name"].split(".")[0]], round(r["velocity_m_s"], 4), lim, r["connection"])
                for r in context.planes
                if r.get("velocity_m_s") is not None and r["role"] == "runner" and r["velocity_m_s"] > lim]


@register_rule
class ChokeLocation(Rule):
    """Where the choke sits follows from the system the engineer chose:
    unpressurized systems choke at the sprue, pressurized ones at the ingates."""
    id = "system.choke_location"
    severity = Severity.WARNING

    def check(self, design, context):
        system = design.doc.data["process"].get("gating_system")
        want = {"unpressurized": "sprue", "pressurized": "ingate"}.get(system)
        if want is None:
            return []
        return [self.issue(f"the {system} path through {', '.join(p['elements'])} chokes at {p['choke_plane']} "
                           f"({p['choke_role'] or 'no role'}), not at the {want}", p["elements"],
                           p["choke_role"], want)
                for p in context.paths if p.get("choke_role") and p["choke_role"] != want]


@register_rule
class ChokeArea(Rule):
    id = "system.choke_area"
    severity = Severity.WARNING

    def check(self, design, context):
        req = context.outputs.get("required_choke_mm2")
        if req is None:
            return []
        return [self.issue(f"the choke at {p['choke_plane']} is {p['choke_mm2']:.0f} mm2; the pour needs "
                           f"{req:.0f} mm2, so it would pour slower than planned", p["elements"],
                           round(p["choke_mm2"], 2), round(req, 2))
                for p in context.paths if p.get("choke_mm2") is not None and p["choke_mm2"] < req]


@register_rule
class JointAngle(Rule):
    """Direction change where a branch leaves a path element (or any joint)."""
    id = "system.joint_angle"
    severity = Severity.WARNING

    def check(self, design, context):
        lim = design.preset.get("limits.max_joint_angle_deg") if design.preset else None
        if lim is None:
            return []
        out = []
        for c in design.graph.connections:
            a = design.built.get(c.frm.instance)
            b = design.built.get(c.to.instance)
            if a is None or b is None:
                continue
            e = design.frm(c)
            if c.joint == "drop":                    # straight down into a runner: not a turn of the flow
                continue
            if c.joint == "tee":                     # along the runner where the ingate leaves it
                s = a.element.nearest_station(a.params, b.element.points(b.params)[0])
                along = np.asarray(a.element.frame_at(a.params, s).normal)
            elif a.element.is_path and e.station is not None:
                along = np.asarray(a.element.frame_at(a.params, e.station).normal)
            else:
                along = np.asarray(a.port(e).frame.normal)
            into = np.asarray(b.port(c.to.port).frame.normal)
            ang = float(np.degrees(np.arccos(np.clip(along @ into, -1, 1))))
            if ang > lim + 1e-9:
                out.append(self.issue(f"metal turns {ang:.1f} deg from {c.frm.label} into {c.to.label}",
                                      (a.instance, b.instance), round(ang, 3), lim, c.id))
        return out
