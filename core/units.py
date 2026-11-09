"""Units and conventions used everywhere inside the module.

Lengths in mm, mass in kg, time in s, temperature in degC.  Areas are mm2 and
volumes mm3 unless a name says otherwise.  Z is up in the pouring position and
the parting plane is Z = 0 of the mold frame.
"""
from __future__ import annotations

UNITS = {"length": "mm", "mass": "kg", "time": "s", "temperature": "degC"}

# Units a Param may declare; anything else is rejected by the registry.
PARAM_UNITS = {"mm", "mm2", "mm3", "deg", "kg", "s", "degC", "m/s", "kg/s", "-", ""}

MM3_PER_M3 = 1e9
MM2_PER_M2 = 1e6


def mm3_to_m3(v: float) -> float:
    return v / MM3_PER_M3


def mass_kg(volume_mm3: float, density_kg_m3: float) -> float:
    """Mass of a volume given in mm3 at a density in kg/m3."""
    return mm3_to_m3(volume_mm3) * density_kg_m3
