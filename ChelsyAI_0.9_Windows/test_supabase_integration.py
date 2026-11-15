import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import supabase_auth as sa
import desktop_auth
import host_server

class SupabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = self.tmp.name
        os.environ.pop('CHELSY_SUPABASE_URL',None)
        os.environ.pop('CHELSY_SUPABASE_PUBLISHABLE_KEY',None)

    def tearDown(self):
        if self.old is None: os.environ.pop('LOCALAPPDATA',None)
        else:os.environ['LOCALAPPDATA']=self.old
        self.tmp.cleanup()

    def setup_conf(self):
        folder=Path(self.tmp.name)/'ChelsyAI'
        folder.mkdir()
        (folder/'supabase.json').write_text('{"url":"https://demo.supabase.co","publishable_key":"sb_publishable_fake"}',encoding='utf-8')

    def test_no_conf_legacy(self):
        self.assertIsNone(sa.config())

    def test_conf_from_appdata(self):
        self.setup_conf()
        self.assertEqual(sa.config()[0],'https://demo.supabase.co')

    def test_secret_rejected(self):
        self.setup_conf()
        os.environ['CHELSY_SUPABASE_PUBLISHABLE_KEY']='sb_secret_not_allowed'
        with self.assertRaises(sa.AuthError):sa.config()
        os.environ.pop('CHELSY_SUPABASE_PUBLISHABLE_KEY',None)

    def test_desktop_auth_uid(self):
        self.setup_conf()
        user='123e4567-e89b-12d3-a456-426614174000'
        with patch('desktop_auth.login',return_value={'user':{'id':user}}):
            self.assertTrue(desktop_auth.authenticate('USER@EXAMPLE.COM','fake_password'))
        self.assertEqual(desktop_auth.account_name('user@example.com'),'sb:'+user)

    def test_host_bearer(self):
        self.setup_conf()
        user='123e4567-e89b-12d3-a456-426614174000'
        with patch('host_server.verify',return_value=user):
            self.assertEqual(host_server.authorize('Bearer testtoken123'), 'sb:'+user)
        self.assertIsNone(host_server.authorize('Basic eDp5'))

    def test_invalid_uid(self):
        with self.assertRaises(ValueError):sa.storage_key('not-a-uuid')

if __name__=='__main__':unittest.main()
