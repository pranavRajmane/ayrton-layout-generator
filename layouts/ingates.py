"""Phase 2: every place on the parting line where an ingate can sit.

The ingate is a flat bar on the parting plane, in the drag (z from -t to 0) or the cope (0 to t), run
straight out from the part along the outward normal (``elements/ingate.py``).  So a candidate is a
station on the part's *outer* outline in that band:

1. The part is cut just off the parting plane, on the ingate's side.  The cut gives closed loops, each
   segment knowing the B-rep face it lies on.  Loops inside an odd number of others are holes (cores):
   metal cannot reach them from a runner, so they give no candidates.
2. Each outer loop is split into patches, one per face.  A patch on a tagged face, or shorter than the
   recipe's minimum contact length, is discarded whole.
3. Stations are spread along each patch so the whole ingate width sits on it, ``merge_distance`` apart.
   A station is discarded when a straight ingate run out from it would cut the part again within the
   runner clearance (at three heights in the band), when the zone behind it is not heavier than the
   ingate (the ingate must freeze first), or when no zone lies behind it.
4. The rest are flagged (impinging a core or a thin section straight ahead), tagged by the recipe's
   rules, and merged: a station closer than ``merge_distance`` to one with a longer contact joins it.

Everything is deterministic: loops, patches and stations are visited in geometric order.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.GCPnts import GCPnts_AbscissaPoint, GCPnts_QuasiUniformAbscissa
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.TopAbs import TopAbs_EDGE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.element import Section

PART = "part"


# ---- the outline ----------------------------------------------------------------------------
@dataclass
class Loop:
    pts: np.ndarray          # (n, 2), closed implicitly (segment i runs from pts[i] to pts[i + 1 mod n])
    face: np.ndarray         # (n,) face index of each segment (-1: unknown)
    depth: int               # 0: bounds metal from outside (counter-clockwise); 1: a hole (core)

    @property
    def seg(self) -> tuple[np.ndarray, np.ndarray]:
        return self.pts, np.roll(self.pts, -1, axis=0)

    @property
    def length(self) -> float:
        a, b = self.seg
        return float(np.linalg.norm(b - a, axis=1).sum())


def _signed_area(p: np.ndarray) -> float:
    q = np.roll(p, -1, axis=0)
    return 0.5 * float((p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]).sum())


def inside(points: np.ndarray, loops: list[Loop]) -> np.ndarray:
    """Even-odd: in the metal of the cut."""
    points = np.atleast_2d(points)
    odd = np.zeros(len(points), bool)
    for lp in loops:
        a, b = lp.seg
        ay, by = a[:, 1][None], b[:, 1][None]
        px, py = points[:, 0:1], points[:, 1:2]
        cross = (ay > py) != (by > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = a[:, 0][None] + (py - ay) * (b[:, 0] - a[:, 0])[None] / (by - ay)
        odd ^= (cross & (px < xint)).sum(1) % 2 == 1
    return odd


def ray_hit(p, d, loops: list[Loop], max_t: float) -> tuple[float, int] | None:
    """First crossing of the ray p + t d (0 < t <= max_t) with any loop: (t, loop index)."""
    xs = crossings(p, d, loops, max_t)
    return xs[0] if xs else None


def crossings(p, d, loops: list[Loop], max_t: float) -> list[tuple[float, int]]:
    """Every crossing of the ray p + t d (0 < t <= max_t) with the loops, nearest first."""
    out = []
    for k, lp in enumerate(loops):
        a, b = lp.seg
        e = b - a
        den = d[0] * e[:, 1] - d[1] * e[:, 0]
        ok = np.abs(den) > 1e-12
        w = a - p
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
            u = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den
        hit = ok & (t > 1e-6) & (t <= max_t) & (u >= 0) & (u < 1)
        out += [(float(x), k) for x in np.unique(np.round(t[hit], 9))]
    return sorted(out)


def cut(shape, z: float, step: float, join_tol: float = 0.05) -> list[Loop]:
    """The part cut by the plane at height z, as loops with the metal on their left (outer loops
    counter-clockwise, holes clockwise), in a fixed order."""
    from build123d import Face

    faces = shape.faces()
    fidx = {hash(f): i for i, f in enumerate(faces)}
    sec = BRepAlgoAPI_Section(shape.wrapped, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), False)
    sec.ComputePCurveOn1(True)
    sec.Approximation(False)
    sec.Build()
    pieces = []
    ex = TopExp_Explorer(sec.Shape(), TopAbs_EDGE)
    while ex.More():
        e = TopoDS.Edge(ex.Current())
        ex.Next()
        f = TopoDS_Shape()
        fi = -1
        if sec.HasAncestorFaceOn1(e, f):
            fi = fidx.get(hash(Face(TopoDS.Face(f))), -1)
        c = BRepAdaptor_Curve(e)
        L = GCPnts_AbscissaPoint.Length_s(c)
        if L < 1e-9:
            continue
        n = max(int(math.ceil(L / step)), 1) + 1
        qa = GCPnts_QuasiUniformAbscissa(c, n)
        if qa.IsDone() and qa.NbPoints() >= 2:
            us = [qa.Parameter(i) for i in range(1, qa.NbPoints() + 1)]
        else:
            us = list(np.linspace(c.FirstParameter(), c.LastParameter(), n))
        pts = np.array([[c.Value(u).X(), c.Value(u).Y()] for u in us])
        if fi < 0:
            fi = _nearest_face(faces, c.Value(0.5 * (us[0] + us[-1])))
        pieces.append((pts, fi))
    loops = _chain(pieces, join_tol)
    # metal on the left of every loop: probe just beside the middle of its longest segment with the
    # even-odd rule over all the loops (a probe on the loop itself is ambiguous where loops touch).
    # Then counter-clockwise loops bound metal from outside (outer), clockwise ones are holes.
    for lp in loops:
        a, b = lp.seg
        e = b - a
        L = np.linalg.norm(e, axis=1)
        i = int(np.argmax(L))
        m = (a[i] + b[i]) / 2
        left = np.array([-e[i, 1], e[i, 0]]) / L[i]
        d = min(1e-3, L[i] / 10)
        if not inside((m + d * left)[None], loops)[0]:
            lp.pts = lp.pts[::-1].copy()
            lp.face = np.roll(lp.face[::-1], -1)
        lp.depth = 0 if _signed_area(lp.pts) > 0 else 1
    for lp in loops:                                    # start each loop at its lowest (x, y) point
        i = int(np.lexsort((lp.pts[:, 1].round(6), lp.pts[:, 0].round(6)))[0])
        lp.pts = np.roll(lp.pts, -i, axis=0)
        lp.face = np.roll(lp.face, -i)
    loops.sort(key=lambda lp: (lp.depth, round(float(lp.pts[0, 0]), 6), round(float(lp.pts[0, 1]), 6)))
    return loops


def _nearest_face(faces, p) -> int:
    from build123d import Vertex
    v = Vertex(p.X(), p.Y(), p.Z())
    d = [f.distance_to(v) for f in faces]
    return int(np.argmin(d))


def _chain(pieces: list[tuple[np.ndarray, int]], tol: float = 1e-3) -> list[Loop]:
    """Join edge polylines end to end into closed loops (segment faces kept)."""
    left = list(range(len(pieces)))
    loops = []
    while left:
        i = left.pop(0)
        pts, f = pieces[i]
        chain = [pts]
        faces = [np.full(len(pts) - 1, f)]
        start, end = pts[0], pts[-1]
        while np.linalg.norm(end - start) > tol:
            best = None
            for j in left:
                q, _ = pieces[j]
                for rev, s in ((False, q[0]), (True, q[-1])):
                    d = float(np.linalg.norm(s - end))
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, j, rev)
            if best is None:
                break                                   # open: a gap in the cut (bad B-rep); closed anyway
            _, j, rev = best
            left.remove(j)
            q, fj = pieces[j]
            q = q[::-1] if rev else q
            chain.append(q[1:])
            faces.append(np.full(len(q) - 1, fj))
            end = q[-1]
        p = np.vstack(chain)
        fc = np.concatenate(faces)
        if np.linalg.norm(p[-1] - p[0]) <= tol:
            p = p[:-1]                                  # last point repeats the first
        else:
            fc = np.append(fc, -1)                      # the closing jump knows no face
        if len(p) >= 3 and abs(_signed_area(p)) > 1e-9:
            loops.append(Loop(p, fc[:len(p)], 0))
    return loops


# ---- patches and stations --------------------------------------------------------------------
@dataclass
class Patch:
    loop: int
    face: int
    start: float             # arc length along the loop where it begins
    length: float
    closed: bool             # the whole loop is one face


def patches(lp: Loop, k: int) -> list[Patch]:
    a, b = lp.seg
    sl = np.linalg.norm(b - a, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(sl)])
    f = lp.face
    if (f == f[0]).all():
        return [Patch(k, int(f[0]), 0.0, float(cum[-1]), True)]
    cuts = [i for i in range(len(f)) if f[i] != f[i - 1]]      # a patch starts at each face change
    out = []
    for n, i in enumerate(cuts):
        j = cuts[(n + 1) % len(cuts)]
        length = cum[j] - cum[i] if j > i else cum[-1] - cum[i] + cum[j]
        out.append(Patch(k, int(f[i]), float(cum[i]), float(length), False))
    return out


class Walker:
    """Points and directions at an arc length along a closed loop."""

    def __init__(self, lp: Loop):
        a, b = lp.seg
        self.a, self.e = a, b - a
        self.sl = np.linalg.norm(self.e, axis=1)
        self.cum = np.concatenate([[0.0], np.cumsum(self.sl)])
        self.L = float(self.cum[-1])

    def at(self, s):
        s = np.mod(np.asarray(s, float), self.L)
        i = np.clip(np.searchsorted(self.cum, s, side="right") - 1, 0, len(self.sl) - 1)
        t = (s - self.cum[i]) / np.where(self.sl[i] > 0, self.sl[i], 1.0)
        return self.a[i] + t[..., None] * self.e[i]


def stations(p: Patch, width: float, spacing: float) -> list[float]:
    """Arc positions of ingate centres on the patch: the ingate wholly on it, ``spacing`` apart,
    centred; a patch narrower than the ingate gets one, at its middle."""
    if p.closed:
        n = max(int(p.length // spacing), 1)
        return [p.start + (k + 0.5) * p.length / n for k in range(n)]
    room = p.length - width
    if room < 0:
        return [p.start + p.length / 2]
    n = int(room // spacing) + 1
    first = width / 2 + (room - (n - 1) * spacing) / 2
    return [p.start + first + k * spacing for k in range(n)]


# ---- the phase --------------------------------------------------------------------------------
RULES = {
    "feeds_heaviest_zone": lambda c, ctx: c["zone_modulus"] >= ctx["max_modulus"] - 1e-9,
    "feeds_hot_spot_zone": lambda c, ctx: c["target_zone"] in ctx["hot_spot_zones"],
    "no_impingement": lambda c, ctx: not (c["flags"]["impinges_core"] or c["flags"]["impinges_thin_wall"]),
}


def ingate_section(values: dict) -> Section:
    g = values["layout"]["ingate"]
    t = float(g["thickness_mm"])
    return Section.rectangle(float(g["aspect"]) * t, t)


def section_dict(sec: Section) -> dict:
    return {"shape": sec.shape, "dims": {k: _r6(v) for k, v in sec.dims}, "area": _r6(sec.area)}


def bar_modulus(sec: Section) -> float:
    """Of a long bar of this section: area / perimeter (its ends do not count)."""
    return sec.area / sec.perimeter


def _r6(x) -> float:
    return float(round(float(x), 6))


def field_of(res) -> dict:
    """The Phase 1 fields Phase 2 reads, from a ModulusResult (or use ``np.load`` of the field file)."""
    return {"zone": res.zones, "thickness": res.lt, "origin": res.vp.origin, "h": res.vp.h}


def _lookup(field: dict, name: str, pts: np.ndarray) -> np.ndarray:
    g = field[name]
    ijk = np.floor((np.asarray(pts, float) - np.asarray(field["origin"], float)) / float(field["h"])).astype(int)
    ok = np.all((ijk >= 0) & (ijk < np.asarray(g.shape)), axis=1)
    out = np.zeros(len(pts), g.dtype)
    out[ok] = g[tuple(ijk[ok].T)]
    return out


def find(shape, modulus: dict, field: dict, values: dict, st: dict, face_tags=()) -> tuple[list, list, list]:
    """Candidates, discarded stretches / stations, and issues, for a part in the mold frame."""
    g = values["layout"]["ingate"]
    side = g["side"]
    sgn = -1.0 if side == "drag" else 1.0
    t = float(g["thickness_mm"])
    sec = ingate_section(values)
    w = sec.d["width"]
    m_ingate = bar_modulus(sec)
    reach = float(values["layout"]["clearance"]["runner_cavity_mm"])
    d_merge = float(g["merge_distance_mm"])
    min_contact = float(g["min_contact_length_mm"])
    imp_d = float(g["impingement_distance_mm"])
    thin = float(g["thin_wall_mm"])
    rules = list(g["rules"])
    unknown = sorted(set(rules) - set(RULES) | set(rules) - set(st["known_rules"]))
    if unknown:
        raise ValueError(f"the recipe names ingate rule(s) {unknown}; known: {sorted(RULES)}")
    step = float(st["outline_step_mm"])
    jt = float(st.get("join_tolerance_mm", 0.05))
    eps = float(st["parting_tolerance_mm"])
    h = float(field["h"])
    tagged = {int(x["face"]) for x in face_tags}
    issues: list[str] = []

    z_top, z_mid, z_bot = sgn * eps, sgn * t / 2, sgn * (t - eps)
    top = cut(shape, z_top, step, jt)
    if not top:
        return [], [], [f"the part has no metal just {side == 'drag' and 'below' or 'above'} the parting plane "
                        f"(ingates in the {side})"]
    band = {z_top: top, z_mid: cut(shape, z_mid, step, jt), z_bot: cut(shape, z_bot, step, jt)}
    # the outline the ingate's end face meets: the part at the ingate's mid height (right at the parting
    # plane a part can carry a lip or a land the ingate passes over)
    loops = band[z_mid] or top
    holes = sum(lp.depth % 2 for lp in loops)
    if holes:
        issues.append(f"{holes} hole(s) in the parting-line cut are cores: no ingates there")
    zones = {z["id"]: z for z in modulus["zones"]}
    ctx = {"max_modulus": max((z["modulus"] for z in modulus["zones"]), default=0.0),
           "hot_spot_zones": {hs["zone"] for hs in modulus["hot_spots"]}}

    pre, discarded = [], []

    def discard(loc, face, length, reasons, detail=None):
        rec = {"id": f"d{len(discarded)}", "part": PART, "location": [_r6(loc[0]), _r6(loc[1]), 0.0],
               "contact_face": None if face < 0 else int(face), "contact_length": _r6(length),
               "reasons": reasons}
        if detail:
            rec["detail"] = detail
        discarded.append(rec)

    n_station = 0
    for k, lp in enumerate(loops):
        if lp.depth % 2:
            continue
        wk = Walker(lp)
        for p in patches(lp, k):
            mid = wk.at(p.start + p.length / 2)
            why = []
            if p.face < 0:
                why.append("outline_gap")        # a stretch that only closes a gap in the cut: no face
            if p.face in tagged:
                why.append("tagged_face")
            if p.length < min_contact - 1e-9:
                why.append("short_contact")
            if why:
                discard(mid, p.face, p.length, why)
                continue
            for s in stations(p, w, d_merge):
                sid = f"s{n_station}"
                n_station += 1
                c = wk.at(s)
                lo, hi = (s - w / 2, s + w / 2) if p.closed else (max(s - w / 2, p.start),
                                                                     min(s + w / 2, p.start + p.length))
                tan = wk.at(hi) - wk.at(lo)
                if np.linalg.norm(tan) < 1e-9:
                    tan = wk.at(s + step) - wk.at(s - step)
                tan = tan / np.linalg.norm(tan)
                nrm = np.array([tan[1], -tan[0]])            # metal on the left: outward is the right
                # how far the outline bulges out past the contact chord (a concave face)
                ss = np.linspace(lo, hi, 9)
                bulge = max(float(((wk.at(ss) - c) @ nrm).max()), 0.0)
                v0 = bulge + max(2 * step, eps)
                # metal just inside the contact and sand just outside, at the top of the band: a loop
                # taken the wrong way round (touching loops in a bad part file) points into the metal
                probe = np.array([c - 2 * step * nrm, c + 2 * step * nrm])
                if not (inside(probe[:1], loops)[0] and not inside(probe[1:], loops)[0]):
                    discard(c, p.face, p.length, ["not_outward"])
                    continue
                us = np.linspace(-w / 2, w / 2, 5)      # five lines across the ingate
                vs = np.arange(v0, reach + 1e-9, max(min(step, t / 2), 0.25))
                grid = (c[None, None] + us[:, None, None] * tan + vs[None, :, None] * nrm).reshape(-1, 2)
                # blocked: along any line of the ingate, metal again after sand (another part of the
                # casting in the way).  Metal right at the contact is the part itself: the ingate overlaps it.
                blocked = False
                for zl in band.values():
                    ins = inside(grid, zl).reshape(len(us), len(vs))
                    sand = np.cumsum(~ins, axis=1) > 0
                    if (ins & sand).any():
                        blocked = True
                        break
                # the zone behind the contact: voxels just inside, across the width and the band
                ins = [np.r_[c + u * tan - dv * nrm, zb] for u in us for zb in band for dv in (0.5 * h, 1.5 * h, 2.5 * h)]
                zs = _lookup(field, "zone", np.array(ins))
                zs = zs[zs > 0]
                zone = None
                if len(zs):
                    vals, cnt = np.unique(zs, return_counts=True)
                    zone = f"z{int(vals[np.argmax(cnt)]) - 1}"
                if blocked or zone is None:
                    discard(c, p.face, p.length, (["blocked"] if blocked else []) + (["no_zone"] if zone is None else []))
                    continue
                zm = zones[zone]["modulus"]
                if not m_ingate < zm:
                    discard(c, p.face, p.length, ["ingate_not_thinner"],
                            f"ingate modulus {m_ingate:.3g} mm, zone {zone} {zm:.3g} mm")
                    continue
                # straight ahead, at mid band, across the stream's width: a core within reach, or a
                # section thinner than the recipe's thin wall
                core = thin_ahead = False
                mid_loops = band[z_mid]
                for u in us:
                    start = c + u * tan + v0 * nrm
                    xs = crossings(start, -nrm, mid_loops, v0 + 2 * reach + imp_d)
                    if not inside(start, mid_loops)[0]:
                        if not xs:
                            continue
                        enter = xs.pop(0)[0]
                    else:
                        enter = 0.0
                    stop = enter + imp_d
                    if xs and xs[0][0] <= stop:
                        stop = xs[0][0]
                        core |= mid_loops[xs[0][1]].depth % 2 == 1
                    if abs(u) > w / 4 + 1e-9:
                        continue                             # thin wall: the stream's middle half only
                    ahead = np.arange(enter + 1.5 * h, stop, 0.5 * h)
                    lt = _lookup(field, "thickness",
                                 np.c_[start[None] - ahead[:, None] * nrm, np.full(len(ahead), z_mid)])
                    lt = lt[lt > 0]
                    thin_ahead |= bool(len(lt) and float(lt.min()) < thin)
                cand = {"id": sid, "part": PART, "location": [_r6(c[0]), _r6(c[1]), 0.0],
                        "contact_face": None if p.face < 0 else int(p.face),
                        "normal": [_r6(nrm[0]), _r6(nrm[1])], "target_zone": zone, "zone_modulus": _r6(zm),
                        "contact_length": _r6(p.length), "section": section_dict(sec),
                        "ingate_modulus": _r6(m_ingate),
                        "flags": {"impinges_core": bool(core), "impinges_thin_wall": thin_ahead},
                        "rule_tags": [], "merged_from": [], "_order": len(pre)}
                cand["rule_tags"] = [r for r in rules if RULES[r](cand, ctx)]
                pre.append(cand)

    # merge: the longest contact first; a station within merge distance of a kept one joins it
    kept: list[dict] = []
    for c in sorted(pre, key=lambda c: (-c["contact_length"], c["_order"])):
        if kept:
            loc = np.array([k["location"][:2] for k in kept])
            d = np.linalg.norm(loc - np.array(c["location"][:2]), axis=1)
            j = int(np.argmin(d))
            if d[j] < d_merge - 1e-9:
                kept[j]["merged_from"].append(c["id"])
                continue
        kept.append(c)
    kept.sort(key=lambda c: c["_order"])
    out = []
    for i, c in enumerate(kept):
        c = dict(c)
        c.pop("_order")
        c["merged_from"] = sorted([c["id"]] + c["merged_from"], key=lambda x: int(x[1:])) if c["merged_from"] else []
        c["id"] = f"g{i}"
        out.append(c)
    return out, discarded, issues


# ---- the stub check ---------------------------------------------------------------------------
def stub(cand: dict, values: dict, length: float | None = None):
    """The candidate's ingate, built by the CAD module's ingate element: from ``length`` out (default:
    the runner clearance) to the part, embedded past the contact as the element does."""
    from elements.ingate import Ingate

    g = values["layout"]["ingate"]
    L = float(length if length is not None else values["layout"]["clearance"]["runner_cavity_mm"])
    c, n = np.array(cand["location"][:2]), np.array(cand["normal"])
    sec = cand["section"]["dims"]
    p = Ingate.resolve_params({"side": g["side"], "width": sec["width"], "thickness": sec["height"],
                               "waypoints": ((*(c + L * n), 0.0), (*c, 0.0))})
    return Ingate().build(p)


def stub_problems(shape, solid, part_valid: bool | None = None) -> list[str]:
    """The stub fused with the part: one solid, no less valid than the part itself, volumes adding up
    (to 0.1 % of the stub, or 1e-5 of the part: Booleans on a large part file drift by that much).
    ``part_valid``: the part's own BRepCheck result, if already known (a part file that is not a valid
    solid cannot give a valid union)."""
    fused = shape.fuse(solid)
    out = []
    if len(fused.solids()) != 1:
        out.append(f"{len(fused.solids())} solids after the union")
    if not fused.is_valid:
        if part_valid is None:
            part_valid = shape.is_valid
        if part_valid:
            out.append("the union is not a valid solid")
    common = shape.intersect(solid)
    overlap = sum(s.volume for s in common.solids()) if common is not None else 0.0
    expect = shape.volume + solid.volume - overlap
    if abs(fused.volume - expect) > max(1e-3 * solid.volume, 1e-5 * shape.volume):
        out.append(f"union volume {fused.volume:.1f} != part + stub - overlap {expect:.1f}")
    if overlap <= 0:
        out.append("the stub does not touch the part")
    return out


def run(job, field: dict | None = None) -> tuple[list, list]:
    """Phase 2 on a GatingJob: writes the ``ingate_candidates`` section.  ``field`` defaults to the
    Phase 1 field file beside the saved job."""
    from layouts.job import JobError
    from layouts.modulus import part_in_mold

    if job.part_changed():
        raise JobError(["the part file changed since the job was made (GatingJob.refresh_part)"])
    job.start("ingate_candidates")
    t0 = time.perf_counter()
    d = job.data
    try:
        if field is None:
            f = d["modulus"].get("field")
            if not f or job.path is None:
                raise JobError(["no Phase 1 field file to read (run modulus on a saved job)"])
            field = dict(np.load(job.path.parent / f))
        cands, disc, issues = find(part_in_mold(job), d["modulus"], field, d["recipe"]["values"],
                                   d["recipe"]["settings"]["ingates"], d["inputs"]["face_tags"])
    except Exception as e:                                          # noqa: BLE001
        job.fail("ingate_candidates", f"{type(e).__name__}: {e}")
        raise
    reasons: dict[str, int] = {}
    for x in disc:
        for r in x["reasons"]:
            reasons[r] = reasons.get(r, 0) + 1
    job.finish("ingate_candidates", {"candidates": cands, "discarded": disc},
               runtime_s=round(time.perf_counter() - t0, 3),
               counts={"candidates": len(cands), "discarded": len(disc), **{f"discarded_{k}": v for k, v in sorted(reasons.items())}},
               issues=issues)
    return cands, disc
