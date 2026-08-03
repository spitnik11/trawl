#!/usr/bin/env python3
"""Trawl entry point.

The app modules live in core/ to keep the repo root clean. This shim puts core/ on the
import path so `python trawl.py <cmd>` (and the Windows launchers) keep working unchanged,
and the modules can go on importing each other by bare name.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))

from cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
