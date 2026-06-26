"""Project-level directory structure and orchestration."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from coral.config import CoralConfig
from coral.hub.checkpoint import init_checkpoint_repo
from coral.workspace.repo import (
    _commit_staged_changes,
    clone_or_init_repo,
    copy_private_data,
    copy_seed_directory,
)

logger = logging.getLogger(__name__)


@dataclass
class ProjectPaths:
    """Paths created by create_project."""

    results_dir: Path  # e.g. results/
    task_dir: Path  # e.g. results/erdos-minimum-overlap-problem/
    run_dir: Path  # e.g. results/erdos-minimum-overlap-problem/2026-03-11_163000/
    coral_dir: Path  # run_dir/.coral/
    agents_dir: Path  # run_dir/agents/
    repo_dir: Path  # run_dir/repo/ (cloned per-run)
    snapshots_dir: Path | None = None  # run_dir/snapshots/ (frozen task inputs)

    def __post_init__(self) -> None:
        if self.snapshots_dir is None:
            self.snapshots_dir = self.run_dir / "snapshots"


def slugify(name: str) -> str:
    """Convert a task name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "task"


_SEED_SKILLS_DIR = Path(__file__).parent.parent / "template" / "skills"
_SEED_AGENTS_DIR = Path(__file__).parent.parent / "template" / "agents"


_PUBLIC_SUBDIRS = (
    "attempts",
    "logs",
    "skills",
    "agents",
    "knowledge",
    "eval_logs",
    "jobs",
    "datasets",
    "control",
)


_KNOWLEDGE_SUBDIRS = (
    "manuals",
    "external/items",
    "practice/agents",
    "briefs/agent-seeds",
)


def _build_public_subtree(
    coral_dir: Path,
    public_root: Path,
    effective_config_dir: Path,
    user_skill_paths: list[str],
) -> None:
    """Create the shared public state tree and seed bundled assets."""
    for sub in _PUBLIC_SUBDIRS:
        (public_root / sub).mkdir(parents=True, exist_ok=True)
    _ensure_knowledge_base(public_root / "knowledge")

    # Seed bundled skills from coral/template/skills/
    if _SEED_SKILLS_DIR.is_dir():
        for skill_dir in _SEED_SKILLS_DIR.iterdir():
            if skill_dir.is_dir():
                dst = public_root / "skills" / skill_dir.name
                if not dst.exists():
                    shutil.copytree(skill_dir, dst)
                    logger.info(f"Seeded skill: {skill_dir.name}")

    # Seed user-provided skills from agents.skills config
    for skill_path in user_skill_paths:
        src = Path(skill_path)
        if not src.is_absolute():
            src = (effective_config_dir / src).resolve()
        if src.is_dir():
            dst = public_root / "skills" / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            logger.info(f"Seeded user skill: {src.name}")
        else:
            logger.warning(f"Skill directory not found: {src}")

    # Seed bundled subagent templates from coral/template/agents/
    if _SEED_AGENTS_DIR.is_dir():
        for agent_file in _SEED_AGENTS_DIR.iterdir():
            if agent_file.is_file():
                dst = public_root / "agents" / agent_file.name
                if not dst.exists():
                    shutil.copy2(agent_file, dst)
                    logger.info(f"Seeded agent template: {agent_file.name}")

    init_checkpoint_repo(str(coral_dir))


