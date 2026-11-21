"""Server-drawn top views of the parting plane, as SVG: the flask and its wall clearance, the cavities,
the sprue, the runners and the ingates, one colour per kind of element.

Every drawn thing carries data attributes with the numbers it was drawn from (a cavity's index and
placed centroid, the sprue's centre), so a test can read the SVG back and compare it with the layout.
Coordinates are millimetres in the mold frame with y flipped (SVG's y runs down).
"""
from __future__ import annotations

import html

import numpy as np
from shapely.geometry import LineString

from layouts import footprint as fp

COLOURS = {"flask": "#2a3337", "wall": "#7c878a", "cavity": "#9fb3bd", "cavity_edge": "#4d6470",
           "runner": "#d9641e", "ingate": "#b8460f", "sprue": "#d9641e", "sand": "#ece6d8"}


def _path(geom) -> str:
    out = []
    for g in getattr(geom, "geoms", [geom]):
        for ring in [g.exterior, *g.interiors]:
            xy = np.asarray(ring.coords)
            out.append("M" + " L".join(f"{x:.2f},{-y:.2f}" for x, y in xy) + " Z")
    return " ".join(out)


def svg(layout: dict, rec: dict | None, outline, scale: float, flask: dict, wall_mm: float,
        width_px: int | None = None, numbers: bool = True) -> str:
    """The top view of one layout (rec: its runner record, None when it was not routed)."""
    L, W = float(flask["length"]), float(flask["width"])
    pad = 0.03 * max(L, W)
    size = f' width="{width_px}"' if width_px else ""
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-L / 2 - pad:.1f} {-W / 2 - pad:.1f} {L + 2 * pad:.1f} '
         f'{W + 2 * pad:.1f}"{size} data-layout="{html.escape(layout["id"])}">',
         f'<rect x="{-L / 2}" y="{-W / 2}" width="{L}" height="{W}" fill="{COLOURS["sand"]}" stroke="{COLOURS["flask"]}" '
         f'stroke-width="{0.004 * max(L, W):.2f}"/>',
         f'<rect x="{-L / 2 + wall_mm}" y="{-W / 2 + wall_mm}" width="{L - 2 * wall_mm}" height="{W - 2 * wall_mm}" '
         f'fill="none" stroke="{COLOURS["wall"]}" stroke-width="{0.002 * max(L, W):.2f}" '
         f'stroke-dasharray="{0.01 * max(L, W):.1f} {0.007 * max(L, W):.1f}"/>']
    for c in sorted(layout["cavities"], key=lambda c: c["index"]):
        poly = fp.place(outline, scale, c["rotation_deg"], c["translation"], c["mirrored"])
        cx, cy = poly.centroid.x, poly.centroid.y
        p.append(f'<path class="cavity" data-cavity="{c["index"]}" data-cx="{cx:.3f}" data-cy="{cy:.3f}" '
                 f'd="{_path(poly)}" fill="{COLOURS["cavity"]}" stroke="{COLOURS["cavity_edge"]}" '
                 f'stroke-width="{0.002 * max(L, W):.2f}"/>')
        if numbers:
            p.append(f'<text x="{cx:.1f}" y="{-cy:.1f}" font-size="{0.03 * max(L, W):.1f}" text-anchor="middle" '
                     f'dominant-baseline="central" fill="{COLOURS["flask"]}" font-family="monospace">{c["index"]}</text>')
    S = layout["sprue"]
    r_sprue = 0.012 * max(L, W)
    if rec is not None:
        for s in rec["segments"]:
            pts = [q[:2] for q in s["points"]]
            d = s["section"]["dims"]
            if s["role"] == "sprue":
                sp = rec["sizing"]["sprue"]
                r_sprue = max(sp["top"]["dims"]["d"], sp["bottom"]["dims"]["d"]) / 2
                continue
            if np.linalg.norm(np.subtract(pts[1], pts[0])) < 1e-6:
                continue
            w = d.get("width") or max(d.get("top", 0), d.get("bottom", 0))
            band = LineString(pts).buffer(w / 2, cap_style="flat")
            kind = "runner" if s["role"] == "runner" else "ingate"
            p.append(f'<path class="{kind}" d="{_path(band)}" fill="{COLOURS[kind]}"/>')
    p.append(f'<circle class="sprue" data-x="{S[0]:.3f}" data-y="{S[1]:.3f}" cx="{S[0]:.2f}" cy="{-S[1]:.2f}" '
             f'r="{r_sprue:.2f}" fill="{COLOURS["sprue"]}" stroke="{COLOURS["flask"]}" stroke-width="{0.002 * max(L, W):.2f}"/>')
    p.append("</svg>")
    return "".join(p)
