"""Commands: init, validate (formerly test-eval)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _module_identifier(name: str) -> str:
    """Sanitize a task directory name into a valid Python module identifier.

    'my-task'      -> 'my_task_grader'
    'My.Task!'     -> 'my_task_grader'
    '123-foo'      -> 'task_123_foo_grader'  (avoid leading digit)
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name).lower().strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "task"
    if cleaned[0].isdigit():
        cleaned = f"task_{cleaned}"
    return f"{cleaned}_grader"


def _distribution_name(name: str) -> str:
    """Sanitize a task directory name into a PEP 503 distribution name.

    'my-task'  -> 'my-task-grader'
    'My.Task!' -> 'my-task-grader'
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", name).lower().strip("-")
    if not cleaned:
        cleaned = "task"
    return f"{cleaned}-grader"


def _create_knowledge_skeleton(path: Path) -> None:
    """Create the default simplified knowledge tree."""
    for subdir in (
        "manuals",
        "external/items",
        "practice/agents",
        "briefs/agent-seeds",
    ):
        (path / subdir).mkdir(parents=True, exist_ok=True)

    (path / "index.md").write_text(
        "# Knowledge Directory\n\n"
        "This directory is an index-first knowledge base. Do not read it as a normal flat folder.\n\n"
        "## Start Here\n"
        "- `eval_spec.md`: the scoring contract and safety rules.\n"
        "- `manuals/`: short framework manuals.\n"
        "- `briefs/agent-seeds/`: Codex-generated starting plan and first eval script for each agent.\n\n"
        "## Two Knowledge Types\n"
        "- External knowledge: papers, repos, docs, datasets, and web references. Indexed by `external/index.jsonl` and stored under `external/items/`.\n"
        "- Practice knowledge: eval-linked notes, routes, score curves, and reflections under `practice/agents/`.\n\n"
        "## Optional Launch Bundles\n"
        "`briefs/agent-seeds/` contains Codex-prepared starting routes and first-eval scripts. It is launch scaffolding, not a third knowledge store.\n\n"
        "## Before And After Launch\n"
        "- Before `coral start`: read these files directly; Codex should fill in `eval_spec.md`, external references, and agent seeds.\n"
        "- After `coral start`: use `coral kb ...` inside the active run/timestamp.\n\n"
        "## Use These Commands After Launch\n"
        "- `coral kb index manual`: show manuals.\n"
        "- `coral kb index external`: show external references.\n"
        "- `coral kb index practice --by score|route|agent|metric`: show run experience by the view you need.\n"
        "- `coral kb read <id>`: open one indexed item.\n"
        "- `coral kb add external <path-or-url> --kind <kind> --title \"...\"`: add a reference.\n"
        "- `coral kb note \"...\"`: add a practice note.\n"
    )
    external_index = path / "external" / "index.jsonl"
    if not external_index.exists():
        external_index.write_text("")
    manuals = {
        "evaluation-spaces.md": (
            "# Evaluation Spaces\n\n"
            "- L1: open A-space scoring.\n"
            "- L2: open A-space exploration and hidden B-space iterative scoring.\n"
            "- L3: open A-space exploration, hidden B-space iteration, sealed C-space final.\n"
        ),
        "submit-system.md": (
            "# Submit System\n\n"
            "`coral eval` stages changes, commits, submits official scoring, and waits by default.\n"
            "Use `coral show <commit> --diff` to inspect an evaluated code change.\n"
        ),
        "knowledge-cli.md": (
            "# Knowledge CLI\n\n"
            "Use `coral kb index ...` first, then `coral kb read <id>`.\n"
        ),
    }
    for filename, content in manuals.items():
        manual = path / "manuals" / filename
        if not manual.exists():
            manual.write_text(content)
    eval_spec = path / "eval_spec.md"
    if not eval_spec.exists():
        eval_spec.write_text(
            "# Eval Spec\n\n"
            "## Agent API\n"
            "- `coral eval -m \"...\"`: submit the current solution for the task's ranking space.\n"
            "- `coral eval --tune -m \"...\"`: optional cheaper scoring for exploration, if supported.\n"
            "- `coral run -- <command>`: run an open A-space exploration script with tracked logs/artifacts, if an isolated or explicitly enabled runner is configured.\n"
            "- Document required files, input/output formats, and forbidden access here.\n\n"
            "## Evaluation Level\n"
            "- L1: A-space scoring is open to agents.\n"
            "- L2: A-space is open exploration; B-space is hidden ranking eval.\n"
            "- L3: A-space is open exploration; B-space is hidden iteration; C-space is sealed final eval.\n\n"
            "## Metrics\n"
            "- Define public metric names, directions, and safe explanations.\n"
            "- Breakthrough metrics define the primary score or research improvement target.\n"
            "- Guardrail metrics define correctness, runtime, memory, safety, and regression constraints.\n"
            "- Anti-cheating checks define leakage, memorization, hidden-data access, and overfitting safeguards.\n"
            "- Do not disclose hidden case IDs, answer keys, private weights, or exploitable scoring details.\n\n"
            "## Acceptance\n"
            "- Define hard requirements such as minimum score, required tests, runtime limit, memory limit, and leakage checks.\n\n"
            "## Progress Protocol\n"
            "- Long evals must call `self.report_progress(...)` so the control panel can render progress.\n\n"
            "## Eval Profiles\n"
            "- quick: same scoring mechanism, fewer cases/seeds, cheaper iteration, higher variance.\n"
            "- medium: stronger signal at moderate cost.\n"
            "- full: main validation profile.\n"
            "- stress: robustness, leakage, or distribution-shift checks.\n\n"
            "## Feedback Report\n"
            "- Successful reports include total score, accepted status, top-5 rank context, self-history, baselines, and per-metric values/ranks.\n"
            "- Failed reports include failure stage, error type, error message, and log path.\n"
        )

def cmd_init(args: argparse.Namespace) -> None:
    """Create a new task directory with a packaged grader.

    Examples:
      coral init my-task            Scaffold at ./my-task/
      coral init my-task --name "My Task"
    """
    task_path = Path(args.path).resolve()
    task_name = args.name or task_path.name
    module_name = _module_identifier(task_path.name)
    dist_name = _distribution_name(task_path.name)

    if task_path.exists() and any(task_path.iterdir()):
        print(f"Error: {task_path} already exists and is not empty.", file=sys.stderr)
        sys.exit(1)

    task_path.mkdir(parents=True, exist_ok=True)
    (task_path / "seed").mkdir()
    _create_knowledge_skeleton(task_path / "knowledge")
    grader_pkg_dir = task_path / "grader" / "src" / module_name
    grader_pkg_dir.mkdir(parents=True)

    (task_path / "task.yaml").write_text(
        f"task:\n"
        f'  name: "{task_name}"\n'
        f"  description: |\n"
        f"    Describe your task here. Agents read this verbatim from CORAL.md.\n"
        f"    Reference the program file by name (solution.py) and describe what\n"
        f'    it must do — e.g. "solution.py must print a single float to stdout".\n'
        f"\n"
        f"evaluation:\n"
        f"  level: L2                 # L1=open A, L2=open A + hidden B, L3=open A + hidden B + sealed C\n"
        f"\n"
        f"grader:\n"
        f'  entrypoint: "{module_name}.grader:Grader"\n'
        f"  setup:\n"
        f'    - "uv pip install -e ./grader"\n'
        f"  timeout: 300\n"
        f"  direction: maximize          # or 'minimize'\n"
        f"  eval_version: eval_v1\n"
        f"  profile: quick\n"
        f"  profiles:\n"
        f"    quick:\n"
        f"      label: Quick iteration\n"
        f"      timeout: 300\n"
        f"      resources:\n"
        f"        cpu_cores: 0\n"
        f"        memory_gb: 0\n"
        f"        gpu_count: 0\n"
        f"      args:\n"
        f"        profile: quick\n"
        f"    full:\n"
        f"      label: Full validation\n"
        f"      timeout: 1200\n"
        f"      resources:\n"
        f"        cpu_cores: 0\n"
        f"        memory_gb: 0\n"
        f"        gpu_count: 0\n"
        f"      args:\n"
        f"        profile: full\n"
        f"  resources:\n"
        f"    cpu_cores: 0              # 0 = unspecified\n"
        f"    memory_gb: 0              # 0 = unspecified\n"
        f"    gpu_count: 0              # 0 = no/unspecified GPU budget\n"
        f"    gpu_ids: []               # e.g. ['0', '1']; sets CUDA_VISIBLE_DEVICES\n"
        f"  args:\n"
        f'    program_file: "solution.py"\n'
        f"\n"
        f"compute:\n"
        f"  backend: local              # local subprocess runner for open A-space jobs\n"
        f"  allow_unisolated_local: false # keep false for L2/L3 unless this is a trusted local-only run\n"
        f"  pool:\n"
        f"    cpu_cores: 0\n"
        f"    memory_gb: 0\n"
        f"    gpu_count: 0\n"
        f"    gpu_ids: []\n"
        f"  classes:\n"
        f"    explore:\n"
        f"      default_profile: cpu-small\n"
        f"      allow_private_data: false\n"
        f"      network: false\n"
        f"      max_running_per_agent: 1\n"
        f"  profiles:\n"
        f"    cpu-small:\n"
        f"      cpu_cores: 2\n"
        f"      memory_gb: 8\n"
        f"      gpu_count: 0\n"
        f"      timeout: 600\n"
        f"    gpu-small:\n"
        f"      cpu_cores: 4\n"
        f"      memory_gb: 24\n"
        f"      gpu_count: 1\n"
        f"      timeout: 1800\n"
        f"\n"
        f"agents:\n"
        f"  count: 1\n"
        f"  runtime: claude_code         # claude_code | codex | cursor | kiro | opencode | 'pkg.module:Cls' for a custom runtime\n"
        f"\n"
        f"knowledge:\n"
        f'  path: "./knowledge"          # copied into each timestamp snapshot\n'
        f"  snapshot: true\n"
        f"\n"
        f"workspace:\n"
        f'  repo_path: "./seed"          # relative to the task directory\n'
        f"\n"
        f"run:\n"
        f"  max_runtime_seconds: 0      # 0 = no run-level wall-clock deadline\n"
    )

    (task_path / "seed" / "solution.py").write_text(
        f'"""Baseline solution for the {task_name} task.\n'
        "\n"
        "The grader runs this file and parses a single floating-point number\n"
        "from stdout as the score. Replace with your real implementation.\n"
        '"""\n'
        "\n"
        "print(0.0)\n"
    )

    (task_path / "grader" / "pyproject.toml").write_text(
        f"[project]\n"
        f'name = "{dist_name}"\n'
        f'version = "0.1.0"\n'
        f'description = "CORAL grader for the {task_name} task."\n'
        f'requires-python = ">=3.11"\n'
        f"dependencies = [\n"
        f'    "coral",\n'
        f"]\n"
        f"\n"
        f"[build-system]\n"
        f'requires = ["hatchling"]\n'
        f'build-backend = "hatchling.build"\n'
        f"\n"
        f"[tool.hatch.build.targets.wheel]\n"
        f'packages = ["src/{module_name}"]\n'
    )

    (grader_pkg_dir / "__init__.py").write_text(
        f'"""{task_name} grader (entrypoint: {module_name}.grader:Grader)."""\n'
        f"\n"
        f"from .grader import Grader\n"
        f"\n"
        f'__all__ = ["Grader"]\n'
    )

    (grader_pkg_dir / "grader.py").write_text(
        f'"""{task_name} grader."""\n'
        f"\n"
        f"from coral.grader import TaskGrader\n"
        f"\n"
        f"\n"
        f"class Grader(TaskGrader):\n"
        f'    """Evaluate agent submissions for the {task_name} task."""\n'
        f"\n"
        f"    def evaluate(self):\n"
        f"        # self.codebase_path  — agent's worktree (read-only; writes are discarded)\n"
        f"        # self.private_dir    — .coral/private/ (hidden answer keys, fixtures)\n"
        f"        # self.args           — dict from task.yaml -> grader.args\n"
        f"        # self.timeout        — eval timeout in seconds\n"
        f"        #\n"
        f"        # Prefer self.report_score(...) / self.fail_report(...) so CORAL can\n"
        f"        # attach rank, top-5, self-history, baseline, and metric reports.\n"
        f'        profile = self.args.get("profile", self.profile)\n'
        f'        self.report_progress(current=0, total=1, phase=profile, message="running seed program")\n'
        f'        program_file = self.args.get("program_file", "solution.py")\n'
        f"        result = self.run_program(program_file)\n"
        f"\n"
        f"        if result.returncode != 0:\n"
        f"            return self.fail_report(\n"
        f'                error_message=f"{{program_file}} crashed: {{result.stderr[:200]}}",\n'
        f'                error_type="runtime_error",\n'
        f'                stage="run_program",\n'
        f"            )\n"
        f"\n"
        f"        try:\n"
        f"            value = float(result.stdout.strip())\n"
        f"        except ValueError:\n"
        f"            return self.fail_report(\n"
        f'                error_message=f"Expected a single float on stdout, got: {{result.stdout[:80]!r}}",\n'
        f'                error_type="invalid_output",\n'
        f'                stage="parse_output",\n'
        f"            )\n"
        f"\n"
        f"        return self.report_score(\n"
        f"            value,\n"
        f'            explanation="Scalar score parsed from solution output.",\n'
        f"            accepted=value >= 0.0,\n"
        f'            acceptance={{"min_score": 0.0, "observed_score": value}},\n'
        f"            metrics={{\n"
        f'                "score": {{\n'
        f'                    "value": value,\n'
        f'                    "direction": self.config.direction,\n'
        f'                    "explanation": "Task scalar score.",\n'
        f"                }},\n"
        f"            }},\n"
        f'            message_for_agent="Use the score, rank, baseline delta, and self-history to decide the next change.",\n'
        f"        )\n"
    )

    print(f"Created task at {task_path}/")
    print("  task.yaml                 — task config + grader entrypoint")
    print("  seed/solution.py          — baseline the agent will iterate on")
    print("  knowledge/                — eval spec, manuals, external index, practice memory")
    print(f"  grader/                   — packaged grader ({dist_name})")
    print(f"  grader/src/{module_name}/grader.py")
    print("\nNext:")
    print(f"  cd {task_path.name}")
    print("  coral validate .          # bootstraps grader venv + runs grader on seed/")
    print("  coral prepare -c task.yaml")
    print("  coral validate --run-dir results/<task>/latest/.coral")
    print("  coral start -c results/<task>/latest/.coral/config.yaml")


