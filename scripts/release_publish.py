#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Publish validated release artifacts; mutation commands require an enabled tag run."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from release_artifacts import CRATES, ROOT, TARGETS, sha256, verify, version


NPM_WAIT_SECONDS = 300
GITHUB_DRAFT_WAIT_SECONDS = 120
NATIVE_PACKAGE_MISSING = 75
NATIVE_PACKAGE_CHECK = """
const { createRequire } = require('node:module');
const wrapper = process.argv[1];
const name = require(wrapper).getPlatformPackage();
if (!name) throw new Error(`Unsupported platform: ${process.platform}-${process.arch}`);
try {
  createRequire(wrapper).resolve(`${name}/package.json`);
} catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  process.exit(75);
}
"""


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def get_json(url, *, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "openapi-mcp-release (github.com/altendky/openapi-mcp)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def wait_for_visibility(check, description, *, timeout, interval=5):
    """Retry confirmed absence; let API, integrity, and execution errors propagate."""
    started = time.monotonic()
    deadline = started + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        result = check(remaining)
        if result is not None:
            return result
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            break
        print(f"Waiting for {description}: {now - started:.0f}s elapsed (limit {timeout}s)", flush=True)
        time.sleep(min(interval, remaining))
    raise RuntimeError(f"Timed out waiting for {description} after {timeout}s; inspect service visibility before rerunning the failed job")


def require_release():
    if os.environ.get("RELEASE_ENABLED") != "true":
        raise ValueError("publishing requires RELEASE_ENABLED=true")
    if os.environ.get("GITHUB_REPOSITORY") != "altendky/openapi-mcp":
        raise ValueError("publishing is restricted to altendky/openapi-mcp")
    tag = f"v{version()}"
    if os.environ.get("GITHUB_REF") != f"refs/tags/{tag}":
        raise ValueError("publishing requires the matching release tag")
    run(sys.executable, ROOT / "scripts/release-version.py", "check", "--tag", tag)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != os.environ.get("GITHUB_SHA"):
        raise ValueError("checkout does not match the workflow commit")
    # The tag must point into main's history, including when a failed run is retried.
    result = json.loads(subprocess.check_output([
        "gh", "api", f"repos/altendky/openapi-mcp/compare/{head}...main",
    ], text=True))
    if result["status"] not in ("ahead", "identical"):
        raise ValueError("release commit is not in main's history")


def cargo(directory):
    verify(directory)
    # Repackage this exact checkout and compare before letting Cargo upload anything.
    run("cargo", "package", "--workspace", "--locked", cwd=ROOT)
    for crate in CRATES:
        filename = f"{crate}-{version()}.crate"
        if sha256(ROOT / "target/package" / filename) != sha256(directory / filename):
            raise ValueError(f"repackaged crate differs from validated artifact: {crate}")
    for crate in CRATES:
        published = get_json(f"https://crates.io/api/v1/crates/{crate}/{version()}")
        if published is not None:
            if published["version"]["checksum"] != sha256(directory / f"{crate}-{version()}.crate"):
                raise ValueError(f"published crate differs from this release: {crate}")
            print(f"Already published matching {crate}@{version()}", flush=True)
            continue
        # Cargo waits for index availability before the next dependent crate.
        run("cargo", "publish", "--locked", "-p", crate, cwd=ROOT)


def npm(directory):
    verify(directory)
    executable = shutil.which("npm")
    if executable is None:
        raise RuntimeError("npm is required")
    # Every optional package must exist before the launcher becomes available.
    packages = [(f"@openapi-mcp-rs/{platform}", f"openapi-mcp-rs-{platform}") for platform in TARGETS]
    packages.append(("openapi-mcp-rs", "openapi-mcp-rs"))
    for name, filename in packages:
        path = directory / f"{filename}-{version()}.tgz"
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
        url = f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='')}/{version()}"
        published = get_json(url)
        if published is not None:
            if published["dist"].get("integrity") != integrity:
                raise ValueError(f"published npm package differs from this release: {name}")
            print(f"Already published matching {name}@{version()}", flush=True)
            continue
        run(executable, "publish", path, "--access", "public", "--provenance", "--ignore-scripts", "--registry", "https://registry.npmjs.org")
        published = wait_for_visibility(
            lambda remaining: get_json(url, timeout=min(30, remaining)),
            f"npm package {name}@{version()}", timeout=NPM_WAIT_SECONDS,
        )
        if published["dist"].get("integrity") != integrity:
            raise ValueError(f"registry integrity mismatch: {name}")


