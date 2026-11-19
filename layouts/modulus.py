"""Phase 1: the part's modulus field, feeding zones, hot spots and zone adjacency.

All in the mold frame (mm), the part as it sits for casting.

1. **Surface.**  The part is triangulated (relative deflection with an angle limit, as the editor's
   mesh: an absolute deflection takes minutes on a large part).  Each face's area is the exact B-rep
   area where OCCT gives a sane one, else the mesh's (OCCT returns nonsense on some faces).
2. **Voxels.**  A voxel is metal when its centre is inside: the triangles are rasterised along
   vertical columns and the crossings summed as a winding number.  The grid puts the parting plane
   (z = 0) on a voxel boundary.  The voxel size defaults to ``voxel_fraction_of_min_wall`` x the
   thinnest wall (inward rays from the surface), within ``max_voxels``.
3. **Distance.**  r = distance from each metal voxel centre to the real surface (nearest surface
   sample, refined on that sample's triangle plane), not the voxel staircase.  Local modulus field
   = r / 3: exact for a sphere or cube, the right *places* for hot spots everywhere, but r / 2 in a
   long bar and r in a plate, so it is a map, never a zone modulus.
4. **Local thickness.**  Every voxel gets the diameter of the largest inscribed sphere covering it.
   The spheres come from the surface samples (shrinking ball), not from voxel centres: a voxel-centred
   sphere is short by up to a voxel, worst at junctions, which made zone boundaries move with h.  A member of constant section is a flat region of this
   field; a junction is a flat region of larger value (the sphere in the corner of an L is 1.17 t,
   at a T 1.25 t, at an X 1.41 t); between them the field ramps.
5. **Zones.**  Flat regions (slope <= ``plateau_slope_max``) that hold a sphere of at least
   ``plateau_core_fraction`` of their own radius, and at least ``plateau_core_min_voxels`` voxels,
   are zone seeds; the ramps and rims are flooded to the nearest seed through the metal.  So members
   and junctions are separate zones (L 3, T 4, X 5, a stepped plate 2) and a cube, sphere, bar or
   plate is one.
6. **Zone modulus** = zone volume / the part surface it owns (faces between zones do not cool).
   Volumes are voxel counts scaled so they add up to the exact part volume; the surface is the
   B-rep area; each bit of surface goes to the zone holding the centre of the largest inscribed sphere
   touching it.
7. **Hot spots** are maxima of r that rise more than ``hot_spot_min_rise_voxels`` voxels above every
   path to higher ground (prominence), one per plateau.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

from seeds.voxel import VoxelPart

SIX = ndi.generate_binary_structure(3, 1)
OFFS26 = [np.array(o) for o in np.ndindex(3, 3, 3) if o != (1, 1, 1)]
OFFS26 = [o - 1 for o in OFFS26]
OFFS6 = [np.array(o) for o in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]


class ModulusError(ValueError):
    pass


# ---- surface ------------------------------------------------------------------------------
@dataclass
class Surface:
    v: np.ndarray            # (nv, 3)
    t: np.ndarray            # (nt, 3) outward counter-clockwise
    face_of: np.ndarray      # (nt,) B-rep face index (shape.faces() order)
    n_faces: int
    face_area: np.ndarray    # (n_faces,) area used for each face (exact or mesh)
    area_fallback: list[int] = field(default_factory=list)   # faces whose exact area was not usable

    @property
    def tri(self) -> np.ndarray:
        return self.v[self.t]

    def vertex_normals(self) -> np.ndarray:
        """Unit normal at each mesh vertex, the area-weighted mean of its triangles' normals.  OCCT
        meshes every B-rep face with its own vertices, so the mean stays within one face: smooth over a
        curved face, sharp at the part's edges.  Facet normals make a cylinder a prism whose inscribed
        spheres near each facet's edge are far too small."""
        if getattr(self, "_vn", None) is None:
            p = self.tri
            c = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])          # 2 x area x normal
            vn = np.zeros_like(self.v)
            for k in range(3):
                np.add.at(vn, self.t[:, k], c)
            self._vn = vn / np.maximum(np.linalg.norm(vn, axis=1), 1e-300)[:, None]
        return self._vn

    def tri_area_normal(self) -> tuple[np.ndarray, np.ndarray]:
        p = self.tri
        c = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        a = 0.5 * np.linalg.norm(c, axis=1)
        n = c / np.maximum(2 * a, 1e-300)[:, None]
        return a, n

    def mesh_volume(self) -> float:
        p = self.tri
        return float(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0)


