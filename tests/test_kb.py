from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from coral.cli.kb import cmd_kb
from coral.hub.attempts import write_attempt
from coral.hub.kb import (
    add_external_source,
    append_notebook_note,
    archive_practice_node,
    index_external,
    index_manuals,
    index_practice,
    read_item,
    read_notebook,
    remove_external_source,
    reset_notebook,
)
from coral.types import Attempt


def _coral_dir(tmp_path: Path) -> Path:
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    return coral_dir


def _attempt(commit: str, agent: str, score: float, title: str) -> Attempt:
    return Attempt(
        commit_hash=commit,
        agent_id=agent,
        title=title,
        score=score,
        status="improved",
        parent_hash=None,
        timestamp=f"2026-06-26T00:00:0{score}Z",
        feedback="score report",
        metadata={
            "score_components": {"latency": {"value": 1.0 / score}},
            "eval_report": {"status": "success", "score": {"total": score}},
        },
    )


def test_kb_external_add_index_read_remove(tmp_path: Path):
    coral_dir = _coral_dir(tmp_path)
    source = tmp_path / "paper.md"
    source.write_text("# Paper\nUseful idea\n")

    entry = add_external_source(
        coral_dir,
        source=str(source),
        kind="paper",
        title="Useful Paper",
        summary="A compact summary",
        tags=["cache", "latency"],
        added_by="agent-1",
    )

    assert entry["id"] == "src-001"
    entries = index_external(coral_dir)
    assert [e["id"] for e in entries] == ["src-001"]
    assert "A compact summary" in read_item(coral_dir, "src-001")

    remove_external_source(coral_dir, "src-001", removed_by="agent-1")
    assert index_external(coral_dir) == []
    assert index_external(coral_dir, include_archived=True)[0]["status"] == "archived"


def test_kb_notebook_and_practice_archive(tmp_path: Path):
    coral_dir = _coral_dir(tmp_path)
    commit = "a" * 40
    write_attempt(coral_dir, _attempt(commit, "agent-1", 0.7, "cache-aware batching"))

    append_notebook_note(coral_dir, "agent-1", "cache removed repeated work", tag="cache")
    assert "cache removed repeated work" in read_notebook(coral_dir, "agent-1")

    node = archive_practice_node(
        coral_dir,
        agent_id="agent-1",
        attempt_hash=commit[:12],
        method="moved repeated preprocessing into cache",
        reflection="score jumped after cache reuse",
        route="cache-aware batching",
        next_plan="- tighten cache invalidation",
    )

    assert node["id"] == "node-agent-1-0001"
    text = read_item(coral_dir, node["id"])
    assert f"coral show {commit[:12]} --diff" in text
    assert "score jumped after cache reuse" in text
    assert "tighten cache invalidation" in read_notebook(coral_dir, "agent-1")
    archive_dir = coral_dir / "public" / "knowledge" / "practice" / "agents" / "agent-1" / "notebook_archive"
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == 1
    assert "cache removed repeated work" in archived[0].read_text()

    score_index = index_practice(coral_dir, by="score")
    assert score_index[0]["id"] == node["id"]
    assert score_index[0]["commit"] == commit

    route_index = index_practice(coral_dir, by="route")
    assert route_index[0]["route"] == "cache-aware batching"
    assert route_index[0]["best_node"] == node["id"]

    metric_index = index_practice(coral_dir, by="metric", metric="latency")
    assert metric_index[0]["metric"] == "latency"


def test_kb_manuals_seeded(tmp_path: Path):
    coral_dir = _coral_dir(tmp_path)
    manuals = index_manuals(coral_dir)
    ids = {m["id"] for m in manuals}
    assert "manual-evaluation-spaces" in ids
    assert "manual-submit-system" in ids
    assert "manual-knowledge-cli" in ids
    assert "manual-coral-overview-cli" in ids
    assert "manual-agent-loops" in ids


def test_reset_notebook_archives_previous_content(tmp_path: Path):
    coral_dir = _coral_dir(tmp_path)
    append_notebook_note(coral_dir, "agent-1", "old exploration note", tag="probe")

    path = reset_notebook(
        coral_dir,
        "agent-1",
        "# Notebook: agent-1\n\n## Current Plan\n- new plan\n",
        reason="external-adjustment",
        actor="codex",
    )

    assert "new plan" in path.read_text()
    archive_dir = path.parent / "notebook_archive"
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == 1
    archived_text = archived[0].read_text()
    assert "reason: external-adjustment" in archived_text
    assert "actor: codex" in archived_text
    assert "old exploration note" in archived_text


