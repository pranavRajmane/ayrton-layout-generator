"""Phase 4: route sprue -> runners -> ingates for a layout, symmetric by construction, and check it.

Each cavity k has its ingate contact q_k and outward normal n_k (the layout's ingate candidate, carried
by the cavity's transform).  The runner meets the ingate at the *foot* f_k = q_k + d n_k.  For a tree or radial runner
d = runner-to-cavity clearance + half the runner's width: the runner's edge is exactly the clearance
off the contact, and the ingate bridges it.  The templates join the sprue S to the feet:

  straight  one runner through S at right angles to the ingates; each ingate runs from its contact
            to it (at least that d long); it runs on past the outermost feet by the recipe's
            extension.  2 arms.
  radial    a straight arm from S to each foot, the ingate continuing it.  N arms.
  h, tree   balanced binary splits: the feet split into two halves that are each other's 180-degree
            turn about the junction (S first); each half's centre is the next junction.  Arms to the
            junctions, then on down; a leaf arm ends at its foot, and the ingate either continues it
            or leaves its side (then it runs on by the extension).  h is the two-level tree (N = 4).

Only the representative's route is designed: the others are its images under the splits'
half-turns, so path lengths and bend sequences match by construction; the checks confirm it.

Sizing (``layouts.sizing``) and routing depend on each other (the runner's width sets the feet, the
runners' length sets the poured weight), so the route is rebuilt inside the sizing iteration.

Checks per layout, each a ``check`` record; a failed one rejects the layout with its reason:
path lengths within ``path_length_tolerance_pct``; runners clear of every cavity's metal in the
runner's half by the clearance, of each other, and of the flask wall; the sprue clear of the cavities;
the system mapped onto itself by the layout's symmetry (Hausdorff < tolerance); areas on the ratio
within ``area_ratio_tolerance_pct``; each resized ingate still thinner (modulus) than its zone.
"""
from __future__ import annotations

import math
import time

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from layouts import footprint as fp
from layouts import sizing as sz

TEMPLATES = ("straight", "h", "tree", "radial")
ANGLE_TOL = 1e-3          # radians: "parallel" / "at right angles"


class Reject(Exception):
    def __init__(self, code: str, message: str, value=None, limit=None):
        super().__init__(message)
        self.code, self.message, self.value, self.limit = code, message, value, limit


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _rot(p, centre, deg) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    d = np.asarray(p, float) - centre
    return centre + np.array([c * d[0] - s * d[1], s * d[0] + c * d[1]])


# ---- the skeleton ----------------------------------------------------------------------------
class Skeleton:
    """Runner arms as (level, a, b, leaf cavity or None); a leaf arm ends at its foot."""

    def __init__(self):
        self.arms: list[tuple[int, np.ndarray, np.ndarray, int | None]] = []
        self.junctions: list[tuple[np.ndarray, int, list[int]]] = []    # position, arm in (-1: sprue), arms out

    def arm(self, level, a, b, leaf=None) -> int:
        self.arms.append((level, np.asarray(a, float), np.asarray(b, float), leaf))
        return len(self.arms) - 1


def straight_feet(S, q, normals, d_min) -> tuple[list[np.ndarray], list[float]]:
    """A straight runner lies on the line through S square to the ingates; each ingate runs from its
    contact to that line (at least d_min long)."""
    n0 = normals[0]
    if any(abs(abs(float(n @ n0)) - 1) > ANGLE_TOL for n in normals):
        raise Reject("runner.ingates_not_parallel", "a straight runner needs every ingate square to it")
    feet, ds = [], []
    for k, (qk, nk) in enumerate(zip(q, normals)):
        d = float((S - qk) @ nk)
        if d < d_min - 1e-6:
            raise Reject("runner.too_close", f"cavity {k}'s contact is {d:.1f} mm from the runner line; it needs "
                         f"{d_min:.1f} (clearance + half the runner)", value=round(d, 3), limit=round(d_min, 3))
        feet.append(qk + d * nk)
        ds.append(d)
    return feet, ds


def _straight(S, feet, normals) -> tuple[Skeleton, list[int]]:
    n0 = normals[0]
    t = np.array([-n0[1], n0[0]])
    s = [float((f - S) @ t) for f in feet]
    sk = Skeleton()
    out = []
    for sign in (1, -1):
        ends = [x for x in s if sign * x > 1e-9]
        reach = max((abs(x) for x in ends), default=0.0)
        if reach > 0 or any(abs(x) <= 1e-9 for x in s):
            out.append(sk.arm(1, S, S + sign * reach * t))
    sk.junctions.append((S, -1, out))
    return sk, [1] * len(feet)


