"""Runner finishing touches: the top face 10-20 % narrower (typed as a taper %), and an extension past
the last ingate drawn off the runner's side."""
import numpy as np
import pytest

from core import workflow
from core.document import DesignDocument, empty
from core.regen import regenerate
from tests.seeds.synthetic import write_step


def _apply(doc, ops):
    doc.apply(workflow.with_sync(doc, ops))


@pytest.fixture
def doc(tmp_path):
    step = write_step("slab", tmp_path)                 # 60 x 60 x 30, its bottom on the parting plane
    data = empty("t")
    data["part"] = {"source": step.name, "shrinkage_pct": 0.0}
    d = DesignDocument(data, base_dir=tmp_path)
    _apply(d, workflow.box_patch(d, 500, 400, 300, 150))
    _apply(d, workflow.sprue_patch(d, -150, 80, 30, 24))
    return d


def runner(doc, rid="runner"):
    return next(e for e in doc.data["elements"] if e["id"] == rid)


def length(pts):
    return sum(float(np.hypot(b[0] - a[0], b[1] - a[1])) for a, b in zip(pts, pts[1:]))


@pytest.mark.parametrize("side", ["drag", "cope"])
def test_taper_makes_the_top_face_narrower(side):
    w_parting, w_far = workflow.runner_widths(side, 30.0, 15.0)
    top, bottom = (w_parting, w_far) if side == "drag" else (w_far, w_parting)
    assert bottom == pytest.approx(30.0) and top == pytest.approx(25.5)
    from core.registry import REGISTRY
    el = REGISTRY.get("runner")()
    sec = el.section(el.resolve_params({"side": side, "w_parting": w_parting, "w_far": w_far, "height": 20.0}))
    assert sec.d["top"] == pytest.approx(25.5) and sec.d["bottom"] == pytest.approx(30.0)


@pytest.mark.parametrize("pct", [9.0, 21.0])
def test_taper_outside_10_to_20_is_refused(pct):
    with pytest.raises(workflow.WorkflowError):
        workflow.runner_widths("drag", 30.0, pct)


def test_extension_past_the_last_side_ingate(doc):
    wp, wf = workflow.runner_widths("drag", 20.0, 15.0)
    # along the slab's front edge (y = -30), 25 mm out from it, drawn well past the part
    _apply(doc, workflow.runner_patch(doc, "drag", wp, wf, 15.0, [[-150, -55], [150, -55]],
                                      taper_pct=15.0, extension=40.0))
    assert runner(doc)["params"]["waypoints"][-1][:2] == [150, -55]        # no ingate yet: as drawn
    _apply(doc, workflow.ingate_patch(doc, "runner", [0, -55], [0, -29], "drag", 20.0, 6.0))
    end = runner(doc)["params"]["waypoints"][-1]
    assert end[0] == pytest.approx(40.0) and end[1] == pytest.approx(-55.0)
    assert runner(doc)["params"]["drawn"][-1][:2] == [150, -55]             # the drawing is kept
    # a second ingate further on: the runner follows it
    _apply(doc, workflow.ingate_patch(doc, "runner", [20, -55], [20, -29], "drag", 20.0, 6.0))
    assert runner(doc)["params"]["waypoints"][-1][0] == pytest.approx(60.0)
    # an extension longer than drawn: carried on straight past the drawn end
    i = next(k for k, e in enumerate(doc.data["elements"]) if e["id"] == "runner")
    _apply(doc, [{"op": "replace", "path": f"/elements/{i}/params/extension", "value": 200.0}])
    assert runner(doc)["params"]["waypoints"][-1][0] == pytest.approx(220.0)
    _apply(doc, [{"op": "replace", "path": f"/elements/{i}/params/extension", "value": 40.0}])
    res = regenerate(doc)
    assert res.ok, [x.message for x in res.issues if x.severity.value == "error"]
    # the runner ends 40 mm past the second ingate's start
    pts = res.built["runner"].element.points(res.built["runner"].params)
    assert pts[-1][0] == pytest.approx(60.0)


def test_extension_ignored_with_the_automatic_in_line_ingate(doc):
    wp, wf = workflow.runner_widths("drag", 20.0, 15.0)
    res = regenerate(doc)
    from core.mold import section_outline
    _apply(doc, workflow.runner_patch(doc, "cope", wp, wf, 15.0, [[-60, 0], [0, 0]],
                                      gate={"width": 20, "thickness": 6, "length": 15, "side": "cope"},
                                      outline=section_outline(res.part.metal, 1e-3) +
                                      [l for z in np.linspace(1e-3, 14.999, 6) for l in section_outline(res.part.metal, z)],
                                      taper_pct=15.0, extension=40.0))
    assert any(e["type"] == "ingate" and e["params"]["placed"] == "auto" for e in doc.data["elements"])
    assert runner(doc)["params"]["waypoints"][-1][0] == pytest.approx(-45.0)   # cut back by the ingate, not extended
