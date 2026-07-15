import numpy as np

from .constants_vuer import grd_yup2grd_zup
from .motion_utils import mat_update, fast_mat_inv
from .TeleVision import OpenTeleVision

# Organize data from VR into a set of 4x4 matrices
class VuerPreprocessor:
    def __init__(self):
        # Absolute coordinate start point of right controller when vuer starts
        self.vuer_right_ctrl_mat = np.eye(4)
        # Left controller start pose (for bimanual teleop)
        self.vuer_left_ctrl_mat = np.eye(4)
        self._T = grd_yup2grd_zup
        self._Tinv = fast_mat_inv(grd_yup2grd_zup)
        # [[1 0 0 0]
        #  [0 1 0 0]
        #  [0 0 1 0]
        #  [0 0 0 1]] means initializes to pos = [0 0 0], quat_xyzw = [0 0 0 1]

    # Looking at the coordinates, Y-axis points upward (overhead). Uses Y UP, -Z forward system.
    def process(self, tv : OpenTeleVision):
        # mat_update retains previous value when matrix determinant is 0, otherwise updates with new matrix
        self.vuer_right_ctrl_mat = mat_update(self.vuer_right_ctrl_mat, tv.right_controller.copy())

        # Must convert Y-up system (coordinate system used in VR) to Z-up system (coordinate system used in simulation/control).
        # New matrix = (transform) x (original pose) x (transform^-1) -> change of basis
        right_ctrl = self._T @ self.vuer_right_ctrl_mat @ self._Tinv

        return right_ctrl

    def process_left(self, tv: OpenTeleVision):
        """Transform the left controller into a z-up 4x4 matrix, same convention as process()."""
        self.vuer_left_ctrl_mat = mat_update(self.vuer_left_ctrl_mat, tv.left_controller.copy())
        left_ctrl = self._T @ self.vuer_left_ctrl_mat @ self._Tinv
        return left_ctrl

    def process_both(self, tv: OpenTeleVision):
        """Transform both hands at once -> (left_ctrl_4x4, right_ctrl_4x4)."""
        return self.process_left(tv), self.process(tv)