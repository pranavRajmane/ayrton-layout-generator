"""HTTP API for the editor.  The kernel stays here; the browser gets glTF.

    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from build123d import export_gltf
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response

from bridge.ayrton import ExportError, export
from core.document import DesignDocument, DocumentError, empty
from core.element import EMBED
from core.occ import baked
from core import fit, moldcheck, ops, workflow
from core.pick import gate_port, pick_face
from calculators.apply import propagation_patch
from calculators.presets import list_presets
from core.regen import RegenResult, regenerate
from core.registry import REGISTRY

REGISTRY.discover("elements")
if os.environ.get("AYRTON_GATING_FIXTURES"):              # framework test elements, for demos before the library exists
    REGISTRY.discover("tests.fixture_elements")
_LOCKS: dict[str, threading.Lock] = {}


def _one_change_at_a_time(request: Request):
    """Requests that change a design session run one after another: two edits sent together (a drag
    and a typed value, say) must not interleave on the document and its regenerated result. Mesh
    downloads (GET) do not wait: they only read what a finished change left."""
    sid = request.path_params.get("sid")
    if request.method != "POST" or not sid:
        yield
        return
    lock = _LOCKS.setdefault(sid, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


app = FastAPI(title="Ayrton gating", dependencies=[Depends(_one_change_at_a_time)])
MESH_TOL = dict(linear_deflection=0.1, angular_deflection=0.3)


@dataclass
class Session:
    doc: DesignDocument
    path: Path | None
    result: RegenResult | None = None
    meshes: dict[str, tuple[str, bytes, list | None]] = field(default_factory=dict)   # name -> (key, glb, matrix)
    part_now: tuple[str, object] = ("", None)                             # (doc part json, PartModel) between rebuilds
    outline: tuple[str, list] = ("", [])                                 # (part mesh key, parting-plane outline)
    suggestions: tuple[str, list] = ("", [])                             # (part mesh key, fit.suggest)
    sections: dict = field(default_factory=dict)                          # (part mesh key, z) -> outline
    saved: str | None = None                                              # when it was last saved (autosave)
    save_error: str | None = None
    resumed: bool = False                                                 # opened from its saved design
    regen_ms: float = 0.0
    backed_up: bool = False                                               # the file as opened kept as .bak
    notes: list[str] = field(default_factory=list)                        # for the user, about the last action


SESSIONS: dict[str, Session] = {}
_MESH_CACHE: OrderedDict[str, bytes] = OrderedDict()     # glb by key, least recently used first
MESH_CACHE_BYTES = 512 * 2**20                           # meshes no longer shown are dropped past this


def _cached_glb(key: str, shape) -> bytes:
    if key in _MESH_CACHE:
        _MESH_CACHE.move_to_end(key)
        return _MESH_CACHE[key]
    glb = _MESH_CACHE[key] = _glb(shape)
    total = sum(map(len, _MESH_CACHE.values()))
    while total > MESH_CACHE_BYTES and len(_MESH_CACHE) > 1:
        _, old = _MESH_CACHE.popitem(last=False)
        total -= len(old)
    return glb


def _session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        raise HTTPException(404, f"no design session '{sid}'")
    return s


def _glb(shape) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.glb"
        export_gltf(baked(shape), str(f), binary=True, **MESH_TOL)
        return f.read_bytes()


def _mesh_key(name: str, res: RegenResult) -> str:
    if name.startswith("gating.") and name[7:] in res.transitions:
        t = res.transitions[name[7:]]
        return hashlib.sha1(f"{name}|{t.solid.volume:.9f}|{tuple(t.solid.center())}".encode()).hexdigest()
    if name == "part" or name.startswith(("core.", "part.")):
        # everything that shapes or places the part and its cavities (files by their change time)
        pdef = res.doc.part or {}
        files = [res.doc.part_path()] + [(res.doc.base_dir / c["source"]).resolve()
                                         for c in pdef.get("cavities", []) if c.get("source")]
        stamps = [f"{p}:{p.stat().st_mtime_ns if p.exists() else 0}" for p in files]
        extra = f"{stamps}:{json.dumps({k: v for k, v in pdef.items() if k != "ports"}, sort_keys=True)}:{res.part.shrinkage_pct if res.part else None}"
    else:
        b = res.built[name.split(".", 1)[1]]
        extra = f"{b.type}:{b.element.version}:{sorted(b.params.items())}:{np.round(b.transform, 9).tobytes().hex()}"
    return hashlib.sha1(f"{name}|{extra}".encode()).hexdigest()


def _regenerate(s: Session) -> None:
    t = time.perf_counter()
    res = s.result = regenerate(s.doc)
    shapes = {}
    if res.part:
        shapes["part"] = res.part.shape
        shapes.update({f"core.{k}": v for k, v in res.part.cores.items()})
    shapes.update({b.tags["solid"]: b.solid for b in res.built.values()})      # untrimmed: what the pattern looks like
    shapes.update({t.name: t.solid for t in res.transitions.values()})
    if res.part:
        shapes.update({f"part.{k}": v for k, v in res.part.cavities.items()})
    s.meshes = {}
    for name, shape in shapes.items():
        if res.part and name in res.part.bases:
            # a part is meshed once per file, as read (at pattern size), and the viewer places it by its
            # matrix: moving it sends no new mesh (a large part's is seconds to make and megabytes to send)
            bkey, base = res.part.bases[name]
            key = hashlib.sha1(f"base|{bkey}".encode()).hexdigest()
            s.meshes[name] = (key, _cached_glb(key, base), np.asarray(res.part.placed[name].m).ravel().tolist())
            continue
        key = _mesh_key(name, res)
        s.meshes[name] = (key, _cached_glb(key, shape), None)
    s.part_now = (json.dumps(s.doc.part, sort_keys=True), res.part)
    s.regen_ms = 1e3 * (time.perf_counter() - t)


def _bb(x) -> list:
    """[lo, hi] of a ready box (lo, hi), or of a shape: its loose box (an exact one takes seconds on a
    large part)."""
    if isinstance(x, tuple):
        return [list(map(float, x[0])), list(map(float, x[1]))]
    b = x.bounding_box(optimal=False)
    return [list(map(float, b.min)), list(map(float, b.max))]


def _pm(s: Session):
    """The part where the document has it now: the last rebuild's, or (moved since, before the rebuild
    that follows) placed again from the cached part files, which is quick."""
    from core.part import load_part
    key = json.dumps(s.doc.part, sort_keys=True)
    if s.part_now[0] != key:
        shrink = s.result.preset.get("shrinkage_pct") if s.result and s.result.preset else None
        s.part_now = (key, load_part(s.doc, shrink))
    return s.part_now[1]


def _parts(s: Session) -> list[dict]:
    """Every part in the box: the main one ("part") and its cavities (copies or other files)."""
    pm = s.result.part if s.result else None
    if pm is None:
        return []
    out = [{"id": "part", "name": pm.path.name, "kind": "main", "bbox": _bb(pm.bbox_of("part")), "volume_mm3": pm.volume_mm3}]
    for c in (s.doc.part or {}).get("cavities", []):
        if c["id"] in pm.cavities:
            out.append({"id": c["id"], "name": c.get("name") or Path(c.get("source") or pm.path.name).name,
                        "kind": "file" if c.get("source") else "copy", "bbox": _bb(pm.bbox_of(f"part.{c['id']}")),
                        "volume_mm3": pm.cavity_volumes.get(c["id"])})
    return out


def _outline(s: Session) -> list:
    """The part's cut by the parting plane (z = 0), for the plan view; cached per part placement."""
    pm = _pm(s) if s.result is not None else None
    if pm is None:
        return []
    if s.outline[0] != pm.key:
        from core.partmesh import section
        # just above and just below the plane: a part face lying on it would give its inner
        # (triangle) edges, the cuts beside it only its outline
        metal = pm.metal_placed()
        s.outline = (pm.key, section(metal, 1e-3) + section(metal, -1e-3))
    return s.outline[1]


