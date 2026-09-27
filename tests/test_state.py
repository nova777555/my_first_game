"""Тесты сохранения/загрузки GameState. Пишет только во временные файлы."""
import tempfile
import unittest
from pathlib import Path

from game.state import GameState


class StateRoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self):
        s = GameState(
            turn=7,
            resources={"wood": 12, "energy": 3},
            buildings={"lumber_camp": 2},
            techs={"woodworking", "toolmaking"},
            broken={"lumber_camp": 1},
            broken_types={"lumber_camp": ["worn_gear"]},
            tool_durability={"wrench": 1},
            building_disabled={"lumber_camp": 1},
            pinned=["wood", "ore"],
            tool_tiers={"pickaxe": 3},
            active_mine_field={"size": 2, "cells": [{"resource": "ore", "hp": 1, "hp_max": 2, "broken": False, "revealed": True}]},
            energy_jobs=[{"recipe_id": "metal_from_energy", "progress": 40}],
            save_name="максимум",
            orders=[{"id": 4, "kind": "recipe", "target_id": "craft_mid", "label": "«x» ×2",
                     "reserved": {"raw_a": 8}, "steps": [], "energy_reserve": 0, "final_energy": 0,
                     "status": None, "waits_energy": False, "qty": 2}],
            next_order_id=5,
            construction_progress={"furnace": 2},
            building_upgrades={"sawmill": ["sawmill_scale_2"]},
            action_priority={"craft_mid": 7},
            storage_tier={"wood": 1},
            creatures={
                "creature_1": {
                    "species_id": "bee", "genotype": {"species": ["bee", "bee"], "lifespan": ["normal", "short"]},
                    "farm_instance_id": "farm_1", "turns_alive": 4, "identified": False,
                },
            },
            next_creature_id=2,
            known_alleles={"lifespan": ["normal", "short"], "species": ["bee"]},
            species_discovered={"bee", "royal_bee"},
            farm_instances={"farm_1": {"building_id": "apiary", "broken": False, "upgrades": ["apiary_scale"]}},
            next_farm_instance_id=2,
            soul_collector_instances={
                "soul_inst_1": {"building_id": "soul_synth", "broken": False, "broken_type": None,
                                 "upgrades": ["soul_synth_slot"], "installed": ["wolf_soul"]},
            },
            next_soul_instance_id=2,
            pinned_recipes=["craft_planks"],
        )
        restored = GameState.from_dict(s.to_dict())
        self.assertEqual(restored.turn, 7)
        self.assertEqual(restored.resources, {"wood": 12, "energy": 3})
        self.assertEqual(restored.buildings, {"lumber_camp": 2})
        self.assertEqual(restored.techs, {"woodworking", "toolmaking"})
        self.assertEqual(restored.broken, {"lumber_camp": 1})
        self.assertEqual(restored.broken_types, {"lumber_camp": ["worn_gear"]})
        self.assertEqual(restored.tool_durability, {"wrench": 1})
        self.assertEqual(restored.building_disabled, {"lumber_camp": 1})
        self.assertEqual(restored.pinned, ["wood", "ore"])
        self.assertEqual(restored.tool_tiers, {"pickaxe": 3})
        self.assertEqual(
            restored.active_mine_field,
            {"size": 2, "cells": [{"resource": "ore", "hp": 1, "hp_max": 2, "broken": False, "revealed": True}]},
        )
        self.assertEqual(restored.energy_jobs, [{"recipe_id": "metal_from_energy", "progress": 40}])
        self.assertEqual(restored.save_name, "максимум")
        self.assertEqual(restored.orders[0]["reserved"], {"raw_a": 8})
        self.assertEqual(restored.next_order_id, 5)
        self.assertEqual(restored.construction_progress, {"furnace": 2})
        self.assertEqual(restored.building_upgrades, {"sawmill": ["sawmill_scale_2"]})
        self.assertEqual(restored.action_priority, {"craft_mid": 7})
        self.assertEqual(restored.storage_tier, {"wood": 1})
        self.assertEqual(restored.creatures, {
            "creature_1": {
                "species_id": "bee", "genotype": {"species": ["bee", "bee"], "lifespan": ["normal", "short"]},
                "farm_instance_id": "farm_1", "turns_alive": 4, "identified": False,
            },
        })
        self.assertEqual(restored.next_creature_id, 2)
        self.assertEqual(restored.known_alleles, {"lifespan": ["normal", "short"], "species": ["bee"]})
        self.assertEqual(restored.species_discovered, {"bee", "royal_bee"})
        self.assertEqual(restored.farm_instances, {"farm_1": {"building_id": "apiary", "broken": False, "upgrades": ["apiary_scale"]}})
        self.assertEqual(restored.next_farm_instance_id, 2)
        self.assertEqual(restored.soul_collector_instances, {
            "soul_inst_1": {"building_id": "soul_synth", "broken": False, "broken_type": None,
                             "upgrades": ["soul_synth_slot"], "installed": ["wolf_soul"]},
        })
        self.assertEqual(restored.next_soul_instance_id, 2)
        self.assertEqual(restored.pinned_recipes, ["craft_planks"])

    def test_old_save_creature_key_farm_building_id_is_read_as_farm_instance_id(self):
        # Сохранение старше переименования поля (см. CLAUDE.md, "Пасеки:
        # индивидуальные экземпляры") хранило id ТИПА постройки под старым ключом -
        # from_dict обязан подставить это же значение под НОВЫМ ключом as-is (без
        # попытки тут же его исправить - этим занимается engine._ensure_farm_instances
        # при первом обращении), а не терять его молча и не падать с KeyError.
        restored = GameState.from_dict({
            "creatures": {
                "creature_1": {
                    "species_id": "bee", "genotype": {"species": ["bee", "bee"]},
                    "farm_building_id": "apiary", "turns_alive": 1, "identified": False,
                },
            },
        })
        self.assertEqual(restored.creatures["creature_1"]["farm_instance_id"], "apiary")
        self.assertNotIn("farm_building_id", restored.creatures["creature_1"])

    def test_defaults_on_missing_fields(self):
        restored = GameState.from_dict({})
        self.assertEqual(restored.turn, 1)
        self.assertEqual(restored.resources, {})
        # Старое сохранение без этих полей (появились вместе с типами поломки/
        # прочностью инструментов) - пустые словари, а не KeyError.
        self.assertEqual(restored.broken_types, {})
        self.assertEqual(restored.tool_durability, {})
        self.assertEqual(restored.building_disabled, {})
        # пусто, а не конкретные id - список ресурсов вообще не знает о конкретном контенте
        self.assertEqual(restored.pinned, [])
        # пусто, а не {"pickaxe": 1} - тир по умолчанию 1 вычисляет engine.tool_tier(),
        # а не state.py (который ничего не знает о конкретных id инструментов).
        self.assertEqual(restored.tool_tiers, {})
        self.assertEqual(restored.energy_jobs, [])
        self.assertIsNone(restored.save_name)
        # Старые сохранения: заказов нет, нумерация с 1 (autocraft_jobs/pending_actions
        # прежнего автокрафта просто отбрасываются - в них ничего не было зарезервировано).
        self.assertEqual((restored.orders, restored.next_order_id), ([], 1))
        self.assertEqual(restored.construction_progress, {})
        self.assertEqual(restored.building_upgrades, {})
        self.assertEqual(restored.action_priority, {})
        self.assertEqual(restored.storage_tier, {})
        self.assertIsNone(restored.active_mine_field)
        self.assertEqual(restored.creatures, {})
        self.assertEqual(restored.next_creature_id, 1)
        self.assertEqual(restored.known_alleles, {})
        self.assertEqual(restored.species_discovered, set())
        self.assertEqual(restored.farm_instances, {})
        self.assertEqual(restored.next_farm_instance_id, 1)
        self.assertEqual(restored.soul_collector_instances, {})
        self.assertEqual(restored.next_soul_instance_id, 1)
        self.assertEqual(restored.soul_slots, {})
        # Старое сохранение старше сеточного крафта (см. PLAN_grid_crafting.md) -
        # пустой список, а не KeyError.
        self.assertEqual(restored.pinned_recipes, [])

    def test_save_and_load_file(self):
        s = GameState(turn=3, resources={"wood": 5}, tool_tiers={"pickaxe": 2})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "save.json"
            s.save(str(path))
            self.assertTrue(path.exists())
            loaded = GameState.load(str(path))
            self.assertEqual(loaded.turn, 3)
            self.assertEqual(loaded.resources, {"wood": 5})
            self.assertEqual(loaded.tool_tiers, {"pickaxe": 2})

    def test_failed_save_keeps_the_previous_file(self):
        # Запись прервалась посреди (здесь - несериализуемое значение): прежний
        # файл цел, а не обнулён - иначе с ним игра уже не запустилась бы.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "save.json"
            GameState(turn=3).save(str(path))
            broken = GameState(turn=4, resources={"wood": object()})
            with self.assertRaises(TypeError):
                broken.save(str(path))
            self.assertEqual(GameState.load(str(path)).turn, 3)


if __name__ == "__main__":
    unittest.main()
