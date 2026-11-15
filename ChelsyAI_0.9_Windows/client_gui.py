"""Chelsy AI Windows Client GUI (Tkinter, standard library only).

Layout mirrors the Desktop app: sidebar with new chat, history search,
conversations (★ favourites first), research and knowledge windows, sync
and profile; main chat with streaming answers, stop, regenerate, edit,
copy, sources, model choice, web toggle, text-file attachments and the
Chelsy Pulse status indicator.
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from tkinter import (BooleanVar, Canvas, END, Frame, Label, Listbox, StringVar, Text, Tk, Toplevel, filedialog,
                     messagebox, ttk)

import client_cache
import secure_store
from client_api import HostClient, HostError, Unauthorized, validate_host
from supabase_auth import AuthError, allow_bundled_config, config as supabase_config, login as supabase_login, \
    refresh as supabase_refresh, _jwt_exp

VERSION = '0.9.3'
BG = '#0B0C20'
PANEL = '#11132B'
PANEL2 = '#161937'
STROKE = '#262A52'
VIOLET = '#6D4AFF'
LAVENDER = '#A78BFA'
TEAL = '#21D9DA'
TEXT = '#ECEBFA'
MUTED = '#A3A6C8'
PULSE = {'ready': TEAL, 'generating': LAVENDER, 'searching': TEAL, 'checking': LAVENDER, 'syncing': VIOLET,
         'offline': '#FF8A65'}
TEXT_EXT = ('.txt', '.md', '.py', '.json', '.csv', '.log', '.js', '.ts', '.html', '.css', '.yaml', '.yml', '.xml',
            '.ini', '.toml', '.dart', '.sql', '.java', '.c', '.cpp', '.cs', '.go', '.rs', '.sh', '.ps1', '.bat')


def asset(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'assets' / name


def settings_path():
    return Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'ChelsyAI' / 'client.json'


class Session:
    """Supabase session; the refresh token is persisted only with DPAPI (Windows)."""

    def __init__(self):
        self.access = None
        self.refresh_token = None
        self.email = ''
        self.lock = threading.Lock()

    def login(self, email, password, remember):
        result = supabase_login(email, password)
        with self.lock:
            self.access = result['access_token']
            self.refresh_token = result.get('refresh_token')
            self.email = (result.get('user') or {}).get('email') or email
        if remember and self.refresh_token:
            secure_store.save(self.refresh_token)
        return result

    def resume(self):
        token = secure_store.load()
        if not token:
            return False
        try:
            result = supabase_refresh(token)
        except AuthError:
            secure_store.clear()
            return False
        with self.lock:
            self.access = result['access_token']
            self.refresh_token = result.get('refresh_token', token)
            self.email = (result.get('user') or {}).get('email', '')
        secure_store.save(self.refresh_token)
        return True

    def token(self, force_refresh=False):
        with self.lock:
            access, refresh = self.access, self.refresh_token
        if not access:
            raise Unauthorized('Нужно войти')
        exp = _jwt_exp(access)
        if force_refresh or (exp is not None and exp < time.time() + 60):
            if not refresh:
                raise Unauthorized('Сессия истекла')
            result = supabase_refresh(refresh)
            with self.lock:
                self.access = result['access_token']
                self.refresh_token = result.get('refresh_token', refresh)
            if secure_store.load():
                secure_store.save(self.refresh_token)
            return self.access
        return access

    def logout(self):
        with self.lock:
            self.access = self.refresh_token = None
        secure_store.clear()


class Pulse(Canvas):
    def __init__(self, master):
        super().__init__(master, width=18, height=18, bg=PANEL, highlightthickness=0)
        self.state = 'ready'
        self.phase = 0
        self.ring = self.create_oval(2, 2, 16, 16, outline=TEAL, width=2)
        self.dot = self.create_oval(5, 5, 13, 13, fill=TEAL, outline='')
        self._animate()

    def set(self, state):
        self.state = state
        color = PULSE.get(state, TEAL)
        self.itemconfigure(self.dot, fill=color)
        self.itemconfigure(self.ring, outline=color)

    def _animate(self):
        if self.state in ('generating', 'searching', 'checking', 'syncing'):
            self.phase = (self.phase + 1) % 12
            r = 4 + self.phase * 0.45
            self.coords(self.ring, 9 - r, 9 - r, 9 + r, 9 + r)
            self.itemconfigure(self.ring, state='normal' if self.phase < 10 else 'hidden')
        else:
            self.coords(self.ring, 2, 2, 16, 16)
            self.itemconfigure(self.ring, state='hidden')
        self.after(90, self._animate)


class ChelsyClientApp:
    def __init__(self):
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text on scaled displays
            except Exception:
                pass
        allow_bundled_config(True)
        self.root = Tk()
        self.root.title(f'Chelsy AI · Client {VERSION}')
        self.root.geometry('1180x780')
        self.root.minsize(760, 520)
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(str(asset('chelsy.ico')))
        except Exception:
            pass
        self.events = queue.Queue()
        self.session = Session()
        self.client = None
        self.conversation = None
        self.conversations = []
        self.messages = []
        self.request_id = None
        self.busy = False
        self.answer_start = None
        try:
            self.saved = json.loads(settings_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.saved = {}
        self._style()
        self.login_frame = self._login_view()
        self.main_frame = None
        self.root.after(60, self._poll)
        if self.saved.get('host'):
            self._background(self._try_resume)

    # ------------------------------------------------------------------ style
    def _style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('.', background=BG, foreground=TEXT, fieldbackground=PANEL2, bordercolor=STROKE,
                        font=('Segoe UI', 10))
        style.configure('TFrame', background=BG)
        style.configure('Side.TFrame', background=PANEL)
        style.configure('TLabel', background=BG, foreground=TEXT)
        style.configure('Side.TLabel', background=PANEL, foreground=TEXT)
        style.configure('Muted.TLabel', background=BG, foreground=MUTED, font=('Segoe UI', 9))
        style.configure('SideMuted.TLabel', background=PANEL, foreground=MUTED, font=('Segoe UI', 9))
        style.configure('Title.TLabel', background=BG, foreground=TEXT, font=('Segoe UI Semibold', 13))
        style.configure('TButton', background=PANEL2, foreground=TEXT, borderwidth=1, focusthickness=2,
                        focuscolor=TEAL, padding=(10, 6))
        style.map('TButton', background=[('active', '#232857'), ('disabled', '#14162e')],
                  foreground=[('disabled', '#5d6190')])
        style.configure('Accent.TButton', background=VIOLET, foreground='white', font=('Segoe UI Semibold', 10))
        style.map('Accent.TButton', background=[('active', '#7D5DFF'), ('disabled', '#3b2d86')])
        style.configure('Stop.TButton', background='#3A1630', foreground='#FFD7E4')
        style.configure('TCheckbutton', background=BG, foreground=TEXT)
        style.map('TCheckbutton', background=[('active', BG)])
        style.configure('TCombobox', fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=LAVENDER)
        style.map('TCombobox', fieldbackground=[('readonly', PANEL2)], foreground=[('readonly', TEXT)],
                  selectbackground=[('readonly', PANEL2)], selectforeground=[('readonly', TEXT)])
        self.root.option_add('*TCombobox*Listbox.background', PANEL2)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        style.configure('TEntry', fieldbackground=PANEL2, foreground=TEXT, insertcolor=TEXT)
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=26)
        style.configure('Treeview.Heading', background=PANEL2, foreground=TEXT)

    # ------------------------------------------------------------------ login
    def _login_view(self):
        frame = ttk.Frame(self.root, padding=30)
        frame.place(relx=0.5, rely=0.5, anchor='center')
        try:
            from tkinter import PhotoImage
            self.logo = PhotoImage(file=str(asset('chelsy_logo_small.png'))).subsample(3, 3)
            Label(frame, image=self.logo, bg=BG).pack(pady=(0, 8))
        except Exception:
            pass
        ttk.Label(frame, text='Chelsy AI', style='Title.TLabel', font=('Segoe UI Semibold', 20)).pack()
        ttk.Label(frame, text='Войди аккаунтом Chelsy Group', style='Muted.TLabel').pack(pady=(0, 16))
        self.host_var = StringVar(value=self.saved.get('host', 'https://'))
        self.email_var = StringVar(value=self.saved.get('email', ''))
        self.password_var = StringVar()
        self.remember_var = BooleanVar(value=secure_store.supported())
        for label, var, show in (('Адрес Chelsy Host (Tailscale)', self.host_var, None), ('Email', self.email_var, None),
                                 ('Пароль', self.password_var, '•')):
            ttk.Label(frame, text=label, style='Muted.TLabel').pack(anchor='w')
            entry = ttk.Entry(frame, textvariable=var, show=show, width=46)
            entry.pack(fill='x', pady=(2, 10), ipady=4)
            entry.bind('<Return>', lambda e: self._login())
        remember = ttk.Checkbutton(frame, text='Запомнить вход (зашифровано Windows DPAPI)', variable=self.remember_var)
        remember.pack(anchor='w', pady=(0, 10))
        if not secure_store.supported():
            remember.state(['disabled'])
        self.login_button = ttk.Button(frame, text='Войти', style='Accent.TButton', command=self._login)
        self.login_button.pack(fill='x', ipady=3)
        self.login_status = ttk.Label(frame, text='', style='Muted.TLabel', wraplength=380)
        self.login_status.pack(pady=(10, 0))
        return frame

    def _remember(self, **values):
        self.saved.update(values)
        try:
            settings_path().parent.mkdir(parents=True, exist_ok=True)
            settings_path().write_text(json.dumps(self.saved), encoding='utf-8')
        except OSError:
            pass

    def _login(self):
        try:
            host = validate_host(self.host_var.get())
        except ValueError as exc:
            self.login_status.configure(text=str(exc))
            return
        if supabase_config() is None:
            self.login_status.configure(text='Supabase не настроен. Запусти «Chelsy Supabase Setup».')
            return
        email, password, remember = self.email_var.get().strip(), self.password_var.get(), self.remember_var.get()
        self.password_var.set('')
        self.login_button.state(['disabled'])
        self.login_status.configure(text='Вход…')

        def job():
            try:
                self.session.login(email, password, remember)
                self.events.put(('logged-in', host))
            except AuthError as exc:
                self.events.put(('login-error', 'Не удалось войти: проверь email и пароль. (' + str(exc) + ')'))
        self._background(job)

    def _try_resume(self):
        try:
            host = validate_host(self.saved.get('host', ''))
        except ValueError:
            return
        if self.session.resume():
            self.events.put(('logged-in', host))

    # ------------------------------------------------------------------ main
    def _main_view(self):
        main = Frame(self.root, bg=BG)
        main.pack(fill='both', expand=True)
        side = ttk.Frame(main, style='Side.TFrame', padding=12, width=270)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        head = ttk.Frame(side, style='Side.TFrame')
        head.pack(fill='x', pady=(0, 10))
        try:
            from tkinter import PhotoImage
            self.logo_small = PhotoImage(file=str(asset('chelsy_logo_small.png'))).subsample(6, 6)
            Label(head, image=self.logo_small, bg=PANEL).pack(side='left')
        except Exception:
            pass
        ttk.Label(head, text=' Chelsy AI', style='Side.TLabel', font=('Segoe UI Semibold', 14)).pack(side='left')
        ttk.Button(side, text='＋  Новый чат', style='Accent.TButton', command=self.new_chat).pack(fill='x', pady=(0, 8))
        self.search_var = StringVar()
        search = ttk.Entry(side, textvariable=self.search_var)
        search.pack(fill='x', ipady=3)
        search.bind('<KeyRelease>', lambda e: self._debounce_search())
        ttk.Label(side, text='ДИАЛОГИ  (★ — избранные)', style='SideMuted.TLabel').pack(anchor='w', pady=(10, 2))
        self.conv_list = Listbox(side, bg=PANEL, fg=TEXT, selectbackground='#2A2160', selectforeground='white',
                                 highlightthickness=0, borderwidth=0, activestyle='none', font=('Segoe UI', 10))
        self.conv_list.pack(fill='both', expand=True)
        self.conv_list.bind('<<ListboxSelect>>', lambda e: self._open_selected())
        row = ttk.Frame(side, style='Side.TFrame')
        row.pack(fill='x', pady=6)
        for text, cmd in (('★', self.toggle_favorite), ('✎', self.rename_chat), ('🗑', self.delete_chat)):
            ttk.Button(row, text=text, width=4, command=cmd).pack(side='left', padx=(0, 4))
        for text, cmd in (('🔎 Исследования', self.open_research), ('📚 База знаний', self.open_knowledge),
                          ('⟳ Синхронизация', self.sync)):
            ttk.Button(side, text=text, command=cmd).pack(fill='x', pady=2)
        profile = ttk.Frame(side, style='Side.TFrame')
        profile.pack(fill='x', pady=(10, 0))
        ttk.Label(profile, text='👤 ' + (self.session.email or 'аккаунт'), style='Side.TLabel').pack(side='left')
        ttk.Button(profile, text='Выйти', command=self.logout).pack(side='right')

        content = ttk.Frame(main, padding=(16, 10))
        content.pack(side='left', fill='both', expand=True)
        top = ttk.Frame(content)
        top.pack(fill='x')
        self.title_label = ttk.Label(top, text='Новый чат', style='Title.TLabel')
        self.title_label.pack(side='left')
        status_box = Frame(top, bg=PANEL, padx=8, pady=4)
        status_box.pack(side='right')
        self.pulse = Pulse(status_box)
        self.pulse.pack(side='left')
        self.status_label = Label(status_box, text='Подключение…', bg=PANEL, fg=MUTED, font=('Segoe UI', 9))
        self.status_label.pack(side='left', padx=(6, 0))
        compose = Frame(content, bg=PANEL2, highlightthickness=1, highlightbackground='#3A3474', padx=8, pady=6)
        self.input = Text(compose, height=3, wrap='word', bg=PANEL2, fg=TEXT, insertbackground=TEXT, borderwidth=0,
                          highlightthickness=0, font=('Segoe UI', 11))
        self.input.pack(side='left', fill='both', expand=True)
        self.input.bind('<Return>', self._enter)
        self.input.bind('<Shift-Return>', lambda e: None)
        buttons = Frame(compose, bg=PANEL2)
        buttons.pack(side='right', padx=(6, 0))
        self.send_button = ttk.Button(buttons, text='➤', width=4, style='Accent.TButton', command=self.send)
        self.send_button.pack(side='top', pady=1)
        self.stop_button = ttk.Button(buttons, text='■', width=4, style='Stop.TButton', command=self.stop)
        self.stop_button.pack(side='top', pady=1)
        self.stop_button.state(['disabled'])
        opts = ttk.Frame(content)
        self.model_var = StringVar()
        self.model_box = ttk.Combobox(opts, textvariable=self.model_var, state='readonly', width=22)
        self.model_box.pack(side='left')
        self.web_var = BooleanVar(value=self.saved.get('web', True))
        ttk.Checkbutton(opts, text='🌐 Поиск в интернете', variable=self.web_var,
                        command=lambda: self._remember(web=self.web_var.get())).pack(side='left', padx=10)
        self.attachments = []
        self.attach_label = ttk.Label(opts, text='', style='Muted.TLabel')
        for text, cmd in (('📎', self.attach), ('↻ Повторить', self.regenerate), ('✎ Изменить', self.edit_last),
                          ('⧉ Копировать', self.copy_answer)):
            ttk.Button(opts, text=text, command=cmd).pack(side='left', padx=2)
        self.attach_label.pack(side='left', padx=8)
        # Bottom widgets are packed first so they stay visible on small or scaled screens.
        ttk.Label(content, text='Enter — отправить · Shift+Enter — новая строка · ответы могут содержать ошибки',
                  style='Muted.TLabel').pack(side='bottom', anchor='w', pady=(4, 0))
        opts.pack(side='bottom', fill='x', pady=(6, 0))
        compose.pack(side='bottom', fill='x')
        chat_frame = Frame(content, bg=BG)
        chat_frame.pack(fill='both', expand=True, pady=(10, 8))
        self.chat = Text(chat_frame, wrap='word', bg=BG, fg=TEXT, insertbackground=TEXT, borderwidth=0,
                         highlightthickness=0, font=('Segoe UI', 11), padx=14, pady=10, spacing1=2, spacing3=4)
        scroll = ttk.Scrollbar(chat_frame, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set, state='disabled')
        scroll.pack(side='right', fill='y')
        self.chat.pack(side='left', fill='both', expand=True)
        self.chat.tag_configure('user', background='#231D4F', lmargin1=180, lmargin2=180, rmargin=10,
                                spacing1=8, spacing3=8)
        self.chat.tag_configure('author', foreground=LAVENDER, font=('Segoe UI Semibold', 10), spacing1=12)
        self.chat.tag_configure('bold', font=('Segoe UI Semibold', 11))
        self.chat.tag_configure('heading', font=('Segoe UI Semibold', 13), spacing1=8)
        self.chat.tag_configure('code', font=('Consolas', 10), background='#0E1029', foreground='#E6E3FF',
                                lmargin1=20, lmargin2=20)
        self.chat.tag_configure('inline', font=('Consolas', 10), background='#1A1D40')
        self.chat.tag_configure('link', foreground=LAVENDER, underline=True)
        self.chat.tag_configure('muted', foreground=MUTED, font=('Segoe UI', 9, 'italic'))
        self.chat.tag_bind('link', '<Button-1>', self._click_link)
        self.chat.tag_bind('link', '<Enter>', lambda e: self.chat.configure(cursor='hand2'))
        self.chat.tag_bind('link', '<Leave>', lambda e: self.chat.configure(cursor=''))

        return main

    # ------------------------------------------------------------------ helpers
    def _background(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _api(self, fn, *args, then=None, **kw):
        def job():
            try:
                result = fn(*args, **kw)
                self.events.put(('call', (then, result)))
            except Unauthorized:
                self.events.put(('unauthorized', None))
            except (HostError, AuthError, OSError) as exc:
                self.events.put(('error', str(exc)))
        self._background(job)

    def _set_status(self, state, text):
        self.pulse.set(state)
        self.status_label.configure(text=text)

    def _debounce_search(self):
        if hasattr(self, '_search_job'):
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(350, self.load_conversations)

    def _enter(self, event):
        if event.state & 0x0001:     # Shift pressed: newline
            return None
        self.send()
        return 'break'

    # ------------------------------------------------------------------ rendering
    def _insert_markdown(self, text):
        in_code = False
        for line in text.split('\n'):
            if line.strip().startswith('```'):
                in_code = not in_code
                continue
            if in_code:
                self.chat.insert(END, line + '\n', 'code')
                continue
            if line.strip() == '---':
                self.chat.insert(END, '─' * 40 + '\n', 'muted')
                continue
            m = re.match(r'^(#{1,4})\s+(.*)', line)
            if m:
                self.chat.insert(END, m.group(2) + '\n', 'heading')
                continue
            for part in re.split(r'(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?://[^)\s]+\))', line):
                if part.startswith('**') and part.endswith('**') and len(part) > 4:
                    self.chat.insert(END, part[2:-2], 'bold')
                elif part.startswith('`') and part.endswith('`') and len(part) > 2:
                    self.chat.insert(END, part[1:-1], 'inline')
                elif part.startswith('[') and '](' in part:
                    label, url = part[1:-1].split('](', 1)
                    self.chat.insert(END, label, ('link', 'url:' + url))
                else:
                    self.chat.insert(END, part)
            self.chat.insert(END, '\n')

    def render(self, streaming_text=None):
        self.chat.configure(state='normal')
        self.chat.delete('1.0', END)
        if not self.messages and streaming_text is None:
            self.chat.insert(END, '\n\n   Привет! Я Челси 💜\n', 'heading')
            self.chat.insert(END, '   Спроси что угодно, попроси объяснить, помочь с кодом или исследовать тему.\n', 'muted')
        for m in self.messages:
            if m['role'] == 'user':
                self.chat.insert(END, 'Ты\n', 'author')
                self.chat.insert(END, m['content'] + '\n', 'user')
            else:
                self.chat.insert(END, 'Chelsy\n', 'author')
                self._insert_markdown(m['content'])
        if streaming_text is not None:
            self.chat.insert(END, 'Chelsy\n', 'author')
            self._insert_markdown(streaming_text + ' ▍')
        self.chat.configure(state='disabled')
        self.chat.see(END)

    def _click_link(self, event):
        index = self.chat.index(f'@{event.x},{event.y}')
        for tag in self.chat.tag_names(index):
            if tag.startswith('url:https://'):
                webbrowser.open(tag[4:])

    # ------------------------------------------------------------------ data
    def _account(self):
        return self.session.email or self.saved.get('email', '')

    def _cached_conversations(self, query):
        """Host first; on network failure fall back to the local offline copy."""
        try:
            items = self.client.conversations(query)
            if not query:
                client_cache.save_conversations(self._account(), items)
            return items
        except Unauthorized:
            raise
        except HostError:
            items = client_cache.load_conversations(self._account(), query)
            self.events.put(('offline', 'Хост недоступен — показана сохранённая на этом ПК копия'))
            return items

    def _cached_messages(self, conversation_id):
        try:
            items = self.client.messages(conversation_id)
            client_cache.save_messages(self._account(), conversation_id, items)
            return items
        except Unauthorized:
            raise
        except HostError:
            items = client_cache.load_messages(self._account(), conversation_id)
            self.events.put(('offline', 'Хост недоступен — показана сохранённая копия диалога'))
            return items

    def load_conversations(self):
        if self.client:
            self._api(self._cached_conversations, self.search_var.get().strip(), then='conversations')

    def _show_conversations(self, items):
        self.conversations = sorted(items, key=lambda c: (not c['favorite'], c['updated']), reverse=False)
        favs = [c for c in items if c['favorite']]
        rest = [c for c in items if not c['favorite']]
        self.conversations = favs + rest
        self.conv_list.delete(0, END)
        for c in self.conversations:
            self.conv_list.insert(END, ('★ ' if c['favorite'] else '   ') + c['title'][:40])
            if c['id'] == self.conversation:
                self.conv_list.selection_set(END)

    def _open_selected(self):
        sel = self.conv_list.curselection()
        if not sel or self.busy:
            return
        conv = self.conversations[sel[0]]
        self.conversation = conv['id']
        self.title_label.configure(text=conv['title'][:70])
        self._api(self._cached_messages, conv['id'], then='messages')

    def new_chat(self):
        if self.busy:
            return
        self.conversation = None
        self.messages = []
        self.title_label.configure(text='Новый чат')
        self.conv_list.selection_clear(0, END)
        self.render()
        self.input.focus_set()

    def toggle_favorite(self):
        conv = next((c for c in self.conversations if c['id'] == self.conversation), None)
        if conv:
            self._api(self.client.update_conversation, conv['id'], favorite=not conv['favorite'], then='refresh')

    def rename_chat(self):
        if not self.conversation:
            return
        from tkinter import simpledialog
        title = simpledialog.askstring('Chelsy AI', 'Новое название чата:', parent=self.root)
        if title and title.strip():
            self.title_label.configure(text=title.strip()[:70])
            self._api(self.client.update_conversation, self.conversation, title=title.strip(), then='refresh')

    def delete_chat(self):
        if self.conversation and messagebox.askyesno('Chelsy AI', 'Удалить этот чат? (Он скрывается, данные на хосте сохраняются в резервной копии.)'):
            self._api(self.client.delete_conversation, self.conversation, then='refresh')
            self.new_chat()

    # ------------------------------------------------------------------ chat
    def attach(self):
        paths = filedialog.askopenfilenames(title='Текстовые файлы', filetypes=[('Текст и код', ' '.join('*' + e for e in TEXT_EXT))])
        self.attachments = []
        for p in paths[:4]:
            path = Path(p)
            if path.suffix.lower() not in TEXT_EXT:
                continue
            data = path.read_bytes()[:200_000]
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                text = data.decode('cp1251', errors='replace')
            self.attachments.append({'name': path.name, 'text': text[:12000]})
        self.attach_label.configure(text=('📎 ' + ', '.join(a['name'] for a in self.attachments)) if self.attachments else '')

    def send(self, regenerate=False, edit_id=None, text=None):
        if self.busy or not self.client:
            return
        text = text if text is not None else self.input.get('1.0', END).strip()
        if not text and not self.attachments and not regenerate:
            return
        if not regenerate:
            self.input.delete('1.0', END)
            if edit_id is not None:
                idx = next((i for i, m in enumerate(self.messages) if m.get('id') == edit_id), len(self.messages))
                self.messages = self.messages[:idx]
            self.messages.append({'role': 'user', 'content': text})
        else:
            while self.messages and self.messages[-1]['role'] == 'assistant':
                self.messages.pop()
        attachments, self.attachments = self.attachments, []
        self.attach_label.configure(text='')
        self.busy = True
        self.request_id = str(uuid.uuid4())
        self.send_button.state(['disabled'])
        self.stop_button.state(['!disabled'])
        self._set_status('generating', 'Генерирую ответ…')
        self.render('')
        kwargs = dict(conversation_id=self.conversation, model=self.model_var.get() or None, web=self.web_var.get(),
                      attachments=attachments, regenerate=regenerate, edit_message_id=edit_id, request_id=self.request_id)

        def job():
            try:
                for event in self.client.chat_stream(text, **kwargs):
                    self.events.put(('stream', event))
            except Unauthorized:
                self.events.put(('unauthorized', None))
            except (HostError, AuthError, OSError) as exc:
                self.events.put(('stream', {'type': 'error', 'message': str(exc)}))
            finally:
                self.events.put(('stream-end', None))
        self._background(job)

    def stop(self):
        if self.busy and self.request_id:
            self._api(self.client.stop, self.request_id, then=None)
            self._set_status('ready', 'Останавливаю…')

    def regenerate(self):
        if self.conversation and self.messages:
            self.send(regenerate=True, text='')

    def edit_last(self):
        last = next((m for m in reversed(self.messages) if m['role'] == 'user' and m.get('id')), None)
        if not last or self.busy:
            return
        from tkinter import simpledialog
        text = simpledialog.askstring('Изменить сообщение', 'Новый текст сообщения:', initialvalue=last['content'],
                                      parent=self.root)
        if text and text.strip():
            self.send(edit_id=last['id'], text=text.strip())

    def copy_answer(self):
        last = next((m for m in reversed(self.messages) if m['role'] == 'assistant'), None)
        if last:
            self.root.clipboard_clear()
            self.root.clipboard_append(last['content'])
            self._set_status('ready', 'Ответ скопирован')

    def sync(self):
        if self.client:
            self._set_status('syncing', 'Синхронизация…')
            self._api(self.client.sync, then='synced')

    def logout(self):
        try:
            if self._account():
                client_cache.clear(self._account())   # shared PCs: no chat copies after sign-out
        except (OSError, ValueError):
            pass
        self.session.logout()
        self.client = None
        if self.main_frame:
            self.main_frame.destroy()
            self.main_frame = None
        self.login_frame = self._login_view()

    # ------------------------------------------------------------------ research & knowledge windows
    def open_research(self):
        if not self.client:
            return
        win = Toplevel(self.root, bg=BG)
        win.title('Исследования — Chelsy AI')
        win.geometry('980x640')
        top = ttk.Frame(win, padding=10)
        top.pack(fill='x')
        topic = StringVar()
        ttk.Label(top, text='Тема или ссылка:').pack(side='left')
        ttk.Entry(top, textvariable=topic, width=60).pack(side='left', padx=6, ipady=3)
        info = ttk.Label(win, text='Найденные материалы попадут в базу знаний только после твоего решения.', style='Muted.TLabel')
        info.pack(anchor='w', padx=10)
        jobs = ttk.Treeview(win, columns=('topic', 'status', 'progress', 'message'), show='headings', height=6)
        for col, title, width in (('topic', 'Тема', 260), ('status', 'Статус', 90), ('progress', '%', 50), ('message', 'Сообщение', 480)):
            jobs.heading(col, text=title)
            jobs.column(col, width=width)
        jobs.pack(fill='x', padx=10, pady=6)
        items = ttk.Treeview(win, columns=('status', 'trust', 'date', 'title', 'url'), show='headings', selectmode='extended')
        for col, title, width in (('status', 'Статус', 90), ('trust', 'Доверие', 60), ('date', 'Дата', 90), ('title', 'Заголовок', 330), ('url', 'Ссылка', 330)):
            items.heading(col, text=title)
            items.column(col, width=width)
        items.pack(fill='both', expand=True, padx=10)
        actions = ttk.Frame(win, padding=10)
        actions.pack(fill='x')

        def refresh_jobs():
            def job():
                try:
                    data = self.client.research()
                    self.events.put(('call', (lambda d=data: fill_jobs(d), None)))
                except (HostError, AuthError, OSError) as exc:
                    self.events.put(('error', str(exc)))
            self._background(job)

        def fill_jobs(data):
            jobs.delete(*jobs.get_children())
            for j in data:
                jobs.insert('', END, iid=j['id'], values=(j['topic'][:60], j['status'], int(j['progress'] * 100), j['message'][:100]))

        def show_items(_=None):
            sel = jobs.selection()
            if not sel:
                return

            def job():
                try:
                    data = self.client.research_job(sel[0])
                    self.events.put(('call', (lambda d=data: fill_items(d), None)))
                except (HostError, AuthError, OSError) as exc:
                    self.events.put(('error', str(exc)))
            self._background(job)

        def fill_items(data):
            items.delete(*items.get_children())
            for i in data['items']:
                items.insert('', END, iid=str(i['id']), values=(i['status'], f'{i["trust"]:.2f}', (i['published'] or '—')[:10], i['title'][:80], i['url']))

        def review(action, scope='personal'):
            selected = items.selection()

            def job():
                for item_id in selected:
                    try:
                        self.client.review_item(int(item_id), action, scope)
                    except HostError as exc:
                        self.events.put(('error', str(exc)))
                self.events.put(('call', (show_items, None)))
            self._background(job)

        def start():
            if len(topic.get().strip()) < 3:
                return
            self._api(self.client.start_research, topic.get().strip(), then=None)
            win.after(800, refresh_jobs)

        def stop_job():
            sel = jobs.selection()
            if sel:
                self._api(self.client.stop_research, sel[0], then=None)
                win.after(800, refresh_jobs)
        ttk.Button(top, text='▶ Начать', style='Accent.TButton', command=start).pack(side='left')
        ttk.Button(top, text='⏹ Стоп', command=stop_job).pack(side='left', padx=4)
        ttk.Button(top, text='↻', command=refresh_jobs).pack(side='left')
        ttk.Button(actions, text='➕ В личную базу', style='Accent.TButton', command=lambda: review('accept')).pack(side='left')
        ttk.Button(actions, text='➕ В общую базу (владелец)', command=lambda: review('accept', 'shared')).pack(side='left', padx=6)
        ttk.Button(actions, text='✖ Отклонить', command=lambda: review('reject')).pack(side='left')
        ttk.Button(actions, text='Открыть ссылку', command=lambda: [webbrowser.open(items.item(i)['values'][4]) for i in items.selection()[:1]
                                                                    if str(items.item(i)['values'][4]).startswith('https://')]).pack(side='right')
        jobs.bind('<<TreeviewSelect>>', show_items)

        def tick():
            if win.winfo_exists():
                refresh_jobs()
                win.after(5000, tick)
        tick()

    def open_knowledge(self):
        if not self.client:
            return
        win = Toplevel(self.root, bg=BG)
        win.title('База знаний — Chelsy AI')
        win.geometry('900x560')
        summary = ttk.Label(win, text='Загрузка…', padding=10)
        summary.pack(anchor='w')
        tree = ttk.Treeview(win, columns=('scope', 'title', 'date', 'trust', 'url'), show='headings')
        for col, title, width in (('scope', 'База', 70), ('title', 'Заголовок', 330), ('date', 'Опубликовано', 100), ('trust', 'Доверие', 60), ('url', 'Ссылка', 320)):
            tree.heading(col, text=title)
            tree.column(col, width=width)
        tree.pack(fill='both', expand=True, padx=10, pady=10)
        ttk.Label(win, text='Журнал автоизучения — что Chelsy изучила сама', style='Muted.TLabel').pack(anchor='w', padx=10)
        journal = Text(win, height=9, wrap='word', bg=PANEL, fg=TEXT, borderwidth=0, font=('Segoe UI', 9))
        journal.pack(fill='x', padx=10, pady=(2, 10))
        labels = {'learned': '📚 изучено', 'added': '➕ добавлено', 'auto-job': '🔎 начато', 'auto-done': '✅ готово',
                  'skipped': '⏸ на проверку', 'limit': '⛔ лимит', 'error': '⚠️ ошибка'}

        def fill_journal(data):
            journal.delete('1.0', END)
            for e in data.get('journal', []):
                journal.insert(END, f'{e["created"][:16].replace("T", " ")}  {labels.get(e["event"], e["event"])}  '
                                    f'{e["title"][:90]}  {e["detail"][:80]}\n')
            if not data.get('journal'):
                journal.insert(END, 'Пока пусто. Chelsy начнёт изучать источники после ответов с поиском в интернете.')

        def load_journal():
            try:
                data = self.client.learning()
                self.events.put(('call', (lambda d=data: fill_journal(d), None)))
            except (HostError, AuthError, OSError):
                pass
        self._background(load_journal)

        def fill(data):
            st = data['stats']
            summary.configure(text=f'Документов: {st["documents"]} · фрагментов: {st["chunks"]} · возможных противоречий: {st["open_conflicts"]}')
            for d in data['documents']:
                tree.insert('', END, values=('личная' if d['personal'] else 'общая', d['title'][:70], (d['published'] or '—')[:10], f'{d["trust"]:.2f}', d['url']))

        def job():
            try:
                data = self.client.knowledge()
                self.events.put(('call', (lambda d=data: fill(d), None)))
            except (HostError, AuthError, OSError) as exc:
                self.events.put(('error', str(exc)))
        self._background(job)

    # ------------------------------------------------------------------ event loop
    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _handle(self, kind, payload):
        if kind == 'logged-in':
            self._remember(host=payload, email=self.session.email or self.email_var.get())
            self.client = HostClient(payload, self.session.token)
            self.login_frame.destroy()
            self.main_frame = self._main_view()
            self.render()
            self._api(self.client.status, then='status')
            self.load_conversations()
        elif kind == 'login-error':
            self.login_button.state(['!disabled'])
            self.login_status.configure(text=payload)
        elif kind == 'unauthorized':
            messagebox.showwarning('Chelsy AI', 'Сессия истекла. Войди снова.')
            self.logout()
        elif kind == 'offline':
            if self.main_frame:
                self._set_status('offline', payload)
        elif kind == 'error':
            if self.main_frame:
                self._set_status('offline', payload[:90])
            else:
                messagebox.showerror('Chelsy AI', payload)
        elif kind == 'call':
            then, result = payload
            if callable(then):
                then()
            elif then == 'conversations':
                self._show_conversations(result)
            elif then == 'messages':
                self.messages = result
                self.render()
            elif then == 'refresh':
                self.load_conversations()
            elif then == 'status':
                models = result.get('models') or []
                self.model_box.configure(values=models)
                if models and not self.model_var.get():
                    self.model_var.set(result.get('default_model') if result.get('default_model') in models else models[0])
                ok = result.get('ollama')
                self._set_status('ready' if ok else 'offline',
                                 f'Host {result.get("version", "")} · ' + ('модель готова' if ok else 'Ollama недоступна на хосте'))
            elif then == 'synced':
                self._set_status('ready', f'Синхронизировано · получено {result.get("received", 0)}')
                self.load_conversations()
        elif kind == 'stream':
            self._stream_event(payload)
        elif kind == 'stream-end':
            self.busy = False
            self.send_button.state(['!disabled'])
            self.stop_button.state(['disabled'])
            if self.conversation:
                self._api(self._cached_messages, self.conversation, then='messages')
            self.load_conversations()

    def _stream_event(self, event):
        etype = event.get('type')
        if etype == 'start':
            self.conversation = event.get('conversation_id') or self.conversation
            self._streaming = ''
        elif etype == 'status':
            self._set_status(event.get('state', 'generating'), event.get('text', ''))
            if not getattr(self, '_streaming', ''):
                self.render('_' + event.get('text', '') + '_')
        elif etype == 'delta':
            self._streaming = getattr(self, '_streaming', '') + event.get('text', '')
            self.render(self._streaming)
        elif etype in ('replace', 'done'):
            self._streaming = event.get('text', '')
            if etype == 'done':
                self.messages.append({'role': 'assistant', 'content': self._streaming, 'id': event.get('message_id')})
                self.render()
                self._set_status('ready', 'Готова к работе')
            else:
                self.render(self._streaming)
        elif etype == 'error':
            self._set_status('offline', event.get('message', 'Ошибка'))
            self.messages.append({'role': 'assistant', 'content': '⚠️ ' + event.get('message', 'Ошибка соединения с хостом')})
            self.render()

    def run(self):
        self.root.mainloop()
