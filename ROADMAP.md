# Roadmap

## Iteration 1: static graph baseline

Status: implemented.

- standard knowledge graph JSON.
- Folder tree graph.
- Python AST call graph.
- JavaScript/TypeScript regex call graph.
- Entity/import graph.
- Database/schema hints from SQL, Prisma, Django, and SQLAlchemy patterns.
- Workflow graph from package scripts, Makefile targets, Compose services, CI workflows, and Python entrypoints.

## Iteration 2: richer language analyzers

- Replace JavaScript/TypeScript regex parsing with tree-sitter or TypeScript compiler service.
- Resolve local imports into file-to-file edges instead of only module-name nodes.
- Merge call target placeholders with known function definitions where names can be resolved safely.
- Add Go, Java, and Rust function/entity parsers.

## Iteration 3: context-engineering outputs

- Emit separate layered graph files plus a merged graph bundle.
- Add graph summaries: hotspots, entrypoints, isolated modules, high-fan-in functions, and workflow start nodes.
- Generate chunk manifests that map graph nodes to source snippets for context retrieval.
- Add optional graph database export.

## Iteration 4: automated iteration runner

- Add a local scheduler command that can run analysis every 20 minutes.
- Produce diff reports between graph generations.
- Keep generated graph snapshots outside git by default.
- Generate a prompt/handoff file for the next coding loop with context, issues, tests, and suggested next implementation steps.

Status: partially implemented.

- `code2graph-iterate` / `python -m code2graph.iterate` can run once, N times, or forever.
- It writes tracked progress to `CODE2GRAPH_PROGRESS.md`.
- It writes tracked next-loop context to `CODE2GRAPH_NEXT_PROMPT.md`.
- It keeps large graph snapshots in ignored `.code2graph-runs/`.
- It can commit and push progress using the configured git identity when `--commit-push` is set.
- It can call a local report command for Discord/webhook integration.
- It can run a test command each loop and include the output in the prompt.
