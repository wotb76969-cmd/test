"""Chelsy AI Desktop 0.9.3 — new chat-first interface (Gradio, local only).

Sidebar: logo, new chat, history search, favourites, recent chats,
research, knowledge base, memory, settings, profile. Main area: spacious
chat with Markdown/code highlighting, copy, regenerate, edit, stop, sources,
model/server status (Chelsy Pulse). Composer: multiline input, attachments
(text files), model choice, web search toggle.
"""
import base64
import html
import sys
import threading
from pathlib import Path

import gradio as gr

import core
from core import (USERS_PATH, MODEL, add_seed, crawl, stats, personal_facts, delete_personal_fact,
                  store_personal_fact)
from desktop_auth import authenticate, account_name, is_admin, bearer_for  # noqa: F401 (authenticate used by launcher)
from cloud_sync import sync as sync_cloud, pull_memories, push_memories, save_memory, delete_cloud_memory, SyncError

VERSION = '0.9.3'
TEXT_TYPES = ['.txt', '.md', '.py', '.json', '.csv', '.log', '.js', '.ts', '.html', '.css', '.yaml', '.yml', '.xml',
              '.ini', '.toml', '.dart', '.sql', '.java', '.c', '.cpp', '.cs', '.go', '.rs', '.sh', '.ps1', '.bat']
MAX_FILE = 200_000
SECTIONS = ['💬 Чат', '🔎 Исследования', '💾 Память', '📚 База знаний', '⚙️ Настройки']
CANCELS = {}
CANCEL_LOCK = threading.Lock()


