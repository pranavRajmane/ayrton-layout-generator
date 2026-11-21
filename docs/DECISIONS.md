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
61. **Layout generator Phase 1: modulus, zones, hot spots** (2026-11-19, `layouts/modulus.py`). Zones are
    *members plus junction nodes* (the engineer's choice: L 3, T 4, X 5, a stepped plate 2), not one
    zone per distance maximum (a pure watershed makes an equal-wall L, T or X one zone). Halving the
    voxel moves every zone modulus by under 5 % (the engineer's limit; the solidification run is the
    final check), with two known exceptions: the turned T junction (6.7 %) and the X junction (5.8 %).
    A junction cools almost only through strips by its inside corners, so moving its cut one voxel
    along an arm changes its modulus about 8 % (plates of t at t/10 voxels: 1.1 t^2 / 1.2 t), while an
    arm, uniform along its length, does not change. The cut sits on a smooth thickness ramp read off
    voxels and moves with the staircase; about six cut rules each fixed one case and broke another.
    The likely fix, not done: cut across each arm through the points where the junction's largest
    sphere touches the surface (the inscribed-circle hand method), which does not depend on voxels.
    How:
    - **Voxels**: column rasterisation of the mesh with a winding count; a column whose crossings do not
      sum to zero went through a gap in the mesh and is cast again on the exact solid (21 columns on
      the 4,313-face part, which had made air into metal and three false hot spots of 45-98 mm).
    - **Inscribed spheres from the surface, not the voxels** (shrinking ball, Ma et al. 2012): from
      surface samples and from points along the part's edges (the sphere filling an X or T junction
      touches only the inside-corner edges). Normals are interpolated from the mesh vertices within
      each face (facet normals make a cylinder a prism with tiny spheres at each facet edge). Samples:
      strips from the corner opposite a triangle's shortest edge, so a long thin triangle gets points
      by its size (subdividing by the longest edge gave 666 k points and 330 s on a 90 k mm2 part).
    - Spheres are thinned before painting to one per cell of 0.15 x their radius (never under half a
      voxel): painting work falls with the square of the spacing, and the spacing no longer moves
      with the voxel size (at 0.25 the turned X moved 3.6 % on halving).
    - **Local thickness** = diameter of the largest sphere covering a voxel; among spheres within 2 %
      of it, the nearest centre owns the voxel (equal spheres along a member otherwise gave it to
      whichever was painted last, and an X junction's volume swung 10 %).
    - **Zones**: seeds are the cores of flat regions of the thickness (voxels at least half their
      sphere's radius, and 2 voxels, in from the region's edge; a thin layer of the same thickness
      cannot join two arms). Every other voxel takes the zone of its nearest core. Touching zones of
      the same thickness (within 5 %) merge only when the seam is that thick too and at least half a
      thickness squared in area (a member split by noise, not two walls meeting over a bridge, nor two
      arms of an X touching at its end faces). Flooding with moves along ramps chained round thick
      members (stepped plate 17.2 against 15.1 by hand) and was dropped.
    - **Every voxel and every bit of surface joins the zone of its largest covering / touching
      sphere's centre.** A centre goes to the zone whose core spheres (spheres centred in its core)
      cover it, the one nearest in thickness to the exact thickness at that point if several do;
      a centre no core sphere covers takes the zone of the voxel holding it. So the outer corner of an L goes to
      the corner block (M about 8 for t = 20, as the hand method has it).
    - **Zone modulus = V / A**: voxel volumes scaled to the exact solid volume; B-rep face areas (the
      mesh's where OCCT's is broken: the 4,313-face part gives a total of -7e20). r / 3 is only the
      heatmap field (exact in a sphere, t/6 in a plate).
    - **Hot spots**: maxima of the distance to the surface rising more than a voxel above every path
      to higher ground.
    - **Voxel size** = thinnest wall / 10 (at / 8 a turned T junction moved 3.1 % on halving), at least
      0.5 mm, at most 20 M voxels. The thinnest wall comes from inward rays; a ray starting off the
      metal (a bore's mesh chord) is measured from where it enters; hits within 0.5 mm (coincident
      faces) are left out and reported.
    - The zone loops work on flat indices of the undecided voxels only (identical output, 4-10x faster).
    Every numerical knob is in `layouts/settings.json` under `modulus`, listed as proposed.
    Convergence tests are marked `slow` (`pytest -m slow`).
62. **Layout generator Phase 2: ingate candidates** (2026-11-19, `layouts/ingates.py`, job schema 1.1).
    An ingate is the CAD module's flat bar on the parting plane, in the drag (z -t..0) or the cope, run
    straight out from the part. Candidates are stations on the part's *outer* outline at the ingate's
    mid height (an OCCT section, each segment knowing its B-rep face; right at the parting plane a part
    can carry a lip or land the ingate passes over, as 15145827 does by production ingate 2); holes
    in the cut are cores and give none. Section edges are joined end to end within 0.05 mm (that part
    file's edges miss each other by up to 0.01 mm; at 0.001 mm a tenth of its loops stayed open and
    were closed by a jump, which flipped them). Each loop is turned so the metal is on its left by
    probing just beside its longest segment with the even-odd rule (a probe on the loop itself is
    ambiguous where loops touch); counter-clockwise loops are outer, clockwise ones holes. A station
    whose normal does not run from metal into sand is discarded as **not_outward** (a guard: with the
    two fixes above, 21 of 15145827's stations). A stretch that only closes a gap the joining could
    not (no face) is discarded as **outline_gap**: a stub there missed the part by 0.9 mm. The outline is split into one patch per face; a tagged face or a patch
    shorter than the recipe's minimum contact is discarded whole. Stations sit along each patch with
    the whole ingate on it, `merge_distance` apart; then a station within `merge_distance` of one on a
    longer contact joins it (`merged_from`). A station is discarded, with its reason, when:
    - **blocked**: along any of five lines across the ingate, out to the recipe's runner clearance and
      at the top, middle and bottom of the band, metal comes back after sand (another part of the
      casting is in the way). Metal right at the contact is not a block: the ingate overlaps the part
      there, as the production ingates on 15145827 do (two of them end 10-12 mm inside its outline);
      the first rule, any metal in the stub's path, missed those two.
    - **ingate_not_thinner**: the bar modulus of the recipe's section (width x thickness /
      2 (width + thickness)) is not below the modulus of the zone behind the contact (the majority zone
      of the voxels just inside, across the width and the band).
    - **no_zone**: no zoned voxel behind it.
    Flags, by marching inward at mid band: **impinges_core** when any of the five lines leaves the metal
    into a hole of the cut within the recipe's impingement distance; **impinges_thin_wall** when the
    local thickness along the middle half of the stream drops below the recipe's thin wall (the edge
    lines run beside the part's own corners, where the thickness field is small and means nothing).
    Rule tags are evaluated from a fixed list (`feeds_heaviest_zone`, `feeds_hot_spot_zone`,
    `no_impingement`, in `settings.json` `ingates.known_rules`); the recipe picks which apply. New
    recipe key `layout.ingate.side` (blank in the presets); the phase also reads
    `layout.clearance.runner_cavity_mm`. Schema 1.1: the section is `{candidates, discarded}`, so the
    analysis panel can show why a stretch of parting line has no candidate; 1.0 jobs load as 1.1.
    Validation: synthetic parts (on the line, outward, merged, tagged, short, too heavy, core and thin
    flags, cope side, every stub fused into one valid solid, deterministic) and, marked slow, the six
    production drag ingates of 15145827: each has a candidate within 2.3-8.7 mm facing the same way
    (88 candidates on that part). That part file fails BRepCheck itself, so a stub's union is judged
    against it: one solid, and volumes adding up to 0.1 % of the stub or 1e-5 of the part.
63. **Layout generator Phase 4: sizing and symmetric runners** (2026-11-19, `layouts/sizing.py`,
    `layouts/runners.py`, `layouts/footprint.py`). Built before Phase 3 at the engineer's request, on
    hand-built layouts; Phase 3 will produce layouts that fit these templates.
    - **Sizing** keeps its whole trail (inputs, each step, the iterations) in the runner record for a
      hand check. Pour time from the recipe's formula in W (castings), G (poured), t; t = the
      diameter of the largest inscribed sphere (the engineer's choice). H = cope height + basin head -
      gate level (the ingate's mid height); H_eff = H - P^2 / 2C (parting-line gating); choke =
      G / (rho t C_d sqrt(2 g H_eff)); sprue : runner : ingate from the recipe's ratio, its smallest
      member the choke (`calculators.hydraulics`, unchanged). The engineer checked the numbers for a
      straight, an H and a radial layout by hand (2026-11-19) and they agree. G = castings (as cast, no allowance) +
      sprue + runners + ingates, iterated to a fixed point with the routing (cup and well come with
      the 3D build and are left out). The runner area is the total at every level (the engineer's
      choice): 2 arms A_r/2, 4 branches A_r/4; no step-down along an arm. One ingate per cavity,
      A_i/N, recipe thickness, width = area / thickness. Runner trapezoid: height = ratio x bottom,
      the face away from the parting plane (1 - taper) x the face on it (`core.workflow.runner_widths`).
      Sprue round: its narrow top = A_s, its bottom opened by the recipe's draft over its length (the
      engineer, 2026-11-19: the pattern draws out at the parting plane, so the sprue is widest at its
      bottom; it was sized by continuity, wider at the top, before, and the hand check above was on
      that version).
    - **Routing**: the runner meets each ingate at its foot, the layout's runner offset out from the
      contact along the ingate's normal, at least clearance + half the runner's width (straight:
      wherever the ingate reaches the runner line, at least that far). straight = one line through the sprue square to every ingate, run on
      by the extension; radial = an arm from the sprue to each foot, the ingate continuing it; h / tree
      = balanced binary splits, each into two halves that are each other's half-turn about the
      junction (the sprue first), arms to the halves' centres; a leaf arm's ingate continues it or
      leaves its side (then it runs on by the extension); any other angle is rejected. Routing only
      the representative and replicating by the half-turns gives equal paths by construction.
    - **Checks** (each a `check` record; any failure rejects the layout with its reasons): path
      lengths within 0.5 %; runner bands clear of every cavity's metal in the runner's half (drag
      or cope slab footprint) by the clearance; no runner crossing another except at its own
      junctions; ingates clear of other cavities; sprue (top diameter) clear of cavities by the sprue
      clearance; everything inside the flask less the wall clearance; Hausdorff of the system onto its
      own symmetry images within 0.01 mm; ingates clear of other runners and of the sprue (with the
      CAD module's 1 mm embed: a short leaf arm or a cavity on the sprue's line had put them there) (outlines, densified, after closing and opening the union by
      1e-4 mm: a union of abutting bands can keep a zero-width sliver on one side only, which a plain
      Hausdorff check read as 2 mm); areas on the ratio within 2 %; the resized ingate's bar
      modulus still below its zone's. Unroutable shapes (not square, not balanced, an H not of 4, a
      tree not of 2^k, a radial ingate not facing the sprue, a contact too close to the runner line)
      reject with a code before the checks.
    - **Footprints**: projected mesh triangles unioned with shapely (new dependency, conda-forge
      2.2), grown by the mesh deflection (0.05 mm) so a chord never undercuts the outline; a slab
      footprint is the part cut between two heights first.
64. **Layout generator Phase 3: cavity layouts** (2026-11-19, `layouts/arrange.py`). Built after
    Phase 4, so it places cavities the way the runner templates need them. The orbit representative is
    the part turned 0 / 90 / 180 / 270 degrees with one ingate candidate; the rest follow from turns:
    - **row / straight**: k cavities along one runner line through the sprue, ingates square to it;
      the other side is their half-turn about the sprue (c2) or their mirror image across the runner
      (d1, only for a mirror-symmetric part); N = 2k, and N = 1 beside the sprue (c1). Rows of three or
      more a side give unequal paths, so Phase 4 rejects them; they are kept to show why.
    - **grid / h, tree**: an H-tree of balanced half-turn splits, directions alternating, N = 2^m
      (m = 2 is the H); the ingate continues the leaf arm ("end") or leaves its side ("side"). The
      split directions go in `arrangement.split_axes_deg` so Phase 4 splits the same way.
    - **radial**: four cavities turned 90 degrees about the sprue, ingates facing it (c4).
    Mirror symmetry: voxel IoU of the part with its mirror in the upright planes through its centroid
    (Phase 1 grid) above `symmetry.mirror_iou_min`. The sprue is at the flask centre (free mode) or
    where the engineer put it (fixed): every layout is symmetric about its sprue, so sliding it along
    a centre line only moves the layout toward a wall.
    **Tightest placement**: the free lengths (pitch, runner offset, arm lengths, radius) start large
    and are shrunk one at a time by bisection (0.05 mm) while cavities stay apart and the runners,
    ingates and sprue stay clear (the Phase 4 checks with the sizes held), on outlines simplified and
    grown by 0.2 mm so they contain the exact ones; the runner is re-sized for the placed layout and the
    lengths re-shrunk until the runner width holds (a few rounds). Then the hard checks on the exact
    outlines: flask wall, cope and drag room (checked first: when it fails no layout can fit),
    cavity, sprue and runner clearances, the cavities mapping onto themselves under the layout's
    symmetry, and riser keep-outs (off by default: circles of 2 x the hot-spot radius, a placeholder).
    A series stops growing at its first flask-wall rejection.
    **Count first**: the combinations are counted before any is placed (N bounded by flask area /
    footprint area); above the threshold the phase fails with the count by family, for the engineer to
    narrow. **Duplicates**: a layout's key is its template, sprue and each cavity's placed centroid and
    contact, minimised over the flask's own symmetries (half-turn; quarter-turns for a square flask;
    its mirror lines for a mirror-symmetric part); a repeat is dropped and counted.
    Hand check (the test): the block 100 x 60 x 40 in a 1000 x 800 flask gives rows up to N = 14 along
    x and 12 along y (pitch 101 + 25 mm), exactly as counted by hand.
