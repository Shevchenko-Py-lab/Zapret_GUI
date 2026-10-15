import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from Zapret_backend.version_store import installed_versions, latest_installation


def make_version(root, name, complete=True):
    p = root / name
    p.mkdir()
    if complete:
        (p / 'bin').mkdir()
        (p / 'utils').mkdir()
        for f in ('service.bat','general.bat','bin/winws.exe','utils/test zapret.ps1'):
            (p / f).touch()
    return p

class VersionTests(unittest.TestCase):
    def test_semantic_order_not_lexical(self):
        with TemporaryDirectory() as temp:
            r = Path(temp)
            make_version(r, 'zapret-discord-youtube-1.9.9')
            expected = make_version(r, 'zapret-discord-youtube-1.10.3')
            self.assertEqual(latest_installation(r), expected)
    def test_incomplete_newer_skipped(self):
        with TemporaryDirectory() as temp:
            r = Path(temp)
            expected = make_version(r, 'zapret-discord-youtube-1.10.3')
            make_version(r, 'zapret-discord-youtube-1.11.0', False)
            self.assertEqual(latest_installation(r), expected)
    def test_missing_installation_fails(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                latest_installation(temp)

if __name__ == '__main__':
    unittest.main()
