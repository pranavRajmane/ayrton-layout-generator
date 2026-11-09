"""Collisions: gating that runs into other gating, the part or a core where
no joint says it may.

Allowed: the two solids of a connection (they overlap by their embeds), runners
that one sprue drops into (they meet under it), and an
element's penetration into the part at its part joint, up to its section area
times its embed (plus 5%).  Anything else over VOLUME_TOL is an error named for
both sides.
"""
from __future__ import annotations

from itertools import combinations

from core.element import Severity, ValidationIssue
from core.occ import boxes_touch, common_volume

VOLUME_TOL = 1.0            # mm3


def check(res) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    items = {i: b.solid for i, b in res.built.items()}
    items.update({t.connection: t.solid for t in res.transitions.values()})
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

    def overlap(a, b) -> float:
        if not boxes_touch(a, b):
            return 0.0
        try:
            return common_volume(a, b)
        except Exception:                                        # noqa: BLE001
            return 0.0

    for x, y in combinations(sorted(items), 2):
        if frozenset((x, y)) in allowed:
            continue
        v = overlap(items[x], items[y])
        if v > VOLUME_TOL:
            issues.append(ValidationIssue("collision.gating", Severity.ERROR, f"{x} and {y} overlap by {v:.1f} mm3",
                                          value=round(v, 2), limit=VOLUME_TOL, instances=(x, y)))
    if res.part is None:
        return issues
    for x, s in items.items():
        v = overlap(s, res.part.metal)
        lim = part_allow.get(x, 0.0) + VOLUME_TOL
        if v > lim:
            issues.append(ValidationIssue("collision.part", Severity.ERROR,
                                          f"{x} runs {v:.1f} mm3 into the part" +
                                          (f" (its part joint allows {lim:.1f})" if x in part_allow else ""),
                                          value=round(v, 2), limit=round(lim, 2), instances=(x,)))
        for k, core in res.part.cores.items():
            v = overlap(s, core)
            if v > VOLUME_TOL:
                issues.append(ValidationIssue("collision.core", Severity.ERROR,
                                              f"{x} crosses core '{k}' ({v:.1f} mm3)", value=round(v, 2),
                                              limit=VOLUME_TOL, instances=(x,)))
    return issues