def asset_path(name):
    """Resolve bundled resources both from source and PyInstaller one-dir."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'assets' / name


def _logo_data():
    return base64.b64encode(asset_path('chelsy_logo_small.png').read_bytes()).decode('ascii')


def brand_header_html():
    """Local, embedded brand image avoids external network assets in the UI."""
    return ('<div class="chelsy-brand">'
            f'<img class="chelsy-brand-avatar" alt="Chelsy AI logo" src="data:image/png;base64,{_logo_data()}">'
            f'<div><div class="chelsy-brand-name">Chelsy AI</div><div class="chelsy-brand-sub">Desktop {VERSION} · beta</div></div>'
            '</div>')


def welcome_html():
    return (f'<div class="chelsy-welcome"><img alt="" src="data:image/png;base64,{_logo_data()}">'
            '<h2>Привет! Я Челси 💜</h2><p>Спроси что угодно, попроси объяснить, помочь с кодом или исследовать тему.</p></div>')


# ------------------------------------------------------------------ helpers
def user(request: gr.Request):
    raw = request.username if request is not None else None
    name = account_name(raw)
    if not name:
        raise gr.Error('Войди в Chelsy AI.')
    return name


def owner(request):
    return is_admin(request.username if request else None)


def _token(username):
    try:
        return bearer_for(username) if username.startswith('sb:') else None
    except Exception:
        return None


def _cloud_user_sync(username):
    token = _token(username)
    if not token:
        return 'local'
    try:
        sync_cloud(username[3:], token)
        pull_memories(username[3:], token)
        push_memories(username[3:], token)
        return 'ok'
    except (SyncError, Exception):
        return 'pending'     # a failed network sync never destroys local chats


def _background_sync(username):
    threading.Thread(target=_cloud_user_sync, args=(username,), daemon=True).start()


def model_choices():
    import ollama_client
    models = ollama_client.list_models()
    return models or ['llama3.2:1b', 'qwen3:4b']


def default_model(username=None):
    import ollama_client
    from intelligence import ENGINE
    preferred = ENGINE.settings(username).get('model') if username else ''
    import os
    return ollama_client.resolve_model(preferred or None, os.environ.get('CHELSY_MODEL') or None)


def pulse_html(state='ready', text=None, extra=''):
    labels = {'ready': 'Готова к работе', 'generating': 'Генерирую ответ', 'searching': 'Ищу информацию',
              'syncing': 'Синхронизирую данные', 'offline': 'Модель или сервер недоступны', 'checking': 'Проверяю ответ'}
    label = html.escape(text or labels.get(state, state))
    return (f'<div class="chelsy-pulse-row" role="status" aria-live="polite"><span class="chelsy-pulse pulse-{state}">'
            f'<i></i></span><span class="pulse-label">{label}</span>{extra}</div>')


def status_html(request: gr.Request):
    import ollama_client
    import live_tools
    username = user(request)
    up = ollama_client.available()
    from knowledge import services
    kb = services()['db'].stats(username)
    online = live_tools.internet_available()
    extra = (f'<span class="pulse-meta">{"🌐 онлайн" if online else "📴 нет интернета"} · '
             f'📚 {kb["documents"]} док.</span>')
    return pulse_html('ready' if up else 'offline', None if up else 'Ollama недоступна — запусти Ollama', extra)


def conversations_update(username, query='', selected=None):
    items = core.list_conversations(username, query)
    favs = [c for c in items if c['favorite']]
    recent = [(c['title'][:48] or 'Без названия', c['id']) for c in items if not c['favorite']]
    fav_choices = [('★ ' + (c['title'][:46] or 'Без названия'), c['id']) for c in favs]
    return (gr.update(choices=fav_choices, value=selected if any(c['id'] == selected for c in favs) else None),
            gr.update(choices=recent, value=selected if any(v == selected for _, v in recent) else None))


def to_chatbot(username, conversation_id):
    if not conversation_id:
        return [], []
    msgs = core.conversation_messages(username, conversation_id)
    return [{'role': m['role'], 'content': m['content']} for m in msgs], [m['id'] for m in msgs]


def title_html(username, conversation_id):
    if not conversation_id:
        return '<div class="chelsy-conv-title">Новый чат</div>'
    item = next((c for c in core.list_conversations(username, limit=500) if c['id'] == conversation_id), None)
    title = item['title'] if item else 'Новый чат'
    star = ' <span class="fav">★</span>' if item and item['favorite'] else ''
    return f'<div class="chelsy-conv-title">{html.escape(title)}{star}</div>'


def read_attachments(files):
    attachments, notes = [], []
    for f in (files or [])[:4]:
        path = Path(f if isinstance(f, str) else getattr(f, 'path', '') or f.get('path', ''))
        if path.suffix.lower() not in TEXT_TYPES:
            notes.append(f'«{path.name}»: этот тип файлов пока не поддерживается (только текстовые файлы и код).')
            continue
        data = path.read_bytes()[:MAX_FILE]
        for enc in ('utf-8', 'cp1251'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            notes.append(f'«{path.name}»: не удалось прочитать как текст.')
            continue
        attachments.append({'name': path.name, 'text': text})
    return attachments, notes


# ------------------------------------------------------------------ chat
def _run(username, conversation_id, question, model, web, request, attachments=None, regenerate=False, notes=None):
    from intelligence import ENGINE
    key = request.session_hash if request is not None else 'local'
    cancel = threading.Event()
    with CANCEL_LOCK:
        CANCELS[key] = cancel
    messages, ids = to_chatbot(username, conversation_id)
    if not regenerate:
        shown = question + (('\n\n📎 ' + ', '.join(a['name'] for a in attachments)) if attachments else '')
        messages = messages + [{'role': 'user', 'content': shown}]
    messages = messages + [{'role': 'assistant', 'content': '…'}]
    answer, state = '', 'generating'
    yield messages, ids, pulse_html('generating')
    events = ENGINE.respond(question, username, conversation_id=conversation_id, model=model, web_enabled=web,
                            cancel=cancel, attachments=attachments, regenerate=regenerate)
    try:
        for event in events:
            if event['type'] == 'status':
                state = event['state']
                if not answer:
                    messages[-1]['content'] = f'_{event["text"]}_'
                yield messages, ids, pulse_html(state, event['text'])
            elif event['type'] == 'delta':
                answer += event['text']
                messages[-1]['content'] = answer + ' ▍'
                yield messages, ids, pulse_html('generating')
            elif event['type'] in ('replace', 'done'):
                messages[-1]['content'] = event['text']
                answer = event['text']
        if notes:
            messages[-1]['content'] += '\n\n' + '\n'.join('⚠️ ' + n for n in notes)
    finally:
        events.close()
        with CANCEL_LOCK:
            CANCELS.pop(key, None)
    messages, ids = to_chatbot(username, conversation_id)
    if notes and messages:
        messages[-1]['content'] += '\n\n' + '\n'.join('⚠️ ' + n for n in notes)
    _background_sync(username)
    yield messages, ids, pulse_html('ready')


def send(message, conversation_id, model, web, request: gr.Request):
    username = user(request)
    text = (message or {}).get('text', '') if isinstance(message, dict) else str(message or '')
    files = (message or {}).get('files', []) if isinstance(message, dict) else []
    attachments, notes = read_attachments(files)
    if not text.strip() and not attachments:
        raise gr.Error('Напиши сообщение или прикрепи текстовый файл.')
    if not core.owns_conversation(username, conversation_id):
        conversation_id = core.create_conversation(username)
    for chat, ids, pulse in _run(username, conversation_id, text.strip(), model, web, request, attachments, notes=notes):
        yield chat, ids, pulse, conversation_id, gr.update(value=None, interactive=False)
    yield chat, ids, pulse, conversation_id, gr.update(value=None, interactive=True)


def stop_generation(request: gr.Request):
    key = request.session_hash if request is not None else 'local'
    with CANCEL_LOCK:
        event = CANCELS.get(key)
    if event:
        event.set()
    return pulse_html('ready', 'Остановлено')


def regenerate(conversation_id, model, web, ids, request: gr.Request):
    username = user(request)
    msgs = core.conversation_messages(username, conversation_id) if conversation_id else []
    last_user = next((m for m in reversed(msgs) if m['role'] == 'user'), None)
    if not last_user:
        raise gr.Error('Нечего генерировать заново.')
    later = [m for m in msgs if m['id'] > last_user['id']]
    if later:
        core.truncate_from(username, conversation_id, later[0]['id'])
    question = last_user['content'].split('\n\n📎 ', 1)[0]
    yield from _run(username, conversation_id, question, model, web, request, regenerate=True)


def edit_message(conversation_id, model, web, ids, data: gr.EditData, request: gr.Request):
    username = user(request)
    index = data.index if isinstance(data.index, int) else data.index[0]
    if not conversation_id or index >= len(ids):
        raise gr.Error('Сообщение не найдено.')
    msgs = {m['id']: m for m in core.conversation_messages(username, conversation_id)}
    target = msgs.get(ids[index])
    if not target or target['role'] != 'user':
        raise gr.Error('Редактировать можно только свои сообщения.')
    text = str(data.value or '').strip()
    if not text:
        raise gr.Error('Сообщение не может быть пустым.')
    core.truncate_from(username, conversation_id, target['id'])
    yield from _run(username, conversation_id, text, model, web, request)


def rate(ids, data: gr.LikeData, request: gr.Request):
    username = user(request)
    index = data.index if isinstance(data.index, int) else data.index[0]
    if index < len(ids):
        core.rate_message(username, ids[index], 1 if data.liked else -1)


def new_chat(request: gr.Request):
    username = user(request)
    favs, recent = conversations_update(username)
    return None, [], [], title_html(username, None), favs, recent, gr.update(value=None, interactive=True)


def open_conversation(conversation_id, request: gr.Request):
    username = user(request)
    if not conversation_id or not core.owns_conversation(username, conversation_id):
        return gr.update(), gr.update(), gr.update(), gr.update()
    chat, ids = to_chatbot(username, conversation_id)
    return conversation_id, chat, ids, title_html(username, conversation_id)


def refresh_lists(query, conversation_id, request: gr.Request):
    username = user(request)
    return conversations_update(username, query or '', conversation_id)


def toggle_favorite(conversation_id, query, request: gr.Request):
    username = user(request)
    if not conversation_id:
        raise gr.Error('Сначала начни или выбери чат.')
    current = next((c for c in core.list_conversations(username, limit=500) if c['id'] == conversation_id), None)
    core.set_favorite(username, conversation_id, not (current and current['favorite']))
    _background_sync(username)
    return (title_html(username, conversation_id),) + conversations_update(username, query or '', conversation_id)


def rename_current(conversation_id, new_title, query, request: gr.Request):
    username = user(request)
    if not conversation_id:
        raise gr.Error('Сначала начни или выбери чат.')
    try:
        core.rename_conversation(username, conversation_id, new_title)
    except ValueError as exc:
        raise gr.Error(str(exc))
    _background_sync(username)
    return (title_html(username, conversation_id), '') + conversations_update(username, query or '', conversation_id)


def delete_current(conversation_id, query, request: gr.Request):
    username = user(request)
    if conversation_id:
        core.delete_conversation(username, conversation_id)
        _background_sync(username)
    favs, recent = conversations_update(username, query or '')
    return None, [], [], title_html(username, None), favs, recent


def initial_load(request: gr.Request):
    username = user(request)
    _background_sync(username)
    cid = core.latest_conversation(username, create=False)
    chat, ids = to_chatbot(username, cid)
    favs, recent = conversations_update(username, '', cid)
    is_owner = owner(request)
    display = html.escape(request.username or '')
    profile = (f'<div class="chelsy-profile"><div class="avatar">{html.escape((request.username or "?")[:1].upper())}</div>'
               f'<div><div class="name">{display}</div><div class="role">{"Владелец" if is_owner else "Пользователь"}'
               f'</div></div><a class="logout" href="/logout" title="Выйти">⎋</a></div>')
    choices = model_choices()
    return (cid, chat, ids, title_html(username, cid), favs, recent, profile,
            gr.update(choices=choices, value=default_model(username)),
            gr.update(value=core.get_setting(username, 'web_search', True)))


def navigate(choice):
    return [gr.update(visible=choice == section) for section in SECTIONS]


# ------------------------------------------------------------------ research
def _kb():
    from knowledge import services
    return services()


def research_start(topic, pages, scope, auto, request: gr.Request):
    from knowledge import ResearchError
    username = user(request)
    target = 'shared' if scope == 'Общая база' else username
    try:
        job = _kb()['scheduler'].submit(username, topic, target_scope=target, max_pages=int(pages),
                                        auto_accept=bool(auto), is_owner=owner(request))
    except ResearchError as exc:
        return f'❌ {exc}', gr.update()
    _kb()['scheduler'].start(owner_check=lambda u: is_admin_key(u))
    return '✅ Исследование запущено.', gr.update(value=job)


def is_admin_key(storage):
    import supabase_auth
    if storage.startswith('sb:'):
        return bool(supabase_auth.admin_user_id()) and storage == 'sb:' + supabase_auth.admin_user_id()
    return storage == 'owner'


def research_jobs(request: gr.Request):
    username = user(request)
    jobs = _kb()['db'].jobs(username)
    status_ru = {'queued': '⏳ в очереди', 'running': '🔄 идёт', 'done': '✅ готово', 'error': '⚠️ ошибка',
                 'stopped': '⏹ остановлено'}
    rows = [[j['id'][:8], j['topic'][:70], status_ru.get(j['status'], j['status']), f'{int(j["progress"] * 100)}%',
             j['message'][:90], j['created'][:16].replace('T', ' ')] for j in jobs]
    choices = [(f'{j["topic"][:50]} · {status_ru.get(j["status"], j["status"])}', j['id']) for j in jobs]
    return rows, gr.update(choices=choices)


def research_items(job_id, request: gr.Request):
    username = user(request)
    if not job_id:
        return '<div class="chelsy-note">Выбери исследование, чтобы увидеть найденные материалы.</div>', [], gr.update(choices=[])
    job = _kb()['db'].get_job(job_id, username)
    if not job:
        return 'Исследование не найдено.', [], gr.update(choices=[])
    items = _kb()['db'].items(job_id)
    status_ru = {'fetched': 'на проверке', 'accepted': 'в базе', 'rejected': 'отклонено', 'error': 'ошибка',
                 'duplicate': 'дубликат', 'skipped': 'пропущено', 'found': 'найдено'}
    pct = int(job['progress'] * 100)
    head = (f'<div class="chelsy-progress"><div style="width:{pct}%"></div></div>'
            f'<div class="chelsy-note">{html.escape(job["message"])}</div>')
    rows = [[i['id'], status_ru.get(i['status'], i['status']), f'{i["trust"]:.2f}', (i['published'] or '—')[:10],
             i['title'][:80], i['url'], ('⚠️ ' + ', '.join(i['flags']) + ' ' if i['flags'] else '') + i['note'][:120]]
            for i in items]
    choices = [(f'#{i["id"]} {i["title"][:60]}', i['id']) for i in items if i['status'] == 'fetched']
    return head, rows, gr.update(choices=choices, value=[])


def research_review(job_id, selected, action, request: gr.Request):
    from knowledge import ResearchError
    username = user(request)
    done, errors = 0, []
    for item_id in selected or []:
        try:
            if action == 'reject':
                _kb()['scheduler'].reject(int(item_id), username)
            else:
                _kb()['scheduler'].accept(int(item_id), username, 'shared' if action == 'shared' else username,
                                          is_owner=owner(request))
            done += 1
        except ResearchError as exc:
            errors.append(str(exc))
    msg = f'Обработано: {done}.' + (' ' + '; '.join(sorted(set(errors))) if errors else '')
    return (msg,) + research_items(job_id, request)


def research_stop(job_id, request: gr.Request):
    username = user(request)
    if job_id and _kb()['scheduler'].stop(job_id, username):
        return '⏹ Остановка запрошена.'
    return 'Выбери исследование.'


def schedule_add(topic, minutes, request: gr.Request):
    if not owner(request):
        raise gr.Error('Периодические исследования настраивает владелец.')
    username = user(request)
    if not topic or len(topic.strip()) < 3:
        return 'Укажи тему или ссылку.', schedules_md(request)
    _kb()['db'].add_schedule(username, topic.strip(), int(minutes), 'shared', auto_accept=False)
    return '✅ Периодическая проверка добавлена (только разрешённые источники).', schedules_md(request)


def schedules_md(request: gr.Request):
    username = user(request)
    items = _kb()['db'].schedules(username)
    if not items:
        return '_Периодических задач нет._'
    return '\n'.join(f'- **{html.escape(s["topic"])}** — каждые {s["interval_minutes"]} мин, '
                     f'последний запуск: {s["last_run"][:16].replace("T", " ") or "ещё не было"}' for s in items)


def sources_md():
    items = _kb()['db'].sources()
    if not items:
        return '_Разрешённых источников пока нет. Добавь, например, `docs.python.org`._'
    return '\n'.join(f'- `{html.escape(s["domain"])}` · доверие {s["trust"]:.2f}' for s in items)


def source_add(domain, request: gr.Request):
    if not owner(request):
        raise gr.Error('Список источников меняет владелец.')
    try:
        _kb()['db'].allow_source(domain, user(request), 0.75)
    except ValueError as exc:
        return f'❌ {exc}', sources_md()
    return '✅ Источник разрешён.', sources_md()


# ------------------------------------------------------------------ knowledge
def knowledge_view(query, scope, request: gr.Request):
    username = user(request)
    db = _kb()['db']
    target = {'Все': None, 'Личная': username, 'Общая': 'shared'}.get(scope)
    docs = db.list_documents(username, query or '', target, limit=200)
    st = db.stats(username)
    summary = (f'**Документов:** {st["documents"]} · **фрагментов:** {st["chunks"]} · '
               f'**с эмбеддингами:** {st["embedded_chunks"]} · **возможных противоречий:** {st["open_conflicts"]}')
    rows = [[d['id'], 'личная' if d['personal'] else 'общая', d['title'][:80], (d['published'] or '—')[:10],
             d['fetched'][:10], f'{d["trust"]:.2f}', d['version'], d['url']] for d in docs]
    conflicts = db.conflicts(username)
    conflict_md = '\n\n'.join(f'⚖️ «{html.escape(c["claim_a"][:160])}» ([источник]({c["url_a"]})) **vs** '
                              f'«{html.escape(c["claim_b"][:160])}» ([источник]({c["url_b"]}))' for c in conflicts[:10]) \
        or '_Противоречий не обнаружено._'
    return summary, rows, conflict_md


def knowledge_preview(query, request: gr.Request):
    username = user(request)
    if not query or len(query.strip()) < 3:
        return 'Введи запрос из нескольких слов.'
    results = _kb()['retriever'].retrieve(query, username, top=5)
    if not results:
        return 'Ничего не найдено в базе знаний.'
    return '\n\n'.join(f'**{i}. [{html.escape(r["title"])}]({r["url"]})** · доверие {r["trust"]:.2f}'
                       f'{" · " + r["published"][:10] if r["published"] else ""}\n\n> {html.escape(r["text"][:400])}…'
                       for i, r in enumerate(results, 1))


def knowledge_archive(doc_id, request: gr.Request):
    username = user(request)
    try:
        doc = _kb()['db'].get_document(int(doc_id), username)
    except (TypeError, ValueError):
        return 'Укажи номер документа.'
    if not doc:
        return 'Документ не найден.'
    if doc['scope'] == 'shared' and not owner(request):
        return 'Общие документы архивирует владелец.'
    _kb()['db'].archive_document(doc['id'], doc['scope'])
    return '🗄 Документ скрыт из поиска; текст сохранён в истории версий.'


# ------------------------------------------------------------------ memory (0.9.2 API kept)
def memory_list(request: gr.Request):
    username = user(request)
    token = _token(username)
    if token:
        try:
            pull_memories(username[3:], token)
        except Exception:
            pass
    facts = personal_facts(username)
    if not facts:
        return 'Пока нет сохранённых личных фактов. Напиши в чате: `Запомни: любимый цвет = фиолетовый`.'
    return '\n'.join(f'- **{html.escape(key)}** — {html.escape(value)}' for key, value in facts.items())


def memory_delete(memory_key, request: gr.Request):
    username = user(request)
    key = (memory_key or '').strip().lower()
    if not key:
        return 'Укажи ключ воспоминания.'
    if key not in personal_facts(username):
        return 'Такой записи нет.'
    token = _token(username)
    if token:
        try:
            delete_cloud_memory(username[3:], token, key)
        except Exception:
            return 'Не удалось удалить облачную запись; локальная запись сохранена.'
    delete_personal_fact(username, key)
    return '✅ Запись удалена. Нажми «Обновить».'


def save_memory_form(memory_key, memory_value, request: gr.Request):
    """Explicit personal memory action: no language model or web lookup needed."""
    username = user(request)
    key = (memory_key or '').strip().lower()
    value = (memory_value or '').strip()
    if not (2 <= len(key) <= 75 and 1 <= len(value) <= 1000):
        return 'Укажи название (2–75 символов) и значение (1–1000 символов).', gr.update(), gr.update(), memory_list(request)
    store_personal_fact(username, key, value)
    token = _token(username)
    if token:
        try:
            save_memory(username[3:], token, key, value)
        except Exception:
            return 'Сохранено локально, облако пока недоступно.', '', '', memory_list(request)
    return '✅ Сохранено в твою личную память.', '', '', memory_list(request)


# ------------------------------------------------------------------ settings
def settings_load(request: gr.Request):
    from intelligence import ENGINE
    username = user(request)
    s = ENGINE.settings(username)
    return (s['temperature'], s['top_p'], s['num_ctx'], s['num_predict'], s['repeat_penalty'], s['use_memory'],
            s['use_knowledge'], core.get_setting(username, 'web_search', True),
            core.get_setting(username, 'training_consent', False))


def settings_save(model, temperature, top_p, num_ctx, num_predict, repeat_penalty, use_memory, use_knowledge, web,
                  consent, request: gr.Request):
    from intelligence import validate_settings
    username = user(request)
    core.set_setting(username, 'generation', validate_settings({
        'model': model or '', 'temperature': temperature, 'top_p': top_p, 'num_ctx': num_ctx,
        'num_predict': num_predict, 'repeat_penalty': repeat_penalty, 'use_memory': use_memory,
        'use_knowledge': use_knowledge}))
    core.set_setting(username, 'web_search', bool(web))
    core.set_setting(username, 'training_consent', bool(consent))
    return '✅ Настройки сохранены.', gr.update(value=bool(web))


def models_md():
    import ollama_client
    installed = ollama_client.list_models()
    lines = ['**Установлены:** ' + (', '.join(f'`{m}`' for m in installed) if installed else 'нет (Ollama не запущена?)'), '',
             '**Рекомендации для качественных ответов:**']
    for r in ollama_client.RECOMMENDED:
        mark = '✅' if r['name'] in installed else '⬇️'
        lines.append(f'- {mark} `{r["name"]}` — ~{r["ram_gb"]} ГБ RAM. {r["note"]}')
    lines.append('\nУстановка: `ollama pull qwen3:8b`. Для семантического поиска по базе знаний: `ollama pull nomic-embed-text`.')
    return '\n'.join(lines)


EVAL_STATE = {'running': False, 'text': ''}


def run_model_eval(request: gr.Request):
    if not owner(request):
        raise gr.Error('Оценку моделей запускает владелец.')
    if EVAL_STATE['running']:
        return 'Оценка уже идёт…'

    def job():
        import model_eval
        import ollama_client
        EVAL_STATE.update(running=True, text='Идёт оценка…')
        try:
            models = ollama_client.list_models()
            results = model_eval.evaluate(models)
            path = model_eval.write_report(results, core.DATA / 'model_eval')
            EVAL_STATE['text'] = '\n'.join(f'- `{r["model"]}` — **{r["score"]:.2f}**, ~{r["avg_seconds"]} с/ответ'
                                           for r in results) + f'\n\nПодробный отчёт: `{path}`'
        except Exception as exc:
            EVAL_STATE['text'] = f'Оценка не удалась: {type(exc).__name__}'
        finally:
            EVAL_STATE['running'] = False
    threading.Thread(target=job, daemon=True).start()
    return 'Оценка запущена — это может занять несколько минут. Нажми «Показать результат».'


def manual_sync(request: gr.Request):
    username = user(request)
    if not username.startswith('sb:'):
        return pulse_html('ready', 'Облачная синхронизация доступна при входе через аккаунт Chelsy Group')
    state = _cloud_user_sync(username)
    return pulse_html('ready' if state == 'ok' else 'offline',
                      'Синхронизировано' if state == 'ok' else 'Облако недоступно — изменения сохранены локально')


# ------------------------------------------------------------------ legacy research (owner)
def seed(url, request: gr.Request):
    if not owner(request):
        raise gr.Error('Исследованиями может управлять только владелец.')
    try:
        return f'✅ Добавлено в очередь: {add_seed(url)}'
    except ValueError as exc:
        return f'❌ {exc}'


CSS = r'''
:root, .dark {
  --chelsy-bg:#0B0C20; --chelsy-panel:#11132B; --chelsy-panel-2:#161937; --chelsy-stroke:#262A52;
  --chelsy-violet:#6D4AFF; --chelsy-lavender:#A78BFA; --chelsy-teal:#21D9DA; --chelsy-text:#ECEBFA; --chelsy-muted:#A3A6C8;
  --body-background-fill:#0B0C20; --background-fill-primary:#0B0C20; --background-fill-secondary:#11132B;
  --block-background-fill:#11132B; --block-border-color:#262A52; --border-color-primary:#262A52;
  --input-background-fill:#161937; --body-text-color:#ECEBFA; --body-text-color-subdued:#A3A6C8;
  --button-primary-background-fill:#6D4AFF; --button-primary-background-fill-hover:#7d5dff;
  --button-primary-text-color:#fff; --color-accent:#6D4AFF; --color-accent-soft:#2a2160;
  --link-text-color:#A78BFA; --link-text-color-hover:#21D9DA;
}
body, gradio-app, .gradio-container { background:var(--chelsy-bg) !important; color:var(--chelsy-text) !important;
  font-family:Inter, 'Segoe UI', system-ui, -apple-system, sans-serif !important; }
.gradio-container { max-width:100% !important; padding:0 !important; }
footer { display:none !important; }
/* sidebar */
.sidebar { background:var(--chelsy-panel) !important; border-right:1px solid var(--chelsy-stroke) !important; }
.chelsy-brand { display:flex; align-items:center; gap:12px; padding:4px 2px 10px; }
.chelsy-brand-avatar { width:44px; height:44px; border-radius:14px; box-shadow:0 0 0 1px #6D4AFF55, 0 0 18px #6D4AFF33; }
.chelsy-brand-name { font-weight:700; font-size:18px; letter-spacing:-.3px; }
.chelsy-brand-sub { font-size:11px; color:var(--chelsy-muted); }
#chelsy-new { border-radius:12px !important; font-weight:600 !important; }
.chelsy-section { font-size:11px; font-weight:700; letter-spacing:.08em; color:var(--chelsy-muted); margin:10px 2px 2px; text-transform:uppercase; }
.conv-list .wrap { flex-direction:column !important; gap:2px !important; }
.conv-list label { width:100%; border-radius:10px !important; padding:7px 10px !important; background:transparent !important;
  border:0 !important; font-size:13px !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.conv-list label:hover { background:#1c2046 !important; }
.conv-list label.selected, .conv-list label:has(input:checked) { background:#2a2160 !important; color:#fff !important; }
.conv-list input { display:none !important; }
.conv-list { max-height:34vh; overflow-y:auto; }
.chelsy-nav button { justify-content:flex-start !important; background:transparent !important; border:0 !important;
  border-radius:10px !important; font-weight:500 !important; color:var(--chelsy-text) !important; }
.chelsy-nav button:hover { background:#1c2046 !important; }
.chelsy-profile { display:flex; align-items:center; gap:10px; padding:10px; border-radius:12px; background:var(--chelsy-panel-2); margin-top:8px; }
.chelsy-profile .avatar { width:32px; height:32px; border-radius:50%; display:grid; place-items:center; font-weight:700;
  background:linear-gradient(135deg,#6D4AFF,#21D9DA); color:#fff; }
.chelsy-profile .name { font-size:13px; max-width:150px; overflow:hidden; text-overflow:ellipsis; }
.chelsy-profile .role { font-size:11px; color:var(--chelsy-muted); }
.chelsy-profile .logout { margin-left:auto; color:var(--chelsy-muted); text-decoration:none; font-size:18px; }
/* main */
#chelsy-main { padding:10px clamp(10px, 3vw, 42px) 8px !important; max-width:1180px; margin:0 auto; width:100%; }
#chelsy-topbar { align-items:center; gap:8px; border-bottom:1px solid var(--chelsy-stroke); padding-bottom:6px; }
.chelsy-conv-title { font-weight:600; font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.chelsy-conv-title .fav { color:#ffd166; }
#chelsy-topbar button { min-width:0 !important; }
#chelsy-chat { border:0 !important; background:transparent !important; }
#chelsy-chat .message { font-size:15px; line-height:1.62; }
#chelsy-chat .message-row.user-row .message, #chelsy-chat .user { background:#231d4f !important; border:1px solid #3a2f7d !important; }
#chelsy-chat .message-row.bot-row .message, #chelsy-chat .bot { background:transparent !important; border:0 !important; }
#chelsy-chat pre, #chelsy-chat .code_wrap pre { border:1px solid var(--chelsy-stroke); border-radius:10px; background:#0E1029 !important; color:#E6E3FF !important; }
#chelsy-chat code { background:#1A1D40; color:#E6E3FF; }
#chelsy-compose .submit-button { background:#6D4AFF !important; color:#fff !important; border-radius:12px !important; }
#chelsy-compose .stop-button { background:#3A1630 !important; color:#FFD7E4 !important; border-radius:12px !important; }
#chelsy-compose .upload-button { color:var(--chelsy-lavender) !important; }
#chelsy-topbar button { background:#1A1D40 !important; color:#E6E3FF !important; border:1px solid #2E3366 !important; max-width:44px; }
#chelsy-chat a { color:var(--chelsy-lavender); }
#chelsy-compose { border:1px solid #3a3474 !important; border-radius:18px !important; background:var(--chelsy-panel-2) !important;
  box-shadow:0 8px 32px #6D4AFF14 !important; }
#chelsy-compose:focus-within { border-color:var(--chelsy-violet) !important; box-shadow:0 0 0 3px #6D4AFF33 !important; }
#chelsy-compose textarea { font-size:15px !important; line-height:1.5 !important; }
#chelsy-options { align-items:center; gap:10px; }
#chelsy-options .hint { font-size:12px; color:var(--chelsy-muted); }
.chelsy-welcome { text-align:center; padding:48px 10px 10px; color:var(--chelsy-muted); }
.chelsy-welcome img { width:72px; height:72px; border-radius:22px; box-shadow:0 0 28px #6D4AFF44; }
.chelsy-welcome h2 { color:var(--chelsy-text); margin:14px 0 6px; font-size:24px; }
/* Chelsy Pulse */
.chelsy-pulse-row { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--chelsy-muted); flex-wrap:wrap; }
.chelsy-pulse { position:relative; width:12px; height:12px; display:inline-block; }
.chelsy-pulse i { position:absolute; inset:2px; border-radius:50%; background:var(--pc); }
.chelsy-pulse::after { content:''; position:absolute; inset:0; border-radius:50%; border:2px solid var(--pc); opacity:.0; }
.pulse-ready { --pc:#21D9DA; } .pulse-generating { --pc:#A78BFA; } .pulse-checking { --pc:#A78BFA; }
.pulse-searching { --pc:#21D9DA; } .pulse-syncing { --pc:#6D4AFF; } .pulse-offline { --pc:#ff8a65; }
.pulse-generating::after, .pulse-checking::after, .pulse-searching::after, .pulse-syncing::after { animation:chelsy-ring 1.6s ease-out infinite; }
.pulse-ready i { animation:chelsy-breathe 4s ease-in-out infinite; }
@keyframes chelsy-ring { 0% { transform:scale(.6); opacity:.9 } 100% { transform:scale(1.9); opacity:0 } }
@keyframes chelsy-breathe { 50% { opacity:.55 } }
.pulse-meta { margin-left:6px; opacity:.85; }
/* pages */
.chelsy-page h2 { font-size:22px; margin:6px 0 4px; }
.chelsy-note { font-size:12.5px; color:var(--chelsy-muted); margin:4px 0; }
.chelsy-progress { height:6px; background:#1c2046; border-radius:6px; overflow:hidden; margin:6px 0; }
.chelsy-progress div { height:100%; background:linear-gradient(90deg,#6D4AFF,#21D9DA); transition:width .4s; }
.chelsy-card { background:var(--chelsy-panel); border:1px solid var(--chelsy-stroke); border-radius:14px; padding:14px; }
button:focus-visible, input:focus-visible, textarea:focus-visible, a:focus-visible { outline:2px solid var(--chelsy-teal) !important; outline-offset:2px; }
@media (max-width: 760px) {
  #chelsy-main { padding:6px 8px !important; }
  #chelsy-chat .message { font-size:14.5px; }
  .chelsy-welcome { padding-top:22px; }
  #chelsy-options .hint { display:none; }
  #chelsy-topbar .pulse-label, #chelsy-topbar .pulse-meta { display:none; }
  #chelsy-topbar { flex-wrap:nowrap !important; }
  #chelsy-topbar > div:first-child { min-width:0 !important; }
}
@media (prefers-reduced-motion: reduce) { .chelsy-pulse::after, .chelsy-pulse i, .chelsy-progress div { animation:none !important; transition:none !important; } }
'''

FORCE_DARK_JS = '''() => {
  console.log('chelsy-js');
  document.documentElement.classList.add('dark');
  document.body.classList.add('dark');
  // On phones the sidebar starts collapsed so the chat is visible first.
  if (window.innerWidth < 760) {
    setTimeout(() => {
      const toggle = document.querySelector('.sidebar .toggle-button, button.toggle-button');
      const sidebar = document.querySelector('.sidebar');
      if (toggle && sidebar && sidebar.classList.contains('open')) toggle.click();
    }, 400);
  }
}'''


def build():
    with gr.Blocks(title='Chelsy AI', fill_height=True) as demo:
        conversation = gr.State(None)
        message_ids = gr.State([])
        with gr.Sidebar(open=True, width=292, elem_id='chelsy-sidebar'):
            gr.HTML(brand_header_html())
            new_btn = gr.Button('＋  Новый чат', variant='primary', elem_id='chelsy-new')
            history_search = gr.Textbox(placeholder='🔍 Поиск по истории…', show_label=False, container=False,
                                        elem_id='chelsy-search')
            gr.HTML('<div class="chelsy-section">Избранное</div>')
            favorites = gr.Radio(choices=[], show_label=False, container=False, elem_classes='conv-list')
            gr.HTML('<div class="chelsy-section">Недавние</div>')
            recent = gr.Radio(choices=[], show_label=False, container=False, elem_classes='conv-list')
            gr.HTML('<div class="chelsy-section">Разделы</div>')
            with gr.Column(elem_classes='chelsy-nav'):
                nav_buttons = [gr.Button(label, size='sm') for label in SECTIONS]
            profile = gr.HTML()
        with gr.Column(elem_id='chelsy-main'):
            with gr.Row(elem_id='chelsy-topbar'):
                title = gr.HTML(title_html(None, None), scale=4)
                pulse = gr.HTML(pulse_html('ready'), scale=3, elem_id='chelsy-pulse-box')
                fav_btn = gr.Button('☆', size='sm', scale=0, min_width=40)
                sync_btn = gr.Button('⟳', size='sm', scale=0, min_width=40)
            with gr.Column(visible=True, elem_classes='chelsy-page') as chat_page:
                chatbot = gr.Chatbot(elem_id='chelsy-chat', show_label=False, height='68vh', editable='user',
                                     buttons=['copy', 'copy_all'], feedback_options=('Like', 'Dislike'),
                                     avatar_images=(None, str(asset_path('chelsy_logo_small.png'))),
                                     placeholder=welcome_html(), allow_tags=False, layout='bubble',
                                     latex_delimiters=[{'left': '$$', 'right': '$$', 'display': True}])
                composer = gr.MultimodalTextbox(placeholder='Спроси Chelsy о чём угодно…',
                                                show_label=False, file_types=TEXT_TYPES, file_count='multiple',
                                                lines=1, max_lines=12, submit_btn='➤', stop_btn='■', elem_id='chelsy-compose',
                                                sources=['upload'])
                with gr.Row(elem_id='chelsy-options'):
                    model = gr.Dropdown(model_choices(), value=None, label='Модель', container=False, scale=2,
                                        min_width=160, allow_custom_value=False)
                    web = gr.Checkbox(value=True, label='🌐 Поиск в интернете', container=False, scale=1, min_width=170)
                    regen_btn = gr.Button('↻ Повторить ответ', size='sm', scale=0, min_width=150)
                    gr.HTML('<span class="hint">Enter — отправить, Shift+Enter — новая строка. Ответы могут содержать ошибки — проверяй важное.</span>')
                with gr.Accordion('Переименовать или удалить чат', open=False):
                    with gr.Row():
                        new_title = gr.Textbox(placeholder='Новое название', show_label=False, scale=4)
                        rename_btn = gr.Button('Переименовать', scale=1)
                        delete_btn = gr.Button('🗑 Удалить чат', variant='stop', scale=1)
            with gr.Column(visible=False, elem_classes='chelsy-page') as research_page:
                gr.HTML('<h2>🔎 Исследования</h2><div class="chelsy-note">Chelsy читает открытые источники с соблюдением '
                        'robots.txt и ограничений частоты. Найденное попадает в базу знаний только после твоего решения. '
                        'Это пополнение базы знаний (RAG), а не переобучение модели.</div>')
                with gr.Row():
                    topic = gr.Textbox(label='Тема или ссылка', placeholder='Например: документация FastAPI или https://docs.python.org/3/tutorial/', scale=4)
                    pages = gr.Slider(1, 25, value=8, step=1, label='Страниц', scale=1)
                with gr.Row():
                    scope = gr.Radio(['Личная база', 'Общая база'], value='Личная база', label='Куда добавлять', scale=2)
                    auto = gr.Checkbox(label='Добавлять без проверки', value=False, scale=1)
                    start_btn = gr.Button('▶ Начать исследование', variant='primary', scale=1)
                research_msg = gr.Markdown()
                jobs_table = gr.Dataframe(headers=['ID', 'Тема', 'Статус', 'Прогресс', 'Сообщение', 'Создано'],
                                          interactive=False, wrap=True, label='Задачи')
                with gr.Row():
                    job_pick = gr.Dropdown(choices=[], label='Открыть исследование', scale=3)
                    stop_job_btn = gr.Button('⏹ Остановить', scale=1)
                    refresh_jobs = gr.Button('↻ Обновить', scale=1)
                job_head = gr.HTML()
                items_table = gr.Dataframe(headers=['#', 'Статус', 'Доверие', 'Дата', 'Заголовок', 'Ссылка', 'Проверка'],
                                           interactive=False, wrap=True, label='Найденные материалы')
                item_pick = gr.CheckboxGroup(choices=[], label='Выбери материалы для решения')
                with gr.Row():
                    accept_personal = gr.Button('➕ В личную базу', variant='primary')
                    accept_shared = gr.Button('➕ В общую базу (владелец)')
                    reject_btn = gr.Button('✖ Отклонить')
                review_msg = gr.Markdown()
                with gr.Accordion('Автономные исследования и разрешённые источники (владелец)', open=False):
                    gr.Markdown('Периодические задачи работают только по **разрешённым доменам**, пока запущен Host или Desktop.')
                    with gr.Row():
                        sched_topic = gr.Textbox(label='Тема или ссылка для регулярной проверки', scale=3)
                        sched_minutes = gr.Slider(60, 10080, value=1440, step=60, label='Интервал, минут', scale=2)
                        sched_btn = gr.Button('Добавить', scale=1)
                    sched_msg = gr.Markdown()
                    sched_list = gr.Markdown()
                    with gr.Row():
                        source_domain = gr.Textbox(label='Разрешить домен', placeholder='docs.python.org', scale=3)
                        source_btn = gr.Button('Разрешить', scale=1)
                    source_msg = gr.Markdown()
                    source_list = gr.Markdown()
                research_timer = gr.Timer(4, active=False)
            with gr.Column(visible=False, elem_classes='chelsy-page') as memory_page:
                gr.HTML('<h2>💾 Личная память</h2><div class="chelsy-note">Записи принадлежат только твоему аккаунту '
                        'и синхронизируются через Supabase. В чате: «Запомни: любимая игра = шахматы».</div>')
                with gr.Row():
                    new_key = gr.Textbox(label='Что запомнить?', placeholder='Название проекта', scale=2)
                    new_value = gr.Textbox(label='Значение', placeholder='Chelsy AI', scale=3)
                    save_fact = gr.Button('💜 Сохранить', variant='primary', scale=1)
                save_fact_status = gr.Markdown()
                memories = gr.Markdown()
                with gr.Row():
                    memory_key = gr.Textbox(label='Ключ записи для удаления', placeholder='любимая игра', scale=3)
                    remove_memory = gr.Button('Удалить запись', variant='stop', scale=1)
                    refresh_memory = gr.Button('↻ Обновить', scale=1)
                memory_status = gr.Markdown()
            with gr.Column(visible=False, elem_classes='chelsy-page') as knowledge_page:
                gr.HTML('<h2>📚 База знаний</h2><div class="chelsy-note">Документы, которые Chelsy использует в ответах. '
                        'Обновления сохраняют прошлые версии; удаление — это архивирование.</div>')
                with gr.Row():
                    kb_query = gr.Textbox(label='Фильтр', placeholder='Название, тема или адрес', scale=3)
                    kb_scope = gr.Radio(['Все', 'Личная', 'Общая'], value='Все', label='База', scale=2)
                    kb_refresh = gr.Button('↻ Показать', scale=1)
                kb_summary = gr.Markdown()
                kb_table = gr.Dataframe(headers=['ID', 'База', 'Заголовок', 'Опубликовано', 'Получено', 'Доверие', 'Версия', 'Ссылка'],
                                        interactive=False, wrap=True)
                with gr.Row():
                    kb_search = gr.Textbox(label='Проверить поиск по базе', placeholder='О чём спросить базу?', scale=4)
                    kb_search_btn = gr.Button('Найти', scale=1)
                kb_results = gr.Markdown()
                gr.Markdown('### ⚖️ Возможные противоречия между источниками')
                kb_conflicts = gr.Markdown()
                with gr.Row():
                    kb_doc = gr.Number(label='ID документа', precision=0, scale=2)
                    kb_archive_btn = gr.Button('🗄 Архивировать', scale=1)
                kb_archive_msg = gr.Markdown()
            with gr.Column(visible=False, elem_classes='chelsy-page') as settings_page:
                gr.HTML('<h2>⚙️ Настройки</h2>')
                with gr.Row():
                    with gr.Column():
                        gr.Markdown('#### Генерация')
                        temperature = gr.Slider(0, 1.5, value=0.6, step=0.05, label='Температура (творчество)')
                        top_p = gr.Slider(0.1, 1, value=0.9, step=0.05, label='Top-p')
                        num_ctx = gr.Slider(2048, 32768, value=8192, step=1024, label='Контекстное окно, токенов')
                        num_predict = gr.Slider(128, 4096, value=1024, step=64, label='Максимальная длина ответа')
                        repeat_penalty = gr.Slider(1.0, 1.5, value=1.1, step=0.01, label='Штраф за повторы')
                    with gr.Column():
                        gr.Markdown('#### Поведение')
                        use_memory = gr.Checkbox(value=True, label='Учитывать личную память')
                        use_knowledge = gr.Checkbox(value=True, label='Использовать базу знаний')
                        web_default = gr.Checkbox(value=True, label='Поиск в интернете по умолчанию')
                        consent = gr.Checkbox(value=False, label='Разрешить использовать оценённые 👍 ответы для будущего дообучения')
                        save_settings = gr.Button('Сохранить настройки', variant='primary')
                        settings_msg = gr.Markdown()
                gr.Markdown('#### Модели')
                models_info = gr.Markdown()
                with gr.Row():
                    eval_btn = gr.Button('🧪 Оценить установленные модели (владелец)')
                    eval_show = gr.Button('Показать результат')
                eval_msg = gr.Markdown()
                gr.Markdown('''#### Подключения и безопасность
- **Chelsy Host** слушает только `127.0.0.1:8787`. Для телефона и второго ПК используй **Tailscale Serve**: `tailscale serve --bg 8787`. Не открывай порт на роутере и не используй Funnel.
- **Ollama** доступна только локально (`127.0.0.1:11434`) и никогда не публикуется в сеть.
- **Аккаунты** — единый Supabase Auth Chelsy Group (сайт, Windows, Android). Секретные ключи в клиентах не хранятся.
- **Данные** — `%LOCALAPPDATA%\\ChelsyAI\\data`; перед каждым обновлением схемы создаётся резервная копия в `data\\backups`.''')

        pages_ = [chat_page, research_page, memory_page, knowledge_page, settings_page]
        for label, button in zip(SECTIONS, nav_buttons):
            button.click(lambda lab=label: navigate(lab) + [gr.Timer(active=lab == SECTIONS[1])],
                         outputs=pages_ + [research_timer], api_name=False)

        chat_outputs = [chatbot, message_ids, pulse]
        submit = composer.submit(send, inputs=[composer, conversation, model, web],
                                 outputs=chat_outputs + [conversation, composer], api_name=False, concurrency_limit=1)
        submit.then(refresh_lists, inputs=[history_search, conversation], outputs=[favorites, recent], api_name=False) \
              .then(title_html_req, inputs=[conversation], outputs=[title], api_name=False)
        composer.stop(stop_generation, outputs=[pulse], api_name=False, queue=False)
        chatbot.retry(regenerate, inputs=[conversation, model, web, message_ids], outputs=chat_outputs, api_name=False,
                      concurrency_limit=1)
        regen_btn.click(regenerate, inputs=[conversation, model, web, message_ids], outputs=chat_outputs, api_name=False,
                        concurrency_limit=1)
        chatbot.edit(edit_message, inputs=[conversation, model, web, message_ids], outputs=chat_outputs, api_name=False,
                     concurrency_limit=1)
        chatbot.like(rate, inputs=[message_ids], outputs=None, api_name=False)
        new_btn.click(new_chat, outputs=[conversation, chatbot, message_ids, title, favorites, recent, composer],
                      api_name=False).then(lambda: navigate(SECTIONS[0]), outputs=pages_, api_name=False)
        chatbot.clear(new_chat, outputs=[conversation, chatbot, message_ids, title, favorites, recent, composer], api_name=False)
        for lst in (favorites, recent):
            lst.input(open_conversation, inputs=[lst], outputs=[conversation, chatbot, message_ids, title], api_name=False) \
               .then(lambda: navigate(SECTIONS[0]), outputs=pages_, api_name=False)
        history_search.change(refresh_lists, inputs=[history_search, conversation], outputs=[favorites, recent],
                              api_name=False, trigger_mode='always_last')
        fav_btn.click(toggle_favorite, inputs=[conversation, history_search], outputs=[title, favorites, recent], api_name=False)
        rename_btn.click(rename_current, inputs=[conversation, new_title, history_search],
                         outputs=[title, new_title, favorites, recent], api_name=False)
        delete_btn.click(delete_current, inputs=[conversation, history_search],
                         outputs=[conversation, chatbot, message_ids, title, favorites, recent], api_name=False)
        sync_btn.click(lambda: pulse_html('syncing'), outputs=[pulse], api_name=False, queue=False) \
                .then(manual_sync, outputs=[pulse], api_name=False)

        start_btn.click(research_start, inputs=[topic, pages, scope, auto], outputs=[research_msg, job_pick], api_name=False) \
                 .then(research_jobs, outputs=[jobs_table, job_pick], api_name=False)
        refresh_jobs.click(research_jobs, outputs=[jobs_table, job_pick], api_name=False)
        job_pick.change(research_items, inputs=[job_pick], outputs=[job_head, items_table, item_pick], api_name=False)
        stop_job_btn.click(research_stop, inputs=[job_pick], outputs=[research_msg], api_name=False)
        accept_personal.click(review_personal, inputs=[job_pick, item_pick], outputs=[review_msg, job_head, items_table, item_pick], api_name=False)
        accept_shared.click(review_shared, inputs=[job_pick, item_pick], outputs=[review_msg, job_head, items_table, item_pick], api_name=False)
        reject_btn.click(review_reject, inputs=[job_pick, item_pick], outputs=[review_msg, job_head, items_table, item_pick], api_name=False)
        research_timer.tick(research_jobs, outputs=[jobs_table, job_pick], api_name=False, show_progress='hidden')
        sched_btn.click(schedule_add, inputs=[sched_topic, sched_minutes], outputs=[sched_msg, sched_list], api_name=False)
        source_btn.click(source_add, inputs=[source_domain], outputs=[source_msg, source_list], api_name=False)

        save_fact.click(save_memory_form, inputs=[new_key, new_value], outputs=[save_fact_status, new_key, new_value, memories],
                        api_name=False)
        refresh_memory.click(memory_list, outputs=[memories], api_name=False)
        remove_memory.click(memory_delete, inputs=[memory_key], outputs=[memory_status], api_name=False)

        kb_refresh.click(knowledge_view, inputs=[kb_query, kb_scope], outputs=[kb_summary, kb_table, kb_conflicts], api_name=False)
        kb_search_btn.click(knowledge_preview, inputs=[kb_search], outputs=[kb_results], api_name=False)
        kb_archive_btn.click(knowledge_archive, inputs=[kb_doc], outputs=[kb_archive_msg], api_name=False)

        save_settings.click(settings_save, inputs=[model, temperature, top_p, num_ctx, num_predict, repeat_penalty, use_memory,
                                                   use_knowledge, web_default, consent],
                            outputs=[settings_msg, web], api_name=False)
        eval_btn.click(run_model_eval, outputs=[eval_msg], api_name=False)
        eval_show.click(lambda: EVAL_STATE['text'] or 'Результатов пока нет.', outputs=[eval_msg], api_name=False)

        status_timer = gr.Timer(20)
        status_timer.tick(status_html, outputs=[pulse], api_name=False, show_progress='hidden')
        demo.load(initial_load, outputs=[conversation, chatbot, message_ids, title, favorites, recent, profile, model, web],
                  api_name=False)
        demo.load(status_html, outputs=[pulse], api_name=False)
        demo.load(memory_list, outputs=[memories], api_name=False)
        demo.load(settings_load, outputs=[temperature, top_p, num_ctx, num_predict, repeat_penalty, use_memory, use_knowledge,
                                          web_default, consent], api_name=False)
        demo.load(models_md, outputs=[models_info], api_name=False)
        demo.load(research_jobs, outputs=[jobs_table, job_pick], api_name=False)
        demo.load(schedules_md, outputs=[sched_list], api_name=False)
        demo.load(sources_md, outputs=[source_list], api_name=False)
        demo.load(knowledge_view, inputs=[kb_query, kb_scope], outputs=[kb_summary, kb_table, kb_conflicts], api_name=False)
    return demo


def title_html_req(conversation_id, request: gr.Request):
    return title_html(user(request), conversation_id)


def review_personal(job_id, selected, request: gr.Request):
    return research_review(job_id, selected, 'personal', request)


def review_shared(job_id, selected, request: gr.Request):
    return research_review(job_id, selected, 'shared', request)


def review_reject(job_id, selected, request: gr.Request):
    return research_review(job_id, selected, 'reject', request)


THEME_VALUES = {
    'body_background_fill': '#0B0C20', 'body_text_color': '#ECEBFA', 'body_text_color_subdued': '#A3A6C8',
    'background_fill_primary': '#0B0C20', 'background_fill_secondary': '#11132B',
    'block_background_fill': '#11132B', 'block_border_color': '#262A52', 'block_label_background_fill': '#161937',
    'block_label_text_color': '#A3A6C8', 'block_title_text_color': '#ECEBFA', 'border_color_primary': '#262A52',
    'border_color_accent': '#6D4AFF', 'color_accent': '#6D4AFF', 'color_accent_soft': '#2A2160',
    'input_background_fill': '#161937', 'input_background_fill_focus': '#1B1F45', 'input_border_color': '#2E3366',
    'input_border_color_focus': '#6D4AFF', 'input_placeholder_color': '#7F84AE',
    'button_primary_background_fill': '#6D4AFF', 'button_primary_background_fill_hover': '#7D5DFF',
    'button_primary_text_color': '#FFFFFF', 'button_primary_border_color': '#6D4AFF',
    'button_secondary_background_fill': '#1A1D40', 'button_secondary_background_fill_hover': '#232857',
    'button_secondary_text_color': '#E6E3FF', 'button_secondary_border_color': '#2E3366',
    'button_cancel_background_fill': '#3A1630', 'button_cancel_text_color': '#FFD7E4',
    'code_background_fill': '#0E1029', 'link_text_color': '#A78BFA', 'link_text_color_hover': '#21D9DA',
    'link_text_color_visited': '#A78BFA', 'checkbox_background_color': '#161937',
    'checkbox_background_color_selected': '#6D4AFF', 'checkbox_border_color': '#3A3F75',
    'checkbox_label_background_fill': '#161937', 'checkbox_label_background_fill_selected': '#2A2160',
    'checkbox_label_text_color': '#ECEBFA', 'checkbox_label_text_color_selected': '#FFFFFF',
    'table_even_background_fill': '#11132B', 'table_odd_background_fill': '#151836', 'table_border_color': '#262A52',
    'table_row_focus': '#2A2160', 'slider_color': '#6D4AFF', 'panel_background_fill': '#11132B',
    'panel_border_color': '#262A52', 'shadow_drop': 'none', 'shadow_drop_lg': '0 10px 30px #00000055',
    'chatbot_text_size': '15px', 'color_accent_soft_dark': '#2A2160',
}


def chelsy_theme():
    """Brand dark theme applied to both Gradio light and dark modes."""
    theme = gr.themes.Soft(primary_hue='violet', secondary_hue='cyan', neutral_hue='slate',
                           # Local system fonts only: no requests to external font CDNs.
                           font=[gr.themes.Font('Segoe UI'), gr.themes.Font('system-ui'), gr.themes.Font('sans-serif')],
                           font_mono=[gr.themes.Font('Cascadia Code'), gr.themes.Font('Consolas'), gr.themes.Font('monospace')])
    values = {}
    for key, value in THEME_VALUES.items():
        base = key[:-5] if key.endswith('_dark') else key
        values[base] = value
        values[base + '_dark'] = value
    valid = {k: v for k, v in values.items() if hasattr(theme, k)}
    return theme.set(**valid)


HEAD = '''<meta name="color-scheme" content="dark">
<script>
(function () {
  // Phones: start with the sidebar collapsed so the chat is visible first.
  var started = Date.now();
  function collapse() {
    if (window.innerWidth >= 760 || Date.now() - started > 8000) return;
    var sidebar = document.querySelector('.sidebar');
    var toggle = document.querySelector('.sidebar .toggle-button, button.toggle-button');
    if (sidebar && toggle && sidebar.classList.contains('open') && document.querySelector('#chelsy-chat')) {
      setTimeout(function () { if (sidebar.classList.contains('open')) toggle.click(); }, 300);
      started = 0;   // only once
    }
  }
  // Gradio may inject this <head> snippet after DOMContentLoaded: observe right away.
  var observer = new MutationObserver(collapse);
  observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['class']});
  setTimeout(function () { observer.disconnect(); }, 9000);
  collapse();
})();
</script>'''


def launch_kwargs():
    return {'css': CSS, 'js': FORCE_DARK_JS, 'head': HEAD, 'theme': chelsy_theme()}


if __name__ == '__main__':
    if not USERS_PATH.exists():
        from supabase_auth import config as _cfg
        if _cfg() is None:
            raise SystemExit('Создай аккаунты через setup_users.py или запусти ChelsyAI.exe')
    demo = build()
    demo.queue(default_concurrency_limit=1, api_open=False)
    demo.launch(server_name='127.0.0.1', server_port=7860, share=False, auth=authenticate,
                auth_message='Вход в Chelsy AI', inbrowser=True, show_error=False, enable_monitoring=False,
                **launch_kwargs())
