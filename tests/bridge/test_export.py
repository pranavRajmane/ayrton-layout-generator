import json

import numpy as np
import pytest
from build123d import Box, export_step

from bridge.ayrton import ExportError, export
from bridge.step_names import read_step_names
from core.document import DesignDocument, empty
from core.registry import REGISTRY
from core.regen import regenerate

REGISTRY.discover("elements")
TOP = {"origin": [0.0, 0.0, 10.0], "normal": [0.0, 0.0, -1.0], "up": [1.0, 0.0, 0.0]}


def read_stl(path):
    """Triangles of a binary or ASCII STL as (n, 3, 3)."""
    raw = path.read_bytes()
    if raw[:5] == b"solid" and b"facet" in raw[:300]:
        v = [list(map(float, l.split()[1:])) for l in raw.decode().splitlines() if l.strip().startswith("vertex")]
        return np.array(v).reshape(-1, 3, 3)
    n = int.from_bytes(raw[80:84], "little")
    a = np.frombuffer(raw[84:84 + 50 * n], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    return a["v"].reshape(-1, 3, 3).astype(float)


def stl_volume(t):
    return abs(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()) / 6


@pytest.fixture(scope="module")
def sprue_on_plate(tmp_path_factory):
    d = tmp_path_factory.mktemp("bridge")
    export_step(Box(200, 150, 20), d / "plate.step")
    doc = empty("sprue on plate")
    doc["part"] = {"source": "plate.step", "ports": [{"id": "top", "frame": TOP}]}
    doc["elements"] = [{"id": "sprue_1", "type": "dummy_box", "params": {"length": 100.0}}]
    doc["connections"] = [{"id": "c1", "from": {"instance": "sprue_1", "port": "outlet"},
                           "to": {"instance": "part", "port": "top"}, "joint": "part"}]
    return regenerate(DesignDocument(doc, d)), d


def test_project_layout_and_names(sprue_on_plate, tmp_path):
    res, _ = sprue_on_plate
    m = export(res, tmp_path)
    proj = tmp_path / "project"
    for f in ("geometry/cast.stl", "geometry/inlet.stl", "materials/transform.json", "regions/manifest.json",
              "regions/part.stl", "regions/gating.sprue_1.stl"):
        assert (proj / f).exists(), f
    assert read_step_names(tmp_path / "design.step") == {"sprue on plate": ["part", "gating.sprue_1"]}
    assert m["missing"] == ["materials/materials.json", "materials/pour.json"]
    assert [p["name"] for p in m["measurement_planes"]] == ["sprue_1.inlet", "sprue_1.outlet"]


def test_inlet_on_the_origin_facing_up(sprue_on_plate, tmp_path):
    res, _ = sprue_on_plate
    export(res, tmp_path)
    tr = json.loads((tmp_path / "project/materials/transform.json").read_text())
    assert tr["inlet"]["normal"] == pytest.approx([0, 0, 1])
    assert tr["inlet"]["centre"] == pytest.approx([0, 0, 110])          # 100 mm sprue on the 10 mm top face
    assert tr["inlet"]["area"] == pytest.approx(400)
    inl = read_stl(tmp_path / "project/geometry/inlet.stl")
    assert np.allclose(inl[..., 2], 0, atol=1e-4)
    area = np.abs(np.cross(inl[:, 1] - inl[:, 0], inl[:, 2] - inl[:, 0])[:, 2]).sum() / 2
    assert area == pytest.approx(400, rel=1e-4)


def test_regions_tile_the_metal(sprue_on_plate, tmp_path):
    res, _ = sprue_on_plate
    m = export(res, tmp_path)
    proj = tmp_path / "project"
    cast = stl_volume(read_stl(proj / "geometry/cast.stl"))
    regions = sum(stl_volume(read_stl(proj / r["file"])) for r in m["regions"])
    assert cast == pytest.approx(200 * 150 * 20 + 100 * 400, rel=1e-4)
    assert regions == pytest.approx(cast, rel=1e-4)
    top = read_stl(proj / "geometry/cast.stl")[..., 2].max()
    assert top == pytest.approx(0.0, abs=1e-4)        # the open inlet's embed is trimmed at the inlet plane


def test_errors_block_export(tmp_path):
    d = empty("bad")
    d["elements"] = [{"id": "loose", "type": "dummy_box", "params": {}}]
    with pytest.raises(ExportError):
        export(regenerate(DesignDocument(d, tmp_path)), tmp_path)
