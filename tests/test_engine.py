"""
Тесты правил игры (game/engine.py). Работают на самодостаточной тестовой фикстуре
(tests/fixtures.py), а не на реальном game/content.json - переименование, удаление
или добавление сущностей в настоящей игре никак на них не влияет. Ничего не пишет
на диск - GameState() живёт только в памяти.

Запуск всех тестов проекта: python -m unittest discover tests -v
Запуск только этого файла:   python -m unittest tests.test_engine -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fixtures  # noqa: E402
from game import engine  # noqa: E402
from game.state import HISTORY_MAX_ENTRIES, GameState  # noqa: E402


def _order_now(state, cost):
    """Заказ под cost с немедленной отработкой шагов (как ход) - вместо прежнего
    мгновенного ensure_and_pay: None при успехе (результат выдан на склад и оплачен
    cost), иначе отказ."""
    failure, order = engine._place_order(state, "recipe", "_test", "тест", cost)
    if failure is not None:
        return failure
    engine._process_orders(state, engine.ProductionReport(), {})
    for resource_id, amount in cost.items():
        state.resources[resource_id] -= amount
    return None


class FixtureTestCase(unittest.TestCase):
    """Подменяет game.engine на фикстурный контент на время каждого теста."""

    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)


class AutocraftTests(FixtureTestCase):
    def test_simple_action_spends_and_grants(self):
        s = GameState(resources={"raw_a": 10})
        result = engine.do_manual_action(s, "gather_a")
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["raw_a"], 13)

    def test_autocraft_fails_cleanly_without_raw_materials(self):
        s = GameState(resources={"raw_a": 5, "raw_b": 100}, buildings={"automation_hub": 1})
        before = dict(s.resources)
        result = engine.do_manual_action(s, "craft_final")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources, before)

    def test_bare_gather_actions_are_not_conjurable(self):
        # регрессия: действия с пустой ценой не должны считаться "рецептом" -
        # иначе автокрафт мог бы бесплатно плодить сырьё в любой глубокой цепочке.
        recipe = engine.resource_recipe(GameState())
        self.assertNotIn("raw_a", recipe)
        self.assertNotIn("raw_b", recipe)
        self.assertIn("mid", recipe)
        self.assertIn("final", recipe)

    def test_resource_recipe_excludes_actions_flagged_excluded_from_autocraft(self):
        # "final" - единственный производитель craft_final (в отличие от "mid", которое
        # ещё даёт tech-запертый locked_action - тот не годится для этой проверки:
        # resource_recipe нарочно не смотрит на текущую доступность, см. её докстринг).
        engine.MANUAL_ACTIONS["craft_final"]["excluded_from_autocraft"] = True
        self.addCleanup(lambda: engine.MANUAL_ACTIONS["craft_final"].pop("excluded_from_autocraft", None))
        recipe = engine.resource_recipe(GameState())
        self.assertNotIn("final", recipe)

    def test_autocraft_does_not_backfill_an_action_flagged_excluded_from_autocraft(self):
        # excluded_from_autocraft делает "final" неотличимым для автокрафта от ресурса
        # без рецепта - заказ отклоняется сразу, ничего не изъято. Ритуалы фикстуры тоже
        # дают "final" (ритуал - запасной источник для автокрафта) - убираем их, чтобы
        # проверять только само действие.
        engine.RITUAL_RECIPES = {}
        s = GameState(resources={"raw_a": 100, "raw_b": 100}, buildings={"automation_hub": 1})
        engine.MANUAL_ACTIONS["craft_final"]["excluded_from_autocraft"] = True
        self.addCleanup(lambda: engine.MANUAL_ACTIONS["craft_final"].pop("excluded_from_autocraft", None))
        failure, order = engine._place_order(s, "recipe", "x", "тест", {"final": 1})
        self.assertEqual((failure.reason, order), ("no_recipe", None))
        self.assertEqual(s.resources["raw_a"], 100)

    def test_locked_action_requires_tech(self):
        s = GameState(resources={"raw_b": 10})
        result = engine.do_manual_action(s, "locked_action")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources.get("mid", 0), 0)

    def test_action_locked_until_required_building_is_working(self):
        s = GameState()
        self.assertFalse(engine.is_manual_action_unlocked("locked_by_building", s))
        result = engine.do_manual_action(s, "locked_by_building")
        self.assertFalse(result.ok)

        s.buildings["gen"] = 1
        self.assertTrue(engine.is_manual_action_unlocked("locked_by_building", s))
        result = engine.do_manual_action(s, "locked_by_building")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 1)

    def test_action_locked_again_if_required_building_breaks_entirely(self):
        s = GameState(buildings={"gen": 1}, broken={"gen": 1})  # единственная копия сломана
        self.assertFalse(engine.is_manual_action_unlocked("locked_by_building", s))

    def test_manual_action_failure_message_is_plain_without_any_processor(self):
        # craft_final стоит mid:2, raw_b:1 - без единого процессора в игре сообщение
        # должно звучать ровно как обычная нехватка при ручном крафте (см.
        # _pay_or_defer), без единого намёка на автокрафт/процессор/память.
        s = GameState(resources={"raw_b": 100})
        result = engine.do_manual_action(s, "craft_final")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Собрать изделие». Не хватает: Полуфабрикат 2.")

    def test_processor_alone_does_not_make_an_unautomated_recipe_visible(self):
        # Тот же craft_final, но в игре есть bare_processor (мощности с избытком) -
        # рецепт mid не автоматизирован, автокрафт его не видит: та же простая нехватка.
        s = GameState(resources={"raw_b": 100}, buildings={"bare_processor": 1})
        result = engine.do_manual_action(s, "craft_final")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Не хватает ресурсов на «Собрать изделие». Не хватает: Полуфабрикат 2.")

    def test_manual_action_failure_lists_all_missing_resources_directly_without_processor(self):
        # Без единого процессора недостача считается ПРЯМО по cost действия - оба
        # недостающих ресурса (mid и raw_b), без анализа рецептов.
        s = GameState(resources={})
        result = engine.do_manual_action(s, "craft_final")
        self.assertFalse(result.ok)
        self.assertEqual(
            result.message,
            "Не хватает ресурсов на «Собрать изделие». Не хватает: Полуфабрикат 2, Сырьё B 1.",
        )

class AutocraftReconvergentGraphTests(unittest.TestCase):
    """_discover_autocraft_graph/_aggregate_autocraft_demand/_commit_autocraft_plan -
    регрессия на "ромбы" (несколько рецептов делят один и тот же ресурс ниже по
    цепочке) и на цикл в рецептах. Использует СВОЙ маленький контент (не
    tests/fixtures.py) - обычная фикстура не годится, нужна конкретная форма графа."""

    def setUp(self):
        from game import engine
        self.engine = engine
        self._originals = {}

    def _patch(self, **names):
        for name, value in names.items():
            self._originals.setdefault(name, getattr(self.engine, name))
            setattr(self.engine, name, value)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._originals.items():
            setattr(self.engine, name, value)
        self._originals = {}

    def _diamond_content(self, depth: int):
        # Самоподобная "решётка из ромбов": raw (неограниченное сырьё) -> a1/b1, каждый
        # следующий уровень a{k}/b{k} требует ПО ОДНОМУ a{k-1} И b{k-1} - раньше такая
        # структура давала ЧИСТУЮ ЭКСПОНЕНТУ вызовов _ensure_available по глубине (см.
        # CLAUDE.md, "Известные ограничения" на момент до фикса 2026-09-10).
        resources = {"raw": "raw"}
        manual_actions = {}
        prev_a = prev_b = "raw"
        for k in range(1, depth + 1):
            a, b = f"a{k}", f"b{k}"
            resources[a] = a
            resources[b] = b
            manual_actions[f"make_{a}"] = {
                "id": f"make_{a}", "name": a, "location": "lat", "requires_tech": None,
                "cost": {prev_a: 1, prev_b: 1}, "gain": {a: 1}, "priority": 0,
            }
            manual_actions[f"make_{b}"] = {
                "id": f"make_{b}", "name": b, "location": "lat", "requires_tech": None,
                "cost": {prev_a: 1, prev_b: 1}, "gain": {b: 1}, "priority": 0,
            }
            prev_a, prev_b = a, b
        buildings = {
            "processor": {
                "id": "processor", "name": "CPU", "requires_tech": None, "cost": {},
                "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
                "compute_power": 10 ** 9, "autocraft_slots": 10 ** 6,
                # безлимитный сборщик локации рецептов решётки - весь заказ за ход
                "autocraft_assembler": {"location": "lat", "per_turn": None},
            },
        }
        self._patch(
            RESOURCE_NAMES=dict(resources), RESOURCE_FRACTIONAL={k: False for k in resources},
            MANUAL_ACTIONS=manual_actions, BUILDINGS=buildings,
        )

    def test_diamond_lattice_resolves_in_linear_time_not_exponential(self):
        depth = 22  # у старого алгоритма это заведомо не влезло бы в разумное время (2^22 вызовов)
        self._diamond_content(depth)
        s = GameState(buildings={"processor": 1}, resources={"raw": 10 ** 9})
        import time
        start = time.perf_counter()
        failure = _order_now(s, {f"a{depth}": 1})
        elapsed = time.perf_counter() - start
        self.assertIsNone(failure)
        self.assertEqual(s.resources.get(f"a{depth}", 0), 0)  # скрафтили и сразу списали как cost - см. ensure_and_pay
        self.assertLess(elapsed, 1.0)  # раньше глубина 14 уже занимала ~1.1 секунды

    def test_diamond_lattice_aggregates_shared_ancestor_demand_exactly(self):
        # a3 и b3 по построению симметричны и делят ВСЮ цепочку предков ниже уровня 3 -
        # a1/b1 нужны ОБОИМ (a2 и b2), значит общий спрос на них удваивается по-честному
        # (раздельное СЫРЬЁ на каждую единицу - агрегация избавляет от повторной РАБОТЫ
        # по выводу цепочки, а не от повторного РАСХОДА сырья, см. test выше на линейное
        # время). Запрос {a3, b3} по симметрии должен стоить РОВНО в 2 раза больше raw,
        # чем один {a3} - ни больше (двойной счёт), ни меньше (недосчитанный спрос).
        self._diamond_content(3)
        s_a = GameState(buildings={"processor": 1}, resources={"raw": 10 ** 6})
        self.assertIsNone(_order_now(s_a, {"a3": 1}))
        cost_a_alone = 10 ** 6 - s_a.resources["raw"]
        self.assertGreater(cost_a_alone, 0)

        s_both = GameState(buildings={"processor": 1}, resources={"raw": 10 ** 6})
        self.assertIsNone(_order_now(s_both, {"a3": 1, "b3": 1}))
        cost_both = 10 ** 6 - s_both.resources["raw"]

        self.assertEqual(cost_both, 2 * cost_a_alone)

    def test_recipe_cycle_is_treated_as_missing_recipe_not_infinite_recursion(self):
        # x -> y (стоит y) -> x (стоит x) - цикл. Должно вернуться "no_recipe" быстро,
        # а не уйти в бесконечную рекурсию/RecursionError.
        self._patch(
            RESOURCE_NAMES={"x": "x", "y": "y"}, RESOURCE_FRACTIONAL={"x": False, "y": False},
            MANUAL_ACTIONS={
                "make_x": {"id": "make_x", "name": "x", "location": "lat", "requires_tech": None,
                           "cost": {"y": 1}, "gain": {"x": 1}, "priority": 0},
                "make_y": {"id": "make_y", "name": "y", "location": "lat", "requires_tech": None,
                           "cost": {"x": 1}, "gain": {"y": 1}, "priority": 0},
            },
            BUILDINGS={
                "processor": {
                    "id": "processor", "name": "CPU", "requires_tech": None, "cost": {},
                    "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
                    "compute_power": 1000, "autocraft_slots": 100,
                    "autocraft_assembler": {"location": "lat", "per_turn": None},
                },
            },
        )
        s = GameState(buildings={"processor": 1}, resources={})
        failure = _order_now(s, {"x": 1})
        self.assertEqual(failure, self.engine.AutocraftFailure("x", "no_recipe", missing={"x": 1}))
        self.assertEqual(s.resources, {})


class AutocraftPreviewTests(FixtureTestCase):
    """engine.autocraft_preview - окно подтверждения перед запуском действия:
    "сколько и чего реально будет потрачено" (включая промежуточные ресурсы),
    без самой попытки автокрафта (см. CLAUDE.md, "Подтверждение автокрафта")."""

    def test_none_when_cost_is_already_fully_covered_by_stock(self):
        # mid:2, raw_b:1 - всё уже в наличии, докрафт не нужен вообще, значит и
        # подтверждение не нужно - действие мгновенное, без докрафта.
        s = GameState(resources={"mid": 2, "raw_b": 1}, buildings={"automation_hub": 1})
        self.assertIsNone(engine.autocraft_preview(s, {"mid": 2, "raw_b": 1}))

    def test_none_without_any_processor(self):
        # Недостача есть (mid:2, ничего нет), но автокрафт как система ещё не
        # "появился" (compute_capacity == 0) - показывать окно "сколько спишется
        # через докрафт" бессмысленно, действие просто провалится как обычно.
        s = GameState(resources={})
        self.assertIsNone(engine.autocraft_preview(s, {"mid": 2}))

    def test_tree_expands_intermediate_and_leaf_resources_with_fires(self):
        # craft_final: mid:2, raw_b:1. mid делается из raw_a (craft_mid: raw_a:4 -> mid:1).
        # Ничего в наличии нет - должно быть 2 корневых узла: mid (докрафт x2 через
        # craft_mid, раскрывается в raw_a:8 - лист, без своего рецепта) и raw_b (лист).
        s = GameState(resources={}, buildings={"automation_hub": 1})
        preview = engine.autocraft_preview(s, {"mid": 2, "raw_b": 1})
        self.assertIsNotNone(preview)
        by_id = {n["resource_id"]: n for n in preview["nodes"]}

        mid_node = by_id["mid"]
        self.assertEqual(mid_node["needed"], 2)
        self.assertEqual(mid_node["stock_before"], 0)
        self.assertEqual(mid_node["taken"], 0)
        self.assertEqual(mid_node["action_id"], "craft_mid")
        self.assertEqual(mid_node["fires"], 2)
        self.assertEqual(len(mid_node["children"]), 1)
        raw_a_node = mid_node["children"][0]
        self.assertEqual(raw_a_node["resource_id"], "raw_a")
        self.assertEqual(raw_a_node["needed"], 8)  # 4 сырья A на партию x2 партии
        self.assertIsNone(raw_a_node["action_id"])  # сырьё - дальше раскладывать некуда
        self.assertEqual(raw_a_node["fires"], 0)
        self.assertEqual(raw_a_node["children"], [])

        raw_b_node = by_id["raw_b"]
        self.assertEqual(raw_b_node["needed"], 1)
        self.assertIsNone(raw_b_node["action_id"])  # лист, без рецепта - докрафт не нужен

    def test_partially_stocked_resource_reports_taken_and_still_expands_the_rest(self):
        # mid:2, из них 1 уже в наличии (stock_before=1, taken=1) - докрафтить
        # нужно только недостающий 1 (fires=1, не 2).
        s = GameState(resources={"mid": 1}, buildings={"automation_hub": 1})
        preview = engine.autocraft_preview(s, {"mid": 2})
        mid_node = preview["nodes"][0]
        self.assertEqual(mid_node["stock_before"], 1)
        self.assertEqual(mid_node["taken"], 1)
        self.assertEqual(mid_node["fires"], 1)
        self.assertEqual(mid_node["children"][0]["needed"], 4)  # 1 партия x 4 сырья A

    def test_fully_stocked_leaf_in_the_same_cost_is_not_expanded(self):
        # raw_b полностью покрыт своим запасом - остаётся листом без действия,
        # даже если mid в том же cost требует докрафта.
        s = GameState(resources={"raw_b": 50}, buildings={"automation_hub": 1})
        preview = engine.autocraft_preview(s, {"mid": 1, "raw_b": 1})
        by_id = {n["resource_id"]: n for n in preview["nodes"]}
        self.assertEqual(by_id["raw_b"]["taken"], 1)
        self.assertEqual(by_id["raw_b"]["stock_before"], 50)
        self.assertIsNone(by_id["raw_b"]["action_id"])

    def test_turns_is_none_when_fires_fit_within_one_turn(self):
        # automation_hub: autocraft_speed=50 - с огромным запасом, 2 прокрутки
        # craft_mid укладываются в один ход мгновенно.
        s = GameState(resources={}, buildings={"automation_hub": 1})
        preview = engine.autocraft_preview(s, {"mid": 2})
        mid_node = preview["nodes"][0]
        self.assertEqual(mid_node["fires"], 2)
        self.assertIsNone(mid_node["turns"])

    def test_turns_estimates_multi_turn_docraft_when_fires_exceed_speed(self):
        # trickle_hub - сборщик мастерской на 2 крафта за ход: 5 полуфабрикатов - 3 хода.
        s = GameState(resources={"raw_a": 100}, buildings={"trickle_hub": 1})
        preview = engine.autocraft_preview(s, {"mid": 5})
        (node,) = preview["nodes"]
        self.assertEqual((node["fires"], node["turns"]), (5, 3))

class ResolveActionCostTests(FixtureTestCase):
    """engine.resolve_action_cost - цена действия ЛЮБОГО типа, без выполнения -
    та же цена, что реально списал бы _pay_or_defer; используется окном
    предпросмотра автокрафта, чтобы узнать cost ДО запуска действия."""

    def test_manual_action_cost(self):
        s = GameState()
        self.assertEqual(engine.resolve_action_cost(s, "manual", "craft_mid"), {"raw_a": 4})

    def test_manual_action_cost_respects_chosen_scale(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        result = engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")  # cap=2
        self.assertTrue(result.ok, result.message)
        self.assertEqual(engine.resolve_action_cost(s, "manual", "mechanism_a_action", scale=1), {"raw_a": 2})
        self.assertEqual(engine.resolve_action_cost(s, "manual", "mechanism_a_action"), {"raw_a": 4})

    def test_build_cost_single_stage(self):
        s = GameState()
        self.assertEqual(engine.resolve_action_cost(s, "build", "gen"), {"raw_b": 5})

    def test_build_cost_multi_stage_uses_current_stage(self):
        # furnace: construction_stages - первый этап стоит raw_a:2 (см. fixtures.py).
        s = GameState()
        self.assertEqual(engine.resolve_action_cost(s, "build", "furnace"), {"raw_a": 2})

    def test_tech_cost(self):
        s = GameState()
        self.assertEqual(engine.resolve_action_cost(s, "tech", "tech1"), {"raw_a": 5})

    def test_repair_cost_scales_with_broken_count(self):
        s = GameState(buildings={"gen": 2}, broken={"gen": 2})
        self.assertEqual(engine.resolve_action_cost(s, "repair", "gen"), {"raw_a": 2})

    def test_upgrade_tool_cost_is_the_next_tier(self):
        s = GameState()  # pickaxe на тире 1 по умолчанию
        self.assertEqual(engine.resolve_action_cost(s, "upgrade_tool", "pickaxe"), {"raw_a": 10})

    def test_building_upgrade_cost(self):
        s = GameState()
        self.assertEqual(engine.resolve_action_cost(s, "building_upgrade", "mechanism_a:mech_a_scale"), {"raw_a": 3})

    def test_storage_upgrade_cost_is_the_next_tier(self):
        s = GameState()  # limited_res на тире 0 (только base_capacity) по умолчанию
        self.assertEqual(engine.resolve_action_cost(s, "storage_upgrade", "limited_res"), {"raw_a": 5})

    def test_unknown_action_type_returns_none(self):
        s = GameState()
        self.assertIsNone(engine.resolve_action_cost(s, "skip", None))


class ManualJobTests(FixtureTestCase):
    """Многоходовые ручные действия (manual_actions.*.duration_turns, см. CLAUDE.md) -
    MANUAL_ACTIONS["craft_delayed"]: cost raw_a:2, gain delayed_output:5, duration_turns=3."""

    def test_action_without_duration_turns_is_unaffected(self):
        # Регрессия: действие без duration_turns ведёт себя ровно как раньше - мгновенно,
        # никакой job не заводится.
        s = GameState(resources={"raw_a": 10})
        result = engine.do_manual_action(s, "gather_a")
        self.assertTrue(result.ok)
        self.assertEqual(s.manual_jobs, {})

    def test_click_pays_cost_immediately_and_starts_job(self):
        s = GameState(resources={"raw_a": 10})
        result = engine.do_manual_action(s, "craft_delayed")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 8)  # cost списан сразу
        self.assertEqual(s.resources.get("delayed_output", 0), 0)  # gain ещё не начислен
        self.assertIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.manual_jobs["craft_delayed"]["turns_total"], 3)
        self.assertEqual(s.manual_jobs["craft_delayed"]["turns_done"], 0)

    def test_job_completes_after_exactly_duration_turns(self):
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")

        engine.advance_turn(s)  # 1/3
        self.assertIn("craft_delayed", s.manual_jobs)
        engine.advance_turn(s)  # 2/3
        self.assertIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.resources.get("delayed_output", 0), 0)
        report = engine.advance_turn(s)  # 3/3 - завершилось

        self.assertNotIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.resources["delayed_output"], 5)
        self.assertIn("craft_delayed", report.manual_completed)

    def test_repeat_click_while_running_is_blocked_without_spending_a_turn(self):
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")
        turn_before = s.turn
        resources_before = dict(s.resources)

        result = engine.do_manual_action(s, "craft_delayed")
        self.assertFalse(result.ok)
        self.assertEqual(s.turn, turn_before)  # клик заблокирован ДО advance_turn
        self.assertEqual(s.resources, resources_before)  # cost не списан второй раз

    def test_other_multiturn_action_in_same_location_is_blocked(self):
        # craft_delayed_sibling живёт в той же location, что craft_delayed
        # ("workshop_loc") - пока craft_delayed тикает, второе многоходовое
        # действие той же локации стартовать нельзя, даже другим action_id.
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")
        turn_before = s.turn
        resources_before = dict(s.resources)

        result = engine.do_manual_action(s, "craft_delayed_sibling")
        self.assertFalse(result.ok)
        self.assertNotIn("craft_delayed_sibling", s.manual_jobs)
        self.assertEqual(s.turn, turn_before)
        self.assertEqual(s.resources, resources_before)  # cost не списан

    def test_multiturn_action_in_child_location_is_not_blocked(self):
        # craft_delayed_sub - в "sub_workshop" (parent_location=workshop_loc) -
        # ограничение НЕ рекурсивное: подлокация свободна, даже пока в родительской
        # локации уже тикает свой многоходовой job.
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")

        result = engine.do_manual_action(s, "craft_delayed_sub")
        self.assertTrue(result.ok, result.message)
        self.assertIn("craft_delayed_sub", s.manual_jobs)

    def test_location_frees_up_once_the_running_job_completes(self):
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")  # 3 хода
        for _ in range(3):
            engine.advance_turn(s)
        self.assertNotIn("craft_delayed", s.manual_jobs)

        result = engine.do_manual_action(s, "craft_delayed_sibling")
        self.assertTrue(result.ok, result.message)
        self.assertIn("craft_delayed_sibling", s.manual_jobs)

    def test_location_frees_up_after_explicit_cancel(self):
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")
        engine.cancel_manual_job(s, "craft_delayed")

        result = engine.do_manual_action(s, "craft_delayed_sibling")
        self.assertTrue(result.ok, result.message)
        self.assertIn("craft_delayed_sibling", s.manual_jobs)

    def test_cancel_manual_job_refunds_already_paid_cost(self):
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")
        self.assertEqual(s.resources["raw_a"], 8)

        result = engine.cancel_manual_job(s, "craft_delayed")
        self.assertTrue(result.ok, result.message)
        self.assertNotIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.resources["raw_a"], 10)  # cost возвращён целиком

        for _ in range(3):
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("delayed_output", 0), 0)  # gain так и не начислен

    def test_cancel_manual_job_after_partial_progress_still_refunds_full_cost(self):
        # Накопленные "бесплатные" тики (turns_done) не влияют на размер возврата -
        # возвращается весь cost, списанный при старте, независимо от того, сколько
        # ходов job уже отсчитал.
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")
        engine.advance_turn(s)
        engine.advance_turn(s)
        self.assertEqual(s.manual_jobs["craft_delayed"]["turns_done"], 2)

        result = engine.cancel_manual_job(s, "craft_delayed")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 10)

    def test_can_start_again_immediately_after_cancelling(self):
        # Отмена немедленно освобождает action_id - новый клик стартует новый job,
        # а не считается "уже выполняется".
        s = GameState(resources={"raw_a": 10})
        engine.do_manual_action(s, "craft_delayed")
        engine.cancel_manual_job(s, "craft_delayed")

        result = engine.do_manual_action(s, "craft_delayed")
        self.assertTrue(result.ok, result.message)
        self.assertIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.resources["raw_a"], 8)  # оплачено заново

    def test_cancel_unknown_manual_job_fails_cleanly(self):
        s = GameState()
        result = engine.cancel_manual_job(s, "craft_delayed")
        self.assertFalse(result.ok)

    def test_resource_recipe_includes_multi_turn_actions(self):
        # craft_delayed (duration_turns=3) - детерминированный рецепт с непустым cost:
        # автокрафт ведёт его партиями через manual_jobs (см.
        # tests/test_autocraft_batches.py::LongActionAutocraftTests).
        s = GameState()
        recipe = engine.resource_recipe(s)
        self.assertEqual(recipe.get("delayed_output"), "craft_delayed")


class EnergyCraftTests(FixtureTestCase):
    def test_accumulates_energy_and_completes_recipe(self):
        # converter: макс. 4 энергии/ход. make_raw_b: порог 10.
        # Партия закрывается на 3-й ход: 4+4+4=12 >= 10.
        s = GameState(resources={"energy": 100}, buildings={"energy_converter": 1})
        result = engine.start_energy_recipe(s, "make_raw_b")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.energy_jobs, [{"recipe_id": "make_raw_b", "progress": 0, "building_id": "energy_converter"}])

        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs, [{"recipe_id": "make_raw_b", "progress": 4, "building_id": "energy_converter"}])
        self.assertEqual(s.resources.get("raw_b", 0), 0)

        s.resources["energy"] = 100
        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs, [{"recipe_id": "make_raw_b", "progress": 8, "building_id": "energy_converter"}])

        s.resources["energy"] = 100
        report = engine.advance_turn(s)
        self.assertEqual(s.energy_jobs, [])  # партия готова, преобразователь свободен
        self.assertEqual(s.resources.get("raw_b", 0), 1)
        self.assertEqual(report.crafted.get("make_raw_b"), 1)

    def test_max_energy_per_turn_caps_the_draw_even_with_more_available(self):
        # 1000 энергии доступно, но converter заберёт только max_energy_per_turn=4.
        s = GameState(resources={"energy": 1000}, buildings={"energy_converter": 1})
        engine.start_energy_recipe(s, "make_raw_b")
        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs[0]["progress"], 4)

    def test_energy_job_yields_nothing_without_energy(self):
        s = GameState(resources={}, buildings={"energy_converter": 1})
        engine.start_energy_recipe(s, "make_raw_b")
        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs[0]["progress"], 0)

    def test_recipe_completes_instantly_when_one_turn_of_energy_is_enough(self):
        # mega_converter не ограничивает забор энергии за ход - если пришедшего с
        # одного хода хватает сразу на весь порог, партия готова В ТОТ ЖЕ ход, а не
        # растягивается на несколько (см. пожелание пользователя: "если хватит
        # одного хода на создание предмета, то он делается сразу").
        s = GameState(resources={"energy": 15}, buildings={"mega_converter": 1})
        engine.start_energy_recipe(s, "make_raw_b")  # порог 10
        report = engine.advance_turn(s)
        self.assertEqual(s.energy_jobs, [])
        self.assertEqual(s.resources.get("raw_b", 0), 1)
        self.assertEqual(report.crafted.get("make_raw_b"), 1)

    def test_cannot_start_a_recipe_while_the_only_converter_is_busy(self):
        s = GameState(buildings={"energy_converter": 1})
        first = engine.start_energy_recipe(s, "make_raw_b")
        self.assertTrue(first.ok)
        second = engine.start_energy_recipe(s, "make_catalyst")
        self.assertFalse(second.ok)
        self.assertEqual(len(s.energy_jobs), 1)  # второй крафт не встал поверх первого

    def test_second_converter_allows_a_second_recipe_at_the_same_time(self):
        s = GameState(buildings={"energy_converter": 2})
        first = engine.start_energy_recipe(s, "make_raw_b")
        second = engine.start_energy_recipe(s, "make_catalyst")
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(len(s.energy_jobs), 2)

    def test_two_converters_process_two_recipes_in_true_parallel_not_shared_budget(self):
        # Регрессия на конкретный баг: раньше первый в списке крафт мог забрать ВЕСЬ
        # суммарный бюджет (max_energy_per_turn * количество преобразователей) и
        # оставить второй без энергии, даже когда есть второй свободный преобразователь -
        # это фактически превращало "параллельно" обратно в "последовательно". Каждый
        # крафт должен получать СВОЙ личный лимit (4), а не делить один на двоих.
        s = GameState(resources={"energy": 100}, buildings={"energy_converter": 2})
        engine.start_energy_recipe(s, "make_raw_b")
        engine.start_energy_recipe(s, "make_catalyst")
        engine.advance_turn(s)
        progress = {j["recipe_id"]: j["progress"] for j in s.energy_jobs}
        self.assertEqual(progress, {"make_raw_b": 4, "make_catalyst": 4})

    def test_starting_the_same_recipe_twice_in_two_converters_is_allowed(self):
        # Ограничение - "один преобразователь ведёт один рецепт", а не "один рецепт
        # ведётся только одним преобразователем" - тот же рецепт можно запустить ещё
        # раз во втором свободном преобразователе.
        s = GameState(buildings={"energy_converter": 2})
        first = engine.start_energy_recipe(s, "make_raw_b")
        second = engine.start_energy_recipe(s, "make_raw_b")
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(len(s.energy_jobs), 2)

    def test_cannot_start_without_any_converter_built(self):
        s = GameState()
        result = engine.start_energy_recipe(s, "make_raw_b")
        self.assertFalse(result.ok)

    def test_cancel_energy_job_frees_converter_and_does_not_refund_spent_energy(self):
        s = GameState(resources={"energy": 100}, buildings={"energy_converter": 1})
        engine.start_energy_recipe(s, "make_raw_b")
        engine.advance_turn(s)  # +4 энергии вложено в progress (не в resources - оно там и не появлялось)
        self.assertEqual(s.energy_jobs[0]["progress"], 4)

        result = engine.cancel_energy_job(s, 0)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.energy_jobs, [])  # преобразователь сразу свободен
        self.assertEqual(s.resources.get("raw_b", 0), 0)  # партия не была готова - ничего не выдано

        # преобразователь снова свободен - можно тут же запустить другой рецепт
        second = engine.start_energy_recipe(s, "make_catalyst")
        self.assertTrue(second.ok, second.message)

    def test_cancel_unknown_energy_job_index_fails_cleanly(self):
        s = GameState(buildings={"energy_converter": 1})
        result = engine.cancel_energy_job(s, 0)
        self.assertFalse(result.ok)

    def test_autocraft_runs_two_energy_recipes_sequentially_with_a_single_converter(self):
        # assemble_capacitor (raw_b + catalyst -> capacitor) - оба ингредиента делаются
        # только крафтом за энергию. С ОДНИМ автоматизированным преобразователем второй
        # рецепт ждёт, пока закончится первый - не больше одной партии разом.
        s = GameState(
            buildings={"automation_hub": 1, "energy_converter": 1},
            building_upgrades={"energy_converter": ["conv_auto"]},
        )
        result = engine.do_manual_action(s, "assemble_capacitor")
        self.assertTrue(result.ok and result.pending, result.message)
        self.assertEqual(len(s.orders), 1)
        for _ in range(12):
            s.resources["energy"] = 100
            engine.advance_turn(s)
            self.assertLessEqual(len(s.energy_jobs), 1)
            if s.resources.get("capacitor", 0):
                break
        self.assertEqual(s.resources.get("capacitor"), 1)
        self.assertEqual(s.orders, [])

    def test_autocraft_runs_two_energy_recipes_in_parallel_with_two_converters(self):
        # Со ВТОРЫМ автоматизированным преобразователем оба ингредиента идут разом.
        s = GameState(
            buildings={"automation_hub": 1, "energy_converter": 2},
            building_upgrades={"energy_converter": ["conv_auto"]},
        )
        self.assertTrue(engine.do_manual_action(s, "assemble_capacitor").pending)
        engine.advance_turn(s)
        self.assertEqual({j["recipe_id"] for j in s.energy_jobs}, {"make_raw_b", "make_catalyst"})
        for _ in range(10):
            if s.resources.get("capacitor", 0):
                break
            s.resources["energy"] = 100
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("capacitor"), 1)

    def test_amplifier_building_locked_without_a_working_converter(self):
        # requires_building - тот же generic-гейт, что и у ручных действий
        # (is_manual_action_unlocked), теперь применён и к ПОСТРОЙКАМ:
        # is_building_unlocked/build() требуют хотя бы одну РАБОТАЮЩУЮ копию
        # energy_converter, прежде чем усилитель появится в каталоге строительства.
        s = GameState(resources={"raw_a": 100})
        self.assertFalse(engine.is_building_unlocked("amplifier", s))
        result = engine.build(s, "amplifier")
        self.assertFalse(result.ok)

        engine.build(s, "energy_converter")
        self.assertTrue(engine.is_building_unlocked("amplifier", s))
        result = engine.build(s, "amplifier")
        self.assertTrue(result.ok, result.message)

    def test_amplifier_building_locked_again_once_converter_breaks_entirely(self):
        s = GameState(buildings={"energy_converter": 1}, broken={"energy_converter": 1})
        self.assertFalse(engine.is_building_unlocked("amplifier", s))

    def test_energy_converter_capacity_upgrade_boosts_throughput(self):
        # energy_converter (фикстура): max_energy_per_turn=4, апгрейды
        # conv_cap_1 (+3) и conv_cap_2 (+9) - "самый сильный купленный
        # побеждает", НЕ складываются друг с другом (как scale/energy_efficiency).
        s = GameState(resources={"raw_a": 100}, buildings={"energy_converter": 1})
        self.assertEqual(engine.energy_converter_max_energy_per_turn(s, "energy_converter"), 4)

        self.assertTrue(engine.purchase_building_upgrade(s, "energy_converter", "conv_cap_1").ok)
        self.assertEqual(engine.energy_converter_max_energy_per_turn(s, "energy_converter"), 7)

        self.assertTrue(engine.purchase_building_upgrade(s, "energy_converter", "conv_cap_2").ok)
        self.assertEqual(engine.energy_converter_max_energy_per_turn(s, "energy_converter"), 13)  # не 4+3+9

    def test_energy_converter_capacity_upgrade_is_scoped_to_its_own_building_only(self):
        # Апгрейд matter_cap_1 куплен для matter_generator - НЕ должен
        # прибавлять свой бонус к energy_converter, даже если оба типа построены
        # одновременно (state.building_upgrades хранится ПЕР building_id).
        s = GameState(resources={"raw_a": 100}, buildings={"energy_converter": 1, "matter_generator": 1})
        self.assertTrue(engine.purchase_building_upgrade(s, "matter_generator", "matter_cap_1").ok)
        self.assertEqual(engine.energy_converter_max_energy_per_turn(s, "energy_converter"), 4)  # без чужого буста
        self.assertEqual(engine.energy_converter_max_energy_per_turn(s, "matter_generator"), 50 + 30)

    def test_energy_converter_capacity_upgrade_does_not_apply_when_converter_has_no_cap(self):
        # mega_converter: max_energy_per_turn=None (без ограничения) - бонусу
        # апгрейда складывать не с чем, лимита как не было, так и нет.
        s = GameState(resources={"raw_a": 100}, buildings={"mega_converter": 1})
        self.assertTrue(engine.purchase_building_upgrade(s, "mega_converter", "mega_cap_1").ok)
        self.assertIsNone(engine.energy_converter_max_energy_per_turn(s, "mega_converter"))

    def test_energy_converter_capacity_upgrade_applies_in_actual_energy_job_progress(self):
        # Сквозная проверка через advance_turn, а не только через голую формулу.
        s = GameState(resources={"energy": 100, "raw_a": 100}, buildings={"energy_converter": 1})
        engine.purchase_building_upgrade(s, "energy_converter", "conv_cap_1")
        engine.start_energy_recipe(s, "make_raw_b")
        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs[0]["progress"], 7)  # 4 (база) + 3 (апгрейд)


class EnergyMachineTypesTests(FixtureTestCase):
    """Несколько ТИПОВ энергетических машин разом (energy_converter +
    matter_generator, см. tests/fixtures.py) - независимая пропускная
    способность, привязка рецепта к типу (machine), материальная цена
    (consumes) и приоритет распределения общего пула при нехватке."""

    def test_two_machine_types_draw_independent_caps_from_the_shared_pool(self):
        # Регрессия на баг: раньше ОБА типа использовали лимит "первой попавшейся"
        # постройки с energy_converter (тут это оказался бы лимит energy_converter,
        # 4) - matter_generator (лимит 50) закрывает свой порог 20 ЗА ОДИН ход,
        # что было бы невозможно при лимите 4 (потребовалось бы 5 ходов).
        s = GameState(
            resources={"energy": 1000, "raw_a": 100},
            buildings={"energy_converter": 1, "matter_generator": 1},
        )
        engine.start_energy_recipe(s, "make_raw_b")  # energy_converter, порог 10
        engine.start_energy_recipe(s, "upgrade_matter")  # matter_generator, порог 20
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("upgraded_matter", 0), 1)  # готово за 1 ход
        self.assertEqual(s.resources.get("raw_a"), 100 - 5)  # consumes списан при запуске
        remaining = {j["recipe_id"]: j["progress"] for j in s.energy_jobs}
        self.assertEqual(remaining["make_raw_b"], 4)  # свой личный лимит energy_converter

    def test_recipe_with_machine_cannot_start_in_a_different_type(self):
        s = GameState(resources={"raw_a": 100}, buildings={"energy_converter": 1})  # НЕТ matter_generator
        result = engine.start_energy_recipe(s, "upgrade_matter")
        self.assertFalse(result.ok)
        self.assertEqual(s.energy_jobs, [])

    def test_recipe_with_machine_runs_in_its_own_type(self):
        s = GameState(resources={"raw_a": 100}, buildings={"matter_generator": 1})
        result = engine.start_energy_recipe(s, "upgrade_matter")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.energy_jobs[0]["building_id"], "matter_generator")

    def test_start_energy_recipe_pays_consumes_upfront(self):
        s = GameState(resources={"raw_a": 10}, buildings={"matter_generator": 1})
        result = engine.start_energy_recipe(s, "upgrade_matter")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 5)

    def test_start_energy_recipe_fails_without_enough_material_and_no_autocraft(self):
        s = GameState(resources={"raw_a": 2}, buildings={"matter_generator": 1})  # нужно 5
        result = engine.start_energy_recipe(s, "upgrade_matter")
        self.assertFalse(result.ok)
        self.assertEqual(s.energy_jobs, [])
        self.assertEqual(s.resources["raw_a"], 2)  # ничего не списано

    def test_energy_job_does_not_draw_more_than_needed_to_finish(self):
        # Регрессия: раньше job забирал ВЕСЬ личный лимит (4), даже если до
        # порога оставалось всего 2 - лишнее сгорало бы впустую вместо того,
        # чтобы остаться в пуле. battery (energy_storage) нужна, чтобы остаток
        # energy пережил обрезку в конце advance_turn и его можно было проверить -
        # без хранилища вся энергия всё равно обрезалась бы до 0 в конце хода.
        s = GameState(resources={"energy": 10}, buildings={"energy_converter": 1, "battery": 2})
        engine.start_energy_recipe(s, "make_raw_b")  # порог 10, личный лимит 4
        s.energy_jobs[0]["progress"] = 8  # нужно ещё всего 2
        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs, [])  # завершился
        self.assertEqual(s.resources.get("raw_b", 0), 1)
        self.assertEqual(s.resources["energy"], 8)  # забрал ровно 2 (10-2), не полный лимит 4 (было бы 10-4=6)

    def test_default_priority_tie_breaks_by_building_order_when_pool_is_short(self):
        # Ни у одного типа нет override приоритета (оба по умолчанию 0) - при
        # равенстве побеждает порядок в BUILDINGS/content.json: energy_converter
        # объявлен раньше matter_generator в фикстуре, значит тянет из пула первым.
        s = GameState(
            resources={"energy": 20, "raw_a": 100},
            buildings={"energy_converter": 1, "matter_generator": 1},
        )
        engine.start_energy_recipe(s, "make_raw_b")  # нужно 4
        engine.start_energy_recipe(s, "upgrade_matter")  # нужно 20, но в пуле осталось лишь 16
        report = engine.advance_turn(s)
        remaining = {j["recipe_id"]: j["progress"] for j in s.energy_jobs}
        self.assertEqual(remaining["make_raw_b"], 4)  # получил свою долю целиком первым
        self.assertEqual(remaining["upgrade_matter"], 16)  # получил лишь остаток (20-4)
        self.assertEqual(report.energy_shortfall.get("matter_generator"), 4)  # не хватило 20-16
        self.assertNotIn("energy_converter", report.energy_shortfall)  # ему хватило целиком - не shortfall

    def test_setting_higher_priority_lets_a_type_drain_the_pool_first(self):
        s = GameState(
            resources={"energy": 20, "raw_a": 100},
            buildings={"energy_converter": 1, "matter_generator": 1},
        )
        engine.set_energy_priority(s, "matter_generator", 10)  # выше умолчания (0) у обоих типов
        engine.start_energy_recipe(s, "make_raw_b")
        engine.start_energy_recipe(s, "upgrade_matter")
        report = engine.advance_turn(s)
        # matter_generator теперь тянет ПЕРВЫМ - получает все 20 (свой полный
        # порог) и сразу завершается; energy_converter достаётся уже пустой пул.
        self.assertEqual(s.resources.get("upgraded_matter", 0), 1)
        remaining = {j["recipe_id"]: j["progress"] for j in s.energy_jobs}
        self.assertEqual(remaining["make_raw_b"], 0)
        self.assertEqual(report.energy_shortfall.get("energy_converter"), 4)

    def test_resetting_energy_priority_reverts_to_content_default(self):
        s = GameState()
        engine.set_energy_priority(s, "matter_generator", 10)
        engine.set_energy_priority(s, "matter_generator", None)
        self.assertEqual(engine.energy_priority(s, "matter_generator"), 0)

    def test_set_energy_priority_rejects_unknown_building(self):
        s = GameState()
        result = engine.set_energy_priority(s, "no_such_building", 5)
        self.assertFalse(result.ok)

    def test_autocraft_starts_energy_recipe_only_in_its_required_machine_type(self):
        # У upgrade_matter machine=matter_generator - заказ запускает его ИМЕННО там,
        # даже при свободном автоматизированном energy_converter; материал (consumes)
        # изымается в резерв заказа сразу и тратится при запуске партии.
        s = GameState(
            resources={"raw_a": 100},
            buildings={"automation_hub": 1, "energy_converter": 1, "matter_generator": 1},
            building_upgrades={"energy_converter": ["conv_auto"], "matter_generator": ["matter_auto"]},
        )
        failure, order = engine._place_order(s, "recipe", "x", "тест", {"upgraded_matter": 1})
        self.assertIsNone(failure)
        self.assertEqual(s.resources["raw_a"], 100 - 5)
        engine._process_orders(s, engine.ProductionReport(), {})
        self.assertEqual([(j["recipe_id"], j["building_id"]) for j in s.energy_jobs], [("upgrade_matter", "matter_generator")])

    def test_autocraft_refuses_to_start_energy_recipe_without_its_material(self):
        # Материал энергорецепта - обычный ингредиент заказа: не хватает, а сделать его
        # не из чего (raw_a - сырьё) - отказ сразу, ничего не изъято и не запущено.
        s = GameState(
            resources={"raw_a": 2}, buildings={"automation_hub": 1, "matter_generator": 1},
            building_upgrades={"matter_generator": ["matter_auto"]},
        )
        failure, order = engine._place_order(s, "recipe", "x", "тест", {"upgraded_matter": 1})
        self.assertEqual((failure.reason, order), ("no_recipe", None))
        self.assertEqual(s.energy_jobs, [])
        self.assertEqual(s.resources["raw_a"], 2)

class BuildResearchRepairTests(FixtureTestCase):
    def test_build_deep_autocraft_chain(self):
        s = GameState(
            resources={"raw_a": 500, "raw_b": 500}, techs={"tech1"}, buildings={"automation_hub": 1},
        )
        result = engine.build(s, "factory")  # cost: mid 3 - полуфабрикатов 0
        self.assertTrue(result.ok and result.pending, result.message)
        self.assertEqual(s.resources["raw_a"], 500 - 3 * 4)  # сырьё сразу в резерве заказа
        engine.advance_turn(s)
        self.assertEqual(s.buildings["factory"], 1)

    def test_multi_stage_building_requires_paying_each_stage_in_order(self):
        # furnace: 3 этапа (raw_a:2 / raw_b:1 / raw_a:1+raw_b:1) - постройка не
        # появляется до последнего этапа, каждый этап платится отдельным build().
        s = GameState(resources={"raw_a": 100, "raw_b": 100})

        r1 = engine.build(s, "furnace")
        self.assertTrue(r1.ok, r1.message)
        self.assertNotIn("furnace", s.buildings)  # ещё не построена целиком
        self.assertEqual(s.construction_progress["furnace"], 1)
        self.assertEqual(s.resources["raw_a"], 98)
        self.assertIn("Стадия 1 из 3", r1.message)
        self.assertIn("установить генератор", r1.message)

        r2 = engine.build(s, "furnace")
        self.assertTrue(r2.ok, r2.message)
        self.assertNotIn("furnace", s.buildings)
        self.assertEqual(s.construction_progress["furnace"], 2)
        self.assertEqual(s.resources["raw_b"], 99)
        self.assertIn("Стадия 2 из 3", r2.message)

        r3 = engine.build(s, "furnace")
        self.assertTrue(r3.ok, r3.message)
        self.assertEqual(s.buildings["furnace"], 1)  # только теперь построена
        self.assertNotIn("furnace", s.construction_progress)  # прогресс сброшен
        self.assertEqual(s.resources["raw_a"], 97)
        self.assertEqual(s.resources["raw_b"], 98)
        self.assertEqual(r3.message, "Построено: Печь.")

    def test_multi_stage_building_catalog_cost_tracks_current_stage(self):
        s = GameState(resources={"raw_a": 100, "raw_b": 100})
        stage = engine.current_construction_stage("furnace", s)
        self.assertEqual(stage["number"], 1)
        self.assertEqual(stage["total"], 3)
        self.assertEqual(stage["cost"], {"raw_a": 2})

        engine.build(s, "furnace")
        stage2 = engine.current_construction_stage("furnace", s)
        self.assertEqual(stage2["number"], 2)
        self.assertEqual(stage2["cost"], {"raw_b": 1})

    def test_current_construction_stage_is_none_for_single_stage_building(self):
        s = GameState()
        self.assertIsNone(engine.current_construction_stage("gen", s))

    def test_multi_stage_building_fails_cleanly_without_enough_resources(self):
        s = GameState(resources={"raw_a": 1})  # не хватает на этап 1 (raw_a:2)
        result = engine.build(s, "furnace")
        self.assertFalse(result.ok)
        self.assertNotIn("furnace", s.construction_progress)  # прогресс не тронут при провале
        self.assertEqual(s.resources["raw_a"], 1)  # ничего не списано

    def test_single_stage_list_hides_the_counter_but_keeps_the_description(self):
        # kiln: ровно 1 этап в construction_stages - счётчик "N из M" не должен
        # появляться нигде (ни в сообщении, ни в catalog-выдаче), но описание
        # самого этапа всё равно показывается. Один этап всегда сразу и последний,
        # так что build() завершает постройку целиком за один вызов.
        s = GameState(resources={"raw_a": 100})
        stage = engine.current_construction_stage("kiln", s)
        self.assertEqual(stage["number"], 1)
        self.assertEqual(stage["total"], 1)

        result = engine.build(s, "kiln")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.buildings["kiln"], 1)
        self.assertEqual(result.message, "Построено: Обжиговая печь.")  # как у обычной одноэтапной постройки
        self.assertNotIn("из 1", result.message)
        self.assertNotIn("kiln", s.construction_progress)  # разово оплаченный единственный этап прогресс не оставляет

    def test_single_stage_list_failure_message_has_no_counter(self):
        s = GameState()  # raw_a не хватает
        result = engine.build(s, "kiln")
        self.assertFalse(result.ok)
        self.assertNotIn("из 1", result.message)
        self.assertIn("«Обжиговая печь»", result.message)

    def test_multi_stage_building_cannot_skip_a_stage_even_via_background_autocraft(self):
        # reactor: этап 1 стоит slow_mid:10, а сборщик trickle_hub делает 2 за ход -
        # заказ идёт 5 ходов. Пока он не готов, этап 1 не засчитан и перепрыгнуть
        # на этап 2 нельзя - build() работает только с ТЕКУЩИМ этапом.
        s = GameState(resources={"slow_raw": 1000, "raw_a": 100}, buildings={"trickle_hub": 1})
        result = engine.build(s, "reactor")
        self.assertTrue(result.ok and result.pending, result.message)
        self.assertNotIn("reactor", s.construction_progress)
        for _ in range(4):
            engine.advance_turn(s)
        self.assertNotIn("reactor", s.construction_progress)
        engine.advance_turn(s)  # 5-й ход - 10 полуфабрикатов готовы, заказ оплачивает этап 1
        self.assertEqual(s.construction_progress.get("reactor"), 1)
        self.assertNotIn("reactor", s.buildings)

    def test_research_requires_prerequisites(self):
        s = GameState(resources={"mid": 10}, solved_hypotheses={"tech2"})
        result = engine.research(s, "tech2")  # requires tech1
        self.assertFalse(result.ok)
        s.techs.add("tech1")
        result = engine.research(s, "tech2")
        self.assertTrue(result.ok)
        self.assertIn("tech2", s.techs)

    def test_research_twice_fails(self):
        s = GameState(resources={"raw_a": 100}, solved_hypotheses={"tech1"})
        result = engine.research(s, "tech1")
        self.assertTrue(result.ok)
        result2 = engine.research(s, "tech1")
        self.assertFalse(result2.ok)

    def test_research_requires_solved_hypothesis(self):
        s = GameState(resources={"raw_a": 100})
        result = engine.research(s, "tech1")
        self.assertFalse(result.ok)
        engine.solve_hypothesis(s, "tech1")
        result = engine.research(s, "tech1")
        self.assertTrue(result.ok)

    def test_solve_hypothesis_twice_fails(self):
        s = GameState()
        result = engine.solve_hypothesis(s, "tech1")
        self.assertTrue(result.ok)
        result2 = engine.solve_hypothesis(s, "tech1")
        self.assertFalse(result2.ok)

    def test_solve_hypothesis_unknown_tech_fails(self):
        s = GameState()
        result = engine.solve_hypothesis(s, "no_such_tech")
        self.assertFalse(result.ok)

    def test_repair_nothing_broken(self):
        s = GameState()
        result = engine.repair(s, "gen")
        self.assertFalse(result.ok)

    def test_repair_fixes_all_broken_copies_at_once(self):
        s = GameState(resources={"raw_a": 100}, buildings={"gen": 3}, broken={"gen": 2})
        result = engine.repair(s, "gen")
        self.assertTrue(result.ok)
        self.assertEqual(s.broken["gen"], 0)

    def test_demolish_unbuilt_building_fails(self):
        s = GameState()
        result = engine.demolish_building(s, "gen")
        self.assertFalse(result.ok)

    def test_demolish_unknown_building_fails(self):
        s = GameState()
        result = engine.demolish_building(s, "no_such_building")
        self.assertFalse(result.ok)

    def test_demolish_removes_one_copy_without_refunding_resources(self):
        # gen.cost = {"raw_b": 5} - демонтаж не начисляет его обратно.
        s = GameState(resources={"raw_b": 100}, buildings={"gen": 2})
        result = engine.demolish_building(s, "gen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.buildings["gen"], 1)
        self.assertEqual(s.resources["raw_b"], 100)  # ни один ресурс не вернулся

    def test_demolish_prefers_a_broken_copy_over_a_working_one(self):
        s = GameState(buildings={"gen": 3}, broken={"gen": 1})
        result = engine.demolish_building(s, "gen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.buildings["gen"], 2)
        self.assertEqual(s.broken.get("gen", 0), 0)  # снесена именно сломанная копия
        self.assertEqual(engine.working_count(s, "gen"), 2)  # ни одна рабочая копия не пострадала

    def test_demolish_a_working_copy_when_none_are_broken(self):
        s = GameState(buildings={"gen": 2})
        result = engine.demolish_building(s, "gen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.buildings["gen"], 1)
        self.assertEqual(engine.working_count(s, "gen"), 1)

    def test_demolish_clamps_manually_disabled_count_to_new_working_count(self):
        # factory - disableable (produces И consumes непустые в фикстуре) -
        # выключено ползунком 2 из 2 копий; снос одной рабочей не должен оставить
        # "призрачных" выключенных копий сверх реально существующих
        # (building_disabled_count зажимает значение сверху working_count при чтении).
        s = GameState(buildings={"factory": 2}, building_disabled={"factory": 2})
        result = engine.demolish_building(s, "factory")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(engine.working_count(s, "factory"), 1)
        self.assertEqual(engine.building_disabled_count(s, "factory"), 1)

    def test_demolish_last_copy_leaves_building_unbuilt(self):
        s = GameState(buildings={"gen": 1})
        result = engine.demolish_building(s, "gen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.buildings["gen"], 0)
        self.assertEqual(engine.working_count(s, "gen"), 0)


class TypedBreakdownAndToolDurabilityTests(FixtureTestCase):
    """flaky_typed (breakdown_types=["worn_gear"], breakdown_chance=1.0) + TOOLS["wrench"]
    (max_durability=2 на тире 2) - см. tests/fixtures.py."""

    def _broken_flaky_typed(self, **state_kwargs):
        s = GameState(resources={"raw_a": 100}, buildings={"flaky_typed": 1}, **state_kwargs)
        engine.advance_turn(s)
        self.assertEqual(s.broken.get("flaky_typed"), 1)  # breakdown_chance=1.0 - гарантировано
        self.assertEqual(s.broken_types.get("flaky_typed"), ["worn_gear"])
        return s

    def test_repair_blocked_without_the_required_tool(self):
        s = self._broken_flaky_typed()  # wrench не куплен (тир 1 - "без ключа")
        self.assertEqual(engine.repairable_count(s, "flaky_typed"), 0)
        result = engine.repair(s, "flaky_typed")
        self.assertFalse(result.ok)
        self.assertEqual(s.broken["flaky_typed"], 1)  # ничего не починилось

    def test_repair_succeeds_with_the_tool_and_consumes_one_durability(self):
        s = self._broken_flaky_typed(tool_tiers={"wrench": 2})
        self.assertEqual(engine.repairable_count(s, "flaky_typed"), 1)
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 2)  # полная прочность тира
        result = engine.repair(s, "flaky_typed")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.broken.get("flaky_typed", 0), 0)
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 1)  # -1 за эту починку
        self.assertFalse(engine.tool_is_broken(s, "wrench"))

    def test_tool_breaks_after_durability_exhausted_and_repair_tool_restores_it(self):
        s = GameState(resources={"raw_a": 100}, buildings={"flaky_typed": 1}, tool_tiers={"wrench": 2})
        # max_durability=2 - две починки проходят нормально...
        for _ in range(2):
            engine.advance_turn(s)
            self.assertTrue(engine.repair(s, "flaky_typed").ok)
        self.assertTrue(engine.tool_is_broken(s, "wrench"))
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 0)
        # ...а третья поломка чинится не может - ключ сам сломан, тир не потерян.
        engine.advance_turn(s)
        self.assertEqual(engine.repairable_count(s, "flaky_typed"), 0)
        result = engine.repair(s, "flaky_typed")
        self.assertFalse(result.ok)
        self.assertEqual(engine.tool_tier(s, "wrench"), 2)  # тир НЕ теряется

        fix = engine.repair_tool(s, "wrench")
        self.assertTrue(fix.ok, fix.message)
        self.assertEqual(s.resources["raw_a"], 100 - 2 * 1 - 1)  # 2 починки постройки (raw_a:1 каждая) + 1 ремонт ключа (raw_a:1)
        self.assertFalse(engine.tool_is_broken(s, "wrench"))
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 2)  # снова полная прочность
        self.assertTrue(engine.repair(s, "flaky_typed").ok)  # теперь чинится как обычно

    def test_repair_tool_refuses_when_not_broken(self):
        s = GameState(tool_tiers={"wrench": 2})
        result = engine.repair_tool(s, "wrench")
        self.assertFalse(result.ok)

    def test_breakdown_immunity_upgrade_disables_breakdown_entirely(self):
        s = GameState(resources={"raw_a": 100}, buildings={"flaky_typed": 1})
        result = engine.purchase_building_upgrade(s, "flaky_typed", "flaky_typed_immune")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(engine.effective_breakdown_chance("flaky_typed", s.techs, purchased=["flaky_typed_immune"]), 0.0)
        for _ in range(5):
            engine.advance_turn(s)
        self.assertEqual(s.broken.get("flaky_typed", 0), 0)  # без апгрейда было бы гарантированно сломано


class ActionDurationReductionTests(FixtureTestCase):
    """mechanism_a_delayed_action (requires_building="mechanism_a", duration_turns=4) +
    апгрейд mech_a_duration_cut (value=0.5) - см. tests/fixtures.py."""

    def test_manual_action_duration_without_upgrade_is_unchanged(self):
        s = GameState(buildings={"mechanism_a": 1})
        self.assertEqual(engine.manual_action_duration("mechanism_a_delayed_action", s), 4)

    def test_manual_action_duration_with_upgrade_is_reduced_and_rounds_up(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_duration_cut")
        self.assertEqual(engine.manual_action_duration("mechanism_a_delayed_action", s), 2)  # ceil(4*0.5)

    def test_do_manual_action_creates_a_job_with_the_reduced_duration(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_duration_cut")
        result = engine.do_manual_action(s, "mechanism_a_delayed_action")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.manual_jobs["mechanism_a_delayed_action"]["turns_total"], 2)

    def test_action_unrelated_to_a_building_is_never_affected(self):
        # craft_delayed не имеет requires_building - апгрейды механизмов не имеют,
        # чем на него подействовать (нет building_id, от которого брать purchased).
        s = GameState(resources={"raw_a": 100})
        self.assertEqual(engine.manual_action_duration("craft_delayed", s), 3)


class ProductionTests(FixtureTestCase):
    def test_advance_turn_applies_production_and_consumption(self):
        s = GameState(resources={"energy": 10}, buildings={"factory": 1})
        engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 2)
        # без построек-хранилищ энергия не копится: фабрика потратила 1 из 10,
        # а остаток (9) обнуляется в конце хода, а не переносится дальше
        self.assertEqual(s.resources["energy"], 0)
        self.assertEqual(s.turn, 2)

    def test_advance_turn_keeps_energy_up_to_storage_capacity(self):
        s = GameState(resources={"energy": 10}, buildings={"factory": 1, "battery": 1})
        engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 2)
        # осталось бы 10-1=9, но ёмкость батареи в фикстуре - 5
        self.assertEqual(s.resources["energy"], 5)

    def test_energy_capacity_ignores_broken_storage(self):
        s = GameState(buildings={"battery": 2}, broken={"battery": 1})
        self.assertEqual(engine.energy_capacity(s), 5)  # только одна рабочая копия x5

    def test_energy_capacity_zero_without_storage_buildings(self):
        s = GameState(buildings={"factory": 1})
        self.assertEqual(engine.energy_capacity(s), 0)

    def test_generator_energy_reaches_same_turn_consumer_regardless_of_build_order(self):
        # "factory" вставлена в словарь раньше своего генератора "gen" - энергия,
        # произведённая generatorом в этот же ход, всё равно должна ему достаться.
        s = GameState(buildings={"factory": 1, "gen": 1})
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 2)  # фабрика отработала полностью

    def test_advance_turn_throttles_when_input_insufficient(self):
        # две фабрики, но энергии хватает только на одну
        s = GameState(resources={"energy": 1}, buildings={"factory": 2})
        report = engine.advance_turn(s)
        ran, idle = report.ran["factory"]
        self.assertEqual(ran, 1)
        self.assertEqual(idle, 1)
        self.assertEqual(s.resources["raw_a"], 2)  # только одна копия сработала

    def test_theoretical_rates_matches_actual_when_unconstrained(self):
        s = GameState(resources={"energy": 1000}, buildings={"factory": 2, "gen": 1})
        rates = engine.theoretical_rates(s)
        self.assertEqual(rates["raw_a"], 4)
        self.assertEqual(rates["energy"], 0)  # -1*2 (фабрики) + 2*1 (генератор)

    def test_broken_building_does_not_produce(self):
        s = GameState(resources={"energy": 100}, buildings={"factory": 1}, broken={"factory": 1})
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 0)

    def test_breakdown_triggers_reliably_without_needing_a_seed(self):
        # "flaky" в фикстуре имеет breakdown_chance=1.0 - поломка гарантирована,
        # так что тест не зависит от конкретного random.seed вообще.
        s = GameState(resources={"raw_a": 100}, buildings={"flaky": 1})
        report = engine.advance_turn(s)
        self.assertEqual(report.newly_broken.get("flaky"), 1)
        self.assertEqual(s.broken["flaky"], 1)


class MechanismToggleTests(FixtureTestCase):
    """Ползунок меню "Механизмы" (state.building_disabled) - см. CLAUDE.md,
    engine.is_disableable_building/building_active_count/set_building_active_count."""

    def test_is_disableable_building_requires_both_produces_and_consumes(self):
        self.assertTrue(engine.is_disableable_building("factory"))  # produces И consumes непустые
        self.assertFalse(engine.is_disableable_building("gen"))  # consumes пуст (бесплатный генератор)
        self.assertFalse(engine.is_disableable_building("flaky"))  # тоже без consumes
        self.assertFalse(engine.is_disableable_building("mechanism_a"))  # без produces/consumes вовсе

    def test_set_active_count_rejects_unknown_or_non_disableable_building(self):
        s = GameState(buildings={"gen": 1})
        result = engine.set_building_active_count(s, "no_such_building", 1)
        self.assertFalse(result.ok)
        result = engine.set_building_active_count(s, "gen", 1)
        self.assertFalse(result.ok)
        self.assertEqual(s.building_disabled, {})

    def test_set_active_count_rejects_without_working_copies(self):
        s = GameState(buildings={"factory": 1}, broken={"factory": 1})
        result = engine.set_building_active_count(s, "factory", 1)
        self.assertFalse(result.ok)

    def test_set_active_count_clamps_to_working_range(self):
        s = GameState(buildings={"factory": 3})
        engine.set_building_active_count(s, "factory", -5)
        self.assertEqual(engine.building_active_count(s, "factory"), 0)
        engine.set_building_active_count(s, "factory", 999)
        self.assertEqual(engine.building_active_count(s, "factory"), 3)

    def test_set_active_count_updates_active_and_disabled_counts(self):
        s = GameState(buildings={"factory": 3})
        result = engine.set_building_active_count(s, "factory", 2)
        self.assertTrue(result.ok)
        self.assertEqual(engine.building_disabled_count(s, "factory"), 1)
        self.assertEqual(engine.building_active_count(s, "factory"), 2)

    def test_disabled_count_clamps_after_copies_break(self):
        # Выключили 2 из 3, потом сломалась ещё одна - working_count упал до 2,
        # а building_disabled не должен "съедать" больше, чем реально осталось.
        s = GameState(buildings={"factory": 3}, building_disabled={"factory": 2})
        s.broken["factory"] = 1
        self.assertEqual(engine.working_count(s, "factory"), 2)
        self.assertEqual(engine.building_disabled_count(s, "factory"), 2)
        self.assertEqual(engine.building_active_count(s, "factory"), 0)

    def test_disabled_copies_do_not_produce_or_consume(self):
        # "battery" даёт ёмкость хранилища энергии (5) - без неё остаток энергии
        # всё равно обнулился бы в конце хода независимо от расхода (см.
        # ProductionTests.test_advance_turn_applies_production_and_consumption),
        # и тест не смог бы отличить "потратили 2" от "потратили всё".
        s = GameState(resources={"energy": 3}, buildings={"factory": 3, "battery": 1})
        engine.set_building_active_count(s, "factory", 2)
        report = engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 4)  # только 2 активные копии по 2 raw_a
        self.assertEqual(s.resources["energy"], 1)  # 3 - только 2 активные копии по 1 энергии
        ran, idle = report.ran["factory"]
        self.assertEqual(ran, 2)
        self.assertEqual(idle, 1)

    def test_fully_disabled_building_produces_nothing(self):
        s = GameState(resources={"energy": 3}, buildings={"factory": 2, "battery": 1})
        engine.set_building_active_count(s, "factory", 0)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertEqual(s.resources["energy"], 3)  # ни одна копия не потребляла

    def test_theoretical_rates_reflect_disabled_copies(self):
        s = GameState(buildings={"factory": 2})
        engine.set_building_active_count(s, "factory", 1)
        rates = engine.theoretical_rates(s)
        self.assertEqual(rates["raw_a"], 2)
        self.assertEqual(rates["energy"], -1)

    def test_energy_breakdown_excludes_disabled_copies(self):
        s = GameState(resources={"energy": 5}, buildings={"gen": 2})
        engine.set_building_active_count(s, "gen", 1)
        # "gen" не disableable (пустой consumes) - ползунок для него молча не
        # применяется (set_building_active_count вернул бы ok=False), генерация
        # должна остаться полной по обеим копиям.
        stock, generation, consumption = engine.energy_breakdown(s)
        self.assertEqual(generation, 4)  # 2 копии x 2 - ползунок не сработал (gen не disableable)

    def test_disabled_copies_cannot_break_down(self):
        # random.random замокан на 0.0 - поломка гарантирована для КАЖДОЙ реально
        # обработанной копии (0.0 < breakdown_chance). Из 3 копий 1 выключена
        # ползунком - она обязана остаться нетронутой, сломаться должны только
        # 2 активные.
        s = GameState(resources={"energy": 100}, buildings={"factory": 3})
        engine.set_building_active_count(s, "factory", 2)
        with mock.patch("game.engine.random.random", return_value=0.0):
            report = engine.advance_turn(s)
        self.assertEqual(report.newly_broken.get("factory"), 2)
        self.assertEqual(s.broken["factory"], 2)


class ResourceCapTests(FixtureTestCase):
    def test_manual_action_gain_is_capped(self):
        s = GameState(resources={"raw_a": engine.MAX_RESOURCE_AMOUNT - 1})
        engine.do_manual_action(s, "gather_a")  # gain: raw_a +3, перевалило бы за потолок
        self.assertEqual(s.resources["raw_a"], engine.MAX_RESOURCE_AMOUNT)

    def test_advance_turn_production_is_capped(self):
        # "flaky" ничего не потребляет, так что производит независимо от времени
        # суток (в отличие от "solar", которое было бы неактивно на ходе 1 - ночь).
        s = GameState(resources={"raw_b": engine.MAX_RESOURCE_AMOUNT - 1}, buildings={"flaky": 1})
        engine.advance_turn(s)
        self.assertEqual(s.resources["raw_b"], engine.MAX_RESOURCE_AMOUNT)

    def test_mining_extraction_is_capped(self):
        s = GameState(resources={"mineral": engine.MAX_RESOURCE_AMOUNT - 1})
        field = {
            "size": 3,
            "cells": [
                {"resource": None, "hp": 1, "hp_max": 1, "broken": False, "revealed": False}
                for _ in range(9)
            ],
        }
        field["cells"][1 * 3 + 0] = {"resource": None, "hp": 1, "hp_max": 1, "broken": True, "revealed": True}
        field["cells"][1 * 3 + 1] = {"resource": "mineral", "hp": 2, "hp_max": 2, "broken": False, "revealed": False}
        s.active_mine_field = field
        engine.hit_mine_field(s, 1, 1)
        self.assertEqual(s.resources["mineral"], engine.MAX_RESOURCE_AMOUNT)

    def test_energy_craft_output_is_capped(self):
        s = GameState(
            resources={"energy": 23, "raw_b": engine.MAX_RESOURCE_AMOUNT - 1}, buildings={"mega_converter": 1},
        )
        engine.start_energy_recipe(s, "make_raw_b")
        engine.advance_turn(s)
        self.assertEqual(s.resources["raw_b"], engine.MAX_RESOURCE_AMOUNT)


class ResourceStorageTests(FixtureTestCase):
    """"Склад" (game/engine.py:storage_capacity/next_storage_tier_info/upgrade_storage) -
    фикстурный ресурс "limited_res" (base_capacity=10, тир 1 без технологии +20,
    тир 2 требует tech1 +50, см. tests/fixtures.py)."""

    def test_resource_without_storage_config_has_no_capacity(self):
        self.assertIsNone(engine.storage_capacity(GameState(), "raw_a"))
        self.assertIsNone(engine.next_storage_tier_info(GameState(), "raw_a"))

    def test_base_capacity_without_any_purchased_tier(self):
        s = GameState()
        self.assertEqual(engine.storage_capacity(s, "limited_res"), 10)
        info = engine.next_storage_tier_info(s, "limited_res")
        self.assertEqual(info["capacity_add"], 20)

    def test_upgrade_storage_pays_cost_and_raises_capacity_tier_by_tier(self):
        s = GameState(resources={"raw_a": 100})
        result = engine.upgrade_storage(s, "limited_res")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 95)
        self.assertEqual(s.storage_tier["limited_res"], 1)
        self.assertEqual(engine.storage_capacity(s, "limited_res"), 30)  # 10 + 20

        # Второй тир требует tech1 - без неё покупка отказывает голым сообщением,
        # а не молча списывает ресурсы.
        result = engine.upgrade_storage(s, "limited_res")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["raw_a"], 95)  # не списано
        self.assertEqual(s.storage_tier["limited_res"], 1)

        s.techs.add("tech1")
        result = engine.upgrade_storage(s, "limited_res")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 80)  # 95 - 15
        self.assertEqual(engine.storage_capacity(s, "limited_res"), 80)  # 10 + 20 + 50

        # Тиров больше нет.
        self.assertIsNone(engine.next_storage_tier_info(s, "limited_res"))
        result = engine.upgrade_storage(s, "limited_res")
        self.assertFalse(result.ok)

    def test_infinite_storage_tier_removes_cap_entirely(self):
        # fixtures.RESOURCE_STORAGE["vault_res"] - тир 1 обычный (+20), тир 2 -
        # "infinite": True вместо capacity_add (game/engine.py:storage_capacity).
        s = GameState(resources={"raw_a": 100})
        self.assertEqual(engine.storage_capacity(s, "vault_res"), 10)
        engine.upgrade_storage(s, "vault_res")
        self.assertEqual(engine.storage_capacity(s, "vault_res"), 30)  # 10 + 20
        result = engine.upgrade_storage(s, "vault_res")
        self.assertTrue(result.ok, result.message)
        self.assertIn("без ограничений", result.message)
        # Тот же None, что у ресурса вообще без storage - клампа в конце хода нет.
        self.assertIsNone(engine.storage_capacity(s, "vault_res"))
        self.assertIsNone(engine.next_storage_tier_info(s, "vault_res"))

    def test_upgradable_storage_excludes_tech_locked_tier(self):
        s = GameState(resources={"raw_a": 100})
        self.assertIn("limited_res", engine.upgradable_storage(s))
        engine.upgrade_storage(s, "limited_res")
        # Следующий тир существует, но требует tech1 - не должен считаться "можно купить".
        self.assertNotIn("limited_res", engine.upgradable_storage(s))
        s.techs.add("tech1")
        self.assertIn("limited_res", engine.upgradable_storage(s))

    def test_advance_turn_clamps_excess_down_to_capacity(self):
        s = GameState(resources={"limited_res": 25})
        engine.advance_turn(s)
        self.assertEqual(s.resources["limited_res"], 10)  # обрезано до базовой вместимости

    def test_advance_turn_does_not_touch_resources_without_storage_config(self):
        s = GameState(resources={"raw_a": 10_000_000})
        engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 10_000_000)

    def test_autocraft_ignores_storage_capacity_of_an_intermediate_resource(self):
        # "Склад не влияет на автокрафт" (см. CLAUDE.md): промежуточные 2 mid живут в
        # резерве заказа, а не на складе, - крошечная вместимость mid им не мешает.
        s = GameState(resources={"raw_a": 100, "raw_b": 100}, buildings={"automation_hub": 1})
        with mock.patch.dict(engine.RESOURCE_STORAGE, {"mid": {"base_capacity": 1, "tiers": []}}):
            result = engine.do_manual_action(s, "craft_final")
            self.assertTrue(result.pending, result.message)
            engine.advance_turn(s)
        self.assertEqual(s.resources["final"], 1)
        self.assertEqual(s.resources.get("mid", 0), 0)

class TechEffectTests(FixtureTestCase):
    def test_wildcard_effect_reduces_breakdown_for_all_buildings(self):
        base = engine.effective_breakdown_chance("factory", set())
        with_tech = engine.effective_breakdown_chance("factory", {"tech3"})
        self.assertLess(with_tech, base)

    def test_specific_building_effect_boosts_production(self):
        base_produces, _ = engine.effective_building_stats("factory", set())
        boosted_produces, _ = engine.effective_building_stats("factory", {"tech2"})
        self.assertGreater(boosted_produces["raw_a"], base_produces["raw_a"])


class TimeTests(FixtureTestCase):
    def test_current_time_derives_day_and_hour_from_turn(self):
        s = GameState(turn=1)
        self.assertEqual(engine.current_time(s), {"day": 1, "hour": 0, "is_day": False})
        s.turn = 3  # (3-1) хода * 3ч/ход = 6ч - начало дня по фикстуре (day_start_hour=6)
        self.assertEqual(engine.current_time(s), {"day": 1, "hour": 6, "is_day": True})
        s.turn = 10  # (10-1)*3 = 27ч -> день 2, час 3 (ночь)
        self.assertEqual(engine.current_time(s), {"day": 2, "hour": 3, "is_day": False})

    def test_day_only_building_idles_at_night_without_breaking_or_consuming(self):
        s = GameState(turn=1, resources={"raw_a": 10}, buildings={"solar": 1})  # ход 1 = час 0, ночь
        report = engine.advance_turn(s)
        self.assertEqual(report.ran["solar"], (0, 1))
        self.assertEqual(s.resources.get("raw_b", 0), 0)

    def test_day_only_building_works_during_daytime(self):
        s = GameState(turn=3, resources={"raw_a": 10}, buildings={"solar": 1})  # ход 3 = час 6, день
        report = engine.advance_turn(s)
        self.assertEqual(report.ran["solar"], (1, 0))
        self.assertEqual(s.resources.get("raw_b", 0), 1)

    def test_is_building_active_now_respects_active_period(self):
        self.assertFalse(engine.is_building_active_now("solar", daytime=False))
        self.assertTrue(engine.is_building_active_now("solar", daytime=True))
        self.assertTrue(engine.is_building_active_now("gen", daytime=False))  # без active_period - всегда активен
        self.assertTrue(engine.is_building_active_now("gen", daytime=True))

    def test_theoretical_rates_zero_out_active_period_building_at_wrong_time(self):
        # Раньше показывало полную теоретическую ставку даже ночью для дневных построек -
        # игрок видел "генерация +N/ход", хотя следующий advance_turn реально даст 0.
        s = GameState(turn=1, buildings={"solar": 1})  # ход 1 = час 0, ночь
        self.assertEqual(engine.theoretical_rates(s).get("raw_b", 0), 0)
        s.turn = 3  # ход 3 = час 6, день
        self.assertEqual(engine.theoretical_rates(s).get("raw_b", 0), 1)


class LocationTests(FixtureTestCase):
    def test_available_locations_lists_all_unlocked(self):
        s = GameState()
        self.assertEqual(
            set(engine.available_locations(s)),
            {"yard", "shaft", "workshop_loc", "mechanisms", "sub_workshop", "converter", "hunting_loc", "grid_loc"},
        )

    def test_manual_actions_at_filters_by_location_and_tech(self):
        s = GameState()
        self.assertEqual(set(engine.manual_actions_at("yard", s)), {"gather_a", "gather_b", "catch_critter_a"})
        self.assertEqual(engine.manual_actions_at("shaft", s), [])  # locked_action требует tech1
        s.techs.add("tech1")
        self.assertEqual(engine.manual_actions_at("shaft", s), ["locked_action"])

    def test_mine_location_id_matches_shaft_fixture(self):
        self.assertEqual(engine.MINE_LOCATION_ID, "shaft")

    def test_converter_location_id_matches_fixture(self):
        self.assertEqual(engine.CONVERTER_LOCATION_ID, "converter")

    def test_converter_location_is_nested_inside_mechanisms(self):
        # спец-локация "converter" не лежит в корне сама по себе - она вложена в
        # обычную (ничем не защищённую) локацию "mechanisms", ровно как в реальном
        # content.json (см. CLAUDE.md про генерализацию Механизмов/Преобразователя).
        s = GameState()
        self.assertEqual(engine.location_children("mechanisms", s), ["converter"])

    def test_location_children_lists_only_nested_locations_of_the_given_parent(self):
        s = GameState()
        self.assertEqual(engine.location_children("workshop_loc", s), ["sub_workshop"])
        self.assertEqual(engine.location_children("sub_workshop", s), [])
        self.assertEqual(engine.location_children("yard", s), [])

    def test_location_children_at_root_excludes_nested_locations(self):
        s = GameState()
        root = set(engine.location_children(None, s))
        self.assertEqual(root, {"yard", "shaft", "workshop_loc", "mechanisms", "hunting_loc", "grid_loc"})
        self.assertNotIn("sub_workshop", root)  # у неё есть родитель - она не в корне

    def test_manual_action_without_location_lives_at_root(self):
        s = GameState()
        self.assertIn("loose_action", engine.manual_actions_at(None, s))
        self.assertNotIn("loose_action", engine.manual_actions_at("yard", s))


class TechTreeTests(FixtureTestCase):
    def test_tech_unlocks_finds_everything_gated_by_the_tech(self):
        unlocks = engine.tech_unlocks("tech1")
        self.assertIn("factory", unlocks["buildings"])
        self.assertIn("locked_action", unlocks["manual_actions"])
        self.assertIn("Тир 3", unlocks["tools"])


class LabTierTests(FixtureTestCase):
    def test_lab_tier_capacity_defaults_to_1_without_any_lab(self):
        s = GameState()
        self.assertEqual(engine.lab_tier_capacity(s), 1)

    def test_lab_tier_capacity_reflects_highest_working_lab(self):
        s = GameState(buildings={"lab2": 1})
        self.assertEqual(engine.lab_tier_capacity(s), 2)

    def test_broken_lab_does_not_count_towards_capacity(self):
        s = GameState(buildings={"lab2": 1}, broken={"lab2": 1})
        self.assertEqual(engine.lab_tier_capacity(s), 1)

    def test_tier1_tech_researchable_without_any_lab(self):
        s = GameState(resources={"raw_a": 5}, solved_hypotheses={"tech1"})
        result = engine.research(s, "tech1")
        self.assertTrue(result.ok)
        self.assertIn("tech1", s.techs)

    def test_tier2_tech_fails_without_a_tier2_lab(self):
        s = GameState(resources={"raw_a": 5}, solved_hypotheses={"tech_tier2"})
        result = engine.research(s, "tech_tier2")
        self.assertFalse(result.ok)
        self.assertNotIn("tech_tier2", s.techs)
        self.assertIn("лаборатория", result.message.lower())

    def test_tier2_tech_succeeds_once_the_lab_is_built_and_working(self):
        s = GameState(resources={"raw_a": 5}, buildings={"lab2": 1}, solved_hypotheses={"tech_tier2"})
        result = engine.research(s, "tech_tier2")
        self.assertTrue(result.ok)
        self.assertIn("tech_tier2", s.techs)

    def test_tier2_tech_fails_again_once_the_lab_breaks(self):
        s = GameState(resources={"raw_a": 5}, buildings={"lab2": 1}, broken={"lab2": 1}, solved_hypotheses={"tech_tier2"})
        result = engine.research(s, "tech_tier2")
        self.assertFalse(result.ok)


class MultiTurnResearchTests(FixtureTestCase):
    """techs.*.research_turns > 1 (см. TECHS["tech_tier2_slow"], research_turns=5) -
    research() запускает state.active_research вместо мгновенного завершения,
    как техи без research_turns (tech1/tech_tier2 и т.п., покрыты выше)."""

    def test_starting_multiturn_research_pays_cost_but_defers_completion(self):
        s = GameState(resources={"raw_a": 5}, buildings={"lab2": 1}, solved_hypotheses={"tech_tier2_slow"})
        result = engine.research(s, "tech_tier2_slow")
        self.assertTrue(result.ok)
        self.assertIn("Начато", result.message)
        self.assertNotIn("tech_tier2_slow", s.techs)
        self.assertEqual(s.resources["raw_a"], 4)  # cost списан сразу
        self.assertEqual(s.active_research, {"tech_id": "tech_tier2_slow", "turns_total": 5, "turns_done": 0, "cost": {"raw_a": 1}})

    def test_advancing_turns_completes_research_after_turns_total(self):
        s = GameState(resources={"raw_a": 5}, buildings={"lab2": 1}, solved_hypotheses={"tech_tier2_slow"})
        engine.research(s, "tech_tier2_slow")
        for _ in range(4):
            report = engine.advance_turn(s)
            self.assertIsNone(report.research_completed)
            self.assertNotIn("tech_tier2_slow", s.techs)
        report = engine.advance_turn(s)
        self.assertEqual(report.research_completed, "tech_tier2_slow")
        self.assertIn("tech_tier2_slow", s.techs)
        self.assertIsNone(s.active_research)

    def test_switching_to_a_different_research_cancels_the_old_one_and_refunds_it(self):
        s = GameState(
            resources={"raw_a": 10},
            buildings={"lab2": 1},
            solved_hypotheses={"tech_tier2_slow", "tech_tier2"},
        )
        engine.research(s, "tech_tier2_slow")
        self.assertEqual(s.resources["raw_a"], 9)  # cost старого исследования уже списан
        result = engine.research(s, "tech_tier2")  # мгновенная технология (без research_turns)
        self.assertTrue(result.ok)
        self.assertIn("отменено, затраты возвращены", result.message)
        self.assertIn("Изучено", result.message)
        self.assertNotIn("tech_tier2_slow", s.techs)
        self.assertIn("tech_tier2", s.techs)
        self.assertIsNone(s.active_research)  # новая - мгновенная, слот снова пуст
        self.assertEqual(s.resources["raw_a"], 9)  # старый cost вернулся (10), новый списан (-1) => 9

    def test_switching_between_two_multiturn_researches_starts_a_fresh_job(self):
        s = GameState(
            resources={"raw_a": 10, "raw_b": 10},
            buildings={"lab2": 1},
            solved_hypotheses={"tech_tier2_slow", "tech_tier2_slow_b"},
        )
        engine.research(s, "tech_tier2_slow")
        engine.advance_turn(s)  # turns_done=1 из turns_total=5
        self.assertEqual(s.resources["raw_a"], 9)
        result = engine.research(s, "tech_tier2_slow_b")
        self.assertTrue(result.ok)
        self.assertIn("«Технология тира 2 (долгая)» отменено, затраты возвращены", result.message)
        self.assertEqual(s.resources["raw_a"], 10)  # старый cost вернулся целиком
        self.assertEqual(s.resources["raw_b"], 9)  # новый cost списан
        # Новый job СВЕЖИЙ (своё research_turns=4, turns_done=0), не донашивает
        # turns_done=1, накопленный отменённым tech_tier2_slow.
        self.assertEqual(s.active_research, {"tech_id": "tech_tier2_slow_b", "turns_total": 4, "turns_done": 0, "cost": {"raw_b": 1}})

    def test_reclicking_the_same_active_research_is_a_no_op(self):
        s = GameState(resources={"raw_a": 10}, buildings={"lab2": 1}, solved_hypotheses={"tech_tier2_slow"})
        engine.research(s, "tech_tier2_slow")
        result = engine.research(s, "tech_tier2_slow")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Уже исследуется.")
        self.assertEqual(s.active_research, {"tech_id": "tech_tier2_slow", "turns_total": 5, "turns_done": 0, "cost": {"raw_a": 1}})
        self.assertEqual(s.resources["raw_a"], 9)  # cost не списан повторно

    def test_failed_switch_does_not_touch_the_original_research(self):
        # raw_a хватает РОВНО на первую технологию - вторая (тоже стоит raw_a) не
        # может быть оплачена и не может быть докрафчена (нет автокрафт-построек в
        # этой фикстуре) - значит research() должен вернуть отказ ДО того, как
        # тронул старый active_research (см. порядок проверок в research()).
        s = GameState(
            resources={"raw_a": 1},
            buildings={"lab2": 1},
            solved_hypotheses={"tech_tier2_slow", "tech_tier2"},
        )
        engine.research(s, "tech_tier2_slow")
        original = dict(s.active_research)
        result = engine.research(s, "tech_tier2")
        self.assertFalse(result.ok)
        self.assertEqual(s.active_research, original)  # старый прогресс не тронут
        self.assertEqual(s.resources["raw_a"], 0)  # не возвращён и не списан повторно

    def test_cancel_research_refunds_cost_and_clears_slot(self):
        s = GameState(resources={"raw_a": 5}, buildings={"lab2": 1}, solved_hypotheses={"tech_tier2_slow"})
        engine.research(s, "tech_tier2_slow")
        result = engine.cancel_research(s)
        self.assertTrue(result.ok)
        self.assertIsNone(s.active_research)
        self.assertEqual(s.resources["raw_a"], 5)  # cost возвращён целиком

    def test_cancel_research_fails_when_nothing_is_active(self):
        s = GameState()
        result = engine.cancel_research(s)
        self.assertFalse(result.ok)

    def test_research_speed_upgrade_reduces_turns_for_matching_tier_only(self):
        s = GameState(resources={"raw_a": 10}, buildings={"lab2": 1})
        engine.purchase_building_upgrade(s, "lab2", "lab2_speed")
        self.assertEqual(engine.tech_research_turns("tech_tier2_slow", s), 3)  # 5 - 2
        # Тир 1 не имеет лаборатории вовсе - апгрейд лаборатории тира 2 его не касается.
        self.assertEqual(engine.tech_research_turns("tech1", s), 1)

    def test_research_speed_upgrade_shortens_the_actual_job(self):
        s = GameState(resources={"raw_a": 10}, buildings={"lab2": 1}, solved_hypotheses={"tech_tier2_slow"})
        engine.purchase_building_upgrade(s, "lab2", "lab2_speed")
        engine.research(s, "tech_tier2_slow")
        self.assertEqual(s.active_research["turns_total"], 3)


class BuildingUpgradesTests(FixtureTestCase):
    BASE_PRODUCES = {"raw_b": 2, "energy": 4}
    BASE_CONSUMES = {"raw_a": 2, "energy": 1}

    def test_effective_stats_without_any_purchase_equal_the_base_recipe(self):
        produces, consumes = engine.effective_building_stats("upgradable_factory", set(), {})
        self.assertEqual(produces, self.BASE_PRODUCES)
        self.assertEqual(consumes, self.BASE_CONSUMES)

    def test_purchase_fails_without_owning_the_building(self):
        s = GameState(resources={"raw_a": 100})
        result = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        self.assertFalse(result.ok)
        self.assertFalse(engine.is_building_upgrade_purchased(s, "upgradable_factory", "uf_scale"))

    def test_purchase_fails_when_upgrade_is_tech_locked(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        result = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_gen_eff")
        self.assertFalse(result.ok)

    def test_purchase_succeeds_once_tech_is_researched(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1}, techs={"tech1"})
        result = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_gen_eff")
        self.assertTrue(result.ok)
        self.assertTrue(engine.is_building_upgrade_purchased(s, "upgradable_factory", "uf_gen_eff"))

    def test_purchase_fails_if_already_purchased(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        result = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        self.assertFalse(result.ok)

    def test_purchase_spends_the_upgrade_cost(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        self.assertEqual(s.resources["raw_a"], 95)

    def test_scale_upgrade_multiplies_produces_and_consumes_alike(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        produces, consumes = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        self.assertEqual(produces, {"raw_b": 4, "energy": 8})
        self.assertEqual(consumes, {"raw_a": 4, "energy": 2})

    def test_energy_efficiency_only_touches_energy_consumption(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_energy_eff")
        produces, consumes = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        self.assertEqual(produces, self.BASE_PRODUCES)
        self.assertEqual(consumes, {"raw_a": 2, "energy": 0.5})

    def test_generation_efficiency_only_touches_energy_production(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1}, techs={"tech1"})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_gen_eff")
        produces, consumes = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        self.assertEqual(produces, {"raw_b": 2, "energy": 6})
        self.assertEqual(consumes, self.BASE_CONSUMES)

    def test_all_three_upgrades_stack_correctly_together(self):
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1}, techs={"tech1"})
        for upgrade_id in ("uf_scale", "uf_energy_eff", "uf_gen_eff"):
            result = engine.purchase_building_upgrade(s, "upgradable_factory", upgrade_id)
            self.assertTrue(result.ok, result.message)
        produces, consumes = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        # scale x2 сначала: produces {raw_b:4, energy:8}, consumes {raw_a:4, energy:2} -
        # затем energy_efficiency x0.5 на consumes.energy (2 -> 1) и generation_efficiency
        # x1.5 на produces.energy (8 -> 12), никакого взаимного влияния друг на друга.
        self.assertEqual(produces, {"raw_b": 4, "energy": 12})
        self.assertEqual(consumes, {"raw_a": 4, "energy": 1})

    def test_same_type_upgrades_do_not_multiply_only_the_strongest_counts(self):
        # По прямому пожеланию: прогрессия "1:1 -> 2:2 -> 3:3 -> 4:4" не должна
        # ломаться накоплением дробных множителей (4/3, 5/4, ... не имеют точного
        # float-представления, и с ростом уже накопленного множителя погрешность
        # округления растёт). Поэтому купленные улучшения ОДНОГО типа не
        # перемножаются - действует только самое сильное (см. _apply_building_upgrades).
        # uf_scale = x2, uf_scale_more = x3 (АБСОЛЮТНЫЕ множители относительно базы,
        # не друг относительно друга) - купив ОБА, итог должен быть ровно x3, а не
        # x6 (было бы при перемножении) и не x2 (если бы побеждало первое купленное).
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        for upgrade_id in ("uf_scale", "uf_scale_more"):
            result = engine.purchase_building_upgrade(s, "upgradable_factory", upgrade_id)
            self.assertTrue(result.ok, result.message)
        produces, consumes = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        self.assertEqual(produces, {"raw_b": 6, "energy": 12})
        self.assertEqual(consumes, {"raw_a": 6, "energy": 3})

    def test_scale_upgrade_purchase_is_now_required_to_go_in_order(self):
        # По прямому пожеланию (добавлено 2026-09-02): следующий уровень
        # прогрессии больше НЕЛЬЗЯ купить, пока не куплен предыдущий ТОГО ЖЕ
        # ТИПА - uf_scale_more (x3) идёт СРАЗУ ПОСЛЕ uf_scale (x2) в списке
        # upgrades у upgradable_factory (см. fixtures.py), так что uf_scale -
        # его прямой предшественник (engine.building_upgrade_prerequisite).
        # Раньше порядок покупки был не важен ("сильнейшее побеждает"
        # независимо от порядка) - теперь порядок стал ОБЯЗАТЕЛЬНЫМ условием
        # самой покупки, а не только влияет на то, что "победит" в расчётах.
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        result = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale_more")
        self.assertFalse(result.ok)
        self.assertIn("Масштаб II", result.message)  # имя предшественника (uf_scale) в сообщении
        self.assertFalse(engine.is_building_upgrade_purchased(s, "upgradable_factory", "uf_scale_more"))
        self.assertEqual(s.resources["raw_a"], 100)  # ничего не списано за проваленную покупку

        first = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        self.assertTrue(first.ok, first.message)
        second = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale_more")
        self.assertTrue(second.ok, second.message)
        produces, _ = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        self.assertEqual(produces["raw_b"], 6)  # 2 база x3 - купленный по порядку прогресс всё ещё "только сильнейшее"

    def test_building_upgrade_prerequisite_is_none_for_first_tier_and_other_types(self):
        # uf_scale - первый в своей прогрессии (scale) - предшественника нет.
        self.assertIsNone(engine.building_upgrade_prerequisite("upgradable_factory", "uf_scale"))
        # uf_energy_eff - единственный своего типа у постройки - тоже без предшественника,
        # чужой тип (scale) в его прогрессию не входит.
        self.assertIsNone(engine.building_upgrade_prerequisite("upgradable_factory", "uf_energy_eff"))
        self.assertEqual(engine.building_upgrade_prerequisite("upgradable_factory", "uf_scale_more")["id"], "uf_scale")

    def test_advance_turn_applies_scale_upgrade_to_real_production(self):
        s = GameState(resources={"raw_a": 100, "energy": 100}, buildings={"upgradable_factory": 1})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 100 - 5 - 4)  # -5 цена улучшения, -4 (2 база x2) потребление
        self.assertEqual(s.resources.get("raw_b", 0), 4)  # 2 база x2

    def test_pending_retry_dispatches_building_upgrade_with_composite_target_id(self):
        # _ORDER_RETRY принимает ВЕСЬ заказ (не голый target_id) - см.
        # CLAUDE.md про "Ползунок масштаба ручного действия": retry-лямбдам нужен
        # доступ к дополнительным полям вроде "scale" у ручных действий.
        s = GameState(resources={"raw_a": 5}, buildings={"upgradable_factory": 1})
        pending = {"kind": "building_upgrade", "target_id": "upgradable_factory:uf_scale", "label": "x"}
        result = engine._ORDER_RETRY["building_upgrade"](s, pending)
        self.assertTrue(result.ok)
        self.assertTrue(engine.is_building_upgrade_purchased(s, "upgradable_factory", "uf_scale"))


class MechanismLocationAndActionUpgradeTests(FixtureTestCase):
    """"Механизм" (mechanism_a) - постройка без единого produces/consumes: весь её
    эффект выражается через requires_building у ЛОКАЦИИ (mechanism_a_loc) и у
    РУЧНОГО ДЕЙСТВИЯ (mechanism_a_action) внутри неё - см. fixtures.py."""

    def test_location_hidden_without_the_mechanism_built(self):
        s = GameState()
        self.assertNotIn("mechanism_a_loc", engine.available_locations(s))

    def test_location_appears_once_the_mechanism_is_built_and_working(self):
        s = GameState(buildings={"mechanism_a": 1})
        self.assertIn("mechanism_a_loc", engine.available_locations(s))

    def test_location_hides_again_once_the_mechanism_breaks(self):
        s = GameState(buildings={"mechanism_a": 1}, broken={"mechanism_a": 1})
        self.assertNotIn("mechanism_a_loc", engine.available_locations(s))

    def test_effective_action_stats_equal_base_without_any_upgrade(self):
        s = GameState(buildings={"mechanism_a": 1})
        cost, gain = engine.effective_manual_action_stats("mechanism_a_action", s)
        self.assertEqual(cost, {"raw_a": 2})
        self.assertEqual(gain, {"mech_output": 3})

    def test_scale_upgrade_of_the_mechanism_doubles_the_dependent_action(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        cost, gain = engine.effective_manual_action_stats("mechanism_a_action", s)
        self.assertEqual(cost, {"raw_a": 4})
        self.assertEqual(gain, {"mech_output": 6})

    def test_do_manual_action_actually_pays_and_grants_the_upgraded_amounts(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        before = s.resources["raw_a"]
        result = engine.do_manual_action(s, "mechanism_a_action")
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["raw_a"], before - 4)
        self.assertEqual(s.resources["mech_output"], 6)

    def test_action_without_requires_building_is_unaffected_by_any_upgrade(self):
        # craft_mid не зависит ни от одной постройки - эффективные cost/gain должны
        # остаться базовыми, даже если у игрока куплены другие улучшения где-то ещё.
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1, "mechanism_a": 1})
        engine.purchase_building_upgrade(s, "upgradable_factory", "uf_scale")
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        cost, gain = engine.effective_manual_action_stats("craft_mid", s)
        self.assertEqual(cost, {"raw_a": 4})
        self.assertEqual(gain, {"mid": 1})


class ToolScalingManualActionTests(FixtureTestCase):
    """manual_actions.*.tool_scaling (см. fixtures.MANUAL_ACTIONS["gather_tool_scaled"]) -
    домножает gain действия на ТЕКУЩИЙ тир указанного инструмента, cost не трогает."""

    def test_base_tier_is_a_neutral_multiplier(self):
        s = GameState()
        cost, gain = engine.effective_manual_action_stats("gather_tool_scaled", s)
        self.assertEqual(cost, {})
        self.assertEqual(gain, {"raw_a": 2})

    def test_higher_tool_tier_scales_gain_but_not_cost(self):
        s = GameState(tool_tiers={"pickaxe": 3})
        cost, gain = engine.effective_manual_action_stats("gather_tool_scaled", s)
        self.assertEqual(cost, {})
        self.assertEqual(gain, {"raw_a": 6})

    def test_do_manual_action_grants_the_tool_scaled_amount(self):
        s = GameState(tool_tiers={"pickaxe": 2})
        result = engine.do_manual_action(s, "gather_tool_scaled")
        self.assertTrue(result.ok)
        self.assertEqual(s.resources.get("raw_a"), 4)


class UsesToolDurabilityManualActionTests(FixtureTestCase):
    """manual_actions.*.uses_tool_durability (см.
    fixtures.MANUAL_ACTIONS["gather_wears_wrench"], TOOLS["wrench"] с
    max_durability=2 на тире 2) - действие тратит 1 прочность выбранного
    инструмента за каждое выполнение и блокируется, когда он сломан."""

    def test_tool_without_durability_is_unaffected(self):
        # тир 1 ("Без ключа") вовсе не имеет max_durability - не тратится, не ломается.
        s = GameState()
        result = engine.do_manual_action(s, "gather_wears_wrench")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources.get("raw_a"), 1)
        self.assertIsNone(engine.tool_durability_remaining(s, "wrench"))

    def test_action_consumes_one_durability_per_run(self):
        s = GameState(tool_tiers={"wrench": 2})
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 2)
        result = engine.do_manual_action(s, "gather_wears_wrench")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 1)

    def test_broken_tool_blocks_the_action_without_spending_cost_or_durability(self):
        s = GameState(tool_tiers={"wrench": 2}, tool_durability={"wrench": 0})
        self.assertTrue(engine.tool_is_broken(s, "wrench"))
        result = engine.do_manual_action(s, "gather_wears_wrench")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 0)


class MechanismScaleSliderTests(FixtureTestCase):
    """"Ползунок масштаба ручного действия" (см. CLAUDE.md) - купленный scale-
    апгрейд теперь задаёт только ВЕРХНЮЮ ГРАНИЦУ, а конкретный масштаб КАЖДОГО
    клика выбирает игрок (engine.mechanism_scale_cap/do_manual_action(scale=...))."""

    def test_scale_cap_is_1_without_any_purchased_scale_upgrade(self):
        s = GameState(buildings={"mechanism_a": 1})
        self.assertEqual(engine.mechanism_scale_cap("mechanism_a", s), 1)

    def test_scale_cap_matches_the_purchased_upgrade_level(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        self.assertEqual(engine.mechanism_scale_cap("mechanism_a", s), 2)

    def test_scale_cap_counts_every_purchase_of_a_repeatable_scale(self):
        # Как _apply_building_upgrades: repeatable-scale, купленный 3 раза, - x6,
        # и ползунок обязан доставать до того же x6.
        upgrade = next(u for u in engine.BUILDINGS["mechanism_a"]["upgrades"] if u["id"] == "mech_a_scale")
        upgrade = {**upgrade, "repeatable": True, "cost_growth": 2}
        building = {**engine.BUILDINGS["mechanism_a"], "upgrades": [upgrade]}
        engine.BUILDINGS = {**engine.BUILDINGS, "mechanism_a": building}
        s = GameState(buildings={"mechanism_a": 1}, building_upgrades={"mechanism_a": ["mech_a_scale"] * 3})
        self.assertEqual(engine.mechanism_scale_cap("mechanism_a", s), 6)
        cost, _ = engine.effective_manual_action_stats("mechanism_a_action", s)
        cost_at_cap, _ = engine.effective_manual_action_stats("mechanism_a_action", s, chosen_scale=6)
        self.assertEqual(cost, cost_at_cap)

    def test_do_manual_action_at_scale_1_pays_base_amounts_despite_upgrade(self):
        # mech_a_scale куплен (cap=2), но явный scale=1 не обязывает игрока к x2 -
        # ровно то, о чём была просьба: не заставлять делать сразу много.
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        before = s.resources["raw_a"]
        result = engine.do_manual_action(s, "mechanism_a_action", scale=1)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], before - 2)  # база, не x2
        self.assertEqual(s.resources["mech_output"], 3)

    def test_do_manual_action_at_scale_above_cap_is_clamped_down(self):
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")  # cap=2
        before = s.resources["raw_a"]
        result = engine.do_manual_action(s, "mechanism_a_action", scale=5)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], before - 4)  # зажато до cap=2, не x5
        self.assertEqual(s.resources["mech_output"], 6)

    def test_do_manual_action_without_scale_still_defaults_to_full_purchased_level(self):
        # scale=None (не передан вовсе) - поведение НЕ меняется относительно
        # того, что было ДО этой фичи: автоматически действует полный масштаб.
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        before = s.resources["raw_a"]
        result = engine.do_manual_action(s, "mechanism_a_action")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], before - 4)
        self.assertEqual(s.resources["mech_output"], 6)

    def test_scale_is_ignored_for_actions_without_requires_building(self):
        # craft_mid ни к какому механизму не привязано - scale ни на что не влияет.
        s = GameState(resources={"raw_a": 100})
        before = s.resources["raw_a"]
        result = engine.do_manual_action(s, "craft_mid", scale=3)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], before - 4)  # база, не x3

    def test_pending_manual_action_retry_preserves_the_chosen_scale(self):
        # Выполнение заказа по готовности (_ORDER_RETRY) обязано взять ТОТ ЖЕ масштаб,
        # что выбрал игрок при клике, а не откатиться на масштаб по умолчанию.
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")  # cap=2
        order = {"kind": "manual", "target_id": "mechanism_a_action", "label": "«тест»", "scale": 1}
        result = engine._ORDER_RETRY["manual"](s, order)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["mech_output"], 3)  # x1 (сохранённый выбор), не x2

class FractionalResourceRoundingTests(FixtureTestCase):
    def test_integer_resource_consumption_rounds_up(self):
        # uf_energy_eff: consumes.energy 1 -> x0.5 = 0.5, но "raw_a" в consumes
        # (2 -> x1 = 2) не дробный - только energy (fractional=True в фикстурах)
        # реально проверяет "не округляется", а не дробный ресурс округлился бы вверх.
        s = GameState(resources={"raw_a": 100}, buildings={"upgradable_factory": 1})
        result = engine.purchase_building_upgrade(s, "upgradable_factory", "uf_energy_eff")
        self.assertTrue(result.ok, result.message)
        _, consumes = engine.effective_building_stats("upgradable_factory", s.techs, s.building_upgrades)
        self.assertEqual(consumes["energy"], 0.5)  # энергия дробная - не округляется

    def test_non_fractional_resource_scale_result_stays_a_clean_integer(self):
        # "mech_output" не помечен fractional; scale всегда целый (валидатор это
        # требует), так что 3 x 2 остаётся ровно 6, без плавающей точки.
        s = GameState(resources={"raw_a": 100}, buildings={"mechanism_a": 1})
        engine.purchase_building_upgrade(s, "mechanism_a", "mech_a_scale")
        _, gain = engine.effective_manual_action_stats("mechanism_a_action", s)
        self.assertEqual(gain["mech_output"], 6)
        self.assertIsInstance(gain["mech_output"], int)

    def test_fractional_base_value_on_non_fractional_resource_rounds_up(self):
        # Дробное значение может прийти не только от улучшения, но и просто быть
        # так написано в content.json - _round_amounts округляет ЛЮБОЙ источник
        # дробности у нецелочисленного ресурса, а не только результат апгрейда.
        produces, _ = engine.effective_building_stats("fractional_base_building", set(), {})
        self.assertEqual(produces["raw_b"], 2)  # math.ceil(1.5)


class AutocraftPriorityTests(FixtureTestCase):
    def test_resource_recipe_picks_the_highest_priority_action_over_insertion_order(self):
        # priority_low объявлен РАНЬШЕ priority_high в fixtures.MANUAL_ACTIONS - если
        # бы приоритет не учитывался, победил бы именно он (см. game/engine.py:
        # resource_recipe - раньше был просто "первый найденный").
        s = GameState()
        self.assertEqual(engine.resource_recipe(s)["prioritized"], "priority_high")

    def test_player_override_beats_the_content_default_priority(self):
        s = GameState()
        engine.set_action_priority(s, "priority_low", 10)
        self.assertEqual(engine.resource_recipe(s)["prioritized"], "priority_low")

    def test_resetting_override_reverts_to_the_content_default(self):
        s = GameState()
        engine.set_action_priority(s, "priority_low", 10)
        engine.set_action_priority(s, "priority_low", None)
        self.assertEqual(engine.resource_recipe(s)["prioritized"], "priority_high")

    def test_action_priority_reflects_default_when_no_override_is_set(self):
        s = GameState()
        self.assertEqual(engine.action_priority(s, "priority_high"), 5)
        self.assertEqual(engine.action_priority(s, "priority_low"), 0)

    def test_set_action_priority_rejects_unknown_action(self):
        s = GameState()
        result = engine.set_action_priority(s, "does_not_exist", 3)
        self.assertFalse(result.ok)


class ChanceBasedActionTests(FixtureTestCase):
    """"Шанс потребления/получения" (game/engine.py:is_manual_action_deterministic/
    _roll_chance_amounts) - см. CLAUDE.md."""

    def test_action_with_full_chances_is_deterministic(self):
        self.assertTrue(engine.is_manual_action_deterministic("gather_a"))
        self.assertTrue(engine.is_manual_action_deterministic("craft_final"))

    def test_action_with_partial_gain_chance_is_not_deterministic(self):
        self.assertFalse(engine.is_manual_action_deterministic("craft_lucky"))

    def test_action_with_partial_cost_chance_is_not_deterministic(self):
        self.assertFalse(engine.is_manual_action_deterministic("craft_risky"))

    def test_resource_recipe_excludes_actions_with_probabilistic_gain(self):
        # craft_lucky имеет непустой cost (формально годится в кандидаты), но
        # gain_chance < 1 на его единственный выход - должен быть исключён целиком.
        s = GameState()
        recipe = engine.resource_recipe(s)
        self.assertNotIn("lucky_output", recipe)

    def test_resource_recipe_excludes_actions_with_probabilistic_cost(self):
        # craft_risky: gain гарантирован (нет gain_chance), но cost_chance < 1 -
        # весь рецепт всё равно исключается, а не только "рискованная" часть цены.
        s = GameState()
        recipe = engine.resource_recipe(s)
        self.assertNotIn("risky_output", recipe)

    def test_autocraft_reports_shortage_instead_of_crafting_a_probabilistic_resource(self):
        # У lucky_output нет гарантированного рецепта - для автокрафта это сырьё:
        # заказ отклоняется, ничего не изъято.
        s = GameState(resources={"raw_a": 100}, buildings={"automation_hub": 1})
        failure, order = engine._place_order(s, "recipe", "x", "тест", {"lucky_output": 10})
        self.assertEqual((failure.reason, order), ("no_recipe", None))
        self.assertEqual(s.resources.get("raw_a"), 100)

    def test_gain_chance_success_grants_the_resource(self):
        s = GameState(resources={"raw_a": 10})
        with mock.patch.object(engine.random, "random", return_value=0.0):  # 0.0 < 0.5 -> успех
            result = engine.do_manual_action(s, "craft_lucky")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources.get("lucky_output", 0), 4)
        self.assertEqual(s.resources["raw_a"], 8)  # cost без chance - списан как обычно

    def test_gain_chance_failure_withholds_the_resource_but_still_charges_cost(self):
        s = GameState(resources={"raw_a": 10})
        with mock.patch.object(engine.random, "random", return_value=0.99):  # 0.99 >= 0.5 -> провал
            result = engine.do_manual_action(s, "craft_lucky")
        self.assertTrue(result.ok, result.message)  # действие "выполнено", просто без выхода
        self.assertEqual(s.resources.get("lucky_output", 0), 0)
        self.assertEqual(s.resources["raw_a"], 8)  # cost всё равно списан

    def test_cost_chance_success_consumes_the_ingredient(self):
        s = GameState(resources={"raw_a": 100})
        with mock.patch.object(engine.random, "random", return_value=0.0):  # 0.0 < 0.1 -> потрачено
            result = engine.do_manual_action(s, "craft_risky")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 90)
        self.assertEqual(s.resources.get("risky_output", 0), 1)  # gain без chance - выдан как обычно

    def test_cost_chance_failure_grants_the_output_for_free(self):
        s = GameState(resources={"raw_a": 100})
        with mock.patch.object(engine.random, "random", return_value=0.99):  # 0.99 >= 0.1 -> не потрачено
            result = engine.do_manual_action(s, "craft_risky")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 100)  # ничего не списано
        self.assertEqual(s.resources.get("risky_output", 0), 1)  # но выход всё равно получен

    def test_action_without_chance_fields_never_rolls_dice(self):
        # Действия без cost_chance/gain_chance не должны вызывать random.random()
        # вообще - поведение и random-стрим существующего контента не меняются.
        s = GameState(resources={"raw_a": 10})
        with mock.patch.object(engine.random, "random") as random_mock:
            result = engine.do_manual_action(s, "gather_a")
        self.assertTrue(result.ok)
        random_mock.assert_not_called()


class RandomProduceTests(FixtureTestCase):
    """Случайное количество produce ({"min","max"} вместо плоского числа, см.
    game/engine.py:_is_range_amount/_roll_range_amounts) - НЕЗАВИСИМО от
    cost_chance/gain_chance, равномерное распределение. Фикстура:
    MANUAL_ACTIONS["craft_variable"] (gain.variable_output = {"min":2,"max":6})
    и BUILDINGS["variable_gen"] (produces.variable_output = {"min":1,"max":4},
    апгрейд "vg_scale" типа scale x2)."""

    def test_action_with_range_gain_is_not_deterministic(self):
        self.assertFalse(engine.is_manual_action_deterministic("craft_variable"))

    def test_resource_recipe_excludes_actions_with_range_gain(self):
        # craft_variable имеет непустой cost (формально годится в кандидаты), но
        # gain.variable_output - диапазон, а не гарантированное число - должен
        # быть исключён из resource_recipe целиком, симметрично craft_lucky/
        # craft_risky (шанс) выше.
        s = GameState()
        recipe = engine.resource_recipe(s)
        self.assertNotIn("variable_output", recipe)

    def test_manual_action_rolls_within_bounds_and_calls_randint_with_range(self):
        s = GameState(resources={"raw_a": 10})
        with mock.patch.object(engine.random, "randint", return_value=4) as randint_mock:
            result = engine.do_manual_action(s, "craft_variable")
        self.assertTrue(result.ok, result.message)
        randint_mock.assert_called_once_with(2, 6)
        self.assertEqual(s.resources.get("variable_output", 0), 4)

    def test_manual_action_never_rolls_random_random_for_a_pure_range_action(self):
        # craft_variable не имеет cost_chance/gain_chance - только диапазон, так
        # что random.random() (механика шанса) вообще не должна вызываться.
        s = GameState(resources={"raw_a": 10})
        with mock.patch.object(engine.random, "random") as random_mock, \
                mock.patch.object(engine.random, "randint", return_value=3):
            engine.do_manual_action(s, "craft_variable")
        random_mock.assert_not_called()

    def test_building_produces_rolls_once_per_turn_and_multiplies_by_working_copies(self):
        s = GameState(resources={"raw_a": 100}, buildings={"variable_gen": 2})
        with mock.patch.object(engine.random, "randint", return_value=3) as randint_mock:
            engine.advance_turn(s)
        randint_mock.assert_called_once_with(1, 4)
        self.assertEqual(s.resources.get("variable_output", 0), 6)  # 3 * 2 рабочих копии

    def test_scale_upgrade_stretches_both_range_bounds(self):
        s = GameState(resources={"raw_a": 100}, buildings={"variable_gen": 1})
        engine.purchase_building_upgrade(s, "variable_gen", "vg_scale")
        produces, _ = engine.effective_building_stats("variable_gen", s.techs, s.building_upgrades)
        self.assertEqual(produces["variable_output"], {"min": 2, "max": 8})

    def test_energy_recipe_produces_range_rolls_within_bounds(self):
        s = GameState(
            resources={"energy": 100},
            buildings={"mega_converter": 1},
            energy_jobs=[{"recipe_id": "make_variable", "progress": 0, "building_id": "mega_converter"}],
        )
        with mock.patch.object(engine.random, "randint", return_value=5) as randint_mock:
            engine.advance_turn(s)
        randint_mock.assert_called_once_with(3, 7)
        self.assertEqual(s.resources.get("variable_output", 0), 5)

    def test_soul_recipe_produces_range_rolls_independently_per_slot(self):
        # capacity = base slots(1) * working copies - 2 копии postройки, чтобы
        # обе вставленные души поместились в capacity без апгрейда слотов.
        s = GameState(
            resources={},
            buildings={"soul_synth": 2},
            soul_slots={"soul_synth": ["variable_soul", "variable_soul"]},
        )
        with mock.patch.object(engine.random, "randint", side_effect=[2, 5]) as randint_mock:
            engine.advance_turn(s)
        self.assertEqual(randint_mock.call_count, 2)
        self.assertEqual(s.resources.get("variable_output", 0), 7)  # 2 + 5, независимые броски


def _blank_mine_field(size: int, overrides: dict[tuple[int, int], dict] | None = None) -> dict:
    cells = [
        {"resource": None, "hp": 1, "hp_max": 1, "broken": False, "revealed": False}
        for _ in range(size * size)
    ]
    for (r, c), patch in (overrides or {}).items():
        cells[r * size + c].update(patch)
    return {"size": size, "cells": cells}


class MiningFieldGenerationTests(FixtureTestCase):
    def test_generated_field_has_broken_and_revealed_center(self):
        s = GameState()
        field = engine._generate_mine_field(s)
        size = field["size"]
        center = size // 2
        center_cell = field["cells"][engine._mine_field_idx(size, center, center)]
        self.assertTrue(center_cell["broken"])
        self.assertTrue(center_cell["revealed"])
        neighbor = field["cells"][engine._mine_field_idx(size, center, center - 1)]
        self.assertTrue(neighbor["revealed"])
        self.assertFalse(neighbor["broken"])

    def test_ore_spawns_regardless_of_tool_tier(self):
        # rare_mineral требует тир 2 - гейт спавна на поле только по requires_tech
        # (тут None), тир инструмента решает лишь ломаемость, а не появление.
        # Состав поля теперь лотерея (см. _roll_field_cluster_counts) - не
        # гарантия "каждый исследованный вид хотя бы раз", поэтому здесь
        # фиксируем розыгрыш мокой, чтобы проверить именно независимость от
        # тира инструмента, а не удачу.
        s = GameState(tool_tiers={"pickaxe": 0})
        with mock.patch.object(engine.random, "choices", return_value=["mineral", "rare_mineral", "mineral", "mineral"]):
            field = engine._generate_mine_field(s)
        self.assertTrue(any(cell["resource"] == "rare_mineral" for cell in field["cells"]))

    def test_ore_requiring_unresearched_tech_does_not_spawn(self):
        patched = {**engine.MINING_INFO["mineral"], "requires_tech": "tech1"}
        s = GameState()  # tech1 не исследована
        with mock.patch.dict(engine.MINING_INFO, {"mineral": patched}):
            field = engine._generate_mine_field(s)
        self.assertFalse(any(cell["resource"] == "mineral" for cell in field["cells"]))
        self.assertTrue(any(cell["resource"] == "rare_mineral" for cell in field["cells"]))

    def test_new_mine_field_regenerates_and_discards_progress(self):
        s = GameState()
        old_field = engine._generate_mine_field(s)
        s.active_mine_field = old_field
        result = engine.new_mine_field(s)
        self.assertTrue(result.ok)
        self.assertIsNot(s.active_mine_field, old_field)

    def test_cluster_lottery_respects_budget_and_weights(self):
        # Состав поля - взвешенная лотерея на cluster_budget слотов (см.
        # _roll_field_cluster_counts), НЕ гарантия "каждому исследованному
        # виду хотя бы 1 кластер" - проверяем и сам вызов (population/k), и
        # что подсчёт повторов работает как ожидается.
        s = GameState()
        with mock.patch.object(engine.random, "choices") as choices_mock:
            choices_mock.return_value = ["mineral", "mineral", "rare_mineral", "mineral"]
            counts = engine._roll_field_cluster_counts(s)
        args, kwargs = choices_mock.call_args
        self.assertEqual(set(args[0]), {"mineral", "rare_mineral"})
        self.assertEqual(kwargs["k"], engine.MINE_FIELD["cluster_budget"])
        self.assertEqual(counts, {"mineral": 3, "rare_mineral": 1})

    def test_rock_cells_get_icon_variant_within_pool_range(self):
        # rock_icon_variants - чисто косметический пул спрайтов породы (см.
        # CLAUDE.md: "разнообразие обычной породы - только визуально"), тут
        # проверяем только что индекс не выходит за пул [rock_icon, *variants]
        # и что реально используются разные варианты, а не всегда 0.
        variants = [{"grid": ["."], "palette": {}}, {"grid": ["."], "palette": {}}]
        patched = {**engine.MINE_FIELD, "rock_icon_variants": variants}
        s = GameState()
        with mock.patch.dict(engine.MINE_FIELD, patched, clear=True):
            field = engine._generate_mine_field(s)
        rock_cells = [c for c in field["cells"] if c["resource"] is None]
        self.assertTrue(rock_cells)
        self.assertTrue(all(0 <= c["icon_variant"] < 3 for c in rock_cells))
        self.assertTrue(any(c["icon_variant"] != 0 for c in rock_cells))

    def test_mine_field_view_exposes_rock_icon_pool_and_cell_variant(self):
        s = GameState()
        view = engine.mine_field_view(s)
        self.assertEqual(view["rock_icons"][0], engine.MINE_FIELD.get("rock_icon"))
        rock_cell_view = next(
            cell for cell in view["cells"]
            if cell.get("revealed") and not cell.get("broken") and "resource" not in cell
        )
        self.assertIn("icon_variant", rock_cell_view)


class MineFieldScannerTests(FixtureTestCase):
    """Щуп-детектор (mine_field.scanner_tool) - чисто информационный, пороги
    тира (fixtures.py: ore_scanner, 6 тиров: types=2, counts=3, radius=4/5,
    reveal_all=6). Тир 1 - дефолтный для всех (см. CLAUDE.md: тир 1 = "не
    откалиброван"), намеренно ничего не даёт."""

    def test_scanner_tier1_reveals_nothing(self):
        s = GameState(tool_tiers={"ore_scanner": 1})
        s.active_mine_field = _blank_mine_field(4, {(0, 0): {"resource": "mineral"}})
        view = engine.mine_field_view(s)
        mineral_entry = next(c for c in view["ore_catalog"] if c["id"] == "mineral")
        self.assertNotIn("present_on_field", mineral_entry)
        self.assertNotIn("field_count", mineral_entry)

    def test_scanner_tier2_reveals_types_without_counts(self):
        s = GameState(tool_tiers={"ore_scanner": 2})
        s.active_mine_field = _blank_mine_field(4, {
            (0, 0): {"resource": "mineral"}, (0, 1): {"resource": "mineral"},
        })
        view = engine.mine_field_view(s)
        mineral_entry = next(c for c in view["ore_catalog"] if c["id"] == "mineral")
        rare_entry = next(c for c in view["ore_catalog"] if c["id"] == "rare_mineral")
        self.assertTrue(mineral_entry["present_on_field"])
        self.assertFalse(rare_entry["present_on_field"])
        self.assertNotIn("field_count", mineral_entry)

    def test_scanner_tier3_reveals_field_counts(self):
        s = GameState(tool_tiers={"ore_scanner": 3})
        s.active_mine_field = _blank_mine_field(4, {
            (0, 0): {"resource": "mineral"}, (0, 1): {"resource": "mineral"}, (1, 0): {"resource": "mineral"},
        })
        view = engine.mine_field_view(s)
        mineral_entry = next(c for c in view["ore_catalog"] if c["id"] == "mineral")
        self.assertEqual(mineral_entry["field_count"], 3)

    def test_scanner_does_not_count_plain_rock(self):
        # по прямому пожеланию, 2026-09-16: подсказки о количестве - только
        # по РУДЕ (mining_info), обычный камень (rock_resource) щуп не считает
        # и не подсвечивает - структурно это уже гарантировано тем, что
        # каталог собирается по MINING_INFO, а не по всем ресурсам поля.
        s = GameState(tool_tiers={"ore_scanner": 3})
        s.active_mine_field = _blank_mine_field(4)  # везде resource=None (порода)
        view = engine.mine_field_view(s)
        for entry in view["ore_catalog"]:
            self.assertEqual(entry["field_count"], 0)

    def test_scanner_tier6_senses_ore_through_fog_anywhere(self):
        s = GameState(tool_tiers={"ore_scanner": 6})
        s.active_mine_field = _blank_mine_field(6, {(5, 5): {"resource": "mineral"}})
        view = engine.mine_field_view(s)
        sensed = view["cells"][engine._mine_field_idx(6, 5, 5)]
        self.assertFalse(sensed["revealed"])
        self.assertEqual(sensed["sensed_resource"], "mineral")

    def test_scanner_below_radius_tier_does_not_sense_fog(self):
        s = GameState(tool_tiers={"ore_scanner": 3})
        s.active_mine_field = _blank_mine_field(6, {
            (0, 0): {"revealed": True}, (0, 1): {"resource": "mineral"},
        })
        view = engine.mine_field_view(s)
        sensed = view["cells"][engine._mine_field_idx(6, 0, 1)]
        self.assertNotIn("sensed_resource", sensed)

    def test_scanner_tier4_senses_ore_within_radius_only(self):
        # тир 4 фикстуры - radius 1 (см. fixtures.py: MINE_FIELD.scanner_radius_tiers)
        s = GameState(tool_tiers={"ore_scanner": 4})
        s.active_mine_field = _blank_mine_field(6, {
            (0, 0): {"revealed": True},
            (0, 1): {"resource": "mineral"},  # Чебышёв-расстояние 1 - в радиусе
            (5, 5): {"resource": "mineral"},  # далеко за радиусом
        })
        view = engine.mine_field_view(s)
        near = view["cells"][engine._mine_field_idx(6, 0, 1)]
        far = view["cells"][engine._mine_field_idx(6, 5, 5)]
        self.assertEqual(near["sensed_resource"], "mineral")
        self.assertNotIn("sensed_resource", far)

    def test_scanner_tier5_senses_wider_radius(self):
        # тир 5 фикстуры - radius 2, сильнее тира 4 (радиусы не складываются,
        # действует только самый большой разблокированный)
        s = GameState(tool_tiers={"ore_scanner": 5})
        s.active_mine_field = _blank_mine_field(6, {
            (0, 0): {"revealed": True},
            (0, 2): {"resource": "mineral"},  # расстояние ровно 2 - на границе радиуса
        })
        view = engine.mine_field_view(s)
        sensed = view["cells"][engine._mine_field_idx(6, 0, 2)]
        self.assertEqual(sensed["sensed_resource"], "mineral")

    def test_scanner_radius_measured_from_any_revealed_cell_not_just_ore(self):
        # источник радиуса - ЛЮБАЯ уже открытая клетка (в т.ч. пустая порода),
        # а не только клетки руды
        s = GameState(tool_tiers={"ore_scanner": 4})
        s.active_mine_field = _blank_mine_field(6, {
            (2, 2): {"revealed": True, "broken": True},  # пустая порода, уже пройдена
            (2, 3): {"resource": "mineral"},
        })
        view = engine.mine_field_view(s)
        sensed = view["cells"][engine._mine_field_idx(6, 2, 3)]
        self.assertEqual(sensed["sensed_resource"], "mineral")


class MiningFieldHitTests(FixtureTestCase):
    def test_hit_unreachable_cell_fails(self):
        s = GameState()
        s.active_mine_field = _blank_mine_field(5)
        result = engine.hit_mine_field(s, 2, 2)
        self.assertFalse(result.ok)

    def test_orthogonal_neighbor_of_broken_cell_is_breakable(self):
        s = GameState()  # тир 1: area=1, power=3
        s.active_mine_field = _blank_mine_field(5, {(2, 2): {"broken": True, "revealed": True}})
        result = engine.hit_mine_field(s, 2, 3)
        self.assertTrue(result.ok)
        cell = s.active_mine_field["cells"][engine._mine_field_idx(5, 2, 3)]
        self.assertTrue(cell["broken"])
        self.assertEqual(s.resources.get("raw_a"), 2)  # порода -> MINE_FIELD.rock_resource/rock_yield

    def test_diagonal_neighbor_of_broken_cell_is_not_breakable(self):
        s = GameState()
        s.active_mine_field = _blank_mine_field(5, {(2, 2): {"broken": True, "revealed": True}})
        result = engine.hit_mine_field(s, 3, 3)
        self.assertFalse(result.ok)
        cell = s.active_mine_field["cells"][engine._mine_field_idx(5, 3, 3)]
        self.assertFalse(cell["broken"])

    def test_area_swing_can_reach_into_fog_if_block_edge_touches_broken(self):
        # тир 3: area=2 - блок 2x2, кликнутый рядом с открытой клеткой, пробивает
        # ЦЕЛИКОМ, включая дальний угол, который сам по себе не граничил бы с ней.
        s = GameState(tool_tiers={"pickaxe": 3})
        s.active_mine_field = _blank_mine_field(6, {(2, 2): {"broken": True, "revealed": True}})
        result = engine.hit_mine_field(s, 3, 2)
        self.assertTrue(result.ok)
        far_corner = s.active_mine_field["cells"][engine._mine_field_idx(6, 4, 3)]
        self.assertTrue(far_corner["broken"])
        self.assertTrue(far_corner["revealed"])

    def test_locked_ore_in_swing_is_revealed_not_broken_but_does_not_block_the_rest(self):
        patched = {**engine.MINING_INFO["rare_mineral"], "required_tier": 4}
        s = GameState(tool_tiers={"pickaxe": 3})  # area=2, power=7, ниже патченного required_tier
        s.active_mine_field = _blank_mine_field(4, {
            (1, 1): {"broken": True, "revealed": True},
            (1, 2): {"resource": "rare_mineral", "hp": 6, "hp_max": 6},
        })
        with mock.patch.dict(engine.MINING_INFO, {"rare_mineral": patched}):
            result = engine.hit_mine_field(s, 1, 1)
        self.assertTrue(result.ok)
        locked_cell = s.active_mine_field["cells"][engine._mine_field_idx(4, 1, 2)]
        self.assertFalse(locked_cell["broken"])
        self.assertTrue(locked_cell["revealed"])
        rock_cell = s.active_mine_field["cells"][engine._mine_field_idx(4, 2, 1)]
        self.assertTrue(rock_cell["broken"])

    def test_toughness_requires_multiple_hits(self):
        s = GameState(tool_tiers={"pickaxe": 2})  # area=1, power=5; тир 2 открывает rare_mineral (required_tier=2)
        s.active_mine_field = _blank_mine_field(3, {
            (1, 0): {"broken": True, "revealed": True},
            (1, 1): {"resource": "rare_mineral", "hp": 6, "hp_max": 6},
        })
        engine.hit_mine_field(s, 1, 1)
        cell = s.active_mine_field["cells"][engine._mine_field_idx(3, 1, 1)]
        self.assertFalse(cell["broken"])
        self.assertEqual(cell["hp"], 1)
        engine.hit_mine_field(s, 1, 1)
        self.assertTrue(cell["broken"])

    def test_yield_is_flat_base_yield_regardless_of_tool_tier(self):
        # По прямому пожеланию, 2026-09-16: "1 клетка = 1 порция руды", тир
        # кирки решает ТОЛЬКО площадь/урон удара и порог доступности клетки
        # (required_tier), выход с клетки больше НЕ домножается на тир.
        for tier in (1, 3):
            with self.subTest(tier=tier):
                s = GameState(tool_tiers={"pickaxe": tier})
                s.active_mine_field = _blank_mine_field(3, {
                    (1, 0): {"broken": True, "revealed": True},
                    (1, 1): {"resource": "mineral", "hp": 2, "hp_max": 2},
                })
                result = engine.hit_mine_field(s, 1, 1)
                self.assertTrue(result.ok)
                self.assertEqual(s.resources.get("mineral"), 4)

    def test_field_fully_cleared_triggers_automatic_regeneration(self):
        # Всё поле, кроме одной клетки породы, уже выбито - добиваем её.
        s = GameState()
        s.active_mine_field = _blank_mine_field(3, {
            (0, 0): {"broken": True, "revealed": True}, (0, 1): {"broken": True, "revealed": True},
            (0, 2): {"broken": True, "revealed": True}, (1, 0): {"broken": True, "revealed": True},
            (1, 1): {"broken": True, "revealed": True}, (1, 2): {"broken": True, "revealed": True},
            (2, 0): {"broken": True, "revealed": True}, (2, 1): {"broken": True, "revealed": True},
        })
        result = engine.hit_mine_field(s, 2, 2)
        self.assertTrue(result.ok)
        self.assertIn("заложен новый", result.message)
        # Фикстурное MINE_FIELD.size = 6 (см. tests/fixtures.py), а ручное
        # тестовое поле - 3: смена размера доказывает, что поле реально
        # перегенерировано, а не просто дописано сообщение.
        self.assertEqual(s.active_mine_field["size"], engine.mine_field_size())
        self.assertNotEqual(s.active_mine_field["size"], 3)

    def test_locked_ore_cell_prevents_automatic_regeneration(self):
        # Последняя небитая клетка поля - руда не по тиру кирки: она не может
        # сломаться сама по себе (см. hit_mine_field), значит поле НЕ считается
        # полностью выкопанным и автообновление не срабатывает.
        patched = {**engine.MINING_INFO["rare_mineral"], "required_tier": 4}
        s = GameState()  # тир 1 кирки - ниже патченного required_tier
        s.active_mine_field = _blank_mine_field(3, {
            (0, 0): {"broken": True, "revealed": True}, (0, 1): {"broken": True, "revealed": True},
            (0, 2): {"broken": True, "revealed": True}, (1, 0): {"broken": True, "revealed": True},
            (1, 1): {"broken": True, "revealed": True}, (1, 2): {"broken": True, "revealed": True},
            (2, 0): {"broken": True, "revealed": True},
            (2, 2): {"resource": "rare_mineral", "hp": 6, "hp_max": 6},
        })
        old_field = s.active_mine_field
        with mock.patch.dict(engine.MINING_INFO, {"rare_mineral": patched}):
            result = engine.hit_mine_field(s, 2, 1)
        self.assertTrue(result.ok)
        self.assertIs(s.active_mine_field, old_field)
        self.assertNotIn("заложен", result.message)


class ToolsAndPinTests(FixtureTestCase):
    def test_upgrade_pickaxe_progression(self):
        s = GameState(resources={"raw_a": 100})
        self.assertEqual(engine.tool_tier(s, "pickaxe"), 1)
        result = engine.upgrade_tool(s, "pickaxe")
        self.assertTrue(result.ok)
        self.assertEqual(engine.tool_tier(s, "pickaxe"), 2)

    def test_upgrade_pickaxe_blocked_without_tech(self):
        s = GameState(resources={"raw_b": 100}, tool_tiers={"pickaxe": 2})  # тир3 требует tech1
        result = engine.upgrade_tool(s, "pickaxe")
        self.assertFalse(result.ok)

    def test_upgrade_tool_blocked_at_max_tier(self):
        s = GameState(tool_tiers={"drill": 2})  # у drill в фикстуре ровно 2 тира
        result = engine.upgrade_tool(s, "drill")
        self.assertFalse(result.ok)

    def test_tools_are_independent_of_each_other(self):
        # улучшение одного инструмента не должно задевать тир другого - до этого
        # был единственный state.pickaxe_tier на всю игру, теперь у каждого свой
        # ключ в state.tool_tiers
        s = GameState(resources={"raw_a": 100})
        engine.upgrade_tool(s, "pickaxe")
        self.assertEqual(engine.tool_tier(s, "pickaxe"), 2)
        self.assertEqual(engine.tool_tier(s, "drill"), 1)

    def test_upgradable_tools_excludes_tech_locked_and_maxed_tools(self):
        # sword/armor тир 2 не требуют технологии - они всегда в списке (не
        # завязаны на tech1), в отличие от pickaxe/drill, ради которых написан
        # этот тест. wrench тир 2 тоже не требует технологии - он всегда в
        # списке, по той же причине (см. TOOLS в fixtures.py).
        # ore_scanner тоже сразу на максимум - иначе он тоже маячил бы в списке
        # (в фикстуре все его тиры без requires_tech, см. TOOLS в fixtures.py),
        # а тест не про него.
        s = GameState(tool_tiers={"pickaxe": 2, "drill": 2, "ore_scanner": 6})  # pickaxe тир3 нужен tech1, drill уже макс
        self.assertEqual(engine.upgradable_tools(s), ["wrench", "sword", "armor"])
        s.techs = {"tech1"}
        self.assertEqual(engine.upgradable_tools(s), ["pickaxe", "wrench", "sword", "armor"])

    def test_requires_tool_tiers_gates_by_exact_membership_not_threshold(self):
        # ключевая проверка новой механики: два действия, одинаковые по смыслу, но
        # каждое доступно ТОЛЬКО на своём тире инструмента (галочки, не ">=") -
        # ровно сценарий, ради которого сделаны requires_tool_tiers
        s = GameState()
        self.assertTrue(engine.is_manual_action_unlocked("drill_tier1_only", s))
        self.assertFalse(engine.is_manual_action_unlocked("drill_tier2_only", s))
        s.tool_tiers["drill"] = 2
        self.assertFalse(engine.is_manual_action_unlocked("drill_tier1_only", s))
        self.assertTrue(engine.is_manual_action_unlocked("drill_tier2_only", s))

    def test_requires_tool_tiers_ignores_tools_not_mentioned(self):
        # действие без requires_tool_tiers вообще не должно зависеть от тиров
        # никаких инструментов - на всякий случай, чтобы регенерика не сломала
        # обычные действия
        s = GameState(tool_tiers={"pickaxe": 1, "drill": 1})
        self.assertTrue(engine.is_manual_action_unlocked("gather_a", s))

    def test_toggle_pin_respects_max_and_excludes_energy(self):
        s = GameState(pinned=[])
        self.assertFalse(engine.toggle_pin(s, "energy").ok)
        non_energy = [r for r in fixtures.RESOURCE_ORDER if r != "energy"]
        # Тест имеет смысл, только если ресурсов больше лимита - иначе он ни разу не
        # проверяет, что лимит вообще где-то срабатывает (было бы совпадение "10 из 10").
        self.assertGreater(len(non_energy), engine.MAX_PINNED)
        results = [engine.toggle_pin(s, rid) for rid in non_energy]
        self.assertEqual(len(s.pinned), engine.MAX_PINNED)
        self.assertEqual(sum(1 for r in results if not r.ok), len(non_energy) - engine.MAX_PINNED)

    def test_toggle_pin_rejects_unknown_resource_without_crashing(self):
        # /api/pin (tools/web_ui.py) передаёт сюда resource_id прямо из тела HTTP-запроса -
        # мусорный id не должен валиться KeyError'ом на RESOURCE_NAMES[resource_id]
        s = GameState(pinned=[])
        result = engine.toggle_pin(s, "no_such_resource")
        self.assertFalse(result.ok)
        self.assertEqual(s.pinned, [])
        self.assertFalse(engine.toggle_pin(s, None).ok)

    def test_energy_breakdown_sums_only_working_buildings(self):
        s = GameState(buildings={"gen": 2}, broken={"gen": 1})
        stock, gen, cons = engine.energy_breakdown(s)
        self.assertEqual(gen, 2)  # только одна рабочая копия x2 энергии


class QuestTests(FixtureTestCase):
    def test_quest_completes_and_does_not_retrigger(self):
        s = GameState(buildings={"gen": 1})
        newly = engine.check_quests(s)
        self.assertIn("test_quest", newly)
        self.assertIn("test_quest", s.completed_quests)
        self.assertEqual(s.completed_quests["test_quest"], s.turn)
        second = engine.check_quests(s)
        self.assertNotIn("test_quest", second)

    def test_specific_building_quest(self):
        s = GameState(buildings={"gen": 5})  # много, но не фабрика
        newly = engine.check_quests(s)
        self.assertNotIn("quest_factory", newly)
        s.buildings["factory"] = 1
        newly2 = engine.check_quests(s)
        self.assertIn("quest_factory", newly2)

    def test_is_quest_available_respects_requires(self):
        # fixtures.QUESTS["quest_factory"].requires == ["test_quest"] - недоступен,
        # пока test_quest не выполнен (см. is_quest_available), даже если СВОИ
        # условия quest_factory станут истинны позже. buildings={"gen": 1} УЖЕ
        # удовлетворяет test_quest ("постройка любого типа"), но НЕ quest_factory
        # (нужна именно "factory") - так первый check_quests не завершит оба сразу
        # каскадом в одном проходе, и можно проверить "доступен, но не выполнен".
        s = GameState(buildings={"gen": 1})
        self.assertFalse(engine.is_quest_available("quest_factory", s))
        engine.check_quests(s)
        self.assertIn("test_quest", s.completed_quests)
        self.assertNotIn("quest_factory", s.completed_quests)
        self.assertTrue(engine.is_quest_available("quest_factory", s))

        s.buildings["factory"] = 1
        newly2 = engine.check_quests(s)
        self.assertIn("quest_factory", newly2)

    def test_is_quest_available_false_once_quest_completed(self):
        s = GameState(buildings={"gen": 1})
        self.assertTrue(engine.is_quest_available("test_quest", s))
        engine.check_quests(s)
        self.assertFalse(engine.is_quest_available("test_quest", s))

    def test_all_techs_researched_quest(self):
        s = GameState(techs=set(fixtures.TECHS.keys()))
        newly = engine.check_quests(s)
        self.assertIn("quest_all_done", newly)

    def test_turn_and_pickaxe_quests(self):
        s = GameState(turn=100, tool_tiers={"pickaxe": len(fixtures.TOOLS["pickaxe"]["tiers"])})
        newly = engine.check_quests(s)
        self.assertIn("quest_turn100", newly)
        self.assertIn("quest_maxpick", newly)

    def test_quest_stays_completed_even_if_condition_later_false(self):
        # Раз условия истинны, квест остаётся выполненным навсегда - повторно не
        # переоценивается (см. docstring check_quests).
        s = GameState(buildings={"gen": 1})
        engine.check_quests(s)
        s.buildings = {}
        self.assertIn("test_quest", s.completed_quests)

    def test_claim_quest_reward_rejects_before_completion(self):
        s = GameState()
        result = engine.claim_quest_reward(s, "test_quest")
        self.assertFalse(result.ok)
        self.assertNotIn("test_quest", s.claimed_quests)

    def test_claim_quest_reward_rolls_a_single_prize_and_marks_claimed(self):
        # fixtures.LOOT_CASES["test_case"] - барабан из raw_a x5 (вес 3) и raw_b x2
        # (вес 1), суммарный вес 4 - см. game/engine.py:_roll_loot_case_prize
        # (random.uniform(0, total_weight)). uniform=2.5 <= 3 (вес raw_a) -> raw_a.
        s = GameState(buildings={"gen": 1})
        engine.check_quests(s)
        with mock.patch.object(engine.random, "uniform", return_value=2.5):
            result = engine.claim_quest_reward(s, "test_quest")
        self.assertTrue(result.ok)
        self.assertIn("test_quest", s.claimed_quests)
        self.assertEqual(result.prize, {"resource": "raw_a", "amount": 5})
        self.assertEqual(s.resources.get("raw_a"), 5)
        self.assertNotIn("raw_b", s.resources)  # выпал РОВНО один приз, не оба сразу

    def test_claim_quest_reward_can_roll_the_other_prize(self):
        # uniform=3.5 > 3 (вес raw_a) -> попадает во второй приз (raw_b).
        s = GameState(buildings={"gen": 1})
        engine.check_quests(s)
        with mock.patch.object(engine.random, "uniform", return_value=3.5):
            result = engine.claim_quest_reward(s, "test_quest")
        self.assertEqual(result.prize, {"resource": "raw_b", "amount": 2})
        self.assertEqual(s.resources.get("raw_b"), 2)

    def test_claim_quest_reward_rejects_double_claim(self):
        s = GameState(buildings={"gen": 1})
        engine.check_quests(s)
        engine.claim_quest_reward(s, "test_quest")
        second = engine.claim_quest_reward(s, "test_quest")
        self.assertFalse(second.ok)

    def test_claim_quest_reward_rejects_unknown_quest(self):
        s = GameState()
        result = engine.claim_quest_reward(s, "no_such_quest")
        self.assertFalse(result.ok)

    def test_manual_action_performed_at_least_quest(self):
        # fixtures.MANUAL_ACTIONS["gather_a"] - мгновенное детерминированное действие
        # (cost {}, gain {raw_a: 3}) - каждый успешный клик должен растить
        # state.action_performed_count["gather_a"], даже если игрок потом потратит
        # весь raw_a (в отличие от resource_at_least, условие не может "отмотаться назад").
        s = GameState()
        quest = {
            "name": "Собиратель", "description": "Добудь A трижды.",
            "tier": 1, "conditions": [{"type": "manual_action_performed_at_least", "action": "gather_a", "amount": 2}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"gatherer": quest}):
            self.assertNotIn("gatherer", engine.check_quests(s))
            engine.do_manual_action(s, "gather_a")
            self.assertNotIn("gatherer", engine.check_quests(s))
            engine.do_manual_action(s, "gather_a")
            s.resources["raw_a"] = 0  # потрачен - condition всё равно должно сработать
            self.assertIn("gatherer", engine.check_quests(s))

    def test_building_upgrade_purchased_quest(self):
        # fixtures.BUILDINGS["flaky_typed"]["upgrades"][0] == "flaky_typed_immune"
        # (cost {raw_a: 5}, requires_tech None) - см. building_upgrade_purchased.
        s = GameState(buildings={"flaky_typed": 1}, resources={"raw_a": 5})
        quest = {
            "name": "Модернизатор", "description": "Купи улучшение постройки.",
            "tier": 1,
            "conditions": [{"type": "building_upgrade_purchased", "building": "flaky_typed", "upgrade": "flaky_typed_immune"}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"upgrader": quest}):
            self.assertNotIn("upgrader", engine.check_quests(s))
            engine.purchase_building_upgrade(s, "flaky_typed", "flaky_typed_immune")
            self.assertIn("upgrader", engine.check_quests(s))

    def test_storage_tier_at_least_quest(self):
        s = GameState(resources={"raw_a": 100})
        quest = {
            "name": "Складовщик", "description": "Расширь склад дважды.",
            "tier": 1,
            "conditions": [{"type": "storage_tier_at_least", "resource": "limited_res", "amount": 2}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"storage_quest": quest}):
            self.assertNotIn("storage_quest", engine.check_quests(s))
            engine.upgrade_storage(s, "limited_res")
            self.assertNotIn("storage_quest", engine.check_quests(s))  # тир 1 < 2
            s.techs.add("tech1")
            engine.upgrade_storage(s, "limited_res")
            self.assertIn("storage_quest", engine.check_quests(s))

    def test_building_upgrade_level_at_least_quest(self):
        # fixtures.BUILDINGS["apiary"]["upgrades"][0] == "apiary_slot_bonus" -
        # repeatable, покупается многократно (game/engine.py:building_upgrade_purchase_count).
        s = GameState(buildings={"apiary": 1}, resources={"raw_a": 100})
        quest = {
            "name": "Пчеловод", "description": "Купи доп. слот пасеки дважды.",
            "tier": 1,
            "conditions": [{"type": "building_upgrade_level_at_least", "building": "apiary", "upgrade": "apiary_slot_bonus", "amount": 2}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"level_quest": quest}):
            engine.purchase_building_upgrade(s, "apiary", "apiary_slot_bonus")
            self.assertNotIn("level_quest", engine.check_quests(s))  # уровень 1 < 2
            engine.purchase_building_upgrade(s, "apiary", "apiary_slot_bonus")
            self.assertIn("level_quest", engine.check_quests(s))

    def test_building_upgrade_type_purchased_any_quest(self):
        # fixtures.BUILDINGS["matter_generator"]["upgrades"][1] == "matter_auto"
        # (type "autocraft") - условие не привязано к конкретной постройке, сработает
        # от покупки этого типа улучшения у ЛЮБОЙ постройки в игре.
        s = GameState(buildings={"matter_generator": 1}, resources={"raw_a": 100})
        quest = {
            "name": "Автоматизатор", "description": "Купи автоматизацию хоть где-нибудь.",
            "tier": 1,
            "conditions": [{"type": "building_upgrade_type_purchased_any", "upgrade_type": "autocraft"}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"auto_quest": quest}):
            self.assertFalse(engine.building_upgrade_type_purchased_anywhere(s, "autocraft"))
            self.assertNotIn("auto_quest", engine.check_quests(s))
            engine.purchase_building_upgrade(s, "matter_generator", "matter_auto")
            self.assertTrue(engine.building_upgrade_type_purchased_anywhere(s, "autocraft"))
            self.assertIn("auto_quest", engine.check_quests(s))


class HistoryAndStatsTests(FixtureTestCase):
    def test_record_history_entry_shape(self):
        s = GameState(resources={"raw_a": 10}, buildings={"gen": 1})
        engine.advance_turn(s)  # turn: 1 -> 2
        report = engine.ProductionReport()
        engine.record_history_entry(s, "manual", "gather_a", "Выполнено.", report, [])
        # первый вызов на свежей игре добавляет ещё и запись-точку отсчёта (ход 0)
        self.assertEqual(len(s.history), 2)
        self.assertEqual(s.history[0]["turn"], 0)
        entry = s.history[-1]
        self.assertEqual(entry["turn"], 1)
        self.assertEqual(entry["resources"], s.resources)
        self.assertEqual(entry["new_techs"], [])

    def test_record_history_entry_tags_build_and_tech(self):
        s = GameState()
        engine.advance_turn(s)
        report = engine.ProductionReport()
        engine.record_history_entry(s, "build", "gen", "Построено.", report, [])
        self.assertEqual(s.history[-1]["new_buildings"], {"gen": 1})

        engine.advance_turn(s)
        engine.record_history_entry(s, "tech", "tech1", "Изучено.", report, ["some_quest"])
        self.assertEqual(s.history[-1]["new_techs"], ["tech1"])
        self.assertEqual(s.history[-1]["quests_completed"], ["some_quest"])

    def test_compute_stats_over_range(self):
        s = GameState()
        report = engine.ProductionReport()
        # ход 1: +3 сырья A
        s.resources["raw_a"] = 3
        engine.advance_turn(s)
        engine.record_history_entry(s, "manual", "gather_a", "msg", report, [])
        # ход 2: +3 сырья A, появился 1 полуфабрикат
        s.resources["raw_a"] = 6
        s.resources["mid"] = 1
        engine.advance_turn(s)
        engine.record_history_entry(s, "manual", "craft_mid", "msg", report, [])
        # ход 3: построен генератор
        s.buildings["gen"] = 1
        engine.advance_turn(s)
        engine.record_history_entry(s, "build", "gen", "msg", report, [])

        stats = engine.compute_stats(s, 1, 3)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["turns_recorded"], 3)
        self.assertEqual(stats["resource_gained"]["raw_a"], 6)  # 3 + 3 (ход 2 raw_a не менялся)
        self.assertEqual(stats["buildings_built"], {"gen": 1})
        self.assertEqual(stats["turns_skipped"], 0)

    def test_compute_stats_empty_range_returns_none(self):
        s = GameState()
        self.assertIsNone(engine.compute_stats(s, 50, 60))

    def test_compute_stats_narrower_range(self):
        s = GameState()
        report = engine.ProductionReport()
        for amount in (3, 6, 9):
            s.resources["raw_a"] = amount
            engine.advance_turn(s)
            engine.record_history_entry(s, "manual", "gather_a", "msg", report, [])
        # диапазон только ход 2..3 не должен учитывать прирост хода 1
        stats = engine.compute_stats(s, 2, 3)
        self.assertEqual(stats["resource_gained"]["raw_a"], 6)

    def test_history_trimmed_to_max_entries(self):
        s = GameState()
        report = engine.ProductionReport()
        for _ in range(HISTORY_MAX_ENTRIES + 20):
            engine.advance_turn(s)
            engine.record_history_entry(s, "manual", "gather_a", "msg", report, [])
        self.assertEqual(len(s.history), HISTORY_MAX_ENTRIES)
        # выжили самые свежие записи, а не самые старые
        self.assertEqual(s.history[-1]["turn"], s.turn - 1)

    def test_from_dict_trims_bloated_history(self):
        bloated = [{"turn": i} for i in range(HISTORY_MAX_ENTRIES + 50)]
        s = GameState.from_dict({"history": bloated})
        self.assertEqual(len(s.history), HISTORY_MAX_ENTRIES)
        self.assertEqual(s.history[-1]["turn"], bloated[-1]["turn"])


