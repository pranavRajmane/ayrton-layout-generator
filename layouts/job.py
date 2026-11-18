"""The GatingJob: one run of the automated layout generator, as plain JSON.

Validated by ``schema/gating_job.v<version>.json``.  ``inputs`` are the engineer's; every
phase reads the sections it depends on and writes its own section, with the hash of what
it read, so a saved job can be reopened and any phase re-run:

    inputs + recipe ─> modulus ─> ingate_candidates ─> layouts (+ rejected) ─> runners (+ rejected) ─> metrics
                                                                   assemblies (Phase 5, on demand, per layout)

A phase is *stale* when the hash of its inputs now differs from the one it recorded.
Writing or clearing a phase clears every phase after it, so the job never holds results
built from different inputs.  Same inputs give byte-identical sections: ids are given in
generation order and nothing reads the clock except the runtime, kept in ``phases``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import jsonschema

SCHEMA_VERSION = "1.0"
SCHEMA_DIR = Path(__file__).parent / "schema"
UNITS = {"length": "mm", "mass": "kg", "time": "s"}

# Phase -> the job paths it reads.  Order is run order.
PHASES: dict[str, tuple[str, ...]] = {
    "modulus": ("inputs.part", "inputs.parting", "recipe.settings.modulus"),
    "ingate_candidates": ("inputs.part", "inputs.parting", "inputs.face_tags", "modulus", "recipe.values",
                          "recipe.settings.ingates"),
    "layouts": ("inputs.flask", "inputs.sprue", "inputs.limits", "inputs.seed", "modulus", "ingate_candidates",
                "recipe.values", "recipe.settings.symmetry", "recipe.settings.layouts"),
    "runners": ("inputs.flask", "layouts", "recipe.values", "recipe.settings.runners"),
    "metrics": ("layouts", "runners", "recipe.values"),
}
ORDER = tuple(PHASES)
REJECTING = ("layouts", "runners")            # phases that add to `rejected`


class JobError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ---- canonical form ---------------------------------------------------------------------
def canonical_json(obj) -> str:
    """One text per value: sorted keys, no spaces, NaN refused."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_path(data: dict, path: str):
    v = data
    for k in path.split("."):
        if not isinstance(v, dict) or k not in v:
            return None
        v = v[k]
    return v


# ---- schema -----------------------------------------------------------------------------
@lru_cache
def schema(version: str = SCHEMA_VERSION) -> dict:
    f = SCHEMA_DIR / f"gating_job.v{version}.json"
    if not f.exists():
        raise JobError([f"unknown GatingJob schema version '{version}'"])
    return json.loads(f.read_text())


def default_limits() -> dict:
    return {"n_min": None, "n_max": None, "families": None, "rotations_deg": None, "templates": None,
            "ingates": None, "disabled_ingates": [], "riser_keepouts": False, "count_threshold": None}


def new(job_id: str, part_source: str | Path, process: str, alloy: str, flask: dict, *,
        part_transform: dict | None = None, parting_source: str = "auto", sprue_mode: str = "free",
        sprue_position=None, units: str = "auto", overrides: dict | None = None,
        face_tags: list | None = None, limits: dict | None = None, seed: int = 0,
        base_dir: str | Path | None = None) -> dict:
    """A fresh job: inputs only, every phase pending.  ``part_source`` is stored as given
    (relative to ``base_dir`` when relative) and hashed now."""
    base = Path(base_dir) if base_dir else Path.cwd()
    src = Path(part_source)
    path = src if src.is_absolute() else base / src
    lim = default_limits()
    lim.update(limits or {})
    data = {
        "schema_version": SCHEMA_VERSION, "id": job_id, "units": dict(UNITS),
        "inputs": {
            "part": {"source": str(part_source), "units": units, "sha256": file_sha256(path)},
            "process": process, "alloy": alloy, "flask": dict(flask),
            "parting": {"part_transform": part_transform or {"rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                                                                          [0.0, 0.0, 1.0]],
                                                             "translation": [0.0, 0.0, 0.0]},
                        "source": parting_source},
            "sprue": {"mode": sprue_mode, "position": list(sprue_position) if sprue_position is not None else None},
            "overrides": dict(overrides or {}), "face_tags": list(face_tags or []), "limits": lim, "seed": seed,
        },
        "recipe": None,
        "phases": {p: {"state": "pending", "input_hash": None} for p in ORDER},
        "modulus": None, "ingate_candidates": None, "layouts": None, "runners": None,
        "rejected": [], "metrics": None, "assemblies": {},
    }
    check(data)
    return data


