# Acceptance Thresholds

Version: `piper_acceptance_v1`

These are frozen G0 integrity thresholds. Motion and tracking thresholds for G1
must be frozen from low-speed characterization before G1 can pass.

| Check | G0 threshold |
| --- | --- |
| Host timestamps | integer, strictly increasing per frame |
| Source indices | integer, strictly increasing per source |
| Joint vector length | exactly 6 |
| Quaternion norm | `abs(norm - 1) <= 1e-3` |
| Gripper range | `0.0 <= value <= 0.087 m` |
| Camera dimensions | positive width and height |
| Camera decode flag | true for training-eligible episodes |
| Episode closure | final phase `done`, end time >= last frame time |
| Successful episode | `failure_reason=none` and all five stage labels true |
| Training eligibility | no disconnect, IK failure, safety trigger, intervention, or missing critical field |

G1 safety defaults inherited from the audited hardware workspace are
`max_joint_delta=0.02 rad` and `max_gripper_delta=0.002 m` per control step.
They are upper bounds, not evidence that G1 has passed. Joint limits, velocity,
acceleration, watchdog timeout, tracking RMSE, overshoot, settling time, and
latency P95 remain `TBD_G1`; true-arm commands are prohibited until they are
confirmed with the operator, physical E-stop, and cleared workspace.
