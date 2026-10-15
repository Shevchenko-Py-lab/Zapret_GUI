import tempfile
import unittest
from pathlib import Path
from Zapret_backend.f06_filters import FilterManager, validate_entry, validate_ports
from Zapret_backend.f07_diagnostics import Diagnostics
from Zapret_backend.f08_traffic import TrafficMonitor

class FilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'service.bat').write_text('@echo off\n')
        (self.root/'lists').mkdir()
        self.filters = FilterManager(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_domains(self):
        self.assertTrue(self.filters.add('domains', ' Example.COM '))
        self.assertFalse(self.filters.add('domains', 'example.com'))
        self.assertEqual(self.filters.read_entries('domains'), ['example.com'])
        self.assertTrue(self.filters.remove('domains', 'example.com'))

    def test_bad_data(self):
        for val in ['http://site.com', 'bad space.com', 'bad&command.com']:
            with self.assertRaises(ValueError): validate_entry('domains', val)
        with self.assertRaises(ValueError): validate_entry('ips', 'not-an-ip')
        with self.assertRaises(ValueError): validate_ports('65536')
        with self.assertRaises(ValueError): validate_ports('100-1')

    def test_game_modes(self):
        s = self.filters.save_game_settings('all', '1024-2000,443', '1000-3000')
        self.assertEqual(s, self.filters.read_game_settings())
        self.assertIn('mode=all', (self.root/'utils/game_filter.enabled').read_text())

    def test_ipset_modes(self):
        ipset = self.root/'lists/ipset-all.txt'
        ipset.write_text('1.1.1.0/24\n')
        self.assertEqual(self.filters.ipset_mode(), 'loaded')
        self.filters.set_ipset_mode('none')
        self.assertEqual(self.filters.ipset_mode(), 'none')
        self.assertTrue((self.root/'lists/ipset-all.txt.backup').exists())
        self.filters.set_ipset_mode('any')
        self.assertEqual(self.filters.ipset_mode(), 'any')
        self.filters.set_ipset_mode('loaded')
        self.assertEqual(ipset.read_text(), '1.1.1.0/24\n')

    def test_preserves_comments(self):
        (self.root/'lists/list-general-user.txt').write_text('# hi\nexample.com\n')
        self.filters.add('domains', 'test.org')
        self.assertIn('# hi', (self.root/'lists/list-general-user.txt').read_text())

class DiagnosticTests(unittest.TestCase):
    def test_read_only_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Diagnostics(tmp).run()
            self.assertTrue(any(x.key=='install' for x in r))
            dest=Diagnostics.export_json(r, Path(tmp)/'report.json')
            self.assertTrue(dest.exists())

class TrafficTests(unittest.TestCase):
    def test_sample(self):
        try: monitor=TrafficMonitor()
        except RuntimeError: self.skipTest('psutil unavailable')
        first=monitor.sample(); second=monitor.sample()
        self.assertGreaterEqual(second.download_bps, 0)
        self.assertEqual(first.interface, 'Все интерфейсы')

if __name__ == '__main__': unittest.main()
