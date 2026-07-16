# Piper Quest 3 Validation Gate Status

Updated: 2026-07-16 (Asia/Tokyo)

## Current decision

| Gate | Status | Evidence / blocker |
| --- | --- | --- |
| G0 task and data contract | PASS | Four frozen documents, canonical schema, validator, and mock episode; server validation passed |
| G1 joint execution | NOT STARTED | Requires an on-site operator, verified physical E-stop, cleared workspace, frozen limits, and explicit motion authorization |
| G2-G8 | BLOCKED BY PREDECESSOR | Gate ordering is mandatory |

## Evidence collected

- Project: `/home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop`
- Branch/commit at audit: `piper-quest3-teleop-lerobot`, `5afe753`; branch was one commit ahead of its configured remote.
- Canonical fixture: `python tools/validate_canonical_episode.py examples/mock_episode.json` passed with 2 frames.
- Existing LeRobot no-hardware integration: single-arm and bimanual mock recordings passed with 30 frames each and valid Parquet output.
- `can_left` and `can_right`: mapped from USB ports `1-5.1` and `1-5.2`, both `UP,LOWER_UP`, `ERROR-ACTIVE`, 1 Mbps.
- Passive `can_right` capture received continuous Piper feedback frames; no test frame or motion command was sent.
- Orbbec SDK v1.10.35 detected and read all three cameras at 640x480: front `CP0BB530000J`, left wrist `CC1N16200P0`, right wrist `CC1N162022N`.
- GPU preflight: RTX PRO 6000 Blackwell Max-Q, 94.9 GiB; PyTorch 2.11.0+cu128 reports CUDA available.

## Open findings

1. The current LeRobot recording feature set contains only `observation.state` and `action`. It does not yet persist the canonical Quest timestamps, command generation/send timestamps, robot feedback timestamps, TCP target/actual, safety state, controller mode, phase, or per-camera source timestamps. Therefore the existing recorder is not G4-capable.
2. `vla-hardware-debug` reports `ModuleNotFoundError: datasets` from the `pi05` environment startup hook. Hardware checks still completed. The Quest recording path uses the `lerobot` environment, where the mock data test passed, so this is not a G0 blocker.
3. CAN interface names were configured at runtime. Persistence across reboot is not yet proven.
4. The audited hardware skill warns that `record_action_from_follower=True` stores encoder feedback instead of the requested gripper target. Freeze this flag explicitly for each collection mode and verify gripper labels before G5.
5. A concurrent modification appeared in `lerobot_robot_bi_piper_quest3/bi_piper_quest3.py` during the audit. It was not changed or reverted by this work.

## G1 run card (requires on-site confirmation)

Before any command that can move an arm, confirm all items in writing or at the console:

- [x] On-site operator is present and coordinating with the remote operator.
- [ ] Physical E-stop identified, tested, and reachable by the on-site operator.
- [ ] Workspace and table cleared; arm starts in a low-risk pose.
- [ ] Correct arm/CAN mapping visually confirmed.
- [ ] Frozen joint position, velocity, acceleration, per-cycle delta, command watchdog, and feedback watchdog thresholds.
- [ ] Logger is running before motor enable and records command, actual, velocity, send/feedback time, and safety state.

Then execute only the G1 matrix: each joint independently, positive and negative small/medium steps repeated five times; low/medium frequency small/medium sine tests; and 20 low-speed A-B-A cycles with five-second holds. Stop immediately on feedback timeout, unexpected direction, limit approach, persistent oscillation, or operator request. Do not connect Quest, IK, cameras, or a policy during G1.

G1 passes only after a report contains per-joint latency median/P95/jitter, rise time, overshoot, settling time, steady-state error, RMSE, direction asymmetry, sine phase lag/amplitude attenuation, and watchdog fault-injection results against frozen thresholds.
