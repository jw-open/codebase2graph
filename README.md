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
python -m code2graph /path/to/repo --graph infra
```

Graph types:

- `folder`: repository folder/file tree.
- `call`: static Python AST and JavaScript/TypeScript call relationships between discovered functions/methods.
- `entity`: source entities and import relationships.
- `schema`: database/schema hints from SQL, Prisma, Django, SQLAlchemy, and common migration files.
- `workflow`: likely runnable workflows from package scripts, Makefile targets, CI jobs, Docker Compose services, and Python entrypoints.
- `infra`: service topology, CI/CD pipelines, service-level communication, cloud providers/resources, integrations, and package/runtime dependencies.
- `all`: merged multi-layer graph.

The infra graph currently extracts:

- Services from Docker Compose, Kubernetes manifests, Dockerfiles, and Serverless files.
- Service communication from Compose `depends_on`, exposed ports, URL-like environment values, and integration endpoint references.
- CI/CD pipelines and jobs from GitHub Actions workflow files.
- Cloud providers/resources from Terraform provider/resource declarations and Serverless provider config.
- Dependencies from `package.json`, `requirements.txt`, and `pyproject.toml`.

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

Run every 20 minutes without invoking Codex. This mode only regenerates local context and can commit the progress report:

```bash
python -m code2graph.iterate ../claude-code-source-code \
  --graph all \
  --interval-minutes 20 \
  --iterations 0 \
  --commit-push
```

Start the autonomous 20-minute Codex loop as a detached background process:

```bash
python -m code2graph.loop start ../claude-code-source-code
python -m code2graph.loop status
python -m code2graph.loop stop
```

The detached loop writes `.code2graph-runs/loop.pid` and `.code2graph-runs/loop.log`.
It invokes the local `codex` CLI every 20 minutes by default, using `~/.codex` credentials/config, asks Codex to implement one focused package improvement, runs tests, commits real source/test/doc/package changes with the `jwpublic` identity, and pushes `origin main`.

Generated graph snapshots go under `.code2graph-runs/`, which is ignored by git. The tracked progress file is `CODE2GRAPH_PROGRESS.md`.
Each loop also writes `CODE2GRAPH_NEXT_PROMPT.md`, a handoff prompt for the next coding pass. It includes latest graph counts, deltas from the previous snapshot when available, graph-health warnings, test output, and recommended next steps.
The autonomous loop does not commit generated snapshots, prompt handoff files, or timestamp-only progress updates.

By default the loop runs `python -m pytest -q` after generating the graph. Override or disable that with:

```bash
python -m code2graph.iterate ../claude-code-source-code \
  --test-command "python -m pytest -q tests/test_code2graph.py"

python -m code2graph.iterate ../claude-code-source-code \
  --test-command ""
```

To report each loop to a bot or webhook, pass a command that reads the progress line from stdin:

```bash
python -m code2graph.iterate ../claude-code-source-code \
  --iterations 0 \
  --report-command './scripts/post-progress-to-discord.sh'
```

Or set a Discord webhook directly:

```bash
export CODE2GRAPH_DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
python -m code2graph.loop start ../claude-code-source-code
```

Useful loop controls:

```bash
python -m code2graph.loop start ../claude-code-source-code --no-codex
python -m code2graph.loop start ../claude-code-source-code --codex-bin /home/jwang/.npm-global/bin/codex
python -m code2graph.loop start ../claude-code-source-code --codex-timeout-seconds 1200
```

## Notes

This first version favors broad, fast static extraction over perfect semantic resolution. JavaScript/TypeScript call parsing is regex-based and intentionally conservative; the next major step is a tree-sitter or compiler-service analyzer that can resolve local imports and merge call targets with concrete function definitions.
