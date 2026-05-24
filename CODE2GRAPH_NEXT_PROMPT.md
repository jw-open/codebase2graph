# code2graph Next Iteration Prompt

You are working only in the `code2graph` repository. Continue improving the code-to-graph generator for OhWise-compatible context engineering graphs.

## Current Snapshot

- Target repo: `/home/jwang/github/ohwise/claude-code-source-code`
- Graph type: `all`
- Current snapshot: `.code2graph-runs/claude-code-source-code.all.20260524T043054Z.json`
- Previous snapshot: `.code2graph-runs/claude-code-source-code.all.20260524T041048Z.json`

## Graph Delta

- Nodes: 34964 (+0)
- Edges: 72061 (+0)
- Dangling edges: 0 (+0)
- Isolated nodes: 0 (+0)

## Node Kinds

- entity: 10070
- function: 9861
- call_target: 9157
- import: 3623
- file: 1929
- folder: 316
- npm_script: 4
- call_graph: 1

## Edge Labels

- calls: 25610
- defines: 19931
- imports: 14409
- contains: 12106
- runs: 4
- has_workflow: 1

## Issues And Bugs To Check

- No blocking graph health issue detected in this snapshot.

## Tests

- `python -m pytest -q` passed with exit code 0.

```text
........                                                                 [100%]
8 passed in 0.03s
```

## Recommended Next Steps

1. Improve call graph resolution by connecting placeholder call targets to concrete function nodes where imports or same-file definitions make that safe.
2. Add graph summary outputs for entrypoints, high-fan-in nodes, high-fan-out nodes, and isolated modules.
3. Add focused regression fixtures before each parser expansion so graph shape stays stable.
4. Keep generated snapshots out of git; commit only source, tests, docs, and progress/prompt context.

## Commit Discipline

- Use `jw-open <176761431+jw-open@users.noreply.github.com>`.
- Push to `jwpublic:jw-open/code2graph.git` `main`.
- Report the commit hash and test result back to Discord.
