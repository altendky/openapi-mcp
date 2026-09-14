#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Test registry readiness and smoke failures without installing or publishing packages."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import release_publish as publish


class RegistrySmokeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sleeps = []
        self.install_results = [0]
        self.probe_results = [0]
        self.install_directories = []
        self.cache_directories = []
        self.probe_directories = []
        self.enterContext(patch.object(publish.time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(patch.object(publish.time, "sleep", side_effect=self.sleep))
        self.enterContext(patch.object(publish, "NPM_WAIT_SECONDS", 25))
        self.enterContext(patch.object(publish, "version", return_value="1.2.3"))
        self.enterContext(patch.object(publish.shutil, "which", side_effect={"npm": "npm-test", "node": "node-test"}.get))
        self.enterContext(patch.dict(os.environ, {"OPENAPI_MCP_NPM_COMMAND": "must-not-run"}))
        self.process = self.enterContext(patch.object(publish.subprocess, "run", side_effect=self.run_process))
        self.launcher = self.enterContext(patch.object(publish.subprocess, "check_output", return_value="openapi-mcp 1.2.3\n"))
        self.stdio = self.enterContext(patch.object(publish, "run"))

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    @staticmethod
    def next_result(results):
        return results.pop(0) if len(results) > 1 else results[0]

    def run_process(self, args, *, cwd, env, timeout):
        directory = Path(cwd)
        self.assertNotIn("OPENAPI_MCP_NPM_COMMAND", env)
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 25 - self.now)
        if args[:2] == ["npm-test", "install"]:
            for old_directory in self.install_directories:
                self.assertFalse(old_directory.exists(), "failed installation must be removed before retrying")
            self.assertEqual(json.loads((directory / "package.json").read_text()), {"private": True})
            self.assertFalse((directory / "package-lock.json").exists())
            self.assertFalse((directory / "node_modules").exists())
            cache = Path(args[args.index("--cache") + 1])
            self.assertFalse(cache.exists())
            self.assertNotIn(cache, self.cache_directories)
            self.assertIn("openapi-mcp-rs@1.2.3", args)
            self.assertIn("--include=optional", args)
            self.assertIn("--ignore-scripts", args)
            self.assertEqual(args[args.index("--registry") + 1], "https://registry.npmjs.org")
            self.install_directories.append(directory)
            self.cache_directories.append(cache)
            # Model the state a successful-but-incomplete install leaves behind.
            (directory / "package-lock.json").write_text('{"omitted":true}')
            (directory / "node_modules").mkdir()
            (directory / "node_modules/.package-lock.json").write_text('{"omitted":true}')
            cache.mkdir()
            (cache / "stale-metadata").write_text("native package missing")
            return subprocess.CompletedProcess(args, self.next_result(self.install_results))
        self.assertEqual(args[:3], ["node-test", "-e", publish.NATIVE_PACKAGE_CHECK])
        self.assertEqual(Path(args[3]), directory / "node_modules/openapi-mcp-rs/lib.js")
        self.probe_directories.append(directory)
        return subprocess.CompletedProcess(args, self.next_result(self.probe_results))

    def assert_cleaned_up(self, installations):
        self.assertEqual(len(self.install_directories), installations)
        self.assertEqual(len(set(self.install_directories)), installations)
        for directory in self.install_directories:
            self.assertFalse(directory.exists())
        for directory in self.cache_directories:
            self.assertFalse(directory.exists())

    def test_zero_exit_without_native_package_retries_with_fresh_project_and_cache(self):
        self.probe_results = [publish.NATIVE_PACKAGE_MISSING, 0]
        publish.smoke()
        self.assert_cleaned_up(2)
        self.assertEqual(len(self.probe_directories), 2)
        self.assertEqual(self.sleeps, [10])
        self.launcher.assert_called_once()
        args, kwargs = self.launcher.call_args
        installed = self.install_directories[-1]
        self.assertEqual(args[0], ["node-test", str(installed / "node_modules/openapi-mcp-rs/bin.js"), "--version"])
        self.assertEqual(Path(kwargs["cwd"]), installed)
        self.assertNotIn("OPENAPI_MCP_NPM_COMMAND", kwargs["env"])
        self.stdio.assert_called_once()
        self.assertEqual(Path(self.stdio.call_args.kwargs["cwd"]), installed)
        self.assertNotIn("OPENAPI_MCP_NPM_COMMAND", self.stdio.call_args.kwargs["env"])

    def test_nonzero_install_is_retried_before_probing(self):
        self.install_results = [1, 0]
        publish.smoke()
        self.assert_cleaned_up(2)
        self.assertEqual(self.probe_directories, self.install_directories[1:])
        self.assertEqual(self.sleeps, [10])
        self.launcher.assert_called_once()
        self.stdio.assert_called_once()

    def test_missing_native_package_has_a_bounded_deadline(self):
        self.probe_results = [publish.NATIVE_PACKAGE_MISSING]
        with self.assertRaisesRegex(RuntimeError, "Timed out waiting for npm installation.*after 25s"):
            publish.smoke()
        self.assert_cleaned_up(3)
        self.assertEqual(self.sleeps, [10, 10, 5])
        self.assertEqual(self.now, 25)
        self.launcher.assert_not_called()
        self.stdio.assert_not_called()

    def test_failed_installs_have_a_bounded_deadline(self):
        self.install_results = [1]
        with self.assertRaisesRegex(RuntimeError, "Timed out waiting for npm installation"):
            publish.smoke()
        self.assert_cleaned_up(3)
        self.assertEqual(self.probe_directories, [])
        self.assertEqual(self.now, 25)
        self.launcher.assert_not_called()
        self.stdio.assert_not_called()

    def test_fatal_readiness_error_is_not_retried(self):
        self.probe_results = [1]
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            publish.smoke()
        self.assertEqual(caught.exception.returncode, 1)
        self.assert_cleaned_up(1)
        self.assertEqual(self.sleeps, [])
        self.launcher.assert_not_called()
        self.stdio.assert_not_called()

    def assert_launcher_error_is_fatal(self, status, message):
        error = subprocess.CalledProcessError(status, ["node-test", "bin.js", "--version"], stderr=message)
        self.launcher.side_effect = error
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            publish.smoke()
        self.assertIs(caught.exception, error)
        self.assert_cleaned_up(1)
        self.assertEqual(self.sleeps, [])
        self.launcher.assert_called_once()
        self.stdio.assert_not_called()

    def test_installed_package_missing_binary_is_not_reinstalled(self):
        self.assert_launcher_error_is_fatal(1, "Error: Unsupported platform: linux-x64")

    def test_broken_binary_is_not_reinstalled(self):
        self.assert_launcher_error_is_fatal(1, "spawnSync openapi-mcp EACCES")

    def test_launcher_failure_is_not_reinstalled(self):
        self.assert_launcher_error_is_fatal(42, "launcher failed")

    def test_wrong_binary_version_is_not_reinstalled(self):
        self.launcher.return_value = "openapi-mcp 9.9.9\n"
        with self.assertRaisesRegex(ValueError, "installed registry binary version mismatch"):
            publish.smoke()
        self.assert_cleaned_up(1)
        self.assertEqual(self.sleeps, [])
        self.launcher.assert_called_once()
        self.stdio.assert_not_called()

    def test_stdio_failure_is_not_reinstalled(self):
        error = subprocess.CalledProcessError(1, ["smoke-test.py"], stderr="invalid initialize response")
        self.stdio.side_effect = error
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            publish.smoke()
        self.assertIs(caught.exception, error)
        self.assert_cleaned_up(1)
        self.assertEqual(self.sleeps, [])
        self.launcher.assert_called_once()
        self.stdio.assert_called_once()


class NativePackageProbeTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if self.node is None:
            self.skipTest("Node.js is required for the real package-resolution probe")
        self.temp = tempfile.TemporaryDirectory(prefix="openapi-native-probe-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        self.modules = self.root / "node_modules"
        wrapper = self.modules / "openapi-mcp-rs"
        wrapper.mkdir(parents=True)
        self.wrapper = wrapper / "lib.js"
        self.wrapper.write_bytes((publish.ROOT / "npm/openapi-mcp-rs/lib.js").read_bytes())
        # The real launcher imports shell-quote, which is unrelated to readiness.
        dependency = self.modules / "shell-quote"
        dependency.mkdir()
        (dependency / "index.js").write_text("module.exports = {parse: () => []};\n")
        self.environment = {**os.environ, "NODE_PATH": ""}
        platform = subprocess.check_output([
            self.node, "-p", "`${process.platform}-${process.arch}`",
        ], text=True, env=self.environment).strip()
        if platform not in publish.TARGETS:
            self.skipTest(f"fixture host is unsupported: {platform}")
        self.native = self.modules / "@openapi-mcp-rs" / platform
        self.package_name = f"@openapi-mcp-rs/{platform}"

    def probe(self):
        return subprocess.run([
            self.node, "-e", publish.NATIVE_PACKAGE_CHECK, str(self.wrapper),
        ], cwd=self.root, env=self.environment, text=True, capture_output=True, timeout=10)

    def add_native_manifest(self, contents=None):
        self.native.mkdir(parents=True)
        (self.native / "package.json").write_text(contents if contents is not None else json.dumps({
            "name": self.package_name, "version": "1.2.3",
        }))

    def assert_fatal(self, result):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(result.returncode, publish.NATIVE_PACKAGE_MISSING, result.stderr)
        self.assertTrue(result.stderr)

    def test_absent_native_package_has_the_retryable_exit_code(self):
        result = self.probe()
        self.assertEqual(result.returncode, publish.NATIVE_PACKAGE_MISSING, result.stderr)

    def test_present_native_manifest_is_ready_even_without_a_binary(self):
        self.add_native_manifest()
        result = self.probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.native / "bin").exists())

    def test_unsupported_platform_is_fatal(self):
        with self.wrapper.open("a") as stream:
            stream.write("\nObject.defineProperty(process, 'platform', {value: 'unsupported'});\n")
        result = self.probe()
        self.assert_fatal(result)
        self.assertIn("Unsupported platform: unsupported-", result.stderr)

    def test_malformed_native_manifest_is_fatal(self):
        self.add_native_manifest("{")
        result = self.probe()
        self.assert_fatal(result)
        self.assertIn("package", result.stderr.lower())

    def test_broken_wrapper_import_is_fatal(self):
        self.wrapper.write_text("this is not valid JavaScript\n")
        result = self.probe()
        self.assert_fatal(result)
        self.assertIn("SyntaxError", result.stderr)

    def test_missing_required_wrapper_dependency_is_fatal(self):
        shutil.rmtree(self.modules / "shell-quote")
        result = self.probe()
        self.assert_fatal(result)
        self.assertIn("shell-quote", result.stderr)


if __name__ == "__main__":
    unittest.main()
