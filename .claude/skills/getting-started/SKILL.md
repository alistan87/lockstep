---
name: getting-started
description: First-run setup for a new lockstep machine — verify the install mechanically, fix what is broken in plain language, and reach a first successful run. Use when someone has just received this repo, when `lockstep doctor` fails, or when a domain expert asks "is this working?".
---

# Getting started on a new machine

You are helping someone who may not be a programmer, on a machine the author
of this repo will never see. Two consequences shape everything below:

1. **Check, never assume.** "Assume we have a similar setup" is how a first
   session dies twenty minutes in, on a missing flag, with a stack trace. Run
   the checks and read the output.
2. **Stop at the first hard failure.** Proceeding into a run that will fail
   later and less legibly is worse than stopping now with a clear sentence.

## Step 1 — the free checks

```powershell
.venv\Scripts\lockstep.exe doctor --setup
```

Spends nothing, needs no config, and is safe to paste back to whoever is
supporting you: it reports machine facts and check results only — never repo
contents, never anything from inside your data.

| Line | Means | Do |
|---|---|---|
| `[FAIL] pwsh` | PowerShell 7 missing | Install PowerShell 7; the panes need it |
| `[FAIL] runs/ gitignored` | **stop here** | `runs/` holds prompts, diffs and model output. Add `runs/` to `.gitignore` before anything runs |
| `[FAIL] Deliverables/` | can't write finished work out | Fix the folder permission, or pick another folder |
| `[FAIL] personas/` | flows naming a persona will fail | Copy `personas/` from the repo |
| `[warn] wezterm` | no panes | Everything still works; you get a plain status loop instead of the cockpit |
| `[warn] cost-fields.toml` | no token numbers | Copy `contrib/cost-fields.toml.example` to `contrib/cost-fields.toml`; spend still shows tasks and time |

## Step 2 — the executor config

`lockstep.toml` is local to this machine and gitignored. Start from the
template and edit the argv to match what is actually installed here:

```powershell
.venv\Scripts\lockstep.exe init          # writes ./lockstep.toml
```

The flags in the template were verified on the author's machine, not this one.
Check them (`pi --help`, `claude --help`, `copilot --help`) — **a renamed flag
is a config edit, never a code change.** Two facts worth knowing before you
wonder whether something is broken:

- **copilot-cli has no JSON mode.** Its nodes will always report `no envelope`
  in cost views. That is a property of the harness, not a fault, and nothing
  you do will change it. You still get task counts and wall time.
- **`readonly: true` needs `readonly_argv` in the stanza.** A typical pi stanza
  has none, so flows using readonly nodes fail verification there. Remove the
  flag or add the argv.

## Step 3 — the paid check

```powershell
.venv\Scripts\lockstep.exe doctor
```

Spends one small model call per configured stanza. It is the **only** check
that catches harness flag drift, so run it after any harness upgrade and about
weekly. If a stanza fails here, no flow using it can work — fix it before
going further.

A clean run writes `runs\doctor-record.json`. From then on, `lockstep run`
prints one reminder line when that record is missing, older than a week, or a
stanza changed since the last successful probe — you do not have to remember
the cadence; the run start tells you. (Advisory only: it never blocks
anything.)

## Step 4 — a first run that costs nothing

```powershell
.venv\Scripts\lockstep.exe verify flows\starter\clarify-gate.tg.json
.venv\Scripts\lockstep.exe run flows\demo\repo-hygiene-demo.tg.json --dry-run
```

`verify` is free and catches malformed flows with named error codes (add
`--lint` for advisory anti-pattern warnings — they never change the exit
code). `--dry-run` prints the wave plan and spawns nothing. Only after both
look right should anyone spend tokens.

Then the real thing, in the cockpit:

```powershell
.venv\Scripts\lockstep.exe run flows\demo\repo-hygiene-demo.tg.json --arg "area=docs"
```

## Step 5 — the cockpit

Double-click `contrib\start-cockpit.cmd`. That is the only way a domain expert
starts or restarts the system, and it is the same double-click after a crash:
it scans for unfinished runs and says, mechanically, whether each is safe to
resume. Nobody has to judge that.

While a run is going:

```powershell
pwsh -File contrib\cockpit.ps1 -RunDir runs\<run-dir>          # panes
python contrib\cost_report.py --compact runs\<run-dir>         # spend right now
python contrib\quiescent.py runs\<run-dir>                     # safe to hand over?
```

## What to do when something is wrong

- **A flow fails verification** → the error codes are named and it lists all of
  them at once. `/flow-authoring` explains the grammar.
- **A run exits nonzero** → `/debug-run`. Exit `2` is a gate block (normal, read
  the verdict), `6` is an approval rejection (normal, including the non-TTY
  auto-reject the cockpit relies on), `8` is a held lock.
- **A file operation fails intermittently** → this is a known machine quirk on
  Windows with AV in the path. Retry once before investigating.
- **You cannot tell whether it is broken or just slow** → the ACTIVITY pane's
  heartbeat means blank never means dead. If the heartbeat is moving, it is
  working.
- **A node re-ran that you expected to be cached (and re-billed)** →
  `lockstep explain runs\<run-dir> <node>` names which recorded input moved
  (prompt, config stanza, heal text, steering, a context file). Free,
  read-only.
- **You want to know a run needs you without watching it** →
  `pwsh -File contrib\attention.ps1 -RunDir runs\<run-dir>` fires a toast when
  a decision is waiting, a step fails, or the run stops.

## The one thing to say to a non-programmer

Nothing here can lose paid work. Closing the laptop, closing a pane, or a
crashed assistant all leave the run recoverable from the same double-click —
the run and the assistant are separate processes, and either can die without
taking the other with it.
