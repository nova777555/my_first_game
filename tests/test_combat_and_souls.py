"""
Тесты боевой механики (охота на мобов/боссов, generic-навыки, синтезаторы душ) и
repeatable-апгрейдов (game/engine.py). Работают на самодостаточной тестовой
фикстуре (tests/fixtures.py), не на реальном game/content.json.

Запуск: python -m unittest tests.test_combat_and_souls -v
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


class SkillTests(FixtureTestCase):
    def test_level_1_by_default(self):
        s = GameState()
        self.assertEqual(engine.skill_level(s, "combat"), 1)
        self.assertEqual(engine.skill_level_effects(s, "combat"), {"damage": 3})

    def test_level_rises_at_threshold_not_before(self):
        s = GameState(skill_xp={"combat": 9})
        self.assertEqual(engine.skill_level(s, "combat"), 1)
        s.skill_xp["combat"] = 10
        self.assertEqual(engine.skill_level(s, "combat"), 2)
        self.assertEqual(engine.skill_level_effects(s, "combat"), {"damage": 100})

    def test_unknown_skill_defaults_to_level_1_empty_effects(self):
        s = GameState()
        self.assertEqual(engine.skill_level(s, "unknown_skill"), 1)
        self.assertEqual(engine.skill_level_effects(s, "unknown_skill"), {})

    def test_add_skill_xp_accumulates_and_ignores_non_positive(self):
        s = GameState()
        engine.add_skill_xp(s, "combat", 4)
        engine.add_skill_xp(s, "combat", 0)
        engine.add_skill_xp(s, "combat", -5)
        self.assertEqual(s.skill_xp["combat"], 4)

    def test_next_skill_level_info_and_max_level(self):
        s = GameState()
        self.assertEqual(engine.next_skill_level_info(s, "combat")["xp_required"], 10)
        s.skill_xp["combat"] = 10
        self.assertIsNone(engine.next_skill_level_info(s, "combat"))

    def test_skills_view_reports_level_and_progress(self):
        s = GameState(skill_xp={"combat": 10})
        view = engine.skills_view(s)
        entry = next(e for e in view if e["id"] == "combat")
        self.assertEqual(entry["level"], 2)
        self.assertEqual(entry["xp"], 10)
        self.assertIsNone(entry["next_xp_required"])


class SearchAndSummonTests(FixtureTestCase):
    def test_search_mob_any_only_finds_non_boss_and_sets_encounter(self):
        s = GameState()
        result = engine.search_mob_any(s, "hunting_loc")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.active_encounter["mob_id"], "test_mob")
        self.assertEqual(s.active_encounter["remaining_hp"], 10)

    def test_search_mob_any_fails_at_unknown_location(self):
        s = GameState()
        result = engine.search_mob_any(s, "nowhere")
        self.assertFalse(result.ok)
        self.assertIsNone(s.active_encounter)

    def test_summon_boss_rejects_non_boss_id(self):
        s = GameState(resources={"raw_a": 100})
        result = engine.summon_boss(s, "test_mob")
        self.assertFalse(result.ok)
        self.assertIsNone(s.active_encounter)

    def test_summon_boss_pays_cost_and_sets_encounter(self):
        s = GameState(resources={"raw_a": 5})
        result = engine.summon_boss(s, "test_boss")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 0)
        self.assertEqual(s.active_encounter, {"mob_id": "test_boss", "remaining_hp": 50})

    def test_summon_boss_without_resources_fails_and_does_not_set_encounter(self):
        s = GameState()
        result = engine.summon_boss(s, "test_boss")
        self.assertFalse(result.ok)
        self.assertIsNone(s.active_encounter)

    def test_new_search_replaces_unfinished_encounter(self):
        s = GameState()
        s.active_encounter = {"mob_id": "test_boss", "remaining_hp": 1}
        engine.search_mob_any(s, "hunting_loc")
        self.assertEqual(s.active_encounter["mob_id"], "test_mob")

    def test_abandon_encounter_clears_without_cost(self):
        s = GameState(resources={"raw_a": 5})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.abandon_encounter(s)
        self.assertTrue(result.ok)
        self.assertIsNone(s.active_encounter)
        self.assertEqual(s.resources["raw_a"], 5)

    def test_abandon_encounter_fails_when_nothing_active(self):
        s = GameState()
        self.assertFalse(engine.abandon_encounter(s).ok)


class AttackEncounterTests(FixtureTestCase):
    def test_no_active_encounter_fails(self):
        s = GameState()
        self.assertFalse(engine.attack_encounter(s).ok)

    def test_non_lethal_hit_reduces_hp_and_grants_nothing(self):
        s = GameState()
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok)
        # skill level 1 -> damage 3, без меча (тир 1, damage_multiplier по
        # умолчанию 1.0) -> урон 3, моб не убит.
        self.assertEqual(s.active_encounter["remaining_hp"], 7)
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertNotIn("test_soul", s.resources)

    def test_lethal_hit_grants_loot_xp_and_soul_then_clears_encounter(self):
        s = GameState(skill_xp={"combat": 10})  # уровень 2 -> урон 100, гарантированный килл
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok)
        self.assertIsNone(s.active_encounter)
        self.assertEqual(s.resources["raw_a"], 2)  # loot
        self.assertEqual(s.resources["test_soul"], 1)  # soul_chance=1.0
        self.assertEqual(s.skill_xp["combat"], 15)  # 10 + xp_reward(5)

    def test_boss_kill_does_not_grant_soul(self):
        # test_boss hp=50, урон уровня 2 = 100 >= 50 -> убит с первого удара.
        s = GameState(skill_xp={"combat": 10}, resources={"raw_a": 5})
        engine.summon_boss(s, "test_boss")
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(s.active_encounter)
        self.assertEqual(s.resources["raw_b"], 3)  # loot босса
        self.assertNotIn("test_soul", s.resources)  # у боссов нет soul_id вовсе

    def test_weapon_instant_kill_bypasses_damage_calc(self):
        # Меч тир 2: instant_kill_max_hp=8, обычный урон при уровне 1 навыка
        # (damage=3 * damage_multiplier 2 = 6) НЕ хватает на remaining_hp=8 -
        # без instant_kill_max_hp моб бы выжил (6 < 8). Проверяем именно
        # instant-kill ветку: она должна убить, несмотря на недостаточный урон.
        s = GameState(tool_tiers={"sword": 2})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 8}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(s.active_encounter)

    def test_weapon_damage_and_loot_multiplier_applied(self):
        s = GameState(tool_tiers={"sword": 2}, skill_xp={"combat": 10})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        engine.attack_encounter(s)
        # loot_multiplier=2 -> raw_a: 2*2=4
        self.assertEqual(s.resources["raw_a"], 4)

    def test_weapon_soul_chance_multiplier_still_capped_at_one(self):
        s = GameState(tool_tiers={"sword": 2}, skill_xp={"combat": 10})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        engine.attack_encounter(s)
        # soul_chance=1.0 * multiplier 2 -> min(1.0, 2.0) = 1.0, всё ещё гарантия
        self.assertEqual(s.resources["test_soul"], 1)


class SoulCollectorTests(FixtureTestCase):
    def test_capacity_zero_without_building(self):
        s = GameState()
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 0)

    def test_capacity_scales_with_working_copies_and_upgrade(self):
        s = GameState(buildings={"soul_synth": 2}, resources={"raw_a": 100})
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 2)
        engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_slot")
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 3)

    def test_capacity_ignores_broken_copies(self):
        s = GameState(buildings={"soul_synth": 2}, broken={"soul_synth": 1})
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 1)

    def test_insert_rejects_unaccepted_soul(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_b": 1})
        result = engine.insert_soul(s, "soul_synth", "raw_b")
        self.assertFalse(result.ok)

    def test_insert_rejects_without_stock(self):
        s = GameState(buildings={"soul_synth": 1})
        result = engine.insert_soul(s, "soul_synth", "test_soul")
        self.assertFalse(result.ok)

    def test_insert_rejects_when_no_free_slot(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 2})
        engine.insert_soul(s, "soul_synth", "test_soul")
        result = engine.insert_soul(s, "soul_synth", "test_soul")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["test_soul"], 1)

    def test_insert_and_remove_round_trip(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        engine.insert_soul(s, "soul_synth", "test_soul")
        self.assertEqual(s.resources["test_soul"], 0)
        self.assertEqual(s.soul_slots["soul_synth"], ["test_soul"])
        result = engine.remove_soul(s, "soul_synth", 0)
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["test_soul"], 1)
        self.assertEqual(s.soul_slots["soul_synth"], [])

    def test_soul_is_eternal_and_keeps_producing_across_turns(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        engine.insert_soul(s, "soul_synth", "test_soul")
        with mock.patch("random.random", return_value=0.9):  # chance 0.5 fails
            engine.advance_turn(s)
            engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 6)  # 3/ход * 2 хода, гарантированная часть
        self.assertEqual(s.soul_slots["soul_synth"], ["test_soul"])  # душа никуда не делась

    def test_soul_production_chance_part_and_efficiency_upgrade(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1, "raw_a": 100})
        engine.insert_soul(s, "soul_synth", "test_soul")
        engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_eff")  # -5 raw_a -> 95
        with mock.patch("random.random", return_value=0.1):  # chance 0.5 succeeds
            engine.advance_turn(s)
        # (raw_a: 3, raw_b: 1) * efficiency(2) = raw_a +6, raw_b +2
        self.assertEqual(s.resources["raw_a"], 101)
        self.assertEqual(s.resources["raw_b"], 2)

    def test_souls_beyond_capacity_do_not_produce(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 2})
        engine.insert_soul(s, "soul_synth", "test_soul")
        s.soul_slots["soul_synth"].append("test_soul")  # обходим лимит слотов напрямую
        with mock.patch("random.random", return_value=0.9):
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 3)  # только 1 (capacity) из 2 произвёл


class RepeatableUpgradeTests(FixtureTestCase):
    def test_non_repeatable_upgrade_rejects_second_purchase(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_eff")
        result = engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_eff")
        self.assertFalse(result.ok)
        self.assertEqual(engine.building_upgrade_purchase_count(s, "soul_synth", "soul_synth_eff"), 1)

    def test_repeatable_building_upgrade_stacks_value_and_cost_grows(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_a": 100})
        r1 = engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_slot")
        self.assertTrue(r1.ok, r1.message)
        self.assertEqual(s.resources["raw_a"], 98)  # cost 2 * growth^0 = 2
        r2 = engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_slot")
        self.assertTrue(r2.ok, r2.message)
        self.assertEqual(s.resources["raw_a"], 94)  # cost 2 * growth^1 = 4
        self.assertEqual(engine.building_upgrade_purchase_count(s, "soul_synth", "soul_synth_slot"), 2)
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 3)  # base 1 + 2*1

    def test_resolve_action_cost_reflects_current_repeatable_price(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "soul_synth", "soul_synth_slot")
        cost = engine.resolve_action_cost(s, "building_upgrade", "soul_synth:soul_synth_slot")
        self.assertEqual(cost, {"raw_a": 4})

    def test_repeatable_farm_instance_upgrade_stacks_per_instance(self):
        s = GameState(buildings={"apiary": 1}, resources={"raw_a": 100})
        engine._ensure_farm_instances(s)
        instance_id = next(iter(s.farm_instances))
        engine.purchase_farm_instance_upgrade(s, instance_id, "apiary_slot_bonus")
        engine.purchase_farm_instance_upgrade(s, instance_id, "apiary_slot_bonus")
        self.assertEqual(engine.farm_instance_upgrade_purchase_count(s, instance_id, "apiary_slot_bonus"), 2)
        self.assertEqual(engine.farm_instance_slot_capacity(s, instance_id), 4)  # base 2 + 2*1
        # cost: 2 (growth^0) + 4 (growth^1) = 6
        self.assertEqual(s.resources["raw_a"], 94)

    def test_repeatable_upgrade_does_not_leak_into_other_instance(self):
        s = GameState(buildings={"apiary": 2}, resources={"raw_a": 100})
        engine._ensure_farm_instances(s)
        ids = list(s.farm_instances)
        engine.purchase_farm_instance_upgrade(s, ids[0], "apiary_slot_bonus")
        self.assertEqual(engine.farm_instance_slot_capacity(s, ids[0]), 3)
        self.assertEqual(engine.farm_instance_slot_capacity(s, ids[1]), 2)


if __name__ == "__main__":
    unittest.main()
