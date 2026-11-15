import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core


class TestChelsy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db = core.DB_PATH
        self.previous_data = core.DATA
        self.previous_users = core.USERS_PATH
        core.DATA = Path(self.tmp.name)
        core.DB_PATH = core.DATA / 'chelsy.db'
        core.USERS_PATH = core.DATA / 'users.json'
    def tearDown(self):
        core.DATA = self.previous_data
        core.DB_PATH = self.previous_db
        core.USERS_PATH = self.previous_users
        self.tmp.cleanup()

    def test_database_connection_closed_after_use(self):
        import sqlite3
        with core.db() as connection:
            connection.execute('SELECT 1').fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_password_and_login(self):
        core.create_user('owner', 'some_long_password_123')
        self.assertTrue(core.authenticate('owner', 'some_long_password_123'))
        self.assertFalse(core.authenticate('owner', 'wrong'))
        self.assertFalse(core.authenticate('tester', 'some_long_password_123'))
        with self.assertRaises(ValueError): core.create_user('owner', 'new_password_123')

    def test_chat_separation(self):
        core.save_message('owner', 'user', 'secret message')
        core.save_message('tester', 'user', 'hello')
        self.assertEqual(len(core.history('tester')), 1)
        self.assertNotIn('secret', str(core.history('tester')))
        core.clear_history('tester')
        self.assertEqual(core.history('tester'), [])
        self.assertEqual(len(core.history('owner')), 1)

    def test_explicit_fact_persists_per_user(self):
        value = core.extract_explicit_memory('Моего тестового робота зовут Астра-7. Запомни это имя.')
        self.assertEqual(value, ('тестовый робот', 'Астра-7'))
        core.store_personal_fact('owner', *value)
        self.assertEqual(core.personal_facts('owner')['тестовый робот'], 'Астра-7')
        self.assertEqual(core.personal_facts('tester'), {})
        self.assertEqual(core.direct_memory_answer('Как зовут моего тестового робота?',core.personal_facts('owner')),
                         'Твоего тестового робота зовут Астра-7.')
        self.assertIsNone(core.direct_memory_answer('Как зовут моего тестового робота?',core.personal_facts('tester')))

    def test_memory_chat_without_ollama(self):
        messages, cleared = core.answer('Моего тестового робота зовут Астра-7. Запомни это имя.', 'owner')
        self.assertIn('Астра-7',messages[-1]['content'])
        messages, cleared = core.answer('Как зовут моего тестового робота?', 'owner')
        self.assertIn('Астра-7', messages[-1]['content'])
        self.assertEqual(cleared, '')
        self.assertEqual(len(core.history('owner')), 4)

    def test_key_value_memory_and_delete(self):
        fact = core.extract_explicit_memory('Запомни: любимая тема = астрофизика')
        self.assertEqual(fact, ('любимая тема', 'астрофизика'))
        core.store_personal_fact('owner', *fact)
        self.assertEqual(core.personal_facts('owner')['любимая тема'], 'астрофизика')
        core.delete_personal_fact('owner','любимая тема')
        self.assertNotIn('любимая тема', core.personal_facts('owner'))

    def test_memory_search(self):
        with core.db() as con:
            con.execute('INSERT INTO pages VALUES (?,?,?,?)',
                        ('https://example.org/test','Обучение Python', 'В Python есть функции и циклы ' * 15, '2026-01-01'))
        self.assertEqual(core.stats()[0], 1)
        results = core.search('Обучение Python')
        self.assertEqual(results[0][1], 'https://example.org/test')

    def test_block_private_addresses(self):
        for url in ['http://127.0.0.1/', 'http://localhost/', 'http://192.168.1.2/', 'file:///etc/passwd',
                    'http://10.0.0.5/', 'http://host.local/', 'https://example.org:8080/']:
            self.assertFalse(core.public_url(url), url)

    def test_html(self):
        parser = core.PageParser()
        parser.feed('<html><title>Demo</title><script>BAD</script><p>Useful info</p><a href="/next">Link</a></html>')
        self.assertIn('Useful info', parser.content)
        self.assertNotIn('BAD', parser.content)
        self.assertEqual(parser.links, ['/next'])

    def test_crawler_offline_fixture(self):
        class FakeResponse:
            class Headers:
                def get_content_type(self): return 'text/html'
                def get_content_charset(self): return 'utf-8'
            headers = Headers()
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, n): return b'<html><title>Research page</title><body>' + (b'Meaningful research text. ' * 20) + b'</body></html>'
        with core.db() as conn:
            conn.execute('INSERT INTO queue(url) VALUES (?)',('https://example.org/start',))
        with patch.object(core,'public_url', return_value=True), patch.object(core,'robots_permission', return_value=(True,0)), patch.object(core.OPENER,'open',return_value=FakeResponse()):
            lines = core.crawl(1)
        self.assertTrue(any('Сохранено' in x for x in lines))
        self.assertEqual(core.stats()[0],1)


if __name__ == '__main__': unittest.main()
