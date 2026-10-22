"""A release is internally consistent even if source files change while read."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from build_release import build, release_files


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='release consistency ')
        self.root=Path(self.temp.name)/'source';self.root.mkdir()
        (self.root/'SKILL.md').write_text('original instructions',encoding='utf-8')
        (self.root/'VERSION').write_text('0.2.0',encoding='utf-8')
        (self.root/'release-files.json').write_text(json.dumps(['SKILL.md','VERSION','release-files.json']),encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def test_archive_payload_matches_manifest_if_source_changes_between_reads(self):
        original_read=Path.read_bytes
        changed=False
        source=self.root/'SKILL.md'
        def read(path):
            nonlocal changed
            data=original_read(path)
            if path==source and not changed:
                changed=True
                source.write_text('changed while packaging',encoding='utf-8')
            return data
        output=Path(self.temp.name)/'release.zip'
        with patch.object(Path,'read_bytes',read):build(output,self.root)
        with zipfile.ZipFile(output) as archive:
            manifest=json.loads(archive.read('livestream-editor/PACKAGE-MANIFEST.json'))
            for name,expected in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read('livestream-editor/'+name)).hexdigest(),expected)
            self.assertEqual(archive.read('livestream-editor/SKILL.md'),b'original instructions')
    def test_generated_manifest_cannot_be_in_source_allowlist(self):
        (self.root/'PACKAGE-MANIFEST.json').write_text('{}',encoding='utf-8')
        (self.root/'release-files.json').write_text(json.dumps(['SKILL.md','VERSION','PACKAGE-MANIFEST.json']),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'自动生成'):
            release_files(self.root)


if __name__=='__main__':unittest.main()
