# code2graph Next Iteration Prompt

You are working only in the `code2graph` repository. Continue improving the code-to-graph generator for OhWise-compatible context engineering graphs.

## Current Snapshot

- Target repo: `/home/jwang/github/ohwise/claude-code-source-code`
- Graph type: `all`
- Current snapshot: `.code2graph-runs/claude-code-source-code.all.20260524T035042Z.json`
- Previous snapshot: `.code2graph-runs/claude-code-source-code.all.20260524T034801Z.json`

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

- Fix the failing test command before broadening graph extraction.

## Tests

- `python -m pytest -q` failed with exit code 1.

```text
.......F                                                                 [100%]
=================================== FAILURES ===================================
_________________ test_loop_status_without_pid_is_not_running __________________

    def test_loop_status_without_pid_is_not_running() -> None:
>       assert loop_main(["status"]) == 1
E       AssertionError: assert 0 == 1
E        +  where 0 = loop_main(['status'])

tests/test_code2graph.py:163: AssertionError
----------------------------- Captured stdout call -----------------------------
code2graph loop running with pid 2469087
=========================== short test summary info ============================
FAILED tests/test_code2graph.py::test_loop_status_without_pid_is_not_running
1 failed, 7 passed in 0.05s
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
