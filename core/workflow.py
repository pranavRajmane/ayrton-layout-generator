"""The guided set-up, step by step, each step one JSON Patch (one undo step):

  1. the molding box: length x width x total height, the parting plane at a
     height above its bottom; the parting plane is z = 0 of the mold frame,
     the box centred on the Z axis.  core.fit suggests how to turn and part the
     part and proposes the box; the user confirms or edits, then may move and
     turn the part in it.
  2. the sprue: a point on the cope top and two diameters; it runs straight
     down to the parting plane, or to the floor of the runner it drops into.
  3. the runners: each drawn on the parting plane from under the sprue through
     the user's points, in the drag or the cope; the sprue drops into every one.
  4. the ingates: each dragged on the parting plane from a point on a runner to
     the part, in the drag or the cope.

The sprue's length and the height of its inlet are not edited by hand: every
patch that goes through ``with_sync`` gets them set to the box top and the
runner floor in the same undo step.
"""
from __future__ import annotations

import copy

import jsonpatch
import numpy as np

from core import orient
from core.ops import next_id, set_part_transform

SPRUE, RUNNER = "sprue", "runner"


class WorkflowError(ValueError):
    pass


# ---- the box ---------------------------------------------------------------------

def box(data: dict) -> dict | None:
    """{"size": [L, W, H], "parting": height of the parting plane over the box bottom,
    "bottom", "top"} or None when the design has no box."""
    f = (data.get("mold") or {}).get("flask") or {}
    if not f.get("size_mm"):
        return None
    L, W, H = map(float, f["size_mm"])
    cz = float((f.get("centre") or (0, 0, 0))[2])
    return {"size": [L, W, H], "parting": H / 2 - cz, "bottom": cz - H / 2, "top": cz + H / 2,
            "centre": [float(x) for x in (f.get("centre") or (0, 0, 0))]}


def box_patch(doc, length: float, width: float, height: float, parting: float | None = None) -> list[dict]:
    if min(length, width, height) <= 0:
        raise WorkflowError("the box needs a positive length, width and height")
    parting = height / 2 if parting is None else float(parting)
    if not 0 < parting < height:
        raise WorkflowError(f"the parting plane must lie inside the box (0 < {parting} < {height})")
    flask = {"size_mm": [float(length), float(width), float(height)], "centre": [0.0, 0.0, height / 2 - parting]}
    return [{"op": "replace", "path": "/mold/flask", "value": flask}]


def fit_patch(doc, move, length: float, width: float, height: float, parting: float, shift=(0, 0, 0),
              carry=()) -> list[dict]:
    """A suggested fit as one undo step: the box, and the part moved into it (move: the suggestion's
    4x4 from where the part is now, then shifted, e.g. to leave the sprue's room at one end)."""
    m = np.asarray(move, float)
    if m.shape != (4, 4):
        raise WorkflowError("a fit's move is a 4x4 matrix")
    t = np.eye(4)
    t[:3, 3] = np.asarray(shift, float)
    return box_patch(doc, length, width, height, parting) + _move_part(doc, t @ m, None, carry)


def _cavity(data: dict, cid: str) -> tuple[int, dict]:
    for i, c in enumerate((data["part"] or {}).get("cavities", [])):
        if c["id"] == cid:
            return i, c
    raise WorkflowError(f"no part '{cid}' in the box")


def _move_part(doc, d: np.ndarray, cavity: str | None, carry=()) -> list[dict]:
    """Patch for the world move d (4x4, rigid) of one part in the box; every other part stays put.
    carry: what sits on that part (from contacts() and the part geometry): top feeders and vents, side
    feeders with their necks, and the ends of ingates drawn by hand move with it (automatic ingates are
    placed again from their runners; feeders and vents are seated again on the moved part)."""
    data = doc.data
    return _follow(data, d, set(carry)) + _move_only(doc, d, cavity)


def _xy(d: np.ndarray, q) -> list[float]:
    m = (d @ np.array([q[0], q[1], q[2] if len(q) > 2 else 0.0, 1.0]))[:3]
    return [float(m[0]), float(m[1]), 0.0]


def _follow(data: dict, d: np.ndarray, carry: set[str]) -> list[dict]:
    out = []
    for i, e in enumerate(data["elements"]):
        if e["id"] not in carry:
            continue
        if e["type"] == "ingate":
            n = len(e["params"]["waypoints"]) - 1
            out.append({"op": "replace", "path": f"/elements/{i}/params/waypoints/{n}",
                        "value": _xy(d, e["params"]["waypoints"][n])})
        elif e["type"] == "neck":                            # on the plane, both ends with its feeder
            out.append({"op": "replace", "path": f"/elements/{i}/params/waypoints",
                        "value": [_xy(d, q) for q in e["params"]["waypoints"]]})
        elif e.get("anchor"):
            o = e["anchor"]["frame"]["origin"]
            if e["type"] == "feeder" and e["params"].get("kind") == "side":
                o2 = _xy(d, o)                               # stands on the parting plane
            else:
                o2 = [float(v) for v in (d @ np.array([*o, 1.0]))[:3]]
            out.append({"op": "replace", "path": f"/elements/{i}/anchor/frame/origin", "value": o2})
    return out


def contacts(data: dict) -> list[tuple[set[str], list[float]]]:
    """What sits on a part, and where it touches it: (ids that move together, contact point) for every
    top feeder and vent (its base), side feeder (with its neck, at the neck's end) and ingate drawn by
    hand (its end). Which part each touches is for the caller to find from the geometry."""
    out = []
    els = {e["id"]: e for e in data["elements"]}
    tee = {c["from"]["instance"]: c["to"]["instance"] for c in data["connections"] if c["joint"] == "tee"}
    for e in data["elements"]:
        p = e["params"]
        if e["type"] in ("vent", "feeder") and e.get("anchor"):
            if e["type"] == "feeder" and p.get("kind") == "side":
                neck = els.get(tee.get(e["id"], ""))
                if neck is not None and neck["type"] == "neck":
                    out.append(({e["id"], neck["id"]}, list(neck["params"]["waypoints"][-1])))
            else:
                out.append(({e["id"]}, list(e["anchor"]["frame"]["origin"])))
        elif e["type"] == "ingate" and p.get("placed") != "auto":
            out.append(({e["id"]}, list(p["waypoints"][-1])))
    return out


