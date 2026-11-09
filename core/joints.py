"""Joints: what happens where two solids meet, after every element is built.

  butt        sections match within tolerance, frames coincide
  drop        an outlet pointing straight down (a sprue) onto the start of a
              path element (a runner); the path starts under it and the
              outlet must not end above the path's floor.  One outlet may drop
              into several runners; under it, a later runner gives its overlap
              with the earlier ones to them
  transition  the framework lofts the upstream section into the downstream one
              over transition.length_mm (placement already left that gap); the
              loft holds each end's section straight across that neighbour's
              embed, so the overlaps are prism on prism
  branch      a tap on a path element feeds an ingate; the ingate's inlet
              section must fit on the runner's face there
  part        the outlet section must sit on the picked part face (contact
              patch) and runs embed into the part (penetration)
  blind       a dead end: nothing may flow on out of the downstream element
  tee         a path element (an ingate) drawn from a point on another element
              (a runner) at the parting plane: its start must lie on that
              element; the upstream element keeps the overlap
  gate        an ingate's end meets the part (side wall or, across the
              parting plane, its face); the part keeps the overlap

Taps without a station (or with side "side") are solved here: the station is
the point of the path nearest the branch's inlet, the side the one facing it.
Port constraints (on_parting_plane, normal horizontal/vertical) are checked on
every placed port.  Last, every joint's overlap goes to its downstream solid.
"""
from __future__ import annotations

import numpy as np
from build123d import Solid

from core.element import Severity, ValidationIssue
from core.graph import Connection, End
from core.occ import common_volume, cut, section_wire, slab, solids_volume
from core.placement import GAP_MM, gap_issue

SECTION_TOL = 0.01
CONTACT_MIN = 0.99          # fraction of a section that must sit on the face it joins
PLANE_TOL_MM = 0.01
ANGLE_TOL_DEG = 0.01


def resolve_tap(res, c: Connection) -> End | None:
    e = c.frm
    b = res.built.get(e.instance)
    if b is None or not (b.element.is_path and e.port.startswith("tap")):
        return e
    if c.id in res.ends:
        return res.ends[c.id]
    side = e.side or "side"
    if e.station is not None and side != "side":
        res.ends[c.id] = e
        return e
    d = res.built.get(c.to.instance)
    target = np.asarray(d.port(c.to.port).frame.origin) if d is not None else None
    if e.station is None and target is None:
        return None
    s = e.station if e.station is not None else b.element.nearest_station(b.params, target)
    if side == "side":
        if target is None:
            side = "left"
        else:
            side = min(("left", "right"), key=lambda sd: np.linalg.norm(
                np.subtract(b.element.tap_frame(b.params, s, sd).origin, target)))
    r = End(e.instance, e.port, float(s), side)
    res.ends[c.id] = r
    return r


def _contact(section, frame, target, depth, side) -> float:
    """Fraction of a thin plate of the section (beside the plane, on `side`) that lies in target."""
    s = slab(section, frame, depth, side)
    v = solids_volume(s)
    return common_volume(s, target) / v if v > 0 else 0.0


def _constraints(inst: str, port, issues: list[ValidationIssue]) -> None:
    c = port.constraints or {}
    o, n = port.frame.origin, port.frame.normal
    if c.get("on_parting_plane") and abs(o[2]) > PLANE_TOL_MM:
        issues.append(ValidationIssue("port.on_parting_plane", Severity.ERROR,
                                      f"{inst}.{port.id} must lie on the parting plane; it is at z = {o[2]:.3f} mm",
                                      value=round(o[2], 4), limit=0.0, instances=(inst,)))
    want = c.get("normal")
    if want == "horizontal" and abs(n[2]) > np.sin(np.radians(ANGLE_TOL_DEG)):
        issues.append(ValidationIssue("port.normal_horizontal", Severity.ERROR,
                                      f"{inst}.{port.id} must point horizontally",
                                      value=round(float(np.degrees(np.arcsin(abs(n[2])))), 3), limit=0.0,
                                      instances=(inst,)))
    if want == "vertical" and abs(n[2]) < np.cos(np.radians(ANGLE_TOL_DEG)):
        issues.append(ValidationIssue("port.normal_vertical", Severity.ERROR,
                                      f"{inst}.{port.id} must point vertically",
                                      value=round(float(np.degrees(np.arccos(abs(n[2])))), 3), limit=0.0,
                                      instances=(inst,)))


