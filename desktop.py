"""Лёгкий desktop-клиент PixelForge.

Важно:
- клиент НЕ запускает FastAPI и не импортирует api.app;
- клиент поднимает только локальный статический сервер для своих HTML/CSS/JS;
- все операции (выбор источника, настройки, старт, прогресс, лог) идут
  через HTTP API, запущенный отдельно: python server.py.

UI: desktop_ui/ (800x600, ТЗ п.5).
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent / "desktop_ui"
DEFAULT_API = "http://127.0.0.1:8000"
WINDOW_TITLE = "PixelForge — Конвертер изображений"


class _QuietHandler(SimpleHTTPRequestHandler):
    """Статика UI без спама в консоль."""

    def log_message(self, format, *args):  # noqa: A002
        pass


class DesktopBridge:
    """JS-мост для нативных диалогов. Данные всё равно уходят в API."""

    def choose_folder(self) -> str | None:
        try:
            import webview  # type: ignore
        except ImportError:
            return None
        if not webview.windows:
            return None
        file_dialog = getattr(webview, "FileDialog", None)
        dialog_type = file_dialog.FOLDER if file_dialog else webview.FOLDER_DIALOG
        result = webview.windows[0].create_file_dialog(dialog_type)
        if not result:
            return None
        first = result[0] if isinstance(result, (list, tuple)) else result
        return str(first)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_ui_server() -> tuple[ThreadingHTTPServer, str]:
    """Статический сервер только для файлов desktop_ui.

    Это не бизнес-сервер: он не умеет конвертировать и не знает про API.
    """
    handler = partial(_QuietHandler, directory=str(UI_DIR))
    httpd = ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="pixelforge-ui").start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _api_ready(api_base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{api_base}/api/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _run_browser_mode(url: str) -> int:
    webbrowser.open(url)
    print(f"Desktop UI: {url}")
    print("Закройте окно или нажмите Ctrl+C для выхода.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def run(api_base: str | None = None) -> int:
    """Запуск desktop-клиента.

    api_base можно передать аргументом --api или переменной PIXELFORGE_API.
    """
    api = (api_base or os.environ.get("PIXELFORGE_API") or DEFAULT_API).rstrip("/")
    httpd, ui_base = _start_ui_server()
    # API-адрес передаётся UI через query string: никакого зашитого localhost.
    ui_url = f"{ui_base}/index.html?api={urllib.parse.quote(api, safe='')}"

    if not _api_ready(api):
        print(f"Внимание: API {api} недоступен. Запустите: python server.py")
    else:
        print(f"API: {api}")

    try:
        import webview  # type: ignore
    except ImportError:
        webview = None  # type: ignore[assignment]

    if webview is None:
        try:
            return _run_browser_mode(ui_url)
        finally:
            httpd.shutdown()

    try:
        webview.create_window(
            WINDOW_TITLE,
            ui_url,
            width=800,
            height=600,
            resizable=False,
            background_color="#1E1E1E",
            js_api=DesktopBridge(),
        )
        # Блокирует главный поток до закрытия окна.
        webview.start()
        return 0
    except Exception as exc:  # noqa: BLE001 - WebView2 может отсутствовать
        print(f"Нативное окно недоступно ({exc}). Открываю браузер.")
        return _run_browser_mode(ui_url)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    raise SystemExit(run())
