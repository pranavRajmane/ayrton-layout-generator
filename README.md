# Ayrton gating

The gating-design CAD module for Ayrton: a design document of element instances and connections,
regenerated into tagged solids with OCCT (build123d), checked, and exported to Ayrton's solver.

Plan: *Ayrton Gating CAD Module — Phase-by-Phase Build Plan* (2026-11-04). Decisions: `docs/DECISIONS.md`.

## Setup

```bash
conda env create -f environment.yml
conda activate ayrton-gating
pip install -e ".[dev]"
(cd ui && npm install)
```

## Run

```bash
python -m uvicorn api.main:app --port 8000      # API (kernel stays on the server)
(cd ui && npx vite)                              # editor at http://localhost:5173, proxies /api
AYRTON_GATING_FIXTURES=1 python -m uvicorn api.main:app --port 8000   # with the framework's test elements
python -m pytest -q                              # tests
AYRTON_SOLID=~/Desktop/ayrton-solid python -m pytest -q tests/test_reference_parts.py   # + Ayrton case build
```

## Layout

| folder | what |
|---|---|
| `core/` | document + schema, frames, element contract + registry, flow graph, placement, path elements, joints, collisions, part import, orientation, picking, draft check, flask, mirror/pattern ops, regeneration |
| `elements/` | element plugins only (the library); `dummy_box` proves the pipeline |
| `calculators/` | pure hydraulics, the formula evaluator, the engine over a regenerated design, ratio propagation |
| `rules/` | system rule plugins (`system.*`) |
| `presets/` | process × alloy presets (Template 4); **all values blank until filled from trusted references** |
| `bridge/` | export to Ayrton (project folder + region STLs + manifest + named STEP) |
| `api/` | FastAPI for the editor |
| `ui/` | React + three.js editor |
| `docs/` | decisions, element template, spec sheets |
| `tests/` | pytest; `reference_parts/` holds the three reference parts with expected outputs |
