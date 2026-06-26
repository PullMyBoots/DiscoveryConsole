"""Tests for workspace setup."""

import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

from coral.config import (
    AgentConfig,
    CoralConfig,
    EvaluationConfig,
    GraderConfig,
    TaskConfig,
    WorkspaceConfig,
)
from coral.workspace import (
    apply_runtime_mounts,
    create_project,
    setup_codex_settings,
    setup_gitignore,
    setup_instruction_links,
    setup_shared_state,
    setup_worktree_env,
    write_agent_id,
)


def _make_config(repo_path: str, results_dir: str | None = None) -> CoralConfig:
    return CoralConfig(
        task=TaskConfig(name="Test Task", description="Test task"),
        grader=GraderConfig(),
        agents=AgentConfig(count=2),
        workspace=WorkspaceConfig(
            results_dir=results_dir or os.path.join(repo_path, "results"),
            repo_path=repo_path,
        ),
    )


def _git_init(d: str) -> None:
    """Initialise a git repo with a dummy commit (works without global config)."""
    subprocess.run(["git", "init", d], capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            d,
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@test.com",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        capture_output=True,
        check=True,
    )


def test_create_project_structure():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        # Init a git repo so workspace can create worktrees
        _git_init(d)

        config = _make_config(d)
        paths = create_project(config)

        assert paths.run_dir.exists()
        assert paths.task_dir.exists()
        assert paths.coral_dir.exists()
        assert (paths.coral_dir / "public").is_dir()
        assert (paths.coral_dir / "public" / "attempts").is_dir()
        assert (paths.coral_dir / "public" / "logs").is_dir()
        assert (paths.coral_dir / "public" / "skills").is_dir()
        assert (paths.coral_dir / "public" / "knowledge").is_dir()
        assert (paths.coral_dir / "public" / "knowledge" / "practice" / "agents").is_dir()
        assert (paths.coral_dir / "public" / "jobs").is_dir()
        assert (paths.coral_dir / "public" / "datasets").is_dir()
        assert not (paths.coral_dir / "public" / "roles").exists()
        assert not (paths.coral_dir / "public" / "notes").exists()
        assert (paths.coral_dir / "private").is_dir()
        assert (paths.coral_dir / "config.yaml").is_file()
        assert paths.snapshots_dir.exists()
        assert (paths.snapshots_dir / "knowledge").is_dir()
        assert paths.agents_dir.exists()
        # Structure: results/<task-slug>/<timestamp>/
        assert "test-task" in str(paths.task_dir)
        # latest symlink
        latest = paths.task_dir / "latest"
        assert latest.is_symlink()


def test_create_project_unique_runs():
    """Each create_project call gets a unique run directory."""
    # ignore_cleanup_errors: git's .git/objects can have background writes
    # racing with rmtree at exit. The test logic is complete by then.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        _git_init(d)

        config = _make_config(d)
        paths1 = create_project(config)

        import time

        time.sleep(1.1)  # ensure different timestamp

        paths2 = create_project(config)

        assert paths1.run_dir != paths2.run_dir
        assert paths1.coral_dir != paths2.coral_dir
        # latest should point to the second run directory
        latest = paths1.task_dir / "latest"
        assert latest.resolve() == paths2.run_dir.resolve()


