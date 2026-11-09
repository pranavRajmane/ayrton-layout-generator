# Element: Feeder              id: feeder        version: 0.1

## Purpose
A closed round feeder (riser) in the cope: on the part's top face (top) or on the parting plane beside the part, joined by a neck (side).

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. Geometry only: no foundry rules.

## Parameters
| name | symbol | unit | default / source |
|------|--------|------|------------------|
| kind | – | – | top | side |
| d | D | mm | 60 (typed) |
| height | H | mm | 90 (typed) |
| embed (framework) | e | mm | 1 |

## Ports
| port | type | cross-section | position |
|------|------|---------------|----------|
| base | part face (top) / outlet (side) | round D | its base: on the part (top), on the parting plane (side) |
| top | blind | round D | closed top |

## Geometry
Cylinder along local X from −e to H, base at the origin.

## Region tags
- solid: feeder.<id>
