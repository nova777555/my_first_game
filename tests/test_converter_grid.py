"""
Тесты сетки преобразователя (game/engine.py: energy_recipe_pattern/
grid_energy_recipes_at/match_grid_target/grid_recipe_key/parse_grid_recipe_key,
а также toggle_pinned_recipe для рецептов крафта за энергию) - рецепт крафта
за энергию собирается раскладкой своего consumes в слотах преобразователя
(locations.converter.crafting), как рецепт печи.

Контент - фикстура tests/fixtures.py, дополненная прямо в тесте (patch.dict):
сетка 2x1 у локации "converter" и энергорецепты с узором.

Запуск: python -m unittest tests.test_converter_grid -v
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

GRID_ENERGY_RECIPES = {
    "energy_single": {
        "name": "Энергорецепт на одну ячейку", "description": "Тест.", "requires_tech": None,
        "threshold": 10, "consumes": {"raw_a": 1}, "produces": {"raw_b": 1},
        "pattern": {"rows": ["A"], "key": {"A": "raw_a"}},
    },
    "energy_pair": {
        "name": "Энергорецепт на пару", "description": "Тест.", "requires_tech": None,
        "threshold": 10, "consumes": {"raw_a": 1, "raw_b": 1}, "produces": {"catalyst": 1},
        "pattern": {"rows": ["AB"], "key": {"A": "raw_a", "B": "raw_b"}}, "pattern_mirror": True,
    },
    "energy_locked": {
        "name": "Закрытый энергорецепт", "description": "Тест.", "requires_tech": "tech1",
        "threshold": 10, "consumes": {"raw_b": 2}, "produces": {"catalyst": 1},
        "pattern": {"rows": ["AA"], "key": {"A": "raw_b"}},
    },
}


class ConverterGridTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        converter = {**engine.LOCATIONS[engine.CONVERTER_LOCATION_ID], "crafting": {"width": 2, "height": 1}}
        for patcher in (
            mock.patch.dict(engine.LOCATIONS, {engine.CONVERTER_LOCATION_ID: converter}),
            mock.patch.dict(engine.ENERGY_RECIPES, GRID_ENERGY_RECIPES),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state = GameState()


class GridEnergyRecipesAtTests(ConverterGridTestCase):
    def test_lists_only_unlocked_recipes_with_pattern(self):
        self.assertEqual(
            engine.grid_energy_recipes_at(engine.CONVERTER_LOCATION_ID, self.state),
            ["energy_single", "energy_pair"],
        )

    def test_tech_opens_locked_recipe(self):
        self.state.techs.add("tech1")
        self.assertIn("energy_locked", engine.grid_energy_recipes_at(engine.CONVERTER_LOCATION_ID, self.state))

    def test_other_locations_have_no_energy_recipes(self):
        self.assertEqual(engine.grid_energy_recipes_at("grid_loc", self.state), [])

    def test_converter_without_grid_has_no_grid_recipes(self):
        plain = {k: v for k, v in engine.LOCATIONS[engine.CONVERTER_LOCATION_ID].items() if k != "crafting"}
        with mock.patch.dict(engine.LOCATIONS, {engine.CONVERTER_LOCATION_ID: plain}):
            self.assertEqual(engine.grid_energy_recipes_at(engine.CONVERTER_LOCATION_ID, self.state), [])


class MatchGridTargetTests(ConverterGridTestCase):
    def test_single_cell_recipe_matches_in_either_slot(self):
        for placed in (["raw_a", None], [None, "raw_a"]):
            self.assertEqual(
                engine.match_grid_target(self.state, engine.CONVERTER_LOCATION_ID, placed),
                ("energy_recipe", "energy_single"),
            )

    def test_mirrored_pair_matches(self):
        for placed in (["raw_a", "raw_b"], ["raw_b", "raw_a"]):
            self.assertEqual(
                engine.match_grid_target(self.state, engine.CONVERTER_LOCATION_ID, placed),
                ("energy_recipe", "energy_pair"),
            )

    def test_locked_recipe_does_not_match_until_researched(self):
        placed = ["raw_b", "raw_b"]
        self.assertIsNone(engine.match_grid_target(self.state, engine.CONVERTER_LOCATION_ID, placed))
        self.state.techs.add("tech1")
        self.assertEqual(
            engine.match_grid_target(self.state, engine.CONVERTER_LOCATION_ID, placed),
            ("energy_recipe", "energy_locked"),
        )

    def test_empty_grid_matches_nothing(self):
        self.assertIsNone(engine.match_grid_target(self.state, engine.CONVERTER_LOCATION_ID, [None, None]))

    def test_manual_grid_action_reports_its_kind(self):
        placed = ["raw_a", "raw_a", None, None]
        self.assertEqual(engine.match_grid_target(self.state, "grid_loc", placed), ("manual_action", "grid_line"))

    def test_match_grid_ignores_energy_recipes(self):
        self.assertIsNone(engine.match_grid(self.state, engine.CONVERTER_LOCATION_ID, ["raw_a", None]))


class GridRecipeKeyTests(unittest.TestCase):
    def test_manual_action_key_is_plain_id(self):
        self.assertEqual(engine.grid_recipe_key("manual_action", "chop"), "chop")
        self.assertEqual(engine.parse_grid_recipe_key("chop"), ("manual_action", "chop"))

    def test_energy_recipe_key_round_trips(self):
        key = engine.grid_recipe_key("energy_recipe", "make_raw_b")
        self.assertEqual(key, "energy_recipe:make_raw_b")
        self.assertEqual(engine.parse_grid_recipe_key(key), ("energy_recipe", "make_raw_b"))


class PinEnergyRecipeTests(ConverterGridTestCase):
    def test_pin_and_unpin_energy_recipe(self):
        key = engine.grid_recipe_key("energy_recipe", "energy_pair")
        self.assertTrue(engine.toggle_pinned_recipe(self.state, key).ok)
        self.assertEqual(self.state.pinned_recipes, [key])
        self.assertTrue(engine.toggle_pinned_recipe(self.state, key).ok)
        self.assertEqual(self.state.pinned_recipes, [])

    def test_energy_recipe_without_pattern_cannot_be_pinned(self):
        result = engine.toggle_pinned_recipe(self.state, engine.grid_recipe_key("energy_recipe", "make_raw_b"))
        self.assertFalse(result.ok)
        self.assertEqual(self.state.pinned_recipes, [])

    def test_unknown_energy_recipe_cannot_be_pinned(self):
        self.assertFalse(engine.toggle_pinned_recipe(self.state, "energy_recipe:nope").ok)

    def test_bare_energy_recipe_id_is_not_a_manual_grid_action(self):
        self.assertFalse(engine.toggle_pinned_recipe(self.state, "energy_pair").ok)


class WebUiConverterGridTests(unittest.TestCase):
    """tools/web_ui.py поверх той же сетки: crafting_view/craft_grid_view/
    converter_view/resource_sources/run_turn."""

    def setUp(self):
        import web_ui
        self.web_ui = web_ui
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        converter = {**engine.LOCATIONS[engine.CONVERTER_LOCATION_ID], "crafting": {"width": 2, "height": 1}}
        for patcher in (
            mock.patch.dict(engine.LOCATIONS, {engine.CONVERTER_LOCATION_ID: converter}),
            mock.patch.dict(engine.ENERGY_RECIPES, GRID_ENERGY_RECIPES),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state = GameState(resources={"raw_a": 3, "raw_b": 3, "energy": 50}, buildings={"energy_converter": 1})

    def converter_entry(self):
        entries = self.web_ui.crafting_view(self.state)
        return next(e for e in entries if e["location_id"] == engine.CONVERTER_LOCATION_ID)

    def test_converter_entry_lists_energy_recipes_by_key(self):
        entry = self.converter_entry()
        recipes = {r["action_id"]: r for r in entry["recipes"]}
        self.assertEqual(set(recipes), {"energy_recipe:energy_single", "energy_recipe:energy_pair"})
        single = recipes["energy_recipe:energy_single"]
        self.assertEqual(single["kind"], "energy_recipe")
        self.assertEqual(single["threshold"], 10)
        self.assertEqual([c["id"] for c in single["cost"]], ["raw_a"])
        self.assertEqual(single["energy_per_turn"], 4)
        self.assertEqual(single["eta_turns"], 3)  # 10 энергии по 4 за ход
        self.assertTrue(single["available"])
        self.assertEqual(entry["converter_slots"], {"busy": 0, "capacity": 1})

    def test_eta_is_not_faster_than_the_energy_inflow(self):
        # Регрессия: срок считался только по потолку преобразователя (4/ход -> 3 хода),
        # хотя в сети пусто и притекает 2/ход - на деле 5 ходов.
        self.state.resources["energy"] = 0
        self.state.buildings["gen"] = 1
        recipes = {r["action_id"]: r for r in self.converter_entry()["recipes"]}
        self.assertEqual(recipes["energy_recipe:energy_single"]["eta_turns"], 5)
        self.state.energy_jobs.append({"recipe_id": "energy_single", "progress": 6, "building_id": "energy_converter"})
        self.assertEqual(self.converter_entry()["job"]["eta_turns"], 2)  # осталось 4 при притоке 2

    def test_recipe_unavailable_when_every_converter_is_busy(self):
        self.state.energy_jobs.append({"recipe_id": "make_raw_b", "progress": 0, "building_id": "energy_converter"})
        entry = self.converter_entry()
        self.assertFalse(all(r["available"] for r in entry["recipes"]))
        self.assertEqual(entry["job"]["kind"], "energy_recipe")
        self.assertEqual(entry["job"]["action_id"], "energy_recipe:make_raw_b")
        self.assertEqual(entry["job"]["index"], 0)

    def test_craft_grid_starts_energy_recipe(self):
        result = self.web_ui.craft_grid_view(self.state, engine.CONVERTER_LOCATION_ID, ["raw_a", "raw_b"], 5)
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(result["action_id"], "energy_recipe:energy_pair")
        self.assertEqual(result["times_requested"], 1)  # пачкой не бывает
        self.assertEqual(self.state.resources["raw_a"], 2)
        self.assertEqual(self.state.resources["raw_b"], 2)
        self.assertEqual([j["recipe_id"] for j in self.state.energy_jobs], ["energy_pair"])

    def test_craft_grid_refuses_when_converter_busy(self):
        self.state.energy_jobs.append({"recipe_id": "make_raw_b", "progress": 0, "building_id": "energy_converter"})
        result = self.web_ui.craft_grid_view(self.state, engine.CONVERTER_LOCATION_ID, ["raw_a", None], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(self.state.resources["raw_a"], 3)

    def test_grid_recipes_are_not_converter_cards(self):
        ids = {item["id"] for item in self.web_ui.converter_view(self.state)}
        self.assertIn("make_raw_b", ids)
        self.assertNotIn("energy_single", ids)
        self.assertNotIn("energy_pair", ids)

    def test_sources_point_to_converter_grid(self):
        sources = self.web_ui.resource_sources("raw_b")
        grid = next(s for s in sources if s.get("action_id") == "energy_recipe:energy_single")
        self.assertEqual(grid["kind"], "grid_craft")
        self.assertEqual(grid["location_id"], engine.CONVERTER_LOCATION_ID)
        self.assertEqual(grid["threshold"], 10)
        plain = next(s for s in sources if s.get("action_id") == "make_raw_b")
        self.assertEqual(plain["kind"], "energy")

    def test_turn_log_reports_completed_energy_keys(self):
        self.state.energy_jobs.append({"recipe_id": "energy_single", "progress": 9, "building_id": "energy_converter"})
        log = self.web_ui.run_turn(self.state, "skip", None)
        self.assertEqual(log["energy_completed_keys"], ["energy_recipe:energy_single"])


if __name__ == "__main__":
    unittest.main()
