"""Spawn N agents, monitor health, auto-resume with eval feedback."""

from __future__ import annotations

import atexit
import json
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from coral.agent.assignments import (
    AgentSpec,
    resolve_agent_specs,
)
from coral.agent.exit_classifier import (
    classify_by_uptime,
)
from coral.agent.exit_classifier import (
    claude_code_log_has_session_error as _log_has_session_error,
)
from coral.agent.registry import get_runtime
from coral.agent.runtime import AgentHandle, AgentRuntime
from coral.agent.state import (
    AgentRuntimeState,
    AgentStateDocument,
    RestartEvent,
    write_agent_state,
)
from coral.config import CoralConfig
from coral.hub.attempts import (
    agent_in_grader_queue,
    read_attempts,
)
from coral.template.coral_md import generate_coral_md
from coral.types import BUDGET_CLASS_REAL, get_budget_class
from coral.workspace import (
    ProjectPaths,
    apply_runtime_mounts,
    create_agent_worktree,
    create_project,
    setup_claude_settings,
    setup_codex_settings,
    setup_cursor_settings,
    setup_gitignore,
    setup_instruction_links,
    setup_opencode_settings,
    setup_shared_state,
    setup_worktree_env,
    write_agent_id,
    write_coral_dir,
)

logger = logging.getLogger(__name__)


