"""Picking a face of the part: a point in the mold frame -> the nearest face of
the source part (its index, kind, area, normal there) and a triangle mesh of
it for highlighting."""
from __future__ import annotations

import numpy as np
from build123d import Vector, Vertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from core.frames import apply_point
from core.part import PartModel


def _mold_frame_matrix(pm: PartModel):
    return pm.to_mold, np.linalg.inv(pm.to_mold)


def face_mesh(face, to_mold: np.ndarray, tol: float = 0.2) -> list[list[float]]:
    """Flat list of triangle vertices (x, y, z per vertex) in the mold frame."""
    verts, tris = face.tessellate(tol, 0.3)
    pts = np.array([[v.X, v.Y, v.Z] for v in verts])
    pts = (to_mold[:3, :3] @ pts.T).T + to_mold[:3, 3]
    return pts[np.asarray(tris).reshape(-1)].reshape(-1).round(4).tolist()


def pick_face(pm: PartModel, point_mold) -> dict:
    to_mold, to_src = _mold_frame_matrix(pm)
    p_src = apply_point(to_src, point_mold)
    vtx = Vertex(*p_src)
    best = None
    for i, f in enumerate(pm.source.faces()):
        d = BRepExtrema_DistShapeShape(vtx.wrapped, f.wrapped)
        if d.IsDone() and (best is None or d.Value() < best[1]):
            best = (i, d.Value(), d.PointOnShape2(1))
    i, dist, q = best
    f = pm.source.faces()[i]
    n_src = f.normal_at(Vector(q.X(), q.Y(), q.Z()))
    r = to_mold[:3, :3]
    n = r @ np.array([n_src.X, n_src.Y, n_src.Z])
    n /= np.linalg.norm(n)
    return {"face": i, "kind": f.geom_type.name.lower(), "area_mm2": f.area,
            "point": apply_point(to_mold, (q.X(), q.Y(), q.Z())).round(6).tolist(),
            "normal": n.round(9).tolist(), "distance_mm": dist * float(np.cbrt(np.linalg.det(r))),
            "mesh": face_mesh(f, to_mold)}


def gate_port(pick: dict, port_id: str) -> dict:
    """A part port at a picked point: flow into the part (against the face's
    outward normal), up as close to +Z as the face allows (else +X)."""
    n = -np.asarray(pick["normal"], float)
    up = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.99 else np.array([1.0, 0.0, 0.0])
    return {"id": port_id, "face": pick["face"],
            "frame": {"origin": pick["point"], "normal": n.round(9).tolist(), "up": up.tolist()}}
