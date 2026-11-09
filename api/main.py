"""HTTP API for the editor.  The kernel stays here; the browser gets glTF.

    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from build123d import export_gltf
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import Response

from bridge.ayrton import ExportError, export
from core.document import DesignDocument, DocumentError, empty
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
app = FastAPI(title="Ayrton gating")
MESH_TOL = dict(linear_deflection=0.1, angular_deflection=0.3)


@dataclass
class Session:
    doc: DesignDocument
    path: Path | None
    result: RegenResult | None = None
    meshes: dict[str, tuple[str, bytes]] = field(default_factory=dict)   # name -> (key, glb)
    outline: tuple[str, list] = ("", [])                                 # (part mesh key, parting-plane outline)
    suggestions: tuple[str, list] = ("", [])                             # (part mesh key, fit.suggest)
    sections: dict = field(default_factory=dict)                          # (part mesh key, z) -> outline
    regen_ms: float = 0.0


SESSIONS: dict[str, Session] = {}
_MESH_CACHE: dict[str, bytes] = {}


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
        key = _mesh_key(name, res)
        if key not in _MESH_CACHE:
            _MESH_CACHE[key] = _glb(shape)
        s.meshes[name] = (key, _MESH_CACHE[key])
    s.regen_ms = 1e3 * (time.perf_counter() - t)


def _bb(shape) -> list:
    b = shape.bounding_box()
    return [list(map(float, b.min)), list(map(float, b.max))]


def _parts(s: Session) -> list[dict]:
    """Every part in the box: the main one ("part") and its cavities (copies or other files)."""
    pm = s.result.part if s.result else None
    if pm is None:
        return []
    out = [{"id": "part", "name": pm.path.name, "kind": "main", "bbox": _bb(pm.shape), "volume_mm3": pm.volume_mm3}]
    for c in (s.doc.part or {}).get("cavities", []):
        if c["id"] in pm.cavities:
            out.append({"id": c["id"], "name": c.get("name") or Path(c.get("source") or pm.path.name).name,
                        "kind": "file" if c.get("source") else "copy", "bbox": _bb(pm.cavities[c["id"]]),
                        "volume_mm3": pm.cavity_volumes.get(c["id"])})
    return out


def _outline(s: Session) -> list:
    """The part's cut by the parting plane (z = 0), for the plan view; cached per part placement."""
    if s.result is None or s.result.part is None or "part" not in s.meshes:
        return []
    key = s.meshes["part"][0]
    if s.outline[0] != key:
        from core.mold import section_outline
        # just above and just below the plane: a part face lying on it would give its inner
        # (triangle) edges, the cuts beside it only its outline
        metal = s.result.part.metal
        s.outline = (key, section_outline(metal, 1e-3) + section_outline(metal, -1e-3))
    return s.outline[1]


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
        "box": workflow.box(s.doc.data),
        "parts": _parts(s),
        "parting_outline": _outline(s),
        "flask": None if res.flask is None else [list(map(float, res.flask[0])), list(map(float, res.flask[1]))],
        "issues": [i.to_dict() for i in res.issues],
        "meshes": [{"name": n, "kind": n.split(".", 1)[0], "url": f"/api/designs/{sid}/mesh/{n}.glb?k={k[:12]}"}
                   for n, (k, _) in s.meshes.items()],
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
def new_design(part: str = Body(..., embed=True)) -> dict:
    """A new design around a part file (STEP or STL); saved next to it as <name>.gating.json."""
    p = Path(part).expanduser().resolve()
    if not p.is_file():
        raise HTTPException(404, f"no file {p}")
    if p.suffix.lower() not in (".step", ".stp", ".stl"):
        raise HTTPException(422, f"{p.name}: a part is a STEP or STL file")
    data = empty(p.stem)
    data["part"] = {"source": p.name}
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = Session(DesignDocument(data, base_dir=p.parent), p.with_name(f"{p.stem}.gating.json"))
    _regenerate(SESSIONS[sid])
    return _state(sid)


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
    _regenerate(s)
    return _state(sid)


@app.post("/api/designs/{sid}/undo")
def undo(sid: str) -> dict:
    s = _session(sid)
    if s.doc.undo():
        _regenerate(s)
    return _state(sid)


@app.post("/api/designs/{sid}/redo")
def redo(sid: str) -> dict:
    s = _session(sid)
    if s.doc.redo():
        _regenerate(s)
    return _state(sid)


@app.post("/api/designs/{sid}/save")
def save(sid: str, path: str | None = Body(None, embed=True)) -> dict:
    s = _session(sid)
    target = Path(path).expanduser().resolve() if path else s.path
    if target is None:
        raise HTTPException(422, "no path to save to")
    s.doc.save(target)
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


