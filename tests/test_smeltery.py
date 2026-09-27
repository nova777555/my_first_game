"""
Тесты плавильни (game/engine.py: smeltery_* / load_melt / cancel_melt_slot /
skim_smeltery / alloy_smeltery / pour_cast / _process_smelteries). Работают на
самодостаточной тестовой фикстуре (tests/fixtures.py: smelt_pit/smelt_forge,
MELT_RECIPES/ALLOY_RECIPES/CAST_RECIPES), не на реальном game/content.json.

Запуск: python -m unittest tests.test_smeltery -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fixtures  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402


class FixtureTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def forge(self, **resources):
        return GameState(buildings={"smelt_forge": 1}, resources=dict(resources))


class CapacityTests(FixtureTestCase):
    def test_zero_without_building_or_when_broken(self):
        s = GameState()
        self.assertEqual(engine.smeltery_slot_capacity(s, "smelt_forge"), 0)
        self.assertEqual(engine.smeltery_tank_capacity(s, "smelt_forge"), 0)
        s = GameState(buildings={"smelt_forge": 1}, broken={"smelt_forge": 1})
        self.assertEqual(engine.smeltery_slot_capacity(s, "smelt_forge"), 0)

    def test_slots_and_tank_scale_with_working_copies(self):
        s = GameState(buildings={"smelt_forge": 2})
        self.assertEqual(engine.smeltery_slot_capacity(s, "smelt_forge"), 4)
        self.assertEqual(engine.smeltery_tank_capacity(s, "smelt_forge"), 12)
        self.assertEqual(engine.smeltery_heat(s, "smelt_forge"), 2)  # жар от копий не зависит

    def test_upgrades_add_absolute_bonus(self):
        s = self.forge(raw_a=10)
        for upgrade in ("forge_capacity", "forge_slots", "forge_heat"):
            self.assertTrue(engine.purchase_building_upgrade(s, "smelt_forge", upgrade).ok)
        self.assertEqual(engine.smeltery_tank_capacity(s, "smelt_forge"), 10)
        self.assertEqual(engine.smeltery_slot_capacity(s, "smelt_forge"), 3)
        self.assertEqual(engine.smeltery_heat(s, "smelt_forge"), 3)


class LoadMeltTests(FixtureTestCase):
    def test_load_pays_input_and_fills_first_free_slot(self):
        s = self.forge(smelt_ore=5, smelt_fuel=2)
        result = engine.load_melt(s, "smelt_forge", "melt_a_hot")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources, {"smelt_ore": 3, "smelt_fuel": 1})
        slot = s.smelteries["smelt_forge"]["slots"][0]
        self.assertEqual((slot["recipe_id"], slot["turns_done"]), ("melt_a_hot", 0))

    def test_missing_resources_refuses_without_paying(self):
        s = self.forge(smelt_ore=1, smelt_fuel=5)
        result = engine.load_melt(s, "smelt_forge", "melt_a_hot")
        self.assertFalse(result.ok)
        self.assertIn("Плавильная руда 1", result.message)
        self.assertEqual(s.resources, {"smelt_ore": 1, "smelt_fuel": 5})

    def test_heat_too_low(self):
        s = GameState(buildings={"smelt_pit": 1}, resources={"smelt_ore": 5, "smelt_fuel": 5})
        result = engine.load_melt(s, "smelt_pit", "melt_a_hot")
        self.assertFalse(result.ok)
        self.assertIn("жар", result.message)
        self.assertTrue(engine.load_melt(s, "smelt_pit", "melt_a_cold").ok)

    def test_tech_lock(self):
        s = self.forge(smelt_ore=5)
        self.assertFalse(engine.load_melt(s, "smelt_forge", "melt_locked").ok)
        s.techs.add("tech1")
        self.assertTrue(engine.load_melt(s, "smelt_forge", "melt_locked").ok)

    def test_no_free_slot(self):
        s = self.forge(smelt_ore_b=5)
        self.assertTrue(engine.load_melt(s, "smelt_forge", "melt_b").ok)
        self.assertTrue(engine.load_melt(s, "smelt_forge", "melt_b").ok)
        result = engine.load_melt(s, "smelt_forge", "melt_b")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["smelt_ore_b"], 3)

    def test_broken_smeltery_refuses(self):
        s = self.forge(smelt_ore_b=5)
        s.broken["smelt_forge"] = 1
        self.assertFalse(engine.load_melt(s, "smelt_forge", "melt_b").ok)

    def test_best_recipe_prefers_hotter_available(self):
        s = self.forge()
        self.assertEqual(engine.best_melt_recipe_for(s, "smelt_forge", "smelt_ore"), "melt_a_hot")
        pit = GameState(buildings={"smelt_pit": 1})
        self.assertEqual(engine.best_melt_recipe_for(pit, "smelt_pit", "smelt_ore"), "melt_a_cold")
        # топливо - не главное сырьё: клик по нему рецепт не выбирает
        self.assertIsNone(engine.best_melt_recipe_for(s, "smelt_forge", "smelt_fuel"))


class ProcessTests(FixtureTestCase):
    def test_melts_over_turns_and_flows_into_tank(self):
        s = GameState(buildings={"smelt_pit": 1}, resources={"smelt_ore": 2, "smelt_fuel": 1})
        engine.load_melt(s, "smelt_pit", "melt_a_cold")  # 2 хода
        report = engine.advance_turn(s)
        self.assertEqual(s.smelteries["smelt_pit"]["slots"][0]["turns_done"], 1)
        self.assertEqual(report.smeltery_events, [])
        report = engine.advance_turn(s)
        self.assertEqual(s.smelteries["smelt_pit"]["slots"][0], None)
        self.assertEqual(s.smelteries["smelt_pit"]["tank"], {"molten_a": 1, "smelt_slag": 1})
        self.assertEqual([e["kind"] for e in report.smeltery_events], ["melted"])

    def test_waits_for_tank_space_and_flows_after_skim(self):
        s = GameState(buildings={"smelt_pit": 1}, resources={"smelt_ore": 2, "smelt_fuel": 1})
        s.smelteries["smelt_pit"] = {"slots": [], "tank": {"molten_a": 2, "smelt_slag": 1}, "cast": None}
        engine.load_melt(s, "smelt_pit", "melt_a_cold")
        engine.advance_turn(s)
        report = engine.advance_turn(s)  # доплавилось, но 3 + 2 > 4
        slot = s.smelteries["smelt_pit"]["slots"][0]
        self.assertTrue(slot["waiting"])
        self.assertEqual([e["kind"] for e in report.smeltery_events], ["waiting"])
        self.assertEqual(engine.advance_turn(s).smeltery_events, [])  # "ждёт" сообщается один раз
        self.assertTrue(engine.skim_smeltery(s, "smelt_pit").ok)
        self.assertEqual(s.resources["smelt_slag"], 1)
        engine.advance_turn(s)
        self.assertEqual(s.smelteries["smelt_pit"]["tank"], {"molten_a": 3, "smelt_slag": 1})

    def test_broken_smeltery_pauses(self):
        s = self.forge(smelt_ore_b=1)
        engine.load_melt(s, "smelt_forge", "melt_b")
        s.broken["smelt_forge"] = 1
        engine.advance_turn(s)
        self.assertEqual(s.smelteries["smelt_forge"]["slots"][0]["turns_done"], 0)

    def test_cancel_slot_refunds_and_skim_keeps_metal(self):
        s = self.forge(smelt_ore=2, smelt_fuel=1)
        engine.load_melt(s, "smelt_forge", "melt_a_hot")
        self.assertTrue(engine.cancel_melt_slot(s, "smelt_forge", 0).ok)
        self.assertEqual(s.resources, {"smelt_ore": 2, "smelt_fuel": 1})
        self.assertFalse(engine.cancel_melt_slot(s, "smelt_forge", 0).ok)
        s.smelteries["smelt_forge"]["tank"] = {"molten_a": 2}
        self.assertFalse(engine.skim_smeltery(s, "smelt_forge").ok)
        self.assertEqual(s.smelteries["smelt_forge"]["tank"], {"molten_a": 2})


class AlloyTests(FixtureTestCase):
    def test_alloy_turns_metals_into_alloy(self):
        s = self.forge()
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 3, "molten_b": 1}, "cast": None}
        result = engine.alloy_smeltery(s, "smelt_forge", "alloy_ab")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.smelteries["smelt_forge"]["tank"], {"molten_a": 1, "molten_ab": 3})

    def test_missing_metal(self):
        s = self.forge()
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 1, "molten_b": 1}, "cast": None}
        result = engine.alloy_smeltery(s, "smelt_forge", "alloy_ab")
        self.assertFalse(result.ok)
        self.assertIn("Жидкий металл A 1", result.message)

    def test_result_must_fit_the_tank(self):
        s = self.forge()
        # alloy_hidden: 1 + 1 -> 2 - объём тот же, влезает даже в полный тигель (6/6)
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 1, "molten_b": 1, "smelt_slag": 4}, "cast": None}
        self.assertTrue(engine.alloy_smeltery(s, "smelt_forge", "alloy_hidden").ok)
        # 2 + 1 -> 4 в полном тигле - на 1 больше, чем влезает
        s.smelteries["smelt_forge"]["tank"] = {"molten_a": 2, "molten_b": 1, "smelt_slag": 3}
        with mock.patch.dict(engine.ALLOY_RECIPES["alloy_ab"], {"output": {"molten_ab": 4}}):
            result = engine.alloy_smeltery(s, "smelt_forge", "alloy_ab")
        self.assertFalse(result.ok)
        self.assertIn("не влезет", result.message)

    def test_hidden_alloy_is_discovered_by_trying(self):
        s = self.forge()
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 1, "molten_b": 1}, "cast": None}
        result = engine.alloy_smeltery(s, "smelt_forge", "alloy_hidden")
        self.assertIn("Открыт новый сплав", result.message)
        self.assertEqual(s.discovered_alloys, {"alloy_hidden"})

    def test_pit_has_no_heat_for_alloy(self):
        s = GameState(buildings={"smelt_pit": 1})
        s.smelteries["smelt_pit"] = {"slots": [], "tank": {"molten_a": 2, "molten_b": 1}, "cast": None}
        self.assertFalse(engine.alloy_smeltery(s, "smelt_pit", "alloy_ab").ok)


class CastTests(FixtureTestCase):
    def test_form_needed_but_not_consumed(self):
        s = self.forge()
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 3}, "cast": None}
        self.assertFalse(engine.pour_cast(s, "smelt_forge", "cast_a").ok)  # нет формы
        s.resources["cast_form"] = 1
        result = engine.pour_cast(s, "smelt_forge", "cast_a")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["cast_form"], 1)
        self.assertEqual(s.smelteries["smelt_forge"]["tank"], {"molten_a": 1})
        self.assertFalse(engine.pour_cast(s, "smelt_forge", "cast_a").ok)  # стол занят
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("cast_product", 0), 0)
        report = engine.advance_turn(s)
        self.assertEqual(s.resources["cast_product"], 1)
        self.assertIsNone(s.smelteries["smelt_forge"]["cast"])
        self.assertEqual([e["kind"] for e in report.smeltery_events], ["cast_done"])

    def test_one_turn_cast_is_instant(self):
        s = self.forge(cast_form=1)
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_ab": 1}, "cast": None}
        self.assertTrue(engine.pour_cast(s, "smelt_forge", "cast_ab_instant").ok)
        self.assertEqual(s.resources["cast_product"], 2)
        self.assertEqual(s.smelteries["smelt_forge"]["tank"], {})

    def test_not_enough_metal(self):
        s = self.forge(cast_form=1)
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 1}, "cast": None}
        result = engine.pour_cast(s, "smelt_forge", "cast_a")
        self.assertFalse(result.ok)
        self.assertEqual(s.smelteries["smelt_forge"]["tank"], {"molten_a": 1})


class LifecycleTests(FixtureTestCase):
    def test_state_round_trip(self):
        s = self.forge(smelt_ore_b=1)
        engine.load_melt(s, "smelt_forge", "melt_b")
        s.smelteries["smelt_forge"]["tank"] = {"molten_a": 2}
        s.smelteries["smelt_forge"]["cast"] = {"recipe_id": "cast_a", "turns_done": 1, "turns_total": 2}
        s.discovered_alloys.add("alloy_hidden")
        restored = GameState.from_dict(s.to_dict())
        self.assertEqual(restored.smelteries, s.smelteries)
        self.assertEqual(restored.discovered_alloys, {"alloy_hidden"})

    def test_drop_removed_content(self):
        s = self.forge()
        s.smelteries["smelt_forge"] = {
            "slots": [{"recipe_id": "gone", "turns_done": 0, "cost": {"smelt_ore": 2}}],
            "tank": {"molten_a": 1, "gone_metal": 3},
            "cast": {"recipe_id": "gone_cast", "turns_done": 0, "turns_total": 2},
        }
        s.smelteries["not_a_smeltery"] = {"slots": [], "tank": {}, "cast": None}
        s.discovered_alloys = {"alloy_hidden", "gone_alloy"}
        engine.drop_removed_content(s)
        self.assertEqual(s.smelteries, {"smelt_forge": {"slots": [None], "tank": {"molten_a": 1}, "cast": None}})
        self.assertEqual(s.resources["smelt_ore"], 2)
        self.assertEqual(s.discovered_alloys, {"alloy_hidden"})

    def test_pin_smeltery_recipes(self):
        s = GameState()
        for key in ("melt_recipe:melt_b", "alloy_recipe:alloy_ab", "cast_recipe:cast_a"):
            self.assertTrue(engine.toggle_pinned_recipe(s, key).ok)
        self.assertFalse(engine.toggle_pinned_recipe(s, "melt_recipe:gone").ok)
        self.assertTrue(engine.toggle_pinned_recipe(s, "cast_recipe:cast_a").ok)  # открепить
        self.assertEqual(s.pinned_recipes, ["melt_recipe:melt_b", "alloy_recipe:alloy_ab"])


class ValidationTests(unittest.TestCase):
    def errors(self, mutate=None):
        import content_validation as cv
        data = fixtures.as_content_dict()
        if mutate:
            mutate(data)
        return cv.validate(data)

    def test_fixture_is_valid(self):
        self.assertEqual(self.errors(), [])

    def test_bad_smeltery_field(self):
        errors = self.errors(lambda d: d["buildings"]["smelt_pit"].update(smeltery={"slots": 0, "capacity": 4, "heat": 1}))
        self.assertTrue(any("smeltery.slots" in e for e in errors), errors)

    def test_smeltery_upgrade_on_non_smeltery(self):
        def mutate(d):
            d["buildings"]["gen"].setdefault("upgrades", []).append(
                {"id": "gen_heat", "name": "x", "description": "x", "type": "smeltery_heat", "value": 1, "cost": {}})
        self.assertTrue(any("только у плавилен" in e for e in self.errors(mutate)))

    def test_molten_in_melt_input(self):
        errors = self.errors(lambda d: d["melt_recipes"]["melt_b"].update(input={"molten_a": 1}))
        self.assertTrue(any("жидкий металл, он бывает только в тигле" in e for e in errors), errors)

    def test_alloy_needs_molten_metals(self):
        errors = self.errors(lambda d: d["alloy_recipes"]["alloy_ab"].update(output={"cast_product": 1}))
        self.assertTrue(any("не жидкий металл" in e for e in errors), errors)

    def test_cast_metal_and_duplicate_pair(self):
        errors = self.errors(lambda d: d["cast_recipes"]["cast_a"].update(metal="smelt_ore"))
        self.assertTrue(any("cast_a" in e and "не жидкий металл" in e for e in errors), errors)
        errors = self.errors(lambda d: d["cast_recipes"]["cast_ab_instant"].update(metal="molten_a"))
        self.assertTrue(any("уже есть в рецепте" in e for e in errors), errors)

    def test_molten_color_required(self):
        def mutate(d):
            next(r for r in d["resources"] if r["id"] == "molten_a")["molten"] = {"color": "red"}
        self.assertTrue(any("molten.color" in e for e in self.errors(mutate)))


class WebUiViewTests(unittest.TestCase):
    def setUp(self):
        import web_ui
        self.web_ui = web_ui
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def test_smelteries_view_lists_recipes_for_its_heat(self):
        s = GameState(buildings={"smelt_pit": 1, "smelt_forge": 1}, resources={"smelt_ore": 4, "smelt_fuel": 2})
        engine.load_melt(s, "smelt_pit", "melt_a_cold")
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"smelt_slag": 1, "molten_b": 1, "molten_a": 2}, "cast": None}
        view = {v["building_id"]: v for v in self.web_ui.smelteries_view(s)}
        pit, forge = view["smelt_pit"], view["smelt_forge"]
        self.assertEqual([r["id"] for r in pit["melt_recipes"]], ["melt_a_cold"])
        self.assertEqual(pit["alloy_recipes"], [])
        self.assertEqual(pit["slots"][0]["main_id"], "smelt_ore")
        # слои снизу вверх: плотный металл, лёгкий металл, шлак сверху
        self.assertEqual([l["id"] for l in forge["tank"]], ["molten_a", "molten_b", "smelt_slag"])
        self.assertEqual(forge["dross"], 1)
        alloy = next(a for a in forge["alloy_recipes"] if a["id"] == "alloy_ab")
        self.assertIsNone(alloy["reason"])
        self.assertTrue(next(a for a in forge["alloy_recipes"] if a["id"] == "alloy_hidden")["hidden"])
        bank = {b["id"]: b for b in forge["bank"]}
        self.assertEqual(bank["smelt_ore"]["melt_recipe"], "melt_a_hot")
        self.assertIsNone(bank["smelt_fuel"]["melt_recipe"])

    def test_run_turn_load_and_log_event(self):
        s = GameState(buildings={"smelt_forge": 1}, resources={"smelt_ore_b": 1})
        log = self.web_ui.run_turn(s, "smelt_load", "smelt_forge:melt_b")
        self.assertTrue(log["ok"], log["message"])
        self.assertEqual(s.smelteries["smelt_forge"]["tank"], {"molten_b": 1})  # 1 ход - стекло в ход загрузки
        self.assertEqual([e["kind"] for e in log["smeltery"]], ["melted"])
        self.assertEqual(s.turn, 2)

    def test_sources_cover_melt_alloy_and_cast(self):
        kinds = {src["kind"] for src in self.web_ui.resource_sources("molten_a")}
        self.assertEqual(kinds, {"smelt"})
        alloy = self.web_ui.resource_sources("molten_ab")[0]
        self.assertEqual((alloy["kind"], alloy["action_id"]), ("alloy", "alloy_recipe:alloy_ab"))
        cast = next(s for s in self.web_ui.resource_sources("cast_product") if s["recipe_id"] == "cast_a")
        self.assertEqual(cast["form"]["id"], "cast_form")
        self.assertEqual(cast["cost"][0]["id"], "molten_a")
        slag = self.web_ui.resource_sources("smelt_slag")
        self.assertEqual({s["recipe_id"] for s in slag}, {"melt_a_cold", "melt_a_hot"})

    def test_pinned_view(self):
        s = GameState(buildings={"smelt_forge": 1}, resources={"smelt_ore_b": 1})
        engine.toggle_pinned_recipe(s, "melt_recipe:melt_b")
        pinned = self.web_ui.pinned_recipes_view(s, [], self.web_ui.smelteries_view(s))
        self.assertEqual(len(pinned), 1)
        self.assertEqual((pinned[0]["gain"][0]["id"], pinned[0]["enough"]), ("molten_b", True))
        self.assertEqual(pinned[0]["location_name"], "Горн (тест)")


class SmelteryHttpTests(unittest.TestCase):
    PORT = 8813

    @classmethod
    def setUpClass(cls):
        import tempfile
        import threading
        from http.server import HTTPServer
        import web_ui
        cls.web_ui = web_ui
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._orig = (web_ui.SAVE_PATH, web_ui.SAVES_DIR)
        web_ui.SAVE_PATH = Path(cls._tmpdir.name) / "save.json"
        web_ui.SAVES_DIR = Path(cls._tmpdir.name) / "saves"
        cls._content_patch = fixtures.patched_content()
        cls._content_patch.__enter__()
        cls.server = HTTPServer(("127.0.0.1", cls.PORT), web_ui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._content_patch.__exit__(None, None, None)
        cls.web_ui.SAVE_PATH, cls.web_ui.SAVES_DIR = cls._orig
        cls._tmpdir.cleanup()

    def _post(self, path, payload):
        import json
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_skim_cancel_and_pour_endpoints(self):
        s = GameState(buildings={"smelt_forge": 1}, resources={"smelt_ore": 2, "smelt_fuel": 1, "cast_form": 1})
        s.smelteries["smelt_forge"] = {"slots": [], "tank": {"molten_a": 2, "smelt_slag": 2}, "cast": None}
        self.web_ui.STATE = s
        resp = self._post("/api/smelt_skim", {"building_id": "smelt_forge"})
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(s.resources["smelt_slag"], 2)
        self.assertEqual(s.turn, 1)  # бесплатно
        engine.load_melt(s, "smelt_forge", "melt_a_hot")
        resp = self._post("/api/smelt_cancel_slot", {"building_id": "smelt_forge", "index": 0})
        self.assertTrue(resp["ok"], resp["message"])
        self.assertEqual(s.resources["smelt_ore"], 2)
        resp = self._post("/api/action", {"type": "smelt_pour", "id": "smelt_forge:cast_a"})
        self.assertTrue(resp["log"]["ok"], resp["log"]["message"])
        self.assertEqual(resp["state"]["smelteries"][0]["cast"]["recipe_id"], "cast_a")
        self.assertEqual(s.turn, 2)


if __name__ == "__main__":
    unittest.main()
