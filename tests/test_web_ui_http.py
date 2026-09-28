"""
Тесты HTTP-слоя веб-игры (tools/web_ui.py). Работают на тестовой фикстуре
(tests/fixtures.py) вместо реального game/content.json - переименование или
пересборка настоящего контента игры их не заденет. SAVE_PATH и STATE тоже
подменяются на время теста, так что реальный save.json не трогается.
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
import web_ui  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402

PORT = 8799


class SaveSandboxTests(unittest.TestCase):
    def test_tests_never_write_into_the_real_saves_folder(self):
        # Регрессия: web_ui.craft_grid_view -> run_turn -> save_state из тестов без
        # своей подмены путей перезаписывал настоящий saves/autosave.json игрока.
        self.assertNotEqual(web_ui.SAVES_DIR.resolve(), (ROOT / "saves").resolve())
        self.assertEqual(web_ui.SAVES_DIR.parent.resolve(), Path(fixtures._SAVE_SANDBOX).resolve())


class WebUiHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._orig_save_path = web_ui.SAVE_PATH
        cls._orig_saves_dir = web_ui.SAVES_DIR
        cls._orig_sounds_dir = web_ui.SOUNDS_DIR
        web_ui.SAVE_PATH = Path(cls._tmpdir.name) / "save.json"
        web_ui.SAVES_DIR = Path(cls._tmpdir.name) / "saves"
        web_ui.SOUNDS_DIR = Path(cls._tmpdir.name) / "sounds"

        cls._content_patch = fixtures.patched_content()
        cls._content_patch.__enter__()

        web_ui.STATE = GameState()
        cls.server = HTTPServer(("127.0.0.1", PORT), web_ui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls._content_patch.__exit__(None, None, None)
        web_ui.SAVE_PATH = cls._orig_save_path
        web_ui.SAVES_DIR = cls._orig_saves_dir
        web_ui.SOUNDS_DIR = cls._orig_sounds_dir
        cls._tmpdir.cleanup()

    def setUp(self):
        # свежее состояние перед каждым тестом, чтобы они не влияли друг на друга
        web_ui.STATE = GameState()
        if web_ui.SAVES_DIR.exists():
            shutil.rmtree(web_ui.SAVES_DIR)

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

    def test_get_state_shape(self):
        status, state = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["turn"], 1)
        self.assertIn("manual", state["categories"])
        self.assertIn("icons", state)
        self.assertEqual(len(state["quests"]), len(fixtures.QUESTS))
        # test_quest (первый в fixtures.QUESTS) не требует других квестов (requires=[])
        # - доступен сразу на свежей игре, но ещё не выполнен.
        self.assertEqual(state["quests"][0]["status"], "available")
        self.assertEqual(state["quests"][0]["era"], 1)
        self.assertEqual(
            {l["id"] for l in state["locations"]},
            {"yard", "shaft", "workshop_loc", "mechanisms", "sub_workshop", "converter", "hunting_loc", "grid_loc"},
        )
        self.assertIn("gather_a", [m["id"] for m in state["categories"]["manual"]])
        self.assertEqual(
            next(m for m in state["categories"]["manual"] if m["id"] == "gather_a")["location"], "yard",
        )

    def test_claim_quest_reward_round_trip(self):
        # Условие test_quest (see fixtures.QUESTS/QUESTS[test_quest]) - хотя бы
        # одна постройка любого типа.
        web_ui.STATE.buildings["gen"] = 1
        status, action_payload = self._post("/api/action", {"type": "skip", "id": None})
        self.assertEqual(status, 200)
        self.assertEqual([q["id"] for q in action_payload["log"]["quests_completed"]], ["test_quest"])
        self.assertEqual(action_payload["state"]["quests"][0]["status"], "completed")

        # Кейс - барабан с одним призом на бросок (см. fixtures.LOOT_CASES["test_case"],
        # game/engine.py:_roll_loot_case_prize) - фиксируем random.uniform, чтобы
        # результат (raw_a x5, вес 3 из суммы 4) был детерминирован.
        with mock.patch.object(engine.random, "uniform", return_value=2.5):
            status, claim_payload = self._post("/api/claim_quest", {"quest_id": "test_quest"})
        self.assertEqual(status, 200)
        self.assertTrue(claim_payload["ok"])
        self.assertEqual(claim_payload["prize"], {"resource": "raw_a", "amount": 5})
        self.assertEqual(claim_payload["state"]["quests"][0]["status"], "claimed")
        self.assertEqual(claim_payload["state"]["resources"].get("raw_a"), 5)

        status, second_claim = self._post("/api/claim_quest", {"quest_id": "test_quest"})
        self.assertEqual(status, 200)
        self.assertFalse(second_claim["ok"])

    def test_nested_location_parent_and_root_level_action_are_exposed(self):
        status, state = self._get("/api/state")
        sub = next(l for l in state["locations"] if l["id"] == "sub_workshop")
        self.assertEqual(sub["parent_location"], "workshop_loc")
        yard = next(l for l in state["locations"] if l["id"] == "yard")
        self.assertIsNone(yard["parent_location"])
        loose = next(m for m in state["categories"]["manual"] if m["id"] == "loose_action")
        self.assertIsNone(loose["location"])
        self.assertIn("time", state)
        self.assertEqual(state["time"], {"day": 1, "hour": 0, "is_day": False, "hours_per_turn": 3})
        self.assertEqual(len(state["tech_tree"]), len(fixtures.TECHS))

    def test_tools_view_exposes_every_tool_with_upgrade_availability(self):
        status, state = self._get("/api/state")
        self.assertIn("tools", state)
        tool_ids = {t["id"] for t in state["tools"]}
        self.assertEqual(tool_ids, {"pickaxe", "drill", "wrench", "sword", "armor", "ore_scanner"})
        pickaxe = next(t for t in state["tools"] if t["id"] == "pickaxe")
        self.assertEqual(pickaxe["tier"], 1)
        self.assertEqual(pickaxe["max_tier"], len(fixtures.TOOLS["pickaxe"]["tiers"]))
        self.assertIsNotNone(pickaxe["next_tier_name"])  # тир 2 не требует технологии
        self.assertTrue(pickaxe["can_upgrade"])
        self.assertIsNone(pickaxe["next_tech_name"])

    def test_tools_view_shows_tech_locked_next_tier_without_hiding_it(self):
        # тир 3 кирки в фикстуре требует tech1 - до его изучения апгрейд нельзя
        # нажать, но название/цену следующего тира и недостающую технологию всё
        # равно показываем (не то же самое, что "инструмент уже максимального тира").
        web_ui.STATE.tool_tiers["pickaxe"] = 2
        status, state = self._get("/api/state")
        pickaxe = next(t for t in state["tools"] if t["id"] == "pickaxe")
        self.assertFalse(pickaxe["can_upgrade"])
        self.assertEqual(pickaxe["next_tier_name"], "Тир 3")
        self.assertEqual(pickaxe["next_tech_name"], fixtures.TECHS["tech1"]["name"])

    def test_upgrade_tool_action_advances_turn_and_raises_tier(self):
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/action", {"type": "upgrade_tool", "id": "pickaxe"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"])
        self.assertEqual(resp["state"]["turn"], 2)
        pickaxe = next(t for t in resp["state"]["tools"] if t["id"] == "pickaxe")
        self.assertEqual(pickaxe["tier"], 2)

    def test_upgrade_tool_fails_without_resources_and_does_not_advance_turn(self):
        status, resp = self._post("/api/action", {"type": "upgrade_tool", "id": "pickaxe"})
        self.assertFalse(resp["log"]["ok"])
        self.assertEqual(resp["state"]["turn"], 1)

    def test_storage_view_exposes_every_resource_with_capacity_where_configured(self):
        status, state = self._get("/api/state")
        self.assertIn("storage", state)
        by_id = {item["id"]: item for item in state["storage"]}
        # energy не участвует в этой панели - у неё своя отдельная механика ёмкости.
        self.assertNotIn("energy", by_id)
        # ресурс без storage вообще - хранится без ограничений.
        self.assertIsNone(by_id["raw_a"]["capacity"])
        self.assertIsNone(by_id["raw_a"]["next_cost"])
        limited = by_id["limited_res"]
        self.assertEqual(limited["capacity"], 10)
        self.assertEqual(limited["tier"], 0)
        self.assertEqual(limited["max_tier"], 2)
        self.assertEqual(limited["next_capacity_add"], 20)
        self.assertTrue(limited["can_upgrade"])
        self.assertIsNone(limited["next_tech_name"])

    def test_storage_view_shows_tech_locked_next_tier_without_hiding_it(self):
        web_ui.STATE.storage_tier["limited_res"] = 1  # тир 2 требует tech1
        status, state = self._get("/api/state")
        limited = next(item for item in state["storage"] if item["id"] == "limited_res")
        self.assertFalse(limited["can_upgrade"])
        self.assertEqual(limited["next_capacity_add"], 50)
        self.assertEqual(limited["next_tech_name"], fixtures.TECHS["tech1"]["name"])

    def test_storage_view_exposes_next_infinite_tier_and_purchase_removes_cap(self):
        # fixtures.RESOURCE_STORAGE["vault_res"]: тир 1 обычный (+20), тир 2 -
        # infinite: True (game/engine.py:storage_capacity) - вместо next_capacity_add
        # панель получает next_infinite: True, а после покупки capacity == None,
        # как у ресурса вовсе без storage.
        web_ui.STATE.storage_tier["vault_res"] = 1
        web_ui.STATE.resources["raw_a"] = 100
        status, state = self._get("/api/state")
        vault = next(item for item in state["storage"] if item["id"] == "vault_res")
        self.assertTrue(vault["next_infinite"])
        self.assertIsNone(vault["next_capacity_add"])
        status, resp = self._post("/api/action", {"type": "storage_upgrade", "id": "vault_res"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertIn("без ограничений", resp["log"]["message"])
        vault = next(item for item in resp["state"]["storage"] if item["id"] == "vault_res")
        self.assertIsNone(vault["capacity"])
        self.assertFalse(vault["next_infinite"])
        self.assertIsNone(vault["next_capacity_add"])

    def test_storage_upgrade_action_advances_turn_and_raises_capacity(self):
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/action", {"type": "storage_upgrade", "id": "limited_res"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(resp["state"]["turn"], 2)
        limited = next(item for item in resp["state"]["storage"] if item["id"] == "limited_res")
        self.assertEqual(limited["tier"], 1)
        self.assertEqual(limited["capacity"], 30)

    def test_storage_upgrade_fails_without_resources_and_does_not_advance_turn(self):
        status, resp = self._post("/api/action", {"type": "storage_upgrade", "id": "limited_res"})
        self.assertFalse(resp["log"]["ok"])
        self.assertEqual(resp["state"]["turn"], 1)

    def test_default_action_delay_ms_is_exposed_for_the_click_cooldown_ui(self):
        # чисто клиентская защита от автокликера (tools/web_ui.html) - сервер лишь
        # публикует значение по умолчанию, ничего не хранит и не считает сам.
        status, state = self._get("/api/state")
        self.assertIn("default_action_delay_ms", state)
        self.assertIsInstance(state["default_action_delay_ms"], (int, float))
        self.assertGreaterEqual(state["default_action_delay_ms"], 0)

    def test_manual_items_expose_click_delay_field_and_there_is_no_build_catalog(self):
        # Каталога «Построить» больше нет: постройки собираются в механизмах
        # (buildings.*.location), их задержка - у рецепта в crafting_view.
        status, state = self._get("/api/state")
        self.assertNotIn("build", state["categories"])
        gather_a = next(m for m in state["categories"]["manual"] if m["id"] == "gather_a")
        self.assertIn("click_delay_ms", gather_a)

    def test_manual_action_exposes_sound_url(self):
        status, state = self._get("/api/state")
        gather_a = next(m for m in state["categories"]["manual"] if m["id"] == "gather_a")
        self.assertIsNone(gather_a["sound_url"])  # у fixtures gather_a нет sound

        web_ui.MANUAL_ACTIONS["gather_a"]["sound"] = "gather_a.wav"
        try:
            status, state = self._get("/api/state")
            gather_a = next(m for m in state["categories"]["manual"] if m["id"] == "gather_a")
            self.assertEqual(gather_a["sound_url"], "/sounds/gather_a.wav")
        finally:
            del web_ui.MANUAL_ACTIONS["gather_a"]["sound"]

    def test_action_response_includes_resolved_sound_url_for_manual_actions(self):
        web_ui.MANUAL_ACTIONS["gather_a"]["sound"] = "gather_a.wav"
        try:
            status, resp = self._post("/api/action", {"type": "manual", "id": "gather_a"})
            self.assertEqual(status, 200)
            self.assertEqual(resp["log"]["sound_url"], "/sounds/gather_a.wav")
        finally:
            del web_ui.MANUAL_ACTIONS["gather_a"]["sound"]

    def test_action_response_sound_url_is_none_without_custom_sound(self):
        status, resp = self._post("/api/action", {"type": "manual", "id": "gather_a"})
        self.assertEqual(status, 200)
        self.assertIsNone(resp["log"]["sound_url"])

    def test_non_manual_action_response_has_no_sound_url(self):
        status, resp = self._post("/api/action", {"type": "skip", "id": None})
        self.assertEqual(status, 200)
        self.assertIsNone(resp["log"]["sound_url"])

    def test_ritual_recipe_exposes_sound_url(self):
        web_ui.STATE.buildings["ritual_altar"] = 1
        status, state = self._get("/api/state")
        altar = next(a for a in state["ritual_altars"] if a["building_id"] == "ritual_altar")
        recipe = next(r for r in altar["recipes"] if r["id"] == "ritual_basic")
        self.assertIsNone(recipe["sound_url"])  # у fixtures ritual_basic нет sound

        web_ui.RITUAL_RECIPES["ritual_basic"]["sound"] = "ritual_basic.wav"
        try:
            status, state = self._get("/api/state")
            altar = next(a for a in state["ritual_altars"] if a["building_id"] == "ritual_altar")
            recipe = next(r for r in altar["recipes"] if r["id"] == "ritual_basic")
            self.assertEqual(recipe["sound_url"], "/sounds/ritual_basic.wav")
        finally:
            del web_ui.RITUAL_RECIPES["ritual_basic"]["sound"]

    def test_action_response_includes_resolved_sound_url_for_ritual(self):
        web_ui.STATE.buildings["ritual_altar"] = 1
        web_ui.STATE.resources["raw_a"] = 2
        web_ui.STATE.resources["raw_b"] = 1
        web_ui.RITUAL_RECIPES["ritual_basic"]["sound"] = "ritual_basic.wav"
        try:
            status, resp = self._post("/api/action", {"type": "start_ritual", "id": "ritual_basic"})
            self.assertEqual(status, 200)
            self.assertTrue(resp["log"]["ok"])
            self.assertEqual(resp["log"]["sound_url"], "/sounds/ritual_basic.wav")
        finally:
            del web_ui.RITUAL_RECIPES["ritual_basic"]["sound"]

    def test_sounds_route_serves_uploaded_file_with_content_type(self):
        sounds_dir = Path(web_ui.SOUNDS_DIR)
        sounds_dir.mkdir(parents=True, exist_ok=True)
        f = sounds_dir / "gather_a.wav"
        f.write_bytes(b"RIFF....WAVEfmt ")
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/sounds/gather_a.wav")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.headers["Content-Type"], "audio/wav")
                self.assertEqual(r.read(), b"RIFF....WAVEfmt ")
        finally:
            f.unlink()

    def test_sounds_route_404_for_missing_file(self):
        status, _ = self._get_raw("/sounds/does_not_exist.wav")
        self.assertEqual(status, 404)

    def test_sounds_route_rejects_path_traversal(self):
        status, _ = self._get_raw("/sounds/..%2F..%2Fweb_ui.py")
        self.assertEqual(status, 404)

    def _get_raw(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_manual_action_cost_jump_to_requires_recipe_board(self):
        status, state = self._get("/api/state")
        craft_final = next(m for m in state["categories"]["manual"] if m["id"] == "craft_final")
        cost_by_id = {c["id"]: c for c in craft_final["cost"]}
        # без recipe_board ингредиенты не кликабельны, даже если у них есть рецепт
        self.assertIsNone(cost_by_id["mid"]["jump_to"])
        self.assertIsNone(cost_by_id["raw_b"]["jump_to"])

        web_ui.STATE.buildings["recipe_board"] = 1
        status, state = self._get("/api/state")
        craft_final = next(m for m in state["categories"]["manual"] if m["id"] == "craft_final")
        cost_by_id = {c["id"]: c for c in craft_final["cost"]}
        # mid делается через craft_mid - теперь кликабелен
        self.assertEqual(cost_by_id["mid"]["jump_to"], "craft_mid")
        # raw_b - голое сырьё (gather_b с пустой ценой) - тоже кликабельно: в
        # отличие от engine.resource_recipe (нужен только автокрафту для
        # рекурсивного докрафта, там сырью подставлять нечего), навигации
        # достаточно того, что gather_b вообще производит raw_b.
        self.assertEqual(cost_by_id["raw_b"]["jump_to"], "gather_b")

    def test_manual_action_exposes_chance_only_when_below_100_percent(self):
        # "Шанс потребления/получения" (см. CLAUDE.md) - tools/web_ui.py кладёт поле
        # "chance" в запись ресурса, ТОЛЬКО когда он < 1 (100%), чтобы не засорять
        # ответ для подавляющего большинства действий без этой механики.
        status, state = self._get("/api/state")
        craft_final = next(m for m in state["categories"]["manual"] if m["id"] == "craft_final")
        cost_by_id = {c["id"]: c for c in craft_final["cost"]}
        self.assertNotIn("chance", cost_by_id["mid"])  # обычный, гарантированный ресурс

        craft_lucky = next(m for m in state["categories"]["manual"] if m["id"] == "craft_lucky")
        gain_by_id = {g["id"]: g for g in craft_lucky["gain"]}
        self.assertAlmostEqual(gain_by_id["lucky_output"]["chance"], 0.5)
        cost_by_id = {c["id"]: c for c in craft_lucky["cost"]}
        self.assertNotIn("chance", cost_by_id["raw_a"])  # у craft_lucky шанс только на gain

        craft_risky = next(m for m in state["categories"]["manual"] if m["id"] == "craft_risky")
        cost_by_id = {c["id"]: c for c in craft_risky["cost"]}
        self.assertAlmostEqual(cost_by_id["raw_a"]["chance"], 0.1)
        gain_by_id = {g["id"]: g for g in craft_risky["gain"]}
        self.assertNotIn("chance", gain_by_id["risky_output"])  # у craft_risky шанс только на cost

    def test_owned_buildings_expose_description(self):
        web_ui.STATE.resources["raw_b"] = 5
        self._post("/api/action", {"type": "build", "id": "gen"})
        status, state = self._get("/api/state")
        gen_owned = next(b for b in state["buildings"] if b["id"] == "gen")
        self.assertEqual(gen_owned["description"], fixtures.BUILDINGS["gen"]["description"])

    def test_building_without_upgrades_exposes_null_upgrades_field(self):
        web_ui.STATE.resources["raw_b"] = 5
        self._post("/api/action", {"type": "build", "id": "gen"})
        status, state = self._get("/api/state")
        gen_owned = next(b for b in state["buildings"] if b["id"] == "gen")
        self.assertIsNone(gen_owned["upgrades"])

    def test_repair_is_no_longer_a_separate_category_and_is_driven_from_the_buildings_panel(self):
        # "gen" ломается без типа (breakdown_types не задан, breakdown_chance=0.0 -
        # ломаем вручную через state, чтобы не зависеть от advance_turn, который
        # repair() всё равно вызывает следом сам) - чинится, как раньше, без
        # всякого инструмента, только теперь кнопка живёт на самой карточке
        # постройки (state["buildings"]), а не в отдельной категории "repair".
        web_ui.STATE.resources["raw_a"] = 100
        web_ui.STATE.resources["raw_b"] = 100
        self._post("/api/action", {"type": "build", "id": "gen"})
        web_ui.STATE.broken["gen"] = 1
        status, state = self._get("/api/state")
        self.assertNotIn("repair", state["categories"])
        gen = next(b for b in state["buildings"] if b["id"] == "gen")
        self.assertEqual(gen["broken"], 1)
        self.assertEqual(gen["repairable"], 1)
        self.assertEqual(gen["repair_blocked"], [])
        self.assertTrue(gen["repair_cost"])

        status, resp = self._post("/api/action", {"type": "repair", "id": "gen"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        status, state = self._get("/api/state")
        gen = next(b for b in state["buildings"] if b["id"] == "gen")
        self.assertEqual(gen["broken"], 0)

    def test_repair_reports_blocked_types_and_repair_tool_fixes_the_tool(self):
        # flaky_typed ломается ВСЕГДА с типом "worn_gear" (требует TOOLS["wrench"]
        # тира 2+, см. tests/fixtures.py) - без ключа ремонт постройки блокирован,
        # и это должно быть видно прямо в state["buildings"] (repairable=0,
        # repair_blocked непусто), не только в провале самого действия repair.
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "flaky_typed"})
        self._post("/api/action", {"type": "skip", "id": None})
        status, state = self._get("/api/state")
        flaky = next(b for b in state["buildings"] if b["id"] == "flaky_typed")
        self.assertEqual(flaky["broken"], 1)
        self.assertEqual(flaky["repairable"], 0)
        self.assertEqual(len(flaky["repair_blocked"]), 1)
        self.assertIn("Гаечный ключ", flaky["repair_blocked"][0]["missing_tools"])

        status, resp = self._post("/api/action", {"type": "repair", "id": "flaky_typed"})
        self.assertFalse(resp["log"]["ok"])

        # Покупаем ключ (тир 2) и чиним - теперь получается, а прочность ключа тратится.
        status, resp = self._post("/api/action", {"type": "upgrade_tool", "id": "wrench"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        status, resp = self._post("/api/action", {"type": "repair", "id": "flaky_typed"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        status, state = self._get("/api/state")
        wrench = next(t for t in state["tools"] if t["id"] == "wrench")
        self.assertEqual(wrench["durability_remaining"], 1)  # max_durability=2, минус эту починку
        self.assertFalse(wrench["is_broken"])

        # Вторая починка исчерпывает прочность ключа - repair_tool восстанавливает её.
        self._post("/api/action", {"type": "skip", "id": None})
        self._post("/api/action", {"type": "repair", "id": "flaky_typed"})
        status, state = self._get("/api/state")
        wrench = next(t for t in state["tools"] if t["id"] == "wrench")
        self.assertTrue(wrench["is_broken"])
        self.assertEqual(wrench["durability_remaining"], 0)

        status, resp = self._post("/api/action", {"type": "repair_tool", "id": "wrench"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        status, state = self._get("/api/state")
        wrench = next(t for t in state["tools"] if t["id"] == "wrench")
        self.assertFalse(wrench["is_broken"])
        self.assertEqual(wrench["durability_remaining"], 2)

    def test_mechanism_upgrade_preview_shows_before_and_after_for_dependent_action(self):
        # mechanism_a сам ничего не производит - весь эффект его улучшения виден
        # только через зависимое ручное действие mechanism_a_action (requires_building).
        # "current"/"preview" теперь несут ОДИН показательный example_action (не список
        # всех зависимых действий - см. CLAUDE.md, "их может быть несколько десятков"),
        # а полный список имён зависимых действий - отдельно, в dependent_action_names.
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "mechanism_a"})
        status, state = self._get("/api/state")
        mech = next(b for b in state["buildings"] if b["id"] == "mechanism_a")
        self.assertIsNotNone(mech["upgrades"])
        self.assertEqual(
            mech["upgrades"]["dependent_action_names"],
            ["Провернуть механизм А", "Долгий прогон механизма А"],
        )
        current_action = mech["upgrades"]["current"]["example_action"]
        self.assertEqual(current_action["id"], "mechanism_a_action")
        self.assertEqual({c["id"]: c["amount"] for c in current_action["cost"]}, {"raw_a": 2})
        self.assertEqual({c["id"]: c["amount"] for c in current_action["gain"]}, {"mech_output": 3})

        scale_upgrade = next(u for u in mech["upgrades"]["upgrades"] if u["id"] == "mech_a_scale")
        self.assertFalse(scale_upgrade["purchased"])
        preview_action = scale_upgrade["preview"]["example_action"]
        self.assertEqual({c["id"]: c["amount"] for c in preview_action["cost"]}, {"raw_a": 4})
        self.assertEqual({c["id"]: c["amount"] for c in preview_action["gain"]}, {"mech_output": 6})

        status, resp = self._post("/api/action", {"type": "building_upgrade", "id": "mechanism_a:mech_a_scale"})
        self.assertTrue(resp["log"]["ok"])
        status, state = self._get("/api/state")
        mech = next(b for b in state["buildings"] if b["id"] == "mechanism_a")
        current_action = mech["upgrades"]["current"]["example_action"]
        self.assertEqual({c["id"]: c["amount"] for c in current_action["gain"]}, {"mech_output": 6})

    def test_manual_action_scale_slider_exposes_cap_and_applies_chosen_scale(self):
        # "Ползунок масштаба ручного действия" (см. CLAUDE.md) - categories.manual
        # теперь отдаёт cost/gain ЗА ОДНУ партию (unit, scale=1) плюс scale_cap,
        # а не сразу за купленный максимум; POST /api/action со scale=1 обязан
        # применить именно базовый расход/выход, даже когда x2 уже куплен.
        web_ui.STATE.buildings["mechanism_a"] = 1
        web_ui.STATE.resources["raw_a"] = 100

        status, state = self._get("/api/state")
        action = next(a for a in state["categories"]["manual"] if a["id"] == "mechanism_a_action")
        self.assertEqual(action["scale_cap"], 1)
        self.assertEqual({c["id"]: c["amount"] for c in action["cost"]}, {"raw_a": 2})
        self.assertEqual({c["id"]: c["amount"] for c in action["gain"]}, {"mech_output": 3})

        web_ui.engine.purchase_building_upgrade(web_ui.STATE, "mechanism_a", "mech_a_scale")
        status, state = self._get("/api/state")
        action = next(a for a in state["categories"]["manual"] if a["id"] == "mechanism_a_action")
        self.assertEqual(action["scale_cap"], 2)
        # cost/gain остаются unit (x1) даже после покупки x2 - масштаб выбирает игрок.
        self.assertEqual({c["id"]: c["amount"] for c in action["cost"]}, {"raw_a": 2})
        self.assertEqual({c["id"]: c["amount"] for c in action["gain"]}, {"mech_output": 3})

        status, resp = self._post("/api/action", {"type": "manual", "id": "mechanism_a_action", "scale": 1})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(resp["state"]["resources"]["mech_output"], 3)  # x1, не x2

    def test_autocraft_preview_returns_no_confirmation_when_stock_is_sufficient(self):
        # Весь cost уже покрыт наличным запасом - докрафт не нужен вообще,
        # подтверждение показывать не нужно (см. CLAUDE.md).
        web_ui.STATE.resources.update({"mid": 2, "raw_b": 1})
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "craft_final"})
        self.assertEqual(status, 200)
        self.assertFalse(resp["needs_confirm"])

    def test_autocraft_preview_returns_no_confirmation_without_a_processor(self):
        # Недостача есть, но автокрафт ещё не "появился" (нет процессора) -
        # действие просто провалится как обычно, предпросмотр ни к чему.
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "craft_final"})
        self.assertEqual(status, 200)
        self.assertFalse(resp["needs_confirm"])

    def test_autocraft_preview_builds_tree_and_summary_for_a_real_shortfall(self):
        # craft_final: mid:2, raw_b:1 - ничего в наличии. mid делается из raw_a
        # (craft_mid). Сводка обязана включать и промежуточный mid, и лист raw_a,
        # не только верхний cost - с числом операций (fires) на каждом узле.
        web_ui.STATE.buildings["automation_hub"] = 1
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "craft_final"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["needs_confirm"])
        self.assertEqual(resp["label"], "Собрать изделие")  # имя триггерящего действия, для заголовка модалки
        summary_by_id = {row["resource_id"]: row for row in resp["summary"]}
        self.assertEqual(summary_by_id["mid"]["taken"], 0)  # своего mid вообще нет - всё докрафчено
        self.assertEqual(summary_by_id["mid"]["fires"], 2)
        self.assertEqual(summary_by_id["raw_a"]["needed"], 8)  # 2 mid x 4 raw_a за партию
        self.assertEqual(summary_by_id["raw_a"]["taken"], 0)  # запаса raw_a тоже 0
        self.assertIsNone(summary_by_id["raw_a"]["percent"])  # своего запаса 0 - нечего делить
        self.assertEqual(summary_by_id["raw_b"]["needed"], 1)
        self.assertEqual(summary_by_id["raw_b"]["taken"], 0)

        mid_node = next(n for n in resp["tree"] if n["resource_id"] == "mid")
        self.assertEqual(mid_node["fires"], 2)
        self.assertEqual(mid_node["action_name"], "Сделать полуфабрикат")
        self.assertEqual(mid_node["children"][0]["resource_id"], "raw_a")
        # сырья raw_a нет - заказ не оформится, окно обязано сказать почему
        self.assertIn("Сырьё A", resp["reason"])
        # сырьё - не «докрафтится», а «не хватает»
        self.assertEqual((summary_by_id["mid"]["missing"], summary_by_id["raw_a"]["missing"]), (0, 8))
        self.assertEqual(mid_node["children"][0]["missing"], 8)

    def test_autocraft_preview_skips_the_window_when_nothing_can_be_crafted(self):
        # Регрессия: рецепт mid не автоматизирован (у bare_processor только мощность) -
        # окно писало «докрафтится 2», хотя автокрафт его не сделает. Докрафтить нечего -
        # окна нет, клик сразу говорит, чего не хватает.
        web_ui.STATE.buildings["bare_processor"] = 1
        web_ui.STATE.resources["raw_b"] = 1
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "craft_final"})
        self.assertEqual(status, 200)
        self.assertFalse(resp["needs_confirm"])
        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_final"})
        self.assertEqual(resp["log"]["message"], "Не хватает ресурсов на «Собрать изделие». Не хватает: Полуфабрикат 2.")

    def test_autocraft_preview_has_no_reason_when_the_order_will_go_through(self):
        web_ui.STATE.buildings["automation_hub"] = 1
        web_ui.STATE.resources.update({"raw_a": 100, "raw_b": 1})
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "craft_final"})
        self.assertTrue(resp["needs_confirm"])
        self.assertIsNone(resp["reason"])

    def test_autocraft_preview_reports_turns_when_docraft_spans_several_turns(self):
        # trickle_hub - сборщик мастерской на 2 крафта за ход. assemble_gadget просит
        # slow_mid:10 - craft_slow_mid делает по 1 за прокрутку, 10 прокруток не
        # влезают в один ход, значит растянется на ceil(10/2)=5 ходов - и summary,
        # и узел дерева обязаны явно показать эту оценку.
        web_ui.STATE.resources["slow_raw"] = 1000
        web_ui.STATE.buildings["trickle_hub"] = 1
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "assemble_gadget"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["needs_confirm"])
        summary_row = next(row for row in resp["summary"] if row["resource_id"] == "slow_mid")
        self.assertEqual(summary_row["turns"], 5)
        tree_node = next(n for n in resp["tree"] if n["resource_id"] == "slow_mid")
        self.assertEqual(tree_node["turns"], 5)

    def test_scale_upgrade_tiers_require_sequential_purchase_and_collapse_when_bought(self):
        # upgradable_factory: uf_scale (x2) идёт ПЕРЕД uf_scale_more (x3) в её
        # upgrades - до покупки uf_scale, uf_scale_more вообще НЕ должен
        # появляться в списке "upgrades" (по прямому пожеланию: раньше
        # показывался задизейбленным с подсказкой "сначала купи предыдущее",
        # теперь просто не занимает место в списке); после покупки ОБОИХ по
        # порядку - остаётся только более сильный (uf_scale_more), а uf_scale
        # (перекрытый) из списка убран (см. CLAUDE.md).
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "upgradable_factory"})

        status, state = self._get("/api/state")
        factory = next(b for b in state["buildings"] if b["id"] == "upgradable_factory")
        ids_before = [u["id"] for u in factory["upgrades"]["upgrades"]]
        self.assertNotIn("uf_scale_more", ids_before)  # ещё не на очереди - не показывается
        self.assertIn("uf_scale", ids_before)  # первый в прогрессии - доступен сразу

        status, resp = self._post("/api/action", {"type": "building_upgrade", "id": "upgradable_factory:uf_scale_more"})
        self.assertFalse(resp["log"]["ok"])  # сервер тоже отказывает, не только подсказка в UI

        self._post("/api/action", {"type": "building_upgrade", "id": "upgradable_factory:uf_scale"})
        status, resp = self._post("/api/action", {"type": "building_upgrade", "id": "upgradable_factory:uf_scale_more"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

        status, state = self._get("/api/state")
        factory = next(b for b in state["buildings"] if b["id"] == "upgradable_factory")
        ids = [u["id"] for u in factory["upgrades"]["upgrades"]]
        self.assertNotIn("uf_scale", ids)  # перекрытый более слабый уровень убран из списка
        self.assertIn("uf_scale_more", ids)

    def test_mining_new_field_and_hit_cell_over_http(self):
        # Поле шахты - отдельные эндпоинты (как insert_soul/house_creature), НЕ
        # общий /api/action - клики по клеткам не должны тратить ход.
        status, resp = self._post("/api/mining_new_field", {})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        turn_before = resp["state"]["turn"]
        mine_field = resp["state"]["mine_field"]
        size = mine_field["size"]
        center = size // 2
        center_cell = mine_field["cells"][center * size + center]
        self.assertTrue(center_cell["broken"])

        # клетка, ортогонально граничащая с уже открытым центром - достижима сразу
        status, resp = self._post("/api/mining_hit_cell", {"r": center, "c": center + 1})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["state"]["turn"], turn_before)

    def test_mining_vein_icon_is_independent_from_resource_icon(self):
        # mining_info.*.icon - как выглядит ЖИЛА на поле, отдельно от icons.resources
        # того же id (как выглядит уже добытый ресурс), по прямому пожеланию,
        # 2026-09-16. Без своей иконки (rare_mineral) в icons.mining_veins записи
        # вообще нет - клиент сам откатывается на resourceIcon (veinIcon() в
        # web_ui.html), проверять тут нечего сверх самого отсутствия ключа.
        vein_icon = {"grid": ["A"], "palette": {"A": "#123456"}}
        patched = {**fixtures.MINING_INFO["mineral"], "icon": vein_icon}
        with mock.patch.dict(web_ui.MINING_INFO, {"mineral": patched}):
            status, state = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["icons"]["mining_veins"]["mineral"], vein_icon)
        self.assertNotIn("rare_mineral", state["icons"]["mining_veins"])

    def test_converter_category_lists_recipes_and_converter_location_flag(self):
        status, state = self._get("/api/state")
        # "mechanisms" - обычная локация-контейнер без спец-поведения; спец-флаг и
        # список рецептов теперь на её дочерней локации "converter" (см. CLAUDE.md).
        mechanisms_loc = next(l for l in state["locations"] if l["id"] == "mechanisms")
        self.assertFalse(mechanisms_loc["is_converter"])
        converter_loc = next(l for l in state["locations"] if l["id"] == "converter")
        self.assertTrue(converter_loc["is_converter"])
        self.assertEqual(converter_loc["parent_location"], "mechanisms")
        recipe_ids = {r["id"] for r in state["categories"]["converter"]}
        self.assertEqual(recipe_ids, {"make_raw_b", "make_catalyst", "upgrade_matter", "make_variable"})
        # без единого построенного преобразователя запустить рецепт нельзя
        self.assertTrue(all(not r["available"] for r in state["categories"]["converter"]))
        self.assertEqual(state["energy_converter_capacity"], 0)
        self.assertEqual(state["energy_jobs"], [])

    def test_start_energy_recipe_action_and_progress_in_energy_jobs_view(self):
        web_ui.STATE.resources["raw_a"] = 5
        status, resp = self._post("/api/action", {"type": "build", "id": "energy_converter"})
        self.assertTrue(resp["log"]["ok"])

        status, resp = self._post("/api/action", {"type": "start_energy_recipe", "id": "make_raw_b"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"])
        jobs = resp["state"]["energy_jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["recipe_id"], "make_raw_b")

        # преобразователь занят - второй рецепт сейчас не запустить
        status, resp = self._post("/api/action", {"type": "start_energy_recipe", "id": "make_catalyst"})
        self.assertFalse(resp["log"]["ok"])

    def test_cancel_energy_job_does_not_advance_the_turn(self):
        web_ui.STATE.resources["raw_a"] = 5
        self._post("/api/action", {"type": "build", "id": "energy_converter"})
        self._post("/api/action", {"type": "start_energy_recipe", "id": "make_raw_b"})
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/cancel_job", {"kind": "energy", "index": 0})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(resp["state"]["energy_jobs"], [])
        self.assertEqual(web_ui.STATE.turn, turn_before)  # отмена не тратит ход

        # преобразователь снова свободен
        status, resp = self._post("/api/action", {"type": "start_energy_recipe", "id": "make_catalyst"})
        self.assertTrue(resp["log"]["ok"])

    def test_order_preview_explains_why_a_recipe_cannot_be_ordered(self):
        status, preview = self._post("/api/order_preview", {"action_id": "craft_slow_mid", "qty": 1})
        self.assertEqual(status, 200)
        self.assertFalse(preview["can_order"])
        self.assertIn("Мастерская", preview["reason"])

    def test_cancel_job_with_unknown_kind_returns_400(self):
        status, resp = self._post("/api/cancel_job", {"kind": "nonsense"})
        self.assertEqual(status, 400)

    def test_manual_action_card_exposes_running_progress_and_blocks_second_start(self):
        web_ui.STATE.resources["raw_a"] = 10
        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_delayed"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

        craft_delayed = next(m for m in resp["state"]["categories"]["manual"] if m["id"] == "craft_delayed")
        self.assertTrue(craft_delayed["already_running"])
        # /api/action уже провёл advance_turn ВНУТРИ этого же запроса (см. web_ui.py:1180) -
        # к моменту ответа job успел тикнуть один раз, поэтому 1, не 0.
        self.assertEqual(craft_delayed["turns_done"], 1)
        self.assertEqual(craft_delayed["turns_total"], 3)

        # Повторный клик - обычный провалившийся ActionResult (ok=False), ход при
        # этом не тратится (см. test_repeat_click_while_running_is_blocked_without_spending_a_turn).
        turn_before = web_ui.STATE.turn
        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_delayed"})
        self.assertFalse(resp["log"]["ok"])
        self.assertEqual(web_ui.STATE.turn, turn_before)

    def test_manual_action_card_exposes_blocked_by_for_other_action_in_same_location(self):
        # craft_delayed_sibling живёт в той же location, что craft_delayed
        # ("workshop_loc") - см. game/engine.py:do_manual_action.
        web_ui.STATE.resources["raw_a"] = 10
        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_delayed"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

        sibling = next(m for m in resp["state"]["categories"]["manual"] if m["id"] == "craft_delayed_sibling")
        self.assertFalse(sibling["already_running"])
        self.assertEqual(sibling["blocked_by"], "Затяжной крафт")

        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_delayed_sibling"})
        self.assertFalse(resp["log"]["ok"])

        # Действие в подлокации ("sub_workshop") под ограничение не подпадает.
        sub = next(m for m in resp["state"]["categories"]["manual"] if m["id"] == "craft_delayed_sub")
        self.assertIsNone(sub["blocked_by"])
        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_delayed_sub"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

    def test_cancel_manual_job_refunds_cost_and_frees_the_action_immediately(self):
        web_ui.STATE.resources["raw_a"] = 10
        self._post("/api/action", {"type": "manual", "id": "craft_delayed"})
        self.assertEqual(web_ui.STATE.resources["raw_a"], 8)
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/cancel_job", {"kind": "manual", "action_id": "craft_delayed"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(resp["state"]["resources"]["raw_a"], 10)  # cost возвращён
        self.assertEqual(web_ui.STATE.turn, turn_before)  # отмена не тратит ход

        craft_delayed = next(m for m in resp["state"]["categories"]["manual"] if m["id"] == "craft_delayed")
        self.assertFalse(craft_delayed["already_running"])

        # действие сразу можно запустить заново
        status, resp = self._post("/api/action", {"type": "manual", "id": "craft_delayed"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

    def test_multiturn_research_exposes_active_research_and_completes_via_advance_turn(self):
        web_ui.STATE.buildings["lab2"] = 1
        web_ui.STATE.resources["raw_a"] = 10
        web_ui.STATE.solved_hypotheses.add("tech_tier2_slow")
        status, resp = self._post("/api/action", {"type": "tech", "id": "tech_tier2_slow"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        state = resp["state"]
        # advance_turn уже прогнан ВНУТРИ этого же запроса - к моменту ответа job
        # успел тикнуть один раз (тот же принцип, что у craft_delayed выше).
        self.assertEqual(state["active_research"], {
            "tech_id": "tech_tier2_slow", "name": "Технология тира 2 (долгая)",
            "turns_done": 1, "turns_total": 5, "paused": None,
        })
        tech_entry = next(t for t in state["tech_tree"] if t["id"] == "tech_tier2_slow")
        self.assertEqual(tech_entry["research_turns"], 5)
        self.assertEqual(tech_entry["status"], "available")  # ещё не изучена

        for _ in range(4):
            self._post("/api/action", {"type": "skip", "id": None})
        status, state = self._get("/api/state")
        self.assertIsNone(state["active_research"])
        tech_entry = next(t for t in state["tech_tree"] if t["id"] == "tech_tier2_slow")
        self.assertEqual(tech_entry["status"], "researched")

    def test_switching_research_over_http_cancels_the_old_one_and_refunds_it(self):
        web_ui.STATE.buildings["lab2"] = 1
        web_ui.STATE.resources["raw_a"] = 10
        web_ui.STATE.solved_hypotheses.update({"tech_tier2_slow", "tech_tier2"})
        self._post("/api/action", {"type": "tech", "id": "tech_tier2_slow"})
        self.assertEqual(web_ui.STATE.resources["raw_a"], 9)
        status, resp = self._post("/api/action", {"type": "tech", "id": "tech_tier2"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertIn("отменено, затраты возвращены", resp["log"]["message"])
        self.assertIsNone(resp["state"]["active_research"])  # tech_tier2 мгновенная
        tech_entry = next(t for t in resp["state"]["tech_tree"] if t["id"] == "tech_tier2_slow")
        self.assertEqual(tech_entry["status"], "available")  # отменённая - осталась неисследованной
        tech_entry = next(t for t in resp["state"]["tech_tree"] if t["id"] == "tech_tier2")
        self.assertEqual(tech_entry["status"], "researched")
        self.assertEqual(resp["state"]["resources"]["raw_a"], 9)  # 10 - 1 (новый cost), старый вернулся

    def test_cancel_research_refunds_cost_over_http(self):
        web_ui.STATE.buildings["lab2"] = 1
        web_ui.STATE.resources["raw_a"] = 10
        web_ui.STATE.solved_hypotheses.add("tech_tier2_slow")
        self._post("/api/action", {"type": "tech", "id": "tech_tier2_slow"})
        self.assertEqual(web_ui.STATE.resources["raw_a"], 9)  # cost списан сразу при старте
        status, resp = self._post("/api/cancel_job", {"kind": "research"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertIsNone(resp["state"]["active_research"])
        self.assertEqual(resp["state"]["resources"]["raw_a"], 10)  # cost возвращён целиком

    def test_farms_and_creature_bank_are_empty_before_any_farm_is_built(self):
        status, state = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["farms"], [])
        self.assertEqual(state["creature_bank"], [])
        self.assertFalse(state["genetics_lab_available"])
        self.assertFalse(state["breeding_station_available"])

    def test_farm_appears_with_slots_once_apiary_is_built(self):
        web_ui.STATE.resources["raw_a"] = 5
        status, resp = self._post("/api/action", {"type": "build", "id": "apiary"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        status, state = self._get("/api/state")
        farm = next(f for f in state["farms"] if f["building_id"] == "apiary")
        self.assertEqual(farm["slot_capacity"], 2)
        self.assertEqual(farm["slots_used"], 0)

    def test_house_creature_puts_it_in_a_farm_and_creature_bank_lists_the_rest(self):
        # Фикстура не даёт действия со spawn_species - особь заводим прямой
        # манипуляцией state.creatures, тем же способом, каким её собрал бы
        # engine.spawn_creature (тот же уровень доверия к state, что и у
        # остальных тестов этого файла).
        web_ui.STATE.resources["raw_a"] = 5
        self._post("/api/action", {"type": "build", "id": "apiary"})
        creature_id = "creature_test_1"
        web_ui.STATE.creatures[creature_id] = {
            "species_id": "critter_a", "genotype": fixtures.make_genotype("critter_a"),
            "farm_instance_id": None, "turns_alive": 0, "identified": False,
        }
        turn_before = web_ui.STATE.turn
        status, state = self._get("/api/state")
        instance_id = next(f for f in state["farms"] if f["building_id"] == "apiary")["id"]

        status, resp = self._post("/api/house_creature", {"creature_id": creature_id, "farm_instance_id": instance_id})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        farm = next(f for f in resp["state"]["farms"] if f["id"] == instance_id)
        self.assertEqual(farm["slots_used"], 1)
        self.assertEqual(resp["state"]["creature_bank"], [])
        self.assertEqual(web_ui.STATE.turn, turn_before)

        status, resp = self._post("/api/release_creature", {"creature_id": creature_id})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        farm = next(f for f in resp["state"]["farms"] if f["id"] == instance_id)
        self.assertEqual(farm["slots_used"], 0)
        self.assertEqual({c["id"] for c in resp["state"]["creature_bank"]}, {creature_id})
        self.assertEqual(web_ui.STATE.turn, turn_before)

    def test_house_creature_without_a_free_slot_fails_cleanly(self):
        web_ui.STATE.resources["raw_a"] = 5
        self._post("/api/action", {"type": "build", "id": "apiary"})  # 2 слота
        status, state = self._get("/api/state")
        instance_id = next(f for f in state["farms"] if f["building_id"] == "apiary")["id"]
        ids = []
        for i in range(3):
            cid = f"creature_test_{i}"
            web_ui.STATE.creatures[cid] = {
                "species_id": "critter_a", "genotype": fixtures.make_genotype("critter_a"),
                "farm_instance_id": None, "turns_alive": 0, "identified": False,
            }
            ids.append(cid)
        self._post("/api/house_creature", {"creature_id": ids[0], "farm_instance_id": instance_id})
        self._post("/api/house_creature", {"creature_id": ids[1], "farm_instance_id": instance_id})
        status, resp = self._post("/api/house_creature", {"creature_id": ids[2], "farm_instance_id": instance_id})
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])

    def test_purchase_farm_upgrade_spends_a_turn_and_is_isolated_per_instance(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "industrial_apiary"})
        self._post("/api/action", {"type": "build", "id": "industrial_apiary"})
        status, state = self._get("/api/state")
        instances = [f["id"] for f in state["farms"] if f["building_id"] == "industrial_apiary"]
        self.assertEqual(len(instances), 2)
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/action", {"type": "farm_instance_upgrade", "id": f"{instances[0]}:ia_output"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before + 1)

        farm_1 = next(f for f in resp["state"]["farms"] if f["id"] == instances[0])
        farm_2 = next(f for f in resp["state"]["farms"] if f["id"] == instances[1])
        purchased_1 = {u["id"] for u in farm_1["upgrades"]["upgrades"] if u["purchased"]}
        purchased_2 = {u["id"] for u in farm_2["upgrades"]["upgrades"] if u["purchased"]}
        self.assertIn("ia_output", purchased_1)
        self.assertNotIn("ia_output", purchased_2)

    def test_insert_and_remove_soul_via_http_uses_instance_id(self):
        # Зеркало test_house_creature_.../test_purchase_farm_upgrade_... выше,
        # но для синтезатора душ - с 2026-09-18 insert_soul/remove_soul тоже
        # берут instance_id (физический экземпляр, state.soul_collector_instances),
        # а не building_id (тип постройки) - см. CLAUDE.md/game/engine.py.
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "soul_synth"})
        self._post("/api/action", {"type": "build", "id": "soul_synth"})
        status, state = self._get("/api/state")
        instances = [e["id"] for e in state["soul_collectors"] if e["building_id"] == "soul_synth"]
        self.assertEqual(len(instances), 2)
        web_ui.STATE.resources["test_soul"] = 1
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/insert_soul", {"instance_id": instances[0], "soul_id": "test_soul"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before)
        entry_1 = next(e for e in resp["state"]["soul_collectors"] if e["id"] == instances[0])
        entry_2 = next(e for e in resp["state"]["soul_collectors"] if e["id"] == instances[1])
        self.assertEqual(len(entry_1["installed"]), 1)
        self.assertEqual(len(entry_2["installed"]), 0)

        status, resp = self._post("/api/remove_soul", {"instance_id": instances[0], "index": 0})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.resources["test_soul"], 1)

    def test_purchase_soul_upgrade_spends_a_turn_and_is_isolated_per_instance(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "soul_synth"})
        self._post("/api/action", {"type": "build", "id": "soul_synth"})
        status, state = self._get("/api/state")
        instances = [e["id"] for e in state["soul_collectors"] if e["building_id"] == "soul_synth"]
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/action", {"type": "soul_instance_upgrade", "id": f"{instances[0]}:soul_synth_slot"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before + 1)

        entry_1 = next(e for e in resp["state"]["soul_collectors"] if e["id"] == instances[0])
        entry_2 = next(e for e in resp["state"]["soul_collectors"] if e["id"] == instances[1])
        self.assertEqual(entry_1["capacity"], 2)  # base 1 + repeatable upgrade x1
        self.assertEqual(entry_2["capacity"], 1)
        purchased_1 = {u["id"] for u in entry_1["upgrades"]["upgrades"] if u["purchased"]}
        purchased_2 = {u["id"] for u in entry_2["upgrades"]["upgrades"] if u["purchased"]}
        self.assertIn("soul_synth_slot", purchased_1)
        self.assertNotIn("soul_synth_slot", purchased_2)

    def test_insert_and_remove_reactor_component_via_http_uses_instance_id(self):
        # Зеркало test_insert_and_remove_soul_via_http_uses_instance_id - установка/
        # снятие компонента реактора тоже берёт instance_id (см. state.py:
        # reactor_instances), не building_id.
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        status, state = self._get("/api/state")
        instances = [e["id"] for e in state["reactors"] if e["building_id"] == "test_reactor"]
        self.assertEqual(len(instances), 2)
        web_ui.STATE.resources["test_fuel"] = 1
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/insert_reactor_component", {"instance_id": instances[0], "cell_index": 0, "resource_id": "test_fuel"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before)
        entry_1 = next(e for e in resp["state"]["reactors"] if e["id"] == instances[0])
        entry_2 = next(e for e in resp["state"]["reactors"] if e["id"] == instances[1])
        self.assertIsNotNone(entry_1["grid"][0])
        self.assertIsNone(entry_2["grid"][0])

        status, resp = self._post("/api/remove_reactor_component", {"instance_id": instances[0], "cell_index": 0})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.resources["test_fuel"], 1)

    def test_set_reactor_zone_control_and_coolant_do_not_advance_turn(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        status, state = self._get("/api/state")
        instance_id = next(e for e in state["reactors"] if e["building_id"] == "test_reactor")["id"]
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/set_reactor_zone_control", {"instance_id": instance_id, "zone_index": 0, "depth_pct": 40})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        status, resp = self._post("/api/set_reactor_zone_coolant", {"instance_id": instance_id, "zone_index": 0, "flow_pct": 60})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before)
        entry = next(e for e in resp["state"]["reactors"] if e["id"] == instance_id)
        self.assertEqual(entry["zones"][0]["control_rod_depth"], 40)
        self.assertEqual(entry["zones"][0]["coolant_flow"], 60)

    def test_purchase_reactor_upgrade_spends_a_turn_and_is_isolated_per_instance(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        status, state = self._get("/api/state")
        instances = [e["id"] for e in state["reactors"] if e["building_id"] == "test_reactor"]
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/action", {"type": "reactor_instance_upgrade", "id": f"{instances[0]}:test_reactor_zoning_2"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before + 1)

        entry_1 = next(e for e in resp["state"]["reactors"] if e["id"] == instances[0])
        entry_2 = next(e for e in resp["state"]["reactors"] if e["id"] == instances[1])
        self.assertEqual(len(entry_1["zones"]), 2)
        self.assertEqual(len(entry_2["zones"]), 1)
        purchased_1 = {u["id"] for u in entry_1["upgrades"]["upgrades"] if u["purchased"]}
        purchased_2 = {u["id"] for u in entry_2["upgrades"]["upgrades"] if u["purchased"]}
        self.assertIn("test_reactor_zoning_2", purchased_1)
        self.assertNotIn("test_reactor_zoning_2", purchased_2)

    def test_tech_tree_lists_unlocks_by_kind_and_improvements(self):
        status, view = self._get("/api/state")
        tech1 = next(t for t in view["tech_tree"] if t["id"] == "tech1")
        groups = {g["label"]: g["items"] for g in tech1["unlock_groups"]}
        self.assertIn("Постройки", groups)
        self.assertIn("Ритуалы", groups)
        self.assertTrue(any("расширение 2" in item for item in groups["Склад"]))
        self.assertIsInstance(tech1["improves"], list)

    def test_grid_modal_shows_and_charges_the_mechanism_scale(self):
        # Верстак - механизм с купленным «Масштабом ×2»: узор тот же (2 ячейки), а
        # модалка показывает и крафт списывает ×2.
        locations = {**web_ui.LOCATIONS, "grid_loc": {**web_ui.LOCATIONS["grid_loc"], "requires_building": "mechanism_a"}}
        with mock.patch.object(web_ui, "LOCATIONS", locations), mock.patch.object(engine, "LOCATIONS", locations):
            web_ui.STATE = GameState(
                resources={"raw_a": 4}, buildings={"mechanism_a": 1},
                building_upgrades={"mechanism_a": ["mech_a_scale"]},
            )
            status, view = self._get("/api/state")
            entry = next(c for c in view["crafting"] if c["location_id"] == "grid_loc")
            recipe = next(r for r in entry["recipes"] if r["action_id"] == "grid_line")
            self.assertEqual(recipe["scale"], 2)
            self.assertEqual([(c["id"], c["amount"]) for c in recipe["cost"]], [("raw_a", 4)])
            self.assertEqual([(g["id"], g["amount"]) for g in recipe["gain"]], [("grid_output", 2)])
            status, resp = self._post("/api/craft_grid", {
                "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 1,
            })
        self.assertTrue(resp["ok"], resp)
        self.assertEqual(web_ui.STATE.resources.get("raw_a", 0), 0)
        self.assertEqual(web_ui.STATE.resources["grid_output"], 2)

    def test_cancel_preview_reports_what_would_burn_without_cancelling(self):
        order = {"id": 1, "kind": "recipe", "target_id": "craft_mid", "label": "x", "steps": [],
                 "reserved": {"limited_res": 6, "raw_a": 2}, "energy_reserve": 0, "final_energy": 0,
                 "status": None, "waits_energy": False}
        web_ui.STATE = GameState(resources={"limited_res": 7}, orders=[order], next_order_id=2)
        status, resp = self._post("/api/cancel_preview", {"kind": "order", "order_id": 1})
        self.assertEqual(status, 200)
        # вместимость 10: из 6 вернувшихся влезут 3
        self.assertEqual([(r["id"], r["amount"]) for r in resp["burn"]], [("limited_res", 3)])
        self.assertEqual({r["id"]: r["amount"] for r in resp["refund"]}, {"limited_res": 6, "raw_a": 2})
        self.assertEqual(len(web_ui.STATE.orders), 1)
        self.assertEqual(web_ui.STATE.resources, {"limited_res": 7})

    def test_instance_upgrades_have_a_price_and_a_label_for_the_confirm_window(self):
        # Окно подтверждения автокрафта (autocraft_preview_view) строится по цене и
        # имени действия - у всех трёх видов экземпляров они должны быть.
        web_ui.STATE.resources["raw_a"] = 100
        for building_id in ("industrial_apiary", "soul_synth", "test_reactor"):
            self._post("/api/action", {"type": "build", "id": building_id})
        s = web_ui.STATE
        cases = [
            ("farm_instance_upgrade", s.farm_instances, "ia_output"),
            ("soul_instance_upgrade", s.soul_collector_instances, "soul_synth_slot"),
            ("reactor_instance_upgrade", s.reactor_instances, "test_reactor_zoning_2"),
        ]
        for kind, instances, upgrade_id in cases:
            action_id = f"{next(iter(instances))}:{upgrade_id}"
            self.assertIsNotNone(engine.resolve_action_cost(s, kind, action_id), kind)
            self.assertTrue(web_ui._action_label(s, kind, action_id), kind)

    def test_purchase_reactor_grid_upgrade_with_dict_value_does_not_crash_view(self):
        # Регрессия: reactor_grid_size хранит value как dict ({"width","height"}),
        # а не число - фильтр "скрыть более слабый купленный уровень того же типа"
        # раньше сравнивал value через max()/< напрямую и падал TypeError на
        # сравнении dict с dict сразу при первой покупке. См.
        # web_ui.py:_superseded_upgrade_ids.
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        status, state = self._get("/api/state")
        instance_id = next(e for e in state["reactors"] if e["building_id"] == "test_reactor")["id"]

        status, resp = self._post("/api/action", {"type": "reactor_instance_upgrade", "id": f"{instance_id}:test_reactor_grid_3x2"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

        # build_view уже пересобран сервером внутри resp - убеждаемся, что
        # список апгрейдов вообще пришёл (а не свалился до ответа) и что
        # купленный dict-value апгрейд помечен purchased.
        entry = next(e for e in resp["state"]["reactors"] if e["id"] == instance_id)
        by_id = {u["id"]: u for u in entry["upgrades"]["upgrades"]}
        self.assertTrue(by_id["test_reactor_grid_3x2"]["purchased"])

        # Повторный GET /api/state (полный build_view с нуля) тоже не должен падать.
        status, state = self._get("/api/state")
        self.assertEqual(status, 200)

    def test_reactor_component_bank_lists_tagged_resources(self):
        web_ui.STATE.resources["test_fuel"] = 3
        status, state = self._get("/api/state")
        bank = {e["id"]: e for e in state["reactor_bank"]}
        self.assertEqual(bank["test_fuel"]["role"], "fuel")
        self.assertEqual(bank["test_fuel"]["amount"], 3)

    def test_breed_action_advances_turn_and_removes_both_parents(self):
        # Скрещивание требует ОТДЕЛЬНЫЙ работающий механизм (crossing_station,
        # поле breeding_station) - фермы (apiary) тут ни при чём.
        web_ui.STATE.resources["raw_a"] = 5
        self._post("/api/action", {"type": "build", "id": "crossing_station"})
        web_ui.STATE.creatures["creature_a"] = {
            "species_id": "critter_a", "genotype": fixtures.make_genotype("critter_a"),
            "farm_instance_id": None, "turns_alive": 0, "identified": False,
        }
        web_ui.STATE.creatures["creature_b"] = {
            "species_id": "critter_a", "genotype": fixtures.make_genotype("critter_a"),
            "farm_instance_id": None, "turns_alive": 0, "identified": False,
        }
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/action", {"type": "breed", "id": "creature_a:creature_b"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before + 1)
        self.assertNotIn("creature_a", web_ui.STATE.creatures)
        self.assertNotIn("creature_b", web_ui.STATE.creatures)
        self.assertEqual(len(web_ui.STATE.creatures), 1)

    def test_analyze_creature_requires_lab_and_does_not_advance_the_turn(self):
        web_ui.STATE.resources["raw_a"] = 5
        web_ui.STATE.creatures["creature_a"] = {
            "species_id": "critter_a", "genotype": fixtures.make_genotype("critter_a"),
            "farm_instance_id": None, "turns_alive": 0, "identified": False,
        }

        status, resp = self._post("/api/analyze_creature", {"creature_id": "creature_a"})
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])  # нет лаборатории

        self._post("/api/action", {"type": "build", "id": "genetics_lab_building"})
        turn_before = web_ui.STATE.turn
        status, resp = self._post("/api/analyze_creature", {"creature_id": "creature_a"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertTrue(web_ui.STATE.creatures["creature_a"]["identified"])
        self.assertEqual(web_ui.STATE.turn, turn_before)

    def test_analyze_creatures_identifies_the_batch_without_advancing_the_turn(self):
        web_ui.STATE.resources["raw_a"] = 5
        for cid in ("creature_a", "creature_b"):
            web_ui.STATE.creatures[cid] = {
                "species_id": "critter_a", "genotype": fixtures.make_genotype("critter_a"),
                "farm_instance_id": None, "turns_alive": 0, "identified": False,
            }
        self._post("/api/action", {"type": "build", "id": "genetics_lab_building"})
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/analyze_creatures", {"creature_ids": ["creature_a", "creature_b"]})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertTrue(web_ui.STATE.creatures["creature_a"]["identified"])
        self.assertTrue(web_ui.STATE.creatures["creature_b"]["identified"])
        self.assertEqual(web_ui.STATE.turn, turn_before)

        status, resp = self._post("/api/analyze_creatures", {"creature_ids": "creature_a"})
        self.assertEqual(status, 400)

    def test_get_root_serves_html(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as r:
            body = r.read().decode("utf-8")
        self.assertEqual(r.status, 200)
        self.assertIn("<html", body.lower())

    def test_manual_action_advances_turn_and_persists(self):
        status, resp = self._post("/api/action", {"type": "manual", "id": "gather_a"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"])
        self.assertEqual(resp["state"]["turn"], 2)
        self.assertEqual(resp["state"]["resources"]["raw_a"], 3)
        self.assertTrue(web_ui.SAVE_PATH.exists())

    def test_skip_turn(self):
        status, resp = self._post("/api/action", {"type": "skip", "id": None})
        self.assertEqual(status, 200)
        self.assertEqual(resp["state"]["turn"], 2)

    def test_failed_action_does_not_advance_turn(self):
        status, resp = self._post("/api/action", {"type": "build", "id": "gen"})  # ресурсов ещё нет
        self.assertEqual(status, 200)
        self.assertFalse(resp["log"]["ok"])
        self.assertEqual(resp["state"]["turn"], 1)  # неудачное действие хода не тратит

    def test_unknown_action_id_returns_400(self):
        status, resp = self._post("/api/action", {"type": "manual", "id": "no_such_action"})
        self.assertEqual(status, 400)
        self.assertIn("error", resp)

    def test_unknown_action_type_returns_400(self):
        status, resp = self._post("/api/action", {"type": "bogus", "id": "x"})
        self.assertEqual(status, 400)

    def test_pin_toggle(self):
        status, resp = self._post("/api/pin", {"resource_id": "raw_a"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertIn("raw_a", resp["state"]["pinned"])

    def test_pin_unknown_resource_id_fails_cleanly(self):
        # resource_id идёт прямо из тела запроса - мусорный id должен вернуть
        # ok:false, а не уронить хендлер KeyError'ом (не было try/except на этом пути)
        status, resp = self._post("/api/pin", {"resource_id": "no_such_resource"})
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])

    def test_order_api_previews_places_and_cancels_an_order(self):
        # Заказ «рецепт ×N» - обычное действие (тратит ход, как клик), предпросмотр
        # и отмена - нет. trickle_hub - сборщик мастерской, 2 крафта за ход.
        web_ui.STATE.buildings["trickle_hub"] = 1
        web_ui.STATE.resources["slow_raw"] = 10
        status, preview = self._post("/api/order_preview", {"action_id": "craft_slow_mid", "qty": 3})
        self.assertEqual(status, 200)
        self.assertTrue(preview["can_order"], preview["reason"])
        self.assertEqual(preview["eta_turns"], 2)

        status, resp = self._post("/api/action", {"type": "order", "id": "craft_slow_mid:3"})
        self.assertEqual(status, 200)
        (order,) = resp["state"]["autocraft"]["orders"]
        self.assertTrue(order["label"].endswith("» ×3"), order["label"])
        self.assertEqual(web_ui.STATE.resources["slow_raw"], 7)  # изъято в резерв

        turn_before = web_ui.STATE.turn
        status, resp = self._post("/api/cancel_job", {"kind": "order", "order_id": order["id"]})
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(resp["state"]["autocraft"]["orders"], [])
        self.assertEqual(web_ui.STATE.turn, turn_before)

    def test_order_api_handles_converter_recipes(self):
        # Рецепт преобразователя заказывается по ключу "energy_recipe:<id>", N - партии.
        web_ui.STATE.buildings.update({"energy_converter": 1, "bare_processor": 1})
        key = "energy_recipe:upgrade_matter"
        status, preview = self._post("/api/order_preview", {"action_id": key, "qty": 2})
        self.assertEqual(status, 200)
        self.assertFalse(preview["can_order"])
        self.assertIn("«Автоматизация»", preview["reason"])

        web_ui.STATE.buildings["matter_generator"] = 1
        web_ui.STATE.building_upgrades["matter_generator"] = ["matter_auto"]
        web_ui.STATE.resources["raw_a"] = 10
        status, preview = self._post("/api/order_preview", {"action_id": key, "qty": 2})
        self.assertTrue(preview["can_order"], preview["reason"])
        status, resp = self._post("/api/action", {"type": "order", "id": f"{key}:2"})
        self.assertEqual(status, 200)
        (order,) = resp["state"]["autocraft"]["orders"]
        self.assertEqual(order["steps"][0]["name"], "Улучшить материю")
        self.assertEqual(web_ui.STATE.resources["raw_a"], 0)  # 2 × 5 в резерве

    def test_order_api_handles_rituals(self):
        # Ритуал заказывается по ключу "ritual_recipe:<id>" - проводят его только
        # сборщики алтаря (autocraft_assembler {building}), 1 ритуал за ход у обычного.
        assembler = {
            "name": "Сборщик ритуалов (тест)", "description": "Тест.", "requires_tech": None, "cost": {},
            "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
            "autocraft_assembler": {"building": "ritual_altar", "per_turn": 1},
        }
        with mock.patch.dict(engine.BUILDINGS, {"ritual_asm": assembler}):
            web_ui.STATE.buildings.update({"ritual_altar": 1, "bare_processor": 1})
            web_ui.STATE.resources.update({"raw_a": 4, "raw_b": 2})
            key = "ritual_recipe:ritual_basic"
            status, preview = self._post("/api/order_preview", {"action_id": key, "qty": 2})
            self.assertEqual(status, 200)
            self.assertFalse(preview["can_order"])
            self.assertIn("«Сборщик ритуалов (тест)»", preview["reason"])

            web_ui.STATE.buildings["ritual_asm"] = 1
            status, preview = self._post("/api/order_preview", {"action_id": key, "qty": 2})
            self.assertTrue(preview["can_order"], preview["reason"])
            self.assertEqual(preview["eta_turns"], 2)
            self.assertEqual(
                web_ui.assembler_view("ritual_asm"),
                {"location_name": "Ритуальный алтарь (тест)", "per_turn": 1, "ritual": True},
            )

            status, resp = self._post("/api/action", {"type": "order", "id": f"{key}:2"})
            self.assertEqual(status, 200)
            (order,) = resp["state"]["autocraft"]["orders"]
            self.assertEqual(order["label"], "ритуал «Простой ритуал» ×2")
            self.assertEqual(order["steps"][0]["done"], 1)  # заказ потратил ход - первый ритуал готов
            self.assertEqual(web_ui.STATE.resources["raw_a"], 0)  # всё сырьё в резерве
            self.assertEqual(web_ui.STATE.ritual_jobs, {})  # алтарь свободен

    def test_preview_tree_names_the_ritual_step(self):
        assembler = {
            "name": "Сборщик ритуалов (тест)", "description": "Тест.", "requires_tech": None, "cost": {},
            "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
            "autocraft_assembler": {"building": "ritual_altar", "per_turn": None},
        }
        with mock.patch.dict(engine.RITUAL_RECIPES, {"ritual_only": {
            "name": "Только ритуалом", "description": "Тест.", "requires_tech": None,
            "requires_building": "ritual_altar", "ring_cost": {"raw_a": 1}, "center_cost": "raw_b",
            "extra_cost": {}, "duration_turns": 3, "extra_consumes_per_turn": {}, "extra_consumes_chance": {},
            "produces": {"mineral": 2}, "produces_chance": {},
        }}), mock.patch.dict(engine.BUILDINGS, {"ritual_asm": assembler}):
            web_ui.STATE.buildings.update({"ritual_altar": 1, "bare_processor": 1, "ritual_asm": 1})
            preview = engine.autocraft_preview(web_ui.STATE, {"mineral": 3})
            (node,) = preview["nodes"]
            self.assertEqual((node["ritual_recipe_id"], node["fires"]), ("ritual_only", 2))
            self.assertEqual(web_ui._preview_node_view(node)["action_name"], "Только ритуалом (ритуал)")

    def test_preview_tree_names_the_converter_step(self):
        web_ui.STATE.buildings.update({"energy_converter": 1, "bare_processor": 1})
        web_ui.STATE.building_upgrades["energy_converter"] = ["conv_auto"]
        view = web_ui.order_preview_view(web_ui.STATE, "assemble_capacitor", 1)
        (node,) = [n for n in view["tree"] if n["resource_id"] == "raw_b"]
        self.assertEqual(node["action_name"], "Сделать сырьё B из энергии (преобразователь)")

    def test_enchanting_view_exposes_strip_on_fail_as_a_content_setting(self):
        # strip_on_fail - настройка КОНТЕНТА (buildings.*.enchanting_table, правит
        # автор в редакторе), не игрока - нет отдельного API-эндпоинта, чтобы
        # игрок мог её переключить сам.
        status, resp = self._get("/api/state")
        self.assertTrue(resp["enchanting"]["strip_on_fail"])  # дефолт, стола ещё нет

        web_ui.STATE.buildings["ench_table"] = 1
        with mock.patch.dict(engine.BUILDINGS["ench_table"]["enchanting_table"], {"strip_on_fail": False}):
            status, resp = self._get("/api/state")
        self.assertFalse(resp["enchanting"]["strip_on_fail"])

    def test_set_action_priority_overrides_default_without_advancing_turn(self):
        status, resp = self._get("/api/state")
        default_entry = next(a for a in resp["action_priorities"] if a["id"] == "craft_mid")
        self.assertEqual(default_entry["priority"], 0)
        self.assertFalse(default_entry["overridden"])

        status, resp = self._post("/api/set_action_priority", {"action_id": "craft_mid", "priority": 7})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["state"]["turn"], 1)  # это настройка, не игровое действие - ход не тратится
        entry = next(a for a in resp["state"]["action_priorities"] if a["id"] == "craft_mid")
        self.assertEqual(entry["priority"], 7)
        self.assertTrue(entry["overridden"])

        status, resp = self._post("/api/set_action_priority", {"action_id": "craft_mid", "priority": None})
        entry = next(a for a in resp["state"]["action_priorities"] if a["id"] == "craft_mid")
        self.assertEqual(entry["priority"], 0)
        self.assertFalse(entry["overridden"])

    def test_converter_recipes_are_listed_and_take_a_priority(self):
        # make_raw_b - единственный путь к raw_b (обычного рецепта в фикстуре нет),
        # поэтому он основной, а не запасной.
        status, resp = self._get("/api/state")
        entry = next(a for a in resp["action_priorities"] if a["id"] == "energy_recipe:make_raw_b")
        self.assertEqual((entry["kind"], entry["priority"]), ("energy_recipe", 0))
        self.assertEqual((entry["is_active_recipe"], entry["is_fallback"]), (True, False))
        status, resp = self._post("/api/set_action_priority", {"action_id": "energy_recipe:make_raw_b", "priority": 5})
        self.assertTrue(resp["ok"])
        entry = next(a for a in resp["state"]["action_priorities"] if a["id"] == "energy_recipe:make_raw_b")
        self.assertEqual((entry["priority"], entry["overridden"]), (5, True))

    def test_set_action_priority_rejects_unknown_action(self):
        status, resp = self._post("/api/set_action_priority", {"action_id": "does_not_exist", "priority": 1})
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])

    def test_energy_priorities_view_lists_every_machine_type_with_quota(self):
        status, resp = self._get("/api/state")
        by_id = {e["id"]: e for e in resp["energy_priorities"]}
        self.assertEqual(set(by_id), {"energy_converter", "mega_converter", "matter_generator"})
        # Ни один тип ещё не построен - built_count 0, запись всё равно
        # присутствует (тот же принцип, что у action_priorities), а квота 0
        # (не путать с "без лимита" - см. engine.energy_converter_max_energy_per_turn).
        self.assertEqual(by_id["energy_converter"]["built_count"], 0)
        self.assertEqual(by_id["energy_converter"]["quota"], 0)
        self.assertEqual(by_id["matter_generator"]["shortfall_last_turn"], 0)  # хода ещё не было

        web_ui.STATE.resources["raw_a"] = 10
        status, resp = self._post("/api/action", {"type": "build", "id": "energy_converter"})
        status, resp = self._post("/api/action", {"type": "build", "id": "mega_converter"})
        by_id = {e["id"]: e for e in resp["state"]["energy_priorities"]}
        self.assertEqual(by_id["energy_converter"]["built_count"], 1)
        self.assertEqual(by_id["energy_converter"]["quota"], 4)  # база из фикстуры, без бустов
        self.assertIsNone(by_id["mega_converter"]["quota"])  # без лимита, даже построенный

    def test_set_energy_priority_overrides_default_without_advancing_turn(self):
        status, resp = self._get("/api/state")
        default_entry = next(e for e in resp["energy_priorities"] if e["id"] == "matter_generator")
        self.assertEqual(default_entry["priority"], 0)
        self.assertFalse(default_entry["overridden"])

        status, resp = self._post("/api/set_energy_priority", {"building_id": "matter_generator", "priority": 9})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["state"]["turn"], 1)  # настройка, не игровое действие - ход не тратится
        entry = next(e for e in resp["state"]["energy_priorities"] if e["id"] == "matter_generator")
        self.assertEqual(entry["priority"], 9)
        self.assertTrue(entry["overridden"])

        status, resp = self._post("/api/set_energy_priority", {"building_id": "matter_generator", "priority": None})
        entry = next(e for e in resp["state"]["energy_priorities"] if e["id"] == "matter_generator")
        self.assertEqual(entry["priority"], 0)
        self.assertFalse(entry["overridden"])

    def test_set_energy_priority_rejects_unknown_building(self):
        status, resp = self._post("/api/set_energy_priority", {"building_id": "does_not_exist", "priority": 1})
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])

    def test_energy_shortfall_snapshot_surfaces_after_a_turn_and_clears_on_reset(self):
        # Пул на двоих не хватает (20 < 4+20) - matter_generator (объявлен позже
        # energy_converter, приоритет по умолчанию у обоих равен) недополучит.
        web_ui.STATE.resources.update({"energy": 20, "raw_a": 100})
        self._post("/api/action", {"type": "build", "id": "energy_converter"})
        self._post("/api/action", {"type": "build", "id": "matter_generator"})
        web_ui.STATE.resources["energy"] = 20
        self._post("/api/action", {"type": "start_energy_recipe", "id": "make_raw_b"})
        self._post("/api/action", {"type": "start_energy_recipe", "id": "upgrade_matter"})
        web_ui.STATE.resources["energy"] = 20
        status, resp = self._post("/api/action", {"type": "skip", "id": None})
        by_id = {e["id"]: e for e in resp["state"]["energy_priorities"]}
        self.assertGreater(by_id["matter_generator"]["shortfall_last_turn"], 0)
        self.assertEqual(by_id["energy_converter"]["shortfall_last_turn"], 0)

        status, resp = self._post("/api/reset", {})
        by_id = {e["id"]: e for e in resp["state"]["energy_priorities"]}
        self.assertEqual(by_id["matter_generator"]["shortfall_last_turn"], 0)  # снимок очищен

    def test_unknown_path_returns_404(self):
        status, resp = self._get("/api/nope")
        self.assertEqual(status, 404)

    def test_quest_completion_surfaces_in_log_and_state(self):
        web_ui.STATE.resources["raw_b"] = 20
        status, resp = self._post("/api/action", {"type": "build", "id": "gen"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["log"]["ok"])
        completed_ids = [q["id"] for q in resp["log"]["quests_completed"]]
        self.assertIn("test_quest", completed_ids)
        quest_status = {q["id"]: q["status"] for q in resp["state"]["quests"]}
        self.assertEqual(quest_status["test_quest"], "completed")

    def test_stats_endpoint_reflects_played_turns(self):
        self._post("/api/action", {"type": "manual", "id": "gather_a"})  # gather_a бесплатен, cost={}
        status, resp = self._post("/api/stats", {"start_turn": 1, "end_turn": 1})
        self.assertEqual(status, 200)
        self.assertIsNotNone(resp["stats"])
        gained = {r["id"]: r["amount"] for r in resp["stats"]["resource_gained"]}
        self.assertEqual(gained.get("raw_a"), 3)

    def test_stats_endpoint_empty_range(self):
        status, resp = self._post("/api/stats", {"start_turn": 500, "end_turn": 600})
        self.assertEqual(status, 200)
        self.assertIsNone(resp["stats"])

    def test_stats_endpoint_rejects_non_numeric_range(self):
        status, resp = self._post("/api/stats", {"start_turn": "abc", "end_turn": 5})
        self.assertEqual(status, 400)

    def test_reset_wipes_progress_and_persists(self):
        self._post("/api/action", {"type": "manual", "id": "gather_a"})
        status, resp = self._post("/api/reset", {})
        self.assertEqual(status, 200)
        self.assertEqual(resp["state"]["turn"], 1)
        self.assertEqual(resp["state"]["resources"], {})
        reloaded = GameState.load(str(web_ui.SAVE_PATH))
        self.assertEqual(reloaded.turn, 1)
        self.assertEqual(reloaded.resources, {})

    def test_saves_list_is_empty_by_default_then_reflects_a_saved_slot(self):
        status, resp = self._get("/api/saves")
        self.assertEqual(status, 200)
        self.assertEqual([s["name"] for s in resp["saves"]], [])

        status, resp = self._post("/api/saves/save", {"name": "slot_one"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        names = [s["name"] for s in resp["saves"]]
        self.assertIn("slot_one", names)
        self.assertTrue((web_ui.SAVES_DIR / "slot_one.json").exists())

    def test_save_slot_captures_current_turn(self):
        self._post("/api/action", {"type": "manual", "id": "gather_a"})  # ход 1 -> 2
        self._post("/api/saves/save", {"name": "slot_turn"})
        status, resp = self._get("/api/saves")
        entry = next(s for s in resp["saves"] if s["name"] == "slot_turn")
        self.assertEqual(entry["turn"], 2)

    def test_load_save_replaces_state_and_persists_as_autosave(self):
        # готовим именованное сохранение с известным прогрессом
        snapshot = GameState(turn=7, resources={"raw_a": 42})
        web_ui.SAVES_DIR.mkdir(exist_ok=True)
        snapshot.save(str(web_ui.SAVES_DIR / "slot_load.json"))

        status, resp = self._post("/api/saves/load", {"name": "slot_load"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["state"]["turn"], 7)
        self.assertEqual(resp["state"]["resources"], {"raw_a": 42})
        self.assertEqual(resp["state"]["save_name"], "slot_load")
        # загруженное сохранение сразу становится текущим автосохранением
        reloaded = GameState.load(str(web_ui.SAVE_PATH))
        self.assertEqual(reloaded.turn, 7)
        self.assertEqual(reloaded.resources, {"raw_a": 42})
        self.assertEqual(reloaded.save_name, "slot_load")

    def test_save_as_updates_save_name_on_current_state(self):
        status, resp = self._get("/api/saves")
        self.assertIsNone(self._state_save_name())

        status, resp = self._post("/api/saves/save", {"name": "my_slot"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["state"]["save_name"], "my_slot")
        self.assertEqual(self._state_save_name(), "my_slot")

    def test_reset_clears_save_name(self):
        self._post("/api/saves/save", {"name": "before_reset"})
        self.assertEqual(self._state_save_name(), "before_reset")
        status, resp = self._post("/api/reset", {})
        self.assertIsNone(resp["state"]["save_name"])
        self.assertIsNone(self._state_save_name())

    def _state_save_name(self):
        _, resp = self._get("/api/state")
        return resp["save_name"]

    def test_load_unknown_save_returns_404(self):
        status, resp = self._post("/api/saves/load", {"name": "no_such_slot"})
        self.assertEqual(status, 404)

    def test_save_rejects_path_traversal_name(self):
        status, resp = self._post("/api/saves/save", {"name": "../evil"})
        self.assertEqual(status, 400)
        status, resp = self._post("/api/saves/save", {"name": ""})
        self.assertEqual(status, 400)

    def test_save_rejects_reserved_autosave_name(self):
        # "autosave" зарезервировано под SAVE_PATH (файл autosave.json в той же
        # SAVES_DIR) - иначе именованное сохранение перезаписало бы автосейв.
        status, resp = self._post("/api/saves/save", {"name": "autosave"})
        self.assertEqual(status, 400)
        status, resp = self._post("/api/saves/save", {"name": "AutoSave"})
        self.assertEqual(status, 400)

    def test_saves_list_excludes_the_autosave_file(self):
        web_ui.SAVES_DIR.mkdir(exist_ok=True)
        web_ui.STATE.save(str(web_ui.SAVES_DIR / "autosave.json"))
        status, resp = self._get("/api/saves")
        self.assertEqual(status, 200)
        self.assertNotIn("autosave", [s["name"] for s in resp["saves"]])

    # -- Сеточный крафт (см. PLAN_grid_crafting.md, этап 3) --------------------

    def test_grid_actions_are_hidden_from_the_plain_manual_list(self):
        status, resp = self._get("/api/state")
        self.assertEqual(status, 200)
        manual_ids = {m["id"] for m in resp["categories"]["manual"]}
        self.assertNotIn("grid_line", manual_ids)
        self.assertNotIn("grid_mirror", manual_ids)
        # Действие БЕЗ узора в той же локации-механизме остаётся обычной карточкой.
        self.assertIn("grid_plain_card", manual_ids)

    def test_crafting_section_lists_the_grid_location(self):
        status, resp = self._get("/api/state")
        self.assertEqual(status, 200)
        entries = {c["location_id"]: c for c in resp["crafting"]}
        self.assertIn("grid_loc", entries)
        entry = entries["grid_loc"]
        self.assertEqual(entry["width"], 2)
        self.assertEqual(entry["height"], 2)
        recipe_ids = {r["action_id"] for r in entry["recipes"]}
        # grid_locked требует tech1, ещё не открыт - не в списке рецептов верстака.
        self.assertEqual(recipe_ids, {"grid_line", "grid_mirror"})

    def test_crafting_recipe_reports_enough_flag(self):
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._get("/api/state")
        entry = next(c for c in resp["crafting"] if c["location_id"] == "grid_loc")
        grid_line = next(r for r in entry["recipes"] if r["action_id"] == "grid_line")
        self.assertTrue(grid_line["enough"])
        grid_mirror = next(r for r in entry["recipes"] if r["action_id"] == "grid_mirror")
        self.assertFalse(grid_mirror["enough"])  # нужен ещё raw_b, которого нет

    def test_craft_grid_matches_and_spends_a_turn(self):
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 1,
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["action_id"], "grid_line")
        self.assertEqual(resp["times_done"], 1)
        self.assertEqual(resp["state"]["turn"], 2)
        self.assertEqual(resp["state"]["resources"]["raw_a"], 98)
        self.assertEqual(resp["state"]["resources"]["grid_output"], 1)

    def test_crafting_view_exposes_mechanism_appearance(self):
        status, state = self._get("/api/state")
        entry = next(c for c in state["crafting"] if c["location_id"] == "grid_loc")
        # Не оформлен - всё пусто, прогресс заливается слева направо (как стрелка).
        self.assertEqual(entry["appearance"], {
            "texture": None, "texture_dim": None, "progress_icon": None, "progress_fill": "right",
            "motion": None, "particles": None,
        })
        icon = {"grid": ["A"], "palette": {"A": "#ffffff"}}
        web_ui.LOCATIONS["grid_loc"]["appearance"] = {
            "texture": icon, "progress_icon": icon, "progress_fill": "up",
            "motion": "strike", "particles": "sparks", "sound": "mechanism_grid_loc.wav",
        }
        try:
            status, state = self._get("/api/state")
            look = next(c for c in state["crafting"] if c["location_id"] == "grid_loc")["appearance"]
            self.assertEqual((look["progress_fill"], look["motion"], look["particles"]), ("up", "strike", "sparks"))
            self.assertEqual(look["texture"], icon)
            self.assertNotIn("sound", look)  # звук подставляет сервер в лог хода
        finally:
            del web_ui.LOCATIONS["grid_loc"]["appearance"]

    def test_mechanism_sound_is_fallback_for_recipes_without_own_sound(self):
        web_ui.STATE.resources["raw_a"] = 100
        web_ui.LOCATIONS["grid_loc"]["appearance"] = {"sound": "mechanism_grid_loc.wav"}
        try:
            # сеточный крафт
            status, resp = self._post("/api/craft_grid", {
                "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 1,
            })
            self.assertEqual(resp["logs"][0]["sound_url"], "/sounds/mechanism_grid_loc.wav")
            # обычная карточка действия той же локации
            status, resp = self._post("/api/action", {"type": "manual", "id": "grid_plain_card"})
            self.assertEqual(resp["log"]["sound_url"], "/sounds/mechanism_grid_loc.wav")
            # свой звук рецепта главнее звука механизма
            web_ui.MANUAL_ACTIONS["grid_line"]["sound"] = "grid_line.wav"
            try:
                status, resp = self._post("/api/craft_grid", {
                    "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 1,
                })
                self.assertEqual(resp["logs"][0]["sound_url"], "/sounds/grid_line.wav")
            finally:
                del web_ui.MANUAL_ACTIONS["grid_line"]["sound"]
        finally:
            del web_ui.LOCATIONS["grid_loc"]["appearance"]

    def test_mechanism_fx_js_is_served(self):
        status, body = self._get_raw("/mechanism_fx.js")
        self.assertEqual(status, 200)
        self.assertIn(b"MECH_FX_MOTIONS", body)

    def test_hunt_attack_qte_flag_from_request_body(self):
        # {"qte": true} в теле /api/action - бонус к урону combat.qte_damage_bonus.
        from unittest import mock
        from game import engine
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            web_ui.STATE.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
            status, resp = self._post("/api/action", {"type": "hunt_attack", "id": None, "qte": True})
            self.assertEqual(status, 200)
            self.assertEqual(resp["log"]["combat"]["damage"], 5)
            web_ui.STATE.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
            status, resp = self._post("/api/action", {"type": "hunt_attack", "id": None})
            self.assertEqual(resp["log"]["combat"]["damage"], 3)
            self.assertFalse(resp["log"]["combat"]["qte_hit"])

    def test_hunt_fx_js_is_served(self):
        # Сцена схватки - общий с превью в редакторе файл (tools/hunt_fx.js).
        status, body = self._get_raw("/hunt_fx.js")
        self.assertEqual(status, 200)
        self.assertIn(b"class HuntStage", body)

    def test_craft_grid_rejects_unmatched_layout_without_spending_a_turn(self):
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": [None, None, None, None], "times": 1,
        })
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])
        self.assertIsNone(resp["action_id"])
        self.assertEqual(resp["state"]["turn"], 1)

    def test_craft_grid_ignores_locked_recipe_layout(self):
        web_ui.STATE.resources["raw_b"] = 100
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_b", None, None, None], "times": 1,
        })
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])  # grid_locked требует tech1, ещё не открыт

    def test_craft_grid_times_n_runs_n_turns(self):
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 3,
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["times_done"], 3)
        self.assertEqual(resp["state"]["turn"], 4)
        self.assertEqual(len(resp["logs"]), 3)

    def test_craft_grid_times_n_stops_on_first_shortfall(self):
        # Хватает ровно на 2 крафта (raw_a: 2 на каждый) - третий должен уйти в
        # докрафт/отложенный платёж и не пройти сразу (craft_final-подобный сырой
        # ресурс без рецепта - см. фикстуру raw_a: голое сырьё, докрафтить нечем).
        web_ui.STATE.resources["raw_a"] = 4
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 5,
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])  # хотя бы одна итерация прошла
        self.assertEqual(resp["times_done"], 2)
        self.assertEqual(resp["state"]["turn"], 3)

    def test_crafting_entry_reports_batch_flag(self):
        status, resp = self._get("/api/state")
        entry = next(c for c in resp["crafting"] if c["location_id"] == "grid_loc")
        self.assertTrue(entry["batch"])

    def test_craft_grid_times_clamped_to_one_without_batch(self):
        # Механизм без crafting.batch (например, печь) крафтит строго по одному -
        # сервер режет times сам, не доверяя "×N" с клиента.
        crafting = web_ui.LOCATIONS["grid_loc"]["crafting"]
        crafting["batch"] = False
        self.addCleanup(crafting.__setitem__, "batch", True)
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 5,
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["times_requested"], 1)
        self.assertEqual(resp["times_done"], 1)
        self.assertEqual(resp["state"]["turn"], 2)
        entry = next(c for c in resp["state"]["crafting"] if c["location_id"] == "grid_loc")
        self.assertFalse(entry["batch"])

    def _make_grid_line_multi_turn(self, turns):
        # Долгий сеточный рецепт (duration_turns) - прогресс-бар в модалке механизма.
        action = web_ui.MANUAL_ACTIONS["grid_line"]
        action["duration_turns"] = turns
        self.addCleanup(action.pop, "duration_turns", None)

    def test_crafting_recipe_reports_duration_and_click_delay(self):
        status, resp = self._get("/api/state")
        entry = next(c for c in resp["crafting"] if c["location_id"] == "grid_loc")
        grid_line = next(r for r in entry["recipes"] if r["action_id"] == "grid_line")
        self.assertEqual(grid_line["duration_turns"], 1)
        self.assertIsNone(grid_line["click_delay_ms"])
        self.assertIsNone(entry["job"])

    def test_crafting_entry_reports_running_multi_turn_job(self):
        self._make_grid_line_multi_turn(3)
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 1,
        })
        self.assertTrue(resp["ok"])
        entry = next(c for c in resp["state"]["crafting"] if c["location_id"] == "grid_loc")
        grid_line = next(r for r in entry["recipes"] if r["action_id"] == "grid_line")
        self.assertEqual(grid_line["duration_turns"], 3)
        job = entry["job"]
        self.assertIsNotNone(job)
        self.assertEqual(job["action_id"], "grid_line")
        self.assertEqual(job["turns_total"], 3)
        self.assertEqual([g["id"] for g in job["gain"]], ["grid_output"])

    def test_craft_grid_multi_turn_recipe_runs_once_even_with_times(self):
        # Долгий рецепт занимает механизм целиком - вторая итерация пачки
        # гарантированно упёрлась бы в "уже выполняется", сервер режет до 1.
        self._make_grid_line_multi_turn(3)
        web_ui.STATE.resources["raw_a"] = 100
        status, resp = self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 5,
        })
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["times_requested"], 1)
        self.assertEqual(resp["times_done"], 1)
        self.assertEqual(resp["state"]["resources"]["raw_a"], 98)

    def test_log_reports_completed_manual_job_ids(self):
        self._make_grid_line_multi_turn(2)
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/craft_grid", {
            "location_id": "grid_loc", "placed": ["raw_a", "raw_a", None, None], "times": 1,
        })
        completed = []
        for _ in range(3):
            status, resp = self._post("/api/action", {"type": "skip", "id": None})
            completed.extend(resp["log"]["manual_completed_ids"])
            if completed:
                break
        self.assertEqual(completed, ["grid_line"])
        entry = next(c for c in resp["state"]["crafting"] if c["location_id"] == "grid_loc")
        self.assertIsNone(entry["job"])

    def test_state_reports_pinned_recipes_including_locked(self):
        # grid_locked ещё закрыт технологией (его нет в crafting[].recipes), но
        # закреплённым он должен числиться - звезда в карточке справочника.
        web_ui.STATE.pinned_recipes.append("grid_locked")
        status, resp = self._get("/api/state")
        self.assertEqual(resp["pinned_recipes"], ["grid_locked"])
        # И плитка слева у него есть - иначе закреплённое «пропадало» бы до открытия.
        self.assertEqual([t["action_id"] for t in resp["pinned_tiles"]], ["grid_locked"])
        self.assertTrue(resp["pinned_tiles"][0]["locked"])

    def test_pin_any_source_from_the_reference(self):
        # Звезда есть у любого способа справочника, не только у сеточного крафта.
        key = next(s["pin_key"] for s in web_ui.resource_sources("mid") if s["kind"] == "ritual")
        status, resp = self._post("/api/pin_recipe", {"action_id": key})
        self.assertTrue(resp["ok"])
        self.assertIn(key, resp["state"]["pinned_recipes"])
        tile = next(t for t in resp["state"]["pinned_tiles"] if t["action_id"] == key)
        self.assertEqual((tile["source_kind"], tile["gain"][0]["id"]), ("ritual", "mid"))
        status, resp = self._post("/api/pin_recipe", {"action_id": key})
        self.assertNotIn(key, resp["state"]["pinned_recipes"])

    def test_pin_refuses_made_up_source(self):
        status, resp = self._post("/api/pin_recipe", {"action_id": "source:mid:ritual:nope"})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["state"]["pinned_recipes"], [])

    def test_pin_recipe_toggle(self):
        status, resp = self._post("/api/pin_recipe", {"action_id": "grid_line"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        entry = next(c for c in resp["state"]["crafting"] if c["location_id"] == "grid_loc")
        grid_line = next(r for r in entry["recipes"] if r["action_id"] == "grid_line")
        self.assertTrue(grid_line["pinned"])

        status, resp = self._post("/api/pin_recipe", {"action_id": "grid_line"})
        self.assertTrue(resp["ok"])
        entry = next(c for c in resp["state"]["crafting"] if c["location_id"] == "grid_loc")
        grid_line = next(r for r in entry["recipes"] if r["action_id"] == "grid_line")
        self.assertFalse(grid_line["pinned"])

    def test_pin_recipe_rejects_plain_action(self):
        status, resp = self._post("/api/pin_recipe", {"action_id": "grid_plain_card"})
        self.assertEqual(status, 200)
        self.assertFalse(resp["ok"])

    def test_engine_error_mid_turn_rolls_the_whole_action_back(self):
        # Действие прошло, ход упал посреди - ни ресурсов действия, ни номера хода.
        web_ui.STATE = GameState(resources={"raw_a": 1})
        with mock.patch.object(engine, "check_quests", side_effect=RuntimeError("сломано")):
            status, resp = self._post("/api/action", {"type": "manual", "id": "gather_a"})
        self.assertEqual(status, 500)
        self.assertFalse(resp["ok"])
        self.assertIn("действие отменено", resp["error"])
        self.assertEqual((web_ui.STATE.turn, web_ui.STATE.resources), (1, {"raw_a": 1}))
        self.assertEqual(resp["state"]["turn"], 1)
        # Следующий клик работает как обычно.
        status, resp = self._post("/api/action", {"type": "manual", "id": "gather_a"})
        self.assertEqual((status, web_ui.STATE.turn, web_ui.STATE.resources["raw_a"]), (200, 2, 4))

    def test_bad_request_leaves_state_untouched(self):
        web_ui.STATE = GameState(resources={"raw_a": 1})
        with mock.patch.object(engine, "advance_turn", side_effect=KeyError("сломано")):
            status, resp = self._post("/api/action", {"type": "manual", "id": "gather_a"})
        self.assertEqual(status, 400)
        self.assertIn("Действие отменено", resp["error"])
        self.assertEqual(web_ui.STATE.resources, {"raw_a": 1})

    def test_state_that_cannot_be_shown_reports_an_error(self):
        with mock.patch.object(web_ui, "build_view", side_effect=KeyError("old_mob")):
            status, resp = self._get("/api/state")
        self.assertEqual(status, 500)
        self.assertIn("old_mob", resp["error"])

    def test_broken_autosave_is_set_aside_and_a_new_game_starts(self):
        web_ui.SAVE_PATH.write_text("{битый", encoding="utf-8")
        state = web_ui.load_state()
        self.assertEqual(state.turn, 1)
        self.assertFalse(web_ui.SAVE_PATH.exists())
        backups = list(web_ui.SAVES_DIR.glob("autosave.broken-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{битый")
        self.assertIn(backups[0].stem, web_ui.LOAD_NOTICE)
        # Сообщение - один раз, первым запросом состояния.
        status, view = self._get("/api/state")
        self.assertIn(backups[0].stem, view["load_notice"])
        status, view = self._get("/api/state")
        self.assertNotIn("load_notice", view)

    def test_reset_with_keep_copy_sets_the_autosave_aside(self):
        GameState(turn=40).save(str(web_ui.SAVE_PATH))
        status, resp = self._post("/api/reset", {"keep_copy": True})
        self.assertEqual((status, resp["state"]["turn"]), (200, 1))
        backups = list(web_ui.SAVES_DIR.glob("autosave.broken-*.json"))
        self.assertEqual(GameState.load(str(backups[0])).turn, 40)

    def test_broken_named_save_does_not_replace_the_current_game(self):
        web_ui.STATE = GameState(turn=7)
        web_ui.SAVES_DIR.mkdir(exist_ok=True)
        (web_ui.SAVES_DIR / "битый.json").write_text("[]", encoding="utf-8")
        status, resp = self._post("/api/saves/load", {"name": "битый"})
        self.assertEqual(status, 400)
        self.assertIn("не открывается", resp["error"])
        self.assertEqual(web_ui.STATE.turn, 7)


if __name__ == "__main__":
    unittest.main()
