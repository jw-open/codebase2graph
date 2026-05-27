from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphUpdateSummary:
    added_nodes: int
    updated_nodes: int
    removed_nodes: int
    added_edges: int
    removed_edges: int

    def to_dict(self) -> dict[str, int]:
        return {
            "added_nodes": self.added_nodes,
            "updated_nodes": self.updated_nodes,
            "removed_nodes": self.removed_nodes,
            "added_edges": self.added_edges,
            "removed_edges": self.removed_edges,
        }


def update_existing_graph(existing: dict[str, Any], fresh: dict[str, Any]) -> tuple[dict[str, Any], GraphUpdateSummary]:
    """Update an existing graph with a freshly generated code2graph snapshot.

    Node and edge ids are deterministic across runs, so the fresh graph is the
    source of truth for current code state. Existing node attributes that are not
    produced by the fresh analyzer are kept, which lets callers retain external
    annotations while still removing stale code nodes and edges.
    """

    existing_nodes = _index_by_id(existing.get("nodes", []))
    existing_edges = _index_by_id(existing.get("edges", []))
    fresh_nodes = _index_by_id(fresh.get("nodes", []))
    fresh_edges = _index_by_id(fresh.get("edges", []))

    updated_nodes: list[dict[str, Any]] = []
    changed_node_count = 0
    for node_id, fresh_node in fresh_nodes.items():
        node = dict(fresh_node)
        old_node = existing_nodes.get(node_id)
        if old_node:
            node["attributes"] = _merge_attributes(old_node.get("attributes"), fresh_node.get("attributes"))
            if node != old_node:
                changed_node_count += 1
        updated_nodes.append(node)

    updated: dict[str, Any] = {
        key: value for key, value in existing.items() if key not in {"nodes", "edges", "current_node_id"}
    }
    updated.update(
        {
            "nodes": updated_nodes,
            "edges": list(fresh_edges.values()),
            "current_node_id": fresh.get("current_node_id"),
        }
    )

    summary = GraphUpdateSummary(
        added_nodes=len(set(fresh_nodes) - set(existing_nodes)),
        updated_nodes=changed_node_count,
        removed_nodes=len(set(existing_nodes) - set(fresh_nodes)),
        added_edges=len(set(fresh_edges) - set(existing_edges)),
        removed_edges=len(set(existing_edges) - set(fresh_edges)),
    )
    return updated, summary


def _index_by_id(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str):
            indexed[item_id] = item
    return indexed


def _merge_attributes(old_attrs: Any, fresh_attrs: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(old_attrs, dict):
        merged.update(old_attrs)
    if isinstance(fresh_attrs, dict):
        merged.update(fresh_attrs)
    return merged
