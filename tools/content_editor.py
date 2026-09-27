"""
Локальный редактор контента игры - HTTP API (стандартная библиотека) поверх
content_validation.py + окно pywebview вместо системного браузера (см.
tools/web_ui.py - тот же приём, чтобы выглядело отдельным приложением, а не
вкладкой браузера). Единственная внешняя зависимость - pywebview (см.
requirements.txt).

В exe-сборке (см. build_exe.ps1/content_editor.spec) - это ПОРТАТИВНЫЙ
dev-инструмент: он живёт прямо в корне репозитория (рядом с main.py) и
редактирует РЕАЛЬНЫЙ game/content.json этого репозитория, а не какую-то
запечённую в сборку копию - в отличие от Igra.exe (там наоборот, копия
content.json нарочно вморожена внутрь, чтобы exe был самодостаточным и
переносимым другу). Поэтому content_editor.spec НЕ упаковывает content.json
как data - только сам код и content_editor.html.

Запуск:
    python tools/content_editor.py

Откроет редактор в отдельном окне. Любое сохранение проверяется на битые
ссылки (несуществующие технологии/постройки/ресурсы и т.п.) - если что-то
не сходится, запись в файл не происходит и редактор покажет список
конкретных ошибок. Перед каждой успешной записью старая версия content.json
копируется в game/content_backups/ - если что-то пошло не так по смыслу
(не по структуре, так что редактор не поймал), можно откатиться руками.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path

import webview

# В exe-сборке (PyInstaller, --windowed) sys.stdout/sys.stderr бывают None -
# голый print() уронил бы процесс ещё до того, как он успеет что-то показать.
if getattr(sys, "frozen", False):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

# В exe-сборке редактор - портативный инструмент, живущий прямо в репозитории
# (см. докстринг выше) - ROOT должен указывать на папку, где лежит сам exe
# (ожидается корень репозитория), а НЕ внутрь распакованной PyInstaller-сборки.
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_validation import validate  # noqa: E402

CONTENT_PATH = ROOT / "game" / "content.json"
BACKUP_DIR = ROOT / "game" / "content_backups"
# Шаблоны повторяющихся "лестниц" карточек (тиры склада/инструмента, улучшения
# построек) - см. content_editor.html: TEMPLATES/templateListButtons. Это данные
# САМОГО РЕДАКТОРА (черновик удобства ввода), не игровой баланс - живёт рядом с
# content_editor.html (не в game/), не валидируется через content_validation.py
# и не бэкапится при записи (в отличие от content.json).
TEMPLATES_PATH = ROOT / "tools" / "content_editor_templates.json"
# content_editor.html - часть кода редактора, а не проекта - его ищем рядом с
# самим файлом (bundle-relative, PyInstaller кладёт его туда же, см. datas в
# content_editor.spec), в отличие от путей выше, которые всегда смотрят на
# реальный репозиторий.
HTML_PATH = Path(__file__).with_name("content_editor.html")
# Общий с игрой код оформления механизма (превью окна «Оформление») - bundle-relative,
# как HTML_PATH (datas в content_editor.spec), см. докстринг tools/mechanism_fx.js.
MECHANISM_FX_PATH = Path(__file__).with_name("mechanism_fx.js")
# Кастомные звуки ручных действий (manual_actions.*.sound) - см. tools/web_ui.py:
# SOUNDS_DIR, одна и та же папка обслуживает и превью в редакторе, и саму игру.
# Это данные ПРОЕКТА (общие с игрой), поэтому путь считается от ROOT, как
# CONTENT_PATH, а не bundle-relative, как HTML_PATH.
SOUNDS_DIR = ROOT / "tools" / "sounds"


def _show_error(text: str) -> None:
    if getattr(sys, "frozen", False):
        ctypes.windll.user32.MessageBoxW(None, text, "Редактор контента", 0x10)
    else:
        print(text)
_ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SOUND_EXTENSIONS = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
_SOUND_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(wav|mp3|ogg)$")
# Разумный потолок для короткого звукового эффекта - защита от случайной
# загрузки огромного файла, а не полноценная квота хранилища.
_MAX_SOUND_BYTES = 5 * 1024 * 1024
# Тот же файл, что main.py/tools/web_ui.py используют как автосейв игрока - редактор
# специально не импортирует game/state.py (см. CLAUDE.md про изоляцию tools/ от
# game/), поэтому "обнулить прогресс" здесь означает просто удалить файл: при
# следующем запуске main.py/web_ui.py сами создадут свежий GameState() - им уже
# известно как обращаться с сохранением, дублировать эту логику тут не нужно.
SAVE_PATH = ROOT / "saves" / "autosave.json"
# Старое место автосохранения (до переноса в saves/) - если игрок открывает
# редактор раньше web_ui.py/main.py (которые сами переносят файл при старте),
# "Обнулить прогресс" должно почистить и его, а не молча оставить как есть.
_LEGACY_SAVE_PATH = ROOT / "save.json"
PORT = 8765


def load_content() -> dict:
    with open(CONTENT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_templates() -> dict:
    if not TEMPLATES_PATH.exists():
        return {}
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_html(HTML_PATH.read_text(encoding="utf-8"))
        elif self.path == "/mechanism_fx.js":
            body = MECHANISM_FX_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/content":
            self._send_json(200, load_content())
        elif self.path == "/api/templates":
            self._send_json(200, load_templates())
        elif self.path.startswith("/sounds/"):
            self._serve_sound(self.path[len("/sounds/"):])
        else:
            self._send_json(404, {"error": "not found"})

    def _serve_sound(self, filename: str) -> None:
        # Та же санитизация, что и в tools/web_ui.py:_serve_sound - только для
        # предпрослушивания в редакторе, файл и так уже прошёл её при загрузке.
        if not _SOUND_FILENAME_RE.match(filename):
            self._send_json(404, {"error": "not found"})
            return
        path = SOUNDS_DIR / filename
        if not path.is_file():
            self._send_json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _SOUND_EXTENSIONS[path.suffix.lower().lstrip(".")])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/reset_save":
            existed = SAVE_PATH.exists() or _LEGACY_SAVE_PATH.exists()
            if SAVE_PATH.exists():
                SAVE_PATH.unlink()
            if _LEGACY_SAVE_PATH.exists():
                _LEGACY_SAVE_PATH.unlink()
            self._send_json(200, {"ok": True, "existed": existed})
            return

        if self.path == "/api/upload_sound":
            self._handle_upload_sound()
            return

        if self.path == "/api/templates":
            self._handle_save_templates()
            return

        if self.path != "/api/content":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            self._send_json(400, {"errors": [f"Некорректный JSON: {e}"]})
            return

        errors = validate(data)
        if errors:
            self._send_json(400, {"errors": errors})
            return

        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(CONTENT_PATH, BACKUP_DIR / f"content-{stamp}.json")

        with open(CONTENT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self._send_json(200, {"ok": True})

    def _handle_upload_sound(self) -> None:
        """Сохраняет кастомный звук ручного действия на диск (tools/sounds/<action_id>.<ext>,
        перезаписывает при повторной загрузке). Файл на content.json не влияет -
        клиент сам проставляет вернувшееся имя в manual_actions.<id>.sound и
        сохраняет контент обычным способом (POST /api/content)."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"Некорректный JSON: {e}"})
            return

        action_id = payload.get("action_id")
        ext = payload.get("ext")
        data_base64 = payload.get("data_base64")
        if not isinstance(action_id, str) or not _ACTION_ID_RE.match(action_id):
            self._send_json(400, {"error": "action_id должен быть snake_case id ручного действия"})
            return
        if ext not in _SOUND_EXTENSIONS:
            self._send_json(400, {"error": f"ext должен быть одним из: {', '.join(_SOUND_EXTENSIONS)}"})
            return
        if not isinstance(data_base64, str):
            self._send_json(400, {"error": "data_base64 обязателен"})
            return
        try:
            audio_bytes = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError):
            self._send_json(400, {"error": "data_base64: некорректная base64-строка"})
            return
        if not audio_bytes:
            self._send_json(400, {"error": "пустой файл"})
            return
        if len(audio_bytes) > _MAX_SOUND_BYTES:
            self._send_json(400, {"error": f"файл больше {_MAX_SOUND_BYTES // (1024 * 1024)} МБ"})
            return

        filename = f"{action_id}.{ext}"
        SOUNDS_DIR.mkdir(exist_ok=True)
        (SOUNDS_DIR / filename).write_bytes(audio_bytes)
        self._send_json(200, {"ok": True, "filename": filename})

    def _handle_save_templates(self) -> None:
        """Перезаписывает content_editor_templates.json целиком - клиент сам ведёт
        структуру {kind: [{name, items}, ...]} и шлёт её всю при любом изменении
        (как /api/content, только без validate()/бэкапа - это не игровой баланс)."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"Некорректный JSON: {e}"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"error": "ожидался объект"})
            return

        with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self._send_json(200, {"ok": True})

    def log_message(self, format: str, *args) -> None:
        pass  # тише в консоли - не засоряем вывод обычными запросами


class _ThreadedTestHTTPServer(ThreadingMixIn, HTTPServer):
    # ТОЛЬКО для --server-only (автотесты) - см. tools/web_ui.py:
    # _ThreadedTestHTTPServer, тот же приём и та же причина (CLAUDE.md).
    daemon_threads = True


def _serve(server: HTTPServer) -> None:
    try:
        server.serve_forever()
    except Exception as e:
        _show_error(f"Редактор неожиданно остановился:\n{e}")


def main() -> None:
    url = f"http://127.0.0.1:{PORT}/"
    server_only = "--server-only" in sys.argv
    server_cls = _ThreadedTestHTTPServer if server_only else HTTPServer
    try:
        server = server_cls(("127.0.0.1", PORT), Handler)
    except OSError:
        _show_error(
            f"Не получилось занять порт {PORT} - возможно, редактор уже запущен в другом окне.\n"
            f"Если так, просто открой {url} в браузере."
        )
        return

    print(f"Редактор контента запущен: {url}")

    if server_only:
        # Без нативного окна pywebview - для автотестов (Playwright и т.п.),
        # см. tools/web_ui.py: main() - тот же приём и та же причина (CLAUDE.md).
        print("Режим без окна: подключайся сам (напр. Playwright) к адресу выше.")
        _serve(server)
        print("Остановлено.")
        return

    print("Закрой окно редактора, когда закончишь.")
    # serve_forever() уходит в фоновый поток - webview.start() ниже занимает
    # главный поток сам (см. tools/web_ui.py: main() - тот же приём).
    threading.Thread(target=_serve, args=(server,), daemon=True).start()

    webview.create_window(
        "Редактор контента игры", url, width=1400, height=900, min_size=(1024, 700), maximized=True
    )
    webview.start()
    server.shutdown()
    print("Остановлено.")


if __name__ == "__main__":
    main()
