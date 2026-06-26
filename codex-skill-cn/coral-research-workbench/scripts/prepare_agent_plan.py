#!/usr/bin/env python3
"""Write runnable CORAL agent initialization plans and eval scripts."""

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
    plan_path: Path | None = None,
    force: bool = False,
) -> None:
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    briefs_dir = knowledge_dir / "briefs"
    agent_dir = briefs_dir / "agent-seeds"
    agent_dir.mkdir(parents=True, exist_ok=True)

    plan = _read_plan(plan_path) if plan_path else _default_plan(agents=agents)
    for agent in plan["agents"]:
        _validate_eval_args(agent.get("eval_args") or [])
        agent_id = _validate_agent_id(agent["id"])
        path = agent_dir / f"{agent_id}.md"
        _write_if_allowed(path, _render_agent(agent), force=force)
        script_path = agent_dir / f"{agent_id}.eval.sh"
        _write_executable_if_allowed(script_path, _render_eval_script(agent), force=force)


def _read_plan(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("plan JSON must be an object")
    agents = data.get("agents") or []
    if not isinstance(agents, list):
        raise SystemExit("plan JSON must contain list field: agents")
    normalized_agents = [_normalize_agent(item, index) for index, item in enumerate(agents)]
    if not normalized_agents:
        raise SystemExit("plan JSON must contain at least one agent")
    return {"agents": normalized_agents}


def _default_plan(*, agents: int) -> dict[str, list[dict[str, Any]]]:
    agent_count = max(1, agents)
    agent_items = []
    for index in range(agent_count):
        agent_id = f"agent-{index + 1}"
        agent_items.append(
            _normalize_agent(
                {
                    "id": agent_id,
                    "title": f"Agent {index + 1}",
                    "brief": DEFAULT_DIRECTIONS[index % len(DEFAULT_DIRECTIONS)],
                    "starting_steps": [
                        f"Read CORAL_SHARED/knowledge/briefs/agent-seeds/{agent_id}.md first.",
                        "Use `coral kb index manual`, `coral kb index external`, and `coral kb index practice --by score` to locate only the evidence needed for the first change.",
                        f"After the first change, run CORAL_SHARED/knowledge/briefs/agent-seeds/{agent_id}.eval.sh before making broad changes.",
                        "Submit one clear attempt and record what changed, including score and guardrail movement.",
                    ],
                    "avoid": [
                        "Editing grader or hidden eval files unless explicitly instructed.",
                        "Optimizing only the breakthrough metric while breaking guardrails.",
                    ],
                },
                index,
            )
        )
    return {"agents": agent_items}


def _normalize_agent(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = {}
    agent_id = str(item.get("id") or item.get("agent_id") or f"agent-{index + 1}")
    title = str(item.get("title") or agent_id)
    brief = str(item.get("brief") or item.get("summary") or "")
    return {
        "id": agent_id,
        "title": title,
        "brief": brief,
        "focus": _string_list(item.get("focus")),
        "starting_steps": _string_list(item.get("starting_steps") or item.get("steps")),
        "avoid": _string_list(item.get("avoid") or item.get("risks")),
        "must_read": _string_list(item.get("must_read") or item.get("knowledge")),
        "optional_read": _string_list(item.get("optional_read")),
        "eval_targets": _string_list(item.get("eval_targets")),
        "eval_args": _string_list(item.get("eval_args")),
        "eval_message": str(item.get("eval_message") or "").strip(),
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _render_agent(agent: dict[str, Any]) -> str:
    agent_id = _validate_agent_id(agent["id"])
    lines = [f"# Runnable Initialization Plan: {agent['title']}", ""]
    lines.extend([f"agent_id: {agent['id']}", ""])
    lines.extend(
        [
            "First eval script: `CORAL_SHARED/knowledge/briefs/agent-seeds/"
            + agent_id
            + ".eval.sh`",
            "",
        ]
    )
    lines.extend(["## Starting Route", agent["brief"] or "Define this agent's technical route.", ""])
    _append_list(lines, "Focus", agent.get("focus") or [])
    lines.extend(
        [
            "## Knowledge Lookup",
            "- Start with `eval_spec.md` and the framework manuals.",
            "- Use `coral kb index external` for static references.",
            "- Use `coral kb index practice --by score|route|agent|metric` for run experience.",
            "- Use `coral kb read <id>` only for the specific item you need.",
            "",
        ]
    )
    _append_list(lines, "Must Read", agent.get("must_read") or [])
    _append_list(lines, "Optional If Needed", agent.get("optional_read") or [])
    _append_list(lines, "Eval Targets", agent.get("eval_targets") or [])
    _append_list(lines, "Runnable First Steps", agent.get("starting_steps") or [])
    lines.extend(
        [
            "## First Eval",
            "- Make the smallest coherent first implementation or diagnostic change for this route.",
            "- Then run `bash CORAL_SHARED/knowledge/briefs/agent-seeds/" + agent_id + ".eval.sh` from the repo worktree.",
            "- The script submits the official CORAL eval and cites this initialization route in the message.",
            "",
            "## Evolution Rule",
            "- Start from this plan, but do not preserve it as an identity.",
            "- After each eval, use `coral kb note` for short observations and `coral kb archive` for durable practice knowledge.",
            "- Change route only when score feedback, guardrails, or shared evidence justify the pivot.",
            "",
        ]
    )
    _append_list(lines, "Avoid", agent.get("avoid") or [])
    return "\n".join(lines).rstrip() + "\n"


def _render_eval_script(agent: dict[str, Any]) -> str:
    agent_id = _validate_agent_id(agent["id"])
    title = str(agent["title"])
    default_message = (
        agent.get("eval_message")
        or f"{agent_id} initialization eval: {title}"
    )
    eval_args = [
        _shell_single_quote(arg)
        for arg in _validated_eval_args(agent.get("eval_args") or [])
    ]
    eval_args_text = " ".join(eval_args)
    if eval_args_text:
        eval_args_line = f"extra_args=({eval_args_text})"
    else:
        eval_args_line = "extra_args=()"
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "# Run this after applying the agent's first implementation or diagnostic change.\n"
        "# It submits the official CORAL eval; it intentionally does not edit code.\n"
        f"message=${{1:-{_shell_single_quote(default_message)}}}\n"
        "if [[ -n \"${CORAL_EVAL_MESSAGE:-}\" ]]; then\n"
        "  message=\"${CORAL_EVAL_MESSAGE}\"\n"
        "fi\n\n"
        f"{eval_args_line}\n"
        "if [[ \"${CORAL_EVAL_TUNE:-}\" == \"1\" ]]; then\n"
        "  extra_args+=(--tune)\n"
        "fi\n"
        "if [[ \"${CORAL_EVAL_NO_WAIT:-}\" == \"1\" ]]; then\n"
        "  extra_args+=(--no-wait)\n"
        "fi\n"
        "if [[ -n \"${CORAL_EVAL_TIMEOUT:-}\" ]]; then\n"
        "  case \"${CORAL_EVAL_TIMEOUT}\" in\n"
        "    ''|*[!0-9.]*) echo \"CORAL_EVAL_TIMEOUT must be numeric\" >&2; exit 2 ;;\n"
        "  esac\n"
        "  extra_args+=(--timeout \"${CORAL_EVAL_TIMEOUT}\")\n"
        "fi\n\n"
        "coral diff || true\n"
        "coral eval \"${extra_args[@]}\" -m \"${message}\"\n"
    )


def _append_list(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.extend([f"## {title}"])
    lines.extend(f"- {item}" for item in items)
    lines.append("")


def _write_if_allowed(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        if path.read_text(encoding="utf-8", errors="replace") != content:
            raise SystemExit(f"{path} exists and differs; rerun with --force to replace it")
        path.chmod(path.stat().st_mode | 0o111)
        return
    path.write_text(content, encoding="utf-8")


def _write_executable_if_allowed(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        if path.read_text(encoding="utf-8", errors="replace") != content:
            raise SystemExit(f"{path} exists and differs; rerun with --force to replace it")
        return
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o755)


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip()).strip("-")
    return safe or "item"


def _validate_agent_id(value: str) -> str:
    agent_id = str(value).strip()
    if not agent_id:
        raise SystemExit("agent id must be non-empty")
    if _safe_id(agent_id) != agent_id:
        raise SystemExit(
            f"agent id {agent_id!r} contains unsupported characters; "
            "use only letters, numbers, underscore, dash, and dot"
        )
    return agent_id


def _validated_eval_args(args: list[str]) -> list[str]:
    args = [str(arg).strip() for arg in args if str(arg).strip()]
    _validate_eval_args(args)
    return args


def _validate_eval_args(args: list[str]) -> None:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--tune", "--no-wait"}:
            index += 1
            continue
        if arg == "--timeout":
            if index + 1 >= len(args) or not _is_number(args[index + 1]):
                raise SystemExit("eval_args --timeout must be followed by a numeric value")
            index += 2
            continue
        if arg.startswith("--timeout="):
            value = arg.split("=", 1)[1]
            if _is_number(value):
                index += 1
                continue
        raise SystemExit(
            "eval_args may only contain --tune, --no-wait, or --timeout <seconds>"
        )


def _is_number(value: str) -> bool:
    try:
        number = float(value)
    except ValueError:
        return False
    return number >= 0


def _shell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("knowledge_dir", type=Path, help="knowledge directory to update")
    parser.add_argument("--plan", type=Path, help="JSON plan generated by Codex")
    parser.add_argument("--agents", type=int, default=1, help="agent count for placeholder plan")
    parser.add_argument("--force", action="store_true", help="overwrite existing brief files")
    args = parser.parse_args()
    write_plan(
        args.knowledge_dir,
        agents=args.agents,
        plan_path=args.plan,
        force=args.force,
    )


if __name__ == "__main__":
    main()
