# Element: Sprue well          id: sprue_well        version: 0.1

## Purpose
A round chamber under the sprue (under its filter) that the runners leave from, reaching below the deepest runner floor.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. Geometry only: no foundry rules.

## Parameters
| name | symbol | unit | default / source |
|------|--------|------|------------------|
| d | D | mm | 50 (typed) |
| depth | d | mm | 20 (typed): below the deepest runner floor |
| length | L | mm | set from the runners and filter |
| embed (framework) | e | mm | 1 |

## Ports
| port | type | cross-section | position |
|------|------|---------------|----------|
| inlet | inlet | round D | its top: the filter's bottom (on the parting plane) or the sprue's bottom |
| outlet | outlet | round D | its bottom; the runners drop from it |

## Geometry
Cylinder along local X from −e to L + e.

## Region tags
- solid: gating.<id>
