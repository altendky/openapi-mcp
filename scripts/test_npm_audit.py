#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise the exact accepted audit scope and failure boundaries."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("project_check", Path(__file__).with_name("check.py"))
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

GLOB = "https://github.com/advisories/GHSA-3g3m-2fjr-6pw4"
MINIMATCH = "https://github.com/advisories/GHSA-g3hv-75wg-m249"
NEW = "https://github.com/advisories/GHSA-xxxx-yyyy-zzzz"
LEVELS = ("info", "low", "moderate", "high", "critical")


def advisory(name, url, severity="moderate"):
    return {"source": 123, "name": name, "dependency": name, "url": url,
            "title": "Audit fixture", "severity": severity}


def counts(report):
    findings = report["vulnerabilities"]
    report["metadata"]["vulnerabilities"] = {
        **{level: sum(item["severity"] == level for item in findings.values()) for level in LEVELS},
        "total": len(findings),
    }


def fixture():
    # npm audit v2's actual four records for the two development advisories.
    report = {"auditReportVersion": 2, "vulnerabilities": {},
              "metadata": {"dependencies": {"prod": 2, "dev": 53, "total": 54}}}
    graph = {
        "c8": (["test-exclude"], []),
        "test-exclude": (["glob", "minimatch"], ["c8"]),
        "glob": ([advisory("glob", GLOB), "minimatch"], ["test-exclude"]),
        "minimatch": ([advisory("minimatch", MINIMATCH)], ["glob", "test-exclude"]),
    }
    for name, (via, effects) in graph.items():
        report["vulnerabilities"][name] = {"name": name, "severity": "moderate",
            "via": via, "effects": effects, "nodes": [f"node_modules/{name}"],
            "isDirect": name == "c8", "range": "fixture", "fixAvailable": False}
    lock = {"lockfileVersion": 3, "packages": {
        "": {"devDependencies": {"c8": "^12.0.0"}},
        "node_modules/c8": {"version": "12.0.0", "dev": True, "dependencies": {"test-exclude": "^8.0.0"}},
        "node_modules/test-exclude": {"version": "8.0.0", "dev": True,
                                      "dependencies": {"glob": "^13.0.6", "minimatch": "^10.2.4"}},
        "node_modules/glob": {"version": "13.0.6", "dev": True, "dependencies": {"minimatch": "^10.0.3"}},
        "node_modules/minimatch": {"version": "10.2.4", "dev": True},
    }}
    counts(report)
    return report, lock


