"""Suggested pouring positions for a part, before the box is made.

Two ways to part a casting, both offered, the user picks one:

  widest  turned so one of its own axes points up (the one with the least
          undercut, then the largest section), parted at its widest
          horizontal section; where the section is widest over a height
          (a flange), at the end of that band nearer the middle of the part
  flat    its largest flat face (coplanar faces taken together) laid on the
          parting plane facing up, the whole part below it in the drag

Each suggestion is the move that takes the part from where it is now to
there, centred on the box axis with the parting plane at z = 0, and what it
leaves: the part's size, how far it reaches below and above the plane, and
an estimate of the undercut area (a ray from each face along its half's pull
that meets the part again).  Nothing here is a foundry rule; the user decides.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from build123d import Shape
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

from core.frames import rigid
from core.occ import transformed
from core.orient import aligning

SLICES = 40              # sections per axis, then refined around the widest
PLATEAU = 0.99           # sections this close to the widest count as the widest band
MAX_FACES = 4000         # undercut estimate on at most this many faces (STL parts: every triangle)
EPS = 1e-2               # mm


def _bbox(shape: Shape):
    b = shape.bounding_box()
    return np.array(tuple(b.min)), np.array(tuple(b.max))


class Mesh:
    """The part's surface as triangles (outward), for fast sections."""

    def __init__(self, shape: Shape, tol: float = 0.2):
        verts, tris = shape.tessellate(tol, 0.3)
        self.v = np.array([tuple(p) for p in verts], float)
        self.t = np.asarray(tris, int)
        self.flat_z: np.ndarray = np.zeros(0)
        a, b, c = (self.v[self.t[:, k]] for k in range(3))
        if np.einsum("ij,ij->i", a, np.cross(b, c)).sum() < 0:          # wound inward: turn it
            self.t = self.t[:, ::-1]

    def turned(self, r: np.ndarray) -> "Mesh":
        m = Mesh.__new__(Mesh)
        m.v, m.t = self.v @ np.asarray(r).T, self.t
        a, b, c = (m.v[m.t[:, k]] for k in range(3))
        n = np.cross(b - a, c - a)
        L = np.linalg.norm(n, axis=1)
        flat = (L > 0) & (np.abs(n[:, 2]) > 0.9999 * L)                 # horizontal triangles: flat faces' heights
        m.flat_z = np.unique(np.round(a[flat, 2], 3))
        return m

    def section_area(self, z: float) -> float:
        """Area of the horizontal section at z: Green's theorem over the cut segments, each run
        counter-clockwise seen from above (the surface's outward normal on its right)."""
        a, b, c = (self.v[self.t[:, k]] for k in range(3))
        n = np.cross(b - a, c - a)
        pts = []
        for p, q in ((a, b), (b, c), (c, a)):
            dp, dq = p[:, 2] - z, q[:, 2] - z
            cross = dp * dq < 0
            t = np.where(cross, dp / np.where(cross, dp - dq, 1.0), 0.0)
            pts.append((p + (q - p) * t[:, None], cross))
        P = np.stack([x for x, _ in pts], 1)                                # (N, 3 edges, 3)
        M = np.stack([m for _, m in pts], 1)
        keep = M.sum(1) == 2
        if not keep.any():
            return 0.0
        P, M, n = P[keep], M[keep], n[keep]
        idx = np.argsort(~M, axis=1, kind="stable")[:, :2]                  # the two crossing edges
        p1, p2 = P[np.arange(len(P)), idx[:, 0]], P[np.arange(len(P)), idx[:, 1]]
        d = p2 - p1
        tang = np.stack([-n[:, 1], n[:, 0]], 1)                             # z x n: the boundary's forward direction
        flip = (d[:, 0] * tang[:, 0] + d[:, 1] * tang[:, 1]) < 0
        p1[flip], p2[flip] = p2[flip], p1[flip].copy()
        return float(0.5 * np.sum(p1[:, 0] * p2[:, 1] - p2[:, 0] * p1[:, 1]))


