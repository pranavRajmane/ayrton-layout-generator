"""The design document: part, mold, process, element instances and connections.

The document is plain JSON validated by ``schema/design.v0.<n>.json``.  Every
user edit is a JSON Patch (RFC 6902) applied through ``apply``; the inverse
patch is kept, so undo and redo are diffs too, and a variant is a patch from
its parent.  An edit that would leave the document invalid is refused and the
document is left as it was.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import jsonpatch
import jsonschema
import numpy as np

from core.frames import rigid
from core.units import UNITS

SCHEMA_VERSION = "0.1"
PART = "part"                     # reserved instance id: the casting in connection records
SCHEMA_DIR = Path(__file__).parent / "schema"


class DocumentError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@lru_cache
def schema(version: str = SCHEMA_VERSION) -> dict:
    return json.loads((SCHEMA_DIR / f"design.v{version}.json").read_text())


def empty(name: str = "untitled") -> dict:
    return {"schema_version": SCHEMA_VERSION, "name": name, "units": dict(UNITS), "part": None,
            "mold": {"flask": None}, "process": {"process": None, "alloy": None, "preset": None},
            "elements": [], "connections": [], "variants": []}


def problems(data: dict) -> list[str]:
    """Schema errors, then reference errors (ids unique, connections point at things that exist)."""
    v = jsonschema.Draft202012Validator(schema(data.get("schema_version", SCHEMA_VERSION)))
    out = [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
           for e in sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path))]
    if out:
        return out
    ids = [e["id"] for e in data["elements"]]
    out += [f"element id '{i}' is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    cids = [c["id"] for c in data["connections"]]
    out += [f"connection id '{i}' is used twice" for i in sorted({i for i in cids if cids.count(i) > 1})]
    part_ports = {p["id"] for p in (data["part"] or {}).get("ports", [])}
    for c in data["connections"]:
        for end in ("from", "to"):
            inst = c[end]["instance"]
            if inst == PART:
                if end == "from":
                    out.append(f"connection {c['id']}: the part cannot be upstream")
                elif c["joint"] != "gate" and c[end]["port"] not in part_ports:     # a gate meets the part anywhere
                    out.append(f"connection {c['id']}: part has no port '{c[end]['port']}'")
            elif inst not in ids:
                out.append(f"connection {c['id']}: no element '{inst}'")
        if (c["joint"] in ("part", "gate")) != (c["to"]["instance"] == PART):
            out.append(f"connection {c['id']}: joints 'part' and 'gate' must end on the part and only they may")
    return out


def check(data: dict) -> None:
    p = problems(data)
    if p:
        raise DocumentError(p)


class DesignDocument:
    def __init__(self, data: dict | None = None, base_dir: str | Path | None = None):
        data = copy.deepcopy(data) if data is not None else empty()
        check(data)
        self._data = data
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._undo: list[tuple[list, list]] = []      # (forward, inverse) patches
        self._redo: list[tuple[list, list]] = []

    # ---- I/O ---------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "DesignDocument":
        path = Path(path)
        return cls(json.loads(path.read_text()), base_dir=path.parent)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())

    def to_json(self) -> str:
        return json.dumps(self._data, indent=2)

    @property
    def data(self) -> dict:
        """A copy: change the document only through apply()."""
        return copy.deepcopy(self._data)

    # ---- edits as diffs ----------------------------------------------------
    def apply(self, patch: list[dict]) -> None:
        new = jsonpatch.apply_patch(self._data, patch, in_place=False)
        check(new)
        inverse = jsonpatch.make_patch(new, self._data).patch
        self._undo.append((list(patch), inverse))
        self._redo.clear()
        self._data = new

    def merge_last(self) -> None:
        """The last two changes become one undo step (a change and what it brought about)."""
        if len(self._undo) < 2:
            return
        f2, i2 = self._undo.pop()
        f1, i1 = self._undo.pop()
        self._undo.append((f1 + f2, i2 + i1))

    def undo(self) -> bool:
        if not self._undo:
            return False
        fwd, inv = self._undo.pop()
        self._data = jsonpatch.apply_patch(self._data, inv, in_place=False)
        self._redo.append((fwd, inv))
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        fwd, inv = self._redo.pop()
        self._data = jsonpatch.apply_patch(self._data, fwd, in_place=False)
        self._undo.append((fwd, inv))
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def set_param(self, instance: str, name: str, value) -> None:
        i = self._index(instance)
        op = "replace" if name in self._data["elements"][i]["params"] else "add"
        self.apply([{"op": op, "path": f"/elements/{i}/params/{name}", "value": value}])

    # ---- reading -----------------------------------------------------------
    def _index(self, instance: str) -> int:
        for i, e in enumerate(self._data["elements"]):
            if e["id"] == instance:
                return i
        raise KeyError(instance)

    def element(self, instance: str) -> dict:
        return copy.deepcopy(self._data["elements"][self._index(instance)])

    @property
    def elements(self) -> list[dict]:
        return copy.deepcopy(self._data["elements"])

    @property
    def connections(self) -> list[dict]:
        return copy.deepcopy(self._data["connections"])

    @property
    def part(self) -> dict | None:
        return copy.deepcopy(self._data["part"])

    def part_path(self) -> Path | None:
        p = self._data["part"]
        return (self.base_dir / p["source"]).resolve() if p else None

    def part_transform(self) -> np.ndarray:
        """Part frame -> mold frame."""
        t = (self._data["part"] or {}).get("transform") or {}
        return rigid(t.get("rotation"), t.get("translation", (0.0, 0.0, 0.0)))
