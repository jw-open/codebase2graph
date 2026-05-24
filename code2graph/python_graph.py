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
        local_classes: dict[str, str],
        known_methods: dict[tuple[str, str], str],
        imported_functions: dict[str, str],
        imported_classes: dict[str, str],
        module_function_aliases: dict[str, str],
        module_class_aliases: dict[str, str],
        function_index: dict[tuple[str, str], str],
        class_index: dict[tuple[str, str], str],
    ) -> None:
        self.root = root
        self.path = path
        self.graph = graph
        self.file_id = rel_id("file", root, path)
        self.local_functions = local_functions
        self.local_classes = local_classes
        self.known_methods = known_methods
        self.imported_functions = imported_functions
        self.imported_classes = imported_classes
        self.module_function_aliases = module_function_aliases
        self.module_class_aliases = module_class_aliases
        self.function_index = function_index
        self.class_index = class_index
        self.current_module = _module_name(root, path)
        self.scope: list[str] = []
        self.lexical_function_scopes: list[dict[str, str]] = []

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
        enclosing_class_id = self.scope[-1] if kind == "method" else None
        scoped_imported_functions = _imported_function_ids_from_body(
            node.body,
            self.current_module,
            self.function_index,
        )
        scoped_imported_classes = _imported_class_ids_from_body(
            node.body,
            self.current_module,
            self.class_index,
        )
        known_classes = {
            **self.imported_classes,
            **self.module_class_aliases,
            **self.local_classes,
            **scoped_imported_classes,
        }
        class_aliases = _function_class_aliases(node, known_classes)
        nested_functions = self._nested_function_ids(node)
        enclosing_functions = _merge_scopes(self.lexical_function_scopes)
        known_functions = {
            **self.imported_functions,
            **self.module_function_aliases,
            **self.local_functions,
            **scoped_imported_functions,
            **enclosing_functions,
            **nested_functions,
        }
        function_aliases, shadowed_functions = _function_aliases(node, known_functions)
        self.scope.append(func_id)
        for child in _scope_calls(node):
            call_name = _call_name(child.func)
            if call_name:
                target_id = (
                    self._method_call_target(call_name, enclosing_class_id)
                    or self._instance_method_call_target(call_name, class_aliases)
                    or self._class_method_call_target(call_name, known_classes)
                    or self._class_call_target(call_name, known_classes)
                )
                if not target_id:
                    if call_name in nested_functions:
                        target_id = nested_functions[call_name]
                    elif call_name in shadowed_functions:
                        target_id = function_aliases.get(call_name)
                    else:
                        target_id = (
                            scoped_imported_functions.get(call_name)
                            or nested_functions.get(call_name)
                            or enclosing_functions.get(call_name)
                            or self.local_functions.get(call_name)
                            or self.module_function_aliases.get(call_name)
                            or self.imported_functions.get(call_name)
                        )
                if not target_id:
                    target_id = f"py:call:{call_name}"
                    self.graph.add_node(
                        target_id,
                        call_name,
                        attributes={"kind": "call_target", "language": "python"},
                    )
                self.graph.add_edge(func_id, target_id, "calls")
        self.lexical_function_scopes.append(nested_functions)
        self.generic_visit(node)
        self.lexical_function_scopes.pop()
        self.scope.pop()

    def _nested_function_ids(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
        counts: dict[str, int] = {}
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                counts[child.name] = counts.get(child.name, 0) + 1

        rel = self.path.relative_to(self.root).as_posix()
        scope = ".".join([self.graph.nodes[s].label for s in self.scope])
        prefix = f"{scope}.{node.name}" if scope else node.name
        return {
            name: f"py:function:{rel}:{prefix}.{name}"
            for name, count in counts.items()
            if count == 1
        }

    def _method_call_target(self, call_name: str, enclosing_class_id: str | None) -> str | None:
        if not enclosing_class_id:
            return None
        receiver, _, method_name = call_name.partition(".")
        if receiver not in {"self", "cls"} or not method_name or "." in method_name:
            return None
        return self.known_methods.get((enclosing_class_id, method_name))

    def _instance_method_call_target(self, call_name: str, class_aliases: dict[str, str]) -> str | None:
        receiver, _, method_name = call_name.partition(".")
        if not receiver or not method_name or "." in method_name:
            return None
        class_id = class_aliases.get(receiver)
        if not class_id:
            return None
        return self.known_methods.get((class_id, method_name))

    def _class_method_call_target(self, call_name: str, known_classes: dict[str, str]) -> str | None:
        receiver, _, method_name = call_name.rpartition(".")
        if not receiver or not method_name:
            return None
        class_id = known_classes.get(receiver)
        if not class_id:
            return None
        return self.known_methods.get((class_id, method_name))

    def _class_call_target(self, call_name: str, known_classes: dict[str, str]) -> str | None:
        return known_classes.get(call_name)

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
    class_index = _project_class_index(root, parsed_modules)
    method_index = _project_method_index(root, parsed_modules, class_index)
    for path, tree in parsed_modules:
        local_functions = _local_function_ids(root, path, tree)
        local_classes = _local_class_ids(root, path, tree)
        imported_functions = _imported_function_ids(root, path, tree, function_index)
        imported_classes = _imported_class_ids(root, path, tree, class_index)
        module_function_aliases = _module_function_aliases(
            tree.body,
            {**imported_functions, **local_functions},
            reserved=set(local_functions),
        )
        module_class_aliases = _module_class_aliases(
            tree.body,
            {**imported_classes, **local_classes},
            reserved=set(local_classes),
        )
        PythonCollector(
            root,
            path,
            graph,
            local_functions,
            local_classes,
            method_index,
            imported_functions,
            imported_classes,
            module_function_aliases,
            module_class_aliases,
            function_index,
            class_index,
        ).visit(tree)
    return graph


def _project_function_index(root: Path, modules: list[tuple[Path, ast.Module]]) -> dict[tuple[str, str], str]:
    function_index: dict[tuple[str, str], str] = {}
    for path, tree in modules:
        module = _module_name(root, path)
        for name, function_id in _local_function_ids(root, path, tree).items():
            function_index[(module, name)] = function_id
    return function_index


def _project_class_index(root: Path, modules: list[tuple[Path, ast.Module]]) -> dict[tuple[str, str], str]:
    class_index: dict[tuple[str, str], str] = {}
    for path, tree in modules:
        module = _module_name(root, path)
        for name, class_id in _local_class_ids(root, path, tree).items():
            class_index[(module, name)] = class_id
    return class_index


def _project_method_index(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    class_index: dict[tuple[str, str], str],
) -> dict[tuple[str, str], str]:
    method_index: dict[tuple[str, str], str] = {}
    for path, tree in modules:
        method_index.update(_local_method_ids(root, path, tree))
    class_bases = _project_class_base_ids(root, modules, class_index)
    for class_id in sorted(class_bases):
        _add_inherited_methods(class_id, method_index, class_bases, set())
    return method_index


def _project_class_base_ids(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    class_index: dict[tuple[str, str], str],
) -> dict[str, list[str]]:
    class_bases: dict[str, list[str]] = {}
    for path, tree in modules:
        local_classes = _local_class_ids(root, path, tree)
        imported_classes = _imported_class_ids(root, path, tree, class_index)
        module_class_aliases = _module_class_aliases(
            tree.body,
            {**imported_classes, **local_classes},
            reserved=set(local_classes),
        )
        known_classes = {**imported_classes, **module_class_aliases, **local_classes}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            class_id = local_classes.get(node.name)
            if not class_id:
                continue
            bases: list[str] = []
            for base in node.bases:
                base_name = _call_name(base)
                if not base_name:
                    continue
                base_id = known_classes.get(base_name)
                if base_id and base_id != class_id:
                    bases.append(base_id)
            if bases:
                class_bases[class_id] = bases
    return class_bases


def _add_inherited_methods(
    class_id: str,
    method_index: dict[tuple[str, str], str],
    class_bases: dict[str, list[str]],
    visiting: set[str],
) -> None:
    if class_id in visiting:
        return
    visiting.add(class_id)
    method_names = {method_name for owner_id, method_name in method_index if owner_id == class_id}
    for base_id in class_bases.get(class_id, []):
        _add_inherited_methods(base_id, method_index, class_bases, visiting)
        for (owner_id, method_name), method_id in list(method_index.items()):
            if owner_id == base_id and method_name not in method_names:
                method_index[(class_id, method_name)] = method_id
                method_names.add(method_name)
    visiting.remove(class_id)


def _imported_function_ids(
    root: Path,
    path: Path,
    tree: ast.Module,
    function_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    return _imported_function_ids_from_body(tree.body, _module_name(root, path), function_index)


def _imported_function_ids_from_body(
    body: list[ast.stmt],
    current_module: str,
    function_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for node in body:
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
                    _add_star_function_aliases(imported, function_index, module)
                    continue
                local_name = alias.asname or alias.name
                function_id = function_index.get((module, alias.name))
                if function_id:
                    imported[local_name] = function_id
                _add_module_function_aliases(imported, function_index, local_name, f"{module}.{alias.name}")
    return imported


def _imported_class_ids(
    root: Path,
    path: Path,
    tree: ast.Module,
    class_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    return _imported_class_ids_from_body(tree.body, _module_name(root, path), class_index)


def _imported_class_ids_from_body(
    body: list[ast.stmt],
    current_module: str,
    class_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for node in body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                local_name = alias.asname or module
                _add_module_class_aliases(imported, class_index, local_name, module)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(current_module, node.level, node.module)
            if not module:
                continue
            for alias in node.names:
                if alias.name == "*":
                    _add_star_class_aliases(imported, class_index, module)
                    continue
                local_name = alias.asname or alias.name
                class_id = class_index.get((module, alias.name))
                if class_id:
                    imported[local_name] = class_id
                _add_module_class_aliases(imported, class_index, local_name, f"{module}.{alias.name}")
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
            suffix = indexed_module.removeprefix(prefix) if indexed_module.startswith(prefix) else ""
            qualified_name = ".".join(part for part in [local_name, suffix, function_name] if part)
            imported[qualified_name] = function_id


def _add_star_function_aliases(
    imported: dict[str, str],
    function_index: dict[tuple[str, str], str],
    module: str,
) -> None:
    for (indexed_module, function_name), function_id in function_index.items():
        if indexed_module == module:
            imported[function_name] = function_id


def _add_module_class_aliases(
    imported: dict[str, str],
    class_index: dict[tuple[str, str], str],
    local_name: str,
    module: str,
) -> None:
    prefix = f"{module}."
    for (indexed_module, class_name), class_id in class_index.items():
        if indexed_module == module or indexed_module.startswith(prefix):
            suffix = indexed_module.removeprefix(prefix) if indexed_module.startswith(prefix) else ""
            qualified_name = ".".join(part for part in [local_name, suffix, class_name] if part)
            imported[qualified_name] = class_id


def _add_star_class_aliases(
    imported: dict[str, str],
    class_index: dict[tuple[str, str], str],
    module: str,
) -> None:
    for (indexed_module, class_name), class_id in class_index.items():
        if indexed_module == module:
            imported[class_name] = class_id


def _local_function_ids(root: Path, path: Path, tree: ast.Module) -> dict[str, str]:
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] = counts.get(node.name, 0) + 1
    rel = path.relative_to(root).as_posix()
    return {name: f"py:function:{rel}:{name}" for name, count in counts.items() if count == 1}


def _local_class_ids(root: Path, path: Path, tree: ast.Module) -> dict[str, str]:
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            counts[node.name] = counts.get(node.name, 0) + 1
    rel = path.relative_to(root).as_posix()
    return {name: f"py:class:{rel}:{name}" for name, count in counts.items() if count == 1}


def _local_method_ids(root: Path, path: Path, tree: ast.Module) -> dict[tuple[str, str], str]:
    rel = path.relative_to(root).as_posix()
    methods: dict[tuple[str, str], str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        counts: dict[str, int] = {}
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                counts[child.name] = counts.get(child.name, 0) + 1
        class_id = f"py:class:{rel}:{node.name}"
        for name, count in counts.items():
            if count == 1:
                methods[(class_id, name)] = f"py:method:{rel}:{node.name}.{name}"
    return methods


def _function_class_aliases(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    local_classes: dict[str, str],
) -> dict[str, str]:
    visitor = _ClassAliasVisitor(local_classes)
    for child in node.body:
        visitor.visit(child)

    aliases: dict[str, str] = {}
    for name, class_ids in visitor.assignments.items():
        if len(class_ids) == 1:
            class_id = next(iter(class_ids))
            if class_id:
                aliases[name] = class_id
    return aliases


def _function_aliases(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_functions: dict[str, str],
) -> tuple[dict[str, str], set[str]]:
    visitor = _FunctionAliasVisitor(known_functions)
    for name in _argument_names(node.args):
        visitor.assignments.setdefault(name, set()).add(None)
    for child in node.body:
        visitor.visit(child)

    aliases: dict[str, str] = {}
    for name, function_ids in visitor.assignments.items():
        if len(function_ids) == 1:
            function_id = next(iter(function_ids))
            if function_id:
                aliases[name] = function_id
    return aliases, set(visitor.assignments)


def _module_function_aliases(
    body: list[ast.stmt],
    known_functions: dict[str, str],
    *,
    reserved: set[str],
) -> dict[str, str]:
    aliases, shadowed = _aliases_from_body(body, _FunctionAliasVisitor(known_functions))
    return {name: target for name, target in aliases.items() if name not in reserved and name in shadowed}


def _module_class_aliases(
    body: list[ast.stmt],
    known_classes: dict[str, str],
    *,
    reserved: set[str],
) -> dict[str, str]:
    aliases, shadowed = _aliases_from_body(body, _ClassAliasReferenceVisitor(known_classes))
    return {name: target for name, target in aliases.items() if name not in reserved and name in shadowed}


def _aliases_from_body(
    body: list[ast.stmt],
    visitor: _FunctionAliasVisitor | _ClassAliasReferenceVisitor,
) -> tuple[dict[str, str], set[str]]:
    for child in body:
        visitor.visit(child)

    aliases: dict[str, str] = {}
    for name, target_ids in visitor.assignments.items():
        if len(target_ids) == 1:
            target_id = next(iter(target_ids))
            if target_id:
                aliases[name] = target_id
    return aliases, set(visitor.assignments)


def _merge_scopes(scopes: list[dict[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for scope in scopes:
        merged.update(scope)
    return merged


class _ClassAliasVisitor(ast.NodeVisitor):
    def __init__(self, local_classes: dict[str, str]) -> None:
        self.local_classes = local_classes
        self.assignments: dict[str, set[str | None]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        class_id = _class_instantiation_target(node.value, self.local_classes)
        for target in node.targets:
            self._record(target, class_id)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        class_id = _class_instantiation_target(node.value, self.local_classes) if node.value else None
        self._record(node.target, class_id)
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record(node.target, None)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self._record(node.target, None)
        for child in [*node.body, *node.orelse]:
            self.visit(child)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars:
                self._record(item.optional_vars, None)
        for child in node.body:
            self.visit(child)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def _record(self, target: ast.AST, class_id: str | None) -> None:
        for name in _target_names(target):
            self.assignments.setdefault(name, set()).add(class_id)


class _FunctionAliasVisitor(ast.NodeVisitor):
    def __init__(self, known_functions: dict[str, str]) -> None:
        self.known_functions = known_functions
        self.assignments: dict[str, set[str | None]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        function_id = self._reference_target(node.value)
        for target in node.targets:
            self._record(target, function_id)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        function_id = self._reference_target(node.value) if node.value else None
        self._record(node.target, function_id)
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record(node.target, None)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self._record(node.target, None)
        for child in [*node.body, *node.orelse]:
            self.visit(child)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars:
                self._record(item.optional_vars, None)
        for child in node.body:
            self.visit(child)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def _record(self, target: ast.AST, function_id: str | None) -> None:
        for name in _target_names(target):
            self.assignments.setdefault(name, set()).add(function_id)

    def _reference_target(self, node: ast.AST) -> str | None:
        call_name = _call_name(node)
        if not call_name or call_name in self.assignments:
            return None
        return self.known_functions.get(call_name)


class _ClassAliasReferenceVisitor(_FunctionAliasVisitor):
    def __init__(self, known_classes: dict[str, str]) -> None:
        self.known_classes = known_classes
        self.assignments: dict[str, set[str | None]] = {}

    def _reference_target(self, node: ast.AST) -> str | None:
        call_name = _call_name(node)
        if not call_name or call_name in self.assignments:
            return None
        return self.known_classes.get(call_name)


class _ScopeCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


def _scope_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    visitor = _ScopeCallVisitor()
    for child in node.body:
        visitor.visit(child)
    return visitor.calls


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_target_names(element))
        return names
    return []


def _class_instantiation_target(node: ast.AST, local_classes: dict[str, str]) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    call_name = _call_name(node.func)
    if not call_name:
        return None
    return local_classes.get(call_name)


def _argument_names(args: ast.arguments) -> list[str]:
    names: list[str] = []
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        names.append(arg.arg)
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


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
