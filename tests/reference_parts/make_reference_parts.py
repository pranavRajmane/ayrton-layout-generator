"""Build the three reference parts, their designs and the expected outputs.

    python tests/reference_parts/make_reference_parts.py

Each folder gets <part>.step (and a core), design.json (a dummy-box sprue
standing on a picked face) and expected.json.  Expected volumes are worked out
by hand here, not taken from the code under test.  These are stand-ins: swap
in real customer parts when they are cleared for the repo.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from build123d import Box, Cylinder, Location, Pos, Rot, export_step

HERE = Path(__file__).parent
SPRUE = {"length": 80.0, "width": 20.0, "height": 20.0}
SPRUE_V = 80 * 400.0                    # after the bridge trims the open inlet's embed
PI = math.pi


def design(name, source, port_origin, cores=None):
    return {
        "schema_version": "0.1", "name": name,
        "units": {"length": "mm", "mass": "kg", "time": "s", "temperature": "degC"},
        "part": {"source": source,
                 "ports": [{"id": "gate_face", "frame": {"origin": port_origin, "normal": [0, 0, -1], "up": [1, 0, 0]}}],
                 **({"cores": cores} if cores else {})},
        "mold": {"flask": None}, "process": {"process": None, "alloy": None, "preset": None},
        "elements": [{"id": "sprue_1", "type": "dummy_box", "type_version": "0.1", "params": dict(SPRUE)}],
        "connections": [{"id": "c1", "from": {"instance": "sprue_1", "port": "outlet"},
                         "to": {"instance": "part", "port": "gate_face"}, "joint": "part",
                         "overlap_mm": None, "transition": None, "measure_plane": True}],
        "variants": []}


def write(folder, part, part_name, d, expected, core=None):
    f = HERE / folder
    f.mkdir(exist_ok=True)
    export_step(part, f / part_name)
    if core is not None:
        export_step(core[1], f / core[0])
    (f / "design.json").write_text(json.dumps(d, indent=2))
    (f / "expected.json").write_text(json.dumps(expected, indent=2))
    print(folder, round(part.volume, 1), "expected", round(expected["part_volume_mm3"], 1))


def plate():
    part = Box(200, 150, 20)
    v = 200 * 150 * 20
    write("plate", part, "plate.step", design("Plate", "plate.step", [0, 0, 10]), {
        "part_volume_mm3": v, "part_bbox": [[-100, -75, -10], [100, 75, 10]],
        "regions": ["part", "gating.sprue_1"], "metal_volume_mm3": v + SPRUE_V,
        "inlet_centre": [0, 0, 90], "measurement_planes": ["sprue_1.inlet", "sprue_1.outlet"]})


def flanged_housing():
    # flange OD 200 x 15 with six 14 mm bolt holes on a 170 PCD; tube OD 120 / bore 80 to z = 100
    flange = Cylinder(100, 15).moved(Location((0, 0, 7.5)))
    for k in range(6):
        a = math.radians(60 * k)
        flange = flange - Cylinder(7, 15).moved(Location((85 * math.cos(a), 85 * math.sin(a), 7.5)))
    tube = Cylinder(60, 100).moved(Location((0, 0, 50)))
    part = (flange + tube) - Cylinder(40, 100).moved(Location((0, 0, 50)))
    v = (PI / 4 * (200 ** 2 - 80 ** 2) * 15 - 6 * PI / 4 * 14 ** 2 * 15 + PI / 4 * (120 ** 2 - 80 ** 2) * 85)
    a = math.radians(30)                                     # between two bolt holes, on the flange top
    origin = [80 * math.cos(a), 80 * math.sin(a), 15.0]
    write("flanged_housing", part, "flanged_housing.step", design("Flanged housing", "flanged_housing.step", origin), {
        "part_volume_mm3": v, "part_bbox": [[-100, -100, 0], [100, 100, 100]],
        "regions": ["part", "gating.sprue_1"], "metal_volume_mm3": v + SPRUE_V,
        "inlet_centre": [origin[0], origin[1], 95.0], "measurement_planes": ["sprue_1.inlet", "sprue_1.outlet"]})


def cored_part():
    # block 160 x 100 x 60 with a 40 mm through bore along X; the core fills it and runs 20 mm into each print
    bore = Rot(0, 90, 0) * Cylinder(20, 160)
    part = Box(160, 100, 60) - bore
    core = Rot(0, 90, 0) * Cylinder(20, 200)
    v = 160 * 100 * 60 - PI * 20 ** 2 * 160
    d = design("Cored block", "cored_part.step", [40, 0, 30],
               cores=[{"id": "bore", "source": "bore_core.step"}])
    write("cored_part", part, "cored_part.step", d, {
        "part_volume_mm3": v, "part_bbox": [[-80, -50, -30], [80, 50, 30]],
        "regions": ["part", "core.bore", "gating.sprue_1"], "metal_volume_mm3": v + SPRUE_V,
        "core_volume_mm3": PI * 20 ** 2 * 200,
        "inlet_centre": [40, 0, 110], "measurement_planes": ["sprue_1.inlet", "sprue_1.outlet"]},
        core=("bore_core.step", core))


if __name__ == "__main__":
    plate()
    flanged_housing()
    cored_part()
