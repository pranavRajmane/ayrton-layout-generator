# Element: Feeder neck         id: neck        version: 0.1

## Purpose
A side feeder's neck: a rectangle on the parting plane from inside the feeder to the part.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. Geometry only: no foundry rules.

## Parameters
| name | symbol | unit | default / source |
|------|--------|------|------------------|
| side | – | – | drag | cope |
| width | w | mm | typed |
| thickness | t | mm | typed |
| waypoints | – | mm | start (in the feeder), end (at the part) |
| embed (framework) | e | mm | 1 |

## Ports
| port | type | cross-section | position |
|------|------|---------------|----------|
| inlet | inlet | rectangle w×t | in the feeder (tee) |
| outlet | outlet | rectangle w×t | at the part (gate) |

## Geometry
As the ingate: the rectangle swept straight, flat face on the parting plane.

## Region tags
- solid: feeder.<id>
