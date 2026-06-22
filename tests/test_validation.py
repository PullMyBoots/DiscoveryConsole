"""Tests for task-directory validation (coral validate / coral start)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from coral.cli.author import _cmd_validate_run_dir, cmd_init
from coral.cli.validation import validate_task
from coral.hub.attempts import write_attempt
from coral.hub.readiness import build_control_readiness
from coral.types import Attempt

_TASK_YAML = """\
task:
  name: t
  description: d
grader:
{grader_body}
agents:
  count: 1
"""


def _make_task(base: Path, grader_body: str) -> Path:
    task_dir = base / "task"
    task_dir.mkdir()
    (task_dir / "task.yaml").write_text(_TASK_YAML.format(grader_body=grader_body))
    return task_dir


def test_validate_accepts_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        task_dir = _make_task(Path(d), '  entrypoint: "my_pkg.grader:Grader"')
        assert validate_task(task_dir) == []


def test_validate_rejects_missing_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        task_dir = _make_task(Path(d), "  timeout: 60")
        errors = validate_task(task_dir)
        assert any("No grader configured" in e for e in errors)


def test_validate_rejects_malformed_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        task_dir = _make_task(Path(d), "  entrypoint: my_pkg.grader.Grader")
        errors = validate_task(task_dir)
        assert any("module.path:ClassName" in e for e in errors)


def test_init_creates_research_workbench_knowledge_skeleton(tmp_path):
    task_dir = tmp_path / "my-task"

    cmd_init(SimpleNamespace(path=str(task_dir), name="My Task"))

    knowledge_dir = task_dir / "knowledge"
    expected_dirs = [
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
    ]
    for rel in expected_dirs:
        assert (knowledge_dir / rel).is_dir(), rel
    assert "Read Order" in (knowledge_dir / "index.md").read_text()
    assert "Active Routes" in (knowledge_dir / "maps" / "methods.md").read_text()
    assert "Global Knowledge Packet" in (knowledge_dir / "packs" / "global.md").read_text()
    assert (knowledge_dir / "manifest.jsonl").read_text() == ""
    eval_spec = (knowledge_dir / "eval_spec.md").read_text()
    assert "Breakthrough Metrics" in eval_spec
    assert "Guardrail Metrics" in eval_spec
    assert "Anti-Cheating and Overfitting Checks" in eval_spec
    assert "Scalar Score" in eval_spec
    assert "Eval Profiles" in eval_spec
    assert "eval_spec.md" in (knowledge_dir / "index.md").read_text()


def _write_ready_run(coral_dir: Path) -> None:
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "sources" / "papers").mkdir(parents=True)
    (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
    (knowledge_dir / "eval_spec.md").write_text(
        "# Eval Spec\n\n"
        "## Breakthrough Metrics\n"
        "Improve the target metric.\n\n"
        "## Guardrail Metrics\n"
        "Keep safety above the floor.\n\n"
        "## Anti-cheating Checks\n"
        "Prevent leakage and overfit.\n"
    )
    (knowledge_dir / "sources" / "papers" / "paper.md").write_text("# Paper\n")
    (knowledge_dir / "manifest.jsonl").write_text(
        '{"title":"Paper","relative_path":"sources/papers/paper.md","category":"papers"}\n'
    )
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").write_text(
        "# Agent 1\n\nTry the baseline first.\n"
    )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 1},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="baseline",
            agent_id="baseline",
            title="baseline",
            score=0.5,
            status="completed",
            parent_hash=None,
            timestamp="2026-06-01T10:00:00Z",
            metadata={"baseline": True},
        ),
    )


def test_build_control_readiness_accepts_prepared_timestamp(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)

    readiness = build_control_readiness(coral_dir)

    assert readiness["status"] == "ready"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["eval_spec"]["status"] == "ready"
    assert checks["knowledge"]["status"] == "ready"
    assert checks["baseline"]["status"] == "ready"
    assert checks["agent_briefs"]["status"] == "ready"


def test_build_control_readiness_rejects_multi_island_empty_island(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "briefs" / "islands").mkdir(parents=True, exist_ok=True)
    (knowledge_dir / "briefs" / "islands" / "0.md").write_text(
        "# Island 0\n\nSparse route.\n"
    )
    (knowledge_dir / "briefs" / "islands" / "1.md").write_text(
        "# Island 1\n\nRobustness route.\n"
    )
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").unlink()
    (knowledge_dir / "briefs" / "agent-seeds" / "0-agent-1.md").write_text(
        "# 0-agent-1\n\nWork on sparse route.\n"
    )
    (knowledge_dir / "briefs" / "agent-seeds" / "0-agent-2.md").write_text(
        "# 0-agent-2\n\nTry an alternate sparse route.\n"
    )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 2},
                "islands": {"count": 2},
            }
        )
    )

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["agent_briefs"]["status"] == "ready"
    assert checks["island_themes"]["status"] == "ready"
    assert checks["island_agents"]["status"] == "missing"
    assert "island(s): 1" in checks["island_agents"]["detail"]


def test_build_control_readiness_accepts_public_baseline_in_multi_island_run(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "briefs" / "islands").mkdir(parents=True, exist_ok=True)
    (knowledge_dir / "briefs" / "islands" / "0.md").write_text("# Island 0\n\nSparse route.\n")
    (knowledge_dir / "briefs" / "islands" / "1.md").write_text("# Island 1\n\nRobust route.\n")
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").unlink()
    (knowledge_dir / "briefs" / "agent-seeds" / "0-agent-1.md").write_text(
        "# 0-agent-1\n\nisland_id: 0\n\nSparse route.\n"
    )
    (knowledge_dir / "briefs" / "agent-seeds" / "1-agent-1.md").write_text(
        "# 1-agent-1\n\nisland_id: 1\n\nRobust route.\n"
    )
    (coral_dir / "islands" / "0" / "attempts").mkdir(parents=True)
    (coral_dir / "islands" / "1" / "attempts").mkdir(parents=True)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 2},
                "islands": {"count": 2},
            }
        )
    )

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "ready"
    assert checks["baseline"]["status"] == "ready"


def test_cli_validate_run_dir_reports_ready_for_prepared_timestamp(tmp_path, capsys):
    run_dir = tmp_path / "results" / "ready-task" / "run-1"
    coral_dir = run_dir / ".coral"
    _write_ready_run(coral_dir)

    _cmd_validate_run_dir(run_dir)

    out = capsys.readouterr().out
    assert "Readiness: READY" in out
    assert "Run readiness: OK" in out


def test_cli_validate_run_dir_exits_for_missing_artifacts(tmp_path):
    coral_dir = tmp_path / "run" / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        _cmd_validate_run_dir(coral_dir)

    assert exc.value.code == 1
