#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check bounded publication visibility waits without registry or GitHub writes."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

import release_publish as publish


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ClockTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.enterContext(patch.object(publish.time, "monotonic", self.clock.monotonic))
        self.enterContext(patch.object(publish.time, "sleep", self.clock.sleep))
        self.enterContext(redirect_stdout(io.StringIO()))


class VisibilityTests(ClockTest):
    def test_immediate_visibility_does_not_sleep(self):
        expected = {"visible": True}
        check = Mock(return_value=expected)
        self.assertIs(publish.wait_for_visibility(check, "artifact", timeout=12), expected)
        check.assert_called_once_with(12)
        self.assertEqual(self.clock.sleeps, [])

    def test_lookup_errors_are_not_retried(self):
        for error in (
            urllib.error.HTTPError("https://example.invalid", 403, "Forbidden", {}, None),
            urllib.error.URLError("connection failed"),
            subprocess.TimeoutExpired("gh", 12),
            ValueError("integrity mismatch"),
        ):
            if isinstance(error, urllib.error.HTTPError):
                self.addCleanup(error.close)
            with self.subTest(error=type(error).__name__):
                check = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    publish.wait_for_visibility(check, "artifact", timeout=12)
                self.assertIs(raised.exception, error)
                check.assert_called_once_with(12)
                self.assertEqual(self.clock.sleeps, [])

    def test_slow_absent_reads_consume_the_deadline(self):
        reads = []

        def check(remaining):
            reads.append((self.clock.now, remaining))
            self.clock.now += min(4, remaining)
            return None

        with self.assertRaisesRegex(RuntimeError, "Timed out.*artifact.*10s"):
            publish.wait_for_visibility(check, "artifact", timeout=10)
        self.assertEqual(reads, [(100, 10), (109, 1)])
        self.assertEqual(self.clock.sleeps, [5])
        self.assertEqual(self.clock.now, 110)

    def test_read_exhausting_budget_has_no_subsequent_lookup_or_sleep(self):
        def absent(_remaining):
            self.clock.now += 12
            return None

        check = Mock(side_effect=absent)
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            publish.wait_for_visibility(check, "artifact", timeout=10)
        check.assert_called_once_with(10)
        self.assertEqual(self.clock.sleeps, [])

    def test_final_sleep_is_capped_and_no_lookup_starts_at_expiry(self):
        check = Mock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            publish.wait_for_visibility(check, "artifact", timeout=12)
        self.assertEqual([call.args[0] for call in check.call_args_list], [12, 7, 2])
        self.assertEqual(self.clock.sleeps, [5, 5, 2])
        self.assertEqual(self.clock.now, 112)

    def test_expired_window_does_not_start_a_lookup(self):
        check = Mock()
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            publish.wait_for_visibility(check, "artifact", timeout=0)
        check.assert_not_called()
        self.assertEqual(self.clock.sleeps, [])


class PublisherTest(ClockTest):
    def setUp(self):
        super().setUp()
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="release-visibility-"))
        self.directory = Path(temporary)
        self.enterContext(patch.object(publish, "version", return_value="1.2.3"))
        self.verify = self.enterContext(patch.object(publish, "verify"))


