"""Where a feeder can stand, in the part's frame (cavity 0).

Top places: flat patches of the part's upper surface (the highest metal of each
column, same height, connected), on which a feeder stands straight up; the
cope is above the parting plane, so patches at z >= 0 come first.
Side places: points on the part's outline in the parting plane, where the
CAD module's side feeder stands on the plane beside the part and a neck runs to
it in the cope or the drag.

Both are ranked by distance from the hot spot's core.  Thinning out side points
(``side_spacing`` voxels apart) and the edge smoothing are numerical settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

from seeds.voxel import VoxelPart

FOUR = ndi.generate_binary_structure(2, 1)


@dataclass
class TopPatch:
    id: str
    k_top: int                    # index of the metal layer the patch is the top of
    z: float                      # height of the face, mm
    cells: np.ndarray             # (nx, ny) bool
    inradius: np.ndarray          # (nx, ny) mm: the largest circle centred on the cell that stays on the patch
    clear: np.ndarray             # (nx, ny) mm: from the cell centre to the nearest part wall rising above the patch

    @property
    def max_radius(self) -> float:
        return float(self.inradius.max())


@dataclass
class Candidate:
    style: str                    # top | side
    point: tuple[float, float, float]     # top: on the face; side: on the part's edge in the parting plane
    distance: float               # from the hot spot core, mm
    cope: bool
    patch: str | None = None
    normal: tuple[float, float] | None = None   # side: outward, in the plane
    room: dict = field(default_factory=dict)    # side: metal height above / depth below the plane at the edge

    def to_dict(self) -> dict:
        return {"style": self.style, "point": list(self.point), "distance": self.distance, "cope": self.cope,
                "patch": self.patch, "normal": list(self.normal) if self.normal else None, "room": self.room}


class Places:
    """Top patches and side points of one part, computed once."""

    def __init__(self, vp: VoxelPart, side_spacing: int = 3, smooth: float = 1.5):
        self.vp = vp
        h = vp.h
        ct = vp.column_top()
        self.column_top = ct
        self.top_z = np.where(ct >= 0, vp.origin[2] + (ct + 1) * h, -np.inf)
        self.patches: dict[str, TopPatch] = {}
        for k in sorted(set(ct[ct >= 0].tolist())):
            lab, n = ndi.label(ct == k, structure=FOUR)
            for i in range(1, n + 1):
                cells = lab == i
                inr = (ndi.distance_transform_edt(np.pad(cells, 1))[1:-1, 1:-1] - 0.5) * h
                pid = f"top{len(self.patches)}"
                higher = ct > k
                clear = (ndi.distance_transform_edt(~higher) - 0.5) * h if higher.any() else np.full(cells.shape, np.inf)
                self.patches[pid] = TopPatch(pid, int(k), float(vp.origin[2] + (k + 1) * h), cells,
                                             np.where(cells, inr, 0.0), np.where(cells, clear, 0.0))
        cope, drag = vp.parting_section()
        self.section = cope | drag
        self.cope_cells, self.drag_cells = cope, drag
        sec = self.section
        edge = sec & ~ndi.binary_erosion(sec, structure=FOUR, border_value=0)
        blur = ndi.gaussian_filter(sec.astype(float), smooth)
        gx, gy = np.gradient(blur)
        above, below = vp.height_above(), vp.depth_below()
        self.side_points = []
        for i, j in np.argwhere(edge):
            g = np.array([gx[i, j], gy[i, j]])
            if np.linalg.norm(g) < 1e-9:
                continue
            nrm = -g / np.linalg.norm(g)
            c = vp.xy(i, j) + nrm * h / 2                   # on the edge, not the cell centre
            self.side_points.append((c, nrm, float(above[i, j]), float(below[i, j]), (int(i), int(j))))
        self.side_spacing = side_spacing * h
        self._cache: dict[tuple, list[Candidate]] = {}

    def candidates(self, centre, limit: float | None = None) -> list[Candidate]:
        """Every top patch (its nearest cell) and the thinned side points, nearest first, the cope first.
        limit: keep only those within this distance, unless none are."""
        key = tuple(np.round(np.asarray(centre, float), 6))
        if key not in self._cache:
            self._cache[key] = self._candidates(np.asarray(centre, float))
        out = self._cache[key]
        if limit is not None:
            near = [q for q in out if q.distance <= limit]
            if near:
                return near
        return list(out)

    def _candidates(self, c: np.ndarray) -> list[Candidate]:
        out = []
        for p in self.patches.values():
            idx = np.argwhere(p.cells)
            xy = self.vp.origin[:2] + (idx + 0.5) * self.vp.h
            dd = np.sqrt(((xy - c[:2]) ** 2).sum(1) + (p.z - c[2]) ** 2)
            m = int(np.argmin(dd))
            out.append(Candidate("top", (float(xy[m, 0]), float(xy[m, 1]), p.z), float(dd[m]), p.z >= 0, p.id))
        sides = sorted(self.side_points, key=lambda s: float(np.hypot(*(s[0] - c[:2]))))
        kept: list = []
        for s in sides:
            if not kept or np.hypot(*(np.array([k[0] for k in kept]) - s[0]).T).min() >= self.side_spacing:
                kept.append(s)
        for xy, nrm, up, down, _ in kept:
            dist = float(np.sqrt(np.hypot(*(xy - c[:2])) ** 2 + c[2] ** 2))
            out.append(Candidate("side", (float(xy[0]), float(xy[1]), 0.0), dist, True, None,
                                 (float(nrm[0]), float(nrm[1])), {"cope_mm": up, "drag_mm": down}))
        out.sort(key=lambda q: (not q.cope, q.distance))
        return out

    def place_top(self, patch: str, radius: float, target, toward_target: bool = False,
                  wall_gap: float = 0.0) -> tuple[float, float] | None:
        """Centre of a feeder of this radius on the patch, at least wall_gap from any wall of the part
        rising above the patch, as near the target (x, y) as it stands wholly on the patch.  Wider than
        the patch: on its most central cell, or (toward_target, a feeder shared by two hot spots) on the
        cell nearest the target, so it reaches across toward the other.  None: no cell is clear."""
        p = self.patches[patch]
        room = p.cells & (p.clear >= radius + wall_gap - 1e-9)
        if not room.any():
            return None
        ok = room & (p.inradius >= radius - 1e-9)
        if not ok.any():
            ok = room if toward_target else room & (p.inradius >= p.inradius[room].max() - 1e-9)
        idx = np.argwhere(ok)
        xy = self.vp.origin[:2] + (idx + 0.5) * self.vp.h
        m = int(np.argmin(((xy - np.asarray(target[:2])) ** 2).sum(1)))
        return float(xy[m, 0]), float(xy[m, 1])
