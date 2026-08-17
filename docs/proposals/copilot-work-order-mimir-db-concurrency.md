---
type: plan
title: "Work order (for Copilot, work repo): MIMIR DuckDB concurrency — audit and fix"
description: Phased audit-and-fix runbook to prepare MIMIR's DuckDB databases (ledger, vector/FTS knowledge, ontology) for multiple concurrent lockstep runs. Written to be executed by a coding agent in the work repo with no prior context. One phase per PR; the audit PR ships before any fix. Rev 2 — reworked after adversarial review (reader locks block writers, versioned part-manifests for the ledger, fail-loud outbox applies).
resource: docs/proposals/copilot-work-order-mimir-db-concurrency.md
status: draft
---

# Work order: MIMIR DuckDB concurrency

**Where this file goes:** copy it into the work repo (suggested:
`docs/runbooks/mimir-db-concurrency.md`) and point the coding agent at it —
either as the body of an agent task, or referenced from
`.github/copilot-instructions.md`. Execute **one phase per PR, in order**.
Phase 0 produces only a report; nothing is changed until it has been reviewed
by a human.

**Why this exists.** We are moving to multiple concurrent lockstep runs
(separate git worktrees, separate drivers). DuckDB's file locking: a
**read-write open requires that no other process has the file open at all** —
read-only opens take shared locks that block a writer, and multiple read-only
processes can share a file only while no writer holds it. Lockstep has **no
cross-run mutex**, and lockstep's caching means a cached/seeded node never
executes, so any effect produced *inside* a cacheable node silently does not
happen on a warm-started lineage. Today, stray Python processes/servers are
already observed holding locks on one or more MIMIR databases. All of that
must be fixed before fleets are safe.

