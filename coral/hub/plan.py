"""Read the Codex-prepared agent/island plan for a run."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from coral.hub._island import all_view_roots


def build_agent_plan(coral_dir: str | Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a read-only preview of generated island themes and agent briefs."""
    coral_dir = Path(coral_dir)
    config = config or {}
    planned_agents = _planned_agent_count(config)
    island_count = _planned_island_count(config)
    roots = _plan_knowledge_roots(coral_dir)
    agents = _agent_entries(roots)
    themes = _island_themes(roots)

    missing_briefs = max(0, planned_agents - len(agents))
    if missing_briefs == 0 and len(agents) >= planned_agents:
        status = "ready"
    elif agents:
        status = "partial"
    else:
        status = "missing"

    return {
        "status": status,
        "planned_agents": planned_agents,
        "brief_count": len(agents),
        "missing_briefs": missing_briefs,
        "island_count": island_count,
        "islands": _group_by_island(agents, themes, island_count),
        "agents": agents,
        "paths": {
            "agent_briefs": str(roots[0][1] / "briefs" / "agent-seeds") if roots else "",
            "island_themes": str(roots[0][1] / "briefs" / "islands") if roots else "",
        },
    }


def _plan_knowledge_roots(coral_dir: Path) -> list[tuple[str | None, Path]]:
    """Return knowledge roots that may hold a Codex-prepared agent plan.

    Prefer public/global knowledge when it contains a plan. After a multi-island
    start, CORAL may have copied the frozen knowledge snapshot into
    `islands/<id>/knowledge`; in that case read each island root and filter by
    island id to avoid duplicate all-agent snapshots.
    """
    public = coral_dir / "public" / "knowledge"
    if _has_plan_files(public):
        return [(None, public)]

    roots: list[tuple[str | None, Path]] = []
    for view_root in all_view_roots(coral_dir):
        island_id = view_root.name if view_root.parent.name == "islands" else None
        knowledge_dir = view_root / "knowledge"
        if knowledge_dir.is_dir():
            roots.append((island_id, knowledge_dir))
    if not roots:
        roots.append((None, public))
    return roots


def _has_plan_files(knowledge_dir: Path) -> bool:
    briefs = knowledge_dir / "briefs"
    return any(
        path.is_file()
        for directory in (
            briefs / "agent-seeds",
            briefs / "islands",
            briefs / "island-themes",
        )
        for path in directory.glob("*.md")
    )


def _agent_entries(roots: list[tuple[str | None, Path]]) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root_island_id, knowledge_dir in roots:
        agent_dir = knowledge_dir / "briefs" / "agent-seeds"
        for path in sorted(agent_dir.glob("*.md")):
            if not path.is_file():
                continue
            entry = _agent_entry(path, knowledge_dir=knowledge_dir)
            entry_island_id = entry.get("island_id")
            if root_island_id is not None and entry_island_id not in {None, root_island_id}:
                continue
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


def _planned_island_count(config: dict[str, Any]) -> int:
    islands = config.get("islands") if isinstance(config.get("islands"), dict) else {}
    try:
        return max(1, int(islands.get("count", 1)))
    except (TypeError, ValueError):
        return 1


def _agent_entry(path: Path, *, knowledge_dir: Path) -> dict[str, Any]:
    text = _read_text(path)
    title = _first_heading(text) or _name_from_path(path)
    agent_id = _agent_id(path, title)
    return {
        "agent_id": agent_id,
        "island_id": _island_id(agent_id, path.name, text),
        "title": title,
        "summary": _summary(text),
        "relative_path": path.relative_to(knowledge_dir).as_posix(),
        "path": str(path),
    }


def _island_themes(roots: list[tuple[str | None, Path]]) -> dict[str, dict[str, Any]]:
    themes: dict[str, dict[str, Any]] = {}
    for root_island_id, knowledge_dir in roots:
        briefs_dir = knowledge_dir / "briefs"
        for directory_name in ("islands", "island-themes"):
            directory = briefs_dir / directory_name
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                if not path.is_file():
                    continue
                text = _read_text(path)
                island_id = _island_id_from_value(path.stem) or path.stem
                if root_island_id is not None and island_id != root_island_id:
                    continue
                themes[island_id] = {
                    "island_id": island_id,
                    "title": _first_heading(text) or path.stem.replace("-", " ").title(),
                    "summary": _summary(text),
                    "relative_path": path.relative_to(knowledge_dir).as_posix(),
                    "path": str(path),
                }
    return themes


def _group_by_island(
    agents: list[dict[str, Any]],
    themes: dict[str, dict[str, Any]],
    island_count: int,
) -> list[dict[str, Any]]:
    island_ids = {str(index) for index in range(island_count)}
    island_ids.update(str(agent["island_id"]) for agent in agents if agent.get("island_id") is not None)
    island_ids.update(themes)
    grouped: list[dict[str, Any]] = []
    for island_id in sorted(island_ids, key=_island_sort_key):
        grouped.append(
            {
                "island_id": island_id,
                "theme": themes.get(island_id),
                "agents": [agent for agent in agents if str(agent.get("island_id")) == island_id],
            }
        )
    no_island = [agent for agent in agents if agent.get("island_id") is None]
    if no_island:
        grouped.append({"island_id": None, "theme": None, "agents": no_island})
    return grouped


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


def _island_id(agent_id: str, filename: str, text: str) -> str | None:
    for value in (agent_id, filename):
        island_id = _island_id_from_value(value)
        if island_id is not None:
            return island_id
    match = re.search(r"^\s*island_id\s*:\s*([A-Za-z0-9_.-]+)\s*$", text, flags=re.MULTILINE)
    if match:
        return match.group(1)
    match = re.search(r"^\s*island\s*:\s*([A-Za-z0-9_.-]+)\s*$", text, flags=re.MULTILINE)
    if match:
        return match.group(1)
    return None


def _island_id_from_value(value: str) -> str | None:
    match = re.match(r"^(\d+)[-_]", value)
    return match.group(1) if match else None


def _name_from_path(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ").title()


def _island_sort_key(value: str) -> tuple[int, str]:
    return (0, f"{int(value):08d}") if value.isdigit() else (1, value)
