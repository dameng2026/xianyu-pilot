import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.ci import windows_portable as package


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'XianyuPilot'
        for name in ['XianyuPilot.exe','_internal/web/desktop-safety.html','_internal/web/assets/safety.js',
                     '_internal/app/desktop_migrations/005_delivery_safety.sql','_internal/python311.dll',
                     '_internal/PySide6/QtWebEngineProcess.exe','_internal/PySide6/Qt6WebEngineCore.dll',
                     '_internal/PySide6/resources/qtwebengine_resources.pak','_internal/PySide6/resources/icudtl.dat',
                     '_internal/PySide6/translations/qtwebengine_locales/en-US.pak','_internal/vcruntime140.dll','_internal/msvcp140.dll']:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'test runtime')
        package.record(self.root,'a'*40)

    def test_package_checks_each_runtime_file_and_preserves_pending_acceptance(self):
        data=package.verify(self.root)
        self.assertFalse(data['realDeliveryEnabledByDefault'])
        self.assertIn('clean-vm',data['manualAcceptancePending'])
        output=package.package(self.root,Path(self.temp.name)/'release')
        with zipfile.ZipFile(output) as archive:
            self.assertIn('XianyuPilot/XianyuPilot.exe',archive.namelist())
            self.assertFalse(any('/data/' in name for name in archive.namelist()))
        self.assertIn(output.name,(output.parent/'SHA256SUMS.txt').read_text())

    def test_modified_or_missing_embedded_browser_cannot_pass(self):
        dll=self.root/'_internal/PySide6/Qt6WebEngineCore.dll'
        dll.write_bytes(b'changed')
        with self.assertRaises(ValueError):package.verify(self.root)
        dll.unlink()
        with self.assertRaises(ValueError):package.record(self.root,'a'*40)

    def test_runtime_data_and_private_settings_cannot_enter_distribution(self):
        for name in ['data/app.sqlite3','.env','.env.production','secrets/token']:
            path=self.root/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'private')
            with self.assertRaises(ValueError):package.record(self.root,'a'*40)
            path.unlink()

    def test_invalid_revision_and_changed_manifest_are_rejected(self):
        with self.assertRaises(ValueError):package.record(self.root,'main')
        path=self.root/package.MANIFEST
        data=json.loads(path.read_text());data['distribution']='onefile';path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):package.verify(self.root)

    def test_build_is_onedir_and_ci_downloads_tools_and_checks_frozen_outputs(self):
        root=Path(__file__).resolve().parents[2]
        spec=(root/'packaging/windows/xianyu_portable.spec').read_text()
        workflow=(root/'.github/workflows/ci-checks.yml').read_text()
        self.assertIn('exclude_binaries=True',spec)
        self.assertIn('COLLECT(',spec)
        self.assertIn('windows-portable:',workflow)
        self.assertIn('packaging/windows/requirements-build.txt',workflow)
        self.assertIn('windows_portable_smoke.py',workflow)
        self.assertNotIn('--onefile',spec)
