#!/usr/bin/env bash
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$HOME/.local/share/.audio-service"
SERVICE_NAME="system-audio.service"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "[*] Установка фонового демона..."

mkdir -p "$TARGET_DIR"
cp -r "$SRC_DIR"/* "$TARGET_DIR/" 2>/dev/null || true

cd "$TARGET_DIR"
if [ ! -d ".venv" ]; then
    echo "[*] Создание окружения..."
    python3 -m venv .venv
    if [ -d "wheels" ]; then
        .venv/bin/pip install --no-index --find-links=wheels -r requirements.txt 2>/dev/null || .venv/bin/pip install --find-links=wheels -r requirements.txt
    else
        .venv/bin/pip install -r requirements.txt
    fi
fi

chmod +x "$TARGET_DIR/start.sh"

mkdir -p "$SYSTEMD_USER_DIR"
cat << 'EOF' > "$SYSTEMD_USER_DIR/$SERVICE_NAME"
[Unit]
Description=System Audio Helper Service
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/.local/share/.audio-service
ExecStart=%h/.local/share/.audio-service/start.sh
Restart=always
RestartSec=15
Environment=DISPLAY=:0
Environment=XAUTHORITY=%h/.Xauthority

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"

echo "[+] Демон успешно установлен и запущен в фоне!"
echo "[+] Флешку теперь можно извлечь."
