"""
Энергия в цене рецепта и ожидание её притока (на заказах автокрафта).

- Заказ не отклоняется из-за нехватки энергии: его шаги каждый ход делают столько
  прокруток, на сколько хватает накопленного (engine._energy_wait_failure,
  _aggregate_autocraft_demand "energy_wait").
- Ручной клик, которому не хватает ТОЛЬКО энергии, тоже ждёт (заказ без шагов,
  процессор ему не нужен).
- Ждать бессмысленно - сразу отказ: нет ни одного источника энергии или один
  платёж больше ёмкости хранилищ.
- Преобразователи тратят энергию ПОСЛЕДНИМИ в ходу и не трогают то, что копят
  заказы (engine.energy_reserved).
- Сетка: энергия в cost сверх узора (валидатор), пачка «×N» останавливается на
  итерации, которая ушла ждать.

Мини-контент добавляется поверх фикстуры в setUp (копии словарей, cleanup
patched_*_content возвращает исходные). Мгновенные рецепты - в механизме e_loc за
безлимитным сборщиком, многоходовой - в e_loc2 за «Автоматизацией».

Запуск: python -m unittest tests.test_energy_wait -v
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

EXTRA_NAMES = {
    "e_ingot": "Тест-слиток", "e_core": "Тест-ядро", "e_spark": "Тест-искра",
    "e_slow": "Тест-медленный", "e_big": "Тест-большой", "grid_energy_output": "Тест-сеточный выход",
}


def _building(name, **extra):
    b = {
        "name": name, "description": f"{name} (тест энергии).", "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
    }
    b.update(extra)
    return b


EXTRA_BUILDINGS = {
    "e_gen": _building("Тест-генератор", produces={"energy": 10}),
    "e_fuel_gen": _building("Тест-топливный генератор", produces={"energy": 10}, consumes={"raw_b": 1}),
    "e_sun": _building("Тест-солнце", produces={"energy": 10}, active_period="day"),
    "e_bank": _building("Тест-банк энергии", energy_storage=50),
    # Процессор и безлимитный сборщик механизма e_loc - мгновенные рецепты.
    "e_hub": _building(
        "Тест-хаб", compute_power=1000, autocraft_slots=5,
        autocraft_assembler={"location": "e_loc", "per_turn": None},
    ),
    "e_mech": _building("Тест-механизм", upgrades=[{
        "id": "e_insulation", "name": "Изоляция", "description": "-50% энергии рецептов механизма.",
        "type": "energy_efficiency", "value": 0.5, "requires_tech": None, "cost": {},
    }]),
    # Механизм многоходового рецепта: «Автоматизация» - как игрок, со своей длительностью.
    "e_mech2": _building("Тест-медленный механизм", upgrades=[{
        "id": "e2_auto", "name": "Автоматизация", "description": "Автокрафт рецептов механизма.",
        "type": "autocraft", "value": 1, "requires_tech": None, "cost": {},
    }]),
}

EXTRA_LOCATIONS = {
    "e_loc": {"name": "Тест-механизм", "requires_tech": None, "requires_building": "e_mech"},
    "e_loc2": {"name": "Тест-медленный механизм", "requires_tech": None, "requires_building": "e_mech2"},
}


def _action(name, location, cost, gain, **extra):
    a = {"name": name, "requires_tech": None, "location": location, "cost": cost, "gain": gain}
    a.update(extra)
    return a


EXTRA_ACTIONS = {
    # Рецепт механизма: постройка e_mech наследуется от локации e_loc.
    "make_ingot": _action("Тест: слиток", "e_loc", {"raw_a": 1, "energy": 10}, {"e_ingot": 1}),
    "make_core": _action("Тест: ядро", "yard", {"e_ingot": 2, "energy": 5}, {"e_core": 1}),
    "spark_click": _action("Тест: искра", "yard", {"energy": 7}, {"e_spark": 1}),
    "spark_with_raw": _action("Тест: искра с сырьём", "yard", {"energy": 7, "raw_a": 3}, {"e_spark": 1}),
    "big_energy": _action("Тест: большой", "yard", {"energy": 60}, {"e_big": 1}),
    "slow_ingot": _action("Тест: медленный", "e_loc2", {"raw_a": 1, "energy": 10}, {"e_slow": 1}, duration_turns=3),
    "use_slow": _action("Тест: из медленного", "yard", {"e_slow": 1}, {"e_spark": 1}),
}


def _install(module, actions=None):
    """Добавляет мини-контент в копии словарей модуля (engine или web_ui)."""
    module.MANUAL_ACTIONS = {**module.MANUAL_ACTIONS, **(actions or EXTRA_ACTIONS)}
    module.BUILDINGS = {**copy.deepcopy(module.BUILDINGS), **copy.deepcopy(EXTRA_BUILDINGS)}
    module.LOCATIONS = {**module.LOCATIONS, **EXTRA_LOCATIONS}
    module.RESOURCE_NAMES = {**module.RESOURCE_NAMES, **EXTRA_NAMES}


def run_turns(s: GameState, n: int) -> None:
    for _ in range(n):
        engine.advance_turn(s)


def order(s: GameState, cost: dict):
    """Заказ «выдать cost на склад» (вид recipe) - как в tests/test_orders.py."""
    return engine._place_order(s, "recipe", "_test", "тест", cost)


class EnergyWaitEngineTests(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        _install(engine)

    def state(self, **kw):
        buildings = {"e_gen": 1, "e_bank": 1, "e_hub": 1, "e_mech": 1, "e_mech2": 1}
        buildings.update(kw.pop("buildings", {}))
        return GameState(buildings=buildings, building_upgrades={"e_mech2": ["e2_auto"]}, **kw)

    # --- заказ ждёт энергию ---

    def test_order_waits_for_energy_instead_of_failing(self):
        s = self.state(resources={"raw_a": 10, "energy": 5})
        failure, placed = order(s, {"e_ingot": 3})
        self.assertIsNone(failure)
        self.assertEqual(placed["steps"][0]["fires_total"], 3)
        self.assertEqual(s.resources["raw_a"], 7)  # сырьё сразу изъято в резерв заказа

    def test_each_turn_crafts_as_much_as_the_energy_allows(self):
        s = self.state(resources={"raw_a": 10, "energy": 5})
        _, placed = order(s, {"e_ingot": 3})
        run_turns(s, 1)  # 5 + 10 = 15 -> одна прокрутка, остаток 5
        self.assertEqual(placed["steps"][0]["fires_done"], 1)
        self.assertEqual(s.resources["energy"], 5)
        run_turns(s, 2)
        self.assertEqual(s.resources.get("e_ingot"), 3)
        self.assertEqual(s.orders, [])
        self.assertIsInstance(s.resources["e_ingot"], int)

    def test_action_on_top_of_energy_chain_completes_by_itself(self):
        # make_core: 2 слитка (по 10 энергии) + 5 энергии самому ядру.
        s = self.state(resources={"raw_a": 10, "energy": 0})
        result = engine.do_manual_action(s, "make_core")
        self.assertTrue(result.ok and result.pending, result.message)
        for _ in range(10):
            engine.advance_turn(s)
            if s.resources.get("e_core"):
                break
        self.assertEqual(s.resources.get("e_core"), 1)
        self.assertEqual(s.orders, [])
        self.assertEqual(s.resources["raw_a"], 8)

    def test_whole_order_may_exceed_storage_if_each_craft_fits(self):
        # 10 слитков = 100 энергии при ёмкости 50 - руками по частям это возможно.
        s = self.state(resources={"raw_a": 20, "energy": 0})
        self.assertIsNone(order(s, {"e_ingot": 10})[0])
        run_turns(s, 10)
        self.assertEqual(s.resources.get("e_ingot"), 10)

    def test_multi_turn_recipe_waits_for_energy_then_starts(self):
        s = self.state(resources={"raw_a": 5, "energy": 0})
        result = engine.do_manual_action(s, "use_slow")
        self.assertTrue(result.pending, result.message)
        self.assertEqual(s.manual_jobs, {})  # на партию (10 энергии) пока не хватает
        engine.advance_turn(s)  # +10 -> заказ запускает партию
        self.assertIn("slow_ingot", s.manual_jobs)
        run_turns(s, 3)
        self.assertEqual(s.resources.get("e_spark"), 1)
        self.assertEqual(s.orders, [])

    def test_mechanism_energy_efficiency_speeds_up_the_wait(self):
        s = self.state(resources={"raw_a": 10, "energy": 0})
        engine.purchase_building_upgrade(s, "e_mech", "e_insulation")
        self.assertEqual(engine.effective_manual_action_stats("make_ingot", s)[0]["energy"], 5)
        _, placed = order(s, {"e_ingot": 4})
        run_turns(s, 1)  # 10 энергии -> две прокрутки по 5
        self.assertEqual(placed["steps"][0]["fires_done"], 2)

    def test_fractional_energy_per_fire(self):
        actions = {**EXTRA_ACTIONS, "make_ingot": _action("Тест: слиток", "e_loc", {"raw_a": 1, "energy": 2.5}, {"e_ingot": 1})}
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, **actions}
        s = self.state(resources={"raw_a": 10, "energy": 0})
        _, placed = order(s, {"e_ingot": 9})
        run_turns(s, 1)  # 10 / 2.5 = ровно 4 прокрутки, без дрейфа float
        self.assertEqual(placed["steps"][0]["fires_done"], 4)
        self.assertEqual(placed["reserved"]["e_ingot"], 4)
        self.assertIsInstance(placed["reserved"]["e_ingot"], int)
        self.assertGreaterEqual(s.resources["energy"], 0)

    # --- когда ждать бессмысленно ---

    def test_no_energy_source_fails_fast(self):
        s = self.state(resources={"raw_a": 10, "energy": 0}, buildings={"e_gen": 0})
        failure, placed = order(s, {"e_ingot": 1})
        self.assertEqual((failure.reason, placed), ("no_energy_source", None))
        self.assertIn("источника энергии", engine.describe_autocraft_failure(failure))
        self.assertEqual(s.resources["raw_a"], 10)

    def test_disabled_generator_is_not_a_source(self):
        s = self.state(resources={"energy": 0, "raw_b": 5}, buildings={"e_gen": 0, "e_fuel_gen": 1})
        self.assertTrue(engine.has_energy_source(s))
        engine.set_building_active_count(s, "e_fuel_gen", 0)
        self.assertFalse(engine.has_energy_source(s))

    def test_solar_counts_as_a_source_at_night(self):
        s = self.state(buildings={"e_gen": 0, "e_sun": 1})
        night = next(t for t in range(200) if not engine.is_daytime(GameState(turn=t)))
        s.turn = night
        self.assertFalse(engine.is_daytime(s))
        self.assertTrue(engine.has_energy_source(s))

    def test_single_payment_over_storage_fails_fast(self):
        s = self.state(resources={"energy": 0})
        failure, _ = order(s, {"energy": 60})
        self.assertEqual(failure.reason, "energy_over_capacity")
        self.assertIn("вмещают только 50", engine.describe_autocraft_failure(failure))
        result = engine.do_manual_action(s, "big_energy")
        self.assertFalse(result.ok)
        self.assertIn("вмещают только 50", result.message)
        self.assertEqual(s.orders, [])

    def test_per_fire_energy_over_storage_fails_fast_in_a_chain(self):
        actions = {**EXTRA_ACTIONS, "make_ingot": _action("Тест: слиток", "e_loc", {"raw_a": 1, "energy": 70}, {"e_ingot": 1})}
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, **actions}
        s = self.state(resources={"raw_a": 10, "energy": 0})
        self.assertEqual(order(s, {"e_ingot": 1})[0].reason, "energy_over_capacity")

    # --- ручной клик ---

    def test_manual_click_waits_for_energy_without_a_processor(self):
        s = self.state(resources={"energy": 3}, buildings={"e_hub": 0})
        result = engine.do_manual_action(s, "spark_click")
        self.assertTrue(result.ok and result.pending, result.message)
        self.assertIn("ждёт энергию", result.message)
        self.assertTrue(s.orders[0]["waits_energy"])
        self.assertEqual(s.orders[0]["steps"], [])  # слот заказов такому не нужен
        engine.advance_turn(s)  # 3 + 10 = 13 >= 7
        self.assertEqual(s.resources.get("e_spark"), 1)
        self.assertEqual(s.resources["energy"], 6)
        self.assertEqual(s.orders, [])

    def test_waiting_click_keeps_its_other_materials_in_reserve(self):
        s = self.state(resources={"energy": 0, "raw_a": 3}, buildings={"e_hub": 0})
        self.assertTrue(engine.do_manual_action(s, "spark_with_raw").pending)
        self.assertEqual(s.resources["raw_a"], 0)  # никто их не перехватит, пока ждём энергию
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("e_spark"), 1)

    def test_manual_click_still_fails_when_something_else_is_short(self):
        s = self.state(resources={"energy": 3, "raw_a": 1}, buildings={"e_hub": 0})
        result = engine.do_manual_action(s, "spark_with_raw")
        self.assertFalse(result.ok)
        self.assertEqual(s.orders, [])

    def test_cancel_a_waiting_click(self):
        s = self.state(resources={"energy": 0}, buildings={"e_hub": 0})
        engine.do_manual_action(s, "spark_click")
        self.assertTrue(engine.cancel_order(s, s.orders[0]["id"]).ok)
        self.assertEqual(s.orders, [])
        engine.advance_turn(s)
        self.assertIsNone(s.resources.get("e_spark"))

    # --- преобразователь берёт только остаток ---

    def test_converter_gets_only_what_orders_left(self):
        s = self.state(resources={"raw_a": 10, "energy": 0}, buildings={"energy_converter": 1})
        self.assertTrue(engine.start_energy_recipe(s, "make_raw_b").ok)
        _, placed = order(s, {"e_ingot": 2})
        engine.advance_turn(s)  # +10: всё забирает прокрутка слитка
        self.assertEqual(placed["steps"][0]["fires_done"], 1)
        self.assertEqual(s.energy_jobs[0]["progress"], 0)

    def test_waiting_manual_click_beats_the_converter(self):
        s = self.state(resources={"energy": 0}, buildings={"e_hub": 0, "energy_converter": 1})
        self.assertTrue(engine.start_energy_recipe(s, "make_raw_b").ok)
        engine.do_manual_action(s, "spark_click")
        engine.advance_turn(s)  # +10: 7 - клику, преобразователю остаток 3 (лимит 4)
        self.assertEqual(s.resources.get("e_spark"), 1)
        self.assertEqual(s.energy_jobs[0]["progress"], 3)

    def test_converter_does_not_eat_what_a_waiting_click_accumulates(self):
        # Клику нужно 25 за раз при +10/ход; безлимитный преобразователь без
        # резерва съедал бы каждый ход всё, что не дотянуло до 25.
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, "spark_25": _action("Тест: 25", "yard", {"energy": 25}, {"e_spark": 1})}
        s = self.state(resources={"energy": 0}, buildings={"e_hub": 0, "mega_converter": 1})
        self.assertTrue(engine.start_energy_recipe(s, "make_raw_b").ok)
        engine.do_manual_action(s, "spark_25")
        self.assertEqual(engine.energy_reserved(s), 25)
        run_turns(s, 2)
        self.assertEqual(s.resources["energy"], 20)  # копится, преобразователь не тронул
        self.assertEqual(s.energy_jobs[0]["progress"], 0)
        engine.advance_turn(s)  # 30 >= 25: действие выполнено, остаток 5 - преобразователю
        self.assertEqual(s.resources.get("e_spark"), 1)
        self.assertEqual(s.energy_jobs[0]["progress"], 5)
        self.assertEqual(engine.energy_reserved(s), 0)

    def test_converter_does_not_starve_an_order(self):
        # Прокрутке слитка нужно 10, приток 6 - без резерва за прокрутку
        # преобразователь забирал бы все 6 каждый ход, и заказ не сдвинулся бы.
        s = self.state(resources={"raw_a": 10, "energy": 0}, buildings={"e_gen": 0, "e_fuel_gen": 1, "mega_converter": 1})
        s.resources["raw_b"] = 100
        engine.BUILDINGS["e_fuel_gen"] = {**engine.BUILDINGS["e_fuel_gen"], "produces": {"energy": 6}}
        self.assertTrue(engine.start_energy_recipe(s, "make_raw_b").ok)
        order(s, {"e_ingot": 1})
        run_turns(s, 2)  # 6 -> 12 >= 10
        self.assertEqual(s.resources.get("e_ingot"), 1)

    # --- подсказки для интерфейса ---

    def test_preview_marks_energy_as_waited_with_eta(self):
        s = self.state(resources={"raw_a": 5, "energy": 0})
        preview = engine.autocraft_preview(s, {"e_ingot": 2})
        ingot = preview["nodes"][0]
        energy = next(c for c in ingot["children"] if c["resource_id"] == "energy")
        self.assertTrue(energy["energy_wait"])
        self.assertEqual(energy["turns"], 2)  # 20 энергии при +10/ход

    def test_orders_view_flags_orders_waiting_for_energy(self):
        s = self.state(resources={"raw_a": 5, "energy": 0}, buildings={"e_gen": 0, "e_sun": 1})
        order(s, {"e_ingot": 3})
        engine._process_orders(s, engine.ProductionReport(), {})
        (view,) = engine.orders_view(s)
        self.assertTrue(view["waits_energy"])
        self.assertEqual(view["status"], "ждёт энергию")


class EnergyWaitWebUiTests(unittest.TestCase):
    """craft_grid_view: пачка останавливается на итерации, которая ушла ждать."""

    def setUp(self):
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        import web_ui
        self.web_ui = web_ui
        actions = {**EXTRA_ACTIONS, "grid_energy": _action(
            "Тест: сеточный с энергией", "grid_loc", {"raw_a": 2, "energy": 25}, {"grid_energy_output": 1},
            pattern={"rows": ["A.", "A."], "key": {"A": "raw_a"}},
        )}
        _install(engine, actions)
        _install(web_ui, actions)
        self.placed = ["raw_a", None, "raw_a", None]

    def test_batch_stops_at_the_iteration_that_waits(self):
        s = GameState(resources={"raw_a": 10, "energy": 25}, buildings={"e_gen": 1, "e_bank": 1})
        out = self.web_ui.craft_grid_view(s, "grid_loc", self.placed, 3)
        # 1-я: сразу (25 -> 0, +10). 2-я: 10 < 25 -> ждёт, за ход 20 < 25 - стоп.
        self.assertTrue(out["ok"])
        self.assertTrue(out["waiting"])
        self.assertEqual(out["times_done"], 1)
        self.assertIn("1 из 3", out["message"])
        self.assertEqual(len(s.orders), 1)
        engine.advance_turn(s)  # 20 + 10 = 30 >= 25
        self.assertEqual(s.resources.get("grid_energy_output"), 2)

    def test_view_exposes_waiting_orders(self):
        s = GameState(resources={"energy": 0}, buildings={"e_gen": 1, "e_bank": 1})
        engine.do_manual_action(s, "spark_click")
        (entry,) = self.web_ui.autocraft_view(s)["orders"]
        self.assertTrue(entry["waits_energy"])
        self.assertEqual(entry["steps"], [])

    def test_preview_view_summary_marks_energy_wait(self):
        s = GameState(resources={"raw_a": 5, "energy": 0}, buildings={"e_gen": 1, "e_bank": 1, "e_hub": 1, "e_mech": 1})
        view = self.web_ui.autocraft_preview_view(s, "manual", "make_core")
        energy = next(r for r in view["summary"] if r["resource_id"] == "energy")
        self.assertTrue(energy["energy_wait"])
        self.assertEqual(energy["turns"], 3)  # 25 энергии (20 слиткам + 5 ядру) при +10/ход


class EnergyInGridCostValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()
        self.action = self.data["manual_actions"]["grid_line"] if "grid_line" in self.data["manual_actions"] else next(
            a for a in self.data["manual_actions"].values() if a.get("location") == "grid_loc" and a.get("pattern")
        )

    def test_energy_beyond_the_pattern_is_valid(self):
        self.action["cost"] = {**self.action["cost"], "energy": 12.5}
        self.assertEqual(cv.validate(self.data), [])

    def test_energy_cannot_be_placed_in_a_cell(self):
        self.action["pattern"]["key"]["A"] = "energy"
        errors = cv.validate(self.data)
        self.assertTrue(any("энергию нельзя положить в ячейку" in e for e in errors), errors)

    def test_items_still_must_match_the_pattern(self):
        self.action["cost"] = {"energy": 5}
        errors = cv.validate(self.data)
        self.assertTrue(any("обязаны совпадать" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
