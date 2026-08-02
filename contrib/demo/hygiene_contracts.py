"""Custom output contracts for the repo-hygiene demo flow.

Referenced from the flow as `contrib/demo/hygiene_contracts.py:ManifestEntry`.
The contract is the seam that keeps the second standing principle of the work
order true — **the model authors the manifest; deterministic code executes it.**
An agent never moves, renames, or edits a file; it emits typed entries, and a
Python engine (or, in this demo, a validator) decides whether they are legal.

Every field here is validated by the driver before the value is allowed
downstream, so a malformed manifest costs one corrective re-spawn rather than a
bad edit to somebody's repo.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

# NOTE: deliberately NO `from __future__ import annotations` here.
# A contracts module is loaded by FILE PATH (importlib spec_from_file_location),
# so it is not registered in sys.modules under an importable name. With
# postponed annotations every type is a string, and pydantic then cannot find
# the module namespace to resolve `Literal` — the model stays "not fully
# defined" and every spawn fails validation. Eager annotations avoid the
# problem entirely. Same reason `Optional[str]` appears instead of `str | None`.


class ManifestEntry(BaseModel):
    """One proposed disposition for one file. `flag` is a first-class outcome:
    "a human should look" must always be cheaper to say than a guess, or the
    model will guess."""

    model_config = {"extra": "forbid"}

    path: str = Field(description="repo-relative path of the file being dispositioned")
    action: Literal["keep", "move", "rename", "inject_okf", "move+inject_okf", "flag"]
    target_path: Optional[str] = Field(
        default=None, description="destination for move/rename; null otherwise")
    okf_type: Optional[str] = Field(
        default=None, description="document type this file should carry")
    confidence: Literal["high", "low"]
    rule_ref: Optional[str] = Field(default=None, description="rule id this follows, if any")
    why: str = Field(max_length=200, description="one line, <=200 chars")
