import numpy as np
import pytest
from build123d import Box, export_step

from core import ops
from core.document import DesignDocument
from core.registry import REGISTRY
from core.regen import regenerate
from tests.core.test_joints import runner_design


@pytest.fixture(scope="module")
def plate_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("ops")
    export_step(Box(200, 150, 20), d / "plate.step")
    return d


def errors(r):
    return [(i.rule, i.instances) for i in r.issues if i.severity.value == "error"]


def test_reflection_is_an_involution():
    m = ops.reflection((0, 5, 0), (0, 1, 0))
    assert np.allclose(m @ m, np.eye(4))
    assert np.allclose(m[:3, :3] @ (0, 1, 0), (0, -1, 0)) and np.allclose(m[:3, 3], (0, 10, 0))


def test_mirror_ingates_across_the_runner_axis(plate_dir):
    doc = DesignDocument(runner_design(), plate_dir)
    # move the two ingates to the other side of y = 0: their part ports mirror to y = +40
    patch = ops.mirror(doc, REGISTRY, ["ingate_1", "ingate_2"], origin=(0, 0, 0), normal=(0, 1, 0))
    doc.apply(patch)
    d = doc.data
    assert {e["id"] for e in d["elements"]} >= {"ingate_1_m", "ingate_2_m"}
    ports = {p["id"]: p["frame"]["origin"] for p in d["part"]["ports"]}
    assert ports["g1_m"] == pytest.approx([-50, 40, 10])
    new = [c for c in d["connections"] if c["id"].endswith("_m")]
    assert {c["from"]["port"] for c in new if c["joint"] == "branch"} == {"tap_1_m", "tap_2_m"}
    r = regenerate(doc)
    # the mirrored ingates sit at y = +40, but the runner runs along y = -40: their taps cannot reach
    assert any(rule == "placement.joint_gap" for rule, _ in errors(r))
    assert doc.undo() and len(doc.data["elements"]) == 5                   # one undo step


def test_mirror_whole_system_is_clean(plate_dir):
    doc = DesignDocument(runner_design(), plate_dir)
    doc.apply(ops.mirror(doc, REGISTRY, ["feeder", "runner", "ingate_1", "ingate_2", "ext"], (0, 0, 0), (0, 1, 0)))
    r = regenerate(doc)
    assert [e for e in errors(r) if e[0] != "system.pouring_inlet"] == []
    assert len(r.built) == 10
    assert r.built["runner_m"].derived["length_mm"] == pytest.approx(350.0)


def test_linear_pattern_with_cavities(plate_dir):
    doc = DesignDocument(runner_design(), plate_dir)
    doc.apply(ops.linear_pattern(doc, REGISTRY, ["feeder", "runner", "ingate_1", "ingate_2", "ext"], (0, 300, 0), 3,
                                 cavities=True))
    r = regenerate(doc)
    assert errors(r) == []
    names = r.named_solids()
    assert {"part", "part.cav_2", "part.cav_3"} <= set(names)
    assert names["part.cav_3"].bounding_box().min.Y == pytest.approx(-75 + 600)
    assert len(r.built) == 15 and len(r.fused().solids()) == 3                 # three separate castings


def test_circular_pattern_angles(plate_dir):
    doc = DesignDocument(runner_design(), plate_dir)
    patch = ops.circular_pattern(doc, REGISTRY, ["ingate_1"], (0, 0, 0), (0, 0, 1), 4)
    doc.apply(patch)
    ports = {p["id"]: p["frame"]["origin"] for p in doc.data["part"]["ports"]}
    assert ports["g1_2"] == pytest.approx([40, -50, 10])              # (-50, -40) turned 90 deg
    assert ports["g1_3"] == pytest.approx([50, 40, 10])


def test_copy_refuses_clashing_ids(plate_dir):
    doc = DesignDocument(runner_design(), plate_dir)
    doc.apply(ops.mirror(doc, REGISTRY, ["ingate_1"], (0, 0, 0), (0, 1, 0)))
    with pytest.raises(ValueError):
        ops.mirror(doc, REGISTRY, ["ingate_1"], (0, 0, 0), (0, 1, 0))