def triangulate(shape, relative: float = 0.1, angular: float = 0.2) -> Surface:
    """Triangles of a copy of the shape (meshing a cached shape would change the editor's mesh)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    wrapped = BRepBuilderAPI_Copy(shape.wrapped).Shape()
    BRepMesh_IncrementalMesh(wrapped, relative, True, angular, True)
    vs, ts, fo, exact = [], [], [], []
    n = 0
    ex = TopExp_Explorer(wrapped, TopAbs_FACE)
    k = 0
    while ex.More():
        f = TopoDS.Face(ex.Current())
        ex.Next()
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props)
        exact.append(props.Mass())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        if tri is not None and tri.NbTriangles():
            tr = loc.Transformation()
            v = np.array([(p.X(), p.Y(), p.Z()) for p in
                          (tri.Node(i).Transformed(tr) for i in range(1, tri.NbNodes() + 1))], float)
            t = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)], int) - 1 + n
            if f.Orientation() == TopAbs_REVERSED:
                t = t[:, ::-1]
            vs.append(v)
            ts.append(t)
            fo.append(np.full(len(t), k))
            n += len(v)
        k += 1
    if not vs:
        raise ModulusError("the part could not be meshed")
    s = Surface(np.vstack(vs), np.vstack(ts), np.concatenate(fo), k, np.zeros(k))
    a, _ = s.tri_area_normal()
    mesh_area = np.bincount(s.face_of, weights=a, minlength=k)
    exact = np.asarray(exact, float)
    # exact unless OCCT's number is not finite, not positive, or far from the mesh (broken face)
    ok = np.isfinite(exact) & (exact > 0) & (np.abs(exact - mesh_area) <= 0.1 * np.maximum(mesh_area, 1e-9))
    s.face_area = np.where(ok, exact, mesh_area)
    s.area_fallback = [int(i) for i in np.flatnonzero(~ok & (mesh_area > 0))]
    return s


def surface_samples(s: Surface, spacing: float, skip_faces=()) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Points about ``spacing`` apart on the surface.  Each triangle is cut, from the corner opposite its
    shortest edge, into strips ``spacing`` deep, each strip holding as many points across as its width
    needs: about area / spacing^2 + length / spacing points, so a long thin triangle (the mesh of a
    cylinder's side) gets points by its size, not by its length squared.  Each point comes with its
    outward normal, the area it stands for (per face, scaled to the face's area) and its face."""
    a0, nrm0 = s.tri_area_normal()
    tri = s.tri
    e = np.stack([np.linalg.norm(tri[:, (k + 1) % 3] - tri[:, (k + 2) % 3], axis=1) for k in range(3)], 1)
    k = np.argmin(e, axis=1)                                   # apex: opposite the shortest edge
    i = np.arange(len(tri))
    A, B, C = tri[i, k], tri[i, (k + 1) % 3], tri[i, (k + 2) % 3]
    m = np.maximum(1, np.ceil(np.maximum(e[i, (k + 1) % 3], e[i, (k + 2) % 3]) / spacing)).astype(np.int64)
    # strips: triangle t, strip j of m
    ts = np.repeat(i, m)
    js = np.arange(m.sum()) - np.repeat(np.cumsum(m) - m, m)
    tm = (js + 0.5) / m[ts]                                    # station along the apex-to-base direction
    base = e[ts, k[ts]]
    across = np.maximum(1, np.ceil(tm * base / spacing)).astype(np.int64)
    strip_area = a0[ts] * ((js + 1) ** 2 - js ** 2) / m[ts] ** 2
    # points: strip q, point r of across[q]
    q = np.repeat(np.arange(len(ts)), across)
    r = np.arange(across.sum()) - np.repeat(np.cumsum(across) - across, across)
    w = (r + 0.5) / across[q]
    t_ = ts[q]
    P0 = A[t_] + tm[q, None] * (B[t_] - A[t_])
    P1 = A[t_] + tm[q, None] * (C[t_] - A[t_])
    pts = P0 + w[:, None] * (P1 - P0)
    ar = strip_area[q] / across[q]
    # the normal interpolated from the vertices (barycentric weights 1 - tm, tm (1 - w), tm w)
    vn = s.vertex_normals()
    kk = k[t_]
    ia, ib, ic = s.t[t_, kk], s.t[t_, (kk + 1) % 3], s.t[t_, (kk + 2) % 3]
    tq = tm[q]
    nn = (1 - tq)[:, None] * vn[ia] + (tq * (1 - w))[:, None] * vn[ib] + (tq * w)[:, None] * vn[ic]
    nn = nn / np.maximum(np.linalg.norm(nn, axis=1), 1e-300)[:, None]
    fc = s.face_of[t_]
    mesh_face = np.bincount(fc, weights=ar, minlength=s.n_faces)
    ar = ar * (s.face_area / np.maximum(mesh_face, 1e-300))[fc]
    if len(skip_faces):
        keep = ~np.isin(fc, np.asarray(list(skip_faces)))
        pts, nn, ar, fc = pts[keep], nn[keep], ar[keep], fc[keep]
    return pts, nn, ar, fc


