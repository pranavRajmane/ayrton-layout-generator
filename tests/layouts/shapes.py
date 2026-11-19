"""Test solids for Phase 1, built with build123d in the mold frame (mm).

Analytic bodies, and junctions of equal-thickness plates extruded along z (a 2D section x depth).
Every body is placed so its faces are not on voxel boundaries by accident."""
from __future__ import annotations

import math

from build123d import Box, Cylinder, Location, Pos, Rot, Sphere, Solid


def cube(a=60.0):
    return Pos(0.3, 0.2, 0.1) * Box(a, a, a)


def sphere(d=60.0):
    return Pos(0.3, 0.2, 0.1) * Sphere(d / 2)


def cylinder(d=40.0, length=400.0):
    """Axis along x; its two end faces are the faces at x = +-length/2."""
    return Pos(0.3, 0.2, 0.1) * (Rot(0, 90, 0) * Cylinder(d / 2, length))


def plate(t=20.0, size=400.0):
    return Pos(0.3, 0.2, 0.1) * Box(size, size, t)


def _union(boxes):
    s = boxes[0]
    for b in boxes[1:]:
        s = s + b
    return s.clean()


def _bar(x0, y0, x1, y1, depth):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, 0) * Box(x1 - x0, y1 - y0, depth)


def l_junction(t=20.0, leg=160.0, depth=120.0):
    return _union([_bar(0, 0, leg, t, depth), _bar(0, 0, t, leg, depth)])


def t_junction(t=20.0, arm=160.0, depth=120.0):
    return _union([_bar(-arm, 0, arm, t, depth), _bar(-t / 2, -arm, t / 2, 0.0, depth)])


def x_junction(t=20.0, arm=160.0, depth=120.0):
    return _union([_bar(-arm, -t / 2, arm, t / 2, depth), _bar(-t / 2, -arm, t / 2, arm, depth)])


def stepped_plate(t1=40.0, t2=20.0, length=160.0, width=200.0):
    """Thick half (x < 0) and thin half, both with their bottom on z = -t1/2."""
    thick = Pos(-length / 2, 0, 0) * Box(length, width, t1)
    thin = Pos(length / 2, 0, -(t1 - t2) / 2) * Box(length, width, t2)
    return _union([thick, thin])


def turned(shape, deg=30.0):
    return Rot(0, 0, deg) * shape


JUNCTIONS = {"L": (l_junction, 3), "T": (t_junction, 4), "X": (x_junction, 5), "step": (stepped_plate, 2)}
JUNCTION_POINT = {"L": (10.0, 10.0), "T": (0.0, 10.0), "X": (0.0, 0.0), "step": (-80.0, 0.0)}
