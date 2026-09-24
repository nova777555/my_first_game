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


# Потребители delayed_output (craft_delayed: raw_a:2 -> delayed_output:5 за 3 хода,
# локация workshop_loc). refine_delayed - мгновенный рецепт поверх многоходового,
# long_refine - многоходовой поверх многоходового В ТОЙ ЖЕ локации.
LONG_TEST_ACTIONS = {
    "use_delayed": {
        "name": "Пустить затяжной выход в дело", "requires_tech": None, "location": "yard",
        "cost": {"delayed_output": 12}, "gain": {"test_top": 1},
    },
    "use_delayed_small": {
        "name": "Пустить немного затяжного выхода", "requires_tech": None, "location": "yard",
        "cost": {"delayed_output": 5}, "gain": {"test_top": 1},
    },
    "refine_delayed": {
        "name": "Очистить затяжной выход", "requires_tech": None, "location": "yard",
        "cost": {"delayed_output": 8}, "gain": {"test_mid": 1},
    },
    "use_refined": {
        "name": "Пустить очищенное в дело", "requires_tech": None, "location": "yard",
        "cost": {"test_mid": 1}, "gain": {"test_top": 1},
    },
}
LONG_TEST_RESOURCES = {"test_top": "Тестовый итог", "test_mid": "Тестовый полуфабрикат"}


class LongActionAutocraftTests(FixtureTestCase):
    """Многоходовые ручные действия в автокрафте: настоящий manual_job, партии по
    одной, следующая - сразу по завершении предыдущей."""

    def setUp(self):
        super().setUp()
        self.add_actions(LONG_TEST_ACTIONS, resources=LONG_TEST_RESOURCES, unlock=["delayed_output", "test_mid"])

    def test_starts_a_real_manual_job_for_the_shortfall(self):
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        failure = engine.ensure_and_pay(s, {"delayed_output": 5})
        self.assertEqual(failure.reason, "in_progress")
        job = s.manual_jobs["craft_delayed"]
        self.assertEqual((job["turns_total"], job["turns_done"]), (3, 0))
        self.assertTrue(job["autocraft"])
        self.assertEqual(s.resources["raw_a"], 8)  # cost партии списан сразу, как при клике
        self.assertEqual(s.autocraft_jobs, {})  # слот автокрафта партия не занимает

    def test_respects_autocraft_unlock(self):
        engine.BUILDINGS["automation_hub"]["unlocks_autocraft"].remove("delayed_output")
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        failure = engine.ensure_and_pay(s, {"delayed_output": 5})
        self.assertEqual(failure.reason, "not_unlocked")
        self.assertEqual(s.manual_jobs, {})

    def test_respects_compute_limit_on_the_whole_request(self):
        # 40 штук = 8 партий * raw_a:2 = 16 > compute_power 15 у automation_hub.
        s = GameState(resources={"raw_a": 100}, buildings={"automation_hub": 1})
        failure = engine.ensure_and_pay(s, {"delayed_output": 40})
        self.assertEqual(failure.reason, "insufficient_compute")
        self.assertEqual(s.manual_jobs, {})

    def test_runs_batches_back_to_back_until_enough(self):
        # 12 штук = 3 партии по 5; каждая 3 хода, следующая стартует в тот же ход,
        # когда закончилась предыдущая - итого ровно 9 ходов.
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        result = engine.do_manual_action(s, "use_delayed")
        self.assertTrue(result.pending, result.message)

        self.assertEqual(run_turns(s, lambda st: st.resources.get("test_top", 0) >= 1), 9)
        self.assertEqual(s.resources["delayed_output"], 15 - 12)
        self.assertEqual(s.resources["raw_a"], 10 - 3 * 2)
        self.assertEqual(s.manual_jobs, {})
        self.assertEqual(s.pending_actions, [])
        # Партии автокрафта - не "игрок выполнил действие" (как и мгновенные прокрутки).
        self.assertEqual(s.action_performed_count.get("craft_delayed", 0), 0)
        self.assertEqual(s.action_performed_count.get("use_delayed", 0), 1)

    def test_waits_while_location_is_busy_with_another_long_action(self):
        # craft_delayed_sibling (2 хода) в той же workshop_loc запущен игроком -
        # партия craft_delayed ждёт его, потом ещё 3 хода: итого 5.
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        self.assertTrue(engine.do_manual_action(s, "craft_delayed_sibling").ok)
        result = engine.do_manual_action(s, "use_delayed_small")
        self.assertTrue(result.pending, result.message)
        self.assertNotIn("craft_delayed", s.manual_jobs)

        self.assertEqual(run_turns(s, lambda st: st.resources.get("test_top", 0) >= 1), 5)

    def test_waits_for_players_own_job_of_the_same_action(self):
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        self.assertTrue(engine.do_manual_action(s, "craft_delayed").ok)
        result = engine.do_manual_action(s, "use_delayed_small")
        self.assertTrue(result.pending, result.message)
        self.assertFalse(s.manual_jobs["craft_delayed"].get("autocraft"))  # задача игрока не подменена

        self.assertEqual(run_turns(s, lambda st: st.resources.get("test_top", 0) >= 1), 3)
        self.assertEqual(s.resources["raw_a"], 8)  # вторую партию не запускали - хватило одной

    def test_instant_recipe_over_long_ingredient_waits_without_a_job(self):
        # refine_delayed (мгновенный, 8 шт.) поверх craft_delayed (5 за партию):
        # 2 партии по 3 хода, своей фоновой задачи у refine_delayed нет.
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        result = engine.do_manual_action(s, "use_refined")
        self.assertTrue(result.pending, result.message)
        self.assertEqual(s.autocraft_jobs, {})
        self.assertIn("craft_delayed", s.manual_jobs)

        self.assertEqual(run_turns(s, lambda st: st.resources.get("test_top", 0) >= 1), 6)
        self.assertEqual(s.resources["delayed_output"], 10 - 8)
        self.assertEqual(s.autocraft_jobs, {})

    def test_long_recipe_over_long_ingredient_in_the_same_location_does_not_deadlock(self):
        # long_refine и craft_delayed делят workshop_loc. Локацию должен первым
        # занять ингредиент (craft_delayed), а не его потребитель - иначе
        # потребитель ждал бы ингредиент, а ингредиент - свободную локацию.
        engine.MANUAL_ACTIONS["long_refine"] = {
            "name": "Долгая очистка", "requires_tech": None, "location": "workshop_loc",
            "cost": {"delayed_output": 5}, "gain": {"test_mid": 1}, "duration_turns": 2, "priority": 10,
        }
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        result = engine.do_manual_action(s, "use_refined")
        self.assertTrue(result.pending, result.message)
        self.assertEqual(list(s.manual_jobs), ["craft_delayed"])

        # 3 хода craft_delayed + 2 хода long_refine.
        self.assertEqual(run_turns(s, lambda st: st.resources.get("test_top", 0) >= 1), 5)
        self.assertEqual(s.manual_jobs, {})

    def test_preview_estimates_turns_as_batches_times_duration(self):
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        preview = engine.autocraft_preview(s, {"delayed_output": 12})
        node = preview["nodes"][0]
        self.assertEqual((node["action_id"], node["fires"], node["turns"]), ("craft_delayed", 3, 9))

    def test_cancelling_an_autocraft_batch_refunds_its_cost(self):
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        engine.ensure_and_pay(s, {"delayed_output": 5})
        self.assertTrue(engine.cancel_manual_job(s, "craft_delayed").ok)
        self.assertEqual(s.resources["raw_a"], 10)


