"""The flask: a box around the part, and the check that the gating stays inside it."""
from __future__ import annotations

import numpy as np
from build123d import Box, Location, Shape

from core.element import Severity, ValidationIssue


def flask_box(flask: dict | None, part_bbox) -> tuple[np.ndarray, np.ndarray] | None:
    """(min corner, max corner) in the mold frame, or None when no flask is set.
    margin_mm: the part's bounding box grown on every side; size_mm: an explicit
    box, centred on `centre` (default: the part's bounding-box centre)."""
    if not flask:
        return None
    if flask.get("size_mm"):
        c = np.asarray(flask.get("centre") or (np.add(*map(np.asarray, part_bbox)) / 2 if part_bbox else (0, 0, 0)), float)
        h = np.asarray(flask["size_mm"], float) / 2
        return c - h, c + h
    if flask.get("margin_mm") is not None and part_bbox:
        m = float(flask["margin_mm"])
        return np.asarray(part_bbox[0]) - m, np.asarray(part_bbox[1]) + m
    return None


def flask_solid(box) -> Shape:
    lo, hi = box
    size = hi - lo
    return Box(*size).moved(Location(tuple((lo + hi) / 2)))


def outside_flask(box, solids: dict[str, Shape], tolerance: dict[str, float] | None = None,
                  boxes: dict | None = None) -> list[ValidationIssue]:
    """boxes: ready bounding boxes (lo, hi) by name, used instead of the solid's (a large part's)."""
    lo, hi = box
    tolerance = tolerance or {}
    boxes = boxes or {}
    out = []
    for inst, s in solids.items():
        if inst in boxes:
            bmin, bmax = boxes[inst]
        else:
            bb = s.bounding_box()
            bmin, bmax = tuple(bb.min), tuple(bb.max)
        below = np.asarray(lo) - np.asarray(bmin)
        above = np.asarray(bmax) - np.asarray(hi)
        over = float(max(below.max(), above.max()))
        if over > tolerance.get(inst, 0.0) + 1e-6:
            out.append(ValidationIssue("mold.outside_flask", Severity.WARNING,
                                       f"{inst} reaches {over:.1f} mm outside the molding box", value=round(over, 3),
                                       limit=0.0, instances=("part",) if inst == "part" or inst.startswith("part.") else (inst,)))
    return out


def section_outline(shape: Shape, z: float = 0.0, step_mm: float = 2.0) -> list[list[tuple[float, float]]]:
    """Where a shape crosses the horizontal plane at height z: polylines (x, y) in mm, one per edge."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    sec = BRepAlgoAPI_Section(shape.wrapped, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), False)
    sec.Build()
    if not sec.IsDone():
        return []
    out = []
    for e in Shape.cast(sec.Shape()).edges():
        n = max(2, int(e.length / step_mm) + 1) if e.geom_type != "LINE" else 2
        out.append([(round(p.X, 3), round(p.Y, 3)) for p in (e.position_at(t) for t in np.linspace(0, 1, n))])
    return out


def parts_overlap(parts: dict[str, Shape], names: dict[str, str] | None = None, boxes: dict | None = None,
                  key: str | None = None) -> list[ValidationIssue]:
    """Parts in one box must not share metal: every pair whose boxes touch is intersected.
    boxes: ready bounding boxes by name; key: where the parts are (the results are kept by it)."""
    from core.occ import boxes_touch, common_volume, common_volume_cached
    names = names or {}
    boxes = boxes or {}
    keys, out = list(parts), []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if not boxes_touch(boxes.get(a, parts[a]), boxes.get(b, parts[b])):
                continue
            try:
                v = (common_volume_cached(("parts", key, a, b), parts[a], parts[b]) if key
                     else common_volume(parts[a], parts[b]))
            except RuntimeError:
                continue
            if v > 1.0:
                out.append(ValidationIssue("mold.parts_overlap", Severity.ERROR,
                                           f"{names.get(a, a)} and {names.get(b, b)} overlap by {v / 1000:.1f} cm3",
                                           value=round(v, 1), limit=1.0, instances=("part",)))
    return out


def seat(shape: Shape, x: float, y: float, d: float, rings: tuple[int, ...] = (16, 8)) -> dict | None:
    """Where something round (diameter d) standing straight up at (x, y) sits on the top of shape.

    Vertical rays at the centre and around rings inside its edge (radius d/2 and d/4) find the top of
    the shape under each: top is the highest of those (its base goes there, so its typed height stands
    clear of the part), drop how far the top falls below that within it (on a curved or sloped face it
    must reach that much further down to meet the part everywhere), misses how many rays found nothing
    (it hangs over the part's edge).  None: nothing under the centre."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    hit = IntCurvesFace_ShapeIntersector()
    hit.Load(shape.wrapped, 1e-6)
    z0 = shape.bounding_box().max.Z + 10.0

    def top(px: float, py: float) -> float | None:
        hit.Perform(gp_Lin(gp_Pnt(px, py, z0), gp_Dir(0, 0, -1)), 0.0, 1e9)
        zs = [hit.Pnt(i).Z() for i in range(1, hit.NbPnt() + 1)]
        return max(zs) if zs else None

    centre = top(x, y)
    if centre is None:
        return None
    zs, misses, n = [centre], 0, 1
    r = d / 2 * 0.999                       # just inside the edge
    for k, count in enumerate(rings):
        rr = r / (2 ** k)
        for a in np.linspace(0, 2 * np.pi, count, endpoint=False):
            z = top(x + rr * np.cos(a), y + rr * np.sin(a))
            n += 1
            if z is None:
                misses += 1
            else:
                zs.append(z)
    return {"top": float(max(zs)), "drop": float(max(zs) - min(zs)), "misses": misses, "rays": n}


def nearest_part(parts: dict[str, Shape], point, within: float) -> str | None:
    """Which of the parts (by id) a point lies on or next to: the nearest one closer than within (mm)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, point))).Vertex()
    best, best_d = None, within
    for pid, s in parts.items():
        bb = s.bounding_box()
        if any(point[i] < tuple(bb.min)[i] - within or point[i] > tuple(bb.max)[i] + within for i in range(3)):
            continue
        dist = BRepExtrema_DistShapeShape(v, s.wrapped)
        if dist.IsDone() and dist.Value() <= best_d:
            best, best_d = pid, dist.Value()
    return best
