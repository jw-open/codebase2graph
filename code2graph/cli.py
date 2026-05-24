from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import GRAPH_BUILDERS, build_graph


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
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args(argv)

    graph = build_graph(args.repo, args.graph).to_dict()
    payload = json.dumps(graph, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

