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
  [ValidateSet('layout', 'mission', 'activity', 'raw')]
  [string]$Role = 'layout',
  [switch]$Boot,
  [switch]$Approve,
  [string]$RunsRoot = 'runs',
  [string]$Deliverable,
  [double]$Interval = 1.0,
  [switch]$NoWezterm,
  [switch]$Follow
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

function Get-MissionLines {
  <#
    The DE tier. Every line is a field mapping over state.json plus the run's
    own flow.tg.json copy for denominators. Nodes may additionally drop a
    one-line phases/<node>/mission.txt, which is included VERBATIM — still
    mechanical (a file copy), so the tier keeps its trust status.
  #>
  param([Parameter(Mandatory)][string]$RunDir)

  $state = Read-RunJson (Join-Path $RunDir 'state.json')
  if ($null -eq $state) { return @('(reading state...)') }
  $flow = Read-RunJson (Join-Path $RunDir 'flow.tg.json')
  $healBudget = Get-HealBudget $flow

  $lines = @()
  foreach ($prop in $state.nodes.PSObject.Properties) {
    $id = $prop.Name
    $rec = $prop.Value
    $word = $script:Glossary[$rec.status]
    if (-not $word) { $word = $rec.status }

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

    $lines += ('{0,-22} {1}' -f $id, $word)

    $missionFile = Join-Path $RunDir "phases/$id/mission.txt"
    $extra = Read-RunFile -Path $missionFile
    if ($extra) {
      $first = ($extra -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
      if ($first) { $lines += ('{0,-22}   {1}' -f '', $first.Trim()) }
    }
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

function Show-Mission {
  param([string]$RunDir, [string]$Deliverable)
  try { $Host.UI.RawUI.WindowTitle = 'LOCKSTEP-MISSION' } catch { }
  $env:PI_SKIP_AUTO = '1'
  $tick = 0
  $lastGood = @()
  $boundRun = $null
  while ($true) {
    $tick++
    $current = Resolve-RunDir -Given $RunDir
    if (-not $current) {
      Show-WaitingScreen -Label 'MISSION'
      Start-Sleep -Seconds ([Math]::Max(1.0, $Interval))
      continue
    }
    if ($current -ne $boundRun) { $boundRun = $current; $lastGood = @() }
    $RunDirActive = $current
    $lines = Get-MissionLines -RunDir $RunDirActive
    if ($lines.Count -gt 0 -and $lines[0] -ne '(reading state...)') { $lastGood = $lines }
    elseif ($lastGood.Count -gt 0) { $lines = $lastGood }   # hold the last good frame

    $name = Split-Path -Leaf $RunDirActive
    Clear-Pane
    Write-Host "MISSION  $name" -ForegroundColor Cyan
    Write-Host ('-' * 64)
    $lines | ForEach-Object { Write-Host $_ }
    Write-Host ('-' * 64)
    Get-SpendLine -RunDir $RunDirActive -Deliverable $Deliverable | ForEach-Object {
      Write-Host $_ -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host "updated $(Get-Date -Format 'HH:mm:ss')  (this pane reads files; it never changes the run)" -ForegroundColor DarkGray
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
  $bound = $null
  $offset = 0L          # per-bound-node read position; reset when re-pointing
  $start = Get-Date
  while ($true) {
    $RunDirActive = Resolve-RunDir -Given $RunDir
    if (-not $RunDirActive) {
      Show-WaitingScreen -Label 'ACTIVITY'
      Start-Sleep -Seconds 2
      continue
    }
    if (-not $bound) {
      $bound = Get-FrontierNode -RunDir $RunDirActive
      if ($bound) {
        Clear-Pane
        Write-Host "ACTIVITY  $bound" -ForegroundColor Green
        Write-Host ('-' * 64)
        $start = Get-Date
        $offset = 0L      # a new node means a new file to read from the top
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
        Start-Sleep -Seconds 2
        continue
      }
    }

    # Incremental: only lines appended since the last poll (L-B2 primitive).
    $progress = Join-Path $RunDirActive "phases/$bound/progress.jsonl"
    foreach ($ln in (Read-RunChunk -Path $progress -Offset ([ref]$offset))) {
      try {
        $obj = $ln | ConvertFrom-Json
        $note = if ($obj.note) { $obj.note } elseif ($obj.message) { $obj.message } else { $ln }
        Write-Host ("  {0}" -f $note)
      } catch { Write-Host "  $ln" }
    }

    $elapsed = [int]((Get-Date) - $start).TotalMinutes
    Write-Host ("`r  working - {0} m elapsed   " -f $elapsed) -NoNewline -ForegroundColor DarkGray

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
      2. approve.ps1 sets its own pane title; we read it back. A pane title
         comes from the process inside it, so this authenticates the PROGRAM,
         not merely the pane id.
      3. Verification failure kills the pane we made and aborts to narration.
  #>
  param(
    [Parameter(Mandatory)][string]$RunDir,
    [Parameter(Mandatory)][string]$Node
  )
  $approve = Join-Path $script:RepoRoot 'contrib/approve.ps1'
  $manual = "pwsh -NoProfile -File `"$approve`" -RunDir `"$RunDir`""

  if (-not (Test-Wezterm)) {
    Write-Host 'wezterm unavailable - no pane to spawn.' -ForegroundColor Yellow
    Write-Host 'Ask the human to run this in a terminal themselves:' -ForegroundColor Yellow
    Write-Host "  $manual"
    return $false
  }

  $marker = "LOCKSTEP-APPROVAL-$Node-$([guid]::NewGuid().ToString('N').Substring(0,8))"
  $handshake = Join-Path ([System.IO.Path]::GetTempPath()) "lockstep-approve-$($marker).json"
  Remove-Item -LiteralPath $handshake -ErrorAction SilentlyContinue

  $cmd = @($script:PaneShell) + @('-File', $approve, '-RunDir', $RunDir,
                                  '-TitleMarker', $marker, '-Handshake', $handshake)
  $paneId = New-RunnerPane -Command $cmd -Direction 'bottom' -Percent 45
  if (-not $paneId) {
    Write-Host 'could not spawn a pane - falling back to chat narration' -ForegroundColor Yellow
    Write-Host "  $manual"
    return $false
  }

  if (-not (Wait-PaneHandshake -PaneId $paneId -HandshakePath $handshake -Marker $marker)) {
    $saw = Get-PaneTitle -PaneId $paneId
    & wezterm cli kill-pane --pane-id $paneId 2>$null | Out-Null
    Remove-Item -LiteralPath $handshake -ErrorAction SilentlyContinue
    Write-Host 'ABORTED: the pane never confirmed it is running the approval script.' -ForegroundColor Red
    Write-Host "  pane $paneId is showing '$saw'." -ForegroundColor Red
    Write-Host '  The pane was killed. Something is substituting the pane program' -ForegroundColor Red
    Write-Host '  (a shell profile, a default_prog, or a wrapper). Handoff aborted' -ForegroundColor Red
    Write-Host '  rather than leaving a decision surface nobody can vouch for.' -ForegroundColor Red
    Write-Host "  The human can still run it themselves:  $manual" -ForegroundColor Yellow
    return $false
  }
  Remove-Item -LiteralPath $handshake -ErrorAction SilentlyContinue
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
  $actPane = New-RunnerPane -Command $act -Direction 'right' -Percent 45
  if (-not $actPane) {
    Write-Host 'could not spawn ACTIVITY - continuing with MISSION only.' -ForegroundColor Yellow
  } elseif (-not (Wait-PaneProgram -PaneId $actPane -Expect 'LOCKSTEP-ACTIVITY')) {
    # A view pane is not a decision surface, so a failed verification is a
    # downgrade rather than an abort — but it is still reported, because a pane
    # running something other than what we asked for is a fact about the
    # machine that the next person needs to know.
    Write-Host 'ACTIVITY pane did not report the expected program - see the pane.' -ForegroundColor Yellow
  }
  Show-Mission -RunDir $RunDir -Deliverable $Deliverable
}

# --- entry ---------------------------------------------------------------------

if ($Boot) { exit (Invoke-Boot -RunsRoot $RunsRoot) }

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

switch ($Role) {
  'mission'  { Show-Mission  -RunDir $RunDir -Deliverable $Deliverable }
  'activity' { Show-Activity -RunDir $RunDir }
  'raw'      { while ($true) { Clear-Pane; & lockstep status $RunDir; Start-Sleep -Seconds 3 } }
  default    { Invoke-Layout -RunDir $RunDir }
}
