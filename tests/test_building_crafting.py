"""
Постройки собираются в механизмах, а не во вкладке «Построить»: buildings.*.location
(+ pattern на верстаке) и сборщик crafting.stacks - форма не важна, в ячейке стопка,
рецепт узнаётся по набору стопок (game/engine.py: match_grid_target/_normalize_stacks,
tools/web_ui.py: crafting_view/craft_grid_view/building_sources).

Работают на фикстуре (tests/fixtures.py) с временно добавленной локацией-сборщиком
"asm_loc"; RealContentBuildingsTests - на настоящем game/content.json.

Запуск: python -m unittest tests.test_building_crafting -v
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fixtures  # noqa: E402
import content_validation as cv  # noqa: E402
import web_ui  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402

ASM = "asm_loc"
EMPTY = [None] * 6


def stacks(*pairs):
    """Раскладка сборщика 3x2: стопки по порядку, остальные ячейки пусты."""
    cells = [{"id": rid, "amount": amount} for rid, amount in pairs]
    return cells + [None] * (6 - len(cells))


class AssemblerFixtureCase(unittest.TestCase):
    """Фикстура + сборщик asm_loc: gen/slot_hub/furnace собираются стопками,
    factory - узором на верстаке grid_loc, asm_action - деталь в сборщике."""

    def setUp(self):
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        patches = [
            mock.patch.dict(engine.LOCATIONS, {ASM: {
                "name": "Тестовый сборщик", "requires_tech": None,
                "crafting": {"width": 3, "height": 2, "stacks": True},
            }}),
            mock.patch.dict(engine.MANUAL_ACTIONS, {"asm_action": {
                "name": "Собрать деталь", "requires_tech": None, "location": ASM,
                "cost": {"raw_a": 2, "raw_b": 1}, "gain": {"mid": 1},
            }}),
            mock.patch.dict(engine.BUILDINGS["gen"], {"location": ASM}),
            mock.patch.dict(engine.BUILDINGS["slot_hub"], {"location": ASM}),
            mock.patch.dict(engine.BUILDINGS["furnace"], {"location": ASM}),
            mock.patch.dict(engine.BUILDINGS["factory"], {
                "location": "grid_loc", "pattern": {"rows": ["AA", "A."], "key": {"A": "mid"}},
            }),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class StacksMatchTests(AssemblerFixtureCase):
    def test_exact_stack_builds_the_building(self):
        self.assertEqual(engine.match_grid_target(GameState(), ASM, stacks(("raw_b", 5))), ("building", "gen"))

    def test_stacks_of_one_resource_add_up_across_cells(self):
        self.assertEqual(
            engine.match_grid_target(GameState(), ASM, stacks(("raw_b", 2), ("raw_b", 3))), ("building", "gen"),
        )

    def test_wrong_amount_or_extra_resource_does_not_match(self):
        s = GameState()
        self.assertIsNone(engine.match_grid_target(s, ASM, stacks(("raw_b", 4))))
        self.assertIsNone(engine.match_grid_target(s, ASM, stacks(("raw_b", 5), ("raw_a", 1))))

    def test_manual_action_in_assembler_matches_by_its_cost(self):
        self.assertTrue(engine.is_grid_action("asm_action"))
        self.assertEqual(
            engine.match_grid_target(GameState(), ASM, stacks(("raw_b", 1), ("raw_a", 2))),
            ("manual_action", "asm_action"),
        )

    def test_multi_stage_building_matches_only_its_current_stage(self):
        s = GameState()
        self.assertEqual(engine.match_grid_target(s, ASM, stacks(("raw_a", 2))), ("building", "furnace"))
        s.construction_progress["furnace"] = 1
        self.assertIsNone(engine.match_grid_target(s, ASM, stacks(("raw_a", 2))))
        self.assertEqual(engine.match_grid_target(s, ASM, stacks(("raw_b", 1))), ("building", "furnace"))

    def test_malformed_layout_is_rejected(self):
        s = GameState()
        self.assertIsNone(engine.match_grid_target(s, ASM, stacks(("no_such_resource", 1))))
        self.assertIsNone(engine.match_grid_target(s, ASM, stacks(("raw_b", -5))))
        self.assertIsNone(engine.match_grid_target(s, ASM, stacks(("raw_b", True))))
        self.assertIsNone(engine.match_grid_target(s, ASM, [{"id": "raw_b", "amount": 5}]))  # не та длина
        self.assertIsNone(engine.match_grid_target(s, ASM, EMPTY))
        self.assertIsNone(engine.match_grid_target(s, "no_such_location", EMPTY))


class ShapedBuildingTests(AssemblerFixtureCase):
    def test_building_pattern_matches_on_the_workbench_once_unlocked(self):
        placed = ["mid", "mid", "mid", None]
        self.assertIsNone(engine.match_grid_target(GameState(), "grid_loc", placed))  # factory требует tech1
        self.assertEqual(engine.match_grid_target(GameState(techs={"tech1"}), "grid_loc", placed), ("building", "factory"))

    def test_stack_cells_on_a_shaped_grid_never_match(self):
        placed = [{"id": "mid", "amount": 1}] * 3 + [None]
        self.assertIsNone(engine.match_grid_target(GameState(techs={"tech1"}), "grid_loc", placed))


class BuildingRecipeKeyTests(AssemblerFixtureCase):
    def test_key_round_trip(self):
        key = engine.grid_recipe_key("building", "gen")
        self.assertEqual(key, "building:gen")
        self.assertEqual(engine.parse_grid_recipe_key(key), ("building", "gen"))

    def test_building_recipe_can_be_pinned_only_if_it_is_built_somewhere(self):
        s = GameState()
        self.assertTrue(engine.toggle_pinned_recipe(s, "building:gen").ok)
        self.assertIn("building:gen", s.pinned_recipes)
        self.assertFalse(engine.toggle_pinned_recipe(s, "building:flaky").ok)  # без location
        self.assertFalse(engine.toggle_pinned_recipe(s, "building:no_such").ok)

    def test_buildings_cannot_be_ordered(self):
        s = GameState()
        s.resources["raw_b"] = 50
        result = engine.order_recipe(s, "building:gen", 2)
        self.assertFalse(result.ok)
        self.assertIn("не заказываются", result.message)
        self.assertIsNotNone(engine.check_order(s, "building:gen", 1))
        with self.assertRaises(KeyError):
            web_ui.order_preview_view(s, "building:gen", 1)


class CraftingViewTests(AssemblerFixtureCase):
    def test_assembler_entry_lists_building_and_action_recipes_as_stacks(self):
        s = GameState()
        s.resources["raw_b"] = 5
        entry = next(e for e in web_ui.crafting_view(s) if e["location_id"] == ASM)
        self.assertTrue(entry["stacks"])
        gen = next(r for r in entry["recipes"] if r["action_id"] == "building:gen")
        self.assertEqual(gen["kind"], "building")
        self.assertIsNone(gen["pattern"])
        self.assertEqual(gen["stack_cost"], [{"id": "raw_b", "name": fixtures.RESOURCE_NAMES["raw_b"], "amount": 5}])
        self.assertEqual(gen["gain"][0]["id"], "building:gen")
        self.assertTrue(gen["enough"])
        action = next(r for r in entry["recipes"] if r["action_id"] == "asm_action")
        self.assertEqual({c["id"]: c["amount"] for c in action["stack_cost"]}, {"raw_a": 2, "raw_b": 1})

    def test_multi_stage_recipe_shows_the_current_stage(self):
        s = GameState()
        s.construction_progress["furnace"] = 1
        entry = next(e for e in web_ui.crafting_view(s) if e["location_id"] == ASM)
        furnace = next(r for r in entry["recipes"] if r["action_id"] == "building:furnace")
        self.assertEqual((furnace["stage_number"], furnace["stage_total"]), (2, 3))
        self.assertEqual(furnace["stage_description"], "установить радиатор")
        self.assertEqual({c["id"]: c["amount"] for c in furnace["stack_cost"]}, {"raw_b": 1})

    def test_assembler_opens_as_a_button(self):
        self.assertTrue(web_ui._location_opens_as_button(ASM))


class CraftGridBuildTests(AssemblerFixtureCase):
    def test_crafting_a_building_places_it_and_spends_a_turn(self):
        s = GameState()
        s.resources["raw_b"] = 5
        turn = s.turn
        result = web_ui.craft_grid_view(s, ASM, stacks(("raw_b", 5)), 1)
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(result["action_id"], "building:gen")
        self.assertEqual(result["message"], "Построено: Генератор.")
        self.assertEqual(s.buildings.get("gen"), 1)
        self.assertEqual(s.resources.get("raw_b", 0), 0)
        self.assertEqual(s.turn, turn + 1)

    def test_max_count_stops_the_second_copy(self):
        s = GameState()
        s.resources["raw_a"] = 10
        self.assertTrue(web_ui.craft_grid_view(s, ASM, stacks(("raw_a", 1)), 1)["ok"])
        entry = next(e for e in web_ui.crafting_view(s) if e["location_id"] == ASM)
        hub = next(r for r in entry["recipes"] if r["action_id"] == "building:slot_hub")
        self.assertTrue(hub["limit_reached"])
        result = web_ui.craft_grid_view(s, ASM, stacks(("raw_a", 1)), 1)
        self.assertFalse(result["ok"])
        self.assertIn("больше 1", result["message"])
        self.assertEqual(s.buildings["slot_hub"], 1)

    def test_multi_stage_building_is_assembled_stage_by_stage(self):
        s = GameState()
        s.resources.update({"raw_a": 10, "raw_b": 10})
        for layout in (stacks(("raw_a", 2)), stacks(("raw_b", 1)), stacks(("raw_a", 1), ("raw_b", 1))):
            self.assertNotIn("furnace", s.buildings)
            result = web_ui.craft_grid_view(s, ASM, layout, 1)
            self.assertTrue(result["ok"], result["message"])
        self.assertEqual(s.buildings["furnace"], 1)
        self.assertEqual(result["message"], "Построено: Печь.")


class SourcesTests(AssemblerFixtureCase):
    def test_building_has_a_reference_card_with_its_stacks(self):
        (src,) = web_ui.resource_sources("building:gen")
        self.assertEqual(src["kind"], "grid_craft")
        self.assertTrue(src["stacks"])
        self.assertTrue(src["building_recipe"])
        self.assertEqual(src["location_id"], ASM)
        self.assertEqual(src["pin_key"], "building:gen")
        self.assertEqual([c["id"] for c in src["stack_cost"]], ["raw_b"])

    def test_shaped_building_card_carries_its_pattern(self):
        (src,) = web_ui.resource_sources("building:factory")
        self.assertFalse(src["stacks"])
        self.assertEqual(src["pattern"]["key"], {"A": "mid"})

    def test_summary_lists_buildings_that_can_be_assembled(self):
        ids = {item["id"]: item for item in web_ui.sources_summary_view()}
        self.assertTrue(ids["building:gen"]["building"])
        self.assertNotIn("building:flaky", ids)  # собрать негде
        self.assertTrue(web_ui.is_known_source_id("building:gen"))
        self.assertFalse(web_ui.is_known_source_id("building:no_such"))

    def test_manual_action_in_assembler_is_a_grid_craft_source(self):
        src = next(s for s in web_ui.resource_sources("mid") if s.get("action_id") == "asm_action")
        self.assertEqual(src["kind"], "grid_craft")
        self.assertTrue(src["stacks"])

    def test_pinned_building_recipe_becomes_a_tile(self):
        s = GameState()
        engine.toggle_pinned_recipe(s, "building:gen")
        tiles = web_ui.pinned_recipes_view(s, web_ui.crafting_view(s), [])
        self.assertEqual(tiles[0]["action_id"], "building:gen")
        self.assertEqual(tiles[0]["gain"][0]["id"], "building:gen")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.as_content_dict()
        self.data["locations"][ASM] = {
            "name": "Сборщик", "requires_tech": None, "crafting": {"width": 3, "height": 2, "stacks": True},
        }
        self.data["buildings"]["gen"]["location"] = ASM

    def errors(self, data=None):
        return cv.validate(data or self.data)

    def test_assembler_building_is_valid(self):
        self.assertEqual(self.errors(), [])

    def test_location_without_grid_is_rejected(self):
        self.data["buildings"]["gen"]["location"] = "yard"
        self.assertTrue(any("нет крафт-сетки" in e for e in self.errors()))

    def test_pattern_in_assembler_is_rejected(self):
        self.data["buildings"]["gen"]["pattern"] = {"rows": ["A..", "..."], "key": {"A": "raw_b"}}
        self.assertTrue(any("узор (pattern) не нужен" in e for e in self.errors()))

    def test_equal_costs_in_one_assembler_are_rejected(self):
        d = copy.deepcopy(self.data)
        d["buildings"]["solar"] = copy.deepcopy(d["buildings"]["gen"])
        self.assertTrue(any("тот же набор стопок" in e for e in self.errors(d)))

    def test_workbench_building_needs_a_pattern_that_matches_cost(self):
        self.data["buildings"]["gen"]["location"] = "grid_loc"
        self.assertTrue(any("узора (pattern) нет" in e for e in self.errors()))
        self.data["buildings"]["gen"]["pattern"] = {"rows": ["AA", ".."], "key": {"A": "raw_b"}}
        self.assertTrue(any("одна ячейка это одна единица" in e for e in self.errors()))

    def test_multi_stage_building_only_in_assembler(self):
        self.data["buildings"]["furnace"]["location"] = "grid_loc"
        self.assertTrue(any("многоэтапную постройку" in e for e in self.errors()))

    def test_assembler_action_needs_no_pattern(self):
        self.data["manual_actions"]["asm_action"] = {
            "name": "Деталь", "requires_tech": None, "location": ASM,
            "cost": {"raw_a": 2}, "gain": {"mid": 1},
            "pattern": {"rows": ["A..", "..."], "key": {"A": "raw_a"}},
        }
        self.assertTrue(any("узор (pattern) не нужен" in e for e in self.errors()))

    def test_more_resources_than_cells_is_rejected(self):
        self.data["locations"][ASM]["crafting"] = {"width": 1, "height": 1, "stacks": True}
        self.data["buildings"]["gen"]["cost"] = {"raw_a": 1, "raw_b": 1}
        self.assertTrue(any("не разложить в 1 ячеек" in e for e in self.errors()))

    def test_converter_cannot_be_an_assembler(self):
        converter = self.data["locations"][cv._CONVERTER_LOCATION_ID]
        converter["crafting"] = {"width": 2, "height": 1, "stacks": True}
        self.assertTrue(any("у преобразователя" in e for e in self.errors()))


class RealContentBuildingsTests(unittest.TestCase):
    """Настоящий контент: вкладки «Построить» нет, значит каждую постройку обязано
    быть где собрать, а первый механизм - сборочный стол - собирается на верстаке с нуля."""

    def test_every_building_is_assembled_somewhere(self):
        missing = [bid for bid in engine.BUILDINGS if not web_ui.building_sources(bid)]
        self.assertEqual(missing, [])

    def test_assembly_table_is_crafted_on_the_workbench_from_scratch(self):
        s = GameState()
        on_workbench = engine.grid_buildings_at("workbench", s)
        self.assertIn("assembly_table", on_workbench)
        self.assertNotIn("assembly", engine.available_locations(s))
        s.resources.update({"wood": 3, "stone": 4})
        layout = ["wood", "wood", "wood", "stone", None, "stone", "stone", None, "stone"]
        self.assertEqual(engine.match_grid_target(s, "workbench", layout), ("building", "assembly_table"))
        self.assertTrue(engine.build(s, "assembly_table").ok)
        self.assertIn("assembly", engine.available_locations(s))


if __name__ == "__main__":
    unittest.main()