def edge_points(s: Surface, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Points along the part's edges (mesh edges between two B-rep faces), ``spacing`` apart, each with
    the bisector of its two faces' outward normals.  The largest sphere at an inside corner (the middle
    of an X or T junction) touches only the corner edges, never a face, so it grows only from points on
    them; and as obstacles they stop spheres bulging into the gap at an edge."""
    _, tn = s.tri_area_normal()
    # OCCT meshes each face on its own: weld coincident vertices so an edge is shared across faces
    scale = float(np.abs(s.v).max()) or 1.0
    _, weld = np.unique(np.round(s.v / (scale * 1e-9)).astype(np.int64), axis=0, return_inverse=True)
    weld = weld.ravel()
    t = weld[s.t]
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    f = np.concatenate([s.face_of] * 3)
    en = np.concatenate([tn] * 3)
    key = np.sort(e, axis=1)
    o = np.lexsort((key[:, 1], key[:, 0]))
    key, f, en = key[o], f[o], en[o]
    same = np.all(key[1:] == key[:-1], axis=1)
    border = same & (f[1:] != f[:-1])
    ed = key[1:][border]
    nb = en[1:][border] + en[:-1][border]
    nb = nb / np.maximum(np.linalg.norm(nb, axis=1), 1e-12)[:, None]
    first = np.zeros(int(weld.max()) + 1, np.int64)
    first[weld[::-1]] = np.arange(len(weld))[::-1]          # a vertex of each welded group
    a, b = s.v[first[ed[:, 0]]], s.v[first[ed[:, 1]]]
    n = np.maximum(1, np.ceil(np.linalg.norm(b - a, axis=1) / spacing)).astype(np.int64)
    k = np.repeat(np.arange(len(ed)), n)
    u = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n) + 0.5) / np.repeat(n, n)
    return a[k] + u[:, None] * (b[k] - a[k]), nb[k]


def wall_thickness(shape, s: Surface, n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Thickness at ``n`` surface points (area-weighted, seeded): the first wall an inward ray meets."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    a, nrm = s.tri_area_normal()
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(a), size=n, p=a / a.sum())
    b = rng.random((n, 2))
    flip = b.sum(1) > 1
    b[flip] = 1 - b[flip]
    p = s.tri[pick]
    q = p[:, 0] + b[:, :1] * (p[:, 1] - p[:, 0]) + b[:, 1:] * (p[:, 2] - p[:, 0])
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN

    hit = IntCurvesFace_ShapeIntersector()
    hit.Load(shape.wrapped, 1e-6)
    inside = BRepClass3d_SolidClassifier(shape.wrapped)
    eps = 1e-3
    out = np.full(n, np.nan)
    for k in range(n):
        d = -nrm[pick[k]]
        o = q[k] + eps * d
        hit.Perform(gp_Lin(gp_Pnt(*o), gp_Dir(*d)), 0.0, 1e9)
        ws = sorted(w for w in (hit.WParameter(i) for i in range(1, hit.NbPnt() + 1)) if w > 1e-6)
        if not ws:
            continue
        # a mesh point on a concave face (a bore) lies off the metal: then the ray first enters it
        inside.Perform(gp_Pnt(*o), 1e-7)
        if inside.State() == TopAbs_IN:
            out[k] = ws[0] + eps
        elif len(ws) >= 2:
            out[k] = ws[1] - ws[0]
    ok = np.isfinite(out)
    return out[ok], a[pick][ok]


def weighted_percentile(x: np.ndarray, w: np.ndarray, pct: float) -> float:
    o = np.argsort(x, kind="stable")
    c = np.cumsum(w[o])
    return float(x[o][min(np.searchsorted(c, pct / 100.0 * c[-1]), len(x) - 1)])


# ---- voxels -------------------------------------------------------------------------------
def voxelize(s: Surface, h: float, part_id: str = "part", chunk: int = 4_000_000, shape=None,
             report: list | None = None) -> VoxelPart:
    """Metal where the voxel centre is inside: vertical rays through the column centres, crossings
    summed as a winding number (entering through a downward-facing triangle counts +1).  Shared edges
    follow the top-left rule on canonically ordered edges, so a ray through an edge counts once."""
    lo, hi = s.v.min(0), s.v.max(0)
    origin, n = VoxelPart._grid(lo, hi, h)
    nx, ny, nz = (int(x) for x in n)
    p = s.tri
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    a2 = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    keep = np.abs(a2) > 1e-12
    p, a2 = p[keep], a2[keep]
    up = a2 > 0                                   # outward normal points up: going up, the ray leaves
    p = np.where(up[:, None, None], p, p[:, [0, 2, 1]])          # counter-clockwise seen from above
    a2 = np.abs(a2)
    ox, oy, oz = origin
    i0 = np.ceil((p[..., 0].min(1) - ox) / h - 0.5).astype(int)
    i1 = np.floor((p[..., 0].max(1) - ox) / h - 0.5).astype(int)
    j0 = np.ceil((p[..., 1].min(1) - oy) / h - 0.5).astype(int)
    j1 = np.floor((p[..., 1].max(1) - oy) / h - 0.5).astype(int)
    ni, nj = np.maximum(i1 - i0 + 1, 0), np.maximum(j1 - j0 + 1, 0)
    cnt = ni * nj
    wind = np.zeros(nx * ny * (nz + 1), np.int32)
    delta_all = np.where(up, -1, 1)
    starts = np.concatenate([[0], np.cumsum(cnt)])
    t0 = 0
    while t0 < len(p):
        t1 = int(np.searchsorted(starts, starts[t0] + chunk, side="right")) - 1
        t1 = max(t1, t0 + 1)
        sel = np.arange(t0, t1)
        c = cnt[sel]
        tid = np.repeat(sel, c)
        off = np.arange(c.sum()) - np.repeat(starts[sel] - starts[t0], c)
        ii = i0[tid] + off // nj[tid]
        jj = j0[tid] + off % nj[tid]
        px = ox + (ii + 0.5) * h
        py = oy + (jj + 0.5) * h
        tri = p[tid]
        inside = np.ones(len(tid), bool)
        w = []
        for e in range(3):
            a, b = tri[:, (e + 1) % 3, :2], tri[:, (e + 2) % 3, :2]      # edge opposite vertex e
            swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
            lo_ = np.where(swap[:, None], b, a)
            hi_ = np.where(swap[:, None], a, b)
            ec = (hi_[:, 0] - lo_[:, 0]) * (py - lo_[:, 1]) - (hi_[:, 1] - lo_[:, 1]) * (px - lo_[:, 0])
            ee = np.where(swap, -ec, ec)
            d = b - a
            top_left = (d[:, 1] < 0) | ((d[:, 1] == 0) & (d[:, 0] < 0))
            inside &= (ee > 0) | ((ee == 0) & top_left)
            w.append(ee)
        if inside.any():
            ww = np.stack(w, 1)[inside] / a2[tid[inside], None]
            zz = np.einsum("ij,ij->i", ww, tri[inside][:, :, 2])
            k = np.clip(np.ceil((zz - oz) / h - 0.5).astype(int), 0, nz)
            flat = (ii[inside] * ny + jj[inside]) * (nz + 1) + k
            wind += np.bincount(flat, weights=delta_all[tid[inside]], minlength=len(wind)).astype(np.int32)
        t0 = t1
    w3 = np.cumsum(wind.reshape(nx, ny, nz + 1), axis=2)
    metal = w3[:, :, :nz] > 0
    # a column whose crossings do not add back to zero went through a gap in the mesh (a face OCCT
    # could not mesh, a crack between faces): it would fill air up to the top of the grid.  Those
    # columns are cast again on the exact solid.
    bad = np.argwhere(w3[:, :, nz] != 0)
    if len(bad) and shape is not None:
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        hit = IntCurvesFace_ShapeIntersector()
        hit.Load(shape.wrapped, 1e-6)
        zc = oz + (np.arange(nz) + 0.5) * h
        top = oz + nz * h + 10.0
        for i, j in bad:
            x, y = ox + (i + 0.5) * h, oy + (j + 0.5) * h
            hit.Perform(gp_Lin(gp_Pnt(x, y, top), gp_Dir(0, 0, -1)), 0.0, 1e9)
            zs = sorted({round(hit.Pnt(m).Z(), 9) for m in range(1, hit.NbPnt() + 1)})
            col = np.zeros(nz, bool)
            for a, b in zip(zs[0::2], zs[1::2]):
                col |= (zc > a) & (zc < b)
            metal[i, j] = col
    if report is not None and len(bad):
        report.append(f"{len(bad)} voxel column(s) crossed a gap in the mesh"
                      + ("; cast again on the exact solid" if shape is not None else ""))
    return VoxelPart(metal, float(h), origin, part_id)


# ---- distance and thickness ---------------------------------------------------------------
def surface_distance(vp: VoxelPart, pts: np.ndarray, nrm: np.ndarray, spacing: float) -> np.ndarray:
    """Distance (mm) from each metal voxel centre to the surface; 0 outside.  The nearest sample's
    triangle plane gives the exact distance over a flat face; ``spacing`` bounds the error elsewhere."""
    idx = np.argwhere(vp.metal)
    c = vp.centre(idx)
    tree = cKDTree(pts)
    de, k = tree.query(c, workers=-1)
    dp = np.abs(np.einsum("ij,ij->i", c - pts[k], nrm[k]))
    r = np.minimum(de, np.maximum(dp, de - spacing))
    out = np.zeros(vp.shape)
    out[tuple(idx.T)] = r
    return out


def _shift(a: np.ndarray, o, fill=0.0) -> np.ndarray:
    """b[x] = a[x + o]."""
    b = np.full_like(a, fill)
    src = tuple(slice(max(d, 0), a.shape[ax] + min(d, 0)) for ax, d in enumerate(o))
    dst = tuple(slice(max(-d, 0), a.shape[ax] + min(-d, 0)) for ax, d in enumerate(o))
    b[dst] = a[src]
    return b


def medial_balls(pts: np.ndarray, nrm: np.ndarray, tree: cKDTree, scale: float,
                 iterations: int = 60, obstacles: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The largest inscribed sphere touching the surface at each sample (shrinking ball, Ma et al.
    2012): start with a huge sphere tangent at p, and while some sample q lies inside it, shrink it to
    the sphere tangent at p through q, R = |p - q|^2 / (2 n . (p - q)).  Exact to the sample spacing,
    whatever the voxel size.  ``tree`` holds the points a sphere may not contain: ``obstacles`` (the
    samples themselves when None).  Returns (centres, radii)."""
    R = np.full(len(pts), scale)
    active = np.arange(len(pts))
    for _ in range(iterations):
        if not len(active):
            break
        c = pts[active] - R[active, None] * nrm[active]
        d, j = tree.query(c, workers=-1)
        q = (pts if obstacles is None else obstacles)[j]
        dp = pts[active] - q
        den = 2 * np.einsum("ij,ij->i", dp, nrm[active])
        inside = (d < R[active] * (1 - 1e-9) - 1e-9) & (den > 1e-12)
        newR = np.where(inside, np.einsum("ij,ij->i", dp, dp) / np.where(den > 1e-12, den, 1.0), np.inf)
        shrink = inside & (newR < R[active] * (1 - 1e-9))
        R[active[shrink]] = newR[shrink]
        active = active[shrink]
    return pts - R[:, None] * nrm, R


