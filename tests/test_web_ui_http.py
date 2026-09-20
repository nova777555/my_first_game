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
            {"yard", "shaft", "workshop_loc", "mechanisms", "sub_workshop", "converter", "hunting_loc"},
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

    def test_build_and_manual_items_expose_click_delay_field(self):
        status, state = self._get("/api/state")
        gen = next(b for b in state["categories"]["build"] if b["id"] == "gen")
        self.assertIn("click_delay_ms", gen)
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

    def test_build_catalog_and_owned_buildings_expose_description(self):
        status, state = self._get("/api/state")
        self.assertEqual(status, 200)
        gen_catalog = next(b for b in state["categories"]["build"] if b["id"] == "gen")
        self.assertEqual(gen_catalog["description"], fixtures.BUILDINGS["gen"]["description"])

        web_ui.STATE.resources["raw_b"] = 5
        self._post("/api/action", {"type": "build", "id": "gen"})
        status, state = self._get("/api/state")
        gen_owned = next(b for b in state["buildings"] if b["id"] == "gen")
        self.assertEqual(gen_owned["description"], fixtures.BUILDINGS["gen"]["description"])

    def test_building_without_upgrades_exposes_null_upgrades_field(self):
        web_ui.STATE.resources["raw_b"] = 5
        self._post("/api/action", {"type": "build", "id": "gen"})
        status, state = self._get("/api/state")
        gen_catalog = next(b for b in state["categories"]["build"] if b["id"] == "gen")
        self.assertIsNone(gen_catalog["upgrades"])

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
        mech = next(b for b in state["categories"]["build"] if b["id"] == "mechanism_a")
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
        mech = next(b for b in state["categories"]["build"] if b["id"] == "mechanism_a")
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

    def test_autocraft_preview_reports_turns_when_docraft_spans_several_turns(self):
        # trickle_hub: скорость автокрафта 1(база)+2=3/ход. assemble_gadget просит
        # slow_mid:10 - craft_slow_mid делает по 1 за прокрутку, 10 прокруток не
        # влезают в один ход, значит растянется на ceil(10/3)=4 хода - и summary,
        # и узел дерева обязаны явно показать эту оценку.
        web_ui.STATE.resources["slow_raw"] = 1000
        web_ui.STATE.buildings["trickle_hub"] = 1
        status, resp = self._post("/api/autocraft_preview", {"type": "manual", "id": "assemble_gadget"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["needs_confirm"])
        summary_row = next(row for row in resp["summary"] if row["resource_id"] == "slow_mid")
        self.assertEqual(summary_row["turns"], 4)
        tree_node = next(n for n in resp["tree"] if n["resource_id"] == "slow_mid")
        self.assertEqual(tree_node["turns"], 4)

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
        factory = next(b for b in state["categories"]["build"] if b["id"] == "upgradable_factory")
        ids_before = [u["id"] for u in factory["upgrades"]["upgrades"]]
        self.assertNotIn("uf_scale_more", ids_before)  # ещё не на очереди - не показывается
        self.assertIn("uf_scale", ids_before)  # первый в прогрессии - доступен сразу

        status, resp = self._post("/api/action", {"type": "building_upgrade", "id": "upgradable_factory:uf_scale_more"})
        self.assertFalse(resp["log"]["ok"])  # сервер тоже отказывает, не только подсказка в UI

        self._post("/api/action", {"type": "building_upgrade", "id": "upgradable_factory:uf_scale"})
        status, resp = self._post("/api/action", {"type": "building_upgrade", "id": "upgradable_factory:uf_scale_more"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])

        status, state = self._get("/api/state")
        factory = next(b for b in state["categories"]["build"] if b["id"] == "upgradable_factory")
        ids = [u["id"] for u in factory["upgrades"]["upgrades"]]
        self.assertNotIn("uf_scale", ids)  # перекрытый более слабый уровень убран из списка
        self.assertIn("uf_scale_more", ids)

    def test_build_catalog_tracks_multi_stage_progress_and_completes_last_stage(self):
        web_ui.STATE.resources.update({"raw_a": 100, "raw_b": 100})

        status, state = self._get("/api/state")
        furnace = next(b for b in state["categories"]["build"] if b["id"] == "furnace")
        self.assertEqual(furnace["stage_number"], 1)
        self.assertEqual(furnace["stage_total"], 3)
        self.assertEqual(furnace["stage_description"], "установить генератор")
        self.assertEqual({c["id"]: c["amount"] for c in furnace["cost"]}, {"raw_a": 2})

        status, resp = self._post("/api/action", {"type": "build", "id": "furnace"})
        self.assertTrue(resp["log"]["ok"])
        self.assertNotIn("furnace", [b["id"] for b in resp["state"]["buildings"]])
        furnace2 = next(b for b in resp["state"]["categories"]["build"] if b["id"] == "furnace")
        self.assertEqual(furnace2["stage_number"], 2)
        self.assertEqual(furnace2["stage_description"], "установить радиатор")

        self._post("/api/action", {"type": "build", "id": "furnace"})
        status, resp = self._post("/api/action", {"type": "build", "id": "furnace"})
        self.assertTrue(resp["log"]["ok"])
        self.assertEqual(resp["log"]["message"], "Построено: Печь.")
        self.assertIn("furnace", [b["id"] for b in resp["state"]["buildings"]])
        furnace_final = next(b for b in resp["state"]["categories"]["build"] if b["id"] == "furnace")
        # furnace без max_count - можно строить ещё одну копию, поэтому каталог
        # снова показывает этап 1/3 (для СЛЕДУЮЩЕЙ копии), а не пропадает совсем.
        self.assertEqual(furnace_final["stage_number"], 1)
        self.assertEqual(next(b for b in resp["state"]["buildings"] if b["id"] == "furnace")["count"], 1)

    def test_build_catalog_reports_stage_total_1_for_a_single_stage_list(self):
        # kiln - ровно 1 этап в construction_stages; и bild_view, и actionLabel на
        # фронте должны уметь отличить это от многоэтапной постройки (там stage_total
        # должен позволить фронту НЕ показывать бессмысленный счётчик "1 из 1").
        status, state = self._get("/api/state")
        kiln = next(b for b in state["categories"]["build"] if b["id"] == "kiln")
        self.assertEqual(kiln["stage_number"], 1)
        self.assertEqual(kiln["stage_total"], 1)
        self.assertEqual(kiln["stage_description"], "обжечь кирпичи")

    def test_build_catalog_drops_a_building_once_its_max_count_is_reached(self):
        # slot_hub (max_count=1, без upgrades) - как только построена единственная
        # разрешённая копия, карточка должна пропасть из каталога "Построить"
        # целиком, а не остаться там неактивной кнопкой (прямое пожелание,
        # 2026-09-02) - штучную постройку по-прежнему видно в "buildings".
        web_ui.STATE.resources["raw_a"] = 100
        status, state = self._get("/api/state")
        self.assertIn("slot_hub", [b["id"] for b in state["categories"]["build"]])

        status, resp = self._post("/api/action", {"type": "build", "id": "slot_hub"})
        self.assertTrue(resp["log"]["ok"])
        self.assertNotIn("slot_hub", [b["id"] for b in resp["state"]["categories"]["build"]])
        self.assertEqual(next(b for b in resp["state"]["buildings"] if b["id"] == "slot_hub")["count"], 1)

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

    def test_cancel_autocraft_job_does_not_advance_the_turn(self):
        web_ui.STATE.buildings["trickle_hub"] = 1
        web_ui.STATE.resources["slow_raw"] = 1000
        web_ui.engine.ensure_and_pay(web_ui.STATE, {"slow_mid": 10})
        self.assertIn("slow_mid", web_ui.STATE.autocraft_jobs)
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/cancel_job", {"kind": "autocraft", "resource_id": "slow_mid"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        job_ids = [j["resource_id"] for j in resp["state"]["autocraft"]["jobs"]]
        self.assertNotIn("slow_mid", job_ids)
        self.assertEqual(web_ui.STATE.turn, turn_before)

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
            "turns_done": 1, "turns_total": 5,
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

    def test_purchase_farm_upgrade_does_not_advance_turn_and_is_isolated_per_instance(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "industrial_apiary"})
        self._post("/api/action", {"type": "build", "id": "industrial_apiary"})
        status, state = self._get("/api/state")
        instances = [f["id"] for f in state["farms"] if f["building_id"] == "industrial_apiary"]
        self.assertEqual(len(instances), 2)
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/purchase_farm_upgrade", {"instance_id": instances[0], "upgrade_id": "ia_output"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before)

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

    def test_purchase_soul_upgrade_does_not_advance_turn_and_is_isolated_per_instance(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "soul_synth"})
        self._post("/api/action", {"type": "build", "id": "soul_synth"})
        status, state = self._get("/api/state")
        instances = [e["id"] for e in state["soul_collectors"] if e["building_id"] == "soul_synth"]
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/purchase_soul_upgrade", {"instance_id": instances[0], "upgrade_id": "soul_synth_slot"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before)

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

    def test_purchase_reactor_upgrade_does_not_advance_turn_and_is_isolated_per_instance(self):
        web_ui.STATE.resources["raw_a"] = 100
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        self._post("/api/action", {"type": "build", "id": "test_reactor"})
        status, state = self._get("/api/state")
        instances = [e["id"] for e in state["reactors"] if e["building_id"] == "test_reactor"]
        turn_before = web_ui.STATE.turn

        status, resp = self._post("/api/purchase_reactor_upgrade", {"instance_id": instances[0], "upgrade_id": "test_reactor_zoning_2"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(web_ui.STATE.turn, turn_before)

        entry_1 = next(e for e in resp["state"]["reactors"] if e["id"] == instances[0])
        entry_2 = next(e for e in resp["state"]["reactors"] if e["id"] == instances[1])
        self.assertEqual(len(entry_1["zones"]), 2)
        self.assertEqual(len(entry_2["zones"]), 1)
        purchased_1 = {u["id"] for u in entry_1["upgrades"]["upgrades"] if u["purchased"]}
        purchased_2 = {u["id"] for u in entry_2["upgrades"]["upgrades"] if u["purchased"]}
        self.assertIn("test_reactor_zoning_2", purchased_1)
        self.assertNotIn("test_reactor_zoning_2", purchased_2)

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

        status, resp = self._post("/api/purchase_reactor_upgrade", {"instance_id": instance_id, "upgrade_id": "test_reactor_grid_3x2"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"], resp["message"])

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

    def test_autocraft_toggle_flips_flag_without_advancing_turn(self):
        status, resp = self._get("/api/state")
        self.assertTrue(resp["autocraft_enabled"])  # по умолчанию включён

        status, resp = self._post("/api/autocraft_toggle", {})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["state"]["autocraft_enabled"])
        self.assertEqual(resp["state"]["turn"], 1)  # это настройка, не игровое действие - ход не тратится

        status, resp = self._post("/api/autocraft_toggle", {})
        self.assertTrue(resp["state"]["autocraft_enabled"])

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


if __name__ == "__main__":
    unittest.main()
