from __future__ import annotations

import argparse

from coral.cli.start import _read_resume_instruction, _task_dir_for_start_config


def test_read_resume_instruction_combines_inline_and_file(tmp_path):
    instruction_file = tmp_path / "next_instruction.md"
    instruction_file.write_text("Use the new guardrail metric.")

    instruction = _read_resume_instruction(
        argparse.Namespace(
            instruction="Continue from the current best attempt.",
            instruction_file=str(instruction_file),
        )
    )

    assert instruction == (
        "Continue from the current best attempt.\n\n"
        "Use the new guardrail metric."
    )


def test_read_resume_instruction_returns_none_when_empty(tmp_path):
    instruction_file = tmp_path / "next_instruction.md"
    instruction_file.write_text("\n")

    instruction = _read_resume_instruction(
        argparse.Namespace(instruction=None, instruction_file=str(instruction_file))
    )

    assert instruction is None


def test_task_dir_for_start_config_uses_saved_config_dir_for_coral_config(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    coral_dir = tmp_path / "results" / "my-task" / "run-1" / ".coral"
    coral_dir.mkdir(parents=True)
    (coral_dir / "config_dir").write_text(str(task_dir))

    assert _task_dir_for_start_config(coral_dir / "config.yaml") == task_dir.resolve()
