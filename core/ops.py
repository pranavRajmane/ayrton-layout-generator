"""Document operations that touch several places at once, built as one JSON
Patch so each is a single undo step."""
from __future__ import annotations

import numpy as np

from core import orient
from core.frames import Frame


def _moved_frame(fd: dict, delta: np.ndarray) -> dict:
    return Frame.from_dict(fd).transformed(delta).to_dict()


def set_part_transform(doc, new_t: dict) -> list[dict]:
    """Patch that gives the part a new transform and carries everything fixed to
    it (picked gate faces, element anchors) along by the same rigid move."""
    data = doc.data
    old = orient.to_matrix((data["part"] or {}).get("transform"))
    delta = orient.to_matrix(new_t) @ np.linalg.inv(old)
    ops = [{"op": "add", "path": "/part/transform", "value": new_t}]
    for i, p in enumerate(data["part"].get("ports", [])):
        ops.append({"op": "replace", "path": f"/part/ports/{i}/frame", "value": _moved_frame(p["frame"], delta)})
    for i, e in enumerate(data["elements"]):
        if e.get("anchor"):
            ops.append({"op": "replace", "path": f"/elements/{i}/anchor/frame",
                        "value": _moved_frame(e["anchor"]["frame"], delta)})
    return ops


def orient_op(doc, centre, op: str, **kw) -> list[dict]:
    t = (doc.data["part"] or {}).get("transform")
    if op == "rotate":
        new = orient.rotate(t, kw["axis"], float(kw["angle_deg"]), centre)
    elif op == "up_vector":
        new = orient.up_vector(t, kw["vector"], centre)
    elif op == "face_down":
        new = orient.face_down(t, kw["normal"], centre)
    elif op == "parting_at":
        new = orient.parting_at(t, float(kw["z"]))
    else:
        raise ValueError(f"unknown orientation op '{op}'")
    return set_part_transform(doc, new)


def add_part_port(doc, port: dict) -> list[dict]:
    path = "/part/ports"
    if "ports" not in doc.data["part"]:
        return [{"op": "add", "path": path, "value": [port]}]
    return [{"op": "add", "path": f"{path}/-", "value": port}]


def next_id(existing, stem: str) -> str:
    n = 1
    while f"{stem}_{n}" in existing:
        n += 1
    return f"{stem}_{n}"


# ---- mirror and pattern ------------------------------------------------------------

def reflection(origin, normal) -> np.ndarray:
    """4x4 reflection in the plane through origin with the given normal."""
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    r = np.eye(3) - 2 * np.outer(n, n)
    o = np.asarray(origin, float)
    m = np.eye(4)
    m[:3, :3], m[:3, 3] = r, o - r @ o
    return m


def _map_frame(fd: dict, m: np.ndarray) -> dict:
    """A frame carried by m (rigid or a reflection): origin, normal and up mapped,
    the side recomputed, so the frame stays right-handed."""
    o = m[:3, :3] @ np.asarray(fd["origin"], float) + m[:3, 3]
    return Frame(tuple(o), tuple(m[:3, :3] @ np.asarray(fd["normal"], float)),
                 tuple(m[:3, :3] @ np.asarray(fd["up"], float))).to_dict()


def _map_params(params: dict, cls, m: np.ndarray) -> dict:
    out = dict(params)
    for prm in cls.all_params():
        if prm.kind == "points" and prm.name in out:
            out[prm.name] = [list(m[:3, :3] @ np.asarray(q, float) + m[:3, 3]) for q in out[prm.name]]
    return out


