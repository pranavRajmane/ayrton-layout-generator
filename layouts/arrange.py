"""Phase 3: every arrangement of N cavities in the flask that a symmetric runner can feed.

A part is never mirrored to make a pair (that is a different part), so cavities are copies of an
orbit representative under turns about the sprue S, or about the tree runner's junctions; a mirrored
copy is offered only when the part is itself mirror-symmetric (voxel IoU with its mirror image above
``symmetry.mirror_iou_min``).  The representative is the part turned 0 / 90 / 180 / 270 degrees with
one ingate candidate; its runner template fixes where every other cavity goes:

  row     straight   k cavities side by side along one runner line through S, their ingates square
                     to it; the other side is their half-turn about S (c2), or their mirror image
                     across the runner (d1, mirror-symmetric parts only).  N = 2k; N = 1 is the single
                     cavity beside the sprue (c1).
  grid    h / tree   balanced binary splits (an H-tree): S -> junctions -> ... -> one cavity per leaf,
                     each split's two halves each other's half-turn, directions alternating; the
                     ingate continues the leaf arm (feed "end") or leaves its side ("side").  N = 2^m,
                     m = 2 is the H.
  radial  radial     four cavities turned 90 degrees apart about S, each ingate facing it (c4).

Each combination is placed as tightly as its clearances allow: the free lengths (pitch, offset,
arm lengths, radius) are shrunk one at a time by bisection while the cavities stay apart, the runner
bands, ingates and sprue stay clear (``layouts.runners``), with the runner sized for the layout
(``layouts.sizing``); sizes and spacing are settled together.  Then the hard constraints decide:
inside the flask less its wall clearance, cope and drag room above and below, cavity-to-cavity,
cavity-to-sprue and runner-to-cavity clearances, and (off by default) riser keep-outs at the hot
spots.  A combination that fails is kept in ``rejected`` with its reasons.

Before anything is placed the combinations are counted; above the count threshold the phase stops
and says how many, so the engineer can narrow N, rotations, ingates, families or templates.  Layouts
equal under the flask's own symmetries (a turn of a half or, for a square flask, a quarter about its
centre; its mirror lines for a mirror-symmetric part) are generated once.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import box

from layouts import footprint as fp
from layouts import runners as rn
from layouts import sizing as sz

GEOMETRIC = ("runner.clear_of_cavities", "runner.no_crossing", "ingate.clear_of_other_cavities",
             "ingate.clear_of_runners", "ingate.clear_of_sprue", "sprue.clear_of_cavities")


class CountExceeded(Exception):
    def __init__(self, count: int, threshold: int, by: dict):
        super().__init__(f"{count} layout combinations to try, over the threshold of {threshold}: "
                         f"narrow N, rotations, ingates, families or templates ({by})")
        self.count, self.threshold, self.by = count, threshold, by


# ---- small geometry ---------------------------------------------------------------------------
def _ang(v) -> float:
    return math.degrees(math.atan2(v[1], v[0])) % 360


def _R(deg) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s], [s, c]])


def _cav(rot, T, mirrored=False, orbit=0) -> dict:
    return {"index": 0, "rotation_deg": float(round(rot % 360, 9)) % 360, "mirrored": bool(mirrored),
            "translation": [float(T[0]), float(T[1])], "orbit": orbit}


def half_turn(c: dict, centre) -> dict:
    T = 2 * np.asarray(centre, float) - np.asarray(c["translation"], float)
    return _cav(c["rotation_deg"] + 180, T, c["mirrored"], c["orbit"])


def quarter(c: dict, centre, k: int) -> dict:
    centre = np.asarray(centre, float)
    T = centre + _R(90 * k) @ (np.asarray(c["translation"], float) - centre)
    return _cav(c["rotation_deg"] + 90 * k, T, c["mirrored"], c["orbit"])


def reflect(c: dict, centre, axis_deg: float) -> dict:
    """The cavity mirrored across the line through centre at axis_deg: Ref R_r (F) = R_(2a - r + 180) (F)
    with the mirror flag flipped (F = the part's own x = 0 mirror)."""
    centre = np.asarray(centre, float)
    a = math.radians(axis_deg)
    ref = np.array([[math.cos(2 * a), math.sin(2 * a)], [math.sin(2 * a), -math.cos(2 * a)]])
    T = centre + ref @ (np.asarray(c["translation"], float) - centre)
    return _cav(2 * axis_deg - c["rotation_deg"] + 180, T, not c["mirrored"], c["orbit"])


# ---- the part ----------------------------------------------------------------------------------
def mirror_iou(field: dict) -> dict[str, float]:
    """IoU of the part's voxels with their mirror image in the upright planes x = cx and y = cy through
    its centroid (Phase 1 grid)."""
    metal = np.asarray(field["zone"]) > 0
    h, o = float(field["h"]), np.asarray(field["origin"], float)
    idx = np.argwhere(metal)
    cen = o + (idx.mean(0) + 0.5) * h
    out = {}
    for ax, name in ((0, "x"), (1, "y")):
        x = o[ax] + (np.arange(metal.shape[ax]) + 0.5) * h
        j = np.floor((2 * cen[ax] - x - o[ax]) / h).astype(int)
        ok = (j >= 0) & (j < metal.shape[ax])
        m2 = np.zeros_like(metal)
        sl_src = [slice(None)] * 3
        sl_dst = [slice(None)] * 3
        sl_dst[ax] = np.nonzero(ok)[0]
        sl_src[ax] = j[ok]
        m2[tuple(sl_dst)] = metal[tuple(sl_src)]
        out[name] = float((metal & m2).sum() / max((metal | m2).sum(), 1))
    return out


@dataclass
class Gate:
    id: str
    l: np.ndarray            # contact, parted part frame (unscaled), x y
    nu: np.ndarray           # outward normal


class Arranger:
    def __init__(self, job_data: dict, ctx: rn.Context, mirror_ok: bool):
        d = job_data
        self.d, self.ctx, self.mirror_ok = d, ctx, mirror_ok
        v = d["recipe"]["values"]
        self.cl = v["layout"]["clearance"]
        self.ext = float(v["layout"]["runner"]["extension_mm"])
        self.scale = ctx.scale
        lim = d["inputs"]["limits"]
        self.lim = lim
        c = d["ingate_candidates"]["candidates"]
        allow = set(lim.get("ingates") or [g["id"] for g in c]) - set(lim["disabled_ingates"])
        self.gates = [Gate(g["id"], np.array(g["location"][:2], float), np.array(g["normal"], float))
                      for g in c if g["id"] in allow]
        self.rotations = sorted(lim["rotations_deg"] or [0, 90, 180, 270])
        self.families = lim["families"] or ["row", "grid", "radial"]
        self.templates = lim.get("templates") or list(rn.TEMPLATES)
        st = d["recipe"]["settings"]["layouts"]
        self.search_tol = float(st.get("search_tolerance_mm", 0.05))
        tol = float(st.get("search_outline_tolerance_mm", 0.2))
        # conservative, simpler outlines for the search (they contain the exact ones)
        self.fast = rn.Context.__new__(rn.Context)
        self.fast.__dict__.update(ctx.__dict__)
        self.fast.outline = ctx.outline.simplify(tol).buffer(tol)
        self.fast.band = ctx.band.simplify(tol).buffer(tol) if ctx.band is not None else None
        fl = d["inputs"]["flask"]
        self.L, self.W = float(fl["length"]), float(fl["width"])
        self.inner = box(-self.L / 2, -self.W / 2, self.L / 2, self.W / 2).buffer(
            -float(self.cl["flask_wall_mm"]), join_style="mitre")
        ol = fp.place(ctx.outline, self.scale, 0, (0, 0))
        self.area = ol.area
        xy = _coords(ol)
        self.reach = {g.id: float(np.hypot(*(xy - g.l * self.scale).T).max()) for g in self.gates}
        S = d["inputs"]["sprue"]
        self.sprue = np.array(S["position"], float) if S["mode"] == "fixed" else np.zeros(2)

    # ---- representative placement ----
    def rep(self, g: Gate, r: float, q, mirrored=False, orbit=0) -> dict:
        rel = fp.place_point(g.l, self.scale, r, (0, 0), mirrored)
        return _cav(r, np.asarray(q, float) - rel, mirrored, orbit)

    def normal(self, g: Gate, r: float) -> np.ndarray:
        return _R(r) @ g.nu

    # ---- patterns ----
    def straight(self, g, r, k, p, D, mode):
        S = self.sprue
        n = self.normal(g, r)
        t = np.array([-n[1], n[0]])
        # one cavity a side (or alone) sits p / 2 along the runner, so its ingate clears the sprue
        if k == 0:                                           # one cavity beside the sprue
            cavs = [self.rep(g, r, S + p / 2 * t - D * n)]
            return cavs, {"per_side": 0, "pitch_mm": p, "runner_offset_mm": D, "runner_axis_deg": _ang(t)}, "c1"
        along = [p / 2] if k == 1 else [(j - (k - 1) / 2) * p for j in range(k)]
        reps = [self.rep(g, r, S + s_ * t - D * n, orbit=j) for j, s_ in enumerate(along)]
        if mode == "c2":
            imgs = [half_turn(c, S) for c in reps]
        else:
            imgs = [reflect(c, S, _ang(t)) for c in reps]
        arr = {"per_side": k, "pitch_mm": p, "runner_offset_mm": D, "runner_axis_deg": _ang(t)}
        if mode == "d1":
            arr["mirror_axes_deg"] = [_ang(t)]
        return reps + imgs, arr, mode

    def tree(self, g, r, m, lengths, b, D, feed):
        S = self.sprue
        n = self.normal(g, r)
        t = np.array([-n[1], n[0]])
        e_m = -n if feed == "end" else t
        perp = np.array([-e_m[1], e_m[0]])
        e = [e_m if (m - i) % 2 == 0 else perp for i in range(1, m + 1)]
        J = [S]
        for i in range(1, m):
            J.append(J[-1] + lengths[i - 1] * e[i - 1])
        foot = J[-1] + b * e[m - 1]
        cavs = [self.rep(g, r, foot - D * n)]
        for i in range(m, 0, -1):
            cavs = cavs + [half_turn(c, J[i - 1]) for c in cavs]
        for o, c in enumerate(cavs):
            c["orbit"] = o % (len(cavs) // 2)
        arr = {"levels": m, "arm_lengths_mm": list(lengths) + [b], "runner_offset_mm": D, "feed": feed,
               "split_axes_deg": [_ang(x) for x in e]}
        return cavs, arr, "c2"

    def radial(self, g, r, R):
        S = self.sprue
        n = self.normal(g, r)
        rep = self.rep(g, r, S - R * n)
        return [quarter(rep, S, k) for k in range(4)], {"radius_mm": R}, "c4"

    # ---- one layout dict ----
    def layout(self, family, template, g, r, cavs, arr, sym, lid="?") -> dict:
        cavs = [dict(c, index=i) for i, c in enumerate(cavs)]
        return {"id": lid, "n": len(cavs), "family": family, "arrangement": arr, "symmetry": sym,
                "rotation_deg": int(r), "ingate": g.id, "sprue": [float(x) for x in self.sprue],
                "template": template, "cavities": cavs, "key": ""}

    # ---- checks ----
    def geometric(self, lay: dict, sizing: dict, ctx) -> list[dict]:
        """Phase 3's spacing checks: cavities apart; runners, ingates, sprue clear (sizes held)."""
        out = []
        polys = [fp.place(ctx.outline, self.scale, c["rotation_deg"], c["translation"], c["mirrored"])
                 for c in lay["cavities"]]
        c_cc = float(self.cl["cavity_cavity_mm"])
        if len(polys) > 1:
            arr = np.array(polys, dtype=object)
            i, j = np.triu_indices(len(polys), 1)
            dd = shapely.distance(arr[i], arr[j])
            k = int(np.argmin(dd))
            if dd[k] < c_cc - 1e-6:
                out.append({"code": "cavity.clearance", "message": f"cavities {i[k]} and {j[k]} are "
                            f"{dd[k]:.2f} mm apart", "value": round(float(dd[k]), 3), "limit": c_cc})
        try:
            rec = rn.route(lay, ctx, sizing)
        except rn.Reject as e:
            return out + [{"code": e.code, "message": e.message}]
        for c in rec["validation"]:
            if c["code"] in GEOMETRIC and not c["passed"]:
                out.append({"code": c["code"], "message": c.get("message") or f"{c['code']} failed",
                            **({"value": c["value"]} if "value" in c else {}),
                            **({"limit": c["limit"]} if "limit" in c else {})})
        return out

    def hard(self, lay: dict, sizing: dict) -> list[dict]:
        """Everything Phase 3 rejects on, on the exact outlines."""
        out = self.geometric(lay, sizing, self.ctx)
        polys = [fp.place(self.ctx.outline, self.scale, c["rotation_deg"], c["translation"], c["mirrored"])
                 for c in lay["cavities"]]
        allc = shapely.union_all(polys)
        err = symmetry_error(lay, allc)
        tol = float(self.d["recipe"]["settings"]["layouts"]["hausdorff_tolerance_mm"])
        if err > tol:
            out.append({"code": "layout.not_symmetric", "message": "the cavities do not map onto themselves "
                        "under the layout's symmetry", "value": round(err, 6), "limit": tol})
        if not self.inner.contains(allc):
            over = allc.difference(self.inner)
            out.append({"code": "flask.wall", "message": "a cavity reaches the flask wall clearance",
                        "value": round(float(over.area), 3), "limit": float(self.cl["flask_wall_mm"])})
        if self.lim["riser_keepouts"]:
            out += self.keepouts(lay, sizing)
        return out

    def keepouts(self, lay, sizing) -> list[dict]:
        f = float(self.d["recipe"]["settings"]["layouts"].get("riser_keepout_radius_factor", 2.0))
        circles = []
        for c in lay["cavities"]:
            for hs in self.d["modulus"]["hot_spots"]:
                p = fp.place_point(hs["position"][:2], self.scale, c["rotation_deg"], c["translation"], c["mirrored"])
                circles.append(shapely.Point(p).buffer(f * hs["radius"]))
        try:
            rec = rn.route(lay, self.ctx, sizing)
        except rn.Reject:
            return []
        bands = []
        for s in rec["segments"]:
            pts = [x[:2] for x in s["points"]]
            if s["role"] == "sprue":
                sp = rec["sizing"]["sprue"]
                bands.append(shapely.Point(pts[0]).buffer(max(sp["top"]["dims"]["d"], sp["bottom"]["dims"]["d"]) / 2))
            else:
                w = max(s["section"]["dims"].get("width", 0), s["section"]["dims"].get("top", 0),
                        s["section"]["dims"].get("bottom", 0))
                bands.append(shapely.LineString(pts).buffer(w / 2, cap_style="flat"))
        hit = shapely.union_all(bands).intersects(shapely.union_all(circles))
        return [{"code": "riser.keepout", "message": "the gating crosses a riser keep-out"}] if hit else []

    # ---- sizes and spacing, settled together ----
    def sizing_for(self, lay: dict, sizing=None) -> dict:
        ctx = self.ctx
        zlo, zhi = ctx.z_range
        if sizing is None:                 # castings only: the first guess
            return sz.size(values=ctx.values, casting_volume_mm3=ctx.casting_volume, n=lay["n"], z_top=zhi,
                           z_bottom=zlo, thickest_mm=ctx.thickest, cope_height=float(ctx.flask["cope_height"]),
                           branches=rn.branches_of(lay["template"], lay["n"]))
        return rn.route(lay, ctx, settle_only=True)

    def d_min(self, sizing) -> float:
        r = sizing["_sizes"]["runners"][-1]
        return float(self.cl["runner_cavity_mm"]) + max(r.d["top"], r.d["bottom"]) / 2

    def place(self, family, template, g, r, build, x0: list[float], lo: list[float], order: list[int]):
        """Shrink the free lengths x (from x0, each down to lo) while the layout stays valid; sizes
        re-settled until they hold.  Returns (layout, sizing, problems)."""
        x = list(x0)
        lay = self.layout(family, template, g, r, *build(x))
        sizing = self.sizing_for(lay)
        for _ in range(4):
            lo_now = [f(sizing) if callable(f) else f for f in lo]
            x = [max(a, b) for a, b in zip(x, lo_now)]
            grow = 0
            while self.geometric(self.layout(family, template, g, r, *build(x)), sizing, self.fast):
                x = [v * 2 if i in order else v for i, v in enumerate(x)]
                grow += 1
                if grow > 5:
                    lay = self.layout(family, template, g, r, *build(x))
                    return lay, sizing, self.geometric(lay, sizing, self.ctx)
            for i in order:
                a, b = lo_now[i], x[i]
                trial = list(x)
                trial[i] = a
                if not self.geometric(self.layout(family, template, g, r, *build(trial)), sizing, self.fast):
                    x = trial
                    continue
                while b - a > self.search_tol:
                    mid = (a + b) / 2
                    trial[i] = mid
                    if self.geometric(self.layout(family, template, g, r, *build(trial)), sizing, self.fast):
                        a = mid
                    else:
                        b = mid
                x[i] = b
            lay = self.layout(family, template, g, r, *build(x))
            new = self.sizing_for(lay, sizing)
            w_old = sizing["_sizes"]["runners"][-1].d["bottom"]
            w_new = new["_sizes"]["runners"][-1].d["bottom"]
            sizing = new
            if abs(w_new - w_old) <= 1e-3:
                break
        lo_now = [f(sizing) if callable(f) else f for f in lo]
        x = [max(a, b) for a, b in zip(x, lo_now)]          # the settled sizes' own minimums
        lay = self.layout(family, template, g, r, *build(x))
        return lay, sizing, None

    # ---- the combinations ----
    def combos(self):
        """(family, template, gate, rotation, variant) in a fixed order, with N, before placing."""
        n_cap = max(int(self.inner.area // max(self.area, 1e-9)), 1)
        lo_n, hi_n = self.lim["n_min"] or 1, min(self.lim["n_max"] or n_cap, n_cap)
        for g in self.gates:
            for r in self.rotations:
                if "row" in self.families and "straight" in self.templates:
                    for mode in ("c2", "d1") if self.mirror_ok else ("c2",):
                        if mode == "c2" and lo_n <= 1 <= hi_n:
                            yield ("row", "straight", g, r, ("c1", 0), 1)
                        for k in range(1, hi_n // 2 + 1):
                            if 2 * k >= lo_n:
                                yield ("row", "straight", g, r, (mode, k), 2 * k)
                if "grid" in self.families:
                    m = 2
                    while 2 ** m <= hi_n:
                        tmpl = "h" if m == 2 else "tree"
                        if tmpl in self.templates and 2 ** m >= lo_n:
                            for feed in ("end", "side"):
                                yield ("grid", tmpl, g, r, (feed, m), 2 ** m)
                        m += 1
                if "radial" in self.families and "radial" in self.templates and lo_n <= 4 <= hi_n:
                    yield ("radial", "radial", g, r, ("c4", 4), 4)

    def count(self) -> tuple[int, dict]:
        by: dict[str, int] = {}
        n = 0
        for fam, tmpl, *_ in self.combos():
            n += 1
            by[fam] = by.get(fam, 0) + 1
        return n, by

    def generate(self):
        """Each combination placed and checked: yields (layout, sizing, problems or [])."""
        stopped: set = set()
        for fam, tmpl, g, r, (variant, size), n in self.combos():
            series = (fam, tmpl, g.id, r, variant if fam != "row" else variant.replace("c1", "c2"))
            if series in stopped:
                continue
            reach = self.reach[g.id]
            if fam == "row":
                mode, k = variant, size

                def build(x, g=g, r=r, k=k, mode=mode):
                    cavs, arr, sym = self.straight(g, r, k, x[0], x[1], "c2" if mode == "c1" else mode)
                    return cavs, arr, sym
                lay, sizing, prob = self.place(fam, tmpl, g, r, build,
                                               [2 * reach + 50, 2 * reach + 50], [0.0, self.d_min], [0, 1])
            elif fam == "grid":
                feed, m = variant, size
                base = 2 * reach + 50

                def build(x, g=g, r=r, m=m, feed=feed):
                    return self.tree(g, r, m, x[:m - 1], x[m - 1], x[m], feed)
                # each level longer than everything below it along its line, so no two subtrees meet
                x0 = [base * 3 ** (m - i) for i in range(1, m)] + [base, base]
                lo = [lambda s: self._w(s, 1)] * (m - 1) + [0.0, self.d_min]
                lay, sizing, prob = self.place(fam, tmpl, g, r, build, x0, lo, [m, m - 1] + list(range(m - 2, -1, -1)))
            else:
                def build(x, g=g, r=r):
                    return self.radial(g, r, x[0])
                lay, sizing, prob = self.place(fam, tmpl, g, r, build, [2 * reach + 50],
                                               [lambda s: self.d_min(s) + max(s["sprue"]["top"]["dims"]["d"], s["sprue"]["bottom"]["dims"]["d"]) / 2], [0])
            if prob is None:
                prob = self.hard(lay, sizing)
            yield lay, sizing, prob
            if any(p["code"] == "flask.wall" for p in prob) and fam != "radial":
                stopped.add(series)                 # more cavities of this series only grow

    def _w(self, sizing, level) -> float:
        r = sizing["_sizes"]["runners"][min(level, len(sizing["_sizes"]["runners"])) - 1]
        return max(r.d["top"], r.d["bottom"])

    # ---- sameness under the flask's own symmetries ----
    def points(self, lay: dict) -> np.ndarray:
        """Each cavity's placed centroid and ingate contact, (N, 4): what makes two layouts the same."""
        g = next(x for x in self.gates if x.id == lay["ingate"])
        cen = np.array(self.ctx.outline.centroid.coords[0])
        return np.array([np.r_[fp.place_point(cen, self.scale, c["rotation_deg"], c["translation"], c["mirrored"]),
                               fp.place_point(g.l, self.scale, c["rotation_deg"], c["translation"], c["mirrored"])]
                         for c in lay["cavities"]])

    def ops(self) -> list[np.ndarray]:
        """The flask's own symmetries about its centre."""
        ops = [np.eye(2), _R(180)]
        if abs(self.L - self.W) < 1e-9:
            ops += [_R(90), _R(270)]
        if self.mirror_ok:
            ops += [np.diag([1.0, -1.0]) @ o for o in list(ops)]
        return ops

    def same(self, a: dict, b: dict, tol: float) -> bool:
        """Equal under one of the flask's symmetries, every point within tol (the spacing search
        settles to its own tolerance, so equal layouts reached two ways differ by a little)."""
        if a["template"] != b["template"] or a["n"] != b["n"]:
            return False
        pa = self.points(a)
        Sa = np.asarray(a["sprue"], float)
        pb, Sb = self.points(b), np.asarray(b["sprue"], float)
        for M in self.ops():
            qb = np.c_[pb[:, :2] @ M.T, pb[:, 2:] @ M.T]
            if np.linalg.norm(M @ Sb - Sa) > tol:
                continue
            d = np.linalg.norm(pa[:, None] - qb[None], axis=2)
            if d.min(1).max() <= tol and d.min(0).max() <= tol:
                return True
        return False

    def key(self, lay: dict) -> str:
        g = next(x for x in self.gates if x.id == lay["ingate"])
        cen = np.array(self.ctx.outline.centroid.coords[0])
        pts = []
        for c in lay["cavities"]:
            a = fp.place_point(cen, self.scale, c["rotation_deg"], c["translation"], c["mirrored"])
            b = fp.place_point(g.l, self.scale, c["rotation_deg"], c["translation"], c["mirrored"])
            pts.append((a, b))
        S = np.asarray(lay["sprue"], float)
        ops = [np.eye(2), _R(180)]
        if abs(self.L - self.W) < 1e-9:
            ops += [_R(90), _R(270)]
        if self.mirror_ok:
            ops += [np.diag([1.0, -1.0]) @ o for o in list(ops)]
        best = None
        for M in ops:
            items = sorted(tuple(round(float(v), 2) + 0.0 for v in np.r_[M @ a, M @ b]) for a, b in pts)
            k = repr((lay["template"], tuple(round(float(v), 2) + 0.0 for v in M @ S), tuple(items)))
            best = k if best is None or k < best else best
        return best


def symmetry_error(lay: dict, cavities) -> float:
    """Hausdorff distance (outlines, densified) between the placed cavities and their image under each of
    the layout's symmetries about the sprue: turns for cN, and the mirror lines too for dN."""
    S = tuple(lay["sprue"])
    sym = lay["symmetry"]
    turns = {"c1": [], "c2": [180], "c4": [90, 180, 270], "d1": [], "d2": [180], "d4": [90, 180, 270]}[sym]
    imgs = [shapely.affinity.rotate(cavities, t, origin=S) for t in turns]
    if sym.startswith("d"):
        imgs += [rn.mirror(cavities, np.asarray(S), a) for a in lay["arrangement"].get("mirror_axes_deg", [])]
    return max((shapely.hausdorff_distance(i.boundary, cavities.boundary, densify=0.25) for i in imgs), default=0.0)


def _coords(g) -> np.ndarray:
    return np.vstack([np.asarray(p.exterior.coords) for p in getattr(g, "geoms", [g])])


# ---- the phase ----------------------------------------------------------------------------------
def height_problems(job_data, ctx: rn.Context) -> list[dict]:
    """Room above and below the cavity in the cope and the drag (the same for every layout)."""
    v = job_data["recipe"]["values"]["layout"]["clearance"]
    fl = job_data["inputs"]["flask"]
    zlo, zhi = (z * ctx.scale for z in ctx.z_range)
    out = []
    if max(zhi, 0.0) + float(v["cope_above_mm"]) > float(fl["cope_height"]) + 1e-9:
        out.append({"code": "flask.cope_height", "message": f"the cavity rises {zhi:.1f} mm into a "
                    f"{fl['cope_height']:g} mm cope; it needs {v['cope_above_mm']:g} mm of sand above",
                    "value": round(max(zhi, 0.0) + float(v["cope_above_mm"]), 3), "limit": float(fl["cope_height"])})
    if max(-zlo, 0.0) + float(v["drag_below_mm"]) > float(fl["drag_height"]) + 1e-9:
        out.append({"code": "flask.drag_height", "message": f"the cavity reaches {-zlo:.1f} mm into a "
                    f"{fl['drag_height']:g} mm drag; it needs {v['drag_below_mm']:g} mm of sand below",
                    "value": round(max(-zlo, 0.0) + float(v["drag_below_mm"]), 3), "limit": float(fl["drag_height"])})
    return out


def arranger(job, ctx: rn.Context | None = None, field: dict | None = None) -> Arranger:
    d = job.data
    ctx = ctx or rn.context(job)
    if field is None:
        field = dict(np.load(job.path.parent / d["modulus"]["field"]))
    iou = mirror_iou(field)
    ok = max(iou.values()) > float(d["recipe"]["settings"]["symmetry"]["mirror_iou_min"])
    a = Arranger(d, ctx, ok)
    a.iou = iou
    return a


def run(job, ctx: rn.Context | None = None, field: dict | None = None, progress=None) -> tuple[list, list]:
    """Phase 3 on a GatingJob: count, then place and check every combination.  ``progress(dict)``, when
    given, hears the count and then each layout as it is placed (accepted or rejected), for a UI to
    stream them."""
    from layouts.job import JobError

    if job.part_changed():
        raise JobError(["the part file changed since the job was made (GatingJob.refresh_part)"])
    job.start("layouts")
    t0 = time.perf_counter()
    try:
        arr = arranger(job, ctx, field)
        d = job.data
        heights = height_problems(d, arr.ctx)          # no layout can fit: nothing to count
        thr = d["inputs"]["limits"]["count_threshold"] or d["recipe"]["settings"]["layouts"]["count_threshold"]
        count, by = arr.count()
        if progress:
            progress({"counted": count, "by_family": by})
        if count > thr and not heights:
            raise CountExceeded(count, thr, by)
        layouts, rejected, seen, dupes, kept = [], [], set(), 0, []
        dup_tol = float(d["recipe"]["settings"]["layouts"].get("duplicate_tolerance_mm", 0.5))
        for lay, sizing, prob in ([] if heights else arr.generate()):
            lay["key"] = arr.key(lay)
            if lay["key"] in seen or any(arr.same(lay, o, dup_tol) for o in kept):
                dupes += 1
                continue
            seen.add(lay["key"])
            kept.append(lay)
            if prob:
                rejected.append({"id": f"r{len(rejected)}", "params": lay, "reasons": prob})
            else:
                lay["id"] = f"l{len(layouts)}"
                layouts.append(lay)
            if progress:
                progress({"placed": len(layouts), "rejected": len(rejected), "duplicates": dupes,
                          "layout": lay if not prob else None})
        if heights:
            rejected.append({"id": "r0", "params": {"n": None, "why": "every layout"}, "reasons": heights})
        issues = [f"part mirror IoU x {arr.iou['x']:.4f}, y {arr.iou['y']:.4f}: "
                  f"{'mirror-symmetric, mirrored layouts offered' if arr.mirror_ok else 'not mirror-symmetric'}"]
    except CountExceeded as e:
        job.fail("layouts", str(e))
        raise
    except Exception as e:                                          # noqa: BLE001
        job.fail("layouts", f"{type(e).__name__}: {e}")
        raise
    job.finish("layouts", layouts, rejected=rejected, runtime_s=round(time.perf_counter() - t0, 3),
               counts={"combinations": count, "layouts": len(layouts), "rejected": len(rejected),
                       "duplicates": dupes},
               issues=issues)
    return layouts, rejected