def _move_only(doc, d: np.ndarray, cavity: str | None) -> list[dict]:
    data = doc.data
    if cavity is not None:
        i, c = _cavity(data, cavity)
        return [{"op": "replace", "path": f"/part/cavities/{i}/transform",
                 "value": orient.to_dict(d @ orient.to_matrix(c["transform"]))}]
    m = orient.to_matrix((data["part"] or {}).get("transform"))
    ops = set_part_transform(doc, orient.to_dict(d @ m), anchors=False)     # the sprue stays in the box
    inv = np.linalg.inv(d)
    for i, c in enumerate(data["part"].get("cavities", [])):
        if not c.get("source"):                   # a copy is placed relative to the part: hold it where it is
            ops.append({"op": "replace", "path": f"/part/cavities/{i}/transform",
                        "value": orient.to_dict(orient.to_matrix(c["transform"]) @ inv)})
    return ops


def move_part_patch(doc, delta, cavity: str | None = None, carry=()) -> list[dict]:
    d = np.eye(4)
    d[:3, 3] = np.asarray(delta, float)
    return _move_part(doc, d, cavity, carry)


def turn_part_patch(doc, axis, angle_deg: float, centre, cavity: str | None = None, carry=()) -> list[dict]:
    """Turn one part about an axis through its own centre."""
    r = orient.rotation(axis, angle_deg)
    c = np.asarray(centre, float)
    d = np.eye(4)
    d[:3, :3], d[:3, 3] = r, c - r @ c
    return _move_part(doc, d, cavity, carry)


def centre_part_patch(doc, part_bbox, cavity: str | None = None, carry=()) -> list[dict]:
    """Move a part so its bounding box is centred on the box axis, half above and
    half below the parting plane."""
    lo, hi = np.asarray(part_bbox[0], float), np.asarray(part_bbox[1], float)
    return move_part_patch(doc, -(lo + hi) / 2, cavity, carry)


def add_copy_patch(doc, of: str | None, offset) -> list[dict]:
    """Another copy of a part in the box, moved by offset from it."""
    data = doc.data
    cavs = (data["part"] or {}).get("cavities", [])
    t = np.eye(4)
    t[:3, 3] = np.asarray(offset, float)
    new = {"id": next_id({c["id"] for c in cavs}, "part")}
    if of is None:
        new["transform"] = orient.to_dict(t)
    else:
        _, c = _cavity(data, of)
        new.update({k: c[k] for k in ("source", "units", "name") if k in c})
        new["transform"] = orient.to_dict(t @ orient.to_matrix(c["transform"]))
    return _add_cavity(data, new)


def add_file_patch(doc, source: str, translation, units: str = "auto", name: str | None = None) -> list[dict]:
    """Another part file in the box, moved by translation from its file position."""
    cavs = (doc.data["part"] or {}).get("cavities", [])
    new = {"id": next_id({c["id"] for c in cavs}, "part"), "source": source,
           "transform": {"rotation": np.eye(3).tolist(), "translation": [float(x) for x in translation]}}
    if units != "auto":
        new["units"] = units
    if name:
        new["name"] = name
    return _add_cavity(doc.data, new)


def _add_cavity(data: dict, new: dict) -> list[dict]:
    if "cavities" not in (data["part"] or {}):
        return [{"op": "add", "path": "/part/cavities", "value": [new]}]
    return [{"op": "add", "path": "/part/cavities/-", "value": new}]


def remove_part_patch(doc, cavity: str, on_it=()) -> list[dict]:
    """A part (not the main one) out of the box, with what sits on it (on_it: ids from contacts(), its
    feeders, vents and hand-drawn ingates, and what hangs off them)."""
    i, _ = _cavity(doc.data, cavity)

    def fn(d):
        gone = set()
        for inst in on_it:
            if _index(d, inst) is not None:
                gone |= _fed_by(d, inst)
        _remove(d, gone)
        d["part"]["cavities"].pop(i)
    return _edit(doc, fn)


# ---- sprue and runner ---------------------------------------------------------------

def _index(data: dict, inst: str) -> int | None:
    return next((i for i, e in enumerate(data["elements"]) if e["id"] == inst), None)


def _drops(data: dict, sprue: str) -> list[dict]:
    """The connections by which a sprue drops into its runners."""
    return [c for c in data["connections"] if c["joint"] == "drop" and c["from"]["instance"] == sprue]


def _of_type(data: dict, t: str) -> dict | None:
    return next((e for e in data["elements"] if e["type"] == t), None)


def drop_source(data: dict) -> str:
    """What the runners drop from: the sprue well when there is one, else the sprue."""
    w = _of_type(data, "sprue_well")
    return w["id"] if w else SPRUE


def _edit(doc, fn) -> list[dict]:
    """A change made on a copy of the document, as the patch from the document to it."""
    new = copy.deepcopy(doc.data)
    fn(new)
    return jsonpatch.make_patch(doc.data, new).patch


def _drop_conns_from(data: dict, src: str, dst: str) -> None:
    for c in data["connections"]:
        if c["joint"] == "drop" and c["from"]["instance"] == src:
            c["from"]["instance"] = dst


def _remove(data: dict, ids: set[str]) -> None:
    data["connections"] = [c for c in data["connections"] if not ids & {c["from"]["instance"], c["to"]["instance"]}]
    data["elements"] = [e for e in data["elements"] if e["id"] not in ids]


def _down(x: float, y: float, z: float, port: str = "inlet") -> dict:
    """An anchor for something placed straight down from (x, y, z): its inlet there, flow -Z."""
    return {"port": port, "frame": {"origin": [float(x), float(y), float(z)], "normal": [0, 0, -1], "up": [1, 0, 0]}}


# ---- on the sprue: the pouring cup; under it: the filter and the well -------------------------

CUP, WELL, FILTER = "pouring_cup", "sprue_well", "filter"


def cup_patch(doc, d_top: float | None, height: float | None = None) -> list[dict]:
    """The conical pouring cup on the sprue (d_top None: none)."""
    data = doc.data
    sprue = _of_type(data, "sprue")
    if sprue is None:
        raise WorkflowError("place the sprue first")
    if d_top is not None and (d_top <= 0 or not height or height <= 0):
        raise WorkflowError("type the cup's top diameter and height")

    def fn(d):
        _remove(d, {CUP})
        if d_top is None:
            return
        x, y, z = sprue["anchor"]["frame"]["origin"]
        d["elements"].append({"id": CUP, "type": "pouring_cup", "type_version": "0.1", "name": "pouring cup",
                              "params": {"d_top": float(d_top), "d_bottom": float(sprue["params"]["d_top"]),
                                         "height": float(height)}, "anchor": _down(x, y, z)})
        d["connections"].append({"id": "cup", "from": {"instance": CUP, "port": "outlet"},
                                 "to": {"instance": SPRUE, "port": "inlet"}, "joint": "butt"})
    return _edit(doc, fn)