def _first_choice(population, weights=None, k=1):
    """Подмена random.choices: всегда первый элемент (единственный исход
    фикстурной мутации, первая аллель пула вида)."""
    return [population[0]]


class GeneticsTests(FixtureTestCase):
    """Фикстура: ТРИ фермы (apiary - обычная, breeding_slots=2, без апгрейдов;
    electric_apiary - на энергии, breeding_slots=1, с апгрейдами breeding_output/
    breeding_immortality; industrial_apiary - breeding_slots=2, апгрейды НА
    ЭКЗЕМПЛЯР, включая environment_emulation ia_env_forest/ia_env_water),
    отдельный механизм скрещивания (crossing_station, breeding_mutation_chance),
    генлаборатория (genetics_lab_building, поле genetics_lab), 4 генетических
    признака (GENETIC_TRAITS: lifespan short(3)/normal(100), output_multiplier
    normal(1)/fast(2), active_period day(доминантна)/night/always, environment -
    6 закрытых id, forest доминантна) и два вида (critter_a доминантный/ловится
    дико, critter_b рецессивный/только через мутацию, требует critter_food).
    Одна мутация critter_a x critter_a -> critter_b (chance=0.5).
    fixtures.make_genotype(species_id, **traits) - гомозиготный генотип для
    тестов (environment по умолчанию "forest"). Фермы теперь физические
    ЭКЗЕМПЛЯРЫ (state.farm_instances, см. engine._ensure_farm_instances) - id
    экземпляра, а не building_id, передаётся в house_creature/
    purchase_farm_instance_upgrade; self._farm_instance() ниже достаёт id
    N-го (по умолчанию первого) экземпляра заданного типа, лениво создавая
    экземпляры через _ensure_farm_instances, если их ещё нет. См. tests/fixtures.py."""

    def _spawn(self, state, species_id, **genotype_kwargs):
        genotype = fixtures.make_genotype(species_id, **genotype_kwargs)
        return engine.spawn_creature(state, genotype, identified=False)

    def _farm_instance(self, state, building_id, index=0):
        """id экземпляра фермы building_id с заданным индексом (0 = построенный
        первым) - создаёт недостающие экземпляры лениво (_ensure_farm_instances),
        как это делает и сам движок при первом реальном обращении."""
        engine._ensure_farm_instances(state)
        ids = [iid for iid, inst in state.farm_instances.items() if inst["building_id"] == building_id]
        return ids[index]

    # ---- Фенотип/доминантность ----

    def test_phenotype_allele_picks_dominant_when_heterozygous(self):
        self.assertEqual(engine._phenotype_allele("lifespan", ["short", "normal"]), "normal")
        self.assertEqual(engine._phenotype_allele("lifespan", ["normal", "short"]), "normal")

    def test_phenotype_allele_picks_first_when_both_same_dominance(self):
        self.assertEqual(engine._phenotype_allele("output_multiplier", ["normal", "normal"]), "normal")

    def test_creature_phenotype_resolves_all_five_traits(self):
        genotype = fixtures.make_genotype(
            "critter_a", lifespan="short", output_multiplier="fast", active_period="night", environment="water",
        )
        phenotype = engine.creature_phenotype({"genotype": genotype})
        self.assertEqual(phenotype["species_id"], "critter_a")
        self.assertEqual(phenotype["lifespan"], 3)
        self.assertEqual(phenotype["output_multiplier"], 2)
        self.assertEqual(phenotype["active_period"], "night")
        self.assertEqual(phenotype["environment_id"], "water")

    def test_creature_phenotype_falls_back_to_dominant_environment_when_genotype_lacks_it(self):
        # Сохранение старше признака "environment" - в genotype его нет вовсе
        # (не [id, id], а ключ отсутствует). creature_phenotype обязан подставить
        # запасной вариант (доминантную аллель - "forest" в фикстуре), а не упасть.
        genotype = fixtures.make_genotype("critter_a")
        del genotype["environment"]
        phenotype = engine.creature_phenotype({"genotype": genotype})
        self.assertEqual(phenotype["environment_id"], "forest")

    def test_is_active_period_now_gates_day_and_night_but_not_always(self):
        self.assertTrue(engine._is_active_period_now("day", daytime=True))
        self.assertFalse(engine._is_active_period_now("day", daytime=False))
        self.assertFalse(engine._is_active_period_now("night", daytime=True))
        self.assertTrue(engine._is_active_period_now("night", daytime=False))
        self.assertTrue(engine._is_active_period_now("always", daytime=True))
        self.assertTrue(engine._is_active_period_now("always", daytime=False))

    # ---- Дикий улов (manual_actions.spawn_species) ----

    def test_roll_wild_genotype_is_homozygous_on_every_trait(self):
        with mock.patch.object(engine.random, "choices", side_effect=lambda ids, weights, k: [ids[0]]):
            genotype = engine._roll_wild_genotype("critter_a")
        self.assertEqual(genotype["species"], ["critter_a", "critter_a"])
        for trait_id in ("lifespan", "output_multiplier", "active_period", "environment"):
            self.assertEqual(genotype[trait_id][0], genotype[trait_id][1])

    def test_wild_genotype_is_rolled_only_from_the_species_gene_pool(self):
        # critter_b носит от природы только short/fast/night/water (fixtures.py) -
        # ни одной аллели вне пула, сколько ни лови.
        for _ in range(30):
            genotype = engine._roll_wild_genotype("critter_b")
            self.assertEqual(genotype["lifespan"], ["short", "short"])
            self.assertEqual(genotype["output_multiplier"], ["fast", "fast"])
            self.assertEqual(genotype["active_period"], ["night", "night"])
            self.assertEqual(genotype["environment"], ["water", "water"])

    def test_wild_genotype_follows_pool_weights(self):
        captured = []

        def spy(population, weights=None, k=1):
            captured.append(dict(zip(population, weights)))
            return [population[0]]

        with mock.patch.object(engine.random, "choices", side_effect=spy):
            engine._roll_wild_genotype("critter_a")
        self.assertIn({"forest": 3, "fields": 1}, captured)

    def test_species_gene_pool_drops_unknown_alleles_and_falls_back_to_dominant(self):
        self.addCleanup(engine.SPECIES["critter_a"].__setitem__, "gene_pool", engine.SPECIES["critter_a"]["gene_pool"])
        engine.SPECIES["critter_a"]["gene_pool"] = {"lifespan": {"no_such": 1, "short": 2, "normal": 0}}
        self.assertEqual(engine.species_gene_pool("critter_a", "lifespan"), {"short": 2})
        # Признака нет в пуле - "обычная особь": только доминантные аллели.
        self.assertEqual(engine.species_gene_pool("critter_a", "active_period"), {"day": 1})

    def test_manual_action_with_spawn_species_creates_an_unidentified_creature(self):
        gathered = {**engine.MANUAL_ACTIONS["gather_a"], "spawn_species": "critter_a", "gain": {}}
        s = GameState()
        with mock.patch.dict(engine.MANUAL_ACTIONS, {"gather_a": gathered}):
            result = engine.do_manual_action(s, "gather_a")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(s.creatures), 1)
        creature = next(iter(s.creatures.values()))
        self.assertEqual(creature["species_id"], "critter_a")
        self.assertFalse(creature["identified"])
        self.assertIsNone(creature["farm_instance_id"])
        self.assertIn("critter_a", s.species_discovered)

    # ---- Фермы: физические экземпляры (state.farm_instances), не общий пул ----

    def test_ensure_farm_instances_creates_one_instance_per_built_copy(self):
        s = GameState(buildings={"apiary": 2, "electric_apiary": 1, "gen": 1})
        engine._ensure_farm_instances(s)
        apiary_ids = [iid for iid, inst in s.farm_instances.items() if inst["building_id"] == "apiary"]
        electric_ids = [iid for iid, inst in s.farm_instances.items() if inst["building_id"] == "electric_apiary"]
        self.assertEqual(len(apiary_ids), 2)
        self.assertEqual(len(electric_ids), 1)
        # "gen" - не ферма (нет breeding_slots), экземпляров для неё быть не должно.
        self.assertTrue(all(inst["building_id"] != "gen" for inst in s.farm_instances.values()))

    def test_ensure_farm_instances_is_idempotent(self):
        s = GameState(buildings={"apiary": 2})
        engine._ensure_farm_instances(s)
        first = dict(s.farm_instances)
        engine._ensure_farm_instances(s)
        self.assertEqual(s.farm_instances, first)

    def test_farm_instance_slot_capacity_is_independent_per_instance(self):
        s = GameState(buildings={"apiary": 2, "electric_apiary": 1})
        apiary_1 = self._farm_instance(s, "apiary", 0)
        apiary_2 = self._farm_instance(s, "apiary", 1)
        electric_1 = self._farm_instance(s, "electric_apiary", 0)
        self.assertEqual(engine.farm_instance_slot_capacity(s, apiary_1), 2)
        self.assertEqual(engine.farm_instance_slot_capacity(s, apiary_2), 2)
        self.assertEqual(engine.farm_instance_slot_capacity(s, electric_1), 1)

    def test_farm_instance_slot_capacity_is_zero_for_a_broken_instance(self):
        # Агрегат state.broken["apiary"]=1 при первой синхронизации помечает
        # РОВНО один из двух новых экземпляров сломанным (см. _ensure_farm_instances).
        s = GameState(buildings={"apiary": 2}, broken={"apiary": 1})
        engine._ensure_farm_instances(s)
        broken_instances = [iid for iid, inst in s.farm_instances.items() if inst["broken"]]
        working_instances = [iid for iid, inst in s.farm_instances.items() if not inst["broken"]]
        self.assertEqual(len(broken_instances), 1)
        self.assertEqual(len(working_instances), 1)
        self.assertEqual(engine.farm_instance_slot_capacity(s, broken_instances[0]), 0)
        self.assertEqual(engine.farm_instance_slot_capacity(s, working_instances[0]), 2)

    # ---- house_creature / release_creature ----

    def test_house_creature_moves_from_bank_to_the_named_farm(self):
        s = GameState(buildings={"apiary": 1})
        cid = self._spawn(s, "critter_a")
        instance_id = self._farm_instance(s, "apiary")
        result = engine.house_creature(s, cid, instance_id)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.creatures[cid]["farm_instance_id"], instance_id)
        self.assertEqual(s.creatures[cid]["turns_alive"], 0)

    def test_demolish_evicts_a_housed_creature_back_to_the_bank(self):
        # Снос экземпляра фермы (engine.demolish_building) удаляет ЦЕЛИКОМ этот
        # физический экземпляр (state.farm_instances), но заселённая в него особь
        # не пропадает - расселяется в "банк" (farm_instance_id = None), тот же
        # приём, что при миграции старых сохранений в _ensure_farm_instances.
        s = GameState(buildings={"apiary": 1})
        instance_id = self._farm_instance(s, "apiary")
        cid = self._spawn(s, "critter_a")
        engine.house_creature(s, cid, instance_id)
        result = engine.demolish_building(s, "apiary")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.buildings["apiary"], 0)
        self.assertNotIn(instance_id, s.farm_instances)
        self.assertIsNone(s.creatures[cid]["farm_instance_id"])

    def test_house_creature_fails_without_a_free_slot_in_that_farm(self):
        s = GameState(buildings={"apiary": 1})  # 2 слота
        instance_id = self._farm_instance(s, "apiary")
        c1, c2, c3 = (self._spawn(s, "critter_a") for _ in range(3))
        engine.house_creature(s, c1, instance_id)
        engine.house_creature(s, c2, instance_id)
        result = engine.house_creature(s, c3, instance_id)  # третий слот занять нечем
        self.assertFalse(result.ok)
        self.assertIsNone(s.creatures[c3]["farm_instance_id"])

    def test_house_creature_fails_for_unknown_creature_or_non_farm_building(self):
        s = GameState(buildings={"apiary": 1, "gen": 1})
        instance_id = self._farm_instance(s, "apiary")
        self.assertFalse(engine.house_creature(s, "nope", instance_id).ok)
        cid = self._spawn(s, "critter_a")
        self.assertFalse(engine.house_creature(s, cid, "gen").ok)  # "gen" - не id экземпляра фермы

    def test_house_creature_fails_when_already_housed(self):
        s = GameState(buildings={"apiary": 1})
        cid = self._spawn(s, "critter_a")
        instance_id = self._farm_instance(s, "apiary")
        engine.house_creature(s, cid, instance_id)
        self.assertFalse(engine.house_creature(s, cid, instance_id).ok)

    def test_house_creature_rejects_a_species_outside_the_farms_breeding_accepts_family(self):
        # breeding_accepts_family - необязательное ограничение (buildings.*.
        # breeding_accepts_family, см. game/engine.py:breeding_accepts_family/
        # species_family) - без него любой вид годится (см. тесты выше, apiary
        # фикстуры его не задаёт). Тут временно даём critter_a/critter_b РАЗНЫЕ
        # семейства и сужаем apiary до семейства critter_a, чтобы проверить отказ.
        engine.SPECIES["critter_a"]["family"] = "alpha"
        engine.SPECIES["critter_b"]["family"] = "beta"
        engine.BUILDINGS["apiary"]["breeding_accepts_family"] = "alpha"

        def cleanup():
            engine.SPECIES["critter_a"].pop("family", None)
            engine.SPECIES["critter_b"].pop("family", None)
            engine.BUILDINGS["apiary"].pop("breeding_accepts_family", None)

        self.addCleanup(cleanup)
        s = GameState(buildings={"apiary": 1})
        instance_id = self._farm_instance(s, "apiary")
        allowed = self._spawn(s, "critter_a")
        rejected = self._spawn(s, "critter_b")
        self.assertTrue(engine.house_creature(s, allowed, instance_id).ok)
        result = engine.house_creature(s, rejected, instance_id)
        self.assertFalse(result.ok)
        self.assertIsNone(s.creatures[rejected]["farm_instance_id"])

    def test_farms_view_exposes_breeding_accepts_family_or_none_when_unrestricted(self):
        engine.BUILDINGS["apiary"]["breeding_accepts_family"] = "alpha"
        self.addCleanup(lambda: engine.BUILDINGS["apiary"].pop("breeding_accepts_family", None))
        s = GameState(buildings={"apiary": 1, "electric_apiary": 1})
        views = {f["building_id"]: f for f in engine.farms_view(s)}
        self.assertEqual(views["apiary"]["accepts_family"], "alpha")
        self.assertIsNone(views["electric_apiary"]["accepts_family"])

    def test_creature_view_exposes_species_family(self):
        engine.SPECIES["critter_a"]["family"] = "alpha"
        self.addCleanup(lambda: engine.SPECIES["critter_a"].pop("family", None))
        s = GameState()
        cid = self._spawn(s, "critter_a")
        entry = engine.creature_view(s, cid)
        self.assertEqual(entry["family"], "alpha")

    def test_release_creature_returns_to_the_bank(self):
        s = GameState(buildings={"apiary": 1})
        cid = self._spawn(s, "critter_a")
        instance_id = self._farm_instance(s, "apiary")
        engine.house_creature(s, cid, instance_id)
        result = engine.release_creature(s, cid)
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(s.creatures[cid]["farm_instance_id"])

    def test_release_creature_fails_when_not_housed_or_unknown(self):
        s = GameState()
        cid = self._spawn(s, "critter_a")
        self.assertFalse(engine.release_creature(s, cid).ok)
        self.assertFalse(engine.release_creature(s, "nope").ok)

    # ---- breed_creatures: менделевское наследование + мутация ----
    #
    # Скрещивание требует ОТДЕЛЬНЫЙ работающий механизм (crossing_station, поле
    # breeding_station) - НЕ ферму (apiary/electric_apiary просто расселяют
    # особей). Оно всегда бесплатно, кроме самих родителей.

    def test_breed_creatures_removes_both_parents_and_spawns_a_child(self):
        s = GameState(buildings={"crossing_station": 1})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        self.assertNotIn(a, s.creatures)
        self.assertNotIn(b, s.creatures)
        self.assertEqual(len(s.creatures), 1)  # только ребёнок

    def test_breed_creatures_rejects_same_parent_twice_or_unknown_id(self):
        s = GameState(buildings={"crossing_station": 1})
        a = self._spawn(s, "critter_a")
        self.assertFalse(engine.breed_creatures(s, a, a).ok)
        self.assertFalse(engine.breed_creatures(s, a, "nope").ok)

    def test_breed_creatures_rejects_parents_of_different_families(self):
        # family (species.*.family, напр. "пчела"/"цветок") - разные семейства
        # скрещивать нельзя (см. game/engine.py:breed_creatures) - ни одна
        # мутация в реальном контенте не пересекает семейства, смешение
        # никогда не имело смысла.
        engine.SPECIES["critter_a"]["family"] = "alpha"
        engine.SPECIES["critter_b"]["family"] = "beta"
        self.addCleanup(lambda: engine.SPECIES["critter_a"].pop("family", None))
        self.addCleanup(lambda: engine.SPECIES["critter_b"].pop("family", None))
        s = GameState(buildings={"crossing_station": 1})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_b")
        result = engine.breed_creatures(s, a, b)
        self.assertFalse(result.ok, result.message)
        self.assertIn(a, s.creatures)
        self.assertIn(b, s.creatures)

    def test_breed_creatures_fails_without_a_working_crossing_station(self):
        s = GameState()  # механизм скрещивания не построен
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        self.assertFalse(engine.breed_creatures(s, a, b).ok)

    def test_breed_creatures_fails_when_only_a_farm_is_built(self):
        # Ферма (breeding_slots) сама по себе скрещивание НЕ открывает - нужен
        # именно breeding_station (см. game/engine.py:breeding_station_available).
        s = GameState(buildings={"apiary": 1})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        self.assertFalse(engine.breed_creatures(s, a, b).ok)

    def test_breed_creatures_fails_when_crossing_station_is_broken(self):
        s = GameState(buildings={"crossing_station": 1}, broken={"crossing_station": 1})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        self.assertFalse(engine.breed_creatures(s, a, b).ok)

    def test_breed_creatures_has_no_extra_cost_beyond_the_parents(self):
        s = GameState(buildings={"crossing_station": 1}, resources={"raw_a": 0})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources.get("raw_a", 0), 0)  # ничего не списалось сверху

    def test_child_inherits_one_allele_per_trait_from_each_parent(self):
        s = GameState(buildings={"crossing_station": 1})
        a = self._spawn(s, "critter_a", lifespan="short")
        b = self._spawn(s, "critter_a", lifespan="normal")
        # 0.99 - мимо фикстурной мутации (chance=0.5): иначе половина генома
        # пришла бы из пула critter_b.
        with mock.patch.object(engine.random, "choice", side_effect=lambda pair: pair[0]),                 mock.patch.object(engine.random, "random", return_value=0.99):
            result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        child = next(iter(s.creatures.values()))
        self.assertEqual(set(child["genotype"]["lifespan"]), {"short", "normal"})

    def test_mutation_can_replace_the_species_pair_with_a_pure_new_species(self):
        # Фикстурная мутация - critter_a x critter_a -> critter_b (parent_a==parent_b
        # НАРОЧНО одинаковы, см. MUTATIONS в fixtures.py), chance=0.5 - random()=0.0
        # заведомо попадает в первый (и единственный) отрезок мутации [0, 0.5).
        s = GameState(buildings={"crossing_station": 1})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        with mock.patch.object(engine.random, "choice", side_effect=lambda pair: pair[0]), \
                mock.patch.object(engine.random, "random", return_value=0.0), \
                mock.patch.object(engine.random, "choices", side_effect=_first_choice):
            result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        self.assertIn("Мутация!", result.message)
        child = next(iter(s.creatures.values()))
        self.assertEqual(child["genotype"]["species"], ["critter_b", "critter_b"])  # чистокровна

    def test_mutation_child_carries_one_allele_per_trait_from_the_new_species_pool(self):
        # Мутация critter_a x critter_a -> critter_b: у родителей только "обычные"
        # гены, у critter_b в пуле - только short/fast/night/water. Потомок несёт по
        # одной аллели из пула нового вида и по одной от родителя.
        s = GameState(buildings={"crossing_station": 1})
        a = self._spawn(s, "critter_a")
        b = self._spawn(s, "critter_a", environment="fields")
        with mock.patch.object(engine.random, "random", return_value=0.0):
            result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        child = next(iter(s.creatures.values()))
        genotype = child["genotype"]
        self.assertEqual(genotype["species"], ["critter_b", "critter_b"])
        self.assertEqual(genotype["lifespan"], ["short", "normal"])
        self.assertEqual(genotype["output_multiplier"], ["fast", "normal"])
        self.assertEqual(genotype["active_period"], ["night", "day"])
        self.assertEqual(genotype["environment"][0], "water")
        # Вторая половина - от ОДНОГО родителя целиком (монетка одна на геном).
        self.assertIn(genotype["environment"][1], ("forest", "fields"))

    def test_cross_without_mutation_takes_no_genes_from_species_pools(self):
        s = GameState(buildings={"crossing_station": 1})
        a = self._spawn(s, "critter_a", lifespan="short")
        b = self._spawn(s, "critter_a", lifespan="short")
        with mock.patch.object(engine.random, "random", return_value=0.99):
            engine.breed_creatures(s, a, b)
        child = next(iter(s.creatures.values()))
        self.assertEqual(child["genotype"]["lifespan"], ["short", "short"])

    def test_environment_mutation_rerolls_from_the_child_species_pool(self):
        self.addCleanup(engine.GENETICS_SETTINGS.__setitem__, "environment_mutation_chance", 0.0)
        engine.GENETICS_SETTINGS["environment_mutation_chance"] = 1.0
        s = GameState(buildings={"crossing_station": 1})
        a = self._spawn(s, "critter_b", environment="space")
        b = self._spawn(s, "critter_b", environment="space")
        engine.breed_creatures(s, a, b)
        child = next(iter(s.creatures.values()))
        self.assertEqual(child["genotype"]["environment"], ["water", "water"])

    def test_species_catalog_exposes_gene_pool_shares(self):
        catalog = {entry["id"]: entry for entry in engine.species_catalog_view(GameState())}
        self.assertEqual(catalog["critter_a"]["gene_pool"]["environment"], {"forest": 0.75, "fields": 0.25})
        self.assertEqual(catalog["critter_b"]["gene_pool"]["lifespan"], {"short": 1.0})
        self.assertNotIn("species", catalog["critter_a"]["gene_pool"])

    def test_mutation_does_not_fire_when_chance_roll_fails(self):
        # chance=0.5 -> отрезок мутации [0, 0.5), остаток [0.5, 1) делится поровну
        # между родителями (тут species_a == species_b == critter_a, так что
        # результат один и тот же независимо от того, в какую половину попал roll).
        s = GameState(buildings={"crossing_station": 1})
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        with mock.patch.object(engine.random, "choice", side_effect=lambda pair: pair[0]), \
                mock.patch.object(engine.random, "random", return_value=0.99):
            result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        self.assertNotIn("Мутация!", result.message)
        child = next(iter(s.creatures.values()))
        self.assertEqual(child["genotype"]["species"], ["critter_a", "critter_a"])  # мутация не сработала

    def test_mutation_chance_is_multiplied_by_the_crossing_station_upgrade(self):
        # Базовый chance фикстуры = 0.5. Без апгрейда 0.9 не проходит (0.9 < 0.5 - ложь);
        # с купленным cs_mutation (x2 -> эффективный порог 1.0) - проходит.
        s = GameState(buildings={"crossing_station": 1})
        s.building_upgrades["crossing_station"] = ["cs_mutation"]
        a, b = self._spawn(s, "critter_a"), self._spawn(s, "critter_a")
        with mock.patch.object(engine.random, "choice", side_effect=lambda pair: pair[0]), \
                mock.patch.object(engine.random, "random", return_value=0.9), \
                mock.patch.object(engine.random, "choices", side_effect=_first_choice):
            result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        child = next(iter(s.creatures.values()))
        self.assertEqual(child["genotype"]["species"], ["critter_b", "critter_b"])

    def test_cross_of_species_without_any_mutation_recipe_always_yields_a_pure_parent_species(self):
        # critter_a x critter_b - для ЭТОЙ пары в фикстуре нет ни одной мутации,
        # так что весь [0, 1) делится поровну между "чистый critter_a" и "чистый
        # critter_b" (см. _cross_genotype: candidates пуст, cursor=0).
        s = GameState(buildings={"crossing_station": 1})
        a = self._spawn(s, "critter_a")
        b = self._spawn(s, "critter_b")
        with mock.patch.object(engine.random, "choice", side_effect=lambda pair: pair[0]), \
                mock.patch.object(engine.random, "random", return_value=0.3):
            result = engine.breed_creatures(s, a, b)
        self.assertTrue(result.ok, result.message)
        child = next(iter(s.creatures.values()))
        self.assertEqual(child["genotype"]["species"], ["critter_a", "critter_a"])

        s2 = GameState(buildings={"crossing_station": 1})
        a2 = self._spawn(s2, "critter_a")
        b2 = self._spawn(s2, "critter_b")
        with mock.patch.object(engine.random, "choice", side_effect=lambda pair: pair[0]), \
                mock.patch.object(engine.random, "random", return_value=0.7):
            result2 = engine.breed_creatures(s2, a2, b2)
        self.assertTrue(result2.ok, result2.message)
        child2 = next(iter(s2.creatures.values()))
        self.assertEqual(child2["genotype"]["species"], ["critter_b", "critter_b"])

    # ---- Генетическая лаборатория: опознание/синтез ----

    def test_genetics_lab_available_requires_a_working_copy(self):
        s = GameState()
        self.assertFalse(engine.genetics_lab_available(s))
        s.buildings["genetics_lab_building"] = 1
        self.assertTrue(engine.genetics_lab_available(s))
        s.broken["genetics_lab_building"] = 1
        self.assertFalse(engine.genetics_lab_available(s))

    def test_analyze_creature_requires_lab_and_marks_identified(self):
        s = GameState()
        cid = self._spawn(s, "critter_a", lifespan="short")
        self.assertFalse(engine.analyze_creature(s, cid).ok)  # нет лаборатории
        s.buildings["genetics_lab_building"] = 1
        result = engine.analyze_creature(s, cid)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(s.creatures[cid]["identified"])
        self.assertIn("critter_a", s.known_alleles["species"])
        self.assertIn("short", s.known_alleles["lifespan"])

    def test_analyze_creature_fails_when_already_identified_or_unknown(self):
        s = GameState(buildings={"genetics_lab_building": 1})
        cid = self._spawn(s, "critter_a")
        engine.analyze_creature(s, cid)
        self.assertFalse(engine.analyze_creature(s, cid).ok)
        self.assertFalse(engine.analyze_creature(s, "nope").ok)

    def test_synthesize_creature_requires_a_known_allele_for_every_trait(self):
        s = GameState(buildings={"genetics_lab_building": 1}, resources={"species_output": 100})
        chosen = {
            "species": "critter_a", "lifespan": "normal", "output_multiplier": "normal",
            "active_period": "day", "environment": "forest",
        }
        result = engine.synthesize_creature(s, chosen)
        self.assertFalse(result.ok)  # ничего ещё не изучено

    def test_synthesize_creature_produces_a_pure_already_identified_creature(self):
        s = GameState(buildings={"genetics_lab_building": 1}, resources={"species_output": 100})
        cid = self._spawn(s, "critter_a", lifespan="short", output_multiplier="fast", active_period="night", environment="water")
        engine.analyze_creature(s, cid)
        chosen = {
            "species": "critter_a", "lifespan": "short", "output_multiplier": "fast",
            "active_period": "night", "environment": "water",
        }
        result = engine.synthesize_creature(s, chosen)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["species_output"], 90)  # synthesis_cost = {species_output: 10}
        new_ids = set(s.creatures) - {cid}
        self.assertEqual(len(new_ids), 1)
        child = s.creatures[next(iter(new_ids))]
        self.assertTrue(child["identified"])
        self.assertEqual(child["genotype"]["lifespan"], ["short", "short"])
        self.assertEqual(child["genotype"]["output_multiplier"], ["fast", "fast"])

    # ---- _process_habitat (через advance_turn) ----

    # ВАЖНО: во всех тестах ниже - active_period="always" ЯВНО (по умолчанию у
    # make_genotype "day", а turn=1 фикстурного TIME - ночь: hours_per_turn=3,
    # day_start_hour=6 => час на turn=1 равен 0, что < 6) - иначе особь просто
    # простаивала бы этот ход, не относясь к тому, что тест на самом деле проверяет.

    def test_habitat_individual_produces_by_phenotype_each_turn(self):
        s = GameState(buildings={"apiary": 1})
        cid = self._spawn(s, "critter_a", output_multiplier="fast", active_period="always")
        engine.house_creature(s, cid, self._farm_instance(s, "apiary"))
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("species_output"), 4)  # 2 базовых * фенотип x2
        self.assertEqual(s.creatures[cid]["turns_alive"], 1)

    def test_habitat_individual_without_upkeep_skips_production_but_survives(self):
        s = GameState(buildings={"apiary": 1})
        cid = engine.spawn_creature(s, fixtures.make_genotype("critter_b", active_period="always"), identified=False)
        engine.house_creature(s, cid, self._farm_instance(s, "apiary"))
        engine.advance_turn(s)
        self.assertNotIn("species_output", s.resources)
        self.assertIn(cid, s.creatures)
        self.assertEqual(s.creatures[cid]["turns_alive"], 1)

    def test_habitat_individual_consumes_upkeep_when_affordable(self):
        s = GameState(buildings={"apiary": 1}, resources={"critter_food": 10})
        cid = engine.spawn_creature(s, fixtures.make_genotype("critter_b", active_period="always"), identified=False)
        engine.house_creature(s, cid, self._farm_instance(s, "apiary"))
        engine.advance_turn(s)
        self.assertEqual(s.resources["critter_food"], 9)
        self.assertEqual(s.resources.get("species_output"), 5)

    def test_habitat_individual_dies_after_reaching_lifespan(self):
        s = GameState(buildings={"apiary": 1}, resources={"critter_food": 10})
        cid = engine.spawn_creature(
            s, fixtures.make_genotype("critter_b", lifespan="short", active_period="always"), identified=False,
        )
        engine.house_creature(s, cid, self._farm_instance(s, "apiary"))
        report = None
        for _ in range(3):
            report = engine.advance_turn(s)
        self.assertNotIn(cid, s.creatures)
        self.assertEqual(report.habitat_died, {"critter_b": 1})

    def test_habitat_individual_immortal_with_farm_upgrade(self):
        # state.building_upgrades заполнен ДО первого обращения к экземплярам -
        # _ensure_farm_instances копирует его в upgrades только что созданного
        # экземпляра (см. CLAUDE.md, "Пасеки: индивидуальные экземпляры") -
        # так что этот тест по-прежнему проверяет ту же цепочку эффекта,
        # только теперь апгрейд физически хранится на экземпляре, а не на типе.
        s = GameState(buildings={"electric_apiary": 1}, resources={"critter_food": 100})
        s.building_upgrades["electric_apiary"] = ["ea_immortal"]
        cid = engine.spawn_creature(
            s, fixtures.make_genotype("critter_b", lifespan="short", active_period="always"), identified=False,
        )
        engine.house_creature(s, cid, self._farm_instance(s, "electric_apiary"))
        for _ in range(5):
            engine.advance_turn(s)
        self.assertIn(cid, s.creatures)  # не умерла, несмотря на короткий срок жизни

    def test_habitat_individual_output_boosted_by_farm_upgrade(self):
        s = GameState(buildings={"electric_apiary": 1}, resources={"energy": 100})
        s.building_upgrades["electric_apiary"] = ["ea_output"]
        cid = self._spawn(s, "critter_a", active_period="always")
        engine.house_creature(s, cid, self._farm_instance(s, "electric_apiary"))
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("species_output"), 4)  # 2 базовых * x2 апгрейда фермы

    def test_habitat_individual_not_housed_does_not_produce_or_age(self):
        s = GameState()
        cid = self._spawn(s, "critter_a", active_period="always")
        engine.advance_turn(s)
        self.assertNotIn("species_output", s.resources)
        self.assertEqual(s.creatures[cid]["turns_alive"], 0)

    # ---- Промышленная пасека: апгрейды НА ЭКЗЕМПЛЯР + бонусная продукция среды ----

    def test_two_instances_of_the_same_farm_type_get_independent_upgrades(self):
        s = GameState(buildings={"industrial_apiary": 2}, resources={"raw_a": 100})
        instance_1 = self._farm_instance(s, "industrial_apiary", 0)
        instance_2 = self._farm_instance(s, "industrial_apiary", 1)
        result = engine.purchase_farm_instance_upgrade(s, instance_1, "ia_output")
        self.assertTrue(result.ok, result.message)
        self.assertTrue(engine._farm_instance_has_upgrade(s, instance_1, "breeding_output"))
        self.assertFalse(engine._farm_instance_has_upgrade(s, instance_2, "breeding_output"))

    def test_environment_emulation_upgrades_are_mutually_exclusive(self):
        s = GameState(buildings={"industrial_apiary": 1}, resources={"raw_a": 100})
        instance_id = self._farm_instance(s, "industrial_apiary")
        engine.purchase_farm_instance_upgrade(s, instance_id, "ia_env_forest")
        self.assertEqual(engine._instance_environment_id(s, instance_id), "forest")
        result = engine.purchase_farm_instance_upgrade(s, instance_id, "ia_env_water")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(engine._instance_environment_id(s, instance_id), "water")
        self.assertNotIn("ia_env_forest", s.farm_instances[instance_id]["upgrades"])

    def test_bonus_production_fires_when_creature_environment_matches_active_emulation(self):
        s = GameState(buildings={"industrial_apiary": 1}, resources={"raw_a": 100})
        instance_id = self._farm_instance(s, "industrial_apiary")
        engine.purchase_farm_instance_upgrade(s, instance_id, "ia_env_forest")
        cid = self._spawn(s, "critter_a", active_period="always", environment="forest")
        engine.house_creature(s, cid, instance_id)
        with mock.patch.object(engine.random, "random", return_value=0.0):
            engine.advance_turn(s)
        # critter_a.bonus_resource "species_output" в фикстуре, тот же ресурс, что и у
        # обычной продукции critter_a - оба начисления складываются: 2 (вид) + 1 (бонус).
        self.assertEqual(s.resources.get("species_output"), 3)

    def test_creature_view_shows_the_species_environment_bonus(self):
        # Бонус среды - поле ВИДА (как в _process_habitat), а не аллели среды.
        s = GameState(buildings={"industrial_apiary": 1}, resources={"raw_a": 100})
        instance_id = self._farm_instance(s, "industrial_apiary")
        engine.purchase_farm_instance_upgrade(s, instance_id, "ia_env_forest")
        cid = self._spawn(s, "critter_a", active_period="always", environment="forest")
        engine.house_creature(s, cid, instance_id)
        s.creatures[cid]["identified"] = True
        self.assertEqual(
            engine.creature_view(s, cid)["bonus_produce"], {"resource_id": "species_output", "chance": 0.3},
        )

    def test_bonus_production_does_not_fire_on_environment_mismatch(self):
        s = GameState(buildings={"industrial_apiary": 1}, resources={"raw_a": 100})
        instance_id = self._farm_instance(s, "industrial_apiary")
        engine.purchase_farm_instance_upgrade(s, instance_id, "ia_env_water")  # эмулирует "water"
        cid = self._spawn(s, "critter_a", active_period="always", environment="forest")  # у пчелы "forest"
        engine.house_creature(s, cid, instance_id)
        with mock.patch.object(engine.random, "random", return_value=0.0):
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("species_output"), 2)  # только обычная продукция, без бонуса

    def test_bonus_production_does_not_fire_without_any_environment_emulation_upgrade(self):
        s = GameState(buildings={"industrial_apiary": 1})
        instance_id = self._farm_instance(s, "industrial_apiary")  # без апгрейдов
        cid = self._spawn(s, "critter_a", active_period="always", environment="forest")
        engine.house_creature(s, cid, instance_id)
        with mock.patch.object(engine.random, "random", return_value=0.0):
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("species_output"), 2)

    def test_bonus_production_resource_depends_on_species_not_environment(self):
        # Два РАЗНЫХ вида в ОДНОЙ и той же благоприятной среде ("forest") должны
        # получить бонус каждый в СВОЙ ресурс (critter_a -> species_output,
        # critter_b -> raw_a, см. SPECIES в tests/fixtures.py) - бонус завязан на
        # вид, а не на среду (среда лишь решает, сработает ли бонус вообще).
        s = GameState(buildings={"industrial_apiary": 2}, resources={"raw_a": 100, "critter_food": 100})
        instance_a = self._farm_instance(s, "industrial_apiary", 0)
        instance_b = self._farm_instance(s, "industrial_apiary", 1)
        engine.purchase_farm_instance_upgrade(s, instance_a, "ia_env_forest")
        engine.purchase_farm_instance_upgrade(s, instance_b, "ia_env_forest")
        cid_a = self._spawn(s, "critter_a", active_period="always", environment="forest")
        cid_b = self._spawn(s, "critter_b", active_period="always", environment="forest")
        engine.house_creature(s, cid_a, instance_a)
        engine.house_creature(s, cid_b, instance_b)
        raw_a_before_turn = s.resources.get("raw_a", 0)  # после оплаты построек/апгрейдов выше
        with mock.patch.object(engine.random, "random", return_value=0.0):
            engine.advance_turn(s)
        # critter_a: 2 (вид) + 1 (бонус в species_output); critter_b: 5 (вид, тоже в
        # species_output - оба вида производят один и тот же ресурс) + СВОЙ бонус в raw_a.
        self.assertEqual(s.resources.get("species_output"), 8)
        self.assertEqual(s.resources.get("raw_a"), raw_a_before_turn + 1)  # critter_b: бонус +1 raw_a

    def test_old_save_migrates_buildings_and_relocates_a_creature_by_its_old_type_id(self):
        # Имитация сохранения СТАРШЕ переименования farm_building_id -> farm_instance_id
        # (см. CLAUDE.md) - state.farm_instances пуст, но state.buildings["apiary"]=2
        # уже есть, и особь хранит id ТИПА постройки там, где теперь ожидается id
        # экземпляра (ровно то, что подставляет GameState.from_dict для старых
        # сохранений - см. game/state.py).
        s = GameState(buildings={"apiary": 2})
        cid = self._spawn(s, "critter_a")
        s.creatures[cid]["farm_instance_id"] = "apiary"
        engine._ensure_farm_instances(s)
        self.assertEqual(len(s.farm_instances), 2)
        self.assertTrue(all(inst["building_id"] == "apiary" for inst in s.farm_instances.values()))
        self.assertIn(s.creatures[cid]["farm_instance_id"], s.farm_instances)

    def test_no_auto_identify_over_time_in_farm(self):
        s = GameState(buildings={"apiary": 1})
        cid = self._spawn(s, "critter_a", active_period="always")
        engine.house_creature(s, cid, self._farm_instance(s, "apiary"))
        for _ in range(50):
            engine.advance_turn(s)
        self.assertFalse(s.creatures[cid]["identified"])
        self.assertNotIn("critter_a", s.known_alleles.get("species", []))

    def test_species_discovered_at_least_quest(self):
        s = GameState()
        s.species_discovered.add("critter_a")
        quest = {
            "name": "Коллекционер", "description": "Открой хотя бы один вид.",
            "tier": 1, "conditions": [{"type": "species_discovered_at_least", "amount": 1}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"collector": quest}):
            completed = engine.check_quests(s)
        self.assertIn("collector", completed)

    def test_species_discovered_quest_targets_one_specific_species(self):
        # В отличие от species_discovered_at_least (просто счётчик), этот тип
        # завязан на КОНКРЕТНЫЙ species_id - открытие ДРУГОГО вида не считается.
        s = GameState()
        s.species_discovered.add("critter_a")
        quest = {
            "name": "Пчеловод", "description": "Выведи critter_b.",
            "tier": 1, "conditions": [{"type": "species_discovered", "species": "critter_b"}],
            "reward_case": "test_case",
        }
        with mock.patch.dict(engine.QUESTS, {"breeder": quest}):
            self.assertNotIn("breeder", engine.check_quests(s))
            s.species_discovered.add("critter_b")
            self.assertIn("breeder", engine.check_quests(s))


if __name__ == "__main__":
    unittest.main()
