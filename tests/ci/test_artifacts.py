import json
from pathlib import Path
import tempfile
import unittest

from scripts.ci import artifacts

ENV = {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123"}


class CompiledArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "index.html").write_text('<script src="assets/app.js"></script>', encoding="utf-8")
        (self.root / "assets").mkdir()
        (self.root / "assets/app.js").write_text("console.log('tested build')", encoding="utf-8")
        artifacts.record(self.root, "web", ENV)

    def mutate(self, **values):
        path = self.root / artifacts.MANIFEST
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(values)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_round_trip_and_rerun_accept_the_exact_tested_files(self):
        result = artifacts.verify(self.root, "web", ENV | {"GITHUB_RUN_ATTEMPT": "2"})
        self.assertEqual(len(result["files"]), 2)

    def test_changed_missing_and_extra_files_are_rejected(self):
        target = self.root / "assets/app.js"
        original = target.read_bytes()
        target.write_bytes(b"different build")
        with self.assertRaises(ValueError):
            artifacts.verify(self.root, "web", ENV)
        target.unlink()
        with self.assertRaises(ValueError):
            artifacts.verify(self.root, "web", ENV)
        target.write_bytes(original)
        (self.root / "unexpected.js").write_text("extra", encoding="utf-8")
        with self.assertRaises(ValueError):
            artifacts.verify(self.root, "web", ENV)

    def test_other_revision_run_or_component_is_rejected(self):
        for env, component in [(ENV | {"GITHUB_SHA": "b" * 40}, "web"),
                               (ENV | {"GITHUB_RUN_ID": "124"}, "web"), (ENV, "crawler")]:
            with self.subTest(component=component, env=env), self.assertRaises(ValueError):
                artifacts.verify(self.root, component, env)

    def test_missing_entrypoint_or_manifest_cannot_pass(self):
        (self.root / "index.html").unlink()
        with self.assertRaises(ValueError):
            artifacts.record(self.root, "web", ENV)
        (self.root / artifacts.MANIFEST).unlink()
        with self.assertRaises(FileNotFoundError):
            artifacts.verify(self.root, "web", ENV)

    def test_inventory_paths_and_hashes_are_validated(self):
        for name, digest in [("../outside", "a" * 64), ("/absolute", "a" * 64),
                             ("C:/outside", "a" * 64), ("assets\\app.js", "a" * 64),
                             ("assets/app.js", "not-a-digest")]:
            with self.subTest(name=name):
                self.mutate(files={"index.html": "b" * 64, name: digest})
                with self.assertRaises(ValueError):
                    artifacts.verify(self.root, "web", ENV)

    def test_symbolic_links_cannot_be_recorded_or_verified(self):
        link = self.root / "linked.js"
        try:
            link.symlink_to(self.root / "assets/app.js")
        except OSError:
            self.skipTest("Creating symlinks requires host permission")
        for operation in (artifacts.record, artifacts.verify):
            with self.assertRaises(ValueError):
                operation(self.root, "web", ENV)

    def test_crawler_uses_its_own_entrypoint(self):
        (self.root / "server.js").write_text("// compiled crawler", encoding="utf-8")
        artifacts.record(self.root, "crawler", ENV)
        self.assertEqual(artifacts.verify(self.root, "crawler", ENV)["component"], "crawler")
