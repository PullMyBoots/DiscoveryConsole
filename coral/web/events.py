"""SSE (Server-Sent Events) endpoint with file-system watcher."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import StreamingResponse

from coral.hub.attempts import read_eval_count


class FileWatcher:
    """Watches .coral/ directory for changes and broadcasts SSE events."""

    def __init__(
        self,
        coral_dir: Path,
        poll_interval: float = 2.0,
        subscribers: list[asyncio.Queue[dict[str, Any]]] | None = None,
    ):
        self.coral_dir = coral_dir
        self.poll_interval = poll_interval
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = (
            subscribers if subscribers is not None else []
        )
        self._state: dict[str, Any] = {}
        self._running = False

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _snapshot(self) -> dict[str, Any]:
        """Take a snapshot of the .coral/ directory state."""
        state: dict[str, Any] = {}

        # Attempts: count + latest mtime
        attempt_files: list[Path] = []
        attempts_dir = self.coral_dir / "public" / "attempts"
        if attempts_dir.exists():
            attempt_files.extend(attempts_dir.glob("*.json"))
        state["attempts_count"] = len(attempt_files)
        state["attempts_mtime"] = max((f.stat().st_mtime for f in attempt_files), default=0)

        # Notes: mtime
        note_files: list[Path] = []
        notes_dir = self.coral_dir / "public" / "notes"
        if notes_dir.exists():
            note_files.extend(notes_dir.rglob("*.md"))
        state["notes_mtime"] = max((f.stat().st_mtime for f in note_files), default=0)

        # Logs: per-file sizes
        log_sizes: dict[str, int] = {}
        progress_mtimes: dict[str, float] = {}
        logs_dir = self.coral_dir / "public" / "logs"
        if logs_dir.exists():
            for lf in logs_dir.glob("*.log"):
                log_sizes[lf.name] = lf.stat().st_size
        eval_logs_dir = self.coral_dir / "public" / "eval_logs"
        if eval_logs_dir.exists():
            for pf in eval_logs_dir.glob("*/progress.jsonl"):
                progress_mtimes[pf.parent.name] = pf.stat().st_mtime
        state["log_sizes"] = log_sizes
        state["progress_mtimes"] = progress_mtimes

        # Eval count
        state["eval_count"] = read_eval_count(self.coral_dir)

        job_files: list[Path] = []
        jobs_dir = self.coral_dir / "public" / "jobs"
        if jobs_dir.exists():
            job_files.extend(jobs_dir.glob("*/job.json"))
        state["jobs_count"] = len(job_files)
        state["jobs_mtime"] = max((f.stat().st_mtime for f in job_files), default=0)

        run_state = self.coral_dir / "public" / "run_state.json"
        state["run_state_mtime"] = run_state.stat().st_mtime if run_state.exists() else 0

        return state

    async def run(self) -> None:
        """Main polling loop. Call as an asyncio task."""
        self._running = True
        self._state = self._snapshot()

        while self._running:
            await asyncio.sleep(self.poll_interval)

            new_state = self._snapshot()

            # Detect changes
            if new_state["attempts_count"] > self._state.get("attempts_count", 0):
                self._broadcast(
                    {
                        "event": "attempt:new",
                        "data": {
                            "count": new_state["attempts_count"],
                            "previous": self._state.get("attempts_count", 0),
                        },
                    }
                )

            if new_state["attempts_mtime"] > self._state.get("attempts_mtime", 0):
                self._broadcast(
                    {
                        "event": "attempt:update",
                        "data": {"mtime": new_state["attempts_mtime"]},
                    }
                )

            if new_state["notes_mtime"] > self._state.get("notes_mtime", 0):
                self._broadcast(
                    {
                        "event": "note:update",
                        "data": {"mtime": new_state["notes_mtime"]},
                    }
                )

            # Check log file growth
            old_sizes = self._state.get("log_sizes", {})
            for name, size in new_state["log_sizes"].items():
                old_size = old_sizes.get(name, 0)
                if size > old_size:
                    self._broadcast(
                        {
                            "event": "log:update",
                            "data": {"file": name, "size": size, "delta": size - old_size},
                        }
                    )

            old_progress = self._state.get("progress_mtimes", {})
            for name, mtime in new_state["progress_mtimes"].items():
                if mtime > old_progress.get(name, 0):
                    self._broadcast(
                        {
                            "event": "eval:progress",
                            "data": {"job": name, "mtime": mtime},
                        }
                    )

            if new_state["eval_count"] != self._state.get("eval_count", 0):
                self._broadcast(
                    {
                        "event": "eval:update",
                        "data": {"count": new_state["eval_count"]},
                    }
                )

            if (
                new_state["jobs_count"] != self._state.get("jobs_count", 0)
                or new_state["jobs_mtime"] > self._state.get("jobs_mtime", 0)
            ):
                self._broadcast(
                    {
                        "event": "job:update",
                        "data": {
                            "count": new_state["jobs_count"],
                            "mtime": new_state["jobs_mtime"],
                        },
                    }
                )

            if new_state["run_state_mtime"] > self._state.get("run_state_mtime", 0):
                self._broadcast(
                    {
                        "event": "run:update",
                        "data": {"mtime": new_state["run_state_mtime"]},
                    }
                )

            self._state = new_state

    def stop(self) -> None:
        self._running = False


async def sse_endpoint(request: Request) -> StreamingResponse:
    """GET /api/events — Server-Sent Events stream."""
    watcher: FileWatcher = request.app.state.watcher

    queue = watcher.subscribe()

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Send initial connected event
            yield f"event: connected\ndata: {json.dumps({'status': 'ok'})}\n\n"

            keepalive_interval = 15.0
            last_keepalive = time.time()

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    event_type = event.get("event", "message")
                    data = json.dumps(event.get("data", {}))
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except TimeoutError:
                    # Send keepalive if enough time has passed
                    now = time.time()
                    if now - last_keepalive >= keepalive_interval:
                        yield f"event: keepalive\ndata: {json.dumps({'time': now})}\n\n"
                        last_keepalive = now

                # Check if client disconnected
                if await request.is_disconnected():
                    break
        finally:
            watcher.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
