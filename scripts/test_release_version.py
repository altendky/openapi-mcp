#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise release metadata and preparation using fixtures and mocked Git."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "release_version", Path(__file__).with_name("release-version.py")
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class FakeCommands:
    def __init__(self, root, *, fail=None, branch="main", dirty="", origin=None,
                 local_refs="", remote_refs="", after_pull=None, local_ahead=False):
        self.root = root
        self.fail = fail
        self.branch = branch
        self.dirty = dirty
        self.origin = origin or "git@github.com:altendky/openapi-mcp.git"
        self.local_refs = local_refs
        self.remote_refs = remote_refs
        self.after_pull = after_pull
        self.local_ahead = local_ahead
        self.calls = []
        self.pr_body = None

    def __call__(self, root, *command):
        if root != self.root:
            raise AssertionError("Unexpected command working directory")
        self.calls.append(command)
        if self.fail and command[:len(self.fail)] == self.fail:
            raise subprocess.CalledProcessError(1, command)
        if command[:2] == ("git", "rev-parse"):
            if command[-1] == "--show-toplevel":
                return str(root)
            return "local-only" if self.local_ahead and command[-1] == "HEAD" else "main-commit"
        if command[:2] == ("git", "symbolic-ref"):
            return self.branch
        if command[:2] == ("git", "status"):
            return self.dirty
        if command[:2] == ("git", "remote"):
            return self.origin
        if command[:2] == ("git", "pull") and self.after_pull:
            self.after_pull()
        if command[:2] == ("git", "for-each-ref"):
            return self.local_refs
        if command[:2] == ("git", "ls-remote"):
            return self.remote_refs
        if command[:3] == ("gh", "pr", "create"):
            self.pr_body = Path(command[command.index("--body-file") + 1]).read_text()
            return "https://github.com/altendky/openapi-mcp/pull/100"
        return ""


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(os.environ.get("TMPDIR", "/tmp")) / "agents"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="release-test-", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        dependencies = "".join(
            f'{name} = {{ path = "crates/{name}", version = "0.1.0" }}\n'
            for name in release.CRATES[:-1]
        )
        self.write("Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n\n'
                   '[workspace.package]\nversion = "0.1.0" # release version\n'
                   'edition = "2024"\n\n[workspace.dependencies]\n' + dependencies
                   + 'external = { version = "0.1.0" } # do not update\n')
        self.external_lock = (
            '[[package]]\nname = "external"\nversion = "0.1.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
            'checksum = "unchanged"\n\n'
            '[[package]]\nname = "openapi-mcp-spec"\nversion = "0.1.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
            'checksum = "also-unchanged"\n\n'
        )
        packages = "".join(
            f'[[package]]\nname = "{name}"\nversion = "0.1.0"\n\n'
            for name in release.CRATES
        )
        self.write("Cargo.lock", "# preserved comment\nversion = 4\n\n"
                   + self.external_lock + packages)
        for name in release.CRATES:
            self.write(f"crates/{name}/Cargo.toml",
                       f'[package]\nname = "{name}"\nversion.workspace = true\n')
        for name in ("openapi-mcp-rs", *release.PLATFORMS):
            package_name = name if name == "openapi-mcp-rs" else f"@openapi-mcp-rs/{name}"
            self.write_json(f"npm/{name}/package.json", {
                "name": package_name, "version": "0.1.0",
                "dependencies": {"external": "^0.1.0"},
            })
        self.write_json("npm/openapi-mcp-rs/package-lock.json", {
            "name": "openapi-mcp-rs", "version": "0.1.0", "lockfileVersion": 3,
            "packages": {
                "": {"name": "openapi-mcp-rs", "version": "0.1.0"},
                "node_modules/external": {
                    "version": "0.1.0", "integrity": "unchanged", "resolved": "https://example.com/a.tgz",
                },
            },
        })

    def write(self, path, content):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def write_json(self, path, value):
        self.write(path, json.dumps(value, indent=2) + "\n")

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_strict_versions_and_stable_tags(self):
        for value in ("0.0.0", "1.2.3", "1.2.3-dev.0", "1.2.3-dev.10"):
            release.parse_version(value)
        for value in ("v1.2.3", "1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3-dev.01",
                      "1.2.3-alpha.0", "1.2.3-dev", "1.2.3+build", "1.2.3\n", " 1.2.3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.parse_version(value)
        self.assertEqual(release.check_versions(self.root, "v0.1.0"), "0.1.0")
        for tag in ("0.1.0", "v0.1.1", "v0.1.0-dev.0", "v00.1.0"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.check_versions(self.root, tag)

    def test_synchronizes_only_owned_version_fields(self):
        before = self.snapshot()
        changed = release.synchronize(self.root, "0.2.0-dev.3")
        self.assertEqual(release.check_versions(self.root), "0.2.0-dev.3")
        self.assertEqual(set(changed), set(release.version_files(self.root)))
        manifest = tomllib.loads((self.root / "Cargo.toml").read_text())
        self.assertEqual(manifest["workspace"]["dependencies"]["external"]["version"], "0.1.0")
        self.assertIn(self.external_lock, (self.root / "Cargo.lock").read_text())
        lock = json.loads((self.root / "npm/openapi-mcp-rs/package-lock.json").read_text())
        original = json.loads(before["npm/openapi-mcp-rs/package-lock.json"])
        self.assertEqual(lock["packages"]["node_modules/external"],
                         original["packages"]["node_modules/external"])
        for name in release.CRATES:
            path = f"crates/{name}/Cargo.toml"
            self.assertEqual((self.root / path).read_bytes(), before[path])
        self.assertEqual(release.synchronize(self.root, "0.2.0-dev.3"), [])

    def test_additional_platform_lock_root_is_synchronized(self):
        self.write_json("npm/linux-x64/package-lock.json", {
            "name": "@openapi-mcp-rs/linux-x64", "version": "0.1.0", "lockfileVersion": 3,
            "packages": {"": {"name": "@openapi-mcp-rs/linux-x64", "version": "0.1.0"}},
        })
        release.synchronize(self.root, "0.1.1")
        self.assertEqual(release.check_versions(self.root, "v0.1.1"), "0.1.1")

    def test_drift_is_rejected_before_any_writes(self):
        path = self.root / "Cargo.lock"
        path.write_text(path.read_text().replace('name = "openapi-mcp"\nversion = "0.1.0"',
                                                'name = "openapi-mcp"\nversion = "0.0.9"'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Cargo.lock"):
            release.synchronize(self.root, "0.1.1")
        self.assertEqual(self.snapshot(), before)

    def test_detects_npm_root_lock_drift(self):
        path = self.root / "npm/openapi-mcp-rs/package-lock.json"
        lock = json.loads(path.read_text())
        lock["packages"][""]["version"] = "0.0.9"
        self.write_json(path.relative_to(self.root), lock)
        with self.assertRaisesRegex(ValueError, "root package"):
            release.check_versions(self.root)

    def test_detects_path_dependency_and_crate_inheritance_drift(self):
        path = self.root / "Cargo.toml"
        original = path.read_text()
        path.write_text(original.replace('version = "0.1.0" }', 'version = "0.0.9" }', 1))
        with self.assertRaisesRegex(ValueError, "path dependency"):
            release.check_versions(self.root)
        path.write_text(original)
        self.write("crates/openapi-mcp/Cargo.toml",
                   '[package]\nname = "openapi-mcp"\nversion = "0.1.0"\n')
        with self.assertRaisesRegex(ValueError, "inherit"):
            release.check_versions(self.root)

    def test_missing_platform_is_rejected(self):
        (self.root / "npm/win32-x64/package.json").unlink()
        with self.assertRaisesRegex(ValueError, "five npm"):
            release.check_versions(self.root)

    def test_next_requires_stable_version(self):
        self.assertEqual(release.next_version(self.root), "0.1.1-dev.0")
        release.synchronize(self.root, "0.1.1-dev.0")
        with self.assertRaises(ValueError):
            release.next_version(self.root)

    def test_prepare_initial_version_uses_signed_empty_commit_and_body_file(self):
        commands = FakeCommands(self.root)
        before = self.snapshot()
        result = release.prepare(self.root, "0.1.0", runner=commands)
        self.assertTrue(result.endswith("/pull/100"))
        self.assertEqual(self.snapshot(), before)
        self.assertIn(("git", "pull", "--ff-only", "origin", "main"), commands.calls)
        self.assertIn(("git", "commit", "--gpg-sign", "--allow-empty", "-m", "Release v0.1.0"),
                      commands.calls)
        self.assertFalse(any(command[:2] == ("git", "add") for command in commands.calls))
        self.assertIn("0.1.0", commands.pr_body)
        self.assertFalse(any("--force" in arg for command in commands.calls for arg in command))

    def test_prepare_signing_failure_stops_without_push_or_retry(self):
        commands = FakeCommands(self.root, fail=("git", "commit"))
        with self.assertRaises(subprocess.CalledProcessError):
            release.prepare(self.root, "0.1.1", runner=commands)
        self.assertEqual(commands.calls[-1][:2], ("git", "commit"))
        self.assertEqual(sum(command[:2] == ("git", "commit") for command in commands.calls), 1)
        self.assertEqual(release.check_versions(self.root), "0.1.1")
        add = next(command for command in commands.calls if command[:2] == ("git", "add"))
        self.assertEqual(set(add[3:]), {str(path.relative_to(self.root))
                                      for path in release.version_files(self.root)})

    def test_prepare_preconditions_fail_before_branch_creation(self):
        for options in ({"branch": "feature"}, {"dirty": "?? file"},
                        {"origin": "git@github.com:someone/other.git"},
                        {"local_refs": "refs/tags/v0.1.0"},
                        {"remote_refs": "abc\trefs/heads/release/v0.1.0"}):
            with self.subTest(options=options):
                commands = FakeCommands(self.root, **options)
                with self.assertRaises(ValueError):
                    release.prepare(self.root, "0.1.0", runner=commands)
                self.assertFalse(any(command[:2] == ("git", "switch") for command in commands.calls))

    def test_prepare_rejects_downgrade_including_after_pull(self):
        commands = FakeCommands(self.root)
        with self.assertRaisesRegex(ValueError, "downgrade"):
            release.prepare(self.root, "0.0.9", runner=commands)
        self.assertFalse(any(command[:2] == ("git", "pull") for command in commands.calls))
        commands = FakeCommands(self.root, after_pull=lambda: release.synchronize(self.root, "0.2.0"))
        with self.assertRaisesRegex(ValueError, "downgrade"):
            release.prepare(self.root, "0.1.0", runner=commands)
        self.assertFalse(any(command[:2] == ("git", "switch") for command in commands.calls))

    def test_prepare_rejects_local_unpushed_main(self):
        commands = FakeCommands(self.root, local_ahead=True)
        with self.assertRaisesRegex(ValueError, "absent from origin/main"):
            release.prepare(self.root, "0.1.1", runner=commands)
        self.assertFalse(any(command[:2] == ("git", "switch") for command in commands.calls))

    def test_prepare_network_failure_stops_at_first_command(self):
        for failure in (("git", "pull"), ("git", "ls-remote"), ("git", "push"), ("gh", "pr", "create")):
            with self.subTest(failure=failure):
                commands = FakeCommands(self.root, fail=failure)
                with self.assertRaises(subprocess.CalledProcessError):
                    release.prepare(self.root, "0.1.0", runner=commands)
                self.assertEqual(commands.calls[-1][:len(failure)], failure)

    def test_template_requires_explicit_completed_body(self):
        self.write(".github/pull_request_template.md", "## Summary\n\n## Validation\n")
        commands = FakeCommands(self.root)
        with self.assertRaisesRegex(ValueError, "--body-file"):
            release.prepare(self.root, "0.1.0", runner=commands)
        self.assertFalse(any(command[:2] == ("git", "pull") for command in commands.calls))
        self.write("body.md", "## Summary\n\nPrepare release.\n\n## Validation\n\nVersion check.\n")
        release.prepare(self.root, "0.1.0", body_file=self.root / "body.md", runner=commands)
        self.assertEqual(commands.pr_body, (self.root / "body.md").read_text())


if __name__ == "__main__":
    unittest.main()
