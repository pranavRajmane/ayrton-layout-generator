"""The casting: read, unit-checked, healed, given its machining stock, scaled
for shrinkage and placed in the mold frame.

The chain, each step on the result of the last:

    source file  --units-->  source shape (mm; face indices refer to this)
                 --machining allowance (thickened faces fused on)-->  as-cast part (weight)
                 --shrinkage scale about the part origin-->  pattern size
                 --part transform-->  mold frame (what the solver and the gating see)
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from build123d import Cylinder, Location, Shape, Solid, import_step

from core.element import Severity, ValidationIssue
from core.frames import Frame, rigid
from core.occ import fuse, heal, placed, solids_volume, transformed
from core.partmesh import PartMesh, Placed
from core.partmesh import bbox as mesh_bbox
from core.partmesh import placed as place_mesh

UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4}
_STEP_PREFIX = {".MILLI.": "mm", ".CENTI.": "cm", "$": "m"}


class PartError(ValueError):
    pass


def step_length_unit(path: Path) -> str | None:
    """The length unit a STEP file declares (OCCT converts it to mm on reading)."""
    head = path.read_bytes()[:2_000_000].decode("latin-1", "replace")
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", head, re.I):
        return "inch"
    m = re.search(r"SI_UNIT\s*\(\s*([.$A-Z]+)\s*,\s*\.METRE\.\s*\)", head)
    return _STEP_PREFIX.get(m.group(1)) if m else None


def read_shape(path: str | Path) -> Shape:
    path = Path(path)
    if not path.exists():
        raise PartError(f"part file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".step", ".stp"):
        shape = import_step(path)
    elif suffix == ".stl":
        shape = read_stl_solid(path)
    else:
        raise PartError(f"unsupported part format '{suffix}' (STEP or STL)")
    if not shape.solids():
        raise PartError(f"{path.name} holds no solid")
    return shape


def read_stl_solid(path: Path) -> Shape:
    """An STL as a B-rep solid: one planar face per triangle, sewn and closed."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.StlAPI import StlAPI_Reader
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Shape

    raw = TopoDS_Shape()
    if not StlAPI_Reader().Read(raw, str(path)):
        raise PartError(f"cannot read STL {path}")
    sew = BRepBuilderAPI_Sewing(1e-6)
    sew.Add(raw)
    sew.Perform()
    ex = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    if not ex.More():
        raise PartError(f"{path.name}: the triangles do not form a closed shell")
    solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(ex.Current())).Solid()
    return Shape.cast(solid)


@dataclass
class SourcePart:
    """A file read once: the shape in mm, and what was found on the way."""
    shape: Shape
    unit: str | None              # declared by the file (STEP) or None
    issues: list[ValidationIssue]


_CACHE: dict[tuple, SourcePart] = {}
_CACHE_FILES = 16                  # part files (or versions of one) kept read


def load_source(path: Path, units: str = "auto") -> SourcePart:
    key = (str(path), path.stat().st_mtime_ns, units)
    if key in _CACHE:
        return _CACHE[key]
    shape = read_shape(path)
    issues: list[ValidationIssue] = []
    stl = path.suffix.lower() == ".stl"
    declared = None if stl else step_length_unit(path)
    if units != "auto":
        # STEP: OCCT already converted from the declared unit, so an override says the file is wrong.
        factor = UNIT_MM[units] / (1.0 if stl else UNIT_MM.get(declared or "mm", 1.0))
        if factor != 1.0:
            shape = transformed(shape, np.eye(4), scale=factor)
        issues.append(ValidationIssue("part.units", Severity.INFO,
                                      f"{path.name} read as {units}" +
                                      (f" (the file declares {declared})" if declared else ""),
                                      instances=("part",)))
    elif stl:
        bb = shape.bounding_box()
        ext = float(np.subtract(tuple(bb.max), tuple(bb.min)).max())
        if ext < 5.0 or ext > 20000.0:
            issues.append(ValidationIssue("part.units_suspect", Severity.WARNING,
                                          f"{path.name} is {ext:.4g} mm across: are the STL's units mm? "
                                          "Set the part units.", value=ext, instances=("part",)))
    shape, changed = heal(shape)
    if changed:
        issues.append(ValidationIssue("part.healed", Severity.INFO, f"{path.name} was repaired by ShapeFix",
                                      instances=("part",)))
    if not shape.is_valid:
        issues.append(ValidationIssue("part.invalid", Severity.WARNING,
                                      f"{path.name} is not a valid solid after repair; booleans may fail",
                                      instances=("part",)))
    if len(shape.solids()) > 1:
        issues.append(ValidationIssue("part.many_solids", Severity.WARNING,
                                      f"{path.name} holds {len(shape.solids())} solids", instances=("part",)))
    _CACHE[key] = SourcePart(shape, declared, issues)
    while len(_CACHE) > _CACHE_FILES:                     # oldest first: a part file read long ago
        del _CACHE[next(iter(_CACHE))]
    return _CACHE[key]


