"""FileStore: run state persisted in the run directory (SPEC §8.1, §10).

The one v1 Store implementation. A graph-backed Store is gated on the §15 open
question; do not design against it (rule of two).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .state import PhaseRecord, RunState, load_state, write_state


class FileStore:
    def __init__(self, run_dir: Path, state: RunState):
        self.run_dir = Path(run_dir)
        self.state = state
        self._lock = threading.Lock()

    # -- Store protocol --

    def record(self, rec: PhaseRecord) -> None:
        with self._lock:
            self.state.nodes[rec.node_id] = rec
            write_state(self.run_dir, self.state)

    def result_of(self, node_id: str) -> Any:
        rec = self.state.nodes[node_id]
        if rec.result_path is None:
            return None
        return Path(rec.result_path).read_text(encoding="utf-8")

    def load_run(self, run_dir: Path) -> RunState:
        return load_state(run_dir)

    # -- helpers --

    def mutate(self, fn) -> None:
        """Apply fn(state) and persist atomically under the state lock."""
        with self._lock:
            fn(self.state)
            write_state(self.run_dir, self.state)

    def phase_dir(self, node_id: str, item_index: int | None = None) -> Path:
        d = self.run_dir / "phases" / node_id
        if item_index is not None:
            d = d / "items" / str(item_index)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_result(self, node_id: str, text: str, *, json_output: bool, item_index: int | None = None) -> Path:
        d = self.phase_dir(node_id, item_index)
        path = d / ("result.json" if json_output else "result.txt")
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def parse_json(text: str) -> Any:
        return json.loads(text)
