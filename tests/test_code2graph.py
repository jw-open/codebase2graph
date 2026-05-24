from __future__ import annotations

import json
from pathlib import Path

from code2graph.builder import build_graph
from code2graph.cli import main
from code2graph.iterate import main as iterate_main
from code2graph import loop
from code2graph.prompt import build_iteration_prompt


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
