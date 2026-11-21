"""Phases 6 and 7 through the layout generator's own server: upload, a job with the test recipe, a
background run, the grid, thumbnails that match the layouts, the rejected tab, a 3D build."""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
import pytest
from build123d import Box, export_step
from fastapi.testclient import TestClient

from layouts import footprint as fp
from layouts import server


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    server.JOBS = tmp_path_factory.mktemp("jobs")
    return TestClient(server.app)


@pytest.fixture(scope="module")
def upload(client, tmp_path_factory):
    f = tmp_path_factory.mktemp("part") / "block.step"
    export_step(Box(100, 60, 40), str(f))
    r = client.post("/api/layouts/upload", params={"name": "block.step"}, content=f.read_bytes())
    assert r.status_code == 200, r.text
    return r.json()


def spec(upload, **kw):
    s = {"name": "Block test", "upload": upload["upload"], "process": "green_sand", "alloy": "grey_iron",
         "recipe_set": "test", "flask": {"length": 800.0, "width": 700.0, "cope_height": 200.0, "drag_height": 200.0},
         "parting": {"move": next(x for x in upload["suggestions"] if x["id"] == "as_is")["move"], "source": "user"},
         "sprue": {"mode": "free", "position": None},
         "limits": {"n_max": 4, "rotations_deg": [0, 90]}}
    s.update(kw)
    return s


def wait(client, jid, timeout=600):
    t = time.time()
    while time.time() - t < timeout:
        p = client.get(f"/api/layouts/jobs/{jid}/progress").json()
        if not p["running"]:
            return p
        time.sleep(0.5)
    raise AssertionError("the run did not finish")


@pytest.fixture(scope="module")
def done(client, upload):
    r = client.post("/api/layouts/jobs", json=spec(upload))
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    client.post(f"/api/layouts/jobs/{jid}/run", json={})
    p = wait(client, jid)
    assert not p.get("error"), p
    return jid


def test_upload_reads_a_closed_solid_with_parting_suggestions(upload):
    assert upload["closed"] and upload["volume_mm3"] == pytest.approx(240000, rel=1e-6)
    assert {s["id"] for s in upload["suggestions"]} >= {"widest", "as_is"}


def test_bad_inputs_are_refused_with_reasons(client, upload):
    r = client.post("/api/layouts/jobs", json=spec(upload, recipe_set="presets"))      # blank foundry presets
    assert r.status_code == 422 and any("recipe lacks" in p for p in r.json()["detail"]["problems"])
    small = spec(upload, flask={"length": 120.0, "width": 700.0, "cope_height": 200.0, "drag_height": 200.0})
    r = client.post("/api/layouts/jobs", json=small)
    assert r.status_code == 422 and any("in length" in p for p in r.json()["detail"]["problems"])
    r = client.post("/api/layouts/jobs", json=spec(upload, alloy="steel"))
    assert r.status_code == 422
    # parted at its bottom face: nothing in the drag for the ingates
    on_top = spec(upload, parting={"move": next(x for x in upload["suggestions"] if x["below"] <= 1e-6)["move"]})
    r = client.post("/api/layouts/jobs", json=on_top)
    assert r.status_code == 422 and any("below the parting plane" in p for p in r.json()["detail"]["problems"])


def test_the_first_parting_choice_leaves_metal_in_the_drag(upload):
    assert upload["suggestions"][0]["below"] > 0


def test_a_run_goes_through_every_phase(client, done):
    s = client.get(f"/api/layouts/jobs/{done}").json()
    assert all(v == "done" for v in s["status"].values())
    c = s["counts"]
    assert c["enumerated"] == c["accepted"] + c["rejected"]
    assert s["progress"]["counted"] > 0


