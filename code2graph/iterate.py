from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .builder import build_graph


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _summarize_graph(graph: dict[str, object]) -> dict[str, object]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    kinds: dict[str, int] = {}
    labels: dict[str, int] = {}
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            attrs = node.get("attributes", {})
            if isinstance(attrs, dict):
                kind = str(attrs.get("kind", "unknown"))
                kinds[kind] = kinds.get(kind, 0) + 1
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            label = str(edge.get("label", "unknown"))
            labels[label] = labels.get(label, 0) + 1
    return {
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "edge_count": len(edges) if isinstance(edges, list) else 0,
        "node_kinds": dict(sorted(kinds.items())),
        "edge_labels": dict(sorted(labels.items())),
    }


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


def _commit_and_push(repo_root: Path, message: str) -> None:
    _run(["git", "config", "user.name", "jw-open"], repo_root)
    _run(["git", "config", "user.email", "176761431+jw-open@users.noreply.github.com"], repo_root)
    _run(["git", "add", "CODE2GRAPH_PROGRESS.md"], repo_root)
    diff = _run(["git", "diff", "--cached", "--quiet"], repo_root)
    if diff.returncode == 0:
        return
    commit = _run(["git", "commit", "-m", message], repo_root)
    if commit.returncode == 0:
        _run(["git", "push", "origin", "main"], repo_root)


def run_once(args: argparse.Namespace, repo_root: Path) -> str:
    analyzed_repo = Path(args.repo).resolve()
    graph = build_graph(analyzed_repo, args.graph).to_dict()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = Path(args.output_dir) / f"{analyzed_repo.name}.{args.graph}.{stamp}.json"
    _write_json(snapshot, graph)
    summary = _summarize_graph(graph)
    report_line = _append_report(Path(args.report_file), summary, snapshot)
    _notify(args.report_command, report_line, repo_root)
    if args.commit_push:
        _commit_and_push(repo_root, f"Record code2graph iteration {stamp}")
    return report_line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repeated code2graph analysis iterations.")
    parser.add_argument("repo", help="Repository path to analyze.")
    parser.add_argument("--graph", default="all", choices=["all", "folder", "call", "entity", "schema", "workflow"])
    parser.add_argument("--interval-minutes", type=float, default=20.0)
    parser.add_argument("--iterations", type=int, default=1, help="Number of loops to run. Use 0 for forever.")
    parser.add_argument("--output-dir", default=".code2graph-runs")
    parser.add_argument("--report-file", default="CODE2GRAPH_PROGRESS.md")
    parser.add_argument("--report-command", help="Optional shell command that receives the progress line on stdin.")
    parser.add_argument("--commit-push", action="store_true", help="Commit progress using jwpublic identity and push main.")
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
