"""
Тесты стола зачарований (game/engine.py: enchant_tool/enchantment_pool/
_enchant_effect_value) и расхода прочности кирки/оружия/брони (уже была
инфраструктура в движке - tool_durability_remaining/_consume_tool_durability,
тут проверяются реальные точки вызова: hit_mine_field/attack_encounter).
Работают на самодостаточной тестовой фикстуре (tests/fixtures.py), не на
реальном game/content.json.

Запуск: python -m unittest tests.test_enchanting -v
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


def _blank_mine_field(size: int, overrides: dict[tuple[int, int], dict] | None = None) -> dict:
    cells = [
        {"resource": None, "hp": 1, "hp_max": 1, "broken": False, "revealed": False}
        for _ in range(size * size)
    ]
    for (r, c), patch in (overrides or {}).items():
        cells[r * size + c].update(patch)
    return {"size": size, "cells": cells}


class EnchantmentPoolTests(FixtureTestCase):
    def test_pool_for_mining_tool_includes_kind_specific_and_universal(self):
        pool = engine.enchantment_pool("pickaxe")
        self.assertEqual({p["enchant_id"] for p in pool}, {"test_luck", "test_durability"})
        # test_luck - 2 уровня, test_durability - 1 уровень (универсальное) = 3 символа.
        self.assertEqual(len(pool), 3)

    def test_pool_for_weapon_tool_excludes_armor_only_enchants(self):
        pool = engine.enchantment_pool("sword")
        self.assertEqual(
            {p["enchant_id"] for p in pool},
            {"test_sharpness", "test_looting", "test_necromancy", "test_durability"},
        )

    def test_pool_for_armor_tool(self):
        pool = engine.enchantment_pool("armor")
        self.assertEqual({p["enchant_id"] for p in pool}, {"test_protection", "test_vitality", "test_durability"})

    def test_pool_empty_for_tool_without_enchant_kind(self):
        self.assertEqual(engine.enchantment_pool("drill"), [])

    def test_enchantable_tools_lists_only_tagged_tools(self):
        self.assertEqual(set(engine.enchantable_tools()), {"pickaxe", "sword", "armor"})


class EnchantingTableAvailabilityTests(FixtureTestCase):
    def test_unavailable_without_building(self):
        self.assertFalse(engine.enchanting_table_available(GameState()))

    def test_available_with_a_working_copy(self):
        self.assertTrue(engine.enchanting_table_available(GameState(buildings={"ench_table": 1})))

    def test_unavailable_when_all_copies_broken(self):
        s = GameState(buildings={"ench_table": 1}, broken={"ench_table": 1})
        self.assertFalse(engine.enchanting_table_available(s))


class EnchantToolTests(FixtureTestCase):
    def test_fails_without_a_working_table(self):
        result = engine.enchant_tool(GameState(resources={"raw_a": 10}), "pickaxe")
        self.assertFalse(result.ok)

    def test_fails_on_a_tool_without_enchant_kind(self):
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 10})
        result = engine.enchant_tool(s, "drill")
        self.assertFalse(result.ok)

    def test_fails_without_enough_cost_and_does_not_touch_resources(self):
        s = GameState(buildings={"ench_table": 1})
        result = engine.enchant_tool(s, "pickaxe")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources, {})

    def test_success_pays_cost_and_applies_the_matched_enchantment(self):
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5})
        pick = {"enchant_id": "test_luck", "level": 1, "value": 2.0}
        with mock.patch("random.choice", return_value=pick):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["raw_a"], 4)
        self.assertEqual(s.tool_enchantments["pickaxe"], {"test_luck": 1})
        self.assertTrue(result.enchant["success"])
        self.assertEqual(result.enchant["applied"], {"enchant_id": "test_luck", "level": 1})
        self.assertEqual(len(result.enchant["circles"]), 3)

    def test_success_never_downgrades_an_already_stronger_level(self):
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5},
                       tool_enchantments={"pickaxe": {"test_luck": 2}})
        pick = {"enchant_id": "test_luck", "level": 1, "value": 2.0}
        with mock.patch("random.choice", return_value=pick):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertTrue(result.enchant["success"])
        self.assertEqual(s.tool_enchantments["pickaxe"], {"test_luck": 2})

    def test_matching_a_weaker_enchantment_wins_over_a_stronger_owned_one(self):
        # Совпадение решает КАКОЕ зачарование сработало - "сильнейшее побеждает"
        # касается только УРОВНЯ ТОГО ЖЕ зачарования, не выбора между разными id.
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5},
                       tool_enchantments={"pickaxe": {"test_durability": 1}})
        pick = {"enchant_id": "test_luck", "level": 2, "value": 3.0}
        with mock.patch("random.choice", return_value=pick):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertEqual(s.tool_enchantments["pickaxe"], {"test_durability": 1, "test_luck": 2})

    def test_failure_clears_every_enchantment_on_that_tool(self):
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5},
                       tool_enchantments={"pickaxe": {"test_luck": 2, "test_durability": 1}})
        picks = [
            {"enchant_id": "test_luck", "level": 1, "value": 2.0},
            {"enchant_id": "test_luck", "level": 2, "value": 3.0},
            {"enchant_id": "test_durability", "level": 1, "value": 2.0},
        ]
        with mock.patch("random.choice", side_effect=picks):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertFalse(result.enchant["success"])
        self.assertIsNone(result.enchant["applied"])
        self.assertNotIn("pickaxe", s.tool_enchantments)
        # Цена попытки списывается независимо от исхода.
        self.assertEqual(s.resources["raw_a"], 4)

    def test_failure_on_a_tool_with_no_prior_enchantments_is_a_plain_message(self):
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5})
        picks = [
            {"enchant_id": "test_luck", "level": 1, "value": 2.0},
            {"enchant_id": "test_luck", "level": 2, "value": 3.0},
            {"enchant_id": "test_durability", "level": 1, "value": 2.0},
        ]
        with mock.patch("random.choice", side_effect=picks):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertTrue(result.ok)
        self.assertNotIn("pickaxe", s.tool_enchantments)

    def test_failure_keeps_enchantments_when_strip_on_fail_is_disabled_in_content(self):
        # strip_on_fail - настройка КОНТЕНТА (buildings.*.enchanting_table), не
        # игрока, см. engine.enchanting_table_strip_on_fail.
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5},
                       tool_enchantments={"pickaxe": {"test_luck": 2, "test_durability": 1}})
        picks = [
            {"enchant_id": "test_luck", "level": 1, "value": 2.0},
            {"enchant_id": "test_luck", "level": 2, "value": 3.0},
            {"enchant_id": "test_durability", "level": 1, "value": 2.0},
        ]
        with mock.patch.dict(engine.BUILDINGS["ench_table"]["enchanting_table"], {"strip_on_fail": False}), \
                mock.patch("random.choice", side_effect=picks):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertFalse(result.enchant["success"])
        # Щадящий режим - провал ничего не даёт, но и ничего не отбирает.
        self.assertEqual(s.tool_enchantments["pickaxe"], {"test_luck": 2, "test_durability": 1})
        # Цена попытки всё равно списывается - смягчён только сам исход провала.
        self.assertEqual(s.resources["raw_a"], 4)

    def test_success_still_takes_the_max_level_when_strip_on_fail_is_disabled_in_content(self):
        # Щадящий режим не трогает ветку удачи - "сильнейшее побеждает" по-прежнему
        # действует между текущим и выпавшим уровнем.
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5},
                       tool_enchantments={"pickaxe": {"test_luck": 2}})
        pick = {"enchant_id": "test_luck", "level": 1, "value": 2.0}
        with mock.patch.dict(engine.BUILDINGS["ench_table"]["enchanting_table"], {"strip_on_fail": False}), \
                mock.patch("random.choice", return_value=pick):
            result = engine.enchant_tool(s, "pickaxe")
        self.assertTrue(result.enchant["success"])
        self.assertEqual(s.tool_enchantments["pickaxe"], {"test_luck": 2})

    def test_does_not_advance_the_turn(self):
        s = GameState(buildings={"ench_table": 1}, resources={"raw_a": 5}, turn=7)
        pick = {"enchant_id": "test_luck", "level": 1, "value": 2.0}
        with mock.patch("random.choice", return_value=pick):
            engine.enchant_tool(s, "pickaxe")
        self.assertEqual(s.turn, 7)


class EnchantingTableStripOnFailContentTests(FixtureTestCase):
    def test_defaults_to_true_when_the_building_config_omits_the_key(self):
        s = GameState(buildings={"ench_table": 1})
        self.assertTrue(engine.enchanting_table_strip_on_fail(s))

    def test_true_when_no_table_is_working_at_all(self):
        self.assertTrue(engine.enchanting_table_strip_on_fail(GameState()))

    def test_reads_false_from_the_building_config(self):
        s = GameState(buildings={"ench_table": 1})
        with mock.patch.dict(engine.BUILDINGS["ench_table"]["enchanting_table"], {"strip_on_fail": False}):
            self.assertFalse(engine.enchanting_table_strip_on_fail(s))


class DurabilityEnchantTests(FixtureTestCase):
    def test_durability_enchant_multiplies_max_and_rounds_up(self):
        s = GameState(tool_tiers={"wrench": 2})  # max_durability=2 на этом тире
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 2)
        s.tool_enchantments["wrench"] = {"test_durability": 1}  # value=2.0
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 4)

    def test_durability_enchant_does_not_retroactively_rescale_spent_durability(self):
        s = GameState(tool_tiers={"wrench": 2}, tool_durability={"wrench": 0}, resources={"raw_a": 1})
        s.tool_enchantments["wrench"] = {"test_durability": 1}
        # Уже потраченный остаток (сломан, 0) - абсолютное число, зачарование не
        # пересчитывает его задним числом, растёт только "потолок", на который
        # инструмент вернётся СЛЕДУЮЩИМ полным ремонтом.
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 0)
        self.assertTrue(engine.repair_tool(s, "wrench").ok)
        self.assertEqual(engine.tool_durability_remaining(s, "wrench"), 4)


class MiningDurabilityAndLuckTests(FixtureTestCase):
    def test_broken_pickaxe_blocks_the_hit_entirely(self):
        s = GameState(tool_durability={"pickaxe": 0})
        s.active_mine_field = _blank_mine_field(5, {(2, 2): {"broken": True, "revealed": True}})
        with mock.patch.dict(engine.TOOLS["pickaxe"]["tiers"][0], {"max_durability": 3, "repair_cost": {"raw_a": 1}}):
            result = engine.hit_mine_field(s, 2, 3)
        self.assertFalse(result.ok)
        cell = s.active_mine_field["cells"][engine._mine_field_idx(5, 2, 3)]
        self.assertFalse(cell["broken"])

    def test_durability_is_spent_once_per_swing_not_per_broken_cell(self):
        # Тир 3: area=2 - один удар ломает блок 2x2 (несколько клеток разом, см.
        # tests/test_engine.py::test_area_swing_can_reach_into_fog_if_block_edge_touches_broken),
        # прочность всё равно должна упасть РОВНО на 1.
        s = GameState(tool_tiers={"pickaxe": 3})
        s.active_mine_field = _blank_mine_field(6, {(2, 2): {"broken": True, "revealed": True}})
        with mock.patch.dict(engine.TOOLS["pickaxe"]["tiers"][2], {"max_durability": 10, "repair_cost": {"raw_a": 1}}):
            result = engine.hit_mine_field(s, 3, 2)
        self.assertTrue(result.ok, result.message)
        far_corner = s.active_mine_field["cells"][engine._mine_field_idx(6, 4, 3)]
        self.assertTrue(far_corner["broken"])  # действительно задело несколько клеток
        self.assertEqual(s.tool_durability["pickaxe"], 9)

    def test_luck_enchant_multiplies_ore_yield(self):
        s = GameState(tool_enchantments={"pickaxe": {"test_luck": 1}})  # value=2.0
        s.active_mine_field = _blank_mine_field(3, {
            (1, 0): {"broken": True, "revealed": True},
            (1, 1): {"resource": "mineral", "hp": 2, "hp_max": 2},
        })
        result = engine.hit_mine_field(s, 1, 1)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources.get("mineral"), 8)  # base_yield 4 * 2.0


class WeaponAndArmorEnchantTests(FixtureTestCase):
    def test_sharpness_boosts_damage_multiplier_of_the_enchanted_weapon(self):
        # sword тир 2: damage_multiplier=2, skill 1: damage=3 -> база 6.
        # test_sharpness value=0.5 -> multiplier 2*(1+0.5)=3 -> 9.
        s = GameState(tool_tiers={"sword": 2}, tool_enchantments={"sword": {"test_sharpness": 1}})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["damage"], 9)

    def test_looting_boosts_loot_chance_of_the_enchanted_weapon(self):
        s = GameState(tool_tiers={"sword": 2}, tool_enchantments={"sword": {"test_looting": 1}})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 1}  # гарантированное убийство
        patched_mob = {**engine.MOBS["test_mob"], "loot_chance": {"raw_a": 0.4}}
        # test_looting value=2.0 -> эффективный шанс 0.8; бросок 0.7 проходит с
        # усилением и провалился бы без него (0.7 >= 0.4).
        with mock.patch.dict(engine.MOBS, {"test_mob": patched_mob}), mock.patch("random.random", return_value=0.7):
            result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertIn("raw_a", s.resources)

    def test_necromancy_stacks_with_looting_on_soul_chance_only(self):
        # test_necromancy (value=1.5) множит ТОЛЬКО soul_chance, дополнительно к
        # test_looting (value=2.0, множит и soul_chance, и loot_chance) - оба
        # зачарования на одном мече должны перемножиться на soul_chance.
        s = GameState(
            tool_tiers={"sword": 2},
            tool_enchantments={"sword": {"test_looting": 1, "test_necromancy": 1}},
        )
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 1}
        patched_mob = {**engine.MOBS["test_mob"], "soul_chance": 0.1}
        # эффективный шанс: 0.1 (моб) * 2.0 (тир меча) * 2.0 (Добыча) * 1.5 (Некромантия) = 0.6,
        # без Некромантии было бы 0.4 - бросок 0.5 различает эти два случая.
        with mock.patch.dict(engine.MOBS, {"test_mob": patched_mob}), mock.patch("random.random", return_value=0.5):
            result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertIn("test_soul", s.resources)

    def test_protection_enchant_adds_to_armor_damage_reduction(self):
        # Без оружия (тир 1 "Без меча") - обычный урон игрока = 3, недостаточно,
        # чтобы убить remaining_hp=100, значит моб бьёт в ответ. armor тир 2:
        # damage_reduction=2, test_mob.damage=4 -> без зачарования ответка 2.
        # test_protection +5 -> reduction=7 -> ответка max(0, 4-7)=0.
        s = GameState(tool_tiers={"armor": 2}, tool_enchantments={"armor": {"test_protection": 1}})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["player_damage_taken"], 0)

    def test_vitality_enchant_adds_to_player_max_hp(self):
        s = GameState(tool_tiers={"armor": 2}, tool_enchantments={"armor": {"test_vitality": 1}})
        self.assertEqual(engine.player_max_hp(s), 20 + 10 + 7)  # база + броня + живучесть

    def test_weapon_durability_is_spent_on_every_hit_kill_or_not(self):
        s = GameState(tool_tiers={"sword": 2})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}  # не убьёт
        with mock.patch.dict(engine.TOOLS["sword"]["tiers"][1], {"max_durability": 5, "repair_cost": {"raw_a": 1}}):
            engine.attack_encounter(s)
        self.assertEqual(s.tool_durability["sword"], 4)

    def test_armor_durability_is_spent_only_when_the_player_actually_takes_a_hit(self):
        with mock.patch.dict(engine.TOOLS["armor"]["tiers"][1], {"max_durability": 5, "repair_cost": {"raw_a": 1}}):
            killed = GameState(tool_tiers={"armor": 2})
            killed.active_encounter = {"mob_id": "test_mob", "remaining_hp": 1}  # умрёт с одного удара
            engine.attack_encounter(killed)
            self.assertNotIn("armor", killed.tool_durability)

            survives = GameState(tool_tiers={"armor": 2})
            survives.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}  # не умрёт, бьёт в ответ
            engine.attack_encounter(survives)
            self.assertEqual(survives.tool_durability["armor"], 4)

    def test_broken_weapon_bonuses_are_excluded_but_the_attack_still_lands(self):
        s = GameState(tool_tiers={"sword": 2}, tool_durability={"sword": 0})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}
        with mock.patch.dict(engine.TOOLS["sword"]["tiers"][1], {"max_durability": 5, "repair_cost": {"raw_a": 1}}):
            result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["damage"], 3)  # только навык, без damage_multiplier сломанного меча

    def test_broken_armor_bonuses_are_excluded(self):
        s = GameState(tool_tiers={"armor": 2}, tool_durability={"armor": 0})
        with mock.patch.dict(engine.TOOLS["armor"]["tiers"][1], {"max_durability": 5, "repair_cost": {"raw_a": 1}}):
            self.assertEqual(engine.player_max_hp(s), 20)  # без бонуса сломанной брони


class ToolEnchantmentsStateRoundTripTests(unittest.TestCase):
    def test_tool_enchantments_survive_to_dict_from_dict(self):
        s = GameState(tool_enchantments={"pickaxe": {"luck": 2}, "sword": {"sharpness": 1}})
        restored = GameState.from_dict(s.to_dict())
        self.assertEqual(restored.tool_enchantments, {"pickaxe": {"luck": 2}, "sword": {"sharpness": 1}})

    def test_missing_key_defaults_to_empty(self):
        restored = GameState.from_dict({})
        self.assertEqual(restored.tool_enchantments, {})


if __name__ == "__main__":
    unittest.main()
