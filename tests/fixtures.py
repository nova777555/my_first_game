"""
Полностью самодостаточный игровой контент для тестов - никак не связан с
game/content.json. Тесты, использующие patched_content(), не заметят и не
сломаются, если реальный контент игры (id ресурсов/построек/технологий/
достижений) будет переименован, удалён или полностью пересоздан в редакторе.

Раскладка нарочно небольшая, но покрывает всё, что нужно для тестов:
- цепочку автокрафта в два уровня (raw_a -> mid -> final)
- постройку с производством/потреблением и без поломок (gen)
- постройку с гарантированной поломкой каждый ход, breakdown_chance=1.0 (flaky) -
  так тесты на поломки не нуждаются в фиксированном random.seed
- постройку-хранилище энергии (battery) и постройку, работающую только днём (solar)
- единый энергетический преобразователь (постройка energy_converter, поле
  energy_converter с потолком забора энергии за ход) и его "мега"-вариант без
  потолка (mega_converter) - для
  тестов накопления энергии, ограничения забора за ход и мгновенного завершения,
  когда одного хода хватает на весь порог сразу
- два независимых рецепта крафта за энергию (ENERGY_RECIPES: make_raw_b -> raw_b,
  make_catalyst -> catalyst) и действие, собирающее готовый предмет из ОБОИХ
  результатов (assemble_capacitor: raw_b + catalyst -> capacitor) - для тестов
  автокрафта поверх крафта за энергию (см. ENERGY_RECIPE_FOR_RESOURCE): один
  преобразователь ведёт рецепты ПОСЛЕДОВАТЕЛЬНО, два - ПАРАЛЛЕЛЬНО
- постройку-заглушку (amplifier: requires_building "energy_converter", ничего не
  делает сама) - для тестов generic-гейта requires_building у ПОСТРОЕК (не только у
  ручных действий); пропускную способность преобразователя наращивают апгрейды
  ТИПА energy_converter_capacity прямо на energy_converter/matter_generator/
  mega_converter (см. purchase_building_upgrade)
- отдельную "медленную" цепочку автокрафта в два уровня (slow_raw -> slow_mid ->
  slow_final) под постройку с низкой скоростью автокрафта (trickle_hub) - для тестов
  фоновых задач/очереди действий, не задевая основную цепочку (raw_a -> mid -> final)
- второй независимый потребитель slow_raw (other_mid, тоже открыт trickle_hub) - для
  тестов на два НЕЗАВИСИМЫХ фоновых задачи, делящих один и тот же ограниченный
  ингредиент; и второе действие, потребляющее slow_mid (assemble_widget) - для тестов
  на два РАЗНЫХ отложенных действия, ждущих один и тот же фоновый автокрафт
- постройку с autocraft_slots и max_count=1 (slot_hub) - для тестов на лимит слотов
  автокрафта и на потолок числа копий постройки
- многоэтапную постройку (furnace, construction_stages из 3 этапов с разной ценой
  и описанием) - для тестов пошагового строительства
- технологию с обычным эффектом на конкретную постройку и с wildcard-эффектом
- обычный и редкий минерал для добычи (rare_mineral разведан только с тира 2 кирки,
  так что по умолчанию единственный кандидат для поиска - mineral, без нужды в seed)
- шесть локаций, включая обязательные "shaft" (engine.MINE_LOCATION_ID) и
  "converter" (engine.CONVERTER_LOCATION_ID, вложена в обычную "mechanisms" -
  тот же приём, что и в реальном content.json, см. CLAUDE.md)
- настройки времени (TIME)
- два инструмента: "pickaxe" (несколько тиров, привязана добыча через
  required_tool/required_tier - порог "начиная с", без изменений по смыслу) и
  "drill" (для requires_tool_tiers - галочки на конкретные тиры) + пара действий
  drill_tier1_only/drill_tier2_only, показывающих ровно тот сценарий, ради
  которого сделаны галочки: одно и то же по смыслу действие, но с разной
  эффективностью на разных тирах одного инструмента
- достижения на каждый тип условия, что есть в движке
- ресурс с настроенной вместимостью склада (limited_res: base_capacity=10, тир 1
  без технологии, тир 2 требует tech1) - для тестов storage_capacity/
  upgrade_storage; остальные ресурсы фикстуры намеренно БЕЗ storage (хранятся
  без ограничений), чтобы существующие тесты, гоняющие raw_a/raw_b и т.п. на
  большие количества, не задело
- полноценная генетика разведения: ТРИ тестовые фермы (apiary - обычная,
  breeding_slots=2, без апгрейдов; electric_apiary - на энергии, breeding_slots=1,
  с апгрейдами breeding_output/breeding_immortality; industrial_apiary -
  breeding_slots=2, апгрейды НА ЭКЗЕМПЛЯР, включая environment_emulation
  (взаимоисключающая замена, не прогрессия) - для тестов "апгрейды ферм не
  делятся между экземплярами одного типа" и бонусной продукции по совпадению
  среды - фермы НЕ проводят скрещивание, только расселяют особей), отдельный
  механизм скрещивания (crossing_station, поле breeding_station - id НАРОЧНО
  не совпадает с id реальной постройки content.json, чтобы тесты проверяли
  generic-поле, а не хардкод конкретного id) с апгрейдом breeding_mutation_chance,
  и генетическая лаборатория (genetics_lab_building, поле genetics_lab) - НИ ОДНА
  не построена по умолчанию, поэтому не засоряют "закрытую вселенную" уже
  существующих exact-set тестов, тот же приём, что у mechanism_a выше.
  GENETIC_TRAITS - 4 признака (lifespan/output_multiplier/active_period/
  environment - у environment id аллелей ЗАКРЫТЫ tools/content_validation.py,
  все 6 обязаны присутствовать), по 2-6 аллелей с обеих сторон доминантности.
  SPECIES - два вида (critter_a доминантный, ловится в диком виде weight=5;
  critter_b рецессивный, требует critter_food, доступен ТОЛЬКО через мутацию
  weight=0). MUTATIONS - один переход critter_a x critter_a -> critter_b
  (chance=0.5, без гейтов). См. make_genotype() ниже - хелпер для тестов,
  собирающий гомозиготный генотип особи без ручного повторения словаря на 5
  признаков в каждом тесте.
- quality_tiers у добычи НЕ добавлены в общую MINING_INFO (это сломало бы
  exact-set тесты MiningTests про кандидатов поиска) - тесты качества жил
  временно патчат engine.MINING_INFO["mineral"] точечно через mock.patch.dict,
  тем же приёмом, что уже применяется для RESOURCE_STORAGE в ResourceStorageTests
  (см. CLAUDE.md).
"""
from __future__ import annotations

