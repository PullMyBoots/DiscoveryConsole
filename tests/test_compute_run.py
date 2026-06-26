"""Tests for open A-space compute jobs."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import time
from pathlib import Path

import pytest

from coral.cli.run import cmd_run
from coral.compute.runner import run_compute_job
from coral.compute.types import read_job, read_jobs
from coral.config import CoralConfig, EvaluationConfig, TaskConfig


def _make_worktree(tmp_path: Path, config: CoralConfig | None = None) -> tuple[Path, Path]:
    coral_dir = tmp_path / "run" / ".coral"
    coral_dir.mkdir(parents=True)
    (coral_dir / "public").mkdir()
    (coral_dir / "private").mkdir()
    (
        config
        or CoralConfig(
            task=TaskConfig(name="Compute", description="d"),
            evaluation=EvaluationConfig(level="L1"),
        )
    ).to_yaml(coral_dir / "config.yaml")

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".coral_dir").write_text(str(coral_dir))
    (worktree / ".coral_agent_id").write_text("agent-1")
    return worktree, coral_dir


def _run_sleep_compute_worker(worktree: str, seconds: float, queue: multiprocessing.Queue) -> None:
    try:
        job = run_compute_job(
            command=[sys.executable, "-c", f"import time; time.sleep({seconds!r})"],
            workdir=worktree,
        )
        queue.put({"status": job.status, "error": ""})
    except Exception as exc:  # noqa: BLE001 - subprocess test helper reports failures.
        queue.put({"status": "error", "error": str(exc)})


def _wait_for_pool_lease(coral_dir: Path) -> None:
    state_path = coral_dir / "private" / "compute_locks" / "pool_state.json"
    for _ in range(60):
        if state_path.exists():
            data = json.loads(state_path.read_text())
            leases = data.get("leases", {})
            if isinstance(leases, dict) and leases:
                return
        time.sleep(0.05)
    raise AssertionError("compute pool lease was not acquired")


def test_run_compute_job_records_success_logs_and_artifacts(tmp_path: Path):
    worktree, coral_dir = _make_worktree(tmp_path)
    code = (
        "import json, os, pathlib\n"
        "print('hello from compute')\n"
        "payload = {k: os.environ.get(k) for k in [\n"
        "  'CORAL_AGENT_ID', 'CORAL_JOB_ID', 'CORAL_JOB_CLASS',\n"
        "  'CORAL_EVAL_LEVEL', 'CORAL_EVAL_SPACE', 'CORAL_DATA_DIR',\n"
        "  'CORAL_ARTIFACT_DIR', 'CORAL_CPU_CORES', 'CORAL_MEMORY_GB',\n"
        "  'CUDA_VISIBLE_DEVICES', 'OMP_NUM_THREADS']}\n"
        "pathlib.Path(os.environ['CORAL_ARTIFACT_DIR'], 'env.json').write_text(\n"
        "  json.dumps(payload, sort_keys=True))\n"
    )

    job = run_compute_job(command=[sys.executable, "-c", code], workdir=worktree)

    assert job.status == "succeeded"
    assert job.exit_code == 0
    assert job.agent_id == "agent-1"
    assert job.eval_space == "A"
    assert job.eval_level == "L1"
    assert job.profile == "cpu-small"
    assert job.resources == {
        "cpu_cores": 2,
        "memory_gb": 8,
        "storage_gb": 0.0,
        "gpu_count": 0,
        "gpu_ids": [],
    }
    assert Path(job.stdout_path).read_text() == "hello from compute\n"
    assert Path(job.stderr_path).read_text() == ""

    env = json.loads((Path(job.artifact_dir) / "env.json").read_text())
    assert env["CORAL_AGENT_ID"] == "agent-1"
    assert env["CORAL_JOB_ID"] == job.job_id
    assert env["CORAL_JOB_CLASS"] == "explore"
    assert env["CORAL_EVAL_SPACE"] == "A"
    assert env["CORAL_CPU_CORES"] == "2"
    assert env["CORAL_MEMORY_GB"] == "8"
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["OMP_NUM_THREADS"] == "2"
    assert Path(env["CORAL_DATA_DIR"]) == coral_dir / "public" / "datasets"
    assert Path(env["CORAL_ARTIFACT_DIR"]) == Path(job.artifact_dir)

    persisted = read_job(coral_dir, job.job_id)
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert [record.job_id for record in read_jobs(coral_dir)] == [job.job_id]


def test_run_compute_job_uses_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    worktree, _coral_dir = _make_worktree(tmp_path)
    code = (
        "import json, os, pathlib\n"
        "pathlib.Path(os.environ['CORAL_ARTIFACT_DIR'], 'env.json').write_text(\n"
        "  json.dumps({'secret': os.environ.get('OPENAI_API_KEY'), "
        "'path': os.environ.get('PATH'), "
        "'agent': os.environ.get('CORAL_AGENT_ID')}))\n"
    )

    job = run_compute_job(command=[sys.executable, "-c", code], workdir=worktree)

    env = json.loads((Path(job.artifact_dir) / "env.json").read_text())
    assert job.status == "succeeded"
    assert env["secret"] is None
    assert env["path"]
    assert env["agent"] == "agent-1"


def test_run_compute_job_rejects_hidden_eval_level_without_isolated_backend(tmp_path: Path):
    worktree, _coral_dir = _make_worktree(
        tmp_path,
        CoralConfig.from_dict({"task": {"name": "Hidden", "description": "d"}}),
    )

    with pytest.raises(RuntimeError, match="disabled for hidden evaluation levels"):
        run_compute_job(command=[sys.executable, "-c", "print('nope')"], workdir=worktree)


def test_run_compute_job_assigns_gpu_profile_from_pool(tmp_path: Path):
    config = CoralConfig.from_dict(
        {
            "task": {"name": "Compute", "description": "d"},
            "evaluation": {"level": "L1"},
            "compute": {
                "pool": {"gpu_count": 2, "gpu_ids": ["2", "3"]},
                "classes": {"explore": {"default_profile": "gpu-small"}},
            },
        }
    )
    worktree, _coral_dir = _make_worktree(tmp_path, config)
    code = (
        "import json, os, pathlib\n"
        "pathlib.Path(os.environ['CORAL_ARTIFACT_DIR'], 'gpu.json').write_text(\n"
        "  json.dumps({'cuda': os.environ.get('CUDA_VISIBLE_DEVICES'), "
        "'gpu_ids': os.environ.get('CORAL_GPU_IDS'), "
        "'gpu_count': os.environ.get('CORAL_GPU_COUNT')}))\n"
    )

    job = run_compute_job(command=[sys.executable, "-c", code], workdir=worktree)

    env = json.loads((Path(job.artifact_dir) / "gpu.json").read_text())
    assert job.status == "succeeded"
    assert job.resources["gpu_count"] == 1
    assert job.resources["gpu_ids"] == ["2"]
    assert env == {"cuda": "2", "gpu_ids": "2", "gpu_count": "1"}


def test_run_compute_job_reserves_cpu_pool_across_processes(tmp_path: Path):
    config = CoralConfig.from_dict(
        {
            "task": {"name": "Compute", "description": "d"},
            "evaluation": {"level": "L1"},
            "compute": {
                "pool": {"cpu_cores": 2},
                "classes": {"explore": {"default_profile": "cpu-only", "max_running_per_agent": 2}},
                "profiles": {"cpu-only": {"cpu_cores": 2, "timeout": 10}},
            },
        }
    )
    worktree, coral_dir = _make_worktree(tmp_path, config)
    queue: multiprocessing.Queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_run_sleep_compute_worker,
        args=(str(worktree), 1.0, queue),
    )

    process.start()
    try:
        _wait_for_pool_lease(coral_dir)
        with pytest.raises(RuntimeError, match="compute.pool does not have enough free capacity"):
            run_compute_job(
                command=[sys.executable, "-c", "print('blocked')"],
                workdir=worktree,
            )
    finally:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    assert queue.get(timeout=1)["status"] == "succeeded"

    job = run_compute_job(command=[sys.executable, "-c", "print('after release')"], workdir=worktree)
    assert job.status == "succeeded"


def test_run_compute_job_records_failure(tmp_path: Path):
    worktree, _coral_dir = _make_worktree(tmp_path)

    job = run_compute_job(
        command=[sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"],
        workdir=worktree,
    )

    assert job.status == "failed"
    assert job.exit_code == 7
    assert Path(job.stdout_path).read_text() == "bad\n"


def test_run_compute_job_records_timeout(tmp_path: Path):
    worktree, _coral_dir = _make_worktree(tmp_path)

    job = run_compute_job(
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        workdir=worktree,
        timeout=1,
    )

    assert job.status == "timeout"
    assert job.exit_code is None
    assert "timed out" in Path(job.stderr_path).read_text()


def test_run_compute_job_rejects_negative_timeout(tmp_path: Path):
    worktree, _coral_dir = _make_worktree(tmp_path)

    with pytest.raises(ValueError, match="timeout"):
        run_compute_job(
            command=[sys.executable, "-c", "print('never')"],
            workdir=worktree,
            timeout=-1,
        )


def test_run_compute_job_timeout_kills_child_process_group(tmp_path: Path):
    worktree, _coral_dir = _make_worktree(tmp_path)
    marker = tmp_path / "child-survived.txt"
    child = (
        "import pathlib, time\n"
        "time.sleep(2)\n"
        f"pathlib.Path({str(marker)!r}).write_text('survived')\n"
    )
    command = f"{sys.executable} -c {json.dumps(child)} & sleep 5"

    job = run_compute_job(
        command=["sh", "-c", command],
        workdir=worktree,
        timeout=1,
    )
    time.sleep(2.5)

    assert job.status == "timeout"
    assert not marker.exists()


def test_cmd_run_strips_separator_and_prints_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    worktree, _coral_dir = _make_worktree(tmp_path)
    args = argparse.Namespace(
        command=["--", sys.executable, "-c", "print('cli ok')"],
        workdir=str(worktree),
        agent=None,
        job_class="explore",
        profile=None,
        timeout=None,
    )

    cmd_run(args)

    out = capsys.readouterr().out
    assert "CORAL Run: succeeded" in out
    assert "Profile:  cpu-small" in out
    assert "Artifacts:" in out
