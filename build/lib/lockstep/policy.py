"""AllowAllPolicy: the no-op authorization seam (SPEC §8.1, §11).

The seam exists in v1 only because it must gate execute, not decorate it —
retrofitting later would mean auditing every call site. v1 ships permissive;
`allows` is consulted at verify time AND immediately pre-execute.
"""

from __future__ import annotations

from .protocols import Decision
from .taskgraph import Node

ACTOR_LOCAL_USER = "local-user"  # the v1 constant actor (SPEC §8.1)


class AllowAllPolicy:
    def allows(self, node: Node, actor: str) -> Decision:
        return Decision(allowed=True)
