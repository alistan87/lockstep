<#
.SYNOPSIS
  The APPROVAL pane: show the evidence, then hand the human the real prompt.

.DESCRIPTION
  This is the command the cockpit PRE-TYPES into a freshly spawned pane. The
  human presses Enter; nothing types Enter for them.

  It exists because of a mechanical fact the proposal originally got wrong: a
  shell node's output goes to phases/<node>/stdout.log, NOT to the terminal,
  and the engine's approval prompt is a bare one-line input(). So without this
  wrapper the pane would show a naked "[approval:x] [a]pprove / [r]eject /
  [e]dit:" and the human would be deciding from chat narration — exactly what
  the evidence rule exists to prevent.

  Order matters: evidence first, prompt second, on one screen.

.PARAMETER RunDir
  The run dir whose approval is waiting.

.PARAMETER Cockpit
  Pass --cockpit to `lockstep resume`, restricting the approval prompt to a/r.
  The domain expert's guide has always said "never type e"; this makes that the
  program's behaviour rather than a request.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$RunDir,
  [string]$Lockstep,
  [string]$TitleMarker,
  [string]$Handshake,
  [switch]$Cockpit
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Lockstep) {
  $venv = Join-Path $repoRoot '.venv\Scripts\lockstep.exe'
  $Lockstep = if (Test-Path $venv) { $venv } else { 'lockstep' }
}

# Announce ourselves to the cockpit BEFORE doing anything else.
#
# The handshake names the marker we were given and the pane we actually find
# ourselves in — wezterm sets WEZTERM_PANE inside every pane it spawns. The
# cockpit requires both to match the pane it just created, and kills the pane
# and aborts the handoff otherwise. This is what stops a substituted pane
# program (a shell profile that launches an agent, a default_prog, a wrapper)
# from silently becoming the surface a human makes a decision on.
#
# It must be written first: the pane title cannot carry this signal, because a
# pane's title follows the FOREGROUND process, and this script hands over to
# `lockstep resume` within a second.
if ($Handshake) {
  try {
    @{ marker = $TitleMarker; pane = $env:WEZTERM_PANE; pid = $PID
       ts = (Get-Date).ToString('o') } | ConvertTo-Json -Compress |
      Set-Content -LiteralPath $Handshake -Encoding UTF8
  } catch { }
}
if ($TitleMarker) {
  try { $Host.UI.RawUI.WindowTitle = $TitleMarker } catch { }
}

# Belt and braces for the substitution hazard above: if this script is ever run
# from a shell that DOES load a profile, tell that profile to stay out.
$env:PI_SKIP_AUTO = '1'

