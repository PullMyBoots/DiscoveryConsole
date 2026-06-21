"""Build startup-readiness checks for a prepared CORAL timestamp."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from coral.config import CoralConfig
from coral.hub._island import all_view_roots


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
    island_count = _planned_island_count(config)
    topology_status, topology_detail = control_topology_status(config)
    checks.append(
        {
            "id": "topology",
            "label": "Topology",
            "status": topology_status,
            "detail": topology_detail,
            "count": island_count,
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
            eval_status = "warning"
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

    from coral.hub.knowledge import list_knowledge_sources

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

    manifest_paths = [knowledge_dir / "manifest.jsonl" for knowledge_dir in knowledge_dirs]
    manifest = next((path for path in manifest_paths if path.exists()), manifest_paths[0])
    sources = list_knowledge_sources(coral_dir)
    if not any(path.exists() for path in manifest_paths):
        knowledge_status = "missing"
        knowledge_detail = "knowledge/manifest.jsonl is missing"
    elif sources:
        knowledge_status = "ready"
        knowledge_detail = f"{len(sources)} source(s) indexed"
    else:
        knowledge_status = "warning"
        knowledge_detail = "knowledge index exists but has no sources"
    checks.append(
        {
            "id": "knowledge",
            "label": "Knowledge",
            "status": knowledge_status,
            "detail": knowledge_detail,
            "count": len(sources),
            "path": str(manifest),
        }
    )

    attempts = _read_readiness_attempt_dicts(coral_dir)
    baselines = [attempt for attempt in attempts if _is_baseline_attempt_dict(attempt)]
    if baselines:
        baseline_status = "ready"
        scored = [attempt for attempt in baselines if attempt.get("score") is not None]
        baseline_detail = f"{len(scored)}/{len(baselines)} scored baseline attempt(s)"
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
    brief_count = int(agent_plan.get("brief_count") or 0)
    if brief_count >= planned:
        brief_status = "ready"
    elif brief_count:
        brief_status = "warning"
    else:
        brief_status = "missing"
    checks.append(
        {
            "id": "agent_briefs",
            "label": "Agent Briefs",
            "status": brief_status,
            "detail": f"{brief_count}/{planned} agent seed brief(s)",
            "count": brief_count,
            "path": str((agent_plan.get("paths") or {}).get("agent_briefs", "")),
        }
    )

    unique_theme_ids = {
        str(island.get("island_id"))
        for island in agent_plan.get("islands", [])
        if island.get("island_id") is not None and island.get("theme")
    }
    if island_count > 1:
        island_agent_status, island_agent_detail, covered_islands = _island_agent_coverage(
            agent_plan,
            island_count,
        )
        checks.append(
            {
                "id": "island_agents",
                "label": "Island Agents",
                "status": island_agent_status,
                "detail": island_agent_detail,
                "count": covered_islands,
            }
        )
        if len(unique_theme_ids) >= island_count:
            theme_status = "ready"
        elif unique_theme_ids:
            theme_status = "warning"
        else:
            theme_status = "missing"
        checks.append(
            {
                "id": "island_themes",
                "label": "Island Themes",
                "status": theme_status,
                "detail": f"{len(unique_theme_ids)}/{island_count} island theme brief(s)",
                "count": len(unique_theme_ids),
                "path": str((agent_plan.get("paths") or {}).get("island_themes", "")),
            }
        )

    overall = "ready"
    if any(check["status"] == "missing" for check in checks):
        overall = "missing"
    elif any(check["status"] == "warning" for check in checks):
        overall = "warning"
    return {"status": overall, "checks": checks}


def control_topology_status(config: dict[str, Any]) -> tuple[str, str]:
    """Return readiness status for agent/island topology."""
    planned = _planned_agent_count(config)
    island_count = _planned_island_count(config)
    if island_count > planned:
        return (
            "missing",
            f"islands.count ({island_count}) cannot exceed planned agents ({planned})",
        )
    if island_count > 1:
        return "ready", f"{planned} planned agent(s) across {island_count} island(s)"
    return "ready", f"{planned} planned agent(s) in single-island mode"


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


def _planned_island_count(config: dict[str, Any]) -> int:
    islands = config.get("islands") if isinstance(config.get("islands"), dict) else {}
    try:
        return max(1, int(islands.get("count", 1)))
    except (TypeError, ValueError):
        return 1


def _island_agent_coverage(agent_plan: dict[str, Any], island_count: int) -> tuple[str, str, int]:
    """Verify every configured island has at least one Codex-prepared agent route."""
    expected = {str(index) for index in range(island_count)}
    covered: set[str] = set()
    unexpected: set[str] = set()
    for island in agent_plan.get("islands", []):
        if not isinstance(island, dict):
            continue
        island_id = island.get("island_id")
        agents = island.get("agents")
        if island_id is None or not isinstance(agents, list) or not agents:
            continue
        normalized = str(island_id)
        if normalized in expected:
            covered.add(normalized)
        else:
            unexpected.add(normalized)

    missing = sorted(expected - covered, key=_island_sort_key)
    unexpected_sorted = sorted(unexpected, key=_island_sort_key)
    if missing or unexpected_sorted:
        details = []
        if missing:
            details.append(f"missing agent brief(s) for island(s): {', '.join(missing)}")
        if unexpected_sorted:
            details.append(f"unknown island id(s) in agent brief(s): {', '.join(unexpected_sorted)}")
        return "missing", "; ".join(details), len(covered)
    return "ready", f"{len(covered)}/{island_count} island(s) have agent brief(s)", len(covered)


def _island_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _knowledge_dirs(coral_dir: Path) -> list[Path]:
    public = coral_dir / "public" / "knowledge"
    dirs: list[Path] = []
    if public.exists():
        dirs.append(public)
    for view_root in all_view_roots(coral_dir):
        knowledge_dir = view_root / "knowledge"
        if knowledge_dir not in dirs and knowledge_dir.exists():
            dirs.append(knowledge_dir)
    return dirs or [public]


def _eval_spec_status(knowledge_dirs: list[Path]) -> tuple[str, str, Path | None, int]:
    """Return readiness status for the Codex-authored eval design spec."""
    best_warning: tuple[str, str, Path | None, int] | None = None
    for knowledge_dir in knowledge_dirs:
        result = _eval_spec_status_for_dir(knowledge_dir)
        if result[0] == "ready":
            return result
        if result[0] == "warning" and best_warning is None:
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

    normalized = text.lower()
    sections = {
        "breakthrough": ("breakthrough", "improve", "提升", "突破"),
        "guardrail": ("guardrail", "safety", "保底", "兜底", "底线"),
        "anti_cheating": (
            "anti-cheat",
            "anti cheating",
            "cheat",
            "overfit",
            "leakage",
            "作弊",
            "过拟合",
            "泄漏",
        ),
    }
    matched = sum(
        1 for keywords in sections.values() if any(keyword in normalized for keyword in keywords)
    )
    if matched == len(sections):
        return (
            "ready",
            "eval spec covers breakthrough, guardrail, and anti-cheating checks",
            spec_path,
            matched,
        )
    return (
        "warning",
        f"eval spec found but only covers {matched}/{len(sections)} required section(s)",
        spec_path,
        matched,
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


def _read_readiness_attempt_dicts(coral_dir: Path) -> list[dict[str, Any]]:
    """Read attempts relevant to launch readiness.

    In multi-island runs normal agent attempts live under `islands/<id>/attempts`,
    while Codex-prepared reference records such as seed baselines are run-global
    and may live under `public/attempts`. Read both scopes so readiness matches
    the workbench contract.
    """
    import json

    from coral.hub.attempts import _read_all_island_attempts
    from coral.types import Attempt

    attempts: list[Attempt] = []
    public_attempts = coral_dir / "public" / "attempts"
    for path in sorted(public_attempts.glob("*.json")):
        try:
            attempts.append(Attempt.from_dict(json.loads(path.read_text())))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    attempts.extend(_read_all_island_attempts(coral_dir))
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
