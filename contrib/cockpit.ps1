<#
.SYNOPSIS
  The domain-expert cockpit: WezTerm panes over a lockstep run dir.

.DESCRIPTION
  pwsh successor to src/lockstep/watch/wezterm-watch.sh, implementing the
  proposal's pane grammar (§A.3). Invoked by the orchestrator or by
  start-cockpit.cmd; a domain expert never types it.

  Pane roles, fixed:
    CHAT      left column   — the orchestrator (not managed here; it is the
                              pane you are already sitting in)
    ACTIVITY  right column  — tail of the running node's progress.jsonl
    MISSION   bottom, full  — DE-tier status + live spend, then the raw table
    APPROVAL  transient     — spawned only when the run is quiescent

  THE READER RULES (L-B2) ARE LOAD-BEARING, NOT STYLE. This script only ever
  reads the run dir, and it must never be the reason a run fails:
    - every file handle opens with FileShare ReadWrite,Delete so the engine can
      rename a file out from under us (it rotates per-attempt files) and
      replace state.json (atomic replace, which this machine's AV already
      makes flaky) while we hold it open;
    - poll >= 0.5s for tails, >= 1s for state;
    - a length regression means the file was replaced -> reopen;
    - *.tmp is never read;
    - every error is display-only. A view that takes the run down is worse
      than no view.

.PARAMETER RunDir
  The lockstep run directory to observe.

.PARAMETER Role
  Which pane this process IS. 'layout' (default) splits the panes and then
  becomes MISSION. 'mission' / 'activity' / 'raw' are what spawned panes run.

.PARAMETER Boot
  Recovery scan: report unfinished lineages and whether each is safe to resume.

.PARAMETER Approve
  Quiescence-check the run and, if it passes, spawn a pane that RUNS
  approve.ps1 — evidence first, then the real prompt. Nothing is ever typed
  into a pane; the human's only input is 'a' or 'r'.

.PARAMETER Follow
  Track whichever run is newest instead of being handed one, and show a
  waiting screen when there is none. This is what lets the cockpit panes exist
  before any run does, and survive the gap between segments.

.EXAMPLE
  pwsh -File contrib/cockpit.ps1 -RunDir runs/hygiene-20260802T101500Z
  pwsh -File contrib/cockpit.ps1 -Role mission -Follow
#>
[CmdletBinding()]
param(
  [string]$RunDir,
  [ValidateSet('layout', 'mission', 'activity', 'raw', 'why')]
  [string]$Role = 'layout',
  [switch]$Boot,
  [switch]$Approve,
  [string]$RunsRoot = 'runs',
  [string]$Deliverable,
  [double]$Interval = 1.0,
  [double]$SpendInterval = 10.0,
  [string]$Node,
  [string]$TitleMarker,
  [string]$Handshake,
  [switch]$NoWezterm,
  [switch]$Follow,
  [switch]$Tui
)

$ErrorActionPreference = 'Continue'
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:Python = if (Test-Path (Join-Path $script:RepoRoot '.venv\Scripts\python.exe')) {
  Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
} else { 'python' }

# --- reader primitives (L-B2) --------------------------------------------------

function Clear-Pane {
  # Clear-Host throws when stdout is redirected (no console handle) — which is
  # exactly how the manual drills and any scripted check run this script. A
  # view must never die of its own cosmetics.
  try { Clear-Host } catch { Write-Host '' }
}

function Read-RunFile {
  <#
    The single primitive every run-dir read goes through. Opens with delete
    sharing so the engine's rotation and atomic replaces keep working while a
    pane holds the file. Returns $null on any failure — callers hold their last
    good frame rather than rendering a zero.
  #>
  param([Parameter(Mandatory)][string]$Path, [int]$Retries = 2)
  for ($i = 0; $i -le $Retries; $i++) {
    try {
      if (-not (Test-Path -LiteralPath $Path)) { return $null }
      $fs = [System.IO.File]::Open(
        $Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
      try {
        $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
        return $sr.ReadToEnd()
      } finally { $fs.Dispose() }
    } catch {
      Start-Sleep -Milliseconds 150
    }
  }
  return $null
}

function Read-RunJson {
  param([Parameter(Mandatory)][string]$Path)
  $text = Read-RunFile -Path $Path
  if ([string]::IsNullOrWhiteSpace($text)) { return $null }
  try { return $text | ConvertFrom-Json } catch { return $null }
}

function Read-RunChunk {
  <#
    THE tail primitive (L-B2). Returns only the lines appended since the caller's
    offset, and advances that offset in place.

    Every incremental pane read goes through this — the ACTIVITY tail and
    Tail-RunFile both. An earlier version had ACTIVITY re-read the whole
    progress file each poll and filter duplicates by comparing against the last
    line, which reprinted every checkpoint on every tick: five copies of
    "checkpoint 1" in four seconds. That is not a cosmetic problem. The pane's
    job is to make "blank never means dead" true, and a pane that repaints its
    entire history once a second destroys a human's ability to tell new
    progress from old.

    Handles the two things the engine does underneath a reader:
      - rotation/replacement: a length regression means a NEW file, so the
        offset resets to 0 and the new content is read from the top;
      - a partial trailing line: normal on an append-only file, so it is left
        unread until its newline arrives rather than emitted as half a record.
  #>
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ref]$Offset
  )
  $out = @()
  try {
    if (-not (Test-Path -LiteralPath $Path)) { return $out }
    $len = (Get-Item -LiteralPath $Path -ErrorAction Stop).Length
    if ($len -lt $Offset.Value) { $Offset.Value = 0L }   # rotated or truncated
    if ($len -le $Offset.Value) { return $out }

    $fs = [System.IO.File]::Open(
      $Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
      [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    try {
      [void]$fs.Seek($Offset.Value, [System.IO.SeekOrigin]::Begin)
      $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
      $chunk = $sr.ReadToEnd()
      $newOffset = $fs.Position
    } finally { $fs.Dispose() }

    $lines = @($chunk -split "`n")
    if ($chunk -notmatch "`n$" -and $lines.Count -gt 0) {
      $keep = $lines[-1]
      $lines = if ($lines.Count -gt 1) { $lines[0..($lines.Count - 2)] } else { @() }
      $newOffset -= [System.Text.Encoding]::UTF8.GetByteCount($keep)
    }
    $Offset.Value = $newOffset
    $out = @($lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
  } catch { }   # display-only, always
  return $out
}

function Tail-RunFile {
  <#
    Follow a file to the end of time, emitting each new line. A thin loop over
    Read-RunChunk so there is exactly one implementation of the hard part.
  #>
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][scriptblock]$OnLine,
    [double]$PollSeconds = 0.5,
    [scriptblock]$OnIdle = $null
  )
  $offset = 0L
  $idleTicks = 0
  while ($true) {
    foreach ($ln in (Read-RunChunk -Path $Path -Offset ([ref]$offset))) {
      & $OnLine $ln
      $idleTicks = 0
    }
    if ($OnIdle) { $idleTicks++; & $OnIdle $idleTicks }
    Start-Sleep -Seconds ([Math]::Max(0.5, $PollSeconds))
  }
}

# --- DE-tier rendering ---------------------------------------------------------

# The fixed glossary. Summary-free by construction: a field mapping, no model.
# This is the DE's trust anchor, so it must never acquire a narrated branch.
$script:Glossary = @{
  'pending' = 'waiting'
  'running' = 'running'
  'done'    = 'done'
  'skipped' = 'not needed'
  'failed'  = 'stopped with a problem'
  'blocked' = 'needs you'
}

