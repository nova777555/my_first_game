"""
Тесты валидатора content.json (tools/content_validation.py) - того же самого кода,
что использует и редактор перед сохранением.

RealContentTests сознательно проверяет настоящий game/content.json (это их смысл -
убедиться, что реальный контент проекта прямо сейчас валиден) и не завязан ни на
один конкретный id. Остальные классы проверяют ПРАВИЛА валидатора и работают на
tests/fixtures.py - независимом наборе данных, так что переименование/удаление
сущностей в реальной игре на них не влияет. Ничего не пишет на диск.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import content_validation as cv  # noqa: E402
import fixtures  # noqa: E402

CONTENT_PATH = ROOT / "game" / "content.json"


def load_real_content() -> dict:
    with open(CONTENT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class RealContentTests(unittest.TestCase):
    """Реальный content.json проекта должен всегда проходить валидацию без ошибок."""

    def test_current_content_is_valid(self):
        data = load_real_content()
        errors = cv.validate(data)
        self.assertEqual(errors, [])

    def test_current_content_has_no_tech_cycles(self):
        data = load_real_content()
        self.assertEqual(cv.find_requires_cycle(data["techs"]), [])


class BrokenReferenceTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_rejects_unknown_tech_reference(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["requires_tech"] = "made_up_tech"
        errors = cv.validate(d)
        self.assertTrue(any("made_up_tech" in e for e in errors))

    def test_rejects_unknown_resource_in_cost(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["cost"] = {"unobtainium": 5}
        errors = cv.validate(d)
        self.assertTrue(any("unobtainium" in e for e in errors))

    def test_rejects_bad_id_format(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"]["Bad Id!"] = d["buildings"].pop(any_building)
        errors = cv.validate(d)
        self.assertTrue(any("snake_case" in e for e in errors))

    def test_rejects_self_referencing_tech(self):
        d = copy.deepcopy(self.data)
        any_tech = next(iter(d["techs"]))
        d["techs"][any_tech]["requires"] = [any_tech]
        errors = cv.validate(d)
        self.assertTrue(any("сама себя" in e for e in errors))

    def test_rejects_tech_cycle(self):
        d = copy.deepcopy(self.data)
        ids = list(d["techs"].keys())
        self.assertGreaterEqual(len(ids), 3, "фикстуре нужно хотя бы 3 технологии для этого теста")
        a, b, c = ids[:3]
        d["techs"][a]["requires"] = list(set(d["techs"][a]["requires"]) | {c})
        d["techs"][b]["requires"] = list(set(d["techs"][b]["requires"]) | {a})
        d["techs"][c]["requires"] = list(set(d["techs"][c]["requires"]) | {b})
        errors = cv.validate(d)
        self.assertTrue(any("Цикл" in e for e in errors))
        cyclic = cv.find_requires_cycle(d["techs"])
        self.assertEqual(set(cyclic), {a, b, c})

    def test_rejects_effect_pointing_at_unknown_building(self):
        d = copy.deepcopy(self.data)
        any_tech = next(iter(d["techs"]))
        d["techs"][any_tech]["effects"] = [{"building": "no_such_building", "produces": {}}]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_building" in e for e in errors))

    def test_rejects_building_requires_building_self_reference(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["requires_building"] = any_building
        errors = cv.validate(d)
        self.assertTrue(any("саму себя" in e for e in errors))

    def test_rejects_building_requires_building_pointing_at_unknown_building(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["requires_building"] = "no_such_building"
        errors = cv.validate(d)
        self.assertTrue(any("no_such_building" in e for e in errors))

    def test_rejects_building_requires_building_cycle(self):
        d = copy.deepcopy(self.data)
        ids = list(d["buildings"].keys())
        self.assertGreaterEqual(len(ids), 2, "фикстуре нужно хотя бы 2 постройки для этого теста")
        a, b = ids[0], ids[1]
        d["buildings"][a]["requires_building"] = b
        d["buildings"][b]["requires_building"] = a
        errors = cv.validate(d)
        self.assertTrue(any("Цикл" in e and "requires_building" in e for e in errors))
        cyclic = cv.find_building_requires_cycle(d["buildings"])
        self.assertEqual(set(cyclic), {a, b})

    def test_rejects_energy_converter_capacity_on_building_without_energy_converter(self):
        d = copy.deepcopy(self.data)
        any_building = next(b for b, v in self.data["buildings"].items() if not v.get("energy_converter"))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_cap", "name": "x", "description": "x", "type": "energy_converter_capacity",
             "value": 10, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("energy_converter_capacity" in e and "energy_converter" in e for e in errors))

    def test_accepts_energy_converter_capacity_on_a_converter(self):
        d = copy.deepcopy(self.data)
        converter = next(b for b, v in self.data["buildings"].items() if v.get("energy_converter"))
        d["buildings"][converter]["upgrades"] = [
            {"id": "up_cap", "name": "x", "description": "x", "type": "energy_converter_capacity",
             "value": 10, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_non_positive_energy_converter_capacity_value(self):
        d = copy.deepcopy(self.data)
        converter = next(b for b, v in self.data["buildings"].items() if v.get("energy_converter"))
        d["buildings"][converter]["upgrades"] = [
            {"id": "up_cap", "name": "x", "description": "x", "type": "energy_converter_capacity",
             "value": 0, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("energy_converter_capacity" in e for e in errors))

    def test_rejects_non_integer_energy_priority(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["energy_priority"] = "high"
        errors = cv.validate(d)
        self.assertTrue(any("energy_priority" in e for e in errors))

    def test_rejects_energy_recipe_machine_pointing_at_unknown_building(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["upgrade_matter"]["machine"] = "no_such_building"
        errors = cv.validate(d)
        self.assertTrue(any("no_such_building" in e for e in errors))

    def test_accepts_energy_recipe_machine_pointing_at_a_real_building(self):
        d = copy.deepcopy(self.data)
        self.assertEqual(cv.validate(d), [])
        self.assertEqual(d["energy_recipes"]["upgrade_matter"]["machine"], "matter_generator")

    def test_rejects_energy_recipe_consumes_referencing_unknown_resource(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["upgrade_matter"]["consumes"] = {"unobtainium": 5}
        errors = cv.validate(d)
        self.assertTrue(any("unobtainium" in e for e in errors))

    def test_rejects_tool_tier_1_with_nonzero_cost(self):
        d = copy.deepcopy(self.data)
        any_resource = d["resources"][0]["id"]
        d["tools"]["pickaxe"]["tiers"][0]["cost"] = {any_resource: 5}
        errors = cv.validate(d)
        self.assertTrue(any("тир №1" in e and "пустую cost" in e for e in errors))

    def test_rejects_mining_rarity_out_of_range(self):
        d = copy.deepcopy(self.data)
        any_resource = d["resources"][0]["id"]
        d["mining_info"][any_resource] = {
            "requires_tech": None, "required_tool": "pickaxe", "required_tier": 1,
            "rarity": 0, "vein_size_range": [1, 2], "base_yield": 1,
        }
        errors = cv.validate(d)
        self.assertTrue(any("rarity" in e for e in errors))

    def test_rejects_mining_required_tool_pointing_at_unknown_tool(self):
        d = copy.deepcopy(self.data)
        any_resource = d["resources"][0]["id"]
        d["mining_info"][any_resource] = {
            "requires_tech": None, "required_tool": "no_such_tool", "required_tier": 1,
            "rarity": 1, "vein_size_range": [1, 2], "base_yield": 1,
        }
        errors = cv.validate(d)
        self.assertTrue(any("no_such_tool" in e for e in errors))

    def test_rejects_mining_required_tier_out_of_range(self):
        d = copy.deepcopy(self.data)
        any_resource = d["resources"][0]["id"]
        tier_count = len(d["tools"]["pickaxe"]["tiers"])
        d["mining_info"][any_resource] = {
            "requires_tech": None, "required_tool": "pickaxe", "required_tier": tier_count + 1,
            "rarity": 1, "vein_size_range": [1, 2], "base_yield": 1,
        }
        errors = cv.validate(d)
        self.assertTrue(any("required_tier" in e for e in errors))

    def test_rejects_malformed_mining_vein_icon(self):
        # mining_info.*.icon - как ЖИЛА выглядит на поле, отдельно от icon самого
        # ресурса (icons.resources); проверяется тем же check_icon, что и везде.
        d = copy.deepcopy(self.data)
        any_resource = d["resources"][0]["id"]
        d["mining_info"][any_resource] = {
            "requires_tech": None, "required_tool": "pickaxe", "required_tier": 1,
            "rarity": 1, "toughness": 1, "field_cluster_size": [1, 2], "base_yield": 1,
            "icon": {"grid": ["A"]},  # нет palette
        }
        errors = cv.validate(d)
        self.assertTrue(any("icon" in e for e in errors))

    def test_rejects_requires_tool_tiers_pointing_at_unknown_tool(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["requires_tool_tiers"] = {"no_such_tool": [1]}
        errors = cv.validate(d)
        self.assertTrue(any("no_such_tool" in e for e in errors))

    def test_rejects_requires_tool_tiers_with_empty_tier_list(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["requires_tool_tiers"] = {"pickaxe": []}
        errors = cv.validate(d)
        self.assertTrue(any("непустым списком тиров" in e for e in errors))

    def test_rejects_requires_tool_tiers_with_tier_out_of_range(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        tier_count = len(d["tools"]["pickaxe"]["tiers"])
        d["manual_actions"][any_action]["requires_tool_tiers"] = {"pickaxe": [tier_count + 1]}
        errors = cv.validate(d)
        self.assertTrue(any("вне диапазона" in e for e in errors))

    def test_rejects_id_reused_across_categories(self):
        # постройка не может взять id, уже занятый ручным действием (и т.п.) -
        # иначе один из двух объектов был бы неотличим/затёрт при создании через GUI.
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_action] = d["buildings"].pop(any_building)
        errors = cv.validate(d)
        self.assertTrue(any(any_action in e and "уникальны" in e for e in errors))

    def test_same_category_reuse_still_fine(self):
        # sanity: обычный валидный контент не должен ложно триггерить кросс-категорийную проверку
        d = copy.deepcopy(self.data)
        errors = cv.validate(d)
        self.assertFalse(any("уникальны во всём проекте" in e for e in errors))


class QuestValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_fixture_quests_and_loot_cases_are_valid(self):
        errors = cv.validate(self.data)
        self.assertEqual(errors, [])

    def test_rejects_unknown_condition_type_in_quest(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [{"type": "made_up_condition"}]
        errors = cv.validate(d)
        self.assertTrue(any("made_up_condition" in e for e in errors))

    def test_rejects_condition_missing_required_field(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [{"type": "turn_at_least"}]  # нет amount
        errors = cv.validate(d)
        self.assertTrue(any("amount" in e for e in errors))

    def test_rejects_condition_referencing_unknown_building(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_count_at_least", "building": "no_such_building", "amount": 1}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_building" in e for e in errors))

    def test_rejects_quest_without_conditions(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = []
        errors = cv.validate(d)
        self.assertTrue(any(any_q in e and "условие" in e for e in errors))

    def test_rejects_tool_tier_at_least_referencing_unknown_tool(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "tool_tier_at_least", "tool": "no_such_tool", "amount": 2}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_tool" in e for e in errors))

    def test_accepts_tool_tier_at_least_with_known_tool(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "tool_tier_at_least", "tool": "pickaxe", "amount": 2}
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_manual_action_performed_at_least_referencing_unknown_action(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "manual_action_performed_at_least", "action": "no_such_action", "amount": 1}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_action" in e for e in errors))

    def test_accepts_manual_action_performed_at_least_with_known_action(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        any_action = next(iter(d["manual_actions"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "manual_action_performed_at_least", "action": any_action, "amount": 1}
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_species_discovered_referencing_unknown_species(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "species_discovered", "species": "no_such_species"}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_species" in e for e in errors))

    def test_rejects_building_upgrade_purchased_referencing_unknown_upgrade(self):
        # Постройка известна, но у НЕЁ конкретно такого улучшения нет (ошибка
        # отдельная от generic "неизвестная постройка" - см. content_validation.py).
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        any_building = next(iter(d["buildings"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_upgrade_purchased", "building": any_building, "upgrade": "no_such_upgrade"}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_upgrade" in e for e in errors))

    def test_accepts_building_upgrade_purchased_with_known_upgrade(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_upgrade_purchased", "building": "flaky_typed", "upgrade": "flaky_typed_immune"}
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_building_upgrade_level_at_least_with_known_upgrade(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_upgrade_level_at_least", "building": "apiary", "upgrade": "apiary_slot_bonus", "amount": 2}
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_building_upgrade_level_at_least_referencing_unknown_upgrade(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        any_building = next(iter(d["buildings"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_upgrade_level_at_least", "building": any_building, "upgrade": "no_such_upgrade", "amount": 1}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_upgrade" in e for e in errors))

    def test_accepts_building_upgrade_type_purchased_any_with_known_type(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_upgrade_type_purchased_any", "upgrade_type": "autocraft"}
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_building_upgrade_type_purchased_any_with_unknown_type(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "building_upgrade_type_purchased_any", "upgrade_type": "no_such_type"}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_type" in e for e in errors))

    def test_accepts_storage_tier_at_least_with_resource_that_has_storage(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["conditions"] = [
            {"type": "storage_tier_at_least", "resource": "limited_res", "amount": 1}
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_storage_tier_at_least_referencing_resource_without_storage(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        no_storage_resource = next(r["id"] for r in d["resources"] if not r.get("storage"))
        d["quests"][any_q]["conditions"] = [
            {"type": "storage_tier_at_least", "resource": no_storage_resource, "amount": 1}
        ]
        errors = cv.validate(d)
        self.assertTrue(any("не настроена вместимость склада" in e for e in errors))

    def test_rejects_quest_reward_case_referencing_unknown_case(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["reward_case"] = "no_such_case"
        errors = cv.validate(d)
        self.assertTrue(any("no_such_case" in e for e in errors))

    def test_rejects_quest_with_non_positive_era(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["era"] = 0
        errors = cv.validate(d)
        self.assertTrue(any(any_q in e and "era" in e for e in errors))

    def test_rejects_quest_requires_referencing_unknown_quest(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["requires"] = ["no_such_quest"]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_quest" in e for e in errors))

    def test_rejects_quest_requiring_itself(self):
        d = copy.deepcopy(self.data)
        any_q = next(iter(d["quests"]))
        d["quests"][any_q]["requires"] = [any_q]
        errors = cv.validate(d)
        self.assertTrue(any(any_q in e and "сам" in e for e in errors))

    def test_rejects_quest_requires_cycle(self):
        d = copy.deepcopy(self.data)
        ids = list(d["quests"].keys())
        self.assertGreaterEqual(len(ids), 2, "фикстуре нужно хотя бы 2 квеста для этого теста")
        a, b = ids[:2]
        d["quests"][a]["requires"] = list(set(d["quests"][a].get("requires", [])) | {b})
        d["quests"][b]["requires"] = list(set(d["quests"][b].get("requires", [])) | {a})
        errors = cv.validate(d)
        self.assertTrue(any("Цикл" in e for e in errors))

    def test_rejects_loot_case_with_empty_prizes(self):
        d = copy.deepcopy(self.data)
        any_case = next(iter(d["loot_cases"]))
        d["loot_cases"][any_case]["prizes"] = []
        errors = cv.validate(d)
        self.assertTrue(any(any_case in e and "prizes" in e for e in errors))

    def test_rejects_loot_case_prize_referencing_unknown_resource(self):
        d = copy.deepcopy(self.data)
        any_case = next(iter(d["loot_cases"]))
        d["loot_cases"][any_case]["prizes"] = [{"resource": "no_such_resource", "amount": 1, "weight": 1}]
        errors = cv.validate(d)
        self.assertTrue(any("no_such_resource" in e for e in errors))

    def test_rejects_loot_case_prize_with_non_positive_weight(self):
        d = copy.deepcopy(self.data)
        any_case = next(iter(d["loot_cases"]))
        d["loot_cases"][any_case]["prizes"] = [{"resource": "raw_a", "amount": 1, "weight": 0}]
        errors = cv.validate(d)
        self.assertTrue(any(any_case in e and "weight" in e for e in errors))

    def test_rejects_quest_id_colliding_with_other_category(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        any_q = next(iter(d["quests"]))
        d["quests"][any_action] = d["quests"].pop(any_q)
        errors = cv.validate(d)
        self.assertTrue(any(any_action in e and "уникальны" in e for e in errors))


class LocationsTimeAndTechDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_fixture_content_is_valid(self):
        errors = cv.validate(self.data)
        self.assertEqual(errors, [])

    def test_rejects_manual_action_with_unknown_location(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["location"] = "no_such_location"
        errors = cv.validate(d)
        self.assertTrue(any("локация" in e for e in errors))

    def test_accepts_manual_action_without_location_as_root_level_action(self):
        # Необязательное поле теперь: null/отсутствие = действие лежит в корне
        # дерева локаций (см. вложенные локации ниже), а не ошибка.
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        del d["manual_actions"][any_action]["location"]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_manual_action_with_unknown_requires_building(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["requires_building"] = "no_such_building"
        errors = cv.validate(d)
        self.assertTrue(any("неизвестная постройка" in e for e in errors))

    def test_accepts_manual_action_with_valid_requires_building(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        any_building = next(iter(d["buildings"]))
        d["manual_actions"][any_action]["requires_building"] = any_building
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_location_with_valid_requires_building(self):
        d = copy.deepcopy(self.data)
        any_location = next(iter(d["locations"]))
        any_building = next(iter(d["buildings"]))
        d["locations"][any_location]["requires_building"] = any_building
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_location_with_unknown_requires_building(self):
        d = copy.deepcopy(self.data)
        any_location = next(iter(d["locations"]))
        d["locations"][any_location]["requires_building"] = "no_such_building"
        errors = cv.validate(d)
        self.assertTrue(any("неизвестная постройка" in e for e in errors))

    def test_fixture_chance_fields_are_valid(self):
        # craft_lucky/craft_risky в fixtures.py уже используют cost_chance/gain_chance -
        # test_fixture_content_is_valid выше проверяет это косвенно, но явный тест
        # называет причину, если он вдруг начнёт падать.
        self.assertIn("gain_chance", self.data["manual_actions"]["craft_lucky"])
        self.assertIn("cost_chance", self.data["manual_actions"]["craft_risky"])
        errors = cv.validate(self.data)
        self.assertEqual(errors, [])

    def test_rejects_cost_chance_referencing_resource_not_in_cost(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_risky"]["cost_chance"]["risky_output"] = 0.5
        errors = cv.validate(d)
        self.assertTrue(any("cost_chance" in e and "risky_output" in e for e in errors))

    def test_rejects_gain_chance_referencing_resource_not_in_gain(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_lucky"]["gain_chance"]["raw_a"] = 0.5
        errors = cv.validate(d)
        self.assertTrue(any("gain_chance" in e and "raw_a" in e for e in errors))

    def test_rejects_chance_out_of_range(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_lucky"]["gain_chance"]["lucky_output"] = 1.5
        errors = cv.validate(d)
        self.assertTrue(any("gain_chance" in e for e in errors))

    def test_rejects_non_numeric_chance(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_risky"]["cost_chance"]["raw_a"] = "always"
        errors = cv.validate(d)
        self.assertTrue(any("cost_chance" in e for e in errors))

    def test_accepts_resource_with_fractional_flag(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["fractional"] = True
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_non_boolean_fractional_flag(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["fractional"] = "yes"
        errors = cv.validate(d)
        self.assertTrue(any("fractional" in e for e in errors))

    def test_accepts_resource_with_valid_storage_config(self):
        # fixtures.py уже содержит "limited_res" с валидным storage - фикстура
        # в целом проходит без ошибок (test_fixture_content_is_valid выше), здесь
        # отдельно проверяем ЯВНО добавленный storage на другом ресурсе.
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 50,
            "tiers": [{"capacity_add": 10, "cost": {"raw_a": 1}, "requires_tech": None}],
        }
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_negative_base_capacity(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {"base_capacity": -5, "tiers": []}
        errors = cv.validate(d)
        self.assertTrue(any("base_capacity" in e for e in errors))

    def test_rejects_non_positive_tier_capacity_add(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 10, "tiers": [{"capacity_add": 0, "cost": {}}],
        }
        errors = cv.validate(d)
        self.assertTrue(any("capacity_add" in e for e in errors))

    def test_rejects_storage_tier_with_unknown_tech(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 10,
            "tiers": [{"capacity_add": 5, "cost": {}, "requires_tech": "no_such_tech"}],
        }
        errors = cv.validate(d)
        self.assertTrue(any("технология" in e for e in errors))

    def test_rejects_storage_tier_cost_referencing_unknown_resource(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 10, "tiers": [{"capacity_add": 5, "cost": {"no_such_resource": 1}}],
        }
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))

    def test_accepts_infinite_storage_tier_without_capacity_add(self):
        # infinite: True - тир снимает ограничение склада совсем вместо capacity_add
        # (game/engine.py:storage_capacity) - capacity_add ему не нужен.
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 10, "tiers": [{"infinite": True, "cost": {}, "requires_tech": None}],
        }
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_non_boolean_infinite_flag(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 10, "tiers": [{"infinite": "yes", "capacity_add": 5, "cost": {}}],
        }
        errors = cv.validate(d)
        self.assertTrue(any("infinite" in e for e in errors))

    def test_rejects_non_infinite_tier_missing_capacity_add(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["storage"] = {
            "base_capacity": 10, "tiers": [{"infinite": False, "cost": {}}],
        }
        errors = cv.validate(d)
        self.assertTrue(any("capacity_add" in e for e in errors))

    def test_rejects_missing_shaft_location(self):
        d = copy.deepcopy(self.data)
        del d["locations"]["shaft"]
        errors = cv.validate(d)
        self.assertTrue(any("shaft" in e for e in errors))

    def test_rejects_missing_converter_location(self):
        # "converter" (не "mechanisms") - обязательная локация-якорь выбора рецепта
        # крафта за энергию, см. CLAUDE.md про генерализацию Механизмов/Преобразователя.
        d = copy.deepcopy(self.data)
        del d["locations"]["converter"]
        errors = cv.validate(d)
        self.assertTrue(any("converter" in e for e in errors))

    def test_rejects_missing_energy_resource(self):
        d = copy.deepcopy(self.data)
        d["resources"] = [r for r in d["resources"] if r["id"] != "energy"]
        errors = cv.validate(d)
        self.assertTrue(any("energy" in e for e in errors))

    def test_rejects_bad_active_period(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["active_period"] = "dusk"
        errors = cv.validate(d)
        self.assertTrue(any("active_period" in e for e in errors))

    def test_accepts_day_and_night_active_period(self):
        d = copy.deepcopy(self.data)
        ids = list(d["buildings"].keys())
        d["buildings"][ids[0]]["active_period"] = "day"
        d["buildings"][ids[1]]["active_period"] = "night"
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_tech_without_description(self):
        d = copy.deepcopy(self.data)
        any_tech = next(iter(d["techs"]))
        del d["techs"][any_tech]["description"]
        errors = cv.validate(d)
        self.assertTrue(any("описание" in e for e in errors))

    def test_rejects_building_without_description(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        del d["buildings"][any_building]["description"]
        errors = cv.validate(d)
        self.assertTrue(any("описание" in e for e in errors))

    def test_rejects_tech_without_tier(self):
        d = copy.deepcopy(self.data)
        any_tech = next(iter(d["techs"]))
        del d["techs"][any_tech]["tier"]
        errors = cv.validate(d)
        self.assertTrue(any("tier" in e for e in errors))

    def test_rejects_tech_with_non_positive_tier(self):
        d = copy.deepcopy(self.data)
        any_tech = next(iter(d["techs"]))
        d["techs"][any_tech]["tier"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("tier" in e for e in errors))

    def test_rejects_building_with_non_positive_lab_tier(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["lab_tier"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("lab_tier" in e for e in errors))

    def test_accepts_building_with_valid_lab_tier(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["lab_tier"] = 2
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_building_with_a_valid_scale_upgrade(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_scale", "name": "Масштаб", "description": "x2.", "type": "scale", "value": 2,
             "requires_tech": None, "cost": {"raw_a": 1}},
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_scale_upgrade_with_a_non_integer_value(self):
        # По прямому пожеланию: множитель scale - ЛЮБОЕ положительное число
        # относительно базового рецепта, не обязательно целое и не обязательно >= 2
        # (иначе после покупки x2 нельзя было бы дойти ровно до x3 второй покупкой).
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_scale", "name": "Масштаб", "description": "x1.5.", "type": "scale", "value": 1.5,
             "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_non_positive_scale_value(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_scale", "name": "Масштаб", "description": "x0.", "type": "scale", "value": 0,
             "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("scale" in e for e in errors))

    def test_rejects_energy_efficiency_value_out_of_range(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_eff", "name": "Эффективность", "description": "x1 - бессмысленно.",
             "type": "energy_efficiency", "value": 1, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("energy_efficiency" in e for e in errors))

    def test_rejects_generation_efficiency_on_building_without_energy_production(self):
        d = copy.deepcopy(self.data)
        any_building = next(b for b, v in self.data["buildings"].items() if "energy" not in v.get("produces", {}))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_gen", "name": "Генерация", "description": "+50%.", "type": "generation_efficiency",
             "value": 1.5, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("generation_efficiency" in e for e in errors))

    def test_accepts_generation_efficiency_on_a_generator(self):
        d = copy.deepcopy(self.data)
        generator = next(b for b, v in self.data["buildings"].items() if "energy" in v.get("produces", {}))
        d["buildings"][generator]["upgrades"] = [
            {"id": "up_gen", "name": "Генерация", "description": "+50%.", "type": "generation_efficiency",
             "value": 1.5, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_unknown_upgrade_type(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["upgrades"] = [
            {"id": "up_x", "name": "Что-то", "description": "Что-то.", "type": "magic",
             "value": 2, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("type" in e for e in errors))

    def test_rejects_duplicate_upgrade_ids_on_the_same_building(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        upgrade = {"id": "dup", "name": "Дубль", "description": "Дубль.", "type": "scale", "value": 2,
                   "requires_tech": None, "cost": {}}
        d["buildings"][any_building]["upgrades"] = [upgrade, dict(upgrade)]
        errors = cv.validate(d)
        self.assertTrue(any("повторяющийся" in e for e in errors))

    def test_rejects_manual_action_with_non_integer_priority(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["priority"] = "high"
        errors = cv.validate(d)
        self.assertTrue(any("priority" in e for e in errors))

    def test_accepts_manual_action_with_integer_priority(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["priority"] = 5
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_manual_action_with_duration_turns_below_two(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["duration_turns"] = 1
        errors = cv.validate(d)
        self.assertTrue(any("duration_turns" in e for e in errors))

    def test_rejects_manual_action_with_non_integer_duration_turns(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["duration_turns"] = "slow"
        errors = cv.validate(d)
        self.assertTrue(any("duration_turns" in e for e in errors))

    def test_accepts_manual_action_with_valid_duration_turns(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["duration_turns"] = 3
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_non_boolean_excluded_from_autocraft(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["excluded_from_autocraft"] = "yes"
        errors = cv.validate(d)
        self.assertTrue(any("excluded_from_autocraft" in e for e in errors))

    def test_accepts_manual_action_with_excluded_from_autocraft(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["excluded_from_autocraft"] = True
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_out_of_range_time_settings(self):
        d = copy.deepcopy(self.data)
        d["time"] = {"hours_per_turn": 30, "day_start_hour": 20, "day_end_hour": 5}
        errors = cv.validate(d)
        self.assertTrue(any("hours_per_turn" in e for e in errors))
        self.assertTrue(any("day_start_hour" in e or "day_end_hour" in e for e in errors))


class NestedLocationsValidationTests(unittest.TestCase):
    """parent_location (локации) - полноценное вложенное меню, см. game/engine.py:
    location_children/manual_actions_at."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_accepts_valid_parent_location(self):
        d = copy.deepcopy(self.data)
        any_location = next(iter(d["locations"]))
        other_location = next(lid for lid in d["locations"] if lid != any_location)
        d["locations"][any_location]["parent_location"] = other_location
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_missing_parent_location_is_fine_and_means_root(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_rejects_unknown_parent_location(self):
        d = copy.deepcopy(self.data)
        any_location = next(iter(d["locations"]))
        d["locations"][any_location]["parent_location"] = "no_such_location"
        errors = cv.validate(d)
        self.assertTrue(any("родительская локация" in e for e in errors))

    def test_rejects_location_as_its_own_parent(self):
        d = copy.deepcopy(self.data)
        any_location = next(iter(d["locations"]))
        d["locations"][any_location]["parent_location"] = any_location
        errors = cv.validate(d)
        self.assertTrue(any("родителем самой себе" in e for e in errors))

    def test_rejects_a_two_location_parent_cycle(self):
        d = copy.deepcopy(self.data)
        ids = list(d["locations"].keys())[:2]
        a, b = ids
        d["locations"][a]["parent_location"] = b
        d["locations"][b]["parent_location"] = a
        errors = cv.validate(d)
        self.assertTrue(any("Цикл в дереве локаций" in e for e in errors))


class AutocraftAndComputeValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_rejects_negative_compute_power(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["compute_power"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("compute_power" in e for e in errors))

    def test_rejects_unlocks_autocraft_referencing_unknown_resource(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["unlocks_autocraft"] = ["no_such_resource"]
        errors = cv.validate(d)
        self.assertTrue(any("unlocks_autocraft" in e for e in errors))

    def test_unlocks_autocraft_is_obsolete(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["unlocks_autocraft"] = []
        self.assertTrue(any("unlocks_autocraft устарело" in e for e in cv.validate(d)))

    def test_assembler_needs_a_known_location_and_a_positive_or_null_limit(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["autocraft_assembler"] = {"location": "grid_loc", "per_turn": None}
        self.assertEqual(cv.validate(copy.deepcopy(d)), [])
        d["buildings"][any_building]["autocraft_assembler"] = {"location": "nowhere", "per_turn": 0}
        errors = cv.validate(d)
        self.assertTrue(any("неизвестная локация 'nowhere'" in e for e in errors), errors)
        self.assertTrue(any("per_turn" in e for e in errors), errors)

    def test_ritual_assembler_targets_an_altar_building(self):
        d = copy.deepcopy(self.data)
        any_building = next(b for b in d["buildings"] if b != "ritual_altar")
        d["buildings"][any_building]["autocraft_assembler"] = {"building": "ritual_altar", "per_turn": 1}
        self.assertEqual(cv.validate(copy.deepcopy(d)), [])
        d["buildings"][any_building]["autocraft_assembler"] = {"building": any_building, "per_turn": 1}
        self.assertTrue(any("не ритуальный алтарь" in e for e in cv.validate(copy.deepcopy(d))))
        d["buildings"][any_building]["autocraft_assembler"] = {"building": "nowhere", "per_turn": 1}
        self.assertTrue(any("неизвестная постройка 'nowhere'" in e for e in cv.validate(copy.deepcopy(d))))

    def test_assembler_needs_exactly_one_target(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        for spec in ({"per_turn": 1}, {"location": "grid_loc", "building": "ritual_altar", "per_turn": 1}):
            d["buildings"][any_building]["autocraft_assembler"] = spec
            self.assertTrue(any("ровно одно из полей" in e for e in cv.validate(copy.deepcopy(d))), spec)

    def test_autocraft_upgrade_only_on_a_mechanism_or_converter(self):
        d = copy.deepcopy(self.data)
        upgrade = {"id": "auto", "name": "Автоматизация", "description": ".", "type": "autocraft",
                   "value": 1, "requires_tech": None, "cost": {}}
        d["buildings"]["mechanism_a"].setdefault("upgrades", []).append(dict(upgrade))
        self.assertEqual(cv.validate(copy.deepcopy(d)), [])
        d["buildings"]["gen"].setdefault("upgrades", []).append(dict(upgrade))
        self.assertTrue(any("autocraft имеет смысл только" in e for e in cv.validate(d)))

    def test_rejects_energy_converter_with_bad_max_energy_per_turn(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["energy_converter"] = {"max_energy_per_turn": -1}
        errors = cv.validate(d)
        self.assertTrue(any("energy_converter.max_energy_per_turn" in e for e in errors))

    def test_accepts_valid_energy_converter(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["energy_converter"] = {"max_energy_per_turn": 20}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_energy_converter_without_max_energy_per_turn(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["energy_converter"] = {}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_negative_autocraft_speed(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["autocraft_speed"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("autocraft_speed" in e for e in errors))

    def test_rejects_negative_autocraft_slots(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["autocraft_slots"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("autocraft_slots" in e for e in errors))

    def test_autocraft_speed_is_obsolete_but_slots_are_fine(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["autocraft_slots"] = 1
        self.assertEqual(cv.validate(copy.deepcopy(d)), [])
        d["buildings"][any_building]["autocraft_speed"] = 8
        self.assertTrue(any("autocraft_speed устарело" in e for e in cv.validate(d)))

    def test_rejects_non_positive_max_count(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["max_count"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("max_count" in e for e in errors))

    def test_accepts_null_or_positive_max_count(self):
        d = copy.deepcopy(self.data)
        ids = list(d["buildings"].keys())
        d["buildings"][ids[0]]["max_count"] = None
        d["buildings"][ids[1]]["max_count"] = 1
        errors = cv.validate(d)
        self.assertEqual(errors, [])


class ConstructionStagesValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_current_fixture_furnace_is_valid(self):
        # furnace в tests/fixtures.py уже многоэтапная - страхуемся, что фикстура
        # сама по себе всегда проходит валидацию, как и настоящий content.json.
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_accepts_single_stage_list(self):
        # Разрешено осознанно: одна "стадия" - способ дать постройке описание её
        # единственного шага без счётчика "1 из 1" (см. engine.current_construction_stage
        # и web_ui.html:actionLabel - счётчик показывается только когда стадий > 1).
        d = copy.deepcopy(self.data)
        d["buildings"]["furnace"]["construction_stages"] = [{"cost": {"raw_a": 1}, "description": "единственный этап"}]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_empty_stage_list(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["furnace"]["construction_stages"] = []
        errors = cv.validate(d)
        self.assertTrue(any("construction_stages" in e for e in errors))

    def test_rejects_stage_without_description(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["furnace"]["construction_stages"][0].pop("description")
        errors = cv.validate(d)
        self.assertTrue(any("описание этапа" in e for e in errors))

    def test_rejects_stage_with_bad_cost(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["furnace"]["construction_stages"][0]["cost"] = {"no_such_resource": 1}
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))

    def test_accepts_null_construction_stages_as_single_stage_building(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["furnace"]["construction_stages"] = None
        errors = cv.validate(d)
        self.assertEqual(errors, [])


class ClickDelayValidationTests(unittest.TestCase):
    """click_delay_ms (buildings/manual_actions/mining_info) и ui.default_action_delay_ms -
    чисто UI-защита от автокликера (см. tools/web_ui.html), но всё равно значения
    должны быть осмысленными числами."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_missing_click_delay_fields_are_fine(self):
        # поле необязательно везде - отсутствие означает "использовать глобальное
        # значение по умолчанию" (game.content.DEFAULT_ACTION_DELAY_MS)
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_accepts_explicit_click_delay_on_building_action_and_mining(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        any_action = next(iter(d["manual_actions"]))
        any_ore = next(iter(d["mining_info"]))
        d["buildings"][any_building]["click_delay_ms"] = 1500
        d["manual_actions"][any_action]["click_delay_ms"] = 100
        d["mining_info"][any_ore]["click_delay_ms"] = 0
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_negative_click_delay_on_building(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["click_delay_ms"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("click_delay_ms" in e for e in errors))

    def test_rejects_negative_click_delay_on_manual_action(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["click_delay_ms"] = -50
        errors = cv.validate(d)
        self.assertTrue(any("click_delay_ms" in e for e in errors))

    def test_accepts_global_default_action_delay_override(self):
        d = copy.deepcopy(self.data)
        d["ui"] = {"default_action_delay_ms": 800}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_missing_ui_section_entirely_is_fine(self):
        d = copy.deepcopy(self.data)
        d.pop("ui", None)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_negative_default_action_delay(self):
        d = copy.deepcopy(self.data)
        d["ui"] = {"default_action_delay_ms": -1}
        errors = cv.validate(d)
        self.assertTrue(any("default_action_delay_ms" in e for e in errors))


class SoundValidationTests(unittest.TestCase):
    """manual_actions.*.sound - имя файла кастомного звука в tools/sounds/."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_accepts_missing_sound(self):
        d = copy.deepcopy(self.data)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_valid_sound_filename(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["sound"] = f"{any_action}.wav"
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_null_sound(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["sound"] = None
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_path_traversal_in_sound(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["sound"] = "../../etc/passwd.wav"
        errors = cv.validate(d)
        self.assertTrue(any("sound" in e for e in errors))

    def test_rejects_disallowed_extension_in_sound(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["sound"] = f"{any_action}.exe"
        errors = cv.validate(d)
        self.assertTrue(any("sound" in e for e in errors))

    def test_rejects_non_string_sound(self):
        d = copy.deepcopy(self.data)
        any_action = next(iter(d["manual_actions"]))
        d["manual_actions"][any_action]["sound"] = 123
        errors = cv.validate(d)
        self.assertTrue(any("sound" in e for e in errors))

    def test_accepts_valid_sound_filename_on_ritual_recipe(self):
        d = copy.deepcopy(self.data)
        any_recipe = next(iter(d["ritual_recipes"]))
        d["ritual_recipes"][any_recipe]["sound"] = f"{any_recipe}.ogg"
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_disallowed_extension_in_ritual_recipe_sound(self):
        d = copy.deepcopy(self.data)
        any_recipe = next(iter(d["ritual_recipes"]))
        d["ritual_recipes"][any_recipe]["sound"] = f"{any_recipe}.exe"
        errors = cv.validate(d)
        self.assertTrue(any("sound" in e for e in errors))


class EnergyRecipesValidationTests(unittest.TestCase):
    """energy_recipes (рецепты для построек с полем energy_converter) - см.
    ENERGY_RECIPE_FOR_RESOURCE в game/engine.py."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_current_fixture_energy_recipes_are_valid(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_missing_energy_recipes_section_is_fine(self):
        d = copy.deepcopy(self.data)
        d.pop("energy_recipes", None)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_recipe_without_name(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["make_raw_b"]["name"] = ""
        errors = cv.validate(d)
        self.assertTrue(any("не указано название" in e for e in errors))

    def test_rejects_recipe_without_threshold(self):
        d = copy.deepcopy(self.data)
        del d["energy_recipes"]["make_raw_b"]["threshold"]
        errors = cv.validate(d)
        self.assertTrue(any("threshold" in e for e in errors))

    def test_rejects_recipe_with_zero_threshold(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["make_raw_b"]["threshold"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("threshold" in e for e in errors))

    def test_rejects_recipe_with_empty_produces(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["make_raw_b"]["produces"] = {}
        errors = cv.validate(d)
        self.assertTrue(any("produces" in e for e in errors))

    def test_rejects_recipe_with_unknown_resource_in_produces(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["make_raw_b"]["produces"] = {"no_such_resource": 1}
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))

    def test_rejects_recipe_with_unknown_tech(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["make_raw_b"]["requires_tech"] = "no_such_tech"
        errors = cv.validate(d)
        self.assertTrue(any("неизвестная технология" in e for e in errors))

    def test_rejects_recipe_id_colliding_with_another_category(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["energy_recipes"][any_building] = copy.deepcopy(d["energy_recipes"]["make_raw_b"])
        errors = cv.validate(d)
        self.assertTrue(any("занят сразу несколькими сущностями" in e for e in errors))


class RandomProduceRangeValidationTests(unittest.TestCase):
    """Случайное количество produce ({"min","max"} вместо плоского числа, см.
    check_amount_range/check_resource_dict(allow_range=True) в
    tools/content_validation.py) - разрешено ТОЛЬКО на produce-стороне
    (buildings.produces/manual_actions.gain/energy_recipes.produces/
    soul_recipes.produces), никогда в cost/consumes. fixtures.py уже содержит
    валидные диапазоны (MANUAL_ACTIONS["craft_variable"].gain,
    BUILDINGS["variable_gen"].produces, ENERGY_RECIPES["make_variable"].produces,
    SOUL_RECIPES["variable_soul"].produces) - покрыты test_fixture_content_is_valid
    в других классах этого файла, здесь только правила самого check_amount_range."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_rejects_min_greater_than_max(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["gain"]["variable_output"] = {"min": 6, "max": 2}
        errors = cv.validate(d)
        self.assertTrue(any("min" in e and "max" in e for e in errors))

    def test_rejects_negative_min(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["gain"]["variable_output"] = {"min": -1, "max": 2}
        errors = cv.validate(d)
        self.assertTrue(any("min" in e for e in errors))

    def test_rejects_missing_max_key(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["gain"]["variable_output"] = {"min": 1}
        errors = cv.validate(d)
        self.assertTrue(any("min и max" in e for e in errors))

    def test_rejects_extra_key_in_range(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["gain"]["variable_output"] = {"min": 1, "max": 2, "chance": 0.5}
        errors = cv.validate(d)
        self.assertTrue(any("min и max" in e for e in errors))

    def test_rejects_non_numeric_bound(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["gain"]["variable_output"] = {"min": "1", "max": 2}
        errors = cv.validate(d)
        self.assertTrue(any("min" in e for e in errors))

    def test_accepts_equal_min_and_max(self):
        # Диапазон с min == max - вырожденный, но не ошибка (равномерно от x до x).
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["gain"]["variable_output"] = {"min": 4, "max": 4}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_range_object_in_cost(self):
        # cost/consumes диапазонов не бывает - check_resource_dict там вызывается
        # БЕЗ allow_range=True, объект должен провалиться как "некорректное количество".
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_variable"]["cost"]["raw_a"] = {"min": 1, "max": 2}
        errors = cv.validate(d)
        self.assertTrue(any("некорректное количество" in e for e in errors))

    def test_rejects_range_object_in_building_consumes(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["variable_gen"]["consumes"] = {"raw_a": {"min": 1, "max": 2}}
        errors = cv.validate(d)
        self.assertTrue(any("некорректное количество" in e for e in errors))

    def test_rejects_range_object_in_tech_effect_produces(self):
        # Тех-эффекты (аддитивные +к produces/consumes) - плоское число, диапазон
        # там не поддержан осознанно (не завязан на check_resource_dict(allow_range)).
        d = copy.deepcopy(self.data)
        any_tech = next(iter(d["techs"]))
        d["techs"][any_tech]["effects"] = [{"building": "*", "produces": {"raw_a": {"min": 1, "max": 2}}}]
        errors = cv.validate(d)
        self.assertTrue(any("некорректное количество" in e for e in errors))

    def test_accepts_range_in_building_produces(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["variable_gen"]["produces"]["variable_output"] = {"min": 2, "max": 9}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_range_in_soul_recipe_produces(self):
        d = copy.deepcopy(self.data)
        d["soul_recipes"]["variable_soul"]["produces"]["variable_output"] = {"min": 1, "max": 9}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_accepts_range_in_energy_recipe_produces(self):
        d = copy.deepcopy(self.data)
        d["energy_recipes"]["make_variable"]["produces"]["variable_output"] = {"min": 1, "max": 9}
        errors = cv.validate(d)
        self.assertEqual(errors, [])


class MiningFieldValidationTests(unittest.TestCase):
    """toughness/field_cluster_size (mining_info[...]) и mine_field (настройки
    самого поля шахты - size/dig_tool/cluster_density/rock_*)."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_current_fixture_mining_info_and_mine_field_are_valid(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_rejects_non_positive_toughness(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"]["toughness"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("toughness" in e for e in errors))

    def test_rejects_field_cluster_size_with_min_greater_than_max(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"]["field_cluster_size"] = [5, 2]
        errors = cv.validate(d)
        self.assertTrue(any("field_cluster_size" in e for e in errors))

    def test_rejects_missing_mine_field_section(self):
        d = copy.deepcopy(self.data)
        del d["mine_field"]
        errors = cv.validate(d)
        self.assertTrue(any("mine_field" in e for e in errors))

    def test_rejects_mine_field_dig_tool_referencing_unknown_tool(self):
        d = copy.deepcopy(self.data)
        d["mine_field"]["dig_tool"] = "does_not_exist"
        errors = cv.validate(d)
        self.assertTrue(any("dig_tool" in e for e in errors))

    def test_rejects_mine_field_rock_resource_referencing_unknown_resource(self):
        d = copy.deepcopy(self.data)
        d["mine_field"]["rock_resource"] = "does_not_exist"
        errors = cv.validate(d)
        self.assertTrue(any("rock_resource" in e for e in errors))

    def test_rejects_non_positive_mine_field_size(self):
        d = copy.deepcopy(self.data)
        d["mine_field"]["size"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("mine_field.size" in e for e in errors))


class PickaxeMiningTierFieldsValidationTests(unittest.TestCase):
    """mining_area/mining_power (tools.pickaxe.tiers[...]) - зона удара и урон по
    клетке поля шахты за клик, необязательные, положительные целые."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_rejects_non_positive_mining_area(self):
        d = copy.deepcopy(self.data)
        d["tools"]["pickaxe"]["tiers"][0]["mining_area"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("mining_area" in e for e in errors))

    def test_rejects_non_positive_mining_power(self):
        d = copy.deepcopy(self.data)
        d["tools"]["pickaxe"]["tiers"][0]["mining_power"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("mining_power" in e for e in errors))


class SpeciesAndBreedingValidationTests(unittest.TestCase):
    """species (content.json["species"], ключ = id уже существующего ресурса, тот же
    принцип, что у mining_info - dominant/weight признака "вид"), genetic_traits
    (3 жёстко зашитых глобальных признака - lifespan/output_multiplier/
    active_period) и mutations (список переходов между видами при скрещивании)."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_current_fixture_species_genetics_and_mutations_are_valid(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_missing_species_genetics_and_mutations_sections_are_fine(self):
        # Отсутствие genetic_traits НЕ проходит - это ОБЯЗАТЕЛЬНЫЕ 3 признака (см.
        # test_rejects_missing_genetic_trait ниже) - тут проверяем species/mutations/
        # genetics по отдельности, оставляя genetic_traits как в фикстуре.
        d = copy.deepcopy(self.data)
        d.pop("species", None)
        d.pop("mutations", None)
        d.pop("genetics", None)
        # spawn_species (см. MANUAL_ACTIONS["catch_critter_a"] в fixtures.py) ссылается
        # на вид - без секции species это ЗАКОНОМЕРНО стало бы ошибкой валидации,
        # но тут проверяется именно "секции species/mutations/genetics опциональны",
        # а не это отдельное поле - убираем зависящее от неё действие тем же приёмом.
        d["manual_actions"].pop("catch_critter_a", None)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_species_key_not_referencing_a_resource(self):
        d = copy.deepcopy(self.data)
        d["species"]["no_such_resource"] = {"description": "x", "produces": {}, "consumes": {}, "dominant": False, "weight": 1}
        errors = cv.validate(d)
        self.assertTrue(any("это не известный ресурс" in e for e in errors))

    def test_species_key_matching_a_resource_id_is_not_flagged_as_a_category_clash(self):
        # Ключ species НАМЕРЕННО совпадает с id ресурса (как у mining_info) - это не
        # отдельное пространство id, значит не должно ловиться уникальностью id.
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertFalse(any("critter_a" in e and "занят сразу несколькими" in e for e in errors))

    def test_rejects_species_without_description(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["description"] = ""
        errors = cv.validate(d)
        self.assertTrue(any("не указано описание" in e for e in errors))

    def test_rejects_species_with_unknown_resource_in_produces(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["produces"] = {"no_such_resource": 1}
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))

    def test_rejects_non_boolean_species_dominant(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["dominant"] = "yes"
        errors = cv.validate(d)
        self.assertTrue(any("dominant" in e for e in errors))

    def test_rejects_negative_species_weight(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["weight"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("weight" in e for e in errors))

    def test_rejects_missing_genetic_trait(self):
        d = copy.deepcopy(self.data)
        del d["genetic_traits"]["lifespan"]
        errors = cv.validate(d)
        self.assertTrue(any("lifespan" in e for e in errors))

    def test_rejects_genetic_trait_with_empty_alleles(self):
        d = copy.deepcopy(self.data)
        d["genetic_traits"]["lifespan"]["alleles"] = []
        errors = cv.validate(d)
        self.assertTrue(any("alleles" in e for e in errors))

    def test_rejects_genetic_trait_without_any_dominant_allele(self):
        d = copy.deepcopy(self.data)
        for a in d["genetic_traits"]["lifespan"]["alleles"]:
            a["dominant"] = False
        errors = cv.validate(d)
        self.assertTrue(any("dominant" in e for e in errors))

    # ---- species.*.gene_pool (game/engine.py: species_gene_pool) ----

    def test_rejects_species_without_gene_pool(self):
        d = copy.deepcopy(self.data)
        del d["species"]["critter_a"]["gene_pool"]
        errors = cv.validate(d)
        self.assertTrue(any("critter_a" in e and "gene_pool" in e for e in errors))

    def test_rejects_gene_pool_missing_a_trait_or_with_empty_trait(self):
        d = copy.deepcopy(self.data)
        del d["species"]["critter_a"]["gene_pool"]["lifespan"]
        d["species"]["critter_b"]["gene_pool"]["environment"] = {}
        errors = cv.validate(d)
        self.assertTrue(any("critter_a" in e and "gene_pool.lifespan" in e for e in errors))
        self.assertTrue(any("critter_b" in e and "gene_pool.environment" in e for e in errors))

    def test_rejects_gene_pool_with_unknown_allele_or_trait(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["gene_pool"]["lifespan"]["ancient"] = 1
        d["species"]["critter_a"]["gene_pool"]["color"] = {"red": 1}
        errors = cv.validate(d)
        self.assertTrue(any("ancient" in e for e in errors))
        self.assertTrue(any("'color'" in e for e in errors))

    def test_rejects_gene_pool_with_non_positive_weight(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["gene_pool"]["lifespan"]["normal"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("gene_pool.lifespan.normal" in e for e in errors))

    def test_genetic_trait_alleles_need_no_weight(self):
        d = copy.deepcopy(self.data)
        for trait in d["genetic_traits"].values():
            for a in trait["alleles"]:
                a.pop("weight", None)
        self.assertEqual(cv.validate(d), [])

    def test_rejects_non_positive_allele_value_for_numeric_trait(self):
        d = copy.deepcopy(self.data)
        d["genetic_traits"]["output_multiplier"]["alleles"][0]["value"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("value" in e for e in errors))

    def test_rejects_active_period_missing_a_required_allele_id(self):
        d = copy.deepcopy(self.data)
        d["genetic_traits"]["active_period"]["alleles"] = [
            a for a in d["genetic_traits"]["active_period"]["alleles"] if a["id"] != "night"
        ]
        errors = cv.validate(d)
        self.assertTrue(any("active_period" in e and "night" in e for e in errors))

    def test_rejects_mutation_with_unknown_parent(self):
        d = copy.deepcopy(self.data)
        d["mutations"][0]["parent_a"] = "no_such_species"
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный вид" in e for e in errors))

    def test_rejects_mutation_with_chance_out_of_range(self):
        d = copy.deepcopy(self.data)
        d["mutations"][0]["chance"] = 1.5
        errors = cv.validate(d)
        self.assertTrue(any("chance" in e for e in errors))

    def test_rejects_mutation_with_unknown_requires_building(self):
        d = copy.deepcopy(self.data)
        d["mutations"][0]["requires_building"] = "no_such_building"
        errors = cv.validate(d)
        self.assertTrue(any("неизвестная постройка" in e for e in errors))

    def test_rejects_mutation_empty_outcomes(self):
        d = copy.deepcopy(self.data)
        d["mutations"][0]["outcomes"] = []
        errors = cv.validate(d)
        self.assertTrue(any("outcomes" in e for e in errors))

    def test_rejects_mutation_outcome_with_unknown_species(self):
        d = copy.deepcopy(self.data)
        d["mutations"][0]["outcomes"] = [{"species_id": "no_such_species", "weight": 1}]
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный вид" in e for e in errors))

    def test_rejects_mutation_outcome_with_non_positive_weight(self):
        d = copy.deepcopy(self.data)
        d["mutations"][0]["outcomes"] = [{"species_id": "critter_b", "weight": 0}]
        errors = cv.validate(d)
        self.assertTrue(any("weight" in e for e in errors))

    def test_rejects_negative_breeding_slots(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["apiary"]["breeding_slots"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("breeding_slots" in e for e in errors))

    def test_rejects_non_string_species_family(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["family"] = 123
        errors = cv.validate(d)
        self.assertTrue(any("family" in e for e in errors))

    def test_rejects_breeding_accepts_family_with_no_matching_species(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["apiary"]["breeding_accepts_family"] = "no_such_family"
        errors = cv.validate(d)
        self.assertTrue(any("breeding_accepts_family" in e for e in errors))

    def test_rejects_empty_breeding_accepts_family_string(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["apiary"]["breeding_accepts_family"] = ""
        errors = cv.validate(d)
        self.assertTrue(any("breeding_accepts_family" in e for e in errors))

    def test_rejects_breeding_accepts_family_on_a_non_farm_building(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["family"] = "alpha"
        d["buildings"]["genetics_lab_building"]["breeding_accepts_family"] = "alpha"
        errors = cv.validate(d)
        self.assertTrue(any("breeding_accepts_family" in e and "breeding_slots" in e for e in errors))

    def test_accepts_valid_breeding_accepts_family(self):
        d = copy.deepcopy(self.data)
        d["species"]["critter_a"]["family"] = "alpha"
        d["buildings"]["apiary"]["breeding_accepts_family"] = "alpha"
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_non_boolean_genetics_lab(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["genetics_lab_building"]["genetics_lab"] = "yes"
        errors = cv.validate(d)
        self.assertTrue(any("genetics_lab" in e for e in errors))

    def test_rejects_non_boolean_breeding_station(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["crossing_station"]["breeding_station"] = "yes"
        errors = cv.validate(d)
        self.assertTrue(any("breeding_station" in e for e in errors))

    def test_accepts_new_breeding_upgrade_types_on_a_farm(self):
        # electric_apiary в фикстуре несёт breeding_output/breeding_immortality,
        # crossing_station несёт breeding_mutation_chance - просто проверяем, что
        # валидатор принимает все три без ошибок (полное покрытие типа - в
        # test_rejects_unknown_upgrade_type/остальных тестах типов выше).
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_rejects_breeding_output_on_a_non_farm_building(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["gen"]["upgrades"] = [
            {"id": "gen_output", "name": "x", "description": "x", "type": "breeding_output", "value": 2, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("breeding_output" in e and "только у ферм" in e for e in errors))

    def test_rejects_breeding_mutation_chance_on_a_building_without_the_breeding_station_flag(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["gen"]["upgrades"] = [
            {"id": "gen_mutation", "name": "x", "description": "x", "type": "breeding_mutation_chance", "value": 2, "requires_tech": None, "cost": {}},
        ]
        errors = cv.validate(d)
        self.assertTrue(any("breeding_mutation_chance" in e and "breeding_station" in e for e in errors))

    def test_rejects_mutation_chances_summing_over_one_for_the_same_pair(self):
        d = copy.deepcopy(self.data)
        # Фикстурная мутация уже занимает 0.5 у пары {critter_a} - добавляем
        # вторую на 0.6, суммарно 1.1 > 1.
        d["mutations"].append({
            "parent_a": "critter_a", "parent_b": "critter_a",
            "requires_tech": None, "requires_building": None,
            "chance": 0.6, "outcomes": [{"species_id": "critter_b", "weight": 1}],
        })
        errors = cv.validate(d)
        self.assertTrue(any("суммарный chance" in e for e in errors))

    def test_rejects_spawn_species_referencing_unknown_species(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["gather_a"]["spawn_species"] = "no_such_species"
        errors = cv.validate(d)
        self.assertTrue(any("spawn_species" in e for e in errors))

    def test_accepts_manual_action_with_empty_gain_when_spawn_species_is_set(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["gather_a"]["gain"] = {}
        d["manual_actions"]["gather_a"]["spawn_species"] = "critter_a"
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_manual_action_with_empty_gain_and_no_spawn_species(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["gather_a"]["gain"] = {}
        errors = cv.validate(d)
        self.assertTrue(any("ничего не производит" in e for e in errors))

    def test_rejects_genetics_settings_with_bad_cost(self):
        d = copy.deepcopy(self.data)
        d["genetics"]["synthesis_cost"] = {"no_such_resource": 1}
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))


class IconFieldTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_valid_icon_accepted(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["icon"] = {"grid": ["A.", ".A"], "palette": {"A": "#123456"}}
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_icon_grid_char_missing_from_palette_rejected(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["icon"] = {"grid": ["AB", ".A"], "palette": {"A": "#123456"}}
        errors = cv.validate(d)
        self.assertTrue(any("icon" in e for e in errors))

    def test_icon_uneven_row_lengths_rejected(self):
        d = copy.deepcopy(self.data)
        d["resources"][0]["icon"] = {"grid": ["AAA", "A"], "palette": {"A": "#123456"}}
        errors = cv.validate(d)
        self.assertTrue(any("icon" in e for e in errors))

    def test_missing_icon_is_fine(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building].pop("icon", None)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_mob_icon_reuses_same_check_as_resources(self):
        d = copy.deepcopy(self.data)
        d["mobs"]["test_mob"]["icon"] = {"grid": ["AB", ".A"], "palette": {"A": "#123456"}}
        errors = cv.validate(d)
        self.assertTrue(any("icon" in e for e in errors))


class BossTurnLimitValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_turn_limit_on_boss_accepted(self):
        d = copy.deepcopy(self.data)
        d["mobs"]["test_boss"]["turn_limit"] = 10
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_turn_limit_on_non_boss_rejected(self):
        d = copy.deepcopy(self.data)
        d["mobs"]["test_mob"]["turn_limit"] = 10
        errors = cv.validate(d)
        self.assertTrue(any("turn_limit" in e for e in errors))

    def test_turn_limit_must_be_positive_int(self):
        d = copy.deepcopy(self.data)
        d["mobs"]["test_boss"]["turn_limit"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("turn_limit" in e for e in errors))
        d["mobs"]["test_boss"]["turn_limit"] = 1.5
        errors = cv.validate(d)
        self.assertTrue(any("turn_limit" in e for e in errors))


class CombatMovesValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_fixture_combat_moves_and_unlocks_are_valid(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_move_missing_name_or_description_rejected(self):
        d = copy.deepcopy(self.data)
        del d["combat_moves"]["power_strike"]["name"]
        errors = cv.validate(d)
        self.assertTrue(any("power_strike" in e for e in errors))

    def test_move_non_positive_damage_multiplier_rejected(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["power_strike"]["damage_multiplier"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("damage_multiplier" in e for e in errors))

    def test_move_negative_cooldown_rejected(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["power_strike"]["cooldown_turns"] = -1
        errors = cv.validate(d)
        self.assertTrue(any("cooldown_turns" in e for e in errors))

    def test_unlocks_moves_unknown_id_rejected(self):
        d = copy.deepcopy(self.data)
        d["tools"]["sword"]["tiers"][2]["unlocks_moves"] = ["no_such_move"]
        errors = cv.validate(d)
        self.assertTrue(any("unlocks_moves" in e for e in errors))

    def test_unlocks_moves_on_non_weapon_tool_rejected(self):
        d = copy.deepcopy(self.data)
        d["tools"]["pickaxe"]["tiers"][1]["unlocks_moves"] = ["power_strike"]
        errors = cv.validate(d)
        self.assertTrue(any("unlocks_moves" in e for e in errors))

    def test_invalid_mode_value_rejected(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["power_strike"]["mode"] = "not_a_real_mode"
        errors = cv.validate(d)
        self.assertTrue(any("power_strike" in e and "mode" in e for e in errors))

    def test_multiplier_mode_rejects_percent_fields(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["power_strike"]["percent"] = 0.5
        errors = cv.validate(d)
        self.assertTrue(any("percent" in e for e in errors))

    def test_percent_max_hp_mode_requires_percent(self):
        d = copy.deepcopy(self.data)
        move = d["combat_moves"]["pct_max_move"]
        del move["percent"]
        errors = cv.validate(d)
        self.assertTrue(any("pct_max_move" in e and "percent" in e for e in errors))

    def test_percent_mode_percent_out_of_range_rejected(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["pct_max_move"]["percent"] = 1.5
        errors = cv.validate(d)
        self.assertTrue(any("percent" in e for e in errors))
        d["combat_moves"]["pct_max_move"]["percent"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("percent" in e for e in errors))

    def test_percent_mode_rejects_damage_multiplier_and_execute_threshold(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["pct_remaining_move"]["damage_multiplier"] = 2
        errors = cv.validate(d)
        self.assertTrue(any("damage_multiplier" in e for e in errors))
        d2 = copy.deepcopy(self.data)
        d2["combat_moves"]["pct_remaining_move"]["execute_threshold_percent"] = 0.5
        errors2 = cv.validate(d2)
        self.assertTrue(any("execute_threshold_percent" in e for e in errors2))

    def test_execute_mode_requires_threshold(self):
        d = copy.deepcopy(self.data)
        del d["combat_moves"]["execute_move"]["execute_threshold_percent"]
        errors = cv.validate(d)
        self.assertTrue(any("execute_move" in e and "execute_threshold_percent" in e for e in errors))

    def test_execute_mode_threshold_out_of_range_rejected(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["execute_move"]["execute_threshold_percent"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("execute_threshold_percent" in e for e in errors))

    def test_execute_mode_rejects_percent_field(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["execute_move"]["percent"] = 0.5
        errors = cv.validate(d)
        self.assertTrue(any("percent" in e for e in errors))

    def test_execute_mode_optional_fallback_multiplier_must_be_positive(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["execute_move"]["damage_multiplier"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("damage_multiplier" in e for e in errors))

    def test_execute_mode_accepts_optional_fallback_multiplier(self):
        d = copy.deepcopy(self.data)
        d["combat_moves"]["execute_move"]["damage_multiplier"] = 1.5
        errors = cv.validate(d)
        self.assertEqual(errors, [])


class BreakdownTypesAndToolDurabilityValidationTests(unittest.TestCase):
    """buildings.*.breakdown_types + верхнеуровневый breakdown_types (см.
    tests/fixtures.py: BREAKDOWN_TYPES["worn_gear"], BUILDINGS["flaky_typed"]) +
    tools.*.tiers[].max_durability/repair_cost (TOOLS["wrench"])."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def test_fixture_breakdown_types_and_durable_tool_are_valid(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_missing_breakdown_types_section_is_fine(self):
        # Раздел необязателен - старый content.json без него (или контент, где
        # ни у одной постройки breakdown_types не задан) должен проходить как раньше.
        d = copy.deepcopy(self.data)
        del d["breakdown_types"]
        for b in d["buildings"].values():
            b.pop("breakdown_types", None)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_building_breakdown_types_rejects_unknown_id(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["flaky_typed"]["breakdown_types"] = ["no_such_type"]
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный вид поломки" in e for e in errors))

    def test_building_breakdown_types_rejects_empty_list(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["flaky_typed"]["breakdown_types"] = []
        errors = cv.validate(d)
        self.assertTrue(any("breakdown_types" in e for e in errors))

    def test_breakdown_type_missing_name_or_description_rejected(self):
        d = copy.deepcopy(self.data)
        del d["breakdown_types"]["worn_gear"]["name"]
        errors = cv.validate(d)
        self.assertTrue(any("worn_gear" in e and "название" in e for e in errors))

    def test_breakdown_type_requires_non_empty_required_tools(self):
        d = copy.deepcopy(self.data)
        d["breakdown_types"]["worn_gear"]["required_tools"] = []
        errors = cv.validate(d)
        self.assertTrue(any("required_tools" in e for e in errors))

    def test_breakdown_type_rejects_unknown_tool_in_required_tools(self):
        d = copy.deepcopy(self.data)
        d["breakdown_types"]["worn_gear"]["required_tools"] = ["no_such_tool"]
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный инструмент" in e for e in errors))

    def test_tool_first_tier_cannot_have_max_durability(self):
        d = copy.deepcopy(self.data)
        d["tools"]["wrench"]["tiers"][0]["max_durability"] = 2
        errors = cv.validate(d)
        self.assertTrue(any("max_durability" in e for e in errors))

    def test_max_durability_must_be_a_positive_integer(self):
        d = copy.deepcopy(self.data)
        d["tools"]["wrench"]["tiers"][1]["max_durability"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("max_durability" in e for e in errors))

    def test_max_durability_requires_repair_cost(self):
        d = copy.deepcopy(self.data)
        del d["tools"]["wrench"]["tiers"][1]["repair_cost"]
        errors = cv.validate(d)
        self.assertTrue(any("repair_cost" in e and "обязателен" in e for e in errors))

    def test_repair_cost_without_max_durability_rejected(self):
        d = copy.deepcopy(self.data)
        d["tools"]["pickaxe"]["tiers"][1]["repair_cost"] = {"raw_a": 1}
        errors = cv.validate(d)
        self.assertTrue(any("repair_cost" in e and "имеет смысл только вместе" in e for e in errors))

    def test_repair_cost_rejects_unknown_resource(self):
        d = copy.deepcopy(self.data)
        d["tools"]["wrench"]["tiers"][1]["repair_cost"] = {"no_such_resource": 1}
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))

    def test_breakdown_immunity_upgrade_allowed_on_any_building_not_just_farms(self):
        # В отличие от breeding_*, breakdown_immunity НЕ ограничен breeding_slots -
        # BUILDINGS["flaky_typed"] (обычная постройка) уже несёт его в фикстуре.
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_breakdown_immunity_value_must_be_a_positive_number(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["flaky_typed"]["upgrades"][0]["value"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("breakdown_immunity" in e for e in errors))

    def test_action_duration_reduction_value_must_be_between_zero_and_one(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["mechanism_a"]["upgrades"][1]["value"] = 1
        errors = cv.validate(d)
        self.assertTrue(any("action_duration_reduction" in e for e in errors))

    def test_action_duration_reduction_requires_a_dependent_multiturn_action(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["mechanism_a_delayed_action"]["requires_building"] = None
        # Иначе действие унаследовало бы mechanism_a от своей локации mechanism_a_loc
        # (см. engine.action_mechanism_building) и всё ещё считалось бы зависимым.
        d["manual_actions"]["mechanism_a_delayed_action"]["location"] = "yard"
        errors = cv.validate(d)
        self.assertTrue(any("action_duration_reduction" in e and "многоходовое" in e for e in errors))

    def test_fixture_research_turns_and_research_speed_upgrade_are_valid(self):
        errors = cv.validate(copy.deepcopy(self.data))
        self.assertEqual(errors, [])

    def test_research_turns_null_is_fine(self):
        d = copy.deepcopy(self.data)
        d["techs"]["tech_tier2_slow"]["research_turns"] = None
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_research_turns_must_be_an_integer_at_least_two(self):
        d = copy.deepcopy(self.data)
        d["techs"]["tech_tier2_slow"]["research_turns"] = 1
        errors = cv.validate(d)
        self.assertTrue(any("research_turns" in e for e in errors))

    def test_research_speed_value_must_be_a_positive_number(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["lab2"]["upgrades"][0]["value"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("research_speed" in e for e in errors))

    def test_research_speed_requires_a_lab_tier_building(self):
        d = copy.deepcopy(self.data)
        del d["buildings"]["lab2"]["lab_tier"]
        errors = cv.validate(d)
        self.assertTrue(any("research_speed" in e and "лабораторий" in e for e in errors))


class CraftingPatternValidationTests(unittest.TestCase):
    """locations.*.crafting (сетка механизма) + manual_actions.*.pattern (узор).

    Наличие pattern у действия И есть переключатель «крафтится на сетке»: без него
    действие остаётся обычной карточкой, даже если его локация - механизм.
    """

    LOC = "workshop_loc"

    def setUp(self):
        self.data = fixtures.as_content_dict()
        self.data["locations"][self.LOC]["crafting"] = {"width": 3, "height": 3}

    def pattern(self, rows, key):
        return {"rows": rows, "key": key}

    def apply(self, data, aid, rows, key):
        """Ставит узор и приводит cost к нему: одна ячейка = одна единица."""
        action = data["manual_actions"][aid]
        action["pattern"] = self.pattern(rows, key)
        cost = {}
        for row in rows:
            for ch in row:
                rid = key.get(ch)
                if rid:
                    cost[rid] = cost.get(rid, 0) + 1
        action["cost"] = cost
        return action

    def test_accepts_a_valid_pattern(self):
        d = copy.deepcopy(self.data)
        self.apply(d, "craft_mid", ["AA.", "AA.", "..."], {"A": "raw_a"})
        self.assertEqual(cv.validate(d), [])

    def test_rejects_pattern_that_disagrees_with_cost(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["AA.", "...", "..."], {"A": "raw_a"})
        d["manual_actions"]["craft_mid"]["cost"] = {"raw_a": 4}
        errors = cv.validate(d)
        self.assertTrue(any("обязаны совпадать" in e for e in errors))

    def test_rejects_pattern_missing_a_resource_that_cost_has(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_final"]["pattern"] = self.pattern(["AA.", "...", "..."], {"A": "mid"})
        d["manual_actions"]["craft_final"]["cost"] = {"mid": 2, "raw_b": 1}
        errors = cv.validate(d)
        self.assertTrue(any("обязаны совпадать" in e for e in errors))

    def test_action_without_pattern_stays_a_plain_card(self):
        """Механизм с сеткой не обязывает КАЖДОЕ своё действие быть сеточным."""
        self.assertEqual(cv.validate(copy.deepcopy(self.data)), [])

    def test_rejects_pattern_when_location_has_no_grid(self):
        d = copy.deepcopy(self.data)
        del d["locations"][self.LOC]["crafting"]
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["A..", "...", "..."], {"A": "raw_a"})
        errors = cv.validate(d)
        self.assertTrue(any("нет крафт-сетки" in e for e in errors))

    def test_rejects_pattern_of_a_different_size_than_the_grid(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["AA", ".."], {"A": "raw_a"})
        errors = cv.validate(d)
        self.assertTrue(any("не совпадает с сеткой" in e for e in errors))

    def test_rejects_rows_of_unequal_length(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["AA.", "..", "..."], {"A": "raw_a"})
        errors = cv.validate(d)
        self.assertTrue(any("одной длины" in e for e in errors))

    def test_rejects_unknown_resource_in_key(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["A..", "...", "..."], {"A": "no_such_res"})
        errors = cv.validate(d)
        self.assertTrue(any("неизвестный ресурс" in e for e in errors))

    def test_rejects_symbol_missing_from_key(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["AB.", "...", "..."], {"A": "raw_a"})
        errors = cv.validate(d)
        self.assertTrue(any("не описан в key" in e for e in errors))

    def test_rejects_unused_key_symbol(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(
            ["A..", "...", "..."], {"A": "raw_a", "B": "raw_b"}
        )
        errors = cv.validate(d)
        self.assertTrue(any("не встречаются в rows" in e for e in errors))

    def test_rejects_empty_pattern(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["...", "...", "..."], {"A": "raw_a"})
        errors = cv.validate(d)
        self.assertTrue(any("узор пуст" in e or "не встречаются в rows" in e for e in errors))

    def test_rejects_dot_as_a_key_symbol(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["A..", "...", "..."], {"A": "raw_a", ".": "raw_b"})
        errors = cv.validate(d)
        self.assertTrue(any("ОДНИМ символом" in e for e in errors))

    def test_rejects_two_actions_with_the_same_shape_in_one_location(self):
        d = copy.deepcopy(self.data)
        same = self.pattern(["AA.", "...", "..."], {"A": "raw_a"})
        d["manual_actions"]["craft_mid"]["pattern"] = copy.deepcopy(same)
        d["manual_actions"]["craft_lucky"]["pattern"] = copy.deepcopy(same)
        errors = cv.validate(d)
        self.assertTrue(any("та же форма узора" in e for e in errors))

    def test_shifted_pattern_counts_as_the_same_shape(self):
        """Пустые края обрезаются, поэтому сдвиг по сетке - не новый рецепт."""
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["AA.", "...", "..."], {"A": "raw_a"})
        d["manual_actions"]["craft_lucky"]["pattern"] = self.pattern(["...", ".AA", "..."], {"A": "raw_a"})
        errors = cv.validate(d)
        self.assertTrue(any("та же форма узора" in e for e in errors))

    def test_same_shape_of_different_resources_is_allowed(self):
        d = copy.deepcopy(self.data)
        self.apply(d, "craft_mid", ["AA.", "...", "..."], {"A": "raw_a"})
        self.apply(d, "craft_lucky", ["AA.", "...", "..."], {"A": "raw_b"})
        self.assertEqual(cv.validate(d), [])

    def test_same_shape_in_different_locations_is_allowed(self):
        d = copy.deepcopy(self.data)
        d["locations"]["mechanism_a_loc"]["crafting"] = {"width": 3, "height": 3}
        self.apply(d, "craft_mid", ["AA.", "...", "..."], {"A": "raw_a"})
        self.apply(d, "mechanism_a_action", ["AA.", "...", "..."], {"A": "raw_a"})
        self.assertEqual(cv.validate(d), [])

    def test_mirror_flag_makes_the_mirrored_shape_taken_too(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern"] = self.pattern(["AB.", "...", "..."], {"A": "raw_a", "B": "raw_b"})
        d["manual_actions"]["craft_mid"]["pattern_mirror"] = True
        d["manual_actions"]["craft_lucky"]["pattern"] = self.pattern(["BA.", "...", "..."], {"A": "raw_a", "B": "raw_b"})
        errors = cv.validate(d)
        self.assertTrue(any("та же форма узора" in e for e in errors))

    def test_rejects_mirror_flag_without_a_pattern(self):
        d = copy.deepcopy(self.data)
        d["manual_actions"]["craft_mid"]["pattern_mirror"] = True
        errors = cv.validate(d)
        self.assertTrue(any("pattern_mirror" in e for e in errors))

    def test_rejects_bad_grid_size(self):
        for width in (0, 10, "3", 2.5):
            with self.subTest(width=width):
                d = copy.deepcopy(self.data)
                d["locations"][self.LOC]["crafting"]["width"] = width
                errors = cv.validate(d)
                self.assertTrue(any("crafting.width" in e for e in errors))

    def test_accepts_batch_flag(self):
        d = copy.deepcopy(self.data)
        d["locations"][self.LOC]["crafting"]["batch"] = True
        errors = cv.validate(d)
        self.assertFalse(any("crafting.batch" in e for e in errors))

    def test_rejects_non_boolean_batch_flag(self):
        for value in ("yes", 1, None):
            with self.subTest(value=value):
                d = copy.deepcopy(self.data)
                d["locations"][self.LOC]["crafting"]["batch"] = value
                errors = cv.validate(d)
                self.assertTrue(any("crafting.batch" in e for e in errors))

    def test_rejects_crafting_that_is_not_an_object(self):
        d = copy.deepcopy(self.data)
        d["locations"][self.LOC]["crafting"] = 3
        errors = cv.validate(d)
        self.assertTrue(any("crafting" in e for e in errors))

    def test_pattern_shape_trims_empty_edges(self):
        shape = cv.pattern_shape({"rows": ["...", ".A.", "..."], "key": {"A": "raw_a"}})
        self.assertEqual(shape, (("raw_a",),))

    def test_pattern_shape_of_an_empty_grid_is_empty(self):
        self.assertEqual(cv.pattern_shape({"rows": ["...", "...", "..."], "key": {"A": "raw_a"}}), ())

    def test_mirror_shape_flips_horizontally(self):
        shape = cv.pattern_shape({"rows": ["AB.", "...", "..."], "key": {"A": "raw_a", "B": "raw_b"}})
        self.assertEqual(cv.mirror_shape(shape), (("raw_b", "raw_a"),))


class MechanismAppearanceValidationTests(unittest.TestCase):
    """locations.*.appearance - оформление модалки механизма (tools/mechanism_fx.js)."""

    ICON = {"grid": ["AB", "BA"], "palette": {"A": "#111111", "B": "#eeeeee"}}

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def _errors_with(self, appearance):
        d = copy.deepcopy(self.data)
        d["locations"]["grid_loc"]["appearance"] = appearance
        return [e for e in cv.validate(d) if "appearance" in e]

    def test_full_appearance_is_valid(self):
        self.assertEqual(self._errors_with({
            "texture": self.ICON, "texture_dim": 0.6, "progress_icon": self.ICON, "progress_fill": "up",
            "motion": "strike", "particles": "sawdust", "sound": "mechanism_grid_loc.wav",
        }), [])

    def test_empty_appearance_is_valid(self):
        self.assertEqual(self._errors_with({}), [])

    def test_rejects_unknown_motion_particles_and_fill(self):
        errors = self._errors_with({"motion": "wobble", "particles": "confetti", "progress_fill": "diagonal"})
        self.assertTrue(any("motion" in e and "wobble" in e for e in errors))
        self.assertTrue(any("particles" in e and "confetti" in e for e in errors))
        self.assertTrue(any("progress_fill" in e for e in errors))

    def test_rejects_broken_texture_under_its_own_field_name(self):
        errors = self._errors_with({"texture": {"grid": ["AB"], "palette": {"A": "#000000"}}})
        self.assertTrue(any("appearance.texture" in e and "'B'" in e for e in errors))

    def test_rejects_texture_dim_out_of_range(self):
        for dim in (-0.1, 0.95, True, "0.5"):
            self.assertTrue(self._errors_with({"texture_dim": dim}), dim)

    def test_rejects_unknown_key_and_bad_sound(self):
        errors = self._errors_with({"colour": "#ff0000", "sound": "../evil.exe"})
        self.assertTrue(any("colour" in e for e in errors))
        self.assertTrue(any("sound" in e for e in errors))

    def test_appearance_without_crafting_is_not_an_error(self):
        # Снятая галочка «Крафт-сетка» в редакторе оставляет оформление лежать.
        d = copy.deepcopy(self.data)
        loc = next(l for l in d["locations"].values() if not l.get("crafting"))
        loc["appearance"] = {"motion": "spin"}
        self.assertEqual([e for e in cv.validate(d) if "appearance" in e], [])

    def test_variant_ids_match_mechanism_fx_js(self):
        # id вариантов продублированы руками (JS не импортирует Python) - держать в паре.
        import re
        js = (ROOT / "tools" / "mechanism_fx.js").read_text(encoding="utf-8")

        def js_keys(const):
            block = re.search(rf"const {const} = \{{(.*?)\n\}};", js, re.S).group(1)
            return set(re.findall(r"^  ([a-z_]+):", block, re.M))
        self.assertEqual(js_keys("MECH_FX_MOTIONS"), set(cv.MECHANISM_MOTIONS))
        self.assertEqual(js_keys("MECH_FX_PARTICLES"), set(cv.MECHANISM_PARTICLES))
        self.assertEqual(js_keys("MECH_FX_FILLS"), set(cv.MECHANISM_PROGRESS_FILLS))


if __name__ == "__main__":
    unittest.main()