def _check_drop(c, a, pa, b, issues) -> None:
    """A drop: the upstream outlet comes straight down onto the start of a path
    element and ends on its floor, so the metal turns into the path there."""
    if not b.element.is_path:
        issues.append(ValidationIssue("joint.drop_target", Severity.ERROR,
                                      f"{c.to.label}: a drop joint must end on a runner (a path element)",
                                      instances=(a.instance, b.instance), connection=c.id))
        return
    if abs(abs(pa.frame.normal[2]) - 1.0) > 1e-6:
        issues.append(ValidationIssue("joint.drop_not_vertical", Severity.ERROR,
                                      f"{c.frm.label} must point straight down to drop into {b.instance}",
                                      instances=(a.instance, b.instance), connection=c.id))
        return
    # at or below the floor: with runners at different depths the sprue goes down to the deepest
    off = float(pa.frame.origin[2]) - b.element.floor_z(b.params)
    if off > GAP_MM:
        issues.append(ValidationIssue(
            "joint.drop_floor", Severity.ERROR, f"{a.instance} ends {off:.2f} mm above the floor of {b.instance}",
            value=round(off, 4), limit=0.0, instances=(a.instance, b.instance), connection=c.id))


def _gap(a, b) -> float:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    d = BRepExtrema_DistShapeShape(a.wrapped, b.wrapped)
    return float(d.Value()) if d.IsDone() else float("inf")


def _check_tee(c, a, b, issues) -> None:
    """The ingate (b) must start on the runner (a): its first point, on the parting plane, touches it."""
    from build123d import Vertex
    if not b.element.is_path:
        issues.append(ValidationIssue("joint.tee_target", Severity.ERROR,
                                      f"{c.to.label}: a tee must start a path element (an ingate)",
                                      instances=(a.instance, b.instance), connection=c.id))
        return
    s = b.element.points(b.params)[0]
    d = _gap(Vertex(float(s[0]), float(s[1]), float(s[2])), a.solid)
    if d > GAP_MM:
        issues.append(ValidationIssue("joint.tee_off", Severity.ERROR,
                                      f"{b.instance} starts {d:.1f} mm away from {a.instance}",
                                      value=round(d, 3), limit=0.0, instances=(a.instance, b.instance),
                                      connection=c.id))


def _check_gate(c, a, res, issues) -> None:
    """The ingate must reach the part (any of its cavities)."""
    if res.part is None:
        return
    d = _gap(a.solid, res.part.metal)
    if d > GAP_MM:
        issues.append(ValidationIssue("joint.gate_short", Severity.ERROR,
                                      f"{a.instance} stops {d:.1f} mm short of the part",
                                      value=round(d, 3), limit=0.0, instances=(a.instance,), connection=c.id))


