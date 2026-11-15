"""Data safety, sync v2, launcher port, training pipeline and model evaluation tests (0.9.3)."""
import socket
import sqlite3
import tempfile
from contextlib import closing
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import cloud_sync
import core
import migrations


def make_092_database(path):
    """Exact schema and sample data as created by Chelsy 0.9.2."""
    conn = sqlite3.connect(path)
    conn.executescript('''
    CREATE TABLE pages (url TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE queue (url TEXT PRIMARY KEY, done INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX messages_user_idx ON messages(username,id);
    CREATE TABLE personal_memory (username TEXT NOT NULL, memory_key TEXT NOT NULL, value TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(username,memory_key));
    CREATE TABLE pending_memory (username TEXT NOT NULL, memory_key TEXT NOT NULL, PRIMARY KEY(username,memory_key));
    CREATE TABLE cloud_imported (username TEXT NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,cloud_id));
    CREATE TABLE cloud_origin (username TEXT NOT NULL, local_id INTEGER NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,local_id));
    ''')
    conn.executemany('INSERT INTO messages(username,role,content,created) VALUES(?,?,?,?)', [
        ('owner', 'user', 'Привет из 0.9.2', '2026-11-01T10:00:00+00:00'),
        ('owner', 'assistant', 'Ответ из 0.9.2', '2026-11-01T10:00:05+00:00'),
        ('sb:11111111-1111-4111-8111-111111111111', 'user', 'Облачное сообщение', '2026-11-02T10:00:00+00:00'),
    ])
    conn.execute("INSERT INTO cloud_origin VALUES ('sb:11111111-1111-4111-8111-111111111111', 3, 'cloud-x')")
    conn.execute("INSERT INTO personal_memory VALUES ('owner','цвет','фиолетовый','2026-11-01')")
    conn.execute("INSERT INTO pages VALUES ('https://docs.python.org/3/','Python docs','" + 'Python tutorial text ' * 30 + "','2026-10-01')")
    conn.commit()
    conn.close()


class TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = core.DATA, core.DB_PATH
        core.DATA = Path(self.tmp.name)
        core.DB_PATH = core.DATA / 'chelsy.db'

    def tearDown(self):
        core.DATA, core.DB_PATH = self.saved
        self.tmp.cleanup()


class MigrationTests(TempData):
    def test_upgrade_092_database_preserves_everything_and_backs_up(self):
        make_092_database(core.DB_PATH)
        applied = migrations.migrate(core.DB_PATH)
        self.assertEqual(applied, list(range(1, migrations.SCHEMA_VERSION + 1)))
        backups = sorted((core.DATA / 'backups').glob('*-before-v*.db'))
        self.assertEqual(len(backups), migrations.SCHEMA_VERSION)
        with closing(sqlite3.connect(backups[0])) as old:
            self.assertEqual(old.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 3)
        self.assertEqual([m['content'] for m in core.history('owner')], ['Привет из 0.9.2', 'Ответ из 0.9.2'])
        self.assertEqual(core.personal_facts('owner'), {'цвет': 'фиолетовый'})
        conv = core.list_conversations('owner')
        self.assertEqual(conv[0]['title'], 'Предыдущая история')
        with core.db() as conn:
            keys = [r[0] for r in conn.execute("SELECT legacy_key FROM messages WHERE username='owner' ORDER BY id")]
            synced = conn.execute('SELECT cloud_synced FROM messages WHERE id=3').fetchone()[0]
            kb = conn.execute("SELECT COUNT(*) FROM kb_documents WHERE scope='shared'").fetchone()[0]
            pages = conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        self.assertEqual(keys, ['host:1', 'host:2'])         # same cloud keys as 0.9.2 -> no duplicates
        self.assertEqual(synced, 1)                          # cloud-origin rows are never re-uploaded
        self.assertEqual((kb, pages), (1, 1))                # research pages imported, original table kept
        self.assertEqual(migrations.migrate(core.DB_PATH), [])  # idempotent

    def test_rollback_restores_backup_and_keeps_safety_copy(self):
        make_092_database(core.DB_PATH)
        migrations.migrate(core.DB_PATH)
        core.save_message('owner', 'user', 'новое после обновления')
        message = migrations.rollback(core.DB_PATH, 1)
        self.assertIn('Restored', message)
        self.assertEqual(migrations.inspect(core.DB_PATH)['version'], 1)
        self.assertTrue(list((core.DATA / 'backups').glob(f'*pre-rollback-v{migrations.SCHEMA_VERSION}.db')))
        core._MIGRATED.discard(str(core.DB_PATH))

    def test_down_migrations(self):
        make_092_database(core.DB_PATH)
        migrations.migrate(core.DB_PATH)
        migrations.rollback(core.DB_PATH, 1, use_down=True)
        info = migrations.inspect(core.DB_PATH)
        self.assertEqual(info['version'], 1)
        self.assertNotIn('conversations', info['tables'])
        self.assertNotIn('conversation_id', info['tables']['messages'])
        with closing(sqlite3.connect(core.DB_PATH)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 3)
        core._MIGRATED.discard(str(core.DB_PATH))

    def test_failed_migration_rolls_back_transaction(self):
        make_092_database(core.DB_PATH)
        migrations.migrate(core.DB_PATH, target=1)

        def broken(conn):
            conn.execute('CREATE TABLE half_done (x)')
            raise RuntimeError('boom')
        with patch.dict(migrations.MIGRATIONS, {2: ('broken', broken, None)}):
            with self.assertRaises(RuntimeError):
                migrations.migrate(core.DB_PATH, target=2)
        info = migrations.inspect(core.DB_PATH)
        self.assertEqual(info['version'], 1)
        self.assertNotIn('half_done', info['tables'])


