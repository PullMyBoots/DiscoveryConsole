#!/usr/bin/env python3
"""Create the CORAL research-workbench knowledge skeleton."""

from __future__ import annotations

import argparse
from pathlib import Path

SUBDIRS = (
    "capsules",
    "maps",
    "packs",
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
            "## Read Order\n"
            "1. Start with the relevant `packs/<agent-id>.md` file.\n"
            "2. Open only the capsules named by that packet.\n"
            "3. Read raw files under `sources/` only when a capsule says the raw source is needed.\n\n"
            "## Start Here For Codex\n"
            "- Add task context in `briefs/task-context.md`.\n"
            "- Fill the eval trust design in `eval_spec.md` before launch.\n"
            "- Add agent launch briefs in `briefs/agent-seeds/`.\n"
            "- Add multi-island themes in `briefs/islands/` when islands are enabled.\n"
            "- Convert useful sources into lightweight capsules in `capsules/`.\n"
            "- Generate per-agent reading packets in `packs/`.\n"
            "- Add experiment reflections in `notes/experiments/`.\n\n"
            "## Active Maps\n"
            "- Method routes: `maps/methods.md`\n"
            "- Run notes: `notes/index.md`\n"
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

    methods_map = path / "maps" / "methods.md"
    if not methods_map.exists():
        methods_map.write_text(
            "# Method Map\n\n"
            "Keep this file short. List only active method families that should guide agent search.\n\n"
            "## Active Routes\n"
            "- (none yet)\n\n"
            "## Failed Or Risky Routes\n"
            "- (none yet)\n"
        )

    global_pack = path / "packs" / "global.md"
    if not global_pack.exists():
        global_pack.write_text(
            "# Global Knowledge Packet\n\n"
            "This is the shared lightweight entry point. Agent-specific packets should stay smaller.\n\n"
            "## Always Read\n"
            "- `eval_spec.md`\n"
            "- `maps/methods.md`\n"
            "- `notes/index.md`\n\n"
            "## Source Rule\n"
            "Prefer capsules over raw sources. Put newly found material in `inbox/` until reviewed.\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="knowledge directory to create or update")
    args = parser.parse_args()
    prepare(args.path)


if __name__ == "__main__":
    main()
