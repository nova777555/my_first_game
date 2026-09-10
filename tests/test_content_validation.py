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

    def test_accepts_valid_unlocks_autocraft(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        any_resource = d["resources"][0]["id"]
        d["buildings"][any_building]["unlocks_autocraft"] = [any_resource]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

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

    def test_accepts_valid_autocraft_speed_and_slots(self):
        d = copy.deepcopy(self.data)
        any_building = next(iter(d["buildings"]))
        d["buildings"][any_building]["autocraft_speed"] = 8
        d["buildings"][any_building]["autocraft_slots"] = 1
        errors = cv.validate(d)
        self.assertEqual(errors, [])

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

    def test_rejects_negative_click_delay_on_mining(self):
        d = copy.deepcopy(self.data)
        any_ore = next(iter(d["mining_info"]))
        d["mining_info"][any_ore]["click_delay_ms"] = -50
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


class MiningQualityValidationTests(unittest.TestCase):
    """quality_tiers (mining_info[...].quality_tiers) - веса/множители/min_tool_tier
    для качества найденной жилы, зависящего от тира инструмента."""

    def setUp(self):
        self.data = fixtures.as_content_dict()

    def _quality_tiers(self, **overrides):
        tier = {"name": "Бедная", "weight": 1, "yield_multiplier": 0.5, "min_tool_tier": 1}
        tier.update(overrides)
        return [tier]

    def test_accepts_valid_quality_tiers(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"]["quality_tiers"] = [
            {"name": "Бедная", "weight": 1, "yield_multiplier": 0.5, "min_tool_tier": 1},
            {"name": "Богатая", "weight": 1, "yield_multiplier": 2, "min_tool_tier": 2},
        ]
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_missing_quality_tiers_is_fine(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"].pop("quality_tiers", None)
        errors = cv.validate(d)
        self.assertEqual(errors, [])

    def test_rejects_empty_quality_tiers_list(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"]["quality_tiers"] = []
        errors = cv.validate(d)
        self.assertTrue(any("quality_tiers" in e for e in errors))

    def test_rejects_non_positive_weight(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"]["quality_tiers"] = self._quality_tiers(weight=0)
        errors = cv.validate(d)
        self.assertTrue(any("weight" in e for e in errors))

    def test_rejects_non_positive_yield_multiplier(self):
        d = copy.deepcopy(self.data)
        d["mining_info"]["mineral"]["quality_tiers"] = self._quality_tiers(yield_multiplier=0)
        errors = cv.validate(d)
        self.assertTrue(any("yield_multiplier" in e for e in errors))

    def test_rejects_min_tool_tier_out_of_range(self):
        d = copy.deepcopy(self.data)
        # required_tool у mineral - "pickaxe", в фикстуре у него 3 тира
        d["mining_info"]["mineral"]["quality_tiers"] = self._quality_tiers(min_tool_tier=99)
        errors = cv.validate(d)
        self.assertTrue(any("min_tool_tier" in e for e in errors))

    def test_rejects_pool_with_no_tier_available_at_required_tier(self):
        d = copy.deepcopy(self.data)
        # required_tier у mineral - 1, но единственный тир качества требует тир 2 -
        # на пороге открытия добычи розыгрыш качества окажется пустым.
        d["mining_info"]["mineral"]["quality_tiers"] = self._quality_tiers(min_tool_tier=2)
        errors = cv.validate(d)
        self.assertTrue(any("не доступен на пороге открытия добычи" in e for e in errors))


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

    def test_rejects_genetic_trait_allele_with_non_positive_weight(self):
        d = copy.deepcopy(self.data)
        d["genetic_traits"]["lifespan"]["alleles"][0]["weight"] = 0
        errors = cv.validate(d)
        self.assertTrue(any("weight" in e for e in errors))

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


if __name__ == "__main__":
    unittest.main()
