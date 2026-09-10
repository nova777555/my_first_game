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

import contextlib
import copy

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
    {"id": "test_soul", "name": "Тестовая душа"},
    # Вторая душа - с диапазоном produces (см. SOUL_RECIPES["variable_soul"]
    # ниже) - для теста "каждая вставленная душа бросает диапазон НЕЗАВИСИМО".
    {"id": "variable_soul", "name": "Переменная душа"},
]
RESOURCE_NAMES = {r["id"]: r["name"] for r in RESOURCES}
RESOURCE_ORDER = [r["id"] for r in RESOURCES]
RESOURCE_ICONS: dict = {}
RESOURCE_FRACTIONAL = {r["id"]: bool(r.get("fractional", False)) for r in RESOURCES}
RESOURCE_STORAGE = {r["id"]: r["storage"] for r in RESOURCES if r.get("storage")}

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
        "repair_cost": {"raw_a": 1}, "unlocks_autocraft": ["mid", "capacitor"], "compute_power": 15,
        "autocraft_speed": 50,  # с запасом выше любых fires в существующих тестах - они не про скорость
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
        "repair_cost": {"raw_a": 1}, "unlocks_autocraft": ["slow_mid", "slow_final", "other_mid"],
        "compute_power": 10000, "autocraft_speed": 2,
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
        "soul_collector": {"slots": 1, "accepts": ["test_soul", "variable_soul"]},
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
    # active_period, значения тут не свободные. bonus_resource у каждой аллели -
    # РАЗНЫЙ существующий ресурс фикстуры (для теста "бонус идёт именно в СВОЙ
    # ресурс, не путается между средами"), bonus_chance - произвольные разные
    # числа (сами тесты бонуса мокают random, а не полагаются на эти значения).
    "environment": {
        "name": "Тестовая среда обитания",
        "alleles": [
            {"id": "forest", "name": "Лес", "dominant": True, "weight": 5,
             "bonus_resource": "species_output", "bonus_amount": 1, "bonus_chance": 0.3},
            {"id": "fields", "name": "Поля", "dominant": False, "weight": 5,
             "bonus_resource": "critter_food", "bonus_amount": 1, "bonus_chance": 0.3},
            {"id": "caves", "name": "Пещеры", "dominant": False, "weight": 3,
             "bonus_resource": "raw_a", "bonus_amount": 1, "bonus_chance": 0.25},
            {"id": "desert", "name": "Пустыня", "dominant": False, "weight": 3,
             "bonus_resource": "raw_b", "bonus_amount": 1, "bonus_chance": 0.25},
            {"id": "water", "name": "Вода", "dominant": False, "weight": 2,
             "bonus_resource": "energy", "bonus_amount": 1, "bonus_chance": 0.2},
            {"id": "space", "name": "Космос", "dominant": False, "weight": 1,
             "bonus_resource": "rare_mineral", "bonus_amount": 1, "bonus_chance": 0.15},
        ],
    },
}

# Виды для разведения (фермы) - ключ = id уже существующего ресурса (см.
# RESOURCES выше), тот же принцип, что у MINING_INFO; это аллели признака "вид".
# critter_a - доминантный, ловится в диком виде (weight=5), без потребления.
# critter_b - рецессивный, weight=0 (НЕ ловится в диком виде - только через
# мутацию), требует critter_food (для теста "не хватает consumes - не
# производит, но и не умирает").
SPECIES = {
    "critter_a": {
        "description": "Тестовый вид А - доминантный, ловится в диком виде, без потребления.",
        "produces": {"species_output": 2}, "consumes": {}, "dominant": True, "weight": 5,
    },
    "critter_b": {
        "description": "Тестовый вид Б - рецессивный, требует корм, доступен только через мутацию.",
        "produces": {"species_output": 5}, "consumes": {"critter_food": 1}, "dominant": False, "weight": 0,
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
}

MINING_INFO = {
    "mineral": {
        "requires_tech": None, "required_tool": "pickaxe", "required_tier": 1,
        "rarity": 1, "vein_size_range": [20, 30], "base_yield": 4,
    },
    "rare_mineral": {
        "requires_tech": None, "required_tool": "pickaxe", "required_tier": 2,
        "rarity": 5, "vein_size_range": [1, 2], "base_yield": 5,
    },
}

TIME = {"hours_per_turn": 3, "day_start_hour": 6, "day_end_hour": 21}

TOOLS = {
    "pickaxe": {
        "name": "Кирка",
        "tiers": [
            {"name": "Тир 1", "cost": None, "requires_tech": None},
            {"name": "Тир 2", "cost": {"raw_a": 10}, "requires_tech": None},
            {"name": "Тир 3", "cost": {"raw_b": 10}, "requires_tech": "tech1"},
        ],
    },
    "drill": {
        "name": "Дрель",
        "tiers": [
            {"name": "Ручная дрель", "cost": None, "requires_tech": None},
            {"name": "Электродрель", "cost": {"raw_a": 5}, "requires_tech": None},
        ],
    },
    # Оружие (weapon=true) - для тестов _weapon_bonuses/attack_encounter. Тир 2
    # несёт все 4 боевых поля разом: damage_multiplier/instant_kill_max_hp/
    # loot_multiplier/soul_chance_multiplier (instant_kill_max_hp=5 покрывает
    # test_mob (hp=10) только вместе с уроном, но НЕ test_boss (hp=50) - см.
    # тесты на мгновенное убийство).
    "sword": {
        "name": "Меч", "weapon": True,
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
        ],
    },
}

