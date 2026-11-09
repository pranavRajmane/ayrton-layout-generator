"""The guided set-up: box, part fitted in it, sprue, runner dropped into from the sprue."""
import math

import numpy as np
import pytest
from build123d import Box, export_step

from core import workflow as wf
from core.document import DesignDocument, empty
from core.occ import fuse
from core.regen import regenerate


@pytest.fixture()
def doc(tmp_path):
    export_step(Box(200, 150, 20), tmp_path / "plate.step")
    d = empty("workflow")
    d["part"] = {"source": "plate.step"}
    return DesignDocument(d, base_dir=tmp_path)


def apply(doc, ops):
    doc.apply(wf.with_sync(doc, ops))
    return regenerate(doc)


def errors(r):
    return [(i.rule, i.instances) for i in r.issues if i.severity.value == "error"]


def built_up(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    apply(doc, wf.move_part_patch(doc, (0, 0, -20)))                # the plate fully in the drag
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    return apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-150, 0], [-150, 100]]))


def test_box_geometry(doc):
    r = apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    b = wf.box(doc.data)
    assert b["bottom"] == pytest.approx(-120) and b["top"] == pytest.approx(180)
    assert tuple(r.flask[0]) == pytest.approx((-300, -200, -120))


def test_box_rejects_parting_outside(doc):
    with pytest.raises(wf.WorkflowError):
        wf.box_patch(doc, 600, 400, 300, parting=300)


def test_part_centred_then_moved(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300))
    r = regenerate(doc)
    r = apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    assert r.part.bbox[0] == pytest.approx((-100, -75, -10))
    r = apply(doc, wf.move_part_patch(doc, (10, 0, -10)))
    assert r.part.bbox[1] == pytest.approx((110, 75, 0))


def test_part_outside_the_box_is_flagged(doc):
    apply(doc, wf.box_patch(doc, 150, 400, 300))
    r = regenerate(doc)
    assert any(i.rule == "mold.outside_flask" and i.instances == ("part",) for i in r.issues)


def test_sprue_runs_from_cope_top_to_parting_plane(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    s = r.built["sprue"]
    assert s.params.length == pytest.approx(180)
    assert s.port("inlet").frame.origin == pytest.approx((-200, 0, 180))
    assert s.port("outlet").frame.origin == pytest.approx((-200, 0, 0))
    # a frustum, plus the embed carried on at both ends
    h, r1, r2 = 180, 15, 12
    assert s.derived["volume_mm3"] == pytest.approx(math.pi * h * (r1 * r1 + r1 * r2 + r2 * r2) / 3)


def test_sprue_must_be_in_the_box(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300))
    with pytest.raises(wf.WorkflowError):
        wf.sprue_patch(doc, 400, 0, 30, 24)


def test_runner_in_the_drag_and_the_sprue_reaches_its_floor(doc):
    r = built_up(doc)
    assert errors(r) == [] or {e[0] for e in errors(r)} <= {"system.dangling_outlet", "system.path_end"}
    run = r.built["runner"]
    assert run.params.path[0] == pytest.approx((-200, 0, 0))            # starts under the sprue
    assert run.element.floor_z(run.params) == pytest.approx(-25)
    assert r.built["sprue"].params.length == pytest.approx(180 + 25)
    bb = run.solid.bounding_box()
    assert bb.max.Z == pytest.approx(0, abs=1e-6) and bb.min.Z == pytest.approx(-25, abs=1e-6)


def test_runner_switched_to_the_cope(doc):
    built_up(doc)
    r = apply(doc, wf.runner_patch(doc, "cope", 30, 20, 25, [[-150, 0], [-150, 100]], "runner"))
    run = r.built["runner"]
    bb = run.solid.bounding_box()
    assert bb.min.Z == pytest.approx(0, abs=1e-6) and bb.max.Z == pytest.approx(25, abs=1e-6)
    assert r.built["sprue"].params.length == pytest.approx(180)
    assert not [i for i in r.issues if i.rule.startswith("joint.")]


