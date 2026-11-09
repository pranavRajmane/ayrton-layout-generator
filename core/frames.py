"""Coordinate frames.

Three frames are in play: the part frame (as the STEP file comes in), the mold
frame (Z up in pouring position, parting plane at Z = 0; every element and the
placed part live here) and each element's local frame (where its build() works).

A ``Frame`` is a port frame: an origin, a flow normal (the direction metal moves)
and an up vector, so a section cannot twist about the normal.  Its rotation has
columns (normal, side, up) with side = up x normal, a right-handed set.
Transforms between frames are 4x4 homogeneous matrices (numpy).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TOL = 1e-9


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n < TOL:
        raise ValueError("zero-length direction")
    return v / n


def rigid(rotation=None, translation=(0.0, 0.0, 0.0)) -> np.ndarray:
    """4x4 matrix from a 3x3 rotation and a translation."""
    m = np.eye(4)
    if rotation is not None:
        m[:3, :3] = np.asarray(rotation, float)
    m[:3, 3] = np.asarray(translation, float)
    return m


def invert(m: np.ndarray) -> np.ndarray:
    """Inverse of a rigid transform."""
    r = m[:3, :3]
    out = np.eye(4)
    out[:3, :3] = r.T
    out[:3, 3] = -r.T @ m[:3, 3]
    return out


def is_rigid(m: np.ndarray, tol: float = 1e-6) -> bool:
    m = np.asarray(m, float)
    r = m[:3, :3]
    return (m.shape == (4, 4) and np.allclose(m[3], [0, 0, 0, 1], atol=tol)
            and np.allclose(r.T @ r, np.eye(3), atol=tol) and abs(np.linalg.det(r) - 1.0) < tol)


def apply_point(m: np.ndarray, p) -> np.ndarray:
    return m[:3, :3] @ np.asarray(p, float) + m[:3, 3]


def apply_dir(m: np.ndarray, d) -> np.ndarray:
    return m[:3, :3] @ np.asarray(d, float)


@dataclass(frozen=True)
class Frame:
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    up: tuple[float, float, float]

    def __post_init__(self):
        n = _unit(self.normal)
        u = np.asarray(self.up, float)
        u = u - (u @ n) * n                      # keep the up vector square to the flow
        if np.linalg.norm(u) < 1e-6:
            raise ValueError("up vector is parallel to the flow normal")
        object.__setattr__(self, "origin", tuple(float(x) for x in self.origin))
        object.__setattr__(self, "normal", tuple(float(x) for x in n))
        object.__setattr__(self, "up", tuple(float(x) for x in _unit(u)))

    @property
    def side(self) -> tuple[float, float, float]:
        return tuple(float(x) for x in np.cross(self.up, self.normal))

    def matrix(self) -> np.ndarray:
        """Frame coordinates (along normal, side, up) -> parent coordinates."""
        return rigid(np.column_stack([self.normal, self.side, self.up]), self.origin)

    @classmethod
    def from_matrix(cls, m: np.ndarray) -> "Frame":
        m = np.asarray(m, float)
        return cls(tuple(m[:3, 3]), tuple(m[:3, 0]), tuple(m[:3, 2]))

    def transformed(self, m: np.ndarray) -> "Frame":
        """This frame carried by the rigid transform m."""
        return Frame(tuple(apply_point(m, self.origin)), tuple(apply_dir(m, self.normal)),
                     tuple(apply_dir(m, self.up)))

    def reversed(self) -> "Frame":
        """Same plane and up vector, flow the other way."""
        return Frame(self.origin, tuple(-x for x in self.normal), self.up)

    def offset(self, distance: float) -> "Frame":
        """Moved along its own normal."""
        return Frame(tuple(np.asarray(self.origin) + distance * np.asarray(self.normal)), self.normal, self.up)

    def gap(self, other: "Frame") -> tuple[float, float, float]:
        """(distance mm, angle between normals deg, twist between up vectors deg)."""
        d = float(np.linalg.norm(np.subtract(self.origin, other.origin)))
        a = float(np.degrees(np.arccos(np.clip(np.dot(self.normal, other.normal), -1, 1))))
        t = float(np.degrees(np.arccos(np.clip(np.dot(self.up, other.up), -1, 1))))
        return d, a, t

    def to_dict(self) -> dict:
        return {"origin": list(self.origin), "normal": list(self.normal), "up": list(self.up)}

    @classmethod
    def from_dict(cls, d: dict) -> "Frame":
        return cls(tuple(d["origin"]), tuple(d["normal"]), tuple(d["up"]))


def placement_onto(local_port: Frame, target: Frame) -> np.ndarray:
    """Element local -> mold transform that lays local_port exactly on target."""
    return target.matrix() @ invert(local_port.matrix())
