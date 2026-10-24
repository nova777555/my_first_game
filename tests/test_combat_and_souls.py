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
        self.assertEqual(engine.skill_level_effects(s, "combat"), {"damage": 100, "max_hp": 15})

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


class MobKilledQuestTests(FixtureTestCase):
    """state.mobs_killed и условие квеста mob_killed: только факт «добит хоть раз»."""

    QUEST = {
        "name": "Первая кровь", "description": "Добей моба и поставь постройку.", "era": 1,
        "conditions": [
            {"type": "mob_killed", "mob": "test_mob"},
            {"type": "total_buildings_at_least", "amount": 1},
        ],
        "reward_case": "test_case",
    }

    def test_only_the_finishing_blow_marks_the_mob(self):
        s = GameState()
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        engine.attack_encounter(s)  # урон 3 - моб жив
        self.assertEqual(s.mobs_killed, set())
        s.skill_xp["combat"] = 10
        engine.attack_encounter(s)
        self.assertEqual(s.mobs_killed, {"test_mob"})

    def test_boss_kill_is_marked_too(self):
        s = GameState(skill_xp={"combat": 10}, resources={"raw_a": 5})
        engine.summon_boss(s, "test_boss")
        engine.attack_encounter(s)
        self.assertEqual(s.mobs_killed, {"test_boss"})

    def test_retreat_or_abandon_does_not_count(self):
        s = GameState(player_hp=1)
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        self.assertTrue(engine.attack_encounter(s).combat["defeated"])
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        engine.abandon_encounter(s)
        self.assertEqual(s.mobs_killed, set())

    def test_quest_needs_that_mob_and_every_other_condition(self):
        s = GameState(buildings={"gen": 1})
        with mock.patch.dict(engine.QUESTS, {"first_blood": self.QUEST}):
            s.mobs_killed.add("test_boss")  # другой моб не считается
            self.assertNotIn("first_blood", engine.check_quests(s))
            s.mobs_killed.add("test_mob")
            s.buildings.clear()  # условия через И - без постройки не выполнен
            self.assertNotIn("first_blood", engine.check_quests(s))
            s.buildings["gen"] = 1
            self.assertIn("first_blood", engine.check_quests(s))

    def test_save_round_trip_and_old_save_without_field(self):
        s = GameState(mobs_killed={"test_mob", "test_boss"})
        self.assertEqual(GameState.from_dict(s.to_dict()).mobs_killed, {"test_mob", "test_boss"})
        self.assertEqual(GameState.from_dict({}).mobs_killed, set())


class AttackPreviewTests(FixtureTestCase):
    """engine.attack_preview - точный прогноз удара для UI (штриховка на полосах HP,
    число на кнопке приёма). Тот же _strike, что у attack_encounter, поэтому здесь
    проверяется главное: прогноз совпадает с реальным ударом и ничего не меняет в state.
    Фикстура: test_mob hp=10, damage=4; навык ур.1 = урон 3; меч тир 3 = ×3; броня тир 2 =
    −2 ответки, +10 HP."""

    def _encounter(self, s, hp=10, mob="test_mob"):
        s.active_encounter = {"mob_id": mob, "remaining_hp": hp}

    def test_none_without_encounter(self):
        self.assertIsNone(engine.attack_preview(GameState()))

    def test_none_for_locked_move(self):
        s = GameState(tool_tiers={"sword": 2})
        self._encounter(s)
        self.assertIsNone(engine.attack_preview(s, "power_strike"))

    def test_matches_real_non_lethal_hit(self):
        s = GameState(tool_tiers={"sword": 3, "armor": 2})
        self._encounter(s)
        preview = engine.attack_preview(s)
        result = engine.attack_encounter(s)
        self.assertFalse(preview["killed"])
        self.assertEqual(preview["damage"], result.combat["damage"])
        self.assertEqual(preview["hp_after"], s.active_encounter["remaining_hp"])
        self.assertEqual(preview["retaliation"], result.combat["player_damage_taken"])
        self.assertEqual(preview["player_hp_after"], result.combat["player_hp"])

    def test_lethal_preview_reports_remaining_hp_and_no_retaliation(self):
        s = GameState(tool_tiers={"sword": 3})
        self._encounter(s)
        preview = engine.attack_preview(s, "power_strike")  # 3 * 3 * 2 = 18 >= 10
        self.assertTrue(preview["killed"])
        self.assertEqual(preview["damage"], 10)
        self.assertEqual(preview["hp_after"], 0)
        self.assertEqual(preview["retaliation"], 0)

    def test_execute_below_threshold_is_forced_kill(self):
        s = GameState(tool_tiers={"sword": 3})
        self._encounter(s, hp=2)  # порог 0.2 * 10 = 2
        preview = engine.attack_preview(s, "execute_move")
        self.assertTrue(preview["forced_kill"])
        self.assertTrue(preview["instant_kill"])

    def test_defeated_when_retaliation_drops_player_to_zero(self):
        s = GameState(player_hp=3)
        self._encounter(s)
        preview = engine.attack_preview(s)
        self.assertTrue(preview["defeated"])
        self.assertTrue(engine.attack_encounter(s).combat["defeated"])

    def test_preview_does_not_consume_durability(self):
        with mock.patch.dict(engine.TOOLS["sword"]["tiers"][2], {"max_durability": 5}):
            s = GameState(tool_tiers={"sword": 3})
            self._encounter(s)
            engine.attack_preview(s)
            engine.attack_preview(s, "power_strike")
            self.assertEqual(s.tool_durability, {})
            engine.attack_encounter(s)
            self.assertEqual(s.tool_durability, {"sword": 4})

    def test_armor_breaking_on_this_hit_no_longer_softens_it(self):
        # Броня с последней единицей прочности ломается на ЭТОЙ ответке и уже не
        # снижает её (порядок attack_encounter) - прогноз обязан сказать то же самое.
        with mock.patch.dict(engine.TOOLS["armor"]["tiers"][1], {"max_durability": 3}):
            s = GameState(tool_tiers={"armor": 2}, tool_durability={"armor": 1})
            self._encounter(s)
            preview = engine.attack_preview(s)
            self.assertEqual(s.tool_durability, {"armor": 1})
            self.assertEqual(preview["retaliation"], 4)
            result = engine.attack_encounter(s)
            self.assertEqual(result.combat["player_damage_taken"], 4)
            self.assertEqual(preview["player_hp_after"], result.combat["player_hp"])


