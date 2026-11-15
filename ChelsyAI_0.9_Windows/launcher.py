"""Chelsy AI Windows entrypoint. Web UI opens locally in the default browser."""
import os
import socket
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

# PyInstaller --windowed sets sys.stdout/sys.stderr to None on Windows.
# Uvicorn's DefaultFormatter calls sys.stderr.isatty(), so redirect both
# streams to persistent log files *before* importing and launching Gradio.
LOG_DIR = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChelsyAI'
LOG_DIR.mkdir(parents=True, exist_ok=True)
_RUNTIME_STREAM = None
if sys.stdout is None or sys.stderr is None:
    _RUNTIME_STREAM = (LOG_DIR / 'runtime.log').open('a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = _RUNTIME_STREAM
    if sys.stderr is None:
        sys.stderr = _RUNTIME_STREAM

# Gradio sends an internal HTTP request to its own loopback endpoint at startup.
# Some Windows proxy/VPN setups return HTTP 503 unless localhost bypasses proxies.
# Keep this limited to localhost; no public sharing or security bypass is enabled.
_loopback_hosts = ('localhost', '127.0.0.1', '::1')
for _key in ('NO_PROXY', 'no_proxy'):
    _current = [item.strip() for item in os.environ.get(_key, '').split(',') if item.strip()]
    _already = {item.lower() for item in _current}
    os.environ[_key] = ','.join(_current + [host for host in _loopback_hosts if host.lower() not in _already])

os.environ['CHELSY_APPDATA'] = '1'
from core import USERS_PATH, create_user, ollama_available


def find_ollama():
    direct = shutil.which('ollama')
    home = os.environ.get('LOCALAPPDATA', '')
    fallback = Path(home) / 'Programs' / 'Ollama' / 'ollama.exe'
    return direct or (str(fallback) if fallback.exists() else None)


def make_accounts():
    from tkinter import messagebox, simpledialog
    if USERS_PATH.exists():
        import json
        try:
            users = json.loads(USERS_PATH.read_text(encoding='utf-8'))
            if 'owner' in users and 'tester' in users:
                return True
        except (ValueError, OSError):
            pass
    messagebox.showinfo('Chelsy AI — настройка', 'Первый запуск: создадим аккаунты owner и tester. Для каждого нужен отдельный пароль минимум из 12 символов.')
    for name in ('owner', 'tester'):
        while True:
            password = simpledialog.askstring('Chelsy AI', f'Придумайте пароль для {name} (минимум 12 символов):', show='*')
            if password is None: return False
            confirm = simpledialog.askstring('Chelsy AI', f'Повторите пароль для {name}:', show='*')
            if confirm is None: return False
            if password != confirm or len(password) < 12:
                messagebox.showerror('Chelsy AI', 'Пароли не совпадают или содержат менее 12 символов.')
                continue
            from core import authenticate
            if USERS_PATH.exists():
                import json
                try:
                    existing = json.loads(USERS_PATH.read_text(encoding='utf-8'))
                except (ValueError, OSError):
                    existing = {}
                if name in existing:
                    break
            create_user(name, password)
            break
    return True


def setup_model():
    from tkinter import messagebox
    binary = find_ollama()
    if not binary or not ollama_available():
        messagebox.showwarning('Chelsy AI — Ollama', 'Ollama пока не обнаружена или не запущена. Интерфейс откроется, но для ответов нужна Ollama и хотя бы одна модель (рекомендуется qwen3:8b при 16 ГБ RAM или gemma3:4b на слабых ПК). Установить: https://ollama.com/download/windows')
        return
    try:
        import json
        from urllib.request import urlopen
        with urlopen('http://127.0.0.1:11434/api/tags',timeout=3) as response:
            models = json.load(response).get('models', [])
        if any(not any(h in m.get('name', '') for h in ('embed', 'bge', 'minilm')) for m in models): return
    except Exception: pass
    if messagebox.askyesno('Chelsy AI — модель', 'Скачать модель qwen3:4b (около 2,5 ГБ, хороший русский язык)? Для этого нужен интернет и свободное место на диске.'):
        messagebox.showinfo('Chelsy AI', 'Начинается загрузка модели. Откроется консоль с индикатором прогресса. Дождитесь завершения.')
        result = subprocess.run([binary, 'pull', 'qwen3:4b'], check=False)
        if result.returncode != 0:
            messagebox.showwarning('Chelsy AI', 'Модель не удалось загрузить. Позже выполните: ollama pull qwen3:4b')


def choose_ui_port():
    requested = os.environ.get('GRADIO_SERVER_PORT', '').strip()
    if requested:
        try:
            candidate = int(requested)
            if not 1 <= candidate <= 65535: raise ValueError()
        except ValueError:
            raise ValueError('GRADIO_SERVER_PORT must be an integer from 1 to 65535')
        ports = [candidate]
    else:
        ports = range(7860, 7891)
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise OSError('No free local UI port. Close another Chelsy instance.')


def main():
    from tkinter import Tk, messagebox
    root = Tk()
    root.withdraw()
    try:
        from supabase_auth import config as auth_config
        if auth_config() is None and not make_accounts(): return
        setup_model()
        root.destroy()
        import core
        import migrations
        # Versioned schema upgrade with an automatic backup of existing data.
        migrations.migrate(core.DB_PATH, log=lambda line: print('[Migrations]', line, flush=True))
        from app import build, authenticate, asset_path, launch_kwargs, is_admin_key
        from knowledge import services
        services()['scheduler'].start(owner_check=is_admin_key)
        app = build()
        app.queue(default_concurrency_limit=2, api_open=False)
        app.launch(server_name='127.0.0.1',server_port=choose_ui_port(),share=False,
                   auth=authenticate,auth_message='Вход в Chelsy AI — используй аккаунт Chelsy Group',
                   inbrowser=True,show_error=False,enable_monitoring=False,
                   favicon_path=str(asset_path('chelsy.ico')), **launch_kwargs())
    except Exception:
        logfile = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ChelsyAI' / 'error.log'
        logfile.parent.mkdir(parents=True,exist_ok=True)
        logfile.write_text(traceback.format_exc(),encoding='utf-8')
        if 'startup-events' in traceback.format_exc() and '503' in traceback.format_exc():
            with (logfile.parent / 'runtime.log').open('a', encoding='utf-8') as log:
                log.write('\nChelsy diagnostic: localhost startup-events returned 503. Check local proxy/VPN, port 7860, or app startup failure.\n')
        try: messagebox.showerror('Chelsy AI — ошибка',f'Не удалось запустить программу. Журнал: {logfile}')
        except Exception: pass
        raise

if __name__ == '__main__': main()
