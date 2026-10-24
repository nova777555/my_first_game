from __future__ import annotations

import json
import os
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
    # building_id -> список id вида поломки (content.json["breakdown_types"])
    # на КАЖДУЮ сломанную копию этого типа - длина держится равной broken[bid]
    # (см. game/engine.py:_broken_type_entries, который сам добивает/обрезает
    # список до нужной длины значениями None - защита от старых сохранений и
    # контента без breakdown_types). None = поломка без типа (старое поведение,
    # ремонт не требует никакого инструмента). Только для НЕ-ферменных построек -
    # у ферм тип поломки хранится в самом farm_instances (см. ниже), т.к. там и
    # так уже есть экземпляр, а не абстрактный счётчик.
    broken_types: dict[str, list[str | None]] = field(default_factory=dict)
    # tool_id -> остаток прочности инструмента НА ТЕКУЩЕМ тире (см.
    # game/engine.py:tool_durability_remaining/tool_is_broken). Отсутствие ключа =
    # полная прочность тира (tiers[tier-1]["max_durability"]), либо инструмент
    # вовсе не изнашивается, если у тира нет max_durability. Сбрасывается (ключ
    # удаляется) при смене тира (upgrade_tool) и при ремонте самого инструмента
    # (repair_tool) - в обоих случаях инструмент снова "полностью исправен".
    tool_durability: dict[str, int] = field(default_factory=dict)
    # tool_id -> {enchant_id: level} - зачарования, применённые к ЭТОЙ линейке
    # инструмента (см. game/engine.py:enchant_tool/tool_enchant_level). Уровень
    # уже применённого зачарования никогда не понижается новой удачной попыткой
    # (см. enchant_tool - тот же принцип "сильнейшее побеждает", что у улучшений
    # построек); провал стирает у инструмента ВСЕ зачарования разом (ключ
    # tool_id целиком удаляется из словаря, а не обнуляется значениями).
    tool_enchantments: dict[str, dict[str, int]] = field(default_factory=dict)
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
    # Поле шахты (fog-of-war сетка, см. game/engine.py:_generate_mine_field/
    # hit_mine_field) - ОДНО на всю игру, как active_encounter/active_research:
    # {"size": N, "cells": [{"resource": id|None, "hp", "hp_max", "broken",
    # "revealed"}, ...]} (row-major, длина size*size). None, пока игрок ни разу не
    # заходил в шахту - создаётся лениво (_ensure_mine_field) при первом обращении.
    active_mine_field: dict | None = None
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
    # Заказы автокрафта (game/engine.py: _place_order/_process_orders) - вместо прежних
    # фоновых задач (autocraft_jobs) и очереди отложенных действий (pending_actions).
    # Каждый заказ ФИЗИЧЕСКИ держит своё: всё нужное сырьё изымается со склада в
    # order["reserved"] в момент заказа, промежуточные предметы его шагов копятся там
    # же - никто другой их не перехватит; отмена (cancel_order) возвращает резерв на
    # склад. Старые сохранения: autocraft_jobs/pending_actions при загрузке просто
    # отбрасываются (в них ничего не было зарезервировано - ресурсы не теряются).
    orders: list[dict] = field(default_factory=list)
    next_order_id: int = 1
    # action_id -> многоходовое ручное действие "в процессе" (manual_actions.*.duration_turns
    # > 1) - см. game/engine.py:_process_manual_jobs/do_manual_action. Одна активная задача НА
    # ДЕЙСТВИЕ (ключ - action_id, как autocraft_jobs по resource_id) - повторный клик, пока
    # задача не закончилась, блокируется (ActionResult(False, ...), без траты хода). cost уже
    # списан целиком В МОМЕНТ СТАРТА и заодно сохранён в самом job'е - досрочная отмена
    # (cancel_manual_job) возвращает его игроку целиком (потерян только уже накопленный
    # "бесплатный" прогресс turns_done, но не ресурсы). gain (в исходном, ещё не
    # развёрнутом по chance/диапазону виде - см. _roll_chance_amounts/_roll_range_amounts)
    # и gain_chance СНИМАЮТСЯ effective_manual_action_stats ОДИН РАЗ в момент старта (тот же
    # принцип, что autocraft_jobs["fires_total"] - эффекты построек фиксируются на момент
    # запуска, не пересчитываются на каждый тик), а РАЗВОРАЧИВАЮТСЯ (бросок шанса/диапазона)
    # РОВНО ОДИН РАЗ, в конце, при начислении - как produces у energy_jobs.
    manual_jobs: dict[str, dict] = field(default_factory=dict)  # action_id -> {"turns_total", "turns_done", "gain", "gain_chance", "spawn_species", "cost"}
    # Имя именованного сохранения (saves/<save_name>.json), из которого поднята текущая
    # партия, или None - не для логики игры, а только чтобы UI мог показать игроку/
    # разработчику, на каком сохранении он сейчас находится. Обновляется при открытии
    # ("Открыть") и при сохранении под новым именем ("Сохранить как") - см. main.py/
    # tools/web_ui.py. Сбрасывается в None при "Начать заново".
    save_name: str | None = None
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
    # "broken_type" - id вида поломки (content.json["breakdown_types"]) ИМЕННО
    # этого сломанного экземпляра, или None (поломка без типа/экземпляр не
    # сломан) - зеркало state.broken_types, только на уровне экземпляра (см.
    # game/engine.py:_ensure_farm_instances, которая доопределяет ключ у старых
    # записей, и repair/_is_breakdown_type_fixable).
    farm_instances: dict[str, dict] = field(default_factory=dict)
    # Счётчик для генерации id новых экземпляров ферм ("farm_{n}") - см.
    # game/engine.py:_ensure_farm_instances.
    next_farm_instance_id: int = 1
    # ФИЗИЧЕСКИЕ экземпляры построек-синтезаторов душ (BUILDINGS[bid]["soul_collector"] -
    # soul_synthesizer/guardian_altar) - instance_id -> {"building_id", "broken",
    # "broken_type", "upgrades", "installed"}. Зеркало farm_instances (см. комментарий
    # выше и game/engine.py:_ensure_soul_collector_instances) - ДО этого поля (2026-09-18)
    # слоты душ были агрегированы ПО ТИПУ постройки (см. soul_slots ниже), что делало
    # физически невозможным показать в UI "экземпляр 2 из 3" со своими слотами - по
    # прямому пожеланию синтезаторы переведены на ту же per-instance модель, что и
    # фермы. "installed" - список id вставленных душ ИМЕННО этого экземпляра (порядок =
    # порядок вставки, не индексы фиксированных слотов, тот же принцип, что у старого
    # soul_slots[building_id]).
    soul_collector_instances: dict[str, dict] = field(default_factory=dict)
    # Счётчик для генерации id новых экземпляров синтезаторов ("soul_inst_{n}") - см.
    # game/engine.py:_ensure_soul_collector_instances.
    next_soul_instance_id: int = 1
    # ФИЗИЧЕСКИЕ экземпляры построек-реакторов (BUILDINGS[bid]["reactor"]) -
    # instance_id -> {"building_id", "broken", "broken_type", "upgrades", "grid",
    # "zones"}. Зеркало soul_collector_instances/farm_instances (см. комментарии
    # выше и game/engine.py:_ensure_reactor_instances) - без миграционной ветки
    # (поле новое, мигрировать нечего). "grid" - row-major список ДЛИНОЙ
    # width*height (см. reactor_grid_dims): элемент None - пустая ячейка, иначе
    # {"resource_id", "charge"} ("charge" - остаток заряда fuel-ячейки, None у
    # moderator/coolant/absorber - они не расходуются). "zones" - список ДЛИНОЙ
    # reactor_zone_count: {"heat", "control_rod_depth", "coolant_flow",
    # "prev_coolant_flow"} - топология "ячейка -> зона" НЕ хранится, это чистая
    # функция от (width, height, zone_count), см. game/engine.py:
    # _reactor_zone_layout.
    reactor_instances: dict[str, dict] = field(default_factory=dict)
    # Счётчик для генерации id новых экземпляров реакторов ("reactor_inst_{n}") -
    # см. game/engine.py:_ensure_reactor_instances.
    next_reactor_instance_id: int = 1
    # skill_id (content.json["skills"]) -> накопленный опыт - ОБЩАЯ, произвольно
    # расширяемая система прокачки (не привязана к бою специально), см.
    # game/engine.py:skill_level/add_skill_xp. Отсутствие ключа = 0 (уровень 1,
    # ровно как tool_tiers резолвится в тир 1, если инструмента ещё нет в словаре).
    skill_xp: dict[str, int] = field(default_factory=dict)
    # Текущая цель схватки (охота на моба/босса, content.json["mobs"]) - ГЛОБАЛЬНО
    # одна на всю игру, как state.active_mine_field: {"mob_id", "remaining_hp"} или None, если
    # никого сейчас не ищут/не призвали. Новый поиск/призыв заменяет её целиком -
    # недобитая цель просто теряется (уже потраченный на призыв босса предмет не
    # возвращается) - см. game/engine.py:search_mob_any/summon_boss/attack_encounter.
    active_encounter: dict | None = None
    # id мобов/боссов (content.json["mobs"]), которых игрок хотя бы раз добил - для
    # условия квеста mob_killed. Только факт "было/не было", число убийств не
    # считается (прямое пожелание). Необратимо, как species_discovered.
    mobs_killed: set[str] = field(default_factory=set)
    # Текущее исследование "в процессе" (techs.*.research_turns > 1, см.
    # game/engine.py:research/_process_research/tech_research_turns) - ГЛОБАЛЬНО
    # одно на всю игру, как state.active_encounter/active_mine_field (НЕ как manual_jobs, где
    # своя задача на каждый action_id): {"tech_id", "turns_total", "turns_done"}
    # или None, если сейчас ничего не исследуется. cost уже списан целиком в
    # момент старта (research()) - досрочная отмена (cancel_research) возвращает
    # его игроку, накопленные turns_done - нет (тот же принцип, что
    # manual_jobs/cancel_manual_job).
    active_research: dict | None = None
    # УСТАРЕВШЕЕ поле (до перехода на per-instance soul_collector_instances выше,
    # 2026-09-18) - building_id -> список id вставленных душ, агрегированный ПО
    # ТИПУ постройки. Больше не пишется НИКУДА в новых сохранениях (сразу же
    # опустошается миграцией - см. game/engine.py:_ensure_soul_collector_instances,
    # которая при первом обращении раскладывает эти души round-robin по
    # soul_collector_instances и оставляет список тут пустым) - поле оставлено
    # только чтобы старые save.json (созданные ДО перехода) продолжали грузиться
    # без потери уже вставленных душ, тот же приём, что farm_building_id у
    # creatures.
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
    # tech_id -> решена ли одноразовая головоломка-гипотеза этапа 1 (game/engine.py:
    # solve_hypothesis) - ПОСТОЯННОЕ состояние, как claimed_quests, не турн-счётчик.
    # Пустое множество у старых сохранений (from_dict ниже) - сознательное решение:
    # ВСЕ технологии, включая уже открытые прежде технологии без головоломки,
    # теперь тоже требуют её один раз для НОВЫХ исследований - см. research()
    # в engine.py. state.techs (уже изученное) этим НЕ затрагивается - оба множества
    # независимы, ничего не переизучивается заново.
    solved_hypotheses: set[str] = field(default_factory=set)
    # move_id (content.json["combat_moves"]) -> сколько ходов осталось до
    # восстановления приёма - убывает на 1 в КАЖДОМ advance_turn (не только в
    # бою, как и остальные счётчики хода), ключ с 0 удаляется. Отсутствие ключа
    # = приём готов. См. game/engine.py:attack_encounter/unlocked_attack_moves -
    # какие приёмы вообще доступны игроку решает ТЕКУЩИЙ тир владеемого оружия
    # (tools.*.tiers[].unlocks_moves), не этот словарь - он только про кулдаун
    # уже открытых приёмов.
    move_cooldowns: dict[str, int] = field(default_factory=dict)
    # Текущее HP игрока - None означает "на максимуме" (тот же приём, что
    # tool_tiers: отсутствие явного значения = дефолт), НИКОГДА не читай
    # напрямую - только через game/engine.py:current_player_hp (клампит в
    # [0, player_max_hp(state)] и разворачивает None). Меняется только внутри
    # схватки (ответный удар моба, см. attack_encounter) и реген вне схватки
    # (см. _process_combat) - у самого поля нет верхней границы, чтобы не
    # хранить дублирующийся max_hp в сейве (он всё равно пересчитывается из
    # content.json + навыка + брони на каждый вызов).
    player_hp: int | None = None
    # Ход, до которого действует дебафф "Ранен" (входящий урон снижен, новая
    # схватка недоступна при 0 HP - см. game/engine.py:is_wounded) - None, если
    # дебаффа нет. Тот же паттерн сравнения, что state.active_encounter
    # ["deadline_turn"] у босса (state.turn <= wounded_until_turn, а не тик
    # отдельным счётчиком) - выставляется при поражении (attack_encounter),
    # обнуляется в _process_combat, когда ход перевалил за это значение.
    wounded_until_turn: int | None = None
    # building_id (постройка с полем automation_hub) -> список правил
    # автоматизации: [{"kind", "target_id", "conditions"}, ...]. "kind" -
    # "manual_action" (target_id - id из content.json["manual_actions"]) или
    # "energy_recipe" (target_id - id из content.json["energy_recipes"], см.
    # game/engine.py:AUTOMATION_TARGET_KINDS). "conditions" -
    # [{"resource", "op" (">=" | "<="), "amount"}, ...], проверяемые через И
    # (пустой список условий = правило срабатывает всегда). Каждый ход
    # (game/engine.py:process_automation_rules, между _process_manual_jobs и
    # _process_pending_actions) для первых automation_slots_capacity(...)
    # правил списка молча выполняется do_manual_action/start_energy_recipe,
    # если ВСЕ условия истинны - недостающие слоты (поломка/непроданный
    # апгрейд) просто обрезают список СПРАВА (порядок = порядок добавления
    # игроком), лишние правила не выселяются принудительно - тот же принцип,
    # что soul_slots. В правило можно поставить действие/рецепт, которые ещё
    # не открыты - будет молча пропускаться, пока не откроется.
    automation_rules: dict[str, list[dict]] = field(default_factory=dict)
    # action_id -> сколько раз это ручное действие УЖЕ дало результат (gain начислен) -
    # растёт и на мгновенном клике (do_manual_action), и на завершении многоходовой
    # задачи (_process_manual_jobs), но НЕ на самом клике/старте многоходовой задачи -
    # только на реальном получении gain. Нужен исключительно для квестов
    # (manual_action_performed_at_least, см. game/engine.py:_condition_met) - раньше
    # факт "скрафтил хоть раз" ничем не отслеживался отдельно от текущего запаса
    # ресурса, который мог быть уже потрачен.
    action_performed_count: dict[str, int] = field(default_factory=dict)
    # building_id (постройка с полем ritual_circle) -> активный ритуал "в процессе"
    # (ritual_recipes.*.duration_turns > 1) - см. game/engine.py:_process_ritual_jobs/
    # start_ritual. Один активный ритуал НА ПОСТРОЙКУ (агрегация по типу, как
    # soul_slots/automation_rules, а НЕ по физическому экземпляру, как farm_instances) -
    # повторный запуск, пока задача не закончилась, блокируется. cost (ring_cost +
    # center_cost + extra_cost) уже списан целиком в момент старта - хранится только
    # для возврата при досрочной отмене (cancel_ritual), тот же принцип, что
    # manual_jobs. extra_consumes_per_turn/extra_consumes_chance тикают КАЖДЫЙ ход
    # отдельно (не предоплачены) - нехватка не отменяет и не тормозит ритуал, просто
    # в этот ход ничего не списывается. produces/produces_chance разворачиваются
    # (шанс/диапазон) РОВНО ОДИН РАЗ при завершении - как gain у manual_jobs.
    ritual_jobs: dict[str, dict] = field(default_factory=dict)  # building_id -> {"recipe_id", "turns_total", "turns_done", "extra_consumes_per_turn", "extra_consumes_chance", "produces", "produces_chance", "cost"}
    # Закреплённые сеточные рецепты (action_id из manual_actions с pattern) - см.
    # CLAUDE.md/PLAN_grid_crafting.md, engine.toggle_pinned_recipe. Отдельное поле
    # от "pinned" (это уже занято ресурсами на главной панели, см. выше) - тот же
    # принцип "закреплённое переживает закрытие модалки", что автоматизация/души.
    pinned_recipes: list[str] = field(default_factory=list)
    # Репликации в репликаторах (buildings.*.replicator) - см. game/engine.py:
    # replicate/_process_replicator_jobs. Материя списана целиком при запуске,
    # job перерабатывает её не быстрее лимита за ход и выдаёт ресурс по мере
    # переработки. Каждый элемент - {"id", "building_id", "resource_id", "qty",
    # "cost", "progress", "delivered"} (+ "order_id"/"order_resource" у партии заказа).
    replicator_jobs: list[dict] = field(default_factory=list)
    # Плавильни (buildings.*.smeltery) - building_id -> {"slots": [None | {"recipe_id",
    # "turns_done", "cost"}], "tank": {resource_id: amount}, "cast": None | {"recipe_id",
    # "turns_done", "turns_total"}}. Агрегация по ТИПУ постройки, как ritual_jobs. cost
    # слота - то, что списано при загрузке (возвращается при выемке). Слот с turns_done >=
    # длительности рецепта просто ждёт места в тигле. См. game/engine.py: smeltery_*.
    smelteries: dict[str, dict] = field(default_factory=dict)
    # id сплавов с alloy_recipes.*.hidden, которые игрок уже открыл пробой (alloy_smeltery).
    discovered_alloys: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "resources": self.resources,
            "buildings": self.buildings,
            "techs": sorted(self.techs),
            "broken": self.broken,
            "broken_types": self.broken_types,
            "tool_durability": self.tool_durability,
            "tool_enchantments": {
                tid: dict(levels) for tid, levels in self.tool_enchantments.items()
            },
            "building_disabled": self.building_disabled,
            "pinned": self.pinned,
            "tool_tiers": self.tool_tiers,
            "active_mine_field": self.active_mine_field,
            "history": self.history,
            "energy_jobs": self.energy_jobs,
            "orders": self.orders,
            "next_order_id": self.next_order_id,
            "manual_jobs": self.manual_jobs,
            "save_name": self.save_name,
            "construction_progress": self.construction_progress,
            "building_upgrades": self.building_upgrades,
            "action_priority": self.action_priority,
            "energy_priority": self.energy_priority,
            "storage_tier": self.storage_tier,
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
            "soul_collector_instances": {
                sid: {**s, "upgrades": list(s.get("upgrades", [])), "installed": list(s.get("installed", []))}
                for sid, s in self.soul_collector_instances.items()
            },
            "next_soul_instance_id": self.next_soul_instance_id,
            "reactor_instances": {
                rid: {
                    **r,
                    "upgrades": list(r.get("upgrades", [])),
                    "grid": [dict(cell) if cell else None for cell in r.get("grid", [])],
                    "zones": [dict(z) for z in r.get("zones", [])],
                }
                for rid, r in self.reactor_instances.items()
            },
            "next_reactor_instance_id": self.next_reactor_instance_id,
            "skill_xp": self.skill_xp,
            "active_encounter": self.active_encounter,
            "mobs_killed": sorted(self.mobs_killed),
            "active_research": self.active_research,
            "soul_slots": self.soul_slots,
            "completed_quests": self.completed_quests,
            "claimed_quests": sorted(self.claimed_quests),
            "solved_hypotheses": sorted(self.solved_hypotheses),
            "move_cooldowns": self.move_cooldowns,
            "player_hp": self.player_hp,
            "wounded_until_turn": self.wounded_until_turn,
            "automation_rules": {
                bid: [dict(r) for r in rules] for bid, rules in self.automation_rules.items()
            },
            "ritual_jobs": self.ritual_jobs,
            "pinned_recipes": self.pinned_recipes,
            "replicator_jobs": self.replicator_jobs,
            "smelteries": {
                bid: {
                    "slots": [dict(s) if s else None for s in sm.get("slots", [])],
                    "tank": dict(sm.get("tank", {})),
                    "cast": dict(sm["cast"]) if sm.get("cast") else None,
                }
                for bid, sm in self.smelteries.items()
            },
            "discovered_alloys": sorted(self.discovered_alloys),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        return cls(
            turn=data.get("turn", 1),
            resources=dict(data.get("resources", {})),
            buildings=dict(data.get("buildings", {})),
            techs=set(data.get("techs", [])),
            broken=dict(data.get("broken", {})),
            broken_types={k: list(v) for k, v in data.get("broken_types", {}).items()},
            tool_durability=dict(data.get("tool_durability", {})),
            tool_enchantments={
                tid: dict(levels) for tid, levels in data.get("tool_enchantments", {}).items()
            },
            building_disabled=dict(data.get("building_disabled", {})),
            pinned=list(data.get("pinned", [])),
            tool_tiers=dict(data.get("tool_tiers", {})),
            active_mine_field=data.get("active_mine_field"),
            history=list(data.get("history", []))[-HISTORY_MAX_ENTRIES:],
            energy_jobs=[dict(j) for j in data.get("energy_jobs", [])],
            orders=[dict(o) for o in data.get("orders", [])],
            next_order_id=int(data.get("next_order_id", 1)),
            manual_jobs={k: dict(v) for k, v in data.get("manual_jobs", {}).items()},
            save_name=data.get("save_name"),
            construction_progress=dict(data.get("construction_progress", {})),
            building_upgrades={k: list(v) for k, v in data.get("building_upgrades", {}).items()},
            action_priority=dict(data.get("action_priority", {})),
            energy_priority=dict(data.get("energy_priority", {})),
            storage_tier=dict(data.get("storage_tier", {})),
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
            soul_collector_instances={
                sid: {**s, "upgrades": list(s.get("upgrades", [])), "installed": list(s.get("installed", []))}
                for sid, s in data.get("soul_collector_instances", {}).items()
            },
            next_soul_instance_id=data.get("next_soul_instance_id", 1),
            reactor_instances={
                rid: {
                    **r,
                    "upgrades": list(r.get("upgrades", [])),
                    "grid": [dict(cell) if cell else None for cell in r.get("grid", [])],
                    "zones": [dict(z) for z in r.get("zones", [])],
                }
                for rid, r in data.get("reactor_instances", {}).items()
            },
            next_reactor_instance_id=data.get("next_reactor_instance_id", 1),
            skill_xp=dict(data.get("skill_xp", {})),
            active_encounter=dict(data["active_encounter"]) if data.get("active_encounter") else None,
            mobs_killed=set(data.get("mobs_killed", [])),
            active_research=dict(data["active_research"]) if data.get("active_research") else None,
            soul_slots={k: list(v) for k, v in data.get("soul_slots", {}).items()},
            completed_quests=dict(data.get("completed_quests", {})),
            claimed_quests=set(data.get("claimed_quests", [])),
            solved_hypotheses=set(data.get("solved_hypotheses", [])),
            move_cooldowns=dict(data.get("move_cooldowns", {})),
            player_hp=data.get("player_hp"),
            wounded_until_turn=data.get("wounded_until_turn"),
            automation_rules={
                bid: [dict(r) for r in rules]
                for bid, rules in data.get("automation_rules", {}).items()
            },
            ritual_jobs={k: dict(v) for k, v in data.get("ritual_jobs", {}).items()},
            pinned_recipes=list(data.get("pinned_recipes", [])),
            replicator_jobs=[dict(j) for j in data.get("replicator_jobs", [])],
            smelteries={
                bid: {
                    "slots": [dict(s) if s else None for s in sm.get("slots", [])],
                    "tank": dict(sm.get("tank", {})),
                    "cast": dict(sm["cast"]) if sm.get("cast") else None,
                }
                for bid, sm in data.get("smelteries", {}).items()
            },
            discovered_alloys=set(data.get("discovered_alloys", [])),
        )

    def save(self, path: str) -> None:
        # Через временный файл и os.replace: автосейв пишется после КАЖДОГО хода, и
        # прямой open(path, "w") сначала обнулял файл - закрытое посреди записи окно
        # оставляло пустой/обрезанный autosave.json, с которым игра уже не запускалась.
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str) -> "GameState":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
