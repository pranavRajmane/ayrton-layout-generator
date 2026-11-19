"""Phase 1 convergence: halving the voxel size changes every zone modulus by less than
``convergence_tolerance_pct``, and no zone appears or disappears.  Slow (each case runs twice, the
second time on eight times the voxels): ``pytest -m slow``."""
from __future__ import annotations

import numpy as np
import pytest

from layouts import recipe as rc
from layouts.modulus import analyse

from . import shapes as S

ST = rc.settings()["modulus"]
TOL = ST["convergence_tolerance_pct"]

CASES = {
    "cube": S.cube, "sphere": S.sphere, "cylinder": S.cylinder,
    "L": S.l_junction, "L30": lambda: S.turned(S.l_junction()),
    "T": S.t_junction, "T30": lambda: S.turned(S.t_junction()),
    "X": S.x_junction, "X30": lambda: S.turned(S.x_junction()),
    "step": S.stepped_plate,
}


# Junction zones whose cooling area is almost all inside-corner strips, so where the zone cut lands
# on the thickness ramp (read off voxels) moves their area: measured on halving 2026-11-19.
# Accepted by the engineer as known (the solidification run is the final check); DECISIONS 61.
KNOWN = {"T30": "turned T junction zone moves 6.7 %", "X": "X junction zone moves 5.8 %"}


@pytest.mark.slow
@pytest.mark.parametrize("name", [
    pytest.param(n, marks=pytest.mark.xfail(reason=KNOWN[n], strict=False)) if n in KNOWN else n
    for n in CASES])
def test_halving_the_voxel_moves_no_zone_modulus_more_than_tolerance(name):
    shape = CASES[name]()
    a = analyse(shape, ST).section
    b = analyse(shape, ST, h=a["voxel_size"] / 2).section
    za, zb = a["zones"], b["zones"]
    assert len(za) == len(zb)
    cb = np.array([z["centroid"] for z in zb])
    for z in za:
        m = zb[int(np.argmin(np.linalg.norm(cb - z["centroid"], axis=1)))]
        change = abs(m["modulus"] - z["modulus"]) / z["modulus"] * 100
        assert change < TOL, (name, z["id"], z["modulus"], m["modulus"], round(change, 2))
