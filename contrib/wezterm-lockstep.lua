-- wezterm-lockstep.lua — a dedicated "lockstep" workspace for the cockpit.
--
-- THIS IS A SNIPPET TO PASTE, NOT A MODULE TO REQUIRE. WezTerm evaluates the
-- config in a sandbox where loading another chunk (`dofile`, or `require` of a
-- user module via package.path) aborts config evaluation SILENTLY — WezTerm
-- falls back to its built-in defaults and prints nothing, so a broken config
-- looks like a config that simply did not take. Verified on wezterm
-- 20240203-110809-5046fc22.
--
-- INSTALL: paste everything between the BEGIN/END markers into your
-- ~/.wezterm.lua, immediately before `return config`, then edit LOCKSTEP_REPO.
-- Press CTRL+SHIFT+ALT+L to open the cockpit.
--
-- OPTIONAL, and deliberately so. The cockpit does not depend on this and must
-- not: it ships to machines whose terminal config nobody here controls, so
-- correctness lives in contrib/cockpit.ps1 (explicit pane programs,
-- -NoProfile, handshake verification). This only makes the cockpit pleasant to
-- reach and keeps its panes out of your other workspaces.
--
-- WHAT IT BUILDS — one window in its own workspace, laid out per the pane
-- grammar:
--
--     +----------------------------+---------------------------+
--     |  CHAT (the assistant)      |  ACTIVITY (what's running)|
--     +----------------------------+---------------------------+
--     |  MISSION — status + spend, full width, always present   |
--     +---------------------------------------------------------+

-- ============================ BEGIN lockstep cockpit ============================
local LOCKSTEP_REPO = "D:\\Shared\\lockstep"
local LOCKSTEP_WORKSPACE = "lockstep"

-- Every cockpit pane runs -NoProfile, for the same reason cockpit.ps1 does: a
-- shell profile that auto-starts an interactive agent in a project directory
-- turns a pane into something that is NOT a shell, and anything typed at it
-- goes to a chat composer instead of a command prompt. A cockpit pane is
-- infrastructure, not your interactive shell.
local function lockstep_pane(role)
  return {
    "pwsh.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", LOCKSTEP_REPO .. "\\contrib\\cockpit.ps1",
    "-Role", role, "-Follow",
  }
end

-- The CHAT pane. This one DOES get your profile: it is the assistant you talk
-- to, and it is the only pane a human types into.
local lockstep_chat = {
  "pwsh.exe", "-NoLogo", "-NoExit", "-Command",
  "if (Test-Path .venv\\Scripts\\Activate.ps1) { & .venv\\Scripts\\Activate.ps1 }; pi",
}

wezterm.on("lockstep-cockpit", function(win, pane)
  local found = nil
  for _, w in ipairs(wezterm.mux.all_windows()) do
    if w:get_workspace() == LOCKSTEP_WORKSPACE then found = w end
  end

  if not found then
    local tab, chat_pane, _ = wezterm.mux.spawn_window({
      workspace = LOCKSTEP_WORKSPACE,
      cwd = LOCKSTEP_REPO,
      args = lockstep_chat,
    })
    tab:set_title("cockpit")

    -- Split order matters: take MISSION off the bottom of the WHOLE window
    -- first so it spans full width, then split what remains for ACTIVITY.
    -- Splitting right first would leave MISSION under only one column.
    chat_pane:split({
      direction = "Bottom", size = 0.30,
      cwd = LOCKSTEP_REPO, args = lockstep_pane("mission"),
    })
    chat_pane:split({
      direction = "Right", size = 0.45,
      cwd = LOCKSTEP_REPO, args = lockstep_pane("activity"),
    })
    -- CHAT keeps the focus: it is where the human lives. The other two panes
    -- are read-only views and must never steal the cursor.
    chat_pane:activate()
  end

  -- Switching is separate from building, so a second press REJOINS the
  -- cockpit instead of stacking another copy of it.
  win:perform_action(
    wezterm.action.SwitchToWorkspace({ name = LOCKSTEP_WORKSPACE }), pane)
end)

-- CTRL+SHIFT+ALT+L: CTRL+SHIFT+L is already ShowDebugOverlay in WezTerm's
-- defaults, and silently shadowing a built-in is a poor trade for one keystroke.
config.keys = config.keys or {}
table.insert(config.keys, {
  key = "L", mods = "CTRL|SHIFT|ALT",
  action = wezterm.action.EmitEvent("lockstep-cockpit"),
})

config.launch_menu = config.launch_menu or {}
table.insert(config.launch_menu, {
  label = "lockstep cockpit",
  cwd = LOCKSTEP_REPO,
  args = { "cmd.exe", "/k", LOCKSTEP_REPO .. "\\contrib\\start-cockpit.cmd" },
})
-- ============================= END lockstep cockpit =============================