function Get-NodeLabels {
  <#
    T1.8 — human names for nodes, from a sidecar the VIEW owns.

    MISSION addressed the domain expert in engineering identifiers (`preflight`,
    `apply`) because there is nowhere else for a name to live: taskgraph `Node`
    is `extra="forbid"`, so a `title` field would be a format change to a
    surface this proposal deliberately does not touch.

    A sidecar keeps the tier mechanical — this is a file lookup, not a narrated
    branch — while leaving every flow written to date verifying unchanged. The
    lookup order is the run's own copy first (so a label change cannot rewrite
    what a completed run was displayed as), then the authoring directory.

    Absent, unreadable, or malformed ⇒ empty map ⇒ node ids, exactly as today.
    A view must never be the reason anything fails.
  #>
  param([Parameter(Mandatory)][string]$RunDir)

  $labels = @{}
  $candidates = @(Join-Path $RunDir 'flow.labels.json')

  # The authoring-side sidecar sits beside the flow file, named for it:
  # flows/foo.tg.json -> flows/foo.labels.json. state.json records the flow
  # NAME, and the run dir's flow copy records nothing about its origin path, so
  # this is a best-effort search of the flows tree by name.
  $state = Read-RunJson (Join-Path $RunDir 'state.json')
  if ($state -and $state.flow_name) {
    $flowsRoot = Join-Path $script:RepoRoot 'flows'
    if (Test-Path -LiteralPath $flowsRoot) {
      $candidates += @(
        Get-ChildItem -Path $flowsRoot -Filter "$($state.flow_name).labels.json" `
          -Recurse -File -ErrorAction SilentlyContinue |
          ForEach-Object { $_.FullName })
    }
  }

  foreach ($path in $candidates) {
    $doc = Read-RunJson $path
    if ($null -eq $doc -or -not $doc.PSObject.Properties['nodes']) { continue }
    foreach ($p in $doc.nodes.PSObject.Properties) {
      if (-not $labels.ContainsKey($p.Name)) { $labels[$p.Name] = [string]$p.Value }
    }
    if ($doc.PSObject.Properties['tiers']) {
      $script:ApprovalTiers = $doc.tiers
    }
  }
  return $labels
}

function Get-NewestRunDir {
  <#
    -Follow mode: the pane tracks whichever run is newest instead of being
    handed one.

    This is what lets the cockpit exist BEFORE a run does. A pane that must be
    given a run dir has to be created at the moment work starts, which means
    the layout is assembled by whoever launches the run — and the MISSION pane
    would blink out of existence between segments, exactly when a human is
    most likely to be looking at it for reassurance. Following the newest run
    keeps the trust anchor permanently on screen.
  #>
  param([string]$Root = 'runs')
  $root = if ([System.IO.Path]::IsPathRooted($Root)) { $Root }
          else { Join-Path $script:RepoRoot $Root }
  if (-not (Test-Path $root)) { return $null }
  $candidates = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'state.json') } |
    Sort-Object LastWriteTime -Descending
  if (-not $candidates) { return $null }
  return $candidates[0].FullName
}

function Resolve-RunDir {
  param([string]$Given)
  if ($Follow -or -not $Given) { return Get-NewestRunDir -Root $RunsRoot }
  return $Given
}

function Show-WaitingScreen {
  param([string]$Label)
  Clear-Pane
  Write-Host "$Label" -ForegroundColor Cyan
  Write-Host ('-' * 64)
  Write-Host ''
  Write-Host '  no run yet.' -ForegroundColor DarkGray
  Write-Host ''
  Write-Host '  Tell the assistant what you would like to work on;'
  Write-Host '  this pane fills in by itself once something starts.'
  Write-Host ''
  Write-Host ('-' * 64)
  Write-Host "  waiting - nothing is spending   $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor DarkGray
}

function Get-HealBudget {
  param($Flow)
  $budgets = @{}
  if ($null -eq $Flow) { return $budgets }
  foreach ($n in $Flow.nodes) {
    if ($n.heal -and $n.heal.max_rounds -gt 0) {
      foreach ($t in $n.heal.targets) { $budgets[$t] = $n.heal.max_rounds }
      $budgets[$n.id] = $n.heal.max_rounds
    }
  }
  return $budgets
}

function Get-StepsToDecision {
  <#
    T1.7 — "a decision is N steps away".

    MECHANICAL, and defined narrowly enough to be checkable: N is the count of
    nodes the single awaiting approval transitively DEPENDS ON that are not yet
    done or skipped, plus the approval itself. It is a remaining-work count over
    the recorded graph, NOT a prediction and not an estimate of time.

    Returns $null when there is no approval, when there is more than one (the
    flow is unsegmented and the number would be ambiguous), or when the flow
    copy cannot be read.
  #>
  param([Parameter(Mandatory)]$State, $Flow)
  if ($null -eq $Flow) { return $null }

  $approvals = @($State.nodes.PSObject.Properties |
    Where-Object { $_.Value.role -eq 'approval' } | ForEach-Object { $_.Name })
  if ($approvals.Count -ne 1) { return $null }

  $deps = @{}
  foreach ($n in $Flow.nodes) {
    $deps[$n.id] = @(if ($n.depends_on) { $n.depends_on } else { @() })
  }

  $seen = New-Object 'System.Collections.Generic.HashSet[string]'
  $stack = [System.Collections.Generic.Stack[string]]::new()
  foreach ($d in $deps[$approvals[0]]) { $stack.Push($d) }
  while ($stack.Count -gt 0) {
    $cur = $stack.Pop()
    if (-not $seen.Add($cur)) { continue }
    foreach ($d in $deps[$cur]) { $stack.Push($d) }
  }

  $remaining = 0
  foreach ($id in $seen) {
    $rec = $State.nodes.PSObject.Properties[$id]
    if (-not $rec) { continue }
    if ($rec.Value.status -notin @('done', 'skipped')) { $remaining++ }
  }
  $approvalRec = $State.nodes.PSObject.Properties[$approvals[0]]
  if ($approvalRec -and $approvalRec.Value.status -ne 'done') { $remaining++ }
  return $remaining
}

function Get-HeadlineLine {
  <#
    T1.7 — one line above the list.

    Rev 7 §A.3 set out to stop attention scaling with graph width, and then
    MISSION listed every node in the graph. This is the line that finishes the
    job: a domain expert reading one line should know how far along the work is,
    whether anything is spending, and how close their own turn is.

    Every element is a count over state.json. Nothing here is narrated and
    nothing is predicted.
  #>
  param([Parameter(Mandatory)]$State, $Flow)

  $recs = @($State.nodes.PSObject.Properties | ForEach-Object { $_.Value })
  $total = $recs.Count
  $settled = @($recs | Where-Object { $_.status -in @('done', 'skipped') }).Count
  $running = @($recs | Where-Object { $_.status -eq 'running' })
  $blocked = @($recs | Where-Object { $_.status -eq 'blocked' })
  $failed = @($recs | Where-Object { $_.status -eq 'failed' })
  $heals = 0
  foreach ($r in $recs) {
    if ($r.PSObject.Properties['heal_round'] -and $r.heal_round) { $heals += [int]$r.heal_round }
  }

  $parts = @("step $([Math]::Min($settled + $running.Count, $total)) of $total")
  $parts += if ($failed.Count) { 'stopped with a problem' }
            elseif ($blocked.Count) { 'needs you' }
            elseif ($running.Count) { 'running' }
            elseif ($settled -eq $total) { 'done' }
            else { 'waiting' }

  # Wall time from the run's own start stamp: the DE asks "how long has this
  # been going", which is elapsed, not the sum of node durations (that number
  # lives on the spend line and means something different).
  if ($State.PSObject.Properties['started_at'] -and $State.started_at) {
    try {
      $began = [datetime]::Parse($State.started_at, [cultureinfo]::InvariantCulture,
                                 [System.Globalization.DateTimeStyles]::AdjustToUniversal)
      $mins = [int]((Get-Date).ToUniversalTime() - $began).TotalMinutes
      $parts += if ($mins -ge 90) { "$([int]($mins / 60)) h $($mins % 60) m" } else { "$mins m" }
    } catch { }
  }
  if ($heals -gt 0) { $parts += "$heals rework round$(if ($heals -ne 1) { 's' })" }

  $toGo = Get-StepsToDecision -State $State -Flow $Flow
  if ($null -ne $toGo) {
    $parts += if ($toGo -le 0) { 'your decision is recorded' }
              elseif ($toGo -eq 1) { 'your decision is next' }
              else { "a decision is $toGo steps away" }
  }
  return ($parts -join '  -  ')
}

function Get-MissionLines {
  <#
    The DE tier. Every line is a field mapping over state.json plus the run's
    own flow.tg.json copy for denominators. Nodes may additionally drop a
    one-line phases/<node>/mission.txt, which is included VERBATIM — still
    mechanical (a file copy), so the tier keeps its trust status.

    T1.7/T1.8: a headline first, node ids replaced by sidecar labels where one
    exists, and finished work collapsed to a count. What is NEVER collapsed:
    anything running, anything needing the human, anything that failed, and
    anything that has been sent back for rework. Collapsing is applied to the
    quiet majority so the loud minority is legible — not the other way round.
  #>
  param([Parameter(Mandatory)][string]$RunDir)

  $state = Read-RunJson (Join-Path $RunDir 'state.json')
  if ($null -eq $state) { return @('(reading state...)') }
  $flow = Read-RunJson (Join-Path $RunDir 'flow.tg.json')
  $healBudget = Get-HealBudget $flow
  $labels = Get-NodeLabels -RunDir $RunDir

  $lines = @((Get-HeadlineLine -State $state -Flow $flow), '')
  $collapsedDone = 0
  $collapsedSkip = 0
  $pendingShown = 0
  $pendingHidden = 0

  foreach ($prop in $state.nodes.PSObject.Properties) {
    $id = $prop.Name
    $rec = $prop.Value
    $word = $script:Glossary[$rec.status]
    if (-not $word) { $word = $rec.status }
    $healed = $rec.PSObject.Properties['heal_round'] -and [int]$rec.heal_round -gt 0
    $isMap = $rec.items -and $rec.items.PSObject.Properties.Count -gt 0
    $hasNote = Test-Path -LiteralPath (Join-Path $RunDir "phases/$id/mission.txt")

    # The collapse rules. A node earns a full line by being loud.
    if ($rec.status -eq 'done' -and -not $healed -and -not $isMap -and -not $hasNote) {
      $collapsedDone++
      continue
    }
    if ($rec.status -eq 'skipped') { $collapsedSkip++; continue }
    if ($rec.status -eq 'pending' -and -not $healed) {
      if ($pendingShown -ge 3) { $pendingHidden++; continue }
      $pendingShown++
    }

    # Heal rounds: "sent back for rework (1 of 2)" — the denominator is the
    # flow's own budget, so the DE learns the ceiling, not just the count.
    #
    # The count is PhaseRecord.heal_round. NOT state.heal_baselines, which maps
    # a gate id to a git TREE SHA (the rollback baseline) — reading it as a
    # number throws on the first healed run.
    $rounds = 0
    if ($rec.PSObject.Properties['heal_round'] -and $null -ne $rec.heal_round) {
      $rounds = [int]$rec.heal_round
    }
    if ($rounds -gt 0) {
      $cap = if ($healBudget[$id]) { $healBudget[$id] } else { '?' }
      $word = "sent back for rework ($rounds of $cap)"
    }

    # Map nodes collapse to one counter line — attention must not scale with
    # graph width (§A.3).
    if ($rec.items -and $rec.items.PSObject.Properties.Count -gt 0) {
      $items = @($rec.items.PSObject.Properties)
      $doneCount = @($items | Where-Object { $_.Value.status -eq 'done' }).Count
      $redone = @($items | Where-Object { [int]($_.Value.attempts) -gt 1 }).Count
      $word = "$word - $doneCount of $($items.Count) checked"
      if ($redone -gt 0) { $word += ", $redone redone" }
    }

    # T1.8: the sidecar label if there is one, the node id otherwise. Never both
    # — a DE-tier line that carries an engineering identifier alongside a plain
    # name invites the reader to work out the relationship between them, which
    # is exactly the cognitive load this tier exists to remove.
    $name = if ($labels.ContainsKey($id) -and $labels[$id]) { [string]$labels[$id] } else { $id }
    if ($name.Length -gt 34) { $name = $name.Substring(0, 33) + '…' }
    $lines += ('{0,-34} {1}' -f $name, $word)

    $missionFile = Join-Path $RunDir "phases/$id/mission.txt"
    $extra = Read-RunFile -Path $missionFile
    if ($extra) {
      $first = ($extra -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
      if ($first) { $lines += ('{0,-34}   {1}' -f '', $first.Trim()) }
    }
  }

  if ($pendingHidden -gt 0) {
    $lines += ('{0,-34} {1}' -f '', "+ $pendingHidden more waiting")
  }
  if ($collapsedDone -gt 0 -or $collapsedSkip -gt 0) {
    $tail = @()
    if ($collapsedDone -gt 0) { $tail += "$collapsedDone finished" }
    if ($collapsedSkip -gt 0) { $tail += "$collapsedSkip not needed" }
    $lines += ''
    $lines += ('{0,-34} {1}' -f '', ($tail -join ', '))
  }
  return $lines
}

function Get-SpendLine {
  param([Parameter(Mandatory)][string]$RunDir, [string]$Deliverable)
  # NOT $args: that is an automatic variable in PowerShell (the unbound
  # argument list). Assigning it inside a function happens to work today and
  # is a trap waiting for the first person who adds a parameter here.
  $reportArgs = @((Join-Path $script:RepoRoot 'contrib/cost_report.py'), '--compact')
  if ($Deliverable) {
    $reportArgs += @('--runs-from', $Deliverable,
                     '--runs-root', (Join-Path $script:RepoRoot $RunsRoot))
  } else {
    $reportArgs += $RunDir
  }
  try {
    $out = & $script:Python @reportArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return @($out) }
  } catch { }
  return @('(spend unavailable)')
}

function Test-NeedsYou {
  <#
    T2.2 — the transition predicate for the notification. `blocked` is the
    engine's word for both an approval awaiting a decision and a gate that
    stopped with questions, which is exactly the set the DE needs to be told
    about.
  #>
  param($State)
  if ($null -eq $State) { return $false }
  return [bool]@($State.nodes.PSObject.Properties |
    Where-Object { $_.Value.status -eq 'blocked' }).Count
}

function Send-NeedsYouSignal {
  <#
    T2.2 — fired ONCE per transition INTO a needs-you state, never on the polls
    that merely observe it. A signal that repeats every second is an alarm, and
    an alarm gets muted.

    Deliberately payload-free. `runs/` holds prompts, diffs and model output; a
    notification is a nudge to look at the pane, not a delivery channel, and the
    moment it carries content it becomes egress that nobody approved.
  #>
  param([Parameter(Mandatory)][string]$RunName)
  try { [Console]::Beep() } catch { try { Write-Host "`a" -NoNewline } catch { } }
  try { $Host.UI.RawUI.WindowTitle = "NEEDS YOU - $RunName" } catch { }
  if ($env:LOCKSTEP_NOTIFY_URL) {
    try {
      Invoke-RestMethod -Uri $env:LOCKSTEP_NOTIFY_URL -Method Post `
        -Body "$RunName - needs you" -TimeoutSec 5 | Out-Null
    } catch { }   # display-only, like every other side effect in a view
  }
}

function Show-Mission {
  param([string]$RunDir, [string]$Deliverable)
  try { $Host.UI.RawUI.WindowTitle = 'LOCKSTEP-MISSION' } catch { }
  $env:PI_SKIP_AUTO = '1'
  Write-PaneHandshake
  $lastGood = @()
  $boundRun = $null
  $painted = $null          # T1.5: the frame currently on screen
  $spend = @('(spend unavailable)')
  $spendAt = [datetime]::MinValue
  $wasNeedingYou = $false
  while ($true) {
    $current = Resolve-RunDir -Given $RunDir
    if (-not $current) {
      if ($painted -ne '<waiting>') { Show-WaitingScreen -Label 'MISSION'; $painted = '<waiting>' }
      Start-Sleep -Seconds ([Math]::Max(1.0, $Interval))
      continue
    }
    if ($current -ne $boundRun) {
      $boundRun = $current; $lastGood = @(); $painted = $null
      $spendAt = [datetime]::MinValue; $wasNeedingYou = $false
    }
    $RunDirActive = $current
    $lines = Get-MissionLines -RunDir $RunDirActive
    if ($lines.Count -gt 0 -and $lines[0] -ne '(reading state...)') { $lastGood = $lines }
    elseif ($lastGood.Count -gt 0) { $lines = $lastGood }   # hold the last good frame

    # T1.4: spend has its own, much slower cadence. Recomputing it meant
    # spawning python once a second and re-walking every phase directory — the
    # most expensive thing in the cockpit, for the number that changes least.
    if (((Get-Date) - $spendAt).TotalSeconds -ge [Math]::Max(1.0, $SpendInterval)) {
      $spend = Get-SpendLine -RunDir $RunDirActive -Deliverable $Deliverable
      $spendAt = Get-Date
    }

    # T2.2: notify on the EDGE, not the level.
    $needsYou = Test-NeedsYou -State (Read-RunJson (Join-Path $RunDirActive 'state.json'))
    if ($needsYou -and -not $wasNeedingYou) {
      Send-NeedsYouSignal -RunName (Split-Path -Leaf $RunDirActive)
    } elseif (-not $needsYou -and $wasNeedingYou) {
      try { $Host.UI.RawUI.WindowTitle = 'LOCKSTEP-MISSION' } catch { }
    }
    $wasNeedingYou = $needsYou

    $name = Split-Path -Leaf $RunDirActive
    $frame = @("MISSION  $name", ('-' * 64)) + $lines + @(('-' * 64)) + $spend
    $key = ($frame -join "`n")

    # T1.5: repaint only when something changed. A pane that clears and rewrites
    # its whole surface once a second destroys a human's ability to tell new
    # information from old — the same reasoning that already forced ACTIVITY to
    # read incrementally — and takes the scrollback with it.
    if ($key -ne $painted) {
      Clear-Pane
      Write-Host $frame[0] -ForegroundColor Cyan
      for ($i = 1; $i -lt $frame.Count; $i++) {
        if ($i -gt ($frame.Count - $spend.Count - 1)) {
          Write-Host $frame[$i] -ForegroundColor Yellow
        } else {
          Write-Host $frame[$i]
        }
      }
      Write-Host ''
      $painted = $key
    }
    # The liveness line is rewritten IN PLACE every tick and is deliberately not
    # part of the repaint key: a clock that ticks would make every frame differ
    # from the last and defeat T1.5 entirely. It still has to be here — "blank
    # never means dead" is the promise MISSION makes, and a frozen screen with
    # no clock cannot be told apart from a dead pane.
    Write-Host ("`rupdated $(Get-Date -Format 'HH:mm:ss')  (this pane reads files; it never changes the run)   ") `
      -NoNewline -ForegroundColor DarkGray
    Start-Sleep -Seconds ([Math]::Max(1.0, $Interval))
  }
}

# --- ACTIVITY ------------------------------------------------------------------

function Get-FrontierNode {
  param([Parameter(Mandatory)][string]$RunDir)
  $state = Read-RunJson (Join-Path $RunDir 'state.json')
  if ($null -eq $state) { return $null }
  foreach ($prop in $state.nodes.PSObject.Properties) {
    if ($prop.Value.status -eq 'running') { return $prop.Name }
  }
  return $null
}

function Format-ProgressLine {
  <#
    T1.5 — render the WHOLE progress record.

    The spawn contract instructs an agent to emit {"step", "pct", "note"}
    (executors/harness.py) and `lockstep status` already renders pct — the pane
    printed `note` alone and dropped the rest on the floor.

    Nothing here is estimated. No pct ⇒ no bar; no step ⇒ no step; a bad record
    is shown raw rather than guessed at. An invented denominator on a progress
    bar is the fastest way to teach a human that this pane makes things up.
  #>
  param([Parameter(Mandatory)][string]$Line)
  try { $obj = $Line | ConvertFrom-Json } catch { return "  $Line" }

  $note = if ($obj.PSObject.Properties['note'] -and $obj.note) { [string]$obj.note }
          elseif ($obj.PSObject.Properties['message'] -and $obj.message) { [string]$obj.message }
          else { '' }

  $bar = ''
  if ($obj.PSObject.Properties['pct'] -and $null -ne $obj.pct) {
    try {
      $pct = [int][Math]::Max(0, [Math]::Min(100, [double]$obj.pct))
      $filled = [int][Math]::Round($pct / 10.0)
      $bar = ('[{0}{1}] {2,3}%' -f ('#' * $filled), ('-' * (10 - $filled)), $pct)
    } catch { }
  }
  $step = if ($obj.PSObject.Properties['step'] -and $obj.step) { "step $($obj.step)" } else { '' }

  $parts = @($bar, $step, $note) | Where-Object { $_ }
  if (-not $parts) { return "  $Line" }
  return '  ' + ($parts -join '  ')
}

function Get-StdoutLiveness {
  <#
    T1.5 — the fallback when an agent emits no progress at all.

    progress.jsonl is written BY THE AGENT, on instruction. A harness that
    ignores the instruction leaves this pane showing "working - 14 m elapsed"
    for fourteen minutes, which is the exact ambiguity the heartbeat principle
    was meant to remove: the DE cannot tell thinking from stuck.

    So: derive liveness mechanically from the file the harness cannot help but
    write. Size and mtime only — never CONTENT. Tailing stdout.log was rejected
    for good reason (JSON-mode harnesses emit nothing for minutes and then dump
    raw JSON), and nothing here reverses that decision.
  #>
  param([Parameter(Mandatory)][string]$PhaseDir)
  $best = $null
  foreach ($name in @('stdout.log', 'stderr.log')) {
    $p = Join-Path $PhaseDir $name
    try {
      if (-not (Test-Path -LiteralPath $p)) { continue }
      $f = Get-Item -LiteralPath $p -ErrorAction Stop
      if ($f.Length -le 0) { continue }
      if ($null -eq $best -or $f.LastWriteTimeUtc -gt $best.LastWriteTimeUtc) { $best = $f }
    } catch { }
  }
  if ($null -eq $best) { return $null }
  $kb = [Math]::Round($best.Length / 1KB, 1)
  $ago = [int]((Get-Date).ToUniversalTime() - $best.LastWriteTimeUtc).TotalSeconds
  if ($ago -lt 0) { $ago = 0 }
  return "still producing output - $kb KB, last write $($ago)s ago"
}

function Show-Activity {
  <#
    Tails the RUNNING node's progress.jsonl — never stdout.log. JSON-mode
    harnesses emit stdout only at the end, so a stdout tail looks hung for
    minutes and then dumps raw JSON: the opposite of reassurance. Progress
    checkpoints are one plain line each, and the heartbeat guarantees that
    blank never means dead.

    Pane-node binding with hysteresis (L-M2): once bound, this pane follows its
    node until that node stops running. It never re-points mid-flight.
  #>
  param([string]$RunDir)
  try { $Host.UI.RawUI.WindowTitle = 'LOCKSTEP-ACTIVITY' } catch { }
  $env:PI_SKIP_AUTO = '1'
  Write-PaneHandshake
  $bound = $null
  $offset = 0L          # per-bound-node read position; reset when re-pointing
  $start = Get-Date
  $heartbeatOwed = $false   # T1.6: is the cursor parked on the heartbeat line?
  while ($true) {
    $RunDirActive = Resolve-RunDir -Given $RunDir
    if (-not $RunDirActive) {
      Show-WaitingScreen -Label 'ACTIVITY'
      $heartbeatOwed = $false
      Start-Sleep -Seconds 2
      continue
    }
    if (-not $bound) {
      $bound = Get-FrontierNode -RunDir $RunDirActive
      if ($bound) {
        Clear-Pane
        $labels = Get-NodeLabels -RunDir $RunDirActive
        $shown = if ($labels.ContainsKey($bound) -and $labels[$bound]) { $labels[$bound] } else { $bound }
        Write-Host "ACTIVITY  $shown" -ForegroundColor Green
        Write-Host ('-' * 64)
        $start = Get-Date
        $offset = 0L      # a new node means a new file to read from the top
        $heartbeatOwed = $false
      } else {
        # Idle placeholders are MECHANICAL (L-M1): blank must never be
        # ambiguous between dead, thinking, and waiting-on-you.
        Clear-Pane
        Write-Host 'ACTIVITY' -ForegroundColor DarkGray
        Write-Host ('-' * 64)
        $state = Read-RunJson (Join-Path $RunDirActive 'state.json')
        $msg = 'waiting - nothing is spending'
        if ($state) {
          $statuses = @($state.nodes.PSObject.Properties | ForEach-Object { $_.Value.status })
          if ($statuses -contains 'blocked') { $msg = 'needs you - nothing is spending' }
          elseif ($statuses -notcontains 'pending') { $msg = 'segment done - nothing is spending' }
        }
        Write-Host $msg

        # T2.1 — the question card. DISPLAY ONLY: there is no input path here,
        # and the answer still travels chat -> steer -> detached resume, so
        # nothing about the human channel changes.
        #
        # What changes is WHEN the verbatim finding is visible. Rev 7 §A.3 gave
        # clarifications no pane, leaving "quote the finding verbatim" to
        # orchestrator discipline, checked after the fact by retrospect.py's
        # overlap tripwire. That is the arrangement the evidence rule already
        # rejected for approvals — a narrated relay at a decision point, audited
        # later. The card puts the original words in front of the DE at the
        # moment they answer.
        $card = Read-RunFile -Path (Join-Path $RunDirActive 'question-card.txt')
        if ($card) {
          Write-Host ''
          Write-Host $card -ForegroundColor Cyan
        }
        Start-Sleep -Seconds 2
        continue
      }
    }

    # Incremental: only lines appended since the last poll (L-B2 primitive).
    $progress = Join-Path $RunDirActive "phases/$bound/progress.jsonl"
    $fresh = @(Read-RunChunk -Path $progress -Offset ([ref]$offset))
    if ($fresh.Count -gt 0) {
      # T1.6: the heartbeat parks the cursor mid-line (it is written with
      # -NoNewline so it can be overwritten in place). Anything printed after it
      # therefore landed ON that line, concatenated onto "working - 3 m elapsed".
      # Clear the line and drop to a fresh one before writing content.
      if ($heartbeatOwed) { Write-Host ("`r" + (' ' * 78) + "`r") -NoNewline; $heartbeatOwed = $false }
      foreach ($ln in $fresh) { Write-Host (Format-ProgressLine -Line $ln) }
    }

    $elapsed = [int]((Get-Date) - $start).TotalMinutes
    $beat = "  working - $elapsed m elapsed"
    # T1.5: when the agent emits no progress records at all, say something
    # mechanical about the process rather than only about the clock.
    if ($offset -le 0) {
      $live = Get-StdoutLiveness -PhaseDir (Join-Path $RunDirActive "phases/$bound")
      if ($live) { $beat = "  working - $elapsed m elapsed - $live" }
    }
    Write-Host ("`r{0,-78}" -f $beat) -NoNewline -ForegroundColor DarkGray
    $heartbeatOwed = $true

    $still = Get-FrontierNode -RunDir $RunDirActive
    if ($still -ne $bound) { $bound = $null; $offset = 0L }   # release the binding
    Start-Sleep -Seconds 1
  }
}

# --- pane management -----------------------------------------------------------

function Test-Wezterm {
  if ($NoWezterm) { return $false }
  return [bool](Get-Command wezterm -ErrorAction SilentlyContinue)
}

# Every pane this script spawns runs with -NoProfile, and with PI_SKIP_AUTO set.
#
# Learned the hard way: a user's PowerShell profile may start an interactive
# agent in any ConsoleHost inside a project workspace. A pane spawned as
# `pwsh -NoExit` therefore does not stay a shell — the profile replaces it with
# an agent, and anything typed into that pane goes to a CHAT COMPOSER instead of
# a command prompt. A cockpit pane is infrastructure, not the operator's
# interactive shell, and must never inherit their startup customisations.
$script:PaneShell = @('pwsh', '-NoProfile', '-NoLogo')

function Get-PaneTitle {
  param([Parameter(Mandatory)][string]$PaneId)
  try {
    $panes = & wezterm cli list --format json 2>$null | ConvertFrom-Json
    $match = @($panes | Where-Object { "$($_.pane_id)" -eq "$PaneId" })
    if ($match.Count -eq 1) { return [string]$match[0].title }
  } catch { }
  return $null
}

function Wait-PaneHandshake {
  <#
    A REAL round-trip. The spawned script writes a handshake file naming the
    per-handoff marker it was given AND the pane it finds itself in
    ($env:WEZTERM_PANE, which wezterm sets inside every pane it spawns). We
    require both to match what we just created.

    Three earlier attempts at this check were each weaker in an instructive way:
      - "the pane id exists": trivially true; says nothing about the program.
      - "set a tab title, read it back": a TAB is shared by every pane in it, so
        this both proved nothing and renamed the operator's own tab.
      - "read the pane title back": racy. A pane's title follows the FOREGROUND
        process, so as soon as the script ran `lockstep resume` the title became
        'python.exe' and a correct handoff looked like a failure.

    A file written by our own script, naming the pane it is actually in, has
    none of those problems: only that script writes it, and only in that pane.
  #>
  param(
    [Parameter(Mandatory)][string]$PaneId,
    [Parameter(Mandatory)][string]$HandshakePath,
    [Parameter(Mandatory)][string]$Marker,
    [int]$TimeoutMs = 15000
  )
  $waited = 0
  while ($waited -lt $TimeoutMs) {
    if (Test-Path -LiteralPath $HandshakePath) {
      $raw = Read-RunFile -Path $HandshakePath
      if ($raw) {
        try {
          $hs = $raw | ConvertFrom-Json
          if ($hs.marker -eq $Marker -and "$($hs.pane)" -eq "$PaneId") { return $true }
          # A handshake that arrives with the WRONG pane means our script is
          # running somewhere we did not put it. That is worse than silence.
          Write-Host "handshake mismatch: expected pane $PaneId, script reports $($hs.pane)" -ForegroundColor Red
          return $false
        } catch { }
      }
    }
    Start-Sleep -Milliseconds 200
    $waited += 200
  }
  return $false
}

function New-RunnerPane {
  <#
    Split and run a script in the new pane. Returns the pane id, or $null.
    Callers must treat $null as "abort", never as "reuse some other pane".
  #>
  param(
    [Parameter(Mandatory)][string[]]$Command,
    [ValidateSet('right', 'bottom')][string]$Direction = 'right',
    [int]$Percent = 40
  )
  try {
    $argv = @('cli', 'split-pane', "--$Direction", '--percent', "$Percent", '--') + $Command
    $paneId = (& wezterm @argv 2>$null | Select-Object -First 1)
    if (-not $paneId) { return $null }
    return $paneId.Trim()
  } catch {
    return $null
  }
}

function Write-PaneHandshake {
  <#
    T1.1 — the pane end of the handshake, factored out of approve.ps1's copy so
    every cockpit pane can prove what it is.

    A pane announces the marker it was given AND the pane wezterm actually put
    it in ($env:WEZTERM_PANE). Both must match what the spawner just created.
    This is what distinguishes "a pane exists" from "our program is running in
    the pane we made", and it is the check that catches a substituted pane
    program — a shell profile, a default_prog, a wrapper.

    Written FIRST, before any work: a pane title cannot carry this signal
    because a title follows the foreground process.
  #>
  if (-not $Handshake) { return }
  try {
    @{ marker = $TitleMarker; pane = $env:WEZTERM_PANE; pid = $PID
       ts = (Get-Date).ToString('o') } | ConvertTo-Json -Compress |
      Set-Content -LiteralPath $Handshake -Encoding UTF8
  } catch { }
  if ($TitleMarker) { try { $Host.UI.RawUI.WindowTitle = $TitleMarker } catch { } }
}

function New-VerifiedPane {
  <#
    T1.1 — spawn a pane and REQUIRE it to prove what it is running.

    This replaces `Wait-PaneProgram`, which was called at the ACTIVITY spawn
    (cockpit.ps1:766) and defined nowhere in the repo. The call site was not
    merely dead: it printed a CommandNotFound error at the domain expert on
    every layout, and it meant ACTIVITY has never actually been verified while
    reading as though it had been. A verification that only exists in the name
    of a function is worse than an honest absence.

    The one asymmetry, deliberate and stated at each call site rather than
    decided here: a failed handshake on a DECISION surface must abort, while on
    a VIEW it may downgrade. A view that is not what we think it is misleads; a
    decision surface that is not what we think it is can be forged.
  #>
  param(
    [Parameter(Mandatory)][string[]]$BaseCommand,
    [Parameter(Mandatory)][string]$Marker,
    [ValidateSet('right', 'bottom')][string]$Direction = 'right',
    [int]$Percent = 40,
    [switch]$KillOnFailure,
    [int]$TimeoutMs = 15000
  )
  $handshake = Join-Path ([System.IO.Path]::GetTempPath()) "lockstep-pane-$Marker.json"
  Remove-Item -LiteralPath $handshake -ErrorAction SilentlyContinue

  $cmd = $BaseCommand + @('-TitleMarker', $Marker, '-Handshake', $handshake)
  $paneId = New-RunnerPane -Command $cmd -Direction $Direction -Percent $Percent
  if (-not $paneId) {
    return [pscustomobject]@{ PaneId = $null; Verified = $false; Saw = $null; Spawned = $false }
  }

  $ok = Wait-PaneHandshake -PaneId $paneId -HandshakePath $handshake `
          -Marker $Marker -TimeoutMs $TimeoutMs
  $saw = if ($ok) { $null } else { Get-PaneTitle -PaneId $paneId }
  if (-not $ok -and $KillOnFailure) {
    & wezterm cli kill-pane --pane-id $paneId 2>$null | Out-Null
    $paneId = $null
  }
  Remove-Item -LiteralPath $handshake -ErrorAction SilentlyContinue
  return [pscustomobject]@{ PaneId = $paneId; Verified = $ok; Saw = $saw; Spawned = $true }
}

function New-ApprovalPane {
  <#
    THE APPROVAL HANDOFF. Nothing is ever typed into a pane.

    Earlier revisions spawned a shell and PRE-TYPED the approval command into
    it, leaving the human to press Enter. That design had a failure mode this
    machine demonstrated immediately: the spawned pane did not stay a shell (a
    profile replaced it with an interactive agent), so the "command" was typed
    into a CHAT COMPOSER. Nothing detected it, because the verification step
    only confirmed the pane existed. Had the human pressed Enter, they would
    have sent a shell command to a language model instead of approving.

    So the pane now RUNS approve.ps1 as its program. There is no send-text at
    all, which is strictly stronger than the rule it replaces: L-B1 asked that
    no automation type into an approval prompt, and the way to guarantee that
    is to have no code path that types anywhere. The human's only input is
    'a' or 'r' at the genuine prompt, which is exactly the guarantee that
    matters — the human channel is never forged.

    Three defences, in order:
      1. -NoProfile, so no startup customisation can substitute the program.
      2. approve.ps1 writes a handshake file naming its per-handoff marker AND
         the pane wezterm put it in, and both must match the pane we just
         created. This authenticates the PROGRAM, not merely the pane id.
         (A pane TITLE cannot carry the signal: it follows the foreground
         process, so it becomes python.exe the moment `lockstep resume` starts.)
      3. Verification failure kills the pane we made and aborts to narration.
  #>
  param(
    [Parameter(Mandatory)][string]$RunDir,
    [Parameter(Mandatory)][string]$Node
  )
  $approve = Join-Path $script:RepoRoot 'contrib/approve.ps1'
  $manual = "pwsh -NoProfile -File `"$approve`" -RunDir `"$RunDir`" -Cockpit"

  if (-not (Test-Wezterm)) {
    Write-Host 'wezterm unavailable - no pane to spawn.' -ForegroundColor Yellow
    Write-Host 'Ask the human to run this in a terminal themselves:' -ForegroundColor Yellow
    Write-Host "  $manual"
    return $false
  }

  $marker = "LOCKSTEP-APPROVAL-$Node-$([guid]::NewGuid().ToString('N').Substring(0,8))"
  # -Cockpit (T1.3): the pane's `lockstep resume` accepts only a or r. The
  # domain expert's guide has always said "never type e"; this is the same rule
  # enforced instead of requested.
  $base = @($script:PaneShell) + @('-File', $approve, '-RunDir', $RunDir, '-Cockpit')

  # KillOnFailure: an approval IS the decision surface, so an unverifiable pane
  # is aborted rather than downgraded.
  $pane = New-VerifiedPane -BaseCommand $base -Marker $marker `
            -Direction 'bottom' -Percent 45 -KillOnFailure
  if (-not $pane.Spawned) {
    Write-Host 'could not spawn a pane - falling back to chat narration' -ForegroundColor Yellow
    Write-Host "  $manual"
    return $false
  }
  if (-not $pane.Verified) {
    Write-Host 'ABORTED: the pane never confirmed it is running the approval script.' -ForegroundColor Red
    Write-Host "  the pane is showing '$($pane.Saw)'." -ForegroundColor Red
    Write-Host '  The pane was killed. Something is substituting the pane program' -ForegroundColor Red
    Write-Host '  (a shell profile, a default_prog, or a wrapper). Handoff aborted' -ForegroundColor Red
    Write-Host '  rather than leaving a decision surface nobody can vouch for.' -ForegroundColor Red
    Write-Host "  The human can still run it themselves:  $manual" -ForegroundColor Yellow
    return $false
  }
  return $true
}

# --- approval handoff ----------------------------------------------------------

function Invoke-ApprovalHandoff {
  <#
    The quiescence check is CODE (contrib/quiescent.py), not a procedure this
    script reimplements: if anything other than the approval is runnable, the
    orchestrator must resume DETACHED first and let the engine burn the queue
    down, because everything runnable executes in the human's own process.
  #>
  param([Parameter(Mandatory)][string]$RunDir)

  $q = Join-Path $script:RepoRoot 'contrib/quiescent.py'
  $approvalNode = & $script:Python $q $RunDir 2>&1
  $rc = $LASTEXITCODE
  if ($rc -eq 2) {
    Write-Host "cannot read $RunDir - not a run dir?" -ForegroundColor Red
    return 2
  }
  if ($rc -ne 0) {
    $lines = @($approvalNode | ForEach-Object { "$_" })
    $reason = ($lines | Where-Object { $_ -match '^reason:' } | Select-Object -First 1)
    Write-Host 'NOT quiescent - do not hand this over yet:' -ForegroundColor Yellow
    $lines | Where-Object { $_ -notmatch '^reason:' } | ForEach-Object { Write-Host "  $_" }
    Write-Host ''
    # Act on the machine tag. Printing "resume detached first" unconditionally
    # contradicted the diagnosis directly above it whenever the run was simply
    # finished — advice that would send an orchestrator into a resume loop
    # waiting for a decision point that does not exist.
    switch -Regex ($reason) {
      'finished' {
        Write-Host 'Nothing to do: this run is complete. Do not resume it.' -ForegroundColor Yellow
        break
      }
      'no-approval' {
        Write-Host 'This flow has no approval node. Let it run to completion;' -ForegroundColor Yellow
        Write-Host 'there is no handoff to make.' -ForegroundColor Yellow
        break
      }
      'multiple-approvals' {
        Write-Host 'Split the flow into segments: one decision per run.' -ForegroundColor Yellow
        break
      }
      default {
        Write-Host 'Resume DETACHED first, let it settle back to the approval, then re-check:' -ForegroundColor Yellow
        Write-Host "  Start-Process -NoNewWindow lockstep -ArgumentList 'resume','$RunDir' -RedirectStandardInput NUL"
      }
    }
    return 1
  }

  $node = ($approvalNode | Select-Object -First 1).ToString().Trim()
  $evidence = Join-Path $RunDir 'approval-evidence.txt'

  if (-not (Test-Path -LiteralPath $evidence)) {
    # B1: a flow whose approval shows no evidence is unsuitable for the cockpit.
    # Say so out loud rather than handing over a blind decision.
    Write-Host "WARNING: no approval-evidence.txt in $RunDir." -ForegroundColor Red
    Write-Host 'The human would be deciding from narration alone. Add a render-evidence' -ForegroundColor Red
    Write-Host 'shell node before the approval (see flows/starter/evidence-approval.tg.json).' -ForegroundColor Red
  }

  Write-Host "approval '$node' is the only runnable node - spawning the pane." -ForegroundColor Green
  if (New-ApprovalPane -RunDir $RunDir -Node $node) {
    Write-Host 'APPROVAL pane is up and verified. Tell the human:' -ForegroundColor Green
    Write-Host '  "read the pane, then type a or r and press Enter."'
    return 0
  }
  return 1
}

# --- drill-down ----------------------------------------------------------------

function Show-Why {
  <#
    T2.3 — "what does 'stopped with a problem' actually mean for this step?"

    MISSION is a wall with no way in: a domain expert who wants the reason
    behind one line has to go through the chat, which puts a narrated answer
    between them and an artifact that already exists. This prints the artifact.

    Display-only, read through the L-B2 primitive, writes nothing. It shows
    WHERE each thing lives as well as what it says, so the answer is checkable
    rather than merely readable.
  #>
  param([Parameter(Mandatory)][string]$RunDir, [Parameter(Mandatory)][string]$NodeId)

  $state = Read-RunJson (Join-Path $RunDir 'state.json')
  if ($null -eq $state -or -not $state.nodes.PSObject.Properties[$NodeId]) {
    Write-Host "no such step in this run: $NodeId" -ForegroundColor Red
    return 2
  }
  $rec = $state.nodes.PSObject.Properties[$NodeId].Value
  $labels = Get-NodeLabels -RunDir $RunDir
  $name = if ($labels.ContainsKey($NodeId) -and $labels[$NodeId]) { $labels[$NodeId] } else { $NodeId }

  Write-Host ('=' * 72)
  Write-Host "  $name" -ForegroundColor Cyan
  if ($name -ne $NodeId) { Write-Host "  (step id: $NodeId)" -ForegroundColor DarkGray }
  Write-Host ('=' * 72)
  $word = $script:Glossary[$rec.status]; if (-not $word) { $word = $rec.status }
  Write-Host "  state      : $word"
  Write-Host "  attempts   : $($rec.attempts)"
  if ($rec.heal_round) { Write-Host "  rework     : round $($rec.heal_round)" }
  if ($rec.started_at) { Write-Host "  started    : $($rec.started_at)" }
  if ($rec.ended_at)   { Write-Host "  ended      : $($rec.ended_at)" }

  if ($rec.error) {
    Write-Host ''
    Write-Host '  what went wrong' -ForegroundColor Yellow
    Write-Host "    $($rec.error)"
  }

  # state.json holds only a LOSSY latest verdict; per-round truth lives in the
  # rotated result-attempt<n>.json files. Say which one this is.
  if ($state.PSObject.Properties['verdicts'] -and $state.verdicts.PSObject.Properties[$NodeId]) {
    Write-Host ''
    Write-Host '  latest verdict (state.json - lossy; per-round truth is in the rotated files)' -ForegroundColor Yellow
    Write-Host "    $($state.verdicts.PSObject.Properties[$NodeId].Value)"
  }

  $phase = Join-Path $RunDir "phases/$NodeId"
  if (Test-Path -LiteralPath $phase) {
    Write-Host ''
    Write-Host '  artifacts' -ForegroundColor Yellow
    Get-ChildItem -LiteralPath $phase -File -ErrorAction SilentlyContinue |
      Sort-Object Name | ForEach-Object {
        Write-Host ("    {0,-28} {1,8:N0} bytes" -f $_.Name, $_.Length)
      }
  }
  Write-Host ''
  return 0
}

# --- boot / recovery -----------------------------------------------------------

function Invoke-Boot {
  <#
    The recovery path IS the boot protocol. The rule is mechanical and is never
    the domain expert's judgment call:
      lock pid DEAD + stale 'running' -> a plain `resume` is safe (the engine
        already auto-clears same-host dead-pid locks; --force-unlock is the
        documented fallback only)
      lock pid ALIVE -> the detached run OUTLIVED the orchestrator, which is the
        normal case after a session-limit kill: reattach the view, narrate
        "still working", and do NOT unlock.
  #>
  param([string]$RunsRoot = 'runs')
  $root = if ([System.IO.Path]::IsPathRooted($RunsRoot)) { $RunsRoot }
          else { Join-Path $script:RepoRoot $RunsRoot }
  Write-Host 'cockpit boot - scanning for unfinished work' -ForegroundColor Cyan
  Write-Host ('-' * 64)
  if (-not (Test-Path $root)) { Write-Host "no runs yet ($root)"; return 0 }

  $any = $false
  foreach ($dir in Get-ChildItem -Path $root -Directory | Sort-Object LastWriteTime -Descending) {
    $state = Read-RunJson (Join-Path $dir.FullName 'state.json')
    if ($null -eq $state) { continue }
    $statuses = @($state.nodes.PSObject.Properties | ForEach-Object { $_.Value.status })
    $unfinished = ($statuses -contains 'pending') -or ($statuses -contains 'running') -or
                  ($statuses -contains 'blocked')
    if (-not $unfinished) { continue }
    $any = $true

    # The engine's lockfile is `<run_dir>/lock` (state.py acquire_lock): a JSON
    # object carrying pid + hostname + start time.
    $lock = Read-RunJson (Join-Path $dir.FullName 'lock')
    $verdict = 'resume is safe (no lock holder recorded)'
    if ($lock -and $lock.pid) {
      $alive = $null -ne (Get-Process -Id $lock.pid -ErrorAction SilentlyContinue)
      $verdict = if ($alive) {
        "STILL RUNNING under pid $($lock.pid) - do NOT unlock; reattach the view"
      } else {
        "lock holder pid $($lock.pid) is gone - a plain 'lockstep resume' is safe"
      }
    }
    $done = @($statuses | Where-Object { $_ -eq 'done' }).Count
    Write-Host ("{0}" -f $dir.Name) -ForegroundColor White
    Write-Host ("   flow: {0}   progress: {1} of {2} steps done" -f $state.flow_name, $done, $statuses.Count)
    Write-Host ("   {0}" -f $verdict)

    # Journal replay: consent already given is restated, never re-asked.
    $journal = Read-RunFile (Join-Path $dir.FullName 'cockpit-journal.jsonl')
    if ($journal) {
      foreach ($ln in ($journal -split "`n")) {
        if (-not $ln.Trim()) { continue }
        try { $e = $ln | ConvertFrom-Json } catch { continue }
        if ($e.kind -eq 'consent') {
          Write-Host ("   consent on record: {0} (deliverable '{1}')" -f $e.cap, $e.deliverable) -ForegroundColor DarkGray
        }
      }
    }
    Write-Host ''
  }
  if (-not $any) { Write-Host 'nothing unfinished - clean slate.' }
  return 0
}

