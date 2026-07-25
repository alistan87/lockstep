# Lockstep spec — Revision 4 amendments

**Status: adopted.** This document is the delta from Revision 3 (`docs/SPEC.md`) and freezes the spec
for v1 implementation. Where this document and Revision 3 disagree, this document wins. Amendments
A1–A5 resolve the five pre-build review findings; M1–M7 are minor gap-fills an implementer would
otherwise have to invent silently.

---

## A1. Doctor cadence contradiction (§8.5 vs §13.3) — resolved in favor of §8.5

§13.3's sentence "README recommends `doctor` in a pre-commit hook: it is the only check that catches
harness flag drift" is **struck**. Replacement:

> README recommends running `lockstep doctor` **after any harness upgrade and on a weekly cadence**;
> it is the only check that catches harness flag drift. A pre-commit hook would spend a model
> round-trip on every commit, which is the wrong trade (§8.5).

§8.5 is unchanged and authoritative.

## A2. `when` evaluation is exempt from transitive skip (§7)

Revision 3's skip-propagation rule made `when: "{steps.x.json} == null"` unable to ever fire on a
skipped upstream unless the node was also `optional`, contradicting the stated `== null` semantics.
Resolved:

1. **`when` references never cause transitive skip.** When a node's dependencies are settled and at
   least one is `skipped`, a node **with a `when`** first evaluates it — references to skipped nodes
   resolve to the literal `null` for this evaluation. If the comparison is false, the node is
   `skipped` (ordinary `when` skip). If true, the node proceeds to step 2.
2. **Body references keep Revision 3 semantics.** If the node's `spec` (task template, `over`,
   context) references a skipped node, the node is transitively `skipped` unless `optional: true`,
   in which case it runs with `null` substituted. `when` passing does not override this.
3. A node **without** a `when` keeps Revision 3 semantics unchanged: any skipped dependency ⇒
   transitive skip unless `optional: true`.

Consequence, now stated plainly: `when: "{steps.x.json} == null"` on a non-optional node fires when
`x` was skipped, and the node runs — provided its body does not also reference `x`.

## A3. Map-item resume granularity and hashing (§9.2, §9.3, §10.2)

Map execution is resumable **per item**, not whole-node — for a harness-kind map, re-running nine
completed items to retry a tenth is a token cost the design exists to avoid.

1. **Per-item hash.** Each item execution has its own
   `item_hash = sha256(role + kind + contract + join(sorted(item_fingerprint_parts)))` where
   `item_fingerprint_parts` are produced by planning the node body with `{item}` bound to that
   item's value (so for harness kind: the item-rendered prompt, persona body, rendered argv, config
   digest). The hash-join rule of M3 applies.
2. **Node-level hash.** The map node's own `input_hash` covers the resolved `over` array
   (compact-serialized) plus the node's spec digest — it detects "the array itself changed."
3. **Records.** `PhaseRecord` gains `items: dict[str, ItemRecord]` keyed by zero-based array index
   as a string:
   ```python
   class ItemRecord(BaseModel):
       status: Literal["pending", "running", "done", "failed", "skipped"]
       input_hash: str | None = None
       result_path: str | None = None
       attempts: int = 0
       error: str | None = None
   ```
4. **Resume.** A `done` item with a matching `item_hash` is skipped and its stored result reused
   (harness/cacheable kinds only; shell items always re-run, matching §9.2). Items that are
   `failed`, stale-`running`, or `pending` re-run. Collected output order remains array order.
5. **Heal.** Per §9.4.6, a heal round re-runs **all** items: heal invalidation clears the map
   node's item records entirely. The §9.4.5 cascade likewise clears item records of invalidated
   descendant map nodes. Plain resume (no heal) keeps them.

## A4. Shell gates get no corrective re-spawn (§9.3, §9.4.3)

The single corrective re-spawn is a **harness-kind** mechanism (it re-prompts a model for
output-only correction). A `kind: "shell"` node — gate or otherwise — that emits missing, invalid,
or non-conforming JSON is deterministic: re-running it re-produces the same bytes. Therefore:

