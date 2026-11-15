"""Chelsy AI 0.9.3: storage core — conversations, memory, legacy research crawler.

Standard library only. Schema changes live in migrations.py (versioned, with backups).
"""
from __future__ import annotations

import hashlib
import hmac
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler, urlopen
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parent
# Installation directory may be read-only: store user data in LocalAppData.
DATA = (Path(os.environ['LOCALAPPDATA']) / 'ChelsyAI' / 'data') if os.name == 'nt' and (getattr(__import__('sys'), 'frozen', False) or os.environ.get('CHELSY_APPDATA') == '1') and os.environ.get('LOCALAPPDATA') else ROOT / 'data'
DB_PATH = DATA / 'chelsy.db'
USERS_PATH = DATA / 'users.json'
MODEL = os.environ.get('CHELSY_MODEL', 'llama3.2:1b')
VERSION = '0.9.3'
USER_AGENT = 'ChelsyAIResearchBot/0.9.3 (private research assistant; respects robots.txt)'
MAX_BYTES = 600_000
MAX_TEXT = 24_000
DB_LOCK = threading.RLock()
CRAWL_LOCK = threading.Lock()

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

# Disable environment proxy use; redirects are disabled to prevent a public URL
# from redirecting into LAN/localhost. This is still a personal beta, not a hardened crawler.
OPENER = build_opener(ProxyHandler({}), NoRedirect())

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.content = []
        self.links = []
        self.title = []
        self.in_title = False
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'svg', 'noscript', 'header', 'footer', 'nav'}:
            self.skip_depth += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'a' and not self.skip_depth:
            href = dict(attrs).get('href')
            if href: self.links.append(href)
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'svg', 'noscript', 'header', 'footer', 'nav'}:
            self.skip_depth = max(0, self.skip_depth - 1)
        if tag == 'title':
            self.in_title = False
    def handle_data(self, data):
        s = data.strip()
        if self.in_title and s: self.title.append(s)
        if not self.skip_depth and s: self.content.append(s)


def normalized_url(value, base=None):
    try:
        url = urldefrag(urljoin(base, value) if base else value)[0]
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 80, 443): return None
        if parsed.path.lower().endswith(('.zip', '.pdf', '.png', '.gif', '.jpg', '.jpeg', '.svg', '.css', '.js', '.mp4', '.xml')):
            return None
        return url if len(url) < 800 else None
    except (ValueError, TypeError):
        return None


def public_url(url):
    url = normalized_url(url)
    if not url: return False
    p = urlparse(url)
    host = p.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        return False
    try:
        ips = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return bool(ips) and all(ipaddress.ip_address(x[4][0]).is_global for x in ips)
    except (OSError, ValueError):
        return False


_MIGRATED = set()
_MIGRATE_LOCK = threading.Lock()


@contextmanager
def db():
    """Open an SQLite database for one operation; always release Windows file locks.

    The first open of a database file applies pending versioned migrations
    (with an automatic backup when data already exists).
    """
    DATA.mkdir(parents=True, exist_ok=True)
    path = DB_PATH
    key = str(path)
    if key not in _MIGRATED or not path.exists():
        with _MIGRATE_LOCK:
            if key not in _MIGRATED or not path.exists():
                import migrations
                migrations.migrate(path)
                _MIGRATED.add(key)
    connection = sqlite3.connect(path, timeout=15)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=15000')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()


def add_seed(url):
    url = normalized_url(url)
    if not url or not public_url(url):
        raise ValueError('Нужен публичный HTTP/HTTPS URL без нестандартного порта.')
    with DB_LOCK, db() as conn:
        conn.execute('INSERT OR IGNORE INTO queue(url) VALUES (?)', (url,))
    return url


def stats():
    with DB_LOCK, db() as conn:
        pages = conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        pending = conn.execute('SELECT COUNT(*) FROM queue WHERE done=0').fetchone()[0]
    return pages, pending


