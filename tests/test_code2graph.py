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
