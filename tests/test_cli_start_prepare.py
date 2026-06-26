"""CLI prepare/start boundary tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from coral.cli.start import cmd_resume, cmd_start


def _write_task_config(path: Path, *, run_session: str = "local") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "task": {"name": "Start Boundary", "description": "d"},
                "grader": {"entrypoint": "pkg.grader:Grader"},
                "agents": {"count": 1, "runtime": "claude_code", "model": "sonnet"},
                "run": {"session": run_session, "ui": False, "verbose": False},
            }
        )
    )


def test_start_rejects_unprepared_task_config(tmp_path, capsys):
    task_config = tmp_path / "task" / "task.yaml"
    _write_task_config(task_config)

    with pytest.raises(SystemExit) as exc:
        cmd_start(SimpleNamespace(config=str(task_config), overrides=[]))

    assert exc.value.code == 2
    assert "coral prepare" in capsys.readouterr().err


def test_start_rejects_topology_override_for_prepared_run(tmp_path, capsys):
    coral_dir = tmp_path / "results" / "start-boundary" / "run-1" / ".coral"
    config_path = coral_dir / "config.yaml"
    _write_task_config(config_path)

    with pytest.raises(SystemExit) as exc:
        cmd_start(SimpleNamespace(config=str(config_path), overrides=["agents.count=4"]))

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "runtime-safe overrides" in err
    assert "agents.count" in err


def test_start_persists_safe_overrides_before_launch(monkeypatch, tmp_path):
    coral_dir = tmp_path / "results" / "start-boundary" / "run-1" / ".coral"
    config_path = coral_dir / "config.yaml"
    _write_task_config(config_path)

    import coral.agent.manager as manager_module
    import coral.cli.validation as validation_module
    import coral.hub.readiness as readiness_module

    monkeypatch.setattr(validation_module, "validate_task", lambda task_dir: [])
    monkeypatch.setattr(
        readiness_module,
        "build_control_readiness",
        lambda coral_dir: {"status": "ready", "checks": []},
    )

    launched: dict[str, object] = {}

    class FakeManager:
        def __init__(self, config, *, verbose: bool = False, config_dir=None):
            self.config = config
            self.verbose = verbose
            self.config_dir = config_dir
            self.paths = None
            self.specs = []

        def launch_prepared(self, paths):
            self.paths = paths
            launched["paths"] = paths
            launched["model"] = self.config.agents.model
            return [
                SimpleNamespace(
                    agent_id="agent-1",
                    process=SimpleNamespace(pid=123),
                    worktree_path=paths.agents_dir / "agent-1",
                )
            ]

        def monitor_loop(self):
            launched["monitored"] = True

    monkeypatch.setattr(manager_module, "AgentManager", FakeManager)

    cmd_start(
        SimpleNamespace(
            config=str(config_path),
            overrides=["agents.model=opus", "run.max_runtime_seconds=120"],
        )
    )

    saved = yaml.safe_load(config_path.read_text())
    assert saved["agents"]["model"] == "opus"
    assert saved["run"]["max_runtime_seconds"] == 120
    assert launched["model"] == "opus"
    assert launched["paths"].coral_dir == coral_dir
    assert launched["monitored"] is True


def test_resume_rejects_topology_override(monkeypatch, tmp_path, capsys):
    coral_dir = tmp_path / "results" / "start-boundary" / "run-1" / ".coral"
    config_path = coral_dir / "config.yaml"
    _write_task_config(config_path)

    import coral.cli.start as start_module

    monkeypatch.setattr(start_module, "find_coral_dir", lambda task, run: coral_dir)

    with pytest.raises(SystemExit) as exc:
        cmd_resume(
            SimpleNamespace(
                task="start-boundary",
                run="run-1",
                instruction=None,
                instruction_file=None,
                overrides=["agents.count=4"],
            )
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "runtime-safe overrides" in err
    assert "agents.count" in err


def test_resume_persists_safe_overrides(monkeypatch, tmp_path):
    coral_dir = tmp_path / "results" / "start-boundary" / "run-1" / ".coral"
    config_path = coral_dir / "config.yaml"
    _write_task_config(config_path)

    import coral.agent.manager as manager_module
    import coral.cli.start as start_module

    monkeypatch.setattr(start_module, "find_coral_dir", lambda task, run: coral_dir)
    monkeypatch.setattr(start_module, "in_tmux", lambda: False)
    monkeypatch.setattr(start_module, "in_docker", lambda: False)

    resumed: dict[str, object] = {}

    class FakeManager:
        def __init__(self, config, *, verbose: bool = False):
            self.config = config
            self.verbose = verbose

        def resume_all(self, paths, instruction=None):
            resumed["paths"] = paths
            resumed["model"] = self.config.agents.model
            resumed["instruction"] = instruction
            return [
                SimpleNamespace(
                    agent_id="agent-1",
                    process=SimpleNamespace(pid=456),
                    session_id="session-1234567890",
                )
            ]

        def monitor_loop(self):
            resumed["monitored"] = True

    monkeypatch.setattr(manager_module, "AgentManager", FakeManager)

    cmd_resume(
        SimpleNamespace(
            task="start-boundary",
            run="run-1",
            instruction=None,
            instruction_file=None,
            overrides=["agents.model=opus", "run.max_runtime_seconds=90"],
        )
    )

    saved = yaml.safe_load(config_path.read_text())
    assert saved["agents"]["model"] == "opus"
    assert saved["run"]["max_runtime_seconds"] == 90
    assert resumed["model"] == "opus"
    assert resumed["paths"].coral_dir == coral_dir
    assert resumed["monitored"] is True
