"""Commands: ui."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from coral.cli._helpers import find_coral_dir
from coral.config import CoralConfig
from coral.workspace.project import slugify


def _ensure_ui_built() -> None:
    """Auto-build the React frontend if static files are missing or stale."""
    static_dir = Path(__file__).parent.parent / "web" / "static"
    index_html = static_dir / "index.html"

    repo_root = Path(__file__).parent.parent.parent
    web_dir = repo_root / "web"

    if not (web_dir / "package.json").exists():
        if index_html.exists():
            return
        print(
            "Error: Dashboard not built and web/ source not found.\n"
            "Run from the repo root:  cd web && npm install && npm run build",
            file=sys.stderr,
        )
        sys.exit(1)

    needs_build = not index_html.exists()
    if not needs_build:
        build_time = index_html.stat().st_mtime
        src_dir = web_dir / "src"
        if src_dir.is_dir():
            for src_file in src_dir.rglob("*"):
                if src_file.is_file() and src_file.stat().st_mtime > build_time:
                    needs_build = True
                    break
        for cfg in ("package.json", "vite.config.ts", "tsconfig.json", "index.html"):
            cfg_path = web_dir / cfg
            if cfg_path.exists() and cfg_path.stat().st_mtime > build_time:
                needs_build = True
                break

    if not needs_build:
        return

    print("[coral] Building dashboard frontend...")

    needs_install = not (web_dir / "node_modules").exists()
    if not needs_install:
        pkg_mtime = (web_dir / "package.json").stat().st_mtime
        lock_file = web_dir / "node_modules" / ".package-lock.json"
        if lock_file.exists():
            needs_install = pkg_mtime > lock_file.stat().st_mtime
        else:
            needs_install = True

    if needs_install:
        print("[coral]   npm install...")
        result = subprocess.run(
            ["npm", "install"],
            cwd=web_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output = (result.stdout + "\n" + result.stderr).strip()
            print(f"Error: npm install failed:\n{output}", file=sys.stderr)
            sys.exit(1)

    print("[coral]   npm run build...")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=web_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = (result.stdout + "\n" + result.stderr).strip()
        print(f"Error: npm build failed:\n{output}", file=sys.stderr)
        sys.exit(1)

    print("[coral]   Done.")


def _ensure_ui_deps() -> None:
    """Auto-install UI dependencies if missing."""
    missing: list[str] = []
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        missing.append("uvicorn")
    try:
        import starlette  # noqa: F401
    except ImportError:
        missing.append("starlette")
    if missing:
        repo_root = Path(__file__).parent.parent.parent
        if not (repo_root / "pyproject.toml").exists():
            print(
                "Error: Web UI dependencies are missing: "
                f"{', '.join(missing)}.\n"
                "Reinstall DiscoveryConsole from the current repository or install the UI extra.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("[coral] UI dependencies not installed. Running: uv sync --extra ui ...")
        result = subprocess.run(
            ["uv", "sync", "--extra", "ui"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output = (result.stdout + "\n" + result.stderr).strip()
            print(f"Error: failed to install UI dependencies:\n{output}", file=sys.stderr)
            sys.exit(1)
        print("[coral] UI dependencies installed.")


def start_ui_background(coral_dir: Path, port: int = 8420, host: str = "127.0.0.1") -> None:
    """Start the web dashboard in a background thread."""
    _ensure_ui_deps()
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: Web UI dependencies still not available after install.",
            file=sys.stderr,
        )
        return

    _ensure_ui_built()

    import threading

    from coral.web import create_app

    results_dir = coral_dir.resolve().parent.parent.parent
    app = create_app(coral_dir, results_dir=results_dir)
    url = f"http://{host}:{port}"

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    print(f"Dashboard:     {url}")

    import webbrowser

    webbrowser.open(url)


def _task_dir_from_ui_args(task: str | None, run: str | None) -> Path | None:
    """Return a task directory suitable for pre-run dashboard creation."""
    if run:
        return None
    candidates: list[Path] = []
    if task:
        raw = Path(task).expanduser()
        candidates.extend([raw, Path.cwd() / raw])
    else:
        candidates.append(Path.cwd())

    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_file() and candidate.name == "task.yaml":
            return candidate.parent
        if candidate.is_dir() and (candidate / "task.yaml").is_file():
            return candidate
    return None


def _resolve_task_relative(path_text: str, task_dir: Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = task_dir / path
    return path.resolve()


def _latest_coral_for_task(task_dir: Path, config: CoralConfig) -> Path | None:
    results_dir = _resolve_task_relative(config.workspace.results_dir, task_dir)
    latest = results_dir / slugify(config.task.name) / "latest"
    if not latest.exists():
        return None
    resolved = latest.resolve() if latest.is_symlink() else latest
    coral_dir = resolved / ".coral" if (resolved / ".coral").is_dir() else resolved
    return coral_dir if coral_dir.is_dir() else None


def _prepare_prelaunch_run_if_task_dir(task: str | None, run: str | None) -> Path | None:
    """Create or reuse a timestamp workspace so `coral ui` can open before start."""
    task_dir = _task_dir_from_ui_args(task, run)
    if task_dir is None:
        return None

    config_path = task_dir / "task.yaml"
    config = CoralConfig.from_yaml(config_path)
    config.task_dir = task_dir

    existing = _latest_coral_for_task(task_dir, config)
    if existing is not None:
        return existing

    print("[coral] No timestamp run found; preparing a pre-launch dashboard workspace...")
    from coral.agent.manager import AgentManager

    paths = AgentManager(config, config_dir=task_dir).prepare_all()
    print(f"[coral] Prepared run workspace: {paths.run_dir}")
    return paths.coral_dir


def cmd_ui(args: argparse.Namespace) -> None:
    """Launch the web dashboard.

    Examples:
      coral ui                      Open dashboard in browser
      coral ui --port 9000          Use custom port
    """
    _ensure_ui_deps()
    import uvicorn

    _ensure_ui_built()

    coral_dir = _prepare_prelaunch_run_if_task_dir(
        getattr(args, "task", None),
        getattr(args, "run", None),
    )
    if coral_dir is None:
        coral_dir = find_coral_dir(getattr(args, "task", None), getattr(args, "run", None))

    from coral.web import create_app

    results_dir = coral_dir.resolve().parent.parent.parent
    app = create_app(coral_dir, results_dir=results_dir)
    url = f"http://{args.host}:{args.port}"
    print(f"CORAL Dashboard: {url}")
    print(f"Serving data from: {coral_dir}")

    # Write PID so `coral stop` can kill us
    pid_file = coral_dir / "public" / "ui.pid"
    import os

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    if not args.no_open:
        import webbrowser

        webbrowser.open(url)

    print("Stop with: coral stop\n")

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        pid_file.unlink(missing_ok=True)
