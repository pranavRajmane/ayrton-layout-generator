"""Synthetic parts, knob ranges and a foundry profile for the seed-generator tests.

The numbers here are TEST values that make the synthetic parts work; they are not foundry data and
must not be copied into seeds/knobs.json or a customer profile."""
import copy

PARTS = {
    # one thick block, split by the parting plane
    "block": [(-30, -30, -30, 30, 30, 30)],
    # two thick blocks joined by a thin plate
    "two_blocks": [(-90, -30, -30, -30, 30, 30), (30, -30, -30, 90, 30, 30), (-30, -30, -6, 30, 30, 6)],
    # a slab wholly in the cope, its bottom face on the parting plane (a side neck can be as thick as it)
    "slab": [(-30, -30, 0, 30, 30, 30)],
    # two thick bosses close together on a thin plate (the plate in the drag, its top on the parting plane)
    "bosses": [(-80, -50, -20, 80, 50, 0), (-40, -15, 0, -10, 15, 40), (10, -15, 0, 40, 15, 40)],
}

KNOBS = {
    "modulus_factor": {"range": [1.1, 1.3]},
    "feeding_efficiency": {"unsleeved": {"range": [0.12, 0.16]}, "sleeved": {"range": [0.3, 0.4]}},
    "neck_ratio": {"range": [1.0, 1.15]},
    "riser_style": {"choices": ["top", "side", "auto"]},
    "attachment_choice": {"choices": ["nearest", "random_top_k"]},
    "attachment_top_k": {"range": [1, 4], "int": True},
    "merge_shared_risers": {"range": [0.0, 1.0]},
    "feeding_distance_factor": {"range": [2.0, 4.5]},
    "ingate_placement": {"choices": ["nearest_thick_section", "random_valid"]},
    "filling_time_s": {"range": [4.0, 10.0]},
    "riser_height_ratio": {"range": [1.0, 1.5]},
    "sleeve_probability": {"range": [0.0, 0.0]},
    "sleeve_mef": {"range": [1.2, 1.4]},
    "neck_aspect": {"range": [1.5, 3.0]},
    "neck_length_mm": {"range": [8.0, 15.0]},
    "ingates_per_cavity": {"range": [1, 2], "int": True},
    "ingate_aspect": {"range": [3.0, 5.0]},
    "ingate_length_mm": {"range": [15.0, 25.0]},
    "runner_side": {"choices": ["drag", "cope"]},
    "runner_height_ratio": {"range": [1.0, 1.4]},
    "runner_taper_pct": {"range": [10.0, 20.0]},
    "runner_extension_mm": {"range": [20.0, 60.0]},
    "cavity_gap_mm": {"range": [60.0, 120.0]},
}

PROFILE = {
    "process": "green_sand", "alloy": "grey_iron",
    "flask_size_mm": [600.0, 450.0, 300.0], "parting_height_mm": 150.0,
    "cavity_count": 1, "gating_ratio": [1.0, 1.2, 1.4],
    "density_kg_m3": 7000.0, "discharge_coefficient": 0.8,
    "volumetric_shrinkage_pct": 4.0, "pattern_shrinkage_pct": 1.0,
    "pouring_temp_c": [1350.0, 1400.0], "basin_head_mm": 40.0,
    "clearances": {"flask_wall_mm": 25.0, "flask_top_mm": 15.0, "flask_bottom_mm": 25.0,
                   "riser_spacing_mm": 10.0, "riser_to_casting_mm": 8.0, "sprue_to_casting_mm": 30.0,
                   "cutoff_access_mm": 5.0, "cavity_spacing_mm": 40.0},
}


def knobs(**over):
    k = copy.deepcopy(KNOBS)
    for name, spec in over.items():
        k[name] = spec
    return k


def profile(**over):
    p = copy.deepcopy(PROFILE)
    p.update(over)
    return p


def solid(name):
    """The synthetic part as an OCCT solid (mold frame)."""
    from build123d import Box, Location
    out = None
    for x0, y0, z0, x1, y1, z1 in PARTS[name]:
        b = Box(x1 - x0, y1 - y0, z1 - z0).moved(Location(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)))
        out = b if out is None else out.fuse(b)
    return out.clean()


def write_step(name, folder):
    from pathlib import Path
    from build123d import export_step
    p = Path(folder) / f"{name}.step"
    export_step(solid(name), str(p))
    return p
