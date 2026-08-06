"""Deterministic gate library (B1): tested programs instead of one-liners.

Each module is a shell-gate body invoked as

    python -m lockstep.gates.<name> <args>

from a `kind: "shell"` gate node. Conventions, uniform across the library:

- stdlib only (pydantic stays the package's only runtime dependency, and these
  run in whatever python the flow's argv names);
- exactly one Verdict JSON object printed to stdout — the §8.3 stdout fallback
  channel — and exit 0 whether the verdict is "pass" or "block": a blocking
  verdict is a RESULT, not a failure. Nonzero exits are reserved for miswired
  invocations (bad flags), which should fail the node loudly;
- unreadable or malformed INPUTS become blocker findings, not crashes: a gate
  that dies where it should block leaves the run undiagnosable;
- policy stays in the flow file: thresholds, section lists, and paths arrive
  as argv where the author and the reviewer can see them. These modules are
  tools, not policy.

Shipped in the package rather than contrib/ so that starter flows work against
ANY target repo: `python -m lockstep.gates.x` resolves wherever lockstep is
importable, while a contrib/ path exists only in this repo's checkout.
"""