def well_patch(doc, d: float | None, depth: float | None = None, filt: dict | None = None) -> list[dict]:
    """The sprue well under the sprue (d None: none), with a filter over it (filt {shape, size,
    thickness}) or not; the runners drop from the well."""
    data = doc.data
    sprue = _of_type(data, "sprue")
    if sprue is None:
        raise WorkflowError("place the sprue first")
    if d is not None and (d <= 0 or depth is None or depth < 0):
        raise WorkflowError("type the well's diameter and depth")
    if filt is not None and (filt.get("shape") not in ("square", "round") or
                             min(float(filt.get("size") or 0), float(filt.get("thickness") or 0)) <= 0):
        raise WorkflowError("type the filter's size and thickness")
    if filt is not None and d is None:
        raise WorkflowError("a filter sits over the sprue well: give the well too")

    def fn(dd):
        _drop_conns_from(dd, WELL, SPRUE)
        _remove(dd, {WELL, FILTER})
        if d is None:
            return
        x, y, _ = sprue["anchor"]["frame"]["origin"]
        dd["elements"].append({"id": WELL, "type": "sprue_well", "type_version": "0.1", "name": "sprue well",
                               "params": {"d": float(d), "depth": float(depth), "length": 1.0, "embed": 0.0},
                               "anchor": _down(x, y, 0.0)})
        top = WELL
        if filt is not None:
            dd["elements"].append({"id": FILTER, "type": "filter", "type_version": "0.1", "name": "filter",
                                   "params": {"shape": filt["shape"], "size": float(filt["size"]),
                                              "thickness": float(filt["thickness"]), "embed": 0.0},
                                   "anchor": _down(x, y, float(filt["thickness"]))})
            dd["connections"].append({"id": "filter_well", "from": {"instance": FILTER, "port": "outlet"},
                                      "to": {"instance": WELL, "port": "inlet"}, "joint": "stack"})
            top = FILTER
        dd["connections"].append({"id": "sprue_" + top, "from": {"instance": SPRUE, "port": "outlet"},
                                  "to": {"instance": top, "port": "inlet"}, "joint": "stack"})
        _drop_conns_from(dd, SPRUE, WELL)
    return _edit(doc, fn)


# ---- feeders and vents ----------------------------------------------------------------------------

def _up(x: float, y: float, z: float, port: str = "base") -> dict:
    """An anchor for something standing up from (x, y, z): its base there, facing down."""
    return {"port": port, "frame": {"origin": [float(x), float(y), float(z)], "normal": [0, 0, -1], "up": [1, 0, 0]}}


def feeder_patch(doc, kind: str, x: float, y: float, z: float, d: float, height: float,
                 neck: dict | None = None, embed: float | None = None) -> list[dict]:
    """A top feeder standing on the part at (x, y, z), or a side feeder standing on the parting plane at
    (x, y) with its neck {width, thickness, side, end} running from it to the part's edge at end."""
    data = doc.data
    if kind not in ("top", "side"):
        raise WorkflowError("a feeder is top or side")
    if min(d, height) <= 0:
        raise WorkflowError("type the feeder's diameter and height")
    if kind == "side" and (neck is None or min(float(neck.get("width") or 0), float(neck.get("thickness") or 0)) <= 0):
        raise WorkflowError("type the neck's width and thickness")
    ids = {e["id"] for e in data["elements"]}
    cids = {c["id"] for c in data["connections"]}
    fid = next_id(ids, "feeder")

    def fn(dd):
        dd["elements"].append({"id": fid, "type": "feeder", "type_version": "0.1", "name": fid,
                               "params": {"kind": kind, "d": float(d), "height": float(height),
                                          **({"embed": float(embed)} if embed is not None and kind == "top" else {})},
                               "anchor": _up(x, y, z if kind == "top" else 0.0)})
        if kind == "top":
            dd["connections"].append({"id": next_id(cids, "gate"), "from": {"instance": fid, "port": "base"},
                                      "to": {"instance": "part", "port": "gate"}, "joint": "gate"})
            return
        nid = next_id(ids | {fid}, "neck")
        dd["elements"].append({"id": nid, "type": "neck", "type_version": "0.1", "name": nid,
                               "params": {"side": neck.get("side", "cope"), "width": float(neck["width"]),
                                          "thickness": float(neck["thickness"]),
                                          "waypoints": [[float(x), float(y), 0.0],
                                                        [float(neck["end"][0]), float(neck["end"][1]), 0.0]]}})
        tee = next_id(cids, "tee")
        dd["connections"] += [
            {"id": tee, "from": {"instance": fid, "port": "base"}, "to": {"instance": nid, "port": "inlet"},
             "joint": "tee"},
            {"id": next_id(cids | {tee}, "gate"), "from": {"instance": nid, "port": "outlet"},
             "to": {"instance": "part", "port": "gate"}, "joint": "gate"}]
    return _edit(doc, fn)


def vent_patch(doc, x: float, y: float, z: float, d: float, embed: float | None = None) -> list[dict]:
    """A round vent from the part at (x, y, z) straight up to the top of the box."""
    data = doc.data
    b = box(data)
    if b is None:
        raise WorkflowError("make the box first")
    if d <= 0:
        raise WorkflowError("type the vent's diameter")
    if z >= b["top"]:
        raise WorkflowError("the part reaches the top of the box there")
    ids = {e["id"] for e in data["elements"]}
    cids = {c["id"] for c in data["connections"]}
    vid = next_id(ids, "vent")

    def fn(dd):
        dd["elements"].append({"id": vid, "type": "vent", "type_version": "0.1", "name": vid,
                               "params": {"d": float(d), "length": b["top"] - float(z),
                                          **({"embed": float(embed)} if embed is not None else {})},
                               "anchor": _up(x, y, z)})
        dd["connections"].append({"id": next_id(cids, "gate"), "from": {"instance": vid, "port": "base"},
                                  "to": {"instance": "part", "port": "gate"}, "joint": "gate"})
    return _edit(doc, fn)