# --- layout --------------------------------------------------------------------

function Invoke-Layout {
  param([Parameter(Mandatory)][string]$RunDir)
  $self = $PSCommandPath
  if (-not (Test-Wezterm)) {
    Write-Host 'wezterm not found - falling back to a single status loop.' -ForegroundColor Yellow
    Show-Mission -RunDir $RunDir -Deliverable $Deliverable
    return
  }
  # ACTIVITY right, then this process becomes MISSION at the bottom of the
  # column it already owns. CHAT (the pane you are in) is never touched.
  $act = @($script:PaneShell) + @('-File', $self, '-RunDir', $RunDir, '-Role', 'activity')
  $marker = "LOCKSTEP-ACTIVITY-$([guid]::NewGuid().ToString('N').Substring(0,8))"
  # No -KillOnFailure: a view pane is not a decision surface, so a failed
  # verification is a downgrade rather than an abort — but it is still reported,
  # because a pane running something other than what we asked for is a fact
  # about the machine that the next person needs to know.
  $pane = New-VerifiedPane -BaseCommand $act -Marker $marker -Direction 'right' -Percent 45
  if (-not $pane.Spawned) {
    Write-Host 'could not spawn ACTIVITY - continuing with MISSION only.' -ForegroundColor Yellow
  } elseif (-not $pane.Verified) {
    Write-Host "ACTIVITY pane never confirmed its program (it shows '$($pane.Saw)')." -ForegroundColor Yellow
    Write-Host 'Continuing - a view is not a decision surface - but treat that pane as untrusted.' -ForegroundColor Yellow
  }
  Show-Mission -RunDir $RunDir -Deliverable $Deliverable
}