def robots_permission(url, cache):
    p = urlparse(url)
    origin = f'{p.scheme}://{p.netloc}'
    if origin not in cache:
        if not public_url(origin + '/robots.txt'):
            cache[origin] = None
        else:
            robots = RobotFileParser()
            robots.set_url(origin + '/robots.txt')
            try:
                req = Request(origin + '/robots.txt', headers={'User-Agent': USER_AGENT})
                with OPENER.open(req, timeout=8) as response:
                    payload = response.read(100_001)
                    if len(payload) > 100_000: raise ValueError('robots.txt too large')
                robots.parse(payload.decode('utf-8', errors='replace').splitlines())
                cache[origin] = robots
            except Exception:
                cache[origin] = None
    rp = cache[origin]
    if rp is None: return False, 3.0
    delay = rp.crawl_delay(USER_AGENT) or rp.crawl_delay('*') or 3
    return rp.can_fetch(USER_AGENT, url), max(3.0, min(60.0, float(delay)))


def crawl(limit=5, log=None):
    if not CRAWL_LOCK.acquire(blocking=False):
        return ['Уже выполняется другое исследование.']
    events = []
    def note(s):
        events.append(s)
        if log: log(s)
    try:
        cache, visited, success = {}, {}, 0
        for _ in range(max(1, min(int(limit), 15))):
            with DB_LOCK, db() as conn:
                row = conn.execute('SELECT url FROM queue WHERE done=0 ORDER BY rowid LIMIT 1').fetchone()
                if not row: break
                url = row[0]
                conn.execute('UPDATE queue SET done=1 WHERE url=?', (url,))
            if not public_url(url):
                note('Пропуск небезопасного адреса: ' + url)
                continue
            allowed, delay = robots_permission(url, cache)
            if not allowed:
                note('Пропуск: robots.txt недоступен или запрещает обход: ' + url)
                continue
            origin = urlparse(url).netloc
            wait = delay - (time.monotonic() - visited.get(origin, -1e9))
            if wait > 0: time.sleep(wait)
            try:
                req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'})
                with OPENER.open(req, timeout=12) as resp:
                    if resp.headers.get_content_type() != 'text/html':
                        note('Пропуск не-HTML: ' + url); continue
                    content = resp.read(MAX_BYTES + 1)
                    if len(content) > MAX_BYTES:
                        note('Пропуск большой страницы: ' + url); continue
                    encoding = resp.headers.get_content_charset() or 'utf-8'
                parser = PageParser()
                parser.feed(content.decode(encoding, errors='replace'))
                body = re.sub(r'\s+', ' ', html.unescape(' '.join(parser.content)))[:MAX_TEXT]
                if len(body) < 120:
                    note('Мало текста: ' + url); continue
                title = ' '.join(parser.title)[:250] or url
                with DB_LOCK, db() as conn:
                    conn.execute('INSERT OR REPLACE INTO pages(url,title,body,updated) VALUES (?,?,?,?)',
                                 (url,title,body,datetime.now(timezone.utc).isoformat()))
                    for href in parser.links[:100]:
                        link = normalized_url(href, url)
                        if link and urlparse(link).hostname == urlparse(url).hostname:
                            conn.execute('INSERT OR IGNORE INTO queue(url) VALUES (?)', (link,))
                success += 1
                note('Сохранено: ' + title[:90] + ' — ' + url)
            except Exception as e:
                note('Ошибка чтения (' + type(e).__name__ + '): ' + url)
            finally:
                visited[origin] = time.monotonic()
        note(f'Завершено. Сохранено страниц: {success}.')
        return events
    finally:
        CRAWL_LOCK.release()


def search(question, top=4):
    words = list(dict.fromkeys(re.findall(r'[^\W_]{3,}', question.lower(), flags=re.UNICODE)))[:15]
    if not words: return []
    with DB_LOCK, db() as conn:
        pages = conn.execute('SELECT url,title,body FROM pages').fetchall()
    scored = []
    for url, title, body in pages:
        lower_title, low = title.lower(), body.lower()
        matches = [w for w in words if w in lower_title or w in low]
        if not matches: continue
        score = sum(3 if w in lower_title else 1 for w in matches)
        positions = [low.find(w) for w in matches if low.find(w) != -1]
        pos = min(positions) if positions else 0
        excerpt = body[max(0, pos - 170):pos + 850]
        scored.append((score,url,title,excerpt))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:top]