def seat_patch(doc, inst: str, z: float, embed: float) -> list[dict]:
    """A top feeder or vent seated again (after its part moved): its base at height z, reaching embed
    into the part; a vent still runs to the top of the box."""
    data = doc.data
    i = _index(data, inst)
    if i is None:
        raise WorkflowError(f"no element '{inst}'")
    e = data["elements"][i]
    o = e["anchor"]["frame"]["origin"]
    params = {**e["params"], "embed": float(embed)}
    if e["type"] == "vent":
        b = box(data)
        if b is None or z >= b["top"]:
            raise WorkflowError(f"{inst}: the part reaches the top of the box there")
        params["length"] = b["top"] - float(z)
    return [{"op": "replace", "path": f"/elements/{i}/anchor/frame/origin", "value": [o[0], o[1], float(z)]},
            {"op": "replace", "path": f"/elements/{i}/params", "value": params}]


def neck_end(outline: list, x: float, y: float, d: float, width: float, toward=None) -> list[float]:
    """Where a side feeder's neck, from the feeder standing at (x, y), meets the part: straight toward
    `toward` (x, y) when given, else toward the nearest point of the part's outline."""
    pts = np.array([q for line in outline for q in line], float)
    if not len(pts):
        raise WorkflowError("the part does not reach the parting plane here")
    if toward is None:
        q = pts[np.argmin(np.hypot(pts[:, 0] - x, pts[:, 1] - y))]
        dist = float(np.hypot(q[0] - x, q[1] - y))
        if dist < d / 2:
            raise WorkflowError("the feeder stands on the part: move it beside the part")
        u = (q[:2] - (x, y)) / dist
    else:
        v = np.asarray(toward[:2], float) - (x, y)
        dist = float(np.linalg.norm(v))
        if dist < 1e-6:
            raise WorkflowError("the neck has no direction")
        u = v / dist
    far = float(np.max(np.hypot(pts[:, 0] - x, pts[:, 1] - y))) + 50.0
    hit = _crossing([[x, y, 0.0], [x + u[0] * far, y + u[1] * far, 0.0]], outline, width / 2)
    if hit is None:
        raise WorkflowError("no part beside the feeder")
    if float(np.hypot(hit[1][0] - x, hit[1][1] - y)) < d / 2:
        raise WorkflowError("the feeder stands on the part: move it beside the part")
    return [float(hit[1][0]), float(hit[1][1]), 0.0]


def neck_patch(doc, neck: str, end) -> list[dict]:
    """A side feeder's neck run again to end (after its part moved)."""
    data = doc.data
    i = _index(data, neck)
    if i is None or data["elements"][i]["type"] != "neck":
        raise WorkflowError(f"no neck '{neck}'")
    w = data["elements"][i]["params"]["waypoints"]
    return [{"op": "replace", "path": f"/elements/{i}/params/waypoints",
             "value": [list(map(float, w[0])), [float(end[0]), float(end[1]), 0.0]]}]


def remove_patch(doc, inst: str) -> list[dict]:
    """An element and what it feeds by tees (a side feeder's neck, a runner's branches and ingates)."""
    if _index(doc.data, inst) is None:
        raise WorkflowError(f"no element '{inst}'")
    if inst in (WELL, FILTER):
        return well_patch(doc, None)
    return _edit(doc, lambda d: _remove(d, _fed_by(d, inst)))


def runners(data: dict) -> list[str]:
    """Every runner, in the order they were drawn (from the sprue or branched off another runner)."""
    return [e["id"] for e in data["elements"] if e["type"] == "runner"]


def feeder(data: dict, inst: str) -> tuple[str, str] | None:
    """(element, joint) that feeds inst: the sprue by a drop, or a runner by a tee."""
    c = next((c for c in data["connections"] if c["to"]["instance"] == inst and c["joint"] in ("drop", "tee")), None)
    return (c["from"]["instance"], c["joint"]) if c else None


def sprue_patch(doc, x: float, y: float, d_top: float, d_bottom: float) -> list[dict]:
    """Place the sprue at (x, y) on the cope top, or move it there."""
    data = doc.data
    b = box(data)
    if b is None:
        raise WorkflowError("make the box first")
    L, W, _ = b["size"]
    if abs(x) > L / 2 or abs(y) > W / 2:
        raise WorkflowError(f"({x:.0f}, {y:.0f}) is outside the box")
    if d_top <= 0 or d_bottom <= 0:
        raise WorkflowError("type both sprue diameters")
    anchor = {"port": "inlet", "frame": {"origin": [float(x), float(y), b["top"]], "normal": [0, 0, -1],
                                         "up": [1, 0, 0]}}
    params = {"d_top": float(d_top), "d_bottom": float(d_bottom), "length": b["parting"]}
    i = _index(data, SPRUE)
    if i is None:
        return [{"op": "add", "path": "/elements/-",
                 "value": {"id": SPRUE, "type": "sprue", "type_version": "0.1", "name": "sprue",
                           "params": params, "anchor": anchor}}]
    return [{"op": "replace", "path": f"/elements/{i}/anchor", "value": anchor},
            {"op": "replace", "path": f"/elements/{i}/params/d_top", "value": params["d_top"]},
            {"op": "replace", "path": f"/elements/{i}/params/d_bottom", "value": params["d_bottom"]}]


def level_for(data: dict, runner: str | None, branch: dict | None, side: str) -> float:
    """The level a runner is drawn at when none is given: its own (redrawn; back to the parting plane
    when it changes half), its parent's (a branch on the same side), or the parting plane."""
    if runner is not None and _index(data, runner) is not None:
        p = data["elements"][_index(data, runner)]["params"]
        lv = float(p.get("level", 0.0))
    elif branch is not None and _index(data, branch["from"]) is not None:
        p = data["elements"][_index(data, branch["from"])]["params"]
        lv = float(p.get("level", 0.0)) if p.get("side", "drag") == side else 0.0
    else:
        return 0.0
    return lv if (lv <= 0 if side == "drag" else lv >= 0) else 0.0


