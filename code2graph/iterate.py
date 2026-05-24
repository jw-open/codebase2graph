from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .builder import build_graph
from .prompt import (
    TestResult,
    build_action_prompt,
    build_iteration_prompt,
    find_previous_snapshot,
    load_graph,
    summarize_graph,
    write_iteration_prompt,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_report(report_file: Path, summary: dict[str, object], snapshot: Path) -> str:
    line = (
        f"- {_utc_now()} generated `{snapshot}` with "
        f"{summary['node_count']} nodes and {summary['edge_count']} edges."
    )
    if report_file.exists():
        existing = report_file.read_text(encoding="utf-8")
    else:
        existing = "# code2graph Iteration Progress\n\n"
    report_file.write_text(existing.rstrip() + "\n" + line + "\n", encoding="utf-8")
    return line


def _notify(command: str | None, message: str, cwd: Path) -> None:
    if not command:
        return
    subprocess.run(command, input=message, cwd=cwd, check=False, text=True, shell=True)


def _run_test(command: str | None, cwd: Path) -> TestResult | None:
    if not command:
        return None
    result = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True, shell=True)
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return TestResult(command=command, returncode=result.returncode, output=output)


def _find_codex_binary(explicit: str | None = None) -> str:
    if explicit:
        return explicit

    candidates = [
        shutil.which("codex"),
        str(Path.home() / ".npm-global" / "bin" / "codex"),
        str(Path.home() / ".local" / "bin" / "codex"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("codex CLI was not found; pass --codex-bin or fix PATH")


def _codex_env() -> dict[str, str]:
    env = os.environ.copy()
    extra_paths = [
        str(Path.home() / ".npm-global" / "bin"),
        str(Path.home() / ".local" / "bin"),
    ]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])
    return env


def _invoke_codex(action_prompt: str, workdir: Path, log_file: Path, *, codex_bin: str | None, timeout: int) -> str:
    """
    Invoke Codex headlessly with the given action prompt piped to stdin.
    Uses the same flags as the discord_codex_relay.mjs relay.
    Returns the output text (truncated to 4000 chars for logging).
    """
    import tempfile

    resolved_codex_bin = _find_codex_binary(codex_bin)
    output_file = Path(tempfile.mktemp(prefix="code2graph-codex-", suffix=".txt"))
    args = [
        resolved_codex_bin,
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--cd",
        str(workdir),
        "--sandbox",
        "danger-full-access",
        "--output-last-message",
        str(output_file),
        "-",
    ]
    try:
        result = subprocess.run(
            args,
            input=action_prompt,
            cwd=workdir,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=_codex_env(),
        )
        output = ""
        if output_file.exists():
            output = output_file.read_text(encoding="utf-8", errors="replace")
            output_file.unlink(missing_ok=True)
        combined = "\n".join(filter(None, [output, result.stdout, result.stderr])).strip()
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"\n--- Codex run at {_utc_now()} (exit {result.returncode}) ---\n")
            f.write(f"command: {shlex.join(args)}\n")
            f.write(combined[:8000] + "\n")
        return combined[:4000]
    except Exception as exc:  # pragma: no cover
        msg = f"Codex invocation failed: {exc}"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"\n--- Codex error at {_utc_now()} ---\n{msg}\n")
        return msg


def _commit_and_push(repo_root: Path, message: str, paths: list[Path]) -> None:
    _run(["git", "config", "user.name", "jw-open"], repo_root)
    _run(["git", "config", "user.email", "176761431+jw-open@users.noreply.github.com"], repo_root)
    _run(["git", "add", *[str(path) for path in paths]], repo_root)
    diff = _run(["git", "diff", "--cached", "--quiet"], repo_root)
    if diff.returncode == 0:
        return
    commit = _run(["git", "commit", "-m", message], repo_root)
    if commit.returncode == 0:
        _run(["git", "push", "origin", "main"], repo_root)


def _push(repo_root: Path) -> None:
    _run(["git", "push", "origin", "main"], repo_root)


def _committable_changed_paths(repo_root: Path) -> list[Path]:
    result = _run(["git", "status", "--porcelain"], repo_root)
    allowed_roots = {"code2graph", "tests", "examples"}
    allowed_names = {"README.md", "ROADMAP.md", "pyproject.toml", ".gitignore", "LICENSE"}
    blocked_names = {"CODE2GRAPH_PROGRESS.md", "CODE2GRAPH_NEXT_PROMPT.md"}
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        path = Path(raw_path)
        if str(path).startswith(".code2graph-runs/") or path.name in blocked_names:
            continue
        if path.parts and (path.parts[0] in allowed_roots or path.name in allowed_names):
            paths.append(path)
    return paths


def _commit_source_changes(repo_root: Path, message: str) -> bool:
    paths = _committable_changed_paths(repo_root)
    if not paths:
        return False
    _commit_and_push(repo_root, message, paths)
    return True


