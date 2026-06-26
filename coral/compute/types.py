"""Types and JSON persistence for open A-space compute jobs."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ComputeJob:
    """Record of one agent-requested open A-space compute job."""

    job_id: str
    agent_id: str
    job_class: str
    profile: str
    command: list[str]
    cwd: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    timeout: int = 0
    resources: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    stdout_path: str = ""
    stderr_path: str = ""
    artifact_dir: str = ""
    eval_level: str = ""
    eval_space: str = "A"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "job_class": self.job_class,
            "profile": self.profile,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "timeout": self.timeout,
            "resources": self.resources,
            "env": self.env,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "artifact_dir": self.artifact_dir,
            "eval_level": self.eval_level,
            "eval_space": self.eval_space,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComputeJob:
        return cls(
            job_id=data["job_id"],
            agent_id=data["agent_id"],
            job_class=data.get("job_class", "explore"),
            profile=data.get("profile", ""),
            command=list(data.get("command", [])),
            cwd=data.get("cwd", ""),
            status=data.get("status", "failed"),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            exit_code=data.get("exit_code"),
            timeout=int(data.get("timeout") or 0),
            resources=dict(data.get("resources") or {}),
            env={str(k): str(v) for k, v in dict(data.get("env") or {}).items()},
            stdout_path=data.get("stdout_path", ""),
            stderr_path=data.get("stderr_path", ""),
            artifact_dir=data.get("artifact_dir", ""),
            eval_level=data.get("eval_level", ""),
            eval_space=data.get("eval_space", "A"),
            error=data.get("error", ""),
        )


def job_dir(coral_dir: str | Path, job_id: str) -> Path:
    path = Path(coral_dir) / "public" / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_job(coral_dir: str | Path, job: ComputeJob) -> Path:
    path = job_dir(coral_dir, job.job_id) / "job.json"
    payload = json.dumps(job.to_dict(), indent=2, sort_keys=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{job.job_id}.",
        suffix=".json.tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path


def read_job(coral_dir: str | Path, job_id: str) -> ComputeJob | None:
    path = Path(coral_dir) / "public" / "jobs" / job_id / "job.json"
    if not path.is_file():
        return None
    try:
        return ComputeJob.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
        return None


def read_jobs(coral_dir: str | Path) -> list[ComputeJob]:
    root = Path(coral_dir) / "public" / "jobs"
    if not root.is_dir():
        return []
    jobs: list[ComputeJob] = []
    for path in sorted(root.glob("*/job.json")):
        try:
            jobs.append(ComputeJob.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
            continue
    return jobs
