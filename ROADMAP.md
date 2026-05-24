# Roadmap

## Iteration 1: static graph baseline

Status: implemented.

- OhWise-compatible graph JSON.
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
- Add optional import into OhWise Knowledge API.

## Iteration 4: automated iteration runner

- Add a local scheduler command that can run analysis every 20 minutes.
- Produce diff reports between graph generations.
- Keep generated graph snapshots outside git by default.

