"""Chelsy AI client for a private Tailscale HTTPS host; standard library Tk GUI."""
from __future__ import annotations
import base64
import json
import queue
import threading
from pathlib import Path
import os
from tkinter import Tk, Text, END, StringVar, Entry, Button, Label, Frame, messagebox, scrolledtext
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from supabase_auth import config as supabase_config, login as supabase_login, refresh as supabase_refresh, AuthError

APP_BG = '#111225'
ACCENT = '#a78bfa'


def validate_host(host):
    host = host.strip().rstrip('/')
    u = urlsplit(host)
    if u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('Enter only the host address, without extra paths or credentials.')
    if u.scheme == 'https' and u.hostname:
        return host
    if host in ('http://127.0.0.1:8787','http://localhost:8787'):
        return host  # same PC smoke test only
    raise ValueError('Use an HTTPS Tailscale Serve URL. HTTP allowed only for localhost tests.')


def request_api(base, username, password, endpoint, payload=None, bearer=None):
    base = validate_host(base)
    if bearer:
        headers = {'Authorization': 'Bearer '+bearer, 'Accept':'application/json'}
    else:
        if not username or not password: raise ValueError('Enter username and password.')
        token = base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
        headers = {'Authorization': 'Basic '+token, 'Accept':'application/json'}
    raw = None
    if payload is not None:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = Request(base+endpoint, headers=headers, data=raw)
    opener = build_opener(ProxyHandler({}))  # do not send private credentials through a system proxy
    try:
        with opener.open(req, timeout=190) as resp:
            return json.load(resp)
    except HTTPError as e:
        try:
            description = json.load(e).get('error','HTTP '+str(e.code))
        except Exception:
            description = 'HTTP '+str(e.code)
        if e.code == 401:
            raise AuthError('Session expired or unauthorized') from e
        raise RuntimeError(description) from e
    except URLError as e:
        raise RuntimeError('Не удалось подключиться. Проверь Tailscale, адрес и включён ли ноутбук.') from e


class Client:
    def __init__(self):
        self.root=Tk()
        self.root.title('Chelsy AI · Client')
        self.root.geometry('900x700')
        self.root.configure(bg=APP_BG)
        self.settings_path=Path(os.getenv('LOCALAPPDATA') or Path.home())/'ChelsyAI'/'client.json'
        try:
            saved=json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError,ValueError): saved={}
        self.host=StringVar(value=saved.get('host','https://your-pc.your-tailnet.ts.net'))
        self.username=StringVar(value='' if supabase_config() else 'tester')
        self.access_token=None
        self.refresh_token=None
        self.password=StringVar()
        self.model=StringVar(value='llama3.2:1b')
        self.queue=queue.Queue()
        self.busy=False
        pane=Frame(self.root,bg=APP_BG)
        pane.pack(fill='x',padx=12,pady=12)
        for label,var,hide in [('Адрес Chelsy Host',self.host,None),('Логин',self.username,None),('Пароль',self.password,'*'),('Модель',self.model,None)]:
            Label(pane,text=label,fg='white',bg=APP_BG).pack(anchor='w')
            Entry(pane,textvariable=var,show=hide,width=85).pack(fill='x',pady=(0,6))
        actions=Frame(pane,bg=APP_BG)
        actions.pack(fill='x')
        Button(actions,text='Войти и открыть историю',command=self.connect,bg=ACCENT).pack(side='left')
        Button(actions,text='☁ Синхронизация',command=self.sync_cloud,bg='#4d4076',fg='white').pack(side='left',padx=8)
        Button(actions,text='Обновить чат',command=self.load_history,bg='#4d4076',fg='white').pack(side='left')
        self.status=Label(pane,text='Нет соединения',fg='#a6a0c4',bg=APP_BG)
        self.status.pack(anchor='w',pady=(5,0))
        self.chat=scrolledtext.ScrolledText(self.root,wrap='word',bg='#1b1d34',fg='white',insertbackground='white',font=('Segoe UI',11))
        self.chat.pack(fill='both',expand=True,padx=12,pady=10)
        sendbox=Frame(self.root,bg=APP_BG)
        sendbox.pack(fill='x',padx=12,pady=10)
        self.message=Entry(sendbox,font=('Segoe UI',12))
        self.message.pack(side='left',fill='x',expand=True)
        self.message.bind('<Return>',lambda e:self.send())
        Button(sendbox,text='Отправить',command=self.send,bg=ACCENT).pack(side='left',padx=8)
        self.root.after(100,self.poll)

    def update_ui(self, output):
        self.chat.delete('1.0',END)
        for m in output:
            author='Вы' if m.get('role')=='user' else 'Chelsy AI'
            self.chat.insert(END,author+':\n'+m.get('content','')+'\n\n')
        self.chat.see(END)

    def background(self, endpoint, payload=None):
        if self.busy:return
        self.busy=True
        host,user,pwd=self.host.get(),self.username.get(),self.password.get()
        bearer=self.access_token
        def job():
            try:
                try:
                    response=request_api(host,user,pwd,endpoint,payload,bearer=bearer)
                except AuthError:
                    if not (bearer and self.refresh_token): raise
                    refreshed=supabase_refresh(self.refresh_token)
                    self.queue.put(('refresh',refreshed))
                    response=request_api(host,user,pwd,endpoint,payload,bearer=refreshed['access_token'])
                self.queue.put(('ok',response))
            except Exception as exc:self.queue.put(('error',str(exc)))
        threading.Thread(target=job,daemon=True).start()

    def remember_host(self):
        try:
            host=validate_host(self.host.get())
            self.settings_path.parent.mkdir(parents=True,exist_ok=True)
            self.settings_path.write_text(json.dumps({'host':host}),encoding='utf-8')
        except (OSError,ValueError): pass

    def sync_cloud(self):
        if not self.access_token:
            messagebox.showinfo('Chelsy AI','Для синхронизации нужен вход через Supabase.')
            return
        self.background('/api/sync')

    def connect(self):
        self.remember_host()
        if supabase_config():
            if self.busy:return
            self.busy=True
            email,passwd=self.username.get(),self.password.get()
            def job():
                try:
                    result=supabase_login(email,passwd)
                    self.queue.put(('login',result))
                except Exception as exc:self.queue.put(('error',str(exc)))
            threading.Thread(target=job,daemon=True).start()
        else:
            self.load_history()

    def load_history(self):self.background('/api/history')
    def send(self):
        msg=self.message.get().strip()
        if not msg:return
        self.message.delete(0,END)
        if supabase_config() and not self.access_token:
            messagebox.showinfo('Chelsy AI','Сначала войди через Supabase.')
            return
        self.background('/api/chat',{'message':msg,'model':self.model.get().strip()})

    def poll(self):
        while not self.queue.empty():
            typ,payload=self.queue.get_nowait()
            self.busy=False
            if typ=='error':
                self.status.config(text='Ошибка подключения')
                messagebox.showerror('Chelsy AI',payload)
            elif typ=='refresh':
                self.access_token=payload['access_token']
                self.refresh_token=payload.get('refresh_token',self.refresh_token)
            elif typ=='login':
                self.access_token=payload['access_token']
                self.refresh_token=payload.get('refresh_token')
                self.password.set('')
                self.load_history()
            elif 'messages' in payload:
                self.update_ui(payload['messages'])
                self.status.config(text='Подключено · облако: '+str(payload.get('cloud','см. синхронизацию')))
            elif 'received' in payload:
                self.status.config(text='☁ Синхронизация завершена')
                self.load_history()
        self.root.after(150,self.poll)

    def run(self):self.root.mainloop()

if __name__=='__main__': Client().run()
