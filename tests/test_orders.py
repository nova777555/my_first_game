"""
Заказы автокрафта (game/engine.py: _place_order/_process_orders/order_recipe).

- Доступ: рецепт автокрафтится только через свой механизм - сборщики локации
  (autocraft_assembler: мгновенно при любой длительности, per_turn крафтов за ход,
  null - без лимита) и/или «Автоматизация» постройки механизма (upgrade type
  autocraft: как игрок - 1 крафт за ход, длительность соблюдается). Процессоры -
  «мозг заказов»: мощность = размер одного заказа, слоты = сколько заказов сразу.
- Резерв: при заказе всё нужное изымается со склада в заказ, промежуточные
  предметы копятся там же; отмена возвращает резерв на склад.
- Нет сырья, которое автокрафт делать не умеет, - отказ сразу. Рецепт, который
  автокрафт сейчас выполнить не может (механизм не автоматизирован, сломан, не
  изучен), он не видит вовсе: такой ресурс для него - сырьё («Не хватает: X N»).

Мини-контент (механизм m_mech с локацией m_loc, сборщики, рецепты) добавляется
поверх фикстуры в setUp.

Запуск: python -m unittest tests.test_orders -v
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
        "name": name, "description": f"{name} (тест заказов).", "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
    }
    b.update(extra)
    return b


def _action(name, location, cost, gain, **extra):
    a = {"name": name, "requires_tech": None, "location": location, "cost": cost, "gain": gain}
    a.update(extra)
    return a


EXTRA_BUILDINGS = {
    "m_mech": _building("Тест-механизм", upgrades=[{
        "id": "m_auto", "name": "Автоматизация", "description": "Автокрафт рецептов механизма.",
        "type": "autocraft", "value": 1, "requires_tech": None, "cost": {},
    }]),
    "m_asm": _building("Тест-сборщик", autocraft_assembler={"location": "m_loc", "per_turn": 1}),
    "m_asm_fast": _building("Тест-ускоренный сборщик", autocraft_assembler={"location": "m_loc", "per_turn": None}),
    "conv_asm": _building("Тест-сборщик преобразователя", autocraft_assembler={"location": "converter", "per_turn": None}),
    "cpu": _building("Тест-процессор", compute_power=1000),
    "e_gen": _building("Тест-генератор", produces={"energy": 10}),
    "e_bank": _building("Тест-банк", energy_storage=200),
    "big_thing": _building("Тест-большая постройка", cost={"m_out": 2, "raw_b": 1}, max_count=1),
    "dispatcher": _building("Тест-диспетчер", autocraft_slots=4),
    "hub": _building("Тест-центр автоматизации", automation_hub={"slots": 1}),
}
EXTRA_LOCATIONS = {"m_loc": {"name": "Тест-механизм", "requires_tech": None, "requires_building": "m_mech"}}
EXTRA_ACTIONS = {
    "m_quick": _action("Тест: быстрый", "m_loc", {"raw_a": 1}, {"m_out": 1}),
    "m_slow": _action("Тест: медленный", "m_loc", {"raw_a": 1}, {"m_slow_out": 1}, duration_turns=3),
    "use_m_out": _action("Тест: из быстрого", "yard", {"m_out": 2}, {"e_top": 1}),
    "use_m_slow": _action("Тест: из медленного", "yard", {"m_slow_out": 2}, {"e_top": 1}),
    "yard_recipe": _action("Тест: во дворе", "yard", {"raw_a": 1}, {"yard_out": 1}),
    "m_refine": _action("Тест: очистка", "m_loc", {"m_slow_out": 1}, {"m_refined": 1}, duration_turns=2),
    "use_refined": _action("Тест: из очищенного", "yard", {"m_refined": 1}, {"e_top": 1}),
    "use_yard_out": _action("Тест: из двора", "yard", {"yard_out": 1}, {"e_top": 1}),
    # два выхода за прокрутку: заказ, которому нужны оба, не крафтит дважды
    "m_split": _action("Тест: разделение", "m_loc", {"raw_a": 2}, {"m_a": 1, "m_b": 1}),
    "m_join": _action("Тест: соединение", "m_loc", {"m_a": 1, "m_b": 1}, {"e_top": 1}),
}
EXTRA_NAMES = {
    "m_out": "Тест-быстрый выход", "m_slow_out": "Тест-медленный выход", "e_top": "Тест-итог",
    "yard_out": "Тест-дворовый выход", "m_refined": "Тест-очищенное", "m_a": "Тест-выход A", "m_b": "Тест-выход Б",
}


class OrdersTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        engine.BUILDINGS = {**copy.deepcopy(engine.BUILDINGS), **copy.deepcopy(EXTRA_BUILDINGS)}
        engine.LOCATIONS = {**engine.LOCATIONS, **EXTRA_LOCATIONS}
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, **EXTRA_ACTIONS}
        engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, **EXTRA_NAMES}

    def mech_state(self, **kw):
        """m_mech построен и автоматизирован, есть процессор."""
        buildings = {"m_mech": 1, "cpu": 1}
        buildings.update(kw.pop("buildings", {}))
        upgrades = kw.pop("building_upgrades", {"m_mech": ["m_auto"]})
        return GameState(buildings=buildings, building_upgrades=upgrades, **kw)

    @staticmethod
    def turns(s, n):
        for _ in range(n):
            engine.advance_turn(s)

    @staticmethod
    def turns_until_done(s, limit=40):
        """Сколько ходов до выполнения всех заказов (None - не уложились в limit)."""
        for turn in range(1, limit + 1):
            engine.advance_turn(s)
            if not s.orders:
                return turn
        return None


class AccessTests(OrdersTestCase):
    def test_recipe_outside_any_mechanism_is_not_autocrafted(self):
        s = GameState(resources={"raw_a": 5}, buildings={"cpu": 1})
        result = engine.do_manual_action(s, "use_yard_out")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Тест: из двора». Не хватает: Тест-дворовый выход 1.")
        self.assertEqual(s.resources["raw_a"], 5)

    def test_recipe_of_a_mechanism_without_automation_is_not_seen(self):
        # Регрессия: писалось «докрафтится» и «нужна Автоматизация», хотя автокрафт этот
        # рецепт выполнить не может - для него это просто нехватка, как у сырья.
        s = self.mech_state(resources={"raw_a": 5}, building_upgrades={})
        result = engine.do_manual_action(s, "use_m_out")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Тест: из быстрого». Не хватает: Тест-быстрый выход 2.")
        self.assertEqual(s.orders, [])

    def test_ordering_the_recipe_itself_still_names_what_is_needed(self):
        # Заказ САМОГО рецепта неавтоматизированного механизма - тут подсказка к месту.
        s = self.mech_state(resources={"raw_a": 5}, building_upgrades={})
        result = engine.order_recipe(s, "m_quick", 1)
        self.assertFalse(result.ok)
        self.assertIn("«Автоматизация» постройки «Тест-механизм»", result.message)
        self.assertIn("«Тест-сборщик»", result.message)

    def test_assembler_alone_is_enough(self):
        s = self.mech_state(resources={"raw_a": 5}, building_upgrades={}, buildings={"m_asm_fast": 1})
        self.assertTrue(engine.do_manual_action(s, "use_m_out").pending)

    def test_without_processor_it_is_a_plain_shortage(self):
        s = GameState(resources={"raw_a": 5}, buildings={"m_mech": 1}, building_upgrades={"m_mech": ["m_auto"]})
        result = engine.do_manual_action(s, "use_m_out")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Тест: из быстрого». Не хватает: Тест-быстрый выход 2.")

    def test_missing_raw_material_rejects_at_once_and_takes_nothing(self):
        s = self.mech_state(resources={"raw_a": 1})
        result = engine.do_manual_action(s, "use_m_out")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Тест: из быстрого». Не хватает: Сырьё A 1.")
        self.assertEqual(s.resources["raw_a"], 1)
        self.assertEqual(s.orders, [])

    def test_shortage_lists_everything_autocraft_cannot_make(self):
        # m_out - рецепт не автоматизирован, raw_b - сырьё: оба в одном списке.
        s = self.mech_state(resources={"raw_a": 5}, building_upgrades={})
        failure = engine.autocraft_failure(s, {"m_out": 2, "raw_b": 1})
        self.assertEqual((failure.reason, failure.missing), ("no_recipe", {"m_out": 2, "raw_b": 1}))
        self.assertEqual(engine.describe_autocraft_failure(failure), "не хватает: Тест-быстрый выход 2, Сырьё B 1")

    def test_order_size_is_capped_by_processor_power(self):
        # automation_hub: мощность 15; 6 полуфабрикатов (по 4) + 3 изделия (по 3) = 33
        s = GameState(resources={"raw_a": 100, "raw_b": 10}, buildings={"automation_hub": 1})
        result = engine.order_recipe(s, "craft_final", 3)
        self.assertFalse(result.ok)
        self.assertIn("нужно 33, мощность 15", result.message)
        self.assertEqual(s.resources["raw_a"], 100)

    def test_processor_slots_limit_concurrent_orders(self):
        s = self.mech_state(resources={"raw_a": 10})
        self.assertTrue(engine.order_recipe(s, "m_slow", 1).ok)
        second = engine.order_recipe(s, "m_quick", 1)
        self.assertFalse(second.ok)
        self.assertIn("слоты заказов", second.message)
        s.buildings["slot_hub"] = 1
        self.assertTrue(engine.order_recipe(s, "m_quick", 1).ok)

    def test_probabilistic_and_excluded_recipes_are_never_used(self):
        s = GameState(resources={"raw_a": 50}, buildings={"automation_hub": 1})
        self.assertFalse(engine._place_order(s, "t", "x", "тест", {"lucky_output": 1})[1])

    def test_recipe_of_a_broken_mechanism_is_not_seen(self):
        s = GameState(resources={"raw_a": 20}, buildings={"mechanism_a": 1, "cpu": 1}, broken={"mechanism_a": 1})
        engine.BUILDINGS["mechanism_a"]["upgrades"].append(
            {"id": "a_auto", "name": "Авт.", "description": ".", "type": "autocraft", "value": 1, "requires_tech": None, "cost": {}})
        s.building_upgrades["mechanism_a"] = ["a_auto"]
        failure, _ = engine._place_order(s, "t", "x", "тест", {"mech_output": 3})
        self.assertEqual((failure.reason, failure.missing), ("no_recipe", {"mech_output": 3}))


class ReservationTests(OrdersTestCase):
    def test_order_withdraws_stock_and_the_store_shows_only_the_rest(self):
        s = self.mech_state(resources={"raw_a": 10})
        self.assertTrue(engine.order_recipe(s, "m_quick", 3).ok)
        self.assertEqual(s.resources["raw_a"], 7)
        self.assertEqual(s.orders[0]["reserved"], {"raw_a": 3})

    def test_nobody_can_take_reserved_materials(self):
        s = self.mech_state(resources={"raw_a": 3})
        engine.order_recipe(s, "m_slow", 3)  # 3 партии по 3 хода - сырьё полежит в резерве
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertFalse(engine.do_manual_action(s, "yard_recipe").ok)  # клик игрока не видит резерв
        self.turns(s, 9)
        self.assertEqual(s.resources.get("m_slow_out"), 3)

    def test_intermediates_stay_in_the_order(self):
        s = GameState(resources={"raw_a": 30, "raw_b": 5}, buildings={"trickle_hub": 1})
        self.assertTrue(engine.order_recipe(s, "craft_final", 3).ok)
        self.turns(s, 1)
        self.assertEqual(s.orders[0]["reserved"].get("mid"), 2)
        self.assertIsNone(s.resources.get("mid"))

    def test_cancel_returns_reserve_and_intermediates(self):
        s = GameState(resources={"raw_a": 30, "raw_b": 5}, buildings={"trickle_hub": 1})
        engine.order_recipe(s, "craft_final", 3)
        self.turns(s, 1)
        result = engine.cancel_order(s, s.orders[0]["id"])
        self.assertTrue(result.ok, result.message)
        self.assertEqual((s.resources["raw_a"], s.resources["raw_b"], s.resources["mid"]), (22, 5, 2))
        self.assertEqual(s.orders, [])

    def test_cancel_unknown_order(self):
        self.assertFalse(engine.cancel_order(GameState(), 42).ok)

    def test_cancelled_order_lets_its_running_batch_finish_into_the_store(self):
        s = self.mech_state(resources={"raw_a": 5})
        engine.order_recipe(s, "m_slow", 2)
        self.turns(s, 1)
        self.assertIn("m_slow", s.manual_jobs)
        engine.cancel_order(s, s.orders[0]["id"])
        self.turns(s, 3)
        self.assertEqual(s.resources.get("m_slow_out"), 1)

    def test_cancelling_the_batch_itself_refunds_into_the_order(self):
        s = self.mech_state(resources={"raw_a": 5})
        engine.order_recipe(s, "m_slow", 1)
        self.turns(s, 1)
        engine.cancel_manual_job(s, "m_slow")
        self.assertEqual(s.orders[0]["reserved"].get("raw_a"), 1)
        self.assertEqual(s.resources["raw_a"], 4)


class ExecutionTests(OrdersTestCase):
    def test_assembler_limit_per_turn(self):
        s = GameState(resources={"raw_a": 30}, buildings={"trickle_hub": 1})  # 2 крафта за ход
        engine.order_recipe(s, "craft_mid", 5)
        self.turns(s, 2)
        self.assertEqual(s.orders[0]["steps"][0]["fires_done"], 4)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("mid"), 5)
        self.assertEqual(s.orders, [])

    def test_unlimited_assembler_finishes_in_the_order_turn(self):
        s = self.mech_state(resources={"raw_a": 30}, buildings={"m_asm_fast": 1})
        engine.order_recipe(s, "m_quick", 20)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("m_out"), 20)

    def test_assembler_ignores_recipe_duration(self):
        s = self.mech_state(resources={"raw_a": 10}, building_upgrades={}, buildings={"m_asm": 1})
        engine.order_recipe(s, "m_slow", 2)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("m_slow_out"), None)  # один сборщик - 1 крафт за ход
        self.assertEqual(s.orders[0]["steps"][0]["fires_done"], 1)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("m_slow_out"), 2)  # 2 хода вместо 6
        self.assertEqual(s.manual_jobs, {})

    def test_mechanism_automation_works_like_a_player(self):
        s = self.mech_state(resources={"raw_a": 10})
        engine.order_recipe(s, "m_quick", 3)
        self.turns(s, 2)
        self.assertEqual(s.orders[0]["steps"][0]["fires_done"], 2)  # 1 крафт за ход
        s2 = self.mech_state(resources={"raw_a": 10})
        engine.order_recipe(s2, "m_slow", 2)
        self.turns(s2, 1)
        self.assertEqual(s2.manual_jobs["m_slow"]["turns_total"], 3)  # своя длительность
        self.turns(s2, 3)  # первая партия готова на 3-м тике, вторая стартует сразу
        self.assertEqual(s2.orders[0]["steps"][0]["fires_done"], 1)
        self.assertIn("m_slow", s2.manual_jobs)
        self.turns(s2, 3)
        self.assertEqual(s2.resources.get("m_slow_out"), 2)

    def test_assemblers_and_mechanism_add_up_for_instant_recipes(self):
        s = self.mech_state(resources={"raw_a": 10}, buildings={"m_asm": 2})
        self.assertEqual(engine.location_autocraft_capacity(s, "m_loc"), 3)
        engine.order_recipe(s, "m_quick", 3)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("m_out"), 3)

    def test_long_batch_waits_for_players_own_job(self):
        s = self.mech_state(resources={"raw_a": 10})
        self.assertTrue(engine.do_manual_action(s, "m_slow").ok)  # игрок сам запустил партию
        engine.order_recipe(s, "m_slow", 1)
        self.turns(s, 1)
        self.assertIn("механизм занят", s.orders[0]["status"])

    def test_long_recipe_over_long_ingredient_in_the_same_location(self):
        # m_refine и m_slow делят m_loc: механизм сначала делает ингредиент (3 хода),
        # потом потребителя (2 хода) - без взаимной блокировки.
        s = self.mech_state(resources={"raw_a": 5})
        self.assertTrue(engine.do_manual_action(s, "use_refined").pending)
        for turn in range(1, 10):
            engine.advance_turn(s)
            if s.resources.get("e_top"):
                break
        self.assertEqual(turn, 5)
        self.assertEqual((s.manual_jobs, s.orders), ({}, []))

    def test_manual_jobs_view_marks_order_batches(self):
        s = self.mech_state(resources={"raw_a": 5})
        engine.order_recipe(s, "m_slow", 1)
        self.turns(s, 1)
        self.assertEqual([(j["action_id"], j["autocraft"]) for j in engine.manual_jobs_view(s)], [("m_slow", True)])

    def test_implicit_order_finishes_the_action(self):
        s = self.mech_state(resources={"raw_a": 5})
        result = engine.do_manual_action(s, "use_m_out")
        self.assertTrue(result.pending, result.message)
        self.turns(s, 2)
        self.assertEqual(s.resources.get("e_top"), 1)
        self.assertEqual(s.resources.get("m_out", 0), 0)
        self.assertEqual(s.orders, [])

    def test_repeat_click_does_not_duplicate_an_order(self):
        s = self.mech_state(resources={"raw_a": 5})
        engine.do_manual_action(s, "use_m_out")
        again = engine.do_manual_action(s, "use_m_out")
        self.assertTrue(again.pending)
        self.assertIn("уже заказано", again.message)
        self.assertEqual(len(s.orders), 1)

    def test_building_through_an_order(self):
        s = self.mech_state(resources={"raw_a": 5, "raw_b": 1})
        self.assertTrue(engine.build(s, "big_thing").pending)
        self.assertEqual(s.resources.get("raw_b", 0), 0)  # раз уж заказ - в резерве и готовое
        self.turns(s, 3)
        self.assertEqual(s.buildings.get("big_thing"), 1)

    def test_final_action_failure_keeps_the_order_with_its_reason(self):
        s = self.mech_state(resources={"raw_a": 5, "raw_b": 1})
        engine.build(s, "big_thing")
        s.buildings["big_thing"] = 1  # тем временем постройка появилась другим путём
        self.turns(s, 3)
        (order,) = s.orders
        self.assertIn("Нельзя построить больше", order["status"])
        self.assertEqual(order["reserved"], {"m_out": 2, "raw_b": 1})
        engine.cancel_order(s, order["id"])
        self.assertEqual((s.resources["m_out"], s.resources["raw_b"]), (2, 1))

    def test_broken_mechanism_pauses_the_order_with_a_reason(self):
        s = self.mech_state(resources={"raw_a": 5})
        engine.order_recipe(s, "m_quick", 3)
        s.broken["m_mech"] = 1
        self.turns(s, 1)
        self.assertIn("не работает", s.orders[0]["status"])
        self.assertEqual(s.orders[0]["reserved"], {"raw_a": 3})

    def test_chosen_scale_survives_until_the_order_is_done(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1, "automation_hub": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        s.resources["raw_a"] = 0
        s.resources["mid"] = 0
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, "mechanism_a_action": {
            **engine.MANUAL_ACTIONS["mechanism_a_action"], "cost": {"mid": 1}}}
        s.resources["raw_a"] = 8
        result = engine.do_manual_action(s, "mechanism_a_action", scale=2)
        self.assertTrue(result.pending, result.message)
        self.assertEqual(s.orders[0]["scale"], 2)
        self.turns(s, 1)
        self.assertEqual(s.resources.get("mech_output"), 6)  # 3 * 2


class ConverterOrderTests(OrdersTestCase):
    def test_converter_recipes_need_automation(self):
        # Без автоматизации рецепта преобразователя для автокрафта нет - raw_b просто не хватает.
        s = GameState(buildings={"energy_converter": 1, "cpu": 1})
        failure, _ = engine._place_order(s, "t", "x", "тест", {"raw_b": 1})
        self.assertEqual((failure.reason, failure.missing), ("no_recipe", {"raw_b": 1}))

    def test_automated_converter_runs_one_batch_per_free_slot(self):
        s = GameState(buildings={"energy_converter": 3, "cpu": 1}, building_upgrades={"energy_converter": ["conv_auto"]})
        failure, order = engine._place_order(s, "t", "x", "тест", {"raw_b": 3})
        self.assertIsNone(failure)
        engine._process_orders(s, engine.ProductionReport(), {})
        self.assertEqual([j["recipe_id"] for j in s.energy_jobs], ["make_raw_b"] * 3)

    def test_machine_type_and_material_come_from_the_order(self):
        s = GameState(
            resources={"raw_a": 5}, buildings={"energy_converter": 1, "matter_generator": 1, "cpu": 1},
            building_upgrades={"energy_converter": ["conv_auto"], "matter_generator": ["matter_auto"]},
        )
        failure, order = engine._place_order(s, "t", "x", "тест", {"upgraded_matter": 1})
        self.assertIsNone(failure)
        self.assertEqual(order["reserved"], {"raw_a": 5})
        engine._process_orders(s, engine.ProductionReport(), {})
        self.assertEqual([(j["recipe_id"], j["building_id"]) for j in s.energy_jobs], [("upgrade_matter", "matter_generator")])
        self.assertEqual(order["reserved"].get("raw_a"), 0)

    def test_converter_assembler_crafts_instantly_paying_the_threshold(self):
        s = GameState(resources={"energy": 100}, buildings={"conv_asm": 1, "cpu": 1, "e_bank": 1})
        failure, order = engine._place_order(s, "recipe", "x", "тест", {"raw_b": 3})
        self.assertIsNone(failure)
        engine._process_orders(s, engine.ProductionReport(), {})
        self.assertEqual(s.resources.get("raw_b"), 3)  # готово в тот же ход, выдано на склад
        self.assertEqual(s.resources["energy"], 70)     # 3 порога по 10 - разом
        self.assertEqual((s.energy_jobs, s.orders), ([], []))


class ConverterRecipeOrderTests(OrdersTestCase):
    """«Заказать ×N» рецепта преобразователя (ключ "energy_recipe:<id>"): N - партии."""

    def conv_state(self, **kw):
        buildings = {"energy_converter": 2, "cpu": 1, "e_gen": 1, "e_bank": 1}
        buildings.update(kw.pop("buildings", {}))
        return GameState(buildings=buildings, building_upgrades={"energy_converter": ["conv_auto"]}, **kw)

    def test_order_runs_n_batches_in_converter_slots(self):
        s = self.conv_state(resources={"energy": 100})
        # 2 слота, порог 10 при 4 энергии/ход: 2 круга по 3 хода
        self.assertEqual(engine.converter_eta_turns(s, "make_raw_b", 3), 6)
        result = engine.order_recipe(s, "energy_recipe:make_raw_b", 3)
        self.assertTrue(result.ok, result.message)
        (order,) = s.orders
        self.assertEqual(order["label"], "«Сделать сырьё B из энергии» ×3")
        self.assertEqual(order["steps"][0]["kind"], "energy")
        self.turns(s, 5)
        self.assertEqual(len(s.orders), 1)
        self.turns(s, 1)
        self.assertEqual((s.orders, s.resources.get("raw_b")), ([], 3))

    def test_order_eta_counts_only_the_slots_of_the_recipe_machine(self):
        # upgrade_matter идёт только на matter_generator (1 слот, 50/ход): 3 партии по
        # порогу 20 - 3 хода; 2 автоматизированных «чужих» преобразователя не помогают.
        def state():
            s = self.conv_state(resources={"raw_a": 20, "energy": 100}, buildings={"matter_generator": 1})
            s.building_upgrades["matter_generator"] = ["matter_auto"]
            return s
        s = state()
        self.assertEqual(engine.order_eta_turns(s, "energy_recipe:upgrade_matter", 3), 3)
        engine.order_recipe(s, "energy_recipe:upgrade_matter", 3)
        self.assertEqual(self.turns_until_done(s), 3)
        # make_raw_b без machine - подходит любой тип, генератор материи тоже помогает
        s = state()
        eta = engine.order_eta_turns(s, "energy_recipe:make_raw_b", 3)
        engine.order_recipe(s, "energy_recipe:make_raw_b", 3)
        self.assertEqual(self.turns_until_done(s), eta)

    def test_order_reserves_consumes_and_uses_the_recipe_machine(self):
        s = GameState(
            resources={"raw_a": 12}, buildings={"matter_generator": 1, "cpu": 1},
            building_upgrades={"matter_generator": ["matter_auto"]},
        )
        result = engine.order_recipe(s, "energy_recipe:upgrade_matter", 2)
        self.assertTrue(result.ok, result.message)
        self.assertEqual((s.orders[0]["reserved"], s.resources["raw_a"]), ({"raw_a": 10}, 2))
        engine._process_orders(s, engine.ProductionReport(), {})
        self.assertEqual([j["building_id"] for j in s.energy_jobs], ["matter_generator"])

    def test_random_output_counts_batches_not_units(self):
        s = GameState(resources={"energy": 100}, buildings={"conv_asm": 1, "cpu": 1, "e_bank": 1})
        self.assertTrue(engine.order_recipe(s, "energy_recipe:make_variable", 2).ok)
        engine._process_orders(s, engine.ProductionReport(), {})
        self.assertEqual((s.orders, s.resources["energy"]), ([], 90))  # ровно 2 партии по 5
        self.assertTrue(6 <= s.resources["variable_output"] <= 14)

    def test_needs_converter_automation(self):
        s = GameState(buildings={"energy_converter": 1, "cpu": 1})
        result = engine.order_recipe(s, "energy_recipe:make_raw_b", 1)
        self.assertFalse(result.ok)
        self.assertIn("«Автоматизация» постройки", result.message)
        self.assertEqual(engine.check_order(s, "energy_recipe:make_raw_b", 1).reason, "not_automated")

    def test_locked_recipe_cannot_be_ordered(self):
        engine.ENERGY_RECIPES = copy.deepcopy(engine.ENERGY_RECIPES)
        engine.ENERGY_RECIPES["make_raw_b"]["requires_tech"] = "t_locked"
        s = self.conv_state()
        self.assertIn("ещё не открыт", engine.order_recipe(s, "energy_recipe:make_raw_b", 1).message)
        self.assertEqual(engine.check_order(s, "energy_recipe:make_raw_b", 1).reason, "tech_locked")
        self.assertFalse(engine.order_recipe(s, "energy_recipe:no_such", 1).ok)

    def test_preview_shows_the_energy_the_converter_waits_for(self):
        # Слоты копят порог по ходам - в предпросмотре это ожидание энергии, как у
        # рецепта с энергией в цене (иначе карточка молчала бы про ночь без солнца).
        s = self.conv_state(resources={"energy": 5})
        (node,) = engine.autocraft_preview(s, {"raw_b": 2})["nodes"]
        (energy,) = [c for c in node["children"] if c["resource_id"] == "energy"]
        self.assertEqual((energy["needed"], energy["taken"], energy.get("energy_wait")), (20, 5, True))

    def test_orders_view_marks_batch_steps(self):
        s = self.conv_state()
        engine.order_recipe(s, "energy_recipe:make_raw_b", 2)
        (step,) = engine.orders_view(s)[0]["steps"]
        self.assertEqual((step["batches_of"], step["total"]), ("make_raw_b", 2))


class _ConverterRoutesCase(OrdersTestCase):
    """raw_b делается и обычным рецептом (m_make_b в механизме m_loc из raw_a), и на
    преобразователе (make_raw_b)."""

    def setUp(self):
        super().setUp()
        engine.MANUAL_ACTIONS = {
            **engine.MANUAL_ACTIONS,
            "m_make_b": _action("Тест: B в механизме", "m_loc", {"raw_a": 2}, {"raw_b": 1}),
            "m_use_b": _action("Тест: из B", "m_loc", {"raw_b": 1}, {"b_top": 1}),
        }
        engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, "b_top": "Тест-итог из B"}

    def state(self, mech_auto=True, conv_auto=True, **kw):
        upgrades = {}
        if mech_auto:
            upgrades["m_mech"] = ["m_auto"]
        if conv_auto:
            upgrades["energy_converter"] = ["conv_auto"]
        return GameState(
            buildings={"m_mech": 1, "cpu": 1, "energy_converter": 1, "e_gen": 1, "e_bank": 1},
            building_upgrades=upgrades, **kw,
        )

    def step_kinds(self, s, cost):
        failure, plan = engine._plan_order(s, cost)
        self.assertIsNone(failure)
        return [(st["kind"], st.get("action_id") or st.get("recipe_id")) for st in plan["steps"]]


class ConverterFallbackTests(_ConverterRoutesCase):
    """Заказ берёт обычный рецепт, а если он невозможен - преобразователь."""

    def test_ordinary_recipe_comes_first(self):
        s = self.state(resources={"raw_a": 10})
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("action", "m_make_b")])

    def test_converter_when_raw_material_is_missing(self):
        s = self.state()
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("energy", "make_raw_b")])

    def test_converter_when_mechanism_is_not_automated(self):
        s = self.state(mech_auto=False, resources={"raw_a": 10})
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("energy", "make_raw_b")])

    def test_failure_explains_the_ordinary_path_when_converter_fails_too(self):
        s = self.state(conv_auto=False)
        failure, _ = engine._plan_order(s, {"raw_b": 2})
        self.assertEqual((failure.resource_id, failure.reason), ("raw_a", "no_recipe"))

    def test_chain_through_the_converter_finishes(self):
        s = self.state()
        result = engine.order_recipe(s, "m_use_b", 2)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(
            [(st["kind"], st["resource_id"]) for st in s.orders[0]["steps"]], [("energy", "raw_b"), ("action", "b_top")],
        )
        for _ in range(12):
            if not s.orders:
                break
            engine.advance_turn(s)
        self.assertEqual((s.orders, s.resources.get("b_top"), s.resources.get("raw_b", 0)), ([], 2, 0))

    def test_preview_follows_the_same_route(self):
        s = self.state()
        (node,) = engine.autocraft_preview(s, {"raw_b": 2})["nodes"]
        self.assertEqual((node["energy_recipe_id"], node["action_id"], node["fires"]), ("make_raw_b", None, 2))
        s.resources["raw_a"] = 10
        (node,) = engine.autocraft_preview(s, {"raw_b": 2})["nodes"]
        self.assertEqual((node["energy_recipe_id"], node["action_id"]), (None, "m_make_b"))


class ConverterPriorityTests(_ConverterRoutesCase):
    """Приоритет рецепта преобразователя ("energy_recipe:<id>" в state.action_priority):
    выше обычного - преобразователь основной путь, обычный рецепт - запасной."""

    def prefer_converter(self, s):
        self.assertTrue(engine.set_action_priority(s, "energy_recipe:make_raw_b", 1).ok)

    def test_higher_priority_puts_the_converter_first(self):
        s = self.state(resources={"raw_a": 10})
        self.prefer_converter(s)
        self.assertEqual(engine.converter_preferred_resources(s), {"raw_b"})
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("energy", "make_raw_b")])

    def test_equal_priority_keeps_the_ordinary_recipe_first(self):
        s = self.state(resources={"raw_a": 10})
        engine.set_action_priority(s, "energy_recipe:make_raw_b", 0)
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("action", "m_make_b")])
        engine.set_action_priority(s, "m_make_b", -1)  # сравниваются сами числа
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("energy", "make_raw_b")])

    def test_ordinary_recipe_backs_up_a_preferred_converter(self):
        s = self.state(conv_auto=False, resources={"raw_a": 10})
        self.prefer_converter(s)
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("action", "m_make_b")])

    def test_both_paths_closed_means_the_resource_is_missing(self):
        s = self.state(conv_auto=False, mech_auto=False, resources={"raw_a": 10})
        self.prefer_converter(s)
        failure, _ = engine._plan_order(s, {"raw_b": 2})
        self.assertEqual((failure.resource_id, failure.reason, failure.missing), ("raw_b", "no_recipe", {"raw_b": 2}))

    def test_unavailable_high_priority_recipe_gives_way_to_an_available_one(self):
        # Приоритет выбирает только среди рецептов, доступных автокрафту: у m_make_b
        # приоритет выше, но механизм не автоматизирован - берётся преобразователь.
        s = self.state(mech_auto=False, resources={"raw_a": 10})
        engine.set_action_priority(s, "m_make_b", 5)
        self.assertEqual(self.step_kinds(s, {"raw_b": 2}), [("energy", "make_raw_b")])

    def test_preview_shows_the_preferred_converter(self):
        s = self.state(resources={"raw_a": 10})
        self.prefer_converter(s)
        (node,) = engine.autocraft_preview(s, {"raw_b": 2})["nodes"]
        self.assertEqual((node["energy_recipe_id"], node["action_id"]), ("make_raw_b", None))

    def test_setting_the_priority(self):
        s = GameState()
        self.assertEqual(engine.action_priority(s, "energy_recipe:make_raw_b"), 0)
        self.assertFalse(engine.set_action_priority(s, "energy_recipe:no_such", 1).ok)
        self.assertFalse(engine.set_action_priority(s, None, 1).ok)
        self.assertIn(engine.ENERGY_RECIPES["make_raw_b"]["name"], engine.set_action_priority(s, "energy_recipe:make_raw_b", 3).message)
        self.assertEqual(engine.action_priority(s, "energy_recipe:make_raw_b"), 3)
        engine.set_action_priority(s, "energy_recipe:make_raw_b", None)
        self.assertEqual(s.action_priority, {})


class ViewAndEstimateTests(OrdersTestCase):
    def test_orders_view(self):
        s = self.mech_state(resources={"raw_a": 5})
        engine.order_recipe(s, "m_quick", 2)
        (view,) = engine.orders_view(s)
        self.assertEqual(view["label"], "«Тест: быстрый» ×2")
        self.assertEqual(view["reserved"], {"raw_a": 2})
        self.assertEqual([(st["resource_id"], st["done"], st["total"]) for st in view["steps"]], [("m_out", 0, 2)])

    def test_eta_follows_assemblers_and_mechanism(self):
        s = self.mech_state()
        self.assertEqual(engine.autocraft_eta_turns(s, 3, "m_quick"), 3)      # как игрок: 1 за ход
        self.assertEqual(engine.autocraft_eta_turns(s, 2, "m_slow"), 6)       # 2 партии по 3 хода
        s.buildings["m_asm"] = 2
        self.assertEqual(engine.autocraft_eta_turns(s, 2, "m_slow"), None)    # сборщики - за ход
        s.buildings["m_asm_fast"] = 1
        self.assertIsNone(engine.autocraft_eta_turns(s, 100, "m_quick"))

    def test_preview_turns_for_a_long_recipe_follow_the_mechanism(self):
        s = self.mech_state(resources={"raw_a": 5})
        (node,) = engine.autocraft_preview(s, {"m_slow_out": 2})["nodes"]
        self.assertEqual((node["fires"], node["turns"]), (2, 6))

    def test_check_order_matches_order_recipe(self):
        s = self.mech_state(resources={"raw_a": 1})
        self.assertIsNone(engine.check_order(s, "m_quick", 1))
        self.assertEqual(engine.check_order(s, "m_quick", 5).reason, "no_recipe")
        self.assertEqual(s.resources["raw_a"], 1)  # проверка ничего не меняет

    def test_autocraft_failure_matches_pay_or_defer(self):
        # Регрессия: окно подтверждения рисовало дерево докрафта и давало «Запустить»,
        # хотя механизм не автоматизирован и заказ тут же отказывал.
        s = self.mech_state(resources={"raw_a": 5}, building_upgrades={})
        (node,) = engine.autocraft_preview(s, {"m_out": 2})["nodes"]
        self.assertEqual((node["action_id"], node["fires"], node["missing"], node["children"]), (None, 0, 2, []))
        failure = engine.autocraft_failure(s, {"m_out": 2}, "manual", "use_m_out")
        self.assertEqual(failure.reason, "no_recipe")
        self.assertFalse(engine.do_manual_action(s, "use_m_out").ok)
        s.building_upgrades["m_mech"] = ["m_auto"]
        self.assertIsNone(engine.autocraft_failure(s, {"m_out": 2}, "manual", "use_m_out"))
        self.assertTrue(engine.do_manual_action(s, "use_m_out").pending)
        # то же действие уже заказано - повторный клик не отказ, а «уже заказано»
        self.assertIsNone(engine.autocraft_failure(s, {"m_out": 2}, "manual", "use_m_out"))
        self.assertEqual(engine.autocraft_failure(s, {"m_out": 2}, "build", "big_thing").reason, "no_free_slots")

    def test_order_eta_adds_up_steps_sharing_one_mechanism(self):
        # Регрессия: карточка брала максимум по шагам, а шаги одного механизма идут
        # друг за другом: очистка 2×2 хода после медленного 2×3 хода = 10, а не 6.
        s = self.mech_state(resources={"raw_a": 5})
        self.assertEqual(engine.order_eta_turns(s, "m_refine", 2), 10)
        engine.order_recipe(s, "m_refine", 2)
        self.assertEqual(self.turns_until_done(s), 10)

    def test_order_eta_counts_orders_ahead_on_the_same_mechanism(self):
        s = self.mech_state(resources={"raw_a": 10}, buildings={"dispatcher": 1})
        self.assertEqual(engine.order_eta_turns(s, "m_quick", 2), 2)
        engine.order_recipe(s, "m_quick", 3)
        self.assertEqual(engine.order_eta_turns(s, "m_quick", 2), 5)  # 3 крафта первого заказа впереди
        engine.order_recipe(s, "m_quick", 2)
        self.assertEqual(self.turns_until_done(s), 5)

    def test_order_eta_waits_for_the_players_own_batch(self):
        s = self.mech_state(resources={"raw_a": 10, "m_slow_out": 1})
        engine.do_manual_action(s, "m_slow")  # игрок сам занял механизм на 3 хода
        # мгновенный рецепт механизм делает и рядом с партией игрока
        self.assertIsNone(engine.order_eta_turns(s, "m_quick", 1))
        # а партии заказа ждут: 3 хода игрока + 2 хода очистки
        self.assertEqual(engine.order_eta_turns(s, "m_refine", 1), 5)
        engine.order_recipe(s, "m_refine", 1)
        self.assertEqual(self.turns_until_done(s), 5)


class RunningEtaTests(OrdersTestCase):
    """Срок идущего заказа (панель «Заказы») и общий срок заказа из клика (окно
    подтверждения): пересчитываются каждый ход по фактическому прогрессу."""

    def test_running_order_counts_down_with_progress(self):
        s = self.mech_state(resources={"raw_a": 10})
        engine.order_recipe(s, "m_quick", 3)
        (order,) = s.orders
        etas = []
        while s.orders:
            etas.append(engine.running_order_eta_turns(s, order))
            engine.advance_turn(s)
        self.assertEqual(etas, [3, 2, 1])

    def test_running_batch_progress_is_subtracted(self):
        s = self.mech_state(resources={"raw_a": 10})
        engine.order_recipe(s, "m_slow", 2)  # 2 партии по 3 хода
        (order,) = s.orders
        self.assertEqual(engine.running_order_eta_turns(s, order), 6)
        self.turns(s, 1)  # первая партия отработала 1 ход из 3
        self.assertEqual(engine.running_order_eta_turns(s, order), 5)
        self.assertEqual(engine.orders_view(s)[0]["eta_turns"], 5)

    def test_orders_ahead_in_the_queue_count(self):
        s = self.mech_state(resources={"raw_a": 10}, buildings={"dispatcher": 1})
        engine.order_recipe(s, "m_quick", 3)
        engine.order_recipe(s, "m_quick", 2)
        self.assertEqual([v["eta_turns"] for v in engine.orders_view(s)], [3, 5])

    def test_click_order_total_eta(self):
        s = self.mech_state(resources={"raw_a": 10})
        # «Тест: из быстрого» - 2 быстрых выхода через механизм 1/ход
        self.assertEqual(engine.cost_eta_turns(s, {"m_out": 2}, "manual", "use_m_out"), 2)
        s.buildings["m_asm_fast"] = 1
        self.assertIsNone(engine.cost_eta_turns(s, {"m_out": 2}, "manual", "use_m_out"))


class ByproductTests(OrdersTestCase):
    def test_second_output_of_a_planned_craft_is_not_crafted_again(self):
        # Регрессия: m_a и m_b считались по отдельности - «разделение» шло дважды на
        # каждое «соединение» (вдвое больше сырья, лишние m_a/m_b оседали на складе).
        s = self.mech_state(resources={"raw_a": 10}, buildings={"m_asm_fast": 1})
        self.assertTrue(engine.order_recipe(s, "m_join", 2).ok)
        (order,) = s.orders
        self.assertEqual(order["reserved"], {"raw_a": 4})
        self.turns(s, 1)
        self.assertEqual((s.orders, s.resources["raw_a"], s.resources.get("e_top")), ([], 6, 2))
        self.assertEqual((s.resources.get("m_a", 0), s.resources.get("m_b", 0)), (0, 0))

    def test_preview_takes_the_second_output_from_the_planned_craft(self):
        s = self.mech_state(resources={"raw_a": 10}, buildings={"m_asm_fast": 1})
        nodes = engine.autocraft_preview(s, {"m_a": 1, "m_b": 1})["nodes"]
        self.assertEqual([(n["resource_id"], n["action_id"], n["fires"]) for n in nodes],
                         [("m_a", "m_split", 1), ("m_b", None, 0)])


class StorageSpaceTests(OrdersTestCase):
    """Место на складе под ИТОГ заказа (не под промежуточные): без места заказ не
    оформляется, а если место кончилось на ходу - заказ стоит с просьбой освободить."""

    def setUp(self):
        super().setUp()
        engine.RESOURCE_STORAGE = {
            "m_out": {"base_capacity": 5, "tiers": []},
            "e_top": {"base_capacity": 0, "tiers": []},
        }

    def test_order_bigger_than_free_space_is_refused(self):
        s = self.mech_state(resources={"raw_a": 20})
        result = engine.order_recipe(s, "m_quick", 6)
        self.assertFalse(result.ok)
        self.assertIn("не хватит места под «Тест-быстрый выход»: заказ выдаст 6, свободно 5", result.message)
        self.assertEqual((s.orders, s.resources["raw_a"]), ([], 20))
        self.assertEqual(engine.check_order(s, "m_quick", 6).reason, "no_storage_space")
        self.assertTrue(engine.order_recipe(s, "m_quick", 5).ok)

    def test_space_promised_to_earlier_orders_counts(self):
        s = self.mech_state(resources={"raw_a": 20}, buildings={"dispatcher": 1})
        self.assertTrue(engine.order_recipe(s, "m_quick", 3).ok)
        self.assertEqual(engine.check_order(s, "m_quick", 3).extra, {"need": 3, "free": 2})

    def test_intermediates_are_not_checked(self):
        # m_slow_out - промежуточный для очистки: склад под него (0) не мешает, он живёт в резерве.
        engine.RESOURCE_STORAGE["m_slow_out"] = {"base_capacity": 0, "tiers": []}
        s = self.mech_state(resources={"raw_a": 5})
        self.assertTrue(engine.order_recipe(s, "m_refine", 1).ok)
        self.assertEqual(self.turns_until_done(s), 5)
        self.assertEqual(s.resources.get("m_refined"), 1)

    def test_click_does_not_start_autocraft_without_space_for_its_result(self):
        s = self.mech_state(resources={"raw_a": 20}, buildings={"m_asm_fast": 1})
        result = engine.do_manual_action(s, "use_m_out")  # e_top вмещает 0
        self.assertFalse(result.ok)
        self.assertIn("не хватит места под «Тест-итог»", result.message)
        self.assertEqual(s.orders, [])
        self.assertEqual(engine.autocraft_failure(s, {"m_out": 2}, "manual", "use_m_out").reason, "no_storage_space")

    def test_order_pauses_when_space_runs_out_and_resumes(self):
        s = self.mech_state(resources={"raw_a": 20})
        self.assertTrue(engine.order_recipe(s, "m_quick", 3).ok)
        self.turns(s, 1)
        s.resources["m_out"] = 4  # склад забили чем-то ещё
        self.turns(s, 2)
        (order,) = s.orders
        self.assertEqual(order["steps"][0]["fires_done"], 1)  # стоит
        self.assertEqual(order["status"], "на складе нет места под «Тест-быстрый выход»: освободи ещё 2")
        s.resources["m_out"] = 0
        self.assertEqual(self.turns_until_done(s), 2)
        self.assertEqual(s.resources["m_out"], 3)


class CyclicRecipeTests(OrdersTestCase):
    """Рецепты, замкнутые в круг, автокрафт источником не берёт (прямое пожелание):
    «упаковать 2 быстрых выхода в блок» и «распаковать блок» - круг; дюп сырья A на
    преобразователе - круг из одного рецепта."""

    def setUp(self):
        super().setUp()
        engine.MANUAL_ACTIONS = {
            **engine.MANUAL_ACTIONS,
            "m_pack": _action("Тест: упаковать", "m_loc", {"m_out": 2}, {"m_block": 1}),
            # приоритетнее «быстрого» - раньше автокрафт брал бы его для m_out
            "m_unpack": _action("Тест: распаковать", "m_loc", {"m_block": 1}, {"m_out": 2}, priority=5),
            "use_block": _action("Тест: из блока", "m_loc", {"m_block": 1}, {"e_top": 1}),
        }
        engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, "m_block": "Тест-блок"}
        engine.ENERGY_RECIPES = {**engine.ENERGY_RECIPES, "dupe_raw": {
            "name": "Тест: удвоение", "description": "Тест.", "requires_tech": None, "threshold": 5,
            "consumes": {"raw_a": 1}, "produces": {"raw_a": 2},
        }}
        engine.ENERGY_RECIPE_FOR_RESOURCE = fixtures.build_energy_recipe_for_resource(engine.ENERGY_RECIPES)

    def test_recipes_of_a_cycle_are_found(self):
        cyclic = engine.autocraft_cyclic_recipes()
        self.assertTrue({"m_pack", "m_unpack", "energy_recipe:dupe_raw"} <= cyclic)
        self.assertFalse({"m_quick", "use_block"} & cyclic)

    def test_a_cyclic_recipe_is_never_a_source(self):
        # m_out делается «быстрым» из сырья, хотя «распаковать» приоритетнее.
        s = self.mech_state(resources={"raw_a": 5, "m_block": 3}, buildings={"m_asm_fast": 1})
        self.assertTrue(engine.do_manual_action(s, "use_m_out").pending)
        self.turns(s, 1)
        self.assertEqual((s.resources["raw_a"], s.resources["m_block"], s.resources.get("e_top")), (3, 3, 1))

    def test_shortage_names_the_cycle(self):
        s = self.mech_state(resources={"raw_a": 10, "m_out": 4}, buildings={"m_asm_fast": 1})
        result = engine.order_recipe(s, "use_block", 1)
        self.assertFalse(result.ok)
        self.assertIn("Не хватает: Тест-блок 1.", result.message)
        self.assertIn("Автокрафт не делает «Тест-блок» (рецепт «Тест: упаковать» замкнут в круг)", result.message)
        self.assertIn("замкнут в круг", engine.describe_autocraft_failure(engine.check_order(s, "use_block", 1)))

    def test_a_cyclic_recipe_can_still_be_ordered_directly(self):
        s = self.mech_state(resources={"m_out": 4}, buildings={"m_asm_fast": 1})
        self.assertTrue(engine.order_recipe(s, "m_pack", 2).ok)
        self.turns(s, 1)
        self.assertEqual((s.orders, s.resources.get("m_block")), ([], 2))


class AutomationRuleVersusOrderTests(OrdersTestCase):
    def test_rule_on_a_long_recipe_does_not_starve_an_order_on_the_same_mechanism(self):
        # Регрессия: правило без условий на многоходовой рецепт перезапускало себя сразу,
        # как партия доигрывала (раньше второго прохода заказов), и заказ на том же
        # механизме ждал «механизм занят» вечно, держа резерв и слот процессора.
        s = self.mech_state(resources={"raw_a": 30}, buildings={"hub": 1})
        self.assertTrue(engine.add_automation_rule(s, "hub", "manual_action", "m_slow", []).ok)
        self.assertTrue(engine.order_recipe(s, "m_refine", 1).ok)
        self.assertIsNotNone(self.turns_until_done(s))
        self.assertEqual(s.resources.get("m_refined"), 1)
        self.turns(s, 3)
        self.assertIn("m_slow", s.manual_jobs)  # правило работает дальше, когда механизм свободен


class FinishedReportTests(OrdersTestCase):
    """report.orders_finished - по нему UI сообщает о готовом заказе, в том числе
    выполненном в тот же ход (прогресс в панели «Заказы» такой заказ не показывает)."""

    @staticmethod
    def finished_until_done(s, limit=10):
        finished = []
        for _ in range(limit):
            finished += engine.advance_turn(s).orders_finished
            if not s.orders:
                break
        return finished

    def test_instant_recipe_order_reports_its_result(self):
        s = self.mech_state(resources={"raw_a": 30}, buildings={"m_asm_fast": 1})
        order_id = s.next_order_id
        engine.order_recipe(s, "m_quick", 20)
        report = engine.advance_turn(s)
        self.assertEqual(report.orders_finished, [{
            "id": order_id, "kind": "recipe", "target_id": "m_quick", "label": "«Тест: быстрый» ×20",
            "gained": {"m_out": 20}, "autocraft": True,
        }])

    def test_recipe_result_excludes_leftover_intermediates(self):
        s = self.mech_state(resources={"raw_a": 10}, buildings={"m_asm_fast": 1})
        engine.order_recipe(s, "m_join", 1)
        s.orders[0]["reserved"]["m_a"] = 1  # остаток промежуточного - на склад, но не «результат»
        [entry] = self.finished_until_done(s)
        self.assertEqual(entry["gained"], {"e_top": 1})
        self.assertEqual(s.resources.get("m_a"), 1)

    def test_every_output_of_a_multi_output_recipe_is_the_result(self):
        s = self.mech_state(resources={"raw_a": 10}, buildings={"m_asm_fast": 1})
        engine.order_recipe(s, "m_split", 2)
        [entry] = self.finished_until_done(s)
        self.assertEqual(entry["gained"], {"m_a": 2, "m_b": 2})

    def test_click_order_reports_what_the_action_gave(self):
        s = self.mech_state(resources={"raw_a": 5})
        self.assertTrue(engine.do_manual_action(s, "use_m_out").pending)
        [entry] = self.finished_until_done(s)
        self.assertEqual((entry["kind"], entry["target_id"]), ("manual", "use_m_out"))
        self.assertEqual(entry["gained"], {"e_top": 1})
        self.assertTrue(entry["autocraft"])

    def test_click_that_only_waited_for_energy_is_not_autocraft(self):
        engine.MANUAL_ACTIONS = {
            **engine.MANUAL_ACTIONS, "spark": _action("Тест: искра", "yard", {"energy": 7}, {"yard_out": 1}),
        }
        s = GameState(buildings={"e_gen": 1, "e_bank": 1})
        self.assertTrue(engine.do_manual_action(s, "spark").pending)
        [entry] = self.finished_until_done(s)
        self.assertEqual(entry["gained"], {"yard_out": 1})
        self.assertFalse(entry["autocraft"])

    def test_order_that_failed_at_the_end_is_not_reported(self):
        s = self.mech_state(resources={"raw_a": 5, "raw_b": 1})
        engine.build(s, "big_thing")
        s.buildings["big_thing"] = 1  # max_count 1 - финальное действие откажет
        finished = []
        for _ in range(4):
            finished += engine.advance_turn(s).orders_finished
        self.assertEqual(finished, [])
        self.assertEqual(len(s.orders), 1)


if __name__ == "__main__":
    unittest.main()
