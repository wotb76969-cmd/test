"""Sanity checks for desktop navigation and UI construction."""
import unittest
import app

class TestDesktopUI(unittest.TestCase):
    def test_navigation_shows_only_target(self):
        values = app.navigate('💾 Память')
        self.assertEqual([v['visible'] for v in values], [False, False, True, False, False])

    def test_ui_builds_without_runtime_server(self):
        ui = app.build()
        self.assertGreater(len(ui.blocks), 30)

if __name__ == '__main__':
    unittest.main()
