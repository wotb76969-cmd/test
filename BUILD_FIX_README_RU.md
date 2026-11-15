# Chelsy AI 0.9.1 — исправление UI-тестов при Supabase Auth

## Исправление

В `ChelsyAI_0.9_Windows/test_ui06.py` тесты сохранения памяти теперь временно подменяют `desktop_auth.config` так, чтобы использовать тестовый локальный режим вместо существующего профиля Supabase пользователя Windows. Настройка ограничена временем выполнения конкретных тестов через `setUp` и `addCleanup`. Production-код (`app.py`, `desktop_auth.py`, API и базы данных) не менялся.

## Почему появлялась ошибка

При уже подключённом Supabase тесты `test_memory_form_saves_only_current_user` и `test_memory_form_rejects_invalid` создавали фиктивные запросы с именами `owner` и `tester`. В Supabase-режиме эти имена не являются подтверждёнными аккаунтами; правильная проверка `app.user()` бросала `gradio.exceptions.Error: Войди в Chelsy AI.`.

## Установка

1. Сделай резервную копию `%LOCALAPPDATA%\ChelsyAI` (на всякий случай).
2. Распакуй полный ZIP в новую папку, например `E:\ChelsyBuild091UIFix`.
3. Открой `ChelsyAI_0.9_Windows` и запусти `BUILD_INSTALLER.bat`.
4. После успешной сборки ищи `installer\ChelsyAI_Setup_0.9.exe`.

При желании можно перенести только файл `ChelsyAI_0.9_Windows/test_ui06.py` поверх одноимённого файла в уже распакованном проекте 0.9.1. Не нужно менять пароль или ключ Supabase.

Проверено: 42 unit-теста в окружении разработки без Supabase и с имитированным локальным конфигом Supabase. Сборка установщика на Windows здесь не выполнялась.
