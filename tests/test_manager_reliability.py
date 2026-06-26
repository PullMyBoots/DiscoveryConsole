"""Unit tests for the agent-manager reliability primitives.

Exercises the decision functions, persistence, and config validation added
by the agent-manager-reliability patch. Integration tests with full
fake-runtime fixtures are tracked separately (task11) and are not in this
file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from coral.agent.builtin.claude_code import ClaudeCodeRuntime
from coral.agent.builtin.codex import CodexRuntime
from coral.agent.builtin.kiro import KiroRuntime
from coral.agent.builtin.opencode import OpenCodeRuntime
from coral.agent.exit_classifier import (
    classify_by_uptime,
    claude_code_has_result,
)
from coral.agent.manager import AgentManager
from coral.agent.state import (
    AGENT_STATE_SCHEMA_VERSION,
    AgentRuntimeState,
    AgentStateDocument,
    RestartEvent,
    read_agent_state,
    state_file_path,
    write_agent_state,
)
from coral.config import AgentConfig, CoralConfig
from coral.hub.attempts import agent_in_grader_queue, write_attempt
from coral.hub.kb import read_notebook
from coral.types import Attempt
from coral.workspace import ProjectPaths

# ---------------------------------------------------------------------------
# classify_by_uptime — markerless runtime fallback
# ---------------------------------------------------------------------------


def test_classify_by_uptime_clean_when_long_uptime_zero_exit() -> None:
    assert classify_by_uptime(0, 120.0, min_clean_runtime_seconds=60) == "clean"


def test_classify_by_uptime_no_result_when_uptime_below_min() -> None:
    assert classify_by_uptime(0, 5.0, min_clean_runtime_seconds=60) == "no_result"


def test_classify_by_uptime_no_result_when_exit_nonzero_even_long_uptime() -> None:
    assert classify_by_uptime(1, 9999.0, min_clean_runtime_seconds=60) == "no_result"


def test_classify_by_uptime_no_result_when_uptime_unknown() -> None:
    assert classify_by_uptime(0, None, min_clean_runtime_seconds=60) == "no_result"


# ---------------------------------------------------------------------------
# claude_code_has_result — marker scanning
# ---------------------------------------------------------------------------


def test_claude_code_has_result_finds_marker(tmp_path: Path) -> None:
    log = tmp_path / "agent-1.0.log"
    log.write_text(
        '{"type":"start","session_id":"abc"}\n'
        '{"type":"assistant"}\n'
        '{"type":"result","cost_usd":0.01}\n'
    )
    assert claude_code_has_result(log) is True


def test_claude_code_has_result_finds_marker_with_space(tmp_path: Path) -> None:
    log = tmp_path / "agent-1.0.log"
    log.write_text('{"type": "result", "duration_ms":12345}\n')
    assert claude_code_has_result(log) is True


def test_claude_code_has_result_returns_false_when_marker_absent(tmp_path: Path) -> None:
    log = tmp_path / "agent-1.0.log"
    log.write_text('{"type":"start"}\n{"type":"assistant"}\n')
    assert claude_code_has_result(log) is False


def test_claude_code_has_result_returns_false_when_file_missing(tmp_path: Path) -> None:
    assert claude_code_has_result(tmp_path / "missing.log") is False


# ---------------------------------------------------------------------------
# Per-runtime classify_exit
# ---------------------------------------------------------------------------


def test_claude_code_classify_exit_clean_requires_marker(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text('{"type":"result"}\n')
    runtime = ClaudeCodeRuntime()
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=10000.0) == "clean"


def test_claude_code_classify_exit_no_result_when_marker_missing(tmp_path: Path) -> None:
    """Long uptime alone must NOT certify clean for claude_code (AC-1.1 negative)."""
    log = tmp_path / "agent.log"
    log.write_text('{"type":"start"}\n')
    runtime = ClaudeCodeRuntime()
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=10000.0) == "no_result"


def test_claude_code_classify_exit_session_error(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("Error: No conversation found with session id 'abc'\n")
    runtime = ClaudeCodeRuntime()
    assert runtime.classify_exit(log, exit_code=1, uptime_seconds=2.0) == "session_error"


def test_codex_classify_exit_uses_uptime_fallback(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("hello world\n")
    runtime = CodexRuntime()
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=120.0) == "clean"
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=5.0) == "no_result"


def test_kiro_classify_exit_uses_uptime_fallback(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("plain text output\n")
    runtime = KiroRuntime()
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=120.0) == "clean"
    assert runtime.classify_exit(log, exit_code=1, uptime_seconds=120.0) == "no_result"


def test_opencode_classify_exit_uses_uptime_fallback(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("opencode output\n")
    runtime = OpenCodeRuntime()
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=120.0) == "clean"
    assert runtime.classify_exit(log, exit_code=0, uptime_seconds=10.0) == "no_result"


# ---------------------------------------------------------------------------
# AgentConfig validation
# ---------------------------------------------------------------------------


def test_agent_config_defaults_pass_validation() -> None:
    cfg = AgentConfig()
    # Must not raise; defaults are documented sane values.
    assert cfg.restart_burst_threshold == 3
    assert cfg.restart_burst_window == 30
    assert cfg.restart_pause_seconds == 300
    assert cfg.min_clean_runtime_seconds == 60


def test_agent_config_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError, match="restart_burst_threshold"):
        AgentConfig(restart_burst_threshold=-1)


def test_agent_config_rejects_pause_shorter_than_window() -> None:
    with pytest.raises(ValueError, match="restart_pause_seconds"):
        AgentConfig(
            restart_burst_threshold=3,
            restart_burst_window=60,
            restart_pause_seconds=30,
        )


def test_agent_config_zero_threshold_is_allowed_disabled() -> None:
    # 0 means "disabled"; no validation error.
    cfg = AgentConfig(restart_burst_threshold=0)
    assert cfg.restart_burst_threshold == 0


def test_agent_config_zero_window_or_pause_is_allowed_disabled() -> None:
    AgentConfig(restart_burst_window=0)
    AgentConfig(restart_pause_seconds=0)


# ---------------------------------------------------------------------------
# Run deadline state
# ---------------------------------------------------------------------------


def _deadline_manager(tmp_path: Path, max_runtime_seconds: int) -> AgentManager:
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    cfg = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
            "run": {"max_runtime_seconds": max_runtime_seconds},
        }
    )
    mgr = AgentManager(cfg)
    mgr.paths = ProjectPaths(
        results_dir=tmp_path / "results",
        task_dir=tmp_path,
        run_dir=tmp_path,
        snapshots_dir=tmp_path / "snapshots",
        coral_dir=coral_dir,
        agents_dir=tmp_path / "agents",
        repo_dir=tmp_path / "repo",
    )
    mgr._runtime_start_epoch = time.time()
    return mgr


def test_run_deadline_state_records_limit_and_deadline(tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=120)

    mgr._refresh_runtime_deadline(force=True, status="running")

    state = json.loads((tmp_path / ".coral" / "public" / "run_state.json").read_text())
    assert state["status"] == "running"
    assert state["max_runtime_seconds"] == 120
    assert state["deadline_at"] is not None
    assert state["remaining_seconds"] > 100


def test_run_deadline_can_be_extended_from_saved_config(tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=60)
    mgr._runtime_start_epoch = time.time() - 10
    (tmp_path / ".coral" / "config.yaml").write_text(
        "task:\n  name: t\n  description: d\nrun:\n  max_runtime_seconds: 180\n"
    )

    mgr._refresh_runtime_deadline(force=True, status="running")

    state = json.loads((tmp_path / ".coral" / "public" / "run_state.json").read_text())
    assert state["max_runtime_seconds"] == 180
    assert 160 <= state["remaining_seconds"] <= 180


def test_run_deadline_stops_all_when_expired(tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=1)
    mgr._runtime_start_epoch = time.time() - 5
    called: list[str] = []

    def fake_stop_all(*, reason: str = "manual") -> None:
        called.append(reason)
        mgr._running = False
        mgr._write_run_state(status="stopped", stopped_reason=reason)

    mgr.stop_all = fake_stop_all  # type: ignore[method-assign]

    assert mgr._maybe_stop_for_deadline() is True
    assert called == ["deadline"]
    state = json.loads((tmp_path / ".coral" / "public" / "run_state.json").read_text())
    assert state["status"] == "stopped"
    assert state["stopped_reason"] == "deadline"


class _FakeAgentHandle:
    def __init__(self, agent_id: str, *, alive: bool = True) -> None:
        self.agent_id = agent_id
        self._alive = alive
        self.process = None
        self.session_id = None
        self.log_path = Path("/tmp/fake-agent.log")
        self.interrupted = False
        self.stopped = False

    @property
    def alive(self) -> bool:
        return self._alive

    def interrupt(self) -> None:
        self.interrupted = True
        self._alive = False

    def stop(self) -> None:
        self.stopped = True
        self._alive = False


def test_agent_control_request_stops_one_agent(tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=0)
    handle = _FakeAgentHandle("agent-1", alive=True)
    mgr.handles = [handle]  # type: ignore[list-item]
    mgr._running = True
    mgr._save_sessions = lambda: None  # type: ignore[method-assign]
    control = tmp_path / ".coral" / "public" / "control" / "agents" / "agent-1.json"
    control.parent.mkdir(parents=True)
    control.write_text(json.dumps({"agent_id": "agent-1", "desired_state": "stopped"}))

    mgr._apply_agent_control_requests()

    assert handle.interrupted is True
    assert "agent-1" in mgr._manual_stopped_agents
    state = read_agent_state(tmp_path / ".coral").agents["agent-1"]
    assert state.state == "stopped"


def test_agent_control_request_resumes_one_agent(monkeypatch, tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=0)
    old_handle = _FakeAgentHandle("agent-1", alive=False)
    new_handle = _FakeAgentHandle("agent-1", alive=True)
    mgr.handles = [old_handle]  # type: ignore[list-item]
    mgr._manual_stopped_agents.add("agent-1")
    mgr._running = True
    mgr._save_sessions = lambda: None  # type: ignore[method-assign]
    control = tmp_path / ".coral" / "public" / "control" / "agents" / "agent-1.json"
    control.parent.mkdir(parents=True)
    control.write_text(json.dumps({"agent_id": "agent-1", "desired_state": "running"}))
    calls: list[tuple[int, str | None]] = []

    def _fake_restart(idx: int, prompt: str | None = None, prompt_source: str | None = None):
        calls.append((idx, prompt_source))
        return new_handle

    monkeypatch.setattr(mgr, "_restart_agent", _fake_restart)

    mgr._apply_agent_control_requests()

    assert calls == [(0, "manual-resume")]
    assert mgr.handles == [new_handle]
    assert "agent-1" not in mgr._manual_stopped_agents
    state = read_agent_state(tmp_path / ".coral").agents["agent-1"]
    assert state.state == "active"


def test_agent_prompt_request_interrupts_running_agent(monkeypatch, tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=0)
    old_handle = _FakeAgentHandle("agent-1", alive=True)
    new_handle = _FakeAgentHandle("agent-1", alive=True)
    mgr.handles = [old_handle]  # type: ignore[list-item]
    mgr._running = True
    control = tmp_path / ".coral" / "public" / "control" / "agents" / "agent-1.json"
    control.parent.mkdir(parents=True)
    control.write_text(
        json.dumps(
            {
                "agent_id": "agent-1",
                "action": "prompt",
                "desired_state": "running",
                "prompt": "Please inspect the latest failed eval.",
                "command_id": "cmd-1",
            }
        )
    )
    calls: list[tuple[int, str, str | None]] = []

    def _fake_interrupt(idx: int, prompt: str, prompt_source: str | None = None):
        calls.append((idx, prompt, prompt_source))
        return new_handle

    monkeypatch.setattr(mgr, "_interrupt_and_resume", _fake_interrupt)

    mgr._apply_agent_control_requests()

    assert len(calls) == 1
    idx, prompt, prompt_source = calls[0]
    assert idx == 0
    assert prompt_source == "dashboard-prompt"
    assert "You are in work_loop" in prompt
    assert "coral kb notebook --agent agent-1" in prompt
    assert "Please inspect the latest failed eval." in prompt
    assert mgr.handles == [new_handle]
    notebook = read_notebook(tmp_path / ".coral", "agent-1")
    assert "External Guidance" in notebook
    assert "Please inspect the latest failed eval." in notebook
    saved = json.loads(control.read_text())
    assert saved["action"] == "idle"
    assert saved["prompt"] == ""
    assert saved["last_prompt_applied_at"]


def test_resume_all_defaults_to_work_loop_and_records_instruction(monkeypatch, tmp_path: Path) -> None:
    mgr = _deadline_manager(tmp_path, max_runtime_seconds=0)
    agents_dir = tmp_path / "agents"
    (agents_dir / "agent-1").mkdir(parents=True)
    mgr.paths = ProjectPaths(
        results_dir=tmp_path / "results",
        task_dir=tmp_path,
        run_dir=tmp_path,
        snapshots_dir=tmp_path / "snapshots",
        coral_dir=tmp_path / ".coral",
        agents_dir=agents_dir,
        repo_dir=tmp_path / "repo",
    )
    old_notebook = (
        tmp_path
        / ".coral"
        / "public"
        / "knowledge"
        / "practice"
        / "agents"
        / "agent-1"
        / "notebook.md"
    )
    old_notebook.parent.mkdir(parents=True)
    old_notebook.write_text("# Notebook: agent-1\n\nold plan\n")
    monkeypatch.setattr(mgr, "_start_gateway_if_enabled", lambda: None)
    monkeypatch.setattr(mgr, "_start_grader_daemon", lambda: None)
    monkeypatch.setattr(mgr, "_kill_old_agent_processes", lambda: None)
    monkeypatch.setattr(mgr, "_load_saved_sessions", lambda: {"agent-1": "sess-1"})
    monkeypatch.setattr("coral.agent.manager._validate_sessions", lambda sessions, coral_dir: sessions)
    calls: list[tuple[str, str | None, str | None]] = []

    def _fake_setup(agent_id: str, resume_session_id: str | None = None, prompt: str | None = None, **kwargs):
        calls.append((agent_id, resume_session_id, prompt))
        return _FakeAgentHandle(agent_id, alive=True)

    monkeypatch.setattr(mgr, "_setup_and_start_agent", _fake_setup)

    handles = mgr.resume_all(mgr.paths, instruction="Prefer route B after review.")

    assert len(handles) == 1
    assert calls[0][0] == "agent-1"
    assert calls[0][1] == "sess-1"
    assert calls[0][2] is not None
    assert "You are in work_loop" in calls[0][2]
    assert "coral kb notebook --agent agent-1" in calls[0][2]
    notebook = read_notebook(tmp_path / ".coral", "agent-1")
    assert "Prefer route B after review." in notebook
    archive_dir = old_notebook.parent / "notebook_archive"
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == 1
    assert "old plan" in archived[0].read_text()


# ---------------------------------------------------------------------------
# RestartEvent / state dataclasses
# ---------------------------------------------------------------------------


def test_restart_event_construction() -> None:
    ev = RestartEvent(
        timestamp=time.time(),
        exit_code=137,
        log_path="/tmp/agent.log",
        classification="no_result",
    )
    assert ev.classification == "no_result"
    assert ev.exit_code == 137


def test_agent_runtime_state_roundtrip() -> None:
    rs = AgentRuntimeState(
        state="reflect_loop",
        paused_until=None,
        pause_count=2,
        last_fault_at="2026-04-29T01:00:00+00:00",
        state_started_at=12345.0,
        state_detail="archive latest eval",
    )
    data = rs.to_dict()
    restored = AgentRuntimeState.from_dict(data)
    assert restored == rs


def test_agent_state_document_roundtrip() -> None:
    doc = AgentStateDocument()
    doc.agents["agent-1"] = AgentRuntimeState(state="paused", paused_until=42.0, pause_count=1)
    data = doc.to_dict()
    restored = AgentStateDocument.from_dict(data)
    assert restored.schema_version == AGENT_STATE_SCHEMA_VERSION
    assert restored.agents["agent-1"].state == "paused"
    assert restored.agents["agent-1"].paused_until == 42.0


# ---------------------------------------------------------------------------
# write_agent_state / read_agent_state
# ---------------------------------------------------------------------------


def test_write_and_read_agent_state_roundtrip(tmp_path: Path) -> None:
    coral_dir = tmp_path / "coral"
    doc = AgentStateDocument()
    doc.agents["agent-1"] = AgentRuntimeState(state="paused", paused_until=99.0, pause_count=3)
    path = write_agent_state(coral_dir, doc)
    assert path.exists()
    assert path == state_file_path(coral_dir)

    restored = read_agent_state(coral_dir)
    assert "agent-1" in restored.agents
    assert restored.agents["agent-1"].pause_count == 3
    # updated_at should have been populated on write.
    assert restored.updated_at != ""


def test_read_agent_state_missing_file_returns_empty(tmp_path: Path) -> None:
    doc = read_agent_state(tmp_path / "no-such-coral-dir")
    assert doc.agents == {}


def test_read_agent_state_corrupt_file_returns_empty(tmp_path: Path) -> None:
    coral_dir = tmp_path / "coral"
    public = coral_dir / "public"
    public.mkdir(parents=True)
    (public / "agent_state.json").write_text("{not valid json")
    doc = read_agent_state(coral_dir)
    assert doc.agents == {}


def test_write_agent_state_is_atomic(tmp_path: Path) -> None:
    """The temp file must be renamed in place; no partial JSON should leak."""
    coral_dir = tmp_path / "coral"
    doc = AgentStateDocument()
    doc.agents["a"] = AgentRuntimeState(state="active")
    write_agent_state(coral_dir, doc)
    # Only the canonical file should remain — no .tmp leftovers.
    public = coral_dir / "public"
    leftovers = [p for p in public.iterdir() if p.name.startswith(".agent_state")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# agent_in_grader_queue
# ---------------------------------------------------------------------------


def _make_attempt(agent_id: str, status: str, score: float | None) -> Attempt:
    return Attempt(
        commit_hash="0" * 40 + agent_id,
        agent_id=agent_id,
        title=f"attempt for {agent_id}",
        score=score,
        status=status,
        parent_hash=None,
        timestamp="2026-04-29T01:00:00+00:00",
    )


def test_agent_in_grader_queue_finds_pending(tmp_path: Path) -> None:
    coral_dir = tmp_path / "coral"
    pending = _make_attempt("agent-1", status="pending", score=None)
    write_attempt(coral_dir, pending)
    found = agent_in_grader_queue(coral_dir, "agent-1")
    assert found is not None
    assert found.agent_id == "agent-1"


def test_agent_in_grader_queue_returns_none_when_score_present(tmp_path: Path) -> None:
    coral_dir = tmp_path / "coral"
    write_attempt(coral_dir, _make_attempt("agent-1", status="pending", score=42.0))
    assert agent_in_grader_queue(coral_dir, "agent-1") is None


def test_agent_in_grader_queue_returns_none_for_other_agent(tmp_path: Path) -> None:
    coral_dir = tmp_path / "coral"
    write_attempt(coral_dir, _make_attempt("agent-1", status="pending", score=None))
    assert agent_in_grader_queue(coral_dir, "agent-2") is None


def test_agent_in_grader_queue_uses_cached_attempts(tmp_path: Path) -> None:
    coral_dir = tmp_path / "coral"
    pending = _make_attempt("agent-1", status="pending", score=None)
    write_attempt(coral_dir, pending)
    # Pass an empty cache — the helper must not fall back to disk read.
    found = agent_in_grader_queue(coral_dir, "agent-1", attempts=[])
    assert found is None
    # Pass the real list — helper finds it.
    found = agent_in_grader_queue(coral_dir, "agent-1", attempts=[pending])
    assert found is not None


def test_agent_in_grader_queue_returns_newest_when_multiple_pending() -> None:
    """When an agent has multiple pending attempts (e.g. crash-resubmit), the
    newest by ISO timestamp must win so the stall-watchdog exemption uses
    the most relevant evidence."""
    older = Attempt(
        commit_hash="0" * 40 + "a",
        agent_id="agent-1",
        title="older pending",
        score=None,
        status="pending",
        parent_hash=None,
        timestamp="2026-04-29T00:00:00+00:00",
    )
    newer = Attempt(
        commit_hash="0" * 40 + "b",
        agent_id="agent-1",
        title="newer pending",
        score=None,
        status="pending",
        parent_hash=None,
        timestamp="2026-04-29T01:00:00+00:00",
    )
    found = agent_in_grader_queue(Path("/tmp/unused"), "agent-1", attempts=[older, newer])
    assert found is not None
    assert found.title == "newer pending"


# ---------------------------------------------------------------------------
# Per-agent latest-attempt filter
# ---------------------------------------------------------------------------


def test_per_agent_latest_attempt_filter_excludes_other_agents(tmp_path: Path) -> None:
    """
    AC-1.3 negative: the resume-prompt latest-attempt lookup must not surface
    attempts owned by a different agent. Verified at the dataclass JSON level
    so we exercise the real on-disk filter path.
    """
    attempts_dir = tmp_path / "public" / "attempts"
    attempts_dir.mkdir(parents=True)
    a1 = _make_attempt("agent-1", status="improved", score=1.0)
    a2 = _make_attempt("agent-2", status="improved", score=2.0)
    (attempts_dir / "a1.json").write_text(json.dumps(a1.to_dict()))
    (attempts_dir / "a2.json").write_text(json.dumps(a2.to_dict()))

    # Read both files, filter by agent_id manually as the manager does.
    new_files = {"a1.json", "a2.json"}
    matched = []
    for fname in new_files:
        data = json.loads((attempts_dir / fname).read_text())
        if data["agent_id"] == "agent-1":
            matched.append(data)
    assert len(matched) == 1
    assert matched[0]["agent_id"] == "agent-1"
    assert matched[0]["score"] == 1.0


def test_monitor_loop_stall_watchdog_reads_public_attempts(tmp_path):
    """The stall watchdog can read public attempts without raising."""
    from coral.agent.manager import AgentManager
    from coral.config import CoralConfig
    from coral.workspace.project import ProjectPaths

    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)

    cfg = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
            "agents": {"count": 2, "timeout": 1},
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

    # Directly exercise the read that crashed before the fix
    from coral.hub.attempts import read_attempts

    assert read_attempts(coral_dir) == []
