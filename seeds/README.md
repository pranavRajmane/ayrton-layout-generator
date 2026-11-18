# Rule-based seed generator

Many varied, valid starting designs for one part. No simulation, no optimisation.

```python
from seeds.voxel import VoxelPart
from seeds.feeding import build_feeding_graph
from seeds.generate import generate_seeds

part = VoxelPart.from_shape(shape_in_mold_frame, h=2.5, part_id="hub")   # parting plane at z = 0
graph = build_feeding_graph(part)
designs = generate_seeds(part, graph, profile_dict, n_designs=50, seed=1, knobs="my_knobs.json")
```

`seeds.build.build_design(design, part_file, profile)` turns a design into a CAD-module document
through the editor's workflow patches and regenerates it.

| file | what |
|---|---|
| `voxel.py` | the part as voxels in the mold frame, the parting plane on a voxel boundary |
| `feeding.py` | geometric feeding graph: zones (watershed on depth), moduli V/A, links, hot spots, feed basins |
| `attach.py` | top patches and parting-plane edge points where a feeder can stand, ranked |
| `sizing.py` | modulus method for feeders and necks, plus the volume check |
| `generate.py` | layout, shared feeders, feeders, gating, the knob draws |
| `validity.py` | manufacturing checks only, each with a reason |
| `report.py` | diversity counts, and a warning when one bucket holds more than 80 % |
| `build.py` | design spec → CAD-module document |
| `knobs.json` | every knob and its range, **all null until the engineer sets them** |
| `config.py` | knob and foundry-profile loading; nothing has a default |

## Casting-rule assumptions (to check)

1. **Zones.** The part splits at the troughs of its distance-to-surface field. Each zone's modulus is
   its volume over the part surface it owns. Faces between zones do not count as cooling area.
2. **Freezing order** follows the modulus (Chvorinov), so moduli are compared and no time is computed.
   `freeze_index` = M².
3. **Hot spot:** a zone whose modulus is at least that of every neighbour. Moduli within 2 % count as
   equal (voxel noise).
4. **Feed basins:** every other zone feeds the hot spot reached by repeatedly stepping to its
   highest-modulus neighbour. A tie splits its volume equally. A feeder's demand is its hot spot's
   basin.
5. **Feeder shape:** an open round cylinder, height = `riser_height_ratio` × d, as the CAD
   `feeder` builds it. A top feeder's base doesn't cool. A side feeder's base does, minus its
   necks.
6. **Sizing:**
   - The feeder's modulus × sleeve MEF must reach `modulus_factor` × the fed zone's modulus (the
     largest zone, if it is shared).
   - Its modulus must exceed the neck's.
   - Volume check: `efficiency × V ≥ shrinkage × (V_fed + V)`, with shrinkage =
     `volumetric_shrinkage_pct`.
   - d is grown in 1 mm steps until all of these hold.
7. **Neck:** a short rectangular bar, modulus `w t / 2(w+t)` = `neck_ratio` × the zone's modulus.
   - It lies wholly in the cope or the drag (the only neck the CAD module builds). It must fit the
     casting edge's height on that side and its full width.
   - Top feeders have no neck: their contact face is the joint.
8. **Top feeders** may overhang their flat face where the casting falls away. They must stay
   `max(riser_to_casting, cutoff_access)` clear of walls that rise above the face.
9. **Feeding distance** = `feeding_distance_factor` × the hot spot's section thickness (twice its core
   depth). It is used to pick nearby places and to decide which pairs can share a feeder (straight-line
   distance).
10. **Shared feeders** are pairs only. Within one casting it is any place within both feeding
    distances. Across neighbouring cavities (grid neighbours) it is a side feeder midway between
    facing edge points, with one neck to each casting.
11. **Prefer the cope:** places on faces at or above the parting plane, and side feeders, rank before
    faces in the drag. Within each group they are ranked by distance.
12. **Gating:**
    - Choke area `W / (ρ t C √(2gH))`, from the existing `calculators.hydraulics`.
    - W = castings + feeders. H = cope height + basin head, taken as parting-line gating
      (`H − P²/2C`).
    - Areas come from the gating ratio. The sprue's top area follows continuity under the basin head.
    - One runner per ingate, from under the sprue. It runs along the casting's edge, the ingate
      leaves its side straight to the edge, and the runner carries on `runner_extension_mm` past it
      (dross trap).
    - The runner's top face is `runner_taper_pct` narrower than its bottom (10–20 %, as the CAD module
      takes it).
    - Ingate area = total / number of ingates. Ingates land on the casting edge on the runner's side.
13. **Ingate placement:** `nearest_thick_section` puts ingates at the edge point of the
    highest-modulus zone, nearest the sprue. `random_valid` picks any point that has a route.
14. **Sprue:** a random free place, `sprue_to_casting_mm` from every casting.
15. **Cavity layout:** a rows × cols grid, `cavity_gap_mm` apart, centred in the flask, all copies
    the same way round.
16. **Pattern shrinkage** only enlarges the cavity in the flask-fit check. Feeders are sized on the
    casting, and the build is at casting size.

## Not covered yet

- **No sleeve or chill element in the CAD module.** `sleeve_probability` must stay [0, 0] for designs
  to build, and no chills are generated.
- **A neck that straddles the parting plane** can't be built. Thick sections split by the plane
  therefore can't be side-fed.
- **Voxel resolution changes the zone count.** The standard model gives 1 hot spot at 4 mm and 2 at
  2.5 mm.
- **Runner routes** are straight lines or one L-shaped corner, with no turn sharper than 90° (the CAD
  sweep fails on hairpins).
