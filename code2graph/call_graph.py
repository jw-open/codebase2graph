from __future__ import annotations

import re
from pathlib import Path

from .models import Graph
from .python_graph import build_python_graph
from .scanner import iter_files, rel_id, read_text

JS_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
    r"|^\s*(?:public|private|protected|static|async|\s)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
    re.M,
)
JS_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")
JS_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "function",
    "return",
    "typeof",
    "import",
    "require",
}


def build_call_graph(root: Path) -> Graph:
    graph = Graph()
    graph.merge(build_python_graph(root))
    graph.merge(_build_javascript_call_graph(root))
    if graph.nodes:
        graph.add_node("call-root", "Call Graph", attributes={"kind": "call_graph"})
        for node_id, node in list(graph.nodes.items()):
            if node.attributes.get("kind") in {"function", "method"}:
                graph.add_edge("call-root", node_id, "contains")
        graph.current_node_id = "call-root"
    return graph


def _build_javascript_call_graph(root: Path) -> Graph:
    graph = Graph()
    for path in iter_files(root):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        rel = path.relative_to(root).as_posix()
        file_id = rel_id("file", root, path)
        graph.add_node(file_id, path.name, attributes={"kind": "file", "language": _language(path), "path": rel})
        text = read_text(path)
        matches = list(JS_FUNC_RE.finditer(text))
        local_functions = _local_javascript_function_ids(rel, matches)
        for index, match in enumerate(matches):
            name = next((group for group in match.groups() if group), None)
            if not name or name in JS_KEYWORDS:
                continue
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[start:end]
            func_id = f"js:function:{rel}:{name}"
            graph.add_node(
                func_id,
                name,
                attributes={
                    "kind": "function",
                    "language": _language(path),
                    "path": rel,
                    "line": str(text.count("\n", 0, start) + 1),
                },
            )
            graph.add_edge(file_id, func_id, "defines")
            for call in JS_CALL_RE.findall(body):
                base = call.split(".", 1)[0]
                if base in JS_KEYWORDS or call == name:
                    continue
                target_id = local_functions.get(call)
                if not target_id:
                    target_id = f"js:call:{call}"
                    graph.add_node(target_id, call, attributes={"kind": "call_target", "language": _language(path)})
                graph.add_edge(func_id, target_id, "calls")
    return graph


def _local_javascript_function_ids(rel: str, matches: list[re.Match[str]]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for match in matches:
        name = next((group for group in match.groups() if group), None)
        if name and name not in JS_KEYWORDS:
            counts[name] = counts.get(name, 0) + 1
    return {name: f"js:function:{rel}:{name}" for name, count in counts.items() if count == 1}


def _language(path: Path) -> str:
    return "typescript" if path.suffix in {".ts", ".tsx"} else "javascript"
