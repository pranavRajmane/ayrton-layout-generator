"""Meshes of the built seed designs for the contact sheet (run in the ayrton-gating env);
render_sheet_draw.py draws them (any env with matplotlib)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402

from core.document import DesignDocument  # noqa: E402
from core.regen import regenerate  # noqa: E402
from core.registry import REGISTRY  # noqa: E402

REGISTRY.discover("elements")
out = ROOT / "seed_runs" / "standard_model"
for folder in sorted(p for p in out.iterdir() if p.is_dir()):
    res = regenerate(DesignDocument.load(next(folder.glob("*.gating.json"))))
    items = [("casting", res.part.metal)] + [(("feeder" if b.element.category == "feeding" and "neck" not in i
                                               else "gating"), b.solid) for i, b in res.built.items()]
    arrays = {"ok": np.array(res.ok)}
    for k, (kind, shape) in enumerate(items):
        v, t = shape.tessellate(1.0, 0.5)
        arrays[f"{k}_{kind}_v"] = np.array([[p.X, p.Y, p.Z] for p in v])
        arrays[f"{k}_{kind}_t"] = np.asarray(t)
    np.savez(folder / "mesh.npz", **arrays)
    print(folder.name, res.ok)
