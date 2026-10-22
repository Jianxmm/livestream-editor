"""User-visible install/update guarantees; all installations are disposable."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install import install
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='安装升级 测试 ')
        self.root = Path(self.temp.name)
        self.source = self.root/'download'; self.source.mkdir()
        (self.source/'SKILL.md').write_text('new skill', encoding='utf-8')
        (self.source/'VERSION').write_text('0.1.1', encoding='utf-8')
        self.names = ['SKILL.md','VERSION','release-files.json']
        (self.source/'release-files.json').write_text(json.dumps(self.names), encoding='utf-8')
        self.target = self.root/'skills/livestream-editor'
        self.backups = self.root/'backups'
    def tearDown(self):self.temp.cleanup()
    def run_install(self, **kwargs):
        return install(self.target, source=self.source, backup_dir=self.backups, **kwargs)
    def test_install_and_identical_reinstall(self):
        self.assertTrue(self.run_install()['changed'])
        self.assertFalse(self.run_install()['changed'])
    def test_different_version_requires_explicit_upgrade(self):
        self.run_install(); (self.target/'SKILL.md').write_text('human edit')
        with self.assertRaises(FileExistsError):self.run_install()
        self.assertEqual((self.target/'SKILL.md').read_text(),'human edit')
    def test_upgrade_preserves_entire_old_installation(self):
        self.run_install(); (self.target/'SKILL.md').write_text('human edit')
        (self.target/'extra.md').write_text('keep this user file')
        result=self.run_install(upgrade=True);backup=Path(result['backup'])
        self.assertEqual((backup/'SKILL.md').read_text(),'human edit')
        self.assertEqual((backup/'extra.md').read_text(),'keep this user file')
        self.assertEqual((self.target/'SKILL.md').read_text(),'new skill')
        self.assertFalse((self.target/'extra.md').exists())
        self.assertFalse(backup.is_relative_to(self.target.parent))
    def test_staging_failure_leaves_previous_install_untouched(self):
        self.run_install(); (self.target/'SKILL.md').write_text('human edit')
        with patch('install.shutil.copyfile', side_effect=OSError('test disk failure')):
            with self.assertRaises(OSError):self.run_install(upgrade=True)
        self.assertEqual((self.target/'SKILL.md').read_text(),'human edit')
    def test_activation_failure_restores_previous_install(self):
        self.run_install(); (self.target/'SKILL.md').write_text('human edit')
        original=Path.rename
        def rename(path,target):
            if path.name=='livestream-editor' and path.parent.name.startswith('.install-'):
                raise OSError('test activation failure')
            return original(path,target)
        with patch.object(Path,'rename',rename):
            with self.assertRaises(OSError):self.run_install(upgrade=True)
        self.assertEqual((self.target/'SKILL.md').read_text(),'human edit')
    def test_manifest_hash_failure_creates_no_target(self):
        manifest={n:hashlib.sha256((self.source/n).read_bytes()).hexdigest() for n in self.names}
        (self.source/'PACKAGE-MANIFEST.json').write_text(json.dumps({'files':manifest}))
        (self.source/'SKILL.md').write_text('tampered')
        with self.assertRaises(ValueError):self.run_install()
        self.assertFalse(self.target.exists())
    def test_backup_inside_skill_scan_directory_is_rejected(self):
        self.run_install(); (self.target/'SKILL.md').write_text('human edit')
        with self.assertRaises(ValueError):
            install(self.target,source=self.source,upgrade=True,backup_dir=self.target.parent/'backup')
        self.assertEqual((self.target/'SKILL.md').read_text(),'human edit')

if __name__=='__main__':unittest.main()
