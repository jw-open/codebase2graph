# Changelog

All notable changes to `code2graph` (PyPI: `codes2graph`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] — 2026-05-27

Initial public release.

### Added

**Core graph types**
- `folder` — repository, folder, and file nodes with `contains` edges; provenance for all other graph types
- `call` — function and method call graphs for Python (AST-based), JavaScript, and TypeScript; `calls` and `defines` edges; resolves imported names to their source module
- `entity` — class, function, and constant nodes with `defines` and `imports` edges; handles Python and common JS/TS patterns
- `schema` — database table and column graphs from SQL DDL, SQLAlchemy models, and data dictionary documents; `has_column` and `foreign_key` edges
- `workflow` — CI/CD pipeline nodes from GitHub Actions YAML, Makefile targets, and shell scripts; `triggers` and `depends_on` edges
- `infra` — infrastructure nodes from Dockerfiles, docker-compose, Terraform, and Kubernetes manifests; `uses`, `exposes`, and `depends_on` edges
- `security` — hardcoded secret patterns, dangerous function calls, and exposed endpoint nodes; `warns` edges connecting to their source file
- `web` — React and Vue component nodes, route definitions, and API endpoint declarations; `renders` and `routes_to` edges
- `android` — Activity, Service, and permission nodes from `AndroidManifest.xml`; `declares` edges
- `decision` — ADR-style architecture decision nodes (problem, options, decision, consequences)
- `all` — merged graph from all applicable extractors for a given repository

**Python API**
- `build_graph(root, graph_type)` — main entry point; returns a `Graph` dataclass
- `Graph` dataclass: `nodes: list[Node]`, `edges: list[Edge]`, `current_node_id: str`
- `Node` dataclass: `id`, `label`, `attributes`, `content`
- `Edge` dataclass: `id`, `from_id`, `to_id`, `label`

**CLI**
- `codes2graph <repo> --graph TYPE --output PATH` — extract graph to JSON
- `--pretty` — pretty-print JSON output
- `--summary-output PATH` — write actionable summary (entrypoints, high-fan-in/out nodes, isolated files)
- `--update-existing PATH` — rebuild graph in place, preserving stable IDs and custom attributes
- `--update-summary-output PATH` — write update diff summary (added/removed nodes and edges)

**Graph summary**
- Entrypoint detection (nodes with incoming edges but no outgoing imports)
- High-fan-in nodes (most incoming call/import edges — likely core utilities)
- High-fan-out nodes (most outgoing call/import edges — likely orchestrators)
- Isolated files (only folder structural links, no semantic edges)

**Update mode**
- Rebuilds selected graph type from current repository state
- Preserves stable node IDs across runs for nodes that haven't changed
- Removes stale nodes/edges for deleted or refactored code
- Preserves custom attributes added to nodes outside `code2graph`
- Reports added/removed node and edge counts in update summary

**Test coverage**
- 125 test functions across Python call graph, TypeScript/JavaScript graph, web/React, Android, entity, schema, CLI output, iteration prompt generation, loop runner

**Packaging**
- Apache-2.0 license
- Python 3.10+ requirement (uses structural pattern matching and `match` in some parsers)
- No required dependencies beyond the standard library

---

[0.1.0]: https://github.com/jw-open/code2graph/releases/tag/v0.1.0