def check_and_join(res) -> None:
    from core.regen import BuiltTransition
    issues = res.issues
    cuts: dict[tuple, list] = {}
    for c in res.graph.connections:
        a = res.built.get(c.frm.instance)
        if a is None:
            continue
        e = resolve_tap(res, c)
        if e is None:
            issues.append(ValidationIssue("joint.tap_unsolved", Severity.ERROR,
                                          f"tap {c.frm.label} has no station and nothing placed to face",
                                          instances=(a.instance,), connection=c.id))
            continue
        pa = a.port(e)
        if c.joint == "gate":
            _check_gate(c, a, res, issues)
            if res.part is not None:
                cuts.setdefault(("e", a.instance), []).append(res.part.metal)
            continue
        if c.to_part:
            if res.part is None or c.to.port not in res.part.ports:
                continue
            gi = gap_issue(c, pa.frame, res.part.ports[c.to.port])
            if gi:
                issues.append(gi)
            elif pa.section is not None:
                f = _contact(pa.section, pa.frame, res.part.metal, max(a.params.embed, 0.2), +1)
                if f < CONTACT_MIN:
                    issues.append(ValidationIssue(
                        "joint.part_contact", Severity.ERROR,
                        f"only {100 * f:.1f}% of {c.frm.label}'s section sits on the part face",
                        value=round(f, 4), limit=CONTACT_MIN, instances=(a.instance,), connection=c.id))
            cuts.setdefault(("e", a.instance), []).append(res.part.metal)
            continue
        b = res.built.get(c.to.instance)
        if b is None:
            continue
        pb = b.port(c.to.port)
        if c.joint == "transition":
            L = (c.transition or {}).get("length_mm")
            if not L:
                issues.append(ValidationIssue("joint.transition_length", Severity.ERROR,
                                              "a transition joint needs transition.length_mm",
                                              instances=(a.instance, b.instance), connection=c.id))
                continue
            gi = gap_issue(c, pa.frame.offset(L), pb.frame)
            if gi:
                issues.append(gi)
                continue
            ea, eb = a.params.embed, b.params.embed
            if L <= ea + eb:
                issues.append(ValidationIssue("joint.transition_length", Severity.ERROR,
                                              f"transition length {L} mm must exceed the two embeds ({ea + eb} mm)",
                                              value=L, limit=ea + eb, instances=(a.instance, b.instance),
                                              connection=c.id))
                continue
            try:
                # Each end runs straight, in its neighbour's own section, across that neighbour's embed;
                # the taper is ruled between.  So every overlap is prism on prism (exact booleans).
                loft = Solid.make_loft([section_wire(pa.section, pa.frame.offset(-ea)),
                                        section_wire(pa.section, pa.frame.offset(ea)),
                                        section_wire(pb.section, pb.frame.offset(-eb)),
                                        section_wire(pb.section, pb.frame.offset(eb))], ruled=True)
                if not loft.is_valid:
                    raise RuntimeError("invalid loft")
            except Exception as ex:                          # noqa: BLE001
                issues.append(ValidationIssue("joint.transition_failed", Severity.ERROR, f"loft failed: {ex}",
                                              instances=(a.instance, b.instance), connection=c.id))
                continue
            res.transitions[c.id] = BuiltTransition(c.id, f"gating.{c.id}", loft, loft)
            cuts.setdefault(("e", a.instance), []).append(loft)
            cuts.setdefault(("t", c.id), []).append(b.solid)
            continue
        if c.joint == "tee":
            _check_tee(c, a, b, issues)
            cuts.setdefault(("e", b.instance), []).append(a.solid)
            continue
        if c.joint == "drop":
            _check_drop(c, a, pa, b, issues)
            cuts.setdefault(("e", a.instance), []).append(b.solid)
            continue
        gi = gap_issue(c, pa.frame, pb.frame)
        if gi:
            issues.append(gi)
        if c.joint == "butt" and pa.section and pb.section and not pa.section.matches(pb.section, SECTION_TOL):
            issues.append(ValidationIssue(
                "joint.butt_section_mismatch", Severity.ERROR,
                f"{c.frm.label} is {pa.section.shape} {pa.section.area:.1f} mm2, "
                f"{c.to.label} is {pb.section.shape} {pb.section.area:.1f} mm2; use a transition joint",
                value=round(pb.section.area, 3), limit=round(pa.section.area, 3),
                instances=(a.instance, b.instance), connection=c.id))
        if c.joint == "branch" and pb.section is not None and not gi:
            f = _contact(pb.section, pb.frame, a.solid, max(b.params.embed, 0.2), -1)
            if f < CONTACT_MIN:
                issues.append(ValidationIssue(
                    "joint.branch_fit", Severity.ERROR,
                    f"{c.to.label}'s section hangs off {e.instance}'s face: {100 * f:.1f}% of it is on the runner",
                    value=round(f, 4), limit=CONTACT_MIN, instances=(a.instance, b.instance), connection=c.id))
        if c.joint == "blind" and res.graph.outgoing(b.instance):
            issues.append(ValidationIssue("joint.blind_outflow", Severity.ERROR,
                                          f"{b.instance} closes a path (blind joint) but metal flows on out of it",
                                          instances=(b.instance,), connection=c.id))
        cuts.setdefault(("e", a.instance), []).append(b.solid)

    for inst, b in res.built.items():
        for port in b.ports():
            _constraints(inst, port, issues)

    # runners a sprue drops into share metal under it: each later one gives that up to the earlier ones
    drops: dict[str, list[str]] = {}
    for c in res.graph.connections:
        if c.joint == "drop" and c.frm.instance in res.built and c.to.instance in res.built:
            drops.setdefault(c.frm.instance, []).append(c.to.instance)
    for sibs in drops.values():
        for k, later in enumerate(sibs[1:], 1):
            cuts.setdefault(("e", later), []).extend(res.built[x].solid for x in sibs[:k])
    for (kind, key), tools in cuts.items():
        obj = res.built[key] if kind == "e" else res.transitions[key]
        try:
            trimmed = cut(obj.solid, tools)
            if not trimmed.solids() or solids_volume(trimmed) <= 0:
                raise RuntimeError("nothing left")
            obj.tagged = trimmed
        except Exception as ex:                              # noqa: BLE001
            issues.append(ValidationIssue("joint.overlap_cut_failed", Severity.WARNING,
                                          f"overlap not trimmed ({ex}); the solver regions overlap here",
                                          instances=(key,)))
