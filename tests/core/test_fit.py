"""Suggested pouring positions and the proposed box."""
import json
import math

import numpy as np
import pytest
from build123d import Box, Cylinder, Location, export_step

from core import fit
from core import workflow as wf
from core.document import DesignDocument, empty
from core.regen import regenerate


def housing():
    """A 200 mm flange 15 thick with a 120 mm hub 85 high on it (lying on its side, to be turned up)."""
    s = Cylinder(100, 15).moved(Location((0, 0, 7.5))) + Cylinder(60, 85).moved(Location((0, 0, 57.5)))
    return s.moved(Location((0, 0, 0), (1, 0, 0), 90))                     # the axis now along -Y


def test_mesh_section_is_exact_on_a_prism():
    m = fit.Mesh(Box(100, 60, 40) - Cylinder(10, 40)).turned(np.eye(3))
    assert m.section_area(0.0) == pytest.approx(6000 - math.pi * 100, rel=2e-3)


def test_widest_section_parts_on_the_flange_face():
    s = fit.suggest_widest(housing())
    assert sorted((round(s["below"], 3), round(s["above"], 3))) == [15.0, 85.0]
    assert s["undercut_mm2"] == 0.0


def test_flat_face_puts_the_whole_part_in_the_drag():
    s = fit.suggest_flat(housing())
    assert s["above"] == pytest.approx(0, abs=1e-6) and s["below"] == pytest.approx(100)
    assert s["face_mm2"] == pytest.approx(math.pi * 100 ** 2, rel=2e-3)          # the flange's back


def test_propose_box_from_clearances_rounded():
    b = fit.propose_box([[-100, -75, -15], [100, 75, 85]], sides=40, below=50, above=60, extra=100, step=50)
    assert (b["length"], b["width"], b["drag"], b["cope"]) == (400, 250, 100, 150)
    assert b["parting"] == 100 and b["height"] == 250 and b["standard"] is None
    assert b["shift"] == [50, 0, 0]


def test_propose_box_picks_the_smallest_standard_that_fits():
    std = [{"name": "big", "length": 800, "width": 600, "cope": 200, "drag": 200},
           {"name": "small", "length": 300, "width": 300, "cope": 100, "drag": 100},
           {"name": "turned", "length": 250, "width": 400, "cope": 150, "drag": 120}]
    b = fit.propose_box([[-100, -75, -15], [100, 75, 85]], sides=40, below=50, above=60, extra=100, standards=std)
    assert b["standard"] == "turned" and (b["length"], b["width"]) == (400, 250)


def test_saving_a_standard_box(tmp_path):
    p = tmp_path / "boxes.json"
    fit.add_standard_box({"length": 400, "width": 300, "cope": 150, "drag": 100}, p)
    boxes = fit.add_standard_box({"name": "A", "length": 500, "width": 400, "cope": 150, "drag": 150}, p)
    assert [b["name"] for b in boxes] == ["400 x 300 x 150/100", "A"]
    assert json.loads(p.read_text())["boxes"][1]["cope"] == 150


def test_apply_a_suggestion(tmp_path):
    from core.registry import REGISTRY
    REGISTRY.discover("elements")
    export_step(housing(), tmp_path / "housing.step")
    d = empty("fit")
    d["part"] = {"source": "housing.step"}
    doc = DesignDocument(d, base_dir=tmp_path)
    r = regenerate(doc)
    opt = fit.suggest_widest(r.part.shape)
    b = fit.propose_box(opt["bbox"], sides=40, below=50, above=60, extra=100, step=50)
    doc.apply(wf.with_sync(doc, wf.fit_patch(doc, opt["move"], b["length"], b["width"], b["height"],
                                             b["parting"], b["shift"])))
    r = regenerate(doc)
    lo, hi = r.part.bbox
    assert sorted((round(-lo[2], 3), round(hi[2], 3))) == [15.0, 85.0]          # parted on the flange face
    assert (lo[0] + hi[0]) / 2 == pytest.approx(50, abs=1e-6)                   # shifted for the sprue's room
    assert not [i for i in r.issues if i.rule == "mold.outside_flask"]
    doc.undo()                                                                   # one step
    assert doc.data["mold"]["flask"] is None
