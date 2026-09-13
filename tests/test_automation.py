"""
Тесты автоматизатора (правила "если ресурс - тогда действие/рецепт", game/engine.py:
process_automation_rules и окружение). Работают на самодостаточной тестовой
фикстуре (tests/fixtures.py), не на реальном game/content.json.

Запуск: python -m unittest tests.test_automation -v
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


class CapacityTests(FixtureTestCase):
    def test_zero_without_building(self):
        s = GameState()
        self.assertEqual(engine.automation_slots_capacity(s, "automator"), 0)

    def test_base_times_working_count_plus_upgrade(self):
        s = GameState(buildings={"automator": 2}, resources={"raw_a": 100})
        self.assertEqual(engine.automation_slots_capacity(s, "automator"), 2)
        engine.purchase_building_upgrade(s, "automator", "automator_slot")
        self.assertEqual(engine.automation_slots_capacity(s, "automator"), 3)

    def test_zero_when_all_copies_broken(self):
        s = GameState(buildings={"automator": 1}, broken={"automator": 1})
        self.assertEqual(engine.automation_slots_capacity(s, "automator"), 0)

    def test_zero_for_building_without_automation_hub(self):
        s = GameState(buildings={"soul_synth": 1})
        self.assertEqual(engine.automation_slots_capacity(s, "soul_synth"), 0)


class EligibilityTests(FixtureTestCase):
    def test_simple_action_is_eligible(self):
        self.assertTrue(engine.is_automation_eligible_action("gather_a"))

    def test_probabilistic_action_is_not_eligible(self):
        self.assertFalse(engine.is_automation_eligible_action("craft_lucky"))
        self.assertFalse(engine.is_automation_eligible_action("craft_risky"))

    def test_range_gain_action_is_not_eligible(self):
        self.assertFalse(engine.is_automation_eligible_action("craft_variable"))

    def test_multiturn_action_is_eligible(self):
        # duration_turns > 1 РАЗРЕШЕНО - do_manual_action сам не даст запустить
        # вторую копию, пока первая не закончится (см. MultiTurnActionAutomationTests
        # ниже), так что автоматизации достаточно обычного отказа ok=False.
        self.assertTrue(engine.is_automation_eligible_action("craft_delayed"))

    def test_unknown_action_is_not_eligible(self):
        self.assertFalse(engine.is_automation_eligible_action("nope"))

    def test_any_existing_energy_recipe_is_eligible(self):
        # У energy_recipes нет cost_chance/gain_chance вовсе, а диапазон в produces
        # (см. make_variable) не мешает - process_automation_rules не занимается
        # плановой бухгалтерией автокрафта, просто вызывает start_energy_recipe.
        self.assertTrue(engine.is_automation_eligible_energy_recipe("make_raw_b"))
        self.assertTrue(engine.is_automation_eligible_energy_recipe("make_variable"))

    def test_unknown_energy_recipe_is_not_eligible(self):
        self.assertFalse(engine.is_automation_eligible_energy_recipe("nope"))

    def test_eligible_targets_list_includes_both_kinds(self):
        targets = engine.automation_eligible_targets()
        by_kind = {(t["kind"], t["id"]) for t in targets}
        self.assertIn(("manual_action", "gather_a"), by_kind)
        self.assertIn(("manual_action", "craft_delayed"), by_kind)
        self.assertNotIn(("manual_action", "craft_lucky"), by_kind)
        self.assertIn(("energy_recipe", "make_raw_b"), by_kind)


class RuleManagementTests(FixtureTestCase):
    def test_add_rejects_building_without_automation_hub(self):
        s = GameState(buildings={"soul_synth": 1})
        result = engine.add_automation_rule(s, "soul_synth", "manual_action", "gather_a", [])
        self.assertFalse(result.ok)
        self.assertEqual(s.automation_rules, {})

    def test_add_rejects_ineligible_action(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(s, "automator", "manual_action", "craft_lucky", [])
        self.assertFalse(result.ok)

    def test_add_rejects_unknown_kind(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(s, "automator", "nope_kind", "gather_a", [])
        self.assertFalse(result.ok)

    def test_add_rejects_unknown_energy_recipe(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(s, "automator", "energy_recipe", "nope", [])
        self.assertFalse(result.ok)

    def test_add_energy_recipe_rule_succeeds(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(s, "automator", "energy_recipe", "make_raw_b", [])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.automation_rules["automator"][0]["kind"], "energy_recipe")
        self.assertEqual(s.automation_rules["automator"][0]["target_id"], "make_raw_b")

    def test_add_rejects_unknown_resource_condition(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "nope", "op": ">=", "amount": 1}],
        )
        self.assertFalse(result.ok)

    def test_add_rejects_bad_operator(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "raw_a", "op": "==", "amount": 1}],
        )
        self.assertFalse(result.ok)

    def test_add_rejects_negative_amount(self):
        s = GameState(buildings={"automator": 1})
        result = engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "raw_a", "op": ">=", "amount": -1}],
        )
        self.assertFalse(result.ok)

    def test_add_succeeds_and_respects_capacity(self):
        s = GameState(buildings={"automator": 1})
        r1 = engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        self.assertTrue(r1.ok, r1.message)
        self.assertEqual(len(s.automation_rules["automator"]), 1)
        r2 = engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        self.assertFalse(r2.ok)  # только 1 слот по умолчанию

    def test_update_rewrites_in_place_without_moving_position(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "automator", "automator_slot")
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "raw_a", "op": ">=", "amount": 1}],
        )
        result = engine.update_automation_rule(s, "automator", 0, "manual_action", "craft_mid", [])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.automation_rules["automator"][0]["target_id"], "craft_mid")
        self.assertEqual(len(s.automation_rules["automator"]), 2)

    def test_update_can_switch_kind_from_manual_action_to_energy_recipe(self):
        s = GameState(buildings={"automator": 1})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        result = engine.update_automation_rule(s, "automator", 0, "energy_recipe", "make_raw_b", [])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.automation_rules["automator"][0]["kind"], "energy_recipe")
        self.assertEqual(s.automation_rules["automator"][0]["target_id"], "make_raw_b")

    def test_update_rejects_out_of_range_index(self):
        s = GameState(buildings={"automator": 1})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        result = engine.update_automation_rule(s, "automator", 5, "manual_action", "gather_a", [])
        self.assertFalse(result.ok)

    def test_remove_pops_rule(self):
        s = GameState(buildings={"automator": 1})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        result = engine.remove_automation_rule(s, "automator", 0)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.automation_rules["automator"], [])

    def test_remove_rejects_out_of_range_index(self):
        s = GameState(buildings={"automator": 1})
        result = engine.remove_automation_rule(s, "automator", 0)
        self.assertFalse(result.ok)

    def test_management_operations_do_not_advance_turn(self):
        s = GameState(buildings={"automator": 1}, turn=5)
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        engine.update_automation_rule(s, "automator", 0, "manual_action", "gather_a", [])
        engine.remove_automation_rule(s, "automator", 0)
        self.assertEqual(s.turn, 5)


class ProcessAutomationRulesTests(FixtureTestCase):
    def test_rule_without_conditions_fires_every_turn(self):
        s = GameState(buildings={"automator": 1})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 3)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 6)

    def test_at_least_condition_gates_firing(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 3})
        engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "raw_a", "op": ">=", "amount": 5}],
        )
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 3)  # условие ложно - не сработало
        s.resources["raw_a"] = 5
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 8)  # условие истинно - сработало (+3)

    def test_at_most_condition(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 10})
        engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "raw_a", "op": "<=", "amount": 5}],
        )
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 10)  # 10 > 5 - не сработало
        s.resources["raw_a"] = 2
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 5)  # 2 <= 5 - сработало (+3)

    def test_multiple_conditions_use_and_semantics(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 10, "raw_b": 0})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [
            {"resource": "raw_a", "op": ">=", "amount": 5},
            {"resource": "raw_b", "op": ">=", "amount": 5},
        ])
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 10)  # raw_b условие ложно
        s.resources["raw_b"] = 5
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 13)  # оба условия истинны

    def test_broken_building_does_not_fire(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 0})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        s.broken["automator"] = 1
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 0)

    def test_missing_other_resource_does_not_crash_and_does_not_fire(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 0})
        engine.add_automation_rule(s, "automator", "manual_action", "craft_mid", [])  # cost raw_a:4, которого нет
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("mid", 0), 0)
        self.assertEqual(s.resources.get("raw_a", 0), 0)

    def test_rule_for_not_yet_unlocked_action_is_silently_skipped_then_fires_after_unlock(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_b": 10})
        engine.add_automation_rule(s, "automator", "manual_action", "locked_action", [])
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("mid", 0), 0)  # ещё не открыто (requires_tech tech1)
        s.techs.add("tech1")
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("mid", 0), 1)

    def test_multiple_rules_share_resource_in_list_order(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "automator", "automator_slot")
        engine.add_automation_rule(s, "automator", "manual_action", "craft_mid", [])
        engine.add_automation_rule(s, "automator", "manual_action", "craft_mid", [])
        engine.advance_turn(s)
        # cost raw_a:4 каждое - оба правила должны были успеть отработать в этот же ход
        # (100 - 2 за покупку слота-апгрейда - 4 - 4 = 90)
        self.assertEqual(s.resources.get("mid", 0), 2)
        self.assertEqual(s.resources.get("raw_a", 0), 90)

    def test_automation_fired_reported_in_production_report(self):
        s = GameState(buildings={"automator": 1})
        engine.add_automation_rule(s, "automator", "manual_action", "gather_a", [])
        report = engine.advance_turn(s)
        self.assertEqual(len(report.automation_fired), 1)
        self.assertEqual(report.automation_fired[0]["building_id"], "automator")
        self.assertEqual(report.automation_fired[0]["kind"], "manual_action")
        self.assertEqual(report.automation_fired[0]["target_id"], "gather_a")


class MultiTurnActionAutomationTests(FixtureTestCase):
    """duration_turns > 1 разрешены в правилах (см. is_automation_eligible_action) -
    do_manual_action сам отказывает повторному запуску, пока job не освободится,
    так что автоматизация просто пропускает ход для такого правила (без очереди,
    без форсированной отмены)."""

    def test_rule_starts_job_and_gain_lands_after_duration(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.add_automation_rule(s, "automator", "manual_action", "craft_delayed", [])
        engine.advance_turn(s)  # ход 1: старт job'а, cost списан сразу
        self.assertIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.resources.get("raw_a", 0), 98)
        self.assertEqual(s.resources.get("delayed_output", 0), 0)
        engine.advance_turn(s)  # ход 2: тикает (turns_done 0 -> 1), ещё не готово
        self.assertEqual(s.resources.get("delayed_output", 0), 0)
        engine.advance_turn(s)  # ход 3: тикает (1 -> 2), ещё не готово
        self.assertEqual(s.resources.get("delayed_output", 0), 0)
        engine.advance_turn(s)  # ход 4: тикает (2 -> 3 == turns_total) - завершилось, gain начислен ОДИН раз
        self.assertEqual(s.resources.get("delayed_output", 0), 5)
        self.assertEqual(s.resources.get("raw_a", 0), 96)  # cost НОВОГО job'а - см. тест ниже про бесшовный перезапуск

    def test_unconditional_rule_seamlessly_restarts_multiturn_job_on_completion(self):
        # _process_manual_jobs (снятие завершённого job'а) идёт РАНЬШЕ
        # process_automation_rules в advance_turn - поэтому правило без условий
        # видит свободный слот и стартует job заново В ТОТ ЖЕ ход, когда
        # предыдущий закончился - без простоя в один ход.
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.add_automation_rule(s, "automator", "manual_action", "craft_delayed", [])
        for _ in range(4):
            engine.advance_turn(s)
        self.assertIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.manual_jobs["craft_delayed"]["turns_done"], 0)  # это уже НОВЫЙ job

    def test_rule_does_not_restart_while_job_is_in_progress(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.add_automation_rule(s, "automator", "manual_action", "craft_delayed", [])
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 98)
        engine.advance_turn(s)  # job уже в процессе - правило просто не срабатывает повторно
        self.assertEqual(s.resources.get("raw_a", 0), 98)

    def test_started_job_is_reported_in_automation_fired(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.add_automation_rule(s, "automator", "manual_action", "craft_delayed", [])
        report = engine.advance_turn(s)
        self.assertEqual(len(report.automation_fired), 1)
        self.assertIn("Начато", report.automation_fired[0]["message"])

    def test_manual_cancel_frees_slot_for_automation_to_restart(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.add_automation_rule(s, "automator", "manual_action", "craft_delayed", [])
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 98)
        result = engine.cancel_manual_job(s, "craft_delayed")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources.get("raw_a", 0), 100)  # cost возвращён целиком
        self.assertNotIn("craft_delayed", s.manual_jobs)
        engine.advance_turn(s)  # слот свободен - правило стартует job заново
        self.assertIn("craft_delayed", s.manual_jobs)
        self.assertEqual(s.resources.get("raw_a", 0), 98)


class EnergyRecipeAutomationTests(FixtureTestCase):
    """kind == "energy_recipe" - правило вызывает start_energy_recipe вместо
    do_manual_action. Архитектурно отдельная от manual_jobs очередь
    (state.energy_jobs) - см. process_automation_rules."""

    def test_rule_starts_energy_job_and_gain_lands_after_threshold(self):
        # energy_converter: max_energy_per_turn=4, make_raw_b: threshold=10 ->
        # 4+4+2 = 3 тика после старта, итого 4 advance_turn (1 старт + 3 тика).
        s = GameState(buildings={"automator": 1, "energy_converter": 1}, resources={"energy": 100})
        engine.add_automation_rule(s, "automator", "energy_recipe", "make_raw_b", [])
        engine.advance_turn(s)  # ход 1: старт job'а (progress=0)
        self.assertEqual(len(s.energy_jobs), 1)
        self.assertEqual(s.energy_jobs[0]["building_id"], "energy_converter")
        self.assertEqual(s.resources.get("raw_b", 0), 0)
        for _ in range(3):
            s.resources["energy"] = 100  # без батарей энергия не копится между ходами
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_b", 0), 1)

    def test_rule_does_not_fire_when_no_free_converter(self):
        s = GameState(buildings={"automator": 1, "energy_converter": 1}, resources={"energy": 100})
        occupied = engine.start_energy_recipe(s, "make_catalyst")
        self.assertTrue(occupied.ok, occupied.message)
        engine.add_automation_rule(s, "automator", "energy_recipe", "make_raw_b", [])
        engine.advance_turn(s)
        # единственный слот уже занят make_catalyst - правило молча не сработало
        self.assertEqual(len(s.energy_jobs), 1)
        self.assertEqual(s.energy_jobs[0]["recipe_id"], "make_catalyst")

    def test_rule_respects_machine_restriction(self):
        # upgrade_matter привязан к matter_generator (machine) - должен занять
        # именно его, даже если energy_converter тоже свободен.
        s = GameState(
            buildings={"automator": 1, "energy_converter": 1, "matter_generator": 1},
            resources={"energy": 100, "raw_a": 20},
        )
        engine.add_automation_rule(s, "automator", "energy_recipe", "upgrade_matter", [])
        engine.advance_turn(s)
        self.assertEqual(len(s.energy_jobs), 1)
        self.assertEqual(s.energy_jobs[0]["building_id"], "matter_generator")

    def test_condition_gates_energy_recipe_firing(self):
        s = GameState(buildings={"automator": 1, "energy_converter": 1}, resources={"energy": 100, "raw_a": 0})
        engine.add_automation_rule(
            s, "automator", "energy_recipe", "make_raw_b", [{"resource": "raw_a", "op": ">=", "amount": 5}],
        )
        engine.advance_turn(s)
        self.assertEqual(s.energy_jobs, [])  # условие ложно - не сработало
        s.resources["raw_a"] = 5
        engine.advance_turn(s)
        self.assertEqual(len(s.energy_jobs), 1)

    def test_recipe_material_consumes_cost_is_paid_on_start(self):
        s = GameState(
            buildings={"automator": 1, "matter_generator": 1}, resources={"energy": 100, "raw_a": 5},
        )
        engine.add_automation_rule(s, "automator", "energy_recipe", "upgrade_matter", [])
        engine.advance_turn(s)
        self.assertEqual(len(s.energy_jobs), 1)
        self.assertEqual(s.resources.get("raw_a", 0), 0)  # consumes.raw_a:5 списан целиком при старте

    def test_recipe_not_yet_researched_is_silently_skipped_then_fires_after_research(self):
        locked_recipe = {**fixtures.ENERGY_RECIPES["make_raw_b"], "requires_tech": "tech1"}
        with mock.patch.dict(engine.ENERGY_RECIPES, {"locked_recipe": locked_recipe}):
            s = GameState(buildings={"automator": 1, "energy_converter": 1}, resources={"energy": 100})
            engine.add_automation_rule(s, "automator", "energy_recipe", "locked_recipe", [])
            engine.advance_turn(s)
            self.assertEqual(s.energy_jobs, [])  # ещё не открыто
            s.techs.add("tech1")
            engine.advance_turn(s)
            self.assertEqual(len(s.energy_jobs), 1)

    def test_started_energy_job_is_reported_in_automation_fired(self):
        s = GameState(buildings={"automator": 1, "energy_converter": 1}, resources={"energy": 100})
        engine.add_automation_rule(s, "automator", "energy_recipe", "make_raw_b", [])
        report = engine.advance_turn(s)
        self.assertEqual(len(report.automation_fired), 1)
        self.assertEqual(report.automation_fired[0]["kind"], "energy_recipe")
        self.assertEqual(report.automation_fired[0]["target_id"], "make_raw_b")
        self.assertIn("Запущен", report.automation_fired[0]["message"])

    def test_manual_cancel_frees_slot_for_automation_to_restart(self):
        s = GameState(buildings={"automator": 1, "energy_converter": 1}, resources={"energy": 100})
        engine.add_automation_rule(s, "automator", "energy_recipe", "make_raw_b", [])
        engine.advance_turn(s)
        self.assertEqual(len(s.energy_jobs), 1)
        result = engine.cancel_energy_job(s, 0)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.energy_jobs, [])
        engine.advance_turn(s)  # слот свободен - правило стартует job заново
        self.assertEqual(len(s.energy_jobs), 1)


class SerializationTests(FixtureTestCase):
    def test_round_trip_preserves_rules(self):
        s = GameState(buildings={"automator": 1})
        engine.add_automation_rule(
            s, "automator", "manual_action", "gather_a", [{"resource": "raw_a", "op": ">=", "amount": 5}],
        )
        restored = GameState.from_dict(s.to_dict())
        self.assertEqual(restored.automation_rules, s.automation_rules)


if __name__ == "__main__":
    unittest.main()
