#!/usr/bin/env python3
"""Check whether the CORAL CLI is available for this workbench skill."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

INSTALL_COMMAND = "curl -fsSL https://raw.githubusercontent.com/Human-Agent-Society/CORAL/main/install.sh | sh"


def check() -> dict[str, Any]:
    coral_bin = os.environ.get("CORAL_BIN") or shutil.which("coral")
    if not coral_bin:
        return {
            "ok": False,
            "status": "missing",
            "message": "CORAL CLI was not found on PATH.",
            "install": INSTALL_COMMAND,
        }

    path = str(Path(coral_bin))
    version = ""
    error = ""
    for args in ((path, "--version"), (path, "version")):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        except OSError as exc:
            error = str(exc)
            continue
        except subprocess.TimeoutExpired:
            error = "version command timed out"
            continue
        output = (result.stdout or result.stderr).strip()
        if result.returncode == 0:
            version = output
            break
        error = output

    return {
        "ok": True,
        "status": "ready",
        "path": path,
        "version": version,
        "message": version or f"CORAL CLI found at {path}",
        "warning": error if not version else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    result = check()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["ok"]:
        print(result["message"])
    else:
        print(result["message"])
        print(f"Install with: {result['install']}")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
