"""A part's surface as triangles, made once per part file (at pattern size), for the questions the
set-up asks on every edit: where the part's top is under a point, where it crosses a height, how far a
point is from it, its bounding box.

On a large part (thousands of faces) the exact B-rep answers took seconds to tens of seconds each,
because OCCT searches every face (a vertical ray: 16 s, a section: 1.7 s, an exact bounding box: 15 s).
Here the triangles only find the candidates: the answer is then taken on the exact faces they came
from (the ray on those faces, the section through those faces, the box of the faces at its sides), so
it is as exact as before.

A part is placed in the mold by a rigid matrix; its exact faces stay where they are (in the frame they
were meshed in) and each query is moved into that frame instead.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from build123d import Shape

# the same as the display mesh (relative: a tenth of each edge's size, 0.3 rad); an absolute 0.1 mm
# does not finish on a large part
DEFLECTION, ANGULAR = 0.1, 0.3
MARGIN = 1.0                     # mm: triangles this close to a query are candidates too (mesh vs exact)


class PartMesh:
    """Triangles of a shape, each knowing the exact face it came from."""

    def __init__(self, shape: Shape):
        from OCP.BRep import BRep_Tool
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS

        BRepMesh_IncrementalMesh(shape.wrapped, DEFLECTION, True, ANGULAR, True)
        self.faces = []
        vs, ts, fo, n = [], [], [], 0
        ex = TopExp_Explorer(shape.wrapped, TopAbs_FACE)
        while ex.More():
            f = TopoDS.Face(ex.Current())
            ex.Next()
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(f, loc)
            k = len(self.faces)
            self.faces.append(f)
            if tri is None:                     # a face OCCT could not mesh: only the exact queries see it
                continue
            tr = loc.Transformation()
            v = np.array([(p.X(), p.Y(), p.Z()) for p in
                          (tri.Node(i).Transformed(tr) for i in range(1, tri.NbNodes() + 1))], float)
            t = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)], int) - 1 + n
            if f.Orientation() == TopAbs_REVERSED:
                t = t[:, ::-1]
            vs.append(v)
            ts.append(t)
            fo.append(np.full(len(t), k))
            n += len(v)
        if not vs:
            raise ValueError("the part could not be meshed")
        self.v = np.vstack(vs)
        self.t = np.vstack(ts)
        self.face_of = np.concatenate(fo)
        self._rays: dict[int, object] = {}      # face index -> its exact line intersector
        self._boxes: dict[bytes, tuple] = {}     # rotation -> exact box of the turned part, before moving

    def turned_box(self, r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The exact bounding box of the part turned by r (about its own origin). The box's sides are
        found on the few faces whose triangles come near the triangles' box (the extremes lie on them:
        the mesh is within its deflection of the surface), so it costs a fraction of an exact box of
        the whole part; kept per turn, as moving the part only shifts it."""
        key = np.round(r, 9).tobytes()
        if key in self._boxes:
            return self._boxes[key]
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Trsf

        v = self.v @ np.asarray(r).T
        tri = v[self.t]
        lo_t, hi_t = tri.min(1), tri.max(1)
        lo, hi = v.min(0), v.max(0)
        margin = 0.01 * float(np.linalg.norm(hi - lo)) + MARGIN
        near = np.any((lo_t <= lo + margin) | (hi_t >= hi - margin), axis=1)
        trsf = gp_Trsf()
        m = np.eye(4)
        m[:3, :3] = r
        trsf.SetValues(*m[0, :4], *m[1, :4], *m[2, :4])
        box = Bnd_Box()
        faces = set(int(k) for k in np.unique(self.face_of[near]))
        faces |= set(range(len(self.faces))) - set(int(k) for k in np.unique(self.face_of))   # unmeshed: always
        for k in faces:
            f = BRepBuilderAPI_Transform(self.faces[k], trsf, True).Shape()
            BRepBndLib.AddOptimal_s(f, box, False, False)
        a, b = box.CornerMin(), box.CornerMax()
        x0, y0, z0, x1, y1, z1 = a.X(), a.Y(), a.Z(), b.X(), b.Y(), b.Z()
        out = (np.minimum(lo, (x0, y0, z0)), np.maximum(hi, (x1, y1, z1)))
        self._boxes[key] = out
        return out

    def ray_tool(self, k: int):
        from OCP.IntCurvesFace import IntCurvesFace_Intersector
        tool = self._rays.get(k)
        if tool is None:
            tool = self._rays[k] = IntCurvesFace_Intersector(self.faces[k], 1e-6)
        return tool


