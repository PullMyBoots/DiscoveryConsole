"""Compatibility API for the index-first knowledge base.

The durable knowledge model is:

- manuals: framework/task instructions
- external: static papers, repos, docs, datasets, and web references
- practice: eval-linked notes, routes, score curves, and reflections

This module keeps the web API function names stable while delegating storage to
``coral.hub.kb``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coral.hub.kb import (
    add_external_source,
    append_notebook_note,
    ensure_kb,
    index_external,
    knowledge_paths,
)


def list_knowledge_sources(coral_dir: str | Path) -> list[dict[str, Any]]:
    """Return external knowledge sources from ``external/index.jsonl``."""
    entries: list[dict[str, Any]] = []
    for entry in index_external(coral_dir, include_archived=True):
        item = dict(entry)
        item["category"] = item.get("kind") or "other"
        item["relative_path"] = item.get("item_path") or ""
        item["source"] = "external"
        entries.append(item)
    return entries


def add_review_note(
    coral_dir: str | Path,
    *,
    title: str,
    body: str,
    category: str = "synthesis",
    creator: str = "user",
) -> dict[str, Any]:
    """Append a review observation to the practice notebook."""
    title = title.strip()
    body = body.strip()
    if not title:
        raise ValueError("title is required")
    if not body:
        raise ValueError("body is required")

    text = f"{title}: {body}"
    path = append_notebook_note(coral_dir, "review", text, tag=category or creator)
    root = knowledge_paths(coral_dir).root
    return {
        "ok": True,
        "title": title,
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "created": datetime.now(UTC).isoformat(),
    }


def add_reference_source(
    coral_dir: str | Path,
    *,
    title: str,
    url: str = "",
    category: str = "web",
    note: str = "",
    added_by: str = "user",
) -> dict[str, Any]:
    """Add an external knowledge source through the new external index."""
    title = title.strip()
    source = url.strip()
    if not title:
        raise ValueError("title is required")
    if not source:
        raise ValueError("url or local path is required")

    entry = add_external_source(
        coral_dir,
        source=source,
        kind=category or "web",
        title=title,
        summary=note.strip(),
        added_by=added_by,
    )
    return {"ok": True, "entry": entry, "path": str(knowledge_paths(coral_dir).external_index)}


def read_eval_spec(coral_dir: str | Path) -> dict[str, Any]:
    """Read the run-global eval design spec."""
    paths = ensure_kb(coral_dir)
    path = paths.root / "eval_spec.md"
    if not path.exists():
        return {
            "content": "",
            "path": str(path),
            "exists": False,
        }
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    return {
        "content": path.read_text(encoding="utf-8"),
        "path": str(path),
        "exists": True,
        "updated_at": updated_at,
    }


def write_eval_spec(
    coral_dir: str | Path,
    *,
    content: str,
    writer: str = "user",
) -> dict[str, Any]:
    """Write the run-global eval design spec."""
    coral_dir = Path(coral_dir)
    paths = ensure_kb(coral_dir)
    path = paths.root / "eval_spec.md"
    if _has_attempts(coral_dir):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if content != existing:
            raise ValueError(
                "eval_spec.md is frozen after attempts exist; fork a new timestamp or "
                "bump grader.eval_version and re-run candidates under the revised eval"
            )
    updated_at = datetime.now(UTC).isoformat()
    path.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "content": content,
        "path": str(path),
        "exists": True,
        "updated_at": updated_at,
        "writer": writer,
    }


def update_reference_source_status(
    coral_dir: str | Path,
    *,
    selector: dict[str, Any],
    status: str,
    reviewer: str = "user",
) -> dict[str, Any]:
    """Update one external source status in ``external/index.jsonl``."""
    normalized_status = _normalize_status(status)
    selector_keys = ("id", "relative_path", "title", "origin_url", "url")
    if not selector or not any(str(selector.get(key) or "").strip() for key in selector_keys):
        raise ValueError("source selector is required")

    paths = ensure_kb(coral_dir)
    entries = _read_external_entries(paths.external_index)
    updated: dict[str, Any] | None = None
    reviewed_at = datetime.now(UTC).isoformat()
    for entry in entries:
        if not _matches_source_selector(entry, selector):
            continue
        entry["status"] = normalized_status
        entry["reviewed_by"] = reviewer
        entry["reviewed_at"] = reviewed_at
        updated = dict(entry)
        break

    if updated is None:
        raise ValueError("source entry not found")

    _write_external_entries(paths.external_index, entries)
    return {"ok": True, "entry": updated, "path": str(paths.external_index)}


def _has_attempts(coral_dir: Path) -> bool:
    attempt_roots = [coral_dir / "public" / "attempts"]
    return any(root.is_dir() and any(root.glob("*.json")) for root in attempt_roots)


def _normalize_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"accepted", "active", "proposed"}:
        return "active"
    if normalized in {"rejected", "archived"}:
        return "archived"
    raise ValueError("status must be active/accepted/proposed or archived/rejected")


def _read_external_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            entries.append(data)
    return entries


def _write_external_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    tmp = path.with_name(".index.jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def _matches_source_selector(entry: dict[str, Any], selector: dict[str, Any]) -> bool:
    aliases = {
        "relative_path": "item_path",
        "origin_url": "source",
        "url": "source",
    }
    for key in ("id", "relative_path", "title", "origin_url", "url"):
        expected = selector.get(key)
        if expected is None or str(expected).strip() == "":
            continue
        actual_key = aliases.get(key, key)
        if str(entry.get(actual_key) or "") != str(expected):
            return False
    return True
