"""Phase 7's informational numbers per routed layout (never a score, never a ranking):

    poured_weight          kg, castings + gating (the sizing's fixed point)
    yield_pct              castings / poured
    runner_length          mm, every runner arm's centreline as built (run-ons included)
    min_clearance          mm, the smallest gap in plan between a cavity and anything not its own:
                           another cavity, a runner arm, the sprue or the flask wall
    path_length_error_pct  (longest - shortest) / shortest sprue-to-ingate path

Written to the job's ``metrics`` section.
"""
from __future__ import annotations

import time

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from layouts import footprint as fp


def of(layout: dict, rec: dict, ctx, flask: dict) -> dict:
    s = rec["sizing"]
    cavs = [fp.place(ctx.outline, ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"])
            for c in sorted(layout["cavities"], key=lambda c: c["index"])]
    band = [fp.place(ctx.band, ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"]) if ctx.band is not None
            else None for c in sorted(layout["cavities"], key=lambda c: c["index"])]
    runners = [x for x in rec["segments"] if x["role"] == "runner"]
    length = float(sum(np.linalg.norm(np.subtract(x["points"][1][:2], x["points"][0][:2])) for x in runners))
    gaps = []
    if len(cavs) > 1:
        arr = np.array(cavs, dtype=object)
        i, j = np.triu_indices(len(cavs), 1)
        gaps.append(float(shapely.distance(arr[i], arr[j]).min()))
    L, W = float(flask["length"]), float(flask["width"])
    wall = box(-L / 2, -W / 2, L / 2, W / 2).exterior
    gaps.append(min(float(c.distance(wall)) for c in cavs))
    for x in runners:
        a, b = (np.asarray(p[:2], float) for p in x["points"][:2])
        if np.linalg.norm(b - a) < 1e-9:
            continue
        d = x["section"]["dims"]
        w = max(d.get("top", 0.0), d.get("bottom", 0.0))
        rb = LineString([a, b]).buffer(w / 2, cap_style="flat")
        gaps += [float(rb.distance(c)) for c in band if c is not None]
    sp = s["sprue"]
    r = max(sp["top"]["dims"]["d"], sp["bottom"]["dims"]["d"]) / 2
    spr = Point(layout["sprue"]).buffer(r)
    gaps += [float(spr.distance(c)) for c in cavs]
    paths = s["path_lengths_mm"]
    return {"layout": layout["id"], "poured_weight": round(float(s["poured_kg"]), 6),
            "yield_pct": round(float(s["yield_pct"]), 6), "runner_length": round(length, 6),
            "min_clearance": round(min(gaps), 6),
            "path_length_error_pct": round(100 * (max(paths) - min(paths)) / max(min(paths), 1e-9), 6)}


def run(job, ctx=None) -> list[dict]:
    """The metrics phase on a GatingJob: one row per routed layout."""
    from layouts import runners as rn

    job.start("metrics")
    t0 = time.perf_counter()
    try:
        ctx = ctx or rn.context(job)
        d = job.data
        lay = {x["id"]: x for x in d["layouts"]}
        rows = [of(lay[r["layout"]], r, ctx, d["inputs"]["flask"]) for r in d["runners"]]
    except Exception as e:                                          # noqa: BLE001
        job.fail("metrics", f"{type(e).__name__}: {e}")
        raise
    job.finish("metrics", rows, runtime_s=round(time.perf_counter() - t0, 3), counts={"layouts": len(rows)})
    return rows
