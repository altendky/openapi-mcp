#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check workspace, lockfile, and npm versions before building artifacts."""

from pathlib import Path
import runpy

if __name__ == "__main__":
    script = Path(__file__).with_name("release-version.py")
    release = runpy.run_path(str(script))
    release["main"](["check"])
