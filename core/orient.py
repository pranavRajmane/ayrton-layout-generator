"""Pouring-position tools.  Each returns a new part transform (part frame ->
mold frame) for the document; the API applies it as a JSON Patch, so every
orientation change is undoable like any other edit.

Rotations turn about the part's current bounding-box centre so the part stays
where it is on screen; the parting plane is Z = 0 of the mold frame, so setting
it is a vertical shift.
"""
from __future__ import annotations

import numpy as np

from core.frames import rigid

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def to_matrix(t: dict | None) -> np.ndarray:
    t = t or {}
    return rigid(t.get("rotation"), t.get("translation", (0.0, 0.0, 0.0)))


def to_dict(m: np.ndarray) -> dict:
    r = np.where(np.abs(m[:3, :3]) < 1e-12, 0.0, m[:3, :3])
    return {"rotation": r.tolist(), "translation": [float(x) for x in m[:3, 3]]}


def rotation(axis, angle_deg: float) -> np.ndarray:
    """Rodrigues rotation matrix about a unit axis."""
    k = np.asarray(AXES.get(axis, axis) if isinstance(axis, str) else axis, float)
    k = k / np.linalg.norm(k)
    a = np.radians(angle_deg)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(a) * kx + (1 - np.cos(a)) * kx @ kx


def aligning(v, target) -> np.ndarray:
    """The smallest rotation taking direction v onto direction target."""
    v = np.asarray(v, float) / np.linalg.norm(v)
    t = np.asarray(target, float) / np.linalg.norm(target)
    c = float(v @ t)
    axis = np.cross(v, t)
    if np.linalg.norm(axis) < 1e-12:
        if c > 0:
            return np.eye(3)
        axis = np.cross(v, (1, 0, 0) if abs(v[0]) < 0.9 else (0, 1, 0))     # half turn about any square axis
    return rotation(axis, np.degrees(np.arccos(np.clip(c, -1, 1))))


def _about(m: np.ndarray, r: np.ndarray, centre) -> np.ndarray:
    """m followed by the rotation r about a point of the mold frame."""
    c = np.asarray(centre, float)
    return rigid(r, c - r @ c) @ m


def rotate(t: dict | None, axis, angle_deg: float, centre) -> dict:
    return to_dict(_about(to_matrix(t), rotation(axis, angle_deg), centre))


def face_down(t: dict | None, normal_mold, centre) -> dict:
    """Turn the part so a face whose outward normal is normal_mold looks straight down."""
    return to_dict(_about(to_matrix(t), aligning(normal_mold, (0, 0, -1)), centre))


def up_vector(t: dict | None, v_part, centre) -> dict:
    """Turn the part so its part-frame direction v_part points up in the mold."""
    m = to_matrix(t)
    v_mold = m[:3, :3] @ np.asarray(v_part, float)
    return to_dict(_about(m, aligning(v_mold, (0, 0, 1)), centre))


def parting_at(t: dict | None, z_mold: float) -> dict:
    """Shift the part so the horizontal plane now at z_mold becomes the parting plane Z = 0."""
    m = to_matrix(t)
    return to_dict(rigid(translation=(0, 0, -z_mold)) @ m)
