"""The rule-based seed generator: many varied starting designs for one part.

    generate_seeds(part, feeding_graph, foundry_profile, n_designs, seed) -> list[Design]

A Design is a JSON-serialisable parametric spec, not geometry (``seeds.build``
turns one into a CAD-module design document).  Each design draws every knob
from its range with ``numpy.random.default_rng([seed, attempt])``, so the same
seed gives the same designs and every design records the knob values and the
random choices that made it.

Order of the rules, per design:
  1. cavity layout: rows x cols drawn among the arrangements that hold the
     cavity count, cavity_gap_mm apart, centred in the flask;
  2. shared feeders: each pair of hot spots that a common place reaches within
     both feeding distances is merged with probability merge_shared_risers;
  3. each feeder: its candidates (riser_style, ranked; attachment_choice picks
     one), sized by the modulus and volume checks; a candidate it does not fit
     passes to the next;
  4. gating: choke area from the pour mass, filling time and effective head;
     the gating ratio gives the sprue, runner and ingate areas; the sprue at a
     random free place; ingates placed by ingate_placement; one runner from the
     sprue to each ingate, on the parting plane;
  5. validity (seeds.validity): rejected designs are kept with their reasons.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from core.workflow import TAPER_PCT, runner_widths
from calculators.hydraulics import choke_area, effective_head, ratio_areas, sprue_top_area
from seeds import validity
from seeds.attach import Candidate, Places
from seeds.config import ConfigError, FoundryProfile, load_knobs, sample_knobs
from seeds.feeding import FeedingGraph
from seeds.moldmap import MoldMap, segment_distance
from seeds.report import diversity, print_report
from seeds.sizing import size_neck, size_riser
from seeds.voxel import VoxelPart


class Infeasible(Exception):
    """The rules could not complete this design (not a validity rejection)."""


@dataclass
class SeedRun:
    designs: list[dict]
    rejected: list[dict]
    report: dict
    attempts: int


@dataclass
class HotSpot:
    id: str                  # c<k>.<zone>
    cavity: int
    zone: str
    centre: np.ndarray       # mold frame
    modulus: float
    thickness: float
    fed_volume: float
    feeding_distance: float


@dataclass
class Ctx:
    """Everything one design is built against."""
    part: VoxelPart
    graph: FeedingGraph
    places: Places
    profile: FoundryProfile
    kv: dict
    rng: np.random.Generator
    offsets: list = field(default_factory=list)
    mold: MoldMap | None = None
    hot: dict[str, HotSpot] = field(default_factory=dict)
    decisions: list = field(default_factory=list)


# ---- public ------------------------------------------------------------------------

def generate_seeds(part: VoxelPart, feeding_graph: FeedingGraph, foundry_profile, n_designs: int, seed: int,
                   knobs=None, report: bool = True, **kw) -> list[dict]:
    """The valid designs (up to n_designs); prints the diversity report unless report=False."""
    run = generate(part, feeding_graph, foundry_profile, n_designs, seed, knobs, **kw)
    if report:
        print_report(run.report)
    return run.designs


def generate(part: VoxelPart, feeding_graph: FeedingGraph, foundry_profile, n_designs: int, seed: int,
             knobs=None, max_attempts: int | None = None, places: Places | None = None) -> SeedRun:
    knobs = load_knobs(knobs)
    lo, hi = knobs["runner_taper_pct"]["range"]
    if lo < TAPER_PCT[0] or hi > TAPER_PCT[1]:
        raise ConfigError([f"knob runner_taper_pct: range {[lo, hi]} goes outside {list(TAPER_PCT)} %"])
    profile = foundry_profile if isinstance(foundry_profile, FoundryProfile) else \
        FoundryProfile.from_dict(foundry_profile)
    places = places or Places(part)
    max_attempts = max_attempts or 20 * n_designs
    designs, rejected = [], []
    a = 0
    while len(designs) < n_designs and a < max_attempts:
        rng = np.random.default_rng([int(seed), a])
        kv = sample_knobs(knobs, rng)
        ctx = Ctx(part, feeding_graph, places, profile, kv, rng)
        did = f"{part.part_id}-s{seed}-a{a}"
        try:
            d = _design(ctx, did, seed, a)
        except Infeasible as e:
            rejected.append({"id": did, "attempt": a, "reasons": [f"generate.{e.args[0]}"],
                             "detail": e.args[1] if len(e.args) > 1 else "", "knob_values": kv})
            a += 1
            continue
        reasons = validity.check(d, ctx)
        if reasons:
            rejected.append({"id": did, "attempt": a, "reasons": sorted({r for r, _ in reasons}),
                             "detail": "; ".join(m for _, m in reasons), "knob_values": kv})
        else:
            designs.append(d)
        a += 1
    return SeedRun(designs, rejected, diversity(designs, rejected), a)


# ---- one design ----------------------------------------------------------------------

def _design(ctx: Ctx, did: str, seed: int, attempt: int) -> dict:
    layout = _layout(ctx)
    _hot_spots(ctx)
    elements: list[dict] = []
    risers: list[dict] = []
    for group in _groups(ctx):
        risers += _feeder(ctx, group, elements)
    _gating(ctx, elements)
    return {"id": did, "part_id": ctx.part.part_id, "cavity_count": ctx.profile.cavity_count,
            "cavity_layout": layout, "elements": elements,
            "provenance": {"generator": "rules", "seed": int(seed), "attempt": int(attempt),
                           "knob_values": dict(ctx.kv), "decisions": ctx.decisions}}


def _layout(ctx: Ctx) -> dict:
    p, vp = ctx.profile, ctx.part
    n = p.cavity_count
    lo, hi = vp.bbox()
    size, c0 = hi - lo, (lo + hi) / 2
    opts = sorted({(math.ceil(n / cols), cols) for cols in range(1, n + 1)})
    rows, cols = opts[int(ctx.rng.integers(len(opts)))]
    gap = ctx.kv["cavity_gap_mm"]
    pitch = size[:2] + gap
    offsets = []
    for k in range(n):
        r, c = divmod(k, cols)
        last_row = min(cols, n - r * cols)            # a short last row is centred too
        x = (c - (last_row - 1) / 2) * pitch[0]
        y = (r - (rows - 1) / 2) * pitch[1]
        offsets.append([float(x - c0[0]), float(y - c0[1]), 0.0])
    ctx.offsets = offsets
    ctx.mold = MoldMap(vp, offsets, p.flask_lo, p.flask_hi)
    ctx.decisions.append({"step": "layout", "rows": rows, "cols": cols, "options": [list(o) for o in opts]})
    return {"rows": rows, "cols": cols, "pitch_mm": [float(x) for x in pitch], "offsets": offsets}


def _hot_spots(ctx: Ctx) -> None:
    g = ctx.graph
    fdf = ctx.kv["feeding_distance_factor"]
    for k, off in enumerate(ctx.offsets):
        for z in g.hot_spots():
            zone = g.zones[z]
            hid = f"c{k}.{z}"
            ctx.hot[hid] = HotSpot(hid, k, z, np.asarray(zone.centre) + off, zone.modulus_mm, zone.thickness_mm,
                                   g.fed_volume(z), fdf * zone.thickness_mm)


# ---- feeders ---------------------------------------------------------------------------

@dataclass
class Option:
    """A place one feeder could go: one candidate (one hot spot or a pair on one casting), or two
    facing side points on neighbouring cavities."""
    style: str
    cavity: int
    cand: Candidate | None = None           # in the part frame
    pair: tuple | None = None               # cross-cavity: (point a, normal a, room a, point b, normal b, room b, cavity b)
    distance: float = 0.0


def _shift(c: Candidate, off) -> np.ndarray:
    return np.asarray(c.point) + np.asarray(off)


def _options_single(ctx: Ctx, hs: HotSpot) -> list[Option]:
    part_centre = hs.centre - ctx.offsets[hs.cavity]
    cands = ctx.places.candidates(part_centre, limit=hs.feeding_distance)
    return [Option(c.style, hs.cavity, c, distance=c.distance) for c in cands]


def _options_shared(ctx: Ctx, a: HotSpot, b: HotSpot) -> list[Option]:
    out = []
    if a.cavity == b.cavity:
        off = np.asarray(ctx.offsets[a.cavity])
        ca, cb = a.centre - off, b.centre - off
        mid = (ca + cb) / 2
        for c in ctx.places.candidates(mid):
            p = np.asarray(c.point)
            da, db = float(np.linalg.norm(p - ca)), float(np.linalg.norm(p - cb))
            if da <= a.feeding_distance and db <= b.feeding_distance:
                out.append(Option(c.style, a.cavity, c, distance=max(da, db)))
    else:
        oa, ob = np.asarray(ctx.offsets[a.cavity]), np.asarray(ctx.offsets[b.cavity])
        pts = ctx.places.side_points
        P = np.array([q[0] for q in pts])
        N = np.array([q[1] for q in pts])
        A, B = P + oa[:2], P + ob[:2]
        V = B[None, :, :] - A[:, None, :]
        gap = np.linalg.norm(V, axis=2)
        facing = (N @ N.T < -0.95) & (gap > 1e-6) & ((V * N[:, None, :]).sum(2) > 0.95 * gap)
        M = (A[:, None, :] + B[None, :, :]) / 2
        M3 = np.concatenate([M, np.zeros(M.shape[:2] + (1,))], axis=2)
        dA = np.linalg.norm(M3 - a.centre, axis=2)
        dB = np.linalg.norm(M3 - b.centre, axis=2)
        ok = facing & (dA <= a.feeding_distance) & (dB <= b.feeding_distance)
        for i, j in np.argwhere(ok):
            out.append(Option("side", a.cavity, None,
                              (np.append(A[i], 0.0), N[i], {"cope_mm": pts[i][2], "drag_mm": pts[i][3]},
                               np.append(B[j], 0.0), N[j], {"cope_mm": pts[j][2], "drag_mm": pts[j][3]}, b.cavity),
                              float(max(dA[i, j], dB[i, j]))))
    out.sort(key=lambda o: (not (o.cand.cope if o.cand else True), o.distance))
    return out


def _neighbours(ctx: Ctx, k: int, m: int) -> bool:
    lay = ctx.decisions[0]
    cols = lay["cols"]
    rk, ck = divmod(k, cols)
    rm, cm = divmod(m, cols)
    return abs(rk - rm) + abs(ck - cm) == 1


def _groups(ctx: Ctx) -> list[dict]:
    """The hot spots, singly or in shared pairs, each with its ordered options."""
    style = ctx.kv["riser_style"]
    pairs = []
    for a, b in combinations(sorted(ctx.hot), 2):
        ha, hb = ctx.hot[a], ctx.hot[b]
        if ha.cavity != hb.cavity and (style == "top" or not _neighbours(ctx, ha.cavity, hb.cavity)):
            continue
        opts = _styled(ctx, _options_shared(ctx, ha, hb), shared=True)
        if opts:
            pairs.append((a, b, opts))
    taken: set[str] = set()
    groups = []
    order = ctx.rng.permutation(len(pairs)) if pairs else []
    for i in order:
        a, b, opts = pairs[int(i)]
        if a in taken or b in taken:
            continue
        u = float(ctx.rng.random())
        merged = u < ctx.kv["merge_shared_risers"]
        ctx.decisions.append({"step": "share", "hot_spots": [a, b], "draw": u, "merged": merged})
        if merged:
            taken |= {a, b}
            groups.append({"hot": [a, b], "options": opts})
    for h in sorted(ctx.hot):
        if h not in taken:
            groups.append({"hot": [h], "options": _styled(ctx, _options_single(ctx, ctx.hot[h]))})
    return groups


def _styled(ctx: Ctx, opts: list[Option], shared: bool = False) -> list[Option]:
    style = ctx.kv["riser_style"]
    if style == "auto":
        return opts
    keep = [o for o in opts if o.style == style]
    if keep or shared:
        return keep
    ctx.decisions.append({"step": "style_fallback", "wanted": style, "used": "auto"})
    return opts


def _ordered(ctx: Ctx, opts: list[Option]) -> tuple[list[Option], int]:
    if ctx.kv["attachment_choice"] == "nearest" or not opts:
        return opts, 0
    k = min(max(ctx.kv["attachment_top_k"], 1), len(opts))
    i = int(ctx.rng.integers(k))
    return [opts[i]] + opts[:i] + opts[i + 1:], i


def _neck_side(room: dict, t: float) -> str | None:
    if room["cope_mm"] >= t:
        return "cope"
    if room["drag_mm"] >= t:
        return "drag"
    return None


def _edge_fits(ctx: Ctx, B, nrm, cavity: int, side: str, width: float) -> bool:
    """A neck (or ingate) of this width meeting the casting's edge at B lands on metal across its whole
    width, on its side of the parting plane (sampled a voxel inside the edge)."""
    vp = ctx.part
    tan = np.array([-nrm[1], nrm[0]])
    b = np.asarray(B[:2], float) - np.asarray(ctx.offsets[cavity][:2]) - np.asarray(nrm) * vp.h
    k = vp.k0 if side == "cope" else vp.k0 - 1
    if not 0 <= k < vp.shape[2]:
        return False
    n = max(int(np.ceil(width / (vp.h / 2))), 2)
    for t in np.linspace(-width / 2, width / 2, n + 1):
        i, j = vp.ij(*(b + t * tan))
        if not (0 <= i < vp.shape[0] and 0 <= j < vp.shape[1] and vp.metal[i, j, k]):
            return False
    return True


def _feeder(ctx: Ctx, group: dict, elements: list[dict]) -> list[dict]:
    kv, p = ctx.kv, ctx.profile
    hs = [ctx.hot[h] for h in group["hot"]]
    if not group["options"]:
        raise Infeasible("no_attachment", f"no place for a feeder for {', '.join(group['hot'])}")
    m_zone = max(h.modulus for h in hs)
    fed = sum(h.fed_volume for h in hs)
    u = float(ctx.rng.random())
    sleeved = u < kv["sleeve_probability"]
    eff = kv["feeding_efficiency.sleeved" if sleeved else "feeding_efficiency.unsleeved"]
    mef = kv["sleeve_mef"] if sleeved else 1.0
    beta = p.volumetric_shrinkage_pct / 100.0
    R = kv["riser_height_ratio"]
    target = m_zone * kv["modulus_factor"]
    opts, pick = _ordered(ctx, group["options"])
    misses = []
    rid = f"r{sum(e['type'] == 'riser' for e in elements) + 1}"
    for rank, o in enumerate(opts):
        base = {"id": rid, "type": "riser", "attached_zones": [h.id for h in hs]}
        common = {"sleeved": sleeved, "feeding_efficiency": eff, "fed_volume_mm3": fed,
                  "zone_modulus_mm": m_zone, "candidate_distance_mm": o.distance}
        if o.style == "top":
            patch = ctx.places.patches[o.cand.patch]
            room = float(p.flask_hi[2]) - p.clearances["flask_top_mm"] - patch.z
            r = size_riser(target, fed, beta, eff, R, base_cools=False, mef=mef, sleeved=sleeved,
                           d_max=room / R)
            if r is None:
                misses.append(f"{o.cand.patch}: no size fits under the cope top")
                continue
            tgt = np.mean([h.centre - ctx.offsets[o.cavity] for h in hs], axis=0)
            xy = ctx.places.place_top(o.cand.patch, r.d / 2, tgt, toward_target=len(hs) > 1,
                                      wall_gap=max(p.clearances["riser_to_casting_mm"], p.clearances["cutoff_access_mm"]))
            if xy is None:
                misses.append(f"{o.cand.patch}: walls of the part too close for a {r.d:.0f} mm feeder")
                continue
            off = ctx.offsets[o.cavity]
            new = [{**base, "params": {**common, "style": "top", "cavity": [o.cavity],
                                       "x": xy[0] + off[0], "y": xy[1] + off[1], "z": patch.z, "patch": patch.id,
                                       "overhang": r.d / 2 > patch.max_radius + 1e-9,
                                       "d": r.d, "height": r.height, "volume_mm3": r.volume_mm3,
                                       "modulus_mm": r.modulus_mm, "target_modulus_mm": target,
                                       "required_volume_mm3": r.required_volume_mm3, "limited_by": r.limited_by}}]
        else:
            neck = size_neck(m_zone, kv["neck_ratio"], kv["neck_aspect"])
            if o.pair is None:
                ends = [(_shift(o.cand, ctx.offsets[o.cavity]), np.asarray(o.cand.normal), o.cand.room, o.cavity)]
            else:
                A, na, ra, B, nb, rb, kb = o.pair
                ends = [(A, na, ra, o.cavity), (B, nb, rb, kb)]
            sides = [_neck_side(e[2], neck.thickness) for e in ends]
            if None in sides:
                misses.append("side: the neck is thicker than the casting's edge")
                continue
            if not all(_edge_fits(ctx, e[0], e[1], e[3], s_, neck.width) for e, s_ in zip(ends, sides)):
                misses.append("side: the neck is wider than the casting's edge")
                continue
            two = len(ends) == 2
            if two:
                gap = float(np.linalg.norm(ends[1][0][:2] - ends[0][0][:2]))
                d_max = gap - 2 * kv["neck_length_mm"]
            else:
                d_max = p.cope_height / R
            r = size_riser(target, fed, beta, eff, R, base_cools=True, neck=neck, mef=mef,
                           sleeved=sleeved, d_max=d_max, n_necks=len(ends))
            if r is None:
                misses.append("side: no size fits")
                continue
            if two:
                cxy = (ends[0][0][:2] + ends[1][0][:2]) / 2
            else:
                cxy = ends[0][0][:2] + ends[0][1] * (kv["neck_length_mm"] + r.d / 2)
            new = [{**base, "params": {**common, "style": "side", "cavity": sorted({e[3] for e in ends}),
                                       "x": float(cxy[0]), "y": float(cxy[1]), "z": 0.0, "d": r.d,
                                       "height": r.height, "volume_mm3": r.volume_mm3, "modulus_mm": r.modulus_mm,
                                       "target_modulus_mm": target, "required_volume_mm3": r.required_volume_mm3,
                                       "limited_by": r.limited_by}}]
            n0 = sum(e["type"] == "neck" for e in elements)
            for i, (pt, nrm, room, k), side in zip(range(len(ends)), ends, sides):
                length = float(np.linalg.norm(pt[:2] - cxy)) - r.d / 2
                new.append({"id": f"n{n0 + i + 1}", "type": "neck",
                            "attached_zones": [h.id for h in hs if h.cavity == k],
                            "params": {"riser": rid, "side": side, "width": neck.width, "thickness": neck.thickness,
                                       "length": length, "modulus_mm": neck.modulus_mm, "cavity": k,
                                       "start": [float(cxy[0]), float(cxy[1]), 0.0],
                                       "end": [float(pt[0]), float(pt[1]), 0.0]}})
        if sleeved:
            new.append({"id": f"sl{rid[1:]}", "type": "sleeve", "attached_zones": [h.id for h in hs],
                        "params": {"riser": rid, "mef": mef}})
        ctx.decisions.append({"step": "feeder", "riser": rid, "hot_spots": group["hot"], "style": o.style,
                              "pick": pick, "used_rank": rank, "options": len(opts), "sleeve_draw": u,
                              "misses": misses})
        elements += new
        return new
    raise Infeasible("no_fit", f"no feeder fits {', '.join(group['hot'])}: {'; '.join(misses[:5])}")


# ---- gating ----------------------------------------------------------------------------

def _gating(ctx: Ctx, elements: list[dict]) -> None:
    kv, p, vp, mold = ctx.kv, ctx.profile, ctx.part, ctx.mold
    risers = [e for e in elements if e["type"] == "riser"]
    necks = [e for e in elements if e["type"] == "neck"]
    lo, hi = vp.bbox()
    v_metal = vp.volume_mm3 * p.cavity_count + sum(r["params"]["volume_mm3"] for r in risers)
    mass = p.density_kg_m3 * v_metal * 1e-9
    head = p.cope_height + p.basin_head_mm
    above = max(float(hi[2]), 0.0)
    h_eff = effective_head("parting", head, above, float(hi[2] - lo[2]))
    t_fill = kv["filling_time_s"]
    choke = choke_area(mass, t_fill, p.density_kg_m3, p.discharge_coefficient, h_eff)
    a_s, a_r, a_i = ratio_areas(choke, list(p.gating_ratio))
    d_bot = math.sqrt(4 * a_s / math.pi)
    d_top = math.sqrt(4 * sprue_top_area(a_s, p.basin_head_mm, p.cope_height) / math.pi)
    n_per = kv["ingates_per_cavity"]
    n_ing = n_per * p.cavity_count
    ai = a_i / n_ing
    ti = round(math.sqrt(ai / kv["ingate_aspect"]), 1)          # sizes to 0.1 mm, as typed in the editor
    wi = round(kv["ingate_aspect"] * ti, 1)
    ar = a_r / n_ing
    side = kv["runner_side"]
    pct, rho = round(kv["runner_taper_pct"], 1), kv["runner_height_ratio"]
    W = round(math.sqrt(2 * ar / ((2 - pct / 100.0) * rho)), 1)   # bottom width; the top is pct narrower
    hr = round(rho * W, 1)
    w_par, w_far = runner_widths(side, W, pct)
    ext = round(kv["runner_extension_mm"], 1)
    lg = round(kv["ingate_length_mm"], 1)
    cl = p.clearances

    # the sprue: a random free place on the parting plane
    c = mold.centres()
    rs = d_top / 2
    ok = (mold.dist >= cl["sprue_to_casting_mm"] + rs)
    ok &= (np.abs(c[..., 0]) <= p.flask_size_mm[0] / 2 - cl["flask_wall_mm"] - rs)
    ok &= (np.abs(c[..., 1]) <= p.flask_size_mm[1] / 2 - cl["flask_wall_mm"] - rs)
    for r in risers:
        q = r["params"]
        ok &= np.hypot(c[..., 0] - q["x"], c[..., 1] - q["y"]) >= q["d"] / 2 + rs + cl["riser_spacing_mm"]
    free = np.argwhere(ok)
    if not len(free):
        raise Infeasible("no_sprue_room", "no free place for the sprue")
    pick = int(ctx.rng.integers(len(free)))
    sx, sy = (float(v) for v in c[tuple(free[pick])])
    ctx.decisions.append({"step": "sprue", "free_places": int(len(free)), "pick": pick})
    elements.append({"id": "sprue", "type": "sprue", "attached_zones": [],
                     "params": {"x": sx, "y": sy, "d_top": d_top, "d_bottom": d_bot, "length": p.cope_height,
                                "area_mm2": a_s, "choke_area_mm2": choke, "pour_mass_kg": mass,
                                "filling_time_s": t_fill, "effective_head_mm": h_eff}})

    # ingates and their runners, cavity by cavity
    g = ctx.graph
    k0 = vp.k0
    legs: list[tuple] = []                                # (a, b, width) of every runner leg so far
    S = np.array([sx, sy, 0.0])
    n_run = 0
    for k, off in enumerate(ctx.offsets):
        pts = []
        for xy, nrm, up, down, (i, j) in ctx.places.side_points:
            room = up if side == "cope" else down
            if room < ti:
                continue
            kk = k0 if k0 < vp.shape[2] and vp.metal[i, j, k0] else k0 - 1
            zid = g.by_label.get(int(g.labels[i, j, kk]))
            B = np.append(xy + np.asarray(off)[:2], 0.0)
            if any(np.linalg.norm(B[:2] - np.asarray(nk["params"]["end"][:2])) <
                   (wi + nk["params"]["width"]) / 2 + cl["riser_spacing_mm"] for nk in necks):
                continue
            pts.append((B, np.asarray(nrm), zid))
        if ctx.kv["ingate_placement"] == "nearest_thick_section":
            pts.sort(key=lambda q: (-g.zones[q[2]].modulus_mm if q[2] else 0.0, float(np.linalg.norm(q[0] - S))))
        else:
            pts = [pts[int(i)] for i in ctx.rng.permutation(len(pts))]
        chosen = []
        for B, nrm, zid in pts:
            if len(chosen) == n_per:
                break
            if any(np.linalg.norm(B - C) < wi + cl["riser_spacing_mm"] for C, _ in chosen):
                continue
            if not _edge_fits(ctx, B, nrm, k, side, wi):
                continue
            found = _route(ctx, S, B, nrm, W, lg, ext, legs, risers, necks)
            if found is None:
                continue
            route, R = found
            chosen.append((B, route))
            n_run += 1
            rid = f"ru{n_run}"
            for a, b in zip(route[:-1], route[1:]):
                legs.append((a, b, W))
            elements.append({"id": rid, "type": "runner", "attached_zones": [],
                             "params": {"side": side, "width": W, "taper_pct": pct, "w_parting": w_par, "w_far": w_far,
                                        "height": hr, "area_mm2": ar, "extension": ext,
                                        "waypoints": [[float(v) for v in q] for q in route]}})
            elements.append({"id": f"g{n_run}", "type": "ingate", "attached_zones": [f"c{k}.{zid}"] if zid else [],
                             "params": {"runner": rid, "side": side, "width": wi, "thickness": ti, "length": lg,
                                        "area_mm2": ai, "cavity": k, "at": [float(B[0]), float(B[1]), 0.0],
                                        "start": [float(R[0]), float(R[1]), 0.0],
                                        "normal": [float(nrm[0]), float(nrm[1])]}})
        if len(chosen) < n_per:
            raise Infeasible("no_ingate_route", f"cavity {k}: {len(chosen)} of {n_per} ingates found a route")
    ctx.decisions.append({"step": "gating", "runners": n_run})


def _route(ctx: Ctx, S, B, nrm, W, lg, ext, legs, risers, necks) -> tuple[list, np.ndarray] | None:
    """A runner from under the sprue that runs along the casting's edge past B, the ingate leaving its
    side at R (lg + W/2 out from B) straight to B, and the runner carrying on ext past R (the dross
    extension).  Returns (runner points, R) or None when no route is clear."""
    mold = ctx.mold
    n3 = np.append(nrm, 0.0)
    tau = np.array([-nrm[1], nrm[0], 0.0])
    R = B + n3 * (lg + W / 2)
    run_in = 2 * W                                # straight run along the edge before the ingate
    tails = []
    for sgn in (1.0, -1.0):
        Rin, Rend = R - sgn * tau * run_in, R + sgn * tau * max(ext, 1e-3)
        tails.append((float(np.linalg.norm(Rin[:2] - S[:2])), Rin, Rend))
    for _, Rin, Rend in sorted(tails, key=lambda t: t[0]):
        if not mold.segment_clear(Rin, Rend, W / 2):
            continue
        for corner in (None, np.array([S[0], Rin[1], 0.0]), np.array([Rin[0], S[1], 0.0])):
            if corner is not None and (np.allclose(corner[:2], S[:2]) or np.allclose(corner[:2], Rin[:2])):
                continue
            pts = [S] + ([corner] if corner is not None else []) + [Rin, Rend]
            if _sharpest_turn(pts) > 90.0 + 1e-6:     # the CAD module's sweep fails on hairpin corners
                continue
            if not all(mold.segment_clear(a, b, W / 2) for a, b in zip(pts[:-1], pts[1:])):
                continue
            if not _clear_of_feeders(pts, W, risers, necks, ctx.profile.clearances["riser_spacing_mm"]):
                continue
            if not _clear_of_runners(pts, W, legs, S):
                continue
            return [list(map(float, q)) for q in pts], R
    return None


def _sharpest_turn(pts) -> float:
    """Largest change of direction along a polyline, degrees (zero-length legs skipped)."""
    v = [np.asarray(b[:2], float) - np.asarray(a[:2], float) for a, b in zip(pts[:-1], pts[1:])]
    v = [x / np.linalg.norm(x) for x in v if np.linalg.norm(x) > 1e-9]
    return max((float(np.degrees(np.arccos(np.clip(a @ b, -1, 1)))) for a, b in zip(v[:-1], v[1:])), default=0.0)


def _clear_of_feeders(pts, wp, risers, necks, spacing) -> bool:
    for a, b in zip(pts[:-1], pts[1:]):
        for r in risers:
            q = r["params"]
            if q["style"] != "side":
                continue
            if segment_distance(a, b, (q["x"], q["y"]), (q["x"], q["y"])) < q["d"] / 2 + wp / 2 + spacing:
                return False
        for nk in necks:
            q = nk["params"]
            if segment_distance(a, b, q["start"], q["end"]) < q["width"] / 2 + wp / 2 + spacing:
                return False
    return True


def _clear_of_runners(pts, wp, legs, S) -> bool:
    """Runners meet only under the sprue."""
    pts = [np.asarray(q, float) for q in pts]
    for a, b in zip(pts[:-1], pts[1:]):
        for c, d, w in legs:
            c, d = np.asarray(c, float), np.asarray(d, float)
            if np.allclose(a, S) and np.allclose(c, S):
                # both leave the sprue: check only beyond their start
                a2 = a + (b - a) * min(1.0, (wp + w) / max(np.linalg.norm(b - a), 1e-9))
                c2 = c + (d - c) * min(1.0, (wp + w) / max(np.linalg.norm(d - c), 1e-9))
                if segment_distance(a2, b, c2, d) < (wp + w) / 2:
                    return False
                continue
            if segment_distance(a, b, c, d) < (wp + w) / 2:
                return False
    return True
