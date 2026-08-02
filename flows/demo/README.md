# The cockpit demo

`repo-hygiene-demo.tg.json` is segment 1 of the repo-hygiene work order's Flow 1
(`docs/repo-hygiene-work-order.md` §2), scaled to run on this repo in minutes.
It exists to exercise **every part of the domain-expert cockpit at once** on
work that is real rather than staged.

It deliberately does NOT build the work order's toolchain — no DuckDB catalog,
no vendored OKF spec, no apply engine. Those are that work order's own build
order. This demonstrates the *driving* half: how a non-programmer runs
substantial work and decides on the result without touching git, JSON, or a
command line.

## What it does

| # | node | kind | what it shows |
|---|---|---|---|
| 1 | `build-catalog` | shell | Deterministic catalog + rule engine over the docs tree. **Zero model cost.** Writes `mission.txt`, which the MISSION pane renders verbatim |
| 2 | `clarify-rules` | gate, heal 0 | Two rules claim the same file at equal precedence. No amount of model judgment can settle that, so it asks — one line, plain language, `category: "question"` |
| 3 | `classify` | map, harness | Model judgment on **the ambiguous residue only** — the cost-escalation principle: hundreds of files, a handful of spawns |
| 4 | `validate-manifest` | gate, shell, heal 2 | Deterministic: collisions, path escapes, missing targets, reproducible ordering. Free, and it **heals** the classifier |
| 5 | `review-sample` | gate, harness | Adversarial: is the classification *right*, not just well-formed |
| 6 | `render-evidence` | shell | The stratified B1 extract the human decides from |
| 7 | `approve-manifest` | approval | Terminal. Exit 6 when detached — the designed handoff |
| 8 | `record` | shell | The sanctioned trivial tail: a copy, seconds long, in the human's own process |

**Nothing moves.** Agents emit a typed manifest (`ManifestEntry`, a custom
contract); deterministic code judges it; a human approves. No agent renames,
moves, or edits a file at any point.

## Running it

```powershell
lockstep verify flows\demo\repo-hygiene-demo.tg.json
lockstep run    flows\demo\repo-hygiene-demo.tg.json --dry-run

# real run, detached (non-TTY stdin is what makes the approval auto-reject)
lockstep run flows\demo\repo-hygiene-demo.tg.json --arg "area=docs" --arg "max_ambiguous=3" < NUL
```

Expect **exit 2** first: the clarify gate finds the rule conflict and asks. That
is the flow working, not failing. Answer it the way the cockpit does:

```powershell
lockstep steer <run_dir> classify      "R-030 wins over R-032: work orders are plans."
lockstep steer <run_dir> clarify-rules "R-030 wins over R-032: work orders are plans."
lockstep resume <run_dir> < NUL
```

Steer the gate **as well as** the classifier, or the gate re-asks a question
that has already been answered and the run never advances.

Then **exit 6** — ready for a decision:

```powershell
python contrib\quiescent.py <run_dir>                        # 0 = safe to hand over
pwsh -File contrib\cockpit.ps1 -RunDir <run_dir> -Approve    # spawns the pane, pre-typed
```

Observability while it runs:

```powershell
pwsh -File contrib\cockpit.ps1 -RunDir <run_dir>            # MISSION + ACTIVITY
python contrib\cost_report.py --compact <run_dir>            # spend right now
```

## Cost

About **9 agent tasks** and a few minutes at `--arg max_ambiguous=3`; the budget
caps at 24. The catalog, the rule engine, and the manifest validator are free —
raising `max_ambiguous` raises only the map fan-out.

## What each part is really demonstrating

- **The clarify gate is not a review gate.** It asks only what a human must
  decide. A rule conflict is the honest example: both rules are legitimate, so
  judgment cannot settle it — only the taxonomy's owner can.
- **The evidence rule.** `contrib/demo/hygiene_evidence.py` shows every
  structural change and *everything the system was unsure about* in full, then
  a deterministic sample of what it was confident about. A stats-only summary
  would hide exactly what needs eyes; sampling is seeded by the manifest's own
  content so the same manifest always produces the same pane.
- **The segmentation rule.** `record` is a shell copy — the only kind of node
  allowed after an approval, because it runs in the human's own process.
- **Deterministic gates heal; model gates judge.** `validate-manifest` costs
  nothing and sends the classifier back with specific violations. Spending a
  model call on something a regex can decide is the wrong trade.

## Known rough edges

- `build-catalog` sees tracked files **plus** untracked-not-ignored ones, so a
  brand-new document is catalogued. `.gitignore` is the boundary — `runs/` and
  anything else ignored stays invisible.
- The `--max-ambiguous` cap announces its own truncation in `notes`, and the
  review gate reads it. A silently truncated fan-out would read as "we checked
  everything".
- The demo's rules are a toy taxonomy. On a first run most files land as
  `unknown`, which correctly produces a low-confidence manifest and an evidence
  pane that says the rules are the problem. That is the right answer, and
  rejecting is the right response.
