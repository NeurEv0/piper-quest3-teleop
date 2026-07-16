# Frame and Time Convention

Version: `piper_frames_v1`

## Units and transforms

- Position: metres (`m`).
- Joint angle and angular velocity: radians (`rad`, `rad/s`).
- Time: integer nanoseconds from a monotonic host clock.
- Quaternion order: `[qx, qy, qz, qw]`, normalized to unit length.
- Transform name `T_a_b` maps coordinates expressed in frame `b` into frame `a`.

Required frames are `world`, `robot_base`, `tcp`, `gripper`, every camera optical
frame, and `quest_right_controller`. The canonical TCP pose is `T_base_tcp`.
Camera extrinsics and Quest-to-base mapping are calibration artifacts referenced
by `calibration_version`; they are not silently embedded in recording code.

## Source timestamps

Each source stores its device timestamp when available and the host receive
timestamp. Robot commands store generation and send timestamps. Robot feedback
stores its feedback timestamp and host receive timestamp. File modification time,
frame number, and array index are never used as source time.

All host timestamps in one episode use `CLOCK_MONOTONIC` (or
`time.monotonic_ns()`) from the recording host. Offline alignment uses robot
feedback timestamps as the reference axis and preserves source indices and
residuals.