def _changed(s: Session) -> None:
    """After every change: regenerate, then save the design to its file (when it has one). The first
    save of a session first keeps the file as it was opened beside it, as <name>.gating.json.bak."""
    _regenerate(s)
    if s.path is None:
        return
    try:
        if not s.backed_up and s.path.exists():
            shutil.copy2(s.path, s.path.with_name(s.path.name + ".bak"))
        s.backed_up = True
        s.doc.save(s.path)
        s.saved, s.save_error = time.strftime("%H:%M:%S"), None
    except OSError as e:
        s.save_error = f"could not save {s.path}: {e.strerror or e}"


def _take_notes(s: Session) -> list[str]:
    notes, s.notes = s.notes, []
    return notes


def _state(sid: str) -> dict:
    s = _session(sid)
    res = s.result
    els = []
    for d in s.doc.elements:
        b = res.built.get(d["id"])
        els.append({"instance": d["id"], "type": d["type"], "name": d.get("name"), "built": b is not None,
                    "params": dict(b.params) if b else d["params"], "derived": b.derived if b else {},
                    "solid": b.tags["solid"] if b else None,
                    "ports": [x.to_dict() for x in b.ports()] if b else []})
    return {
        "id": sid, "path": str(s.path) if s.path else None, "document": s.doc.data,
        "can_undo": s.doc.can_undo, "can_redo": s.doc.can_redo,
        "elements": els,
        "part": None if not res.part else {
            "volume_mm3": res.part.volume_mm3, "bbox": res.part.bbox, "source_unit": res.part.source_unit,
            "shrinkage_pct": res.part.shrinkage_pct,
            "ports": {k: f.to_dict() for k, f in res.part.ports.items()}},
        "preset": res.preset.data if res.preset else None,
        "saved": s.saved, "save_error": s.save_error, "resumed": s.resumed, "notes": _take_notes(s),
        "box": workflow.box(s.doc.data),
        "parts": _parts(s),
        "parting_outline": _outline(s),
        "flask": None if res.flask is None else [list(map(float, res.flask[0])), list(map(float, res.flask[1]))],
        "issues": [i.to_dict() for i in res.issues],
        "meshes": [{"name": n, "kind": n.split(".", 1)[0], "url": f"/api/designs/{sid}/mesh/{n}.glb?k={k[:12]}",
                    "matrix": mx} for n, (k, _, mx) in s.meshes.items()],
        "measurement_planes": res.measurement_planes(),
        "calc": res.calc.to_dict() if res.calc else None,
        "transitions": [{"connection": t.connection, "name": t.name} for t in res.transitions.values()],
        "timings_ms": {**res.timings_ms, "regen_and_mesh": s.regen_ms},
    }


