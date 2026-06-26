"""Post-run review summary for the web dashboard."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coral.hub.knowledge import list_knowledge_sources
from coral.hub.notes import list_notes
from coral.types import (
    BUDGET_CLASS_GRADER_ERROR,
    BUDGET_CLASS_REAL,
    BUDGET_CLASS_TUNE,
    Attempt,
)


def build_review_summary(
    coral_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    run_state: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact evidence bundle for human/Codex review after a run."""
    coral_dir = Path(coral_dir)
    config = config or {}
    grader_cfg = config.get("grader") if isinstance(config.get("grader"), dict) else {}
    task_cfg = config.get("task") if isinstance(config.get("task"), dict) else {}
    direction = str(grader_cfg.get("direction") or "maximize")

    attempts = _read_review_attempts(coral_dir)
    attempts.sort(key=lambda attempt: attempt.timestamp)
    scored = [attempt for attempt in attempts if attempt.score is not None]
    real_scored = [
        attempt
        for attempt in scored
        if attempt.budget_class == BUDGET_CLASS_REAL and not _is_baseline(attempt)
    ]
    baselines = [attempt for attempt in attempts if _is_baseline(attempt)]
    pending = [attempt for attempt in attempts if attempt.status == "pending" and attempt.score is None]
    crashed = [attempt for attempt in attempts if attempt.status == "crashed"]
    timeout = [attempt for attempt in attempts if attempt.status == "timeout"]

    ranked = _rank_attempts(real_scored or scored, direction)
    best = ranked[0] if ranked else None
    baseline_ranked = _rank_attempts([a for a in baselines if a.score is not None], direction)
    best_baseline = baseline_ranked[0] if baseline_ranked else None
    improvement = _score_delta(best, best_baseline, direction)

    sources = list_knowledge_sources(coral_dir)
    notes = list_notes(coral_dir)
    eval_versions = _counter_values(attempts, "eval_version")
    eval_profiles = _counter_values(attempts, "eval_profile")
    source_status_counts = Counter(str(source.get("status") or "indexed") for source in sources)
    eval_spec = _eval_spec_summary(coral_dir, attempts)

    summary = {
        "task": {
            "name": task_cfg.get("name") or "",
            "eval_version": grader_cfg.get("eval_version") or "",
            "eval_profile": grader_cfg.get("profile") or "",
            "direction": direction,
        },
        "run_state": run_state or {},
        "attempts": {
            "total": len(attempts),
            "scored": len(scored),
            "real_scored": len(real_scored),
            "pending": len(pending),
            "crashed": len(crashed),
            "timeout": len(timeout),
            "tune": sum(1 for attempt in attempts if attempt.budget_class == BUDGET_CLASS_TUNE),
            "grader_error": sum(
                1 for attempt in attempts if attempt.budget_class == BUDGET_CLASS_GRADER_ERROR
            ),
            "baseline": len(baselines),
            "by_status": dict(Counter(attempt.status for attempt in attempts)),
            "by_agent": _attempts_by_agent(attempts, direction),
            "top": [_attempt_summary(attempt) for attempt in ranked[:5]],
            "recent": [_attempt_summary(attempt) for attempt in reversed(attempts[-5:])],
            "best": _attempt_summary(best) if best else None,
            "best_baseline": _attempt_summary(best_baseline) if best_baseline else None,
            "improvement_over_baseline": improvement,
            "eval_versions": dict(eval_versions),
            "eval_profiles": dict(eval_profiles),
        },
        "knowledge": {
            "sources": len(sources),
            "notes": len(notes),
            "proposed_sources": 0,
            "sources_by_category": dict(
                Counter(str(source.get("category") or "other") for source in sources)
            ),
            "sources_by_status": dict(source_status_counts),
            "notes_by_category": dict(Counter(str(note.get("category") or "other") for note in notes)),
            "recent_notes": [
                {
                    "title": note.get("title", ""),
                    "date": note.get("date", ""),
                    "category": note.get("category", "other"),
                    "relative_path": note.get("relative_path", ""),
                }
                for note in notes[-5:]
            ],
        },
        "eval_spec": eval_spec,
        "usage": usage or {},
        "readiness": readiness or {},
    }
    summary["flags"] = _review_flags(summary)
    summary["recommended_actions"] = _recommended_actions(summary)
    return summary


