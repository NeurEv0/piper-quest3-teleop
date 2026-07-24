import time
import os
from vuer import Vuer
from vuer.schemas import ImageBackground, DefaultScene
from vuer.schemas import MotionControllers
from multiprocessing import Array, Value, Process, shared_memory
import numpy as np
import asyncio
from pathlib import Path
from .piper_arm_skeleton_vuer import VuerRobotSkeleton  
from vuer.schemas import Sphere

def robot_to_vuer_pos(p_r):
    x, y, z = p_r
    return np.array([x, z, -y], dtype=float)


class OpenTeleVision:
    def __init__(self, img_shape, shm_name, stream_mode="image", cert_file="./cert.pem", key_file="./key.pem", ngrok=False, stream_images=True):
        base_dir = Path(__file__).resolve().parent
        cert_path = (base_dir / cert_file).resolve() if not Path(cert_file).is_absolute() else Path(cert_file)
        key_path  = (base_dir / key_file).resolve()  if not Path(key_file).is_absolute()  else Path(key_file)

        cert_file = str(cert_path)
        key_file  = str(key_path)

        # self.app=Vuer()
        self.img_shape = (img_shape[0], 2*img_shape[1], 3) ## Comes in at resolution based on one eye (left or right)
        self.img_height, self.img_width = img_shape[:2] ## height/width based on one eye (left or right) 

        # ngrok - tunneling service that makes a locally running server accessible from anywhere on the internet
        # Use plain HTTP when VUER_HTTP=1 (e.g. USB cable / adb reverse):
        # http://localhost IS a secure context in Chromium -> WebXR works, and
        # no self-signed cert is involved (fixes the Quest3 "not private" error).
        use_http = os.environ.get("VUER_HTTP") == "1"
        if ngrok or use_http: ## No local cert: ngrok terminates TLS, or HTTP over USB
            self.app = Vuer(host='0.0.0.0', queries=dict(grid=False), queue_len=3) ## queries dict(grid=False) turns off Vuer default UI grid display. queue_len=3 limits event queue length (prevents lag)
        else: ## Use certificate directly
            self.app = Vuer(host='0.0.0.0', cert=cert_file, key=key_file, queries=dict(grid=False), queue_len=3)
        
        # Controller event handler
        self.app.add_handler("CONTROLLER_MOVE")(self.on_controller_move)

        # Shared memory — only when streaming camera images to the headset.
        self._stream_images = bool(stream_images)
        if self._stream_images and stream_mode == "image":
            self._existing_shm = shared_memory.SharedMemory(name=shm_name)
            self.img_array = np.ndarray((self.img_shape[0], self.img_shape[1], 3), dtype=np.uint8, buffer=self._existing_shm.buf)
        elif self._stream_images:
            raise ValueError("stream_mode must be 'image'")
        else:
            self._existing_shm = None
            self.img_array = None

        # Always register the Vuer session coroutine for MotionControllers + skeleton.
        # When stream_images is False the image-streaming part is skipped inside
        # main_image, but the session must run so controller events still fire.
        self.app.spawn(start=False)(self.main_image)

        
        # RIGHT controller
        self.right_controller_shared = Array('d', 16, lock=True) ## 4x4 (right hand)
        self.right_state_shared = Array('d', 14, lock=True)

        # LEFT controller
        self.left_controller_shared = Array('d', 16, lock=True)  # 4x4 (left hand) — for bimanual teleop
        self.left_state_shared = Array('d', 14, lock=True)
        
        # Robot skeleton shared memory (joints xyz)
        self.max_joints = 8  # Generously sized
        self.robot_n_joints = Value('i', 0, lock=True)  # Current number of valid joints
        self.robot_joints_shared = Array('d', 3 * self.max_joints, lock=True)  # xyz flat

        # base->...->ee chain. Matches FK joint order
        # edges (joint-to-joint links) are [(0,1),(1,2)...]
        self.robot_edges = [] 

        self.skel = VuerRobotSkeleton(
            edges=self.robot_edges,
            key="robot-skel",
            joint_radius=0.015,
            link_radius=0.008,
            offset=(0.0, 0.0, 0.0),  # Or remove offset parameter
            layers=0,                # (if present) layers also safe
        )

        self._R_yaw = np.array([
            [0.0, 0.0, 1.0],
            [0.0, 1.0, 0.0],
            [-1.0, 0.0, 0.0],
        ], dtype=float)  # yaw +90

        ## Simplified formula for easy understanding:
        # ee_vuer = ee_fk + (anchor_in_vuer - ee_fk)

        # EE-anchor calibration state (translation that moves EE position computed in real robot coordinates to Quest3 coordinates)
        self._world_offset = None  # np.array shape (3,) or None

        # EE position in Vuer (controller position)
        self._anchor_in_vuer = np.array([0.0, 0.0, 0.0], dtype=float)

        # EE index in joints_xyz (usually -1 if last)
        self._ee_index = -1

        # Camera aspect
        self.aspect_shared = Value('d', 1.0, lock=True) ## 1x1 (camera aspect)

        # Host-side freshness signals used by preflight and Canonical Raw.
        self.controller_event_count = Value('q', 0, lock=True)
        self.last_controller_event_ns = Value('q', 0, lock=True)

        self.process = Process(target=self.run)
        self.process.daemon = True
        self.process.start()

    
    def run(self):
        self.app.run()

    ## Track controllers
    async def on_controller_move(self, event, session, fps=60):
        if not hasattr(self, '_evt_cnt'):
            self._evt_cnt = 0
        self._evt_cnt += 1
        data = event.value
        try:
            with self.controller_event_count.get_lock():
                self.controller_event_count.value += 1
            with self.last_controller_event_ns.get_lock():
                self.last_controller_event_ns.value = time.monotonic_ns()
            # Print first 3 events + every 60th to confirm data is flowing
            if self._evt_cnt <= 3 or self._evt_cnt % 60 == 0:
                right_state = data.get("rightState")
                right_keys = sorted(right_state.keys()) if isinstance(right_state, dict) else "N/A"
                print(f"[VR_EVT #{self._evt_cnt}] keys={sorted(data.keys())} rightState_keys={sorted(right_keys) if isinstance(right_keys, list) else right_keys} squeeze={data.get('rightState', {}).get('squeeze', 'MISSING') if isinstance(data.get('rightState'), dict) else 'NOT_DICT'}")
            # RIGHT
            right = data.get("right") # length-16 array
            if isinstance(right, (list, tuple)) and len(right) == 16:
                self.right_controller_shared[:] = right

            # RIGHT state
            rs = data.get("rightState")
            # right_state_shared 
            if isinstance(rs, dict):
                tp = rs.get("touchpadValue") or [0.0, 0.0]
                ts = rs.get("thumbstickValue") or [0.0, 0.0]

                self.right_state_shared[:] = [
                    1.0 if rs.get("trigger", False) else 0.0,        # 0: this is the gripper
                    1.0 if rs.get("squeeze", False) else 0.0,        # 1: this indicates active state
                    1.0 if rs.get("touchpad", False) else 0.0,       # 2
                    1.0 if rs.get("thumbstick", False) else 0.0,     # 3
                    1.0 if rs.get("aButton", False) else 0.0,        # 4
                    1.0 if rs.get("bButton", False) else 0.0,        # 5

                    float(rs.get("triggerValue", 0.0) or 0.0),       # 6
                    float(rs.get("squeezeValue", 0.0) or 0.0),       # 7
                    float(tp[0] if len(tp) > 0 else 0.0),            # 8
                    float(tp[1] if len(tp) > 1 else 0.0),            # 9
                    float(ts[0] if len(ts) > 0 else 0.0),            # 10
                    float(ts[1] if len(ts) > 1 else 0.0),            # 11

                    1.0 if rs.get("aButtonValue", False) else 0.0,   # 12  
                    1.0 if rs.get("bButtonValue", False) else 0.0,   # 13
                ]

            # LEFT
            left = data.get("left")  # length-16 (left hand 4x4) — for bimanual teleop
            if isinstance(left, (list, tuple)) and len(left) == 16:
                self.left_controller_shared[:] = left

            # LEFT state
            ls = data.get("leftState")
            if isinstance(ls, dict):
                tp = ls.get("touchpadValue") or [0.0, 0.0]
                ts = ls.get("thumbstickValue") or [0.0, 0.0]

                self.left_state_shared[:] = [
                    1.0 if ls.get("trigger", False) else 0.0,        # 0
                    1.0 if ls.get("squeeze", False) else 0.0,        # 1
                    1.0 if ls.get("touchpad", False) else 0.0,       # 2
                    1.0 if ls.get("thumbstick", False) else 0.0,     # 3
                    1.0 if ls.get("aButton", False) else 0.0,        # 4 
                    1.0 if ls.get("bButton", False) else 0.0,        # 5

                    float(ls.get("triggerValue", 0.0) or 0.0),       # 6
                    float(ls.get("squeezeValue", 0.0) or 0.0),       # 7
                    float(tp[0] if len(tp) > 0 else 0.0),            # 8
                    float(tp[1] if len(tp) > 1 else 0.0),            # 9
                    float(ts[0] if len(ts) > 0 else 0.0),            # 10
                    float(ts[1] if len(ts) > 1 else 0.0),            # 11

                    1.0 if ls.get("aButtonValue", False) else 0.0,   # 12
                    1.0 if ls.get("bButtonValue", False) else 0.0,   # 13
                ]


        except Exception as e:
            print("[CONTROLLER_MOVE] error:", e)

    ################### Robot joint skeleton to be rendered on Quest3 #######################
    def enable_skeleton(self, anchor_pos_vuer: np.ndarray):
        self._anchor_in_vuer = np.asarray(anchor_pos_vuer, dtype=float).reshape(3,)
        self._world_offset = None  

    def clear_robot_joints(self):
        with self.robot_n_joints.get_lock():
            self.robot_n_joints.value = 0
        with self.robot_joints_shared.get_lock():
            for k in range(3 * self.max_joints):
                self.robot_joints_shared[k] = 0.0

    def set_robot_joints(self, joints_xyz: np.ndarray):
        arr_r = np.asarray(joints_xyz, dtype=float).reshape(-1, 3)

        # robot -> vuer
        arr_v = np.stack([robot_to_vuer_pos(p) for p in arr_r], axis=0)

        # Unify yaw frame: apply rotation first
        if getattr(self, "_R_yaw", None) is not None:
            arr_v = (self._R_yaw @ arr_v.T).T

        # EE-based offset calibration (using yaw-applied ee0)
        if self._world_offset is None and arr_v.shape[0] >= 1:
            ee0 = arr_v[self._ee_index].copy()
            self._world_offset = self._anchor_in_vuer - ee0
            print(f"[CALIB] ee0={ee0}, world_offset={self._world_offset}")

        # Apply offset
        if self._world_offset is not None:
            arr_v = arr_v + self._world_offset

        n = int(min(arr_v.shape[0], self.max_joints))
        self.robot_edges = [(i, i + 1) for i in range(max(0, n - 1))]

        with self.robot_n_joints.get_lock():
            self.robot_n_joints.value = n

        with self.robot_joints_shared.get_lock():
            flat = self.robot_joints_shared
            for k in range(3 * self.max_joints):
                flat[k] = 0.0
            for i in range(n):
                base = 3 * i
                flat[base + 0] = float(arr_v[i, 0])
                flat[base + 1] = float(arr_v[i, 1])
                flat[base + 2] = float(arr_v[i, 2])
    ########################################################################

    async def main_image(self, session, fps=60):
        print("[VR_SESSION] Vuer WebSocket session connected", flush=True)
        # Turn off grid
        session.set @ DefaultScene(grid=False, frameloop="always")
        
        # Controllers
        session.upsert @ MotionControllers(stream=True, key="motion-controller", left=True, right=True,)
        
        try:
            while True:
                # ── Camera image streaming (only when enabled) ──────────
                if self._stream_images and self.img_array is not None:
                    display_image = self.img_array

                    session.upsert(
                    [ImageBackground(
                        display_image[::2, :self.img_width:2],
                        # 'jpg' encoding is significantly faster than 'png'.
                        format="jpeg",
                        quality=80,
                        key="left-image",
                        interpolate=True,
                        # fixed=True,
                        aspect=1.66667,
                        # distanceToCamera=0.5,
                        height = 2,
                        position=[0, 1, 3],
                        # rotation=[0, 0, 0],
                        layers=1, 
                    ),
                    ImageBackground(
                        display_image[::2, self.img_width::2],
                        # 'jpg' encoding is significantly faster than 'png'.
                        format="jpeg",
                        quality=80,
                        key="right-image",
                        interpolate=True,
                        # fixed=True,
                        aspect=1.66667,
                        # distanceToCamera=0.5,
                        height = 2,
                        position=[0, 1, 3],
                        # rotation=[0, 0, 0],
                        layers=2, 
                    )],
                    to="bgChildren",
                    )

                # Draw robot skeleton
                with self.robot_n_joints.get_lock(), self.robot_joints_shared.get_lock():
                    n = int(self.robot_n_joints.value)
                    if n >= 2:
                        buf = np.array(self.robot_joints_shared[: 3 * n], dtype=float)
                    else:
                        buf = None

                if n >= 2 and buf is not None:
                    joints = buf.reshape(n, 3).copy()

                    # Sync edges (chain form)
                    self.skel.edges = [(i, i + 1) for i in range(n - 1)]

                    self.skel.upsert(session, joints)

                await asyncio.sleep(0.03)

        except asyncio.CancelledError:
            raise
        except Exception as e: ## Web Socket disconnected
            print("[main_image] session ended:", repr(e))
            return


    @property
    def right_controller(self):
        return np.array(self.right_controller_shared[:]).reshape(4, 4, order="F")

    @property
    def left_controller(self):
        """Left-hand controller 4x4 pose (for bimanual teleop). Same convention as right_controller."""
        return np.array(self.left_controller_shared[:]).reshape(4, 4, order="F")

    @property
    def right_state(self) -> "np.ndarray":
        """
        right_state shape: (14,)
        """
        return np.array(self.right_state_shared[:], dtype=float)

    @property
    def left_state(self) -> "np.ndarray":
        """
        left_state shape: (14,)
        """
        return np.array(self.left_state_shared[:], dtype=float)

    @property
    def aspect(self):
        # with self.aspect_shared.get_lock():
            # return float(self.aspect_shared.value)
        return float(self.aspect_shared.value)
