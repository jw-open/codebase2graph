from __future__ import annotations

import ast
from pathlib import Path

from .models import Graph
from .scanner import iter_files, rel_id, read_text


class PythonCollector(ast.NodeVisitor):
    def __init__(
        self,
        root: Path,
        path: Path,
        graph: Graph,
        local_functions: dict[str, str],
        imported_functions: dict[str, str],
    ) -> None:
        self.root = root
        self.path = path
        self.graph = graph
        self.file_id = rel_id("file", root, path)
        self.local_functions = local_functions
        self.imported_functions = imported_functions
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
                    target_id = self.local_functions.get(call_name) or self.imported_functions.get(call_name)
                    if not target_id:
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
    parsed_modules: list[tuple[Path, ast.Module]] = []
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
        parsed_modules.append((path, tree))

    function_index = _project_function_index(root, parsed_modules)
    for path, tree in parsed_modules:
        PythonCollector(
            root,
            path,
            graph,
            _local_function_ids(root, path, tree),
            _imported_function_ids(root, path, tree, function_index),
        ).visit(tree)
    return graph


def _project_function_index(root: Path, modules: list[tuple[Path, ast.Module]]) -> dict[tuple[str, str], str]:
    function_index: dict[tuple[str, str], str] = {}
    for path, tree in modules:
        module = _module_name(root, path)
        for name, function_id in _local_function_ids(root, path, tree).items():
            function_index[(module, name)] = function_id
    return function_index


def _imported_function_ids(
    root: Path,
    path: Path,
    tree: ast.Module,
    function_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    imported: dict[str, str] = {}
    current_module = _module_name(root, path)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                local_name = alias.asname or module
                _add_module_function_aliases(imported, function_index, local_name, module)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(current_module, node.level, node.module)
            if not module:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                function_id = function_index.get((module, alias.name))
                if function_id:
                    imported[local_name] = function_id
                _add_module_function_aliases(imported, function_index, local_name, f"{module}.{alias.name}")
    return imported


def _add_module_function_aliases(
    imported: dict[str, str],
    function_index: dict[tuple[str, str], str],
    local_name: str,
    module: str,
) -> None:
    prefix = f"{module}."
    for (indexed_module, function_name), function_id in function_index.items():
        if indexed_module == module or indexed_module.startswith(prefix):
            imported[f"{local_name}.{function_name}"] = function_id


def _local_function_ids(root: Path, path: Path, tree: ast.Module) -> dict[str, str]:
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] = counts.get(node.name, 0) + 1
    rel = path.relative_to(root).as_posix()
    return {name: f"py:function:{rel}:{name}" for name, count in counts.items() if count == 1}


def _module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def _resolve_import_from_module(current_module: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    package_parts = current_module.split(".")[:-1]
    if level > 1:
        package_parts = package_parts[: -(level - 1)]
    module_parts = [part for part in (module or "").split(".") if part]
    return ".".join([*package_parts, *module_parts])


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None
