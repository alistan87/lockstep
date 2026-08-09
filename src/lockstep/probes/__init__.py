"""Observation programs for shell nodes: run something, report what happened,
never fail the node.

The sibling of `lockstep.gates`, and the distinction is the point:

- a **gate** DECIDES. It emits a `Verdict` and the driver branches on it.
- a **probe** OBSERVES. It emits text describing a fact about the workspace,
  and something downstream — usually a readonly harness node — judges it.

Probes exist because `spec.readonly` has to remove every write vector to be
worth anything, and shell execution is a write vector. A reviewer that cannot
run `git diff` cannot review a diff; a diagnostician that cannot run the repro
cannot observe the failure. Putting that one command in a shell node instead
gives the judgement node its input as DATA, and buys three things on the way:
the observation is deterministic, it is hashed and cached like any other node,
and it survives in the run directory as evidence.

Every probe: prints to stdout, **always exits 0** (a command that failed is an
observation, not a broken node), and never writes to the workspace.
"""