import atexit
import contextlib
import copy
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Сохранения тестов - во временной папке, а не в настоящем saves/ игрока (см.
# GAME_SAVE_ROOT в tools/web_ui.py): тест, дошедший до web_ui.save_state (например,
# craft_grid_view -> run_turn), иначе перезаписывал saves/autosave.json. Все тесты
# импортируют этот модуль раньше web_ui; если web_ui всё же уже импортирован -
# переставляем его пути напрямую.
_SAVE_SANDBOX = tempfile.mkdtemp(prefix="game_test_saves_")
os.environ["GAME_SAVE_ROOT"] = _SAVE_SANDBOX
atexit.register(shutil.rmtree, _SAVE_SANDBOX, True)
if "web_ui" in sys.modules:
    sys.modules["web_ui"].SAVES_DIR = Path(_SAVE_SANDBOX) / "saves"
    sys.modules["web_ui"].SAVE_PATH = Path(_SAVE_SANDBOX) / "saves" / "autosave.json"

RESOURCES = [
    {"id": "energy", "name": "Тестовая энергия", "fractional": True},
    {"id": "raw_a", "name": "Сырьё A"},
    {"id": "raw_b", "name": "Сырьё B"},
    {"id": "mid", "name": "Полуфабрикат"},
    {"id": "final", "name": "Готовое изделие"},
    {"id": "mineral", "name": "Минерал"},
    {"id": "rare_mineral", "name": "Редкий минерал"},
    {"id": "slow_raw", "name": "Медленное сырьё"},
    {"id": "slow_mid", "name": "Медленный полуфабрикат"},
    {"id": "slow_final", "name": "Медленное изделие"},
    {"id": "gadget", "name": "Гаджет"},
    {"id": "other_mid", "name": "Другой медленный полуфабрикат"},
    {"id": "widget", "name": "Виджет"},
    {"id": "catalyst", "name": "Катализатор"},
    {"id": "capacitor", "name": "Конденсатор"},
    {"id": "prioritized", "name": "Приоритетный ресурс"},
    {"id": "mech_output", "name": "Выход механизма А"},
    # Для тестов "Шанс потребления/получения" (game/engine.py:is_manual_action_deterministic) -
    # см. MANUAL_ACTIONS["craft_lucky"]/["craft_risky"] ниже.
    {"id": "lucky_output", "name": "Удачливый выход"},
    {"id": "risky_output", "name": "Рискованный выход"},
    # Для тестов случайного produce (game/engine.py: _is_range_amount/
    # _roll_range_amounts) - см. MANUAL_ACTIONS["craft_variable"]/
    # BUILDINGS["variable_gen"] ниже.
    {"id": "variable_output", "name": "Переменный выход"},
    # Для тестов многоходовых ручных действий (game/engine.py:_process_manual_jobs,
    # manual_actions.*.duration_turns) - см. MANUAL_ACTIONS["craft_delayed"] ниже.
    {"id": "delayed_output", "name": "Отложенный выход"},
    # Для теста "одна локация - одна активная многоходовая задача" (см.
    # MANUAL_ACTIONS["craft_delayed_sibling"]/["craft_delayed_sub"] ниже).
    {"id": "sibling_delayed_output", "name": "Соседний отложенный выход"},
    {"id": "sub_delayed_output", "name": "Отложенный выход подлокации"},
    # Единственный ресурс фикстуры с настроенной вместимостью склада (game/engine.py:
    # storage_capacity) - остальные ресурсы намеренно без storage (хранятся без
    # ограничений, как до этой механики), чтобы существующие тесты, гоняющие
    # raw_a/raw_b и т.п. на большие количества, не задело. Два тира: первый без
    # технологии, второй требует tech1 - покрывает обе ветки (свободно доступный
    # следующий тир и технологически запертый) той же схемой, что TOOLS["pickaxe"].
    {
        "id": "limited_res", "name": "Ограниченный ресурс",
        "storage": {
            "base_capacity": 10,
            "tiers": [
                {"capacity_add": 20, "cost": {"raw_a": 5}, "requires_tech": None},
                {"capacity_add": 50, "cost": {"raw_a": 15}, "requires_tech": "tech1"},
            ],
        },
    },
    # Отдельный ресурс - тир с "infinite": True вместо capacity_add (game/engine.py:
    # storage_capacity) - для теста "куплен infinite-тир -> вместимость None (как
    # без storage вовсе)". Отдельно от limited_res, чтобы не менять его max_tier/
    # прогрессию и не задеть существующие тесты, завязанные на неё.
    {
        "id": "vault_res", "name": "Хранимый ресурс",
        "storage": {
            "base_capacity": 10,
            "tiers": [
                {"capacity_add": 20, "cost": {"raw_a": 5}, "requires_tech": None},
                {"infinite": True, "cost": {"raw_a": 15}, "requires_tech": None},
            ],
        },
    },
    # Виды для разведения (см. SPECIES ниже) - обычные ресурсы, ключ в SPECIES
    # добавляет им метаданные признака "вид" генотипа (produces/consumes/
    # dominant/weight) - но сами особи живут в state.creatures (уникальные
    # сущности со своим генотипом), НЕ стопкой в state.resources.
    {"id": "critter_a", "name": "Существо А"},
    {"id": "critter_b", "name": "Существо Б"},
    {"id": "critter_food", "name": "Корм для существ"},
    {"id": "species_output", "name": "Продукт питомника"},
    # Для тестов ВТОРОГО типа энергетической машины (matter_generator ниже) -
    # выход рецепта upgrade_matter (материал + энергия -> материал повыше).
    {"id": "upgraded_matter", "name": "Улучшенная материя"},
    # Для тестов боя/душ (см. MOBS/SKILLS/SOUL_RECIPES ниже) - "вечная" душа
    # тестового моба, вставляемая в синтезатор (BUILDINGS["soul_synth"]).
    {"id": "test_soul", "name": "Тестовая душа", "soul_kind": "test"},
    # Вторая душа - с диапазоном produces (см. SOUL_RECIPES["variable_soul"]
    # ниже) - для теста "каждая вставленная душа бросает диапазон НЕЗАВИСИМО".
    # ТОТ ЖЕ soul_kind, что у test_soul - soul_synth (см. BUILDINGS ниже)
    # принимает обе одним тегом, как и раньше принимал обе явным списком.
    {"id": "variable_soul", "name": "Переменная душа", "soul_kind": "test"},
    # Для тестов реактора (BUILDINGS["test_reactor"]/_process_reactors) - один
    # компонент на каждую роль решётки, плюс отход выгоревшего топлива.
    {"id": "test_fuel", "name": "Тестовое топливо", "reactor_component": {
        "role": "fuel", "heat": 4, "output": 6, "burnup_per_turn": 1, "charge_max": 3,
        "spent_result": "test_spent_fuel",
    }},
    {"id": "test_spent_fuel", "name": "Тестовое отработанное топливо"},
    {"id": "test_moderator", "name": "Тестовый замедлитель", "reactor_component": {"role": "moderator", "power": 1}},
    {"id": "test_coolant", "name": "Тестовый теплоноситель", "reactor_component": {"role": "coolant", "power": 5}},
    {"id": "test_absorber", "name": "Тестовый поглотитель", "reactor_component": {"role": "absorber", "power": 1}},
    {"id": "test_reflector", "name": "Тестовый отражатель", "reactor_component": {"role": "reflector", "power": 2}},
    {"id": "test_shield", "name": "Тестовый радиатор", "reactor_component": {"role": "shield", "power": 0.5}},
    # Катализаторы решётки (resources.*.reactor_catalyst) - один на каждый
    # self_stat, круглые проценты ради простой ручной проверки в тестах.
    {"id": "test_catalyst_boost", "name": "Катализатор форсажа", "reactor_catalyst": {
        "applies_to": ["fuel"], "self_stat": "heat_output", "self_pct": 0.5, "neighbor_malus_pct": 0.2,
    }},
    {"id": "test_catalyst_stabilizer", "name": "Катализатор стабилизации", "reactor_catalyst": {
        "applies_to": ["fuel"], "self_stat": "burnup", "self_pct": 0.5, "neighbor_malus_pct": 0.2,
    }},
    {"id": "test_catalyst_field", "name": "Катализатор поля", "reactor_catalyst": {
        "applies_to": ["fuel"], "self_stat": "field", "self_pct": 0.5, "neighbor_malus_pct": 0.2,
    }},
    {"id": "test_catalyst_amplifier", "name": "Катализатор усиления", "reactor_catalyst": {
        "applies_to": ["moderator", "coolant", "absorber", "reflector", "shield"],
        "self_stat": "power", "self_pct": 0.5, "neighbor_malus_pct": 0.2,
    }},
    # Выходы сеточных действий локации "grid_loc" (см. MANUAL_ACTIONS["grid_*"] ниже).
    {"id": "grid_output", "name": "Выход сеточного рецепта"},
    {"id": "grid_locked_output", "name": "Выход заблокированного сеточного рецепта"},
    {"id": "grid_mirror_output", "name": "Выход зеркального сеточного рецепта"},
    {"id": "grid_plain_output", "name": "Выход обычного действия на верстаке"},
    # Плавильня (tests/test_smeltery.py): руда/топливо -> жидкие металлы (molten) + шлак
    # в тигле, сплав металлов, литьё в форму (форма не расходуется).
    {"id": "smelt_ore", "name": "Плавильная руда"},
    {"id": "smelt_ore_b", "name": "Вторая руда"},
    {"id": "smelt_fuel", "name": "Плавильное топливо"},
    {"id": "smelt_slag", "name": "Тестовый шлак"},
    {"id": "molten_a", "name": "Жидкий металл A", "molten": {"color": "#e07a3f", "density": 9}},
    {"id": "molten_b", "name": "Жидкий металл B", "molten": {"color": "#b9c4c9", "density": 7}},
    {"id": "molten_ab", "name": "Жидкий сплав AB", "molten": {"color": "#c8903a", "density": 8}},
    {"id": "molten_hidden", "name": "Тайный сплав", "molten": {"color": "#ec8f5e"}},
    {"id": "cast_form", "name": "Тестовая форма"},
    {"id": "cast_product", "name": "Тестовая отливка"},
]
RESOURCE_NAMES = {r["id"]: r["name"] for r in RESOURCES}
RESOURCE_ORDER = [r["id"] for r in RESOURCES]
RESOURCE_ICONS: dict = {}
RESOURCE_FRACTIONAL = {r["id"]: bool(r.get("fractional", False)) for r in RESOURCES}
RESOURCE_STORAGE = {r["id"]: r["storage"] for r in RESOURCES if r.get("storage")}
RESOURCE_REPLICATION_COST = {r["id"]: r["replication_cost"] for r in RESOURCES if r.get("replication_cost")}
RESOURCE_SOUL_KIND = {r["id"]: r["soul_kind"] for r in RESOURCES if r.get("soul_kind")}
RESOURCE_REACTOR_COMPONENT = {r["id"]: r["reactor_component"] for r in RESOURCES if r.get("reactor_component")}
RESOURCE_REACTOR_CATALYST = {r["id"]: r["reactor_catalyst"] for r in RESOURCES if r.get("reactor_catalyst")}
# Охлаждающие жидкости реактора. В фикстуре их НЕТ намеренно: пустой словарь
# включает в движке путь "механика расходника не заведена" (теплоносители
# работают без жидкости), и все старые тесты про охлаждение остаются в силе.
# Тесты самой механики жидкостей подменяют эту константу точечно.
RESOURCE_REACTOR_COOLANT_FLUID = {r["id"]: r["reactor_coolant_fluid"]
                                  for r in RESOURCES if r.get("reactor_coolant_fluid")}
RESOURCE_MOLTEN = {r["id"]: r["molten"] for r in RESOURCES if r.get("molten")}

LOCATIONS = {
    "yard": {"name": "Двор", "requires_tech": None},
    "shaft": {"name": "Шахта", "requires_tech": None},  # engine.MINE_LOCATION_ID
    "workshop_loc": {"name": "Мастерская", "requires_tech": None},
    # Обычная локация-контейнер, БЕЗ спец-поведения - оно живёт на её дочерней
    # локации "converter" ниже (engine.CONVERTER_LOCATION_ID), см. CLAUDE.md.
    "mechanisms": {"name": "Механизмы", "requires_tech": None},
    # Вложена в workshop_loc (parent_location) - для тестов полноценного дерева
    # локаций: у workshop_loc теперь одновременно есть и собственные действия, и
    # дочерняя локация (см. engine.location_children).
    "sub_workshop": {"name": "Дальний угол мастерской", "requires_tech": None, "parent_location": "workshop_loc"},
    "converter": {"name": "Преобразователь", "requires_tech": None, "parent_location": "mechanisms"},  # engine.CONVERTER_LOCATION_ID
    # Вложена в "mechanisms" и гейтится requires_building - для теста паттерна
    # "постройка-механизм без produces/consumes открывает СВОЮ локацию с ручными
    # действиями" (см. BUILDINGS["mechanism_a"]/MANUAL_ACTIONS["mechanism_a_action"]).
    "mechanism_a_loc": {
        "name": "Механизм А", "requires_tech": None,
        "requires_building": "mechanism_a", "parent_location": "mechanisms",
    },
    # "Охота" (см. MOBS ниже) - для тестов search_mob_any/summon_boss/attack_encounter.
    "hunting_loc": {"name": "Охота", "requires_tech": None},
    # Локация-верстак (см. MANUAL_ACTIONS["grid_*"] ниже) - для тестов
    # engine.match_grid/location_crafting/grid_actions_at. Сетка 2x2 - минимальный
    # размер, на котором ещё можно проверить и обрезку краёв, и зеркало.
    # batch - крафт пачкой ("Скрафтить ×N", см. web_ui.craft_grid_view), нужен
    # тестам times>1; обрезку до 1 без флага проверяет test_web_ui_http.
    "grid_loc": {"name": "Тестовый верстак", "requires_tech": None, "crafting": {"width": 2, "height": 2, "batch": True}},
}

MANUAL_ACTIONS = {
    "gather_a": {"name": "Добыть A", "requires_tech": None, "location": "yard", "cost": {}, "gain": {"raw_a": 3}},
    "gather_b": {"name": "Добыть B", "requires_tech": None, "location": "yard", "cost": {}, "gain": {"raw_b": 2}},
    "craft_mid": {
        "name": "Сделать полуфабрикат", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 4}, "gain": {"mid": 1},
    },
    "craft_final": {
        "name": "Собрать изделие", "requires_tech": None, "location": "workshop_loc",
        "cost": {"mid": 2, "raw_b": 1}, "gain": {"final": 1},
    },
    "locked_action": {
        "name": "Заблокированное действие", "requires_tech": "tech1", "location": "shaft",
        "cost": {"raw_b": 1}, "gain": {"mid": 1},
    },
    "locked_by_building": {
        "name": "Действие у станка", "requires_tech": None, "requires_building": "gen", "location": "yard",
        "cost": {}, "gain": {"raw_a": 1},
    },
    "gather_slow_raw": {
        "name": "Добыть медленное сырьё", "requires_tech": None, "location": "workshop_loc",
        "cost": {}, "gain": {"slow_raw": 100},
    },
    "craft_slow_mid": {
        "name": "Сделать медленный полуфабрикат", "requires_tech": None, "location": "workshop_loc",
        "cost": {"slow_raw": 1}, "gain": {"slow_mid": 1},
    },
    "craft_slow_final": {
        "name": "Собрать медленное изделие", "requires_tech": None, "location": "workshop_loc",
        "cost": {"slow_mid": 1}, "gain": {"slow_final": 1},
    },
    "assemble_gadget": {
        "name": "Собрать гаджет", "requires_tech": None, "location": "workshop_loc",
        "cost": {"slow_mid": 10}, "gain": {"gadget": 1},
    },
    "craft_other_mid": {
        "name": "Сделать другой медленный полуфабрикат", "requires_tech": None, "location": "workshop_loc",
        "cost": {"slow_raw": 1}, "gain": {"other_mid": 1},
    },
    "assemble_widget": {
        "name": "Собрать виджет", "requires_tech": None, "location": "workshop_loc",
        "cost": {"slow_mid": 6}, "gain": {"widget": 1},
    },
    "assemble_capacitor": {
        "name": "Собрать конденсатор", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_b": 1, "catalyst": 1}, "gain": {"capacitor": 1},
    },
    "drill_tier1_only": {
        "name": "Сверлить (базовая дрель)", "requires_tech": None, "location": "workshop_loc",
        "cost": {}, "gain": {"raw_a": 1}, "requires_tool_tiers": {"drill": [1]},
    },
    "drill_tier2_only": {
        "name": "Сверлить (продвинутая дрель)", "requires_tech": None, "location": "workshop_loc",
        "cost": {}, "gain": {"raw_a": 3}, "requires_tool_tiers": {"drill": [2]},
    },
    # tool_scaling - для тестов effective_manual_action_stats: gain домножается на
    # ТЕКУЩИЙ тир "pickaxe" (переиспользует TOOLS["pickaxe"] ниже, тир 1 по умолчанию
    # = множитель x1, не отдельная "нейтральная" ветка кода). cost НЕ домножается -
    # тут пусто, но это в любом случае не тот механизм (см. engine.py).
    "gather_tool_scaled": {
        "name": "Добыть A киркой", "requires_tech": None, "location": "workshop_loc",
        "cost": {}, "gain": {"raw_a": 2}, "tool_scaling": {"tool": "pickaxe"},
    },
    # uses_tool_durability - для тестов do_manual_action: тратит 1 прочность
    # TOOLS["wrench"] за каждое выполнение (тир 2 держит max_durability=2, см.
    # TOOLS ниже) и блокируется, когда ключ уже сломан (tool_is_broken).
    "gather_wears_wrench": {
        "name": "Добыть A ключом", "requires_tech": None, "location": "workshop_loc",
        "cost": {}, "gain": {"raw_a": 1}, "uses_tool_durability": {"tool": "wrench"},
    },
    # location отсутствует вовсе (не просто null) - действие лежит в КОРНЕ дерева
    # локаций, наравне с локациями верхнего уровня - см. manual_actions_at(None, ...).
    "loose_action": {
        "name": "Действие без локации", "requires_tech": None,
        "cost": {}, "gain": {"raw_a": 1},
    },
    # Два рецепта на ОДИН и тот же ресурс - для теста приоритета автокрафта
    # (engine.RESOURCE_RECIPE). "low" стоит РАНЬШЕ в словаре, но с priority ниже
    # (по умолчанию 0) - без учёта priority автокрафт выбрал бы именно его
    # (setdefault брал первый попавшийся). "high" должен победить именно ЗА СЧЁТ
    # priority, а не порядка в словаре - так тест ловит регрессию "приоритет
    # не учитывается, побеждает порядок вставки".
    "priority_low": {
        "name": "Медленный рецепт приоритетного ресурса", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 1}, "gain": {"prioritized": 1},
    },
    "priority_high": {
        "name": "Быстрый рецепт приоритетного ресурса", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_b": 1}, "gain": {"prioritized": 1}, "priority": 5,
    },
    # Зависит от "mechanism_a" (см. BUILDINGS/LOCATIONS выше) - и доступностью
    # (requires_building), и эффективным cost/gain (улучшения "mechanism_a"
    # домножают именно ЭТО действие, см. engine.effective_manual_action_stats).
    "mechanism_a_action": {
        "name": "Провернуть механизм А", "requires_tech": None, "location": "mechanism_a_loc",
        "requires_building": "mechanism_a", "cost": {"raw_a": 2}, "gain": {"mech_output": 3},
    },
    # Многоходовое действие, ЗАВИСЯЩЕЕ от постройки-механизма (requires_building) -
    # для теста апгрейда "mech_a_duration_cut" (game/engine.py:manual_action_duration).
    "mechanism_a_delayed_action": {
        "name": "Долгий прогон механизма А", "requires_tech": None, "location": "mechanism_a_loc",
        "requires_building": "mechanism_a", "cost": {"raw_a": 3}, "gain": {"mech_output": 5},
        "duration_turns": 4,
    },
    # "Шанс потребления/получения" (game/engine.py:is_manual_action_deterministic/
    # _roll_chance_amounts) - cost непустой (значит формально мог бы попасть в
    # resource_recipe), но gain_chance < 1 на его ЕДИНСТВЕННЫЙ выход - действие
    # должно быть исключено из resource_recipe целиком, а lucky_output для
    # автокрафта должен выглядеть как обычное сырьё без рецепта.
    "craft_lucky": {
        "name": "Рискованная попытка", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 2}, "gain": {"lucky_output": 4},
        "gain_chance": {"lucky_output": 0.5},
    },
    # Симметричный случай - gain гарантирован (нет gain_chance вовсе), но cost_chance
    # < 1 на ингредиент. Тоже должно исключать действие из resource_recipe целиком
    # (шанс где угодно в cost ИЛИ gain делает весь рецепт "не автокрафтибельным").
    "craft_risky": {
        "name": "Рискованный крафт", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 10}, "gain": {"risky_output": 1},
        "cost_chance": {"raw_a": 0.1},
    },
    # Случайное количество produce (game/engine.py: _is_range_amount/
    # _roll_range_amounts, см. CLAUDE.md) - gain.variable_output - диапазон
    # {"min","max"} вместо плоского числа, НЕЗАВИСИМО от chance (здесь его нет
    # вовсе - действие гарантировано выполняется, но количество каждый раз
    # случайно). НЕ должно попадать в resource_recipe (см.
    # is_manual_action_deterministic) - для теста автокрафт-исключения по
    # диапазону, симметрично craft_lucky/craft_risky по chance.
    "craft_variable": {
        "name": "Собрать переменную партию", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 1}, "gain": {"variable_output": {"min": 2, "max": 6}},
    },
    # Многоходовое действие (game/engine.py:_process_manual_jobs, duration_turns=3) -
    # cost списывается сразу при клике, gain (+chance на часть выхода, для теста
    # "шанс/диапазон разворачиваются ПРИ ЗАВЕРШЕНИИ, не при старте") начисляется
    # только через 3 advance_turn. Непустой cost - формально годится в кандидаты
    # resource_recipe, но должен быть исключён целиком (см. CLAUDE.md).
    "craft_delayed": {
        "name": "Затяжной крафт", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 2}, "gain": {"delayed_output": 5},
        "duration_turns": 3,
    },
    # Другое многоходовое действие В ТОЙ ЖЕ локации, что craft_delayed
    # ("workshop_loc") - для теста "одна локация - одна активная многоходовая
    # задача одновременно" (game/engine.py:do_manual_action): пока craft_delayed
    # тикает, запустить ЭТО действие нельзя, хотя это другой action_id.
    "craft_delayed_sibling": {
        "name": "Соседний затяжной крафт", "requires_tech": None, "location": "workshop_loc",
        "cost": {"raw_a": 1}, "gain": {"sibling_delayed_output": 3},
        "duration_turns": 2,
    },
    # Многоходовое действие в ПОДЛОКАЦИИ "workshop_loc" (см. LOCATIONS["sub_workshop"])
    # - ограничение НЕ рекурсивное по parent_location, так что оно должно спокойно
    # запускаться параллельно с craft_delayed/craft_delayed_sibling в родительской
    # локации (тот же принцип, что "охота в подлокации шахты не мешает добыче жилы").
    "craft_delayed_sub": {
        "name": "Затяжной крафт в подлокации", "requires_tech": None, "location": "sub_workshop",
        "cost": {"raw_a": 1}, "gain": {"sub_delayed_output": 2},
        "duration_turns": 2,
    },
    # Сеточные действия локации "grid_loc" (2x2, см. LOCATIONS выше) - для тестов
    # engine.match_grid. "grid_line" - узор 1x2, ловится и в верхнем, и в нижнем
    # ряду сетки (проверка обрезки/сдвига, см. _normalize_grid/_pattern_shape).
    # Узор ОБЯЗАН быть указан в полный размер сетки локации (2x2, см. LOCATIONS
    # ["grid_loc"]) - валидатор (tools/content_validation.py:check_action_pattern)
    # сверяет буквальный размер rows с width/height. Занятая часть узора (верхний
    # ряд) при матчинге всё равно обрезается до 1x2 (см. _pattern_shape) - именно
    # это и проверяет tests/test_grid_crafting.py::test_match_shifted_to_bottom_row.
    "grid_line": {
        "name": "Сеточный рецепт-линия", "requires_tech": None, "location": "grid_loc",
        "cost": {"raw_a": 2}, "gain": {"grid_output": 1},
        "pattern": {"rows": ["AA", ".."], "key": {"A": "raw_a"}},
    },
    # Требует нерешённую по умолчанию tech1 - для теста "неоткрытое действие не матчится".
    "grid_locked": {
        "name": "Сеточный рецепт (заблокирован)", "requires_tech": "tech1", "location": "grid_loc",
        "cost": {"raw_b": 1}, "gain": {"grid_locked_output": 1},
        "pattern": {"rows": ["A.", ".."], "key": {"A": "raw_b"}},
    },
    # Несимметричный узор с pattern_mirror - для теста "зеркальная раскладка
    # тоже засчитывается, если и только если pattern_mirror включён".
    "grid_mirror": {
        "name": "Сеточный рецепт-зеркало", "requires_tech": None, "location": "grid_loc",
        "cost": {"raw_a": 1, "raw_b": 1}, "gain": {"grid_mirror_output": 1},
        "pattern": {"rows": ["AB", ".."], "key": {"A": "raw_a", "B": "raw_b"}}, "pattern_mirror": True,
    },
    # spawn_species (см. CLAUDE.md, do_manual_action) - ловля дикой особи,
    # НЕЗАВИСИМО от gain (тут его нет вовсе) - для теста справочника источников
    # (tools/web_ui.py: resource_sources, kind "spawn").
    "catch_critter_a": {
        "name": "Поймать дикое существо А", "requires_tech": None, "location": "yard",
        "cost": {}, "gain": {}, "spawn_species": "critter_a",
    },
    # Действие БЕЗ узора в той же локации-механизме - остаётся обычной карточкой
    # (см. CLAUDE.md, "два явных переключателя") - для теста, что оно не попадает
    # в grid_actions_at, хотя manual_actions_at его видит как обычно.
    "grid_plain_card": {
        "name": "Обычное действие на верстаке", "requires_tech": None, "location": "grid_loc",
        "cost": {"raw_a": 1}, "gain": {"grid_plain_output": 1},
    },
}

