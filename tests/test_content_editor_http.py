"""
Тесты HTTP-слоя редактора контента (tools/content_editor.py). CONTENT_PATH и
BACKUP_DIR подменяются на временную копию, так что реальный game/content.json
не трогается ни при успешном, ни при отклонённом сохранении.
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import content_editor as ce  # noqa: E402

PORT = 8798


class ContentEditorHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp_content = Path(cls._tmpdir.name) / "content.json"
        shutil.copy(ROOT / "game" / "content.json", tmp_content)

        cls._orig_content_path = ce.CONTENT_PATH
        cls._orig_backup_dir = ce.BACKUP_DIR
        cls._orig_save_path = ce.SAVE_PATH
        cls._orig_legacy_save_path = ce._LEGACY_SAVE_PATH
        cls._orig_sounds_dir = ce.SOUNDS_DIR
        ce.CONTENT_PATH = tmp_content
        ce.BACKUP_DIR = Path(cls._tmpdir.name) / "content_backups"
        ce.SAVE_PATH = Path(cls._tmpdir.name) / "save.json"
        # Тоже подменяем на tmp-путь - иначе /api/reset_save тронул бы РЕАЛЬНЫЙ
        # save.json в корне репозитория, если бы он там оказался (см. ce.py).
        ce._LEGACY_SAVE_PATH = Path(cls._tmpdir.name) / "legacy_save.json"
        ce.SOUNDS_DIR = Path(cls._tmpdir.name) / "sounds"

        cls.server = HTTPServer(("127.0.0.1", PORT), ce.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        ce.CONTENT_PATH = cls._orig_content_path
        ce.BACKUP_DIR = cls._orig_backup_dir
        ce.SAVE_PATH = cls._orig_save_path
        ce._LEGACY_SAVE_PATH = cls._orig_legacy_save_path
        ce.SOUNDS_DIR = cls._orig_sounds_dir
        cls._tmpdir.cleanup()

    def _get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_get_content_matches_temp_file(self):
        status, data = self._get("/api/content")
        self.assertEqual(status, 200)
        self.assertIn("resources", data)
        self.assertIn("techs", data)

    def test_valid_save_succeeds_and_creates_backup(self):
        status, data = self._get("/api/content")
        status, resp = self._post("/api/content", data)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertTrue(ce.BACKUP_DIR.exists())
        self.assertTrue(any(ce.BACKUP_DIR.iterdir()))

    def test_invalid_save_is_rejected_and_file_unchanged(self):
        status, data = self._get("/api/content")
        before_raw = ce.CONTENT_PATH.read_text(encoding="utf-8")

        broken = json.loads(json.dumps(data))
        any_building = next(iter(broken["buildings"]))
        broken["buildings"][any_building]["requires_tech"] = "totally_made_up"
        status, resp = self._post("/api/content", broken)

        self.assertEqual(status, 400)
        self.assertTrue(any("totally_made_up" in e for e in resp["errors"]))
        self.assertEqual(ce.CONTENT_PATH.read_text(encoding="utf-8"), before_raw)

    def test_malformed_json_returns_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/content",
            data=b"{not valid json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_unknown_path_returns_404(self):
        status, resp = self._get("/api/nope")
        self.assertEqual(status, 404)

    def test_mechanism_fx_js_is_served(self):
        # Превью «Оформление механизма» - тот же файл, что у игры (tools/mechanism_fx.js).
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/mechanism_fx.js", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"MECH_FX_PARTICLES", r.read())

    def test_hunt_fx_js_is_served(self):
        # Превью «Бой»/«Сцена охоты» - тот же файл, что у игры (tools/hunt_fx.js).
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/hunt_fx.js", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"HUNT_ATTACK_STYLES", r.read())

    def test_reset_save_deletes_existing_file(self):
        ce.SAVE_PATH.write_text("{}", encoding="utf-8")
        status, resp = self._post("/api/reset_save", {})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["existed"])
        self.assertFalse(ce.SAVE_PATH.exists())

    def test_reset_save_is_a_noop_when_nothing_to_delete(self):
        if ce.SAVE_PATH.exists():
            ce.SAVE_PATH.unlink()
        status, resp = self._post("/api/reset_save", {})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["existed"])

    def test_upload_sound_saves_file_and_returns_filename(self):
        import base64
        status, data = self._get("/api/content")
        any_action = next(iter(data["manual_actions"]))
        payload = {
            "action_id": any_action, "ext": "wav",
            "data_base64": base64.b64encode(b"RIFF....WAVEfmt ").decode("ascii"),
        }
        status, resp = self._post("/api/upload_sound", payload)
        self.assertEqual(status, 200)
        self.assertEqual(resp["filename"], f"{any_action}.wav")
        self.assertEqual((ce.SOUNDS_DIR / resp["filename"]).read_bytes(), b"RIFF....WAVEfmt ")

    def test_upload_sound_overwrites_on_repeated_upload(self):
        import base64
        status, data = self._get("/api/content")
        any_action = next(iter(data["manual_actions"]))
        first = {
            "action_id": any_action, "ext": "wav",
            "data_base64": base64.b64encode(b"first").decode("ascii"),
        }
        second = {
            "action_id": any_action, "ext": "wav",
            "data_base64": base64.b64encode(b"second-version").decode("ascii"),
        }
        self._post("/api/upload_sound", first)
        status, resp = self._post("/api/upload_sound", second)
        self.assertEqual(status, 200)
        self.assertEqual((ce.SOUNDS_DIR / resp["filename"]).read_bytes(), b"second-version")

    def test_upload_sound_rejects_unsafe_action_id(self):
        import base64
        payload = {
            "action_id": "../../evil", "ext": "wav",
            "data_base64": base64.b64encode(b"x").decode("ascii"),
        }
        status, resp = self._post("/api/upload_sound", payload)
        self.assertEqual(status, 400)
        self.assertFalse((ce.SOUNDS_DIR / "evil.wav").exists() if ce.SOUNDS_DIR.exists() else False)

    def test_upload_sound_rejects_disallowed_extension(self):
        import base64
        status, data = self._get("/api/content")
        any_action = next(iter(data["manual_actions"]))
        payload = {
            "action_id": any_action, "ext": "exe",
            "data_base64": base64.b64encode(b"x").decode("ascii"),
        }
        status, resp = self._post("/api/upload_sound", payload)
        self.assertEqual(status, 400)

    def test_upload_sound_rejects_oversized_file(self):
        import base64
        status, data = self._get("/api/content")
        any_action = next(iter(data["manual_actions"]))
        too_big = b"0" * (ce._MAX_SOUND_BYTES + 1)
        payload = {
            "action_id": any_action, "ext": "wav",
            "data_base64": base64.b64encode(too_big).decode("ascii"),
        }
        status, resp = self._post("/api/upload_sound", payload)
        self.assertEqual(status, 400)

    def test_sounds_route_serves_uploaded_file(self):
        import base64
        status, data = self._get("/api/content")
        any_action = next(iter(data["manual_actions"]))
        payload = {
            "action_id": any_action, "ext": "wav",
            "data_base64": base64.b64encode(b"preview-bytes").decode("ascii"),
        }
        self._post("/api/upload_sound", payload)
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/sounds/{any_action}.wav", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "audio/wav")
            self.assertEqual(r.read(), b"preview-bytes")

    def test_sounds_route_404_for_missing_file(self):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/sounds/does_not_exist.wav", timeout=5)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
