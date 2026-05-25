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
    r"|export\s+(?:\*|\{[^}]*\})\s+from\s+['\"]([^'\"]+)['\"]"
    r"|from\s+([\w.]+)\s+import"
    r"|(?:const|let|var)\s+.+?=\s*require\(['\"]([^'\"]+)['\"]\)"
    r"|(?:const|let|var)\s+.+?=\s*(?:await\s+)?import\(\s*['\"]([^'\"]+)['\"]\s*\)"
    r"|require\(['\"]([^'\"]+)['\"]\)"
    r")",
    re.M,
)
TS_ENTITY_RE = re.compile(r"^\s*export\s+(?:default\s+)?(?:class|function|interface|type|const)\s+([A-Za-z_$][\w$]*)|^\s*(?:class|function)\s+([A-Za-z_$][\w$]*)", re.M)
GO_ENTITY_RE = re.compile(
    r"^\s*func\s+(?:\((?P<receiver>[^)]+)\)\s*)?(?P<func>[A-Za-z_]\w*)\s*\("
    r"|^\s*type\s+(?P<type>[A-Za-z_]\w*)\s+(?:struct|interface)\b",
    re.M,
)
GO_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:"
    r"(?:\.|_|[A-Za-z_]\w*)?\s*\"(?P<single>[^\"]+)\""
    r"|\((?P<block>.*?)\)"
    r")",
    re.M | re.S,
)
GO_IMPORT_ITEM_RE = re.compile(r"^\s*(?:\.|_|[A-Za-z_]\w*)?\s*\"(?P<path>[^\"]+)\"", re.M)
RUST_TYPE_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+(?P<name>[A-Za-z_]\w*)\b",
    re.M,
)
RUST_FN_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:<[^>{;]*>\s*)?\(",
    re.M,
)
RUST_IMPL_RE = re.compile(
    r"^\s*impl(?:\s*<[^>{;]*>)?\s+(?P<type>[A-Za-z_]\w*)[^{;]*\{",
    re.M,
)
RUST_MOD_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>[A-Za-z_]\w*)\s*;", re.M)
RUST_USE_RE = re.compile(r"^\s*use\s+(?P<path>[^;]+);", re.M)
JAVA_TYPE_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*"
    r"(?:class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)[^{;]*\{",
    re.M,
)
JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+(?P<package>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;", re.M)
JAVA_IMPORT_RE = re.compile(
    r"^\s*import\s+(?P<static>static\s+)?(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\.\*)?)\s*;",
    re.M,
)
JAVA_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)\s+)*"
    r"(?:(?:<[^>{;]+>\s*)?(?P<return>[A-Za-z_]\w*(?:[<>\[\].?,\s]+[A-Za-z_]\w*)*)\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[^{]+)?\{",
    re.M,
)


def build_entity_graph(root: Path) -> Graph:
    graph = Graph()
    python_modules = _python_module_index(root)
    javascript_modules = _javascript_module_index(root)
    go_modules = _go_module_index(root)
    rust_modules = _rust_module_index(root)
    java_types, java_packages = _java_type_indexes(root)
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
        elif path.suffix == ".go":
            _add_go_entities(graph, root, path, text, file_id)
            _add_go_imports(graph, file_id, text, go_modules, _go_module_name(root))
        elif path.suffix == ".rs":
            _add_rust_entities(graph, root, path, text, file_id)
            _add_rust_imports(graph, root, path, text, file_id, rust_modules)
        elif path.suffix == ".java":
            _add_java_entities(graph, root, path, text, file_id)
            _add_java_imports(graph, file_id, text, java_types, java_packages)
        elif path.suffix == ".py":
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


