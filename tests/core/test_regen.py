import time

import numpy as np
import pytest
from build123d import Box, export_step

from core.document import DesignDocument, empty
from core.registry import REGISTRY
from core.regen import regenerate

REGISTRY.discover("elements")
FACE = {"origin": [-100.0, 0.0, 0.0], "normal": [1.0, 0.0, 0.0], "up": [0.0, 0.0, 1.0]}   # the plate's -X face, flow in


@pytest.fixture(scope="module")
def plate_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("plate")
    export_step(Box(200, 150, 20), d / "plate.step")
    return d


def chain(n, length=30.0, anchor_first=False):
    doc = empty("chain")
    doc["part"] = {"source": "plate.step", "ports": [{"id": "face_1", "frame": FACE}]}
    doc["elements"] = [{"id": f"b{i}", "type": "dummy_box", "params": {"length": length}} for i in range(n)]
    doc["connections"] = [
        {"id": f"c{i}", "from": {"instance": f"b{i}", "port": "outlet"},
         "to": {"instance": f"b{i + 1}", "port": "inlet"}, "joint": "butt"} for i in range(n - 1)]
    doc["connections"].append({"id": "cp", "from": {"instance": f"b{n - 1}", "port": "outlet"},
                               "to": {"instance": "part", "port": "face_1"}, "joint": "part"})
    return doc


def errors(r):
    return [i.to_dict() for i in r.issues if i.severity.value == "error"]


def test_chain_hung_from_the_part(plate_dir):
    r = regenerate(DesignDocument(chain(3), plate_dir))
    assert errors(r) == []
    # b2's outlet sits on the face; b0's inlet 90 mm upstream along -X
    assert np.allclose(r.built["b2"].port("outlet").frame.origin, (-100, 0, 0))
    assert np.allclose(r.built["b0"].port("inlet").frame.origin, (-190, 0, 0))
    assert [n for n, _ in [(i, p.id) for i, p in r.pouring_ports()]] == ["b0"]


def test_overlaps_owned_downstream(plate_dir):
    r = regenerate(DesignDocument(chain(3), plate_dir))
    s = r.named_solids()
    # each box is 32 mm long with embed.  At a box-box joint both embeds overlap (2 mm), all of it the
    # downstream box's; at the part joint only the box's own 1 mm embed lies inside the part.
    assert s["gating.b0"].volume == pytest.approx(30 * 400, rel=1e-6)
    assert s["gating.b1"].volume == pytest.approx(30 * 400, rel=1e-6)
    assert s["gating.b2"].volume == pytest.approx(31 * 400, rel=1e-6)
    assert s["part"].volume == pytest.approx(200 * 150 * 20)
    total = sum(v.volume for v in s.values())
    assert r.fused().volume == pytest.approx(total, rel=1e-6)      # the regions tile the metal
    assert len(r.fused().solids()) == 1


def test_measurement_planes(plate_dir):
    r = regenerate(DesignDocument(chain(3), plate_dir))
    names = [p["name"] for p in r.measurement_planes()]
    assert names == ["b0.inlet", "b0.outlet", "b1.outlet", "b2.outlet"]


def test_ten_elements_regenerate_under_two_seconds(plate_dir):
    doc = DesignDocument(chain(10), plate_dir)
    regenerate(doc)                                   # warm the part cache, as the editor would be
    doc.set_param("b4", "length", 45.0)
    t = time.perf_counter()
    r = regenerate(doc)
    r.fused()
    dt = time.perf_counter() - t
    assert errors(r) == [] and len(r.built) == 10
    assert dt < 2.0, f"regeneration took {dt:.2f} s"


def test_failures_stay_on_their_element(plate_dir):
    d = chain(3)
    d["elements"][1]["params"]["length"] = -5.0
    r = regenerate(DesignDocument(d, plate_dir))
    by = {i.rule: i.instances for i in r.issues}
    assert by["element.params"] == ("b1",)
    assert by["placement.neighbour_failed"] == ("b0",)
    assert "b2" in r.built                                   # hung from the part, still built


def test_butt_section_mismatch_and_follows(plate_dir):
    d = chain(2)
    d["elements"][1]["params"].update(width=30.0)
    r = regenerate(DesignDocument(d, plate_dir))
    assert [i.rule for i in r.issues if i.severity.value == "error"] == ["joint.butt_section_mismatch"]
    # the downstream inlet follows: b1 becomes 20 x 20 like b0, no mismatch
    d = chain(2)
    d["elements"][0]["anchor"] = {"frame": {"origin": [-160, 0, 0], "normal": [1, 0, 0], "up": [0, 0, 1]}}
    d["elements"][0]["params"].update(width=30.0, height=12.0)
    d["elements"][1]["params"].update(inlet_policy="follows")
    r = regenerate(DesignDocument(d, plate_dir))
    assert errors(r) == []
    assert (r.built["b1"].params.width, r.built["b1"].params.height) == (30.0, 12.0)


def test_over_constrained_gap_reported(plate_dir):
    d = chain(2)
    d["elements"][0]["anchor"] = {"frame": {"origin": [-160, 5, 0], "normal": [1, 0, 0], "up": [0, 0, 1]}}
    r = regenerate(DesignDocument(d, plate_dir))
    gaps = [i for i in r.issues if i.rule == "placement.joint_gap"]
    assert len(gaps) == 1 and gaps[0].connection == "c0" and gaps[0].value == pytest.approx(5.0)


def test_unanchored_and_unknown_type(plate_dir):
    d = chain(1)
    d["elements"].append({"id": "loose", "type": "dummy_box", "params": {}})
    d["elements"].append({"id": "ghost", "type": "no_such_element", "params": {}})
    r = regenerate(DesignDocument(d, plate_dir))
    rules = {i.instances[0]: i.rule for i in r.issues if i.instances and i.severity.value == "error"}
    assert rules["loose"] == "placement.unanchored" and rules["ghost"] == "element.params"


def test_missing_part_file(tmp_path):
    r = regenerate(DesignDocument(chain(1), tmp_path))
    assert any(i.rule == "part.load_failed" for i in r.issues)
