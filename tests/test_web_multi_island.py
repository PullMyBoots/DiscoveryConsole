"""Web dashboard behavior for multi-island run layouts."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import yaml
from starlette.responses import JSONResponse

from coral.agent.state import AgentRuntimeState, AgentStateDocument, write_agent_state
from coral.hub.attempts import write_attempt
from coral.types import Attempt
from coral.web.api import (
    add_knowledge_note,
    add_knowledge_source,
    create_run,
    get_control_instruction,
    get_control_plan,
    get_control_readiness,
    get_evals,
    get_knowledge,
    get_knowledge_eval_spec,
    get_notes,
    get_review,
    get_skill_detail,
    get_status,
    prompt_agent,
    resume_agent,
    resume_control_run,
    save_control_config,
    save_control_instruction,
    save_knowledge_eval_spec,
    stop_agent,
    update_knowledge_source_status,
)
from coral.web.events import FileWatcher
from coral.web.logs import list_log_files


def _make_attempt(commit: str, agent: str, score: float = 0.5) -> Attempt:
    return Attempt(
        commit_hash=commit,
        agent_id=agent,
        title="attempt",
        score=score,
        status="improved",
        parent_hash=None,
        timestamp="2026-06-01T10:00:00Z",
    )


def _make_pending(commit: str, agent: str, timestamp: str, island_id: str | None = None) -> Attempt:
    metadata = {"island_id": island_id} if island_id is not None else {}
    return Attempt(
        commit_hash=commit,
        agent_id=agent,
        title=f"pending {commit}",
        score=None,
        status="pending",
        parent_hash=None,
        timestamp=timestamp,
        metadata=metadata,
    )


def _make_multi_island(coral_dir: Path) -> None:
    (coral_dir / "public").mkdir(parents=True)
    for island in ("0", "1"):
        for subdir in ("attempts", "logs", "notes", "skills"):
            (coral_dir / "islands" / island / subdir).mkdir(parents=True)


def _write_eval_spec(knowledge_dir: Path) -> None:
    (knowledge_dir / "eval_spec.md").write_text(
        "# Eval Spec\n\n"
        "## Breakthrough Metrics\n"
        "- Improve the target score.\n\n"
        "## Guardrail Metrics\n"
        "- Keep baseline safety above the floor.\n\n"
        "## Anti-Cheating and Overfitting Checks\n"
        "- Prevent leakage, cheating, and overfit behavior.\n"
    )


def _request(coral_dir: Path, **path_params):
    results_dir = path_params.pop("results_dir", coral_dir.parent.parent.parent)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(coral_dir=coral_dir, results_dir=results_dir)),
        path_params=path_params,
    )


def _json_request(coral_dir: Path, body: dict, **path_params):
    request = _request(coral_dir, **path_params)

    async def _json():
        return body

    request.json = _json
    return request


def test_list_log_files_aggregates_island_logs(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "islands" / "0" / "logs" / "0-agent-1.0.log").write_text("a")
    (coral_dir / "islands" / "1" / "logs" / "1-agent-1.0.log").write_text("b")

    logs = list_log_files(coral_dir)

    assert set(logs) == {"0-agent-1", "1-agent-1"}
    assert logs["0-agent-1"][0]["island_id"] == "0"
    assert logs["1-agent-1"][0]["island_id"] == "1"


async def test_status_uses_global_eval_count_and_island_logs(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "eval_count").write_text("7")
    (coral_dir / "islands" / "1" / "logs" / "1-agent-1.0.log").write_text("log")
    write_attempt(coral_dir, _make_attempt("abc", "1-agent-1"), island_id="1")

    response = await get_status(_request(coral_dir))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["eval_count"] == 7
    assert [a["agent_id"] for a in payload["agents"]] == ["1-agent-1"]
    assert payload["agents"][0]["island_id"] == "1"
    assert payload["run_state"]["status"] == "stopped"


async def test_status_marks_agents_waiting_and_evaluating(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "config.yaml").write_text(
        "task:\n"
        "  name: t\n"
        "  description: d\n"
        "grader:\n"
        "  parallel:\n"
        "    max_workers: 1\n"
    )
    (coral_dir / "islands" / "0" / "logs" / "0-agent-1.0.log").write_text("log")
    (coral_dir / "islands" / "0" / "logs" / "0-agent-2.0.log").write_text("log")
    write_attempt(
        coral_dir,
        _make_pending("aaa", "0-agent-1", "2026-06-01T10:00:00Z", island_id="0"),
        island_id="0",
    )
    write_attempt(
        coral_dir,
        _make_pending("bbb", "0-agent-2", "2026-06-01T10:01:00Z", island_id="0"),
        island_id="0",
    )

    response = await get_status(_request(coral_dir))

    assert response.status_code == 200
    agents = {a["agent_id"]: a["status"] for a in json.loads(response.body)["agents"]}
    assert agents["0-agent-1"] == "evaluating"
    assert agents["0-agent-2"] == "waiting"


async def test_status_returns_run_state_with_remaining_seconds(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "config.yaml").write_text(
        "task:\n"
        "  name: t\n"
        "  description: d\n"
        "run:\n"
        "  max_runtime_seconds: 600\n"
    )
    (coral_dir / "public" / "run_state.json").write_text(
        json.dumps(
            {
                "status": "running",
                "started_at": "2026-06-01T10:00:00+00:00",
                "deadline_at": "2999-01-01T00:00:00+00:00",
                "max_runtime_seconds": 600,
                "remaining_seconds": 1,
                "stopped_reason": None,
                "updated_at": "2026-06-01T10:00:00+00:00",
            }
        )
    )

    response = await get_status(_request(coral_dir))

    payload = json.loads(response.body)
    assert payload["run_state"]["status"] == "stopped"
    assert payload["run_state"]["max_runtime_seconds"] == 600
    assert payload["run_state"]["remaining_seconds"] > 0
    assert payload["run_state"]["elapsed_seconds"] == 0


async def test_status_ignores_stale_agent_pid_map_without_manager(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    (coral_dir / "public" / "agent_pids.json").write_text(
        json.dumps({"agent-1": 999999999})
    )
    (coral_dir / "config.yaml").write_text("task:\n  name: t\n  description: d\n")

    response = await get_status(_request(coral_dir))

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["manager_alive"] is False
    assert payload["run_state"]["status"] == "stopped"


async def test_status_aggregates_log_usage_and_cache_hit_rate(tmp_path):
    coral_dir = tmp_path / ".coral"
    log_dir = coral_dir / "public" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "agent-1.0.log").write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "working"}],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 30,
                        "cache_read_input_tokens": 70,
                    },
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "result",
                "result": "done",
                "total_cost_usd": 0.0123,
                "duration_ms": 1000,
                "duration_api_ms": 500,
                "num_turns": 1,
                "session_id": "session-1",
                "usage": {},
                "modelUsage": {},
            }
        )
        + "\n"
    )
    (log_dir / "agent-2.0.log").write_text(
        json.dumps(
            {
                "type": "turn",
                "usage": {"total_tokens": 50},
                "cost_usd": 0.01,
            }
        )
        + "\n"
    )

    response = await get_status(_request(coral_dir))
    payload = json.loads(response.body)
    agents = {agent["agent_id"]: agent for agent in payload["agents"]}

    assert payload["usage"]["input_tokens"] == 100
    assert payload["usage"]["output_tokens"] == 20
    assert payload["usage"]["cache_creation_tokens"] == 30
    assert payload["usage"]["cache_read_tokens"] == 70
    assert payload["usage"]["uncategorized_tokens"] == 50
    assert payload["usage"]["total_tokens"] == 270
    assert payload["usage"]["cache_hit_rate"] == 0.35
    assert payload["usage"]["total_cost_usd"] == 0.0223
    assert agents["agent-1"]["usage"]["total_tokens"] == 220
    assert agents["agent-2"]["usage"]["uncategorized_tokens"] == 50


async def test_skill_detail_finds_island_skill(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    skill_dir = coral_dir / "islands" / "1" / "skills" / "island-skill"
    skill_dir.mkdir()
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: island-skill\ndescription: Island scoped\n---\nBody\n"
    )

    response = await get_skill_detail(_request(coral_dir, name="island-skill"))

    assert response.status_code == 200
    assert json.loads(response.body)["metadata"]["name"] == "island-skill"


async def test_get_knowledge_lists_manifest_and_source_files(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "knowledge" / "sources" / "papers" / "paper-a").mkdir(
        parents=True
    )
    (coral_dir / "public" / "knowledge" / "sources" / "papers" / "paper-a" / "text.md").write_text(
        "# Paper A\n"
    )
    (coral_dir / "public" / "knowledge" / "manifest.jsonl").write_text(
        json.dumps(
            {
                "title": "Reference repo",
                "relative_path": "sources/repos/ref",
                "origin_url": "https://example.com/ref.git",
                "category": "repos",
                "added_by": "codex",
            }
        )
        + "\n"
    )

    response = await get_knowledge(_request(coral_dir))

    assert response.status_code == 200
    sources = json.loads(response.body)["sources"]
    paths = {source["relative_path"] for source in sources}
    assert "sources/repos/ref" in paths
    assert "sources/papers/paper-a/text.md" in paths
    repo = next(source for source in sources if source["relative_path"] == "sources/repos/ref")
    assert repo["source"] == "manifest"
    paper = next(
        source for source in sources if source["relative_path"] == "sources/papers/paper-a/text.md"
    )
    assert paper["category"] == "papers"
    assert paper["source"] == "filesystem"


async def test_add_knowledge_note_and_source_write_global_review_artifacts(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    note_response = await add_knowledge_note(
        _json_request(
            coral_dir,
            {
                "title": "Eval reliability review",
                "body": "Guardrail metric is too weak for small cases.",
                "category": "synthesis",
            },
        )
    )
    source_response = await add_knowledge_source(
        _json_request(
            coral_dir,
            {
                "title": "Reference method",
                "url": "https://example.com/method",
                "category": "papers",
                "note": "Compare against this method next.",
            },
        )
    )

    assert note_response.status_code == 200
    assert source_response.status_code == 200
    note_payload = json.loads(note_response.body)
    source_payload = json.loads(source_response.body)
    assert Path(note_payload["path"]).is_file()
    assert source_payload["entry"]["status"] == "proposed"

    notes_response = await get_notes(_request(coral_dir))
    notes = json.loads(notes_response.body)
    assert notes[0]["title"] == "Eval reliability review"
    assert notes[0]["category"] == "synthesis"

    knowledge_response = await get_knowledge(_request(coral_dir))
    sources = json.loads(knowledge_response.body)["sources"]
    assert any(source["title"] == "Reference method" for source in sources)


async def test_knowledge_eval_spec_roundtrip_updates_global_spec(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    missing_response = await get_knowledge_eval_spec(_request(coral_dir))
    missing = json.loads(missing_response.body)
    assert missing["exists"] is False
    assert missing["content"] == ""
    assert missing["path"].endswith("knowledge/eval_spec.md")

    content = (
        "# Eval Spec\n\n"
        "## Breakthrough Metrics\n"
        "- Improve score.\n\n"
        "## Guardrail Metrics\n"
        "- Preserve validity.\n\n"
        "## Anti-cheating Checks\n"
        "- Holdout split.\n\n"
        "## Scalar Score Formula\n"
        "- aggregate = score - penalty.\n\n"
    )
    save_response = await save_knowledge_eval_spec(
        _json_request(coral_dir, {"content": content})
    )
    assert save_response.status_code == 200
    saved = json.loads(save_response.body)
    assert saved["exists"] is True
    assert saved["content"] == content
    assert Path(saved["path"]).read_text() == content

    read_response = await get_knowledge_eval_spec(_request(coral_dir))
    read_back = json.loads(read_response.body)
    assert read_back["content"] == content
    assert read_back["updated_at"]


async def test_update_knowledge_source_status_reviews_global_manifest_entry(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    await add_knowledge_source(
        _json_request(
            coral_dir,
            {
                "title": "Candidate repo",
                "url": "https://example.com/repo.git",
                "category": "repos",
                "note": "Useful implementation reference.",
            },
        )
    )

    response = await update_knowledge_source_status(
        _json_request(
            coral_dir,
            {
                "selector": {
                    "relative_path": "inbox/candidate-repo",
                    "title": "Candidate repo",
                },
                "status": "accepted",
            },
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["entry"]["status"] == "accepted"
    assert payload["entry"]["reviewed_by"] == "user"

    knowledge_response = await get_knowledge(_request(coral_dir))
    sources = json.loads(knowledge_response.body)["sources"]
    source = next(source for source in sources if source["title"] == "Candidate repo")
    assert source["status"] == "accepted"
    assert source["reviewed_by"] == "user"

    review_response = await get_review(_request(coral_dir))
    review = json.loads(review_response.body)
    assert review["knowledge"]["sources_by_status"]["accepted"] == 1
    assert review["knowledge"]["proposed_sources"] == 0


async def test_public_knowledge_is_visible_in_multi_island_runs(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "public" / "knowledge" / "notes" / "synthesis").mkdir(parents=True)
    (coral_dir / "public" / "knowledge" / "manifest.jsonl").write_text(
        json.dumps(
            {
                "title": "Global source",
                "relative_path": "inbox/global-source",
                "category": "web",
            }
        )
        + "\n"
    )
    (coral_dir / "public" / "notes").symlink_to("knowledge/notes")
    (
        coral_dir / "public" / "knowledge" / "notes" / "synthesis" / "review.md"
    ).write_text(
        "---\ncreator: user\ncreated: 2026-06-01T10:00:00+00:00\n---\n\n# Global Review\n\nUse stricter eval.\n"
    )

    notes_response = await get_notes(_request(coral_dir))
    knowledge_response = await get_knowledge(_request(coral_dir))

    assert any(note["title"] == "Global Review" for note in json.loads(notes_response.body))
    assert any(
        source["title"] == "Global source"
        for source in json.loads(knowledge_response.body)["sources"]
    )


async def test_get_review_summarizes_attempts_eval_and_knowledge(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "knowledge").mkdir(parents=True)
    (coral_dir / "public" / "knowledge" / "manifest.jsonl").write_text(
        json.dumps(
            {
                "title": "Proposed paper",
                "relative_path": "inbox/proposed-paper",
                "category": "papers",
                "status": "proposed",
            }
        )
        + "\n"
    )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "review-task", "description": "d"},
                "grader": {
                    "entrypoint": "g:Grader",
                    "direction": "maximize",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                },
                "agents": {"count": 1},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="base",
            agent_id="baseline",
            title="baseline",
            score=0.4,
            status="baseline",
            parent_hash=None,
            timestamp="2026-06-01T10:00:00Z",
            metadata={"baseline": True, "eval_version": "eval_v1", "eval_profile": "quick"},
        ),
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="best",
            agent_id="agent-1",
            title="new method",
            score=0.7,
            status="improved",
            parent_hash="base",
            timestamp="2026-06-01T10:01:00Z",
            metadata={
                "eval_version": "eval_v1",
                "eval_profile": "quick",
                "score_components": {"quality": 0.8, "guardrail": 0.6},
            },
        ),
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="bad",
            agent_id="agent-1",
            title="broken method",
            score=None,
            status="crashed",
            parent_hash="best",
            timestamp="2026-06-01T10:02:00Z",
        ),
    )

    response = await get_review(_request(coral_dir))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["task"]["eval_version"] == "eval_v1"
    assert payload["task"]["eval_profile"] == "quick"
    assert payload["attempts"]["total"] == 3
    assert payload["attempts"]["best"]["commit_hash"] == "best"
    assert payload["attempts"]["best_baseline"]["commit_hash"] == "base"
    assert payload["attempts"]["improvement_over_baseline"] == 0.29999999999999993
    assert payload["attempts"]["crashed"] == 1
    assert payload["knowledge"]["proposed_sources"] == 1
    assert any(flag["label"] == "Failed evals need triage" for flag in payload["flags"])
    assert any(flag["label"] == "Proposed sources to process" for flag in payload["flags"])


async def test_get_review_includes_public_baseline_for_multi_island_run(tmp_path):
    coral_dir = tmp_path / ".coral"
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    for island_id in ("0", "1"):
        knowledge_dir = coral_dir / "islands" / island_id / "knowledge"
        knowledge_dir.mkdir(parents=True)
        (knowledge_dir / "manifest.jsonl").write_text("")
        (knowledge_dir / "eval_spec.md").write_text(
            "# Eval Spec\n\n"
            "## Breakthrough Metrics\nImprove score.\n\n"
            "## Guardrail Metrics\nKeep valid output.\n\n"
            "## Anti-cheating Checks\nAvoid leakage and overfit.\n"
        )
        (coral_dir / "islands" / island_id / "attempts").mkdir(parents=True)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "review-task", "description": "d"},
                "grader": {
                    "entrypoint": "g:Grader",
                    "direction": "maximize",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                },
                "agents": {"count": 2},
                "islands": {"count": 2},
            }
        )
    )
    (coral_dir / "public" / "attempts" / "baseline.json").write_text(
        json.dumps(
            Attempt(
                commit_hash="baseline",
                agent_id="baseline",
                title="baseline",
                score=0.4,
                status="baseline",
                parent_hash=None,
                timestamp="2026-06-01T10:00:00Z",
                metadata={"baseline": True, "eval_version": "eval_v1", "eval_profile": "quick"},
            ).to_dict()
        )
    )

    response = await get_review(_request(coral_dir))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["attempts"]["baseline"] == 1
    assert payload["attempts"]["best_baseline"]["commit_hash"] == "baseline"
    assert payload["eval_spec"]["exists"] is True
    assert "islands/0/knowledge/eval_spec.md" in payload["eval_spec"]["path"]


async def test_get_review_flags_eval_spec_changed_after_attempts(tmp_path):
    coral_dir = tmp_path / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "manifest.jsonl").write_text("")
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "review-task", "description": "d"},
                "grader": {
                    "entrypoint": "g:Grader",
                    "direction": "maximize",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                },
                "agents": {"count": 1},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="best",
            agent_id="agent-1",
            title="new method",
            score=0.7,
            status="improved",
            parent_hash=None,
            timestamp="2026-06-01T10:01:00Z",
            metadata={"eval_version": "eval_v1", "eval_profile": "quick"},
        ),
    )
    (knowledge_dir / "eval_spec.md").write_text(
        "# Eval Spec\n\n"
        "## Breakthrough Metrics\n"
        "Improve target.\n\n"
        "## Guardrail Metrics\n"
        "Keep floor.\n\n"
        "## Anti-cheating Checks\n"
        "Prevent leakage.\n"
    )

    response = await get_review(_request(coral_dir))

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["eval_spec"]["exists"] is True
    assert payload["eval_spec"]["modified_after_attempts"] is True
    assert any(flag["label"] == "Eval spec changed after scoring" for flag in payload["flags"])
    assert any("bumped eval_version" in action for action in payload["recommended_actions"])


async def test_control_readiness_reports_codex_prepared_workspace(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "sources" / "papers").mkdir(parents=True)
    (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
    _write_eval_spec(knowledge_dir)
    (knowledge_dir / "manifest.jsonl").write_text(
        json.dumps(
            {
                "title": "Paper A",
                "relative_path": "sources/papers/paper-a/text.md",
                "category": "papers",
            }
        )
        + "\n"
    )
    (knowledge_dir / "sources" / "papers" / "paper-a.md").write_text("# Paper A\n")
    for index in (1, 2):
        (knowledge_dir / "briefs" / "agent-seeds" / f"agent-{index}.md").write_text(
            f"# Agent {index}\n"
        )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "Solve the task."},
                "grader": {
                    "entrypoint": "my_task.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 2, "runtime": "codex"},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="baseline",
            agent_id="baseline",
            title="baseline",
            score=0.4,
            status="baseline",
            parent_hash=None,
            timestamp="2026-06-01T10:00:00Z",
            metadata={"baseline": True, "baseline_name": "seed"},
        ),
    )

    response = await get_control_readiness(_request(coral_dir))
    payload = json.loads(response.body)
    checks = {check["id"]: check for check in payload["checks"]}

    assert response.status_code == 200
    assert payload["status"] == "ready"
    assert checks["config"]["status"] == "ready"
    assert checks["grader"]["status"] == "ready"
    assert checks["eval"]["status"] == "ready"
    assert checks["eval_spec"]["status"] == "ready"
    assert checks["knowledge"]["count"] >= 1
    assert checks["baseline"]["status"] == "ready"
    assert checks["agent_briefs"]["detail"] == "2/2 agent seed brief(s)"


async def test_control_readiness_requires_eval_spec(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "manifest.jsonl").parent.mkdir(parents=True)
    (knowledge_dir / "manifest.jsonl").write_text("")
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "Solve the task."},
                "grader": {
                    "entrypoint": "my_task.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                },
                "agents": {"count": 1, "runtime": "codex"},
            }
        )
    )

    response = await get_control_readiness(_request(coral_dir))
    payload = json.loads(response.body)
    checks = {check["id"]: check for check in payload["checks"]}

    assert payload["status"] == "missing"
    assert checks["eval_spec"]["status"] == "missing"
    assert checks["eval_spec"]["path"].endswith("knowledge/eval_spec.md")


async def test_control_readiness_warns_on_incomplete_eval_spec(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "eval_spec.md").write_text("# Eval Spec\n\nBreakthrough only.\n")
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "Solve the task."},
                "grader": {
                    "entrypoint": "my_task.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                },
                "agents": {"count": 1, "runtime": "codex"},
            }
        )
    )

    response = await get_control_readiness(_request(coral_dir))
    payload = json.loads(response.body)
    checks = {check["id"]: check for check in payload["checks"]}

    assert checks["eval_spec"]["status"] == "warning"
    assert checks["eval_spec"]["count"] == 1


async def test_control_readiness_requires_island_themes_for_multi_island_workspace(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "sources" / "papers").mkdir(parents=True)
    (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
    _write_eval_spec(knowledge_dir)
    (knowledge_dir / "manifest.jsonl").write_text(
        json.dumps(
            {
                "title": "Paper A",
                "relative_path": "sources/papers/paper-a.md",
                "category": "papers",
            }
        )
        + "\n"
    )
    (knowledge_dir / "sources" / "papers" / "paper-a.md").write_text("# Paper A\n")
    for agent_id in ("0-agent-1", "1-agent-1"):
        (knowledge_dir / "briefs" / "agent-seeds" / f"{agent_id}.md").write_text(
            f"# {agent_id}\n"
        )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "Solve."},
                "grader": {
                    "entrypoint": "my_task.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                },
                "agents": {"count": 2, "runtime": "codex"},
                "islands": {"count": 2},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="baseline",
            agent_id="baseline",
            title="baseline",
            score=0.4,
            status="baseline",
            parent_hash=None,
            timestamp="2026-06-01T10:00:00Z",
            metadata={"baseline": True},
        ),
    )

    response = await get_control_readiness(_request(coral_dir))
    payload = json.loads(response.body)
    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["status"] == "missing"
    assert checks["island_themes"]["status"] == "missing"

    (knowledge_dir / "briefs" / "islands").mkdir(parents=True)
    (knowledge_dir / "briefs" / "islands" / "0.md").write_text("# Island 0\n")
    (knowledge_dir / "briefs" / "islands" / "1.md").write_text("# Island 1\n")

    response = await get_control_readiness(_request(coral_dir))
    payload = json.loads(response.body)
    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["status"] == "ready"
    assert checks["island_themes"]["status"] == "ready"
    assert checks["island_themes"]["detail"] == "2/2 island theme brief(s)"


async def test_control_plan_summarizes_generated_island_and_agent_briefs(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    briefs_dir = coral_dir / "public" / "knowledge" / "briefs"
    (briefs_dir / "agent-seeds").mkdir(parents=True)
    (briefs_dir / "islands").mkdir(parents=True)
    (briefs_dir / "islands" / "0.md").write_text(
        "# Sparse Search Island\n\nFocus on sparse representations and low-cost baselines.\n"
    )
    (briefs_dir / "islands" / "1.md").write_text(
        "# Robustness Island\n\nFocus on guardrails and anti-overfitting checks.\n"
    )
    (briefs_dir / "agent-seeds" / "0-agent-1.md").write_text(
        "# 0-agent-1 Sparse Baseline\n\nStart from a small sparse model and tune cautiously.\n"
    )
    (briefs_dir / "agent-seeds" / "1-agent-1.md").write_text(
        "# 1-agent-1 Guardrail Audit\n\nStress test candidate scoring and failure cases.\n"
    )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "d"},
                "grader": {"entrypoint": "g:Grader"},
                "agents": {"count": 2},
                "islands": {"count": 2},
            }
        )
    )

    response = await get_control_plan(_request(coral_dir))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["status"] == "ready"
    assert payload["planned_agents"] == 2
    assert payload["brief_count"] == 2
    assert payload["island_count"] == 2
    islands = {island["island_id"]: island for island in payload["islands"]}
    assert islands["0"]["theme"]["title"] == "Sparse Search Island"
    assert islands["1"]["theme"]["title"] == "Robustness Island"
    assert islands["0"]["agents"][0]["agent_id"] == "0-agent-1"
    assert "sparse model" in islands["0"]["agents"][0]["summary"]


async def test_control_plan_and_readiness_read_started_multi_island_knowledge(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    for island_id, title in (("0", "Sparse Search"), ("1", "Robustness")):
        knowledge_dir = coral_dir / "islands" / island_id / "knowledge"
        (knowledge_dir / "sources" / "papers").mkdir(parents=True)
        (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
        (knowledge_dir / "briefs" / "islands").mkdir(parents=True)
        _write_eval_spec(knowledge_dir)
        (knowledge_dir / "manifest.jsonl").write_text(
            json.dumps(
                {
                    "title": f"Paper {island_id}",
                    "relative_path": f"sources/papers/paper-{island_id}.md",
                    "category": "papers",
                }
            )
            + "\n"
        )
        (knowledge_dir / "sources" / "papers" / f"paper-{island_id}.md").write_text("# Paper\n")
        (knowledge_dir / "briefs" / "islands" / f"{island_id}.md").write_text(
            f"# {title} Island\n\nTheme for island {island_id}.\n"
        )
        (knowledge_dir / "briefs" / "agent-seeds" / f"{island_id}-agent-1.md").write_text(
            f"# {island_id}-agent-1\n\nStart from route {island_id}.\n"
        )
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "Solve."},
                "grader": {
                    "entrypoint": "my_task.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 2, "runtime": "codex"},
                "islands": {"count": 2},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="baseline",
            agent_id="baseline",
            title="baseline",
            score=0.4,
            status="baseline",
            parent_hash=None,
            timestamp="2026-06-01T10:00:00Z",
            metadata={"baseline": True},
        ),
        island_id="0",
    )

    plan_response = await get_control_plan(_request(coral_dir))
    plan = json.loads(plan_response.body)
    readiness_response = await get_control_readiness(_request(coral_dir))
    readiness = json.loads(readiness_response.body)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert plan["status"] == "ready"
    assert plan["brief_count"] == 2
    assert {island["island_id"] for island in plan["islands"]} >= {"0", "1"}
    assert readiness["status"] == "ready"
    assert checks["eval_spec"]["status"] == "ready"
    assert checks["knowledge"]["status"] == "ready"
    assert checks["agent_briefs"]["detail"] == "2/2 agent seed brief(s)"
    assert checks["island_themes"]["detail"] == "2/2 island theme brief(s)"


async def test_get_evals_marks_evaluating_and_waiting_with_progress(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "config.yaml").write_text(
        "task:\n"
        "  name: t\n"
        "  description: d\n"
        "grader:\n"
        "  eval_version: eval_v2\n"
        "  profile: quick\n"
        "  resources:\n"
        "    cpu_cores: 8\n"
        "  parallel:\n"
        "    max_workers: 2\n"
    )
    for island in ("0", "1"):
        (coral_dir / "islands" / island / "eval_logs").mkdir(parents=True)

    write_attempt(
        coral_dir,
        _make_pending("aaa", "0-agent-1", "2026-06-01T10:00:00Z", island_id="0"),
        island_id="0",
    )
    write_attempt(
        coral_dir,
        _make_pending("bbb", "1-agent-1", "2026-06-01T10:01:00Z", island_id="1"),
        island_id="1",
    )
    write_attempt(
        coral_dir,
        _make_pending("ccc", "0-agent-2", "2026-06-01T10:02:00Z", island_id="0"),
        island_id="0",
    )
    progress = coral_dir / "islands" / "0" / "eval_logs" / "aaa" / "progress.jsonl"
    progress.parent.mkdir(parents=True)
    progress.write_text(
        '{"type":"progress","job_id":"aaa","current":5,"total":10,"percent":0.5,"phase":"evaluate","message":"half"}\n'
    )

    response = await get_evals(_request(coral_dir))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["max_workers"] == 2
    assert [job["commit_hash"] for job in payload["jobs"]] == ["aaa", "bbb", "ccc"]
    assert [job["queue_status"] for job in payload["jobs"]] == [
        "evaluating",
        "evaluating",
        "waiting",
    ]
    assert payload["jobs"][0]["progress"]["percent"] == 0.5
    assert payload["jobs"][0]["eval_version"] == "eval_v2"
    assert payload["jobs"][0]["eval_profile"] == "quick"
    assert payload["jobs"][0]["resources"]["cpu_cores"] == 8


async def test_get_evals_uses_resource_pool_for_evaluating_status(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "t", "description": "d"},
                "grader": {
                    "eval_version": "eval_v2",
                    "profile": "quick",
                    "resources": {"gpu_count": 1},
                    "parallel": {
                        "max_workers": 4,
                        "resources": {"gpu_count": 2, "gpu_ids": ["0", "1"]},
                    },
                },
            }
        )
    )
    for index, island in enumerate(("0", "1", "0")):
        write_attempt(
            coral_dir,
            _make_pending(
                f"gpu{index}",
                f"{island}-agent-{index + 1}",
                f"2026-06-01T10:0{index}:00Z",
                island_id=island,
            ),
            island_id=island,
        )

    response = await get_evals(_request(coral_dir))

    payload = json.loads(response.body)
    assert payload["max_workers"] == 4
    assert payload["resource_pool"]["gpu_ids"] == ["0", "1"]
    assert [job["commit_hash"] for job in payload["jobs"]] == ["gpu0", "gpu1", "gpu2"]
    assert [job["queue_status"] for job in payload["jobs"]] == [
        "evaluating",
        "evaluating",
        "waiting",
    ]


async def test_control_instruction_roundtrip(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    response = await save_control_instruction(
        _json_request(coral_dir, {"instruction": "Try the sparse baseline first."})
    )
    assert response.status_code == 200

    response = await get_control_instruction(_request(coral_dir))
    payload = json.loads(response.body)
    assert payload["instruction"] == "Try the sparse baseline first."
    assert payload["path"].endswith(".coral/public/control/next_instruction.md")


async def test_agent_control_writes_desired_state(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    response = await stop_agent(_request(coral_dir, id="agent-1"))
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["desired_state"] == "stopped"

    control_file = coral_dir / "public" / "control" / "agents" / "agent-1.json"
    saved = json.loads(control_file.read_text())
    assert saved["agent_id"] == "agent-1"
    assert saved["desired_state"] == "stopped"

    response = await resume_agent(_request(coral_dir, id="agent-1"))
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["desired_state"] == "running"
    assert json.loads(control_file.read_text())["desired_state"] == "running"


async def test_agent_prompt_writes_one_shot_control_command(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    response = await prompt_agent(
        _json_request(coral_dir, {"prompt": "Try a smaller batch size first."}, id="agent-1")
    )
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["action"] == "prompt"
    control_file = coral_dir / "public" / "control" / "agents" / "agent-1.json"
    saved = json.loads(control_file.read_text())
    assert saved["agent_id"] == "agent-1"
    assert saved["action"] == "prompt"
    assert saved["desired_state"] == "running"
    assert saved["prompt"] == "Try a smaller batch size first."
    assert saved["command_id"]


async def test_status_uses_agent_runtime_state(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    logs_dir = coral_dir / "public" / "logs"
    logs_dir.mkdir(parents=True)
    log_path = logs_dir / "agent-1.0.log"
    log_path.write_text("recent activity")
    os_pid = 999999999
    (coral_dir / "public" / "agent_pids.json").write_text(json.dumps({"agent-1": os_pid}))
    write_agent_state(
        coral_dir,
        AgentStateDocument(
            agents={"agent-1": AgentRuntimeState(state="stopped")},
        ),
    )
    control_file = coral_dir / "public" / "control" / "agents" / "agent-1.json"
    control_file.parent.mkdir(parents=True)
    control_file.write_text(json.dumps({"agent_id": "agent-1", "desired_state": "stopped"}))

    response = await get_status(_request(coral_dir))
    payload = json.loads(response.body)

    assert payload["agents"][0]["agent_id"] == "agent-1"
    assert payload["agents"][0]["status"] == "stopped"
    assert payload["agents"][0]["runtime_state"] == "stopped"
    assert payload["agents"][0]["desired_state"] == "stopped"
    assert payload["agents"][0]["active_seconds"] >= 0
    assert payload["agents"][0]["last_activity_age_seconds"] >= 0


async def test_status_exposes_heartbeat_runtime_state(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    logs_dir = coral_dir / "public" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-1.0.log").write_text("recent activity")
    started_at = time.time() - 12
    write_agent_state(
        coral_dir,
        AgentStateDocument(
            agents={
                "agent-1": AgentRuntimeState(
                    state="heartbeat",
                    state_started_at=started_at,
                    state_detail="reflect, pivot",
                )
            },
        ),
    )

    response = await get_status(_request(coral_dir))
    payload = json.loads(response.body)

    assert payload["agents"][0]["agent_id"] == "agent-1"
    assert payload["agents"][0]["status"] == "heartbeat"
    assert payload["agents"][0]["runtime_state"] == "heartbeat"
    assert payload["agents"][0]["status_duration_seconds"] >= 10


async def test_status_tolerates_non_object_agent_pid_map(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    logs_dir = coral_dir / "public" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-1.0.log").write_text("recent activity")
    (coral_dir / "public" / "agent_pids.json").write_text("999999999")

    response = await get_status(_request(coral_dir))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["agents"][0]["agent_id"] == "agent-1"


async def test_control_resume_passes_instruction_file(monkeypatch, tmp_path):
    results_dir = tmp_path / "results"
    coral_dir = results_dir / "my-task" / "run-1" / ".coral"
    instruction_path = coral_dir / "public" / "control" / "next_instruction.md"
    instruction_path.parent.mkdir(parents=True)
    instruction_path.write_text("Review failed attempts before coding.")
    (coral_dir.parent / "agents" / "agent-1").mkdir(parents=True)

    captured: dict[str, object] = {}

    class FakePopen:
        pid = 12345

        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    async def fake_readiness(_request):
        return JSONResponse({"status": "ready", "checks": []})

    monkeypatch.setattr("coral.web.api.subprocess.Popen", FakePopen)
    monkeypatch.setattr("coral.web.api.get_control_readiness", fake_readiness)

    response = await resume_control_run(
        _request(coral_dir, results_dir=results_dir)
    )

    payload = json.loads(response.body)
    assert payload["ok"] is True
    cmd = captured["cmd"]
    assert "--instruction-file" in cmd
    assert cmd[cmd.index("--instruction-file") + 1] == str(instruction_path)


async def test_control_resume_starts_fresh_timestamp_without_worktrees(monkeypatch, tmp_path):
    results_dir = tmp_path / "results"
    coral_dir = results_dir / "my-task" / "run-1" / ".coral"
    (coral_dir / "public" / "control").mkdir(parents=True)
    (coral_dir / "config.yaml").write_text("task:\n  name: my-task\n  description: d\n")

    captured: dict[str, object] = {}

    class FakePopen:
        pid = 12345

        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    async def fake_readiness(_request):
        return JSONResponse({"status": "ready", "checks": []})

    monkeypatch.setattr("coral.web.api.subprocess.Popen", FakePopen)
    monkeypatch.setattr("coral.web.api.get_control_readiness", fake_readiness)

    response = await resume_control_run(_request(coral_dir, results_dir=results_dir))

    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["message"] == "start requested"
    cmd = captured["cmd"]
    assert "start" in cmd
    assert "--config" in cmd
    assert cmd[cmd.index("--config") + 1] == str(coral_dir / "config.yaml")


async def test_control_resume_blocks_missing_readiness(tmp_path):
    results_dir = tmp_path / "results"
    coral_dir = results_dir / "my-task" / "run-1" / ".coral"
    (coral_dir / "public" / "control").mkdir(parents=True)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "d"},
                "grader": {"entrypoint": "g:Grader", "eval_version": "eval_v1", "profile": "quick"},
                "agents": {"count": 1, "runtime": "codex"},
            }
        )
    )

    response = await resume_control_run(_request(coral_dir, results_dir=results_dir))
    payload = json.loads(response.body)

    assert response.status_code == 409
    assert payload["ok"] is False
    assert payload["readiness"]["status"] == "missing"
    assert "Run blocked until Codex prepares" in payload["message"]


async def test_create_run_forks_prelaunch_state_without_runtime_artifacts(tmp_path):
    results_dir = tmp_path / "results"
    task_dir = results_dir / "my-task"
    run_dir = task_dir / "run-1"
    coral_dir = run_dir / ".coral"
    task_source = tmp_path / "task-source"
    task_source.mkdir()
    (task_source / "task.yaml").write_text("task:\n  name: my-task\n  description: d\n")
    (run_dir / "snapshots" / "knowledge").mkdir(parents=True)
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "sources" / "papers").mkdir(parents=True)
    (knowledge_dir / "sources" / "web").mkdir(parents=True)
    (knowledge_dir / "inbox").mkdir(parents=True)
    (knowledge_dir / "sources" / "papers" / "paper.md").write_text("# P\n")
    (knowledge_dir / "sources" / "papers" / "rejected.md").write_text("# R\n")
    (knowledge_dir / "inbox" / "candidate.md").write_text("# C\n")
    (knowledge_dir / "inbox" / "accepted.md").write_text("# A\n")
    (knowledge_dir / "manifest.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "title": "Accepted Paper",
                        "relative_path": "sources/papers/paper.md",
                        "status": "accepted",
                    }
                ),
                json.dumps(
                    {
                        "title": "Duplicate Rejected View",
                        "relative_path": "sources/papers/paper.md",
                        "status": "rejected",
                    }
                ),
                json.dumps(
                    {
                        "title": "Rejected Paper",
                        "relative_path": "sources/papers/rejected.md",
                        "status": "rejected",
                    }
                ),
                json.dumps(
                    {
                        "title": "Candidate",
                        "relative_path": "inbox/candidate.md",
                        "status": "proposed",
                    }
                ),
                json.dumps(
                    {
                        "title": "Accepted Inbox",
                        "relative_path": "inbox/accepted.md",
                        "category": "docs",
                        "status": "accepted",
                    }
                ),
                json.dumps(
                    {
                        "title": "Unmarked Startup Source",
                        "relative_path": "sources/web/startup.md",
                    }
                ),
            ]
        )
        + "\n"
    )
    (knowledge_dir / "sources" / "web" / "startup.md").write_text("# S\n")
    (coral_dir / "public" / "attempts").mkdir(parents=True)
    (coral_dir / "public" / "attempts" / "old.json").write_text("{}")
    (coral_dir / "public" / "logs").mkdir(parents=True)
    (coral_dir / "public" / "logs" / "agent-1.0.log").write_text("log")
    (coral_dir / "public" / "control" / "agents").mkdir(parents=True)
    (coral_dir / "public" / "control" / "next_instruction.md").write_text("old resume feedback")
    (coral_dir / "public" / "control" / "agents" / "agent-1.json").write_text(
        json.dumps({"desired_state": "stopped"})
    )
    (coral_dir / "public" / "run_state.json").write_text("{}")
    (coral_dir / "config_dir").write_text(str(task_source))
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "My Task", "description": "d"},
                "workspace": {"results_dir": str(results_dir), "repo_path": "./seed"},
                "grader": {"entrypoint": "g:Grader"},
                "agents": {"count": 1},
            }
        )
    )

    response = await create_run(_request(coral_dir, results_dir=results_dir))
    payload = json.loads(response.body)
    new_coral = Path(payload["coral_dir"])

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["task"] == "my-task"
    assert (new_coral / "config.yaml").is_file()
    assert (new_coral / "config_dir").read_text() == str(task_source)
    new_knowledge = new_coral / "public" / "knowledge"
    assert (new_knowledge / "sources" / "papers" / "paper.md").read_text() == "# P\n"
    assert (new_knowledge / "sources" / "web" / "startup.md").read_text() == "# S\n"
    assert (new_knowledge / "sources" / "docs" / "accepted.md").read_text() == "# A\n"
    assert not (new_knowledge / "sources" / "papers" / "rejected.md").exists()
    assert not (new_knowledge / "inbox" / "candidate.md").exists()
    assert not (new_knowledge / "inbox" / "accepted.md").exists()
    promoted_entries = [
        json.loads(line)
        for line in (new_knowledge / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [entry["title"] for entry in promoted_entries] == [
        "Accepted Paper",
        "Accepted Inbox",
        "Unmarked Startup Source",
    ]
    assert promoted_entries[0]["status"] == "accepted"
    assert "promoted_at" in promoted_entries[0]
    assert promoted_entries[1]["relative_path"] == "sources/docs/accepted.md"
    assert promoted_entries[1]["promoted_from"] == "inbox/accepted.md"
    assert (knowledge_dir / "sources" / "papers" / "rejected.md").exists()
    assert (knowledge_dir / "inbox" / "candidate.md").exists()
    assert (knowledge_dir / "inbox" / "accepted.md").exists()
    assert (new_coral / "public" / "notes").resolve() == (
        new_coral / "public" / "knowledge" / "notes"
    ).resolve()
    assert not (new_coral / "public" / "attempts").exists()
    assert not (new_coral / "public" / "logs").exists()
    assert not (new_coral / "public" / "control").exists()
    assert not (new_coral / "public" / "run_state.json").exists()
    saved = yaml.safe_load((new_coral / "config.yaml").read_text())
    assert saved["workspace"]["run_dir"] == str(new_coral.parent)
    assert (task_dir / "latest").resolve() == new_coral.parent.resolve()


async def test_create_run_blocks_while_current_run_is_alive(tmp_path):
    results_dir = tmp_path / "results"
    task_dir = results_dir / "my-task"
    run_dir = task_dir / "run-1"
    coral_dir = run_dir / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    (coral_dir / "public" / "manager.pid").write_text(str(os.getpid()))
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "My Task", "description": "d"},
                "workspace": {"results_dir": str(results_dir), "repo_path": "./seed"},
                "agents": {"count": 1},
            }
        )
    )

    response = await create_run(_request(coral_dir, results_dir=results_dir))

    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["ok"] is False
    assert "stop the current run" in payload["message"]


async def test_create_run_blocks_when_agent_process_is_alive_without_manager_pid(tmp_path):
    results_dir = tmp_path / "results"
    task_dir = results_dir / "my-task"
    run_dir = task_dir / "run-1"
    coral_dir = run_dir / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    (coral_dir / "public" / "agent.pids").write_text(str(os.getpid()))
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "My Task", "description": "d"},
                "workspace": {"results_dir": str(results_dir), "repo_path": "./seed"},
                "agents": {"count": 1},
            }
        )
    )

    response = await create_run(_request(coral_dir, results_dir=results_dir))

    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["ok"] is False
    assert "stop the current run" in payload["message"]


async def test_control_config_preserves_plan_fields_after_activity(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    current = yaml.safe_load(
        """