class AgentManager:
    """Manage the lifecycle of multiple CORAL agents."""

    def __init__(
        self,
        config: CoralConfig,
        verbose: bool = False,
        config_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.config_dir = config_dir
        runtime_import_dir = config_dir or config.task_dir
        if runtime_import_dir is not None:
            runtime_import_path = str(Path(runtime_import_dir).expanduser().resolve())
            if runtime_import_path not in sys.path:
                sys.path.insert(0, runtime_import_path)
        self.specs: list[AgentSpec] = resolve_agent_specs(config)
        self.specs_by_id: dict[str, AgentSpec] = {s.agent_id: s for s in self.specs}
        # One runtime instance per agent_id. In uniform mode all entries point
        # to the same class; in mix-and-match mode each agent uses its own.
        self.runtimes: dict[str, AgentRuntime] = {
            s.agent_id: get_runtime(s.runtime) for s in self.specs
        }
        # Default runtime used for run-level operations that aren't tied to a
        # specific agent. Falls back to the first spec.
        self.runtime: AgentRuntime = self.runtimes[self.specs[0].agent_id]
        self.handles: list[AgentHandle] = []
        self.paths: ProjectPaths | None = None
        self.verbose = verbose
        self._running = False
        self._stop_event = threading.Event()
        self._stopping = False
        self._start_time: datetime | None = None
        self._runtime_start_epoch: float | None = None
        self._run_deadline_epoch: float | None = None
        self._last_runtime_limit_seconds: int | None = None
        self._restart_counts: dict[str, int] = {}
        self._agent_eval_counts: dict[str, int] = {}
        self._agent_best_scores: dict[str, float] = {}
        # Per-agent score history (real attempts only, in submit order).
        # ``None`` entries represent grader-error attempts and preserve
        # failure pressure without changing any anchor.
        self._agent_score_history: dict[str, list[float | None]] = {}
        # Reliability state. `_started_at` records when each agent's current
        # subprocess began running (epoch seconds), used as the uptime input
        # for the runtime exit classifier. `_crash_history` is the sliding
        # window of non-clean exits the circuit breaker counts. `_paused_until`
        # is the wall-clock deadline at which a paused agent is allowed to
        # restart again. `_pause_count` and `_last_fault_at` are persisted
        # metadata on `agent_state.json` for `coral status`.
        # `_pending_restart_after_pause` tracks agents whose pause just
        # expired so the dead-agent branch restarts them once without
        # re-classifying the original exit (which would double-count it).
        self._started_at: dict[str, float] = {}
        self._crash_history: dict[str, deque[RestartEvent]] = {}
        self._paused_until: dict[str, float] = {}
        self._pause_count: dict[str, int] = {}
        self._last_fault_at: dict[str, str] = {}
        self._pending_restart_after_pause: set[str] = set()
        # Agents explicitly stopped from the dashboard. This is deliberately
        # separate from crash-burst PAUSED state so manual intervention does
        # not interact with the reliability breaker.
        self._manual_stopped_agents: set[str] = set()
        self._agent_control_applied: dict[str, str] = {}
        self._reflect_started_at: dict[str, float] = {}
        self._reflect_detail: dict[str, str] = {}
        self._gateway: Any | None = None
        self._gateway_keys: dict[str, str] = {}  # agent_id -> proxy key
        self._grader_proc: multiprocessing.Process | None = None
        self._grader_stop_event: Any | None = None  # multiprocessing.Event

    def _runtime_for(self, agent_id: str) -> AgentRuntime:
        """Return the runtime instance for an agent_id, creating one on demand.

        ``resume_all`` may discover worktrees that the current ``specs`` list
        doesn't cover (e.g. the saved config no longer mentions them). Falling
        back to the default runtime keeps resume robust.
        """
        runtime = self.runtimes.get(agent_id)
        if runtime is None:
            runtime = self.runtime
            self.runtimes[agent_id] = runtime
        return runtime

    def _mounts_base_dir(self) -> Path:
        """Return the directory used to resolve relative ``runtime_options.mounts`` sources.

        Prefers ``config.task_dir`` (where ``task.yaml`` lives — typically
        what the user means when they write ``./agent-settings.json`` in
        their task config), falls back to ``self.config_dir``, then cwd.
        """
        for candidate in (self.config.task_dir, self.config_dir):
            if candidate is not None:
                return Path(candidate)
        return Path.cwd()

    def _iso_from_epoch(self, value: float | None) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(value, UTC).isoformat()

    def _configured_runtime_limit_seconds(self) -> int:
        """Read the current run-level runtime cap, allowing live UI edits.

        The manager owns a config object loaded at process start, but the
        control panel can save `config.yaml` while the run is active. Reading
        only this small field lets users extend/shorten the deadline without
        restarting the manager or mutating agent topology.
        """
        limit = self.config.run.max_runtime_seconds
        if self.paths is None:
            return limit
        config_path = self.paths.coral_dir / "config.yaml"
        if not config_path.exists():
            return limit
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
            value = (data.get("run") or {}).get("max_runtime_seconds", limit)
            limit = int(value or 0)
        except (OSError, TypeError, ValueError, yaml.YAMLError):
            return self.config.run.max_runtime_seconds
        if limit < 0:
            return self.config.run.max_runtime_seconds
        self.config.run.max_runtime_seconds = limit
        return limit

    def _agent_control_path(self, agent_id: str) -> Path | None:
        if self.paths is None:
            return None
        return self.paths.coral_dir / "public" / "control" / "agents" / f"{agent_id}.json"

    def _read_agent_control_payload(self, agent_id: str) -> dict[str, Any]:
        path = self._agent_control_path(agent_id)
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_agent_control_payload(self, agent_id: str, payload: dict[str, Any]) -> None:
        path = self._agent_control_path(agent_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
            tmp.replace(path)
        except OSError as exc:
            logger.warning(f"Failed to update control file for {agent_id}: {exc}")

    def _knowledge_dir_for_agent(self) -> Path | None:
        if self.paths is None:
            return None
        return self.paths.coral_dir / "public" / "knowledge"

    def _read_agent_seed_brief(
        self,
        agent_id: str,
        shared_dir: str,
    ) -> tuple[str, str]:
        """Read the Codex-prepared initialization plan for an agent, if present."""
        if self.paths is None:
            return "", ""

        candidate_roots: list[Path] = []
        visible = self._knowledge_dir_for_agent()
        if visible is not None:
            candidate_roots.append(visible)
        public = self.paths.coral_dir / "public" / "knowledge"
        if public not in candidate_roots:
            candidate_roots.append(public)

        for knowledge_dir in candidate_roots:
            seed_dir = knowledge_dir / "briefs" / "agent-seeds"
            candidates = [seed_dir / f"{agent_id}.md"]
            if seed_dir.is_dir():
                candidates.extend(
                    path
                    for path in sorted(seed_dir.glob("*.md"))
                    if path.stem == agent_id or agent_id in path.stem
                )
            for path in candidates:
                if not path.is_file():
                    continue
                text = self._read_prompt_brief(path)
                if text:
                    return text, self._shared_knowledge_ref(path, knowledge_dir, visible, shared_dir)
        return "", ""

    def _read_prompt_brief(self, path: Path, *, max_chars: int = 6000) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "..."

    def _shared_knowledge_ref(
        self,
        path: Path,
        knowledge_dir: Path,
        visible_knowledge_dir: Path | None,
        shared_dir: str,
    ) -> str:
        try:
            rel = path.relative_to(knowledge_dir).as_posix()
        except ValueError:
            return str(path)
        if visible_knowledge_dir is not None and knowledge_dir.resolve() == visible_knowledge_dir.resolve():
            return f"{shared_dir}/knowledge/{rel}"
        return str(path)

    def _read_agent_control_request(self, agent_id: str) -> str | None:
        data = self._read_agent_control_payload(agent_id)
        if not data:
            return None
        desired = str(data.get("desired_state", "")).strip().lower()
        return desired if desired in {"running", "stopped"} else None

    def _write_external_guidance_to_notebook(
        self,
        agent_id: str,
        guidance: str,
        *,
        source: str,
        actor: str,
    ) -> str:
        """Archive/reset an agent notebook for external control guidance."""
        if self.paths is None:
            return guidance
        try:
            from coral.hub.kb import reset_notebook

            now = datetime.now(UTC).isoformat(timespec="seconds")
            notebook_content = (
                f"# Notebook: {agent_id}\n\n"
                "## Current Plan\n"
                "External review/control guidance was applied by the framework. "
                "Use this as the next work_loop plan unless later evidence contradicts it.\n\n"
                "## External Guidance\n"
                f"- Applied at: {now}\n"
                f"- Source: {source}\n\n"
                f"{guidance}\n\n"
                "## Work Notes\n"
            )
            reset_notebook(
                self.paths.coral_dir,
                agent_id,
                notebook_content,
                reason="external-adjustment",
                actor=actor,
            )
        except Exception as exc:
            logger.warning(f"Failed to reset notebook for external guidance on {agent_id}: {exc}")
            return guidance

        return (
            "You are in work_loop. External review/control guidance has been "
            "written to your notebook. Read it first with:\n"
            f"`coral kb notebook --agent {agent_id}`\n\n"
            "Original guidance:\n"
            f"{guidance}"
        )

    def _apply_agent_prompt_request(
        self,
        idx: int,
        handle: AgentHandle,
        payload: dict[str, Any],
    ) -> bool:
        action = str(payload.get("action", "")).strip().lower()
        if action != "prompt":
            return False
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return False

        agent_id = handle.agent_id
        command_id = str(payload.get("command_id") or payload.get("updated_at") or "")
        if command_id and self._agent_control_applied.get(agent_id) == command_id:
            return False

        logger.info(f"Dashboard prompt requested for {agent_id}")
        if self.verbose:
            print(f"[coral] injecting dashboard prompt into {agent_id}")

        prompt_to_send = self._write_external_guidance_to_notebook(
            agent_id,
            prompt,
            source="dashboard-prompt",
            actor="dashboard",
        )

        self._manual_stopped_agents.discard(agent_id)
        self._paused_until.pop(agent_id, None)
        self._pending_restart_after_pause.discard(agent_id)
        self._clear_agent_reflect_loop(agent_id)
        if handle.alive:
            self.handles[idx] = self._interrupt_and_resume(
                idx,
                prompt_to_send,
                prompt_source="dashboard-prompt",
            )
        elif self._running:
            self.handles[idx] = self._restart_agent(
                idx,
                prompt=prompt_to_send,
                prompt_source="dashboard-prompt",
            )
        else:
            return False

        if command_id:
            self._agent_control_applied[agent_id] = command_id
        payload["action"] = "idle"
        payload["desired_state"] = "running"
        payload["prompt"] = ""
        payload["last_prompt_applied_at"] = datetime.now(UTC).isoformat()
        self._write_agent_control_payload(agent_id, payload)
        self._write_agent_pids()
        self._persist_agent_state()
        return True

    def _apply_agent_control_requests(self) -> None:
        """Apply per-agent stop/resume/prompt requests written by the dashboard."""
        if self.paths is None:
            return

        for i, handle in enumerate(list(self.handles)):
            agent_id = handle.agent_id
            payload = self._read_agent_control_payload(agent_id)
            if self._apply_agent_prompt_request(i, handle, payload):
                continue
            desired = str(payload.get("desired_state", "")).strip().lower()
            if desired == "stopped" and agent_id not in self._manual_stopped_agents:
                logger.info(f"Dashboard requested stop for {agent_id}")
                if self.verbose:
                    print(f"[coral] stopping {agent_id} by dashboard request")
                self._manual_stopped_agents.add(agent_id)
                self._pending_restart_after_pause.discard(agent_id)
                self._paused_until.pop(agent_id, None)
                self._clear_agent_reflect_loop(agent_id)
                self._save_sessions()
                handle.interrupt()
                if handle.alive:
                    handle.stop()
                self._save_sessions()
                self._write_agent_pids()
                self._persist_agent_state()
            elif desired == "running" and agent_id in self._manual_stopped_agents:
                logger.info(f"Dashboard requested resume for {agent_id}")
                if self.verbose:
                    print(f"[coral] resuming {agent_id} by dashboard request")
                self._manual_stopped_agents.discard(agent_id)
                self._paused_until.pop(agent_id, None)
                self._pending_restart_after_pause.discard(agent_id)
                self._clear_agent_reflect_loop(agent_id)
                if not handle.alive and self._running:
                    self.handles[i] = self._restart_agent(
                        i,
                        prompt="Resume work after a manual dashboard pause.",
                        prompt_source="manual-resume",
                    )
                    self._write_agent_pids()
                self._persist_agent_state()

    def _refresh_runtime_deadline(self, *, force: bool = False, status: str = "running") -> None:
        if self._runtime_start_epoch is None:
            now = time.time()
            self._runtime_start_epoch = now
            self._start_time = datetime.fromtimestamp(now, UTC)

        limit = self._configured_runtime_limit_seconds()
        deadline = self._runtime_start_epoch + limit if limit > 0 else None
        changed = (
            force
            or limit != self._last_runtime_limit_seconds
            or deadline != self._run_deadline_epoch
        )
        self._last_runtime_limit_seconds = limit
        self._run_deadline_epoch = deadline
        if changed:
            self._write_run_state(status=status)

    def _write_run_state(self, *, status: str, stopped_reason: str | None = None) -> None:
        """Persist run-level lifecycle state for the dashboard."""
        if self.paths is None:
            return
        now = time.time()
        remaining = None
        if self._run_deadline_epoch is not None:
            remaining = max(0.0, self._run_deadline_epoch - now)
        state = {
            "status": status,
            "started_at": self._iso_from_epoch(self._runtime_start_epoch),
            "deadline_at": self._iso_from_epoch(self._run_deadline_epoch),
            "max_runtime_seconds": self._last_runtime_limit_seconds
            if self._last_runtime_limit_seconds is not None
            else self.config.run.max_runtime_seconds,
            "remaining_seconds": remaining,
            "stopped_reason": stopped_reason,
            "updated_at": datetime.fromtimestamp(now, UTC).isoformat(),
        }
        path = self.paths.coral_dir / "public" / "run_state.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".run_state.json.tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.replace(path)
        except OSError as exc:
            logger.warning(f"Failed to write run_state.json: {exc}")

    def _maybe_stop_for_deadline(self) -> bool:
        """Stop the whole run when the configured wall-clock deadline expires."""
        self._refresh_runtime_deadline(status="running")
        if self._run_deadline_epoch is None:
            return False
        if time.time() < self._run_deadline_epoch:
            return False
        logger.info("Run deadline reached; stopping all agents and grader")
        if self.verbose:
            print("[coral] Run deadline reached; stopping all agents and grader")
        self.stop_all(reason="deadline")
        return True

    def prepare_all(self) -> ProjectPaths:
        """Create the run workspace and materialize each agent worktree."""
        self.paths = create_project(self.config, config_dir=self.config_dir)
        self._write_run_state(status="prepared")
        logger.info(f"Run directory: {self.paths.run_dir}")
        logger.info(f"  coral_dir: {self.paths.coral_dir}")
        logger.info(f"  repo_dir:  {self.paths.repo_dir}")

        for agent_id in [s.agent_id for s in self.specs]:
            self._materialize_agent_workspace(agent_id, create_missing=True)

        return self.paths

    def launch_prepared(self, paths: ProjectPaths) -> list[AgentHandle]:
        """Launch agents from an already prepared timestamp run."""
        self._start_time = datetime.now(UTC)
        self._runtime_start_epoch = self._start_time.timestamp()
        self.paths = paths
        self._refresh_runtime_deadline(force=True, status="starting")

        missing_worktrees = [
            s.agent_id for s in self.specs if not (self.paths.agents_dir / s.agent_id).is_dir()
        ]
        if missing_worktrees:
            raise RuntimeError(
                "Prepared run is missing agent workspaces: "
                + ", ".join(missing_worktrees)
                + ". Run `coral prepare` again before `coral start`."
            )

        logger.info(f"Run directory: {self.paths.run_dir}")
        logger.info(f"  coral_dir: {self.paths.coral_dir}")
        logger.info(f"  repo_dir:  {self.paths.repo_dir}")

        # 1. Start gateway if configured
        self._start_gateway_if_enabled()

        # 2. Start grader daemon. Agents' `coral eval` writes pending attempts;
        #     the daemon picks them up, grades inside an isolated worktree,
        #     and writes the score back. Must be running before agents start.
        self._start_grader_daemon()

        # 3. For each prepared agent: refresh runtime files and spawn runtime.
        agent_ids = [s.agent_id for s in self.specs]
        handles = []
        for i, agent_id in enumerate(agent_ids):
            if i > 0 and self.config.agents.stagger_seconds > 0:
                logger.info(f"Staggering {agent_id} by {self.config.agents.stagger_seconds}s")
                time.sleep(self.config.agents.stagger_seconds)
            handle = self._setup_and_start_agent(agent_id, create_missing=False)
            handles.append(handle)

        self.handles = handles
        self._running = True
        self._write_run_state(status="running")

        # 5. Write PID file
        self._write_pid_file()

        # 6. Register atexit handler as safety net for unexpected exits
        atexit.register(self._atexit_cleanup)

        return handles

    def _start_grader_daemon(self) -> None:
        """Spawn the grader daemon subprocess. Idempotent.

        Before spawning, kills any stale daemon from a prior run whose PID is
        still recorded in .coral/public/grader_daemon.pid — otherwise two
        daemons would race for the same pending attempts.
        """
        assert self.paths is not None

        if self._grader_proc is not None and self._grader_proc.is_alive():
            return

        # Best-effort cleanup of a stale daemon from a previous run.
        pid_file = self.paths.coral_dir / "public" / "grader_daemon.pid"
        if pid_file.exists():
            try:
                stale_pid = int(pid_file.read_text().strip())
                os.kill(stale_pid, signal.SIGTERM)
                logger.info(f"Killed stale grader daemon PID {stale_pid}")
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass  # PID gone or unkillable — just move on
            try:
                pid_file.unlink()
            except OSError:
                pass

        # Lazy import — tests and CLI-only paths should not trigger grader import.
        from coral.grader.daemon import run_daemon

        stop_event = multiprocessing.Event()
        proc = multiprocessing.Process(
            target=run_daemon,
            args=(str(self.paths.coral_dir), stop_event),
            name="coral-grader-daemon",
            daemon=False,  # explicit: we manage its lifecycle
        )
        proc.start()
        self._grader_proc = proc
        self._grader_stop_event = stop_event
        try:
            pid_file.write_text(str(proc.pid))
        except OSError:
            pass
        logger.info(f"Grader daemon started (PID {proc.pid})")
        if self.verbose:
            print(f"[coral] Grader daemon running (PID {proc.pid})")

    def _stop_grader_daemon(self, timeout: float = 10.0) -> None:
        """Signal the grader daemon to stop, then wait and fall back to SIGTERM/SIGKILL."""
        proc = self._grader_proc
        if proc is None:
            return

        if self._grader_stop_event is not None:
            try:
                self._grader_stop_event.set()
            except Exception:
                pass

        try:
            proc.join(timeout=timeout)
            if proc.is_alive():
                logger.warning("Grader daemon ignored stop event; sending SIGTERM")
                proc.terminate()
                proc.join(timeout=5)
            if proc.is_alive():
                logger.warning("Grader daemon ignored SIGTERM; sending SIGKILL")
                proc.kill()
                proc.join(timeout=5)
        finally:
            try:
                proc.close()
            except Exception:
                pass
            self._grader_proc = None
            self._grader_stop_event = None
            if self.paths is not None:
                pid_file = self.paths.coral_dir / "public" / "grader_daemon.pid"
                try:
                    if pid_file.exists():
                        pid_file.unlink()
                except OSError:
                    pass
            logger.info("Grader daemon stopped")

    def _start_gateway_if_enabled(self) -> None:
        """Start the LiteLLM gateway if configured."""
        assert self.paths is not None
        gw_cfg = self.config.agents.gateway
        if not gw_cfg.enabled:
            return

        from coral.gateway.config import generate_default_litellm_config
        from coral.gateway.server import GatewayManager

        # Resolve config path relative to task dir
        config_path = gw_cfg.config
        if not config_path:
            # Generate default config at project root
            config_path = str(self.paths.run_dir / "litellm_config.yaml")
            generate_default_litellm_config(
                Path(config_path),
                model=self.config.agents.model,
            )
        elif not Path(config_path).is_absolute():
            if self.config.task_dir:
                config_path = str(self.config.task_dir / config_path)
            else:
                logger.warning(
                    f"Cannot resolve relative gateway config '{config_path}': "
                    f"task_dir is unknown. Trying as-is."
                )

        log_dir = self.paths.coral_dir / "public" / "gateway"
        gateway = GatewayManager(
            port=gw_cfg.port,
            config_path=config_path,
            api_key=gw_cfg.api_key,
            log_dir=log_dir,
        )
        gateway.start()
        self._gateway = gateway
        logger.info(f"Gateway running at {gateway.url}")

    def _setup_and_start_agent(
        self,
        agent_id: str,
        resume_session_id: str | None = None,
        prompt: str | None = None,
        prompt_source: str | None = None,
        max_turns: int | None = None,
        create_missing: bool = True,
    ) -> AgentHandle:
        """Set up a single agent and start it."""
        assert self.paths is not None

        worktree_path, instruction_file, model, runtime_options = self._materialize_agent_workspace(
            agent_id,
            create_missing=create_missing,
        )
        runtime = self._runtime_for(agent_id)

        # Start agent
        log_dir = self.paths.coral_dir / "public" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = runtime.start(
            worktree_path=worktree_path,
            coral_md_path=worktree_path / instruction_file,
            model=model,
            runtime_options=runtime_options,
            max_turns=max_turns if max_turns is not None else self.config.agents.max_turns,
            verbose=self.verbose,
            log_dir=log_dir,
            resume_session_id=resume_session_id,
            prompt=prompt,
            prompt_source=prompt_source,
            task_name=self.config.task.name,
            task_description=self.config.task.description,
            gateway_url=self._gateway.url if self._gateway else None,
            gateway_api_key=self._gateway_keys.get(agent_id),
        )
        # Record fresh process start time for the exit-classifier uptime check.
        self._started_at[agent_id] = time.time()
        return handle

    def _materialize_agent_workspace(
        self,
        agent_id: str,
        *,
        create_missing: bool,
    ) -> tuple[Path, str, str, dict[str, Any]]:
        """Create or refresh a prepared agent worktree without launching a runtime."""
        assert self.paths is not None

        runtime = self._runtime_for(agent_id)
        spec = self.specs_by_id.get(agent_id)

        # Create worktree (idempotent)
        logger.info(f"Setting up {agent_id}...")
        worktree_path = self.paths.agents_dir / agent_id
        if worktree_path.exists():
            logger.info(f"Worktree already exists at {worktree_path}, reusing")
        elif create_missing:
            worktree_path = create_agent_worktree(
                self.paths.repo_dir,
                agent_id,
                self.paths.agents_dir,
            )
        else:
            raise RuntimeError(
                f"Prepared run is missing worktree for {agent_id}: {worktree_path}"
            )
        logger.info(f"  Worktree: {worktree_path}")

        # Set up .gitignore for CORAL files
        setup_gitignore(worktree_path)

        # Run setup commands (uv sync, etc.) and install coral in the worktree
        setup_worktree_env(worktree_path, self.config.workspace.setup)

        # Write .coral_dir breadcrumb (used by workspace guard hook)
        write_coral_dir(worktree_path, self.paths.coral_dir)

        # Set up shared state directory (notes, skills, attempts symlinks)
        shared_dir_name = runtime.shared_dir_name
        setup_shared_state(
            worktree_path,
            self.paths.coral_dir,
            shared_dir_name,
        )
        setup_instruction_links(worktree_path, shared_dir_name)

        # Register agent with gateway if active (before settings so we have the key)
        if self._gateway and agent_id not in self._gateway_keys:
            proxy_key = self._gateway.register_agent(agent_id, worktree_path)
            self._gateway_keys[agent_id] = proxy_key

        gateway_url = self._gateway.url if self._gateway else None
        gateway_api_key = self._gateway_keys.get(agent_id)

        # Per-agent runtime/model/options come from the resolved spec when
        # available; resume paths that pre-date the specs map fall back to
        # the top-level defaults. Resolved here (before mounts apply) so
        # per-agent ``runtime_options.mounts`` can populate the worktree.
        if spec is not None:
            model = spec.model
            runtime_options = spec.runtime_options
        else:
            model = self.config.agents.model
            runtime_options = self.config.agents.runtime_options

        # Runtime-specific: write permission settings per worktree
        if shared_dir_name == ".claude":
            setup_claude_settings(
                worktree_path,
                coral_dir=self.paths.coral_dir,
                research=self.config.agents.research,
                gateway_url=gateway_url,
                gateway_api_key=gateway_api_key,
            )
        elif shared_dir_name == ".opencode":
            setup_opencode_settings(
                worktree_path,
                coral_dir=self.paths.coral_dir,
                research=self.config.agents.research,
                gateway_url=gateway_url,
                gateway_api_key=gateway_api_key,
            )
        elif shared_dir_name == ".codex":
            setup_codex_settings(
                worktree_path,
                coral_dir=self.paths.coral_dir,
                research=self.config.agents.research,
                gateway_url=gateway_url,
                gateway_api_key=gateway_api_key,
            )
        elif shared_dir_name == ".cursor":
            setup_cursor_settings(
                worktree_path,
                coral_dir=self.paths.coral_dir,
                research=self.config.agents.research,
                gateway_url=gateway_url,
                gateway_api_key=gateway_api_key,
            )

        # Apply per-agent file mounts last so the user's files win over
        # CORAL's defaults (e.g. dropping a custom .claude/settings.json
        # next to CORAL's settings.local.json — Claude Code merges both).
        mounts = (runtime_options or {}).get("mounts") or {}
        if mounts:
            apply_runtime_mounts(worktree_path, mounts, self._mounts_base_dir())

        # Write agent ID
        write_agent_id(worktree_path, agent_id)
        try:
            from coral.hub.kb import notebook_path

            notebook_path(self.paths.coral_dir, agent_id)
        except Exception as exc:
            logger.warning(f"Failed to initialize notebook for {agent_id}: {exc}")

        # Generate instruction file (CLAUDE.md, AGENTS.md, etc.)
        instruction_file = runtime.instruction_filename
        single_agent = len(self.specs) == 1
        agent_seed_brief, agent_seed_brief_path = self._read_agent_seed_brief(
            agent_id,
            shared_dir_name,
        )
        coral_md = generate_coral_md(
            self.config,
            agent_id,
            single_agent=single_agent,
            shared_dir=shared_dir_name,
            agent_seed_brief=agent_seed_brief,
            agent_seed_brief_path=agent_seed_brief_path,
        )
        (worktree_path / instruction_file).write_text(coral_md)

        return worktree_path, instruction_file, model, runtime_options

    def _restart_agent(
        self,
        idx: int,
        prompt: str | None = None,
        prompt_source: str | None = None,
    ) -> AgentHandle:
        """Restart a dead agent, resuming its session with optional feedback prompt."""
        old_handle = self.handles[idx]
        agent_id = old_handle.agent_id
        self._restart_counts[agent_id] = self._restart_counts.get(agent_id, 0) + 1

        # Ensure old process and file handles are fully cleaned up
        old_handle.stop()

        # Check if the previous exit was a session-not-found error
        session_id: str | None = None
        if not _log_has_session_error(old_handle.log_path):
            # Try to extract session_id from the old log for resumption
            session_id = self._runtime_for(agent_id).extract_session_id(old_handle.log_path)

        if session_id:
            logger.info(f"Resuming {agent_id} with session {session_id}")
        else:
            logger.info(f"Starting {agent_id} fresh (no session to resume)")

        return self._setup_and_start_agent(
            agent_id,
            resume_session_id=session_id,
            prompt=prompt,
            prompt_source=prompt_source or "restart",
        )

    def _interrupt_and_resume(
        self,
        idx: int,
        prompt: str,
        prompt_source: str | None = None,
    ) -> AgentHandle:
        """Interrupt a running agent and resume with a feedback prompt."""
        handle = self.handles[idx]
        agent_id = handle.agent_id

        # SIGINT the agent — it saves the session so we can resume it.
        # Each CLI emits a different log format, so extract the session_id
        # via the owning runtime after interrupt() returns.
        handle.interrupt()
        session_id = self._runtime_for(agent_id).extract_session_id(handle.log_path)
        self._restart_counts[agent_id] = self._restart_counts.get(agent_id, 0) + 1

        if session_id:
            logger.info(f"Interrupted {agent_id}, resuming session {session_id} with feedback")
        else:
            logger.warning(f"No session_id for {agent_id}, starting fresh")

        return self._setup_and_start_agent(
            agent_id,
            resume_session_id=session_id,
            prompt=prompt,
            prompt_source=prompt_source,
        )

    def resume_all(
        self,
        paths: ProjectPaths,
        instruction: str | None = None,
    ) -> list[AgentHandle]:
        """Resume agents into an existing run's worktrees."""
        self._start_time = datetime.now(UTC)
        self._runtime_start_epoch = self._start_time.timestamp()
        self.paths = paths
        self._refresh_runtime_deadline(force=True, status="starting")

        # Start gateway if configured
        self._start_gateway_if_enabled()

        # Start grader daemon (must be up before resumed agents submit evals).
        self._start_grader_daemon()

        # Kill any leftover agent processes from a previous run so they
        # don't hold session locks and block the new agents.
        self._kill_old_agent_processes()

        # Load saved sessions
        saved_sessions = self._load_saved_sessions()

        # Validate saved sessions by checking if they exist locally
        validated_sessions = _validate_sessions(saved_sessions, coral_dir=paths.coral_dir)

        # Discover agents from existing worktrees
        if not paths.agents_dir.is_dir():
            raise RuntimeError(f"No agents directory found at {paths.agents_dir}")

        agent_dirs = sorted(d for d in paths.agents_dir.iterdir() if d.is_dir())
        if not agent_dirs:
            raise RuntimeError(f"No agent worktrees found in {paths.agents_dir}")

        resumed_work_loop_prompt = (
            "You are in work_loop. This is a resumed run, and resumed runs always "
            "continue in work_loop even if the previous process stopped during reflect_loop.\n\n"
            "Before writing code:\n"
            "1. Read your notebook: `coral kb notebook --agent <your-agent-id>`\n"
            "2. Run `coral log` and `coral log --recent` to inspect current eval evidence\n"
            "3. Query practice knowledge with `coral kb index practice --by score|route|agent|metric`\n"
            "4. Inspect any relevant code change with `coral show <hash> --diff`\n\n"
            "Continue from the notebook plan, update the notebook with useful open-set "
            "findings, use or draft shared skills when directly useful, "
            "and submit the next eval when the work_loop has a concrete change."
        )

        handles = []
        for agent_dir in agent_dirs:
            agent_id = agent_dir.name
            session_id = validated_sessions.get(agent_id)

            # Fallback: extract from latest log file
            if not session_id:
                session_id = self._find_latest_session_from_logs(agent_id)
                # Validate this one too
                if session_id and not _session_exists(session_id, coral_dir=paths.coral_dir):
                    logger.info(
                        f"Session {session_id} for {agent_id} not found locally "
                        f"(different machine?), starting fresh"
                    )
                    session_id = None

            if session_id:
                logger.info(f"Resuming {agent_id} with session {session_id}")
                prompt = resumed_work_loop_prompt
            else:
                logger.info(f"Starting {agent_id} fresh (no session to resume)")
                prompt = resumed_work_loop_prompt

            if instruction:
                prompt = self._write_external_guidance_to_notebook(
                    agent_id,
                    instruction,
                    source="resume-instruction",
                    actor="operator",
                )

            handle = self._setup_and_start_agent(
                agent_id,
                resume_session_id=session_id,
                prompt=prompt,
            )
            handles.append(handle)

        self.handles = handles
        self._running = True
        self._write_run_state(status="running")
        self._write_pid_file()
        atexit.register(self._atexit_cleanup)
        return handles

    def _save_sessions(self) -> None:
        """Persist agent session IDs to sessions.json for later resume."""
        if not self.paths:
            return
        sessions: dict[str, str] = {}
        for handle in self.handles:
            sid = handle.session_id
            if not sid:
                sid = self._runtime_for(handle.agent_id).extract_session_id(handle.log_path)
            if sid:
                sessions[handle.agent_id] = sid
        sessions_file = self.paths.coral_dir / "public" / "sessions.json"
        sessions_file.write_text(json.dumps(sessions, indent=2))
        logger.info(f"Saved {len(sessions)} session ID(s) to sessions.json")

    def _load_saved_sessions(self) -> dict[str, str]:
        """Load saved session IDs from sessions.json."""
        if not self.paths:
            return {}
        sessions_file = self.paths.coral_dir / "public" / "sessions.json"
        if sessions_file.exists():
            try:
                return json.loads(sessions_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read sessions.json: {e}")
        return {}

    def _find_latest_session_from_logs(self, agent_id: str) -> str | None:
        """Extract session ID from the most recent log file for an agent."""
        if not self.paths:
            return None
        logs_dir = self.paths.coral_dir / "public" / "logs"
        if not logs_dir.exists():
            return None
        logs = sorted(
            logs_dir.glob(f"{agent_id}.*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        if logs:
            return self._runtime_for(agent_id).extract_session_id(logs[-1])
        return None

    def stop_all(self, *, reason: str = "manual") -> None:
        """Gracefully stop all agents.

        Uses SIGINT first so Claude Code can save sessions for later resume,
        then falls back to SIGTERM/SIGKILL if needed.
        """
        if self._stopping:
            return
        self._stopping = True
        self._write_run_state(status="stopping", stopped_reason=reason)
        self._running = False
        self._stop_event.set()
        # Save session IDs before killing processes
        self._save_sessions()
        for handle in self.handles:
            # Try graceful interrupt first so sessions can be resumed
            handle.interrupt()
        # Force-stop any that didn't exit
        for handle in self.handles:
            if handle.alive:
                handle.stop()
        self._cleanup_pid_file()
        # Stop grader daemon before the gateway so any in-flight grade can
        # finish its LLM call (if the grader uses the gateway).
        self._stop_grader_daemon()
        # Stop gateway after all agents are down
        if self._gateway:
            self._gateway.stop()
            self._gateway = None
        self._write_run_state(status="stopped", stopped_reason=reason)
        logger.info("All agents stopped.")

    def status(self) -> list[dict[str, Any]]:
        """Get status of all agents."""
        statuses = []
        for handle in self.handles:
            statuses.append(
                {
                    "agent_id": handle.agent_id,
                    "alive": handle.alive,
                    "pid": handle.process.pid if handle.process else None,
                    "worktree": str(handle.worktree_path),
                    "log": str(handle.log_path),
                    "session_id": handle.session_id,
                    "restarts": self._restart_counts.get(handle.agent_id, 0),
                }
            )
        return statuses

    def grader_daemon_alive(self) -> bool:
        """Whether the grader daemon subprocess is currently running."""
        proc = self._grader_proc
        return bool(proc and proc.is_alive())

    def _get_seen_attempts(self) -> set[str]:
        """Get the set of attempt filenames currently in public attempts."""
        assert self.paths is not None
        attempts_dir = self.paths.coral_dir / "public" / "attempts"
        if not attempts_dir.exists():
            return set()
        return {f.name for f in attempts_dir.glob("*.json")}

    def _resolve_attempt_path(self, fname: str) -> Path | None:
        """Look up an attempt JSON file under public attempts."""
        assert self.paths is not None
        p = self.paths.coral_dir / "public" / "attempts" / fname
        return p if p.exists() else None

    def _filter_scored(self, new_files: set[str]) -> set[str]:
        """Return only those filenames whose attempt status is not 'pending'.

        Pending attempts are grader-in-progress: the monitor loop must skip
        them (not trigger loop feedback, not advance score history) until the
        grader daemon finalizes them. Malformed files are also skipped and
        will be retried next tick.
        """
        scored: set[str] = set()
        for fname in new_files:
            path = self._resolve_attempt_path(fname)
            if path is None:
                continue
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                # Transient read (e.g. mid-rename on some filesystems) — retry next tick.
                continue
            status = data.get("status")
            if status and status != "pending":
                scored.add(fname)
        return scored

    def _read_latest_attempt(
        self, new_files: set[str], agent_id: str | None = None
    ) -> dict[str, Any] | None:
        """Read the most recent attempt from a set of new attempt filenames.

        When `agent_id` is provided, only attempts owned by that agent are
        considered. This prevents cross-agent score leakage when building a
        resume prompt for a dying agent in multi-agent runs.
        """
        newest_path: Path | None = None
        newest_data: dict[str, Any] | None = None
        newest_mtime = 0.0
        for fname in new_files:
            path = self._resolve_attempt_path(fname)
            if path is None:
                continue
            mtime = path.stat().st_mtime
            if mtime <= newest_mtime:
                continue
            if agent_id is not None:
                # When filtering, we have to read each candidate to inspect
                # its agent_id field; cache the parse so we do not re-read.
                try:
                    data = json.loads(path.read_text())
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"Failed to read attempt {path}: {e}")
                    continue
                if data.get("agent_id") != agent_id:
                    continue
                newest_mtime = mtime
                newest_path = path
                newest_data = data
            else:
                newest_mtime = mtime
                newest_path = path
                newest_data = None  # parse lazily below
        if newest_data is not None:
            return newest_data
        if newest_path is not None:
            try:
                return json.loads(newest_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read attempt {newest_path}: {e}")
        return None

    def _get_eval_count(self) -> int:
        """Read the current global eval count."""
        assert self.paths is not None
        counter_file = self.paths.coral_dir / "public" / "eval_count"
        if counter_file.exists():
            try:
                return int(counter_file.read_text().strip())
            except ValueError:
                pass
        return 0

    def _is_paused(self, agent_id: str) -> bool:
        """Return True if the agent is currently in PAUSED state.

        On expiry the deadline is cleared, the crash window is reset (so a
        single fresh exit cannot retrigger the breaker), and the agent is
        marked for an unconditional one-shot restart on the next dead-agent
        observation. This avoids re-classifying the same dead handle and
        double-counting the exit that originally triggered the pause.
        """
        if agent_id in self._manual_stopped_agents:
            return True
        until = self._paused_until.get(agent_id)
        if until is None:
            return False
        if time.time() >= until:
            self._paused_until.pop(agent_id, None)
            self._crash_history.pop(agent_id, None)
            self._pending_restart_after_pause.add(agent_id)
            self._persist_agent_state()
            logger.info(f"Agent {agent_id} pause expired; eligible for restart")
            return False
        return True

    def _classify_agent_exit(self, agent_id: str, log_path: Path, exit_code: int | None) -> str:
        """Dispatch to the runtime's classifier with the manager's uptime view."""
        started = self._started_at.get(agent_id)
        uptime = time.time() - started if started is not None else None
        min_clean = self.config.agents.min_clean_runtime_seconds
        runtime = self._runtime_for(agent_id)
        if hasattr(runtime, "classify_exit"):
            try:
                return runtime.classify_exit(
                    log_path,
                    exit_code,
                    uptime,
                    min_clean_runtime_seconds=min_clean,
                )
            except Exception as e:
                logger.warning(
                    f"runtime.classify_exit raised for {agent_id}: {e}; "
                    f"falling back to uptime heuristic"
                )
        return classify_by_uptime(exit_code, uptime, min_clean)

    def _record_crash(
        self,
        agent_id: str,
        exit_code: int | None,
        log_path: Path,
        classification: str,
    ) -> None:
        """Append a non-clean exit event and prune entries outside the window.

        When the breaker is disabled (any knob == 0) we do not even allocate
        history: the breaker cannot fire, so accumulating events would just
        leak memory across an overnight run.
        """
        if not self._breaker_enabled():
            return
        history = self._crash_history.setdefault(agent_id, deque(maxlen=64))
        history.append(
            RestartEvent(
                timestamp=time.time(),
                exit_code=exit_code,
                log_path=str(log_path),
                classification=classification,
            )
        )
        cutoff = time.time() - self.config.agents.restart_burst_window
        while history and history[0].timestamp < cutoff:
            history.popleft()

    def _breaker_enabled(self) -> bool:
        """Return True iff all three breaker knobs are positive (>0).

        Setting any of `restart_burst_threshold`, `restart_burst_window`, or
        `restart_pause_seconds` to 0 disables the breaker entirely, matching
        the `agents.timeout=0`-disables-the-stall-watchdog convention.
        """
        cfg = self.config.agents
        return (
            cfg.restart_burst_threshold > 0
            and cfg.restart_burst_window > 0
            and cfg.restart_pause_seconds > 0
        )

    def _should_pause_for_burst(self, agent_id: str) -> bool:
        """Return True iff the recent crash count meets the configured threshold."""
        if not self._breaker_enabled():
            return False
        history = self._crash_history.get(agent_id)
        if not history:
            return False
        return len(history) >= self.config.agents.restart_burst_threshold

    def _enter_paused(self, agent_id: str, log_path: Path) -> None:
        """Transition the agent into PAUSED, dump fault evidence, persist state."""
        pause_seconds = self.config.agents.restart_pause_seconds
        self._paused_until[agent_id] = time.time() + pause_seconds
        self._pause_count[agent_id] = self._pause_count.get(agent_id, 0) + 1
        fault_at = self._dump_fault_log(agent_id, log_path)
        if fault_at:
            self._last_fault_at[agent_id] = fault_at
        self._persist_agent_state()
        burst_count = len(self._crash_history.get(agent_id, []))
        logger.warning(
            f"Agent {agent_id} entered PAUSED for {pause_seconds}s after "
            f"{burst_count} crashes within {self.config.agents.restart_burst_window}s. "
            f"Fault dump under public/diagnostics/{agent_id}/fault.log"
        )
        if self.verbose:
            print(
                f"[coral] {agent_id} PAUSED ({pause_seconds}s) — "
                f"see public/diagnostics/{agent_id}/fault.log"
            )

    def _dump_fault_log(self, agent_id: str, log_path: Path) -> str | None:
        """Write a fault dump under public/diagnostics/<agent_id>/fault.log.

        The file is overwritten on each pause cycle so stale data does not
        linger. Returns the ISO-8601 timestamp of the dump on success, or
        None if the dump could not be written.
        """
        assert self.paths is not None
        diag_dir = self.paths.coral_dir / "public" / "diagnostics" / agent_id
        try:
            diag_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create diagnostics dir for {agent_id}: {e}")
            return None
        fault_path = diag_dir / "fault.log"
        now_iso = datetime.now(UTC).isoformat()
        history = list(self._crash_history.get(agent_id, []))
        try:
            with open(fault_path, "w", encoding="utf-8") as f:
                f.write(f"# Fault dump for {agent_id}\n")
                f.write(f"# Written: {now_iso}\n")
                f.write(f"# Pause cycle #{self._pause_count.get(agent_id, 0)}\n")
                f.write(f"# Burst window: {self.config.agents.restart_burst_window}s\n")
                f.write(f"# Burst threshold: {self.config.agents.restart_burst_threshold}\n")
                f.write("# Recent crash events (oldest first):\n")
                for ev in history:
                    ev_iso = datetime.fromtimestamp(ev.timestamp, UTC).isoformat()
                    f.write(
                        f"#   {ev_iso} exit_code={ev.exit_code} "
                        f"classification={ev.classification} log={ev.log_path}\n"
                    )
                f.write("#\n")
                f.write(f"# --- Last 200 lines of {log_path} ---\n")
                try:
                    tail: deque[str] = deque(maxlen=200)
                    with open(log_path, encoding="utf-8", errors="replace") as src:
                        for line in src:
                            tail.append(line)
                    f.writelines(tail)
                except OSError as e:
                    f.write(f"# (could not read agent log: {e})\n")
                # Append the per-agent stderr tail when available — typically
                # this is where startup-time crash messages land for runtimes
                # that emit nothing useful to the stream-json log.
                err_path = self.paths.coral_dir / "public" / "diagnostics" / agent_id / "agent.err"
                if err_path.exists():
                    f.write(f"#\n# --- Last 100 lines of {err_path} ---\n")
                    try:
                        err_tail: deque[str] = deque(maxlen=100)
                        with open(err_path, encoding="utf-8", errors="replace") as src:
                            for line in src:
                                err_tail.append(line)
                        f.writelines(err_tail)
                    except OSError as e:
                        f.write(f"# (could not read stderr capture: {e})\n")
            return now_iso
        except OSError as e:
            logger.error(f"Failed to write fault dump for {agent_id}: {e}")
            return None

    def _grader_alive(self) -> bool:
        """Return True iff the grader daemon multiprocessing.Process is alive.

        We use the live process handle the manager already owns
        (`self._grader_proc`) rather than the on-disk
        `<coral_dir>/public/grader_daemon_liveness` file. The liveness file
        is only refreshed in the daemon's idle path and around each grade
        attempt; during a long-running grade subprocess the file's mtime can
        drift past any reasonable freshness threshold. The live process check
        is both stricter (catches a daemon that died mid-grade) and looser
        on the only axis that matters (does not falsely report dead during a
        healthy long grade).
        """
        proc = self._grader_proc
        if proc is None:
            return False
        try:
            return bool(proc.is_alive())
        except Exception:
            return False

    def _attempt_age_seconds(self, timestamp_iso: str) -> float | None:
        """Return age in seconds of an attempt's ISO timestamp, or None on parse failure."""
        try:
            ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (datetime.now(UTC) - ts).total_seconds()

    def _should_enter_reflect_loop(self, attempt: dict[str, Any], budget_class: str) -> bool:
        """Return True when a finalized attempt should enter reflect_loop."""
        if budget_class != BUDGET_CLASS_REAL:
            return False
        if attempt.get("score") is None:
            return False
        return attempt.get("status") not in {"pending", "crashed", "timeout"}

    def _mark_agent_reflect_loop(self, agent_id: str, detail: str) -> None:
        self._reflect_started_at[agent_id] = time.time()
        self._reflect_detail[agent_id] = detail

    def _clear_agent_reflect_loop(self, agent_id: str) -> None:
        self._reflect_started_at.pop(agent_id, None)
        self._reflect_detail.pop(agent_id, None)

    def _persist_agent_state(self) -> None:
        """Persist current visible lifecycle state to public/agent_state.json."""
        if self.paths is None:
            return
        document = AgentStateDocument()
        for handle in self.handles:
            agent_id = handle.agent_id
            state_started_at = None
            state_detail = None
            until = self._paused_until.get(agent_id)
            if agent_id in self._manual_stopped_agents:
                state = "stopped"
            elif until is not None:
                state = "paused"
            elif agent_id in self._reflect_started_at:
                state = "reflect_loop"
                state_started_at = self._reflect_started_at.get(agent_id)
                state_detail = self._reflect_detail.get(agent_id)
            else:
                state = "active"
            document.agents[agent_id] = AgentRuntimeState(
                state=state,
                paused_until=until,
                pause_count=self._pause_count.get(agent_id, 0),
                last_fault_at=self._last_fault_at.get(agent_id),
                state_started_at=state_started_at,
                state_detail=state_detail,
            )
        try:
            write_agent_state(self.paths.coral_dir, document)
        except OSError as e:
            logger.error(f"Failed to persist agent_state.json: {e}")

    def _build_score_prompt(self, attempt: dict[str, Any], eval_count: int) -> str:
        """Build a work_loop resume prompt with latest eval context."""
        score = attempt.get("score")
        score_str = f"{score:.10f}" if score is not None else "FAILED"
        commit = attempt.get("commit_hash", "unknown")[:12]
        feedback = attempt.get("feedback", "")
        title = attempt.get("title", "")
        agent_id = attempt.get("agent_id", "unknown")

        lines = [
            "You are in work_loop.",
            "",
            "Read your current notebook first:",
            f"- `coral kb notebook --agent {agent_id}`",
            "",
            f"Eval #{eval_count}: score={score_str} (commit {commit})",
            f"What you did: {title}",
        ]
        if feedback:
            lines.append(f"Feedback: {feedback}")
        lines.extend(
            [
                "",
                "Keep working. Do NOT exit just because progress has stalled, the "
                "obvious next steps are exhausted, or you concluded last session that "
                "the task is intractable / saturated / done. Even when no immediate "
                "path forward is visible, there is always productive work to do:",
                "",
                "- **Gather new information.** Read parts of the codebase, docs, or "
                "data you haven't touched. Profile or instrument what you've been "
                "guessing at. Search the web for related work. Check what other "
                "agents have tried via `coral kb index practice --by score`, "
                "`coral kb index practice --by route`, and `coral log -n 10`.",
                "- **Run trial experiments.** Probe assumptions you've been treating "
                "as facts. Ablate components you've been treating as load-bearing. "
                "Where the grader supports it, sweep variants cheaply with "
                "`coral eval --tune` before committing a real eval.",
                "- **Update the notebook.** Use `coral kb note \"...\" --agent "
                f"{agent_id}` for short observations before the next official eval.",
                "- **Use skills pragmatically.** Inspect `coral skills` or the shared skills directory "
                "for reusable tools. You may draft or update a skill/helper script during work_loop "
                "only when it directly supports the current eval; durable cleanup belongs in reflect_loop.",
            ]
        )
        if self.config.agents.count > 1:
            lines.append(
                "- **Find a complementary technical route.** Reflect on what you've "
                "learned so far and on what your teammates are working on "
                "(`coral kb index practice --by route`, `coral log -n 10 --recent`). Pick a route "
                "that complements rather than duplicates them: investigate a "
                "sub-problem nobody is testing, build a shared tool they're missing, "
                "or pursue a method family they haven't explored."
            )
        lines.extend(
            [
                "",
                "A short acknowledgment of the current state is not an acceptable "
                "session. Pick one of the above and act on it.",
            ]
        )
        return "\n".join(lines)

    def _build_reflect_loop_prompt(self, attempt: dict[str, Any], agent_eval_count: int) -> str:
        """Build the reflect_loop prompt after a successful real eval."""
        score = attempt.get("score")
        score_str = f"{score:.10f}" if score is not None else "FAILED"
        commit = attempt.get("commit_hash", "unknown")
        commit_short = commit[:12]
        feedback = attempt.get("feedback", "")
        title = attempt.get("title", "")
        agent_id = attempt.get("agent_id", "unknown")

        lines = [
            "You are in reflect_loop.",
            "",
            "This phase is for knowledge maintenance only. Do not edit solution code. "
            "Do not run `coral eval`. Do not continue exploration.",
            "",
            f"## Eval #{agent_eval_count} Results",
            "",
            f"Score: {score_str}",
            f"Commit: {commit_short}",
            f"What you did: {title}",
        ]
        if feedback:
            lines.append(f"Feedback: {feedback}")
        lines.extend(
            [
                "",
                "## Required Actions",
                "",
                "1. Read the current notebook:",
                f"   `coral kb notebook --agent {agent_id}`",
                "2. Review the eval evidence for this commit and any detailed report available through `coral show`.",
                "3. Query reference knowledge before writing the reflection: `coral kb index manual`, "
                "`coral kb index external`, and teammate practice indexes such as "
                "`coral kb index practice --by score` and `coral kb index practice --by route`.",
                "4. If it is directly relevant, use web search or downloaded reference projects to calibrate the reflection.",
                "5. Critique whether your technical route is still independent from teammates' stronger routes; "
                "the next plan should preserve useful diversity instead of converging by default.",
                "6. If the eval validated a reusable technique, script, diagnostic, or workflow, "
                "create or update the corresponding shared skill under the runtime skills directory. "
                "Keep one-off observations in the practice chain.",
                "7. Write a short method summary, a reflection, and the next work_loop plan to temporary markdown files.",
                "8. Archive this eval into the practice chain:",
                f"   `coral kb archive --attempt {commit_short} --agent {agent_id} --route \"<route>\" --method-file <method.md> --reflection-file <reflection.md> --next-plan <next-plan.md>`",
                "9. The next-plan file resets the notebook for the next work_loop. The archive command will first "
                "archive the old notebook, then write the next plan.",
                "10. After the archive command succeeds, stop. The manager will resume work_loop.",
                "",
                "The archive must preserve the score, eval report, git commit pointer, method overview, "
                "and reflect_loop note so other agents can find it via `coral kb index practice --by score|route|metric`.",
            ]
        )
        return "\n".join(lines)

    def monitor_loop(self, check_interval: int = 5) -> None:
        """Monitor agents, deliver eval feedback via --resume, auto-restart.

        Watches .coral/public/attempts/ for newly scored attempt files. A successful
        real eval interrupts the owning agent into reflect_loop so it can
        archive practice knowledge. Failed/tune attempts remain in work_loop.
        If an agent exits, it resumes with a work_loop score summary.
        """

        def _signal_handler(sig: int, frame: Any) -> None:
            if self._stopping:
                # Second Ctrl+C: force immediate exit
                logger.warning("Force exit (second signal)")
                for handle in self.handles:
                    if handle.process and handle.alive:
                        try:
                            os.killpg(os.getpgid(handle.process.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            try:
                                handle.process.kill()
                            except Exception:
                                pass
                self._cleanup_pid_file()
                os._exit(1)
            logger.info(f"Received signal {sig}, shutting down...")
            self.stop_all()

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

        # Only mark already-scored attempts as "seen" at startup. Pending
        # attempts left over from a previous manager (still in the grader
        # queue or mid-grade when we came up) need to flow through the
        # normal new-attempts path so reflect_loop fires for them when they
        # transition to scored.
        seen_attempts = self._filter_scored(self._get_seen_attempts())

        logger.info(f"Monitoring {len(self.handles)} agent(s) (check every {check_interval}s)...")

        while self._running:
            if self._maybe_stop_for_deadline():
                break
            self._apply_agent_control_requests()

            # Check for new attempts
            current_attempts = self._get_seen_attempts()
            new_attempts = current_attempts - seen_attempts

            # Pending attempts (grader daemon hasn't scored them yet) are kept
            # on the re-check list — we neither mark them as seen nor trigger
            # loop feedback until they transition to a terminal status.
            scored_new = self._filter_scored(new_attempts)
            seen_attempts = seen_attempts | scored_new

            if scored_new:
                attempt_data = self._read_latest_attempt(scored_new)

                if attempt_data:
                    committing_agent_id = attempt_data.get("agent_id")
                    if not committing_agent_id:
                        continue
                    had_reflect_state = committing_agent_id in self._reflect_started_at
                    self._clear_agent_reflect_loop(committing_agent_id)

                    # Increment per-agent eval count
                    self._agent_eval_counts[committing_agent_id] = (
                        self._agent_eval_counts.get(committing_agent_id, 0) + 1
                    )
                    agent_eval_count = self._agent_eval_counts[committing_agent_id]
                    # Only "real" attempts advance score history. Tune-mode
                    # and grader_error attempts are recorded but don't trigger
                    # reflect_loop.
                    budget_class = get_budget_class(attempt_data.get("metadata"))
                    score = attempt_data.get("score")
                    minimize = self.config.grader.direction == "minimize"
                    if budget_class == BUDGET_CLASS_REAL:
                        # Update strict-> personal best (always, regardless of epsilon)
                        if score is not None:
                            prev_best = self._agent_best_scores.get(committing_agent_id)
                            strictly_improved = (
                                prev_best is None
                                or (minimize and score < prev_best)
                                or (not minimize and score > prev_best)
                            )
                            if strictly_improved:
                                self._agent_best_scores[committing_agent_id] = score
                        # Append to score history (None for broken evals).
                        self._agent_score_history.setdefault(committing_agent_id, []).append(score)

                    if not self._should_enter_reflect_loop(attempt_data, budget_class):
                        if had_reflect_state:
                            self._persist_agent_state()
                        continue

                    # Find the committing agent's handle
                    committing_idx = None
                    for i, handle in enumerate(self.handles):
                        if handle.agent_id == committing_agent_id and handle.alive:
                            committing_idx = i
                            break
                    if committing_idx is None:
                        continue

                    combined_prompt = self._build_reflect_loop_prompt(
                        attempt_data,
                        agent_eval_count,
                    )
                    logger.info(
                        f"reflect_loop (agent eval #{agent_eval_count}): "
                        f"interrupting {committing_agent_id} for archive"
                    )
                    if self.verbose:
                        print(
                            f"\n[coral] Agent eval #{agent_eval_count}: score={attempt_data.get('score', '?')}"
                        )
                        print(f"[coral] Interrupting {committing_agent_id} for reflect_loop...\n")
                    self.handles[committing_idx] = self._interrupt_and_resume(
                        committing_idx,
                        combined_prompt,
                        prompt_source="reflect_loop",
                    )
                    self._mark_agent_reflect_loop(committing_agent_id, "archive latest eval")
                    self._write_agent_pids()
                    self._persist_agent_state()

            # Check for dead agents (max-turns exit, crash, etc.)
            for i, handle in enumerate(self.handles):
                if not handle.alive and self._running:
                    agent_id = handle.agent_id

                    # Honor an active PAUSED window: skip the restart entirely
                    # until the cooldown deadline passes.
                    if self._is_paused(agent_id):
                        continue

                    # Just-expired pause: restart without re-classifying. The
                    # exit that triggered the pause was already counted; the
                    # crash window was cleared on expiry, so a single fresh
                    # exit on the new process cannot retrigger the breaker.
                    if agent_id in self._pending_restart_after_pause:
                        self._pending_restart_after_pause.discard(agent_id)
                        count = self._restart_counts.get(agent_id, 0) + 1
                        eval_count = self._get_eval_count()
                        latest = self._read_latest_attempt(current_attempts, agent_id=agent_id)
                        prompt = self._build_score_prompt(latest, eval_count) if latest else None
                        logger.warning(
                            f"Agent {agent_id} restarting after pause cooldown (restart #{count})"
                        )
                        if self.verbose:
                            print(f"[coral] {agent_id} resuming after pause cooldown")
                        self.handles[i] = self._restart_agent(
                            i, prompt=prompt, prompt_source="post-pause"
                        )
                        self._write_agent_pids()
                        continue

                    exit_code = handle.process.returncode if handle.process else None
                    log_path = handle.log_path

                    # Classify the exit. Only non-clean exits feed the breaker;
                    # clean `max_turns`-style completions never trip it.
                    classification = self._classify_agent_exit(agent_id, log_path, exit_code)
                    if classification != "clean":
                        self._record_crash(agent_id, exit_code, log_path, classification)
                        if self._should_pause_for_burst(agent_id):
                            self._enter_paused(agent_id, log_path)
                            self._write_agent_pids()
                            continue

                    count = self._restart_counts.get(agent_id, 0) + 1

                    # Build resume prompt from this agent's own latest attempt
                    # so multi-agent runs do not feed cross-agent feedback.
                    eval_count = self._get_eval_count()
                    latest = self._read_latest_attempt(current_attempts, agent_id=agent_id)
                    if latest:
                        prompt = self._build_score_prompt(latest, eval_count)
                    else:
                        prompt = None

                    logger.warning(
                        f"Agent {agent_id} exited "
                        f"(code: {exit_code}, classification: {classification}), "
                        f"restart #{count}"
                    )
                    if self.verbose:
                        print(
                            f"[coral] {agent_id} exited "
                            f"(code: {exit_code}, {classification}), resuming..."
                        )
                    self.handles[i] = self._restart_agent(i, prompt=prompt)
                    self._write_agent_pids()

            # Check for stalled agents (alive but no output for > timeout).
            # `agents.timeout == 0` disables the watchdog entirely.
            stall_threshold = self.config.agents.timeout
            if stall_threshold > 0:
                # Cache pending attempts and the grader liveness once per tick
                # so per-agent exemption checks do not rescan the attempts dir.
                attempts_cache = read_attempts(self.paths.coral_dir)
                grader_alive = self._grader_alive()

                for i, handle in enumerate(self.handles):
                    if handle.alive and self._running:
                        try:
                            age = time.time() - handle.log_path.stat().st_mtime
                        except OSError:
                            continue
                        if age <= stall_threshold:
                            continue

                        # Grader-queue exemption: an agent that just submitted
                        # an attempt is silent because the grader is working,
                        # not because it deadlocked. Skip the stall check
                        # only when the grader process is alive AND the
                        # pending attempt has not aged past the cap (so a
                        # forgotten pending file cannot mask a true hang).
                        if grader_alive:
                            pending = agent_in_grader_queue(
                                self.paths.coral_dir,
                                handle.agent_id,
                                attempts_cache,
                            )
                            if pending is not None:
                                pending_age = self._attempt_age_seconds(pending.timestamp)
                                if (
                                    pending_age is not None
                                    and pending_age <= self.config.agents.grader_pending_max_age
                                ):
                                    logger.info(
                                        f"Agent {handle.agent_id} silent for "
                                        f"{int(age)}s but pending attempt "
                                        f"{pending.commit_hash[:12]} is in grader queue "
                                        f"({int(pending_age)}s old); "
                                        f"stall check exempt"
                                    )
                                    continue

                        logger.warning(
                            f"Agent {handle.agent_id} stalled "
                            f"({int(age)}s since last output), restarting"
                        )
                        if self.verbose:
                            print(
                                f"[coral] {handle.agent_id} stalled "
                                f"({int(age)}s with no output), restarting..."
                            )
                        self.handles[i] = self._interrupt_and_resume(
                            i,
                            "You were automatically restarted because you "
                            "produced no output for an extended period. "
                            "Continue working on the task.",
                            prompt_source="timeout",
                        )
                        self._write_agent_pids()

            # Interruptible sleep
            if self._stop_event.wait(timeout=check_interval):
                break

    def wait_for_completion(self) -> None:
        """Single-agent verbose mode: watch for attempts and deliver feedback via --resume."""
        self.monitor_loop(check_interval=3)

    def _kill_old_agent_processes(self) -> None:
        """Kill leftover agent processes from a previous run.

        When resuming, old claude processes may still hold session locks,
        preventing new agents from resuming those sessions.  We send
        SIGINT first so Claude Code can save the session gracefully,
        then escalate to SIGKILL if needed.
        """
        if not self.paths:
            return
        agent_pids_file = self.paths.coral_dir / "public" / "agent.pids"
        if not agent_pids_file.exists():
            return

        pids = []
        for line in agent_pids_file.read_text().strip().splitlines():
            line = line.strip()
            if line:
                pids.append(int(line))

        if not pids:
            return

        # SIGINT first for graceful session save
        for pid in pids:
            try:
                os.kill(pid, signal.SIGINT)
                logger.info(f"Sent SIGINT to leftover agent process {pid}")
            except (ProcessLookupError, PermissionError):
                pass

        # Wait for graceful exit
        time.sleep(3)

        # Force kill any survivors
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                logger.info(f"Force-killed leftover agent process {pid}")
            except (ProcessLookupError, PermissionError):
                pass

    def _write_pid_file(self) -> None:
        if self.paths:
            pid_file = self.paths.coral_dir / "public" / "manager.pid"
            pid_file.write_text(str(os.getpid()))
            # Also write agent PIDs so coral stop can kill them as fallback
            self._write_agent_pids()

    def _write_agent_pids(self) -> None:
        """Write agent PIDs to file for fallback cleanup by coral stop."""
        if self.paths:
            agent_pids_file = self.paths.coral_dir / "public" / "agent.pids"
            pids = []
            pid_map = {}
            for handle in self.handles:
                if handle.process and handle.process.pid and handle.alive:
                    pids.append(str(handle.process.pid))
                    pid_map[handle.agent_id] = handle.process.pid
            agent_pids_file.write_text("\n".join(pids))
            # Also write JSON mapping for the web UI to check process liveness
            pid_map_file = self.paths.coral_dir / "public" / "agent_pids.json"
            pid_map_file.write_text(json.dumps(pid_map))

    def _atexit_cleanup(self) -> None:
        """Safety net: kill any surviving agent processes on interpreter exit."""
        self._save_sessions()
        for handle in self.handles:
            if handle.process and handle.alive:
                try:
                    os.killpg(os.getpgid(handle.process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    try:
                        handle.process.kill()
                    except Exception:
                        pass
        # Kill grader daemon too if still running.
        proc = self._grader_proc
        if proc is not None and proc.is_alive():
            try:
                proc.kill()
            except Exception:
                pass
        if self._gateway:
            self._gateway.stop()
            self._gateway = None
        self._cleanup_pid_file()

    def _cleanup_pid_file(self) -> None:
        if self.paths:
            for name in ("manager.pid", "agent.pids", "agent_pids.json"):
                f = self.paths.coral_dir / "public" / name
                if f.exists():
                    f.unlink()


def _session_exists(session_id: str, coral_dir: Path | None = None) -> bool:
    """Check if a Claude Code session exists locally.

    Checks the CORAL sessions dir first (sessions stored with results via
    CLAUDE_CONFIG_DIR), then falls back to the default Claude Code locations.
    """
    # Check CORAL sessions dir (stored with results, portable across machines)
    if coral_dir:
        sessions_dir = coral_dir / "public" / "sessions"
        if sessions_dir.exists():
            for project_dir in sessions_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                if (project_dir / f"{session_id}.jsonl").exists():
                    return True

    # Check default Claude Code locations
    for base in [
        Path.home() / ".config" / "claude" / "projects",
        Path.home() / ".claude" / "projects",
    ]:
        if not base.exists():
            continue
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            if (project_dir / f"{session_id}.jsonl").exists():
                return True
    return False


def _validate_sessions(
    sessions: dict[str, str],
    coral_dir: Path | None = None,
) -> dict[str, str]:
    """Filter saved sessions to only those that exist locally."""
    if not sessions:
        return {}
    validated = {}
    for agent_id, session_id in sessions.items():
        if _session_exists(session_id, coral_dir=coral_dir):
            validated[agent_id] = session_id
        else:
            logger.info(
                f"Session {session_id} for {agent_id} not found locally "
                f"(different machine?), will start fresh"
            )
    return validated
