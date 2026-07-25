"""lockstep — a harness-agnostic driver for headless coding agents.

Executes a taskgraph (*.tg.json): a declarative DAG whose nodes are run by
pluggable executors (headless agent harnesses, plain subprocesses). See
docs/SPEC.md (revision 3) and docs/AMENDMENTS-r4.md (adopted delta).
"""

__version__ = "0.1.0"

FORMAT_VERSION = "1.0"

# Exit codes, frozen (SPEC §3).
EXIT_OK = 0
EXIT_GATE_BLOCK = 2
EXIT_NODE_FAILED = 3
EXIT_BUDGET = 4
EXIT_VERIFY = 5
EXIT_APPROVAL_REJECTED = 6
EXIT_CONFIG = 7
EXIT_LOCKED = 8
