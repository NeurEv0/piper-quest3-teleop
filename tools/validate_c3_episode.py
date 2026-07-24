#!/usr/bin/env python3
"""Validate the frozen C3 action/state contract without hardware."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonical_raw.c3_validator import validate_c3_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    import pyarrow.parquet as pq
    metadata = json.loads((args.episode / "metadata.json").read_text(encoding="utf-8"))
    control = pq.read_table(args.episode / "control.parquet").to_pylist()
    feedback = pq.read_table(args.episode / "robot_feedback.parquet").to_pylist()
    report = validate_c3_rows(metadata, control, feedback)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