class SyncV2Tests(TempData):
    """Fake PostgREST that understands the v2 schema (conversations + soft delete)."""

    def setUp(self):
        super().setUp()
        self.uid = str(uuid.uuid4())
        self.cloud = {'chelsy_ai_conversations': {}, 'chelsy_ai_messages': {}, 'chelsy_ai_memories': {}}
        cloud_sync._schema_cache.clear()

    def api(self, table, token, method='GET', params=None, rows=None, prefer=None):
        store = self.cloud[table]
        if method == 'GET':
            if table == 'chelsy_ai_messages':
                offset = int((params or {}).get('offset', 0))
                return list(store.values())[offset:offset + 500]
            return list(store.values())
        if method == 'POST':
            for row in rows:
                assert row['user_id'] == self.uid, 'cross-account write'
                key = row['id'] if table == 'chelsy_ai_conversations' else (
                    row['legacy_id'] if table == 'chelsy_ai_messages' else row['memory_key'])
                if key in store and 'ignore' in (prefer or ''):
                    continue
                existing = store.get(key, {'id': str(uuid.uuid4())} if table == 'chelsy_ai_messages' else {})
                store[key] = {**existing, **row}
            return []
        if method == 'PATCH':
            for row in store.values():
                row.update(rows)
            return []
        raise AssertionError(method)

    def test_two_hosts_share_history_without_duplicates(self):
        user = 'sb:' + self.uid
        with patch.object(cloud_sync, '_call', side_effect=self.api):
            cid = core.create_conversation(user, 'Общий диалог')
            core.save_message(user, 'user', 'С ноутбука', cid)
            cloud_sync.sync(self.uid, 'token')
            self.assertEqual(cloud_sync.sync(self.uid, 'token')['considered_for_upload'], 0)   # nothing re-sent
            # Second host: fresh database
            first_db = core.DB_PATH
            core.DB_PATH = core.DATA / 'second.db'
            received = cloud_sync.sync(self.uid, 'token')['received']
            self.assertEqual(received, 1)
            convs = core.list_conversations(user)
            self.assertEqual([c['title'] for c in convs], ['Общий диалог'])
            core.save_message(user, 'assistant', 'Со второго ПК', convs[0]['id'])
            cloud_sync.sync(self.uid, 'token')
            core.DB_PATH = first_db
            cloud_sync.sync(self.uid, 'token')
            self.assertEqual([m['content'] for m in core.conversation_messages(user, cid)], ['С ноутбука', 'Со второго ПК'])
        self.assertEqual(len(self.cloud['chelsy_ai_messages']), 2)

    def test_offline_changes_survive_and_upload_later(self):
        user = 'sb:' + self.uid
        cid = core.create_conversation(user)
        core.save_message(user, 'user', 'Написано без интернета', cid)

        def offline(*a, **k):
            raise cloud_sync.SyncError('Cloud service unavailable')
        with patch.object(cloud_sync, '_call', side_effect=offline):
            with self.assertRaises(cloud_sync.SyncError):
                cloud_sync.sync(self.uid, 'token')
        self.assertEqual(len(core.history(user, cid)), 1)            # nothing lost locally
        with patch.object(cloud_sync, '_call', side_effect=self.api):
            self.assertEqual(cloud_sync.sync(self.uid, 'token')['considered_for_upload'], 1)

    def test_soft_delete_and_favourite_propagate(self):
        user = 'sb:' + self.uid
        with patch.object(cloud_sync, '_call', side_effect=self.api):
            cid = core.create_conversation(user, 'Удаляемый')
            core.save_message(user, 'user', 'текст', cid)
            cloud_sync.sync(self.uid, 'token')
            core.set_favorite(user, cid, True)
            core.delete_conversation(user, cid)
            cloud_sync.sync(self.uid, 'token')
        row = self.cloud['chelsy_ai_conversations'][cid]
        self.assertTrue(row['favorite'])
        self.assertIsNotNone(row['deleted_at'])

    def test_memory_last_writer_wins_but_local_pending_first(self):
        user = 'sb:' + self.uid
        self.cloud['chelsy_ai_memories']['город'] = {'memory_key': 'город', 'value': 'Казань', 'updated_at': '2099-01-01T00:00:00+00:00'}
        with patch.object(cloud_sync, '_call', side_effect=self.api):
            cloud_sync.pull_memories(self.uid, 'token')
            self.assertEqual(core.personal_facts(user)['город'], 'Казань')
            core.store_personal_fact(user, 'город', 'Москва')        # local pending edit
            cloud_sync.pull_memories(self.uid, 'token')
            self.assertEqual(core.personal_facts(user)['город'], 'Москва')
            self.assertEqual(cloud_sync.push_memories(self.uid, 'token'), 1)
        self.assertEqual(self.cloud['chelsy_ai_memories']['город']['value'], 'Москва')


