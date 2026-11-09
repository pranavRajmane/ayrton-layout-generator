"""Export a regenerated design to Ayrton; a thin adapter with its own tests.

Ayrton reads a pipeline project (what the occserver web app writes):

    project/geometry/cast.stl        all the metal, one closed surface, mm
    project/geometry/inlet.stl       the pouring opening, a flat patch
    project/materials/transform.json inlet centre / outward normal / area, applied transform
    project/materials/materials.json metal and sand properties (NOT made here)
    project/materials/pour.json      flow rate (written only when given; calculators, Phase 1.3)

To that the bridge adds the region tags the solver does not read yet:

    project/regions/<name>.stl       one closed surface per named solid, overlaps owned downstream
    project/regions/manifest.json    regions, measurement planes (one per port), the pouring port
    design.step                      STEP assembly of the same named solids (mold frame)
    pattern.step                     all the metal fused (mold frame)

As in the pipeline, the project geometry is moved so that the inlet's centre is
the origin; ``translation`` in transform.json and the manifest says by how much.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from build123d import Shape, export_step, export_stl

from core.element import Severity, ValidationIssue
from core.frames import rigid
from core.occ import compound, cut, placed, section_face, solids_volume
from core.regen import RegenResult

MANIFEST_SCHEMA = "ayrton-regions/0.1"
STL_TOL = dict(tolerance=0.02, angular_tolerance=0.1)


class ExportError(RuntimeError):
    def __init__(self, issues: list[ValidationIssue]):
        super().__init__("; ".join(i.message for i in issues))
        self.issues = issues


def _kind(name: str) -> str:
    """part, part.<cavity>, core.<id>, gating.<id>, filter.<id>, feeder.<id>, vent.<id>, mold.flask -> the part
    before the dot (filters are porous to the solver; feeders and vents are metal like the gating)."""
    return name.split(".", 1)[0] if "." in name else name


def _named(shape: Shape, name: str) -> Shape:
    s = Shape.cast(shape.wrapped)                 # a fresh wrapper: never relabel a cached shape
    s.label = name
    return s


def pouring_port(res: RegenResult):
    pours = res.pouring_ports()
    problems = []
    if len(pours) != 1:
        problems.append(ValidationIssue(
            "export.pouring_inlet", Severity.ERROR,
            f"need exactly one inlet that nothing feeds (the pouring point), found {len(pours)}: "
            + ", ".join(f"{i}.{p.id}" for i, p in pours), instances=tuple(i for i, _ in pours)))
        raise ExportError(problems)
    inst, port = pours[0]
    if abs(port.frame.normal[2]) < 0.999:
        raise ExportError([ValidationIssue(
            "export.pouring_not_vertical", Severity.ERROR,
            f"the pouring inlet {inst}.{port.id} must face straight up or down in the pouring position "
            f"(flow normal {np.round(port.frame.normal, 3).tolist()})", instances=(inst,))])
    return inst, port


def _upstream_half_space(res: RegenResult, frame):
    """A box filling everything upstream of a port plane (to trim the open inlet's embed)."""
    from build123d import Align, Box
    bb = res.fused().bounding_box()
    big = 4 * float(np.linalg.norm(np.subtract(tuple(bb.max), tuple(bb.min)))) + 100
    return placed(Box(big, big, big, align=(Align.MAX, Align.CENTER, Align.CENTER)), frame.matrix())


def export(res: RegenResult, out_dir: str | Path, materials_json: str | Path | None = None,
           pour: dict | None = None, allow_errors: bool = False) -> dict:
    """Write the project; returns the manifest.  Refuses a design with errors unless allow_errors."""
    errs = [i for i in res.issues if i.severity == Severity.ERROR]
    if errs and not allow_errors:
        raise ExportError(errs)
    out = Path(out_dir)
    proj = out / "project"
    for sub in ("geometry", "materials", "regions"):
        (proj / sub).mkdir(parents=True, exist_ok=True)

    inst, port = pouring_port(res)
    centre = np.asarray(port.frame.origin)
    shift = rigid(translation=-centre)              # mold -> project
    outward = [-x for x in port.frame.normal]       # the opening faces against the flow

    # Nothing joins the open pouring inlet, so its embed only pokes metal over the inlet plane: trim
    # that stub (the pouring element above the plane), and nothing else; the casting may rise higher.
    stub = res.built[inst].solid & _upstream_half_space(res, port.frame)
    named = res.named_solids()
    pour_name = res.built[inst].tags["solid"]
    metal = res.fused()
    if stub.solids():
        named[pour_name] = cut(named[pour_name], [stub])
        metal = cut(metal, [stub])
    export_step(compound([_named(s, n) for n, s in named.items()], label=res.doc.data["name"] or "design"),
                out / "design.step")
    export_step(_named(metal, "metal"), out / "pattern.step")
    export_stl(placed(metal, shift), proj / "geometry" / "cast.stl", **STL_TOL)
    inlet = placed(section_face(port.section, port.frame), shift)
    export_stl(inlet, proj / "geometry" / "inlet.stl", **STL_TOL)

    regions = []
    for name, s in named.items():
        f = proj / "regions" / f"{name}.stl"
        export_stl(placed(s, shift), f, **STL_TOL)
        b = res.built.get(name.split(".", 1)[1]) if _kind(name) in ("gating", "filter", "feeder", "vent") else None
        regions.append({"name": name, "kind": _kind(name), "file": f"regions/{f.name}",
                        "volume_mm3": solids_volume(s), "instance": b.instance if b else None,
                        "type": b.type if b else None, "type_version": b.element.version if b else None})

    tr = {"inlet": {"centre": centre.tolist(), "normal": outward, "area": port.section.area,
                    "triangles": len(inlet.faces())},
          "alignTo": "+Z", "rotation": np.eye(3).tolist(), "translation": (-centre).tolist(),
          "cad": "design.step", "source": "ayrton-gating export"}
    (proj / "materials" / "transform.json").write_text(json.dumps(tr, indent=2))
    (proj / "materials" / "geometry.json").write_text(json.dumps({"units": "mm", "source": "ayrton-gating export"},
                                                                 indent=2))
    missing = []
    if materials_json:
        shutil.copyfile(materials_json, proj / "materials" / "materials.json")
    else:
        missing.append("materials/materials.json")
    if pour:
        (proj / "materials" / "pour.json").write_text(json.dumps(pour, indent=2))
    else:
        missing.append("materials/pour.json")

    def shifted(plane):
        return {**plane, "origin": (np.asarray(plane["origin"]) - centre).tolist()}

    manifest = {
        "schema": MANIFEST_SCHEMA, "units": "mm", "design": res.doc.data["name"],
        "frame": "project frame = mold frame + translation (Z up, pouring position)",
        "translation": (-centre).tolist(),
        "metal": {"file": "geometry/cast.stl", "volume_mm3": solids_volume(metal)},
        "pouring": {"port": f"{inst}.{port.id}", **shifted(port.frame.to_dict()), "section": port.section.to_dict()},
        "regions": regions,
        "measurement_planes": [shifted(p) for p in res.measurement_planes()],
        "missing": missing,
        "issues": [i.to_dict() for i in res.issues],
    }
    (proj / "regions" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "design.json").write_text(res.doc.to_json())
    return manifest
