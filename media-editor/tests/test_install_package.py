import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        archive = self.root / 'bundle.zip'
        load('packager', ROOT / 'build_package.py').build(archive)
        with zipfile.ZipFile(archive) as z:
            z.extractall(self.root / 'extracted')
        self.source = self.root / 'extracted/media-editor'
        self.installer = load('installer', self.source / 'install.py')

    def test_independent_install_includes_unchanged_core_and_preserves_existing(self):
        target = self.root / 'skills/media-editor'
        self.installer.install(self.source, target)
        self.assertEqual((target / '.livestream-core/SKILL.md').read_bytes(),
                         (ROOT / '.livestream-core/SKILL.md').read_bytes())
        marker = target / 'my-edits.txt'; marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.installer.install(self.source, target)
        self.assertEqual(marker.read_text(), 'keep')

    def test_damaged_release_is_not_installed(self):
        (self.source / 'SKILL.md').write_text('modified')
        target = self.root / 'skills/media-editor'
        with self.assertRaises(ValueError):
            self.installer.install(self.source, target)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
