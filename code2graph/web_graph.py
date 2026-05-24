from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

WEB_SOURCE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".html", ".css"}
JSX_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}

REACT_COMPONENT_RE = re.compile(
    r"^\s*(?:export\s+default\s+|export\s+)?(?:function\s+|const\s+)([A-Z][A-Za-z0-9_]*)\b",
    re.M,
)
REACT_HOOK_RE = re.compile(r"\b(use[A-Z][A-Za-z0-9_]*)\s*\(")
JSX_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9._:-]*)\b")
JSX_ROUTE_RE = re.compile(
    r"(?:<Route\b[^>]*\bpath\s*=\s*|path\s*:\s*)[\"'{`]([^\"'`}]+)[\"'`}]",
    re.M,
)
NEXT_DYNAMIC_ROUTE_RE = re.compile(r"\[([^\]]+)\]")
HTML_ASSET_RE = re.compile(r"\b(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", re.I)
CSS_SELECTOR_RE = re.compile(r"(^|})\s*([^@{}][^{}]{0,240})\s*\{", re.M)
TAILWIND_CLASS_RE = re.compile(r"\bclass(?:Name)?\s*=\s*(?:[\"']([^\"']+)[\"']|\{[\"'`]([^\"'`]+)[\"'`]\})", re.M)

FRAMEWORK_DEPENDENCIES = {
    "@angular/core": "angular",
    "@nestjs/core": "nestjs",
    "@sveltejs/kit": "sveltekit",
    "@vitejs/plugin-react": "vite_react",
    "astro": "astro",
    "express": "express",
    "fastify": "fastify",
    "gatsby": "gatsby",
    "next": "nextjs",
    "nuxt": "nuxt",
    "react": "react",
    "react-dom": "react",
    "react-router": "react_router",
    "react-router-dom": "react_router",
    "remix": "remix",
    "svelte": "svelte",
    "tailwindcss": "tailwindcss",
    "vite": "vite",
    "vue": "vue",
}


def build_web_graph(root: Path) -> Graph:
    graph = Graph()
    graph.add_node("web-root", "Web Application", attributes={"kind": "web_root", "path": "."})
    _add_package_web_metadata(graph, root)
    for path in iter_files(root):
        if path.suffix not in WEB_SOURCE_EXTENSIONS:
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        file_id = f"web:file:{rel}"
        graph.add_node(
            file_id,
            path.name,
            attributes={"kind": "web_file", "language": _language(path), "path": rel},
        )
        graph.add_edge("web-root", file_id, "contains")
        if path.suffix in JSX_EXTENSIONS:
            _add_react_nodes(graph, root, path, text, file_id)
            _add_tailwind_nodes(graph, text, file_id)
        elif path.suffix == ".html":
            _add_html_nodes(graph, root, path, text, file_id)
            _add_tailwind_nodes(graph, text, file_id)
        elif path.suffix == ".css":
            _add_css_nodes(graph, root, path, text, file_id)
    graph.current_node_id = "web-root"
    return graph


def _add_package_web_metadata(graph: Graph, root: Path) -> None:
    package_json = root / "package.json"
    if not package_json.exists():
        return
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    deps = {
        **data.get("dependencies", {}),
        **data.get("devDependencies", {}),
        **data.get("peerDependencies", {}),
    }
    for package, version in sorted(deps.items()):
        framework = FRAMEWORK_DEPENDENCIES.get(package)
        if not framework:
            continue
        node_id = f"web:framework:{framework}"
        graph.add_node(
            node_id,
            framework,
            attributes={"kind": "web_framework", "package": package, "version": str(version), "path": "package.json"},
        )
        graph.add_edge("web-root", node_id, "uses_framework")