# Виды поломки (см. BUILDINGS["flaky_typed"] ниже) - "worn_gear" требует
# владения инструментом "wrench" (тир 2+, см. TOOLS выше) для починки.
BREAKDOWN_TYPES = {
    "worn_gear": {
        "name": "Изношенная шестерня",
        "description": "Тестовый вид поломки, требующий гаечный ключ для починки.",
        "required_tools": ["wrench"],
    },
}

BUILDINGS = {
    "gen": {
        "name": "Генератор", "description": "Тестовый источник энергии.",
        "requires_tech": None, "cost": {"raw_b": 5},
        "produces": {"energy": 2}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1},
    },
    "factory": {
        "name": "Фабрика", "description": "Тестовый потребитель энергии, производящий сырьё A.",
        "requires_tech": "tech1", "cost": {"mid": 3},
        "produces": {"raw_a": 2}, "consumes": {"energy": 1}, "breakdown_chance": 0.02,
        "repair_cost": {"raw_b": 2},
    },
    "flaky": {
        "name": "Нестабильный станок", "description": "Тестовая постройка с гарантированной поломкой каждый ход.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {"raw_b": 1}, "consumes": {}, "breakdown_chance": 1.0,
        "repair_cost": {"raw_a": 1},
    },
    # Как "flaky" (гарантированная поломка каждый ход), но поломка ТИПИЗИРОВАНА
    # (breakdown_types -> BREAKDOWN_TYPES["worn_gear"], требует TOOLS["wrench"]
    # тира 2+) - для тестов repair()/repairable_count() с недостающим/владеемым
    # инструментом, а не привычного "чинится всегда без вопросов" у "flaky".
    # Апгрейд breakdown_immunity - для теста "апгрейд отключает поломку вовсе".
    "flaky_typed": {
        "name": "Нестабильный станок (типизированный)",
        "description": "Тестовая постройка с гарантированной поломкой каждый ход и типизированной поломкой.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {"raw_b": 1}, "consumes": {}, "breakdown_chance": 1.0,
        "repair_cost": {"raw_a": 1}, "breakdown_types": ["worn_gear"],
        "upgrades": [
            {
                "id": "flaky_typed_immune", "name": "Неломкость",
                "description": "Полностью отключает поломку этой постройки.",
                "type": "breakdown_immunity", "value": 1, "requires_tech": None, "cost": {"raw_a": 5},
            },
        ],
    },
    "battery": {
        "name": "Батарея", "description": "Тестовое хранилище энергии.",
        "requires_tech": None, "cost": {"raw_a": 2},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1}, "energy_storage": 5,
    },
    "solar": {
        "name": "Панель", "description": "Тестовая постройка, работающая только днём.",
        "requires_tech": None, "cost": {"raw_a": 3},
        "produces": {"raw_b": 1}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1}, "active_period": "day",
    },
    "lab2": {
        "name": "Лаборатория тира 2", "description": "Тестовая постройка с lab_tier - открывает исследование технологий тира 2 (game/engine.py:lab_tier_capacity).",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1}, "lab_tier": 2,
        "upgrades": [
            # Для теста tech_research_turns()/game/engine.py:_farm_upgrade_multiplier -
            # сокращает НА 2 ХОДА (АБСОЛЮТНО, не доля) техи ТИРА 2 (== lab_tier этой
            # постройки), см. TECHS["tech_tier2_slow"].
            {
                "id": "lab2_speed", "name": "Ускоренные испытания",
                "description": "-2 хода к исследованию технологий тира 2.",
                "type": "research_speed", "value": 2, "requires_tech": None, "cost": {"raw_a": 2},
            },
        ],
    },
    # Три жёстко зашитых типа улучшений разом на ОДНОЙ постройке (она специально и
    # производит, и потребляет энергию) - для теста их совместной работы
    # (game/engine.py:effective_building_stats): scale домножает ВСЁ (produces И
    # consumes, включая энергию), energy_efficiency - ТОЛЬКО consumes.energy (после
    # scale), generation_efficiency - ТОЛЬКО produces.energy (тоже после scale).
    "upgradable_factory": {
        "name": "Модернизируемая фабрика",
        "description": "Тестовая постройка с тремя типами улучшений - для проверки их совместной работы.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {"raw_b": 2, "energy": 4}, "consumes": {"raw_a": 2, "energy": 1},
        "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "upgrades": [
            {
                "id": "uf_scale", "name": "Масштаб II", "description": "Удваивает весь рецепт разом.",
                "type": "scale", "value": 2, "requires_tech": None, "cost": {"raw_a": 5},
            },
            # Второй уровень той же прогрессии - АБСОЛЮТНЫЙ множитель x3 (не x1.5
            # "довеска" к уже купленному x2!) - для теста "среди купленных улучшений
            # ОДНОГО типа действует только самое сильное" (см. _apply_building_upgrades):
            # купив ОБА (uf_scale x2 и uf_scale_more x3), итог должен быть x3, а не
            # x6 (как было бы при перемножении) и не x2 (как было бы, выбери движок
            # по ошибке первое найденное вместо максимума).
            {
                "id": "uf_scale_more", "name": "Масштаб III", "description": "Утраивает весь рецепт относительно базового (не относительно Масштаба II).",
                "type": "scale", "value": 3, "requires_tech": None, "cost": {"raw_a": 5},
            },
            {
                "id": "uf_energy_eff", "name": "Энергоэффективность", "description": "-50% расход энергии.",
                "type": "energy_efficiency", "value": 0.5, "requires_tech": None, "cost": {"raw_a": 5},
            },
            {
                "id": "uf_gen_eff", "name": "Генерация", "description": "+50% выработка энергии.",
                "type": "generation_efficiency", "value": 1.5, "requires_tech": "tech1", "cost": {"raw_a": 5},
            },
        ],
    },
    # Постройка-"механизм" без единого produces/consumes - весь её эффект выражается
    # через ручное действие "mechanism_a_action" (requires_building указывает СЮДА)
    # и локацию "mechanism_a_loc" (тоже requires_building сюда). Улучшение этой
    # постройки должно домножать cost/gain действия, а не пустые produces/consumes
    # самой постройки - см. engine.effective_manual_action_stats.
    "mechanism_a": {
        "name": "Механизм А", "description": "Тестовый механизм без produces/consumes - весь эффект через зависимое ручное действие.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1},
        "upgrades": [
            {
                "id": "mech_a_scale", "name": "Масштаб", "description": "x2 к зависимому действию.",
                "type": "scale", "value": 2, "requires_tech": None, "cost": {"raw_a": 3},
            },
            # Для теста manual_action_duration()/do_manual_action() - см.
            # MANUAL_ACTIONS["mechanism_a_delayed_action"] ниже (duration_turns=4,
            # requires_building="mechanism_a"): value=0.5 сокращает 4 хода до
            # ceil(4*0.5)=2.
            {
                "id": "mech_a_duration_cut", "name": "Ускорение", "description": "-50% к сроку зависимого многоходового действия.",
                "type": "action_duration_reduction", "value": 0.5, "requires_tech": None, "cost": {"raw_a": 4},
            },
        ],
    },
    # Ненулевая ДРОБНАЯ база (не от улучшения - просто так написано в контенте) на
    # ресурсе, НЕ помеченном fractional (raw_b) - для прямой проверки округления
    # вверх в engine.py:_round_amounts независимо от механизма улучшений.
    "fractional_base_building": {
        "name": "Тест дробной базы", "description": "Тестовая постройка с нецелым produces на нецелочисленном ресурсе.",
        "requires_tech": None, "cost": {}, "produces": {"raw_b": 1.5}, "consumes": {},
        "breakdown_chance": 0.0, "repair_cost": {},
    },
    "automation_hub": {
        "name": "Автоматизация", "description": "Тестовая постройка, открывающая автокрафт и дающая вычислительную мощность и скорость.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1}, "compute_power": 15,
        # Сборщик локации мастерской (workshop_loc): мгновенно, 50 крафтов за ход - с
        # запасом выше любых fires в тестах, которые не про лимит сборщика.
        "autocraft_assembler": {"location": "workshop_loc", "per_turn": 50},
    },
    "bare_processor": {
        "name": "Голый процессор", "description": "Тестовая постройка: даёт compute_power, но НИЧЕГО не разблокирует - нужна отдельно от automation_hub, чтобы проверять not_unlocked/tech_locked в изоляции от нового no_processor (см. engine._ensure_available).",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1}, "compute_power": 1000,
    },
    "trickle_hub": {
        "name": "Медленный хаб", "description": "Тестовая постройка с большой мощностью, но низкой скоростью автокрафта.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1},
        "compute_power": 10000,
        "autocraft_assembler": {"location": "workshop_loc", "per_turn": 2},
    },
    "slot_hub": {
        "name": "Диспетчер", "description": "Тестовая постройка, дающая +1 слот автокрафта; можно построить только одну.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0,
        "repair_cost": {"raw_a": 1}, "autocraft_slots": 1, "max_count": 1,
    },
    "furnace": {
        "name": "Печь", "description": "Тестовая многоэтапная постройка (construction_stages) - три этапа с разной ценой и описанием.",
        "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
        "construction_stages": [
            {"cost": {"raw_a": 2}, "description": "установить генератор"},
            {"cost": {"raw_b": 1}, "description": "установить радиатор"},
            {"cost": {"raw_a": 1, "raw_b": 1}, "description": "подключить трубы"},
        ],
    },
    "kiln": {
        "name": "Обжиговая печь",
        "description": "Тестовая постройка с ровно ОДНИМ этапом в construction_stages - для проверки, что счётчик "
                        "«N из M» не показывается, пока стадия одна (см. current_construction_stage).",
        "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
        "construction_stages": [
            {"cost": {"raw_a": 3}, "description": "обжечь кирпичи"},
        ],
    },
    "reactor": {
        "name": "Реактор",
        "description": "Тестовая многоэтапная постройка, чья первая стадия требует автокрафта в фоне - "
                        "для проверки, что стадии нельзя перепрыгнуть, даже пока автокрафт докрафтывает предыдущую.",
        "requires_tech": None, "cost": {},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
        "construction_stages": [
            {"cost": {"slow_mid": 10}, "description": "разогреть реактор"},
            {"cost": {"raw_a": 1}, "description": "подключить контроль"},
        ],
    },
    "recipe_board": {
        "name": "Стенд с рецептами", "description": "Тестовая постройка - делает ингредиенты в рецептах кликабельными (engine.RECIPE_BOARD_BUILDING_ID).",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {},
    },
    "energy_converter": {
        "name": "Преобразователь", "description": "Тестовый энергетический преобразователь с потолком забора энергии за ход - "
                                                    "ведёт один рецепт из ENERGY_RECIPES за раз, выбранный отдельно (start_energy_recipe).",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "energy_converter": {"max_energy_per_turn": 4},
        # Прогрессия абсолютных бонусов (не складываются - действует только
        # самый сильный купленный уровень), см. test_energy_converter_capacity_upgrade_*.
        "upgrades": [
            {"id": "conv_cap_1", "name": "Расширение I", "description": "+3 энергии/ход.",
             "type": "energy_converter_capacity", "value": 3, "requires_tech": None, "cost": {"raw_a": 1}},
            {"id": "conv_cap_2", "name": "Расширение II", "description": "+9 энергии/ход.",
             "type": "energy_converter_capacity", "value": 9, "requires_tech": None, "cost": {"raw_a": 2}},
            {"id": "conv_auto", "name": "Автоматизация", "description": "Автокрафт может запускать рецепты.",
             "type": "autocraft", "value": 1, "requires_tech": None, "cost": {}},
        ],
    },
    "mega_converter": {
        "name": "Мега-преобразователь", "description": "Тестовый энергетический преобразователь без потолка забора энергии за ход - "
                                                         "может закрыть весь порог рецепта за один ход, если энергии хватает сразу.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "energy_converter": {"max_energy_per_turn": None},
        "upgrades": [
            {"id": "mega_cap_1", "name": "Расширение", "description": "Не даёт эффекта - у mega_converter и так нет потолка.",
             "type": "energy_converter_capacity", "value": 100, "requires_tech": None, "cost": {"raw_a": 1}},
            {"id": "mega_auto", "name": "Автоматизация", "description": "Автокрафт может запускать рецепты.",
             "type": "autocraft", "value": 1, "requires_tech": None, "cost": {}},
        ],
    },
    "amplifier": {
        "name": "Усилитель", "description": "Тестовая постройка-заглушка - ничего не производит и не потребляет, "
                                             "нужна только чтобы требовать работающий energy_converter (requires_building) "
                                             "для теста generic-гейта requires_building у ПОСТРОЕК.",
        "requires_tech": None, "requires_building": "energy_converter", "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
    },
    # ВТОРОЙ, независимый тип энергетической машины - для тестов
    # energy_converter_max_energy_per_turn/_process_energy_jobs на нескольких
    # ТИПАХ разом (у каждого своя пропускная способность, свои апгрейды, свой
    # приоритет) - см. energy_priority ниже и ENERGY_RECIPES["upgrade_matter"].
    "matter_generator": {
        "name": "Генератор материи", "description": "Тестовый ВТОРОЙ тип энергетической машины - "
                                                      "материал + энергия -> материал повыше (upgrade_matter).",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "energy_converter": {"max_energy_per_turn": 50},
        "upgrades": [
            {"id": "matter_cap_1", "name": "Расширение", "description": "+30 энергии/ход - для теста, что апгрейд "
             "ОДНОЙ постройки не задевает пропускную способность ДРУГОГО типа (energy_converter).",
             "type": "energy_converter_capacity", "value": 30, "requires_tech": None, "cost": {"raw_a": 1}},
            {"id": "matter_auto", "name": "Автоматизация", "description": "Автокрафт может запускать рецепты.",
             "type": "autocraft", "value": 1, "requires_tech": None, "cost": {}},
        ],
    },
    "apiary": {
        "name": "Питомник", "description": "Тестовая ферма (breeding_slots) - для тестов базового поселения/производства/старости, а также repeatable-апгрейда слотов.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "breeding_slots": 2,
        "upgrades": [
            # repeatable: покупается сколько угодно раз подряд, цена растёт
            # геометрически (cost_growth), value СТАКАЕТСЯ (value * число покупок) -
            # см. game/engine.py:_purchased_upgrade_value/repeatable_upgrade_cost.
            {
                "id": "apiary_slot_bonus", "name": "Доп. слот", "description": "+1 слот в ЭТОМ экземпляре пасеки, покупается многократно.",
                "type": "breeding_slots_bonus", "value": 1, "requires_tech": None,
                "cost": {"raw_a": 2}, "repeatable": True, "cost_growth": 2,
            },
        ],
    },
    "soul_synth": {
        "name": "Синтезатор душ (тест)", "description": "Тестовый синтезатор душ (soul_collector) с repeatable-апгрейдом слотов и обычным апгрейдом эффективности.",
        "requires_tech": None, "cost": {"raw_a": 3},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "soul_collector": {"slots": 1, "accepts_kind": "test"},
        "upgrades": [
            {
                "id": "soul_synth_slot", "name": "Доп. слот", "description": "+1 слот, покупается многократно.",
                "type": "soul_collector_slots", "value": 1, "requires_tech": None,
                "cost": {"raw_a": 2}, "repeatable": True, "cost_growth": 2,
            },
            {
                "id": "soul_synth_eff", "name": "Эффективность", "description": "x2 продукция синтезатора.",
                "type": "soul_generation_efficiency", "value": 2, "requires_tech": None, "cost": {"raw_a": 5},
            },
        ],
    },
    "automator": {
        "name": "Автоматизатор (тест)", "description": "Тестовая постройка с automation_hub и repeatable-апгрейдом слотов.",
        "requires_tech": None, "cost": {"raw_a": 3},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "automation_hub": {"slots": 1},
        "upgrades": [
            {
                "id": "automator_slot", "name": "Доп. слот", "description": "+1 слот, покупается многократно.",
                "type": "automation_slots", "value": 1, "requires_tech": None,
                "cost": {"raw_a": 2}, "repeatable": True, "cost_growth": 2,
            },
        ],
    },
    # Ритуальный круг (ritual_circle) - тестовый алтарь с прогрессией пьедesталов
    # кольца 3 -> 6 -> 9 (ДВА НЕ-repeatable уровня одного типа, как scale "x2 ->
    # x3" - среди купленных действует только самый сильный, а не сумма; второй
    # уровень требует, чтобы первый уже был куплен - см.
    # game/engine.py:building_upgrade_prerequisite). НЕ построена по умолчанию,
    # тот же приём, что у apiary/genetics_lab_building - не засоряет exact-set
    # тесты.
    "ritual_altar": {
        "name": "Ритуальный алтарь (тест)", "description": "Тестовый алтарь (ritual_circle) с прогрессией пьедесталов кольца 3 -> 6 -> 9.",
        "requires_tech": None, "cost": {"raw_a": 3},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "ritual_circle": {"ring_pedestals": 3},
        "upgrades": [
            {
                "id": "ritual_altar_ring_6", "name": "Расширение круга (6)", "description": "Кольцо алтаря до 6 пьедесталов.",
                "type": "ritual_pedestals", "value": 3, "requires_tech": None, "cost": {"raw_a": 2},
            },
            {
                "id": "ritual_altar_ring_9", "name": "Расширение круга (9)", "description": "Кольцо алтаря до 9 пьедесталов.",
                "type": "ritual_pedestals", "value": 6, "requires_tech": None, "cost": {"raw_a": 4},
            },
        ],
    },
    # Плавильни (buildings.*.smeltery, tests/test_smeltery.py): холодная яма (1 слот,
    # тигель 4, жар 1) и горн (2 слота/копия, тигель 6/копия, жар 2) с апгрейдами всех
    # трёх типов. НЕ построены по умолчанию.
    "smelt_pit": {
        "name": "Плавильная яма (тест)", "description": "Тестовая плавильня с низким жаром.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "smeltery": {"slots": 1, "capacity": 4, "heat": 1},
    },
    "smelt_forge": {
        "name": "Горн (тест)", "description": "Тестовая плавильня с высоким жаром.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "smeltery": {"slots": 2, "capacity": 6, "heat": 2},
        "upgrades": [
            {"id": "forge_capacity", "name": "Большой тигель", "description": "+4 к тиглю.",
             "type": "smeltery_capacity", "value": 4, "requires_tech": None, "cost": {"raw_a": 1}},
            {"id": "forge_slots", "name": "Ещё слот", "description": "+1 слот плавки.",
             "type": "smeltery_slots", "value": 1, "requires_tech": None, "cost": {"raw_a": 1}},
            {"id": "forge_heat", "name": "Большие мехи", "description": "+1 к жару.",
             "type": "smeltery_heat", "value": 1, "requires_tech": None, "cost": {"raw_a": 1}},
        ],
    },
    # Реактор (buildings.*.reactor) - решётка 2x2, принимает все 4 роли;
    # апгрейды покрывают все три типа сразу (reactor_zoning/reactor_grid_size/
    # reactor_telemetry) - см. game/engine.py:_process_reactors и
    # tests/test_reactor.py. НЕ построен по умолчанию (как apiary и др.) - не
    # засоряет exact-set тесты остальных модулей.
    "test_reactor": {
        "name": "Реактор (тест)", "description": "Тестовый реактор (reactor) - решётка ячеек с ролями fuel/moderator/coolant/absorber.",
        "requires_tech": None, "cost": {"raw_a": 5},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "reactor": {
            "grid_width": 2, "grid_height": 2, "zones_default": 1,
            "accepts_roles": ["fuel", "moderator", "coolant", "absorber", "reflector", "shield"],
            "heat_cap_per_cell": 10, "soft_threshold_pct": 0.5,
            "fuel_adjacency_heat_step": 0.5, "fuel_adjacency_output_step": 0.25,
            "moderator_adjacency_output_bonus": 1.0, "absorber_adjacency_reduction": 1.0,
            "shield_adjacency_reduction": 1.0,
            "reflector_adjacency_output_mult_step": 0.5,
            "thermal_shock_flow_delta_pct": 40, "thermal_shock_heat_bonus_pct": 0.2,
            "accident_table": "test_meltdown",
        },
        "upgrades": [
            {
                "id": "test_reactor_zoning_2", "name": "2 зоны", "description": "Решётка делится на 2 зоны.",
                "type": "reactor_zoning", "value": 2, "requires_tech": None, "cost": {"raw_a": 3},
            },
            {
                "id": "test_reactor_grid_3x2", "name": "Решётка 3x2", "description": "Расширение решётки до 3x2.",
                "type": "reactor_grid_size", "value": {"width": 3, "height": 2}, "requires_tech": None, "cost": {"raw_a": 4},
            },
            {
                "id": "test_reactor_telemetry", "name": "Телеметрия", "description": "Точные показания зон.",
                "type": "reactor_telemetry", "value": True, "requires_tech": None, "cost": {"raw_a": 2},
            },
        ],
    },
    # Случайное количество produce (game/engine.py: _is_range_amount/
    # _roll_range_amounts, см. CLAUDE.md) - produces.variable_output - диапазон
    # {"min","max"} вместо плоского числа. uf_scale-как апгрейд НЕ добавлен сюда
    # намеренно - тест масштабирования диапазона апгрейдом живёт на
    # upgradable_factory отдельно (там же, где остальные типы апгрейдов), эта
    # постройка - для голого "бросок раз в ход, домноженный на число рабочих копий".
    "variable_gen": {
        "name": "Переменный генератор (тест)", "description": "Тестовая постройка со случайным produce.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {"variable_output": {"min": 1, "max": 4}}, "consumes": {},
        "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        # Для теста "апгрейд типа scale домножает ОБЕ границы диапазона" (см.
        # game/engine.py:_scale_amount) - без завязки на upgradable_factory, чтобы
        # не задеть его собственные exact-set тесты на raw_b/energy.
        "upgrades": [
            {
                "id": "vg_scale", "name": "Масштаб", "description": "x2 весь рецепт разом.",
                "type": "scale", "value": 2, "requires_tech": None, "cost": {"raw_a": 5},
            },
        ],
    },
    # Вторая, отдельная ферма - НА ЭНЕРГИИ, с апгрейдами breeding_output/
    # breeding_immortality - для тестов "апгрейд фермы X не влияет на особей в
    # ДРУГОЙ ферме Y" (у каждого ФИЗИЧЕСКОГО ЭКЗЕМПЛЯРА свой пул слотов и свои
    # апгрейды, см. game/engine.py:farm_instance_slot_capacity/
    # _farm_instance_upgrade_multiplier). Шанс мутации (breeding_mutation_chance)
    # сюда НЕ входит - он у отдельного механизма crossing_station ниже, фермы
    # скрещивание не проводят.
    "electric_apiary": {
        "name": "Электропитомник", "description": "Тестовая ферма на энергии с апгрейдами продукции/бессмертия.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {"energy": 1}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "breeding_slots": 1,
        "upgrades": [
            {
                "id": "ea_output", "name": "Продукция", "description": "x2 продукция особей этой фермы.",
                "type": "breeding_output", "value": 2, "requires_tech": None, "cost": {"raw_a": 5},
            },
            {
                "id": "ea_immortal", "name": "Бессмертие", "description": "Особи этой фермы не умирают от старости.",
                "type": "breeding_immortality", "value": 1, "requires_tech": None, "cost": {"raw_a": 5},
            },
        ],
    },
    # ТРЕТЬЯ ферма - специально для тестов "апгрейды ферм теперь НА ЭКЗЕМПЛЯР, не
    # на тип" (см. game/engine.py:_ensure_farm_instances/farm_instances_upgrade_*):
    # два экземпляра этого ТИПА, с РАЗНЫМИ купленными апгрейдами (в т.ч.
    # environment_emulation - эмуляция среды, взаимоисключающая замена, а не
    # прогрессия), не должны делить их между собой, в отличие от apiary/
    # electric_apiary выше (те по-прежнему используются только для тестов на
    # СТАРОЕ поведение общего пула/агрегата, не трогаются).
    "industrial_apiary": {
        "name": "Промышленный питомник", "description": "Тестовая ферма с апгрейдами на экземпляр, включая эмуляцию среды.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "breeding_slots": 2,
        "upgrades": [
            {
                "id": "ia_output", "name": "Продукция", "description": "x2 продукция особей ЭТОГО экземпляра пасеки.",
                "type": "breeding_output", "value": 2, "requires_tech": None, "cost": {"raw_a": 5},
            },
            {
                "id": "ia_env_forest", "name": "Эмуляция леса (тест)", "description": "Эмулирует среду 'forest' в ЭТОМ экземпляре.",
                "type": "environment_emulation", "value": "forest", "requires_tech": None, "cost": {"raw_a": 3},
            },
            {
                "id": "ia_env_water", "name": "Эмуляция воды (тест)", "description": "Эмулирует среду 'water' в ЭТОМ экземпляре.",
                "type": "environment_emulation", "value": "water", "requires_tech": None, "cost": {"raw_a": 3},
            },
        ],
    },
    # Отдельная постройка с generic-полем genetics_lab (НЕ ферма - без
    # breeding_slots) - для тестов analyze_creature/synthesize_creature.
    "genetics_lab_building": {
        "name": "Генлаборатория", "description": "Тестовая генетическая лаборатория (genetics_lab) - опознание/синтез особей.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "genetics_lab": True,
    },
    # Отдельный механизм скрещивания (НЕ ферма - без breeding_slots) - для
    # тестов breeding_station_available/breed_creatures/_breeding_mutation_multiplier.
    # id НАРОЧНО не совпадает с id реальной постройки content.json ("breeding_station") -
    # движок обязан проверять generic-поле breeding_station, а не хардкодить
    # конкретный id (тот же принцип, что genetics_lab_building выше).
    "crossing_station": {
        "name": "Станция скрещивания (тест)", "description": "Тестовый механизм скрещивания (breeding_station) с апгрейдом шанса мутации.",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "breeding_station": True,
        "upgrades": [
            {
                "id": "cs_mutation", "name": "Мутагенез", "description": "x2 шанс мутации при скрещивании.",
                "type": "breeding_mutation_chance", "value": 2, "requires_tech": None, "cost": {"raw_a": 5},
            },
        ],
    },
    # Стол зачарований (enchanting_table) - НЕ построена по умолчанию (тот же
    # приём, что genetics_lab_building/crossing_station выше), см.
    # tests/test_enchanting.py. cost попытки - raw_a (не заводим отдельный
    # ресурс-книгу, фикстуре хватает уже существующего raw_a).
    "ench_table": {
        "name": "Стол зачарований (тест)", "description": "Тестовый стол зачарований (enchanting_table).",
        "requires_tech": None, "cost": {"raw_a": 1},
        "produces": {}, "consumes": {}, "breakdown_chance": 0.0, "repair_cost": {"raw_a": 1},
        "enchanting_table": {"cost": {"raw_a": 1}},
    },
}

