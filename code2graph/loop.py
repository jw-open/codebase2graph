from __future__ import annotations

import argparse
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path

from .builder import GRAPH_BUILDERS

RUNTIME_DIR = ".code2graph-runs"
PID_FILE = "loop.pid"
LOG_FILE = "loop.log"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runtime_path(name: str) -> Path:
    return _repo_root() / RUNTIME_DIR / name


def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _build_iterate_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "code2graph.iterate",
        str(Path(args.repo).resolve()),
        "--graph",
        args.graph,
        "--interval-minutes",
        str(args.interval_minutes),
        "--iterations",
        "0",
        "--output-dir",
        args.output_dir,
        "--report-file",
        args.report_file,
        "--prompt-file",
        args.prompt_file,
        "--test-command",
        args.test_command,
    ]
    if args.commit_push:
        command.append("--commit-push")
    if args.codex:
        command.append("--codex")
    codex_bin = args.codex_bin or shutil.which("codex") or str(Path.home() / ".npm-global" / "bin" / "codex")
    if codex_bin:
        command.extend(["--codex-bin", codex_bin])
    if args.codex_timeout_seconds:
        command.extend(["--codex-timeout-seconds", str(args.codex_timeout_seconds)])
    if args.discord_webhook_url:
        command.extend(["--discord-webhook-url", args.discord_webhook_url])
    if args.report_command:
        command.extend(["--report-command", args.report_command])
    return command


def _loop_env() -> dict[str, str]:
    env = os.environ.copy()
    extra_paths = [
        str(Path.home() / ".npm-global" / "bin"),
        str(Path.home() / ".local" / "bin"),
    ]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])
    return env


def start(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    runtime_dir = repo_root / RUNTIME_DIR
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _runtime_path(PID_FILE)
    existing_pid = _read_pid(pid_file)
    if _is_running(existing_pid):
        print(f"code2graph loop already running with pid {existing_pid}")
        return 0

    log_file = _runtime_path(LOG_FILE)
    command = _build_iterate_command(args)
    with log_file.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=_loop_env(),
        )
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    print(f"started code2graph loop pid {process.pid}; log: {log_file}")
    return 0


def status(_args: argparse.Namespace) -> int:
    pid = _read_pid(_runtime_path(PID_FILE))
    if _is_running(pid):
        print(f"code2graph loop running with pid {pid}")
        return 0
    print("code2graph loop is not running")
    return 1


def stop(_args: argparse.Namespace) -> int:
    pid_file = _runtime_path(PID_FILE)
    pid = _read_pid(pid_file)
    if not _is_running(pid):
        pid_file.unlink(missing_ok=True)
        print("code2graph loop is not running")
        return 0
    assert pid is not None
    os.kill(pid, signal.SIGTERM)
    pid_file.unlink(missing_ok=True)
    print(f"stopped code2graph loop pid {pid}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a detached 20-minute code2graph iteration loop.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the detached iteration loop.")
    start_parser.add_argument("repo", help="Repository path to analyze.")
    start_parser.add_argument("--graph", default="all", choices=["all", *GRAPH_BUILDERS.keys()])
    start_parser.add_argument("--interval-minutes", type=float, default=20.0)
    start_parser.add_argument("--output-dir", default=RUNTIME_DIR)
    start_parser.add_argument("--report-file", default="CODE2GRAPH_PROGRESS.md")
    start_parser.add_argument("--prompt-file", default="CODE2GRAPH_NEXT_PROMPT.md")
    start_parser.add_argument("--report-command")
    start_parser.add_argument("--discord-webhook-url")
    start_parser.add_argument("--test-command", default="python -m pytest -q")
    start_parser.add_argument("--codex-bin")
    start_parser.add_argument("--codex-timeout-seconds", type=int, default=900)
    start_parser.add_argument("--no-codex", dest="codex", action="store_false")
    start_parser.add_argument("--no-commit-push", dest="commit_push", action="store_false")
    start_parser.set_defaults(func=start, commit_push=True, codex=True)

    status_parser = subparsers.add_parser("status", help="Show whether the loop is running.")
    status_parser.set_defaults(func=status)

    stop_parser = subparsers.add_parser("stop", help="Stop the detached loop.")
    stop_parser.set_defaults(func=stop)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
