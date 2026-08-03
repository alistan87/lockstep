---
type: report
title: AUDIT DOCS 2026 07 26 (2026-07-26)
resource: docs/audits/AUDIT-DOCS-2026-07-26.md
---
## Verdict: PASS

**Reason:** All five findings are documentation drift confirmed against the current tree. None are blockers — the one `major` finding is a frontmatter description whose own agent body states the correct authority order, so no agent following these docs would run a broken command or take a materially wrong action.

## Upheld Findings

| Severity | File | Claim |
|---|---|---|
| major | `.claude/agents/spec-auditor.md:3` | Frontmatter `description` scopes the audit to SPEC.md + AMENDMENTS-r4.md only, contradicting the agent's own body and CLAUDE.md's r6 > r5 > r4 > SPEC authority order. |
| minor | `.claude/skills/debug-run/SKILL.md:23` | Says shell nodes write only `stdout.log`/`stderr.log` "overwritten in place on retry," but r5 A4 rotates prior attempts and shell nodes also write `pid.txt` — misdirecting readers away from existing evidence. |
| minor | `.claude/agents/run-diagnostician.md:3` | Description lists `wait-for-reset` (not a real command) and presents `--force-unlock`/`--fresh` as standalone commands instead of flags on `resume`/`run`. |
| minor | `.claude/skills/flow-authoring/SKILL.md:8` | Names only AMENDMENTS-r4.md as governing the grammar, contradicting CLAUDE.md (r4/r5/r6 all adopted) and the same file's own line 71, which cites r5. |
| nit | `.claude/skills/flow-authoring/SKILL.md:35` | Labels `ProgressEvent`/`SteerMessage` "reserved for v2," though r6 adopted both and they're used on live paths (`roles.py`, `cli.py`). |

## Recommended Actions

- `.claude/agents/spec-auditor.md`: reword description to "...against docs/spec/SPEC.md as amended by docs/AMENDMENTS-r4/-r5/-r6.md (later revision wins)."
- `.claude/skills/debug-run/SKILL.md`: update shell-node artifact description to mention `pid.txt` and r5 A4 attempt-rotation.
- `.claude/agents/run-diagnostician.md`: correct the description's recovery list to `(resume / resume --force-unlock / run / run --fresh / wait-then-resume)`.
- `.claude/skills/flow-authoring/SKILL.md:8`: update grammar reference to cite r4, r5, and r6 (later wins).
- `.claude/skills/flow-authoring/SKILL.md:35`: reword to reflect that `ProgressEvent`/`SteerMessage` are adopted r6 shapes, not v2-reserved.