The lockstep mechanisms this document names (`spec.reads` hashing file
content as `path|sha256`; the `{steps.ID.output}` reference form; `--seed`
serving recorded results without re-materialising a node's files) are
verified against the lockstep source (`src/lockstep/reads.py`,
`src/lockstep/interpolate.py`, `src/lockstep/seed.py`). The work repo is a
mirror; if it has drifted, re-verify there before relying on them.

## Hard rules (apply to every phase)

1. **One writer per file.** No design may put two read-write processes on
   the same `.duckdb` file. This is enforced by convention plus the Phase 3
   holder-file backstop, not by any mechanism — the degraded path when the
   convention is violated is Phase 3's retry-queueing plus Rule 4's
   idempotency, which is survivable but must never be *planned* for.
2. **No harness (agent) node opens DuckDB read-write.** Harness nodes write
   *files* (outbox artifacts) at stable, worktree-relative paths, **and their
   recorded result includes an outbox manifest** (relative path + sha256 per
   artifact). Only **shell nodes** — which lockstep always re-runs and never
   caches or seeds — apply changes to a database, and an apply **verifies
   every manifest entry (existence and hash) and exits nonzero on any miss**.
   Rationale: `--seed` serves a hash-matched harness node's recorded result
   without re-creating its files, so a warm-started lineage would otherwise
   no-op silently; the fail-loud apply turns that into a visible failure, and
   `--force-stale <producer>` is the documented way to re-materialise.
   (`--replay` is different and fine: it serves *every* node including shell
   nodes — replayed runs are deliberately effect-free.)
3. **Canonical database files and ledger parts are immutable once
   published.** Writers publish *new* versions/files; the only mutable shared
   objects are tiny pointer files, replaced atomically (write temp +
   `os.replace`, retried on `PermissionError` — AV interference is a known
   quirk on these machines). Never rename over or delete a file another
   process may have open without tolerating the sharing violation.
4. **Every merge/apply step is idempotent, keyed by run id** (anti-join on
   `run_id`, or `INSERT OR REPLACE`). A healed, resumed, or re-run merge must
   not double-apply.
5. **No new runtime dependencies** without explicit human sign-off. Prefer
   stdlib. (DuckDB itself is already present; nothing else is assumed.)
6. **Do not touch lockstep engine code** (`src/lockstep/`) from this runbook.
   Everything here is MIMIR-side: flows, servers, scripts, docs.
7. Full test suite green before each PR is opened. If the repo's agreement is
   TDD, follow it: failing test first for every behavioural change.

---

## Phase 0 — Audit (report only; changes nothing)

**Deliverable:** `docs/runbooks/mimir-db-audit.md` containing the four tables
below. Open it as its own PR and stop.

### 0.1 Every DuckDB open

Search (adjust globs to the repo layout):

```
grep -rn "duckdb.connect"        --include="*.py"
grep -rn "\.duckdb"              --include="*.py" --include="*.json" --include="*.toml"
grep -rn "read_only"             --include="*.py"
```

Table columns: **file:line | database (ledger / knowledge / ontology / other) |
mode (rw / read_only / unspecified=rw) | process lifetime (per-call / node /
holder / server) | verdict (legit writer / should be read-only / should use
outbox)**.

`connect(path)` with no `read_only=True` **is** a read-write open — classify
it as rw even if the code only ever reads.

### 0.2 Every long-lived process

Inventory servers and background holders: FastAPI/Flask/uvicorn services,
schedulers, the MIMIR background DuckDB holder(s), anything launched by a
lockstep node that outlives the node. For each: **what it opens, in what mode,
who starts it, who stops it, what happens to its lock if it is killed**.
This table is expected to explain the observed stray locks — name the
offender(s) explicitly.

### 0.3 Every hard-coded database path

Search the lockstep flows (`*.tg.json`), personas, prompts, configs, and
scripts for literal DB paths. Table: **file:line | path literal | how it
should be supplied instead (flow arg / env var)**.

### 0.4 Write-inside-cacheable-node violations

For each flow node that causes a DB write (directly or via a script it runs):
**flow | node id | node kind | cacheable? | violates rule 2?** Any harness
node that ends in a DB write is a finding.

**Acceptance:** a human has read the audit and agreed on the classification
before Phase 1 starts.

---

## Phase 1 — Read-only hygiene

For every open classified *should be read-only* in 0.1: add
`read_only=True`. For every server in 0.2 that only serves queries: it opens
read-only, and (after Phase 5) opens *by version pointer*.

**What this fixes, and what it does not.** This stops servers from holding
the *write* lock — after this phase, no query server can be the reason a
writer's data is at risk, and stray-lock diagnosis gets simpler. It does
**not** yet let a writer in: a read-only open still holds a shared lock that
blocks any read-write open of the same file. Until Phase 5 moves readers onto
immutable version files, the designated writer can only run while the
long-lived readers are down. Plan maintenance windows accordingly; do not
"fix" this by making the writer retry forever against live readers.

**Acceptance:**
- The audit's 0.1 table re-generated: every open not in the *legit writer*
  list now shows `read_only=True`.
- A CI check that fails on any `duckdb.connect` call outside an allowlisted
  writers module (module-level allowlist, not per-line grep — a
  continuation-line `read_only=True` defeats line matching).
- With the long-lived servers running: a *second* read-only open of each DB
  succeeds (shared lock, no writer). With the servers stopped: a read-write
  open succeeds. Both checks scripted, both outputs pasted into the PR.

---

## Phase 2 — Parameterize every database path; define the outbox

Replace every literal from 0.3 with a parameter:

- **Flow-visible paths** (what a node reads/queries): flow args, interpolated
  into the node — so the path is part of the node's hashed input.
- **Run-specific paths** (a per-run delta DB, a per-run outbox dir): supplied
  via environment or derived from the run/worktree — **never** interpolated
  into a cacheable node's prompt/argv, or the cache can never hit.
- Canonical env names, defined once and documented in the runbook dir:
  - `MIMIR_LEDGER_DIR` — root of the ledger partition tree (Phase 4)
  - `MIMIR_KNOWLEDGE_DIR` — dir holding knowledge version files + `current`
    pointer (Phase 5)
  - `MIMIR_ONTOLOGY_DIR` — same layout as knowledge (Phase 5)
  - `MIMIR_OUTBOX_DIR` — per-run outbox (below)
  - `MIMIR_RUN_DELTA_DB` — per-run **scratch** DuckDB. Lifecycle: opened only
    by the run's own background holder, dies with the run's driver, and is
    **never** read by the librarian or any other run. Anything durable must
    leave it via the outbox before the run ends.

**The outbox contract** (consumed by Phases 4–6): a run's harness nodes drop
artifacts (markdown deltas, ontology-edit diffs, parquet-bound rows) under
`MIMIR_OUTBOX_DIR` at stable relative paths, and each producing node's
recorded result lists what it wrote as `path + sha256` (Rule 2). Applies
verify against that manifest and fail loudly on a miss.

**Acceptance:** the 0.3 grep (scoped to code, flows, and configs — exclude
`docs/`, which legitimately contains path examples, including this file)
returns zero literals outside the one config module that defines defaults;
two simultaneous invocations of any DB-touching script can be pointed at
different files purely by env/args.

---

## Phase 3 — Lock hygiene: holder files, who-holds, preflight

1. **Holder files.** Any process that has *successfully* opened a DB
   read-write then writes `<db>.holder.json` `{pid, started, purpose,
   run_dir?}`; on exit it removes the file **only if it still names its own
   pid**. Write-after-acquire, not before — a contender that writes first and
   loses would otherwise leave a holder file naming a process that never held
   the lock, and its cleanup would delete the winner's record. (Advisory, not
   a lock — the DB's own lock is the lock.)
2. **`scripts/who_holds.py <db>`** — reads the holder file, checks pid
   liveness, prints `LIVE <pid> <purpose>` / `STALE <pid>` / `NONE`. Exit 0
   always; it reports, it does not decide. Mirrors lockstep's own
   lock/STALE vocabulary on purpose.
3. **Preflight probe** for flows that will write a DB: attempts a read-write
   open, closes immediately on success; on failure emits a named error
   quoting the holder file. It is a **diagnostic, not a reservation** — the
   lock can be taken between preflight and write (check-then-act), which is
   why item 4 exists. Its value is converting the common case (a known
   long-lived holder) into a fast, named failure at the head of the flow.
4. **Retry-until-acquired wrapper** — wraps the designated writer's *actual
   open* (bounded retries, backoff, a hard timeout that fails with the
   holder-file contents). This, not the preflight, is the serialization
   point: a transient race queues instead of failing.

**Acceptance** (two scenarios — they exercise different branches):
- *Stale holder:* start a writer, `taskkill /F` it mid-run. The OS releases
  the file lock with the process, so: `who_holds` reports `STALE <dead pid>`;
  the preflight **succeeds** (and mentions the stale holder file); a new
  writer acquires immediately and replaces the holder file.
- *Live holder:* hold a read-write open from a scratch process. The preflight
  **fails naming that holder**; the wrapper queues and acquires only when the
  scratch process releases; `who_holds` reports LIVE throughout.

---

## Phase 4 — Ledger: Parquet parts under a versioned manifest

The ledger is heavy and append-mostly — stop appending into a `.duckdb` file.
Three principles, shared with Phase 5: appends create new immutable files;
readers see only *published* state; the only mutable shared object is a
pointer.

1. Layout under `MIMIR_LEDGER_DIR`:
   ```
   ledger/<table>/incoming/part-<runid>-<n>.parquet   # appended by runs
   ledger/<table>/part-….parquet                      # admitted/compacted parts
   ledger/<table>/manifest-v<NNNN>.txt                # list of part paths, one per line
   ledger/<table>/current                             # pointer: the live manifest's filename
   ```
2. **Appenders** (concurrent, uncoordinated): write to a temp name the
   readers can never match (`part-….parquet.tmp`), fsync, `os.replace` to the
   final name in `incoming/`. New files only; no shared file is ever opened
   rw; no manifest, pointer, or metadata file is touched by an appender.
3. **Readers never glob the live tree.** A reader resolves `current`, reads
   that manifest, and queries exactly the parts it lists —
   `read_parquet([...])` from an **in-memory** DuckDB connection (or any
   private one). There is no shared views/metadata `.duckdb` for readers to
   open: a shared read-only open would block the merger's rw open, which is
   the canonical-mutable-file problem all over again. If the merger keeps a
   metadata `.duckdb` for its own bookkeeping, the merger alone ever opens it.
4. **The librarian admits and compacts.** *Admission* (cheap, frequent): list
   `incoming/`, move admitted parts out of `incoming/` (same-volume rename),
   publish `manifest-v(N+1)` including them, flip `current`. *Compaction*
   (rolls small parts into big ones): write new consolidated parts, publish
   `manifest-v(N+2)` that lists the new parts and drops the old, flip
   `current`. Parts dropped from the manifest are **garbage-collected by age,
   tolerating `PermissionError`** (a reader may still hold one open on
   Windows; skip and retry next pass). Never delete a part the live manifest
   references.
5. **Visibility is a stated property, not a bug:** appended rows become
   reader-visible at the next librarian admission, not instantly. Readers
   that peek at `incoming/` directly are accepting half-written-file and
   duplicate hazards and may not do so in production paths.
6. **Cache-correctness fingerprint:** flows whose *cached* nodes read the
   ledger declare the pointer file in `spec.reads`
   (e.g. `"reads": ["ledger/<table>/current"]`) — lockstep hashes declared
   reads as `path|content-sha256`, so the node's cache busts exactly when a
   new manifest is published. (Equivalent alternative where `spec.reads` is
   awkward: a shell node — always re-runs — cats the pointer, and the cached
   node interpolates `{steps.<id>.output}`.) The reader then queries **the
   manifest version that was interpolated/declared into its input**, not a
   re-resolution of `current` at execution time — otherwise a publish between
   hashing and execution makes the record misdescribe what was read.
7. One-time migration: export the existing ledger tables to the layout;
   verify row counts and a checksum query match before deleting anything —
   and keep the old file until Phase 6 validation passes.

**Acceptance:** two concurrent append jobs + one manifest-following reader +
one librarian admission running simultaneously: zero errors of any kind
(lock, half-file, or duplicate-row — assert the reader's row count matches
admitted data exactly); queries over the published manifest match the
pre-migration checksum plus the admitted appends; a compaction pass under a
live reader leaves row counts identical before and after the pointer flip.

---

## Phase 5 — Knowledge (VSS/FTS) and ontology: versioned build-and-swap

VSS/FTS indexes must live inside a `.duckdb` file, so version the file:

1. Layout, applied **identically** to both stores:
   `MIMIR_KNOWLEDGE_DIR/knowledge-v<NNNN>.duckdb` + `current` (a text pointer
   file containing `knowledge-v<NNNN>.duckdb`), and
   `MIMIR_ONTOLOGY_DIR/ontology-v<NNNN>.duckdb` + `current`.
2. **Readers** resolve the pointer, open that version read-only. A version
   file, once pointed at, is never written again — reader locks can never
   block a writer, because the writer only ever creates the *next* version.
3. **Runs never write these DBs.** A run that produces new knowledge emits
   markdown/doc deltas into its outbox (Phase 2 contract, manifest + hashes).
   A run that proposes ontology edits emits a diff artifact the same way.
4. **The indexer** (librarian-owned, serialized): collect outbox deltas →
   verify them against their producing nodes' manifests (fail loudly on a
   miss — Rule 2) → copy current version → apply + rebuild VSS/FTS → write
   `knowledge-v<N+1>.duckdb` → fsync → atomically replace the pointer (write
   temp + `os.replace`, retry on `PermissionError`) → record which run-id
   deltas are included (idempotency key).
