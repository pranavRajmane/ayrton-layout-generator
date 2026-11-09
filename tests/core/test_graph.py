import pytest

from core.graph import FlowGraph, GraphError


def els(*ids):
    return [{"id": i, "type": "dummy_box", "params": {}} for i in ids]


def con(cid, a, ap, b, bp, joint="butt"):
    return {"id": cid, "from": {"instance": a, "port": ap}, "to": {"instance": b, "port": bp}, "joint": joint}


def test_order_and_paths():
    g = FlowGraph(els("ingate", "sprue", "runner"), [
        con("c1", "sprue", "outlet", "runner", "inlet"),
        con("c2", "runner", "outlet", "ingate", "inlet"),
        con("c3", "ingate", "outlet", "part", "f1", "part")])
    assert g.order() == ["sprue", "runner", "ingate"]
    assert g.sources() == ["sprue"]
    assert [[c.id for c in p] for p in g.paths()] == [["c1", "c2", "c3"]]


def test_loop_rejected():
    g = FlowGraph(els("a", "b"), [con("c1", "a", "outlet", "b", "inlet"), con("c2", "b", "outlet", "a", "inlet")])
    with pytest.raises(GraphError) as e:
        g.order()
    assert set(e.value.instances) == {"a", "b"}


def test_port_used_twice_rejected():
    with pytest.raises(GraphError):
        FlowGraph(els("a", "b", "c"), [con("c1", "a", "outlet", "b", "inlet"), con("c2", "a", "outlet", "c", "inlet")])


def test_taps_at_different_stations_are_distinct():
    g = FlowGraph(els("r", "g1", "g2"), [
        {"id": "c1", "from": {"instance": "r", "port": "tap", "station": 0.3, "side": "side"},
         "to": {"instance": "g1", "port": "inlet"}, "joint": "branch"},
        {"id": "c2", "from": {"instance": "r", "port": "tap", "station": 0.7, "side": "side"},
         "to": {"instance": "g2", "port": "inlet"}, "joint": "branch"}])
    assert g.order() == ["r", "g1", "g2"] and len(g.paths()) == 2