def add_machining(shape: Shape, groups: list[dict]) -> tuple[Shape, list[ValidationIssue]]:
    """Fuse stock onto the listed faces (indices into shape.faces()), each
    thickened outward by its allowance."""
    if not groups:
        return shape, []
    faces = shape.faces()
    stock, issues = [], []
    for g in groups:
        for i in g["faces"]:
            if i >= len(faces):
                issues.append(ValidationIssue("part.machining_face", Severity.ERROR,
                                              f"machining group '{g['id']}': the part has no face {i}",
                                              value=i, limit=len(faces) - 1, instances=("part",)))
                continue
            try:
                stock.append(Solid.thicken(faces[i], g["allowance_mm"]))
            except Exception as e:                              # noqa: BLE001
                issues.append(ValidationIssue("part.machining_failed", Severity.ERROR,
                                              f"machining group '{g['id']}': face {i} could not be thickened ({e})",
                                              instances=("part",)))
    if not stock:
        return shape, issues
    return fuse([shape] + stock), issues


def core_shape(c: dict, base_dir: Path) -> tuple[Shape, list[ValidationIssue]]:
    """A core in the part frame: from its file, or a cylinder (prints included)."""
    if "primitive" in c:
        pr = c["primitive"]
        f = Frame.from_dict(pr["frame"])
        # build123d's cylinder runs along Z; turn it onto local X, which the frame takes to its normal
        cyl = Cylinder(pr["d"] / 2, pr["length"]).moved(Location((0, 0, 0), (0, 1, 0), 90))
        return placed(cyl, f.matrix()), []
    src = load_source((base_dir / c["source"]).resolve())
    t = c.get("transform") or {}
    m = rigid(t.get("rotation"), t.get("translation", (0, 0, 0)))
    return (src.shape if np.allclose(m, np.eye(4)) else placed(src.shape, m)), list(src.issues)


@dataclass
class PartModel:
    path: Path
    shape: Shape                       # in the mold frame, at pattern size
    volume_mm3: float                  # as cast: with machining stock, without the shrinkage scale
    bbox: tuple[tuple[float, float, float], tuple[float, float, float]]
    source_unit: str | None = None
    shrinkage_pct: float = 0.0
    to_mold: np.ndarray = field(default_factory=lambda: np.eye(4))   # source (mm) -> mold, incl. shrink scale
    source: Shape | None = None        # source shape in mm (face indices refer to it)
    ports: dict[str, Frame] = field(default_factory=dict)
    cores: dict[str, Shape] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)
    cavities: dict[str, Shape] = field(default_factory=dict)    # more parts: copies, or other files
    cavity_volumes: dict[str, float] = field(default_factory=dict)   # as cast, mm3
    # per solid ("part", "part.<cavity id>"): the shape it is placed from (at pattern size, read and
    # meshed once per file) with its key, and its mesh where the mold has it
    bases: dict[str, tuple[str, Shape]] = field(default_factory=dict)
    placed: dict[str, Placed] = field(default_factory=dict)
    key: str = ""                      # changes whenever any of the part's solids changes or moves

    def bbox_of(self, name: str):
        """A solid's bounding box ("part", "part.<cavity id>"), from its mesh: an exact box takes
        seconds on a large part."""
        return self.placed[name].bbox() if name in self.placed else _bbox(self.cavities.get(name[5:], self.shape))

    def metal_placed(self) -> list[Placed]:
        """Every cavity's mesh where the mold has it (for fast queries; see core.partmesh)."""
        return list(self.placed.values())

    @property
    def metal(self) -> Shape:
        """Every cavity of the part as one shape (the main one alone when there are no others)."""
        if not self.cavities:
            return self.shape
        from build123d import Compound
        return Compound(children=[self.shape, *self.cavities.values()])


def _bbox(shape: Shape):
    b = shape.bounding_box()
    return (tuple(b.min), tuple(b.max))


@dataclass
class _Base:
    key: str
    shape: Shape                       # as cast (machining on), scaled for shrinkage, not placed
    volume_mm3: float                  # as cast, unscaled
    issues: list[ValidationIssue]
    mesh: PartMesh


_BASES: dict[tuple, _Base] = {}


