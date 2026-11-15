"""Chelsy AI Desktop 0.9: accessible, branded local desktop interface."""
import html
import base64
from pathlib import Path
import sys
import threading
import time

import gradio as gr

from core import (
    USERS_PATH, MODEL, add_seed, answer, clear_history,
    crawl, history, ollama_available, stats, personal_facts,
    delete_personal_fact, store_personal_fact,
)

def asset_path(name):
    """Resolve bundled resources both from source and PyInstaller one-dir."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'assets' / name


def brand_header_html():
    """Local, embedded brand image avoids external network assets in the UI."""
    logo = asset_path('chelsy_logo_small.png').read_bytes()
    encoded = base64.b64encode(logo).decode('ascii')
    return (
        '<div class="chelsy-brand">'
        f'<img class="chelsy-brand-avatar" alt="Chelsy AI logo" src="data:image/png;base64,{encoded}">'
        '<div><h1>Chelsy AI</h1>'
        '<p>Твой интеллект · Desktop 0.9 · закрытая бета</p></div>'
        '<div class="chelsy-brand-tag">✦ CHELSY</div></div>'
    )


LOCK = threading.Lock()
STATE = {'active': False, 'enabled': False, 'lines': ['Пока нет исследований.'], 'minutes': 60, 'pages': 5, 'generation': 0}


from desktop_auth import authenticate, account_name, is_admin, bearer_for
from cloud_sync import sync as sync_cloud, pull_memories, push_memories, save_memory, delete_cloud_memory, delete_cloud_history, SyncError

def user(request: gr.Request):
    raw = request.username if request is not None else None
    name = account_name(raw)
    if not name:
        raise gr.Error('Войди в Chelsy AI.')
    return name


def _cloud_user_sync(username):
    if not username.startswith('sb:'):
        return
    try:
        token=bearer_for(username)
        sync_cloud(username[3:],token)
        pull_memories(username[3:],token)
    except (SyncError,Exception):
        # A failed network sync must never destroy local chats.
        pass


def get_history(request: gr.Request):
    username=user(request)
    _cloud_user_sync(username)
    return history(username)


def chat(message, model_name, request: gr.Request):
    model_name = (model_name or MODEL).strip()
    # Keep the model value limited to an exact, predefined local Ollama model name.
    if model_name not in {'llama3.2:1b', 'qwen3:4b'}:
        raise gr.Error('Неизвестная модель Ollama.')
    username=user(request)
    _cloud_user_sync(username)
    result=answer(message, username, model=model_name)
    if username.startswith('sb:'):
        try:
            sync_cloud(username[3:],bearer_for(username))
            push_memories(username[3:],bearer_for(username))
        except (SyncError,Exception): pass
    return result


def erase(request: gr.Request):
    username=user(request)
    if username.startswith('sb:'):
        try: delete_cloud_history(username[3:],bearer_for(username))
        except Exception: raise gr.Error('Не удалось удалить облачную историю; локальная история сохранена.')
    clear_history(username)
    return [], 'История очищена только для текущего аккаунта.'


def pulse(request: gr.Request):
    username = html.escape(user(request))
    connected = ollama_available()
    count, pending = stats()
    status = 'Модель подключена' if connected else 'Ollama недоступна'
    color = '#70e9b4' if connected else '#f5b96c'
    return f'''<div class="chelsy-status-line">
        <span class="chelsy-pulse" style="--pulse-color:{color}"></span>
        <span>{status}</span><span class="chelsy-status-separator">·</span>
        <span>{username}</span><span class="chelsy-status-separator">·</span>
        <span>{count} страниц в памяти</span><span class="chelsy-status-separator">·</span>
        <span>{pending} в очереди</span>
    </div>'''


def is_owner(request: gr.Request):
    if not is_admin(request.username if request else None):
        raise gr.Error('Исследованиями может управлять только владелец.')


def seed(url, request: gr.Request):
    is_owner(request)
    try:
        checked = add_seed(url)
        return f'✅ Добавлено в очередь: {checked}'
    except ValueError as exc:
        return f'❌ {exc}'


def run_research(pages, request: gr.Request):
    is_owner(request)
    with LOCK:
        if STATE['active']:
            return 'Исследование уже выполняется.'
        STATE['active'] = True
    try:
        messages = crawl(int(pages))
        with LOCK:
            STATE['lines'] = (STATE['lines'] + messages)[-100:]
        return '\n'.join(messages)
    finally:
        with LOCK:
            STATE['active'] = False


def background_loop(generation):
    while True:
        with LOCK:
            enabled, minutes, pages = STATE['enabled'], STATE['minutes'], STATE['pages']
            current_generation = STATE['generation']
        if not enabled or generation != current_generation:
            return
        for _ in range(minutes * 60):
            time.sleep(1)
            with LOCK:
                if not STATE['enabled'] or generation != STATE['generation']:
                    return
        with LOCK:
            if STATE['active']:
                continue
            STATE['active'] = True
        try:
            lines = crawl(pages)
            with LOCK:
                STATE['lines'] = (STATE['lines'] + lines)[-100:]
        finally:
            with LOCK:
                STATE['active'] = False


def change_auto(enabled, pages, minutes, request: gr.Request):
    is_owner(request)
    with LOCK:
        previous = STATE['enabled']
        STATE['enabled'] = bool(enabled)
        if bool(enabled) != previous:
            STATE['generation'] += 1
        generation = STATE['generation']
        STATE['pages'] = max(1, min(int(pages), 15))
        STATE['minutes'] = max(5, min(int(minutes), 1440))
    if enabled and not previous:
        threading.Thread(target=background_loop, args=(generation,), daemon=True).start()
    return ('✅ Автоматическое исследование включено. Следующий запуск после интервала.'
            if enabled else 'Автоматическое исследование выключено.')


def research_status(request: gr.Request):
    user(request)
    pages, pending = stats()
    with LOCK:
        enabled, active, lines = STATE['enabled'], STATE['active'], STATE['lines'][-12:]
    description = (f'**Сохранено страниц:** {pages} · **В очереди:** {pending} · '
                   f'**Автоматический режим:** {"включён" if enabled else "выключен"} · '
                   f'**Исследование:** {"идёт" if active else "не запущено"}')
    return description, '\n'.join(lines)


def memory_list(request: gr.Request):
    username=user(request)
    if username.startswith('sb:'):
        try: pull_memories(username[3:],bearer_for(username))
        except Exception: pass
    facts = personal_facts(username)
    if not facts:
        return 'Пока нет сохранённых личных фактов. Напиши в чате: `Запомни: любимый цвет = фиолетовый`.'
    # Escape stored plain-text values before embedding in Markdown to avoid Markdown injection.
    return '\n'.join(f'- **{html.escape(key)}** — {html.escape(value)}' for key, value in facts.items())


def memory_delete(memory_key, request: gr.Request):
    username = user(request)
    key = (memory_key or '').strip().lower()
    if not key:
        return 'Укажи ключ воспоминания.'
    if key not in personal_facts(username):
        return 'Такой записи нет.'
    if username.startswith('sb:'):
        try: delete_cloud_memory(username[3:],bearer_for(username),key)
        except Exception: return 'Не удалось удалить облачную запись; локальная запись сохранена.'
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
    if username.startswith('sb:'):
        try: save_memory(username[3:],bearer_for(username),key,value)
        except Exception: return 'Сохранено локально, облако пока недоступно.', '', '', memory_list(request)
    return '✅ Сохранено в твою личную память.', '', '', memory_list(request)


def use_example(value):
    return value


def navigate(choice):
    return [gr.update(visible=choice == section) for section in (
        '💬 Чат', '🌐 Исследования', '💾 Память', '🔗 Подключения', '⚙️ Настройки'
    )]


CSS = r'''
:root { --chelsy-bg:#0b0c17; --chelsy-panel:#141628; --chelsy-stroke:#292b45; --chelsy-violet:#a99bff; }
body, .gradio-container { background: #0b0c17 !important; color:#e9e9f7 !important; }
.gradio-container { max-width: 1540px !important; margin: auto !important; padding: 12px 16px !important; }
#chelsy-header { background: linear-gradient(125deg, #17172f 0%, #161b31 65%, #101322 100%); border: 1px solid #313151; border-radius: 20px; padding: 14px 21px; margin-bottom: 14px; }
#chelsy-header h1 { font-size: 25px; margin: 0; letter-spacing:-.6px; color: #f2efff !important; }
#chelsy-header p { color:#acabd2 !important; margin-top:2px; font-size: 12px; }
#chelsy-shell { gap: 15px; align-items: stretch; }
#chelsy-sidebar { background:#131525; border:1px solid #2b2c45; border-radius:20px; padding: 18px 13px 16px; min-height: 650px; }
#chelsy-sidebar .prose h3 { color: #ddd7ff; margin-bottom: 12px; font-size: 13px; letter-spacing:1px; }
#chelsy-sidebar .wrap { background: transparent !important; border:0 !important; }
#chelsy-sidebar .wrap label {border-radius:12px !important; padding: 11px 10px !important; background:transparent !important; color:#d9d7ed !important; margin-bottom:4px !important;}
#chelsy-sidebar .wrap label.selected, #chelsy-sidebar .wrap label:has(input:checked) { background: #332c60 !important; color:#fff !important; }
#chelsy-main { background: #111322; border:1px solid #2b2d45; border-radius:20px; padding: 16px 19px !important; min-height: 650px; }
#chelsy-main h2 {font-size:23px; letter-spacing:-.5px; color:#faf8ff; margin:0 0 7px;}
#chelsy-main .prose p, #chelsy-main .prose li {color:#bcbdd5;}
#chelsy-pulse-box { margin:0 !important; padding:0 !important; background: transparent !important; border: 0 !important; }
.chelsy-status-line {display:flex;gap:9px;flex-wrap:wrap;align-items:center;font-size:12px;color:#b5b8d2;padding:4px 0 8px;}
.chelsy-pulse {height:10px;width:10px;background:var(--pulse-color);border-radius:50%;box-shadow:0 0 0 4px #829ac420,0 0 15px var(--pulse-color);animation:chelsy-beat 2.5s ease-in-out infinite;}
@keyframes chelsy-beat {50% {opacity:.58;transform:scale(.82)}}
.chelsy-status-separator {color:#545872}
#chelsy-chatbot {border:1px solid #292b44 !important; border-radius:15px !important; background:#0f1120 !important;}
#chelsy-send {background:linear-gradient(115deg,#8975f7,#b09aff) !important;color:#151127 !important;border-radius:12px !important;font-weight:700 !important;}
#chelsy-compose textarea {background:#191b2f !important;}
#chelsy-compose {border-radius:14px !important;}
.chelsy-card {background:#191b30;border:1px solid #30334f;border-radius:15px;padding:16px;margin-bottom:8px;}
footer {display:none !important;}
#chelsy-header { background:linear-gradient(110deg,#10132a,#17132c 64%,#0d2632) !important; }
.chelsy-brand { display:flex;align-items:center;gap:15px; }
.chelsy-brand-avatar { width:74px;height:74px;border-radius:22px;object-fit:cover;box-shadow:0 0 19px #a789fa65; }
.chelsy-brand h1 { margin:0 !important; font-size:27px !important;line-height:1.2; }
.chelsy-brand p { margin-top:5px !important;color:#bcb6dc !important;font-size:13px !important; }
.chelsy-brand-tag {margin-left:auto;padding:7px 12px;border-radius:999px;color:#c7b9ff;border:1px solid #554478;background:#251a3d;font-size:11px;font-weight:700;white-space:nowrap;}
@media(max-width:700px){.chelsy-brand-avatar{width:52px;height:52px;border-radius:16px}.chelsy-brand-tag{display:none}}

@media (max-width: 850px) { #chelsy-sidebar{min-height:auto !important;} #chelsy-main{min-height:530px !important;} }

/* Chelsy 0.6: readable typography, roomy chat, keyboard focus and responsive layout */
.gradio-container {font-family:Inter, 'Segoe UI', system-ui, sans-serif !important;}
#chelsy-header {position:relative; overflow:hidden;}
#chelsy-header:after {content:"";position:absolute;right:-40px;top:-70px;width:190px;height:190px;border-radius:50%;background:#9b66f1;filter:blur(95px);opacity:.22;pointer-events:none}
#chelsy-main {box-shadow:0 22px 70px #02030c44;}
#chelsy-chatbot {min-height:460px !important;}
#chelsy-chatbot .message {font-size:14px;line-height:1.58;}
#chelsy-compose textarea {font-size:15px !important;line-height:1.5 !important;}
#chelsy-main button {transition:transform .15s ease, filter .15s ease;}
#chelsy-main button:hover {filter:brightness(1.08)}
#chelsy-main button:focus-visible, #chelsy-sidebar input:focus-visible {outline:2px solid #a99bff !important;outline-offset:2px;}
#chelsy-hero {background:linear-gradient(115deg,#282344,#142b36);border:1px solid #414267;border-radius:16px;padding:16px 20px;margin-bottom:10px;}
#chelsy-hero h2 {margin:0 0 4px !important;font-size:24px;}
#chelsy-hero p {margin:0 !important;color:#bfc3dd;}
#chelsy-quick button {font-size:13px !important;border-radius:99px !important;border:1px solid #48436d !important;background:#25213e !important;color:#e7e0ff !important;}
#chelsy-compose {border:1px solid #51447d !important;box-shadow:0 4px 28px #6547ac18 !important;}
.chelsy-note {font-size:12px;color:#adb1ce;padding:5px 0 12px;}
@media(max-width:850px){.gradio-container{padding:8px !important}#chelsy-sidebar{min-height:auto !important}#chelsy-main{padding:12px !important;min-height:500px !important}#chelsy-chatbot{min-height:360px !important}#chelsy-header{padding:10px !important}}
@media(prefers-reduced-motion:reduce){.chelsy-pulse{animation:none !important}#chelsy-main button{transition:none !important}}
'''


def build():
    with gr.Blocks(title='Chelsy AI 0.6 | Desktop') as demo:
        gr.HTML(brand_header_html(), elem_id='chelsy-header')
        with gr.Row(elem_id='chelsy-shell'):
            with gr.Column(scale=1, min_width=200, elem_id='chelsy-sidebar'):
                gr.Markdown('### РАЗДЕЛЫ')
                menu = gr.Radio(
                    choices=['💬 Чат', '🌐 Исследования', '💾 Память', '🔗 Подключения', '⚙️ Настройки'],
                    value='💬 Чат', show_label=False, container=False, elem_id='chelsy-nav')
                gr.Markdown('''---
**CHELSY PULSE**  
Живой статус локальной модели и памяти.''')
                refresh_pulse = gr.Button('↻ Обновить статус', variant='secondary', size='sm')
                gr.Markdown('''<small>🔒 Локальный режим · `127.0.0.1`</small>''')
            with gr.Column(scale=4, min_width=360, elem_id='chelsy-main'):
                pulse_line = gr.HTML(elem_id='chelsy-pulse-box')
                with gr.Group(visible=True) as chat_panel:
                    gr.HTML('<div id="chelsy-hero"><h2>✦ Привет! Чем займёмся?</h2><p>Задавай вопросы, исследуй идеи и сохраняй важное в личной памяти.</p></div>')
                    chatbox = gr.Chatbot(label='Chelsy · Диалог', height=520, elem_id='chelsy-chatbot', allow_tags=False, buttons=['copy','copy_all'], avatar_images=(None, str(asset_path('chelsy_logo_small.png'))), placeholder='Напиши Chelsy первое сообщение — диалог появится здесь.')
                    with gr.Row(elem_id='chelsy-quick'):
                        quick_explain = gr.Button('💡 Объясни простыми словами', size='sm')
                        quick_code = gr.Button('💻 Помоги с кодом', size='sm')
                        quick_plan = gr.Button('📋 Составь план', size='sm')
                    with gr.Row():
                        prompt = gr.Textbox(show_label=False, placeholder='Спроси Chelsy о чём угодно…', lines=3, scale=8, elem_id='chelsy-compose')
                        send = gr.Button('➤ Отправить', variant='primary', scale=1, min_width=125, elem_id='chelsy-send')
                    with gr.Row():
                        model = gr.Dropdown(['llama3.2:1b','qwen3:4b'], value=MODEL if MODEL in ('llama3.2:1b','qwen3:4b') else 'llama3.2:1b', label='Модель Ollama', scale=2)
                        clear = gr.Button('Очистить диалог', variant='secondary', scale=1)
                    chatinfo = gr.Markdown('⌨️ Enter — отправить · Shift+Enter — новая строка. Ответы могут ошибаться: проверяй важные факты.')
                    quick_explain.click(lambda: 'Объясни простыми словами: ', outputs=[prompt], api_name=False)
                    quick_code.click(lambda: 'Помоги разобраться с этим кодом: ', outputs=[prompt], api_name=False)
                    quick_plan.click(lambda: 'Составь пошаговый план для: ', outputs=[prompt], api_name=False)
                    send.click(chat, inputs=[prompt, model], outputs=[chatbox, prompt], concurrency_limit=1, api_name=False)
                    prompt.submit(chat, inputs=[prompt, model], outputs=[chatbox, prompt], concurrency_limit=1, api_name=False)
                    clear.click(erase, outputs=[chatbox, chatinfo], api_name=False)
                with gr.Group(visible=False) as research_panel:
                    gr.Markdown('## 🌐 Исследования\nChelsy исследует публичные страницы и сохраняет доступные знания. Запуск обхода — только для `owner`.')
                    url = gr.Textbox(label='Добавить открытый источник', placeholder='https://docs.python.org/3/tutorial/')
                    add = gr.Button('Добавить источник', variant='secondary')
                    add_result = gr.Markdown()
                    with gr.Row():
                        pages = gr.Slider(1,15,value=5,step=1,label='Страниц за цикл')
                        launch = gr.Button('Начать исследование', variant='primary')
                    log = gr.Textbox(label='Журнал', lines=11, interactive=False)
                    gr.Markdown('### Автономный режим\nРаботает **только когда Chelsy AI открыта**. Не запускается на выключенном ноутбуке.')
                    with gr.Row():
                        enabled = gr.Checkbox(label='Автоисследования', value=False)
                        minutes = gr.Slider(5,240,value=60,step=5,label='Интервал (минуты)')
                        toggle = gr.Button('Применить')
                    auto_result = gr.Markdown()
                    status = gr.Markdown()
                    refresh = gr.Button('↻ Обновить статистику', variant='secondary')
                    add.click(seed, inputs=[url], outputs=[add_result], api_name=False)
                    launch.click(run_research, inputs=[pages], outputs=[log], concurrency_limit=1, api_name=False)
                    toggle.click(change_auto, inputs=[enabled,pages,minutes], outputs=[auto_result], api_name=False)
                    refresh.click(research_status, outputs=[status,log], api_name=False)
                with gr.Group(visible=False) as memory_panel:
                    gr.Markdown('## 💾 Личная память\nЗаписи принадлежат только твоему аккаунту; у второго тестера память отдельная.')
                    gr.Markdown('Сохраняй факты прямо здесь или в чате командой `Запомни: любимая игра = шахматы`.')
                    with gr.Group():
                        gr.Markdown('### ✦ Новое воспоминание')
                        new_key = gr.Textbox(label='Что запомнить?', placeholder='Название проекта')
                        new_value = gr.Textbox(label='Значение', placeholder='Chelsy AI', lines=2)
                        save_fact = gr.Button('💜 Сохранить в память', variant='primary')
                        save_fact_status = gr.Markdown()
                    memories = gr.Markdown()
                    save_fact.click(save_memory_form, inputs=[new_key, new_value], outputs=[save_fact_status, new_key, new_value, memories], api_name=False)
                    refresh_memory = gr.Button('↻ Обновить воспоминания', variant='secondary')
                    memory_key = gr.Textbox(label='Ключ записи для удаления', placeholder='любимая игра')
                    remove_memory = gr.Button('Удалить запись', variant='stop')
                    memory_status = gr.Markdown()
                    refresh_memory.click(memory_list, outputs=[memories], api_name=False)
                    remove_memory.click(memory_delete, inputs=[memory_key], outputs=[memory_status], api_name=False)
                with gr.Group(visible=False) as connections_panel:
                    gr.Markdown('## 🔗 Подключения\n**Локальная версия:** второй компьютер пока не подключён. Режим Host/Client разрабатывается отдельно.')
                    gr.Markdown('''<div class="chelsy-card"><strong>🔒 Безопасный тест второго ПК</strong><p>Для двух доверенных тестеров используем Tailscale Serve, отдельный аккаунт tester и доступ только внутри частной сети. Не открывай порт 7860 напрямую в интернете и не используй публичный Gradio share.</p></div>''')
                    gr.Markdown('**Сейчас:** интерфейс привязан к `127.0.0.1:7860`. Для удалённого доступа требуется отдельная настройка Tailscale Serve на ноутбуке. Android и Host/Client API пока не включены.')
                with gr.Group(visible=False) as settings_panel:
                    gr.Markdown('## ⚙️ Настройки\nChelsy автоматически старается отвечать на языке последнего сообщения пользователя.')
                    gr.Markdown('''**Текущая конфигурация**\n- Режим: локальная Windows-сборка.\n- Ollama: `127.0.0.1:11434`.\n- Модель: выбирается в чате (установи её заранее командой `ollama pull`).\n- История: отдельная для каждого аккаунта.\n- Данные пользователя: `%LOCALAPPDATA%\\ChelsyAI\\data`.\n- Общая база исследований: SQLite, лексический поиск.\n\n**Безопасность:** не передавай пароли и не предоставляй удалённый доступ вне доверенной частной сети.''')
                menu.change(navigate, inputs=[menu], outputs=[chat_panel,research_panel,memory_panel,connections_panel,settings_panel], api_name=False)
        demo.load(pulse, outputs=[pulse_line], api_name=False)
        demo.load(get_history, outputs=[chatbox], api_name=False)
        demo.load(memory_list, outputs=[memories], api_name=False)
        demo.load(research_status, outputs=[status,log], api_name=False)
        refresh_pulse.click(pulse, outputs=[pulse_line], api_name=False)
    return demo


if __name__ == '__main__':
    if not USERS_PATH.exists():
        raise SystemExit('Создай аккаунты через setup_users.py или запусти ChelsyAI.exe')
    demo = build()
    demo.queue(default_concurrency_limit=1, api_open=False)
    demo.launch(server_name='127.0.0.1', server_port=7860, share=False,
                auth=authenticate, auth_message='Вход для тестеров Chelsy AI',
                inbrowser=True, show_error=False, enable_monitoring=False,
                css=CSS, theme=gr.themes.Soft(primary_hue='violet', neutral_hue='slate'))
