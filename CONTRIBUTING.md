# Contributing to code2graph

Thank you for your interest in contributing. This document covers how to set up a development environment, run tests, and submit changes.

---

## Development setup

```bash
git clone https://github.com/jw-open/code2graph
cd code2graph
pip install -e ".[dev]"
```

---

## Running tests

```bash
pytest tests/ -v
```

Run a single test:

```bash
pytest tests/test_code2graph.py::test_folder_graph_has_repo_root -v
```

With coverage:

```bash
pip install pytest-cov
pytest tests/ --cov=code2graph --cov-report=term-missing
```

All 125 tests must pass before submitting a pull request.

---

## Project structure

```
code2graph/
├── code2graph/
│   ├── __init__.py         # Public API: build_graph, Graph, Node, Edge
│   ├── builder.py          # Dispatch to graph-type builders
│   ├── models.py           # Graph, Node, Edge dataclasses
│   ├── cli.py              # CLI entry point
│   ├── update.py           # Update-existing-graph mode
│   ├── scanner.py          # Repository file scanner
│   ├── folder_graph.py     # Folder/file structure
│   ├── call_graph.py       # Function call graphs (Python, JS, TS)
│   ├── entity_graph.py     # Classes, functions, constants
│   ├── schema_graph.py     # Database schema (SQL, ORM)
│   ├── workflow_graph.py   # CI/CD pipelines
│   ├── infra_graph.py      # Docker, Terraform, Kubernetes
│   ├── security_graph.py   # Secret patterns, dangerous calls
│   ├── web_graph.py        # React/Vue components, routes
│   ├── android_graph.py    # AndroidManifest.xml
│   ├── decision_graph.py   # ADR-style decisions
│   ├── python_graph.py     # Python AST utilities
│   └── iterate.py / loop.py / prompt.py  # Autonomous iteration tooling
├── tests/
│   └── test_code2graph.py  # 125 tests, ~4700 lines
├── examples/
├── ROADMAP.md
├── pyproject.toml
├── CHANGELOG.md
└── README.md
```

---

## Adding a new graph type

1. Create `code2graph/mygraph_graph.py`
2. Implement `build_mygraph_graph(root: Path) -> tuple[list[Node], list[Edge]]`
3. Register it in `code2graph/builder.py` — add to `GRAPH_BUILDERS`
4. Add CLI option to `code2graph/cli.py` choices list
5. Add at least 5 tests covering: empty repo, typical case, edge cases
6. Update `CHANGELOG.md` under `[Unreleased]`

Node IDs should be deterministic and stable: `"kind:qualified.name"` format.
Edge IDs should follow: `"edge:from_id:label:to_id"`.

---

## Submitting changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-graph-type`
3. Make changes, add tests
4. Run `pytest tests/ -v` — all tests must pass
5. Open a pull request against `main`

**Pull request checklist:**
- [ ] Tests pass (`pytest tests/ -v`)
- [ ] New graph type has at least 5 tests
- [ ] Node/edge IDs are deterministic (same input = same output)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No hardcoded paths, credentials, or personal identifiers in any file

---

## Reporting bugs

Open an issue at https://github.com/jw-open/code2graph/issues with:
- Python version and OS
- `codes2graph` version (`pip show codes2graph`)
- Repository type (Python, JavaScript, mixed, etc.)
- Minimal reproduction command + expected vs actual output

---

## Design guidelines

- **Deterministic** — same repository state must produce the same graph. Avoid filesystem ordering assumptions; always sort node/edge lists before returning.
- **Static analysis only** — never execute code from the target repository. Parse, don't run.
- **No required dependencies** — the standard library is sufficient for all graph types.
- **Stable node IDs** — `"kind:qualified.identifier"` format. Node IDs must not change when unrelated files change.
- **Plain output** — `Graph`, `Node`, `Edge` are simple dataclasses. No custom serialization.

---

## License

By contributing you agree your changes will be licensed under Apache-2.0.
