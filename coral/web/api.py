"""REST API endpoints for the CORAL web dashboard."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from starlette.requests import Request
from starlette.responses import JSONResponse

from coral.agent.state import read_agent_state
from coral.cli._helpers import (
    has_docker_marker,
    is_docker_run_alive,
    kill_docker_container,
    kill_orphaned_agents,
    kill_tmux_session,
)
from coral.config import CoralConfig

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _coral_dir(request: Request) -> Path:
    return request.app.state.coral_dir


def _agent_control_path(coral_dir: Path, agent_id: str) -> Path:
    return coral_dir / "public" / "control" / "agents" / f"{agent_id}.json"


def _read_agent_desired_state(coral_dir: Path, agent_id: str) -> str | None:
    path = _agent_control_path(coral_dir, agent_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    desired = str(data.get("desired_state", "")).strip().lower()
    return desired if desired in {"running", "stopped"} else None


def _read_agent_control_payload(coral_dir: Path, agent_id: str) -> dict[str, Any]:
    path = _agent_control_path(coral_dir, agent_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_agent_control_payload(
    coral_dir: Path,
    agent_id: str,
    payload: dict[str, Any],
) -> Path:
    path = _agent_control_path(coral_dir, agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["agent_id"] = agent_id
    payload["updated_at"] = datetime.now(UTC).isoformat()
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)
    return path


def _write_agent_desired_state(coral_dir: Path, agent_id: str, desired_state: str) -> Path:
    payload = {
        "desired_state": desired_state,
    }
    return _write_agent_control_payload(coral_dir, agent_id, payload)


def _known_agent_ids(coral_dir: Path) -> set[str]:
    """Return agent ids that are planned or have observable runtime state."""
    ids: set[str] = set()

    try:
        from coral.hub.plan import build_agent_plan

        plan = build_agent_plan(coral_dir)
        for island in plan.get("islands", []):
            if not isinstance(island, dict):
                continue
            for agent in island.get("agents", []):
                if isinstance(agent, dict) and agent.get("agent_id"):
                    ids.add(str(agent["agent_id"]))
    except Exception:
        pass

    try:
        ids.update(read_agent_state(coral_dir).agents.keys())
    except Exception:
        pass

    pid_map_file = coral_dir / "public" / "agent_pids.json"
    if pid_map_file.exists():
        try:
            data = json.loads(pid_map_file.read_text())
            if isinstance(data, dict):
                ids.update(str(agent_id) for agent_id in data)
        except (OSError, json.JSONDecodeError):
            pass

    try:
        from coral.web.logs import list_log_files

        ids.update(list_log_files(coral_dir).keys())
    except Exception:
        pass

    return ids


def _validate_agent_control_target(coral_dir: Path, agent_id: str) -> JSONResponse | None:
    if not _AGENT_ID_RE.fullmatch(agent_id):
        return JSONResponse({"ok": False, "message": "invalid agent id"}, status_code=400)
    known = _known_agent_ids(coral_dir)
    if agent_id not in known:
        return JSONResponse(
            {
                "ok": False,
                "message": f"agent {agent_id!r} is not part of this run",
                "known_agents": sorted(known),
            },
            status_code=404,
        )
    return None


async def get_config(request: Request) -> JSONResponse:
    """GET /api/config — return the run configuration."""
    config_path = _coral_dir(request) / "config.yaml"
    if not config_path.exists():
        return JSONResponse({"error": "config.yaml not found"}, status_code=404)

    with open(config_path) as f:
        config = yaml.safe_load(f)
    return JSONResponse(config)


async def get_attempts(request: Request) -> JSONResponse:
    """GET /api/attempts — return all attempts sorted by timestamp.

    Aggregates across islands so the dashboard reflects the whole team, not
    just the attempts in ``coral_dir/public/attempts`` (which is empty in
    multi-island mode).
    """
    from coral.hub.attempts import _read_all_island_attempts

    attempts = _read_all_island_attempts(_coral_dir(request))
    attempts.sort(key=lambda a: a.timestamp)
    return JSONResponse([a.to_dict() for a in attempts])


async def get_leaderboard(request: Request) -> JSONResponse:
    """GET /api/leaderboard?top=N — return top N attempts by score."""
    from coral.hub.attempts import get_leaderboard as _get_leaderboard

    top_n = int(request.query_params.get("top", "20"))
    attempts = _get_leaderboard(
        str(_coral_dir(request)), top_n=top_n, direction=_direction(request)
    )
    return JSONResponse([a.to_dict() for a in attempts])


async def get_attempt_detail(request: Request) -> JSONResponse:
    """GET /api/attempts/{hash} — return a single attempt.

    Searches every island's attempts dir (and the legacy ``public/attempts``)
    so a hash from any island resolves. Mirrors the cross-island lookup that
    ``coral show`` already does in the CLI.
    """
    from coral.hub._island import all_view_roots

    commit_hash = request.path_params["hash"]
    coral_dir = _coral_dir(request)

    # Direct hit anywhere first.
    for view_root in all_view_roots(coral_dir):
        candidate = view_root / "attempts" / f"{commit_hash}.json"
        if candidate.exists():
            return JSONResponse(json.loads(candidate.read_text()))

    # Prefix match — ambiguous across islands → 404 rather than guessing.
    matches: list[Path] = []
    for view_root in all_view_roots(coral_dir):
        matches.extend((view_root / "attempts").glob(f"{commit_hash}*.json"))
    if len(matches) == 1:
        return JSONResponse(json.loads(matches[0].read_text()))
    return JSONResponse({"error": "attempt not found"}, status_code=404)


async def get_agent_attempts(request: Request) -> JSONResponse:
    """GET /api/attempts/agent/{id} — return attempts for a specific agent."""
    from coral.hub.attempts import get_agent_attempts as _get_agent_attempts

    agent_id = request.path_params["id"]
    attempts = _get_agent_attempts(str(_coral_dir(request)), agent_id)
    return JSONResponse([a.to_dict() for a in attempts])


async def get_notes(request: Request) -> JSONResponse:
    """GET /api/notes — return all notes."""
    from coral.hub.notes import list_notes

    entries = list_notes(str(_coral_dir(request)))
    for i, entry in enumerate(entries):
        entry["index"] = i
    return JSONResponse(entries)


async def get_skills(request: Request) -> JSONResponse:
    """GET /api/skills — return all skills."""
    from coral.hub.skills import list_skills

    skills = list_skills(str(_coral_dir(request)))
    # Convert any non-string values (e.g. datetime from YAML) to strings
    for sk in skills:
        for key in ("created", "updated"):
            if sk.get(key) and not isinstance(sk[key], str):
                sk[key] = str(sk[key])
    return JSONResponse(skills)


async def get_knowledge(request: Request) -> JSONResponse:
    """GET /api/knowledge — return sources from the unified knowledge base."""
    from coral.hub.knowledge import list_knowledge_sources

    return JSONResponse({"sources": list_knowledge_sources(_coral_dir(request))})


async def get_knowledge_eval_spec(request: Request) -> JSONResponse:
    """GET /api/knowledge/eval-spec — return the run-global eval design spec."""
    from coral.hub.knowledge import read_eval_spec

    return JSONResponse(read_eval_spec(_coral_dir(request)))


async def get_review(request: Request) -> JSONResponse:
    """GET /api/review — return a structured post-run review summary."""
    from coral.hub.review import build_review_summary

    coral_dir = _coral_dir(request)
    manager_alive = _run_alive(coral_dir)
    readiness_response = await get_control_readiness(request)
    try:
        readiness = json.loads(readiness_response.body)
    except json.JSONDecodeError:
        readiness = {}
    summary = build_review_summary(
        coral_dir,
        config=_load_yaml(coral_dir / "config.yaml"),
        run_state=_run_state(coral_dir, manager_alive=manager_alive),
        usage=_aggregate_log_usage(coral_dir),
        readiness=readiness,
    )
    return JSONResponse(summary)


async def add_knowledge_note(request: Request) -> JSONResponse:
    """POST /api/knowledge/notes — add a run-global review note."""
    from coral.hub.knowledge import add_review_note

    body = await request.json()
    try:
        result = add_review_note(
            _coral_dir(request),
            title=str(body.get("title") or ""),
            body=str(body.get("body") or ""),
            category=str(body.get("category") or "synthesis"),
            creator=str(body.get("creator") or "user"),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse(result)


async def add_knowledge_source(request: Request) -> JSONResponse:
    """POST /api/knowledge/sources — add a proposed reference source."""
    from coral.hub.knowledge import add_reference_source

    body = await request.json()
    try:
        result = add_reference_source(
            _coral_dir(request),
            title=str(body.get("title") or ""),
            url=str(body.get("url") or body.get("origin_url") or ""),
            category=str(body.get("category") or "web"),
            note=str(body.get("note") or ""),
            added_by=str(body.get("added_by") or "user"),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse(result)


async def save_knowledge_eval_spec(request: Request) -> JSONResponse:
    """POST /api/knowledge/eval-spec — update the run-global eval design spec."""
    from coral.hub.knowledge import write_eval_spec

    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        return JSONResponse({"ok": False, "message": "content must be a string"}, status_code=400)
    try:
        result = write_eval_spec(
            _coral_dir(request),
            content=content,
            writer=str(body.get("writer") or "user"),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse(result)


async def update_knowledge_source_status(request: Request) -> JSONResponse:
    """POST /api/knowledge/sources/status — mark a run-global source reviewed."""
    from coral.hub.knowledge import update_reference_source_status

    body = await request.json()
    selector = body.get("selector")
    if not isinstance(selector, dict):
        selector = {
            key: body.get(key)
            for key in ("id", "relative_path", "title", "origin_url", "url")
            if body.get(key) is not None
        }
    try:
        result = update_reference_source_status(
            _coral_dir(request),
            selector=selector,
            status=str(body.get("status") or ""),
            reviewer=str(body.get("reviewer") or "user"),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse(result)


async def get_skill_detail(request: Request) -> JSONResponse:
    """GET /api/skills/{name} — return a specific skill."""
    from coral.hub._island import all_view_roots
    from coral.hub.skills import read_skill

    name = request.path_params["name"]
    coral_dir = _coral_dir(request)
    skill_dir = None
    for view_root in all_view_roots(coral_dir):
        candidate = view_root / "skills" / name
        if candidate.is_dir():
            skill_dir = candidate
            break
    if skill_dir is None:
        return JSONResponse({"error": "skill not found"}, status_code=404)

    info = read_skill(skill_dir)
    return JSONResponse(info)


async def get_logs(request: Request) -> JSONResponse:
    """GET /api/logs/{agent_id} — return parsed log turns for an agent."""
    from coral.web.logs import list_log_files, parse_log_file

    agent_id = request.path_params["agent_id"]
    coral_dir = _coral_dir(request)
    agent_logs = list_log_files(coral_dir)

    if agent_id not in agent_logs:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    # Parse all log files for this agent, grouped by session
    sessions: list[dict[str, Any]] = []
    all_session_metas: list[dict[str, Any]] = []
    global_turn_idx = 0
    for log_info in sorted(agent_logs[agent_id], key=lambda x: x["index"]):
        turns, _, session_meta = parse_log_file(Path(log_info["path"]))
        session_turns = []
        for t in turns:
            td = t.to_dict()
            td["index"] = global_turn_idx
            global_turn_idx += 1
            session_turns.append(td)
        session_data: dict[str, Any] = {
            "session_index": log_info["index"],
            "turns": session_turns,
        }
        if session_meta:
            session_data["meta"] = session_meta.to_dict()
            all_session_metas.append(session_meta.to_dict())
        sessions.append(session_data)

    # Also flatten for backward compat
    all_turns = [t for s in sessions for t in s["turns"]]

    # Aggregate session-level metadata for the whole agent
    agent_meta: dict[str, Any] | None = None
    if all_session_metas:
        total_cost = sum(m.get("total_cost_usd") or 0 for m in all_session_metas)
        total_duration = sum(m.get("duration_ms") or 0 for m in all_session_metas)
        total_api_duration = sum(m.get("duration_api_ms") or 0 for m in all_session_metas)
        total_turns = sum(m.get("num_turns") or 0 for m in all_session_metas)
        # Aggregate usage across sessions
        agg_usage: dict[str, int] = {}
        for m in all_session_metas:
            for k, v in m.get("usage", {}).items():
                if isinstance(v, (int, float)):
                    agg_usage[k] = agg_usage.get(k, 0) + int(v)
        agent_meta = {
            "total_cost_usd": total_cost,
            "duration_ms": total_duration,
            "duration_api_ms": total_api_duration,
            "num_turns": total_turns,
            "usage": agg_usage,
        }

    return JSONResponse(
        {
            "agent_id": agent_id,
            "log_files": agent_logs[agent_id],
            "turns": all_turns,
            "sessions": sessions,
            "agent_meta": agent_meta,
        }
    )


async def get_logs_list(request: Request) -> JSONResponse:
    """GET /api/logs — return available agents and their log files."""
    from coral.web.logs import list_log_files

    agent_logs = list_log_files(_coral_dir(request))
    return JSONResponse(agent_logs)


def _direction(request: Request) -> str:
    """Read grader direction from config. Returns 'maximize' or 'minimize'."""
    config_path = _coral_dir(request) / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        return (config.get("grader") or {}).get("direction", "maximize")
    return "maximize"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _latest_progress_event(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            return event
    return None


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _new_usage_summary() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "uncategorized_tokens": 0,
        "total_tokens": 0,
        "cache_hit_rate": 0.0,
        "total_cost_usd": 0.0,
        "duration_ms": 0,
        "duration_api_ms": 0,
        "num_turns": 0,
    }


def _finalize_usage_summary(summary: dict[str, Any]) -> dict[str, Any]:
    total_input = (
        int(summary["input_tokens"])
        + int(summary["cache_creation_tokens"])
        + int(summary["cache_read_tokens"])
    )
    total_tokens = total_input + int(summary["output_tokens"]) + int(summary["uncategorized_tokens"])
    summary["total_tokens"] = total_tokens
    summary["cache_hit_rate"] = (
        float(summary["cache_read_tokens"]) / float(total_input) if total_input else 0.0
    )
    summary["duration_ms"] = int(summary["duration_ms"])
    summary["duration_api_ms"] = int(summary["duration_api_ms"])
    summary["num_turns"] = int(summary["num_turns"])
    return summary


def _add_usage_summary(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "uncategorized_tokens",
        "total_cost_usd",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
    ):
        target[key] += source.get(key, 0) or 0


def _first_number(mapping: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _usage_from_mapping(mapping: dict[str, Any]) -> dict[str, int]:
    input_tokens = int(_first_number(mapping, "input_tokens", "prompt_tokens"))
    output_tokens = int(_first_number(mapping, "output_tokens", "completion_tokens"))
    cache_creation = int(
        _first_number(
            mapping,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_creation",
            "cache_write_tokens",
        )
    )
    cache_read = int(
        _first_number(
            mapping,
            "cache_read_input_tokens",
            "cache_read_tokens",
            "cache_read",
            "cached_input_tokens",
        )
    )
    total = int(_first_number(mapping, "total_tokens", "tokens"))
    known = input_tokens + output_tokens + cache_creation + cache_read
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "uncategorized_tokens": max(0, total - known),
    }


def _add_token_usage(target: dict[str, Any], usage: dict[str, Any]) -> int:
    parsed = _usage_from_mapping(usage)
    for key, value in parsed.items():
        target[key] += value
    return sum(parsed.values())


def _scan_generic_log_usage(path: Path) -> dict[str, Any]:
    """Best-effort usage extraction for non-Claude JSONL event formats."""
    summary = _new_usage_summary()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return summary

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        usage = event.get("usage")
        if isinstance(usage, dict):
            _add_token_usage(summary, usage)
        else:
            _add_token_usage(summary, event)

        summary["total_cost_usd"] += _first_number(
            event, "total_cost_usd", "cost_usd", "cost"
        )
        summary["duration_ms"] += int(_first_number(event, "duration_ms"))
        summary["duration_api_ms"] += int(_first_number(event, "duration_api_ms"))
        summary["num_turns"] += int(_first_number(event, "num_turns", "turns"))

    return summary


def _aggregate_log_usage(coral_dir: Path) -> dict[str, Any]:
    """Aggregate token/cost/cache usage from parsed agent logs."""
    from coral.web.logs import list_log_files, parse_log_file

    agents: dict[str, dict[str, Any]] = {}
    total = _new_usage_summary()
    for agent_id, logs in list_log_files(coral_dir).items():
        agent_summary = _new_usage_summary()
        for log_info in logs:
            log_path = Path(log_info["path"])
            turns, _, session_meta = parse_log_file(log_path)
            parsed_tokens = 0
            for turn in turns:
                usage = turn.usage or {}
                parsed_tokens += _add_token_usage(
                    agent_summary,
                    {
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cache_creation_tokens": usage.get("cache_creation"),
                        "cache_read_tokens": usage.get("cache_read"),
                    },
                )
            if session_meta:
                meta = session_meta.to_dict()
                agent_summary["total_cost_usd"] += _numeric(meta.get("total_cost_usd"))
                agent_summary["duration_ms"] += int(_numeric(meta.get("duration_ms")))
                agent_summary["duration_api_ms"] += int(_numeric(meta.get("duration_api_ms")))
                agent_summary["num_turns"] += int(_numeric(meta.get("num_turns")))
            if parsed_tokens == 0:
                _add_usage_summary(agent_summary, _scan_generic_log_usage(log_path))

        _finalize_usage_summary(agent_summary)
        agents[agent_id] = agent_summary
        _add_usage_summary(total, agent_summary)

    _finalize_usage_summary(total)
    total["agents"] = agents
    return total


async def get_evals(request: Request) -> JSONResponse:
    """GET /api/evals — return queued/evaluating eval jobs with latest progress."""
    from coral.config import CoralConfig
    from coral.grader.daemon import planned_evaluating_hashes
    from coral.hub._island import all_view_roots
    from coral.hub.attempts import _read_all_island_attempts

    coral_dir = _coral_dir(request)
    config = _load_yaml(coral_dir / "config.yaml")
    grader_cfg = config.get("grader") or {}
    parallel_cfg = grader_cfg.get("parallel") or {}
    try:
        max_workers = max(int(parallel_cfg.get("max_workers", 1)), 1)
    except (TypeError, ValueError):
        max_workers = 1
    try:
        coral_config = CoralConfig.from_dict(config)
    except Exception:
        coral_config = None

    pending = [
        a for a in _read_all_island_attempts(coral_dir) if a.status == "pending" and a.score is None
    ]
    pending.sort(key=lambda a: a.timestamp)
    if coral_config is not None:
        evaluating_hashes = planned_evaluating_hashes(pending, coral_config, max_workers=max_workers)
    else:
        evaluating_hashes = {a.commit_hash for a in pending[:max_workers]}

    view_roots = all_view_roots(coral_dir)
    eval_log_roots = {root.name: root / "eval_logs" for root in view_roots}
    if len(view_roots) == 1 and view_roots[0].name == "public":
        eval_log_roots[None] = view_roots[0] / "eval_logs"

    jobs: list[dict[str, Any]] = []
    for attempt in pending:
        island_id = (attempt.metadata or {}).get("island_id")
        log_root = eval_log_roots.get(str(island_id)) or eval_log_roots.get(None)
        progress = None
        if log_root is not None:
            progress = _latest_progress_event(log_root / attempt.commit_hash / "progress.jsonl")

        jobs.append(
            {
                "commit_hash": attempt.commit_hash,
                "agent_id": attempt.agent_id,
                "title": attempt.title,
                "timestamp": attempt.timestamp,
                "queue_status": "evaluating"
                if attempt.commit_hash in evaluating_hashes
                else "waiting",
                "island_id": island_id,
                "eval_version": (attempt.metadata or {}).get(
                    "eval_version", grader_cfg.get("eval_version", "eval_v1")
                ),
                "eval_profile": (attempt.metadata or {}).get(
                    "eval_profile", grader_cfg.get("profile", "default")
                ),
                "resources": (attempt.metadata or {}).get(
                    "resources", grader_cfg.get("resources", {})
                ),
                "progress": progress,
            }
        )

    resource_pool = (parallel_cfg.get("resources") or {}) if isinstance(parallel_cfg, dict) else {}
    return JSONResponse({"max_workers": max_workers, "resource_pool": resource_pool, "jobs": jobs})


def _results_dir(request: Request) -> Path:
    return request.app.state.results_dir


def _current_task_run(coral_dir: Path) -> tuple[str, str]:
    resolved = coral_dir.resolve()
    return resolved.parent.parent.name, resolved.parent.name


def _manager_pid(coral_dir: Path) -> int | None:
    pid_file = coral_dir / "public" / "manager.pid"
    return _read_pid_file(pid_file)


def _read_pid_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _terminate_pid_file(pid_file: Path, *, label: str, timeout_seconds: float = 4.0) -> list[str]:
    """Terminate the process recorded in pid_file and remove the stale marker."""
    stopped: list[str] = []
    pid = _read_pid_file(pid_file)
    if pid is None:
        pid_file.unlink(missing_ok=True)
        return stopped
    try:
        os.kill(pid, signal.SIGTERM)
        stopped.append(f"{label}:{pid}")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not _process_alive(pid):
                break
            time.sleep(0.2)
        if _process_alive(pid):
            os.kill(pid, signal.SIGKILL)
            stopped.append(f"{label}:{pid}:killed")
    except (ProcessLookupError, PermissionError):
        pass
    finally:
        pid_file.unlink(missing_ok=True)
    return stopped


def _process_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _agent_processes_alive(coral_dir: Path) -> bool:
    """Return True when any recorded agent process is still alive on this host."""
    public = coral_dir / "public"
    pid_map_file = public / "agent_pids.json"
    if pid_map_file.exists():
        try:
            raw_pid_map = json.loads(pid_map_file.read_text())
            if isinstance(raw_pid_map, dict):
                for pid in raw_pid_map.values():
                    try:
                        parsed = int(pid)
                    except (TypeError, ValueError):
                        continue
                    if _process_alive(parsed):
                        return True
        except (json.JSONDecodeError, OSError):
            pass

    pids_file = public / "agent.pids"
    if pids_file.exists():
        try:
            for line in pids_file.read_text().strip().splitlines():
                try:
                    parsed = int(line.strip())
                except ValueError:
                    continue
                if _process_alive(parsed):
                    return True
        except OSError:
            pass
    return False


def _run_alive(coral_dir: Path) -> bool:
    return (
        _process_alive(_manager_pid(coral_dir))
        or is_docker_run_alive(coral_dir)
        or _agent_processes_alive(coral_dir)
    )



def _control_dir(coral_dir: Path) -> Path:
    return coral_dir / "public" / "control"


def _control_instruction_path(coral_dir: Path) -> Path:
    return _control_dir(coral_dir) / "next_instruction.md"


def _copy_config_path(source: dict[str, Any], target: dict[str, Any], path: tuple[str, ...]) -> None:
    current: Any = source
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return
        current = current[key]

    dest = target
    for key in path[:-1]:
        next_dest = dest.get(key)
        if not isinstance(next_dest, dict):
            next_dest = {}
            dest[key] = next_dest
        dest = next_dest
    dest[path[-1]] = current


def _run_has_activity(coral_dir: Path) -> bool:
    if _run_alive(coral_dir):
        return True

    state_path = coral_dir / "public" / "run_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            state = {}
        if isinstance(state, dict) and state.get("started_at"):
            return True

    eval_count_path = coral_dir / "eval_count"
    if eval_count_path.exists():
        try:
            if int(eval_count_path.read_text().strip()) > 0:
                return True
        except (OSError, ValueError):
            pass

    from coral.hub._island import all_view_roots

    for view_root in all_view_roots(coral_dir):
        attempts_dir = view_root / "attempts"
        if attempts_dir.is_dir() and any(attempts_dir.glob("*.json")):
            return True
        logs_dir = view_root / "logs"
        if logs_dir.is_dir() and any(logs_dir.glob("*.log")):
            return True

    return False


def _sanitize_control_config_update(
    incoming: dict[str, Any], current: dict[str, Any], *, run_has_activity: bool
) -> dict[str, Any]:
    """Keep Codex-owned workspace fields out of ordinary control-panel saves."""
    sanitized = dict(incoming)

    # These define the prepared workspace and task identity, not runtime knobs.
    for path in (
        ("task",),
        ("workspace",),
        ("knowledge",),
        ("agents", "count"),
        ("agents", "max_turns"),
        ("agents", "assignments"),
        ("grader", "entrypoint"),
        ("grader", "setup"),
        ("grader", "private"),
    ):
        _copy_config_path(current, sanitized, path)

    # Once a timestamp has run, session identity and topology are fixed.
    if run_has_activity:
        for path in (
            ("agents", "runtime"),
            ("islands", "count"),
            ("grader", "direction"),
            ("grader", "eval_version"),
        ):
            _copy_config_path(current, sanitized, path)
        current_options = (current.get("agents") or {}).get("runtime_options")
        incoming_options = (sanitized.get("agents") or {}).get("runtime_options")
        if isinstance(current_options, dict):
            merged_options = dict(current_options)
            if isinstance(incoming_options, dict):
                for key in ("model_reasoning_effort", "thinking"):
                    if key in incoming_options:
                        merged_options[key] = incoming_options[key]
            agents = sanitized.get("agents")
            if not isinstance(agents, dict):
                agents = {}
                sanitized["agents"] = agents
            agents["runtime_options"] = merged_options

    return sanitized


def _parse_iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _run_state(coral_dir: Path, *, manager_alive: bool) -> dict[str, Any]:
    """Read run lifecycle state, computing remaining time for the dashboard."""
    state_path = coral_dir / "public" / "run_state.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text())
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError):
            state = {}

    config = _load_yaml(coral_dir / "config.yaml")
    configured_limit = ((config.get("run") or {}).get("max_runtime_seconds") or 0)
    try:
        configured_limit = int(configured_limit)
    except (TypeError, ValueError):
        configured_limit = 0

    deadline_epoch = _parse_iso_epoch(state.get("deadline_at"))
    remaining = None
    if deadline_epoch is not None:
        remaining = max(0.0, deadline_epoch - time.time())
    started_epoch = _parse_iso_epoch(state.get("started_at"))
    elapsed = None
    if started_epoch is not None:
        end_epoch = time.time() if manager_alive else _parse_iso_epoch(state.get("updated_at"))
        elapsed = max(0.0, (end_epoch or time.time()) - started_epoch)

    status = state.get("status") or ("running" if manager_alive else "stopped")
    if not manager_alive and status in {"starting", "running", "stopping"}:
        status = "stopped"

    return {
        "status": status,
        "started_at": state.get("started_at"),
        "deadline_at": state.get("deadline_at"),
        "max_runtime_seconds": state.get("max_runtime_seconds", configured_limit),
        "remaining_seconds": remaining,
        "elapsed_seconds": elapsed,
        "stopped_reason": state.get("stopped_reason"),
        "updated_at": state.get("updated_at"),
    }


def _write_control_run_state(coral_dir: Path, *, status: str, reason: str | None) -> None:
    """Best-effort state update for web-triggered stops."""
    public = coral_dir / "public"
    public.mkdir(parents=True, exist_ok=True)
    current = _run_state(coral_dir, manager_alive=status in {"starting", "running", "stopping"})
    current["status"] = status
    current["stopped_reason"] = reason
    current["updated_at"] = datetime.now(UTC).isoformat()
    if status == "stopped":
        current["remaining_seconds"] = 0
    tmp = public / ".run_state.json.tmp"
    tmp.write_text(json.dumps(current, indent=2))
    tmp.replace(public / "run_state.json")


def _enumerate_runs(results_dir: Path, current_coral_dir: Path) -> dict:
    """Walk results_dir and return structured task/run listing."""
    current_resolved = current_coral_dir.resolve()
    current_task = current_resolved.parent.parent.name
    current_run = current_resolved.parent.name

    tasks = []
    if not results_dir.is_dir():
        return {"current": {"task": current_task, "run": current_run}, "tasks": tasks}

    for task_dir in sorted(results_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_slug = task_dir.name

        # Resolve "latest" symlink target
        latest_link = task_dir / "latest"
        latest_target = None
        if latest_link.is_symlink():
            try:
                latest_target = latest_link.resolve()
            except OSError:
                pass

        runs = []
        for run_dir in sorted(task_dir.iterdir(), reverse=True):
            if not run_dir.is_dir() or run_dir.is_symlink():
                continue
            coral_dir = run_dir / ".coral"
            if not coral_dir.is_dir():
                continue

            # Check manager status
            pid_file = coral_dir / "public" / "manager.pid"
            status = "stopped"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    status = "running"
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
            if status == "stopped" and is_docker_run_alive(coral_dir):
                status = "running"

            # Count attempts across every view root. In multi-island mode the
            # attempts live in islands/<id>/attempts/ — public/attempts is
            # empty — so a single-dir glob would undercount every run.
            from coral.hub._island import all_view_roots

            attempt_count = 0
            for view_root in all_view_roots(coral_dir):
                attempts_dir = view_root / "attempts"
                if attempts_dir.is_dir():
                    attempt_count += sum(1 for _ in attempts_dir.glob("*.json"))

            # Check if latest (latest symlink now points to run_dir, not .coral)
            is_latest = latest_target is not None and latest_target == run_dir.resolve()

            runs.append(
                {
                    "timestamp": run_dir.name,
                    "status": status,
                    "attempts": attempt_count,
                    "is_latest": is_latest,
                }
            )

        if runs:
            tasks.append({"slug": task_slug, "runs": runs})

    return {"current": {"task": current_task, "run": current_run}, "tasks": tasks}


async def get_runs(request: Request) -> JSONResponse:
    """GET /api/runs — list all tasks and runs."""
    results_dir = _results_dir(request)
    coral_dir = _coral_dir(request)
    data = _enumerate_runs(results_dir, coral_dir)
    return JSONResponse(data)


def _unique_run_dir(task_dir: Path) -> Path:
    base = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    candidate = task_dir / base
    suffix = 1
    while candidate.exists():
        candidate = task_dir / f"{base}-{suffix}"
        suffix += 1
    return candidate


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists() and not src.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


def _copy_public_prelaunch_state(src_public: Path, dst_public: Path) -> None:
    """Copy Codex-prepared shared state while leaving runtime artifacts behind."""
    for name in ("knowledge", "skills", "agents", "roles"):
        _copy_if_exists(src_public / name, dst_public / name)

    from coral.workspace.project import _ensure_knowledge_base, _link_legacy_notes_dir

    _ensure_knowledge_base(dst_public / "knowledge")
    _link_legacy_notes_dir(dst_public)
    _promote_active_knowledge(dst_public / "knowledge")


def _promote_active_knowledge(knowledge_dir: Path) -> None:
    """Keep only active reviewed knowledge in a freshly forked timestamp."""
    manifest = knowledge_dir / "manifest.jsonl"
    if not manifest.exists():
        return

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status and status != "accepted":
            dropped.append(entry)
            continue
        if status == "accepted":
            promoted = dict(entry)
            promoted["status"] = "accepted"
            promoted["promoted_at"] = datetime.now(UTC).isoformat()
            _promote_accepted_inbox_source(knowledge_dir, promoted)
            kept.append(promoted)
        else:
            kept.append(entry)

    protected_paths = {_manifest_relative_path(entry) for entry in kept}
    protected_paths.discard("")
    for entry in dropped:
        rel = _manifest_relative_path(entry)
        if rel and rel not in protected_paths:
            _remove_promoted_source_path(knowledge_dir, rel)

    tmp = manifest.with_name(f".{manifest.name}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in kept:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    tmp.replace(manifest)


def _manifest_relative_path(entry: dict[str, Any]) -> str:
    return str(entry.get("relative_path") or entry.get("path") or "").strip()


def _promote_accepted_inbox_source(knowledge_dir: Path, entry: dict[str, Any]) -> None:
    rel = _manifest_relative_path(entry)
    parts = Path(rel).parts
    if not parts or parts[0] != "inbox":
        return

    source = (knowledge_dir / rel).resolve()
    try:
        knowledge_root = knowledge_dir.resolve()
    except OSError:
        return
    if source != knowledge_root and knowledge_root not in source.parents:
        return
    if not source.exists() and not source.is_symlink():
        return

    category = _safe_knowledge_segment(str(entry.get("category") or "web"), default="web")
    dest_dir = knowledge_dir / "sources" / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_destination(dest_dir / source.name)
    shutil.move(str(source), str(dest))
    entry["relative_path"] = dest.relative_to(knowledge_dir).as_posix()
    entry["promoted_from"] = rel


def _safe_knowledge_segment(value: str, *, default: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    cleaned = cleaned.strip("-_")
    return cleaned or default


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _remove_promoted_source_path(knowledge_dir: Path, rel: str) -> None:
    target = (knowledge_dir / rel).resolve()
    try:
        knowledge_root = knowledge_dir.resolve()
    except OSError:
        return
    if target != knowledge_root and knowledge_root not in target.parents:
        return
    if not target.exists() and not target.is_symlink():
        return
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink()


async def create_run(request: Request) -> JSONResponse:
    """POST /api/runs/new — create a clean timestamp fork from the current run."""
    coral_dir = _coral_dir(request)
    if _run_alive(coral_dir):
        return JSONResponse(
            {"ok": False, "message": "stop the current run before creating a new timestamp"},
            status_code=409,
        )

    current_run_dir = coral_dir.parent
    task_dir = current_run_dir.parent
    results_dir = task_dir.parent
    task_slug = task_dir.name
    new_run_dir = _unique_run_dir(task_dir)
    new_coral_dir = new_run_dir / ".coral"
    new_public = new_coral_dir / "public"
    new_private = new_coral_dir / "private"
    new_public.mkdir(parents=True, exist_ok=True)
    new_private.mkdir(parents=True, exist_ok=True)

    _copy_if_exists(current_run_dir / "snapshots", new_run_dir / "snapshots")
    _copy_public_prelaunch_state(coral_dir / "public", new_public)

    for name in ("config_dir", ".coral_host_config_dir", ".coral_host_repo_path"):
        _copy_if_exists(coral_dir / name, new_coral_dir / name)

    config = _load_yaml(coral_dir / "config.yaml")
    workspace = config.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
        config["workspace"] = workspace
    workspace["results_dir"] = str(results_dir)
    workspace["run_dir"] = str(new_run_dir)
    (new_coral_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    latest = task_dir / "latest"
    if latest.is_symlink() or latest.exists():
        if latest.is_dir() and not latest.is_symlink():
            shutil.rmtree(latest)
        else:
            latest.unlink()
    latest.symlink_to(os.path.relpath(new_run_dir, task_dir))

    return JSONResponse(
        {
            "ok": True,
            "task": task_slug,
            "run": new_run_dir.name,
            "run_dir": str(new_run_dir),
            "coral_dir": str(new_coral_dir),
        }
    )


async def switch_run(request: Request) -> JSONResponse:
    """POST /api/runs/switch — switch to a different run."""
    import asyncio

    from coral.web.events import FileWatcher

    body = await request.json()
    task = body.get("task")
    run = body.get("run")
    if not task or not run:
        return JSONResponse({"error": "task and run required"}, status_code=400)

    results_dir = _results_dir(request)
    new_coral_dir = results_dir / task / run / ".coral"
    if not new_coral_dir.is_dir():
        return JSONResponse({"error": "run not found"}, status_code=404)

    app = request.app

    async with app.state._switch_lock:
        # Stop old watcher
        old_watcher = app.state.watcher
        old_watcher.stop()
        app.state._watcher_task.cancel()
        try:
            await app.state._watcher_task
        except asyncio.CancelledError:
            pass

        # Switch coral_dir
        app.state.coral_dir = new_coral_dir.resolve()

        # Start new watcher, reusing subscriber list
        new_watcher = FileWatcher(
            app.state.coral_dir,
            subscribers=old_watcher._subscribers,
        )
        app.state.watcher = new_watcher
        app.state._watcher_task = asyncio.create_task(new_watcher.run())

        # Broadcast switch event
        new_watcher._broadcast(
            {
                "event": "run:switched",
                "data": {"task": task, "run": run},
            }
        )

    return JSONResponse({"ok": True, "task": task, "run": run})


async def get_control_config(request: Request) -> JSONResponse:
    """GET /api/control/config — return editable run configuration metadata."""
    coral_dir = _coral_dir(request)
    config_path = coral_dir / "config.yaml"
    if not config_path.exists():
        return JSONResponse({"error": "config.yaml not found"}, status_code=404)

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    task, run = _current_task_run(coral_dir)
    return JSONResponse(
        {
            "config": config,
            "task": task,
            "run": run,
            "run_dir": str(coral_dir.parent),
            "config_path": str(config_path),
            "results_dir": str(_results_dir(request)),
        }
    )


async def get_control_readiness(request: Request) -> JSONResponse:
    """GET /api/control/readiness — summarize Codex-prepared workspace checks."""
    from coral.hub.readiness import build_control_readiness

    return JSONResponse(build_control_readiness(_coral_dir(request)))


async def get_control_plan(request: Request) -> JSONResponse:
    """GET /api/control/plan — preview Codex-generated agent/island plan."""
    from coral.hub.plan import build_agent_plan

    coral_dir = _coral_dir(request)
    return JSONResponse(build_agent_plan(coral_dir, config=_load_yaml(coral_dir / "config.yaml")))


async def save_control_config(request: Request) -> JSONResponse:
    """POST /api/control/config — validate and save the editable run config."""
    body = await request.json()
    config = body.get("config")
    if not isinstance(config, dict):
        return JSONResponse({"error": "config object required"}, status_code=400)

    config_path = _coral_dir(request) / "config.yaml"
    current = _load_yaml(config_path)
    if current:
        config = _sanitize_control_config_update(
            config,
            current,
            run_has_activity=_run_has_activity(_coral_dir(request)),
        )

    try:
        cfg = CoralConfig.from_dict(config)
    except Exception as exc:
        return JSONResponse({"error": f"invalid config: {exc}"}, status_code=400)
    from coral.hub.readiness import control_topology_status

    topology_status, topology_detail = control_topology_status(cfg.to_dict())
    if topology_status == "missing":
        return JSONResponse({"error": f"invalid config: {topology_detail}"}, status_code=400)

    cfg.to_yaml(config_path)
    return JSONResponse({"ok": True, "config": cfg.to_dict(), "config_path": str(config_path)})


async def get_control_instruction(request: Request) -> JSONResponse:
    """GET /api/control/instruction — return the instruction for the next resume."""
    path = _control_instruction_path(_coral_dir(request))
    instruction = ""
    if path.exists():
        try:
            instruction = path.read_text()
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"instruction": instruction, "path": str(path)})


async def save_control_instruction(request: Request) -> JSONResponse:
    """POST /api/control/instruction — save the instruction for the next resume."""
    body = await request.json()
    instruction = body.get("instruction", "")
    if instruction is None:
        instruction = ""
    if not isinstance(instruction, str):
        return JSONResponse({"error": "instruction string required"}, status_code=400)

    path = _control_instruction_path(_coral_dir(request))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(instruction)
    return JSONResponse({"ok": True, "instruction": instruction, "path": str(path)})


async def resume_control_run(request: Request) -> JSONResponse:
    """POST /api/control/resume — start or resume the selected run in the background."""
    coral_dir = _coral_dir(request)
    if _run_alive(coral_dir):
        return JSONResponse({"ok": False, "message": "run is already active"})

    readiness_response = await get_control_readiness(request)
    try:
        readiness = json.loads(readiness_response.body)
    except json.JSONDecodeError:
        readiness = {"status": "missing", "checks": []}
    if readiness.get("status") == "missing":
        missing = [
            str(check.get("label") or check.get("id") or "check")
            for check in readiness.get("checks", [])
            if isinstance(check, dict) and check.get("status") == "missing"
        ]
        detail = ", ".join(missing) if missing else "workspace readiness checks"
        return JSONResponse(
            {
                "ok": False,
                "message": f"Run blocked until Codex prepares: {detail}",
                "readiness": readiness,
            },
            status_code=409,
        )

    task, run = _current_task_run(coral_dir)
    control_dir = _control_dir(coral_dir)
    control_dir.mkdir(parents=True, exist_ok=True)
    log_path = control_dir / "last_resume.log"
    instruction_path = _control_instruction_path(coral_dir)

    log_fh = open(log_path, "ab")
    agents_dir = coral_dir.parent / "agents"
    has_agent_worktrees = agents_dir.is_dir() and any(path.is_dir() for path in agents_dir.iterdir())
    if has_agent_worktrees:
        cmd = [sys.executable, "-m", "coral.cli", "resume", "--task", task, "--run", run]
    else:
        cmd = [
            sys.executable,
            "-m",
            "coral.cli",
            "start",
            "--config",
            str(coral_dir / "config.yaml"),
        ]
    has_instruction = instruction_path.exists() and instruction_path.read_text().strip()
    if has_agent_worktrees and has_instruction:
        cmd.extend(["--instruction-file", str(instruction_path)])
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=_results_dir(request).parent,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as exc:
        log_fh.close()
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)
    finally:
        log_fh.close()

    return JSONResponse(
        {
            "ok": True,
            "message": "resume requested" if has_agent_worktrees else "start requested",
            "pid": proc.pid,
            "log_path": str(log_path),
            "instruction_path": str(instruction_path) if has_agent_worktrees and has_instruction else None,
        }
    )


async def stop_control_run(request: Request) -> JSONResponse:
    """POST /api/control/stop — pause the selected run without stopping the dashboard."""
    coral_dir = _coral_dir(request)
    stopped: list[str] = []

    if has_docker_marker(coral_dir):
        kill_docker_container(coral_dir)
        stopped.append("docker")

    pid = _manager_pid(coral_dir)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(f"manager:{pid}")
            for _ in range(20):
                if not _process_alive(pid):
                    break
                time.sleep(0.2)
            if _process_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        (coral_dir / "public" / "manager.pid").unlink(missing_ok=True)

    stopped.extend(
        _terminate_pid_file(
            coral_dir / "public" / "grader_daemon.pid",
            label="grader",
            timeout_seconds=4.0,
        )
    )

    kill_orphaned_agents(coral_dir / "public" / "agent.pids")
    kill_tmux_session(coral_dir)

    if not stopped:
        _write_control_run_state(coral_dir, status="stopped", reason="manual")
        return JSONResponse({"ok": True, "message": "run was already paused/stopped"})
    _write_control_run_state(coral_dir, status="stopped", reason="manual")
    return JSONResponse({"ok": True, "message": "pause requested", "stopped": stopped})


async def stop_agent(request: Request) -> JSONResponse:
    """POST /api/agents/{id}/stop — request one agent to stop."""
    coral_dir = _coral_dir(request)
    agent_id = request.path_params["id"]
    invalid = _validate_agent_control_target(coral_dir, agent_id)
    if invalid is not None:
        return invalid
    path = _write_agent_desired_state(coral_dir, agent_id, "stopped")
    return JSONResponse(
        {
            "ok": True,
            "message": f"stop requested for {agent_id}",
            "agent_id": agent_id,
            "desired_state": "stopped",
            "path": str(path),
        }
    )


async def resume_agent(request: Request) -> JSONResponse:
    """POST /api/agents/{id}/resume — request one manually stopped agent to resume."""
    coral_dir = _coral_dir(request)
    agent_id = request.path_params["id"]
    invalid = _validate_agent_control_target(coral_dir, agent_id)
    if invalid is not None:
        return invalid
    path = _write_agent_desired_state(coral_dir, agent_id, "running")
    return JSONResponse(
        {
            "ok": True,
            "message": f"resume requested for {agent_id}",
            "agent_id": agent_id,
            "desired_state": "running",
            "path": str(path),
        }
    )


async def prompt_agent(request: Request) -> JSONResponse:
    """POST /api/agents/{id}/prompt — inject feedback into one agent."""
    coral_dir = _coral_dir(request)
    agent_id = request.path_params["id"]
    invalid = _validate_agent_control_target(coral_dir, agent_id)
    if invalid is not None:
        return invalid
    try:
        body = await request.json()
    except Exception:
        body = {}
    prompt = str((body or {}).get("prompt", "")).strip()
    if not prompt:
        return JSONResponse({"ok": False, "message": "prompt is required"}, status_code=400)
    if len(prompt) > 12000:
        return JSONResponse({"ok": False, "message": "prompt is too long"}, status_code=400)

    payload = _read_agent_control_payload(coral_dir, agent_id)
    payload.update(
        {
            "action": "prompt",
            "desired_state": "running",
            "prompt": prompt,
            "command_id": datetime.now(UTC).isoformat(),
        }
    )
    path = _write_agent_control_payload(coral_dir, agent_id, payload)
    return JSONResponse(
        {
            "ok": True,
            "message": f"prompt queued for {agent_id}",
            "agent_id": agent_id,
            "action": "prompt",
            "desired_state": "running",
            "path": str(path),
        }
    )


async def get_status(request: Request) -> JSONResponse:
    """GET /api/status — return overall run status."""
    from coral.web.logs import list_log_files

    coral_dir = _coral_dir(request)

    # Manager liveness
    pid_file = coral_dir / "public" / "manager.pid"
    manager_alive = False
    manager_pid = None
    if pid_file.exists():
        try:
            manager_pid = int(pid_file.read_text().strip())
            os.kill(manager_pid, 0)
            manager_alive = True
        except (ProcessLookupError, PermissionError, ValueError):
            pass
    is_docker = not manager_alive and is_docker_run_alive(coral_dir)
    if is_docker:
        manager_alive = True

    # Eval count
    from coral.hub.attempts import read_eval_count

    eval_count = read_eval_count(coral_dir)

    # Attempts summary — aggregate across islands so the status pane shows
    # the whole team, not just public/attempts (empty in multi-island mode).
    from coral.hub.attempts import _read_all_island_attempts

    attempts = _read_all_island_attempts(coral_dir)
    scored = [a for a in attempts if a.score is not None]
    minimize = _direction(request) == "minimize"
    best_fn = min if minimize else max
    best = best_fn(scored, key=lambda a: a.score or 0.0) if scored else None
    pending = [a for a in attempts if a.status == "pending" and a.score is None]
    pending.sort(key=lambda a: a.timestamp)
    config = _load_yaml(coral_dir / "config.yaml")
    parallel_cfg = ((config.get("grader") or {}).get("parallel") or {})
    try:
        max_workers = max(int(parallel_cfg.get("max_workers", 1)), 1)
    except (TypeError, ValueError):
        max_workers = 1
    try:
        from coral.config import CoralConfig
        from coral.grader.daemon import planned_evaluating_hashes

        evaluating_hashes = planned_evaluating_hashes(
            pending,
            CoralConfig.from_dict(config),
            max_workers=max_workers,
        )
    except Exception:
        evaluating_hashes = {a.commit_hash for a in pending[:max_workers]}
    agent_eval_status: dict[str, str] = {}
    for pending_attempt in pending:
        if pending_attempt.agent_id not in agent_eval_status:
            agent_eval_status[pending_attempt.agent_id] = (
                "evaluating"
                if pending_attempt.commit_hash in evaluating_hashes
                else "waiting"
            )
    agent_pending_started_at: dict[str, float] = {}
    for pending_attempt in pending:
        if pending_attempt.agent_id in agent_pending_started_at:
            continue
        started = _parse_iso_epoch(pending_attempt.timestamp)
        if started is not None:
            agent_pending_started_at[pending_attempt.agent_id] = started
    agent_attempt_islands: dict[str, str] = {}
    for attempt in sorted(attempts, key=lambda a: a.timestamp):
        island_id = (attempt.metadata or {}).get("island_id")
        if island_id is not None:
            agent_attempt_islands[attempt.agent_id] = str(island_id)

    # Per-agent status
    agent_logs = list_log_files(coral_dir)
    run_usage = _aggregate_log_usage(coral_dir)
    usage_by_agent = run_usage.get("agents") if isinstance(run_usage.get("agents"), dict) else {}
    agents_status: list[dict[str, Any]] = []
    agent_runtime_states = read_agent_state(coral_dir).agents

    # Read per-agent PID map for process liveness checks.
    # Skip for Docker runs — container-internal PIDs aren't valid on the host.
    agent_pid_map: dict[str, int] = {}
    pid_map_file = coral_dir / "public" / "agent_pids.json"
    if not is_docker and pid_map_file.exists():
        try:
            raw_pid_map = json.loads(pid_map_file.read_text())
            if isinstance(raw_pid_map, dict):
                agent_pid_map = {
                    str(agent_id): int(pid)
                    for agent_id, pid in raw_pid_map.items()
                    if isinstance(pid, int | str)
                }
        except (json.JSONDecodeError, OSError):
            pass
        except (TypeError, ValueError):
            agent_pid_map = {}

    any_agent_alive = False if is_docker else _agent_processes_alive(coral_dir)

    # If agent processes are alive but manager.pid is missing, treat as alive
    if not manager_alive and any_agent_alive:
        manager_alive = True

    import time

    all_agent_ids = sorted(
        set(agent_logs)
        | set(agent_runtime_states)
        | set(agent_pid_map)
        | set(agent_eval_status)
        | set(agent_attempt_islands)
    )

    for agent_id in all_agent_ids:
        logs = agent_logs.get(agent_id, [])
        latest = max(logs, key=lambda log: log["modified"]) if logs else None
        island_id = (latest or {}).get("island_id") or agent_attempt_islands.get(agent_id)
        island_id = str(island_id) if island_id is not None else None
        now = time.time()
        status_since = agent_pending_started_at.get(agent_id)
        if latest is not None:
            age = now - latest["modified"]
            earliest = min(logs, key=lambda log: log["modified"])
            active_seconds = max(0.0, now - earliest["modified"])
        else:
            age = None
            runtime_state = agent_runtime_states.get(agent_id)
            if runtime_state and runtime_state.state_started_at is not None:
                active_seconds = max(0.0, now - runtime_state.state_started_at)
                status_since = status_since or runtime_state.state_started_at
            else:
                active_seconds = None

        agent_pid = agent_pid_map.get(agent_id)
        if agent_pid:
            # Direct PID check — most reliable
            try:
                os.kill(agent_pid, 0)
                status = "active"
            except (ProcessLookupError, PermissionError):
                status = "stopped"
        elif (any_agent_alive or is_docker) and age is not None:
            # Container or agent.pids says something is running but no per-agent mapping
            status = "active" if age < 300 else "idle"
        elif age is not None and manager_alive:
            # No per-agent PID info while the run is alive — log recency is a last resort.
            status = "active" if age < 120 else "stopped"
        else:
            status = "stopped"
        eval_status = agent_eval_status.get(agent_id)
        status = eval_status or status
        runtime_state = agent_runtime_states.get(agent_id)
        desired_state = _read_agent_desired_state(coral_dir, agent_id)
        if runtime_state is not None and runtime_state.state in {"paused", "stopped"}:
            status = runtime_state.state
        elif runtime_state is not None and runtime_state.state == "heartbeat" and eval_status is None:
            status = "heartbeat"
            status_since = runtime_state.state_started_at or status_since

        agent_attempts = [a for a in attempts if a.agent_id == agent_id]
        agent_scored = [a for a in agent_attempts if a.score is not None]
        agent_best = best_fn(agent_scored, key=lambda a: a.score or 0.0) if agent_scored else None

        agents_status.append(
            {
                "agent_id": agent_id,
                "island_id": island_id,
                "status": status,
                "sessions": len(logs),
                "last_activity": latest["modified"] if latest is not None else None,
                "last_activity_age_seconds": max(0.0, age) if age is not None else None,
                "active_seconds": active_seconds,
                "status_duration_seconds": max(0.0, now - status_since)
                if status_since is not None
                else None,
                "attempts": len(agent_attempts),
                "best_score": agent_best.score if agent_best else None,
                "runtime_state": runtime_state.state if runtime_state else None,
                "desired_state": desired_state,
                "usage": usage_by_agent.get(agent_id, _new_usage_summary()),
            }
        )

    return JSONResponse(
        {
            "manager_alive": manager_alive,
            "manager_pid": manager_pid,
            "eval_count": eval_count,
            "total_attempts": len(attempts),
            "scored_attempts": len(scored),
            "crashed_attempts": len([a for a in attempts if a.status == "crashed"]),
            "best_score": best.score if best else None,
            "best_title": best.title if best else None,
            "run_state": _run_state(coral_dir, manager_alive=manager_alive),
            "usage": run_usage,
            "agents": agents_status,
        }
    )
