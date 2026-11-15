"""Minimal Ollama HTTP client: model list, blocking chat, cancellable streaming, embeddings.

Ollama is reached ONLY on the loopback interface of the host machine. Network
clients never talk to Ollama directly; they go through Chelsy Host.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler

BASE = os.environ.get('CHELSY_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
if not re.fullmatch(r'http://(127\.0\.0\.1|localhost|\[::1\]):\d{2,5}', BASE):
    BASE = 'http://127.0.0.1:11434'   # never send prompts/memory to a remote host
_OPENER = build_opener(ProxyHandler({}))
MODEL_NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._\-/]{0,80}(:[a-zA-Z0-9._\-]{1,60})?$')
EMBED_HINTS = ('embed', 'bge', 'minilm', 'e5-', 'gte-', 'arctic-embed')

# Models known to give good RU/EN quality on consumer laptops (informational).
RECOMMENDED = [
    {'name': 'qwen3:8b', 'ram_gb': 10, 'note': 'Сильный русский и английский, рассуждения; рекомендуется при 16 ГБ RAM.'},
    {'name': 'gemma3:12b', 'ram_gb': 14, 'note': 'Очень естественный стиль; нужен мощный ноутбук.'},
    {'name': 'gemma3:4b', 'ram_gb': 6, 'note': 'Хороший баланс качества и скорости.'},
    {'name': 'qwen3:4b', 'ram_gb': 5, 'note': 'Компактная модель с приличным русским.'},
    {'name': 'llama3.1:8b', 'ram_gb': 9, 'note': 'Надёжный английский, русский средний.'},
    {'name': 'llama3.2:1b', 'ram_gb': 2, 'note': 'Только для слабых ПК: заметно слабее в русском.'},
]
EMBED_MODEL = os.environ.get('CHELSY_EMBED_MODEL', 'nomic-embed-text')

_cache = {'models': None, 'stamp': 0.0, 'up': None, 'up_stamp': 0.0}
_lock = threading.Lock()


class OllamaError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def _post(path, payload, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = Request(BASE + path, data=data, headers={'Content-Type': 'application/json'})
    return _OPENER.open(req, timeout=timeout)


def available(ttl=5.0):
    now = time.monotonic()
    with _lock:
        if _cache['up'] is not None and now - _cache['up_stamp'] < ttl:
            return _cache['up']
    try:
        with _OPENER.open(BASE + '/api/tags', timeout=2) as resp:
            up = resp.status == 200
    except Exception:
        up = False
    with _lock:
        _cache['up'], _cache['up_stamp'] = up, now
    return up


def list_models(ttl=20.0, include_embedding=False):
    """Installed model names (cached). Empty list when Ollama is down."""
    now = time.monotonic()
    with _lock:
        cached = _cache['models'] if now - _cache['stamp'] < ttl else None
    if cached is None:
        try:
            with _OPENER.open(BASE + '/api/tags', timeout=3) as resp:
                data = json.load(resp)
            cached = sorted({m.get('name', '') for m in data.get('models', [])
                             if MODEL_NAME.match(m.get('name', '') or '')})
        except Exception:
            cached = []
        with _lock:
            _cache['models'], _cache['stamp'] = cached, now
    if include_embedding:
        return list(cached)
    return [m for m in cached if not any(h in m.lower() for h in EMBED_HINTS)]


def resolve_model(requested=None, fallback=None):
    """Return an installed chat model; prefer the requested one."""
    installed = list_models()
    if requested and MODEL_NAME.match(requested):
        if not installed or requested in installed:
            return requested
        if requested + ':latest' in installed:
            return requested + ':latest'
    if fallback and (not installed or fallback in installed):
        return fallback
    for item in RECOMMENDED:
        if item['name'] in installed:
            return item['name']
    return installed[0] if installed else (requested or fallback or 'llama3.2:1b')


def _options(settings):
    return {k: settings[k] for k in ('temperature', 'top_p', 'top_k', 'num_ctx', 'num_predict',
                                     'repeat_penalty', 'seed') if k in settings and settings[k] is not None}


def _thinking_flag(model):
    name = model.lower()
    return name.startswith(('qwen3', 'deepseek-r1', 'magistral')) or 'think' in name


_THINK = re.compile(r'<think>.*?(</think>|$)', re.S | re.I)


def strip_thinking(text):
    return _THINK.sub('', text or '').strip()


def chat(model, messages, settings=None, timeout=240):
    settings = settings or {}
    payload = {'model': model, 'messages': messages, 'stream': False, 'options': _options(settings)}
    if _thinking_flag(model):
        payload['think'] = False
    try:
        with _post('/api/chat', payload, timeout) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise OllamaError(type(exc).__name__) from exc
    return strip_thinking((data.get('message') or {}).get('content', ''))


def stream_chat(model, messages, settings=None, cancel: threading.Event | None = None, timeout=240):
    """Yield text deltas. Closing the generator or setting ``cancel`` aborts the HTTP stream."""
    settings = settings or {}
    payload = {'model': model, 'messages': messages, 'stream': True, 'options': _options(settings)}
    if _thinking_flag(model):
        payload['think'] = False
    try:
        resp = _post('/api/chat', payload, timeout)
    except Exception as exc:
        raise OllamaError(type(exc).__name__) from exc
    in_think = False
    try:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('error'):
                raise OllamaError(str(event['error'])[:200])
            piece = (event.get('message') or {}).get('content', '')
            if piece:
                # Hide <think> blocks of reasoning models from the user.
                if '<think>' in piece:
                    in_think = True
                    piece = piece.split('<think>', 1)[0]
                if in_think:
                    if '</think>' in piece:
                        in_think = False
                        piece = piece.split('</think>', 1)[1]
                    else:
                        piece = ''
                if piece:
                    yield piece
            if event.get('done'):
                break
    finally:
        resp.close()


def embed(texts, model=None, timeout=60):
    """Return list of vectors or None if the embedding model is unavailable."""
    model = model or EMBED_MODEL
    if not texts:
        return []
    try:
        with _post('/api/embed', {'model': model, 'input': list(texts)}, timeout) as resp:
            data = json.load(resp)
        vectors = data.get('embeddings')
        if isinstance(vectors, list) and len(vectors) == len(texts):
            return vectors
    except Exception:
        return None
    return None
