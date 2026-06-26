"""Tests for task-directory validation (coral validate / coral start)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from coral.cli.author import _cmd_validate_run_dir, cmd_init
from coral.cli.validation import validate_task
from coral.hub.attempts import write_attempt
from coral.hub.readiness import build_control_readiness
from coral.types import Attempt

_TASK_YAML = """\
task:
  name: t
  description: d
grader:
{grader_body}
agents:
  count: 1
"""


def _make_task(base: Path, grader_body: str) -> Path:
    task_dir = base / "task"
    task_dir.mkdir()
    (task_dir / "task.yaml").write_text(_TASK_YAML.format(grader_body=grader_body))
    return task_dir


def test_validate_accepts_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        task_dir = _make_task(Path(d), '  entrypoint: "my_pkg.grader:Grader"')
        assert validate_task(task_dir) == []


def test_validate_rejects_missing_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        task_dir = _make_task(Path(d), "  timeout: 60")
        errors = validate_task(task_dir)
        assert any("No grader configured" in e for e in errors)


def test_validate_rejects_malformed_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        task_dir = _make_task(Path(d), "  entrypoint: my_pkg.grader.Grader")
        errors = validate_task(task_dir)
        assert any("module.path:ClassName" in e for e in errors)


def test_init_creates_research_workbench_knowledge_skeleton(tmp_path):
    task_dir = tmp_path / "my-task"

    cmd_init(SimpleNamespace(path=str(task_dir), name="My Task"))

    knowledge_dir = task_dir / "knowledge"
    expected_dirs = [
        "manuals",
        "external/items",
        "practice/agents",
        "briefs/agent-seeds",
    ]
    for rel in expected_dirs:
        assert (knowledge_dir / rel).is_dir(), rel
    old_dirs = ["capsules", "maps", "packs", "sources", "notes", "inbox", "archive"]
    for rel in old_dirs:
        assert not (knowledge_dir / rel).exists(), rel
    assert "coral kb index manual" in (knowledge_dir / "index.md").read_text()
    assert (knowledge_dir / "external" / "index.jsonl").read_text() == ""
    assert (knowledge_dir / "manuals" / "evaluation-spaces.md").is_file()
    assert (knowledge_dir / "manuals" / "submit-system.md").is_file()
    assert (knowledge_dir / "manuals" / "knowledge-cli.md").is_file()
    eval_spec = (knowledge_dir / "eval_spec.md").read_text()
    assert "Agent API" in eval_spec
    assert "Evaluation Level" in eval_spec
    assert "Metrics" in eval_spec
    assert "Acceptance" in eval_spec
    assert "Progress Protocol" in eval_spec
    assert "Eval Profiles" in eval_spec
    assert "Feedback Report" in eval_spec
    assert "Breakthrough metrics" in eval_spec
    assert "Guardrail metrics" in eval_spec
    assert "Anti-cheating checks" in eval_spec
    assert "eval_spec.md" in (knowledge_dir / "index.md").read_text()


def test_validate_checks_eval_spec_contract_sections(tmp_path):
    task_dir = _make_task(tmp_path, '  entrypoint: "my_pkg.grader:Grader"')
    knowledge_dir = task_dir / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "eval_spec.md").write_text("# Eval Spec\n\n## Metrics\n- score\n")

    errors = validate_task(task_dir)

    assert any("eval_spec.md" in e and "agent api" in e for e in errors)


def _write_ready_run(coral_dir: Path) -> None:
    knowledge_dir = coral_dir / "public" / "knowledge"
    source_dir = knowledge_dir / "external" / "items" / "src-001"
    source_dir.mkdir(parents=True)
    (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
    (knowledge_dir / "eval_spec.md").write_text(
        "# Eval Spec\n\n"
        "## Agent API\n"
        "Use coral eval and coral run.\n\n"
        "## Evaluation Level\n"
        "L2.\n\n"
        "## Metrics\n"
        "Breakthrough metrics improve the target. Guardrail metrics keep safety above the floor. Anti-cheating checks prevent leakage and overfit.\n\n"
        "## Acceptance\n"
        "Define the acceptance floor.\n\n"
        "## Progress Protocol\n"
        "Long evals report progress.\n\n"
        "## Eval Profiles\n"
        "quick, medium, full, stress.\n\n"
        "## Feedback Report\n"
        "Return score, rank, self history, baselines, per-metric ranks, and failure reports.\n"
    )
    (source_dir / "source.md").write_text("# Paper\n")
    (knowledge_dir / "external" / "index.jsonl").write_text(
        json.dumps(
            {
                "id": "src-001",
                "space": "external",
                "kind": "paper",
                "title": "Paper",
                "status": "active",
                "source": "https://example.com/paper",
                "item_path": "external/items/src-001",
            }
        )
        + "\n"
    )
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").write_text(
        "# Agent 1\n\nTry the baseline first.\n"
    )
    agent_script = knowledge_dir / "briefs" / "agent-seeds" / "agent-1.eval.sh"
    agent_script.write_text("#!/usr/bin/env bash\ncoral eval -m 'agent-1 first eval'\n")
    agent_script.chmod(0o755)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 1},
            }
        )
    )
    write_attempt(
        coral_dir,
        Attempt(
            commit_hash="baseline",
            agent_id="baseline",
            title="baseline",
            score=0.5,
            status="completed",
            parent_hash=None,
            timestamp="2026-06-01T10:00:00Z",
            metadata={"baseline": True, "eval_version": "eval_v1", "eval_profile": "quick"},
        ),
    )


def test_build_control_readiness_accepts_prepared_timestamp(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)

    readiness = build_control_readiness(coral_dir)

    assert readiness["status"] == "ready"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["eval_spec"]["status"] == "ready"
    assert checks["knowledge"]["status"] == "ready"
    assert checks["baseline"]["status"] == "ready"
    assert checks["agent_briefs"]["status"] == "ready"


def test_build_control_readiness_requires_executable_agent_eval_script(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    script = coral_dir / "public" / "knowledge" / "briefs" / "agent-seeds" / "agent-1.eval.sh"
    script.unlink()

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["agent_briefs"]["status"] == "missing"
    assert "0/1 initialization bundle" in checks["agent_briefs"]["detail"]


def test_build_control_readiness_rejects_non_executable_agent_eval_script(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    script = coral_dir / "public" / "knowledge" / "briefs" / "agent-seeds" / "agent-1.eval.sh"
    script.chmod(0o644)

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["agent_briefs"]["status"] == "missing"
    assert "missing: agent-1" in checks["agent_briefs"]["detail"]


def test_build_control_readiness_requires_matching_agent_ids(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-99.md").write_text(
        "# agent-99\n\nWrong route.\n"
    )
    wrong_script = knowledge_dir / "briefs" / "agent-seeds" / "agent-99.eval.sh"
    wrong_script.write_text("#!/usr/bin/env bash\ncoral eval -m 'wrong eval'\n")
    wrong_script.chmod(0o755)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 2},
            }
        )
    )

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["agent_briefs"]["status"] == "missing"
    assert "missing: agent-2" in checks["agent_briefs"]["detail"]


def test_build_control_readiness_rejects_unscored_baseline(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    baseline = coral_dir / "public" / "attempts" / "baseline.json"
    baseline.write_text(
        baseline.read_text().replace('"score": 0.5', '"score": null'),
        encoding="utf-8",
    )

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["baseline"]["status"] == "missing"
    assert "0/1 baseline attempt" in checks["baseline"]["detail"]


def test_build_control_readiness_rejects_baseline_with_mismatched_eval_identity(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    baseline = coral_dir / "public" / "attempts" / "baseline.json"
    baseline.write_text(
        baseline.read_text().replace('"eval_profile": "quick"', '"eval_profile": "full"'),
        encoding="utf-8",
    )

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["baseline"]["status"] == "missing"
    assert "0 match eval_v1 / quick" in checks["baseline"]["detail"]


def test_build_control_readiness_rejects_missing_external_item(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    source = coral_dir / "public" / "knowledge" / "external" / "items" / "src-001" / "source.md"
    source.unlink()

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["knowledge"]["status"] == "missing"
    assert "missing file external/items/src-001/source.md" in checks["knowledge"]["detail"]


def test_build_control_readiness_rejects_external_index_without_item_path(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    index = coral_dir / "public" / "knowledge" / "external" / "index.jsonl"
    index.write_text('{"id":"src-999","title":"Missing","status":"active"}\n')

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "missing"
    assert checks["knowledge"]["status"] == "missing"
    assert "has no item_path" in checks["knowledge"]["detail"]


def test_build_control_readiness_accepts_public_baseline_with_multiple_agent_routes(tmp_path):
    coral_dir = tmp_path / "results" / "ready-task" / "run-1" / ".coral"
    _write_ready_run(coral_dir)
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").unlink()
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").write_text(
        "# agent-1\n\nSparse route.\n"
    )
    agent_1_script = knowledge_dir / "briefs" / "agent-seeds" / "agent-1.eval.sh"
    agent_1_script.write_text("#!/usr/bin/env bash\ncoral eval -m 'agent-1 first eval'\n")
    agent_1_script.chmod(0o755)
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-2.md").write_text(
        "# agent-2\n\nRobust route.\n"
    )
    agent_2_script = knowledge_dir / "briefs" / "agent-seeds" / "agent-2.eval.sh"
    agent_2_script.write_text("#!/usr/bin/env bash\ncoral eval -m 'agent-2 first eval'\n")
    agent_2_script.chmod(0o755)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v1",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 2},
            }
        )
    )

    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert readiness["status"] == "ready"
    assert checks["baseline"]["status"] == "ready"


def test_cli_validate_run_dir_reports_ready_for_prepared_timestamp(tmp_path, capsys):
    run_dir = tmp_path / "results" / "ready-task" / "run-1"
    coral_dir = run_dir / ".coral"
    _write_ready_run(coral_dir)

    _cmd_validate_run_dir(run_dir)

    out = capsys.readouterr().out
    assert "Readiness: READY" in out
    assert "Run readiness: OK" in out


def test_cli_validate_run_dir_exits_for_missing_artifacts(tmp_path):
    coral_dir = tmp_path / "run" / ".coral"
    (coral_dir / "public").mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        _cmd_validate_run_dir(coral_dir)

    assert exc.value.code == 1
