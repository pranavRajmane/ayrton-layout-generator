"""The layout generator's own web server (Phases 6 and 7), apart from the editor's API:

    uvicorn layouts.server:app --port 8010          (the UI: layouts_ui/, Vite on 5174)

Jobs live in folders under ``jobs/`` (or $LAYOUT_JOBS_DIR): ``<id>/<id>.job.json`` with the part file,
Phase 1's field, the built STEP files and the engineer's shortlist (``<id>.review.json``).  A run goes
through the phases that are not done (or are out of date) in a background thread; the UI polls
``/progress``, and Phase 3's layouts stream in as they are placed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from layouts import arrange as ar
from layouts import assemble as asm
from layouts import ingates as ig
from layouts import metrics as mt
from layouts import modulus as md
from layouts import recipe as rc
from layouts import runners as rn
from layouts import thumbs
from layouts.job import ORDER, GatingJob, JobError, default_limits, file_sha256, new

ROOT = Path(__file__).resolve().parents[1]
JOBS = Path(os.environ.get("LAYOUT_JOBS_DIR", ROOT / "jobs"))
RECIPE_SETS = {
    "presets": {"label": "Foundry presets", "dir": ROOT / "presets"},
    "test": {"label": "Test recipe (invented values, not foundry data)", "dir": ROOT / "tests" / "layouts" / "recipes"},
}
UI_DIST = ROOT / "layouts_ui" / "dist"

app = FastAPI(title="Ayrton layout generator")
_LOCK = threading.Lock()
PROGRESS: dict[str, dict] = {}            # job id -> what its running (or last) run is doing
CONTEXT: dict[str, tuple[str, rn.Context]] = {}      # job id -> (key, footprints and the rest)


# ---- helpers ---------------------------------------------------------------------------------------
def _job_dir(jid: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", jid):
        raise HTTPException(404, f"no job '{jid}'")
    return JOBS / jid


def _load(jid: str) -> GatingJob:
    f = _job_dir(jid) / f"{jid}.job.json"
    if not f.exists():
        raise HTTPException(404, f"no job '{jid}'")
    return GatingJob.load(f)


def _context(job: GatingJob) -> rn.Context:
    """Footprints and the rest (Phases 3, 4 and 7 need them): built once per part and placement."""
    d = job.data
    key = json.dumps([d["inputs"]["part"]["sha256"], d["inputs"]["parting"], d["recipe"]["values"]["shrinkage_pct"],
                      d["recipe"]["values"]["layout"]["runner"]["side"]], sort_keys=True)
    hit = CONTEXT.get(d["id"])
    if hit and hit[0] == key:
        ctx = hit[1]
        ctx.__init__(d, ctx.outline, ctx.band, ctx.casting_volume, ctx.z_range)      # the job's current data
        return ctx
    ctx = rn.context(job)
    CONTEXT[d["id"]] = (key, ctx)
    return ctx


def _review(jid: str) -> dict:
    f = _job_dir(jid) / f"{jid}.review.json"
    return json.loads(f.read_text()) if f.exists() else {"starred": []}


def _err(e: Exception) -> str:
    return "; ".join(e.problems) if isinstance(e, JobError) else f"{type(e).__name__}: {e}"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "job"
    s = s if s[0].isalpha() else "j_" + s
    return f"{s[:40]}_{hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]}"


def _closed(shape) -> tuple[bool, str]:
    from OCP.BRep import BRep_Tool

    solids = shape.solids()
    if not solids:
        return False, "the file holds no solid"
    open_shells = sum(not BRep_Tool.IsClosed_s(sh.wrapped) for sh in shape.shells())
    if open_shells:
        return False, f"{open_shells} shell(s) are not closed"
    return True, f"{len(solids)} closed solid(s)"


def _glb(shape, tol: float = 0.2) -> bytes:
    from build123d import export_gltf

    from core.occ import baked
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.glb"
        export_gltf(baked(shape), str(f), binary=True, linear_deflection=tol, angular_deflection=0.3)
        return f.read_bytes()


# ---- the offer ---------------------------------------------------------------------------------------
@app.get("/api/layouts/settings")
def settings() -> dict:
    st = rc.settings()
    return {"alloys": st["alloys"], "processes": st["processes"],
            "recipe_sets": {k: v["label"] for k, v in RECIPE_SETS.items()},
            "families": ["row", "grid", "radial"], "templates": list(rn.TEMPLATES), "rotations": [0, 90, 180, 270],
            "limits": default_limits()}


@app.get("/api/layouts/recipe")
def recipe(process: str, alloy: str, recipe_set: str = "presets") -> dict:
    """A recipe's values and what each phase still lacks."""
    rs = RECIPE_SETS.get(recipe_set)
    if rs is None:
        raise HTTPException(404, f"no recipe set '{recipe_set}'")
    f = rs["dir"] / f"{rc.recipe_id(process, alloy)}.json"
    if not f.exists():
        raise HTTPException(404, f"no recipe {f.name} in {rs['label']}")
    v = json.loads(f.read_text())
    return {"id": f.stem, "values": v, "missing": rc.missing_all(v), "keys": rc.required_keys()}