@app.get("/api/elements")
def elements() -> list[dict]:
    return REGISTRY.describe()


@app.post("/api/designs/open")
def open_design(path: str = Body(..., embed=True)) -> dict:
    p = Path(path).expanduser().resolve()
    try:
        doc = DesignDocument.load(p)
    except FileNotFoundError:
        raise HTTPException(404, f"no file {p}") from None
    except DocumentError as e:
        raise HTTPException(422, e.problems) from None
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = Session(doc, p)
    _regenerate(SESSIONS[sid])
    return _state(sid)


UPLOADS = Path(__file__).resolve().parents[1] / "parts"     # uploaded part files (kept out of git)


@app.post("/api/upload")
async def upload(request: Request, name: str) -> dict:
    """A part file sent as the raw request body; saved under parts/, its path returned."""
    safe = Path(name).name
    if Path(safe).suffix.lower() not in (".step", ".stp", ".stl"):
        raise HTTPException(422, f"{safe}: a part is a STEP or STL file")
    data = await request.body()
    if not data:
        raise HTTPException(422, "the file is empty")
    UPLOADS.mkdir(exist_ok=True)
    target, n = UPLOADS / safe, 1
    while target.exists() and target.read_bytes() != data:       # same name, other content: keep both
        target = UPLOADS / f"{Path(safe).stem}_{n}{Path(safe).suffix}"
        n += 1
    target.write_bytes(data)
    return {"path": str(target)}


@app.post("/api/designs/new")
def new_design(part: str = Body(...), fresh: bool = Body(False)) -> dict:
    """A design around a part file (STEP or STL), saved next to it as <name>.gating.json after every
    change. If that file is already there, the saved design is opened again (fresh: start over)."""
    p = Path(part).expanduser().resolve()
    if not p.is_file():
        raise HTTPException(404, f"no file {p}")
    if p.suffix.lower() not in (".step", ".stp", ".stl"):
        raise HTTPException(422, f"{p.name}: a part is a STEP or STL file")
    target = p.with_name(f"{p.stem}.gating.json")
    sid = uuid.uuid4().hex[:12]
    notes = []
    if target.exists() and not fresh:
        try:
            doc = DesignDocument.load(target)
        except (DocumentError, ValueError, KeyError, UnicodeDecodeError) as e:
            # not a design we can read: kept under another name, never overwritten, and a new one started
            kept = target.with_name(f"{p.stem}.gating.unreadable-{time.strftime('%Y%m%d-%H%M%S')}.json")
            try:
                target.rename(kept)
            except OSError as e2:
                raise HTTPException(422, f"{target.name} cannot be read ({_short(e)}) and cannot be moved "
                                         f"aside ({e2.strerror or e2}): move it yourself and open the part again") from None
            notes.append(f"The saved design {target.name} could not be read ({_short(e)}); it was kept as "
                         f"{kept.name} and a new design started.")
        else:
            s = SESSIONS[sid] = Session(doc, target, resumed=True)
            try:
                _regenerate(s)
            except Exception as e:                       # noqa: BLE001 - readable but not buildable: leave the file alone
                del SESSIONS[sid]
                raise HTTPException(422, f"the saved design {target.name} could not be rebuilt ({_short(e)}); "
                                         "it was left as it is") from None
            return _state(sid)
    elif target.exists():                                # starting over: the old design is kept, not lost
        kept = target.with_name(f"{p.stem}.gating.previous.json")
        try:
            shutil.copy2(target, kept)
            notes.append(f"The design you had was kept as {kept.name}.")
        except OSError:
            pass
    data = empty(p.stem)
    data["part"] = {"source": p.name}
    s = SESSIONS[sid] = Session(DesignDocument(data, base_dir=p.parent), target, backed_up=True, notes=notes)
    _changed(s) if fresh else _regenerate(s)
    return _state(sid)


def _short(e: Exception, n: int = 160) -> str:
    m = str(e) or type(e).__name__
    return m if len(m) <= n else m[:n] + "…"


@app.get("/api/designs/{sid}")
def get_design(sid: str) -> dict:
    return _state(sid)


@app.post("/api/designs/{sid}/patch")
def patch(sid: str, ops: list[dict] = Body(...)) -> dict:
    s = _session(sid)
    try:
        s.doc.apply(workflow.with_sync(s.doc, ops))
    except DocumentError as e:
        raise HTTPException(422, e.problems) from None
    except Exception as e:                                 # a malformed JSON Patch
        raise HTTPException(422, [f"{type(e).__name__}: {e}"]) from None
    _changed(s)
    return _state(sid)


@app.post("/api/designs/{sid}/undo")
def undo(sid: str) -> dict:
    s = _session(sid)
    if s.doc.undo():
        _changed(s)
    return _state(sid)


@app.post("/api/designs/{sid}/redo")
def redo(sid: str) -> dict:
    s = _session(sid)
    if s.doc.redo():
        _changed(s)
    return _state(sid)