def cmd_validate(args: argparse.Namespace) -> None:
    """Test your grader against seed code.

    Examples:
      coral validate my-task        Dry-run the grader in my-task/
      coral validate --run-dir .coral
                                    Check prepared timestamp readiness.
    """
    import shutil
    import tempfile

    from coral.cli.validation import validate_task
    from coral.config import CoralConfig

    run_dir_arg = getattr(args, "run_dir", None)
    if run_dir_arg:
        _cmd_validate_run_dir(Path(run_dir_arg).expanduser().resolve())
        return

    if not getattr(args, "path", None):
        print("Error: provide a task path or --run-dir", file=sys.stderr)
        sys.exit(2)

    task_dir = Path(args.path).resolve()

    errors = validate_task(task_dir)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    print("Validation: OK")

    config = CoralConfig.from_yaml(task_dir / "task.yaml")

    with tempfile.TemporaryDirectory(prefix="coral_test_eval_") as tmpdir:
        tmpdir = Path(tmpdir)
        workspace = tmpdir / "workspace"
        workspace.mkdir()

        seed_dir = task_dir / "seed"
        if seed_dir.is_dir() and any(seed_dir.iterdir()):
            for item in seed_dir.iterdir():
                if item.name == "__pycache__":
                    continue
                dst = workspace / item.name
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
            print(f"Seed: copied {seed_dir.name}/ into workspace")
        else:
            print("Warning: No seed/ directory — grader will run against an empty workspace.")
            print("  This is fine if your task expects agents to build from scratch.")

        coral_dir = tmpdir / ".coral"
        private_dir = coral_dir / "private"
        private_dir.mkdir(parents=True)

        for private_path_str in config.grader.private:
            src = Path(private_path_str)
            if not src.is_absolute():
                src = (task_dir / src).resolve()
            if src.exists():
                dst = private_dir / src.name
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

        # Bootstrap the grader's isolated venv where the entrypoint runs.
        from coral.workspace.grader_env import setup_grader_env

        print("Setting up grader venv (.coral/private/grader_venv)...")
        setup_grader_env(coral_dir, config.grader, task_dir)

        from coral.grader.loader import load_grader
        from coral.types import Task

        try:
            grader = load_grader(config, coral_dir)
        except Exception as e:
            print(f"Error loading grader: {e}", file=sys.stderr)
            sys.exit(1)

        task = Task(
            id=config.task.name,
            name=config.task.name,
            description=config.task.description,
        )

        print(
            f"\nRunning grader against {'seed code' if seed_dir.is_dir() else 'empty workspace'}..."
        )
        import asyncio

        try:
            result = asyncio.run(grader.grade(str(workspace), [task]))
            score = result.aggregated
            print(f"\n{'=' * 50}")
            print(f"Score: {score}")
            if result.scores:
                for name, s in result.scores.items():
                    if s.explanation:
                        print(f"  {name}: {s.explanation}")
            print(f"{'=' * 50}")
        except Exception as e:
            print(f"\nGrader crashed: {e}", file=sys.stderr)
            sys.exit(1)


def _cmd_validate_run_dir(coral_dir: Path) -> None:
    """Validate a prepared timestamp .coral directory for workbench launch."""
    from coral.hub.readiness import build_control_readiness

    if coral_dir.name != ".coral":
        candidate = coral_dir / ".coral"
        if candidate.is_dir():
            coral_dir = candidate
    if not coral_dir.is_dir():
        print(f"Error: run directory not found: {coral_dir}", file=sys.stderr)
        sys.exit(1)

    readiness = build_control_readiness(coral_dir)
    status = str(readiness.get("status") or "missing")
    print(f"Readiness: {status.upper()}")
    for check in readiness.get("checks", []):
        check_status = str(check.get("status") or "missing").upper()
        label = str(check.get("label") or check.get("id") or "check")
        detail = str(check.get("detail") or "")
        print(f"  [{check_status}] {label}: {detail}")
        path = check.get("path")
        if path:
            print(f"      {path}")

    if status == "missing":
        print("\nRun readiness is missing required Codex-prepared artifacts.", file=sys.stderr)
        sys.exit(1)
    if status == "warning":
        print("\nRun readiness has warnings; review before launching.")
    else:
        print("\nRun readiness: OK")
