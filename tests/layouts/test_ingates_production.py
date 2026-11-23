"""Phase 2 against production gating: part 15145827 is poured through six drag ingates (20 x 10 mm,
from its saved design ``parts/15145827 (1) - Part 1.gating.json``).  Each must have a candidate
within the merge distance of where it meets the part, facing the same way.  Slow: Phase 1 on this
part takes about 25 minutes (``pytest -m slow``)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from layouts import ingates as ig
from layouts import modulus
from layouts import recipe as rc
from layouts.job import GatingJob, new

ROOT = Path(__file__).resolve().parents[2]
PART = ROOT / "parts/15145827 (1) - Part 1.step"
DESIGN = ROOT / "parts/15145827 (1) - Part 1.gating.json"


def production():
    """The design's part placement (parting plane at its ingates' height moved to z = 0) and each
    ingate's line: (end at the part, unit direction toward the part)."""
    d = json.loads(DESIGN.read_text())
    gates = [e for e in d["elements"] if e["type"] == "ingate"]
    z = gates[0]["params"]["waypoints"][0][2]
    assert all(g["params"]["side"] == "drag" and g["params"]["waypoints"][0][2] == z for g in gates)
    t = d["part"]["transform"]
    tr = {"rotation": t["rotation"], "translation": [t["translation"][0], t["translation"][1], t["translation"][2] - z]}
    lines = {}
    for g in gates:
        a, b = (np.array(p[:2], float) for p in g["params"]["waypoints"][:2])
        lines[g["id"]] = (b, (b - a) / np.linalg.norm(b - a), g["params"])
    return tr, lines


@pytest.mark.slow
@pytest.mark.skipif(not (PART.exists() and DESIGN.exists()), reason="the production part is not in the repo (parts/ holds customer CAD)")
def test_production_ingates_are_among_the_candidates(tmp_path):
    tr, lines = production()
    size = next(iter(lines.values()))[2]
    data = new("p15145827", PART, "green_sand", "grey_iron",
               {"length": 1000.0, "width": 1000.0, "cope_height": 250.0, "drag_height": 250.0},
               part_transform=tr, parting_source="user",
               overrides={"layout.ingate.thickness_mm": size["thickness"],
                          "layout.ingate.aspect": size["width"] / size["thickness"]})
    job = GatingJob(data, tmp_path / "p.job.json")
    job.set_recipe(rc.resolve(job.data["inputs"], preset_dir=Path(__file__).parent / "recipes"))
    modulus.run(job)
    job.save()
    cands, _ = ig.run(job)
    shape = modulus.part_in_mold(job)
    loops = ig.cut(shape, -job.data["recipe"]["settings"]["ingates"]["parting_tolerance_mm"], 0.5)
    d = job.data["recipe"]["values"]["layout"]["ingate"]["merge_distance_mm"]
    loc = np.array([c["location"][:2] for c in cands])
    nrm = np.array([c["normal"] for c in cands])
    part_valid = shape.is_valid                  # this part file does not pass BRepCheck itself
    misses = []
    for gid, (end, toward, _) in sorted(lines.items()):
        out = -toward
        hit = ig.ray_hit(end + 40 * out, toward, loops, 100.0)
        assert hit is not None, gid
        contact = end + (40 - hit[0]) * out
        dist = np.where(nrm @ out > np.cos(np.radians(30)), np.linalg.norm(loc - contact, axis=1), np.inf)
        j = int(np.argmin(dist))
        if dist[j] > d:
            misses.append((gid, contact.round(1).tolist(), float(dist[j])))
            continue
        assert ig.stub_problems(shape, ig.stub(cands[j], job.data["recipe"]["values"]), part_valid) == [], gid
    assert not misses