def _check_level(data: dict, side: str, height: float, level: float) -> None:
    if side == "drag" and level > 1e-9:
        raise WorkflowError("a runner in the drag goes down from the parting plane, not up")
    if side == "cope" and level < -1e-9:
        raise WorkflowError("a runner in the cope goes up from the parting plane, not down")
    b = box(data)
    if b is None:
        return
    if side == "drag" and level - height < b["bottom"] - 1e-9:
        raise WorkflowError(f"{abs(level):g} mm down, the runner would come out of the bottom of the box "
                            f"(at most {-b['bottom'] - height:g} mm)")
    if side == "cope" and level + height > b["top"] + 1e-9:
        raise WorkflowError(f"{level:g} mm up, the runner would come out of the top of the box "
                            f"(at most {b['top'] - height:g} mm)")


def runner_level_patch(doc, runner: str, level: float) -> list[dict]:
    """Move a runner up or down (level: the height of its parting-plane face, within its own half),
    with everything branched off it: its branch runners and every ingate off them stay level with it.
    The sprue, its well and filter follow in sync; the automatic ingates are cut again by the caller
    against the part at the new height."""
    data = doc.data
    j = _index(data, runner)
    if j is None or data["elements"][j]["type"] != "runner":
        raise WorkflowError(f"no runner '{runner}'")
    side = data["elements"][j]["params"].get("side", "drag")
    level = float(level)
    group = _fed_by(data, runner)
    for e in data["elements"]:
        if e["id"] in group and e["type"] == "runner":
            if e["params"].get("side", "drag") != side:
                raise WorkflowError(f"{e['id']} branches off it in the {e['params'].get('side')}: "
                                    f"a runner and its branches move together only in one half")
            _check_level(data, side, float(e["params"].get("height", 25.0)), level)

    def fn(d):
        for e in d["elements"]:
            if e["id"] not in group or e["type"] not in ("runner", "ingate"):
                continue
            p = e["params"]
            if e["type"] == "runner":
                p["level"] = level
                if p.get("drawn"):
                    p["drawn"] = [list(q[:2]) + [level] for q in p["drawn"]]
            p["waypoints"] = [list(q[:2]) + [level] for q in p["waypoints"]]
    return _edit(doc, fn)


GATE_KEYS = ("width", "thickness", "length", "side")
TAPER_PCT = (10.0, 20.0)            # the range the engineer gave for the runner's taper


def runner_widths(side: str, width: float, taper_pct: float) -> tuple[float, float]:
    """(width at the parting plane, width away from it) for a runner width wide at the parting plane and
    taper_pct narrower at its far face, in the cope and the drag alike: the pattern draws out at the
    parting plane, so every gating face is widest there (the engineer, 2026-11-19; before that the
    drag runner's narrow face was on the plane)."""
    if side not in ("drag", "cope"):
        raise WorkflowError("side is drag or cope")
    lo, hi = TAPER_PCT
    if not lo - 1e-9 <= taper_pct <= hi + 1e-9:
        raise WorkflowError(f"the runner's taper is {lo:g}-{hi:g} %, not {taper_pct:g} %")
    if width <= 0:
        raise WorkflowError("type the runner's width")
    far = width * (1 - taper_pct / 100.0)
    return width, far


def _crossing(chain: list, outline: list, half: float = 0.0) -> tuple[int, np.ndarray, float] | None:
    """The first place, going along chain from its start, where a band half wide either side of it
    touches the part's outline: (segment index, the point on the chain there, distance along that segment)."""
    best = None
    for k, (a, b) in enumerate(zip(chain, chain[1:])):
        a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            continue
        side = np.array([-d[1], d[0]]) / L
        for o in (0.0, half / 2, -half / 2, half, -half) if half > 0 else (0.0,):
            a0 = a + o * side
            for line in outline:
                for p, q in zip(line, line[1:]):
                    p, q = np.asarray(p, float), np.asarray(q, float)
                    e = q - p
                    den = d[0] * e[1] - d[1] * e[0]
                    if abs(den) < 1e-12:
                        continue
                    w = p - a0
                    t = (w[0] * e[1] - w[1] * e[0]) / den          # along the runner segment
                    u = (w[0] * d[1] - w[1] * d[0]) / den          # along the outline segment
                    if 1e-9 < t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                        key = (k, t)
                        if best is None or key < best[0]:
                            best = (key, a + t * d, t * L)
        if best is not None and best[0][0] == k:
            break
    return None if best is None else (best[0][0], best[1], best[2])


def gate_cut(chain: list, outline: list, length: float, half: float = 0.0):
    """Where a runner (chain: its start, then its points) meets the part: the runner cut back by
    length along its last straight run, and the ingate over that length up to the part's edge.
    half: half the width of the runner or ingate, whichever is wider: the band touches the part
    where any of it first does.
    Returns (runner points after the start, ingate start, ingate end) or None if it never meets the part."""
    hit = _crossing(chain, outline, half)
    if hit is None:
        return None
    k, P, run = hit
    if run < length - 1e-9:
        raise WorkflowError(f"the runner's last straight run into the part is {run:.0f} mm; "
                            f"the ingate needs {length:g} mm: draw that run longer")
    a = np.asarray(chain[k][:2], float)
    d = (P - a) / np.linalg.norm(P - a)
    S = P - length * d
    keep = [list(map(float, q[:2])) + [0.0] for q in chain[1:k + 1]]
    if run - length > 0.5:                                   # a stub of runner left on this run
        keep.append([float(S[0]), float(S[1]), 0.0])
    if not keep:
        raise WorkflowError("the ingate would start at the runner's start: draw the runner longer")
    return keep, [float(S[0]), float(S[1]), 0.0], [float(P[0]), float(P[1]), 0.0]


def _auto_gates(data: dict, runner: str) -> list[str]:
    return [g["id"] for g in ingates(data) if g["runner"] == runner and
            data["elements"][_index(data, g["id"])]["params"].get("placed") == "auto"]


