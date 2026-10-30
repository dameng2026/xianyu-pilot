import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.ci.gates import check_jobs, check_sarif, reviewed_locations, summarize_audit
from contextlib import redirect_stdout
import io


class QualityGateTests(unittest.TestCase):
    def test_failure_skip_and_cancellation_all_block_publication(self):
        for status in ("failure", "skipped", "cancelled", "neutral", None):
            with self.subTest(status=status):
                jobs = {"build": {"result": "success"}, "audit": {"result": status}}
                self.assertEqual(check_jobs(jobs, ["build", "audit"]), ["audit"])

    def test_all_success_is_required(self):
        self.assertEqual(check_jobs({"test": {"result": "success"}}, ["test"]), [])

    def test_missing_and_empty_evidence_are_rejected(self):
        for jobs in ({}, {"test": {"result": "success"}, "extra": {"result": "success"}}):
            with self.assertRaises(ValueError):
                check_jobs(jobs, ["test"])
        with self.assertRaises(ValueError):
            check_jobs({}, [])

    def test_malformed_job_entries_block(self):
        jobs = {"build": None, "audit": {"result": "success"}}
        self.assertEqual(check_jobs(jobs, ["build", "audit"]), ["build"])


class CodeQLGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_report(self, score="7.0", results=None):
        self.report = {
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {"rules": [{"id": "test/rule", "properties": {"security-severity": score}}]}},
                "results": [{"ruleId": "test/rule"}] if results is None else results,
            }],
        }
        self.save()

    def save(self):
        (self.root / "report.sarif").write_text(json.dumps(self.report), encoding="utf-8")

    def test_severity_boundary_blocks_high_and_allows_lower(self):
        for score, blocked in (("6.9", 0), ("7.0", 1), ("9.8", 1)):
            self.write_report(score)
            self.assertEqual(check_sarif(self.root), (1, blocked))

    def test_missing_reports_and_incomplete_results_fail_closed(self):
        with self.assertRaises(ValueError):
            check_sarif(self.root)
        self.write_report()
        del self.report["runs"][0]["results"]
        self.save()
        with self.assertRaises(ValueError):
            check_sarif(self.root)

    def test_empty_clean_report_passes(self):
        self.write_report(results=[])
        self.assertEqual(check_sarif(self.root), (0, 0))

    def test_analysis_errors_block_even_when_no_findings(self):
        self.write_report(results=[])
        self.report["runs"][0]["invocations"] = [{"executionSuccessful": False}]
        self.save()
        with self.assertRaises(ValueError):
            check_sarif(self.root)

    def test_unscored_error_is_blocked(self):
        self.write_report(results=[{"ruleId": "test/rule", "level": "error"}])
        self.report["runs"][0]["tool"]["driver"]["rules"][0]["properties"] = {}
        self.save()
        self.assertEqual(check_sarif(self.root), (1, 1))

    def test_invalid_scores_and_rule_indices_fail(self):
        for score in ("NaN", "Infinity", "11", "-1"):
            self.write_report(score)
            with self.assertRaises(ValueError):
                check_sarif(self.root)
        self.write_report(results=[{"ruleIndex": -1}])
        with self.assertRaises(ValueError):
            check_sarif(self.root)

    def test_rules_in_extensions_are_resolved(self):
        self.write_report()
        run = self.report["runs"][0]
        rule = run["tool"]["driver"]["rules"].pop()
        run["tool"]["extensions"] = [{"rules": [rule]}]
        self.save()
        self.assertEqual(check_sarif(self.root), (1, 1))

    def test_null_properties_and_missing_rule_id_safely_fallback(self):
        # Case 1: properties is None/null
        self.write_report(results=[{"ruleId": "test/rule", "level": "warning"}])
        self.report["runs"][0]["tool"]["driver"]["rules"][0]["properties"] = None
        self.save()
        self.assertEqual(check_sarif(self.root), (1, 0))

        # Case 2: ruleId not in driver/extensions falls back to result level
        self.write_report(results=[{"ruleId": "external/rule", "level": "error"}])
        self.assertEqual(check_sarif(self.root), (1, 1))

    def test_protocol_review_matches_only_exact_rule_function_and_digest(self):
        source = "def protocol_sign(value):\n    return value\n"
        (self.root / "protocol.py").write_text(source, encoding="utf-8")
        entry = {"ruleId": "test/rule", "path": "protocol.py", "function": "protocol_sign",
                 "sha256": hashlib.sha256(source.rstrip().encode()).hexdigest(),
                 "reason": "External protocol contract"}
        reviews = self.root / "reviews.json"
        reviews.write_text(json.dumps([entry]), encoding="utf-8")
        approved = reviewed_locations(reviews, self.root)
        self.write_report(results=[{"ruleId": "test/rule", "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": "protocol.py"}, "region": {"startLine": 2},
        }}]}])
        self.assertEqual(check_sarif(self.root, approved=approved), (1, 0))
        self.report["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] = 3
        self.save()
        self.assertEqual(check_sarif(self.root, approved=approved), (1, 1))
        self.assertEqual(check_sarif(self.root, approved={("different/rule", "protocol.py", 3)}), (1, 1))
        (self.root / "protocol.py").write_text(source.replace("return value", "return str(value)"), encoding="utf-8")
        with self.assertRaises(ValueError):
            reviewed_locations(reviews, self.root)

    def test_review_cannot_escape_source_root(self):
        reviews = self.root / "reviews.json"
        reviews.write_text(json.dumps([{"path": "../outside.py"}]), encoding="utf-8")
        with self.assertRaises(ValueError):
            reviewed_locations(reviews, self.root)

    def test_secret_summary_never_prints_match_or_secret(self):
        path = self.root / "gitleaks.json"
        path.write_text(json.dumps([{"RuleID": "generic-api-key", "File": "config.py",
                                    "Fingerprint": "sha:config.py:generic-api-key:1",
                                    "Secret": "must-not-be-printed", "Match": "must-not-be-printed"}]), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(summarize_audit(path, "secrets"), 1)
        self.assertNotIn("must-not-be-printed", output.getvalue())
        self.assertIn("sha:config.py", output.getvalue())

    def test_dependency_summary_blocks_unfixed_and_skipped_packages(self):
        path = self.root / "pip.json"
        dependency = {"name": "package", "version": "1", "vulns": [{"id": "CVE-test", "fix_versions": []}]}
        path.write_text(json.dumps({"dependencies": [dependency]}), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(summarize_audit(path, "pip-audit"), 1)
        dependency["skip_reason"] = "No metadata"
        path.write_text(json.dumps({"dependencies": [dependency]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            summarize_audit(path, "pip-audit")
