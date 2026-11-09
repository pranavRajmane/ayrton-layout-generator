import math

import pytest
from build123d import Box, Location, export_step

from core.document import DesignDocument, empty
from core.regen import regenerate


@pytest.fixture(scope="module")
def plate_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("joints")
    export_step(Box(200, 150, 20), d / "plate.step")                      # top face at z = 10
    export_step(Box(200, 150, 20).moved(Location((0, 0, -10))), d / "low_plate.step")   # top on the parting plane
    return d


def F(o, n, u):
    return {"origin": list(o), "normal": list(n), "up": list(u)}


def errors(r):
    return [(i.rule, i.instances) for i in r.issues if i.severity.value == "error"]


def con(cid, a, ap, b, bp, joint="butt", **kw):
    return {"id": cid, "from": {"instance": a, "port": ap, **kw.pop("frm", {})}, "to": {"instance": b, "port": bp},
            "joint": joint, **kw}


def runner_design(ingate_width=20.0, extra=None):
    d = empty("runner")
    d["part"] = {"source": "plate.step", "ports": [
        {"id": "g1", "frame": F((-50, -40, 10), (0, 0, -1), (1, 0, 0))},
        {"id": "g2", "frame": F((50, -40, 10), (0, 0, -1), (1, 0, 0))}]}
    d["elements"] = [
        {"id": "feeder", "type": "dummy_box", "params": {"length": 50.0, "width": 30.0, "height": 25.0},
         "anchor": {"frame": F((-250, -40, 40), (1, 0, 0), (0, 0, 1))}},
        {"id": "runner", "type": "test_runner", "params": {"waypoints": [[150, -40, 40]], "top": 30.0, "bottom": 30.0}},
        {"id": "ingate_1", "type": "dummy_box", "params": {"length": 17.5, "width": ingate_width}},
        {"id": "ingate_2", "type": "dummy_box", "params": {"length": 17.5, "width": ingate_width}},
        {"id": "ext", "type": "dummy_box", "params": {"length": 30.0, "width": 30.0, "height": 25.0}},
    ]
    d["connections"] = [
        con("c0", "feeder", "outlet", "runner", "inlet"),
        con("c1", "runner", "tap_1", "ingate_1", "inlet", "branch", frm={"side": "bottom"}),
        con("c2", "runner", "tap_2", "ingate_2", "inlet", "branch", frm={"side": "bottom"}),
        con("p1", "ingate_1", "outlet", "part", "g1", "part"),
        con("p2", "ingate_2", "outlet", "part", "g2", "part"),
        con("x1", "runner", "outlet", "ext", "inlet", "blind"),
    ]
    if extra:
        extra(d)
    return d


def test_runner_path_taps_and_branches(plate_dir):
    r = regenerate(DesignDocument(runner_design(), plate_dir))
    assert errors(r) == []
    run = r.built["runner"]
    assert run.derived["length_mm"] == pytest.approx(350.0)            # from the feeder's outlet to the waypoint
    assert [round(r.ends[c].station, 4) for c in ("c1", "c2")] == [round(150 / 350, 4), round(250 / 350, 4)]
    planes = [p["name"] for p in r.measurement_planes()]
    assert "runner.tap_1@0.429" in planes and "runner.tap_2@0.714" in planes
    s = r.named_solids()
    total = sum(v.volume for k, v in s.items())
    assert r.fused().volume == pytest.approx(total, rel=1e-6)          # regions tile the metal


def test_branch_that_does_not_fit(plate_dir):
    r = regenerate(DesignDocument(runner_design(ingate_width=40.0), plate_dir))
    assert ("joint.branch_fit", ("runner", "ingate_1")) in errors(r)


def test_collision_with_unconnected_gating(plate_dir):
    def add(d):
        d["elements"].append({"id": "stray", "type": "dummy_box", "params": {"length": 60.0},
                              "anchor": {"frame": F((0, -70, 40), (0, 1, 0), (0, 0, 1))}})
    r = regenerate(DesignDocument(runner_design(extra=add), plate_dir))
    assert ("collision.gating", ("runner", "stray")) in errors(r)


def test_collision_with_part_and_core(plate_dir):
    def add(d):
        d["part"]["cores"] = [{"id": "c", "primitive": {"shape": "cylinder", "d": 10, "length": 400,
                                                        "frame": F((0, -40, 40), (1, 0, 0), (0, 0, 1))}}]
        d["elements"].append({"id": "sinker", "type": "dummy_box", "params": {"length": 40.0},
                              "anchor": {"frame": F((60, 40, 20), (0, 0, -1), (1, 0, 0))}})
    r = regenerate(DesignDocument(runner_design(extra=add), plate_dir))
    errs = errors(r)
    assert ("collision.part", ("sinker",)) in errs and ("collision.core", ("runner",)) in errs


