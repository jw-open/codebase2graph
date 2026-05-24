from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s|$)", re.M)
PY_ENTRY_RE = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")


def build_workflow_graph(root: Path) -> Graph:
    graph = Graph()
    graph.add_node("workflow-root", "Workflows", attributes={"kind": "workflow_root", "path": "."})

    package_json = root / "package.json"
    if package_json.exists():
        _add_package_scripts(graph, package_json)

    makefile = next((root / name for name in ("Makefile", "makefile") if (root / name).exists()), None)
    if makefile:
        _add_make_targets(graph, makefile)

    for compose_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        compose = root / compose_name
        if compose.exists():
            _add_compose_services(graph, compose)

    for ci in [root / ".github" / "workflows"]:
        if ci.exists():
            for path in ci.glob("*.y*ml"):
                _add_ci_workflow(graph, root, path)

    for path in iter_files(root):
        if path.suffix == ".py" and PY_ENTRY_RE.search(read_text(path)):
            rel = path.relative_to(root).as_posix()
            workflow_id = f"workflow:python:{rel}"
            graph.add_node(workflow_id, path.name, attributes={"kind": "python_entrypoint", "path": rel})
            graph.add_edge("workflow-root", workflow_id, "has_workflow")

    return graph


def _add_package_scripts(graph: Graph, package_json: Path) -> None:
    try:
        data = json.loads(read_text(package_json))
    except json.JSONDecodeError:
        return
    scripts = data.get("scripts") or {}
    parent = "workflow:package.json:scripts"
    graph.add_node(parent, "package scripts", attributes={"kind": "script_group", "path": "package.json"})
    graph.add_edge("workflow-root", parent, "has_workflow")
    for name, command in scripts.items():
        node_id = f"workflow:npm:{name}"
        graph.add_node(node_id, name, attributes={"kind": "npm_script", "command": str(command), "path": "package.json"})
        graph.add_edge(parent, node_id, "runs")


def _add_make_targets(graph: Graph, makefile: Path) -> None:
    text = read_text(makefile)
    parent = "workflow:makefile"
    graph.add_node(parent, "Makefile", attributes={"kind": "makefile", "path": makefile.name})
    graph.add_edge("workflow-root", parent, "has_workflow")
    for target in MAKE_TARGET_RE.findall(text):
        if target.startswith("."):
            continue
        node_id = f"workflow:make:{target}"
        graph.add_node(node_id, target, attributes={"kind": "make_target", "path": makefile.name})
        graph.add_edge(parent, node_id, "runs")


def _add_compose_services(graph: Graph, compose: Path) -> None:
    text = read_text(compose)
    parent = f"workflow:compose:{compose.name}"
    graph.add_node(parent, compose.name, attributes={"kind": "compose_file", "path": compose.name})
    graph.add_edge("workflow-root", parent, "has_workflow")
    in_services = False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services:
            match = re.match(r"^\s{2}([A-Za-z0-9_.-]+):\s*$", line)
            if match:
                service = match.group(1)
                node_id = f"workflow:compose:{service}"
                graph.add_node(node_id, service, attributes={"kind": "compose_service", "path": compose.name})
                graph.add_edge(parent, node_id, "starts")
            elif line and not line.startswith(" "):
                break


def _add_ci_workflow(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    node_id = f"workflow:ci:{rel}"
    graph.add_node(node_id, path.stem, attributes={"kind": "ci_workflow", "path": rel})
    graph.add_edge("workflow-root", node_id, "has_workflow")