task:
  name: original-task
  description: original instructions
workspace:
  results_dir: ./results
  repo_path: ./seed
knowledge:
  path: ./knowledge
  snapshot: true
grader:
  entrypoint: original.grader:Grader
  setup:
    - uv pip install -e ./grader
  private:
    - secret.json
  direction: maximize
  eval_version: eval_v1
  profile: quick
agents:
  count: 2
  runtime: claude_code
  model: sonnet
  research: false
  warmstart:
    enabled: false
  max_turns: 0
  runtime_options:
    mounts:
      data: ./data
    model_reasoning_effort: medium
islands:
  count: 2
  migration:
    enabled: true
    every: 50
run:
  max_runtime_seconds: 600
"""
    )
    (coral_dir / "config.yaml").write_text(yaml.safe_dump(current))
    (coral_dir / "public" / "run_state.json").write_text(
        json.dumps({"started_at": "2026-06-01T10:00:00+00:00"})
    )

    incoming = json.loads(json.dumps(current))
    incoming["task"]["name"] = "changed-task"
    incoming["workspace"]["repo_path"] = "/tmp/changed"
    incoming["knowledge"]["path"] = "/tmp/knowledge"
    incoming["grader"]["entrypoint"] = "changed.grader:Grader"
    incoming["grader"]["setup"] = ["echo changed"]
    incoming["grader"]["private"] = ["changed.json"]
    incoming["grader"]["direction"] = "minimize"
    incoming["grader"]["eval_version"] = "eval_v2"
    incoming["agents"]["count"] = 9
    incoming["agents"]["runtime"] = "codex"
    incoming["agents"]["model"] = "gpt-5"
    incoming["agents"]["research"] = True
    incoming["agents"]["warmstart"]["enabled"] = True
    incoming["agents"]["max_turns"] = 99
    incoming["agents"]["runtime_options"]["mounts"] = {"data": "/tmp/changed"}
    incoming["agents"]["runtime_options"]["model_reasoning_effort"] = "high"
    incoming["islands"]["count"] = 4
    incoming["islands"]["migration"]["every"] = 25
    incoming["run"]["max_runtime_seconds"] = 1200

    response = await save_control_config(_json_request(coral_dir, {"config": incoming}))

    assert response.status_code == 200
    saved = json.loads(response.body)["config"]
    assert saved["task"]["name"] == "original-task"
    assert saved["workspace"]["repo_path"] == "./seed"
    assert saved["knowledge"]["path"] == "./knowledge"
    assert saved["grader"]["entrypoint"] == "original.grader:Grader"
    assert saved["grader"]["setup"] == ["uv pip install -e ./grader"]
    assert saved["grader"]["private"] == ["secret.json"]
    assert saved["grader"]["direction"] == "maximize"
    assert saved["grader"]["eval_version"] == "eval_v1"
    assert saved["agents"]["count"] == 2
    assert saved["agents"]["runtime"] == "claude_code"
    assert saved["agents"]["model"] == "gpt-5"
    assert saved["agents"]["research"] is True
    assert saved["agents"]["warmstart"]["enabled"] is True
    assert saved["agents"]["max_turns"] == 0
    assert saved["agents"]["runtime_options"]["mounts"] == {"data": "./data"}
    assert saved["agents"]["runtime_options"]["model_reasoning_effort"] == "high"
    assert saved["islands"]["count"] == 2
    assert saved["islands"]["migration"]["every"] == 25
    assert saved["run"]["max_runtime_seconds"] == 1200


async def test_control_config_allows_runtime_and_topology_before_activity(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    current = yaml.safe_load(
        """