65. **Layout generator Phase 5: assembly in the CAD module** (2026-11-19, `layouts/assemble.py`).
    A routed layout becomes an ordinary design document built with the editor's own workflow patches,
    so it opens in the editor: box (parting plane at the drag height = the job's z = 0), cavity 0 as the
    part at pattern size (the module scales the file then places it, so the parting transform's
    translation is scaled too) and the rest as rigid copies, sprue, one runner element per routed arm
    (the first dropping from the sprue, the others branching off their parent's end), ingates from each
    foot to its contact, then the cup and the well (added last so the runners' drops move onto the
    well). Built on demand only: `assemble(job, layout)` writes `<job>.<layout>.gating.json` and
    `.step` and records the checks in the job's `assemblies`: regeneration without errors; the fused
    metal one solid, as valid as the part file; its volume = parts + gating less where they meet,
    within 0.1 %; a STEP round trip within 0.1 %; every element at least the recipe's draft. On the
    8-cube test all 15 routed layouts pass every check (about a second each). Mirrored cavities are
    refused for now (the module copies a part by a rigid move only).
    **Draft, the engineer's rule** (2026-11-19): patterns draw out at the parting plane, so every gating
    face is widest there. The sprue is narrow at its top (which carries the ratio's area) and opens by
    the draft to its bottom; drag runners are now wide on the plane (`core.workflow.runner_widths`
    changed for the editor too; the old rule, narrow on the plane in both halves, had a test that is
    rewritten); `draft_deg` params (default 0) added to the ingate (a trapezoid of the same area) and
    the sprue well (narrowing downward); the cup stands on the cope and opens upward.
    **CAD-module collision rule** (`core/collide.py`): path elements branching off one element at the
    same point may overlap each other there (a T or H junction: no joint joins siblings), and the
    sprue (and what stacks on it) may run down through the runners that drop from the well under it,
    as the module's own sync builds it. The production design of 15145827 had shown that second
    overlap as an error before.
67. **Layout generator Phases 6 and 7: the app** (2026-11-21, `layouts/server.py`, `layouts/metrics.py`,
    `layouts/thumbs.py`, `layouts_ui/`). Its own server (port 8010) and its own React app (Vite, port
    5174), with the editor's stack and versions, at the engineer's request: the editor's `api/` and
    `ui/` are untouched. A job is a folder under `jobs/`. **Inputs** (Phase 6): a STEP upload (closed
    solid checked, volume, weight when the recipe has a density), the parting plane from the editor's
    own `core.fit.suggest` (widest section, largest flat face) or as in the file, alloy and process
    (offered from `settings.json`), a recipe set (the foundry presets, or the test recipe marked as
    invented), the flask, the sprue (free, or fixed by a click on the flask plan), optional N range,
    families, part turns and riser keep-outs, and the recipe's layout values pre-filled; what the
    engineer changes becomes a per-job override. Refused before running, with the reasons: not a closed
    solid, a flask smaller than the part and its clearances, any recipe value a phase needs left blank.
    **Runs** go through the phases not done or out of date in a background thread; the UI polls the
    progress; Phase 3 reports its count and then each layout as it is placed (`arrange.run(progress=)`),
    so the grid fills while it runs; over the count threshold the run stops and the UI offers to narrow
    N or raise the threshold. **Part analysis**: the part coloured by local modulus (the thickness field
    / 6 just inside each point; the display mesh is split to two voxels so a flat face is coloured
    across), hot spots, the ingate candidates as cones (click to switch one off, then re-run from Phase
    3), zones and candidates as tables, and face tagging by clicking the part. **Metrics** (new phase,
    information only): poured weight, yield, runner length, the smallest gap in plan between a cavity
    and anything not its own, path-length error. **Grid** (Phase 7): server-drawn SVG top views (one
    colour per kind of element; each cavity and the sprue carry the numbers they were drawn from, so a
    test reads them back against the layout), filters on N, family, runner, ingate and part turn, sort on
    any metric, a shortlist kept per job, compare 2-4 side by side, only the rows in view drawn. The
    rejected tab groups by reason and shows each rejected layout. The detail view builds the 3D model on
    demand (Phase 5), shows it, lists every Phase 4 and Phase 5 check, downloads the STEP, and has a
    disabled "Send to simulation". Tests: `tests/layouts/test_server.py` (upload, refusals, a full run,
    every accepted layout once and the counts adding up, thumbnails against the layouts, the rejected
    groups, shortlist, build and download, an ingate switched off making layouts stale).