# ---- the part --------------------------------------------------------------------------------------
@app.post("/api/layouts/upload")
async def upload(request: Request, name: str) -> dict:
    """A STEP part as the raw body: kept by its content hash; is it a closed solid, its size and volume,
    and the two parting suggestions of the editor's fit (each a move putting the parting plane at z = 0)."""
    from core import fit
    from core.part import load_source

    safe = Path(name).name
    if Path(safe).suffix.lower() not in (".step", ".stp"):
        raise HTTPException(422, f"{safe}: the part must be a STEP file")
    data = await request.body()
    if not data:
        raise HTTPException(422, "the file is empty")
    sha = hashlib.sha256(data).hexdigest()
    folder = JOBS / "_uploads" / sha[:16]
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / safe
    f.write_bytes(data)
    try:
        shape = load_source(f).shape
    except Exception as e:                                          # noqa: BLE001
        raise HTTPException(422, f"{safe} does not read as a part: {e}")
    ok, why = _closed(shape)
    bb = shape.bounding_box()
    out = {"upload": sha[:16], "name": safe, "closed": ok, "closed_detail": why, "volume_mm3": float(shape.volume),
           "bbox": [[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]], "suggestions": []}
    if ok:
        lo, hi = out["bbox"]
        out["suggestions"] = fit.suggest(shape) + [{
            "id": "as_is", "label": "As in the file (its z = 0 is the parting plane)", "move": np.eye(4).tolist(),
            "bbox": out["bbox"], "size": [h - l for l, h in zip(lo, hi)], "below": max(-lo[2], 0.0), "above": max(hi[2], 0.0)}]
    (folder / "info.json").write_text(json.dumps(out))
    return out


@app.get("/api/layouts/upload/{up}/part.glb")
def upload_glb(up: str) -> Response:
    from core.part import load_source

    folder = JOBS / "_uploads" / re.sub(r"[^0-9a-f]", "", up)
    files = [x for x in folder.glob("*") if x.suffix.lower() in (".step", ".stp")]
    if not files:
        raise HTTPException(404, "no such upload")
    cache = folder / "part.glb"
    if not cache.exists():
        cache.write_bytes(_glb(load_source(files[0]).shape))
    return Response(cache.read_bytes(), media_type="model/gltf-binary")


# ---- jobs ------------------------------------------------------------------------------------------
def _problems(job_data: dict, shape_bbox, closed: bool) -> list[str]:
    """Before running: a closed solid, a flask larger than the part and its clearances, every recipe
    value each phase needs."""
    out = []
    if not closed:
        out.append("the part is not a closed solid")
    v = job_data["recipe"]["values"]
    miss = rc.missing_all(v)
    for step, keys in miss.items():
        out.append(f"the recipe lacks for {step}: " + ", ".join(k["path"] for k in keys))
    if not miss:
        s = 1 + float(v["shrinkage_pct"]) / 100
        lo, hi = (np.asarray(x, float) * s for x in shape_bbox)
        cl = v["layout"]["clearance"]
        fl = job_data["inputs"]["flask"]
        for i, (side, key) in enumerate((("length", "length"), ("width", "width"))):
            need = float(hi[i] - lo[i]) + 2 * float(cl["flask_wall_mm"])
            if need > float(fl[key]) + 1e-9:
                out.append(f"the flask is {fl[key]:g} mm in {side}; the part needs {need:.0f} with the wall clearance")
        if max(hi[2], 0) + float(cl["cope_above_mm"]) > float(fl["cope_height"]) + 1e-9:
            out.append(f"the cope is {fl['cope_height']:g} mm high; the part needs {max(hi[2], 0) + float(cl['cope_above_mm']):.0f}")
        if max(-lo[2], 0) + float(cl["drag_below_mm"]) > float(fl["drag_height"]) + 1e-9:
            out.append(f"the drag is {fl['drag_height']:g} mm deep; the part needs {max(-lo[2], 0) + float(cl['drag_below_mm']):.0f}")
    return out


