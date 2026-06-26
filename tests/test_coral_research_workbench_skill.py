"""Tests for the bundled Codex CORAL research workbench skill scripts."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from coral.hub.readiness import build_control_readiness

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "codex-skill" / "coral-research-workbench"
DISCOVERY_REPO = "https://github.com/PullMyBoots/DiscoveryConsole"
DISCOVERY_RAW_INSTALL = (
    "https://raw.githubusercontent.com/PullMyBoots/DiscoveryConsole/main/install.sh"
)


def test_check_coral_install_reports_missing_when_cli_absent(tmp_path):
    script = SKILL_DIR / "scripts" / "check_coral_install.py"

    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=False,
        text=True,
        capture_output=True,
        env={"PATH": str(tmp_path)},
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["ok"] is False
    assert payload["status"] == "missing"
    assert payload["install"] == f"curl -fsSL {DISCOVERY_RAW_INSTALL} | sh"


def test_install_sources_point_to_discoveryconsole() -> None:
    files = [
        ROOT / "README.md",
        ROOT / "README_CN.md",
        ROOT / "install.sh",
        ROOT / "pyproject.toml",
        ROOT / "docs" / "content" / "docs" / "getting-started" / "installation.mdx",
        SKILL_DIR / "scripts" / "check_coral_install.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

    assert DISCOVERY_REPO in combined
    assert DISCOVERY_RAW_INSTALL in combined
    assert "git+https://github.com/Human-Agent-Society/CORAL.git" not in combined
    assert "raw.githubusercontent.com/Human-Agent-Society/CORAL" not in combined


def test_skill_reference_links_exist() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r"`(references/[^`]+\.md)`", skill)))

    assert refs
    for ref in refs:
        assert (SKILL_DIR / ref).is_file(), ref


def test_check_coral_install_reports_ready_for_fake_cli(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "coral"
    fake.write_text("#!/bin/sh\nprintf 'coral 9.9.9\\n'\n")
    fake.chmod(0o755)
    script = SKILL_DIR / "scripts" / "check_coral_install.py"

    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        text=True,
        capture_output=True,
        env={"PATH": str(bin_dir)},
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["version"] == "coral 9.9.9"
    assert payload["path"] == str(fake)


def test_validate_workspace_reports_missing_when_cli_absent(tmp_path):
    script = SKILL_DIR / "scripts" / "validate_workspace.py"

    result = subprocess.run(
        [sys.executable, str(script), "--task-dir", str(tmp_path), "--json"],
        check=False,
        text=True,
        capture_output=True,
        env={"PATH": str(tmp_path / "empty-bin")},
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["ok"] is False
    assert payload["status"] == "missing"
    assert payload["steps"] == []


def test_validate_workspace_runs_task_and_readiness_checks(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    task_dir = tmp_path / "task"
    run_dir = tmp_path / "run" / ".coral"
    task_dir.mkdir()
    run_dir.mkdir(parents=True)
    log_path = tmp_path / "argv.log"
    fake = bin_dir / "coral"
    fake.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log_path}\n"
        "printf 'ok: %s\\n' \"$*\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script = SKILL_DIR / "scripts" / "validate_workspace.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--task-dir",
            str(task_dir),
            "--run-dir",
            str(run_dir),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        env={"PATH": str(bin_dir)},
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert [step["name"] for step in payload["steps"]] == ["task", "run_readiness"]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        f"validate {task_dir}",
        f"validate --run-dir {run_dir}",
    ]


def test_prepare_knowledge_script_creates_required_skeleton(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    script = SKILL_DIR / "scripts" / "prepare_knowledge.py"

    subprocess.run(
        [sys.executable, str(script), str(knowledge_dir)],
        check=True,
        text=True,
        capture_output=True,
    )

    expected_dirs = [
        "manuals",
        "external/items",
        "practice/agents",
        "briefs/agent-seeds",
    ]
    for rel in expected_dirs:
        assert (knowledge_dir / rel).is_dir(), rel
    for rel in ["capsules", "maps", "packs", "sources", "notes", "inbox", "archive"]:
        assert not (knowledge_dir / rel).exists(), rel
    manuals = {path.name for path in (knowledge_dir / "manuals").iterdir()}
    assert {
        "coral-overview-cli.md",
        "agent-loops.md",
        "evaluation-spaces.md",
        "submit-system.md",
        "knowledge-cli.md",
    } <= manuals
    assert "coral kb index manual" in (knowledge_dir / "index.md").read_text()
    eval_spec = (knowledge_dir / "eval_spec.md").read_text()
    assert "Agent API" in eval_spec
    assert "Evaluation Level" in eval_spec
    assert "Choose exactly one level" in eval_spec
    assert "Metrics" in eval_spec
    assert "Breakthrough metrics" in eval_spec
    assert "Feedback Report" in eval_spec
    assert (knowledge_dir / "external" / "index.jsonl").read_text() == ""


def test_prepare_agent_plan_script_writes_runnable_initialization_plans(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "agent-1",
                        "title": "Sparse Optimizer",
                        "brief": "Start from sparse baseline.",
                        "must_read": ["src-001"],
                        "optional_read": ["src-002"],
                        "eval_targets": ["breakthrough.score"],
                        "focus": ["fast iteration"],
                        "starting_steps": ["Run quick eval"],
                        "eval_args": ["--tune"],
                        "avoid": ["Changing eval files"],
                    },
                    {
                        "id": "agent-2",
                        "title": "Guardrail Tester",
                        "brief": "Probe failure cases.",
                    },
                ],
            }
        )
    )
    script = SKILL_DIR / "scripts" / "prepare_agent_plan.py"

    subprocess.run(
        [sys.executable, str(script), str(knowledge_dir), "--plan", str(plan_path)],
        check=True,
        text=True,
        capture_output=True,
    )

    agent = (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").read_text()
    eval_script_path = knowledge_dir / "briefs" / "agent-seeds" / "agent-1.eval.sh"
    eval_script = eval_script_path.read_text()
    assert "# Runnable Initialization Plan: Sparse Optimizer" in agent
    assert "First eval script: `CORAL_SHARED/knowledge/briefs/agent-seeds/agent-1.eval.sh`" in agent
    assert "## Starting Route" in agent
    assert "## Knowledge Lookup" in agent
    assert "## Runnable First Steps" in agent
    assert "## First Eval" in agent
    assert "## Evolution Rule" in agent
    assert "bash CORAL_SHARED/knowledge/briefs/agent-seeds/agent-1.eval.sh" in agent
    assert "- Run quick eval" in agent
    assert "- Changing eval files" in agent
    assert eval_script_path.stat().st_mode & 0o111
    assert "coral eval" in eval_script
    assert "--agent" not in eval_script
    assert "--tune" in eval_script
    assert "CORAL_EVAL_ARGS" not in eval_script
    assert "CORAL_EVAL_TUNE" in eval_script
    assert "CORAL_EVAL_TIMEOUT" in eval_script
    assert "- src-001" in agent
    assert "- src-002" in agent
    assert "- breakthrough.score" in agent


def test_prepare_agent_plan_rejects_json_plan_without_agents(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"agents": []}))
    script = SKILL_DIR / "scripts" / "prepare_agent_plan.py"

    result = subprocess.run(
        [sys.executable, str(script), str(knowledge_dir), "--plan", str(plan_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "plan JSON must contain at least one agent" in result.stderr


def test_prepare_agent_plan_rejects_invalid_agent_id(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "agent 1",
                        "title": "Invalid",
                        "brief": "test",
                    }
                ]
            }
        )
    )
    script = SKILL_DIR / "scripts" / "prepare_agent_plan.py"

    result = subprocess.run(
        [sys.executable, str(script), str(knowledge_dir), "--plan", str(plan_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "unsupported characters" in result.stderr


def test_prepare_agent_plan_rejects_unsafe_eval_args(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "agent-1",
                        "title": "Unsafe",
                        "brief": "test",
                        "eval_args": ["--workdir", "/tmp/other"],
                    }
                ]
            }
        )
    )
    script = SKILL_DIR / "scripts" / "prepare_agent_plan.py"

    result = subprocess.run(
        [sys.executable, str(script), str(knowledge_dir), "--plan", str(plan_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "eval_args may only contain" in result.stderr


def test_record_baseline_attempt_script_satisfies_readiness_baseline(tmp_path):
    coral_dir = tmp_path / "run" / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    source_dir = knowledge_dir / "external" / "items" / "src-001"
    source_dir.mkdir(parents=True)
    (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
    (knowledge_dir / "eval_spec.md").write_text(
        "# Eval Spec\n\n"
        "## Agent API\nUse coral eval.\n\n"
        "## Evaluation Level\nL2.\n\n"
        "## Metrics\nBreakthrough metrics improve target; guardrail metrics keep floor; anti-cheating checks prevent leakage.\n\n"
        "## Acceptance\nDefine accept floor.\n\n"
        "## Progress Protocol\nReport progress.\n\n"
        "## Eval Profiles\nquick and full.\n\n"
        "## Feedback Report\nReturn score, ranks, history, baselines, and failures.\n"
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
        "# Agent 1\n\nTry the seed baseline first.\n"
    )
    agent_script = knowledge_dir / "briefs" / "agent-seeds" / "agent-1.eval.sh"
    agent_script.write_text("#!/usr/bin/env bash\ncoral eval -m 'agent-1 first eval'\n")
    agent_script.chmod(0o755)
    (coral_dir / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (coral_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "task": {"name": "ready-task", "description": "d"},
                "grader": {
                    "entrypoint": "pkg.grader:Grader",
                    "eval_version": "eval_v7",
                    "profile": "quick",
                    "profiles": {"quick": {"timeout": 60}},
                },
                "agents": {"count": 1},
            }
        )
    )

    script = SKILL_DIR / "scripts" / "record_baseline_attempt.py"
    subprocess.run(
        [
            sys.executable,
            str(script),
            str(coral_dir),
            "--score",
            "0.72",
            "--name",
            "seed",
            "--components",
            '{"breakthrough": 0.8, "guardrail": {"value": 1.0}}',
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    attempt_path = coral_dir / "public" / "attempts" / "baseline-seed.json"
    attempt = json.loads(attempt_path.read_text())
    readiness = build_control_readiness(coral_dir)
    checks = {check["id"]: check for check in readiness["checks"]}

    assert attempt["score"] == 0.72
    assert attempt["status"] == "baseline"
    assert attempt["metadata"]["baseline"] is True
    assert attempt["metadata"]["reference"] == "baseline"
    assert attempt["metadata"]["eval_version"] == "eval_v7"
    assert attempt["metadata"]["eval_profile"] == "quick"
    assert attempt["metadata"]["score_components"]["breakthrough"]["value"] == 0.8
    assert readiness["status"] == "ready"
    assert checks["baseline"]["status"] == "ready"


def test_write_eval_progress_script_writes_frontend_protocol(tmp_path):
    progress_path = tmp_path / "eval_logs" / "abc123" / "progress.jsonl"
    script = SKILL_DIR / "scripts" / "write_eval_progress.py"

    subprocess.run(
        [
            sys.executable,
            str(script),
            str(progress_path),
            "--job-id",
            "abc123",
            "--current",
            "3",
            "--total",
            "6",
            "--phase",
            "evaluate",
            "--message",
            "case 3/6",
            "--eval-version",
            "eval_v2",
            "--eval-profile",
            "quick",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    event = json.loads(progress_path.read_text().splitlines()[-1])
    assert event["type"] == "progress"
    assert event["job_id"] == "abc123"
    assert event["current"] == 3
    assert event["total"] == 6
    assert event["percent"] == 0.5
    assert event["phase"] == "evaluate"
    assert event["message"] == "case 3/6"
    assert event["eval_version"] == "eval_v2"
    assert event["eval_profile"] == "quick"
    assert "timestamp" in event
