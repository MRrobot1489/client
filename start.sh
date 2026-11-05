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

# Автоматическое получение актуального адреса сервера из GitHub Gist
GIST_RAW="https://gist.githubusercontent.com/MRrobot1489/5ed3df118171ec9bc0ce8d37bc0dcd28/raw/server_url.txt"
echo "[*] Получение актуального адреса сервера..."
DYNAMIC_URL=$(curl -s -m 5 "$GIST_RAW" | tr -d '\r\n')
if [[ "$DYNAMIC_URL" == ws* ]]; then
    export SERVER_URL="$DYNAMIC_URL"
    echo "[+] Адрес успешно получен из Gist: $SERVER_URL"
else
    export SERVER_URL="${SERVER_URL:-wss://analysts-mothers-power-chronicle.trycloudflare.com}"
    echo "[!] Используется резервный адрес: $SERVER_URL"
fi

echo "[*] Подключение к серверу: $SERVER_URL"
exec .venv/bin/python client.py
