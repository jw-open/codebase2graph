from __future__ import annotations

import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, rel_id, read_text

SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rs", ".rb", ".php"}

IMPORT_RE = re.compile(r"^\s*(?:import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]|from\s+([\w.]+)\s+import|require\(['\"]([^'\"]+)['\"]\))", re.M)
TS_ENTITY_RE = re.compile(r"^\s*export\s+(?:default\s+)?(?:class|function|interface|type|const)\s+([A-Za-z_$][\w$]*)|^\s*(?:class|function)\s+([A-Za-z_$][\w$]*)", re.M)


def build_entity_graph(root: Path) -> Graph:
    graph = Graph()
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
        if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            _add_javascript_entities(graph, root, path, text, file_id)
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
        import_id = f"import:{language}:{module}"
        graph.add_node(import_id, module, attributes={"kind": "import", "language": language})
        graph.add_edge(file_id, import_id, "imports")


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

