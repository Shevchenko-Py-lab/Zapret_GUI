import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from Zapret_backend.f02_strategy import strategies_in
from Zapret_backend.f03_auto_strategy import make_unattended
from Zapret_backend.f01_power import PowerController

class BaselineTests(unittest.TestCase):
    def test_only_general_bat(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('general.bat', 'general (ALT).bat', 'service.bat', 'other.exe'):
                (root / name).touch()
            self.assertEqual(len(strategies_in(root)), 2)
    def test_missing_start_file(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                PowerController(Path(temp) / 'missing.bat')
    def test_ui_available(self):
        self.assertTrue((Path(__file__).resolve().parent.parent / 'zapret_frontend' / 'main_window.ui').exists())

if __name__ == '__main__':
    unittest.main()