class NpmVisibilityTests(PublisherTest):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(publish.shutil, "which", return_value="npm"))
        names = [f"@openapi-mcp-rs/{platform}" for platform in publish.TARGETS]
        names.append("openapi-mcp-rs")
        self.paths = {}
        self.metadata = {}
        self.original = {}
        for name in names:
            filename = name.lstrip("@").replace("/", "-") + "-1.2.3.tgz"
            path = self.directory / filename
            content = f"previously validated tarball: {name}".encode()
            path.write_bytes(content)
            self.paths[name] = path
            self.original[path] = content
            integrity = "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode()
            self.metadata[name] = {"dist": {"integrity": integrity}}

    @staticmethod
    def package_name(url):
        return urllib.parse.unquote(url.removeprefix("https://registry.npmjs.org/").rsplit("/", 1)[0])

    def assert_original_bytes(self):
        self.assertEqual({path: path.read_bytes() for path in self.directory.iterdir()}, self.original)

    def test_visibility_after_sixty_seconds_preserves_publish_order_and_bytes(self):
        uploaded = {}
        visible = set()
        mutations = []
        first = next(iter(self.paths))

        def lookup(url, *, timeout=30):
            name = self.package_name(url)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 30)
            if name not in uploaded or (name == first and self.clock.now - uploaded[name] < 75):
                return None
            visible.add(name)
            return self.metadata[name]

        def upload(*args):
            name = next(name for name, path in self.paths.items() if path == args[2])
            self.assertNotIn(name, uploaded)
            self.assertEqual(visible, set(uploaded))
            self.assertEqual(args, (
                "npm", "publish", self.paths[name], "--access", "public", "--provenance",
                "--ignore-scripts", "--registry", "https://registry.npmjs.org",
            ))
            self.assert_original_bytes()
            uploaded[name] = self.clock.now
            mutations.append(name)

        with patch.object(publish, "get_json", side_effect=lookup), patch.object(publish, "run", side_effect=upload):
            publish.npm(self.directory)
        self.assertEqual(mutations, list(self.paths))
        self.assertEqual(visible, set(self.paths))
        self.assertEqual(self.clock.now - 100, 75)
        self.verify.assert_called_once_with(self.directory)
        self.assert_original_bytes()

    def test_timeout_does_not_republish_or_advance_to_the_next_package(self):
        with patch.object(publish, "get_json", return_value=None) as lookup, patch.object(publish, "run") as command:
            with self.assertRaisesRegex(RuntimeError, "Timed out.*npm package.*300s"):
                publish.npm(self.directory)
        command.assert_called_once()
        self.assertEqual(command.call_args.args[2], next(iter(self.paths.values())))
        self.assertEqual({self.package_name(call.args[0]) for call in lookup.call_args_list}, {next(iter(self.paths))})
        self.assertEqual(lookup.call_args.kwargs["timeout"], 5)
        self.assertEqual(self.clock.now, 400)
        self.assert_original_bytes()

    def test_post_publish_integrity_failure_is_immediate(self):
        with patch.object(publish, "get_json", side_effect=[None, {"dist": {"integrity": "different"}}]) as lookup, patch.object(publish, "run") as command:
            with self.assertRaisesRegex(ValueError, "registry integrity mismatch"):
                publish.npm(self.directory)
        command.assert_called_once()
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(self.clock.sleeps, [])
        self.assert_original_bytes()

    def test_post_publish_auth_and_transport_errors_are_immediate(self):
        for error in (
            urllib.error.HTTPError("https://registry.npmjs.org", 401, "Unauthorized", {}, None),
            urllib.error.HTTPError("https://registry.npmjs.org", 403, "Forbidden", {}, None),
            urllib.error.URLError("connection failed"),
        ):
            if isinstance(error, urllib.error.HTTPError):
                self.addCleanup(error.close)
            with self.subTest(error=str(error)), patch.object(publish, "get_json", side_effect=[None, error]) as lookup, patch.object(publish, "run") as command:
                with self.assertRaises(type(error)) as raised:
                    publish.npm(self.directory)
                self.assertIs(raised.exception, error)
                command.assert_called_once()
                self.assertEqual(lookup.call_count, 2)
                self.assertEqual(self.clock.sleeps, [])
                self.assert_original_bytes()

    def test_failed_publish_is_not_retried_or_followed_by_visibility_reads(self):
        error = subprocess.CalledProcessError(1, ["npm", "publish"])
        with patch.object(publish, "get_json", return_value=None) as lookup, patch.object(publish, "run", side_effect=error) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                publish.npm(self.directory)
        command.assert_called_once()
        lookup.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])

    def test_resume_keeps_matching_packages_and_publishes_only_remaining_bytes(self):
        existing = set(list(self.paths)[:2])
        published = set(existing)
        mutations = []

        def lookup(url, **_kwargs):
            name = self.package_name(url)
            return self.metadata[name] if name in published else None

        def upload(*args):
            name = next(name for name, path in self.paths.items() if path == args[2])
            self.assertNotIn(name, published)
            published.add(name)
            mutations.append(name)

        with patch.object(publish, "get_json", side_effect=lookup), patch.object(publish, "run", side_effect=upload):
            publish.npm(self.directory)
        self.assertEqual(mutations, [name for name in self.paths if name not in existing])
        self.assertEqual(published, set(self.paths))
        self.assertEqual(self.clock.sleeps, [])
        self.assert_original_bytes()