@app.post("/api/designs/{sid}/save")
def save(sid: str, path: str | None = Body(None, embed=True)) -> dict:
    s = _session(sid)
    target = Path(path).expanduser().resolve() if path else s.path
    if target is None:
        raise HTTPException(422, "no path to save to")
    try:
        s.doc.save(target)
    except OSError as e:
        raise HTTPException(422, f"could not save {target}: {e.strerror or e}") from None
    return {"saved": str(target)}


@app.get("/api/designs/{sid}/mesh/{name}.glb")
def mesh(sid: str, name: str) -> Response:
    s = _session(sid)
    if name not in s.meshes:
        raise HTTPException(404, f"no solid '{name}'")
    return Response(s.meshes[name][1], media_type="model/gltf-binary",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/api/designs/{sid}/export")
def export_design(sid: str, out_dir: str = Body(..., embed=True),
                  materials_json: str | None = Body(None, embed=True)) -> dict:
    s = _session(sid)
    try:
        return export(s.result, Path(out_dir).expanduser(), materials_json=materials_json)
    except ExportError as e:
        raise HTTPException(422, [i.to_dict() for i in e.issues]) from None
    except OSError as e:
        raise HTTPException(422, f"could not write the export to {out_dir}: {e.strerror or e}") from None


def _apply(s: Session, patch: list[dict], rebuild: bool = True) -> bool:
    """Apply a change (one undo step) and rebuild; rebuild False: the caller rebuilds (once, after what
    follows from it). Whether anything changed."""
    try:
        full = workflow.with_sync(s.doc, patch)
        if not full:                                     # nothing changes: no empty undo step
            return False
        s.doc.apply(full)
    except DocumentError as e:
        raise HTTPException(422, e.problems) from None
    if rebuild:
        _changed(s)
    return True


def _part(s: Session):
    if s.result is None or s.result.part is None:
        raise HTTPException(422, "the design has no part loaded")
    return s.result.part


@app.get("/api/presets")
def presets() -> list[str]:
    return list_presets()


@app.post("/api/designs/{sid}/part/pick")
def pick(sid: str, point: list[float] = Body(..., embed=True)) -> dict:
    return pick_face(_part(_session(sid)), point)


@app.post("/api/designs/{sid}/part/orient")
def orient_part(sid: str, op: dict = Body(...)) -> dict:
    """op: {"op": "rotate", "axis": "x"|[..], "angle_deg"} | {"op": "up_vector", "vector"} |
    {"op": "face_down", "normal"} | {"op": "parting_at", "z"}"""
    s = _session(sid)
    pm = _part(s)
    centre = (np.asarray(pm.bbox[0]) + np.asarray(pm.bbox[1])) / 2
    try:
        patch = ops.orient_op(s.doc, centre, **op)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(422, f"bad orientation op: {e}") from None
    _apply(s, patch)
    return _state(sid)


@app.post("/api/designs/{sid}/part/gate_port")
def add_gate_port(sid: str, point: list[float] = Body(..., embed=True)) -> dict:
    s = _session(sid)
    pk = pick_face(_part(s), point)
    existing = {p["id"] for p in (s.doc.part or {}).get("ports", [])}
    _apply(s, ops.add_part_port(s.doc, gate_port(pk, ops.next_id(existing, "gate"))))
    return _state(sid)


@app.post("/api/designs/{sid}/part/moldcheck")
def mold_check(sid: str) -> dict:
    s = _session(sid)
    res = moldcheck.check(_part(s), s.doc.data["mold"].get("draft_min_deg"))
    return {**res, "issues": [i.to_dict() for i in res["issues"]]}


@app.post("/api/designs/{sid}/calculators/apply")
def apply_calculators(sid: str) -> dict:
    """Push the calculator results into the driven parameters (one undo step)."""
    s = _session(sid)
    patch, issues = propagation_patch(s.result)
    if not patch:
        raise HTTPException(422, ["nothing to apply"] + [i.message for i in issues])
    _apply(s, patch)
    return _state(sid)


@app.post("/api/designs/{sid}/ops/mirror")
def mirror_op(sid: str, instances: list[str] = Body(...), origin: list[float] = Body(...),
              normal: list[float] = Body(...), suffix: str = Body("_m")) -> dict:
    s = _session(sid)
    try:
        patch = ops.mirror(s.doc, REGISTRY, instances, origin, normal, suffix)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e)) from None
    _apply(s, patch)
    return _state(sid)


@app.post("/api/designs/{sid}/ops/pattern")
def pattern_op(sid: str, spec: dict = Body(...)) -> dict:
    """spec: {"kind": "linear", "instances", "step", "count", "cavities"} |
    {"kind": "circular", "instances", "axis_origin", "axis_dir", "count", "total_deg", "cavities"}"""
    s = _session(sid)
    try:
        if spec["kind"] == "linear":
            patch = ops.linear_pattern(s.doc, REGISTRY, spec["instances"], spec["step"], int(spec["count"]),
                                       bool(spec.get("cavities")))
        else:
            patch = ops.circular_pattern(s.doc, REGISTRY, spec["instances"], spec["axis_origin"], spec["axis_dir"],
                                         int(spec["count"]), float(spec.get("total_deg", 360.0)),
                                         bool(spec.get("cavities")))
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(422, f"bad pattern: {e}") from None
    _apply(s, patch)
    return _state(sid)


