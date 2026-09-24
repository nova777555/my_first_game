"""
Рецепт внутри локации-механизма наследует постройку этой локации
(engine.action_mechanism_building): и гейт "механизм построен и не сломан", и
улучшения постройки (масштаб, энергоэффективность, ускорение многоходовых
действий) - без requires_building в самом рецепте.

Фикстура: mechanism_a_loc (requires_building="mechanism_a"), в ней
mechanism_a_action (raw_a:2 -> mech_output:3) и mechanism_a_delayed_action
(duration_turns=4). Здесь у обоих убран собственный requires_building - постройка
приходит только из локации.

Запуск: python -m unittest tests.test_mechanism_inheritance -v
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fixtures  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402

import content_validation as cv  # noqa: E402


def _without_own_building(actions: dict) -> dict:
    actions = copy.deepcopy(actions)
    for aid in ("mechanism_a_action", "mechanism_a_delayed_action"):
        del actions[aid]["requires_building"]
    # Рецепт с энергией в цене - для energy_efficiency механизма.
    actions["mechanism_a_energy_action"] = {
        "name": "Прогнать механизм А на энергии", "requires_tech": None, "location": "mechanism_a_loc",
        "cost": {"energy": 10, "raw_a": 1}, "gain": {"mech_output": 1},
    }
    return actions


class InheritedMechanismTests(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        # cleanup patched_engine_content вернёт исходные значения обоих словарей.
        engine.MANUAL_ACTIONS = _without_own_building(engine.MANUAL_ACTIONS)
        engine.BUILDINGS = copy.deepcopy(engine.BUILDINGS)
        engine.BUILDINGS["mechanism_a"]["upgrades"].append({
            "id": "mech_a_insulation", "name": "Теплоизоляция", "description": "-50% энергии.",
            "type": "energy_efficiency", "value": 0.5, "requires_tech": None, "cost": {"raw_a": 1},
        })

    def test_building_comes_from_the_location(self):
        self.assertEqual(engine.action_mechanism_building(engine.MANUAL_ACTIONS["mechanism_a_action"]), "mechanism_a")

    def test_own_requires_building_wins_over_the_location(self):
        action = {"location": "mechanism_a_loc", "requires_building": "automation_hub"}
        self.assertEqual(engine.action_mechanism_building(action), "automation_hub")

    def test_location_without_building_gives_none(self):
        self.assertIsNone(engine.action_mechanism_building({"location": "yard"}))
        self.assertIsNone(engine.action_mechanism_building({"location": None}))

    def test_recipe_is_locked_until_the_mechanism_works(self):
        self.assertFalse(engine.is_manual_action_unlocked("mechanism_a_action", GameState()))
        self.assertTrue(engine.is_manual_action_unlocked("mechanism_a_action", GameState(buildings={"mechanism_a": 1})))
        broken = GameState(buildings={"mechanism_a": 1}, broken={"mechanism_a": 1})
        self.assertFalse(engine.is_manual_action_unlocked("mechanism_a_action", broken))

    def test_scale_upgrade_applies_to_every_recipe_inside(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        self.assertEqual(engine.mechanism_scale_cap("mechanism_a", s), 2)
        cost, gain = engine.effective_manual_action_stats("mechanism_a_action", s)
        self.assertEqual((cost, gain), ({"raw_a": 4}, {"mech_output": 6}))

    def test_duration_reduction_applies_to_the_inherited_recipe(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        self.assertEqual(engine.manual_action_duration("mechanism_a_delayed_action", s), 4)
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_duration_cut")
        self.assertEqual(engine.manual_action_duration("mechanism_a_delayed_action", s), 2)
        self.assertTrue(engine.do_manual_action(s, "mechanism_a_delayed_action").ok)
        self.assertEqual(s.manual_jobs["mechanism_a_delayed_action"]["turns_total"], 2)

    def test_energy_efficiency_applies_to_the_inherited_recipe(self):
        s = GameState(resources={"raw_a": 100, "energy": 50}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_insulation")
        cost, _ = engine.effective_manual_action_stats("mechanism_a_energy_action", s)
        self.assertEqual(cost, {"energy": 5, "raw_a": 1})

    def test_autocraft_names_the_inherited_mechanism(self):
        for hub in ("automation_hub", "trickle_hub"):
            engine.BUILDINGS[hub]["unlocks_autocraft"].append("mech_output")
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        failure = engine.ensure_and_pay(s, {"mech_output": 3})
        self.assertEqual((failure.reason, failure.building_id), ("building_missing", "mechanism_a"))
        s.buildings["mechanism_a"] = 1
        self.assertIsNone(engine.ensure_and_pay(s, {"mech_output": 3}))


class InheritedMechanismWebUiTests(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        import web_ui
        self.web_ui = web_ui
        actions = _without_own_building(engine.MANUAL_ACTIONS)
        engine.MANUAL_ACTIONS = actions
        web_ui.MANUAL_ACTIONS = actions

    def test_upgrade_preview_lists_inherited_recipes(self):
        deps = self.web_ui._dependent_action_ids("mechanism_a")
        self.assertIn("mechanism_a_action", deps)
        self.assertIn("mechanism_a_energy_action", deps)


class InheritedMechanismValidationTests(unittest.TestCase):
    def test_duration_reduction_counts_recipes_inherited_from_the_location(self):
        data = fixtures.as_content_dict()
        del data["manual_actions"]["mechanism_a_delayed_action"]["requires_building"]
        errors = cv.validate(data)
        self.assertFalse([e for e in errors if "action_duration_reduction" in e], errors)


if __name__ == "__main__":
    unittest.main()
