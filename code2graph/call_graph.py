from __future__ import annotations

from dataclasses import dataclass
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
JS_LOCAL_EXPORT_RE = re.compile(r"^\s*export\s+(?P<clause>\{[^}]+\})\s*;?\s*$", re.M)
JS_COMMONJS_NAMED_EXPORT_RE = re.compile(
    r"^\s*(?:module\.)?exports\.(?P<exported>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<local>[A-Za-z_$][\w$]*)\s*;?\s*$",
    re.M,
)
JS_COMMONJS_OBJECT_EXPORT_RE = re.compile(r"^\s*module\.exports\s*=\s*\{(?P<body>.*?)\}\s*;?", re.M | re.S)
JS_COMMONJS_DEFAULT_EXPORT_RE = re.compile(
    r"^\s*module\.exports\s*=\s*(?P<local>[A-Za-z_$][\w$]*)\s*;?\s*$",
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
JS_DESTRUCTURING_ALIAS_DECL_RE = re.compile(
    r"^\s*(?:const|let|var)\s+\{(?P<body>[^}]+)\}\s*=\s*"
    r"(?P<target>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*(?:[;\n]|$)",
    re.M,
)
JS_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][\w$]*)[^{]*\{")
JS_NEW_INSTANCE_RE = re.compile(
    r"\b(?:(?:const|let|var)\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*=\s*new\s+"
    r"(?P<class>[A-Za-z_$][\w$]*)\s*\(",
)
JS_CONST_OBJECT_LITERAL_RE = re.compile(
    r"^\s*const\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*\{",
    re.M,
)
JS_ASSIGNMENT_RE = re.compile(r"^\s*(?P<name>[A-Za-z_$][\w$]*)\s*=", re.M)
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
    graph.merge(_build_go_call_graph(root))
    graph.merge(_build_java_call_graph(root))
    graph.merge(_build_rust_call_graph(root))
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
        class_methods = _local_javascript_class_methods(rel, text, local_functions)
        known_classes = {class_name for class_name, _method_name in class_methods}
        object_method_targets = _local_javascript_object_method_targets(text, local_functions)
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
                {**local_functions, **imported_functions, **object_method_targets},
            )
            instance_aliases = _javascript_instance_aliases(body, known_classes)
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
                        or _javascript_instance_method_call_target(call, instance_aliases, class_methods)
                        or _javascript_class_method_call_target(call, known_classes, class_methods)
                        or object_method_targets.get(call)
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


def _local_javascript_class_methods(
    rel: str,
    text: str,
    local_functions: dict[str, str],
) -> dict[tuple[str, str], str]:
    class_counts: dict[str, int] = {}
    discovered: list[tuple[str, str]] = []
    for class_match in JS_CLASS_RE.finditer(text):
        class_name = class_match.group("name")
        class_counts[class_name] = class_counts.get(class_name, 0) + 1
        class_body = _javascript_braced_block(text, class_match.end() - 1)
        if class_body is None:
            continue
        for method_match in JS_FUNC_RE.finditer(class_body):
            method_name = _javascript_function_name(method_match)
            if method_name and method_name not in JS_KEYWORDS and method_name != "constructor":
                discovered.append((class_name, method_name))

    methods: dict[tuple[str, str], str] = {}
    for class_name, method_name in discovered:
        function_id = local_functions.get(method_name)
        if function_id and class_counts.get(class_name) == 1:
            methods[(class_name, method_name)] = function_id
    return methods


def _local_javascript_object_method_targets(text: str, local_functions: dict[str, str]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for match in JS_CONST_OBJECT_LITERAL_RE.finditer(text):
        object_name = match.group("name")
        if _has_later_javascript_assignment(text, object_name, match.end()):
            continue
        object_body = _javascript_braced_block(text, match.end() - 1)
        if object_body is None:
            continue
        for method_match in JS_FUNC_RE.finditer(object_body):
            method_name = _javascript_function_name(method_match)
            function_id = local_functions.get(method_name or "")
            if method_name and function_id:
                targets[f"{object_name}.{method_name}"] = function_id
    return targets


def _has_later_javascript_assignment(text: str, name: str, start: int) -> bool:
    return any(match.group("name") == name for match in JS_ASSIGNMENT_RE.finditer(text, start))


def _javascript_braced_block(text: str, open_brace_index: int) -> str | None:
    depth = 0
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1 : index]
    return None


def _javascript_instance_aliases(body: str, known_classes: set[str]) -> dict[str, str]:
    assignments: dict[str, set[str | None]] = {}
    for match in JS_NEW_INSTANCE_RE.finditer(body):
        name = match.group("name")
        class_name = match.group("class")
        assignments.setdefault(name, set()).add(class_name if class_name in known_classes else None)

    aliases: dict[str, str] = {}
    for name, class_names in assignments.items():
        if len(class_names) == 1:
            class_name = next(iter(class_names))
            if class_name:
                aliases[name] = class_name
    return aliases


def _javascript_instance_method_call_target(
    call: str,
    instance_aliases: dict[str, str],
    class_methods: dict[tuple[str, str], str],
) -> str | None:
    receiver, _, method_name = call.partition(".")
    if not method_name:
        return None
    class_name = instance_aliases.get(receiver)
    if not class_name:
        return None
    return class_methods.get((class_name, method_name))


def _javascript_class_method_call_target(
    call: str,
    known_classes: set[str],
    class_methods: dict[tuple[str, str], str],
) -> str | None:
    class_name, _, method_name = call.partition(".")
    if not method_name or class_name not in known_classes:
        return None
    return class_methods.get((class_name, method_name))


def _javascript_function_aliases(body: str, known_functions: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    assignments: dict[str, set[str | None]] = {}
    for name in _javascript_parameter_names(body):
        assignments.setdefault(name, set()).add(None)

    for match in JS_ALIAS_DECL_RE.finditer(body):
        _record_javascript_alias(assignments, known_functions, match.group("name"), match.group("target"))
    for match in JS_ALIAS_ASSIGN_RE.finditer(body):
        _record_javascript_alias(assignments, known_functions, match.group("name"), match.group("target"))
    for match in JS_DESTRUCTURING_ALIAS_DECL_RE.finditer(body):
        _record_javascript_destructuring_aliases(
            assignments,
            known_functions,
            match.group("body"),
            match.group("target"),
        )

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


def _record_javascript_destructuring_aliases(
    assignments: dict[str, set[str | None]],
    known_functions: dict[str, str],
    destructuring_body: str,
    target: str,
) -> None:
    for item in destructuring_body.split(","):
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"\s*:\s*", item, maxsplit=1)
        property_name = parts[0].strip()
        local_name = parts[1].strip() if len(parts) == 2 else property_name
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", property_name):
            continue
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", local_name) or local_name in JS_KEYWORDS:
            continue
        assignments.setdefault(local_name, set()).add(
            _javascript_reference_target(assignments, known_functions, f"{target}.{property_name}")
        )


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
            _add_bound_default_javascript_import(imported, default_function_index, module_keys, binding)
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
            local_exports = _local_exported_javascript_functions(
                root,
                path,
                text,
                function_index,
                default_function_index,
                module_index,
            )
            for exported_name, function_id in local_exports.items():
                for module_key in current_module_keys:
                    targets = function_index.setdefault((module_key, exported_name), set())
                    if function_id not in targets:
                        targets.add(function_id)
                        changed = True
            commonjs_default_exports = _commonjs_default_exported_javascript_function_ids(root, path, text)
            for function_id in commonjs_default_exports:
                for module_key in current_module_keys:
                    targets = default_function_index.setdefault(module_key, set())
                    if function_id not in targets:
                        targets.add(function_id)
                        changed = True


def _local_exported_javascript_functions(
    root: Path,
    path: Path,
    text: str,
    function_index: dict[tuple[str, str], set[str]],
    default_function_index: dict[str, set[str]],
    module_index: dict[str, set[Path]],
) -> dict[str, str]:
    matches = [
        match
        for match in JS_FUNC_RE.finditer(text)
        if (_javascript_function_name(match) not in JS_KEYWORDS)
    ]
    known_functions = {
        **_local_javascript_function_ids(path.relative_to(root).as_posix(), matches),
        **_imported_javascript_function_ids(root, path, text, function_index, default_function_index, module_index),
    }
    exported: dict[str, str] = {}
    for match in JS_LOCAL_EXPORT_RE.finditer(text):
        for item in match.group("clause").strip("{}").split(","):
            item = item.strip()
            if not item:
                continue
            parts = re.split(r"\s+as\s+", item, maxsplit=1)
            local_name = parts[0].strip()
            exported_name = parts[1].strip() if len(parts) == 2 else local_name
            target_id = known_functions.get(local_name)
            if target_id:
                exported[exported_name] = target_id
    for exported_name, local_name in _commonjs_named_javascript_exports(text).items():
        target_id = known_functions.get(local_name)
        if target_id:
            exported[exported_name] = target_id
    return exported


def _commonjs_named_javascript_exports(text: str) -> dict[str, str]:
    exported: dict[str, str] = {}
    for match in JS_COMMONJS_NAMED_EXPORT_RE.finditer(text):
        exported[match.group("exported")] = match.group("local")
    for match in JS_COMMONJS_OBJECT_EXPORT_RE.finditer(text):
        for item in match.group("body").split(","):
            item = item.strip()
            if not item:
                continue
            parts = re.split(r"\s*:\s*", item, maxsplit=1)
            exported_name = parts[0].strip()
            local_name = parts[1].strip() if len(parts) == 2 else exported_name
            if re.fullmatch(r"[A-Za-z_$][\w$]*", exported_name) and re.fullmatch(r"[A-Za-z_$][\w$]*", local_name):
                exported[exported_name] = local_name
    return exported


def _commonjs_default_exported_javascript_function_ids(root: Path, path: Path, text: str) -> set[str]:
    matches = [
        match
        for match in JS_FUNC_RE.finditer(text)
        if (_javascript_function_name(match) not in JS_KEYWORDS)
    ]
    local_functions = _local_javascript_function_ids(path.relative_to(root).as_posix(), matches)
    exported: set[str] = set()
    for match in JS_COMMONJS_DEFAULT_EXPORT_RE.finditer(text):
        local_name = match.group("local")
        target_id = local_functions.get(local_name)
        if target_id:
            exported.add(target_id)
    return exported


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


def _add_bound_default_javascript_import(
    imported: dict[str, str],
    default_function_index: dict[str, set[str]],
    module_keys: list[str],
    local_name: str,
) -> None:
    target_id = _unique_default_javascript_function(default_function_index, module_keys)
    if target_id:
        imported[local_name] = target_id


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


GO_FUNC_RE = re.compile(
    r"^\s*func\s+(?:\((?P<receiver>[^)]+)\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(",
    re.M,
)
GO_CALL_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*\(")
GO_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:"
    r"(?P<single_alias>\.|_|[A-Za-z_]\w*)?\s*\"(?P<single>[^\"]+)\""
    r"|\((?P<block>.*?)\)"
    r")",
    re.M | re.S,
)
GO_IMPORT_ITEM_RE = re.compile(r"^\s*(?P<alias>\.|_|[A-Za-z_]\w*)?\s*\"(?P<path>[^\"]+)\"", re.M)
GO_VAR_ASSIGN_RE = re.compile(
    r"\b(?P<name>[A-Za-z_]\w*)\s*(?::=|=)\s*&?(?P<type>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*\{",
    re.M,
)
GO_KEYWORDS = {
    "append",
    "cap",
    "close",
    "complex",
    "copy",
    "delete",
    "defer",
    "for",
    "func",
    "go",
    "if",
    "imag",
    "len",
    "make",
    "new",
    "panic",
    "print",
    "println",
    "range",
    "real",
    "recover",
    "return",
    "select",
    "switch",
}


def _build_go_call_graph(root: Path) -> Graph:
    graph = Graph()
    files: list[tuple[Path, str, str, list[re.Match[str]], str]] = []
    package_functions: dict[tuple[str, str], set[str]] = {}
    package_methods: dict[tuple[str, str, str], set[str]] = {}
    package_paths: dict[str, str] = {}
    module_name = _go_module_name(root)

    for path in iter_files(root):
        if path.suffix != ".go":
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        package_key = _go_package_key(root, path)
        package_paths[_go_package_import_path(root, path, module_name)] = package_key
        matches = list(GO_FUNC_RE.finditer(text))
        files.append((path, rel, text, matches, package_key))
        for match in matches:
            name = match.group("name")
            receiver_type = _go_receiver_type(match.group("receiver"))
            if receiver_type:
                package_methods.setdefault((package_key, receiver_type, name), set()).add(
                    f"go:method:{rel}:{receiver_type}.{name}"
                )
            else:
                package_functions.setdefault((package_key, name), set()).add(f"go:function:{rel}:{name}")

    for path, rel, text, matches, package_key in files:
        file_id = rel_id("file", root, path)
        graph.add_node(file_id, path.name, attributes={"kind": "file", "language": "go", "path": rel})
        local_functions = _go_local_functions(package_functions, package_key)
        imported_functions = _go_imported_functions(text, package_functions, package_paths, module_name)
        known_types = _go_known_type_refs(text, package_methods, package_paths, module_name, package_key)
        for index, match in enumerate(matches):
            name = match.group("name")
            receiver_name = _go_receiver_name(match.group("receiver"))
            receiver_type = _go_receiver_type(match.group("receiver"))
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[start:end]
            if receiver_type:
                func_id = f"go:method:{rel}:{receiver_type}.{name}"
                kind = "method"
                label = f"{receiver_type}.{name}"
            else:
                func_id = f"go:function:{rel}:{name}"
                kind = "function"
                label = name
            graph.add_node(
                func_id,
                label,
                attributes={
                    "kind": kind,
                    "language": "go",
                    "path": rel,
                    "line": str(text.count("\n", 0, start) + 1),
                },
            )
            graph.add_edge(file_id, func_id, "defines")
            instance_aliases = _go_instance_aliases(body, known_types)
            for call in GO_CALL_RE.findall(body):
                base = call.split(".", 1)[0]
                if base in GO_KEYWORDS or call == name:
                    continue
                target_id = (
                    local_functions.get(call)
                    or imported_functions.get(call)
                    or _go_method_call_target(
                        call,
                        receiver_name,
                        receiver_type,
                        package_key,
                        package_methods,
                        instance_aliases,
                    )
                )
                if not target_id:
                    target_id = f"go:call:{call}"
                    graph.add_node(target_id, call, attributes={"kind": "call_target", "language": "go"})
                graph.add_edge(func_id, target_id, "calls")
    return graph


def _go_module_name(root: Path) -> str:
    match = re.search(r"^\s*module\s+(\S+)", read_text(root / "go.mod"), re.M)
    return match.group(1) if match else ""


def _go_package_key(root: Path, path: Path) -> str:
    parent = path.parent.relative_to(root).as_posix()
    return "" if parent == "." else parent


def _go_package_import_path(root: Path, path: Path, module_name: str) -> str:
    package_key = _go_package_key(root, path)
    if module_name:
        return "/".join(part for part in [module_name, package_key] if part)
    return package_key


def _go_receiver_type(receiver: str | None) -> str | None:
    if not receiver:
        return None
    parts = receiver.strip().split()
    type_name = parts[-1] if parts else ""
    return type_name.strip("*[]")


def _go_receiver_name(receiver: str | None) -> str | None:
    if not receiver:
        return None
    parts = receiver.strip().split()
    return parts[0].strip("*[]") if len(parts) > 1 else None


def _go_local_functions(
    package_functions: dict[tuple[str, str], set[str]],
    package_key: str,
) -> dict[str, str]:
    local: dict[str, str] = {}
    for (indexed_package, name), function_ids in package_functions.items():
        if indexed_package == package_key and len(function_ids) == 1:
            local[name] = next(iter(function_ids))
    return local


def _go_imported_functions(
    text: str,
    package_functions: dict[tuple[str, str], set[str]],
    package_paths: dict[str, str],
    module_name: str,
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for alias, import_path in _go_imports(text):
        package_key = package_paths.get(import_path)
        if package_key is None and module_name and import_path.startswith(f"{module_name}/"):
            package_key = import_path.removeprefix(f"{module_name}/")
        if package_key is None:
            continue
        package_alias = alias if alias and alias not in {".", "_"} else import_path.rsplit("/", 1)[-1]
        for (indexed_package, name), function_ids in package_functions.items():
            if indexed_package == package_key and len(function_ids) == 1:
                function_id = next(iter(function_ids))
                imported[f"{package_alias}.{name}"] = function_id
                if alias == ".":
                    imported[name] = function_id
    return imported


def _go_imports(text: str) -> list[tuple[str | None, str]]:
    imports: list[tuple[str | None, str]] = []
    for match in GO_IMPORT_RE.finditer(text):
        if match.group("single"):
            imports.append((match.group("single_alias"), match.group("single")))
            continue
        block = match.group("block")
        if not block:
            continue
        for item in GO_IMPORT_ITEM_RE.finditer(block):
            imports.append((item.group("alias"), item.group("path")))
    return imports


def _go_known_types(
    package_methods: dict[tuple[str, str, str], set[str]],
    package_key: str,
) -> set[str]:
    return {type_name for indexed_package, type_name, _method in package_methods if indexed_package == package_key}


def _go_known_type_refs(
    text: str,
    package_methods: dict[tuple[str, str, str], set[str]],
    package_paths: dict[str, str],
    module_name: str,
    package_key: str,
) -> dict[str, tuple[str, str]]:
    known_types = {type_name: (package_key, type_name) for type_name in _go_known_types(package_methods, package_key)}
    for alias, import_path in _go_imports(text):
        imported_package_key = package_paths.get(import_path)
        if imported_package_key is None and module_name and import_path.startswith(f"{module_name}/"):
            imported_package_key = import_path.removeprefix(f"{module_name}/")
        if imported_package_key is None:
            continue
        package_alias = alias if alias and alias not in {".", "_"} else import_path.rsplit("/", 1)[-1]
        for indexed_package, type_name, _method in package_methods:
            if indexed_package == imported_package_key:
                known_types[f"{package_alias}.{type_name}"] = (indexed_package, type_name)
                if alias == ".":
                    known_types[type_name] = (indexed_package, type_name)
    return known_types


def _go_instance_aliases(body: str, known_types: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    assignments: dict[str, set[tuple[str, str] | None]] = {}
    for match in GO_VAR_ASSIGN_RE.finditer(body):
        name = match.group("name")
        type_name = match.group("type")
        assignments.setdefault(name, set()).add(known_types.get(type_name))
    for name, type_refs in assignments.items():
        if len(type_refs) == 1:
            type_ref = next(iter(type_refs))
            if type_ref:
                aliases[name] = type_ref
    return aliases


def _go_method_call_target(
    call: str,
    receiver_name: str | None,
    receiver_type: str | None,
    package_key: str,
    package_methods: dict[tuple[str, str, str], set[str]],
    instance_aliases: dict[str, tuple[str, str]],
) -> str | None:
    receiver, _, method_name = call.partition(".")
    if not method_name:
        return None
    type_ref = (package_key, receiver_type) if receiver == receiver_name and receiver_type else instance_aliases.get(receiver)
    if not type_ref:
        return None
    owner_package, type_name = type_ref
    method_ids = package_methods.get((owner_package, type_name, method_name), set())
    if len(method_ids) == 1:
        return next(iter(method_ids))
    return None


@dataclass(frozen=True)
class JavaMethod:
    path: Path
    rel: str
    package: str
    class_name: str
    name: str
    start: int
    end: int

    @property
    def id(self) -> str:
        return f"java:method:{self.rel}:{self.class_name}.{self.name}"

    @property
    def label(self) -> str:
        return f"{self.class_name}.{self.name}"


@dataclass(frozen=True)
class RustCallable:
    path: Path
    rel: str
    name: str
    start: int
    end: int
    owner: str | None = None

    @property
    def id(self) -> str:
        if self.owner:
            return f"rust:method:{self.rel}:{self.owner}.{self.name}"
        return f"rust:function:{self.rel}:{self.name}"

    @property
    def label(self) -> str:
        if self.owner:
            return f"{self.owner}.{self.name}"
        return self.name

    @property
    def kind(self) -> str:
        return "method" if self.owner else "function"


JAVA_TYPE_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|abstract|final|static)\s+)*"
    r"(?:class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)[^{]*\{",
    re.M,
)
JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+(?P<package>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;", re.M)
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?P<class>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;", re.M)
JAVA_STATIC_IMPORT_RE = re.compile(
    r"^\s*import\s+static\s+(?P<class>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.(?P<member>[A-Za-z_]\w*|\*)\s*;",
    re.M,
)
JAVA_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)\s+)*"
    r"(?:(?:<[^>{;]+>\s*)?(?P<return>[A-Za-z_]\w*(?:[<>\[\].?,\s]+[A-Za-z_]\w*)*)\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[^{]+)?\{",
    re.M,
)
JAVA_CALL_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*\(")
JAVA_NEW_INSTANCE_RE = re.compile(
    r"\b(?:(?:final\s+)?(?:[A-Za-z_]\w*(?:<[^;=(){}]+>)?|var)\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*=\s*new\s+(?P<class>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(",
    re.M,
)
JAVA_KEYWORDS = {
    "assert",
    "catch",
    "do",
    "for",
    "if",
    "new",
    "return",
    "super",
    "switch",
    "synchronized",
    "this",
    "throw",
    "try",
    "while",
}


def _build_java_call_graph(root: Path) -> Graph:
    graph = Graph()
    methods: list[JavaMethod] = []
    class_methods: dict[tuple[str, str, str], set[str]] = {}
    class_refs: dict[tuple[str, str], set[tuple[str, str]]] = {}
    files: dict[Path, str] = {}
    file_packages: dict[Path, str] = {}

    for path in iter_files(root):
        if path.suffix != ".java":
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        package_name = _java_package(text)
        files[path] = text
        file_packages[path] = package_name
        for method in _java_methods(path, rel, text, package_name):
            methods.append(method)
            class_methods.setdefault((method.package, method.class_name, method.name), set()).add(method.id)
            class_refs.setdefault((method.package, method.class_name), set()).add((method.package, method.class_name))

    for path, text in files.items():
        rel = path.relative_to(root).as_posix()
        file_id = rel_id("file", root, path)
        graph.add_node(file_id, path.name, attributes={"kind": "file", "language": "java", "path": rel})

    for method in methods:
        text = files[method.path]
        body = text[method.start : method.end]
        file_id = rel_id("file", root, method.path)
        graph.add_node(
            method.id,
            method.label,
            attributes={
                "kind": "method",
                "language": "java",
                "path": method.rel,
                "line": str(text.count("\n", 0, method.start) + 1),
            },
        )
        graph.add_edge(file_id, method.id, "defines")
        known_classes = _java_known_class_refs(text, file_packages[method.path], class_refs)
        static_imported_methods = _java_static_imported_methods(text, class_methods, class_refs)
        instance_aliases = _java_instance_aliases(body, known_classes)
        for call_match in JAVA_CALL_RE.finditer(body):
            call = call_match.group(1)
            base = call.split(".", 1)[0]
            preceding_text = body[max(0, call_match.start() - 8) : call_match.start()]
            if re.search(r"\bnew\s+$", preceding_text) and call in known_classes:
                continue
            if (base in JAVA_KEYWORDS and base != "this") or call == method.name:
                continue
            target_id = _java_method_call_target(
                call,
                (method.package, method.class_name),
                class_methods,
                instance_aliases,
                known_classes,
                static_imported_methods,
            )
            if not target_id:
                target_id = f"java:call:{call}"
                graph.add_node(target_id, call, attributes={"kind": "call_target", "language": "java"})
            graph.add_edge(method.id, target_id, "calls")

    return graph


def _java_package(text: str) -> str:
    match = JAVA_PACKAGE_RE.search(text)
    return match.group("package") if match else ""


def _java_methods(path: Path, rel: str, text: str, package_name: str) -> list[JavaMethod]:
    methods: list[JavaMethod] = []
    for class_match in JAVA_TYPE_RE.finditer(text):
        class_name = class_match.group("name")
        class_body_start = class_match.end() - 1
        class_body = _javascript_braced_block(text, class_body_start)
        if class_body is None:
            continue
        class_offset = class_body_start + 1
        for method_match in JAVA_METHOD_RE.finditer(class_body):
            method_name = method_match.group("name")
            if method_name in JAVA_KEYWORDS:
                continue
            if method_match.group("return") is None and method_name != class_name:
                continue
            start = class_offset + method_match.start()
            method_body_start = class_offset + method_match.end() - 1
            method_body = _javascript_braced_block(text, method_body_start)
            end = method_body_start + len(method_body) + 2 if method_body is not None else class_offset + method_match.end()
            methods.append(JavaMethod(path, rel, package_name, class_name, method_name, start, end))
    return methods


def _java_known_class_refs(
    text: str,
    package_name: str,
    class_refs: dict[tuple[str, str], set[tuple[str, str]]],
) -> dict[str, tuple[str, str]]:
    known: dict[str, tuple[str, str]] = {}
    for (indexed_package, class_name), refs in class_refs.items():
        if indexed_package == package_name and len(refs) == 1:
            known[class_name] = next(iter(refs))

    for match in JAVA_IMPORT_RE.finditer(text):
        qualified_class = match.group("class")
        if qualified_class.endswith(".*"):
            continue
        package, _, class_name = qualified_class.rpartition(".")
        refs = class_refs.get((package, class_name), set())
        if len(refs) == 1:
            ref = next(iter(refs))
            known[class_name] = ref
            known[qualified_class] = ref
    return known


def _java_static_imported_methods(
    text: str,
    class_methods: dict[tuple[str, str, str], set[str]],
    class_refs: dict[tuple[str, str], set[tuple[str, str]]],
) -> dict[str, str]:
    imported: dict[str, set[str]] = {}
    for match in JAVA_STATIC_IMPORT_RE.finditer(text):
        package, _, class_name = match.group("class").rpartition(".")
        refs = class_refs.get((package, class_name), set())
        if len(refs) != 1:
            continue
        class_ref = next(iter(refs))
        member = match.group("member")
        for (method_package, method_class, method_name), method_ids in class_methods.items():
            if (method_package, method_class) != class_ref:
                continue
            if member != "*" and method_name != member:
                continue
            imported.setdefault(method_name, set()).update(method_ids)
    return {name: next(iter(method_ids)) for name, method_ids in imported.items() if len(method_ids) == 1}


def _java_instance_aliases(
    body: str,
    known_classes: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    assignments: dict[str, set[tuple[str, str] | None]] = {}
    for match in JAVA_NEW_INSTANCE_RE.finditer(body):
        name = match.group("name")
        class_name = match.group("class")
        assignments.setdefault(name, set()).add(known_classes.get(class_name))
    for name, class_refs in assignments.items():
        if len(class_refs) == 1:
            class_ref = next(iter(class_refs))
            if class_ref:
                aliases[name] = class_ref
    return aliases


def _java_method_call_target(
    call: str,
    current_class: tuple[str, str],
    class_methods: dict[tuple[str, str, str], set[str]],
    instance_aliases: dict[str, tuple[str, str]],
    known_classes: dict[str, tuple[str, str]],
    static_imported_methods: dict[str, str],
) -> str | None:
    receiver, separator, method_name = call.partition(".")
    if not separator:
        method_ids = class_methods.get((*current_class, receiver), set())
        if len(method_ids) == 1:
            return next(iter(method_ids))
        return static_imported_methods.get(receiver)
    else:
        target_class = current_class if receiver == "this" else instance_aliases.get(receiver) or known_classes.get(receiver)
        method_ids = class_methods.get((*target_class, method_name), set()) if target_class else set()
    if len(method_ids) == 1:
        return next(iter(method_ids))
    return None


RUST_FN_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:<[^>{;]*>\s*)?\(",
    re.M,
)
RUST_IMPL_RE = re.compile(
    r"^\s*impl(?:\s*<[^>{;]*>)?\s+(?P<type>[A-Za-z_]\w*)[^{;]*\{",
    re.M,
)
RUST_CALL_RE = re.compile(
    r"\b([A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)?)\s*(?:::<[^>{}]+>)?\("
)
RUST_USE_RE = re.compile(r"^\s*use\s+(?P<path>[^;]+);", re.M)
RUST_INSTANCE_RE = re.compile(
    r"\blet\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*(?::\s*[A-Za-z_]\w*)?\s*=\s*"
    r"(?P<type>[A-Za-z_]\w*)\s*(?:::|\{)",
    re.M,
)
RUST_KEYWORDS = {
    "async",
    "for",
    "fn",
    "if",
    "impl",
    "let",
    "loop",
    "macro_rules",
    "match",
    "move",
    "return",
    "struct",
    "trait",
    "unsafe",
    "while",
}


def _build_rust_call_graph(root: Path) -> Graph:
    graph = Graph()
    files: dict[Path, str] = {}
    callables: list[RustCallable] = []
    file_functions: dict[tuple[str, str], set[str]] = {}
    module_functions: dict[tuple[str, str], set[str]] = {}
    methods: dict[tuple[str, str], set[str]] = {}

    for path in iter_files(root):
        if path.suffix != ".rs":
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        files[path] = text
        module_key = _rust_module_key(root, path)
        for callable_item in _rust_callables(path, rel, text):
            callables.append(callable_item)
            if callable_item.owner:
                methods.setdefault((callable_item.owner, callable_item.name), set()).add(callable_item.id)
            else:
                file_functions.setdefault((rel, callable_item.name), set()).add(callable_item.id)
                module_functions.setdefault((module_key, callable_item.name), set()).add(callable_item.id)

    known_types = {type_name for type_name, _method_name in methods}
    for path, text in files.items():
        rel = path.relative_to(root).as_posix()
        file_id = rel_id("file", root, path)
        graph.add_node(file_id, path.name, attributes={"kind": "file", "language": "rust", "path": rel})

    for callable_item in callables:
        text = files[callable_item.path]
        body = text[callable_item.start : callable_item.end]
        file_id = rel_id("file", root, callable_item.path)
        local_functions = _rust_local_functions(file_functions, callable_item.rel)
        imported_functions = _rust_imported_functions(text, module_functions)
        module_key = _rust_module_key(root, callable_item.path)
        instance_aliases = _rust_instance_aliases(body, known_types)
        graph.add_node(
            callable_item.id,
            callable_item.label,
            attributes={
                "kind": callable_item.kind,
                "language": "rust",
                "path": callable_item.rel,
                "line": str(text.count("\n", 0, callable_item.start) + 1),
            },
        )
        graph.add_edge(file_id, callable_item.id, "defines")
        for call_match in RUST_CALL_RE.finditer(body):
            call = call_match.group(1)
            base = re.split(r"::|\.", call, maxsplit=1)[0]
            if base in RUST_KEYWORDS:
                continue
            if _rust_call_is_definition_name(body, call_match):
                continue
            target_id = (
                local_functions.get(call)
                or imported_functions.get(call)
                or _rust_module_function_call_target(call, module_key, module_functions)
                or _rust_method_call_target(call, callable_item.owner, methods, instance_aliases)
            )
            if target_id == callable_item.id or (not target_id and call == callable_item.name):
                continue
            if not target_id:
                target_id = f"rust:call:{call}"
                graph.add_node(target_id, call, attributes={"kind": "call_target", "language": "rust"})
            graph.add_edge(callable_item.id, target_id, "calls")

    return graph


def _rust_module_key(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    if rel.name in {"main", "lib"} and rel.parent.as_posix() == ".":
        return ""
    if rel.name == "mod":
        parent = rel.parent.as_posix()
        return "" if parent == "." else parent.replace("/", "::")
    return rel.as_posix().replace("/", "::")


def _rust_callables(path: Path, rel: str, text: str) -> list[RustCallable]:
    impl_ranges: list[tuple[int, int, str]] = []
    callables: list[RustCallable] = []
    for impl_match in RUST_IMPL_RE.finditer(text):
        body_start = impl_match.end() - 1
        body = _javascript_braced_block(text, body_start)
        if body is None:
            continue
        start = body_start + 1
        end = body_start + len(body) + 2
        owner = impl_match.group("type")
        impl_ranges.append((start, end, owner))
        for fn_match in RUST_FN_RE.finditer(body):
            fn_start = start + fn_match.start()
            fn_end = _rust_function_end(text, start + fn_match.end())
            if fn_end is None:
                continue
            callables.append(RustCallable(path, rel, fn_match.group("name"), fn_start, fn_end, owner=owner))

    for fn_match in RUST_FN_RE.finditer(text):
        if any(start <= fn_match.start() < end for start, end, _owner in impl_ranges):
            continue
        fn_end = _rust_function_end(text, fn_match.end())
        if fn_end is None:
            continue
        callables.append(RustCallable(path, rel, fn_match.group("name"), fn_match.start(), fn_end))
    return callables


def _rust_function_end(text: str, search_start: int) -> int | None:
    semicolon_index = text.find(";", search_start)
    body_start = text.find("{", search_start)
    if body_start < 0 or (semicolon_index >= 0 and semicolon_index < body_start):
        return None
    body = _javascript_braced_block(text, body_start)
    if body is None:
        return None
    return body_start + len(body) + 2


def _rust_local_functions(
    file_functions: dict[tuple[str, str], set[str]],
    rel: str,
) -> dict[str, str]:
    local: dict[str, str] = {}
    for (indexed_rel, name), function_ids in file_functions.items():
        if indexed_rel == rel and len(function_ids) == 1:
            local[name] = next(iter(function_ids))
    return local


def _rust_imported_functions(
    text: str,
    module_functions: dict[tuple[str, str], set[str]],
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for module_path, imported_name, local_name in _rust_use_function_aliases(text):
        function_id = _unique_rust_module_function(module_functions, module_path, imported_name)
        if function_id:
            imported[local_name] = function_id
    return imported


def _rust_use_function_aliases(text: str) -> list[tuple[str, str, str]]:
    aliases: list[tuple[str, str, str]] = []
    for match in RUST_USE_RE.finditer(text):
        path = match.group("path").strip()
        brace_match = re.fullmatch(r"(?P<module>.+)::\{(?P<items>[^}]+)\}", path)
        if brace_match:
            module_path = _normal_rust_module_path(brace_match.group("module"))
            for item in brace_match.group("items").split(","):
                parsed = _parse_rust_use_item(item)
                if parsed:
                    imported_name, local_name = parsed
                    aliases.append((module_path, imported_name, local_name))
            continue
        parts = path.rsplit("::", 1)
        if len(parts) != 2:
            continue
        parsed = _parse_rust_use_item(parts[1])
        if parsed:
            imported_name, local_name = parsed
            aliases.append((_normal_rust_module_path(parts[0]), imported_name, local_name))
    return aliases


def _parse_rust_use_item(item: str) -> tuple[str, str] | None:
    item = item.strip()
    if not item or item == "self" or item == "*":
        return None
    parts = re.split(r"\s+as\s+", item, maxsplit=1)
    imported_name = parts[0].strip()
    local_name = parts[1].strip() if len(parts) == 2 else imported_name
    if re.fullmatch(r"[A-Za-z_]\w*", imported_name) and re.fullmatch(r"[A-Za-z_]\w*", local_name):
        return imported_name, local_name
    return None


def _normal_rust_module_path(module_path: str) -> str:
    parts = [part for part in module_path.split("::") if part and part not in {"crate", "self"}]
    return "::".join(parts)


def _rust_module_function_call_target(
    call: str,
    current_module: str,
    module_functions: dict[tuple[str, str], set[str]],
) -> str | None:
    if "::" not in call:
        return None
    module_path, function_name = call.rsplit("::", 1)
    module_path = _normal_rust_module_path(module_path)
    candidate_modules = [module_path]
    if current_module and module_path:
        candidate_modules.append(f"{current_module}::{module_path}")
    matches: set[str] = set()
    for candidate in candidate_modules:
        matches.update(module_functions.get((candidate, function_name), set()))
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _unique_rust_module_function(
    module_functions: dict[tuple[str, str], set[str]],
    module_path: str,
    function_name: str,
) -> str | None:
    function_ids = module_functions.get((module_path, function_name), set())
    if len(function_ids) == 1:
        return next(iter(function_ids))
    return None


def _rust_instance_aliases(body: str, known_types: set[str]) -> dict[str, str]:
    assignments: dict[str, set[str | None]] = {}
    for match in RUST_INSTANCE_RE.finditer(body):
        name = match.group("name")
        type_name = match.group("type")
        assignments.setdefault(name, set()).add(type_name if type_name in known_types else None)
    aliases: dict[str, str] = {}
    for name, type_names in assignments.items():
        if len(type_names) == 1:
            type_name = next(iter(type_names))
            if type_name:
                aliases[name] = type_name
    return aliases


def _rust_method_call_target(
    call: str,
    current_owner: str | None,
    methods: dict[tuple[str, str], set[str]],
    instance_aliases: dict[str, str],
) -> str | None:
    if "." in call:
        receiver, method_name = call.split(".", 1)
        owner = current_owner if receiver == "self" else instance_aliases.get(receiver)
    elif "::" in call:
        owner, method_name = call.split("::", 1)
    else:
        return None
    if not owner:
        return None
    method_ids = methods.get((owner, method_name), set())
    if len(method_ids) == 1:
        return next(iter(method_ids))
    return None


def _rust_call_is_definition_name(body: str, call_match: re.Match[str]) -> bool:
    line_start = body.rfind("\n", 0, call_match.start()) + 1
    line_prefix = body[line_start : call_match.start()]
    return bool(re.search(r"\bfn\s+$", line_prefix))