@app.post("/api/layouts/jobs")
def create_job(spec: dict = Body(...)) -> dict:
    """{name, upload, process, alloy, recipe_set, flask, parting: {move, source}, sprue: {mode, position},
    limits, overrides, face_tags}.  Refused with the problems found when the inputs cannot run."""
    from core.occ import transformed
    from core.part import load_source

    folder = JOBS / "_uploads" / re.sub(r"[^0-9a-f]", "", str(spec.get("upload", "")))
    info_f = folder / "info.json"
    if not info_f.exists():
        raise HTTPException(422, "upload the part first")
    info = json.loads(info_f.read_text())
    rs = RECIPE_SETS.get(spec.get("recipe_set", "presets"))
    if rs is None:
        raise HTTPException(422, f"no recipe set '{spec.get('recipe_set')}'")
    jid = _slug(spec.get("name") or Path(info["name"]).stem)
    jdir = JOBS / jid
    jdir.mkdir(parents=True)
    part = jdir / info["name"]
    shutil.copyfile(folder / info["name"], part)
    move = np.asarray(spec["parting"]["move"], float)
    T = {"rotation": move[:3, :3].tolist(), "translation": move[:3, 3].tolist()}
    lim = default_limits()
    lim.update(spec.get("limits") or {})
    try:
        data = new(jid, part.name, spec["process"], spec["alloy"], spec["flask"], part_transform=T,
                   parting_source=spec["parting"].get("source", "auto"), sprue_mode=spec["sprue"]["mode"],
                   sprue_position=spec["sprue"].get("position"), overrides=spec.get("overrides") or {},
                   face_tags=spec.get("face_tags") or [], limits=lim, base_dir=jdir)
        job = GatingJob(data, jdir / f"{jid}.job.json")
        job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=rs["dir"]))
    except (JobError, KeyError, ValueError) as e:
        shutil.rmtree(jdir, ignore_errors=True)
        raise HTTPException(422, {"problems": e.problems if isinstance(e, JobError) else [str(e)]})
    placed = transformed(load_source(part).shape, np.asarray(move))
    bb = placed.bounding_box()
    probs = _problems(job.data, ([bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]), info["closed"])
    if probs:
        shutil.rmtree(jdir, ignore_errors=True)
        raise HTTPException(422, {"problems": probs})
    job.save()
    (jdir / "meta.json").write_text(json.dumps({"name": spec.get("name") or info["name"], "recipe_set": spec.get("recipe_set", "presets"),
                                                "created": time.time(), "upload": spec["upload"]}))
    return summary(jid)


@app.get("/api/layouts/jobs")
def list_jobs() -> list[dict]:
    out = []
    for d in sorted(JOBS.glob("*/meta.json"), key=lambda p: -p.stat().st_mtime):
        jid = d.parent.name
        try:
            out.append(summary(jid))
        except Exception:                                           # noqa: BLE001
            continue
    return out


@app.get("/api/layouts/jobs/{jid}")
def summary(jid: str) -> dict:
    job = _load(jid)
    d = job.data
    meta = json.loads((_job_dir(jid) / "meta.json").read_text())
    return {"id": jid, "name": meta["name"], "recipe_set": meta["recipe_set"], "inputs": d["inputs"],
            "recipe": {"id": d["recipe"]["id"], "values": d["recipe"]["values"]},
            "status": job.status(), "phases": d["phases"], "counts": job.counts(),
            "progress": PROGRESS.get(jid, {"running": False}), "starred": _review(jid)["starred"]}


