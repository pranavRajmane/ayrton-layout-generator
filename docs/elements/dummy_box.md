# Element: Dummy box            id: dummy_box     version: 0.1

## Purpose
A rectangular bar that proves the framework end to end (registry, ports, placement, regeneration, export). Not a foundry element.

## Applicable processes / alloys
- any; it carries no foundry rules

## Parameters
| name | symbol | unit | default | min | max | driven by calculator? |
|------|--------|------|---------|-----|-----|-----------------------|
| length | L | mm | 100 | 1 | 2000 | no |
| width | w | mm | 20 | 1 | 500 | no |
| height | h | mm | 20 | 1 | 500 | no |
| inlet_policy | – | – | own | own, follows | | no |
| embed (framework) | e | mm | 1 | 0 | 10 | no |

## Derived values (computed, not edited)
| name | formula | unit |
|------|---------|------|
| volume_mm3 | L·w·h (without embed) | mm³ |
| section_area_mm2 | w·h | mm² |

## Ports
| port | type | cross-section at port | position rule |
|------|------|-----------------------|---------------|
| inlet | inlet | rectangle w × h | local origin, flow +X, up +Z; policy from inlet_policy |
| outlet | outlet | rectangle w × h | (L, 0, 0), flow +X, up +Z; policy own |

## Geometry construction (in element local frame)
1. Box of (L + 2e) × w × h, from x = −e to x = L + e, centred on the X axis.

## Validation rules
| rule id | condition | severity | message |
|---------|-----------|----------|---------|
| dummy_box.embed_vs_length | e > L/4 | warning | embed is more than a quarter of the length |

## Region tags for the solver
- solid tag: gating.<instance id>
- named faces: inlet, outlet

## Test cases
| case | inputs | expected volume (mm³) | expected port area (mm²) |
|------|--------|-----------------------|--------------------------|
| default | L 100, w 20, h 20, e 1 | 40 800 | 400 |
| thin | L 50, w 10, h 30, e 2 | 16 200 | 300 |
