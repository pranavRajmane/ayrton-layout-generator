"""The gating flow graph: element instances are nodes, connection records join
an outlet (or tap) of one element to an inlet of the next, or to the part.

The same graph drives placement, joining, the calculators, the rules and the
mapping of solver results back to elements.  It must be a DAG: a ring runner is
one element with many taps, not a cycle of elements.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.document import PART

JOINTS = ("butt", "transition", "branch", "part", "blind")
DEFAULT_OVERLAP_MM = 1.0


class GraphError(ValueError):
    def __init__(self, message: str, instances: tuple[str, ...] = ()):
        super().__init__(message)
        self.instances = instances


@dataclass(frozen=True)
class End:
    instance: str
    port: str
    station: float | None = None
    side: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "End":
        return cls(d["instance"], d["port"], d.get("station"), d.get("side"))

    @property
    def key(self) -> tuple:
        return (self.instance, self.port, self.station, self.side)

    @property
    def label(self) -> str:
        return f"{self.instance}.{self.port}"


@dataclass(frozen=True)
class Connection:
    id: str
    frm: End
    to: End
    joint: str
    overlap_mm: float = DEFAULT_OVERLAP_MM
    transition: dict | None = None
    measure_plane: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Connection":
        ov = d.get("overlap_mm")
        return cls(d["id"], End.from_dict(d["from"]), End.from_dict(d["to"]), d["joint"],
                   DEFAULT_OVERLAP_MM if ov is None else float(ov), d.get("transition"),
                   d.get("measure_plane", True))

    @property
    def to_part(self) -> bool:
        return self.to.instance == PART


class FlowGraph:
    def __init__(self, elements: list[dict], connections: list[dict]):
        self.instances = [e["id"] for e in elements]
        self.connections = [Connection.from_dict(c) for c in connections]
        self._check_ports_used_once()

    def _check_ports_used_once(self):
        seen: dict[tuple, str] = {}
        for c in self.connections:
            for end, role in ((c.frm, "from"), (c.to, "to")):
                if end.instance == PART:
                    continue                      # many ingates may land on one part face
                k = (role,) + end.key
                fanned = ("drop", "tee")             # a sprue drops into runners, a runner feeds ingates
                if k in seen and not (role == "from" and c.joint in fanned and seen[k][1] == c.joint):
                    raise GraphError(f"port {end.label} is used by connections {seen[k][0]} and {c.id}",
                                     (end.instance,))
                seen[k] = (c.id, c.joint)

    def incoming(self, inst: str) -> list[Connection]:
        return [c for c in self.connections if c.to.instance == inst]

    def outgoing(self, inst: str) -> list[Connection]:
        return [c for c in self.connections if c.frm.instance == inst]

    def sources(self) -> list[str]:
        return [i for i in self.instances if not self.incoming(i)]

    def order(self) -> list[str]:
        """Topological order (Kahn), stable in document order; GraphError on a cycle."""
        indeg = {i: 0 for i in self.instances}
        for c in self.connections:
            if not c.to_part:
                indeg[c.to.instance] += 1
        ready = [i for i in self.instances if indeg[i] == 0]
        out = []
        while ready:
            i = ready.pop(0)
            out.append(i)
            for c in self.outgoing(i):
                if c.to_part:
                    continue
                indeg[c.to.instance] -= 1
                if indeg[c.to.instance] == 0:
                    ready.append(c.to.instance)
        if len(out) != len(self.instances):
            loop = tuple(i for i in self.instances if i not in out)
            raise GraphError(f"the flow graph has a loop through {', '.join(loop)}; "
                             f"model a ring runner as one element with taps", loop)
        return out

    def paths(self) -> list[list[Connection]]:
        """Every path of connections from a source element to a sink (the part or a dead end)."""
        out = []

        def walk(inst, acc):
            nxt = self.outgoing(inst)
            if not nxt:
                out.append(acc)
            for c in nxt:
                if c.to_part:
                    out.append(acc + [c])
                else:
                    walk(c.to.instance, acc + [c])

        for s in self.sources():
            walk(s, [])
        return [p for p in out if p]
