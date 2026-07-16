# Piper Quest 3 Pick-and-Place Task Specification

Version: `piper_pick_place_v1`

## Scope

- Robot: one Piper arm, six revolute joints and one gripper.
- Object: lightweight 20 mm x 20 mm cube.
- Target: fixed 20 mm x 20 mm base.
- Initial object pose: randomized only inside the marked tabletop region.
- Episode begins after reset is complete and the operator explicitly arms control.
- Episode ends on success, failure, operator abort, or safety transition.

## Phase labels

Every frame has exactly one phase from:

`reset`, `approach`, `grasp`, `lift`, `transport`, `place`, `release`,
`retreat`, `done`.

## Outcomes

`task_success=true` only when the cube center projection lies inside the target
boundary after release and remains there for the frozen dwell time. A collision
with the table, a dropped cube, safety intervention, IK failure, missing critical
data, or human correction makes the episode ineligible for training.

The required stage labels are `grasp_success`, `lift_success`,
`transport_success`, `place_success`, and `release_success`. Failure reasons use
stable snake-case values, with `none` reserved for successful episodes.

## Versioning

Each episode records task, calibration, robot model, teleoperation commit, control
commit, and schema versions. Raw episodes are immutable. Alignment, filtering,
and action derivation create versioned derived datasets.
