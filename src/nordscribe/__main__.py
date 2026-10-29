"""``python -m nordscribe`` entry point."""

from __future__ import annotations

import multiprocessing

from nordscribe.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
