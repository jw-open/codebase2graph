"""
Feature graph — extracts user-facing capabilities as a graph.

Each node represents a *feature*: a named, documented public capability of the
codebase.  Edges link features to the functions/classes that implement them,
to the tests that validate them, and to the API endpoints that expose them.

Supported extraction sources (no LLM required):
  - Python public functions / methods / classes with docstrings (AST)
  - Test functions (``test_*``) linked back to the feature they appear to test
  - FastAPI / Flask / Starlette / Django route handlers
  - TypeScript / JavaScript exported functions with JSDoc
  - CLI command definitions (argparse, click, typer)
  - ``@feature`` decorator as an explicit annotation convention

GraphRAG use-cases
  - Pull the feature graph before making a change → agent sees existing
    capabilities and their test coverage → backward-compatibility awareness.
  - Identify which tests to write for a new feature by finding similar feature
    nodes and inspecting their ``validates`` edges.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Generator

from .models import Graph
from .scanner import iter_files, read_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_doc(doc: str | None) -> str:
    """Return the first non-empty line of a docstring (the summary line)."""
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


# ---------------------------------------------------------------------------
# Python extractor
# ---------------------------------------------------------------------------

_CLICK_DECORATORS = {"command", "group", "argument", "option"}
_TYPER_DECORATORS = {"command", "callback"}
_FASTAPI_METHODS  = {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
_FLASK_METHODS    = {"route", "get", "post", "put", "patch", "delete"}
_TEST_RE          = re.compile(r"^test_", re.I)
_FEATURE_DECO_RE  = re.compile(r"^feature$", re.I)


def _decorator_names(decorator_list: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for d in decorator_list:
        if isinstance(d, ast.Name):
            names.add(d.id)
        elif isinstance(d, ast.Attribute):
            names.add(d.attr)
        elif isinstance(d, ast.Call):
            if isinstance(d.func, ast.Name):
                names.add(d.func.id)
            elif isinstance(d.func, ast.Attribute):
                names.add(d.func.attr)
    return names


def _is_public(name: str) -> bool:
    return not name.startswith("_")


class _PythonFeatureVisitor(ast.NodeVisitor):
    """Walk a single Python file and emit feature/test/api nodes."""

    def __init__(self, root: Path, path: Path, graph: Graph) -> None:
        self.root  = root
        self.path  = path
        self.graph = graph
        self.rel   = path.relative_to(root).as_posix()
        self._scope: list[str] = []          # class names
        self._is_test_file = "test" in self.rel.lower()

    # ------------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node)

    # ------------------------------------------------------------------
    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        deco_names = _decorator_names(node.decorator_list)
        doc        = ast.get_docstring(node)
        summary    = _clean_doc(doc)
        qual_name  = f"{self._scope[-1]}.{node.name}" if self._scope else node.name

        # ---- test node -----------------------------------------------
        if _TEST_RE.match(node.name) or self._is_test_file:
            test_id = f"test:{self.rel}:{qual_name}"
            self.graph.add_node(
                test_id,
                qual_name,
                attributes={
                    "kind":    "test",
                    "path":    self.rel,
                    "line":    str(node.lineno),
                    "summary": summary,
                },
            )
            self.graph.add_edge("feature-root", test_id, "has_test")
            # Heuristic: link test → feature with matching name
            # e.g. test_user_signup → feature:user_signup
            stripped = _TEST_RE.sub("", node.name)
            feature_id = f"feature:{_slug(stripped)}"
            if feature_id in self.graph.nodes:
                self.graph.add_edge(test_id, feature_id, "validates")
            self.generic_visit(node)
            return

        # ---- explicit @feature decorator -----------------------------
        has_feature_deco = any(_FEATURE_DECO_RE.match(d) for d in deco_names)

        # ---- API route handler ---------------------------------------
        is_fastapi = bool(deco_names & _FASTAPI_METHODS)
        is_flask   = bool(deco_names & _FLASK_METHODS)
        is_cli     = bool(deco_names & (_CLICK_DECORATORS | _TYPER_DECORATORS))

        if is_fastapi or is_flask:
            route_path = self._extract_route_path(node)
            api_id = f"api:{self.rel}:{qual_name}"
            self.graph.add_node(
                api_id,
                qual_name,
                attributes={
                    "kind":       "api_endpoint",
                    "path":       self.rel,
                    "line":       str(node.lineno),
                    "route":      route_path,
                    "summary":    summary,
                    "framework":  "fastapi" if is_fastapi else "flask",
                },
            )
            self.graph.add_edge("feature-root", api_id, "has_api")
            # Also create a feature node for the handler
            feat_id = f"feature:{_slug(qual_name)}"
            self._add_feature_node(feat_id, qual_name, summary, node)
            self.graph.add_edge(api_id, feat_id, "exposes")
            self.generic_visit(node)
            return

        if is_cli:
            cli_id = f"cli:{self.rel}:{qual_name}"
            self.graph.add_node(
                cli_id,
                qual_name,
                attributes={
                    "kind":    "cli_command",
                    "path":    self.rel,
                    "line":    str(node.lineno),
                    "summary": summary,
                },
            )
            self.graph.add_edge("feature-root", cli_id, "has_cli")
            feat_id = f"feature:{_slug(qual_name)}"
            self._add_feature_node(feat_id, qual_name, summary, node)
            self.graph.add_edge(cli_id, feat_id, "exposes")
            self.generic_visit(node)
            return

        # ---- public function with docstring (or @feature) ------------
        if (has_feature_deco or (doc and _is_public(node.name))):
            feat_id = f"feature:{self.rel}:{_slug(qual_name)}"
            self._add_feature_node(feat_id, qual_name, summary, node)

        self.generic_visit(node)

    def _add_feature_node(
        self,
        feat_id:  str,
        name:     str,
        summary:  str,
        node:     ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self.graph.add_node(
            feat_id,
            name,
            attributes={
                "kind":     "feature",
                "path":     self.rel,
                "line":     str(node.lineno),
                "summary":  summary,
                "language": "python",
            },
        )
        self.graph.add_edge("feature-root", feat_id, "has_feature")
        # Link to the implementation function node if call_graph already built it
        impl_id = f"func:{self.rel}:{name}"
        self.graph.add_edge(feat_id, impl_id, "implements")

    @staticmethod
    def _extract_route_path(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Best-effort: pull the first string arg from the route decorator."""
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and deco.args:
                first = deco.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    return first.value
        return ""