class AuditPolicyTests(unittest.TestCase):
    def invoke(self, report, lock, returncode=0, stdout=None):
        output = io.StringIO()
        result = subprocess.CompletedProcess([], returncode,
            json.dumps(report) if stdout is None else stdout, "")
        with (patch.object(check.shutil, "which", return_value="npm"),
              patch.object(check.subprocess, "run", return_value=result) as run,
              patch.object(Path, "read_text", return_value=json.dumps(lock)),
              redirect_stdout(output), redirect_stderr(io.StringIO())):
            check.npm_audit()
        self.assertIn("--audit-level=high", run.call_args.args[0])
        self.assertNotIn("--omit=dev", run.call_args.args[0])
        return output.getvalue()

    def test_current_four_records_only_are_accepted_without_mutating_input(self):
        report, lock = fixture()
        original = copy.deepcopy(report)
        filtered, accepted = check.filter_npm_audit(report, lock)
        self.assertEqual(accepted, sorted([GLOB, MINIMATCH]))
        self.assertEqual(filtered["vulnerabilities"], {})
        self.assertEqual(filtered["metadata"]["vulnerabilities"]["total"], 0)
        self.assertEqual(filtered["metadata"]["dependencies"], original["metadata"]["dependencies"])
        self.assertEqual(report, original)
        self.assertIn("issues/169", self.invoke(report, lock))

    def test_mixed_new_high_and_critical_in_same_chain_remain_and_fail(self):
        for level in ("high", "critical"):
            with self.subTest(level=level):
                report, lock = fixture()
                report["vulnerabilities"]["glob"]["via"].append(advisory("glob", NEW, level))
                for name in ("glob", "test-exclude", "c8"):
                    report["vulnerabilities"][name]["severity"] = level
                counts(report)
                filtered, accepted = check.filter_npm_audit(report, lock)
                self.assertEqual(accepted, [])
                self.assertEqual(filtered, report)
                with self.assertRaises(subprocess.CalledProcessError):
                    self.invoke(report, lock)

    def test_new_independent_findings_of_every_severity_are_reported(self):
        for level in LEVELS:
            with self.subTest(level=level):
                report, lock = fixture()
                item = {"name": "other", "severity": level, "via": [advisory("other", NEW, level)],
                        "nodes": ["node_modules/other"], "effects": []}
                report["vulnerabilities"]["other"] = item
                counts(report)
                filtered, accepted = check.filter_npm_audit(report, lock)
                self.assertEqual(set(filtered["vulnerabilities"]), {"other"})
                self.assertEqual(filtered["vulnerabilities"]["other"], item)
                self.assertEqual(len(accepted), 2)
                if level in ("high", "critical"):
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.invoke(report, lock)
                else:
                    self.assertIn(NEW, self.invoke(report, lock))

    def test_production_missing_dev_and_unexpected_consumers_are_not_accepted(self):
        for mode in ("production", "missing-dev", "consumer", "root-production", "effect"):
            with self.subTest(mode=mode):
                report, lock = fixture()
                if mode == "production":
                    lock["packages"]["node_modules/glob"]["dev"] = False
                elif mode == "missing-dev":
                    del lock["packages"]["node_modules/minimatch"]["dev"]
                elif mode == "consumer":
                    lock["packages"]["node_modules/other"] = {"dev": True, "dependencies": {"glob": "*"}}
                elif mode == "root-production":
                    lock["packages"][""]["dependencies"] = {"c8": "^12.0.0"}
                else:
                    report["vulnerabilities"]["other"] = {"name": "other", "severity": "low",
                        "via": [advisory("other", NEW, "low")], "nodes": ["node_modules/other"], "effects": []}
                    report["vulnerabilities"]["glob"]["effects"].append("other")
                    counts(report)
                self.assertEqual(check.filter_npm_audit(report, lock), (report, []))

    def test_changed_versions_node_paths_ids_or_package_identity_are_not_accepted(self):
        for mode in ("glob-version", "minimatch-version", "c8-version", "test-exclude-version", "path", "id", "package"):
            with self.subTest(mode=mode):
                report, lock = fixture()
                if mode.endswith("version"):
                    name = mode.removesuffix("-version")
                    lock["packages"][f"node_modules/{name}"]["version"] = "99.0.0"
                elif mode == "path":
                    report["vulnerabilities"]["glob"]["nodes"].append("node_modules/other/node_modules/glob")
                else:
                    report["vulnerabilities"]["glob"]["via"][0]["url" if mode == "id" else "dependency"] = NEW
                self.assertEqual(check.filter_npm_audit(report, lock), (report, []))

    def test_package_or_direct_advisory_severity_escalation_is_not_accepted(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                report, lock = fixture()
                report["vulnerabilities"]["glob"]["severity"] = "high"
                if direct:
                    report["vulnerabilities"]["glob"]["via"][0]["severity"] = "high"
                counts(report)
                self.assertEqual(check.filter_npm_audit(report, lock), (report, []))
                with self.assertRaises(subprocess.CalledProcessError):
                    self.invoke(report, lock)

    def test_cycle_missing_reference_and_unknown_schema_fail_closed(self):
        for mode in ("cycle", "missing", "version", "severity", "via", "counts", "truncated", "lock"):
            with self.subTest(mode=mode):
                report, lock = fixture()
                if mode in ("cycle", "missing"):
                    report["vulnerabilities"]["c8"]["via"].append("c8" if mode == "cycle" else "absent")
                elif mode == "version": report["auditReportVersion"] = 3
                elif mode == "severity": report["vulnerabilities"]["glob"]["severity"] = "unknown"
                elif mode == "via": report["vulnerabilities"]["glob"]["via"] = [None]
                elif mode == "counts": report["metadata"]["vulnerabilities"]["high"] = 1
                elif mode == "truncated": report["vulnerabilities"] = {}
                else: lock["lockfileVersion"] = 99
                with self.assertRaises(ValueError): check.filter_npm_audit(report, lock)

    def test_error_key_presence_non_json_and_nonzero_exit_fail_closed(self):
        for error in (None, {}, {"code": "ENETUNREACH"}):
            report, lock = fixture()
            report["error"] = error
            with self.subTest(error=error), self.assertRaises(subprocess.CalledProcessError):
                self.invoke(report, lock)
        report, lock = fixture()
        with self.assertRaises(subprocess.CalledProcessError): self.invoke(report, lock, stdout="not JSON")
        for code in (1, 2):
            with self.subTest(code=code), self.assertRaises(subprocess.CalledProcessError):
                self.invoke(report, lock, returncode=code)
        with (patch.object(check.shutil, "which", return_value="npm"),
              patch.object(check.subprocess, "run", side_effect=OSError("network unavailable")),
              redirect_stdout(io.StringIO()), self.assertRaises(OSError)):
            check.npm_audit()

    def test_clean_report_stays_clean(self):
        report, lock = fixture()
        report["vulnerabilities"] = {}
        counts(report)
        self.assertEqual(check.filter_npm_audit(report, lock), (report, []))
        self.invoke(report, lock)

    def test_external_effects_into_coverage_keep_chain_visible(self):
        report, lock = fixture()
        report["vulnerabilities"]["other"] = {
            "name": "other", "severity": "low", "via": [advisory("other", NEW, "low")],
            "nodes": ["node_modules/other"], "effects": ["glob"],
        }
        counts(report)
        self.assertEqual(check.filter_npm_audit(report, lock), (report, []))

    def test_direct_high_or_critical_fails_even_with_lower_package_severity(self):
        for level in ("high", "critical"):
            with self.subTest(level=level):
                report, lock = fixture()
                report["vulnerabilities"]["glob"]["via"][0]["severity"] = level
                self.assertEqual(check.filter_npm_audit(report, lock), (report, []))
                with self.assertRaises(subprocess.CalledProcessError):
                    self.invoke(report, lock)

    def test_existing_security_task_runs_regressions_and_explicit_audit(self):
        calls = []
        with (patch.object(check, "cargo", side_effect=lambda *args: calls.append(("cargo", args))),
              patch.object(check, "run", side_effect=lambda *args: calls.append(("tests", args))),
              patch.object(check, "npm", side_effect=lambda *args: calls.append(("npm", args))),
              patch.object(check, "npm_audit", side_effect=lambda: calls.append(("audit", ())))):
            check.check("security")
        self.assertEqual(calls[0], ("cargo", ("deny", "--locked", "--all-features", "check")))
        self.assertEqual(calls[1][1][-1], "test_npm_audit.py")
        self.assertEqual(calls[2:], [("npm", ("ci", "--no-audit")), ("audit", ())])


if __name__ == "__main__":
    unittest.main()
