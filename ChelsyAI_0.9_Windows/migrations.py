"""Versioned, reversible SQLite migrations for Chelsy AI (0.9.3).

Rules (data safety first):
* every migration of a database that already holds data is preceded by an
  online backup made with the SQLite backup API (``data/backups``);
* migrations are additive: no table, user, message or memory is dropped;
* the applied versions are recorded in ``schema_migrations`` and
  ``PRAGMA user_version``;
* ``rollback(path, version)`` restores the backup taken before the next
  version, after first saving the current state as another backup.

Command line::

    python migrations.py status            # show version and tables
    python migrations.py migrate           # apply pending migrations
    python migrations.py backup            # manual backup
    python migrations.py rollback 1        # return to schema version 1
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 4
MAX_BACKUPS = 30


def _now():
    return datetime.now(timezone.utc).isoformat()


def columns(conn, table):
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def tables(conn):
    return sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def inspect(path):
    """Return {'version': n, 'tables': {name: [columns]}} without modifying the DB."""
    path = Path(path)
    if not path.exists():
        return {'version': 0, 'tables': {}}
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=15)
    try:
        return {'version': conn.execute('PRAGMA user_version').fetchone()[0],
                'tables': {t: columns(conn, t) for t in tables(conn)}}
    finally:
        conn.close()


def _script(conn, sql):
    """Run several statements inside the CURRENT transaction (executescript would commit)."""
    for statement in sql.split(';\n'):
        if statement.strip():
            conn.execute(statement)


def _add_column(conn, table, name, ddl):
    if name not in columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}')


# --------------------------------------------------------------------------- v1
def _m1_baseline(conn):
    """Schema of Chelsy 0.9.2 (created lazily by core.db() and cloud_sync)."""
    _script(conn, '''
    CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY, done INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS messages_user_idx ON messages(username,id);
    CREATE TABLE IF NOT EXISTS personal_memory (username TEXT NOT NULL, memory_key TEXT NOT NULL, value TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(username,memory_key));
    CREATE TABLE IF NOT EXISTS pending_memory (username TEXT NOT NULL, memory_key TEXT NOT NULL, PRIMARY KEY(username,memory_key));
    CREATE TABLE IF NOT EXISTS cloud_imported (username TEXT NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,cloud_id));
    CREATE TABLE IF NOT EXISTS cloud_origin (username TEXT NOT NULL, local_id INTEGER NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,local_id));
    ''')


# --------------------------------------------------------------------------- v2
def _m2_conversations(conn):
    """Conversations, stable message ids for multi-device sync, feedback, settings."""
    _script(conn, '''
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        title TEXT NOT NULL,
        created TEXT NOT NULL,
        updated TEXT NOT NULL,
        favorite INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0,
        cloud_synced INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations(username, deleted, updated);
    CREATE TABLE IF NOT EXISTS user_settings (
        username TEXT NOT NULL, setting TEXT NOT NULL, value TEXT NOT NULL,
        PRIMARY KEY(username, setting)
    );
    ''')
    _add_column(conn, 'messages', 'conversation_id', 'TEXT')
    _add_column(conn, 'messages', 'client_id', 'TEXT')
    _add_column(conn, 'messages', 'legacy_key', 'TEXT')
    _add_column(conn, 'messages', 'cloud_synced', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'messages', 'deleted', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'messages', 'rating', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'messages', 'meta', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'personal_memory', 'updated', 'TEXT')
    conn.execute('UPDATE personal_memory SET updated=created WHERE updated IS NULL')
    # Existing messages keep the exact cloud key used by 0.9.2 (host:<id>) so a
    # re-upload is still de-duplicated by the cloud unique(user_id, legacy_id).
    conn.execute("UPDATE messages SET legacy_key='host:'||id WHERE legacy_key IS NULL")
    for (row_id,) in conn.execute('SELECT id FROM messages WHERE client_id IS NULL').fetchall():
        conn.execute('UPDATE messages SET client_id=? WHERE id=?', (str(uuid.uuid4()), row_id))
    # Messages that came from the cloud must never be uploaded again.
    if 'cloud_origin' in tables(conn):
        conn.execute('UPDATE messages SET cloud_synced=1 WHERE id IN '
                     '(SELECT local_id FROM cloud_origin WHERE cloud_origin.username=messages.username)')
    users = [r[0] for r in conn.execute(
        'SELECT DISTINCT username FROM messages WHERE conversation_id IS NULL')]
    for username in users:
        first, last = conn.execute(
            'SELECT MIN(created), MAX(created) FROM messages WHERE username=? AND conversation_id IS NULL',
            (username,)).fetchone()
        cid = str(uuid.uuid4())
        conn.execute('INSERT INTO conversations(id,username,title,created,updated) VALUES(?,?,?,?,?)',
                     (cid, username, 'Предыдущая история', first or _now(), last or _now()))
        conn.execute('UPDATE messages SET conversation_id=? WHERE username=? AND conversation_id IS NULL',
                     (cid, username))
    conn.execute('CREATE INDEX IF NOT EXISTS messages_conv_idx ON messages(username, conversation_id, id)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS messages_client_id ON messages(client_id)')


def _m2_down(conn):
    conn.execute('DROP INDEX IF EXISTS messages_conv_idx')
    conn.execute('DROP INDEX IF EXISTS messages_client_id')
    for name in ('conversation_id', 'client_id', 'legacy_key', 'cloud_synced', 'deleted', 'rating', 'meta'):
        if name in columns(conn, 'messages'):
            conn.execute(f'ALTER TABLE messages DROP COLUMN {name}')
    if 'updated' in columns(conn, 'personal_memory'):
        conn.execute('ALTER TABLE personal_memory DROP COLUMN updated')
    conn.execute('DROP TABLE IF EXISTS conversations')
    conn.execute('DROP TABLE IF EXISTS user_settings')


# --------------------------------------------------------------------------- v3
def _m3_knowledge(conn):
    """Knowledge Engine storage (documents, versions, chunks, FTS, research)."""
    _script(conn, '''
    CREATE TABLE IF NOT EXISTS kb_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,                 -- 'shared' or owner storage key
        url TEXT NOT NULL,
        canonical_url TEXT NOT NULL,
        domain TEXT NOT NULL,
        title TEXT NOT NULL,
        topic TEXT NOT NULL DEFAULT '',
        lang TEXT NOT NULL DEFAULT '',
        published TEXT NOT NULL DEFAULT '',
        fetched TEXT NOT NULL,
        updated TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        body TEXT NOT NULL,
        trust REAL NOT NULL DEFAULT 0.5,
        flags TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active',
        version INTEGER NOT NULL DEFAULT 1,
        UNIQUE(scope, canonical_url)
    );
    CREATE INDEX IF NOT EXISTS kb_documents_hash ON kb_documents(scope, content_hash);
    CREATE TABLE IF NOT EXISTS kb_versions (
        doc_id INTEGER NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL,
        content_hash TEXT NOT NULL, body TEXT NOT NULL, fetched TEXT NOT NULL,
        published TEXT NOT NULL DEFAULT '', archived TEXT NOT NULL,
        PRIMARY KEY(doc_id, version)
    );
    CREATE TABLE IF NOT EXISTS kb_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL, ord INTEGER NOT NULL, text TEXT NOT NULL,
        embedding BLOB, embed_model TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS kb_chunks_doc ON kb_chunks(doc_id, ord);
    CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2');
    CREATE TABLE IF NOT EXISTS kb_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_a INTEGER NOT NULL, doc_b INTEGER NOT NULL,
        claim_a TEXT NOT NULL, claim_b TEXT NOT NULL,
        detected TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
        UNIQUE(doc_a, doc_b, claim_a, claim_b)
    );
    CREATE TABLE IF NOT EXISTS kb_sources (
        domain TEXT PRIMARY KEY, added_by TEXT NOT NULL, trust REAL NOT NULL DEFAULT 0.7,
        created TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS research_jobs (
        id TEXT PRIMARY KEY, username TEXT NOT NULL, topic TEXT NOT NULL,
        kind TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '', params TEXT NOT NULL DEFAULT '{}',
        created TEXT NOT NULL, updated TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS research_jobs_user ON research_jobs(username, created);
    CREATE TABLE IF NOT EXISTS research_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
        published TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '',
        trust REAL NOT NULL DEFAULT 0, flags TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'found', doc_id INTEGER, note TEXT NOT NULL DEFAULT '',
        lang TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
        UNIQUE(job_id, url)
    );
    CREATE TABLE IF NOT EXISTS research_schedule (
        id TEXT PRIMARY KEY, username TEXT NOT NULL, topic TEXT NOT NULL,
        interval_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        auto_accept INTEGER NOT NULL DEFAULT 0, target_scope TEXT NOT NULL,
        last_run TEXT NOT NULL DEFAULT '', created TEXT NOT NULL
    );
    ''')
    # Import 0.9.2 research pages into the knowledge base (pages table is kept).
    import hashlib
    from urllib.parse import urlparse
    for url, title, body, updated in conn.execute('SELECT url,title,body,updated FROM pages').fetchall():
        digest = hashlib.sha256(body.encode('utf-8', 'replace')).hexdigest()
        domain = (urlparse(url).hostname or '').lower()
        cur = conn.execute(
            'INSERT OR IGNORE INTO kb_documents(scope,url,canonical_url,domain,title,fetched,updated,content_hash,body,trust,flags) '
            "VALUES('shared',?,?,?,?,?,?,?,?,0.5,'[\"imported-0.9.2\"]')",
            (url, url, domain, title, updated, updated, digest, body))
        if cur.rowcount:
            doc_id = cur.lastrowid
            step = 1100
            for order, start in enumerate(range(0, len(body), step)):
                text = body[start:start + step + 150]
                cid = conn.execute('INSERT INTO kb_chunks(doc_id,ord,text) VALUES(?,?,?)',
                                   (doc_id, order, text)).lastrowid
                conn.execute('INSERT INTO kb_fts(rowid,text) VALUES(?,?)', (cid, text))


def _m3_down(conn):
    for name in ('kb_fts', 'kb_chunks', 'kb_versions', 'kb_conflicts', 'kb_sources',
                 'research_items', 'research_jobs', 'research_schedule', 'kb_documents'):
        conn.execute(f'DROP TABLE IF EXISTS {name}')


# --------------------------------------------------------------------------- v4
def _m4_learning_log(conn):
    """Journal of autonomous learning (what Chelsy studied, why, from where)."""
    _script(conn, '''
    CREATE TABLE IF NOT EXISTS learning_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        created TEXT NOT NULL,
        event TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS learning_log_user ON learning_log(username, created)
    ''')


def _m4_down(conn):
    conn.execute('DROP TABLE IF EXISTS learning_log')


MIGRATIONS = {
    1: ('Baseline schema of Chelsy 0.9.2', _m1_baseline, None),
    2: ('Conversations, stable message ids, feedback, user settings', _m2_conversations, _m2_down),
    3: ('Knowledge Engine and research storage', _m3_knowledge, _m3_down),
    4: ('Autonomous learning journal', _m4_learning_log, _m4_down),
}


def backup(path, label='manual'):
    """Consistent online copy of the database. Returns the backup path or None."""
    path = Path(path)
    if not path.exists():
        return None
    folder = path.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    target = folder / f'{path.stem}-{stamp}-{label}.db'
    source = sqlite3.connect(path, timeout=30)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    backups = sorted(folder.glob(f'{path.stem}-*.db'))
    for old in backups[:-MAX_BACKUPS]:
        if '-before-v' not in old.name:   # migration backups are never auto-removed
            old.unlink(missing_ok=True)
    return target


def _has_user_data(conn):
    for table in ('messages', 'personal_memory', 'pages'):
        if table in tables(conn) and conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
            return True
    return False


def migrate(path, target=SCHEMA_VERSION, log=None):
    """Apply pending migrations up to ``target``. Returns list of applied versions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    applied = []
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version >= target:
            return applied
        for number in range(version + 1, target + 1):
            description, up, _ = MIGRATIONS[number]
            saved = backup(path, f'before-v{number}') if _has_user_data(conn) else None
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, '
                             'description TEXT NOT NULL, applied TEXT NOT NULL, backup TEXT NOT NULL DEFAULT \'\')')
                up(conn)
                conn.execute('INSERT OR REPLACE INTO schema_migrations(version,description,applied,backup) VALUES(?,?,?,?)',
                             (number, description, _now(), str(saved or '')))
                conn.execute(f'PRAGMA user_version={int(number)}')
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
            applied.append(number)
            if log:
                log(f'Migration v{number} applied: {description}' + (f' (backup: {saved})' if saved else ''))
        return applied
    finally:
        conn.close()


