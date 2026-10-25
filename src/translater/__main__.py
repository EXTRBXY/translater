import multiprocessing
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    # Windowed build: no console streams; a failed stderr write makes PyInstaller block on an error dialog.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    multiprocessing.freeze_support()

    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from translater.selftest import run

        raise SystemExit(run(Path(sys.argv[2])))

    from translater.app import main

    raise SystemExit(main())