def _radial(S, feet, normals) -> tuple[Skeleton, list[int]]:
    sk = Skeleton()
    out = []
    for k, (f, n) in enumerate(zip(feet, normals)):
        d = f - S
        if np.linalg.norm(d) < 1e-6 or abs(float(_unit(d) @ -n) - 1) > ANGLE_TOL:
            raise Reject("runner.ingate_not_facing_sprue", f"cavity {k}'s ingate does not face the sprue")
        out.append(sk.arm(1, S, f, k))
    sk.junctions.append((S, -1, out))
    return sk, [1] * len(feet)


def _tree(S, feet, normals, tol) -> tuple[Skeleton, list[int]]:
    sk = Skeleton()
    depth = [0] * len(feet)

    def split(J, idx, level, parent):
        if len(idx) == 1:
            k = idx[0]
            if parent is not None and np.linalg.norm(feet[k] - J) < 1e-9:
                return
            sk.arm(level, J, feet[k], k)
            depth[k] = level
            return
        P = np.array([feet[i] - J for i in idx])
        w, V = np.linalg.eigh(P.T @ P)
        axis = V[:, int(np.argmax(w))]
        axis = axis if (axis[0] > 1e-12 or (abs(axis[0]) <= 1e-12 and axis[1] > 0)) else -axis
        proj = P @ axis
        if np.min(np.abs(proj)) < 1e-6:
            raise Reject("runner.unbalanced", "a split of the runner tree has a foot on its axis")
        g1 = [i for i, x in zip(idx, proj) if x > 0]
        g2 = [i for i, x in zip(idx, proj) if x < 0]
        if len(g1) != len(g2):
            raise Reject("runner.unbalanced", "a split of the runner tree is not even",
                         value=[len(g1), len(g2)])
        a = np.array([feet[i] for i in g1])
        b = np.array([_rot(feet[i], J, 180) for i in g2])
        dist = np.linalg.norm(a[:, None] - b[None], axis=2).min(1).max()
        if dist > tol:
            raise Reject("runner.unbalanced", "the two halves of a split are not each other's half-turn",
                         value=float(dist), limit=tol)
        J1 = a.mean(0) if len(g1) > 1 else feet[g1[0]]
        J2 = 2 * J - J1
        arms = []
        for Jc, g in ((J1, g1), (J2, g2)):
            leaf = g[0] if len(g) == 1 else None
            arms.append(sk.arm(level, J, Jc, leaf))
            if leaf is not None:
                depth[leaf] = level
        sk.junctions.append((J, parent, arms))
        for Jc, g, arm_id in zip((J1, J2), (g1, g2), arms):
            if len(g) > 1:
                split(Jc, g, level + 1, arm_id)

    split(S, list(range(len(feet))), 1, -1)
    return sk, depth


def branches_of(template: str, n: int) -> list[int]:
    """Branches at each level below the sprue (for the area split)."""
    if template == "straight":
        return [2] if n > 1 else [1]
    if template == "radial":
        return [n]
    levels = max(int(round(math.log2(n))), 1)
    if 2 ** levels != n:
        raise Reject("runner.tree_count", f"a {template} runner feeds 2, 4, 8 ... cavities, not {n}", value=n)
    if template == "h" and n != 4:
        raise Reject("runner.h_count", f"an H runner feeds 4 cavities, not {n}", value=n)
    return [2 ** k for k in range(1, levels + 1)]


# ---- one layout ------------------------------------------------------------------------------
class Context:
    """What routing a layout needs besides the layout itself."""

    def __init__(self, job_data: dict, outline, band_outline, casting_volume: float, z_range):
        d = job_data
        self.values = d["recipe"]["values"]
        self.settings = d["recipe"]["settings"]["runners"]
        self.flask = d["inputs"]["flask"]
        self.cands = {c["id"]: c for c in d["ingate_candidates"]["candidates"]}
        self.zones = {z["id"]: z for z in d["modulus"]["zones"]}
        hs = d["modulus"]["hot_spots"]
        self.thickest = 2 * max((h["radius"] for h in hs), default=d["modulus"]["min_wall"] / 2)
        self.outline, self.band = outline, band_outline
        self.casting_volume = casting_volume
        self.z_range = z_range
        self.scale = 1 + float(self.values["shrinkage_pct"]) / 100


