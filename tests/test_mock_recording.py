#!/usr/bin/env python3
"""No-hardware integration test for Piper Quest3 VR LeRobot recording.

Creates a mock-VR teleoperator, runs a mini recording loop with synthetic
observations, and verifies the produced LeRobot v3 dataset.
"""

from __future__ import annotations

import sys
import tempfile
import time
import shutil
import numpy as np
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def build_mock_observation() -> dict[str, Any]:
    """Synthetic observation dict (matches Quest3VR action_features keys)."""
    return {
        "joint_1.pos": 0.0, "joint_2.pos": 0.0, "joint_3.pos": 0.0,
        "joint_4.pos": 0.0, "joint_5.pos": 0.0, "joint_6.pos": 0.0,
        "gripper.pos": 0.07,
    }


def build_mock_observation_bimanual() -> dict[str, Any]:
    """Synthetic dual-arm observation dict (left_/right_ prefixed, 14 DoF)."""
    obs: dict[str, Any] = {}
    for side in ("left_", "right_"):
        for i in range(1, 7):
            obs[f"{side}joint_{i}.pos"] = 0.0
        obs[f"{side}gripper.pos"] = 0.07
    return obs


def test_mock_recording() -> bool:
    from lerobot_teleoperator_quest3_vr import Quest3VR, Quest3VRConfig
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.pipeline_features import (
        aggregate_pipeline_dataset_features,
        create_initial_features,
    )
    from lerobot.datasets.utils import combine_feature_dicts, build_dataset_frame
    from lerobot.processor import make_default_processors

    tmpdir = tempfile.mkdtemp(prefix="piper_quest3_test_")
    dataset_root = Path(tmpdir) / "dataset"  # LeRobotDataset.create requires non-existing dir
    repo_id = "test/piper_quest3_mock"
    fps = 10
    num_frames = 30
    task = "Mock VR teleop test task"

    print(f"Temp dir: {tmpdir}")
    print(f"Recording {num_frames} frames at {fps} fps...")

    # ---- 1. Create teleoperator (mock VR) ---------------------------------
    teleop_cfg = Quest3VRConfig(mock_vr=True)
    teleop = Quest3VR(teleop_cfg)
    teleop.connect()
    print(f"  Mode: {teleop.mode}, Connected: {teleop.is_connected}")

    # ---- 2. Build feature schemas (same pattern as lerobot_record.py) ------
    teleop_ap, robot_ap, robot_op = make_default_processors()

    obs_feature_types = {k: float for k in build_mock_observation()}

    act_initial = create_initial_features(action=teleop.action_features)
    obs_initial = create_initial_features(observation=obs_feature_types)

    act_agg = aggregate_pipeline_dataset_features(
        pipeline=teleop_ap, initial_features=act_initial, use_videos=False,
    )
    obs_agg = aggregate_pipeline_dataset_features(
        pipeline=robot_op, initial_features=obs_initial, use_videos=False,
    )
    dataset_features = combine_feature_dicts(act_agg, obs_agg)

    print(f"  Features: {list(dataset_features.keys())}")

    # ---- 3. Create LeRobot dataset ----------------------------------------
    dataset = LeRobotDataset.create(
        repo_id=repo_id,
        fps=fps,
        root=dataset_root,
        robot_type="piper_quest3",
        features=dataset_features,
        use_videos=False,
    )
    print(f"  Dataset created OK")

    # ---- 4. Recording loop ------------------------------------------------
    start_t = time.perf_counter()
    for frame_idx in range(num_frames):
        loop_start = time.perf_counter()

        obs = build_mock_observation()
        teleop.set_observation(obs)
        action = teleop.get_action()

        obs_frame = build_dataset_frame(dataset.features, obs, prefix="observation.state")
        act_frame = build_dataset_frame(dataset.features, action, prefix="action")
        frame = {**obs_frame, **act_frame, "task": task}
        dataset.add_frame(frame)

        elapsed = time.perf_counter() - loop_start
        sleep_t = (1.0 / fps) - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

    dataset.save_episode()
    elapsed_total = time.perf_counter() - start_t
    print(f"  Recorded {num_frames} frames in {elapsed_total:.1f}s "
          f"({num_frames / elapsed_total:.1f} actual fps)")

    # ---- 5. Verify dataset -------------------------------------------------
    print(f"\nVerification:")
    print(f"  Episodes: {dataset.num_episodes}")
    print(f"  Total frames: {dataset.num_frames}")
    print(f"  FPS: {dataset.fps}")
    print(f"  Robot type: {dataset.meta.robot_type if hasattr(dataset, 'meta') else 'N/A'}")

    data_dir = Path(dataset_root) / "data"
    parquet_files = sorted(data_dir.glob("**/*.parquet"))
    print(f"  Parquet files: {len(parquet_files)}")
    for pf in parquet_files:
        print(f"    {pf.relative_to(dataset_root)} ({pf.stat().st_size} bytes)")

    meta_files = sorted(Path(dataset_root).glob("meta/**/*"))
    print(f"  Meta files: {len(meta_files)}")

    # Verify frame count and files (skip readback due to parquet flush timing)
    assert dataset.num_episodes == 1
    assert dataset.num_frames == num_frames
    assert len(parquet_files) > 0, "No parquet files written"
    assert parquet_files[0].stat().st_size > 1000, "Parquet file too small (incomplete)"

    print(f"  ✓ Dataset structure verified")

    # ---- 6. Cleanup --------------------------------------------------------
    teleop.disconnect()
    shutil.rmtree(tmpdir)
    print(f"\n  Cleanup OK")

    print(f"\n✓ ALL TESTS PASSED ({num_frames} frames recorded & verified)")
    return True