class QteDamageBonusTests(FixtureTestCase):
    """Попадание в QTE-кольцо (qte_hit) - бонус к урону combat.qte_damage_bonus
    (engine._move_damage). Фикстура: навык ур.1 = урон 3, test_mob hp=10."""

    def _encounter(self, s, hp=10):
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": hp}

    def test_without_bonus_qte_changes_nothing(self):
        s = GameState()
        self._encounter(s)
        self.assertEqual(engine.qte_damage_bonus(), 0.0)
        self.assertEqual(engine.attack_preview(s, qte_hit=True)["damage"], engine.attack_preview(s)["damage"])

    def test_bonus_applies_to_basic_attack_and_rounds_up(self):
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            s = GameState()
            self._encounter(s)
            combat = engine.attack_encounter(s, qte_hit=True).combat
        self.assertEqual(combat["damage"], 5)  # 3 * 1.5 = 4.5 -> 5
        self.assertTrue(combat["qte_hit"])

    def test_bonus_applies_to_percent_modes(self):
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            s = GameState(tool_tiers={"sword": 3})
            self._encounter(s)
            self.assertEqual(engine.attack_preview(s, "pct_max_move")["damage"], 2)  # 20% от 10
            self.assertEqual(engine.attack_preview(s, "pct_max_move", qte_hit=True)["damage"], 3)

    def test_execute_below_threshold_is_unaffected(self):
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            s = GameState(tool_tiers={"sword": 3})
            self._encounter(s, hp=2)
            preview = engine.attack_preview(s, "execute_move", qte_hit=True)
        self.assertTrue(preview["forced_kill"])
        self.assertEqual(preview["damage"], 2)

    def test_qte_can_turn_a_hit_into_a_kill_and_skip_retaliation(self):
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            s = GameState()
            self._encounter(s, hp=5)  # обычный удар 3 - не убивает, с QTE 5 - убивает
            self.assertFalse(engine.attack_preview(s)["killed"])
            preview = engine.attack_preview(s, qte_hit=True)
            self.assertTrue(preview["killed"])
            self.assertEqual(preview["retaliation"], 0)
            self.assertTrue(engine.attack_encounter(s, qte_hit=True).combat["killed"])

    def test_damage_ceil_ignores_float_noise(self):
        self.assertEqual(engine._ceil_damage(10 * 1.1), 11)
        self.assertEqual(engine._ceil_damage(4.5), 5)
        self.assertEqual(engine._ceil_damage(0.2), 1)


