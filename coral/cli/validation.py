"""Task validation — checks that a task directory is well-formed.

Called automatically by `coral start` and `coral validate`.
"""

from __future__ import annotations

from pathlib import Path

from coral.config import CoralConfig

REQUIRED_EVAL_SPEC_SECTIONS = (
    "agent api",
    "evaluation level",
    "metrics",
    "acceptance",
    "progress protocol",
    "eval profiles",
    "feedback report",
)

REQUIRED_EVAL_SPEC_CONCEPTS: dict[str, tuple[str, ...]] = {
    "breakthrough metrics": ("breakthrough", "improve", "提升", "突破"),
    "guardrail metrics": ("guardrail", "safety", "correctness", "保底", "兜底", "底线"),
    "anti-cheating checks": (
        "anti-cheat",
        "anti cheating",
        "anti-cheating",
        "cheat",
        "overfit",
        "overfitting",
        "leakage",
        "hidden-data",
        "hidden data",
        "作弊",
        "过拟合",
        "泄漏",
    ),
}


def validate_eval_spec_text(text: str) -> list[str]:
    """Return missing eval-spec contract items for a candidate eval_spec.md."""
    normalized = text.lower()
    missing: list[str] = [
        f"section: {section}"
        for section in REQUIRED_EVAL_SPEC_SECTIONS
        if f"## {section}" not in normalized
    ]
    missing.extend(
        f"concept: {concept}"
        for concept, keywords in REQUIRED_EVAL_SPEC_CONCEPTS.items()
        if not any(keyword in normalized for keyword in keywords)
    )
    return missing


def validate_task(task_dir: Path) -> list[str]:
    """Validate a task directory. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    # 1. task.yaml exists and parses
    task_yaml = task_dir / "task.yaml"
    if not task_yaml.exists():
        errors.append(f"task.yaml not found in {task_dir}")
        return errors  # Can't continue without config

    try:
        config = CoralConfig.from_yaml(task_yaml)
    except Exception as e:
        errors.append(f"task.yaml parse error: {e}")
        return errors

    # 2. grader.entrypoint is set and well-formed.
    if not config.grader.entrypoint:
        errors.append(
            "No grader configured. Set grader.entrypoint = "
            "'your_pkg.module:Grader' in task.yaml and grader.setup to "
            "install the package."
        )
    elif ":" not in config.grader.entrypoint:
        errors.append(
            f"grader.entrypoint must be 'module.path:ClassName', got {config.grader.entrypoint!r}"
        )

    # 3. direction is valid
    if config.grader.direction not in ("maximize", "minimize"):
        errors.append(
            f"grader.direction must be 'maximize' or 'minimize', got '{config.grader.direction}'"
        )

    # 4. Extra private files exist if specified
    for private_path in config.grader.private:
        p = Path(private_path)
        if not p.is_absolute():
            p = task_dir / p
        if not p.exists():
            errors.append(f"Private file not found: {private_path}")

    # 5. If the task ships an eval spec, enforce the standard eval contract
    # sections. Missing eval_spec.md is tolerated for legacy tasks.
    eval_spec = task_dir / "knowledge" / "eval_spec.md"
    if eval_spec.exists():
        missing = validate_eval_spec_text(eval_spec.read_text(errors="ignore"))
        if missing:
            errors.append(
                "knowledge/eval_spec.md is missing required contract item(s): "
                + ", ".join(missing)
            )

    return errors
