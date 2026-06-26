"""YAML-based project configuration for CORAL, powered by OmegaConf."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from omegaconf import MISSING, OmegaConf


@dataclass
class TaskConfig:
    """Task definition within a CORAL project."""

    name: str = MISSING
    description: str = MISSING
    tips: str = ""


@dataclass
class EvaluationConfig:
    """Task-level evaluation topology.

    The level is selected once for the task by the human/Codex task-design
    discussion. CORAL then derives the legal spaces from it:

    - L1: A only. Agents may inspect the data and scoring mechanism; A scores rank.
    - L2: A + B. A is open exploration; B is hidden iterative scoring.
    - L3: A + B + C. B is hidden iterative scoring; C is sealed final scoring
      kept outside the default agent loop.
    """

    level: str = "L2"
    allow_loop_final: bool = False

    def __post_init__(self) -> None:
        self.level = str(self.level).upper()
        if self.level not in {"L1", "L2", "L3"}:
            raise ValueError(
                f"evaluation.level must be one of L1, L2, L3, got {self.level!r}"
            )

    def score_space(self, *, final: bool = False) -> str:
        if final:
            if self.level != "L3":
                raise ValueError("final evaluation is only valid when evaluation.level is L3")
            return "C"
        if self.level == "L1":
            return "A"
        return "B"


@dataclass
class ParallelGraderConfig:
    """Parallel evaluation in the grader daemon.

    ``max_workers=1`` (the default) is serial. ``resources`` is the optional
    total evaluator resource pool; when set, the daemon schedules pending jobs
    only while both ``max_workers`` and this pool have capacity. Per-job demand
    still comes from ``grader.resources`` plus selected profile overrides.
    """

    max_workers: int = 1
    resources: ResourceConfig = field(default_factory=lambda: ResourceConfig())

    def __post_init__(self) -> None:
        if isinstance(self.resources, dict):
            self.resources = ResourceConfig(**self.resources)


@dataclass
class ResourceConfig:
    """Resource budget hints for evaluation jobs.

    These fields are intentionally advisory at the CORAL core level: graders
    receive them through TaskGrader.resources and standardized environment
    variables. A task-specific grader can enforce them with Docker, Slurm,
    cgroups, CUDA_VISIBLE_DEVICES, or its own scheduler.
    """

    cpu_cores: int = 0  # 0 = unspecified
    memory_gb: float = 0.0  # 0 = unspecified
    storage_gb: float = 0.0  # 0 = unspecified
    gpu_count: int = 0  # 0 = no/unspecified GPU budget
    gpu_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cpu_cores < 0:
            raise ValueError(f"resources.cpu_cores must be >= 0, got {self.cpu_cores}")
        if self.memory_gb < 0:
            raise ValueError(f"resources.memory_gb must be >= 0, got {self.memory_gb}")
        if self.storage_gb < 0:
            raise ValueError(f"resources.storage_gb must be >= 0, got {self.storage_gb}")
        if self.gpu_count < 0:
            raise ValueError(f"resources.gpu_count must be >= 0, got {self.gpu_count}")
        self.gpu_ids = [str(gpu_id) for gpu_id in self.gpu_ids]

    def active(self) -> bool:
        return bool(self.cpu_cores or self.memory_gb or self.storage_gb or self.gpu_count or self.gpu_ids)

    def to_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.cpu_cores > 0:
            env["CORAL_CPU_CORES"] = str(self.cpu_cores)
        if self.memory_gb > 0:
            env["CORAL_MEMORY_GB"] = f"{self.memory_gb:g}"
        if self.storage_gb > 0:
            env["CORAL_STORAGE_GB"] = f"{self.storage_gb:g}"
        if self.gpu_count > 0:
            env["CORAL_GPU_COUNT"] = str(self.gpu_count)
        if self.gpu_ids:
            joined = ",".join(self.gpu_ids)
            env["CORAL_GPU_IDS"] = joined
            env["CUDA_VISIBLE_DEVICES"] = joined
        return env


@dataclass
class ComputeProfileConfig:
    """Resource and timeout profile for open A-space compute jobs."""

    cpu_cores: int = 1
    memory_gb: float = 0.0
    gpu_count: int = 0
    gpu_ids: list[str] = field(default_factory=list)
    timeout: int = 600
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeout < 0:
            raise ValueError(f"compute.profiles[].timeout must be >= 0, got {self.timeout}")
        self.gpu_ids = [str(gpu_id) for gpu_id in self.gpu_ids]
        # Reuse ResourceConfig validation.
        self.resources()

    def resources(self) -> ResourceConfig:
        return ResourceConfig(
            cpu_cores=self.cpu_cores,
            memory_gb=self.memory_gb,
            gpu_count=self.gpu_count,
            gpu_ids=list(self.gpu_ids),
        )


@dataclass
class ComputeClassConfig:
    """Policy for a class of compute jobs."""

    default_profile: str = "cpu-small"
    allow_private_data: bool = False
    network: bool = False
    max_running_per_agent: int = 1
    max_jobs_per_work_loop: int = 0  # 0 = unlimited until loop accounting exists
    max_gpu_minutes_per_work_loop: int = 0  # 0 = unlimited until loop accounting exists

    def __post_init__(self) -> None:
        if self.max_running_per_agent < 1:
            raise ValueError(
                "compute.classes[].max_running_per_agent must be >= 1, "
                f"got {self.max_running_per_agent}"
            )
        if self.max_jobs_per_work_loop < 0:
            raise ValueError(
                "compute.classes[].max_jobs_per_work_loop must be >= 0, "
                f"got {self.max_jobs_per_work_loop}"
            )
        if self.max_gpu_minutes_per_work_loop < 0:
            raise ValueError(
                "compute.classes[].max_gpu_minutes_per_work_loop must be >= 0, "
                f"got {self.max_gpu_minutes_per_work_loop}"
            )


def _default_compute_profiles() -> dict[str, ComputeProfileConfig]:
    return {
        "cpu-small": ComputeProfileConfig(cpu_cores=2, memory_gb=8, timeout=600),
        "cpu-large": ComputeProfileConfig(cpu_cores=8, memory_gb=32, timeout=1800),
        "gpu-small": ComputeProfileConfig(cpu_cores=4, memory_gb=24, gpu_count=1, timeout=1800),
        "gpu-large": ComputeProfileConfig(cpu_cores=8, memory_gb=64, gpu_count=1, timeout=7200),
    }


@dataclass
class ComputeConfig:
    """Open A-space compute runner configuration.

    This is intentionally a small CORAL-level contract. The default backend is
    a local subprocess runner; Docker/Slurm/Nomad can be added behind the same
    `coral run` job schema later.
    """

    backend: str = "local"
    allow_unisolated_local: bool = False
    pool: ResourceConfig = field(default_factory=ResourceConfig)
    profiles: dict[str, ComputeProfileConfig] = field(default_factory=_default_compute_profiles)
    classes: dict[str, ComputeClassConfig] = field(
        default_factory=lambda: {"explore": ComputeClassConfig()}
    )

    def __post_init__(self) -> None:
        if isinstance(self.pool, dict):
            self.pool = ResourceConfig(**self.pool)
        self.profiles = {
            name: profile
            if isinstance(profile, ComputeProfileConfig)
            else ComputeProfileConfig(**profile)
            for name, profile in self.profiles.items()
        }
        self.classes = {
            name: cls if isinstance(cls, ComputeClassConfig) else ComputeClassConfig(**cls)
            for name, cls in self.classes.items()
        }
        if self.backend != "local":
            raise ValueError(f"compute.backend currently supports only 'local', got {self.backend!r}")
        for class_name, cls in self.classes.items():
            if cls.default_profile not in self.profiles:
                raise ValueError(
                    f"compute.classes.{class_name}.default_profile "
                    f"{cls.default_profile!r} is not defined in compute.profiles"
                )


@dataclass
class GraderProfileConfig:
    """Named eval profile used to trade off cost and confidence."""

    label: str = ""
    timeout: int = 0  # 0 = inherit grader.timeout
    args: dict[str, Any] = field(default_factory=dict)
    resources: ResourceConfig = field(default_factory=ResourceConfig)

    def __post_init__(self) -> None:
        if self.timeout < 0:
            raise ValueError(f"grader.profiles[].timeout must be >= 0, got {self.timeout}")
        if isinstance(self.resources, dict):
            self.resources = ResourceConfig(**self.resources)


@dataclass
class FinalGraderConfig:
    """Optional sealed C-space grader for L3 tasks.

    The final grader reuses the main grader venv/setup. It can point at a
    different entrypoint and private assets, with lightweight overrides for
    timeout/profile/args/resources.
    """

    entrypoint: str = ""
    timeout: int = 0  # 0 = inherit grader.timeout
    args: dict[str, Any] = field(default_factory=dict)
    private: list[str] = field(default_factory=list)
    direction: str = ""  # empty = inherit grader.direction
    eval_version: str = ""  # empty = derive from grader.eval_version
    profile: str = ""  # empty = inherit grader.profile
    profiles: dict[str, GraderProfileConfig] = field(default_factory=dict)
    resources: ResourceConfig = field(default_factory=ResourceConfig)

    def __post_init__(self) -> None:
        if self.timeout < 0:
            raise ValueError(f"grader.final.timeout must be >= 0, got {self.timeout}")
        if isinstance(self.resources, dict):
            self.resources = ResourceConfig(**self.resources)
        self.profiles = {
            name: profile
            if isinstance(profile, GraderProfileConfig)
            else GraderProfileConfig(**profile)
            for name, profile in self.profiles.items()
        }


@dataclass
class GraderConfig:
    """Grader configuration."""

    entrypoint: str = (
        ""  # "module.path:ClassName" — required; resolved inside .coral/private/grader_venv/
    )
    setup: list[str] = field(
        default_factory=list
    )  # shell commands run in .coral/private/grader_venv/ before agents start
    timeout: int = 300  # eval timeout in seconds (0 = no limit)
    args: dict[str, Any] = field(default_factory=dict)
    private: list[str] = field(
        default_factory=list
    )  # files/dirs copied to .coral/ (hidden from agents)
    direction: str = "maximize"  # "maximize" or "minimize"
    eval_version: str = "eval_v1"
    profile: str = "default"
    profiles: dict[str, GraderProfileConfig] = field(default_factory=dict)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    # Producer-side queue cap. Reject `coral eval` when an agent already has
    # this many ungraded submissions in flight. 0 = unlimited (legacy behavior).
    # Default 1: an agent can only enqueue a fresh attempt once the prior one
    # is graded, which prevents runaway pending floods when the grader is slow.
    max_pending_per_agent: int = 1
    parallel: ParallelGraderConfig = field(default_factory=ParallelGraderConfig)
    final: FinalGraderConfig = field(default_factory=FinalGraderConfig)

    def __post_init__(self) -> None:
        if self.max_pending_per_agent < 0:
            raise ValueError(
                f"grader.max_pending_per_agent must be >= 0, got {self.max_pending_per_agent}"
            )
        # SubprocessGrader serializes GraderConfig via dataclasses.asdict and
        # rebuilds with `GraderConfig(**payload)`, which leaves `parallel` as a
        # plain dict. Coerce here so validation and downstream attribute access
        # work for both real callers and the worker reconstruction path.
        if isinstance(self.parallel, dict):
            self.parallel = ParallelGraderConfig(**self.parallel)
        if isinstance(self.resources, dict):
            self.resources = ResourceConfig(**self.resources)
        if isinstance(self.final, dict):
            self.final = FinalGraderConfig(**self.final)
        self.profiles = {
            name: profile
            if isinstance(profile, GraderProfileConfig)
            else GraderProfileConfig(**profile)
            for name, profile in self.profiles.items()
        }
        if self.parallel.max_workers < 1:
            raise ValueError(
                f"grader.parallel.max_workers must be >= 1, got {self.parallel.max_workers}"
            )
        if not self.eval_version:
            raise ValueError("grader.eval_version must be non-empty")
        if not self.profile:
            raise ValueError("grader.profile must be non-empty")

    def for_space(self, eval_space: str) -> GraderConfig:
        """Return the effective grader config for an evaluation space."""
        if eval_space != "C":
            return self

        final = self.final
        if not final.entrypoint:
            raise ValueError("L3 final evaluation requires grader.final.entrypoint")
        final_args = dict(self.args)
        final_args.update(final.args)
        return GraderConfig(
            entrypoint=final.entrypoint,
            setup=list(self.setup),
            timeout=final.timeout or self.timeout,
            args=final_args,
            private=list(self.private) + list(final.private),
            direction=final.direction or self.direction,
            eval_version=final.eval_version or f"{self.eval_version}_final",
            profile=final.profile or self.profile,
            profiles=final.profiles or dict(self.profiles),
            resources=final.resources if final.resources.active() else self.resources,
            max_pending_per_agent=self.max_pending_per_agent,
            parallel=self.parallel,
        )


@dataclass
class GatewayConfig:
    """LiteLLM gateway configuration for intercepting agent model traffic."""

    enabled: bool = False
    port: int = 4000
    config: str = ""  # path to litellm_config.yaml
    api_key: str = ""  # LiteLLM master key (auto-generated if empty)


@dataclass
class AgentAssignmentConfig:
    """Per-assignment override of runtime/model for mix-and-match multi-agent runs.

    When ``agents.assignments`` is set, it overrides ``agents.count``: the total
    number of agents spawned is the sum of ``count`` across every assignment.
    Empty string fields inherit from the top-level ``agents.*`` defaults.
    Each assignment can override:
    - ``runtime``:        the agent runtime (claude_code / codex / opencode / ...)
    - ``model``:          model passed to that runtime
    - ``count``:          how many agents of this kind to spawn (default 1)
    - ``runtime_options`` extra options forwarded to that runtime's ``start()``
    """

    runtime: str = ""  # empty -> inherit from agents.runtime
    model: str = ""  # empty -> inherit from agents.model (or runtime default)
    count: int = 1
    runtime_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError(f"agents.assignments[].count must be >= 1, got {self.count}")


@dataclass
class AgentConfig:
    """Agent spawning configuration."""

    count: int = 1
    runtime: str = "claude_code"
    model: str = "sonnet"
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    runtime_options: dict[str, Any] = field(default_factory=dict)
    # Mix-and-match: when non-empty, each entry spawns its own runtime/model
    # combo. ``agents.count`` is ignored (total = sum of assignment counts).
    # Empty fields on an assignment inherit the agents.* defaults below.
    assignments: list[AgentAssignmentConfig] = field(default_factory=list)
    # Max agent turns per session before the runtime exits and the manager
    # restarts the agent (preserving context via --resume). 0 = no cap, let
    # the underlying CLI run until it exits naturally.
    max_turns: int = 0
    # Stall watchdog: restart an agent that produces no output for this many
    # seconds. 0 disables the watchdog. Default 1200s (20 min) catches deadlocks
    # faster than the prior 3600s while still being well above legitimate quiet
    # periods (long tool calls, grader queue waits — the latter is exempted).
    timeout: int = 1200
    skills: list[str] = field(default_factory=list)  # skill dirs copied to .coral/public/skills/
    research: bool = True  # enable web search / literature review step in workflow
    stagger_seconds: int = 0  # delay between spawning each agent (rate-limit backpressure)

    # Reliability: crash-burst circuit breaker.
    # When an agent exits repeatedly in a short window with no clean-exit marker,
    # the manager pauses it instead of respawning into a tight loop.
    # 0 in any of the three knobs disables the breaker entirely.
    restart_burst_threshold: int = 3  # crashes within window before pausing the agent
    restart_burst_window: int = 30  # seconds; sliding window for crash counting
    restart_pause_seconds: int = (
        300  # how long the paused state holds before restart attempts resume
    )

    # Reliability: grader-queue exemption for stall detection.
    # Skip stall checks for an agent whose latest attempt is pending grading,
    # but only if the grader process is alive and the pending attempt is not stale.
    grader_pending_max_age: int = 1800  # seconds; older pending attempts no longer exempt

    # Reliability: minimum runtime in seconds before an exit_code==0 is considered "clean"
    # for runtimes that lack a stable terminal marker (codex/opencode/kiro).
    min_clean_runtime_seconds: int = 60

    def __post_init__(self) -> None:
        # Reject negative values for the new reliability knobs;
        # 0 is treated as "disabled" for the same fields where it makes sense.
        for field_name in (
            "restart_burst_threshold",
            "restart_burst_window",
            "restart_pause_seconds",
            "grader_pending_max_age",
            "min_clean_runtime_seconds",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"agents.{field_name} must be >= 0, got {value}")
        # If the breaker is enabled at all, the pause must outlast the burst window;
        # otherwise the breaker can re-arm before the burst counter has cleared.
        if (
            self.restart_burst_threshold > 0
            and self.restart_burst_window > 0
            and 0 < self.restart_pause_seconds < self.restart_burst_window
        ):
            raise ValueError(
                "agents.restart_pause_seconds must be >= agents.restart_burst_window "
                f"(got pause={self.restart_pause_seconds}, window={self.restart_burst_window})"
            )

@dataclass
class SharingConfig:
    """Deprecated true-only compatibility shim for old task.yaml files."""

    attempts: bool = True
    notes: bool = True
    skills: bool = True

    def __post_init__(self) -> None:
        disabled = [
            name
            for name in ("attempts", "notes", "skills")
            if getattr(self, name) is not True
        ]
        if disabled:
            names = ", ".join(f"sharing.{name}" for name in disabled)
            raise ValueError(
                "sharing.* toggles have been removed. CORAL now exposes one fixed "
                "public shared state model through the runtime shared directory and "
                f"the `coral kb` CLI. Remove {names} from task.yaml."
            )


@dataclass
class KnowledgeConfig:
    """Task knowledge base copied into each run.

    ``path`` is resolved relative to the task config directory. At run creation
    time CORAL copies it into the run's snapshots/ directory and seeds the
    active shared knowledge base from that frozen snapshot. Missing paths are
    allowed: CORAL still creates an empty active knowledge base so agents have a
    stable place to put papers, repos, research notes, and experiment notes.
    """

    path: str = "./knowledge"
    snapshot: bool = True


@dataclass
class WorkspaceConfig:
    """Workspace layout configuration."""

    results_dir: str = "./results"
    repo_path: str = "."
    setup: list[str] = field(default_factory=list)  # shell commands to run before agents start
    # Ignored if results_dir is set
    base_dir: str = ""
    run_dir: str = ""  # if set, use this exact run directory instead of generating one


@dataclass
class RunConfig:
    """Runtime flags for a CORAL session."""

    verbose: bool = False
    ui: bool = False
    session: str = "tmux"  # "local", "tmux", or "docker"
    docker_image: str = ""  # empty = auto-build from project Dockerfile
    # Run-level wall-clock deadline in seconds. 0 = no run-level deadline.
    # Unlike agents.max_turns, this stops the whole manager, all agents, and
    # the grader daemon when the elapsed active runtime reaches the limit.
    max_runtime_seconds: int = 0

    def __post_init__(self) -> None:
        if self.max_runtime_seconds < 0:
            raise ValueError(
                f"run.max_runtime_seconds must be >= 0, got {self.max_runtime_seconds}"
            )


@dataclass
class CoralConfig:
    """Top-level project configuration."""

    task: TaskConfig = field(default_factory=TaskConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    grader: GraderConfig = field(default_factory=GraderConfig)
    compute: ComputeConfig = field(default_factory=ComputeConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)
    sharing: SharingConfig = field(default_factory=SharingConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    run: RunConfig = field(default_factory=RunConfig)
    task_dir: Path | None = None  # internal: directory containing task.yaml

    def __post_init__(self) -> None:
        if isinstance(self.evaluation, dict):
            self.evaluation = EvaluationConfig(**self.evaluation)
        if isinstance(self.grader, dict):
            self.grader = GraderConfig(**self.grader)
        if isinstance(self.compute, dict):
            self.compute = ComputeConfig(**self.compute)
        _validate_evaluation_topology(self.evaluation, self.grader)

    @classmethod
    def from_yaml(cls, path: str | Path) -> CoralConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoralConfig:
        data = _preprocess(dict(data))
        schema = OmegaConf.structured(cls)
        raw = OmegaConf.create(data)
        merged = OmegaConf.merge(schema, raw)
        cfg: CoralConfig = OmegaConf.to_object(merged)  # type: ignore[assignment]
        _validate_evaluation_topology(cfg.evaluation, cfg.grader)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        sc = OmegaConf.structured(self)
        container: dict[str, Any] = OmegaConf.to_container(sc, resolve=True)  # type: ignore[assignment]
        # Remove internal-only fields
        container.pop("task_dir", None)
        return container

    def to_yaml(self, path: str | Path) -> None:
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def merge_dotlist(cls, config: CoralConfig, dotlist: list[str]) -> CoralConfig:
        """Merge CLI dotlist overrides into an existing config."""
        if not dotlist:
            return config
        keys = [item.split("=", 1)[0] for item in dotlist]
        _reject_removed_topology_keys(keys)
        _reject_removed_agent_loop_keys(
            key.removeprefix("agents.") for key in keys if key.startswith("agents.")
        )
        base = OmegaConf.structured(config)
        overrides = OmegaConf.from_dotlist(dotlist)
        merged = OmegaConf.merge(base, overrides)
        cfg: CoralConfig = OmegaConf.to_object(merged)  # type: ignore[assignment]
        return cfg


def _preprocess(data: dict[str, Any]) -> dict[str, Any]:
    """Transform legacy keys before OmegaConf merge."""
    _reject_removed_topology_keys(data.keys())

    # Reject removed grader.type / grader.module fields with upgrade guidance.
    grader_data = data.get("grader")
    if isinstance(grader_data, dict):
        legacy_grader_keys = [k for k in ("type", "module") if k in grader_data]
        if legacy_grader_keys:
            raise ValueError(
                f"grader.{' / grader.'.join(legacy_grader_keys)} is removed. "
                f"Use grader.entrypoint = 'your_pkg.module:Grader' (and grader.setup "
                f"to install the package). See docs/guides/custom-grader."
            )

    agents_data = data.get("agents", {})
    if not isinstance(agents_data, dict):
        return data

    # Make a copy so we don't mutate the original
    agents_data = dict(agents_data)

    _reject_removed_agent_loop_keys(agents_data.keys())

    # If runtime is set but model is not, use the runtime-specific default.
    # Custom-entrypoint runtimes ('module.path:ClassName') have no default —
    # require the user to set agents.model explicitly so a footgun like the
    # builtin "sonnet" default doesn't silently land on a foreign runtime.
    if "runtime" in agents_data and "model" not in agents_data:
        from coral.agent.registry import default_model_for_runtime

        rt = agents_data["runtime"]
        default_model = default_model_for_runtime(rt)
        if default_model:
            agents_data["model"] = default_model
        elif isinstance(rt, str) and ":" in rt:
            raise ValueError(
                f"agents.runtime={rt!r} is a custom runtime entrypoint; "
                f"set agents.model explicitly in task.yaml."
            )

    # Normalize assignments: fill in missing model defaults from the assignment's
    # runtime so each entry stores a concrete model. Empty fields are kept as ""
    # (will inherit from the top-level agents.* defaults at resolve time).
    assignments_raw = agents_data.get("assignments")
    if isinstance(assignments_raw, list):
        from coral.agent.registry import default_model_for_runtime

        normalized: list[dict[str, Any]] = []
        for entry in assignments_raw:
            if not isinstance(entry, dict):
                continue
            entry = dict(entry)
            if entry.get("runtime") and not entry.get("model"):
                m = default_model_for_runtime(entry["runtime"])
                if m:
                    entry["model"] = m
            normalized.append(entry)
        agents_data["assignments"] = normalized

    data["agents"] = agents_data

    # Remove task_dir if present in raw data (it's internal-only)
    data.pop("task_dir", None)

    return data


def _validate_evaluation_topology(evaluation: EvaluationConfig, grader: GraderConfig) -> None:
    """Validate the fixed L1/L2/L3 space topology against grader config."""
    level = evaluation.level
    has_final = bool(
        grader.final.entrypoint
        or grader.final.private
        or grader.final.args
        or grader.final.eval_version
        or grader.final.profile
        or grader.final.profiles
        or grader.final.resources.active()
        or grader.final.timeout
    )
    if level == "L1":
        if grader.private:
            raise ValueError(
                "evaluation.level=L1 means A-space scoring is fully open; "
                "do not configure grader.private hidden scoring assets."
            )
        if has_final:
            raise ValueError("evaluation.level=L1 cannot configure grader.final")
    elif level == "L2":
        if has_final:
            raise ValueError("evaluation.level=L2 cannot configure grader.final; use L3 for C-space")
    elif level == "L3" and not grader.final.entrypoint:
        raise ValueError("evaluation.level=L3 requires grader.final.entrypoint for sealed C-space")


def _reject_removed_topology_keys(keys: Iterable[str]) -> None:
    for key in keys:
        if key == "islands" or key.startswith("islands."):
            raise ValueError(
                "The removed topology section is no longer supported. CORAL now runs "
                "multiple agent routes in one shared .coral/public state space. "
                "Remove that section and use agents.count or agents.assignments "
                "to control parallel agents."
            )


def _reject_removed_agent_loop_keys(keys: Iterable[str]) -> None:
    removed = {"heartbeat", "reflect_every", "heartbeat_every"}
    found = [key for key in keys if key in removed]
    if found:
        raise ValueError(
            "Removed agent loop key(s): "
            + ", ".join(f"agents.{key}" for key in found)
            + ". CORAL no longer supports interval/plateau heartbeat actions. "
            "Use eval feedback reports, work_loop, reflect_loop archives, and "
            "outer-loop review notes instead."
        )
