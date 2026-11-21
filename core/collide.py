"""Collisions: gating that runs into other gating, the part or a core where
no joint says it may.

Allowed: the two solids of a connection (they overlap by their embeds), runners
that one sprue drops into (they meet under it, and the sprue, filter or well stacked
over the one they drop from runs down through them), path elements that branch off one
element at the same point (they meet there, as at a T or an H junction), and an
element's penetration into the part at its part joint, up to its section area
times its embed (plus 5%).  Anything else over VOLUME_TOL is an error named for
both sides.
"""
from __future__ import annotations

import hashlib
from itertools import combinations

import numpy as np

from core.element import Severity, ValidationIssue
from core.occ import boxes_touch, common_volume_cached

VOLUME_TOL = 1.0            # mm3


def check(res) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    items = {i: b.solid for i, b in res.built.items()}
    items.update({t.connection: t.solid for t in res.transitions.values()})
    keys = {i: solid_key(b) for i, b in res.built.items()}
    keys.update({t.connection: _transition_key(t) for t in res.transitions.values()})
    allowed = set()
    part_allow: dict[str, float] = {}
    for c in res.graph.connections:
        if c.to_part:
            b = res.built.get(c.frm.instance)
            if b is not None:
                e = res.frm(c)
                try:
                    sec = b.port(e).section
                except Exception:                                # noqa: BLE001
                    sec = None
                part_allow[b.instance] = part_allow.get(b.instance, 0.0) + (
                    1.05 * sec.area * b.params.embed if sec else 0.0)
            continue
        allowed.add(frozenset((c.frm.instance, c.to.instance)))
        if c.id in res.transitions:
            allowed.add(frozenset((c.frm.instance, c.id)))
            allowed.add(frozenset((c.id, c.to.instance)))

    drops: dict[str, list[str]] = {}                 # runners fed by one sprue meet under it
    for c in res.graph.connections:
        if c.joint == "drop":
            drops.setdefault(c.frm.instance, []).append(c.to.instance)
    for sibs in drops.values():
        allowed.update(frozenset(p) for p in combinations(sibs, 2))
    above: dict[str, set[str]] = {}                 # what stands stacked over each element on the sprue axis
    for c in res.graph.connections:
        if c.joint in ("stack", "butt"):
            above.setdefault(c.to.instance, set()).add(c.frm.instance)
    for src, sibs in drops.items():
        stack, todo = set(), [src]
        while todo:
            for u in above.get(todo.pop(), ()):
                if u not in stack:
                    stack.add(u)
                    todo.append(u)
        allowed.update(frozenset((u, r)) for u in stack for r in sibs)
    tees: dict[tuple, list[str]] = {}               # branches off one element at one point meet there
    for c in res.graph.connections:
        if c.joint != "tee":
            continue
        d = res.built.get(c.to.instance)
        if d is None or not d.element.is_path:
            continue
        start = np.round(np.asarray(d.port("inlet").frame.origin, float)[:2], 3)
        tees.setdefault((c.frm.instance, *start.tolist()), []).append(c.to.instance)
    for sibs in tees.values():
        allowed.update(frozenset(p) for p in combinations(sibs, 2))

    def overlap(a, b, key, a_box=None, b_box=None) -> float:
        if not boxes_touch(a if a_box is None else a_box, b if b_box is None else b_box):
            return 0.0
        try:
            return common_volume_cached(key, a, b)
        except Exception:                                        # noqa: BLE001
            return 0.0

    for x, y in combinations(sorted(items), 2):
        if frozenset((x, y)) in allowed:
            continue
        v = overlap(items[x], items[y], (keys[x], keys[y]))
        if v > VOLUME_TOL:
            issues.append(ValidationIssue("collision.gating", Severity.ERROR, f"{x} and {y} overlap by {v:.1f} mm3",
                                          value=round(v, 2), limit=VOLUME_TOL, instances=(x, y)))
    if res.part is None:
        return issues
    metal, metal_box = res.part.metal, res.part.bbox     # the part's box from its mesh: exact takes seconds
    for x, s in items.items():
        v = overlap(s, metal, (keys[x], "part", res.part.key), b_box=metal_box)
        lim = part_allow.get(x, 0.0) + VOLUME_TOL
        if v > lim:
            issues.append(ValidationIssue("collision.part", Severity.ERROR,
                                          f"{x} runs {v:.1f} mm3 into the part" +
                                          (f" (its part joint allows {lim:.1f})" if x in part_allow else ""),
                                          value=round(v, 2), limit=round(lim, 2), instances=(x,)))
        for k, core in res.part.cores.items():
            v = overlap(s, core, (keys[x], "core", k, res.part.key))
            if v > VOLUME_TOL:
                issues.append(ValidationIssue("collision.core", Severity.ERROR,
                                              f"{x} crosses core '{k}' ({v:.1f} mm3)", value=round(v, 2),
                                              limit=VOLUME_TOL, instances=(x,)))
    return issues


def solid_key(b) -> str:
    """What decides a built element's solid: its type and version, parameters and placement."""
    return hashlib.sha1(f"{b.type}:{b.element.version}:{sorted(b.params.items())}:"
                        f"{np.round(b.transform, 9).tobytes().hex()}".encode()).hexdigest()


def _transition_key(t) -> str:
    return hashlib.sha1(f"{t.connection}:{t.solid.volume:.9f}:{tuple(t.solid.center())}".encode()).hexdigest()
