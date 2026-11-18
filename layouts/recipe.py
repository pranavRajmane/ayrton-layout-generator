"""The recipe set a job runs with: the process x alloy preset, the job's overrides on top,
and the layout generator's numerical settings, frozen into the job as ``recipe``.

Which alloys and processes are offered is data (``settings.json``); what each phase needs
from the preset is data (``recipe_keys.json``).  A new alloy is a preset file, not code.
Nothing here supplies a foundry number: a null stays null and the phase that needs it
reports it missing.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from calculators.presets import PRESET_DIR
from layouts.job import ORDER, file_sha256, get_path

HERE = Path(__file__).parent
SETTINGS_FILE = HERE / "settings.json"
KEYS_FILE = HERE / "recipe_keys.json"
STEPS = ORDER + ("assembly",)


@lru_cache
def _read(path: str) -> dict:
    return json.loads(Path(path).read_text())


def settings(path: str | Path | None = None) -> dict:
    """The numerical settings, without the _comment keys."""
    return {k: copy.deepcopy(v) for k, v in _read(str(path or SETTINGS_FILE)).items() if not k.startswith("_")}


def required_keys(path: str | Path | None = None) -> dict[str, list[dict]]:
    return {k: v for k, v in _read(str(path or KEYS_FILE)).items() if not k.startswith("_")}


def recipe_id(process: str, alloy: str) -> str:
    return f"{process}_{alloy}"


def _set_path(tree: dict, path: str, value) -> None:
    keys = path.split(".")
    v = tree
    for k in keys[:-1]:
        v = v[k]
    v[keys[-1]] = value


def input_problems(inputs: dict, preset_dir: Path | None = None, settings_path: Path | None = None) -> list[str]:
    """Problems with the choice of recipe: alloy and process offered, preset present,
    every override naming a value the preset has."""
    st = settings(settings_path)
    out = []
    if inputs["alloy"] not in st["alloys"]:
        out.append(f"alloy '{inputs['alloy']}' is not offered (offered: {', '.join(st['alloys'])})")
    if inputs["process"] not in st["processes"]:
        out.append(f"process '{inputs['process']}' is not offered (offered: {', '.join(st['processes'])})")
    f = Path(preset_dir or PRESET_DIR) / f"{recipe_id(inputs['process'], inputs['alloy'])}.json"
    if not f.exists():
        out.append(f"no recipe set {f.name}")
        return out
    preset = json.loads(f.read_text())
    for path in sorted(inputs["overrides"]):
        parent, _, leaf = path.rpartition(".")
        holder = get_path(preset, parent) if parent else preset
        if not isinstance(holder, dict) or leaf not in holder:
            out.append(f"override '{path}': the recipe set has no such value")
    return out


def resolve(inputs: dict, preset_dir: Path | None = None, settings_path: Path | None = None) -> dict:
    """The recipe snapshot for a job (``GatingJob.set_recipe``)."""
    p = input_problems(inputs, preset_dir, settings_path)
    if p:
        from layouts.job import JobError
        raise JobError(p)
    rid = recipe_id(inputs["process"], inputs["alloy"])
    f = Path(preset_dir or PRESET_DIR) / f"{rid}.json"
    values = json.loads(f.read_text())
    for path in sorted(inputs["overrides"]):
        _set_path(values, path, copy.deepcopy(inputs["overrides"][path]))
    return {"id": rid, "source_sha256": file_sha256(f), "values": values, "settings": settings(settings_path)}


def missing(values: dict, step: str, keys_path: Path | None = None) -> list[dict]:
    """The recipe values ``step`` needs that are null (a list counts as null if any item is)."""
    out = []
    for k in required_keys(keys_path).get(step, []):
        v = get_path(values, k["path"])
        if v is None or (isinstance(v, list) and any(x is None for x in v)):
            out.append(dict(k))
    return out


def missing_all(values: dict, keys_path: Path | None = None) -> dict[str, list[dict]]:
    """Per step (the phases and assembly), what is missing; steps with nothing missing left out."""
    return {s: m for s in STEPS if (m := missing(values, s, keys_path))}