def _base(path: Path, units: str, machining: list, scale: float) -> _Base:
    """A part file made ready to place, kept: reading, healing, machining stock, the volume, the scale
    and the mesh are the same for every placement (on a large part each takes seconds)."""
    src = load_source(path, units)
    k = (str(path), path.stat().st_mtime_ns, units, json.dumps(machining, sort_keys=True), round(scale, 12))
    b = _BASES.get(k)
    if b is None:
        as_cast, mi = add_machining(src.shape, machining)
        shape = transformed(as_cast, np.eye(4), scale=scale) if scale != 1.0 else as_cast
        b = _BASES[k] = _Base(hashlib.sha1(repr(k).encode()).hexdigest(), shape, solids_volume(as_cast), mi,
                              PartMesh(shape))
        while len(_BASES) > _CACHE_FILES:
            del _BASES[next(iter(_BASES))]
    return b


def load_part(doc, shrinkage_pct: float | None = None) -> PartModel | None:
    """The document's part (and cores) in the mold frame, or None if it has none.
    shrinkage_pct: the preset's value, used when the document leaves it null."""
    pdef = doc.part
    if pdef is None:
        return None
    src = load_source(doc.part_path(), pdef.get("units", "auto"))
    issues = list(src.issues)
    shrink = pdef.get("shrinkage_pct")
    if shrink is None:
        shrink = shrinkage_pct
    if shrink is None:
        shrink = 0.0
        issues.append(ValidationIssue("part.shrinkage_unset", Severity.INFO,
                                      "no shrinkage allowance in the document or the preset: pattern at casting size",
                                      instances=("part",)))
    s = 1.0 + shrink / 100.0
    main = _base(doc.part_path(), pdef.get("units", "auto"), pdef.get("machining", []), s)
    issues += main.issues
    m = doc.part_transform()
    moved = s != 1.0 or not np.allclose(m, np.eye(4))
    shape = transformed(main.shape, m) if not np.allclose(m, np.eye(4)) else main.shape
    bases = {"part": (main.key, main.shape)}
    meshes = {"part": place_mesh(main.mesh, m)}
    ports = {p["id"]: Frame.from_dict(p["frame"]) for p in pdef.get("ports", [])}
    cores = {}
    for c in pdef.get("cores", []):
        try:
            cs, ci = core_shape(c, doc.base_dir)
        except Exception as e:                                  # noqa: BLE001
            issues.append(ValidationIssue("part.core_failed", Severity.ERROR, f"core '{c['id']}': {e}",
                                          instances=("part",)))
            continue
        cores[c["id"]] = transformed(cs, m, scale=s) if moved else cs
        issues += ci
    cavities, cavity_volumes = {}, {}
    for cv in pdef.get("cavities", []):
        t = cv["transform"]
        cm = rigid(t.get("rotation"), t.get("translation", (0, 0, 0)))
        name = f"part.{cv['id']}"
        if cv.get("source"):                     # another part file, placed on its own (no machining, no cores)
            try:
                other_path = (doc.base_dir / cv["source"]).resolve()
                other = load_source(other_path, cv.get("units", "auto"))
                ob = _base(other_path, cv.get("units", "auto"), [], s)
            except Exception as e:                              # noqa: BLE001
                issues.append(ValidationIssue("part.cavity_failed", Severity.ERROR, f"part '{cv['id']}': {e}",
                                              instances=("part",)))
                continue
            issues += [ValidationIssue(**{**i.__dict__, "message": f"{cv['id']}: {i.message}"}) for i in other.issues]
            cavities[cv["id"]] = transformed(ob.shape, cm)
            cavity_volumes[cv["id"]] = ob.volume_mm3
            bases[name] = (ob.key, ob.shape)
            meshes[name] = place_mesh(ob.mesh, cm)
            continue
        cavities[cv["id"]] = transformed(shape, cm)
        cavity_volumes[cv["id"]] = main.volume_mm3
        bases[name] = (main.key, main.shape)
        meshes[name] = place_mesh(main.mesh, cm @ m)
        cores.update({f"{k}.{cv['id']}": transformed(v, cm) for k, v in list(cores.items()) if "." not in k})
    lo, hi = mesh_bbox(list(meshes.values()))
    key = hashlib.sha1(repr(([(n, b[0]) for n, b in bases.items()],
                             [np.round(p.m, 9).tobytes() for p in meshes.values()],
                             json.dumps(pdef.get("cores", []), sort_keys=True))).encode()).hexdigest()
    return PartModel(doc.part_path(), shape, main.volume_mm3, (lo, hi), src.unit, shrink,
                     m @ np.diag([s, s, s, 1.0]), src.shape, ports, cores, issues, cavities, cavity_volumes,
                     bases, meshes, key)
