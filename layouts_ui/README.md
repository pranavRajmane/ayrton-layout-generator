# Ayrton layout generator: the app (Phases 6 and 7)

A separate app from the editor (`ui/` and `api/` are not used or changed).

    # the server, from the repo root, in the ayrton-gating environment
    uvicorn layouts.server:app --port 8010

    # the UI (dev), in layouts_ui/
    npm ci && npx vite --port 5174            # then open http://localhost:5174

    # or build it once and let the server serve it on http://localhost:8010
    npx vite build

Jobs are kept in `jobs/` at the repo root (or `$LAYOUT_JOBS_DIR`), one folder per job: the job file,
the part, Phase 1's field, built models and the shortlist.

Pages: `#/` jobs · `#/new` a new job · `#/job/<id>` run, part analysis, layouts, rejected ·
`#/job/<id>/layout/<lid>` one layout (3D build, STEP) · `#/job/<id>/compare/<a,b,...>`.

The foundry presets are blank by design; until they are filled, a job runs only with the test recipe
(invented values, clearly labelled as such in the form).