ENERGY_RECIPES = {
    "make_raw_b": {
        "name": "Сделать сырьё B из энергии", "description": "Тестовый рецепт крафта за энергию.",
        "requires_tech": None, "threshold": 10, "produces": {"raw_b": 1},
    },
    "make_catalyst": {
        "name": "Сделать катализатор из энергии", "description": "Тестовый второй, независимый рецепт крафта за энергию.",
        "requires_tech": None, "threshold": 8, "produces": {"catalyst": 1},
    },
    # machine + consumes - для тестов "Генератора материи" (см. CLAUDE.md): рецепт
    # можно запустить ТОЛЬКО в matter_generator (не в energy_converter, даже если
    # там есть свободный слот), и он требует материал В ДОПОЛНЕНИЕ к энергии.
    "upgrade_matter": {
        "name": "Улучшить материю", "description": "Тестовый рецепт крафта за энергию с материальной ценой и "
                                                     "привязкой к конкретному типу машины (matter_generator).",
        "requires_tech": None, "threshold": 20, "machine": "matter_generator",
        "consumes": {"raw_a": 5}, "produces": {"upgraded_matter": 1},
    },
    # Случайное количество produce (game/engine.py: _is_range_amount) - для теста
    # "produces рецепта крафта за энергию может быть диапазоном".
    "make_variable": {
        "name": "Сделать переменную партию из энергии", "description": "Тестовый рецепт крафта за энергию со случайным produce.",
        "requires_tech": None, "threshold": 5, "produces": {"variable_output": {"min": 3, "max": 7}},
    },
}

