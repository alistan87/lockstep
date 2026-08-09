# Installing the lockstep cockpit on the work machine

For a machine with **WezTerm + pwsh + pi.dev + GitHub Copilot** and an existing
repo holding proprietary data. Assumes nothing about that repo except that it is
a git repo.

**Read first, depending on who you are:**

- **`docs/guides/COCKPIT-FOR-DOMAIN-EXPERTS.md`** — two pages, no jargon. Give this to
  the person who will actually use the system. It is the only document they
  need.
- **`docs/guides/THEORY-OF-OPERATIONS.md`** — what lockstep is and why it behaves the
  way it does: caching, gates, healing, budgets, resume. Read this before
  authoring or adapting any flow.
- **`docs/guides/COCKPIT-THEORY-OF-OPERATIONS.md`** — the operating manual for the
  assistant that drives lockstep on their behalf. Point your agent session at
  it before the first session.

Two things are being installed, and they go to different places:

| What | Where | Why |
|---|---|---|
| `lockstep` (the driver) | a venv | a Python package; never touches your data |
| the cockpit (`contrib/`, `flows/`, `personas/`) | **inside your work repo** | the scripts resolve paths relative to the repo root they sit in |

**Building the bundle (do this on the source machine, not the work machine):**

```powershell
.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
.venv\Scripts\python.exe contrib\build_bundle.py --version 0.3.1
```

That produces `dist\lockstep-cockpit-<version>.zip` — wheel, `contrib/`,
`flows/`, `personas/`, `docs/`, the skills, and this guide. It refuses to build
if an input is missing rather than shipping a bundle with quiet gaps, and it
never carries `runs/`, `lockstep.toml`, or `cost-fields.toml`. **Rebuild it
after any change to the repo**; a stale zip is the most common way the work
machine ends up on documentation that no longer matches the code.

---

## 1. Install the driver

```powershell
cd <your-work-repo>
python -m venv .venv
.venv\Scripts\python.exe -m pip install lockstep-0.3.1-py3-none-any.whl
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

There is a second, quieter reason. The engine excludes the run directory from
the write-scope check and from heal rollback, but **not** from the lineage
fingerprint — so an un-ignored `runs/` makes every resume warn about external
edits to its own `state.json`, and the warning that should mean "somebody
touched your tree" becomes noise you learn to skip.

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

# WRITERS. `--mode json` gives the cost views their usage numbers, and costs
# these nodes nothing: they answer in a file, which the driver reads first.
# NO readonly_argv here -- see `pi-review`.
[executors.pi]
argv = ["pi.cmd", "-p", "--mode", "json", "--no-session", "{prompt}"]
prompt_via = "stdin"                       # not argv: see §11 on the 32k cap

# JUDGEMENT NODES (reviewers, arbiters, triage, estimation, planning).
# The one difference is that `--mode json` is GONE, which is what leaves stdout
# usable as the result channel -- a readonly node has no write tool, so stdout
# is the only channel it has. Measured against pi 0.83.0, `--mode json` is an
# event stream ending in {"type":"agent_settled"}, and that is what the driver
# would read as the answer. The trade: these nodes report `no envelope` in the
# cost views.
[executors.pi-review]
argv = ["pi.cmd", "-p", "--no-session", "{prompt}"]
prompt_via = "stdin"
readonly_argv = ["--tools", "read,grep,find,ls,submit_result"]

# The work order's `bulk` class: high-volume classification.
[executors.bulk]
argv = ["copilot", "-p", "{prompt}"]
prompt_via = "argv"

# Same as `pi`, plus the in-session write-scope guard. Use it for nodes that
# declare `spec.writes`; the guard does not gate a node that declares none.
# A writer stanza, so no readonly_argv here either.
[executors.pi-guarded]
argv = ["pi.cmd", "-p", "--mode", "json", "--no-session",
        "--extension", "contrib/pi-extension/lockstep-guard.ts", "{prompt}"]
prompt_via = "stdin"
```

Pointing a `readonly` node at a `--mode json` stanza is caught at `verify`
time (`readonly-unenforced`, free) precisely because those stanzas declare no
`readonly_argv`. That is deliberate: the alternative is a runtime failure you
pay for.

`prompt_via = "stdin"` from the start, not `"argv"`: a corrective re-spawn's
prompt is several times the original, and Windows caps a command line near 32k
(`verify --lint` warns about it). Switching later re-bills every node on the
stanza, because argv composition is part of the input hash.

Two facts worth knowing before you file a bug:

- **copilot-cli has no JSON mode.** Its nodes report `no envelope` in every cost
  view, permanently. That is a property of the harness, not a fault, and you
  still get task counts and wall time.