def _rank_attempts(attempts: list[Attempt], direction: str) -> list[Attempt]:
    descending = direction != "minimize"
    return sorted(
        [attempt for attempt in attempts if attempt.score is not None],
        key=lambda attempt: float(attempt.score or 0.0),
        reverse=descending,
    )


def _attempt_summary(attempt: Attempt) -> dict[str, Any]:
    metadata = attempt.metadata or {}
    components = metadata.get("score_components")
    return {
        "commit_hash": attempt.commit_hash,
        "agent_id": attempt.agent_id,
        "title": attempt.title,
        "score": attempt.score,
        "status": attempt.status,
        "timestamp": attempt.timestamp,
        "budget_class": attempt.budget_class,
        "is_baseline": _is_baseline(attempt),
        "eval_version": metadata.get("eval_version"),
        "eval_profile": metadata.get("eval_profile"),
        "score_components": components if isinstance(components, dict) else {},
    }


def _attempts_by_agent(attempts: list[Attempt], direction: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Attempt]] = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.agent_id].append(attempt)

    rows: list[dict[str, Any]] = []
    for agent_id, agent_attempts in sorted(grouped.items()):
        scored = [attempt for attempt in agent_attempts if attempt.score is not None]
        ranked = _rank_attempts(scored, direction)
        rows.append(
            {
                "agent_id": agent_id,
                "attempts": len(agent_attempts),
                "scored": len(scored),
                "pending": sum(
                    1
                    for attempt in agent_attempts
                    if attempt.status == "pending" and attempt.score is None
                ),
                "crashed": sum(1 for attempt in agent_attempts if attempt.status == "crashed"),
                "best": _attempt_summary(ranked[0]) if ranked else None,
            }
        )
    return rows


def _counter_values(attempts: list[Attempt], key: str) -> Counter[str]:
    values: Counter[str] = Counter()
    for attempt in attempts:
        value = (attempt.metadata or {}).get(key)
        if value:
            values[str(value)] += 1
    return values


def _eval_spec_summary(coral_dir: Path, attempts: list[Attempt]) -> dict[str, Any]:
    path = _find_eval_spec(coral_dir)
    default_path = coral_dir / "public" / "knowledge" / "eval_spec.md"
    if path is None:
        return {
            "exists": False,
            "path": str(default_path),
            "updated_at": None,
            "modified_after_attempts": False,
        }

    updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    latest_attempt_at = _latest_attempt_timestamp(attempts)
    modified_after_attempts = latest_attempt_at is not None and updated_at > latest_attempt_at
    return {
        "exists": True,
        "path": str(path),
        "updated_at": updated_at.isoformat(),
        "latest_attempt_at": latest_attempt_at.isoformat() if latest_attempt_at else None,
        "modified_after_attempts": modified_after_attempts,
    }


def _read_review_attempts(coral_dir: Path) -> list[Attempt]:
    """Read attempts for post-run review, including run-global baselines."""
    attempts: list[Attempt] = []
    public_attempts = coral_dir / "public" / "attempts"
    for path in sorted(public_attempts.glob("*.json")):
        try:
            attempts.append(Attempt.from_dict(json.loads(path.read_text())))
        except (OSError, json.JSONDecodeError, KeyError):
            continue

    seen: set[str] = set()
    deduped: list[Attempt] = []
    for attempt in attempts:
        if attempt.commit_hash in seen:
            continue
        seen.add(attempt.commit_hash)
        deduped.append(attempt)
    return deduped


def _find_eval_spec(coral_dir: Path) -> Path | None:
    path = coral_dir / "public" / "knowledge" / "eval_spec.md"
    return path if path.exists() else None


def _latest_attempt_timestamp(attempts: list[Attempt]) -> datetime | None:
    parsed: list[datetime] = []
    for attempt in attempts:
        try:
            parsed.append(datetime.fromisoformat(attempt.timestamp.replace("Z", "+00:00")))
        except ValueError:
            continue
    if not parsed:
        return None
    return max(value if value.tzinfo is not None else value.replace(tzinfo=UTC) for value in parsed)


