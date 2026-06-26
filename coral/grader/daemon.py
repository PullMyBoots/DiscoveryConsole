"""Grader daemon: watches .coral/public/attempts/ for pending entries and grades them.

One long-running process per CORAL run. Reuses a single `TaskGrader`
instance across all evals (no per-eval cold start) and grades each attempt
inside an isolated `git worktree add --detach <commit>` checkout, so agent
commits during grading do not perturb the codebase the grader sees.

Design invariants:
- Pending attempts are dispatched through a thread pool of size
  `grader.parallel.max_workers` (default 1 = serial). Bumping the value is
  just configuration; safety is the operator's call — most graders are NOT
  concurrency-safe (Docker port conflicts, GPU contention, shared scratch
  dirs, etc.).
- Writes are atomic via hub.attempts.write_attempt (tmp + rename).
- Daemon is idempotent: re-seeing an already-scored attempt is a no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coral.config import CoralConfig, ResourceConfig
from coral.grader.loader import load_grader
from coral.hub.attempts import (
    get_agent_attempts,
    increment_eval_count,
    read_attempts,
    write_attempt,
)
from coral.types import (
    BUDGET_CLASS_GRADER_ERROR,
    Attempt,
    Task,
    get_budget_class,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SEC = 0.5

# Guards `increment_eval_count` (read-modify-write on .coral/public/eval_count).
# The daemon is the sole writer; this lock is only needed because pending
# attempts can be drained in parallel by multiple worker threads.
_eval_count_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Grader invocation                                                           #
# --------------------------------------------------------------------------- #


def _run_grader(
    config_path: str,
    coral_dir: str,
    codebase_path: str,
    tasks: list,
    resource_override: ResourceConfig | None = None,
    eval_space: str | None = None,
) -> Any:
    """Resolve the entrypoint grader and run one grade() call.

    Timeout enforcement lives inside SubprocessGrader — its worker runs under
    ``subprocess.run(timeout=grader.timeout)``, so a hung grader is killed
    there and reported back as a clean timed-out bundle.

    """
    config = CoralConfig.from_yaml(config_path)
    grader = load_grader(config, coral_dir=coral_dir, eval_space=eval_space)
    if resource_override is not None:
        grader._resource_override = resource_override
    return asyncio.run(grader.grade(codebase_path, tasks))


# --------------------------------------------------------------------------- #
# Isolated worktree management                                                #
# --------------------------------------------------------------------------- #


def _grader_checkouts_dir(coral_dir: Path) -> Path:
    d = coral_dir / "private" / "grader_checkouts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_dir(coral_dir: Path) -> Path:
    """The per-run cloned repo. Production layout: run_dir/repo/. Test layout
    sometimes puts .coral/ directly inside the repo, so fall back to
    coral_dir.parent if that's also a git repo.
    """
    candidate = coral_dir.parent / "repo"
    if _is_git_repo(candidate):
        return candidate
    if _is_git_repo(coral_dir.parent):
        return coral_dir.parent
    raise RuntimeError(
        f"Cannot locate source repo from {coral_dir} (tried {candidate} and {coral_dir.parent})"
    )


def _is_git_repo(path: Path) -> bool:
    """True if `path` exists and contains a .git directory/file."""
    return path.is_dir() and (path / ".git").exists()


_WORKTREE_ADD_RETRIES = 3
_WORKTREE_ADD_RETRY_BACKOFF = 0.05  # seconds; the worktree TOCTOU window is <100ms
# Match the exact race-condition message git emits when two ``git worktree add``
# invocations collide on the same repo. Strings come from git's own
# builtin/worktree.c; matching verbatim keeps the retry scoped to the race
# rather than masking real failures.
_WORKTREE_RACE_MARKERS = (
    "failed to read .git/worktrees/",
    "cannot lock ref",  # extra coverage for the same TOCTOU on refs
)


def _is_worktree_race(stderr: str) -> bool:
    return any(marker in stderr for marker in _WORKTREE_RACE_MARKERS)


def _add_isolated_worktree(repo_dir: Path, commit_hash: str, dest: Path) -> None:
    """Create a detached worktree at `dest` pointing at `commit_hash`.

    Force-removes any prior checkout at the same path (crash-recovery).
    Retries on git's known ``worktree add`` TOCTOU race ("failed to read
    .git/worktrees/<id>/commondir") — two parallel invocations from the
    same repo can race even when the calls are independent. Retries with a
    small backoff; preserves ``grader.parallel.max_workers > 1`` overlap.
    """
    if dest.exists():
        _remove_worktree(repo_dir, dest)

    last_err = ""
    for attempt in range(_WORKTREE_ADD_RETRIES):
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(dest), commit_hash],
            capture_output=True,
            text=True,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            return
        last_err = result.stderr.strip()
        if not _is_worktree_race(last_err) or attempt == _WORKTREE_ADD_RETRIES - 1:
            break
        time.sleep(_WORKTREE_ADD_RETRY_BACKOFF)
    raise RuntimeError(f"git worktree add --detach {commit_hash[:12]} failed: {last_err}")


def _remove_worktree(repo_dir: Path, dest: Path) -> None:
    """Remove a worktree. Best-effort; logs on failure but does not raise."""
    # git worktree remove is the preferred path; fall back to rmtree + prune.
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(dest)],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    if result.returncode != 0:
        logger.warning(
            "git worktree remove %s failed (rc=%d): %s — falling back to rmtree",
            dest,
            result.returncode,
            result.stderr.strip(),
        )
        try:
            if dest.exists():
                shutil.rmtree(dest)
        except OSError as e:
            logger.warning("rmtree %s failed: %s", dest, e)
        subprocess.run(
            ["git", "worktree", "prune"],
            capture_output=True,
            text=True,
            cwd=str(repo_dir),
        )


# --------------------------------------------------------------------------- #
# Per-attempt grading                                                         #
# --------------------------------------------------------------------------- #


def _compute_status(
    score: float | None,
    agent_id: str,
    commit_hash: str,
    coral_dir: Path,
    minimize: bool,
) -> str:
    """Compare `score` to this agent's previous best to classify the attempt."""
    if score is None:
        return "crashed"

    prev_attempts = get_agent_attempts(str(coral_dir), agent_id)
    prev_scores = [
        a.score for a in prev_attempts if a.score is not None and a.commit_hash != commit_hash
    ]
    if not prev_scores:
        return "improved"

    prev_best = min(prev_scores) if minimize else max(prev_scores)
    if (minimize and score < prev_best) or (not minimize and score > prev_best):
        return "improved"
    if score == prev_best:
        return "baseline"
    return "regressed"


