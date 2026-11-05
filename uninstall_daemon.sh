#!/usr/bin/env bash
SERVICE_NAME="system-audio.service"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
TARGET_DIR="$HOME/.local/share/.audio-service"

echo "[*] Остановка и удаление демона..."
systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SYSTEMD_USER_DIR/$SERVICE_NAME"
systemctl --user daemon-reload
rm -rf "$TARGET_DIR"

echo "[+] Демон полностью удален."
