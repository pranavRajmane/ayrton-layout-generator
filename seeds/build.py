"""A seed design built in the CAD module: a design document made with the same
workflow patches the editor uses (box, cavities, sprue, runners with their
automatic ingates, feeders and necks), then regenerated and checked.

Not built yet (the CAD module has no element for them): sleeves and chills.
A design holding one is refused with that reason.  The part is built at casting
size (shrinkage_pct 0): the generator placed everything on the casting.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import workflow
from core.document import DesignDocument, empty
from core.mold import seat, section_outline
from core.regen import RegenResult, regenerate
from core.element import EMBED

UNBUILDABLE = {"sleeve", "chill"}


class BuildError(RuntimeError):
    pass


def _outline(metal, side: str, depth: float) -> list:
    """Like the editor: the part's cut just above and below the plane and through the depth of what
    runs to it, so a neck ends exactly where it first meets the part."""
    sign = 1.0 if side == "cope" else -1.0
    zs = {sign * min(max(depth * k / 12, 1e-3), depth - 1e-3) for k in range(13)}
    out = section_outline(metal, 1e-3) + section_outline(metal, -1e-3)
    for z in sorted(zs):
        out += section_outline(metal, z)
    return out


def _neck_end(metal, q: dict, n: dict) -> list[float]:
    """The neck's end on the real part (the spec's end is on the voxel edge, up to half a voxel off)."""
    return workflow.neck_end(_outline(metal, n["side"], n["thickness"]), n["start"][0], n["start"][1],
                             q["d"], n["width"], n["end"])


def build_design(design: dict, part_file: str | Path, profile, out_dir: str | Path | None = None
                 ) -> tuple[DesignDocument, RegenResult]:
    bad = sorted({e["type"] for e in design["elements"]} & UNBUILDABLE)
    if bad:
        raise BuildError(f"the CAD module has no element for {', '.join(bad)}")
    part_file = Path(part_file).resolve()
    base = Path(out_dir or part_file.parent).resolve()
    data = empty(design["id"])
    offs = design["cavity_layout"]["offsets"]
    o0 = offs[0]
    data["part"] = {"source": os.path.relpath(part_file, base), "shrinkage_pct": 0.0,
                    "transform": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation": list(map(float, o0))}}
    doc = DesignDocument(data, base_dir=base)

    def apply(patch):
        full = workflow.with_sync(doc, patch)
        if full:
            doc.apply(full)

    L, W, H = profile.flask_size_mm
    apply(workflow.box_patch(doc, L, W, H, profile.parting_height_mm))
    for off in offs[1:]:
        apply(workflow.add_copy_patch(doc, None, [off[i] - o0[i] for i in range(3)]))
    metal = regenerate(doc).part.metal

    els = {e["id"]: e for e in design["elements"]}
    sp = els["sprue"]["params"]
    apply(workflow.sprue_patch(doc, sp["x"], sp["y"], sp["d_top"], sp["d_bottom"]))
    for e in design["elements"]:
        if e["type"] != "runner":
            continue
        q = e["params"]
        apply(workflow.runner_patch(doc, q["side"], q["w_parting"], q["w_far"], q["height"],
                                    [p[:2] for p in q["waypoints"][1:]], taper_pct=q["taper_pct"],
                                    extension=q["extension"]))
        rid = [x["id"] for x in doc.data["elements"] if x["type"] == "runner"][-1]
        for g in (x["params"] for x in design["elements"] if x["type"] == "ingate" and x["params"]["runner"] == e["id"]):
            apply(workflow.ingate_patch(doc, rid, g["start"][:2], g["at"][:2], g["side"], g["width"], g["thickness"]))
    for e in design["elements"]:
        if e["type"] != "riser":
            continue
        q = e["params"]
        if q["style"] == "top":
            st = seat(metal, q["x"], q["y"], q["d"])
            if st is None:
                raise BuildError(f"{e['id']}: no part under ({q['x']:.0f}, {q['y']:.0f})")
            apply(workflow.feeder_patch(doc, "top", q["x"], q["y"], st["top"], q["d"], q["height"],
                                        embed=st["drop"] + EMBED.default))
            continue
        necks = [n["params"] for n in design["elements"] if n["type"] == "neck" and n["params"]["riser"] == e["id"]]
        necks = [{**n, "end": _neck_end(metal, q, n)} for n in necks]
        n0 = necks[0]
        apply(workflow.feeder_patch(doc, "side", q["x"], q["y"], 0.0, q["d"], q["height"],
                                    neck={"width": n0["width"], "thickness": n0["thickness"], "side": n0["side"],
                                          "end": n0["end"]}))
        if len(necks) > 1:                       # a feeder shared across two cavities: two necks that
            apply(_start_off_centre(doc, q))     # start a quarter diameter out, so they do not overlap
            for n in necks[1:]:
                apply(_second_neck(doc, n, q))
    res = regenerate(doc)
    return doc, res


def _off_centre(start, end, d: float) -> list[float]:
    import numpy as np
    a, b = np.asarray(start[:2], float), np.asarray(end[:2], float)
    u = (b - a) / max(np.linalg.norm(b - a), 1e-9)
    p = a + u * d / 4
    return [float(p[0]), float(p[1]), 0.0]


def _start_off_centre(doc, q: dict) -> list[dict]:
    data = doc.data
    i = max(k for k, e in enumerate(data["elements"]) if e["type"] == "neck")
    w = data["elements"][i]["params"]["waypoints"]
    return [{"op": "replace", "path": f"/elements/{i}/params/waypoints/0", "value": _off_centre(w[0], w[-1], q["d"])}]


def _second_neck(doc, n: dict, q: dict) -> list[dict]:
    """Another neck from the feeder added last, joined the way feeder_patch joins the first."""
    from core.ops import next_id
    data = doc.data
    fid = [e["id"] for e in data["elements"] if e["type"] == "feeder"][-1]
    ids = {e["id"] for e in data["elements"]}
    cids = {c["id"] for c in data["connections"]}
    nid = next_id(ids, "neck")
    tee = next_id(cids, "tee")
    return [{"op": "add", "path": "/elements/-",
             "value": {"id": nid, "type": "neck", "type_version": "0.1", "name": nid,
                       "params": {"side": n["side"], "width": float(n["width"]), "thickness": float(n["thickness"]),
                                  "waypoints": [_off_centre(n["start"], n["end"], q["d"]), list(map(float, n["end"]))]}}},
            {"op": "add", "path": "/connections/-",
             "value": {"id": tee, "from": {"instance": fid, "port": "base"}, "to": {"instance": nid, "port": "inlet"},
                       "joint": "tee"}},
            {"op": "add", "path": "/connections/-",
             "value": {"id": next_id(cids | {tee}, "gate"), "from": {"instance": nid, "port": "outlet"},
                       "to": {"instance": "part", "port": "gate"}, "joint": "gate"}}]
