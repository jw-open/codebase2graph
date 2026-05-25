from __future__ import annotations

import re
from pathlib import Path

from .models import Graph
from .python_graph import build_python_graph
from .scanner import iter_files, rel_id, read_text

JS_FUNC_RE = re.compile(
    r"^\s*(?:(?:export\s+default\s+)|(?:export\s+))?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("
    r"|^\s*export\s+default\s+(?:async\s+)?function\s*\("
    r"|^\s*export\s+default\s+(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function(?:\s+[A-Za-z_$][\w$]*)?\s*\("
    r"|^\s*(?:public|private|protected|static|async|\s)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
    re.M,
)
JS_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")
JS_IMPORT_RE = re.compile(r"^\s*import\s+(?P<clause>.+?)\s+from\s+['\"](?P<module>[^'\"]+)['\"]", re.M)
JS_REQUIRE_RE = re.compile(
    r"^\s*(?:const|let|var)\s+(?P<binding>\{[^}]+\}|[A-Za-z_$][\w$]*)\s*=\s*require\(['\"](?P<module>[^'\"]+)['\"]\)",
    re.M,
)
JS_REEXPORT_RE = re.compile(
    r"^\s*export\s+(?P<clause>\{[^}]+\}|\*)\s+from\s+['\"](?P<module>[^'\"]+)['\"]",
    re.M,
)
JS_ALIAS_DECL_RE = re.compile(
    r"^\s*(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<target>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*(?:[;\n,]|$)",
    re.M,
)
JS_ALIAS_ASSIGN_RE = re.compile(
    r"^\s*(?!const\b|let\b|var\b)(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<target>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*(?:[;\n,]|$)",
    re.M,
)
JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
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
    files: list[tuple[Path, str, str, list[re.Match[str]]]] = []
    function_index: dict[tuple[str, str], set[str]] = {}
    default_function_index: dict[str, set[str]] = {}
    module_index: dict[str, set[Path]] = {}
    for path in iter_files(root):
        if path.suffix not in JS_EXTENSIONS:
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        matches = [
            match
            for match in JS_FUNC_RE.finditer(text)
            if (_javascript_function_name(match) not in JS_KEYWORDS)
        ]
        files.append((path, rel, text, matches))
        for module_key in _javascript_module_keys(root, path):
            module_index.setdefault(module_key, set()).add(path)
        for name, function_id in _local_javascript_function_ids(rel, matches).items():
            for module_key in _javascript_module_keys(root, path):
                function_index.setdefault((module_key, name), set()).add(function_id)
        for name, function_id in _default_javascript_function_ids(rel, matches).items():
            for module_key in _javascript_module_keys(root, path):
                default_function_index.setdefault(module_key, set()).add(function_id)

    _add_reexported_javascript_function_ids(root, files, function_index, default_function_index, module_index)

    for path, rel, text, matches in files:
        file_id = rel_id("file", root, path)
        graph.add_node(file_id, path.name, attributes={"kind": "file", "language": _language(path), "path": rel})
        local_functions = _local_javascript_function_ids(rel, matches)
        imported_functions = _imported_javascript_function_ids(
            root,
            path,
            text,
            function_index,
            default_function_index,
            module_index,
        )
        for index, match in enumerate(matches):
            name = _javascript_function_name(match)
            if not name or name in JS_KEYWORDS:
                continue
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[start:end]
            func_id = f"js:function:{rel}:{name}"
            function_aliases, shadowed_functions = _javascript_function_aliases(
                body,
                {**local_functions, **imported_functions},
            )
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
                if call in shadowed_functions:
                    target_id = function_aliases.get(call)
                else:
                    target_id = (
                        local_functions.get(call)
                        or _local_javascript_member_call_target(call, local_functions)
                        or imported_functions.get(call)
                    )
                if not target_id:
                    target_id = f"js:call:{call}"
                    graph.add_node(target_id, call, attributes={"kind": "call_target", "language": _language(path)})
                graph.add_edge(func_id, target_id, "calls")
    return graph