def _notify_webhook(webhook_url: str | None, message: str) -> None:
    if not webhook_url:
        return
    payload = json.dumps({"content": message[:1900]}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except (urllib.error.URLError, TimeoutError):
        return


def run_once(args: argparse.Namespace, repo_root: Path) -> str:
    analyzed_repo = Path(args.repo).resolve()
    graph = build_graph(analyzed_repo, args.graph).to_dict()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = Path(args.output_dir) / f"{analyzed_repo.name}.{args.graph}.{stamp}.json"
    previous_snapshot = find_previous_snapshot(Path(args.output_dir), snapshot, analyzed_repo.name, args.graph)
    previous_summary = summarize_graph(load_graph(previous_snapshot)) if previous_snapshot else None
    _write_json(snapshot, graph)
    summary = summarize_graph(graph)
    pre_test_result = _run_test(args.test_command, repo_root)
    prompt_file = Path(args.prompt_file)
    context_prompt = build_iteration_prompt(
        repo_path=analyzed_repo,
        graph_type=args.graph,
        snapshot=snapshot,
        summary=summary,
        previous_snapshot=previous_snapshot,
        previous_summary=previous_summary,
        test_result=pre_test_result,
    )
    write_iteration_prompt(prompt_file, context_prompt)
    report_line = _append_report(Path(args.report_file), summary, snapshot)
    test_status = ""
    if pre_test_result is not None:
        test_status = " Pre-tests passed." if pre_test_result.passed else f" Pre-tests failed: `{pre_test_result.command}`."

    codex_summary = "codex disabled"
    committed = False
    post_test_result: TestResult | None = None
    if args.codex:
        log_file = repo_root / ".code2graph-runs" / "loop.log"
        action_prompt = build_action_prompt(context_prompt, repo_root)
        codex_output = _invoke_codex(
            action_prompt,
            repo_root,
            log_file,
            codex_bin=args.codex_bin,
            timeout=args.codex_timeout_seconds,
        )
        codex_summary = codex_output[:300].replace("\n", " ").strip() if codex_output else "(no output)"
        post_test_result = _run_test(args.test_command, repo_root)
        if args.commit_push:
            if post_test_result is None or post_test_result.passed:
                committed = _commit_source_changes(repo_root, f"Improve code2graph iteration {stamp}")
                _push(repo_root)
    elif args.commit_push:
        # Manual/non-Codex mode keeps the old progress behavior explicit.
        _commit_and_push(repo_root, f"chore: record code2graph iteration {stamp}", [Path(args.report_file), prompt_file])

    post_status = ""
    if post_test_result is not None:
        post_status = " Post-tests passed." if post_test_result.passed else f" Post-tests failed: `{post_test_result.command}`."
    commit_status = " committed source changes." if committed else " no source commit created."
    report_message = f"{report_line} Prompt: `{prompt_file}`.{test_status}{post_status} Codex: {codex_summary};{commit_status}"
    _notify(args.report_command, report_message, repo_root)
    _notify_webhook(args.discord_webhook_url or os.environ.get("CODE2GRAPH_DISCORD_WEBHOOK_URL"), report_message)
    return report_message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repeated code2graph analysis iterations.")
    parser.add_argument("repo", help="Repository path to analyze.")
    parser.add_argument("--graph", default="all", choices=["all", "folder", "call", "entity", "schema", "workflow"])
    parser.add_argument("--interval-minutes", type=float, default=20.0)
    parser.add_argument("--iterations", type=int, default=1, help="Number of loops to run. Use 0 for forever.")
    parser.add_argument("--output-dir", default=".code2graph-runs")
    parser.add_argument("--report-file", default="CODE2GRAPH_PROGRESS.md")
    parser.add_argument("--prompt-file", default="CODE2GRAPH_NEXT_PROMPT.md")
    parser.add_argument("--report-command", help="Optional shell command that receives the progress line on stdin.")
    parser.add_argument("--discord-webhook-url", help="Optional Discord webhook URL. Defaults to CODE2GRAPH_DISCORD_WEBHOOK_URL.")
    parser.add_argument(
        "--test-command",
        default="python -m pytest -q",
        help="Optional shell command to run after graph generation. Use an empty string to skip.",
    )
    parser.add_argument("--commit-push", action="store_true", help="Commit progress using jwpublic identity and push main.")
    parser.add_argument("--codex", action="store_true", help="Invoke Codex each loop to implement one source improvement.")
    parser.add_argument("--codex-bin", help="Path to the codex CLI. Defaults to PATH or ~/.npm-global/bin/codex.")
    parser.add_argument("--codex-timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    count = 0
    while args.iterations == 0 or count < args.iterations:
        try:
            print(run_once(args, repo_root), flush=True)
        except Exception as exc:  # pragma: no cover - defensive loop behavior
            print(f"code2graph iteration failed: {exc}", file=sys.stderr, flush=True)
            _notify(args.report_command, f"code2graph iteration failed: {exc}", repo_root)
            return 1
        count += 1
        if args.iterations == 0 or count < args.iterations:
            time.sleep(args.interval_minutes * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
