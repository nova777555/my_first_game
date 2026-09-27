"""
Проверка целостности content.json: битые ссылки на технологии/постройки/ресурсы,
циклы в дереве технологий, форма пользовательских пиксельных иконок и т.п.

Используется и редактором контента (tools/content_editor.py) перед каждой записью
на диск, и автотестами (tests/) - чтобы правила проверки не разъезжались в двух местах.
"""
from __future__ import annotations

import re

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Имя файла кастомного звука ручного действия (manual_actions.*.sound) - см.
# tools/sounds/<action_id>.<ext> в web_ui.py/content_editor.py. Проверяется только
# форма значения (это самостоятельная запись файла на диске, не ссылка на другую
# сущность content.json) - существование файла этот модуль не проверяет (см. модуль-докстринг).
_SOUND_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(wav|mp3|ogg)$")

# Цвет темы эры квестбука (data["eras"][...]["color"]) и необязательный
# per-квестовый оверрайд фона/рамки узла графа (quests.*.bg/border) - строго
# '#rrggbb', тот же формат, что отдаёт HTML <input type="color">.
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Должен совпадать с game.engine.MINE_LOCATION_ID и const MINE_LOCATION_ID в
# tools/content_editor.html. Не импортируется отсюда напрямую: этот модуль намеренно
# не знает о game.* (валидирует произвольный словарь-кандидат, а не текущий диск),
# так что id держится в трёх местах руками - как и CONDITION_FIELDS ниже держится
# в паре с _condition_met в game/engine.py.
_MINE_LOCATION_ID = "shaft"

# Должен совпадать с game.engine.CONVERTER_LOCATION_ID и const CONVERTER_LOCATION_ID
# в tools/content_editor.html - тот же принцип ручного дублирования, что и у
# _MINE_LOCATION_ID выше (к этой локации привязан выбор рецепта крафта за энергию;
# вложена в обычную, ничем не защищённую локацию "mechanisms"/"Механизмы").
_CONVERTER_LOCATION_ID = "converter"

# Должен совпадать с game.engine.ENERGY_RESOURCE_ID и const ENERGY_RESOURCE_ID в
# tools/content_editor.html - тот же принцип ручного дублирования, что и у
# _MINE_LOCATION_ID выше. Без этого ресурса игра упадёт в первом же месте,
# которое к нему обращается (энергия - не просто ресурс, а спец-поведение в коде).
_ENERGY_RESOURCE_ID = "energy"

# Закрытый список id аллелей признака genetic_traits.environment - держать в паре
# с 6 апгрейдами типа environment_emulation (buildings.*.upgrades), с самими
# genetic_traits.environment.alleles и с ENVIRONMENT_ALLELE_IDS в
# tools/content_editor.html - тот же принцип ручного дублирования, что и у
# _MINE_LOCATION_ID выше. Используется и при валидации построек (упреждающе, до
# самого раздела genetic_traits), и при валидации genetic_traits - на уровне
# модуля, а не внутри validate(), чтобы порядок этих двух проверок был не важен.
_ENVIRONMENT_ALLELE_IDS = {"forest", "caves", "water", "space", "desert", "fields"}

# Закрытый список id исходов, которые умеет интерпретировать
# game/engine.py:_apply_reactor_accident_result - как и _ENVIRONMENT_ALLELE_IDS
# выше, result в reactor_accidents НЕ произвольная строка контента, а
# хардкод-enum, понятный только движку - держать в паре с этой функцией.
_REACTOR_ACCIDENT_RESULTS = {"destroy_one_fuel_cell", "destroy_zone_grid"}

# Тип условия квеста (data["quests"][...]["conditions"]) -> обязательные поля
# (кроме самого "type"). Держать в паре с _condition_met в game/engine.py -
# список типов общий.
CONDITION_FIELDS = {
    "resource_at_least": ["resource", "amount"],
    "building_count_at_least": ["building", "amount"],
    "total_buildings_at_least": ["amount"],
    "tech_researched": ["tech"],
    "tech_count_at_least": ["amount"],
    "all_techs_researched": [],
    "turn_at_least": ["amount"],
    "tool_tier_at_least": ["tool", "amount"],
    "species_discovered_at_least": ["amount"],
    "species_discovered": ["species"],
    "manual_action_performed_at_least": ["action", "amount"],
    "building_upgrade_purchased": ["building", "upgrade"],
    "storage_tier_at_least": ["resource", "amount"],
    "building_upgrade_level_at_least": ["building", "upgrade", "amount"],
    "building_upgrade_type_purchased_any": ["upgrade_type"],
}

# Типы улучшений построек (buildings.*.upgrades[].type) - держать в паре с веткой
# if/elif в check_buildings ниже (сообщение об ошибке там же перечисляет их текстом)
# и с UPGRADE_TYPE_OPTIONS в tools/content_editor.html. Используется для валидации
# quests.*.conditions[].upgrade_type (building_upgrade_type_purchased_any).
_BUILDING_UPGRADE_TYPES = {
    "scale", "energy_efficiency", "generation_efficiency", "breeding_output",
    "breeding_slots_bonus", "breeding_immortality", "breeding_mutation_chance",
    "energy_converter_capacity", "environment_emulation", "soul_collector_slots",
    "soul_generation_efficiency", "breakdown_immunity", "action_duration_reduction",
    "automation_slots", "research_speed", "ritual_pedestals", "reactor_zoning",
    "reactor_grid_size", "reactor_telemetry", "autocraft", "replicator_throughput",
    "smeltery_slots", "smeltery_capacity", "smeltery_heat",
}


def find_requires_cycle(entities: dict) -> list[str]:
    """Возвращает id сущностей, участвующих в цикле зависимостей requires (пусто,
    если циклов нет) - полностью общая (не завязана на техи конкретно): годится
    для любого словаря {id: {"requires": [id, ...]}}, используется и техами, и
    квестами (см. вызовы ниже)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in entities}
    cyclic: set[str] = set()

    def visit(tid: str, stack: list[str]) -> None:
        color[tid] = GRAY
        stack.append(tid)
        for req in entities.get(tid, {}).get("requires", []):
            if req not in entities:
                continue
            if color.get(req) == GRAY:
                idx = stack.index(req)
                cyclic.update(stack[idx:])
            elif color.get(req) == WHITE:
                visit(req, stack)
        stack.pop()
        color[tid] = BLACK

    for tid in entities:
        if color[tid] == WHITE:
            visit(tid, [])
    return sorted(cyclic)


def find_location_cycle(locations: dict) -> list[str]:
    """Как find_requires_cycle, но для дерева локаций (parent_location - ОДНА ссылка,
    не список, но алгоритм тот же обход "в глубину по цепочке предков")."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {lid: WHITE for lid in locations}
    cyclic: set[str] = set()

    def visit(lid: str, stack: list[str]) -> None:
        color[lid] = GRAY
        stack.append(lid)
        parent = locations.get(lid, {}).get("parent_location")
        if parent in locations:
            if color.get(parent) == GRAY:
                idx = stack.index(parent)
                cyclic.update(stack[idx:])
            elif color.get(parent) == WHITE:
                visit(parent, stack)
        stack.pop()
        color[lid] = BLACK

    for lid in locations:
        if color[lid] == WHITE:
            visit(lid, [])
    return sorted(cyclic)


