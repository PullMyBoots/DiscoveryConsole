"""CORAL CLI — clean, grouped command-line interface."""

from __future__ import annotations

import argparse
import difflib
import sys


class _GroupedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Custom formatter that suppresses the default subcommand list.

    We print our own grouped help in the epilog instead.
    """

    def _format_usage(self, usage, actions, groups, prefix):
        # Show clean usage without the giant {cmd1,cmd2,...} list
        return "usage: coral <command> [options]\n"

    def _format_action(self, action: argparse.Action) -> str:
        # Hide the auto-generated subcommand choices list
        if isinstance(action, argparse._SubParsersAction):
            return ""
        return super()._format_action(action)


class _CommandHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Formatter for individual commands — preserves docstring examples."""

    pass


class _HelpOnErrorParser(argparse.ArgumentParser):
    """ArgumentParser that prints help alongside error messages."""

    def error(self, message: str) -> None:
        sys.stderr.write(f"\nerror: {message}\n\n")
        self.print_help(sys.stderr)
        sys.exit(2)


# All visible commands for "did you mean?" suggestions
_VISIBLE_COMMANDS = [
    "init",
    "validate",
    "prepare",
    "start",
    "resume",
    "stop",
    "status",
    "log",
    "show",
    "kb",
    "skills",
    "runs",
    "ui",
    "run",
    "eval",
    "wait",
    "diff",
    "revert",
    "checkout",
]


class _MainParser(_HelpOnErrorParser):
    """Top-level parser with 'did you mean?' suggestions for unknown commands."""

    def error(self, message: str) -> None:
        # Check for unknown command and suggest closest match
        if "invalid choice:" in message:
            # Extract the bad command from the error message
            try:
                bad_cmd = message.split("'")[1]
            except IndexError:
                bad_cmd = None
            if bad_cmd:
                matches = difflib.get_close_matches(bad_cmd, _VISIBLE_COMMANDS, n=3, cutoff=0.5)
                sys.stderr.write(f"\nerror: unknown command '{bad_cmd}'\n")
                if matches:
                    sys.stderr.write("\nDid you mean?\n")
                    for m in matches:
                        sys.stderr.write(f"  coral {m}\n")
                sys.stderr.write("\n")
                self.print_help(sys.stderr)
                sys.exit(2)
        super().error(message)


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    """Add the common --task and --run flags."""
    parser.add_argument("--task", help="Task name (auto-detected if omitted)")
    parser.add_argument("--run", help="Run ID (defaults to latest)")