def test_every_accepted_layout_is_in_the_grid_once(client, done):
    g = client.get(f"/api/layouts/jobs/{done}/layouts", params={"limit": 1000}).json()
    ids = [c["id"] for c in g["items"]]
    s = client.get(f"/api/layouts/jobs/{done}").json()
    assert len(ids) == len(set(ids)) == s["counts"]["accepted"]
    assert all(c["metrics"] is not None for c in g["items"])
    two = client.get(f"/api/layouts/jobs/{done}/layouts", params={"n": "2", "limit": 1000}).json()
    assert two["items"] and all(c["n"] == 2 for c in two["items"])
    by = client.get(f"/api/layouts/jobs/{done}/layouts", params={"sort": "yield_pct", "desc": True, "limit": 1000}).json()
    y = [c["metrics"]["yield_pct"] for c in by["items"]]
    assert y == sorted(y, reverse=True)


def test_thumbnails_match_the_layouts(client, done):
    job = server._load(done)
    ctx = server._context(job)
    for lay in job.data["layouts"][:6]:
        svg = client.get(f"/api/layouts/jobs/{done}/layouts/{lay['id']}/thumb.svg").text
        drawn = {int(m[0]): (float(m[1]), float(m[2])) for m in
                 re.findall(r'data-cavity="(\d+)" data-cx="([-\d.]+)" data-cy="([-\d.]+)"', svg)}
        assert sorted(drawn) == list(range(lay["n"]))
        for c in lay["cavities"]:
            want = fp.place(ctx.outline, ctx.scale, c["rotation_deg"], c["translation"], c["mirrored"]).centroid
            assert np.hypot(drawn[c["index"]][0] - want.x, drawn[c["index"]][1] - want.y) < 1e-2
        sx, sy = map(float, re.search(r'class="sprue" data-x="([-\d.]+)" data-y="([-\d.]+)"', svg).groups())
        assert (sx, sy) == pytest.approx(tuple(lay["sprue"]), abs=1e-2)


def test_rejected_tab_groups_by_reason(client, done):
    r = client.get(f"/api/layouts/jobs/{done}/rejected").json()
    assert sum(g["count"] for g in r["groups"]) >= r["total"]
    if r["groups"]:
        code = r["groups"][0]["code"]
        items = client.get(f"/api/layouts/jobs/{done}/rejected", params={"code": code}).json()["items"]
        assert items and all(any(x["code"] == code for x in it["reasons"]) for it in items)


def test_star_and_build_and_download(client, done):
    g = client.get(f"/api/layouts/jobs/{done}/layouts", params={"limit": 1}).json()["items"][0]
    assert client.post(f"/api/layouts/jobs/{done}/layouts/{g['id']}/star", json={"starred": True}).json()["starred"] == [g["id"]]
    starred = client.get(f"/api/layouts/jobs/{done}/layouts", params={"starred": True}).json()
    assert [c["id"] for c in starred["items"]] == [g["id"]]
    b = client.post(f"/api/layouts/jobs/{done}/layouts/{g['id']}/build")
    assert b.status_code == 200, b.text
    assert all(c["passed"] for c in b.json()["validation"])
    st = client.get(f"/api/layouts/jobs/{done}/layouts/{g['id']}/step")
    assert st.status_code == 200 and b"ISO-10303-21" in st.content[:200]
    for what in ("cavities", "gating"):
        m = client.get(f"/api/layouts/jobs/{done}/layouts/{g['id']}/model/{what}.glb")
        assert m.status_code == 200 and m.content[:4] == b"glTF"
    d = client.get(f"/api/layouts/jobs/{done}/layouts/{g['id']}").json()
    assert d["assembly"]["step"].endswith(".step") and d["starred"]


def test_disabling_an_ingate_makes_layouts_stale(client, done):
    a = client.get(f"/api/layouts/jobs/{done}/analysis").json()
    assert a["modulus"]["zones"] and a["ingates"]["candidates"]
    m = client.get(f"/api/layouts/jobs/{done}/part_mesh").json()
    assert len(m["positions"]) // 3 == len(m["modulus"]) and len(m["triangles"]) // 3 == len(m["faces"])
    gid = a["ingates"]["candidates"][0]["id"]
    s = client.post(f"/api/layouts/jobs/{done}/inputs", json={"limits": {"disabled_ingates": [gid]}}).json()
    assert s["status"]["layouts"] == "stale" and s["status"]["modulus"] == "done"