class CombatPacketTests(FixtureTestCase):
    """ActionResult.combat - данные для анимации и экрана «Добыча» в UI."""

    def test_kill_packet_carries_loot_soul_xp_and_level_up(self):
        s = GameState(skill_xp={"combat": 5})  # ур. 1; +5 опыта -> ровно порог ур. 2
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 3}  # урон ур. 1 = 3
        combat = engine.attack_encounter(s).combat
        self.assertTrue(combat["killed"])
        self.assertEqual(combat["mob_id"], "test_mob")
        self.assertEqual(combat["loot"], {"raw_a": 2})
        self.assertEqual(combat["soul"], "test_soul")
        self.assertEqual(combat["xp"], 5)
        self.assertEqual((combat["skill_level_before"], combat["skill_level_after"]), (1, 2))

    def test_non_lethal_packet_reports_armor_absorbed_and_move(self):
        s = GameState(tool_tiers={"sword": 3, "armor": 2})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 50}
        combat = engine.attack_encounter(s, "pct_max_move").combat
        self.assertEqual(combat["move_id"], "pct_max_move")
        self.assertEqual(combat["armor_absorbed"], 2)
        self.assertEqual(combat["player_damage_taken"], 2)

    def test_defeat_packet_carries_wounded_turns(self):
        s = GameState(player_hp=1)
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        combat = engine.attack_encounter(s).combat
        self.assertTrue(combat["defeated"])
        self.assertEqual(combat["wounded_turns"], 5)


class HuntingViewTests(unittest.TestCase):
    """tools/web_ui.py: hunting_view - прогнозы, снаряжение и визуал из контента."""

    def setUp(self):
        import web_ui
        self.web_ui = web_ui
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def test_no_predictions_without_encounter(self):
        view = self.web_ui.hunting_view(GameState())
        self.assertIsNone(view["attack_prediction"])
        self.assertEqual(view["predictions"], {})

    def test_predictions_for_unlocked_moves_only(self):
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        view = self.web_ui.hunting_view(s)
        prediction = dict(view["attack_prediction"])
        self.assertIsNone(prediction.pop("qte"))  # бонуса за QTE в фикстуре нет
        self.assertEqual(prediction, engine.attack_preview(s))
        self.assertEqual(set(view["predictions"]), engine.unlocked_attack_moves(s))

    def test_predictions_carry_qte_variant_when_bonus_is_set(self):
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            s = GameState()
            s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
            view = self.web_ui.hunting_view(s)
        self.assertEqual(view["qte_damage_bonus"], 0.5)
        self.assertEqual(view["attack_prediction"]["damage"], 3)
        self.assertEqual(view["attack_prediction"]["qte"]["damage"], 5)

    def test_run_turn_passes_qte_to_attack(self):
        with mock.patch.dict(engine.COMBAT_SETTINGS, {"qte_damage_bonus": 0.5}):
            s = GameState()
            s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
            log = self.web_ui.run_turn(s, "hunt_attack", None, qte_hit=True)
        self.assertTrue(log["combat"]["qte_hit"])
        self.assertEqual(log["combat"]["damage"], 5)

    def test_combat_fx_and_hunt_scene_passed_through_from_content(self):
        fx = {"attack_style": "slam", "scale": 6, "attack_lines": ["Бьёт"]}
        scene = {"backdrop": "cave", "ambient": "embers"}
        with mock.patch.dict(engine.MOBS["test_mob"], {"combat_fx": fx}), \
                mock.patch.dict(self.web_ui.LOCATIONS["hunting_loc"], {"hunt_scene": scene}):
            s = GameState()
            s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
            encounter = self.web_ui.hunting_view(s)["active_encounter"]
        self.assertEqual(encounter["fx"], fx)
        self.assertEqual(encounter["scene"], scene)

    def test_encounter_without_fx_gets_empty_dicts(self):
        s = GameState()
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        encounter = self.web_ui.hunting_view(s)["active_encounter"]
        self.assertEqual((encounter["fx"], encounter["scene"]), ({}, {}))

    def test_gear_lists_forged_weapons_and_armor_only(self):
        with mock.patch.dict(engine.TOOLS["sword"]["tiers"][2], {"slash_color": "#aabbcc"}):
            view = self.web_ui.hunting_view(GameState(tool_tiers={"sword": 3, "armor": 2}))
            gear = view["gear"]
            self.assertEqual([w["tool_id"] for w in gear["weapons"]], ["sword"])
            self.assertEqual(gear["weapons"][0]["slash_color"], "#aabbcc")
            self.assertEqual([a["tool_id"] for a in gear["armor"]], ["armor"])
            self.assertEqual(gear["damage_reduction"], 2)
            empty = self.web_ui.hunting_view(GameState())["gear"]
            self.assertEqual((empty["weapons"], empty["armor"]), ([], []))

    def test_moves_carry_fx_and_icon_fields(self):
        with mock.patch.dict(engine.COMBAT_MOVES["power_strike"], {"fx": {"style": "heavy"}}):
            moves = {m["id"]: m for m in self.web_ui.hunting_view(GameState())["combat_moves"]}
        self.assertEqual(moves["power_strike"]["fx"], {"style": "heavy"})
        self.assertEqual(moves["pct_max_move"]["fx"], {})
        self.assertIsNone(moves["pct_max_move"]["icon"])


