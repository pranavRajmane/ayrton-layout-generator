# Element: Vent                id: vent        version: 0.1

## Purpose
A round vent from the part's top straight up through the cope to the top of the box.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. Geometry only: no foundry rules.

## Parameters
| name | symbol | unit | default / source |
|------|--------|------|------------------|
| d | d | mm | 10 (typed) |
| length | L | mm | set: part to box top |
| embed (framework) | e | mm | 1 |

## Ports
| port | type | cross-section | position |
|------|------|---------------|----------|
| base | part face | round d | on the part |
| top | blind | round d | the box top |

## Geometry
Cylinder along local X from −e to L.

## Region tags
- solid: vent.<id>
