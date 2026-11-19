"""The part seen from above: its outline on the parting plane, exact up to the mesh deflection.

``footprint(shape)`` is the whole part projected onto z = 0 (cavity-to-cavity and cavity-to-flask
clearances); ``footprint(shape, z_lo, z_hi)`` is only the metal between those heights (a runner in
the drag meets only the part below the parting plane).  Projected mesh triangles are unioned with
shapely, then grown by the mesh deflection, so a chord never lets the outline fall inside the part.

Cavities are the parted part scaled by the pattern allowance about the mold origin (the parting plane
stays at z = 0), then turned and moved as the layout's ``cavities`` say.
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon


def _triangles(shape, deflection: float, angular: float) -> np.ndarray:
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    wrapped = BRepBuilderAPI_Copy(shape.wrapped).Shape()
    BRepMesh_IncrementalMesh(wrapped, deflection, False, angular, True)
    out = []
    ex = TopExp_Explorer(wrapped, TopAbs_FACE)
    while ex.More():
        f = TopoDS.Face(ex.Current())
        ex.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        if tri is None:
            continue
        tr = loc.Transformation()
        v = np.array([[p.X(), p.Y()] for p in (tri.Node(i).Transformed(tr) for i in range(1, tri.NbNodes() + 1))])
        t = np.array([[tri.Triangle(i).Value(k) - 1 for k in (1, 2, 3)] for i in range(1, tri.NbTriangles() + 1)])
        if len(t):
            out.append(v[t])
    return np.concatenate(out) if out else np.zeros((0, 3, 2))


def footprint(shape, z_lo: float | None = None, z_hi: float | None = None, deflection: float = 0.05,
              angular: float = 0.2):
    """Outline (shapely Polygon or MultiPolygon, holes kept) of the part, or of its slab between
    z_lo and z_hi; None when the slab is empty."""
    if z_lo is not None or z_hi is not None:
        from build123d import Box, Compound, Pos

        bb = shape.bounding_box()
        lo = bb.min.Z - 1 if z_lo is None else z_lo
        hi = bb.max.Z + 1 if z_hi is None else z_hi
        if hi <= max(lo, bb.min.Z) or lo >= bb.max.Z:
            return None
        cx, cy = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2
        slab = Pos(cx, cy, (lo + hi) / 2) * Box(bb.size.X + 10, bb.size.Y + 10, hi - lo)
        cut = shape.intersect(slab)
        solids = list(cut.solids()) if cut is not None else []
        if not solids:
            return None
        shape = Compound(solids)
    tri = _triangles(shape, deflection, angular)
    a = 0.5 * np.abs((tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1])
                     - (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1]))
    tri = tri[a > 1e-9]                                     # upright faces project to nothing
    if not len(tri):
        return None
    polys = shapely.polygons(np.concatenate([tri, tri[:, :1]], axis=1))
    outline = shapely.union_all(polys, grid_size=1e-6)
    outline = outline.buffer(deflection, quad_segs=8)
    return shapely.make_valid(outline)


def place(outline, scale: float, rotation_deg: float, translation, mirrored: bool = False):
    """A cavity's outline: scaled about the mold origin, mirrored in x = 0, turned about the origin,
    moved (the layout's cavity transform)."""
    g = affinity.scale(outline, scale, scale, origin=(0, 0))
    if mirrored:
        g = affinity.scale(g, -1, 1, origin=(0, 0))
    g = affinity.rotate(g, rotation_deg, origin=(0, 0))
    return affinity.translate(g, float(translation[0]), float(translation[1]))


def place_point(p, scale: float, rotation_deg: float, translation, mirrored: bool = False) -> np.ndarray:
    """The same transform for a point (or a direction, with translation 0 and scale 1)."""
    q = np.asarray(p, float)[:2] * scale
    if mirrored:
        q = q * np.array([-1.0, 1.0])
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    return np.array([c * q[0] - s * q[1], s * q[0] + c * q[1]]) + np.asarray(translation, float)[:2]


def as_multi(g) -> MultiPolygon:
    if g is None or g.is_empty:
        return MultiPolygon()
    if isinstance(g, Polygon):
        return MultiPolygon([g])
    if isinstance(g, MultiPolygon):
        return g
    return MultiPolygon([p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)])
