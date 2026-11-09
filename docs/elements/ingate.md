# Element: Ingate               id: ingate        version: 0.1

## Purpose
The gate into the casting: a straight rectangular channel on the parting plane from a point on a runner to the part, in the drag or the cope.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. No foundry rules yet.

## Parameters
| name | symbol | unit | default | min | max | driven by calculator? |
|------|--------|------|---------|-----|-----|-----------------------|
| side | – | – | drag | drag, cope | | no |
| width | w | mm | 30 (typed) | 0.5 | 500 | yes: ingate area (width) |
| thickness | t | mm | 8 (typed) | 0.5 | 200 | no |
| waypoints | – | mm | start, end | | | no (dragged in the viewer) |
| embed (framework) | e | mm | 1 | 0 | 10 | no |

## Ports
| port | type | cross-section | position rule |
|------|------|---------------|---------------|
| inlet | inlet | rectangle w × t | the start, on the runner (tee joint) |
| outlet | outlet | rectangle w × t | the end, at the part (gate joint) |

## Geometry
1. The rectangle, flat face on the parting plane, t below it (drag) or above it (cope), swept straight from start to end, run on by e at both ends. The runner keeps the overlap at the start, the part at the end.

## Region tags
- solid: gating.<id>
