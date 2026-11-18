"""A runner moved up or down within its half: its points, branches and ingates go with it, the sprue
(and its well and filter) follow it down."""
import pytest
from build123d import Box, export_step

from core import workflow as wf
from core.document import DesignDocument, empty
from core.regen import regenerate


@pytest.fixture()
def doc(tmp_path):
    export_step(Box(200, 150, 20), tmp_path / "plate.step")
    d = empty("level")
    d["part"] = {"source": "plate.step"}
    return DesignDocument(d, base_dir=tmp_path)


def apply(doc, ops):
    doc.apply(wf.with_sync(doc, ops))
    return regenerate(doc)


def errors(r):
    return {i.rule for i in r.issues if i.severity.value == "error"}


def el(doc, inst):
    return next(e for e in doc.data["elements"] if e["id"] == inst)


OUTLINE = [[[-100, -75], [100, -75], [100, 75], [-100, 75], [-100, -75]]]
GATE = {"width": 40, "thickness": 8, "length": 20, "side": "drag"}


def gated(doc):
    """Box 600 x 400 x 300 parted 120 up, the plate centred, sprue at x -200, a drag runner run into the
    plate's bottom edge with its automatic ingate."""
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    return apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-200, -120], [0, -120], [0, -40]],
                                      gate=GATE, outline=OUTLINE))


def test_lowered_runner_takes_its_ingate_and_the_sprue_down(doc):
    gated(doc)                                                    # the plate: z -10..10
    r = apply(doc, wf.runner_level_patch(doc, "runner", -5))      # its ingate (-13..-5) still meets it
    p = el(doc, "runner")["params"]
    assert p["level"] == -5
    assert all(q[2] == pytest.approx(-5) for q in p["waypoints"] + p["drawn"])
    g = next(e for e in doc.data["elements"] if e["type"] == "ingate")
    assert all(q[2] == pytest.approx(-5) for q in g["params"]["waypoints"])
    lo, hi = r.built["runner"].solid.bounding_box().min, r.built["runner"].solid.bounding_box().max
    assert hi.Z == pytest.approx(-5, abs=0.6) and lo.Z == pytest.approx(-30, abs=0.6)      # 0.5 embed past the ends
    s = r.built["sprue"]
    assert s.params.length == pytest.approx(180 + 30)                       # the cope top down to the new floor
    assert r.built["runner"].params.path[0] == pytest.approx((-200, 0, -5))    # starts straight under it
    assert errors(r) == set()


def test_each_runner_stays_in_its_half(doc):
    gated(doc)
    with pytest.raises(wf.WorkflowError, match="down from the parting plane"):
        wf.runner_level_patch(doc, "runner", 5)
    with pytest.raises(wf.WorkflowError, match="bottom of the box"):
        wf.runner_level_patch(doc, "runner", -100)                         # 100 + 25 below, the box is 120
    apply(doc, wf.runner_patch(doc, "cope", 20, 30, 25, [[-200, 120]]))
    with pytest.raises(wf.WorkflowError, match="up from the parting plane"):
        wf.runner_level_patch(doc, "runner_1", -5)
    r = apply(doc, wf.runner_level_patch(doc, "runner_1", 40))
    lo, hi = r.built["runner_1"].solid.bounding_box().min, r.built["runner_1"].solid.bounding_box().max
    assert lo.Z == pytest.approx(40, abs=0.6) and hi.Z == pytest.approx(65, abs=0.6)
    assert r.built["sprue"].params.length == pytest.approx(180 + 25)      # still down to the drag runner's floor


def test_a_raised_cope_runner_ends_the_sprue_on_its_floor(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    apply(doc, wf.runner_patch(doc, "cope", 20, 30, 25, [[-200, 120]]))
    r = apply(doc, wf.runner_level_patch(doc, "runner", 10))
    assert r.built["sprue"].params.length == pytest.approx(180 - 10)


def test_redrawn_runner_keeps_its_level_until_it_changes_half(doc):
    gated(doc)
    apply(doc, wf.runner_level_patch(doc, "runner", -20))
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 30, [[-200, -120], [20, -120], [20, -40]], "runner",
                               gate=GATE, outline=OUTLINE))
    p = el(doc, "runner")["params"]
    assert p["level"] == -20 and p["waypoints"][-1] == pytest.approx([20, -95, -20])
    apply(doc, wf.runner_patch(doc, "cope", 20, 30, 30, [[-200, -120], [20, -120]], "runner"))
    assert el(doc, "runner")["params"]["level"] == 0


def test_branches_and_drawn_ingates_move_with_their_runner(doc):
    gated(doc)
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-150, 120], [0, 120]],
                               branch={"from": "runner", "at": [-150, -120]}))
    apply(doc, wf.ingate_patch(doc, "runner_1", [0, 120], [0, 75], "drag", 30, 8))
    apply(doc, wf.runner_level_patch(doc, "runner", -15))
    assert el(doc, "runner_1")["params"]["level"] == -15
    assert all(q[2] == pytest.approx(-15) for q in el(doc, "runner_1")["params"]["waypoints"])
    drawn = next(e for e in doc.data["elements"] if e["type"] == "ingate" and e["params"].get("placed") != "auto")
    assert all(q[2] == pytest.approx(-15) for q in drawn["params"]["waypoints"])
    # a new branch off a lowered runner starts at its level; a new ingate off it too
    apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[-100, 150]], branch={"from": "runner_1", "at": [-100, 120]}))
    assert el(doc, "runner_2")["params"]["waypoints"][0] == pytest.approx([-100, 120, -15])


def test_a_branch_in_the_other_half_keeps_the_runner_on_the_plane(doc):
    gated(doc)
    apply(doc, wf.runner_patch(doc, "cope", 20, 30, 25, [[-150, 120]], branch={"from": "runner", "at": [-150, -120]}))
    with pytest.raises(wf.WorkflowError, match="only in one half"):
        wf.runner_level_patch(doc, "runner", -10)


def test_the_well_and_filter_follow_the_runner_down(doc):
    gated(doc)
    apply(doc, wf.well_patch(doc, 50, 20, {"shape": "square", "size": 50, "thickness": 20}))
    r = apply(doc, wf.runner_level_patch(doc, "runner", -30))
    assert el(doc, "filter")["anchor"]["frame"]["origin"][2] == pytest.approx(-30 + 20)   # on the runner's top face
    well = r.built["sprue_well"]
    assert el(doc, "sprue_well")["anchor"]["frame"]["origin"][2] == pytest.approx(-30)
    assert well.params.length == pytest.approx(25 + 20)                    # to 20 below the runner floor at -55
    assert r.built["sprue"].params.length == pytest.approx(180 + 30 - 20)  # down to the filter's top