@app.post("/api/layouts/jobs/{jid}/inputs")
def set_inputs(jid: str, changes: dict = Body(...)) -> dict:
    """Change inputs (limits, flask, sprue, face_tags, overrides); later phases go out of date."""
    with _LOCK:
        if PROGRESS.get(jid, {}).get("running"):
            raise HTTPException(409, "the job is running")
        job = _load(jid)
        allowed = {"limits", "flask", "sprue", "face_tags", "overrides"}
        bad = sorted(set(changes) - allowed)
        if bad:
            raise HTTPException(422, f"these inputs cannot change here: {bad}")
        if "limits" in changes:
            lim = dict(job.data["inputs"]["limits"])
            lim.update(changes["limits"])
            changes = {**changes, "limits": lim}
        try:
            stale = job.set_inputs(**changes)
            if "overrides" in changes:
                meta = json.loads((_job_dir(jid) / "meta.json").read_text())
                job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=RECIPE_SETS[meta["recipe_set"]]["dir"]))
        except JobError as e:
            raise HTTPException(422, {"problems": e.problems})
        job.save()
        return {**summary(jid), "made_stale": stale}


# ---- running ---------------------------------------------------------------------------------------
PHASE_LABELS = {"modulus": "Modulus analysis", "ingate_candidates": "Ingate candidates",
                "layouts": "Cavity layouts", "runners": "Runners and sizing", "metrics": "Metrics"}


def _run(jid: str, start: str | None) -> None:
    p = PROGRESS[jid]
    try:
        job = _load(jid)
        status = job.status()
        first = start or next((ph for ph in ORDER if status[ph] != "done"), None)
        if first is None:
            p.update(running=False, phase=None, message="every phase is done")
            return
        live: list[dict] = []
        p["live"] = live
        for ph in ORDER[ORDER.index(first):]:
            p.update(phase=ph, message=f"{PHASE_LABELS[ph]}...", phase_started=time.time())
            if ph == "modulus":
                md.run(job)
            elif ph == "ingate_candidates":
                ig.run(job)
            elif ph == "layouts":
                def hear(ev):
                    if "counted" in ev:
                        p.update(counted=ev["counted"], by_family=ev["by_family"],
                                 message=f"{ev['counted']} layout combinations counted")
                    else:
                        p.update(placed=ev["placed"], rejected=ev["rejected"], duplicates=ev["duplicates"])
                        if ev["layout"] is not None:
                            live.append(ev["layout"])
                        p["message"] = f"{ev['placed']} layouts placed, {ev['rejected']} rejected"
                ar.run(job, _context(job), progress=hear)
            elif ph == "runners":
                rn.run(job, _context(job))
            else:
                mt.run(job, _context(job))
            job.save()
        p.update(phase=None, message="done", live=[])
    except ar.CountExceeded as e:
        p.update(error=str(e), count_exceeded={"count": e.count, "threshold": e.threshold, "by": e.by})
        try:
            job.save()
        except Exception:                                           # noqa: BLE001
            pass
    except Exception as e:                                          # noqa: BLE001
        p.update(error=_err(e), trace=traceback.format_exc()[-2000:])
        try:
            job.save()
        except Exception:                                           # noqa: BLE001
            pass
    finally:
        p.update(running=False, finished=time.time())


@app.post("/api/layouts/jobs/{jid}/run")
def run(jid: str, start: str | None = Body(None, embed=True)) -> dict:
    """Run the phases not done or out of date (or from ``start`` on), in the background."""
    with _LOCK:
        if PROGRESS.get(jid, {}).get("running"):
            raise HTTPException(409, "the job is already running")
        if start is not None and start not in ORDER:
            raise HTTPException(422, f"no phase '{start}'")
        _load(jid)
        PROGRESS[jid] = {"running": True, "started": time.time(), "phase": None, "message": "starting",
                         "error": None, "live": []}
        threading.Thread(target=_run, args=(jid, start), daemon=True).start()
    return progress(jid)


