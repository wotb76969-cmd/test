import base64
import os
import tempfile
import unittest
from unittest.mock import patch
import host_server
from client import validate_host

class TestPrivateNetwork(unittest.TestCase):
    def test_no_public_http(self):
        with self.assertRaises(ValueError): validate_host('http://example.com')
        self.assertEqual(validate_host('http://127.0.0.1:8787'), 'http://127.0.0.1:8787')
    def test_https_url(self):
        self.assertEqual(validate_host('https://example.ts.net/'),'https://example.ts.net')
        with self.assertRaises(ValueError):validate_host('https://user:secret@example.ts.net')
    def test_auth(self):
        with patch.dict('os.environ',{'CHELSY_ALLOW_LEGACY_BASIC':'1'}), patch('host_server.sb_config', return_value=None), patch('host_server.authenticate', return_value=True):
            token=base64.b64encode(b'tester:good-password').decode()
            self.assertEqual(host_server.authorize('Basic '+token),'tester')
        self.assertIsNone(host_server.authorize(None))
        self.assertIsNone(host_server.authorize('Basic invalid'))
    def test_rate_limit(self):
        with host_server.RATE_LOCK:host_server.RATE.clear()
        for i in range(12):self.assertTrue(host_server.permit('test',now=i))
        self.assertFalse(host_server.permit('test',now=12))
        self.assertTrue(host_server.permit('test',now=62))
    def test_loopback_only_source(self):
        import inspect
        self.assertIn("('127.0.0.1', PORT)", inspect.getsource(host_server.serve))

if __name__=='__main__':unittest.main()
