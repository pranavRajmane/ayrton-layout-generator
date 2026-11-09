"""Path elements (runners): a section swept along a polyline with rounded bends.

A path element lives in the mold frame (its placement is the identity).  Its
polyline is solved by the regeneration engine between its constrained ends:

    path = [upstream port origin (if fed)] + waypoints + [downstream inlet origin (if that is fixed)]

and handed to build() as ``p.path``.  The section keeps +Z as its up vector
along the whole path, so paths belong in horizontal planes (the parting plane)
or are straight.  Taps sit on the section's surface at a station s in [0, 1]
(fraction of the path length) on the top, bottom, left or right side.

Subclasses (library elements) provide ``section(p)`` and declare the params
``waypoints`` (kind="points") and ``bend_radius``.  A subclass whose path is
not the section centre (a runner drawn on the parting plane and hanging into
the cope or the drag) returns the centre's height over the path from
``rise(p)``.
"""
from __future__ import annotations

import numpy as np
from build123d import Shape, Wire
from OCP.BRepBuilderAPI import BRepBuilderAPI_RightCorner
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.gp import gp_Dir

from core.element import Element, Params, Port, PortKind, Section, SectionPolicy
from core.frames import Frame

UP = np.array([0.0, 0.0, 1.0])


class PathError(ValueError):
    pass


def section_extent(sec: Section) -> tuple[float, float, float]:
    """(half height, half width at mid height, half width at top) of a section centred on the path."""
    d = sec.d
    if sec.shape == "round":
        return d["d"] / 2, d["d"] / 2, 0.0
    if sec.shape == "rectangle":
        return d["height"] / 2, d["width"] / 2, d["width"] / 2
    return d["height"] / 2, (d["top"] + d["bottom"]) / 4, d["top"] / 2


def polyline_wire(points, radius: float) -> Wire:
    pts = [tuple(map(float, q)) for q in points]
    if len(pts) < 2:
        raise PathError("a path needs at least two points")
    if any(np.linalg.norm(np.subtract(a, b)) < 1e-6 for a, b in zip(pts, pts[1:])):
        raise PathError("two consecutive path points coincide")
    w = Wire.make_polygon(pts, close=False)
    if radius > 0 and len(pts) > 2:
        if np.ptp(np.asarray(pts)[:, 2]) > 1e-6:
            raise PathError("bends can be rounded only on a horizontal path")
        try:
            w = w.fillet_2d(radius, w.vertices()[1:-1])
        except Exception as e:                              # noqa: BLE001
            raise PathError(f"bend radius {radius} mm does not fit between the path points ({e})") from None
    return w


