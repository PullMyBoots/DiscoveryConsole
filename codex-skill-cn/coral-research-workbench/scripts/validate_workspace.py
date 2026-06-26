#!/usr/bin/env python3
"""Validate a Codex-authored CORAL task and optional timestamp workspace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _coral_bin() -> str | None:
    return os.environ.get("CORAL_BIN") or shutil.which("coral")


def _run_step(name: str, args: list[str], cwd: Path | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=None,
            check=False,
        )
    except OSError as exc:
        return {
            "name": name,
            "ok": False,
            "returncode": 127,
            "command": args,
            "stdout": "",
            "stderr": str(exc),
        }

    return {
        "name": name,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "command": args,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def validate(task_dir: Path | None, run_dir: Path | None) -> dict[str, Any]:
    coral = _coral_bin()
    if not coral:
        return {
            "ok": False,
            "status": "missing",
            "message": "CORAL CLI was not found on PATH. Set CORAL_BIN or install CORAL before validation.",
            "steps": [],
        }

    steps: list[dict[str, Any]] = []
    if task_dir is not None:
        steps.append(_run_step("task", [coral, "validate", str(task_dir)]))
    if run_dir is not None:
        steps.append(_run_step("run_readiness", [coral, "validate", "--run-dir", str(run_dir)]))

    ok = bool(steps) and all(step["ok"] for step in steps)
    return {
        "ok": ok,
        "status": "ready" if ok else "failed",
        "message": "Workspace validation passed." if ok else "Workspace validation failed.",
        "steps": steps,
    }


def _print_text(result: dict[str, Any]) -> None:
    print(result["message"])
    for step in result.get("steps", []):
        status = "OK" if step.get("ok") else "FAIL"
        command = " ".join(str(part) for part in step.get("command", []))
        print(f"\n[{status}] {step.get('name')}: {command}")
        stdout = str(step.get("stdout") or "").strip()
        stderr = str(step.get("stderr") or "").strip()
        if stdout:
            print(stdout)
        if stderr:
            print(stderr, file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the mandatory validation gate after Codex changes a CORAL workspace.",
    )
    parser.add_argument("--task-dir", type=Path, help="CORAL task directory containing task.yaml")
    parser.add_argument("--run-dir", type=Path, help="prepared timestamp directory or its .coral subdirectory")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    if args.task_dir is None and args.run_dir is None:
        parser.error("provide --task-dir, --run-dir, or both")

    result = validate(
        args.task_dir.expanduser().resolve() if args.task_dir else None,
        args.run_dir.expanduser().resolve() if args.run_dir else None,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_text(result)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
