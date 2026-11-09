"""The reference parts end to end: load, regenerate, export, compare with the
hand-computed expected outputs.  Set AYRTON_SOLID=<path to ayrton-solid> to
also build each export into an Ayrton voxel case with validation.stl_case."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from bridge.ayrton import export
from bridge.step_names import read_step_names
from core.document import DesignDocument
from core.registry import REGISTRY
from core.regen import regenerate

REGISTRY.discover("elements")
REF = Path(__file__).parent / "reference_parts"
PARTS = ["plate", "flanged_housing", "cored_part"]


@pytest.fixture(params=PARTS)
def case(request, tmp_path):
    folder = REF / request.param
    doc = DesignDocument.load(folder / "design.json")
    res = regenerate(doc)
    return res, json.loads((folder / "expected.json").read_text()), tmp_path


def test_regenerates_clean(case):
    res, exp, _ = case
    assert [i.to_dict() for i in res.issues if i.severity.value == "error"] == []
    assert res.part.volume_mm3 == pytest.approx(exp["part_volume_mm3"], rel=1e-3)
    for got, want in zip(res.part.bbox, exp["part_bbox"]):
        assert got == pytest.approx(want, abs=1e-3)


def test_export_matches_expected(case):
    res, exp, out = case
    m = export(res, out)
    assert [r["name"] for r in m["regions"]] == exp["regions"]
    assert m["metal"]["volume_mm3"] == pytest.approx(exp["metal_volume_mm3"], rel=0.01)
    assert [p["name"] for p in m["measurement_planes"]] == exp["measurement_planes"]
    assert list(read_step_names(out / "design.step").values()) == [exp["regions"]]
    tr = json.loads((out / "project/materials/transform.json").read_text())
    assert tr["inlet"]["centre"] == pytest.approx(exp["inlet_centre"], abs=1e-6)
    if "core_volume_mm3" in exp:
        core = next(r for r in m["regions"] if r["kind"] == "core")
        assert core["volume_mm3"] == pytest.approx(exp["core_volume_mm3"], rel=1e-3)


@pytest.mark.skipif(not os.environ.get("AYRTON_SOLID"), reason="set AYRTON_SOLID to the ayrton-solid checkout")
def test_ayrton_builds_a_case(case):
    res, exp, out = case
    export(res, out)
    root = Path(os.environ["AYRTON_SOLID"])
    py = root / ".venv" / "bin" / "python"
    r = subprocess.run([str(py), "-m", "validation.stl_case", "build", str(out / "project"), "--dx", "0.002",
                        "--out", str(out / "case.npz")], cwd=root, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    info = json.loads((out / "case.json").read_text())
    assert info["V_cad"] * 1e9 == pytest.approx(exp["metal_volume_mm3"], rel=0.01)
    assert info["cap_cut"] == 0 and info["cut_off"] == 0 and info["n_inlet"] > 0
