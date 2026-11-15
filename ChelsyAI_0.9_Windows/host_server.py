"""Chelsy AI private host API. Bind loopback ONLY; publish using Tailscale Serve HTTPS.
No direct Internet exposure and never expose Ollama to remote users.
"""
from __future__ import annotations
import base64
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
from urllib.parse import urlsplit

from core import authenticate, answer, history, personal_facts, stats, ollama_available, crawl
from supabase_auth import verify, storage_key, config as sb_config, AuthError
from cloud_sync import sync as cloud_sync, pull_memories, push_memories, save_memory, SyncError
from core import store_personal_fact

PORT = int(os.environ.get('CHELSY_HOST_PORT', '8787'))
MAX_BODY = 12_000
RATE_LIMIT = 12
RATE_PERIOD = 60
RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()
GENERATION_LOCK = threading.Lock()
ALLOWED_MODELS = {'llama3.2:1b', 'qwen3:4b'}


def authorize(header: str | None):
    """Return username or None. Credentials accepted over HTTPS tunnel only."""
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


class Handler(BaseHTTPRequestHandler):
    server_version = 'ChelsyPrivateHost/0.9'
    def log_message(self, fmt, *args):
        # Do not log authorization headers, message contents, or credentials.
        print('[Chelsy Host]', self.address_string(), fmt % args)

    def reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def user(self):
        name = authorize(self.headers.get('Authorization'))
        if not name:
            self.reply(401, {'error':'Invalid credentials', 'message':'Неверный логин или пароль.'})
        return name

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in {'/api/status','/api/history','/api/memory','/api/sync'}:
            return self.reply(404, {'error':'Not found'})
        username = self.user()
        if not username:
            return
        if path == '/api/status':
            pages, pending = stats()
            return self.reply(200, {'service':'Chelsy AI Host', 'version':'0.9', 'ollama':ollama_available(), 'pages':pages,'pending':pending,'user':username})
        if path == '/api/sync':
            if not username.startswith('sb:'):
                return self.reply(409, {'error':'Supabase login required for cloud sync'})
            try:
                token = self.headers['Authorization'][7:].strip()
                uid = username[3:]
                result = cloud_sync(uid,token)
                result['memories_received']=pull_memories(uid,token)
                result['memories_considered_for_upload']=push_memories(uid,token)
                return self.reply(200,result)
            except SyncError as exc:
                return self.reply(503, {'error':str(exc),'local_data_preserved':True})
        if path == '/api/history':
            if username.startswith('sb:'):
                try:
                    cloud_sync(username[3:], self.headers['Authorization'][7:].strip())
                except SyncError:
                    pass  # local history remains accessible if Supabase is down
            return self.reply(200, {'messages':history(username)})
        if username.startswith('sb:'):
            try: pull_memories(username[3:],self.headers['Authorization'][7:].strip())
            except SyncError: pass
        return self.reply(200, {'memory':personal_facts(username)})

    def do_POST(self):
        path=urlsplit(self.path).path
        if path not in ('/api/chat','/api/memory'):
            return self.reply(404, {'error':'Not found'})
        username = self.user()
        if not username:
            return
        length_text = self.headers.get('Content-Length','')
        try:
            length = int(length_text)
        except ValueError:
            return self.reply(411, {'error':'Content-Length required'})
        if length < 2 or length > MAX_BODY:
            return self.reply(413, {'error':'Request too large'})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('JSON object required')
            message = body.get('message')
            model = body.get('model', 'llama3.2:1b')
            if not isinstance(message, str) or not message.strip() or len(message)>3000:
                return self.reply(400, {'error':'Invalid message'})
            if path == '/api/chat' and model not in ALLOWED_MODELS:
                return self.reply(400, {'error':'Model not allowed'})
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, {'error':'Invalid JSON'})
        if path == '/api/memory':
            key,value=body.get('key'),body.get('value')
            if not (isinstance(key,str) and isinstance(value,str) and 1<=len(key)<=75 and 1<=len(value)<=2000):
                return self.reply(400, {'error':'Invalid memory'})
            store_personal_fact(username,key,value)
            if username.startswith('sb:'):
                try: save_memory(username[3:],self.headers['Authorization'][7:].strip(),key,value)
                except SyncError: return self.reply(202, {'saved_locally':True,'cloud':'pending'})
            return self.reply(200,{'saved_locally':True,'cloud':'ok' if username.startswith('sb:') else 'disabled'})
        if not permit(username):
            return self.reply(429, {'error':'Rate limit reached'})
        if not GENERATION_LOCK.acquire(blocking=False):
            return self.reply(503, {'error':'Model busy; retry later'})
        try:
            if username.startswith('sb:'):
                try:
                    token=self.headers['Authorization'][7:].strip()
                    cloud_sync(username[3:],token)
                    pull_memories(username[3:],token)
                except SyncError:
                    pass
            transcript, _ = answer(message, username, model=model)
            cloud_state='disabled'
            if username.startswith('sb:'):
                try:
                    cloud_sync(username[3:],self.headers['Authorization'][7:].strip())
                    push_memories(username[3:],self.headers['Authorization'][7:].strip())
                    cloud_state='ok'
                except SyncError:
                    cloud_state='pending'
            return self.reply(200, {'answer':transcript[-1]['content'] if transcript else '', 'messages':transcript,'cloud':cloud_state})
        except Exception:
            return self.reply(500, {'error':'Internal error; check host logs'})
        finally:
            GENERATION_LOCK.release()


def auto_research_loop():
    """Bounded opt-in crawl: queue seeded by owner in Desktop > Research."""
    interval = max(900, int(os.environ.get('CHELSY_CRAWL_INTERVAL_SECONDS','3600')))
    while True:
        try:
            events = crawl(limit=2)
            print('[Research]', events[-1] if events else 'idle', flush=True)
        except Exception as exc:
            print('[Research error]', type(exc).__name__, flush=True)
        time.sleep(interval)


def serve():
    if sb_config() is None and os.getenv('CHELSY_ALLOW_LEGACY_BASIC') != '1':
        raise RuntimeError('Configure Supabase Auth before running Chelsy Host. Run ChelsyConfig.')
    if PORT < 1024 or PORT > 65535:
        raise ValueError('Use an unprivileged port between 1024 and 65535')
    if os.environ.get('CHELSY_AUTO_CRAWL') == '1':
        threading.Thread(target=auto_research_loop, daemon=True).start()
    print('Chelsy private host runs on http://127.0.0.1:%d' % PORT, flush=True)
    print('To allow a friend, install Tailscale and run: tailscale serve %d' % PORT, flush=True)
    print('Never forward this port on your router or use Tailscale Funnel.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

if __name__ == '__main__':
    serve()
