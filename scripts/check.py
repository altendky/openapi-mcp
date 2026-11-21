#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run the same project checks locally and in CI."""

import argparse
import copy
import json
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
NPM_AUDIT_ISSUE = "https://github.com/altendky/openapi-mcp/issues/169"
# Review by 2026-12-21; remove after a supported upstream coverage update clears both advisories.
NPM_AUDIT_EXCEPTIONS = {
    "glob": ("13.0.6", "https://github.com/advisories/GHSA-3g3m-2fjr-6pw4"),
    "minimatch": ("10.2.4", "https://github.com/advisories/GHSA-g3hv-75wg-m249"),
}
COVERAGE_AUDIT_REFS = {
    "c8": {"test-exclude"}, "test-exclude": {"glob", "minimatch"},
    "glob": {"minimatch"}, "minimatch": set(),
}
AUDIT_SEVERITIES = ("info", "low", "moderate", "high", "critical")


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


def filter_npm_audit(report, lock):
    """Apply issue 169 only to the reviewed development coverage graph."""
    if (not isinstance(report, dict) or "error" in report
            or type(report.get("auditReportVersion")) is not int or report["auditReportVersion"] != 2
            or not isinstance(report.get("vulnerabilities"), dict)
            or not isinstance(report.get("metadata"), dict)):
        raise ValueError("unsupported or failed npm audit report")
    if (not isinstance(lock, dict) or type(lock.get("lockfileVersion")) is not int or lock["lockfileVersion"] != 3
            or not isinstance(lock.get("packages"), dict)):
        raise ValueError("unsupported npm package lock")
    findings = report["vulnerabilities"]
    for name, item in findings.items():
        if (not isinstance(name, str) or not name or not isinstance(item, dict) or item.get("name") != name
                or item.get("severity") not in AUDIT_SEVERITIES
                or not isinstance(item.get("via"), list) or not item["via"]
                or not isinstance(item.get("nodes"), list) or not item["nodes"]
                or not all(isinstance(node, str) and node for node in item["nodes"])
                or not isinstance(item.get("effects"), list)):
            raise ValueError(f"invalid npm audit finding: {name}")
        for via in item["via"]:
            if isinstance(via, str):
                if via not in findings:
                    raise ValueError(f"missing npm audit reference: {via}")
            elif (not isinstance(via, dict) or via.get("severity") not in AUDIT_SEVERITIES
                  or not all(isinstance(via.get(key), str) and via[key]
                             for key in ("name", "dependency", "url", "title"))):
                raise ValueError(f"invalid npm audit advisory: {name}")
        if any(not isinstance(effect, str) or effect not in findings for effect in item["effects"]):
            raise ValueError(f"missing npm audit effect: {name}")
    counts = report["metadata"].get("vulnerabilities")
    expected_counts = {level: sum(item["severity"] == level for item in findings.values())
                       for level in AUDIT_SEVERITIES}
    expected_counts["total"] = len(findings)
    if (not isinstance(counts, dict) or counts != expected_counts
            or any(type(value) is not int for value in counts.values())):
        raise ValueError("inconsistent npm audit vulnerability counts")

    # Traverse only npm's reported vulnerability links, never resolve dependencies.
    visited, visiting = set(), set()
    def visit(name):
        if name in visiting:
            raise ValueError("cyclic npm audit references")
        if name not in visited:
            visiting.add(name)
            for via in findings[name]["via"]:
                if isinstance(via, str):
                    visit(via)
            visiting.remove(name)
            visited.add(name)
    for name in findings:
        visit(name)

    packages = lock["packages"]
    expected_edges = {("", "devDependencies", "c8")}
    expected_edges.update((f"node_modules/{parent}", "dependencies", child)
                          for parent, children in COVERAGE_AUDIT_REFS.items() for child in children)
    edges = set()
    for path, package in packages.items():
        if not isinstance(package, dict):
            raise ValueError("invalid npm package lock entry")
        for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            dependencies = package.get(section, {})
            if not isinstance(dependencies, dict):
                raise ValueError("invalid npm package lock dependencies")
            edges.update((path, section, child) for child in dependencies if child in COVERAGE_AUDIT_REFS)
    expected_refs = {(parent, child) for parent, children in COVERAGE_AUDIT_REFS.items() for child in children}
    actual_refs = {(parent, via) for parent, item in findings.items()
                   for via in item["via"] if isinstance(via, str) and via in COVERAGE_AUDIT_REFS}
    scoped = edges == expected_edges and actual_refs == expected_refs
    scoped &= all(not set(item["effects"]).intersection(COVERAGE_AUDIT_REFS)
                  for name, item in findings.items() if name not in COVERAGE_AUDIT_REFS)
    for name in COVERAGE_AUDIT_REFS:
        node = f"node_modules/{name}"
        scoped &= (name in findings and findings[name]["nodes"] == [node]
                   and findings[name]["severity"] == "moderate"
                   and isinstance(packages.get(node), dict) and packages[node].get("dev") is True)
        if name in findings:
            parents = {parent for parent, children in COVERAGE_AUDIT_REFS.items() if name in children}
            scoped &= set(findings[name]["effects"]) == parents
            references = [via for via in findings[name]["via"] if isinstance(via, str)]
            advisories = [via for via in findings[name]["via"] if isinstance(via, dict)]
            scoped &= set(references) == COVERAGE_AUDIT_REFS[name] and len(references) == len(set(references))
            if name in NPM_AUDIT_EXCEPTIONS:
                _, url = NPM_AUDIT_EXCEPTIONS[name]
                scoped &= (len(advisories) == 1 and advisories[0]["url"] == url
                           and advisories[0]["name"] == name and advisories[0]["dependency"] == name
                           and advisories[0]["severity"] == "moderate")
            else:
                scoped &= not advisories
    for name, (version, _) in NPM_AUDIT_EXCEPTIONS.items():
        scoped &= packages.get(f"node_modules/{name}", {}).get("version") == version
    for name, version in (("c8", "12.0.0"), ("test-exclude", "8.0.0")):
        scoped &= packages.get(f"node_modules/{name}", {}).get("version") == version

    filtered = copy.deepcopy(report)
    active = filtered["vulnerabilities"]
    suppressed = []
    if scoped:
        # A changed or mixed coverage finding keeps the entire chain visible.
        for name in COVERAGE_AUDIT_REFS:
            del active[name]
        suppressed = sorted(url for _, url in NPM_AUDIT_EXCEPTIONS.values())
    counts = dict.fromkeys(AUDIT_SEVERITIES, 0)
    for item in active.values():
        counts[item["severity"]] += 1
    filtered["metadata"]["vulnerabilities"] = {**counts, "total": len(active)}
    return filtered, suppressed


def npm_audit():
    executable = shutil.which("npm")
    if not executable:
        raise RuntimeError("Node.js and npm are required; run mise install --locked")
    command = [executable, "--prefix", str(NPM_ROOT), "audit", "--json", "--audit-level=high"]
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    try:
        report, suppressed = filter_npm_audit(json.loads(result.stdout),
                                            json.loads((NPM_ROOT / "package-lock.json").read_text()))
    except (ValueError, OSError) as error:
        print(f"npm audit failed: {error}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode or 1, command) from error
    if suppressed:
        print(f"Accepted moderate development advisories ({NPM_AUDIT_ISSUE}): " + ", ".join(suppressed))
    print(json.dumps(report, indent=2))
    if result.returncode or any(
        item["severity"] in ("high", "critical")
        or any(isinstance(via, dict) and via["severity"] in ("high", "critical")
               for via in item["via"])
        for item in report["vulnerabilities"].values()
    ):
        raise subprocess.CalledProcessError(result.returncode or 1, command)


def npm_security():
    run(sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_npm_audit.py")
    npm("ci", "--no-audit")
    npm_audit()


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
        npm_security()
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