def _resource_env(config: CoralConfig, *, eval_space: str | None = None) -> dict[str, str]:
    """Return selected eval resource env with profile overrides applied."""
    return _effective_eval_resources(config, eval_space=eval_space).to_env()


def _effective_eval_resources(config: CoralConfig, *, eval_space: str | None = None) -> ResourceConfig:
    """Return selected per-job eval resource demand with profile overrides."""
    grader_config = config.grader.for_space(eval_space or config.evaluation.score_space())
    base = grader_config.resources
    profile = grader_config.profiles.get(grader_config.profile)
    if profile is not None and profile.resources.active():
        override = profile.resources
        return ResourceConfig(
            cpu_cores=override.cpu_cores or base.cpu_cores,
            memory_gb=override.memory_gb or base.memory_gb,
            storage_gb=override.storage_gb or base.storage_gb,
            gpu_count=override.gpu_count or base.gpu_count,
            gpu_ids=override.gpu_ids or base.gpu_ids,
        )
    return base


def _scheduler_job_resources(config: CoralConfig, *, eval_space: str | None = None) -> ResourceConfig:
    """Return per-job demand for daemon scheduling.

    If a grader GPU pool is configured but the task omitted per-job GPU demand,
    default each eval to one GPU. This matches the common GPU assumption that one
    evaluator process owns one visible device unless the task says otherwise.
    """
    demand = _effective_eval_resources(config, eval_space=eval_space)
    pool = config.grader.parallel.resources
    has_gpu_pool = bool(pool.gpu_count > 0 or pool.gpu_ids)
    has_gpu_demand = bool(demand.gpu_count > 0 or demand.gpu_ids)
    if has_gpu_pool and not has_gpu_demand:
        return ResourceConfig(
            cpu_cores=demand.cpu_cores,
            memory_gb=demand.memory_gb,
            storage_gb=demand.storage_gb,
            gpu_count=1,
        )
    return demand


@dataclass
class _ResourceLease:
    resource: ResourceConfig
    env: dict[str, str]
    cpu_cores: int = 0
    memory_gb: float = 0.0
    gpu_ids: tuple[str, ...] = ()
    oversubscribed: bool = False


class _ResourceScheduler:
    """Small first-fit scheduler for evaluator resource budgets."""

    def __init__(self, *, max_workers: int, pool: ResourceConfig, per_job: ResourceConfig) -> None:
        self.max_workers = max(max_workers, 1)
        self.pool = pool
        self.per_job = per_job
        self.running = 0
        self.used_cpu = 0
        self.used_memory = 0.0
        self.gpu_id_pool = self._gpu_id_pool(pool)
        self.free_gpu_ids = list(self.gpu_id_pool)

    @property
    def active(self) -> bool:
        return self.pool.active()

    def can_start_more(self) -> bool:
        return self.running < self.max_workers

    def try_acquire(self, per_job: ResourceConfig | None = None) -> _ResourceLease | None:
        if not self.can_start_more():
            return None
        per_job = per_job or self.per_job
        cpu = per_job.cpu_cores
        memory = per_job.memory_gb
        gpu_count = self._required_gpu_count(per_job)

        oversubscribed = False
        if self.pool.cpu_cores > 0 and cpu > 0 and self.used_cpu + cpu > self.pool.cpu_cores:
            if self.running > 0:
                return None
            oversubscribed = True
        if self.pool.memory_gb > 0 and memory > 0 and self.used_memory + memory > self.pool.memory_gb:
            if self.running > 0:
                return None
            oversubscribed = True

        assigned_gpus: tuple[str, ...] = ()
        if gpu_count > 0:
            if per_job.gpu_ids:
                assigned_gpus = tuple(per_job.gpu_ids)
                if self.free_gpu_ids:
                    if not set(assigned_gpus).issubset(set(self.free_gpu_ids)):
                        if self.running > 0:
                            return None
                        oversubscribed = True
                    else:
                        self.free_gpu_ids = [
                            gpu_id for gpu_id in self.free_gpu_ids if gpu_id not in assigned_gpus
                        ]
            elif self.free_gpu_ids:
                if len(self.free_gpu_ids) < gpu_count:
                    if self.running > 0:
                        return None
                    oversubscribed = True
                assigned_gpus = tuple(self.free_gpu_ids[:gpu_count])
                self.free_gpu_ids = self.free_gpu_ids[len(assigned_gpus) :]
            elif self.pool.gpu_count > 0:
                if self.running > 0:
                    return None
                oversubscribed = gpu_count > self.pool.gpu_count

        self.running += 1
        if self.pool.cpu_cores > 0 and cpu > 0:
            self.used_cpu += cpu
        if self.pool.memory_gb > 0 and memory > 0:
            self.used_memory += memory
        resource = self._lease_resource(per_job, assigned_gpus)
        return _ResourceLease(
            resource=resource,
            env=resource.to_env(),
            cpu_cores=cpu if self.pool.cpu_cores > 0 else 0,
            memory_gb=memory if self.pool.memory_gb > 0 else 0.0,
            gpu_ids=assigned_gpus,
            oversubscribed=oversubscribed,
        )

    def release(self, lease: _ResourceLease) -> None:
        self.running = max(0, self.running - 1)
        if lease.cpu_cores:
            self.used_cpu = max(0, self.used_cpu - lease.cpu_cores)
        if lease.memory_gb:
            self.used_memory = max(0.0, self.used_memory - lease.memory_gb)
        if lease.gpu_ids and self.gpu_id_pool:
            self.free_gpu_ids.extend(lease.gpu_ids)
            order = {gpu_id: index for index, gpu_id in enumerate(self.gpu_id_pool)}
            self.free_gpu_ids = sorted(set(self.free_gpu_ids), key=lambda gpu_id: order.get(gpu_id, 9999))

    def _required_gpu_count(self, per_job: ResourceConfig) -> int:
        if per_job.gpu_count > 0:
            return per_job.gpu_count
        return len(per_job.gpu_ids)

    def _lease_resource(self, per_job: ResourceConfig, assigned_gpus: tuple[str, ...]) -> ResourceConfig:
        return ResourceConfig(
            cpu_cores=per_job.cpu_cores,
            memory_gb=per_job.memory_gb,
            storage_gb=per_job.storage_gb,
            gpu_count=len(assigned_gpus) if assigned_gpus else per_job.gpu_count,
            gpu_ids=list(assigned_gpus) if assigned_gpus else list(per_job.gpu_ids),
        )

    @staticmethod
    def _gpu_id_pool(pool: ResourceConfig) -> list[str]:
        if pool.gpu_ids:
            return list(pool.gpu_ids)
        if pool.gpu_count > 0:
            return [str(index) for index in range(pool.gpu_count)]
        return []