# 3 ГЛОБАЛЬНЫХ признака генотипа (species - отдельно, ниже, через SPECIES[].
# dominant/weight) - "normal"/"day" везде намеренно ДОЛГОЖИВУЩИЕ/доминантные
# (lifespan.normal=100 - практически бессмертна на длину обычного теста), чтобы
# тесты, которым старость не важна, не спотыкались об неё случайно; "short"/
# "fast"/"night"/"always" - для тестов, которым нужны конкретные другие значения.
GENETIC_TRAITS = {
    "lifespan": {
        "name": "Тестовый срок жизни",
        "alleles": [
            {"id": "short", "name": "Короткая", "value": 3, "dominant": False, "weight": 1},
            {"id": "normal", "name": "Обычная", "value": 100, "dominant": True, "weight": 5},
        ],
    },
    "output_multiplier": {
        "name": "Тестовая продуктивность",
        "alleles": [
            {"id": "normal", "name": "Обычная", "value": 1, "dominant": True, "weight": 5},
            {"id": "fast", "name": "Быстрая", "value": 2, "dominant": False, "weight": 1},
        ],
    },
    "active_period": {
        "name": "Тестовая активность",
        "alleles": [
            {"id": "day", "name": "Днём", "dominant": True, "weight": 5},
            {"id": "night", "name": "Ночью", "dominant": False, "weight": 1},
            {"id": "always", "name": "Всегда", "dominant": False, "weight": 1},
        ],
    },
    # 6 id ЗАКРЫТЫ (tools/content_validation.py:_ENVIRONMENT_ALLELE_IDS) - как у
    # active_period, значения тут не свободные. Аллели среды решают только КАКАЯ
    # среда "благоприятна" для генотипа особи - САМ бонус (ресурс/количество/шанс)
    # живёт на SPECIES ниже, не здесь (см. комментарий у SPECIES).
    "environment": {
        "name": "Тестовая среда обитания",
        "alleles": [
            {"id": "forest", "name": "Лес", "dominant": True, "weight": 5},
            {"id": "fields", "name": "Поля", "dominant": False, "weight": 5},
            {"id": "caves", "name": "Пещеры", "dominant": False, "weight": 3},
            {"id": "desert", "name": "Пустыня", "dominant": False, "weight": 3},
            {"id": "water", "name": "Вода", "dominant": False, "weight": 2},
            {"id": "space", "name": "Космос", "dominant": False, "weight": 1},
        ],
    },
}

