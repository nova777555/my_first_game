"""
Локальный редактор контента игры - GUI в браузере, без установки зависимостей
(только стандартная библиотека Python).

Запуск:
    python tools/content_editor.py

Откроет http://127.0.0.1:8765 в браузере. Любое сохранение проверяется на
битые ссылки (несуществующие технологии/постройки/ресурсы и т.п.) - если
что-то не сходится, запись в файл не происходит и редактор покажет список
конкретных ошибок. Перед каждой успешной записью старая версия content.json
копируется в game/content_backups/ - если что-то пошло не так по смыслу
(не по структуре, так что редактор не поймал), можно откатиться руками.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_validation import validate  # noqa: E402

CONTENT_PATH = ROOT / "game" / "content.json"
BACKUP_DIR = ROOT / "game" / "content_backups"
HTML_PATH = Path(__file__).with_name("content_editor.html")
# Кастомные звуки ручных действий (manual_actions.*.sound) - см. tools/web_ui.py:
# SOUNDS_DIR, одна и та же папка обслуживает и превью в редакторе, и саму игру.
SOUNDS_DIR = Path(__file__).with_name("sounds")
_ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SOUND_EXTENSIONS = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
_SOUND_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(wav|mp3|ogg)$")
# Разумный потолок для короткого звукового эффекта - защита от случайной
# загрузки огромного файла, а не полноценная квота хранилища.
_MAX_SOUND_BYTES = 5 * 1024 * 1024
# Тот же файл, что main.py/tools/web_ui.py используют как сейв игрока - редактор
# специально не импортирует game/state.py (см. CLAUDE.md про изоляцию tools/ от
# game/), поэтому "обнулить прогресс" здесь означает просто удалить файл: при
# следующем запуске main.py/web_ui.py сами создадут свежий GameState() - им уже
# известно как обращаться с сохранением, дублировать эту логику тут не нужно.
SAVE_PATH = ROOT / "save.json"
PORT = 8765


def load_content() -> dict:
    with open(CONTENT_PATH, "r", encoding="utf-8") as f:
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
        elif self.path == "/api/content":
            self._send_json(200, load_content())
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
            existed = SAVE_PATH.exists()
            if existed:
                SAVE_PATH.unlink()
            self._send_json(200, {"ok": True, "existed": existed})
            return

        if self.path == "/api/upload_sound":
            self._handle_upload_sound()
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

    def log_message(self, format: str, *args) -> None:
        pass  # тише в консоли - не засоряем вывод обычными запросами


def main() -> None:
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = HTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Не получилось занять порт {PORT} - возможно, редактор уже запущен в другом окне.")
        print(f"Если так, просто открой {url} в браузере.")
        return

    print(f"Редактор контента запущен: {url}")
    print("Открываю браузер... Останови сервер (Ctrl+C), когда закончишь редактировать.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