def runner_patch(doc, side: str, w_parting: float, w_far: float, height: float, points,
                 runner: str | None = None, branch: dict | None = None,
                 gate: dict | None = None, outline: list | None = None,
                 taper_pct: float | None = None, extension: float | None = None,
                 level: float | None = None) -> list[dict]:
    """Redraw a runner, or (runner None) draw a new one: from the sprue (points after its start under
    the sprue) or, with branch {"from": runner, "at": [x, y]}, off a point on another runner.
    level: the height of its parting-plane face (see level_for when None); its points, and its
    automatic ingate, are at that height.
    With gate {"width", "thickness", "length", "side"} and the part's outline on the parting plane,
    a runner that runs into the part is cut back by the ingate's length and that ingate added there
    (replacing the one placed before); one that does not, or is drawn without gate, keeps none."""
    data = doc.data
    if _index(data, SPRUE) is None:
        raise WorkflowError("place the sprue first")
    if side not in ("drag", "cope"):
        raise WorkflowError("side is drag or cope")
    if min(w_parting, w_far, height) <= 0:
        raise WorkflowError("type the runner's widths and height")
    lv = level_for(data, runner, branch, side) if level is None else float(level)
    _check_level(data, side, float(height), lv)
    pts = [[float(p[0]), float(p[1]), lv] for p in points]

    # where it starts: under the sprue, at its branch point, or (redrawn) as it was
    if runner is None and branch is not None:
        j = _index(data, branch["from"])
        if j is None or data["elements"][j]["type"] != "runner":
            raise WorkflowError(f"no runner '{branch['from']}' to branch from")
        pts = [[float(branch["at"][0]), float(branch["at"][1]), lv]] + pts
        tee_from = branch["from"]
    elif runner is not None and (feeder(data, runner) or (None, None))[1] == "tee":
        tee_from = None                                       # its first point is its branch point: kept
        old = data["elements"][_index(data, runner)]["params"]["waypoints"]
        if pts and np.hypot(pts[0][0] - old[0][0], pts[0][1] - old[0][1]) > 1e-6:
            pts = [old[0]] + pts                              # only the points after the branch point came
    else:
        tee_from = None
    branched = (runner is None and branch is not None) or \
        (runner is not None and (feeder(data, runner) or (None, None))[1] == "tee")
    if len(pts) < (2 if branched else 1):
        raise WorkflowError("the runner needs at least one point after its start")

    drawn = [list(p) for p in pts]                            # as drawn: kept to cut and gate again later
    gate_el = None
    if gate is not None and outline:
        if min(float(gate[k]) for k in ("width", "thickness", "length")) <= 0 or gate.get("side") not in ("drag", "cope"):
            raise WorkflowError("type the ingate's width, thickness and length")
        sx, sy = data["elements"][_index(data, SPRUE)]["anchor"]["frame"]["origin"][:2]
        chain = pts if branched else [[sx, sy, lv]] + pts
        cut = gate_cut(chain, outline, float(gate["length"]), max(float(w_parting), float(gate["width"])) / 2)
        if cut is not None:
            keep, S, P = cut
            keep, S, P = [q[:2] + [lv] for q in keep], S[:2] + [lv], P[:2] + [lv]
            pts = ([chain[0]] + keep) if branched else keep
            gate_el = {"side": gate["side"], "width": float(gate["width"]), "thickness": float(gate["thickness"]),
                       "waypoints": [S, P], "placed": "auto"}

    params = {"side": side, "w_parting": float(w_parting), "w_far": float(w_far), "height": float(height),
              "waypoints": pts, "drawn": drawn, "level": lv,
              "gate_width": float(gate["width"]) if gate else 0.0,
              "gate_thickness": float(gate["thickness"]) if gate else 0.0,
              "gate_length": float(gate["length"]) if gate else 0.0,
              "gate_side": gate["side"] if gate else "drag"}
    if taper_pct is not None:
        params["taper_pct"] = float(taper_pct)
    if extension is not None:
        if extension < 0:
            raise WorkflowError("the runner's extension cannot be negative")
        params["extension"] = float(extension)
    ids = {e["id"] for e in data["elements"]}
    cids = {c["id"] for c in data["connections"]}
    ops: list[dict] = []
    if runner is None:
        rid = RUNNER if RUNNER not in ids else next_id(ids, RUNNER)
        ids.add(rid)
        ops.append({"op": "add", "path": "/elements/-",
                    "value": {"id": rid, "type": "runner", "type_version": "0.1", "name": rid, "params": params}})
        if tee_from:
            cid = next_id(cids, "tee")
            ops.append({"op": "add", "path": "/connections/-",
                        "value": {"id": cid, "from": {"instance": tee_from, "port": "outlet"},
                                  "to": {"instance": rid, "port": "inlet"}, "joint": "tee"}})
        else:
            cid = next_id(cids, "drop")
            ops.append({"op": "add", "path": "/connections/-",
                        "value": {"id": cid, "from": {"instance": drop_source(data), "port": "outlet"},
                                  "to": {"instance": rid, "port": "inlet"}, "joint": "drop"}})
        cids.add(cid)
    else:
        rid = runner
        i = _index(data, runner)
        if i is None or data["elements"][i]["type"] != "runner":
            raise WorkflowError(f"no runner '{runner}'")
        ops.append({"op": "replace", "path": f"/elements/{i}/params",
                    "value": {**data["elements"][i]["params"], **params}})
        # its automatic ingate is placed again, or dropped
        olds = _auto_gates(data, runner)
        if olds and gate_el is not None:                      # same ingate, new place and size
            k = _index(data, olds[0])
            ops.append({"op": "replace", "path": f"/elements/{k}/params",
                        "value": {**data["elements"][k]["params"], **gate_el}})
            gate_el, olds = None, olds[1:]
        gone = set(olds)
        ci = [k for k, c in enumerate(data["connections"]) if gone & {c["from"]["instance"], c["to"]["instance"]}]
        ei = [k for k, e in enumerate(data["elements"]) if e["id"] in gone]
        ops += [{"op": "remove", "path": f"/connections/{k}"} for k in sorted(ci, reverse=True)]
        ops += [{"op": "remove", "path": f"/elements/{k}"} for k in sorted(ei, reverse=True)]
    if gate_el is not None:
        gid = next_id(ids, "ingate")
        tee = next_id(cids, "tee")
        ops += [{"op": "add", "path": "/elements/-",
                 "value": {"id": gid, "type": "ingate", "type_version": "0.1", "name": gid, "params": gate_el}},
                {"op": "add", "path": "/connections/-",
                 "value": {"id": tee, "from": {"instance": rid, "port": "outlet"},
                           "to": {"instance": gid, "port": "inlet"}, "joint": "tee"}},
                {"op": "add", "path": "/connections/-",
                 "value": {"id": next_id(cids | {tee}, "gate"), "from": {"instance": gid, "port": "outlet"},
                           "to": {"instance": "part", "port": "gate"}, "joint": "gate"}}]
    return ops


