"""Rules on the shape of the flow graph: no metal can leak out of an open
outlet, every path ends at the part or a blind end, and there is one pouring
inlet."""
from core.element import PortKind, Severity
from core.rules import Rule, register_rule


@register_rule
class DanglingOutlet(Rule):
    id = "system.dangling_outlet"
    severity = Severity.ERROR

    def check(self, design, context):
        used = {(c.frm.instance, c.frm.port) for c in design.graph.connections}
        closed = {c.to.instance for c in design.graph.connections if c.joint == "blind"}   # dead ends by design
        return [self.issue(f"{i}.{p.id} is an open outlet: metal would run out of the gating there", (i,))
                for i, b in design.built.items() if i not in closed for p in b.ports()
                if p.kind in (PortKind.OUTLET, PortKind.PART_FACE) and (i, p.id) not in used]


@register_rule
class PathEnds(Rule):
    id = "system.path_end"
    severity = Severity.ERROR

    def check(self, design, context):
        out = []
        for path in design.graph.paths():
            last = path[-1]
            if not last.to_part and last.joint != "blind":
                out.append(self.issue(f"the path {' -> '.join([path[0].frm.instance] + [c.to.instance for c in path])} "
                                      "reaches neither the part nor a blind end",
                                      [c.to.instance for c in path[-1:]], connection=last.id))
        return out


@register_rule
class OnePouringInlet(Rule):
    id = "system.pouring_inlet"
    severity = Severity.WARNING

    def check(self, design, context):
        pours = design.pouring_ports()
        if len(pours) == 1 or not design.built:
            return []
        return [self.issue(f"{len(pours)} open inlets ({', '.join(f'{i}.{p.id}' for i, p in pours)}); the design "
                           "should have one pouring inlet", [i for i, _ in pours], value=len(pours), limit=1)]
