from __future__ import annotations

import re
from pathlib import Path

from .models import Graph
from .scanner import iter_files, read_text

SQL_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([\w.]+)[`\"]?\s*\((.*?)\);", re.I | re.S)
SQL_COLUMN_RE = re.compile(r"^\s*[`\"]?(\w+)[`\"]?\s+([A-Z][A-Z0-9_() ]+)", re.I)
PRISMA_MODEL_RE = re.compile(r"model\s+(\w+)\s*\{(.*?)\}", re.S)
PY_MODEL_RE = re.compile(r"class\s+(\w+)\((?:.*?Model.*?)\):")
SQLALCHEMY_TABLE_RE = re.compile(r"__tablename__\s*=\s*['\"]([^'\"]+)['\"]")


def build_schema_graph(root: Path) -> Graph:
    graph = Graph()
    for path in iter_files(root):
        text = read_text(path)
        if not text:
            continue
        if path.suffix.lower() in {".sql", ".ddl"}:
            _add_sql_schema(graph, root, path, text)
        elif path.name == "schema.prisma":
            _add_prisma_schema(graph, root, path, text)
        elif path.suffix == ".py":
            _add_python_schema_hints(graph, root, path, text)
    return graph


def _add_sql_schema(graph: Graph, root: Path, path: Path, text: str) -> None:
    rel = path.relative_to(root).as_posix()
    source_id = f"schema-source:{rel}"
    graph.add_node(source_id, path.name, attributes={"kind": "schema_source", "path": rel})
    for table, body in SQL_TABLE_RE.findall(text):
        table_id = f"db:table:{table}"
        graph.add_node(table_id, table, attributes={"kind": "table", "source": rel})
        graph.add_edge(source_id, table_id, "declares")
        for line in body.splitlines():
            match = SQL_COLUMN_RE.match(line.strip().rstrip(","))
            if not match:
                continue
            column, dtype = match.groups()
            if column.upper() in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}:
                continue
            column_id = f"db:column:{table}.{column}"
            graph.add_node(
                column_id,
                column,
                attributes={"kind": "column", "table": table, "data_type": " ".join(dtype.split())},
            )
            graph.add_edge(table_id, column_id, "has_column")


def _add_prisma_schema(graph: Graph, root: Path, path: Path, text: str) -> None:
    rel = path.relative_to(root).as_posix()
    source_id = f"schema-source:{rel}"
    graph.add_node(source_id, path.name, attributes={"kind": "schema_source", "path": rel, "language": "prisma"})
    for model, body in PRISMA_MODEL_RE.findall(text):
        model_id = f"db:table:{model}"
        graph.add_node(model_id, model, attributes={"kind": "table", "source": rel, "schema_language": "prisma"})
        graph.add_edge(source_id, model_id, "declares")
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("//") or line.startswith("@@"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            field, dtype = parts[0], parts[1]
            column_id = f"db:column:{model}.{field}"
            graph.add_node(column_id, field, attributes={"kind": "column", "table": model, "data_type": dtype})
            graph.add_edge(model_id, column_id, "has_column")


def _add_python_schema_hints(graph: Graph, root: Path, path: Path, text: str) -> None:
    rel = path.relative_to(root).as_posix()
    for class_name in PY_MODEL_RE.findall(text):
        table = class_name
        table_match = SQLALCHEMY_TABLE_RE.search(text)
        if table_match:
            table = table_match.group(1)
        table_id = f"db:table:{table}"
        graph.add_node(table_id, table, attributes={"kind": "table", "source": rel, "schema_language": "python"})
        graph.add_node(f"schema-source:{rel}", path.name, attributes={"kind": "schema_source", "path": rel})
        graph.add_edge(f"schema-source:{rel}", table_id, "declares")