# ---------------------------------------------------------------- conversations
DEFAULT_TITLE = 'Новый чат'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _conversation_row(row):
    cid, title, created, updated, favorite = row
    return {'id': cid, 'title': title, 'created': created, 'updated': updated, 'favorite': bool(favorite)}


def create_conversation(username, title=DEFAULT_TITLE, conversation_id=None):
    if not username:
        raise ValueError('Invalid user')
    cid = str(uuid.UUID(conversation_id)) if conversation_id else str(uuid.uuid4())
    title = re.sub(r'\s+', ' ', (title or DEFAULT_TITLE)).strip()[:120] or DEFAULT_TITLE
    stamp = _now()
    with DB_LOCK, db() as conn:
        owner = conn.execute('SELECT username FROM conversations WHERE id=?', (cid,)).fetchone()
        if owner and owner[0] != username:
            raise PermissionError('Conversation belongs to another account')
        conn.execute('INSERT OR IGNORE INTO conversations(id,username,title,created,updated) VALUES(?,?,?,?,?)',
                     (cid, username, title, stamp, stamp))
    return cid


def owns_conversation(username, conversation_id):
    if not conversation_id:
        return False
    with DB_LOCK, db() as conn:
        return bool(conn.execute('SELECT 1 FROM conversations WHERE id=? AND username=? AND deleted=0',
                                 (conversation_id, username)).fetchone())


def latest_conversation(username, create=True):
    with DB_LOCK, db() as conn:
        row = conn.execute('SELECT id FROM conversations WHERE username=? AND deleted=0 '
                           'ORDER BY updated DESC LIMIT 1', (username,)).fetchone()
    if row:
        return row[0]
    return create_conversation(username) if create else None


def list_conversations(username, query='', favorites_only=False, limit=60):
    sql = 'SELECT id,title,created,updated,favorite FROM conversations WHERE username=? AND deleted=0'
    args = [username]
    if favorites_only:
        sql += ' AND favorite=1'
    query = (query or '').strip()[:100]
    if query:
        like = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        sql += (" AND (title LIKE ? ESCAPE '\\' OR id IN (SELECT conversation_id FROM messages "
                "WHERE username=? AND deleted=0 AND content LIKE ? ESCAPE '\\'))")
        args += [like, username, like]
    sql += ' ORDER BY updated DESC LIMIT ?'
    args.append(max(1, min(int(limit), 500)))
    with DB_LOCK, db() as conn:
        return [_conversation_row(r) for r in conn.execute(sql, args).fetchall()]


def rename_conversation(username, conversation_id, title):
    title = re.sub(r'\s+', ' ', title or '').strip()[:120]
    if not title:
        raise ValueError('Название не может быть пустым')
    with DB_LOCK, db() as conn:
        conn.execute('UPDATE conversations SET title=?, cloud_synced=0 WHERE id=? AND username=?',
                     (title, conversation_id, username))


def set_favorite(username, conversation_id, favorite=True):
    with DB_LOCK, db() as conn:
        conn.execute('UPDATE conversations SET favorite=?, cloud_synced=0 WHERE id=? AND username=?',
                     (1 if favorite else 0, conversation_id, username))


def delete_conversation(username, conversation_id):
    """Soft delete: rows stay in SQLite (and the automatic backups)."""
    with DB_LOCK, db() as conn:
        conn.execute('UPDATE conversations SET deleted=1, cloud_synced=0, updated=? WHERE id=? AND username=?',
                     (_now(), conversation_id, username))


def conversation_messages(username, conversation_id, limit=400):
    with DB_LOCK, db() as conn:
        rows = conn.execute(
            'SELECT id,role,content,created,meta,rating FROM messages WHERE username=? AND conversation_id=? '
            'AND deleted=0 ORDER BY id DESC LIMIT ?', (username, conversation_id, int(limit))).fetchall()
    result = []
    for mid, role, content, created, meta, rating in reversed(rows):
        try:
            parsed = json.loads(meta) if meta else {}
        except ValueError:
            parsed = {}
        result.append({'id': mid, 'role': role, 'content': content, 'created': created,
                       'meta': parsed, 'rating': rating})
    return result


