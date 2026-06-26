"""Tests for the manager's seen-attempts initialization.

Regression: pending attempts left over from a previous manager (mid-grade
when we resumed) used to be captured in the initial `seen_attempts` set,
so when they later transitioned to scored they never appeared in
`new_attempts` and never triggered eval-completion dispatch. The fix initializes
`seen_attempts` to only the already-scored attempts so pending-at-startup
evals flow through the normal detection path.
"""

from __future__ import annotations

import json
from pathlib import Path

from coral.agent.manager import AgentManager
from coral.config import CoralConfig
from coral.template.coral_md import generate_coral_md
from coral.workspace import ProjectPaths


def _build_manager_with_attempts(tmp_path: Path, attempts: dict[str, str]):
    """Spin up a manager with a fake attempts directory pre-populated."""
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    (coral_dir / "public" / "logs").mkdir()
    for name, status in attempts.items():
        path = coral_dir / "public" / "attempts" / f"{name}.json"
        path.write_text(json.dumps({"agent_id": "agent-1", "status": status}))

    paths = ProjectPaths(
        results_dir=tmp_path / "results",
        task_dir=tmp_path,
        run_dir=tmp_path,
        snapshots_dir=tmp_path / "snapshots",
        coral_dir=coral_dir,
        agents_dir=tmp_path / "agents",
        repo_dir=tmp_path / "repo",
    )
    cfg = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
            "agents": {"runtime": "claude-code"},
        }
    )
    manager = AgentManager(cfg, verbose=False)
    manager.paths = paths
    return manager, paths


def test_pending_at_startup_is_not_seen(tmp_path: Path) -> None:
    """Pending attempts at startup must NOT be in the initial seen set so
    reflect_loop can fire when they later transition to scored."""
    manager, _ = _build_manager_with_attempts(
        tmp_path,
        {
            "scored1": "improved",
            "scored2": "regressed",
            "pending1": "pending",
        },
    )
    # This mirrors the initialization in monitor_loop: only already-scored
    # attempts are seen.
    seen = manager._filter_scored(manager._get_seen_attempts())
    assert "scored1.json" in seen
    assert "scored2.json" in seen
    assert "pending1.json" not in seen


def test_manager_reads_initialization_plan_for_instruction_injection(tmp_path: Path) -> None:
    """Manager should load the Codex-prepared plan that gets injected into prompts."""
    manager, paths = _build_manager_with_attempts(tmp_path, {})
    plan_dir = paths.coral_dir / "public" / "knowledge" / "briefs" / "agent-seeds"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "agent-1.md"
    plan_path.write_text(
        "# Runnable Initialization Plan: Agent 1\n\n"
        "Start from the sparse baseline and run the quick eval first.\n"
    )

    brief, brief_ref = manager._read_agent_seed_brief("agent-1", ".codex")
    md = generate_coral_md(
        manager.config,
        "agent-1",
        shared_dir=".codex",
        agent_seed_brief=brief,
        agent_seed_brief_path=brief_ref,
    )

    assert "Start from the sparse baseline" in brief
    assert brief_ref == ".codex/knowledge/briefs/agent-seeds/agent-1.md"
    assert "Codex-prepared runnable initialization bundle" in md
    assert "Start from the sparse baseline" in md
    assert ".codex/roles" not in md


def test_pending_then_scored_appears_as_new(tmp_path: Path) -> None:
    """A pending-at-startup attempt that later scores must be visible in
    `current - seen` so the dispatch path picks it up."""
    manager, paths = _build_manager_with_attempts(
        tmp_path,
        {
            "pending1": "pending",
        },
    )
    seen = manager._filter_scored(manager._get_seen_attempts())
    assert seen == set()  # nothing scored yet

    # Simulate the grader daemon finalizing the attempt.
    pending_path = paths.coral_dir / "public" / "attempts" / "pending1.json"
    pending_path.write_text(json.dumps({"agent_id": "agent-1", "status": "improved"}))

    current = manager._get_seen_attempts()
    new = current - seen
    scored_new = manager._filter_scored(new)
    assert scored_new == {"pending1.json"}


def test_already_scored_at_startup_does_not_re_fire(tmp_path: Path) -> None:
    """Attempts that were already scored when the manager came up must
    not be in the post-startup new set — we don't want to re-dispatch
    reflect_loop for historical evals."""
    manager, _ = _build_manager_with_attempts(
        tmp_path,
        {
            "scored1": "improved",
        },
    )
    seen = manager._filter_scored(manager._get_seen_attempts())
    current = manager._get_seen_attempts()
    new = current - seen
    assert new == set()


def test_get_seen_attempts_reads_public_attempts(tmp_path):
    """_get_seen_attempts scans public attempts."""
    from coral.agent.manager import AgentManager
    from coral.config import CoralConfig
    from coral.hub.attempts import write_attempt
    from coral.types import Attempt
    from coral.workspace.project import ProjectPaths

    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    a0 = Attempt(
        commit_hash="aaa",
        agent_id="agent-1",
        title="x",
        score=None,
        status="pending",
        parent_hash=None,
        timestamp="2026-05-31T10:00:00Z",
    )
    a1 = Attempt(
        commit_hash="bbb",
        agent_id="agent-2",
        title="y",
        score=None,
        status="pending",
        parent_hash=None,
        timestamp="2026-05-31T10:01:00Z",
    )
    write_attempt(coral_dir, a0)
    write_attempt(coral_dir, a1)

    cfg = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
            "agents": {"count": 2},
        }
    )
    mgr = AgentManager(cfg)
    mgr.paths = ProjectPaths(
        results_dir=tmp_path,
        task_dir=tmp_path,
        run_dir=tmp_path,
        snapshots_dir=tmp_path / "snapshots",
        coral_dir=coral_dir,
        agents_dir=tmp_path / "agents",
        repo_dir=tmp_path / "repo",
    )

    seen = mgr._get_seen_attempts()
    assert {"aaa.json", "bbb.json"} <= seen


def test_successful_real_eval_enters_reflect_loop(tmp_path: Path) -> None:
    manager, _ = _build_manager_with_attempts(tmp_path, {})
    attempt = {
        "agent_id": "agent-1",
        "commit_hash": "a" * 40,
        "title": "cache-aware batching",
        "score": 0.75,
        "status": "improved",
        "feedback": "good",
        "metadata": {},
    }

    assert manager._should_enter_reflect_loop(attempt, "real") is True
    prompt = manager._build_reflect_loop_prompt(attempt, 3)
    assert "You are in reflect_loop" in prompt
    assert "coral kb notebook --agent agent-1" in prompt
    assert "coral kb archive --attempt aaaaaaaaaaaa --agent agent-1" in prompt
    assert "--method-file" in prompt
    assert "--reflection-file" in prompt
    assert "teammate practice indexes" in prompt
    assert "technical route is still independent" in prompt
    assert "create or update the corresponding shared skill" in prompt
    assert "archive the old notebook" in prompt


def test_tune_or_failed_eval_stays_in_work_loop(tmp_path: Path) -> None:
    manager, _ = _build_manager_with_attempts(tmp_path, {})
    scored = {
        "agent_id": "agent-1",
        "commit_hash": "b" * 40,
        "title": "sweep",
        "score": 0.5,
        "status": "improved",
        "metadata": {"budget_class": "tune"},
    }
    failed = {
        "agent_id": "agent-1",
        "commit_hash": "c" * 40,
        "title": "bad",
        "score": None,
        "status": "crashed",
        "metadata": {},
    }

    assert manager._should_enter_reflect_loop(scored, "tune") is False
    assert manager._should_enter_reflect_loop(failed, "real") is False
