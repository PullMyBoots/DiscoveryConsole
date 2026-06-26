"""Tests for compute job dashboard API."""

from __future__ import annotations

from pathlib import Path

import yaml
from starlette.testclient import TestClient

from coral.compute.types import ComputeJob, write_job
from coral.web.app import create_app


def test_jobs_api_returns_compute_jobs(tmp_path: Path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    write_job(
        coral_dir,
        ComputeJob(
            job_id="job-20260626000000-test",
            agent_id="agent-1",
            job_class="explore",
            profile="cpu-small",
            command=["python", "probe.py"],
            cwd="/workspace",
            status="succeeded",
            created_at="2026-06-26T00:00:00+00:00",
            exit_code=0,
            timeout=60,
            eval_level="L1",
            eval_space="A",
        ),
    )

    with TestClient(create_app(coral_dir, results_dir=tmp_path)) as client:
        response = client.get("/api/jobs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["job_id"] == "job-20260626000000-test"
    assert payload["jobs"][0]["agent_id"] == "agent-1"
    assert payload["jobs"][0]["eval_space"] == "A"


def test_control_config_save_freezes_eval_identity_and_final_grader(tmp_path: Path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    config_path = coral_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "task": {"name": "sealed-task", "description": "d"},
                "evaluation": {"level": "L3", "allow_loop_final": False},
                "grader": {
                    "entrypoint": "pkg:BGrader",
                    "direction": "minimize",
                    "eval_version": "eval_b",
                    "profile": "full",
                    "resources": {"gpu_count": 1},
                    "final": {
                        "entrypoint": "pkg:CGrader",
                        "eval_version": "eval_c",
                        "profile": "sealed",
                        "resources": {"gpu_count": 2},
                    },
                },
                "agents": {"count": 2},
            }
        )
    )

    with TestClient(create_app(coral_dir, results_dir=tmp_path)) as client:
        current = client.get("/api/control/config").json()["config"]
        current["evaluation"] = {"level": "L1", "allow_loop_final": True}
        current["grader"]["direction"] = "maximize"
        current["grader"]["eval_version"] = "evil"
        current["grader"]["profile"] = "quick"
        current["grader"]["resources"] = {"gpu_count": 99}
        current["grader"]["final"] = {"entrypoint": "pkg:EvilFinal"}
        current["agents"]["count"] = 99
        response = client.post("/api/control/config", json={"config": current})

    assert response.status_code == 200
    saved = response.json()["config"]
    assert saved["evaluation"]["level"] == "L3"
    assert saved["evaluation"]["allow_loop_final"] is False
    assert saved["grader"]["direction"] == "minimize"
    assert saved["grader"]["eval_version"] == "eval_b"
    assert saved["grader"]["profile"] == "full"
    assert saved["grader"]["resources"]["gpu_count"] == 1
    assert saved["grader"]["final"]["entrypoint"] == "pkg:CGrader"
    assert saved["grader"]["final"]["eval_version"] == "eval_c"
    assert saved["grader"]["final"]["profile"] == "sealed"
    assert saved["agents"]["count"] == 2
