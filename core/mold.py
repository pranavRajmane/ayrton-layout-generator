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


def outside_flask(box, solids: dict[str, Shape], tolerance: dict[str, float] | None = None) -> list[ValidationIssue]:
    lo, hi = box
    tolerance = tolerance or {}
    out = []
    for inst, s in solids.items():
        bb = s.bounding_box()
        below = np.asarray(lo) - np.asarray(tuple(bb.min))
        above = np.asarray(tuple(bb.max)) - np.asarray(hi)
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


def parts_overlap(parts: dict[str, Shape], names: dict[str, str] | None = None) -> list[ValidationIssue]:
    """Parts in one box must not share metal: every pair whose boxes touch is intersected."""
    from core.occ import boxes_touch, common_volume
    names = names or {}
    keys, out = list(parts), []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if not boxes_touch(parts[a], parts[b]):
                continue
            try:
                v = common_volume(parts[a], parts[b])
            except RuntimeError:
                continue
            if v > 1.0:
                out.append(ValidationIssue("mold.parts_overlap", Severity.ERROR,
                                           f"{names.get(a, a)} and {names.get(b, b)} overlap by {v / 1000:.1f} cm3",
                                           value=round(v, 1), limit=1.0, instances=("part",)))
    return out
