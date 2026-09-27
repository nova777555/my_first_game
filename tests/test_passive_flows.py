"""
Приток и расход за ход (game/engine.py: passive_flows/theoretical_rates/energy_breakdown).

Панель энергии и «+N/ход» видят не только постройки, а всё, что работает само:
заселённых особей, вставленные души, реакторы (энергия и жидкость контура по
прогнозу хода) и доп. расход идущих ритуалов. Прогноз обязан совпадать с тем, что
реально даёт advance_turn.

Запуск: python -m unittest tests.test_passive_flows -v
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
from game import engine  # noqa: E402
from game.state import GameState  # noqa: E402


class PassiveFlowsTests(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def test_housed_creatures_count_in_rates(self):
        s = GameState(buildings={"apiary": 1})
        engine._ensure_physical_instances(s)
        farm_id = next(iter(s.farm_instances))
        for _ in range(2):
            cid = engine.spawn_creature(s, fixtures.make_genotype("critter_a", active_period="always"), identified=True)
            self.assertTrue(engine.house_creature(s, cid, farm_id).ok)
        self.assertEqual(engine.theoretical_rates(s)["species_output"], 4)
        before = s.resources.get("species_output", 0)
        engine.advance_turn(s)
        self.assertEqual(s.resources["species_output"] - before, 4)

    def test_installed_souls_count_with_chance_as_expectation(self):
        s = GameState(buildings={"soul_synth": 1}, resources={"test_soul": 1})
        engine._ensure_physical_instances(s)
        self.assertTrue(engine.insert_soul(s, next(iter(s.soul_collector_instances)), "test_soul").ok)
        rates = engine.theoretical_rates(s)
        self.assertEqual((rates["raw_a"], rates["raw_b"]), (3, 0.5))

    def test_reactor_output_and_coolant_fluid_match_the_real_turn(self):
        engine.RESOURCE_REACTOR_COOLANT_FLUID = {"raw_b": {"cooling_mult": 1.0, "units_per_power": 0.2}}
        s = GameState(buildings={"test_reactor": 1, "battery": 5}, resources={"test_fuel": 2, "test_coolant": 1, "raw_b": 50})
        engine._ensure_physical_instances(s)
        rid = next(iter(s.reactor_instances))
        for cell, component in enumerate(("test_fuel", "test_coolant", "test_fuel")):
            self.assertTrue(engine.insert_reactor_component(s, rid, cell, component).ok)
        engine.set_reactor_coolant_fluid(s, rid, "raw_b")
        stock, generation, consumption = engine.energy_breakdown(s)
        rates = engine.theoretical_rates(s)
        self.assertGreater(generation, 0)
        self.assertLess(rates["raw_b"], 0)
        before = copy.deepcopy(s.resources)
        engine.advance_turn(s)
        self.assertAlmostEqual(s.resources.get("energy", 0) - before.get("energy", 0), generation - consumption)
        self.assertAlmostEqual(s.resources["raw_b"] - before["raw_b"], rates["raw_b"])

    def test_running_ritual_extra_consumption_counts(self):
        s = GameState(buildings={"ritual_altar": 1}, resources={"raw_a": 5, "raw_b": 1})
        self.assertTrue(engine.start_ritual(s, "ritual_slow").ok)
        self.assertEqual(engine.theoretical_rates(s)["raw_a"], -1)


if __name__ == "__main__":
    unittest.main()