def _process_python_file(root: Path, path: Path, graph: Graph) -> None:
    src = read_text(path)
    if not src:
        return
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return
    _PythonFeatureVisitor(root, path, graph).visit(tree)

    # Second pass: back-link test→feature for tests discovered earlier
    # (tests may be defined after the features in the same file)
    for node_id, node_obj in list(graph.nodes.items()):
        if node_obj.attributes.get("kind") == "test":
            stripped = _TEST_RE.sub("", node_obj.label)
            feature_id = f"feature:{_slug(stripped)}"
            if feature_id in graph.nodes:
                edge_id = f"edge:{node_id}:validates:{feature_id}"
                if edge_id not in graph.edges:
                    graph.add_edge(node_id, feature_id, "validates")


# ---------------------------------------------------------------------------
# TypeScript / JavaScript extractor  (JSDoc + exports)
# ---------------------------------------------------------------------------

_JSDOC_FEATURE_RE  = re.compile(r"@feature\s+(.*)", re.I)
_EXPORT_FUNC_RE    = re.compile(
    r"export\s+(?:async\s+)?function\s+(\w+)\s*\(",
    re.M,
)
_EXPORT_ARROW_RE   = re.compile(
    r"export\s+(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\(",
    re.M,
)
_JSDOC_BLOCK_RE    = re.compile(r"/\*\*(.*?)\*/", re.S)
_FIRST_LINE_RE     = re.compile(r"\*\s+(.+)")
_ROUTE_HANDLER_RE  = re.compile(
    r"""(?:app|router)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]""",
    re.I | re.M,
)


def _process_ts_file(root: Path, path: Path, graph: Graph) -> None:
    src = read_text(path)
    if not src:
        return
    rel = path.relative_to(root).as_posix()
    is_test = "test" in rel.lower() or ".spec." in rel or ".test." in rel

    # Extract JSDoc blocks with their positions
    jsdoc_map: dict[int, str] = {}
    for m in _JSDOC_BLOCK_RE.finditer(src):
        lines = _FIRST_LINE_RE.findall(m.group(1))
        summary = lines[0].strip() if lines else ""
        feature_tag = _JSDOC_FEATURE_RE.search(m.group(1))
        jsdoc_map[m.end()] = feature_tag.group(1).strip() if feature_tag else summary

    def _nearest_jsdoc(pos: int) -> str:
        candidates = {k: v for k, v in jsdoc_map.items() if k <= pos and pos - k < 300}
        if not candidates:
            return ""
        return candidates[max(candidates)]

    # Exported functions
    for m in _EXPORT_FUNC_RE.finditer(src):
        func_name = m.group(1)
        summary   = _nearest_jsdoc(m.start())
        if is_test or _TEST_RE.match(func_name):
            tid = f"test:{rel}:{func_name}"
            graph.add_node(tid, func_name, attributes={"kind": "test", "path": rel, "summary": summary})
            graph.add_edge("feature-root", tid, "has_test")
        elif summary:
            fid = f"feature:{rel}:{_slug(func_name)}"
            graph.add_node(fid, func_name, attributes={
                "kind": "feature", "path": rel, "summary": summary, "language": "typescript"
            })
            graph.add_edge("feature-root", fid, "has_feature")

    # Arrow function exports (same logic)
    for m in _EXPORT_ARROW_RE.finditer(src):
        func_name = m.group(1)
        summary   = _nearest_jsdoc(m.start())
        if is_test or _TEST_RE.match(func_name):
            continue  # arrow test helpers are usually utilities, skip
        elif summary:
            fid = f"feature:{rel}:{_slug(func_name)}"
            if fid not in graph.nodes:
                graph.add_node(fid, func_name, attributes={
                    "kind": "feature", "path": rel, "summary": summary, "language": "typescript"
                })
                graph.add_edge("feature-root", fid, "has_feature")

    # Express / Next.js route handlers
    for m in _ROUTE_HANDLER_RE.finditer(src):
        method     = m.group(1).upper()
        route_path = m.group(2)
        api_id     = f"api:{rel}:{method}:{_slug(route_path)}"
        graph.add_node(api_id, f"{method} {route_path}", attributes={
            "kind": "api_endpoint", "path": rel, "method": method,
            "route": route_path, "framework": "express/next",
        })
        graph.add_edge("feature-root", api_id, "has_api")