def _apply(s: Session, patch: list[dict]) -> None:
    try:
        s.doc.apply(workflow.with_sync(s.doc, patch))
    except DocumentError as e:
        raise HTTPException(422, e.problems) from None
    _regenerate(s)


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

def _workflow(s: Session, make) -> None:
    try:
        patch = make()
    except workflow.WorkflowError as e:
        raise HTTPException(422, str(e)) from None
    _apply(s, patch)


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


@app.post("/api/designs/{sid}/workflow/centre_part")
def centre_part(sid: str, cavity: str | None = Body(None, embed=True)) -> dict:
    s = _session(sid)
    bb = _part_entry(s, cavity)["bbox"]
    _workflow(s, lambda: workflow.centre_part_patch(s.doc, bb, cavity))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/move_part")
def move_part(sid: str, delta: list[float] = Body(...), cavity: str | None = Body(None)) -> dict:
    s = _session(sid)
    _part_entry(s, cavity)
    _workflow(s, lambda: workflow.move_part_patch(s.doc, delta, cavity))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/turn_part")
def turn_part(sid: str, axis: str = Body(...), angle_deg: float = Body(...), cavity: str | None = Body(None)) -> dict:
    s = _session(sid)
    lo, hi = _part_entry(s, cavity)["bbox"]
    centre = [(a + b) / 2 for a, b in zip(lo, hi)]
    _workflow(s, lambda: workflow.turn_part_patch(s.doc, axis, angle_deg, centre, cavity))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/add_copy")
def add_copy(sid: str, of: str | None = Body(None, embed=True)) -> dict:
    """Another copy of a part, put beside everything already in the box."""
    s = _session(sid)
    lo = _part_entry(s, of)["bbox"][0]
    _workflow(s, lambda: workflow.add_copy_patch(s.doc, of, _beside(s, lo[0])))
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
    _workflow(s, lambda: workflow.add_file_patch(s.doc, str(p), [dx, -c[1], -c[2]], units, p.name))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/remove_part")
def remove_part(sid: str, cavity: str = Body(..., embed=True)) -> dict:
    s = _session(sid)
    _workflow(s, lambda: workflow.remove_part_patch(s.doc, cavity))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/sprue")
def place_sprue(sid: str, x: float = Body(...), y: float = Body(...), d_top: float = Body(...),
                d_bottom: float = Body(...)) -> dict:
    s = _session(sid)
    _workflow(s, lambda: workflow.sprue_patch(s.doc, x, y, d_top, d_bottom))
    return _state(sid)


@app.post("/api/designs/{sid}/workflow/runner")
def draw_runner(sid: str, side: str = Body(...), w_parting: float = Body(...), w_far: float = Body(...),
                height: float = Body(...), points: list[list[float]] = Body(...),
                runner: str | None = Body(None), branch: dict | None = Body(None),
                gate: dict | None = Body(None)) -> dict:
    """Redraw a runner, or (runner null) draw a new one from the sprue or (branch {from, at}) off
    another runner; with gate {width, thickness, length, side} an ingate goes where it meets the part."""
    s = _session(sid)
    outline = None
    if gate and s.result and s.result.part:
        # where the runner first touches the part: the part's outline through the runner's depth (and the
        # ingate's), not only on the parting plane: the part may reach further out below or above it
        def levels(sign: float, depth: float) -> set[float]:     # ~every twelfth of the depth, ends just inside
            n = 12
            return {sign * min(max(depth * k / n, 1e-3), depth - 1e-3) for k in range(n + 1)}
        zs = levels(1.0 if side == "cope" else -1.0, height)
        t = float(gate.get("thickness") or 0)
        if t > 0:
            zs |= levels(1.0 if gate.get("side") == "cope" else -1.0, t)
        outline = _outline(s) + [line for z in sorted(zs) for line in _section_at(s, z)]
    _workflow(s, lambda: workflow.runner_patch(s.doc, side, w_parting, w_far, height, points, runner,
                                               branch, gate, outline))
    return _state(sid)


def _section_at(s: Session, z: float) -> list:
    """The part cut at height z (mm, mold frame), cached per part placement."""
    from core.mold import section_outline
    key = (s.meshes["part"][0] if "part" in s.meshes else "", round(z, 4))
    if key not in s.sections:
        if len(s.sections) > 64:
            s.sections.clear()
        s.sections[key] = section_outline(s.result.part.metal, z)
    return s.sections[key]


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
    key = s.meshes["part"][0] if "part" in s.meshes else ""
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
    _workflow(s, lambda: workflow.fit_patch(s.doc, move, length, width, height, parting, shift))
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
