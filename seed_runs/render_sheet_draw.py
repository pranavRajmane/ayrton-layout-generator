"""Contact sheet: casting grey, feeders purple, gating orange.  Needs render_sheet.py run first."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

out = Path(__file__).resolve().parent / "standard_model"
COL = {"casting": "#9aa0a6", "feeder": "#7b3fa0", "gating": "#d9822b"}
folders = sorted(p for p in out.iterdir() if p.is_dir())
fig = plt.figure(figsize=(15, 9.5))
for n, folder in enumerate(folders):
    m = np.load(folder / "mesh.npz")
    seed = json.loads((folder / "seed_design.json").read_text())
    ax = fig.add_subplot(2, (len(folders) + 1) // 2, n + 1, projection="3d")
    allv = []
    for key in sorted(k for k in m.files if k.endswith("_v")):
        kind = key.split("_")[1]
        v, t = m[key], m[key[:-2] + "_t"]
        allv.append(v)
        ax.add_collection3d(Poly3DCollection(v[t], facecolors=COL[kind], edgecolors=COL[kind], linewidths=0.1, shade=True,
                                             lightsource=LightSource(315, 45)))
    v = np.vstack(allv)
    lo, hi = v.min(0), v.max(0)
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1)); ax.view_init(28, -55); ax.set_axis_off()
    rs = [e for e in seed["elements"] if e["type"] == "riser"]
    shared = any(len(e["attached_zones"]) > 1 for e in rs)
    gates = sum(e["type"] == "ingate" for e in seed["elements"])
    ax.set_title(f"{folder.name}: {len(rs)} feeder{'s' * (len(rs) > 1)}{' (shared)' if shared else ''}, "
                 f"{gates} ingate{'s' * (gates > 1)}\n" + ", ".join(f"d{e['params']['d']:.0f}" for e in rs)
                 + f"  |  {'built OK' if bool(m['ok']) else 'BUILD ERRORS'}", fontsize=10)
fig.suptitle("Seed designs for ayrton-standard-model (TEST knob ranges)", fontsize=13)
plt.tight_layout()
plt.savefig(out / "designs.png", dpi=110)
print(out / "designs.png")
