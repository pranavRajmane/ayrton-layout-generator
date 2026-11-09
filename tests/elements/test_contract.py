"""Contract compliance: runs on EVERY element in elements/ automatically.
A new element passes these before its own tests mean anything."""
import json
import time
from pathlib import Path

import numpy as np
import pytest

from core.element import (
    FLOW_OUT, Element, Params, Port, PortKind, Section, ValidationContext, ValidationIssue,
)
from core.frames import Frame, is_rigid
from core.registry import REGISTRY, contract_problems

ROOT = Path(__file__).resolve().parents[2]
REGISTRY.discover("elements")
TYPES = REGISTRY.discover("tests.fixture_elements")


@pytest.fixture(params=TYPES)
def el(request):
    return REGISTRY.get(request.param)()


def test_some_elements_found():
    assert TYPES


def test_class_contract(el):
    assert contract_problems(type(el)) == []
    assert type(el).__module__.split(".")[-1] == el.id, "module file name must equal the element id"


def test_spec_sheet_exists(el):
    if not type(el).__module__.startswith("elements."):
        pytest.skip("framework test fixture, not a library element")
    assert (ROOT / "docs" / "elements" / f"{el.id}.md").exists()


def test_describe_is_json(el):
    d = el.describe()
    json.dumps(d)
    assert any(p["name"] == "embed" for p in d["params"])


def test_defaults_resolve_and_bad_values_rejected(el):
    p = el.resolve_params({})
    assert isinstance(p, Params)
    with pytest.raises(ValueError):
        el.resolve_params({"no_such_parameter_xyz": 1})
    for prm in el.all_params():
        if prm.min is not None:
            with pytest.raises(ValueError):
                el.resolve_params({prm.name: prm.min - 1})
        if prm.max is not None:
            with pytest.raises(ValueError):
                el.resolve_params({prm.name: prm.max + 1})


def test_build_one_valid_solid(el):
    p = el.resolve_params({})
    t = time.perf_counter()
    s = el.build(p)
    assert time.perf_counter() - t < 0.5, "build() of the defaults should take well under the 2 s budget"
    assert len(s.solids()) == 1, "build() must return ONE solid"
    assert s.is_valid
    assert s.volume > 0


def test_ports(el):
    p = el.resolve_params({})
    ports = el.ports(p)
    assert ports and all(isinstance(x, Port) for x in ports)
    ids = [x.id for x in ports]
    assert len(ids) == len(set(ids))
    for x in ports:
        assert isinstance(x.kind, PortKind) and isinstance(x.frame, Frame)
        assert is_rigid(x.frame.matrix())
        if x.kind != PortKind.BLIND:
            assert isinstance(x.section, Section) and x.section.area > 0


def test_embed_honoured_at_every_port(el):
    """The solid reaches embed/2 past each port plane: upstream of inlets,
    downstream of outlets."""
    p = el.resolve_params({})
    s = el.build(p)
    assert p.embed > 0
    for x in el.ports(p):
        if x.kind == PortKind.BLIND:
            continue
        sign = 1.0 if x.kind in FLOW_OUT else -1.0
        probe = np.add(x.frame.origin, sign * 0.5 * p.embed * np.asarray(x.frame.normal))
        assert s.is_inside(tuple(probe)), f"port '{x.id}': solid stops short of embed"


def test_taps_on_path_elements(el):
    if not el.is_path:
        pytest.skip("not a path element")
    p = el.resolve_params({})
    for side in ("top", "bottom", "side"):
        assert isinstance(el.tap_frame(p, 0.5, side), Frame)


def test_derived_validate_tags(el):
    p = el.resolve_params({})
    json.dumps(el.derived(p))
    issues = el.validate(p, ValidationContext(instance_id="x_1"))
    assert isinstance(issues, list) and all(isinstance(i, ValidationIssue) for i in issues)
    tags = el.tags("x_1")
    assert tags["solid"] == "gating.x_1"
    port_ids = {x.id for x in el.ports(p)}
    assert set(tags.get("faces", {})) <= port_ids