def _resource_scheduler(config: CoralConfig, *, max_workers: int) -> _ResourceScheduler | None:
    pool = config.grader.parallel.resources
    if not pool.active():
        return None
    return _ResourceScheduler(
        max_workers=max_workers,
        pool=pool,
        per_job=_scheduler_job_resources(config),
    )


def _scheduler_job_resources_for_attempt(
    coral_dir: Path | None,
    attempt: Attempt,
    config: CoralConfig,
) -> ResourceConfig:
    """Return scheduler demand for the attempt's trusted eval route."""
    if coral_dir is None:
        return _scheduler_job_resources(config)
    _level, eval_space, _final = _trusted_eval_route(coral_dir, attempt, config)
    return _scheduler_job_resources(config, eval_space=eval_space)


def planned_evaluating_hashes(
    pending: list[Attempt],
    config: CoralConfig,
    *,
    max_workers: int | None = None,
    coral_dir: Path | None = None,
) -> set[str]:
    """Return the pending attempts that would start in the first scheduler wave."""
    worker_count = max_workers or config.grader.parallel.max_workers
    scheduler = _resource_scheduler(config, max_workers=worker_count)
    if scheduler is None:
        return {attempt.commit_hash for attempt in pending[:worker_count]}
    hashes: set[str] = set()
    for attempt in pending:
        lease = scheduler.try_acquire(_scheduler_job_resources_for_attempt(coral_dir, attempt, config))
        if lease is None:
            continue
        hashes.add(attempt.commit_hash)
        if not scheduler.can_start_more():
            break
    return hashes


def _select_pending_wave(
    pending: list[Attempt],
    config: CoralConfig,
    *,
    max_workers: int,
    coral_dir: Path | None = None,
) -> list[Attempt]:
    """Return the attempts to launch under the current daemon config.

    The long-running daemon reloads config between waves, so it should not
    submit every pending attempt to a thread pool with stale profile/resource
    settings. One-shot callers can still pass the full pending list directly
    to `_drain_pending`.
    """
    if not pending:
        return []
    scheduler = _resource_scheduler(config, max_workers=max_workers)
    if scheduler is None:
        return pending[:max_workers]

    wave: list[Attempt] = []
    for attempt in pending:
        lease = scheduler.try_acquire(_scheduler_job_resources_for_attempt(coral_dir, attempt, config))
        if lease is None:
            continue
        wave.append(attempt)
        if not scheduler.can_start_more():
            break
    return wave


def _read_attempt_file(path: Path) -> Attempt | None:
    try:
        return Attempt.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, OSError, TypeError):
        return None


def _build_feedback(bundle: Any) -> str:
    """Combine bundle-level feedback + per-score explanations into one string."""
    parts = []
    if getattr(bundle, "feedback", None):
        parts.append(bundle.feedback)
    scores = getattr(bundle, "scores", None) or {}
    for name, s in scores.items():
        explanation = getattr(s, "explanation", None)
        if explanation:
            parts.append(f"{name}: {explanation}")
    return "\n".join(parts)


# Fallback shared-dir names per runtime. Used by _append_eval_logs_hint when
# the runtime isn't registered in coral.agent.registry (e.g. custom entrypoint
# runtimes). Mirrors the .shared_dir_name contract in coral/agent/runtime.py.
_DEFAULT_SHARED_DIR_BY_RUNTIME: dict[str, str] = {
    "claude_code": ".claude",
    "codex": ".codex",
    "opencode": ".opencode",
    "cursor_agent": ".cursor",
    "kiro": ".kiro",
}


def _resolve_shared_dir(runtime_name: str) -> str:
    """Look up a runtime's shared-dir name; fall back to .claude.

    The shared dir is the worktree-local directory the agent reads state
    from — `.claude/`, `.codex/`, etc. The eval_logs symlink in
    setup_shared_state targets that dir, so the agent accesses their
    attempt's logs at `<shared_dir>/eval_logs/<hash>/`.
    """
    if not runtime_name:
        return ".claude"
    try:
        from coral.agent.registry import get_runtime

        return get_runtime(runtime_name).shared_dir_name
    except (KeyError, ImportError, Exception):  # noqa: BLE001 — best-effort hint
        return _DEFAULT_SHARED_DIR_BY_RUNTIME.get(runtime_name, ".claude")


