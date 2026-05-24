from __future__ import annotations

import ast
from pathlib import Path

from .models import Graph
from .scanner import iter_files, rel_id, read_text


class PythonCollector(ast.NodeVisitor):
    def __init__(self, root: Path, path: Path, graph: Graph) -> None:
        self.root = root
        self.path = path
        self.graph = graph
        self.file_id = rel_id("file", root, path)
        self.scope: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_id = self._entity_id("class", node.name)
        self.graph.add_node(
            class_id,
            node.name,
            attributes={
                "kind": "class",
                "language": "python",
                "path": self.path.relative_to(self.root).as_posix(),
                "line": str(node.lineno),
            },
        )
        self.graph.add_edge(self.file_id, class_id, "defines")
        if self.scope:
            self.graph.add_edge(self.scope[-1], class_id, "contains")
        self.scope.append(class_id)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_import(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        self._add_import(module, node.lineno)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self.scope and self.scope[-1].startswith("py:class:") else "function"
        func_id = self._entity_id(kind, node.name)
        self.graph.add_node(
            func_id,
            node.name,
            attributes={
                "kind": kind,
                "language": "python",
                "path": self.path.relative_to(self.root).as_posix(),
                "line": str(node.lineno),
                "async": str(isinstance(node, ast.AsyncFunctionDef)).lower(),
            },
        )
        self.graph.add_edge(self.scope[-1] if self.scope else self.file_id, func_id, "defines")
        self.scope.append(func_id)
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = _call_name(child.func)
                if call_name:
                    target_id = f"py:call:{call_name}"
                    self.graph.add_node(
                        target_id,
                        call_name,
                        attributes={"kind": "call_target", "language": "python"},
                    )
                    self.graph.add_edge(func_id, target_id, "calls")
        self.generic_visit(node)
        self.scope.pop()

    def _add_import(self, name: str, line: int) -> None:
        import_id = f"py:import:{name}"
        self.graph.add_node(
            import_id,
            name,
            attributes={"kind": "import", "language": "python", "line": str(line)},
        )
        self.graph.add_edge(self.file_id, import_id, "imports")

    def _entity_id(self, kind: str, name: str) -> str:
        rel = self.path.relative_to(self.root).as_posix()
        scope = ".".join([self.graph.nodes[s].label for s in self.scope])
        qualified = f"{scope}.{name}" if scope else name
        return f"py:{kind}:{rel}:{qualified}"


def build_python_graph(root: Path) -> Graph:
    graph = Graph()
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        graph.add_node(
            rel_id("file", root, path),
            path.name,
            attributes={"kind": "file", "language": "python", "path": path.relative_to(root).as_posix()},
        )
        text = read_text(path)
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        PythonCollector(root, path, graph).visit(tree)
    return graph


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None