5. Old versions are garbage-collected by age, tolerating sharing violations
   (skip, retry next pass) — a reader may still hold one open.
6. Ontology merge conflicts are **domain decisions**: when the indexer
   detects one, it stops and emits an evidence artifact for the approval flow
   instead of guessing.
7. **Cache-correctness, same rule as Phase 4 item 6:** reader nodes declare
   the pointer in `spec.reads` (or interpolate a shell node's cat of it), and
   then open **exactly the version string that entered their hashed input** —
   never re-resolve `current` at execution time.

**Acceptance:** a publish completes while three readers hold the old version;
readers pick up v+1 on their next open; re-running the indexer with the same
outbox is a no-op (idempotency); a forced conflict produces the evidence
artifact and no partial publish; an outbox entry deleted after its producer
ran makes the indexer fail loudly, not skip silently.

---

## Phase 6 — The merge lane, end-to-end validation

1. Package indexer + ledger librarian (admission/compaction) +
   ontology-merger as **one flow** (or one script) — "the librarian" — with
   the Phase 3 preflight at its head. It is the *only* thing that opens any
   canonical `.duckdb` read-write or touches a manifest/pointer.
2. **Startup backstop:** the librarian refuses to start if any of its target
   holder files reports LIVE (Phase 3 semantics) — the mechanical guard
   behind Rule 1's convention. Two librarians launched by mistake therefore
   stop at startup; if the race slips past even that, the degraded path is
   Phase 3 queueing + Rule 4 idempotency (correct but slow — never planned
   for).
3. The orchestration layer (pi at work) runs the librarian **in a single
   lane, serialized** — lockstep's exclusive tokens are per-driver and cannot
   provide cross-run exclusion. Concurrent worker runs only ever: read
   pointer versions, read manifest-listed parquet, write their own
   outbox/delta/incoming files.
4. **Validation, all concurrent:** two lockstep runs in separate worktrees
   appending ledger parts + emitting knowledge deltas, one query server up
   (read-only, pointer-following), then one librarian pass. Zero lock errors;
   both runs' data present exactly once; `who_holds` reports NONE for every
   canonical DB at the end.
5. Only after the item-4 validation passes: delete the pre-migration ledger
   file kept in Phase 4.

**Definition of done for the whole order:**
- [ ] No read-write `duckdb.connect` outside the librarian's allowlisted
      module (CI-checked at module level)
- [ ] No literal DB path in code/flows/configs outside the config module
      (CI-checked; docs excluded)
- [ ] No harness node causes a DB write; every apply is a shell node,
      idempotent, and verifies its producer's outbox manifest (fail-loud)
- [ ] Canonical files and parts immutable; readers follow pointers/manifests,
      never the live tree; publishes are new versions with atomic pointer flips
- [ ] Ledger appends are temp-then-replace parquet files in `incoming/`;
      visibility via librarian admission; caches fingerprinted by the
      manifest pointer through `spec.reads`
- [ ] Holder files (write-after-acquire, delete-own-pid-only) + `who_holds`
      + preflight + retry wrapper in place; both Phase 3 acceptance scenarios
      pass
- [ ] Phase 6 concurrent validation documented with its actual output
