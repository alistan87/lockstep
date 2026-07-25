# Instruction-File Audit Report

**Verdict:** PASS

**Reason:** No blockers survived adjudication. Both claimed majors were discarded on evidence — `resume --flow` exists (`src/lockstep/cli.py:386`, with the flow_hash refusal at `:233`), and `readonly-unenforced` is the verbatim slug the verifier emits (`taskgraph.py:368`). Four documentation nits remain; none would cause an agent to run a broken command or take a wrong action.

## Upheld Findings

| Severity | File | Claim |
|---|---|---|
| nit | `.claude/skills/flow-authoring/SKILL.md:32` | Enumerated `contract` resolution forms omit the bare-name form resolved via flow-level `contracts_module`, implying custom contracts must always use `module:Name`. |
| nit | `.claude/skills/flow-authoring/SKILL.md:41` | The healing-gate/git-workspace rule is grouped under "Rules verification WILL enforce (§6)," but the verifier only emits a WARNING (`heal-rollback-nongit`) — exit stays 0 on a non-git tree; run-time exit 7 is already stated correctly. |
| nit | `CLAUDE.md:31` | Module map omits `policy.py` (home of `AllowAllPolicy` / `ACTOR_LOCAL_USER`), pointing readers only at `protocols.py` for the Policy seam. |
| nit | `.claude/agents/run-diagnostician.md:4` | Grants unrestricted Bash while prose promises read-only behavior; sibling `spec-auditor` backs the same promise structurally by omitting Bash/Write/Edit. Adjudicated down from major — Bash is required for step 1 (`lockstep status`) and no wrong/broken command results. |

## Recommended Actions

- Add the bare-name/`contracts_module` resolution form to `.claude/skills/flow-authoring/SKILL.md` near line 32.
- Reclassify the `heal-rollback-nongit` bullet in `SKILL.md` as a verification WARNING that hardens into an exit-7 run-time refusal, rather than grouping it with §6 hard errors.
- Add `policy.py` (AllowAllPolicy, actor `local-user`) to the module map in `CLAUDE.md`.
- Narrow the prose in `.claude/agents/run-diagnostician.md` to state Bash is scoped to read-only lockstep subcommands (`status`, `verify`, `render`) only.
