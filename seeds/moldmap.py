"""A plan-view raster of the mold for one layout: where the cavities are, how
high their tops are, and distances from them.  Cell size = the part's voxel
size; the raster covers the flask."""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

from seeds.voxel import VoxelPart


class MoldMap:
    def __init__(self, vp: VoxelPart, offsets: list, lo, hi):
        h = vp.h
        self.h = h
        self.lo = np.asarray(lo, float)[:2]
        self.hi = np.asarray(hi, float)[:2]
        n = np.maximum(np.ceil((self.hi - self.lo) / h).astype(int), 1)
        self.footprint = np.zeros(tuple(n), bool)
        self.top = np.full(tuple(n), -np.inf)
        self.cavity = np.full(tuple(n), -1, int)
        self.spill = 0                                   # part cells that fell outside the flask
        fp = vp.footprint()
        ct = vp.column_top()
        idx = np.argwhere(fp)
        top_z = vp.origin[2] + (ct[fp] + 1) * h
        for k, off in enumerate(offsets):
            xy = vp.origin[:2] + (idx + 0.5) * h + np.asarray(off, float)[:2]
            ij = np.floor((xy - self.lo) / h).astype(int)
            ok = (ij >= 0).all(1) & (ij < n).all(1)
            self.spill += int((~ok).sum())
            i, j = ij[ok, 0], ij[ok, 1]
            self.footprint[i, j] = True
            self.top[i, j] = np.maximum(self.top[i, j], top_z[ok])
            self.cavity[i, j] = k
        # distance (mm) from a cell centre to the nearest cavity cell's edge; 0 on a cavity
        d = ndi.distance_transform_edt(~self.footprint) if self.footprint.any() else np.full(n, np.inf)
        self.dist = np.where(self.footprint, 0.0, np.maximum(d - 0.5, 0.0) * h)

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        i, j = np.floor((np.array([x, y]) - self.lo) / self.h).astype(int)
        if 0 <= i < self.footprint.shape[0] and 0 <= j < self.footprint.shape[1]:
            return int(i), int(j)
        return None

    def centres(self) -> np.ndarray:
        n = self.footprint.shape
        gx = self.lo[0] + (np.arange(n[0]) + 0.5) * self.h
        gy = self.lo[1] + (np.arange(n[1]) + 0.5) * self.h
        return np.stack(np.meshgrid(gx, gy, indexing="ij"), axis=-1)

    def gap(self, x: float, y: float) -> float:
        """Distance from a point to the nearest cavity, mm (0 on one; -inf outside the flask)."""
        c = self.cell(x, y)
        return float(self.dist[c]) if c else -np.inf

    def segment_clear(self, a, b, half_width: float, skip_end: float = 0.0) -> bool:
        """No cavity within half_width of the segment a -> b (its last skip_end mm not checked)."""
        a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
        L = float(np.linalg.norm(b - a))
        n = max(int(np.ceil(L / (self.h / 2))), 1)
        t = np.linspace(0, 1, n + 1)
        t = t[t * L <= L - skip_end + 1e-9] if skip_end > 0 else t
        if not len(t):
            return True
        p = a + t[:, None] * (b - a)
        ij = np.floor((p - self.lo) / self.h).astype(int)
        inside = (ij >= 0).all(1) & (ij < self.footprint.shape).all(1)
        if not inside.all():
            return False
        return bool((self.dist[ij[:, 0], ij[:, 1]] >= half_width).all())

    def walls_near(self, x: float, y: float, radius: float, z: float) -> bool:
        """Any cavity rising above z within radius of (x, y)."""
        c = self.centres()
        near = np.hypot(c[..., 0] - x, c[..., 1] - y) <= radius
        return bool((self.top[near] > z + self.h / 2).any())


def segment_distance(p1, p2, q1, q2) -> float:
    """Shortest distance between two segments in the plane."""
    p1, p2, q1, q2 = (np.asarray(v[:2], float) for v in (p1, p2, q1, q2))

    def pt_seg(p, a, b):
        ab = b - a
        t = 0.0 if not ab.any() else float(np.clip((p - a) @ ab / (ab @ ab), 0, 1))
        return float(np.linalg.norm(p - (a + t * ab)))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1, d2 = cross(q1, q2, p1), cross(q1, q2, p2)
    d3, d4 = cross(p1, p2, q1), cross(p1, p2, q2)
    if (d1 * d2 < 0) and (d3 * d4 < 0):
        return 0.0
    return min(pt_seg(p1, q1, q2), pt_seg(p2, q1, q2), pt_seg(q1, p1, p2), pt_seg(q2, p1, p2))
