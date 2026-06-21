"""TaskGrader base class — the single way to write graders for CORAL tasks.

Task authors ship a small grader package (referenced by ``grader.entrypoint``
in task.yaml) with a class inheriting from TaskGrader and implementing
evaluate():

    from coral.grader import TaskGrader

    class Grader(TaskGrader):
        def evaluate(self) -> float:
            return 0.85
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coral.config import GraderConfig, GraderProfileConfig, ResourceConfig
from coral.types import BUDGET_CLASS_TUNE, Score, ScoreBundle, Task, get_budget_class

DEFAULT_TUNE_DESCRIPTION = (
    "This grader does not differentiate tune mode from a real "
    "submission: scoring runs the full evaluation either way and "
    "returns the same score it would return without `--tune`. "
    "The flag's only effect is budget classification — tune "
    "attempts do not count against the plateau / heartbeat budget."
)


class TaskGrader(ABC):
    """Base class for task graders.

    Subclasses implement evaluate() and return a float or ScoreBundle.
    The framework sets codebase_path, private_dir, config, args, tasks,
    and island_id before calling.
    """

    codebase_path: str
    private_dir: str
    config: GraderConfig
    tasks: list[Task]
    island_id: str | int | None

    def __init__(self, config: GraderConfig) -> None:
        self.config = config
        self.tasks = []
        self._resource_override: ResourceConfig | None = None

    @property
    def args(self) -> dict[str, Any]:
        """Grader-specific args with the selected profile layered on top."""
        args = dict(self.config.args)
        profile = self.profile_config
        if profile is not None:
            args.update(profile.args)
        return args

    @property
    def profile(self) -> str:
        """Selected evaluation profile name, e.g. quick/medium/full/stress."""
        return self.config.profile

    @property
    def eval_version(self) -> str:
        """Evaluation version that produced this attempt's score."""
        return self.config.eval_version

    @property
    def profile_config(self) -> GraderProfileConfig | None:
        """Selected profile config, or None when no profile block is defined."""
        return self.config.profiles.get(self.profile)

    @property
    def resources(self) -> ResourceConfig:
        """Resource budget for this eval, with profile overrides applied."""
        if self._resource_override is not None:
            return self._resource_override
        base = self.config.resources
        profile = self.profile_config
        if profile is None or not profile.resources.active():
            return base
        override = profile.resources
        return ResourceConfig(
            cpu_cores=override.cpu_cores or base.cpu_cores,
            memory_gb=override.memory_gb or base.memory_gb,
            gpu_count=override.gpu_count or base.gpu_count,
            gpu_ids=override.gpu_ids or base.gpu_ids,
        )

    def resource_env(self) -> dict[str, str]:
        """Environment variables that expose the eval resource budget."""
        env = self.resources.to_env()
        cpu = env.get("CORAL_CPU_CORES")
        if cpu:
            env.setdefault("OMP_NUM_THREADS", cpu)
            env.setdefault("MKL_NUM_THREADS", cpu)
            env.setdefault("OPENBLAS_NUM_THREADS", cpu)
            env.setdefault("NUMEXPR_NUM_THREADS", cpu)
        return env

    def subprocess_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Base environment for grader-launched subprocesses."""
        env = dict(os.environ)
        env.update(self.resource_env())
        if extra:
            env.update(extra)
        return env

    @property
    def timeout(self) -> int | None:
        """Eval timeout in seconds, from grader config. None means no limit."""
        profile = self.profile_config
        if profile is not None and profile.timeout > 0:
            return profile.timeout
        return self.config.timeout or None

    @property
    def budget_class(self) -> str:
        """The pending attempt's budget class.

        Only "real" or "tune" from inside the grader — "grader_error" is
        stamped by the daemon *after* grading (timeout / exception).
        """
        return get_budget_class(self.tasks[0].metadata if self.tasks else None)

    @property
    def tune(self) -> bool:
        """True if this attempt was submitted with `coral eval --tune`.

        Use this to switch your grader to a cheaper local target — a smaller
        eval slice, dev split, or smoke harness — when the agent is sweeping
        hyperparameters rather than making a real submission. Tune-mode
        attempts don't count against the plateau / heartbeat budget.
        """
        return self.budget_class == BUDGET_CLASS_TUNE

    def describe_tune(self) -> str:
        """Override to describe what `--tune` does on this grader.

        Prepended to the eval feedback whenever ``self.tune`` is true, so
        the agent learns whether tune mode uses a cheaper target or is
        identical to a real eval.
        """
        return DEFAULT_TUNE_DESCRIPTION

    @property
    def eval_logs_dir(self) -> Path:
        """Per-attempt directory for eval artifacts that should outlive the grader.

        The grader runs in an isolated checkout that the daemon force-removes
        after each eval (see coral/grader/daemon.py:_remove_worktree), so
        anything written under self.codebase_path is lost. Use this dir for
        subprocess logs, terminal recordings, traces, etc. the agent should
        be able to inspect after the eval finishes.

        Path (single-island): .coral/public/eval_logs/<checkout_dir_name>/
        Path (multi-island):  .coral/islands/<island_id>/eval_logs/<checkout_dir_name>/
        (= attempt commit hash when invoked by the grader daemon)

        Symlinked into each agent worktree at `<worktree>/.claude/eval_logs/`
        by setup_shared_state, so the multi-island branch keeps eval logs
        island-scoped (consistent with attempts/skills/notes/etc.).
        """
        coral_root = Path(self.private_dir).parent
        island_id = getattr(self, "island_id", None)
        if island_id is not None:
            d = (
                coral_root
                / "islands"
                / str(island_id)
                / "eval_logs"
                / Path(self.codebase_path).name
            )
        else:
            d = coral_root / "public" / "eval_logs" / Path(self.codebase_path).name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def eval_logs_worktree_path(self, abs_path: Path) -> Path:
        """Return an eval_logs absolute path as `eval_logs/<...>` (runtime-agnostic).

        The grader's eval_logs dir is symlinked into each agent worktree under
        the runtime's shared state dir (e.g. `.claude/eval_logs/`,
        `.codex/eval_logs/`, ...). Print the no-prefix form so agents on any
        runtime can prepend their own shared dir to access it via Read.

        Falls back to the original absolute path if it isn't under eval_logs/.
        """
        parts = Path(abs_path).parts
        try:
            idx = parts.index("eval_logs")
        except ValueError:
            return Path(abs_path)
        return Path(*parts[idx:])

    @abstractmethod
    def evaluate(self) -> float | ScoreBundle:
        """Implement this. Return a numeric score or a ScoreBundle."""
        ...

    # --- Helpers ---

    def get_python_command(self) -> list[str]:
        """Return the Python command for running task programs.

        Uses ``uv run`` when a ``pyproject.toml`` exists in the codebase so
        that task-specific dependencies (numpy, scipy, …) are available.
        Falls back to the current interpreter otherwise.
        """
        if (Path(self.codebase_path) / "pyproject.toml").exists() and shutil.which("uv"):
            return ["uv", "run", "--project", self.codebase_path, "python"]
        return [sys.executable]

    def run_program(
        self,
        filename: str,
        *cmd_args: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run a file from the agent's codebase in a subprocess."""
        filepath = Path(self.codebase_path) / filename
        if not filepath.exists():
            raise FileNotFoundError(f"{filename} not found in codebase")
        return subprocess.run(
            [*self.get_python_command(), str(filepath), *cmd_args],
            capture_output=True,
            text=True,
            cwd=self.codebase_path,
            timeout=self.timeout,
            env=self.subprocess_env(),
        )

    def run_script(
        self,
        script: str,
        *,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        """Run an inline Python script using the correct interpreter."""
        return subprocess.run(
            [*self.get_python_command(), "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self.subprocess_env(),
        )

    def run_script_json(
        self,
        script: str,
        *,
        timeout: int = 300,
    ) -> dict:
        """Run an inline script that prints JSON to stdout and return parsed dict.

        Handles common failure modes:
        - Non-zero exit: raises RuntimeError with stderr
        - Empty stdout: raises RuntimeError with stderr for diagnostics
        - Stdout polluted by print statements: scans for last JSON line
        """
        result = self.run_script(script, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-2000:])
        stdout = result.stdout.strip()
        if not stdout:
            stderr_tail = result.stderr.strip()[-1000:]
            raise RuntimeError(f"Script produced no output on stdout.\nstderr: {stderr_tail}")
        # Try full stdout first
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass
        # Scan lines in reverse for a JSON object (handles print() pollution)
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise RuntimeError(
            f"No valid JSON in script output.\n"
            f"stdout (last 500): {stdout[-500:]}\n"
            f"stderr (last 500): {result.stderr.strip()[-500:]}"
        )

    def report_progress(
        self,
        *,
        current: int,
        total: int,
        phase: str = "evaluate",
        message: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a structured progress event for the frontend.

        Events are written to ``eval_logs/<attempt_hash>/progress.jsonl``.
        The method is best-effort: progress reporting must never fail the
        grader, because eval correctness is more important than UI telemetry.
        """
        try:
            total = max(int(total), 0)
            current = max(int(current), 0)
            percent = (current / total) if total else None
            event: dict[str, Any] = {
                "type": "progress",
                "job_id": Path(self.codebase_path).name,
                "phase": phase,
                "current": current,
                "total": total,
                "percent": percent,
                "message": message,
                "timestamp": datetime.now(UTC).isoformat(),
                "eval_version": self.eval_version,
                "eval_profile": self.profile,
            }
            if extra:
                event.update(extra)
            progress_path = self.eval_logs_dir / "progress.jsonl"
            with progress_path.open("a") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")
        except Exception:
            return

    def score(
        self,
        value: float | None,
        explanation: str = "",
        feedback: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScoreBundle:
        """Return a single-score bundle."""
        return self.bundle(value, explanation, feedback=feedback, metadata=metadata)

    def fail(self, explanation: str = "", feedback: str | None = None) -> ScoreBundle:
        """Return a bundle with a null score (evaluation failed)."""
        return self.bundle(None, explanation, feedback=feedback)

    def bundle(
        self,
        value: float | None,
        explanation: str = "",
        feedback: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScoreBundle:
        """Create a ScoreBundle from a score value and explanation."""
        s = Score(
            value=value,
            name="eval",
            explanation=explanation or None,
        )
        return ScoreBundle(
            scores={"eval": s},
            aggregated=value,
            feedback=feedback,
            metadata=metadata or {},
        )

    # --- Internal: called by the framework ---

    async def grade(
        self,
        codebase_path: str,
        tasks: list[Task],
        **kwargs: Any,
    ) -> ScoreBundle:
        """GraderInterface implementation. Sets context and calls evaluate().

        Enforces self.timeout around the entire evaluate() call. On tune-mode
        attempts, prepends ``describe_tune()`` to the bundle's feedback so the
        agent learns the per-grader tune contract from the eval result itself
        — no startup RPC, no CORAL.md plumbing.

        ``island_id`` is threaded through ``**kwargs`` so legacy grader
        signatures still work; we pop it explicitly so it's visible on
        ``self.island_id`` for graders that need to scope hub reads
        (e.g. ``read_attempts(coral_dir, island_id=self.island_id)``).
        """
        self.codebase_path = codebase_path
        self.tasks = tasks
        self.island_id = kwargs.pop("island_id", None)
        resource_env = self.resource_env()
        for task in self.tasks:
            task.metadata.setdefault("eval_version", self.eval_version)
            task.metadata.setdefault("eval_profile", self.profile)
            task.metadata.setdefault("resources", resource_env)

        self.report_progress(current=0, total=1, phase="evaluate", message="started")

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(pool, self.evaluate),
                    timeout=self.timeout,
                )
            except TimeoutError:
                bundle = self.fail(f"Evaluation timed out after {self.timeout}s")
                self.report_progress(current=1, total=1, phase="timeout", message="timed out")
                return self._annotate_tune(self._annotate_eval_context(bundle))

        if isinstance(result, ScoreBundle):
            self.report_progress(current=1, total=1, phase="evaluate", message="completed")
            return self._annotate_tune(self._annotate_eval_context(result))

        # float/int — wrap in a ScoreBundle
        value = float(result)
        bundle = ScoreBundle(
            scores={"eval": Score(value=value, name="eval")},
            aggregated=value,
        )
        self.report_progress(current=1, total=1, phase="evaluate", message="completed")
        return self._annotate_tune(self._annotate_eval_context(bundle))

    def _annotate_eval_context(self, bundle: ScoreBundle) -> ScoreBundle:
        bundle.metadata.setdefault("eval_version", self.eval_version)
        bundle.metadata.setdefault("eval_profile", self.profile)
        bundle.metadata.setdefault("resources", self.resources.to_env())
        return bundle

    def _annotate_tune(self, bundle: ScoreBundle) -> ScoreBundle:
        """Prepend the tune-mode description to bundle feedback when self.tune.

        No-op on real submissions. Keeps the per-grader tune contract attached
        to the result instead of templated into CORAL.md at startup.
        """
        if not self.tune:
            return bundle
        description = (self.describe_tune() or "").strip() or DEFAULT_TUNE_DESCRIPTION
        prefix = f"[--tune mode] {description}"
        bundle.feedback = f"{prefix}\n\n{bundle.feedback}" if bundle.feedback else prefix
        return bundle
