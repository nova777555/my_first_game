"""
Генератор на излишках энергии и репликатор (game/engine.py).

- Постройка с energy_surplus_only берёт энергию ПРЕДПОСЛЕДНЕЙ в ходу: после обычных
  потребителей (независимо от порядка постройки), заказов и кликов, ждущих энергию, но
  до преобразователей; энергию, которую копят заказы (energy_reserved), не трогает.
- Репликатор (buildings.*.replicator) делает ресурс с ценой репликации
  (resources.*.replication_cost - материи за единицу, может быть дробной): материя
  списывается при запуске целиком, переработка - не быстрее лимита за ход, ресурс
  приходит по мере переработки, отмена возвращает непереработанное.
- Автокрафт берёт ресурс с репликатора только при купленной «Автоматизации»: сырьё без
  рецепта, запасной путь, когда основной упёрся в нехватку, и основной путь, если игрок
  поднял приоритет "replicator:<ресурс>". Заказ «Репликация ×N».

Мини-контент добавляется поверх фикстуры в setUp.

Запуск: python -m unittest tests.test_replicator -v
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


def _building(name, **extra):
    b = {
        "name": name, "description": f"{name} (тест репликатора).", "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
    }
    b.update(extra)
    return b


EXTRA_BUILDINGS = {
    "liq_gen": _building(
        "Тест-генератор материи", consumes={"energy": 2}, produces={"matter": 1}, energy_surplus_only=True,
    ),
    "repl": _building(
        "Тест-репликатор", replicator={"matter": "matter", "max_matter_per_turn": 2, "slots": 1},
        upgrades=[
            {"id": "repl_auto", "name": "Автоматизация", "description": "Автокрафт репликатора.",
             "type": "autocraft", "value": 1, "requires_tech": None, "cost": {}},
            {"id": "repl_speed", "name": "Насос", "description": "+2 материи/ход.",
             "type": "replicator_throughput", "value": 2, "requires_tech": None, "cost": {},
             "repeatable": True, "cost_growth": 1.5},
        ],
    ),
    "e_bank": _building("Тест-банк энергии", energy_storage=50),
    "cpu": _building("Тест-процессор", compute_power=1000),
}
EXTRA_ACTIONS = {
    "spark": {"name": "Тест: искра", "requires_tech": None, "location": "yard",
              "cost": {"energy": 6}, "gain": {"e_spark": 1}},
    "use_mid": {"name": "Тест: из полуфабриката", "requires_tech": None, "location": "workshop_loc",
                "cost": {"mid": 1}, "gain": {"u_item": 1}},
}
EXTRA_NAMES = {"matter": "Тест-материя", "e_spark": "Тест-искра", "u_item": "Тест-изделие"}
PRICES = {"raw_a": 0.5, "raw_b": 0.25, "mid": 3, "limited_res": 1}


def rk(resource_id):
    """Ключ заказа/приоритета репликации (engine.grid_recipe_key)."""
    return engine.grid_recipe_key("replicator", resource_id)


class ReplicatorTestCase(unittest.TestCase):
    def setUp(self):
        patch = fixtures.patched_engine_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        engine.BUILDINGS = {**copy.deepcopy(engine.BUILDINGS), **copy.deepcopy(EXTRA_BUILDINGS)}
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, **copy.deepcopy(EXTRA_ACTIONS)}
        engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, **EXTRA_NAMES}
        engine.RESOURCE_FRACTIONAL = {**engine.RESOURCE_FRACTIONAL, "matter": True}
        engine.RESOURCE_REPLICATION_COST = dict(PRICES)

    @staticmethod
    def state(automated=False, **kw):
        buildings = {"repl": 1}
        buildings.update(kw.pop("buildings", {}))
        s = GameState(buildings=buildings, **kw)
        if automated:
            s.building_upgrades["repl"] = ["repl_auto"]
        return s

    @staticmethod
    def turns(s, n):
        for _ in range(n):
            engine.advance_turn(s)


class SurplusGeneratorTests(ReplicatorTestCase):
    def test_ordinary_consumer_goes_first_regardless_of_build_order(self):
        # Генератор материи построен РАНЬШЕ фабрики - обычным потребителем он забрал бы
        # всю энергию первым.
        s = GameState(buildings={"liq_gen": 1, "gen": 1, "factory": 1})
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 2)  # фабрика получила свою 1 энергию
        self.assertEqual(s.resources.get("matter", 0), 0)  # остатка 1 не хватило генератору

    def test_goes_before_converters(self):
        s = GameState(buildings={"gen": 1, "liq_gen": 1, "energy_converter": 1})
        self.assertTrue(engine.start_energy_recipe(s, "make_raw_b").ok)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("matter"), 1)
        self.assertEqual(s.energy_jobs[0]["progress"], 0)

    def test_does_not_eat_energy_a_waiting_click_accumulates(self):
        s = GameState(buildings={"gen": 2, "liq_gen": 1, "e_bank": 1})
        result = engine.do_manual_action(s, "spark")
        self.assertTrue(result.pending, result.message)
        engine.advance_turn(s)  # +4, всё копится под клик (нужно 6)
        self.assertEqual(s.resources.get("matter", 0), 0)
        self.assertEqual(s.resources.get("energy"), 4)
        engine.advance_turn(s)  # 8 >= 6: клик выполнен, остаток 2 - генератору материи
        self.assertEqual(s.resources.get("e_spark"), 1)
        self.assertEqual(s.resources.get("matter"), 1)

    def test_energy_wait_estimate_ignores_surplus_consumption(self):
        s = GameState(buildings={"gen": 1, "liq_gen": 1})
        self.assertEqual(engine.energy_breakdown(s)[1:], (2, 2))
        self.assertEqual(engine.energy_wait_eta_turns(s, 4), 2)


class ManualReplicationTests(ReplicatorTestCase):
    def test_pays_whole_cost_upfront_and_delivers_within_the_limit(self):
        s = self.state(resources={"matter": 10})
        result = engine.replicate(s, "raw_a", 4)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["matter"], 8)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 4)
        self.assertEqual(s.replicator_jobs, [])

    def test_large_amount_is_spread_over_turns_and_arrives_gradually(self):
        s = self.state(resources={"matter": 10})
        result = engine.replicate(s, "raw_a", 10)  # 5 материи при лимите 2/ход
        self.assertIn("3 х.", result.message)
        self.assertEqual(s.resources["matter"], 5)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 4)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 8)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 10)
        self.assertEqual(s.replicator_jobs, [])

    def test_fractional_price(self):
        s = self.state(resources={"matter": 1})
        self.assertTrue(engine.replicate(s, "raw_b", 3).ok)
        self.assertAlmostEqual(s.resources["matter"], 0.25)

    def test_not_enough_matter_changes_nothing(self):
        s = self.state(resources={"matter": 1})
        result = engine.replicate(s, "mid", 1)
        self.assertFalse(result.ok)
        self.assertIn("Тест-материя 2", result.message)
        self.assertEqual(s.resources["matter"], 1)
        self.assertEqual(s.replicator_jobs, [])

    def test_resource_without_price_and_bad_qty_are_refused(self):
        s = self.state(resources={"matter": 10})
        self.assertFalse(engine.replicate(s, "final", 1).ok)
        self.assertFalse(engine.replicate(s, "raw_a", 0).ok)
        self.assertFalse(engine.replicate(s, "raw_a", "abc").ok)

    def test_needs_a_working_replicator_and_a_free_slot(self):
        s = GameState(resources={"matter": 10})
        self.assertIn("Нет ни одного", engine.replicate(s, "raw_a", 1).message)
        s = self.state(resources={"matter": 10})
        self.assertTrue(engine.replicate(s, "raw_a", 10).ok)
        self.assertIn("заняты", engine.replicate(s, "raw_a", 1).message)
        s.buildings["repl"] = 2
        self.assertTrue(engine.replicate(s, "raw_a", 1).ok)

    def test_jobs_beyond_working_copies_wait(self):
        s = self.state(resources={"matter": 10}, buildings={"repl": 2})
        self.assertTrue(engine.replicate(s, "raw_a", 2).ok)
        self.assertTrue(engine.replicate(s, "raw_b", 2).ok)
        s.broken["repl"] = 1
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 2)
        self.assertEqual(s.resources.get("raw_b", 0), 0)  # вторая ждёт свободной копии
        self.assertEqual(len(s.replicator_jobs), 1)

    def test_cancel_returns_unprocessed_matter_and_keeps_delivered(self):
        s = self.state(resources={"matter": 5})
        self.assertTrue(engine.replicate(s, "raw_a", 10).ok)
        engine.advance_turn(s)  # переработано 2 из 5, выдано 4
        job_id = s.replicator_jobs[0]["id"]
        result = engine.cancel_replicator_job(s, job_id)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["matter"], 3)
        self.assertEqual(s.resources["raw_a"], 4)
        self.assertFalse(engine.cancel_replicator_job(s, job_id).ok)

    def test_throughput_upgrade_is_repeatable(self):
        s = self.state()
        self.assertEqual(engine.replicator_max_matter_per_turn(s, "repl"), 2)
        self.assertTrue(engine.purchase_building_upgrade(s, "repl", "repl_speed").ok)
        self.assertTrue(engine.purchase_building_upgrade(s, "repl", "repl_speed").ok)
        self.assertEqual(engine.replicator_max_matter_per_turn(s, "repl"), 6)

    def test_refuses_what_would_not_fit_in_storage(self):
        # limited_res: склад на 10 (фикстура) - излишек сгорел бы в конце хода.
        s = self.state(resources={"matter": 20, "limited_res": 8})
        result = engine.replicate(s, "limited_res", 5)
        self.assertFalse(result.ok)
        self.assertIn("поместится ещё 2", result.message)
        self.assertTrue(engine.replicate(s, "limited_res", 2).ok)
        self.assertEqual(engine.replication_storage_free(s, "limited_res"), 0)

    def test_jobs_survive_save_and_load(self):
        s = self.state(resources={"matter": 10})
        engine.replicate(s, "raw_a", 10)
        engine.advance_turn(s)
        loaded = GameState.from_dict(s.to_dict())
        self.assertEqual(loaded.replicator_jobs, s.replicator_jobs)


class ReplicatorAutocraftTests(ReplicatorTestCase):
    def test_raw_resource_is_replicated_when_replicator_is_automated(self):
        s = self.state(automated=True, resources={"matter": 10}, buildings={"automation_hub": 1})
        result = engine.order_recipe(s, "craft_mid", 1)
        self.assertTrue(result.ok, result.message)
        self.assertEqual([st["kind"] for st in s.orders[0]["steps"]], ["replicate", "action"])
        self.assertEqual(s.resources["matter"], 8)  # 4 сырья по 0.5 - в резерв заказа
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("mid"), 1)
        self.assertEqual(s.orders, [])

    def test_without_automation_raw_resource_is_just_missing(self):
        s = self.state(resources={"matter": 10}, buildings={"automation_hub": 1})
        result = engine.order_recipe(s, "craft_mid", 1)
        self.assertFalse(result.ok)
        self.assertIn("Сырьё A 4", result.message)
        self.assertEqual(s.resources["matter"], 10)

    def test_replicator_is_a_fallback_when_the_recipe_path_lacks_raw(self):
        # Сырьё A теперь не реплицируется - полуфабрикат делается репликатором целиком.
        engine.RESOURCE_REPLICATION_COST = {"mid": 3}
        s = self.state(automated=True, resources={"matter": 10}, buildings={"automation_hub": 1, "cpu": 1})
        result = engine.order_recipe(s, "use_mid", 1)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(
            [(st["kind"], st["resource_id"]) for st in s.orders[0]["steps"]],
            [("replicate", "mid"), ("action", "u_item")],
        )

    def test_recipe_path_wins_by_default_and_priority_makes_replicator_main(self):
        s = self.state(automated=True, resources={"matter": 10, "raw_a": 4}, buildings={"automation_hub": 1, "cpu": 1})
        failure, plan = engine._plan_order(s, {"mid": 1})
        self.assertIsNone(failure)
        self.assertEqual([st["kind"] for st in plan["steps"]], ["action"])
        self.assertEqual(engine.replicator_preferred_resources(s), frozenset())
        self.assertTrue(engine.set_action_priority(s, rk("mid"), 5).ok)
        failure, plan = engine._plan_order(s, {"mid": 1})
        self.assertEqual([st["kind"] for st in plan["steps"]], ["replicate"])
        self.assertEqual(engine.replicator_preferred_resources(s), frozenset({"mid"}))

    def test_order_replication_xn_goes_to_storage_at_the_end(self):
        s = self.state(automated=True, resources={"matter": 10}, buildings={"cpu": 1})
        self.assertEqual(engine.order_eta_turns(s, rk("raw_a"), 6), 2)  # 3 материи при 2/ход
        result = engine.order_recipe(s, rk("raw_a"), 6)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(s.resources["matter"], 7)
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a", 0), 0)  # пока копится в резерве заказа
        engine.advance_turn(s)
        self.assertEqual(s.resources.get("raw_a"), 6)
        self.assertEqual(s.orders, [])

    def test_order_replication_needs_automation(self):
        s = self.state(resources={"matter": 10}, buildings={"cpu": 1})
        result = engine.order_recipe(s, rk("raw_a"), 2)
        self.assertFalse(result.ok)
        self.assertIn("Автоматизация", result.message)
        self.assertIsNotNone(engine.check_order(s, rk("raw_a"), 2))

    def test_preview_shows_the_replication_node(self):
        s = self.state(automated=True, resources={"matter": 10}, buildings={"cpu": 1})
        preview = engine.autocraft_preview(s, {"raw_a": 4})
        node = preview["nodes"][0]
        self.assertEqual(node["replicator_id"], "repl")
        self.assertEqual(node["fires"], 4)
        self.assertEqual(node["children"][0]["resource_id"], "matter")
        self.assertEqual(node["children"][0]["needed"], 2)

    def test_cancelled_order_lets_its_replication_finish_to_storage(self):
        s = self.state(automated=True, resources={"matter": 10}, buildings={"cpu": 1})
        self.assertTrue(engine.order_recipe(s, rk("raw_a"), 10).ok)  # 5 материи, 3 хода
        engine.advance_turn(s)
        self.assertTrue(engine.cancel_order(s, s.orders[0]["id"]).ok)
        self.assertEqual(s.resources.get("raw_a"), 4)  # то, что уже в резерве, - на склад
        self.turns(s, 2)
        self.assertEqual(s.resources.get("raw_a"), 10)

    def test_matter_is_never_replicated_from_itself(self):
        engine.RESOURCE_REPLICATION_COST = {**PRICES, "matter": 1}
        s = self.state(automated=True, buildings={"cpu": 1})
        result = engine.order_recipe(s, rk("raw_a"), 2)
        self.assertFalse(result.ok)
        self.assertIn("Тест-материя", result.message)


class ReplicatorWebUiTests(unittest.TestCase):
    """HTTP-слой (tools/web_ui.py): модалка, ход с репликацией, справочник, приоритеты."""

    def setUp(self):
        import web_ui
        self.web_ui = web_ui
        patch = fixtures.patched_content()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        engine.BUILDINGS = {**copy.deepcopy(engine.BUILDINGS), **copy.deepcopy(EXTRA_BUILDINGS)}
        engine.MANUAL_ACTIONS = {**engine.MANUAL_ACTIONS, **copy.deepcopy(EXTRA_ACTIONS)}
        engine.RESOURCE_NAMES = {**engine.RESOURCE_NAMES, **EXTRA_NAMES}
        engine.RESOURCE_FRACTIONAL = {**engine.RESOURCE_FRACTIONAL, "matter": True}
        engine.RESOURCE_REPLICATION_COST = dict(PRICES)
        web_ui.BUILDINGS = engine.BUILDINGS
        web_ui.MANUAL_ACTIONS = engine.MANUAL_ACTIONS
        web_ui.RESOURCE_NAMES = engine.RESOURCE_NAMES

    def state(self, automated=False, **kw):
        s = GameState(buildings={"repl": 1, **kw.pop("buildings", {})}, **kw)
        if automated:
            s.building_upgrades["repl"] = ["repl_auto"]
        return s

    def test_modal_view(self):
        s = self.state(resources={"matter": 7.5, "raw_a": 3}, buildings={"liq_gen": 1, "gen": 1})
        view = self.web_ui.replicator_view(s, engine.theoretical_rates(s))
        self.assertEqual(view["matter"], {"id": "matter", "name": "Тест-материя", "amount": 7.5, "rate": 1, "fractional": True})
        self.assertEqual([m["id"] for m in view["machines"]], ["repl"])
        self.assertEqual(view["machines"][0]["per_turn"], 2)
        self.assertFalse(view["machines"][0]["automated"])
        entry = next(e for e in view["catalog"] if e["resource_id"] == "raw_a")
        self.assertEqual((entry["price"], entry["have"], entry["storage_free"]), (0.5, 3, None))
        self.assertTrue(view["free_slot"])

    def test_no_replicator_in_content_means_no_view(self):
        engine.BUILDINGS = {b: v for b, v in engine.BUILDINGS.items() if b != "repl"}
        self.web_ui.BUILDINGS = engine.BUILDINGS
        self.assertIsNone(self.web_ui.replicator_view(GameState()))

    def test_turn_with_replication_logs_the_output(self):
        s = self.state(resources={"matter": 10})
        turn = s.turn
        log = self.web_ui.run_turn(s, "replicate", "raw_a:4")
        self.assertTrue(log["ok"], log["message"])
        self.assertEqual(log["replicated"], [{"resource_id": "raw_a", "name": "Сырьё A", "amount": 4}])
        self.assertEqual(s.resources["raw_a"], 4)
        self.assertEqual(s.turn, turn + 1)

    def test_failed_replication_does_not_spend_a_turn(self):
        s = self.state(resources={"matter": 1})
        turn = s.turn
        log = self.web_ui.run_turn(s, "replicate", "mid:1")
        self.assertFalse(log["ok"])
        self.assertEqual(s.turn, turn)

    def test_sources_list_the_replicator_with_price_per_unit(self):
        sources = [src for src in self.web_ui.resource_sources("raw_a") if src["kind"] == "replicator"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["action_id"], "replicator:raw_a")
        self.assertEqual(sources[0]["cost"], [{"id": "matter", "name": "Тест-материя", "amount": 0.5}])
        self.assertEqual([s for s in self.web_ui.resource_sources("final") if s["kind"] == "replicator"], [])

    def test_priorities_show_replication_and_mark_the_main_path(self):
        s = self.state()
        rows = {r["id"]: r for r in self.web_ui.action_priority_view(s)}
        self.assertTrue(rows[rk("raw_a")]["is_active_recipe"])  # у сырья A другого рецепта нет
        self.assertTrue(rows[rk("mid")]["is_fallback"])
        self.assertTrue(rows["craft_mid"]["is_active_recipe"])
        engine.set_action_priority(s, rk("mid"), 3)
        rows = {r["id"]: r for r in self.web_ui.action_priority_view(s)}
        self.assertTrue(rows[rk("mid")]["is_active_recipe"])
        self.assertTrue(rows["craft_mid"]["is_fallback"])

    def test_order_preview_and_step_name(self):
        s = self.state(automated=True, resources={"matter": 10}, buildings={"cpu": 1})
        preview = self.web_ui.order_preview_view(s, rk("raw_a"), 6)
        self.assertTrue(preview["can_order"], preview["reason"])
        self.assertEqual(preview["eta_turns"], 2)
        self.assertTrue(engine.order_recipe(s, rk("raw_a"), 6).ok)
        step = self.web_ui.autocraft_view(s)["orders"][0]["steps"][0]
        self.assertEqual(step["name"], "Репликация: Сырьё A")


class ReplicatorValidationTests(unittest.TestCase):
    """tools/content_validation.py: replicator, energy_surplus_only, replication_cost."""

    def setUp(self):
        import content_validation
        self.cv = content_validation
        d = fixtures.as_content_dict()
        d["resources"].append({"id": "matter", "name": "Тест-материя", "fractional": True})
        for r in d["resources"]:
            if r["id"] in PRICES:
                r["replication_cost"] = PRICES[r["id"]]
        d["buildings"].update(copy.deepcopy({k: EXTRA_BUILDINGS[k] for k in ("liq_gen", "repl")}))
        self.data = d

    def errors(self):
        return self.cv.validate(self.data)

    def test_valid_setup_has_no_errors(self):
        self.assertEqual(self.errors(), [])

    def test_matter_must_exist_and_not_be_energy(self):
        self.data["buildings"]["repl"]["replicator"]["matter"] = "nope"
        self.assertTrue(any("replicator.matter" in e for e in self.errors()))
        self.data["buildings"]["repl"]["replicator"]["matter"] = "energy"
        self.assertTrue(any("не может быть энергией" in e for e in self.errors()))

    def test_limit_and_slots(self):
        self.data["buildings"]["repl"]["replicator"]["max_matter_per_turn"] = 0
        self.data["buildings"]["repl"]["replicator"]["slots"] = 0
        errors = self.errors()
        self.assertTrue(any("max_matter_per_turn" in e for e in errors))
        self.assertTrue(any("replicator.slots" in e for e in errors))

    def test_bad_prices(self):
        resources = {r["id"]: r for r in self.data["resources"]}
        resources["raw_a"]["replication_cost"] = -1
        resources["energy"]["replication_cost"] = 1
        resources["matter"]["replication_cost"] = 1
        errors = self.errors()
        self.assertTrue(any("'raw_a': replication_cost" in e for e in errors))
        self.assertTrue(any("энергию репликатор не делает" in e for e in errors))
        self.assertTrue(any("это материя репликатора" in e for e in errors))

    def test_surplus_only_needs_energy_consumption(self):
        self.data["buildings"]["liq_gen"]["consumes"] = {}
        self.assertTrue(any("energy_surplus_only" in e for e in self.errors()))

    def test_throughput_upgrade_only_on_replicator(self):
        upgrade = copy.deepcopy(EXTRA_BUILDINGS["repl"]["upgrades"][1])
        self.data["buildings"]["gen"]["upgrades"] = [upgrade]
        self.assertTrue(any("replicator_throughput имеет смысл только у репликатора" in e for e in self.errors()))


if __name__ == "__main__":
    unittest.main()