@app.post("/api/designs/{sid}/elements/remove")
def remove_element(sid: str, instance: str = Body(..., embed=True)) -> dict:
    """Remove an element and every connection touching it (one undo step)."""
    s = _session(sid)
    d = s.doc.data
    ci = [i for i, c in enumerate(d["connections"]) if instance in (c["from"]["instance"], c["to"]["instance"])]
    ei = [i for i, e in enumerate(d["elements"]) if e["id"] == instance]
    if not ei:
        raise HTTPException(404, f"no element '{instance}'")
    patch = [{"op": "remove", "path": f"/connections/{i}"} for i in sorted(ci, reverse=True)]
    patch.append({"op": "remove", "path": f"/elements/{ei[0]}"})
    _apply(s, patch)
    return _state(sid)


# ---- the guided set-up: box, part in the box, sprue, runner ---------------------------

def _workflow(s: Session, make, rebuild: bool = True) -> bool:
    try:
        patch = make()
    except workflow.WorkflowError as e:
        raise HTTPException(422, str(e)) from None
    return _apply(s, patch, rebuild)


def _workflow_then_follow(s: Session, make, carry=frozenset()) -> None:
    """A change that moves, adds or removes a part (or the sprue), then what follows from it (seating,
    re-gating), rebuilt once at the end rather than after each."""
    changed = _workflow(s, make, rebuild=False)
    try:
        changed = _after_part_change(s, carry) or changed
    finally:
        if changed:
            _changed(s)


@app.post("/api/designs/{sid}/workflow/box")
def set_box(sid: str, length: float = Body(...), width: float = Body(...), height: float = Body(...),
            parting: float | None = Body(None)) -> dict:
    """The molding box; the first time, the part is also centred in it (same undo step)."""
    s = _session(sid)
    first = workflow.box(s.doc.data) is None

    def make():
        patch = workflow.box_patch(s.doc, length, width, height, parting)
        if first and s.result and s.result.part:
            patch += workflow.centre_part_patch(s.doc, s.result.part.bbox)
        return patch
    _workflow(s, make)
    return _state(sid)


def _part_entry(s: Session, cavity: str | None) -> dict:
    p = next((x for x in _parts(s) if x["id"] == (cavity or "part")), None)
    if p is None:
        raise HTTPException(404, f"no part '{cavity}' in the box")
    return p


def _beside(s: Session, lo_x: float) -> list[float]:
    """Offset that puts something whose left end is at lo_x 20 mm right of every part in the box."""
    return [max(p["bbox"][1][0] for p in _parts(s)) + 20.0 - lo_x, 0.0, 0.0]


def _part_meshes(s: Session) -> dict:
    """Each part's mesh where the mold has it, by its id in the box ("part" or a cavity's)."""
    pm = _pm(s) if s.result else None
    return {} if pm is None else {("part" if n == "part" else n[5:]): p for n, p in pm.placed.items()}


def _on_part(s: Session, pid: str) -> set[str]:
    """Ids of what sits on part pid (its id in the box: "part" or a cavity's): the feeders, vents and
    hand-drawn ingates whose contact point lies on it, found from the geometry."""
    from core.partmesh import nearest
    shapes = _part_meshes(s)
    if pid not in shapes:
        return set()
    els = {e["id"]: e for e in s.doc.elements}
    out = set()
    for ids, point in workflow.contacts(s.doc.data):
        reach = 2.0 + max(float(els[i]["params"].get("embed", EMBED.default)) for i in ids)
        if nearest(shapes, point, reach) == pid:
            out |= ids
    return out


def _move(s: Session, cavity: str | None, make) -> None:
    """A part move: make(carry) gives the patch that moves the part and what sits on it; then the top
    feeders and vents are seated again, side feeders' necks run again and the runners gated again
    against the moved part, all in the same undo step."""
    carry = _on_part(s, cavity or "part")
    _workflow_then_follow(s, lambda: make(carry), carry)


