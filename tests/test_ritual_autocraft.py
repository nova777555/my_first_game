"""
Ритуалы в автокрафте и автоматизаторе (game/engine.py: building_assembler_capacity,
order_recipe/check_order с ключом "ritual_recipe:<id>", шаг заказа kind "ritual", цель автоматизатора
"ritual_recipe").

- Сборщик ритуалов - постройка с autocraft_assembler {building: <алтарь>, per_turn}:
  проводит ритуалы этого алтаря для заказов мгновенно, без хода у алтаря (алтарь
  остаётся свободным) и без extra_consumes_per_turn, per_turn ритуалов за ход на
  копию (null - без лимита).
- Ритуал - источник ресурса для цепочек заказов (после обычного рецепта и крафта
  за энергию); ритуал со случайным результатом автокрафт не проводит.
- Автоматизатор: правило с целью "ritual_recipe" запускает ритуал на алтаре, как
  клик игрока.

Запуск: python -m unittest tests.test_ritual_autocraft -v
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


def _building(name, **extra):
    b = {
        "name": name, "description": f"{name} (тест ритуалов).", "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
    }
    b.update(extra)
    return b


def _ritual(name, ring, center, produces, **extra):
    r = {
        "name": name, "description": f"{name} (тест).", "requires_tech": None, "requires_building": "ritual_altar",
        "ring_cost": ring, "center_cost": center, "extra_cost": {}, "duration_turns": 1,
        "extra_consumes_per_turn": {}, "extra_consumes_chance": {}, "produces": produces, "produces_chance": {},
    }
    r.update(extra)
    return r


EXTRA_BUILDINGS = {
    "r_asm": _building("Тест-сборщик ритуалов", autocraft_assembler={"building": "ritual_altar", "per_turn": 1}),
    "r_asm_fast": _building("Тест-ускоренный сборщик ритуалов", autocraft_assembler={"building": "ritual_altar", "per_turn": None}),
    "cpu": _building("Тест-процессор", compute_power=1000),
}
EXTRA_RITUALS = {
    # Долгий ритуал с расходом за ход - сборщик пропускает и то и другое.
    "r_essence": _ritual(
        "Тест: эссенция", {"raw_a": 2}, "raw_b", {"essence": 3},
        duration_turns=5, extra_consumes_per_turn={"raw_a": 1},
    ),
    "r_luck": _ritual("Тест: удача", {"raw_a": 1}, "raw_b", {"luck": 1}, produces_chance={"luck": 0.5}),
    "r_huge": _ritual("Тест: большой", {"raw_a": 5}, "raw_b", {"huge": 1}),
    # Затравка в центре = результат (как «Кристаллизация маны»): цикл в графе рецептов.
    "r_seed": _ritual("Тест: затравка", {"raw_a": 1}, "seed", {"seed": 2}),
}
EXTRA_ACTIONS = {
    "use_essence": {"name": "Тест: из эссенции", "requires_tech": None, "location": "yard",
                    "cost": {"essence": 4}, "gain": {"e_item": 1}},
}
EXTRA_NAMES = {
    "essence": "Тест-эссенция", "luck": "Тест-удача", "huge": "Тест-большое", "e_item": "Тест-изделие",
    "seed": "Тест-затравка",
}


def rk(recipe_id):
    """Ключ заказа «ритуал ×N» (engine.grid_recipe_key)."""
    return engine.grid_recipe_key("ritual_recipe", recipe_id)


class RitualAutocraftTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        engine.BUILDINGS = {**copy.deepcopy(engine.BUILDINGS), **copy.deepcopy(EXTRA_BUILDINGS)}
        engine.RITUAL_RECIPES = {**copy.deepcopy(engine.RITUAL_RECIPES), **copy.deepcopy(EXTRA_RITUALS)}
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, **EXTRA_ACTIONS}
        engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, **EXTRA_NAMES}

    @staticmethod
    def state(assembler=None, **kw):
        buildings = {"ritual_altar": 1, "cpu": 1}
        if assembler:
            buildings[assembler] = 1
        buildings.update(kw.pop("buildings", {}))
        return GameState(buildings=buildings, **kw)

    @staticmethod
    def turns(s, n):
        for _ in range(n):
            engine.advance_turn(s)


class AssemblerCapacityTests(RitualAutocraftTestCase):
    def test_counts_working_copies_of_altar_assemblers(self):
        s = self.state()
        self.assertEqual(engine.building_assembler_capacity(s, "ritual_altar"), 0)
        s.buildings["r_asm"] = 2
        self.assertEqual(engine.building_assembler_capacity(s, "ritual_altar"), 2)
        s.broken["r_asm"] = 1
        self.assertEqual(engine.building_assembler_capacity(s, "ritual_altar"), 1)
        s.buildings["r_asm_fast"] = 1
        self.assertIsNone(engine.building_assembler_capacity(s, "ritual_altar"))

    def test_altar_assembler_does_not_count_for_locations(self):
        s = self.state("r_asm_fast")
        self.assertEqual(engine.location_assembler_capacity(s, "yard"), 0)
        self.assertEqual(engine.location_assembler_capacity(s, None), 0)


class OrderRitualTests(RitualAutocraftTestCase):
    def test_ordinary_assembler_makes_one_ritual_per_turn_of_any_duration(self):
        s = self.state("r_asm", resources={"raw_a": 6, "raw_b": 3})
        result = engine.order_recipe(s, rk("r_essence"), 3)
        self.assertTrue(result.ok, result.message)
        # Всё сырьё заказа сразу изъято со склада в резерв.
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertEqual(s.resources.get("raw_b", 0), 0)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("essence", 0), 0)  # результат - на склад по завершении заказа
        self.assertEqual(s.orders[0]["steps"][-1]["fires_done"], 1)
        self.turns(s, 2)
        self.assertEqual(s.resources.get("essence", 0), 9)
        self.assertEqual(s.orders, [])
        self.assertEqual(s.ritual_jobs, {})  # алтарь сборщик не занимал

    def test_accelerated_assembler_makes_any_number_at_once(self):
        s = self.state("r_asm_fast", resources={"raw_a": 20, "raw_b": 10})
        self.assertTrue(engine.order_recipe(s, rk("r_essence"), 10).ok)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("essence", 0), 30)
        self.assertEqual(s.orders, [])

    def test_assembler_skips_per_turn_upkeep(self):
        s = self.state("r_asm_fast", resources={"raw_a": 5, "raw_b": 1})
        self.assertTrue(engine.order_recipe(s, rk("r_essence"), 1).ok)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("raw_a", 0), 3)  # 2 на кольцо, extra_consumes_per_turn не списан

    def test_assemblers_of_one_altar_add_up(self):
        s = self.state("r_asm", resources={"raw_a": 8, "raw_b": 4}, buildings={"r_asm": 2})
        self.assertTrue(engine.order_recipe(s, rk("r_essence"), 4).ok)
        self.turns(s, 2)
        self.assertEqual(s.resources.get("essence", 0), 12)

    def test_altar_stays_free_for_the_player(self):
        s = self.state("r_asm", resources={"raw_a": 10, "raw_b": 5})
        self.assertTrue(engine.order_recipe(s, rk("r_essence"), 2).ok)
        self.assertTrue(engine.start_ritual(s, "ritual_slow").ok)  # игрок сам занял алтарь
        self.turns(s, 2)
        self.assertEqual(s.resources.get("essence", 0), 6)

    def test_without_assembler_order_is_refused_and_names_it(self):
        s = self.state(resources={"raw_a": 2, "raw_b": 1})
        result = engine.order_recipe(s, rk("r_essence"), 1)
        self.assertFalse(result.ok)
        self.assertIn("«Тест-сборщик ритуалов»", result.message)
        self.assertEqual(s.resources["raw_a"], 2)
        self.assertEqual(s.orders, [])

    def test_random_ritual_cannot_be_ordered(self):
        s = self.state("r_asm_fast", resources={"raw_a": 1, "raw_b": 1})
        result = engine.order_recipe(s, rk("r_luck"), 1)
        self.assertFalse(result.ok)
        self.assertIn("случайный результат", result.message)

    def test_ritual_needs_a_working_altar_with_enough_pedestals(self):
        s = self.state("r_asm_fast", resources={"raw_a": 5, "raw_b": 1})
        self.assertFalse(engine.order_recipe(s, rk("r_huge"), 1).ok)  # 5 в кольце, у алтаря 3
        s.broken["ritual_altar"] = 1
        self.assertFalse(engine.order_recipe(s, rk("r_essence"), 1).ok)

    def test_missing_raw_material_is_refused_at_once(self):
        s = self.state("r_asm_fast", resources={"raw_a": 1, "raw_b": 1})
        self.assertFalse(engine.order_recipe(s, rk("r_essence"), 1).ok)
        self.assertEqual(s.resources["raw_a"], 1)

    def test_check_order_matches_order(self):
        s = self.state("r_asm", resources={"raw_a": 2, "raw_b": 1})
        self.assertIsNone(engine.check_order(s, rk("r_essence"), 1))
        self.assertIsNotNone(engine.check_order(s, rk("r_essence"), 2))  # сырья на 2 нет
        self.assertEqual(s.orders, [])

    def test_eta_by_assembler_limit(self):
        s = self.state("r_asm")
        self.assertIsNone(engine.ritual_eta_turns(s, "r_essence", 1))
        self.assertEqual(engine.ritual_eta_turns(s, "r_essence", 3), 3)
        s.buildings["r_asm_fast"] = 1
        self.assertIsNone(engine.ritual_eta_turns(s, "r_essence", 50))

    def test_cancel_returns_reserve(self):
        s = self.state("r_asm", resources={"raw_a": 6, "raw_b": 3})
        engine.order_recipe(s, rk("r_essence"), 3)
        self.turns(s, 1)
        result = engine.cancel_order(s, s.orders[0]["id"])
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["raw_a"], 4)
        self.assertEqual(s.resources["raw_b"], 2)
        self.assertEqual(s.resources["essence"], 3)  # уже проведённый ритуал - тоже из резерва


class RitualKeyTests(RitualAutocraftTestCase):
    def test_key_round_trip(self):
        self.assertEqual(rk("r_essence"), "ritual_recipe:r_essence")
        self.assertEqual(engine.parse_grid_recipe_key(rk("r_essence")), ("ritual_recipe", "r_essence"))

    def test_ritual_is_not_a_pinnable_grid_recipe(self):
        s = self.state()
        self.assertFalse(engine.toggle_pinned_recipe(s, rk("ritual_basic")).ok)
        self.assertEqual(s.pinned_recipes, [])

    def test_unknown_ritual_key_is_refused(self):
        s = self.state("r_asm_fast")
        self.assertFalse(engine.order_recipe(s, rk("nope"), 1).ok)


class SelfSeedingRitualTests(RitualAutocraftTestCase):
    def test_orders_when_the_seed_is_in_stock(self):
        s = self.state("r_asm_fast", resources={"raw_a": 1, "seed": 1})
        self.assertTrue(engine.order_recipe(s, rk("r_seed"), 1).ok)
        self.turns(s, 1)
        self.assertEqual(s.resources["seed"], 2)

    def test_seed_is_not_conjured_from_itself(self):
        s = self.state("r_asm_fast", resources={"raw_a": 3, "seed": 1})
        result = engine.order_recipe(s, rk("r_seed"), 3)
        self.assertFalse(result.ok)
        self.assertIn("Не хватает: Тест-затравка 2", result.message)
        self.assertEqual(s.resources, {"raw_a": 3, "seed": 1})


class RitualAsIngredientTests(RitualAutocraftTestCase):
    def test_click_orders_missing_ritual_output(self):
        s = self.state("r_asm_fast", resources={"raw_a": 4, "raw_b": 2})
        result = engine.do_manual_action(s, "use_essence")
        self.assertTrue(result.ok and result.pending, result.message)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("e_item", 0), 1)
        self.assertEqual(s.resources.get("essence", 0), 2)  # 2 ритуала по 3, действие съело 4

    def test_without_assembler_the_ritual_output_is_just_missing(self):
        # Ритуал без сборщика автокрафту недоступен - он его не видит вовсе.
        s = self.state(resources={"raw_a": 4, "raw_b": 2})
        result = engine.do_manual_action(s, "use_essence")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Тест: из эссенции». Не хватает: Тест-эссенция 4.")
        self.assertEqual(s.orders, [])

    def test_second_ritual_of_a_resource_wins_by_priority(self):
        engine.RITUAL_RECIPES = {**engine.RITUAL_RECIPES, "r_essence_alt": _ritual(
            "Тест: эссенция иначе", {"raw_b": 1}, "raw_a", {"essence": 3},
        )}
        s = self.state("r_asm_fast", resources={"raw_a": 10, "raw_b": 10})
        failure, plan = engine._plan_order(s, {"essence": 3})
        self.assertIsNone(failure)
        self.assertEqual([st.get("recipe_id") for st in plan["steps"]], ["r_essence"])
        self.assertTrue(engine.set_action_priority(s, rk("r_essence_alt"), 2).ok)
        failure, plan = engine._plan_order(s, {"essence": 3})
        self.assertEqual([st.get("recipe_id") for st in plan["steps"]], ["r_essence_alt"])

    def test_random_ritual_is_not_a_recipe(self):
        s = self.state("r_asm_fast", resources={"raw_a": 1, "raw_b": 1})
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, "use_luck": {
            "name": "Тест: из удачи", "requires_tech": None, "location": "yard", "cost": {"luck": 1}, "gain": {"e_item": 1},
        }}
        result = engine.do_manual_action(s, "use_luck")
        self.assertFalse(result.ok)
        self.assertEqual(s.orders, [])


class RitualAutomationTests(RitualAutocraftTestCase):
    def test_ritual_is_an_automation_target(self):
        targets = engine.automation_eligible_targets()
        self.assertIn({"kind": "ritual_recipe", "id": "ritual_basic", "name": "Простой ритуал"}, targets)
        self.assertIn("ritual_recipe", engine.AUTOMATION_TARGET_KINDS)

    def test_add_rule_accepts_known_ritual_only(self):
        s = GameState(buildings={"automator": 1})
        self.assertTrue(engine.add_automation_rule(s, "automator", "ritual_recipe", "ritual_basic", []).ok)
        self.assertFalse(engine.add_automation_rule(s, "automator", "ritual_recipe", "nope", []).ok)

    def test_rule_runs_instant_ritual_every_turn(self):
        s = GameState(buildings={"automator": 1, "ritual_altar": 1}, resources={"raw_a": 4, "raw_b": 2})
        engine.add_automation_rule(s, "automator", "ritual_recipe", "ritual_basic", [])
        self.turns(s, 3)
        self.assertEqual(s.resources.get("mid", 0), 2)  # на третий ход сырья уже нет

    def test_rule_waits_for_locked_ritual(self):
        s = GameState(buildings={"automator": 1, "ritual_altar": 1}, resources={"raw_a": 4, "raw_b": 2})
        engine.add_automation_rule(s, "automator", "ritual_recipe", "ritual_locked", [])
        self.turns(s, 1)
        self.assertEqual(s.resources.get("mid", 0), 0)
        s.techs.add("tech1")
        self.turns(s, 1)
        self.assertEqual(s.resources.get("mid", 0), 1)

    def test_long_ritual_restarts_right_after_completion(self):
        s = GameState(buildings={"automator": 1, "ritual_altar": 1}, resources={"raw_a": 20, "raw_b": 5})
        engine.add_automation_rule(s, "automator", "ritual_recipe", "ritual_slow", [])
        self.turns(s, 1)
        self.assertEqual(s.ritual_jobs["ritual_altar"]["turns_done"], 0)
        self.turns(s, 2)  # 2 хода ритуала - завершился и тут же запущен заново
        self.assertEqual(s.resources.get("mid", 0), 2)
        self.assertEqual(s.ritual_jobs["ritual_altar"]["turns_done"], 0)

    def test_conditions_gate_the_rule(self):
        s = GameState(buildings={"automator": 1, "ritual_altar": 1}, resources={"raw_a": 4, "raw_b": 2})
        engine.add_automation_rule(s, "automator", "ritual_recipe", "ritual_basic",
                                   [{"resource": "raw_a", "op": ">=", "amount": 10}])
        self.turns(s, 2)
        self.assertEqual(s.resources.get("mid", 0), 0)


if __name__ == "__main__":
    unittest.main()
