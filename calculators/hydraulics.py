"""Pure hydraulics of a gating system: no geometry, no foundry constants.
Units: mm, mm2, kg, s, kg/m3, Pa s; velocities in m/s, flow in m3/s.

The discharge coefficient, density, viscosity and every limit come from the
process preset; which effective-head formula applies is the engineer's choice.
"""
from __future__ import annotations

import math

G = 9.80665                     # m/s2, standard gravity


def free_fall_velocity(head_mm: float) -> float:
    """Bernoulli: v = sqrt(2 g H)."""
    if head_mm <= 0:
        raise ValueError("the metal head must be positive")
    return math.sqrt(2 * G * head_mm / 1000.0)


def choke_area(mass_kg: float, pour_time_s: float, density_kg_m3: float, discharge_coefficient: float,
               head_mm: float) -> float:
    """Smallest flow area (mm2) that delivers mass_kg in pour_time_s under head_mm:
    A = W / (rho t C sqrt(2 g H))."""
    if min(mass_kg, pour_time_s, density_kg_m3, discharge_coefficient) <= 0:
        raise ValueError("mass, pour time, density and discharge coefficient must be positive")
    a_m2 = mass_kg / (density_kg_m3 * pour_time_s * discharge_coefficient * free_fall_velocity(head_mm))
    return a_m2 * 1e6


def effective_head(method: str, head_mm: float, height_above_gate_mm: float = 0.0,
                   casting_height_mm: float = 0.0) -> float:
    """H the metal pushes with, by the textbook forms:
       top      H                         (gates at the top of the casting)
       bottom   H - C/2                   (gates at the bottom)
       parting  H - P**2 / (2 C)          (gates at the parting line)
    H: metal level above the gates, P: casting height above the gates, C: casting height."""
    if method == "top":
        return head_mm
    if casting_height_mm <= 0:
        raise ValueError("the casting height must be positive")
    if method == "bottom":
        return head_mm - casting_height_mm / 2
    if method == "parting":
        return head_mm - height_above_gate_mm ** 2 / (2 * casting_height_mm)
    raise ValueError(f"unknown head method '{method}'")


def sprue_top_area(bottom_area_mm2: float, head_at_top_mm: float, sprue_height_mm: float) -> float:
    """Continuity with free fall: A v is constant and v grows as sqrt(head), so
    A_top = A_bottom sqrt((h_top + L) / h_top); a straight sprue wider at the top
    would let the falling stream pull away from its walls and draw in air."""
    if head_at_top_mm <= 0:
        raise ValueError("the metal head over the sprue top must be positive")
    return bottom_area_mm2 * math.sqrt((head_at_top_mm + sprue_height_mm) / head_at_top_mm)


def flow_rate(volume_mm3: float, pour_time_s: float) -> float:
    """m3/s."""
    return volume_mm3 * 1e-9 / pour_time_s


def velocity(flow_m3_s: float, area_mm2: float) -> float:
    return flow_m3_s / (area_mm2 * 1e-6)


def hydraulic_diameter(area_mm2: float, perimeter_mm: float) -> float:
    return 4 * area_mm2 / perimeter_mm


def reynolds(density_kg_m3: float, velocity_m_s: float, hydraulic_diameter_mm: float, viscosity_pa_s: float) -> float:
    return density_kg_m3 * velocity_m_s * hydraulic_diameter_mm / 1000.0 / viscosity_pa_s


def ratio_areas(choke_mm2: float, ratio: list[float]) -> list[float]:
    """Total areas of (sprue, runner, ingate) for a ratio, its smallest member being the choke."""
    if len(ratio) != 3 or min(ratio) <= 0:
        raise ValueError("a gating ratio has three positive members")
    return [choke_mm2 * r / min(ratio) for r in ratio]
