"""Custom output contracts for the factory flows (`contracts_module` of every
flow under flows/factory/). Same discipline as the built-ins in
src/lockstep/contracts.py: the driver validates results against these — no
model is trusted to self-report conformance."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# --- research-report (D5) -------------------------------------------------------

class SourceEntry(BaseModel):
    id: str  # "S1", "S2", ... — the citation tokens the report must use
    path: str
    fingerprint: str  # content digest: per-item caching invalidates on EDITS


class SourceManifest(BaseModel):
    schema_version: str = "1.0"
    sources: list[SourceEntry]


class SourceClaim(BaseModel):
    claim: str  # one sentence, in the extractor's words
    quote: str  # the source's own words backing it


class SourceNote(BaseModel):
    schema_version: str = "1.0"
    id: str
    path: str
    claims: list[SourceClaim]


class OutlineSection(BaseModel):
    title: str
    sources: list[str]  # the [S#] ids this section may cite
    intent: str = ""


class Outline(BaseModel):
    schema_version: str = "1.0"
    sections: list[OutlineSection]


# --- codemod pair (D2) ----------------------------------------------------------

class ChangeOrder(BaseModel):
    schema_version: str = "1.0"
    file: str
    fingerprint: str  # of the file the order was written AGAINST (staleness gate)
    anchor: str  # where in the file (a quoted line or symbol, not a line number)
    change: str  # what to do, precisely enough that another agent can apply it
    risk: str = ""


# --- triage-intake (D3) ---------------------------------------------------------

class ReportBatch(BaseModel):
    schema_version: str = "1.0"
    reports: list[str]



class TriageRecord(BaseModel):
    schema_version: str = "1.0"
    report: str  # the incoming report, verbatim or summarised
    reproduced: Literal["yes", "no", "blocked"]
    repro_command: str = ""  # the exact command run, as evidence
    severity: Literal["blocker", "major", "minor", "nit"]
    component: str
    notes: str = ""


# --- harness-bakeoff (D4) -------------------------------------------------------

class ScoreCard(BaseModel):
    schema_version: str = "1.0"
    stanza: str
    task: str
    score: int  # 0-10, judged
    rationale: str
