# Element: Sprue                id: sprue         version: 0.1

## Purpose
The down-sprue: a tapered round channel straight down from the top of the cope to the runner it drops into.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. No foundry rules yet (sizes are typed or driven by the calculators).

## Parameters
| name | symbol | unit | default | min | max | driven by calculator? |
|------|--------|------|---------|-----|-----|-----------------------|
| d_top | D1 | mm | 30 (typed when placed) | 1 | 500 | no |
| d_bottom | D2 | mm | 25 (typed when placed) | 1 | 500 | yes: choke area |
| length | L | mm | 200 (set by the box and the runner) | 1 | 5000 | no |
| embed (framework) | e | mm | 1 | 0 | 10 | no |

## Derived values
| name | formula | unit |
|------|---------|------|
| volume_mm3 | πL(r1² + r1·r2 + r2²)/3 | mm³ |
| top_area_mm2 / bottom_area_mm2 | πr² | mm² |

## Ports
| port | type | cross-section | position rule |
|------|------|---------------|---------------|
| inlet | inlet (pouring) | round D1 | local origin, flow +X |
| outlet | outlet | round D2 | (L, 0, 0), flow +X; joins a runner with a drop joint |

## Geometry
1. Cone along local X from −e to L + e, the taper carried on across both embeds.
Placed by its inlet on the cope top, flow straight down.

## Validation rules
| rule id | condition | severity | message |
|---------|-----------|----------|---------|
| sprue.taper | radius ≤ 0 at L + e | error | the taper closes the sprue before its bottom |

## Region tags
- solid: gating.<id>; faces: inlet = pouring, outlet = sprue_base
