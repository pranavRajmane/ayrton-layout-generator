"""Phase 5: a layout built in 3D by the CAD module, on demand.

A routed layout becomes an ordinary design document, made with the same workflow patches the editor
uses (``core.workflow``), so it opens in the editor like any design:

    box          the flask, its parting plane at the drag height (the job's z = 0)
    part         cavity 0 at pattern size (``shrinkage_pct`` from the recipe), the others copies of it
                 moved as the layout says
    sprue        at the layout's sprue, its narrow top the sized diameter and its bottom opened by the draft
                 over its length as placed; the pouring cup on it
                 and the sprue well under it from the recipe (well diameter = ratio x sprue bottom)
    runners      each arm of the routed runner, the first ones dropping from the well, the others
                 branching off their parent's end; widths and height from the sizing
    ingates      from each foot on its runner to the cavity's contact, sized width x thickness

then regenerated (``core.regen``), fused and written as STEP.  Draft from the recipe goes on the
elements that take it (ingates, sprue well, the sprue's bottom); every face is widest at the parting
plane, where the pattern draws out (the cup stands on the cope and opens upward); the runners carry
the recipe's taper; the checks say whether each is at least the recipe's draft.

Checks (each a ``check`` record in the job's ``assemblies``): the regeneration has no errors; the
fused metal is one solid, as valid as the part file itself; its volume is the parts' plus the gating
elements' within ``assembly.volume_tolerance_pct``; a STEP round trip keeps the volume within the same
tolerance; every gating element has at least the recipe's draft.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

import numpy as np

from core import orient, workflow
from core.document import DesignDocument, empty
from core.registry import REGISTRY
from core.regen import regenerate
from layouts.job import JobError, digest

REGISTRY.discover("elements")                       # the element library registers itself on import


class AssemblyError(ValueError):
    pass


def _rigid(rot_deg: float, t) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = orient.rotation((0, 0, 1), rot_deg)
    m[:2, 3] = np.asarray(t, float)[:2]
    return m


def design(job, layout_id: str, out_dir: Path) -> DesignDocument:
    """The CAD-module design document of a routed layout."""
    d = job.data
    lay = next((x for x in d["layouts"] or [] if x["id"] == layout_id), None)
    rec = next((x for x in d["runners"] or [] if x["layout"] == layout_id), None)
    if lay is None or rec is None:
        raise AssemblyError(f"layout '{layout_id}' was not routed (Phase 4)")
    if any(c["mirrored"] for c in lay["cavities"]):
        raise AssemblyError("mirrored cavities are not built yet: the CAD module copies a part by a rigid move")
    v = d["recipe"]["values"]
    lay_v = v["layout"]
    fl = d["inputs"]["flask"]
    s = 1 + float(v["shrinkage_pct"]) / 100
    out_dir = Path(out_dir).resolve()
    data = empty(f"{d['id']}_{layout_id}")
    T = orient.to_matrix(d["inputs"]["parting"]["part_transform"])
    Ts = T.copy()
    Ts[:3, 3] *= s                                  # the module scales the file, then places it
    cav = sorted(lay["cavities"], key=lambda c: c["index"])
    M = [_rigid(c["rotation_deg"], c["translation"]) for c in cav]
    data["part"] = {"source": os.path.relpath(job.part_path().resolve(), out_dir),
                    "units": d["inputs"]["part"]["units"], "shrinkage_pct": float(v["shrinkage_pct"]),
                    "transform": orient.to_dict(M[0] @ Ts)}
    if len(cav) > 1:
        inv0 = np.linalg.inv(M[0])
        data["part"]["cavities"] = [{"id": f"part_{c['index']}", "transform": orient.to_dict(m @ inv0)}
                                    for c, m in zip(cav[1:], M[1:])]
    data["process"] = {**(data.get("process") or {}), "process": d["inputs"]["process"],
                       "alloy": d["inputs"]["alloy"], "preset": d["recipe"]["id"]}
    doc = DesignDocument(data, base_dir=out_dir)

    def apply(patch):
        full = workflow.with_sync(doc, patch)
        if full:
            doc.apply(full)

    H = float(fl["cope_height"]) + float(fl["drag_height"])
    apply(workflow.box_patch(doc, float(fl["length"]), float(fl["width"]), H, float(fl["drag_height"])))
    sz = rec["sizing"]
    S = lay["sprue"]
    d_bot = sz["sprue"]["bottom"]["dims"]["d"]
    apply(workflow.sprue_patch(doc, S[0], S[1], sz["sprue"]["top"]["dims"]["d"], d_bot))
    side = lay_v["runner"]["side"]
    draft = float(lay_v["draft_deg"])
    from core.workflow import runner_widths
    arms = [x for x in rec["segments"] if x["role"] == "runner" and
            np.linalg.norm(np.subtract(x["points"][1][:2], x["points"][0][:2])) > 1e-6]
    S2 = np.asarray(S, float)

    rid_of: dict[str, str] = {}
    pending = list(arms)
    while pending:                                   # parents before children
        progressed = False
        for x in list(pending):
            a, b = np.asarray(x["points"][0][:2]), np.asarray(x["points"][1][:2])
            dims = x["section"]["dims"]
            w = max(dims["top"], dims["bottom"])
            taper = 100 * (1 - min(dims["top"], dims["bottom"]) / w)
            w_par, w_far = runner_widths(side, w, taper)
            if np.linalg.norm(a - S2) < 1e-6:
                branch = None                        # off the sprue (the well, once it is placed)
            else:
                parent = next((p for p in arms if p["id"] in rid_of and
                               np.linalg.norm(np.asarray(p["points"][1][:2]) - a) < 1e-6), None)
                if parent is None:
                    continue
                branch = {"from": rid_of[parent["id"]], "at": [float(a[0]), float(a[1])]}
            apply(workflow.runner_patch(doc, side, w_par, w_far, dims["height"], [[float(b[0]), float(b[1])]],
                                        branch=branch, taper_pct=taper, extension=0.0, level=0.0))
            rid_of[x["id"]] = [e["id"] for e in doc.data["elements"] if e["type"] == "runner"][-1]
            pending.remove(x)
            progressed = True
        if not progressed:
            raise AssemblyError("a runner arm starts neither at the sprue nor at another arm's end")

    gate_side = lay_v["ingate"]["side"]
    for g in (x for x in rec["segments"] if x["role"] == "ingate"):
        foot, q = np.asarray(g["points"][0][:2]), np.asarray(g["points"][1][:2])
        host = _host(arms, foot)
        dims = g["section"]["dims"]
        apply(workflow.ingate_patch(doc, rid_of[host], foot.tolist(), q.tolist(), gate_side,
                                    float(dims["width"]), float(dims["height"])))
        _set(doc, [e["id"] for e in doc.data["elements"] if e["type"] == "ingate"][-1], "draft_deg", draft)

    # the cup on the sprue, then the well under it: the runners' drops move onto the well
    apply(workflow.cup_patch(doc, float(lay_v["cup"]["d_top_mm"]), float(lay_v["cup"]["height_mm"])))
    apply(workflow.well_patch(doc, float(lay_v["well"]["d_ratio"]) * d_bot, float(lay_v["well"]["depth_mm"])))
    _set(doc, "sprue_well", "draft_deg", draft)
    # the sprue as placed runs from under the cup to the runners' floor: its bottom opens by the draft
    # over that length (sizing took the cope height)
    sp = next(e for e in doc.data["elements"] if e["type"] == "sprue")["params"]
    _set(doc, "sprue", "d_bottom", float(sp["d_top"]) + 2 * float(sp["length"]) * math.tan(math.radians(draft)))
    return doc


def _host(arms, foot) -> str:
    """The runner arm an ingate leaves: the one its foot lies on (a leaf arm ends there)."""
    best, bd = None, math.inf
    for x in arms:
        a, b = np.asarray(x["points"][0][:2]), np.asarray(x["points"][1][:2])
        t = np.clip((foot - a) @ (b - a) / max((b - a) @ (b - a), 1e-12), 0, 1)
        dd = float(np.linalg.norm(a + t * (b - a) - foot))
        if dd < bd - 1e-9 or (abs(dd - bd) <= 1e-9 and t > 0.5):      # a tie at a junction: the arm it ends
            best, bd = x["id"], dd
    if bd > 1e-3:
        raise AssemblyError(f"an ingate does not start on a runner ({bd:.3f} mm off)")
    return best


def _shared(solids) -> float:
    """Volume where two of the solids meet (an element's embed in its neighbour, the sprue through the
    runners under it), summed over the pairs whose boxes touch."""
    from core.occ import boxes_touch, common_volume

    out = 0.0
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            if boxes_touch(solids[i], solids[j]):
                try:
                    out += common_volume(solids[i], solids[j])
                except Exception:                                     # noqa: BLE001
                    pass
    return out


def _set(doc, inst: str, name: str, value) -> None:
    i = next(k for k, e in enumerate(doc.data["elements"]) if e["id"] == inst)
    doc.apply([{"op": "add", "path": f"/elements/{i}/params/{name}", "value": value}])


# ---- checks -------------------------------------------------------------------------------------
def draft_checks(doc, draft: float) -> list[dict]:
    """Each gating element's wall draft (degrees, positive: wider toward the parting plane) against
    the recipe's."""
    out = []
    for e in doc.data["elements"]:
        p, t = e["params"], e["type"]
        if t == "pouring_cup":
            ang = math.degrees(math.atan((p["d_top"] - p["d_bottom"]) / 2 / p["height"]))
        elif t == "sprue":                                    # widest at the parting plane: its bottom
            ang = math.degrees(math.atan((p["d_bottom"] - p["d_top"]) / 2 / p["length"]))
        elif t == "sprue_well":
            ang = float(p.get("draft_deg", 0.0))
        elif t == "ingate":
            ang = float(p.get("draft_deg", 0.0))
        elif t == "runner":
            lean = (p["w_parting"] - p["w_far"]) / 2          # wider at the plane: positive
            ang = math.degrees(math.atan(lean / p["height"]))
        else:
            continue
        out.append({"code": f"draft.{e['id']}", "passed": ang >= draft - 1e-6, "value": round(ang, 4),
                    "limit": draft})
    return out


