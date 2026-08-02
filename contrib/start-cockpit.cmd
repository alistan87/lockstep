@echo off
REM ============================================================================
REM  start-cockpit.cmd - the double-clickable entry point.
REM
REM  This is the ONLY way a domain expert starts or restarts the system. Cold
REM  start and "the morning after a crash" are the same double-click: the boot
REM  scan below reports every unfinished run and whether it is safe to resume,
REM  using the mechanical lock/pid rule - never a judgment call, least of all
REM  the expert's.
REM
REM  It does NOT start a run. It shows you where things stand and drops you in
REM  the chat pane, which is where every decision gets made.
REM ============================================================================

setlocal
cd /d "%~dp0.."

set "PWSH=pwsh"
where pwsh >nul 2>&1 || set "PWSH=powershell"

echo.
echo   lockstep cockpit
echo   ----------------
echo.

%PWSH% -NoProfile -File "contrib\cockpit.ps1" -Boot

echo.
echo   ------------------------------------------------------------------
echo   Nothing above is an error. If a run says "STILL RUNNING", it kept
echo   working while your screen was off - that is normal and nothing was
echo   lost. If it says a resume is safe, say "continue" in the chat.
echo   ------------------------------------------------------------------
echo.

REM Hand the terminal to the orchestrator. The expert talks here; the
REM orchestrator opens the MISSION and ACTIVITY panes when a run starts.
where pi >nul 2>&1
if %ERRORLEVEL%==0 (
  echo   Starting the assistant. Tell it what you would like to work on.
  echo.
  pi
) else (
  echo   [pi not found on PATH - start your assistant here yourself]
  echo.
  cmd /k
)

endlocal