class CombatMovesTests(FixtureTestCase):
    def test_no_moves_unlocked_without_weapon_tier3(self):
        s = GameState(tool_tiers={"sword": 2})
        self.assertEqual(engine.unlocked_attack_moves(s), set())

    def test_move_unlocked_at_tier3_and_stays_unlocked_higher(self):
        s = GameState(tool_tiers={"sword": 3})
        self.assertEqual(
            engine.unlocked_attack_moves(s),
            {"power_strike", "pct_max_move", "pct_remaining_move", "execute_move"},
        )

    def test_attack_with_unknown_move_fails_without_consuming_turn(self):
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s, move_id="no_such_move")
        self.assertFalse(result.ok)
        self.assertEqual(s.active_encounter["remaining_hp"], 10)

    def test_attack_with_locked_move_fails(self):
        # sword тир 2 - power_strike требует тир 3, ещё не открыт.
        s = GameState(tool_tiers={"sword": 2})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s, move_id="power_strike")
        self.assertFalse(result.ok)
        self.assertEqual(s.active_encounter["remaining_hp"], 10)

    def test_move_multiplies_damage_and_sets_cooldown(self):
        # Тир 3: damage_multiplier=3 (у _weapon_bonuses), skill level 1 damage=3
        # -> обычный урон 9. С приёмом (multiplier x2) -> 18.
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}
        result = engine.attack_encounter(s, move_id="power_strike")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["damage"], 18)
        self.assertEqual(s.active_encounter["remaining_hp"], 82)
        self.assertEqual(s.move_cooldowns["power_strike"], 2)

    def test_move_on_cooldown_is_rejected(self):
        s = GameState(tool_tiers={"sword": 3}, move_cooldowns={"power_strike": 1})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}
        result = engine.attack_encounter(s, move_id="power_strike")
        self.assertFalse(result.ok)
        self.assertEqual(s.active_encounter["remaining_hp"], 100)

    def test_cooldown_ticks_down_every_turn_and_clears_at_zero(self):
        s = GameState(move_cooldowns={"power_strike": 2})
        engine.advance_turn(s)
        self.assertEqual(s.move_cooldowns["power_strike"], 1)
        engine.advance_turn(s)
        self.assertNotIn("power_strike", s.move_cooldowns)

    def test_basic_attack_without_move_id_is_unaffected(self):
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 100}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["damage"], 9)
        self.assertNotIn("power_strike", s.move_cooldowns)

    def test_percent_max_hp_move_ignores_skill_and_weapon(self):
        # test_boss hp=50, percent=0.2 -> 10 урона, НЕЗАВИСИМО от навыка/оружия
        # (skill level 1 damage=3, тир3 меча damage_multiplier=3 - обычный расчёт
        # дал бы 9, а не 10, если бы percent_max_hp их учитывал).
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_boss", "remaining_hp": 50}
        result = engine.attack_encounter(s, move_id="pct_max_move")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["damage"], 10)
        self.assertEqual(s.active_encounter["remaining_hp"], 40)

    def test_percent_remaining_hp_move_scales_with_current_not_max(self):
        # test_boss hp=50, но remaining_hp уже подсажен до 20 - percent=0.5 от
        # ОСТАВШЕГОСЯ (20), а не от максимума (50) -> 10, не 25.
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_boss", "remaining_hp": 20}
        result = engine.attack_encounter(s, move_id="pct_remaining_move")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.combat["damage"], 10)
        self.assertEqual(s.active_encounter["remaining_hp"], 10)

    def test_execute_move_kills_at_or_below_threshold(self):
        # test_boss hp=50, execute_threshold_percent=0.2 -> порог 10. remaining_hp=10
        # РОВНО на пороге ("не выше" - включительно) - должен добить, несмотря на то,
        # что обычный урон (навык 3 * оружие 3 = 9) сам по себе НЕ убил бы (9 < 10).
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_boss", "remaining_hp": 10}
        result = engine.attack_encounter(s, move_id="execute_move")
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(s.active_encounter)
        self.assertTrue(result.combat["instant_kill"])
        self.assertEqual(result.combat["damage"], 10)

    def test_execute_move_deals_fallback_damage_above_threshold(self):
        # Выше порога (remaining_hp=50 > 10) - execute_move бьёт как обычная атака
        # (fallback damage_multiplier по умолчанию 1.0, приём не задавал его явно):
        # навык 3 * оружие 3 * 1.0 = 9.
        s = GameState(tool_tiers={"sword": 3})
        s.active_encounter = {"mob_id": "test_boss", "remaining_hp": 50}
        result = engine.attack_encounter(s, move_id="execute_move")
        self.assertTrue(result.ok, result.message)
        self.assertFalse(result.combat["killed"])
        self.assertEqual(result.combat["damage"], 9)
        self.assertEqual(s.active_encounter["remaining_hp"], 41)


