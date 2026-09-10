from __future__ import annotations

import json
from dataclasses import dataclass, field

# Максимум записей в history - см. поле ниже и engine.py:record_history_entry.
# Ограничивает и рост state.history за одну партию, и то, насколько раздутым
# может остаться save.json после единственной загрузки старого сохранения
# (обрезаем и здесь, в from_dict, а не только при добавлении новой записи).
HISTORY_MAX_ENTRIES = 100


@dataclass
class GameState:
    turn: int = 1
    resources: dict[str, int] = field(default_factory=dict)
    buildings: dict[str, int] = field(default_factory=dict)
    techs: set[str] = field(default_factory=set)
    broken: dict[str, int] = field(default_factory=dict)  # building_id -> сколько копий сломано
    # building_id -> сколько РАБОТАЮЩИХ копий постройки игрок вручную выключил
    # ползунком "Механизмы" (только у построек с непустыми И produces, И consumes -
    # см. game/engine.py:is_disableable_building/set_building_active_count).
    # Выключенная копия не производит/не потребляет и не может сломаться в этот
    # ход (тот же принцип, что active_period вне своего времени суток), но
    # остаётся "построенной" для requires_building/прочих гейтов - ползунок
    # влияет только на пассивное produces/consumes. Зажимается сверху текущим
    # working_count при чтении (game/engine.py:building_disabled_count) - если
    # копии сломались/разобраны, лишнее значение тут просто не мешает.
    building_disabled: dict[str, int] = field(default_factory=dict)
    pinned: list[str] = field(default_factory=list)  # ресурсы на главной панели
    # tool_id -> текущий тир этого инструмента (content.json["tools"][tool_id]["tiers"],
    # см. game/engine.py:tool_tier) - инструмент, ещё не встречавшийся в этом словаре,
    # считается тиром 1 (стартовый тир любого инструмента бесплатен и есть у игрока
    # с самого начала, как раньше было с единственной киркой).
    tool_tiers: dict[str, int] = field(default_factory=dict)
    veins: dict[str, int] = field(default_factory=dict)  # resource_id -> сколько осталось в найденной жиле
    # Снимок по каждому ходу, для статистики за диапазон - не более HISTORY_MAX_ENTRIES
    # записей (старые вытесняются, см. engine.py:record_history_entry и from_dict ниже).
    history: list[dict] = field(default_factory=list)
    # Активные крафты за энергию в энергетических преобразователях - список, а не
    # словарь по постройке: каждый преобразователь (копия постройки с полем
    # energy_converter) может параллельно вести СВОЙ рецепт, и одна и та же копия
    # НЕ может вести два сразу - см. game/engine.py:start_energy_recipe/
    # energy_converter_capacity. Каждый элемент - {"recipe_id", "progress"};
    # готовая партия убирается из списка целиком (крафт одноразовый, не крутится
    # по кругу как раньше).
    energy_jobs: list[dict] = field(default_factory=list)
    autocraft_jobs: dict[str, dict] = field(default_factory=dict)  # resource_id -> {"action_id", "fires_total", "fires_done"}
    pending_actions: list[dict] = field(default_factory=list)  # [{"kind", "target_id", "label"}] - действия, ждущие фоновый автокрафт
    # action_id -> многоходовое ручное действие "в процессе" (manual_actions.*.duration_turns
    # > 1) - см. game/engine.py:_process_manual_jobs/do_manual_action. Одна активная задача НА
    # ДЕЙСТВИЕ (ключ - action_id, как autocraft_jobs по resource_id) - повторный клик, пока
    # задача не закончилась, блокируется (ActionResult(False, ...), без траты хода). cost уже
    # списан целиком В МОМЕНТ СТАРТА (как energy_jobs) - progress копится "бесплатно", отмена
    # (cancel_manual_job) НЕ возвращает уже потраченное. gain (в исходном, ещё не
    # развёрнутом по chance/диапазону виде - см. _roll_chance_amounts/_roll_range_amounts)
    # и gain_chance СНИМАЮТСЯ effective_manual_action_stats ОДИН РАЗ в момент старта (тот же
    # принцип, что autocraft_jobs["fires_total"] - эффекты построек фиксируются на момент
    # запуска, не пересчитываются на каждый тик), а РАЗВОРАЧИВАЮТСЯ (бросок шанса/диапазона)
    # РОВНО ОДИН РАЗ, в конце, при начислении - как produces у energy_jobs.
    manual_jobs: dict[str, dict] = field(default_factory=dict)  # action_id -> {"turns_total", "turns_done", "gain", "gain_chance", "spawn_species"}
    # Имя именованного сохранения (saves/<save_name>.json), из которого поднята текущая
    # партия, или None - не для логики игры, а только чтобы UI мог показать игроку/
    # разработчику, на каком сохранении он сейчас находится. Обновляется при открытии
    # ("Открыть") и при сохранении под новым именем ("Сохранить как") - см. main.py/
    # tools/web_ui.py. Сбрасывается в None при "Начать заново".
    save_name: str | None = None
    # Галочка "Автокрафт" в UI (появляется, только когда compute_capacity(state) > 0 -
    # см. game/engine.py) - пока включена, нехватка ресурса пытается докрафтиться
    # автоматически (с автокрафт-специфичными ошибками); выключена - действия делаются
    # строго руками, как при отсутствии автокрафта вообще (см. _ensure_available).
    # По умолчанию True, чтобы поведение старых сохранений не менялось молча.
    autocraft_enabled: bool = True
    # building_id -> сколько этапов многоэтапного строительства (BUILDINGS[id]
    # ["construction_stages"]) УЖЕ оплачено для текущей строящейся копии. Ключ
    # присутствует, только пока 0 < прогресс < числа этапов - как только оплачен
    # последний этап, ключ удаляется и buildings[id] увеличивается на 1, ровно как
    # при обычной одноэтапной постройке (см. game/engine.py:build). Одноэтапные
    # постройки этот словарь вообще не трогают.
    construction_progress: dict[str, int] = field(default_factory=dict)
    # building_id -> список id купленных улучшений этой постройки (BUILDINGS[id]
    # ["upgrades"], см. game/engine.py:purchase_building_upgrade/effective_building_stats).
    # Список, а не set (JSON не хранит set) - дублей быть не может, purchase_building_upgrade
    # сам проверяет "уже куплено" перед добавлением.
    building_upgrades: dict[str, list[str]] = field(default_factory=dict)
    # action_id -> приоритет автокрафт-рецепта, ПЕРЕОПРЕДЕЛЁННЫЙ игроком для этого
    # сохранения (см. game/engine.py:action_priority/set_action_priority/
    # resource_recipe). Действие, которого здесь нет, использует значение по
    # умолчанию из content.json (manual_actions.*.priority, 0, если не указано) -
    # содержимое content.json не трогается, у каждого игрока свой выбор.
    action_priority: dict[str, int] = field(default_factory=dict)
    # building_id (тип энергетической машины, т.е. постройка с полем
    # energy_converter в content.json) -> приоритет, ПЕРЕОПРЕДЕЛЁННЫЙ игроком для
    # этого сохранения (см. game/engine.py:energy_priority/set_energy_priority) -
    # зеркало action_priority, только для распределения общего пула энергии между
    # РАЗНЫМИ типами машин, когда его не хватает на всех (см. _process_energy_jobs).
    # Тип, которого здесь нет, использует значение по умолчанию из content.json
    # (buildings.*.energy_priority, 0, если не указано).
    energy_priority: dict[str, int] = field(default_factory=dict)
    # resource_id -> сколько тиров расширения склада уже куплено (content.json
    # resources[].storage.tiers, см. game/engine.py:storage_capacity/upgrade_storage).
    # Отсутствие ключа = 0 (только базовая вместимость, capacity.base_capacity) -
    # ровно как tool_tiers резолвится в тир 1, если инструмента ещё нет в словаре.
    storage_tier: dict[str, int] = field(default_factory=dict)
    # resource_id -> индекс выбранного тира качества (content.json mining_info[...]
    # ["quality_tiers"]) для ТЕКУЩЕЙ найденной жилы этого ресурса (см.
    # game/engine.py:search_vein_any/mining_yield_per_turn). Заменяется целиком при
    # каждом новом поиске, синхронно с state.veins - у ресурса без quality_tiers
    # ключа тут не будет вовсе.
    vein_quality: dict[str, int] = field(default_factory=dict)
    # ВСЕ особи игрока (пойманные, выведенные скрещиванием, синтезированные) -
    # словарь по уникальному id особи (не по species_id: у двух особей одного вида
    # могут быть разные гены). См. game/engine.py:spawn_creature/breed_creatures/
    # house_creature/release_creature/_process_habitat. Значение:
    #   "species_id"      - текущий ФЕНОТИП вида (для быстрого доступа/отображения,
    #                        пересчитывается из genotype["species"] при создании -
    #                        не меняется потом сам по себе, гены не "плывут").
    #   "genotype"         - {trait_id: [allele_id, allele_id]} на каждый из 5
    #                        жёстко зашитых признаков (game/engine.py:GENOTYPE_TRAIT_IDS).
    #   "farm_instance_id" - id ФИЗИЧЕСКОГО экземпляра фермы (state.farm_instances),
    #                        в который поселена, или None, если особь в "банке"
    #                        (не занимает слот ни одной фермы).
    #   "turns_alive"      - растёт только пока farm_instance_id не None; сравнивается
    #                        с фенотипом lifespan, если у экземпляра фермы нет апгрейда
    #                        breeding_immortality.
    #   "identified"       - опознан ли геном полностью (иначе UI показывает только
    #                        фенотип) - см. analyze_creature.
    creatures: dict[str, dict] = field(default_factory=dict)
    # Счётчик для генерации id новых особей ("creature_{n}") - see spawn_creature.
    next_creature_id: int = 1
    # trait_id -> список allele_id, которые игрок хотя бы раз видел ОПОЗНАННЫМИ
    # (см. analyze_creature) - определяет, что доступно для генетических шаблонов
    # (synthesize_creature). Рецессивная аллель, ни разу не всплывавшая в
    # опознанном геноме, тут не появится, даже если игрок формально ловил вид,
    # который её теоретически может нести.
    known_alleles: dict[str, list[str]] = field(default_factory=dict)
    # id всех видов (content.json["species"]), фенотип которых игрок хотя бы раз
    # получил (поимкой, мутацией или синтезом) - для коллекционных достижений
    # (species_discovered_at_least). Не привязано к тому, жива ли ещё хоть одна
    # такая особь прямо сейчас - факт открытия вида необратим.
    species_discovered: set[str] = field(default_factory=set)
    # ФИЗИЧЕСКИЕ экземпляры построек-ферм (BUILDINGS[bid]["breeding_slots"] > 0:
    # apiary/electric_apiary/industrial_apiary) - instance_id -> {"building_id",
    # "broken", "upgrades"}. Параллельный слой поверх агрегатного state.buildings/
    # state.broken (которые остаются авторитетными для working_count, ремонта,
    # поломки, достижений, requires_building - см. game/engine.py:_ensure_farm_instances).
    # "broken" - bool ОДНОГО экземпляра (не путать с state.broken[bid] - агрегатным
    # счётчиком копий). "upgrades" - список id купленных апгрейдов ИМЕННО этого
    # экземпляра (аналог state.building_upgrades[bid], но на одну копию).
    farm_instances: dict[str, dict] = field(default_factory=dict)
    # Счётчик для генерации id новых экземпляров ферм ("farm_{n}") - см.
    # game/engine.py:_ensure_farm_instances.
    next_farm_instance_id: int = 1
    # skill_id (content.json["skills"]) -> накопленный опыт - ОБЩАЯ, произвольно
    # расширяемая система прокачки (не привязана к бою специально), см.
    # game/engine.py:skill_level/add_skill_xp. Отсутствие ключа = 0 (уровень 1,
    # ровно как tool_tiers резолвится в тир 1, если инструмента ещё нет в словаре).
    skill_xp: dict[str, int] = field(default_factory=dict)
    # Текущая цель схватки (охота на моба/босса, content.json["mobs"]) - ГЛОБАЛЬНО
    # одна на всю игру, как state.veins: {"mob_id", "remaining_hp"} или None, если
    # никого сейчас не ищут/не призвали. Новый поиск/призыв заменяет её целиком -
    # недобитая цель просто теряется (уже потраченный на призыв босса предмет не
    # возвращается) - см. game/engine.py:search_mob_any/summon_boss/attack_encounter.
    active_encounter: dict | None = None
    # building_id (постройка с полем soul_collector) -> список id вставленных душ
    # (порядок = порядок вставки, НЕ индексы фиксированных слотов - см.
    # game/engine.py:insert_soul/remove_soul/soul_collector_capacity). Душа
    # "вечная" - не расходуется и не портится сама по себе, стоит в слоте, пока
    # игрок сам её не вынет. Если сломанная копия/непроданный апгрейд снизили
    # capacity ниже len(list), лишние (по порядку вставки) просто не участвуют в
    # производстве этот ход - не выселяются принудительно.
    soul_slots: dict[str, list[str]] = field(default_factory=dict)
    # quest_id -> ход, на котором ВПЕРВЫЕ выполнены все условия квеста (content.json
    # ["quests"]) - раз условия истинны, квест остаётся "выполнен" навсегда, даже
    # если условие потом перестало бы быть истинным (например, ресурс потрачен) -
    # не переоценивается заново. См. game/engine.py:check_quests.
    completed_quests: dict[str, int] = field(default_factory=dict)
    # id квестов, награда (loot_cases) которых уже забрана игроком - отдельно от
    # completed_quests, потому что выполнение условий и получение кейса разные
    # моменты: игрок кликает "Забрать награду" сам, ход не тратится (см.
    # game/engine.py:claim_quest_reward, тот же принцип, что insert_soul/remove_soul).
    claimed_quests: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "resources": self.resources,
            "buildings": self.buildings,
            "techs": sorted(self.techs),
            "broken": self.broken,
            "building_disabled": self.building_disabled,
            "pinned": self.pinned,
            "tool_tiers": self.tool_tiers,
            "veins": self.veins,
            "history": self.history,
            "energy_jobs": self.energy_jobs,
            "autocraft_jobs": self.autocraft_jobs,
            "pending_actions": self.pending_actions,
            "manual_jobs": self.manual_jobs,
            "save_name": self.save_name,
            "autocraft_enabled": self.autocraft_enabled,
            "construction_progress": self.construction_progress,
            "building_upgrades": self.building_upgrades,
            "action_priority": self.action_priority,
            "energy_priority": self.energy_priority,
            "storage_tier": self.storage_tier,
            "vein_quality": self.vein_quality,
            "creatures": {
                cid: {**c, "genotype": {t: list(pair) for t, pair in c["genotype"].items()}}
                for cid, c in self.creatures.items()
            },
            "next_creature_id": self.next_creature_id,
            "known_alleles": {t: sorted(alleles) for t, alleles in self.known_alleles.items()},
            "species_discovered": sorted(self.species_discovered),
            "farm_instances": {
                fid: {**f, "upgrades": list(f.get("upgrades", []))}
                for fid, f in self.farm_instances.items()
            },
            "next_farm_instance_id": self.next_farm_instance_id,
            "skill_xp": self.skill_xp,
            "active_encounter": self.active_encounter,
            "soul_slots": self.soul_slots,
            "completed_quests": self.completed_quests,
            "claimed_quests": sorted(self.claimed_quests),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        return cls(
            turn=data.get("turn", 1),
            resources=dict(data.get("resources", {})),
            buildings=dict(data.get("buildings", {})),
            techs=set(data.get("techs", [])),
            broken=dict(data.get("broken", {})),
            building_disabled=dict(data.get("building_disabled", {})),
            pinned=list(data.get("pinned", [])),
            tool_tiers=dict(data.get("tool_tiers", {})),
            veins=dict(data.get("veins", {})),
            history=list(data.get("history", []))[-HISTORY_MAX_ENTRIES:],
            energy_jobs=[dict(j) for j in data.get("energy_jobs", [])],
            autocraft_jobs={k: dict(v) for k, v in data.get("autocraft_jobs", {}).items()},
            pending_actions=[dict(p) for p in data.get("pending_actions", [])],
            manual_jobs={k: dict(v) for k, v in data.get("manual_jobs", {}).items()},
            save_name=data.get("save_name"),
            autocraft_enabled=data.get("autocraft_enabled", True),
            construction_progress=dict(data.get("construction_progress", {})),
            building_upgrades={k: list(v) for k, v in data.get("building_upgrades", {}).items()},
            action_priority=dict(data.get("action_priority", {})),
            energy_priority=dict(data.get("energy_priority", {})),
            storage_tier=dict(data.get("storage_tier", {})),
            vein_quality=dict(data.get("vein_quality", {})),
            creatures={
                cid: {
                    **{k: v for k, v in c.items() if k not in ("genotype", "farm_building_id")},
                    "genotype": {t: list(pair) for t, pair in c.get("genotype", {}).items()},
                    # Сохранение старше переименования поля (farm_building_id -> farm_instance_id,
                    # см. CLAUDE.md "Пасеки: индивидуальные экземпляры") хранило здесь id ТИПА
                    # постройки - подсовываем это же старое значение под НОВЫМ ключом как есть,
                    # без попытки его тут же поправить: engine._ensure_farm_instances при первом
                    # обращении сама распознает "это id типа, а не найденного instance_id" и
                    # расселит особь по конкретному экземпляру (или в None, если не найдёт свободный).
                    "farm_instance_id": c.get("farm_instance_id", c.get("farm_building_id")),
                }
                for cid, c in data.get("creatures", {}).items()
            },
            next_creature_id=data.get("next_creature_id", 1),
            known_alleles={t: list(alleles) for t, alleles in data.get("known_alleles", {}).items()},
            species_discovered=set(data.get("species_discovered", [])),
            farm_instances={
                fid: {**f, "upgrades": list(f.get("upgrades", []))}
                for fid, f in data.get("farm_instances", {}).items()
            },
            next_farm_instance_id=data.get("next_farm_instance_id", 1),
            skill_xp=dict(data.get("skill_xp", {})),
            active_encounter=dict(data["active_encounter"]) if data.get("active_encounter") else None,
            soul_slots={k: list(v) for k, v in data.get("soul_slots", {}).items()},
            completed_quests=dict(data.get("completed_quests", {})),
            claimed_quests=set(data.get("claimed_quests", [])),
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "GameState":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