class PathElement(Element):
    is_path = True

    def section(self, p: Params) -> Section:
        raise NotImplementedError

    def rise(self, p: Params) -> float:
        """Height of the section centre above the path line."""
        return 0.0

    def floor_z(self, p: Params) -> float:
        """Height of the section's lowest point (the path is horizontal at its start)."""
        return float(self.points(p)[0][2]) + self.rise(p) - section_extent(self.section(p))[0]

    def drop_point(self, p: Params, origin) -> tuple[float, float, float]:
        """Where the path starts under metal dropped in at origin (a drop joint):
        straight below or above it, in the plane of the waypoints."""
        z = float(p.waypoints[0][2]) if p.waypoints else float(origin[2])
        return float(origin[0]), float(origin[1]), z

    # ---- the solved path ---------------------------------------------------
    def points(self, p: Params):
        pts = p.get("path") or p.waypoints
        return pts

    def wire(self, p: Params) -> Wire:
        return polyline_wire(self.points(p), p.bend_radius)

    def frame_at(self, p: Params, s: float) -> Frame:
        w = self.wire(p)
        s = float(np.clip(s, 0.0, 1.0))
        o, t = w.position_at(s), w.tangent_at(s)
        return Frame((o.X, o.Y, o.Z), (t.X, t.Y, t.Z), tuple(UP))

    def length(self, p: Params) -> float:
        return self.wire(p).length

    def nearest_station(self, p: Params, point) -> float:
        w = self.wire(p)
        n = max(200, int(w.length / 2))
        ss = np.linspace(0, 1, n + 1)
        pts = np.array([tuple(w.position_at(x)) for x in ss])
        i = int(np.argmin(np.linalg.norm(pts - np.asarray(point, float), axis=1)))
        lo, hi = ss[max(i - 1, 0)], ss[min(i + 1, n)]
        for _ in range(40):                                  # golden-free bisection on the distance
            a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
            da = np.linalg.norm(np.subtract(tuple(w.position_at(a)), point))
            db = np.linalg.norm(np.subtract(tuple(w.position_at(b)), point))
            lo, hi = (lo, b) if da < db else (a, hi)
        return float((lo + hi) / 2)

    # ---- contract ------------------------------------------------------------
    def ports(self, p: Params):
        sec = self.section(p)
        return [Port("inlet", PortKind.INLET, self.frame_at(p, 0.0), sec, SectionPolicy.OWN),
                Port("outlet", PortKind.OUTLET, self.frame_at(p, 1.0), sec, SectionPolicy.OWN)]

    def tap_frame(self, p: Params, s: float, side: str) -> Frame:
        f = self.frame_at(p, s)
        t, o = np.asarray(f.normal), np.asarray(f.origin)
        lateral = np.cross(UP, t)                             # left of the flow
        hh, hw_mid, _ = section_extent(self.section(p))
        o = o + self.rise(p) * UP
        if side == "top":
            return Frame(tuple(o + hh * UP), tuple(UP), tuple(t))
        if side == "bottom":
            return Frame(tuple(o - hh * UP), tuple(-UP), tuple(t))
        sign = -1.0 if side == "right" else 1.0
        return Frame(tuple(o + sign * hw_mid * lateral), tuple(sign * lateral), tuple(UP))

    def derived(self, p: Params) -> dict:
        L = self.length(p)
        return {"length_mm": L, "section_area_mm2": self.section(p).area, "volume_mm3": L * self.section(p).area}

    def build(self, p: Params) -> Shape:
        pts = [np.asarray(q, float) for q in self.points(p)]
        e = p.embed
        if e > 0:                                             # run embed past both end planes, straight on
            t0 = (pts[1] - pts[0]) / np.linalg.norm(pts[1] - pts[0])
            t1 = (pts[-1] - pts[-2]) / np.linalg.norm(pts[-1] - pts[-2])
            pts = [pts[0] - e * t0] + pts[1:-1] + [pts[-1] + e * t1]
        w = polyline_wire(pts, p.bend_radius)
        f0 = Frame(tuple(pts[0]), tuple(w.tangent_at(0.0)), tuple(UP))
        sd, up, o = np.asarray(f0.side), np.asarray(f0.up), np.asarray(f0.origin) + self.rise(p) * UP
        prof = Wire.make_polygon([tuple(o + a * sd + b * up) for a, b in self.section(p).outline()], close=True)
        ps = BRepOffsetAPI_MakePipeShell(w.wrapped)
        ps.SetMode(gp_Dir(*UP))                               # binormal fixed: the section never twists
        ps.SetTransitionMode(BRepBuilderAPI_RightCorner)      # sharp bends mitred, not left open
        ps.Add(prof.wrapped, False, False)
        ps.Build()
        if not ps.IsDone() or not ps.MakeSolid():
            raise PathError("the sweep along the path failed")
        solid = Shape.cast(ps.Shape())
        if not solid.is_valid:
            raise PathError(self._tight(p) or "the sweep along the path gave an invalid solid")
        return solid

    def _tight(self, p: Params) -> str | None:
        """Why a sweep folds over itself: a segment shorter than the section is wide, at a bend."""
        pts = [np.asarray(q, float) for q in self.points(p)]
        w = 2 * section_extent(self.section(p))[1]
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            L = float(np.linalg.norm(b - a))
            if L < w and len(pts) > 2:
                return (f"segment {k + 1} is {L:.0f} mm long, shorter than the runner is wide ({w:.0f} mm): "
                        "move its points apart")
        return None
