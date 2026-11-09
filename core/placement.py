"""Placement: where each element sits in the mold frame.

Solved in passes over the topological order.  An element is placed by the first of:
  1. its anchor (a fixed frame in the mold: the pouring point, a free element),
  2. a part joint (its port lands on the picked part face: a fixed anchor),
  3. chain placement (its inlet port lands on the upstream port it attaches to),
  4. back-chaining (its outlet lands on a placed downstream port), so a chain
     hung only from an ingate's part contact is placed back up to the sprue.
Any further connection is then a constraint to check: its two port frames must
coincide.  Runner paths solved between two fixed ends come in Phase 1.2; until
then such a gap is reported, not closed.
"""
from __future__ import annotations

import numpy as np

from core.element import Element, Params, Severity, ValidationIssue
from core.frames import Frame, placement_onto
from core.graph import Connection

IDENTITY = Frame((0, 0, 0), (1, 0, 0), (0, 0, 1))
GAP_MM = 1e-3
GAP_DEG = 0.01


def place(inst: str, el: Element, p: Params, anchor: dict | None, incoming: list[Connection],
          outgoing: list[Connection], world_port, part_ports: dict[str, Frame]
          ) -> tuple[np.ndarray | None, list[ValidationIssue]]:
    """Local -> mold transform for one element, or None while nothing it hangs
    off is placed yet (the engine tries again on its next pass)."""
    if anchor:
        local = el.port(p, anchor["port"]).frame if anchor.get("port") else IDENTITY
        return placement_onto(local, Frame.from_dict(anchor["frame"])), []
    for c in outgoing:
        if c.to_part:
            return placement_onto(el.port(p, c.frm.port, c.frm.station, c.frm.side).frame,
                                  part_ports[c.to.port]), []
    for c in incoming:                                   # chain: hang off the upstream port
        up = world_port(c.frm)
        if up is not None:
            return placement_onto(el.port(p, c.to.port).frame, up.frame.offset(_gap(c))), []
    for c in outgoing:                                   # or back off a placed downstream port
        if c.frm.port.startswith("tap"):
            continue                                     # a path element is never placed from its taps
        down = world_port(c.to)
        if down is not None:
            return placement_onto(el.port(p, c.frm.port).frame, down.frame.offset(-_gap(c))), []
    return None, []


def _gap(c: Connection) -> float:
    """A transition joint leaves room for its loft between the two ports."""
    return float((c.transition or {}).get("length_mm") or 0.0) if c.joint == "transition" else 0.0


def gap_issue(c: Connection, a: Frame, b: Frame) -> ValidationIssue | None:
    d, ang, twist = a.gap(b)
    if d <= GAP_MM and ang <= GAP_DEG:
        return None
    return ValidationIssue(
        "placement.joint_gap", Severity.ERROR,
        f"ports {c.frm.label} and {c.to.label} do not meet: {d:.3f} mm apart, {ang:.2f} deg between normals",
        value=round(d, 4), limit=GAP_MM, instances=(c.frm.instance,) + (() if c.to_part else (c.to.instance,)),
        connection=c.id)
