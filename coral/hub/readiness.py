"""Build startup-readiness checks for a prepared CORAL timestamp."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from coral.cli.validation import validate_eval_spec_text
from coral.config import CoralConfig


def build_control_readiness(coral_dir: str | Path) -> dict[str, Any]:
    """Summarize whether a timestamp is ready for user-launched execution."""
    coral_dir = Path(coral_dir)
    config_path = coral_dir / "config.yaml"
    config = _load_yaml(config_path)
    checks: list[dict[str, Any]] = []

    try:
        CoralConfig.from_dict(config)
        config_ready = True
        config_detail = "config.yaml is valid"
    except Exception as exc:
        config_ready = False
        config_detail = f"config.yaml is invalid: {exc}"
    checks.append(
        {
            "id": "config",
            "label": "Config",
            "status": "ready" if config_ready else "missing",
            "detail": config_detail,
            "path": str(config_path),
        }
    )

    planned = _planned_agent_count(config)
    topology_status, topology_detail = control_topology_status(config)
    checks.append(
        {
            "id": "topology",
            "label": "Topology",
            "status": topology_status,
            "detail": topology_detail,
            "count": planned,
        }
    )

    grader = config.get("grader") or {}
    entrypoint = str(grader.get("entrypoint") or "").strip()
    checks.append(
        {
            "id": "grader",
            "label": "Grader",
            "status": "ready" if entrypoint else "missing",
            "detail": entrypoint or "grader.entrypoint is missing",
        }
    )

    eval_version = str(grader.get("eval_version") or "").strip()
    profile = str(grader.get("profile") or "").strip()
    profiles = grader.get("profiles") if isinstance(grader.get("profiles"), dict) else {}
    if eval_version and profile:
        eval_status = "ready"
        if profiles and profile not in profiles:
            eval_status = "missing"
            eval_detail = f"{eval_version} / {profile} is not defined in grader.profiles"
        else:
            eval_detail = f"{eval_version} / {profile}"
    else:
        eval_status = "missing"
        eval_detail = "grader.eval_version or grader.profile is missing"
    checks.append(
        {
            "id": "eval",
            "label": "Eval Profile",
            "status": eval_status,
            "detail": eval_detail,
        }
    )

    from coral.hub.kb import index_external

    knowledge_dirs = _knowledge_dirs(coral_dir)
    eval_spec_status, eval_spec_detail, eval_spec_path, eval_spec_sections = _eval_spec_status(
        knowledge_dirs
    )
    checks.append(
        {
            "id": "eval_spec",
            "label": "Eval Spec",
            "status": eval_spec_status,
            "detail": eval_spec_detail,
            "count": eval_spec_sections,
            "path": str(eval_spec_path) if eval_spec_path else str(knowledge_dirs[0] / "eval_spec.md"),
        }
    )

    index_paths = [knowledge_dir / "external" / "index.jsonl" for knowledge_dir in knowledge_dirs]
    index_path = next((path for path in index_paths if path.exists()), index_paths[0])
    sources = index_external(coral_dir)
    knowledge_errors = _knowledge_source_errors(knowledge_dirs, sources)
    if not any(path.exists() for path in index_paths):
        knowledge_status = "missing"
        knowledge_detail = "knowledge/external/index.jsonl is missing"
    elif knowledge_errors:
        knowledge_status = "missing"
        preview = "; ".join(knowledge_errors[:3])
        suffix = f" (+{len(knowledge_errors) - 3} more)" if len(knowledge_errors) > 3 else ""
        knowledge_detail = f"knowledge index has invalid source(s): {preview}{suffix}"
    elif sources:
        knowledge_status = "ready"
        knowledge_detail = f"{len(sources)} source(s) indexed"
    else:
        knowledge_status = "missing"
        knowledge_detail = "knowledge index exists but has no sources"
    checks.append(
        {
            "id": "knowledge",
            "label": "Knowledge",
            "status": knowledge_status,
            "detail": knowledge_detail,
            "count": len(sources),
            "path": str(index_path),
        }
    )

    attempts = _read_readiness_attempt_dicts(coral_dir)
    baselines = [attempt for attempt in attempts if _is_baseline_attempt_dict(attempt)]
    valid_baselines = [
        attempt
        for attempt in baselines
        if _baseline_attempt_has_score(attempt)
        and _baseline_attempt_matches_eval(attempt, eval_version=eval_version, profile=profile)
    ]
    if valid_baselines:
        baseline_status = "ready"
        baseline_detail = f"{len(valid_baselines)}/{len(baselines)} scored baseline attempt(s) match eval identity"
    elif baselines:
        baseline_status = "missing"
        scored = sum(1 for attempt in baselines if _baseline_attempt_has_score(attempt))
        baseline_detail = (
            f"{scored}/{len(baselines)} baseline attempt(s) have numeric scores and "
            f"0 match {eval_version or 'missing eval_version'} / {profile or 'missing profile'}"
        )
    else:
        baseline_status = "missing"
        baseline_detail = "No attempt marked with metadata.baseline/reference baseline"
    checks.append(
        {
            "id": "baseline",
            "label": "Baseline",
            "status": baseline_status,
            "detail": baseline_detail,
            "count": len(baselines),
        }
    )

    from coral.hub.plan import build_agent_plan

    agent_plan = build_agent_plan(coral_dir, config=config)
    bundle_count = int(agent_plan.get("bundle_count") or 0)
    missing_agent_ids = [str(item) for item in agent_plan.get("missing_agent_ids") or []]
    if bundle_count >= planned:
        brief_status = "ready"
    elif bundle_count:
        brief_status = "missing"
    else:
        brief_status = "missing"
    bundle_detail = (
        f"{bundle_count}/{planned} initialization bundle(s) with plan and executable eval script"
    )
    if missing_agent_ids:
        bundle_detail += f"; missing: {', '.join(missing_agent_ids)}"
    checks.append(
        {
            "id": "agent_briefs",
            "label": "Agent Initialization Bundles",
            "status": brief_status,
            "detail": bundle_detail,
            "count": bundle_count,
            "path": str((agent_plan.get("paths") or {}).get("agent_briefs", "")),
        }
    )

    overall = "ready"
    if any(check["status"] == "missing" for check in checks):
        overall = "missing"
    elif any(check["status"] == "warning" for check in checks):
        overall = "warning"
    return {"status": overall, "checks": checks}


def control_topology_status(config: dict[str, Any]) -> tuple[str, str]:
    """Return readiness status for the planned agent topology."""
    planned = _planned_agent_count(config)
    return "ready", f"{planned} planned agent(s)"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _planned_agent_count(config: dict[str, Any]) -> int:
    agents = config.get("agents") or {}
    assignments = agents.get("assignments")
    if isinstance(assignments, list) and assignments:
        total = 0
        for assignment in assignments:
            if isinstance(assignment, dict):
                try:
                    total += max(1, int(assignment.get("count", 1)))
                except (TypeError, ValueError):
                    total += 1
        return total or 1
    try:
        return max(1, int(agents.get("count", 1)))
    except (TypeError, ValueError):
        return 1


def _knowledge_dirs(coral_dir: Path) -> list[Path]:
    public = coral_dir / "public" / "knowledge"
    return [public]


def _knowledge_source_errors(
    knowledge_dirs: list[Path],
    sources: list[dict[str, Any]],
) -> list[str]:
    """Return hard launch errors for external-index knowledge sources."""
    errors: list[str] = []
    roots = [path.resolve() for path in knowledge_dirs if path.exists()]
    for source in sources:
        title = str(source.get("title") or source.get("id") or "external source")
        rel = str(source.get("item_path") or "").strip()
        if not rel:
            errors.append(f"{title} has no item_path")
            continue
        if rel == "external/index.jsonl":
            errors.append(f"{title} points at external/index.jsonl")
            continue
        source_md = f"{rel.rstrip('/')}/source.md"
        if not _relative_source_exists(roots, source_md):
            errors.append(f"{title} missing file {source_md}")
    return errors


def _relative_source_exists(roots: list[Path], relative_path: str) -> bool:
    for root in roots:
        candidate = (root / relative_path).resolve()
        try:
            inside_root = candidate.is_relative_to(root)
        except ValueError:
            inside_root = False
        if inside_root and candidate.is_file():
            return True
    return False


def _eval_spec_status(knowledge_dirs: list[Path]) -> tuple[str, str, Path | None, int]:
    """Return readiness status for the Codex-authored eval design spec."""
    best_warning: tuple[str, str, Path | None, int] | None = None
    for knowledge_dir in knowledge_dirs:
        result = _eval_spec_status_for_dir(knowledge_dir)
        if result[0] == "ready":
            return result
        if result[0] == "missing" and best_warning is None:
            best_warning = result
    if best_warning is not None:
        return best_warning
    return "missing", "knowledge/eval_spec.md is missing", None, 0


def _eval_spec_status_for_dir(knowledge_dir: Path) -> tuple[str, str, Path | None, int]:
    candidates = [
        knowledge_dir / "eval_spec.md",
        knowledge_dir / "eval-spec.md",
        knowledge_dir / "briefs" / "eval-spec.md",
    ]
    spec_path = next((path for path in candidates if path.exists()), None)
    if spec_path is None:
        return "missing", "knowledge/eval_spec.md is missing", None, 0
    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "missing", f"{spec_path.name} cannot be read", spec_path, 0

    missing = validate_eval_spec_text(text)
    total = 10
    present = max(0, total - len(missing))
    if not missing:
        return (
            "ready",
            "eval spec covers required sections, metrics, guardrails, and anti-cheating checks",
            spec_path,
            present,
        )
    return (
        "missing",
        "eval spec is missing required contract item(s): " + ", ".join(missing[:4]),
        spec_path,
        present,
    )


def _is_baseline_attempt_dict(attempt: dict[str, Any]) -> bool:
    metadata = attempt.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    return (
        metadata.get("baseline") is True
        or metadata.get("is_baseline") is True
        or metadata.get("reference") == "baseline"
        or metadata.get("kind") == "baseline"
    )


def _baseline_attempt_has_score(attempt: dict[str, Any]) -> bool:
    score = attempt.get("score")
    return isinstance(score, (int, float)) and not isinstance(score, bool)


def _baseline_attempt_matches_eval(
    attempt: dict[str, Any],
    *,
    eval_version: str,
    profile: str,
) -> bool:
    metadata = attempt.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    return (
        str(metadata.get("eval_version") or "") == eval_version
        and str(metadata.get("eval_profile") or metadata.get("profile") or "") == profile
    )


def _read_readiness_attempt_dicts(coral_dir: Path) -> list[dict[str, Any]]:
    """Read attempts relevant to launch readiness."""
    import json

    from coral.types import Attempt

    attempts: list[Attempt] = []
    public_attempts = coral_dir / "public" / "attempts"
    for path in sorted(public_attempts.glob("*.json")):
        try:
            attempts.append(Attempt.from_dict(json.loads(path.read_text())))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for attempt in attempts:
        data = attempt.to_dict()
        key = str(data.get("commit_hash") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(data)
    return result