def _after_part_change(s: Session, carry=frozenset()) -> bool:
    """Seat what moved with a part (carry) on it again, then re-gate every gated runner, merged into the
    last undo step; what could not be done is told in the notes. Whether it changed the document (the
    caller, _workflow_then_follow, rebuilds once)."""
    from core.partmesh import seat
    if s.result is None or _pm(s) is None:
        return False
    notes, ops = [], []
    data = s.doc.data
    els = {e["id"]: e for e in data["elements"]}
    tee = {c["from"]["instance"]: c["to"]["instance"] for c in data["connections"] if c["joint"] == "tee"}
    metal = _pm(s).metal_placed()
    for inst in sorted(carry):
        e = els.get(inst)
        if e is None or e["type"] not in ("feeder", "vent"):
            continue
        p = e["params"]
        try:
            if e["type"] == "feeder" and p.get("kind") == "side":
                neck = els.get(tee.get(inst, ""))
                if neck is None:
                    continue
                n = neck["params"]
                outline = _runner_outline(s, n["side"], float(n["thickness"]), None) or []
                o = e["anchor"]["frame"]["origin"]
                end = workflow.neck_end(outline, o[0], o[1], float(p["d"]), float(n["width"]), n["waypoints"][-1])
                ops += workflow.neck_patch(s.doc, neck["id"], end)
            else:
                o = e["anchor"]["frame"]["origin"]
                st = seat(metal, o[0], o[1], float(p["d"]))
                if st is None:
                    notes.append(f"{inst} is no longer on the part after the move: place it again")
                    continue
                ops += workflow.seat_patch(s.doc, inst, st["top"], st["drop"] + EMBED.default)
                if st["misses"]:
                    notes.append(_overhang(inst, st))
        except workflow.WorkflowError as x:
            notes.append(f"{inst} could not follow the part ({x}): place it again")
    changed = False
    if ops:
        try:
            s.doc.apply(ops)
            s.doc.merge_last()
            changed = True
        except DocumentError as x:
            notes.append(f"the feeders and vents could not follow the part: {'; '.join(x.problems)}")
    try:
        patch, failed = workflow.regate_patch(s.doc, lambda p: _runner_outline(
            s, p["side"], p["height"], {"thickness": p["gate_thickness"], "side": p.get("gate_side", "drag")},
            p.get("level", 0.0)))
        notes += failed
        if patch:
            s.doc.apply(patch)
            s.doc.merge_last()
            changed = True
    except DocumentError as x:
        notes.append(f"the runners could not be gated again: {'; '.join(x.problems)}")
    s.notes += notes
    return changed


def _overhang(inst: str, st: dict) -> str:
    return (f"{inst} hangs over the part's edge: {st['misses']} of {st['rays']} points across it have no part "
            "under them")


@app.post("/api/designs/{sid}/workflow/centre_part")
def centre_part(sid: str, cavity: str | None = Body(None, embed=True)) -> dict:
    s = _session(sid)
    bb = _part_entry(s, cavity)["bbox"]
    _move(s, cavity, lambda carry: workflow.centre_part_patch(s.doc, bb, cavity, carry))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/move_part")
def move_part(sid: str, delta: list[float] = Body(...), cavity: str | None = Body(None)) -> dict:
    s = _session(sid)
    _part_entry(s, cavity)
    _move(s, cavity, lambda carry: workflow.move_part_patch(s.doc, delta, cavity, carry))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/turn_part")
def turn_part(sid: str, axis: str = Body(...), angle_deg: float = Body(...), cavity: str | None = Body(None)) -> dict:
    s = _session(sid)
    lo, hi = _part_entry(s, cavity)["bbox"]
    centre = [(a + b) / 2 for a, b in zip(lo, hi)]
    _move(s, cavity, lambda carry: workflow.turn_part_patch(s.doc, axis, angle_deg, centre, cavity, carry))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/add_copy")
def add_copy(sid: str, of: str | None = Body(None, embed=True)) -> dict:
    """Another copy of a part, put beside everything already in the box."""
    s = _session(sid)
    lo = _part_entry(s, of)["bbox"][0]
    _workflow_then_follow(s, lambda: workflow.add_copy_patch(s.doc, of, _beside(s, lo[0])))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/add_part_file")
def add_part_file(sid: str, path: str = Body(...), units: str = Body("auto")) -> dict:
    """Another part file (STEP or STL) in the box, beside everything already there, centred on the parting plane."""
    from core.part import PartError, load_source
    s = _session(sid)
    _part(s)
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise HTTPException(404, f"no file {p}")
    try:
        src = load_source(p, units)
    except PartError as e:
        raise HTTPException(422, str(e)) from None
    lo, hi = _bb(src.shape)
    c = [(a + b) / 2 for a, b in zip(lo, hi)]
    dx = _beside(s, lo[0])[0]
    source = os.path.relpath(p, s.doc.base_dir.resolve())        # relative to the design, as the main part is
    _workflow_then_follow(s, lambda: workflow.add_file_patch(s.doc, source, [dx, -c[1], -c[2]], units, p.name))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/remove_part")
def remove_part(sid: str, cavity: str = Body(..., embed=True)) -> dict:
    """A part out of the box, with the feeders, vents and hand-drawn ingates on it; runners are gated again."""
    s = _session(sid)
    _part_entry(s, cavity)
    on_it = _on_part(s, cavity)
    _workflow_then_follow(s, lambda: workflow.remove_part_patch(s.doc, cavity, on_it))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/sprue")
def place_sprue(sid: str, x: float = Body(...), y: float = Body(...), d_top: float = Body(...),
                d_bottom: float = Body(...)) -> dict:
    s = _session(sid)
    # runners from it run in from the new place
    _workflow_then_follow(s, lambda: workflow.sprue_patch(s.doc, x, y, d_top, d_bottom))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/runner")
