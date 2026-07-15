# teleop/runtime/init_camera.py
from typing import Optional
from ..io.camera import OpenCVCameraStreamer, CameraStreamerConfig

def init_camera(teleoperator, camera_index: Optional[int]):
    if camera_index is None:
        print("[Camera] Disabled (no --camera).")
        return None
    img_array = getattr(teleoperator, "img_array", None)
    if img_array is None:
        print("[Camera] Cannot initialize: teleoperator has no image buffer "
              "(stream_images disabled). Use --camera to enable headset display.")
        return None
    return OpenCVCameraStreamer(
        img_array,
        CameraStreamerConfig(camera_index=camera_index),
    )