def find_github_release(repository, tag, *, timeout=None):
    # List via the authenticated API so 404 permission errors are not treated as absence.
    # The release-by-tag endpoint cannot retrieve drafts, including newly created ones.
    releases = json.loads(subprocess.check_output([
        "gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases",
    ], text=True, timeout=timeout))
    return next((item for page in releases for item in page if item["tag_name"] == tag), None)


def github(directory):
    verify(directory)
    tag = f"v{version()}"
    repository = os.environ["GITHUB_REPOSITORY"]
    release = find_github_release(repository, tag)
    if release is None:
        run("gh", "release", "create", tag, "--repo", repository, "--draft", "--verify-tag", "--title", tag, "--generate-notes")
        release = wait_for_visibility(
            lambda remaining: find_github_release(repository, tag, timeout=remaining),
            f"created GitHub draft {tag}", timeout=GITHUB_DRAFT_WAIT_SECONDS,
        )
    assets = {asset["name"]: asset for asset in release["assets"]}
    expected = {path.name for path in directory.iterdir()}
    if set(assets) - expected:
        raise ValueError("existing release contains unexpected assets; inspect before rerunning")
    for path in sorted(directory.iterdir()):
        if path.name in assets:
            if assets[path.name].get("digest") != f"sha256:{sha256(path)}":
                raise ValueError(f"existing release asset differs: {path.name}")
        elif release["draft"]:
            run("gh", "release", "upload", tag, path, "--repo", repository)
        else:
            raise ValueError(f"published release is missing {path.name}; inspect before rerunning")
    if release["draft"]:
        run("gh", "release", "edit", tag, "--repo", repository, "--draft=false")


def smoke():
    npm_path, node = shutil.which("npm"), shutil.which("node")
    if not npm_path or not node:
        raise RuntimeError("Node.js and npm are required")
    environment = {key: value for key, value in os.environ.items() if key != "OPENAPI_MCP_NPM_COMMAND"}
    with tempfile.TemporaryDirectory(prefix="openapi-mcp-registry-") as temp:
        def install(remaining):
            # Avoid retaining lockfiles, partial node_modules, or stale npm metadata.
            directory = Path(tempfile.mkdtemp(prefix="install-", dir=temp))
            (directory / "package.json").write_text('{"private":true}')
            started = time.monotonic()
            result = subprocess.run([
                npm_path, "install", f"openapi-mcp-rs@{version()}", "--include=optional", "--ignore-scripts",
                "--no-audit", "--no-fund", "--registry", "https://registry.npmjs.org",
                "--cache", str(directory / "npm-cache"),
            ], cwd=directory, env=environment, timeout=remaining)
            remaining -= time.monotonic() - started
            if result.returncode != 0 or remaining <= 0:
                shutil.rmtree(directory)
                return None
            result = subprocess.run([
                node, "-e", NATIVE_PACKAGE_CHECK,
                str(directory / "node_modules/openapi-mcp-rs/lib.js"),
            ], cwd=directory, env=environment, timeout=remaining)
            if result.returncode == NATIVE_PACKAGE_MISSING:
                print("npm install omitted the native optional package; retrying a fresh installation", flush=True)
                shutil.rmtree(directory)
                return None
            result.check_returncode()
            return directory

        directory = wait_for_visibility(
            install, f"npm installation of openapi-mcp-rs@{version()}",
            timeout=NPM_WAIT_SECONDS, interval=10,
        )
        # Once the native package exists, real launcher/version/smoke errors are fatal.
        launcher = directory / "node_modules/openapi-mcp-rs/bin.js"
        actual = subprocess.check_output([node, str(launcher), "--version"], cwd=directory, env=environment, text=True).strip()
        if actual != f"openapi-mcp {version()}":
            raise ValueError(f"installed registry binary version mismatch: {actual}")
        run(sys.executable, ROOT / "scripts/smoke-test.py", "--", node, launcher, cwd=directory, env=environment)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["cargo", "npm", "github", "smoke"])
    parser.add_argument("directory", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "smoke":
        smoke()
    else:
        require_release()
        if args.directory is None:
            parser.error("publishing requires a validated release directory")
        {"cargo": cargo, "npm": npm, "github": github}[args.command](args.directory.resolve())
