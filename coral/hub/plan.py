"""Read the Codex-prepared agent plan for a run."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def build_agent_plan(coral_dir: str | Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a read-only preview of generated agent initialization bundles."""
    coral_dir = Path(coral_dir)
    config = config or {}
    planned_agents = _planned_agent_count(config)
    expected_agent_ids = [f"agent-{index + 1}" for index in range(planned_agents)]
    knowledge_dir = coral_dir / "public" / "knowledge"
    agents = _agent_entries(knowledge_dir)
    agents_by_id = {str(agent["agent_id"]): agent for agent in agents}
    complete_agent_ids = [
        agent_id
        for agent_id in expected_agent_ids
        if _entry_complete(agents_by_id.get(agent_id))
    ]
    missing_agent_ids = [
        agent_id
        for agent_id in expected_agent_ids
        if not _entry_complete(agents_by_id.get(agent_id))
    ]
    bundle_count = len(complete_agent_ids)

    missing_bundles = max(0, planned_agents - bundle_count)
    if missing_bundles == 0 and bundle_count >= planned_agents:
        status = "ready"
    elif agents:
        status = "partial"
    else:
        status = "missing"

    return {
        "status": status,
        "planned_agents": planned_agents,
        "brief_count": len(agents),
        "plan_count": len(agents),
        "bundle_count": bundle_count,
        "missing_briefs": missing_bundles,
        "missing_plans": missing_bundles,
        "missing_bundles": missing_bundles,
        "expected_agent_ids": expected_agent_ids,
        "complete_agent_ids": complete_agent_ids,
        "missing_agent_ids": missing_agent_ids,
        "agents": agents,
        "paths": {
            "agent_briefs": str(knowledge_dir / "briefs" / "agent-seeds"),
            "initialization_plans": str(knowledge_dir / "briefs" / "agent-seeds"),
        },
    }


def _has_plan_files(knowledge_dir: Path) -> bool:
    briefs = knowledge_dir / "briefs"
    return any(path.is_file() for path in (briefs / "agent-seeds").glob("*.md"))


def _agent_entries(knowledge_dir: Path) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    agent_dir = knowledge_dir / "briefs" / "agent-seeds"
    for path in sorted(agent_dir.glob("*.md")):
        if not path.is_file():
            continue
        entry = _agent_entry(path, knowledge_dir=knowledge_dir)
        agent_id = str(entry["agent_id"])
        if agent_id in seen:
            continue
        seen.add(agent_id)
        agents.append(entry)
    return agents


def _planned_agent_count(config: dict[str, Any]) -> int:
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    assignments = agents.get("assignments")
    if isinstance(assignments, list) and assignments:
        total = 0
        for assignment in assignments:
            if not isinstance(assignment, dict):
                continue
            try:
                total += max(1, int(assignment.get("count", 1)))
            except (TypeError, ValueError):
                total += 1
        return total or 1
    try:
        return max(1, int(agents.get("count", 1)))
    except (TypeError, ValueError):
        return 1


def _agent_entry(path: Path, *, knowledge_dir: Path) -> dict[str, Any]:
    text = _read_text(path)
    title = _first_heading(text) or _name_from_path(path)
    agent_id = _agent_id(path, title)
    eval_script = path.with_suffix(".eval.sh")
    eval_script_ready = eval_script.is_file() and bool(eval_script.stat().st_mode & 0o111)
    return {
        "agent_id": agent_id,
        "title": title,
        "summary": _summary(text),
        "relative_path": path.relative_to(knowledge_dir).as_posix(),
        "path": str(path),
        "eval_script_relative_path": eval_script.relative_to(knowledge_dir).as_posix(),
        "eval_script_path": str(eval_script),
        "eval_script_exists": eval_script.is_file(),
        "eval_script_executable": eval_script_ready,
        "bundle_complete": eval_script_ready,
    }


def _entry_complete(entry: dict[str, Any] | None) -> bool:
    return bool(entry and entry.get("bundle_complete"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _summary(text: str, *, max_chars: int = 360) -> str:
    lines = []
    in_frontmatter = False
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    joined = " ".join(lines)
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 1].rstrip() + "..."


def _agent_id(path: Path, title: str) -> str:
    stem = path.stem
    match = re.search(r"((?:\d+-)?agent-\d+)", stem, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"((?:\d+-)?agent-\d+)", title, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return stem


def _name_from_path(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ").title()