def _add_react_nodes(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    components = set()
    for match in REACT_COMPONENT_RE.finditer(text):
        name = match.group(1)
        if f"<{name}" not in text and "return" not in text[match.start() : match.start() + 400]:
            continue
        components.add(name)
        component_id = f"web:component:{rel}:{name}"
        graph.add_node(
            component_id,
            name,
            attributes={"kind": "react_component", "language": _language(path), "path": rel},
        )
        graph.add_edge(file_id, component_id, "defines")

    for hook in sorted(set(REACT_HOOK_RE.findall(text))):
        hook_id = f"web:hook:{rel}:{hook}"
        graph.add_node(hook_id, hook, attributes={"kind": "react_hook", "language": _language(path), "path": rel})
        graph.add_edge(file_id, hook_id, "uses_hook")
        for component in components:
            if hook in _component_body(text, component):
                graph.add_edge(f"web:component:{rel}:{component}", hook_id, "uses_hook")

    for tag in sorted(set(JSX_TAG_RE.findall(text))):
        if tag[0].islower():
            tag_id = f"web:dom:{tag}"
            graph.add_node(tag_id, tag, attributes={"kind": "dom_element", "tag": tag})
            graph.add_edge(file_id, tag_id, "renders")
        else:
            target_id = f"web:component_ref:{tag}"
            graph.add_node(target_id, tag, attributes={"kind": "component_reference", "language": _language(path)})
            graph.add_edge(file_id, target_id, "renders_component")

    for route in sorted(_routes_for_file(root, path, text)):
        route_id = f"web:route:{route}"
        graph.add_node(route_id, route, attributes={"kind": "web_route", "path_pattern": route})
        graph.add_edge(file_id, route_id, "handles_route")


def _add_html_nodes(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    for tag in sorted(set(JSX_TAG_RE.findall(text))):
        tag_id = f"web:dom:{tag.lower()}"
        graph.add_node(tag_id, tag.lower(), attributes={"kind": "dom_element", "tag": tag.lower()})
        graph.add_edge(file_id, tag_id, "contains_element")
    for asset in sorted(set(HTML_ASSET_RE.findall(text))):
        if asset.startswith("#") or asset.startswith("mailto:"):
            continue
        asset_id = f"web:asset:{rel}:{asset}"
        graph.add_node(asset_id, asset, attributes={"kind": "web_asset", "path": rel, "asset": asset})
        graph.add_edge(file_id, asset_id, "references_asset")


def _add_css_nodes(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    for _, selector_group in CSS_SELECTOR_RE.findall(text):
        for selector in selector_group.split(","):
            selector = selector.strip()
            if not selector or selector.startswith(("from ", "to ", "%")):
                continue
            selector_id = f"web:css_selector:{rel}:{selector}"
            graph.add_node(selector_id, selector, attributes={"kind": "css_selector", "path": rel, "selector": selector})
            graph.add_edge(file_id, selector_id, "defines_selector")


def _add_tailwind_nodes(graph: Graph, text: str, file_id: str) -> None:
    classes: set[str] = set()
    for match in TAILWIND_CLASS_RE.finditer(text):
        value = match.group(1) or match.group(2) or ""
        classes.update(part for part in value.split() if _looks_like_tailwind(part))
    for class_name in sorted(classes):
        node_id = f"web:tailwind:{class_name}"
        graph.add_node(node_id, class_name, attributes={"kind": "tailwind_utility", "class": class_name})
        graph.add_edge(file_id, node_id, "uses_style")


def _routes_for_file(root: Path, path: Path, text: str) -> set[str]:
    routes = set(JSX_ROUTE_RE.findall(text))
    rel = path.relative_to(root).as_posix()
    route = _next_route_from_path(rel)
    if route:
        routes.add(route)
    return routes


def _next_route_from_path(rel: str) -> str | None:
    parts = Path(rel).parts
    if "pages" in parts:
        start = parts.index("pages") + 1
    elif "app" in parts:
        start = parts.index("app") + 1
    else:
        return None
    route_parts = list(parts[start:])
    if not route_parts:
        return None
    leaf = Path(route_parts[-1]).with_suffix("").name
    if leaf in {"page", "index", "route"}:
        route_parts = route_parts[:-1]
    else:
        route_parts[-1] = leaf
    cleaned = [part for part in route_parts if not part.startswith("(")]
    route = "/" + "/".join(cleaned)
    route = NEXT_DYNAMIC_ROUTE_RE.sub(r":\1", route)
    return route.rstrip("/") or "/"


def _component_body(text: str, component: str) -> str:
    match = re.search(rf"\b(?:function|const)\s+{re.escape(component)}\b", text)
    if not match:
        return ""
    return text[match.start() : match.start() + 4000]


def _looks_like_tailwind(class_name: str) -> bool:
    if class_name in {"block", "contents", "flex", "grid", "hidden", "inline", "inline-block", "inline-flex"}:
        return True
    return bool(
        re.match(
            r"^(?:[a-z0-9-]+:)*(?:m|p|text|bg|border|flex|grid|items|justify|gap|"
            r"w|h|min|max|rounded|shadow|font|leading|tracking|space|opacity|"
            r"overflow|relative|absolute|fixed|sticky|z|inset|top|right|bottom|left)-",
            class_name,
        )
    )


def _language(path: Path) -> str:
    return {
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".html": "html",
        ".css": "css",
    }.get(path.suffix, "unknown")