@app.get("/api/layouts/jobs/{jid}/progress")
def progress(jid: str) -> dict:
    p = dict(PROGRESS.get(jid, {"running": False}))
    p["live_count"] = len(p.pop("live", []) or [])
    p.pop("trace", None)
    job = _load(jid)
    return {**p, "status": job.status(), "phases": job.data["phases"], "counts": job.counts()}


# ---- the part analysis -----------------------------------------------------------------------------
@app.get("/api/layouts/jobs/{jid}/analysis")
def analysis(jid: str) -> dict:
    """Zones, hot spots, ingate candidates (and the stretches discarded, with why)."""
    job = _load(jid)
    d = job.data
    if d["modulus"] is None:
        raise HTTPException(409, "run the modulus analysis first")
    m = {k: d["modulus"][k] for k in ("voxel_size", "min_wall", "volume", "zones", "hot_spots", "adjacency")}
    return {"modulus": m, "ingates": d["ingate_candidates"],
            "disabled": d["inputs"]["limits"]["disabled_ingates"], "face_tags": d["inputs"]["face_tags"]}


@app.get("/api/layouts/jobs/{jid}/part_mesh")
def part_mesh(jid: str) -> Response:
    """The part as placed (mold frame) for the heatmap: positions, triangles, the B-rep face of each
    triangle (to tag faces) and a local modulus per vertex (the thickness field / 6 just inside)."""
    job = _load(jid)
    if job.data["modulus"] is None:
        raise HTTPException(409, "run the modulus analysis first")
    cache = _job_dir(jid) / f"{jid}.part_mesh.json"
    if cache.exists() and cache.stat().st_mtime >= (_job_dir(jid) / job.data["modulus"]["field"]).stat().st_mtime:
        return Response(cache.read_bytes(), media_type="application/json")
    surf = md.triangulate(md.part_in_mold(job), 0.3, 0.4)
    field = dict(np.load(_job_dir(jid) / job.data["modulus"]["field"]))
    h = float(field["h"])
    # split triangles (each into four) until no edge is longer than two voxels, so a flat face meshed
    # as two triangles is still coloured across it (at most ~400 k triangles)
    tri, face = surf.tri, surf.face_of
    for _ in range(8):
        e = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2).max(1)
        big = e > 2 * h
        if not big.any() or len(tri) + 3 * big.sum() > 400_000:
            break
        a, b, c = tri[big, 0], tri[big, 1], tri[big, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        new = np.concatenate([np.stack(x, 1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
        tri = np.concatenate([tri[~big], new])
        face = np.concatenate([face[~big], np.tile(face[big], 4)])
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    pts = tri.reshape(-1, 3)
    probe = pts - 0.75 * h * np.repeat(n, 3, axis=0)                  # just inside, off the surface
    lt = ig._lookup(field, "thickness", probe).astype(float)
    out = {"positions": np.round(pts, 2).ravel().tolist(), "triangles": list(range(len(pts))),
           "faces": face.tolist(), "modulus": np.round(lt / 6, 3).tolist()}
    body = json.dumps(out).encode()
    cache.write_bytes(body)
    return Response(body, media_type="application/json")


# ---- the layouts -----------------------------------------------------------------------------------
def _cards(jid: str) -> list[dict]:
    job = _load(jid)
    d = job.data
    p = PROGRESS.get(jid, {})
    routed = {r["layout"]: r for r in d["runners"] or []}
    rej4 = {r["id"]: r for r in d["rejected"] if r["phase"] == "runners"}
    met = {m["layout"]: m for m in d["metrics"] or []}
    star = set(_review(jid)["starred"])
    layouts = d["layouts"] if d["layouts"] is not None else [dict(x) for x in p.get("live", [])]
    out = []
    for i, lay in enumerate(layouts):
        lid = lay["id"] if d["layouts"] is not None else f"pending_{i}"
        state = "routed" if lid in routed else "rejected" if lid in rej4 else "pending"
        out.append({"id": lid, "n": lay["n"], "family": lay["family"], "template": lay["template"],
                    "symmetry": lay["symmetry"], "rotation_deg": lay["rotation_deg"], "ingate": lay["ingate"],
                    "arrangement": lay["arrangement"], "state": state, "metrics": met.get(lid),
                    "turns": None, "starred": lid in star,
                    "reasons": rej4[lid]["reasons"] if lid in rej4 else None})
    return out


@app.get("/api/layouts/jobs/{jid}/layouts")
def layouts(jid: str, n: str | None = None, family: str | None = None, template: str | None = None,
            ingate: str | None = None, rotation: str | None = None, state: str = "routed", starred: bool = False,
            sort: str = "id", desc: bool = False, offset: int = 0, limit: int = 60) -> dict:
    """The grid: filtered, sorted by any metric (or id / n), a page at a time, with the values each filter
    can take."""
    cards = _cards(jid)
    facets = {k: sorted({c[k] for c in cards}, key=str) for k in ("n", "family", "template", "ingate", "rotation_deg", "state")}

    def pick(val, key, cast=str):
        return val is None or val == "" or str(cast(key)) in val.split(",")
    sel = [c for c in cards if (state == "all" or c["state"] == state) and pick(n, c["n"]) and pick(family, c["family"])
           and pick(template, c["template"]) and pick(ingate, c["ingate"]) and pick(rotation, c["rotation_deg"])
           and (not starred or c["starred"])]

    def key(c):
        if sort in ("id",):
            return (int(re.sub(r"\D", "", c["id"]) or 0),)
        if sort == "n":
            return (c["n"],)
        v = (c["metrics"] or {}).get(sort)
        return (v is None, v if v is not None else 0)
    sel.sort(key=key, reverse=desc)
    return {"total": len(sel), "all": len(cards), "offset": offset, "items": sel[offset:offset + limit], "facets": facets}


def _layout_and_record(job: GatingJob, lid: str):
    d = job.data
    lay = next((x for x in d["layouts"] or [] if x["id"] == lid), None)
    if lay is None:
        rej = next((r for r in d["rejected"] if r["id"] == lid and r["phase"] == "layouts"), None)
        if rej is None or not rej["params"].get("cavities"):
            raise HTTPException(404, f"no layout '{lid}'")
        lay = rej["params"]
    rec = next((r for r in d["runners"] or [] if r["layout"] == lid), None)
    return lay, rec


@app.get("/api/layouts/jobs/{jid}/layouts/{lid}/thumb.svg")
def thumb(jid: str, lid: str, w: int | None = None) -> Response:
    job = _load(jid)
    if lid.startswith("pending_"):                # placed by a running Phase 3, not routed yet: cavities only
        live = PROGRESS.get(jid, {}).get("live", [])
        i = int(lid[8:]) if lid[8:].isdigit() else -1
        if not 0 <= i < len(live):
            raise HTTPException(404, f"no layout '{lid}'")
        lay, rec = live[i], None
    else:
        lay, rec = _layout_and_record(job, lid)
    if rec is None and not lid.startswith("pending_") and job.data["layouts"] is not None and \
            any(x["id"] == lid for x in job.data["layouts"]):
        try:                                  # rejected in routing: draw what it would have been
            rec = rn.route(lay, _context(job))
        except rn.Reject:
            rec = None
    ctx = _context(job)
    wall = float(job.data["recipe"]["values"]["layout"]["clearance"]["flask_wall_mm"])
    s = thumbs.svg(lay, rec, ctx.outline, ctx.scale, job.data["inputs"]["flask"], wall, w)
    return Response(s, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


@app.get("/api/layouts/jobs/{jid}/layouts/{lid}")
def layout_detail(jid: str, lid: str) -> dict:
    job = _load(jid)
    lay, rec = _layout_and_record(job, lid)
    d = job.data
    return {"layout": lay, "runners": rec, "metrics": next((m for m in d["metrics"] or [] if m["layout"] == lid), None),
            "rejected": [r for r in d["rejected"] if r["id"] == lid],
            "assembly": d["assemblies"].get(lid), "starred": lid in _review(jid)["starred"],
            "candidate": next((c for c in (d["ingate_candidates"] or {}).get("candidates", []) if c["id"] == lay["ingate"]), None)}


@app.post("/api/layouts/jobs/{jid}/layouts/{lid}/star")
def star(jid: str, lid: str, starred: bool = Body(..., embed=True)) -> dict:
    with _LOCK:
        r = _review(jid)
        s = set(r["starred"])
        (s.add if starred else s.discard)(lid)
        r["starred"] = sorted(s, key=lambda x: int(re.sub(r"\D", "", x) or 0))
        (_job_dir(jid) / f"{jid}.review.json").write_text(json.dumps(r))
    return r


@app.post("/api/layouts/jobs/{jid}/layouts/{lid}/build")
def build(jid: str, lid: str) -> dict:
    """Phase 5 for one layout: the 3D model in the CAD module, its STEP and checks."""
    with _LOCK:
        job = _load(jid)
    try:
        rec = asm.assemble(job, lid, _job_dir(jid))
    except (asm.AssemblyError, JobError) as e:
        raise HTTPException(422, _err(e))
    with _LOCK:
        cur = _load(jid)                       # written by assemble on its copy: record it on the saved job
        cur.set_assembly(lid, job.data["assemblies"][lid])
        cur.save()
    for f in _job_dir(jid).glob(f"{jid}.{lid}.*.glb"):
        f.unlink()
    return {k: v for k, v in rec.items() if k != "issues"}


@app.get("/api/layouts/jobs/{jid}/layouts/{lid}/model/{what}.glb")
def model(jid: str, lid: str, what: str) -> Response:
    """The built model as glTF: ``cavities`` or ``gating``."""
    from core.document import DesignDocument
    from core.regen import regenerate

    if what not in ("cavities", "gating"):
        raise HTTPException(404, "cavities or gating")
    job = _load(jid)
    a = job.data["assemblies"].get(lid)
    if a is None:
        raise HTTPException(409, "build the layout first")
    f = _job_dir(jid) / f"{jid}.{lid}.{what}.glb"
    if not f.exists():
        res = regenerate(DesignDocument.load(_job_dir(jid) / a["design"]))
        from build123d import Compound
        cav = Compound([res.part.shape, *res.part.cavities.values()])
        gat = Compound(list(res.gating_solids().values()))
        (_job_dir(jid) / f"{jid}.{lid}.cavities.glb").write_bytes(_glb(cav, 0.3))
        (_job_dir(jid) / f"{jid}.{lid}.gating.glb").write_bytes(_glb(gat, 0.1))
    return Response(f.read_bytes(), media_type="model/gltf-binary")


@app.get("/api/layouts/jobs/{jid}/layouts/{lid}/step")
def step(jid: str, lid: str) -> FileResponse:
    job = _load(jid)
    a = job.data["assemblies"].get(lid)
    if a is None:
        raise HTTPException(409, "build the layout first")
    return FileResponse(_job_dir(jid) / a["step"], media_type="application/step", filename=a["step"])


@app.get("/api/layouts/jobs/{jid}/rejected")
def rejected(jid: str, code: str | None = None, offset: int = 0, limit: int = 60) -> dict:
    """Rejected layouts grouped by reason (a layout counts under each of its reasons); with ``code``, that
    group's layouts."""
    job = _load(jid)
    rej = job.data["rejected"]
    groups: dict[str, dict] = {}
    for r in rej:
        for x in r["reasons"]:
            g = groups.setdefault(x["code"], {"code": x["code"], "phase": r["phase"], "count": 0, "example": x.get("message")})
            g["count"] += 1
    items = []
    if code:
        for r in rej:
            if any(x["code"] == code for x in r["reasons"]):
                p = r["params"]
                items.append({"id": r["id"], "phase": r["phase"], "reasons": r["reasons"],
                              "n": p.get("n"), "family": p.get("family"), "template": p.get("template"),
                              "rotation_deg": p.get("rotation_deg"), "ingate": p.get("ingate"),
                              "drawable": bool(p.get("cavities"))})
    return {"groups": sorted(groups.values(), key=lambda g: -g["count"]), "total": len(rej), "counts": job.counts(),
            "items": items[offset:offset + limit], "items_total": len(items)}


# ---- the built UI, when there is one ---------------------------------------------------------------
if UI_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
