#!/usr/bin/env bash
# Observation surface (SPEC §12): one pane per running node tailing log output,
# plus a status pane. Zero coupling — plain `tail -f` and `lockstep status`
# work in any terminal or multiplexer if you don't use wezterm.
set -euo pipefail

RUN_DIR="${1:?usage: wezterm-watch.sh <run_dir>}"

if ! command -v wezterm >/dev/null 2>&1; then
  echo "wezterm not found; falling back to a plain status loop." >&2
  while true; do
    clear
    lockstep status "$RUN_DIR" || true
    sleep 2
  done
fi

# Status pane.
wezterm cli split-pane --bottom --percent 30 -- \
  bash -c "while true; do clear; lockstep status '$RUN_DIR'; sleep 2; done" >/dev/null

# One pane per currently-running node (best effort; re-run to pick up new nodes).
python - "$RUN_DIR" <<'PY' | while read -r node; do
import json, sys
from pathlib import Path
state = json.loads((Path(sys.argv[1]) / "state.json").read_text(encoding="utf-8"))
for node_id, rec in state["nodes"].items():
    if rec["status"] == "running":
        print(node_id)
PY
  for f in stdout.log stderr.log log.txt; do
    p="$RUN_DIR/phases/$node/$f"
    [ -f "$p" ] && { wezterm cli split-pane --right -- tail -f "$p" >/dev/null; break; }
  done
done