class LongActionWebUiTests(FixtureTestCase):
    """tools/web_ui.py поверх тех же партий: пометка в панели "Долгие действия" и
    оценка ходов в окне подтверждения автокрафта."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        patch = fixtures.patched_content()  # заодно подменяет имена в web_ui
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        self.add_actions(LONG_TEST_ACTIONS, resources=LONG_TEST_RESOURCES, unlock=["delayed_output"])
        import web_ui
        self.web_ui = web_ui
        web_ui.MANUAL_ACTIONS = engine.MANUAL_ACTIONS  # восстановит cleanup patched_content

    def test_manual_jobs_view_marks_autocraft_batches(self):
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        self.assertTrue(engine.do_manual_action(s, "craft_delayed_sub").ok)  # игрок, sub_workshop
        engine.ensure_and_pay(s, {"delayed_output": 5})  # автокрафт, workshop_loc
        flags = {job["action_id"]: job["autocraft"] for job in self.web_ui.manual_jobs_view(s)}
        self.assertEqual(flags, {"craft_delayed_sub": False, "craft_delayed": True})

    def test_preview_summary_turns_for_long_recipe(self):
        s = GameState(resources={"raw_a": 10}, buildings={"automation_hub": 1})
        view = self.web_ui.autocraft_preview_view(s, "manual", "use_delayed")
        self.assertTrue(view["needs_confirm"])
        row = next(r for r in view["summary"] if r["resource_id"] == "delayed_output")
        self.assertEqual((row["fires"], row["turns"]), (3, 9))
        self.assertEqual(view["tree"][0]["turns"], 9)


if __name__ == "__main__":
    unittest.main()
