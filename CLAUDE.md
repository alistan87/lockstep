# lockstep

The spec is authoritative: `docs/SPEC.md` (revision 3) as amended by
`docs/AMENDMENTS-r4.md` (adopted; wins on any disagreement). Build order is
SPEC §14. Run the full pytest suite after every change:

```
.venv\Scripts\python.exe -m pytest
```

Working agreement (SPEC §14): `pydantic` remains the only runtime dependency;
prefer deleting a feature over adding a dependency; **stop and ask before
deviating from any MUST, exit code, or stated guarantee**. Record deviations in
`docs/DEVIATIONS.md` (what, why, date).

Things that look like bugs but are not:
- `interpolate.py`: the FULL pre-spill value is hashed while the prompt gets a
  stub, and the stub's absolute path is excluded from the hash (SPEC §7).
- Shell nodes always re-run (`cacheable=False`) — deliberate (SPEC §0.1.7).
- A done map node always re-enters `_run_map`; per-ITEM hashes do the caching.
- `NullWorkspace` disables external-edit detection (AMENDMENTS M6).

`lockstep doctor` is the only check that catches harness flag drift: run it
after any harness upgrade and weekly (AMENDMENTS A1 — not a pre-commit hook).
