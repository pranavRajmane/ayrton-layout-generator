"""Demo: seed designs for parts/ayrton-standard-model.step, built into real CAD-module designs.

Uses the TEST knob ranges and a made-up profile from tests/seeds/synthetic.py (not foundry data).
Each design gets its own folder: the part, <part>.gating.json (opens in the editor), the seed spec,
and a STEP of the casting with all its gating and feeders."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build123d import Compound, export_step  # noqa: E402

from core.registry import REGISTRY  # noqa: E402
from core.part import read_shape  # noqa: E402
from seeds.build import build_design  # noqa: E402
from seeds.config import FoundryProfile  # noqa: E402
from seeds.feeding import build_feeding_graph  # noqa: E402
from seeds.generate import generate  # noqa: E402
from seeds.report import format_report  # noqa: E402
from seeds.voxel import VoxelPart  # noqa: E402
from tests.seeds.synthetic import knobs, profile  # noqa: E402

REGISTRY.discover("elements")
part = ROOT / "parts" / "ayrton-standard-model.step"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
prof_d = profile(flask_size_mm=[600.0, 400.0, 400.0], parting_height_mm=100.0)
prof = FoundryProfile.from_dict(prof_d)

vp = VoxelPart.from_shape(read_shape(part), 2.5, part_id="standard_model")
graph = build_feeding_graph(vp)
print("hot spots:", {h: round(graph.zones[h].modulus_mm, 1) for h in graph.hot_spots()})
run = generate(vp, graph, prof, n, seed, knobs())
print(format_report(run.report))

out = ROOT / "seed_runs" / "standard_model"
out.mkdir(parents=True, exist_ok=True)
summary = []
for i, d in enumerate(run.designs):
    folder = out / f"design_{i:02d}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    p = folder / part.name
    shutil.copy2(part, p)
    (folder / "seed_design.json").write_text(json.dumps(d, indent=2))
    doc, res = build_design(d, p, prof, folder)
    doc.save(folder / f"{p.stem}.gating.json")
    solids = [res.part.metal] + [b.solid for b in res.built.values()]
    export_step(Compound(children=solids), str(folder / "casting_with_gating.step"))
    rs = [e for e in d["elements"] if e["type"] == "riser"]
    line = {"folder": folder.name, "id": d["id"], "builds_ok": res.ok,
            "errors": [x.message for x in res.issues if x.severity.value == "error"],
            "risers": [f"{r['params']['style']} d{r['params']['d']:.0f} h{r['params']['height']:.0f} -> {','.join(r['attached_zones'])}" for r in rs],
            "ingates": sum(e["type"] == "ingate" for e in d["elements"])}
    summary.append(line)
    print(line)
(out / "summary.json").write_text(json.dumps({"report": run.report, "designs": summary}, indent=2))