def test_drop_floor_mismatch_is_an_error(doc):
    built_up(doc)
    i = next(k for k, e in enumerate(doc.data["elements"]) if e["id"] == "sprue")
    doc.apply([{"op": "replace", "path": f"/elements/{i}/params/length", "value": 150.0}])   # no sync
    r = regenerate(doc)
    assert ("joint.drop_floor", ("sprue", "runner")) in errors(r)


def test_redrawing_keeps_one_runner(doc):
    built_up(doc)
    r = apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[0, 0]], "runner"))
    assert [e["id"] for e in doc.data["elements"]] == ["sprue", "runner"]
    assert len(doc.data["connections"]) == 1
    assert r.built["runner"].params.path[-1] == pytest.approx((0, 0, 0))


def test_one_undo_step_per_action(doc):
    built_up(doc)
    doc.undo()                                           # the runner and the sprue's new length go together
    r = regenerate(doc)
    assert "runner" not in r.built and r.built["sprue"].params.length == pytest.approx(180)


def test_sprue_and_runner_are_one_piece(doc):
    r = built_up(doc)
    assert len(fuse([b.solid for b in r.built.values()]).solids()) == 1


# ---- several parts in the box --------------------------------------------------------

def test_copy_stays_put_when_the_part_moves(doc):
    apply(doc, wf.box_patch(doc, 800, 400, 300))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    r = apply(doc, wf.add_copy_patch(doc, None, (250, 0, 0)))
    before = r.part.cavities["part_1"].bounding_box()
    r = apply(doc, wf.move_part_patch(doc, (-30, 0, 0)))
    after = r.part.cavities["part_1"].bounding_box()
    assert tuple(after.min) == pytest.approx(tuple(before.min))
    assert r.part.shape.bounding_box().min.X == pytest.approx(-130)


def test_each_part_moves_and_turns_on_its_own(doc):
    apply(doc, wf.box_patch(doc, 800, 400, 300))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    apply(doc, wf.add_copy_patch(doc, None, (250, 0, 0)))
    r = apply(doc, wf.move_part_patch(doc, (0, 50, 0), "part_1"))
    bb = r.part.cavities["part_1"].bounding_box()
    assert (bb.min.X, bb.min.Y) == pytest.approx((150, -25))
    assert r.part.shape.bounding_box().min.Y == pytest.approx(-75)          # the main part did not move
    r = apply(doc, wf.turn_part_patch(doc, "z", 90, (250, 50, 0), "part_1"))
    bb = r.part.cavities["part_1"].bounding_box()
    assert (bb.max.X - bb.min.X, bb.max.Y - bb.min.Y) == pytest.approx((150, 200))


def test_another_part_file(doc, tmp_path):
    export_step(Box(60, 40, 30), tmp_path / "small.step")
    apply(doc, wf.box_patch(doc, 800, 400, 300))
    r = apply(doc, wf.add_file_patch(doc, "small.step", (200, 0, 0), name="small.step"))
    bb = r.part.cavities["part_1"].bounding_box()
    assert (bb.min.X, bb.max.X) == pytest.approx((170, 230))
    assert r.part.cavity_volumes["part_1"] == pytest.approx(60 * 40 * 30)
    r = apply(doc, wf.remove_part_patch(doc, "part_1"))
    assert r.part.cavities == {}


def test_overlapping_parts_are_an_error(doc):
    apply(doc, wf.box_patch(doc, 800, 400, 300))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    r = apply(doc, wf.add_copy_patch(doc, None, (150, 0, 0)))             # 50 mm of the 200 mm plate shared
    o = [i for i in r.issues if i.rule == "mold.parts_overlap"]
    assert len(o) == 1 and o[0].value == pytest.approx(50 * 150 * 20, rel=1e-3)
    r = apply(doc, wf.move_part_patch(doc, (60, 0, 0), "part_1"))
    assert not [i for i in r.issues if i.rule == "mold.parts_overlap"]


