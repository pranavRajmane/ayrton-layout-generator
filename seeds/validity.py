"""Manufacturing checks on a generated design.  Only whether the mold can be
made and the feeders removed: nothing here judges a design unconventional.
Every limit is a clearance from the foundry profile.

Returns (rule, message) pairs; empty means valid.
  flask.*        cavities, feeders, sprue, runners and ingates inside the flask with its clearances
  spacing.*      feeder to feeder, feeder to casting wall, cavity to cavity, sprue to casting
  parting.*      runners, ingates and necks on the parting plane (z = 0), not running through a casting
  removable.*    each feeder can be knocked off or cut: a top feeder has cutoff_access_mm free of casting
                 walls rising around it (it may overhang its face where the casting falls away); a side
                 feeder's neck is at least that long
  coverage.*     every hot spot of every cavity has a feeder
"""
from __future__ import annotations

import numpy as np

from seeds.moldmap import segment_distance


def check(design: dict, ctx) -> list[tuple[str, str]]:
    p, vp, mold = ctx.profile, ctx.part, ctx.mold
    cl = p.clearances
    out: list[tuple[str, str]] = []
    els = design["elements"]
    risers = [e for e in els if e["type"] == "riser"]
    necks = [e for e in els if e["type"] == "neck"]
    runners = [e for e in els if e["type"] == "runner"]
    ingates = [e for e in els if e["type"] == "ingate"]
    sprue = next((e for e in els if e["type"] == "sprue"), None)
    L, W, _ = p.flask_size_mm
    xmax, ymax = L / 2 - cl["flask_wall_mm"], W / 2 - cl["flask_wall_mm"]
    top = float(p.flask_hi[2]) - cl["flask_top_mm"]
    bottom = float(p.flask_lo[2]) + cl["flask_bottom_mm"]

    def inside(x, y, r=0.0) -> bool:
        return abs(x) + r <= xmax + 1e-9 and abs(y) + r <= ymax + 1e-9

    # ---- flask ----------------------------------------------------------------
    lo, hi = vp.bbox()
    s = 1 + p.pattern_shrinkage_pct / 100.0                  # the pattern is the casting grown by the shrinkage
    for k, off in enumerate(design["cavity_layout"]["offsets"]):
        c = (lo + hi) / 2 + np.asarray(off)
        half = (hi - lo) / 2 * s
        if not (inside(c[0], c[1]) and abs(c[0]) + half[0] <= xmax + 1e-9 and abs(c[1]) + half[1] <= ymax + 1e-9):
            out.append(("flask.cavity", f"cavity {k} reaches into the flask's wall clearance"))
        if c[2] + half[2] > top + 1e-9 or c[2] - half[2] < bottom - 1e-9:
            out.append(("flask.cavity", f"cavity {k} reaches into the flask's top or bottom clearance"))
    for r in risers:
        q = r["params"]
        if not inside(q["x"], q["y"], q["d"] / 2):
            out.append(("flask.riser", f"{r['id']} reaches into the flask's wall clearance"))
        if q["z"] + q["height"] > top + 1e-9:
            out.append(("flask.riser", f"{r['id']} is {q['z'] + q['height'] - top:.1f} mm too tall for the cope"))
    if sprue and not inside(sprue["params"]["x"], sprue["params"]["y"], sprue["params"]["d_top"] / 2):
        out.append(("flask.sprue", "the sprue reaches into the flask's wall clearance"))
    for e in runners:
        w = max(e["params"]["w_parting"], e["params"]["w_far"]) / 2
        if not all(inside(x, y, w) for x, y, _ in e["params"]["waypoints"]):
            out.append(("flask.runner", f"{e['id']} reaches into the flask's wall clearance"))
    for e in necks:
        if not all(inside(x, y) for x, y, _ in (e["params"]["start"], e["params"]["end"])):
            out.append(("flask.neck", f"{e['id']} is outside the flask"))
    if mold.spill:
        out.append(("flask.cavity", "part of a cavity lies outside the flask"))

    # ---- spacing ----------------------------------------------------------------
    for a, b in ((a, b) for i, a in enumerate(risers) for b in risers[i + 1:]):
        qa, qb = a["params"], b["params"]
        gap = float(np.hypot(qa["x"] - qb["x"], qa["y"] - qb["y"])) - (qa["d"] + qb["d"]) / 2
        if gap < cl["riser_spacing_mm"]:
            out.append(("spacing.riser_riser", f"{a['id']} and {b['id']} are {gap:.1f} mm apart"))
    for r in risers:
        q = r["params"]
        if q["style"] == "side":
            gap = _circle_gap(mold, q["x"], q["y"], q["d"] / 2)
            if gap < cl["riser_to_casting_mm"] - 1e-9:
                out.append(("spacing.riser_casting", f"{r['id']} is {gap:.1f} mm from a casting wall"))
        elif mold.walls_near(q["x"], q["y"], q["d"] / 2 + cl["riser_to_casting_mm"], q["z"]):
            out.append(("spacing.riser_casting", f"{r['id']} stands within {cl['riser_to_casting_mm']} mm of a "
                                                 "casting wall rising beside it"))
    offs = design["cavity_layout"]["offsets"]
    for i in range(len(offs)):
        for j in range(i + 1, len(offs)):
            d = np.abs(np.asarray(offs[i]) - np.asarray(offs[j]))[:2] - (hi - lo)[:2] * s
            if max(d) < cl["cavity_spacing_mm"] - 1e-9:
                out.append(("spacing.cavity", f"cavities {i} and {j} are {max(d):.1f} mm apart"))
    if sprue:
        q = sprue["params"]
        gap = mold.gap(q["x"], q["y"]) - q["d_top"] / 2
        if gap < cl["sprue_to_casting_mm"] - mold.h:
            out.append(("spacing.sprue_casting", f"the sprue is {gap:.1f} mm from a casting"))

    # ---- parting plane ------------------------------------------------------------
    for e in runners + necks + ingates:
        q = e["params"]
        pts = q.get("waypoints") or [q[k] for k in ("start", "end", "at") if k in q]
        if any(abs(z) > 1e-9 for _, _, z in pts):
            out.append(("parting.off_plane", f"{e['id']} leaves the parting plane"))
    for e in runners:
        pts = e["params"]["waypoints"]
        hw = max(e["params"]["w_parting"], e["params"]["w_far"]) / 2
        if not all(mold.segment_clear(a, b, hw) for a, b in zip(pts[:-1], pts[1:])):
            out.append(("parting.runner_through_casting", f"{e['id']} runs through a casting"))
        for r in risers:
            if r["params"]["style"] != "side":
                continue
            q = r["params"]
            if any(segment_distance(a, b, (q["x"], q["y"]), (q["x"], q["y"])) < q["d"] / 2 + hw
                   for a, b in zip(pts[:-1], pts[1:])):
                out.append(("parting.runner_through_riser", f"{e['id']} runs through {r['id']}"))

    # ---- removable ----------------------------------------------------------------
    for r in risers:
        q = r["params"]
        if q["style"] == "top":
            if mold.walls_near(q["x"], q["y"], q["d"] / 2 + cl["cutoff_access_mm"], q["z"]):
                out.append(("removable.access", f"{r['id']} has no {cl['cutoff_access_mm']} mm cut-off access"))
    for n in necks:
        if n["params"]["length"] < cl["cutoff_access_mm"] - 1e-9:
            out.append(("removable.neck", f"{n['id']} is {n['params']['length']:.1f} mm long, under the "
                                          f"{cl['cutoff_access_mm']} mm cut-off access"))

    # ---- coverage ------------------------------------------------------------------
    covered = {z for r in risers for z in r["attached_zones"]}
    for h in sorted(ctx.hot):
        if h not in covered:
            out.append(("coverage.hot_spot", f"hot spot {h} has no feeder"))
    return out


def _circle_gap(mold, x: float, y: float, r: float) -> float:
    """Smallest distance from a circle's edge to a casting, mm (negative: it overlaps one)."""
    c = mold.centres()
    rr = np.hypot(c[..., 0] - x, c[..., 1] - y)
    if (mold.footprint & (rr <= r)).any():
        return -1.0
    if not mold.footprint.any():
        return np.inf
    return float((rr[mold.footprint] - mold.h / 2).min() - r)
