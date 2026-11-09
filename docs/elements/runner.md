# Element: Runner               id: runner        version: 0.1

## Purpose
A trapezoid runner drawn on the parting plane, in the drag or the cope; the user places and drags its points.

## Applicable processes / alloys
- green sand, no-bake; grey iron, SG iron, steel. No foundry rules yet.

## Parameters
| name | symbol | unit | default | min | max | driven by calculator? |
|------|--------|------|---------|-----|-----|-----------------------|
| side | – | – | drag | drag, cope | | no |
| w_parting | a | mm | 30 (typed) | 1 | 500 | yes: runner area (widths scaled) |
| w_far | b | mm | 20 (typed) | 1 | 500 | yes: runner area (widths scaled) |
| height | h | mm | 25 (typed) | 1 | 500 | no |
| waypoints | – | mm | (0,0,0), (200,0,0) | | | no (placed in the viewer) |
| bend_radius | R | mm | 0 | 0 | 1000 | no |
| embed (framework) | e | mm | 1 | 0 | 10 | no |

## Ports
| port | type | cross-section | position rule |
|------|------|---------------|---------------|
| inlet | inlet | trapezoid | path start on the parting plane (under the sprue for a drop joint) |
| outlet | outlet | trapezoid | last waypoint |
| tap_* | tap | – | on the section surface at a station (framework) |

## Geometry
1. The path: [start] + waypoints, on the parting plane, bends filleted with R.
2. The trapezoid, wide face (a) on the parting plane, hangs h below it (drag) or stands h above it (cope), swept along the path with embed run past both ends.

## Region tags
- solid: gating.<id>
