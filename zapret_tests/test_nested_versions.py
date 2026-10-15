import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from Zapret_backend.version_store import installed_versions, latest_installation


def create_installation(where):
    where.mkdir(parents=True, exist_ok=True)
    (where / 'bin').mkdir(exist_ok=True)
    (where / 'utils').mkdir(exist_ok=True)
    for name in ('service.bat', 'general.bat', 'bin/winws.exe', 'utils/test zapret.ps1'):
        (where / name).touch()
    return where


class NestedVersionTests(unittest.TestCase):
    def test_direct(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            p = create_installation(root / 'zapret-discord-youtube-1.10.3')
            self.assertEqual(latest_installation(root), p)

    def test_nested_wrapper(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            p = create_installation(root / 'downloads' / 'zapret-discord-youtube-1.10.3' / 'extracted' / 'app')
            self.assertEqual(latest_installation(root), p)

    def test_newer_and_incomplete(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            create_installation(root / 'zapret-discord-youtube-1.9.9')
            newer = create_installation(root / 'nested' / 'zapret-discord-youtube-1.10.3')
            (root / 'zapret-discord-youtube-1.12.0').mkdir()
            self.assertEqual(latest_installation(root), newer)
            self.assertEqual(len(installed_versions(root)), 2)

    def test_error_reports_missing_files(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / 'zapret-discord-youtube-1.10.3').mkdir()
            with self.assertRaisesRegex(FileNotFoundError, 'winws.exe'):
                latest_installation(root)


if __name__ == '__main__':
    unittest.main()
