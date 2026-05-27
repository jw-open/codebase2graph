# code2graph

[![PyPI version](https://img.shields.io/pypi/v/codes2graph.svg)](https://pypi.org/project/codes2graph/)
[![PyPI downloads](https://img.shields.io/pypi/dm/codes2graph.svg)](https://pypi.org/project/codes2graph/)
[![Python](https://img.shields.io/pypi/pyversions/codes2graph.svg)](https://pypi.org/project/codes2graph/)
[![CI](https://github.com/jw-open/code2graph/actions/workflows/ci.yml/badge.svg)](https://github.com/jw-open/code2graph/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Turn a source code repository into a queryable knowledge graph — no LLM required.**

`code2graph` statically extracts the structure of a codebase — files, modules, functions, classes, calls, dependencies, schemas, infrastructure — as a typed graph of nodes and edges. Rank the most relevant nodes for any query with Personalized PageRank and pass focused context to any LLM.

**Pure Python. No LLM dependency. Bring your own model.**

---

## Quick start

```bash
pip install codes2graph
```

```bash
# Extract full graph from a repo
codes2graph /path/to/repo --graph all --output repo.graph.json

# Python call graph only
codes2graph /path/to/repo --graph call --output calls.graph.json

# With actionable summary
codes2graph /path/to/repo --graph all \
  --output repo.graph.json \
  --summary-output repo.summary.json
```

```python
from code2graph import build_graph

graph = build_graph("/path/to/repo", graph_type="all")
# graph.nodes — list of Node objects
# graph.edges — list of Edge objects
```

---

## Why graph-based code context?

| Approach | What you lose |
|----------|--------------|
| Dump entire codebase into prompt | Token budget, focus |
| Embed + search file chunks | Call relationships, module structure, dependency chains |
| **code2graph** | Nothing — relationships are explicit labeled edges |

The graph knows that `auth.login()` *calls* `db.query()`, which *imports* `connection_pool`, which *depends on* `config.DATABASE_URL`. Flat file chunks don't.

---

## Graph types

| Type | What it extracts |
|------|-----------------|
| `folder` | Repo, folder, file nodes with `contains` edges |
| `call` | Functions/methods with `calls` and `defines` edges (Python, JS, TS) |
| `entity` | Classes, functions, constants with `defines` and `imports` edges |
| `schema` | Database tables, columns, foreign keys (SQL, ORM models) |
| `workflow` | CI/CD pipelines, GitHub Actions, Makefile targets |
| `infra` | Dockerfiles, docker-compose, Terraform, Kubernetes manifests |
| `security` | Hardcoded secrets patterns, dangerous function calls, exposed endpoints |
| `web` | React/Vue components, routes, API endpoints |
| `android` | Activities, services, permissions from AndroidManifest.xml |
| `decision` | ADR-style architecture decisions |
| `all` | Merged graph from all applicable extractors |

```bash
codes2graph /path/to/repo --graph call   --output call.graph.json
codes2graph /path/to/repo --graph schema --output schema.graph.json
codes2graph /path/to/repo --graph infra  --output infra.graph.json
codes2graph /path/to/repo --graph all    --output full.graph.json
```

---

## Installation

```bash
pip install codes2graph
```

No extra dependencies required — all graph types work with the standard install.

---

## Python API

### Build a graph

```python
from code2graph import build_graph, Graph, Node, Edge

# Full graph
graph: Graph = build_graph("/path/to/repo", graph_type="all")

# Specific type
call_graph = build_graph("/path/to/repo", graph_type="call")
schema_graph = build_graph("/path/to/repo", graph_type="schema")
```

### Inspect results

```python
print(f"{len(graph.nodes)} nodes, {len(graph.edges)} edges")

# Filter by kind
functions = [n for n in graph.nodes if n.attributes.get("kind") == "function"]
calls = [e for e in graph.edges if e.label == "calls"]
```

### Export

```python
import json

# To dict
d = {"nodes": [vars(n) for n in graph.nodes], "edges": [vars(e) for e in graph.edges]}
json.dump(d, open("graph.json", "w"), indent=2)
```

---

## Graph output format

```json
{
  "nodes": [
    {
      "id": "function:auth.login",
      "label": "login",
      "attributes": {
        "kind": "function",
        "module": "auth",
        "file": "src/auth.py",
        "line": 42
      },
      "content": "def login(username, password): ..."
    }
  ],
  "edges": [
    {
      "id": "edge:auth.login:calls:db.query",
      "from": "function:auth.login",
      "to": "function:db.query",
      "label": "calls"
    }
  ],
  "current_node_id": "repo"
}
```

---

## CLI reference

```
codes2graph <repo> [options]

Arguments:
  repo                    Path to the repository root

Options:
  --graph TYPE            Graph type: folder, call, entity, schema, workflow,
                          infra, security, web, android, decision, all
                          (default: all)
  --output PATH           Write graph JSON to this file (default: stdout)
  --pretty                Pretty-print JSON output
  --summary-output PATH   Write graph summary JSON (entrypoints, fan-in/out nodes)
  --update-existing PATH  Update an existing graph JSON in place
  --update-summary-output PATH
                          Write update diff summary JSON
  -h, --help              Show help
```

### Update mode

Rebuild a graph from the current repository state while preserving stable node IDs and custom attributes added outside `code2graph`:

```bash
codes2graph /path/to/repo --graph all \
  --update-existing repo.graph.json \
  --update-summary-output repo.update.json
```

Update mode removes stale nodes/edges for deleted or changed code, adds new nodes/edges, and keeps stable IDs for nodes that haven't changed. Custom attributes on existing nodes are preserved.

---

## Use cases

- **Code review** — extract call graph before/after a PR to see what changed structurally
- **LLM code assistance** — pass ranked subgraph as context instead of dumping whole files
- **Dependency analysis** — find all callers of a function, all modules depending on a service
- **Security audit** — detect hardcoded secrets, dangerous API patterns, exposed endpoints
- **Architecture docs** — extract infra + schema + decision graphs for living documentation
- **Onboarding** — give a new developer a ranked subgraph of the most important entry points

---

## Design principles

- **Pure Python** — no LLM, no cloud, no database required
- **Deterministic** — same repository state always produces the same graph
- **Static analysis only** — no code execution, safe to run on any codebase
- **Works with any model** — output is plain JSON; pass to GPT-4, Claude, Llama, or any other model
- **Companion to [docs2graph](https://github.com/jw-open/doc2graph)** — same node/edge schema, combine code and documentation graphs

---

## Related projects

| Package | What it does |
|---------|-------------|
| [docs2graph](https://github.com/jw-open/doc2graph) | Documents → knowledge graph (same node/edge schema) |
| [graph2sql](https://github.com/jw-open/graph2sql) | Graph-based schema analysis for text-to-SQL |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
git clone https://github.com/jw-open/code2graph
cd code2graph
pip install -e ".[dev]"
pytest tests/ -v
```

---

## License

Apache-2.0 — see [LICENSE](LICENSE)
