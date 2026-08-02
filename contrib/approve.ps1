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
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$RunDir,
  [string]$Lockstep,
  [string]$TitleMarker,
  [string]$Handshake
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

Clear-Host

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
Write-Host '  Never type e. If anything unexpected appears, copy it into the chat.' -ForegroundColor Cyan
Write-Host ('=' * 72) -ForegroundColor Cyan
Write-Host ''

& $Lockstep resume $RunDir
$rc = $LASTEXITCODE

Write-Host ''
switch ($rc) {
  0 { Write-Host 'Approved. This segment is finished - you can close this pane.' -ForegroundColor Green }
  6 { Write-Host 'Rejected. Nothing was lost; say so in the chat and we will fix it.' -ForegroundColor Yellow }
  default { Write-Host "Finished with code $rc - paste that number into the chat." -ForegroundColor Yellow }
}
exit $rc
