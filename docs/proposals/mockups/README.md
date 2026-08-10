---
type: notes
title: Trace page mockups and renders
description: The pre-build design target for the trace page, and a snapshot of what the shipped renderer actually emits. Open the shipped one; the mockup is design history.
resource: docs/proposals/mockups/README.md
---
# Trace page — the mockup, and the real thing

| file | what it is |
|---|---|
| `trace-page-shipped.html` | **Open this one.** A snapshot of what `contrib/mission_server.py` actually emits, over a synthetic run built to exercise every state the page can draw: a healed step with two intervals and a superseded segment, a waiting approval with its evidence quoted, a skipped step, a note, and real cost figures. |
| `trace-page-shipped.png` | The board, as a browser renders it. |
| `trace-page-shipped-timeline.png` | The same page with its script stripped, which is how both views render at once — the timeline and its table twin are visible together. Doubles as proof the no-JS fallback is real. |
| `trace-page.html` + `.png` | The pre-build **design target** for `PROPOSAL-sssf-adoptions.md` §4.6. Superseded, kept on purpose: rendering it is what caught four layout defects that reading the spec did not, and that is part of the record of how the design was arrived at. Not updated as the page moves. |

Where these differ, the shipped ones are right.

## Screenshotting it is a check, not a courtesy

The first render of the shipped page found five defects nothing in
`tests/test_trace_page.py` could see: a CSS escape written as `\203A` inside a
Python string (an **octal** escape — the browser got U+0083 and a literal `A`,
so every disclosure triangle was tofu), `.card>h2` failing to match the one
heading that sits inside `.cardhead`, a row fade-in that re-fired on every
refresh and flashed the whole list, a bar tip overflowing the card, and a sample
whose approval was not actually waiting so the evidence block never appeared.
Each is now pinned by a test — but a test was written *because* someone looked.

```powershell
$c = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
& $c --headless=new --disable-gpu --hide-scrollbars --user-data-dir="$env:TEMP\ls-shot" `
     --window-size=1400,1750 --screenshot="shot.png" `
     "file:///$($PWD -replace '\\','/')/docs/proposals/mockups/trace-page-shipped.html"
```

Use a throwaway `--user-data-dir` so it does not touch a real browser profile.

**Check the file actually changed.** Chrome resolves a path it cannot find to
nothing, writes no file, and still exits 0 — so a bad `--screenshot` path or a
bash-style `file:///d/...` URL (Windows Chrome wants `file:///D:/...`) leaves
the PREVIOUS png sitting there, and the next person reads a stale image as if
it were the new page. That happened three times in a row on 2026-08-10 while
regenerating this snapshot, and each time the conclusion drawn from it was
wrong. Drive it from Python and use `Path.as_uri()` plus an mtime assertion, or
check the byte size moved.

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
