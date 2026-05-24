from __future__ import annotations

from pathlib import Path

from .call_graph import build_call_graph
from .entity_graph import build_entity_graph
from .folder_graph import build_folder_graph
from .infra_graph import build_infra_graph
from .models import Graph
from .schema_graph import build_schema_graph
from .workflow_graph import build_workflow_graph

GRAPH_BUILDERS = {
    "folder": build_folder_graph,
    "call": build_call_graph,
    "entity": build_entity_graph,
    "schema": build_schema_graph,
    "workflow": build_workflow_graph,
    "infra": build_infra_graph,
}


def build_graph(root: str | Path, graph_type: str = "all") -> Graph:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(root_path)
    if not root_path.is_dir():
        raise NotADirectoryError(root_path)

    if graph_type == "all":
        graph = Graph()
        for builder in GRAPH_BUILDERS.values():
            graph.merge(builder(root_path))
        graph.current_node_id = "repo" if "repo" in graph.nodes else graph.current_node_id
        return graph

    try:
        return GRAPH_BUILDERS[graph_type](root_path)
    except KeyError as exc:
        known = ", ".join(["all", *GRAPH_BUILDERS.keys()])
        raise ValueError(f"Unknown graph type {graph_type!r}. Expected one of: {known}") from exc