# ---- one sprue, several runners ---------------------------------------------------------

def test_two_runners_from_one_sprue(doc):
    built_up(doc)                                                         # runner 1: drag, 25 high
    r = apply(doc, wf.runner_patch(doc, "drag", 30, 20, 40, [[-200, -100]]))
    assert wf.runners(doc.data) == ["runner", "runner_1"]
    assert r.built["runner_1"].params.path[0] == pytest.approx((-200, 0, 0))   # starts under the sprue too
    assert r.built["sprue"].params.length == pytest.approx(180 + 40)            # down to the deeper floor
    bad = {i.rule for i in r.issues if i.severity.value == "error"} - {"system.dangling_outlet", "system.path_end"}
    assert bad == set()
    # the metal under the sprue is counted once: tagged solids tile the fused gating
    tagged = sum(b.tagged.volume for b in r.built.values())
    assert tagged == pytest.approx(fuse([b.solid for b in r.built.values()]).volume, rel=1e-6)


def test_removing_a_runner(doc):
    built_up(doc)
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 40, [[-200, -100]]))
    r = apply(doc, wf.remove_runner_patch(doc, "runner_1"))
    assert wf.runners(doc.data) == ["runner"] and "runner_1" not in r.built
    assert r.built["sprue"].params.length == pytest.approx(180 + 25)


# ---- ingates ------------------------------------------------------------------------------

def gated(doc):
    """Plate centred on the parting plane, sprue, a drag runner along its +y side, an ingate into it."""
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))                     # plate z -10..10, y -75..75
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-200, 110], [50, 110]]))
    return apply(doc, wf.ingate_patch(doc, "runner", (0, 105), (0, 75), "drag", 40, 8))


def test_an_ingate_closes_the_gating(doc):
    r = gated(doc)
    assert [i.rule for i in r.issues if i.severity.value in ("error", "warning")] == []
    g = r.built["ingate_1"]
    bb = g.solid.bounding_box()
    assert (bb.min.Z, bb.max.Z) == pytest.approx((-8, 0), abs=1e-6)
    # regions tile the metal: the runner keeps the start, the part keeps the end
    fused = r.fused()
    assert len(fused.solids()) == 1
    tagged = sum(b.tagged.volume for b in r.built.values()) + r.part.shape.volume
    assert tagged == pytest.approx(fused.volume, rel=1e-6)


def test_ingate_short_of_the_part(doc):
    gated(doc)
    r = apply(doc, wf.ingate_patch(doc, "runner", (0, 105), (0, 85), "drag", 40, 8, "ingate_1"))
    assert ("joint.gate_short", ("ingate_1",)) in errors(r)


def test_ingate_off_the_runner(doc):
    gated(doc)
    r = apply(doc, wf.ingate_patch(doc, "runner", (0, 150), (0, 75), "drag", 40, 8, "ingate_1"))
    assert ("joint.tee_off", ("runner", "ingate_1")) in errors(r)


def test_removing_a_runner_takes_its_ingates(doc):
    gated(doc)
    r = apply(doc, wf.remove_runner_patch(doc, "runner"))
    assert wf.ingates(doc.data) == [] and doc.data["connections"] == []


def test_gated_design_exports(doc, tmp_path):
    from bridge.ayrton import export
    r = gated(doc)
    out = export(r, tmp_path / "ayrton")
    assert (tmp_path / "ayrton" / "project" / "geometry" / "cast.stl").exists()
    # less the 1 mm embed stub over the cope top (the sprue's top radius is 15 mm there), trimmed on export
    assert out["metal"]["volume_mm3"] == pytest.approx(r.fused().volume - math.pi * 15.02 ** 2 * 1.0, rel=1e-4)


# ---- branches and automatic ingates ---------------------------------------------------------