- **`readonly: true` needs `readonly_argv` in the stanza** — point those nodes
  at `pi-review` above (`spec.executor`). pi's `--tools` is an argv-visible
  allowlist, which is what SPEC §6.11 asks for; name the node's answer tool in
  it, since the allowlist covers extension tools too, and naming a tool pi does
  not have is harmless. Worth doing rather than dropping the flag: on a
  request-metered plan your spend is round trips, and a node that cannot edit
  cannot spend one trying.
- **A 429 on Copilot usually means quota, not a blip.** Set
  `"retry": {"max": 0}` on nodes using subscription-backed stanzas and resume
  when quota returns; the default 60s backoff just burns two more requests
  against the same wall.
- **The scope guard attaches per stanza**, from argv:
  `--extension contrib/pi-extension/lockstep-guard.ts` (the `pi-guarded`
  stanza above). Live-verify it on this machine after install and after any pi
  upgrade — **with the guarded stanza, or the probe tests the wrong thing**:

  ```powershell
  .venv\Scripts\lockstep.exe run flows\starter\pi-guard-smoke.tg.json `
    --executor-default pi-guarded --fresh
  ```

  `--fresh` because editing the extension does not change the argv that names
  it, so a plain re-run reuses the cached result and skips the probe.

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
python contrib\mission_server.py                             # the MISSION page, loopback
```

The MISSION page is the surface to give the domain expert if they would rather
look at a browser than a terminal: board → timeline → step → raw record, all
server-rendered so it works with JavaScript off, and no route that writes. It
binds to loopback; `--host` requires an explicit value and prints a warning
naming what it exposes, because `runs/` is exactly the sensitive material this
setup exists to keep ignored — and, when one exists, `rejection.txt`, which is
the human's own words.

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

## 10. If the work repo is a FastAPI + Jinja2 + DuckDB application

Lockstep drops into that stack, but three boundaries decide whether it stays
trustworthy afterwards. None of them is a preference; each protects a guarantee
something else already relies on.

**The driver stays dependency-free; the cockpit may not.** `pydantic` is the
only runtime dependency of `src/lockstep/`, and that is what lets the driver run
anywhere without negotiating with the host application's environment. FastAPI is
pydantic v2 + starlette, so there is no version conflict — but make the boundary
explicit: **`src/lockstep/` keeps the rule, `contrib/` (or a `cockpit/` package)
may use the house stack.** A dependency that reaches the driver is a dependency
every future machine has to satisfy before a run can start.

**The page ports; the no-write guarantee does not port by itself.** `mission_view.py`
and `cost_report.py` are pure projections of the run directory — a FastAPI app
imports them unchanged and the CSS and markup move into Jinja2 templates as they
are. Jinja2 actually helps the standing rule that every word comes from
`mission_view`, because a template receives already-worded values and "no
template contains a status word or a time format" is greppable. Two things to
carry over deliberately:

- Today "no route writes" is *the absence of the code*: `mission_server` has no
  `do_POST`, and a test asserts the method does not exist. Under FastAPI a
  router is one decorator away, so replace that mechanism rather than dropping
  it — `assert {m for r in app.routes for m in r.methods} == {"GET"}` — and port
  the write-patching purity harness with it. It is the guarantee the whole
  cockpit is sold on.
- Use `def` routes, not `async def`. The projections are blocking file I/O
  (~40 ms for a full page render on a 40-node run); Starlette runs sync handlers
  in a threadpool, while an `async def` doing that work stalls the event loop
  for every other request.

**DuckDB is a read model, never the store.** This was decided, with reasons, in
`docs/proposals/PROPOSAL-sssf-adoptions.md` §8b — written about SQLite and true
verbatim of DuckDB: the journal is hash-chained and `verify-trace` is a
guarantee, so a mirror is a second store that can disagree with the one thing a
reader can check, and the failure is silent. `CLAUDE.md` says the same about the
`Store` protocol: do not design against it.

What DuckDB *is* good for here is everything downstream of the record — spend
and cost analytics across many runs, lineage rollups, a fleet view over `runs/`.
Build it as a **derived cache rebuilt from `events.jsonl` and `state.json`, safe
to delete at any moment**, and keep MISSION reading the run directory, so the
sentence the domain expert was given — *when two surfaces disagree, MISSION is
right* — stays true. One operational note: DuckDB is embedded and single-writer,
so the ingester and the dashboard need separate read-only connections.

## 11. Known local quirks

- Transient `PermissionError` on file replaces and git object writes (AV in the
  path). Retry once before investigating; `resume` absorbs it.
- Session limits can kill a long assistant session. The detached run survives;
  the boot protocol reattaches.
- Windows caps a command line at ~32k chars. With `prompt_via = "argv"`, very
  large interpolations fail to spawn (exit 127). Switch that stanza to
  `prompt_via = "stdin"` if you hit it.
