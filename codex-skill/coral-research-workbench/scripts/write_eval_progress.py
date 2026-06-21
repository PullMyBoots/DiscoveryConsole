#!/usr/bin/env python3
"""Append one CORAL eval progress event to a progress.jsonl file."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("progress_path", type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--current", type=int, required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--phase", default="evaluate")
    parser.add_argument("--message", default="")
    parser.add_argument("--eval-version", default="")
    parser.add_argument("--eval-profile", default="")
    args = parser.parse_args()

    total = max(args.total, 0)
    current = max(args.current, 0)
    event = {
        "type": "progress",
        "job_id": args.job_id,
        "phase": args.phase,
        "current": current,
        "total": total,
        "percent": (current / total) if total else None,
        "message": args.message,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if args.eval_version:
        event["eval_version"] = args.eval_version
    if args.eval_profile:
        event["eval_profile"] = args.eval_profile

    args.progress_path.parent.mkdir(parents=True, exist_ok=True)
    with args.progress_path.open("a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
