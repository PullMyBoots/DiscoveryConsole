#!/usr/bin/env python3
"""Create the CORAL research-workbench knowledge skeleton."""

from __future__ import annotations

import argparse
from pathlib import Path

SUBDIRS = (
    "sources/papers",
    "sources/repos",
    "sources/web",
    "sources/docs",
    "sources/datasets",
    "notes/research",
    "notes/experiments",
    "notes/synthesis",
    "notes/open-questions",
    "briefs/agent-seeds",
    "briefs/islands",
    "briefs/island-themes",
    "inbox",
    "archive",
)


def prepare(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for subdir in SUBDIRS:
        (path / subdir).mkdir(parents=True, exist_ok=True)

    index = path / "index.md"
    if not index.exists():
        index.write_text(
            "# Knowledge Index\n\n"
            "## Start Here\n"
            "- Add task context in `briefs/task-context.md`.\n"
            "- Fill the eval trust design in `eval_spec.md` before launch.\n"
            "- Add agent launch briefs in `briefs/agent-seeds/`.\n"
            "- Add multi-island themes in `briefs/islands/` when islands are enabled.\n"
            "- Add research summaries in `notes/research/`.\n"
            "- Add experiment reflections in `notes/experiments/`.\n\n"
            "## Sources\n"
            "- Papers: `sources/papers/`\n"
            "- Repositories: `sources/repos/`\n"
            "- Web/docs/datasets: `sources/`\n"
        )

    eval_spec = path / "eval_spec.md"
    if not eval_spec.exists():
        eval_spec.write_text(
            "# Eval Spec\n\n"
            "## Breakthrough Metrics\n"
            "- Define the metrics the run should improve.\n\n"
            "## Guardrail Metrics\n"
            "- Define minimum acceptable behavior and hard failure thresholds.\n\n"
            "## Anti-Cheating and Overfitting Checks\n"
            "- Define leakage checks, invalid-output checks, robustness cases, and "
            "any held-out or stress evaluation.\n\n"
            "## Scalar Score\n"
            "- Define how breakthrough and guardrail metrics become the single "
            "CORAL scheduling score.\n\n"
            "## Eval Profiles\n"
            "- quick:\n"
            "- medium:\n"
            "- full:\n"
            "- stress:\n"
        )

    manifest = path / "manifest.jsonl"
    if not manifest.exists():
        manifest.write_text("")

    notes_index = path / "notes" / "index.md"
    if not notes_index.exists():
        notes_index.write_text(
            "# Notes Index\n\n"
            "## Research\n"
            "- (none yet)\n\n"
            "## Experiments\n"
            "- (none yet)\n\n"
            "## Open Questions\n"
            "- (none yet)\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="knowledge directory to create or update")
    args = parser.parse_args()
    prepare(args.path)


if __name__ == "__main__":
    main()
