"""`python -m lockstep` — the same entry point as the console script.

Exists because `--detach` re-invokes the driver as a child process and cannot
rely on the console script being on PATH.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
