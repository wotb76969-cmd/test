# Как соединить аккаунты сайта Chelsy Group и Chelsy AI

**Рекомендация:** не соединять браузер и локальную SQLite напрямую. Создать один публичный Auth/API Backend в РФ и пользоваться единым идентификатором пользователя (`user_id`) на сайте, Android и Windows.

## 1. Текущая ситуация

- Сайт Chelsy Group размещён на Netlify (публичный фронтенд), но точная система регистрации и база сайта пока **не известны**.
- Chelsy Desktop 0.7 использует локальные `users.json` и `chelsy.db`. Они остаются только для личной/закрытой беты через Tailscale.
- В 0.7 НЕ выполнена живая синхронизация с сайтом: без доступа к его исходникам нельзя безопасно утверждать, что она работает.

## 2. Целевая архитектура

```
Chelsy Group (Netlify)       Desktop / Android / Web Client
          \                         /
           HTTPS + авторизованный запрос
                       |
             Auth API + Chelsy API
              (российский VPS)
                       |
               PostgreSQL (РФ)
          users, identities, plans,
          subscriptions, conversations,
          messages, personal_memories
                       |
              AI/Research Worker
         (позже облако, сейчас ноутбук)
```

**Принцип:** сайт НЕ получает доступ к таблицам БД и не хранит пароль пользователей. Только backend проверяет права на данные. Нельзя синхронизировать таблицы `users.json` и `chelsy.db` копированием файлов.

## 3. Определить, что уже используется на сайте

- Если на сайте уже есть Auth provider (например, Supabase Auth, Firebase Auth, Clerk, Keycloak) — оставить его как единственный источник идентификации и проверять access-токены на API по официальным JWKS / SDK этого провайдера.
- Если есть только визуальные кнопки входа без настоящего backend — прежде всего реализовать регистрацию и безопасный OAuth/OIDC или управляемый Auth.
- Если сайт уже хранит пароли в своей базе — НЕ копировать пароли и хеши в Desktop. Продумать миграцию пользователей через сброс пароля / подтверждение входа у старого провайдера.

## 4. Минимальная схема PostgreSQL (пример для разработки)

```sql
CREATE TABLE app_users (
  user_id UUID PRIMARY KEY,
  auth_subject TEXT UNIQUE NOT NULL,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE plans (
  plan_code TEXT PRIMARY KEY,
  price_rub INTEGER NOT NULL CHECK (price_rub >= 0)
);
INSERT INTO plans(plan_code,price_rub) VALUES
  ('start',0),('plus',690),('pro',1490),('teams',2990);
CREATE TABLE subscriptions (
  subscription_id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES app_users(user_id),
  plan_code TEXT NOT NULL REFERENCES plans(plan_code),
  provider_subscription_id TEXT UNIQUE,
  status TEXT NOT NULL,
  current_period_end TIMESTAMPTZ
);
CREATE TABLE conversations (
  conversation_id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES app_users(user_id),
  title TEXT NOT NULL DEFAULT 'Новый чат',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE messages (
  message_id UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE personal_memories (
  user_id UUID NOT NULL REFERENCES app_users(user_id),
  memory_key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id,memory_key)
);
```

`user_id` назначает сервер; браузер не может сам выбирать чужой `user_id` для доступа. Оплата/подписка активируется только после подтверждения webhook провайдера и проверки его подписи. Для командного тарифа позже добавить таблицы organisations, memberships и seats.

## 5. Контракт API

- `GET /v1/me` — проверенный профиль, тариф, лимиты (сервер определяет пользователя из проверенного токена)
- `GET /v1/conversations` — только переписки текущего пользователя
- `POST /v1/conversations` — новый диалог
- `POST /v1/conversations/{id}/messages` — отправка сообщения, сервер проверяет владельца диалога и лимит
- `GET /v1/memory` — личные воспоминания
- `PUT /v1/memory/{key}` и `DELETE /v1/memory/{key}` — управление личной памятью
- `POST /v1/billing/checkout` — создание платёжной сессии только на сервере
- `POST /v1/billing/webhook` — проверка подписи и состояния платежа

Для Android/Windows использовать PKCE/OIDC авторизацию с системным браузером (не собирать пароль в собственном окне клиента), хранить токены в защищённом хранилище ОС. Для веба — безопасные сессии с HttpOnly/Secure/SameSite cookie либо подходящий PKCE-вариант с CSRF/CORS контролем. Не хранить долговременные токены в localStorage без оценки угроз.

## 6. Перенос нынешних аккаунтов owner/tester

- В 0.7 это локальные пользователи. Не копировать их пароли/хеши в облако.
- Зарегистрировать пользователей на сайте и подтвердить их владение аккаунтами.
- Однократно экспортировать историю и личную память локального пользователя, затем импортировать в новую БД для подтверждённого `user_id`.
- Перед импортом делать резервную копию. Проверять изоляцию данных между пользователями и запрет доступа к чужим чатам.

## 7. Этапы подключения

1. Уточнить используемую сейчас БД/авторизацию Chelsy Group и получить доступ к исходникам сайта.
2. Выбрать один auth-провайдер, развернуть защищённый API и PostgreSQL в РФ; настроить TLS, резервные копии и защиту секретов.
3. Реализовать `/v1/me` и вход на сайте, проверить токены, корректные logout/refresh.
4. Подключить Android и Windows к тому же API; проверить отдельность данных пользователей.
5. Добавить биллинг Start/Plus/Pro/Teams, лимиты запросов и аудит webhooks.
6. Позднее внедрить региональное размещение и правомерную передачу данных для международного рынка.

Нужны исходники/экспорт структуры сайта и информация о действующей авторизации; **пока это подробное ТЗ, не подключённая база**.
