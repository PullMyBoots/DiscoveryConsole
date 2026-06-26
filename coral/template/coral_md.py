"""Generate CORAL.md agent instructions from template."""

from __future__ import annotations

from pathlib import Path

from coral.config import CoralConfig

_TEMPLATE_PATH = Path(__file__).parent / "coral.md.template"
_SINGLE_TEMPLATE_PATH = Path(__file__).parent / "coral_single.md.template"


def generate_coral_md(
    config: CoralConfig,
    agent_id: str,
    single_agent: bool = False,
    shared_dir: str = ".claude",
    agent_seed_brief: str = "",
    agent_seed_brief_path: str = "",
) -> str:
    """Produce the CORAL.md file that agents read at startup.

    Args:
        config: The coral config
        agent_id: This agent's ID
        single_agent: If True, use simplified single-agent template (no sharing references)
        shared_dir: Name of the shared state directory (e.g. ".claude", ".codex", ".opencode")
        agent_seed_brief: Codex-prepared runnable initialization bundle for this agent
        agent_seed_brief_path: Shared-state path where the initialization plan is stored
    """
    template_path = _SINGLE_TEMPLATE_PATH if single_agent else _TEMPLATE_PATH
    template = template_path.read_text()

    # Build optional sections
    tips_section = ""
    if config.task.tips:
        tips_section = f"\n## Tips\n{config.task.tips}\n"

    # Determine score direction from config or grader type
    score_direction = _get_score_direction(config)

    # Research step is conditional
    research_enabled = config.agents.research
    if research_enabled:
        if config.evaluation.level == "L1":
            objective_guidance = (
                "read the public grader code, understand the objective function, "
                "identify constraints and evaluation criteria"
            )
        else:
            objective_guidance = (
                "read the public eval spec, task description, score feedback, "
                "and allowed A-space materials; do not seek hidden grader internals"
            )
        workflow_summary = "research → plan → edit → eval → repeat"
        research_section = (
            "\n## 1. Research\n\n"
            "**On your first iteration and whenever you're changing direction**, "
            "invest time in research before planning. Register durable references with "
            "`coral kb add external <url-or-path> --kind paper|repo|web|doc|dataset "
            "--title \"...\" --summary \"...\"`.\n\n"
            "**Research steps:**\n"
            f"- **Understand the problem deeply** — {objective_guidance}.\n"
            "- **Survey the literature** — use web search to find state-of-the-art approaches, "
            "academic papers, benchmark comparisons, and existing implementations. "
            'Search broadly first (`"[problem] state of the art"`), then drill into '
            "specific techniques.\n"
            "- **Review domain knowledge** — if the task involves specialized domains "
            "(biology, chemistry, physics, math), research the underlying science. "
            "Understanding the domain often reveals approaches that pure ML/CS thinking misses.\n"
            "- **Analyze existing solutions** — use `coral kb index practice --by score`, "
            "`coral kb index practice --by route`, and `coral kb read <id>` to locate "
            "the relevant practice chain before inspecting code with `coral show <commit> --diff`.\n"
            "- **Compare 2-4 candidate approaches** — document trade-offs, evidence, "
            "and implementation complexity for each.\n"
            "- **Add useful external material** — use `coral kb add external <path-or-url> "
            "--kind paper|repo|web|doc|dataset --title \"...\" --summary \"...\"` so "
            "future agents can discover it through `coral kb index external`.\n\n"
            "**When to research:**\n"
            "- First iteration: always. Understand the landscape before writing code.\n"
            "- After getting stuck (3+ evals without improvement): step back and "
            "look for new angles.\n"
            "- When pivoting to a fundamentally different approach.\n"
            "- When the task involves unfamiliar domain knowledge.\n\n"
            "**When to skip:** If you have a clear plan from your last eval's feedback "
            "and just need to iterate on an existing approach, go straight to Step 2.\n"
        )
        step_offset = 2  # Plan starts at step 2
        research_back_reference = " (or **Step 1: Research** if you need a new direction)"
        repeat_research_hint = (
            "go back to **Step 1: Research** to find new techniques via web search, "
        )
    else:
        workflow_summary = "plan → edit → eval → repeat"
        research_section = ""
        step_offset = 1  # Plan starts at step 1
        research_back_reference = ""
        repeat_research_hint = "research new techniques, "

    rendered = template.format(
        task_name=config.task.name,
        task_description=config.task.description,
        tips_section=tips_section,
        score_direction=score_direction,
        agent_id=agent_id,
        shared_dir=shared_dir,
        workflow_summary=workflow_summary,
        research_section=research_section,
        plan_step_num=step_offset,
        edit_step_num=step_offset + 1,
        eval_step_num=step_offset + 2,
        results_step_num=step_offset + 3,
        knowledge_step_num=step_offset + 4,
        research_back_reference=research_back_reference,
        repeat_research_hint=repeat_research_hint,
    )

    if agent_seed_brief:
        rendered += "\n\n## Codex-prepared runnable initialization bundle\n\n"
        rendered += (
            "Codex prepared your initial technical route before launch. Make the "
            "smallest coherent first change, run the bundled eval script to get "
            "official score evidence, then evolve the route through notebook notes, "
            "practice-chain archives, and indexed knowledge when scores or guardrails point to a better "
            "direction.\n"
        )
        rendered += "\n### Your initialization plan\n\n"
        if agent_seed_brief_path:
            rendered += f"Source: `{agent_seed_brief_path}`\n\n"
        rendered += agent_seed_brief.strip() + "\n"

    return rendered


def _get_score_direction(config: CoralConfig) -> str:
    """Return a human-readable description of what 'better' means for this grader."""
    if config.grader.direction == "minimize":
        return "lower is better"
    return "higher is better"
