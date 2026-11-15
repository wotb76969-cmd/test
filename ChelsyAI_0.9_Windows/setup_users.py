"""Interactive initial registration of owner and one tester; no default passwords."""
from getpass import getpass
from core import USERS_PATH, create_user

if USERS_PATH.exists():
    raise SystemExit('Учётные записи уже созданы. Для безопасности скрипт не перезаписывает их.')
print('Chelsy AI 0.2 — настройка двух учётных записей.')
for name in ('owner', 'tester'):
    while True:
        password = getpass(f'Придумай пароль для {name} (от 12 символов): ')
        confirm = getpass('Повтори пароль: ')
        if password != confirm:
            print('Пароли не совпадают.')
            continue
        try:
            create_user(name, password)
            break
        except ValueError as e:
            print(e)
print('Готово! Не отправляй пароли в чате и не копируй файл data/users.json.')