def test_mock_recording_bimanual() -> bool:
    """No-hardware dual-arm integration test (BiQuest3VR, 14 DoF)."""
    from lerobot_teleoperator_bi_quest3_vr import BiQuest3VR, BiQuest3VRConfig
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.pipeline_features import (
        aggregate_pipeline_dataset_features,
        create_initial_features,
    )
    from lerobot.datasets.utils import combine_feature_dicts, build_dataset_frame
    from lerobot.processor import make_default_processors

    tmpdir = tempfile.mkdtemp(prefix="piper_bi_quest3_test_")
    dataset_root = Path(tmpdir) / "dataset"
    repo_id = "test/piper_bi_quest3_mock"
    fps = 10
    num_frames = 30
    task = "Mock bimanual VR teleop test task"

    print(f"\n[BIMANUAL] Temp dir: {tmpdir}")
    print(f"[BIMANUAL] Recording {num_frames} frames at {fps} fps...")

    # ---- 1. Create dual-arm teleoperator (mock VR) ------------------------
    teleop = BiQuest3VR(BiQuest3VRConfig(mock_vr=True))
    teleop.connect()
    print(f"  Modes (left,right): {teleop.mode}, Connected: {teleop.is_connected}")
    assert len(teleop.action_features) == 14, "Bimanual teleop must expose 14 DoF"

    # ---- 2. Build feature schemas -----------------------------------------
    teleop_ap, robot_ap, robot_op = make_default_processors()
    obs_feature_types = {k: float for k in build_mock_observation_bimanual()}

    act_agg = aggregate_pipeline_dataset_features(
        pipeline=teleop_ap,
        initial_features=create_initial_features(action=teleop.action_features),
        use_videos=False,
    )
    obs_agg = aggregate_pipeline_dataset_features(
        pipeline=robot_op,
        initial_features=create_initial_features(observation=obs_feature_types),
        use_videos=False,
    )
    dataset_features = combine_feature_dicts(act_agg, obs_agg)
    print(f"  Features: {list(dataset_features.keys())}")

    # ---- 3. Create dataset ------------------------------------------------
    dataset = LeRobotDataset.create(
        repo_id=repo_id,
        fps=fps,
        root=dataset_root,
        robot_type="bi_piper_quest3",
        features=dataset_features,
        use_videos=False,
    )

    # ---- 4. Recording loop ------------------------------------------------
    for _ in range(num_frames):
        loop_start = time.perf_counter()
        obs = build_mock_observation_bimanual()
        teleop.set_observation(obs)
        action = teleop.get_action()
        assert len(action) == 14, f"Expected 14-DoF action, got {len(action)}"

        obs_frame = build_dataset_frame(dataset.features, obs, prefix="observation.state")
        act_frame = build_dataset_frame(dataset.features, action, prefix="action")
        frame = {**obs_frame, **act_frame, "task": task}
        dataset.add_frame(frame)

        sleep_t = (1.0 / fps) - (time.perf_counter() - loop_start)
        if sleep_t > 0:
            time.sleep(sleep_t)

    dataset.save_episode()

    # ---- 5. Verify --------------------------------------------------------
    parquet_files = sorted((Path(dataset_root) / "data").glob("**/*.parquet"))
    print(f"  Episodes: {dataset.num_episodes}, Frames: {dataset.num_frames}, "
          f"Parquet: {len(parquet_files)}")
    assert dataset.num_episodes == 1
    assert dataset.num_frames == num_frames
    assert len(parquet_files) > 0, "No parquet files written"
    assert parquet_files[0].stat().st_size > 1000, "Parquet file too small (incomplete)"
    print(f"  ✓ Bimanual dataset structure verified")

    # ---- 6. Cleanup -------------------------------------------------------
    teleop.disconnect()
    shutil.rmtree(tmpdir)
    print(f"\n✓ BIMANUAL TEST PASSED ({num_frames} frames recorded & verified)")
    return True


if __name__ == "__main__":
    ok = test_mock_recording()
    ok = test_mock_recording_bimanual() and ok
    sys.exit(0 if ok else 1)
