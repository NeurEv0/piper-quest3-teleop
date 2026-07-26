#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from canonical_raw.fixtures import write_cleaning_ready_fixture


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic no-hardware cleaning-ready fixture.")
    parser.add_argument("output_root", type=Path)
    parser.add_argument(
        "--print-session-workflow",
        action="store_true",
        help="Print a downstream session-workflow command template for the generated episode.",
    )
    args = parser.parse_args()
    paths = write_cleaning_ready_fixture(args.output_root)
    for path in paths:
        print(path)
    if args.print_session_workflow:
        episode_dir = paths[-1]
        print(
            "session-workflow --input "
            f"{episode_dir} --manifest {episode_dir / 'manifest.json'} --validation {episode_dir / 'validation.json'} "
            "--capture-contract piper_capture_cleaning_ready_v1"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