class GitHubVisibilityTests(PublisherTest):
    def setUp(self):
        super().setUp()
        self.repository = "altendky/openapi-mcp"
        self.enterContext(patch.dict(os.environ, {"GITHUB_REPOSITORY": self.repository}))
        for filename in ("archive.tar.gz", "SHA256SUMS"):
            (self.directory / filename).write_bytes(f"validated {filename}".encode())
        self.release = {"tag_name": "v1.2.3", "draft": True, "assets": []}

    def test_draft_appears_later_without_duplicate_creation_or_early_uploads(self):
        created_at = None
        visible = False
        mutations = []
        read_timeouts = []

        def lookup(args, *, text, timeout):
            nonlocal visible
            self.assertTrue(text)
            self.assertEqual(args, ["gh", "api", "--paginate", "--slurp", f"repos/{self.repository}/releases"])
            read_timeouts.append(timeout)
            if created_at is not None and self.clock.now - created_at >= 15:
                visible = True
            return json.dumps([[{"tag_name": "v0.0.0"}], [self.release] if visible else []])

        def mutate(*args):
            nonlocal created_at
            if args[:3] == ("gh", "release", "create"):
                self.assertIsNone(created_at)
                created_at = self.clock.now
            else:
                self.assertTrue(visible, "assets must wait for confirmed draft visibility")
            mutations.append(args)

        with patch.object(publish.subprocess, "check_output", side_effect=lookup), patch.object(publish, "run", side_effect=mutate):
            publish.github(self.directory)
        self.assertEqual(read_timeouts, [None, 120, 115, 110, 105])
        self.assertEqual(mutations[0][:4], ("gh", "release", "create", "v1.2.3"))
        self.assertEqual(mutations[1:-1], [
            ("gh", "release", "upload", "v1.2.3", path, "--repo", self.repository)
            for path in sorted(self.directory.iterdir())
        ])
        self.assertEqual(mutations[-1], ("gh", "release", "edit", "v1.2.3", "--repo", self.repository, "--draft=false"))
        self.verify.assert_called_once_with(self.directory)

    def test_draft_timeout_creates_once_without_upload_or_publication(self):
        with patch.object(publish, "find_github_release", return_value=None) as lookup, patch.object(publish, "run") as command:
            with self.assertRaisesRegex(RuntimeError, "Timed out.*created GitHub draft.*120s"):
                publish.github(self.directory)
        command.assert_called_once()
        self.assertEqual(command.call_args.args[:3], ("gh", "release", "create"))
        self.assertEqual(lookup.call_args.kwargs["timeout"], 5)
        self.assertEqual(self.clock.now, 220)

    def test_api_failure_after_creation_is_not_absence(self):
        error = subprocess.CalledProcessError(1, ["gh", "api"], stderr="HTTP 403")
        with patch.object(publish, "find_github_release", side_effect=[None, error]) as lookup, patch.object(publish, "run") as command:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                publish.github(self.directory)
        self.assertIs(raised.exception, error)
        command.assert_called_once()
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(self.clock.sleeps, [])

    def test_failed_creation_is_not_retried_or_followed_by_visibility_reads(self):
        error = subprocess.CalledProcessError(1, ["gh", "release", "create"])
        with patch.object(publish, "find_github_release", return_value=None) as lookup, patch.object(publish, "run", side_effect=error) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                publish.github(self.directory)
        command.assert_called_once()
        lookup.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])

    def test_visible_draft_still_rejects_unexpected_or_differing_assets(self):
        path = sorted(self.directory.iterdir())[0]
        for asset, message in (
            ({"name": "unexpected.zip", "digest": "sha256:wrong"}, "unexpected assets"),
            ({"name": path.name, "digest": "sha256:wrong"}, "asset differs"),
        ):
            release = {**self.release, "assets": [asset]}
            with self.subTest(asset=asset["name"]), patch.object(publish, "find_github_release", side_effect=[None, None, release]), patch.object(publish, "run") as command:
                with self.assertRaisesRegex(ValueError, message):
                    publish.github(self.directory)
                command.assert_called_once()
                self.assertEqual(command.call_args.args[:3], ("gh", "release", "create"))


if __name__ == "__main__":
    unittest.main()
