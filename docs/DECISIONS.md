# Decisions

Architectural choices and why, newest last.  Foundry numbers are never decided here: they live in
`presets/*.json` with their source, and are left as TODOs until the domain owner supplies them.

## Phase 0 (2026-11-04)

1. **Own repository** (`ayrton-gating`), next to `ayrton-solid`. The CAD module talks to the solver only
   through the bridge's files, so it carries no solver code.
2. **Python 3.12 in a conda env** (`environment.yml`). The machine's Python is 3.14 and OCCT's wheels
   (cadquery-ocp, under build123d 0.13) do not cover it.
3. **Ayrton's input is the occserver project folder, not a STEP.** `validation/stl_case.py` reads
   `geometry/cast.stl` (all the metal), `geometry/inlet.stl` (the pouring patch) and
   `materials/{materials,transform,pour}.json`. The bridge writes exactly that, and adds the region tags the
   solver does not read yet: one STL per named solid plus `regions/manifest.json`, and a STEP assembly of the
   same named solids (`design.step`). Teaching the voxeliser to label cells by region is a solver-side change,
   not done here.
4. **STEP solid names are read through OCCT's XCAF labels** (`bridge/step_names.py`). build123d's
   `import_step` rewrites `" .()"` to `_`, which would turn `gating.runner_1` into `gating_runner_1`.
5. **Project frame = mold frame moved so the inlet's centre is the origin**, as the pipeline does;
   `translation` in `transform.json` and the manifest records it. The inlet's normal in `transform.json` is
   the opening's outward normal (against the flow), which is what `stl_case` flips on.
6. **The open pouring inlet's embed is trimmed on export.** Nothing joins it, so the stub only pokes metal
   over the inlet plane (stl_case then adds a sand layer and cuts metal). Only the pouring element's stub is
   cut; a casting may rise above the inlet plane.
7. **Port frames: `normal` is the flow direction** (into the part for part ports). A frame's matrix has
   columns (normal, side, up), side = up × normal. Placement lays the element's port frame exactly on the
   target frame (same normal, same up).
8. **Placement runs in passes**: anchor, then part contact, then a placed upstream port, then a placed
   downstream port (back-chaining, so a chain hung only from an ingate's part contact is placed back up to
   the sprue). Every further connection is checked: its two port frames must coincide, otherwise
   `placement.joint_gap`. Gaps are reported, not closed, until runner path solving (Phase 1.2).
9. **The downstream element owns the overlap.** For the solver regions the upstream solid is cut by the
   downstream solid (by the part at a part joint), so the regions tile the metal. At an element-element
   joint both embeds overlap (2 × embed). The pattern is one fuse of the untrimmed solids.
   `overlap_mm` on a connection is stored but not used yet; `embed` (an element parameter the framework adds
   to every element) sets the overlap.
10. **A port is used once per role** (one connection leaves an outlet, one enters an inlet); a part port can
    take many ingates. Taps are distinct by (station, side).
11. **Measurement planes are named after the port the metal leaves by**: `<instance>.<port>`, with
    `@<station>` for taps; the pouring inlet is `<instance>.<port>` too.
12. **Every edit is a JSON Patch.** `DesignDocument.apply` validates the result (schema and references) and
    refuses an invalid edit, leaving the document unchanged; the inverse patch is kept for undo.
13. **`part` is a reserved instance id**: connection records end on `{"instance": "part", "port": <picked
    face id>}` with joint `part`.
14. **Placed solids are baked before glTF export.** OCCT's glTF writer composes a shape's location on the
    wrong side of its Z-up to Y-up conversion; baked shapes come out right. The viewer turns the glTF back to
    Z-up millimetres, so overlays use the API's mold coordinates directly.
15. **Viewer: React + three.js through @react-three/fiber and drei**, Vite + TypeScript; the camera is framed
    from the part's bounding box (drei's `<Bounds>` measured before the scaled group updated).
16. **Template differences.** In build123d 0.13 `Shape.is_valid` is a property, not a method; `PortType` is
    kept as an alias of `PortKind`; categories gain `test` for framework fixtures like `dummy_box`.

## Phase 1.1 to 1.3 (2026-11-04)

17. **The part chain**: source file → units → machining stock (each listed face thickened outward by its
    allowance and fused on) → as-cast part (its volume is the casting weight) → shrinkage scale about the
    part-frame origin (pattern size) → part transform → mold frame. Face indices (machining groups, picked
    gate faces) refer to the source shape's faces.