# Виды для разведения (фермы) - ключ = id уже существующего ресурса (см.
# RESOURCES выше), тот же принцип, что у MINING_INFO; это аллели признака "вид".
# critter_a - доминантный, ловится в диком виде (weight=5), без потребления.
# critter_b - рецессивный, weight=0 (НЕ ловится в диком виде - только через
# мутацию), требует critter_food (для теста "не хватает consumes - не
# производит, но и не умирает"). bonus_resource у каждого вида - СВОЙ, РАЗНЫЙ
# ресурс (для теста "бонус зависит от вида, не от среды" - два вида в ОДНОЙ и
# той же благоприятной среде дают разные ресурсы), bonus_chance - произвольные
# числа (тесты бонуса мокают random, а не полагаются на эти значения).
SPECIES = {
    "critter_a": {
        "description": "Тестовый вид А - доминантный, ловится в диком виде, без потребления.",
        "produces": {"species_output": 2}, "consumes": {}, "dominant": True, "weight": 5,
        "bonus_resource": "species_output", "bonus_amount": 1, "bonus_chance": 0.3,
    },
    "critter_b": {
        "description": "Тестовый вид Б - рецессивный, требует корм, доступен только через мутацию.",
        "produces": {"species_output": 5}, "consumes": {"critter_food": 1}, "dominant": False, "weight": 0,
        "bonus_resource": "raw_a", "bonus_amount": 1, "bonus_chance": 0.25,
    },
}

# Единственная тестовая мутация - critter_a x critter_a -> шанс critter_b
# (гетерозиготная пара по виду при скрещивании ДВУХ critter_a невозможна на
# первый взгляд, но если оба родителя гомозиготны critter_a, ребёнок тоже будет
# гомозиготен critter_a - мутация тут никогда не сработает "естественно"; тесты
# мутации явно скрещивают особей с РАЗНЫМ генотипом видовой пары через прямое
# построение state.creatures, а не полагаются на естественный розыгрыш).
MUTATIONS = [
    {
        "parent_a": "critter_a", "parent_b": "critter_a",
        "requires_tech": None, "requires_building": None,
        "chance": 0.5,
        "outcomes": [{"species_id": "critter_b", "weight": 1}],
    },
]

# environment_mutation_chance=0.0 по умолчанию - существующие тесты скрещивания
# не должны случайно словить "переигранную" среду потомка; тест самой мутации
# среды подменяет это значение точечно (тем же приёмом, что и другие
# вероятностные тесты - см. CLAUDE.md).
GENETICS_SETTINGS = {"synthesis_cost": {"species_output": 10}, "environment_mutation_chance": 0.0}

# HP игрока/дебафф "Ранен" (game/engine.py:player_max_hp/current_player_hp/
# is_wounded/_process_combat) - player_base_hp=20 даёт круглые числа с
# MOBS["test_mob"]["damage"]/["test_boss"]["damage"] ниже в тестах ответного
# урона (см. tests/test_combat_and_souls.py).
COMBAT_SETTINGS = {
    "player_base_hp": 20, "hp_regen_per_turn": 3,
    "wounded_turns": 5, "wounded_damage_multiplier": 0.5,
}


def make_genotype(species_id, *, lifespan="normal", output_multiplier="normal", active_period="day", environment="forest"):
    """Гомозиготный (обе аллели пары одинаковы) генотип особи для тестов - тот
    же формат, что state.creatures[...]['genotype'] - чтобы не повторять
    словарь на 5 признаков вручную в каждом тесте."""
    return {
        "species": [species_id, species_id],
        "lifespan": [lifespan, lifespan],
        "output_multiplier": [output_multiplier, output_multiplier],
        "active_period": [active_period, active_period],
        "environment": [environment, environment],
    }

TECHS = {
    "tech1": {
        "name": "Технология 1", "tier": 1, "description": "Тестовая технология 1.",
        "requires": [], "cost": {"raw_a": 5}, "effects": [],
    },
    "tech2": {
        "name": "Технология 2", "tier": 1, "description": "Тестовая технология 2.",
        "requires": ["tech1"], "cost": {"mid": 2},
        "effects": [{"building": "factory", "produces": {"raw_a": 1}}],
    },
    "tech3": {
        "name": "Технология 3", "tier": 1, "description": "Тестовая технология 3.",
        "requires": ["tech1"], "cost": {"raw_b": 3},
        "effects": [{"building": "*", "breakdown_delta": -0.5}],
    },
    # Тир 2 - фикстура специально под лаборатории (game/engine.py:lab_tier_capacity):
    # requires пуст (не зависит от tech1/2/3), чтобы тесты могли исследовать её сразу,
    # проверяя ИМЕННО гейт лаборатории, а не заодно ещё и цепочку requires.
    "tech_tier2": {
        "name": "Технология тира 2", "tier": 2, "description": "Тестовая технология 2 тира.",
        "requires": [], "cost": {"raw_a": 1}, "effects": [],
    },
    # Для теста многоходового исследования (game/engine.py:research/
    # _process_research/tech_research_turns) - отдельная от tech_tier2
    # технология, чтобы не ломать существующие тесты LabTierTests, ожидающие
    # МГНОВЕННОЕ завершение tech_tier2. research_turns=5, с BUILDINGS["lab2"]
    # ["upgrades"] research_speed (value=2) сокращается до 3 (см. lab2 выше).
    "tech_tier2_slow": {
        "name": "Технология тира 2 (долгая)", "tier": 2,
        "description": "Тестовая многоходовая технология 2 тира.",
        "requires": [], "cost": {"raw_a": 1}, "effects": [], "research_turns": 5,
    },
    # Вторая многоходовая технология (тот же тир, другая цена) - для теста
    # переключения МЕЖДУ ДВУМЯ многоходовыми исследованиями (game/engine.py:
    # research() - "переключение в любой момент"), чтобы убедиться, что новый
    # job заводится СВЕЖИМ (turns_done=0 от своего research_turns), а не
    # донашивает счётчик отменённого.
    "tech_tier2_slow_b": {
        "name": "Технология тира 2 (долгая, вторая)", "tier": 2,
        "description": "Тестовая многоходовая технология 2 тира, вторая линейка.",
        "requires": [], "cost": {"raw_b": 1}, "effects": [], "research_turns": 4,
    },
}

MINING_INFO = {
    "mineral": {
        "requires_tech": None, "required_tool": "pickaxe", "required_tier": 1,
        "rarity": 1, "toughness": 2, "field_cluster_size": [2, 4], "base_yield": 4,
    },
    "rare_mineral": {
        "requires_tech": None, "required_tool": "pickaxe", "required_tier": 2,
        "rarity": 5, "toughness": 6, "field_cluster_size": [1, 1], "base_yield": 5,
    },
}

MINE_FIELD = {
    "size": 6,
    "dig_tool": "pickaxe",
    "cluster_budget": 4,
    "scanner_tool": "ore_scanner",
    "scanner_types_tier": 2,
    "scanner_counts_tier": 3,
    # Два радиусных порога (тир 4/5) МЕЖДУ counts_tier и reveal_all_tier - см.
    # engine.py: _scanner_sense_radius/mine_field_view. Радиусы намеренно
    # маленькие (1/2), чтобы уместиться в тестовом поле 6x6.
    "scanner_radius_tiers": [
        {"tier": 4, "radius": 1},
        {"tier": 5, "radius": 2},
    ],
    "scanner_reveal_all_tier": 6,
    "rock_resource": "raw_a",
    "rock_yield": 2,
    "rock_toughness": 1,
    "rock_icon": None,
    "rock_broken_icon": None,
}

TIME = {"hours_per_turn": 3, "day_start_hour": 6, "day_end_hour": 21}

TOOLS = {
    "pickaxe": {
        "name": "Кирка",
        "enchant_kind": "mining",
        "tiers": [
            {"name": "Тир 1", "cost": None, "requires_tech": None, "mining_area": 1, "mining_power": 3},
            {"name": "Тир 2", "cost": {"raw_a": 10}, "requires_tech": None, "mining_area": 1, "mining_power": 5},
            {"name": "Тир 3", "cost": {"raw_b": 10}, "requires_tech": "tech1", "mining_area": 2, "mining_power": 7},
        ],
    },
    "drill": {
        "name": "Дрель",
        "tiers": [
            {"name": "Ручная дрель", "cost": None, "requires_tech": None},
            {"name": "Электродрель", "cost": {"raw_a": 5}, "requires_tech": None},
        ],
    },
    # Щуп-детектор шахты (mine_field.scanner_tool) - чисто информационный,
    # никаких mining_area/mining_power. Тир 1 - дефолтный для всех, намеренно
    # ничего не даёт (см. engine.py: scanner_tier/mine_field_view) - все
    # пороги детализации (типы/счёт/радиус/полная подсветка) живут на
    # MINE_FIELD, не на самих тирах - тут только 6 позиций-заглушек.
    "ore_scanner": {
        "name": "Рудный щуп",
        "tiers": [
            {"name": "Тир 1", "cost": None, "requires_tech": None},
            {"name": "Тир 2", "cost": {"raw_a": 3}, "requires_tech": None},
            {"name": "Тир 3", "cost": {"raw_a": 6}, "requires_tech": None},
            {"name": "Тир 4", "cost": {"raw_a": 8}, "requires_tech": None},
            {"name": "Тир 5", "cost": {"raw_a": 10}, "requires_tech": None},
            {"name": "Тир 6", "cost": {"raw_a": 14}, "requires_tech": None},
        ],
    },
    # Инструмент ремонта с прочностью (см. BREAKDOWN_TYPES/BUILDINGS["flaky_typed"]
    # ниже) - тир 2 держит 2 починки (max_durability=2), затем требует своего
    # платного ремонта (repair_cost) - для тестов tool_is_broken/repair_tool/
    # repairable_count.
    "wrench": {
        "name": "Гаечный ключ",
        "tiers": [
            {"name": "Без ключа", "cost": None, "requires_tech": None},
            {
                "name": "Гаечный ключ", "cost": {"raw_a": 2}, "requires_tech": None,
                "max_durability": 2, "repair_cost": {"raw_a": 1},
            },
        ],
    },
    # Оружие (weapon=true) - для тестов _weapon_bonuses/attack_encounter. Тир 2
    # несёт все 4 боевых поля разом: damage_multiplier/instant_kill_max_hp/
    # loot_multiplier/soul_chance_multiplier (instant_kill_max_hp=5 покрывает
    # test_mob (hp=10) только вместе с уроном, но НЕ test_boss (hp=50) - см.
    # тесты на мгновенное убийство).
    "sword": {
        "name": "Меч", "weapon": True, "enchant_kind": "weapon",
        "tiers": [
            {"name": "Без меча", "cost": None, "requires_tech": None},
            # damage_multiplier=2: при уровне навыка 1 (damage=3) даёт урон 6 -
            # НЕ хватает на remaining_hp=8, поэтому instant_kill_max_hp=8 в тестах
            # проверяется ИЗОЛИРОВАННО от обычного расчёта урона (см.
            # test_weapon_instant_kill_bypasses_damage_calc).
            {
                "name": "Меч", "cost": {"raw_a": 5}, "requires_tech": None,
                "damage_multiplier": 2, "instant_kill_max_hp": 8,
                "loot_multiplier": 2, "soul_chance_multiplier": 2,
            },
            # Тир 3 - только для тестов unlocked_attack_moves/attack_encounter(move_id=...):
            # unlocks_moves кумулятивен (см. CLAUDE.md), поэтому тир 3 держит меч
            # тира 2 "живым" боевым тиром (instant_kill_max_hp и т.п. этого тира
            # больше НЕ действуют - _weapon_bonuses смотрит только ТЕКУЩИЙ тир),
            # но добавляет доступ к приёму "power_strike".
            {
                "name": "Меч+", "cost": {"raw_a": 8}, "requires_tech": None,
                "damage_multiplier": 3,
                "unlocks_moves": ["power_strike", "pct_max_move", "pct_remaining_move", "execute_move"],
            },
        ],
    },
    # Броня (armor=true) - зеркало sword, для тестов _armor_bonuses/player_max_hp/
    # attack_encounter (снижение входящего урона). Тир 2 даёт и max_hp_bonus, и
    # damage_reduction разом - большинству тестов достаточно одного тира.
    "armor": {
        "name": "Броня", "armor": True, "enchant_kind": "armor",
        "tiers": [
            {"name": "Без брони", "cost": None, "requires_tech": None},
            {
                "name": "Броня", "cost": {"raw_a": 5}, "requires_tech": None,
                "max_hp_bonus": 10, "damage_reduction": 2,
            },
        ],
    },
}

