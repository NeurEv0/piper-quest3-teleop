#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import numpy as np
from teleop.VuerTeleop import VuerTeleop  # Adjust path to match your project structure

def main():
    teleop = VuerTeleop("inspire_hand.yml")  # Same as the config you're currently using

    try:
        while True:
            head_rmat, left_pose, right_pose, left_qpos, right_qpos = teleop.step()

            # 1) Log format/shape once
            print("head_rmat shape:", np.shape(head_rmat))
            print("left_pose shape:", np.shape(left_pose))
            print("right_pose shape:", np.shape(right_pose))
            print("left_qpos shape:", np.shape(left_qpos))
            print("right_qpos shape:", np.shape(right_qpos))

            # 2) Print a few actual values
            print("head_rmat[0]:", head_rmat[0])
            print("right_pose (xyz):", right_pose[:3])
            print("right_pose (quat):", right_pose[3:])

            # 3) Brief sleep to check if values change
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n[TEST] Interrupted. Bye!")

if __name__ == "__main__":
    main()
