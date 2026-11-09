"""The calculator engine: reads the regenerated design (the flow graph, the
port sections, the part) and the process preset, and works out what a method
engineer checks by hand.  Every number it cannot get (a preset value, a design
input) is listed as missing, never guessed.

  readouts     casting, gating and poured volume and weight; yield
  pour         pour time (override, or the preset's formula in W, G, t), flow rate
  head         metal head from the geometry (pouring inlet + basin head - gate level),
               effective head by the chosen method
  choke        required choke area (Bernoulli) against the design's choke on each path
  planes       flow, velocity and Reynolds number at every measurement plane, the
               flow split across ingates in proportion to their areas
  paths        every path from the pouring inlet to the part: its sections, its choke,
               the gating ratio read off its sprue, runner and ingate members
  driven       values for parameters declared driven_by="calculators.<key>"
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from calculators import hydraulics as hy
from calculators.formula import FormulaError, evaluate
from calculators.presets import missing
from core.element import Severity, ValidationIssue

ROLES = ("sprue", "runner", "ingate")


@dataclass
class CalcResult:
    inputs: dict = field(default_factory=dict)
    readouts: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    planes: list[dict] = field(default_factory=list)
    paths: list[dict] = field(default_factory=list)
    driven: dict[str, dict] = field(default_factory=dict)      # instance -> {param: value}
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"inputs": self.inputs, "readouts": self.readouts, "outputs": self.outputs, "planes": self.planes,
                "paths": self.paths, "driven": self.driven, "issues": [i.to_dict() for i in self.issues]}


def _need(cr: CalcResult, preset, path: str, why: str):
    if preset is None:
        cr.issues.append(missing(None, path, why))
        return None
    return preset.need(path, why, cr.issues)


def compute(res) -> CalcResult:
    cr = CalcResult()
    proc = res.doc.data["process"]
    preset = res.preset
    if res.graph is None:
        return cr
    rho = _need(cr, preset, "density_kg_m3", "weights, flow and choke area")
    cd = _need(cr, preset, "discharge_coefficient", "the choke area")
    mu = _need(cr, preset, "viscosity_pa_s", "Reynolds numbers")
    cr.inputs.update(density_kg_m3=rho, discharge_coefficient=cd, viscosity_pa_s=mu,
                     section_thickness_mm=proc.get("section_thickness_mm"), basin_head_mm=proc.get("basin_head_mm"),
                     head_method=proc.get("head_method"), gating_system=proc.get("gating_system"),
                     preset=preset.id if preset else None)

    # ---- readouts ------------------------------------------------------------------------
    n_cav = 1 + (len(res.part.cavities) if res.part else 0)
    casting = res.part.volume_mm3 * n_cav if res.part else 0.0
    gating = sum(s.volume for s in res.gating_solids(tagged=True).values())
    poured = casting + gating
    ro = cr.readouts
    ro.update(casting_volume_mm3=casting, gating_volume_mm3=gating, poured_volume_mm3=poured, cavities=n_cav,
              yield_pct=100 * casting / poured if poured else None)
    if rho:
        ro.update(casting_kg=casting * 1e-9 * rho, gating_kg=gating * 1e-9 * rho, poured_kg=poured * 1e-9 * rho)

    # ---- pour time and flow --------------------------------------------------------------
    t_pour = proc.get("pour_time_s")
    if t_pour is None:
        formula = _need(cr, preset, "pour_time_formula", "the pour time")
        t_sec = proc.get("section_thickness_mm")
        if t_sec is None:
            cr.issues.append(ValidationIssue("calc.input_missing", Severity.INFO,
                                             "the pour time needs the governing section thickness "
                                             "(process.section_thickness_mm)", value="section_thickness_mm"))
        if formula and t_sec is not None and rho:
            try:
                t_pour = evaluate(formula, W=ro["casting_kg"], G=ro["poured_kg"], t=t_sec)
                cr.outputs["pour_time_source"] = f"preset formula: {formula}"
            except FormulaError as e:
                cr.issues.append(ValidationIssue("calc.formula", Severity.ERROR, str(e), value=formula))
    else:
        cr.outputs["pour_time_source"] = "design override"
    q = None
    if t_pour:
        q = hy.flow_rate(poured, t_pour)
        cr.outputs.update(pour_time_s=t_pour, flow_m3_s=q, flow_l_s=q * 1e3)
        if rho:
            cr.outputs["mass_rate_kg_s"] = ro["poured_kg"] / t_pour

    # ---- head ----------------------------------------------------------------------------
    pours = res.pouring_ports()
    gates = [(c, res.built[c.frm.instance]) for c in res.graph.connections
             if c.to_part and c.frm.instance in res.built]
    h_eff = None
    if len(pours) == 1 and gates and res.part:
        z_pour = pours[0][1].frame.origin[2]
        gate_ports = [b.port(res.frm(c)) for c, b in gates]
        z_gate = float(np.mean([g.frame.origin[2] for g in gate_ports]))
        basin = proc.get("basin_head_mm")
        if basin is None:
            cr.issues.append(ValidationIssue("calc.input_missing", Severity.INFO,
                                             "no basin head (process.basin_head_mm): the head is measured from "
                                             "the pouring inlet", value="basin_head_mm"))
        H = z_pour + (basin or 0.0) - z_gate
        C = res.part.bbox[1][2] - res.part.bbox[0][2]
        P = res.part.bbox[1][2] - z_gate
        cr.outputs.update(head_mm=H, gate_level_z_mm=z_gate, casting_height_mm=C, casting_above_gate_mm=P)
        method = proc.get("head_method")
        if method is None:
            cr.issues.append(ValidationIssue("calc.input_missing", Severity.INFO,
                                             "choose how the effective head is taken (process.head_method: "
                                             "top, bottom or parting)", value="head_method"))
        else:
            try:
                h_eff = hy.effective_head(method, H, P, C)
                cr.outputs["effective_head_mm"] = h_eff
            except ValueError as e:
                cr.issues.append(ValidationIssue("calc.head", Severity.ERROR, str(e), value=method))

    # ---- required choke ------------------------------------------------------------------
    if t_pour and rho and cd and h_eff:
        try:
            a = hy.choke_area(ro["poured_kg"], t_pour, rho, cd, h_eff)
            cr.outputs["required_choke_mm2"] = a
            cr.outputs["choke_velocity_m_s"] = cd * hy.free_fall_velocity(h_eff)
        except ValueError as e:
            cr.issues.append(ValidationIssue("calc.choke", Severity.ERROR, str(e), value=h_eff))

    # ---- flow split and planes -----------------------------------------------------------
    gate_area = {}
    for c, b in gates:
        sec = b.port(res.frm(c)).section
        if sec is not None:
            gate_area[c.id] = sec.area
    total_gate = sum(gate_area.values())

    def downstream_share(inst: str, seen=None) -> float:
        seen = seen or set()
        if inst in seen:
            return 0.0
        seen.add(inst)
        share = 0.0
        for c in res.graph.outgoing(inst):
            if c.to_part:
                share += gate_area.get(c.id, 0.0) / total_gate if total_gate else 0.0
            else:
                share += downstream_share(c.to.instance, seen)
        return share

    for pl in res.measurement_planes():
        sec = pl["section"]
        conn = next((c for c in res.graph.connections if c.id == pl["connection"]), None)
        if conn is None:
            share = 1.0
        elif conn.to_part:
            share = gate_area.get(conn.id, 0.0) / total_gate if total_gate else 0.0
        else:
            share = downstream_share(conn.to.instance)
        row = {"name": pl["name"], "connection": pl["connection"], "z_mm": pl["origin"][2],
               "area_mm2": sec["area"] if sec else None, "flow_share": share,
               "role": _role(res, conn, pl["name"])}
        if q and sec:
            v = hy.velocity(q * share, sec["area"])
            row["velocity_m_s"] = v
            if rho and mu:
                from core.element import Section
                s_obj = Section(sec["shape"], tuple(sec["dims"].items()))
                row["reynolds"] = hy.reynolds(rho, v, hy.hydraulic_diameter(sec["area"], s_obj.perimeter), mu)
        cr.planes.append(row)

    # ---- paths: choke and ratio ----------------------------------------------------------
    for path in res.graph.paths():
        insts = [path[0].frm.instance] + [c.to.instance for c in path if not c.to_part]
        names = {c.id for c in path}
        rows = [r for r in cr.planes if r["connection"] in names or (
            r["connection"] is None and r["name"].split(".")[0] == insts[0])]
        rows = [r for r in rows if r["area_mm2"]]
        entry = {"elements": insts, "ends_at_part": path[-1].to_part, "planes": [r["name"] for r in rows]}
        if rows:
            ch = min(rows, key=lambda r: r["area_mm2"])
            entry.update(choke_plane=ch["name"], choke_mm2=ch["area_mm2"], choke_role=ch["role"])
        members = {}
        for role in ROLES:
            areas = [x.section.area for i in insts if i in res.built and res.built[i].element.gating_role == role
                     for x in res.built[i].ports() if x.section is not None]
            if areas:
                members[role] = min(areas)
        if len(members) == 3:
            base = members["sprue"]
            entry["ratio"] = [round(members[r] / base, 3) for r in ROLES]
        entry["members_mm2"] = members
        cr.paths.append(entry)

    # ---- ratio propagation and driven values --------------------------------------------
    req = cr.outputs.get("required_choke_mm2")
    system = proc.get("gating_system")
    ratio = None
    if preset is not None:
        key = f"gating_ratios.options.{system}" if system else "gating_ratios.default"
        ratio = preset.need(key, "propagating areas from the gating ratio", cr.issues)
    counts = {r: sum(1 for b in res.built.values() if b.element.gating_role == r) for r in ROLES}
    if req and ratio:
        totals = hy.ratio_areas(req, ratio)
        cr.outputs["ratio_used"] = ratio
        for r, tot in zip(ROLES, totals):
            cr.outputs[f"{r}_area_total_mm2"] = tot
            if counts[r]:
                cr.outputs[f"{r}_area_each_mm2"] = tot / counts[r]
    keys = {"calculators.choke_area": req,
            **{f"calculators.{r}_area": cr.outputs.get(f"{r}_area_each_mm2") for r in ROLES}}
    basin = proc.get("basin_head_mm")
    for inst, b in res.built.items():
        vals = {}
        for prm in b.element.all_params():
            if not prm.driven_by:
                continue
            v = keys.get(prm.driven_by)
            if prm.driven_by == "calculators.sprue_top_area" and b.element.gating_role == "sprue":
                ports = b.ports()
                bottom = min(ports, key=lambda x: x.frame.origin[2])
                top = max(ports, key=lambda x: x.frame.origin[2])
                if basin and bottom.section:
                    v = hy.sprue_top_area(bottom.section.area, basin,
                                          top.frame.origin[2] - bottom.frame.origin[2])
            if v is not None:
                vals[prm.name] = v
        if vals:
            cr.driven[inst] = vals
    return cr


def _role(res, conn, name: str) -> str | None:
    inst = name.split(".")[0]
    b = res.built.get(inst)
    return b.element.gating_role if b else None