def test_create_project_snapshots_and_seeds_knowledge():
    """Task knowledge is frozen per run and seeded into the shared knowledge base."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        repo = root / "repo"
        _git_init(str(repo))

        (root / "task.yaml").write_text("task:\n  name: Test Task\n  description: d\n")
        paper_dir = root / "knowledge" / "external" / "items" / "src-001"
        paper_dir.mkdir(parents=True)
        (paper_dir / "source.md").write_text("# Paper A\n")
        (root / "knowledge" / "external" / "index.jsonl").write_text(
            '{"id":"src-001","kind":"paper","title":"Paper A","status":"active","source":"https://example.com/a","item_path":"external/items/src-001"}\n'
        )

        config = CoralConfig(
            task=TaskConfig(name="Test Task", description="Test task"),
            grader=GraderConfig(),
            agents=AgentConfig(count=1),
            workspace=WorkspaceConfig(results_dir=str(root / "results"), repo_path=str(repo)),
        )

        paths = create_project(config, config_dir=root)

        assert (paths.snapshots_dir / "task.yaml").read_text().startswith("task:")
        assert (paths.snapshots_dir / "knowledge" / "external" / "items" / "src-001" / "source.md").read_text() == "# Paper A\n"
        assert (paths.coral_dir / "public" / "knowledge" / "external" / "items" / "src-001" / "source.md").read_text() == "# Paper A\n"
        assert (paths.coral_dir / "public" / "knowledge" / "practice" / "agents").is_dir()
        assert not (paths.coral_dir / "public" / "knowledge" / "capsules").exists()
        assert not (paths.coral_dir / "public" / "knowledge" / "maps").exists()
        assert not (paths.coral_dir / "public" / "knowledge" / "packs").exists()


def test_l1_exposes_grader_assets_in_agent_repo():
    """L1 tasks make the scoring mechanism visible in the run repo."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        repo = root / "repo"
        _git_init(str(repo))
        (root / "grader" / "src").mkdir(parents=True)
        (root / "grader" / "src" / "visible_grader.py").write_text("# visible\n")

        config = CoralConfig(
            task=TaskConfig(name="Open Eval", description="d"),
            evaluation=EvaluationConfig(level="L1"),
            grader=GraderConfig(entrypoint="visible_grader:Grader"),
            workspace=WorkspaceConfig(results_dir=str(root / "results"), repo_path=str(repo)),
        )
        paths = create_project(config, config_dir=root)

        assert (paths.repo_dir / "grader" / "src" / "visible_grader.py").read_text() == "# visible\n"


def test_l2_does_not_expose_grader_assets_in_agent_repo():
    """L2 keeps grader package outside the agent-visible run repo by default."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        repo = root / "repo"
        _git_init(str(repo))
        (root / "grader" / "src").mkdir(parents=True)
        (root / "grader" / "src" / "hidden_grader.py").write_text("# hidden\n")

        config = CoralConfig(
            task=TaskConfig(name="Hidden Eval", description="d"),
            grader=GraderConfig(entrypoint="hidden_grader:Grader"),
            workspace=WorkspaceConfig(results_dir=str(root / "results"), repo_path=str(repo)),
        )
        paths = create_project(config, config_dir=root)

        assert not (paths.repo_dir / "grader").exists()


def test_write_agent_id():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d)
        write_agent_id(worktree, "agent-42")
        content = (worktree / ".coral_agent_id").read_text()
        assert content == "agent-42"


def test_setup_gitignore():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d)
        setup_gitignore(worktree)

        gitignore = worktree / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".coral/" in content
        assert ".coral_agent_id" in content
        assert "CLAUDE.md" in content
        assert "CORAL_OVERVIEW.md" in content
        assert "CORAL_LOOPS.md" in content
        assert "CORAL_SHARED" in content
        assert ".claude/" in content


def test_setup_gitignore_preserves_existing():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d)
        gitignore = worktree / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n")

        setup_gitignore(worktree)

        content = gitignore.read_text()
        assert "*.pyc" in content
        assert ".coral_agent_id" in content
        assert ".claude/" in content


def test_setup_gitignore_idempotent():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d)
        setup_gitignore(worktree)
        setup_gitignore(worktree)

        content = (worktree / ".gitignore").read_text()
        assert content.count(".claude/") == 1
        assert content.count("CORAL_SHARED") == 1


@pytest.mark.parametrize(
    ("research", "expected"),
    [
        (True, "live"),
        (False, "disabled"),
    ],
)
def test_setup_codex_settings_writes_top_level_web_search(
    research: bool,
    expected: str,
):
    """Codex expects web_search as a top-level mode, not under [tools]."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        coral_dir = Path(d) / ".coral"
        worktree.mkdir()
        coral_dir.mkdir()

        setup_codex_settings(worktree, coral_dir, research=research)

        config_toml = (worktree / ".codex" / "config.toml").read_text()
        config = tomllib.loads(config_toml)
        assert config["web_search"] == expected
        assert config["sandbox_mode"] == "workspace-write"
        assert "danger-full-access" not in config_toml
        assert str(worktree.resolve()) in config["sandbox_workspace_write"]["writable_roots"]
        assert str((coral_dir / "public").resolve()) in config["sandbox_workspace_write"]["writable_roots"]
        assert "tools" not in config


