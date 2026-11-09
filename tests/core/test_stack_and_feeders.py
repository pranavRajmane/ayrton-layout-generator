"""On the sprue: pouring cup, filter and well; on the part: feeders and vents."""
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
    d = empty("stack")
    d["part"] = {"source": "plate.step"}
    return DesignDocument(d, base_dir=tmp_path)


def apply(doc, ops):
    doc.apply(wf.with_sync(doc, ops))
    return regenerate(doc)


def bad(r):
    return sorted({(i.rule, i.message) for i in r.issues if i.severity.value in ("error", "warning")})


OUTLINE = [[[-100, -75], [100, -75], [100, 75], [-100, 75], [-100, -75]]]
GATE = {"width": 40, "thickness": 8, "length": 20, "side": "drag"}


def gated(doc):
    apply(doc, wf.box_patch(doc, 600, 400, 300, parting=120))              # top at z = 180
    r = regenerate(doc)
    apply(doc, wf.centre_part_patch(doc, r.part.bbox))                     # plate z -10..10
    apply(doc, wf.sprue_patch(doc, -200, 0, 30, 24))
    return apply(doc, wf.runner_patch(doc, "drag", 30, 20, 25, [[0, 0]], gate=GATE, outline=OUTLINE))


def z_range(s):
    b = s.bounding_box()
    return round(b.min.Z, 3), round(b.max.Z, 3)


def test_cup_on_the_sprue(doc):
    gated(doc)
    r = apply(doc, wf.cup_patch(doc, 80, 60))
    assert bad(r) == []
    assert r.built["pouring_cup"].port("inlet").frame.origin == pytest.approx((-200, 0, 180))
    assert r.built["sprue"].port("inlet").frame.origin == pytest.approx((-200, 0, 120))      # under the cup
    assert r.built["pouring_cup"].params.d_bottom == pytest.approx(30)                       # the sprue's top
    assert r.pouring_ports()[0][0] == "pouring_cup"
    r = apply(doc, wf.cup_patch(doc, None))
    assert "pouring_cup" not in r.built and r.built["sprue"].port("inlet").frame.origin[2] == pytest.approx(180)


def test_well_and_filter_under_the_sprue(doc):
    gated(doc)
    r = apply(doc, wf.well_patch(doc, 60, 20, {"shape": "square", "size": 50, "thickness": 20}))
    assert bad(r) == []
    assert z_range(r.built["filter"].solid) == (0, 20)                    # on the parting plane
    assert r.built["sprue"].port("outlet").frame.origin[2] == pytest.approx(20)   # onto the filter
    assert z_range(r.built["sprue_well"].solid) == (-45, 0)              # runner floor -25, 20 deeper
    assert wf.drop_source(doc.data) == "sprue_well"
    assert doc.data["connections"][0]["from"]["instance"] in ("sprue", "sprue_well")
    # the filter is its own region, exactly its size; the regions tile the metal
    named = r.named_solids()
    assert named["filter.filter"].volume == pytest.approx(50 * 50 * 20, rel=1e-6)
    metal = sum(v.volume for k, v in named.items() if k != "mold.flask")
    assert metal == pytest.approx(r.fused().volume, rel=1e-6)
    # without the filter, the sprue comes down to the runner floor and the well hangs under it
    r = apply(doc, wf.well_patch(doc, 60, 20))
    assert "filter" not in r.built and z_range(r.built["sprue_well"].solid) == (-45, -25)
    r = apply(doc, wf.remove_patch(doc, "sprue_well"))
    assert wf.drop_source(doc.data) == "sprue" and bad(r) == []


def test_top_feeder_and_vent(doc):
    gated(doc)
    r = apply(doc, wf.feeder_patch(doc, "top", 50, 0, 10, 60, 90))
    r = apply(doc, wf.vent_patch(doc, -50, 40, 10, 10))
    assert bad(r) == []
    assert z_range(r.built["feeder_1"].solid) == (9, 100)                 # on the plate top, 1 mm embedded
    assert z_range(r.built["vent_1"].solid)[1] == pytest.approx(180)      # to the box top
    assert {"feeder.feeder_1", "vent.vent_1"} <= set(r.named_solids())


def test_side_feeder_with_its_neck(doc):
    gated(doc)
    r = apply(doc, wf.feeder_patch(doc, "side", 0, 150, 0, 60, 90,
                                   {"width": 30, "thickness": 10, "side": "cope", "end": [0, 75]}))
    assert bad(r) == []
    assert z_range(r.built["feeder_1"].solid) == (-1, 90)
    assert len(fuse([r.part.shape] + [b.solid for b in r.built.values()]).solids()) == 1
    r = apply(doc, wf.remove_patch(doc, "feeder_1"))                      # takes its neck along
    assert "neck_1" not in r.built