class PlayerHpAndArmorTests(FixtureTestCase):
    """HP игрока/броня/дебафф "Ранен" (game/engine.py:player_max_hp/
    current_player_hp/is_wounded/_armor_bonuses, attack_encounter - ответный
    удар моба). fixtures.COMBAT_SETTINGS: player_base_hp=20, hp_regen_per_turn=3,
    wounded_turns=5, wounded_damage_multiplier=0.5. fixtures.MOBS: test_mob.damage=4,
    test_boss.damage=15. fixtures.SKILLS["combat"] level 2 (xp>=10) добавляет
    max_hp=15. fixtures.TOOLS["armor"] тир 2: max_hp_bonus=10, damage_reduction=2."""

    def test_player_max_hp_is_base_by_default(self):
        s = GameState()
        self.assertEqual(engine.player_max_hp(s), 20)

    def test_player_max_hp_adds_skill_bonus(self):
        s = GameState(skill_xp={"combat": 10})  # уровень 2
        self.assertEqual(engine.player_max_hp(s), 35)

    def test_player_max_hp_adds_armor_bonus(self):
        s = GameState(tool_tiers={"armor": 2})
        self.assertEqual(engine.player_max_hp(s), 30)

    def test_player_max_hp_combines_skill_and_armor(self):
        s = GameState(skill_xp={"combat": 10}, tool_tiers={"armor": 2})
        self.assertEqual(engine.player_max_hp(s), 45)

    def test_current_player_hp_defaults_to_max_when_none(self):
        s = GameState()
        self.assertIsNone(s.player_hp)
        self.assertEqual(engine.current_player_hp(s), 20)

    def test_current_player_hp_clamps_into_valid_range(self):
        s = GameState(player_hp=999)
        self.assertEqual(engine.current_player_hp(s), 20)
        s2 = GameState(player_hp=-5)
        self.assertEqual(engine.current_player_hp(s2), 0)

    def test_is_wounded_false_without_debuff(self):
        s = GameState()
        self.assertFalse(engine.is_wounded(s))

    def test_is_wounded_true_while_turn_within_deadline(self):
        s = GameState(wounded_until_turn=1)  # state.turn по умолчанию 1
        self.assertTrue(engine.is_wounded(s))

    def test_non_lethal_hit_deals_retaliation_damage(self):
        s = GameState()
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertFalse(result.combat["killed"])
        self.assertEqual(result.combat["player_damage_taken"], 4)
        self.assertEqual(result.combat["player_hp"], 16)
        self.assertEqual(result.combat["player_max_hp"], 20)
        self.assertFalse(result.combat["defeated"])
        self.assertEqual(s.player_hp, 16)

    def test_armor_reduces_retaliation_damage(self):
        s = GameState(tool_tiers={"armor": 2})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        # damage_reduction=2 -> 4 - 2 = 2, а не 4 без брони.
        self.assertEqual(result.combat["player_damage_taken"], 2)
        self.assertEqual(s.player_hp, 28)  # max_hp 30 (база 20 + броня 10) - 2

    def test_killing_blow_grants_no_retaliation(self):
        # Уровень навыка 2 -> урон 100, test_mob (hp=10) убит одним ударом -
        # ответка не считается вовсе, player_hp/state.player_hp не трогаются.
        s = GameState(skill_xp={"combat": 10})
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.combat["killed"])
        self.assertEqual(result.combat["player_damage_taken"], 0)
        self.assertFalse(result.combat["defeated"])
        self.assertEqual(result.combat["player_max_hp"], 35)  # база 20 + навык 15
        self.assertEqual(result.combat["player_hp"], 35)
        self.assertIsNone(s.player_hp)

    def test_instant_kill_grants_no_retaliation(self):
        s = GameState(tool_tiers={"sword": 2})  # instant_kill_max_hp=8
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 8}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.combat["instant_kill"])
        self.assertEqual(result.combat["player_damage_taken"], 0)
        self.assertIsNone(s.player_hp)

    def test_defeat_at_zero_hp_ends_encounter_and_applies_wounded_debuff(self):
        s = GameState(player_hp=3)
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.combat["defeated"])
        self.assertEqual(result.combat["player_hp"], 0)
        self.assertIsNone(s.active_encounter)
        self.assertEqual(s.player_hp, 0)
        self.assertEqual(s.wounded_until_turn, s.turn + 5)
        self.assertIn("Ранен", result.message)
        # Поражение - НЕ убийство: лут/опыт/душа не выданы.
        self.assertEqual(s.resources.get("raw_a", 0), 0)
        self.assertNotIn("test_soul", s.resources)

    def test_search_mob_any_blocked_at_zero_hp(self):
        s = GameState(player_hp=0)
        result = engine.search_mob_any(s, "hunting_loc")
        self.assertFalse(result.ok)
        self.assertIsNone(s.active_encounter)

    def test_summon_boss_blocked_at_zero_hp_before_paying_cost(self):
        s = GameState(player_hp=0, resources={"raw_a": 5})
        result = engine.summon_boss(s, "test_boss")
        self.assertFalse(result.ok)
        self.assertIsNone(s.active_encounter)
        self.assertEqual(s.resources["raw_a"], 5)  # цена НЕ списана

    def test_wounded_debuff_halves_player_damage_output(self):
        s = GameState(wounded_until_turn=1)  # state.turn=1 по умолчанию -> ранен
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 10}
        result = engine.attack_encounter(s)
        self.assertTrue(result.ok, result.message)
        # Обычный урон 3 (навык 1) * 0.5 -> ceil(1.5) = 2, а не 3.
        self.assertEqual(result.combat["damage"], 2)

    def test_process_combat_regenerates_hp_only_outside_encounter(self):
        s = GameState(player_hp=10)
        engine.advance_turn(s)
        self.assertEqual(s.player_hp, 13)  # +3/ход вне схватки
        s.active_encounter = {"mob_id": "test_mob", "remaining_hp": 5}
        engine.advance_turn(s)
        self.assertEqual(s.player_hp, 13)  # не регенерирует во время схватки

    def test_process_combat_clears_wounded_debuff_once_turn_passes(self):
        s = GameState(wounded_until_turn=2)
        engine.advance_turn(s)
        self.assertEqual(s.turn, 2)
        self.assertTrue(engine.is_wounded(s))
        self.assertEqual(s.wounded_until_turn, 2)
        engine.advance_turn(s)
        self.assertEqual(s.turn, 3)
        self.assertIsNone(s.wounded_until_turn)
        self.assertFalse(engine.is_wounded(s))


