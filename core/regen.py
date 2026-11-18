"""Regeneration: from the design document to placed, tagged solids.

Walks the flow graph in passes over its topological order.  For each element
instance: resolve its parameters, adopt the upstream section where a port
FOLLOWS, place it (core.placement; a path element instead has its path solved
between its constrained ends), build its solid in the local frame and move it
into the mold frame.  Every failure is an issue on that instance; the rest of
the design still regenerates.

Then the joints (core.joints): taps without a station are put opposite the
branch they feed, transition lofts are built, gaps, sections, contacts and port
constraints are checked, and the overlap at each joint is given to the
downstream solid (the upstream solid is cut by the downstream one, by the part
at a part joint), so the named solids the solver gets tile the metal without
double counting.  Last, collisions (core.collide) and the flask.  The pattern
is one fuse of the untrimmed solids, made only when asked for.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from build123d import Shape

from calculators import engine as calc_engine
from calculators.presets import Preset, load_preset, preset_id
from core import collide, joints, mold, placement, rules
from core.document import PART, DesignDocument
from core.element import (
    Element, Params, Port, PortKind, SectionPolicy, Severity, ValidationContext, ValidationIssue,
)
from core.graph import Connection, End, FlowGraph, GraphError
from core.occ import fuse, placed
from core.part import PartModel, load_part
from core.registry import REGISTRY, Registry


@dataclass
class BuiltElement:
    instance: str
    type: str
    element: Element
    params: Params
    transform: np.ndarray
    solid: Shape                        # placed, untrimmed (for the pattern)
    tagged: Shape                       # placed, overlaps given away downstream (for the solver)
    derived: dict
    tags: dict
    build_ms: float

    def port(self, end_or_id: End | str) -> Port:
        if isinstance(end_or_id, str):
            return self.element.port(self.params, end_or_id).transformed(self.transform)
        e = end_or_id
        return self.element.port(self.params, e.port, e.station, e.side).transformed(self.transform)

    def ports(self) -> list[Port]:
        return [x.transformed(self.transform) for x in self.element.ports(self.params)]


@dataclass
class BuiltTransition:
    """The loft the framework inserts at a transition joint."""
    connection: str
    name: str                           # gating.<connection id>
    solid: Shape
    tagged: Shape


@dataclass
class RegenResult:
    doc: DesignDocument
    graph: FlowGraph | None
    part: PartModel | None
    preset: Preset | None = None
    flask: tuple | None = None                       # (min corner, max corner) in the mold frame
    built: dict[str, BuiltElement] = field(default_factory=dict)
    transitions: dict[str, BuiltTransition] = field(default_factory=dict)
    ends: dict[str, End] = field(default_factory=dict)            # connection id -> resolved upstream end
    calc: "calc_engine.CalcResult | None" = None
    issues: list[ValidationIssue] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    _fused: Shape | None = None

    @property
    def ok(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)

    def issues_for(self, inst: str) -> list[ValidationIssue]:
        return [i for i in self.issues if inst in i.instances]

    def frm(self, c: Connection) -> End:
        """The connection's upstream end, with a solved tap station and side."""
        return self.ends.get(c.id, c.frm)

    def gating_solids(self, tagged: bool = False) -> dict[str, Shape]:
        out = {b.tags["solid"]: (b.tagged if tagged else b.solid) for b in self.built.values()}
        out.update({t.name: (t.tagged if tagged else t.solid) for t in self.transitions.values()})
        return out

    def named_solids(self) -> dict[str, Shape]:
        """Region name -> solid, for the solver: part, core.<id>, gating.<id>, mold.flask."""
        out = {}
        if self.part:
            out["part"] = self.part.shape
            out.update({f"part.{k}": v for k, v in self.part.cavities.items()})
            out.update({f"core.{k}": v for k, v in self.part.cores.items()})
        out.update(self.gating_solids(tagged=True))
        if self.flask is not None:
            out["mold.flask"] = mold.flask_solid(self.flask)
        return out

    def fused(self) -> Shape:
        """All the metal as one solid (pattern export, cast.stl)."""
        if self._fused is None:
            t = time.perf_counter()
            gating = list(self.gating_solids().values())
            g = fuse(gating) if gating else None             # the gating first, then onto the part
            parts = [self.part.shape, *self.part.cavities.values()] if self.part else []
            self._fused = fuse([x for x in parts + [g] if x is not None])
            self.timings_ms["fuse"] = 1e3 * (time.perf_counter() - t)
        return self._fused

    def pouring_ports(self) -> list[tuple[str, Port]]:
        """Inlet ports nothing feeds: where the metal is poured."""
        fed = {(c.to.instance, c.to.port) for c in (self.graph.connections if self.graph else [])}
        return [(i, x) for i, b in self.built.items() for x in b.ports()
                if x.kind == PortKind.INLET and (i, x.id) not in fed]

    def measurement_planes(self) -> list[dict]:
        """One plane per connection (named after the port metal leaves by), the far
        end of each transition, and each pouring inlet."""
        out = []
        for inst, x in self.pouring_ports():
            out.append(_plane(f"{inst}.{x.id}", x, None))
        for c in (self.graph.connections if self.graph else []):
            e = self.frm(c)
            if not c.measure_plane or e.instance not in self.built:
                continue
            name = e.label if e.station is None else f"{e.label}@{e.station:.3f}"
            try:
                out.append(_plane(name, self.built[e.instance].port(e), c.id))
            except Exception:                                # noqa: BLE001 - an unsolved tap has no plane yet
                continue
            if c.joint == "transition" and c.to.instance in self.built:
                out.append(_plane(c.to.label, self.built[c.to.instance].port(c.to.port), c.id))
        return out


