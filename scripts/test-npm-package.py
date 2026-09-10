#!/usr/bin/env python3
"""Install local tarballs outside the checkout and test the published layout."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(tarballs):
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise RuntimeError("Node.js and npm are required")
    packages = list(tarballs.resolve().glob("*.tgz"))
    if len(packages) != 2:
        raise RuntimeError("expected one launcher and one native-platform tarball")
    with tempfile.TemporaryDirectory(prefix="openapi-mcp-npm-install-") as temp:
        directory = Path(temp)
        (directory / "package.json").write_text('{"private":true}')
        subprocess.run([
            npm, "install", "--ignore-scripts", "--no-audit", "--no-fund",
            *map(str, packages),
        ], cwd=temp, check=True)
        launcher = directory / "node_modules/openapi-mcp-rs/bin.js"
        subprocess.run([node, str(launcher), "--version"], cwd=temp, check=True)
        subprocess.run([
            sys.executable, str(ROOT / "scripts/smoke-test.py"), "--", node, str(launcher),
        ], cwd=temp, check=True)
    print("installed npm package smoke test passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tarballs", type=Path)
    run(parser.parse_args().tarballs)
