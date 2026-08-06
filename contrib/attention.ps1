<#
.SYNOPSIS
  attention.ps1 — notify on the transitions where a detached run needs a human (C1).

.DESCRIPTION
  A poll loop over one run dir that fires a notification on TRANSITIONS only:

    decision-waiting   quiescent.py flipped to exit 0: the approval is runnable,
                       and only it
    step-failed        a node reached status "failed" (retries exhausted)
    run-stopped        the engine's lockfile disappeared: the run process ended
                       (success, block, and failure all end the waiting)
    run-complete       every node is done or skipped

  The payload is MECHANICAL: run dir, node id, transition name. There is
  deliberately NO summary field — a notification that describes the decision is
  a competing narration of it, and the cockpit forbids the code path, not just
  the habit. This script is a READER: it cannot answer an approval, cannot
  steer, cannot resume; the paths do not exist here.

  Reader rules (L-B2) apply in full: FileShare ReadWrite|Delete on every open,
  poll >= 2s, *.tmp never read, every error display-only. A watcher that takes
  the run down is worse than no watcher.

  Notification transport, in order: Windows toast via the WinRT API that
  powershell.exe (Windows PowerShell) exposes; console bell + line as the
  fallback; optionally an HTTP POST to -WebhookUrl (JSON payload, errors
  display-only).

.PARAMETER RunDir
  The run directory to watch.
.PARAMETER IntervalSeconds
  Poll interval, minimum 2.
.PARAMETER WebhookUrl
  Optional: POST each transition as JSON here as well.
.EXAMPLE
  pwsh -File contrib\attention.ps1 -RunDir runs\research-report-20260804T120000Z
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$RunDir,
  [double]$IntervalSeconds = 3,
  [string]$WebhookUrl = ""
)

$ErrorActionPreference = 'Continue'
if ($IntervalSeconds -lt 2) { $IntervalSeconds = 2 }
$RunDir = (Resolve-Path -LiteralPath $RunDir).Path
$script:Python = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
if (-not (Test-Path $script:Python)) { $script:Python = 'python' }
$script:Quiescent = Join-Path $PSScriptRoot 'quiescent.py'

function Read-SharedText([string]$Path) {
  # L-B2: shared open, tolerate replacement mid-read, never throw outward.
  foreach ($attempt in 1..3) {
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

function Get-Snapshot {
  $snap = [ordered]@{
    lock            = Test-Path -LiteralPath (Join-Path $RunDir 'lock')
    failed          = @()
    complete        = $false
    decision        = $false
  }
  $raw = Read-SharedText (Join-Path $RunDir 'state.json')
  if ($raw) {
    try {
      $state = $raw | ConvertFrom-Json
      $nodes = @($state.nodes.PSObject.Properties.Value)
      $snap.failed = @($nodes | Where-Object { $_.status -eq 'failed' } | ForEach-Object { $_.node_id })
      $done = @($nodes | Where-Object { $_.status -in @('done', 'skipped') })
      $snap.complete = ($nodes.Count -gt 0 -and $done.Count -eq $nodes.Count)
    } catch { }  # a half-written state is the next poll's problem
  }
  if (Test-Path -LiteralPath $script:Quiescent) {
    & $script:Python $script:Quiescent $RunDir *> $null
    $snap.decision = ($LASTEXITCODE -eq 0)
  }
  return $snap
}

function Send-Toast([string]$Title, [string]$Body) {
  # WinRT toasts load reliably in Windows PowerShell, not in pwsh; delegate.
  # Escape single quotes: the values are spliced into single-quoted literals
  # below, and an un-escaped ' (e.g. "node 'impl' failed") would otherwise
  # break the generated script — silently, on exactly the transitions that
  # carry node ids.
  $Title = $Title -replace "'", "''"
  $Body = $Body -replace "'", "''"
  $script = @"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
`$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
`$texts = `$xml.GetElementsByTagName('text')
`$texts.Item(0).AppendChild(`$xml.CreateTextNode('$Title')) | Out-Null
`$texts.Item(1).AppendChild(`$xml.CreateTextNode('$Body')) | Out-Null
`$toast = [Windows.UI.Notifications.ToastNotification]::new(`$xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('lockstep').Show(`$toast)
"@
  try {
    $p = Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -PassThru `
      -ArgumentList @('-NoProfile', '-NonInteractive', '-Command', $script)
    $null = $p.WaitForExit(5000)
    if ($p.ExitCode -eq 0) { return $true }
  } catch { }
  return $false
}

function Publish-Transition([string]$Kind, [string]$Detail) {
  $stamp = (Get-Date).ToString('HH:mm:ss')
  $line = "[$stamp] $Kind - $Detail"
  if (-not (Send-Toast "lockstep: $Kind" $Detail)) {
    Write-Host "`a$line" -ForegroundColor Yellow   # bell + line: the fallback transport
  } else {
    Write-Host $line -ForegroundColor Yellow
  }
  if ($WebhookUrl) {
    # Mechanical payload only. No summary field exists — by design.
    $payload = @{ run_dir = $RunDir; transition = $Kind; detail = $Detail } | ConvertTo-Json -Compress
    try {
      Invoke-RestMethod -Uri $WebhookUrl -Method Post -Body $payload `
        -ContentType 'application/json' -TimeoutSec 10 | Out-Null
    } catch {
      Write-Host "  (webhook failed: $($_.Exception.Message) - display-only)" -ForegroundColor DarkGray
    }
  }
}

Write-Host "watching $RunDir (interval ${IntervalSeconds}s) - Ctrl-C to stop" -ForegroundColor DarkGray
$prev = Get-Snapshot
$announcedFailed = [System.Collections.Generic.HashSet[string]]::new()
foreach ($f in $prev.failed) { [void]$announcedFailed.Add($f) }

while ($true) {
  Start-Sleep -Seconds $IntervalSeconds
  $cur = Get-Snapshot

  if ($cur.decision -and -not $prev.decision) {
    Publish-Transition 'decision-waiting' 'the approval is runnable, and only it — open your APPROVAL terminal'
  }
  foreach ($node in $cur.failed) {
    if ($announcedFailed.Add($node)) {
      Publish-Transition 'step-failed' "node '$node' failed with retries exhausted"
    }
  }
  if ($cur.complete -and -not $prev.complete) {
    Publish-Transition 'run-complete' 'every step is done or skipped'
  }
  if ($prev.lock -and -not $cur.lock) {
    Publish-Transition 'run-stopped' 'the engine process ended (success, block, and failure all end the waiting)'
  }
  $prev = $cur
}