def test_create_project_runs_setup_commands():
    """Setup commands execute in the worktree directory."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()

        setup_worktree_env(worktree, ["echo hello > setup_marker.txt"])

        marker = worktree / "setup_marker.txt"
        assert marker.exists()
        assert marker.read_text().strip() == "hello"


def test_create_project_setup_command_failure():
    """A failing setup command raises RuntimeError."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()

        with pytest.raises(RuntimeError, match="Setup command failed"):
            setup_worktree_env(worktree, ["exit 1"])


def test_create_project_setup_runs_sequentially():
    """Setup commands run in order so later commands can depend on earlier ones."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()

        setup_worktree_env(
            worktree,
            [
                "mkdir -p mydir",
                "echo done > mydir/result.txt",
            ],
        )

        result_file = worktree / "mydir" / "result.txt"
        assert result_file.exists()
        assert result_file.read_text().strip() == "done"


def test_setup_worktree_env_skips_when_venv_exists():
    """Idempotent: if .venv/bin/python already exists, setup is skipped.

    Avoids re-running uv sync on every interrupt-and-resume cycle, which
    can otherwise dominate restart latency.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()
        # Pre-create a fake populated venv
        venv_bin = worktree / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("#!/bin/sh\nexit 0\n")
        (venv_bin / "python").chmod(0o755)

        # If setup ran, this would create the marker file
        marker = worktree / "setup_ran.marker"
        setup_worktree_env(worktree, [f"touch {marker}"])

        assert not marker.exists(), "Setup should have been skipped"


def test_setup_worktree_env_runs_when_venv_missing():
    """When .venv doesn't exist yet, setup runs as normal (first launch path)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()

        marker = worktree / "setup_ran.marker"
        setup_worktree_env(worktree, [f"touch {marker}"])

        assert marker.exists(), "Setup should have run on first launch"


# --- apply_runtime_mounts tests ---


def _mount_workspace(d: Path) -> tuple[Path, Path]:
    """Create a worktree dir and a base_dir under d; return both."""
    worktree = d / "worktree"
    worktree.mkdir()
    base = d / "base"
    base.mkdir()
    return worktree, base


def test_apply_runtime_mounts_no_mounts_is_noop():
    """Empty/missing mounts must not error or touch the worktree."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        before = sorted(worktree.iterdir())
        apply_runtime_mounts(worktree, {}, base)
        apply_runtime_mounts(worktree, None, base)  # type: ignore[arg-type]
        assert sorted(worktree.iterdir()) == before


def test_apply_runtime_mounts_copies_file_with_relative_source():
    """Relative source resolves against base_dir; dest is worktree-relative."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        (base / "settings.json").write_text('{"foo": 1}')

        apply_runtime_mounts(
            worktree,
            {"settings.json": ".claude/settings.json"},
            base,
        )

        dest = worktree / ".claude" / "settings.json"
        assert dest.exists()
        assert dest.read_text() == '{"foo": 1}'


def test_apply_runtime_mounts_absolute_source():
    """Absolute source is used as-is (base_dir ignored)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        elsewhere = Path(d) / "elsewhere"
        elsewhere.mkdir()
        src = elsewhere / "src.json"
        src.write_text("absolute")

        apply_runtime_mounts(worktree, {str(src): ".claude/settings.json"}, base)

        assert (worktree / ".claude" / "settings.json").read_text() == "absolute"


