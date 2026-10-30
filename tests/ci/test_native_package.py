import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.ci import artifacts, native_package as package

ENV = {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
       "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v1.2.3", "GH_REPO": "owner/project"}


class NativePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "release"
        self.web, self.crawler = self.root / "web", self.root / "crawler"
        for component, directory, entry in [("web", self.web, "index.html"), ("crawler", self.crawler, "server.js")]:
            directory.mkdir()
            (directory / entry).write_text("compiled " + component, encoding="utf-8")
            if component=="web":
                (directory/"desktop-safety.html").write_text("compiled safety",encoding="utf-8")
            artifacts.record(directory, component, ENV)
        self.sources = {name: b"source" for name in package.REQUIRED
                        if package.allowed_source(name)}
        self.sources[".env.development.example"] = b"# public example, not a real credential"
        self.path = package.create(self.output, self.web, self.crawler, ENV, self.sources)

    def rewrite(self, change):
        with zipfile.ZipFile(self.path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        change(files)
        with zipfile.ZipFile(self.path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        (self.output / "SHA256SUMS.txt").write_text(f"{digest}  {self.path.name}\n", encoding="utf-8")

    def test_verified_bundle_contains_original_runtime_and_tested_builds(self):
        path, info = package.verify(self.output, ENV)
        self.assertEqual(path, self.path)
        self.assertTrue(package.REQUIRED <= info["files"].keys())
        self.assertEqual(info["component"], "native-source")
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.read("apps/web/dist/index.html"), b"compiled web")
            self.assertEqual(archive.read("apps/crawler/dist/server.js"), b"compiled crawler")

    def test_worktree_data_desktop_prototype_and_containers_are_not_sources(self):
        for name in [".env", "secrets/token", "data/app.sqlite3", "data/uploads/customer.txt", "apps/api/.env", "apps/api/uploads/customer.txt",
                     "apps/api/secrets/key", "apps/web/node_modules/dependency/index.js",
                     "apps/desktop/host.py", "apps/web/Dockerfile", "docker-compose.yml", "../escape"]:
            with self.subTest(name=name):
                self.assertFalse(package.allowed_source(name))
        self.assertTrue(package.allowed_source("apps/api/app/core/secret_store.py"))
        with self.assertRaises(ValueError):
            package.create(self.output, self.web, self.crawler, ENV, self.sources | {".env": b"private"})

    def test_changed_compiled_source_and_incomplete_runtime_block_packaging(self):
        (self.web / "index.html").write_text("untested build", encoding="utf-8")
        with self.assertRaises(ValueError):
            package.create(self.output, self.web, self.crawler, ENV, self.sources)
        artifacts.record(self.web, "web", ENV)
        incomplete = dict(self.sources)
        del incomplete["apps/api/requirements.txt"]
        with self.assertRaises(ValueError):
            package.create(self.output, self.web, self.crawler, ENV, incomplete)

    def test_changed_archive_and_inner_file_hashes_block_release(self):
        original = self.path.read_bytes()
        self.path.write_bytes(original + b"unexpected bytes")
        with self.assertRaises(ValueError):
            package.verify(self.output, ENV)
        self.path.write_bytes(original)
        self.rewrite(lambda files: files.update({"apps/api/app/main.py": b"different source"}))
        with self.assertRaises(ValueError):
            package.verify(self.output, ENV)

    def test_other_run_and_unexpected_zip_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            package.verify(self.output, ENV | {"GITHUB_RUN_ID": "124"})
        self.rewrite(lambda files: files.update({"../escape.txt": b"not extracted"}))
        with self.assertRaises(ValueError):
            package.verify(self.output, ENV)

    def test_development_pr_and_main_pushes_cannot_publish(self):
        for changes in [{"GITHUB_REF": "refs/heads/codex/test"},
                        {"GITHUB_EVENT_NAME": "pull_request"},
                        {"GITHUB_REF": "refs/heads/main"},
                        {"GITHUB_REF": "refs/tags/v1;unsafe"},
                        {"GITHUB_REF": "refs/tags/vinvalid"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                package.release_target(ENV | changes)
        self.assertEqual(package.release_target(ENV), ("v1.2.3", False))
        self.assertEqual(package.release_target(ENV | {"GITHUB_REF": "refs/tags/v1.2.3-rc.1"}), ("v1.2.3-rc.1", True))
        self.assertEqual(package.release_target(ENV | {"GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch"}), ("native-123-1", True))

    def test_moved_tag_and_failed_checksums_never_create_a_release(self):
        with patch.object(package.subprocess, "run") as run:
            run.return_value.stdout = "b" * 40
            with self.assertRaises(ValueError):
                package.publish(self.output, ENV)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][1], "api")
        self.path.write_bytes(self.path.read_bytes() + b"tampered")
        with patch.object(package.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                package.publish(self.output, ENV)
            run.assert_not_called()

    def test_release_uses_verified_source_and_never_overwrites_existing_assets(self):
        with patch.object(package.subprocess, "run") as run:
            run.return_value.stdout = ENV["GITHUB_SHA"]
            package.publish(self.output, ENV)
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ["gh", "release", "create", "v1.2.3"])
            self.assertIn(str(self.path), command)
            self.assertIn("--verify-tag", command)
            self.assertNotIn("--clobber", command)
            self.assertIn(ENV["GITHUB_SHA"], command)


class ContainerFreeWorkflowTests(unittest.TestCase):
    def test_all_active_workflows_use_native_runners_without_container_jobs(self):
        root = Path(__file__).resolve().parents[2]
        for path in (root / ".github/workflows").glob("*.yml"):
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"^\s*(?:container|services):", text, re.M))
                self.assertIsNone(re.search(r"uses:\s*docker/|\b(?:sudo\s+)?docker\s+(?:build|run|compose|push|login)\b", text))