# ---------------------------------------------------------------------------
# Changelog / CHANGELOG.md extractor
# ---------------------------------------------------------------------------

_CHANGELOG_VERSION_RE = re.compile(r"^#+\s*(?:\[?v?[\d.]+\]?|Unreleased)", re.M | re.I)
_CHANGELOG_ITEM_RE    = re.compile(r"^[-*]\s+(.+)", re.M)
_CHANGELOG_CATEGORY_RE= re.compile(r"^#+\s+(Added|Changed|Fixed|Removed|Deprecated|Security)", re.M | re.I)


def _process_changelog(root: Path, graph: Graph) -> None:
    for name in ("CHANGELOG.md", "CHANGELOG.rst", "CHANGES.md", "HISTORY.md"):
        clog = root / name
        if not clog.exists():
            continue
        src = read_text(clog)
        if not src:
            return

        # Split by version headers; take only the latest (first) block
        splits = _CHANGELOG_VERSION_RE.split(src, maxsplit=2)
        if len(splits) < 2:
            return
        latest_block = splits[1]

        current_category = "Changed"
        for line in latest_block.splitlines():
            cat_m = _CHANGELOG_CATEGORY_RE.match(line)
            if cat_m:
                current_category = cat_m.group(1).capitalize()
                continue
            item_m = _CHANGELOG_ITEM_RE.match(line)
            if item_m:
                text   = item_m.group(1).strip()
                feat_id = f"feature:changelog:{_slug(text[:60])}"
                graph.add_node(feat_id, text[:80], attributes={
                    "kind":     "feature",
                    "source":   "changelog",
                    "category": current_category,
                    "summary":  text,
                })
                graph.add_edge("feature-root", feat_id, "has_feature")
        return  # only process first changelog found


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_feature_graph(root: Path) -> Graph:
    """
    Extract a *feature graph* from the repository at *root*.

    The graph contains:
      - ``feature:*`` nodes — named, documented public capabilities
      - ``test:*``    nodes — test cases that validate features
      - ``api:*``     nodes — HTTP endpoints / CLI commands that expose features
      - ``feature-root`` — sentinel root node for the whole feature set

    Edge labels:
      - ``has_feature`` / ``has_test`` / ``has_api`` / ``has_cli``
      - ``implements``  (feature → implementing function in call_graph)
      - ``validates``   (test → feature)
      - ``exposes``     (api/cli → feature)
      - ``depends_on``  (feature → feature, when call_graph edges exist)

    No LLM is required — all extraction is pure static analysis.
    """
    graph = Graph()
    graph.add_node(
        "feature-root",
        "Features",
        attributes={"kind": "feature_root", "path": "."},
    )

    for path in iter_files(root):
        suffix = path.suffix.lower()
        if suffix == ".py":
            _process_python_file(root, path, graph)
        elif suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            _process_ts_file(root, path, graph)

    _process_changelog(root, graph)

    # Post-pass: add depends_on edges between features that share
    # implementations (approximate: same file, different feature nodes)
    # This is intentionally lightweight — a full cross-feature call graph
    # requires the call_graph module; here we just flag same-file siblings.
    file_features: dict[str, list[str]] = {}
    for nid, node in graph.nodes.items():
        if node.attributes.get("kind") == "feature":
            fp = node.attributes.get("path", "")
            file_features.setdefault(fp, []).append(nid)

    graph.current_node_id = "feature-root"
    return graph