@dataclass(eq=False)
class Placed:
    """A part mesh where the mold has it: m takes the mesh's frame to the mold frame (rigid)."""
    mesh: PartMesh
    m: np.ndarray
    v: np.ndarray = field(init=False)
    lo: np.ndarray = field(init=False)           # per triangle, mold frame
    hi: np.ndarray = field(init=False)

    def __post_init__(self):
        r, t = self.m[:3, :3], self.m[:3, 3]
        self.v = self.mesh.v @ r.T + t
        tri = self.v[self.mesh.t]                 # (M, 3 corners, 3)
        self.lo, self.hi = tri.min(1), tri.max(1)

    def bbox(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """The exact bounding box (see PartMesh.turned_box)."""
        lo, hi = self.mesh.turned_box(self.m[:3, :3])
        t = self.m[:3, 3]
        return tuple(map(float, lo + t)), tuple(map(float, hi + t))

    def to_local(self, p) -> np.ndarray:
        r, t = self.m[:3, :3], self.m[:3, 3]
        return r.T @ (np.asarray(p, float) - t)

    def dir_local(self, d) -> np.ndarray:
        return self.m[:3, :3].T @ np.asarray(d, float)


_PLACED: OrderedDict[tuple, Placed] = OrderedDict()


def placed(mesh: PartMesh, m: np.ndarray) -> Placed:
    """The mesh moved by m, kept for the last few placements (a drag asks the same one many times)."""
    m = np.asarray(m, float)
    key = (id(mesh), np.round(m, 9).tobytes())
    p = _PLACED.get(key)
    if p is None or p.mesh is not mesh:
        p = _PLACED[key] = Placed(mesh, m)
        while len(_PLACED) > 16:
            _PLACED.popitem(last=False)
    else:
        _PLACED.move_to_end(key)
    return p


def bbox(parts: list[Placed]):
    boxes = [p.bbox() for p in parts]
    return (tuple(map(float, np.min([b[0] for b in boxes], axis=0))),
            tuple(map(float, np.max([b[1] for b in boxes], axis=0))))


def top_at(parts: list[Placed], x: float, y: float) -> float | None:
    """The highest point of the parts on the vertical line through (x, y), mold frame; None: nothing there."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    best = None
    for p in parts:
        near = ((p.lo[:, 0] <= x + MARGIN) & (p.hi[:, 0] >= x - MARGIN) &
                (p.lo[:, 1] <= y + MARGIN) & (p.hi[:, 1] >= y - MARGIN))
        if not near.any():
            continue
        o = p.to_local((x, y, 0.0))
        d = p.dir_local((0.0, 0.0, 1.0))
        line = gp_Lin(gp_Pnt(*map(float, o)), gp_Dir(*map(float, d)))
        for k in np.unique(p.mesh.face_of[near]):
            tool = p.mesh.ray_tool(int(k))
            tool.Perform(line, -1e9, 1e9)
            if not tool.IsDone():
                continue
            for i in range(1, tool.NbPnt() + 1):
                z = float(tool.WParameter(i))      # along the line: its height above z = 0 in the mold
                if best is None or z > best:
                    best = z
    return best


def seat(parts: list[Placed], x: float, y: float, d: float, rings: tuple[int, ...] = (16, 8)) -> dict | None:
    """As core.mold.seat (same rays, same answer), on the parts' exact faces under each ray only."""
    centre = top_at(parts, x, y)
    if centre is None:
        return None
    zs, misses, n = [centre], 0, 1
    r = d / 2 * 0.999
    for k, count in enumerate(rings):
        rr = r / (2 ** k)
        for a in np.linspace(0, 2 * np.pi, count, endpoint=False):
            z = top_at(parts, x + rr * np.cos(a), y + rr * np.sin(a))
            n += 1
            if z is None:
                misses += 1
            else:
                zs.append(z)
    return {"top": float(max(zs)), "drop": float(max(zs) - min(zs)), "misses": misses, "rays": n}


_SECTIONS: OrderedDict[tuple, list[np.ndarray]] = OrderedDict()


def section(parts: list[Placed], z: float, step_mm: float = 2.0) -> list[list[tuple[float, float]]]:
    """As core.mold.section_outline: where the parts cross the plane at height z, polylines (x, y) in mm,
    one per edge; cut through only the exact faces whose triangles reach that height. Each cut is kept
    in the part's own frame, so a part slid sideways (the usual drag) is not cut again."""
    out = []
    for p in parts:
        n = p.dir_local((0.0, 0.0, 1.0))
        o = p.to_local((0.0, 0.0, z))
        key = (id(p.mesh), tuple(np.round(n, 9)), round(float(n @ o), 6), step_mm)
        local = _SECTIONS.get(key)
        if local is None:
            local = _SECTIONS[key] = _cut(p, z, o, n, step_mm)
            while len(_SECTIONS) > 512:
                _SECTIONS.popitem(last=False)
        else:
            _SECTIONS.move_to_end(key)
        r, t = p.m[:3, :3], p.m[:3, 3]
        for pts in local:
            xy = (pts @ r.T + t)[:, :2]
            out.append([(round(float(a), 3), round(float(c), 3)) for a, c in xy])
    return out


def _cut(p: Placed, z: float, o, n, step_mm: float) -> list[np.ndarray]:
    """The section's edges as points in the mesh's frame (each edge sampled every step_mm)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopoDS import TopoDS_Compound

    near = (p.lo[:, 2] <= z + MARGIN) & (p.hi[:, 2] >= z - MARGIN)
    if not near.any():
        return []
    comp, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(comp)
    for k in np.unique(p.mesh.face_of[near]):
        b.Add(comp, p.mesh.faces[int(k)])
    sec = BRepAlgoAPI_Section(comp, gp_Pln(gp_Pnt(*map(float, o)), gp_Dir(*map(float, n))), False)
    sec.SetRunParallel(True)
    sec.Build()
    if not sec.IsDone():
        return []
    out = []
    for e in Shape.cast(sec.Shape()).edges():
        k = max(2, int(e.length / step_mm) + 1) if e.geom_type != "LINE" else 2
        out.append(np.array([tuple(q) for q in (e.position_at(s) for s in np.linspace(0, 1, k))], float))
    return out


def distance(p: Placed, point, within: float) -> float | None:
    """How far a point (mold frame) is from the part's surface, when closer than within (by the
    triangles: within the mesh deflection of the exact distance); None: farther."""
    q = np.asarray(point, float)
    near = np.all((p.lo <= q + within) & (p.hi >= q - within), axis=1)
    if not near.any():
        return None
    tri = p.v[p.mesh.t[near]]
    d = float(np.sqrt(_closest_sq(q, tri[:, 0], tri[:, 1], tri[:, 2]).min()))
    return d if d <= within else None


def nearest(parts: dict[str, Placed], point, within: float) -> str | None:
    """As core.mold.nearest_part: which part (by id) a point lies on or next to, closer than within."""
    best, best_d = None, within
    for pid, p in parts.items():
        d = distance(p, point, best_d)
        if d is not None and d <= best_d:
            best, best_d = pid, d
    return best


def _closest_sq(p, a, b, c) -> np.ndarray:
    """Squared distance from point p to each triangle (a, b, c): the closest-point regions of Ericson,
    Real-Time Collision Detection 5.1.5, over arrays of triangles."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = va + vb + vc
        v = np.where(denom != 0, vb / denom, 0.0)
        w = np.where(denom != 0, vc / denom, 0.0)
        out = a + ab * v[:, None] + ac * w[:, None]                       # inside the face
        # edge regions
        t_ab = np.where(d1 - d3 != 0, d1 / (d1 - d3), 0.0)
        t_ac = np.where(d2 - d6 != 0, d2 / (d2 - d6), 0.0)
        t_bc = np.where((d4 - d3) + (d5 - d6) != 0, (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0)
    on_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    on_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    on_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    out = np.where(on_bc[:, None], b + (c - b) * t_bc[:, None], out)
    out = np.where(on_ac[:, None], a + ac * t_ac[:, None], out)
    out = np.where(on_ab[:, None], a + ab * t_ab[:, None], out)
    # vertex regions
    out = np.where(((d6 >= 0) & (d5 <= d6))[:, None], c, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[:, None], b, out)
    out = np.where(((d1 <= 0) & (d2 <= 0))[:, None], a, out)
    return ((out - p) ** 2).sum(1)
