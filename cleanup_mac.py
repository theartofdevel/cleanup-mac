#!/usr/bin/env python3
"""Entry point for the Nuitka build and `make install` symlink target.
Logic lives in the `cleanup_mac/` package."""

from __future__ import annotations

if __name__ == "__main__":
    import sys

    from cleanup_mac.cli import main

    sys.exit(main())