def _local_javascript_function_ids(rel: str, matches: list[re.Match[str]]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for match in matches:
        name = _javascript_function_name(match)
        if name and name not in JS_KEYWORDS:
            counts[name] = counts.get(name, 0) + 1
    return {name: f"js:function:{rel}:{name}" for name, count in counts.items() if count == 1}


def _javascript_function_name(match: re.Match[str]) -> str | None:
    name = next((group for group in match.groups() if group), None)
    if name:
        return name
    if re.match(r"^\s*export\s+default\b", match.group(0)):
        return "default"
    return None


def _local_javascript_member_call_target(call: str, local_functions: dict[str, str]) -> str | None:
    receiver, _, member = call.partition(".")
    if receiver != "this" or not member or "." in member:
        return None
    return local_functions.get(member)


def _javascript_function_aliases(body: str, known_functions: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    assignments: dict[str, set[str | None]] = {}
    for name in _javascript_parameter_names(body):
        assignments.setdefault(name, set()).add(None)

    for match in JS_ALIAS_DECL_RE.finditer(body):
        _record_javascript_alias(assignments, known_functions, match.group("name"), match.group("target"))
    for match in JS_ALIAS_ASSIGN_RE.finditer(body):
        _record_javascript_alias(assignments, known_functions, match.group("name"), match.group("target"))

    aliases: dict[str, str] = {}
    for name, target_ids in assignments.items():
        if len(target_ids) == 1:
            target_id = next(iter(target_ids))
            if target_id:
                aliases[name] = target_id
    return aliases, set(assignments)


def _record_javascript_alias(
    assignments: dict[str, set[str | None]],
    known_functions: dict[str, str],
    name: str,
    target: str,
) -> None:
    if name in JS_KEYWORDS:
        return
    assignments.setdefault(name, set()).add(_javascript_reference_target(assignments, known_functions, target))


def _javascript_reference_target(
    assignments: dict[str, set[str | None]],
    known_functions: dict[str, str],
    target: str,
) -> str | None:
    if target in assignments:
        target_ids = assignments[target]
        if len(target_ids) == 1:
            return next(iter(target_ids))
        return None
    return known_functions.get(target)


def _javascript_parameter_names(body: str) -> set[str]:
    header = body.split("{", 1)[0]
    params: str | None = None
    arrow_match = re.search(
        r"=\s*(?:async\s*)?(?:\((?P<group>[^)]*)\)|(?P<single>[A-Za-z_$][\w$]*))\s*=>",
        header,
    )
    if arrow_match:
        params = arrow_match.group("group") or arrow_match.group("single")
    else:
        function_match = re.search(r"\bfunction(?:\s+[A-Za-z_$][\w$]*)?\s*\((?P<params>[^)]*)\)", header)
        method_match = re.search(r"\b[A-Za-z_$][\w$]*\s*\((?P<params>[^)]*)\)\s*$", header)
        if function_match:
            params = function_match.group("params")
        elif method_match:
            params = method_match.group("params")

    if not params:
        return set()
    return {
        part.strip().lstrip(".").split("=", 1)[0].strip()
        for part in params.split(",")
        if re.match(r"^\s*\.{0,3}[A-Za-z_$][\w$]*(?:\s*=.*)?$", part)
    }


def _default_javascript_function_ids(rel: str, matches: list[re.Match[str]]) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for match in matches:
        name = _javascript_function_name(match)
        if name and name not in JS_KEYWORDS and _is_default_javascript_export(match):
            defaults[name] = f"js:function:{rel}:{name}"
    return defaults


def _is_default_javascript_export(match: re.Match[str]) -> bool:
    return bool(re.match(r"^\s*export\s+default\b", match.group(0)))


def _imported_javascript_function_ids(
    root: Path,
    path: Path,
    text: str,
    function_index: dict[tuple[str, str], set[str]],
    default_function_index: dict[str, set[str]],
    module_index: dict[str, set[Path]],
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for match in JS_IMPORT_RE.finditer(text):
        module_keys = _resolve_javascript_module_keys(root, path, match.group("module"), module_index)
        if not module_keys:
            continue
        _add_default_javascript_import(imported, default_function_index, module_keys, match.group("clause"))
        _add_named_javascript_imports(imported, function_index, module_keys, match.group("clause"))
        _add_namespace_javascript_imports(imported, function_index, module_keys, match.group("clause"))

    for match in JS_REQUIRE_RE.finditer(text):
        module_keys = _resolve_javascript_module_keys(root, path, match.group("module"), module_index)
        if not module_keys:
            continue
        binding = match.group("binding").strip()
        if binding.startswith("{"):
            _add_named_javascript_imports(imported, function_index, module_keys, binding)
        else:
            _add_module_javascript_imports(imported, function_index, module_keys, binding)
    return imported


def _add_reexported_javascript_function_ids(
    root: Path,
    files: list[tuple[Path, str, str, list[re.Match[str]]]],
    function_index: dict[tuple[str, str], set[str]],
    default_function_index: dict[str, set[str]],
    module_index: dict[str, set[Path]],
) -> None:
    changed = True
    while changed:
        changed = False
        for path, _rel, text, _matches in files:
            current_module_keys = _javascript_module_keys(root, path)
            for match in JS_REEXPORT_RE.finditer(text):
                target_module_keys = _resolve_javascript_module_keys(root, path, match.group("module"), module_index)
                if not target_module_keys:
                    continue
                clause = match.group("clause")
                additions = (
                    _star_reexported_javascript_functions(function_index, target_module_keys)
                    if clause == "*"
                    else _named_reexported_javascript_functions(
                        function_index,
                        default_function_index,
                        target_module_keys,
                        clause,
                    )
                )
                for exported_name, function_id in additions.items():
                    for module_key in current_module_keys:
                        targets = function_index.setdefault((module_key, exported_name), set())
                        if function_id not in targets:
                            targets.add(function_id)
                            changed = True


def _named_reexported_javascript_functions(
    function_index: dict[tuple[str, str], set[str]],
    default_function_index: dict[str, set[str]],
    module_keys: list[str],
    clause: str,
) -> dict[str, str]:
    exported: dict[str, str] = {}
    named_match = re.search(r"\{(?P<named>[^}]+)\}", clause)
    if not named_match:
        return exported
    for item in named_match.group("named").split(","):
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"\s+as\s+", item, maxsplit=1)
        imported_name = parts[0].strip()
        exported_name = parts[1].strip() if len(parts) == 2 else imported_name
        target_id = (
            _unique_default_javascript_function(default_function_index, module_keys)
            if imported_name == "default"
            else _unique_javascript_function(function_index, module_keys, imported_name)
        )
        if target_id:
            exported[exported_name] = target_id
    return exported


def _star_reexported_javascript_functions(
    function_index: dict[tuple[str, str], set[str]],
    module_keys: list[str],
) -> dict[str, str]:
    exported: dict[str, str] = {}
    function_names = {function_name for module_key, function_name in function_index if module_key in module_keys}
    for function_name in function_names:
        target_id = _unique_javascript_function(function_index, module_keys, function_name)
        if target_id:
            exported[function_name] = target_id
    return exported


def _add_default_javascript_import(
    imported: dict[str, str],
    default_function_index: dict[str, set[str]],
    module_keys: list[str],
    clause: str,
) -> None:
    default_match = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
    if not default_match:
        return
    target_id = _unique_default_javascript_function(default_function_index, module_keys)
    if target_id:
        imported[default_match.group(1)] = target_id


def _add_named_javascript_imports(
    imported: dict[str, str],
    function_index: dict[tuple[str, str], set[str]],
    module_keys: list[str],
    clause: str,
) -> None:
    named_match = re.search(r"\{(?P<named>[^}]+)\}", clause)
    if not named_match:
        return
    for item in named_match.group("named").split(","):
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"\s+as\s+|\s*:\s*", item, maxsplit=1)
        exported_name = parts[0].strip()
        local_name = parts[1].strip() if len(parts) == 2 else exported_name
        target_id = _unique_javascript_function(function_index, module_keys, exported_name)
        if target_id:
            imported[local_name] = target_id


def _add_namespace_javascript_imports(
    imported: dict[str, str],
    function_index: dict[tuple[str, str], set[str]],
    module_keys: list[str],
    clause: str,
) -> None:
    namespace_match = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if namespace_match:
        _add_module_javascript_imports(imported, function_index, module_keys, namespace_match.group(1))


def _add_module_javascript_imports(
    imported: dict[str, str],
    function_index: dict[tuple[str, str], set[str]],
    module_keys: list[str],
    local_name: str,
) -> None:
    imported_names = {function_name for module_key, function_name in function_index if module_key in module_keys}
    for function_name in imported_names:
        function_id = _unique_javascript_function(function_index, module_keys, function_name)
        if function_id:
            imported[f"{local_name}.{function_name}"] = function_id


def _unique_javascript_function(
    function_index: dict[tuple[str, str], set[str]],
    module_keys: list[str],
    name: str,
) -> str | None:
    matches: set[str] = set()
    for module_key in module_keys:
        matches.update(function_index.get((module_key, name), set()))
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _unique_default_javascript_function(
    default_function_index: dict[str, set[str]],
    module_keys: list[str],
) -> str | None:
    matches: set[str] = set()
    for module_key in module_keys:
        matches.update(default_function_index.get(module_key, set()))
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _resolve_javascript_module_keys(
    root: Path,
    path: Path,
    module: str,
    module_index: dict[str, set[Path]],
) -> list[str]:
    if not module.startswith("."):
        return []
    base = (path.parent / module).resolve()
    try:
        rel = base.relative_to(root).as_posix()
    except ValueError:
        return []
    rel_no_suffix = str(Path(rel).with_suffix("")) if Path(rel).suffix in JS_EXTENSIONS else rel
    candidates = [rel_no_suffix, f"{rel_no_suffix}/index"]
    resolved: list[str] = []
    seen_paths: set[Path] = set()
    for candidate in candidates:
        for target_path in module_index.get(candidate, set()):
            if target_path in seen_paths:
                continue
            resolved.extend(_javascript_module_keys(root, target_path))
            seen_paths.add(target_path)
    return resolved


def _javascript_module_keys(root: Path, path: Path) -> list[str]:
    rel = path.relative_to(root).with_suffix("")
    keys = [rel.as_posix()]
    if rel.name == "index":
        parent = rel.parent.as_posix()
        if parent != ".":
            keys.append(parent)
    return keys


def _language(path: Path) -> str:
    return "typescript" if path.suffix in {".ts", ".tsx"} else "javascript"