def test_transition_round_to_rectangle(plate_dir):
    d = empty("transition")
    d["part"] = {"source": "plate.step", "ports": [{"id": "side", "frame": F((-100, 0, 0), (1, 0, 0), (0, 0, 1))}]}
    d["elements"] = [
        {"id": "bar", "type": "test_round_bar", "params": {"length": 60.0, "d": 20.0},
         "anchor": {"frame": F((-200, 0, 0), (1, 0, 0), (0, 0, 1))}},
        {"id": "gate", "type": "dummy_box", "params": {"length": 10.0, "width": 30.0, "height": 15.0}}]
    d["connections"] = [con("t1", "bar", "outlet", "gate", "inlet", "transition", transition={"length_mm": 30.0}),
                        con("p1", "gate", "outlet", "part", "side", "part")]
    r = regenerate(DesignDocument(d, plate_dir))
    assert errors(r) == []
    t = r.transitions["t1"]
    a1, a2 = math.pi * 100, 450.0
    assert t.solid.volume == pytest.approx(30 * (a1 + a2) / 2, rel=0.1)      # between the prism bounds
    assert "gating.t1" in r.named_solids()
    s = r.named_solids()
    assert r.fused().volume == pytest.approx(sum(v.volume for v in s.values()), rel=1e-6)
    assert [p["name"] for p in r.measurement_planes()] == ["bar.inlet", "bar.outlet", "gate.inlet", "gate.outlet"]


def test_transition_without_length(plate_dir):
    d = empty("t")
    d["elements"] = [{"id": "a", "type": "test_round_bar", "params": {}, "anchor": {"frame": F((0, 0, 0), (1, 0, 0), (0, 0, 1))}},
                     {"id": "b", "type": "dummy_box", "params": {}}]
    d["connections"] = [con("t1", "a", "outlet", "b", "inlet", "transition")]
    r = regenerate(DesignDocument(d, plate_dir))
    assert ("joint.transition_length", ("a", "b")) in errors(r)


def test_part_contact_overhang(plate_dir):
    d = empty("t")
    d["part"] = {"source": "plate.step", "ports": [{"id": "edge", "frame": F((95, 0, 10), (0, 0, -1), (1, 0, 0))}]}
    d["elements"] = [{"id": "g", "type": "dummy_box", "params": {"length": 30.0}}]
    d["connections"] = [con("p", "g", "outlet", "part", "edge", "part")]
    r = regenerate(DesignDocument(d, plate_dir))
    issue = next(i for i in r.issues if i.rule == "joint.part_contact")
    assert issue.value == pytest.approx(0.75, abs=0.01)                    # 15 of its 20 mm sit on the plate


def test_port_constraints(plate_dir):
    def design(src, z):
        d = empty("t")
        d["part"] = {"source": src, "ports": [{"id": "s", "frame": F((-100, 0, z), (1, 0, 0), (0, 0, 1))}]}
        d["elements"] = [{"id": "g", "type": "test_parting_gate", "params": {"length": 30.0}}]
        d["connections"] = [con("p", "g", "outlet", "part", "s", "part")]
        return d
    r = regenerate(DesignDocument(design("plate.step", 5.0), plate_dir))
    assert ("port.on_parting_plane", ("g",)) in errors(r)
    r = regenerate(DesignDocument(design("low_plate.step", -5.0), plate_dir))
    assert ("port.on_parting_plane", ("g",)) in errors(r)


def test_blind_end(plate_dir):
    r = regenerate(DesignDocument(runner_design(), plate_dir))
    assert errors(r) == []                    # the extension closes the runner; a 30/30 trapezoid butts a 30-wide box

    def add_outflow(d):
        d["elements"].append({"id": "more", "type": "dummy_box", "params": {"width": 30.0, "height": 25.0}})
        d["connections"].append(con("b2", "ext", "outlet", "more", "inlet"))
    r = regenerate(DesignDocument(runner_design(extra=add_outflow), plate_dir))
    assert ("joint.blind_outflow", ("ext",)) in errors(r)


def test_open_outlet_is_a_leak(plate_dir):
    def drop_ext(d):
        d["elements"] = [e for e in d["elements"] if e["id"] != "ext"]
        d["connections"] = [c for c in d["connections"] if c["id"] != "x1"]
    r = regenerate(DesignDocument(runner_design(extra=drop_ext), plate_dir))
    assert errors(r) == [("system.dangling_outlet", ("runner",))]


def test_bend_radius_on_the_parting_plane(plate_dir):
    d = empty("bend")
    d["elements"] = [{"id": "r", "type": "test_runner", "params": {
        "waypoints": [[0, 0, 40], [100, 0, 40], [100, 100, 40]], "bend_radius": 30.0}}]
    r = regenerate(DesignDocument(d, plate_dir))
    assert [e for e in errors(r) if not e[0].startswith("system.")] == []      # a lone runner: open ends are fine here
    L = r.built["r"].derived["length_mm"]
    assert L == pytest.approx(200 - 2 * 30 + math.pi / 2 * 30)            # two straights shortened, a quarter arc
    sec = 0.5 * (20 + 30) * 25
    assert r.built["r"].solid.volume == pytest.approx(sec * (L + 2), rel=1e-3)   # + 1 mm embed at each end
    d["elements"][0]["params"]["bend_radius"] = 150.0                     # does not fit between the points
    assert [e[0] for e in errors(regenerate(DesignDocument(d, plate_dir))) if not e[0].startswith("system.")] == [
        "element.build_failed"]