OUTLINE = [[[-100, -75], [100, -75], [100, 75], [-100, 75], [-100, -75]]]       # the plate at the parting plane
GATE = {"width": 40, "thickness": 8, "length": 20, "side": "drag"}


def test_runner_into_the_part_gets_an_ingate(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    # drawn from the sprue across to x = 0 and up into the plate (its edge at y = -75... the runner comes from below)
    r = apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-200, -120], [0, -120], [0, -40]],
                                   gate=GATE, outline=OUTLINE))
    run = doc.data["elements"][[e["id"] for e in doc.data["elements"]].index("runner")]["params"]["waypoints"]
    assert run[-1] == pytest.approx([0, -95, 0])                       # cut back 20 mm before the edge
    g = next(e for e in doc.data["elements"] if e["type"] == "ingate")
    assert np.allclose(g["params"]["waypoints"], [[0, -95, 0], [0, -75, 0]]) and g["params"]["placed"] == "auto"
    assert [i.rule for i in r.issues if i.severity.value in ("error", "warning")] == []


def test_moving_the_runner_moves_its_ingate(doc):
    test_runner_into_the_part_gets_an_ingate(doc)
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-200, -120], [50, -120], [50, -40]], "runner",
                               gate=GATE, outline=OUTLINE))
    gs = [e for e in doc.data["elements"] if e["type"] == "ingate"]
    assert len(gs) == 1 and gs[0]["params"]["waypoints"][0] == pytest.approx([50, -95, 0])
    # drawn short of the part: no ingate any more
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-200, -120], [50, -120]], "runner",
                               gate=GATE, outline=OUTLINE))
    assert not [e for e in doc.data["elements"] if e["type"] == "ingate"]


def test_too_short_a_run_for_the_ingate(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    with pytest.raises(wf.WorkflowError, match="last straight run"):
        wf.runner_patch(doc, "drag", 30, 20, 25, [[-200, -120], [0, -120], [0, -60]],
                        gate={**GATE, "length": 60}, outline=OUTLINE)


def test_branch_off_the_middle_of_a_runner(doc):
    """A Z runner to the plate's bottom edge, and a branch off its middle up to the top edge."""
    test_runner_into_the_part_gets_an_ingate(doc)
    r = apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-150, 120], [0, 120], [0, 40]],
                                   branch={"from": "runner", "at": [-150, -120]}, gate=GATE, outline=OUTLINE))
    assert wf.runners(doc.data) == ["runner", "runner_1"]
    assert wf.feeder(doc.data, "runner_1") == ("runner", "tee")
    assert len([e for e in doc.data["elements"] if e["type"] == "ingate"]) == 2
    assert {i.rule for i in r.issues if i.severity.value in ("error", "warning")} == set()
    # the branch is redrawn with its start kept
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-150, -120], [-150, 120], [20, 120], [20, 40]], "runner_1",
                               gate=GATE, outline=OUTLINE))
    b = next(e for e in doc.data["elements"] if e["id"] == "runner_1")["params"]["waypoints"]
    assert b[0] == pytest.approx([-150, -120, 0]) and b[-1] == pytest.approx([20, 95, 0])
    # removing the trunk takes the branch and both ingates
    apply(doc, wf.remove_runner_patch(doc, "runner"))
    assert [e["id"] for e in doc.data["elements"]] == ["sprue"]


def test_a_runner_grazing_the_part_with_its_side_is_gated_there(doc):
    """The centre line passes beside the plate's corner, but the 30 mm wide runner overlaps it:
    the ingate goes where the band first touches."""
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    apply(doc, wf.sprue_patch(doc, -200, -150, 30, 24))
    # along x = -110 (the plate's left edge is x = -100): the band reaches x = -90 and meets the plate at y = -75
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-110, -150], [-110, 0]], gate=GATE, outline=OUTLINE))
    g = next(e for e in doc.data["elements"] if e["type"] == "ingate")
    assert np.allclose(g["params"]["waypoints"], [[-110, -95, 0], [-110, -75, 0]])
