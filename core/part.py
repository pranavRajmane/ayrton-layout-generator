"""The casting: read, unit-checked, healed, given its machining stock, scaled
for shrinkage and placed in the mold frame.

The chain, each step on the result of the last:

    source file  --units-->  source shape (mm; face indices refer to this)
                 --machining allowance (thickened faces fused on)-->  as-cast part (weight)
                 --shrinkage scale about the part origin-->  pattern size
                 --part transform-->  mold frame (what the solver and the gating see)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from build123d import Cylinder, Location, Shape, Solid, import_step

from core.element import Severity, ValidationIssue
from core.frames import Frame, rigid
from core.occ import fuse, heal, placed, solids_volume, transformed

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


def load_part(doc, shrinkage_pct: float | None = None) -> PartModel | None:
    """The document's part (and cores) in the mold frame, or None if it has none.
    shrinkage_pct: the preset's value, used when the document leaves it null."""
    pdef = doc.part
    if pdef is None:
        return None
    src = load_source(doc.part_path(), pdef.get("units", "auto"))
    issues = list(src.issues)
    as_cast, mi = add_machining(src.shape, pdef.get("machining", []))
    issues += mi
    shrink = pdef.get("shrinkage_pct")
    if shrink is None:
        shrink = shrinkage_pct
    if shrink is None:
        shrink = 0.0
        issues.append(ValidationIssue("part.shrinkage_unset", Severity.INFO,
                                      "no shrinkage allowance in the document or the preset: pattern at casting size",
                                      instances=("part",)))
    s = 1.0 + shrink / 100.0
    m = doc.part_transform()
    moved = s != 1.0 or not np.allclose(m, np.eye(4))
    shape = transformed(as_cast, m, scale=s) if moved else as_cast
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
        if cv.get("source"):                     # another part file, placed on its own (no machining, no cores)
            try:
                other = load_source((doc.base_dir / cv["source"]).resolve(), cv.get("units", "auto"))
            except Exception as e:                              # noqa: BLE001
                issues.append(ValidationIssue("part.cavity_failed", Severity.ERROR, f"part '{cv['id']}': {e}",
                                              instances=("part",)))
                continue
            issues += [ValidationIssue(**{**i.__dict__, "message": f"{cv['id']}: {i.message}"}) for i in other.issues]
            cavities[cv["id"]] = transformed(other.shape, cm, scale=s)
            cavity_volumes[cv["id"]] = solids_volume(other.shape)
            continue
        cavities[cv["id"]] = transformed(shape, cm)
        cavity_volumes[cv["id"]] = solids_volume(as_cast)
        cores.update({f"{k}.{cv['id']}": transformed(v, cm) for k, v in list(cores.items()) if "." not in k})
    allshapes = [shape, *cavities.values()]
    lo = np.min([_bbox(x)[0] for x in allshapes], axis=0)
    hi = np.max([_bbox(x)[1] for x in allshapes], axis=0)
    return PartModel(doc.part_path(), shape, solids_volume(as_cast), (tuple(lo), tuple(hi)), src.unit, shrink,
                     m @ np.diag([s, s, s, 1.0]), src.shape, ports, cores, issues, cavities, cavity_volumes)
