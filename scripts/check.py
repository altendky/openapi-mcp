#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run the same project checks locally and in CI."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = tomllib.loads((ROOT / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
MSRV = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]["package"]["rust-version"]
NPM_ROOT = ROOT / "npm/openapi-mcp-rs"
RUST_FLAGS = ["--workspace", "--all-features", "--locked"]


def run(*args, toolchain=CHANNEL):
    command = list(map(str, args))
    print("+ " + " ".join(command), flush=True)
    subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "RUSTUP_TOOLCHAIN": toolchain},
        check=True,
    )


def cargo(*args, toolchain=CHANNEL):
    run("cargo", f"+{toolchain}", *args, toolchain=toolchain)


def npm(*args):
    executable = shutil.which("npm")
    if not executable:
        raise RuntimeError("Node.js and npm are required; run mise install --locked")
    run(executable, "--prefix", NPM_ROOT, *args)


def binary_path(binary):
    if binary is not None:
        return binary.resolve()
    cargo("build", "--locked", "-p", "openapi-mcp")
    return ROOT / "target/debug" / ("openapi-mcp.exe" if os.name == "nt" else "openapi-mcp")


def check(name, *, binary=None, platform=None):
    if name == "check":
        for task in ["fmt", "clippy", "versions", "release-test", "test", "docs", "security", "npm-test", "smoke", "package-test"]:
            check(task)
    elif name == "fmt":
        cargo("fmt", "--all", "--check")
    elif name == "clippy":
        cargo("clippy", *RUST_FLAGS, "--all-targets", "--", "-D", "warnings")
    elif name == "versions":
        run(sys.executable, ROOT / "scripts/check-versions.py")
    elif name == "release-test":
        run(sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_release*.py")
    elif name == "test":
        cargo("nextest", "run", *RUST_FLAGS, "--profile", "ci")
        cargo("test", "--doc", *RUST_FLAGS)
    elif name == "msrv":
        cargo("check", *RUST_FLAGS, "--all-targets", toolchain=MSRV)
    elif name == "npm-test":
        npm("ci")
        npm("test")
    elif name == "smoke":
        run(sys.executable, ROOT / "scripts/smoke-test.py", "--", binary_path(binary))
    elif name == "package-test":
        package_args = ["--platform", platform] if platform else []
        run(sys.executable, ROOT / "scripts/package-npm.py", "--binary", binary_path(binary), *package_args)
        run(sys.executable, ROOT / "scripts/test-npm-package.py", ROOT / "dist")
    elif name == "security":
        cargo("deny", "--locked", "--all-features", "check")
        npm("ci")
        npm("audit", "--audit-level=high")
    elif name == "coverage":
        output = ROOT / "target/coverage"
        output.mkdir(parents=True, exist_ok=True)
        cargo("llvm-cov", "nextest", *RUST_FLAGS, "--lcov", "--output-path", output / "rust.lcov")
        npm("ci")
        npm("run", "test:coverage")
    elif name == "docs":
        run("mdbook", "build", "docs")
        run("mdbook", "test", "docs")
    elif name == "docs-links":
        sources = [ROOT / "README.md", ROOT / "docs/distribution.md", NPM_ROOT / "README.md"]
        sources.extend(sorted((ROOT / "docs/src").rglob("*.md")))
        run("lychee", "--offline=false", "--no-progress", "--config", ".lychee.toml", *sources)
    else:
        raise ValueError(f"Unknown check: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=["check", "fmt", "clippy", "versions", "release-test", "test", "msrv", "npm-test", "smoke", "package-test", "security", "coverage", "docs", "docs-links"])
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--platform")
    args = parser.parse_args()
    try:
        check(args.check, binary=args.binary, platform=args.platform)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