def route(layout: dict, ctx: Context) -> dict:
    """The runner record for a layout (``runners`` item), or raise Reject."""
    v = ctx.values
    lay = v["layout"]
    cl = lay["clearance"]
    template = layout["template"]
    n = layout["n"]
    S = np.asarray(layout["sprue"], float)
    cand = ctx.cands[layout["ingate"]]
    cavs = sorted(layout["cavities"], key=lambda c: c["index"])
    q = [fp.place_point(cand["location"], ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"]) for c in cavs]
    nrm = [_unit(fp.place_point(cand["normal"], 1.0, c["rotation_deg"], (0, 0), c["mirrored"])) for c in cavs]
    branches = branches_of(template, n)
    tol = float(ctx.settings["hausdorff_tolerance_mm"])
    ext = float(lay["runner"]["extension_mm"])

    def geometry(sizes):
        w = max(sizes["runners"][-1].d["top"], sizes["runners"][-1].d["bottom"])
        d_min = float(cl["runner_cavity_mm"]) + w / 2
        ds = [d_min] * n
        if template == "straight":
            feet, ds = straight_feet(S, q, nrm, d_min)
        else:
            feet = [qk + d_min * nk for qk, nk in zip(q, nrm)]
        if template == "straight":
            sk, depth = _straight(S, feet, nrm)
        elif template == "radial":
            sk, depth = _radial(S, feet, nrm)
        else:
            sk, depth = _tree(S, feet, nrm, tol)
        # leaf arms: does the ingate continue the arm (end-fed) or leave its side (tap: run on)?
        arms = [list(a) for a in sk.arms]
        for i, (lv, a, b, leaf) in enumerate(sk.arms):
            if leaf is None or np.linalg.norm(b - a) < 1e-9:
                continue
            u = _unit(b - a)
            c = float(u @ -nrm[leaf])
            if abs(c - 1) <= ANGLE_TOL:
                continue
            if abs(c) <= ANGLE_TOL:
                arms[i][2] = b + ext * u
                continue
            raise Reject("runner.ingate_angle", f"cavity {leaf}'s ingate meets its runner at "
                         f"{math.degrees(math.acos(max(-1, min(1, c)))):.1f} degrees (0 or 90 only)")
        if template == "straight":                     # the straight runner runs on past its outer feet
            for i, (lv, a, b, _) in enumerate(arms):
                L = np.linalg.norm(b - a)
                u = _unit(b - a) if L > 1e-9 else np.array([-nrm[0][1], nrm[0][0]]) * (1 if i == 0 else -1)
                arms[i][2] = b + ext * u
        return feet, ds, sk, arms, depth

    def gating_volume(sizes):
        feet, ds, sk, arms, _ = geometry(sizes)
        vol = sizes["sprue_length"] / 3 * (sizes["sprue_top"].area + sizes["sprue_bottom"].area
                                            + math.sqrt(sizes["sprue_top"].area * sizes["sprue_bottom"].area))
        vol += sum(np.linalg.norm(b - a) * sizes["runners"][lv - 1].area for lv, a, b, _ in arms)
        vol += sum(ds) * sizes["ingate"].area
        return vol

    zlo, zhi = ctx.z_range
    sizing = sz.size(values=v, casting_volume_mm3=ctx.casting_volume, n=n, z_top=zhi, z_bottom=zlo,
                     thickest_mm=ctx.thickest, cope_height=float(ctx.flask["cope_height"]), branches=branches,
                     gating_volume=gating_volume)
    sizes = sizing.pop("_sizes")
    feet, ds, sk, arms, depth = geometry(sizes)
    checks: list[dict] = []

    def check(code, ok, value=None, limit=None, message=None):
        rec = {"code": code, "passed": bool(ok)}
        if value is not None:
            rec["value"] = value
        if limit is not None:
            rec["limit"] = limit
        if message:
            rec["message"] = message
        checks.append(rec)
        return ok

    # path lengths: along the arms from S to each foot, then the ingate
    parents = {}
    for J, pin, outs in sk.junctions:
        for o in outs:
            parents[o] = pin
    lengths = []
    for k in range(n):
        leaf_arm = next((i for i, a in enumerate(sk.arms) if a[3] == k), None)
        if leaf_arm is None:                                   # straight: a tap on the line
            L = float(np.linalg.norm(feet[k] - S))
        else:
            L, i = 0.0, leaf_arm
            while i is not None and i >= 0:
                L += float(np.linalg.norm(sk.arms[i][2] - sk.arms[i][1]))
                i = parents.get(i)
        lengths.append(L + ds[k])
    err = 100 * (max(lengths) - min(lengths)) / max(min(lengths), 1e-9)
    check("runner.path_lengths", err <= ctx.settings["path_length_tolerance_pct"], round(err, 6),
          ctx.settings["path_length_tolerance_pct"])

    # geometry in plan: runner bands, ingates, sprue, cavities
    rsec = sizes["runners"]
    bands = [LineString([a, b]).buffer(max(rsec[lv - 1].d["top"], rsec[lv - 1].d["bottom"]) / 2, cap_style="flat")
             for lv, a, b, _ in arms if np.linalg.norm(b - a) > 1e-9]
    cav_band = [fp.place(ctx.band, ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"]) if ctx.band is not None
                else None for c in cavs]
    cav_all = [fp.place(ctx.outline, ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"]) for c in cavs]
    c_rc = float(cl["runner_cavity_mm"])
    hit = []
    for i, bnd in enumerate(bands):
        for k, cb in enumerate(cav_band):
            if cb is not None and bnd.distance(cb) < c_rc - 1e-6:
                hit.append((i, k, round(bnd.distance(cb), 3)))
    check("runner.clear_of_cavities", not hit, [h[2] for h in hit][:1] or None, c_rc,
          f"runner arm {hit[0][0]} is {hit[0][2]} mm from cavity {hit[0][1]}" if hit else None)

    cross = []
    for i in range(len(bands)):
        for j in range(i + 1, len(bands)):
            ai, aj = arms[i], arms[j]
            touching = min(np.linalg.norm(p - r) for p in (ai[1], ai[2]) for r in (aj[1], aj[2])) < 1e-6
            if not touching and bands[i].intersects(bands[j]):
                cross.append((i, j))
    check("runner.no_crossing", not cross, len(cross), 0)

    gates = []
    w_i, t_i = sizes["ingate"].d["width"], sizes["ingate"].d["height"]
    for k in range(n):
        gates.append(LineString([feet[k], q[k]]).buffer(w_i / 2, cap_style="flat"))
    g_hit = [(k, m) for k in range(n) for m in range(n) if m != k and cav_band[m] is not None
             and gates[k].intersects(cav_band[m])]
    check("ingate.clear_of_other_cavities", not g_hit, len(g_hit), 0)

    r_sprue = sizes["sprue_top"].d["d"] / 2
    sp = Point(S).buffer(r_sprue)
    gap = min(sp.distance(c) for c in cav_all)
    check("sprue.clear_of_cavities", gap >= float(cl["cavity_sprue_mm"]) - 1e-6, round(gap, 3),
          float(cl["cavity_sprue_mm"]))

    L, Wf = float(ctx.flask["length"]), float(ctx.flask["width"])
    inner = box(-L / 2, -Wf / 2, L / 2, Wf / 2).buffer(-float(cl["flask_wall_mm"]), join_style="mitre")
    system = unary_union(bands + gates + [sp])
    check("runner.inside_flask", inner.contains(system), None, float(cl["flask_wall_mm"]))

    # symmetry: the system onto itself
    sym = layout["symmetry"]
    turns = {"c1": [], "c2": [180], "c4": [90, 180, 270], "d1": [], "d2": [180], "d4": [90, 180, 270]}[sym]
    worst = 0.0
    for deg in turns:
        img = affinity.rotate(system, deg, origin=tuple(S))
        worst = max(worst, img.hausdorff_distance(system))
    check("runner.symmetric", worst <= tol, round(worst, 6), tol)

    # areas on the ratio
    a_s, a_r, a_i = sizing["sprue_area_mm2"], sizing["runner_area_mm2"], sizing["ingate_area_mm2"]
    built = [sizes["sprue_bottom"].area, max(b * s.area for b, s in zip(branches, rsec)), n * sizes["ingate"].area]
    worst_pct = max(abs(b / r - 1) * 100 for b, r in zip(built, (a_s, a_r, a_i)))
    check("gating.ratio", worst_pct <= ctx.settings["area_ratio_tolerance_pct"], round(worst_pct, 6),
          ctx.settings["area_ratio_tolerance_pct"])

    # the resized ingate must still freeze first
    m_g = sizes["ingate"].area / sizes["ingate"].perimeter
    m_z = ctx.zones[cand["target_zone"]]["modulus"]
    check("ingate.freezes_first", m_g < m_z, round(m_g, 6), m_z)

    # the record
    segs = [{"id": "sprue", "role": "sprue", "kind": "polyline",
             "points": [[*S, float(ctx.flask["cope_height"])], [*S, 0.0]],
             "section": sz.section_dict(sizes["sprue_bottom"]), "cavity": None}]
    z_r = -1.0 if lay["runner"]["side"] == "drag" else 1.0
    for i, (lv, a, b, leaf) in enumerate(arms):
        segs.append({"id": f"r{i}", "role": "runner", "kind": "polyline", "points": [[*a, 0.0], [*b, 0.0]],
                     "section": sz.section_dict(rsec[lv - 1]), "cavity": leaf})
    for k in range(n):
        segs.append({"id": f"g{k}", "role": "ingate", "kind": "polyline", "points": [[*feet[k], 0.0], [*q[k], 0.0]],
                     "section": sz.section_dict(sizes["ingate"]), "cavity": k})
    junctions = []
    for j, (J, pin, outs) in enumerate(sk.junctions):
        if len(outs) >= 2:
            junctions.append({"id": f"j{j}", "kind": "T", "position": [*J, 0.0],
                              "in": "sprue" if pin == -1 else f"r{pin}", "out": [f"r{o}" for o in outs]})
    sizing = _round(sizing)
    sizing["runner_side_sign"] = z_r
    sizing["path_lengths_mm"] = [round(x, 6) for x in lengths]
    return {"layout": layout["id"], "template": template, "segments": _round(segs), "junctions": _round(junctions),
            "sizing": sizing, "validation": checks}


def _round(x):
    if isinstance(x, (float, np.floating)):
        return round(float(x), 6)
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    return x


# ---- the phase ---------------------------------------------------------------------------------
def context(job) -> Context:
    from layouts.modulus import part_in_mold

    d = job.data
    shape = part_in_mold(job)
    side = d["recipe"]["values"]["layout"]["runner"]["side"]
    defl = float(d["recipe"]["settings"]["runners"].get("footprint_deflection_mm", 0.05))
    outline = fp.footprint(shape, deflection=defl)
    band = fp.footprint(shape, None, 0.0, deflection=defl) if side == "drag" else fp.footprint(shape, 0.0, None,
                                                                                               deflection=defl)
    bb = shape.bounding_box()
    return Context(d, outline, band, float(shape.volume), (bb.min.Z, bb.max.Z))


def run(job, ctx: Context | None = None) -> tuple[list, list]:
    """Phase 4 on a GatingJob: route every layout; failed checks reject it."""
    from layouts.job import JobError

    if job.part_changed():
        raise JobError(["the part file changed since the job was made (GatingJob.refresh_part)"])
    job.start("runners")
    t0 = time.perf_counter()
    try:
        ctx = ctx or context(job)
        out, rejected = [], []
        for lay in job.data["layouts"]:
            try:
                rec = route(lay, ctx)
            except Reject as r:
                rejected.append({"id": lay["id"], "params": lay, "reasons": [_reason(r)]})
                continue
            bad = [c for c in rec["validation"] if not c["passed"]]
            if bad:
                rejected.append({"id": lay["id"], "params": lay, "reasons": [
                    {"code": c["code"], "message": c.get("message") or f"{c['code']} failed",
                     **({"value": c["value"]} if "value" in c else {}), **({"limit": c["limit"]} if "limit" in c else {})}
                    for c in bad]})
            else:
                out.append(rec)
    except Exception as e:                                          # noqa: BLE001
        job.fail("runners", f"{type(e).__name__}: {e}")
        raise
    job.finish("runners", out, rejected=rejected, runtime_s=round(time.perf_counter() - t0, 3),
               counts={"routed": len(out), "rejected": len(rejected)})
    return out, rejected


def _reason(r: Reject) -> dict:
    rec = {"code": r.code, "message": r.message}
    if r.value is not None:
        rec["value"] = r.value
    if r.limit is not None:
        rec["limit"] = r.limit
    return rec
