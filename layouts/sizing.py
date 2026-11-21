"""Phase 4 sizing: pour time, head, choke and the sprue / runner / ingate sections of one layout.

Every number comes from the recipe or the geometry, and the result keeps the whole trail (inputs,
each step, the iterations) so an engineer can check it by hand.  The textbook forms
(``calculators.hydraulics``):

    pour time   t = the recipe's formula in W (castings, kg), G (poured, kg), t (thickest section, mm)
    head        H = cope height + basin head - gate level           (metal level above the ingates)
    effective   H_eff = H - P^2 / (2 C)                              (parting-line gating)
                P = casting height above the gate level, C = casting height
    choke       A = G / (rho t C_d sqrt(2 g H_eff))
    areas       sprue : runner : ingate from the recipe's ratio, its smallest member being the choke
    sprue       its narrow top takes the sprue area; its bottom opens by the recipe's draft over its
                length (the pattern draws out at the parting plane, so the sprue is widest there:
                the engineer, 2026-11-19; it was sized by continuity, wider at the top, before)

Conventions (the engineer's choices, DECISIONS 63):
  - G = castings + sprue + runners + ingates (cup and well are left out: they come with the 3D build),
    iterated to a fixed point because the runner sizes depend on G.
  - The runner area is the total over the branches at every level: two arms off the sprue get
    A_r / 2 each, four branches A_r / 4 each.  A runner does not step down along an arm.
  - One ingate per cavity, A_i / N each; its thickness is the recipe's, its width = area / thickness.
  - t for the pour-time formula = the diameter of the largest inscribed sphere (Phase 1's biggest
    hot-spot radius x 2).
  - Castings are as cast (no pattern allowance); the cavities in the mold are the scaled ones.
"""
from __future__ import annotations

import math

from calculators import hydraulics as hy
from calculators.formula import evaluate
from core.element import Section
from core.workflow import runner_widths


class SizingError(ValueError):
    pass


def runner_section(area: float, side: str, height_ratio: float, taper_pct: float) -> Section:
    """Trapezoid of this area: bottom W, top (1 - taper) W, height = ratio x W."""
    w = math.sqrt(2 * area / (height_ratio * (2 - taper_pct / 100.0)))
    w_par, w_far = runner_widths(side, w, taper_pct)
    top, bottom = (w_par, w_far) if side == "drag" else (w_far, w_par)
    return Section.trapezoid(top, bottom, height_ratio * w)


def section_dict(sec: Section) -> dict:
    return {"shape": sec.shape, "dims": {k: round(v, 6) for k, v in sec.dims}, "area": round(sec.area, 6)}


def size(*, values: dict, casting_volume_mm3: float, n: int, z_top: float, z_bottom: float, thickest_mm: float,
         cope_height: float, branches: list[int], gating_volume=None, max_iter: int = 100,
         rel_tol: float = 1e-9) -> dict:
    """Sizes for a layout of ``n`` cavities.  ``branches``: how many runner branches run at each level
    below the sprue (straight [2], H [2, 4], radial [4], ...).  ``gating_volume(sizes) -> mm3`` gives the
    volume of sprue, runners and ingates for trial sizes (the routing knows their lengths); without it
    G = castings only."""
    v = values
    lay = v["layout"]
    rho, cd = float(v["density_kg_m3"]), float(v["discharge_coefficient"])
    ratio = [float(x) for x in v["gating_ratios"]["default"]]
    formula = v["pour_time_formula"]
    basin = float(lay["sprue"]["basin_head_mm"])
    draft = float(lay["draft_deg"])
    run = lay["runner"]
    side = run["side"]
    gate = lay["ingate"]
    t_i = float(gate["thickness_mm"])
    z_gate = -t_i / 2 if gate["side"] == "drag" else t_i / 2

    H = cope_height + basin - z_gate
    C = z_top - z_bottom
    P = min(max(z_top - z_gate, 0.0), C)
    h_eff = hy.effective_head("parting", H, P, C)
    if h_eff <= 0:
        raise SizingError(f"no effective head: H {H:.1f} mm, P {P:.1f} mm, C {C:.1f} mm")
    W = rho * casting_volume_mm3 * n * 1e-9

    sprue_len = cope_height                     # drag runner: the sprue ends on the plane, the runner below
    G, trail = W, []
    for it in range(max_iter):
        t_pour = evaluate(formula, W=W, G=G, t=thickest_mm)
        if not t_pour > 0:
            raise SizingError(f"the pour-time formula gives {t_pour} s")
        choke = hy.choke_area(G, t_pour, rho, cd, h_eff)
        a_s, a_r, a_i = hy.ratio_areas(choke, ratio)
        runners = [runner_section(a_r / b, side, float(run["height_ratio"]), float(run["taper_pct"]))
                   for b in branches]
        if side == "cope":
            sprue_len = cope_height - runners[0].d["height"]
        sprue_top = Section.round(math.sqrt(4 * a_s / math.pi))
        sprue_bottom = Section.round(sprue_top.d["d"] + 2 * sprue_len * math.tan(math.radians(draft)))
        ingate = Section.rectangle(a_i / n / t_i, t_i)
        sizes = {"sprue_bottom": sprue_bottom, "sprue_top": sprue_top, "sprue_length": sprue_len,
                 "runners": runners, "ingate": ingate}
        g_vol = gating_volume(sizes) if gating_volume else 0.0
        G_new = W + rho * g_vol * 1e-9
        trail.append({"G_kg": G, "pour_time_s": t_pour, "choke_mm2": choke, "gating_volume_mm3": g_vol})
        if abs(G_new - G) <= rel_tol * G:
            G = G_new
            break
        G = G_new
    else:
        raise SizingError("the poured weight did not settle")

    q = hy.flow_rate(G / rho * 1e9, t_pour)
    return {
        "inputs": {"density_kg_m3": rho, "discharge_coefficient": cd, "pour_time_formula": formula,
                   "gating_ratio": ratio, "basin_head_mm": basin, "cope_height_mm": cope_height,
                   "casting_volume_mm3": casting_volume_mm3, "cavities": n, "thickest_section_mm": thickest_mm,
                   "gate_level_mm": z_gate, "casting_top_mm": z_top, "casting_bottom_mm": z_bottom,
                   "runner_branches": list(branches), "runner_side": side,
                   "runner_height_ratio": float(run["height_ratio"]), "runner_taper_pct": float(run["taper_pct"]),
                   "ingate_thickness_mm": t_i, "draft_deg": draft},
        "castings_kg": W, "poured_kg": G, "yield_pct": 100 * W / G, "pour_time_s": t_pour,
        "head_mm": H, "casting_height_mm": C, "casting_above_gate_mm": P, "effective_head_mm": h_eff,
        "choke_mm2": choke, "sprue_area_mm2": a_s, "runner_area_mm2": a_r, "ingate_area_mm2": a_i,
        "sprue": {"bottom": section_dict(sprue_bottom), "top": section_dict(sprue_top), "length_mm": sprue_len},
        "runner_levels": [{"branches": b, "section": section_dict(s)} for b, s in zip(branches, runners)],
        "ingate": section_dict(ingate),
        "flow_l_s": q * 1e3,
        "velocity_m_s": {"sprue_top": hy.velocity(q, a_s),
                         "runner": [hy.velocity(q / b, s.area) for b, s in zip(branches, runners)],
                         "ingate": hy.velocity(q / n, ingate.area)},
        "iterations": trail,
        "_sizes": sizes,
    }
