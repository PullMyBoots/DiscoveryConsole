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
from coral.hub._island import island_root
from coral.hub.checkpoint import init_checkpoint_repo
from coral.workspace.repo import (
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
_ROLE_TEMPLATE_PATH = Path(__file__).parent.parent / "template" / "role_template.md"


_PER_ISLAND_SUBDIRS = (
    "attempts",
    "logs",
    "skills",
    "agents",
    "knowledge",
    "heartbeat",
    "eval_logs",
    "roles",
)


_KNOWLEDGE_SUBDIRS = (
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


def _island_id_from_root(coral_dir: Path, island_root: Path) -> str | None:
    """Return the island_id implied by island_root, or None for single-island."""
    try:
        rel = island_root.resolve().relative_to((coral_dir / "islands").resolve())
        # rel is like Path("0"); take first segment
        rel_str = str(rel)
        return rel_str.split("/", 1)[0] if rel_str and rel_str != "." else None
    except ValueError:
        return None


def _build_island_subtree(
    coral_dir: Path,
    island_root: Path,
    effective_config_dir: Path,
    user_skill_paths: list[str],
) -> None:
    """Create the per-island state directory tree and seed bundled assets.

    Used once for ``public/`` in single-island mode, and once per
    ``islands/<id>/`` in multi-island mode. Seeds bundled skills + bundled
    subagent templates + initializes the checkpoint git repo for this island.
    """
    for sub in _PER_ISLAND_SUBDIRS:
        (island_root / sub).mkdir(parents=True, exist_ok=True)
    _ensure_knowledge_base(island_root / "knowledge")
    _link_legacy_notes_dir(island_root)

    # Seed bundled skills from coral/template/skills/
    if _SEED_SKILLS_DIR.is_dir():
        for skill_dir in _SEED_SKILLS_DIR.iterdir():
            if skill_dir.is_dir():
                dst = island_root / "skills" / skill_dir.name
                if not dst.exists():
                    shutil.copytree(skill_dir, dst)
                    logger.info(f"Seeded skill: {skill_dir.name}")

    # Seed user-provided skills from agents.skills config
    for skill_path in user_skill_paths:
        src = Path(skill_path)
        if not src.is_absolute():
            src = (effective_config_dir / src).resolve()
        if src.is_dir():
            dst = island_root / "skills" / src.name
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
                dst = island_root / "agents" / agent_file.name
                if not dst.exists():
                    shutil.copy2(agent_file, dst)
                    logger.info(f"Seeded agent template: {agent_file.name}")

    # Per-island checkpoint git repo (one .git per island, scoped locks)
    init_checkpoint_repo(
        str(coral_dir),
        island_id=_island_id_from_root(coral_dir, island_root),
    )


def _ensure_knowledge_base(knowledge_dir: Path) -> None:
    """Create the unified knowledge-base skeleton.

    The old CORAL convention exposed ``notes/`` as the top-level knowledge
    surface. The new layout keeps notes under ``knowledge/notes/`` and keeps
    raw/source artifacts, briefs, and inbox material in the same durable tree.
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    for sub in _KNOWLEDGE_SUBDIRS:
        (knowledge_dir / sub).mkdir(parents=True, exist_ok=True)

    index = knowledge_dir / "index.md"
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

    eval_spec = knowledge_dir / "eval_spec.md"
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

    manifest = knowledge_dir / "manifest.jsonl"
    if not manifest.exists():
        manifest.write_text("")

    notes_index = knowledge_dir / "notes" / "index.md"
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

    methods_map = knowledge_dir / "maps" / "methods.md"
    if not methods_map.exists():
        methods_map.write_text(
            "# Method Map\n\n"
            "Keep this file short. List only active method families that should guide agent search.\n\n"
            "## Active Routes\n"
            "- (none yet)\n\n"
            "## Failed Or Risky Routes\n"
            "- (none yet)\n"
        )

    global_pack = knowledge_dir / "packs" / "global.md"
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


def _link_legacy_notes_dir(island_root: Path) -> None:
    """Expose ``notes`` as a compatibility alias for ``knowledge/notes``."""
    notes_dir = island_root / "notes"
    target = island_root / "knowledge" / "notes"

    if notes_dir.is_symlink():
        notes_dir.unlink()
    elif notes_dir.exists():
        # Preserve any pre-existing files by moving them into knowledge/notes.
        target.mkdir(parents=True, exist_ok=True)
        for entry in notes_dir.iterdir():
            dst = target / entry.name
            if dst.exists():
                continue
            shutil.move(str(entry), str(dst))
        try:
            notes_dir.rmdir()
        except OSError:
            return

    if not notes_dir.exists():
        rel = os.path.relpath(target.resolve(), island_root.resolve())
        notes_dir.symlink_to(rel)


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
    """Seed each island's active knowledge base from the frozen snapshot."""
    src = snapshots_dir / "knowledge"
    if config.islands.count == 1:
        destinations = [coral_dir / "public" / "knowledge"]
    else:
        destinations = [coral_dir / "islands" / str(i) / "knowledge" for i in range(config.islands.count)]

    for dst in destinations:
        if src.exists():
            _copytree_replace(src, dst)
        _ensure_knowledge_base(dst)
        _link_legacy_notes_dir(dst.parent)


def seed_agent_role(
    coral_dir: Path,
    agent_id: str,
    source: str | None = None,
    base_dir: Path | None = None,
    *,
    island_id: str | int | None = None,
) -> Path:
    """Write the per-agent role description at the per-island roles dir.

    The role describes *what the agent does* on the team — its posture, lane,
    objectives, and accumulated self-knowledge. It is mutable and evolves over
    the run.

    Resolves to ``coral_dir/public/roles/<agent_id>.md`` in single-island
    runs, or ``coral_dir/islands/<island_id>/roles/<agent_id>.md`` in
    multi-island runs. The latter matches the symlink installed by
    ``worktree.setup_shared_state``, so the agent's worktree
    ``.claude/roles/<agent_id>.md`` resolves to the file we write here.

    Idempotent: does nothing if the file already exists, so an agent's evolved
    role description is never clobbered by a re-setup or resume.

    When ``source`` is None (the default), renders the bundled gen-0 role
    template — every agent starts with a blank role they earn into.

    When ``source`` is set, copies that user-provided .md file as-is, giving
    each agent a custom starting posture. ``source`` is a host path with ``~``
    expansion; resolved against ``base_dir`` (typically the task directory)
    when not absolute. Matches the path convention of ``apply_runtime_mounts``.

    Raises:
        FileNotFoundError: if ``source`` is given but does not resolve to a file.
        ValueError: if ``source`` is given but ``base_dir`` is None and ``source``
            is a relative path.
    """
    roles_dir = island_root(coral_dir, island_id) / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    dst = roles_dir / f"{agent_id}.md"
    if dst.exists():
        return dst

    if source is None:
        template = _ROLE_TEMPLATE_PATH.read_text()
        rendered = template.format(
            agent_id=agent_id,
            created_at=datetime.now().isoformat(),
        )
        dst.write_text(rendered)
        return dst

    src = Path(source).expanduser()
    if not src.is_absolute():
        if base_dir is None:
            raise ValueError(
                f"role_file {source!r} is relative; base_dir is required to resolve it"
            )
        src = (base_dir / src).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"role_file {source!r} (resolved to {src}) does not exist")
    shutil.copy2(src, dst)
    return dst


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

    # Create shared state directories.
    # Single-island (count == 1): keep today's exact layout under public/.
    # Multi-island (count > 1):   build islands/<id>/ subtree per island, leave
    #                              public/ minimal (only global meta).
    (coral_dir / "public").mkdir(parents=True, exist_ok=True)
    (coral_dir / "private").mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)

    if config.islands.count == 1:
        _build_island_subtree(
            coral_dir,
            coral_dir / "public",
            effective_config_dir,
            list(config.agents.skills),
        )
    else:
        (coral_dir / "islands").mkdir(parents=True, exist_ok=True)
        for i in range(config.islands.count):
            island_root = coral_dir / "islands" / str(i)
            island_root.mkdir(parents=True, exist_ok=True)
            _build_island_subtree(
                coral_dir,
                island_root,
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

    # Copy private grader data into .coral/ (hidden from agents)
    if config.grader.private:
        copy_private_data(config.grader.private, coral_dir, config_dir or Path.cwd())

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