class BossTurnLimitTests(FixtureTestCase):
    def test_summon_sets_deadline_when_turn_limit_present(self):
        s = GameState(resources={"raw_a": 5})
        engine.summon_boss(s, "test_boss")
        # test_boss (фикстура) не имеет turn_limit - дедлайна нет.
        self.assertNotIn("deadline_turn", s.active_encounter)

    def test_boss_flees_after_deadline_turn_passes(self):
        s = GameState(resources={"raw_a": 5})
        engine.summon_boss(s, "test_boss")
        # Проставляем дедлайн руками (в фикстуре у test_boss нет turn_limit) -
        # проверяем именно механику _process_combat, не контент.
        s.active_encounter["deadline_turn"] = s.turn + 1
        report = engine.advance_turn(s)
        self.assertIsNone(report.boss_fled)
        self.assertIsNotNone(s.active_encounter)
        report = engine.advance_turn(s)
        self.assertEqual(report.boss_fled, "Тестовый босс")
        self.assertIsNone(s.active_encounter)

    def test_boss_does_not_flee_before_deadline(self):
        s = GameState(resources={"raw_a": 5})
        engine.summon_boss(s, "test_boss")
        s.active_encounter["deadline_turn"] = s.turn + 5
        for _ in range(4):
            engine.advance_turn(s)
        self.assertIsNotNone(s.active_encounter)

    def test_killing_boss_before_deadline_leaves_no_trace(self):
        s = GameState(resources={"raw_a": 5}, skill_xp={"combat": 10})
        engine.summon_boss(s, "test_boss")
        s.active_encounter["deadline_turn"] = s.turn + 5
        engine.attack_encounter(s)  # урон уровня 2 = 100 >= 50, убит сразу
        self.assertIsNone(s.active_encounter)
        report = engine.advance_turn(s)
        self.assertIsNone(report.boss_fled)


