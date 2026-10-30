"""Fail closed when required jobs or CodeQL reports do not pass."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys


def check_jobs(jobs: dict, required: list[str]) -> list[str]:
    if not isinstance(jobs, dict) or not required or set(jobs) != set(required):
        raise ValueError("The quality gate must receive exactly the required jobs")
    failed = []
    for name in required:
        job = jobs.get(name)
        if not isinstance(job, dict) or job.get("result") != "success":
            failed.append(name)
    return failed


def reviewed_locations(path: Path, source_root: Path) -> set[tuple[str, str, int]]:
    """Bind protocol exceptions to an exact rule, function body and source line.

    A source change invalidates its review. Never allow a whole file or query.
    """
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("Invalid CodeQL review list")
    approved = set()
    for entry in entries:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or ":" in str(relative) or "\\" in str(relative):
            raise ValueError("Invalid reviewed source path")
        source = source_root.joinpath(*relative.parts).resolve()
        source.relative_to(source_root.resolve())
        if not entry.get("reason") or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ValueError("Missing review rationale or source digest")
        text = source.read_text(encoding="utf-8-sig")
        functions = [node for node in ast.parse(text).body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == entry["function"]]
        if len(functions) != 1:
            raise ValueError("Reviewed function is missing or ambiguous")
        node = functions[0]
        segment = ast.get_source_segment(text, node)
        if hashlib.sha256(segment.encode("utf-8")).hexdigest() != entry["sha256"]:
            raise ValueError("Reviewed source changed; review is required")
        for line in range(node.lineno, node.end_lineno + 1):
            approved.add((entry["ruleId"], relative.as_posix(), line))
    return approved


def result_location(result: dict) -> tuple[str, int]:
    locations = result.get("locations") or []
    if not locations:
        return "", 0
    physical = locations[0].get("physicalLocation") or {}
    return ((physical.get("artifactLocation") or {}).get("uri", ""),
            (physical.get("region") or {}).get("startLine", 0))


def check_sarif(directory: Path, threshold: float = 7.0, *,
                approved: set[tuple[str, str, int]] | None = None,
                findings: list[dict] | None = None) -> tuple[int, int]:
    if not math.isfinite(threshold) or not 0 <= threshold <= 10:
        raise ValueError("Invalid security severity threshold")
    reports = sorted(directory.rglob("*.sarif"))
    if not reports:
        raise ValueError("CodeQL produced no SARIF reports")
    total = blocked = 0
    for path in reports:
        data = json.loads(path.read_text(encoding="utf-8"))
        runs = data.get("runs")
        if data.get("version") != "2.1.0" or not isinstance(runs, list) or not runs:
            raise ValueError("Invalid SARIF document")
        for run in runs:
            for invocation in run.get("invocations", []):
                if invocation.get("executionSuccessful") is False:
                    raise ValueError("CodeQL execution failed")
                if any(n.get("level") == "error" for n in invocation.get("toolExecutionNotifications", [])):
                    raise ValueError("CodeQL reported an execution error")
            tool = run.get("tool") or {}
            driver = tool.get("driver") or {}
            rules = driver.get("rules", [])
            rule_map = {r["id"]: r for r in rules if isinstance(r, dict) and "id" in r}
            for extension in tool.get("extensions", []):
                if isinstance(extension, dict):
                    for rule in extension.get("rules", []):
                        if isinstance(rule, dict) and "id" in rule:
                            rule_map.setdefault(rule["id"], rule)
            results = run.get("results")
            if not isinstance(results, list):
                raise ValueError("SARIF results are missing")
            for result in results:
                if not isinstance(result, dict):
                    raise ValueError("Invalid SARIF result")
                if "ruleId" in result:
                    rule = rule_map.get(result["ruleId"]) or {}
                else:
                    index = result.get("ruleIndex")
                    if type(index) is not int or not 0 <= index < len(rules):
                        raise ValueError("Invalid SARIF rule index")
                    rule = rules[index]
                props = rule.get("properties") if isinstance(rule, dict) else None
                score = (props or {}).get("security-severity")
                if score is not None:
                    score = float(score)
                    if not math.isfinite(score) or not 0 <= score <= 10:
                        raise ValueError("Invalid SARIF severity")
                    blocking = score >= threshold
                else:
                    default_cfg = (rule.get("defaultConfiguration") or {}) if isinstance(rule, dict) else {}
                    level = result.get("level") or default_cfg.get("level")
                    blocking = level == "error"
                rule_id = result.get("ruleId") or rule.get("id", "unknown")
                path, line = result_location(result)
                reviewed = bool(approved and (rule_id, path, line) in approved
                                and len(result.get("locations", [])) == 1)
                if findings is not None and blocking:
                    findings.append({"rule": rule_id, "path": path, "line": line,
                                     "reviewed": reviewed})
                blocking = blocking and not reviewed
                total += 1
                blocked += bool(blocking)
    return total, blocked


def write_summary(text: str) -> None:
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(text + "\n")


def safe_label(value: object) -> str:
    # Reports are untrusted. Never turn their text into Actions commands/HTML.
    return re.sub(r"[^a-zA-Z0-9_./:@+\-]", "_", str(value))[:250]


def summarize_audit(path: Path, kind: str) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    if kind == "pip-audit":
        dependencies = data.get("dependencies")
        if not isinstance(dependencies, list) or any(d.get("skip_reason") for d in dependencies):
            raise ValueError("Incomplete dependency audit")
        for dependency in dependencies:
            for vulnerability in dependency["vulns"]:
                lines.append("| {} | {} | {} | {} |".format(
                    safe_label(dependency["name"]), safe_label(dependency["version"]),
                    safe_label(vulnerability["id"]),
                    ", ".join(safe_label(v) for v in vulnerability["fix_versions"]) or "No published fix",
                ))
        header = "| Package | Version | Advisory | Fixed versions |\n| --- | --- | --- | --- |"
    else:
        if not isinstance(data, list):
            raise ValueError("Invalid secret scan")
        for finding in data:
            # Never include Match, Secret, source lines or commit messages.
            lines.append("| {} | {} | {} |".format(
                safe_label(finding["RuleID"]), safe_label(finding["File"]),
                safe_label(finding["Fingerprint"]),
            ))
        header = "| Rule | File | Fingerprint requiring review/rotation |\n| --- | --- | --- |"
    summary = f"## {kind}\n\n" + (header + "\n" + "\n".join(lines) if lines else "No findings.")
    write_summary(summary)
    print(summary)
    return int(bool(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    jobs = sub.add_parser("jobs")
    jobs.add_argument("required", nargs="+")
    sarif = sub.add_parser("sarif")
    sarif.add_argument("directory", type=Path)
    sarif.add_argument("--reviews", type=Path)
    for kind in ("pip-audit", "secrets"):
        audit = sub.add_parser(kind)
        audit.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command in {"pip-audit", "secrets"}:
            return summarize_audit(args.report, args.command)
        if args.command == "jobs":
            jobs = json.loads(os.environ["JOB_RESULTS"])
            failed = check_jobs(jobs, args.required)
            summary = "| Check | Result |\n| --- | --- |\n" + "".join(
                f"| {name} | {jobs[name]['result']} |\n" for name in args.required
            )
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                    stream.write("## Quality gate\n\n" + summary)
            print("Blocked checks: " + ", ".join(failed) if failed else "All required checks passed")
            return int(bool(failed))
        approved = reviewed_locations(args.reviews, Path.cwd()) if args.reviews else None
        findings = []
        total, blocked = check_sarif(args.directory, approved=approved, findings=findings)
        rows = ["| Rule | Location | Decision |", "| --- | --- | --- |"]
        for finding in findings:
            rule, path = safe_label(finding["rule"]), safe_label(finding["path"])
            line = finding["line"] if type(finding["line"]) is int else 0
            decision = "Reviewed protocol requirement" if finding["reviewed"] else "Blocked"
            rows.append(f"| {rule} | {path}:{line} | {decision} |")
            print(f"CodeQL {decision}: {rule} at {path}:{line}")
        write_summary("## CodeQL gate\n\n" + "\n".join(rows))
        print(f"CodeQL: {total} findings, {blocked} blocking findings (severity >= 7 or unscored error)")
        return int(bool(blocked))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError):
        # Reports can contain source text. Never echo their bodies on failure.
        print("Quality gate received invalid or incomplete evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
