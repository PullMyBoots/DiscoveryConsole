"""Tests for built-in coding-agent CLI helper argument mapping."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from coral.agent.builtin.claude_code import ClaudeCodeRuntime
from coral.agent.builtin.opencode import OpenCodeRuntime


class _FakeProcess:
    captured: list[dict[str, Any]] = []

    def __init__(self, cmd, **kwargs) -> None:  # type: ignore[no-untyped-def]
        type(self).captured.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        self.pid = 4242
        self.returncode: int | None = None
        self.stdout = None
        self.stderr = None

    def poll(self) -> int | None:
        return self.returncode


def _make_worktree(tmp_path: Path, agent_id: str = "agent-1") -> Path:
    worktree = tmp_path / agent_id
    worktree.mkdir()
    (worktree / ".coral_agent_id").write_text(agent_id)
    return worktree


def test_claude_code_runtime_maps_reasoning_to_effort(
    monkeypatch, tmp_path: Path
) -> None:
    _FakeProcess.captured = []
    monkeypatch.setattr(subprocess, "Popen", _FakeProcess)
    worktree = _make_worktree(tmp_path)

    ClaudeCodeRuntime().start(
        worktree_path=worktree,
        coral_md_path=worktree / "CLAUDE.md",
        model="sonnet",
        runtime_options={"model_reasoning_effort": "high"},
        log_dir=tmp_path / "logs",
        prompt="Begin.",
    )

    cmd = _FakeProcess.captured[0]["cmd"]
    assert cmd[0:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"


def test_opencode_runtime_maps_reasoning_to_variant_and_resume(
    monkeypatch, tmp_path: Path
) -> None:
    _FakeProcess.captured = []
    monkeypatch.setattr(subprocess, "Popen", _FakeProcess)
    worktree = _make_worktree(tmp_path)

    OpenCodeRuntime().start(
        worktree_path=worktree,
        coral_md_path=worktree / "AGENTS.md",
        model="openai/gpt-5",
        runtime_options={"model_reasoning_effort": "high"},
        log_dir=tmp_path / "logs",
        resume_session_id="sess-123",
        prompt="Continue.",
    )

    cmd = _FakeProcess.captured[0]["cmd"]
    assert cmd[0:2] == ["opencode", "run"]
    assert cmd[cmd.index("--model") + 1] == "openai/gpt-5"
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[cmd.index("--variant") + 1] == "high"
    assert cmd[cmd.index("--session") + 1] == "sess-123"
    assert "--continue" in cmd
    assert cmd[-1] == "Continue."
