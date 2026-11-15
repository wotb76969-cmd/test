"""Windows Client GUI smoke test. Runs where Tk is available (e.g. the Windows CI runner);
skipped on headless Linux without tkinter. No network: the host client is a stub."""
import os
import tempfile
import unittest

try:
    import tkinter
    tkinter.Tk().destroy()
    TK = True
except Exception:          # no tkinter module or no display
    TK = False


@unittest.skipUnless(TK, 'Tk is not available in this environment')
class ClientGuiSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = self.old
        self.tmp.cleanup()

    def test_build_login_and_main_views(self):
        import client_gui
        app = client_gui.ChelsyClientApp()
        try:
            self.assertEqual(app.root.title(), 'Chelsy AI · Client 0.9.3')
            app.session.email = 'tester@example.org'
            app.login_frame.destroy()
            app.main_frame = app._main_view()
            app.messages = [{'role': 'user', 'content': 'Почему небо голубое?'},
                            {'role': 'assistant', 'content': '# Ответ\nДело в **рассеянии**.\n```python\nprint(1)\n```\n[Источник](https://example.org)'}]
            app.render()
            text = app.chat.get('1.0', 'end')
            self.assertIn('рассеянии', text)
            self.assertIn('print(1)', text)
            self.assertNotIn('**', text)
            app._show_conversations([{'id': 'a', 'title': 'Чат', 'favorite': True, 'updated': '2'},
                                     {'id': 'b', 'title': 'Другой', 'favorite': False, 'updated': '1'}])
            self.assertTrue(app.conv_list.get(0).startswith('★'))
            for state in ('generating', 'searching', 'syncing', 'offline', 'ready'):
                app.pulse.set(state)
            app.root.update()
        finally:
            app.root.destroy()


if __name__ == '__main__':
    unittest.main()
