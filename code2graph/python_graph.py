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
        class_bases: dict[str, list[str]],
        imported_functions: dict[str, str],
        imported_classes: dict[str, str],
        module_function_aliases: dict[str, str],
        module_class_aliases: dict[str, str],
        module_instance_aliases: dict[str, str],
        module_partial_names: set[str],
        class_instance_aliases: dict[str, dict[str, str]],
        function_return_classes: dict[str, str],
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
        self.class_bases = class_bases
        self.imported_functions = imported_functions
        self.imported_classes = imported_classes
        self.module_function_aliases = module_function_aliases
        self.module_class_aliases = module_class_aliases
        self.module_instance_aliases = module_instance_aliases
        self.module_partial_names = module_partial_names
        self.class_instance_aliases = class_instance_aliases
        self.function_return_classes = function_return_classes
        self.function_index = function_index
        self.class_index = class_index
        self.current_module = _module_name(root, path)
        self.current_module_is_package = path.name == "__init__.py"
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
            current_is_package=self.current_module_is_package,
        )
        scoped_imported_classes = _imported_class_ids_from_body(
            node.body,
            self.current_module,
            self.class_index,
            current_is_package=self.current_module_is_package,
        )
        known_classes = {
            **self.imported_classes,
            **self.module_class_aliases,
            **self.local_classes,
            **scoped_imported_classes,
        }
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
        known_factory_returns = _function_return_class_aliases(known_functions, self.function_return_classes)
        class_aliases = {
            **self.module_instance_aliases,
            **(self.class_instance_aliases.get(enclosing_class_id, {}) if enclosing_class_id else {}),
            **_function_class_aliases(node, known_classes, known_factory_returns),
        }
        known_callables = {
            **known_functions,
            **self._method_reference_targets(class_aliases),
            **self._method_reference_targets(known_classes),
            **self._method_reference_targets(("self", "cls"), enclosing_class_id),
        }
        function_aliases, shadowed_functions = _function_aliases(
            node,
            known_callables,
            partial_names=self.module_partial_names | _functools_partial_names(node.body),
        )
        self.scope.append(func_id)
        for child in _scope_calls(node):
            call_name = _call_name(child.func)
            if call_name:
                target_id = (
                    self._super_method_call_target(child.func, enclosing_class_id)
                    or self._method_call_target(call_name, enclosing_class_id)
                    or self._instance_method_call_target(call_name, class_aliases)
                    or self._instance_call_target(call_name, class_aliases)
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

    def _super_method_call_target(self, node: ast.AST, enclosing_class_id: str | None) -> str | None:
        if not enclosing_class_id or not isinstance(node, ast.Attribute):
            return None
        if not _is_super_call(node.value):
            return None
        return _unique_base_method_target(
            enclosing_class_id,
            node.attr,
            self.known_methods,
            self.class_bases,
            set(),
        )

    def _instance_method_call_target(self, call_name: str, class_aliases: dict[str, str]) -> str | None:
        receiver, _, method_name = call_name.rpartition(".")
        if not receiver or not method_name or "." in method_name:
            return None
        class_id = class_aliases.get(receiver)
        if not class_id:
            return None
        return self.known_methods.get((class_id, method_name))

    def _instance_call_target(self, call_name: str, class_aliases: dict[str, str]) -> str | None:
        if "." in call_name:
            return None
        class_id = class_aliases.get(call_name)
        if not class_id:
            return None
        return self.known_methods.get((class_id, "__call__"))

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

    def _method_reference_targets(
        self,
        receivers: dict[str, str] | tuple[str, ...],
        class_id: str | None = None,
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for receiver in receivers:
            receiver_class_id = class_id
            if receiver_class_id is None and isinstance(receivers, dict):
                receiver_class_id = receivers.get(receiver)
            if not receiver_class_id:
                continue
            for (owner_id, method_name), method_id in self.known_methods.items():
                if owner_id == receiver_class_id:
                    resolved[f"{receiver}.{method_name}"] = method_id
        return resolved

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
    function_return_classes = _project_function_return_class_ids(root, parsed_modules, class_index)
    class_bases = _project_class_base_ids(root, parsed_modules, class_index)
    method_index = _project_method_index(root, parsed_modules, class_index, class_bases)
    class_instance_aliases = _project_class_instance_aliases(
        root,
        parsed_modules,
        class_index,
        function_index,
        function_return_classes,
    )
    for path, tree in parsed_modules:
        local_functions = _local_function_ids(root, path, tree)
        local_classes = _local_class_ids(root, path, tree)
        imported_functions = _imported_function_ids(root, path, tree, function_index)
        imported_classes = _imported_class_ids(root, path, tree, class_index)
        module_function_aliases = _module_function_aliases(
            tree.body,
            {**imported_functions, **local_functions},
            reserved=set(local_functions),
            partial_names=_functools_partial_names(tree.body),
        )
        module_class_aliases = _module_class_aliases(
            tree.body,
            {**imported_classes, **local_classes},
            reserved=set(local_classes),
        )
        module_instance_aliases = _module_instance_aliases(
            tree.body,
            {**imported_classes, **module_class_aliases, **local_classes},
            _function_return_class_aliases(
                {**imported_functions, **module_function_aliases, **local_functions},
                function_return_classes,
            ),
            reserved={*imported_classes, *module_class_aliases, *local_classes},
        )
        PythonCollector(
            root,
            path,
            graph,
            local_functions,
            local_classes,
            method_index,
            class_bases,
            imported_functions,
            imported_classes,
            module_function_aliases,
            module_class_aliases,
            module_instance_aliases,
            _functools_partial_names(tree.body),
            class_instance_aliases,
            function_return_classes,
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
    _add_reexported_function_ids(root, modules, function_index)
    return function_index


def _project_class_index(root: Path, modules: list[tuple[Path, ast.Module]]) -> dict[tuple[str, str], str]:
    class_index: dict[tuple[str, str], str] = {}
    for path, tree in modules:
        module = _module_name(root, path)
        for name, class_id in _local_class_ids(root, path, tree).items():
            class_index[(module, name)] = class_id
    _add_reexported_class_ids(root, modules, class_index)
    return class_index


def _add_reexported_function_ids(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    function_index: dict[tuple[str, str], str],
) -> None:
    changed = True
    while changed:
        changed = False
        for path, tree in modules:
            module = _module_name(root, path)
            for name, function_id in _reexported_function_ids(root, path, tree, function_index).items():
                key = (module, name)
                if key not in function_index:
                    function_index[key] = function_id
                    changed = True


def _add_reexported_class_ids(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    class_index: dict[tuple[str, str], str],
) -> None:
    changed = True
    while changed:
        changed = False
        for path, tree in modules:
            module = _module_name(root, path)
            for name, class_id in _reexported_class_ids(root, path, tree, class_index).items():
                key = (module, name)
                if key not in class_index:
                    class_index[key] = class_id
                    changed = True


def _reexported_function_ids(
    root: Path,
    path: Path,
    tree: ast.Module,
    function_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    return _imported_function_ids_from_body(
        [node for node in tree.body if isinstance(node, ast.ImportFrom)],
        _module_name(root, path),
        function_index,
        current_is_package=path.name == "__init__.py",
    )


def _reexported_class_ids(
    root: Path,
    path: Path,
    tree: ast.Module,
    class_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    return _imported_class_ids_from_body(
        [node for node in tree.body if isinstance(node, ast.ImportFrom)],
        _module_name(root, path),
        class_index,
        current_is_package=path.name == "__init__.py",
    )


def _project_method_index(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    class_index: dict[tuple[str, str], str],
    class_bases: dict[str, list[str]] | None = None,
) -> dict[tuple[str, str], str]:
    method_index: dict[tuple[str, str], str] = {}
    for path, tree in modules:
        method_index.update(_local_method_ids(root, path, tree))
    if class_bases is None:
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


def _project_function_return_class_ids(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    class_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    return_classes: dict[str, str] = {}
    for path, tree in modules:
        local_functions = _local_function_ids(root, path, tree)
        local_classes = _local_class_ids(root, path, tree)
        imported_classes = _imported_class_ids(root, path, tree, class_index)
        module_class_aliases = _module_class_aliases(
            tree.body,
            {**imported_classes, **local_classes},
            reserved=set(local_classes),
        )
        known_classes = {**imported_classes, **module_class_aliases, **local_classes}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_id = local_functions.get(node.name)
            if not function_id:
                continue
            class_id = _function_return_class_id(node, known_classes)
            if class_id:
                return_classes[function_id] = class_id
    return return_classes


def _function_return_class_id(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_classes: dict[str, str],
) -> str | None:
    annotation_id = _annotation_class_id(node.returns, known_classes)
    returned_ids = _returned_class_ids(node, known_classes)
    if annotation_id:
        return annotation_id if returned_ids == {annotation_id} else None
    if len(returned_ids) == 1:
        class_id = next(iter(returned_ids))
        return class_id or None
    return None


def _annotation_class_id(node: ast.AST | None, known_classes: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return known_classes.get(node.value)
    name = _call_name(node)
    return known_classes.get(name) if name else None


def _returned_class_ids(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_classes: dict[str, str],
) -> set[str]:
    visitor = _ReturnClassVisitor(known_classes)
    for child in node.body:
        visitor.visit(child)
    return visitor.class_ids


def _function_return_class_aliases(
    known_functions: dict[str, str],
    function_return_classes: dict[str, str],
) -> dict[str, str]:
    return {
        name: class_id
        for name, function_id in known_functions.items()
        if (class_id := function_return_classes.get(function_id))
    }


def _project_class_instance_aliases(
    root: Path,
    modules: list[tuple[Path, ast.Module]],
    class_index: dict[tuple[str, str], str],
    function_index: dict[tuple[str, str], str],
    function_return_classes: dict[str, str],
) -> dict[str, dict[str, str]]:
    class_instance_aliases: dict[str, dict[str, str]] = {}
    for path, tree in modules:
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
        known_classes = {**imported_classes, **module_class_aliases, **local_classes}
        known_factory_returns = _function_return_class_aliases(
            {**imported_functions, **module_function_aliases, **local_functions},
            function_return_classes,
        )
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            class_id = local_classes.get(node.name)
            if not class_id:
                continue
            aliases = _class_instance_aliases(node, known_classes, known_factory_returns)
            if aliases:
                class_instance_aliases[class_id] = aliases
    return class_instance_aliases


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


def _unique_base_method_target(
    class_id: str,
    method_name: str,
    method_index: dict[tuple[str, str], str],
    class_bases: dict[str, list[str]],
    visiting: set[str],
) -> str | None:
    if class_id in visiting:
        return None
    visiting.add(class_id)
    matches: set[str] = set()
    for base_id in class_bases.get(class_id, []):
        method_id = method_index.get((base_id, method_name))
        if method_id:
            matches.add(method_id)
        inherited_method_id = _unique_base_method_target(
            base_id,
            method_name,
            method_index,
            class_bases,
            visiting,
        )
        if inherited_method_id:
            matches.add(inherited_method_id)
    visiting.remove(class_id)
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _imported_function_ids(
    root: Path,
    path: Path,
    tree: ast.Module,
    function_index: dict[tuple[str, str], str],
) -> dict[str, str]:
    return _imported_function_ids_from_body(
        tree.body,
        _module_name(root, path),
        function_index,
        current_is_package=path.name == "__init__.py",
    )


def _imported_function_ids_from_body(
    body: list[ast.stmt],
    current_module: str,
    function_index: dict[tuple[str, str], str],
    *,
    current_is_package: bool = False,
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for node in _scope_import_nodes(body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                local_name = alias.asname or module
                _add_module_function_aliases(imported, function_index, local_name, module)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(
                current_module,
                node.level,
                node.module,
                current_is_package=current_is_package,
            )
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
    return _imported_class_ids_from_body(
        tree.body,
        _module_name(root, path),
        class_index,
        current_is_package=path.name == "__init__.py",
    )


def _imported_class_ids_from_body(
    body: list[ast.stmt],
    current_module: str,
    class_index: dict[tuple[str, str], str],
    *,
    current_is_package: bool = False,
) -> dict[str, str]:
    imported: dict[str, str] = {}
    for node in _scope_import_nodes(body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                local_name = alias.asname or module
                _add_module_class_aliases(imported, class_index, local_name, module)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(
                current_module,
                node.level,
                node.module,
                current_is_package=current_is_package,
            )
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


class _ScopeImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


def _scope_import_nodes(body: list[ast.stmt]) -> list[ast.Import | ast.ImportFrom]:
    visitor = _ScopeImportVisitor()
    for child in body:
        visitor.visit(child)
    return visitor.imports


def _functools_partial_names(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in _scope_import_nodes(body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "functools":
                    names.add(f"{alias.asname or alias.name}.partial")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "functools":
            for alias in node.names:
                if alias.name == "partial":
                    names.add(alias.asname or alias.name)
                elif alias.name == "*":
                    names.add("partial")
    return names


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
    factory_returns: dict[str, str] | None = None,
) -> dict[str, str]:
    visitor = _ClassAliasVisitor(local_classes, factory_returns or {})
    for child in node.body:
        visitor.visit(child)

    aliases: dict[str, str] = {}
    for name, class_ids in visitor.assignments.items():
        if len(class_ids) == 1:
            class_id = next(iter(class_ids))
            if class_id:
                aliases[name] = class_id
    return aliases


def _class_instance_aliases(
    node: ast.ClassDef,
    known_classes: dict[str, str],
    factory_returns: dict[str, str] | None = None,
) -> dict[str, str]:
    visitor = _ClassAliasVisitor(known_classes, factory_returns or {})
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for statement in child.body:
                visitor.visit(statement)

    aliases: dict[str, str] = {}
    for name, class_ids in visitor.assignments.items():
        if not name.startswith(("self.", "cls.")) or len(class_ids) != 1:
            continue
        class_id = next(iter(class_ids))
        if class_id:
            aliases[name] = class_id
    return aliases


def _function_aliases(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_functions: dict[str, str],
    *,
    partial_names: set[str] | None = None,
) -> tuple[dict[str, str], set[str]]:
    visitor = _FunctionAliasVisitor(known_functions, partial_names or set())
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
    partial_names: set[str] | None = None,
) -> dict[str, str]:
    aliases, shadowed = _aliases_from_body(body, _FunctionAliasVisitor(known_functions, partial_names or set()))
    return {name: target for name, target in aliases.items() if name not in reserved and name in shadowed}


def _module_class_aliases(
    body: list[ast.stmt],
    known_classes: dict[str, str],
    *,
    reserved: set[str],
) -> dict[str, str]:
    aliases, shadowed = _aliases_from_body(body, _ClassAliasReferenceVisitor(known_classes))
    return {name: target for name, target in aliases.items() if name not in reserved and name in shadowed}


def _module_instance_aliases(
    body: list[ast.stmt],
    known_classes: dict[str, str],
    factory_returns: dict[str, str],
    *,
    reserved: set[str],
) -> dict[str, str]:
    aliases, shadowed = _aliases_from_body(body, _ClassAliasVisitor(known_classes, factory_returns))
    return {name: target for name, target in aliases.items() if name not in reserved and name in shadowed}


def _aliases_from_body(
    body: list[ast.stmt],
    visitor: _FunctionAliasVisitor | _ClassAliasVisitor | _ClassAliasReferenceVisitor,
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
    def __init__(self, local_classes: dict[str, str], factory_returns: dict[str, str] | None = None) -> None:
        self.local_classes = local_classes
        self.factory_returns = factory_returns or {}
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
        for target in node.targets:
            for assignment_target, assignment_value in _assignment_target_values(target, node.value):
                class_id = (
                    _class_instantiation_target(assignment_value, self.local_classes, self.factory_returns)
                    if assignment_value is not None
                    else None
                )
                self._record(assignment_target, class_id)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        class_id = (
            _class_instantiation_target(node.value, self.local_classes, self.factory_returns)
            if node.value
            else None
        )
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
    def __init__(self, known_functions: dict[str, str], partial_names: set[str] | None = None) -> None:
        self.known_functions = known_functions
        self.partial_names = partial_names or set()
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
        for target in node.targets:
            for assignment_target, assignment_value in _assignment_target_values(target, node.value):
                function_id = self._reference_target(assignment_value) if assignment_value is not None else None
                self._record(assignment_target, function_id)
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
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in self.partial_names and node.args:
                return self._reference_target(node.args[0])
        call_name = _call_name(node)
        if not call_name:
            return None
        if call_name in self.assignments:
            assigned_ids = self.assignments[call_name]
            if len(assigned_ids) == 1:
                return next(iter(assigned_ids))
            return None
        return self.known_functions.get(call_name)


class _ClassAliasReferenceVisitor(_FunctionAliasVisitor):
    def __init__(self, known_classes: dict[str, str]) -> None:
        self.known_classes = known_classes
        self.assignments: dict[str, set[str | None]] = {}

    def _reference_target(self, node: ast.AST) -> str | None:
        call_name = _call_name(node)
        if not call_name:
            return None
        if call_name in self.assignments:
            assigned_ids = self.assignments[call_name]
            if len(assigned_ids) == 1:
                return next(iter(assigned_ids))
            return None
        return self.known_classes.get(call_name)


class _ReturnClassVisitor(ast.NodeVisitor):
    def __init__(self, known_classes: dict[str, str]) -> None:
        self.known_classes = known_classes
        self.class_ids: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is None:
            self.class_ids.add("")
            return
        class_id = _class_instantiation_target(node.value, self.known_classes)
        self.class_ids.add(class_id or "")
        self.generic_visit(node.value)


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
    if isinstance(node, ast.Attribute):
        name = _call_name(node)
        return [name] if name else []
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_target_names(element))
        return names
    return []


def _assignment_target_values(target: ast.AST, value: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
    if isinstance(target, (ast.Tuple, ast.List)):
        if not isinstance(value, (ast.Tuple, ast.List)) or len(target.elts) != len(value.elts):
            return [(target, None)]
        pairs: list[tuple[ast.AST, ast.AST | None]] = []
        for target_element, value_element in zip(target.elts, value.elts):
            pairs.extend(_assignment_target_values(target_element, value_element))
        return pairs
    return [(target, value)]


def _class_instantiation_target(
    node: ast.AST,
    local_classes: dict[str, str],
    factory_returns: dict[str, str] | None = None,
) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    call_name = _call_name(node.func)
    if not call_name:
        return None
    return local_classes.get(call_name) or (factory_returns or {}).get(call_name)


def _is_super_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return isinstance(node.func, ast.Name) and node.func.id == "super"


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


def _resolve_import_from_module(
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


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None