def _append_eval_logs_hint(
    feedback: str,
    commit_hash: str,
    runtime_name: str,
) -> str:
    """Append a footer pointing the agent at their per-attempt trace logs.

    Universal across success / timeout / crashed feedback paths. The grader's
    own _parse_job_result also includes a Logs block with per-trial paths;
    this footer is the safety net for the cases where the grader never
    reached _parse_job_result (timeout mid-run, harbor never produced a
    result.json, exception in the worker subprocess, etc.).

    The path is given in BOTH worktree-relative form (always correct) and
    concrete-with-shared-dir form (correct for this run's runtime). The
    agent prepends their own shared-dir to read.
    """
    shared_dir = _resolve_shared_dir(runtime_name)
    rel_path = f"eval_logs/{commit_hash}/"
    footer = (
        f"\n\n### Trace logs\n"
        f"Per-attempt harbor logs (agent trajectories, terminal recordings, "
        f"verifier output): `{rel_path}` — in your worktree that resolves to "
        f"`{shared_dir}/{rel_path}` (your runtime's shared state dir; "
        f"`.codex/`, `.opencode/`, `.kiro/` on other runtimes)."
    )
    return (feedback or "") + footer


def _is_baseline_attempt(attempt: Attempt) -> bool:
    metadata = attempt.metadata or {}
    return bool(
        metadata.get("baseline") is True
        or metadata.get("is_baseline") is True
        or metadata.get("reference") == "baseline"
        or metadata.get("kind") == "baseline"
    )


def _score_sort_key(attempt: Attempt, *, minimize: bool) -> float:
    value = float(attempt.score or 0.0)
    return value if minimize else -value


def _rank_for_score(scored: list[Attempt], commit_hash: str, *, minimize: bool) -> int | None:
    ordered = sorted(scored, key=lambda a: _score_sort_key(a, minimize=minimize))
    for index, attempt in enumerate(ordered, start=1):
        if attempt.commit_hash == commit_hash:
            return index
    return None


def _attempt_summary(attempt: Attempt, rank: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": attempt.agent_id,
        "commit_hash": attempt.commit_hash,
        "score": attempt.score,
        "title": attempt.title,
        "timestamp": attempt.timestamp,
    }
    if rank is not None:
        payload["rank"] = rank
    return payload


def _self_history(
    attempts: list[Attempt],
    *,
    current: Attempt,
    minimize: bool,
) -> dict[str, Any]:
    previous = [
        a
        for a in attempts
        if a.agent_id == current.agent_id
        and a.commit_hash != current.commit_hash
        and a.score is not None
        and not _is_baseline_attempt(a)
    ]
    previous.sort(key=lambda a: a.timestamp)
    scores = [float(a.score) for a in previous]
    current_score = float(current.score) if current.score is not None else None
    prior_best = (min(scores) if minimize else max(scores)) if scores else None
    prior_last = scores[-1] if scores else None
    last_five = scores[-5:]
    best_so_far: float | None = None
    non_improving_streak_before = 0
    for score in scores:
        improved = best_so_far is None or (score < best_so_far if minimize else score > best_so_far)
        if improved:
            best_so_far = score
            non_improving_streak_before = 0
        else:
            non_improving_streak_before += 1
    non_improving_streak = non_improving_streak_before
    if current_score is not None and prior_best is not None:
        current_improved = current_score < prior_best if minimize else current_score > prior_best
        non_improving_streak = 0 if current_improved else non_improving_streak_before + 1

    history: dict[str, Any] = {
        "attempts": len(scores),
        "previous": prior_last,
        "best_before": prior_best,
        "mean_last_5": (sum(last_five) / len(last_five)) if last_five else None,
        "non_improving_streak": non_improving_streak,
    }
    if current_score is not None and prior_last is not None:
        history["delta_previous"] = current_score - prior_last
    if current_score is not None and prior_best is not None:
        history["delta_best_before"] = current_score - prior_best
    return history


def _baseline_summaries(
    attempts: list[Attempt],
    *,
    current_score: float | None,
    minimize: bool,
    limit: int = 5,
) -> list[dict[str, Any]]:
    baselines = [a for a in attempts if a.score is not None and _is_baseline_attempt(a)]
    baselines.sort(key=lambda a: _score_sort_key(a, minimize=minimize))
    result: list[dict[str, Any]] = []
    for attempt in baselines[:limit]:
        payload = _attempt_summary(attempt)
        metadata = attempt.metadata or {}
        for key in ("baseline_name", "method", "reference_name"):
            if metadata.get(key):
                payload["name"] = metadata[key]
                break
        else:
            payload["name"] = attempt.title or "baseline"
        if current_score is not None and attempt.score is not None:
            payload["delta"] = current_score - float(attempt.score)
        result.append(payload)
    return result


def _metric_rank(
    attempts: list[Attempt],
    *,
    metric_name: str,
    current_hash: str,
    current_value: Any,
    minimize: bool,
) -> int | None:
    values: list[tuple[str, float]] = []
    for attempt in attempts:
        components = (attempt.metadata or {}).get("score_components")
        if not isinstance(components, dict):
            continue
        metric = components.get(metric_name)
        if not isinstance(metric, dict):
            continue
        value = metric.get("value")
        if isinstance(value, int | float):
            values.append((attempt.commit_hash, float(value)))
    if isinstance(current_value, int | float) and not any(h == current_hash for h, _ in values):
        values.append((current_hash, float(current_value)))
    if not values:
        return None
    values.sort(key=lambda item: item[1], reverse=not minimize)
    for index, (commit_hash, _value) in enumerate(values, start=1):
        if commit_hash == current_hash:
            return index
    return None


