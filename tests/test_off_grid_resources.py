"""
Фоновые ресурсы (resources.*.off_grid, например мана): в ячейку крафт-сетки не
кладутся - в цене сеточного рецепта списываются СВЕРХ узора, как энергия (она
фоновая всегда, без флага). Правило - game/engine.py: is_off_grid_resource, копии -
tools/content_validation.py: is_off_grid и content_editor.html: isOffGrid.

Мини-контент добавляется поверх фикстуры в setUp: ресурс "t_mana" и сеточный рецепт
"grid_mana" на верстаке grid_loc (узор - два raw_b столбиком, мана - сверх узора).

Запуск: python -m unittest tests.test_off_grid_resources -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fixtures  # noqa: E402
import content_validation as cv  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402

MANA = "t_mana"
PLACED = ["raw_b", None, "raw_b", None]
GRID_MANA_ACTION = {
    "name": "Тест: сеточный с маной", "requires_tech": None, "location": "grid_loc",
    "cost": {"raw_b": 2, MANA: 3}, "gain": {"grid_output": 1},
    "pattern": {"rows": ["A.", "A."], "key": {"A": "raw_b"}},
}


class OffGridFixtureCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_content()
        patch.__enter__()
        # patched_content на выходе возвращает исходные значения всех имён ниже.
        self.addCleanup(patch.__exit__, None, None, None)
        import web_ui
        self.web_ui = web_ui
        for module in (engine, web_ui):
            module.RESOURCE_NAMES = {**module.RESOURCE_NAMES, MANA: "Тест-мана"}
            module.MANUAL_ACTIONS = {**module.MANUAL_ACTIONS, "grid_mana": GRID_MANA_ACTION}
        web_ui.RESOURCE_ORDER = [*web_ui.RESOURCE_ORDER, MANA]
        engine.RESOURCE_FRACTIONAL = {**engine.RESOURCE_FRACTIONAL, MANA: True}
        engine.RESOURCE_OFF_GRID = frozenset({MANA})


class OffGridEngineTests(OffGridFixtureCase):
    def test_energy_is_always_off_grid_others_by_flag(self):
        self.assertTrue(engine.is_off_grid_resource(engine.ENERGY_RESOURCE_ID))
        self.assertTrue(engine.is_off_grid_resource(MANA))
        self.assertFalse(engine.is_off_grid_resource("raw_a"))

    def test_assembler_stacks_skip_off_grid_resources(self):
        self.assertEqual(engine.stack_recipe_cost({"raw_a": 2, MANA: 3, "energy": 1}), {"raw_a": 2})
        self.assertTrue(engine._stacks_match({"raw_a": 2, MANA: 3}, {"raw_a": 2}))
        self.assertFalse(engine._stacks_match({"raw_a": 2, MANA: 3}, {"raw_a": 2, MANA: 3}))

    def test_pattern_matches_without_mana_in_cells(self):
        self.assertEqual(engine.match_grid(GameState(), "grid_loc", PLACED), "grid_mana")


class OffGridWebUiTests(OffGridFixtureCase):
    def test_craft_spends_mana_beyond_the_pattern(self):
        s = GameState(resources={"raw_b": 2, MANA: 5})
        out = self.web_ui.craft_grid_view(s, "grid_loc", PLACED, 1)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual(s.resources.get(MANA), 2)
        self.assertEqual(s.resources.get("raw_b", 0), 0)
        self.assertEqual(s.resources.get("grid_output"), 1)

    def test_mana_shortage_is_an_ordinary_shortage(self):
        # Не энергия - ждать притока не станет: рецепта у маны нет, крафт отказывает.
        s = GameState(resources={"raw_b": 2, MANA: 1})
        out = self.web_ui.craft_grid_view(s, "grid_loc", PLACED, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(s.resources.get(MANA), 1)
        self.assertEqual(s.resources.get("raw_b"), 2)
        self.assertFalse(s.orders)

    def test_grid_bank_hides_off_grid_resources(self):
        s = GameState(resources={"raw_b": 2, MANA: 5, "energy": 3})
        entry = next(e for e in self.web_ui.crafting_view(s) if e["location_id"] == "grid_loc")
        bank_ids = {b["id"] for b in entry["bank"]}
        self.assertIn("raw_b", bank_ids)
        self.assertNotIn(MANA, bank_ids)
        self.assertNotIn("energy", bank_ids)
        recipe = next(r for r in entry["recipes"] if r["action_id"] == "grid_mana")
        self.assertIn(MANA, {c["id"] for c in recipe["cost"]})

    def test_view_lists_off_grid_resources_for_the_client(self):
        view = self.web_ui.build_view(GameState())
        self.assertIn("energy", view["off_grid_resources"])
        self.assertIn(MANA, view["off_grid_resources"])
        self.assertNotIn("raw_a", view["off_grid_resources"])


class OffGridValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()
        self.mana = {"id": MANA, "name": "Тест-мана", "fractional": True, "off_grid": True}
        self.data["resources"].append(self.mana)
        self.action = self.data["manual_actions"]["grid_line"]

    def test_off_grid_resource_beyond_the_pattern_is_valid(self):
        self.action["cost"] = {**self.action["cost"], MANA: 4.5}
        self.assertEqual(cv.validate(self.data), [])

    def test_off_grid_resource_cannot_be_placed_in_a_cell(self):
        self.action["pattern"]["key"]["A"] = MANA
        self.action["cost"] = {MANA: 2}
        errors = cv.validate(self.data)
        self.assertTrue(any("фоновый ресурс" in e for e in errors), errors)

    def test_without_the_flag_mana_must_be_in_the_pattern(self):
        del self.mana["off_grid"]
        self.action["cost"] = {**self.action["cost"], MANA: 4}
        errors = cv.validate(self.data)
        self.assertTrue(any("обязаны совпадать" in e for e in errors), errors)

    def test_flag_must_be_boolean(self):
        self.mana["off_grid"] = "yes"
        errors = cv.validate(self.data)
        self.assertTrue(any("off_grid должен быть true или false" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