def test_apply_runtime_mounts_expands_tilde(monkeypatch):
    """``~`` in source expands to $HOME."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        fake_home = Path(d) / "fake_home"
        fake_home.mkdir()
        (fake_home / "settings.json").write_text("from-home")
        monkeypatch.setenv("HOME", str(fake_home))

        apply_runtime_mounts(
            worktree,
            {"~/settings.json": ".claude/settings.json"},
            base,
        )

        assert (worktree / ".claude" / "settings.json").read_text() == "from-home"


def test_apply_runtime_mounts_copies_directory():
    """Directory sources copy recursively."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        srcdir = base / "mcp"
        srcdir.mkdir()
        (srcdir / "db.json").write_text("db config")
        (srcdir / "fs.json").write_text("fs config")

        apply_runtime_mounts(worktree, {"mcp": ".claude/mcp"}, base)

        dest = worktree / ".claude" / "mcp"
        assert (dest / "db.json").read_text() == "db config"
        assert (dest / "fs.json").read_text() == "fs config"


def test_apply_runtime_mounts_overwrites_existing_file():
    """Existing dest is overwritten — second invocation refreshes."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        src = base / "settings.json"
        src.write_text("v1")

        apply_runtime_mounts(worktree, {"settings.json": ".claude/settings.json"}, base)
        src.write_text("v2")
        apply_runtime_mounts(worktree, {"settings.json": ".claude/settings.json"}, base)

        assert (worktree / ".claude" / "settings.json").read_text() == "v2"


def test_apply_runtime_mounts_overwrites_corals_settings_local_json():
    """User can replace CORAL's settings.local.json (mounts run last, user wins)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        # Simulate CORAL having already written settings.local.json
        (worktree / ".claude").mkdir()
        (worktree / ".claude" / "settings.local.json").write_text('{"coral": true}')

        (base / "user-settings.json").write_text('{"user": true}')

        apply_runtime_mounts(
            worktree,
            {"user-settings.json": ".claude/settings.local.json"},
            base,
        )

        assert (worktree / ".claude" / "settings.local.json").read_text() == '{"user": true}'


def test_apply_runtime_mounts_missing_source_raises():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        with pytest.raises(FileNotFoundError, match="mount source"):
            apply_runtime_mounts(worktree, {"nope.json": ".claude/x.json"}, base)


def test_apply_runtime_mounts_absolute_dest_rejected():
    """Dest must be worktree-relative — absolute paths are rejected."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        (base / "src").write_text("x")
        with pytest.raises(ValueError, match="must be worktree-relative"):
            apply_runtime_mounts(worktree, {"src": "/etc/passwd"}, base)


def test_apply_runtime_mounts_dest_escape_rejected():
    """Dest cannot escape the worktree via ``..``."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        (base / "src").write_text("x")
        with pytest.raises(ValueError, match="escapes worktree"):
            apply_runtime_mounts(worktree, {"src": "../escape.txt"}, base)


def test_apply_runtime_mounts_creates_parent_dirs():
    """Nested dest paths get their parent directories created."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        (base / "src.json").write_text("nested")

        apply_runtime_mounts(
            worktree,
            {"src.json": "deeply/nested/dir/file.json"},
            base,
        )

        assert (worktree / "deeply" / "nested" / "dir" / "file.json").read_text() == "nested"


def test_apply_runtime_mounts_multiple_files():
    """All entries in the mounts dict get copied."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree, base = _mount_workspace(Path(d))
        (base / "a.json").write_text("A")
        (base / "b.json").write_text("B")

        apply_runtime_mounts(
            worktree,
            {
                "a.json": ".claude/a.json",
                "b.json": ".claude/b.json",
            },
            base,
        )

        assert (worktree / ".claude" / "a.json").read_text() == "A"
        assert (worktree / ".claude" / "b.json").read_text() == "B"