def find_building_requires_cycle(buildings: dict) -> list[str]:
    """Как find_location_cycle, но для requires_building у построек (тоже ОДНА
    ссылка, не список) - без этой проверки A требует B, B требует A навсегда
    оставило бы обе постройки недостижимыми (ни одну нельзя было бы построить
    первой), и это не поймал бы простой "не сама на себя" (self-check уже есть
    отдельно, в цикле по buildings.items() при основной проверке полей)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {bid: WHITE for bid in buildings}
    cyclic: set[str] = set()

    def visit(bid: str, stack: list[str]) -> None:
        color[bid] = GRAY
        stack.append(bid)
        req = buildings.get(bid, {}).get("requires_building")
        if req in buildings:
            if color.get(req) == GRAY:
                idx = stack.index(req)
                cyclic.update(stack[idx:])
            elif color.get(req) == WHITE:
                visit(req, stack)
        stack.pop()
        color[bid] = BLACK

    for bid in buildings:
        if color[bid] == WHITE:
            visit(bid, [])
    return sorted(cyclic)


# Максимальная сторона крафт-сетки локации (locations.*.crafting). 9 - это
# "ужасающий верстак" 9x9, больше не предусмотрено ни дизайном, ни UI.
MAX_CRAFTING_SIDE = 9


def pattern_shape(pattern: dict) -> tuple[tuple[str | None, ...], ...]:
    """Нормализованная форма узора: сетка id ресурсов с ОБРЕЗАННЫМИ пустыми краями.

    Обрезка - то самое правило, из-за которого рецепт 2x2 ловится в любом углу
    сетки 3x3, а рецепт верстака работает и на ужасающем верстаке. Сравнивать
    два узора между собой (как на дубликаты, так и с тем, что разложил игрок)
    можно ТОЛЬКО через эту функцию, иначе один и тот же рисунок, сдвинутый на
    клетку, посчитается другим рецептом.

    Форма узора должна быть уже проверена (см. validate) - тут только приведение.
    """
    key = pattern.get("key") or {}
    grid = [[(None if ch == "." else key.get(ch)) for ch in row] for row in pattern.get("rows") or []]
    if not grid:
        return ()
    rows_used = [y for y, row in enumerate(grid) if any(c is not None for c in row)]
    cols_used = [x for x in range(max(len(r) for r in grid)) if any(x < len(r) and r[x] is not None for r in grid)]
    if not rows_used or not cols_used:
        return ()
    top, bottom = rows_used[0], rows_used[-1]
    left, right = cols_used[0], cols_used[-1]
    return tuple(
        tuple(grid[y][x] if x < len(grid[y]) else None for x in range(left, right + 1))
        for y in range(top, bottom + 1)
    )


def mirror_shape(shape: tuple[tuple[str | None, ...], ...]) -> tuple[tuple[str | None, ...], ...]:
    """Та же форма, отражённая по горизонтали - для pattern_mirror."""
    return tuple(tuple(reversed(row)) for row in shape)


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    try:
        resources = data["resources"]
        locations = data["locations"]
        manual_actions = data["manual_actions"]
        buildings = data["buildings"]
        techs = data["techs"]
        mining_info = data["mining_info"]
        tools = data["tools"]
        time_settings = data["time"]
    except KeyError as e:
        return [f"В данных отсутствует обязательный раздел: {e}"]

    ui_settings = data.get("ui", {})
    # Необязательный раздел (см. buildings.*.breakdown_types) - через data.get, а
    # не data[...], иначе старый content.json без этой фичи вовсе перестал бы
    # проходить валидацию (KeyError выше отклонил бы ВСЁ, а не только эту фичу).
    breakdown_types = data.get("breakdown_types", {})

    def check_click_delay(value, where: str) -> None:
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            errors.append(f"{where}: click_delay_ms должен быть null или неотрицательным числом")

    def check_sound(value, where: str) -> None:
        if value is not None and (not isinstance(value, str) or not _SOUND_FILENAME_RE.match(value)):
            errors.append(
                f"{where}: sound должен быть null или именем файла вида "
                "'<буквы/цифры/_-> .wav|.mp3|.ogg'"
            )

    def check_id(kind: str, value: str) -> None:
        if not isinstance(value, str) or not _ID_RE.match(value):
            errors.append(
                f"{kind} '{value}': id должен быть латиницей в snake_case "
                "(строчные буквы, цифры, подчёркивания, начинаться с буквы)"
            )

    def check_amount_range(d, where: str) -> None:
        """Случайное количество produce ({"min","max"}, равномерное распределение,
        см. game.engine._roll_range_amounts) - НЕЗАВИСИМО от cost_chance/gain_chance
        (шанс решает, случится ли начисление вообще, диапазон - сколько именно, если
        да). Разрешено только там, где check_resource_dict вызван с allow_range=True -
        то есть только в produce-стороне (buildings.produces/manual_actions.gain/
        energy_recipes.produces/soul_recipes.produces), никогда в cost/consumes."""
        if set(d.keys()) != {"min", "max"}:
            errors.append(f"{where}: диапазон должен содержать РОВНО ключи min и max")
            return
        lo, hi = d["min"], d["max"]
        if not isinstance(lo, (int, float)) or isinstance(lo, bool) or lo < 0:
            errors.append(f"{where}: min диапазона должен быть неотрицательным числом")
            return
        if not isinstance(hi, (int, float)) or isinstance(hi, bool) or hi < 0:
            errors.append(f"{where}: max диапазона должен быть неотрицательным числом")
            return
        if lo > hi:
            errors.append(f"{where}: min диапазона не может быть больше max")

    def check_resource_dict(d, where: str, allow_range: bool = False) -> None:
        if not isinstance(d, dict):
            errors.append(f"{where}: ожидался набор ресурс->количество")
            return
        for rid, amount in d.items():
            if rid not in resource_ids:
                errors.append(f"{where}: неизвестный ресурс '{rid}'")
            if allow_range and isinstance(amount, dict):
                check_amount_range(amount, f"{where}.'{rid}'")
                continue
            if not isinstance(amount, (int, float)) or amount < 0:
                errors.append(f"{where}: некорректное количество для '{rid}'")

    def check_chance_dict(d, base_dict, where: str) -> None:
        """cost_chance/gain_chance (см. game.engine.is_manual_action_deterministic) -
        необязательный словарь ресурс->шанс (0..1), КАЖДЫЙ ключ которого обязан уже
        быть в соответствующем cost/gain - шанс на ресурс, которого нет в самом
        рецепте, ничего не значит."""
        if d is None:
            return
        if not isinstance(d, dict):
            errors.append(f"{where}: должен быть объектом ресурс->шанс (0..1) или отсутствовать вовсе")
            return
        for rid, chance in d.items():
            if rid not in base_dict:
                errors.append(f"{where}: '{rid}' отсутствует в соответствующем cost/gain этого действия")
            if not isinstance(chance, (int, float)) or isinstance(chance, bool) or not (0 <= chance <= 1):
                errors.append(f"{where}: шанс для '{rid}' должен быть числом от 0 до 1")

    def is_stacks_location(loc_id) -> bool:
        crafting = locations.get(loc_id, {}).get("crafting") if loc_id in locations else None
        return isinstance(crafting, dict) and crafting.get("stacks") is True

    def check_stacks_recipe(label: str, entity: dict, costs: list, loc_id) -> None:
        """Рецепт сборщика (crafting.stacks): форма не важна, рецептом служит цена -
        узор лишний, а цена без энергии обязана быть непустой (энергию, как и в узоре,
        в ячейку не положить - разложить было бы нечего) и влезать в ячейки механизма
        (одна стопка - один ресурс). costs - [(где, cost)], у многоэтапной постройки
        по одной на этап."""
        if entity.get("pattern") is not None or entity.get("pattern_mirror"):
            errors.append(f"{label}: в сборщике форма не важна - узор (pattern) не нужен, рецептом служит сама цена")
        crafting = locations[loc_id]["crafting"]
        cells = crafting.get("width", 0) * crafting.get("height", 0) if all(
            isinstance(crafting.get(side), int) for side in ("width", "height")) else None
        for where, cost in costs:
            if not isinstance(cost, dict):
                continue
            stacks = [rid for rid in cost if rid != _ENERGY_RESOURCE_ID]
            if not stacks:
                errors.append(f"{where}: в сборщике рецепт узнаётся по стопкам цены, а кроме энергии в ней ничего нет")
            elif cells is not None and len(stacks) > cells:
                errors.append(
                    f"{where}: {len(stacks)} разных ресурсов не разложить в {cells} ячеек сборщика '{loc_id}' "
                    f"(одна ячейка - одна стопка одного ресурса)"
                )

    def check_action_pattern(aid: str, action: dict, loc_id) -> None:
        if is_stacks_location(loc_id):
            check_stacks_recipe(f"Действие '{aid}'", action, [(f"Действие '{aid}'.cost", action.get("cost") or {})], loc_id)
            return
        check_grid_pattern(f"Действие '{aid}'", action, loc_id, "cost")

    def check_grid_pattern(label: str, action: dict, loc_id, cost_field: str) -> None:
        """manual_actions.*.pattern (cost_field="cost") и energy_recipes.*.pattern
        (cost_field="consumes", loc_id - всегда преобразователь) - необязательный
        узор крафта на сетке механизма.

        НАЛИЧИЕ этого поля и есть переключатель «действие крафтится на сетке»:
        действие без pattern остаётся обычной карточкой в локации, даже если сама
        локация - механизм с сеткой (так плавка, которая тратит прочность молота,
        может жить рядом с сеточными рецептами, не требуя модалки). Форма:
        {"rows": ["AA.", "B..", "..."], "key": {"A": "ore", "B": "charcoal"}} -
        тот же приём "сетка символов + легенда", что у пиксельных иконок.

        Размер rows обязан совпадать с сеткой локации (locations.*.crafting):
        узор хранится в координатах своего механизма, а сдвиг по сетке обеспечивает
        уже обрезка краёв при сравнении (см. pattern_shape).
        """
        pattern = action.get("pattern")
        mirror = action.get("pattern_mirror", False)
        if not isinstance(mirror, bool):
            errors.append(f"{label}: pattern_mirror должен быть true или false")
        if pattern is None:
            if mirror:
                errors.append(f"{label}: pattern_mirror указан, но самого узора (pattern) нет")
            return
        where = f"{label}.pattern"
        if not isinstance(pattern, dict):
            errors.append(f"{where}: должен быть объектом {{rows, key}} или отсутствовать вовсе")
            return

        crafting = locations.get(loc_id, {}).get("crafting") if loc_id in locations else None
        if not isinstance(crafting, dict):
            errors.append(
                f"{where}: узор есть, а у локации '{loc_id}' нет крафт-сетки (crafting) - "
                f"либо задай сетку локации, либо убери узор"
            )
            crafting = None

        rows = pattern.get("rows")
        key = pattern.get("key")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, str) for r in rows):
            errors.append(f"{where}.rows: должен быть непустым списком строк")
            return
        if not isinstance(key, dict) or not key:
            errors.append(f"{where}.key: должен быть непустым объектом символ->ресурс")
            return

        widths = {len(r) for r in rows}
        if len(widths) != 1:
            errors.append(f"{where}.rows: все строки должны быть одной длины")
            return
        width = widths.pop()
        if crafting is not None:
            want_w, want_h = crafting.get("width"), crafting.get("height")
            if isinstance(want_w, int) and isinstance(want_h, int) and (width != want_w or len(rows) != want_h):
                errors.append(
                    f"{where}.rows: размер узора {width}x{len(rows)} не совпадает с сеткой "
                    f"локации '{loc_id}' ({want_w}x{want_h})"
                )

        for ch, rid in key.items():
            if not isinstance(ch, str) or len(ch) != 1 or ch == ".":
                errors.append(f"{where}.key: ключ '{ch}' должен быть ОДНИМ символом и не '.' (точка = пустая ячейка)")
                continue
            if rid not in resource_ids:
                errors.append(f"{where}.key['{ch}']: неизвестный ресурс '{rid}'")
            elif rid == _ENERGY_RESOURCE_ID:
                errors.append(
                    f"{where}.key['{ch}']: энергию нельзя положить в ячейку - "
                    f"укажи её в {cost_field} сверх узора, она спишется при крафте"
                )

        used: set[str] = set()
        for row in rows:
            for ch in row:
                if ch == ".":
                    continue
                if ch not in key:
                    errors.append(f"{where}.rows: символ '{ch}' не описан в key")
                else:
                    used.add(ch)
        unused = sorted(set(key) - used)
        if unused:
            errors.append(f"{where}.key: символы {', '.join(unused)} не встречаются в rows")
        if not used:
            errors.append(f"{where}: узор пуст - в сетке нет ни одной занятой ячейки")
            return

        # Одна ячейка = одна единица ресурса, поэтому cost сеточного действия -
        # это ровно подсчёт занятых ячеек. Два источника истины тут были бы
        # источником тихого расхождения: игрок видит узор, а движок списывает cost.
        # Единственное исключение - энергия: в ячейку её не положить, она идёт в
        # cost СВЕРХ узора и списывается при крафте (держать в паре с
        # content_editor.html: patternCostOf/itemCostOf).
        from_pattern: dict[str, int] = {}
        for row in rows:
            for ch in row:
                rid = key.get(ch)
                if rid:
                    from_pattern[rid] = from_pattern.get(rid, 0) + 1
        cost = action.get(cost_field) or {}
        if isinstance(cost, dict):
            cost = {rid: n for rid, n in cost.items() if rid != _ENERGY_RESOURCE_ID}
        if isinstance(cost, dict) and from_pattern != cost:
            shown = ", ".join(f"{rid}×{n}" for rid, n in sorted(from_pattern.items())) or "пусто"
            current = ", ".join(f"{rid}×{n}" for rid, n in sorted(cost.items())) or "пусто"
            errors.append(
                f"{where}: узор даёт цену {shown}, а в {cost_field} записано {current} - "
                f"одна ячейка это одна единица, они обязаны совпадать"
            )

    def check_tech_ref(tech_id, where: str) -> None:
        if tech_id is not None and tech_id not in techs:
            errors.append(f"{where}: неизвестная технология '{tech_id}'")

    def check_building_ref(building_id, where: str) -> None:
        if building_id is not None and building_id not in buildings:
            errors.append(f"{where}: неизвестная постройка '{building_id}'")

    def check_breakdown_types_ref(value, where: str) -> None:
        """buildings.*.breakdown_types - необязательный список id из
        breakdown_types (см. check_breakdown_types_section ниже). Пусто/
        отсутствует = поломка без типа (старое поведение)."""
        if value is None:
            return
        if not isinstance(value, list) or not value:
            errors.append(f"{where}: breakdown_types должен быть непустым списком id или отсутствовать вовсе")
            return
        for bt_id in value:
            if bt_id not in breakdown_types:
                errors.append(f"{where}: неизвестный вид поломки '{bt_id}'")

    def check_tool_tiers(requires_tool_tiers, where: str) -> None:
        if not isinstance(requires_tool_tiers, dict):
            errors.append(f"{where}: должен быть объектом инструмент -> список тиров")
            return
        for tool_id, tiers in requires_tool_tiers.items():
            if tool_id not in tools:
                errors.append(f"{where}: неизвестный инструмент '{tool_id}'")
                continue
            tier_count = len(tools[tool_id]["tiers"])
            if not isinstance(tiers, list) or not tiers:
                errors.append(f"{where}.{tool_id}: должен быть непустым списком тиров (или отсутствовать вовсе, если инструмент не ограничивает)")
                continue
            for t in tiers:
                if not isinstance(t, int) or not (1 <= t <= tier_count):
                    errors.append(f"{where}.{tool_id}: тир {t} вне диапазона 1..{tier_count}")

    def check_icon(icon, where: str) -> None:
        if icon is None:
            return
        if not isinstance(icon, dict) or "grid" not in icon or "palette" not in icon:
            errors.append(f"{where}.icon: должен быть объектом с полями grid и palette")
            return
        grid = icon["grid"]
        palette = icon["palette"]
        if not isinstance(grid, list) or not grid or not all(isinstance(row, str) for row in grid):
            errors.append(f"{where}.icon.grid: должен быть непустым списком строк")
            return
        width = len(grid[0])
        if width == 0 or any(len(row) != width for row in grid):
            errors.append(f"{where}.icon.grid: все строки должны быть одной (ненулевой) длины")
        if not isinstance(palette, dict):
            errors.append(f"{where}.icon.palette: должен быть словарём символ->цвет")
            return
        used_chars = {ch for row in grid for ch in row if ch != "."}
        missing = used_chars - set(palette.keys())
        if missing:
            errors.append(f"{where}.icon: символы {sorted(missing)} встречаются в grid, но нет в palette")

    # id общие на весь проект: постройка не может взять id, уже занятый ресурсом,
    # ручным действием или технологией (и наоборот) - иначе в рантайме или в GUI
    # они бы неотличимо путались при поиске "к чему относится этот id".
    id_owners: dict[str, list[str]] = {}

    def record_id(id_: str, category: str) -> None:
        id_owners.setdefault(id_, []).append(category)

    resource_ids: set[str] = set()
    for r in resources:
        rid = r.get("id", "")
        check_id("Ресурс", rid)
        if rid in resource_ids:
            errors.append(f"Повторяющийся id ресурса: {rid}")
        resource_ids.add(rid)
        record_id(rid, "ресурс")
        if not r.get("name"):
            errors.append(f"У ресурса '{rid}' не указано название")
        fractional = r.get("fractional", False)
        if not isinstance(fractional, bool):
            errors.append(f"Ресурс '{rid}': fractional должен быть true или false")
        soul_kind = r.get("soul_kind")
        if soul_kind is not None and (not isinstance(soul_kind, str) or not soul_kind):
            errors.append(f"Ресурс '{rid}': soul_kind должен быть null или непустой строкой (тег вида души, например 'моб'/'босс')")
        check_icon(r.get("icon"), f"Ресурс '{rid}'")
    if _ENERGY_RESOURCE_ID not in resource_ids:
        errors.append(f"Ресурс '{_ENERGY_RESOURCE_ID}' (Энергия) обязателен - с ним завязано особое поведение в движке")

    # Цена репликации (resources.*.replication_cost) - материи репликатора за ОДНУ
    # единицу ресурса, может быть дробной. Держать в паре с game/engine.py:
    # replication_matter_cost/replicate.
    replicator_matters = {
        (b.get("replicator") or {}).get("matter")
        for b in buildings.values() if isinstance(b, dict) and isinstance(b.get("replicator"), dict)
    } - {None}
    for r in resources:
        rid = r.get("id", "")
        price = r.get("replication_cost")
        if price is None:
            continue
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
            errors.append(f"Ресурс '{rid}': replication_cost должен быть положительным числом (сколько материи репликатора стоит 1 шт.) или отсутствовать")
        if rid == _ENERGY_RESOURCE_ID:
            errors.append(f"Ресурс '{rid}': энергию репликатор не делает - уберите replication_cost")
        if rid in replicator_matters:
            errors.append(f"Ресурс '{rid}': это материя репликатора - делать её из неё самой бессмысленно, уберите replication_cost")

    # Отдельным проходом ПОСЛЕ основного цикла по resources - check_resource_dict
    # (в storage.tiers[].cost) сверяется с resource_ids, а он заполняется как раз
    # ЭТИМ циклом; проверять cost ВНУТРИ него означало бы ложно ругаться на вполне
    # существующий ресурс, если он в списке ПОЗЖЕ текущего (resource_ids к этому
    # моменту ещё не содержит его id).
    for r in resources:
        rid = r.get("id", "")
        storage = r.get("storage")
        if storage is None:
            continue
        where = f"Ресурс '{rid}'.storage"
        if not isinstance(storage, dict):
            errors.append(f"{where}: должен быть объектом")
            continue
        base_capacity = storage.get("base_capacity")
        if not isinstance(base_capacity, int) or isinstance(base_capacity, bool) or base_capacity < 0:
            errors.append(f"{where}.base_capacity: должен быть неотрицательным целым числом")
        tiers = storage.get("tiers")
        if not isinstance(tiers, list):
            errors.append(f"{where}.tiers: должен быть списком тиров расширения (может быть пустым)")
            continue
        for i, tier in enumerate(tiers, start=1):
            twhere = f"{where}, тир {i}"
            if not isinstance(tier, dict):
                errors.append(f"{twhere}: должен быть объектом")
                continue
            infinite = tier.get("infinite", False)
            if not isinstance(infinite, bool):
                errors.append(f"{twhere}.infinite: должен быть true или false")
                infinite = False
            # infinite - тир, который вместо очередного capacity_add снимает
            # ограничение склада совсем (см. game/engine.py:storage_capacity) -
            # capacity_add ему тогда не нужен (в расчёте не участвует).
            capacity_add = tier.get("capacity_add")
            if not infinite and (not isinstance(capacity_add, int) or isinstance(capacity_add, bool) or capacity_add <= 0):
                errors.append(f"{twhere}.capacity_add: должен быть положительным целым числом")
            check_tech_ref(tier.get("requires_tech"), twhere)
            check_resource_dict(tier.get("cost") or {}, f"{twhere}.cost")

    # Тот же порядок причин, что у storage выше - spent_result сверяется с
    # resource_ids, который заполняется основным циклом по resources.
    _REACTOR_ROLES_FUEL_ONLY = {"heat", "output", "burnup_per_turn", "charge_max", "spent_result"}
    for r in resources:
        rid = r.get("id", "")
        component = r.get("reactor_component")
        if component is None:
            continue
        where = f"Ресурс '{rid}'.reactor_component"
        if not isinstance(component, dict):
            errors.append(f"{where}: должен быть объектом")
            continue
        role = component.get("role")
        if not isinstance(role, str) or not role:
            errors.append(f"{where}.role: должен быть непустой строкой (тег роли в решётке реактора, например 'fuel'/'moderator'/'coolant'/'absorber')")
        for numeric_field in ("heat", "output", "power", "burnup_per_turn", "charge_max"):
            fval = component.get(numeric_field)
            if fval is not None and (not isinstance(fval, (int, float)) or isinstance(fval, bool) or fval < 0):
                errors.append(f"{where}.{numeric_field}: должен быть null или неотрицательным числом")
        spent_result = component.get("spent_result")
        if role == "fuel":
            if not spent_result:
                errors.append(f"{where}: у топлива (role == 'fuel') обязан быть spent_result - иначе непонятно, что остаётся в ячейке после выгорания")
            elif spent_result not in resource_ids:
                errors.append(f"{where}.spent_result: ссылается на неизвестный ресурс '{spent_result}'")
        else:
            if spent_result is not None:
                errors.append(f"{where}: spent_result имеет смысл только у топлива (role == 'fuel')")
            if any(component.get(f) is not None for f in ("burnup_per_turn", "charge_max")):
                errors.append(f"{where}: burnup_per_turn/charge_max имеют смысл только у топлива (role == 'fuel')")

    # Вынесено выше (до manual_actions) - нужен уже там для проверки spawn_species.
    species = data.get("species", {})
    # Вынесено выше (до buildings) - нужен уже там для проверки buildings.*.breeding_accepts_family.
    species_families = {sp.get("family") for sp in species.values() if sp.get("family")}
    # Вынесено выше (до buildings) - нужен уже там для проверки buildings.*.soul_collector.accepts_kind.
    soul_recipes = data.get("soul_recipes", {})
    soul_kinds = {r.get("soul_kind") for r in resources if r.get("soul_kind")}
    # Вынесено выше (до buildings) - нужен уже там для проверки buildings.*.reactor.accepts_roles/accident_table.
    reactor_roles = {r.get("reactor_component", {}).get("role") for r in resources if r.get("reactor_component")}
    reactor_accident_tables = set(data.get("reactor_accidents", {}) or {})

    # Катализатор решётки реактора (resources.*.reactor_catalyst) - отдельный
    # от reactor_component тег: не занимает ячейку сам, а усиливает УЖЕ
    # стоящий в ней компонент (см. game/engine.py:_reactor_effective_component).
    _REACTOR_CATALYST_SELF_STATS = {"heat_output", "burnup", "power", "field"}
    for r in resources:
        rid = r.get("id", "")
        catalyst = r.get("reactor_catalyst")
        if catalyst is None:
            continue
        where = f"Ресурс '{rid}'.reactor_catalyst"
        if not isinstance(catalyst, dict):
            errors.append(f"{where}: должен быть объектом")
            continue
        applies_to = catalyst.get("applies_to")
        if not isinstance(applies_to, list) or not applies_to:
            errors.append(f"{where}.applies_to: должен быть непустым списком тегов ролей")
        else:
            for role in applies_to:
                if not isinstance(role, str) or not role:
                    errors.append(f"{where}.applies_to: содержит нестроковый/пустой элемент")
                elif role not in reactor_roles:
                    errors.append(f"{where}.applies_to: ссылается на тег роли '{role}', которого нет ни у одного ресурса-компонента реактора")
        self_stat = catalyst.get("self_stat")
        if self_stat not in _REACTOR_CATALYST_SELF_STATS:
            errors.append(f"{where}.self_stat: должен быть одним из {sorted(_REACTOR_CATALYST_SELF_STATS)}")
        elif self_stat == "burnup" and isinstance(applies_to, list) and applies_to and "fuel" not in applies_to:
            errors.append(f"{where}: self_stat 'burnup'/'heat_output'/'field' имеют смысл только у applies_to, включающего 'fuel'")
        elif self_stat in ("heat_output", "field") and isinstance(applies_to, list) and applies_to and "fuel" not in applies_to:
            errors.append(f"{where}: self_stat 'burnup'/'heat_output'/'field' имеют смысл только у applies_to, включающего 'fuel'")
        for pct_field in ("self_pct", "neighbor_malus_pct"):
            pval = catalyst.get(pct_field)
            if not isinstance(pval, (int, float)) or isinstance(pval, bool) or pval < 0:
                errors.append(f"{where}.{pct_field}: должен быть неотрицательным числом")
        # НЕОБЯЗАТЕЛЬНОЕ: насколько растёт ТЕПЛОВАЯ сторона катализатора, если она
        # должна отличаться от выходной. Отсутствие = обе стороны растут одинаково
        # (прежнее поведение). ОТРИЦАТЕЛЬНОЕ значение осмысленно и нужно: именно
        # оно позволяет катализатору ГАСИТЬ разогрев там, где он усиливает выход,
        # - без этого катализатор поля усиливал заведомо невыгодную сделку
        # (соседство топлива даёт больше тепла, чем выхода) и не окупался никогда.
        # Ниже -1 множитель ушёл бы в минус, то есть тепло стало бы отрицательным.
        if "self_pct_heat" in catalyst:
            hval = catalyst.get("self_pct_heat")
            if not isinstance(hval, (int, float)) or isinstance(hval, bool) or hval < -1:
                errors.append(f"{where}.self_pct_heat: должен быть числом не меньше -1 "
                              f"(-1 полностью гасит тепловую сторону, 0 оставляет её как есть)")

    # Охлаждающая жидкость реактора (resources.*.reactor_coolant_fluid) - РАСХОДНИК,
    # который теплоносители решётки тратят каждый ход. Не путать с ролью "coolant"
    # у reactor_component: та - железо в ячейке, эта - то, что в него залито.
    # Завести новую жидкость = добавить ресурс с этим тегом, код менять не нужно.
    for r in resources:
        rid = r.get("id", "")
        fluid = r.get("reactor_coolant_fluid")
        if fluid is None:
            continue
        where = f"Ресурс '{rid}'.reactor_coolant_fluid"
        if not isinstance(fluid, dict):
            errors.append(f"{where}: должен быть объектом")
            continue
        cooling = fluid.get("cooling_mult")
        if not isinstance(cooling, (int, float)) or isinstance(cooling, bool) or cooling <= 0:
            errors.append(f"{where}.cooling_mult: должен быть положительным числом (множитель снимаемого тепла)")
        upp = fluid.get("units_per_power")
        if not isinstance(upp, (int, float)) or isinstance(upp, bool) or upp < 0:
            errors.append(f"{where}.units_per_power: должен быть неотрицательным числом (расход единиц на единицу мощности теплоносителя при потоке 100%)")

    # Жидкий металл плавильни (resources.*.molten) - живёт только в тигле; color - цвет
    # слоя, density - порядок слоёв (тяжёлый тонет). См. game/content.py:RESOURCE_MOLTEN.
    molten_ids = set()
    for r in resources:
        molten = r.get("molten")
        if molten is None:
            continue
        where = f"Ресурс '{r.get('id', '')}'.molten"
        molten_ids.add(r.get("id"))
        if not isinstance(molten, dict):
            errors.append(f"{where}: должен быть объектом {{color, density}}")
            continue
        color = molten.get("color")
        if not isinstance(color, str) or not _HEX_COLOR_RE.match(color):
            errors.append(f"{where}.color: должен быть цветом вида '#rrggbb'")
        density = molten.get("density", 0)
        if not isinstance(density, (int, float)) or isinstance(density, bool):
            errors.append(f"{where}.density: должен быть числом (или отсутствовать)")

    for lid, loc in locations.items():
        record_id(lid, "локация")
        check_id("Локация", lid)
        check_tech_ref(loc.get("requires_tech"), f"Локация '{lid}'")
        check_building_ref(loc.get("requires_building"), f"Локация '{lid}'.requires_building")
        parent = loc.get("parent_location")
        if parent is not None:
            if parent not in locations:
                errors.append(f"Локация '{lid}': неизвестная родительская локация '{parent}'")
            elif parent == lid:
                errors.append(f"Локация '{lid}': не может быть родителем самой себе")
        if not loc.get("name"):
            errors.append(f"Локация '{lid}': не указано название")
        check_icon(loc.get("icon"), f"Локация '{lid}'")
        crafting = loc.get("crafting")
        if crafting is not None:
            where = f"Локация '{lid}'.crafting"
            if not isinstance(crafting, dict):
                errors.append(f"{where}: должен быть объектом {{width, height}} или отсутствовать вовсе")
            else:
                for side in ("width", "height"):
                    value = crafting.get(side)
                    if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= MAX_CRAFTING_SIDE):
                        errors.append(f"{where}.{side}: должен быть целым числом от 1 до {MAX_CRAFTING_SIDE}")
                # batch - крафт пачкой ("Скрафтить ×N"), необязательный флаг.
                if "batch" in crafting and not isinstance(crafting["batch"], bool):
                    errors.append(f"{where}.batch: должен быть true/false или отсутствовать")
                # stacks - сборщик: форма не важна, в ячейке стопка (ресурс + количество),
                # рецептом служит сама цена (game/engine.py: match_grid_target).
                if "stacks" in crafting and not isinstance(crafting["stacks"], bool):
                    errors.append(f"{where}.stacks: должен быть true/false или отсутствовать")
                elif crafting.get("stacks") and lid == _CONVERTER_LOCATION_ID:
                    errors.append(f"{where}.stacks: у преобразователя рецепты собираются только узором")
        # shows_building_mechanisms - в этой локации (обычно «Механизмы») появляются
        # кнопки открытия построек-механизмов (автоматизатор, синтезатор душ,
        # ритуальный круг, реактор), см. web_ui.html: buildingMechanismsAt.
        if "shows_building_mechanisms" in loc and not isinstance(loc["shows_building_mechanisms"], bool):
            errors.append(f"Локация '{lid}'.shows_building_mechanisms: должен быть true/false или отсутствовать")
    if _MINE_LOCATION_ID not in locations:
        errors.append(f"Локация '{_MINE_LOCATION_ID}' (Шахта) обязательна - к ней привязан поиск и добыча жил")
    if _CONVERTER_LOCATION_ID not in locations:
        errors.append(f"Локация '{_CONVERTER_LOCATION_ID}' (Преобразователь) обязательна - в ней выбирается рецепт крафта за энергию")

    loc_cyclic = find_location_cycle(locations)
    if loc_cyclic:
        names = ", ".join(f"'{locations[l]['name']}'" for l in loc_cyclic if l in locations)
        errors.append(f"Цикл в дереве локаций (родитель ссылается сам на себя через цепочку): {names}")

    for aid, a in manual_actions.items():
        record_id(aid, "ручное действие")
        check_id("Ручное действие", aid)
        check_tech_ref(a.get("requires_tech"), f"Действие '{aid}'")
        check_building_ref(a.get("requires_building"), f"Действие '{aid}'")
        check_resource_dict(a.get("cost", {}), f"Действие '{aid}'.cost")
        check_resource_dict(a.get("gain", {}), f"Действие '{aid}'.gain", allow_range=True)
        check_chance_dict(a.get("cost_chance"), a.get("cost", {}), f"Действие '{aid}'.cost_chance")
        check_chance_dict(a.get("gain_chance"), a.get("gain", {}), f"Действие '{aid}'.gain_chance")
        spawn_species = a.get("spawn_species")
        if spawn_species is not None and spawn_species not in species:
            errors.append(f"Действие '{aid}'.spawn_species: неизвестный вид '{spawn_species}'")
        if not a.get("gain") and not spawn_species:
            errors.append(f"Действие '{aid}': gain пустой и spawn_species не указан - действие ничего не производит")
        if not a.get("name"):
            errors.append(f"Действие '{aid}': не указано название")
        check_click_delay(a.get("click_delay_ms"), f"Действие '{aid}'")
        check_sound(a.get("sound"), f"Действие '{aid}'")
        loc_id = a.get("location")
        if loc_id is not None and loc_id not in locations:
            errors.append(f"Действие '{aid}': неизвестная локация '{loc_id}'")
        check_tool_tiers(a.get("requires_tool_tiers", {}), f"Действие '{aid}'.requires_tool_tiers")
        tool_scaling = a.get("tool_scaling")
        if tool_scaling is not None:
            if not isinstance(tool_scaling, dict) or "tool" not in tool_scaling:
                errors.append(f"Действие '{aid}'.tool_scaling: должен быть объектом с полем tool")
            elif tool_scaling["tool"] not in tools:
                errors.append(f"Действие '{aid}'.tool_scaling: неизвестный инструмент '{tool_scaling['tool']}'")
        uses_tool_durability = a.get("uses_tool_durability")
        if uses_tool_durability is not None:
            if not isinstance(uses_tool_durability, dict) or "tool" not in uses_tool_durability:
                errors.append(f"Действие '{aid}'.uses_tool_durability: должен быть объектом с полем tool")
            elif uses_tool_durability["tool"] not in tools:
                errors.append(f"Действие '{aid}'.uses_tool_durability: неизвестный инструмент '{uses_tool_durability['tool']}'")
        priority = a.get("priority", 0)
        if not isinstance(priority, int):
            errors.append(f"Действие '{aid}': priority должен быть целым числом")
        duration_turns = a.get("duration_turns")
        if duration_turns is not None and (not isinstance(duration_turns, int) or duration_turns < 2):
            errors.append(f"Действие '{aid}': duration_turns должен быть null или целым числом >= 2")
        excluded_from_autocraft = a.get("excluded_from_autocraft", False)
        if not isinstance(excluded_from_autocraft, bool):
            errors.append(f"Действие '{aid}': excluded_from_autocraft должен быть true или false")
        check_action_pattern(aid, a, loc_id)

    # Дубликаты форм внутри ОДНОЙ локации-механизма: два рецепта с одинаковым
    # рисунком неразличимы для игрока (движок не смог бы выбрать между ними), а с
    # учётом pattern_mirror форма считается занятой и своим отражением.
    # Энергорецепты преобразователя (energy_recipes.*.pattern) делят ту же таблицу
    # форм с ручными действиями локации преобразователя - см. их цикл ниже.
    shapes_by_location: dict[str, dict[tuple, str]] = {}

    def register_pattern_shape(label: str, entity: dict, loc_id) -> None:
        pattern = entity.get("pattern")
        if not isinstance(pattern, dict) or loc_id not in locations:
            return
        shape = pattern_shape(pattern)
        if not shape:
            return
        taken = shapes_by_location.setdefault(loc_id, {})
        variants = [shape]
        if entity.get("pattern_mirror"):
            variants.append(mirror_shape(shape))
        for variant in variants:
            other = taken.get(variant)
            if other is not None and other != label:
                errors.append(
                    f"{label}.pattern: та же форма узора уже занята ({other}) "
                    f"в локации '{loc_id}' - два рецепта одного механизма не могут выглядеть одинаково"
                )
                break
        for variant in variants:
            taken.setdefault(variant, label)

    # То же для сборщика: форма там не важна, рецепт узнаётся по набору стопок (цена
    # без энергии, game/engine.py: _stacks_match) - одинаковые цены не различить.
    stacks_by_location: dict[str, dict[tuple, str]] = {}

    def register_stacks(label: str, loc_id, cost) -> None:
        if not isinstance(cost, dict):
            return
        items = [(rid, n) for rid, n in cost.items() if rid != _ENERGY_RESOURCE_ID]
        if not items or not all(isinstance(n, (int, float)) and not isinstance(n, bool) for _, n in items):
            return
        need = tuple(sorted(items))
        taken = stacks_by_location.setdefault(loc_id, {})
        other = taken.get(need)
        if other is not None and other != label:
            errors.append(
                f"{label}: тот же набор стопок уже занят ({other}) в сборщике '{loc_id}' - "
                f"два рецепта с одинаковой ценой игроку не различить"
            )
            return
        taken[need] = label

    for aid, a in manual_actions.items():
        register_pattern_shape(f"Действие '{aid}'", a, a.get("location"))
        if is_stacks_location(a.get("location")):
            register_stacks(f"Действие '{aid}'", a.get("location"), a.get("cost") or {})

    # Где собирается постройка (buildings.*.location) - механизм с крафт-сеткой: на
    # верстаке узор (pattern - слой над cost, как у ручного действия, та же таблица
    # форм), в сборщике стопки. Без location постройку в игре не собрать нигде -
    # это не ошибка (редактор предупреждает), но узор без location бессмыслен.
    for bid, b in buildings.items():
        label = f"Постройка '{bid}'"
        loc_id = b.get("location")
        if loc_id is None:
            if b.get("pattern") is not None:
                errors.append(f"{label}.pattern: узор есть, а где собирается постройка (location) не указано")
            continue
        if loc_id not in locations:
            errors.append(f"{label}.location: неизвестная локация '{loc_id}'")
            continue
        if not isinstance(locations[loc_id].get("crafting"), dict):
            errors.append(f"{label}.location: у локации '{loc_id}' нет крафт-сетки (crafting) - собрать постройку там негде")
            continue
        stages = b.get("construction_stages")
        stage_costs = [
            (f"{label}.construction_stages[{i}].cost", s.get("cost") or {})
            for i, s in enumerate(stages) if isinstance(s, dict)
        ] if isinstance(stages, list) and stages else []
        if is_stacks_location(loc_id):
            costs = stage_costs or [(f"{label}.cost", b.get("cost") or {})]
            check_stacks_recipe(label, b, costs, loc_id)
            for _, cost in costs:
                register_stacks(label, loc_id, cost)
            continue
        if stage_costs:
            errors.append(
                f"{label}: многоэтапную постройку собирают только в сборщике (crafting.stacks) - "
                f"один узор не повторит цену каждого этапа"
            )
        if b.get("pattern") is None:
            errors.append(
                f"{label}: у механизма '{loc_id}' рецепты собираются узором, а у постройки узора (pattern) нет - "
                f"собрать её будет нельзя"
            )
        check_grid_pattern(label, b, loc_id, "cost")
        register_pattern_shape(label, b, loc_id)

    for bid, b in buildings.items():
        record_id(bid, "постройка")
        check_id("Постройка", bid)
        check_tech_ref(b.get("requires_tech"), f"Постройка '{bid}'")
        check_resource_dict(b.get("cost", {}), f"Постройка '{bid}'.cost")
        check_resource_dict(b.get("produces", {}), f"Постройка '{bid}'.produces", allow_range=True)
        check_resource_dict(b.get("consumes", {}), f"Постройка '{bid}'.consumes")
        check_resource_dict(b.get("repair_cost", {}), f"Постройка '{bid}'.repair_cost")
        bc = b.get("breakdown_chance", 0)
        if not isinstance(bc, (int, float)) or not (0 <= bc <= 1):
            errors.append(f"Постройка '{bid}': breakdown_chance должен быть числом от 0 до 1")
        check_breakdown_types_ref(b.get("breakdown_types"), f"Постройка '{bid}'")
        es = b.get("energy_storage", 0)
        if not isinstance(es, (int, float)) or es < 0:
            errors.append(f"Постройка '{bid}': energy_storage должен быть неотрицательным числом")
        active_period = b.get("active_period")
        if active_period not in (None, "day", "night"):
            errors.append(f"Постройка '{bid}': active_period должен быть null, 'day' или 'night'")
        cp = b.get("compute_power", 0)
        if not isinstance(cp, (int, float)) or cp < 0:
            errors.append(f"Постройка '{bid}': compute_power должен быть неотрицательным числом")
        if "autocraft_speed" in b:
            errors.append(
                f"Постройка '{bid}': autocraft_speed устарело - скорость автокрафта теперь задают сборщики "
                f"(autocraft_assembler) и улучшение «Автоматизация» механизма (type autocraft)"
            )
        slots = b.get("autocraft_slots", 0)
        if not isinstance(slots, (int, float)) or slots < 0:
            errors.append(f"Постройка '{bid}': autocraft_slots должен быть неотрицательным числом")
        breeding_slots = b.get("breeding_slots", 0)
        if not isinstance(breeding_slots, int) or isinstance(breeding_slots, bool) or breeding_slots < 0:
            errors.append(f"Постройка '{bid}': breeding_slots должен быть неотрицательным целым числом")
        breeding_accepts_family = b.get("breeding_accepts_family")
        if breeding_accepts_family is not None:
            if not isinstance(breeding_accepts_family, str) or not breeding_accepts_family:
                errors.append(f"Постройка '{bid}': breeding_accepts_family должен быть null или непустой строкой (тег семейства, см. species.*.family)")
            elif breeding_accepts_family not in species_families:
                errors.append(f"Постройка '{bid}': breeding_accepts_family ссылается на семейство '{breeding_accepts_family}', которого нет ни у одного вида")
            if not breeding_slots:
                errors.append(f"Постройка '{bid}': breeding_accepts_family имеет смысл только у ферм (breeding_slots > 0)")
        genetics_lab = b.get("genetics_lab", False)
        if not isinstance(genetics_lab, bool):
            errors.append(f"Постройка '{bid}': genetics_lab должен быть true или false")
        breeding_station = b.get("breeding_station", False)
        if not isinstance(breeding_station, bool):
            errors.append(f"Постройка '{bid}': breeding_station должен быть true или false")
        energy_prio = b.get("energy_priority", 0)
        if not isinstance(energy_prio, int) or isinstance(energy_prio, bool):
            errors.append(f"Постройка '{bid}': energy_priority должен быть целым числом")
        req_building = b.get("requires_building")
        if req_building is not None:
            if req_building == bid:
                errors.append(f"Постройка '{bid}': requires_building не может ссылаться на саму себя")
            else:
                check_building_ref(req_building, f"Постройка '{bid}'.requires_building")
        max_count = b.get("max_count")
        if max_count is not None and (not isinstance(max_count, int) or max_count <= 0):
            errors.append(f"Постройка '{bid}': max_count должен быть null или положительным целым числом")
        lab_tier = b.get("lab_tier")
        if lab_tier is not None and (not isinstance(lab_tier, int) or lab_tier <= 0):
            errors.append(f"Постройка '{bid}': lab_tier должен быть null или положительным целым числом")
        check_click_delay(b.get("click_delay_ms"), f"Постройка '{bid}'")
        if "unlocks_autocraft" in b:
            errors.append(
                f"Постройка '{bid}': unlocks_autocraft устарело - автокрафт рецепта открывает его механизм: "
                f"сборщик локации (autocraft_assembler) или улучшение «Автоматизация» (type autocraft)"
            )
        # autocraft_assembler {location | building, per_turn} - сборщик: делает рецепты
        # этой локации (или ритуалы этого алтаря - building с ritual_circle) мгновенно при
        # любой длительности, per_turn крафтов за ход на копию (null - без лимита).
        # Держать в паре с game/engine.py: _assembler_capacity.
        assembler = b.get("autocraft_assembler")
        if assembler is not None:
            where = f"Постройка '{bid}'.autocraft_assembler"
            if not isinstance(assembler, dict):
                errors.append(f"{where}: должен быть объектом {{location или building, per_turn}} или отсутствовать")
            else:
                has_location, has_building = "location" in assembler, "building" in assembler
                if has_location == has_building:
                    errors.append(f"{where}: нужно ровно одно из полей location (локация-механизм) или building (ритуальный алтарь)")
                elif has_location and assembler["location"] not in locations:
                    errors.append(f"{where}.location: неизвестная локация '{assembler['location']}'")
                elif has_building:
                    target = assembler["building"]
                    if target not in buildings:
                        errors.append(f"{where}.building: неизвестная постройка '{target}'")
                    elif not buildings[target].get("ritual_circle"):
                        errors.append(f"{where}.building: '{target}' не ритуальный алтарь (нет поля ritual_circle) - сборщик по постройке сейчас бывает только у ритуалов")
                per_turn = assembler.get("per_turn")
                if per_turn is not None and (not isinstance(per_turn, int) or isinstance(per_turn, bool) or per_turn <= 0):
                    errors.append(f"{where}.per_turn: должен быть положительным целым (крафтов за ход) или null (без лимита)")
        converter = b.get("energy_converter")
        if converter is not None:
            if not isinstance(converter, dict):
                errors.append(f"Постройка '{bid}': energy_converter должен быть объектом")
            else:
                max_per_turn = converter.get("max_energy_per_turn")
                if max_per_turn is not None and (not isinstance(max_per_turn, (int, float)) or max_per_turn <= 0):
                    errors.append(f"Постройка '{bid}': energy_converter.max_energy_per_turn должен быть null или положительным числом")
        # Репликатор: делает любой ресурс с replication_cost из материи matter,
        # не быстрее max_matter_per_turn за ход на одну репликацию (null - без лимита),
        # slots репликаций сразу на копию. Держать в паре с game/engine.py: replicate.
        replicator = b.get("replicator")
        if replicator is not None:
            if not isinstance(replicator, dict):
                errors.append(f"Постройка '{bid}': replicator должен быть объектом")
            else:
                matter = replicator.get("matter")
                if matter not in resource_ids:
                    errors.append(f"Постройка '{bid}': replicator.matter ссылается на несуществующий ресурс '{matter}'")
                elif matter == _ENERGY_RESOURCE_ID:
                    errors.append(f"Постройка '{bid}': replicator.matter не может быть энергией - это отдельный ресурс-материя")
                per_turn = replicator.get("max_matter_per_turn")
                if per_turn is not None and (not isinstance(per_turn, (int, float)) or isinstance(per_turn, bool) or per_turn <= 0):
                    errors.append(f"Постройка '{bid}': replicator.max_matter_per_turn должен быть null (без лимита) или положительным числом")
                slots = replicator.get("slots", 1)
                if not isinstance(slots, int) or isinstance(slots, bool) or slots <= 0:
                    errors.append(f"Постройка '{bid}': replicator.slots должен быть положительным целым числом (репликаций сразу на копию)")
        # «Только на излишках энергии»: берёт энергию после всех, кроме преобразователей
        # (game/engine.py: advance_turn) - без энергии в consumes флаг ни на что не влияет.
        surplus_only = b.get("energy_surplus_only")
        if surplus_only is not None:
            if not isinstance(surplus_only, bool):
                errors.append(f"Постройка '{bid}': energy_surplus_only должен быть true или false")
            elif surplus_only and not (b.get("consumes") or {}).get(_ENERGY_RESOURCE_ID):
                errors.append(f"Постройка '{bid}': energy_surplus_only имеет смысл только у постройки, потребляющей энергию (consumes.{_ENERGY_RESOURCE_ID})")
        soul_collector = b.get("soul_collector")
        if soul_collector is not None:
            if not isinstance(soul_collector, dict):
                errors.append(f"Постройка '{bid}': soul_collector должен быть объектом")
            else:
                slots = soul_collector.get("slots")
                if not isinstance(slots, int) or isinstance(slots, bool) or slots <= 0:
                    errors.append(f"Постройка '{bid}': soul_collector.slots должен быть положительным целым числом")
                accepts_kind = soul_collector.get("accepts_kind")
                if not isinstance(accepts_kind, str) or not accepts_kind:
                    errors.append(f"Постройка '{bid}': soul_collector.accepts_kind должен быть непустой строкой (тег вида души, см. resources.*.soul_kind)")
                elif accepts_kind not in soul_kinds:
                    errors.append(f"Постройка '{bid}': soul_collector.accepts_kind ссылается на тег '{accepts_kind}', которого нет ни у одного ресурса-души")
        enchanting_table = b.get("enchanting_table")
        if enchanting_table is not None:
            if not isinstance(enchanting_table, dict):
                errors.append(f"Постройка '{bid}': enchanting_table должен быть объектом")
            else:
                check_resource_dict(enchanting_table.get("cost", {}), f"Постройка '{bid}'.enchanting_table.cost")
                if not enchanting_table.get("cost"):
                    errors.append(f"Постройка '{bid}': enchanting_table.cost пуст - попытка зачарования должна что-то стоить (обычно книгу зачарований)")
                strip_on_fail = enchanting_table.get("strip_on_fail")
                if strip_on_fail is not None and not isinstance(strip_on_fail, bool):
                    errors.append(f"Постройка '{bid}': enchanting_table.strip_on_fail должен быть true/false")
        automation_hub = b.get("automation_hub")
        if automation_hub is not None:
            if not isinstance(automation_hub, dict):
                errors.append(f"Постройка '{bid}': automation_hub должен быть объектом")
            else:
                hub_slots = automation_hub.get("slots")
                if not isinstance(hub_slots, int) or isinstance(hub_slots, bool) or hub_slots <= 0:
                    errors.append(f"Постройка '{bid}': automation_hub.slots должен быть положительным целым числом")
        smeltery = b.get("smeltery")
        if smeltery is not None:
            if not isinstance(smeltery, dict):
                errors.append(f"Постройка '{bid}': smeltery должен быть объектом {{slots, capacity, heat}}")
            else:
                for field_name in ("slots", "capacity", "heat"):
                    val = smeltery.get(field_name)
                    if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                        errors.append(f"Постройка '{bid}': smeltery.{field_name} должен быть положительным целым числом")
        ritual_circle = b.get("ritual_circle")
        if ritual_circle is not None:
            if not isinstance(ritual_circle, dict):
                errors.append(f"Постройка '{bid}': ritual_circle должен быть объектом")
            else:
                ring_pedestals = ritual_circle.get("ring_pedestals")
                if not isinstance(ring_pedestals, int) or isinstance(ring_pedestals, bool) or ring_pedestals <= 0:
                    errors.append(f"Постройка '{bid}': ritual_circle.ring_pedestals должен быть положительным целым числом")
        reactor = b.get("reactor")
        if reactor is not None:
            if not isinstance(reactor, dict):
                errors.append(f"Постройка '{bid}': reactor должен быть объектом")
            else:
                for dim_field in ("grid_width", "grid_height", "zones_default"):
                    dim_val = reactor.get(dim_field)
                    if not isinstance(dim_val, int) or isinstance(dim_val, bool) or dim_val <= 0:
                        errors.append(f"Постройка '{bid}': reactor.{dim_field} должен быть положительным целым числом")
                accepts_roles = reactor.get("accepts_roles")
                if not isinstance(accepts_roles, list) or not accepts_roles:
                    errors.append(f"Постройка '{bid}': reactor.accepts_roles должен быть непустым списком тегов ролей")
                else:
                    for role in accepts_roles:
                        if not isinstance(role, str) or not role:
                            errors.append(f"Постройка '{bid}': reactor.accepts_roles содержит нестроковый/пустой элемент")
                        elif role not in reactor_roles:
                            errors.append(f"Постройка '{bid}': reactor.accepts_roles ссылается на тег роли '{role}', которого нет ни у одного ресурса-компонента реактора (см. resources.*.reactor_component.role)")
                for pct_field in ("soft_threshold_pct",):
                    pct_val = reactor.get(pct_field)
                    if not isinstance(pct_val, (int, float)) or isinstance(pct_val, bool) or not (0 < pct_val <= 1):
                        errors.append(f"Постройка '{bid}': reactor.{pct_field} должен быть числом от 0 (не включая) до 1 (включая)")
                for step_field in (
                    "heat_cap_per_cell", "fuel_adjacency_heat_step", "fuel_adjacency_output_step",
                    "moderator_adjacency_output_bonus", "absorber_adjacency_reduction",
                    "shield_adjacency_reduction",
                    "reflector_adjacency_output_mult_step", "thermal_shock_flow_delta_pct",
                    "thermal_shock_heat_bonus_pct",
                ):
                    step_val = reactor.get(step_field)
                    if not isinstance(step_val, (int, float)) or isinstance(step_val, bool) or step_val < 0:
                        errors.append(f"Постройка '{bid}': reactor.{step_field} должен быть неотрицательным числом")
                # НЕОБЯЗАТЕЛЬНЫЕ поля: отсутствие = движок берёт безопасный
                # дефолт (отражатель не греет; поглотитель режет выход так же,
                # как тепло), т.е. контент без них работает как раньше -
                # проверяем только если поле вообще задано
                for opt_field in (
                    "absorber_adjacency_output_reduction",
                    "reflector_adjacency_heat_mult_step",
                    "control_rod_burnup_factor",
                    "control_rod_heat_damp_exponent",
                    "catalyst_malus_radius",
                    "fuel_min_heat_mult",
                ):
                    if opt_field not in reactor:
                        continue
                    opt_val = reactor.get(opt_field)
                    if not isinstance(opt_val, (int, float)) or isinstance(opt_val, bool) or opt_val < 0:
                        errors.append(f"Постройка '{bid}': reactor.{opt_field} должен быть неотрицательным числом")
                burnup_factor = reactor.get("control_rod_burnup_factor")
                if isinstance(burnup_factor, (int, float)) and not isinstance(burnup_factor, bool) and burnup_factor > 1:
                    errors.append(f"Постройка '{bid}': reactor.control_rod_burnup_factor - доля от 0 до 1 (0 = заряд тратится независимо от стержней, 1 = ровно в темпе демпфирования)")
                heat_exp = reactor.get("control_rod_heat_damp_exponent")
                if isinstance(heat_exp, (int, float)) and not isinstance(heat_exp, bool) and heat_exp < 1:
                    errors.append(f"Постройка '{bid}': reactor.control_rod_heat_damp_exponent должен быть >= 1 (1 = рычаг душит тепло и выход одинаково, больше 1 = тепло сильнее)")
                malus_radius = reactor.get("catalyst_malus_radius")
                if isinstance(malus_radius, (int, float)) and not isinstance(malus_radius, bool) and malus_radius != int(malus_radius):
                    errors.append(f"Постройка '{bid}': reactor.catalyst_malus_radius должен быть целым (радиус Чебышёва в клетках)")
                min_heat = reactor.get("fuel_min_heat_mult")
                if isinstance(min_heat, (int, float)) and not isinstance(min_heat, bool) and min_heat >= 1:
                    errors.append(f"Постройка '{bid}': reactor.fuel_min_heat_mult - доля собственного тепла стержня от 0 (включая) до 1 (не включая); 1 и выше означало бы, что соседи вообще не гасят тепло")
                accident_table = reactor.get("accident_table")
                if not isinstance(accident_table, str) or not accident_table:
                    errors.append(f"Постройка '{bid}': reactor.accident_table должен быть непустой строкой (id из top-level reactor_accidents)")
                elif accident_table not in reactor_accident_tables:
                    errors.append(f"Постройка '{bid}': reactor.accident_table ссылается на неизвестную таблицу '{accident_table}' (см. top-level reactor_accidents)")
        stages = b.get("construction_stages")
        if stages is not None:
            if not isinstance(stages, list) or len(stages) < 1:
                errors.append(
                    f"Постройка '{bid}': construction_stages должен быть непустым списком этапов "
                    "(или отсутствовать вовсе у одноэтапной постройки)"
                )
            else:
                for i, stage in enumerate(stages, start=1):
                    where = f"Постройка '{bid}', этап {i}"
                    if not isinstance(stage, dict):
                        errors.append(f"{where}: должен быть объектом")
                        continue
                    check_resource_dict(stage.get("cost", {}), f"{where}.cost")
                    if not stage.get("description"):
                        errors.append(f"{where}: не указано описание этапа")
        upgrades = b.get("upgrades")
        if upgrades is not None:
            if not isinstance(upgrades, list):
                errors.append(f"Постройка '{bid}': upgrades должен быть списком улучшений (или отсутствовать вовсе)")
            else:
                seen_upgrade_ids: set[str] = set()
                for i, up in enumerate(upgrades, start=1):
                    where = f"Постройка '{bid}', улучшение {i}"
                    if not isinstance(up, dict):
                        errors.append(f"{where}: должно быть объектом")
                        continue
                    uid = up.get("id", "")
                    check_id("Улучшение", uid)
                    if uid in seen_upgrade_ids:
                        errors.append(f"{where}: повторяющийся id улучшения '{uid}' у этой же постройки")
                    seen_upgrade_ids.add(uid)
                    if not up.get("name"):
                        errors.append(f"{where}: не указано название")
                    if not up.get("description"):
                        errors.append(f"{where}: не указано описание - игроку должно быть понятно, что даёт улучшение")
                    check_tech_ref(up.get("requires_tech"), where)
                    check_resource_dict(up.get("cost", {}), f"{where}.cost")
                    # repeatable - улучшение можно купить НЕСКОЛЬКО РАЗ подряд (а не
                    # один раз, как остальные) - каждая покупка добавляет ЕЩЁ ОДИН
                    # value (см. game/engine.py:_purchased_upgrade_value), а цена
                    # растёт геометрически (cost_growth в степени "сколько раз уже
                    # куплено", см. repeatable_upgrade_cost). Одна запись в контенте
                    # тем самым заменяет N вручную прописанных дискретных тиров -
                    # удобно для "просто ещё один слот подороже", годится для ЛЮБОГО
                    # типа улучшения, не только *_slots/*_capacity.
                    repeatable = up.get("repeatable", False)
                    if not isinstance(repeatable, bool):
                        errors.append(f"{where}: repeatable должен быть true или false")
                    cost_growth = up.get("cost_growth", 1)
                    if not isinstance(cost_growth, (int, float)) or isinstance(cost_growth, bool) or cost_growth <= 0:
                        errors.append(f"{where}: cost_growth должен быть положительным числом")
                    elif repeatable and cost_growth <= 1:
                        errors.append(f"{where}: cost_growth должен быть больше 1 у repeatable-улучшения - иначе цена не растёт с каждой следующей покупкой")
                    max_purchases = up.get("max_purchases")
                    if max_purchases is not None:
                        if not isinstance(max_purchases, int) or isinstance(max_purchases, bool) or max_purchases <= 0:
                            errors.append(f"{where}: max_purchases должен быть null или положительным целым числом")
                        if not repeatable:
                            errors.append(f"{where}: max_purchases имеет смысл только у repeatable-улучшений (иначе улучшение и так покупается не больше одного раза)")
                    kind = up.get("type")
                    value = up.get("value")
                    if kind == "scale":
                        if not isinstance(value, (int, float)) or value <= 0:
                            errors.append(f"{where}: value для scale должен быть положительным числом (множитель ВСЕГО рецепта относительно базового - не обязательно целым и не обязательно >= 2, автор волен задать любой, например 1.5 или 3)")
                    elif kind == "energy_efficiency":
                        if not isinstance(value, (int, float)) or not (0 < value < 1):
                            errors.append(f"{where}: value для energy_efficiency должен быть числом от 0 до 1 (множитель потребления энергии)")
                    elif kind == "generation_efficiency":
                        if not isinstance(value, (int, float)) or value <= 1:
                            errors.append(f"{where}: value для generation_efficiency должен быть числом больше 1 (множитель выработки энергии)")
                        if not b.get("produces", {}).get(_ENERGY_RESOURCE_ID):
                            errors.append(f"{where}: generation_efficiency имеет смысл только у построек, производящих энергию (produces.{_ENERGY_RESOURCE_ID})")
                    elif kind == "breeding_output":
                        if not isinstance(value, (int, float)) or value <= 0:
                            errors.append(f"{where}: value для breeding_output должен быть положительным числом (множитель продукции особей, поселённых в ЭТОЙ ферме)")
                        if not b.get("breeding_slots"):
                            errors.append(f"{where}: breeding_output имеет смысл только у ферм (breeding_slots > 0)")
                    elif kind == "breeding_slots_bonus":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для breeding_slots_bonus должен быть положительным числом (АБСОЛЮТНАЯ добавка к вместимости ЭТОГО экземпляра фермы - см. game/engine.py:farm_instance_slot_capacity)")
                        if not b.get("breeding_slots"):
                            errors.append(f"{where}: breeding_slots_bonus имеет смысл только у ферм (breeding_slots > 0)")
                    elif kind == "breeding_immortality":
                        if not isinstance(value, (int, float)) or value <= 0:
                            errors.append(f"{where}: value для breeding_immortality должен быть положительным числом (само число не используется - куплено/не куплено, но поле обязано быть числом для единообразия)")
                        if not b.get("breeding_slots"):
                            errors.append(f"{where}: breeding_immortality имеет смысл только у ферм (breeding_slots > 0)")
                    elif kind == "breeding_mutation_chance":
                        if not isinstance(value, (int, float)) or value <= 0:
                            errors.append(f"{where}: value для breeding_mutation_chance должен быть положительным числом (множитель шанса мутации при скрещивании)")
                        if not b.get("breeding_station"):
                            errors.append(f"{where}: breeding_mutation_chance имеет смысл только у механизма скрещивания (breeding_station == true)")
                    elif kind == "energy_converter_capacity":
                        if not isinstance(value, (int, float)) or value <= 0:
                            errors.append(f"{where}: value для energy_converter_capacity должен быть положительным числом (АБСОЛЮТНАЯ добавка к energy_converter.max_energy_per_turn этой же постройки, уровни не складываются - см. game/engine.py:energy_converter_max_energy_per_turn)")
                        if not b.get("energy_converter"):
                            errors.append(f"{where}: energy_converter_capacity имеет смысл только у построек с energy_converter")
                    elif kind == "environment_emulation":
                        # В отличие от остальных 7 типов, value тут - СТРОКА (id аллели
                        # среды), не число: эти апгрейды взаимоисключающие (не прогрессия
                        # "сильнейший побеждает") и покупаются на КАЖДЫЙ экземпляр фермы
                        # отдельно - см. game/engine.py:purchase_farm_instance_upgrade.
                        if value not in _ENVIRONMENT_ALLELE_IDS:
                            errors.append(f"{where}: value для environment_emulation должен быть одним из {sorted(_ENVIRONMENT_ALLELE_IDS)} (сейчас: {value!r})")
                        if not b.get("breeding_slots"):
                            errors.append(f"{where}: environment_emulation имеет смысл только у ферм (breeding_slots > 0)")
                        if repeatable:
                            errors.append(f"{where}: environment_emulation не может быть repeatable - это взаимоисключающий выбор среды, а не накопительный бонус")
                    elif kind == "soul_collector_slots":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для soul_collector_slots должен быть положительным числом (АБСОЛЮТНАЯ добавка к вместимости синтезатора, уровни не складываются - см. game/engine.py:soul_collector_capacity)")
                        if not b.get("soul_collector"):
                            errors.append(f"{where}: soul_collector_slots имеет смысл только у синтезаторов душ (soul_collector)")
                    elif kind == "soul_generation_efficiency":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 1:
                            errors.append(f"{where}: value для soul_generation_efficiency должен быть числом больше 1 (множитель продукции синтезатора)")
                        if not b.get("soul_collector"):
                            errors.append(f"{where}: soul_generation_efficiency имеет смысл только у синтезаторов душ (soul_collector)")
                    elif kind == "breakdown_immunity":
                        # Как breeding_immortality - число не используется по значению
                        # (куплено/не куплено), но обязано быть числом для единообразия.
                        # В ОТЛИЧИЕ от остальных типов, разрешён у ЛЮБОЙ постройки
                        # (не только у ферм) - это ровно и есть "апгрейд для всех
                        # механизмов" (см. game/engine.py:effective_breakdown_chance).
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для breakdown_immunity должен быть положительным числом (само число не используется - куплено/не куплено, но поле обязано быть числом для единообразия)")
                    elif kind == "action_duration_reduction":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 < value < 1):
                            errors.append(f"{where}: value для action_duration_reduction должен быть числом от 0 до 1 (не включая 1) - доля сокращения duration_turns")
                        # Держать в паре с game/engine.py:action_mechanism_building -
                        # действие зависит от постройки и через СВОЙ requires_building,
                        # и через requires_building своей локации (рецепты печи-механизма).
                        has_multiturn_dependent = any(
                            (a.get("requires_building") or (locations.get(a.get("location")) or {}).get("requires_building")) == bid
                            and (a.get("duration_turns") or 1) > 1
                            for a in manual_actions.values()
                        )
                        if not has_multiturn_dependent:
                            errors.append(f"{where}: action_duration_reduction имеет смысл только у построек, от которых зависит (requires_building самого действия или его локации) хотя бы одно многоходовое (duration_turns > 1) ручное действие")
                    elif kind == "autocraft":
                        # «Автоматизация» механизма: автокрафт может запускать рецепты
                        # локации, которую открывает эта постройка (как игрок), либо
                        # рецепты преобразователя. Держать в паре с engine.mechanism_automated/
                        # automated_converter_types.
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для autocraft должен быть положительным числом (само число не используется - куплено/не куплено)")
                        if up.get("repeatable"):
                            errors.append(f"{where}: autocraft не может быть repeatable - это переключатель, а не накопление")
                        opens_location = any(l.get("requires_building") == bid for l in locations.values())
                        if not opens_location and not b.get("energy_converter") and not b.get("replicator"):
                            errors.append(f"{where}: autocraft имеет смысл только у постройки, которая открывает локацию-механизм (locations.*.requires_building), у преобразователя (energy_converter) или у репликатора (replicator)")
                    elif kind == "replicator_throughput":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для replicator_throughput должен быть положительным числом (АБСОЛЮТНАЯ добавка к replicator.max_matter_per_turn этой же постройки - см. game/engine.py:replicator_max_matter_per_turn)")
                        if not b.get("replicator"):
                            errors.append(f"{where}: replicator_throughput имеет смысл только у репликатора (replicator)")
                    elif kind == "automation_slots":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для automation_slots должен быть положительным числом (АБСОЛЮТНАЯ добавка к вместимости автоматизатора, уровни не складываются - см. game/engine.py:automation_slots_capacity)")
                        if not b.get("automation_hub"):
                            errors.append(f"{where}: automation_slots имеет смысл только у построек-автоматизаторов (automation_hub)")
                    elif kind == "research_speed":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для research_speed должен быть положительным числом (АБСОЛЮТНОЕ сокращение времени исследования, В ХОДАХ, не доля - см. game/engine.py:tech_research_turns), уровни не складываются")
                        if not b.get("lab_tier"):
                            errors.append(f"{where}: research_speed имеет смысл только у построек-лабораторий (lab_tier) - действует на техи ТОГО ЖЕ тира, что lab_tier этой постройки")
                    elif kind == "ritual_pedestals":
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для ritual_pedestals должен быть положительным числом (АБСОЛЮТНАЯ добавка к ёмкости кольца ритуального круга, уровни не складываются - см. game/engine.py:ritual_pedestal_capacity)")
                        if not b.get("ritual_circle"):
                            errors.append(f"{where}: ritual_pedestals имеет смысл только у построек-алтарей (ritual_circle)")
                    elif kind == "reactor_zoning":
                        # Как environment_emulation - pick-one (замена, не накопление),
                        # value тут ЦЕЛОЕ ЧИСЛО зон, а не мультипликатор.
                        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для reactor_zoning должен быть положительным целым числом (сколько зон в решётке ПОСЛЕ покупки)")
                        if not b.get("reactor"):
                            errors.append(f"{where}: reactor_zoning имеет смысл только у построек-реакторов (reactor)")
                        if repeatable:
                            errors.append(f"{where}: reactor_zoning не может быть repeatable - это взаимоисключающий выбор зонирования, а не накопительный бонус")
                    elif kind == "reactor_grid_size":
                        if not isinstance(value, dict) or "width" not in value or "height" not in value:
                            errors.append(f"{where}: value для reactor_grid_size должен быть объектом {{\"width\": N, \"height\": M}}")
                        else:
                            for dim_field in ("width", "height"):
                                dim_val = value.get(dim_field)
                                if not isinstance(dim_val, int) or isinstance(dim_val, bool) or dim_val <= 0:
                                    errors.append(f"{where}: value.{dim_field} для reactor_grid_size должен быть положительным целым числом")
                        if not b.get("reactor"):
                            errors.append(f"{where}: reactor_grid_size имеет смысл только у построек-реакторов (reactor)")
                        if repeatable:
                            errors.append(f"{where}: reactor_grid_size не может быть repeatable - размер решётки прогрессия по позиции (как scale), а не накопление на каждую покупку")
                    elif kind == "reactor_telemetry":
                        if not isinstance(value, bool) or not value:
                            errors.append(f"{where}: value для reactor_telemetry должен быть true (само значение не используется по величине - куплено/не куплено)")
                        if not b.get("reactor"):
                            errors.append(f"{where}: reactor_telemetry имеет смысл только у построек-реакторов (reactor)")
                    elif kind in ("smeltery_slots", "smeltery_capacity", "smeltery_heat"):
                        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                            errors.append(f"{where}: value для {kind} должен быть положительным целым числом (АБСОЛЮТНАЯ добавка к smeltery этой постройки, уровни не складываются - см. game/engine.py:smeltery_heat/smeltery_slot_capacity/smeltery_tank_capacity)")
                        if not b.get("smeltery"):
                            errors.append(f"{where}: {kind} имеет смысл только у плавилен (smeltery)")
                    else:
                        errors.append(
                            f"{where}: type должен быть 'scale', 'energy_efficiency', 'generation_efficiency', "
                            f"'breeding_output', 'breeding_slots_bonus', 'breeding_immortality', "
                            f"'breeding_mutation_chance', 'energy_converter_capacity', 'environment_emulation', "
                            f"'soul_collector_slots', 'soul_generation_efficiency', 'breakdown_immunity', "
                            f"'action_duration_reduction', 'automation_slots', 'research_speed', 'ritual_pedestals', "
                            f"'reactor_zoning', 'reactor_grid_size', 'reactor_telemetry', 'smeltery_slots', "
                            f"'smeltery_capacity', 'smeltery_heat' или 'autocraft' (сейчас: {kind!r})"
                        )
        if not b.get("name"):
            errors.append(f"Постройка '{bid}': не указано название")
        if not b.get("description"):
            errors.append(f"Постройка '{bid}': не указано описание - игроку должно быть понятно, зачем она нужна")
        label = b.get("icon_label")
        if label is not None and (not isinstance(label, str) or not (1 <= len(label) <= 3)):
            errors.append(f"Постройка '{bid}': icon_label должен быть строкой из 1-3 символов")
        check_icon(b.get("icon"), f"Постройка '{bid}'")
        tags = b.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
            errors.append(f"Постройка '{bid}': tags должен быть списком непустых строк (произвольные теги для фильтра в панели «Производственные мощности»)")

    building_cyclic = find_building_requires_cycle(buildings)
    if building_cyclic:
        names = ", ".join(f"'{buildings[b]['name']}'" for b in building_cyclic if b in buildings)
        errors.append(f"Цикл в зависимостях requires_building построек (одна ссылается на другую через цепочку): {names}")

    for btid, bt in breakdown_types.items():
        record_id(btid, "вид поломки")
        check_id("Вид поломки", btid)
        if not bt.get("name"):
            errors.append(f"Вид поломки '{btid}': не указано название")
        if not bt.get("description"):
            errors.append(f"Вид поломки '{btid}': не указано описание - игроку должно быть понятно, что случилось")
        required_tools = bt.get("required_tools", [])
        if not isinstance(required_tools, list) or not required_tools:
            errors.append(f"Вид поломки '{btid}': required_tools должен быть непустым списком id инструментов")
        else:
            for tool_id in required_tools:
                if tool_id not in tools:
                    errors.append(f"Вид поломки '{btid}': неизвестный инструмент '{tool_id}' в required_tools")

    for tid, t in techs.items():
        record_id(tid, "технология")
        check_id("Технология", tid)
        tier = t.get("tier")
        if not isinstance(tier, int) or tier < 1:
            errors.append(f"Технология '{tid}': tier должен быть положительным целым числом (тир 1 доступен без лаборатории)")
        research_turns = t.get("research_turns")
        if research_turns is not None and (not isinstance(research_turns, int) or isinstance(research_turns, bool) or research_turns < 2):
            errors.append(f"Технология '{tid}': research_turns должен быть null или целым числом >= 2 (сколько ходов занимает исследование сверх мгновенного - см. game/engine.py:tech_research_turns)")
        for req in t.get("requires", []):
            if req == tid:
                errors.append(f"Технология '{tid}' требует сама себя")
            elif req not in techs:
                errors.append(f"Технология '{tid}': неизвестное условие '{req}'")
        check_resource_dict(t.get("cost", {}), f"Технология '{tid}'.cost")
        if not t.get("name"):
            errors.append(f"Технология '{tid}': не указано название")
        if not t.get("description"):
            errors.append(f"Технология '{tid}': не указано описание - игроку должно быть понятно, что она даёт")
        for eff in t.get("effects", []):
            b = eff.get("building")
            if b != "*" and b not in buildings:
                errors.append(f"Технология '{tid}': эффект ссылается на неизвестную постройку '{b}'")
            if "produces" in eff:
                check_resource_dict(eff["produces"], f"Технология '{tid}'.эффект.produces")
            if "consumes" in eff:
                check_resource_dict(eff["consumes"], f"Технология '{tid}'.эффект.consumes")
            if "breakdown_delta" in eff and not isinstance(eff["breakdown_delta"], (int, float)):
                errors.append(f"Технология '{tid}': breakdown_delta эффекта должен быть числом")

    cyclic = find_requires_cycle(techs)
    if cyclic:
        names = ", ".join(f"'{techs[t]['name']}'" for t in cyclic if t in techs)
        errors.append(f"Цикл в зависимостях технологий (получить их будет невозможно): {names}")

    for rid, info in mining_info.items():
        if rid not in resource_ids:
            errors.append(f"Добыча '{rid}': это не известный ресурс")
        check_tech_ref(info.get("requires_tech"), f"Добыча '{rid}'")
        required_tool = info.get("required_tool")
        if required_tool not in tools:
            errors.append(f"Добыча '{rid}': required_tool ссылается на неизвестный инструмент '{required_tool}'")
        else:
            tier = info.get("required_tier", 1)
            tier_count = len(tools[required_tool]["tiers"])
            if not isinstance(tier, int) or not (1 <= tier <= tier_count):
                errors.append(f"Добыча '{rid}': required_tier должен быть от 1 до {tier_count}")
        rarity = info.get("rarity", 0)
        if not isinstance(rarity, (int, float)) or rarity <= 0:
            errors.append(f"Добыча '{rid}': rarity должен быть положительным числом (чем больше - тем реже встречается руда)")
        toughness = info.get("toughness")
        if not isinstance(toughness, int) or isinstance(toughness, bool) or toughness <= 0:
            errors.append(f"Добыча '{rid}': toughness должен быть положительным целым числом (сколько урона снять с клетки)")
        cluster = info.get("field_cluster_size")
        if not (
            isinstance(cluster, list) and len(cluster) == 2 and all(isinstance(x, int) for x in cluster)
            and 0 < cluster[0] <= cluster[1]
        ):
            errors.append(f"Добыча '{rid}': field_cluster_size должен быть парой [min, max], 0 < min <= max")
        yld = info.get("base_yield", 0)
        if not isinstance(yld, int) or yld <= 0:
            errors.append(f"Добыча '{rid}': base_yield должен быть положительным целым числом")
        check_icon(info.get("icon"), f"Добыча '{rid}'")

    mine_field = data.get("mine_field", {})
    if not isinstance(mine_field, dict):
        errors.append("mine_field: должен быть объектом настроек поля шахты")
    else:
        size = mine_field.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append("mine_field.size: должен быть положительным целым числом")
        dig_tool = mine_field.get("dig_tool")
        if dig_tool not in tools:
            errors.append(f"mine_field.dig_tool: ссылается на неизвестный инструмент '{dig_tool}'")
        budget = mine_field.get("cluster_budget")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            errors.append("mine_field.cluster_budget: должен быть положительным целым числом (общее число кластеров руды на поле)")
        scanner_tool = mine_field.get("scanner_tool")
        if scanner_tool not in tools:
            errors.append(f"mine_field.scanner_tool: ссылается на неизвестный инструмент '{scanner_tool}'")
        else:
            scanner_tier_count = len(tools[scanner_tool]["tiers"])
            prev_tier = 0
            for key in ("scanner_types_tier", "scanner_counts_tier", "scanner_reveal_all_tier"):
                value = mine_field.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= scanner_tier_count):
                    errors.append(f"mine_field.{key}: должен быть целым числом от 1 до {scanner_tier_count} (число тиров '{scanner_tool}')")
                elif value < prev_tier:
                    errors.append(f"mine_field.{key}: должен быть не меньше предыдущего порога щупа (иначе более высокий тир ничего не добавляет)")
                else:
                    prev_tier = value
            reveal_all_tier = mine_field.get("scanner_reveal_all_tier")
            radius_tiers = mine_field.get("scanner_radius_tiers", [])
            if not isinstance(radius_tiers, list):
                errors.append("mine_field.scanner_radius_tiers: должен быть списком порогов {tier, radius}")
            else:
                for i, entry in enumerate(radius_tiers):
                    if not isinstance(entry, dict):
                        errors.append(f"mine_field.scanner_radius_tiers[{i}]: должен быть объектом {{tier, radius}}")
                        continue
                    radius_tier = entry.get("tier")
                    radius = entry.get("radius")
                    if not isinstance(radius_tier, int) or isinstance(radius_tier, bool) or not (1 <= radius_tier <= scanner_tier_count):
                        errors.append(f"mine_field.scanner_radius_tiers[{i}].tier: должен быть целым числом от 1 до {scanner_tier_count}")
                    elif isinstance(reveal_all_tier, int) and radius_tier >= reveal_all_tier:
                        errors.append(f"mine_field.scanner_radius_tiers[{i}].tier: должен быть меньше scanner_reveal_all_tier ({reveal_all_tier}) - иначе порог никогда не сработает")
                    if not isinstance(radius, int) or isinstance(radius, bool) or radius <= 0:
                        errors.append(f"mine_field.scanner_radius_tiers[{i}].radius: должен быть положительным целым числом")
        rock_resource = mine_field.get("rock_resource")
        if rock_resource not in resource_ids:
            errors.append(f"mine_field.rock_resource: ссылается на неизвестный ресурс '{rock_resource}'")
        rock_yield = mine_field.get("rock_yield")
        if not isinstance(rock_yield, int) or isinstance(rock_yield, bool) or rock_yield <= 0:
            errors.append("mine_field.rock_yield: должен быть положительным целым числом")
        rock_toughness = mine_field.get("rock_toughness")
        if not isinstance(rock_toughness, int) or isinstance(rock_toughness, bool) or rock_toughness <= 0:
            errors.append("mine_field.rock_toughness: должен быть положительным целым числом")
        check_icon(mine_field.get("rock_icon"), "mine_field.rock_icon")
        check_icon(mine_field.get("rock_broken_icon"), "mine_field.rock_broken_icon")
        rock_variants = mine_field.get("rock_icon_variants", [])
        if not isinstance(rock_variants, list):
            errors.append("mine_field.rock_icon_variants: должен быть списком иконок")
        else:
            for i, variant in enumerate(rock_variants):
                check_icon(variant, f"mine_field.rock_icon_variants[{i}]")

    mobs = data.get("mobs", {})
    for mid, mob in mobs.items():
        record_id(mid, "моб/босс")
        check_id("Моб/босс", mid)
        where = f"Моб/босс '{mid}'"
        if not mob.get("name"):
            errors.append(f"{where}: не указано название")
        if not mob.get("description"):
            errors.append(f"{where}: не указано описание")
        check_icon(mob.get("icon"), where)
        loc = mob.get("location")
        if loc not in locations:
            errors.append(f"{where}: location ссылается на неизвестную локацию '{loc}'")
        check_tech_ref(mob.get("requires_tech"), where)
        check_building_ref(mob.get("requires_building"), f"{where}.requires_building")
        hp = mob.get("hp")
        if not isinstance(hp, int) or isinstance(hp, bool) or hp <= 0:
            errors.append(f"{where}: hp должен быть положительным целым числом")
        xp_reward = mob.get("xp_reward", 0)
        if not isinstance(xp_reward, (int, float)) or isinstance(xp_reward, bool) or xp_reward < 0:
            errors.append(f"{where}: xp_reward должен быть неотрицательным числом")
        damage = mob.get("damage", 0)
        if not isinstance(damage, (int, float)) or isinstance(damage, bool) or damage < 0:
            errors.append(f"{where}: damage должен быть неотрицательным числом (ответный урон по игроку за НЕубивающий удар, см. game/engine.py:attack_encounter), по умолчанию 0")
        check_resource_dict(mob.get("loot", {}), f"{where}.loot")
        check_chance_dict(mob.get("loot_chance"), mob.get("loot", {}), f"{where}.loot_chance")
        is_boss = mob.get("is_boss", False)
        if not isinstance(is_boss, bool):
            errors.append(f"{where}: is_boss должен быть true или false")
        if is_boss:
            check_resource_dict(mob.get("summon_cost", {}), f"{where}.summon_cost")
            if not mob.get("summon_cost"):
                errors.append(f"{where}: это босс (is_boss=true), но summon_cost пуст - его нечем призывать")
            # По прямому пожеланию: душа босса добывается ТОЛЬКО крафтом (обычный
            # manual_actions рецепт из лута босса), а не выбивается с шансом при
            # убийстве - в отличие от обычных мобов (см. ветку else ниже).
            if mob.get("soul_id") is not None or mob.get("soul_chance") is not None:
                errors.append(f"{where}: у боссов не бывает soul_id/soul_chance - душа босса добывается только крафтом")
            turn_limit = mob.get("turn_limit")
            if turn_limit is not None:
                if not isinstance(turn_limit, int) or isinstance(turn_limit, bool) or turn_limit <= 0:
                    errors.append(f"{where}.turn_limit: должен быть положительным целым числом, если указан")
        else:
            rarity = mob.get("rarity", 0)
            if not isinstance(rarity, (int, float)) or isinstance(rarity, bool) or rarity <= 0:
                errors.append(f"{where}: rarity должен быть положительным числом (чем больше - тем реже встречается, см. mining_info)")
            soul_id = mob.get("soul_id")
            if soul_id is not None:
                if soul_id not in resource_ids:
                    errors.append(f"{where}.soul_id: неизвестный ресурс '{soul_id}'")
                soul_chance = mob.get("soul_chance")
                if not isinstance(soul_chance, (int, float)) or isinstance(soul_chance, bool) or not (0 < soul_chance <= 1):
                    errors.append(f"{where}.soul_chance: должен быть числом от 0 (не включая) до 1, если указан soul_id")
            if mob.get("turn_limit") is not None:
                errors.append(f"{where}: turn_limit имеет смысл только у боссов (is_boss=true)")

    # 4 жёстко зашитых признака генотипа (species - отдельно, через species[].
    # dominant/weight ниже) - держать в паре с GENOTYPE_TRAIT_IDS в
    # game/engine.py, тот же принцип ручного дублирования, что и у _MINE_LOCATION_ID.
    _GENETIC_TRAIT_IDS = ("lifespan", "output_multiplier", "active_period", "environment")
    genetic_traits = data.get("genetic_traits", {})
    trait_allele_ids = {
        tid: {a.get("id") for a in (t.get("alleles") or []) if isinstance(a, dict)}
        for tid, t in genetic_traits.items() if isinstance(t, dict)
    } if isinstance(genetic_traits, dict) else {}

    for sid, sp in species.items():
        # Ключ - id уже существующего ресурса (тот же принцип, что у mining_info) -
        # НЕ регистрируется через record_id: он ЗАКОНОМЕРНО совпадает с id ресурса,
        # это не отдельная сущность своего пространства id (аллель признака "вид"
        # генотипа - см. genetic_traits ниже про остальные 3 признака).
        if sid not in resource_ids:
            errors.append(f"Вид '{sid}': это не известный ресурс")
        if not sp.get("description"):
            errors.append(f"Вид '{sid}': не указано описание")
        check_resource_dict(sp.get("produces", {}), f"Вид '{sid}'.produces")
        check_resource_dict(sp.get("consumes", {}), f"Вид '{sid}'.consumes")
        dominant = sp.get("dominant", False)
        if not isinstance(dominant, bool):
            errors.append(f"Вид '{sid}': dominant должен быть true или false")
        weight = sp.get("weight", 0)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
            errors.append(f"Вид '{sid}': weight должен быть неотрицательным числом (0 = недоступен для дикого улова, только через мутацию)")
        family = sp.get("family")
        if family is not None and (not isinstance(family, str) or not family):
            errors.append(f"Вид '{sid}': family должен быть null или непустой строкой (например 'пчела'/'цветок') - см. buildings.*.breeding_accepts_family")
        # Пул генов вида (game/engine.py: species_gene_pool) - какие аллели особь
        # носит от природы. Обязателен по каждому признаку: без него движок молча
        # подставил бы одни доминантные аллели, и вид выглядел бы "настроенным".
        gene_pool = sp.get("gene_pool")
        if not isinstance(gene_pool, dict):
            errors.append(f"Вид '{sid}': не задан пул генов (gene_pool) - какие аллели каждого признака носит особь от природы")
        else:
            for trait_id in gene_pool:
                if trait_id not in _GENETIC_TRAIT_IDS:
                    errors.append(f"Вид '{sid}'.gene_pool: неизвестный признак '{trait_id}' (допустимы {list(_GENETIC_TRAIT_IDS)})")
            for trait_id in _GENETIC_TRAIT_IDS:
                pool = gene_pool.get(trait_id)
                pwhere = f"Вид '{sid}'.gene_pool.{trait_id}"
                if not isinstance(pool, dict) or not pool:
                    errors.append(f"{pwhere}: нужна хотя бы одна аллель")
                    continue
                for allele_id, weight in pool.items():
                    if trait_id in trait_allele_ids and allele_id not in trait_allele_ids[trait_id]:
                        errors.append(f"{pwhere}: неизвестная аллель '{allele_id}'")
                    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                        errors.append(f"{pwhere}.{allele_id}: вес должен быть положительным числом")
        # bonus_* - бонусная продукция промышленной пасеки (см. game/engine.py:
        # _process_habitat): КАКОЙ ресурс зависит от ВИДА (тут), а СРАБОТАЕТ ли
        # бонус в этот ход - от генотипа "environment" (см. genetic_traits ниже) -
        # два разных вида в одной и той же благоприятной среде дают разные бонусы.
        # bonus_resource - необязательное поле (null/отсутствует = у вида нет
        # бонуса); amount/chance проверяются, только если он указан - пустых у
        # вида без бонуса не требуем, тот же принцип, что у soul_id/soul_chance
        # моба (см. validate mobs выше).
        bonus_resource = sp.get("bonus_resource")
        if bonus_resource is not None:
            if bonus_resource not in resource_ids:
                errors.append(f"Вид '{sid}'.bonus_resource: неизвестный ресурс '{bonus_resource}'")
            bonus_amount = sp.get("bonus_amount")
            if not isinstance(bonus_amount, (int, float)) or isinstance(bonus_amount, bool) or bonus_amount <= 0:
                errors.append(f"Вид '{sid}'.bonus_amount: должен быть положительным числом, если указан bonus_resource")
            bonus_chance = sp.get("bonus_chance")
            if not isinstance(bonus_chance, (int, float)) or isinstance(bonus_chance, bool) or not (0 < bonus_chance <= 1):
                errors.append(f"Вид '{sid}'.bonus_chance: должен быть числом от 0 (не включая) до 1, если указан bonus_resource")

    # Каталог аллелей 4 признаков (_GENETIC_TRAIT_IDS выше). Веса у аллели нет:
    # как часто она встречается, решает пул каждого вида (species.*.gene_pool).
    _ACTIVE_PERIOD_ALLELE_IDS = {"day", "night", "always"}
    for trait_id in _GENETIC_TRAIT_IDS:
        trait = genetic_traits.get(trait_id)
        if trait is None:
            errors.append(f"genetic_traits: не хватает обязательного признака '{trait_id}'")
            continue
        if not isinstance(trait, dict):
            errors.append(f"genetic_traits.{trait_id}: должен быть объектом")
            continue
        if not trait.get("name"):
            errors.append(f"genetic_traits.{trait_id}: не указано название")
        alleles = trait.get("alleles")
        if not isinstance(alleles, list) or not alleles:
            errors.append(f"genetic_traits.{trait_id}.alleles: должен быть непустым списком аллелей")
            continue
        seen_allele_ids: set[str] = set()
        has_dominant = False
        for i, a in enumerate(alleles, start=1):
            awhere = f"genetic_traits.{trait_id}, аллель {i}"
            if not isinstance(a, dict):
                errors.append(f"{awhere}: должен быть объектом")
                continue
            aid = a.get("id", "")
            check_id("Аллель", aid)
            if aid in seen_allele_ids:
                errors.append(f"{awhere}: повторяющийся id аллели '{aid}' у этого же признака")
            seen_allele_ids.add(aid)
            if not a.get("name"):
                errors.append(f"{awhere}: не указано название")
            dominant = a.get("dominant", False)
            if not isinstance(dominant, bool):
                errors.append(f"{awhere}: dominant должен быть true или false")
            elif dominant:
                has_dominant = True
            if trait_id == "active_period":
                if aid not in _ACTIVE_PERIOD_ALLELE_IDS:
                    errors.append(f"{awhere}: у признака active_period id аллели должен быть одним из {sorted(_ACTIVE_PERIOD_ALLELE_IDS)} (сейчас: '{aid}')")
            elif trait_id == "environment":
                if aid not in _ENVIRONMENT_ALLELE_IDS:
                    errors.append(f"{awhere}: у признака environment id аллели должен быть одним из {sorted(_ENVIRONMENT_ALLELE_IDS)} (сейчас: '{aid}')")
            else:
                value = a.get("value")
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    errors.append(f"{awhere}.value: должен быть положительным числом")
        if trait_id == "active_period":
            missing = _ACTIVE_PERIOD_ALLELE_IDS - seen_allele_ids
            if missing:
                errors.append(f"genetic_traits.active_period: не хватает обязательных аллелей {sorted(missing)}")
        elif trait_id == "environment":
            missing = _ENVIRONMENT_ALLELE_IDS - seen_allele_ids
            if missing:
                errors.append(f"genetic_traits.environment: не хватает обязательных аллелей {sorted(missing)}")
        if not has_dominant:
            errors.append(f"genetic_traits.{trait_id}: ни одна аллель не помечена dominant=true - при гетерозиготе фенотип не определить")

    mutations = data.get("mutations", [])
    if not isinstance(mutations, list):
        errors.append("mutations: должен быть списком")
    else:
        chance_by_pair: dict[frozenset, float] = {}
        for i, mutation in enumerate(mutations, start=1):
            mwhere = f"Мутация {i}"
            if not isinstance(mutation, dict):
                errors.append(f"{mwhere}: должна быть объектом")
                continue
            parent_a, parent_b = mutation.get("parent_a"), mutation.get("parent_b")
            for field_name, parent in (("parent_a", parent_a), ("parent_b", parent_b)):
                if parent not in species:
                    errors.append(f"{mwhere}.{field_name}: неизвестный вид '{parent}'")
            check_tech_ref(mutation.get("requires_tech"), mwhere)
            check_building_ref(mutation.get("requires_building"), f"{mwhere}.requires_building")
            chance = mutation.get("chance")
            if not isinstance(chance, (int, float)) or isinstance(chance, bool) or not (0 < chance <= 1):
                errors.append(f"{mwhere}.chance: должен быть числом от 0 (не включая) до 1 (включительно)")
            elif parent_a in species and parent_b in species:
                # Игрок при скрещивании этой пары в итоге получает вид одного из
                # родителей с шансом (1 - сумма шансов ВСЕХ применимых мутаций)/2
                # каждый (см. game/engine.py:_cross_genotype) - если сумма шансов
                # мутаций для пары превысит 1, эта доля уйдёт в минус, т.е. чистый
                # вид родителя станет НЕДОСТИЖИМ. requires_tech/requires_building
                # тут намеренно не учитываются - берём ХУДШИЙ случай "все мутации
                # для этой пары открыты одновременно", а не текущий прогресс.
                key = frozenset({parent_a, parent_b})
                chance_by_pair[key] = chance_by_pair.get(key, 0.0) + chance
            outcomes = mutation.get("outcomes")
            if not isinstance(outcomes, list) or not outcomes:
                errors.append(f"{mwhere}: outcomes должен быть непустым списком исходов")
            else:
                for j, o in enumerate(outcomes, start=1):
                    owhere = f"{mwhere}, исход {j}"
                    if not isinstance(o, dict):
                        errors.append(f"{owhere}: должен быть объектом")
                        continue
                    if o.get("species_id") not in species:
                        errors.append(f"{owhere}.species_id: неизвестный вид '{o.get('species_id')}'")
                    weight = o.get("weight")
                    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                        errors.append(f"{owhere}.weight: должен быть положительным числом")
        for key, total in chance_by_pair.items():
            if total > 1:
                pair_names = " x ".join(sorted(key))
                errors.append(
                    f"Мутации для пары '{pair_names}': суммарный chance всех мутаций этой пары "
                    f"({total}) больше 1 - если все они окажутся открыты одновременно, шанс получить "
                    "чистокровного потомка вида одного из родителей уйдёт в минус"
                )

    genetics_settings = data.get("genetics", {})
    if not isinstance(genetics_settings, dict):
        errors.append("genetics: должен быть объектом")
    else:
        check_resource_dict(genetics_settings.get("synthesis_cost", {}), "genetics.synthesis_cost")
        env_mutation_chance = genetics_settings.get("environment_mutation_chance")
        if env_mutation_chance is not None and (
            not isinstance(env_mutation_chance, (int, float)) or isinstance(env_mutation_chance, bool)
            or not (0 <= env_mutation_chance <= 1)
        ):
            errors.append("genetics.environment_mutation_chance: должен быть числом от 0 до 1, если указан")

    combat_settings = data.get("combat", {})
    if not isinstance(combat_settings, dict):
        errors.append("combat: должен быть объектом")
    else:
        # Необязательный раздел целиком (тот же принцип, что genetics) - у каждого
        # поля есть защитный дефолт на стороне движка (game/content.py:COMBAT_SETTINGS,
        # game/engine.py:player_max_hp/_process_combat/_move_damage), поэтому здесь
        # проверяем только те поля, что реально заданы.
        for field_name in ("player_base_hp", "hp_regen_per_turn", "wounded_turns"):
            value = combat_settings.get(field_name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
                errors.append(f"combat.{field_name}: должен быть неотрицательным числом, если указан")
        wound_mult = combat_settings.get("wounded_damage_multiplier")
        if wound_mult is not None and (
            not isinstance(wound_mult, (int, float)) or isinstance(wound_mult, bool) or not (0 < wound_mult <= 1)
        ):
            errors.append("combat.wounded_damage_multiplier: должен быть числом в диапазоне (0, 1], если указан")

    energy_recipes = data.get("energy_recipes", {})
    converter_has_grid = isinstance(locations.get(_CONVERTER_LOCATION_ID, {}).get("crafting"), dict)
    for rid, recipe in energy_recipes.items():
        record_id(rid, "рецепт крафта за энергию")
        check_id("Рецепт крафта за энергию", rid)
        check_tech_ref(recipe.get("requires_tech"), f"Рецепт крафта за энергию '{rid}'")
        if not recipe.get("name"):
            errors.append(f"Рецепт крафта за энергию '{rid}': не указано название")
        threshold = recipe.get("threshold")
        if not isinstance(threshold, (int, float)) or threshold <= 0:
            errors.append(f"Рецепт крафта за энергию '{rid}': threshold должен быть положительным числом")
        check_resource_dict(recipe.get("produces", {}), f"Рецепт крафта за энергию '{rid}'.produces", allow_range=True)
        if not recipe.get("produces"):
            errors.append(f"Рецепт крафта за энергию '{rid}': produces пустой - рецепт ничего не даёт")
        check_resource_dict(recipe.get("consumes", {}), f"Рецепт крафта за энергию '{rid}'.consumes")
        check_building_ref(recipe.get("machine"), f"Рецепт крафта за энергию '{rid}'.machine")
        # Узор - слой над consumes, собирается в сетке преобразователя (у
        # energy_recipes нет своего поля location). Пока у преобразователя есть
        # сетка, рецепт без узора игрок не смог бы запустить руками вовсе.
        label = f"Рецепт крафта за энергию '{rid}'"
        check_grid_pattern(label, recipe, _CONVERTER_LOCATION_ID, "consumes")
        register_pattern_shape(label, recipe, _CONVERTER_LOCATION_ID)
        if converter_has_grid and recipe.get("pattern") is None:
            errors.append(
                f"{label}: у преобразователя есть крафт-сетка, а у рецепта нет узора (pattern) - "
                f"запустить его руками будет нельзя"
            )

    # Приёмы боя (id -> {"name", "description", "mode", ...}) - разблокируются НЕ
    # отсюда, а tools.*.tiers[].unlocks_moves (см. ниже) - здесь только сами
    # определения, набор id нужен ДО цикла по tools, чтобы проверить ссылки
    # unlocks_moves. "mode" (необязателен, по умолчанию "multiplier") - НЕЗАВИСИМЫЕ
    # способы посчитать урон одного удара (см. game/engine.py:_move_damage):
    #   "multiplier"          - damage_multiplier домножает обычный расчёт по навыку/оружию.
    #   "percent_max_hp"      - percent от МАКСИМАЛЬНОГО HP цели, не зависит от навыка/оружия.
    #   "percent_remaining_hp"- percent от ОСТАВШЕГОСЯ прямо сейчас HP цели.
    #   "execute"             - добивает НАЦЕЛО, если remaining_hp уже не выше
    #                           execute_threshold_percent от максимума; иначе - обычный
    #                           расчёт, домноженный на необязательный damage_multiplier
    #                           (по умолчанию 1.0 - как без приёма).
    # У каждого mode - СВОЙ обязательный набор полей; поля чужого mode - ошибка (чтобы
    # в контенте не оставалось "мёртвых" полей от смены mode задним числом).
    _COMBAT_MOVE_MODES = ("multiplier", "percent_max_hp", "percent_remaining_hp", "execute")
    combat_moves = data.get("combat_moves", {})
    for move_id, move in combat_moves.items():
        record_id(move_id, "приём боя")
        check_id("Приём боя", move_id)
        cwhere = f"Приём боя '{move_id}'"
        if not move.get("name"):
            errors.append(f"{cwhere}: не указано название")
        if not move.get("description"):
            errors.append(f"{cwhere}: не указано описание")
        mode = move.get("mode", "multiplier")
        if mode not in _COMBAT_MOVE_MODES:
            errors.append(f"{cwhere}.mode: должен быть одним из {list(_COMBAT_MOVE_MODES)}, если указан")
        if mode == "multiplier":
            multiplier = move.get("damage_multiplier")
            if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool) or multiplier <= 0:
                errors.append(f"{cwhere}.damage_multiplier: должен быть положительным числом при mode=multiplier")
            for extra in ("percent", "execute_threshold_percent"):
                if extra in move:
                    errors.append(f"{cwhere}.{extra}: имеет смысл только при mode=percent_max_hp/percent_remaining_hp/execute")
        elif mode in ("percent_max_hp", "percent_remaining_hp"):
            percent = move.get("percent")
            if not isinstance(percent, (int, float)) or isinstance(percent, bool) or not (0 < percent <= 1):
                errors.append(f"{cwhere}.percent: должен быть числом от 0 (не включая) до 1 (включительно) при mode={mode}")
            if "damage_multiplier" in move:
                errors.append(f"{cwhere}.damage_multiplier: имеет смысл только при mode=multiplier/execute")
            if "execute_threshold_percent" in move:
                errors.append(f"{cwhere}.execute_threshold_percent: имеет смысл только при mode=execute")
        elif mode == "execute":
            threshold = move.get("execute_threshold_percent")
            if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not (0 < threshold <= 1):
                errors.append(f"{cwhere}.execute_threshold_percent: должен быть числом от 0 (не включая) до 1 (включительно) при mode=execute")
            if "damage_multiplier" in move:
                fallback = move["damage_multiplier"]
                if not isinstance(fallback, (int, float)) or isinstance(fallback, bool) or fallback <= 0:
                    errors.append(f"{cwhere}.damage_multiplier: должен быть положительным числом, если указан (запасной урон, пока цель ещё выше порога)")
            if "percent" in move:
                errors.append(f"{cwhere}.percent: имеет смысл только при mode=percent_max_hp/percent_remaining_hp")
        cooldown = move.get("cooldown_turns", 0)
        if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
            errors.append(f"{cwhere}.cooldown_turns: должен быть неотрицательным целым числом, если указан")

    if not tools:
        errors.append("Список инструментов пуст - должен быть хотя бы один (например, кирка)")
    for tool_id, tool in tools.items():
        record_id(tool_id, "инструмент")
        check_id("Инструмент", tool_id)
        if not tool.get("name"):
            errors.append(f"Инструмент '{tool_id}': не указано название")
        tiers = tool.get("tiers")
        if not tiers:
            errors.append(f"Инструмент '{tool_id}': список тиров пуст - должен быть хотя бы стартовый тир")
            continue
        if tiers[0].get("cost") not in (None, {}):
            errors.append(f"Инструмент '{tool_id}': тир №1 (стартовый) должен иметь пустую cost - он уже есть у игрока бесплатно")
        if tiers[0].get("max_durability") is not None:
            errors.append(f"Инструмент '{tool_id}': тир №1 (стартовый, \"без инструмента\") не может изнашиваться - max_durability имеет смысл только у тира 2+")
        for i, tier in enumerate(tiers[1:], start=2):
            if not tier.get("name"):
                errors.append(f"Инструмент '{tool_id}', тир №{i}: не указано название")
            check_tech_ref(tier.get("requires_tech"), f"Инструмент '{tool_id}', тир №{i}")
            check_resource_dict(tier.get("cost") or {}, f"Инструмент '{tool_id}', тир №{i}.cost")
            twhere_tier = f"Инструмент '{tool_id}', тир №{i}"
            max_durability = tier.get("max_durability")
            if max_durability is not None:
                if not isinstance(max_durability, int) or isinstance(max_durability, bool) or max_durability <= 0:
                    errors.append(f"{twhere_tier}: max_durability должен быть null или положительным целым числом")
                check_resource_dict(tier.get("repair_cost") or {}, f"{twhere_tier}.repair_cost")
                if not tier.get("repair_cost"):
                    errors.append(f"{twhere_tier}: repair_cost обязателен, если задан max_durability - иначе непонятно, чем инструмент чинится, когда прочность дойдёт до 0")
            elif tier.get("repair_cost"):
                errors.append(f"{twhere_tier}: repair_cost имеет смысл только вместе с max_durability (инструмент без прочности не ломается вовсе)")
        weapon = tool.get("weapon", False)
        if not isinstance(weapon, bool):
            errors.append(f"Инструмент '{tool_id}': weapon должен быть true или false")
        armor = tool.get("armor", False)
        if not isinstance(armor, bool):
            errors.append(f"Инструмент '{tool_id}': armor должен быть true или false")
        enchant_kind = tool.get("enchant_kind")
        if enchant_kind is not None and (not isinstance(enchant_kind, str) or not enchant_kind):
            errors.append(f"Инструмент '{tool_id}': enchant_kind должен быть null или непустой строкой (тег вида зачарования, см. enchantments.*.applies_to_kind)")
        for i, tier in enumerate(tiers, start=1):
            check_icon(tier.get("icon"), f"Инструмент '{tool_id}', тир №{i}")
            # Боевые поля тира (damage_multiplier/instant_kill_max_hp/loot_multiplier/
            # soul_chance_multiplier) - необязательны, имеют смысл только у оружия
            # (weapon=true), см. game/engine.py:_weapon_bonuses. Несколько линеек-
            # оружия работают ОДНОВРЕМЕННО (максимум по каждому полю независимо), так
            # уникальный меч-ваншот (своя линейка из 2 тиров: тир 1 - "не выковано",
            # тир 2 - собственно легендарный меч с этими полями) не мешает обычному
            # прогрессивному мечу давать свой damage_multiplier.
            twhere = f"Инструмент '{tool_id}', тир №{i}"
            for field_name in ("damage_multiplier", "loot_multiplier", "soul_chance_multiplier"):
                value = tier.get(field_name)
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    errors.append(f"{twhere}.{field_name}: должен быть положительным числом, если указан")
                if not weapon:
                    errors.append(f"{twhere}: {field_name} имеет смысл только у оружия (weapon=true)")
            instant_kill = tier.get("instant_kill_max_hp")
            if instant_kill is not None:
                if not isinstance(instant_kill, (int, float)) or isinstance(instant_kill, bool) or instant_kill < 0:
                    errors.append(f"{twhere}.instant_kill_max_hp: должен быть неотрицательным числом, если указан")
                if not weapon:
                    errors.append(f"{twhere}: instant_kill_max_hp имеет смысл только у оружия (weapon=true)")
            # Зеркало боевых полей оружия для защиты (max_hp_bonus/damage_reduction) -
            # необязательны, имеют смысл только у брони (armor=true), см.
            # game/engine.py:_armor_bonuses. Несколько линеек брони действуют
            # ОДНОВРЕМЕННО (максимум по каждому полю независимо), тот же принцип.
            for field_name in ("max_hp_bonus", "damage_reduction"):
                value = tier.get(field_name)
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    errors.append(f"{twhere}.{field_name}: должен быть неотрицательным числом, если указан")
                if not armor:
                    errors.append(f"{twhere}: {field_name} имеет смысл только у брони (armor=true)")
            # mining_area/mining_power - зона удара (N x N) и урон по клетке поля шахты
            # за клик, необязательны, ни к какому флагу не привязаны (в отличие от
            # weapon/armor полей выше) - какой именно инструмент их использует, решает
            # content.json mine_field.dig_tool, не хардкод здесь.
            mining_area = tier.get("mining_area")
            if mining_area is not None and (not isinstance(mining_area, int) or isinstance(mining_area, bool) or mining_area <= 0):
                errors.append(f"{twhere}.mining_area: должен быть положительным целым числом, если указан")
            mining_power = tier.get("mining_power")
            if mining_power is not None and (not isinstance(mining_power, int) or isinstance(mining_power, bool) or mining_power <= 0):
                errors.append(f"{twhere}.mining_power: должен быть положительным целым числом, если указан")
            # unlocks_moves - приёмы боя, которые игрок получает, ДОСТИГНУВ этого тира
            # (кумулятивно с более ранними тирами той же линейки, см.
            # game/engine.py:unlocked_attack_moves) - в отличие от остальных боевых
            # полей тира (которые действуют только пока это ТЕКУЩИЙ тир), список
            # НЕ обязан повторяться на более высоких тирах - разблокировка не забывается.
            unlocks_moves = tier.get("unlocks_moves", [])
            if not isinstance(unlocks_moves, list):
                errors.append(f"{twhere}.unlocks_moves: должен быть списком id приёмов боя")
            else:
                if unlocks_moves and not weapon:
                    errors.append(f"{twhere}: unlocks_moves имеет смысл только у оружия (weapon=true)")
                for move_id in unlocks_moves:
                    if move_id not in combat_moves:
                        errors.append(f"{twhere}.unlocks_moves: неизвестный приём боя '{move_id}'")

    skills = data.get("skills", {})
    for skid, skill in skills.items():
        record_id(skid, "навык")
        check_id("Навык", skid)
        if not skill.get("name"):
            errors.append(f"Навык '{skid}': не указано название")
        levels = skill.get("levels")
        if not isinstance(levels, list) or not levels:
            errors.append(f"Навык '{skid}': levels должен быть непустым списком уровней (хотя бы стартовый уровень 1)")
            continue
        prev_xp = None
        for i, lvl in enumerate(levels, start=1):
            lwhere = f"Навык '{skid}', уровень {i}"
            if not isinstance(lvl, dict):
                errors.append(f"{lwhere}: должен быть объектом")
                continue
            xp_required = lvl.get("xp_required", 0)
            if not isinstance(xp_required, (int, float)) or isinstance(xp_required, bool) or xp_required < 0:
                errors.append(f"{lwhere}.xp_required: должен быть неотрицательным числом")
            elif i > 1 and prev_xp is not None and xp_required <= prev_xp:
                errors.append(f"{lwhere}.xp_required: должен быть больше, чем у предыдущего уровня - иначе уровни неразличимы по накопленному опыту")
            if isinstance(xp_required, (int, float)) and not isinstance(xp_required, bool):
                prev_xp = xp_required
            effects = lvl.get("effects", {})
            if not isinstance(effects, dict):
                errors.append(f"{lwhere}.effects: должен быть объектом (свободная форма - какую механику он прокачивает, решает не сама система навыков)")

    # enchantments - стол зачарований (см. game/engine.py:enchantment_pool/enchant_tool) -
    # закрытый список типов эффекта, держать в паре с _enchant_effect_value/
    # tool_durability_remaining/_weapon_bonuses/_armor_bonuses в game/engine.py.
    _ENCHANT_EFFECT_TYPES = {
        "yield_multiplier", "durability_multiplier", "damage_bonus",
        "drop_chance_multiplier", "soul_chance_multiplier", "armor_defense_bonus", "max_hp_bonus_add",
    }
    enchantments = data.get("enchantments", {})
    enchant_kinds = {t.get("enchant_kind") for t in tools.values() if t.get("enchant_kind")}
    for eid, ench in enchantments.items():
        record_id(eid, "зачарование")
        check_id("Зачарование", eid)
        where = f"Зачарование '{eid}'"
        if not ench.get("name"):
            errors.append(f"{where}: не указано название")
        if not ench.get("description"):
            errors.append(f"{where}: не указано описание")
        check_icon(ench.get("icon"), where)
        applies_to_kind = ench.get("applies_to_kind")
        if applies_to_kind is not None:
            if not isinstance(applies_to_kind, str) or not applies_to_kind:
                errors.append(f"{where}: applies_to_kind должен быть null (универсальное зачарование, доступно ЛЮБОМУ зачаровываемому инструменту) или непустой строкой (тег tools.*.enchant_kind)")
            elif applies_to_kind not in enchant_kinds:
                errors.append(f"{where}: applies_to_kind ссылается на тег '{applies_to_kind}', которого нет ни у одного инструмента (tools.*.enchant_kind)")
        effect = ench.get("effect")
        if not isinstance(effect, dict) or "type" not in effect:
            errors.append(f"{where}: effect должен быть объектом с полем type")
        elif effect["type"] not in _ENCHANT_EFFECT_TYPES:
            errors.append(f"{where}.effect.type: должен быть одним из {sorted(_ENCHANT_EFFECT_TYPES)} (сейчас: {effect['type']!r})")
        levels = ench.get("levels")
        if not isinstance(levels, list) or not levels:
            errors.append(f"{where}: levels должен быть непустым списком уровней (хотя бы один)")
        else:
            for i, lvl in enumerate(levels, start=1):
                lwhere = f"{where}, уровень {i}"
                if not isinstance(lvl, dict):
                    errors.append(f"{lwhere}: должен быть объектом")
                    continue
                value = lvl.get("value")
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    errors.append(f"{lwhere}.value: должен быть числом")
                weight = lvl.get("weight", 1)
                if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                    errors.append(f"{lwhere}.weight: должен быть положительным числом (или отсутствовать - тогда 1)")

    # soul_recipes ключуется id ресурса (души) - тот же принцип, что species/mining_info
    # (словарь ДОБАВЛЯЕТ метаданные существующему ресурсу, отдельного id не заводит).
    for soul_id, recipe in soul_recipes.items():
        where = f"Продукция синтезатора для '{soul_id}'"
        if soul_id not in resource_ids:
            errors.append(f"{where}: '{soul_id}' - это не известный ресурс")
        produces = recipe.get("produces", {})
        check_resource_dict(produces, f"{where}.produces", allow_range=True)
        if not produces:
            errors.append(f"{where}: produces пустой - эта душа ничего не производит")
        check_chance_dict(recipe.get("produces_chance"), produces, f"{where}.produces_chance")
        resource = next((r for r in resources if r.get("id") == soul_id), None)
        if resource is not None and not resource.get("soul_kind"):
            errors.append(f"{where}: у ресурса '{soul_id}' не задан soul_kind - его нельзя будет вставить ни в один синтезатор (см. resources.*.soul_kind)")

    # ritual_recipes - свежие id (как manual_actions), а НЕ декорация существующего
    # ресурса (в отличие от soul_recipes) - требуют обычной проверки check_id/record_id
    # на уникальность среди ВСЕХ id проекта.
    ritual_recipes = data.get("ritual_recipes", {})
    for rid, recipe in ritual_recipes.items():
        where = f"Ритуал '{rid}'"
        check_id("Рецепт ритуала", rid)
        record_id(rid, "рецепт ритуала")
        if not recipe.get("name"):
            errors.append(f"{where}: не указано название")
        check_tech_ref(recipe.get("requires_tech"), where)
        building_id = recipe.get("requires_building")
        if building_id is None:
            errors.append(f"{where}: requires_building обязателен - у каждого ритуала должен быть алтарь")
        else:
            check_building_ref(building_id, where)
            if building_id in buildings and not buildings[building_id].get("ritual_circle"):
                errors.append(f"{where}: requires_building '{building_id}' не является алтарём (нет поля ritual_circle)")
        ring_cost = recipe.get("ring_cost", {})
        check_resource_dict(ring_cost, f"{where}.ring_cost")
        if not ring_cost:
            errors.append(f"{where}: ring_cost пуст - ритуал должен требовать хотя бы один ингредиент в кольце")
        center_cost = recipe.get("center_cost")
        if not isinstance(center_cost, str) or not center_cost:
            errors.append(f"{where}: center_cost должен быть непустой строкой (id ресурса для центрального пьедестала)")
        elif center_cost not in resource_ids:
            errors.append(f"{where}: center_cost ссылается на неизвестный ресурс '{center_cost}'")
        check_resource_dict(recipe.get("extra_cost", {}), f"{where}.extra_cost")
        duration = recipe.get("duration_turns", 1)
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
            errors.append(f"{where}.duration_turns: должен быть целым числом не меньше 1")
        extra_per_turn = recipe.get("extra_consumes_per_turn", {})
        check_resource_dict(extra_per_turn, f"{where}.extra_consumes_per_turn")
        check_chance_dict(recipe.get("extra_consumes_chance"), extra_per_turn, f"{where}.extra_consumes_chance")
        if extra_per_turn and duration <= 1:
            errors.append(f"{where}: extra_consumes_per_turn задан, но duration_turns <= 1 - тратить его не за что (добавьте ходов или перенесите расход в extra_cost)")
        produces = recipe.get("produces", {})
        check_resource_dict(produces, f"{where}.produces", allow_range=True)
        if not produces:
            errors.append(f"{where}: produces пустой - этот ритуал ничего не производит")
        check_chance_dict(recipe.get("produces_chance"), produces, f"{where}.produces_chance")
        check_sound(recipe.get("sound"), where)

    # Плавильня (buildings.*.smeltery) - три раздела рецептов со СВОИМИ свежими id (как
    # ritual_recipes). Жидкие металлы (resources.*.molten) бывают только в тигле: в input
    # плавки их нет, у сплава весь input/output - металлы, литьё берёт ровно один металл.
    def check_positive_int(value, where: str, field_name: str, default=None) -> None:
        val = default if value is None else value
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            errors.append(f"{where}.{field_name}: должен быть целым числом не меньше 1")

    def check_smeltery_common(recipe, kind_name: str, rid: str) -> str:
        where = f"{kind_name} '{rid}'"
        check_id(kind_name, rid)
        record_id(rid, kind_name.lower())
        if not isinstance(recipe, dict):
            errors.append(f"{where}: должен быть объектом")
            return where
        if not recipe.get("name"):
            errors.append(f"{where}: не указано название")
        check_tech_ref(recipe.get("requires_tech"), where)
        return where

    for rid, recipe in (data.get("melt_recipes") or {}).items():
        where = check_smeltery_common(recipe, "Рецепт плавки", rid)
        if not isinstance(recipe, dict):
            continue
        check_positive_int(recipe.get("heat"), where, "heat", default=1)
        check_positive_int(recipe.get("turns"), where, "turns", default=1)
        melt_input = recipe.get("input") or {}
        check_resource_dict(melt_input, f"{where}.input")
        if not melt_input:
            errors.append(f"{where}: input пуст - плавить нечего")
        for res in melt_input:
            if res in molten_ids:
                errors.append(f"{where}.input: '{res}' - жидкий металл, он бывает только в тигле (переплавку слитка задавайте слитком)")
        melt_output = recipe.get("output") or {}
        check_resource_dict(melt_output, f"{where}.output")
        if not melt_output:
            errors.append(f"{where}: output пуст - в тигель ничего не стечёт")

    for rid, recipe in (data.get("alloy_recipes") or {}).items():
        where = check_smeltery_common(recipe, "Рецепт сплава", rid)
        if not isinstance(recipe, dict):
            continue
        check_positive_int(recipe.get("heat"), where, "heat", default=1)
        if "hidden" in recipe and not isinstance(recipe["hidden"], bool):
            errors.append(f"{where}.hidden: должен быть true/false")
        for field_name in ("input", "output"):
            amounts = recipe.get(field_name) or {}
            check_resource_dict(amounts, f"{where}.{field_name}")
            if not amounts:
                errors.append(f"{where}: {field_name} пуст")
            for res in amounts:
                if res in resource_ids and res not in molten_ids:
                    errors.append(f"{where}.{field_name}: '{res}' не жидкий металл (нет resources.*.molten) - сплав идёт только в тигле")

    cast_pairs: dict = {}
    for rid, recipe in (data.get("cast_recipes") or {}).items():
        where = check_smeltery_common(recipe, "Рецепт литья", rid)
        if not isinstance(recipe, dict):
            continue
        check_positive_int(recipe.get("turns"), where, "turns", default=1)
        check_positive_int(recipe.get("amount"), where, "amount", default=1)
        form = recipe.get("form")
        if form not in resource_ids:
            errors.append(f"{where}.form: '{form}' - не известный ресурс (форма - предмет склада, литьё его не тратит)")
        elif form in molten_ids:
            errors.append(f"{where}.form: '{form}' - жидкий металл, формой он быть не может")
        metal = recipe.get("metal")
        if metal not in molten_ids:
            errors.append(f"{where}.metal: '{metal}' не жидкий металл (нужен ресурс с resources.*.molten)")
        cast_output = recipe.get("output") or {}
        check_resource_dict(cast_output, f"{where}.output")
        if not cast_output:
            errors.append(f"{where}: output пуст - отливке нечего дать")
        pair = (form, metal)
        if pair in cast_pairs:
            errors.append(f"{where}: форма '{form}' с металлом '{metal}' уже есть в рецепте '{cast_pairs[pair]}' - литейный стол не поймёт, какой из них лить")
        else:
            cast_pairs[pair] = rid

    def check_conditions(conditions, label: str) -> None:
        """Список условий {type, ...} квеста. Пусто/не список - единственная
        обязательная проверка на этом уровне, каждое отдельное условие проверяется
        по CONDITION_FIELDS/game.engine._condition_met."""
        if not isinstance(conditions, list) or not conditions:
            errors.append(f"{label}: должно быть хотя бы одно условие")
            return
        for i, c in enumerate(conditions, start=1):
            where = f"{label}, условие {i}"
            if not isinstance(c, dict):
                errors.append(f"{where}: должно быть объектом")
                continue
            ctype = c.get("type")
            required = CONDITION_FIELDS.get(ctype)
            if required is None:
                errors.append(f"{where}: неизвестный тип условия '{ctype}'")
                continue
            for field_name in required:
                if field_name not in c:
                    errors.append(f"{where}: не хватает поля '{field_name}'")
            if "resource" in c and c["resource"] not in resource_ids:
                errors.append(f"{where}: неизвестный ресурс '{c['resource']}'")
            if "building" in c and c["building"] not in buildings:
                errors.append(f"{where}: неизвестная постройка '{c['building']}'")
            if "tech" in c and c["tech"] not in techs:
                errors.append(f"{where}: неизвестная технология '{c['tech']}'")
            if "tool" in c and c["tool"] not in tools:
                errors.append(f"{where}: неизвестный инструмент '{c['tool']}'")
            if "species" in c and c["species"] not in species:
                errors.append(f"{where}: неизвестный вид '{c['species']}'")
            if "action" in c and c["action"] not in manual_actions:
                errors.append(f"{where}: неизвестное ручное действие '{c['action']}'")
            if "upgrade" in c and "building" in c and c["building"] in buildings:
                upgrade_ids = {u.get("id") for u in buildings[c["building"]].get("upgrades", [])}
                if c["upgrade"] not in upgrade_ids:
                    errors.append(f"{where}: у постройки '{c['building']}' нет улучшения '{c['upgrade']}'")
            if ctype == "storage_tier_at_least" and "resource" in c and c["resource"] in resource_ids:
                res_def = next((r for r in resources if r.get("id") == c["resource"]), None)
                if not res_def or not res_def.get("storage"):
                    errors.append(f"{where}: у ресурса '{c['resource']}' не настроена вместимость склада (storage)")
            if "upgrade_type" in c and c["upgrade_type"] not in _BUILDING_UPGRADE_TYPES:
                errors.append(f"{where}: неизвестный тип улучшения '{c['upgrade_type']}'")
            if "amount" in c and (not isinstance(c["amount"], (int, float)) or c["amount"] <= 0):
                errors.append(f"{where}: amount должен быть положительным числом")

    # loot_cases - НОВОЕ пространство id (не привязано к ресурсу, в отличие от
    # soul_recipes выше) - награда за квесты (см. quests ниже), но кейс может не
    # быть ничьей наградой (валидатор это не требует). "prizes" - барабан лутбокса:
    # выпадает РОВНО один приз (см. game/engine.py:_roll_loot_case_prize), поэтому,
    # в отличие от soul_recipes/produces, тут нет отдельной "гарантированной" части -
    # весь список И ЕСТЬ возможные исходы.
    loot_cases = data.get("loot_cases", {})
    for cid, case in loot_cases.items():
        record_id(cid, "кейс с лутом")
        check_id("Кейс с лутом", cid)
        where = f"Кейс с лутом '{cid}'"
        if not case.get("name"):
            errors.append(f"{where}: не указано название")
        if not case.get("description"):
            errors.append(f"{where}: не указано описание")
        prizes = case.get("prizes")
        if not isinstance(prizes, list) or not prizes:
            errors.append(f"{where}: prizes должен быть непустым списком призов барабана")
            continue
        for i, prize in enumerate(prizes, start=1):
            pwhere = f"{where}, приз {i}"
            if not isinstance(prize, dict):
                errors.append(f"{pwhere}: должен быть объектом")
                continue
            if prize.get("resource") not in resource_ids:
                errors.append(f"{pwhere}: неизвестный ресурс '{prize.get('resource')}'")
            amount = prize.get("amount")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
                errors.append(f"{pwhere}.amount: должен быть положительным числом")
            weight = prize.get("weight", 1)
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                errors.append(f"{pwhere}.weight: должен быть положительным числом (или отсутствовать - тогда 1)")

    # reactor_accidents - барабан аварий реактора (buildings.*.reactor.accident_table
    # ссылается сюда по id), тот же алгоритм взвешенного розыгрыша, что у loot_cases
    # (см. game/engine.py:_roll_reactor_accident - буквальная копия _roll_loot_case_prize),
    # но "result" - не ресурс, а закрытый хардкод-enum (_REACTOR_ACCIDENT_RESULTS).
    reactor_accidents = data.get("reactor_accidents", {})
    if not isinstance(reactor_accidents, dict):
        errors.append("reactor_accidents: ожидался словарь id_таблицы -> {entries}")
        reactor_accidents = {}
    else:
        for tid, table in reactor_accidents.items():
            record_id(tid, "таблица аварий реактора")
            check_id("Таблица аварий реактора", tid)
            where = f"Таблица аварий реактора '{tid}'"
            if not isinstance(table, dict):
                errors.append(f"{where}: должна быть объектом {{entries}}")
                continue
            entries = table.get("entries")
            if not isinstance(entries, list) or not entries:
                errors.append(f"{where}: entries должен быть непустым списком исходов")
                continue
            for i, entry in enumerate(entries, start=1):
                ewhere = f"{where}, исход {i}"
                if not isinstance(entry, dict):
                    errors.append(f"{ewhere}: должен быть объектом")
                    continue
                result = entry.get("result")
                if result not in _REACTOR_ACCIDENT_RESULTS:
                    errors.append(f"{ewhere}.result: должен быть одним из {sorted(_REACTOR_ACCIDENT_RESULTS)} (сейчас: {result!r})")
                weight = entry.get("weight", 1)
                if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                    errors.append(f"{ewhere}.weight: должен быть положительным числом (или отсутствовать - тогда 1)")
                if not entry.get("message"):
                    errors.append(f"{ewhere}: не указано message - игроку должно быть понятно, что произошло")

    # eras - имя/главный цвет каждой эры квестбука (ключ - era квеста, как
    # строка). НЕОБЯЗАТЕЛЬНЫЙ раздел: у era, для которой тут нет записи,
    # UI-стороны (content_editor.html/web_ui.html) сами подставляют дефолтное
    # имя "Эра N" и цикличную палитру - тут это НЕ ошибка, чтобы можно было
    # завести у квеста новый номер era раньше, чем зайти и назвать её отдельно.
    eras = data.get("eras", {})
    if not isinstance(eras, dict):
        errors.append("eras: ожидался словарь id_эры -> {name, color}")
        eras = {}
    else:
        for eid, era_info in eras.items():
            where = f"Эра '{eid}'"
            if not isinstance(eid, str) or not eid.isdigit() or int(eid) < 1:
                errors.append(f"{where}: ключ должен быть строкой положительного целого числа (как era квеста)")
            if not isinstance(era_info, dict):
                errors.append(f"{where}: должна быть объектом {{name, color}}")
                continue
            if not era_info.get("name"):
                errors.append(f"{where}: не указано name")
            color = era_info.get("color")
            if not isinstance(color, str) or not _HEX_COLOR_RE.match(color):
                errors.append(f"{where}: color должен быть цветом вида '#rrggbb'")

    # era квеста - ЧИСТО визуальная колонка графа (как tier у техов), никак не
    # связана с технологиями - валидируется только как положительное целое (само
    # оформление этой era - см. eras выше). requires - граф, буквально как
    # techs.*.requires (см. find_requires_cycle ниже) - только ссылается на
    # ДРУГОЙ КВЕСТ, а не на технологию. icon/bg/border/tooltip_color -
    # необязательные per-квестовые оверрайды оформления узла графа (icon -
    # {grid,palette}, та же пиксель-иконка, что у ресурсов/построек/мобов, см.
    # check_icon; подменяет центральный значок узла ЛЮБОГО статуса, включая
    # locked - жетон статуса в углу узла отдельным значком остаётся всегда,
    # см. web_ui.html:buildQuestNode; bg/border подменяют цвет узла целиком
    # вместо цвета эры; tooltip_color - цвет текста всплывающей подсказки с
    # именем квеста, НЕ общая настройка - каждый квест красит свою подсказку
    # сам).
    quests = data.get("quests", {})
    for qid, q in quests.items():
        record_id(qid, "квест")
        check_id("Квест", qid)
        where = f"Квест '{qid}'"
        if not q.get("name"):
            errors.append(f"{where}: не указано название")
        if not q.get("description"):
            errors.append(f"{where}: не указано описание")
        era = q.get("era")
        if not isinstance(era, int) or isinstance(era, bool) or era < 1:
            errors.append(f"{where}: era должен быть положительным целым числом")
        check_icon(q.get("icon"), where)
        for field in ("bg", "border", "tooltip_color"):
            val = q.get(field)
            if val is not None and (not isinstance(val, str) or not _HEX_COLOR_RE.match(val)):
                errors.append(f"{where}: {field} должен быть цветом вида '#rrggbb' (или отсутствовать)")
        requires = q.get("requires", [])
        if not isinstance(requires, list):
            errors.append(f"{where}: requires должен быть списком id квестов (или отсутствовать)")
        else:
            for r in requires:
                if r not in quests:
                    errors.append(f"{where}: requires ссылается на неизвестный квест '{r}'")
                elif r == qid:
                    errors.append(f"{where}: requires не может ссылаться на сам себя")
        check_conditions(q.get("conditions"), where)
        reward_case = q.get("reward_case")
        if reward_case not in loot_cases:
            errors.append(f"{where}: неизвестный кейс с лутом '{reward_case}' в reward_case")

    quest_cyclic = find_requires_cycle(quests)
    if quest_cyclic:
        names = ", ".join(f"'{quests[q]['name']}'" for q in quest_cyclic if q in quests)
        errors.append(f"Цикл в зависимостях квестов (выполнить их будет невозможно): {names}")

    hpt = time_settings.get("hours_per_turn")
    if not isinstance(hpt, int) or not (1 <= hpt <= 24):
        errors.append("time.hours_per_turn должен быть целым числом от 1 до 24")
    start = time_settings.get("day_start_hour")
    end = time_settings.get("day_end_hour")
    if not isinstance(start, int) or not (0 <= start <= 23):
        errors.append("time.day_start_hour должен быть целым числом от 0 до 23")
    if not isinstance(end, int) or not (1 <= end <= 24):
        errors.append("time.day_end_hour должен быть целым числом от 1 до 24")
    if isinstance(start, int) and isinstance(end, int) and start >= end:
        errors.append("time.day_start_hour должен быть меньше time.day_end_hour")

    default_delay = ui_settings.get("default_action_delay_ms")
    if default_delay is not None and (not isinstance(default_delay, (int, float)) or default_delay < 0):
        errors.append("ui.default_action_delay_ms должен быть null или неотрицательным числом")

    # Глобальные звуки (фон/открытие лутбокса/клик по кнопке, см. game/content.py:
    # UI_SOUNDS) - та же форма значения, что manual_actions.*.sound.
    ui_sounds = ui_settings.get("sounds", {})
    if not isinstance(ui_sounds, dict):
        errors.append("ui.sounds должен быть словарём")
    else:
        for key in ("background_music", "lootbox_open", "button_click", "mine_hit"):
            check_sound(ui_sounds.get(key), f"ui.sounds.{key}")

    for id_, owners in id_owners.items():
        distinct = sorted(set(owners))
        if len(distinct) > 1:
            errors.append(
                f"id '{id_}' занят сразу несколькими сущностями ({', '.join(distinct)}) - "
                "id должны быть уникальны во всём проекте, а не только внутри одной вкладки"
            )

    return errors