- A shell **gate** with no valid `Verdict` is an **immediate** terminal BLOCK ("no valid verdict
  emitted"), no re-spawn, no round consumed, exit 2.
- A shell **work** node with `output: "json"` failing contract validation is immediately `failed`
  (after `retry`, which covers nonzero exits/timeouts, not schema mismatch).

`retry` (process-level: nonzero exit, timeout) still applies to shell nodes; only the
schema-corrective re-spawn is harness-only.

## A5. Windows `kill_tree` is mandatory in v1 (§8.5)

The development and primary execution machine is Windows. The Revision 3 escape hatch ("refuses to
start on Windows with exit 7") would make the tool unusable where it is built, so it is
**withdrawn**: v1 **implements** the Windows branch (`CREATE_NEW_PROCESS_GROUP` at spawn;
`taskkill /T /F` on timeout) alongside POSIX (`start_new_session=True` + `os.killpg`). Test 8's
`kill_tree` case must pass on the development platform.

---

## M1. `contracts.py` added to the repository layout (§2)

`src/lockstep/contracts.py` (built-in output contracts, §5) is added to the §2 tree; §5 and §14
already depended on it.

## M2. Helper type shapes (§8.1)

Previously referenced but undefined; fixed as:

```python
class RenderCtx(BaseModel):          # everything an executor needs to plan a node
    model_config = ConfigDict(arbitrary_types_allowed=True)
    args: dict[str, str]
    results: dict[str, Any]          # node_id -> parsed result (text or JSON)
    statuses: dict[str, str]         # node_id -> status (for skip/null resolution)
    item: Any | None = None          # bound value inside a map body
    repo_root: Path
    personas_dir: Path
    phase_dir: Path                  # this node's (or item's) phase directory
    max_interp_chars: int
    config_digest: str               # digest of lockstep.toml (harness fingerprint part)
    executor_default: str | None     # resolved default stanza name

class RawResult(BaseModel):          # executor -> driver; contract validation is the driver's job
    exit_code: int
    result_text: str | None          # content of result.json/result.txt, or stdout fallback
                                     #   (post json_field unwrap, fences stripped), or None
    source: Literal["file", "stdout", "none"]
    stdout_path: str; stderr_path: str
    error: str | None = None         # spawn/timeout error, if any

class SnapshotRef(BaseModel):
    ref: str                         # GitWorkspace: tree object sha

class Decision(BaseModel):
    allowed: bool
    reason: str = ""
```

`PhaseIO` is subsumed: `RenderCtx.phase_dir` plus the `RawResult` paths cover it; `execute(work,
phase_dir)` takes the directory directly. `Workspace.restore` gains the discard destination the
§9.4.4 semantics already required:

```python
def restore(self, ref: SnapshotRef, scope: list[str], discard_dir: Path) -> None
    # paths in scope present in the baseline are checked out from it; paths in scope
    # absent from the baseline (created since) are MOVED into discard_dir, never deleted.
```

## M3. Hash-join rule (§9.2)

`join(sorted(fingerprint_parts))` is defined as: sort the parts, then join
`f"{len(part)}:{part}"` with `"\x00"`. Length-prefixing removes concatenation ambiguity
(`["ab","c"]` vs `["a","bc"]`); the NUL separator is belt-and-braces. The composed string is
UTF-8-encoded and sha256'd together with `role`, `kind`, and `contract` (empty string when absent),
each likewise length-prefixed, in that order, before the parts.

## M4. The automatic timeout/empty-result retry is additive (§9.3)

The "one automatic retry on timeout or empty result" fires **at most once per node execution**,
**in addition to** `RetrySpec` — it applies even when `retry.max == 0`. `RetrySpec` then covers
further nonzero exits and timeouts with backoff. The corrective re-spawn (schema mismatch,
harness-only per A4) remains a separate, single, output-only mechanism.

## M5. `personas/` and `flow_hash` resolution (§6, §9.2)

- `personas/` is resolved relative to the **invocation directory** (or `--repo-root` when given) —
  the same base as `cwd` in §4. It is project-owned, not package-owned.
- `flow_hash` is the sha256 of the flow file's raw bytes.

## M6. `NullWorkspace` disables external-edit detection — by design (§9.2)

`NullWorkspace.fingerprint()` is a constant, so the lineage-head comparison always matches and
external tree edits are **never detected** on non-git trees. This is accepted v1 behavior, not a
bug: without git there is no cheap content fingerprint worth trusting. `lockstep status` and the
resume banner note "workspace: null (external-edit detection off)" so the operator knows.

## M7. Lineage-head changed-path naming (§9.2)

To let the resume warning *name* changed paths (not just report a digest mismatch), the workspace
fingerprint is stored in `RunState` as digest **plus** per-path detail
(`fingerprint_detail: dict[str, str]`, path → content hash, gitignore-honoring, >1 MB files
skipped) for the lineage head only. The warning diffs current detail against stored detail and
prints added/removed/changed paths. `PhaseRecord.workspace_fingerprint` remains digest-only.

---

## Test-list deltas

- Test 6 gains the A2 cases: `when == null` fires on a skipped upstream without `optional`; a
  passing `when` does not rescue a body reference to a skipped node.
- Test 8's map cases gain A3: per-item skip on resume (done items not re-run), heal clears item
  records (all items re-run).
- Test 4 gains A4: a shell gate emitting invalid JSON is terminal BLOCK with **zero** re-spawns.
- Test 8's `kill_tree` case must pass on Windows (A5).

*End of Revision 4 amendments.*