# --- entry ---------------------------------------------------------------------

if ($Boot) { exit (Invoke-Boot -RunsRoot $RunsRoot) }

if ($Tui) {
  # T3.1 — the single-process view. OPT-IN, and it stays that way: this script
  # is the path that ships to machines whose terminal configuration nobody here
  # controls, so correctness lives here and the TUI is the nicer thing you can
  # choose. If that ever inverts it should be a decision with its own commit,
  # not a drift.
  $tui = Join-Path $script:RepoRoot 'contrib/mission_tui.py'
  $tuiArgs = @($tui, '--runs-root', $RunsRoot, '--repo-root', $script:RepoRoot)
  if ($RunDir -and -not $Follow) { $tuiArgs = @($tui, $RunDir) + $tuiArgs[1..($tuiArgs.Count - 1)] }
  & $script:Python @tuiArgs
  exit $LASTEXITCODE
}

if (-not $RunDir -and -not $Follow) {
  Write-Host 'usage: cockpit.ps1 -RunDir <run_dir> [-Role layout|mission|activity] [-Approve] [-Boot]'
  Write-Host '       cockpit.ps1 -Role mission -Follow      # track the newest run'
  exit 2
}
if ($RunDir -and -not (Test-Path -LiteralPath $RunDir)) {
  Write-Host "no such run dir: $RunDir" -ForegroundColor Red
  exit 2
}
if ($Approve -and -not $RunDir) {
  # An approval is a specific decision about a specific run. Guessing which one
  # from a directory listing is exactly the kind of judgment call this design
  # keeps out of the handoff path.
  Write-Host '-Approve needs an explicit -RunDir (never the newest guess).' -ForegroundColor Red
  exit 2
}

if ($Approve) { exit (Invoke-ApprovalHandoff -RunDir $RunDir) }

if ($Role -eq 'why') {
  if (-not $RunDir) { Write-Host '-Role why needs -RunDir and -Node.' -ForegroundColor Red; exit 2 }
  if (-not $Node)   { Write-Host '-Role why needs -Node <step id>.' -ForegroundColor Red; exit 2 }
  exit (Show-Why -RunDir $RunDir -NodeId $Node)
}

switch ($Role) {
  'mission'  { Show-Mission  -RunDir $RunDir -Deliverable $Deliverable }
  'activity' { Show-Activity -RunDir $RunDir }
  'raw'      { while ($true) { Clear-Pane; & lockstep status $RunDir; Start-Sleep -Seconds 3 } }
  default    { Invoke-Layout -RunDir $RunDir }
}
