"""List the unified run knowledge base."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coral.hub._island import all_view_roots


def list_knowledge_sources(coral_dir: str | Path) -> list[dict[str, Any]]:
    """Return manifest entries and source files from every visible knowledge base."""
    coral_dir = Path(coral_dir)
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()

    for view_root in _knowledge_view_roots(coral_dir):
        island_id = view_root.name if view_root.parent.name == "islands" else None
        knowledge_dir = view_root / "knowledge"
        if not knowledge_dir.is_dir():
            continue

        for entry in _read_manifest_entries(knowledge_dir, island_id):
            key = (island_id, entry.get("relative_path") or entry.get("path") or entry.get("title", ""))
            seen.add((key[0], str(key[1])))
            entries.append(entry)

        for entry in _scan_source_files(knowledge_dir, island_id):
            key = (island_id, entry["relative_path"])
            if key in seen:
                continue
            entries.append(entry)

    entries.sort(key=lambda e: (e.get("category", ""), e.get("relative_path", ""), e.get("title", "")))
    return entries


def add_review_note(
    coral_dir: str | Path,
    *,
    title: str,
    body: str,
    category: str = "synthesis",
    creator: str = "user",
) -> dict[str, Any]:
    """Write a human/Codex review note into the run-global knowledge base."""
    title = title.strip()
    body = body.strip()
    if not title:
        raise ValueError("title is required")
    if not body:
        raise ValueError("body is required")

    knowledge_dir = Path(coral_dir) / "public" / "knowledge"
    from coral.workspace.project import _ensure_knowledge_base, _link_legacy_notes_dir

    _ensure_knowledge_base(knowledge_dir)
    _link_legacy_notes_dir(knowledge_dir.parent)
    notes_dir = knowledge_dir / "notes" / _safe_segment(category, default="synthesis")
    notes_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC).isoformat()
    filename = f"{created[:19].replace(':', '').replace('-', '').replace('T', '-')}-{_slugify(title)}.md"
    path = notes_dir / filename
    path.write_text(
        "---\n"
        f"creator: {creator}\n"
        f"created: {created}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
    return {
        "ok": True,
        "title": title,
        "path": str(path),
        "relative_path": path.relative_to(knowledge_dir / "notes").as_posix(),
        "created": created,
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
    """Append a proposed reference source to the run-global knowledge manifest."""
    title = title.strip()
    url = url.strip()
    note = note.strip()
    if not title:
        raise ValueError("title is required")
    if not url and not note:
        raise ValueError("url or note is required")

    knowledge_dir = Path(coral_dir) / "public" / "knowledge"
    from coral.workspace.project import _ensure_knowledge_base, _link_legacy_notes_dir

    _ensure_knowledge_base(knowledge_dir)
    _link_legacy_notes_dir(knowledge_dir.parent)
    manifest = knowledge_dir / "manifest.jsonl"
    entry: dict[str, Any] = {
        "title": title,
        "relative_path": f"inbox/{_slugify(title)}",
        "category": _safe_segment(category, default="web"),
        "source": "manifest",
        "status": "proposed",
        "added_by": added_by,
        "added_at": datetime.now(UTC).isoformat(),
    }
    if url:
        entry["origin_url"] = url
    if note:
        entry["note"] = note
    inbox_dir = knowledge_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    stub_path = inbox_dir / f"{_slugify(title)}.md"
    entry["relative_path"] = stub_path.relative_to(knowledge_dir).as_posix()
    stub_path.write_text(
        "---\n"
        f"title: {title}\n"
        f"category: {entry['category']}\n"
        f"status: {entry['status']}\n"
        f"added_by: {added_by}\n"
        f"added_at: {entry['added_at']}\n"
        "---\n\n"
        f"# {title}\n\n"
        + (f"Source: {url}\n\n" if url else "")
        + (f"{note}\n" if note else ""),
        encoding="utf-8",
    )
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True, "entry": entry, "path": str(manifest)}


def read_eval_spec(coral_dir: str | Path) -> dict[str, Any]:
    """Read the run-global eval design spec from the unified knowledge base."""
    knowledge_dir = Path(coral_dir) / "public" / "knowledge"
    path = knowledge_dir / "eval_spec.md"
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
    """Write the run-global eval design spec into the unified knowledge base."""
    coral_dir = Path(coral_dir)
    knowledge_dir = Path(coral_dir) / "public" / "knowledge"
    from coral.workspace.project import _ensure_knowledge_base, _link_legacy_notes_dir

    _ensure_knowledge_base(knowledge_dir)
    _link_legacy_notes_dir(knowledge_dir.parent)
    path = knowledge_dir / "eval_spec.md"
    if _has_attempts(coral_dir):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if content != existing:
            raise ValueError(
                "eval_spec.md is frozen after attempts exist; fork a new timestamp or "
                "bump grader.eval_version and re-run candidates under the revised eval"
            )
    updated_at = datetime.now(UTC).isoformat()
    text = content
    path.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "content": text,
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
    """Update one run-global manifest entry's review status."""
    normalized_status = _safe_segment(status, default="")
    if normalized_status not in {"accepted", "rejected", "archived", "proposed"}:
        raise ValueError("status must be accepted, rejected, archived, or proposed")
    selector_keys = ("id", "relative_path", "title", "origin_url", "url")
    if not selector or not any(str(selector.get(key) or "").strip() for key in selector_keys):
        raise ValueError("source selector is required")

    knowledge_dir = Path(coral_dir) / "public" / "knowledge"
    manifest = knowledge_dir / "manifest.jsonl"
    if not manifest.exists():
        raise ValueError("knowledge manifest does not exist")

    entries = _read_raw_manifest(manifest)
    updated: dict[str, Any] | None = None
    reviewed_at = datetime.now(UTC).isoformat()
    for entry in entries:
        if not isinstance(entry, dict) or not _matches_source_selector(entry, selector):
            continue
        entry["status"] = normalized_status
        entry["reviewed_by"] = reviewer
        entry["reviewed_at"] = reviewed_at
        updated = dict(entry)
        break

    if updated is None:
        raise ValueError("source entry not found")

    tmp = manifest.with_name(f".{manifest.name}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    tmp.replace(manifest)
    return {"ok": True, "entry": updated, "path": str(manifest)}


def _has_attempts(coral_dir: Path) -> bool:
    """Return True once a timestamp has produced or recorded attempts."""
    attempt_roots = [coral_dir / "public" / "attempts"]
    attempt_roots.extend(coral_dir.glob("islands/*/attempts"))
    return any(root.is_dir() and any(root.glob("*.json")) for root in attempt_roots)


def _knowledge_view_roots(coral_dir: Path) -> list[Path]:
    public = coral_dir / "public"
    roots = all_view_roots(coral_dir)
    if public not in roots and (public / "knowledge").exists():
        return [public, *roots]
    return roots


def _read_manifest_entries(knowledge_dir: Path, island_id: str | None) -> list[dict[str, Any]]:
    manifest = knowledge_dir / "manifest.jsonl"
    if not manifest.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line_number, data in enumerate(_read_raw_manifest(manifest), start=1):
        if not isinstance(data, dict):
            continue
        entry = dict(data)
        rel = str(entry.get("relative_path") or entry.get("path") or "")
        if entry.get("status") == "invalid":
            entry.setdefault("title", f"Invalid manifest line {line_number}")
        else:
            entry.setdefault("title", Path(rel).name if rel else f"Manifest entry {line_number}")
        entry.setdefault("relative_path", rel)
        entry.setdefault("category", _category_for_relative_path(rel))
        entry.setdefault("source", "manifest")
        if island_id is not None:
            entry["island_id"] = island_id
        entries.append(entry)
    return entries


def _read_raw_manifest(manifest: Path) -> list[Any]:
    entries: list[Any] = []
    for line_number, line in enumerate(manifest.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append(
                {
                    "title": f"Invalid manifest line {line_number}",
                    "relative_path": "manifest.jsonl",
                    "status": "invalid",
                    "source": "manifest",
                }
            )
    return entries


def _matches_source_selector(entry: dict[str, Any], selector: dict[str, Any]) -> bool:
    for key in ("id", "relative_path", "title", "origin_url", "url"):
        expected = selector.get(key)
        if expected is None or str(expected).strip() == "":
            continue
        actual = entry.get(key)
        if key == "url" and actual is None:
            actual = entry.get("origin_url")
        if str(actual or "") != str(expected):
            return False
    return True


def _scan_source_files(knowledge_dir: Path, island_id: str | None) -> list[dict[str, Any]]:
    sources_dir = knowledge_dir / "sources"
    if not sources_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for path in sorted(p for p in sources_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(knowledge_dir).as_posix()
        stat = path.stat()
        entry: dict[str, Any] = {
            "title": path.name,
            "relative_path": rel,
            "category": _category_for_relative_path(rel),
            "source": "filesystem",
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        }
        if island_id is not None:
            entry["island_id"] = island_id
        entries.append(entry)
    return entries


def _category_for_relative_path(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if len(parts) >= 2 and parts[0] == "sources":
        return parts[1]
    if len(parts) >= 1 and parts[0] == "inbox":
        return "raw"
    return "other"


def _safe_segment(value: str, *, default: str) -> str:
    cleaned = _slugify(value)
    return cleaned or default


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "item"