def assemble(job, layout_id: str, out_dir: str | Path | None = None) -> dict:
    """Build one layout in 3D, check it, write ``<job>.<layout>.gating.json`` and ``.step`` beside the
    job (or in out_dir), and record it in the job's ``assemblies``."""
    from build123d import export_step, import_step

    if job.status()["runners"] != "done":
        raise JobError(["Phase 4 (runners) must be done before a layout is built"])
    out = Path(out_dir or (job.path.parent if job.path else Path.cwd()))
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    doc = design(job, layout_id, out)
    stem = f"{job.data['id']}.{layout_id}"
    doc.save(out / f"{stem}.gating.json")
    res = regenerate(doc)
    tol = float(job.data["recipe"]["settings"]["assembly"]["volume_tolerance_pct"])
    checks = []
    errors = [i for i in res.issues if getattr(i.severity, "value", i.severity) == "error"]
    checks.append({"code": "assembly.regenerated", "passed": not errors, "value": len(errors), "limit": 0,
                   **({"message": "; ".join(i.message for i in errors[:3])} if errors else {})})
    fused = res.fused()
    part_valid = res.part.shape.is_valid
    n_sol = len(fused.solids())
    checks.append({"code": "assembly.one_solid", "passed": n_sol == 1, "value": n_sol, "limit": 1})
    ok_valid = fused.is_valid or not part_valid
    checks.append({"code": "assembly.watertight", "passed": bool(ok_valid),
                   **({} if fused.is_valid else {"message": "BRepCheck fails" + ("" if part_valid else
                                                                                   " (as the part file itself does)")})})
    parts = [res.part.shape, *res.part.cavities.values()]
    gating = list(res.gating_solids().values())
    total = sum(x.volume for x in parts) + sum(x.volume for x in gating)
    shared = _shared(parts + gating)
    expect = total - shared
    err = 100 * abs(fused.volume - expect) / expect
    checks.append({"code": "assembly.volume", "passed": err <= tol, "value": round(err, 6), "limit": tol,
                   "message": f"fused {fused.volume:.1f} mm3; parts + gating {total:.1f} less where they "
                              f"meet {shared:.1f} = {expect:.1f} mm3"})
    step = out / f"{stem}.step"
    export_step(fused, str(step))
    back = import_step(str(step))
    err2 = 100 * abs(back.volume - fused.volume) / fused.volume
    checks.append({"code": "assembly.step_round_trip", "passed": err2 <= tol, "value": round(err2, 6), "limit": tol})
    checks += draft_checks(doc, float(job.data["recipe"]["values"]["layout"]["draft_deg"]))
    rec = {"step": step.name, "design": f"{stem}.gating.json",
           "input_hash": digest({"runners": job.input_hash("runners"), "layout": layout_id}),
           "validation": checks}
    job.set_assembly(layout_id, rec)
    return {**rec, "runtime_s": round(time.perf_counter() - t0, 3), "volume_mm3": fused.volume,
            "issues": [i.message for i in res.issues]}