def main() -> None:
    from coral import __version__

    epilog = """\
Getting Started:
  init            Create a new task directory
  validate        Test your grader against seed code
  prepare         Create a timestamp run and agent workspaces

Running Agents:
  start           Launch agents from a prepared run
  resume          Resume a previous run
  stop            Shut down running agents
  status          Show agent health and leaderboard

Inspecting Results:
  log             List and search attempts (leaderboard)
  show            Show details of a specific attempt
  kb              Index-first knowledge lookup and maintenance
  skills          Browse shared skills
  runs            List runs (active only; --all for stopped)

Dashboard:
  ui              Launch the web dashboard

Agent Internals:
  run             Run an open A-space script with job logs/artifacts
  eval            Stage, commit, and evaluate changes
  wait            Wait for a submitted eval's score
  diff            Show uncommitted changes
  revert          Undo the last commit
  checkout        Reset to a previous attempt

Run 'coral <command> --help' for details on any command."""

    parser = _MainParser(
        prog="coral",
        description=f"CORAL v{__version__} \u2014 Autonomous agent orchestration",
        epilog=epilog,
        formatter_class=_GroupedHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"coral {__version__}")
    sub = parser.add_subparsers(dest="command", prog="coral")

    # --- Getting Started ---

    p_init = sub.add_parser(
        "init",
        help="Create a new task directory",
        description="Create a new task directory with scaffolded config and grader.",
        epilog="Examples:\n  coral init my-task\n  coral init my-task --name 'My Task'",
        formatter_class=_CommandHelpFormatter,
    )
    p_init.add_argument("path", help="Path for the new task directory")
    p_init.add_argument("--name", help="Task name (default: directory name)")

    p_validate = sub.add_parser(
        "validate",
        help="Test your grader against seed code",
        description="Validate task structure, or validate a prepared timestamp run directory.",
        epilog=(
            "Examples:\n"
            "  coral validate my-task\n"
            "  coral validate --run-dir results/my-task/latest/.coral"
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_validate.add_argument("path", nargs="?", help="Path to the task directory")
    p_validate.add_argument(
        "--run-dir",
        help="Path to a prepared timestamp .coral directory for workbench readiness checks",
    )
    # Hidden alias: test-eval -> validate
    sub.add_parser("test-eval", help=argparse.SUPPRESS)

    # --- Running Agents ---

    p_prepare = sub.add_parser(
        "prepare",
        help="Create a prepared run workspace",
        description=(
            "Create a timestamp run, shared state, repo clone, and per-agent "
            "workspaces without launching agents."
        ),
        epilog=(
            "Examples:\n"
            "  coral prepare -c task.yaml\n"
            "  coral prepare -c task.yaml agents.count=4"
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_prepare.add_argument("--config", "-c", required=True, help="Path to task config YAML")
    p_prepare.add_argument(
        "overrides",
        nargs="*",
        default=[],
        help="Config overrides as key=value (e.g. agents.count=4 run.verbose=true)",
    )

    p_start = sub.add_parser(
        "start",
        help="Launch agents from a prepared run",
        description="Launch autonomous agents from a prepared timestamp run.",
        epilog=(
            "Examples:\n"
            "  coral start -c results/my-task/latest/.coral/config.yaml\n"
            "  coral start -c results/my-task/2026-06-26_120000/.coral/config.yaml\n"
            "  coral start -c results/my-task/latest/.coral/config.yaml run.ui=true"
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_start.add_argument(
        "--config",
        "-c",
        required=True,
        help="Path to a prepared run's .coral/config.yaml",
    )
    p_start.add_argument(
        "overrides",
        nargs="*",
        default=[],
        help=(
            "Runtime-safe overrides as key=value "
            "(e.g. run.verbose=true run.session=local agents.model=opus)"
        ),
    )

    p_resume = sub.add_parser(
        "resume",
        help="Resume a previous run",
        description="Resume agents from a previous run, restoring their sessions.",
        epilog="Examples:\n  coral resume\n  coral resume --task my-task agents.model=opus",
        formatter_class=_CommandHelpFormatter,
    )
    _add_run_args(p_resume)
    p_resume.add_argument(
        "--instruction",
        "-i",
        type=str,
        default=None,
        help="Additional instruction to inject into agents at resume time",
    )
    p_resume.add_argument(
        "--instruction-file",
        type=str,
        default=None,
        help="Read additional resume instruction from a file",
    )
    p_resume.add_argument(
        "overrides",
        nargs="*",
        default=[],
        help="Runtime-safe overrides as key=value (e.g. agents.model=opus run.verbose=true)",
    )

    p_stop = sub.add_parser(
        "stop",
        help="Shut down running agents",
        description="Gracefully stop the CORAL manager and all agents.",
        formatter_class=_CommandHelpFormatter,
    )
    p_stop.add_argument("--all", action="store_true", help="Stop all active runs")
    _add_run_args(p_stop)

    p_status = sub.add_parser(
        "status",
        help="Show agent health and leaderboard",
        description="Show manager/agent status and top leaderboard entries.",
        formatter_class=_CommandHelpFormatter,
    )
    p_status.add_argument(
        "--all",
        action="store_true",
        help="Include tune-mode and grader-error attempts in the body summary "
        "and leaderboard (hidden by default, matching `coral log`)",
    )
    _add_run_args(p_status)

    # --- Inspecting Results ---

    p_log = sub.add_parser(
        "log",
        help="List and search attempts (leaderboard)",
        description="List and search attempts. Default: top 20 sorted by score.",
        epilog=(
            "Examples:\n"
            "  coral log                     Top 20 by score\n"
            "  coral log -n 5                Top 5\n"
            "  coral log --recent            Sort by time instead of score\n"
            "  coral log --agent agent-1     Filter by agent\n"
            "  coral log --search 'kernel'   Full-text search\n"
            "  coral log --all               Include tune + grader_error attempts\n"
            "  coral log --class tune        Show only tune-mode attempts"
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_log.add_argument(
        "-n", "--count", type=int, default=20, help="Number of results (default: 20)"
    )
    p_log.add_argument("--recent", action="store_true", help="Sort by time instead of score")
    p_log.add_argument("--agent", help="Filter by agent ID")
    p_log.add_argument("--search", help="Full-text search")
    g_class = p_log.add_mutually_exclusive_group()
    g_class.add_argument(
        "--all",
        action="store_true",
        help="Include tune-mode and grader-error attempts (hidden by default)",
    )
    g_class.add_argument(
        "--class",
        dest="budget_class",
        choices=("real", "tune", "grader_error"),
        help="Show only attempts of this budget class (mutually exclusive with --all)",
    )
    _add_run_args(p_log)
    # Hidden alias: attempts -> log
    p_attempts_alias = sub.add_parser("attempts", help=argparse.SUPPRESS)
    p_attempts_alias.add_argument("--top", type=int, help=argparse.SUPPRESS)
    p_attempts_alias.add_argument("--recent", type=int, help=argparse.SUPPRESS)
    p_attempts_alias.add_argument("--agent", help=argparse.SUPPRESS)
    p_attempts_alias.add_argument("--search", help=argparse.SUPPRESS)
    _add_run_args(p_attempts_alias)

    p_show = sub.add_parser(
        "show",
        help="Show details of a specific attempt",
        description="Show full details and diff for a specific attempt.",
        epilog="Examples:\n  coral show abc123\n  coral show <full-commit-hash>",
        formatter_class=_CommandHelpFormatter,
    )
    p_show.add_argument("hash", help="Commit hash or prefix")
    p_show.add_argument(
        "--diff", action="store_true", default=False, help="Show full code diff instead of summary"
    )
    _add_run_args(p_show)
    # Hidden alias: attempt -> show
    p_attempt_alias = sub.add_parser("attempt", help=argparse.SUPPRESS)
    p_attempt_alias.add_argument("hash", help=argparse.SUPPRESS)
    p_attempt_alias.add_argument(
        "--diff", action="store_true", default=False, help=argparse.SUPPRESS
    )
    _add_run_args(p_attempt_alias)

    p_kb = sub.add_parser(
        "kb",
        help="Index-first knowledge lookup and maintenance",
        description=(
            "Query and maintain CORAL knowledge through a small controlled CLI.\n"
            "Use `index` first, then `read <id>`; avoid browsing knowledge files directly."
        ),
        epilog=(
            "Examples:\n"
            "  coral kb index manual\n"
            "  coral kb index external\n"
            "  coral kb index practice --by score\n"
            "  coral kb index practice --by route\n"
            "  coral kb read src-001\n"
            "  coral kb add external ./paper.pdf --kind paper --title \"Paper\" --summary \"...\"\n"
            "  coral kb remove src-001\n"
            "  coral kb note \"batch 128 needs warmup\" --tag training\n"
            "  coral kb archive --attempt abc123 --route \"cache-aware batching\""
        ),
        formatter_class=_CommandHelpFormatter,
    )
    _add_run_args(p_kb)
    kb_sub = p_kb.add_subparsers(dest="kb_action")

    kb_index = kb_sub.add_parser("index", help="Show an index before reading details")
    kb_index.add_argument("space", choices=["manual", "external", "practice"])
    kb_index.add_argument(
        "--by",
        choices=["score", "route", "agent", "metric"],
        default="score",
        help="Practice index view (default: score)",
    )
    kb_index.add_argument("--metric", help="Metric name for --by metric")
    kb_index.add_argument("--agent", help="Filter practice index by agent")
    kb_index.add_argument(
        "--direction",
        choices=["maximize", "minimize"],
        default=None,
        help="Score direction for ranking practice knowledge (default: grader.direction)",
    )
    kb_index.add_argument("--all", action="store_true", help="Include archived external sources")
    _add_run_args(kb_index)

    kb_read = kb_sub.add_parser("read", help="Read one indexed knowledge item")
    kb_read.add_argument("id", help="manual-..., src-..., node-..., or route-...")
    _add_run_args(kb_read)

    kb_add = kb_sub.add_parser("add", help="Add an external knowledge source")
    kb_add.add_argument("space", choices=["external"])
    kb_add.add_argument("source", help="Path, directory, or URL")
    kb_add.add_argument("--kind", choices=["paper", "repo", "web", "doc", "dataset", "other"], default="other")
    kb_add.add_argument("--title", required=True)
    kb_add.add_argument("--summary", default="")
    kb_add.add_argument("--tags", help="Comma-separated tags")
    kb_add.add_argument("--by", help="Creator name (default: agent id or user)")
    kb_add.add_argument("--workdir", help="Working directory for agent id detection")
    _add_run_args(kb_add)

    kb_remove = kb_sub.add_parser("remove", help="Archive an external source")
    kb_remove.add_argument("id", help="Source id, e.g. src-001")
    kb_remove.add_argument("--by", help="Remover name (default: agent id or user)")
    kb_remove.add_argument("--workdir", help="Working directory for agent id detection")
    _add_run_args(kb_remove)

    kb_note = kb_sub.add_parser("note", help="Append a short note to the current agent notebook")
    kb_note.add_argument("text")
    kb_note.add_argument("--tag", default="")
    kb_note.add_argument("--agent", help="Agent ID (default: read .coral_agent_id)")
    kb_note.add_argument("--workdir", help="Working directory for agent id detection")
    _add_run_args(kb_note)

    kb_notebook = kb_sub.add_parser("notebook", help="Read or reset the current agent notebook")
    kb_notebook.add_argument("--agent", help="Agent ID (default: read .coral_agent_id)")
    kb_notebook.add_argument("--set", help="Replace notebook content from this markdown file")
    kb_notebook.add_argument("--reason", default="external-adjustment", help="Archive reason for --set")
    kb_notebook.add_argument("--by", help="Actor name for --set archive metadata")
    kb_notebook.add_argument("--workdir", help="Working directory for agent id detection")
    _add_run_args(kb_notebook)

    kb_archive = kb_sub.add_parser("archive", help="Archive an eval into the agent practice chain")
    kb_archive.add_argument("--attempt", required=True, help="Attempt commit hash or prefix")
    kb_archive.add_argument("--agent", help="Agent ID (default: read .coral_agent_id)")
    kb_archive.add_argument("--route", default="", help="Technical route label")
    kb_archive.add_argument("--method", default="", help="Short method summary")
    kb_archive.add_argument("--method-file", help="Read method summary from a markdown file")
    kb_archive.add_argument("--reflection", default="", help="Reflect-loop note")
    kb_archive.add_argument("--reflection-file", help="Read reflect-loop note from a markdown file")
    kb_archive.add_argument("--next-plan", help="Markdown file used to reset the notebook")
    kb_archive.add_argument("--workdir", help="Working directory for agent id detection")
    _add_run_args(kb_archive)

    p_skills = sub.add_parser(
        "skills",
        help="Browse shared skills",
        description="List skills or show details of a specific skill.",
        epilog="Examples:\n  coral skills\n  coral skills --read optimizer",
        formatter_class=_CommandHelpFormatter,
    )
    p_skills.add_argument("--read", "-r", help="Show details of a skill (name or prefix)")
    _add_run_args(p_skills)

    p_runs = sub.add_parser(
        "runs",
        help="List all runs across tasks",
        description="List all CORAL runs. Default: active runs only, most recent first.",
        epilog=(
            "Examples:\n"
            "  coral runs                    Active runs only\n"
            "  coral runs --all              Include stopped runs\n"
            "  coral runs --task my-task     Filter by task\n"
            "  coral runs -n 5              Show at most 5 runs"
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_runs.add_argument("--all", "-a", action="store_true", help="Include stopped runs")
    p_runs.add_argument("--task", "-t", help="Filter by task name")
    p_runs.add_argument(
        "-n", "--count", type=int, default=20, help="Number of results (default: 20)"
    )
    p_runs.add_argument("--verbose", "-v", action="store_true", help="Show full paths")

    # --- Dashboard ---

    p_ui = sub.add_parser(
        "ui",
        help="Launch the web dashboard",
        description="Start the CORAL web dashboard for monitoring runs.",
        epilog="Examples:\n  coral ui\n  coral ui --port 9000",
        formatter_class=_CommandHelpFormatter,
    )
    p_ui.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    p_ui.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    _add_run_args(p_ui)
    p_ui.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    # --- Agent Internals ---

    p_run = sub.add_parser(
        "run",
        help="Run an open A-space script",
        description=(
            "Run a command as an open A-space compute job. CORAL records the\n"
            "job, injects resource/profile environment variables, writes logs,\n"
            "and exposes artifacts under public/jobs/<job_id>/artifacts.\n"
            "The local backend is disabled by default for hidden L2/L3 tasks\n"
            "because same-user subprocesses cannot isolate .coral/private."
        ),
        epilog=(
            "Examples:\n"
            "  coral run -- python scripts/probe.py\n"
            "  coral run --profile cpu-large -- python experiments/check.py\n"
            "  coral run --profile gpu-small --timeout 1800 -- python train_probe.py"
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_run.add_argument("--agent", help="Agent ID (default: read from .coral_agent_id)")
    p_run.add_argument("--workdir", help="Working directory (default: cwd)")
    p_run.add_argument(
        "--class",
        dest="job_class",
        default="explore",
        help="Compute job class (default: explore)",
    )
    p_run.add_argument("--profile", help="Compute profile (default: class default)")
    p_run.add_argument("--timeout", type=int, default=None, help="Override profile timeout seconds")
    p_run.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")

    p_eval = sub.add_parser(
        "eval",
        help="Stage, commit, and evaluate changes",
        description=(
            "Stage all changes, commit with a message, and submit for grading.\n"
            "By default blocks until the grader daemon returns a score.\n"
            "Use --no-wait to return immediately with a pending status and\n"
            "poll later via `coral wait <hash>`."
        ),
        epilog=(
            "Examples:\n"
            '  coral eval -m "Optimized inner loop"\n'
            '  coral eval -m "Try variant A" --no-wait\n'
            '  coral eval -m "Heavy benchmark" --timeout 1800\n'
            '  coral eval --tune -m "Sweep lr=1e-3 vs 3e-4"\n'
            '  coral eval --final -m "Final sealed validation"'
        ),
        formatter_class=_CommandHelpFormatter,
    )
    p_eval.add_argument(
        "-m", "--message", required=True, help="Description of what you changed and why"
    )
    p_eval.add_argument("--agent", help="Agent ID (default: read from .coral_agent_id)")
    p_eval.add_argument("--workdir", help="Working directory (default: cwd)")
    p_eval.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wait for grader to return a score (default). Use --no-wait to return immediately.",
    )
    p_eval.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds to wait for grader (default: derived from grader.timeout).",
    )
    p_eval.add_argument(
        "--tune",
        action="store_true",
        default=False,
        help=(
            "Submit as a tune-mode attempt: scored and recorded normally, but "
            "excluded from the real-eval reflect_loop archive trigger. Use for "
            "hyperparameter sweeps and config exploration."
        ),
    )
    p_eval.add_argument(
        "--final",
        action="store_true",
        default=False,
        help=(
            "Submit an L3 sealed C-space final evaluation. Disabled unless "
            "evaluation.allow_loop_final=true; C-space is normally run outside "
            "the agent search loop."
        ),
    )

    p_wait = sub.add_parser(
        "wait",
        help="Wait for a submitted eval's score",
        description=(
            "Block until the grader daemon finalizes a previously submitted\n"
            "attempt (e.g. one submitted with `coral eval --no-wait`)."
        ),
        epilog="Examples:\n  coral wait abc123\n  coral wait abc123 --timeout 600",
        formatter_class=_CommandHelpFormatter,
    )
    p_wait.add_argument("hash", help="Commit hash or prefix of the attempt to wait on")
    p_wait.add_argument("--workdir", help="Working directory (default: cwd)")
    p_wait.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds to wait (default: derived from grader.timeout).",
    )

    p_diff = sub.add_parser(
        "diff",
        help="Show uncommitted changes",
        description="Show staged and unstaged changes in the working tree.",
        formatter_class=_CommandHelpFormatter,
    )
    p_diff.add_argument("--workdir", help="Working directory (default: cwd)")

    p_revert = sub.add_parser(
        "revert",
        help="Undo the last commit",
        description="Reset to HEAD~1, discarding the last commit and its changes.",
        formatter_class=_CommandHelpFormatter,
    )
    p_revert.add_argument("--workdir", help="Working directory (default: cwd)")

    p_checkout = sub.add_parser(
        "checkout",
        help="Reset to a previous attempt",
        description="Reset the working tree to a previous attempt's commit.",
        epilog="Examples:\n  coral checkout abc123",
        formatter_class=_CommandHelpFormatter,
    )
    p_checkout.add_argument("hash", help="Commit hash or prefix")
    p_checkout.add_argument("--workdir", help="Working directory (default: cwd)")
    _add_run_args(p_checkout)

    # --- Parse and dispatch ---

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Lazy imports for fast startup
    from coral.cli.author import cmd_init, cmd_validate
    from coral.cli.eval import cmd_checkout, cmd_diff, cmd_eval, cmd_revert, cmd_wait
    from coral.cli.kb import cmd_kb
    from coral.cli.query import cmd_log, cmd_runs, cmd_show, cmd_skills
    from coral.cli.run import cmd_run
    from coral.cli.start import cmd_prepare, cmd_resume, cmd_start, cmd_status, cmd_stop
    from coral.cli.ui import cmd_ui

    commands = {
        "start": cmd_start,
        "prepare": cmd_prepare,
        "resume": cmd_resume,
        "stop": cmd_stop,
        "status": cmd_status,
        "run": cmd_run,
        "eval": cmd_eval,
        "wait": cmd_wait,
        "revert": cmd_revert,
        "checkout": cmd_checkout,
        "diff": cmd_diff,
        "log": cmd_log,
        "show": cmd_show,
        "kb": cmd_kb,
        "skills": cmd_skills,
        "runs": cmd_runs,
        "init": cmd_init,
        "validate": cmd_validate,
        "ui": cmd_ui,
        # Hidden aliases for backward compatibility
        "attempts": _cmd_attempts_compat,
        "attempt": cmd_show,
        "test-eval": cmd_validate,
    }
    commands[args.command](args)


def _cmd_attempts_compat(args: argparse.Namespace) -> None:
    """Backward-compatible wrapper: translates old attempts flags to new log flags."""
    from coral.cli.query import cmd_log

    # Map old --top N and --recent N to new --count N and --recent (bool)
    if hasattr(args, "top") and args.top:
        args.count = args.top
    elif not hasattr(args, "count") or args.count is None:
        args.count = 20

    if hasattr(args, "recent") and isinstance(args.recent, int) and args.recent:
        args.count = args.recent
        args.recent = True
    elif not hasattr(args, "recent") or args.recent is None:
        args.recent = False

    cmd_log(args)


if __name__ == "__main__":
    main()