class SoulCollectorTests(FixtureTestCase):
    """С перехода на per-instance слоты (2026-09-18, см. state.py:
    soul_collector_instances) синтезатор душ - зеркало пасек: у каждого
    ФИЗИЧЕСКОГО экземпляра свой пул слотов/апгрейдов, insert_soul/remove_soul
    берут instance_id (как house_creature/release_creature - farm_instance_id),
    не id типа постройки. _instance(s) - хелпер, достающий id единственного
    экземпляра soul_synth в фикстуре (мирроит паттерн
    test_repeatable_farm_instance_upgrade_stacks_per_instance)."""

    def _instance(self, s):
        engine._ensure_soul_collector_instances(s)
        return next(iter(s.soul_collector_instances))

    def test_capacity_zero_without_building(self):
        s = GameState()
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 0)

    def test_deferred_soul_instance_upgrade_is_registered_in_pending_retry(self):
        # Регрессия: тот же баг, что у reactor_instance_upgrade (см.
        # tests/test_reactor.py) - таблица выполнения заказов (_ORDER_RETRY) не знала
        # kind "soul_instance_upgrade" (см. purchase_soul_collector_instance_upgrade),
        # заказ по готовности падал бы KeyError.
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_a": 100})
        instance_id = self._instance(s)
        s.orders.append({
            "id": 1, "kind": "soul_instance_upgrade", "target_id": f"{instance_id}:soul_synth_slot", "label": "улучшение (тест)",
            "reserved": {}, "steps": [], "energy_reserve": 0, "final_energy": 0, "status": None, "waits_energy": False,
        })
        report = engine.ProductionReport()
        engine._process_orders(s, report, {})  # не должно бросить KeyError
        self.assertIn("soul_synth_slot", s.soul_collector_instances[instance_id]["upgrades"])
        self.assertEqual(s.orders, [])
        self.assertIn("улучшение (тест)", report.pending_finished)

    def test_capacity_scales_with_working_copies_and_upgrade(self):
        s = GameState(buildings={"soul_synth": 2}, resources={"raw_a": 100})
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 2)
        engine._ensure_soul_collector_instances(s)
        instance_id = next(iter(s.soul_collector_instances))
        engine.purchase_soul_collector_instance_upgrade(s, instance_id, "soul_synth_slot")
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 3)
        # Апгрейд куплен только на ОДИН экземпляр - у второго capacity не изменилась.
        self.assertEqual(engine.soul_collector_instance_capacity(s, instance_id), 2)

    def test_capacity_ignores_broken_copies(self):
        s = GameState(buildings={"soul_synth": 2}, broken={"soul_synth": 1})
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 1)

    def test_capacity_zero_when_all_broken_even_with_upgrade(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_a": 100, "test_soul": 1})
        instance_id = self._instance(s)
        engine.insert_soul(s, instance_id, "test_soul")
        engine.purchase_soul_collector_instance_upgrade(s, instance_id, "soul_synth_slot")
        s.broken["soul_synth"] = 1
        s.soul_collector_instances[instance_id]["broken"] = True
        self.assertEqual(engine.soul_collector_capacity(s, "soul_synth"), 0)
        with mock.patch("random.random", return_value=0.9):  # chance 0.5 fails
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 98)  # 100 - 2 (апгрейд); сломанный синтезатор не производит

    def test_insert_rejects_unaccepted_soul(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"raw_b": 1})
        result = engine.insert_soul(s, self._instance(s), "raw_b")
        self.assertFalse(result.ok)

    def test_insert_rejects_without_stock(self):
        s = GameState(buildings={"soul_synth": 1})
        result = engine.insert_soul(s, self._instance(s), "test_soul")
        self.assertFalse(result.ok)

    def test_insert_rejects_when_no_free_slot(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 2})
        instance_id = self._instance(s)
        engine.insert_soul(s, instance_id, "test_soul")
        result = engine.insert_soul(s, instance_id, "test_soul")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["test_soul"], 1)

    def test_insert_and_remove_round_trip(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        instance_id = self._instance(s)
        engine.insert_soul(s, instance_id, "test_soul")
        self.assertEqual(s.resources["test_soul"], 0)
        self.assertEqual(s.soul_collector_instances[instance_id]["installed"], ["test_soul"])
        result = engine.remove_soul(s, instance_id, 0)
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["test_soul"], 1)
        self.assertEqual(s.soul_collector_instances[instance_id]["installed"], [])

    def test_soul_is_eternal_and_keeps_producing_across_turns(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        instance_id = self._instance(s)
        engine.insert_soul(s, instance_id, "test_soul")
        with mock.patch("random.random", return_value=0.9):  # chance 0.5 fails
            engine.advance_turn(s)
            engine.advance_turn(s)
        self.assertEqual(s.resources["raw_a"], 6)  # 3/ход * 2 хода, гарантированная часть
        self.assertEqual(s.soul_collector_instances[instance_id]["installed"], ["test_soul"])  # душа никуда не делась

    def test_soul_production_chance_part_and_efficiency_upgrade(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1, "raw_a": 100})
        instance_id = self._instance(s)
        engine.insert_soul(s, instance_id, "test_soul")
        engine.purchase_soul_collector_instance_upgrade(s, instance_id, "soul_synth_eff")  # -5 raw_a -> 95
        with mock.patch("random.random", return_value=0.1):  # chance 0.5 succeeds
            engine.advance_turn(s)
        # (raw_a: 3, raw_b: 1) * efficiency(2) = raw_a +6, raw_b +2
        self.assertEqual(s.resources["raw_a"], 101)
        self.assertEqual(s.resources["raw_b"], 2)

    def test_souls_beyond_capacity_do_not_produce(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 2})
        instance_id = self._instance(s)
        engine.insert_soul(s, instance_id, "test_soul")
        s.soul_collector_instances[instance_id]["installed"].append("test_soul")  # обходим лимит слотов напрямую
        with mock.patch("random.random", return_value=0.9):
            engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 3)  # только 1 (capacity) из 2 произвёл

    def test_upgrade_does_not_leak_into_other_instance(self):
        s = GameState(buildings={"soul_synth": 2}, resources={"raw_a": 100})
        engine._ensure_soul_collector_instances(s)
        ids = list(s.soul_collector_instances)
        engine.purchase_soul_collector_instance_upgrade(s, ids[0], "soul_synth_slot")
        self.assertEqual(engine.soul_collector_instance_capacity(s, ids[0]), 2)
        self.assertEqual(engine.soul_collector_instance_capacity(s, ids[1]), 1)

    def test_old_save_migrates_legacy_soul_slots_into_instances(self):
        # Имитация сохранения СТАРШЕ перехода на per-instance слоты (2026-09-18,
        # см. CLAUDE.md/state.py:soul_collector_instances) - state.soul_slots
        # хранит души ПО ТИПУ постройки, state.soul_collector_instances ещё пуст.
        # _ensure_soul_collector_instances обязана разложить их round-robin по
        # новосозданным экземплярам, ничего не потеряв.
        s = GameState(buildings={"soul_synth": 2}, soul_slots={"soul_synth": ["test_soul", "test_soul"]})
        engine._ensure_soul_collector_instances(s)
        self.assertEqual(len(s.soul_collector_instances), 2)
        installed_total = sum(len(inst["installed"]) for inst in s.soul_collector_instances.values())
        self.assertEqual(installed_total, 2)
        self.assertTrue(all(inst["installed"] == ["test_soul"] for inst in s.soul_collector_instances.values()))
        self.assertEqual(s.soul_slots["soul_synth"], [])


