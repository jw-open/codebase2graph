from __future__ import annotations

from pathlib import Path

from .models import Graph
from .scanner import DEFAULT_IGNORES, rel_id


def build_folder_graph(root: Path) -> Graph:
    graph = Graph()
    root = root.resolve()
    repo_id = "repo"
    graph.add_node(repo_id, root.name, attributes={"kind": "repository", "path": "."})

    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part in DEFAULT_IGNORES for part in rel_parts):
            continue

        kind = "folder" if path.is_dir() else "file"
        node_id = rel_id(kind, root, path)
        graph.add_node(
            node_id,
            path.name,
            attributes={
                "kind": kind,
                "path": path.relative_to(root).as_posix(),
                "extension": path.suffix,
            },
        )
        parent = path.parent
        parent_id = repo_id if parent == root else rel_id("folder", root, parent)
        graph.add_edge(parent_id, node_id, "contains")

    return graph