def history(username, conversation_id=None, limit=30):
    """Messages of a conversation (default: the most recently used one)."""
    conversation_id = conversation_id or latest_conversation(username, create=False)
    if not conversation_id:
        return []
    return [{'role': m['role'], 'content': m['content']}
            for m in conversation_messages(username, conversation_id, limit)]


def save_message(username, role, content, conversation_id=None, meta=None, client_id=None):
    if not username or role not in ('user', 'assistant'):
        raise ValueError('Invalid message')
    if not isinstance(content, str):
        raise ValueError('Invalid message')
    conversation_id = conversation_id or latest_conversation(username)
    client_id = str(uuid.UUID(client_id)) if client_id else str(uuid.uuid4())
    stamp = _now()
    with DB_LOCK, db() as conn:
        owner = conn.execute('SELECT username,title FROM conversations WHERE id=?', (conversation_id,)).fetchone()
        if owner is None:
            conn.execute('INSERT INTO conversations(id,username,title,created,updated) VALUES(?,?,?,?,?)',
                         (conversation_id, username, DEFAULT_TITLE, stamp, stamp))
            owner = (username, DEFAULT_TITLE)
        if owner[0] != username:
            raise PermissionError('Conversation belongs to another account')
        existing = conn.execute('SELECT id FROM messages WHERE client_id=?', (client_id,)).fetchone()
        if existing:
            return existing[0]  # idempotent retry from a client
        cur = conn.execute(
            'INSERT INTO messages(username,role,content,created,conversation_id,client_id,legacy_key,meta) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (username, role, content, stamp, conversation_id, client_id, 'msg:' + client_id,
             json.dumps(meta, ensure_ascii=False) if meta else ''))
        title_update = ''
        if role == 'user' and owner[1] == DEFAULT_TITLE:
            title_update = re.sub(r'\s+', ' ', content).strip()[:60] or DEFAULT_TITLE
        conn.execute('UPDATE conversations SET updated=?, cloud_synced=0' + (', title=?' if title_update else '') +
                     ' WHERE id=?', (stamp, title_update, conversation_id) if title_update else (stamp, conversation_id))
        return cur.lastrowid


def truncate_from(username, conversation_id, message_id):
    """Soft-delete a message and everything after it (edit / regenerate)."""
    with DB_LOCK, db() as conn:
        conn.execute('UPDATE messages SET deleted=1, cloud_synced=0 WHERE username=? AND conversation_id=? AND id>=?',
                     (username, conversation_id, int(message_id)))


def rate_message(username, message_id, rating):
    rating = 1 if rating > 0 else -1 if rating < 0 else 0
    with DB_LOCK, db() as conn:
        conn.execute('UPDATE messages SET rating=? WHERE id=? AND username=?', (rating, int(message_id), username))


def clear_history(username, conversation_id=None):
    """Remove the user's chats from the active view (soft delete, recoverable from SQLite)."""
    with DB_LOCK, db() as conn:
        if conversation_id:
            conn.execute('UPDATE messages SET deleted=1, cloud_synced=0 WHERE username=? AND conversation_id=?',
                         (username, conversation_id))
        else:
            conn.execute('UPDATE messages SET deleted=1, cloud_synced=0 WHERE username=?', (username,))
            conn.execute('UPDATE conversations SET deleted=1, cloud_synced=0 WHERE username=?', (username,))


