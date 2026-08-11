---
type: guide
title: The torture flows — driving the engine's failure paths on purpose
resource: flows/demo/torture/README.md
---
# The torture flows

Six flows that make something go wrong on purpose, so the engine paths that
only exist when something goes wrong can be tested at all.

```powershell
python contrib\torture_suite.py                  # all six; zero tokens, ~90s
python contrib\torture_suite.py --only heal      # substring filter
python contrib\torture_suite.py --keep           # leave the temp repos to inspect
```

## Why these exist

`contrib/replay_suite.py` proves that recorded runs still replay. But a
recording only ever contains the path a run actually took, and the paths worth
worrying about — healing, rollback, cascade invalidation, the corrective
re-spawn, write-scope quarantine, timeouts — are the ones a healthy run never
takes. They were covered by unit tests against a fake executor, and end-to-end
by nothing.

So the harness is a **script**, not a model: `contrib/demo/scripted_agent.py` is
wired in as an ordinary executor stanza. The driver cannot tell the difference —
same prompt file, same footer, same §8.3 result channel, same contract
validation, same retry, same hashing — but its behaviour is stated in the
node's own task text:

```
SCENARIO: heal-after:2
ARTIFACT: torture/app.txt
EXPECT_RESPAWN: heal
```

Zero tokens, about a minute, repeatable, and the scenario is part of the prompt
so it lands in the input hash like any other prompt content.

## The six

| Flow | Exit | What it proves |
|---|---|---|
| `torture-heal` | 0 | a gate blocks twice, heals its target, and passes on the third round; the tree is rolled back between rounds; a completed descendant is re-run by the cascade; both blocked attempts are preserved as patches and the discarded artifacts are moved aside, not deleted |
| `torture-heal-exhausted` | 2 | rounds run out ⇒ terminal block, dependents not run, reason recorded |
| `torture-contract` | 0 | malformed JSON against a contract ⇒ exactly one corrective re-spawn, carrying the original prompt AND the validation error |
| `torture-quarantine` | 3 | a write outside `spec.writes` is detected inside the node's lock, preserved as a patch, removed from the tree, and the node fails naming it |
| `torture-timeout` | 3 | a hung child is killed at `timeout_s` and the failure says so |
| `torture-resume` | 0 | the driver is killed mid-node; a PLAIN `resume` reclaims the stale lock, does not re-run the completed node, re-runs the interrupted one, and finishes |

## Two things that make it a test rather than a demo

**The agent asserts on its own prompt.** A node marked `EXPECT_RESPAWN: heal`
checks that every invocation after the first carries the gate's findings, and
exits 9 if it does not. A heal round that loses its findings cannot converge,
and from the outside it looks exactly like one that needed another round — so
without this it would show up as a mysterious extra round, not a defect. The
same holds for `EXPECT_RESPAWN: corrective`.

Note the assertion is opt-in per node, and that is not laziness: a node merely
INVALIDATED by the cascade is correctly re-spawned with its original prompt and
no heal block — only the heal *targets* receive findings. Asserting on every
node would have failed the cascade probe for behaving correctly.

**The suite asserts on the run directory, not the exit code.** A flow can exit
2 for the wrong reason. Each scenario also checks how many times a node was
really invoked (`scripted-invocations.jsonl` in the phase dir, which survives
both attempt rotation and heal rollback), which events were journalled, and
which artifacts the engine left behind.

Each scenario runs in its own throwaway git repo with its own generated
`lockstep.toml`, so the suite never touches the tree it is launched from.

## The resume scenario, specifically

`torture-resume` is the one with a custom driver, because it has to interrupt
the driver rather than wait for it. It starts the run, waits until `slow` has
**actually begun** — on the node's own invocation record, never on a sleep,
because a timing guess would make a crash-recovery test flaky in exactly the
way it must not be — then `kill_tree`s the whole process group and runs a
**plain `resume`**. It drives the driver through a plain `Popen`, not
`proc.spawn`, so no Job Object is involved and the Windows branch is the bare
`taskkill /T /F` path; the job object's own guarantees are regressed in
`tests/test_lifecycle.py`, not here.

Plain is the point. The killed run leaves its lockfile behind, and
`acquire_lock` clears it only because the recorded pid is dead *on this host*
(SPEC §10.3); a cross-host lock still needs `--force-unlock`. That is what
makes the boot protocol's rule — *lock pid dead ⇒ a plain resume is safe* —
a mechanical fact rather than advice.

What it then asserts: `first` ran **once**, because its input hash had not
moved and resume served it from the record rather than re-billing it; `slow`
ran twice, because it never completed; `last` was reached; and the lockfile is
gone at the end. Only the first invocation of `slow` sleeps — after a resume the
node has to re-run anyway, and making the second pass wait again would add
latency to a point already made.

## Keeping it honest

The suite passed on its first run, which is exactly the situation that warrants
suspicion. It was mutation-tested, and every mutation was detected:

| Mutation | What should break |
|---|---|
| agent always writes a GOOD artifact | 5 failures across the two heal scenarios — `build ran 1x, expected 3`, `0 heal-round events`, `0 preserved attempt patch(es)` … |
| `HEAL_MARKER` made unmatchable | the agent's own assertion fires: *a re-spawn of 'build' did not carry the gate's findings* |
| `torture-resume`'s slow node made fast | `slow ran 1x, expected 2` — nothing was there to interrupt |
| the resume step skipped after the kill | `slow ended 'running'`, `last ended 'pending'`, `the lockfile survived` |

Do the same after changing anything here — an assertion that cannot fail is
not one.