def draw_runner(sid: str, side: str = Body(...), height: float = Body(...), points: list[list[float]] = Body(...),
                width: float | None = Body(None), taper_pct: float | None = Body(None),
                w_parting: float | None = Body(None), w_far: float | None = Body(None),
                extension: float | None = Body(None),
                runner: str | None = Body(None), branch: dict | None = Body(None),
                gate: dict | None = Body(None)) -> dict:
    """Redraw a runner, or (runner null) draw a new one from the sprue or (branch {from, at}) off
    another runner; with gate {width, thickness, length, side} an ingate goes where it meets the part.
    Its section: width (the bottom face) and taper_pct (the top face that much narrower), or the two
    widths w_parting and w_far.  extension: how far it runs past its last ingate (0: as drawn)."""
    s = _session(sid)
    if width is not None or taper_pct is not None:
        if width is None or taper_pct is None:
            raise HTTPException(422, "type the runner's width and its taper %")
        try:
            w_parting, w_far = workflow.runner_widths(side, float(width), float(taper_pct))
        except workflow.WorkflowError as e:
            raise HTTPException(422, str(e)) from None
    elif w_parting is None or w_far is None:
        raise HTTPException(422, "type the runner's width and its taper %")
    level = workflow.level_for(s.doc.data, runner, branch, side)
    outline = _runner_outline(s, side, height, gate, level) if gate else None
    _workflow(s, lambda: workflow.runner_patch(s.doc, side, w_parting, w_far, height, points, runner,
                                               branch, gate, outline, taper_pct, extension))
    return _state(sid)


def _runner_outline(s: Session, side: str, height: float, gate: dict | None, level: float = 0.0) -> list | None:
    """Where a runner first touches the part: the part's outline through the runner's depth (and the
    ingate's), not only at its parting-plane face: the part may reach further out below or above it.
    level: the height of the runner's parting-plane face (0: on the parting plane)."""
    if not (s.result and _pm(s)):
        return None

    def levels(sign: float, depth: float) -> set[float]:        # ~every twelfth of the depth, ends just inside
        n = 12
        return {sign * min(max(depth * k / n, 1e-3), depth - 1e-3) for k in range(n + 1)}
    lv = float(level or 0.0)
    zs = {lv + z for z in levels(1.0 if side == "cope" else -1.0, float(height))}
    t = float((gate or {}).get("thickness") or 0)
    if t > 0:
        zs |= {lv + z for z in levels(1.0 if gate.get("side") == "cope" else -1.0, t)}
    face = _outline(s) if abs(lv) < 1e-9 else _section_at(s, lv + (1e-3 if side == "cope" else -1e-3))
    return face + [line for z in sorted(zs) for line in _section_at(s, z)]


@app.post("/api/designs/{sid}/workflow/gate")
def set_gate(sid: str, gate: dict | None = Body(..., embed=True)) -> dict:
    """The ingate size {width, thickness, length, side} for every runner: each is cut and gated again
    with it where it runs into a part, all in one undo step. gate null: no automatic ingates."""
    s = _session(sid)
    if gate is None:
        _workflow(s, lambda: workflow.ungate_patch(s.doc))
        return _state(sid)
    try:
        g = {k: float(gate[k]) for k in ("width", "thickness", "length")}
        g["side"] = gate.get("side", "drag")
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "the ingate needs a width, thickness and length") from None
    if min(g["width"], g["thickness"], g["length"]) <= 0 or g["side"] not in ("drag", "cope"):
        raise HTTPException(422, "type the ingate's width, thickness and length (side drag or cope)")
    patch, failed = workflow.regate_patch(s.doc, lambda p: _runner_outline(
        s, p["side"], p["height"], {"thickness": p["gate_thickness"], "side": p["gate_side"]},
        p.get("level", 0.0)), g)
    s.notes += failed
    if patch:
        _apply(s, patch)
    return _state(sid)


def _section_at(s: Session, z: float) -> list:
    """The part cut at height z (mm, mold frame), cached per part placement."""
    from core.partmesh import section
    pm = _pm(s)
    key = (pm.key, round(z, 4))
    if key not in s.sections:
        if len(s.sections) > 64:
            s.sections.clear()
        s.sections[key] = section(pm.metal_placed(), z)
    return s.sections[key]


@app.post("/api/designs/{sid}/workflow/runner_level")
def set_runner_level(sid: str, runner: str = Body(...), level: float = Body(...)) -> dict:
    """Move a runner up or down within its half (level: the height of its parting-plane face, 0 on the
    parting plane, negative in the drag), with its branches and ingates; the sprue follows it down and
    the automatic ingates are cut again against the part at the new height, in one undo step."""
    s = _session(sid)
    _workflow_then_follow(s, lambda: workflow.runner_level_patch(s.doc, runner, level))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/remove_runner")
def remove_runner(sid: str, runner: str = Body(..., embed=True)) -> dict:
    s = _session(sid)
    _workflow(s, lambda: workflow.remove_runner_patch(s.doc, runner))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/ingate")
def draw_ingate(sid: str, runner: str = Body(...), start: list[float] = Body(...), end: list[float] = Body(...),
                side: str = Body(...), width: float = Body(...), thickness: float = Body(...),
                ingate: str | None = Body(None)) -> dict:
    """A new ingate from a point on a runner to the part, or (ingate given) that one redrawn."""
    s = _session(sid)
    _workflow(s, lambda: workflow.ingate_patch(s.doc, runner, start, end, side, width, thickness, ingate))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/remove_ingate")
