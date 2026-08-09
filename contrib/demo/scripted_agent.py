#!/usr/bin/env python
"""scripted_agent.py — a harness that does exactly what it is told to do wrong.

    argv:  scripted_agent.py            (prompt arrives on stdin)

A stand-in for a coding agent, wired into `lockstep.toml` as an ordinary
harness stanza. The driver cannot tell it from a model: same prompt file, same
footer, same §8.3 result channel, same contract validation, same retry, same
hashing. What differs is that its behaviour is a SCRIPT, so the engine paths
that only appear when something goes wrong can be driven on purpose, in
seconds, for zero tokens, repeatably.

The flow states the scenario in the node's own task text, which means it is
visible in the flow file, recorded in `prompt.txt`, and folded into the input
hash like any other prompt content:

    SCENARIO: heal-after:2          what to do, and when to stop doing it
    ARTIFACT: torture/app.txt       the file this "implementation" writes
    STRAY:    torture/escape.txt    an out-of-scope write, for the quarantine path

Scenarios:

  ok                 write the artifact, answer, succeed
  heal-after:N       write a BAD artifact for the first N invocations, then a
                     GOOD one — the gate blocks until then, so the flow heals
  bad-json:N         emit malformed output on the result channel for the first
                     N invocations (drives the corrective re-spawn)
  stray-write        write STRAY as well as ARTIFACT (drives write-scope
                     quarantine), then answer normally
  hang               sleep until the driver's timeout kills the process tree
  crash              exit non-zero without answering

**It also asserts.** On any invocation after the first, the prompt must carry
either the heal block or the corrective-re-spawn block — because a re-spawn
that lost the gate's findings is a healing loop that cannot converge, and it
would otherwise look identical to one that simply needed another round. The
agent fails loudly and says so rather than quietly trying again.

Invocations are counted in `scripted-invocations.jsonl` in the phase directory.
That file is deliberately NOT one of the names the executor rotates per
attempt, and the run directory is excluded from heal rollback — so the counter
survives both, which is what makes "this node ran three times" checkable after
the fact.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

PHASE_DIR = Path(os.environ.get("LOCKSTEP_PHASE_DIR", "."))
REPO_ROOT = Path(os.environ.get("LOCKSTEP_REPO_ROOT") or ".")
NODE_ID = os.environ.get("LOCKSTEP_NODE_ID", "?")
CONTRACT = os.environ.get("LOCKSTEP_CONTRACT", "")

COUNTER = "scripted-invocations.jsonl"

# The exact wording the driver prepends. If either of these strings moves, this
# agent's central assertion silently stops testing anything — so it asserts on
# them explicitly and the torture suite fails loudly rather than passing.
HEAL_MARKER = "A quality gate blocked with:"
CORRECTIVE_MARKER = "failed contract"
READONLY_MARKER = "You are running READ-ONLY"


def directive(prompt: str, key: str) -> str | None:
    m = re.search(rf"^\s*{key}:\s*(\S.*?)\s*$", prompt, re.MULTILINE)
    return m.group(1) if m else None


def record(entry: dict) -> int:
    """Append this invocation and return its 1-based number."""
    path = PHASE_DIR / COUNTER
    prior = 0
    if path.exists():
        prior = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    entry = {"n": prior + 1, **entry}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return prior + 1


def answer(prompt: str, text: str, *, malformed: bool = False) -> None:
    """Deliver on whichever §8.3 channel this node actually has."""
    if CONTRACT.startswith("Finding"):
        body = "{{{ not json" if malformed else "[]"
    elif CONTRACT == "Verdict":
        body = "{{{ not json" if malformed else json.dumps(
            {"findings": [], "verdict": "pass", "reason": text}
        )
    else:
        body = text
    if READONLY_MARKER in prompt:
        # No write tools: the answer goes to stdout (FOOTER_READONLY).
        sys.stdout.write(body + "\n")
        return
    name = "result.json" if CONTRACT else "result.txt"
    (PHASE_DIR / name).write_text(body, encoding="utf-8")


def write_artifact(rel: str, content: str) -> None:
    path = REPO_ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    prompt = sys.stdin.read()
    scenario = directive(prompt, "SCENARIO") or "ok"
    artifact = directive(prompt, "ARTIFACT")
    stray = directive(prompt, "STRAY")

    name, _, arg = scenario.partition(":")
    limit = int(arg) if arg.strip().isdigit() else 0

    healed = HEAL_MARKER in prompt
    corrective = CORRECTIVE_MARKER in prompt
    n = record({
        "node": NODE_ID, "scenario": scenario,
        "healed": healed, "corrective": corrective,
        "prompt_chars": len(prompt),
    })

    # The assertion this whole harness exists for. Opt-in per node, because
    # only heal TARGETS receive the findings: a node merely INVALIDATED by the
    # cascade is re-spawned with its original prompt and no heal block, which
    # is correct. Asserting on every node would have failed the cascade test
    # for doing the right thing.
    expect = (directive(prompt, "EXPECT_RESPAWN") or "").lower()
    if n > 1 and expect:
        got = {"heal": healed, "corrective": corrective}.get(expect)
        if got is False:
            sys.stderr.write(
                f"scripted_agent: invocation {n} of {NODE_ID!r} was supposed to carry the "
                f"{expect} block and does not. A re-spawn that lost the gate's findings "
                f"cannot converge, and looks exactly like one that needed another round.\n"
            )
            return 9
        if got is None:
            sys.stderr.write(f"scripted_agent: unknown EXPECT_RESPAWN {expect!r}\n")
            return 9

    if name == "hang":
        time.sleep(3600)
        return 0
    if name == "crash":
        sys.stderr.write(f"scripted_agent: crashing on purpose (invocation {n})\n")
        return 3

    if name == "stray-write" and stray:
        # Outside spec.writes: the driver must quarantine it and fail the node.
        write_artifact(stray, f"escaped from {NODE_ID} on invocation {n}\n")

    if artifact:
        good = name != "heal-after" or n > limit
        write_artifact(artifact, ("GOOD" if good else "BAD") + f"\ninvocation {n}\n")

    answer(prompt, f"{NODE_ID}: scenario {scenario}, invocation {n}",
           malformed=(name == "bad-json" and n <= limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
