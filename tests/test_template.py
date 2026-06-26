"""Tests for CORAL.md template generation."""

from coral.config import AgentConfig, CoralConfig, EvaluationConfig, GraderConfig, TaskConfig
from coral.template.coral_md import generate_coral_md


def test_generate_coral_md_has_required_sections():
    config = CoralConfig(
        task=TaskConfig(
            name="Kernel Optimization",
            description="Optimize the kernel for speed.",
            tips="Profile first!",
        ),
        grader=GraderConfig(direction="minimize"),
        agents=AgentConfig(count=2),
    )

    md = generate_coral_md(config, "agent-1")

    # Task info
    assert "Kernel Optimization" in md
    assert "Optimize the kernel for speed" in md

    # Tips
    assert "Profile first!" in md

    # Agent ID
    assert "agent-1" in md
    assert "creator: agent-1" in md

    # Score direction comes from grader.direction now (no type-based table)
    assert "lower is better" in md

    # Core structure
    assert "Orientation" in md
    assert "## 1. Research" in md
    assert "## 2. Plan" in md
    assert "## 3. Edit" in md
    assert "## 4. Evaluate" in md
    assert "## 6. Record Knowledge" in md
    assert "Ground Rules" in md

    # Key behavioral instructions
    assert "fully autonomous" in md
    assert "Do not duplicate effort" in md
    assert "Keep iterating" in md

    # Multi-agent awareness
    assert "several agents" in md
    assert "other agents" in md

    # Shared state
    assert "coral log --search" in md
    assert "coral kb index practice" in md
    assert ".claude/knowledge/practice" in md
    assert ".claude/skills/" in md
    assert "CORAL_OVERVIEW.md" in md
    assert "CORAL_LOOPS.md" in md
    assert "CORAL_SHARED" in md
    assert ".coral/attempts" not in md
    assert ".coral/jobs" not in md


def test_generate_coral_md_without_optional_sections():
    config = CoralConfig(
        task=TaskConfig(name="Simple Task", description="Do the thing."),
        grader=GraderConfig(),
    )

    md = generate_coral_md(config, "agent-5")

    assert "Simple Task" in md
    assert "Do the thing." in md
    assert "agent-5" in md
    assert "## Key Files" not in md
    assert "## Tips" not in md
    assert "higher is better" in md


def test_generate_coral_md_hidden_eval_does_not_tell_agent_to_read_grader_code():
    config = CoralConfig(
        task=TaskConfig(name="Hidden Eval", description="d"),
        evaluation=EvaluationConfig(level="L2"),
        grader=GraderConfig(),
        agents=AgentConfig(research=True),
    )

    md = generate_coral_md(config, "agent-1")

    assert "read the grader code" not in md
    assert "do not seek hidden grader internals" in md


def test_generate_coral_md_l1_allows_public_grader_research():
    config = CoralConfig(
        task=TaskConfig(name="Open Eval", description="d"),
        evaluation=EvaluationConfig(level="L1"),
        grader=GraderConfig(),
        agents=AgentConfig(research=True),
    )

    md = generate_coral_md(config, "agent-1")

    assert "read the public grader code" in md


def test_generate_coral_md_single_agent():
    """Single-agent template omits multi-agent sharing references."""
    config = CoralConfig(
        task=TaskConfig(
            name="Solo Task",
            description="Optimize alone.",
            tips="Be thorough.",
        ),
        grader=GraderConfig(),
        agents=AgentConfig(count=1),
    )

    md = generate_coral_md(config, "agent-1", single_agent=True)

    # Core content present
    assert "Solo Task" in md
    assert "Optimize alone." in md
    assert "Be thorough." in md
    assert "agent-1" in md
    assert "fully autonomous" in md
    assert "Keep iterating" in md

    # Multi-agent references absent
    assert "several agents" not in md
    assert "other agents" not in md
    assert "Share Knowledge" not in md
    assert "Do not duplicate effort" not in md

    # Single-agent still has notebook/skills (for self-use)
    assert "notebook" in md.lower()
    assert "skills" in md.lower()
    assert "Skills Across Loops" in md
    assert "work_loop:" in md
    assert "reflect_loop:" in md
    assert "Record Knowledge" in md


