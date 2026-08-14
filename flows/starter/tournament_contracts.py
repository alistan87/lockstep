"""Custom contract for `tournament-judge.tg.json`, resolved via the flow's
`contracts_module` field (SPEC §5).

Why not a built-in: the judge's product is a SELECTION, and none of the
built-ins carries one honestly — a Verdict's `reason` is prose, and a prose
winner cannot drive the deterministic publish step or the pick gate. This is
also the starter set's worked example of `contracts_module`: the path in the
flow is repo-root-relative, so keep this file beside the flow and update the
field if you move them.

The driver states this model's shape inside the judge's prompt (generated
from this class, so prompt and validator cannot drift) and validates the
result against it. The comments here are for the human reader.
"""

from __future__ import annotations

from pydantic import BaseModel


class TournamentPick(BaseModel):
    schema_version: str = "1.0"
    winner: str | None  # the winning candidate's NODE ID; null = no answer met the bar
    ranking: list[str]  # every candidate id, best first (including the winner, when set)
    rationale: str  # why the winner won and what the losers missed, quoting the answers
