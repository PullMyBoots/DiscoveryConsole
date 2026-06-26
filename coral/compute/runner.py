"""Local runner for open A-space compute jobs."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from uuid import uuid4

from coral.cli._helpers import read_agent_id
from coral.compute.types import ComputeJob, job_dir, write_job
from coral.config import ComputeProfileConfig, CoralConfig
from coral.workspace.breadcrumbs import find_coral_breadcrumb

_BASE_ENV_ALLOWLIST = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "USER",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_job_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"job-{stamp}-{uuid4().hex[:8]}"


def _profile_env(profile: ComputeProfileConfig, assigned_gpu_ids: list[str]) -> dict[str, str]:
    resource = profile.resources()
    if assigned_gpu_ids:
        resource.gpu_ids = assigned_gpu_ids
        resource.gpu_count = len(assigned_gpu_ids)
    env = resource.to_env()
    if profile.cpu_cores > 0:
        cpu = str(profile.cpu_cores)
        env.setdefault("OMP_NUM_THREADS", cpu)
        env.setdefault("MKL_NUM_THREADS", cpu)
        env.setdefault("OPENBLAS_NUM_THREADS", cpu)
        env.setdefault("NUMEXPR_NUM_THREADS", cpu)
    if not assigned_gpu_ids and profile.gpu_count <= 0 and not profile.gpu_ids:
        env.setdefault("CUDA_VISIBLE_DEVICES", "")
    env.update({str(k): str(v) for k, v in profile.env.items()})
    return env


class _HeldLock:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w")
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            self.file = None
            return False
        self.file.write(str(os.getpid()))
        self.file.flush()
        return True

    def release(self) -> None:
        if self.file is None:
            return
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
        self.file = None


class _StateLock:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self) -> _StateLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w")
        fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.file is None:
            return
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None


class _PoolLease:
    def __init__(self, coral_dir: Path, job_id: str):
        self.coral_dir = coral_dir
        self.job_id = job_id
        self.active = True

    def release(self) -> None:
        if not self.active:
            return
        with _StateLock(_pool_state_lock_path(self.coral_dir)):
            state = _read_pool_state(_pool_state_path(self.coral_dir))
            leases = state.setdefault("leases", {})
            if isinstance(leases, dict):
                leases.pop(self.job_id, None)
            _write_pool_state(_pool_state_path(self.coral_dir), state)
        self.active = False


def _safe_lock_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


def _acquire_agent_slots(
    coral_dir: Path,
    *,
    agent_id: str,
    job_class: str,
    limit: int,
) -> list[_HeldLock]:
    safe_agent = _safe_lock_name(agent_id)
    safe_class = _safe_lock_name(job_class)
    root = coral_dir / "private" / "compute_locks" / "agents" / safe_agent / safe_class
    for slot in range(max(limit, 1)):
        lock = _HeldLock(root / f"slot-{slot}.lock")
        if lock.acquire():
            return [lock]
    raise RuntimeError(
        f"compute class {job_class!r} already has {limit} running job(s) for agent {agent_id}"
    )


def _gpu_pool_ids(config: CoralConfig, profile: ComputeProfileConfig) -> list[str]:
    if profile.gpu_ids:
        return list(profile.gpu_ids)
    pool_ids = list(config.compute.pool.gpu_ids)
    if not pool_ids and config.compute.pool.gpu_count > 0:
        pool_ids = [str(index) for index in range(config.compute.pool.gpu_count)]
    return pool_ids


def _acquire_gpu_locks(
    coral_dir: Path,
    config: CoralConfig,
    profile: ComputeProfileConfig,
) -> tuple[list[str], list[_HeldLock]]:
    if profile.gpu_ids:
        ids = list(profile.gpu_ids)
        locks = []
        try:
            for gpu_id in ids:
                lock = _HeldLock(
                    coral_dir
                    / "private"
                    / "compute_locks"
                    / "gpus"
                    / f"{_safe_lock_name(gpu_id)}.lock"
                )
                if not lock.acquire():
                    raise RuntimeError(f"GPU {gpu_id} is already reserved by another compute job")
                locks.append(lock)
        except Exception:
            for lock in locks:
                lock.release()
            raise
        return ids, locks
    if profile.gpu_count <= 0:
        return [], []
    pool_ids = _gpu_pool_ids(config, profile)
    if len(pool_ids) < profile.gpu_count:
        raise RuntimeError(
            f"compute profile requests {profile.gpu_count} GPU(s), "
            f"but compute.pool exposes {len(pool_ids)}"
        )
    for candidate in combinations(pool_ids, profile.gpu_count):
        locks = []
        try:
            for gpu_id in candidate:
                lock = _HeldLock(
                    coral_dir
                    / "private"
                    / "compute_locks"
                    / "gpus"
                    / f"{_safe_lock_name(gpu_id)}.lock"
                )
                if not lock.acquire():
                    raise BlockingIOError
                locks.append(lock)
            return list(candidate), locks
        except BlockingIOError:
            for lock in locks:
                lock.release()
            continue
    raise RuntimeError("No GPU from compute.pool is currently available")


def _pool_state_path(coral_dir: Path) -> Path:
    return coral_dir / "private" / "compute_locks" / "pool_state.json"


def _pool_state_lock_path(coral_dir: Path) -> Path:
    return coral_dir / "private" / "compute_locks" / "pool_state.lock"


def _read_pool_state(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return {"leases": {}}
    if not isinstance(data, dict):
        return {"leases": {}}
    leases = data.get("leases")
    if not isinstance(leases, dict):
        data["leases"] = {}
    return data


def _write_pool_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cleanup_stale_pool_leases(state: dict[str, object]) -> None:
    leases = state.setdefault("leases", {})
    if not isinstance(leases, dict):
        state["leases"] = {}
        return
    for job_id, record in list(leases.items()):
        if not isinstance(record, dict) or not _pid_alive(record.get("pid")):
            leases.pop(job_id, None)


def _pool_usage(state: dict[str, object]) -> dict[str, float]:
    usage = {"cpu_cores": 0.0, "memory_gb": 0.0, "storage_gb": 0.0}
    leases = state.get("leases")
    if not isinstance(leases, dict):
        return usage
    for record in leases.values():
        if not isinstance(record, dict):
            continue
        for key in usage:
            value = record.get(key, 0)
            if isinstance(value, int | float):
                usage[key] += float(value)
    return usage


def _resource_total(resource: object, key: str) -> float:
    value = getattr(resource, key, 0)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _resource_demand(profile: ComputeProfileConfig) -> dict[str, float]:
    resource = profile.resources()
    return {
        "cpu_cores": float(resource.cpu_cores),
        "memory_gb": float(resource.memory_gb),
        "storage_gb": float(resource.storage_gb),
    }


def _format_pool_shortage(shortages: list[str]) -> str:
    return ", ".join(shortages)


def _acquire_pool_lease(
    coral_dir: Path,
    config: CoralConfig,
    profile: ComputeProfileConfig,
    *,
    job_id: str,
    profile_key: str,
) -> _PoolLease | None:
    pool = config.compute.pool
    demand = _resource_demand(profile)
    constrained = [
        key for key, amount in demand.items() if amount > 0 and _resource_total(pool, key) > 0
    ]
    if not constrained:
        return None

    state_path = _pool_state_path(coral_dir)
    with _StateLock(_pool_state_lock_path(coral_dir)):
        state = _read_pool_state(state_path)
        _cleanup_stale_pool_leases(state)
        usage = _pool_usage(state)
        shortages = []
        busy = False
        for key in constrained:
            total = _resource_total(pool, key)
            requested = demand[key]
            if usage[key] + requested > total:
                shortages.append(f"{key}: requested {requested:g}, used {usage[key]:g}, pool {total:g}")
                if usage[key] > 0:
                    busy = True
        if busy:
            raise RuntimeError(
                "compute.pool does not have enough free capacity for "
                f"profile {profile_key!r} ({_format_pool_shortage(shortages)})"
            )

        leases = state.setdefault("leases", {})
        if not isinstance(leases, dict):
            leases = {}
            state["leases"] = leases
        leases[job_id] = {
            "pid": os.getpid(),
            "profile": profile_key,
            "created_at": _now(),
            "cpu_cores": demand["cpu_cores"],
            "memory_gb": demand["memory_gb"],
            "storage_gb": demand["storage_gb"],
        }
        _write_pool_state(state_path, state)

    return _PoolLease(coral_dir, job_id)


def _resource_record(resource: object) -> dict[str, object]:
    return {
        "cpu_cores": getattr(resource, "cpu_cores", 0),
        "memory_gb": getattr(resource, "memory_gb", 0.0),
        "storage_gb": getattr(resource, "storage_gb", 0.0),
        "gpu_count": getattr(resource, "gpu_count", 0),
        "gpu_ids": list(getattr(resource, "gpu_ids", [])),
    }


def _workdir_and_coral(workdir: str | Path) -> tuple[Path, Path]:
    workdir_path = Path(workdir).resolve()
    breadcrumb = find_coral_breadcrumb(workdir_path)
    if breadcrumb is None:
        raise FileNotFoundError(f"No .coral directory found from {workdir_path}")
    coral_dir, _breadcrumb_dir = breadcrumb
    return workdir_path, coral_dir


def _base_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _BASE_ENV_ALLOWLIST or key.startswith("LC_")
    }
    env.setdefault("PATH", os.defpath)
    return env


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> tuple[int | None, str]:
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout if timeout > 0 else None), ""
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            message = f"Command timed out after {timeout}s"
            stderr.write(message + "\n")
            return None, message


def run_compute_job(
    *,
    command: list[str],
    workdir: str | Path = ".",
    agent_id: str | None = None,
    job_class: str = "explore",
    profile_name: str | None = None,
    timeout: int | None = None,
) -> ComputeJob:
    """Run an open A-space command and persist a ComputeJob record."""
    if not command:
        raise ValueError("coral run requires a command")
    workdir_path, coral_dir = _workdir_and_coral(workdir)
    config = CoralConfig.from_yaml(coral_dir / "config.yaml")
    if (
        config.compute.backend == "local"
        and config.evaluation.level != "L1"
        and not config.compute.allow_unisolated_local
    ):
        raise RuntimeError(
            "local `coral run` is disabled for hidden evaluation levels because it cannot "
            "isolate .coral/private from same-user subprocesses. Use evaluation.level=L1, "
            "configure an isolated compute backend, or explicitly set "
            "compute.allow_unisolated_local=true for trusted local experiments."
        )

    if job_class not in config.compute.classes:
        raise ValueError(f"compute class {job_class!r} is not defined")
    class_cfg = config.compute.classes[job_class]
    if class_cfg.allow_private_data:
        raise ValueError("coral run is reserved for open A-space jobs; private data is not mounted")
    profile_key = profile_name or class_cfg.default_profile
    if profile_key not in config.compute.profiles:
        raise ValueError(f"compute profile {profile_key!r} is not defined")
    profile = config.compute.profiles[profile_key]
    effective_timeout = int(timeout if timeout is not None else profile.timeout)
    if effective_timeout < 0:
        raise ValueError("coral run timeout must be >= 0")

    resolved_agent_id = agent_id or read_agent_id(str(workdir_path))
    job_id = _new_job_id()
    held_locks: list[_HeldLock] = []
    pool_lease: _PoolLease | None = None
    try:
        held_locks.extend(
            _acquire_agent_slots(
                coral_dir,
                agent_id=resolved_agent_id,
                job_class=job_class,
                limit=class_cfg.max_running_per_agent,
            )
        )
        pool_lease = _acquire_pool_lease(
            coral_dir,
            config,
            profile,
            job_id=job_id,
            profile_key=profile_key,
        )
        gpu_ids, gpu_locks = _acquire_gpu_locks(coral_dir, config, profile)
        held_locks.extend(gpu_locks)
    except Exception:
        if pool_lease is not None:
            pool_lease.release()
        for lock in held_locks:
            lock.release()
        raise

    root = job_dir(coral_dir, job_id)
    stdout_path = root / "stdout.log"
    stderr_path = root / "stderr.log"
    artifact_dir = root / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    data_dir = coral_dir / "public" / "datasets"
    data_dir.mkdir(parents=True, exist_ok=True)

    env_delta = _profile_env(profile, gpu_ids)
    env_delta.update(
        {
            "CORAL_AGENT_ID": resolved_agent_id,
            "CORAL_JOB_ID": job_id,
            "CORAL_JOB_CLASS": job_class,
            "CORAL_EVAL_LEVEL": config.evaluation.level,
            "CORAL_EVAL_SPACE": "A",
            "CORAL_DATA_DIR": str(data_dir),
            "CORAL_ARTIFACT_DIR": str(artifact_dir),
        }
    )
    full_env = _base_env()
    full_env.update(env_delta)

    resource = profile.resources()
    if gpu_ids:
        resource.gpu_ids = gpu_ids
        resource.gpu_count = len(gpu_ids)

    job = ComputeJob(
        job_id=job_id,
        agent_id=resolved_agent_id,
        job_class=job_class,
        profile=profile_key,
        command=list(command),
        cwd=str(workdir_path),
        status="running",
        created_at=_now(),
        started_at=_now(),
        timeout=effective_timeout,
        resources=_resource_record(resource),
        env=env_delta,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        artifact_dir=str(artifact_dir),
        eval_level=config.evaluation.level,
        eval_space="A",
    )
    write_job(coral_dir, job)

    try:
        exit_code, timeout_error = _run_process(
            command,
            cwd=workdir_path,
            env=full_env,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout=effective_timeout,
        )
        job.exit_code = exit_code
        if timeout_error:
            job.status = "timeout"
            job.error = timeout_error
        else:
            job.status = "succeeded" if exit_code == 0 else "failed"
    except Exception as exc:
        job.exit_code = None
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        with stderr_path.open("a") as stderr:
            stderr.write(job.error + "\n")
    finally:
        job.finished_at = _now()
        write_job(coral_dir, job)
        for lock in reversed(held_locks):
            lock.release()
        if pool_lease is not None:
            pool_lease.release()

    return job
