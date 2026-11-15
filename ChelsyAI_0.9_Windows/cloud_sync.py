"""Chelsy cloud sync v2 (0.9.3): conversations, messages and personal memory.

Security model: every PostgREST request carries the END USER's access token
and the public (publishable) project key. Supabase RLS is the authorization
boundary; the host never uses a service-role key and never impersonates.

Data safety:
* pull never deletes or overwrites local messages; imports are idempotent
  (``cloud_imported`` + stable ``legacy_id`` / ``client_id``);
* only rows with ``cloud_synced=0`` are uploaded, so unsynced local changes
  survive offline periods and are sent on the next successful sync;
* conflicts: messages are append-only (no conflict); conversation titles /
  favourites and memories use last-writer-wins by ``updated_at`` and a local
  pending edit always wins until it has been uploaded;
* works with the 0.9.2 cloud schema (001/002) in "legacy" mode and enables
  conversations/soft-delete once migration 003 is applied.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from core import DB_LOCK, db, latest_conversation  # noqa: F401  (DB_PATH/DATA re-exported for tools)
from core import DB_PATH, DATA  # noqa: F401
from supabase_auth import config, storage_key


class SyncError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


PAGE = 500
MAX_ROWS = 20_000
_schema_cache = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _call(table, token, *, method='GET', params=None, rows=None, prefer=None):
    cfg = config()
    if cfg is None:
        raise SyncError('Supabase not configured')
    url, key = cfg
    suffix = ('?' + urlencode(params, doseq=True)) if params else ''
    headers = {'apikey': key, 'Authorization': 'Bearer ' + token,
               'Accept': 'application/json', 'Cache-Control': 'no-store'}
    raw = None
    if rows is not None:
        raw = json.dumps(rows, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if prefer:
        headers['Prefer'] = prefer
    request = Request(url + '/rest/v1/' + table + suffix, headers=headers, data=raw, method=method)
    try:
        with build_opener().open(request, timeout=25) as response:
            body = response.read()
            return json.loads(body) if body else []
    except HTTPError as exc:
        raise SyncError('Cloud sync HTTP %d: check schema, RLS and account session' % exc.code, exc.code) from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise SyncError('Cloud service unavailable or returned invalid data') from exc


def _init(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS cloud_imported (username TEXT NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,cloud_id))')
    conn.execute('CREATE TABLE IF NOT EXISTS cloud_origin (username TEXT NOT NULL, local_id INTEGER NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,local_id))')


def schema(uid, token):
    """'v2' when cloud migration 003 (conversations) exists, else 'legacy'."""
    cached = _schema_cache.get(uid)
    if cached and time.monotonic() - cached[1] < 600:
        return cached[0]
    try:
        _call('chelsy_ai_conversations', token, params={'select': 'id', 'limit': '1'})
        mode = 'v2'
    except SyncError as exc:
        if exc.status in (400, 404):
            mode = 'legacy'
        else:
            raise
    _schema_cache[uid] = (mode, time.monotonic())
    return mode


def _legacy_conversation(conn, username):
    row = conn.execute("SELECT value FROM user_settings WHERE username=? AND setting='cloud_legacy_conversation'",
                       (username,)).fetchone()
    if row:
        cid = json.loads(row[0])
        if conn.execute('SELECT 1 FROM conversations WHERE id=? AND username=?', (cid, username)).fetchone():
            return cid
    cid = str(uuid.uuid4())
    stamp = _now()
    conn.execute('INSERT INTO conversations(id,username,title,created,updated,cloud_synced) VALUES(?,?,?,?,?,0)',
                 (cid, username, 'История из облака', stamp, stamp))
    conn.execute("INSERT OR REPLACE INTO user_settings(username,setting,value) VALUES(?,'cloud_legacy_conversation',?)",
                 (username, json.dumps(cid)))
    return cid


def _ensure_conversation(conn, username, cid, title='Диалог с другого устройства'):
    try:
        cid = str(uuid.UUID(str(cid)))
    except ValueError:
        return _legacy_conversation(conn, username)
    owner = conn.execute('SELECT username FROM conversations WHERE id=?', (cid,)).fetchone()
    if owner is None:
        stamp = _now()
        conn.execute('INSERT INTO conversations(id,username,title,created,updated,cloud_synced) VALUES(?,?,?,?,?,1)',
                     (cid, username, title, stamp, stamp))
        return cid
    return cid if owner[0] == username else _legacy_conversation(conn, username)


# ------------------------------------------------------------------ pull
def pull_conversations(uid, token):
    username = storage_key(uid)
    rows = _call('chelsy_ai_conversations', token, params={
        'select': 'id,title,favorite,deleted_at,created_at,updated_at', 'user_id': 'eq.' + uid,
        'order': 'updated_at.desc', 'limit': '1000'})
    if not isinstance(rows, list):
        raise SyncError('Unexpected conversations response')
    changed = 0
    with DB_LOCK, db() as conn:
        for row in rows:
            try:
                cid = str(uuid.UUID(str(row.get('id'))))
            except ValueError:
                continue
            title = str(row.get('title') or 'Диалог')[:120]
            favorite = 1 if row.get('favorite') else 0
            deleted = 1 if row.get('deleted_at') else 0
            updated = str(row.get('updated_at') or _now())
            local = conn.execute('SELECT username, cloud_synced, updated FROM conversations WHERE id=?', (cid,)).fetchone()
            if local is None:
                conn.execute('INSERT INTO conversations(id,username,title,created,updated,favorite,deleted,cloud_synced) '
                             'VALUES(?,?,?,?,?,?,?,1)', (cid, username, title, str(row.get('created_at') or updated),
                                                         updated, favorite, deleted))
                changed += 1
            elif local[0] == username and local[1] == 1 and updated > (local[2] or ''):
                conn.execute('UPDATE conversations SET title=?, favorite=?, deleted=?, updated=? WHERE id=?',
                             (title, favorite, deleted, updated, cid))
                changed += 1
    return changed


def pull(uid, token):
    """Import cloud messages into local conversations, idempotently."""
    username = storage_key(uid)
    mode = schema(uid, token)
    if mode == 'v2':
        pull_conversations(uid, token)
    fields = 'id,legacy_id,role,content,created_at' + (',conversation_id,deleted_at' if mode == 'v2' else '')
    imported = 0
    for offset in range(0, MAX_ROWS, PAGE):
        rows = _call('chelsy_ai_messages', token, params={
            'select': fields, 'user_id': 'eq.' + uid, 'order': 'created_at.asc,id.asc',
            'limit': str(PAGE), 'offset': str(offset)})
        if not isinstance(rows, list):
            raise SyncError('Unexpected cloud history response')
        with DB_LOCK, db() as conn:
            _init(conn)
            for entry in rows:
                cloud_id, legacy_id = entry.get('id'), entry.get('legacy_id')
                role, content, created = entry.get('role'), entry.get('content'), entry.get('created_at')
                if not isinstance(cloud_id, str) or role not in ('user', 'assistant') or not isinstance(content, str):
                    continue
                if len(content) > 100_000:
                    continue
                if conn.execute('SELECT 1 FROM cloud_imported WHERE username=? AND cloud_id=?', (username, cloud_id)).fetchone():
                    continue
                local = None
                if isinstance(legacy_id, str):
                    local = conn.execute('SELECT id FROM messages WHERE username=? AND legacy_key=?',
                                         (username, legacy_id)).fetchone()
                    if not local and legacy_id.startswith('host:'):
                        # 0.9.2 rows uploaded before the legacy_key column existed.
                        try:
                            local = conn.execute('SELECT id FROM messages WHERE id=? AND username=? AND legacy_key=?',
                                                 (int(legacy_id[5:]), username, legacy_id)).fetchone()
                        except ValueError:
                            local = None
                if local is None:
                    client_id = None
                    if isinstance(legacy_id, str) and legacy_id.startswith('msg:'):
                        try:
                            client_id = str(uuid.UUID(legacy_id[4:]))
                        except ValueError:
                            client_id = None
                    if client_id and conn.execute('SELECT 1 FROM messages WHERE client_id=?', (client_id,)).fetchone():
                        client_id = None    # same id under another account — never merge across accounts
                    remote_conv = entry.get('conversation_id')
                    cid = (_ensure_conversation(conn, username, remote_conv) if remote_conv
                           else _legacy_conversation(conn, username))
                    cur = conn.execute(
                        'INSERT INTO messages(username,role,content,created,conversation_id,client_id,legacy_key,cloud_synced,deleted) '
                        'VALUES(?,?,?,?,?,?,?,1,?)',
                        (username, role, content, created if isinstance(created, str) else '', cid,
                         client_id or str(uuid.uuid4()), legacy_id if isinstance(legacy_id, str) else 'cloud:' + cloud_id,
                         1 if entry.get('deleted_at') else 0))
                    conn.execute('INSERT OR IGNORE INTO cloud_origin(username,local_id,cloud_id) VALUES(?,?,?)',
                                 (username, cur.lastrowid, cloud_id))
                    conn.execute('UPDATE conversations SET updated=MAX(updated, ?) WHERE id=?',
                                 (created if isinstance(created, str) else '', cid))
                    imported += 1
                conn.execute('INSERT OR IGNORE INTO cloud_imported(username,cloud_id) VALUES(?,?)', (username, cloud_id))
        if len(rows) < PAGE:
            break
    return imported


# ------------------------------------------------------------------ push
def push_conversations(uid, token):
    username = storage_key(uid)
    with DB_LOCK, db() as conn:
        rows = conn.execute('SELECT id,title,favorite,deleted,created,updated FROM conversations '
                            'WHERE username=? AND cloud_synced=0', (username,)).fetchall()
    sent = 0
    for start in range(0, len(rows), 50):
        batch = rows[start:start + 50]
        payload = [{'id': cid, 'user_id': uid, 'title': title, 'favorite': bool(fav),
                    'deleted_at': updated if deleted else None, 'created_at': created, 'updated_at': updated}
                   for cid, title, fav, deleted, created, updated in batch]
        _call('chelsy_ai_conversations', token, method='POST', params={'on_conflict': 'id'}, rows=payload,
              prefer='resolution=merge-duplicates,return=minimal')
        with DB_LOCK, db() as conn:
            for cid, _, _, _, _, updated in batch:
                conn.execute('UPDATE conversations SET cloud_synced=1 WHERE id=? AND updated=?', (cid, updated))
        sent += len(batch)
    return sent


def push(uid, token):
    username = storage_key(uid)
    mode = schema(uid, token)
    if mode == 'v2':
        push_conversations(uid, token)
    with DB_LOCK, db() as conn:
        _init(conn)
        records = conn.execute(
            'SELECT id,role,content,created,legacy_key,conversation_id,deleted FROM messages '
            'WHERE username=? AND cloud_synced=0 AND id NOT IN (SELECT local_id FROM cloud_origin WHERE username=?) '
            'ORDER BY id LIMIT ?', (username, username, MAX_ROWS)).fetchall()
    upload, ids, skipped = [], [], []
    for mid, role, content, created, legacy_key, conv, deleted in records:
        if mode == 'legacy' and deleted:
            skipped.append(mid)     # legacy cloud has no soft delete; do not upload deleted rows
            continue
        row = {'user_id': uid, 'legacy_id': legacy_key or 'host:' + str(mid), 'role': role, 'content': content,
               'created_at': created}
        if mode == 'v2':
            row['conversation_id'] = conv
            row['deleted_at'] = _now() if deleted else None
        upload.append(row)
        ids.append(mid)
    prefer = 'resolution=merge-duplicates,return=minimal' if mode == 'v2' else 'resolution=ignore-duplicates,return=minimal'
    for i in range(0, len(upload), 50):
        _call('chelsy_ai_messages', token, method='POST', params={'on_conflict': 'user_id,legacy_id'},
              rows=upload[i:i + 50], prefer=prefer)
        with DB_LOCK, db() as conn:
            conn.executemany('UPDATE messages SET cloud_synced=1 WHERE id=?', [(x,) for x in ids[i:i + 50]])
    if skipped:
        with DB_LOCK, db() as conn:
            conn.executemany('UPDATE messages SET cloud_synced=1 WHERE id=?', [(x,) for x in skipped])
    return len(upload)


def sync(uid, token):
    """Pull then push; a failed cloud request never deletes local data."""
    received = pull(uid, token)
    sent = push(uid, token)
    return {'received': received, 'considered_for_upload': sent, 'cloud': 'ok',
            'schema': _schema_cache.get(uid, ('legacy',))[0]}


# ------------------------------------------------------------------ memory
def save_memory(uid, token, key, value):
    if not (isinstance(key, str) and isinstance(value, str) and 1 <= len(key) <= 75 and 1 <= len(value) <= 2000):
        raise ValueError('Invalid memory')
    _call('chelsy_ai_memories', token, method='POST', params={'on_conflict': 'user_id,memory_key'},
          rows=[{'user_id': uid, 'memory_key': key, 'value': value, 'updated_at': _now()}],
          prefer='resolution=merge-duplicates,return=minimal')
    username = storage_key(uid)
    with DB_LOCK, db() as conn:
        current = conn.execute('SELECT value FROM personal_memory WHERE username=? AND memory_key=?', (username, key)).fetchone()
        if current and current[0] == value:
            conn.execute('DELETE FROM pending_memory WHERE username=? AND memory_key=?', (username, key))


def pull_memories(uid, token):
    rows = _call('chelsy_ai_memories', token, params={'select': 'memory_key,value,updated_at', 'user_id': 'eq.' + uid,
                                                       'limit': '500'})
    if not isinstance(rows, list):
        raise SyncError('Invalid memories response')
    username = storage_key(uid)
    count = 0
    with DB_LOCK, db() as conn:
        for row in rows:
            key, value = row.get('memory_key'), row.get('value')
            if not (isinstance(key, str) and isinstance(value, str)):
                continue
            if conn.execute('SELECT 1 FROM pending_memory WHERE username=? AND memory_key=?', (username, key)).fetchone():
                continue  # local offline edit has priority until uploaded
            remote_updated = str(row.get('updated_at') or '')
            before = conn.execute('SELECT value, updated FROM personal_memory WHERE username=? AND memory_key=?',
                                  (username, key)).fetchone()
            if before is None or (before[0] != value and (not remote_updated or remote_updated >= (before[1] or ''))):
                stamp = remote_updated or _now()
                conn.execute('INSERT INTO personal_memory(username,memory_key,value,created,updated) VALUES(?,?,?,?,?) '
                             'ON CONFLICT(username,memory_key) DO UPDATE SET value=excluded.value,created=excluded.created,'
                             'updated=excluded.updated', (username, key, value, stamp, stamp))
                count += 1
    return count


def push_memories(uid, token):
    """Upload memories edited locally (pending) in one batch."""
    username = storage_key(uid)
    with DB_LOCK, db() as conn:
        dirty = conn.execute('SELECT m.memory_key, m.value, m.updated FROM personal_memory m JOIN pending_memory p '
                             'ON p.username=m.username AND p.memory_key=m.memory_key WHERE m.username=?',
                             (username,)).fetchall()
        orphan = conn.execute('SELECT memory_key FROM pending_memory WHERE username=? AND memory_key NOT IN '
                              '(SELECT memory_key FROM personal_memory WHERE username=?)', (username, username)).fetchall()
        for (key,) in orphan:
            conn.execute('DELETE FROM pending_memory WHERE username=? AND memory_key=?', (username, key))
    if not dirty:
        return 0
    rows = [{'user_id': uid, 'memory_key': k, 'value': v, 'updated_at': u or _now()} for k, v, u in dirty]
    for i in range(0, len(rows), 50):
        _call('chelsy_ai_memories', token, method='POST', params={'on_conflict': 'user_id,memory_key'},
              rows=rows[i:i + 50], prefer='resolution=merge-duplicates,return=minimal')
    with DB_LOCK, db() as conn:
        for key, value, _ in dirty:
            current = conn.execute('SELECT value FROM personal_memory WHERE username=? AND memory_key=?',
                                   (username, key)).fetchone()
            if current and current[0] == value:   # an edit made during the request stays pending
                conn.execute('DELETE FROM pending_memory WHERE username=? AND memory_key=?', (username, key))
    return len(rows)


def delete_cloud_memory(uid, token, key):
    _call('chelsy_ai_memories', token, method='DELETE', params={'user_id': 'eq.' + uid, 'memory_key': 'eq.' + key})


def delete_cloud_history(uid, token):
    """User-requested removal of their own cloud history (soft delete on v2 schema)."""
    if schema(uid, token) == 'v2':
        _call('chelsy_ai_messages', token, method='PATCH', params={'user_id': 'eq.' + uid},
              rows={'deleted_at': _now()}, prefer='return=minimal')
        _call('chelsy_ai_conversations', token, method='PATCH', params={'user_id': 'eq.' + uid},
              rows={'deleted_at': _now()}, prefer='return=minimal')
    else:
        _call('chelsy_ai_messages', token, method='DELETE', params={'user_id': 'eq.' + uid})
