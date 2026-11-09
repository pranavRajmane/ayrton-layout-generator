"""Propagate calculator results into element parameters, on request: one JSON
Patch (one undo step) that sets every parameter declared driven_by a calculator
value, and sizes each sprue, runner and ingate for its share of the area the
gating ratio gives its role."""
from __future__ import annotations

from core.element import Severity, ValidationIssue

ROLES = ("sprue", "runner", "ingate")


def propagation_patch(res) -> tuple[list[dict], list[ValidationIssue]]:
    cr = res.calc
    idx = {e["id"]: i for i, e in enumerate(res.doc.data["elements"])}
    saved = {e["id"]: e["params"] for e in res.doc.data["elements"]}
    changes: dict[str, dict] = {i: dict(v) for i, v in cr.driven.items()}
    issues = []
    for inst, b in res.built.items():
        role = b.element.gating_role
        area = cr.outputs.get(f"{role}_area_each_mm2") if role else None
        if area is None:
            continue
        try:
            changes.setdefault(inst, {}).update(b.element.size_for_area(b.params, area))
        except NotImplementedError as e:
            issues.append(ValidationIssue("calc.cannot_size", Severity.WARNING, str(e), value=area, instances=(inst,)))
    ops = []
    for inst, vals in changes.items():
        for name, v in vals.items():
            op = "replace" if name in saved[inst] else "add"
            ops.append({"op": op, "path": f"/elements/{idx[inst]}/params/{name}", "value": float(v)})
    return ops, issues
