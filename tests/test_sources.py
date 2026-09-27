"""
Тесты справочника источников (tools/web_ui.py: resource_sources/sources_summary_view) -
см. PLAN_grid_crafting.md, этап 5. Работают на самодостаточной тестовой фикстуре
(tests/fixtures.py), а не на реальном game/content.json.

Запуск: python -m unittest tests.test_sources -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
import web_ui  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402


class FixtureTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)


def _find(sources, **kwargs):
    for s in sources:
        if all(s.get(k) == v for k, v in kwargs.items()):
            return s
    return None


class GridCraftSourceTests(FixtureTestCase):
    def test_grid_recipe_reports_pattern_action_and_cost(self):
        sources = web_ui.resource_sources("grid_output")
        entry = _find(sources, kind="grid_craft", action_id="grid_line")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["location_id"], "grid_loc")
        self.assertEqual(entry["cost"], [{"id": "raw_a", "name": "Сырьё A", "amount": 2}])
        self.assertEqual(entry["amount"], 1)
        self.assertFalse(entry["pattern_mirror"])

    def test_grid_recipe_reports_pattern_and_mechanism_name(self):
        # Карточка рецепта рисует сам узор сеткой механизма - игрок должен видеть
        # целевую раскладку, а не только список ингредиентов.
        sources = web_ui.resource_sources("grid_output")
        entry = _find(sources, kind="grid_craft", action_id="grid_line")
        self.assertEqual(entry["pattern"], {"rows": ["AA", ".."], "key": {"A": "raw_a"}})
        self.assertEqual(entry["location_name"], "Тестовый верстак")

    def test_grid_recipe_reports_pattern_mirror_flag(self):
        sources = web_ui.resource_sources("grid_mirror_output")
        entry = _find(sources, kind="grid_craft", action_id="grid_mirror")
        self.assertIsNotNone(entry)
        self.assertTrue(entry["pattern_mirror"])


class PlainManualSourceTests(FixtureTestCase):
    def test_action_without_pattern_reports_manual_kind(self):
        sources = web_ui.resource_sources("raw_a")
        entry = _find(sources, kind="manual", action_id="gather_a")
        self.assertIsNotNone(entry)
        self.assertNotIn("location_id", entry)
        self.assertEqual(entry["cost"], [])


class BuildingSourceTests(FixtureTestCase):
    def test_passive_building_reports_produces_and_consumes(self):
        sources = web_ui.resource_sources("energy")
        entry = _find(sources, kind="building", building_id="gen")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["amount"], 2)
        self.assertEqual(entry["cost"], [])


class CreatureSourceTests(FixtureTestCase):
    def test_species_reports_its_farm_building(self):
        sources = web_ui.resource_sources("species_output")
        entry = _find(sources, kind="creature", species_id="critter_a")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["building_id"], "apiary")
        self.assertIn("Питомник", entry["name"])


class SoulSourceTests(FixtureTestCase):
    def test_soul_recipe_reports_its_collector_building(self):
        sources = web_ui.resource_sources("raw_a")
        entry = _find(sources, kind="soul", soul_id="test_soul")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["building_id"], "soul_synth")
        self.assertNotIn("chance", entry)  # raw_a гарантирован (нет в produces_chance)

    def test_soul_recipe_reports_chance_for_chancy_output(self):
        sources = web_ui.resource_sources("raw_b")
        entry = _find(sources, kind="soul", soul_id="test_soul")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["chance"], 0.5)


class EnergyRecipeSourceTests(FixtureTestCase):
    def test_energy_recipe_reports_consumes_as_cost(self):
        sources = web_ui.resource_sources("catalyst")
        entry = _find(sources, kind="energy", action_id="make_catalyst")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["amount"], 1)


class RitualSourceTests(FixtureTestCase):
    def test_ritual_recipe_reports_its_altar_building(self):
        sources = web_ui.resource_sources("mid")
        entry = _find(sources, kind="ritual", action_id="ritual_basic")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["building_id"], "ritual_altar")

    def test_ritual_cost_is_ring_center_and_extra_together(self):
        entry = _find(web_ui.resource_sources("mid"), kind="ritual", action_id="ritual_basic")
        self.assertEqual({c["id"]: c["amount"] for c in entry["cost"]}, {"raw_a": 2, "raw_b": 1})


class MiningSourceTests(FixtureTestCase):
    def test_mineable_resource_reports_base_yield(self):
        sources = web_ui.resource_sources("mineral")
        entry = _find(sources, kind="mining")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["amount"], 4)


class LootSourceTests(FixtureTestCase):
    def test_mob_loot_reports_the_mob(self):
        sources = web_ui.resource_sources("raw_a")
        entry = _find(sources, kind="loot", mob_id="test_mob")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["amount"], 2)

    def test_mob_soul_drop_is_a_separate_loot_entry(self):
        sources = web_ui.resource_sources("test_soul")
        entry = _find(sources, kind="loot", mob_id="test_mob")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["chance"], 1.0)
        self.assertIn("душа", entry["name"])


class LootboxSourceTests(FixtureTestCase):
    def test_case_prize_names_its_rewarding_quest(self):
        sources = web_ui.resource_sources("raw_a")
        entry = _find(sources, kind="lootbox", case_id="test_case")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["amount"], 5)
        self.assertIn("Тестовый квест", entry["name"])


class SpawnSourceTests(FixtureTestCase):
    def test_spawn_species_action_is_a_separate_kind(self):
        sources = web_ui.resource_sources("critter_a")
        entry = _find(sources, kind="spawn", action_id="catch_critter_a")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["location_id"], "yard")


class BreedingSourceTests(FixtureTestCase):
    def test_mutation_outcome_reports_both_parents_and_station(self):
        sources = web_ui.resource_sources("critter_b")
        entry = _find(sources, kind="breeding")
        self.assertIsNotNone(entry)
        self.assertIn("Существо А", entry["name"])
        self.assertEqual(entry["building_id"], "crossing_station")
        self.assertEqual(entry["chance"], 0.5)


class SourcePinKeyTests(FixtureTestCase):
    """Звезда закрепления есть у ЛЮБОГО способа (прямое пожелание, 2026-09-27)."""

    RESOURCES = ["raw_a", "raw_b", "energy", "mid", "catalyst", "mineral", "test_soul",
                 "critter_a", "critter_b", "species_output", "grid_output"]

    def test_every_source_has_a_distinct_pin_key(self):
        for rid in self.RESOURCES:
            keys = [s["pin_key"] for s in web_ui.resource_sources(rid)]
            self.assertTrue(keys, rid)
            self.assertEqual(len(keys), len(set(keys)), rid)

    def test_grid_recipe_keeps_its_recipe_key(self):
        entry = _find(web_ui.resource_sources("grid_output"), kind="grid_craft", action_id="grid_line")
        self.assertEqual(entry["pin_key"], "grid_line")

    def test_other_sources_are_keyed_by_resource_kind_and_id(self):
        entry = _find(web_ui.resource_sources("raw_a"), kind="loot", mob_id="test_mob")
        self.assertEqual(engine.parse_source_pin_key(entry["pin_key"]), ("raw_a", "loot", "test_mob"))
        soul = _find(web_ui.resource_sources("test_soul"), kind="loot", mob_id="test_mob")
        self.assertNotEqual(soul["pin_key"], entry["pin_key"])
        self.assertNotIn("pin_ident", soul)

    def test_engine_pins_and_unpins_a_source_key(self):
        s = GameState()
        key = engine.source_pin_key("raw_a", "loot", "test_mob")
        self.assertTrue(engine.toggle_pinned_recipe(s, key).ok)
        self.assertEqual(s.pinned_recipes, [key])
        self.assertTrue(engine.toggle_pinned_recipe(s, key).ok)
        self.assertEqual(s.pinned_recipes, [])

    def test_engine_refuses_unknown_resource_and_non_string(self):
        s = GameState()
        self.assertFalse(engine.toggle_pinned_recipe(s, "source:nope:loot:test_mob").ok)
        self.assertFalse(engine.toggle_pinned_recipe(s, None).ok)
        self.assertEqual(s.pinned_recipes, [])


class PinnedTilesTests(FixtureTestCase):
    def test_tiles_follow_pin_order_and_cover_every_kind(self):
        ritual = _find(web_ui.resource_sources("mid"), kind="ritual")["pin_key"]
        mining = _find(web_ui.resource_sources("mineral"), kind="mining")["pin_key"]
        s = GameState(pinned_recipes=[mining, "grid_locked", ritual])
        tiles = web_ui.pinned_recipes_view(s, web_ui.crafting_view(s), [])
        self.assertEqual([t["action_id"] for t in tiles], [mining, "grid_locked", ritual])
        self.assertEqual(tiles[0]["gain"][0]["id"], "mineral")
        self.assertIsNone(tiles[0]["enough"])  # у шахты нет разовой цены - точки нет
        self.assertTrue(tiles[1]["locked"])
        self.assertFalse(tiles[2]["enough"])  # сырья на ритуал нет

    def test_source_tile_reports_enough_when_cost_is_on_hand(self):
        ritual = _find(web_ui.resource_sources("mid"), kind="ritual")["pin_key"]
        s = GameState(pinned_recipes=[ritual], resources={"raw_a": 2, "raw_b": 1})
        tile = web_ui.pinned_recipes_view(s, [], [])[0]
        self.assertTrue(tile["enough"])

    def test_forgotten_source_is_skipped(self):
        s = GameState(pinned_recipes=["source:raw_a:loot:gone_mob"])
        self.assertEqual(web_ui.pinned_recipes_view(s, [], []), [])


class SourcesSummaryTests(FixtureTestCase):
    def test_summary_lists_every_resource_with_a_source(self):
        summary = web_ui.sources_summary_view()
        by_id = {item["id"]: item for item in summary}
        self.assertIn("raw_a", by_id)
        self.assertIn("grid_craft", by_id["grid_output"]["kinds"])
        # raw_a has manual/soul/loot/lootbox sources - несколько видов сразу.
        self.assertTrue({"manual", "soul", "loot", "lootbox"}.issubset(set(by_id["raw_a"]["kinds"])))


if __name__ == "__main__":
    unittest.main()
