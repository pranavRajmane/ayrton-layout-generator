"""Thin helpers over build123d / OCP that the rest of the core shares."""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
from build123d import Compound, Location, Shape
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Trsf
from OCP.ShapeFix import ShapeFix_Shape
from OCP.collections import List_TopoDS_Shape


def to_location(m: np.ndarray) -> Location:
    t = gp_Trsf()
    m = np.asarray(m, float)
    t.SetValues(*m[0, :4], *m[1, :4], *m[2, :4])
    return Location(t)


def placed(shape: Shape, m: np.ndarray) -> Shape:
    return shape.moved(to_location(m))


def heal(shape: Shape) -> tuple[Shape, bool]:
    """ShapeFix on a shape; (result, whether anything changed)."""
    if BRepCheck_Analyzer(shape.wrapped).IsValid():
        return shape, False
    fix = ShapeFix_Shape(shape.wrapped)
    fix.Perform()
    return Shape.cast(fix.Shape()), True


def _args(shapes) -> List_TopoDS_Shape:
    lst = List_TopoDS_Shape()
    for s in shapes:
        lst.Append(s.wrapped)
    return lst


def _run(op, a: Shape, tools: list[Shape]) -> Shape:
    op.SetArguments(_args([a]))
    op.SetTools(_args(tools))
    op.SetRunParallel(True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("boolean failed")
    return Shape.cast(op.Shape())


def fuse(shapes: list[Shape]) -> Shape:
    """One boolean over all the shapes; if OCCT returns nothing sensible, fuse
    them one at a time instead."""
    if len(shapes) == 1:
        return shapes[0]
    biggest = max(solids_volume(x) for x in shapes)
    out = _run(BRepAlgoAPI_Fuse(), shapes[0], shapes[1:])
    if not out.solids() or solids_volume(out) < biggest * (1 - 1e-9):
        out = shapes[0]
        for x in shapes[1:]:
            out = _run(BRepAlgoAPI_Fuse(), out, [x])
        if not out.solids():
            raise RuntimeError("fuse failed")
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    u = ShapeUpgrade_UnifySameDomain(out.wrapped, True, True, False)
    u.Build()
    return Shape.cast(u.Shape())


def cut(a: Shape, tools: list[Shape]) -> Shape:
    return _run(BRepAlgoAPI_Cut(), a, tools)


def solids_volume(shape: Shape) -> float:
    return float(sum(s.volume for s in shape.solids()))


def compound(shapes: list[Shape], label: str = "") -> Compound:
    return Compound(children=shapes, label=label)


def section_face(section, frame):
    """The section as a planar face lying in the port plane."""
    from build123d import Face, Wire
    o, sd, up = (np.asarray(v, float) for v in (frame.origin, frame.side, frame.up))
    pts = [tuple(o + a * sd + b * up) for a, b in section.outline()]
    return Face(Wire.make_polygon(pts, close=True))


def baked(shape: Shape) -> Shape:
    """The same geometry with its placement copied into the coordinates and an
    identity location.  OCCT's glTF writer composes a shape's location on the
    wrong side of its Z-up to Y-up conversion, so located shapes come out
    misplaced; baked ones do not."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.TopLoc import TopLoc_Location
    w = shape.wrapped
    t = w.Location().Transformation()
    return Shape.cast(BRepBuilderAPI_Transform(w.Located(TopLoc_Location()), t, True).Shape())


def transformed(shape: Shape, m: np.ndarray, scale: float = 1.0) -> Shape:
    """A copy scaled uniformly about the origin, then moved by the rigid m (baked, not located)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Pnt
    w = shape.wrapped
    if scale != 1.0:
        t = gp_Trsf()
        t.SetScale(gp_Pnt(0, 0, 0), scale)
        w = BRepBuilderAPI_Transform(w, t, True).Shape()
    if not np.allclose(m, np.eye(4)):
        r = gp_Trsf()
        m = np.asarray(m, float)
        r.SetValues(*m[0, :4], *m[1, :4], *m[2, :4])
        w = BRepBuilderAPI_Transform(w, r, True).Shape()
    return Shape.cast(w)


def section_wire(section, frame):
    """The section's outline as a closed wire in the port plane (a true circle when round)."""
    from build123d import Edge, Plane, Wire
    if section.shape == "round":
        pl = Plane(origin=frame.origin, x_dir=frame.side, z_dir=frame.normal)
        return Wire([Edge.make_circle(section.d["d"] / 2, pl)])
    o, sd, up = (np.asarray(v, float) for v in (frame.origin, frame.side, frame.up))
    return Wire.make_polygon([tuple(o + a * sd + b * up) for a, b in section.outline()], close=True)


def slab(section, frame, depth: float, side: float = 1.0, gap: float = 0.01) -> Shape:
    """A thin plate of the section's shape just beside its port plane: side +1
    downstream of the plane, -1 upstream."""
    from build123d import Solid, Vector
    f = frame.offset(side * gap)
    return Solid.extrude(section_face(section, f), Vector(*(side * depth * np.asarray(f.normal))))


def common_volume(a: Shape, b: Shape) -> float:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    op = BRepAlgoAPI_Common(a.wrapped, b.wrapped)
    if not op.IsDone():
        raise RuntimeError("boolean common failed")
    return solids_volume(Shape.cast(op.Shape()))


def boxes_touch(a, b, tol: float = 1e-3) -> bool:
    """Whether two bounding boxes touch; a and b are shapes or ready boxes (lo, hi)."""
    amin, amax = _box(a)
    bmin, bmax = _box(b)
    return all(amin[i] <= bmax[i] + tol and bmin[i] <= amax[i] + tol for i in range(3))


def _box(x):
    if isinstance(x, Shape):
        b = x.bounding_box()
        return tuple(b.min), tuple(b.max)
    return tuple(x[0]), tuple(x[1])


_COMMON: "OrderedDict[tuple, float]" = OrderedDict()


def common_volume_cached(key, a: Shape, b: Shape) -> float:
    """common_volume, kept by key (what a and b are and where): rebuilding a design asks the same
    overlaps again for everything that did not change, and against a large part each takes ~0.7 s."""
    if key in _COMMON:
        _COMMON.move_to_end(key)
        return _COMMON[key]
    v = _COMMON[key] = common_volume(a, b)
    while len(_COMMON) > 4096:
        _COMMON.popitem(last=False)
    return v
