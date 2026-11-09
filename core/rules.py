"""System-level rules: plugins in rules/, each checking the whole regenerated
design (graph, solids, calculator results) and naming the instances involved.
Element-level rules live in the elements' own validate()."""
from __future__ import annotations

import importlib
import pkgutil
from typing import ClassVar

from core.element import Severity, ValidationIssue


class Rule:
    id: ClassVar[str]
    severity: ClassVar[Severity] = Severity.WARNING
    applies_to: ClassVar[list[str]] = []          # processes; empty: all

    def check(self, design, context) -> list[ValidationIssue]:
        raise NotImplementedError

    def issue(self, message: str, instances=(), value=None, limit=None, connection=None,
              severity: Severity | None = None) -> ValidationIssue:
        return ValidationIssue(self.id, severity or self.severity, message, value, limit, tuple(instances), connection)


RULES: dict[str, type[Rule]] = {}


def register_rule(cls: type[Rule]) -> type[Rule]:
    if not getattr(cls, "id", "").startswith("system."):
        raise ValueError(f"{cls.__name__}: system rule ids start with 'system.'")
    old = RULES.get(cls.id)
    if old is not None and (old.__module__, old.__qualname__) != (cls.__module__, cls.__qualname__):
        raise ValueError(f"rule id '{cls.id}' registered twice")
    RULES[cls.id] = cls
    return cls


def discover(package: str = "rules") -> list[str]:
    pkg = importlib.import_module(package)
    for m in pkgutil.iter_modules(pkg.__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{package}.{m.name}")
    return sorted(RULES)


def run(design, context) -> list[ValidationIssue]:
    if not RULES:
        discover()
    process = design.doc.data["process"].get("process")
    out = []
    for rid in sorted(RULES):
        rule = RULES[rid]()
        if rule.applies_to and process not in rule.applies_to:
            continue
        try:
            out += rule.check(design, context)
        except Exception as e:                                   # noqa: BLE001 - a broken rule is itself an issue
            out.append(ValidationIssue("rules.failed", Severity.ERROR, f"rule {rid} failed: {type(e).__name__}: {e}"))
    return out
