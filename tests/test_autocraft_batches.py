"""Автокрафт поверх "медленных" источников, которые идут ПАРТИЯМИ, а не
прокрутками: крафт за энергию (energy_jobs) и многоходовые ручные действия
(manual_jobs). См. game/engine.py: _start_energy_batches, _commit_autocraft_plan
(статус "waiting")."""

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402


class FixtureTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def add_actions(self, actions: dict, resources: dict | None = None, unlock: list | None = None):
        """Доливает в ПОДМЕНЁННЫЙ контент тестовые действия/ресурсы и открывает
        автокрафт нужных ресурсов на automation_hub. Работает с копиями - фикстура
        общая для всех тестов, а cleanup patched_engine_content всё равно вернёт
        engine исходные (реальные) значения."""
        engine.MANUAL_ACTIONS = {**copy.deepcopy(engine.MANUAL_ACTIONS), **actions}
        if resources:
            engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, **resources}
        if unlock:
            engine.BUILDINGS = copy.deepcopy(engine.BUILDINGS)
            engine.BUILDINGS["automation_hub"]["unlocks_autocraft"] += unlock


def run_turns(s: GameState, done, limit: int = 30) -> int:
    """Крутит advance_turn (с полным запасом энергии на каждый ход), пока done(s)
    не станет истинным - возвращает число прошедших ходов."""
    for turn in range(1, limit + 1):
        s.resources["energy"] = 100
        engine.advance_turn(s)
        if done(s):
            return turn
    raise AssertionError(f"не завершилось за {limit} ходов")


# Мгновенный рецепт поверх крафта за энергию, которому нужно НЕСКОЛЬКО партий
# (make_raw_b даёт 1 raw_b за партию) - приоритет выше assemble_capacitor, чтобы
# автокрафт конденсатора выбрал именно его.
BULK_CAPACITOR = {
    "assemble_capacitor_bulk": {
        "name": "Собрать конденсатор из трёх B", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_b": 3}, "gain": {"capacitor": 1}, "priority": 10,
    },
    "use_capacitor": {
        "name": "Пустить конденсатор в дело", "requires_tech": None, "location": "workshop_loc",
        "cost": {"capacitor": 1}, "gain": {"test_top": 1},
    },
}


class EnergyBatchesTests(FixtureTestCase):
    """Автокрафт запускает партии крафта за энергию на ВСЕХ свободных
    преобразователях сразу (сколько нужно), а не одну за раз."""

    def test_starts_one_batch_per_free_converter(self):
        s = GameState(buildings={"automation_hub": 1, "energy_converter": 3})
        failure = engine.ensure_and_pay(s, {"raw_b": 3})
        self.assertEqual(failure.reason, "in_progress")
        self.assertEqual([j["recipe_id"] for j in s.energy_jobs], ["make_raw_b"] * 3)

    def test_does_not_start_more_batches_than_needed(self):
        s = GameState(buildings={"automation_hub": 1, "energy_converter": 5})
        engine.ensure_and_pay(s, {"raw_b": 2})
        self.assertEqual(len(s.energy_jobs), 2)

    def test_counts_stock_and_already_running_batches(self):
        s = GameState(resources={"raw_b": 1}, buildings={"automation_hub": 1, "energy_converter": 5})
        self.assertTrue(engine.start_energy_recipe(s, "make_raw_b").ok)  # игрок сам запустил одну
        engine.ensure_and_pay(s, {"raw_b": 4})
        # 1 на складе + 1 в работе - нужно ещё 2, а не 4 и не 3.
        self.assertEqual(len(s.energy_jobs), 3)

    def test_uses_guaranteed_minimum_of_a_range_produce(self):
        # make_variable даёт 3..7 - на 7 штук считаем по нижней границе: 3 партии.
        s = GameState(buildings={"automation_hub": 1, "energy_converter": 5})
        engine.ensure_and_pay(s, {"variable_output": 7})
        self.assertEqual([j["recipe_id"] for j in s.energy_jobs], ["make_variable"] * 3)

    def test_material_limits_batches_without_failing(self):
        # upgrade_matter ест raw_a:5 за партию - на 12 raw_a влезает только 2 партии
        # из 3 нужных; это не ошибка, третья стартует, когда материал появится.
        s = GameState(resources={"raw_a": 12}, buildings={"automation_hub": 1, "matter_generator": 3})
        failure = engine.ensure_and_pay(s, {"upgraded_matter": 3})
        self.assertEqual(failure.reason, "in_progress")
        self.assertEqual(len(s.energy_jobs), 2)
        self.assertEqual(s.resources["raw_a"], 2)

    def test_leftover_batches_start_as_soon_as_converters_free_up(self):
        # 2 преобразователя, нужно 3 партии: 2 сразу, третья - в тот же ход, когда
        # первые две закончились (без хода простоя). make_raw_b: порог 10 при
        # лимите 4/ход - 3 хода на партию, итого 6 ходов.
        self.add_actions(BULK_CAPACITOR, resources={"test_top": "Тестовый итог"})
        s = GameState(buildings={"automation_hub": 1, "energy_converter": 2})
        result = engine.do_manual_action(s, "assemble_capacitor_bulk")
        self.assertTrue(result.pending, result.message)
        self.assertEqual(len(s.energy_jobs), 2)

        max_jobs = 0

        def done(state):
            nonlocal max_jobs
            max_jobs = max(max_jobs, len(state.energy_jobs))
            return state.resources.get("capacitor", 0) >= 1

        self.assertEqual(run_turns(s, done), 6)
        self.assertLessEqual(max_jobs, 2)
        self.assertEqual(s.resources.get("raw_b", 0), 0)
        self.assertEqual(s.pending_actions, [])

    def test_instant_recipe_over_energy_batches_does_not_park_a_stuck_job(self):
        # Регрессия: раньше мгновенный рецепт поверх ещё не готового крафта за
        # энергию заводил СВОЮ фоновую задачу автокрафта. По ресурсу с задачей спрос
        # дальше вниз не идёт, поэтому после первой партии raw_b вторую никто не
        # запускал - задача висела вечно. Теперь рецепт просто ждёт (без задачи и
        # без слота), а повторная попытка каждый ход заново пускает спрос вниз.
        self.add_actions(BULK_CAPACITOR, resources={"test_top": "Тестовый итог"}, unlock=["capacitor"])
        s = GameState(buildings={"automation_hub": 1, "energy_converter": 1})
        result = engine.do_manual_action(s, "use_capacitor")
        self.assertTrue(result.pending, result.message)
        self.assertEqual(s.autocraft_jobs, {})
        self.assertEqual(len(s.energy_jobs), 1)

        run_turns(s, lambda state: state.resources.get("test_top", 0) >= 1)
        self.assertEqual(s.resources.get("capacitor", 0), 0)
        self.assertEqual(s.pending_actions, [])
        self.assertEqual(s.autocraft_jobs, {})


if __name__ == "__main__":
    unittest.main()
