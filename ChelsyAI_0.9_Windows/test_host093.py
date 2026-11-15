"""Integration tests: real Chelsy Host HTTP server + real client_api.HostClient.

Mocked: Supabase token verification (fixed token -> uuid map) and the Ollama
model (scripted streaming). Everything else (HTTP, routing, SQLite, engine,
knowledge DB, isolation) runs for real.
"""
import json
import tempfile
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import core
import host_server
import intelligence
from client_api import HostClient, HostError
from test_engine093 import FakeLLM, FakeSearch

ALICE, BOB, OWNER = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
TOKENS = {'token-alice-0123456789': ALICE, 'token-bob-0123456789xx': BOB, 'token-owner-0123456789': OWNER}


def fake_verify(token):
    if token not in TOKENS:
        raise host_server.AuthError('bad token')
    return TOKENS[token]


class SlowLLM(FakeLLM):
    def stream_chat(self, model, messages, options=None, cancel=None):
        for i in range(200):
            if cancel is not None and cancel.is_set():
                import ollama_client
                raise ollama_client.Cancelled()
            time.sleep(0.02)
            yield f'слово{i} '


class HostV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = core.DATA, core.DB_PATH
        core.DATA = Path(cls.tmp.name)
        core.DB_PATH = core.DATA / 'chelsy.db'
        cls.patches = [patch('host_server.verify', side_effect=fake_verify),
                       patch('host_server.admin_user_id', return_value=OWNER),
                       patch('host_server.allowed_models', return_value=['test-model', 'other-model']),
                       patch('host_server._background_sync', return_value=None),
                       patch('live_tools.internet_available', return_value=False)]
        for p in cls.patches:
            p.start()
        cls.engine_llm = intelligence.ENGINE.llm
        cls.engine_search = intelligence.ENGINE._searcher
        intelligence.ENGINE._searcher = FakeSearch(key=False)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), host_server.Handler)
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        intelligence.ENGINE.llm = cls.engine_llm
        intelligence.ENGINE._searcher = cls.engine_search
        core.DATA, core.DB_PATH = cls.saved
        cls.tmp.cleanup()

    def setUp(self):
        intelligence.ENGINE.llm = FakeLLM(stream=['Привет! 💜 Чем займёмся?'] * 10)
        with host_server.RATE_LOCK:
            host_server.RATE.clear()

    def client(self, token='token-alice-0123456789'):
        c = HostClient('https://example.ts.net', lambda refresh=False: token)
        c.base = self.url          # test server is plain HTTP on loopback
        return c

    def raw(self, method, path, token=None, body=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        return build_opener(ProxyHandler({})).open(Request(self.url + path, data=data, headers=headers, method=method), timeout=10)

    def test_health_is_public_but_minimal(self):
        with self.raw('GET', '/api/health') as resp:
            data = json.load(resp)
            self.assertEqual(resp.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(set(data), {'service', 'version', 'ok'})

    def test_v2_requires_valid_token(self):
        for token in (None, 'forged-token-0123456789'):
            with self.assertRaises(HTTPError) as ctx:
                self.raw('GET', '/api/v2/conversations', token)
            self.assertEqual(ctx.exception.code, 401)

    def test_streaming_chat_and_history(self):
        c = self.client()
        events = list(c.chat_stream('Как дела, Челси?'))
        types = [e['type'] for e in events]
        self.assertEqual(types[0], 'start')
        self.assertIn('delta', types)
        self.assertEqual(types[-1], 'done')
        cid = events[0]['conversation_id']
        msgs = c.messages(cid)
        self.assertEqual([m['role'] for m in msgs], ['user', 'assistant'])
        self.assertIn('Чем займёмся', msgs[1]['content'])
        self.assertTrue(any(x['id'] == cid for x in c.conversations()))

    def test_user_isolation(self):
        alice, bob = self.client(), self.client('token-bob-0123456789xx')
        cid = list(alice.chat_stream('Секрет Алисы'))[0]['conversation_id']
        with self.assertRaises(HostError) as ctx:
            bob.messages(cid)
        self.assertEqual(ctx.exception.status, 404)
        self.assertFalse(any(c['id'] == cid for c in bob.conversations()))
        with self.assertRaises(HostError):
            list(bob.chat_stream('Пишу в чужой чат', conversation_id=cid))
        with self.assertRaises(HostError):
            bob.update_conversation(cid, title='взлом')
        self.assertEqual([m['content'] for m in alice.messages(cid)][0], 'Секрет Алисы')

    def test_stop_generation(self):
        intelligence.ENGINE.llm = SlowLLM()
        c = self.client()
        request_id = str(uuid.uuid4())
        events = []

        def run():
            events.extend(c.chat_stream('Длинный ответ', request_id=request_id))
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.4)
        self.assertTrue(c.stop(request_id)['stopped'])
        worker.join(10)
        self.assertTrue(events[-1]['stopped'])
        self.assertIn('остановлена', events[-1]['text'])
        other = self.client('token-bob-0123456789xx')
        with self.assertRaises(HostError):
            other.stop(request_id)       # finished/unknown or not yours

    def test_regenerate_edit_rate(self):
        intelligence.ENGINE.llm = FakeLLM(stream=['Первый ответ.', 'Второй ответ.', 'Ответ на правку.'])
        c = self.client()
        cid = list(c.chat_stream('Вопрос один'))[0]['conversation_id']
        list(c.chat_stream('', conversation_id=cid, regenerate=True))
        msgs = c.messages(cid)
        self.assertEqual([m['content'] for m in msgs], ['Вопрос один', 'Второй ответ.'])
        list(c.chat_stream('Вопрос исправленный', conversation_id=cid, edit_message_id=msgs[0]['id']))
        msgs = c.messages(cid)
        self.assertEqual(msgs[0]['content'], 'Вопрос исправленный')
        self.assertEqual(len(msgs), 2)
        c.rate(msgs[1]['id'], 1)
        self.assertEqual(c.messages(cid)[1]['rating'], 1)

    def test_conversation_management(self):
        c = self.client()
        cid = c.create_conversation('Планы')
        c.update_conversation(cid, favorite=True, title='Планы на неделю')
        conv = next(x for x in c.conversations() if x['id'] == cid)
        self.assertTrue(conv['favorite'])
        self.assertEqual(conv['title'], 'Планы на неделю')
        self.assertTrue(any(x['id'] == cid for x in c.conversations(favorites=True)))
        c.delete_conversation(cid)
        self.assertFalse(any(x['id'] == cid for x in c.conversations()))

    def test_validation(self):
        token = 'token-alice-0123456789'
        with self.assertRaises(HTTPError) as ctx:
            self.raw('POST', '/api/v2/chat', token, {'message': 'x', 'model': 'evil-model'})
        self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(HTTPError) as ctx:
            self.raw('POST', '/api/v2/chat', token, b'{' + b' ' * 80000 + b'}')
        self.assertEqual(ctx.exception.code, 413)
        with self.assertRaises(HTTPError) as ctx:
            self.raw('POST', '/api/v2/chat', token, b'not json')
        self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(HTTPError) as ctx:
            self.raw('POST', '/api/v2/chat', token, {'message': 'x', 'conversation_id': '../../etc'})
        self.assertEqual(ctx.exception.code, 400)

    def test_research_permissions(self):
        alice, owner = self.client(), self.client('token-owner-0123456789')
        with self.assertRaises(HostError) as ctx:
            alice.call('POST', '/api/v2/research', {'topic': 'тема для общей базы', 'scope': 'shared'})
        self.assertEqual(ctx.exception.status, 400)
        job = alice.start_research('https://docs.example.org/', max_pages=1)
        self.assertEqual(alice.research_job(job)['job']['id'], job)
        with self.assertRaises(HostError):
            self.client('token-bob-0123456789xx').research_job(job)
        self.assertTrue(alice.stop_research(job)['stopped'])
        self.assertTrue(owner.call('POST', '/api/v2/research', {'topic': 'общая тема', 'scope': 'shared'})['id'])
        with self.assertRaises(HostError) as ctx:
            alice.call('POST', '/api/v2/knowledge/sources', {'domain': 'docs.python.org'})
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(owner.call('POST', '/api/v2/knowledge/sources', {'domain': 'docs.python.org'})['domain'],
                         'docs.python.org')

    def test_settings_are_validated_per_user(self):
        c = self.client()
        c.call('POST', '/api/v2/settings', {'generation': {'temperature': 7, 'num_ctx': 1}, 'web_search': False})
        s = c.call('GET', '/api/v2/settings')
        self.assertEqual(s['generation']['temperature'], 1.5)
        self.assertEqual(s['generation']['num_ctx'], 2048)
        self.assertFalse(s['web_search'])
        other = self.client('token-bob-0123456789xx').call('GET', '/api/v2/settings')
        self.assertTrue(other['web_search'])

    def test_legacy_092_chat_endpoint_still_works(self):
        with patch('host_server.cloud_sync'), patch('host_server.pull_memories'), patch('host_server.push_memories'):
            with self.raw('POST', '/api/chat', 'token-alice-0123456789',
                          {'message': 'Привет из старого клиента', 'model': 'llama3.2:1b'}) as resp:
                data = json.load(resp)
        self.assertIn('messages', data)
        self.assertEqual(data['messages'][-2]['content'], 'Привет из старого клиента')

    def test_status_reports_capabilities(self):
        with patch('ollama_client.available', return_value=False):
            status = self.client('token-owner-0123456789').status()
        self.assertTrue(status['owner'])
        self.assertFalse(status['ollama'])
        self.assertIn('knowledge', status)
        self.assertFalse(self.client().status()['owner'])


if __name__ == '__main__':
    unittest.main()