def regate_patch(doc, outline_for, gate: dict | None = None) -> tuple[list[dict], list[str]]:
    """Every runner drawn with an ingate is cut and gated again from its drawn points against the
    part as it is now (after a part moved), or, with gate {width, thickness, length, side}, every
    runner with that ingate size. outline_for(runner params) gives the part's outline to meet.
    Returns (one patch, a message for each runner it could not gate again: that runner is left as
    it was)."""
    from core.document import DesignDocument
    scratch = DesignDocument(copy.deepcopy(doc.data), base_dir=doc.base_dir)
    failed = []
    for rid in runners(doc.data):
        p = scratch.data["elements"][_index(scratch.data, rid)]["params"]
        if gate is None and (not p.get("drawn") or not p.get("gate_length")):
            continue
        g = dict(gate) if gate is not None else {"width": p["gate_width"], "thickness": p["gate_thickness"],
                                                 "length": p["gate_length"], "side": p.get("gate_side", "drag")}
        pts = p.get("drawn") or p["waypoints"]
        try:
            ops = runner_patch(scratch, p["side"], p["w_parting"], p["w_far"], p["height"], pts, rid,
                               gate=g, outline=outline_for({**p, "gate_thickness": g["thickness"],
                                                            "gate_side": g["side"]}))
            scratch.apply(ops)
        except WorkflowError as e:
            failed.append(f"{rid} could not be gated again ({e}); it was left as it was")
    return jsonpatch.make_patch(doc.data, scratch.data).patch, failed


def ungate_patch(doc) -> list[dict]:
    """Every runner without its automatic ingate: drawn again from its drawn points, ingate size cleared."""
    from core.document import DesignDocument
    scratch = DesignDocument(copy.deepcopy(doc.data), base_dir=doc.base_dir)
    for rid in runners(doc.data):
        p = scratch.data["elements"][_index(scratch.data, rid)]["params"]
        scratch.apply(runner_patch(scratch, p["side"], p["w_parting"], p["w_far"], p["height"],
                                   p.get("drawn") or p["waypoints"], rid))
    return jsonpatch.make_patch(doc.data, scratch.data).patch


def _fed_by(data: dict, inst: str) -> set[str]:
    """inst and everything fed from it by tees (its branches, their ingates, and on)."""
    out, todo = {inst}, [inst]
    while todo:
        x = todo.pop()
        for c in data["connections"]:
            if c["joint"] == "tee" and c["from"]["instance"] == x and c["to"]["instance"] not in out:
                out.add(c["to"]["instance"])
                todo.append(c["to"]["instance"])
    return out


def remove_runner_patch(doc, runner: str) -> list[dict]:
    """A runner, everything branched off it (runners and ingates), and every connection touching them."""
    data = doc.data
    if _index(data, runner) is None:
        raise WorkflowError(f"no runner '{runner}'")
    gone = _fed_by(data, runner)
    ci = [k for k, c in enumerate(data["connections"]) if gone & {c["from"]["instance"], c["to"]["instance"]}]
    ei = [k for k, e in enumerate(data["elements"]) if e["id"] in gone]
    return [{"op": "remove", "path": f"/connections/{k}"} for k in sorted(ci, reverse=True)] + \
        [{"op": "remove", "path": f"/elements/{k}"} for k in sorted(ei, reverse=True)]


def ingates(data: dict) -> list[dict]:
    """[{"id", "runner"}] for every ingate, in the order they were drawn."""
    feeds = {c["to"]["instance"]: c["from"]["instance"] for c in data["connections"] if c["joint"] == "tee"}
    return [{"id": e["id"], "runner": feeds.get(e["id"])} for e in data["elements"] if e["type"] == "ingate"]


def ingate_patch(doc, runner: str, start, end, side: str, width: float, thickness: float,
                 ingate: str | None = None) -> list[dict]:
    """Draw an ingate from start (on the runner) to end (at the part), on the parting plane,
    or (ingate given) redraw that one."""
    data = doc.data
    j = _index(data, runner)
    if j is None or data["elements"][j]["type"] != "runner":
        raise WorkflowError(f"no runner '{runner}'")
    if side not in ("drag", "cope"):
        raise WorkflowError("side is drag or cope")
    if min(width, thickness) <= 0:
        raise WorkflowError("type the ingate's width and thickness")
    lv = float(data["elements"][j]["params"].get("level", 0.0))       # level with its runner
    a, b = [float(start[0]), float(start[1]), lv], [float(end[0]), float(end[1]), lv]
    if np.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
        raise WorkflowError("drag the ingate from the runner to the part")
    params = {"side": side, "width": float(width), "thickness": float(thickness), "waypoints": [a, b]}
    if ingate is None:
        ids = {e["id"] for e in data["elements"]}
        gid = next_id(ids, "ingate")
        cids = {c["id"] for c in data["connections"]}
        tee = next_id(cids, "tee")
        return [{"op": "add", "path": "/elements/-",
                 "value": {"id": gid, "type": "ingate", "type_version": "0.1", "name": gid, "params": params}},
                {"op": "add", "path": "/connections/-",
                 "value": {"id": tee, "from": {"instance": runner, "port": "outlet"},
                           "to": {"instance": gid, "port": "inlet"}, "joint": "tee"}},
                {"op": "add", "path": "/connections/-",
                 "value": {"id": next_id(cids | {tee}, "gate"), "from": {"instance": gid, "port": "outlet"},
                           "to": {"instance": "part", "port": "gate"}, "joint": "gate"}}]
    i = _index(data, ingate)
    if i is None or data["elements"][i]["type"] != "ingate":
        raise WorkflowError(f"no ingate '{ingate}'")
    ops = [{"op": "replace", "path": f"/elements/{i}/params", "value": {**data["elements"][i]["params"], **params}}]
    for k, c in enumerate(data["connections"]):                    # moved onto another runner
        if c["joint"] == "tee" and c["to"]["instance"] == ingate and c["from"]["instance"] != runner:
            ops.append({"op": "replace", "path": f"/connections/{k}/from/instance", "value": runner})
    return ops


def remove_element_patch(doc, inst: str) -> list[dict]:
    """An element and every connection touching it."""
    data = doc.data
    i = _index(data, inst)
    if i is None:
        raise WorkflowError(f"no element '{inst}'")
    ci = [k for k, c in enumerate(data["connections"]) if inst in (c["from"]["instance"], c["to"]["instance"])]
    return [{"op": "remove", "path": f"/connections/{k}"} for k in sorted(ci, reverse=True)] + \
        [{"op": "remove", "path": f"/elements/{i}"}]


