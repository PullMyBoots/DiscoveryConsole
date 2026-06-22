"""CLI dashboard launch behavior."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import yaml

from coral.cli.ui import _prepare_prelaunch_run_if_task_dir


@contextmanager
def _chdir(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write_minimal_task(task_dir: Path) -> None:
    (task_dir / "seed").mkdir(parents=True)
    (task_dir / "seed" / "solution.py").write_text("print(0.0)\n")
    (task_dir / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "UI Smoke", "description": "d"},
                "grader": {"entrypoint": "g:Grader", "setup": []},
                "workspace": {"repo_path": "./seed", "results_dir": "./results"},
            }
        )
    )


def test_ui_prepares_prelaunch_run_from_task_cwd(monkeypatch, tmp_path):
    task_dir = tmp_path / "ui-task"
    task_dir.mkdir()
    _write_minimal_task(task_dir)
    monkeypatch.setattr(
        "coral.workspace.grader_env.setup_grader_env",
        lambda coral_dir, grader_config, config_dir, **kwargs: coral_dir / "private" / "python",
    )

    with _chdir(task_dir):
        coral_dir = _prepare_prelaunch_run_if_task_dir(None, None)

    assert coral_dir is not None
    run_dir = coral_dir.parent
    assert coral_dir.is_dir()
    assert (run_dir / "repo" / "solution.py").read_text() == "print(0.0)\n"
    assert (task_dir / "results" / "ui-smoke" / "latest").resolve() == run_dir.resolve()
    saved = yaml.safe_load((coral_dir / "config.yaml").read_text())
    assert saved["workspace"]["run_dir"] == str(run_dir)
    assert (run_dir / "snapshots" / "task.yaml").is_file()


def test_ui_prelaunch_reuses_existing_latest(monkeypatch, tmp_path):
    task_dir = tmp_path / "ui-task"
    task_dir.mkdir()
    _write_minimal_task(task_dir)
    monkeypatch.setattr(
        "coral.workspace.grader_env.setup_grader_env",
        lambda coral_dir, grader_config, config_dir, **kwargs: coral_dir / "private" / "python",
    )

    first = _prepare_prelaunch_run_if_task_dir(str(task_dir), None)
    second = _prepare_prelaunch_run_if_task_dir(str(task_dir), None)

    assert first is not None
    assert second is not None
    assert second.resolve() == first.resolve()
