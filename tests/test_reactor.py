"""
Тесты решётчатого реактора (game/engine.py: жизненный цикл per-instance
решётки/зон - _ensure_reactor_instances/_ensure_reactor_grid/
_ensure_reactor_zones/reactor_grid_dims/reactor_zone_count/_reactor_zone_layout).
Работают на самодостаточной тестовой фикстуре (tests/fixtures.py: BUILDINGS
["test_reactor"]), не на реальном game/content.json.

Запуск: python -m unittest tests.test_reactor -v
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


class ReactorInstanceLifecycleTests(FixtureTestCase):
    """_ensure_reactor_instances - зеркало _ensure_soul_collector_instances,
    см. game/engine.py и tests/test_combat_and_souls.py:SoulCollectorTests
    для того же паттерна теста."""

    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_no_instances_without_building(self):
        s = GameState()
        engine._ensure_reactor_instances(s)
        self.assertEqual(s.reactor_instances, {})

    def test_building_creates_instance_with_seeded_grid_and_zone(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        inst = s.reactor_instances[instance_id]
        self.assertEqual(inst["building_id"], "test_reactor")
        self.assertFalse(inst["broken"])
        self.assertIsNone(inst["broken_type"])
        self.assertEqual(inst["grid"], [None, None, None, None])  # 2x2 по фикстуре
        self.assertEqual(len(inst["zones"]), 1)
        self.assertEqual(
            inst["zones"][0],
            {"heat": 0.0, "control_rod_depth": 0, "coolant_flow": 100, "prev_coolant_flow": 100},
        )

    def test_second_copy_gets_its_own_instance(self):
        s = GameState(buildings={"test_reactor": 2})
        engine._ensure_reactor_instances(s)
        self.assertEqual(len(s.reactor_instances), 2)
        ids = list(s.reactor_instances)
        self.assertNotEqual(ids[0], ids[1])

    def test_broken_aggregate_marks_some_created_instances_broken(self):
        s = GameState(buildings={"test_reactor": 2}, broken={"test_reactor": 1})
        engine._ensure_reactor_instances(s)
        broken_count = sum(1 for inst in s.reactor_instances.values() if inst["broken"])
        self.assertEqual(broken_count, 1)

    def test_idempotent_second_call_does_not_duplicate(self):
        s = GameState(buildings={"test_reactor": 1})
        engine._ensure_reactor_instances(s)
        engine._ensure_reactor_instances(s)
        self.assertEqual(len(s.reactor_instances), 1)

    def test_building_a_second_copy_later_adds_only_the_missing_instance(self):
        s = GameState(buildings={"test_reactor": 1})
        engine._ensure_reactor_instances(s)
        first_ids = set(s.reactor_instances)
        s.buildings["test_reactor"] = 2
        engine._ensure_reactor_instances(s)
        self.assertEqual(len(s.reactor_instances), 2)
        self.assertTrue(first_ids.issubset(s.reactor_instances))


class ReactorGridDimsAndZoneCountTests(FixtureTestCase):
    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_default_dims_and_zone_count(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        self.assertEqual(engine.reactor_grid_dims(s, instance_id), (2, 2))
        self.assertEqual(engine.reactor_zone_count(s, instance_id), 1)

    def test_missing_instance_defaults(self):
        s = GameState()
        self.assertEqual(engine.reactor_grid_dims(s, "nope"), (0, 0))
        self.assertEqual(engine.reactor_zone_count(s, "nope"), 1)

    def test_grid_size_upgrade_grows_dims_and_grid_preserves_filled_cells(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["grid"][0] = {"resource_id": "test_fuel", "charge": 3}
        s.reactor_instances[instance_id]["upgrades"].append("test_reactor_grid_3x2")
        self.assertEqual(engine.reactor_grid_dims(s, instance_id), (3, 2))
        engine._ensure_reactor_grid(s, instance_id)
        grid = s.reactor_instances[instance_id]["grid"]
        self.assertEqual(len(grid), 6)
        self.assertEqual(grid[0], {"resource_id": "test_fuel", "charge": 3})
        self.assertTrue(all(cell is None for cell in grid[1:]))

    def test_zoning_upgrade_changes_zone_count_and_resets_heat(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 5.0
        s.reactor_instances[instance_id]["upgrades"].append("test_reactor_zoning_2")
        self.assertEqual(engine.reactor_zone_count(s, instance_id), 2)
        engine._ensure_reactor_zones(s, instance_id)
        zones = s.reactor_instances[instance_id]["zones"]
        self.assertEqual(len(zones), 2)
        self.assertTrue(all(z["heat"] == 0.0 for z in zones))

    def test_ensure_zones_is_noop_when_count_unchanged(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 7.0
        engine._ensure_reactor_zones(s, instance_id)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 7.0)

    def test_grid_size_ignores_smaller_upgrade_than_base(self):
        # Апгрейд с меньшей площадью, чем текущая база - не должен УМЕНЬШИТЬ dims
        # ("сильнейшее побеждает", не "последнее купленное").
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["upgrades"].append("test_reactor_grid_3x2")  # 3x2 = 6
        self.assertEqual(engine.reactor_grid_dims(s, instance_id), (3, 2))


class ReactorZoneLayoutTests(unittest.TestCase):
    def test_single_zone_covers_everything(self):
        self.assertEqual(engine._reactor_zone_layout(2, 2, 1), [0, 0, 0, 0])

    def test_even_split_by_rows(self):
        # 2 столбца x 4 строки, 2 зоны -> первые 2 строки зона 0, вторые зона 1.
        self.assertEqual(engine._reactor_zone_layout(2, 4, 2), [0, 0, 0, 0, 1, 1, 1, 1])

    def test_zone_count_clamped_to_height(self):
        self.assertEqual(engine._reactor_zone_layout(2, 2, 5), engine._reactor_zone_layout(2, 2, 2))

    def test_uneven_rows_go_to_earlier_zones(self):
        # 3 строки, 2 зоны -> зона 0 забирает 2 строки (лишнюю), зона 1 - 1.
        self.assertEqual(engine._reactor_zone_layout(1, 3, 2), [0, 0, 1])

    def test_empty_grid_gives_empty_layout(self):
        self.assertEqual(engine._reactor_zone_layout(0, 3, 2), [])
        self.assertEqual(engine._reactor_zone_layout(3, 0, 2), [])


class ReactorComponentPlacementTests(FixtureTestCase):
    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_bank_view_lists_all_tagged_resources(self):
        s = GameState(resources={"test_fuel": 2})
        bank = {e["id"]: e for e in engine.reactor_component_bank_view(s)}
        self.assertEqual(bank["test_fuel"]["role"], "fuel")
        self.assertEqual(bank["test_fuel"]["amount"], 2)
        self.assertEqual(bank["test_moderator"]["role"], "moderator")
        self.assertEqual(bank["test_moderator"]["amount"], 0)

    def test_insert_rejects_unaccepted_role(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 5})
        result = engine.insert_reactor_component(s, self._instance(s), 0, "raw_a")
        self.assertFalse(result.ok)

    def test_insert_rejects_without_stock(self):
        s = GameState(buildings={"test_reactor": 1})
        result = engine.insert_reactor_component(s, self._instance(s), 0, "test_fuel")
        self.assertFalse(result.ok)

    def test_insert_rejects_out_of_range_cell(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        result = engine.insert_reactor_component(s, self._instance(s), 99, "test_fuel")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["test_fuel"], 1)  # не списано

    def test_insert_rejects_occupied_cell(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 2})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        result = engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["test_fuel"], 1)

    def test_insert_fuel_sets_charge_to_max_non_fuel_has_no_charge(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_moderator": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_component(s, instance_id, 1, "test_moderator")
        grid = s.reactor_instances[instance_id]["grid"]
        self.assertEqual(grid[0], {"resource_id": "test_fuel", "charge": 3})
        self.assertEqual(grid[1], {"resource_id": "test_moderator", "charge": None})
        self.assertEqual(s.resources["test_fuel"], 0)

    def test_remove_returns_untouched_component_as_is(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_moderator": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_moderator")
        result = engine.remove_reactor_component(s, instance_id, 0)
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["test_moderator"], 1)
        self.assertIsNone(s.reactor_instances[instance_id]["grid"][0])

    def test_remove_spent_fuel_returns_spent_result_instead(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        s.reactor_instances[instance_id]["grid"][0]["charge"] = 0  # выгорело
        result = engine.remove_reactor_component(s, instance_id, 0)
        self.assertTrue(result.ok)
        self.assertEqual(s.resources.get("test_fuel", 0), 0)
        self.assertEqual(s.resources.get("test_spent_fuel", 0), 1)

    def test_remove_rejects_empty_cell(self):
        s = GameState(buildings={"test_reactor": 1})
        result = engine.remove_reactor_component(s, self._instance(s), 0)
        self.assertFalse(result.ok)


class ReactorZoneControlTests(FixtureTestCase):
    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_set_control_clamps_to_0_100(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        engine.set_reactor_zone_control(s, instance_id, 0, 150)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["control_rod_depth"], 100)
        engine.set_reactor_zone_control(s, instance_id, 0, -10)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["control_rod_depth"], 0)

    def test_set_control_rejects_unknown_zone(self):
        s = GameState(buildings={"test_reactor": 1})
        result = engine.set_reactor_zone_control(s, self._instance(s), 5, 50)
        self.assertFalse(result.ok)

    def test_set_coolant_tracks_previous_value(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        engine.set_reactor_zone_coolant(s, instance_id, 0, 20)
        zone = s.reactor_instances[instance_id]["zones"][0]
        self.assertEqual(zone["prev_coolant_flow"], 100)  # дефолт до вызова
        self.assertEqual(zone["coolant_flow"], 20)
        engine.set_reactor_zone_coolant(s, instance_id, 0, 90)
        self.assertEqual(zone["prev_coolant_flow"], 20)
        self.assertEqual(zone["coolant_flow"], 90)


class ReactorThermalShockTests(FixtureTestCase):
    """set_reactor_zone_coolant - термоудар при резком скачке потока на уже
    разогретой зоне (test_reactor.reactor: thermal_shock_flow_delta_pct=40,
    thermal_shock_heat_bonus_pct=0.2, heat_cap_per_cell=10 -> cap зоны из 4
    ячеек = 40, soft_threshold_pct=0.5 -> порог "разогрета" = heat > 20)."""

    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_big_flow_delta_on_hot_zone_triggers_shock(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 25.0  # 25/40=0.625 > 0.5, "горячая"
        result = engine.set_reactor_zone_coolant(s, instance_id, 0, 50)  # 100 -> 50, delta=50 >= 40
        self.assertTrue(result.ok)
        self.assertIn("Термоудар", result.message)
        # bonus = cap(40) * 0.2 = 8
        self.assertAlmostEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 33.0)

    def test_big_flow_delta_on_cool_zone_does_not_trigger_shock(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 5.0  # 5/40=0.125, не разогрета
        result = engine.set_reactor_zone_coolant(s, instance_id, 0, 50)  # тот же большой скачок
        self.assertNotIn("Термоудар", result.message)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 5.0)

    def test_small_flow_delta_on_hot_zone_does_not_trigger_shock(self):
        s = GameState(buildings={"test_reactor": 1})
        instance_id = self._instance(s)
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 25.0  # разогрета
        result = engine.set_reactor_zone_coolant(s, instance_id, 0, 90)  # 100 -> 90, delta=10 < 40
        self.assertNotIn("Термоудар", result.message)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 25.0)


class ReactorZoneProjectionTests(FixtureTestCase):
    """reactor_zone_projection - прогноз телеметрии на следующий ход, ЧИСТАЯ
    функция (не мутирует state), доступна только при купленной телеметрии."""

    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_none_without_telemetry(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        self.assertIsNone(engine.reactor_zone_projection(s, instance_id))

    def test_matches_next_turn_when_no_threshold_crossed(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "raw_a": 10})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_telemetry")
        projection = engine.reactor_zone_projection(s, instance_id)
        self.assertEqual(len(projection), 1)
        zone_proj = projection[0]
        self.assertAlmostEqual(zone_proj["next_heat"], 4.0)  # heat=4, без соседей/охлаждения
        self.assertAlmostEqual(zone_proj["next_output"], 6.0)
        self.assertFalse(zone_proj["will_trip"])
        # Прогноз не должен был ничего мутировать в реальном состоянии зоны.
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 0.0)
        self.assertEqual(s.reactor_instances[instance_id]["grid"][0]["charge"], 3)  # burnup ещё не списан

    def test_flags_will_trip_without_mutating_state(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "raw_a": 10})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_telemetry")
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 40.0  # уже на пределе (cap=40)
        projection = engine.reactor_zone_projection(s, instance_id)
        zone_proj = projection[0]
        self.assertTrue(zone_proj["will_trip"])
        self.assertEqual(zone_proj["next_heat"], 0.0)
        self.assertEqual(zone_proj["next_ratio"], 0.0)
        # Состояние зоны не тронуто - авария реально бросается только в _process_reactors.
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 40.0)


class ReactorAdjacencyBonusTests(FixtureTestCase):
    """_reactor_fuel_adjacency_bonus в изоляции, без накопления тепла/ходов -
    сетка фикстуры 2x2 (test_reactor.reactor), индексы row-major:
    0=(0,0) 1=(0,1)
    2=(1,0) 3=(1,1)
    fuel_adjacency_heat_step=0.5, fuel_adjacency_output_step=0.25,
    moderator_adjacency_output_bonus=1.0, absorber_adjacency_reduction=1.0."""

    def _config(self):
        from game.engine import BUILDINGS
        return BUILDINGS["test_reactor"]["reactor"]

    def test_no_neighbors_gives_neutral_multipliers(self):
        grid = [{"resource_id": "test_fuel", "charge": 3}, None, None, None]
        bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(bonus, (1.0, 1.0, 0.0))

    def test_adjacent_fuel_boosts_heat_more_than_output(self):
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_fuel", "charge": 3}, None, None]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(heat_mult, 1.5)
        self.assertEqual(output_mult, 1.25)
        self.assertEqual(flat_bonus, 0.0)

    def test_adjacent_moderator_adds_flat_output_only(self):
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_moderator", "charge": None}, None, None]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual((heat_mult, output_mult), (1.0, 1.0))
        self.assertEqual(flat_bonus, 1.0)

    def test_adjacent_absorber_can_fully_suppress(self):
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_absorber", "charge": None}, None, None]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual((heat_mult, output_mult, flat_bonus), (0.0, 0.0, 0.0))

    def test_adjacent_reflector_multiplies_output_only(self):
        # power=2, reflector_adjacency_output_mult_step=0.5 -> output_mult *= (1 + 0.5*2) = 2.0, heat не трогает.
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_reflector", "charge": None}, None, None]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(heat_mult, 1.0)
        self.assertEqual(output_mult, 2.0)
        self.assertEqual(flat_bonus, 0.0)

    def test_two_adjacent_reflectors_multiply_not_add(self):
        # Оба соседа (справа и снизу) - отражатели: 1.0 * 2.0 * 2.0 = 4.0, а не 1 + 0.5*2 + 0.5*2 = 3.0.
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_reflector", "charge": None},
            {"resource_id": "test_reflector", "charge": None},
            None,
        ]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(heat_mult, 1.0)
        self.assertAlmostEqual(output_mult, 4.0)
        self.assertEqual(flat_bonus, 0.0)

    def test_adjacent_shield_reduces_heat_only(self):
        # power=0.5, shield_adjacency_reduction=1.0 -> heat_mult -= 1.0*0.5 = 0.5, output_mult не тронут.
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_shield", "charge": None}, None, None]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(heat_mult, 0.5)
        self.assertEqual(output_mult, 1.0)
        self.assertEqual(flat_bonus, 0.0)

    def test_two_adjacent_shields_can_fully_suppress_heat_without_touching_output(self):
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_shield", "charge": None},
            {"resource_id": "test_shield", "charge": None},
            None,
        ]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(heat_mult, 0.0)
        self.assertEqual(output_mult, 1.0)
        self.assertEqual(flat_bonus, 0.0)

    def test_shield_and_absorber_combine_but_only_absorber_hits_output(self):
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_absorber", "charge": None},
            {"resource_id": "test_shield", "charge": None},
            None,
        ]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual(heat_mult, 0.0)  # 1.0 - 1.0(absorber) - 0.5(shield), floor 0
        self.assertEqual(output_mult, 0.0)  # 1.0 - 1.0(absorber) - 0(shield не трогает output)
        self.assertEqual(flat_bonus, 0.0)


class ReactorReflectorHeatAndAbsorberAsymmetryTests(FixtureTestCase):
    """Два НЕОБЯЗАТЕЛЬНЫХ коэффициента, ломающих строгое доминирование:

    * reflector_adjacency_heat_mult_step - отражатель множит не только выход,
      но и тепло. Без него отражатель - бесплатный множитель, который ВСЕГДА
      выгодно ставить по максимуму, из-за чего любая оптимальная схема
      вырождалась в одну и ту же шахматку "топливо + отражатель".
    * absorber_adjacency_output_reduction - поглотитель режет выход СЛАБЕЕ,
      чем тепло. При равных коэффициентах (старое поведение) поглотитель
      математически всегда строго хуже щита: тот гасит тепло вовсе не трогая
      выход.

    Оба поля необязательны: их отсутствие обязано воспроизводить ровно прежнее
    поведение (см. первые два теста), иначе уже сохранённый контент сломался
    бы молча."""

    def _config(self, **over):
        from game.engine import BUILDINGS
        return dict(BUILDINGS["test_reactor"]["reactor"], **over)

    def test_reflector_does_not_heat_when_field_absent(self):
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_reflector", "charge": None}, None, None]
        heat_mult, output_mult, _ = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual((heat_mult, output_mult), (1.0, 2.0))

    def test_absorber_cuts_output_as_much_as_heat_when_field_absent(self):
        # absorber_adjacency_reduction=1.0, power=1 -> оба множителя в пол.
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_absorber", "charge": None}, None, None]
        heat_mult, output_mult, _ = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual((heat_mult, output_mult), (0.0, 0.0))

    def test_reflector_multiplies_heat_when_step_set(self):
        # power=2, heat_step=0.25 -> heat_mult *= 1 + 0.25*2 = 1.5 (выход как был, x2.0)
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_reflector", "charge": None}, None, None]
        heat_mult, output_mult, _ = engine._reactor_fuel_adjacency_bonus(
            grid, 2, 2, 0, self._config(reflector_adjacency_heat_mult_step=0.25))
        self.assertEqual(heat_mult, 1.5)
        self.assertEqual(output_mult, 2.0)

    def test_two_reflectors_multiply_heat_not_add(self):
        # Как и выход, тепло ПЕРЕМНОЖАЕТСЯ: 1.0 * 1.5 * 1.5 = 2.25, а не 1 + 0.5 + 0.5.
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_reflector", "charge": None},
            {"resource_id": "test_reflector", "charge": None},
            None,
        ]
        heat_mult, output_mult, _ = engine._reactor_fuel_adjacency_bonus(
            grid, 2, 2, 0, self._config(reflector_adjacency_heat_mult_step=0.25))
        self.assertAlmostEqual(heat_mult, 2.25)
        self.assertAlmostEqual(output_mult, 4.0)

    def test_absorber_output_reduction_is_independent_of_heat_reduction(self):
        # heat -0.6*1, output -0.2*1 -> поглотитель гасит тепло втрое сильнее,
        # чем платит выходом; ИМЕННО это отличает его от щита.
        grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_absorber", "charge": None}, None, None]
        heat_mult, output_mult, _ = engine._reactor_fuel_adjacency_bonus(
            grid, 2, 2, 0, self._config(absorber_adjacency_reduction=0.6,
                                        absorber_adjacency_output_reduction=0.2))
        self.assertAlmostEqual(heat_mult, 0.4)
        self.assertAlmostEqual(output_mult, 0.8)

    def test_absorber_beats_shield_on_heat_per_cell(self):
        """Смысл асимметрии: за ОДНУ клетку поглотитель снимает больше тепла,
        чем щит, - иначе щит доминировал бы всегда и поглотитель был мёртв."""
        cfg = self._config(absorber_adjacency_reduction=0.6,
                           absorber_adjacency_output_reduction=0.2,
                           shield_adjacency_reduction=0.3)
        abs_grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_absorber", "charge": None}, None, None]
        shl_grid = [{"resource_id": "test_fuel", "charge": 3}, {"resource_id": "test_shield", "charge": None}, None, None]
        abs_heat, abs_out, _ = engine._reactor_fuel_adjacency_bonus(abs_grid, 2, 2, 0, cfg)
        shl_heat, shl_out, _ = engine._reactor_fuel_adjacency_bonus(shl_grid, 2, 2, 0, cfg)
        self.assertLess(abs_heat, shl_heat)   # поглотитель гасит тепло сильнее
        self.assertLess(abs_out, shl_out)     # но платит выходом, а щит - нет

    def test_real_content_uses_both_new_coefficients(self):
        """Регрессия на сам БАЛАНС: если эти коэффициенты снова обнулить,
        отражатель опять станет бесплатным, а поглотитель - строго хуже щита,
        и все схемы схлопнутся в одну."""
        import json, pathlib
        data = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "game" / "content.json").read_text(encoding="utf-8"))
        reactor = data["buildings"]["modular_reactor"]["reactor"]
        self.assertGreater(reactor["reflector_adjacency_heat_mult_step"], 0)
        self.assertLess(reactor["absorber_adjacency_output_reduction"],
                        reactor["absorber_adjacency_reduction"])
        self.assertGreater(reactor["absorber_adjacency_reduction"],
                           reactor["shield_adjacency_reduction"])


class ReactorFuelMinHeatMultTests(FixtureTestCase):
    """fuel_min_heat_mult - ТЕПЛОВОЙ ПОЛ стержня: доля собственного тепла,
    которую он отдаёт всегда, сколько щитов/поглотителей вокруг ни поставь.

    Без пола снижения щитов и поглотителей складывались линейно и спокойно
    уводили heat_mult в РОВНЫЙ НОЛЬ (на реальном контенте хватало четырёх
    улучшенных радиаторов или двух улучшенных поглотителей), причём щит не
    трогает выработку вовсе. То есть с открытием продвинутых компонентов
    стержень становился абсолютно холодным при 100% выхода, зона переставала
    накапливать тепло - и теплоносители вместе с расходом охлаждающей жидкости
    превращались в декорацию. С полом тепло всегда положительно, а само оно
    нигде не убывает (см. _reactor_zone_next_state), поэтому снимать его
    теплоносителем приходится на ЛЮБОМ тире компонентов.

    Поле необязательное: его отсутствие обязано воспроизводить ровно прежнее
    поведение (см. первый тест), иначе уже сохранённый контент сломался бы."""

    def _config(self, **over):
        from game.engine import BUILDINGS
        return dict(BUILDINGS["test_reactor"]["reactor"], **over)

    def _shielded(self, cfg, component="test_shield", count=1):
        """Стержень в углу 2x2, вокруг - count гасящих компонентов."""
        grid = [{"resource_id": "test_fuel", "charge": 3}, None, None, None]
        for cell in (1, 2)[:count]:
            grid[cell] = {"resource_id": component, "charge": None}
        return engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, cfg)

    def test_absent_field_still_allows_zeroing_heat(self):
        # Фикстура: absorber_adjacency_reduction=1.0, power=1 -> тепло в ноль.
        heat_mult, _out, _flat = self._shielded(self._config(), "test_absorber")
        self.assertEqual(heat_mult, 0.0)

    def test_floor_stops_absorber_from_zeroing_heat(self):
        heat_mult, _out, _flat = self._shielded(
            self._config(fuel_min_heat_mult=0.25), "test_absorber")
        self.assertAlmostEqual(heat_mult, 0.25)

    def test_floor_stops_any_number_of_shields(self):
        """Суть пола: он не «замедляет» гашение, а ставит непробиваемую границу -
        второй/третий щит уже ничего не отнимает."""
        # power щита в фикстуре 0.5, так что 2.0 * 0.5 = 1.0 гасит тепло целиком
        # уже ОДНИМ щитом, а второй по старой математике увёл бы множитель в минус.
        cfg = self._config(fuel_min_heat_mult=0.25, shield_adjacency_reduction=2.0)
        one, _o, _f = self._shielded(cfg, "test_shield", count=1)
        two, _o, _f = self._shielded(cfg, "test_shield", count=2)
        self.assertAlmostEqual(one, 0.25)
        self.assertAlmostEqual(two, 0.25)

    def test_floor_does_not_lift_unshielded_fuel(self):
        # Пол - НИЖНЯЯ граница, а не замена расчёта: голый стержень как был x1.0.
        grid = [{"resource_id": "test_fuel", "charge": 3}, None, None, None]
        heat_mult, output_mult, _flat = engine._reactor_fuel_adjacency_bonus(
            grid, 2, 2, 0, self._config(fuel_min_heat_mult=0.25))
        self.assertEqual((heat_mult, output_mult), (1.0, 1.0))

    def test_floor_does_not_touch_output(self):
        """Пол только на тепле - поглотитель по-прежнему платит выходом полностью,
        иначе он стал бы бесплатным гасителем."""
        _heat, output_mult, _flat = self._shielded(
            self._config(fuel_min_heat_mult=0.25), "test_absorber")
        self.assertEqual(output_mult, 0.0)

    def test_shielded_zone_still_heats_up_over_a_turn(self):
        """Итог для игрока: даже полностью обложенный щитами стержень греет зону,
        а тепло само не убывает - значит охлаждение нужно всегда."""
        from game.engine import BUILDINGS
        cfg = dict(BUILDINGS["test_reactor"]["reactor"], fuel_min_heat_mult=0.25,
                   shield_adjacency_reduction=5.0)
        with mock.patch.dict(BUILDINGS["test_reactor"], {"reactor": cfg}):
            s = GameState(buildings={"test_reactor": 1},
                          resources={"test_fuel": 1, "test_shield": 2})
            engine._ensure_reactor_instances(s)
            instance_id = next(iter(s.reactor_instances))
            engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
            engine.insert_reactor_component(s, instance_id, 1, "test_shield")
            engine.insert_reactor_component(s, instance_id, 2, "test_shield")
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            zone = s.reactor_instances[instance_id]["zones"][0]
            self.assertAlmostEqual(zone["heat"], 1.0)  # heat=4 * пол 0.25

    def test_real_content_has_a_positive_floor(self):
        """Регрессия на сам БАЛАНС: обнулить пол - значит вернуть сборки, которые
        не просят охлаждающей жидкости вовсе."""
        import json, pathlib
        data = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "game" / "content.json").read_text(encoding="utf-8"))
        self.assertGreater(
            data["buildings"]["modular_reactor"]["reactor"]["fuel_min_heat_mult"], 0)

    def test_real_content_shields_cannot_zero_heat(self):
        """Прямая проверка той самой дыры: на РЕАЛЬНОМ контенте стержень,
        обложенный четырьмя улучшенными радиаторами, обязан остаться горячим."""
        import json, pathlib
        data = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "game" / "content.json").read_text(encoding="utf-8"))
        cfg = data["buildings"]["modular_reactor"]["reactor"]
        roles = {r["id"]: r["reactor_component"] for r in data["resources"]
                 if r.get("reactor_component")}
        with mock.patch.object(engine, "RESOURCE_REACTOR_COMPONENT", roles):
            for shield in ("reactor_radiator_improved", "reactor_absorber_improved"):
                grid = [None] * 9
                grid[4] = {"resource_id": "fuel_rod_uranium", "charge": 20}
                for i in (1, 3, 5, 7):
                    grid[i] = {"resource_id": shield, "charge": None}
                heat_mult, _out, _flat = engine._reactor_fuel_adjacency_bonus(grid, 3, 3, 4, cfg)
                self.assertGreater(heat_mult, 0, f"{shield} обнуляет тепло стержня")


class ReactorCatalystTests(FixtureTestCase):
    """Катализаторы решётки (resources.*.reactor_catalyst) - отдельный слот
    ПОВЕРХ уже установленного компонента (cell["catalyst_id"]), не занимает
    ячейку сам. test_catalyst_boost/stabilizer/field (applies_to: fuel,
    self_pct=0.5, neighbor_malus_pct=0.2) и test_catalyst_amplifier
    (applies_to: не-fuel роли). Сетка фикстуры 2x2, индексы как в
    ReactorAdjacencyBonusTests: 0=(0,0) 1=(0,1) / 2=(1,0) 3=(1,1)."""

    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def _config(self):
        from game.engine import BUILDINGS
        return BUILDINGS["test_reactor"]["reactor"]

    def test_bank_view_lists_all_tagged_resources(self):
        s = GameState(resources={"test_catalyst_boost": 2})
        bank = {e["id"]: e for e in engine.reactor_catalyst_bank_view(s)}
        self.assertEqual(bank["test_catalyst_boost"]["applies_to"], ["fuel"])
        self.assertEqual(bank["test_catalyst_boost"]["amount"], 2)
        self.assertEqual(bank["test_catalyst_amplifier"]["amount"], 0)

    def test_insert_rejects_empty_cell(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_catalyst_boost": 1})
        result = engine.insert_reactor_catalyst(s, self._instance(s), 0, "test_catalyst_boost")
        self.assertFalse(result.ok)

    def test_insert_rejects_unknown_catalyst(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        result = engine.insert_reactor_catalyst(s, instance_id, 0, "test_fuel")
        self.assertFalse(result.ok)

    def test_insert_rejects_wrong_role(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_moderator": 1, "test_catalyst_boost": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_moderator")
        result = engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["test_catalyst_boost"], 1)  # не списан

    def test_insert_rejects_without_stock(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        result = engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        self.assertFalse(result.ok)

    def test_insert_rejects_second_catalyst_in_same_cell(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_catalyst_boost": 2})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        result = engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        self.assertFalse(result.ok)
        self.assertEqual(s.resources["test_catalyst_boost"], 1)  # второй не списан

    def test_insert_success_sets_cell_and_spends_stock(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_catalyst_boost": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        result = engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        self.assertTrue(result.ok)
        self.assertEqual(s.reactor_instances[instance_id]["grid"][0]["catalyst_id"], "test_catalyst_boost")
        self.assertEqual(s.resources["test_catalyst_boost"], 0)

    def test_remove_catalyst_returns_it_without_touching_component(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_catalyst_boost": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        result = engine.remove_reactor_catalyst(s, instance_id, 0)
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["test_catalyst_boost"], 1)
        grid_cell = s.reactor_instances[instance_id]["grid"][0]
        self.assertEqual(grid_cell["resource_id"], "test_fuel")  # компонент остался
        self.assertIsNone(grid_cell["catalyst_id"])

    def test_remove_catalyst_rejects_when_none_present(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        result = engine.remove_reactor_catalyst(s, instance_id, 0)
        self.assertFalse(result.ok)

    def test_remove_component_also_returns_its_catalyst(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_catalyst_boost": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_boost")
        result = engine.remove_reactor_component(s, instance_id, 0)
        self.assertTrue(result.ok)
        self.assertEqual(s.resources["test_fuel"], 1)
        self.assertEqual(s.resources["test_catalyst_boost"], 1)
        self.assertIsNone(s.reactor_instances[instance_id]["grid"][0])

    def test_own_boost_catalyst_raises_own_heat_and_output(self):
        grid = [{"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_boost"}, None, None, None]
        eff = engine._reactor_effective_component(grid, 2, 2, 0)
        self.assertAlmostEqual(eff["heat"], 6.0)  # 4 * 1.5
        self.assertAlmostEqual(eff["output"], 9.0)  # 6 * 1.5

    def test_own_stabilizer_catalyst_lowers_burnup_only(self):
        grid = [{"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_stabilizer"}, None, None, None]
        eff = engine._reactor_effective_component(grid, 2, 2, 0)
        self.assertAlmostEqual(eff["heat"], 4.0)  # не тронут
        self.assertAlmostEqual(eff["output"], 6.0)  # не тронут
        self.assertAlmostEqual(eff["burnup_per_turn"], 0.5)  # 1 * (1 - 0.5)

    def test_own_amplifier_catalyst_raises_power_of_non_fuel_role(self):
        grid = [{"resource_id": "test_moderator", "charge": None, "catalyst_id": "test_catalyst_amplifier"}, None, None, None]
        eff = engine._reactor_effective_component(grid, 2, 2, 0)
        self.assertAlmostEqual(eff["power"], 1.5)  # 1 * 1.5

    def test_neighbor_catalyst_malus_reduces_heat_output_power_but_not_self(self):
        # У соседа (клетка 1) стоит катализатор - клетка 0 получает штраф 20%,
        # сама клетка 1 (закатализированная) собственным штрафом себя не бьёт.
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_boost"},
            None, None,
        ]
        eff0 = engine._reactor_effective_component(grid, 2, 2, 0)
        self.assertAlmostEqual(eff0["heat"], 3.2)  # 4 * 0.8 (штраф соседа), без собственного бонуса
        self.assertAlmostEqual(eff0["output"], 4.8)  # 6 * 0.8
        eff1 = engine._reactor_effective_component(grid, 2, 2, 1)
        # клетка 1 сама закатализирована (heat_output +50%), но штрафа от клетки 0 (без катализатора) нет
        self.assertAlmostEqual(eff1["heat"], 6.0)  # 4 * 1.5
        self.assertAlmostEqual(eff1["output"], 9.0)  # 6 * 1.5

    def test_neighbor_malus_stacks_multiplicatively_across_multiple_catalyzed_neighbors(self):
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_boost"},
            {"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_stabilizer"},
            None,
        ]
        eff0 = engine._reactor_effective_component(grid, 2, 2, 0)
        self.assertAlmostEqual(eff0["heat"], 4.0 * 0.8 * 0.8)  # два закатализированных соседа
        self.assertAlmostEqual(eff0["output"], 6.0 * 0.8 * 0.8)

    def test_field_catalyst_amplifies_the_step_it_projects_onto_fuel_neighbor(self):
        # У соседа (клетка 1) - катализатор поля (self_pct=0.5): шаг heat/output,
        # который клетка 1 добавляет соседу-топливу, растёт в 1.5 раза.
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_field"},
            None, None,
        ]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertAlmostEqual(heat_mult, 1.0 + 0.5 * 1.5)  # fuel_adjacency_heat_step=0.5 * 1.5
        self.assertAlmostEqual(output_mult, 1.0 + 0.25 * 1.5)  # fuel_adjacency_output_step=0.25 * 1.5
        self.assertEqual(flat_bonus, 0.0)

    def test_amplified_moderator_gives_bigger_flat_output_bonus(self):
        grid = [
            {"resource_id": "test_fuel", "charge": 3},
            {"resource_id": "test_moderator", "charge": None, "catalyst_id": "test_catalyst_amplifier"},
            None, None,
        ]
        heat_mult, output_mult, flat_bonus = engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, self._config())
        self.assertEqual((heat_mult, output_mult), (1.0, 1.0))
        self.assertAlmostEqual(flat_bonus, 1.5)  # moderator_adjacency_output_bonus(1.0) * power(1 * 1.5)

    def test_process_reactors_applies_stabilizer_to_burnup(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_catalyst_stabilizer": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_catalyst(s, instance_id, 0, "test_catalyst_stabilizer")
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        self.assertAlmostEqual(s.reactor_instances[instance_id]["grid"][0]["charge"], 2.5)  # 3 - 0.5
        self.assertAlmostEqual(s.resources.get("energy", 0), 6.0)  # output не тронут стабилизатором


class ReactorCoolantFluidTests(FixtureTestCase):
    """Охлаждающая ЖИДКОСТЬ (resources.*.reactor_coolant_fluid) - расходник,
    который теплоносители решётки тратят каждый ход. В фикстуре жидкостей нет
    намеренно (тогда движок работает по старому пути, без расходника), поэтому
    здесь константа подменяется точечно."""

    FLUIDS = {
        "test_water": {"cooling_mult": 0.5, "units_per_power": 1.0},
        "test_metal_fluid": {"cooling_mult": 2.0, "units_per_power": 0.5},
    }

    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def _rig(self, fluid_amount=100.0, fluid="test_water"):
        s = GameState(buildings={"test_reactor": 1},
                      resources={"test_fuel": 1, "test_coolant": 1, fluid: fluid_amount})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_component(s, instance_id, 3, "test_coolant")  # не сосед fuel
        engine.set_reactor_coolant_fluid(s, instance_id, fluid)
        return s, instance_id

    def test_without_fluids_in_content_cooling_works_as_before(self):
        """Пустой каталог жидкостей = механика не заведена, старое поведение."""
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_coolant": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_component(s, instance_id, 3, "test_coolant")
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 0.0)

    def test_fluid_is_consumed_and_scales_cooling(self):
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig()
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            # power=5, flow=100% -> расход 5*1.0=5 единиц; снятие 5*0.5=2.5 при heat=4
            self.assertAlmostEqual(s.resources["test_water"], 95.0)
            self.assertAlmostEqual(s.reactor_instances[iid]["zones"][0]["heat"], 1.5)

    def test_better_fluid_cools_more_and_costs_fewer_units(self):
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig(fluid="test_metal_fluid")
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            # cooling_mult=2.0 -> снятие 10 > heat=4, поэтому тепло в пол;
            # units_per_power=0.5 -> расход 5*0.5=2.5
            self.assertAlmostEqual(s.resources["test_metal_fluid"], 97.5)
            self.assertEqual(s.reactor_instances[iid]["zones"][0]["heat"], 0.0)

    def test_flow_scales_both_cooling_and_consumption(self):
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig()
            engine.set_reactor_zone_coolant(s, iid, 0, 50)
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            self.assertAlmostEqual(s.resources["test_water"], 97.5)  # 5*1.0*0.5
            # снятие 5*0.5(мощность жидкости)*0.5(поток) = 1.25 при heat=4
            self.assertAlmostEqual(s.reactor_instances[iid]["zones"][0]["heat"], 2.75)

    def test_shortage_degrades_cooling_proportionally_not_to_zero(self):
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig(fluid_amount=2.0)  # нужно 5, есть 2 -> 40% охлаждения
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            self.assertAlmostEqual(s.resources["test_water"], 0.0)
            self.assertAlmostEqual(s.reactor_instances[iid]["zones"][0]["heat"], 4.0 - 2.5 * 0.4)

    def test_empty_tank_means_no_cooling_at_all(self):
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig(fluid_amount=0.0)
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            self.assertAlmostEqual(s.reactor_instances[iid]["zones"][0]["heat"], 4.0)

    def test_drained_circuit_rejects_unknown_fluid_but_accepts_none(self):
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig()
            self.assertFalse(engine.set_reactor_coolant_fluid(s, iid, "test_fuel").ok)
            self.assertTrue(engine.set_reactor_coolant_fluid(s, iid, None).ok)
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            self.assertAlmostEqual(s.reactor_instances[iid]["zones"][0]["heat"], 4.0)
            self.assertAlmostEqual(s.resources["test_water"], 100.0)  # сухой контур ничего не тратит

    def test_projection_matches_what_actually_happens_on_shortage(self):
        """Прогноз телеметрии обязан учитывать нехватку жидкости так же, как ход."""
        with mock.patch.dict(engine.RESOURCE_REACTOR_COOLANT_FLUID, self.FLUIDS, clear=True):
            s, iid = self._rig(fluid_amount=2.0)
            s.reactor_instances[iid]["upgrades"].append("test_reactor_telemetry")
            proj = engine.reactor_zone_projection(s, iid)
            self.assertIsNotNone(proj)
            self.assertTrue(proj[0]["fluid_short"])
            predicted = proj[0]["next_heat"]
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            self.assertAlmostEqual(s.reactor_instances[iid]["zones"][0]["heat"], predicted)


class ReactorControlRodBurnupTests(FixtureTestCase):
    """control_rod_burnup_factor: стержни притормаживают и РАСХОД ТОПЛИВА, а не
    только выработку. Без него глубина была чистой потерей (выход падал, заряд
    таял в прежнем темпе), и рычаг у грамотного игрока всегда стоял на нуле."""

    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def _run(self, depth, factor):
        from game.engine import BUILDINGS
        config = dict(BUILDINGS["test_reactor"]["reactor"], control_rod_burnup_factor=factor)
        patched = dict(BUILDINGS["test_reactor"], reactor=config)
        with mock.patch.dict(BUILDINGS, {"test_reactor": patched}):
            s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
            iid = self._instance(s)
            engine.insert_reactor_component(s, iid, 0, "test_fuel")
            engine.set_reactor_zone_control(s, iid, 0, depth)
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
            return s.reactor_instances[iid]["grid"][0]["charge"], s.resources.get("energy", 0)

    def test_factor_zero_keeps_old_behaviour(self):
        charge, energy = self._run(depth=100, factor=0.0)
        self.assertEqual(charge, 2)               # заряд 3 - 1, как раньше
        self.assertAlmostEqual(energy, 0.6)       # но выход придавлен

    def test_factor_one_slows_burnup_in_step_with_output(self):
        charge, energy = self._run(depth=100, factor=1.0)
        self.assertAlmostEqual(charge, 2.9)       # damp=0.1 -> сгорело 0.1 вместо 1
        self.assertAlmostEqual(energy, 0.6)

    def test_no_depth_means_no_difference(self):
        self.assertEqual(self._run(depth=0, factor=1.0), self._run(depth=0, factor=0.0))


class ReactorHeatDampExponentTests(FixtureTestCase):
    """control_rod_heat_damp_exponent: рычаг душит ТЕПЛО сильнее, чем выход.

    Без него погружение меняло оба показателя ровно одинаково, поэтому глубина
    была чистой потерей темпа и у грамотного игрока всегда стояла на нуле. При
    степени больше 1 она УЛУЧШАЕТ отношение тепла к выходу, то есть освобождает
    тепловой запас, который можно обменять на клетки под топливо."""

    def _next(self, depth, exponent):
        zone = {"heat": 0.0, "control_rod_depth": depth, "coolant_flow": 0}
        return engine._reactor_zone_next_state(zone, 100.0, 100.0, 0.0, 10, 1000.0, 0.6, exponent)

    def test_exponent_one_damps_both_equally(self):
        heat, _ratio, output, _trip = self._next(50, 1.0)
        self.assertAlmostEqual(heat, 55.0)    # damp = 1 - 0.5*0.9 = 0.55
        self.assertAlmostEqual(output, 55.0)

    def test_default_argument_keeps_old_behaviour(self):
        zone = {"heat": 0.0, "control_rod_depth": 50, "coolant_flow": 0}
        heat, _r, output, _t = engine._reactor_zone_next_state(zone, 100.0, 100.0, 0.0, 10, 1000.0, 0.6)
        self.assertAlmostEqual(heat, output)

    def test_exponent_two_damps_heat_quadratically(self):
        heat, _ratio, output, _trip = self._next(50, 2.0)
        self.assertAlmostEqual(heat, 30.25)   # 0.55^2
        self.assertAlmostEqual(output, 55.0)  # выход - всегда линейно

    def test_deeper_rods_improve_heat_per_output(self):
        ratios = []
        for depth in (0, 25, 50, 75):
            heat, _r, output, _t = self._next(depth, 2.0)
            ratios.append(heat / output)
        self.assertEqual(ratios, sorted(ratios, reverse=True))  # отношение строго улучшается

    def test_real_content_uses_exponent_above_one(self):
        import json, pathlib
        data = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "game" / "content.json").read_text(encoding="utf-8"))
        self.assertGreater(
            data["buildings"]["modular_reactor"]["reactor"]["control_rod_heat_damp_exponent"], 1)


class ReactorCatalystSelfPctHeatTests(FixtureTestCase):
    """reactor_catalyst.self_pct_heat - ТЕПЛОВАЯ сторона катализатора может расти
    иначе, чем выходная (в том числе УБЫВАТЬ).

    Зачем: соседство топлива с топливом само по себе невыгодно - оно добавляет
    больше тепла (fuel_adjacency_heat_step), чем выхода
    (fuel_adjacency_output_step). Катализатор поля, множивший обе стороны
    ОДИНАКОВО, тем самым лишь усиливал заведомо плохую сделку и не окупался
    никогда, ни при каких значениях self_pct. Отрицательный self_pct_heat даёт
    ему собственный смысл: он ГАСИТ взаимный разогрев, усиливая взаимную
    подпитку, и тем самым локально легализует плотную набивку топлива.
    То же поле работает и для "heat_output" (форсаж).

    Поле необязательное: его отсутствие обязано воспроизводить ровно прежнее
    поведение, иначе уже сохранённый контент сломался бы молча."""

    def _config(self, **over):
        from game.engine import BUILDINGS
        return dict(BUILDINGS["test_reactor"]["reactor"], **over)

    def _pair(self, catalyst_id, cfg=None):
        """Два топливных соседа; катализатор стоит на ВТОРОМ, меряем ПЕРВЫЙ."""
        grid = [{"resource_id": "test_fuel", "charge": 3},
                {"resource_id": "test_fuel", "charge": 3, "catalyst_id": catalyst_id},
                None, None]
        return engine._reactor_fuel_adjacency_bonus(grid, 2, 2, 0, cfg or self._config())

    def _patch_catalyst(self, cat_id, **over):
        from game.engine import RESOURCE_REACTOR_CATALYST
        patched = dict(RESOURCE_REACTOR_CATALYST)
        patched[cat_id] = dict(patched[cat_id], **over)
        return mock.patch.object(engine, "RESOURCE_REACTOR_CATALYST", patched)

    def test_field_scales_both_sides_when_absent(self):
        # Фикстура: heat_step=0.5, output_step=0.25, self_pct=0.5 -> обе x1.5.
        heat_mult, output_mult, _ = self._pair("test_catalyst_field")
        self.assertAlmostEqual(heat_mult, 1 + 0.5 * 1.5)
        self.assertAlmostEqual(output_mult, 1 + 0.25 * 1.5)

    def test_field_can_damp_heat_side(self):
        with self._patch_catalyst("test_catalyst_field", self_pct_heat=-0.6):
            heat_mult, output_mult, _ = self._pair("test_catalyst_field")
        self.assertAlmostEqual(heat_mult, 1 + 0.5 * 0.4)    # тепловая сторона x0.4
        self.assertAlmostEqual(output_mult, 1 + 0.25 * 1.5)  # выходная как была

    def test_field_damping_improves_the_adjacency_trade(self):
        """Суть правки: соседство должно стать ВЫГОДНЕЕ, а не просто крупнее."""
        plain_h, plain_o, _ = self._pair("test_catalyst_field")
        with self._patch_catalyst("test_catalyst_field", self_pct_heat=-0.6):
            damp_h, damp_o, _ = self._pair("test_catalyst_field")
        # без правки курс сделки не меняется вовсе - обе стороны множатся поровну
        self.assertAlmostEqual((plain_o - 1) / (plain_h - 1), 0.25 / 0.5)
        self.assertGreater((damp_o - 1) / (damp_h - 1), (plain_o - 1) / (plain_h - 1))

    def test_overdrive_scales_both_sides_when_absent(self):
        grid = [{"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_boost"},
                None, None, None]
        eff = engine._reactor_effective_component(grid, 2, 2, 0, self._config())
        self.assertAlmostEqual(eff["heat"], 4.0 * 1.5)
        self.assertAlmostEqual(eff["output"], 6.0 * 1.5)

    def test_overdrive_heat_side_can_lag_output(self):
        grid = [{"resource_id": "test_fuel", "charge": 3, "catalyst_id": "test_catalyst_boost"},
                None, None, None]
        with self._patch_catalyst("test_catalyst_boost", self_pct_heat=0.2):
            eff = engine._reactor_effective_component(grid, 2, 2, 0, self._config())
        self.assertAlmostEqual(eff["heat"], 4.0 * 1.2)
        self.assertAlmostEqual(eff["output"], 6.0 * 1.5)

    def test_real_content_field_catalyst_damps_heat(self):
        """Регрессия на БАЛАНС: без гашения тепла катализатор поля снова станет
        усилителем невыгодной сделки, то есть мёртвым контентом."""
        import json, pathlib
        data = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "game" / "content.json").read_text(encoding="utf-8"))
        field = next(r["reactor_catalyst"] for r in data["resources"]
                     if r.get("reactor_catalyst", {}).get("self_stat") == "field")
        self.assertLess(field["self_pct_heat"], 0)
        reactor = data["buildings"]["modular_reactor"]["reactor"]
        # проверяем ИМЕННО то, ради чего правка: закатализированное соседство
        # должно давать выхода на единицу тепла больше, чем самый выгодный стержень
        rate = (reactor["fuel_adjacency_output_step"] * (1 + field["self_pct"])
                / (reactor["fuel_adjacency_heat_step"] * (1 + field["self_pct_heat"])))
        best_rod = max(r["reactor_component"]["output"] / r["reactor_component"]["heat"]
                       for r in data["resources"]
                       if r.get("reactor_component", {}).get("role") == "fuel")
        self.assertGreater(rate, 1.0)
        self.assertLess(rate, best_rod * 1.5)  # но не настолько, чтобы забить всё


class ReactorCatalystMalusRadiusTests(FixtureTestCase):
    """catalyst_malus_radius расширяет окно ШТРАФА катализатора с 4 ортогональных
    клеток до квадрата (2r+1)x(2r+1). Без этого шахматная раскладка делала штраф
    бесплатным: у замедлителя в шахматке все ортогональные соседи - топливо, а
    плоский бонус замедлителя прибавляется ПОСЛЕ множителей и штрафом не
    задевается, из-за чего "закатализировать все замедлители" было очевидным и
    безальтернативным решением."""

    def _diag_grid(self):
        # 2x2: катализатор в (0,0), проверяемая ячейка по ДИАГОНАЛИ в (1,1)
        return [
            {"resource_id": "test_moderator", "charge": None, "catalyst_id": "test_catalyst_amplifier"},
            None,
            None,
            {"resource_id": "test_moderator", "charge": None},
        ]

    def test_diagonal_neighbour_untouched_without_radius(self):
        eff = engine._reactor_effective_component(self._diag_grid(), 2, 2, 3, None)
        self.assertAlmostEqual(eff["power"], 1.0)  # базовая сила фикстуры, штрафа нет

    def test_diagonal_neighbour_penalised_with_radius_one(self):
        eff = engine._reactor_effective_component(
            self._diag_grid(), 2, 2, 3, {"catalyst_malus_radius": 1})
        self.assertAlmostEqual(eff["power"], 1.0 * (1 - 0.2))  # штраф фикстуры 20%

    def test_radius_one_is_superset_of_orthogonal(self):
        grid = [
            {"resource_id": "test_moderator", "charge": None, "catalyst_id": "test_catalyst_amplifier"},
            {"resource_id": "test_moderator", "charge": None},
            None, None,
        ]
        ortho = engine._reactor_effective_component(grid, 2, 2, 1, None)["power"]
        wide = engine._reactor_effective_component(grid, 2, 2, 1, {"catalyst_malus_radius": 1})["power"]
        self.assertAlmostEqual(ortho, wide)  # ортогональный сосед штрафуется в обоих случаях

    def test_real_content_uses_wide_window(self):
        import json, pathlib
        data = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "game" / "content.json").read_text(encoding="utf-8"))
        reactor = data["buildings"]["modular_reactor"]["reactor"]
        self.assertGreaterEqual(reactor["catalyst_malus_radius"], 1)
        self.assertGreater(reactor["control_rod_burnup_factor"], 0)


class ProcessReactorsTests(FixtureTestCase):
    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_single_fuel_cell_produces_energy_accumulates_heat_and_burns_up(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        report = engine.ProductionReport()
        engine._process_reactors(s, report, daytime=True)
        self.assertAlmostEqual(s.resources.get("energy", 0), 6.0)  # output=6, без соседей/демпинга
        zone = s.reactor_instances[instance_id]["zones"][0]
        self.assertAlmostEqual(zone["heat"], 4.0)  # heat=4, coolant=0
        self.assertEqual(s.reactor_instances[instance_id]["grid"][0]["charge"], 2)  # 3 - burnup(1)

    def test_burnt_out_fuel_stops_producing_without_going_negative(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        for _ in range(3):  # charge_max=3, burnup=1/ход -> выгорает ровно за 3 хода
            engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        self.assertEqual(s.reactor_instances[instance_id]["grid"][0]["charge"], 0)
        energy_after_burnout = s.resources.get("energy", 0)
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        self.assertEqual(s.resources.get("energy", 0), energy_after_burnout)  # больше не производит
        self.assertEqual(s.reactor_instances[instance_id]["grid"][0]["charge"], 0)  # и не уходит в минус

    def test_coolant_removes_heat_scaled_by_flow(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_coolant": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_component(s, instance_id, 3, "test_coolant")  # НЕ сосед fuel (по диагонали)
        engine.set_reactor_zone_coolant(s, instance_id, 0, 50)
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        # heat=4 (без соседства, coolant по диагонали не бонусит fuel), removed=5*0.5=2.5
        zone = s.reactor_instances[instance_id]["zones"][0]
        self.assertAlmostEqual(zone["heat"], 1.5)

    def test_coolant_clamps_heat_at_zero_not_negative(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1, "test_coolant": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.insert_reactor_component(s, instance_id, 3, "test_coolant")
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)  # flow=100% по умолчанию, removed=5 > heat=4
        zone = s.reactor_instances[instance_id]["zones"][0]
        self.assertEqual(zone["heat"], 0.0)

    def test_control_rod_depth_dampens_both_heat_and_output(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        engine.set_reactor_zone_control(s, instance_id, 0, 100)  # damp = 1 - 1*0.9 = 0.1
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        self.assertAlmostEqual(s.resources.get("energy", 0), 0.6)  # 6 * 0.1
        zone = s.reactor_instances[instance_id]["zones"][0]
        self.assertAlmostEqual(zone["heat"], 0.4)  # 4 * 0.1

    def test_soft_threshold_degrades_output_but_not_below_zero(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 25.0  # cap=4*10=40, 25/40=0.625 > soft 0.5
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        # heat после начисления: 25+4=29, ratio=29/40=0.725, falloff=(0.725-0.5)/0.5=0.45
        self.assertAlmostEqual(s.resources.get("energy", 0), 6.0 * 0.55)
        zone = s.reactor_instances[instance_id]["zones"][0]
        self.assertAlmostEqual(zone["heat"], 29.0)

    def test_hard_threshold_rolls_accident_and_resets_heat(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        s.reactor_instances[instance_id]["zones"][0]["heat"] = 40.0  # уже на пределе (cap=40)
        report = engine.ProductionReport()
        with mock.patch("random.uniform", return_value=1.0):  # entries: weight 3 (destroy_one_fuel_cell), 1 (destroy_zone_grid); uniform(0,4)=1.0 -> первый
            engine._process_reactors(s, report, daytime=True)
        self.assertEqual(len(report.reactor_accidents), 1)
        entry = report.reactor_accidents[0]
        self.assertEqual(entry["result"], "destroy_one_fuel_cell")
        self.assertEqual(entry["instance_id"], instance_id)
        self.assertEqual(s.reactor_instances[instance_id]["grid"][0], None)  # единственная fuel-ячейка уничтожена
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 0.0)

    def test_broken_instance_produces_nothing(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        instance_id = self._instance(s)
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        s.reactor_instances[instance_id]["broken"] = True
        engine._process_reactors(s, engine.ProductionReport(), daytime=True)
        self.assertEqual(s.resources.get("energy", 0), 0)
        self.assertEqual(s.reactor_instances[instance_id]["zones"][0]["heat"], 0.0)


class ReactorUpgradePurchaseTests(FixtureTestCase):
    def _instance(self, s):
        engine._ensure_reactor_instances(s)
        return next(iter(s.reactor_instances))

    def test_purchase_zoning_upgrade_changes_zone_count(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 10})
        instance_id = self._instance(s)
        result = engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_zoning_2")
        self.assertTrue(result.ok)
        self.assertEqual(len(s.reactor_instances[instance_id]["zones"]), 2)
        self.assertEqual(s.resources["raw_a"], 7)  # 10 - cost(3)

    def test_purchase_grid_size_upgrade_grows_grid_immediately(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 10})
        instance_id = self._instance(s)
        result = engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_grid_3x2")
        self.assertTrue(result.ok)
        self.assertEqual(len(s.reactor_instances[instance_id]["grid"]), 6)

    def test_purchase_rejects_without_enough_resources(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 1})
        result = engine.purchase_reactor_instance_upgrade(s, self._instance(s), "test_reactor_zoning_2")
        self.assertFalse(result.ok)

    def test_zoning_upgrade_purchased_twice_is_rejected_not_stacked(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 10})
        instance_id = self._instance(s)
        engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_zoning_2")
        result = engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_zoning_2")
        self.assertFalse(result.ok)

    def test_deferred_reactor_instance_upgrade_is_registered_in_pending_retry(self):
        # Регрессия: _pay_or_defer заводит запись в state.pending_actions с
        # kind="reactor_instance_upgrade" (см. purchase_reactor_instance_upgrade),
        # но _PENDING_RETRY эту kind раньше не знал вовсе - _process_pending_actions
        # падал KeyError на КАЖДОМ следующем ходу/действии, и сломанная запись
        # никогда не снималась с очереди (падение происходит раньше, чем
        # state.pending_actions переприсваивается). Тут - не через реальную
        # отложенную покупку (фикстура raw_a не автокрафтится в фоне), а прямая
        # проверка, что _process_pending_actions вообще умеет эту kind, вручную
        # положив запись в очередь, как это сделал бы _pay_or_defer.
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 10})
        instance_id = self._instance(s)
        s.orders.append({
            "id": 1, "kind": "reactor_instance_upgrade", "target_id": f"{instance_id}:test_reactor_zoning_2", "label": "улучшение (тест)",
            "reserved": {}, "steps": [], "energy_reserve": 0, "final_energy": 0, "status": None, "waits_energy": False,
        })
        report = engine.ProductionReport()
        engine._process_orders(s, report, {})  # не должно бросить KeyError
        self.assertIn("test_reactor_zoning_2", s.reactor_instances[instance_id]["upgrades"])
        self.assertEqual(s.orders, [])
        self.assertIn("улучшение (тест)", report.pending_finished)

    def test_telemetry_upgrade_flag(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 10})
        instance_id = self._instance(s)
        self.assertFalse(engine.reactor_telemetry_available(s, instance_id))
        engine.purchase_reactor_instance_upgrade(s, instance_id, "test_reactor_telemetry")
        self.assertTrue(engine.reactor_telemetry_available(s, instance_id))

    def test_purchase_rejects_unknown_upgrade(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"raw_a": 10})
        result = engine.purchase_reactor_instance_upgrade(s, self._instance(s), "nope")
        self.assertFalse(result.ok)

    def test_purchase_rejects_unknown_instance(self):
        s = GameState()
        result = engine.purchase_reactor_instance_upgrade(s, "nope", "test_reactor_zoning_2")
        self.assertFalse(result.ok)


class ReactorInstancesViewTests(FixtureTestCase):
    def test_view_reports_grid_zones_and_names(self):
        s = GameState(buildings={"test_reactor": 1}, resources={"test_fuel": 1})
        engine._ensure_reactor_instances(s)
        instance_id = next(iter(s.reactor_instances))
        engine.insert_reactor_component(s, instance_id, 0, "test_fuel")
        view = engine.reactor_instances_view(s)
        self.assertEqual(len(view), 1)
        entry = view[0]
        self.assertEqual(entry["name"], "Реактор (тест) №1")
        self.assertEqual((entry["width"], entry["height"]), (2, 2))
        self.assertEqual(len(entry["grid"]), 4)
        self.assertEqual(entry["grid"][0]["resource_id"], "test_fuel")
        self.assertEqual(entry["grid"][0]["role"], "fuel")
        self.assertEqual(entry["grid"][0]["zone_index"], 0)
        self.assertIsNone(entry["grid"][1])
        self.assertEqual(len(entry["zones"]), 1)
        self.assertEqual(entry["zones"][0]["heat_cap"], 40)  # 4 клетки * 10
        self.assertFalse(entry["telemetry"])
        self.assertIsNone(entry["projection"])  # нет телеметрии - нет прогноза


if __name__ == "__main__":
    unittest.main()
