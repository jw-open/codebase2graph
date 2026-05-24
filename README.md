# code2graph

`code2graph` generates OhWise-compatible graph JSON from a source repository.

Output shape:

```json
{
  "nodes": [
    {
      "id": "folder:/src",
      "label": "src",
      "attributes": {
        "kind": "folder",
        "path": "src"
      }
    }
  ],
  "edges": [
    {
      "id": "edge:repo:contains:folder:/src",
      "from": "repo",
      "to": "folder:/src",
      "label": "contains"
    }
  ],
  "current_node_id": "repo"
}
```

## Usage

```bash
python -m code2graph /path/to/repo --graph all --output out/code-graph.json
```

Generate one graph type:

```bash
python -m code2graph /path/to/repo --graph folder
python -m code2graph /path/to/repo --graph call
python -m code2graph /path/to/repo --graph entity
python -m code2graph /path/to/repo --graph schema
python -m code2graph /path/to/repo --graph workflow
```

Graph types:

- `folder`: repository folder/file tree.
- `call`: static Python AST and JavaScript/TypeScript call relationships between discovered functions/methods.
- `entity`: source entities and import relationships.
- `schema`: database/schema hints from SQL, Prisma, Django, SQLAlchemy, and common migration files.
- `workflow`: likely runnable workflows from package scripts, Makefile targets, CI jobs, Docker Compose services, and Python entrypoints.
- `all`: merged multi-layer graph.

## Sample

```bash
python -m code2graph ../claude-code-source-code --graph all --output examples/claude-code-source-code.graph.json
```

The generated JSON can be pasted into an OhWise Knowledge graph because it uses the same `nodes`, `edges`, and `current_node_id` structure.

## Iteration Loop

Run one iteration and write a progress report:

```bash
python -m code2graph.iterate ../claude-code-source-code --graph all
```

Run every 20 minutes, commit the progress report with the `jwpublic` identity, and push `main`:

```bash
python -m code2graph.iterate ../claude-code-source-code \
  --graph all \
  --interval-minutes 20 \
  --iterations 0 \
  --commit-push
```

Generated graph snapshots go under `.code2graph-runs/`, which is ignored by git. The tracked progress file is `CODE2GRAPH_PROGRESS.md`.

To report each loop to a bot or webhook, pass a command that reads the progress line from stdin:

```bash
python -m code2graph.iterate ../claude-code-source-code \
  --iterations 0 \
  --report-command './scripts/post-progress-to-discord.sh'
```

## Notes

This first version favors broad, fast static extraction over perfect semantic resolution. JavaScript/TypeScript call parsing is regex-based and intentionally conservative; the next major step is a tree-sitter or compiler-service analyzer that can resolve local imports and merge call targets with concrete function definitions.
