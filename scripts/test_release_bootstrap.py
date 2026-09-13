#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check initial npm publication guards without credentials or publication."""

import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import bootstrap_npm as bootstrap


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openapi-bootstrap-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for name in ("release.json", "SHA256SUMS", "openapi-mcp-0.1.0.crate", "npm.tgz"):
            (self.directory / name).write_bytes(name.encode())
        self.module = Mock(CRATES=["openapi-mcp"])
        self.module.sha256.side_effect = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        checksum = self.module.sha256(self.directory / "openapi-mcp-0.1.0.crate")
        self.module.get_json.return_value = {"version": {"checksum": checksum}}
        self.run = {
            "id": bootstrap.SOURCE_RUN, "head_sha": bootstrap.SOURCE_SHA,
            "head_branch": "main", "event": "push", "path": ".github/workflows/ci.yml",
            "status": "completed", "conclusion": "success",
            "repository": {"full_name": bootstrap.REPOSITORY},
        }
        self.tag = {"object": {"type": "commit", "sha": bootstrap.SOURCE_SHA}}
        self.release = {"tag_name": bootstrap.TAG, "prerelease": False, "assets": [
            {"name": path.name, "digest": f"sha256:{self.module.sha256(path)}"}
            for path in self.directory.iterdir()
        ]}
        responses = {
            f"actions/runs/{bootstrap.SOURCE_RUN}": self.run,
            f"git/ref/tags/{bootstrap.TAG}": self.tag,
            f"compare/{bootstrap.SOURCE_SHA}...main": {"status": "ahead"},
            f"releases/{bootstrap.RELEASE_ID}": self.release,
        }
        self.api = patch.object(bootstrap, "github", side_effect=responses.__getitem__).start()
        patch.object(bootstrap, "publisher", return_value=self.module).start()
        patch.object(bootstrap, "MANIFEST_SHA256", self.module.sha256(self.directory / "release.json")).start()
        self.addCleanup(patch.stopall)

    def check(self):
        return bootstrap.preflight(Path("source"), self.directory)

    def test_original_bundle_is_checked_without_publication(self):
        self.assertIs(self.check(), self.module)
        self.module.verify.assert_called_once_with(self.directory)
        self.module.npm.assert_not_called()
        self.module.github.assert_not_called()

    def test_different_build_manifest_stops_before_remote_checks(self):
        (self.directory / "release.json").write_bytes(b"another valid build")
        with self.assertRaisesRegex(ValueError, "original merged-main"):
            self.check()
        self.api.assert_not_called()

    def test_only_successful_original_main_ci_is_accepted(self):
        for key, value in (("conclusion", "failure"), ("head_sha", "different"), ("event", "pull_request")):
            with self.subTest(key=key):
                original = self.run[key]
                self.run[key] = value
                with self.assertRaisesRegex(ValueError, "successful release build"):
                    self.check()
                self.run[key] = original

    def test_moved_tag_is_rejected(self):
        self.tag["object"]["sha"] = "different"
        with self.assertRaisesRegex(ValueError, "release tag"):
            self.check()

    def test_draft_missing_or_changed_assets_are_rejected_before_registry_queries(self):
        original = copy.deepcopy(self.release["assets"])
        for assets in (original[:-1], [{**item, "digest": "sha256:changed"} for item in original]):
            with self.subTest(assets=assets):
                self.release["assets"] = assets
                with self.assertRaisesRegex(ValueError, "release assets differ"):
                    self.check()
                self.module.get_json.assert_not_called()

    def test_existing_crates_must_match(self):
        for result in (None, {"version": {"checksum": "different"}}):
            self.module.get_json.return_value = result
            with self.assertRaisesRegex(ValueError, "published crate differs"):
                self.check()


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": bootstrap.REPOSITORY,
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": f"{bootstrap.REPOSITORY}/.github/workflows/bootstrap-npm.yml@refs/heads/main",
            "BOOTSTRAP_PUBLISH": "true", "GITHUB_SHA": "automation-sha", "GITHUB_RUN_ID": "123",
            "NODE_AUTH_TOKEN": "test-placeholder", "BOOTSTRAP_REGISTRY_RESULT": "success",
        }
        self.run = {
            "head_sha": "automation-sha", "head_branch": "main", "event": "workflow_dispatch",
            "path": ".github/workflows/bootstrap-npm.yml", "run_attempt": 1,
        }
        responses = {"actions/runs/123": self.run}
        self.api = patch.object(bootstrap, "github", side_effect=responses.__getitem__).start()
        patch.object(bootstrap, "git", return_value="automation-sha").start()
        self.addCleanup(patch.stopall)

    def authorize(self, command="npm"):
        with patch.dict(os.environ, self.environment, clear=True):
            before = dict(os.environ)
            bootstrap.authorize(command)
            self.assertEqual(dict(os.environ), before)

    def test_explicit_main_dispatch_preserves_real_workflow_identity(self):
        self.authorize()

    def test_default_and_non_main_dispatch_cannot_publish(self):
        for key, value in (("BOOTSTRAP_PUBLISH", "false"), ("GITHUB_REF", "refs/tags/v0.1.0"),
                           ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "other/repo")):
            with self.subTest(key=key):
                original = self.environment[key]
                self.environment[key] = value
                with self.assertRaisesRegex(ValueError, "explicit bootstrap dispatch"):
                    self.authorize()
                self.environment[key] = original
                self.api.assert_not_called()

    def test_missing_token_stops_publication(self):
        del self.environment["NODE_AUTH_TOKEN"]
        with self.assertRaisesRegex(ValueError, "temporary bootstrap token"):
            self.authorize()

    def test_actual_workflow_run_must_match(self):
        self.run["head_sha"] = bootstrap.SOURCE_SHA
        with self.assertRaisesRegex(ValueError, "actual workflow run"):
            self.authorize()

    def test_finalization_requires_success_on_every_platform(self):
        self.authorize("github")
        for conclusion in ("failure", "skipped", "cancelled", ""):
            self.environment["BOOTSTRAP_REGISTRY_RESULT"] = conclusion
            with self.assertRaisesRegex(ValueError, "all five registry smoke"):
                self.authorize("github")

    def test_unverified_source_cannot_supply_publishing_code(self):
        for answers in (("wrong-sha",), (bootstrap.SOURCE_SHA, " M scripts/release_publish.py")):
            with patch.object(bootstrap, "git", side_effect=answers), patch.object(bootstrap.importlib, "import_module") as load:
                with self.assertRaisesRegex(ValueError, "source checkout"):
                    bootstrap.publisher(Path("source"))
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
