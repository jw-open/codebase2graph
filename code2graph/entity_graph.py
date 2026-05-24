from __future__ import annotations

import ast
import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, rel_id, read_text

SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rs", ".rb", ".php"}
JAVASCRIPT_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}

IMPORT_RE = re.compile(
    r"^\s*(?:"
    r"import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]"
    r"|from\s+([\w.]+)\s+import"
    r"|(?:const|let|var)\s+.+?=\s*require\(['\"]([^'\"]+)['\"]\)"
    r"|require\(['\"]([^'\"]+)['\"]\)"
    r")",
    re.M,
)
TS_ENTITY_RE = re.compile(r"^\s*export\s+(?:default\s+)?(?:class|function|interface|type|const)\s+([A-Za-z_$][\w$]*)|^\s*(?:class|function)\s+([A-Za-z_$][\w$]*)", re.M)


def build_entity_graph(root: Path) -> Graph:
    graph = Graph()
    python_modules = _python_module_index(root)
    javascript_modules = _javascript_module_index(root)
    for path in iter_files(root):
        if path.suffix not in SOURCE_EXTENSIONS:
            continue
        file_id = rel_id("file", root, path)
        language = _language(path)
        graph.add_node(
            file_id,
            path.name,
            attributes={"kind": "file", "language": language, "path": path.relative_to(root).as_posix()},
        )
        text = read_text(path)
        if path.suffix in JAVASCRIPT_EXTENSIONS:
            _add_javascript_entities(graph, root, path, text, file_id)
            _add_javascript_imports(graph, root, path, text, file_id, javascript_modules)
        if path.suffix == ".py":
            _add_python_imports(graph, root, path, text, file_id, python_modules)
        elif path.suffix not in JAVASCRIPT_EXTENSIONS:
            _add_imports(graph, text, file_id, language)
    return graph


def _add_javascript_entities(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    for match in TS_ENTITY_RE.finditer(text):
        name = match.group(1) or match.group(2)
        if not name:
            continue
        entity_id = f"js:entity:{rel}:{name}"
        graph.add_node(
            entity_id,
            name,
            attributes={"kind": "entity", "language": _language(path), "path": rel},
        )
        graph.add_edge(file_id, entity_id, "defines")


def _add_imports(graph: Graph, text: str, file_id: str, language: str) -> None:
    for match in IMPORT_RE.finditer(text):
        module = next((group for group in match.groups() if group), None)
        if not module:
            continue
        _add_import(graph, file_id, language, module)


def _add_python_imports(
    graph: Graph,
    root: Path,
    path: Path,
    text: str,
    file_id: str,
    python_modules: dict[str, str],
) -> None:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        _add_imports(graph, text, file_id, "python")
        return

    current_module = _python_module_name(root, path)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_import(graph, file_id, "python", alias.name)
                target_id = python_modules.get(alias.name)
                if target_id:
                    graph.add_edge(file_id, target_id, "imports")
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_python_import_from(current_module, node.level, node.module)
            if not module:
                continue
            _add_import(graph, file_id, "python", module)
            target_id = python_modules.get(module)
            if target_id:
                graph.add_edge(file_id, target_id, "imports")
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported_module = f"{module}.{alias.name}"
                imported_target_id = python_modules.get(imported_module)
                if imported_target_id:
                    graph.add_edge(file_id, imported_target_id, "imports")


def _add_import(graph: Graph, file_id: str, language: str, module: str) -> None:
    import_id = f"import:{language}:{module}"
    graph.add_node(import_id, module, attributes={"kind": "import", "language": language})
    graph.add_edge(file_id, import_id, "imports")


def _add_javascript_imports(
    graph: Graph,
    root: Path,
    path: Path,
    text: str,
    file_id: str,
    javascript_modules: dict[str, set[str]],
) -> None:
    language = _language(path)
    for match in IMPORT_RE.finditer(text):
        module = next((group for group in match.groups() if group), None)
        if not module:
            continue
        _add_import(graph, file_id, language, module)
        for target_id in _resolve_javascript_import(root, path, module, javascript_modules):
            graph.add_edge(file_id, target_id, "imports")


def _javascript_module_index(root: Path) -> dict[str, set[str]]:
    modules: dict[str, set[str]] = {}
    for path in iter_files(root):
        if path.suffix not in JAVASCRIPT_EXTENSIONS:
            continue
        for module in _javascript_module_names(root, path):
            modules.setdefault(module, set()).add(rel_id("file", root, path))
    return modules


def _javascript_module_names(root: Path, path: Path) -> list[str]:
    rel = path.relative_to(root).with_suffix("")
    names = [rel.as_posix()]
    if rel.name == "index":
        parent = rel.parent.as_posix()
        if parent != ".":
            names.append(parent)
    return names


def _resolve_javascript_import(
    root: Path,
    path: Path,
    module: str,
    javascript_modules: dict[str, set[str]],
) -> list[str]:
    if not module.startswith("."):
        return []
    base = (path.parent / module).resolve()
    try:
        rel = base.relative_to(root).as_posix()
    except ValueError:
        return []
    rel_path = Path(rel)
    module_name = rel_path.with_suffix("").as_posix() if rel_path.suffix in JAVASCRIPT_EXTENSIONS else rel
    candidates = [module_name, f"{module_name}/index"]
    target_ids: set[str] = set()
    for candidate in candidates:
        target_ids.update(javascript_modules.get(candidate, set()))
    return sorted(target_ids)


def _python_module_index(root: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        module = _python_module_name(root, path)
        if module:
            modules[module] = rel_id("file", root, path)
    return modules


def _python_module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def _resolve_python_import_from(current_module: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    package_parts = current_module.split(".")[:-1]
    if level > 1:
        package_parts = package_parts[: -(level - 1)]
    module_parts = [part for part in (module or "").split(".") if part]
    return ".".join([*package_parts, *module_parts])


def _language(path: Path) -> str:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".java": "java",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
    }.get(path.suffix, "unknown")