def copy_group(doc, registry, instances: list[str], m: np.ndarray, suffix: str, mirrored: bool = False,
               cavity: dict | None = None) -> list[dict]:
    """Patch adding a copy of the instances (and their connections) carried by m.

    - connections inside the group are copied;
    - a connection from outside into the group is copied with the same upstream
      element (a tap on it gets a new port id and an auto-solved station);
    - a part joint gets a copy of its part port, carried by m;
    - connections out of the group to elements outside it are not copied (an
      inlet takes one feed).
    A reflection rebuilds each element from its parameters in the mirrored
    frame; a handed element needs its own mirrored variant.
    """
    data = doc.data
    group = set(instances)
    els = {e["id"]: e for e in data["elements"]}
    missing = group - set(els)
    if missing:
        raise KeyError(f"no element(s) {sorted(missing)}")
    taken = set(els) | {c["id"] for c in data["connections"]}
    pports = {p["id"]: p for p in (data["part"] or {}).get("ports", [])}
    new_id = {i: i + suffix for i in group}
    clash = [v for v in new_id.values() if v in taken]
    if clash:
        raise ValueError(f"ids already used: {clash}")
    ops = []
    for i in instances:
        e = dict(els[i])
        e["id"] = new_id[i]
        e["params"] = _map_params(e["params"], registry.get(e["type"]), m)
        if e.get("anchor"):
            e["anchor"] = {**e["anchor"], "frame": _map_frame(e["anchor"]["frame"], m)}
        ops.append({"op": "add", "path": "/elements/-", "value": e})
    new_ports = []
    used_taps = {c["from"]["port"] for c in data["connections"]}
    for c in data["connections"]:
        a, b = c["from"]["instance"], c["to"]["instance"]
        if a not in group and b not in group:
            continue
        if a in group and b not in group and b != "part":
            continue
        nc = {**c, "id": c["id"] + suffix, "from": dict(c["from"]), "to": dict(c["to"])}
        if a in group:
            nc["from"]["instance"] = new_id[a]
        elif nc["from"]["port"].startswith("tap"):
            port = nc["from"]["port"] + suffix
            while port in used_taps:
                port += suffix
            used_taps.add(port)
            nc["from"]["port"] = port
            nc["from"].pop("station", None)
            side = nc["from"].get("side")
            if mirrored and side in ("left", "right"):
                nc["from"]["side"] = "right" if side == "left" else "left"
        if b == "part":
            src = pports[c["to"]["port"]]
            pid = src["id"] + suffix
            if pid not in pports and pid not in {p["id"] for p in new_ports}:
                np_ = {**src, "id": pid, "frame": _map_frame(src["frame"], m)}
                if cavity is None:
                    np_.pop("face", None)                     # a reflected point is not on that face index
                new_ports.append(np_)
            nc["to"]["port"] = pid
        else:
            nc["to"]["instance"] = new_id[b]
        ops.append({"op": "add", "path": "/connections/-", "value": nc})
    for p_ in new_ports:
        if not ops_has_ports(doc, ops):
            ops.append({"op": "add", "path": "/part/ports", "value": []})
        ops.append({"op": "add", "path": "/part/ports/-", "value": p_})
    if cavity is not None:
        path = "/part/cavities"
        if "cavities" not in (data["part"] or {}):
            ops.append({"op": "add", "path": path, "value": []})
        ops.append({"op": "add", "path": f"{path}/-", "value": cavity})
    return ops


def ops_has_ports(doc, ops) -> bool:
    return "ports" in (doc.data["part"] or {}) or any(o["path"] == "/part/ports" for o in ops)


def mirror(doc, registry, instances: list[str], origin, normal, suffix: str = "_m") -> list[dict]:
    return copy_group(doc, registry, instances, reflection(origin, normal), suffix, mirrored=True)


def linear_pattern(doc, registry, instances, step, count: int, cavities: bool = False) -> list[dict]:
    """count - 1 more copies, each moved by step from the last."""
    from core.frames import rigid
    ops, d = [], doc
    for k in range(1, count):
        m = rigid(translation=np.asarray(step, float) * k)
        cav = {"id": f"cav_{k + 1}", "transform": orient.to_dict(m)} if cavities else None
        ops += copy_group(_Staged(doc, ops), registry, instances, m, f"_{k + 1}", cavity=cav)
    return ops


def circular_pattern(doc, registry, instances, axis_origin, axis_dir, count: int, total_deg: float = 360.0,
                     cavities: bool = False) -> list[dict]:
    """count copies (the original included) spread over total_deg about the axis."""
    from core.frames import rigid
    ops = []
    step = total_deg / count if abs(total_deg - 360.0) < 1e-9 else total_deg / max(count - 1, 1)
    for k in range(1, count):
        r = orient.rotation(axis_dir, step * k)
        o = np.asarray(axis_origin, float)
        m = rigid(r, o - r @ o)
        cav = {"id": f"cav_{k + 1}", "transform": orient.to_dict(m)} if cavities else None
        ops += copy_group(_Staged(doc, ops), registry, instances, m, f"_{k + 1}", cavity=cav)
    return ops


class _Staged:
    """The document as it will be after the ops so far (so later copies see earlier ones)."""

    def __init__(self, doc, ops):
        import jsonpatch
        self._data = jsonpatch.apply_patch(doc.data, ops, in_place=False) if ops else doc.data

    @property
    def data(self):
        import copy
        return copy.deepcopy(self._data)