def _add_go_entities(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    for match in GO_ENTITY_RE.finditer(text):
        type_name = match.group("type")
        func_name = match.group("func")
        receiver_type = _go_receiver_type(match.group("receiver"))
        if type_name:
            entity_id = f"go:entity:{rel}:{type_name}"
            label = type_name
            kind = "entity"
        elif receiver_type and func_name:
            entity_id = f"go:method:{rel}:{receiver_type}.{func_name}"
            label = f"{receiver_type}.{func_name}"
            kind = "method"
        elif func_name:
            entity_id = f"go:function:{rel}:{func_name}"
            label = func_name
            kind = "function"
        else:
            continue
        graph.add_node(entity_id, label, attributes={"kind": kind, "language": "go", "path": rel})
        graph.add_edge(file_id, entity_id, "defines")


def _add_rust_entities(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    impl_ranges: list[tuple[int, int]] = []
    for match in RUST_TYPE_RE.finditer(text):
        name = match.group("name")
        entity_id = f"rust:entity:{rel}:{name}"
        graph.add_node(entity_id, name, attributes={"kind": "entity", "language": "rust", "path": rel})
        graph.add_edge(file_id, entity_id, "defines")

    for impl_match in RUST_IMPL_RE.finditer(text):
        body_start = impl_match.end() - 1
        body = _braced_block(text, body_start)
        if body is None:
            continue
        start = body_start + 1
        end = body_start + len(body) + 2
        impl_ranges.append((start, end))
        owner = impl_match.group("type")
        for fn_match in RUST_FN_RE.finditer(body):
            if not _rust_function_has_body(text, start + fn_match.end()):
                continue
            name = fn_match.group("name")
            method_id = f"rust:method:{rel}:{owner}.{name}"
            graph.add_node(method_id, f"{owner}.{name}", attributes={"kind": "method", "language": "rust", "path": rel})
            graph.add_edge(file_id, method_id, "defines")

    for fn_match in RUST_FN_RE.finditer(text):
        if any(start <= fn_match.start() < end for start, end in impl_ranges):
            continue
        if not _rust_function_has_body(text, fn_match.end()):
            continue
        name = fn_match.group("name")
        function_id = f"rust:function:{rel}:{name}"
        graph.add_node(function_id, name, attributes={"kind": "function", "language": "rust", "path": rel})
        graph.add_edge(file_id, function_id, "defines")


def _add_java_entities(graph: Graph, root: Path, path: Path, text: str, file_id: str) -> None:
    rel = path.relative_to(root).as_posix()
    for type_match in JAVA_TYPE_RE.finditer(text):
        type_name = type_match.group("name")
        entity_id = f"java:entity:{rel}:{type_name}"
        graph.add_node(entity_id, type_name, attributes={"kind": "entity", "language": "java", "path": rel})
        graph.add_edge(file_id, entity_id, "defines")

        class_body_start = type_match.end() - 1
        class_body = _braced_block(text, class_body_start)
        if class_body is None:
            continue
        class_offset = class_body_start + 1
        for method_match in JAVA_METHOD_RE.finditer(class_body):
            method_name = method_match.group("name")
            if method_match.group("return") is None and method_name != type_name:
                continue
            method_id = f"java:method:{rel}:{type_name}.{method_name}"
            graph.add_node(
                method_id,
                f"{type_name}.{method_name}",
                attributes={
                    "kind": "method",
                    "language": "java",
                    "path": rel,
                    "line": str(text.count("\n", 0, class_offset + method_match.start()) + 1),
                },
            )
            graph.add_edge(file_id, method_id, "defines")


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
            module = _resolve_python_import_from(
                current_module,
                node.level,
                node.module,
                current_is_package=path.name == "__init__.py",
            )
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


def _add_go_imports(
    graph: Graph,
    file_id: str,
    text: str,
    go_modules: dict[str, str],
    module_name: str,
) -> None:
    for import_path in _go_imports(text):
        _add_import(graph, file_id, "go", import_path)
        target_id = go_modules.get(import_path)
        if target_id:
            graph.add_edge(file_id, target_id, "imports")
            continue
        if module_name and import_path.startswith(f"{module_name}/"):
            target_id = go_modules.get(import_path.removeprefix(f"{module_name}/"))
            if target_id:
                graph.add_edge(file_id, target_id, "imports")


def _go_imports(text: str) -> list[str]:
    imports: list[str] = []
    for match in GO_IMPORT_RE.finditer(text):
        if match.group("single"):
            imports.append(match.group("single"))
            continue
        block = match.group("block")
        if not block:
            continue
        imports.extend(item.group("path") for item in GO_IMPORT_ITEM_RE.finditer(block))
    return imports


def _go_module_index(root: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    module_name = _go_module_name(root)
    for path in iter_files(root):
        if path.suffix != ".go":
            continue
        package_key = _go_package_key(root, path)
        file_id = rel_id("file", root, path)
        modules.setdefault(package_key, file_id)
        if module_name:
            modules.setdefault("/".join(part for part in [module_name, package_key] if part), file_id)
    return modules


def _go_module_name(root: Path) -> str:
    match = re.search(r"^\s*module\s+(\S+)", read_text(root / "go.mod"), re.M)
    return match.group(1) if match else ""


def _go_package_key(root: Path, path: Path) -> str:
    parent = path.parent.relative_to(root).as_posix()
    return "" if parent == "." else parent


def _add_rust_imports(
    graph: Graph,
    root: Path,
    path: Path,
    text: str,
    file_id: str,
    rust_modules: dict[str, str],
) -> None:
    current_module = _rust_module_name(root, path)
    for match in RUST_MOD_RE.finditer(text):
        module = _join_rust_module(current_module, match.group("name"))
        _add_import(graph, file_id, "rust", module)
        target_id = rust_modules.get(module)
        if target_id:
            graph.add_edge(file_id, target_id, "imports")

    for match in RUST_USE_RE.finditer(text):
        for module in _rust_use_modules(match.group("path"), rust_modules):
            _add_import(graph, file_id, "rust", module)
            target_id = rust_modules.get(module)
            if target_id:
                graph.add_edge(file_id, target_id, "imports")


def _add_java_imports(
    graph: Graph,
    file_id: str,
    text: str,
    java_types: dict[str, str],
    java_packages: dict[str, set[str]],
) -> None:
    for match in JAVA_IMPORT_RE.finditer(text):
        target = match.group("target")
        _add_import(graph, file_id, "java", target)
        for target_id in _resolve_java_import(target, bool(match.group("static")), java_types, java_packages):
            graph.add_edge(file_id, target_id, "imports")


def _resolve_java_import(
    target: str,
    is_static: bool,
    java_types: dict[str, str],
    java_packages: dict[str, set[str]],
) -> list[str]:
    if target.endswith(".*"):
        owner = target[:-2]
        if is_static:
            target_id = java_types.get(owner)
            return [target_id] if target_id else []
        return sorted(java_packages.get(owner, set()))

    target_id = java_types.get(target)
    if target_id:
        return [target_id]
    if is_static:
        owner, _, _member = target.rpartition(".")
        target_id = java_types.get(owner)
        if target_id:
            return [target_id]
    return []


def _java_type_indexes(root: Path) -> tuple[dict[str, str], dict[str, set[str]]]:
    types: dict[str, str] = {}
    packages: dict[str, set[str]] = {}
    for path in iter_files(root):
        if path.suffix != ".java":
            continue
        text = read_text(path)
        package_name = _java_package(text)
        file_id = rel_id("file", root, path)
        for type_match in JAVA_TYPE_RE.finditer(text):
            type_name = type_match.group("name")
            qualified_name = ".".join(part for part in [package_name, type_name] if part)
            if qualified_name:
                types.setdefault(qualified_name, file_id)
            if package_name:
                packages.setdefault(package_name, set()).add(file_id)
    return types, packages


def _java_package(text: str) -> str:
    match = JAVA_PACKAGE_RE.search(text)
    return match.group("package") if match else ""


def _rust_use_modules(path: str, rust_modules: dict[str, str]) -> list[str]:
    modules: set[str] = set()
    path = path.strip()
    brace_match = re.fullmatch(r"(?P<module>.+)::\{(?P<items>[^}]+)\}", path)
    if brace_match:
        module = _normal_rust_module_path(brace_match.group("module"))
        if module in rust_modules:
            modules.add(module)
        for item in brace_match.group("items").split(","):
            item_name = re.split(r"\s+as\s+", item.strip(), maxsplit=1)[0].strip()
            if item_name and item_name not in {"self", "*"}:
                nested_module = _join_rust_module(module, item_name)
                if nested_module in rust_modules:
                    modules.add(nested_module)
        return sorted(modules)

    module = _longest_rust_module_prefix(_normal_rust_module_path(path), rust_modules)
    return [module] if module else []


def _longest_rust_module_prefix(path: str, rust_modules: dict[str, str]) -> str | None:
    parts = [part for part in path.split("::") if part]
    for end in range(len(parts), 0, -1):
        candidate = "::".join(parts[:end])
        if candidate in rust_modules:
            return candidate
    return None


def _rust_module_index(root: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in iter_files(root):
        if path.suffix != ".rs":
            continue
        modules.setdefault(_rust_module_name(root, path), rel_id("file", root, path))
    return modules


def _rust_module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    if rel.name in {"main", "lib"} and rel.parent.as_posix() == ".":
        return ""
    if rel.name == "mod":
        parent = rel.parent.as_posix()
        return "" if parent == "." else parent.replace("/", "::")
    return rel.as_posix().replace("/", "::")


def _normal_rust_module_path(module_path: str) -> str:
    parts = [part for part in module_path.split("::") if part and part not in {"crate", "self"}]
    return "::".join(parts)


def _join_rust_module(parent: str, child: str) -> str:
    return "::".join(part for part in [parent, child] if part)


def _go_receiver_type(receiver: str | None) -> str | None:
    if not receiver:
        return None
    parts = receiver.strip().split()
    type_name = parts[-1] if parts else ""
    return type_name.strip("*[]")


def _braced_block(text: str, open_brace_index: int) -> str | None:
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


def _rust_function_has_body(text: str, search_start: int) -> bool:
    semicolon_index = text.find(";", search_start)
    body_start = text.find("{", search_start)
    return body_start >= 0 and not (semicolon_index >= 0 and semicolon_index < body_start)


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


def _resolve_python_import_from(
    current_module: str,
    level: int,
    module: str | None,
    *,
    current_is_package: bool = False,
) -> str:
    if level == 0:
        return module or ""
    package_parts = current_module.split(".") if current_is_package else current_module.split(".")[:-1]
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
