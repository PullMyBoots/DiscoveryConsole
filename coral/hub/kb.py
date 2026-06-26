"""Simple index-first knowledge base for CORAL runs.

The public knowledge model has three durable spaces:

- manuals: framework/task instructions, read-only for agents in normal loops
- external: externally obtained material, appended and archived through CLI
- practice: eval-linked agent chains, written by reflect_loop/archive

Agents should use ``coral kb index ...`` before ``coral kb read <id>``.  The
filesystem remains markdown/jsonl for auditability, but direct browsing is not
the primary interface.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coral.hub.attempts import read_attempt, read_attempts
from coral.types import BUDGET_CLASS_REAL, Attempt

SOURCE_KINDS = {"paper", "repo", "web", "doc", "dataset", "other"}


@dataclass(frozen=True)
class KnowledgePaths:
    root: Path
    manuals: Path
    external: Path
    external_items: Path
    external_index: Path
    practice: Path
    practice_agents: Path


def knowledge_paths(coral_dir: str | Path) -> KnowledgePaths:
    root = Path(coral_dir) / "public" / "knowledge"
    return KnowledgePaths(
        root=root,
        manuals=root / "manuals",
        external=root / "external",
        external_items=root / "external" / "items",
        external_index=root / "external" / "index.jsonl",
        practice=root / "practice",
        practice_agents=root / "practice" / "agents",
    )


def ensure_kb(coral_dir: str | Path) -> KnowledgePaths:
    paths = knowledge_paths(coral_dir)
    for directory in (
        paths.root,
        paths.manuals,
        paths.external,
        paths.external_items,
        paths.practice,
        paths.practice_agents,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if not paths.external_index.exists():
        paths.external_index.write_text("", encoding="utf-8")
    _ensure_manuals(paths)
    index = paths.root / "index.md"
    if not index.exists():
        index.write_text(
            "# Knowledge Directory\n\n"
            "This directory is an index-first knowledge base. Do not read it as a normal flat folder.\n\n"
            "## Start Here\n"
            "- `eval_spec.md`: the scoring contract and safety rules.\n"
            "- `manuals/`: short framework manuals.\n"
            "- `briefs/agent-seeds/`: Codex-generated starting plan and first eval script for each agent.\n\n"
            "## Two Knowledge Types\n"
            "- External knowledge: papers, repos, docs, datasets, and web references. Indexed by `external/index.jsonl` and stored under `external/items/`.\n"
            "- Practice knowledge: eval-linked notes, routes, score curves, and reflections under `practice/agents/`.\n\n"
            "## Optional Launch Bundles\n"
            "`briefs/agent-seeds/` contains Codex-prepared starting routes and first-eval scripts. It is launch scaffolding, not a third knowledge store.\n\n"
            "## Before And After Launch\n"
            "- Before `coral start`: read these files directly; Codex should fill in `eval_spec.md`, external references, and agent seeds.\n"
            "- After `coral start`: use `coral kb ...` inside the active run/timestamp.\n\n"
            "## Use These Commands After Launch\n"
            "- `coral kb index manual`: show manuals.\n"
            "- `coral kb index external`: show external references.\n"
            "- `coral kb index practice --by score|route|agent|metric`: show run experience by the view you need.\n"
            "- `coral kb read <id>`: open one indexed item.\n"
            "- `coral kb add external <path-or-url> --kind <kind> --title \"...\"`: add a reference.\n"
            "- `coral kb remove <id>`: remove one indexed reference.\n",
            encoding="utf-8",
        )
    return paths


def index_manuals(coral_dir: str | Path) -> list[dict[str, Any]]:
    paths = ensure_kb(coral_dir)
    entries = []
    for path in sorted(paths.manuals.glob("*.md")):
        manual_id = f"manual-{path.stem}"
        title = _first_heading(path) or path.stem.replace("-", " ").title()
        entries.append(
            {
                "id": manual_id,
                "space": "manual",
                "title": title,
                "relative_path": path.relative_to(paths.root).as_posix(),
            }
        )
    return entries


def add_external_source(
    coral_dir: str | Path,
    *,
    source: str,
    kind: str,
    title: str,
    summary: str = "",
    tags: list[str] | None = None,
    added_by: str = "agent",
) -> dict[str, Any]:
    paths = ensure_kb(coral_dir)
    title = title.strip()
    source = source.strip()
    kind = _safe_kind(kind)
    if not title:
        raise ValueError("title is required")
    if not source:
        raise ValueError("source path or URL is required")

    entries = _read_external_entries(paths)
    source_id = _next_source_id(entries)
    item_dir = paths.external_items / source_id
    item_dir.mkdir(parents=True, exist_ok=False)

    created_at = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "id": source_id,
        "space": "external",
        "kind": kind,
        "title": title,
        "summary": summary.strip(),
        "tags": tags or [],
        "status": "active",
        "source": source,
        "item_path": item_dir.relative_to(paths.root).as_posix(),
        "added_by": added_by,
        "added_at": created_at,
    }

    source_path = Path(source).expanduser()
    if _looks_like_url(source):
        (item_dir / "source.md").write_text(
            _external_markdown(payload, body=f"URL: {source}\n"),
            encoding="utf-8",
        )
    elif source_path.exists():
        files_dir = item_dir / "files"
        files_dir.mkdir()
        if source_path.is_dir():
            shutil.copytree(source_path, files_dir / source_path.name)
        else:
            shutil.copy2(source_path, files_dir / source_path.name)
        payload["local_copy"] = (files_dir / source_path.name).relative_to(paths.root).as_posix()
        (item_dir / "source.md").write_text(_external_markdown(payload), encoding="utf-8")
    else:
        raise ValueError(f"source path does not exist and is not a URL: {source}")

    _append_external_entry(paths, payload)
    return payload


def remove_external_source(
    coral_dir: str | Path,
    source_id: str,
    *,
    removed_by: str = "agent",
) -> dict[str, Any]:
    paths = ensure_kb(coral_dir)
    entries = _read_external_entries(paths, include_archived=True)
    normalized = source_id.strip()
    updated: dict[str, Any] | None = None
    for entry in entries:
        if entry.get("id") == normalized:
            entry["status"] = "archived"
            entry["removed_by"] = removed_by
            entry["removed_at"] = datetime.now(UTC).isoformat()
            updated = dict(entry)
            break
    if updated is None:
        raise ValueError(f"external source not found: {source_id}")
    _write_external_entries(paths, entries)
    return updated


def index_external(coral_dir: str | Path, *, include_archived: bool = False) -> list[dict[str, Any]]:
    paths = ensure_kb(coral_dir)
    entries = _read_external_entries(paths, include_archived=include_archived)
    entries.sort(key=lambda e: (e.get("kind", ""), e.get("id", "")))
    return entries


def read_item(coral_dir: str | Path, item_id: str) -> str:
    item_id = item_id.strip()
    if item_id.startswith("manual-"):
        return _read_manual(coral_dir, item_id)
    if item_id.startswith("src-"):
        return _read_external(coral_dir, item_id)
    if item_id.startswith("node-"):
        return _read_practice_node(coral_dir, item_id)
    if item_id.startswith("route-"):
        return _read_route(coral_dir, item_id)
    raise ValueError(f"unknown knowledge id: {item_id}")


def notebook_path(coral_dir: str | Path, agent_id: str) -> Path:
    paths = ensure_kb(coral_dir)
    agent_dir = paths.practice_agents / _safe_segment(agent_id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / "notebook.md"
    if not path.exists():
        path.write_text(
            f"# Notebook: {agent_id}\n\n"
            "## Current Plan\n"
            "- Define the next concrete change.\n\n"
            "## Work Notes\n"
            "- Add short observations with `coral kb note \"...\"`.\n",
            encoding="utf-8",
        )
    return path


def read_notebook(coral_dir: str | Path, agent_id: str) -> str:
    return notebook_path(coral_dir, agent_id).read_text(encoding="utf-8")


def append_notebook_note(
    coral_dir: str | Path,
    agent_id: str,
    note: str,
    *,
    tag: str = "",
) -> Path:
    note = note.strip()
    if not note:
        raise ValueError("note is required")
    path = notebook_path(coral_dir, agent_id)
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    tag_text = f" [{_safe_segment(tag)}]" if tag else ""
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n- {timestamp}{tag_text}: {note}\n")
    return path


def archive_notebook(
    coral_dir: str | Path,
    agent_id: str,
    *,
    reason: str = "reset",
    actor: str = "agent",
) -> Path | None:
    paths = ensure_kb(coral_dir)
    agent_dir = paths.practice_agents / _safe_segment(agent_id)
    path = agent_dir / "notebook.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None

    archive_dir = agent_dir / "notebook_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    reason_slug = _safe_segment(reason or "reset")
    candidate = archive_dir / f"{stamp}-{reason_slug}.md"
    suffix = 2
    while candidate.exists():
        candidate = archive_dir / f"{stamp}-{reason_slug}-{suffix}.md"
        suffix += 1

    candidate.write_text(
        "---\n"
        f"agent_id: {agent_id}\n"
        f"reason: {reason}\n"
        f"actor: {actor}\n"
        f"archived_at: {created_at}\n"
        "---\n\n"
        f"{text.rstrip()}\n",
        encoding="utf-8",
    )
    return candidate


def reset_notebook(
    coral_dir: str | Path,
    agent_id: str,
    content: str,
    *,
    archive: bool = True,
    reason: str = "reset",
    actor: str = "agent",
) -> Path:
    paths = ensure_kb(coral_dir)
    agent_dir = paths.practice_agents / _safe_segment(agent_id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / "notebook.md"
    if archive:
        archive_notebook(coral_dir, agent_id, reason=reason, actor=actor)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def archive_practice_node(
    coral_dir: str | Path,
    *,
    agent_id: str,
    attempt_hash: str,
    method: str = "",
    reflection: str = "",
    route: str = "",
    next_plan: str = "",
) -> dict[str, Any]:
    paths = ensure_kb(coral_dir)
    attempt = _resolve_attempt(coral_dir, attempt_hash)
    if attempt is None:
        raise ValueError(f"attempt not found: {attempt_hash}")
    agent_id = agent_id or attempt.agent_id
    if attempt.agent_id != agent_id:
        raise ValueError(f"attempt {attempt_hash} belongs to {attempt.agent_id}, not {agent_id}")

    agent_dir = paths.practice_agents / _safe_segment(agent_id)
    chain_dir = agent_dir / "chain"
    chain_dir.mkdir(parents=True, exist_ok=True)
    notebook = notebook_path(coral_dir, agent_id)
    notebook_text = notebook.read_text(encoding="utf-8") if notebook.exists() else ""

    existing_nodes = sorted(chain_dir.glob("eval-*.md"))
    eval_index = len(existing_nodes) + 1
    node_id = f"node-{_safe_segment(agent_id)}-{eval_index:04d}"
    route = route.strip() or _infer_route(attempt, method)
    method = method.strip() or attempt.title
    reflection = reflection.strip() or "No reflect_loop note provided."
    created_at = datetime.now(UTC).isoformat()
    path = chain_dir / f"eval-{eval_index:04d}-{attempt.commit_hash[:12]}.md"
    path.write_text(
        _practice_markdown(
            node_id=node_id,
            agent_id=agent_id,
            eval_index=eval_index,
            attempt=attempt,
            route=route,
            method=method,
            reflection=reflection,
            notebook_text=notebook_text,
            created_at=created_at,
        ),
        encoding="utf-8",
    )

    if next_plan:
        reset_notebook(
            coral_dir,
            agent_id,
            f"# Notebook: {agent_id}\n\n## Current Plan\n{next_plan.rstrip()}\n\n## Work Notes\n",
            reason=f"reflect-loop-{attempt.commit_hash[:12]}",
            actor=agent_id,
        )
    return {
        "id": node_id,
        "space": "practice",
        "agent_id": agent_id,
        "eval_index": eval_index,
        "attempt": attempt.commit_hash,
        "commit": attempt.commit_hash,
        "score": attempt.score,
        "route": route,
        "path": str(path),
        "relative_path": path.relative_to(paths.root).as_posix(),
    }


def index_practice(
    coral_dir: str | Path,
    *,
    by: str = "score",
    metric: str | None = None,
    agent: str | None = None,
    direction: str = "maximize",
) -> list[dict[str, Any]]:
    attempts = [
        a
        for a in read_attempts(coral_dir)
        if a.status != "pending" and a.budget_class == BUDGET_CLASS_REAL
    ]
    if agent:
        attempts = [a for a in attempts if a.agent_id == agent]
    nodes = _practice_nodes(coral_dir)
    nodes_by_commit = {n.get("commit"): n for n in nodes}

    if by == "route":
        return _route_index(attempts, nodes_by_commit, direction=direction)
    if by == "agent":
        return _agent_index(attempts, direction=direction)
    if by == "metric":
        return _metric_index(attempts, nodes_by_commit, metric=metric, direction=direction)
    return _score_index(attempts, nodes_by_commit, direction=direction)


def _ensure_manuals(paths: KnowledgePaths) -> None:
    manuals = {
        "evaluation-spaces.md": (
            "# Evaluation Spaces\n\n"
            "- L1: open A-space scoring.\n"
            "- L2: open A-space exploration and hidden B-space iterative scoring.\n"
            "- L3: open A-space exploration, hidden B-space iteration, sealed C-space final.\n\n"
            "`coral run -- <command>` is for open A-space exploration. "
            "`coral eval -m \"...\"` submits official scoring. "
            "C-space is sealed for human/Codex final validation after the CORAL "
            "search loop; it is not part of the default agent eval loop.\n"
        ),
        "submit-system.md": (
            "# Submit System\n\n"
            "`coral eval` stages changes, creates a git commit, writes a pending attempt, "
            "waits for the grader by default, and records score feedback. "
            "Use `coral show <commit> --diff` to inspect a scored code change.\n"
        ),
        "knowledge-cli.md": (
            "# Knowledge CLI\n\n"
            "Use index-first lookup:\n\n"
            "- `coral kb index manual`\n"
            "- `coral kb index external`\n"
            "- `coral kb index practice --by score|route|agent|metric`\n"
            "- `coral kb read <id>`\n"
            "- `coral kb add external <path-or-url> --kind <kind> --title \"...\"`\n"
            "- `coral kb remove <src-id>`\n"
            "- `coral kb note \"...\"`\n"
            "- `coral kb archive --attempt <hash>`\n"
        ),
        "coral-overview-cli.md": (
            "# CORAL Overview And CLI\n\n"
            "CORAL is the lightweight runtime shell for this timestamp. Codex prepares "
            "the task workspace, eval contract, knowledge sources, baselines, and "
            "agent initialization bundles before launch. CORAL then controls agent "
            "processes, isolated worktrees, eval submission, compute jobs, knowledge "
            "lookup, and dashboard rendering.\n\n"
            "Core commands:\n\n"
            "- `coral eval -m \"...\"`: submit the current code for official scoring.\n"
            "- `coral eval --tune -m \"...\"`: submit cheaper exploratory scoring when supported.\n"
            "- `coral run -- <command>`: run an open A-space exploration job with tracked logs/artifacts.\n"
            "- `coral log` and `coral log --recent`: inspect scored attempts.\n"
            "- `coral show <hash> --diff`: inspect the code change behind an attempt.\n"
            "- `coral kb index manual|external|practice`: discover reusable knowledge.\n"
            "- `coral kb read <id>`: read one indexed knowledge item.\n"
            "- `coral kb note \"...\"`: add a short notebook observation.\n"
            "- `coral kb archive --attempt <hash>`: archive a real-eval lesson into practice knowledge.\n\n"
            "Do not read `.coral/private/`. It contains hidden grader assets and sealed data.\n"
        ),
        "agent-loops.md": (
            "# Agent Work And Reflect Loops\n\n"
            "Agents operate in two task loops.\n\n"
            "## work_loop\n\n"
            "Use work_loop to implement, diagnose, and explore candidate methods. Start "
            "from the Codex-prepared route for your agent when one exists. Before "
            "editing, read the eval spec and query relevant knowledge indexes. Make "
            "one coherent change, run local checks when useful, then submit with "
            "`coral eval -m \"...\"` when there is a concrete result to score.\n\n"
            "Useful work_loop actions:\n\n"
            "- Read `CORAL_OVERVIEW.md` for CLI orientation.\n"
            "- Read `CORAL_LOOPS.md` when deciding whether to work or archive.\n"
            "- Use `coral kb index practice --by score|route|agent|metric` before copying ideas.\n"
            "- Use `coral show <hash> --diff` to inspect interesting prior changes.\n"
            "- Use `coral run -- <command>` for open A-space exploration jobs.\n"
            "- Use `coral kb note \"...\"` for short observations that should survive context pressure.\n\n"
            "## reflect_loop\n\n"
            "After a successful real eval, reflect_loop turns the eval result into "
            "durable practice knowledge. Use the score report, ranking context, "
            "baseline delta, metric details, and git diff to decide what actually "
            "changed and whether the route should continue.\n\n"
            "A useful reflect_loop archive contains:\n\n"
            "- route name\n"
            "- method summary\n"
            "- why the score moved or did not move\n"
            "- guardrail or overfitting concerns\n"
            "- next work_loop plan\n\n"
            "Tune attempts are for cheaper exploration and do not automatically enter "
            "reflect_loop. Once tuning has a clear candidate, submit a normal real eval.\n"
        ),
    }
    for filename, content in manuals.items():
        path = paths.manuals / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _read_external_entries(
    paths: KnowledgePaths,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    if not paths.external_index.exists():
        return []
    entries = []
    for line in paths.external_index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not include_archived and entry.get("status") == "archived":
            continue
        entries.append(entry)
    return entries


def _append_external_entry(paths: KnowledgePaths, entry: dict[str, Any]) -> None:
    with paths.external_index.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _write_external_entries(paths: KnowledgePaths, entries: list[dict[str, Any]]) -> None:
    tmp = paths.external_index.with_name(".index.jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(paths.external_index)


def _next_source_id(entries: list[dict[str, Any]]) -> str:
    max_seen = 0
    for entry in entries:
        match = re.match(r"src-(\d+)$", str(entry.get("id", "")))
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"src-{max_seen + 1:03d}"


def _external_markdown(entry: dict[str, Any], body: str = "") -> str:
    return (
        "---\n"
        f"id: {entry['id']}\n"
        f"kind: {entry['kind']}\n"
        f"status: {entry['status']}\n"
        f"added_at: {entry['added_at']}\n"
        "---\n\n"
        f"# {entry['title']}\n\n"
        f"Summary: {entry.get('summary') or '(none)'}\n\n"
        f"Tags: {', '.join(entry.get('tags') or []) or '(none)'}\n\n"
        f"{body}"
    )


def _score_index(
    attempts: list[Attempt],
    nodes_by_commit: dict[str, dict[str, Any]],
    *,
    direction: str,
) -> list[dict[str, Any]]:
    descending = direction != "minimize"
    scored = [a for a in attempts if a.score is not None]
    scored.sort(key=lambda a: a.score if a.score is not None else 0.0, reverse=descending)
    rows = []
    prev_by_agent: dict[str, float] = {}
    chronological = sorted(scored, key=lambda a: a.timestamp)
    deltas: dict[str, float | None] = {}
    for attempt in chronological:
        prev = prev_by_agent.get(attempt.agent_id)
        deltas[attempt.commit_hash] = None if prev is None else float(attempt.score) - prev
        prev_by_agent[attempt.agent_id] = float(attempt.score)
    for rank, attempt in enumerate(scored, start=1):
        node = nodes_by_commit.get(attempt.commit_hash, {})
        rows.append(_attempt_row(attempt, node=node, rank=rank, delta=deltas.get(attempt.commit_hash)))
    return rows


def _metric_index(
    attempts: list[Attempt],
    nodes_by_commit: dict[str, dict[str, Any]],
    *,
    metric: str | None,
    direction: str,
) -> list[dict[str, Any]]:
    metric = metric or "total"
    rows = []
    for attempt in attempts:
        value = _metric_value(attempt, metric)
        if value is None:
            continue
        rows.append((_attempt_row(attempt, node=nodes_by_commit.get(attempt.commit_hash, {})), value))
    descending = direction != "minimize"
    rows.sort(key=lambda item: item[1], reverse=descending)
    output = []
    for rank, (row, value) in enumerate(rows, start=1):
        row["rank"] = rank
        row["metric"] = metric
        row["metric_value"] = value
        output.append(row)
    return output


def _route_index(
    attempts: list[Attempt],
    nodes_by_commit: dict[str, dict[str, Any]],
    *,
    direction: str,
) -> list[dict[str, Any]]:
    by_route: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        node = nodes_by_commit.get(attempt.commit_hash, {})
        route = str(node.get("route") or _infer_route(attempt, "") or "unlabeled")
        by_route.setdefault(route, []).append(attempt)
    rows = []
    for idx, (route, route_attempts) in enumerate(sorted(by_route.items()), start=1):
        scored = [a for a in route_attempts if a.score is not None]
        best = _best_attempt(scored, direction=direction) if scored else None
        rows.append(
            {
                "id": f"route-{idx:03d}",
                "space": "practice",
                "route": route,
                "agents": sorted({a.agent_id for a in route_attempts}),
                "attempts": len(route_attempts),
                "best_node": nodes_by_commit.get(best.commit_hash, {}).get("id") if best else "",
                "best_commit": best.commit_hash if best else "",
                "best_score": best.score if best else None,
                "summary": _route_summary(route_attempts, nodes_by_commit),
            }
        )
    rows.sort(key=lambda r: (r["best_score"] is None, r["best_score"] or 0.0), reverse=direction != "minimize")
    for idx, row in enumerate(rows, start=1):
        row["id"] = f"route-{idx:03d}"
    return rows


def _agent_index(attempts: list[Attempt], *, direction: str) -> list[dict[str, Any]]:
    rows = []
    for agent_id in sorted({a.agent_id for a in attempts}):
        agent_attempts = [a for a in attempts if a.agent_id == agent_id]
        scored = [a for a in agent_attempts if a.score is not None]
        best = _best_attempt(scored, direction=direction) if scored else None
        curve = [a.score for a in sorted(scored, key=lambda a: a.timestamp)]
        rows.append(
            {
                "id": f"agent-{_safe_segment(agent_id)}",
                "space": "practice",
                "agent_id": agent_id,
                "attempts": len(agent_attempts),
                "best_commit": best.commit_hash if best else "",
                "best_score": best.score if best else None,
                "curve": curve,
            }
        )
    return rows


def _attempt_row(
    attempt: Attempt,
    *,
    node: dict[str, Any] | None = None,
    rank: int | None = None,
    delta: float | None = None,
) -> dict[str, Any]:
    node = node or {}
    row = {
        "id": node.get("id") or f"node-{attempt.commit_hash[:12]}",
        "space": "practice",
        "rank": rank,
        "agent_id": attempt.agent_id,
        "commit": attempt.commit_hash,
        "score": attempt.score,
        "delta": delta,
        "status": attempt.status,
        "title": attempt.title,
        "route": node.get("route") or _infer_route(attempt, ""),
        "archived": bool(node),
    }
    return row


def _practice_nodes(coral_dir: str | Path) -> list[dict[str, Any]]:
    paths = ensure_kb(coral_dir)
    nodes = []
    for path in sorted(paths.practice_agents.glob("*/chain/eval-*.md")):
        meta = _frontmatter(path.read_text(encoding="utf-8"))
        if not meta:
            continue
        meta["relative_path"] = path.relative_to(paths.root).as_posix()
        nodes.append(meta)
    return nodes


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    meta: dict[str, Any] = {}
    for line in text[3:end].strip().splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = value.strip().strip('"')
    if "score" in meta:
        try:
            meta["score"] = float(meta["score"])
        except ValueError:
            pass
    return meta


def _resolve_attempt(coral_dir: str | Path, attempt_hash: str) -> Attempt | None:
    attempts = read_attempts(coral_dir)
    matches = [a for a in attempts if a.commit_hash.startswith(attempt_hash)]
    if len(matches) == 1:
        return matches[0]
    return read_attempt(coral_dir, attempt_hash)


def _read_manual(coral_dir: str | Path, item_id: str) -> str:
    paths = ensure_kb(coral_dir)
    name = item_id.removeprefix("manual-")
    path = paths.manuals / f"{name}.md"
    if not path.exists():
        raise ValueError(f"manual not found: {item_id}")
    return path.read_text(encoding="utf-8")


def _read_external(coral_dir: str | Path, item_id: str) -> str:
    paths = ensure_kb(coral_dir)
    entries = _read_external_entries(paths, include_archived=True)
    entry = next((e for e in entries if e.get("id") == item_id), None)
    if entry is None:
        raise ValueError(f"external source not found: {item_id}")
    source_md = paths.root / str(entry.get("item_path", "")) / "source.md"
    body = source_md.read_text(encoding="utf-8") if source_md.exists() else ""
    return (
        f"ID: {entry['id']}\n"
        f"Kind: {entry.get('kind')}\n"
        f"Status: {entry.get('status')}\n"
        f"Title: {entry.get('title')}\n"
        f"Summary: {entry.get('summary') or '(none)'}\n"
        f"Source: {entry.get('source')}\n"
        f"Path: {entry.get('item_path')}\n\n"
        f"{body}"
    )


def _read_practice_node(coral_dir: str | Path, item_id: str) -> str:
    paths = ensure_kb(coral_dir)
    for path in sorted(paths.practice_agents.glob("*/chain/eval-*.md")):
        text = path.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        if meta.get("id") == item_id or item_id == f"node-{meta.get('commit', '')[:12]}":
            return text
    # Fall back to attempt-only synthetic node.
    commit = item_id.removeprefix("node-")
    attempt = _resolve_attempt(coral_dir, commit)
    if attempt is None:
        raise ValueError(f"practice node not found: {item_id}")
    return _synthetic_attempt_text(attempt)


def _read_route(coral_dir: str | Path, item_id: str) -> str:
    routes = index_practice(coral_dir, by="route")
    route = next((r for r in routes if r.get("id") == item_id), None)
    if route is None:
        raise ValueError(f"route not found: {item_id}")
    lines = [
        f"# {route['id']} {route['route']}",
        "",
        f"Agents: {', '.join(route.get('agents') or [])}",
        f"Attempts: {route.get('attempts')}",
        f"Best score: {route.get('best_score')}",
        f"Best commit: {route.get('best_commit')}",
        f"Best node: {route.get('best_node') or '(not archived)'}",
        "",
        "Inspect code:",
        f"- `coral show {str(route.get('best_commit') or '')[:12]} --diff`",
    ]
    return "\n".join(lines)


def _practice_markdown(
    *,
    node_id: str,
    agent_id: str,
    eval_index: int,
    attempt: Attempt,
    route: str,
    method: str,
    reflection: str,
    notebook_text: str,
    created_at: str,
) -> str:
    report = attempt.metadata.get("eval_report") if isinstance(attempt.metadata, dict) else None
    report_text = json.dumps(report, indent=2, ensure_ascii=False) if report else attempt.feedback
    return (
        "---\n"
        f"id: {node_id}\n"
        "space: practice\n"
        f"agent_id: {agent_id}\n"
        f"eval_index: {eval_index}\n"
        f"commit: {attempt.commit_hash}\n"
        f"score: {attempt.score}\n"
        f"status: {attempt.status}\n"
        f"route: \"{route}\"\n"
        f"created_at: {created_at}\n"
        "---\n\n"
        f"# Eval {eval_index:04d}: {attempt.title}\n\n"
        f"- Agent: {agent_id}\n"
        f"- Commit: `{attempt.commit_hash}`\n"
        f"- Score: {attempt.score}\n"
        f"- Status: {attempt.status}\n"
        f"- Route: {route}\n\n"
        "## Code Pointer\n\n"
        f"- Inspect diff: `coral show {attempt.commit_hash[:12]} --diff`\n"
        f"- Checkout snapshot: `coral checkout {attempt.commit_hash[:12]}`\n\n"
        "## Method Summary\n\n"
        f"{method}\n\n"
        "## Eval Report\n\n"
        f"```json\n{report_text}\n```\n\n"
        "## Reflect Loop Note\n\n"
        f"{reflection}\n\n"
        "## Work Loop Notebook Snapshot\n\n"
        f"```md\n{notebook_text.rstrip()}\n```\n"
    )


def _synthetic_attempt_text(attempt: Attempt) -> str:
    return (
        f"# Attempt {attempt.commit_hash[:12]}\n\n"
        f"- Agent: {attempt.agent_id}\n"
        f"- Commit: `{attempt.commit_hash}`\n"
        f"- Score: {attempt.score}\n"
        f"- Status: {attempt.status}\n"
        f"- Title: {attempt.title}\n\n"
        "## Code Pointer\n\n"
        f"- Inspect diff: `coral show {attempt.commit_hash[:12]} --diff`\n"
        f"- Checkout snapshot: `coral checkout {attempt.commit_hash[:12]}`\n\n"
        "## Feedback\n\n"
        f"{attempt.feedback}\n"
    )


def _metric_value(attempt: Attempt, metric: str) -> float | None:
    if metric == "total":
        return attempt.score
    metadata = attempt.metadata or {}
    components = metadata.get("score_components")
    if isinstance(components, dict):
        value = components.get(metric)
        if isinstance(value, dict):
            raw = value.get("value")
        else:
            raw = value
        try:
            return None if raw is None else float(raw)
        except (TypeError, ValueError):
            return None
    report = metadata.get("eval_report")
    if isinstance(report, dict):
        metrics = report.get("metrics")
        if isinstance(metrics, dict) and metric in metrics:
            raw_metric = metrics[metric]
            raw = raw_metric.get("value") if isinstance(raw_metric, dict) else raw_metric
            try:
                return None if raw is None else float(raw)
            except (TypeError, ValueError):
                return None
    return None


def _best_attempt(attempts: list[Attempt], *, direction: str) -> Attempt:
    if direction == "minimize":
        return min(attempts, key=lambda a: a.score if a.score is not None else float("inf"))
    return max(attempts, key=lambda a: a.score if a.score is not None else float("-inf"))


def _route_summary(
    attempts: list[Attempt],
    nodes_by_commit: dict[str, dict[str, Any]],
) -> str:
    for attempt in reversed(sorted(attempts, key=lambda a: a.timestamp)):
        node = nodes_by_commit.get(attempt.commit_hash)
        if node and node.get("route"):
            return f"{node.get('route')} ({len(attempts)} attempt(s))"
    return attempts[-1].title if attempts else ""


def _infer_route(attempt: Attempt, method: str) -> str:
    metadata = attempt.metadata or {}
    for key in ("route", "method_route", "strategy"):
        value = metadata.get(key)
        if value:
            return str(value)
    text = method or attempt.title or "unlabeled"
    return text[:80]


def _first_heading(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return ""


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "git://", "ssh://"))


def _safe_kind(kind: str) -> str:
    cleaned = _safe_segment(kind)
    return cleaned if cleaned in SOURCE_KINDS else "other"


def _safe_segment(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9_.-]+", "-", value)
    value = value.strip("-._")
    return value or "item"