18. **Units**: STEP files declare theirs (OCCT converts to mm); `part.units` overrides a wrong file. STL has
    none: mm unless set, with a warning when the part is under 5 mm or over 20 m across.
19. **Orientation is a document edit.** Rotations turn about the part's bounding-box centre; changing the
    part transform carries the gate faces and element anchors along in the same patch (one undo).
20. **Draft and undercut** are measured on samples of each face (its own triangulation, normals from the
    surface): draft = asin(n · pull), pull +Z above the parting plane and −Z below; undercut = a ray from
    just off the face, along the pull, hits the part again. Samples inside a core are skipped. The minimum
    draft is the engineer's number (`mold.draft_min_deg`); without it only undercuts are reported.
21. **Cores**: from a file or a cylinder primitive (prints included), tagged `core.<id>`; gating that crosses
    one is a `collision.core` error.
22. **Flask**: the part's bounding box plus a margin, or an explicit box; gating outside it is a warning; it is
    exported as region `mold.flask` (its outer box; the sand is the box minus metal and cores).
23. **Path elements** (runners) live in the mold frame. Their polyline is solved between their constrained
    ends: [upstream port origin] + waypoints + [inlet of a downstream element fixed elsewhere]. Bends are
    rounded with `bend_radius` (horizontal paths only); the section is swept with a fixed binormal (+Z), so it
    never twists (OCCT pipe shell; build123d's `sweep` binormal takes an edge, not a direction).
24. **Taps** are created by connecting to `tap_<n>` on a path element. Without a station, the station is the
    point of the path nearest the branch's inlet; side `side` resolves to the side facing it. Explicit
    `left`/`right` are allowed too.
25. **Transitions** are framework solids (`gating.<connection id>`): a ruled loft through four sections, each
    end held straight in its neighbour's own section across that neighbour's embed. A plain two-section loft
    made OCCT miss the overlap entirely (ruled sides converging onto the neighbour's faces); with the
    straight ends every overlap is prism on prism and the regions tile the metal exactly.
26. **Contact checks** (part joint, branch) measure how much of a thin plate of the section, just beside the
    port plane, lies in the solid it joins; under 99% is an error.
27. **Fusing**: the gating first, then onto the part, with a one-at-a-time fallback; one boolean over the
    part and a loft and two other solids returned nothing.
28. **Multi-cavity**: `part.cavities` are rigid copies of the placed part, regions `part.<id>`. Mirror and
    pattern copy element groups as one patch; a mirror rebuilds each element from its parameters in the
    reflected frame (a handed element needs a mirrored variant), and copied taps get auto-solved stations.
29. **Calculators use physics only** (Bernoulli, continuity, Q = V/t, v = Q/A, Reynolds with the hydraulic
    diameter). Density, discharge coefficient, viscosity, pour-time formula, gating ratios and limits come from
    the preset; the effective-head form (top, bottom, parting: the textbook ones) is chosen per design. A
    missing value is listed (`preset.missing`, `calc.input_missing`), never guessed.
30. **Flow split** across ingates is in proportion to their outlet areas; every measurement plane carries the
    share of the flow that reaches the ingates downstream of it.
31. **Gating ratio** is read off each path as the smallest port area of its sprue, runner and ingate members
    (elements declare `gating_role`), relative to the sprue. Propagation (on request, one patch) gives each
    role its total from the preset ratio, split equally among that role's elements, through each element's
    `size_for_area`, and fills every parameter declared `driven_by` a calculator value.
32. **System rules** are plugins in `rules/` (ids `system.*`): open outlet (a leak; the downstream element of
    a blind joint is closed by design), every path ends at the part or a blind end, one pouring inlet, choke
    location by the chosen system (unpressurized: sprue; pressurized: ingates), choke area against the
    required one, ingate/runner velocity and joint angle against preset limits (silent while unset).
33. **Framework fixtures** (`tests/fixture_elements/`: runner, sprue, well, ingate, round bar, parting gate)
    exercise the framework; they are not the library. `AYRTON_GATING_FIXTURES=1` loads them into the API for
    demos.
34. **Guided set-up** (user's process, 2026-11-04): opening a part file asks for the molding box first,
    then the user fits the part in it, places the sprue, then draws and drags the runner (`core/workflow.py`,
    `ui/src/Setup.tsx`). Box = length × width × total height with the parting plane at a typed height over the
    box bottom (user's choice); the parting plane is z = 0 of the mold frame and the box is centred on the Z
    axis. The first box also centres the part in it (same undo step). The part sticking out of the box is a
    warning (`mold.outside_flask`, now also for the part); an element may poke out by its embed (the pouring
    stub, trimmed on export).
35. **Sprue and runner elements** (`elements/sprue.py`, `elements/runner.py`): geometry only. The user types
    the sprue's two diameters and the runner's widths and height (user's choice: no foundry defaults are used;
    the parameter defaults exist only for the element contract tests). The runner is drawn on the parting
    plane with its wide face on it, hanging into the drag or standing in the cope, chosen per runner (user's
    choice); `PathElement.rise()` offsets the section from the drawn line.
36. **Drop joint**: a sprue's outlet points straight down onto the start of a path element; the path starts
    under it and the outlet must end on the path's floor (`joint.drop_floor` otherwise). Ownership as for
    every joint (the runner cuts the sprue). The sprue's length and inlet height are never typed: every
    patch through the API is followed, in the same undo step, by `workflow.sync`, which sets the inlet on the
    cope top and the bottom on the runner floor (or the parting plane before there is a runner).
37. **Sharp runner bends** are mitred (`BRepBuilderAPI_RightCorner`); the default transition left an invalid
    solid at a 90° corner.
38. **Plan-view sketching** (user's choice, 2026-11-04): the sprue and runner steps switch to an orthographic
    view straight down on the parting plane (pan and zoom only; a Plan/3D toggle). The part's cut by the
    parting plane is drawn over it (`mold.section_outline`, taken 1 µm above and below the plane so faces
    lying on it give their outline, not their triangles). The sprue's ghost follows the cursor and drops on a
    click; it is dragged to move. The runner is drawn click by click from under the sprue with a live band
    of its width and segment lengths; double-click or Enter finishes, Backspace removes the last point.
    Placed points are dragged, a segment's middle dot is dragged to add a bend, Delete removes a selected
    point. Points snap to 5 mm and square up within 6° of an axis; Alt turns snapping off.
39. **Several parts in one box** (user's choice: copies and other files): `part.cavities` entries may name
    another file (`source`, `units`, `name`); such a cavity's transform is its own placement (file frame to
    mold frame), a copy's stays a move of the placed part. In the set-up each part is picked and moved or
    turned on its own; moving the main part holds the copies where they are. Parts that share metal are an
    error (`mold.parts_overlap`).
40. **One sprue, several runners** (to feed several parts): a sprue's outlet may drop into more than one
    runner (the graph allows a "from" port in several drop joints only). The runners all start under the
    sprue; there they share metal, which each later runner gives up to the earlier ones (so the regions
    still tile) and which is not a collision. The sprue goes down to the deepest runner floor; a sprue
    ending above a runner's floor is an error, below it is fine. Runner sweeps that fold over themselves (a
    segment at a bend shorter than the runner is wide) fail with that reason.
41. **Ingates** (user's choices, 2026-11-04): dragged in plan view from a point on a runner to the part's
    edge (the end snaps onto the parting-plane outline within 8 mm); a width × thickness rectangle, flat face
    on the parting plane, in the drag or the cope per ingate (`elements/ingate.py`). Two new joints:
    `tee` (runner → ingate: the ingate's first point must touch the runner; the runner keeps the overlap;
    a runner may tee into many ingates) and `gate` (ingate → part, anywhere on it, no picked face: the
    ingate must touch the part, across the parting plane or at a side wall; the part keeps the overlap).
    A runner whose outlet feeds ingates by tees is not an open outlet (its end is a dead end). Removing a
    runner removes its ingates. Gate area and velocity readouts wait for the presets.
42. **Suggested fit** (user's choices, 2026-11-09): when a part is opened, step 1 offers two ways to part it
    (`core/fit.py`) and the user picks: *widest* (of the part's three axes pointing up, the one with the
    least undercut, then the largest section; parted at its widest horizontal section, at the end of a
    widest band nearer the middle and snapped onto a flat face within one slice, so a flange stays whole)
    and *flat* (the largest outer flat face, coplanar faces together, on the plane facing up, the part in
    the drag). Sections come from the surface triangles (Green's theorem over the cut segments), the
    undercut is an estimate (a ray per face along its half's pull). The box: the smallest of the shop's
    standard boxes (`presets/boxes/matchboxes.json`, empty until the user saves one; either way round)
    that the part fits with the clearances, else the part plus clearances rounded up to a step. The
    clearances (sides, below, above, extra length for the sprue at the -x end) are typed per part and only
    remembered by the browser; no default is assumed. The user can edit the box before "Use this", which
    turns, places and boxes the part in one undo step.
43. **Runners branch from anywhere** (user, 2026-11-09): a new runner starts on the sprue (a drop) or at
    any point of another runner (a tee: its first point is that branch point, kept when it is redrawn); the
    first click of a new runner picks which. Deleting a runner deletes what branches off it.
44. **Automatic ingates** (user): with an ingate size set (width, thickness, length, cope/drag), a runner
    drawn into a part is cut back by the ingate's length from where it first touches the part and that
    ingate joins it to the part ("placed": "auto"; it follows the runner when the runner is redrawn, and
    goes when the runner no longer reaches the part). "Touches" is taken over the runner's (or ingate's,
    if wider) whole width and through its depth: the part's outline at ~13 heights through the runner and
    the ingate, since a part can reach further out below or above the parting plane than at it. The run
    into the part must be at least the ingate's length. The drawn-ingate tools (step 4) stay.
45. **Alignment** (user): in plan view a point being placed or dragged (sprue, runner points, ingate ends)
    snaps within 7 screen pixels onto the x or y of the sprue, every runner point, ingate ends, part
    centres and edges and the box's centre lines, with a guide line drawn; Alt/Option turns all snapping
    off. In step 1's plan view parts are dragged by their footprint and snap (centre or edge) to the other
    parts' centres and edges and the box's walls and centre lines; green lines show where parts already
    line up. Parts are best placed before gating: a gate does not follow its part.
46. **Autosave** (user, 2026-11-09): every change (and every undo/redo) saves the design to its file:
    the design file it was opened from, or `<part>.gating.json` beside a part file. Opening a part whose
    `.gating.json` exists opens that design again ("Start over" begins a new one and replaces it). The
    undo history lives in the session only.
47. **Ingates follow their part**: a runner keeps the points it was drawn with and its ingate size
    (`drawn`, `gate_*`); after a part is moved, turned, centred or fitted, every gated runner is cut and gated
    again from them against the part where it now is, in the same undo step as the move. Ingates drawn
    by hand that end on the moved part's footprint move their end with it. Part moves in the guided set-up
    no longer carry element anchors along: the sprue stands in the box.
48. **On the sprue's axis** (user, 2026-11-09: conical cup; filter under the sprue): a pouring cup is a cone
    cut into the top of the cope (top diameter and height typed, bottom = the sprue's top, butt-joined; its
    open top is now the pouring inlet). A sprue well (diameter, depth below the runners typed) hangs under
    the sprue, and the runners drop from it; a filter (square or round, size and thickness typed) lies on
    the parting plane over the well, the sprue coming down onto it. New joint `stack` (straight down the
    axis, sections may differ). The filter and well have no embed, so the filter's region is exactly the
    filter. All the heights are kept by `workflow.sync`.
49. **Feeders and vents** (user: top and side feeders; round vents from the part): closed round feeders in
    the cope, on the part's top where clicked (its height there found by a ray) or on the parting plane
    beside the part with a neck (width, thickness, cope/drag) running to where it first touches the part;
    vents from the part's top straight up to the box top. Each joins the part by a `gate` joint. Region
    kinds `feeder.<id>` and `vent.<id>` (metal) and `filter.<id>` (porous): the element contract now allows
    these besides `gating.<id>`.
50. **Filters in the simulation**: ayrton-solid's `validation/stl_case.py` reads `regions/manifest.json`
    and voxelizes every `filter` region into the case's filter cells (the solver's ceramic foam).

## Review fixes (2026-11-11)

51. **Saving never loses a design**: a save writes a temporary file beside the design and renames it over
    the old one (a crash mid-write leaves the last good save). The first save of a session keeps the file
    as it was opened as `<name>.gating.json.bak`. A saved design that cannot be read is renamed
    `<name>.gating.unreadable-<time>.json` and a new design started, with a note saying so; one that reads
    but cannot be rebuilt is refused and left alone. "Start over" keeps the old design as
    `<name>.gating.previous.json`. Save and export failures are 422 with the reason, not 500.
52. **What sits on a part goes with it**: top feeders and vents (their base), side feeders with their necks
    (the neck's end) and hand-drawn ingates (their end) belong to the part their contact point lies on,
    found from the geometry (within 2 mm plus the embed), not from bounding boxes. A part move, turn,
    centring or fit carries them; then, in the same undo step, top feeders and vents are seated again on
    the moved part, necks run again toward the part and every gated runner is gated again. Removing a part
    removes what sits on it. Moving the sprue, adding a part and removing one also re-gate. What could not
    follow (nothing under a feeder after a turn, a runner too short to gate) is told in the state's `notes`.
53. **Seated on curved tops**: vertical rays at the centre and on two rings inside the edge (radius d/2
    with 16 rays, d/4 with 8) find the part's top under a feeder or vent. Its base goes on the highest of
    those, so the typed height (vent: to the box top) stands clear of the part everywhere; its `embed`
    reaches from there to the lowest plus the usual 1 mm, and the gate joint cuts it back to the part.
    Feeders and vents declare their own `embed` (up to 2000 mm) for this. Rays that find no part (it
    hangs over the edge) give a note.
54. **One change at a time per design**: POST requests on one design session wait for each other (a
    thread lock per session, taken by an app-wide dependency); mesh downloads do not wait. Glb meshes are
    kept least-recently-used up to 512 MB, part files read up to 16.
55. **Ingate size for every runner** (`/workflow/gate`): every runner is cut and gated again with the new
    size in one undo step (null: no automatic ingates); a runner that cannot be gated is told and left.
56. **Exports into the same folder**: region files listed in the old manifest whose solid is gone are
    deleted, so the folder holds only the exported design's regions. An added part file is stored
    relative to the design, like the main part.
57. **The editor sends one change at a time** (a queue in `run`), covers the view while one runs, keeps a
    dragged item where it was dropped until the reply, and acts on Delete/Enter/Escape only on the set-up
    tab. Snapped and aligned positions are sent exactly; free ones in whole mm.
58. **Large parts stay fast** (2026-11-15, after a 4,313-face STEP took ~12 s per move): a part file is
    read, healed, given its machining stock, scaled, weighed and meshed once (`core.part._base`); the
    viewer gets that mesh once and places it by the part's matrix (`meshes[].matrix`), so a move sends
    no new mesh. Questions asked on every edit go through the triangles to the few exact faces that
    matter (`core.partmesh`): seating rays (16 s -> 40 ms), sections (kept in the part's own frame, so
    a sideways move cuts nothing again), the nearest part, and the bounding box (exact, from the faces
    at the mesh's sides, kept per turn; an exact box of the whole part took 15 s and was taken several
    times per edit). Overlap volumes are kept by what the two solids are and where. A part move, copy,
    file, removal or sprue change rebuilds once, after seating and re-gating, not twice. Results match
    the exact ones; the mesh is relative (0.1, 0.3 rad), as an absolute 0.1 mm does not finish on such
    a part.
59. **A runner moves up and down within its half** (2026-11-15, the engineer's choices): `level` is the
    height of its parting-plane face (0 on the plane; down into the drag, up into the cope, never across;
    not out of the box). Its points, its branches (same half only) and every ingate off them stay level
    with it; the automatic ingates are cut again against the part at that height (the outline is taken
    through the runner's band, not at the plane). The sprue reaches down to the deepest runner's floor
    (or ends on a raised cope runner's floor); the filter sits on the deepest drag runner's top face
    with the well under it. A redrawn runner keeps its level, a branch takes its parent's, a runner
    switched to the other half goes back to the plane. Set by a typed depth / height in the runner step
    or a z-only arrow on the selected runner in 3D (`/workflow/runner_level`, one undo step).
60. **Automated layout generator: the GatingJob** (2026-11-19, Phase 0 of the layout-generator task). A
    job (`layouts/job.py`, schema `layouts/schema/gating_job.v1.0.json`) holds the engineer's inputs, a
    snapshot of the recipe set it ran with, and one section per phase (modulus, ingate candidates,
    layouts, runners, metrics; `rejected` collects layouts rejected in phases 3 and 4; `assemblies` the
    3D models built on demand). Each phase records the hash of what it read; a changed input makes that
    phase and every later one stale, and re-running a phase clears everything after it. Ids are given
    in generation order and the result sections hold no times, so the same job gives the same JSON.
    Ingates are disabled through `inputs.limits.disabled_ingates` (candidate ids are stable), which
    leaves Phases 1-2 valid. The recipe set is still `presets/<process>_<alloy>.json`; the layout values
    are a new `layout` block there, **blank** like the rest. Which alloys and processes the generator
    offers is data (`layouts/settings.json`: grey and SG iron, green sand and no-bake); which recipe
    values each phase needs is data (`layouts/recipe_keys.json`). Tolerances given in the task are in
    `settings.json`; the module's own starting choices are listed there under `_proposed`. Tests use
    invented, clearly marked recipes in `tests/layouts/recipes/`, never the presets.
