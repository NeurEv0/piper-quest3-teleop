# teleop/app.py
import time
import numpy as np
import mujoco

from .VuerTeleop import VuerTeleop
from . import config
from .runtime.context import RuntimeContext
from .runtime.init_camera import init_camera
from .runtime.init_fk_mapper import init_fk_and_start, init_mapper
from .runtime.init_mink import init_mink
from .runtime.init_viewer import init_viewer
# from .runtime.init_ros2_bridge import init_ros2_bridge
from .runtime.init_mujoco import init_T_zero
from .runtime.init_mujoco import init_mujoco_state_zero
from .runtime.init_mujoco import init_gripper_indices
from .runtime.init_left_controller import LeftController, LeftControllerConfig
from .runtime.init_right_controller import RightController, RightControllerConfig

from .utils.joint123_near_zero import joints123_near_zero
from .utils.runtime_reset import reset_to_zero_like_init
from .control.ik_stepper import ik_step

from multiprocessing import Process, Event, Array
from .runtime.piper_send_process import piper_sender  

from .utils.profiler import LoopProfiler

## Return value goes into run_loop -> Initialize all objects needed for execution
def build_runtime(args) -> RuntimeContext:
    teleoperator = VuerTeleop(stream_images=(args.camera is not None))
    left = LeftController(LeftControllerConfig(
        deadband=0.15,
        max_v=0.6,
        max_w=1.2,
        pub_hz=20.0,
    ))
    right = RightController(RightControllerConfig(
        gripper_mode="analog",
        gripper_out_min=0,
        gripper_out_max=1000,
        gripper_close_when_high=True,
        gripper_alpha=0.35,
        idx_squeeze_pressed=1,
        idx_return_pressed=4,
    ))
    right.reset(open_gripper=True)

    # vision_rclpy, vision_node = init_ros2_bridge(args.vision60)
    vision_rclpy, vision_node = None, None
    cam = init_camera(teleoperator, args.camera) # camera

    fk, q_zero, EE_START, R_ee0 = init_fk_and_start() # EE_START is x,y,z at zero pos, R_ee0 is rotation matrix at zero pos
    mapper = init_mapper(EE_START, R_ee0, debug=args.debug_mapper)
    model, data, configuration, tasks, limits, solver, rate = init_mink(q_zero)
    viewer = init_viewer(model, data, args.dry_run)

    T_zero = init_T_zero(EE_START, R_ee0)
    init_mujoco_state_zero(model, data)
    mujoco.mj_forward(model, data)

    # gripper indices
    q_idx7, q_idx8 = init_gripper_indices(model)
    
    ### shared memory ###
    # shared cmd: [q1..q6, grip_hw]
    cmd_shared = Array('d', 7, lock=True)
    stop_event = Event()

    # Initial values
    with cmd_shared.get_lock():
        for i in range(6):
            cmd_shared[i] = 0.0
        cmd_shared[6] = 0.0

    send_hz = float(getattr(config, "SEND_RATE_HZ", 100.0))
    sender_proc = Process(
        target=piper_sender,
        args=(args, cmd_shared, stop_event, send_hz),
        daemon=True,
    )

    # piper sender process
    sender_proc.start()
    if not sender_proc.is_alive():
        raise RuntimeError("piper_sender process failed to start")

    # store driver inside teleoperator or return separately
    rt = RuntimeContext(
        teleoperator=teleoperator, 
        cam=cam,
        fk=fk, mapper=mapper,
        model=model, data=data, configuration=configuration,
        tasks=tasks, limits=limits, solver=solver, rate=rate,
        viewer=viewer, 
        T_zero=T_zero,
        last_q=q_zero.copy(), T_filt=None, vel_filt=None,
        q_idx7=q_idx7, q_idx8=q_idx8,
        startup_sent_zero=False, # Send zero at startup
        sent_joint_zero=False, # Send zero after returning
        hold_target=None,
        mode="RETURNING",
        vision_rclpy=vision_rclpy,
        vision_node=vision_node,
        left=left,
        right=right,
        cmd_shared=cmd_shared,
        stop_event=stop_event,
        sender_proc=sender_proc
    )
    return rt


