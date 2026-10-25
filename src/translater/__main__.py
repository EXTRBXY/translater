import multiprocessing
import sys
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()

    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from translater.selftest import run

        raise SystemExit(run(Path(sys.argv[2])))

    from translater.app import main

    raise SystemExit(main())
