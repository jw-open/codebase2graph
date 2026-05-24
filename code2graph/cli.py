from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import GRAPH_BUILDERS, build_graph
from .prompt import summarize_graph
from .update import update_existing_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate OhWise-compatible graph JSON from a source repository.")
    parser.add_argument("repo", help="Repository path to analyze.")
    parser.add_argument(
        "--graph",
        choices=["all", *GRAPH_BUILDERS.keys()],
        default="all",
        help="Graph type to generate.",
    )
    parser.add_argument("--output", "-o", help="Write JSON to this file instead of stdout.")
    parser.add_argument(
        "--update-existing",
        help=(
            "Update an existing OhWise graph JSON file. When --output is omitted, "
            "the existing file is updated in place."
        ),
    )
    parser.add_argument("--update-summary-output", help="Write update diff summary JSON to this file.")
    parser.add_argument("--summary-output", help="Write graph summary JSON to this file.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args(argv)

    graph = build_graph(args.repo, args.graph).to_dict()
    if args.update_existing:
        existing_path = Path(args.update_existing)
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        graph, update_summary = update_existing_graph(existing, graph)
        if args.update_summary_output:
            update_summary_output = Path(args.update_summary_output)
            update_summary_output.parent.mkdir(parents=True, exist_ok=True)
            update_summary_output.write_text(
                json.dumps(update_summary.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    if args.summary_output:
        summary_output = Path(args.summary_output)
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(
            json.dumps(summarize_graph(graph), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    payload = json.dumps(graph, indent=2 if args.pretty else None, sort_keys=True)
    if args.output or args.update_existing:
        output = Path(args.output) if args.output else Path(args.update_existing)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
