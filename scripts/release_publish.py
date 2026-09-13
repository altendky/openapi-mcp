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


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "openapi-mcp-release (github.com/altendky/openapi-mcp)"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


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
        for attempt in range(12):
            published = get_json(url)
            if published is not None:
                if published["dist"].get("integrity") != integrity:
                    raise ValueError(f"registry integrity mismatch: {name}")
                break
            time.sleep(5)
        else:
            raise RuntimeError(f"registry propagation timed out: {name}; rerun after checking npm")


def github(directory):
    verify(directory)
    tag = f"v{version()}"
    repository = os.environ["GITHUB_REPOSITORY"]
    # List via the authenticated API so 404 permission errors are not treated as absence.
    releases = json.loads(subprocess.check_output([
        "gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases",
    ], text=True))
    release = next((item for page in releases for item in page if item["tag_name"] == tag), None)
    if release is None:
        run("gh", "release", "create", tag, "--repo", repository, "--draft", "--verify-tag", "--title", tag, "--generate-notes")
        release = json.loads(subprocess.check_output(["gh", "api", f"repos/{repository}/releases/tags/{tag}"], text=True))
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
        directory = Path(temp)
        (directory / "package.json").write_text('{"private":true}')
        # Retry only installation/registry propagation; never retry a failing smoke test.
        for attempt in range(6):
            result = subprocess.run([
                npm_path, "install", f"openapi-mcp-rs@{version()}", "--include=optional", "--ignore-scripts",
                "--no-audit", "--no-fund", "--registry", "https://registry.npmjs.org",
            ], cwd=temp, env=environment)
            if result.returncode == 0:
                break
            if attempt == 5:
                result.check_returncode()
            time.sleep(10)
        launcher = directory / "node_modules/openapi-mcp-rs/bin.js"
        actual = subprocess.check_output([node, str(launcher), "--version"], cwd=temp, env=environment, text=True).strip()
        if actual != f"openapi-mcp {version()}":
            raise ValueError(f"installed registry binary version mismatch: {actual}")
        run(sys.executable, ROOT / "scripts/smoke-test.py", "--", node, launcher, cwd=temp, env=environment)


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
