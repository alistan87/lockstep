---
type: guide
title: webapp-local — an expense splitter built by local models
resource: flows/demo/README-webapp.md
---
# `webapp-local` — backend + frontend, on local models, for nothing

```powershell
lockstep verify flows\demo\webapp-local.tg.json
lockstep run    flows\demo\webapp-local.tg.json --dry-run     # 9 waves, spends nothing
lockstep run    flows\demo\webapp-local.tg.json               # no network, no credential, no tokens
```

Flow B of the stress-test plan. `contrib/torture_suite.py` drives the engine's
failure paths with a scripted agent; this drives them with a *real* unreliable
model, which is the half a script cannot simulate: prompts that are almost
right, output that is almost parseable, and a gate that has to say something
useful about it.

## Why an expense splitter

Because it has **invariants a gate can check with its own implementation**.
"Did it store the row" is either trivially right or trivially broken and
neither produces a useful heal round. Settlement math gives the gate teeth:
balances must match a reference, money must be conserved, expense order must
not matter, the transfers must actually zero everyone out, and *n* people need
at most *n−1* of them. When a model gets that wrong the gate hands back a
**runnable counterexample**, and `fix_hint` is the next prompt.

## Requirements

- **ollama**, with `qwen2.5-coder:7b` and `qwen2.5-coder:14b` pulled.
- **pi** on PATH (`pi.cmd` on Windows), which lockstep calls with
  `--provider ollama`.
- **Node 18+**, for the frontend gate. No npm, no install step — Node's own
  runtime is the whole toolchain.
- Two stanzas, `local-fast` and `local-smart`; both are in
  `lockstep.toml.example`, copy them across.

## Every harness node is tool-less, and that is load-bearing

Measured against pi 0.83.0 + ollama:

| harness | result |
|---|---|
| `qwen2.5-coder:7b` with `--tools read` | answered with a **hallucinated tool call** — `{"name":"read","arguments":{…}}` — as its result text |
| `qwen2.5-coder:7b` with `--no-tools` | clean, correct JSON in 3s |
| `qwen2.5-coder:14b` with `--no-tools` | clean, correct JSON in 27s |

Small models narrate tool calls into the result channel. So they get no tools,
everything they need is in the prompt, and **shell nodes do all the file I/O**
(`contrib/save_result.py --strip-fence`). That is the §8.3 stdout-channel shape
the sudoku demo already used, and it is the lockstep creed rather than a
workaround: the model authors content, never control flow.

Every harness node is also `readonly: true`. Not an optimisation — a
correctness fix. Without it the standard footer instructs a model with **no
tools** to write `result.json` to the phase directory, which is precisely the
"a prompt demanding what the tools forbid" defect. `FOOTER_READONLY` says the
right thing, and dropping the `tree` token is what puts the three writers in
one wave instead of three.

## What it exercises

| Capability | Where |
|---|---|
| healing, 3 rounds each | all three gates heal their own author |
| cascade invalidation | `write-server` depends on `logic-gate`, so healing the logic re-runs the server |
| readonly parallelism | wave 0 runs three writers at once |
| write scope | each `save-*` node declares the single file it may write |
| map + `optional` | `review` fans out over a fingerprinted `PathManifest` |
| per-node models | 14B for the settlement algorithm, 7B for the mechanical files |
| deterministic gates | three of them, all machine-decidable, none asking a model |

## The gates, and the rule they follow

Each was run against a known-good and several known-bad inputs **before** the
flow existed (`tests/test_split_check.py`, `tests/test_webapp_gates.py`):

- **`split_check.py`** — the settlement logic against its own reference, seeded
  rather than random so identical code gives identical findings. Runs in a
  child process with a clock: model-written loops hang often, and a gate that
  hangs gets killed, retried, and produces "no valid verdict emitted" twice —
  correct, and useless to whoever has to fix it.
- **`api_check.py`** — starts the real server on a port *the gate chooses*
  (a hard-coded 8000 turns a correct server into a red gate), polls `/health`
  rather than sleeping, drives the contract over HTTP, and kills the child in a
  `finally` so a failed round cannot poison the next one.
- **`ui_check.py`** — runs the frontend logic under Node, and refuses a page
  that references any external URL. That last one is not style: there is no
  network here, so a page depending on a CDN works on the machine that built it
  and nowhere else.

## Expect it to block

That is the point. A 7B will produce a settlement that leaves someone a few
cents short, a server that answers 200 for unknown paths, a `formatAmount` that
returns `12.345`. Those are the heal rounds this flow exists to generate. If it
passes every gate first try, raise the difficulty rather than celebrating — a
gate that never fires is not evidence of quality.
