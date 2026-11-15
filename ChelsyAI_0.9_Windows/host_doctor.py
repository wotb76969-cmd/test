"""Chelsy Host readiness check ("доктор") and Tailscale helper.

    ChelsyHost.exe --doctor            # проверить, готов ли хост обслуживать устройства
    ChelsyHost.exe --setup-tailscale   # опубликовать хост в своей сети Tailscale (tailscale serve)

Nothing here changes data. --setup-tailscale only runs ``tailscale serve --bg <port>``
(HTTPS inside your private tailnet; never Funnel / public internet).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

OK, WARN, FAIL = 'OK', 'ВНИМАНИЕ', 'ОШИБКА'


def tailscale_binary():
    found = shutil.which('tailscale')
    if found:
        return found
    for candidate in (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale' / 'tailscale.exe',
                      Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale')):
        if candidate.exists():
            return str(candidate)
    return None


def _run(args, timeout=8):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, '', str(exc)


def tailscale_info():
    """{'installed', 'running', 'url', 'serving'} for this machine."""
    binary = tailscale_binary()
    info = {'installed': bool(binary), 'running': False, 'url': '', 'serving': False}
    if not binary:
        return info
    code, out, _ = _run([binary, 'status', '--json'])
    if code == 0:
        try:
            data = json.loads(out)
            info['running'] = data.get('BackendState') == 'Running'
            dns = ((data.get('Self') or {}).get('DNSName') or '').rstrip('.')
            if dns:
                info['url'] = 'https://' + dns
        except ValueError:
            pass
    code, out, _ = _run([binary, 'serve', 'status', '--json'])
    if code == 0 and out.strip() and out.strip() not in ('{}', 'null'):
        info['serving'] = str(port()) in out
    return info


def port():
    try:
        return int(os.environ.get('CHELSY_HOST_PORT', '8787'))
    except ValueError:
        return 8787


def checks():
    import core
    import migrations
    import ollama_client
    from supabase_auth import AuthError, admin_user_id, config
    results = []

    def add(status, name, detail, fix=''):
        results.append({'status': status, 'name': name, 'detail': detail, 'fix': fix})

    # Data and storage
    try:
        core.DATA.mkdir(parents=True, exist_ok=True)
        probe = core.DATA / '.write_test'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        info = migrations.inspect(core.DB_PATH)
        backups = len(list((core.DATA / 'backups').glob('*.db'))) if (core.DATA / 'backups').exists() else 0
        add(OK, 'Локальные данные', f'{core.DATA} · схема v{info["version"]}/{migrations.SCHEMA_VERSION} · резервных копий: {backups}')
    except OSError as exc:
        add(FAIL, 'Локальные данные', f'Нет доступа на запись: {exc}', 'Проверь права на папку %LOCALAPPDATA%\\ChelsyAI')

    # Accounts
    try:
        cfg = config()
        if cfg:
            add(OK, 'Supabase Auth', cfg[0])
            add(OK if admin_user_id() else WARN, 'Владелец (администратор)',
                admin_user_id() or 'UUID владельца не указан — общая база знаний и автоизучение будут без владельца',
                'Запусти «Chelsy AI — настройка Supabase» и укажи свой UUID')
        else:
            add(FAIL, 'Supabase Auth', 'Не настроен — устройства не смогут войти', 'Запусти «Chelsy AI — настройка Supabase»')
    except AuthError as exc:
        add(FAIL, 'Supabase Auth', str(exc), 'Запусти «Chelsy AI — настройка Supabase»')

    # Model
    if ollama_client.available():
        models = ollama_client.list_models()
        embed = [m for m in ollama_client.list_models(include_embedding=True) if m not in models]
        if models:
            best = ollama_client.resolve_model(None, os.environ.get('CHELSY_MODEL') or None)
            add(OK, 'Ollama и модели', f'установлены: {", ".join(models)} · по умолчанию: {best}')
        else:
            add(FAIL, 'Ollama и модели', 'Ollama работает, но нет ни одной модели', 'ollama pull qwen3:8b')
        add(OK if embed else WARN, 'Смысловой поиск по знаниям',
            ', '.join(embed) if embed else 'модель эмбеддингов не установлена — работает только полнотекстовый поиск',
            'ollama pull nomic-embed-text')
    else:
        add(FAIL, 'Ollama', 'не запущена — нейросеть не сможет отвечать', 'Установи Ollama с ollama.com и запусти её')

    # Internet & autonomous learning
    import live_tools
    from knowledge import autolearn
    from knowledge.search_manager import SearchManager
    online = live_tools.internet_available()
    add(OK if online else WARN, 'Интернет', 'есть' if online else 'нет — работают только локальные знания')
    add(OK if SearchManager.configured() else WARN, 'Поисковый API (Brave)',
        'настроен' if SearchManager.configured() else 'не задан — нет свежего поиска и «любопытства» автоизучения',
        'Задай переменную окружения CHELSY_BRAVE_SEARCH_KEY на этом ноутбуке')
    learn = autolearn.settings()
    add(OK if learn['enabled'] else WARN, 'Автоизучение',
        ('включено' if learn['enabled'] else 'выключено') +
        f' · из поиска: {"да" if learn["from_search"] else "нет"} · любопытство: {"да" if learn["curiosity"] else "нет"}'
        f' · лимит в день: {learn["daily_jobs"]} · мин. надёжность: {learn["min_trust"]}')

    # Host process
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port()}/api/health', timeout=2) as resp:
            running = json.load(resp).get('ok') is True
    except Exception:
        running = False
    add(OK if running else WARN, 'Chelsy Host', f'запущен на 127.0.0.1:{port()}' if running else 'сейчас не запущен',
        'Запусти «Chelsy AI Host»')

    # Remote devices
    ts = tailscale_info()
    if not ts['installed']:
        add(FAIL, 'Tailscale', 'не установлен — телефон и другие ПК не подключатся', 'Установи Tailscale с tailscale.com/download')
    elif not ts['running']:
        add(FAIL, 'Tailscale', 'установлен, но не подключён', 'Открой Tailscale и войди в свою сеть')
    elif not ts['serving']:
        add(WARN, 'Tailscale Serve', f'хост ещё не опубликован в сети ({ts["url"] or "адрес неизвестен"})',
            'Запусти «Chelsy AI Host — включить доступ с устройств» или: tailscale serve --bg ' + str(port()))
    else:
        add(OK, 'Доступ с устройств', f'адрес для клиентов: {ts["url"]}')
    return results, ts


def print_report():
    results, ts = checks()
    width = max(len(r['name']) for r in results)
    print('\nChelsy AI Host — проверка готовности\n' + '=' * 60)
    for r in results:
        print(f'[{r["status"]:^9}] {r["name"]:<{width}}  {r["detail"]}')
        if r['status'] != OK and r['fix']:
            print(' ' * 12 + ' ' * width + '  → ' + r['fix'])
    print('=' * 60)
    if ts.get('url') and ts.get('serving'):
        print(f'Адрес для Windows Client и Android: {ts["url"]}')
    failed = [r for r in results if r['status'] == FAIL]
    print('Готово к работе на всех устройствах.' if not failed else f'Нужно исправить пунктов: {len(failed)}.')
    return 1 if failed else 0


def setup_tailscale():
    binary = tailscale_binary()
    if not binary:
        print('Tailscale не установлен: https://tailscale.com/download')
        return 1
    code, out, err = _run([binary, 'serve', '--bg', str(port())], timeout=30)
    print(out or err)
    if code != 0:
        print('Не получилось включить Tailscale Serve. Убедись, что Tailscale подключён и HTTPS включён в админке tailnet.')
        return code
    info = tailscale_info()
    print(f'Готово. Адрес для клиентов: {info["url"] or "смотри tailscale status"}')
    print('Это доступ только внутри твоей сети Tailscale. Не используй Tailscale Funnel.')
    return 0


def wait_if_frozen():
    if getattr(sys, 'frozen', False):
        try:
            input('\nНажми Enter, чтобы закрыть окно…')
        except EOFError:
            pass