# ---- keeping the sprue on the box and the runner ---------------------------------------

def sync(data: dict) -> list[dict]:
    """Patch that keeps what stands on the sprue's axis in its place: the cup's top on the box top (its
    bottom diameter the sprue's top), the sprue under it, the filter on the parting plane and the well
    under it (down past the deepest runner by its depth) or, without a well, the sprue's bottom on the
    deepest runner floor; and every vent reaching the box top."""
    b = box(data)
    if b is None:
        return []
    out: list[dict] = []
    idx = {e["id"]: i for i, e in enumerate(data["elements"])}

    def put(i: int, path: str, value) -> None:
        cur = data["elements"][i]
        for k in path.split("/"):
            cur = cur.get(k) if isinstance(cur, dict) else None
        same = (cur is not None and (np.allclose(cur, value, atol=1e-9) if isinstance(value, list)
                                      else abs(float(cur) - float(value)) <= 1e-9))
        if not same:
            out.append({"op": "replace" if cur is not None else "add", "path": f"/elements/{i}/{path}", "value": value})

    sprue = _of_type(data, "sprue")
    if sprue is not None and sprue.get("anchor"):
        x, y = sprue["anchor"]["frame"]["origin"][:2]
        top = b["top"]
        cup, well, filt = _of_type(data, "pouring_cup"), _of_type(data, "sprue_well"), _of_type(data, "filter")
        if cup is not None:
            put(idx[cup["id"]], "anchor/frame/origin", [x, y, top])
            put(idx[cup["id"]], "params/d_bottom", float(sprue["params"]["d_top"]))
            top -= float(cup["params"]["height"])
        # the deepest runner the stack drops into (floor: its bottom face), and the parting-plane face
        # of the deepest one (deck: the filter sits there, over the well)
        # (a cope runner raised off the plane: the sprue ends on its floor; no runner yet: the plane)
        floors, deck = [], 0.0
        for c in _drops(data, drop_source(data)):
            j = idx.get(c["to"]["instance"])
            if j is not None and data["elements"][j]["type"] == "runner":
                rp = data["elements"][j]["params"]
                lv = float(rp.get("level", 0.0))
                drag = rp.get("side", "drag") == "drag"
                floors.append(lv - float(rp.get("height", 25.0)) if drag else lv)
                deck = min(deck, lv) if drag else deck
        floor = min(floors) if floors else 0.0
        bottom = floor
        if well is not None:
            wtop = deck if filt is not None else floor
            if filt is not None:
                t = float(filt["params"]["thickness"])
                put(idx[filt["id"]], "anchor/frame/origin", [x, y, deck + t])
                bottom = deck + t
            else:
                bottom = wtop
            put(idx[well["id"]], "anchor/frame/origin", [x, y, wtop])
            put(idx[well["id"]], "params/length", max(wtop - (floor - float(well["params"]["depth"])), 1.0))
        i = idx[sprue["id"]]
        put(i, "anchor/frame/origin", [x, y, top])
        put(i, "params/length", top - bottom)
    for e in data["elements"]:
        if e["type"] == "runner":
            wp = _extended(data, e)
            if wp is not None:
                put(idx[e["id"]], "params/waypoints", wp)
    for e in data["elements"]:
        if e["type"] == "vent" and e.get("anchor"):
            put(idx[e["id"]], "params/length", max(b["top"] - float(e["anchor"]["frame"]["origin"][2]), 0.5))
    return out


def _cut_at(chain: list, s: float) -> list:
    """The polyline chain up to arc length s, or carried on straight past its end to s."""
    pts = [np.asarray(q[:2], float) for q in chain]
    out, run = [pts[0]], 0.0
    for a, b in zip(pts, pts[1:]):
        L = float(np.linalg.norm(b - a))
        if run + L >= s - 1e-9:
            out.append(a + (b - a) * (s - run) / L if L > 1e-12 else b)
            return out
        out.append(b)
        run += L
    a, b = pts[-2], pts[-1]
    out[-1] = b + (b - a) / np.linalg.norm(b - a) * (s - run)
    return out


def _station(chain: list, q) -> float:
    """Arc length along chain of the point of it nearest q."""
    q = np.asarray(q[:2], float)
    best, run = (np.inf, 0.0), 0.0
    for a, b in zip(chain, chain[1:]):
        a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-12:
            continue
        t = float(np.clip((q - a) @ d / (L * L), 0.0, 1.0))
        dist = float(np.linalg.norm(a + t * d - q))
        if dist < best[0] - 1e-9:
            best = (dist, run + t * L)
        run += L
    return best[1]


def _extended(data: dict, r: dict) -> list | None:
    """A runner with an extension and ingates (or runners) drawn off its side: its points, ending the
    extension past the last of them along its drawn path.  None: nothing to change (no extension, no
    ingate off it yet, or its ingate is the automatic one at its end)."""
    ext = float(r["params"].get("extension") or 0.0)
    if ext <= 0:
        return None
    rid = r["id"]
    tees = [c["to"]["instance"] for c in data["connections"] if c["joint"] == "tee" and c["from"]["instance"] == rid]
    kids = [e for e in data["elements"] if e["id"] in tees]
    if not kids or any(k["type"] == "ingate" and k["params"].get("placed") == "auto" for k in kids):
        return None
    base = r["params"].get("drawn") or r["params"]["waypoints"]
    feed = next((c for c in data["connections"] if c["to"]["instance"] == rid and c["joint"] in ("drop", "tee")), None)
    branched = feed is not None and feed["joint"] == "tee"
    sprue = _of_type(data, SPRUE)
    if not branched and (sprue is None or not sprue.get("anchor")):
        return None
    chain = [list(b) for b in base] if branched else [sprue["anchor"]["frame"]["origin"][:2]] + [list(b) for b in base]
    last = max(_station(chain, k["params"]["waypoints"][0]) for k in kids)
    pts = _cut_at(chain, last + ext)
    pts = pts if branched else pts[1:]
    lv = float(r["params"].get("level", 0.0))
    return [[float(q[0]), float(q[1]), lv] for q in pts]


def with_sync(doc, ops: list[dict]) -> list[dict]:
    """ops plus the sync they make necessary, as one patch."""
    trial = jsonpatch.apply_patch(copy.deepcopy(doc.data), ops)
    return list(ops) + sync(trial)
