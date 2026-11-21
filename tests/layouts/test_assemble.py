"""Phase 5: routed layouts built in 3D by the CAD module (the block of test_runners, hand-built layouts)."""
from __future__ import annotations

import copy
import math

import pytest
from build123d import import_step

from core.document import DesignDocument
from core.regen import regenerate
from layouts import assemble as asm
from layouts import runners as rn
from layouts.job import GatingJob

from .test_runners import base, h4, radial4, straight2, tree8   # noqa: F401  (the fixture and layouts)


@pytest.fixture(scope="module")
def routed(base, tmp_path_factory):                               # noqa: F811
    job0, ctx = base
    out = tmp_path_factory.mktemp("asm")
    job = GatingJob(job0.data, out / "blk.job.json")
    job.finish("layouts", [straight2(job), h4(job), tree8(job), radial4(job)])
    ok, rej = rn.run(job, ctx)
    assert not rej
    job.save()
    return job, out


@pytest.mark.parametrize("lid", ["straight2", "h4", "tree8", "radial4"])
def test_every_check_passes(routed, lid):
    job, out = routed
    rec = asm.assemble(job, lid, out)
    bad = [c for c in rec["validation"] if not c["passed"]]
    assert not bad, bad
    codes = {c["code"] for c in rec["validation"]}
    assert {"assembly.regenerated", "assembly.one_solid", "assembly.watertight", "assembly.volume",
            "assembly.step_round_trip", "draft.sprue", "draft.pouring_cup", "draft.sprue_well"} <= codes
    assert any(c.startswith("draft.runner") for c in codes) and any(c.startswith("draft.ingate") for c in codes)
    assert job.data["assemblies"][lid]["step"] == f"blk.{lid}.step"
    assert abs(import_step(str(out / f"blk.{lid}.step")).volume - rec["volume_mm3"]) <= 1e-3 * rec["volume_mm3"]


def test_the_design_opens_in_the_cad_module_as_built(routed):
    job, out = routed
    asm.assemble(job, "h4", out)
    doc = DesignDocument.load(out / "blk.h4.gating.json")
    els = doc.data["elements"]
    types = sorted({e["type"] for e in els})
    assert types == ["ingate", "pouring_cup", "runner", "sprue", "sprue_well"]
    assert sum(e["type"] == "ingate" for e in els) == 4
    assert len(doc.data["part"]["cavities"]) == 3
    res = regenerate(doc)
    assert res.ok, [i.message for i in res.issues if i.severity.value == "error"]
    rec = next(r for r in job.data["runners"] if r["layout"] == "h4")
    # the sprue: narrow top the sized area, bottom opened by the draft over its length as placed
    sp = next(e for e in els if e["type"] == "sprue")["params"]
    assert math.pi * sp["d_top"] ** 2 / 4 == pytest.approx(rec["sizing"]["sprue_area_mm2"], rel=1e-5)
    draft = job.data["recipe"]["values"]["layout"]["draft_deg"]
    assert sp["d_bottom"] == pytest.approx(sp["d_top"] + 2 * sp["length"] * math.tan(math.radians(draft)))
    # every runner and ingate widest at the parting plane
    for e in els:
        if e["type"] == "runner":
            assert e["params"]["w_parting"] > e["params"]["w_far"]
        if e["type"] == "ingate":
            assert e["params"]["draft_deg"] == draft


def test_more_draft_than_the_runner_taper_fails_the_runner_draft_check(routed):
    job, out = routed
    asm.assemble(job, "h4", out)
    doc = DesignDocument.load(out / "blk.h4.gating.json")
    checks = asm.draft_checks(doc, 6.0)                           # the runners lean 4.3 degrees (15 % taper)
    runners = [c for c in checks if c["code"].startswith("draft.runner")]
    assert runners and not any(c["passed"] for c in runners)
    assert all(c["value"] == pytest.approx(4.2892, abs=1e-3) for c in runners)


def test_mirrored_cavities_are_refused_with_a_reason(routed):
    job, out = routed
    data = copy.deepcopy(job.data)
    data["layouts"][0]["cavities"][1]["mirrored"] = True
    j2 = GatingJob.__new__(GatingJob)
    j2._data, j2.path = data, None
    with pytest.raises(asm.AssemblyError, match="mirrored"):
        asm.design(j2, data["layouts"][0]["id"], out)


def test_only_routed_layouts_are_built(routed):
    job, out = routed
    with pytest.raises(asm.AssemblyError, match="not routed"):
        asm.design(job, "nope", out)
