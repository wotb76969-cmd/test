"""Local data storage of the Windows Client (offline copy of conversations).

File: %LOCALAPPDATA%\\ChelsyAI\\client_cache.db (inside the user's own profile).
Separate per account; removed for that account on logout. Only what the
user has already seen is stored — no passwords or tokens.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

MAX_CONVERSATIONS = 200
MAX_MESSAGES = 500
_LOCK = threading.Lock()


def path():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChelsyAI' / 'client_cache.db'


def _connect():
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS conversations (account TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
        favorite INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL DEFAULT '', PRIMARY KEY(account, id));
    CREATE TABLE IF NOT EXISTS messages (account TEXT NOT NULL, conversation_id TEXT NOT NULL, position INTEGER NOT NULL,
        id INTEGER, role TEXT NOT NULL, content TEXT NOT NULL, created TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(account, conversation_id, position));
    ''')
    return conn


def _account(account):
    account = (account or '').strip().lower()
    if not account:
        raise ValueError('account required')
    return account


def save_conversations(account, items, replace=True):
    account = _account(account)
    with _LOCK, closing(_connect()) as conn, conn:
        if replace:
            conn.execute('DELETE FROM conversations WHERE account=?', (account,))
        conn.executemany('INSERT OR REPLACE INTO conversations(account,id,title,favorite,updated) VALUES(?,?,?,?,?)',
                         [(account, c['id'], c.get('title', ''), 1 if c.get('favorite') else 0, c.get('updated', ''))
                          for c in items[:MAX_CONVERSATIONS]])


def load_conversations(account, query=''):
    account = _account(account)
    sql = 'SELECT id,title,favorite,updated FROM conversations WHERE account=?'
    args = [account]
    if query:
        sql += ' AND title LIKE ?'
        args.append('%' + query.replace('%', '') + '%')
    with _LOCK, closing(_connect()) as conn:
        rows = conn.execute(sql + ' ORDER BY updated DESC', args).fetchall()
    return [{'id': r[0], 'title': r[1], 'favorite': bool(r[2]), 'updated': r[3]} for r in rows]


def save_messages(account, conversation_id, messages):
    account = _account(account)
    tail = messages[-MAX_MESSAGES:]
    with _LOCK, closing(_connect()) as conn, conn:
        conn.execute('DELETE FROM messages WHERE account=? AND conversation_id=?', (account, conversation_id))
        conn.executemany('INSERT INTO messages(account,conversation_id,position,id,role,content,created) VALUES(?,?,?,?,?,?,?)',
                         [(account, conversation_id, i, m.get('id'), m.get('role', 'assistant'), m.get('content', ''),
                           m.get('created', '')) for i, m in enumerate(tail)])


def load_messages(account, conversation_id):
    account = _account(account)
    with _LOCK, closing(_connect()) as conn:
        rows = conn.execute('SELECT id,role,content,created FROM messages WHERE account=? AND conversation_id=? '
                            'ORDER BY position', (account, conversation_id)).fetchall()
    return [{'id': r[0], 'role': r[1], 'content': r[2], 'created': r[3]} for r in rows]


def clear(account):
    account = _account(account)
    with _LOCK, closing(_connect()) as conn, conn:
        conn.execute('DELETE FROM messages WHERE account=?', (account,))
        conn.execute('DELETE FROM conversations WHERE account=?', (account,))
