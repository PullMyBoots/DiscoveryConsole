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
        "sources/papers",
        "sources/repos",
        "sources/web",
        "sources/docs",
        "sources/datasets",
        "notes/research",
        "notes/experiments",
        "notes/synthesis",
        "notes/open-questions",
        "briefs/agent-seeds",
        "briefs/islands",
        "briefs/island-themes",
        "inbox",
        "archive",
    ]
    for rel in expected_dirs:
        assert (knowledge_dir / rel).is_dir(), rel
    assert "Breakthrough Metrics" in (knowledge_dir / "eval_spec.md").read_text()
    assert (knowledge_dir / "manifest.jsonl").read_text() == ""


def test_prepare_agent_plan_script_writes_concrete_briefs(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "islands": [
                    {"id": "0", "title": "Sparse Search", "brief": "Explore sparse variants."},
                    {"id": "1", "title": "Robustness", "brief": "Stress guardrails."},
                ],
                "agents": [
                    {
                        "id": "0-agent-1",
                        "island_id": "0",
                        "title": "Sparse Optimizer",
                        "brief": "Start from sparse baseline.",
                        "focus": ["fast iteration"],
                        "starting_steps": ["Run quick eval"],
                        "avoid": ["Changing eval files"],
                    },
                    {
                        "id": "1-agent-1",
                        "island_id": "1",
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

    island = (knowledge_dir / "briefs" / "islands" / "0.md").read_text()
    agent = (knowledge_dir / "briefs" / "agent-seeds" / "0-agent-1.md").read_text()
    assert "# Sparse Search" in island
    assert "island_id: 0" in island
    assert "# Sparse Optimizer" in agent
    assert "## Initial Direction" in agent
    assert "- Run quick eval" in agent
    assert "- Changing eval files" in agent


def test_prepare_agent_plan_rejects_empty_islands(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    script = SKILL_DIR / "scripts" / "prepare_agent_plan.py"

    result = subprocess.run(
        [sys.executable, str(script), str(knowledge_dir), "--agents", "1", "--islands", "2"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "island count (2) cannot exceed agent count (1)" in result.stderr


def test_prepare_agent_plan_rejects_json_plan_with_unassigned_island(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "islands": [
                    {"id": "0", "title": "Sparse"},
                    {"id": "1", "title": "Robustness"},
                ],
                "agents": [
                    {"id": "0-agent-1", "island_id": "0", "title": "A"},
                    {"id": "0-agent-2", "island_id": "0", "title": "B"},
                ],
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
    assert "island(s) without any assigned agent: 1" in result.stderr


def test_prepare_agent_plan_rejects_json_plan_with_unknown_island(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "islands": [{"id": "0", "title": "Sparse"}],
                "agents": [
                    {"id": "1-agent-1", "island_id": "1", "title": "Unknown route"},
                ],
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
    assert "agent plan references unknown island id(s): 1" in result.stderr


def test_record_baseline_attempt_script_satisfies_readiness_baseline(tmp_path):
    coral_dir = tmp_path / "run" / ".coral"
    knowledge_dir = coral_dir / "public" / "knowledge"
    (knowledge_dir / "sources" / "papers").mkdir(parents=True)
    (knowledge_dir / "briefs" / "agent-seeds").mkdir(parents=True)
    (knowledge_dir / "eval_spec.md").write_text(
        "# Eval Spec\n\n"
        "## Breakthrough Metrics\nImprove target.\n\n"
        "## Guardrail Metrics\nKeep floor.\n\n"
        "## Anti-cheating Checks\nPrevent leakage and overfit.\n"
    )
    (knowledge_dir / "manifest.jsonl").write_text(
        json.dumps(
            {
                "title": "Paper",
                "relative_path": "sources/papers/paper.md",
                "category": "papers",
            }
        )
        + "\n"
    )
    (knowledge_dir / "sources" / "papers" / "paper.md").write_text("# Paper\n")
    (knowledge_dir / "briefs" / "agent-seeds" / "agent-1.md").write_text(
        "# Agent 1\n\nTry the seed baseline first.\n"
    )
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
