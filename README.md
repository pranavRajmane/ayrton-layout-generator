# Ayrton layout generator

Takes a part (STEP), a flask size and an alloy, and generates every valid multi-cavity gating layout:
sprue, runners and ingates. Each layout is checked and either accepted or rejected with its reasons;
an engineer reviews them in a grid and picks the ones worth simulating. Nothing is ranked or
auto-selected. Alloys: grey cast iron and SG (ductile) iron.

It is built on the Ayrton gating CAD module in the same repo (`core/`, `elements/`), which builds each
chosen layout in 3D. The CAD module's own editor (`api/`, `ui/`) is a separate app; see the end of this file.

| phase | what | code |
|---|---|---|
| 0 | GatingJob spec, recipe sets, phase bookkeeping | `layouts/job.py` |
| 1 | modulus, feeding zones, hot spots | `layouts/modulus.py` |
| 2 | ingate candidates on the parting line | `layouts/ingates.py` |
| 3 | cavity layouts in the flask (rows, grid / H-tree, radial) | `layouts/arrange.py`, `layouts/footprint.py` |
| 4 | runner sizing and symmetric routing, with the checks | `layouts/sizing.py`, `layouts/runners.py` |
| 5 | 3D build through the CAD module, STEP export | `layouts/assemble.py` |
| 6 | job server and input form | `layouts/server.py`, `layouts_ui/` |
| 7 | layout grid, detail and compare views | `layouts_ui/` |

## Requirements

- macOS or Linux
- [Miniforge](https://github.com/conda-forge/miniforge) (conda, for Python 3.12 with OCCT)
- Node.js 18 or newer (for the UI)

## Install (once)

From the repo root:

```bash
conda env create -f environment.yml
conda activate ayrton-gating
pip install -e ".[dev]"
(cd layouts_ui && npm ci)
```

Python must be 3.12: the OCCT wheels under build123d don't cover 3.14.

## Launch

Two terminals, both at the repo root.

**1. The server** (port 8010):

```bash
conda activate ayrton-gating
python -m uvicorn layouts.server:app --port 8010
```

**2. The UI** (port 5174):

```bash
cd layouts_ui && npx vite --port 5174
```

Open **http://localhost:5174**. The UI forwards `/api/layouts` to the server on 8010.

### One port instead of two

Build the UI once and the server serves it itself:

```bash
(cd layouts_ui && npx vite build)
python -m uvicorn layouts.server:app --port 8010
```

Then open **http://localhost:8010**. Rebuild after changing anything in `layouts_ui/src`.

### While changing the code

Add `--reload --reload-dir layouts` to the server command so it restarts when a file in `layouts/` changes.
Vite reloads the UI on its own.

## Using it

1. **New job.** Upload the part's STEP file and give:
   - the alloy, the process and the recipe set;
   - the flask's length, width, cope height and drag depth;
   - the sprue: free (flask centre) or fixed at a point you click;
   - the parting plane.

   Optionally limit the number of cavities, the layout families and the part rotations.
2. **Parting plane.** Keep the suggestion the form picks, or choose one that passes through the part.
   Ingates go in the drag, so a plane with no metal below it is refused.
3. **Recipe set.** The foundry presets are blank by design, so until they are filled in, use
   **Test recipe**. Its values are invented and labelled as such in the form. You can override
   single values for one job in "Recipe values for this job".
4. **Run.** The Run tab runs the phases and shows progress. Layouts appear in the grid as Phase 3 places them.
5. **Review.**
   - **Part analysis:** the modulus heatmap, hot spots and ingate candidates. Click a candidate to switch it off.
   - **Layouts:** filter, sort on any metric, shortlist with ★ and tick up to four to compare.
   - **Rejected:** the rejected layouts with their reasons.
6. **Build.** Open a layout to build it in 3D and download the STEP.

Jobs are saved under `jobs/` at the repo root, one folder per job. They are git-ignored. To keep them
somewhere else, set `LAYOUT_JOBS_DIR`.

Pages: `#/` jobs · `#/new` new job · `#/job/<id>` run, part analysis, layouts, rejected ·
`#/job/<id>/layout/<lid>` one layout · `#/job/<id>/compare/<a,b,...>`.

## Tests

```bash
python -m pytest -q tests/layouts                # the generator
python -m pytest -q                              # everything (about 13 minutes)
python -m pytest -q -m slow                      # slow tests, deselected by default
```

One slow test checks Phase 2 against a production part kept in `parts/`. That folder isn't in the repo
(customer CAD), so the test skips without it.

## Repo layout

| folder | what |
|---|---|
| `layouts/` | the layout generator: GatingJob spec, recipe sets, phases 1–5, the job server |
| `layouts_ui/` | the generator's React + three.js UI (Vite) |
| `core/` | CAD module: document and schema, frames, element contract and registry, flow graph, placement, path elements, joints, collisions, part import, orientation, draft check, flask, mirror and pattern ops, regeneration |
| `elements/` | element plugins (sprue, runner, ingate, cup, well, feeders, vents, ...) |
| `calculators/` | hydraulics, the formula evaluator, ratio propagation |
| `rules/` | system rule plugins |
| `presets/` | process × alloy presets; **all values blank until filled from trusted references** |
| `bridge/` | export to Ayrton (project folder, region STLs, manifest, named STEP) |
| `api/`, `ui/` | the CAD module's editor (separate app) |
| `docs/` | decisions (`DECISIONS.md`), element template, spec sheets |
| `tests/` | pytest; `reference_parts/` holds three synthetic reference parts with expected outputs |

## The CAD module's editor

The manual gating editor, separate from the generator:

```bash
(cd ui && npm install)
python -m uvicorn api.main:app --port 8000      # API
(cd ui && npx vite)                              # editor at http://localhost:5173, proxies /api
AYRTON_GATING_FIXTURES=1 python -m uvicorn api.main:app --port 8000   # with the framework's test elements
AYRTON_SOLID=~/Desktop/ayrton-solid python -m pytest -q tests/test_reference_parts.py   # + Ayrton case build
```