def remove_ingate(sid: str, ingate: str = Body(..., embed=True)) -> dict:
    s = _session(sid)
    _workflow(s, lambda: workflow.remove_element_patch(s.doc, ingate))
    return _state(sid)


# ---- suggested fit: how to part the part, and the box -------------------------------------------

@app.post("/api/designs/{sid}/workflow/suggest")
def suggest_fit(sid: str) -> dict:
    """Two ways to turn and part the main part (widest section, largest flat face), each with the move
    from where it is now and what it leaves (size, below/above the plane, undercut estimate)."""
    s = _session(sid)
    pm = _part(s)
    key = pm.key
    if s.suggestions[0] != key or not key:
        s.suggestions = (key, fit.suggest(pm.shape))
    return {"options": s.suggestions[1]}


@app.post("/api/designs/{sid}/workflow/propose_box")
def propose_box(sid: str, bbox: list[list[float]] = Body(...), sides: float = Body(0.0), below: float = Body(0.0),
                above: float = Body(0.0), extra: float = Body(0.0), step: float = Body(0.0)) -> dict:
    _session(sid)
    if min(sides, below, above, extra, step) < 0:
        raise HTTPException(422, "clearances and the rounding step cannot be negative")
    return fit.propose_box(bbox, sides, below, above, extra, step, fit.standard_boxes())


@app.post("/api/designs/{sid}/workflow/apply_fit")
def apply_fit(sid: str, move: list[list[float]] = Body(...), length: float = Body(...), width: float = Body(...),
              height: float = Body(...), parting: float = Body(...), shift: list[float] = Body([0, 0, 0])) -> dict:
    s = _session(sid)
    _part(s)
    _move(s, None, lambda carry: workflow.fit_patch(s.doc, move, length, width, height, parting, shift, carry))
    return _state(sid)


@app.get("/api/matchboxes")
def matchboxes() -> list[dict]:
    return fit.standard_boxes()


@app.post("/api/matchboxes")
def add_matchbox(box: dict = Body(...)) -> list[dict]:
    try:
        return fit.add_standard_box(box)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(422, f"bad box: {e}") from None


# ---- on the sprue: cup, filter, well; on the part: feeders and vents ---------------------------

def _seat(s: Session, x: float, y: float, d: float) -> tuple[float, float]:
    """(base height, embed) for something round of diameter d standing on the part at (x, y): its base
    on the highest point of the part's top under it, reaching down to the lowest (plus the usual embed)."""
    from core.partmesh import seat
    _part(s)
    st = seat(_pm(s).metal_placed(), x, y, d)
    if st is None:
        raise HTTPException(422, f"there is no part at ({x:.0f}, {y:.0f}): click on the part")
    if st["misses"]:
        s.notes.append(_overhang("it", st).replace("it hangs", "It hangs", 1))
    return st["top"], st["drop"] + EMBED.default


@app.post("/api/designs/{sid}/workflow/cup")
def set_cup(sid: str, d_top: float | None = Body(None), height: float | None = Body(None)) -> dict:
    """The pouring cup on the sprue (d_top null: none)."""
    s = _session(sid)
    _workflow(s, lambda: workflow.cup_patch(s.doc, d_top, height))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/well")
def set_well(sid: str, d: float | None = Body(None), depth: float | None = Body(None),
             filter: dict | None = Body(None)) -> dict:
    """The sprue well (d null: none), with the filter over it or not."""
    s = _session(sid)
    _workflow(s, lambda: workflow.well_patch(s.doc, d, depth, filter))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/feeder")
def add_feeder(sid: str, kind: str = Body(...), x: float = Body(...), y: float = Body(...), d: float = Body(...),
               height: float = Body(...), neck: dict | None = Body(None)) -> dict:
    """A top feeder where (x, y) falls on the part, or a side feeder standing at (x, y) beside it, its neck
    {width, thickness, side} running to where it first meets the part."""
    s = _session(sid)
    z, embed = 0.0, None
    if kind == "top":
        z, embed = _seat(s, x, y, d)
    elif kind == "side":
        if not neck or min(float(neck.get("width") or 0), float(neck.get("thickness") or 0)) <= 0:
            raise HTTPException(422, "type the neck's width and thickness")
        side = neck.get("side", "cope")
        outline = _runner_outline(s, side, float(neck["thickness"]), None) or []
        try:
            end = workflow.neck_end(outline, x, y, d, float(neck["width"]))
        except workflow.WorkflowError as e:
            raise HTTPException(422, str(e)) from None
        neck = {**neck, "side": side, "end": end}
    _workflow(s, lambda: workflow.feeder_patch(s.doc, kind, x, y, z, d, height, neck, embed))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/vent")
def add_vent(sid: str, x: float = Body(...), y: float = Body(...), d: float = Body(...)) -> dict:
    s = _session(sid)
    z, embed = _seat(s, x, y, d)
    _workflow(s, lambda: workflow.vent_patch(s.doc, x, y, z, d, embed))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/remove")
def remove_any(sid: str, instance: str = Body(..., embed=True)) -> dict:
    """An element and what hangs off it (a side feeder's neck, the filter with the well)."""
    s = _session(sid)
    _workflow(s, lambda: workflow.remove_patch(s.doc, instance))
    return _state(sid)