def largest_per_cell(vp: VoxelPart, centres: np.ndarray, radii: np.ndarray, spacing_fraction: float,
                     min_cells_per_voxel: int = 2, band: float = 1.25) -> tuple[np.ndarray, np.ndarray]:
    """Thin the spheres before painting: in each band of similar radius (within ``band``), one sphere
    per cell, the largest whose centre falls in it.  The cell is ``spacing_fraction`` x the band's
    radius, never finer than 1 / ``min_cells_per_voxel`` of a voxel.  Neighbours that close nearly
    contain each other, so coverage hardly changes, while painting work falls with the square of the
    spacing (a wall's spheres were one per half voxel: 266 s on a 1 M voxel part).  Spacing by the
    radius, not the voxel, also keeps where a junction's spheres end the same when the voxel halves."""
    h = vp.h
    keep = []
    ids = np.flatnonzero(radii > 0)
    lo = float(radii[ids].min())
    bnd = np.floor(np.log(radii[ids] / lo) / math.log(band)).astype(int)
    for b in np.unique(bnd):
        sel = ids[bnd == b]
        cell = max(h / min_cells_per_voxel, spacing_fraction * float(radii[sel].min()))
        c = np.floor((centres[sel] - vp.origin) / cell).astype(np.int64)
        key = (c[:, 0] * 1_000_003 + c[:, 1]) * 1_000_033 + c[:, 2]
        o = np.lexsort((-radii[sel], key))
        first = np.ones(len(o), bool)
        first[1:] = key[o][1:] != key[o][:-1]
        keep.append(sel[o[first]])
    k = np.sort(np.concatenate(keep))
    return centres[k], radii[k]


