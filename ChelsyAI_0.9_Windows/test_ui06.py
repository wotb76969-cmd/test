"""Focused regression checks for Chelsy 0.6 UI."""
import unittest
from unittest.mock import patch
import app


class TestUnified06(unittest.TestCase):
    def setUp(self):
        # Legacy fixture users are valid only in local-auth mode.
        # Do not read the developer's actual Supabase settings in UI tests.
        # This patch changes tests only; production authorization is unchanged.
        self._local_auth = patch('desktop_auth.config', return_value=None)
        self._local_auth.start()
        self.addCleanup(self._local_auth.stop)

    def test_interface_has_new_components(self):
        ui = app.build()
        self.assertGreaterEqual(len(ui.blocks), 65)

    def test_memory_form_saves_only_current_user(self):
        class Request:
            username = 'owner'
        with patch.object(app, 'store_personal_fact') as save, patch.object(app, 'memory_list', return_value='test'):
            result = app.save_memory_form(' Project ', ' Chelsy ', Request())
        save.assert_called_once_with('owner', 'project', 'Chelsy')
        self.assertEqual(result[1:3], ('', ''))

    def test_memory_form_rejects_invalid(self):
        class Request:
            username = 'tester'
        with patch.object(app, 'store_personal_fact') as save, patch.object(app, 'memory_list', return_value='test'):
            result = app.save_memory_form('x', '', Request())
        save.assert_not_called()
        self.assertIn('Укажи', result[0])

if __name__ == '__main__':
    unittest.main()