def _dupes(ids) -> list[str]:
    ids = list(ids)
    return sorted({i for i in ids if ids.count(i) > 1})


def problems(data: dict) -> list[str]:
    """Schema errors first; then references between sections."""
    try:
        v = jsonschema.Draft202012Validator(schema(data.get("schema_version", SCHEMA_VERSION)))
    except JobError as e:
        return e.problems
    out = [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
           for e in sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path))]
    if out:
        return out

    inp = data["inputs"]
    if inp["sprue"]["mode"] == "fixed" and inp["sprue"]["position"] is None:
        out.append("inputs/sprue: a fixed sprue needs a position")
    lim = inp["limits"]
    if lim["n_min"] is not None and lim["n_max"] is not None and lim["n_min"] > lim["n_max"]:
        out.append(f"inputs/limits: n_min {lim['n_min']} > n_max {lim['n_max']}")
    out += [f"inputs/face_tags: face {f} is tagged twice" for f in _dupes(t["face"] for t in inp["face_tags"])]

    zones: set[str] = set()
    if (m := data["modulus"]) is not None:
        zl = [z["id"] for z in m["zones"]]
        zones = set(zl)
        out += [f"modulus: zone id '{i}' is used twice" for i in _dupes(zl)]
        out += [f"modulus: hot spot id '{i}' is used twice" for i in _dupes(h["id"] for h in m["hot_spots"])]
        out += [f"modulus: hot spot {h['id']} is in no zone '{h['zone']}'" for h in m["hot_spots"]
                if h["zone"] not in zones]
        for e in m["adjacency"]:
            if e["a"] not in zones or e["b"] not in zones:
                out.append(f"modulus: adjacency {e['a']}-{e['b']} names a zone that does not exist")
            if e["a"] >= e["b"]:
                out.append(f"modulus: adjacency {e['a']}-{e['b']} is not in order (a < b)")
        out += [f"modulus: adjacency {p} is listed twice" for p in _dupes(f"{e['a']}-{e['b']}" for e in m["adjacency"])]

    cands: set[str] = set()
    if (c := data["ingate_candidates"]) is not None:
        if data["modulus"] is None:
            out.append("ingate_candidates: present without modulus")
        cl = [g["id"] for g in c]
        cands = set(cl)
        out += [f"ingate_candidates: id '{i}' is used twice" for i in _dupes(cl)]
        out += [f"ingate_candidates: {g['id']} targets no zone '{g['target_zone']}'" for g in c
                if zones and g["target_zone"] not in zones]
        for g in c:
            n = g["normal"]
            if abs((n[0] ** 2 + n[1] ** 2) ** 0.5 - 1.0) > 1e-6:
                out.append(f"ingate_candidates: {g['id']} normal is not a unit vector")

    layout_ids: set[str] = set()
    if (lay := data["layouts"]) is not None:
        if data["ingate_candidates"] is None:
            out.append("layouts: present without ingate_candidates")
        ll = [x["id"] for x in lay]
        layout_ids = set(ll)
        out += [f"layouts: id '{i}' is used twice" for i in _dupes(ll)]
        out += [f"layouts: key of '{i}' is used twice (duplicate layout)" for i in _dupes(x["key"] for x in lay)]
        for x in lay:
            if x["ingate"] not in cands:
                out.append(f"layouts: {x['id']} uses no ingate candidate '{x['ingate']}'")
            idx = sorted(cv["index"] for cv in x["cavities"])
            if idx != list(range(x["n"])):
                out.append(f"layouts: {x['id']} has cavities {idx}, expected 0..{x['n'] - 1}")
    rejected_ids = [r["id"] for r in data["rejected"]]
    out += [f"rejected: id '{i}' is used twice" for i in _dupes(rejected_ids)]
    for r in data["rejected"]:
        if data["phases"][r["phase"]]["state"] != "done":
            out.append(f"rejected: {r['id']} is from phase '{r['phase']}', which is not done")

    routed: set[str] = set()
    if (run := data["runners"]) is not None:
        if lay is None:
            out.append("runners: present without layouts")
        rl = [r["layout"] for r in run]
        routed = set(rl)
        out += [f"runners: layout '{i}' is routed twice" for i in _dupes(rl)]
        out += [f"runners: no layout '{i}'" for i in sorted(routed - layout_ids)]
        out += [f"runners: layout '{i}' is both routed and rejected" for i in sorted(routed & set(rejected_ids))]
        # every geometric layout either routed or was rejected while routing
        rej4 = {r["id"] for r in data["rejected"] if r["phase"] == "runners"}
        out += [f"runners: layout '{i}' was neither routed nor rejected" for i in sorted(layout_ids - routed - rej4)]
        out += [f"rejected: '{i}' was rejected in routing but is no layout" for i in sorted(rej4 - layout_ids)]
    if lay is not None:
        # a phase-3 rejection never shares an id with an accepted layout
        out += [f"layouts: '{i}' is both a layout and rejected in phase 'layouts'" for i in sorted(
            layout_ids & {r["id"] for r in data["rejected"] if r["phase"] == "layouts"})]

    if (met := data["metrics"]) is not None:
        ml = [x["layout"] for x in met]
        out += [f"metrics: layout '{i}' listed twice" for i in _dupes(ml)]
        out += [f"metrics: layout '{i}' has no runners" for i in sorted(set(ml) - routed)]
    out += [f"assemblies: no accepted layout '{i}'" for i in sorted(set(data["assemblies"]) - routed)]
    return out


