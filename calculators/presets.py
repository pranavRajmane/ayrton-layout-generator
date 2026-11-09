"""Process and alloy presets (presets/<process>_<alloy>.json).

Values are foundry numbers: they come from the references the domain owner
trusts, recorded in each file's "source".  A null value is a TODO; whatever
needs it reports `preset.missing` instead of falling back to a guess.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.element import Severity, ValidationIssue

PRESET_DIR = Path(__file__).resolve().parents[1] / "presets"


@dataclass(frozen=True)
class Preset:
    id: str
    data: dict

    def get(self, path: str):
        """Dotted path, e.g. "limits.max_ingate_velocity_m_s"; None when unset."""
        v = self.data
        for k in path.split("."):
            if not isinstance(v, dict) or k not in v:
                return None
            v = v[k]
        return v

    def need(self, path: str, why: str, issues: list[ValidationIssue]):
        """The value, or None after recording what is missing and why it was wanted."""
        v = self.get(path)
        if v is None or (isinstance(v, list) and any(x is None for x in v)):
            issues.append(missing(self.id, path, why))
            return None
        return v


def missing(preset_id: str | None, path: str, why: str) -> ValidationIssue:
    where = f"preset '{preset_id}'" if preset_id else "the design (no process and alloy chosen)"
    return ValidationIssue("preset.missing", Severity.INFO, f"{why}: {where} has no {path}", value=path)


def preset_id(process: dict) -> str | None:
    if process.get("preset"):
        return process["preset"]
    if process.get("process") and process.get("alloy"):
        return f"{process['process']}_{process['alloy']}"
    return None


def load_preset(pid: str | None, directory: Path | None = None) -> Preset | None:
    if not pid:
        return None
    f = (directory or PRESET_DIR) / f"{pid}.json"
    if not f.exists():
        raise FileNotFoundError(f"no preset {f}")
    return Preset(pid, json.loads(f.read_text()))


def list_presets(directory: Path | None = None) -> list[str]:
    return sorted(p.stem for p in (directory or PRESET_DIR).glob("*.json"))
