#!/usr/bin/env python3
"""Write CORAL agent seed briefs and island theme briefs.

Use a JSON plan when Codex has generated concrete directions:

{
  "islands": [
    {"id": "0", "title": "Sparse Search", "brief": "Explore sparse variants."}
  ],
  "agents": [
    {
      "id": "0-agent-1",
      "island_id": "0",
      "title": "Sparse baseline optimizer",
      "brief": "Start from the baseline and tune sparse parameters.",
      "focus": ["fast iteration", "simple ablations"],
      "starting_steps": ["Run quick eval", "Change one variable at a time"],
      "avoid": ["changing eval files"]
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_DIRECTIONS = (
    "Baseline reproduction and conservative improvement.",
    "Alternative algorithmic route with a distinct technical assumption.",
    "Robustness-first route focused on guardrails and failure cases.",
    "Efficiency-first route focused on cost, latency, or resource use.",
    "Ablation and synthesis route that combines promising partial results.",
)


def write_plan(
    knowledge_dir: Path,
    *,
    agents: int = 1,
    islands: int = 1,
    plan_path: Path | None = None,
    force: bool = False,
) -> None:
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    briefs_dir = knowledge_dir / "briefs"
    agent_dir = briefs_dir / "agent-seeds"
    island_dir = briefs_dir / "islands"
    agent_dir.mkdir(parents=True, exist_ok=True)
    island_dir.mkdir(parents=True, exist_ok=True)

    plan = _read_plan(plan_path) if plan_path else _default_plan(agents=agents, islands=islands)
    _validate_topology(plan)
    for island in plan["islands"]:
        path = island_dir / f"{_safe_id(island['id'])}.md"
        _write_if_allowed(path, _render_island(island), force=force)
    for agent in plan["agents"]:
        path = agent_dir / f"{_safe_id(agent['id'])}.md"
        _write_if_allowed(path, _render_agent(agent), force=force)


def _read_plan(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("plan JSON must be an object")
    islands = data.get("islands") or []
    agents = data.get("agents") or []
    if not isinstance(islands, list) or not isinstance(agents, list):
        raise SystemExit("plan JSON must contain list fields: islands, agents")
    normalized_islands = [_normalize_island(item, index) for index, item in enumerate(islands)]
    normalized_agents = [_normalize_agent(item, index) for index, item in enumerate(agents)]
    if not normalized_agents:
        raise SystemExit("plan JSON must contain at least one agent")
    if not normalized_islands:
        island_ids = sorted(
            {str(agent.get("island_id")) for agent in normalized_agents if agent.get("island_id")}
        )
        normalized_islands = [
            _normalize_island({"id": island_id, "title": f"Island {island_id}"}, index)
            for index, island_id in enumerate(island_ids or ["0"])
        ]
    return {"islands": normalized_islands, "agents": normalized_agents}


def _validate_topology(plan: dict[str, list[dict[str, Any]]]) -> None:
    island_ids = {str(island["id"]) for island in plan["islands"]}
    if len(island_ids) > len(plan["agents"]):
        raise SystemExit(
            f"island count ({len(island_ids)}) cannot exceed agent count ({len(plan['agents'])})"
        )
    agent_island_ids = {
        str(agent["island_id"]) for agent in plan["agents"] if agent.get("island_id") is not None
    }
    unknown = sorted(agent_island_ids - island_ids)
    if unknown:
        raise SystemExit(f"agent plan references unknown island id(s): {', '.join(unknown)}")
    if len(island_ids) > 1:
        covered = agent_island_ids
        empty = sorted(island_ids - covered)
        if empty:
            raise SystemExit(f"island(s) without any assigned agent: {', '.join(empty)}")


def _default_plan(*, agents: int, islands: int) -> dict[str, list[dict[str, Any]]]:
    agent_count = max(1, agents)
    island_count = max(1, islands)
    island_items = [
        _normalize_island(
            {
                "id": str(index),
                "title": f"Island {index}",
                "brief": DEFAULT_DIRECTIONS[index % len(DEFAULT_DIRECTIONS)],
            },
            index,
        )
        for index in range(island_count)
    ]
    agent_items = []
    for index in range(agent_count):
        island_id = str(index % island_count) if island_count > 1 else None
        agent_id = f"{island_id}-agent-{index + 1}" if island_id is not None else f"agent-{index + 1}"
        agent_items.append(
            _normalize_agent(
                {
                    "id": agent_id,
                    "island_id": island_id,
                    "title": f"Agent {index + 1}",
                    "brief": DEFAULT_DIRECTIONS[index % len(DEFAULT_DIRECTIONS)],
                    "starting_steps": [
                        "Read CORAL.md, knowledge/eval_spec.md, and relevant sources.",
                        "Run the quick eval before making broad changes.",
                        "Submit one clear attempt and record what changed.",
                    ],
                    "avoid": [
                        "Editing grader or hidden eval files unless explicitly instructed.",
                        "Optimizing only the breakthrough metric while breaking guardrails.",
                    ],
                },
                index,
            )
        )
    return {"islands": island_items, "agents": agent_items}


def _normalize_island(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = {}
    island_id = str(item.get("id") or item.get("island_id") or index)
    title = str(item.get("title") or f"Island {island_id}")
    brief = str(item.get("brief") or item.get("summary") or "")
    return {"id": island_id, "title": title, "brief": brief}


def _normalize_agent(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = {}
    island_id = item.get("island_id")
    if island_id is not None:
        island_id = str(island_id)
    agent_id = str(item.get("id") or item.get("agent_id") or f"agent-{index + 1}")
    title = str(item.get("title") or agent_id)
    brief = str(item.get("brief") or item.get("summary") or "")
    return {
        "id": agent_id,
        "island_id": island_id,
        "title": title,
        "brief": brief,
        "focus": _string_list(item.get("focus")),
        "starting_steps": _string_list(item.get("starting_steps") or item.get("steps")),
        "avoid": _string_list(item.get("avoid") or item.get("risks")),
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _render_island(island: dict[str, Any]) -> str:
    return (
        f"# {island['title']}\n\n"
        f"island_id: {island['id']}\n\n"
        "## Technical Theme\n"
        f"{island['brief'] or 'Define the island-specific technical route.'}\n"
    )


def _render_agent(agent: dict[str, Any]) -> str:
    lines = [f"# {agent['title']}", ""]
    if agent.get("island_id") is not None:
        lines.extend([f"island_id: {agent['island_id']}", ""])
    lines.extend(["## Initial Direction", agent["brief"] or "Define this agent's technical route.", ""])
    _append_list(lines, "Focus", agent.get("focus") or [])
    _append_list(lines, "Starting Steps", agent.get("starting_steps") or [])
    _append_list(lines, "Avoid", agent.get("avoid") or [])
    return "\n".join(lines).rstrip() + "\n"


def _append_list(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.extend([f"## {title}"])
    lines.extend(f"- {item}" for item in items)
    lines.append("")


def _write_if_allowed(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.write_text(content, encoding="utf-8")


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip()).strip("-")
    return safe or "item"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("knowledge_dir", type=Path, help="knowledge directory to update")
    parser.add_argument("--plan", type=Path, help="JSON plan generated by Codex")
    parser.add_argument("--agents", type=int, default=1, help="agent count for placeholder plan")
    parser.add_argument("--islands", type=int, default=1, help="island count for placeholder plan")
    parser.add_argument("--force", action="store_true", help="overwrite existing brief files")
    args = parser.parse_args()
    write_plan(
        args.knowledge_dir,
        agents=args.agents,
        islands=args.islands,
        plan_path=args.plan,
        force=args.force,
    )


if __name__ == "__main__":
    main()
