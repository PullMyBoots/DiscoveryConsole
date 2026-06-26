"""Command: run open A-space scripts under CORAL job tracking."""

from __future__ import annotations

import argparse
import sys

from coral.compute.runner import run_compute_job


def _strip_command_separator(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


def cmd_run(args: argparse.Namespace) -> None:
    """Run an agent-requested open A-space compute job."""
    command = _strip_command_separator(list(args.command or []))
    if not command:
        print("Error: coral run requires a command after --", file=sys.stderr)
        sys.exit(2)

    try:
        job = run_compute_job(
            command=command,
            workdir=args.workdir or ".",
            agent_id=args.agent,
            job_class=args.job_class,
            profile_name=args.profile,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"CORAL Run: {job.status}")
    print(f"Job:      {job.job_id}")
    print(f"Class:    {job.job_class}")
    print(f"Profile:  {job.profile}")
    print(f"Command:  {' '.join(job.command)}")
    print(f"Stdout:   {job.stdout_path}")
    print(f"Stderr:   {job.stderr_path}")
    print(f"Artifacts:{job.artifact_dir}")
    if job.error:
        print(f"Error:    {job.error}")

    if job.status == "timeout":
        sys.exit(124)
    if job.status != "succeeded":
        sys.exit(job.exit_code or 1)