task:
  name: original-task
  description: original instructions
grader:
  eval_version: eval_v1
agents:
  count: 2
  runtime: claude_code
  model: sonnet
islands:
  count: 1
  migration:
    enabled: true
    every: 50
"""
    )
    (coral_dir / "config.yaml").write_text(yaml.safe_dump(current))

    incoming = json.loads(json.dumps(current))
    incoming["agents"]["runtime"] = "codex"
    incoming["agents"]["model"] = "gpt-5"
    incoming["islands"]["count"] = 2
    incoming["islands"]["migration"]["every"] = 20

    response = await save_control_config(_json_request(coral_dir, {"config": incoming}))

    assert response.status_code == 200
    saved = json.loads(response.body)["config"]
    assert saved["agents"]["runtime"] == "codex"
    assert saved["agents"]["model"] == "gpt-5"
    assert saved["islands"]["count"] == 2
    assert saved["islands"]["migration"]["every"] == 20


async def test_control_config_rejects_more_islands_than_planned_agents(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    current = yaml.safe_load(
        """
task:
  name: original-task
  description: original instructions
agents:
  count: 2
islands:
  count: 1
"""
    )
    (coral_dir / "config.yaml").write_text(yaml.safe_dump(current))

    incoming = json.loads(json.dumps(current))
    incoming["islands"]["count"] = 3

    response = await save_control_config(_json_request(coral_dir, {"config": incoming}))

    assert response.status_code == 400
    payload = json.loads(response.body)
    assert "islands.count (3) cannot exceed planned agents (2)" in payload["error"]


async def test_control_readiness_reports_invalid_agent_island_topology(tmp_path):
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    (coral_dir / "public").mkdir(parents=True)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "my-task", "description": "d"},
                "grader": {"entrypoint": "g:Grader", "eval_version": "eval_v1", "profile": "quick"},
                "agents": {"count": 1},
                "islands": {"count": 2},
            }
        )
    )

    response = await get_control_readiness(_request(coral_dir))

    assert response.status_code == 200
    payload = json.loads(response.body)
    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["status"] == "missing"
    assert checks["topology"]["status"] == "missing"
    assert "islands.count (2) cannot exceed planned agents (1)" in checks["topology"]["detail"]


def test_file_watcher_snapshot_scans_island_roots(tmp_path):
    coral_dir = tmp_path / ".coral"
    _make_multi_island(coral_dir)
    (coral_dir / "eval_count").write_text("3")
    write_attempt(coral_dir, _make_attempt("abc", "0-agent-1"), island_id="0")
    note = coral_dir / "islands" / "1" / "notes" / "n.md"
    note.write_text("# n")
    log = coral_dir / "islands" / "0" / "logs" / "0-agent-1.0.log"
    log.write_text("x")
    progress = coral_dir / "islands" / "0" / "eval_logs" / "abc" / "progress.jsonl"
    progress.parent.mkdir(parents=True)
    progress.write_text("{}\n")
    run_state = coral_dir / "public" / "run_state.json"
    run_state.write_text("{}")
    for path in (note, log):
        path.touch()

    snapshot = FileWatcher(coral_dir)._snapshot()

    assert snapshot["attempts_count"] == 1
    assert snapshot["attempts_mtime"] > 0
    assert snapshot["notes_mtime"] > 0
    assert snapshot["log_sizes"] == {"0/0-agent-1.0.log": 1}
    assert "0/abc" in snapshot["progress_mtimes"]
    assert snapshot["eval_count"] == 3
    assert snapshot["run_state_mtime"] > 0