def test_setup_shared_state_does_not_symlink_roles():
    """The removed role mechanism is not exposed in agent worktrees."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        coral_dir = Path(d) / ".coral"
        (coral_dir / "public").mkdir(parents=True)
        worktree = Path(d) / "worktree"
        worktree.mkdir()

        setup_shared_state(worktree, coral_dir, ".claude")

        assert not (worktree / ".claude" / "roles").exists()
        assert not (worktree / ".claude" / "notes").exists()
        assert not (coral_dir / "public" / "roles").exists()
        assert not (coral_dir / "public" / "notes").exists()
        assert (worktree / ".claude" / "jobs").is_symlink()
        assert (worktree / ".claude" / "datasets").is_symlink()
        assert (worktree / ".claude" / "control").is_symlink()
        assert (coral_dir / "public" / "jobs").is_dir()
        assert (coral_dir / "public" / "datasets").is_dir()
        assert (coral_dir / "public" / "control").is_dir()


def test_setup_instruction_links_exposes_reusable_manuals():
    """Root-level manual links point into the shared knowledge manuals."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()

        setup_instruction_links(worktree, ".claude")

        overview = worktree / "CORAL_OVERVIEW.md"
        loops = worktree / "CORAL_LOOPS.md"
        shared = worktree / "CORAL_SHARED"
        assert overview.is_symlink()
        assert loops.is_symlink()
        assert shared.is_symlink()
        assert os.readlink(overview) == ".claude/knowledge/manuals/coral-overview-cli.md"
        assert os.readlink(loops) == ".claude/knowledge/manuals/agent-loops.md"
        assert os.readlink(shared) == ".claude"


def test_setup_instruction_links_preserves_user_files():
    """Unexpected real files with these names are not overwritten."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        worktree = Path(d) / "worktree"
        worktree.mkdir()
        existing = worktree / "CORAL_OVERVIEW.md"
        existing.write_text("user-owned\n")

        setup_instruction_links(worktree, ".claude")

        assert not existing.is_symlink()
        assert existing.read_text() == "user-owned\n"
        assert (worktree / "CORAL_LOOPS.md").is_symlink()


def test_create_project_seeds_user_skills():
    """agents.skills directories are copied into .coral/public/skills/."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        _git_init(d)
        root = Path(d)

        skill_name = "test-skill"
        skill_dir = root / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "run.sh").write_text("#!/bin/bash\necho hello")

        config = CoralConfig(
            task=TaskConfig(name="Test Task", description="Test task"),
            grader=GraderConfig(),
            agents=AgentConfig(count=1, skills=[f"./skills/{skill_name}"]),
            workspace=WorkspaceConfig(results_dir=str(root / "results"), repo_path=d),
        )
        paths = create_project(config, config_dir=root)

        seeded = paths.coral_dir / "public" / "skills" / skill_name / "run.sh"
        assert seeded.is_file()
        assert "echo hello" in seeded.read_text()


def test_create_project_user_skills_override_builtin():
    """User skills with the same name as a built-in skill take precedence."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        _git_init(d)
        root = Path(d)

        skill_name = "coral-workflow"
        skill_dir = root / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "custom.txt").write_text("user version")

        config = CoralConfig(
            task=TaskConfig(name="Test Task", description="Test task"),
            grader=GraderConfig(),
            agents=AgentConfig(count=1, skills=[f"./skills/{skill_name}"]),
            workspace=WorkspaceConfig(results_dir=str(root / "results"), repo_path=d),
        )
        paths = create_project(config, config_dir=root)

        dst = paths.coral_dir / "public" / "skills" / skill_name
        assert (dst / "custom.txt").read_text() == "user version"
