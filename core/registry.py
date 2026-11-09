"""Element registry: finds element plugins and exposes their parameters.

Plugins register themselves on import (``@register_element``); ``discover``
imports every module of a package so they get the chance.  The core never
imports an element by name.
"""
from __future__ import annotations

import importlib
import pkgutil

from core.element import ALLOYS, CATEGORIES, ID_RE, PROCESSES, Element, Param
from core.units import PARAM_UNITS


class RegistryError(ValueError):
    pass


def contract_problems(cls) -> list[str]:
    """Class-level contract checks, run when an element registers."""
    out = []
    if not (isinstance(cls, type) and issubclass(cls, Element)):
        return [f"{cls!r} is not an Element subclass"]
    eid = getattr(cls, "id", None)
    if not isinstance(eid, str) or not ID_RE.match(eid):
        out.append(f"{cls.__name__}.id must be snake_case, got {eid!r}")
    if not isinstance(getattr(cls, "version", None), str):
        out.append(f"{cls.__name__}.version must be a string")
    if getattr(cls, "category", None) not in CATEGORIES:
        out.append(f"{cls.__name__}.category must be one of {CATEGORIES}")
    out += [f"{cls.__name__}: unknown process '{x}'" for x in cls.processes if x not in PROCESSES]
    out += [f"{cls.__name__}: unknown alloy '{x}'" for x in cls.alloys if x not in ALLOYS]
    names = []
    for p in cls.params:
        if not isinstance(p, Param):
            out.append(f"{cls.__name__}: params must be Param, got {p!r}")
            continue
        names.append(p.name)
        if p.unit not in PARAM_UNITS:
            out.append(f"{cls.__name__}.{p.name}: unit '{p.unit}' is not one of {sorted(PARAM_UNITS)}")
        try:
            p.coerce(p.default)
        except ValueError as e:
            out.append(f"{cls.__name__}: default rejected: {e}")
    out += [f"{cls.__name__}: parameter '{n}' declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    if getattr(cls, "gating_role", None) not in (None, "sprue", "runner", "ingate"):
        out.append(f"{cls.__name__}.gating_role must be sprue, runner, ingate or None")
    for m in ("ports", "build"):
        if getattr(cls, m) is getattr(Element, m):
            out.append(f"{cls.__name__} must implement {m}()")
    return out


class Registry:
    def __init__(self):
        self._types: dict[str, type[Element]] = {}

    def add(self, cls: type[Element]) -> None:
        problems = contract_problems(cls)
        if problems:
            raise RegistryError("; ".join(problems))
        old = self._types.get(cls.id)
        if old is not None and (old.__module__, old.__qualname__) != (cls.__module__, cls.__qualname__):
            raise RegistryError(f"element id '{cls.id}' registered by {old.__module__} and {cls.__module__}")
        self._types[cls.id] = cls

    def get(self, type_id: str) -> type[Element]:
        try:
            return self._types[type_id]
        except KeyError:
            raise KeyError(f"unknown element type '{type_id}'") from None

    def __contains__(self, type_id: str) -> bool:
        return type_id in self._types

    def types(self) -> list[str]:
        return sorted(self._types)

    def describe(self) -> list[dict]:
        return [self._types[t].describe() for t in self.types()]

    def discover(self, package: str = "elements") -> list[str]:
        pkg = importlib.import_module(package)
        for m in pkgutil.iter_modules(pkg.__path__):
            if not m.name.startswith("_"):
                importlib.import_module(f"{package}.{m.name}")
        return self.types()


REGISTRY = Registry()