def _ensure_knowledge_base(knowledge_dir: Path) -> None:
    """Create the simplified index-first knowledge-base skeleton."""
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    for sub in _KNOWLEDGE_SUBDIRS:
        (knowledge_dir / sub).mkdir(parents=True, exist_ok=True)

    index = knowledge_dir / "index.md"
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
    external_index = knowledge_dir / "external" / "index.jsonl"
    if not external_index.exists():
        external_index.write_text("")

    eval_spec = knowledge_dir / "eval_spec.md"
    if not eval_spec.exists():
        eval_spec.write_text(
            "# Eval Spec\n\n"
            "## Agent API\n"
            "- `coral eval -m \"...\"`: submit the current solution for the task's ranking space.\n"
            "- `coral eval --tune -m \"...\"`: optional cheaper scoring for exploration, if supported.\n"
            "- `coral run -- <command>`: run an open A-space exploration script with tracked logs/artifacts, if an isolated or explicitly enabled runner is configured.\n"
            "- Document required files, input/output formats, and forbidden access here.\n\n"
            "## Evaluation Level\n"
            "- L1: A-space scoring is open to agents.\n"
            "- L2: A-space is open exploration; B-space is hidden ranking eval.\n"
            "- L3: A-space is open exploration; B-space is hidden iteration; C-space is sealed final eval.\n\n"
            "## Metrics\n"
            "- Define public metric names, directions, and safe explanations.\n"
            "- Breakthrough metrics define the primary score or research improvement target.\n"
            "- Guardrail metrics define correctness, runtime, memory, safety, and regression constraints.\n"
            "- Anti-cheating checks define leakage, memorization, hidden-data access, and overfitting safeguards.\n"
            "- Do not disclose hidden case IDs, answer keys, private weights, or exploitable scoring details.\n\n"
            "## Acceptance\n"
            "- Define hard requirements such as minimum score, required tests, runtime limit, memory limit, and leakage checks.\n\n"
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

    try:
        from coral.hub.kb import ensure_kb

        # Seeds manuals and ensures the new external/practice indices exist.
        ensure_kb(knowledge_dir.parent.parent)
    except Exception:
        logger.debug("Failed to seed kb manuals", exc_info=True)


def _copytree_replace(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.copytree(src, dst, symlinks=True)


def _snapshot_file_or_dir(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_dir():
        _copytree_replace(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _prepare_snapshots(
    run_dir: Path,
    task_source_dir: Path,
    config: CoralConfig,
) -> Path:
    """Freeze task inputs that define the run's meaning."""
    snapshots_dir = run_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    _snapshot_file_or_dir(task_source_dir / "task.yaml", snapshots_dir / "task.yaml")
    _snapshot_file_or_dir(task_source_dir / "seed", snapshots_dir / "seed")
    _snapshot_file_or_dir(task_source_dir / "grader", snapshots_dir / "grader")

    knowledge_src = Path(config.knowledge.path).expanduser()
    if not knowledge_src.is_absolute():
        knowledge_src = (task_source_dir / knowledge_src).resolve()
    if config.knowledge.snapshot and knowledge_src.exists():
        _snapshot_file_or_dir(knowledge_src, snapshots_dir / "knowledge")
    else:
        _ensure_knowledge_base(snapshots_dir / "knowledge")

    return snapshots_dir


def _seed_active_knowledge(coral_dir: Path, snapshots_dir: Path, config: CoralConfig) -> None:
    """Seed the active knowledge base from the frozen snapshot."""
    src = snapshots_dir / "knowledge"
    dst = coral_dir / "public" / "knowledge"
    if src.exists():
        _copytree_replace(src, dst)
    _ensure_knowledge_base(dst)


def _expose_l1_grader_assets(task_source_dir: Path, repo_dir: Path) -> None:
    """Expose the grader package inside the agent repo for open L1 tasks."""
    grader_dir = task_source_dir / "grader"
    if not grader_dir.is_dir():
        return
    dst = repo_dir / "grader"
    _copytree_replace(grader_dir, dst)
    logger.info("Exposed L1 grader assets in repo: grader/")
    _commit_staged_changes(repo_dir, "Expose L1 grader assets")


def create_project(config: CoralConfig, config_dir: Path | None = None) -> ProjectPaths:
    """Create the full project directory structure.

    Each run gets its own clone of the source repo so runs are fully independent.

    Layout:
        results/
        └── <task-slug>/
            ├── latest -> 2026-03-11_163000   (symlink)
            └── <timestamp>/
                ├── .coral/
                │   ├── public/          # contents symlinked into .claude/ in worktrees
                │   │   ├── CLAUDE.md
                │   │   ├── notes/
                │   │   ├── change_summary.md
                │   │   ├── skills/
                │   │   ├── agents/
                │   │   ├── attempts/
                │   │   ├── logs/
                │   │   └── settings.local.json
                │   ├── private/
                │   └── config.yaml
                ├── repo/                # cloned from source
                └── agents/              # worktrees off repo/
    """
    # Resolve task directory for relative path resolution. User-facing task
    # YAML paths should be relative to the config/task directory, not whatever
    # shell directory happens to launch CORAL.
    effective_config_dir = (config.task_dir or config_dir or Path.cwd()).resolve()
    task_source_dir = effective_config_dir

    results_dir_path = Path(config.workspace.results_dir).expanduser()
    if not results_dir_path.is_absolute():
        results_dir_path = effective_config_dir / results_dir_path
    results_dir = results_dir_path.resolve()

    source_repo = Path(config.workspace.repo_path).expanduser()
    if not source_repo.is_absolute():
        source_repo = effective_config_dir / source_repo
    source_repo = source_repo.resolve()

    task_slug = slugify(config.task.name)
    task_dir = results_dir / task_slug

    # Use explicit run_dir if provided, otherwise generate timestamped one
    if config.workspace.run_dir:
        run_dir_path = Path(config.workspace.run_dir).expanduser()
        if not run_dir_path.is_absolute():
            run_dir_path = effective_config_dir / run_dir_path
        run_dir = run_dir_path.resolve()
        task_dir = run_dir.parent
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_dir = task_dir / timestamp
    coral_dir = run_dir / ".coral"
    agents_dir = run_dir / "agents"
    run_repo = run_dir / "repo"
    snapshots_dir = run_dir / "snapshots"

    logger.debug(f"results_dir={results_dir}, task_dir={task_dir}, run_dir={run_dir}")

    # Create shared state directories under public/.
    (coral_dir / "public").mkdir(parents=True, exist_ok=True)
    (coral_dir / "private").mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)

    _build_public_subtree(
        coral_dir,
        coral_dir / "public",
        effective_config_dir,
        list(config.agents.skills),
    )

    snapshots_dir = _prepare_snapshots(run_dir, task_source_dir, config)
    _seed_active_knowledge(coral_dir, snapshots_dir, config)

    # Save config
    original_config_run_dir = config.workspace.run_dir
    config.workspace.run_dir = str(run_dir)
    try:
        config.to_yaml(coral_dir / "config.yaml")
    finally:
        config.workspace.run_dir = original_config_run_dir

    # Save config_dir so resume can restore task_dir for relative path resolution
    (coral_dir / "config_dir").write_text(str(effective_config_dir))

    # Create/update "latest" symlink at task_dir/latest -> this run directory
    latest_link = task_dir / "latest"
    if latest_link.is_symlink():
        latest_link.unlink()
    if not latest_link.exists():
        rel = os.path.relpath(run_dir, task_dir)
        latest_link.symlink_to(rel)
        logger.info(f"Symlinked {latest_link} -> {rel}")

    # Clone source repo into run_dir/repo/
    repo_dir = clone_or_init_repo(source_repo, run_repo)

    # Auto-copy seed/ into repo (if present in task directory)
    seed_dir = task_source_dir / "seed"
    if seed_dir.is_dir():
        copy_seed_directory(seed_dir, repo_dir)
    if config.evaluation.level == "L1":
        _expose_l1_grader_assets(task_source_dir, repo_dir)

    # Copy private grader data into .coral/ (hidden from agents). L3 final
    # assets live in the same sealed private root for post-search human/Codex
    # validation; they are not part of the default agent eval loop.
    private_paths = list(config.grader.private)
    if config.evaluation.level == "L3":
        private_paths.extend(config.grader.final.private)
    if private_paths:
        copy_private_data(private_paths, coral_dir, config_dir or Path.cwd())

    # Bootstrap the grader's isolated venv at .coral/private/grader_venv/ and
    # run any user-supplied install steps.
    from coral.workspace.grader_env import setup_grader_env

    setup_grader_env(coral_dir, config.grader, config_dir or Path.cwd())

    return ProjectPaths(
        results_dir=results_dir,
        task_dir=task_dir,
        run_dir=run_dir,
        coral_dir=coral_dir,
        agents_dir=agents_dir,
        repo_dir=repo_dir,
        snapshots_dir=snapshots_dir,
    )


def reconstruct_paths(coral_dir: Path) -> ProjectPaths:
    """Reconstruct ProjectPaths from an existing .coral directory.

    Used by `coral resume` to rebuild paths without creating a new run.
    """
    coral_dir = coral_dir.resolve()
    run_dir = coral_dir.parent
    task_dir = run_dir.parent
    results_dir = task_dir.parent

    return ProjectPaths(
        results_dir=results_dir,
        task_dir=task_dir,
        run_dir=run_dir,
        coral_dir=coral_dir,
        agents_dir=run_dir / "agents",
        repo_dir=run_dir / "repo",
        snapshots_dir=run_dir / "snapshots",
    )
