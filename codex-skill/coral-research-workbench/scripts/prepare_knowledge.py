#!/usr/bin/env python3
"""Create the CORAL research-workbench knowledge skeleton."""

from __future__ import annotations

import argparse
from pathlib import Path

SUBDIRS = (
    "manuals",
    "external/items",
    "practice/agents",
    "briefs/agent-seeds",
)


def prepare(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for subdir in SUBDIRS:
        (path / subdir).mkdir(parents=True, exist_ok=True)

    index = path / "index.md"
    if not index.exists():
        index.write_text(
            "# Knowledge Directory\n\n"
            "This directory is an index-first knowledge base. Do not read it as a normal flat folder.\n\n"
            "## Start Here\n"
            "- `eval_spec.md`: the scoring contract and safety rules.\n"
            "- `manuals/`: short framework manuals.\n"
            "- `briefs/agent-seeds/`: Codex-generated starting plan and first eval script for each agent.\n\n"
            "## Two Knowledge Types\n"
            "- External knowledge: papers, repos, docs, datasets, and web references. Indexed by `external/index.jsonl` and stored under `external/items/`.\n"
            "- Practice knowledge: eval-linked notes, routes, score curves, and reflections under `practice/agents/`.\n\n"
            "## Optional Launch Bundles\n"
            "`briefs/agent-seeds/` contains Codex-prepared starting routes and first-eval scripts. It is launch scaffolding, not a third knowledge store.\n\n"
            "## Before And After Launch\n"
            "- Before `coral start`: read these files directly; Codex should fill in `eval_spec.md`, external references, and agent seeds.\n"
            "- After `coral start`: use `coral kb ...` inside the active run/timestamp.\n\n"
            "## Use These Commands After Launch\n"
            "- `coral kb index manual`: show manuals.\n"
            "- `coral kb index external`: show external references.\n"
            "- `coral kb index practice --by score|route|agent|metric`: show run experience by the view you need.\n"
            "- `coral kb read <id>`: open one indexed item.\n"
            "- `coral kb add external <path-or-url> --kind <kind> --title \"...\"`: add a reference.\n"
            "- `coral kb note \"...\"`: add a practice note.\n"
        )
    external_index = path / "external" / "index.jsonl"
    if not external_index.exists():
        external_index.write_text("")
    manuals = {
        "coral-overview-cli.md": (
            "# CORAL Overview And CLI\n\n"
            "CORAL is the lightweight runtime shell. Codex prepares the task "
            "workspace, eval contract, baselines, knowledge, and agent "
            "initialization bundles. CORAL starts and supervises agents, manages "
            "worktrees, schedules eval/compute jobs, exposes the knowledge CLI, "
            "and renders the dashboard.\n\n"
            "Core commands: `coral prepare`, `coral start`, `coral eval`, "
            "`coral run`, `coral log`, `coral show`, and `coral kb ...`.\n"
        ),
        "agent-loops.md": (
            "# Agent Work And Reflect Loops\n\n"
            "work_loop implements or diagnoses one candidate route, then submits "
            "evidence with `coral eval`. reflect_loop turns a real eval result "
            "into durable practice knowledge with `coral kb archive --attempt ...`.\n"
        ),
        "evaluation-spaces.md": (
            "# Evaluation Spaces\n\n"
            "Choose exactly one task level with the user: L1, L2, or L3. "
            "This is a task-level research-design contract, not a runtime "
            "preference. Once the research question and intended claim are "
            "defined, the question should match one level by design. Use "
            "environment certainty as the main axis: fixed and closed tasks "
            "belong nearer L1; open or deployment-uncertain tasks belong "
            "nearer L3. Do not "
            "try L1, then L2, then L3 for the same question; if the level "
            "changes, fork a new task version or timestamp lineage.\n\n"
            "- L1: A-space scoring is open to agents.\n"
            "- L2: A-space is open exploration; B-space is hidden ranking eval.\n"
            "- L3: A-space is open exploration; B-space is hidden iteration; "
            "C-space is sealed final validation outside the normal agent loop.\n"
            "- When generalization matters, A/B/C should form a graded "
            "evidence ladder, not arbitrary same-distribution splits: A is "
            "cheap and learnable, B is hidden and more representative, and C "
            "is sealed and closest to the target environment.\n"
        ),
        "submit-system.md": "# Submit System\n\n`coral eval` submits official scored attempts.\n",
        "knowledge-cli.md": "# Knowledge CLI\n\nUse `coral kb index ...` then `coral kb read <id>`.\n",
    }
    for filename, content in manuals.items():
        manual = path / "manuals" / filename
        if not manual.exists():
            manual.write_text(content)

    eval_spec = path / "eval_spec.md"
    if not eval_spec.exists():
        eval_spec.write_text(
            "# Eval Spec\n\n"
            "## Agent API\n"
            "- `coral eval -m \"...\"`: submit the current solution for the task's ranking space.\n"
            "- `coral eval --tune -m \"...\"`: optional cheaper scoring for exploration, if supported.\n"
            "- `coral run -- <command>`: run an open A-space exploration script with tracked logs/artifacts, if an isolated or explicitly enabled runner is configured.\n\n"
            "## Evaluation Level\n"
            "- Choose exactly one level for this task with the user: L1, L2, or L3.\n"
            "- Treat the selected level as a task-level research-design contract, not a runtime tuning knob.\n"
            "- Once the research question and intended claim are defined, the question should match one level by design.\n"
            "- Use environment certainty as the main axis: fixed and closed tasks belong nearer L1; open or deployment-uncertain tasks belong nearer L3.\n"
            "- Do not try L1, then L2, then L3 for the same question; if the level changes, fork a new task version or timestamp lineage and avoid direct score comparison.\n"
            "- L1: A-space scoring is open to agents.\n"
            "- L2: A-space is open exploration; B-space is hidden ranking eval.\n"
            "- L3: A-space is open exploration; B-space is hidden iteration; C-space is sealed final validation outside the normal agent loop.\n\n"
            "## Evaluation Spaces\n"
            "- When generalization matters, A/B/C should be a graded evidence ladder, not arbitrary same-distribution splits.\n"
            "- A-space should be cheap, open, and learnable enough for optimization without becoming a toy problem.\n"
            "- B-space should be hidden and more representative while remaining continuous enough with A that improvements can transfer.\n"
            "- C-space, when used, should be sealed and closest to the target deployment or final claim.\n"
            "- Avoid cliffs: if A is too unlike B, agents cannot optimize; if B is too unlike C, the selected winner may not support the final claim.\n\n"
            "## Metrics\n"
            "- Breakthrough metrics define what the run should improve.\n"
            "- Guardrail metrics define minimum acceptable behavior and hard failure thresholds.\n"
            "- Anti-cheating checks define leakage, memorization, hidden-data access, and overfitting safeguards.\n"
            "- Do not disclose hidden case IDs, answer keys, private weights, or exploitable scoring details.\n\n"
            "## Acceptance\n"
            "- Define anti-cheating, overfitting, leakage, invalid-output, runtime, and memory gates.\n\n"
            "## Progress Protocol\n"
            "- Long evals must call `self.report_progress(...)` so the control panel can render progress.\n\n"
            "## Eval Profiles\n"
            "- quick: same scoring mechanism, fewer cases/seeds, cheaper iteration, higher variance.\n"
            "- medium: stronger signal at moderate cost.\n"
            "- full: main validation profile.\n"
            "- stress: robustness, leakage, or distribution-shift checks.\n\n"
            "## Feedback Report\n"
            "- Successful reports include total score, accepted status, top-5 rank context, self-history, baselines, and per-metric values/ranks.\n"
            "- Failed reports include failure stage, error type, error message, and log path.\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="knowledge directory to create or update")
    args = parser.parse_args()
    prepare(args.path)


if __name__ == "__main__":
    main()
