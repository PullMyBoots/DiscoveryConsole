"""Starlette application factory for the CORAL web dashboard."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from coral.web.api import (
    add_knowledge_note,
    add_knowledge_source,
    create_run,
    get_agent_attempts,
    get_attempt_detail,
    get_attempts,
    get_config,
    get_control_config,
    get_control_instruction,
    get_control_plan,
    get_control_readiness,
    get_evals,
    get_knowledge,
    get_knowledge_eval_spec,
    get_leaderboard,
    get_logs,
    get_logs_list,
    get_notes,
    get_review,
    get_runs,
    get_skill_detail,
    get_skills,
    get_status,
    prompt_agent,
    resume_agent,
    resume_control_run,
    save_control_config,
    save_control_instruction,
    save_knowledge_eval_spec,
    stop_agent,
    stop_control_run,
    switch_run,
    update_knowledge_source_status,
)
from coral.web.events import FileWatcher, sse_endpoint

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_local_origin(value: str | None) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in _LOCAL_ORIGIN_HOSTS


class RejectNonLocalWriteMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method.upper() not in _SAFE_METHODS:
            origin = request.headers.get("origin") or request.headers.get("referer")
            if not _is_local_origin(origin):
                return Response("Forbidden: dashboard writes require a local origin", status_code=403)
        return await call_next(request)


def create_app(coral_dir: Path, results_dir: Path | None = None) -> Starlette:
    """Create the Starlette application.

    Args:
        coral_dir: Path to the .coral/ directory to serve.
        results_dir: Path to the top-level results/ directory (for run listing).
                     If not provided, derived from coral_dir.
    """
    coral_dir = Path(coral_dir).resolve()
    if results_dir is None:
        # coral_dir = results/<task>/<run>/.coral → results_dir = results/
        results_dir = coral_dir.parent.parent.parent
    results_dir = Path(results_dir).resolve()
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        # startup
        app.state.coral_dir = coral_dir
        app.state.results_dir = results_dir
        app.state._switch_lock = asyncio.Lock()
        app.state.watcher = FileWatcher(coral_dir)
        app.state._watcher_task = asyncio.create_task(app.state.watcher.run())
        try:
            yield
        finally:
            # shutdown
            app.state.watcher.stop()
            app.state._watcher_task.cancel()
            try:
                await app.state._watcher_task
            except asyncio.CancelledError:
                pass

    # SPA fallback: serve index.html for any non-API, non-static route
    async def spa_fallback(request: Request) -> Response:
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        return Response("Dashboard not built. Run: cd web && npm run build", status_code=404)

    routes = [
        # API routes
        Route("/api/config", get_config),
        Route("/api/attempts", get_attempts),
        Route("/api/leaderboard", get_leaderboard),
        Route("/api/evals", get_evals),
        Route("/api/attempts/agent/{id}", get_agent_attempts),
        Route("/api/attempts/{hash}", get_attempt_detail),
        Route("/api/notes", get_notes),
        Route("/api/knowledge", get_knowledge),
        Route("/api/review", get_review),
        Route("/api/knowledge/eval-spec", get_knowledge_eval_spec),
        Route("/api/knowledge/eval-spec", save_knowledge_eval_spec, methods=["POST"]),
        Route("/api/knowledge/notes", add_knowledge_note, methods=["POST"]),
        Route("/api/knowledge/sources", add_knowledge_source, methods=["POST"]),
        Route("/api/knowledge/sources/status", update_knowledge_source_status, methods=["POST"]),
        Route("/api/skills", get_skills),
        Route("/api/skills/{name}", get_skill_detail),
        Route("/api/logs", get_logs_list),
        Route("/api/logs/{agent_id}", get_logs),
        Route("/api/status", get_status),
        Route("/api/agents/{id}/stop", stop_agent, methods=["POST"]),
        Route("/api/agents/{id}/resume", resume_agent, methods=["POST"]),
        Route("/api/agents/{id}/prompt", prompt_agent, methods=["POST"]),
        Route("/api/runs", get_runs),
        Route("/api/runs/new", create_run, methods=["POST"]),
        Route("/api/runs/switch", switch_run, methods=["POST"]),
        Route("/api/control/config", get_control_config),
        Route("/api/control/config", save_control_config, methods=["POST"]),
        Route("/api/control/plan", get_control_plan),
        Route("/api/control/readiness", get_control_readiness),
        Route("/api/control/instruction", get_control_instruction),
        Route("/api/control/instruction", save_control_instruction, methods=["POST"]),
        Route("/api/control/resume", resume_control_run, methods=["POST"]),
        Route("/api/control/stop", stop_control_run, methods=["POST"]),
        Route("/api/events", sse_endpoint),
    ]

    # Mount static files if the directory exists (post-build)
    if static_dir.exists():
        routes.append(
            Mount("/assets", app=StaticFiles(directory=static_dir / "assets"), name="assets")
            if (static_dir / "assets").exists()
            else Mount("/static", app=StaticFiles(directory=static_dir), name="static")
        )

    # SPA catch-all must be last
    routes.append(Route("/{path:path}", spa_fallback))

    middleware = [
        Middleware(RejectNonLocalWriteMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )

    return app
