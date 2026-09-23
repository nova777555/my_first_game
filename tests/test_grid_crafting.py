"""
Тесты сеточного крафта (game/engine.py: location_crafting/action_pattern/
is_grid_action/grid_actions_at/match_grid) - см. PLAN_grid_crafting.md, этап 1.
Работают на самодостаточной тестовой фикстуре (tests/fixtures.py, локация
"grid_loc" 2x2 + действия "grid_*"), а не на реальном content.json.

Запуск: python -m unittest tests.test_grid_crafting -v
"""
import sys
import unittest
from pathlib import Path

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


class LocationCraftingTests(FixtureTestCase):
    def test_grid_location_reports_its_size(self):
        self.assertEqual(engine.location_crafting("grid_loc"), {"width": 2, "height": 2})

    def test_plain_location_has_no_crafting(self):
        self.assertIsNone(engine.location_crafting("yard"))


class ActionPatternTests(FixtureTestCase):
    def test_grid_action_reports_pattern(self):
        self.assertEqual(engine.action_pattern("grid_line"), {"rows": ["AA", ".."], "key": {"A": "raw_a"}})
        self.assertTrue(engine.is_grid_action("grid_line"))

    def test_plain_action_has_no_pattern(self):
        self.assertIsNone(engine.action_pattern("grid_plain_card"))
        self.assertFalse(engine.is_grid_action("grid_plain_card"))


class GridActionsAtTests(FixtureTestCase):
    def test_lists_only_grid_actions_of_this_location(self):
        s = GameState()
        # grid_locked требует tech1, ещё не открыт - не в списке.
        self.assertEqual(set(engine.grid_actions_at("grid_loc", s)), {"grid_line", "grid_mirror"})

    def test_opening_tech_adds_locked_grid_action(self):
        s = GameState(techs={"tech1"})
        self.assertIn("grid_locked", engine.grid_actions_at("grid_loc", s))

    def test_manual_actions_at_still_sees_plain_card(self):
        s = GameState()
        self.assertIn("grid_plain_card", engine.manual_actions_at("grid_loc", s))
        self.assertNotIn("grid_plain_card", engine.grid_actions_at("grid_loc", s))


class MatchGridTests(FixtureTestCase):
    def test_exact_match_top_row(self):
        s = GameState()
        placed = ["raw_a", "raw_a", None, None]
        self.assertEqual(engine.match_grid(s, "grid_loc", placed), "grid_line")

    def test_match_shifted_to_bottom_row(self):
        # Тот же узор 1x2, но сдвинут в нижний ряд сетки 2x2 - должен матчиться
        # ровно так же (см. _normalize_grid: обрезка пустых краёв).
        s = GameState()
        placed = [None, None, "raw_a", "raw_a"]
        self.assertEqual(engine.match_grid(s, "grid_loc", placed), "grid_line")

    def test_empty_grid_does_not_match(self):
        s = GameState()
        self.assertIsNone(engine.match_grid(s, "grid_loc", [None, None, None, None]))

    def test_partial_pattern_does_not_match(self):
        s = GameState()
        placed = ["raw_a", None, None, None]
        self.assertIsNone(engine.match_grid(s, "grid_loc", placed))

    def test_extra_cell_breaks_match(self):
        # Лишний ресурс где-то ещё в сетке меняет нормализованную форму целиком.
        s = GameState()
        placed = ["raw_a", "raw_a", "raw_b", None]
        self.assertIsNone(engine.match_grid(s, "grid_loc", placed))

    def test_locked_action_is_not_matched(self):
        s = GameState()
        placed = ["raw_b", None, None, None]
        self.assertIsNone(engine.match_grid(s, "grid_loc", placed))

    def test_locked_action_matches_once_tech_known(self):
        s = GameState(techs={"tech1"})
        placed = ["raw_b", None, None, None]
        self.assertEqual(engine.match_grid(s, "grid_loc", placed), "grid_locked")

    def test_direct_pattern_matches_without_mirroring(self):
        s = GameState()
        placed = ["raw_a", "raw_b", None, None]
        self.assertEqual(engine.match_grid(s, "grid_loc", placed), "grid_mirror")

    def test_mirrored_pattern_matches_when_allowed(self):
        s = GameState()
        placed = ["raw_b", "raw_a", None, None]
        self.assertEqual(engine.match_grid(s, "grid_loc", placed), "grid_mirror")

    def test_location_without_crafting_never_matches(self):
        s = GameState()
        self.assertIsNone(engine.match_grid(s, "yard", ["raw_a"]))


class TogglePinnedRecipeTests(FixtureTestCase):
    def test_pinning_a_grid_recipe_adds_it(self):
        s = GameState()
        result = engine.toggle_pinned_recipe(s, "grid_line")
        self.assertTrue(result.ok)
        self.assertEqual(s.pinned_recipes, ["grid_line"])

    def test_pinning_twice_unpins(self):
        s = GameState(pinned_recipes=["grid_line"])
        result = engine.toggle_pinned_recipe(s, "grid_line")
        self.assertTrue(result.ok)
        self.assertEqual(s.pinned_recipes, [])

    def test_cannot_pin_a_plain_action(self):
        s = GameState()
        result = engine.toggle_pinned_recipe(s, "grid_plain_card")
        self.assertFalse(result.ok)
        self.assertEqual(s.pinned_recipes, [])

    def test_cannot_pin_unknown_action(self):
        s = GameState()
        result = engine.toggle_pinned_recipe(s, "not_a_real_action")
        self.assertFalse(result.ok)

    def test_unpinning_removed_content_still_works(self):
        # См. CLAUDE.md "Удаление контента ломает сохранения" - открепить уже
        # закреплённый, но с тех пор удалённый из content.json id должно быть
        # можно, чтобы игрок мог вычистить сохранение.
        s = GameState(pinned_recipes=["deleted_action"])
        result = engine.toggle_pinned_recipe(s, "deleted_action")
        self.assertTrue(result.ok)
        self.assertEqual(s.pinned_recipes, [])


if __name__ == "__main__":
    unittest.main()
