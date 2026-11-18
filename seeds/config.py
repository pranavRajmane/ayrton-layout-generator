"""The knobs file and the foundry profile: loading, completeness, sampling.

Nothing here has a default value: a knob range or a profile number left null
stops generation with the list of what is missing.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KNOBS_FILE = Path(__file__).with_name("knobs.json")


class ConfigError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _leaves(tree: dict, prefix: str = ""):
    for k, v in tree.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        name = f"{prefix}{k}"
        if "range" in v or "choices" in v:
            yield name, v
        else:
            yield from _leaves(v, f"{name}.")


def load_knobs(path: str | Path | dict | None = None) -> dict[str, dict]:
    """Flat {knob name: spec} (nested names joined with '.')."""
    tree = path if isinstance(path, dict) else json.loads(Path(path or KNOBS_FILE).read_text())
    knobs = dict(_leaves(tree))
    problems = []
    for name, spec in knobs.items():
        if "range" in spec:
            r = spec["range"]
            if not isinstance(r, list) or len(r) != 2 or any(x is None for x in r):
                problems.append(f"knob {name}: range not set")
            elif not all(isinstance(x, (int, float)) and math.isfinite(x) for x in r) or r[0] > r[1]:
                problems.append(f"knob {name}: range {r} is not [low, high]")
        elif not spec["choices"]:
            problems.append(f"knob {name}: no choices left")
    if problems:
        raise ConfigError(problems)
    return knobs


def sample_knobs(knobs: dict[str, dict], rng: np.random.Generator) -> dict:
    out = {}
    for name in sorted(knobs):
        spec = knobs[name]
        if "choices" in spec:
            out[name] = spec["choices"][int(rng.integers(len(spec["choices"])))]
        elif spec.get("int"):
            lo, hi = int(spec["range"][0]), int(spec["range"][1])
            out[name] = int(rng.integers(lo, hi + 1))
        else:
            lo, hi = map(float, spec["range"])
            out[name] = float(rng.uniform(lo, hi)) if hi > lo else lo
    return out


CLEARANCES = ("flask_wall_mm", "flask_top_mm", "flask_bottom_mm", "riser_spacing_mm", "riser_to_casting_mm",
              "sprue_to_casting_mm", "cutoff_access_mm", "cavity_spacing_mm")


@dataclass
class FoundryProfile:
    """One customer's foundry.  Lengths mm, densities kg/m3, percentages in %.
    flask_size_mm: L x W x total height; parting_height_mm: the parting plane above the box bottom
    (the mold frame has the plane at z = 0 and the box centred on the Z axis, like the CAD module)."""
    process: str
    alloy: str
    flask_size_mm: tuple[float, float, float]
    parting_height_mm: float
    cavity_count: int
    gating_ratio: tuple[float, float, float]       # sprue : runner : ingate
    density_kg_m3: float
    discharge_coefficient: float
    volumetric_shrinkage_pct: float                # the liquid + solidification shrinkage feeders make up
    pattern_shrinkage_pct: float
    pouring_temp_c: tuple[float, float]
    basin_head_mm: float                           # metal over the sprue's top while pouring
    clearances: dict[str, float]

    @classmethod
    def from_dict(cls, d: dict) -> "FoundryProfile":
        need = ["process", "alloy", "flask_size_mm", "parting_height_mm", "cavity_count", "gating_ratio",
                "density_kg_m3", "discharge_coefficient", "volumetric_shrinkage_pct", "pattern_shrinkage_pct",
                "pouring_temp_c", "basin_head_mm", "clearances"]
        problems = [f"profile: {k} not set" for k in need if d.get(k) is None]
        cl = d.get("clearances") or {}
        problems += [f"profile: clearances.{k} not set" for k in CLEARANCES if cl.get(k) is None]
        if not problems:
            if len(d["flask_size_mm"]) != 3 or min(d["flask_size_mm"]) <= 0:
                problems.append("profile: flask_size_mm is L, W, H, all positive")
            elif not 0 < d["parting_height_mm"] < d["flask_size_mm"][2]:
                problems.append("profile: the parting plane must lie inside the flask")
            if len(d["gating_ratio"]) != 3 or min(d["gating_ratio"]) <= 0:
                problems.append("profile: gating_ratio has three positive members")
            if int(d["cavity_count"]) < 1:
                problems.append("profile: cavity_count must be at least 1")
            if d["basin_head_mm"] <= 0:
                problems.append("profile: basin_head_mm must be positive")
        if problems:
            raise ConfigError(problems)
        return cls(d["process"], d["alloy"], tuple(map(float, d["flask_size_mm"])), float(d["parting_height_mm"]),
                   int(d["cavity_count"]), tuple(map(float, d["gating_ratio"])), float(d["density_kg_m3"]),
                   float(d["discharge_coefficient"]), float(d["volumetric_shrinkage_pct"]),
                   float(d["pattern_shrinkage_pct"]), tuple(map(float, d["pouring_temp_c"])),
                   float(d["basin_head_mm"]), {k: float(cl[k]) for k in CLEARANCES})

    # the box in the mold frame
    @property
    def flask_lo(self) -> np.ndarray:
        L, W, H = self.flask_size_mm
        return np.array([-L / 2, -W / 2, -self.parting_height_mm])

    @property
    def flask_hi(self) -> np.ndarray:
        L, W, H = self.flask_size_mm
        return np.array([L / 2, W / 2, H - self.parting_height_mm])

    @property
    def cope_height(self) -> float:
        return self.flask_size_mm[2] - self.parting_height_mm
