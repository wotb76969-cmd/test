import base64
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from host_server import Handler


class HostIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)

    def test_rejects_unauthenticated(self):
        req = Request(self.url + '/api/status')
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(req, timeout=3)
        self.assertEqual(ctx.exception.code, 401)

    def test_status_and_history_with_login(self):
        token = base64.b64encode(b'tester:password').decode('ascii')
        headers = {'Authorization': 'Basic ' + token}
        with patch.dict('os.environ',{'CHELSY_ALLOW_LEGACY_BASIC':'1'}), patch('host_server.sb_config', return_value=None), patch('host_server.authenticate', return_value=True), patch('host_server.stats', return_value=(5, 2)), patch('host_server.ollama_available', return_value=True):
            with self.opener.open(Request(self.url + '/api/status', headers=headers), timeout=3) as response:
                result = json.load(response)
        self.assertEqual(result['user'], 'tester')
        self.assertEqual(result['pages'], 5)
        self.assertTrue(result['ollama'])
        with patch.dict('os.environ',{'CHELSY_ALLOW_LEGACY_BASIC':'1'}), patch('host_server.sb_config', return_value=None), patch('host_server.authenticate', return_value=True), patch('host_server.history', return_value=[{'role':'assistant','content':'OK'}]):
            with self.opener.open(Request(self.url + '/api/history', headers=headers), timeout=3) as response:
                self.assertEqual(json.load(response)['messages'][0]['content'], 'OK')

if __name__ == '__main__':
    unittest.main()
