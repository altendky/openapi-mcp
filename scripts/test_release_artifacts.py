#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise missing, corrupt, mismatched and partially published release artifacts."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import release_artifacts as artifacts
import release_publish as publish


def tar(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openapi-release-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs, self.output = self.root / "input", self.root / "output"
        self.version = artifacts.version()
        self.wrapper_name = f"openapi-mcp-rs-{self.version}.tgz"
        licenses = {name: (artifacts.ROOT / name).read_bytes() for name in artifacts.LICENSES}
        self.wrapper = {
            "name": "openapi-mcp-rs", "version": self.version,
            "optionalDependencies": {f"@openapi-mcp-rs/{platform}": self.version for platform in artifacts.TARGETS},
        }
        wrapper_files = {f"package/{name}": data for name, data in licenses.items()}
        wrapper_files["package/package.json"] = json.dumps(self.wrapper).encode()
        # Copy identical wrapper bytes, matching npm pack's deterministic tarball behavior.
        wrapper_archive = self.root / self.wrapper_name
        tar(wrapper_archive, wrapper_files)
        for platform in artifacts.TARGETS:
            directory = self.inputs / f"npm-{platform}"
            directory.mkdir(parents=True)
            (directory / self.wrapper_name).write_bytes(wrapper_archive.read_bytes())
            binary = "openapi-mcp.exe" if platform == "win32-x64" else "openapi-mcp"
            os_name, cpu = platform.split("-")
            manifest = {"name": f"@openapi-mcp-rs/{platform}", "version": self.version, "os": [os_name], "cpu": [cpu]}
            files = {f"package/{name}": data for name, data in licenses.items()}
            files.update({"package/package.json": json.dumps(manifest).encode(), f"package/bin/{binary}": platform.encode()})
            tar(directory / f"openapi-mcp-rs-{platform}-{self.version}.tgz", files)
            path = self.inputs / f"release-{platform}" / artifacts.archive_name(platform, self.version)
            stem = path.name.removesuffix(".zip").removesuffix(".tar.gz")
            contents = {f"{stem}/{name}": data for name, data in licenses.items()}
            contents[f"{stem}/{binary}"] = platform.encode()
            if platform == "win32-x64":
                path.parent.mkdir(parents=True)
                with zipfile.ZipFile(path, "w") as archive:
                    for name, data in contents.items():
                        archive.writestr(name, data)
            else:
                tar(path, contents)
        for crate in artifacts.CRATES:
            tar(self.inputs / "crates" / f"{crate}-{self.version}.crate", {
                f"{crate}-{self.version}/Cargo.toml": f'[package]\nname = "{crate}"\nversion = "{self.version}"\n'.encode(),
            })

    def test_complete_bundle_and_corruption(self):
        artifacts.bundle(self.inputs, self.output)
        manifest = artifacts.verify(self.output)
        self.assertEqual(len(manifest["sha256"]), 15)
        path = self.output / self.wrapper_name
        path.write_bytes(path.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            artifacts.verify(self.output)

    def test_missing_platform(self):
        (self.inputs / "npm-darwin-x64" / self.wrapper_name).unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            artifacts.bundle(self.inputs, self.output)

    def test_duplicate_wrapper_must_be_identical(self):
        path = self.inputs / "npm-linux-arm64" / self.wrapper_name
        files = artifacts.files_in_archive(path)
        files["package/extra.js"] = b"different launcher content"
        tar(path, files)
        with self.assertRaisesRegex(ValueError, "launcher tarballs differ"):
            artifacts.bundle(self.inputs, self.output)

    def test_native_archive_must_contain_tested_npm_binary(self):
        path = self.inputs / "npm-linux-x64" / f"openapi-mcp-rs-linux-x64-{self.version}.tgz"
        files = artifacts.files_in_archive(path)
        files["package/bin/openapi-mcp"] = b"wrong binary"
        tar(path, files)
        with self.assertRaisesRegex(ValueError, "binaries differ"):
            artifacts.bundle(self.inputs, self.output)

    def test_exact_optional_dependency_versions(self):
        path = self.inputs / "npm-linux-x64" / self.wrapper_name
        files = artifacts.files_in_archive(path)
        self.wrapper["optionalDependencies"]["@openapi-mcp-rs/linux-x64"] = "^" + self.version
        files["package/package.json"] = json.dumps(self.wrapper).encode()
        tar(path, files)
        with self.assertRaisesRegex(ValueError, "exact release version"):
            artifacts.bundle(self.inputs, self.output)

    def test_manifest_cannot_omit_artifact(self):
        artifacts.bundle(self.inputs, self.output)
        path = self.output / "release.json"
        manifest = json.loads(path.read_text())
        del manifest["sha256"][self.wrapper_name]
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "missing or unexpected artifacts"):
            artifacts.verify(self.output)

    def test_publish_requires_enabled_matching_tag(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(publish, "run") as command:
            with self.assertRaisesRegex(ValueError, "RELEASE_ENABLED"):
                publish.require_release()
            command.assert_not_called()
        environment = {"RELEASE_ENABLED": "true", "GITHUB_REPOSITORY": "altendky/openapi-mcp", "GITHUB_REF": "refs/heads/main"}
        with patch.dict(os.environ, environment, clear=True), patch.object(publish, "run") as command:
            with self.assertRaisesRegex(ValueError, "matching release tag"):
                publish.require_release()
            command.assert_not_called()

    def test_npm_resume_skips_identical_packages_and_publishes_wrapper_last(self):
        artifacts.bundle(self.inputs, self.output)
        def published(url):
            # All platform packages were published before an interrupted launcher upload.
            if "openapi-mcp-rs%2F" not in url:
                return None
            platform = url.split("%2F")[1].split("/")[0]
            data = (self.output / f"openapi-mcp-rs-{platform}-{self.version}.tgz").read_bytes()
            return {"dist": {"integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()}}
        wrapper_integrity = "sha512-" + base64.b64encode(hashlib.sha512((self.output / self.wrapper_name).read_bytes()).digest()).decode()
        calls = []
        def lookup(url):
            calls.append(url)
            if len(calls) == 7:
                return {"dist": {"integrity": wrapper_integrity}}
            return published(url)
        with patch.object(publish, "get_json", side_effect=lookup), patch.object(publish, "run") as command, patch.object(publish.shutil, "which", return_value="npm"):
            publish.npm(self.output)
            command.assert_called_once()
            self.assertEqual(command.call_args.args[2], self.output / self.wrapper_name)

    def test_npm_resume_rejects_different_published_bytes(self):
        artifacts.bundle(self.inputs, self.output)
        with patch.object(publish, "get_json", return_value={"dist": {"integrity": "different"}}), patch.object(publish, "run") as command, patch.object(publish.shutil, "which", return_value="npm"):
            with self.assertRaisesRegex(ValueError, "differs from this release"):
                publish.npm(self.output)
            command.assert_not_called()

    def test_native_archives_are_reproducible_and_executable(self):
        binary = self.root / "binary"
        binary.write_bytes(b"the tested executable")
        for platform in ("linux-x64", "win32-x64"):
            first, second = self.root / f"first-{platform}", self.root / f"second-{platform}"
            with patch.object(artifacts.subprocess, "check_output", return_value=f"openapi-mcp {self.version}\n"):
                artifacts.pack(binary, platform, first)
                os.utime(binary, (100, 100))
                artifacts.pack(binary, platform, second)
            name = artifacts.archive_name(platform, self.version)
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            if platform == "linux-x64":
                with tarfile.open(first / name) as archive:
                    executable = next(entry for entry in archive if entry.name.endswith("/openapi-mcp"))
                    self.assertEqual(executable.mode, 0o755)

    def test_cargo_rejects_repackaging_drift_before_upload(self):
        artifacts.bundle(self.inputs, self.output)
        with patch.object(publish, "sha256", side_effect=["new", "validated"]), patch.object(publish, "run") as command, patch.object(publish, "get_json") as registry:
            with self.assertRaisesRegex(ValueError, "repackaged crate differs"):
                publish.cargo(self.output)
            self.assertEqual(command.call_args.args[:2], ("cargo", "package"))
            command.assert_called_once()
            registry.assert_not_called()

    def test_github_release_resume_does_not_replace_assets(self):
        artifacts.bundle(self.inputs, self.output)
        release = {"tag_name": f"v{self.version}", "draft": True, "assets": [
            {"name": path.name, "digest": f"sha256:{artifacts.sha256(path)}"}
            for path in self.output.iterdir()
        ]}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "altendky/openapi-mcp"}), patch.object(publish.subprocess, "check_output", return_value=json.dumps([[release]])), patch.object(publish, "verify"), patch.object(publish, "run") as command:
            publish.github(self.output)
            command.assert_called_once()
            self.assertIn("--draft=false", command.call_args.args)
            command.reset_mock()
            release["assets"][0]["digest"] = "sha256:wrong"
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "altendky/openapi-mcp"}), patch.object(publish.subprocess, "check_output", return_value=json.dumps([[release]])), patch.object(publish, "verify"), patch.object(publish, "run") as command:
            with self.assertRaisesRegex(ValueError, "asset differs"):
                publish.github(self.output)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
