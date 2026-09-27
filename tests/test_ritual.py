"""
Тесты ритуального круга (game/engine.py: ritual_pedestal_capacity,
is_ritual_recipe_available, start_ritual, cancel_ritual, _process_ritual_jobs).
Работают на самодостаточной тестовой фикстуре (tests/fixtures.py), не на
реальном game/content.json.

Запуск: python -m unittest tests.test_ritual -v
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


class FixtureTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)


class RitualPedestalCapacityTests(FixtureTestCase):
    def test_zero_without_building(self):
        s = GameState()
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 0)

    def test_base_scales_with_working_copies(self):
        s = GameState(buildings={"ritual_altar": 2})
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 6)  # 3 пьедестала/копия * 2 копии

    def test_ignores_broken_copies(self):
        s = GameState(buildings={"ritual_altar": 2}, broken={"ritual_altar": 1})
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 3)

    def test_upgrade_tiers_progress_3_then_6_then_9(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 100})
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 3)
        engine.purchase_building_upgrade(s, "ritual_altar", "ritual_altar_ring_6")
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 6)  # база 3 + бонус 3
        engine.purchase_building_upgrade(s, "ritual_altar", "ritual_altar_ring_9")
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 9)  # база 3 + сильнейший бонус 6 (не сумма)

    def test_second_tier_requires_first_tier_first(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 100})
        result = engine.purchase_building_upgrade(s, "ritual_altar", "ritual_altar_ring_9")
        self.assertFalse(result.ok)
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 3)

    def test_zero_when_all_broken_even_with_upgrade(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "ritual_altar", "ritual_altar_ring_6")
        s.broken["ritual_altar"] = 1
        self.assertEqual(engine.ritual_pedestal_capacity(s, "ritual_altar"), 0)


class RitualAvailabilityTests(FixtureTestCase):
    def test_unavailable_without_building(self):
        s = GameState()
        self.assertFalse(engine.is_ritual_recipe_available("ritual_basic", s))

    def test_available_with_enough_pedestals(self):
        s = GameState(buildings={"ritual_altar": 1})
        self.assertTrue(engine.is_ritual_recipe_available("ritual_basic", s))

    def test_unavailable_without_tech(self):
        s = GameState(buildings={"ritual_altar": 1})
        self.assertFalse(engine.is_ritual_recipe_available("ritual_locked", s))
        s.techs.add("tech1")
        self.assertTrue(engine.is_ritual_recipe_available("ritual_locked", s))

    def test_unavailable_without_enough_pedestals(self):
        s = GameState(buildings={"ritual_altar": 1})
        self.assertFalse(engine.is_ritual_recipe_available("ritual_grand", s))  # нужно 4, база 3
        s.resources["raw_a"] = 100
        engine.purchase_building_upgrade(s, "ritual_altar", "ritual_altar_ring_6")
        self.assertTrue(engine.is_ritual_recipe_available("ritual_grand", s))

    def test_available_ritual_recipes_lists_only_unlocked(self):
        s = GameState(buildings={"ritual_altar": 1})
        available = engine.available_ritual_recipes(s)
        self.assertIn("ritual_basic", available)
        self.assertNotIn("ritual_locked", available)
        self.assertNotIn("ritual_grand", available)


class StartRitualTests(FixtureTestCase):
    def test_rejects_unknown_recipe(self):
        s = GameState(buildings={"ritual_altar": 1})
        result = engine.start_ritual(s, "no_such_recipe")
        self.assertFalse(result.ok)

    def test_rejects_when_unavailable(self):
        s = GameState()
        result = engine.start_ritual(s, "ritual_basic")
        self.assertFalse(result.ok)

    def test_rejects_without_enough_resources(self):
        s = GameState(buildings={"ritual_altar": 1})
        result = engine.start_ritual(s, "ritual_basic")
        self.assertFalse(result.ok)
        self.assertNotIn("ritual_altar", s.ritual_jobs)

    def test_instant_recipe_pays_and_produces_immediately(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 2, "raw_b": 1})
        result = engine.start_ritual(s, "ritual_basic")
        self.assertTrue(result.ok)
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertEqual(s.resources.get("raw_b", 0), 0)  # center_cost - тоже списан
        self.assertEqual(s.resources.get("mid", 0), 1)
        self.assertNotIn("ritual_altar", s.ritual_jobs)  # мгновенный - job не заводится

    def test_multiturn_recipe_pays_upfront_and_creates_job(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 1, "raw_b": 1})
        result = engine.start_ritual(s, "ritual_slow")
        self.assertTrue(result.ok)
        self.assertEqual(s.resources.get("raw_a", 0), 0)  # ring_cost списан целиком при старте
        self.assertEqual(s.resources.get("raw_b", 0), 0)
        self.assertEqual(s.resources.get("mid", 0), 0)  # produces ещё не начислен
        job = s.ritual_jobs["ritual_altar"]
        self.assertEqual(job["recipe_id"], "ritual_slow")
        self.assertEqual(job["turns_done"], 0)
        self.assertEqual(job["turns_total"], 2)

    def test_rejects_second_ritual_on_same_building(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 3, "raw_b": 2})
        engine.start_ritual(s, "ritual_slow")
        result = engine.start_ritual(s, "ritual_slow")
        self.assertFalse(result.ok)

    def test_produces_chance_rolled_on_instant_completion(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 1, "raw_b": 1})
        with mock.patch("random.random", return_value=0.9):  # chance 0.5 fails
            engine.start_ritual(s, "ritual_variable")
        self.assertEqual(s.resources.get("mid", 0), 0)
        s2 = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 1, "raw_b": 1})
        with mock.patch("random.random", return_value=0.1):  # chance 0.5 succeeds
            engine.start_ritual(s2, "ritual_variable")
        self.assertEqual(s2.resources.get("mid", 0), 2)


class ProcessRitualJobsTests(FixtureTestCase):
    def test_turns_done_ticks_unconditionally(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 1, "raw_b": 1})
        engine.start_ritual(s, "ritual_slow")  # duration 2, extra_consumes_per_turn {raw_a: 1}
        s.resources["raw_a"] = 0  # нечем платить extra за ход
        engine.advance_turn(s)
        self.assertEqual(s.ritual_jobs["ritual_altar"]["turns_done"], 1)
        self.assertEqual(s.resources.get("raw_a", 0), 0)  # нехватка - тихо пропущено, не откатилось
        engine.advance_turn(s)
        self.assertNotIn("ritual_altar", s.ritual_jobs)  # завершился и удалился
        self.assertEqual(s.resources.get("mid", 0), 2)  # produces развернулся один раз

    def test_guaranteed_extra_consumed_each_turn_when_affordable(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 3, "raw_b": 1})
        engine.start_ritual(s, "ritual_slow")  # ring_cost {raw_a:1} уже списан -> raw_a=2 в наличии
        self.assertEqual(s.resources.get("raw_a", 0), 2)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 1)  # extra_consumes_per_turn {raw_a: 1} списан
        self.assertEqual(s.ritual_jobs["ritual_altar"]["turns_done"], 1)

    def test_chance_extra_rolled_independently_each_turn(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 1, "raw_b": 5})
        engine.start_ritual(s, "ritual_chancy")  # duration 2, extra_consumes_per_turn {raw_b:1}, chance 0.5
        self.assertEqual(s.resources.get("raw_b", 0), 4)  # center_cost 1 списан при старте
        with mock.patch("random.random", return_value=0.1):  # chance 0.5 succeeds -> списывается
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_b", 0), 3)
        with mock.patch("random.random", return_value=0.9):  # chance 0.5 fails -> не списывается
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_b", 0), 3)
        self.assertEqual(s.resources.get("final", 0), 1)  # ритуал всё равно завершился

    def test_multiple_completions_add_produces_once_each(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 1, "raw_b": 1})
        engine.start_ritual(s, "ritual_slow")
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("mid", 0), 0)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("mid", 0), 2)
        engine.advance_turn(s)  # ничего больше не идёт - лишнего начисления нет
        self.assertEqual(s.resources.get("mid", 0), 2)


class CancelRitualTests(FixtureTestCase):
    def test_rejects_when_nothing_running(self):
        s = GameState(buildings={"ritual_altar": 1})
        result = engine.cancel_ritual(s, "ritual_altar")
        self.assertFalse(result.ok)

    def test_refunds_paid_cost_but_not_spent_extra(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 3, "raw_b": 1})
        engine.start_ritual(s, "ritual_slow")  # raw_a 3 -> 2 (ring_cost 1 списан)
        engine.advance_turn(s)  # extra_consumes_per_turn {raw_a: 1} списан -> raw_a 1
        self.assertEqual(s.resources.get("raw_a", 0), 1)
        result = engine.cancel_ritual(s, "ritual_altar")
        self.assertTrue(result.ok)
        self.assertNotIn("ritual_altar", s.ritual_jobs)
        # Возвращается только ОДНОРАЗОВЫЙ cost (ring 1 + center 1 = 2), уже потраченный
        # extra за прошедший ход - не возвращается.
        self.assertEqual(s.resources.get("raw_a", 0), 2)


if __name__ == "__main__":
    unittest.main()