def run_loop(args, rt: RuntimeContext):
    prof = LoopProfiler(report_period=1.0)
    try:
        while True:
            t_loop0 = time.perf_counter()
            
            t = time.perf_counter()

            ## Check if viewer is alive
            if rt.viewer is not None and not rt.viewer.is_running():
                break
            prof.add("viewer_alive_check", time.perf_counter() - t)

            
            if not rt.startup_sent_zero:
                t_startup = time.perf_counter()
                rt.last_q[:6] = 0.0

                # Also maintain viewer state
                for k in range(6):
                    j = rt.model.joint(f"joint{k+1}")
                    adr = int(np.asarray(j.qposadr).item())
                    rt.data.qpos[adr] = 0.0
                    vadr = int(np.asarray(j.dofadr).item())
                    rt.data.qvel[vadr] = 0.0
                mujoco.mj_forward(rt.model, rt.data)

                # Write to shared cmd so sender process can send
                with rt.cmd_shared.get_lock():
                    for i in range(6):
                        rt.cmd_shared[i] = float(rt.last_q[i])
                    rt.cmd_shared[6] = 0.0 


                rt.startup_sent_zero = True

                # startup_block only here!
                prof.add("startup_block", time.perf_counter() - t_startup)

                t = time.perf_counter()
                rt.rate.sleep()
                prof.add("rate_sleep", time.perf_counter() - t)

                # Finish profiling before continue
                prof.add("loop_total", time.perf_counter() - t_loop0)
                prof.tick_loop()
                if prof.should_report():
                    prof.report_and_reset()
                continue


            t = time.perf_counter()
            # Receive right-hand controller info from VR
            right_pose_T = rt.teleoperator.step()
            prof.add("teleop_step", time.perf_counter() - t)

            

            t = time.perf_counter()
            # LEFT Controller -> handles vision60 command + publish + toggle internally
            rt.left.update(
                rt.teleoperator.left_state,
                vision_node=rt.vision_node,
                vision_rclpy=rt.vision_rclpy,
            )
            prof.add("left_update", time.perf_counter() - t)


            t = time.perf_counter()
            # RIGHT Controller
            r = rt.right.update(rt.teleoperator)
            prof.add("right_update", time.perf_counter() - t)

            grip_hw = r.grip_out
            squeeze_holding = r.holding
            squeeze_just_pressed = r.just_pressed
            

            ##################### Four Modes ######################
            ## RETURNING - returning to zero position
            ## AT_ZERO - arrived at zero position
            ## HOLD - holding current pose
            ## TELEOP - squeeze button pressed (in teleoperation)


            t = time.perf_counter()
            # Get current robot EE pose
            T_cur = rt.fk.compute_fk(rt.last_q)
            prof.add("fk_compute", time.perf_counter() - t)

            t = time.perf_counter()
            if rt.mode == "RETURNING":
                if joints123_near_zero(rt.last_q, tol_deg=5.0):
                    rt.mode = "AT_ZERO"
                    rt.mapper.reset_state(keep_neutral_target=False)

                    if not rt.sent_joint_zero:
                        q_zero6 = np.zeros(6, dtype=float)
                        reset_to_zero_like_init(rt, q_zero6)
                        rt.sent_joint_zero = True
                target_T = rt.T_zero

            elif rt.mode == "AT_ZERO":
                target_T = rt.T_zero

                if squeeze_just_pressed:
                    controller_anchor = rt.teleoperator.tv.right_controller[:3, 3].copy()
                    rt.teleoperator.tv.enable_skeleton(controller_anchor)

                    q_zero6 = np.zeros(6, dtype=float)
                    reset_to_zero_like_init(rt, q_zero6)

                    rt.mapper.set_neutral(rt.T_zero, right_pose_T)

                    rt.mode = "TELEOP"

                    # mode_logic done for this frame, record and bail out
                    prof.add("mode_logic", time.perf_counter() - t)

                    t_sleep = time.perf_counter()
                    rt.rate.sleep()
                    prof.add("rate_sleep", time.perf_counter() - t_sleep)

                    prof.add("loop_total", time.perf_counter() - t_loop0)
                    prof.tick_loop()
                    if prof.should_report():
                        prof.report_and_reset()
                    continue

            elif rt.mode == "HOLD":
                target_T = rt.hold_target if rt.hold_target is not None else T_cur

                # Re-enter TELEOP when squeeze is pressed again
                if squeeze_just_pressed:
                    controller_anchor = rt.teleoperator.tv.right_controller[:3, 3].copy()
                    rt.teleoperator.tv.enable_skeleton(controller_anchor)

                    rt.mapper.set_neutral(target_T, right_pose_T)
                    rt.T_filt = None
                    rt.vel_filt = None
                    rt.mode = "TELEOP"

                    prof.add("mode_logic", time.perf_counter() - t)
                    t = time.perf_counter()
                    
                    rt.rate.sleep()
                    prof.add("rate_sleep", time.perf_counter() - t)

                    prof.add("loop_total", time.perf_counter() - t_loop0)
                    
                    prof.tick_loop()
                    if prof.should_report():
                        prof.report_and_reset()
                    continue

                # Only return when RETURN button is pressed
                if r.go_to_zero:
                    rt.mode = "RETURNING"
                    rt.T_filt = None
                    rt.vel_filt = None
                    rt.sent_joint_zero = False
                    if hasattr(rt.mapper, "neutral_target_T"):
                        rt.mapper.neutral_target_T = None

            elif rt.mode == "TELEOP":
                # Only map controller when in TELEOP
                target_T = rt.mapper.compute_target_T(right_pose_T)
                # Transition to HOLD when squeeze release is detected
                if not squeeze_holding:
                    rt.teleoperator.tv.clear_robot_joints()
                    rt.hold_target = T_cur.copy()
                    rt.mode = "HOLD"
                    rt.T_filt = None
                    rt.vel_filt = None
            ###################################################
            prof.add("mode_logic", time.perf_counter() - t)
            
            ############### IK step #####################
            if rt.mode != "AT_ZERO":
                t = time.perf_counter()
                try:
                    dt = float(rt.rate.dt)

                    rt.last_q = ik_step(
                        rt.model, rt.data, rt.configuration,
                        rt.tasks, rt.limits, rt.solver, dt,
                        rt.last_q, target_T,
                        grip_hw, rt.q_idx7, rt.q_idx8,
                        debug_qpos_check=False
                    )
                except Exception as e:
                    print("[mink IK] Failed -> keep last_q:", repr(e))
                prof.add("ik_step", time.perf_counter() - t)
            ##############################################


            ############ skeleton render #################
            if rt.mode == "TELEOP":
                t = time.perf_counter()
                joints_xyz = rt.fk.fk_all_joint_positions(rt.last_q)
                rt.teleoperator.tv.set_robot_joints(joints_xyz)
                prof.add("skeleton_render", time.perf_counter() - t)
            ##############################################

            ############## send to robot ##################
            # Record latest command for sender process to read
            t = time.perf_counter()
            with rt.cmd_shared.get_lock():
                for i in range(6):
                    rt.cmd_shared[i] = float(rt.last_q[i])
                rt.cmd_shared[6] = float(grip_hw * 1000.0)
            prof.add("cmd_write_lock", time.perf_counter() - t)

            # Camera
            if rt.cam is not None:
                t = time.perf_counter()
                rt.cam.step()
                prof.add("camera_step", time.perf_counter() - t)

            if rt.viewer is not None:
                t = time.perf_counter()
                rt.viewer.sync()
                prof.add("viewer_sync", time.perf_counter() - t)

            t = time.perf_counter()
            rt.rate.sleep()
            prof.add("rate_sleep", time.perf_counter() - t)

            prof.add("loop_total", time.perf_counter() - t_loop0)
            prof.tick_loop()

            if prof.should_report():
                prof.report_and_reset()



    except KeyboardInterrupt:
        pass
    finally:
        try:
            rt.close()
        except Exception:
            pass