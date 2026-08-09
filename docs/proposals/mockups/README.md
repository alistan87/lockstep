---
type: notes
title: Trace page mockups and renders
description: The pre-build design target for the trace page, and a snapshot of what the shipped renderer actually emits. Open the shipped one; the mockup is design history.
resource: docs/proposals/mockups/README.md
---
# Trace page — the mockup, and the real thing

| file | what it is |
|---|---|
| `trace-page-shipped.html` | **Open this one.** A snapshot of what `contrib/mission_server.py` actually emits, over a synthetic run built to exercise every state the page can draw: a healed step with two intervals, a running step, a waiting approval with its evidence, a skipped step, a note, and real cost figures. |
| `trace-page.html` + `.png` | The pre-build **design target** for `PROPOSAL-sssf-adoptions.md` §4.6. Superseded, kept on purpose: rendering it is what caught four layout defects that reading the spec did not, and that is part of the record of how the design was arrived at. Not updated as the page moves. |

Where the two differ, the shipped one is right.

## The snapshot is a snapshot

It is generated, not authored, and it will drift as the page changes. It carries
no authority — the page is the page, and `tests/test_trace_page.py` is what
holds it to its promises. Regenerate after a visual change:

```powershell
.venv\Scripts\python.exe docs\proposals\mockups\make-shipped-sample.py
```

The generator builds its run directory in a throwaway temp dir and renders
against an **empty repo root**, because `contrib/session_spend.py` reads the
orchestrator's own transcript and a committed file must not carry one. It
asserts the output contains no local path before writing.

## What it will not show you

Anything that only exists in a browser: the poll, the hover and focus hints,
the board↔timeline switch, the drawers opening, the offline notice. Those need
the real thing —

```powershell
python contrib\mission_server.py        # loopback, GET only
```
