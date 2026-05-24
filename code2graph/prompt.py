from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TestResult:
    command: str
    returncode: int
    output: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def load_graph(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_graph(graph: dict[str, object]) -> dict[str, object]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    kinds: dict[str, int] = {}
    labels: dict[str, int] = {}
    node_ids: set[str] = set()
    linked_node_ids: set[str] = set()
    nodes_by_id: dict[str, dict[str, object]] = {}
    incoming_counts: dict[str, int] = {}
    outgoing_counts: dict[str, int] = {}
    semantic_edge_node_ids: set[str] = set()
    dangling_edges = 0

    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id", ""))
            if node_id:
                node_ids.add(node_id)
                nodes_by_id[node_id] = node
            attrs = node.get("attributes", {})
            if isinstance(attrs, dict):
                kind = str(attrs.get("kind", "unknown"))
                kinds[kind] = kinds.get(kind, 0) + 1

    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            from_id = str(edge.get("from", ""))
            to_id = str(edge.get("to", ""))
            linked_node_ids.update({from_id, to_id})
            outgoing_counts[from_id] = outgoing_counts.get(from_id, 0) + 1
            incoming_counts[to_id] = incoming_counts.get(to_id, 0) + 1
            if from_id not in node_ids or to_id not in node_ids:
                dangling_edges += 1
            label = str(edge.get("label", "unknown"))
            labels[label] = labels.get(label, 0) + 1
            if label != "contains":
                semantic_edge_node_ids.update({from_id, to_id})

    isolated = node_ids - linked_node_ids
    isolated.discard(str(graph.get("current_node_id", "")))
    return {
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "edge_count": len(edges) if isinstance(edges, list) else 0,
        "node_kinds": dict(sorted(kinds.items())),
        "edge_labels": dict(sorted(labels.items())),
        "dangling_edge_count": dangling_edges,
        "isolated_node_count": len(isolated),
        "entrypoints": _entrypoints(nodes_by_id),
        "high_fan_in": _ranked_nodes(nodes_by_id, incoming_counts),
        "high_fan_out": _ranked_nodes(nodes_by_id, outgoing_counts),
        "isolated_modules": _isolated_modules(nodes_by_id, semantic_edge_node_ids),
    }


def _node_summary(node_id: str, node: dict[str, object], *, count: int | None = None) -> dict[str, object]:
    attrs = node.get("attributes", {})
    attributes = attrs if isinstance(attrs, dict) else {}
    summary: dict[str, object] = {
        "id": node_id,
        "label": str(node.get("label", "")),
        "kind": str(attributes.get("kind", "unknown")),
    }
    path = attributes.get("path")
    if path is not None:
        summary["path"] = str(path)
    if count is not None:
        summary["count"] = count
    return summary


def _entrypoints(nodes_by_id: dict[str, dict[str, object]], limit: int = 12) -> list[dict[str, object]]:
    entrypoint_kinds = {
        "ci_workflow",
        "compose_service",
        "make_target",
        "npm_script",
        "python_entrypoint",
    }
    entries: list[dict[str, object]] = []
    for node_id, node in nodes_by_id.items():
        attrs = node.get("attributes", {})
        if isinstance(attrs, dict) and attrs.get("kind") in entrypoint_kinds:
            entries.append(_node_summary(node_id, node))
    return sorted(entries, key=lambda item: (str(item.get("kind", "")), str(item.get("id", ""))))[:limit]


def _ranked_nodes(
    nodes_by_id: dict[str, dict[str, object]],
    counts: dict[str, int],
    limit: int = 8,
) -> list[dict[str, object]]:
    ranked = [
        _node_summary(node_id, nodes_by_id[node_id], count=count)
        for node_id, count in counts.items()
        if count > 0 and node_id in nodes_by_id
    ]
    return sorted(ranked, key=lambda item: (-int(item["count"]), str(item["id"])))[:limit]


def _isolated_modules(
    nodes_by_id: dict[str, dict[str, object]],
    semantic_edge_node_ids: set[str],
    limit: int = 12,
) -> list[dict[str, object]]:
    modules: list[dict[str, object]] = []
    for node_id, node in nodes_by_id.items():
        attrs = node.get("attributes", {})
        if isinstance(attrs, dict) and attrs.get("kind") == "file" and node_id not in semantic_edge_node_ids:
            modules.append(_node_summary(node_id, node))
    return sorted(modules, key=lambda item: str(item.get("path", item["id"])))[:limit]


def find_previous_snapshot(output_dir: Path, current_snapshot: Path, repo_name: str, graph_type: str) -> Path | None:
    pattern = f"{repo_name}.{graph_type}.*.json"
    candidates = sorted(path for path in output_dir.glob(pattern) if path != current_snapshot)
    return candidates[-1] if candidates else None


def diff_summaries(current: dict[str, object], previous: dict[str, object] | None) -> dict[str, object]:
    if previous is None:
        return {"has_previous": False}

    current_nodes = int(current.get("node_count", 0))
    previous_nodes = int(previous.get("node_count", 0))
    current_edges = int(current.get("edge_count", 0))
    previous_edges = int(previous.get("edge_count", 0))
    return {
        "has_previous": True,
        "node_delta": current_nodes - previous_nodes,
        "edge_delta": current_edges - previous_edges,
        "dangling_edge_delta": int(current.get("dangling_edge_count", 0))
        - int(previous.get("dangling_edge_count", 0)),
        "isolated_node_delta": int(current.get("isolated_node_count", 0))
        - int(previous.get("isolated_node_count", 0)),
    }


def _top_counts(counts: object, limit: int = 8) -> str:
    if not isinstance(counts, dict) or not counts:
        return "- none"
    ordered = sorted(((str(k), int(v)) for k, v in counts.items()), key=lambda item: (-item[1], item[0]))
    return "\n".join(f"- {name}: {count}" for name, count in ordered[:limit])


def _top_nodes(nodes: object, limit: int = 8) -> str:
    if not isinstance(nodes, list) or not nodes:
        return "- none"
    lines: list[str] = []
    for node in nodes[:limit]:
        if not isinstance(node, dict):
            continue
        path = f" `{node['path']}`" if node.get("path") else ""
        count = f" ({node['count']})" if node.get("count") is not None else ""
        lines.append(f"- {node.get('label', '')} [{node.get('kind', 'unknown')}]{path}{count}")
    return "\n".join(lines) if lines else "- none"


def _format_test_result(result: TestResult | None) -> str:
    if result is None:
        return "- Not run."
    status = "passed" if result.passed else "failed"
    output = result.output.strip() or "(no output)"
    if len(output) > 1200:
        output = output[:1200].rstrip() + "\n... truncated ..."
    return f"- `{result.command}` {status} with exit code {result.returncode}.\n\n```text\n{output}\n```"


def build_iteration_prompt(
    *,
    repo_path: Path,
    graph_type: str,
    snapshot: Path,
    summary: dict[str, object],
    previous_snapshot: Path | None,
    previous_summary: dict[str, object] | None,
    test_result: TestResult | None,
) -> str:
    diff = diff_summaries(summary, previous_summary)
    warnings: list[str] = []
    if int(summary.get("dangling_edge_count", 0)) > 0:
        warnings.append(f"Fix {summary['dangling_edge_count']} dangling edges or explain why placeholders are expected.")
    if int(summary.get("isolated_node_count", 0)) > 0:
        warnings.append(f"Review {summary['isolated_node_count']} isolated nodes for missing relationship extraction.")
    if test_result is not None and not test_result.passed:
        warnings.append("Fix the failing test command before broadening graph extraction.")
    if not warnings:
        warnings.append("No blocking graph health issue detected in this snapshot.")

    if diff.get("has_previous"):
        delta_text = (
            f"- Nodes: {summary['node_count']} ({diff['node_delta']:+})\n"
            f"- Edges: {summary['edge_count']} ({diff['edge_delta']:+})\n"
            f"- Dangling edges: {summary['dangling_edge_count']} ({diff['dangling_edge_delta']:+})\n"
            f"- Isolated nodes: {summary['isolated_node_count']} ({diff['isolated_node_delta']:+})"
        )
    else:
        delta_text = (
            f"- Nodes: {summary['node_count']}\n"
            f"- Edges: {summary['edge_count']}\n"
            f"- Dangling edges: {summary['dangling_edge_count']}\n"
            f"- Isolated nodes: {summary['isolated_node_count']}"
        )

    previous_line = f"`{previous_snapshot}`" if previous_snapshot else "none"
    next_steps = [
        "Improve call graph resolution by connecting placeholder call targets to concrete function nodes where imports or same-file definitions make that safe.",
        "Add graph summary outputs for entrypoints, high-fan-in nodes, high-fan-out nodes, and isolated modules.",
        "Add focused regression fixtures before each parser expansion so graph shape stays stable.",
        "Keep generated snapshots out of git; commit only source, tests, docs, and progress/prompt context.",
    ]

    return (
        "# code2graph Next Iteration Prompt\n\n"
        "You are working only in the `code2graph` repository. Continue improving the code-to-graph generator for "
        "OhWise-compatible context engineering graphs.\n\n"
        "## Current Snapshot\n\n"
        f"- Target repo: `{repo_path}`\n"
        f"- Graph type: `{graph_type}`\n"
        f"- Current snapshot: `{snapshot}`\n"
        f"- Previous snapshot: {previous_line}\n\n"
        "## Graph Delta\n\n"
        f"{delta_text}\n\n"
        "## Node Kinds\n\n"
        f"{_top_counts(summary.get('node_kinds'))}\n\n"
        "## Edge Labels\n\n"
        f"{_top_counts(summary.get('edge_labels'))}\n\n"
        "## Entrypoints\n\n"
        f"{_top_nodes(summary.get('entrypoints'))}\n\n"
        "## High Fan In\n\n"
        f"{_top_nodes(summary.get('high_fan_in'))}\n\n"
        "## High Fan Out\n\n"
        f"{_top_nodes(summary.get('high_fan_out'))}\n\n"
        "## Isolated Modules\n\n"
        f"{_top_nodes(summary.get('isolated_modules'))}\n\n"
        "## Issues And Bugs To Check\n\n"
        + "\n".join(f"- {warning}" for warning in warnings)
        + "\n\n"
        "## Tests\n\n"
        f"{_format_test_result(test_result)}\n\n"
        "## Recommended Next Steps\n\n"
        + "\n".join(f"{index}. {step}" for index, step in enumerate(next_steps, start=1))
        + "\n\n"
        "## Commit Discipline\n\n"
        "- Use `jw-open <176761431+jw-open@users.noreply.github.com>`.\n"
        "- Push to `jwpublic:jw-open/code2graph.git` `main`.\n"
        "- Commit only source, tests, docs, and packaging changes that improve the tool.\n"
        "- Do not commit generated snapshots or timestamp-only progress updates.\n"
    )


def write_iteration_prompt(path: Path, prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")


def build_action_prompt(context_prompt: str, repo_root: Path) -> str:
    """
    Wrap the analysis context in explicit implementation instructions for Codex.

    The context prompt describes current graph state + recommended next steps.
    This wrapper tells Codex to pick ONE step and actually implement it.
    """
    roadmap_file = repo_root / "ROADMAP.md"
    roadmap_section = ""
    if roadmap_file.exists():
        roadmap_section = f"\n## Project Roadmap\n\n{roadmap_file.read_text(encoding='utf-8').strip()}\n"

    return (
        "You are an autonomous developer working on `code2graph`, "
        "a pure-Python package that extracts knowledge graphs from code repositories.\n\n"
        "Your working directory is the `code2graph` repo root.\n"
        + roadmap_section
        + "\n## Current Analysis Context\n\n"
        + context_prompt.strip()
        + """

## Your Task

1. Read the **Recommended Next Steps** section above carefully.
2. Pick the ONE step that adds the most concrete value right now — prefer steps that improve
   graph correctness or add missing coverage over documentation or cleanup.
3. Implement it: write real, working Python code in `code2graph/`.
4. Run `python -m pytest -q` and fix any failures before committing.
5. Commit ONLY source/test/doc/package files — never commit `.code2graph-runs/*.json`,
   `CODE2GRAPH_PROGRESS.md`, or `CODE2GRAPH_NEXT_PROMPT.md`.
6. Use this git identity for commits:
   - name: `jw-open`
   - email: `176761431+jw-open@users.noreply.github.com`
7. Push to `origin main`.

## Constraints

- Do NOT just re-run graph generation or update `CODE2GRAPH_PROGRESS.md` — that happens automatically.
- Do NOT add new dependencies unless strictly necessary.
- Each iteration must produce at least one real code change in `code2graph/` or `tests/`.
- If all recommended steps are already done, look at `ROADMAP.md` for the next priority.
- Keep changes focused and minimal — one clear improvement per iteration.
"""
    )