class LauncherPortTests(unittest.TestCase):
    def test_busy_port_moves_to_next_free_port(self):
        import launcher
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            taken = busy.getsockname()[1]
            with patch('launcher.range', create=True, side_effect=lambda a, b: [taken, taken + 1]), \
                    patch.dict('os.environ', {'GRADIO_SERVER_PORT': ''}):
                port = launcher.choose_ui_port()
        self.assertNotEqual(port, taken)

    def test_explicit_busy_port_fails_clearly(self):
        import launcher
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            with patch.dict('os.environ', {'GRADIO_SERVER_PORT': str(busy.getsockname()[1])}):
                with self.assertRaises(OSError):
                    launcher.choose_ui_port()

    def test_invalid_port_value(self):
        import launcher
        with patch.dict('os.environ', {'GRADIO_SERVER_PORT': '99999'}):
            with self.assertRaises(ValueError):
                launcher.choose_ui_port()


class TrainingPipelineTests(TempData):
    def test_dataset_requires_consent_and_rating(self):
        from training.dataset import export
        cid = core.create_conversation('alice')
        core.save_message('alice', 'user', 'Почему небо голубое?', cid)
        mid = core.save_message('alice', 'assistant', 'Из-за рассеяния света в атмосфере. Пиши мне на a@b.ru', cid)
        core.rate_message('alice', mid, 1)
        bob = core.create_conversation('bob')
        core.save_message('bob', 'user', 'Секрет Боба', bob)
        core.rate_message('bob', core.save_message('bob', 'assistant', 'Ответ Бобу без согласия', bob), 1)
        self.assertEqual(export(core.DATA / 'ds0')['train'] + export(core.DATA / 'ds0')['eval'], 0)  # no consent yet
        core.set_setting('alice', 'training_consent', True)
        manifest = export(core.DATA / 'ds1')
        self.assertEqual(manifest['train'] + manifest['eval'], 1)
        text = (core.DATA / 'ds1' / 'train.jsonl').read_text(encoding='utf-8') + (core.DATA / 'ds1' / 'eval.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('Боба', text)
        self.assertNotIn('a@b.ru', text)
        self.assertEqual(len(manifest['sha256']), 64)

    def test_registry_gate_and_rollback(self):
        from training.registry import ModelRegistry, RegistryError

        class Done:
            returncode, stderr = 0, ''
        gguf = core.DATA / 'model.gguf'
        gguf.write_bytes(b'GGUF')
        calls = []
        registry = ModelRegistry(core.DATA / 'models', runner=lambda args, **kw: calls.append(args) or Done())
        registry.register('chelsy-ft:v1', gguf, 'qwen3:8b')
        self.assertEqual(calls[0][:3], ['ollama', 'create', 'chelsy-ft:v1'])     # no shell, argument list
        with self.assertRaises(RegistryError):
            registry.activate('chelsy-ft:v1', baseline_score=0.8)                   # not evaluated
        registry.record_score('chelsy-ft:v1', 0.5)
        with self.assertRaises(RegistryError):
            registry.activate('chelsy-ft:v1', baseline_score=0.8)                   # worse than baseline
        registry.record_score('chelsy-ft:v1', 0.85)
        registry.activate('chelsy-ft:v1', baseline_score=0.8)
        import intelligence
        self.assertEqual(intelligence.load_host_settings()['model'], 'chelsy-ft:v1')
        self.assertIsNone(registry.rollback())
        self.assertEqual(intelligence.load_host_settings()['model'], '')
        self.assertTrue(list((core.DATA / 'models' / 'backups').glob('registry-*.json')))
        with self.assertRaises(RegistryError):
            registry.register('bad name; rm -rf /', gguf, 'x')


class ModelEvalTests(unittest.TestCase):
    def test_scoring_and_ranking_with_fake_models(self):
        import model_eval

        class LLM:
            def chat(self, model, messages, options=None):
                q = messages[-1]['content']
                if model == 'bad':
                    return 'The the the the ' * 40
                if 'Python' in q or 'Python function' in q or 'palindrome' in q:
                    return 'Вот пример:\n```python\ndef f(x):\n    return x == x[::-1]\n```\nРекурсия — вызов функции из себя.'
                if q.startswith(('Hi', 'Explain')):
                    return 'Hello! A cookie is a small piece of data the server asks the browser to store and send back.'
                return 'Это зависит от рассеяния света: короткие волны рассеиваются сильнее. 1. Шаг первый. Прогноз точно невозможно.'
        results = model_eval.evaluate(['good', 'bad'], llm=LLM())
        self.assertEqual([r['model'] for r in results], ['good', 'bad'])
        self.assertGreater(results[0]['score'], results[1]['score'])
        path = model_eval.write_report(results, Path(tempfile.mkdtemp()))
        self.assertIn('| good |', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