def check(data: dict) -> None:
    p = problems(data)
    if p:
        raise JobError(p)


# ---- the job ----------------------------------------------------------------------------
class GatingJob:
    def __init__(self, data: dict, path: str | Path | None = None):
        data = copy.deepcopy(data)
        check(data)
        self._data = data
        self.path = Path(path) if path else None

    @property
    def data(self) -> dict:
        """Read-only view: change the job through the methods so hashes and staleness hold."""
        return copy.deepcopy(self._data)

    @property
    def base_dir(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    def part_path(self) -> Path:
        src = Path(self._data["inputs"]["part"]["source"])
        return src if src.is_absolute() else self.base_dir / src

    # ---- I/O -----------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "GatingJob":
        path = Path(path)
        return cls(json.loads(path.read_text()), path)

    def to_json(self) -> str:
        return json.dumps(self._data, indent=1, allow_nan=False)

    def save(self, path: str | Path | None = None) -> Path:
        """Atomic, like the design document: a temporary file beside the target replaces it."""
        path = Path(path) if path else self.path
        if path is None:
            raise ValueError("no path to save the job to")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(self.to_json())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.path = path
        return path

    # ---- hashing and staleness -----------------------------------------------------------
    def input_hash(self, phase: str) -> str:
        """Hash of everything ``phase`` reads, as the job holds it now."""
        return digest({p: get_path(self._data, p) for p in PHASES[phase]})

    def _own_stale(self, phase: str) -> bool:
        st = self._data["phases"][phase]
        return st["state"] == "done" and st["input_hash"] != self.input_hash(phase)

    def stale(self, phase: str) -> bool:
        """Done, but what it read has changed since, or an earlier phase is stale (its results
        would change when re-run, so everything built on them is out of date too)."""
        if self._data["phases"][phase]["state"] != "done":
            return False
        return any(self._own_stale(p) for p in ORDER[:ORDER.index(phase) + 1])

    def part_changed(self) -> bool:
        return file_sha256(self.part_path()) != self._data["inputs"]["part"]["sha256"]

    def refresh_part(self) -> list[str]:
        """Take the part file as it is now; returns the phases this made stale."""
        part = dict(self._data["inputs"]["part"], sha256=file_sha256(self.part_path()))
        return self.set_inputs(part=part)

    def status(self) -> dict[str, str]:
        """Per phase: pending / running / done / failed / stale."""
        return {p: ("stale" if self.stale(p) else self._data["phases"][p]["state"]) for p in ORDER}

    def first_stale(self) -> str | None:
        for p in ORDER:
            if self.status()[p] != "done":
                return p
        return None

    # ---- editing -------------------------------------------------------------------------
    def _commit(self, data: dict) -> None:
        check(data)
        self._data = data

    def set_inputs(self, **changes) -> list[str]:
        """Change inputs (top-level keys of ``inputs``); returns the phases this made stale."""
        data = copy.deepcopy(self._data)
        for k, v in changes.items():
            if k not in data["inputs"]:
                raise JobError([f"inputs has no '{k}'"])
            data["inputs"][k] = copy.deepcopy(v)
        self._commit(data)
        return [p for p in ORDER if self.stale(p)]

    def set_recipe(self, recipe: dict) -> None:
        data = copy.deepcopy(self._data)
        data["recipe"] = copy.deepcopy(recipe)
        self._commit(data)

    def start(self, phase: str) -> None:
        """Mark ``phase`` running and clear it and everything after it."""
        self._need_upstream(phase)
        data = copy.deepcopy(self._data)
        _clear_from(data, phase)
        data["phases"][phase] = {"state": "running", "input_hash": None}
        self._commit(data)

    def finish(self, phase: str, section, *, rejected: list | None = None, runtime_s: float | None = None,
               counts: dict | None = None, issues: list[str] | None = None) -> None:
        """Write ``phase``'s section (and, for layouts / runners, its rejections) with the hash of
        what it read.  Phases after it are cleared."""
        self._need_upstream(phase)
        if rejected and phase not in REJECTING:
            raise JobError([f"phase '{phase}' does not reject layouts"])
        data = copy.deepcopy(self._data)
        h = self.input_hash(phase)
        _clear_from(data, phase)
        data[phase] = copy.deepcopy(section)
        for r in rejected or []:
            data["rejected"].append({**copy.deepcopy(r), "phase": phase})
        data["phases"][phase] = {"state": "done", "input_hash": h, "runtime_s": runtime_s,
                                 "counts": dict(counts or {}), "error": None, "issues": list(issues or [])}
        self._commit(data)

    def fail(self, phase: str, error: str) -> None:
        data = copy.deepcopy(self._data)
        _clear_from(data, phase)
        data["phases"][phase] = {"state": "failed", "input_hash": None, "error": error}
        self._commit(data)

    def clear(self, phase: str) -> None:
        """Forget ``phase`` and everything after it (to re-run from there)."""
        data = copy.deepcopy(self._data)
        _clear_from(data, phase)
        self._commit(data)

    def set_assembly(self, layout_id: str, record: dict) -> None:
        data = copy.deepcopy(self._data)
        data["assemblies"][layout_id] = copy.deepcopy(record)
        self._commit(data)

    def _need_upstream(self, phase: str) -> None:
        if phase not in PHASES:
            raise JobError([f"no phase '{phase}'"])
        if self._data["recipe"] is None:
            raise JobError(["the job has no recipe snapshot yet (layouts.recipe.resolve)"])
        st = self.status()
        behind = [p for p in ORDER[:ORDER.index(phase)] if st[p] != "done"]
        if behind:
            raise JobError([f"phase '{phase}' needs {', '.join(f'{p} ({st[p]})' for p in behind)} first"])

    # ---- reading -------------------------------------------------------------------------
    def accepted(self) -> list[dict]:
        """Layouts that routed, in layout order (empty until runners are done)."""
        if self._data["runners"] is None:
            return []
        routed = {r["layout"] for r in self._data["runners"]}
        return [copy.deepcopy(x) for x in self._data["layouts"] or [] if x["id"] in routed]

    def counts(self) -> dict[str, int]:
        """enumerated = accepted + rejected, once layouts and runners have run."""
        d = self._data
        lay = d["layouts"] or []
        rej3 = sum(r["phase"] == "layouts" for r in d["rejected"])
        rej4 = sum(r["phase"] == "runners" for r in d["rejected"])
        out = {"enumerated": len(lay) + rej3, "geometric_ok": len(lay), "rejected": rej3 + rej4}
        if d["runners"] is not None:
            out["accepted"] = len(d["runners"])
        return out

    def results_fingerprint(self) -> str:
        """Hash of every result section (not timings): equal for two runs of the same job."""
        d = self._data
        return digest({k: d[k] for k in ("recipe", "modulus", "ingate_candidates", "layouts", "runners",
                                          "rejected", "metrics")})


def _clear_from(data: dict, phase: str) -> None:
    i = ORDER.index(phase)
    for p in ORDER[i:]:
        data[p] = None
        data["phases"][p] = {"state": "pending", "input_hash": None}
    later = set(ORDER[i:])
    data["rejected"] = [r for r in data["rejected"] if r["phase"] not in later]
    if "layouts" in later or "runners" in later:
        data["assemblies"] = {}
