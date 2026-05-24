from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Node:
    id: str
    label: str
    attributes: dict[str, str] = field(default_factory=dict)
    content: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "attributes": self.attributes,
        }
        if self.content is not None:
            data["content"] = self.content
        return data


@dataclass(slots=True)
class Edge:
    id: str
    from_id: str
    to_id: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "from": self.from_id,
            "to": self.to_id,
            "label": self.label,
        }


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)
    current_node_id: str | None = None

    def add_node(
        self,
        node_id: str,
        label: str,
        *,
        attributes: dict[str, str] | None = None,
        content: str | None = None,
    ) -> str:
        if node_id in self.nodes:
            node = self.nodes[node_id]
            if attributes:
                node.attributes.update({k: str(v) for k, v in attributes.items()})
            if content and not node.content:
                node.content = content
            return node_id

        self.nodes[node_id] = Node(
            id=node_id,
            label=label,
            attributes={k: str(v) for k, v in (attributes or {}).items()},
            content=content,
        )
        if self.current_node_id is None:
            self.current_node_id = node_id
        return node_id

    def add_edge(self, from_id: str, to_id: str, label: str) -> str:
        edge_id = f"edge:{from_id}:{label}:{to_id}"
        if edge_id not in self.edges:
            self.edges[edge_id] = Edge(edge_id, from_id, to_id, label)
        return edge_id

    def merge(self, other: Graph) -> None:
        for node in other.nodes.values():
            self.add_node(
                node.id,
                node.label,
                attributes=node.attributes,
                content=node.content,
            )
        for edge in other.edges.values():
            self.add_edge(edge.from_id, edge.to_id, edge.label)
        if self.current_node_id is None:
            self.current_node_id = other.current_node_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges.values()],
            "current_node_id": self.current_node_id,
        }

