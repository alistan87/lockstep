"""Adjudicated Finding[] + a findings LEDGER -> Verdict with cross-round
lifecycle (upstream-response-ow07-feedback S2+G5, the adjudicated-review
programme).

The convergence problem this closes: open-ended reviewers re-audit from
scratch every round, every self-graded major is terminal at a raw severity
gate, and nothing remembers what round N-1 decided — the OW-07 "moving
goalpost" loop. The programme's split: reviewers discover, ONE adjudicator
re-grades, and THIS PROGRAM owns the ledger file. The model proposes
findings; the program does the bookkeeping — so "an accepted-risk
disposition cannot be silently removed by a later model response" is true by
construction, not by prompt.

Usage from a flow (the ledger path must sit inside this gate node's
`spec.writes` — the engine's scope quarantine covers the program too):

    ["python", "-m", "lockstep.gates.ledger_check",
     "--node", "adjudicate", "--ledger", "reviews/x-ledger.json",
     "--at", "major"]

Ledger schema (schema_version 1.0), owned by this gate and by nobody else:

    {"schema_version": "1.0", "round": 2, "last_input_digest": "<sha256>",
     "entries": [{"id": "<12 hex>", "category": ..., "file": ..., "claim": ...,
                  "severity": ..., "state": "new" | "persisting" |
                  "reported-resolved" | "accepted-risk",
                  "first_round": 1, "last_seen_round": 2,
                  "evidence": ..., "fix_hint": ..., "disposition": ""}]}

Rules, all mechanical:

- **Identity** is sha256 over normalized (category, file, claim), 12 hex. A
  re-worded claim is a new id — the known weakness; the adjudicator's task
  text should tell it to keep claims stable across rounds.
- **Nothing is ever deleted.** An active (new/persisting) entry absent from
  this round's adjudication becomes `reported-resolved` with a mechanical
  disposition naming the round. One that returns later goes back to
  `persisting`, disposition recording the return — history, visible.
- **`accepted-risk` is a HUMAN state.** Only a person editing the ledger file
  (attributably, in git) sets it; it must carry a non-empty `disposition` or
  this gate blocks; a re-raised finding matching one stays accepted-risk —
  the standing decision holds until a human changes it — and only records
  the round it was seen again.
- **Idempotent re-runs.** Shell gates always re-run (§0.1.7); the same
  adjudication input (by digest) re-emits the same verdict without bumping
  the round or touching the file, so resume revalidation cannot inflate
  history.
- **Fail closed, write nothing.** Malformed findings, an unreadable ledger,
  an unknown state, an unattributed accepted-risk: the verdict blocks and
  the ledger file is left exactly as it was.
- **The verdict** blocks on ACTIVE (new/persisting) entries at or above
  `--at`. `accepted-risk` and `reported-resolved` never gate; they are
  the memory that stops the loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from ._common import SEVERITIES, emit, finding, resolve_node_result

STATES = ("new", "persisting", "reported-resolved", "accepted-risk")
ACTIVE = ("new", "persisting")


def _norm(value) -> str:
    return " ".join(str(value or "").lower().split())


def stable_id(f: dict) -> str:
    key = f"{_norm(f.get('category'))}|{_norm(f.get('file'))}|{_norm(f.get('claim'))}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _entry_finding(e: dict) -> dict:
    """A ledger entry re-emitted in the Finding shape, lifecycle in evidence."""
    return finding(
        e.get("severity", "blocker"), e.get("category", "?"), e.get("file", "?"),
        e.get("claim", "?"),
        f"id {e.get('id')}; state {e.get('state')}; first seen round "
        f"{e.get('first_round')}; " + str(e.get("evidence") or ""),
        str(e.get("fix_hint") or ""),
    )


def _fresh_ledger() -> dict:
    return {"schema_version": "1.0", "round": 0, "last_input_digest": "", "entries": []}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.ledger_check")
    ap.add_argument("--at", default="major", choices=list(SEVERITIES),
                    help="block on ACTIVE ledger entries at or above this severity")
    ap.add_argument("--ledger", required=True,
                    help="the ledger file this program owns (inside the gate's writes)")
    ap.add_argument("--node", default=None,
                    help="node id whose result.json holds the adjudicated Finding[]")
    ap.add_argument("path", nargs="?", default=None, help="explicit findings-file path")
    ns = ap.parse_args(argv)
    if bool(ns.node) == bool(ns.path):
        ap.error("pass exactly one of --node <id> or a findings-file path")

    if ns.node:
        p, problem = resolve_node_result(ns.node)
        if problem:
            return emit([problem], "")
    else:
        p = Path(ns.path)
    try:
        findings = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return emit([finding("blocker", "gate-error", str(p),
                             "could not read adjudicated findings", str(e),
                             "inspect the adjudicator's phase dir")], "")
    if not isinstance(findings, list):
        return emit([finding("blocker", "gate-error", str(p),
                             "result is not a Finding array",
                             type(findings).__name__,
                             "inspect the adjudicator's phase dir")], "")
    malformed = [f for f in findings
                 if not isinstance(f, dict) or f.get("severity") not in SEVERITIES]
    if malformed:
        # Same fail-closed rule as block_on_severity: an unknown severity must
        # not slip under the threshold — and must not enter the ledger either.
        return emit([finding(
            "blocker", "gate-error", str(p),
            f"{len(malformed)} finding(s) with missing or unknown severity",
            json.dumps(malformed[:3], ensure_ascii=False)[:500],
            "fix the adjudicator to emit the Finding contract's severities")], "")

    ledger_path = Path(ns.ledger)
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return emit([finding(
                "blocker", "gate-error", str(ledger_path),
                "the ledger is unreadable — refusing to overwrite history",
                str(e), "repair or remove the ledger file, attributably")], "")
        bad_state = [e for e in ledger.get("entries", [])
                     if e.get("state") not in STATES]
        if bad_state:
            return emit([finding(
                "blocker", "gate-error", str(ledger_path),
                f"{len(bad_state)} ledger entr(y/ies) with unknown state",
                json.dumps(bad_state[:3], ensure_ascii=False)[:500],
                f"states are {', '.join(STATES)}")], "")
        unattributed = [e.get("id") for e in ledger.get("entries", [])
                        if e.get("state") == "accepted-risk"
                        and not _norm(e.get("disposition"))]
        if unattributed:
            # The Sol acceptance test, enforced where a prompt cannot be:
            # accepting a risk is a decision, and decisions carry their reason.
            return emit([finding(
                "blocker", "gate-error", str(ledger_path),
                "accepted-risk without a disposition is not attributable",
                f"entries: {', '.join(map(str, unattributed))}",
                "add WHO accepted the risk and WHY to each entry's disposition")], "")
    else:
        ledger = _fresh_ledger()

    canonical = json.dumps(findings, sort_keys=True, ensure_ascii=False)
    input_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    if input_digest != ledger.get("last_input_digest"):
        round_no = int(ledger.get("round", 0)) + 1
        by_id = {e["id"]: dict(e) for e in ledger.get("entries", []) if e.get("id")}
        incoming: set[str] = set()
        for f in findings:
            fid = stable_id(f)
            incoming.add(fid)
            e = by_id.get(fid)
            if e is None:
                by_id[fid] = {
                    "id": fid, "category": f.get("category", ""),
                    "file": f.get("file", ""), "claim": f.get("claim", ""),
                    "severity": f["severity"], "state": "new",
                    "first_round": round_no, "last_seen_round": round_no,
                    "evidence": f.get("evidence", ""),
                    "fix_hint": f.get("fix_hint", ""), "disposition": "",
                }
            elif e["state"] == "accepted-risk":
                e["last_seen_round"] = round_no  # seen, recorded, still decided
            else:
                if e["state"] == "reported-resolved":
                    e["disposition"] = (f"returned in round {round_no} after being "
                                        f"reported resolved")
                e.update(severity=f["severity"], state="persisting",
                         last_seen_round=round_no,
                         evidence=f.get("evidence", ""),
                         fix_hint=f.get("fix_hint", ""))
        for e in by_id.values():
            if e["id"] not in incoming and e["state"] in ACTIVE:
                e["state"] = "reported-resolved"
                e["disposition"] = f"absent from the round {round_no} adjudication"
        ledger = {
            "schema_version": "1.0", "round": round_no,
            "last_input_digest": input_digest,
            "entries": sorted(by_id.values(),
                              key=lambda e: (e["first_round"], e["id"])),
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")

    keep = SEVERITIES[: SEVERITIES.index(ns.at) + 1]
    active = [e for e in ledger["entries"]
              if e["state"] in ACTIVE and e.get("severity") in keep]
    counts = {s: sum(1 for e in ledger["entries"] if e["state"] == s) for s in STATES}
    tally = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    return emit(
        [_entry_finding(e) for e in active],
        f"no active findings at or above '{ns.at}' — ledger round "
        f"{ledger['round']}: {tally or 'empty'}",
        f"{len(active)} active finding(s) at ledger round {ledger['round']} "
        f"({tally}) - fix and resume",
    )


if __name__ == "__main__":
    sys.exit(main())
