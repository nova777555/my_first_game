"""
Сохранение переживает удаление контента (game/engine.py: drop_removed_content).

Автор правит content.json в редакторе при живом сейве. При загрузке всё, что
ссылается на удалённое (или на то, что сменило вид), снимается, как отменой:
задачи, заказы, исследование, схватка, души, компоненты реактора - списанное
возвращается на склад. После этого игра обязана и ходить, и строить экран.

Запуск: python -m unittest tests.test_removed_content -v
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fixtures  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402


class RemovedContentTests(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        import web_ui
        self.web_ui = web_ui

    def remove(self, table: str, key: str) -> None:
        """Удаляет сущность из контента движка и web_ui (копией - фикстуру не трогаем)."""
        value = {k: v for k, v in getattr(engine, table).items() if k != key}
        setattr(engine, table, value)
        if hasattr(self.web_ui, table):
            setattr(self.web_ui, table, value)

    def survives(self, s: GameState) -> None:
        """После очистки игра строит экран и делает несколько ходов."""
        engine.drop_removed_content(s)
        self.web_ui.build_view(s)
        for _ in range(3):
            engine.advance_turn(s)
        self.web_ui.build_view(s)

    def test_long_job_of_removed_action_returns_its_cost(self):
        s = GameState(resources={"raw_a": 5})
        engine._ensure_physical_instances(s)
        self.assertTrue(engine.do_manual_action(s, "craft_delayed").ok)
        self.remove("MANUAL_ACTIONS", "craft_delayed")
        self.survives(s)
        self.assertEqual(s.manual_jobs, {})
        self.assertEqual(s.resources["raw_a"], 5)

    def test_research_of_removed_tech_returns_its_cost(self):
        s = GameState(resources={"raw_a": 3}, active_research={
            "tech_id": "tech_tier2_slow", "turns_total": 5, "turns_done": 2, "cost": {"raw_a": 1},
        })
        self.remove("TECHS", "tech_tier2_slow")
        self.survives(s)
        self.assertIsNone(s.active_research)
        self.assertEqual(s.resources["raw_a"], 4)

    def test_encounter_with_removed_mob_is_dropped(self):
        s = GameState(active_encounter={"mob_id": "test_mob", "remaining_hp": 4})
        self.remove("MOBS", "test_mob")
        self.survives(s)
        self.assertIsNone(s.active_encounter)

    def test_removed_soul_collector_returns_its_souls(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        engine._ensure_physical_instances(s)
        instance_id = next(iter(s.soul_collector_instances))
        self.assertTrue(engine.insert_soul(s, instance_id, "test_soul").ok)
        self.remove("BUILDINGS", "soul_synth")
        self.survives(s)
        self.assertEqual(s.soul_collector_instances, {})
        self.assertEqual(s.resources["test_soul"], 1)

    def test_soul_that_stopped_being_a_soul_goes_back_to_storage(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        engine._ensure_physical_instances(s)
        instance = next(iter(s.soul_collector_instances.values()))
        instance["installed"] = ["test_soul", "gone_soul"]
        self.remove("SOUL_RECIPES", "test_soul")
        self.survives(s)
        self.assertEqual(instance["installed"], [])
        self.assertEqual(s.resources["test_soul"], 2)
        self.assertNotIn("gone_soul", s.resources)

    def test_removed_reactor_returns_components_and_catalysts(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_catalyst_boost": 1})
        engine._ensure_physical_instances(s)
        instance_id = next(iter(s.reactor_instances))
        self.assertTrue(engine.insert_reactor_component(s, instance_id, 0, "test_fuel").ok)
        self.assertTrue(engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost").ok)
        self.remove("BUILDINGS", "test_reactor")
        self.survives(s)
        self.assertEqual(s.reactor_instances, {})
        self.assertEqual((s.resources["test_fuel"], s.resources["test_catalyst_boost"]), (1, 1))

    def test_removed_farm_moves_creatures_to_the_bank(self):
        s = GameState(buildings={"apiary": 1})
        engine._ensure_physical_instances(s)
        farm_id = next(iter(s.farm_instances))
        creature_id = engine.spawn_creature(s, fixtures.make_genotype("critter_a"), identified=True)
        s.creatures[creature_id]["farm_instance_id"] = farm_id
        self.remove("BUILDINGS", "apiary")
        self.survives(s)
        self.assertEqual(s.farm_instances, {})
        self.assertIsNone(s.creatures[creature_id]["farm_instance_id"])

    def test_creatures_of_removed_species_and_alleles(self):
        s = GameState()
        gone = engine.spawn_creature(s, fixtures.make_genotype("critter_b"), identified=True)
        mixed = engine.spawn_creature(s, fixtures.make_genotype("critter_a"), identified=True)
        s.creatures[mixed]["genotype"]["species"] = ["critter_b", "critter_a"]
        s.creatures[mixed]["genotype"]["lifespan"] = ["gone_allele", "gone_allele"]
        s.known_alleles = {"species": ["critter_a", "critter_b"], "lifespan": ["normal", "gone_allele"]}
        s.species_discovered = {"critter_a", "critter_b"}
        self.remove("SPECIES", "critter_b")
        self.survives(s)
        self.assertNotIn(gone, s.creatures)
        self.assertEqual(s.creatures[mixed]["genotype"]["species"], ["critter_a", "critter_a"])
        self.assertEqual(s.creatures[mixed]["genotype"]["lifespan"], ["normal", "normal"])
        self.assertEqual(s.creatures[mixed]["species_id"], "critter_a")
        self.assertEqual(s.known_alleles, {"species": ["critter_a"], "lifespan": ["normal"]})
        self.assertEqual(s.species_discovered, {"critter_a"})

    def test_energy_job_of_removed_recipe_returns_materials_to_its_order(self):
        s = GameState(orders=[{
            "id": 1, "kind": "recipe", "target_id": "craft_mid", "label": "x", "reserved": {}, "steps": [],
            "energy_reserve": 0, "final_energy": 0, "status": None, "waits_energy": False, "qty": 1,
        }], next_order_id=2, energy_jobs=[
            {"recipe_id": "make_raw_b", "progress": 3, "building_id": "energy_converter",
             "cost": {"raw_a": 2}, "order_id": 1},
            {"recipe_id": "make_raw_b", "progress": 3, "building_id": "gone_converter", "cost": {"raw_a": 1}},
        ])
        self.remove("ENERGY_RECIPES", "make_raw_b")
        engine.drop_removed_content(s)
        self.assertEqual(s.energy_jobs, [])
        self.assertEqual((s.orders[0]["reserved"], s.resources), ({"raw_a": 2}, {"raw_a": 1}))
        self.survives(s)

    def test_ritual_job_of_removed_recipe_returns_its_cost(self):
        s = GameState(ritual_jobs={"ritual_altar": {
            "recipe_id": "ritual_slow", "turns_total": 2, "turns_done": 1, "extra_consumes_per_turn": {},
            "extra_consumes_chance": None, "produces": {"mid": 2}, "produces_chance": None,
            "cost": {"raw_a": 1, "raw_b": 1},
        }})
        self.remove("RITUAL_RECIPES", "ritual_slow")
        self.survives(s)
        self.assertEqual(s.ritual_jobs, {})
        self.assertEqual((s.resources["raw_a"], s.resources["raw_b"]), (1, 1))

    def test_orders_referring_to_removed_content_are_cancelled_with_refund(self):
        base = {"label": "x", "energy_reserve": 0, "final_energy": 0, "status": None, "waits_energy": False}
        s = GameState(orders=[
            {**base, "id": 1, "kind": "manual", "target_id": "craft_delayed", "reserved": {"raw_a": 2}, "steps": []},
            {**base, "id": 2, "kind": "recipe", "target_id": "craft_mid", "reserved": {"raw_b": 1},
             "steps": [{"resource_id": "delayed_output", "kind": "long", "action_id": "craft_delayed",
                        "fires_total": 1, "fires_done": 0}]},
            {**base, "id": 3, "kind": "tech", "target_id": "tech1", "reserved": {"raw_a": 1, "gone_res": 4},
             "steps": []},
        ], next_order_id=4)
        self.remove("MANUAL_ACTIONS", "craft_delayed")
        self.survives(s)
        self.assertEqual([o["id"] for o in s.orders if o["id"] in (1, 2)], [])
        self.assertEqual((s.resources.get("raw_a"), s.resources.get("raw_b")), (2, 1))
        self.assertNotIn("gone_res", s.resources)

    def test_tools_enchantments_and_storage_are_clamped(self):
        s = GameState(
            tool_tiers={"sword": 3, "gone_tool": 2}, tool_durability={"sword": 1, "gone_tool": 1},
            tool_enchantments={"sword": {"test_sharpness": 1, "gone_ench": 2}, "gone_tool": {"test_luck": 1}},
            storage_tier={"limited_res": 9, "raw_a": 1},
        )
        engine.TOOLS = copy.deepcopy(engine.TOOLS)
        engine.TOOLS["sword"]["tiers"] = engine.TOOLS["sword"]["tiers"][:2]
        self.web_ui.TOOLS = engine.TOOLS
        self.survives(s)
        self.assertEqual(s.tool_tiers, {"sword": 2})
        self.assertEqual(s.tool_durability, {})
        self.assertEqual(s.tool_enchantments, {"sword": {"test_sharpness": 1}})
        tiers = len(engine.RESOURCE_STORAGE["limited_res"]["tiers"])
        self.assertEqual(s.storage_tier, {"limited_res": tiers})

    def test_mine_cell_of_removed_ore_becomes_empty(self):
        s = GameState()
        engine._ensure_mine_field(s)
        cell = s.active_mine_field["cells"][0]
        cell.update({"resource": "rare_mineral", "broken": False})
        self.remove("MINING_INFO", "rare_mineral")
        self.survives(s)
        self.assertIsNone(cell["resource"])

    def test_everything_at_once_on_an_old_save(self):
        """Дымовой: всё сразу ссылается на несуществующее - игра открывается и ходит."""
        s = GameState(
            buildings={"gone_building": 2, "soul_synth": 1, "test_reactor": 1, "apiary": 1},
            broken={"gone_building": 1}, broken_types={"gone_building": ["gone_type"]},
            building_upgrades={"soul_synth": ["gone_upgrade"]},
            construction_progress={"gone_building": 1, "gen": 3},
            manual_jobs={"gone_action": {"turns_total": 3, "turns_done": 1, "gain": {"raw_a": 1},
                                         "gain_chance": None, "spawn_species": None, "cost": {"raw_b": 2}}},
            energy_jobs=[{"recipe_id": "gone_recipe", "progress": 1, "building_id": "energy_converter"}],
            ritual_jobs={"gone_altar": {"recipe_id": "ritual_basic", "turns_total": 2, "turns_done": 0,
                                        "cost": {"raw_a": 1}}},
            replicator_jobs=[{"id": 1, "building_id": "gone_repl", "resource_id": "raw_a", "qty": 1,
                              "cost": 1, "progress": 0, "delivered": 0}],
            active_research={"tech_id": "gone_tech", "turns_total": 3, "turns_done": 1},
            active_encounter={"mob_id": "gone_mob", "remaining_hp": 5},
            automation_rules={"gone_hub": [{"kind": "manual_action", "target_id": "gather", "conditions": []}],
                              "automator": [{"kind": "manual_action", "target_id": "gone_action", "conditions": []}]},
            orders=[{"id": 9, "kind": "summon_boss", "target_id": "gone_boss", "label": "x", "reserved": {},
                     "steps": [], "energy_reserve": 0, "final_energy": 0, "status": None, "waits_energy": False}],
            move_cooldowns={"gone_move": 2}, soul_slots={"gone_building": ["test_soul"]},
            tool_tiers={"gone_tool": 3}, pinned_recipes=["gone_recipe"], pinned=["gone_res"],
            resources={"gone_res": 3},
        )
        engine._ensure_physical_instances(s)
        next(iter(s.soul_collector_instances.values()))["installed"] = ["gone_soul"]
        next(iter(s.reactor_instances.values()))["grid"][0] = {"resource_id": "gone_fuel", "charge": 2}
        next(iter(s.reactor_instances.values()))["coolant_fluid"] = "gone_fluid"
        self.survives(s)
        self.assertEqual(s.resources.get("raw_b"), 2)
        self.assertEqual(s.resources.get("test_soul"), 1)
        self.assertEqual(s.orders, [])
        self.assertEqual(s.automation_rules, {"automator": []})


if __name__ == "__main__":
    unittest.main()
