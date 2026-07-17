# Piper Quest 3 Validation Gate Status

Updated: 2026-07-16 (Asia/Tokyo)

## Current decision

| Gate | Status | Evidence / blocker |
| --- | --- | --- |
| G0 task and data contract | PASS | Four frozen documents, canonical schema, validator, and mock episode; server validation passed |
| G1 joint execution | IN PROGRESS - NOT PASSED | E-stop and 12 microsteps verified; disabling between trials caused multi-joint gravity sag, so the full characterization is stopped pending a hold/home-before-disable procedure |
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
6. After reconnecting the device, the hardware skill's E-stop identity was found exactly: `LinTx USB Keyboard`, VID/PID `8189:0019`, serial `BEC987F2`, keyboard node `/dev/input/event3`, auxiliary mouse/joystick node `/dev/input/event4`. Two 30-second read-only captures (keyboard only, then both nodes) received no button event. No emergency-stop service is running. Device discovery is complete, but physical E-stop actuation remains unverified and blocks motor enable.
7. A synchronized capture verified the E-stop button as Linux `EV_KEY code=28` (`KEY_ENTER`): press=`1`, hold=`2`, release=`0`. The system service was restart-looping because `config.yaml` still referenced `can1/can0`; it was corrected to `can_left/can_right` with backup `config.yaml.bak-20260716-1850`. `piper-emergency-stop.service` is active, initialized both CAN interfaces, and exclusively owns `/dev/input/event3`. The on-site button test triggered `DisableArm(7)` for both arms, and independent feedback readback confirmed all six enable bits `False` on both `can_left` and `can_right`. End-to-end physical E-stop verification passed.

## G1 run card (requires on-site confirmation)

Before any command that can move an arm, confirm all items in writing or at the console:

- [x] On-site operator is present and coordinating with the remote operator.
- [x] LinTx E-stop USB device identified by VID/PID and serial number.
- [x] E-stop button press/hold/release event verified as `KEY_ENTER`.
- [x] E-stop service running with corrected `can_left/can_right` mapping.
- [x] Physical E-stop identified, tested, reachable, and verified by dual-arm enable-state readback.
- [x] Workspace and table cleared by the on-site operator.
- [ ] Correct arm/CAN mapping visually confirmed.
- [ ] Frozen joint position, velocity, acceleration, per-cycle delta, command watchdog, and feedback watchdog thresholds.
- [ ] Logger is running before motor enable and records command, actual, velocity, send/feedback time, and safety state.

Then execute only the G1 matrix: each joint independently, positive and negative small/medium steps repeated five times; low/medium frequency small/medium sine tests; and 20 low-speed A-B-A cycles with five-second holds. Stop immediately on feedback timeout, unexpected direction, limit approach, persistent oscillation, or operator request. Do not connect Quest, IK, cameras, or a policy during G1.

G1 passes only after a report contains per-joint latency median/P95/jitter, rise time, overshoot, settling time, steady-state error, RMSE, direction asymmetry, sine phase lag/amplitude attenuation, and watchdog fault-injection results against frozen thresholds.

## G1 microstep pilot

Both arms completed a low-speed positive microstep and return on joints 1-6. Joint 1 used `0.5 deg`; joints 2-6 used `0.3 deg`; control logging ran at about 49.9 Hz. Every trial required the E-stop service, enabled only the selected arm, used SDK joint limits and 10% motion speed, and disabled the arm in `finally`.

The selected-joint traces stayed below the provisional 3-degree abort threshold. Joint-1 actual excursion was `0.457 deg` left and `0.526 deg` right, with maximum absolute selected-joint errors `0.127 deg` and `0.120 deg`. All 12 scripts returned normally and disabled the arm.

This pilot does **not** pass G1. The first logger stored only the selected joint, while independent six-joint readback showed pose changes after repeated disable cycles. Relative to the initial pilot pose, final disabled drift was:

| Arm | J1 | J2 | J3 | J4 | J5 | J6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| left | 0.000 deg | -2.241 deg | +0.951 deg | +7.684 deg | -2.735 deg | -9.243 deg |
| right | -0.011 deg | -2.367 deg | +1.436 deg | -1.552 deg | -3.550 deg | 0.000 deg |

Both arms were confirmed fully disabled after the pilot. No larger step, repetitions, sine test, Quest, camera, or IK test is authorized from this result. Before resuming G1, update the logger to record all six joints and run a grouped characterization that avoids disable/re-enable between individual samples, returns smoothly to a verified support-safe pose, and only then disables. The on-site operator must confirm the current sagged pose is mechanically safe before recovery motion.

### Grouped recovery pilot

After the on-site operator confirmed the sagged pose was safe, the logger was upgraded to store command, actual, and error for all six joints. Each arm then ran one continuous enable cycle: a five-second recovery to the recorded safe pose, `+0.3 deg` and `-0.3 deg` microsteps for joints 1-6, return to the safe pose, and disable.

| Metric | Left | Right |
| --- | ---: | ---: |
| Logged frames | 1330 | 1330 |
| Effective logging rate | 49.56 Hz | 49.56 Hz |
| Duration | 26.82 s | 26.81 s |
| Distinct phases | 37 | 37 |
| Maximum error across all six joints | 0.421 deg | 0.475 deg |
| Maximum safe-pose return error before disable | 0.073 deg | 0.067 deg |

All host timestamps were strictly increasing. The E-stop service remained active, no abort threshold fired, and independent post-run readback showed all six enable bits false on both arms. This resolves the per-trial gravity-sag contamination for the microstep workflow, but G1 remains in progress until the required repeated small/medium steps, sine tests, A-B-A cycles, latency metrics, and watchdog fault injections pass frozen thresholds.