# Мобы/боссы (см. LOCATIONS["hunting_loc"]) - test_mob обычный (soul_id/soul_chance
# гарантирован, chance=1.0, чтобы тесты души не зависели от random.seed), test_boss -
# босс (summon_cost, без soul_id - души боссов только крафтом, см. CLAUDE.md).
MOBS = {
    "test_mob": {
        "name": "Тестовый моб", "description": "Тестовый обычный моб.",
        "location": "hunting_loc", "requires_tech": None, "is_boss": False,
        "rarity": 1, "hp": 10, "xp_reward": 5,
        "loot": {"raw_a": 2}, "soul_id": "test_soul", "soul_chance": 1.0,
    },
    "test_boss": {
        "name": "Тестовый босс", "description": "Тестовый босс.",
        "location": "hunting_loc", "requires_tech": None, "is_boss": True,
        "hp": 50, "xp_reward": 20,
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
            {"xp_required": 10, "effects": {"damage": 100}},
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
        "tools": copy.deepcopy(TOOLS),
        "energy_recipes": copy.deepcopy(ENERGY_RECIPES),
        "species": copy.deepcopy(SPECIES),
        "genetic_traits": copy.deepcopy(GENETIC_TRAITS),
        "mutations": copy.deepcopy(MUTATIONS),
        "genetics": copy.deepcopy(GENETICS_SETTINGS),
        "time": copy.deepcopy(TIME),
        "mobs": copy.deepcopy(MOBS),
        "skills": copy.deepcopy(SKILLS),
        "soul_recipes": copy.deepcopy(SOUL_RECIPES),
        "loot_cases": copy.deepcopy(LOOT_CASES),
        "quests": copy.deepcopy(QUESTS),
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


_ENGINE_NAMES = ["BUILDINGS", "ENERGY_RECIPES", "GENETIC_TRAITS", "GENETICS_SETTINGS",
                 "LOCATIONS", "MANUAL_ACTIONS", "MINING_INFO", "MUTATIONS", "SPECIES", "TOOLS",
                 "RESOURCE_NAMES", "TECHS", "TIME", "ENERGY_RECIPE_FOR_RESOURCE", "MUTATIONS_BY_PAIR",
                 "RESOURCE_FRACTIONAL", "RESOURCE_STORAGE", "MOBS", "SKILLS", "SOUL_RECIPES",
                 "LOOT_CASES", "QUESTS"]
_WEB_UI_NAMES = ["BUILDINGS", "ENERGY_RECIPES", "GENETIC_TRAITS", "LOCATIONS",
                 "MANUAL_ACTIONS", "MINING_INFO", "TOOLS", "RESOURCE_ICONS", "RESOURCE_NAMES",
                 "RESOURCE_ORDER", "TECHS", "RESOURCE_STORAGE", "MOBS", "SKILLS", "SOUL_RECIPES",
                 "LOOT_CASES", "QUESTS"]


def _fixture_values() -> dict:
    return {
        "BUILDINGS": BUILDINGS,
        "ENERGY_RECIPES": ENERGY_RECIPES,
        "GENETIC_TRAITS": GENETIC_TRAITS,
        "GENETICS_SETTINGS": GENETICS_SETTINGS,
        "LOCATIONS": LOCATIONS,
        "MANUAL_ACTIONS": MANUAL_ACTIONS,
        "MINING_INFO": MINING_INFO,
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
        "MOBS": MOBS,
        "SKILLS": SKILLS,
        "SOUL_RECIPES": SOUL_RECIPES,
        "LOOT_CASES": LOOT_CASES,
        "QUESTS": QUESTS,
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