function Write-RejectionReason {
  <#
    T1.2 — capture the human's own words, mechanically.

    Evidence travels human-ward as an artifact precisely because a narrated
    summary at a decision point cannot be trusted. The reason for a REJECTION —
    the single most decision-relevant thing the human produces all session —
    travelled back the other way through the orchestrator's narration. The
    argument that justified approval-evidence.txt applies unchanged in reverse.

    So: one line, verbatim, into <run_dir>/rejection.txt. Nothing is typed for
    them and nothing is inferred if they skip. rejection.txt is a new artifact
    class — written by the HUMAN, not by the orchestrator (that is the journal)
    and not by the engine (that is state.json) — which is what makes it usable
    as a tripwire against the orchestrator's account of what happened.
  #>
  param([Parameter(Mandatory)][string]$RunDir)

  # Exit 6 has two causes and only one of them is a person. A non-TTY
  # auto-reject must not produce a file implying somebody decided something.
  try {
    $state = Get-Content -LiteralPath (Join-Path $RunDir 'state.json') -Raw -ErrorAction Stop |
      ConvertFrom-Json
    foreach ($p in $state.nodes.PSObject.Properties) {
      if ("$($p.Value.error)" -like '*auto-rejected*') { return }
    }
  } catch { }

  Write-Host ''
  Write-Host '  In one line - what was wrong?  (just press Enter to skip)' -ForegroundColor Cyan
  $reason = $null
  try { $reason = Read-Host '  ' } catch { return }   # redirected stdin: skip
  if ([string]::IsNullOrWhiteSpace($reason)) {
    Write-Host '  Skipped. Say so in the chat and we will fix it.' -ForegroundColor Yellow
    return
  }

  # Which approval this was about. Read from the run's own state rather than
  # passed in: a parameter is something a caller can get wrong, and this file is
  # evidence about a specific decision.
  $node = '(unknown)'
  try {
    $st = Get-Content -LiteralPath (Join-Path $RunDir 'state.json') -Raw -ErrorAction Stop |
      ConvertFrom-Json
    foreach ($p in $st.nodes.PSObject.Properties) {
      if ($p.Value.role -eq 'approval' -and $p.Value.status -eq 'blocked') { $node = $p.Name }
    }
  } catch { }

  $text = @(
    ('=' * 72),
    '  WHY THIS WAS REJECTED - in the words of the person who rejected it',
    ('=' * 72),
    '',
    "  $($reason.Trim())",
    '',
    "  decision : $node",
    "  run      : $(Split-Path -Leaf $RunDir)",
    "  recorded : $((Get-Date).ToUniversalTime().ToString('o'))",
    ''
  ) -join [Environment]::NewLine
  try {
    Set-Content -LiteralPath (Join-Path $RunDir 'rejection.txt') -Value $text -Encoding UTF8
    Write-Host '  Recorded. The assistant will read it from the run itself.' -ForegroundColor Green
  } catch {
    Write-Host "  (could not write rejection.txt: $_ - please paste it into the chat)" -ForegroundColor Yellow
  }
}

# Guarded for the same reason cockpit.ps1's Clear-Pane is: Clear-Host throws
# when stdout is redirected, which is how every scripted drill runs this.
try { Clear-Host } catch { Write-Host '' }

$evidence = Join-Path $RunDir 'approval-evidence.txt'
if (Test-Path -LiteralPath $evidence) {
  # Read with delete sharing like every other run-dir read (L-B2).
  try {
    $fs = [System.IO.File]::Open($evidence, [System.IO.FileMode]::Open,
      [System.IO.FileAccess]::Read,
      [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    try {
      $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
      Write-Host $sr.ReadToEnd()
    } finally { $fs.Dispose() }
  } catch {
    Write-Host "(could not read $evidence : $_)" -ForegroundColor Yellow
  }
} else {
  Write-Host ('=' * 72)
  Write-Host '  NO EVIDENCE WAS RENDERED FOR THIS APPROVAL' -ForegroundColor Red
  Write-Host ('=' * 72)
  Write-Host ''
  Write-Host 'This flow reached a decision point without showing you what you are'
  Write-Host 'deciding about. That is a defect in the flow, not something for you to'
  Write-Host 'work around. The safe answer is r (reject) - nothing is lost, and the'
  Write-Host 'person who wrote the flow can add the evidence step.'
  Write-Host ''
}

Write-Host ('=' * 72) -ForegroundColor Cyan
Write-Host '  Type  a  to approve, or  r  to reject, then press Enter.' -ForegroundColor Cyan
if ($Cockpit) {
  Write-Host '  Those are the only two answers this prompt accepts.' -ForegroundColor Cyan
} else {
  Write-Host '  Never type e. If anything unexpected appears, copy it into the chat.' -ForegroundColor Cyan
}
Write-Host ('=' * 72) -ForegroundColor Cyan
Write-Host ''

$resumeArgs = @('resume', $RunDir)
if ($Cockpit) { $resumeArgs += '--cockpit' }
& $Lockstep @resumeArgs
$rc = $LASTEXITCODE

Write-Host ''
switch ($rc) {
  0 { Write-Host 'Approved. This segment is finished - you can close this pane.' -ForegroundColor Green }
  6 {
    Write-Host 'Rejected. Nothing was lost.' -ForegroundColor Yellow
    Write-RejectionReason -RunDir $RunDir
  }
  default { Write-Host "Finished with code $rc - paste that number into the chat." -ForegroundColor Yellow }
}
exit $rc
