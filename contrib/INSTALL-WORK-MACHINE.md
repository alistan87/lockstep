# Installing the lockstep cockpit on the work machine

For a machine with **WezTerm + pwsh + pi.dev + GitHub Copilot** and an existing
repo holding proprietary data. Assumes nothing about that repo except that it is
a git repo.

Two things are being installed, and they go to different places:

| What | Where | Why |
|---|---|---|
| `lockstep` (the driver) | a venv | a Python package; never touches your data |
| the cockpit (`contrib/`, `flows/`, `personas/`) | **inside your work repo** | the scripts resolve paths relative to the repo root they sit in |

---

## 1. Install the driver

```powershell
cd <your-work-repo>
python -m venv .venv
.venv\Scripts\python.exe -m pip install lockstep-0.3.0-py3-none-any.whl
.venv\Scripts\lockstep.exe --help
```

Python 3.11+. The only runtime dependency is `pydantic`.

## 2. Copy the cockpit into your repo

From this bundle, copy these directories to your work repo root:

```
contrib\      flows\      personas\      .claude\skills\
```

`contrib\cockpit.ps1` computes the repo root as its own parent directory, so it
must live at `<work-repo>\contrib\`, not somewhere central.

## 3. Ignore the run directory — do this before the first run

```powershell
Add-Content .gitignore "`nruns/`nDeliverables/`nlockstep.toml`ncontrib/cost-fields.toml"
```

**`runs/` holds rendered prompts, diffs, and raw model output over your
proprietary data.** On this machine that is the single most important line in
the setup. `lockstep doctor --setup` fails if it is not ignored.

## 4. Configure the executors

```powershell
.venv\Scripts\lockstep.exe init        # writes ./lockstep.toml
```

Then edit it. **Verify every flag against what is actually installed** — a
renamed flag is a config edit, never a code change:

```powershell
pi --help ; copilot --help
```

A starting point for this machine:

```toml
default = "pi"

[executors.pi]
argv = ["pi.cmd", "-p", "--mode", "json", "--no-session", "{prompt}"]
prompt_via = "argv"

# The work order's `bulk` class: high-volume classification.
[executors.bulk]
argv = ["copilot", "-p", "{prompt}"]
prompt_via = "argv"

# The `strong` class: review gates and adversarial passes.
[executors.strong]
argv = ["pi.cmd", "-p", "--mode", "json", "--no-session", "{prompt}"]
prompt_via = "argv"
```

Two facts worth knowing before you file a bug:

- **copilot-cli has no JSON mode.** Its nodes report `no envelope` in every cost
  view, permanently. That is a property of the harness, not a fault, and you
  still get task counts and wall time.
- **`readonly: true` needs `readonly_argv` in the stanza.** A plain pi stanza
  has none, so flows using readonly nodes fail verification. Remove the flag or
  add the argv.

Copy `contrib\cost-fields.toml.example` to `contrib\cost-fields.toml` to get
token numbers where the harness reports them (pi does; copilot cannot).

## 5. Verify

```powershell
.venv\Scripts\lockstep.exe doctor --setup   # free, no model calls
.venv\Scripts\lockstep.exe doctor           # one small model call per stanza
```

`--setup` output is safe to send to whoever is supporting you: machine facts and
check results only, never repo contents.

Expect `pwsh profile: N profile(s) present`. If your profile auto-starts pi in a
project directory, that is fine — cockpit panes run with `-NoProfile` and are
unaffected. It only means a terminal **you** open by hand is not a plain shell.

## 6. WezTerm workspace (optional)

Paste the block between the BEGIN/END markers in `contrib\wezterm-lockstep.lua`
into `~\.wezterm.lua` before `return config`, and set `LOCKSTEP_REPO` to your
work repo path. `CTRL+SHIFT+ALT+L` then opens a `lockstep` workspace laid out as
CHAT / ACTIVITY / MISSION.

It is a **paste-in snippet, not a module**: WezTerm's config sandbox aborts
silently on `dofile`/`require` of a user module, and a config that failed that
way looks identical to one that simply did not take effect.

## 7. First run

```powershell
.venv\Scripts\lockstep.exe verify flows\demo\repo-hygiene-demo.tg.json
.venv\Scripts\lockstep.exe run    flows\demo\repo-hygiene-demo.tg.json --dry-run
```

Both are free. **Do step 4 first**: `verify` resolves every node's executor
against `lockstep.toml`, so before that file exists it fails with
`no-executor-stanza` on every harness node — which looks like a broken flow and
is really a missing config. Then, for real, detached:

```powershell
.venv\Scripts\lockstep.exe run flows\demo\repo-hygiene-demo.tg.json `
  --arg "area=docs" --arg "max_ambiguous=3" < NUL
```

**The rules in `contrib\demo\hygiene_catalog.py` are a toy taxonomy written for
the lockstep repo.** On your corpus almost everything will land as `unknown`,
which correctly produces a low-confidence manifest and an evidence pane saying
the rules are the problem. That is the honest first result. Write real rules for
your repo before treating any output as a proposal worth applying.

## 8. The loop, once something is running

```powershell
pwsh -File contrib\cockpit.ps1 -RunDir runs\<run> -Approve   # hand over a decision
python contrib\quiescent.py runs\<run>                       # is it safe to hand over?
python contrib\cost_report.py --compact runs\<run>           # spend right now
python contrib\retrospect.py runs\                           # friction across runs
```

Rules that matter:

- **Run detached, with non-TTY stdin** (`< NUL`). A run in a bare pane sits at
  the approval prompt forever instead of auto-rejecting, and dies with the pane.
- **Exit 2 is a gate block** (often a question for you). **Exit 6 is an approval
  handoff**, not a failure.
- **Never hand over without `quiescent.py` exiting 0.** Whatever is runnable at
  that moment runs inside the human's own terminal.
- **Answers to clarification gates are effectively permanent** — they fold into
  the prompt and the hash. Confirm before sending; true retraction means
  `--fresh`, which re-bills the lineage.

## 9. Recovery

Double-click `contrib\start-cockpit.cmd`. It scans for unfinished runs and
applies the mechanical rule: lock pid dead ⇒ a plain `resume` is safe; lock pid
alive ⇒ the run outlived the assistant, reattach and do **not** unlock. Cold
start and the morning after a crash are the same double-click.

Closing a laptop, a pane, or the assistant never loses paid work: the run and
the assistant are separate processes and either can die without the other.

## 10. Known local quirks

- Transient `PermissionError` on file replaces and git object writes (AV in the
  path). Retry once before investigating; `resume` absorbs it.
- Session limits can kill a long assistant session. The detached run survives;
  the boot protocol reattaches.
- Windows caps a command line at ~32k chars. With `prompt_via = "argv"`, very
  large interpolations fail to spawn (exit 127). Switch that stanza to
  `prompt_via = "stdin"` if you hit it.