def _augment_metric_report(
    report: dict[str, Any],
    *,
    attempts: list[Attempt],
    current: Attempt,
    minimize: bool,
) -> None:
    components = current.metadata.get("score_components") if current.metadata else None
    metrics = report.setdefault("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
        report["metrics"] = metrics
    if isinstance(components, dict):
        for name, component in components.items():
            if not isinstance(component, dict):
                continue
            metric = metrics.setdefault(str(name), {})
            if not isinstance(metric, dict):
                metric = {}
                metrics[str(name)] = metric
            metric.setdefault("value", component.get("value"))
            if component.get("explanation"):
                metric.setdefault("explanation", component.get("explanation"))
            metadata = component.get("metadata")
            if isinstance(metadata, dict) and metadata.get("direction"):
                metric.setdefault("direction", metadata.get("direction"))
            metric.setdefault("direction", "minimize" if minimize else "maximize")

    for name, metric in list(metrics.items()):
        if not isinstance(metric, dict):
            continue
        metric_minimize = str(metric.get("direction", "")).lower() == "minimize"
        rank = _metric_rank(
            attempts,
            metric_name=str(name),
            current_hash=current.commit_hash,
            current_value=metric.get("value"),
            minimize=metric_minimize,
        )
        if rank is not None:
            metric.setdefault("rank", rank)


def _build_standard_eval_report(
    *,
    attempt: Attempt,
    metadata: dict[str, Any],
    status: str,
    feedback: str,
    coral_dir: Path,
    config: CoralConfig,
    grader_config: Any,
    eval_level: str,
    eval_space: str,
    minimize: bool,
) -> dict[str, Any]:
    """Build or augment the standard report persisted as metadata.eval_report."""
    existing = metadata.get("eval_report")
    report = dict(existing) if isinstance(existing, dict) else {}
    report["eval_level"] = eval_level
    report["eval_space"] = eval_space
    report["eval_profile"] = grader_config.profile

    if attempt.score is None or status in {"crashed", "timeout"}:
        report["status"] = "failed"
        report.setdefault("error_type", "timeout" if status == "timeout" else "runtime_error")
        report.setdefault("error_message", feedback or status)
        report.setdefault("stage", status if status in {"timeout", "crashed"} else "evaluate")
        report.setdefault("log_path", f"eval_logs/{attempt.commit_hash}/")
        report.setdefault(
            "message_for_agent",
            "The eval failed before producing a score. Fix the reported error before optimizing.",
        )
        return report

    report["status"] = "success"
    report.setdefault("accepted", True)
    direction = "minimize" if minimize else "maximize"
    score_block = dict(report.get("score") or {})
    score_block.setdefault("total", attempt.score)
    score_block.setdefault("direction", direction)

    attempts = read_attempts(coral_dir)
    scored = [
        a
        for a in attempts
        if a.score is not None
        and a.budget_class == "real"
        and not _is_baseline_attempt(a)
        and a.commit_hash != attempt.commit_hash
    ]
    scored_with_current = [
        *scored,
        attempt,
    ]
    rank = _rank_for_score(scored_with_current, attempt.commit_hash, minimize=minimize)
    if rank is not None:
        score_block.setdefault("rank", rank)
    ordered = sorted(scored_with_current, key=lambda a: _score_sort_key(a, minimize=minimize))
    score_block.setdefault(
        "top_k",
        [_attempt_summary(a, rank=i) for i, a in enumerate(ordered[:5], start=1)],
    )
    report["score"] = score_block
    report.setdefault(
        "self_history",
        _self_history(scored_with_current, current=attempt, minimize=minimize),
    )
    report.setdefault(
        "baselines",
        _baseline_summaries(
            attempts,
            current_score=float(attempt.score) if attempt.score is not None else None,
            minimize=minimize,
        ),
    )
    _augment_metric_report(report, attempts=scored_with_current, current=attempt, minimize=minimize)
    report.setdefault(
        "message_for_agent",
        "Use rank, self-history, baseline deltas, and metric ranks to decide the next change.",
    )
    return report


def _format_eval_report_feedback(report: dict[str, Any]) -> str:
    """Render a compact agent-facing summary from metadata.eval_report."""
    if report.get("status") == "failed":
        parts = [
            "### Eval report",
            "Result status: failed",
            f"Error type: {report.get('error_type', 'runtime_error')}",
            f"Stage: {report.get('stage', 'evaluate')}",
            f"Error: {report.get('error_message', '')}",
        ]
        if report.get("log_path"):
            parts.append(f"Logs: `{report['log_path']}`")
        if report.get("message_for_agent"):
            parts.append(str(report["message_for_agent"]))
        return "\n".join(parts)

    score = report.get("score") if isinstance(report.get("score"), dict) else {}
    parts = [
        "### Eval report",
        "Result status: success",
        f"Accepted: {'yes' if report.get('accepted') else 'no'}",
        f"Total score: {score.get('total')}",
    ]
    if score.get("rank") is not None:
        parts.append(f"Rank: {score.get('rank')}")
    history = report.get("self_history")
    if isinstance(history, dict):
        parts.append(
            "Self history: "
            f"attempts={history.get('attempts')}, "
            f"previous={history.get('previous')}, "
            f"best_before={history.get('best_before')}, "
            f"mean_last_5={history.get('mean_last_5')}, "
            f"delta_previous={history.get('delta_previous')}, "
            f"delta_best={history.get('delta_best_before')}, "
            f"no_improve={history.get('non_improving_streak')}"
        )
    baselines = report.get("baselines")
    if isinstance(baselines, list) and baselines:
        compact = ", ".join(
            f"{b.get('name', b.get('agent_id', 'baseline'))}: {b.get('score')} (delta {b.get('delta')})"
            for b in baselines[:3]
            if isinstance(b, dict)
        )
        if compact:
            parts.append(f"Baselines: {compact}")
    top_k = score.get("top_k")
    if isinstance(top_k, list) and top_k:
        compact_top = ", ".join(
            f"#{item.get('rank')} {item.get('agent_id')} {item.get('score')}"
            for item in top_k[:5]
            if isinstance(item, dict)
        )
        if compact_top:
            parts.append(f"Top 5: {compact_top}")
    metrics = report.get("metrics")
    if isinstance(metrics, dict) and metrics:
        metric_parts = []
        for name, metric in list(metrics.items())[:8]:
            if isinstance(metric, dict):
                metric_parts.append(
                    f"{name}={metric.get('value')} rank={metric.get('rank')} ({metric.get('direction')})"
                )
        if metric_parts:
            parts.append("Metrics: " + "; ".join(metric_parts))
    if report.get("message_for_agent"):
        parts.append(str(report["message_for_agent"]))
    return "\n".join(parts)


def _trusted_eval_route(coral_dir: Path, attempt: Attempt, config: CoralConfig) -> tuple[str, str, bool]:
    """Return trusted eval routing for a pending attempt.

    Public attempt JSON is agent-editable shared state. C-space routing must
    come from the private submit ledger written by `coral eval --final`, not
    from metadata embedded in the public attempt file.
    """
    request_path = coral_dir / "private" / "eval_requests" / f"{attempt.commit_hash}.json"
    if request_path.is_file():
        try:
            data = json.loads(request_path.read_text())
        except (json.JSONDecodeError, OSError, TypeError):
            data = {}
        if data.get("commit_hash") == attempt.commit_hash:
            level = str(data.get("eval_level") or config.evaluation.level).upper()
            final = bool(data.get("eval_final"))
            try:
                space = config.evaluation.score_space(final=final)
            except ValueError:
                logger.warning(
                    "Ignoring invalid final route for %s under evaluation.level=%s",
                    attempt.commit_hash[:12],
                    config.evaluation.level,
                )
                final = False
                space = config.evaluation.score_space(final=False)
            recorded_space = str(data.get("eval_space") or "").upper()
            if recorded_space and recorded_space != space:
                logger.warning(
                    "Ignoring inconsistent private eval route for %s: recorded %s, expected %s",
                    attempt.commit_hash[:12],
                    recorded_space,
                    space,
                )
            return level, space, final

    # Legacy pending attempts predate the private route ledger. Do not trust
    # public metadata to promote them into C-space.
    return config.evaluation.level, config.evaluation.score_space(final=False), False


def _grade_one(
    attempt: Attempt,
    config_path: Path,
    coral_dir: Path,
    config: CoralConfig,
    resource_override: ResourceConfig | None = None,
) -> Attempt:
    """Grade a single pending attempt and return the finalized Attempt record."""
    eval_level, eval_space, eval_final = _trusted_eval_route(coral_dir, attempt, config)
    grader_config = config.grader.for_space(eval_space)
    resource_env = (
        resource_override.to_env()
        if resource_override is not None
        else _resource_env(config, eval_space=eval_space)
    )
    # Task.metadata is the canonical channel for surfacing per-attempt context
    # to the user's grader (read via TaskGrader.tune / .budget_class).
    # Final budget_class may flip to "grader_error" below.
    budget_class = get_budget_class(attempt.metadata)
    task = Task(
        id=config.task.name,
        name=config.task.name,
        description=config.task.description,
        metadata={
            "budget_class": budget_class,
            "agent_id": attempt.agent_id,
            "commit_hash": attempt.commit_hash,
            "eval_level": eval_level,
            "eval_space": eval_space,
            "eval_final": eval_final,
            "eval_version": grader_config.eval_version,
            "eval_profile": grader_config.profile,
            "resources": resource_env,
        },
    )
    timeout = grader_config.timeout
    minimize = grader_config.direction == "minimize"
    repo_dir = _repo_dir(coral_dir)
    checkout_path = _grader_checkouts_dir(coral_dir) / attempt.commit_hash

    score: float | None = None
    status = "crashed"
    feedback = ""
    metadata: dict = {}
    grader_completed = False

    try:
        _add_isolated_worktree(repo_dir, attempt.commit_hash, checkout_path)
        try:
            bundle = _run_grader(
                str(config_path),
                str(coral_dir),
                str(checkout_path),
                [task],
                resource_override=resource_override,
                eval_space=eval_space,
            )
            score = bundle.aggregated
            feedback = _build_feedback(bundle)
            metadata = dict(getattr(bundle, "metadata", None) or {})
            score_components = _score_components_from_bundle(bundle)
            if score_components:
                metadata.setdefault("score_components", score_components)
            metadata.setdefault("aggregated_score", score)
            grader_completed = True
        finally:
            _remove_worktree(repo_dir, checkout_path)
    except TimeoutError:
        logger.error("Grader timed out on %s after %ss", attempt.commit_hash[:12], timeout)
        status = "timeout"
        feedback = f"Eval timed out after {timeout}s."
        budget_class = BUDGET_CLASS_GRADER_ERROR
    except Exception as e:
        logger.exception("Grader crashed on %s", attempt.commit_hash[:12])
        status = "crashed"
        feedback = str(e)
        budget_class = BUDGET_CLASS_GRADER_ERROR

    base_attempt = attempt

    if grader_completed:
        status = _compute_status(
            score,
            base_attempt.agent_id,
            base_attempt.commit_hash,
            coral_dir,
            minimize,
        )

    # Carry forward any pending metadata the grader bundle didn't overwrite,
    # then stamp the final budget_class (always wins over any pending value).
    for k, v in (base_attempt.metadata or {}).items():
        metadata.setdefault(k, v)
    metadata["eval_level"] = eval_level
    metadata["eval_space"] = eval_space
    if eval_final:
        metadata["eval_final"] = True
    else:
        metadata.pop("eval_final", None)
    metadata["eval_version"] = grader_config.eval_version
    metadata["eval_profile"] = grader_config.profile
    metadata["resources"] = resource_env
    metadata["budget_class"] = budget_class

    report_attempt = Attempt(
        commit_hash=base_attempt.commit_hash,
        agent_id=base_attempt.agent_id,
        title=base_attempt.title,
        score=score,
        status=status,
        parent_hash=base_attempt.parent_hash,
        timestamp=base_attempt.timestamp,
        feedback=feedback,
        shared_state_hash=base_attempt.shared_state_hash,
        parent_shared_state_hash=base_attempt.parent_shared_state_hash,
        metadata=metadata,
    )
    eval_report = _build_standard_eval_report(
        attempt=report_attempt,
        metadata=metadata,
        status=status,
        feedback=feedback,
        coral_dir=coral_dir,
        config=config,
        grader_config=grader_config,
        eval_level=eval_level,
        eval_space=eval_space,
        minimize=minimize,
    )
    metadata["eval_report"] = eval_report

    report_feedback = _format_eval_report_feedback(eval_report)
    if report_feedback:
        feedback = f"{feedback}\n\n{report_feedback}" if feedback else report_feedback

    # Append the per-attempt eval_logs path so the agent can always find
    # their trace logs, regardless of which feedback path produced this
    # result (success / timeout / crashed). This is the universal safety
    # net — see _append_eval_logs_hint for the contract.
    feedback = _append_eval_logs_hint(feedback, attempt.commit_hash, config.agents.runtime)

    finalized = Attempt(
        commit_hash=base_attempt.commit_hash,
        agent_id=base_attempt.agent_id,
        title=base_attempt.title,
        score=score,
        status=status,
        parent_hash=base_attempt.parent_hash,
        # Preserve original submission timestamp; daemon doesn't re-stamp.
        timestamp=base_attempt.timestamp,
        feedback=feedback,
        shared_state_hash=base_attempt.shared_state_hash,
        parent_shared_state_hash=base_attempt.parent_shared_state_hash,
        metadata=metadata,
    )
    write_attempt(str(coral_dir), finalized)
    with _eval_count_lock:
        count = increment_eval_count(coral_dir)
    logger.info(
        "Graded #%d %s -> score=%s status=%s",
        count,
        attempt.commit_hash[:12],
        f"{score:.6f}" if score is not None else "None",
        status,
    )
    return finalized


def _score_components_from_bundle(bundle: Any) -> dict[str, dict[str, Any]]:
    """Return JSON-friendly per-score details from a ScoreBundle-like object."""
    scores = getattr(bundle, "scores", None)
    if not isinstance(scores, dict):
        return {}

    components: dict[str, dict[str, Any]] = {}
    for name, score in scores.items():
        key = str(name)
        if hasattr(score, "to_dict"):
            value = score.to_dict()
            if isinstance(value, dict):
                components[key] = value
                continue
        components[key] = {
            "value": getattr(score, "value", None),
            "name": getattr(score, "name", key),
            "explanation": getattr(score, "explanation", None),
            "metadata": dict(getattr(score, "metadata", {}) or {}),
        }
    return components


# --------------------------------------------------------------------------- #
# Main loop                                                                   #
# --------------------------------------------------------------------------- #


def _find_pending(coral_dir: Path) -> list[Attempt]:
    """Return pending attempts oldest first."""
    attempt_dirs = [coral_dir / "public" / "attempts"]
    pending: list[Attempt] = []
    for d in attempt_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                a = Attempt.from_dict(data)
            except Exception:
                continue
            if a.status == "pending" and a.score is None:
                pending.append(a)
    pending.sort(key=lambda x: x.timestamp)
    return pending


def _safe_grade_one(
    attempt: Attempt,
    config_path: Path,
    coral_dir: Path,
    config: CoralConfig,
    resource_override: ResourceConfig | None = None,
) -> Attempt | None:
    """Grade an attempt, swallowing truly unexpected errors as `crashed`.

    Per-attempt failures (timeout, grader exception) are already turned into
    `crashed`/`timeout` Attempts inside `_grade_one`. This wrapper is the last
    line of defense so a thread in the pool can't take the daemon down.
    Returns the finalized Attempt, or None if even the crash-record write
    failed.
    """
    try:
        return _grade_one(attempt, config_path, coral_dir, config, resource_override)
    except Exception:
        logger.exception("Unhandled error grading %s; marking crashed", attempt.commit_hash[:12])
        try:
            base_attempt = attempt
            metadata = dict(base_attempt.metadata or {})
            config = CoralConfig.from_yaml(coral_dir / "config.yaml")
            resource_env = (
                resource_override.to_env() if resource_override is not None else _resource_env(config)
            )
            metadata.setdefault("resources", resource_env)
            metadata["budget_class"] = BUDGET_CLASS_GRADER_ERROR
            crashed = Attempt(
                commit_hash=base_attempt.commit_hash,
                agent_id=base_attempt.agent_id,
                title=base_attempt.title,
                score=None,
                status="crashed",
                parent_hash=base_attempt.parent_hash,
                timestamp=base_attempt.timestamp,
                feedback="Grader daemon hit an unexpected error; see logs.",
                shared_state_hash=base_attempt.shared_state_hash,
                parent_shared_state_hash=base_attempt.parent_shared_state_hash,
                metadata=metadata,
            )
            write_attempt(str(coral_dir), crashed)
            with _eval_count_lock:
                increment_eval_count(coral_dir)
            return crashed
        except Exception:
            logger.exception("Failed to record crash for %s", attempt.commit_hash[:12])
            return None


def _drain_pending(
    pending: list[Attempt],
    config_path: Path,
    coral_dir: Path,
    config: CoralConfig,
    *,
    max_workers: int,
    liveness_file: Path | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[Attempt]:
    """Grade `pending` attempts via a worker pool of size `max_workers`.

    `max_workers=1` is serial — same behavior as the pre-pool daemon. Larger
    values let the daemon overlap grades when the operator has set
    `grader.parallel.max_workers` higher (only safe for concurrency-safe
    graders).

    Stop semantics: when `should_stop()` becomes true, queued (not-yet-running)
    futures are cancelled. Already-running grades finish — same as the old
    serial loop, where a stop signal mid-attempt waited for that attempt.
    """
    finalized: list[Attempt] = []
    if not pending:
        return finalized

    scheduler = _resource_scheduler(config, max_workers=max_workers)
    if scheduler is not None:
        return _drain_pending_with_resource_scheduler(
            pending,
            config_path,
            coral_dir,
            config,
            scheduler=scheduler,
            liveness_file=liveness_file,
            should_stop=should_stop,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_safe_grade_one, attempt, config_path, coral_dir, config): attempt
            for attempt in pending
        }
        try:
            for fut in as_completed(futures):
                if liveness_file is not None:
                    try:
                        liveness_file.write_text(datetime.now(UTC).isoformat())
                    except OSError:
                        pass
                result = fut.result()
                if result is not None:
                    finalized.append(result)
                if should_stop():
                    for queued in futures:
                        queued.cancel()
                    break
        finally:
            # ThreadPoolExecutor.__exit__ waits for in-flight grades (their
            # subprocesses already own the work and will return on their own
            # timeout). We don't kill mid-grade workers from a stop signal.
            pass
    return finalized


def _drain_pending_with_resource_scheduler(
    pending: list[Attempt],
    config_path: Path,
    coral_dir: Path,
    config: CoralConfig,
    *,
    scheduler: _ResourceScheduler,
    liveness_file: Path | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[Attempt]:
    """Drain pending attempts while respecting evaluator resource capacity."""
    finalized: list[Attempt] = []
    queue = list(pending)
    running: dict[Any, tuple[Attempt, _ResourceLease]] = {}

    def _write_liveness() -> None:
        if liveness_file is not None:
            try:
                liveness_file.write_text(datetime.now(UTC).isoformat())
            except OSError:
                pass

    def _schedule_ready(pool: ThreadPoolExecutor) -> None:
        made_progress = True
        while queue and made_progress and not should_stop():
            made_progress = False
            for attempt in list(queue):
                lease = scheduler.try_acquire(
                    _scheduler_job_resources_for_attempt(coral_dir, attempt, config)
                )
                if lease is None:
                    continue
                if lease.oversubscribed:
                    logger.warning(
                        "Eval %s exceeds configured grader.parallel.resources; running it alone",
                        attempt.commit_hash[:12],
                    )
                future = pool.submit(
                    _safe_grade_one,
                    attempt,
                    config_path,
                    coral_dir,
                    config,
                    lease.resource,
                )
                running[future] = (attempt, lease)
                queue.remove(attempt)
                made_progress = True
                if not scheduler.can_start_more():
                    return

    with ThreadPoolExecutor(max_workers=scheduler.max_workers) as pool:
        _schedule_ready(pool)
        while running:
            done, _not_done = wait(running.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                _attempt, lease = running.pop(future)
                try:
                    result = future.result()
                    if result is not None:
                        finalized.append(result)
                finally:
                    scheduler.release(lease)
                    _write_liveness()
            if should_stop():
                for future in running:
                    future.cancel()
                break
            _schedule_ready(pool)
    return finalized


def process_pending_once(coral_dir: str | Path) -> list[Attempt]:
    """Drain all currently-pending attempts synchronously and return finalized records.

    Intended for tests and one-shot grading workflows where spawning a
    separate daemon process is overkill. Shares code with the main loop.
    """
    coral_dir = Path(coral_dir).resolve()
    config_path = coral_dir / "config.yaml"
    config = CoralConfig.from_yaml(config_path)
    return _drain_pending(
        _find_pending(coral_dir),
        config_path,
        coral_dir,
        config,
        max_workers=config.grader.parallel.max_workers,
    )


def _reload_daemon_config(config_path: Path, current: CoralConfig) -> CoralConfig:
    """Best-effort live config reload for user-facing evaluator knobs.

    The control panel can change eval profile, worker count, and resource
    budgets while the manager is running. Reloading before each drain lets the
    daemon apply those changes to future evals without restarting. If the file
    is momentarily unreadable or invalid, keep the prior config so in-flight
    runs are not killed by a transient write/read race.
    """
    try:
        return CoralConfig.from_yaml(config_path)
    except Exception as exc:  # noqa: BLE001 - daemon must survive transient config writes
        logger.warning("Failed to reload grader config from %s; keeping previous: %s", config_path, exc)
        return current


def run_daemon(coral_dir: str | Path, stop_event: Any = None) -> None:
    """Watch coral_dir/public/attempts/ and grade pending entries.

    Loops until `stop_event.is_set()` (multiprocessing.Event) or SIGTERM.
    The grader is (re)resolved per grade by `_run_grader`; the expensive bit
    (Docker init, dataset parsing, etc.) can amortize across evals if the
    grader exposes module-level caches.
    """
    coral_dir = Path(coral_dir).resolve()
    config_path = coral_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"No config.yaml at {config_path}")

    config = CoralConfig.from_yaml(config_path)
    max_workers = config.grader.parallel.max_workers

    logger.info(
        "Grader daemon started (coral_dir=%s, max_workers=%d)",
        coral_dir,
        max_workers,
    )
    started_at = datetime.now(UTC).isoformat()
    liveness_file = coral_dir / "public" / "grader_daemon_liveness"
    liveness_file.write_text(started_at)

    def _should_stop() -> bool:
        return bool(stop_event and stop_event.is_set())

    while not _should_stop():
        config = _reload_daemon_config(config_path, config)
        max_workers = config.grader.parallel.max_workers

        try:
            pending = _find_pending(coral_dir)
        except Exception:
            logger.exception("Failed to scan for pending attempts")
            pending = []

        if not pending:
            # Idle liveness update so supervisors can tell the daemon is alive.
            try:
                liveness_file.write_text(datetime.now(UTC).isoformat())
            except OSError:
                pass
            time.sleep(_POLL_INTERVAL_SEC)
            continue

        wave = _select_pending_wave(pending, config, max_workers=max_workers, coral_dir=coral_dir)
        if not wave:
            try:
                liveness_file.write_text(datetime.now(UTC).isoformat())
            except OSError:
                pass
            time.sleep(_POLL_INTERVAL_SEC)
            continue

        _drain_pending(
            wave,
            config_path,
            coral_dir,
            config,
            max_workers=max_workers,
            liveness_file=liveness_file,
            should_stop=_should_stop,
        )

    logger.info("Grader daemon stopped")
