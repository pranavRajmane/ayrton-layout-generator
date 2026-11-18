"""Feeder and neck sizes by the modulus method.  Every number that is a
casting choice arrives as an argument (a knob or the foundry profile).

Feeder: a round cylinder, height = height_ratio x d, like the CAD module's
``feeder`` element.  Cooling area: its side and top; its base too for a side
feeder (it stands on sand), less the neck's section; a top feeder's base sits
on the casting and does not cool.  A sleeve multiplies the modulus by its
modulus extension factor.

Sizing grows d in ``step`` mm until
  modulus x mef >= target (the fed zones' modulus x modulus_factor),
  modulus x mef >  the neck's modulus (side feeders),
  efficiency x V >= shrinkage x (V_fed + V)       (the volume check),
  d >= the neck's width,
and gives up past d_max.

Neck: a short rectangular bar, width = aspect x thickness, modulus
w t / (2 (w + t)) = neck_ratio x the zone's modulus.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def cylinder(d: float, height: float, base_cools: bool, neck_area: float = 0.0) -> tuple[float, float, float]:
    """(volume mm3, cooling area mm2, modulus mm)."""
    v = math.pi * d * d / 4 * height
    a = math.pi * d * height + math.pi * d * d / 4
    if base_cools:
        a += max(math.pi * d * d / 4 - neck_area, 0.0)
    return v, a, v / a


@dataclass
class Neck:
    width: float
    thickness: float
    modulus_mm: float


def size_neck(zone_modulus: float, neck_ratio: float, aspect: float) -> Neck:
    m = zone_modulus * neck_ratio
    t = 2 * m * (aspect + 1) / aspect
    return Neck(aspect * t, t, m)


@dataclass
class Riser:
    d: float
    height: float
    volume_mm3: float
    modulus_mm: float               # effective (with the sleeve)
    target_modulus_mm: float
    required_volume_mm3: float
    sleeved: bool
    limited_by: str                 # which condition set the final size


def size_riser(target_modulus: float, fed_volume: float, shrinkage: float, efficiency: float,
               height_ratio: float, base_cools: bool, neck: Neck | None = None, mef: float = 1.0,
               sleeved: bool = False, d_max: float = math.inf, step: float = 1.0, n_necks: int = 1) -> Riser | None:
    """shrinkage and efficiency are fractions (0.05, not 5 %); n_necks: how many necks leave its base
    (two for a feeder shared by neighbouring cavities).  None: nothing up to d_max holds (or the
    efficiency cannot cover the shrinkage at all)."""
    if efficiency <= shrinkage:
        return None
    v_req = shrinkage * fed_volume / (efficiency - shrinkage)
    neck_area = n_necks * neck.width * neck.thickness if neck else 0.0
    d, last = step, "step"
    while d <= d_max + 1e-9:
        v, _, m = cylinder(d, height_ratio * d, base_cools, neck_area)
        m *= mef
        fails = []
        if m < target_modulus:
            fails.append("modulus")
        if neck and m <= neck.modulus_mm:
            fails.append("neck")
        if v < v_req:
            fails.append("volume")
        if neck and d < neck.width:
            fails.append("neck_width")
        if not fails:
            return Riser(d, height_ratio * d, v, m, target_modulus, v_req, sleeved, last)
        last = fails[0]
        d += step
    return None
