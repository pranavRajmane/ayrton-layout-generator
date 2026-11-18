"""The part as voxels in the mold frame: what the seed generator reasons on.

The grid is aligned so the parting plane (z = 0 of the mold frame) falls on a
voxel boundary: layer ``k0`` is the first layer above the plane, ``k0 - 1`` the
last one below it.  A voxel is metal when its centre is inside the part.  One
empty voxel is kept on every side, so every metal voxel has a neighbour.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VoxelPart:
    metal: np.ndarray            # (nx, ny, nz) bool
    h: float                     # voxel edge, mm
    origin: np.ndarray           # mold-frame position of the corner of voxel (0, 0, 0), mm
    part_id: str = "part"

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.metal.shape

    @property
    def k0(self) -> int:
        """Index of the first voxel layer above the parting plane."""
        return int(round(-self.origin[2] / self.h))

    def centre(self, idx) -> np.ndarray:
        """Mold-frame centres of voxel indices (..., 3)."""
        return self.origin + (np.asarray(idx, float) + 0.5) * self.h

    def xy(self, i, j) -> np.ndarray:
        return self.origin[:2] + (np.asarray([i, j], float) + 0.5) * self.h

    def ij(self, x: float, y: float) -> tuple[int, int]:
        return (int(np.floor((x - self.origin[0]) / self.h)), int(np.floor((y - self.origin[1]) / self.h)))

    def z_bottom(self, k) -> float:
        return float(self.origin[2] + k * self.h)

    @property
    def volume_mm3(self) -> float:
        return float(self.metal.sum()) * self.h ** 3

    def bbox(self) -> tuple[np.ndarray, np.ndarray]:
        idx = np.argwhere(self.metal)
        return self.origin + idx.min(0) * self.h, self.origin + (idx.max(0) + 1) * self.h

    # ---- 2D views ----------------------------------------------------------
    def footprint(self) -> np.ndarray:
        """(nx, ny): columns holding any metal."""
        return self.metal.any(axis=2)

    def column_top(self) -> np.ndarray:
        """(nx, ny) index of the highest metal voxel in each column, -1 where none."""
        nz = self.shape[2]
        rev = self.metal[:, :, ::-1]
        has = rev.any(axis=2)
        return np.where(has, nz - 1 - rev.argmax(axis=2), -1)

    def parting_section(self) -> tuple[np.ndarray, np.ndarray]:
        """(cope, drag): the metal in the voxel layer just above and just below the parting plane."""
        k0 = self.k0
        nz = self.shape[2]
        cope = self.metal[:, :, k0] if 0 <= k0 < nz else np.zeros(self.shape[:2], bool)
        drag = self.metal[:, :, k0 - 1] if 0 <= k0 - 1 < nz else np.zeros(self.shape[:2], bool)
        return cope, drag

    def height_above(self) -> np.ndarray:
        """(nx, ny) mm of metal continuous upward from the parting plane in each column."""
        return self._run(self.metal[:, :, self.k0:]) * self.h

    def depth_below(self) -> np.ndarray:
        """(nx, ny) mm of metal continuous downward from the parting plane in each column."""
        return self._run(self.metal[:, :, :self.k0][:, :, ::-1]) * self.h

    @staticmethod
    def _run(a: np.ndarray) -> np.ndarray:
        if a.shape[2] == 0:
            return np.zeros(a.shape[:2])
        gap = ~a
        first_gap = np.where(gap.any(axis=2), gap.argmax(axis=2), a.shape[2])
        return first_gap.astype(float)

    # ---- building ----------------------------------------------------------
    @classmethod
    def _grid(cls, lo, hi, h: float):
        lo = np.floor(np.asarray(lo, float) / h) * h - h
        hi = np.ceil(np.asarray(hi, float) / h) * h + h
        n = np.round((hi - lo) / h).astype(int)
        return lo, n

    @classmethod
    def from_boxes(cls, boxes, h: float, part_id: str = "part") -> "VoxelPart":
        """Union of axis-aligned boxes (xmin, ymin, zmin, xmax, ymax, zmax), mm, mold frame."""
        b = np.asarray(boxes, float)
        origin, n = cls._grid(b[:, :3].min(0), b[:, 3:].max(0), h)
        c = [origin[a] + (np.arange(n[a]) + 0.5) * h for a in range(3)]
        X, Y, Z = np.meshgrid(*c, indexing="ij")
        metal = np.zeros(tuple(n), bool)
        for x0, y0, z0, x1, y1, z1 in b:
            metal |= (X >= x0) & (X < x1) & (Y >= y0) & (Y < y1) & (Z >= z0) & (Z < z1)
        return cls(metal, float(h), origin, part_id)

    @classmethod
    def from_shape(cls, shape, h: float, part_id: str = "part") -> "VoxelPart":
        """An OCCT shape (mold frame): vertical rays through every column centre, metal between
        each entry and exit."""
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        bb = shape.bounding_box()
        origin, n = cls._grid(tuple(bb.min), tuple(bb.max), h)
        zc = origin[2] + (np.arange(n[2]) + 0.5) * h
        z0 = origin[2] + n[2] * h + 10.0
        hit = IntCurvesFace_ShapeIntersector()
        hit.Load(shape.wrapped, 1e-6)
        metal = np.zeros(tuple(n), bool)
        for i in range(n[0]):
            x = origin[0] + (i + 0.5) * h
            for j in range(n[1]):
                y = origin[1] + (j + 0.5) * h
                hit.Perform(gp_Lin(gp_Pnt(x, y, z0), gp_Dir(0, 0, -1)), 0.0, 1e9)
                zs = sorted({round(hit.Pnt(m).Z(), 6) for m in range(1, hit.NbPnt() + 1)})
                for a, b in zip(zs[0::2], zs[1::2]):
                    metal[i, j] |= (zc > a) & (zc < b)
        return cls(metal, float(h), origin, part_id)