def _is_baseline(attempt: Attempt) -> bool:
    metadata = attempt.metadata or {}
    return (
        metadata.get("baseline") is True
        or metadata.get("is_baseline") is True
        or metadata.get("reference") == "baseline"
        or metadata.get("kind") == "baseline"
    )


def _score_delta(
    best: Attempt | None,
    baseline: Attempt | None,
    direction: str,
) -> float | None:
    if best is None or baseline is None or best.score is None or baseline.score is None:
        return None
    delta = float(best.score) - float(baseline.score)
    return -delta if direction == "minimize" else delta


def _review_flags(summary: dict[str, Any]) -> list[dict[str, str]]:
    attempts = summary["attempts"]
    task = summary["task"]
    knowledge = summary["knowledge"]
    eval_spec = summary.get("eval_spec") if isinstance(summary.get("eval_spec"), dict) else {}
    flags: list[dict[str, str]] = []

    if attempts["scored"] == 0:
        flags.append(
            {
                "severity": "high",
                "label": "No scored attempts",
                "detail": "The run has not produced a graded result yet.",
            }
        )
    if attempts["pending"] > 0:
        flags.append(
            {
                "severity": "medium",
                "label": "Pending evals remain",
                "detail": f"{attempts['pending']} attempt(s) are still waiting for grading.",
            }
        )
    if attempts["crashed"] or attempts["timeout"] or attempts["grader_error"]:
        flags.append(
            {
                "severity": "medium",
                "label": "Failed evals need triage",
                "detail": (
                    f"{attempts['crashed']} crashed, {attempts['timeout']} timed out, "
                    f"{attempts['grader_error']} grader-error attempt(s)."
                ),
            }
        )
    if attempts["baseline"] == 0:
        flags.append(
            {
                "severity": "medium",
                "label": "No recorded baseline",
                "detail": "Scores are harder to interpret without a baseline attempt.",
            }
        )
    elif attempts["improvement_over_baseline"] is not None and attempts["improvement_over_baseline"] <= 0:
        flags.append(
            {
                "severity": "medium",
                "label": "No baseline improvement",
                "detail": "The best non-baseline result does not beat the recorded baseline.",
            }
        )
    if not task["eval_version"] or not task["eval_profile"]:
        flags.append(
            {
                "severity": "high",
                "label": "Eval identity incomplete",
                "detail": "grader.eval_version and grader.profile should be recorded before comparing runs.",
            }
        )
    if len(attempts["eval_versions"]) > 1 or len(attempts["eval_profiles"]) > 1:
        flags.append(
            {
                "severity": "medium",
                "label": "Mixed eval identity",
                "detail": "This run contains attempts from multiple eval versions or profiles.",
            }
        )
    if eval_spec.get("modified_after_attempts"):
        flags.append(
            {
                "severity": "medium",
                "label": "Eval spec changed after scoring",
                "detail": (
                    "The eval trust spec was edited after attempts were produced; bump eval_version "
                    "or re-run comparisons under one frozen eval before making claims."
                ),
            }
        )
    if knowledge["sources"] == 0:
        flags.append(
            {
                "severity": "low",
                "label": "No knowledge sources",
                "detail": "No papers, repos, docs, or proposed references are indexed for this run.",
            }
        )
    return flags


def _recommended_actions(summary: dict[str, Any]) -> list[str]:
    attempts = summary["attempts"]
    eval_spec = summary.get("eval_spec") if isinstance(summary.get("eval_spec"), dict) else {}
    actions: list[str] = []
    if attempts["best"]:
        actions.append("Inspect the best attempt and decide which idea should be preserved.")
    if attempts["crashed"] or attempts["timeout"] or attempts["grader_error"]:
        actions.append("Separate agent-code failures from grader/environment failures before trusting scores.")
    if attempts["improvement_over_baseline"] is not None:
        actions.append("Record whether the best result beats the baseline under the frozen eval profile.")
    if eval_spec.get("modified_after_attempts"):
        actions.append("Start a new timestamp with a bumped eval_version or re-run selected attempts under the revised eval spec.")
    if not actions:
        actions.append("Capture a short review note before starting the next timestamp.")
    return actions