def get_setting(username, setting, default=None):
    with DB_LOCK, db() as conn:
        row = conn.execute('SELECT value FROM user_settings WHERE username=? AND setting=?',
                           (username, setting)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def set_setting(username, setting, value):
    with DB_LOCK, db() as conn:
        conn.execute('INSERT INTO user_settings(username,setting,value) VALUES(?,?,?) '
                     'ON CONFLICT(username,setting) DO UPDATE SET value=excluded.value',
                     (username, setting, json.dumps(value, ensure_ascii=False)))


def ollama_available():
    from ollama_client import available
    return available()


def store_personal_fact(username, memory_key, value):
    """Store only facts explicitly provided by the account owner."""
    if not username or not memory_key or not value:
        raise ValueError('Memory requires a user, key, and value')
    stamp = _now()
    with DB_LOCK, db() as conn:
        conn.execute('INSERT INTO personal_memory(username,memory_key,value,created,updated) VALUES (?,?,?,?,?) '
                     'ON CONFLICT(username,memory_key) DO UPDATE SET value=excluded.value, created=excluded.created, '
                     'updated=excluded.updated',
                     (username, memory_key, value[:2000], stamp, stamp))
        conn.execute('INSERT OR IGNORE INTO pending_memory(username,memory_key) VALUES(?,?)', (username, memory_key))


def personal_facts(username):
    with DB_LOCK, db() as conn:
        return dict(conn.execute('SELECT memory_key,value FROM personal_memory WHERE username=? ORDER BY created DESC LIMIT 100', (username,)).fetchall())


def delete_personal_fact(username, memory_key):
    with DB_LOCK, db() as conn:
        conn.execute('DELETE FROM personal_memory WHERE username=? AND memory_key=?', (username, memory_key))
        conn.execute('DELETE FROM pending_memory WHERE username=? AND memory_key=?', (username, memory_key))


def recent_personal_facts(username, max_items=15):
    return list(personal_facts(username).items())[:max_items]


def extract_explicit_memory(message):
    """Return simple explicit user memories; never extract from retrieved web pages."""
    m = re.search(r'(?:моего|мою|мой)\s+тестов(?:ого|ую|ый)\s+робот(?:а)?\s+зовут\s+([^.!?\n]{1,80})', message, re.I)
    if m:
        return ('тестовый робот', m.group(1).strip(' \"«»'))
    m = re.search(r'запомни\s*[:：]\s*([^=\n:：]{2,75})\s*[=：:]\s*(.{1,500})', message, re.I | re.S)
    if m:
        return (m.group(1).strip().lower(), m.group(2).strip())
    m = re.search(r'запомни\s*[:：]\s*(.{3,500})', message, re.I|re.S)
    if m:
        raw = m.group(1).strip()
        return ('заметка: ' + re.sub(r'\s+', ' ', raw[:75]).lower(), raw)
    m = re.search(r'\bremember\s*:\s*([^=\n:]{2,75})\s*=\s*(.{1,500})', message, re.I | re.S)
    if m:
        return (m.group(1).strip().lower(), m.group(2).strip())
    return None


def direct_memory_answer(question, facts):
    """Return precise answers to a few supported memory questions without hallucination."""
    q = question.lower()
    if ('робот' in q and ('как зовут' in q or 'имя' in q)) and facts.get('тестовый робот'):
        return 'Твоего тестового робота зовут ' + facts['тестовый робот'] + '.'
    return None


def answer(question, username, model=None, conversation_id=None, web_enabled=True):
    """Blocking compatibility wrapper (0.9.2 API): returns (history, '')."""
    from intelligence import ENGINE
    conversation_id = conversation_id or latest_conversation(username)
    for _ in ENGINE.respond(question, username, conversation_id=conversation_id,
                            model=model, web_enabled=web_enabled):
        pass
    return history(username, conversation_id), ''


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 240_000)
    return salt.hex() + ':' + digest.hex()


def check_password(password, record):
    try:
        salt_hex, hash_hex = record.split(':', 1)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), 240_000)
        return hmac.compare_digest(expected, actual)
    except (ValueError, AttributeError):
        return False


def create_user(username, password):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{3,32}', username):
        raise ValueError('Логин: 3–32 символа (латинские буквы, цифры, _ или -).')
    if len(password) < 12:
        raise ValueError('Пароль должен содержать не менее 12 символов.')
    DATA.mkdir(parents=True, exist_ok=True)
    users = json.loads(USERS_PATH.read_text(encoding='utf-8')) if USERS_PATH.exists() else {}
    if username in users: raise ValueError('Такой пользователь уже существует.')
    users[username] = password_hash(password)
    USERS_PATH.write_text(json.dumps(users, indent=2), encoding='utf-8')


def authenticate(username, password):
    if not USERS_PATH.exists(): return False
    try:
        users = json.loads(USERS_PATH.read_text(encoding='utf-8'))
        record = users.get(username, '')
        return check_password(password, record) if record else False
    except (ValueError, OSError):
        return False