# Зачарования (content.json["enchantments"]) - см. game/engine.py:enchantment_pool/
# enchant_tool/_enchant_effect_value. НИ у одного инструмента фикстуры (pickaxe/
# sword/armor/wrench/drill/ore_scanner выше) НЕТ enchant_kind - значит для ВСЕХ
# существующих тестов enchantment_pool(...) пуст и _enchant_effect_value(...)
# всегда возвращает нейтральный default, т.е. это ЦЕЛИКОМ инертное дополнение,
# пока конкретный тест сам не патчит TOOLS[...]["enchant_kind"] через
# mock.patch.dict (см. tests/test_enchanting.py) - тот же приём точечного патча,
# что и у "качества жил" в MiningTests (см. докстринг модуля выше).
ENCHANTMENTS = {
    "test_luck": {
        "name": "Тестовая удача", "description": "Множит выход руды.",
        "applies_to_kind": "mining",
        "effect": {"type": "yield_multiplier"},
        "levels": [{"value": 2.0}, {"value": 3.0}],
    },
    "test_durability": {
        "name": "Тестовая прочность", "description": "Универсальное - множит max_durability.",
        "applies_to_kind": None,
        "effect": {"type": "durability_multiplier"},
        "levels": [{"value": 2.0}],
    },
    "test_sharpness": {
        "name": "Тестовая острота", "description": "Добавка к урону оружия.",
        "applies_to_kind": "weapon",
        "effect": {"type": "damage_bonus"},
        "levels": [{"value": 0.5}],
    },
    "test_looting": {
        "name": "Тестовая добыча", "description": "Множит шанс дропа/души.",
        "applies_to_kind": "weapon",
        "effect": {"type": "drop_chance_multiplier"},
        "levels": [{"value": 2.0}],
    },
    # Отдельное от test_looting зачарование, ТОЖЕ множащее soul_chance -
    # для теста "стакается с Добычей перемножением" (см. game/engine.py:_weapon_bonuses).
    "test_necromancy": {
        "name": "Тестовая некромантия", "description": "Дополнительно множит ТОЛЬКО шанс души.",
        "applies_to_kind": "weapon",
        "effect": {"type": "soul_chance_multiplier"},
        "levels": [{"value": 1.5}],
    },
    "test_protection": {
        "name": "Тестовая защита", "description": "Добавка к снижению урона брони.",
        "applies_to_kind": "armor",
        "effect": {"type": "armor_defense_bonus"},
        "levels": [{"value": 5}],
    },
    "test_vitality": {
        "name": "Тестовая живучесть", "description": "Добавка к максимуму HP.",
        "applies_to_kind": "armor",
        "effect": {"type": "max_hp_bonus_add"},
        "levels": [{"value": 7}],
    },
}

# Приёмы боя (content.json["combat_moves"]) - для тестов unlocked_attack_moves/
# attack_encounter(move_id=...)/advance_turn-тика кулдауна и всех 4 режимов расчёта
# урона (см. game/engine.py:_move_damage). power_strike - mode по умолчанию
# ("multiplier", не указан явно - тест на обратную совместимость со старым контентом
# без поля mode вовсе); остальные три - по одному на каждый ДРУГОЙ режим.
COMBAT_MOVES = {
    "power_strike": {
        "name": "Тестовый приём", "description": "Тестовый мощный удар.",
        "damage_multiplier": 2, "cooldown_turns": 2,
    },
    "pct_max_move": {
        "name": "Тестовый % от макс. HP", "description": "Тестовый приём.",
        "mode": "percent_max_hp", "percent": 0.2, "cooldown_turns": 0,
    },
    "pct_remaining_move": {
        "name": "Тестовый % от остатка HP", "description": "Тестовый приём.",
        "mode": "percent_remaining_hp", "percent": 0.5, "cooldown_turns": 0,
    },
    "execute_move": {
        "name": "Тестовый добивающий удар", "description": "Тестовый приём.",
        "mode": "execute", "execute_threshold_percent": 0.2, "cooldown_turns": 0,
    },
}

# Мобы/боссы (см. LOCATIONS["hunting_loc"]) - test_mob обычный (soul_id/soul_chance
# гарантирован, chance=1.0, чтобы тесты души не зависели от random.seed), test_boss -
# босс (summon_cost, без soul_id - души боссов только крафтом, см. CLAUDE.md).
MOBS = {
    "test_mob": {
        "name": "Тестовый моб", "description": "Тестовый обычный моб.",
        "location": "hunting_loc", "requires_tech": None, "is_boss": False,
        "rarity": 1, "hp": 10, "damage": 4, "xp_reward": 5,
        "loot": {"raw_a": 2}, "soul_id": "test_soul", "soul_chance": 1.0,
    },
    "test_boss": {
        "name": "Тестовый босс", "description": "Тестовый босс.",
        "location": "hunting_loc", "requires_tech": None, "is_boss": True,
        "hp": 50, "damage": 15, "xp_reward": 20,
        "loot": {"raw_b": 3}, "summon_cost": {"raw_a": 5},
    },
}

# Боевой навык - тир 2 даёт урон 100 (заведомо смертельный для test_mob/test_boss
# одним ударом) для тестов, которым важно только "убит/не убит", а не подсчёт ударов.
SKILLS = {
    "combat": {
        "name": "Тестовый боевой навык", "description": "Тестовая линейка.",
        "levels": [
            {"xp_required": 0, "effects": {"damage": 3}},
            {"xp_required": 10, "effects": {"damage": 100, "max_hp": 15}},
        ],
    },
}

# Продукция синтезатора душ (BUILDINGS["soul_synth"]) - гарантированная часть
# (raw_a) и шансовая (raw_b, отдельно мокается в тестах через random.random).
SOUL_RECIPES = {
    "test_soul": {"produces": {"raw_a": 3, "raw_b": 1}, "produces_chance": {"raw_b": 0.5}},
    # Случайное количество produce (см. game/engine.py:_is_range_amount) - каждая
    # вставленная variable_soul бросает {"min":1,"max":6} НЕЗАВИСИМО от остальных.
    "variable_soul": {"produces": {"variable_output": {"min": 1, "max": 6}}},
}

# Рецепты ритуального круга (BUILDINGS["ritual_altar"], базовая ёмкость кольца - 2
# без апгрейдов). "ritual_basic" - мгновенный, ровно на базовую ёмкость. "ritual_slow" -
# многоходовой с ГАРАНТИРОВАННЫМ extra_consumes_per_turn. "ritual_chancy" - многоходовой
# с extra_consumes_chance (мокается в тестах через random.random). "ritual_locked" -
# требует tech1 (для lock_reason == "tech", ring_cost намеренно маленький, чтобы
# ёмкость кольца точно была ни при чём). "ritual_grand" - ring_cost БОЛЬШЕ базовой
# ёмкости кольца, без requires_tech (для lock_reason == "pedestals").
RITUAL_RECIPES = {
    "ritual_basic": {
        "name": "Простой ритуал", "description": "Тестовый мгновенный ритуал.",
        "requires_tech": None, "requires_building": "ritual_altar",
        "ring_cost": {"raw_a": 2}, "center_cost": "raw_b", "extra_cost": {},
        "duration_turns": 1, "extra_consumes_per_turn": {}, "extra_consumes_chance": {},
        "produces": {"mid": 1}, "produces_chance": {},
    },
    "ritual_slow": {
        "name": "Долгий ритуал", "description": "Тестовый многоходовой ритуал с гарантированным доп. расходом за ход.",
        "requires_tech": None, "requires_building": "ritual_altar",
        "ring_cost": {"raw_a": 1}, "center_cost": "raw_b", "extra_cost": {},
        "duration_turns": 2, "extra_consumes_per_turn": {"raw_a": 1}, "extra_consumes_chance": {},
        "produces": {"mid": 2}, "produces_chance": {},
    },
    "ritual_chancy": {
        "name": "Шансовый ритуал", "description": "Тестовый многоходовой ритуал с шансовым доп. расходом за ход.",
        "requires_tech": None, "requires_building": "ritual_altar",
        "ring_cost": {"raw_a": 1}, "center_cost": "raw_b", "extra_cost": {},
        "duration_turns": 2, "extra_consumes_per_turn": {"raw_b": 1}, "extra_consumes_chance": {"raw_b": 0.5},
        "produces": {"final": 1}, "produces_chance": {},
    },
    "ritual_locked": {
        "name": "Запертый ритуал", "description": "Тестовый ритуал за технологией tech1.",
        "requires_tech": "tech1", "requires_building": "ritual_altar",
        "ring_cost": {"raw_a": 1}, "center_cost": "raw_b", "extra_cost": {},
        "duration_turns": 1, "extra_consumes_per_turn": {}, "extra_consumes_chance": {},
        "produces": {"mid": 1}, "produces_chance": {},
    },
    "ritual_grand": {
        "name": "Большой ритуал", "description": "Тестовый ритуал с ring_cost больше базовой ёмкости кольца.",
        "requires_tech": None, "requires_building": "ritual_altar",
        "ring_cost": {"raw_a": 4}, "center_cost": "raw_b", "extra_cost": {},
        "duration_turns": 1, "extra_consumes_per_turn": {}, "extra_consumes_chance": {},
        "produces": {"final": 1}, "produces_chance": {},
    },
    # Мгновенный ритуал с шансовым produces (см. game/engine.py:_roll_chance_amounts) -
    # для тестов "produces_chance у мгновенного ритуала работает так же, как gain_chance
    # у ручного действия".
    "ritual_variable": {
        "name": "Шансовый результат", "description": "Тестовый мгновенный ритуал с шансовым produces.",
        "requires_tech": None, "requires_building": "ritual_altar",
        "ring_cost": {"raw_a": 1}, "center_cost": "raw_b", "extra_cost": {},
        "duration_turns": 1, "extra_consumes_per_turn": {}, "extra_consumes_chance": {},
        "produces": {"mid": 2}, "produces_chance": {"mid": 0.5},
    },
}

# Плавильня (tests/test_smeltery.py): одна и та же руда в «холодном» (жар 1, хуже выход)
# и «горячем» (жар 2) рецепте, металл B только в горне, рецепт за tech1; сплав AB и
# скрытый сплав (hidden - пропорция видна после первой пробы); литьё с остыванием и мгновенное.
MELT_RECIPES = {
    "melt_a_cold": {"name": "Руда в яме", "requires_tech": None, "heat": 1,
                    "input": {"smelt_ore": 2, "smelt_fuel": 1}, "output": {"molten_a": 1, "smelt_slag": 1}, "turns": 2},
    "melt_a_hot": {"name": "Руда в горне", "requires_tech": None, "heat": 2,
                   "input": {"smelt_ore": 2, "smelt_fuel": 1}, "output": {"molten_a": 2, "smelt_slag": 1}, "turns": 1},
    "melt_b": {"name": "Вторая руда", "requires_tech": None, "heat": 2,
               "input": {"smelt_ore_b": 1}, "output": {"molten_b": 1}, "turns": 1},
    "melt_locked": {"name": "Запертая плавка", "requires_tech": "tech1", "heat": 1,
                    "input": {"smelt_ore": 1}, "output": {"molten_a": 1}, "turns": 1},
}
ALLOY_RECIPES = {
    "alloy_ab": {"name": "Сплав AB", "requires_tech": None, "heat": 2,
                 "input": {"molten_a": 2, "molten_b": 1}, "output": {"molten_ab": 3}},
    "alloy_hidden": {"name": "Тайный сплав", "requires_tech": None, "heat": 2, "hidden": True,
                     "input": {"molten_a": 1, "molten_b": 1}, "output": {"molten_hidden": 2}},
}
CAST_RECIPES = {
    "cast_a": {"name": "Отливка из A", "requires_tech": None, "form": "cast_form", "metal": "molten_a",
               "amount": 2, "output": {"cast_product": 1}, "turns": 2},
    "cast_ab_instant": {"name": "Отливка из AB", "requires_tech": None, "form": "cast_form", "metal": "molten_ab",
                        "amount": 1, "output": {"cast_product": 2}, "turns": 1},
}