def _plane(name: str, port: Port, connection: str | None) -> dict:
    return {"name": name, "connection": connection, **port.frame.to_dict(),
            "section": port.section.to_dict() if port.section else None}


def regenerate(doc: DesignDocument, registry: Registry = REGISTRY) -> RegenResult:
    t0 = time.perf_counter()
    res = RegenResult(doc, None, None)
    issues = res.issues
    data = doc.data
    try:
        res.preset = load_preset(preset_id(data["process"]))
    except FileNotFoundError as e:
        issues.append(ValidationIssue("preset.not_found", Severity.ERROR, str(e)))
    try:
        res.part = load_part(doc, res.preset.get("shrinkage_pct") if res.preset else None)
        if res.part:
            issues += res.part.issues
    except Exception as e:                                   # noqa: BLE001 - reported, not raised
        issues.append(ValidationIssue("part.load_failed", Severity.ERROR, str(e), instances=(PART,)))
    res.timings_ms["part"] = 1e3 * (time.perf_counter() - t0)

    try:
        graph = res.graph = FlowGraph(data["elements"], data["connections"])
        res.order = graph.order()
    except GraphError as e:
        issues.append(ValidationIssue("graph.invalid", Severity.ERROR, str(e), instances=e.instances))
        return res

    defs = {e["id"]: e for e in data["elements"]}
    part_ports = res.part.ports if res.part else {}
    proc = data["process"]
    fixed = {i for i in res.order if defs[i].get("anchor") or any(c.to_part for c in graph.outgoing(i))}

    def world_port(end: End) -> Port | None:
        b = res.built.get(end.instance)
        if b is None:
            return None
        if b.element.is_path and end.port.startswith("tap"):
            c = next((c for c in graph.outgoing(end.instance) if c.frm == end), None)
            e = joints.resolve_tap(res, c) if c else end
            return None if e is None else b.port(e)
        return b.port(end)

    pending = list(res.order)
    failed: set[str] = set()

    def attempt(inst: str, last_chance: bool) -> bool:
        """Try to build one instance; False to wait for a neighbour."""
        t = time.perf_counter()
        d = defs[inst]
        try:
            cls = registry.get(d["type"])
            el = cls()
            p = el.resolve_params(d["params"])
        except (KeyError, ValueError) as e:
            issues.append(ValidationIssue("element.params", Severity.ERROR, str(e).strip("'\""), instances=(inst,)))
            failed.add(inst)
            return True
        incoming, outgoing = graph.incoming(inst), graph.outgoing(inst)
        upstream_sections = {}
        try:
            for c in incoming:
                follows = not el.is_path and el.port(p, c.to.port).section_policy == SectionPolicy.FOLLOWS
                up = world_port(c.frm)
                if up is None:
                    if (follows or el.is_path) and c.frm.instance not in failed and not last_chance:
                        return False                     # wait: the section to follow / the path start is not built
                    continue
                if up.section is None:
                    continue
                upstream_sections[c.to.port] = up.section
                if follows:
                    p = el.adopt_section(p, c.to.port, up.section)
            if any(c.to_part and c.joint != "gate" and c.to.port not in part_ports for c in outgoing):
                raise KeyError("part port missing (is the part loaded?)")
            if el.is_path:
                p, wait = _solve_path(el, p, incoming, outgoing, world_port, res, fixed, failed, last_chance)
                if wait:
                    return False
                m = np.eye(4)
            else:
                m, place_issues = placement.place(inst, el, p, d.get("anchor"), incoming, outgoing, world_port,
                                                  part_ports)
                if m is None:
                    return False
                issues.extend(place_issues)
            local = el.build(p)
            if len(local.solids()) != 1 or not local.is_valid:
                raise RuntimeError(f"build() gave {len(local.solids())} solid(s), valid={local.is_valid}")
            solid = local if el.is_path else placed(local, m)
        except Exception as e:                               # noqa: BLE001 - one element must not stop the rest
            issues.append(ValidationIssue("element.build_failed", Severity.ERROR,
                                          f"{type(e).__name__}: {e}", instances=(inst,)))
            failed.add(inst)
            return True
        if d.get("type_version") and d["type_version"] != cls.version:
            issues.append(ValidationIssue("element.version", Severity.WARNING,
                                          f"saved with {d['type']} {d['type_version']}, built with {cls.version}",
                                          instances=(inst,)))
        ctx = ValidationContext(inst, proc.get("process"), proc.get("alloy"), upstream_sections)
        for i in el.validate(p, ctx):
            issues.append(i if i.instances else ValidationIssue(**{**i.__dict__, "instances": (inst,)}))
        res.built[inst] = BuiltElement(inst, d["type"], el, p, m, solid, solid, el.derived(p), el.tags(inst),
                                       1e3 * (time.perf_counter() - t))
        return True

    progress, last_chance = True, False
    while pending:
        progress = False
        for inst in list(pending):
            if attempt(inst, last_chance):
                pending.remove(inst)
                progress = True
        if not progress:
            if last_chance:
                break
            last_chance = True                               # let path elements go with what they have
        else:
            last_chance = False
    for inst in pending:
        nbrs = {c.frm.instance for c in graph.incoming(inst)} | {
            c.to.instance for c in graph.outgoing(inst) if not c.to_part}
        if nbrs & failed:
            issues.append(ValidationIssue("placement.neighbour_failed", Severity.ERROR,
                                          "not placed: the element it hangs off failed", instances=(inst,)))
        else:
            issues.append(ValidationIssue("placement.unanchored", Severity.ERROR,
                                          "not placed: nothing fixes it in the mold (no anchor, no part "
                                          "contact, no placed neighbour; a tap feeding it needs a station)",
                                          instances=(inst,)))
        failed.add(inst)

    joints.check_and_join(res)
    t = time.perf_counter()
    issues.extend(collide.check(res))
    res.timings_ms["collisions"] = 1e3 * (time.perf_counter() - t)
    t = time.perf_counter()
    res.calc = calc_engine.compute(res)
    issues.extend(res.calc.issues)
    issues.extend(rules.run(res, res.calc))
    res.timings_ms["calculators_and_rules"] = 1e3 * (time.perf_counter() - t)
    res.flask = mold.flask_box(data["mold"].get("flask"), res.part.bbox if res.part else None)
    if res.flask is not None:
        solids = {**{i: b.solid for i, b in res.built.items()}, **{k: t.solid for k, t in res.transitions.items()}}
        if res.part is not None:
            solids.update({"part": res.part.shape, **{f"part.{k}": v for k, v in res.part.cavities.items()}})
        # an element may poke out by its embed (the pouring stub over the cope top, trimmed on export)
        tol = {i: b.params.embed for i, b in res.built.items()}
        boxes = {n: res.part.bbox_of(n) for n in res.part.placed} if res.part is not None else {}
        issues.extend(mold.outside_flask(res.flask, solids, tol, boxes))
    if res.part is not None and res.part.cavities:
        names = {"part": res.part.path.name, **{f"part.{c['id']}": c.get("name") or f"{res.part.path.name} ({c['id']})"
                                                for c in (data["part"] or {}).get("cavities", [])}}
        issues.extend(mold.parts_overlap({"part": res.part.shape, **{f"part.{k}": v for k, v in res.part.cavities.items()}},
                                         names, {n: res.part.bbox_of(n) for n in res.part.placed}, res.part.key))
    res.timings_ms["total"] = 1e3 * (time.perf_counter() - t0)
    return res


def _solve_path(el, p, incoming, outgoing, world_port, res, fixed, failed, last_chance):
    """[upstream port origin] + waypoints + [fixed downstream inlet origin] -> p.path.
    Returns (params, wait)."""
    pts = [tuple(q) for q in p.waypoints]
    feed = next((c for c in incoming if c.joint != "tee"), None)      # a tee'd path starts where it is drawn
    if feed is not None:
        up = world_port(feed.frm)
        if up is None and feed.frm.instance not in failed and not last_chance:
            return p, True
        if up is not None:
            pts = [el.drop_point(p, up.frame.origin) if feed.joint == "drop" else up.frame.origin] + pts
    out = next((c for c in outgoing if c.frm.port == "outlet" and not c.to_part), None)
    if out is not None:
        down = res.built.get(out.to.instance)
        if down is None and out.to.instance in fixed and out.to.instance not in failed and not last_chance:
            return p, True                                   # its end is fixed elsewhere: wait for it
        if down is not None:
            pts = pts + [down.port(out.to.port).frame.origin]
    return p.replace(path=tuple(pts)), False
