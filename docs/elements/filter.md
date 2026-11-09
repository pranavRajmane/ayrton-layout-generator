# Element: Filter              id: filter        version: 0.1

## Purpose
A ceramic foam filter across the bottom of the sprue, on the parting plane, over the sprue well.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. Geometry only: no foundry rules.

## Parameters
| name | symbol | unit | default / source |
|------|--------|------|------------------|
| shape | – | – | square | round |
| size | a | mm | 50 (typed): side or diameter |
| thickness | t | mm | 20 (typed) |
| embed (framework) | e | mm | 1 |

## Ports
| port | type | cross-section | position |
|------|------|---------------|----------|
| inlet | inlet | square a×a or round a | its top face |
| outlet | outlet | same | its bottom face, on the parting plane |

## Geometry
Box a×a×t or cylinder ⌀a×t, in the cope, its bottom on the parting plane. Embed 0: its region is exactly the filter.

## Region tags
- solid: filter.<id> (porous for the solver)
