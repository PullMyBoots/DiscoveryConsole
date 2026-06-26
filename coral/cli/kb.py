"""Command: kb, the simple index-first knowledge interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from coral.cli._helpers import find_coral_dir, read_agent_id, read_direction


def cmd_kb(args: argparse.Namespace) -> None:
    action = getattr(args, "kb_action", None)
    if action == "index":
        _cmd_index(args)
    elif action == "read":
        _cmd_read(args)
    elif action == "add":
        _cmd_add(args)
    elif action == "remove":
        _cmd_remove(args)
    elif action == "note":
        _cmd_note(args)
    elif action == "notebook":
        _cmd_notebook(args)
    elif action == "archive":
        _cmd_archive(args)
    else:
        print("Error: missing kb action. Run `coral kb --help`.", file=sys.stderr)
        sys.exit(2)


def _coral_dir(args: argparse.Namespace) -> Path:
    return find_coral_dir(getattr(args, "task", None), getattr(args, "run", None))


def _agent_id(args: argparse.Namespace) -> str:
    return getattr(args, "agent", None) or read_agent_id(getattr(args, "workdir", None) or ".")


def _cmd_index(args: argparse.Namespace) -> None:
    from coral.hub.kb import index_external, index_manuals, index_practice

    coral_dir = _coral_dir(args)
    space = args.space
    if space == "manual":
        print(_format_manual_index(index_manuals(coral_dir)))
    elif space == "external":
        print(_format_external_index(index_external(coral_dir, include_archived=args.all)))
    elif space == "practice":
        print(
            _format_practice_index(
                index_practice(
                    coral_dir,
                    by=args.by,
                    metric=args.metric,
                    agent=args.agent,
                    direction=args.direction or read_direction(coral_dir),
                ),
                by=args.by,
            )
        )
    else:
        raise SystemExit(f"Unknown kb space: {space}")


def _cmd_read(args: argparse.Namespace) -> None:
    from coral.hub.kb import read_item

    try:
        print(read_item(_coral_dir(args), args.id))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_add(args: argparse.Namespace) -> None:
    from coral.hub.kb import add_external_source

    if args.space != "external":
        print("Error: only `coral kb add external ...` is supported.", file=sys.stderr)
        sys.exit(2)
    try:
        entry = add_external_source(
            _coral_dir(args),
            source=args.source,
            kind=args.kind,
            title=args.title,
            summary=args.summary or "",
            tags=_split_csv(args.tags),
            added_by=args.by or _safe_agent(args),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Added {entry['id']}: {entry['title']}")
    print(f"Read: coral kb read {entry['id']}")


def _cmd_remove(args: argparse.Namespace) -> None:
    from coral.hub.kb import remove_external_source

    try:
        entry = remove_external_source(_coral_dir(args), args.id, removed_by=args.by or _safe_agent(args))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Archived {entry['id']}: {entry.get('title', '')}")


def _cmd_note(args: argparse.Namespace) -> None:
    from coral.hub.kb import append_notebook_note

    agent_id = args.agent or _agent_id(args)
    try:
        path = append_notebook_note(_coral_dir(args), agent_id, args.text, tag=args.tag or "")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Notebook updated: {path}")


def _cmd_notebook(args: argparse.Namespace) -> None:
    from coral.hub.kb import read_notebook, reset_notebook

    agent_id = args.agent or _agent_id(args)
    coral_dir = _coral_dir(args)
    if args.set:
        content = Path(args.set).read_text(encoding="utf-8")
        path = reset_notebook(
            coral_dir,
            agent_id,
            content,
            reason=getattr(args, "reason", None) or "external-adjustment",
            actor=getattr(args, "by", None) or _safe_agent(args),
        )
        print(f"Notebook reset: {path}")
        return
    print(read_notebook(coral_dir, agent_id))


def _cmd_archive(args: argparse.Namespace) -> None:
    from coral.hub.kb import archive_practice_node

    agent_id = args.agent or _agent_id(args)
    next_plan = ""
    if args.next_plan:
        next_plan = Path(args.next_plan).read_text(encoding="utf-8")
    method = args.method or ""
    if getattr(args, "method_file", None):
        method = Path(args.method_file).read_text(encoding="utf-8")
    reflection = args.reflection or ""
    if getattr(args, "reflection_file", None):
        reflection = Path(args.reflection_file).read_text(encoding="utf-8")
    try:
        node = archive_practice_node(
            _coral_dir(args),
            agent_id=agent_id,
            attempt_hash=args.attempt,
            method=method,
            reflection=reflection,
            route=args.route or "",
            next_plan=next_plan,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Archived {node['id']}: {node['relative_path']}")
    print(f"Code: coral show {node['commit'][:12]} --diff")


def _format_manual_index(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No manuals."
    lines = ["Manual index:"]
    for entry in entries:
        lines.append(f"  {entry['id']:<32} {entry['title']}")
    lines.append("\nRead with: coral kb read <manual-id>")
    return "\n".join(lines)


def _format_external_index(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No external sources."
    lines = ["External source index:"]
    for entry in entries:
        tags = ", ".join(entry.get("tags") or [])
        status = entry.get("status", "active")
        summary = entry.get("summary") or "(no summary)"
        tag_text = f" [{tags}]" if tags else ""
        lines.append(f"  {entry['id']:<8} {entry.get('kind','other'):<8} {status:<8} {entry.get('title','')}{tag_text}")
        lines.append(f"           {summary}")
    lines.append("\nRead with: coral kb read <src-id>")
    return "\n".join(lines)


def _format_practice_index(entries: list[dict[str, Any]], *, by: str) -> str:
    if not entries:
        return "No practice knowledge yet."
    if by == "route":
        lines = ["Practice route index:"]
        for entry in entries:
            agents = ",".join(entry.get("agents") or [])
            best = entry.get("best_score")
            best_str = "n/a" if best is None else f"{best:.10f}"
            lines.append(
                f"  {entry['id']:<10} best={best_str:<14} attempts={entry.get('attempts',0):<3} agents={agents} route={entry.get('route','')}"
            )
            lines.append(f"             {entry.get('summary','')}")
        lines.append("\nRead route with: coral kb read <route-id>")
        return "\n".join(lines)
    if by == "agent":
        lines = ["Practice agent index:"]
        for entry in entries:
            curve = " -> ".join(_fmt_score(x) for x in entry.get("curve") or [])
            lines.append(
                f"  {entry['agent_id']:<12} attempts={entry.get('attempts',0):<3} best={_fmt_score(entry.get('best_score'))} commit={str(entry.get('best_commit') or '')[:12]}"
            )
            lines.append(f"             curve: {curve or '(none)'}")
        return "\n".join(lines)

    title = "Practice metric index:" if by == "metric" else "Practice score index:"
    lines = [title]
    for entry in entries:
        score = entry.get("metric_value") if by == "metric" else entry.get("score")
        delta = entry.get("delta")
        delta_text = "" if delta is None else f" delta={delta:+.6g}"
        archived = "archived" if entry.get("archived") else "attempt"
        lines.append(
            f"  {entry['id']:<22} {_fmt_score(score):>14}{delta_text:<18} {entry.get('agent_id',''):<10} {archived:<8} {entry.get('route') or entry.get('title','')}"
        )
        lines.append(f"                         commit={str(entry.get('commit') or '')[:12]} title={entry.get('title','')}")
    lines.append("\nRead with: coral kb read <node-id>; inspect code with `coral show <commit> --diff`.")
    return "\n".join(lines)


def _fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.10f}"
    except (TypeError, ValueError):
        return str(value)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _safe_agent(args: argparse.Namespace) -> str:
    try:
        return _agent_id(args)
    except Exception:
        return "user"
