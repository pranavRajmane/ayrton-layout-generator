# Element: Pouring cup         id: pouring_cup        version: 0.1

## Purpose
A conical cup cut into the top of the cope over the sprue; poured into at its open top.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. Geometry only: no foundry rules.

## Parameters
| name | symbol | unit | default / source |
|------|--------|------|------------------|
| d_top | D1 | mm | 80 (typed) |
| d_bottom | D2 | mm | the sprue's top diameter (kept equal) |
| height | h | mm | 60 (typed) |
| embed (framework) | e | mm | 1 |

## Ports
| port | type | cross-section | position |
|------|------|---------------|----------|
| inlet | inlet (pouring) | round D1 | the open top, on the cope top |
| outlet | outlet | round D2 | h down, onto the sprue (butt) |

## Geometry
Cone along local X from −e to h + e (taper carried on), placed with its top on the box top over the sprue.

## Region tags
- solid: gating.<id>
