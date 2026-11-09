import json
import math

import numpy as np
import pytest
from build123d import Box, Cylinder, export_step, export_stl

from core.document import DesignDocument, empty
from core.part import load_part, step_length_unit
from core.regen import regenerate


def doc_for(tmp_path, source="plate.step", **part):
    d = empty("t")
    d["part"] = {"source": source, **part}
    return DesignDocument(d, tmp_path)


@pytest.fixture
def plate(tmp_path):
    export_step(Box(100, 80, 20), tmp_path / "plate.step")
    return tmp_path


def test_step_units_detected(plate):
    assert step_length_unit(plate / "plate.step") == "mm"
    pm = load_part(doc_for(plate))
    assert pm.source_unit == "mm" and pm.volume_mm3 == pytest.approx(160_000)


def test_unit_override_scales(plate):
    pm = load_part(doc_for(plate, units="cm"))          # "the file says mm but it is really cm"
    assert pm.volume_mm3 == pytest.approx(160_000 * 1000)


def test_stl_in_metres_flagged(tmp_path):
    export_stl(Box(0.1, 0.08, 0.02), tmp_path / "p.stl")
    pm = load_part(doc_for(tmp_path, "p.stl"))
    assert any(i.rule == "part.units_suspect" for i in pm.issues)
    pm = load_part(doc_for(tmp_path, "p.stl", units="m"))
    assert pm.volume_mm3 == pytest.approx(160_000, rel=1e-6)


def test_machining_allowance_and_shrinkage(plate):
    top = next(i for i, f in enumerate(load_part(doc_for(plate)).source.faces()) if f.center().Z > 9.9)
    pm = load_part(doc_for(plate, machining=[{"id": "top", "faces": [top], "allowance_mm": 3.0}], shrinkage_pct=1.0))
    assert pm.volume_mm3 == pytest.approx(100 * 80 * 23)                     # as cast, before the scale
    assert pm.shape.volume == pytest.approx(100 * 80 * 23 * 1.01 ** 3, rel=1e-6)
    assert pm.bbox[1][2] == pytest.approx(13 * 1.01)


def test_bad_machining_face(plate):
    pm = load_part(doc_for(plate, machining=[{"id": "x", "faces": [99], "allowance_mm": 2.0}]))
    assert [i.rule for i in pm.issues if i.severity.value == "error"] == ["part.machining_face"]


def test_primitive_core_with_transform(plate):
    core = {"id": "bore", "primitive": {"shape": "cylinder", "d": 20, "length": 140,
                                        "frame": {"origin": [0, 0, 0], "normal": [1, 0, 0], "up": [0, 0, 1]}}}
    pm = load_part(doc_for(plate, cores=[core], transform={"translation": [0, 0, 50]}))
    c = pm.cores["bore"]
    assert c.volume == pytest.approx(math.pi * 100 * 140, rel=1e-6)
    bb = c.bounding_box()
    assert (bb.min.X, bb.max.X) == pytest.approx((-70, 70)) and bb.min.Z == pytest.approx(40)


def test_flask_margin_and_outside_warning(plate):
    d = empty("t")
    d["part"] = {"source": "plate.step", "ports": [
        {"id": "top", "frame": {"origin": [0, 0, 10], "normal": [0, 0, -1], "up": [1, 0, 0]}}]}
    d["mold"]["flask"] = {"margin_mm": 50.0}
    d["elements"] = [{"id": "sprue", "type": "dummy_box", "params": {"length": 100.0}}]
    d["connections"] = [{"id": "c1", "from": {"instance": "sprue", "port": "outlet"},
                         "to": {"instance": "part", "port": "top"}, "joint": "part"}]
    r = regenerate(DesignDocument(d, plate))
    out = [i for i in r.issues if i.rule == "mold.outside_flask"]
    assert len(out) == 1 and out[0].instances == ("sprue",) and out[0].value == pytest.approx(51.0)   # top at z 111, flask to 60
    assert "mold.flask" in r.named_solids()
