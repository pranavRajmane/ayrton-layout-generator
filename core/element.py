"""The element contract.  Every gating element is a plugin that subclasses
``Element`` and registers itself with ``@register_element``; the core only ever
talks to this interface.

An element declares typed parameters, builds ONE solid in its own local frame,
exposes ports (planes where metal enters or leaves), checks its own rules and
names its solver tags.  The framework adds an ``embed`` parameter to every
element: build() must run the solid that far past each port plane, upstream of
an inlet and downstream of an outlet, so neighbouring solids overlap instead of
touching (coplanar contact is what makes OCCT booleans fail).
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from core.frames import Frame
from core.units import PARAM_UNITS

CATEGORIES = ("entry", "junction", "distribution", "cleaning", "venting", "feeding", "test")
PROCESSES = ("green_sand", "no_bake")
ALLOYS = ("grey_iron", "sg_iron", "steel")
SIDES = ("top", "bottom", "side", "left", "right")     # side: the side facing what attaches
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PortKind(str, Enum):
    INLET = "inlet"
    OUTLET = "outlet"
    TAP = "tap"
    PART_FACE = "part_face"
    BLIND = "blind"


PortType = PortKind          # the name the element templates use

# Ports metal leaves through: the solid's embed runs downstream of these.
FLOW_OUT = {PortKind.OUTLET, PortKind.TAP, PortKind.PART_FACE}


class SectionPolicy(str, Enum):
    DRIVES = "drives"        # sets the section for whatever attaches
    FOLLOWS = "follows"      # adopts the upstream section
    OWN = "own"              # keeps its own; a transition is needed if it differs


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Section:
    """A flow cross-section.  round: d; rectangle: width, height;
    trapezoid: top, bottom, height (widths at the top and bottom faces)."""
    shape: str
    dims: tuple[tuple[str, float], ...]

    SHAPES: ClassVar[dict[str, tuple[str, ...]]] = {
        "round": ("d",), "rectangle": ("width", "height"), "trapezoid": ("top", "bottom", "height")}

    def __post_init__(self):
        if self.shape not in self.SHAPES:
            raise ValueError(f"unknown section shape '{self.shape}'")
        names = tuple(k for k, _ in self.dims)
        if set(names) != set(self.SHAPES[self.shape]):
            raise ValueError(f"{self.shape} section needs {self.SHAPES[self.shape]}, got {names}")
        if any(v <= 0 for _, v in self.dims):
            raise ValueError("section dimensions must be positive")

    @classmethod
    def round(cls, d: float) -> "Section":
        return cls("round", (("d", float(d)),))

    @classmethod
    def rectangle(cls, width: float, height: float) -> "Section":
        return cls("rectangle", (("width", float(width)), ("height", float(height))))

    @classmethod
    def trapezoid(cls, top: float, bottom: float, height: float) -> "Section":
        return cls("trapezoid", (("top", float(top)), ("bottom", float(bottom)), ("height", float(height))))

    @property
    def d(self) -> dict[str, float]:
        return dict(self.dims)

    @property
    def area(self) -> float:
        d = self.d
        if self.shape == "round":
            return math.pi * d["d"] ** 2 / 4.0
        if self.shape == "rectangle":
            return d["width"] * d["height"]
        return 0.5 * (d["top"] + d["bottom"]) * d["height"]

    @property
    def perimeter(self) -> float:
        if self.shape == "round":
            return math.pi * self.d["d"]
        pts = self.outline()
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:] + pts[:1]))

    def outline(self, n: int = 64) -> list[tuple[float, float]]:
        """Closed outline in the port plane as (side, up) points, counter-clockwise
        seen from upstream; round sections as an n-gon of equal area."""
        d = self.d
        if self.shape == "round":
            r = d["d"] / 2 * math.sqrt(2 * math.pi / (n * math.sin(2 * math.pi / n)))
            return [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        if self.shape == "rectangle":
            w, h = d["width"] / 2, d["height"] / 2
            return [(-w, -h), (w, -h), (w, h), (-w, h)]
        b, t, h = d["bottom"] / 2, d["top"] / 2, d["height"] / 2
        return [(-b, -h), (b, -h), (t, h), (-t, h)]

    def canonical(self) -> "Section":
        """A trapezoid with equal widths is a rectangle."""
        d = self.d
        if self.shape == "trapezoid" and math.isclose(d["top"], d["bottom"], rel_tol=1e-9):
            return Section.rectangle(d["top"], d["height"])
        return self

    def matches(self, other: "Section", rel_tol: float = 0.01) -> bool:
        a, b = self.canonical(), other.canonical()
        return a.shape == b.shape and all(math.isclose(v, b.d[k], rel_tol=rel_tol) for k, v in a.dims)

    def to_dict(self) -> dict:
        return {"shape": self.shape, "dims": self.d, "area": self.area}


@dataclass(frozen=True)
class Port:
    id: str
    kind: PortKind
    frame: Frame
    section: Section | None = None
    section_policy: SectionPolicy = SectionPolicy.OWN
    constraints: Mapping[str, Any] = field(default_factory=dict)   # on_parting_plane, normal, side

    def transformed(self, m) -> "Port":
        return Port(self.id, self.kind, self.frame.transformed(m), self.section, self.section_policy,
                    self.constraints)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind.value, "frame": self.frame.to_dict(),
                "section": self.section.to_dict() if self.section else None,
                "section_policy": self.section_policy.value, "constraints": dict(self.constraints)}


@dataclass(frozen=True)
class Param:
    name: str
    unit: str = ""
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None
    driven_by: str | None = None          # e.g. "calculators.choke_area"
    label: str | None = None
    description: str = ""
    kind: str | None = None               # "points": a list of (x, y, z) in the mold frame

    def __post_init__(self):
        if self.choices is not None:
            object.__setattr__(self, "choices", tuple(self.choices))

    @property
    def type(self) -> type:
        if self.kind == "points":
            return tuple
        if self.choices is not None:
            return type(self.choices[0])
        return float if isinstance(self.default, int) and not isinstance(self.default, bool) \
            else type(self.default)

    def coerce(self, value):
        if self.kind == "points":
            try:
                pts = tuple(tuple(float(x) for x in q) for q in value)
            except (TypeError, ValueError):
                raise ValueError(f"{self.name}: expected a list of [x, y, z] points") from None
            if any(len(q) != 3 for q in pts) or not all(math.isfinite(x) for q in pts for x in q):
                raise ValueError(f"{self.name}: expected a list of [x, y, z] points")
            return pts
        if self.choices is not None:
            if value not in self.choices:
                raise ValueError(f"{self.name}: {value!r} is not one of {self.choices}")
            return value
        t = self.type
        if t is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{self.name}: expected a number, got {value!r}")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{self.name}: {value} is not finite")
        elif t is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{self.name}: expected true/false, got {value!r}")
        elif not isinstance(value, t):
            raise ValueError(f"{self.name}: expected {t.__name__}, got {value!r}")
        if self.min is not None and value < self.min:
            raise ValueError(f"{self.name} = {value} {self.unit} is below the minimum {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"{self.name} = {value} {self.unit} is above the maximum {self.max}")
        return value

    def schema(self) -> dict:
        return {"name": self.name, "label": self.label or self.name.replace("_", " "), "unit": self.unit,
                "type": "points" if self.kind == "points" else self.type.__name__, "default": self.default, "min": self.min, "max": self.max,
                "choices": list(self.choices) if self.choices else None, "driven_by": self.driven_by,
                "description": self.description}


EMBED = Param("embed", unit="mm", default=1.0, min=0.0, max=10.0,
              description="how far the solid runs past each port plane so neighbours overlap")


class Params(Mapping):
    """Resolved parameter values; read-only, by key or by attribute."""

    def __init__(self, values: dict):
        object.__setattr__(self, "_v", dict(values))

    def __getitem__(self, k):
        return self._v[k]

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __getattr__(self, k):
        try:
            return self._v[k]
        except KeyError:
            raise AttributeError(k) from None

    def __setattr__(self, k, v):
        raise AttributeError("Params are read-only; use replace()")

    def replace(self, **changes) -> "Params":
        return Params({**self._v, **changes})

    def __repr__(self):
        return f"Params({self._v})"


@dataclass(frozen=True)
class ValidationIssue:
    rule: str
    severity: Severity
    message: str
    value: Any = None
    limit: Any = None
    instances: tuple[str, ...] = ()
    connection: str | None = None

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": Severity(self.severity).value, "message": self.message,
                "value": self.value, "limit": self.limit, "instances": list(self.instances),
                "connection": self.connection}


@dataclass
class ValidationContext:
    """What an element may look at when it checks itself."""
    instance_id: str
    process: str | None = None
    alloy: str | None = None
    upstream_sections: dict[str, Section] = field(default_factory=dict)   # by this element's port id
    calculators: dict[str, Any] = field(default_factory=dict)             # TODO(phase 1.3): calculator results


class Element:
    id: ClassVar[str]
    version: ClassVar[str]
    category: ClassVar[str]
    processes: ClassVar[list[str]] = []
    alloys: ClassVar[list[str]] = []
    params: ClassVar[list[Param]] = []
    is_path: ClassVar[bool] = False          # path elements (runners) expose taps
    gating_role: ClassVar[str | None] = None   # "sprue" | "runner" | "ingate": its member of the gating ratio

    # ---- parameters ----------------------------------------------------------
    @classmethod
    def all_params(cls) -> list[Param]:
        names = {p.name for p in cls.params}
        return list(cls.params) + ([] if "embed" in names else [EMBED])

    @classmethod
    def resolve_params(cls, raw: Mapping | None = None) -> Params:
        raw = dict(raw or {})
        decl = {p.name: p for p in cls.all_params()}
        unknown = sorted(set(raw) - set(decl))
        if unknown:
            raise ValueError(f"{cls.id}: unknown parameter(s) {unknown}")
        return Params({n: p.coerce(raw.get(n, p.default)) for n, p in decl.items()})

    @classmethod
    def describe(cls) -> dict:
        return {"id": cls.id, "version": cls.version, "category": cls.category,
                "processes": list(cls.processes), "alloys": list(cls.alloys), "is_path": cls.is_path,
                "gating_role": cls.gating_role,
                "params": [p.schema() for p in cls.all_params()]}

    # ---- the contract ------------------------------------------------------
    def ports(self, p: Params) -> list[Port]:
        raise NotImplementedError

    def derived(self, p: Params) -> dict:
        return {}

    def build(self, p: Params):
        """ONE valid solid in the local frame; no booleans with other elements."""
        raise NotImplementedError

    def validate(self, p: Params, context: ValidationContext) -> list[ValidationIssue]:
        return []

    def tags(self, instance_id: str) -> dict:
        return {"solid": f"gating.{instance_id}", "faces": {}}

    # ---- optional ----------------------------------------------------------
    def tap_frame(self, p: Params, s: float, side: str) -> Frame:
        """Path elements: the frame of a tap at station s in [0, 1] on a side."""
        raise NotImplementedError(f"{self.id} is not a path element")

    def tap_section(self, p: Params, s: float, side: str) -> Section | None:
        return None

    def size_for_area(self, p: Params, area_mm2: float) -> dict:
        """Parameter changes that give the element this flow area (the ratio
        propagation calls it).  Default: an `area` parameter, if declared."""
        if any(x.name == "area" for x in self.all_params()):
            return {"area": area_mm2}
        raise NotImplementedError(f"{self.id} cannot be sized for an area")

    def adopt_section(self, p: Params, port_id: str, section: Section) -> Params:
        """Port with policy FOLLOWS: return params that give it this section."""
        raise NotImplementedError(f"{self.id} cannot adopt a section at '{port_id}'")

    # ---- helpers the framework uses ----------------------------------------
    def port(self, p: Params, port_id: str, station: float | None = None, side: str | None = None) -> Port:
        if station is not None:
            if not self.is_path:
                raise ValueError(f"{self.id} has no taps (not a path element)")
            if side not in SIDES:
                raise ValueError(f"tap side must be one of {SIDES}")
            if side == "side":
                side = "left"                    # unresolved: the engine picks the side facing the branch
            return Port(port_id, PortKind.TAP, self.tap_frame(p, station, side), self.tap_section(p, station, side))
        for port in self.ports(p):
            if port.id == port_id:
                return port
        raise KeyError(f"{self.id} has no port '{port_id}'")


def register_element(cls: type[Element]) -> type[Element]:
    from core.registry import REGISTRY
    REGISTRY.add(cls)
    return cls
