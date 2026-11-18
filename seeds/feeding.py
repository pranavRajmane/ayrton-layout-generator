"""A geometric feeding graph of the part: zones, their moduli, which touch.

No simulation: the part is split into zones by a watershed on the distance to
its surface (each zone grows from one thick core), and every zone gets the
modulus V/A of its own volume over the part surface it owns (faces between
zones do not cool).  Freezing order follows the modulus (Chvorinov: freeze time
grows with M squared), so ``freeze_index`` = M**2 ranks the zones; it is not a
time.

Two zones merge when the ridge between them is as high as the higher of their
two cores, less ``merge_tol`` voxels (one section split by voxel noise), and a
zone smaller than ``min_zone_voxels`` merges into the neighbour it shares most
faces with.  Both are numerical settings of the segmentation, not casting
rules.

A hot spot is a zone whose modulus is at least that of every neighbour: it
freezes after all of them, so no hotter zone can feed it.  Every other zone
feeds, through its hottest neighbour, the hot spot its climb ends at; a tie
splits its volume equally.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

from seeds.voxel import VoxelPart

TIE = 0.02          # moduli this close count as equal (voxel staircase noise)
AXES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


@dataclass
class Zone:
    id: str
    label: int
    volume_mm3: float
    cooling_area_mm2: float
    modulus_mm: float
    thickness_mm: float          # twice the depth of its core under the surface
    centre: tuple[float, float, float]   # the middle of its core, mold frame
    n_voxels: int

    @property
    def freeze_index(self) -> float:
        return self.modulus_mm ** 2

    def to_dict(self) -> dict:
        return {"id": self.id, "volume_mm3": self.volume_mm3, "cooling_area_mm2": self.cooling_area_mm2,
                "modulus_mm": self.modulus_mm, "thickness_mm": self.thickness_mm,
                "centre": list(self.centre), "freeze_index": self.freeze_index}


@dataclass
class Link:
    a: str
    b: str
    contact_area_mm2: float
    saddle_mm: float             # depth under the surface of the deepest point on their shared boundary


@dataclass
class FeedingGraph:
    part: VoxelPart
    labels: np.ndarray           # zone label per voxel, 0 outside the part
    zones: dict[str, Zone]
    links: list[Link]
    by_label: dict[int, str] = field(default_factory=dict)

    def neighbours(self, zid: str) -> list[str]:
        return sorted({l.b if l.a == zid else l.a for l in self.links if zid in (l.a, l.b)})

    def hot_spots(self, rtol: float = TIE) -> list[str]:
        out = []
        for zid, z in self.zones.items():
            if all(z.modulus_mm >= self.zones[n].modulus_mm * (1 - rtol) for n in self.neighbours(zid)):
                out.append(zid)
        return sorted(out)

    def basins(self, rtol: float = TIE) -> dict[str, dict[str, float]]:
        """{hot spot: {zone: share of that zone's volume it must feed}}."""
        hot = set(self.hot_spots(rtol))
        share: dict[str, dict[str, float]] = {}
        for zid in sorted(self.zones, key=lambda z: -self.zones[z].modulus_mm):
            if zid in hot:
                share[zid] = {zid: 1.0}
                continue
            m = self.zones[zid].modulus_mm
            up = [n for n in self.neighbours(zid) if self.zones[n].modulus_mm > m * (1 + rtol)]
            top = max(self.zones[n].modulus_mm for n in up)
            best = [n for n in up if self.zones[n].modulus_mm >= top * (1 - rtol)]
            acc: dict[str, float] = {}
            for n in best:
                for h, f in share[n].items():
                    acc[h] = acc.get(h, 0.0) + f / len(best)
            share[zid] = acc
        out: dict[str, dict[str, float]] = {h: {} for h in hot}
        for zid, s in share.items():
            for h, f in s.items():
                out[h][zid] = out[h].get(zid, 0.0) + f
        return out

    def fed_volume(self, hot: str) -> float:
        """Volume (mm3) whose shrinkage the hot spot draws on: its basin, by share."""
        return sum(self.zones[z].volume_mm3 * f for z, f in self.basins()[hot].items())

    def to_dict(self) -> dict:
        return {"part_id": self.part.part_id, "zones": [z.to_dict() for z in self.zones.values()],
                "links": [l.__dict__ for l in self.links], "hot_spots": self.hot_spots()}


def _pairs(lab: np.ndarray, d: np.ndarray):
    """Face-adjacent voxel pairs with different non-zero labels: (la, lb, min depth) arrays."""
    out = []
    for ax in range(3):
        a = [slice(None)] * 3
        b = [slice(None)] * 3
        a[ax], b[ax] = slice(0, -1), slice(1, None)
        la, lb = lab[tuple(a)], lab[tuple(b)]
        m = (la != lb) & (la > 0) & (lb > 0)
        out.append((la[m], lb[m], np.minimum(d[tuple(a)][m], d[tuple(b)][m])))
    return tuple(np.concatenate(x) for x in zip(*out))


SIX = ndi.generate_binary_structure(3, 1)


def _flood(lab: np.ndarray, d: np.ndarray, metal: np.ndarray) -> np.ndarray:
    """Watershed from the cores down: level by level of depth, each label grows into the unlabelled
    metal at or above that level next to it (a tie goes to the higher label)."""
    lab = lab.copy()
    for lv in np.unique(d[metal])[::-1]:
        mask = metal & (d >= lv - 1e-9)
        while True:
            todo = mask & (lab == 0)
            if not todo.any():
                break
            grown = ndi.grey_dilation(lab, footprint=SIX)
            new = todo & (grown > 0)
            if not new.any():                       # a core no marker reached: give it its own label
                extra, k = ndi.label(todo, structure=SIX)
                lab[todo] = extra[todo] + lab.max()
                break
            lab[new] = grown[new]
    return lab


def build_feeding_graph(vp: VoxelPart, merge_tol: float = 1.0, min_zone_voxels: int = 8) -> FeedingGraph:
    metal = vp.metal
    if not metal.any():
        raise ValueError("the part has no metal voxels")
    d = ndi.distance_transform_edt(metal)                 # voxels, to the nearest empty voxel centre
    peaks = metal & (d >= ndi.maximum_filter(d, size=3, mode="constant") - 1e-9)
    markers, n = ndi.label(peaks, structure=np.ones((3, 3, 3)))
    lab = _flood(markers.astype(np.int64), d, metal)
    n = int(lab.max())

    # merging (union-find over labels)
    parent = {i: i for i in range(1, n + 1)}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    peak = dict(zip(range(1, n + 1), ndi.maximum(d, lab, range(1, n + 1))))
    la, lb, s = _pairs(lab, d)
    changed = True
    while changed:
        changed = False
        ra = np.array([root(int(x)) for x in la]) if len(la) else la
        rb = np.array([root(int(x)) for x in lb]) if len(lb) else lb
        sad: dict[tuple[int, int], float] = {}
        for x, y, v in zip(ra, rb, s):
            if x == y:
                continue
            k = (min(x, y), max(x, y))
            if v > sad.get(k, -1.0):
                sad[k] = float(v)
        for (x, y), v in sorted(sad.items(), key=lambda kv: -kv[1]):
            x, y = root(x), root(y)
            if x == y:
                continue
            if v >= max(peak[x], peak[y]) - merge_tol:
                parent[y] = x
                peak[x] = max(peak[x], peak[y])
                changed = True
    lab = np.vectorize(lambda i: root(i) if i else 0, otypes=[np.int64])(lab) if n else lab

    # tiny zones into the neighbour they touch most
    while True:
        ids, counts = np.unique(lab[lab > 0], return_counts=True)
        small = [int(i) for i, c in zip(ids, counts) if c < min_zone_voxels]
        if not small or len(ids) == 1:
            break
        la, lb, _ = _pairs(lab, d)
        z = small[0]
        nb = np.concatenate([lb[la == z], la[lb == z]])
        if not len(nb):
            break
        vals, cnt = np.unique(nb, return_counts=True)
        lab[lab == z] = int(vals[np.argmax(cnt)])

    # relabel 1..m, measure
    ids = [int(i) for i in np.unique(lab[lab > 0])]
    remap = np.zeros(int(lab.max()) + 1, np.int64)
    for new, old in enumerate(ids, start=1):
        remap[old] = new
    lab = remap[lab]
    h = vp.h
    m = len(ids)
    rng = range(1, m + 1)
    vox = np.bincount(lab.ravel(), minlength=m + 1)
    area = np.zeros(m + 1)
    padded = np.pad(metal, 1)
    for ax in AXES:
        nb = np.roll(padded, shift=[-a for a in ax], axis=(0, 1, 2))[1:-1, 1:-1, 1:-1]
        face = metal & ~nb
        area += np.bincount(lab[face], minlength=m + 1)
    area *= h * h
    peak = np.asarray(ndi.maximum(d, lab, rng), float)
    zones, by_label = {}, {}
    for li in rng:
        core = (lab == li) & (d >= peak[li - 1] - 0.5)
        c = vp.centre(np.argwhere(core)).mean(axis=0)
        vol = float(vox[li]) * h ** 3
        a = float(area[li])
        zid = f"z{li}"
        zones[zid] = Zone(zid, li, vol, a, vol / a if a > 0 else (peak[li - 1] - 0.5) * h,
                          float(2 * peak[li - 1] * h), tuple(float(x) for x in c), int(vox[li]))
        by_label[li] = zid
    links: dict[tuple[int, int], list[float]] = {}
    la, lb, s = _pairs(lab, d)
    for x, y, v in zip(la, lb, s):
        k = (int(min(x, y)), int(max(x, y)))
        acc = links.setdefault(k, [0, 0.0])
        acc[0] += 1
        acc[1] = max(acc[1], float(v))
    out = [Link(by_label[a], by_label[b], c * h * h, (sv - 0.5) * h) for (a, b), (c, sv) in sorted(links.items())]
    return FeedingGraph(vp, lab, zones, out, by_label)
