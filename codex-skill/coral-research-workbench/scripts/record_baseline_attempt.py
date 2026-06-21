#!/usr/bin/env python3
"""Record a baseline score in a prepared CORAL timestamp.

This script writes `.coral/public/attempts/<commit>.json` with the metadata
shape expected by CORAL readiness and the Overview baseline chart.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "baseline"


def _load_config_identity(coral_dir: Path) -> tuple[str, str]:
    config_path = coral_dir / "config.yaml"
    if not config_path.exists():
        return "eval_v1", "default"
    text = config_path.read_text(encoding="utf-8", errors="replace")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        grader = data.get("grader") if isinstance(data, dict) else {}
        if isinstance(grader, dict):
            return (
                str(grader.get("eval_version") or "eval_v1"),
                str(grader.get("profile") or "default"),
            )
    except Exception:
        pass
    return _regex_config_value(text, "eval_version", "eval_v1"), _regex_config_value(
        text,
        "profile",
        "default",
    )


def _regex_config_value(text: str, key: str, default: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}\s*:\s*['\"]?([^'\"\n#]+)", text, re.MULTILINE)
    return match.group(1).strip() if match else default


def _score_components(raw: str) -> dict[str, dict[str, Any]]:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--components must be a JSON object")
    components: dict[str, dict[str, Any]] = {}
    for name, value in data.items():
        key = str(name)
        if isinstance(value, dict):
            component = dict(value)
            component.setdefault("name", key)
            components[key] = component
        else:
            components[key] = {"name": key, "value": value}
    return components


def record_baseline(
    coral_dir: Path,
    *,
    score: float,
    name: str,
    title: str,
    commit_hash: str,
    feedback: str,
    components_json: str,
    eval_version: str,
    eval_profile: str,
    force: bool,
) -> Path:
    attempts_dir = coral_dir / "public" / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    path = attempts_dir / f"{commit_hash}.json"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite")

    metadata: dict[str, Any] = {
        "baseline": True,
        "reference": "baseline",
        "baseline_name": name,
        "eval_version": eval_version,
        "eval_profile": eval_profile,
        "aggregated_score": score,
        "budget_class": "real",
    }
    components = _score_components(components_json)
    if components:
        metadata["score_components"] = components

    attempt = {
        "commit_hash": commit_hash,
        "agent_id": "baseline",
        "title": title,
        "score": score,
        "status": "baseline",
        "parent_hash": None,
        "timestamp": datetime.now(UTC).isoformat(),
        "feedback": feedback,
        "metadata": metadata,
    }
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(attempt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("coral_dir", type=Path, help="path to a timestamp .coral directory")
    parser.add_argument("--score", type=float, required=True, help="baseline scalar score")
    parser.add_argument("--name", default="seed", help="baseline name shown in charts")
    parser.add_argument("--title", default="", help="attempt title")
    parser.add_argument("--commit-hash", default="", help="stable attempt id")
    parser.add_argument("--feedback", default="", help="human-readable baseline note")
    parser.add_argument(
        "--components",
        default="",
        help='optional JSON object, e.g. {"breakthrough":0.5,"guardrail":{"value":1.0}}',
    )
    parser.add_argument("--eval-version", default="", help="override eval version")
    parser.add_argument("--eval-profile", default="", help="override eval profile")
    parser.add_argument("--force", action="store_true", help="overwrite existing baseline record")
    args = parser.parse_args()

    coral_dir = args.coral_dir
    config_version, config_profile = _load_config_identity(coral_dir)
    name = args.name.strip() or "seed"
    commit_hash = args.commit_hash.strip() or f"baseline-{_slug(name)}"
    title = args.title.strip() or f"Baseline: {name}"

    path = record_baseline(
        coral_dir,
        score=args.score,
        name=name,
        title=title,
        commit_hash=commit_hash,
        feedback=args.feedback,
        components_json=args.components,
        eval_version=args.eval_version or config_version,
        eval_profile=args.eval_profile or config_profile,
        force=args.force,
    )
    print(path)


if __name__ == "__main__":
    main()
