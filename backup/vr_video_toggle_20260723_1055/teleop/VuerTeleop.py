# vuer_teleop.py
from __future__ import annotations

from multiprocessing import shared_memory
from typing import Tuple
import time

import numpy as np
from pytransform3d import rotations

from .TeleVision import OpenTeleVision
from .Preprocessor import VuerPreprocessor

class VuerTeleop:
    def __init__(self, stream_images: bool = True):
        # Image resolution for Vuer/Quest3 (H, W)
        self.resolution = (720, 1280)

        # Crop settings (no crop currently)
        self.crop_size_w = 0
        self.crop_size_h = 0
        self.resolution_cropped = (
            self.resolution[0] - self.crop_size_h,
            self.resolution[1] - 2 * self.crop_size_w,
        )

        # (H, 2W, 3): buffer with left-eye/right-eye images stitched horizontally
        self.img_shape = (
            self.resolution_cropped[0],
            2 * self.resolution_cropped[1],
            3,
        )
        self.img_height, self.img_width = self.resolution_cropped[:2]

        # Shared memory — only when streaming camera images to the headset.
        self._stream_images = bool(stream_images)
        if self._stream_images:
            nbytes = int(np.prod(self.img_shape) * np.dtype(np.uint8).itemsize)
            self.shm = shared_memory.SharedMemory(create=True, size=nbytes)

            # numpy view directly mapped to OS shared memory
            self.img_array = np.ndarray(
                (self.img_shape[0], self.img_shape[1], 3),
                dtype=np.uint8,
                buffer=self.shm.buf,
            )
        else:
            self.shm = None
            self.img_array = None

        # Handles Quest3 network communication (server)
        # e.g. https://192.168.x.x:PORT?ws=wss://192.168.x.x:PORT
        self.tv = OpenTeleVision(
            self.resolution_cropped,
            self.shm.name if self.shm is not None else "",
            stream_images=self._stream_images,
        )

        # Preprocessor that processes raw VR data
        self.processor = VuerPreprocessor()


    def step(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        returns:
          right_pose:(7,)  [x,y,z,qx,qy,qz,qw]  (quat is xyzw)
        """
        # Process raw VR data (y-up -> z-up, etc.)
        right_wrist_mat = self.processor.process(self.tv)

        # Right hand pose [x, y, z, qx, qy, qz, qw]
        right_quat_wxyz = rotations.quaternion_from_matrix(right_wrist_mat[:3, :3])
        right_quat_xyzw = right_quat_wxyz[[1, 2, 3, 0]]
        right_pose = np.concatenate([right_wrist_mat[:3, 3], right_quat_xyzw])

        return right_pose

    @staticmethod
    def _mat_to_pose7(wrist_mat: np.ndarray) -> np.ndarray:
        """Convert a 4x4 wrist matrix to a pose7 [x,y,z,qx,qy,qz,qw] (xyzw quat)."""
        quat_wxyz = rotations.quaternion_from_matrix(wrist_mat[:3, :3])
        quat_xyzw = quat_wxyz[[1, 2, 3, 0]]
        return np.concatenate([wrist_mat[:3, 3], quat_xyzw])

    def step_both(self) -> Tuple[np.ndarray, np.ndarray]:
        """Read both controllers for bimanual teleop.

        returns:
          (left_pose, right_pose), each (7,) [x,y,z,qx,qy,qz,qw] (xyzw quat)
        """
        left_wrist_mat, right_wrist_mat = self.processor.process_both(self.tv)
        return self._mat_to_pose7(left_wrist_mat), self._mat_to_pose7(right_wrist_mat)

    @property
    def right_state(self) -> np.ndarray:
        return self.tv.right_state
    @property
    def left_state(self) -> np.ndarray:
        return self.tv.left_state

    @property
    def controller_event_status(self) -> dict[str, int | float | None]:
        with self.tv.controller_event_count.get_lock():
            count = int(self.tv.controller_event_count.value)
        with self.tv.last_controller_event_ns.get_lock():
            last_ns = int(self.tv.last_controller_event_ns.value)
        age_s = (time.monotonic_ns() - last_ns) / 1e9 if last_ns else None
        return {"event_count": count, "last_event_ns": last_ns, "age_s": age_s}
    
    def close(self) -> None:
        shm = getattr(self, "shm", None)
        if shm is None:
            return
        try:
            shm.close()
        except Exception:
            pass

        try:
            shm.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

        self.shm = None
