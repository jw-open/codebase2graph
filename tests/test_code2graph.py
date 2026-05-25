from __future__ import annotations

import json
from pathlib import Path

from code2graph.builder import build_graph
from code2graph.cli import main
from code2graph.iterate import main as iterate_main
from code2graph import loop
from code2graph.prompt import build_iteration_prompt, summarize_graph


def test_folder_graph_shape(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    graph = build_graph(tmp_path, "folder").to_dict()

    assert graph["current_node_id"] == "repo"
    assert any(node["id"] == "folder:src" for node in graph["nodes"])
    assert any(edge["from"] == "folder:src" and edge["to"] == "file:src/app.py" for edge in graph["edges"])


def test_python_call_graph(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        """
def helper():
    return 1

def main():
    helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    labels = {node["label"] for node in graph["nodes"]}
    assert {"helper", "main"}.issubset(labels)
    assert any(edge["label"] == "calls" and edge["to"] == "py:function:app.py:helper" for edge in graph["edges"])


def test_typescript_call_graph(tmp_path: Path) -> None:
    source = tmp_path / "app.ts"
    source.write_text(
        """
export function helper() {
  return 1;
}

export const main = () => {
  helper();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(node["id"] == "js:function:app.ts:main" for node in graph["nodes"])
    assert any(edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:app.ts:helper" for edge in graph["edges"])


def test_javascript_function_expression_and_single_param_arrow_calls_resolve(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text(
        """
const normalize = value => value.trim();
const render = function (text) {
  return normalize(text);
};

export const main = input => render(input);
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(node["id"] == "js:function:app.js:normalize" for node in graph["nodes"])
    assert any(node["id"] == "js:function:app.js:render" for node in graph["nodes"])
    assert any(node["id"] == "js:function:app.js:main" for node in graph["nodes"])
    assert any(
        edge["from"] == "js:function:app.js:render" and edge["to"] == "js:function:app.js:normalize"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:app.js:render"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:normalize", "js:call:render"} for node in graph["nodes"])


def test_typescript_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.ts").write_text(
        """
export function direct() {
  return 1;
}

export function qualified() {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        """
import { direct as renamed } from "./helpers";
import * as helpers from "./helpers";

export function main() {
  renamed();
  helpers.qualified();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:qualified"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:renamed", "js:call:helpers.qualified"} for node in graph["nodes"])


def test_typescript_destructured_namespace_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.ts").write_text(
        """
export function direct() {
  return 1;
}

export function qualified() {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        """
import * as helpers from "./helpers";

export function main() {
  const { direct, qualified: renamed } = helpers;
  direct();
  renamed();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:qualified"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:direct", "js:call:renamed"} for node in graph["nodes"])


def test_typescript_default_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.ts").write_text(
        """
export default function run() {
  return 1;
}

export function direct() {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        """
import execute, { direct } from "./helpers";

export function main() {
  execute();
  direct();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:run"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:direct"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "js:call:execute" for node in graph["nodes"])


def test_typescript_anonymous_default_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "arrow.ts").write_text(
        """
export default () => {
  return 1;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "function.ts").write_text(
        """
export default function () {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        """
import runArrow from "./arrow";
import runFunction from "./function";

export function main() {
  runArrow();
  runFunction();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(node["id"] == "js:function:arrow.ts:default" for node in graph["nodes"])
    assert any(node["id"] == "js:function:function.ts:default" for node in graph["nodes"])
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:arrow.ts:default"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:function.ts:default"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:runArrow", "js:call:runFunction"} for node in graph["nodes"])


def test_typescript_reexported_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.ts").write_text(
        """
export function direct() {
  return 1;
}

export default function run() {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text(
        """
export * from "./helpers";
export { direct as renamed, default as execute } from "./helpers";
""",
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        """
import { direct, renamed, execute } from "./index";

export function main() {
  direct();
  renamed();
  execute();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:run"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:direct", "js:call:renamed", "js:call:execute"} for node in graph["nodes"])


def test_typescript_local_export_list_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.ts").write_text(
        """
export function direct() {
  return 1;
}

export function renamedSource() {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text(
        """
import { direct, renamedSource } from "./helpers";

export { direct, renamedSource as renamed };
""",
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        """
import { direct, renamed } from "./index";

export function main() {
  direct();
  renamed();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.ts:main" and edge["to"] == "js:function:helpers.ts:renamedSource"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:direct", "js:call:renamed"} for node in graph["nodes"])


def test_javascript_require_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.js").write_text(
        """
function direct() {
  return 1;
}

function qualified() {
  return 2;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        """
const { direct: directAlias } = require("./helpers");
const helpers = require("./helpers");

function main() {
  directAlias();
  helpers.qualified();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:helpers.js:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:helpers.js:qualified"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:directAlias", "js:call:helpers.qualified"} for node in graph["nodes"])


def test_javascript_commonjs_export_alias_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.js").write_text(
        """
function internal() {
  return 1;
}

function objectInternal() {
  return 2;
}

exports.direct = internal;
module.exports.objectAlias = objectInternal;
""",
        encoding="utf-8",
    )
    (tmp_path / "object.js").write_text(
        """
function objectInternal() {
  return 1;
}

module.exports = { objectDirect: objectInternal };
""",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        """
const { direct, objectAlias } = require("./helpers");
const helpers = require("./helpers");
const { objectDirect } = require("./object");

function main() {
  direct();
  objectAlias();
  objectDirect();
  helpers.direct();
  helpers.objectAlias();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:helpers.js:internal"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:helpers.js:objectInternal"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:object.js:objectInternal"
        for edge in graph["edges"]
    )
    assert not any(
        node["id"]
        in {
            "js:call:direct",
            "js:call:objectAlias",
            "js:call:objectDirect",
            "js:call:helpers.direct",
            "js:call:helpers.objectAlias",
        }
        for node in graph["nodes"]
    )


def test_javascript_commonjs_default_export_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.js").write_text(
        """
function internal() {
  return 1;
}

module.exports = internal;
""",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        """
const run = require("./helpers");

function main() {
  run();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:helpers.js:internal"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "js:call:run" for node in graph["nodes"])


def test_javascript_function_alias_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.js").write_text(
        """
function imported() {
  return 1;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        """
const { imported } = require("./helpers");

function local() {
  return 2;
}

function main() {
  const localAlias = local;
  const importedAlias = imported;
  localAlias();
  importedAlias();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:app.js:local"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:helpers.js:imported"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:localAlias", "js:call:importedAlias"} for node in graph["nodes"])


def test_javascript_ambiguous_function_alias_calls_remain_placeholders(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text(
        """
function first() {
  return 1;
}

function second() {
  return 2;
}

function main(first, flag) {
  const paramAlias = first;
  let alias = first;
  if (flag) {
    alias = second;
  }
  first();
  paramAlias();
  alias();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(edge["from"] == "js:function:app.js:main" and edge["to"] == "js:call:first" for edge in graph["edges"])
    assert any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:call:paramAlias"
        for edge in graph["edges"]
    )
    assert any(edge["from"] == "js:function:app.js:main" and edge["to"] == "js:call:alias" for edge in graph["edges"])
    assert not any(
        edge["from"] == "js:function:app.js:main" and edge["to"] == "js:function:app.js:first"
        for edge in graph["edges"]
    )


def test_typescript_this_method_calls_resolve_to_same_file_methods(tmp_path: Path) -> None:
    (tmp_path / "service.ts").write_text(
        """
class Service {
  helper() {
    return 1;
  }

  main() {
    return this.helper();
  }
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:service.ts:main"
        and edge["to"] == "js:function:service.ts:helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "js:call:this.helper" for node in graph["nodes"])


def test_typescript_local_instance_method_calls_resolve_to_same_file_methods(tmp_path: Path) -> None:
    (tmp_path / "service.ts").write_text(
        """
class Service {
  helper() {
    return 1;
  }
}

export function main() {
  const service = new Service();
  return service.helper() + Service.helper();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:service.ts:main"
        and edge["to"] == "js:function:service.ts:helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"js:call:service.helper", "js:call:Service.helper"} for node in graph["nodes"])


def test_typescript_object_literal_method_calls_resolve_to_same_file_methods(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text(
        """
const actions = {
  save() {
    return 1;
  },
};

export function main() {
  return actions.save();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main"
        and edge["to"] == "js:function:app.ts:save"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "js:call:actions.save" for node in graph["nodes"])


def test_typescript_destructured_object_method_calls_resolve_to_same_file_methods(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text(
        """
const actions = {
  save() {
    return 1;
  },
};

export function main() {
  const { save: persist } = actions;
  return persist();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:app.ts:main"
        and edge["to"] == "js:function:app.ts:save"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "js:call:persist" for node in graph["nodes"])


def test_typescript_reassigned_instance_method_calls_remain_placeholders(tmp_path: Path) -> None:
    (tmp_path / "service.ts").write_text(
        """
class First {
  helper() {
    return 1;
  }
}

class Second {
  helper() {
    return 2;
  }
}

export function main(flag: boolean) {
  let service = new First();
  if (flag) {
    service = new Second();
  }
  return service.helper();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "js:function:service.ts:main" and edge["to"] == "js:call:service.helper"
        for edge in graph["edges"]
    )
    assert not any(
        edge["from"] == "js:function:service.ts:main"
        and edge["to"] in {
            "js:function:service.ts:First.helper",
            "js:function:service.ts:Second.helper",
            "js:function:service.ts:helper",
        }
        for edge in graph["edges"]
    )


def test_go_call_graph_resolves_same_package_and_local_imports(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "helper.go").write_text(
        """
package pkg

func Helper() int {
    return 1
}
""",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        """
package main

import "example.com/app/pkg"

func local() int {
    return 2
}

func main() {
    local()
    pkg.Helper()
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "go:function:main.go:main" and edge["to"] == "go:function:main.go:local"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "go:function:main.go:main" and edge["to"] == "go:function:pkg/helper.go:Helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"go:call:local", "go:call:pkg.Helper"} for node in graph["nodes"])


def test_go_call_graph_resolves_receiver_and_instance_methods(tmp_path: Path) -> None:
    (tmp_path / "service.go").write_text(
        """
package service

type Service struct{}

func (s *Service) Helper() int {
    return 1
}

func (s *Service) Run() int {
    return s.Helper()
}

func main() {
    svc := Service{}
    svc.Helper()
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "go:method:service.go:Service.Run"
        and edge["to"] == "go:method:service.go:Service.Helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "go:function:service.go:main"
        and edge["to"] == "go:method:service.go:Service.Helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"go:call:s.Helper", "go:call:svc.Helper"} for node in graph["nodes"])


def test_go_call_graph_resolves_imported_package_instance_methods(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "service.go").write_text(
        """
package pkg

type Service struct{}

func (s *Service) Helper() int {
    return 1
}
""",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        """
package main

import (
    "example.com/app/pkg"
    . "example.com/app/pkg"
)

func main() {
    svc := pkg.Service{}
    dot := Service{}
    svc.Helper()
    dot.Helper()
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "go:function:main.go:main"
        and edge["to"] == "go:method:pkg/service.go:Service.Helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"go:call:svc.Helper", "go:call:dot.Helper"} for node in graph["nodes"])


def test_java_call_graph_resolves_same_class_and_instance_methods(tmp_path: Path) -> None:
    (tmp_path / "Service.java").write_text(
        """
class Service {
    int helper() {
        return 1;
    }

    int run() {
        return this.helper();
    }
}
""",
        encoding="utf-8",
    )
    (tmp_path / "App.java").write_text(
        """
class App {
    void local() {
    }

    void main() {
        local();
        Service service = new Service();
        service.helper();
        missing();
    }
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "java:method:Service.java:Service.run"
        and edge["to"] == "java:method:Service.java:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "java:method:App.java:App.main" and edge["to"] == "java:method:App.java:App.local"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "java:method:App.java:App.main"
        and edge["to"] == "java:method:Service.java:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "java:method:App.java:App.main" and edge["to"] == "java:call:missing"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"java:call:this.helper", "java:call:local", "java:call:service.helper"} for node in graph["nodes"])


def test_rust_call_graph_resolves_functions_and_impl_methods(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text(
        """
fn helper() -> i32 {
    1
}

struct Service;

impl Service {
    fn helper(&self) -> i32 {
        helper()
    }

    fn run(&self) -> i32 {
        self.helper()
    }
}

fn main() {
    helper();
    let service = Service::new();
    service.helper();
    missing();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "rust:method:main.rs:Service.helper"
        and edge["to"] == "rust:function:main.rs:helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "rust:method:main.rs:Service.run"
        and edge["to"] == "rust:method:main.rs:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "rust:function:main.rs:main"
        and edge["to"] == "rust:function:main.rs:helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "rust:function:main.rs:main"
        and edge["to"] == "rust:method:main.rs:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "rust:function:main.rs:main" and edge["to"] == "rust:call:missing"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"rust:call:helper", "rust:call:self.helper", "rust:call:service.helper"} for node in graph["nodes"])


def test_rust_call_graph_resolves_local_module_imports(tmp_path: Path) -> None:
    (tmp_path / "helpers.rs").write_text(
        """
pub fn direct() -> i32 {
    1
}

pub fn qualified() -> i32 {
    2
}

pub fn renamed_source() -> i32 {
    3
}
""",
        encoding="utf-8",
    )
    (tmp_path / "main.rs").write_text(
        """
mod helpers;

use helpers::{direct, renamed_source as renamed};

fn main() {
    direct();
    renamed();
    helpers::qualified();
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "rust:function:main.rs:main"
        and edge["to"] == "rust:function:helpers.rs:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "rust:function:main.rs:main"
        and edge["to"] == "rust:function:helpers.rs:renamed_source"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "rust:function:main.rs:main"
        and edge["to"] == "rust:function:helpers.rs:qualified"
        for edge in graph["edges"]
    )
    assert not any(
        node["id"] in {"rust:call:direct", "rust:call:renamed", "rust:call:helpers::qualified"}
        for node in graph["nodes"]
    )


def test_rust_entity_graph_defines_types_functions_and_methods(tmp_path: Path) -> None:
    (tmp_path / "lib.rs").write_text(
        """
pub struct Service;
pub enum Mode { Fast }
pub trait Runner {
    fn run(&self);
}

pub fn build() -> Service {
    Service
}

impl Service {
    pub fn run(&self) {}
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "entity").to_dict()

    assert any(node["id"] == "rust:entity:lib.rs:Service" for node in graph["nodes"])
    assert any(node["id"] == "rust:entity:lib.rs:Mode" for node in graph["nodes"])
    assert any(node["id"] == "rust:entity:lib.rs:Runner" for node in graph["nodes"])
    assert any(node["id"] == "rust:function:lib.rs:build" for node in graph["nodes"])
    assert any(node["id"] == "rust:method:lib.rs:Service.run" for node in graph["nodes"])


def test_unresolved_calls_remain_placeholder_targets(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        """
def main():
    external()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(edge["from"] == "py:function:app.py:main" and edge["to"] == "py:call:external" for edge in graph["edges"])


def test_python_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        """
def direct():
    return 1

def qualified():
    return 2
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
import helpers
from helpers import direct

def main():
    direct()
    helpers.qualified()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:qualified"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:direct" for node in graph["nodes"])
    assert not any(node["id"] == "py:call:helpers.qualified" for node in graph["nodes"])


def test_python_package_imported_calls_resolve_to_nested_modules(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        """
def run():
    return 1

class Worker:
    def helper(self):
        return run()
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
import pkg

def main():
    worker = pkg.service.Worker()
    pkg.service.run()
    worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:pkg/service.py:run"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:pkg/service.py:Worker"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main"
        and edge["to"] == "py:method:pkg/service.py:Worker.helper"
        for edge in graph["edges"]
    )
    assert not any(
        node["id"] in {"py:call:pkg.service.run", "py:call:pkg.service.Worker", "py:call:worker.helper"}
        for node in graph["nodes"]
    )


def test_python_package_reexported_calls_resolve_to_source_modules(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        """
from .service import Service, run
""",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        """
def run():
    return 1

class Service:
    def helper(self):
        return run()
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from pkg import Service, run

def main():
    worker = Service()
    run()
    worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:pkg/service.py:run"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:pkg/service.py:Service"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main"
        and edge["to"] == "py:method:pkg/service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:run", "py:call:Service", "py:call:worker.helper"} for node in graph["nodes"])


def test_python_star_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        """
def direct():
    return 1

class Service:
    def helper(self):
        return direct()
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from helpers import *

def main():
    worker = Service()
    direct()
    worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:helpers.py:Service"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:method:helpers.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:Service", "py:call:direct", "py:call:worker.helper"} for node in graph["nodes"])


def test_python_function_local_imported_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        """
def direct():
    return 1

def qualified():
    return 2
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
def main():
    import helpers as tools
    from helpers import direct as renamed
    renamed()
    tools.qualified()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:direct"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:qualified"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:renamed", "py:call:tools.qualified"} for node in graph["nodes"])


def test_python_function_alias_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        """
def imported():
    return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from helpers import imported

def local():
    return 2

def main():
    local_alias = local
    imported_alias = imported
    local_alias()
    imported_alias()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:app.py:local"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:imported"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:local_alias", "py:call:imported_alias"} for node in graph["nodes"])


def test_python_module_function_alias_calls_resolve_to_project_functions(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        """
def imported():
    return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from helpers import imported

def local():
    return 2

local_alias = local
imported_alias = imported

def main():
    local_alias()
    imported_alias()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:app.py:local"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:imported"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:local_alias", "py:call:imported_alias"} for node in graph["nodes"])


def test_python_local_functions_shadow_imported_function_aliases(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        """
def helper():
    return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from helpers import helper

def helper():
    return 2

def main():
    alias = helper
    alias()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:app.py:helper"
        for edge in graph["edges"]
    )
    assert not any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:helpers.py:helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:alias" for node in graph["nodes"])


def test_python_ambiguous_function_alias_calls_remain_placeholders(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        """
def first():
    return 1

def second():
    return 2

def main(flag, first):
    param_alias = first
    alias = first
    if flag:
        alias = second
    first()
    param_alias()
    alias()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(edge["from"] == "py:function:app.py:main" and edge["to"] == "py:call:first" for edge in graph["edges"])
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:call:param_alias" for edge in graph["edges"]
    )
    assert any(edge["from"] == "py:function:app.py:main" and edge["to"] == "py:call:alias" for edge in graph["edges"])
    assert not any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:function:app.py:first"
        for edge in graph["edges"]
    )


def test_python_same_class_method_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1

    def main(self):
        return self.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:method:service.py:Service.main"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:self.helper" for node in graph["nodes"])


def test_python_local_instance_method_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1

def main():
    service = Service()
    return service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:service.py:main"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:service.helper" for node in graph["nodes"])


def test_python_attribute_instance_method_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1

class Controller:
    def main(self):
        self.service = Service()
        return self.service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:method:service.py:Controller.main"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:self.service.helper" for node in graph["nodes"])


def test_python_bound_method_alias_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1

    @staticmethod
    def build():
        return 2

def main():
    service = Service()
    instance_handler = service.helper
    class_handler = Service.build
    instance_handler()
    class_handler()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:service.py:main"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:service.py:main"
        and edge["to"] == "py:method:service.py:Service.build"
        for edge in graph["edges"]
    )
    assert not any(
        node["id"] in {"py:call:instance_handler", "py:call:class_handler"}
        for node in graph["nodes"]
    )


def test_python_inherited_instance_method_calls_resolve_to_base_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Base:
    def helper(self):
        return 1

class Child(Base):
    pass

def main():
    service = Child()
    return service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:service.py:main"
        and edge["to"] == "py:method:service.py:Base.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:service.helper" for node in graph["nodes"])


def test_python_imported_inherited_method_calls_resolve_to_base_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Base:
    def helper(self):
        return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from service import Base

class Child(Base):
    pass

def main():
    service = Child()
    return service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main"
        and edge["to"] == "py:method:service.py:Base.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:service.helper" for node in graph["nodes"])


def test_python_super_method_calls_resolve_to_base_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Base:
    def helper(self):
        return 1

class Child(Base):
    def helper(self):
        return super().helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:method:service.py:Child.helper"
        and edge["to"] == "py:method:service.py:Base.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:helper" for node in graph["nodes"])


def test_python_imported_instance_method_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
import service
from service import Service

def direct():
    worker = Service()
    return worker.helper()

def qualified():
    worker = service.Service()
    return worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:direct"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:qualified"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:worker.helper" for node in graph["nodes"])


def test_python_module_class_alias_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from service import Service

Worker = Service

def direct():
    Worker()

def method():
    worker = Worker()
    return worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:direct" and edge["to"] == "py:class:service.py:Service"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:method"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:Worker", "py:call:worker.helper"} for node in graph["nodes"])


def test_python_module_instance_method_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
import service
from service import Service

direct_worker = Service()
qualified_worker = service.Service()

def direct():
    return direct_worker.helper()

def qualified():
    return qualified_worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:direct"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:qualified"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(
        node["id"] in {"py:call:direct_worker.helper", "py:call:qualified_worker.helper"}
        for node in graph["nodes"]
    )


def test_python_reassigned_module_instance_calls_remain_placeholder_targets(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class First:
    def helper(self):
        return 1

class Second:
    def helper(self):
        return 2

worker = First()
worker = Second()

def main():
    return worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:service.py:main"
        and edge["to"] == "py:call:worker.helper"
        for edge in graph["edges"]
    )
    assert not any(
        edge["from"] == "py:function:service.py:main"
        and edge["to"] in {
            "py:method:service.py:First.helper",
            "py:method:service.py:Second.helper",
        }
        for edge in graph["edges"]
    )


def test_python_function_local_imported_class_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    def helper(self):
        return 1
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
def direct():
    from service import Service
    worker = Service()
    return worker.helper()

def qualified():
    import service
    worker = service.Service()
    return worker.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:direct"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:qualified"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:worker.helper" for node in graph["nodes"])


def test_python_constructor_calls_resolve_to_classes(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    pass
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
import service
from service import Service

class Local:
    pass

def main():
    Local()
    Service()
    service.Service()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:app.py:Local"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:service.py:Service"
        for edge in graph["edges"]
    )
    assert not any(
        node["id"] in {"py:call:Local", "py:call:Service", "py:call:service.Service"}
        for node in graph["nodes"]
    )


def test_python_local_classes_shadow_imported_classes(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    pass
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
from service import Service

class Service:
    pass

def main():
    Service()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:app.py:Service"
        for edge in graph["edges"]
    )
    assert not any(
        edge["from"] == "py:function:app.py:main" and edge["to"] == "py:class:service.py:Service"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:Service" for node in graph["nodes"])


def test_python_class_qualified_method_calls_resolve_to_methods(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class Service:
    @staticmethod
    def helper():
        return 1

def local():
    return Service.helper()
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
import service
from service import Service

def direct():
    return Service.helper()

def qualified():
    return service.Service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:service.py:local"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:direct"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:qualified"
        and edge["to"] == "py:method:service.py:Service.helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] in {"py:call:Service.helper", "py:call:service.Service.helper"} for node in graph["nodes"])


def test_python_reassigned_instance_calls_remain_placeholder_targets(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class First:
    def helper(self):
        return 1

class Second:
    def helper(self):
        return 2

def main(flag):
    service = First()
    if flag:
        service = Second()
    return service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:service.py:main" and edge["to"] == "py:call:service.helper"
        for edge in graph["edges"]
    )


def test_python_reassigned_attribute_instance_calls_remain_placeholder_targets(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """
class First:
    def helper(self):
        return 1

class Second:
    def helper(self):
        return 2

class Controller:
    def main(self, flag):
        self.service = First()
        if flag:
            self.service = Second()
        return self.service.helper()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:method:service.py:Controller.main"
        and edge["to"] == "py:call:self.service.helper"
        for edge in graph["edges"]
    )
    assert not any(
        edge["from"] == "py:method:service.py:Controller.main"
        and edge["to"] in {
            "py:method:service.py:First.helper",
            "py:method:service.py:Second.helper",
        }
        for edge in graph["edges"]
    )


def test_python_nested_function_calls_stay_in_lexical_scope(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        """
def top_helper():
    return 1

def outer():
    def inner():
        return top_helper()

    return inner()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:outer"
        and edge["to"] == "py:function:app.py:outer.inner"
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] == "py:function:app.py:outer.inner"
        and edge["to"] == "py:function:app.py:top_helper"
        for edge in graph["edges"]
    )
    assert not any(
        edge["from"] == "py:function:app.py:outer"
        and edge["to"] == "py:function:app.py:top_helper"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:inner" for node in graph["nodes"])


def test_python_nested_sibling_function_calls_resolve_in_enclosing_scope(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        """
def outer():
    def first():
        return second()

    def second():
        return 1

    return first()
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "call").to_dict()

    assert any(
        edge["from"] == "py:function:app.py:outer.first"
        and edge["to"] == "py:function:app.py:outer.second"
        for edge in graph["edges"]
    )
    assert not any(node["id"] == "py:call:second" for node in graph["nodes"])


def test_python_entity_imports_resolve_to_local_files(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worker.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (package / "service.py").write_text("from . import worker\n", encoding="utf-8")
    (tmp_path / "helpers.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        """
import helpers
from pkg import service
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "entity").to_dict()

    assert any(edge["from"] == "file:app.py" and edge["to"] == "file:helpers.py" for edge in graph["edges"])
    assert any(edge["from"] == "file:app.py" and edge["to"] == "file:pkg/service.py" for edge in graph["edges"])
    assert any(edge["from"] == "file:pkg/service.py" and edge["to"] == "file:pkg/worker.py" for edge in graph["edges"])
    assert any(node["id"] == "import:python:helpers" for node in graph["nodes"])


def test_javascript_entity_imports_resolve_to_local_files(tmp_path: Path) -> None:
    (tmp_path / "helpers.ts").write_text("export function helper() {}\n", encoding="utf-8")
    (tmp_path / "setup.ts").write_text("export const loaded = true;\n", encoding="utf-8")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "index.ts").write_text("export function fromIndex() {}\n", encoding="utf-8")
    (package / "worker.js").write_text("exports.run = () => 1;\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text(
        """
import { helper } from "./helpers";
import "./setup";
import * as pkg from "./pkg";
const worker = require("./pkg/worker");
import react from "react";
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "entity").to_dict()

    assert any(edge["from"] == "file:app.ts" and edge["to"] == "file:helpers.ts" for edge in graph["edges"])
    assert any(edge["from"] == "file:app.ts" and edge["to"] == "file:setup.ts" for edge in graph["edges"])
    assert any(edge["from"] == "file:app.ts" and edge["to"] == "file:pkg/index.ts" for edge in graph["edges"])
    assert any(edge["from"] == "file:app.ts" and edge["to"] == "file:pkg/worker.js" for edge in graph["edges"])
    assert any(node["id"] == "import:typescript:react" for node in graph["nodes"])
    assert any(edge["from"] == "file:app.ts" and edge["to"] == "import:typescript:react" for edge in graph["edges"])


def test_go_entity_graph_extracts_entities_and_resolves_local_imports(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    service = tmp_path / "service"
    service.mkdir()
    (service / "service.go").write_text(
        """
package service

type Service struct{}

func Run() int {
    return 1
}

func (s *Service) Helper() int {
    return Run()
}
""",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        """
package main

import "example.com/app/service"

func main() {
    service.Run()
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "entity").to_dict()

    assert any(node["id"] == "go:entity:service/service.go:Service" for node in graph["nodes"])
    assert any(node["id"] == "go:function:service/service.go:Run" for node in graph["nodes"])
    assert any(node["id"] == "go:method:service/service.go:Service.Helper" for node in graph["nodes"])
    assert any(edge["from"] == "file:main.go" and edge["to"] == "file:service/service.go" for edge in graph["edges"])
    assert any(node["id"] == "import:go:example.com/app/service" for node in graph["nodes"])


def test_schema_graph_from_sql(tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text(
        """
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL
);
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "schema").to_dict()

    assert any(node["id"] == "db:table:users" for node in graph["nodes"])
    assert any(node["id"] == "db:column:users.email" for node in graph["nodes"])


def test_cli_writes_ohwise_graph_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc --noEmit"}}),
        encoding="utf-8",
    )
    output = tmp_path / "graph.json"

    code = main([str(tmp_path), "--graph", "workflow", "--output", str(output)])

    assert code == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert set(data) == {"nodes", "edges", "current_node_id"}
    assert any(node["id"] == "workflow:npm:build" for node in data["nodes"])


def test_cli_updates_existing_graph_and_removes_stale_nodes(tmp_path: Path) -> None:
    (tmp_path / "old.py").write_text("def old():\n    return 1\n", encoding="utf-8")
    graph_path = tmp_path / "graph.json"

    assert main([str(tmp_path), "--graph", "folder", "--output", str(graph_path)]) == 0
    initial = json.loads(graph_path.read_text(encoding="utf-8"))
    for node in initial["nodes"]:
        if node["id"] == "file:old.py":
            node["attributes"]["owner_note"] = "keep this if file survives"
    graph_path.write_text(json.dumps(initial), encoding="utf-8")

    (tmp_path / "old.py").unlink()
    (tmp_path / "new.py").write_text("def new():\n    return 2\n", encoding="utf-8")
    summary_path = tmp_path / "update-summary.json"

    code = main(
        [
            str(tmp_path),
            "--graph",
            "folder",
            "--update-existing",
            str(graph_path),
            "--update-summary-output",
            str(summary_path),
        ]
    )

    assert code == 0
    updated = json.loads(graph_path.read_text(encoding="utf-8"))
    node_ids = {node["id"] for node in updated["nodes"]}
    assert "file:new.py" in node_ids
    assert "file:old.py" not in node_ids
    assert all(edge["from"] in node_ids and edge["to"] in node_ids for edge in updated["edges"])

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["added_nodes"] >= 1
    assert summary["removed_nodes"] >= 1


def test_cli_update_preserves_existing_custom_node_attributes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    graph_path = tmp_path / "graph.json"

    assert main([str(tmp_path), "--graph", "folder", "--output", str(graph_path)]) == 0
    existing = json.loads(graph_path.read_text(encoding="utf-8"))
    for node in existing["nodes"]:
        if node["id"] == "file:app.py":
            node["attributes"]["review_status"] = "approved"
    graph_path.write_text(json.dumps(existing), encoding="utf-8")

    assert main([str(tmp_path), "--graph", "folder", "--update-existing", str(graph_path)]) == 0

    updated = json.loads(graph_path.read_text(encoding="utf-8"))
    app_node = next(node for node in updated["nodes"] if node["id"] == "file:app.py")
    assert app_node["attributes"]["kind"] == "file"
    assert app_node["attributes"]["review_status"] == "approved"


def test_graph_summary_reports_entrypoints_hotspots_and_isolated_modules(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "python app.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
def helper():
    return 1

def main():
    return helper()

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text("context only\n", encoding="utf-8")

    summary = summarize_graph(build_graph(tmp_path, "all").to_dict())

    assert any(node["id"] == "workflow:npm:start" for node in summary["entrypoints"])
    assert any(node["id"] == "workflow:python:app.py" for node in summary["entrypoints"])
    assert any(node["id"] == "py:function:app.py:helper" for node in summary["high_fan_in"])
    assert any(node["id"] == "file:notes.md" for node in summary["isolated_modules"])


def test_cli_writes_graph_summary_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc --noEmit"}}),
        encoding="utf-8",
    )
    output = tmp_path / "graph.json"
    summary_output = tmp_path / "summary.json"

    code = main([str(tmp_path), "--graph", "all", "--output", str(output), "--summary-output", str(summary_output)])

    assert code == 0
    summary = json.loads(summary_output.read_text(encoding="utf-8"))
    assert "high_fan_in" in summary
    assert "high_fan_out" in summary
    assert any(node["id"] == "workflow:npm:build" for node in summary["entrypoints"])


def test_infra_graph_from_compose_ci_and_cloud_files(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        """
services:
  api:
    ports:
      - "8000:8000"
    depends_on:
      - db
    environment:
      STRIPE_API_URL: https://api.stripe.com
  db:
    image: postgres:16
""",
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "deploy.yml").write_text(
        """
jobs:
  build:
    runs-on: ubuntu-latest
  deploy:
    runs-on: ubuntu-latest
""",
        encoding="utf-8",
    )
    (tmp_path / "main.tf").write_text(
        """
provider "aws" {}
resource "aws_lambda_function" "worker" {
  runtime = "python3.12"
}
resource "aws_s3_bucket" "assets" {
  bucket = "assets-prod"
}
resource "aws_eks_cluster" "app" {
  version = "1.30"
}
""",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"next": "latest"}}), encoding="utf-8")

    graph = build_graph(tmp_path, "infra").to_dict()

    assert any(node["id"] == "infra:service:api" for node in graph["nodes"])
    assert any(edge["from"] == "infra:service:api" and edge["to"] == "infra:service:db" and edge["label"] == "depends_on" for edge in graph["edges"])
    assert any(edge["from"] == "infra:service:api" and edge["to"] == "infra:integration:api.stripe.com" for edge in graph["edges"])
    assert any(node["id"] == "infra:pipeline:.github/workflows/deploy.yml" for node in graph["nodes"])
    assert any(node["id"] == "infra:ci_job:.github/workflows/deploy.yml:deploy" for node in graph["nodes"])
    assert any(node["id"] == "infra:cloud:aws" for node in graph["nodes"])
    assert any(
        node["id"] == "infra:cloud_service:aws:s3"
        for node in graph["nodes"]
    )
    assert any(
        node["id"] == "infra:cloud_resource:aws:eks_cluster:app"
        and node["attributes"].get("cloud_service") == "eks"
        and node["attributes"].get("config_version") == "1.30"
        for node in graph["nodes"]
    )
    assert any(
        node["id"] == "infra:service:api"
        and node["attributes"].get("deployment") == "local"
        and node["attributes"].get("runtime") == "docker_compose"
        for node in graph["nodes"]
    )
    assert any(node["id"] == "infra:dependency:npm:next" for node in graph["nodes"])


def test_security_graph_detects_risks_without_secret_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        """
OPENAI_API_KEY=sk-test-secret
INTERNAL_URL=http://api.example.com
""",
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile").write_text(
        """
FROM python:latest
RUN echo ok
""",
        encoding="utf-8",
    )
    (tmp_path / "main.tf").write_text(
        """
resource "aws_s3_bucket" "assets" {
  acl = "public-read"
}
resource "aws_iam_policy" "wide" {
  policy = jsonencode({ Action = "*", Resource = "*" })
}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "security").to_dict()

    risks = [node for node in graph["nodes"] if node["attributes"].get("kind") == "security_risk"]
    risk_types = {node["attributes"]["risk_type"] for node in risks}
    assert {"hardcoded_secret", "plaintext_http", "unpinned_image", "public_storage", "wildcard_iam"}.issubset(risk_types)
    assert all("sk-test-secret" not in json.dumps(node) for node in risks)


def test_web_graph_extracts_react_routes_tailwind_and_assets(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "19.1.0", "next": "15.3.0", "tailwindcss": "4.1.0"}}),
        encoding="utf-8",
    )
    page = tmp_path / "app" / "users" / "[id]"
    page.mkdir(parents=True)
    (page / "page.tsx").write_text(
        """
import { useMemo } from "react";

export default function UserPage() {
  const name = useMemo(() => "Ada", []);
  return <main className="flex gap-2 bg-white"><Profile name={name} /></main>;
}

function Profile() {
  return <section className="text-sm rounded-md">profile</section>;
}
""",
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text(
        '<html><head><link href="/style.css"></head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text(".card, main > section { color: red; }\n", encoding="utf-8")

    graph = build_graph(tmp_path, "web").to_dict()

    assert any(node["id"] == "web:framework:react" for node in graph["nodes"])
    assert any(node["id"] == "web:framework:nextjs" for node in graph["nodes"])
    assert any(node["id"] == "web:component:app/users/[id]/page.tsx:UserPage" for node in graph["nodes"])
    assert any(node["id"] == "web:hook:app/users/[id]/page.tsx:useMemo" for node in graph["nodes"])
    assert any(node["id"] == "web:route:/users/:id" for node in graph["nodes"])
    assert any(node["id"] == "web:tailwind:flex" for node in graph["nodes"])
    assert any(node["id"] == "web:asset:index.html:/style.css" for node in graph["nodes"])
    assert any(node["attributes"].get("kind") == "css_selector" and node["label"] == ".card" for node in graph["nodes"])


def test_android_graph_extracts_gradle_manifest_sources_and_resources(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle.kts").write_text('pluginManagement {}\ninclude(":app", ":feature:feed")\n', encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    (app / "build.gradle.kts").write_text(
        """
plugins {
  id("com.android.application")
  id("org.jetbrains.kotlin.android")
}

android {
  namespace = "com.example.app"
  compileSdk = 35
  defaultConfig {
    applicationId = "com.example.app"
    minSdk = 26
    targetSdk = 35
    versionName = "1.2.3"
  }
}

dependencies {
  implementation("androidx.activity:activity-compose:1.9.0")
  implementation("com.squareup.retrofit2:retrofit:2.11.0")
}
""",
        encoding="utf-8",
    )
    manifest = app / "src" / "main"
    manifest.mkdir(parents=True)
    (manifest / "AndroidManifest.xml").write_text(
        """
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app">
  <uses-permission android:name="android.permission.INTERNET" />
  <application android:label="@string/app_name">
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
    <service android:name=".SyncService" android:exported="false" />
  </application>
</manifest>
""",
        encoding="utf-8",
    )
    source = app / "src" / "main" / "java" / "com" / "example" / "app"
    source.mkdir(parents=True)
    (source / "MainActivity.kt").write_text(
        """
package com.example.app

class MainActivity : ComponentActivity() {
  fun open() {
    startActivity(Intent(this, SettingsActivity::class.java))
  }
}

class SyncService : Service()
""",
        encoding="utf-8",
    )
    layout = app / "src" / "main" / "res" / "layout"
    layout.mkdir(parents=True)
    (layout / "activity_main.xml").write_text(
        '<LinearLayout><TextView android:id="@+id/title" /></LinearLayout>',
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "android").to_dict()

    assert any(node["id"] == "android:module::app" for node in graph["nodes"])
    assert any(node["id"] == "android:plugin:com.android.application" for node in graph["nodes"])
    assert any(node["id"] == "android:dependency:com.squareup.retrofit2:retrofit:2.11.0" for node in graph["nodes"])
    assert any(node["id"] == "android:permission:android.permission.INTERNET" for node in graph["nodes"])
    assert any(node["id"] == "android:activity:.MainActivity" for node in graph["nodes"])
    assert any(node["id"] == "android:service:.SyncService" for node in graph["nodes"])
    assert any(
        node["attributes"].get("kind") == "android_activity" and node["label"] == "MainActivity"
        for node in graph["nodes"]
    )
    assert any(node["id"] == "android:api:intent" for node in graph["nodes"])
    assert any(node["attributes"].get("kind") == "android_resource" and node["attributes"].get("resource_type") == "layout" for node in graph["nodes"])
    assert any(node["id"] == "android:widget:TextView" for node in graph["nodes"])


def test_decision_graph_extracts_architecture_tradeoffs(tmp_path: Path) -> None:
    docs = tmp_path / "docs" / "adr"
    docs.mkdir(parents=True)
    (docs / "0001-graph-storage.md").write_text(
        """
# Use JSON graph output

## Problem

Users need to inspect code graphs without operating a graph database.

## Options

Option A: write JSON files. Option B: insert directly into Neo4j.

## Pros

JSON files are portable and easy to review.

## Cons

Large JSON files can be slower to query than a graph database.

## Tradeoffs

The tradeoff is repeatable offline generation versus faster online traversal.

## Decision

We will emit deterministic OhWise-compatible JSON and make graph database loading optional.
""",
        encoding="utf-8",
    )
    (tmp_path / "builder.py").write_text(
        """
# Decision: keep graph generation deterministic so it does not require an LLM.
def build():
    return {}
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "decision").to_dict()

    kinds = {node["attributes"].get("kind") for node in graph["nodes"]}
    assert {
        "decision_root",
        "decision_source",
        "design_problem",
        "design_option",
        "design_pro",
        "design_con",
        "design_tradeoff",
        "design_decision",
    }.issubset(kinds)
    assert any(edge["label"] == "has_option" for edge in graph["edges"])
    assert any(edge["label"] == "has_tradeoff" for edge in graph["edges"])
    assert any(edge["label"] == "resolved_by" for edge in graph["edges"])
    assert any(
        node["attributes"].get("source_type") == "code_comment"
        and "does not require an LLM" in node.get("content", "")
        for node in graph["nodes"]
    )


def test_all_graph_includes_decision_graph(tmp_path: Path) -> None:
    (tmp_path / "ARCHITECTURE.md").write_text(
        """
# Architecture

## Problem
The API needs a stable extraction layer.

## Decision
We use analyzer plugins so each language can evolve independently.
""",
        encoding="utf-8",
    )

    graph = build_graph(tmp_path, "all").to_dict()

    assert any(node["attributes"].get("kind") == "design_decision" for node in graph["nodes"])


def test_iteration_runner_writes_progress_and_snapshot(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    report_file = tmp_path / "progress.md"
    prompt_file = tmp_path / "next_prompt.md"

    monkeypatch.chdir(tmp_path)
    code = iterate_main(
        [
            str(repo),
            "--graph",
            "all",
            "--iterations",
            "1",
            "--output-dir",
            str(output_dir),
            "--report-file",
            str(report_file),
            "--prompt-file",
            str(prompt_file),
            "--test-command",
            "",
        ]
    )

    assert code == 0
    assert "generated" in report_file.read_text(encoding="utf-8")
    assert list(output_dir.glob("repo.all.*.json"))
    prompt = prompt_file.read_text(encoding="utf-8")
    assert "code2graph Next Iteration Prompt" in prompt
    assert "Recommended Next Steps" in prompt
    assert "Not run" in prompt


def test_iteration_prompt_includes_graph_health_and_tests(tmp_path: Path) -> None:
    prompt = build_iteration_prompt(
        repo_path=tmp_path,
        graph_type="all",
        snapshot=tmp_path / "current.json",
        summary={
            "node_count": 3,
            "edge_count": 2,
            "node_kinds": {"file": 2, "function": 1},
            "edge_labels": {"contains": 1, "calls": 1},
            "dangling_edge_count": 1,
            "isolated_node_count": 1,
        },
        previous_snapshot=tmp_path / "previous.json",
        previous_summary={
            "node_count": 2,
            "edge_count": 1,
            "dangling_edge_count": 0,
            "isolated_node_count": 2,
        },
        test_result=None,
    )

    assert "Nodes: 3 (+1)" in prompt
    assert "Fix 1 dangling edges" in prompt
    assert "Review 1 isolated nodes" in prompt
    assert "jw-open <176761431+jw-open@users.noreply.github.com>" in prompt


def test_loop_status_without_pid_is_not_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(loop, "_runtime_path", lambda name: tmp_path / name)

    assert loop.main(["status"]) == 1


def test_loop_start_command_defaults_to_codex(tmp_path: Path) -> None:
    class Args:
        repo = str(tmp_path)
        graph = "all"
        interval_minutes = 20.0
        output_dir = ".code2graph-runs"
        report_file = "CODE2GRAPH_PROGRESS.md"
        prompt_file = "CODE2GRAPH_NEXT_PROMPT.md"
        test_command = "python -m pytest -q"
        commit_push = True
        codex = True
        codex_bin = "/tmp/codex"
        codex_timeout_seconds = 900
        discord_webhook_url = None
        report_command = None

    command = loop._build_iterate_command(Args())

    assert "--codex" in command
    assert "--codex-bin" in command
    assert "/tmp/codex" in command
    assert "--commit-push" in command