class RepeatableUpgradeTests(FixtureTestCase):
    """Апгрейды soul_collector переехали на per-instance (см. SoulCollectorTests
    выше) - эти тесты проверяют ОБЩУЮ механику purchase_building_upgrade/
    repeatable_upgrade_cost (по-прежнему ПО ТИПУ постройки для построек без
    физических экземпляров), поэтому переведены на energy_converter/automator -
    те же самые числа cost/growth, что были у soul_synth_eff/soul_synth_slot."""

    def test_non_repeatable_upgrade_rejects_second_purchase(self):
        s = GameState(buildings={"energy_converter": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "energy_converter", "conv_cap_1")
        result = engine.purchase_building_upgrade(s, "energy_converter", "conv_cap_1")
        self.assertFalse(result.ok)
        self.assertEqual(engine.building_upgrade_purchase_count(s, "energy_converter", "conv_cap_1"), 1)

    def test_repeatable_building_upgrade_stacks_value_and_cost_grows(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        r1 = engine.purchase_building_upgrade(s, "automator", "automator_slot")
        self.assertTrue(r1.ok, r1.message)
        self.assertEqual(s.resources["raw_a"], 98)  # cost 2 * growth^0 = 2
        r2 = engine.purchase_building_upgrade(s, "automator", "automator_slot")
        self.assertTrue(r2.ok, r2.message)
        self.assertEqual(s.resources["raw_a"], 94)  # cost 2 * growth^1 = 4
        self.assertEqual(engine.building_upgrade_purchase_count(s, "automator", "automator_slot"), 2)
        self.assertEqual(engine.automation_slots_capacity(s, "automator"), 3)  # base 1 + 2*1

    def test_resolve_action_cost_reflects_current_repeatable_price(self):
        s = GameState(buildings={"automator": 1}, resources={"raw_a": 100})
        engine.purchase_building_upgrade(s, "automator", "automator_slot")
        cost = engine.resolve_action_cost(s, "building_upgrade", "automator:automator_slot")
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
