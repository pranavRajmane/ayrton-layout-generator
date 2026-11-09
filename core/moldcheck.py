"""Draft and undercut against the parting plane (Z = 0 of the mold frame).

Each half of the mold is pulled away from the plane: the cope (z > 0) up, the
drag (z < 0) down.  Over samples on every face of the part (the face's own
triangulation, normals taken from the surface):

  draft    = asin(n . pull) in degrees: 90 on a face looking along the pull,
             0 on a wall square to the plane, negative on a face looking back
             toward the plane;
  undercut = a ray from the sample along the pull hits the part again (sand
             there cannot leave with its half).

Samples inside a core are skipped: the core, not the mold half, forms them.
The smallest acceptable draft is the engineer's number (mold.draft_min_deg);
without it only undercuts are reported.
"""
from __future__ import annotations

import numpy as np
from build123d import Vector
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

from core.element import Severity, ValidationIssue
from core.part import PartModel
from core.pick import face_mesh

EPS = 1e-3          # mm: ignore samples on the plane; start rays this far along the pull
OFFSET = 1e-2       # mm: and this far out of the face, so a ray beside a wall does not graze it
MAX_SAMPLES = 60    # per face


def _samples(face, tol: float):
    verts, tris = face.tessellate(tol, 0.3)
    pts = np.array([[v.X, v.Y, v.Z] for v in verts])
    cent = pts[np.asarray(tris)].mean(axis=1)
    if len(cent) > MAX_SAMPLES:
        cent = cent[np.linspace(0, len(cent) - 1, MAX_SAMPLES).astype(int)]
    return cent


def check(pm: PartModel, min_draft_deg: float | None = None, tol: float = 0.5) -> dict:
    shape = pm.shape                                       # mold frame
    hit = IntCurvesFace_ShapeIntersector()
    hit.Load(shape.wrapped, 1e-6)
    cores = list(pm.cores.values())
    faces_out, issues = [], []
    for i, f in enumerate(shape.faces()):
        drafts, under, sides = [], 0, set()
        for c in _samples(f, tol):
            z = c[2]
            if abs(z) < EPS:
                continue
            if any(core.is_inside(Vector(*c), tolerance=1e-3) for core in cores):
                continue
            pull = 1.0 if z > 0 else -1.0
            sides.add("cope" if z > 0 else "drag")
            n = f.normal_at(Vector(*c))
            drafts.append(float(np.degrees(np.arcsin(np.clip(n.Z * pull, -1, 1)))))
            o = c + OFFSET * np.array([n.X, n.Y, n.Z]) + (0, 0, pull * EPS)     # start in the sand, off the face
            hit.Perform(gp_Lin(gp_Pnt(*o), gp_Dir(0, 0, pull)), 0.0, 1e9)
            under += hit.NbPnt() > 0
        if not drafts:
            continue
        rec = {"face": i, "kind": f.geom_type.name.lower(), "area_mm2": f.area, "side": "/".join(sorted(sides)),
               "min_draft_deg": round(min(drafts), 3), "undercut_fraction": round(under / len(drafts), 3)}
        flagged = False
        if under:
            flagged = True
            issues.append(ValidationIssue("mold.undercut", Severity.WARNING,
                                          f"face {i} ({rec['kind']}, {rec['side']}) is undercut on "
                                          f"{100 * rec['undercut_fraction']:.0f}% of its area",
                                          value=rec["undercut_fraction"], limit=0.0, instances=("part",)))
        elif min_draft_deg is not None and rec["min_draft_deg"] < min_draft_deg - 1e-9:
            flagged = True
            issues.append(ValidationIssue("mold.draft", Severity.WARNING,
                                          f"face {i} ({rec['kind']}, {rec['side']}) has {rec['min_draft_deg']:.2f} deg "
                                          f"of draft", value=rec["min_draft_deg"], limit=min_draft_deg,
                                          instances=("part",)))
        if flagged:
            rec["mesh"] = face_mesh(f, np.eye(4))
        faces_out.append(rec)
    return {"faces": faces_out, "issues": issues, "min_draft_deg": min_draft_deg}