def test_generate_coral_md_tune_guardrails_present():
    """The when-to / when-not-to guardrails are explicit in both templates."""
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
        grader=GraderConfig(),
        agents=AgentConfig(count=2),
    )
    md_multi = generate_coral_md(config, "agent-1")
    md_single = generate_coral_md(config, "agent-1", single_agent=True)
    for md in (md_multi, md_single):
        assert "Use `--tune` for" in md or "Use `--tune` for:" in md
        assert "Do NOT use `--tune` for" in md
        assert "final" in md.lower()
        # Tune attempts should not enter the real-eval reflect archive path.
        assert "reflect_loop" in md
        assert "Skills Across Loops" in md
        assert "promote what was actually validated" in md
        # Per-grader description now ships in feedback, not in CORAL.md.
        # The template should advertise that contract so the agent knows
        # to look for the [--tune mode] line in their next eval result.
        assert "[--tune mode]" in md


def test_generate_coral_md_does_not_describe_tune_per_grader():
    """Per-grader tune description is now delivered via feedback, not CORAL.md."""
    config = CoralConfig(
        task=TaskConfig(name="t", description="d"),
        grader=GraderConfig(),
    )
    md = generate_coral_md(config, "agent-1")
    # Old placeholder must be gone.
    assert "{tune_description}" not in md
    assert "What this grader does in tune mode" not in md


def test_generate_coral_md_score_direction_from_config():
    """Score direction now comes solely from grader.direction (no type table)."""
    for direction, expected in [
        ("maximize", "higher is better"),
        ("minimize", "lower is better"),
    ]:
        config = CoralConfig(
            task=TaskConfig(name="t", description="d"),
            grader=GraderConfig(direction=direction),
        )
        md = generate_coral_md(config, "agent-1")
        assert expected in md, f"Missing '{expected}' for direction '{direction}'"


def test_generate_coral_md_has_no_removed_topology_mention():
    """Agent prompt does not include removed topology hints."""
    from coral.config import CoralConfig
    from coral.template.coral_md import generate_coral_md

    cfg = CoralConfig.from_dict({"task": {"name": "t", "description": "d"}})
    md = generate_coral_md(cfg, agent_id="agent-1")
    removed_term = "is" + "land"
    assert removed_term not in md.lower()


def test_generate_coral_md_includes_codex_prepared_initialization_plan():
    cfg = CoralConfig.from_dict(
        {
            "task": {"name": "t", "description": "d"},
        }
    )

    md = generate_coral_md(
        cfg,
        agent_id="agent-1",
        shared_dir=".codex",
        agent_seed_brief="# agent-1\n\nStart from the sparse baseline.",
        agent_seed_brief_path=".codex/knowledge/briefs/agent-seeds/agent-1.md",
    )

    assert "Codex-prepared runnable initialization bundle" in md
    assert "sparse baseline" in md
    assert ".codex/knowledge/briefs/agent-seeds/agent-1.md" in md


def test_generate_coral_md_uses_index_first_kb_orientation():
    cfg = CoralConfig.from_dict({"task": {"name": "t", "description": "d"}})

    md = generate_coral_md(cfg, agent_id="agent-1", shared_dir=".codex")

    assert "coral kb notebook --agent agent-1" in md
    assert "CORAL_OVERVIEW.md" in md
    assert "CORAL_LOOPS.md" in md
    assert "coral kb index manual" in md
    assert "coral kb index external" in md
    assert "coral kb index practice --by score" in md
    assert ".codex/knowledge/briefs/agent-seeds/agent-1.eval.sh" in md
    assert "index -> read -> act" in md
    assert ".codex/roles" not in md
    assert ".coral/attempts" not in md
