from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
GRADLE_FILES = {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
SOURCE_EXTENSIONS = {".kt", ".kts", ".java"}
RESOURCE_EXTENSIONS = {".xml"}

PLUGIN_RE = re.compile(r"id\s*\(?\s*[\"']([^\"']+)[\"']")
INCLUDE_RE = re.compile(r"\binclude\s*\(?\s*([^\n)]+)")
DEPENDENCY_RE = re.compile(
    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|androidTestImplementation)\s*\(?\s*[\"']([^\"']+)[\"']"
)
CONFIG_RE = re.compile(r"\b(applicationId|namespace|compileSdk|minSdk|targetSdk|versionCode|versionName)\s*(?:=|\s)\s*[\"']?([^\"'\n,)]+)")
CLASS_RE = re.compile(
    r"\b(?:class|object)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*(?::\s*([A-Za-z0-9_.,<>\s()]+))?"
)
ROUTE_RE = re.compile(r"\bstartActivity\s*\(|\bIntent\s*\(")

ANDROID_COMPONENT_BASES = {
    "Activity": "activity",
    "AppCompatActivity": "activity",
    "ComponentActivity": "activity",
    "FragmentActivity": "activity",
    "Service": "service",
    "IntentService": "service",
    "BroadcastReceiver": "receiver",
    "ContentProvider": "provider",
    "Fragment": "fragment",
    "DialogFragment": "fragment",
    "ViewModel": "view_model",
}


def build_android_graph(root: Path) -> Graph:
    graph = Graph()
    graph.add_node("android-root", "Android", attributes={"kind": "android_root", "path": "."})
    _add_gradle_metadata(graph, root)
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        if path.name == "AndroidManifest.xml":
            _add_manifest(graph, root, path)
        elif path.suffix in SOURCE_EXTENSIONS:
            _add_source_file(graph, root, path)
        elif path.suffix in RESOURCE_EXTENSIONS and "/res/" in f"/{rel}":
            _add_resource(graph, root, path)
    graph.current_node_id = "android-root"
    return graph


def _add_gradle_metadata(graph: Graph, root: Path) -> None:
    for path in iter_files(root):
        if path.name not in GRADLE_FILES:
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        file_id = f"android:gradle:{rel}"
        graph.add_node(file_id, path.name, attributes={"kind": "android_gradle_file", "path": rel})
        graph.add_edge("android-root", file_id, "configures")

        module = _module_for_gradle(root, path)
        if module:
            module_id = f"android:module:{module}"
            graph.add_node(module_id, module, attributes={"kind": "android_module", "path": str(path.parent.relative_to(root))})
            graph.add_edge("android-root", module_id, "has_module")
            graph.add_edge(module_id, file_id, "configured_by")

        for plugin in sorted(set(PLUGIN_RE.findall(text))):
            plugin_id = f"android:plugin:{plugin}"
            graph.add_node(plugin_id, plugin, attributes={"kind": "android_gradle_plugin", "plugin": plugin, "path": rel})
            graph.add_edge(file_id, plugin_id, "uses_plugin")

        for key, value in CONFIG_RE.findall(text):
            config_id = f"android:config:{rel}:{key}"
            graph.add_node(config_id, key, attributes={"kind": "android_config", "key": key, "value": value.strip(), "path": rel})
            graph.add_edge(file_id, config_id, "sets")

        for dependency in sorted(set(DEPENDENCY_RE.findall(text))):
            dep_id = f"android:dependency:{dependency}"
            attrs = {"kind": "android_dependency", "coordinate": dependency, "path": rel}
            parts = dependency.split(":")
            if len(parts) >= 3:
                attrs.update({"group": parts[0], "artifact": parts[1], "version": parts[2]})
            graph.add_node(dep_id, dependency, attributes=attrs)
            graph.add_edge(file_id, dep_id, "depends_on")

        for include in _parse_settings_includes(text):
            module_id = f"android:module:{include}"
            graph.add_node(module_id, include, attributes={"kind": "android_module"})
            graph.add_edge(file_id, module_id, "includes_module")


def _add_manifest(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    manifest_id = f"android:manifest:{rel}"
    graph.add_node(manifest_id, "AndroidManifest.xml", attributes={"kind": "android_manifest", "path": rel})
    graph.add_edge("android-root", manifest_id, "declares")
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return
    root_el = tree.getroot()
    package = root_el.attrib.get("package")
    if package:
        package_id = f"android:package:{package}"
        graph.add_node(package_id, package, attributes={"kind": "android_package", "package": package, "path": rel})
        graph.add_edge(manifest_id, package_id, "declares_package")

    for permission in root_el.findall("uses-permission"):
        name = _android_attr(permission, "name")
        if not name:
            continue
        permission_id = f"android:permission:{name}"
        graph.add_node(permission_id, name, attributes={"kind": "android_permission", "permission": name, "path": rel})
        graph.add_edge(manifest_id, permission_id, "requests_permission")

    for application in root_el.findall("application"):
        app_id = f"android:application:{rel}"
        label = _android_attr(application, "label") or "application"
        graph.add_node(app_id, label, attributes={"kind": "android_application", "path": rel})
        graph.add_edge(manifest_id, app_id, "declares_application")
        for tag, kind in (("activity", "activity"), ("service", "service"), ("receiver", "receiver"), ("provider", "provider")):
            for element in application.findall(tag):
                _add_manifest_component(graph, app_id, rel, kind, element)


def _add_manifest_component(graph: Graph, app_id: str, rel: str, kind: str, element: ET.Element) -> None:
    name = _android_attr(element, "name") or f"anonymous-{kind}"
    component_id = f"android:{kind}:{name}"
    attrs = {
        "kind": f"android_{kind}",
        "component_type": kind,
        "name": name,
        "path": rel,
    }
    for key in ("exported", "enabled", "permission", "process", "theme"):
        value = _android_attr(element, key)
        if value is not None:
            attrs[key] = value
    graph.add_node(component_id, name, attributes=attrs)
    graph.add_edge(app_id, component_id, "declares_component")
    for intent_filter in element.findall("intent-filter"):
        filter_id = f"android:intent_filter:{name}:{len(graph.nodes)}"
        graph.add_node(filter_id, "intent-filter", attributes={"kind": "android_intent_filter", "path": rel})
        graph.add_edge(component_id, filter_id, "has_intent_filter")
        for child_tag, edge_label in (("action", "handles_action"), ("category", "has_category"), ("data", "matches_data")):
            for child in intent_filter.findall(child_tag):
                value = _android_attr(child, "name") or _android_attr(child, "scheme") or _android_attr(child, "host")
                if not value:
                    continue
                node_id = f"android:{child_tag}:{value}"
                graph.add_node(node_id, value, attributes={"kind": f"android_intent_{child_tag}", "value": value, "path": rel})
                graph.add_edge(filter_id, node_id, edge_label)


def _add_source_file(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    text = read_text(path)
    file_id = f"android:source:{rel}"
    graph.add_node(file_id, path.name, attributes={"kind": "android_source_file", "language": _language(path), "path": rel})
    graph.add_edge("android-root", file_id, "contains")
    for match in CLASS_RE.finditer(text):
        name = match.group(1)
        bases = match.group(2) or ""
        component_kind = _component_kind_for_bases(bases)
        class_id = f"android:class:{rel}:{name}"
        graph.add_node(
            class_id,
            name,
            attributes={
                "kind": "android_class" if not component_kind else f"android_{component_kind}",
                "component_type": component_kind or "",
                "language": _language(path),
                "path": rel,
                "extends": _clean_bases(bases),
            },
        )
        graph.add_edge(file_id, class_id, "defines")
        if ROUTE_RE.search(text[match.start() : match.start() + 4000]):
            graph.add_edge(class_id, "android:api:intent", "uses")
            graph.add_node("android:api:intent", "Intent", attributes={"kind": "android_api", "api": "Intent"})


def _add_resource(graph: Graph, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    resource_type = _resource_type(path)
    resource_id = f"android:resource:{rel}"
    graph.add_node(
        resource_id,
        path.stem,
        attributes={"kind": "android_resource", "resource_type": resource_type, "path": rel},
    )
    graph.add_edge("android-root", resource_id, "has_resource")
    if resource_type == "layout":
        text = read_text(path)
        for tag in sorted(set(re.findall(r"<([A-Za-z][A-Za-z0-9_.]+)\b", text))):
            widget_id = f"android:widget:{tag}"
            graph.add_node(widget_id, tag, attributes={"kind": "android_widget", "widget": tag})
            graph.add_edge(resource_id, widget_id, "uses_widget")


def _android_attr(element: ET.Element, key: str) -> str | None:
    return element.attrib.get(f"{ANDROID_NS}{key}") or element.attrib.get(key)


def _module_for_gradle(root: Path, path: Path) -> str | None:
    if path.parent == root:
        return None
    return ":" + path.parent.relative_to(root).as_posix().replace("/", ":")


def _parse_settings_includes(text: str) -> set[str]:
    modules: set[str] = set()
    for match in INCLUDE_RE.finditer(text):
        for raw in re.findall(r"[\"'](:[^\"']+)[\"']", match.group(1)):
            modules.add(raw)
    return modules


def _component_kind_for_bases(bases: str) -> str | None:
    for base, kind in ANDROID_COMPONENT_BASES.items():
        if re.search(rf"\b{re.escape(base)}\b", bases):
            return kind
    return None


def _clean_bases(bases: str) -> str:
    return " ".join(bases.replace("\n", " ").split())


def _resource_type(path: Path) -> str:
    for part in path.parts:
        if part.startswith("layout"):
            return "layout"
        if part.startswith("values"):
            return "values"
        if part.startswith("drawable"):
            return "drawable"
        if part.startswith("navigation"):
            return "navigation"
        if part.startswith("menu"):
            return "menu"
        if part.startswith("xml"):
            return "xml"
    return "resource"


def _language(path: Path) -> str:
    return {
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
    }.get(path.suffix, path.suffix.lstrip("."))