def widest(mesh: Mesh) -> tuple[float, float]:
    """(z, area) of the widest horizontal section of a mesh (already turned)."""
    lo, hi = mesh.v.min(0), mesh.v.max(0)
    step = (hi[2] - lo[2]) / (SLICES - 1)
    zs = np.linspace(lo[2] + EPS, hi[2] - EPS, SLICES)
    areas = np.array([mesh.section_area(z) for z in zs])
    k = int(np.argmax(areas))
    fine = np.linspace(zs[max(k - 1, 0)], zs[min(k + 1, len(zs) - 1)], 21)
    zs = np.concatenate([zs, fine])
    areas = np.concatenate([areas, [mesh.section_area(z) for z in fine]])
    top = areas.max()
    band = np.sort(zs[areas >= PLATEAU * top])
    mid = (lo[2] + hi[2]) / 2
    # the widest band's end nearer the middle: a flange stays whole in one half
    z = float(min((band[0], band[-1]), key=lambda e: abs(e - mid)))
    # onto a flat face there (a flange's top) when one is within a slice
    near = mesh.flat_z[np.abs(mesh.flat_z - z) <= step] if mesh.flat_z.size else []
    if len(near):
        z = float(near[np.argmin(np.abs(near - z))])
    return float(np.clip(z, lo[2], hi[2])), float(top)


def undercut(shape: Shape) -> tuple[float, int]:
    """(area, faces) that cannot be drawn: in the mold frame, parting plane z = 0, cope pulled up, drag down."""
    hit = IntCurvesFace_ShapeIntersector()
    hit.Load(shape.wrapped, 1e-6)
    faces = shape.faces()
    stride = max(1, len(faces) // MAX_FACES)
    area, count = 0.0, 0
    for f in faces[::stride]:
        try:
            p = f.position_at(0.5, 0.5)
            n = f.normal_at(p)
        except Exception:                                         # noqa: BLE001
            continue
        if abs(p.Z) < EPS:
            continue
        pull = 1.0 if p.Z > 0 else -1.0
        if n.Z * pull > 0.999:                                    # looks straight along the pull: draws
            continue
        o = np.array([p.X, p.Y, p.Z]) + EPS * np.array([n.X, n.Y, n.Z]) + (0, 0, pull * EPS)
        hit.Perform(gp_Lin(gp_Pnt(*o), gp_Dir(0, 0, pull)), 0.0, 1e9)
        if hit.NbPnt() > 0:
            area += f.area * stride
            count += stride
    return area, count


def _placed(shape: Shape, r: np.ndarray, z_part: float) -> tuple[np.ndarray, Shape]:
    """The move (rotate by r about the origin, then centre on the box axis with z_part at z = 0)."""
    turned = transformed(shape, rigid(r))
    lo, hi = _bbox(turned)
    d = rigid(translation=(-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -z_part)) @ rigid(r)
    return d, transformed(shape, d)


def _summary(kind: str, label: str, d: np.ndarray, placed: Shape, **extra) -> dict:
    lo, hi = _bbox(placed)
    area, faces = undercut(placed)
    return {"id": kind, "label": label, "move": d.tolist(), "bbox": [lo.tolist(), hi.tolist()],
            "size": (hi - lo).tolist(), "below": float(-lo[2]), "above": float(hi[2]),
            "undercut_mm2": round(area, 1), "undercut_faces": faces, **extra}


AXES = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0), "+Z": (0, 0, 1), "-Z": (0, 0, -1)}


def suggest_widest(shape: Shape, mesh: Mesh | None = None) -> dict:
    """Of the three axes of the part as it is now (each pointing up), the one with the least undercut
    when parted at its widest section, then the largest section."""
    mesh = mesh or Mesh(shape)
    best = None
    for name in ("+Z", "+X", "+Y"):
        r = aligning(AXES[name], (0, 0, 1))
        z, area = widest(mesh.turned(r))
        d, placed = _placed(shape, r, z)
        s = _summary("widest", "Parted at the widest section", d, placed, up=name, section_mm2=round(area, 1))
        key = (s["undercut_mm2"], -area)
        if best is None or key < best[0]:
            best = (key, s)
    return best[1]


