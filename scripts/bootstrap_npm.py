#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Promote the original 0.1.0 bundle without rebuilding it or moving its tag."""

import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "altendky/openapi-mcp"
SOURCE_SHA = "eb1a00812284b4f5e723c13e2a2a51e9ea8730df"
SOURCE_RUN = 34768353819
RELEASE_ID = 387965177
TAG = "v0.1.0"
MANIFEST_SHA256 = "5be632204f2f1a41a070bb4ed9f18be8490a4f5c0f448d8bfc990e5a2bcf1841"


def github(endpoint):
    return json.loads(subprocess.check_output([
        "gh", "api", f"repos/{REPOSITORY}/{endpoint}",
    ], text=True))


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def publisher(source):
    if git(source, "rev-parse", "HEAD") != SOURCE_SHA:
        raise ValueError("source checkout must be the original release commit")
    if git(source, "status", "--porcelain"):
        raise ValueError("source checkout must be clean")
    sys.path.insert(0, str(source / "scripts"))
    module = importlib.import_module("release_publish")
    if module.ROOT.resolve() != source.resolve():
        raise ValueError("publisher must come from the verified release checkout")
    return module


def preflight(source, directory):
    module = publisher(source)
    module.verify(directory)
    if module.sha256(directory / "release.json") != MANIFEST_SHA256:
        raise ValueError("bundle is not the original merged-main artifact")
    run = github(f"actions/runs/{SOURCE_RUN}")
    expected = {
        "id": SOURCE_RUN, "head_sha": SOURCE_SHA, "head_branch": "main",
        "event": "push", "path": ".github/workflows/ci.yml",
        "status": "completed", "conclusion": "success",
    }
    if any(run.get(key) != value for key, value in expected.items()):
        raise ValueError("original CI run does not match the successful release build")
    if run.get("repository", {}).get("full_name") != REPOSITORY:
        raise ValueError("original CI run belongs to another repository")
    tag = github(f"git/ref/tags/{TAG}")
    if tag.get("object", {}).get("type") != "commit" or tag["object"].get("sha") != SOURCE_SHA:
        raise ValueError("release tag no longer points to the original commit")
    if github(f"compare/{SOURCE_SHA}...main")["status"] not in ("ahead", "identical"):
        raise ValueError("original release commit is not in main's history")
    release = github(f"releases/{RELEASE_ID}")
    if release.get("tag_name") != TAG or release.get("prerelease"):
        raise ValueError("existing release identity does not match the bootstrap")
    assets = release["assets"]
    expected_assets = {path.name: f"sha256:{module.sha256(path)}" for path in directory.iterdir()}
    actual_assets = {asset["name"]: asset.get("digest") for asset in assets}
    if len(assets) != len(expected_assets) or actual_assets != expected_assets:
        raise ValueError("existing GitHub release assets differ from the original bundle")
    for crate in module.CRATES:
        published = module.get_json(f"https://crates.io/api/v1/crates/{crate}/0.1.0")
        checksum = module.sha256(directory / f"{crate}-0.1.0.crate")
        if published is None or published["version"]["checksum"] != checksum:
            raise ValueError(f"published crate differs from the original bundle: {crate}")
    print(f"Verified original {TAG} bundle, source run, release assets, and crates.io checksums", flush=True)
    return module


def authorize(command):
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/bootstrap-npm.yml@refs/heads/main",
        "BOOTSTRAP_PUBLISH": "true",
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise ValueError("publication requires explicit bootstrap dispatch on main")
    sha = os.environ.get("GITHUB_SHA")
    if git(ROOT, "rev-parse", "HEAD") != sha:
        raise ValueError("automation checkout does not match the actual workflow commit")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdecimal():
        raise ValueError("missing workflow run identity")
    run = github(f"actions/runs/{run_id}")
    if (run.get("head_sha"), run.get("head_branch"), run.get("event"), run.get("path")) != (
        sha, "main", "workflow_dispatch", ".github/workflows/bootstrap-npm.yml",
    ):
        raise ValueError("actual workflow run does not match bootstrap authorization")
    if command == "npm" and not os.environ.get("NODE_AUTH_TOKEN"):
        raise ValueError("initial npm publication requires the temporary bootstrap token")
    # Use the matrix dependency result, which survives rerunning only failed jobs.
    if command == "github" and os.environ.get("BOOTSTRAP_REGISTRY_RESULT") != "success":
        raise ValueError("all five registry smoke jobs must pass before finalization")


def smoke(source):
    module = publisher(source)
    module.smoke()
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required")
    environment = {key: value for key, value in os.environ.items() if key != "OPENAPI_MCP_NPM_COMMAND"}
    with tempfile.TemporaryDirectory(prefix="openapi-mcp-npx-") as directory:
        actual = subprocess.check_output([
            npm, "exec", "--yes", "--ignore-scripts", "--registry=https://registry.npmjs.org",
            "--package=openapi-mcp-rs@0.1.0", "--", "openapi-mcp", "--version",
        ], cwd=directory, env=environment, text=True).strip()
    if actual != "openapi-mcp 0.1.0":
        raise ValueError(f"npx registry version mismatch: {actual}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "npm", "smoke", "github"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if args.command == "smoke":
        smoke(source)
        return
    if args.bundle is None:
        parser.error("check and publication require --bundle")
    if args.command != "check":
        authorize(args.command)
    directory = args.bundle.resolve()
    module = preflight(source, directory)
    if args.command != "check":
        getattr(module, args.command)(directory)


if __name__ == "__main__":
    main()