def local_thickness(vp: VoxelPart, centres: np.ndarray, radii: np.ndarray, tie: float = 0.02,
                    chunk: int = 4_000_000) -> tuple[np.ndarray, np.ndarray]:
    """Diameter (mm) of the largest inscribed sphere covering each voxel centre (0 where none does),
    and which sphere that is (index into ``centres``, -1 where none).  Spheres are painted in chunks
    of (sphere, offset) pairs with a scatter-max, then a second pass names the owner."""
    h = vp.h
    shape = np.asarray(vp.shape)
    cv = (centres - vp.origin) / h - 0.5                 # in voxel-index units
    base_all = np.round(cv).astype(np.int64)
    frac_all = cv - base_all
    rad_all = radii / h
    ids = np.flatnonzero(rad_all > 0.5)
    strides = np.array([shape[1] * shape[2], shape[2], 1], np.int64)
    lt = np.zeros(int(np.prod(shape)))
    owner = np.full(len(lt), -1, np.int64)
    groups = np.ceil(rad_all[ids] + 0.5).astype(int)
    batches = []
    for g in np.unique(groups):
        sel = ids[groups == g]
        o = np.argwhere(np.ones((2 * g + 1,) * 3, bool)) - g
        o = o[np.linalg.norm(o, axis=1) <= g + 0.5]
        per = max(1, chunk // len(o))
        for a0 in range(0, len(sel), per):
            batches.append((sel[a0:a0 + per], o))

    def pairs(sel, o):
        t = base_all[sel][:, None, :] + o[None, :, :]                      # (n, m, 3)
        ok = np.all((t >= 0) & (t < shape), axis=2)
        dist = np.linalg.norm(o[None, :, :] - frac_all[sel][:, None, :], axis=2)
        ok &= dist <= rad_all[sel][:, None] + 1e-6
        flat = (t @ strides)[ok]
        who = np.broadcast_to(sel[:, None], ok.shape)[ok]
        return flat, who, dist[ok]

    for sel, o in batches:
        flat, who, _ = pairs(sel, o)
        np.maximum.at(lt, flat, 2 * radii[who])
    # the owner: of the spheres within ``tie`` of the largest, the one whose centre is nearest (equal
    # spheres all along a member would otherwise give a voxel to whichever was painted last)
    near = np.full(len(lt), np.inf)
    for sel, o in batches:
        flat, who, d = pairs(sel, o)
        top = 2 * radii[who] >= (1 - tie) * lt[flat]
        np.minimum.at(near, flat[top], d[top])
    for sel, o in batches:
        flat, who, d = pairs(sel, o)
        mine = (2 * radii[who] >= (1 - tie) * lt[flat]) & (d <= near[flat])
        owner[flat[mine]] = who[mine]
    metal = vp.metal.ravel()
    lt = np.where(metal, lt, 0.0).reshape(vp.shape)
    owner = np.where(metal, owner, -1).reshape(vp.shape)
    return lt, owner


def thickness_at(q: np.ndarray, centres: np.ndarray, radii: np.ndarray, k: int = 8,
                 ratio: float = 1.25) -> np.ndarray:
    """Local thickness at arbitrary points, from the spheres themselves: twice the radius of the largest
    sphere containing each point.  Spheres are taken in groups of similar radius (within ``ratio``);
    in each group the ``k`` nearest centres are tried."""
    out = np.zeros(len(q))
    if not len(q) or not len(radii):
        return out
    lo = max(float(radii.min()), 1e-9)
    grp = np.floor(np.log(np.maximum(radii, lo) / lo) / math.log(ratio)).astype(int)
    for g in np.unique(grp):
        sel = np.flatnonzero(grp == g)
        rg = float(radii[sel].max())
        kk = min(k, len(sel))
        d, j = cKDTree(centres[sel]).query(q, k=kk, distance_upper_bound=rg * (1 + 1e-9) + 1e-9)
        if kk == 1:
            d, j = d[:, None], j[:, None]
        ok = np.isfinite(d)
        jj = np.where(ok, j, 0)
        r = radii[sel][jj]
        cov = ok & (d <= r * (1 + 1e-9) + 1e-9)
        out = np.maximum(out, np.where(cov, 2 * r, 0.0).max(axis=1))
    return out


def centre_zones(vp: VoxelPart, zl: np.ndarray, th: dict[int, float], q: np.ndarray, centres: np.ndarray,
                 radii: np.ndarray, sphere_zone: np.ndarray, k: int = 8, ratio: float = 1.25) -> np.ndarray:
    """The zone of each point ``q`` (a sphere centre, on the medial axis).  The candidates are the zones
    whose core spheres (``sphere_zone`` > 0: centred in the zone's core) contain the point;
    of those, the one whose thickness is nearest the thickness at the point itself (twice the largest
    sphere containing it).  A point none of them contains takes the zone of the voxel holding it (the
    nearest core).

    A zone so reaches only where its own core spheres reach: nothing passes from voxel to voxel, so a
    thick member's tapering edges cannot be handed on to a thinner member (a stepped plate's thick
    half lost a sixth of its cooling area that way), and between a junction and its arms the split is
    where the junction's sphere ends, exactly, at any voxel size."""
    thv = np.zeros(int(zl.max()) + 1)
    for i, v in th.items():
        thv[i] = v
    out = _zone_at(vp, zl, q)
    if not len(q):
        return out
    t_q = thickness_at(q, centres, radii)
    best_c = np.full(len(q), np.inf)
    best_z = np.zeros(len(q), np.int64)
    core = np.flatnonzero(sphere_zone > 0)
    if not len(core):
        return out
    lo = max(float(radii[core].min()), 1e-9)
    grp = np.floor(np.log(np.maximum(radii[core], lo) / lo) / math.log(ratio)).astype(int)
    for g in np.unique(grp):
        sel = core[grp == g]
        kk = min(k, len(sel))
        d, j = cKDTree(centres[sel]).query(q, k=kk, distance_upper_bound=float(radii[sel].max()) * (1 + 1e-9) + 1e-9)
        if kk == 1:
            d, j = d[:, None], j[:, None]
        ok = np.isfinite(d)
        jj = sel[np.where(ok, j, 0)]
        cov = ok & (d <= radii[jj] * (1 + 1e-9) + 1e-9)
        z = sphere_zone[jj]
        c = np.where(cov, np.abs(t_q[:, None] - thv[z]), np.inf)
        m = np.argmin(c, axis=1)
        cm = c[np.arange(len(q)), m]
        better = cm < best_c
        best_c[better], best_z[better] = cm[better], z[np.arange(len(q)), m][better]
    out[np.isfinite(best_c)] = best_z[np.isfinite(best_c)]
    return out


# ---- zones --------------------------------------------------------------------------------
def segment(lt: np.ndarray, metal: np.ndarray, h: float, slope_max: float, core_fraction: float,
            core_min_voxels: float, same_tol: float) -> tuple[np.ndarray, dict[int, float], np.ndarray]:
    """Zone label per voxel (1..n, 0 outside: each voxel its nearest core), each zone's plateau
    thickness (mm), and the core voxels' labels (0 elsewhere)."""
    g = np.zeros(lt.shape)
    for o in OFFS6:
        nb, nm = _shift(lt, o), _shift(metal, o, False)
        g = np.maximum(g, np.where(nm, np.abs(lt - nb), 0.0))
    plateau = metal & (lt > 0) & (g <= slope_max * h)
    if not plateau.any():
        raise ModulusError("no section of constant thickness found (voxels too coarse?)")
    # seeds are the cores of the flat regions: voxels at least ``core_fraction`` of their own sphere's
    # radius (and ``core_min_voxels``) in from the region's edge.  A thin layer of the same thickness
    # cannot join two cores (the arms of an X meet in one round its end faces, where the junction's
    # thickness falls to theirs)
    inr = ndi.distance_transform_edt(plateau) * h
    cores = plateau & (inr >= np.maximum(core_fraction * lt / 2, core_min_voxels * h))
    if not cores.any():
        cores = plateau & (inr >= inr.max())
    lab, n = ndi.label(cores, structure=SIX)
    ids = np.arange(1, n + 1)
    thick = np.asarray(ndi.median(lt, lab, ids))
    zl = lab.astype(np.int64)
    th = {int(i): float(thick[i - 1]) for i in ids}
    seeded = cores
    # every other metal voxel joins the nearest core (straight line).  Purely geometric: it cannot chain
    # along an edge to another zone (a thick member's edges taper like a ramp to a thinner one), nor
    # hang on which flood arrives first; between a junction and its arms it cuts across the member
    # halfway between their cores
    _, ind = ndi.distance_transform_edt(~seeded, return_indices=True)
    zl = np.where(metal, zl[tuple(ind)], 0)

    # one member split by noise: touching zones of the same thickness whose seam is that thick too
    parent = {i: i for i in th}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seam = _seam_thickness(zl, lt)
    for (a, b), (t_seam, n_faces) in sorted(seam.items()):
        ra, rb = root(a), root(b)
        if ra == rb or a not in th or b not in th:
            continue
        # a member split by noise: the seam is as thick as both sides (not a thinner bridge between
        # two walls, nor the thicker ramp of a junction two arms meet across) and runs across the
        # member (half a thickness squared at least), not a sliver where two arms of an X touch at the
        # end faces, where the junction's thickness falls to theirs
        mid = 0.5 * (th[ra] + th[rb])
        if abs(th[ra] - th[rb]) <= same_tol * max(th[ra], th[rb]) and abs(t_seam - mid) <= same_tol * mid \
                and n_faces * h * h >= 0.5 * mid * mid:
            parent[max(ra, rb)] = min(ra, rb)
    lut = np.arange(int(zl.max()) + 1)
    for i in th:
        lut[i] = root(i)
    zl = lut[zl]
    return zl, {i: v for i, v in th.items() if root(i) == i}, np.where(seeded, zl, 0)


def _seam_thickness(zl: np.ndarray, lt: np.ndarray) -> dict[tuple[int, int], tuple[float, int]]:
    """For each pair of touching zones (a < b): the median local thickness on the voxel faces between
    them, and how many faces."""
    vals: dict[tuple[int, int], list] = {}
    for ax in range(3):
        a = [slice(None)] * 3
        b = [slice(None)] * 3
        a[ax], b[ax] = slice(0, -1), slice(1, None)
        la, lb = zl[tuple(a)], zl[tuple(b)]
        m = (la != lb) & (la > 0) & (lb > 0)
        if not m.any():
            continue
        x, y = np.minimum(la[m], lb[m]), np.maximum(la[m], lb[m])
        t = np.minimum(lt[tuple(a)][m], lt[tuple(b)][m])
        key = x.astype(np.int64) * (int(zl.max()) + 1) + y
        o = np.argsort(key, kind="stable")
        key, t = key[o], t[o]
        cut = np.flatnonzero(np.diff(key)) + 1
        for kk, tt in zip(np.split(key, cut), np.split(t, cut)):
            pair = (int(kk[0] // (int(zl.max()) + 1)), int(kk[0] % (int(zl.max()) + 1)))
            vals.setdefault(pair, []).append(tt)
    return {k: (float(np.median(np.concatenate(v))), int(sum(len(x) for x in v))) for k, v in vals.items()}


def _interfaces(lab: np.ndarray) -> list[tuple[int, int, int]]:
    """(a, b, number of shared voxel faces), a < b, sorted."""
    acc: dict[tuple[int, int], int] = {}
    for ax in range(3):
        a = [slice(None)] * 3
        b = [slice(None)] * 3
        a[ax], b[ax] = slice(0, -1), slice(1, None)
        la, lb = lab[tuple(a)], lab[tuple(b)]
        m = (la != lb) & (la > 0) & (lb > 0)
        if not m.any():
            continue
        x, y = np.minimum(la[m], lb[m]), np.maximum(la[m], lb[m])
        pairs, cnt = np.unique(np.stack([x, y], 1), axis=0, return_counts=True)
        for (p, q), c in zip(pairs, cnt):
            acc[(int(p), int(q))] = acc.get((int(p), int(q)), 0) + int(c)
    return [(a, b, c) for (a, b), c in sorted(acc.items())]


# ---- hot spots ----------------------------------------------------------------------------
def hot_spots(r: np.ndarray, metal: np.ndarray, h: float, rise: float) -> list[dict]:
    """Maxima of r rising more than ``rise`` (mm) above every path to higher ground; one per
    plateau.  r is binned by ``rise``: a regional maximum of the bins is noise when, going down one
    bin, it joins higher ground (or an equal maximum met first)."""
    q = np.where(metal, np.floor(r / rise).astype(np.int64), -1)
    lm = metal & (q == ndi.maximum_filter(q, size=3, mode="constant", cval=-1))
    lab, n = ndi.label(lm, structure=np.ones((3, 3, 3)))
    if n == 0:
        return []
    ids = np.arange(1, n + 1)
    val = np.asarray(ndi.maximum(q, lab, ids)).astype(int)
    pos = ndi.maximum_position(r, lab, ids)
    out, taken = [], set()
    for v in sorted(set(val.tolist()), reverse=True):
        reach, _ = ndi.label(q >= v - 1, structure=np.ones((3, 3, 3)))
        top = ndi.maximum(q, reach, np.arange(1, reach.max() + 1))
        for c in ids[val == v]:
            comp = int(reach[pos[c - 1]])
            if top[comp - 1] > v or comp in taken:
                continue
            taken.add(comp)
            plat = (reach == comp) & (q == v)
            pi = np.argwhere(plat)
            cen = pi.mean(0)
            best = pi[np.argmin(np.linalg.norm(pi - cen, axis=1))]      # on the plateau, even if it bends
            out.append({"voxel": tuple(int(x) for x in best), "radius": float(r[plat].max())})
    return out


# ---- the analysis -------------------------------------------------------------------------
@dataclass
class ModulusResult:
    section: dict                     # the job's `modulus` section (without `field`)
    vp: VoxelPart
    r: np.ndarray                     # distance to the surface, mm
    lt: np.ndarray                    # local thickness, mm
    zones: np.ndarray                 # zone label per voxel (0 outside), labels in section order + 1
    timings: dict
    issues: list[str]
    surface: dict = field(default_factory=dict)   # per surface sample: point, area, face, zone (1-based)

    def save_field(self, path) -> None:
        np.savez_compressed(path, distance=self.r.astype(np.float32), thickness=self.lt.astype(np.float32),
                            zone=self.zones.astype(np.int16), origin=self.vp.origin, h=self.vp.h)


def _r6(x: float) -> float:
    return float(round(float(x), 6))


def choose_voxel(min_wall: float, bbox_lo, bbox_hi, st: dict) -> tuple[float, bool]:
    """``voxel_fraction_of_min_wall`` x the thinnest wall, at least ``voxel_min_mm``, and coarse enough
    that the grid holds no more than ``max_voxels``.  Returns (h, capped)."""
    h = max(st["voxel_fraction_of_min_wall"] * min_wall, st["voxel_min_mm"])
    ext = np.asarray(bbox_hi) - np.asarray(bbox_lo)
    h_cap = (float(np.prod(ext + 2 * h)) / st["max_voxels"]) ** (1 / 3)
    return (h_cap, True) if h_cap > h else (h, False)


def analyse(shape, st: dict, h: float | None = None, skip_faces=(), part_id: str = "part",
            seed: int = 0) -> ModulusResult:
    """Phase 1 on a shape already in the mold frame.  ``st`` is ``settings['modulus']``; ``skip_faces``
    (face indices) do not cool (a test's cut ends standing in for an infinitely long body)."""
    t = {}
    issues: list[str] = []
    t0 = time.perf_counter()
    surf = triangulate(shape, st["mesh_relative_deflection"], st["mesh_angular_deflection_rad"])
    if surf.area_fallback:
        issues.append(f"{len(surf.area_fallback)} face(s) have no usable exact area; their mesh area is used")
    t["mesh_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    thick, w = wall_thickness(shape, surf, st["min_wall_rays"], seed)
    sliver = thick < st["voxel_min_mm"]
    if sliver.any():
        issues.append(f"{int(sliver.sum())} of {len(thick)} wall rays met another face within "
                      f"{st['voxel_min_mm']} mm (coincident or sliver faces in the part file?); left out of "
                      f"the thinnest wall")
        thick, w = thick[~sliver], w[~sliver]
    if not len(thick):
        raise ModulusError("no inward ray met a wall: is the part a closed solid?")
    min_wall = weighted_percentile(thick, w, st["min_wall_percentile"])
    t["min_wall_s"] = time.perf_counter() - t0
    capped = False
    if h is None:
        h, capped = choose_voxel(min_wall, surf.v.min(0), surf.v.max(0), st)
        if capped:
            issues.append(f"voxel size {h:.3g} mm set by max_voxels ({st['max_voxels']:.3g}), coarser than "
                          f"{st['voxel_fraction_of_min_wall']} x the thinnest wall ({min_wall:.3g} mm)")

    t0 = time.perf_counter()
    vp = voxelize(surf, h, part_id, shape=shape, report=issues)
    if not vp.metal.any():
        raise ModulusError("no voxel is inside the part (voxels larger than the part?)")
    t["voxelize_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    spacing = st["sample_spacing_fraction"] * h
    all_pts, all_nrm, all_area, all_face = surface_samples(surf, spacing)
    if len(skip_faces):
        cool = ~np.isin(all_face, np.asarray(list(skip_faces)))
        pts, nrm, area, face = all_pts[cool], all_nrm[cool], all_area[cool], all_face[cool]
    else:
        pts, nrm, area, face = all_pts, all_nrm, all_area, all_face
    r = surface_distance(vp, all_pts, all_nrm, spacing)
    t["distance_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    # the largest inscribed sphere touching the surface at each sphere sample (all faces: the geometry,
    # cooling or not); each area sample takes its nearest sphere sample's sphere
    fpts, fnrm, _, _ = surface_samples(surf, st["ball_spacing_fraction"] * h)
    epts, enrm = edge_points(surf, 0.5 * st["ball_spacing_fraction"] * h)
    bpts, bnrm = np.concatenate([fpts, epts]), np.concatenate([fnrm, enrm])
    t1 = time.perf_counter()
    cen_b, rad_b = medial_balls(bpts, bnrm, cKDTree(bpts), 1.05 * float(r.max()) + 2 * h)
    t["spheres_s"] = time.perf_counter() - t1
    _, nb = cKDTree(fpts).query(pts, workers=-1)          # each area sample: its nearest face sphere
    cen_p, rad_p = largest_per_cell(vp, cen_b, rad_b, st["sphere_spacing_fraction"])
    t1 = time.perf_counter()
    lt, owner = local_thickness(vp, cen_p, rad_p)
    t["paint_s"] = time.perf_counter() - t1
    t["thickness_s"] = time.perf_counter() - t0
    t["n_spheres"] = float(len(bpts))
    t["n_painted"] = float(len(cen_p))

    t0 = time.perf_counter()
    zl, plate_t, zl_core = segment(lt, vp.metal, h, st["plateau_slope_max"], st["plateau_core_fraction"],
                                   st["plateau_core_min_voxels"], st["same_thickness_tol"])
    # every voxel joins the zone of the largest sphere covering it, every bit of surface the zone of the
    # largest sphere touching it: the zone at that sphere's centre, on the medial axis
    tol = st["same_thickness_tol"]
    # a zone's own spheres: those centred in its core
    sph_zone = zl_core[tuple(np.clip(np.floor((cen_p - vp.origin) / h).astype(int), 0,
                                     np.asarray(vp.shape) - 1).T)]
    zone_p = centre_zones(vp, zl, plate_t, cen_p, cen_p, rad_p, sph_zone)
    face_sph = np.unique(nb)
    zone_f = np.zeros(len(fpts), np.int64)
    zone_f[face_sph] = centre_zones(vp, zl, plate_t, cen_b[face_sph], cen_p, rad_p, sph_zone)
    has = owner >= 0
    zl = zl.copy()
    zl[has] = zone_p[owner[has]]
    zone_of = zone_f[nb]
    t["zones_s"] = time.perf_counter() - t0

    # zone volume (scaled to the exact volume) and cooling area (surface samples, to the voxel inside)
    labels = [int(x) for x in np.unique(zl[zl > 0])]
    vox = np.bincount(zl.ravel(), minlength=max(labels) + 1).astype(float)
    exact_v = float(shape.volume)
    mesh_v = surf.mesh_volume()
    if math.isfinite(exact_v) and abs(exact_v - mesh_v) <= 0.05 * abs(mesh_v):
        vol_total = exact_v
    else:
        vol_total = mesh_v
        issues.append("the exact volume is not usable; the mesh volume is used")
    vscale = vol_total / (vox[1:].sum() * h ** 3)
    a_zone = np.bincount(zone_of, weights=area, minlength=len(vox))

    cen = ndi.center_of_mass(vp.metal, zl, labels)
    rmax = ndi.maximum(r, zl, labels)
    order = sorted(range(len(labels)), key=lambda i: tuple(round(float(x), 3) for x in cen[i]))
    zid = {labels[i]: f"z{k}" for k, i in enumerate(order)}
    zones = []
    for i in order:
        li = labels[i]
        v = vox[li] * h ** 3 * vscale
        a = float(a_zone[li])
        zones.append({"id": zid[li], "volume": _r6(v), "cooling_area": _r6(a),
                      "modulus": _r6(v / a) if a > 0 else 0.0,
                      "centroid": [_r6(x) for x in vp.origin + (np.asarray(cen[i]) + 0.5) * h],
                      "max_distance": _r6(rmax[i])})
    lut = np.zeros(max(labels) + 1, np.int64)
    for k, i in enumerate(order):
        lut[labels[i]] = k + 1
    zl = lut[zl]

    adj = [{"a": f"z{a - 1}", "b": f"z{b - 1}", "interface_area": _r6(c * h * h)}
           for a, b, c in _interfaces(zl)]
    adj = sorted(({**e, "a": min(e["a"], e["b"]), "b": max(e["a"], e["b"])} for e in adj),
                 key=lambda e: (e["a"], e["b"]))

    hs = hot_spots(r, vp.metal, h, st["hot_spot_min_rise_voxels"] * h)
    hs.sort(key=lambda x: (-round(x["radius"], 6), x["voxel"]))
    spots = [{"id": f"h{k}", "position": [_r6(x) for x in vp.centre(np.asarray(x["voxel"]))],
              "radius": _r6(x["radius"]), "local_modulus": _r6(x["radius"] / 3),
              "zone": f"z{int(zl[x['voxel']]) - 1}"} for k, x in enumerate(hs)]

    section = {"voxel_size": _r6(h), "min_wall": _r6(min_wall),
               "grid": {"origin": [_r6(x) for x in vp.origin], "shape": [int(x) for x in vp.shape]},
               "field": None, "volume": _r6(vol_total), "zones": zones, "hot_spots": spots, "adjacency": adj}
    return ModulusResult(section, vp, r, lt, zl, t, issues,
                         {"point": pts, "area": area, "face": face, "zone": lut[zone_of]})


def _zone_at(vp: VoxelPart, zl: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Zone of the voxel holding each point (the nearest zoned voxel where that one has none).  Used with
    the centre of the largest sphere touching each surface sample: the centre lies on the medial axis,
    deep in the zone the surface there belongs to, so the split of the surface does not hang on how
    the zones' voxels happen to meet it."""
    ijk = np.floor((q - vp.origin) / vp.h).astype(int)
    ok = np.all((ijk >= 0) & (ijk < np.asarray(vp.shape)), axis=1)
    z = np.zeros(len(q), np.int64)
    z[ok] = zl[tuple(ijk[ok].T)]
    miss = z == 0
    if miss.any():
        idx = np.argwhere(zl > 0)
        _, k = cKDTree(vp.centre(idx)).query(q[miss], workers=-1)
        z[miss] = zl[tuple(idx[k].T)]
    return z


# ---- the phase ----------------------------------------------------------------------------
def part_in_mold(job) -> object:
    """The job's part, read in its units and placed by the parting transform (mold frame)."""
    from core.occ import transformed
    from core.orient import to_matrix
    from core.part import load_source

    inp = job.data["inputs"]
    src = load_source(job.part_path(), inp["part"]["units"])
    return transformed(src.shape, to_matrix(inp["parting"]["part_transform"]))


def run(job, h: float | None = None) -> ModulusResult:
    """Phase 1 on a GatingJob: writes the ``modulus`` section (and, for a saved job, the field file
    ``<job id>.modulus.npz`` beside it, for the heatmap)."""
    from layouts.job import JobError

    if job.part_changed():
        raise JobError(["the part file changed since the job was made (GatingJob.refresh_part)"])
    job.start("modulus")
    t0 = time.perf_counter()
    try:
        st = job.data["recipe"]["settings"]["modulus"]
        res = analyse(part_in_mold(job), st, h=h, seed=job.data["inputs"]["seed"])
    except Exception as e:                                         # noqa: BLE001
        job.fail("modulus", f"{type(e).__name__}: {e}")
        raise
    section = dict(res.section)
    if job.path is not None:
        f = job.path.parent / f"{job.data['id']}.modulus.npz"
        res.save_field(f)
        section["field"] = f.name
    job.finish("modulus", section, runtime_s=round(time.perf_counter() - t0, 3),
               counts={"zones": len(section["zones"]), "hot_spots": len(section["hot_spots"])},
               issues=res.issues)
    return res