def flat_faces(shape: Shape) -> list[tuple[float, np.ndarray, float]]:
    """Planar faces on the outside of the part, coplanar ones together, largest first: (area, normal, offset)."""
    groups: dict[tuple, list] = {}
    for f in shape.faces():
        if f.geom_type.name != "PLANE":
            continue
        p = f.position_at(0.5, 0.5)
        n = np.array(tuple(f.normal_at(p)))
        n = n / np.linalg.norm(n)
        off = float(n @ np.array(tuple(p)))
        key = (*np.round(n, 3), round(off, 1))
        g = groups.setdefault(key, [0.0, n, off])
        g[0] += f.area
    verts = np.array([tuple(v) for v in shape.vertices()])
    out = []
    for a, n, off in groups.values():
        if verts.size and off >= float((verts @ n).max()) - 1e-3:    # nothing of the part beyond it
            out.append((a, n, off))
    return sorted(out, key=lambda g: -g[0])


def suggest_flat(shape: Shape) -> dict | None:
    faces = flat_faces(shape)
    if not faces:
        return None
    a, n, off = faces[0]
    r = aligning(n, (0, 0, 1))                     # the face looks up; the part hangs below it, in the drag
    turned = transformed(shape, rigid(r))
    _, hi = _bbox(turned)
    d, placed = _placed(shape, r, float(hi[2]))
    return _summary("flat", "Largest flat face on the plane", d, placed, face_mm2=round(a, 1))


def suggest(shape: Shape) -> list[dict]:
    """Both suggestions (the flat one only when the part has an outer flat face)."""
    out = [suggest_widest(shape)]
    flat = suggest_flat(shape)
    if flat is not None:
        out.append(flat)
    return out


# ---- the box ---------------------------------------------------------------------------------

BOXES_FILE = Path(__file__).resolve().parents[1] / "presets" / "boxes" / "matchboxes.json"


def standard_boxes(path: Path | None = None) -> list[dict]:
    """[{"name", "length", "width", "cope", "drag"}] (mm, inside sizes), as saved."""
    p = path or BOXES_FILE
    return json.loads(p.read_text()).get("boxes", []) if p.exists() else []


def add_standard_box(box: dict, path: Path | None = None) -> list[dict]:
    p = path or BOXES_FILE
    data = json.loads(p.read_text()) if p.exists() else {"boxes": []}
    entry = {"name": str(box.get("name") or f"{box['length']:g} x {box['width']:g} x {box['cope']:g}/{box['drag']:g}"),
             **{k: float(box[k]) for k in ("length", "width", "cope", "drag")}}
    if min(entry[k] for k in ("length", "width", "cope", "drag")) <= 0:
        raise ValueError("a box needs a positive length, width, cope and drag")
    data["boxes"] = [b for b in data.get("boxes", []) if b["name"] != entry["name"]] + [entry]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    return data["boxes"]


def _up(x: float, step: float) -> float:
    return float(np.ceil(x / step - 1e-9) * step) if step > 0 else float(x)


def propose_box(bbox, sides: float = 0.0, below: float = 0.0, above: float = 0.0, extra: float = 0.0,
                step: float = 0.0, standards: list[dict] | None = None) -> dict:
    """The box for a part placed as suggested (bbox in the mold frame, parting plane z = 0): the smallest
    standard box it fits in with the clearances, else the part plus clearances rounded up to step.
    extra is more length on one end (-x) for the sprue and runner; the part moves +extra/2 to leave it."""
    lo, hi = np.asarray(bbox[0], float), np.asarray(bbox[1], float)
    need = {"length": hi[0] - lo[0] + 2 * sides + extra, "width": hi[1] - lo[1] + 2 * sides,
            "drag": max(-lo[2], 0.0) + below, "cope": max(hi[2], 0.0) + above}
    fits = []
    for b in standards or []:
        for L, W in ((b["length"], b["width"]), (b["width"], b["length"])):
            if L >= need["length"] - 1e-9 and W >= need["width"] - 1e-9 and \
                    b["cope"] >= need["cope"] - 1e-9 and b["drag"] >= need["drag"] - 1e-9:
                fits.append((L * W * (b["cope"] + b["drag"]), b["name"], L, W, b["cope"], b["drag"]))
                break
    if fits:
        _, name, L, W, cope, drag = min(fits)
    else:
        name = None
        L, W = _up(need["length"], step), _up(need["width"], step)
        cope, drag = _up(need["cope"], step), _up(need["drag"], step)
    return {"length": L, "width": W, "height": cope + drag, "parting": drag, "cope": cope, "drag": drag,
            "standard": name, "need": need, "shift": [extra / 2, 0.0, 0.0]}