def test_kb_cli_index_and_read_use_breadcrumb(tmp_path: Path, monkeypatch, capsys):
    coral_dir = _coral_dir(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".coral_dir").write_text(str(coral_dir))
    monkeypatch.chdir(worktree)

    cmd_kb(SimpleNamespace(kb_action="index", space="manual", task=None, run=None))
    out = capsys.readouterr().out
    assert "manual-knowledge-cli" in out

    cmd_kb(SimpleNamespace(kb_action="read", id="manual-knowledge-cli", task=None, run=None))
    out = capsys.readouterr().out
    assert "Knowledge CLI" in out


def test_kb_cli_practice_index_defaults_to_grader_direction(tmp_path: Path, monkeypatch, capsys):
    coral_dir = _coral_dir(tmp_path)
    (coral_dir / "config.yaml").write_text(
        "task:\n  name: minimize-task\n  description: d\n"
        "grader:\n  entrypoint: pkg.grader:Grader\n  direction: minimize\n"
    )
    low = "1" * 40
    high = "9" * 40
    write_attempt(coral_dir, _attempt(high, "agent-1", 0.9, "high score"))
    write_attempt(coral_dir, _attempt(low, "agent-2", 0.1, "low score"))
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".coral_dir").write_text(str(coral_dir))
    monkeypatch.chdir(worktree)

    cmd_kb(
        SimpleNamespace(
            kb_action="index",
            space="practice",
            by="score",
            metric=None,
            agent=None,
            direction=None,
            task=None,
            run=None,
        )
    )
    out = capsys.readouterr().out

    assert out.index(low[:12]) < out.index(high[:12])


def test_kb_cli_notebook_set_archives_previous_content(tmp_path: Path, monkeypatch, capsys):
    coral_dir = _coral_dir(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".coral_dir").write_text(str(coral_dir))
    (worktree / ".coral_agent_id").write_text("agent-1")
    append_notebook_note(coral_dir, "agent-1", "old dashboard critique", tag="review")
    plan = worktree / "plan.md"
    plan.write_text("# Notebook: agent-1\n\n## Current Plan\n- follow external advice\n")
    monkeypatch.chdir(worktree)

    cmd_kb(
        SimpleNamespace(
            kb_action="notebook",
            agent=None,
            set=str(plan),
            reason="external-adjustment",
            by="codex",
            workdir=None,
            task=None,
            run=None,
        )
    )

    out = capsys.readouterr().out
    assert "Notebook reset:" in out
    assert "follow external advice" in read_notebook(coral_dir, "agent-1")
    archive_dir = (
        coral_dir
        / "public"
        / "knowledge"
        / "practice"
        / "agents"
        / "agent-1"
        / "notebook_archive"
    )
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == 1
    assert "old dashboard critique" in archived[0].read_text()


def test_kb_cli_archive_accepts_method_and_reflection_files(tmp_path: Path, monkeypatch, capsys):
    coral_dir = _coral_dir(tmp_path)
    commit = "d" * 40
    write_attempt(coral_dir, _attempt(commit, "agent-1", 0.8, "route title"))
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".coral_dir").write_text(str(coral_dir))
    (worktree / ".coral_agent_id").write_text("agent-1")
    method = worktree / "method.md"
    reflection = worktree / "reflection.md"
    plan = worktree / "plan.md"
    method.write_text("method from file")
    reflection.write_text("reflection from file")
    plan.write_text("- next plan from file")
    monkeypatch.chdir(worktree)

    cmd_kb(
        SimpleNamespace(
            kb_action="archive",
            attempt=commit[:12],
            agent=None,
            route="file route",
            method="",
            method_file=str(method),
            reflection="",
            reflection_file=str(reflection),
            next_plan=str(plan),
            workdir=None,
            task=None,
            run=None,
        )
    )
    out = capsys.readouterr().out
    assert "Archived node-agent-1-0001" in out
    text = read_item(coral_dir, "node-agent-1-0001")
    assert "method from file" in text
    assert "reflection from file" in text
