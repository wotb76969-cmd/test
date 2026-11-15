"""Chelsy AI Host API (0.9.3).

Binds to loopback ONLY. Remote devices reach it through Tailscale Serve
(HTTPS inside your private tailnet). Ollama is never exposed: clients talk
to this API, the API talks to Ollama on 127.0.0.1.

Compatibility: all 0.9.2 endpoints (/api/status, /api/history, /api/chat,
/api/memory, /api/sync) keep working for older Windows/Android clients.
New clients use /api/v2/* (conversations, streaming, stop, research,
knowledge base, settings).
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import core
from core import authenticate, answer, history, personal_facts, stats, ollama_available, crawl, store_personal_fact
from supabase_auth import verify_cached as verify, storage_key, config as sb_config, admin_user_id, AuthError
from cloud_sync import sync as cloud_sync, pull_memories, push_memories, save_memory, SyncError

VERSION = '0.9.3'
PORT = int(os.environ.get('CHELSY_HOST_PORT', '8787'))
MAX_BODY = 12_000
MAX_CHAT_BODY = 70_000
RATE_LIMIT = 12
RATE_PERIOD = 60
RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()
GENERATION_LOCK = threading.Lock()
ACTIVE = {}                       # request_id -> (username, threading.Event)
ACTIVE_LOCK = threading.Lock()
ALLOWED_MODELS = {'llama3.2:1b', 'qwen3:4b'}   # 0.9.2 defaults; installed models are also allowed
UUID_RE = re.compile(r'^[0-9a-fA-F-]{36}$')


def allowed_models():
    import ollama_client
    installed = ollama_client.list_models()
    extra = {m.strip() for m in os.environ.get('CHELSY_ALLOWED_MODELS', '').split(',') if m.strip()}
    if extra:
        return sorted(m for m in installed if m in extra) or sorted(extra)
    return sorted(set(installed) or ALLOWED_MODELS)


def authorize(header: str | None):
    """Return storage key ('sb:<uuid>' or legacy name) or None."""
    if header and header.startswith('Bearer '):
        try:
            return storage_key(verify(header[7:].strip()))
        except (AuthError, ValueError):
            return None
    if sb_config() is not None or os.getenv('CHELSY_ALLOW_LEGACY_BASIC') != '1':
        return None  # unified auth is required by default; legacy only on explicit dev opt-in
    if not header or not header.startswith('Basic '):
        return None
    try:
        raw = base64.b64decode(header[6:], validate=True).decode('utf-8')
        username, password = raw.split(':', 1)
        if authenticate(username, password):
            return username
    except (ValueError, UnicodeDecodeError):
        pass
    return None


def permit(username: str, now=None):
    now = time.monotonic() if now is None else now
    with RATE_LOCK:
        q = RATE[username]
        while q and now - q[0] >= RATE_PERIOD:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return False
        q.append(now)
        return True


def is_owner(username):
    if username.startswith('sb:'):
        admin = admin_user_id()
        return bool(admin) and username == 'sb:' + admin
    return username == 'owner'


def _background_sync(username, token):
    if not username.startswith('sb:') or not token:
        return

    def job():
        try:
            cloud_sync(username[3:], token)
            push_memories(username[3:], token)
        except SyncError:
            pass   # unsynced rows stay flagged and are retried on the next sync
        except Exception as exc:
            print('[Sync error]', type(exc).__name__, flush=True)
    threading.Thread(target=job, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ChelsyPrivateHost/0.9.3'
    sys_version = ''

    def log_message(self, fmt, *args):
        # Never log authorization headers, message contents or credentials.
        print('[Chelsy Host]', self.address_string(), fmt % args, flush=True)

    # ---------------------------------------------------------------- helpers
    def _headers(self, status, ctype='application/json; charset=utf-8', length=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.end_headers()

    def reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._headers(status, length=len(data))
        self.wfile.write(data)

    def user(self):
        name = authorize(self.headers.get('Authorization'))
        if not name:
            self.reply(401, {'error': 'Invalid credentials', 'message': 'Требуется вход. Сессия истекла или неверна.'})
        return name

    def token(self):
        header = self.headers.get('Authorization', '')
        return header[7:].strip() if header.startswith('Bearer ') else None

    def body(self, limit=MAX_BODY):
        length_text = self.headers.get('Content-Length', '')
        try:
            length = int(length_text)
        except ValueError:
            self.reply(411, {'error': 'Content-Length required'})
            return None
        if length < 2 or length > limit:
            self.reply(413, {'error': 'Request too large'})
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self.reply(400, {'error': 'Invalid JSON'})
            return None
        if not isinstance(data, dict):
            self.reply(400, {'error': 'JSON object required'})
            return None
        return data

    def query(self):
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    # ---------------------------------------------------------------- routing
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.reply(200, {'service': 'Chelsy AI Host', 'version': VERSION, 'ok': True})
        legacy = {'/api/status', '/api/history', '/api/memory', '/api/sync'}
        if path not in legacy and not path.startswith('/api/v2/'):
            return self.reply(404, {'error': 'Not found'})
        username = self.user()
        if not username:
            return
        if path in legacy:
            return self.legacy_get(path, username)
        return self.v2_get(path, username)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path in ('/api/chat', '/api/memory'):
            username = self.user()
            if username:
                self.legacy_post(path, username)
            return
        if not path.startswith('/api/v2/'):
            return self.reply(404, {'error': 'Not found'})
        username = self.user()
        if not username:
            return
        return self.v2_post(path, username)

    def do_DELETE(self):
        path = urlsplit(self.path).path
        m = re.fullmatch(r'/api/v2/conversations/([0-9a-fA-F-]{36})', path)
        if not m:
            return self.reply(404, {'error': 'Not found'})
        username = self.user()
        if not username:
            return
        if not core.owns_conversation(username, m.group(1)):
            return self.reply(404, {'error': 'Not found'})
        core.delete_conversation(username, m.group(1))
        _background_sync(username, self.token())
        return self.reply(200, {'deleted': True})

    # ---------------------------------------------------------------- 0.9.2
    def legacy_get(self, path, username):
        token = self.token()
        if path == '/api/status':
            pages, pending = stats()
            return self.reply(200, {'service': 'Chelsy AI Host', 'version': VERSION, 'ollama': ollama_available(),
                                    'pages': pages, 'pending': pending, 'user': username})
        if path == '/api/sync':
            if not username.startswith('sb:'):
                return self.reply(409, {'error': 'Supabase login required for cloud sync'})
            try:
                uid = username[3:]
                result = cloud_sync(uid, token)
                result['memories_received'] = pull_memories(uid, token)
                result['memories_considered_for_upload'] = push_memories(uid, token)
                return self.reply(200, result)
            except SyncError as exc:
                return self.reply(503, {'error': str(exc), 'local_data_preserved': True})
        if path == '/api/history':
            if username.startswith('sb:'):
                try:
                    cloud_sync(username[3:], token)
                except SyncError:
                    pass  # local history remains accessible if Supabase is down
            return self.reply(200, {'messages': history(username)})
        if username.startswith('sb:'):
            try:
                pull_memories(username[3:], token)
            except SyncError:
                pass
        return self.reply(200, {'memory': personal_facts(username)})

    def legacy_post(self, path, username):
        body = self.body()
        if body is None:
            return
        message = body.get('message')
        model = body.get('model', 'llama3.2:1b')
        if path == '/api/memory':
            key, value = body.get('key'), body.get('value')
            if not (isinstance(key, str) and isinstance(value, str) and 1 <= len(key) <= 75 and 1 <= len(value) <= 2000):
                return self.reply(400, {'error': 'Invalid memory'})
            store_personal_fact(username, key, value)
            if username.startswith('sb:'):
                try:
                    save_memory(username[3:], self.token(), key, value)
                except SyncError:
                    return self.reply(202, {'saved_locally': True, 'cloud': 'pending'})
            return self.reply(200, {'saved_locally': True, 'cloud': 'ok' if username.startswith('sb:') else 'disabled'})
        if not isinstance(message, str) or not message.strip() or len(message) > 3000:
            return self.reply(400, {'error': 'Invalid message'})
        if not isinstance(model, str) or (model not in ALLOWED_MODELS and model not in allowed_models()):
            return self.reply(400, {'error': 'Model not allowed'})
        if not permit(username):
            return self.reply(429, {'error': 'Rate limit reached'})
        if not GENERATION_LOCK.acquire(timeout=30):
            return self.reply(503, {'error': 'Model busy; retry later'})
        try:
            if username.startswith('sb:'):
                try:
                    cloud_sync(username[3:], self.token())
                    pull_memories(username[3:], self.token())
                except SyncError:
                    pass
            transcript, _ = answer(message, username, model=model)
            cloud_state = 'disabled'
            if username.startswith('sb:'):
                try:
                    cloud_sync(username[3:], self.token())
                    push_memories(username[3:], self.token())
                    cloud_state = 'ok'
                except SyncError:
                    cloud_state = 'pending'
            return self.reply(200, {'answer': transcript[-1]['content'] if transcript else '', 'messages': transcript,
                                    'cloud': cloud_state})
        except Exception:
            return self.reply(500, {'error': 'Internal error; check host logs'})
        finally:
            GENERATION_LOCK.release()

    # ---------------------------------------------------------------- v2 GET
    def v2_get(self, path, username):
        q = self.query()
        if path == '/api/v2/status':
            import live_tools
            import ollama_client
            from knowledge import services, SearchManager
            pages, pending = stats()
            return self.reply(200, {
                'service': 'Chelsy AI Host', 'version': VERSION, 'user': username, 'owner': is_owner(username),
                'ollama': ollama_client.available(), 'models': allowed_models(),
                'default_model': ollama_client.resolve_model(None, os.environ.get('CHELSY_MODEL') or None),
                'busy': GENERATION_LOCK.locked(), 'internet': live_tools.internet_available(),
                'search_configured': SearchManager.configured(), 'knowledge': services()['db'].stats(username),
                'legacy_pages': pages, 'legacy_queue': pending,
                'cloud': 'configured' if sb_config() else 'disabled'})
        if path == '/api/v2/models':
            import ollama_client
            return self.reply(200, {'models': allowed_models(), 'recommended': ollama_client.RECOMMENDED,
                                    'default': ollama_client.resolve_model(None, os.environ.get('CHELSY_MODEL') or None)})
        if path == '/api/v2/conversations':
            if username.startswith('sb:') and q.get('sync') == '1':
                try:
                    cloud_sync(username[3:], self.token())
                except SyncError:
                    pass
            return self.reply(200, {'conversations': core.list_conversations(
                username, q.get('q', ''), favorites_only=q.get('favorites') == '1')})
        m = re.fullmatch(r'/api/v2/conversations/([0-9a-fA-F-]{36})/messages', path)
        if m:
            if not core.owns_conversation(username, m.group(1)):
                return self.reply(404, {'error': 'Not found'})
            return self.reply(200, {'messages': core.conversation_messages(username, m.group(1))})
        if path == '/api/v2/settings':
            from intelligence import ENGINE
            return self.reply(200, {'generation': ENGINE.settings(username),
                                    'web_search': core.get_setting(username, 'web_search', True),
                                    'training_consent': core.get_setting(username, 'training_consent', False)})
        if path == '/api/v2/memory':
            return self.reply(200, {'memory': personal_facts(username)})
        if path == '/api/v2/sync':
            return self.legacy_get('/api/sync', username)
        from knowledge import services
        kb = services()
        if path == '/api/v2/research':
            return self.reply(200, {'jobs': kb['db'].jobs(username)})
        m = re.fullmatch(r'/api/v2/research/([0-9a-fA-F-]{36})', path)
        if m:
            job = kb['db'].get_job(m.group(1), username)
            if not job:
                return self.reply(404, {'error': 'Not found'})
            return self.reply(200, {'job': job, 'items': kb['db'].items(job['id'])})
        if path == '/api/v2/knowledge':
            scope = q.get('scope')
            scope = username if scope == 'personal' else 'shared' if scope == 'shared' else None
            return self.reply(200, {'documents': kb['db'].list_documents(username, q.get('q', '')[:100], scope),
                                    'stats': kb['db'].stats(username), 'conflicts': kb['db'].conflicts(username),
                                    'sources': kb['db'].sources()})
        if path == '/api/v2/knowledge/search':
            return self.reply(200, {'results': kb['retriever'].retrieve(q.get('q', '')[:500], username, top=6)})
        return self.reply(404, {'error': 'Not found'})

    # ---------------------------------------------------------------- v2 POST
    def v2_post(self, path, username):
        if path == '/api/v2/chat':
            body = self.body(MAX_CHAT_BODY)
            if body is not None:
                self.v2_chat(username, body)
            return
        body = self.body()
        if body is None:
            return
        if path == '/api/v2/chat/stop':
            rid = str(body.get('request_id', ''))
            with ACTIVE_LOCK:
                entry = ACTIVE.get(rid)
            if not entry or entry[0] != username:
                return self.reply(404, {'stopped': False})
            entry[1].set()
            return self.reply(200, {'stopped': True})
        if path == '/api/v2/conversations':
            cid = core.create_conversation(username, str(body.get('title') or core.DEFAULT_TITLE)[:120])
            return self.reply(201, {'id': cid})
        m = re.fullmatch(r'/api/v2/conversations/([0-9a-fA-F-]{36})', path)
        if m:
            cid = m.group(1)
            if not core.owns_conversation(username, cid):
                return self.reply(404, {'error': 'Not found'})
            if isinstance(body.get('title'), str):
                try:
                    core.rename_conversation(username, cid, body['title'])
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
            if 'favorite' in body:
                core.set_favorite(username, cid, bool(body['favorite']))
            if body.get('deleted') is True:
                core.delete_conversation(username, cid)
            _background_sync(username, self.token())
            return self.reply(200, {'updated': True})
        m = re.fullmatch(r'/api/v2/messages/(\d{1,12})/rating', path)
        if m:
            core.rate_message(username, int(m.group(1)), int(body.get('rating', 0)))
            return self.reply(200, {'rated': True})
        if path == '/api/v2/memory':
            return self.legacy_post_memory(username, body)
        if path == '/api/v2/settings':
            from intelligence import validate_settings
            if isinstance(body.get('generation'), dict):
                core.set_setting(username, 'generation', validate_settings(body['generation']))
            if 'web_search' in body:
                core.set_setting(username, 'web_search', bool(body['web_search']))
            if 'training_consent' in body:
                core.set_setting(username, 'training_consent', body['training_consent'] is True)
            return self.reply(200, {'saved': True})
        from knowledge import services, ResearchError
        kb = services()
        try:
            if path == '/api/v2/research':
                scope = 'shared' if body.get('scope') == 'shared' else username
                job = kb['scheduler'].submit(username, str(body.get('topic', '')), target_scope=scope,
                                             max_pages=int(body.get('max_pages', 8)), is_owner=is_owner(username),
                                             auto_accept=bool(body.get('auto_accept')), news=bool(body.get('news')))
                return self.reply(201, {'id': job})
            m = re.fullmatch(r'/api/v2/research/([0-9a-fA-F-]{36})/stop', path)
            if m:
                return self.reply(200, {'stopped': kb['scheduler'].stop(m.group(1), username)})
            m = re.fullmatch(r'/api/v2/research/items/(\d{1,12})', path)
            if m:
                action = body.get('action')
                if action == 'accept':
                    scope = 'shared' if body.get('scope') == 'shared' else username
                    doc_id, result = kb['scheduler'].accept(int(m.group(1)), username, scope, is_owner(username))
                    return self.reply(200, {'doc_id': doc_id, 'result': result})
                if action == 'reject':
                    kb['scheduler'].reject(int(m.group(1)), username)
                    return self.reply(200, {'rejected': True})
                return self.reply(400, {'error': 'Unknown action'})
            if path == '/api/v2/knowledge/sources':
                if not is_owner(username):
                    return self.reply(403, {'error': 'Owner only'})
                if body.get('remove'):
                    kb['db'].remove_source(str(body['remove']))
                    return self.reply(200, {'removed': True})
                return self.reply(200, {'domain': kb['db'].allow_source(str(body.get('domain', '')), username,
                                                                        float(body.get('trust', 0.7)))})
        except (ResearchError, ValueError) as exc:
            return self.reply(400, {'error': str(exc)})
        return self.reply(404, {'error': 'Not found'})

    def legacy_post_memory(self, username, body):
        key, value = body.get('key'), body.get('value')
        if not (isinstance(key, str) and isinstance(value, str) and 1 <= len(key) <= 75 and 1 <= len(value) <= 2000):
            return self.reply(400, {'error': 'Invalid memory'})
        store_personal_fact(username, key.strip().lower(), value.strip())
        _background_sync(username, self.token())
        return self.reply(200, {'saved_locally': True})

    # ---------------------------------------------------------------- v2 chat
    def v2_chat(self, username, body):
        from intelligence import ENGINE
        message = body.get('message', '')
        model = body.get('model') or None
        cid = body.get('conversation_id') or None
        attachments = body.get('attachments') or []
        if not isinstance(message, str) or len(message) > 6000:
            return self.reply(400, {'error': 'Invalid message'})
        if model is not None and (not isinstance(model, str) or model not in allowed_models()):
            return self.reply(400, {'error': 'Model not allowed'})
        if cid is not None and (not isinstance(cid, str) or not UUID_RE.match(cid)):
            return self.reply(400, {'error': 'Invalid conversation'})
        if not isinstance(attachments, list) or len(attachments) > 4 or not all(
                isinstance(a, dict) and isinstance(a.get('name', ''), str) and isinstance(a.get('text', ''), str)
                for a in attachments):
            return self.reply(400, {'error': 'Invalid attachments'})
        if cid and not core.owns_conversation(username, cid):
            return self.reply(404, {'error': 'Conversation not found'})
        cid = cid or core.create_conversation(username)
        regenerate = body.get('regenerate') is True
        edit_id = body.get('edit_message_id')
        if regenerate or edit_id is not None:
            msgs = core.conversation_messages(username, cid)
            if edit_id is not None:
                target = next((m for m in msgs if m['id'] == edit_id and m['role'] == 'user'), None)
                if not target or not message.strip():
                    return self.reply(400, {'error': 'Message to edit not found'})
                core.truncate_from(username, cid, target['id'])
                regenerate = False
            else:
                last_user = next((m for m in reversed(msgs) if m['role'] == 'user'), None)
                if not last_user:
                    return self.reply(400, {'error': 'Nothing to regenerate'})
                later = [m for m in msgs if m['id'] > last_user['id']]
                if later:
                    core.truncate_from(username, cid, later[0]['id'])
                message = last_user['content']
        elif not message.strip() and not attachments:
            return self.reply(400, {'error': 'Invalid message'})
        if not permit(username):
            return self.reply(429, {'error': 'Rate limit reached', 'message': 'Слишком много запросов, подожди минуту.'})
        if not GENERATION_LOCK.acquire(timeout=30):
            return self.reply(503, {'error': 'Model busy; retry later', 'message': 'Модель занята другим запросом.'})
        request_id = str(body.get('request_id') or uuid.uuid4())[:64]
        cancel = threading.Event()
        with ACTIVE_LOCK:
            ACTIVE[request_id] = (username, cancel)
        stream = body.get('stream') is not False
        web = body.get('web')
        web = core.get_setting(username, 'web_search', True) if web is None else bool(web)
        try:
            events = ENGINE.respond(message, username, conversation_id=cid, model=model, web_enabled=web,
                                    cancel=cancel, attachments=attachments, regenerate=regenerate)
            if not stream:
                final = None
                for event in events:
                    final = event if event['type'] == 'done' else final
                _background_sync(username, self.token())
                return self.reply(200, {'request_id': request_id, 'conversation_id': cid, **(final or {})})
            self._headers(200, 'application/x-ndjson; charset=utf-8')
            self.close_connection = True
            self.wfile.write((json.dumps({'type': 'start', 'request_id': request_id, 'conversation_id': cid}) + '\n').encode())
            self.wfile.flush()
            try:
                for event in events:
                    self.wfile.write((json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8'))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                cancel.set()          # client went away: stop generating, keep partial answer
                events.close()
            _background_sync(username, self.token())
        except Exception as exc:
            print('[Chat error]', type(exc).__name__, flush=True)
            try:
                self.wfile.write((json.dumps({'type': 'error', 'message': 'Внутренняя ошибка хоста'}) + '\n').encode())
            except OSError:
                pass
        finally:
            with ACTIVE_LOCK:
                ACTIVE.pop(request_id, None)
            GENERATION_LOCK.release()


def auto_research_loop():
    """Legacy opt-in crawl (0.9.2): queue seeded by owner in Desktop > Research."""
    interval = max(900, int(os.environ.get('CHELSY_CRAWL_INTERVAL_SECONDS', '3600')))
    while True:
        try:
            events = crawl(limit=2)
            print('[Research]', events[-1] if events else 'idle', flush=True)
        except Exception as exc:
            print('[Research error]', type(exc).__name__, flush=True)
        time.sleep(interval)


def make_server(port=None):
    port = PORT if port is None else port
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve():
    if sb_config() is None and os.getenv('CHELSY_ALLOW_LEGACY_BASIC') != '1':
        raise RuntimeError('Configure Supabase Auth before running Chelsy Host. Run ChelsyConfig.')
    if PORT < 1024 or PORT > 65535:
        raise ValueError('Use an unprivileged port between 1024 and 65535')
    import migrations
    migrations.migrate(core.DB_PATH, log=print)
    from knowledge import services
    services()['scheduler'].start(owner_check=is_owner)
    if os.environ.get('CHELSY_AUTO_CRAWL') == '1':
        threading.Thread(target=auto_research_loop, daemon=True).start()
    print(f'Chelsy AI Host {VERSION} runs on http://127.0.0.1:%d' % PORT, flush=True)
    print('To allow your devices, install Tailscale and run: tailscale serve --bg %d' % PORT, flush=True)
    print('Never forward this port on your router or use Tailscale Funnel.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()


if __name__ == '__main__':
    serve()