def rollback(path, to_version, use_down=False):
    """Return the DB to ``to_version``.

    Default: restore the automatic backup taken before ``to_version + 1``.
    The current file is backed up first, so a rollback can itself be undone.
    ``use_down=True`` runs reversible down-migrations instead (keeps newer
    messages but drops columns/tables introduced later).
    """
    path = Path(path)
    current = inspect(path)['version']
    if to_version >= current:
        return f'Nothing to do: schema is v{current}.'
    safety = backup(path, f'pre-rollback-v{current}')
    if use_down:
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            for number in range(current, to_version, -1):
                down = MIGRATIONS[number][2]
                if down is None:
                    raise RuntimeError(f'Migration v{number} is not reversible')
                conn.execute('BEGIN IMMEDIATE')
                try:
                    down(conn)
                    conn.execute('DELETE FROM schema_migrations WHERE version=?', (number,))
                    conn.execute(f'PRAGMA user_version={int(number - 1)}')
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
        finally:
            conn.close()
        return f'Rolled back with down-migrations to v{to_version}. Safety backup: {safety}'
    candidates = sorted((path.parent / 'backups').glob(f'{path.stem}-*-before-v{to_version + 1}.db'))
    if not candidates:
        raise FileNotFoundError(f'No backup taken before v{to_version + 1}; use --down')
    source = candidates[-1]
    for suffix in ('-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    shutil.copy2(source, path)
    return f'Restored {source.name}. Safety backup of the newer state: {safety}'


def main(argv=None):
    import os
    os.environ.setdefault('CHELSY_APPDATA', '1')
    from core import DB_PATH
    parser = argparse.ArgumentParser(description='Chelsy AI SQLite migrations')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('migrate')
    sub.add_parser('backup')
    rb = sub.add_parser('rollback')
    rb.add_argument('version', type=int)
    rb.add_argument('--down', action='store_true', help='use down-migrations instead of backup restore')
    args = parser.parse_args(argv)
    if args.cmd == 'status':
        print(json.dumps({'database': str(DB_PATH), **inspect(DB_PATH), 'latest': SCHEMA_VERSION},
                         ensure_ascii=False, indent=2))
    elif args.cmd == 'migrate':
        print(migrate(DB_PATH, log=print) or 'Up to date')
    elif args.cmd == 'backup':
        print(backup(DB_PATH) or 'No database yet')
    else:
        print(rollback(DB_PATH, args.version, use_down=args.down))


if __name__ == '__main__':
    sys.exit(main())