# Кейс с лутом (см. QUESTS ниже) - "prizes" - барабан лутбокса (веса, а не шансы) -
# выпадает РОВНО один приз, см. game/engine.py:_roll_loot_case_prize.
LOOT_CASES = {
    "test_case": {
        "name": "Тестовый кейс", "description": "Тестовый кейс с лутом.",
        "prizes": [
            {"resource": "raw_a", "amount": 5, "weight": 3},
            {"resource": "raw_b", "amount": 2, "weight": 1},
        ],
    },
}

# Таблица аварий реактора (BUILDINGS["test_reactor"]["reactor"]["accident_table"]) -
# тот же барабанный алгоритм, что LOOT_CASES выше (см. game/engine.py:
# _roll_reactor_accident), но result - хардкод-enum движка, не ресурс.
REACTOR_ACCIDENTS = {
    "test_meltdown": {
        "entries": [
            {"result": "destroy_one_fuel_cell", "weight": 3, "message": "Стержень оплавился."},
            {"result": "destroy_zone_grid", "weight": 1, "message": "Решётка зоны разрушена."},
        ],
    },
}

# Квестбук - граф (не тир/тех-разблокировка): "era" тут чисто визуальная
# группировка в колонки, "requires" - какие ДРУГИЕ квесты должны быть уже
# выполнены (см. game/engine.py:is_quest_available/check_quests). Разные
# condition-типы разнесены по отдельным квестам (не по одному большому И) - так
# тесты могут выполнить/проверить их независимо. quest_factory.requires =
# [test_quest] специально - даёт тестам цепочку для is_quest_available/graph-gating.
QUESTS = {
    "test_quest": {
        "name": "Тестовый квест", "description": "Построй что угодно.",
        "era": 1, "requires": [], "conditions": [{"type": "total_buildings_at_least", "amount": 1}],
        "reward_case": "test_case",
    },
    "quest_factory": {
        "name": "Квест: фабрика", "description": "Построй фабрику.",
        "era": 2, "requires": ["test_quest"],
        "conditions": [{"type": "building_count_at_least", "building": "factory", "amount": 1}],
        "reward_case": "test_case",
    },
    "quest_scholar": {
        "name": "Квест: учёный", "description": "Исследуй 2 технологии.",
        "era": 1, "requires": [], "conditions": [{"type": "tech_count_at_least", "amount": 2}],
        "reward_case": "test_case",
    },
    "quest_knows_tech1": {
        "name": "Квест: технология 1", "description": "Исследуй технологию 1.",
        "era": 1, "requires": [], "conditions": [{"type": "tech_researched", "tech": "tech1"}],
        "reward_case": "test_case",
    },
    "quest_all_done": {
        "name": "Квест: всё изучено", "description": "Исследуй все технологии.",
        "era": 1, "requires": [], "conditions": [{"type": "all_techs_researched"}],
        "reward_case": "test_case",
    },
    "quest_turn100": {
        "name": "Квест: век", "description": "Доживи до 100 хода.",
        "era": 1, "requires": [], "conditions": [{"type": "turn_at_least", "amount": 100}],
        "reward_case": "test_case",
    },
    "quest_maxpick": {
        "name": "Квест: максимальная кирка", "description": "Прокачай кирку до предела.",
        "era": 1, "requires": [],
        "conditions": [{"type": "tool_tier_at_least", "tool": "pickaxe", "amount": len(TOOLS["pickaxe"]["tiers"])}],
        "reward_case": "test_case",
    },
}


def as_content_dict() -> dict:
    """Тот же контент, но в форме content.json - для тестов валидатора
    (tools/content_validation.py), которым нужен сырой JSON-совместимый словарь,
    а не смонтированный в engine/web_ui."""
    return {
        "resources": copy.deepcopy(RESOURCES),
        "locations": copy.deepcopy(LOCATIONS),
        "manual_actions": copy.deepcopy(MANUAL_ACTIONS),
        "buildings": copy.deepcopy(BUILDINGS),
        "techs": copy.deepcopy(TECHS),
        "mining_info": copy.deepcopy(MINING_INFO),
        "mine_field": copy.deepcopy(MINE_FIELD),
        "tools": copy.deepcopy(TOOLS),
        "energy_recipes": copy.deepcopy(ENERGY_RECIPES),
        "species": copy.deepcopy(SPECIES),
        "genetic_traits": copy.deepcopy(GENETIC_TRAITS),
        "mutations": copy.deepcopy(MUTATIONS),
        "genetics": copy.deepcopy(GENETICS_SETTINGS),
        "combat": copy.deepcopy(COMBAT_SETTINGS),
        "time": copy.deepcopy(TIME),
        "mobs": copy.deepcopy(MOBS),
        "skills": copy.deepcopy(SKILLS),
        "soul_recipes": copy.deepcopy(SOUL_RECIPES),
        "ritual_recipes": copy.deepcopy(RITUAL_RECIPES),
        "melt_recipes": copy.deepcopy(MELT_RECIPES),
        "alloy_recipes": copy.deepcopy(ALLOY_RECIPES),
        "cast_recipes": copy.deepcopy(CAST_RECIPES),
        "loot_cases": copy.deepcopy(LOOT_CASES),
        "quests": copy.deepcopy(QUESTS),
        "combat_moves": copy.deepcopy(COMBAT_MOVES),
        "breakdown_types": copy.deepcopy(BREAKDOWN_TYPES),
        "enchantments": copy.deepcopy(ENCHANTMENTS),
        "reactor_accidents": copy.deepcopy(REACTOR_ACCIDENTS),
    }


def build_energy_recipe_for_resource(energy_recipes: dict) -> dict[str, str]:
    recipe: dict[str, str] = {}
    for recipe_id, r in energy_recipes.items():
        for resource_id in r.get("produces", {}):
            recipe.setdefault(resource_id, recipe_id)
    return recipe


def build_mutations_by_pair(mutations: list) -> dict:
    """Как ENERGY_RECIPE_FOR_RESOURCE - производный от MUTATIONS индекс, который
    game/engine.py считает ОДИН РАЗ при импорте (MUTATIONS_BY_PAIR) и потому не
    подхватил бы patched MUTATIONS сам по себе - приходится пересчитывать и
    патчить этот индекс отдельно, тем же приёмом."""
    by_pair: dict = {}
    for m in mutations:
        key = frozenset({m["parent_a"], m["parent_b"]})
        by_pair.setdefault(key, []).append(m)
    return by_pair


_ENGINE_NAMES = ["BUILDINGS", "ENERGY_RECIPES", "GENETIC_TRAITS", "GENETICS_SETTINGS", "COMBAT_SETTINGS",
                 "LOCATIONS", "MANUAL_ACTIONS", "MINING_INFO", "MINE_FIELD", "MUTATIONS", "SPECIES", "TOOLS",
                 "RESOURCE_NAMES", "TECHS", "TIME", "ENERGY_RECIPE_FOR_RESOURCE", "MUTATIONS_BY_PAIR",
                 "RESOURCE_FRACTIONAL", "RESOURCE_STORAGE", "RESOURCE_REPLICATION_COST", "RESOURCE_SOUL_KIND", "RESOURCE_REACTOR_COMPONENT",
                 "RESOURCE_REACTOR_CATALYST", "RESOURCE_REACTOR_COOLANT_FLUID", "RESOURCE_MOLTEN",
                 "REACTOR_ACCIDENTS", "MOBS", "SKILLS", "SOUL_RECIPES",
                 "RITUAL_RECIPES", "MELT_RECIPES", "ALLOY_RECIPES", "CAST_RECIPES",
                 "LOOT_CASES", "QUESTS", "COMBAT_MOVES", "BREAKDOWN_TYPES", "ENCHANTMENTS"]
_WEB_UI_NAMES = ["BUILDINGS", "ENERGY_RECIPES", "GENETIC_TRAITS", "LOCATIONS",
                 "MANUAL_ACTIONS", "MINING_INFO", "MINE_FIELD", "TOOLS", "RESOURCE_ICONS", "RESOURCE_NAMES",
                 "RESOURCE_ORDER", "TECHS", "RESOURCE_STORAGE", "MOBS", "SKILLS", "SOUL_RECIPES", "RITUAL_RECIPES",
                 "MELT_RECIPES", "ALLOY_RECIPES", "CAST_RECIPES", "RESOURCE_MOLTEN",
                 "LOOT_CASES", "QUESTS", "BREAKDOWN_TYPES", "ENCHANTMENTS", "SPECIES", "MUTATIONS"]


def _fixture_values() -> dict:
    return {
        "BUILDINGS": BUILDINGS,
        "ENERGY_RECIPES": ENERGY_RECIPES,
        "GENETIC_TRAITS": GENETIC_TRAITS,
        "GENETICS_SETTINGS": GENETICS_SETTINGS,
        "COMBAT_SETTINGS": COMBAT_SETTINGS,
        "LOCATIONS": LOCATIONS,
        "MANUAL_ACTIONS": MANUAL_ACTIONS,
        "MINING_INFO": MINING_INFO,
        "MINE_FIELD": MINE_FIELD,
        "MUTATIONS": MUTATIONS,
        "SPECIES": SPECIES,
        "TOOLS": TOOLS,
        "RESOURCE_ICONS": RESOURCE_ICONS,
        "RESOURCE_NAMES": RESOURCE_NAMES,
        "RESOURCE_ORDER": RESOURCE_ORDER,
        "TECHS": TECHS,
        "TIME": TIME,
        "ENERGY_RECIPE_FOR_RESOURCE": build_energy_recipe_for_resource(ENERGY_RECIPES),
        "MUTATIONS_BY_PAIR": build_mutations_by_pair(MUTATIONS),
        "RESOURCE_FRACTIONAL": RESOURCE_FRACTIONAL,
        "RESOURCE_STORAGE": RESOURCE_STORAGE,
        "RESOURCE_REPLICATION_COST": RESOURCE_REPLICATION_COST,
        "RESOURCE_SOUL_KIND": RESOURCE_SOUL_KIND,
        "RESOURCE_REACTOR_COMPONENT": RESOURCE_REACTOR_COMPONENT,
        "RESOURCE_REACTOR_CATALYST": RESOURCE_REACTOR_CATALYST,
        "RESOURCE_REACTOR_COOLANT_FLUID": RESOURCE_REACTOR_COOLANT_FLUID,
        "REACTOR_ACCIDENTS": REACTOR_ACCIDENTS,
        "MOBS": MOBS,
        "SKILLS": SKILLS,
        "SOUL_RECIPES": SOUL_RECIPES,
        "RITUAL_RECIPES": RITUAL_RECIPES,
        "MELT_RECIPES": MELT_RECIPES,
        "ALLOY_RECIPES": ALLOY_RECIPES,
        "CAST_RECIPES": CAST_RECIPES,
        "RESOURCE_MOLTEN": RESOURCE_MOLTEN,
        "LOOT_CASES": LOOT_CASES,
        "QUESTS": QUESTS,
        "COMBAT_MOVES": COMBAT_MOVES,
        "BREAKDOWN_TYPES": BREAKDOWN_TYPES,
        "ENCHANTMENTS": ENCHANTMENTS,
    }


@contextlib.contextmanager
def _patched(module, names):
    values = _fixture_values()
    originals = {name: getattr(module, name) for name in names}
    for name in names:
        setattr(module, name, values[name])
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(module, name, value)


@contextlib.contextmanager
def patched_engine_content():
    """Подменяет game.engine на фикстурный контент выше на время блока."""
    from game import engine
    with _patched(engine, _ENGINE_NAMES):
        yield


@contextlib.contextmanager
def patched_content():
    """Как patched_engine_content, но заодно подменяет и tools/web_ui.py -
    он импортирует те же имена в свой собственный неймспейс, отдельно от engine."""
    from game import engine
    import web_ui  # tools/ уже должен быть в sys.path у вызывающего теста
    with _patched(engine, _ENGINE_NAMES), _patched(web_ui, _WEB_UI_NAMES):
        yield
