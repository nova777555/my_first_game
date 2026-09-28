# Правила игры. Ничего не печатает и ничего не читает с клавиатуры -
# только принимает GameState и возвращает результат. Это позволяет
# позже приделать любой интерфейс (консоль, браузер) не трогая этот файл.
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field

from .content import (
    ALLOY_RECIPES, BREAKDOWN_TYPES, BUILDINGS, CAST_RECIPES, COMBAT_MOVES, COMBAT_SETTINGS, ENCHANTMENTS,
    ENERGY_RECIPES, GENETIC_TRAITS, GENETICS_SETTINGS, LOCATIONS, LOOT_CASES, MANUAL_ACTIONS,
    MELT_RECIPES, MINE_FIELD, MINING_INFO, MOBS, MUTATIONS, QUESTS, REACTOR_ACCIDENTS, RESOURCE_FRACTIONAL,
    RESOURCE_MOLTEN, RESOURCE_NAMES, RESOURCE_REACTOR_CATALYST, RESOURCE_REACTOR_COOLANT_FLUID,
    RESOURCE_REACTOR_COMPONENT, RESOURCE_REPLICATION_COST, RESOURCE_SOUL_KIND,
    RESOURCE_STORAGE, RITUAL_RECIPES, SKILLS, SOUL_RECIPES, SPECIES, TECHS, TIME, TOOLS,
)
from .state import HISTORY_MAX_ENTRIES, GameState

MAX_PINNED = 10

# Потолок на количество ОДНОГО ресурса в state.resources - без него очень долгая
# партия (или зацикленный крафт за энергию/автокрафт) могла бы разогнать число до
# величин, которые начинают ощутимо есть память/CPU на сериализации в save.json.
# Единая точка применения - _add_resource(); нигде больше resources[r] не должен
# расти напрямую (списание/уменьшение клампа не требует - оно не может превысить потолок).
MAX_RESOURCE_AMOUNT = 1_000_000_000

# Локация, к которой всегда привязаны поиск и добыча жил - в отличие от обычных
# ручных действий, эта механика не хранится в content.json как отдельная запись
# (её нечем удалить в редакторе), а просто всегда всплывает в UI для локации с
# этим id. Сама локация - обычная запись в content.json, но её id защищён
# content_validation.py (проверяется, что она существует) и неизменяем в редакторе.
# Апгрейд инструментов (в т.ч. кирки) с этой локацией больше НЕ связан - см.
# upgradable_tools/upgrade_tool - это отдельная, полностью общая для ЛЮБОГО
# инструмента механика, не завязанная ни на одну конкретную локацию.
MINE_LOCATION_ID = "shaft"

# Локация, в которой игрок вручную выбирает, какой рецепт запустить в свободном
# энергетическом преобразователе - как и MINE_LOCATION_ID, эта механика не хранится
# как обычные ручные действия content.json (её нечем удалить в редакторе), а просто
# всегда всплывает в UI для локации с этим id. Сама локация - обычная запись в
# content.json, но её id защищён content_validation.py и неизменяем в редакторе.
# Вложена в локацию "mechanisms" (Механизмы), которая сама по себе теперь просто
# пустой контейнер без собственной логики - по прямому пожеланию "рецепты должны
# быть на уровень глубже", не прямо в Механизмах.
CONVERTER_LOCATION_ID = "converter"

# Единственный ресурс с особым поведением (не копится без хранилищ, всегда закреплён
# на панели, не участвует в автокрафте) - остальной код узнаёт его по этому id, а не
# по вписанному тексту "energy", чтобы такие проверки было видно через один grep.
ENERGY_RESOURCE_ID = "energy"

# Постройка-помощник: пока работает хотя бы одна копия, интерфейс показывает
# ЛЮБОЙ ресурс (не только ингредиенты в списке ручных действий - ещё и цену
# построек/технологий/улучшений/ремонта, и сами плитки ресурсов на складе/
# закреплённой панели) кликабельным - клик перекидывает к ручному действию,
# которое производит его с наивысшим приоритетом СЕЙЧАС (см. recipe_board_target
# ниже и tools/web_ui.py:manual_action_cost_list/cost_list), прокручивая и
# подсвечивая его карточку (переключая локацию, если действие в другой). Название
# постройки - обычный редактируемый контент (её можно переименовать в
# редакторе), id - нет: код узнаёт её только по id, как MINE_LOCATION_ID/
# ENERGY_RESOURCE_ID выше.
RECIPE_BOARD_BUILDING_ID = "recipe_board"

# Линейка навыка (content.json["skills"]), которую читает боевая механика (охота
# на мобов/боссов) для расчёта урона - см. skill_level_effects/attack_encounter.
# Сама система навыков (skill_level/add_skill_xp) полностью generic и ничего не
# знает про бой - это единственное место, где движок жёстко связывает КОНКРЕТНУЮ
# линейку с КОНКРЕТНОЙ механикой, тем же принципом, что и остальные 4 id выше.
# Другая будущая механика на прокачке заведёт себе свой skill_id и свою такую же
# константу - без изменений в самой системе навыков.
COMBAT_SKILL_ID = "combat"

def action_priority(state: GameState, action_id: str) -> int:
    """Текущий приоритет действия для автокрафта (см. resource_recipe) - собственный
    выбор ИГРОКА (state.action_priority, редактируется через set_action_priority в
    UI, НЕ тратит ход), если он есть, иначе значение по умолчанию, заданное автором
    контента (manual_actions.*.priority, 0, если не указано). action_id может быть и
    ключом рецепта преобразователя "energy_recipe:<id>" или репликации "replicator:<ресурс>"
    (grid_recipe_key): своего значения в контенте у них нет, по умолчанию 0 (см.
    converter_preferred_resources/_autocraft_source_options)."""
    if action_id in state.action_priority:
        return state.action_priority[action_id]
    if parse_grid_recipe_key(action_id)[0] in ("energy_recipe", "replicator", "ritual_recipe"):
        return 0
    return MANUAL_ACTIONS[action_id].get("priority", 0)


def set_action_priority(state: GameState, action_id: str, priority: int | None) -> ActionResult:
    """Переопределяет приоритет ОДНОГО действия (или рецепта преобразователя по ключу
    "energy_recipe:<id>") для ЭТОГО сохранения (не трогает content.json - у каждого
    игрока свой выбор). priority=None сбрасывает переопределение, возвращая действие к
    значению по умолчанию из контента. Как toggle_pin/toggle_autocraft - настройка, не
    тратит ход."""
    kind, target_id = parse_grid_recipe_key(action_id) if isinstance(action_id, str) else (None, None)
    if kind == "energy_recipe" and target_id in ENERGY_RECIPES:
        name = ENERGY_RECIPES[target_id]["name"]
    elif kind == "replicator" and target_id in RESOURCE_REPLICATION_COST:
        name = f"Репликация: {RESOURCE_NAMES[target_id]}"
    elif kind == "manual_action" and target_id in MANUAL_ACTIONS:
        name = MANUAL_ACTIONS[target_id]["name"]
    else:
        return ActionResult(False, "Неизвестное действие.")
    if priority is None:
        state.action_priority.pop(action_id, None)
        return ActionResult(True, f"Приоритет «{name}» сброшен к значению по умолчанию.")
    state.action_priority[action_id] = priority
    return ActionResult(True, f"Приоритет «{name}»: {priority}.")


def energy_priority(state: GameState, building_id: str) -> int:
    """Текущий приоритет ТИПА энергетической машины (см. _process_energy_jobs) -
    зеркало action_priority: собственный выбор игрока (state.energy_priority,
    редактируется через set_energy_priority в UI, НЕ тратит ход), если он есть,
    иначе значение по умолчанию, заданное автором контента (buildings.*.energy_priority,
    0, если не указано). При нехватке общего пула энергии на все активные крафты
    сразу типы с БОЛЬШИМ приоритетом тянут из пула ПЕРВЫМИ."""
    if building_id in state.energy_priority:
        return state.energy_priority[building_id]
    return BUILDINGS[building_id].get("energy_priority", 0)


def set_energy_priority(state: GameState, building_id: str, priority: int | None) -> ActionResult:
    """Переопределяет приоритет ОДНОГО типа энергетической машины для ЭТОГО
    сохранения - зеркало set_action_priority. priority=None сбрасывает
    переопределение. Не тратит ход."""
    if building_id not in BUILDINGS:
        return ActionResult(False, "Неизвестная постройка.")
    name = BUILDINGS[building_id]["name"]
    if priority is None:
        state.energy_priority.pop(building_id, None)
        return ActionResult(True, f"Приоритет «{name}» сброшен к значению по умолчанию.")
    state.energy_priority[building_id] = priority
    return ActionResult(True, f"Приоритет «{name}»: {priority}.")


# Шанс (0..1) потребления/получения КОНКРЕТНОГО ресурса в cost/gain ручного действия -
# необязательные словари resource_id -> шанс (см. manual_actions.*.cost_chance/gain_chance
# в content.json). Отсутствие ключа (или отсутствие самого словаря) значит 1.0 (100%,
# как было всегда до этой механики) - см. is_manual_action_deterministic/do_manual_action.
def is_manual_action_deterministic(action_id: str) -> bool:
    """Действие детерминировано, если у КАЖДОГО ресурса в cost_chance/gain_chance шанс
    равен 1.0 (100%) - в том числе если этих полей вообще нет (по умолчанию). Только
    такие действия автокрафт вправе выбрать рецептом ресурса (см. resource_recipe) -
    вся его бухгалтерия (fires, compute_per_fire, фоновые задачи) устроена на
    ГАРАНТИРОВАННОМ результате каждой "прокрутки"; действие, которое иногда не
    потребляет ингредиент или не выдаёт результат, эту гарантию нарушает. Автокрафт
    поэтому смотрит на ЦЕЛЫЙ рецепт разом - вероятностный cost исключает действие из
    resource_recipe точно так же, как вероятностный gain, даже если КОНКРЕТНЫЙ нужный
    автокрафту ресурс в gain при этом гарантирован на 100%. По той же причине
    случайный ДИАПАЗОН ({"min","max"}, см. _roll_range_amounts) хоть в одном
    ресурсе gain тоже делает действие недетерминированным - fires/compute_per_fire
    автокрафта посчитаны на ГАРАНТИРОВАННОЕ число за прокрутку, а не на
    распределение."""
    action = MANUAL_ACTIONS[action_id]
    chances = list(action.get("cost_chance", {}).values()) + list(action.get("gain_chance", {}).values())
    if not all(c >= 1.0 for c in chances):
        return False
    return not any(_is_range_amount(v) for v in action.get("gain", {}).values())


def _roll_chance_amounts(amounts: dict[str, int], chances: dict[str, float]) -> dict[str, int]:
    """Применяет шанс (0..1, по умолчанию 1.0 - см. is_manual_action_deterministic) К
    КАЖДОМУ ресурсу НЕЗАВИСИМО: ресурс, чей бросок не удался, целиком выпадает из
    результата на ЭТУ попытку (а не уменьшается пропорционально - партия либо
    случается, либо нет). Используется ТОЛЬКО do_manual_action (реальное выполнение
    игроком или повторной попыткой из очереди pending_actions) - автокрафт
    (_ensure_available/_process_autocraft_jobs) сюда никогда не доходит, потому что
    resource_recipe вообще не выбирает действия с шансом < 100% (см. выше)."""
    return {r: amount for r, amount in amounts.items() if random.random() < chances.get(r, 1.0)}


# Случайное количество produce ({"min", "max"} вместо плоского числа - см.
# content_validation.check_amount_range) - НЕЗАВИСИМО от cost_chance/gain_chance
# выше: шанс решает, случится ли начисление вообще, диапазон - сколько именно,
# если да. Разрешено только на produce-стороне (buildings.produces/
# manual_actions.gain/energy_recipes.produces/soul_recipes.produces), никогда в
# cost/consumes - поэтому вся арифметика ниже (тех-эффекты/апгрейды/округление)
# трогает диапазон только там, где реально читает produces/gain.
def _is_range_amount(value) -> bool:
    return isinstance(value, dict)


def _add_amount(value, delta: float):
    """Плюсует ФИКСИРОВАННЫЙ delta (тех-эффект) к значению, которое может быть
    диапазоном - сдвигает ОБЕ границы, число как раньше."""
    if _is_range_amount(value):
        return {"min": value["min"] + delta, "max": value["max"] + delta}
    return value + delta


def _scale_amount(value, factor: float):
    """Домножает значение (апгрейд scale/energy_efficiency/generation_efficiency) -
    домножает ОБЕ границы диапазона, число как раньше."""
    if _is_range_amount(value):
        return {"min": value["min"] * factor, "max": value["max"] * factor}
    return value * factor


def _amount_estimate(value) -> float:
    """Одно число вместо диапазона там, где значение ДОЛЖНО остаться одним числом
    для отображения (theoretical_rates/energy_breakdown - это прогноз, не момент
    начисления, см. _roll_range_amounts) - середина [min, max]."""
    if _is_range_amount(value):
        return (value["min"] + value["max"]) / 2
    return value


def _roll_range_amounts(amounts: dict[str, int | float | dict]) -> dict[str, int | float]:
    """Разворачивает диапазон {"min", "max"} в конкретное случайное количество РОВНО
    ОДИН РАЗ - самый последний шаг перед начислением ресурса игроку (после шанса,
    после тех-эффектов/апгрейдов, после округления границ через _round_amounts), так
    что вся промежуточная арифметика видит диапазон целиком, а не уже случайное
    число. Целочисленные ресурсы получают random.randint (границы ВКЛЮЧИТЕЛЬНО),
    дробные (RESOURCE_FRACTIONAL) - random.uniform. Значения без диапазона проходят
    как есть, не трогая random - контент без единого диапазона продолжает не
    бросать кости вообще, как и с cost_chance/gain_chance (см.
    test_action_without_chance_fields_never_rolls_dice)."""
    result: dict[str, int | float] = {}
    for r, v in amounts.items():
        if _is_range_amount(v):
            lo, hi = v["min"], v["max"]
            if RESOURCE_FRACTIONAL.get(r, False):
                result[r] = random.uniform(lo, hi)
            else:
                result[r] = random.randint(int(lo), int(hi))
        else:
            result[r] = v
    return result


# Какое ручное действие производит данный ресурс из ДРУГОГО сырья - используется автокрафтом:
# при нехватке промежуточного ресурса он рекурсивно докрафчивается из более простого сырья.
# Действия с пустой ценой (голый сбор вроде "Рубить дерево") сюда не попадают: у них нет
# сырья, которое можно было бы подставить - их цена это сам ход, и его так просто не обойти.
# Действия с шансом < 100% на любой ресурс в cost ИЛИ gain (см. is_manual_action_deterministic)
# тоже сюда не попадают - по прямому пожеланию: автокрафт должен полагаться только на
# ГАРАНТИРОВАННЫЕ рецепты, иначе он мог бы бесконечно "ждать" ресурс, который выдаётся не
# каждую попытку. Ресурс, у которого ЕСТЬ только вероятностный рецепт, для автокрафта
# неотличим от обычного сырья без рецепта вообще ("no_recipe") - автокрафт честно
# напишет "не хватает", а не будет пытаться докрафтить его сам.
# Многоходовые действия (duration_turns > 1, см. state.manual_jobs) сюда ПОПАДАЮТ, но
# автокрафт ведёт их не прокрутками, а ПАРТИЯМИ - запускает настоящий manual_job (как
# клик игрока), по одной партии за раз, и ждёт её завершения (kind="long_action" в
# _discover_autocraft_graph, см. _aggregate_autocraft_demand/_commit_autocraft_plan).
# manual_actions.*.excluded_from_autocraft: true - явный ручной запрет автору контента,
# НЕ связанный с детерминизмом/длительностью выше: действие может быть полностью
# гарантированным и мгновенным и всё равно не годиться для автокрафта (например, "убить
# босса" не должно происходить само по себе фоновой задачей) - автокрафт ведёт себя так,
# будто рецепта для этого ресурса вообще нет (as no_recipe), кнопка "Перейти к рецепту"
# (recipe_board_target) этим НЕ гейтится - игрок всё ещё может найти и выполнить действие
# сам, просто автокрафт никогда не выберет его сам.
# Если РЕСУРС производят НЕСКОЛЬКО таких действий (несколько рецептов на один и тот же
# ресурс), выбирается действие с НАИБОЛЬШИМ action_priority(state, ...) - у игрока
# (state.action_priority) приоритет НАД значением по умолчанию из content.json. При
# РАВНОМ приоритете выбор произвольный (первое найденное в порядке manual_actions в
# content.json) - явного пожелания различать равные приоритеты не было. Раньше это был
# статический словарь (RESOURCE_RECIPE, вычислялся один раз при импорте) - стал функцией
# от state именно из-за игрока: приоритет теперь не только в content.json, а ещё и
# переопределяется ПЕР-СОХРАНЕНИЕ, так что результат больше не может быть константой на
# весь процесс. Пересчитывается заново на каждый вызов - при нынешних объёмах контента
# (единицы-десятки ручных действий) это не проблема (см. другие похожие оговорки в CLAUDE.md).
# usable_only - только рецепты, которые автокрафт может выполнить ПРЯМО СЕЙЧАС
# (_autocraft_action_usable): так их видят заказ и предпросмотр. Без него - все
# кандидаты независимо от доступности (приоритеты в UI).
def resource_recipe(state: GameState, usable_only: bool = False) -> dict[str, str]:
    recipe: dict[str, str] = {}
    best_priority: dict[str, int] = {}
    cyclic = autocraft_cyclic_recipes() if usable_only else frozenset()
    for action_id, action in MANUAL_ACTIONS.items():
        if action_id in cyclic:
            continue
        if not action["cost"]:
            continue
        if action.get("excluded_from_autocraft"):
            continue
        if not is_manual_action_deterministic(action_id):
            continue
        if usable_only and not _autocraft_action_usable(state, action):
            continue
        priority = action_priority(state, action_id)
        for resource_id in action["gain"]:
            if resource_id not in recipe or priority > best_priority[resource_id]:
                recipe[resource_id] = action_id
                best_priority[resource_id] = priority
    return recipe


def autocraft_cyclic_recipes() -> frozenset[str]:
    """Рецепты, замкнутые в круг («9 слитков -> блок -> 9 слитков», «росток -> 2 семени ->
    росток», «1 руда -> 2 руды»): автокрафт НЕ берёт их источником вовсе (прямое пожелание,
    2026-09-25) - ресурс, который делается только ими, для заказа сырьё. Иначе заказ
    решал бы, какое звено круга считать сырьём, и мог выбрать неудачно: отказать, хотя
    всё есть, или оформиться и молча встать, съев то, что нужно ему самому.

    Считается по контенту, не по state (как is_manual_action_deterministic): ребро
    «ингредиент -> выход» от каждого рецепта, который автокрафт в принципе может взять
    источником - детерминированное действие с ценой, крафт за энергию и ритуал, которые
    автокрафт выбирает для ресурса, репликация; рецепт в круге, если хоть одно его ребро
    ведёт туда, откуда можно вернуться к его же ингредиенту. Энергия ребром не считается -
    её не крафтят. Ключи - как grid_recipe_key: id действия, "energy_recipe:<id>",
    "ritual_recipe:<id>", "replicator:<ресурс>". Сам такой рецепт по-прежнему можно заказать
    напрямую («рецепт ×N») и поставить в правило центра автоматизации - он лишь не
    становится источником для других заказов."""
    edges: list[tuple[str, str, str]] = []
    for action_id, action in MANUAL_ACTIONS.items():
        if not action["cost"] or action.get("excluded_from_autocraft") or not is_manual_action_deterministic(action_id):
            continue
        edges += [(ing, out, action_id) for ing in action["cost"] for out in action["gain"]]
    for out, recipe_id in ENERGY_RECIPE_FOR_RESOURCE.items():
        key = grid_recipe_key("energy_recipe", recipe_id)
        edges += [(ing, out, key) for ing in ENERGY_RECIPES[recipe_id].get("consumes", {})]
    for out, ritual_id in ritual_recipe_for_resource().items():
        key = grid_recipe_key("ritual_recipe", ritual_id)
        edges += [(ing, out, key) for ing in _ritual_full_cost(RITUAL_RECIPES[ritual_id])]
    for matter in {b["replicator"].get("matter") for b in BUILDINGS.values() if b.get("replicator")} - {None}:
        edges += [(matter, out, grid_recipe_key("replicator", out)) for out in RESOURCE_REPLICATION_COST if out != matter]
    edges = [edge for edge in edges if edge[0] != ENERGY_RESOURCE_ID]
    graph: dict[str, set[str]] = {}
    for ing, out, _ in edges:
        graph.setdefault(ing, set()).add(out)
    reach_of: dict[str, set[str]] = {}

    def reach(start: str) -> set[str]:
        """Все ресурсы, которые можно сделать из start (цепочкой рецептов)."""
        if start not in reach_of:
            seen, stack = set(), [start]
            while stack:
                for nxt in graph.get(stack.pop(), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            reach_of[start] = seen
        return reach_of[start]

    return frozenset(key for ing, out, key in edges if ing == out or ing in reach(out))


def _cyclic_recipe_name(resource_id: str, cyclic: frozenset) -> str | None:
    """Имя рецепта из круга (autocraft_cyclic_recipes), которым делается resource_id, или None."""
    for action_id, action in MANUAL_ACTIONS.items():
        if action_id in cyclic and resource_id in action["gain"]:
            return action["name"]
    energy_id = ENERGY_RECIPE_FOR_RESOURCE.get(resource_id)
    if energy_id is not None and grid_recipe_key("energy_recipe", energy_id) in cyclic:
        return ENERGY_RECIPES[energy_id]["name"]
    ritual_id = ritual_recipe_for_resource().get(resource_id)
    if ritual_id is not None and grid_recipe_key("ritual_recipe", ritual_id) in cyclic:
        return RITUAL_RECIPES[ritual_id]["name"]
    return None


def _cycle_notes(missing: dict) -> list[str]:
    """Для «не хватает»: какие из недостающих ресурсов делаются только рецептом из круга -
    «Росток» (рецепт «Проращивание» замкнут в круг)."""
    cyclic = autocraft_cyclic_recipes()
    notes = []
    for resource_id in missing:
        name = _cyclic_recipe_name(resource_id, cyclic)
        if name:
            notes.append(f"«{RESOURCE_NAMES.get(resource_id, resource_id)}» (рецепт «{name}» замкнут в круг)")
    return notes


def cycle_note(missing: dict) -> str:
    """Пояснение к «не хватает»: у ресурса есть рецепт, но он замкнут в круг, и автокрафт
    его не берёт (autocraft_cyclic_recipes) - иначе игрок не понял бы, почему «Росток» не
    докрафтился, хотя рецепт ростка есть. Пустая строка - пояснять нечего."""
    notes = _cycle_notes(missing)
    return f" Автокрафт не делает {', '.join(notes)}." if notes else ""


# Как resource_recipe, но для кнопки "Перейти к рецепту" (RECIPE_BOARD_BUILDING_ID) -
# в отличие от resource_recipe (нужен автокрафту ТОЛЬКО для рекурсивного докрафта
# сырья, поэтому голый сбор - "Рубить дерево" и т.п. - туда не попадает, докрафчивать
# в нём нечего), навигации сгодится и голый сбор: игрок жмёт на ресурс, чтобы просто
# УЗНАТЬ, каким действием его добыть, а не подставить как сырьё для чего-то ещё.
# Второе отличие - resource_recipe нарочно смотрит на ВЕСЬ MANUAL_ACTIONS (в том
# числе то, что ещё не открыто технологией/постройкой), а сюда попадают только
# СЕЙЧАС доступные действия (available_manual_actions) - прыгать в карточку,
# которой ещё нет в списке действий, было бы некуда. Правило выбора при нескольких
# действиях на один ресурс - то же самое (наибольший action_priority, при равенстве -
# первое по порядку в content.json).
def recipe_board_target(state: GameState) -> dict[str, str]:
    recipe: dict[str, str] = {}
    best_priority: dict[str, int] = {}
    for action_id in available_manual_actions(state):
        action = MANUAL_ACTIONS[action_id]
        if not is_manual_action_deterministic(action_id):
            continue
        priority = action_priority(state, action_id)
        for resource_id in action["gain"]:
            if resource_id not in recipe or priority > best_priority[resource_id]:
                recipe[resource_id] = action_id
                best_priority[resource_id] = priority
    return recipe


# Какой рецепт крафта за энергию (content.json["energy_recipes"]) производит данный
# ресурс - альтернативный источник для автокрафта, когда у ресурса нет обычного рецепта
# (RESOURCE_RECIPE): вместо "нет рецепта" автокрафт может сам запустить этот рецепт в
# свободном энергетическом преобразователе (см. _ensure_available/start_energy_recipe).
ENERGY_RECIPE_FOR_RESOURCE: dict[str, str] = {}
for _recipe_id, _recipe in ENERGY_RECIPES.items():
    for _resource_id in _recipe.get("produces", {}):
        ENERGY_RECIPE_FOR_RESOURCE.setdefault(_resource_id, _recipe_id)


def converter_preferred_resources(state: GameState, recipe: dict[str, str] | None = None) -> frozenset[str]:
    """Ресурсы, которые автокрафт делает на преобразователе В ПЕРВУЮ очередь, хотя у них
    есть и обычный рецепт (resource_recipe): игрок поставил рецепту преобразователя
    ("energy_recipe:<id>") приоритет СТРОГО выше, чем у обычного. При равенстве - обычный
    рецепт, преобразователь остаётся запасным путём; у предпочтённого преобразователя
    запасным становится обычный рецепт (см. _order_route)."""
    recipe = resource_recipe(state) if recipe is None else recipe
    return frozenset(
        resource_id for resource_id, recipe_id in ENERGY_RECIPE_FOR_RESOURCE.items()
        if resource_id in recipe
        and action_priority(state, grid_recipe_key("energy_recipe", recipe_id)) > action_priority(state, recipe[resource_id])
    )

# Мутации по НЕУПОРЯДОЧЕННОЙ паре видов (frozenset - {parent_a, parent_b} и
# {parent_b, parent_a} это одна и та же запись) - несколько мутаций на одну пару
# допустимы (например, разные требования requires_tech открывают разные исходы по
# мере прогресса). Строится один раз при импорте, как ENERGY_RECIPE_FOR_RESOURCE
# выше - MUTATIONS (список, не словарь) сам по себе не подходит для быстрого
# поиска "что может получиться из ЭТИХ двух видов" при каждом скрещивании.
MUTATIONS_BY_PAIR: dict[frozenset, list[dict]] = {}
for _mutation in MUTATIONS:
    _key = frozenset({_mutation["parent_a"], _mutation["parent_b"]})
    MUTATIONS_BY_PAIR.setdefault(_key, []).append(_mutation)

# 5 жёстко зашитых признаков генотипа особи (закрытый список, как 3 типа улучшений
# построек scale/energy_efficiency/generation_efficiency) - "species" ссылается на
# content.json["species"] (каждый вид - одна аллель этого признака), остальные четыре -
# на content.json["genetic_traits"] (общий каталог аллелей на всю игру - ровно как в
# Forestry срок жизни пчелы отделён от её вида). КАКИЕ из них может нести особь
# вида от природы, решает пул вида (species.*.gene_pool, см. species_gene_pool).
GENOTYPE_TRAIT_IDS = ("species", "lifespan", "output_multiplier", "active_period", "environment")


def trait_alleles(trait_id: str) -> dict[str, dict]:
    """allele_id -> {"name", "dominant", "weight", ...} для ОДНОГО признака -
    для "species" строится на лету из SPECIES (id вида = id его аллели), для
    остальных трёх берётся из GENETIC_TRAITS[trait_id]["alleles"]."""
    if trait_id == "species":
        return {
            sid: {"name": RESOURCE_NAMES.get(sid, sid), "dominant": sp.get("dominant", False), "weight": sp.get("weight", 0)}
            for sid, sp in SPECIES.items()
        }
    return {a["id"]: a for a in GENETIC_TRAITS.get(trait_id, {}).get("alleles", [])}


@dataclass
class ActionResult:
    ok: bool
    message: str
    # True - на действие оформлен заказ автокрафта (state.orders) и оно ЕЩЁ НЕ
    # применилось по-настоящему, хотя ok=True (игроку это подано как принятое, а не
    # провалившееся действие). Нужно отдельно от ok, чтобы _finalize_order мог
    # отличить "выполнено" от "оформило себе новый заказ" - оба варианта
    # возвращают ok=True, но означают разное.
    pending: bool = False
    # Приз, выпавший из лутбокса (claim_quest_reward) - {"resource", "amount"} или
    # None у ЛЮБОГО другого действия движка. Нужен UI отдельно от message (который
    # уже содержит тот же приз текстом), чтобы анимировать прокрутку барабана до
    # РЕАЛЬНОГО результата броска, а не подделывать его заново на клиенте.
    prize: dict | None = None
    # Данные удара attack_encounter для анимации в UI (летящий урон, тряска
    # портрета моба, вспышка на ваншот) - {"damage", "killed", "instant_kill",
    # "remaining_hp", "max_hp"} или None у ЛЮБОГО другого действия. Тот же
    # принцип, что prize: сообщение message уже содержит то же самое текстом,
    # но фронтенду нужны отдельные машиночитаемые числа, а не парсинг строки.
    combat: dict | None = None
    # Результат попытки зачарования (enchant_tool) - {"tool_id", "circles": [{"enchant_id",
    # "level"}, x3], "success", "applied": {"enchant_id","level"} | None} или None у ЛЮБОГО
    # другого действия. Сервер решает исход СРАЗУ (тот же принцип, что prize/_roll_loot_case_prize) -
    # UI анимирует раскрытие трёх кругов до уже готового результата, не подделывая его сам.
    enchant: dict | None = None
    # Структурные данные результата скрещивания (breed_creatures) - {"mutated",
    # "parent_a_species_id", "parent_a_name", "parent_b_species_id", "parent_b_name",
    # "child_species_id", "child_name"} или None у ЛЮБОГО другого действия. Тот же
    # принцип, что prize/combat/enchant выше.
    breed: dict | None = None


@dataclass
class AutocraftFailure:
    """Почему заказ автокрафта под resource_id оформить нельзя - конкретная причина
    нужна, чтобы игрок видел не общее "не хватает ресурсов", а что именно чинить:
    построить сборщик/купить «Автоматизацию», доисследовать технологию, нарастить
    процессоры или добыть сырьё (см. _plan_order/_place_order). "no_processor" -
    отдельный случай: пока в игре нет ни одного работающего процессора
    (compute_capacity == 0), автокрафт как система ещё не "появился" для игрока -
    _pay_or_defer показывает голое "не хватает ресурсов на X.", как при ручном крафте.
    building_id - только для "building_missing": какой постройки не хватает рецепту.
    "not_automated" - рецепт делается на механизме, который автокрафт пока не умеет
    запускать (нет сборщика/улучшения «Автоматизация», см. location_autocraft_capacity);
    extra = {"where", "hint"} для текста.
    Энергия (ENERGY_RESOURCE_ID) - не сырьё "без рецепта" и никогда не причина отказа:
    заказ её ЖДЁТ, даже если источника нет или за раз нужно больше, чем вмещают
    хранилища, - игрок видит почему в статусе заказа (energy_wait_note).
    missing - ВСЁ, чего не хватает и что автокрафт сделать не может (сырьё или ресурс,
    чей рецепт ему сейчас недоступен), resource_id -> недостача; заполняет _plan_order."""
    resource_id: str
    reason: str  # "no_processor" | "no_recipe" | "not_automated" | "tech_locked" | "building_missing" | "ritual_pedestals" | "random_result" | "no_output" | "insufficient_compute" | "no_free_slots" | "in_progress" | "no_storage_space"
    building_id: str | None = None
    extra: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)


_AUTOCRAFT_REASON_TEXT = {
    "no_processor": "автокрафт пока недоступен - в игре ещё нет ни одного работающего процессора",
    "no_recipe": "не хватает: {missing}",
    "not_automated": "«{name}» делается на «{where}» - для автокрафта {hint}",
    "tech_locked": "рецепт «{name}» ещё не изучен",
    "building_missing": "рецепту «{name}» нужна работающая постройка «{building}»",
    "insufficient_compute": "заказ слишком большой для процессоров: нужно {size}, мощность {capacity}",
    "no_storage_space": "на складе не хватит места под «{name}»: заказ выдаст {need}, свободно {free} - освободи место или расширь склад",
    "no_free_slots": "все слоты заказов процессоров заняты ({slots}) - дождись выполнения или отмени заказ",
    "in_progress": "«{name}» уже докрафтывается в фоне",
    "ritual_pedestals": "ритуалу «{ritual}» нужно {need} пьедесталов в кольце, у «{building}» их {have}",
    "random_result": "у рецепта «{name}» случайный результат - автокрафт его не делает",
    "no_output": "«{name}» не кладётся на склад - заказать нечего",
}


def describe_autocraft_failure(failure: AutocraftFailure) -> str:
    name = RESOURCE_NAMES.get(failure.resource_id, failure.resource_id)
    template = _AUTOCRAFT_REASON_TEXT[failure.reason]
    building = BUILDINGS[failure.building_id]["name"] if failure.building_id in BUILDINGS else failure.building_id
    missing = _format_shortfall(failure.missing) if failure.missing else f"«{name}»"
    text = template.format(name=name, building=building, missing=missing, **failure.extra)
    notes = _cycle_notes(failure.missing) if failure.reason == "no_recipe" else []
    if notes:
        text += f"; автокрафт не делает {', '.join(notes)}"
    return text


def _add_resource(resources: dict[str, int], resource_id: str, amount: int) -> None:
    """Прибавляет amount к resources[resource_id], но не выше MAX_RESOURCE_AMOUNT -
    единственная точка, через которую должно идти любое НАЧИСЛЕНИЕ (не списание)
    ресурса, чтобы потолок применялся одинаково везде: ручные действия, автопроизводство
    построек, автокрафт, крафт за энергию, добыча жил."""
    resources[resource_id] = min(MAX_RESOURCE_AMOUNT, resources.get(resource_id, 0) + amount)


def _gain(resources: dict[str, int], amounts: dict[str, int]) -> None:
    for r, amount in amounts.items():
        _add_resource(resources, r, amount)


def lab_tier_capacity(state: GameState) -> int:
    """Наивысший тир среди РАБОТАЮЩИХ построек с полем lab_tier - в отличие от
    compute_power/energy_storage, тиры лабораторий НЕ складываются (лаборатория
    тира 2 + лаборатория тира 2 не дают тир 4), берётся максимум. Тир 1 всегда
    доступен без единой лаборатории - см. research()."""
    tiers = [BUILDINGS[b].get("lab_tier", 0) for b in state.buildings if working_count(state, b) > 0]
    return max([1, *tiers])


def compute_capacity(state: GameState) -> int:
    """Суммарная вычислительная мощность работающих построек-процессоров (поле
    compute_power) - "мозг заказов": потолок РАЗМЕРА одного заказа автокрафта (сколько
    предметов съедят все его крафты, см. _plan_order), вне зависимости от того, сколько
    ходов он займёт. Считается так же, как energy_capacity."""
    capacity = 0
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        capacity += BUILDINGS[building_id].get("compute_power", 0) * working
    return capacity


def _is_farm_building(building_id: str) -> bool:
    return BUILDINGS.get(building_id, {}).get("breeding_slots", 0) > 0


def _is_soul_collector_building(building_id: str) -> bool:
    return bool(BUILDINGS.get(building_id, {}).get("soul_collector"))


def _is_reactor_building(building_id: str) -> bool:
    return bool(BUILDINGS.get(building_id, {}).get("reactor"))


def _uses_physical_instances(building_id: str) -> bool:
    """True для построек, физические копии которых отслеживаются ПО ЭКЗЕМПЛЯРУ
    (farm_instances/soul_collector_instances/reactor_instances), а не абстрактным
    счётчиком state.broken[bid] - см. _physical_instances ниже."""
    return _is_farm_building(building_id) or _is_soul_collector_building(building_id) or _is_reactor_building(building_id)


def _physical_instances(state: GameState, building_id: str) -> dict[str, dict]:
    """Словарь физических экземпляров ИМЕННО этого building_id - farm_instances
    для ферм, soul_collector_instances для синтезаторов душ, reactor_instances
    для реакторов, пустой dict для построек без физического отслеживания
    экземпляров (обычный агрегатный state.broken[bid]). Общая точка, которой
    пользуются repairable_count/repair_blocked_types/repair/advance_turn, чтобы
    не дублировать ветвление farm-vs-soul-vs-reactor в каждой из них - сам
    список экземпляров всё ещё нужно фильтровать по building_id (все три
    структуры общие на ВСЕ типы построек своего вида, не по одной на
    building_id)."""
    if _is_farm_building(building_id):
        return state.farm_instances
    if _is_soul_collector_building(building_id):
        return state.soul_collector_instances
    if _is_reactor_building(building_id):
        return state.reactor_instances
    return {}


def _instance_in_use(state: GameState, instance_id: str, instance: dict) -> bool:
    """Работает ли физический экземпляр в этот ход (только такой может сломаться):
    в пасеке живёт хоть одна особь, в синтезаторе стоит душа, в реакторе есть топливо
    с зарядом."""
    building_id = instance["building_id"]
    if _is_farm_building(building_id):
        return any(c.get("farm_instance_id") == instance_id for c in state.creatures.values())
    if _is_soul_collector_building(building_id):
        return bool(instance.get("installed"))
    if _is_reactor_building(building_id):
        return any(
            cell is not None and reactor_component_role(cell["resource_id"]) == "fuel" and (cell.get("charge") or 0) > 0
            for cell in instance.get("grid", [])
        )
    return True


def _ensure_physical_instances(state: GameState) -> None:
    """Идемпотентно добирает недостающие физические экземпляры ВСЕХ трёх видов
    (фермы + синтезаторы душ + реакторы) - тонкая обёртка над
    _ensure_farm_instances/_ensure_soul_collector_instances/
    _ensure_reactor_instances, чтобы вызывающему (build/repair/advance_turn и
    т.п.) не нужно было помнить про все вызовы по отдельности."""
    _ensure_farm_instances(state)
    _ensure_soul_collector_instances(state)
    _ensure_reactor_instances(state)


def _ensure_farm_instances(state: GameState) -> None:
    """Синхронизирует state.farm_instances (по одной записи на КАЖДУЮ физическую
    копию постройки-фермы) с агрегатом state.buildings/state.broken, который
    остаётся авторитетным источником истины для working_count/ремонта/поломки/
    достижений/requires_building - см. CLAUDE.md. Идемпотентна: вызывается в
    начале farms_view/house_creature/purchase_farm_instance_upgrade/build/repair
    и один раз в начале advance_turn, но реально что-то добавляет только когда
    экземпляров МЕНЬШЕ, чем копий в агрегате.

    Это одновременно и создание НОВОЙ фермы (первый вызов после build()), и
    ленивая миграция СТАРЫХ сохранений (state.farm_instances пуст, но
    state.buildings["apiary"] уже есть) - в обоих случаях поведение одинаковое:
    недостающие экземпляры добираются, первые max(0, state.broken[bid] -
    уже_сломано_среди_существующих) из НОВЫХ помечаются сломанными (чтобы сумма
    broken по экземплярам совпала с агрегатом), а их upgrades копируют ТЕКУЩИЙ
    state.building_upgrades[bid] (для миграции - все старые купленные улучшения
    типа переезжают на все экземпляры этого типа поровну; для новой стройки
    список и так пуст).

    Дополнительно (только при миграции): особь, чей farm_instance_id всё ещё
    хранит СТАРОЕ значение - id ТИПА постройки, а не найденный instance_id (см.
    GameState.from_dict) - расселяется по свободным слотам только что созданных
    экземпляров этого типа (round-robin); если свободного слота не нашлось,
    считается невыселенной (None), без падения."""
    # Миграция старых записей (сохранение старше введения типов поломки) - у них
    # просто нет ключа "broken_type" вовсе, а не None явно.
    for inst in state.farm_instances.values():
        inst.setdefault("broken_type", None)
    for building_id in BUILDINGS:
        if not _is_farm_building(building_id):
            continue
        target_count = state.buildings.get(building_id, 0)
        if target_count <= 0:
            continue
        existing_ids = [iid for iid, inst in state.farm_instances.items() if inst["building_id"] == building_id]
        if len(existing_ids) >= target_count:
            continue
        already_broken = sum(1 for iid in existing_ids if state.farm_instances[iid]["broken"])
        need_broken = max(0, state.broken.get(building_id, 0) - already_broken)
        upgrades_copy = list(state.building_upgrades.get(building_id, []))
        new_ids = []
        for i in range(target_count - len(existing_ids)):
            instance_id = f"farm_{state.next_farm_instance_id}"
            state.next_farm_instance_id += 1
            state.farm_instances[instance_id] = {
                "building_id": building_id,
                "broken": i < need_broken,
                "broken_type": _roll_breakdown_type(building_id) if i < need_broken else None,
                "upgrades": list(upgrades_copy),
            }
            new_ids.append(instance_id)

        for creature in state.creatures.values():
            if creature.get("farm_instance_id") != building_id:
                continue
            creature["farm_instance_id"] = None
            for instance_id in new_ids:
                if farm_instance_slots_used(state, instance_id) < farm_instance_slot_capacity(state, instance_id):
                    creature["farm_instance_id"] = instance_id
                    break


def _ensure_soul_collector_instances(state: GameState) -> None:
    """Зеркало _ensure_farm_instances (см. её докстринг) для построек-синтезаторов
    душ (BUILDINGS[bid]["soul_collector"]) - синхронизирует
    state.soul_collector_instances с агрегатом state.buildings/state.broken,
    добирая недостающие экземпляры (и то же самое одновременно решает и
    создание новой постройки после build(), и ленивую миграцию старых
    сохранений).

    Дополнительно (только при миграции, зеркало расселения особей у ферм):
    души, ещё лежащие в УСТАРЕВШЕМ агрегатном state.soul_slots[building_id]
    (до перехода на per-instance модель, 2026-09-18), раскладываются
    round-robin по свободным слотам только что созданных экземпляров ЭТОГО
    типа - если свободных слотов не хватило на все, остаток остаётся в
    state.soul_slots[building_id] как есть (заберётся следующим вызовом, как
    только появится ещё один экземпляр/апгрейд слотов) вместо потери души."""
    for inst in state.soul_collector_instances.values():
        inst.setdefault("broken_type", None)
        inst.setdefault("installed", [])
    for building_id in BUILDINGS:
        if not _is_soul_collector_building(building_id):
            continue
        target_count = state.buildings.get(building_id, 0)
        if target_count <= 0:
            continue
        existing_ids = [iid for iid, inst in state.soul_collector_instances.items() if inst["building_id"] == building_id]
        if len(existing_ids) >= target_count:
            continue
        already_broken = sum(1 for iid in existing_ids if state.soul_collector_instances[iid]["broken"])
        need_broken = max(0, state.broken.get(building_id, 0) - already_broken)
        upgrades_copy = list(state.building_upgrades.get(building_id, []))
        new_ids = []
        for i in range(target_count - len(existing_ids)):
            instance_id = f"soul_inst_{state.next_soul_instance_id}"
            state.next_soul_instance_id += 1
            state.soul_collector_instances[instance_id] = {
                "building_id": building_id,
                "broken": i < need_broken,
                "broken_type": _roll_breakdown_type(building_id) if i < need_broken else None,
                "upgrades": list(upgrades_copy),
                "installed": [],
            }
            new_ids.append(instance_id)

        legacy = state.soul_slots.get(building_id, [])
        if legacy and new_ids:
            remaining = []
            for soul_id in legacy:
                placed = False
                for instance_id in new_ids:
                    inst = state.soul_collector_instances[instance_id]
                    if len(inst["installed"]) < soul_collector_instance_capacity(state, instance_id):
                        inst["installed"].append(soul_id)
                        placed = True
                        break
                if not placed:
                    remaining.append(soul_id)
            state.soul_slots[building_id] = remaining


def _ensure_reactor_instances(state: GameState) -> None:
    """Зеркало _ensure_soul_collector_instances (см. её докстринг) для построек-
    реакторов (BUILDINGS[bid]["reactor"]) - синхронизирует state.reactor_instances
    с агрегатом state.buildings/state.broken. Без миграционной ветки (поле новое,
    мигрировать неоткуда) - но КАЖДЫЙ новый экземпляр сразу засеивает пустую
    решётку и зоны (_ensure_reactor_grid/_ensure_reactor_zones), а не оставляет
    их до первого обращения."""
    for inst in state.reactor_instances.values():
        inst.setdefault("broken_type", None)
        inst.setdefault("grid", [])
        inst.setdefault("zones", [])
        # Сохранения, сделанные до появления охлаждающих жидкостей, приходят без
        # этого поля. Заливаем ПЕРВУЮ жидкость контента (самую дешёвую по
        # замыслу автора), а не None: иначе у уже работающего реактора молча
        # пропало бы охлаждение. Явно осушить контур игрок может сам
        # (set_reactor_coolant_fluid с None).
        if "coolant_fluid" not in inst:
            inst["coolant_fluid"] = next(iter(RESOURCE_REACTOR_COOLANT_FLUID), None)
    for building_id in BUILDINGS:
        if not _is_reactor_building(building_id):
            continue
        target_count = state.buildings.get(building_id, 0)
        if target_count <= 0:
            continue
        existing_ids = [iid for iid, inst in state.reactor_instances.items() if inst["building_id"] == building_id]
        if len(existing_ids) >= target_count:
            continue
        already_broken = sum(1 for iid in existing_ids if state.reactor_instances[iid]["broken"])
        need_broken = max(0, state.broken.get(building_id, 0) - already_broken)
        upgrades_copy = list(state.building_upgrades.get(building_id, []))
        new_ids = []
        for i in range(target_count - len(existing_ids)):
            instance_id = f"reactor_inst_{state.next_reactor_instance_id}"
            state.next_reactor_instance_id += 1
            state.reactor_instances[instance_id] = {
                "building_id": building_id,
                "broken": i < need_broken,
                "broken_type": _roll_breakdown_type(building_id) if i < need_broken else None,
                "upgrades": list(upgrades_copy),
                "grid": [],
                "zones": [],
                "coolant_fluid": next(iter(RESOURCE_REACTOR_COOLANT_FLUID), None),
            }
            new_ids.append(instance_id)
        for instance_id in new_ids:
            _ensure_reactor_grid(state, instance_id)
            _ensure_reactor_zones(state, instance_id)


def _reactor_instance_upgrade_defs(instance: dict, upgrade_type: str) -> list[dict]:
    """Определения (из content, не просто id) апгрейдов заданного type,
    КУПЛЕННЫХ у ЭТОГО экземпляра реактора - общий хелпер для
    reactor_grid_dims/reactor_zone_count ниже, оба читают чужой (не
    мультипликативный) content-эффект по типу апгрейда, а не через
    _apply_building_upgrades (та функция знает только про scale/*_efficiency)."""
    defs_by_id = {u["id"]: u for u in building_upgrade_defs(instance["building_id"])}
    return [defs_by_id[uid] for uid in instance["upgrades"] if defs_by_id.get(uid, {}).get("type") == upgrade_type]


def reactor_grid_dims(state: GameState, instance_id: str) -> tuple[int, int]:
    """(ширина, высота) решётки экземпляра - база из buildings.*.reactor +
    АБСОЛЮТНАЯ прогрессия купленных апгрейдов типа reactor_grid_size (среди
    купленных действует тот, что даёт БОЛЬШУЮ площадь - тот же принцип
    "сильнейшее побеждает", что у scale, не сумма)."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return (0, 0)
    config = BUILDINGS.get(instance["building_id"], {}).get("reactor", {})
    width, height = config.get("grid_width", 1), config.get("grid_height", 1)
    for upgrade in _reactor_instance_upgrade_defs(instance, "reactor_grid_size"):
        value = upgrade.get("value") or {}
        w, h = value.get("width"), value.get("height")
        if isinstance(w, int) and isinstance(h, int) and w * h > width * height:
            width, height = w, h
    return (width, height)


def reactor_zone_count(state: GameState, instance_id: str) -> int:
    """Число зон экземпляра - база zones_default из buildings.*.reactor, если
    куплен апгрейд типа reactor_zoning. ВЗАИМОИСКЛЮЧАЮЩИЙ выбор (см.
    purchase_reactor_instance_upgrade - покупка нового уровня стирает у
    экземпляра предыдущий купленный reactor_zoning того же типа, зеркало
    environment_emulation), поэтому среди купленных фактически не больше
    одного - max() тут просто защита от гипотетического рассинхрона, не
    основная логика."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return 1
    config = BUILDINGS.get(instance["building_id"], {}).get("reactor", {})
    count = config.get("zones_default", 1)
    for upgrade in _reactor_instance_upgrade_defs(instance, "reactor_zoning"):
        value = upgrade.get("value")
        if isinstance(value, int) and value > count:
            count = value
    return count


def reactor_telemetry_available(state: GameState, instance_id: str) -> bool:
    """Куплен ли у ЭТОГО экземпляра апгрейд типа reactor_telemetry - булево
    наличие (само число/value не используется по величине), тот же приём, что
    breakdown_immunity/_instance_has_upgrade_type (та функция уже общая на
    любой instance-словарь, не завязана на farm_instances конкретно)."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return False
    return _instance_has_upgrade_type(instance["building_id"], instance, "reactor_telemetry")


def _reactor_zone_layout(width: int, height: int, zone_count: int) -> list[int]:
    """Индекс зоны (0..zone_count-1) для КАЖДОЙ ячейки решётки, row-major -
    чистая функция от (width, height, zone_count), НИГДЕ не хранится (см.
    GameState.reactor_instances) - топология всегда пересчитывается заново, а
    не кэшируется в state, тот же принцип, что effective_building_stats.
    Зоны - смежные горизонтальные полосы строк, максимально ровные по высоте
    (если высота не делится нацело на zone_count, лишние строки достаются
    НАЧАЛЬНЫМ зонам - естественный результат целочисленного floor-division по
    номеру строки, не отдельная ветка кода); zone_count зажимается в
    [1, height], чтобы не завести зон без единой строки."""
    if width <= 0 or height <= 0:
        return []
    zone_count = max(1, min(zone_count, height))
    cells: list[int] = []
    for r in range(height):
        zone = min(zone_count - 1, r * zone_count // height)
        cells.extend([zone] * width)
    return cells


def _reactor_zone_heat_cap(state: GameState, instance_id: str, zone_index: int, layout: list[int] | None = None) -> float:
    """Порог тепла ОДНОЙ зоны, выше которого начинается деградация выхода
    (soft_threshold_pct её доля) и на 100% которого - авария (см.
    _process_reactors) - число ячеек ЭТОЙ зоны * buildings.*.reactor.
    heat_cap_per_cell. layout можно передать готовым (уже посчитан вызывающим,
    например reactor_instances_view для всех зон разом), иначе считается тут же."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return 0.0
    config = BUILDINGS.get(instance["building_id"], {}).get("reactor", {})
    if layout is None:
        width, height = reactor_grid_dims(state, instance_id)
        layout = _reactor_zone_layout(width, height, len(instance["zones"]))
    cell_count = sum(1 for z in layout if z == zone_index)
    return cell_count * config.get("heat_cap_per_cell", 0.0)


def _ensure_reactor_grid(state: GameState, instance_id: str) -> None:
    """Идемпотентно доращивает instance["grid"] до текущего reactor_grid_dims
    (на свежесозданном экземпляре или после покупки reactor_grid_size) - НИКОГДА
    не обрезает и не переставляет уже заполненные ячейки, новые слоты
    добавляются В КОНЕЦ (row-major append-only) - логически более широкая
    решётка не сдвигает индексы старых ячеек."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return
    width, height = reactor_grid_dims(state, instance_id)
    target = width * height
    grid = instance.setdefault("grid", [])
    while len(grid) < target:
        grid.append(None)


def _ensure_reactor_zones(state: GameState, instance_id: str) -> None:
    """Идемпотентно синхронизирует instance["zones"] с текущим
    reactor_zone_count - В ОТЛИЧИЕ от _ensure_reactor_grid (append-only), смена
    ЧИСЛА зон полностью ПЕРЕСОЗДАЁТ список (топология "ячейка -> зона" меняется
    целиком при смене zone_count - см. _reactor_zone_layout, частичный перенос
    тепла между старой и новой разбивкой был бы неоднозначен), поэтому
    накопленное тепло/рычаги всех зон осознанно сбрасываются при любом
    изменении числа зон, не только при росте."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return
    target = reactor_zone_count(state, instance_id)
    if len(instance.get("zones", [])) == target:
        return
    instance["zones"] = [
        {"heat": 0.0, "control_rod_depth": 0, "coolant_flow": 100, "prev_coolant_flow": 100}
        for _ in range(target)
    ]


def farm_instance_slot_capacity(state: GameState, instance_id: str) -> int:
    """Слотов у ОДНОГО экземпляра фермы - breeding_slots его типа (0, если
    экземпляр сломан - сломанная копия не может держать особей) + АБСОЛЮТНАЯ
    добавка от сильнейшего купленного НА ЭТОТ ЭКЗЕМПЛЯР апгрейда типа
    breeding_slots_bonus (тот же принцип, что energy_converter_capacity/
    soul_collector_slots - см. _farm_instance_upgrade_multiplier; обычно
    repeatable, см. _purchased_upgrade_value)."""
    instance = state.farm_instances.get(instance_id)
    if instance is None or instance["broken"]:
        return 0
    base = BUILDINGS[instance["building_id"]].get("breeding_slots", 0)
    bonus = _farm_instance_upgrade_multiplier(state, instance_id, "breeding_slots_bonus", default=0)
    return int(base + bonus)


def farm_instance_slots_used(state: GameState, instance_id: str) -> int:
    return sum(1 for c in state.creatures.values() if c.get("farm_instance_id") == instance_id)


def soul_collector_instance_capacity(state: GameState, instance_id: str) -> int:
    """Слотов у ОДНОГО экземпляра синтезатора душ - зеркало
    farm_instance_slot_capacity: soul_collector.slots его типа (0, если
    экземпляр сломан) + АБСОЛЮТНАЯ добавка от сильнейшего купленного НА ЭТОТ
    ЭКЗЕМПЛЯР апгрейда типа soul_collector_slots (см.
    _soul_instance_upgrade_multiplier)."""
    instance = state.soul_collector_instances.get(instance_id)
    if instance is None or instance["broken"]:
        return 0
    base = BUILDINGS[instance["building_id"]].get("soul_collector", {}).get("slots", 0)
    bonus = _soul_instance_upgrade_multiplier(state, instance_id, "soul_collector_slots", default=0)
    return int(base + bonus)


def soul_collector_instance_slots_used(state: GameState, instance_id: str) -> int:
    instance = state.soul_collector_instances.get(instance_id)
    return len(instance["installed"]) if instance is not None else 0


# Базовое число слотов заказов БЕЗ единой профильной постройки - один заказ за раз.
BASE_AUTOCRAFT_SLOTS = 1


def autocraft_slot_capacity(state: GameState) -> int:
    """Сколько заказов с докрафтом (state.orders с шагами) может идти ОДНОВРЕМЕННО -
    процессоры как "мозг заказов" (как в AE2): BASE_AUTOCRAFT_SLOTS плюс autocraft_slots
    работающих построек. Заказ, который только ждёт энергию (без шагов), слота не
    занимает. Скорость крафта задают не процессоры, а сборщики и «Автоматизация»
    механизмов (см. location_autocraft_capacity)."""
    capacity = BASE_AUTOCRAFT_SLOTS
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        capacity += BUILDINGS[building_id].get("autocraft_slots", 0) * working
    return capacity


def has_autocraft_upgrade(state: GameState, building_id: str) -> bool:
    """Куплено ли у постройки улучшение типа "autocraft" («Автоматизация»)."""
    purchased = state.building_upgrades.get(building_id, [])
    return any(
        u["type"] == "autocraft" and u["id"] in purchased
        for u in BUILDINGS.get(building_id, {}).get("upgrades", [])
    )


def location_assembler_capacity(state: GameState, location_id: str | None) -> int | None:
    """Сколько крафтов рецептов этой локации сборщики делают ЗА ХОД: 0 - сборщиков
    нет, None - без лимита. Сборщик - постройка с autocraft_assembler {location,
    per_turn}: per_turn на КАЖДУЮ работающую копию (32 молекулярных сборщика по 1 -
    32 крафта за ход), per_turn = null - без лимита (ускоренный). Главное свойство
    сборщика - крафт МГНОВЕННЫЙ при любой длительности рецепта (даже если в самом
    механизме он шёл бы 100 ходов). Поле generic - сборщик заводится под ЛЮБУЮ
    локацию-механизм, включая преобразователь."""
    if not location_id:
        return 0
    return _assembler_capacity(state, "location", location_id)


def building_assembler_capacity(state: GameState, building_id: str) -> int | None:
    """То же, что location_assembler_capacity, но для ритуалов алтаря building_id -
    сборщики с autocraft_assembler {building, per_turn} (у ритуалов своей локации нет,
    механизм - сама постройка с ritual_circle). 0 - сборщиков нет, None - без лимита."""
    return _assembler_capacity(state, "building", building_id)


def _assembler_capacity(state: GameState, target_key: str, target_id: str) -> int | None:
    total = 0
    for building_id in state.buildings:
        spec = BUILDINGS[building_id].get("autocraft_assembler")
        if not spec or spec.get(target_key) != target_id:
            continue
        working = working_count(state, building_id)
        if working <= 0:
            continue
        if spec.get("per_turn") is None:
            return None
        total += spec["per_turn"] * working
    return total


def _assembler_names(target_key: str, target_id: str) -> list[str]:
    """Имена построек-сборщиков для механизма (локации или алтаря) - для подсказок."""
    return [
        b["name"] for b in BUILDINGS.values()
        if target_key in (b.get("autocraft_assembler") or {}) and b["autocraft_assembler"][target_key] == target_id
    ]


def mechanism_automated(state: GameState, location_id: str | None) -> bool:
    """Куплена ли «Автоматизация» (улучшение типа autocraft) у постройки, которая
    открывает эту локацию (locations.*.requires_building). Автоматизированный механизм
    работает как игрок, стоящий у него: 1 крафт за ход, многоходовой рецепт - одна
    партия за раз своей длительностью (см. _advance_order_step)."""
    mechanism = LOCATIONS.get(location_id, {}).get("requires_building") if location_id else None
    return bool(mechanism) and working_count(state, mechanism) > 0 and has_autocraft_upgrade(state, mechanism)


def location_autocraft_capacity(state: GameState, location_id: str | None) -> int | None:
    """Сколько крафтов этой локации автокрафт может сделать ЗА ХОД в сумме: сборщики
    (location_assembler_capacity) плюс 1, если механизм автоматизирован. 0 - локация
    не автоматизирована (рецепт вне механизма, без сборщиков и «Автоматизации», -
    только вручную), None - без лимита."""
    assemblers = location_assembler_capacity(state, location_id)
    if assemblers is None:
        return None
    return assemblers + (1 if mechanism_automated(state, location_id) else 0)


def automated_converter_types(state: GameState, machine: str | None) -> list[str]:
    """Типы преобразователей (energy_converter), на которых автокрафт может запускать
    рецепты крафта за энергию партиями (как игрок): у самого типа куплена
    «Автоматизация». machine (energy_recipes.*.machine) сужает выбор до одного типа.
    Сборщик локации преобразователя работает иначе - мгновенно, минуя слоты (см.
    _advance_order_step)."""
    candidates = [machine] if machine else energy_converter_building_types(state)
    return [b for b in candidates if b in BUILDINGS and has_autocraft_upgrade(state, b)]


def _automation_hint(location_id: str | None) -> dict:
    """extra для отказа not_automated: где делается рецепт и что нужно игроку."""
    loc = LOCATIONS.get(location_id) if location_id else None
    where = loc["name"] if loc else "без механизма"
    parts = []
    mechanism = loc.get("requires_building") if loc else None
    if mechanism:
        parts.append(f"нужна «Автоматизация» постройки «{BUILDINGS[mechanism]['name']}»")
    assemblers = _assembler_names("location", location_id) if location_id else []
    if assemblers:
        parts.append("нужна постройка " + " или ".join(f"«{n}»" for n in assemblers))
    hint = " или ".join(parts) if parts else "нет ни сборщика, ни механизма - этот рецепт только вручную"
    return {"where": where, "hint": hint}


def _ritual_hint(building_id: str) -> dict:
    """extra для отказа not_automated у ритуала: ритуалы алтаря автокрафт проводит
    только сборщиками (autocraft_assembler {building})."""
    assemblers = _assembler_names("building", building_id)
    hint = (
        "нужна постройка " + " или ".join(f"«{n}»" for n in assemblers) if assemblers
        else "нет ни одного сборщика ритуалов - только вручную"
    )
    return {"where": BUILDINGS.get(building_id, {}).get("name", building_id), "hint": hint}


def _converter_hint(machine: str | None) -> dict:
    names = [BUILDINGS[machine]["name"]] if machine else [
        b["name"] for b in BUILDINGS.values() if b.get("energy_converter")
    ]
    return {
        "where": LOCATIONS.get(CONVERTER_LOCATION_ID, {}).get("name", "преобразователь"),
        "hint": "нужна «Автоматизация» постройки " + " или ".join(f"«{n}»" for n in names),
    }


def energy_wait_eta_turns(state: GameState, deficit: float) -> int | None:
    """Через сколько ходов накопится ещё deficit энергии при ТЕКУЩЕМ чистом притоке
    (генерация минус потребление построек в этот ход, см. energy_breakdown) - оценка
    для игрока, не обещание: ночью солнце не светит, преобразователи и автокрафт
    тоже тянут из того же пула. None - притока сейчас нет (ждать можно, но сколько -
    не сказать); 0 - уже хватает."""
    if deficit <= 0:
        return 0
    # Постройки «только на излишках» ждущим энергию уступают - не расход.
    _, generation, consumption = energy_breakdown(state, include_surplus_only=False)
    net = generation - consumption
    if net <= 0:
        return None
    return math.ceil(deficit / net)


def _find_order(state: GameState, order_id) -> dict | None:
    return next((o for o in state.orders if o["id"] == order_id), None)


def _refund_existing(target: dict, amounts: dict) -> None:
    """Возврат при снятии задачи удалённого контента: ресурсы, которых в контенте уже
    нет, вернуть некуда - они пропадают."""
    _gain(target, {r: a for r, a in amounts.items() if r in RESOURCE_NAMES})


def _job_refund_target(state: GameState, job: dict) -> dict:
    """Куда возвращать цену снятой задачи: партия заказа - в резерв своего заказа."""
    order = _find_order(state, job.get("order_id")) if job.get("order_id") is not None else None
    return order["reserved"] if order is not None else state.resources


def _upgrade_ids(building_id: str) -> set[str]:
    return {u["id"] for u in BUILDINGS.get(building_id, {}).get("upgrades", [])}


def _order_references_exist(state: GameState, order: dict) -> bool:
    """Все ли id, на которые ссылается заказ (его цель и шаги), ещё есть в контенте."""
    kind, target = order["kind"], order["target_id"]
    if kind == "recipe":
        rkind, rid = parse_grid_recipe_key(target)
        ok = {
            "energy_recipe": ENERGY_RECIPES, "ritual_recipe": RITUAL_RECIPES,
            "replicator": RESOURCE_NAMES, "building": BUILDINGS, "manual_action": MANUAL_ACTIONS,
        }[rkind].__contains__(rid)
    elif kind in ("building_upgrade", "farm_instance_upgrade", "reactor_instance_upgrade", "soul_instance_upgrade"):
        owner, _, upgrade_id = target.partition(":")
        if kind == "building_upgrade":
            building_id = owner
        else:
            instances = {
                "farm_instance_upgrade": state.farm_instances, "reactor_instance_upgrade": state.reactor_instances,
                "soul_instance_upgrade": state.soul_collector_instances,
            }[kind]
            building_id = instances.get(owner, {}).get("building_id")
        ok = upgrade_id in _upgrade_ids(building_id)
    elif kind == "synthesize":
        ok = all(a in trait_alleles(t) for t, a in order.get("chosen_alleles", {}).items())
    elif kind == "storage_upgrade":
        ok = target in RESOURCE_STORAGE
    else:
        table = {
            "manual": MANUAL_ACTIONS, "build": BUILDINGS, "tech": TECHS, "repair": BUILDINGS,
            "upgrade_tool": TOOLS, "repair_tool": TOOLS, "start_energy_recipe": ENERGY_RECIPES,
            "start_ritual": RITUAL_RECIPES, "summon_boss": MOBS,
        }.get(kind)
        ok = table is not None and target in table
    if not ok:
        return False
    for step in order.get("steps", []):
        if step.get("resource_id") not in RESOURCE_NAMES:
            return False
        if step["kind"] in ("action", "long") and step.get("action_id") not in MANUAL_ACTIONS:
            return False
        if step["kind"] == "energy" and step.get("recipe_id") not in ENERGY_RECIPES:
            return False
        if step["kind"] == "ritual" and step.get("recipe_id") not in RITUAL_RECIPES:
            return False
    return True


def _repair_genotype(creature: dict) -> bool:
    """Аллели, которых в контенте больше нет, заменяет второй аллелью пары (или
    доминантной/первой аллелью признака). False - спасать нечего: вида особи больше нет."""
    genotype = creature["genotype"]
    for trait_id in GENOTYPE_TRAIT_IDS:
        alleles = trait_alleles(trait_id)
        pair = genotype.get(trait_id)
        if pair is None or all(a in alleles for a in pair):
            continue
        known = [a for a in pair if a in alleles]
        if not known:
            if trait_id == "species":
                return False
            fallback = next((aid for aid, a in alleles.items() if a.get("dominant")), next(iter(alleles), None))
            if fallback is None:
                genotype.pop(trait_id)
                continue
            known = [fallback]
        genotype[trait_id] = [a if a in alleles else known[0] for a in pair]
    creature["species_id"] = _phenotype_allele("species", genotype["species"])
    return True


def _return_reactor_cell(state: GameState, cell: dict) -> None:
    """Компонент и катализатор ячейки - на склад (выгоревшее топливо - своим остатком),
    как remove_reactor_component; удалённые из контента ресурсы пропадают."""
    resource_id = cell["resource_id"]
    if cell.get("charge") == 0:
        resource_id = RESOURCE_REACTOR_COMPONENT.get(resource_id, {}).get("spent_result", resource_id)
    _refund_existing(state.resources, {resource_id: 1})
    if cell.get("catalyst_id") is not None:
        _refund_existing(state.resources, {cell["catalyst_id"]: 1})


def _drop_removed_instances(state: GameState) -> None:
    """Физические экземпляры (фермы, синтезаторы душ, реакторы): экземпляр постройки,
    которой больше нет (или она больше не того вида), снимается, его содержимое
    возвращается - особи в банк, души и компоненты на склад. У живых экземпляров
    убираются души/компоненты, которые этот экземпляр больше не принимает."""
    for iid in [i for i, inst in state.farm_instances.items() if not _is_farm_building(inst["building_id"])]:
        del state.farm_instances[iid]
    for creature in state.creatures.values():
        home = creature.get("farm_instance_id")
        # id ТИПА фермы - старое сохранение, его расселит _ensure_farm_instances.
        if home is not None and home not in state.farm_instances and not _is_farm_building(home):
            creature["farm_instance_id"] = None

    for iid, inst in list(state.soul_collector_instances.items()):
        accepts = soul_collector_accepts_kind(inst["building_id"])
        alive = _is_soul_collector_building(inst["building_id"])
        kept = []
        for soul_id in inst.get("installed", []):
            if alive and soul_id in SOUL_RECIPES and soul_kind(soul_id) == accepts:
                kept.append(soul_id)
            else:
                _refund_existing(state.resources, {soul_id: 1})
        inst["installed"] = kept
        if not alive:
            del state.soul_collector_instances[iid]
    for building_id in list(state.soul_slots):
        souls = state.soul_slots.pop(building_id)
        if _is_soul_collector_building(building_id):
            state.soul_slots[building_id] = [s for s in souls if s in RESOURCE_NAMES]
        else:
            _refund_existing(state.resources, Counter(souls))

    for iid, inst in list(state.reactor_instances.items()):
        alive = _is_reactor_building(inst["building_id"])
        grid = inst.get("grid", [])
        for i, cell in enumerate(grid):
            if cell is None:
                continue
            role = reactor_component_role(cell["resource_id"])
            if not alive or role is None or not reactor_accepts_role(inst["building_id"], role):
                _return_reactor_cell(state, cell)
                grid[i] = None
                continue
            catalyst_id = cell.get("catalyst_id")
            if catalyst_id is not None and role not in RESOURCE_REACTOR_CATALYST.get(catalyst_id, {}).get("applies_to", []):
                _refund_existing(state.resources, {catalyst_id: 1})
                cell["catalyst_id"] = None
        if inst.get("coolant_fluid") is not None and inst["coolant_fluid"] not in RESOURCE_REACTOR_COOLANT_FLUID:
            inst["coolant_fluid"] = next(iter(RESOURCE_REACTOR_COOLANT_FLUID), None)
        if not alive:
            del state.reactor_instances[iid]

    for instances in (state.farm_instances, state.soul_collector_instances, state.reactor_instances):
        for inst in instances.values():
            allowed = _upgrade_ids(inst["building_id"])
            inst["upgrades"] = [u for u in inst.get("upgrades", []) if u in allowed]
            if inst.get("broken_type") is not None and inst["broken_type"] not in BREAKDOWN_TYPES:
                inst["broken_type"] = None


def _drop_removed_jobs(state: GameState) -> None:
    """Идущие задачи, рецепт или механизм которых удалён из контента, снимаются, как
    отменой: списанная цена - на склад (партия заказа - в резерв заказа)."""
    for action_id in [a for a in state.manual_jobs if a not in MANUAL_ACTIONS]:
        job = state.manual_jobs.pop(action_id)
        _refund_existing(_job_refund_target(state, job), job.get("cost", {}))
    kept = []
    for job in state.energy_jobs:
        building = BUILDINGS.get(job.get("building_id"), {})
        if job["recipe_id"] in ENERGY_RECIPES and (job.get("building_id") is None or building.get("energy_converter")):
            kept.append(job)
        else:
            _refund_existing(_job_refund_target(state, job), job.get("cost", {}))
    state.energy_jobs = kept
    for building_id, job in list(state.ritual_jobs.items()):
        if BUILDINGS.get(building_id, {}).get("ritual_circle") and job["recipe_id"] in RITUAL_RECIPES:
            continue
        del state.ritual_jobs[building_id]
        _refund_existing(state.resources, job.get("cost", {}))
    kept = []
    for job in state.replicator_jobs:
        if BUILDINGS.get(job["building_id"], {}).get("replicator") and job["resource_id"] in RESOURCE_NAMES:
            kept.append(job)
            continue
        matter = replicator_matter(job["building_id"]) if BUILDINGS.get(job["building_id"], {}).get("replicator") else None
        refund = max(0, job["cost"] - job["progress"])
        if matter is not None and not RESOURCE_FRACTIONAL.get(matter, False):
            refund = math.floor(refund + 1e-9)
        if matter is not None and refund > 0:
            _refund_existing(_job_refund_target(state, job), {matter: round(refund, 9)})
    state.replicator_jobs = kept
    if state.active_research is not None and state.active_research["tech_id"] not in TECHS:
        _refund_existing(state.resources, state.active_research.get("cost", {}))
        state.active_research = None
    if state.active_encounter is not None and state.active_encounter.get("mob_id") not in MOBS:
        state.active_encounter = None
    for order in list(state.orders):
        if not _order_references_exist(state, order):
            state.orders.remove(order)
            _refund_existing(state.resources, order["reserved"])
            for job in [*state.manual_jobs.values(), *state.energy_jobs, *state.replicator_jobs]:
                if job.get("order_id") == order["id"]:
                    job.pop("order_id", None)
        else:
            order["reserved"] = {r: a for r, a in order["reserved"].items() if r in RESOURCE_NAMES}


def drop_removed_content(state: GameState) -> None:
    """Сохранение старше контента: снимает всё, что ссылается на удалённое из
    content.json (или на то, что сменило вид: постройка больше не синтезатор, у
    инструмента меньше тиров и т.п.) - иначе первый же ход или экран упал бы KeyError.
    Постройки уходят вместе с поломками, улучшениями и ползунком; задачи, заказы,
    исследование, души и компоненты реактора снимаются, как отменой, с возвратом
    списанного на склад. Вызывается при загрузке сохранения (tools/web_ui.py:
    load_state/открытие)."""
    for building_id in [b for b in state.buildings if b not in BUILDINGS]:
        state.buildings.pop(building_id, None)
        state.broken.pop(building_id, None)
        state.building_upgrades.pop(building_id, None)
        state.building_disabled.pop(building_id, None)
    for building_id in [b for b in state.construction_progress if b not in BUILDINGS]:
        state.construction_progress.pop(building_id)
    for building_id, done in list(state.construction_progress.items()):
        if not 0 < done < len(building_construction_stages(building_id)):
            state.construction_progress.pop(building_id)
    for building_id in list(state.building_upgrades):
        allowed = _upgrade_ids(building_id)
        state.building_upgrades[building_id] = [u for u in state.building_upgrades[building_id] if u in allowed]
    for building_id, types in state.broken_types.items():
        state.broken_types[building_id] = [t if t in BREAKDOWN_TYPES else None for t in types]
    for building_id in [b for b in state.building_disabled if b not in BUILDINGS]:
        state.building_disabled.pop(building_id)
    _drop_removed_instances(state)
    _drop_removed_jobs(state)
    for building_id in [b for b in state.automation_rules if not BUILDINGS.get(b, {}).get("automation_hub")]:
        state.automation_rules.pop(building_id)
    target_tables = {"manual_action": MANUAL_ACTIONS, "energy_recipe": ENERGY_RECIPES, "ritual_recipe": RITUAL_RECIPES}
    for building_id, rules in state.automation_rules.items():
        state.automation_rules[building_id] = [
            r for r in rules
            if r.get("target_id") in target_tables.get(r.get("kind", "manual_action"), {})
            and all(c.get("resource") in RESOURCE_NAMES for c in r.get("conditions", []))
        ]

    # Инструменты: удалённая линейка, тир сверх списка, зачарования.
    for tool_id in [t for t in state.tool_tiers if t not in TOOLS]:
        state.tool_tiers.pop(tool_id)
    for tool_id, tier in list(state.tool_tiers.items()):
        top = len(TOOLS[tool_id]["tiers"])
        if tier > top:
            state.tool_tiers[tool_id] = top
            state.tool_durability.pop(tool_id, None)
    for tool_id in [t for t in state.tool_durability if t not in TOOLS]:
        state.tool_durability.pop(tool_id)
    for tool_id in list(state.tool_enchantments):
        levels = {
            e: min(level, len(ENCHANTMENTS[e].get("levels", [])))
            for e, level in state.tool_enchantments[tool_id].items()
            if e in ENCHANTMENTS and ENCHANTMENTS[e].get("levels")
        }
        if tool_id in TOOLS and levels:
            state.tool_enchantments[tool_id] = levels
        else:
            state.tool_enchantments.pop(tool_id)
    for resource_id in list(state.storage_tier):
        tiers = RESOURCE_STORAGE.get(resource_id, {}).get("tiers", [])
        if not tiers:
            state.storage_tier.pop(resource_id)
        else:
            state.storage_tier[resource_id] = min(state.storage_tier[resource_id], len(tiers))
    state.move_cooldowns = {m: t for m, t in state.move_cooldowns.items() if m in COMBAT_MOVES}

    # Особи и генетика.
    for creature_id in list(state.creatures):
        if not _repair_genotype(state.creatures[creature_id]):
            del state.creatures[creature_id]
    for trait_id in list(state.known_alleles):
        alleles = trait_alleles(trait_id) if trait_id in GENOTYPE_TRAIT_IDS else {}
        state.known_alleles[trait_id] = [a for a in state.known_alleles[trait_id] if a in alleles]
    state.species_discovered &= set(SPECIES)

    # Шахта: клетка с рудой, которой больше нет, становится пустой породой.
    if state.active_mine_field:
        for cell in state.active_mine_field.get("cells", []):
            if cell.get("resource") is not None and cell["resource"] not in MINING_INFO:
                cell["resource"] = None

    # Плавильни: постройка больше не плавильня, рецепт или ресурс тигля пропал из контента.
    for building_id in [b for b in state.smelteries if not is_smeltery(b)]:
        _drop_smeltery(state, building_id)
    for sm in state.smelteries.values():
        for i, slot in enumerate(sm["slots"]):
            if slot and slot["recipe_id"] not in MELT_RECIPES:
                _gain(state.resources, {r: a for r, a in slot.get("cost", {}).items() if r in RESOURCE_NAMES})
                sm["slots"][i] = None
        sm["tank"] = {r: a for r, a in sm["tank"].items() if r in RESOURCE_NAMES}
        if sm.get("cast") and sm["cast"]["recipe_id"] not in CAST_RECIPES:
            sm["cast"] = None
    state.discovered_alloys &= set(ALLOY_RECIPES)


def cancel_order(state: GameState, order_id: int) -> ActionResult:
    """Отменяет заказ немедленно, БЕЗ траты хода: всё, что лежит в его резерве (изъятое
    со склада сырьё и уже сделанные промежуточные предметы), возвращается на склад.
    Потраченная энергия не возвращается. Партии, которые заказ уже запустил на
    механизмах/преобразователях, доиграют сами и отдадут результат на склад."""
    order = _find_order(state, order_id)
    if order is None:
        return ActionResult(False, "Такого заказа уже нет.")
    state.orders.remove(order)
    _gain(state.resources, order["reserved"])
    for job in state.manual_jobs.values():
        if job.get("order_id") == order_id:
            job.pop("order_id", None)
    for job in state.energy_jobs + state.replicator_jobs:
        if job.get("order_id") == order_id:
            job.pop("order_id", None)
    return ActionResult(True, f"Заказ отменён: {order['label']} - резерв вернулся на склад.")


def _pick_converter_building_with_free_slot(
    state: GameState, energy_jobs: list[dict], machine: str | None, automated_only: bool = False,
) -> str | None:
    """Первый ТИП постройки с energy_converter, у которого есть свободный слот
    прямо сейчас (с учётом уже идущих energy_jobs) - None, если такого типа нет. machine
    (см. content.json energy_recipes.*.machine) сужает перебор до ОДНОГО конкретного
    типа, если задан; иначе перебираются все типы в порядке BUILDINGS/content.json.
    automated_only - только типы, на которых автокрафт может запускать рецепты
    (automated_converter_types)."""
    candidates = automated_converter_types(state, machine) if automated_only else (
        [machine] if machine else energy_converter_building_types(state)
    )
    for building_id in candidates:
        capacity = energy_converter_capacity(state, building_id)
        if capacity <= 0:
            continue
        running = sum(1 for j in energy_jobs if j.get("building_id") == building_id)
        if running < capacity:
            return building_id
    return None


class _AutocraftGraph:
    """Структура дерева рецептов, нужных для запроса cost - СЧИТАЕТСЯ ОДИН РАЗ на
    каждый resource_id (не на каждое его появление в дереве, см. _discover_autocraft_graph),
    поэтому не боится "ромбов" (несколько потребителей делят один и тот же ресурс ниже
    по цепочке, см. модуль ensure_and_pay)."""

    __slots__ = (
        "ingredient_first", "preorder", "kind_of", "action_of", "stats_of", "ingredients_of", "ritual_of",
        "replicator_of", "option_count",
    )

    def __init__(self):
        self.ingredient_first: list[str] = []  # топологический порядок: сырьё раньше того, кто его ест
        self.preorder: dict[str, int] = {}  # порядок ПЕРВОГО обнаружения (родитель раньше своих ингредиентов) -
        # нужен только для того, чтобы при НЕСКОЛЬКИХ одновременных настоящих ошибках выбрать
        # ту же самую, что показал бы старый рекурсивный код (см. ensure_and_pay)
        # "action" | "long_action" (многоходовое, manual_action_duration > 1) | "energy" |
        # "ritual" | "replicate" | "none" (сырьё/цикл)
        self.kind_of: dict[str, str] = {}
        self.action_of: dict[str, str] = {}  # только для kind="action"/"long_action"
        self.stats_of: dict[str, tuple[dict[str, int], dict[str, int]]] = {}  # только для kind="action"/"long_action"
        self.ingredients_of: dict[str, list[str]] = {}  # рёбра дерева: из чего делается ресурс (см. _route_fallback)
        self.ritual_of: dict[str, str] = {}  # только для kind="ritual": id ритуала
        self.replicator_of: dict[str, str] = {}  # только для kind="replicate": тип репликатора
        # сколько вообще путей есть у ресурса (_autocraft_source_options) - запасные
        # пути ищет _route_fallback
        self.option_count: dict[str, int] = {}


def _autocraft_action_usable(state: GameState, action: dict) -> bool:
    """Может ли автокрафт выполнить рецепт-действие прямо сейчас: технология, хотя бы
    одна РАБОТАЮЩАЯ копия постройки-механизма (тот же гейт, что у клика игрока) и
    сборщик или «Автоматизация» его локации. requires_tool_tiers автокрафт НАМЕРЕННО не
    проверяет (прямое пожелание)."""
    if action["requires_tech"] and action["requires_tech"] not in state.techs:
        return False
    return _action_building_ok(action, state) and location_autocraft_capacity(state, action.get("location")) != 0


def _autocraft_energy_recipe_usable(state: GameState, recipe_id: str) -> bool:
    """То же для крафта за энергию: технология и сборщик преобразователя или
    автоматизированный преобразователь нужного типа."""
    recipe = ENERGY_RECIPES[recipe_id]
    if recipe.get("requires_tech") and recipe["requires_tech"] not in state.techs:
        return False
    if location_assembler_capacity(state, CONVERTER_LOCATION_ID) != 0:
        return True
    return any(energy_converter_capacity(state, b) > 0 for b in automated_converter_types(state, recipe.get("machine")))


def _autocraft_replicator_building(state: GameState) -> str | None:
    """Тип репликатора, которым автокрафт делает ресурсы: первый работающий с купленной
    «Автоматизацией» (улучшение типа autocraft). None - автокрафту репликатор недоступен
    (материю без спроса игрока он не тратит)."""
    return next((b for b in replicator_building_types(state) if has_autocraft_upgrade(state, b)), None)


def _replicator_hint() -> dict:
    """extra для отказа not_automated у заказа репликации."""
    names = [b["name"] for b in BUILDINGS.values() if b.get("replicator")]
    return {
        "where": names[0] if names else "репликатор",
        "hint": "нужна «Автоматизация» постройки " + " или ".join(f"«{n}»" for n in names) if names
        else "в игре нет репликатора",
    }


def _autocraft_source_options(
    state: GameState, resource_id: str, recipe: dict[str, str], rituals: dict[str, str], replicator: str | None,
    cyclic: frozenset = frozenset(),
) -> list[tuple[str, str]]:
    """Чем автокрафт может сделать resource_id прямо сейчас, в порядке предпочтения:
    [(kind, target_id)], kind - "action" (id действия), "energy" (id рецепта
    преобразователя), "ritual" (id ритуала), "replicator" (тип репликатора). Первый -
    основной путь, остальные - запасные (_route_fallback). Порядок - по приоритету игрока
    (state.action_priority: id действия, "energy_recipe:<id>", "replicator:<ресурс>",
    по умолчанию 0): СТРОГО больший побеждает, при равенстве - обычный рецепт, потом
    преобразователь, потом репликатор. Ритуал - только если нет ни обычного рецепта, ни
    преобразователя, своего приоритета у него нет. Материю репликатор из себя не делает.
    Рецепты из круга (cyclic, см. autocraft_cyclic_recipes) путями не бывают; recipe
    (resource_recipe(usable_only=True)) их уже не содержит."""
    options: list[tuple[int, int, str, str]] = []
    action_id = recipe.get(resource_id)
    if action_id is not None:
        options.append((action_priority(state, action_id), 0, "action", action_id))
    energy_id = ENERGY_RECIPE_FOR_RESOURCE.get(resource_id)
    if energy_id is not None and grid_recipe_key("energy_recipe", energy_id) in cyclic:
        energy_id = None
    if energy_id is not None and _autocraft_energy_recipe_usable(state, energy_id):
        options.append((action_priority(state, grid_recipe_key("energy_recipe", energy_id)), 1, "energy", energy_id))
    if not options:
        ritual_id = rituals.get(resource_id)
        if ritual_id is not None and grid_recipe_key("ritual_recipe", ritual_id) in cyclic:
            ritual_id = None
        if ritual_id is not None and _ritual_autocraft_failure(state, ritual_id, resource_id) is None:
            options.append((0, 2, "ritual", ritual_id))
    if replicator is not None and grid_recipe_key("replicator", resource_id) in cyclic:
        replicator = None
    if replicator is not None and resource_id in RESOURCE_REPLICATION_COST and resource_id != replicator_matter(replicator):
        options.append((action_priority(state, grid_recipe_key("replicator", resource_id)), 3, "replicator", replicator))
    options.sort(key=lambda option: (-option[0], option[1]))
    return [(kind, target_id) for _, _, kind, target_id in options]


def replicator_preferred_resources(state: GameState, recipe: dict[str, str] | None = None) -> frozenset[str]:
    """Ресурсы, которые автокрафт делает репликатором В ПЕРВУЮ очередь, хотя у них есть и
    другой рецепт (обычный или преобразователь): игрок поставил "replicator:<ресурс>"
    приоритет СТРОГО выше, чем у них (зеркало converter_preferred_resources, для UI
    приоритетов; сам выбор пути - _autocraft_source_options)."""
    recipe = resource_recipe(state) if recipe is None else recipe
    result = set()
    for resource_id in RESOURCE_REPLICATION_COST:
        others = []
        if resource_id in recipe:
            others.append(action_priority(state, recipe[resource_id]))
        energy_id = ENERGY_RECIPE_FOR_RESOURCE.get(resource_id)
        if energy_id is not None:
            others.append(action_priority(state, grid_recipe_key("energy_recipe", energy_id)))
        if others and action_priority(state, grid_recipe_key("replicator", resource_id)) > max(others):
            result.add(resource_id)
    return frozenset(result)


def _discover_autocraft_graph(
    state: GameState, resource_ids, recipe: dict[str, str], choice: dict[str, int] | None = None,
    rituals: dict[str, str] | None = None, replicator: str | None = None, cyclic: frozenset | None = None,
) -> _AutocraftGraph:
    """Обходит дерево рецептов ОТ resource_ids ВНИЗ до сырья, заходя в КАЖДЫЙ resource_id
    ровно один раз (не один раз на каждого потребителя) - структура рецепта (какое
    действие, из чего) от количества не зависит, поэтому её достаточно узнать один раз,
    даже если один и тот же ресурс нужен пяти разным веткам разом. Цикл в рецептах
    (resource_id зависит сам от себя через цепочку) определяется классическим DFS
    3-цветным обходом (серый = сейчас на пути обхода, чёрный = уже разобран целиком) -
    НЕ тем же самым, что "уже разобран где-то в дереве": ромб (общий ресурс у двух
    РАЗНЫХ веток) - это нормально и не цикл, серым он бывает только пока раскрывается
    его СОБСТВЕННОЕ поддерево, а не пока на него просто ссылаются.

    Путь каждого ресурса - из _autocraft_source_options (обычный рецепт, крафт за
    энергию, ритуал, репликатор - по приоритету игрока); choice[resource_id] - номер
    пути в этом списке (0 - основной, больше - запасной, см. _order_route). Рецепты,
    которые автокрафт сейчас выполнить не может (recipe уже без них, см.
    resource_recipe(usable_only=True); остальные проверяются там же), он не видит
    вовсе: ресурс без доступного пути для него - сырьё ("none")."""
    graph = _AutocraftGraph()
    choice = choice or {}
    rituals = ritual_recipe_for_resource() if rituals is None else rituals
    cyclic = autocraft_cyclic_recipes() if cyclic is None else cyclic
    color: dict[str, int] = {}  # 0/нет записи = не тронут, 1 = серый (на пути), 2 = чёрный (готов)

    def source(resource_id: str) -> tuple[str, str] | None:
        options = _autocraft_source_options(state, resource_id, recipe, rituals, replicator, cyclic)
        graph.option_count[resource_id] = len(options)
        if not options:
            return None
        return options[min(choice.get(resource_id, 0), len(options) - 1)]

    def visit(resource_id: str) -> None:
        if color.get(resource_id, 0) == 2:
            return
        if color.get(resource_id, 0) == 1:
            # Круги рецептов отсекает autocraft_cyclic_recipes, сюда они не доходят;
            # это лишь страховка от бесконечной рекурсии - как будто рецепта нет.
            graph.kind_of[resource_id] = "none"
            return
        graph.preorder[resource_id] = len(graph.preorder)
        color[resource_id] = 1
        kind, target_id = source(resource_id) or (None, None)
        ingredients: list[str] = []
        if kind == "energy":
            graph.kind_of[resource_id] = "energy"
            # Материальная цена крафта за энергию (consumes) - обычные ингредиенты
            # заказа: резервируются/докрафчиваются, как у любого рецепта.
            ingredients = list(ENERGY_RECIPES[target_id].get("consumes", {}))
            # Сборщик преобразователя платит порог энергии РАЗОМ - энергия
            # становится обычным ингредиентом (ожидание/резерв/ёмкость).
            if location_assembler_capacity(state, CONVERTER_LOCATION_ID) != 0:
                ingredients.append(ENERGY_RESOURCE_ID)
        elif kind == "ritual":
            graph.kind_of[resource_id] = "ritual"
            graph.ritual_of[resource_id] = target_id
            ingredients = list(_ritual_full_cost(RITUAL_RECIPES[target_id]))
        elif kind == "replicator":
            graph.kind_of[resource_id] = "replicate"
            graph.replicator_of[resource_id] = target_id
            ingredients = [replicator_matter(target_id)]
        elif kind is None:
            graph.kind_of[resource_id] = "none"
        else:
            # Длительность - уже с апгрейдом action_duration_reduction: сокращённое до
            # 1 хода действие становится обычным мгновенным рецептом.
            graph.kind_of[resource_id] = "long_action" if manual_action_duration(target_id, state) > 1 else "action"
            graph.action_of[resource_id] = target_id
            effective_cost, effective_gain = effective_manual_action_stats(target_id, state)
            graph.stats_of[resource_id] = (effective_cost, effective_gain)
            ingredients = list(effective_cost)
        graph.ingredients_of[resource_id] = ingredients
        for ing_id in ingredients:
            visit(ing_id)
        color[resource_id] = 2
        graph.ingredient_first.append(resource_id)

    for resource_id in resource_ids:
        visit(resource_id)
    return graph


def _aggregate_autocraft_demand(
    state: GameState, cost: dict[str, int], graph: _AutocraftGraph,
) -> tuple[dict[str, AutocraftFailure], dict[str, float], dict[str, float], dict[str, float], dict[str, str], float]:
    """Считает СУММАРНЫЙ спрос на каждый resource_id (от исходного cost плюс от ВСЕХ
    рецептов дерева, которым он нужен как ингредиент) и решает, можно ли его покрыть.
    Идёт по graph.ingredient_first В ОБРАТНОМ порядке (потребитель раньше своего сырья) -
    к моменту, когда дошли до resource_id, весь спрос НА НЕГО уже посчитан, поэтому
    fires считается сразу верно и ОДИН раз ("ромбы" в дереве не дают экспоненты).

    Спрос сначала покрывается запасом склада (у заказа он будет изъят в резерв, см.
    _place_order), недостача - рецептом. Рецепты в graph уже только доступные автокрафту
    (см. _discover_autocraft_graph), поэтому ресурс без рецепта - просто недостача
    ("no_recipe", missing = сколько не хватает). Ошибки копятся в failures (resource_id ->
    AutocraftFailure), проход не прерывается - вызывающий выбирает ту, что с наименьшим
    graph.preorder (порядок первого обнаружения - как было у старой рекурсии).

    resolved_of[resource_id]: "ok" (хватает запаса), "craft"/"long" (мгновенный/
    многоходовой рецепт, fires в fires_map), "energy" (крафт за энергию, партии в
    fires_map), "ritual" (ритуал, fires - число ритуалов), "replicate" (репликатор,
    fires - единицы ресурса), "energy_wait" (энергию ждём, она притекает сама). energy_per_craft -
    самый большой РАЗОВЫЙ платёж энергией в запросе (его копит резерв заказа, см.
    energy_reserved)."""
    total_needed: dict[str, float] = dict(cost)
    fires_map: dict[str, float] = {}
    # единицы, которые сделает преобразователь (его шаг считает единицы, а не партии)
    energy_units: dict[str, float] = {}
    resolved_of: dict[str, str] = {}
    failures: dict[str, AutocraftFailure] = {}
    energy_per_craft = cost.get(ENERGY_RESOURCE_ID, 0)
    # Побочный выход уже запланированных прокруток: рецепт с несколькими выходами
    # («просеять гравий» -> песок + кремень), запланированный ради одного ресурса, даёт
    # и остальные - их не нужно крафтить второй раз (шаг заказа кладёт в резерв весь gain).
    byproduct: dict[str, float] = {}
    for resource_id in reversed(graph.ingredient_first):
        needed = total_needed.get(resource_id, 0)
        have = state.resources.get(resource_id, 0) + byproduct.get(resource_id, 0)
        if needed <= have:
            resolved_of[resource_id] = "ok"
            continue
        kind = graph.kind_of[resource_id]
        if kind == "none" and resource_id == ENERGY_RESOURCE_ID:
            # Энергию не крафтят, она ПРИТЕКАЕТ сама (генераторы) - нехватку ждём, а не
            # отказываем (прямое пожелание): шаги заказа берут её по ходам, сколько
            # накопилось; нет источника - заказ просто стоит, причина в его статусе.
            resolved_of[resource_id] = "energy_wait"
            continue
        if kind == "none":
            # сырьё, рецепт автокрафту недоступен или цикл
            failures[resource_id] = AutocraftFailure(resource_id, "no_recipe", missing={resource_id: needed - have})
            continue
        if compute_capacity(state) <= 0:
            failures[resource_id] = AutocraftFailure(resource_id, "no_processor")
            continue

        if kind == "energy":
            energy_recipe = ENERGY_RECIPES[ENERGY_RECIPE_FOR_RESOURCE[resource_id]]
            assembled = location_assembler_capacity(state, CONVERTER_LOCATION_ID) != 0
            per_batch = _guaranteed_amount(energy_recipe["produces"][resource_id])
            batches = -(-(needed - have) // per_batch) if per_batch > 0 else 1
            fires_map[resource_id] = batches
            energy_units[resource_id] = needed - have
            resolved_of[resource_id] = "energy"
            for ing_id, ing_amount in energy_recipe.get("consumes", {}).items():
                total_needed[ing_id] = total_needed.get(ing_id, 0) + ing_amount * batches
            if assembled:
                total_needed[ENERGY_RESOURCE_ID] = total_needed.get(ENERGY_RESOURCE_ID, 0) + energy_recipe["threshold"] * batches
                energy_per_craft = max(energy_per_craft, energy_recipe["threshold"])
            continue

        if kind == "ritual":
            ritual = RITUAL_RECIPES[graph.ritual_of[resource_id]]
            ritual_cost = _ritual_full_cost(ritual)
            fires = -(-(needed - have) // ritual["produces"][resource_id])
            fires_map[resource_id] = fires
            resolved_of[resource_id] = "ritual"
            for ing_id, ing_amount in ritual_cost.items():
                total_needed[ing_id] = total_needed.get(ing_id, 0) + ing_amount * fires
            energy_per_craft = max(energy_per_craft, ritual_cost.get(ENERGY_RESOURCE_ID, 0))
            continue

        if kind == "replicate":
            matter = replicator_matter(graph.replicator_of[resource_id])
            units = needed - have
            if not RESOURCE_FRACTIONAL.get(resource_id, False):
                units = math.ceil(units - 1e-9)
            fires_map[resource_id] = units
            resolved_of[resource_id] = "replicate"
            total_needed[matter] = total_needed.get(matter, 0) + replication_matter_cost(resource_id, units, matter)
            continue

        # kind == "action" | "long_action"
        effective_cost, effective_gain = graph.stats_of[resource_id]
        gain_per_fire = effective_gain[resource_id]
        fires = -(-(needed - have) // gain_per_fire)  # округление вверх
        fires_map[resource_id] = fires
        resolved_of[resource_id] = "long" if kind == "long_action" else "craft"
        for ing_id, ing_amount in effective_cost.items():
            total_needed[ing_id] = total_needed.get(ing_id, 0) + ing_amount * fires
        for out_id, out_amount in effective_gain.items():
            if out_id != resource_id and not _is_range_amount(out_amount):
                byproduct[out_id] = byproduct.get(out_id, 0) + out_amount * fires
        energy_per_craft = max(energy_per_craft, effective_cost.get(ENERGY_RESOURCE_ID, 0))
    return failures, total_needed, fires_map, energy_units, resolved_of, energy_per_craft


def has_energy_source(state: GameState) -> bool:
    """Есть ли хоть что-то, от чего энергия МОЖЕТ прибыть сама: работающая (и не
    выключенная ползунком целиком) постройка с энергией в produces - время суток НЕ
    учитывается (солнечная панель ночью всё равно источник, утром даст), построенный
    реактор, вставленная душа или заселённая особь, чей рецепт/вид даёт энергию.
    Нужна статусу заказа, чтобы объяснить игроку, почему энергия не идёт (energy_wait_note)."""
    for building_id in state.buildings:
        if working_count(state, building_id) <= 0:
            continue
        if BUILDINGS[building_id].get("reactor"):
            return True
        if building_active_count(state, building_id) <= 0:
            continue
        produces, _ = effective_building_stats(building_id, state.techs, state.building_upgrades)
        amount = produces.get(ENERGY_RESOURCE_ID, 0)
        if (amount["max"] if _is_range_amount(amount) else amount) > 0:
            return True
    # Души живут в экземплярах синтезаторов (state.soul_slots - только старые сохранения).
    for instance_id, instance in state.soul_collector_instances.items():
        for soul_id in instance["installed"][:soul_collector_instance_capacity(state, instance_id)]:
            if ENERGY_RESOURCE_ID in SOUL_RECIPES.get(soul_id, {}).get("produces", {}):
                return True
    for creature in state.creatures.values():
        if creature.get("farm_instance_id") is None:
            continue
        species = SPECIES.get(creature_phenotype(creature)["species_id"], {})
        if ENERGY_RESOURCE_ID in species.get("produces", {}):
            return True
    return False


def energy_wait_note(state: GameState, energy_per_craft: float) -> str | None:
    """Пояснение к «ждёт энергию» (статус заказа, сообщение клика): в сети нет ни одного
    источника или ОДИН платёж больше, чем вмещают хранилища. Во втором случае заказ всё
    равно может пройти - энергия этого хода сперва идёт на крафты, в хранилища попадает
    только остаток, - поэтому пишем цифры, а не «никогда». None - пояснять нечего."""
    if not has_energy_source(state):
        return "в сети нет ни одного источника энергии"
    capacity = energy_capacity(state)
    if energy_per_craft > capacity:
        _, generation, consumption = energy_breakdown(state)
        return (
            f"за раз нужно {_format_amount(energy_per_craft)}, хранилища вмещают {_format_amount(capacity)}, "
            f"приток ~{_format_amount(max(0, generation - consumption))} в ход"
        )
    return None


def _format_amount(value: float) -> int | float:
    """Число для текста игроку: целое без ".0", дробное - до сотых."""
    value = round(value, 2)
    return int(value) if value == int(value) else value


def _guaranteed_amount(value) -> float:
    """Сколько produce-значение даёт НАВЕРНЯКА - нижняя граница диапазона
    {"min","max"} (см. _is_range_amount) или само число. Нужно, чтобы считать
    число партий "с запасом не в пользу игрока": недобор просто доберётся
    следующей партией на повторной попытке, а перебор сжёг бы энергию/материал."""
    return value["min"] if _is_range_amount(value) else value


def _items_per_fire(cost: dict) -> float:
    """Сколько ПРЕДМЕТОВ потребляет одна прокрутка - энергия не предмет, мощность
    процессора на неё не тратится."""
    return sum(v for r, v in cost.items() if r != ENERGY_RESOURCE_ID)


def _energy_step_per_fire(step: dict) -> float:
    """На сколько продвигает шаг «крафт за энергию» одна партия: шаг цепочки считает
    единицы ресурса (гарантированный выход партии), шаг заказа «рецепт ×N» - сами
    партии (step["batches"])."""
    if step.get("batches"):
        return 1
    return _guaranteed_amount(ENERGY_RECIPES[step["recipe_id"]]["produces"][step["resource_id"]])


def _energy_step_progress(step: dict, produced: dict) -> float:
    """Сколько засчитать шагу «крафт за энергию» за одну готовую партию."""
    return 1 if step.get("batches") else produced.get(step["resource_id"], 0)


def _route_fallback(graph: _AutocraftGraph, failures: dict, choice: dict[str, int]) -> str | None:
    """Какой ресурс перевести на следующий (запасной) путь, чтобы обойти отказ: ближайший
    к месту отказа (сам ресурс или его потребитель выше по дереву), у которого ещё есть
    непробованный путь (_autocraft_source_options: например, преобразователь для
    обычного рецепта, обычный рецепт для предпочтённого игроком преобразователя,
    репликатор). None - обходить нечем."""
    candidates = {
        rid for rid, count in graph.option_count.items()
        if graph.kind_of.get(rid, "none") != "none" and choice.get(rid, 0) + 1 < count
    }
    if not candidates:
        return None
    consumers: dict[str, list[str]] = {}
    for rid, ingredients in graph.ingredients_of.items():
        for ing_id in ingredients:
            consumers.setdefault(ing_id, []).append(rid)
    for failed in sorted(failures, key=lambda rid: graph.preorder[rid]):
        if failures[failed].reason == "no_processor":
            continue  # процессора нет вообще - другой рецепт не поможет
        queue, seen = [failed], {failed}
        while queue:
            rid = queue.pop(0)
            if rid in candidates:
                return rid
            for consumer in consumers.get(rid, ()):
                if consumer not in seen:
                    seen.add(consumer)
                    queue.append(consumer)
    return None


def _order_route(state: GameState, cost: dict[str, int]) -> tuple[_AutocraftGraph, tuple]:
    """Дерево рецептов заказа и его суммарный спрос: (graph, результат
    _aggregate_autocraft_demand). Рецепты - только доступные автокрафту
    (resource_recipe(usable_only=True)): недоступный он не видит вовсе. Сначала каждый
    ресурс делается основным путём (_autocraft_source_options: обычный рецепт, если
    игрок не поднял приоритет преобразователю или репликатору). Если так не выходит (не
    хватает сырья) - ресурс, у которого есть ещё путь, переводится на следующий, и
    расчёт повторяется (_route_fallback). Не помогли и запасные пути - возвращается
    ПЕРВАЯ попытка: её отказ объясняет основной."""
    recipe = resource_recipe(state, usable_only=True)
    rituals = ritual_recipe_for_resource()
    replicator = _autocraft_replicator_building(state)
    cyclic = autocraft_cyclic_recipes()
    choice: dict[str, int] = {}
    first = None
    while True:
        graph = _discover_autocraft_graph(state, cost.keys(), recipe, choice, rituals, replicator, cyclic)
        demand = _aggregate_autocraft_demand(state, cost, graph)
        if not demand[0]:
            return graph, demand
        if first is None:
            first = (graph, demand)
        switch = _route_fallback(graph, demand[0], choice)
        if switch is None:
            return first
        choice[switch] = choice.get(switch, 0) + 1


def _plan_order(
    state: GameState, cost: dict[str, int], top: dict | None = None,
) -> tuple[AutocraftFailure | None, dict | None]:
    """Чистый расчёт заказа (state не меняется): дерево рецептов и суммарный спрос
    (_order_route) -> шаги и то, что изъять со склада. top - шаг самого заказанного
    рецепта (заказ «Рецепт ×N», см. _recipe_order_spec): {"kind", "resource_id",
    "fires_total", "energy_per_fire"} + "action_id" (действие) или "recipe_id"/"batches"
    (крафт за энергию, считаются партии) или "recipe_id" (ритуал, считаются ритуалы);
    тогда cost - его ингредиенты на все N прокруток
    (без энергии - её каждая прокрутка берёт сама). Возвращает (отказ, None) или
    (None, plan): plan = {"steps", "take", "energy_reserve"}. Шаг репликации (kind
    "replicate") - и в top (заказ "replicator:<ресурс>" ×N), и в цепочке."""
    graph, demand = _order_route(state, cost)
    failures, total_needed, fires_map, energy_units, resolved_of, energy_per_craft = demand
    if failures:
        ordered = sorted(failures, key=lambda rid: graph.preorder[rid])
        failure = failures[ordered[0]]
        failure.missing = {r: amount for fid in ordered for r, amount in failures[fid].missing.items()}
        return failure, None

    steps: list[dict] = []
    for resource_id in graph.ingredient_first:
        resolution = resolved_of.get(resource_id)
        if resolution in ("craft", "long"):
            steps.append({
                "resource_id": resource_id, "kind": "action" if resolution == "craft" else "long",
                "action_id": graph.action_of[resource_id],
                "fires_total": fires_map[resource_id], "fires_done": 0,
            })
        elif resolution == "energy":
            needed = energy_units[resource_id]
            steps.append({
                "resource_id": resource_id, "kind": "energy",
                "recipe_id": ENERGY_RECIPE_FOR_RESOURCE[resource_id],
                # у крафта за энергию считаем ЕДИНИЦЫ ресурса, а не партии: выход партии
                # бывает диапазоном, партий запускается столько, сколько нужно по факту
                "fires_total": needed, "fires_done": 0,
            })
        elif resolution == "ritual":
            steps.append({
                "resource_id": resource_id, "kind": "ritual", "recipe_id": graph.ritual_of[resource_id],
                "fires_total": fires_map[resource_id], "fires_done": 0,
            })
        elif resolution == "replicate":
            steps.append({
                "resource_id": resource_id, "kind": "replicate",
                # у репликации считаем ЕДИНИЦЫ ресурса (они и приходят по мере переработки)
                "fires_total": fires_map[resource_id], "fires_done": 0,
            })
    total_energy = total_needed.get(ENERGY_RESOURCE_ID, 0)
    if top is not None:
        steps.append({**{k: v for k, v in top.items() if k != "energy_per_fire"}, "fires_done": 0})
        energy_per_craft = max(energy_per_craft, top["energy_per_fire"])
        total_energy += top["energy_per_fire"] * top["fires_total"]

    if steps:
        # Мощность процессора - "мозг заказов": потолок РАЗМЕРА одного заказа, в
        # предметах, которые съедят все его прокрутки (энергия не считается).
        size = 0
        for step in steps:
            if step["kind"] == "energy":
                recipe_def = ENERGY_RECIPES[step["recipe_id"]]
                per_batch = _energy_step_per_fire(step) or 1
                size += _items_per_fire(recipe_def.get("consumes", {})) * -(-step["fires_total"] // per_batch)
            elif step["kind"] == "ritual":
                size += _items_per_fire(_ritual_full_cost(RITUAL_RECIPES[step["recipe_id"]])) * step["fires_total"]
            elif step["kind"] == "replicate":
                size += RESOURCE_REPLICATION_COST[step["resource_id"]] * step["fires_total"]  # съеденная материя
            else:
                size += _items_per_fire(effective_manual_action_stats(step["action_id"], state)[0]) * step["fires_total"]
        capacity = compute_capacity(state)
        if capacity <= 0:
            return AutocraftFailure(steps[-1]["resource_id"], "no_processor"), None
        if size > capacity:
            return AutocraftFailure(
                steps[-1]["resource_id"], "insufficient_compute",
                extra={"size": _format_amount(size), "capacity": _format_amount(capacity)},
            ), None

    take = {}
    for resource_id, needed in total_needed.items():
        if resource_id == ENERGY_RESOURCE_ID or needed <= 0:
            continue
        amount = min(needed, state.resources.get(resource_id, 0))
        if amount > 0:
            take[resource_id] = amount
    return None, {
        "steps": steps, "take": take,
        "energy_reserve": energy_per_craft if total_energy > 0 else 0,
    }


def _order_failure(
    state: GameState, cost: dict[str, int], top: dict | None = None, output: dict | None = None,
) -> tuple[AutocraftFailure | None, dict | None]:
    """Все проверки оформления заказа без изменений state: план (_plan_order), свободный
    слот заказов и место на складе под итог заказа (output, см. recipe_order_output/
    action_order_output). Общая точка для _place_order и проверок «получится ли»
    (check_order, autocraft_failure) - чтобы окно и карточка не разошлись с заказом."""
    failure, plan = _plan_order(state, cost, top)
    if failure is None and plan["steps"]:
        slots = autocraft_slot_capacity(state)
        if sum(1 for o in state.orders if o["steps"]) >= slots:
            return AutocraftFailure(plan["steps"][-1]["resource_id"], "no_free_slots", extra={"slots": slots}), None
    if failure is None:
        shortfall = _storage_shortfall(state, output, state.orders)
        if shortfall is not None:
            resource_id, need, free = shortfall
            return AutocraftFailure(resource_id, "no_storage_space", extra={
                "need": _format_amount(need), "free": _format_amount(free),
            }), None
    return failure, plan


def _amount_upper(value) -> float:
    """Сколько produce-значение может дать НАИБОЛЬШЕЕ (верх диапазона или само число) -
    место на складе нужно под худший случай."""
    return value["max"] if _is_range_amount(value) else value


def recipe_order_output(state: GameState, key: str, qty: int) -> dict[str, float]:
    """Что заказ «рецепт ×qty» выдаст на склад в конце - итоговый ресурс рецепта, не
    промежуточные (прямое пожелание): под него проверяется место на складе."""
    kind, target_id = parse_grid_recipe_key(key)
    if kind == "energy_recipe":
        produces = ENERGY_RECIPES[target_id].get("produces", {})
    elif kind == "ritual_recipe":
        produces = RITUAL_RECIPES[target_id].get("produces", {})
    elif kind == "replicator":
        produces = {target_id: 1}
    else:
        produces = effective_manual_action_stats(target_id, state)[1]
    return {r: _amount_upper(v) * qty for r, v in produces.items()}


def action_order_output(state: GameState, action_type: str, target_id, scale: int | None = None) -> dict[str, float]:
    """То же для заказа, который оформил клик (_pay_or_defer): на склад результат кладёт
    только ручное действие - у построек, технологий и улучшений итогового ресурса нет."""
    if action_type != "manual" or target_id not in MANUAL_ACTIONS:
        return {}
    building_id = action_mechanism_building(MANUAL_ACTIONS[target_id])
    # ползунок масштаба - в тех же пределах, что у клика (do_manual_action)
    scale = max(1, min(int(scale), mechanism_scale_cap(building_id, state))) if building_id and scale is not None else None
    gain = effective_manual_action_stats(target_id, state, chosen_scale=scale)[1]
    return {r: _amount_upper(v) for r, v in gain.items()}


def _storage_shortfall(state: GameState, output: dict | None, orders=()) -> tuple[str, float, float] | None:
    """Первый итоговый ресурс заказа, под который на складе нет места: (ресурс, выдаст,
    свободно), иначе None. orders - заказы, место под итог которых уже обещано (при
    оформлении; во время работы проверяется только сам склад). Ресурсы без
    вместимости склада не проверяются."""
    for resource_id, amount in (output or {}).items():
        cap = storage_capacity(state, resource_id)
        if cap is None or amount <= 0:
            continue
        promised = sum(o.get("output", {}).get(resource_id, 0) for o in orders)
        free = cap - state.resources.get(resource_id, 0) - promised
        if amount > free:
            return resource_id, amount, max(0, free)
    return None


def _place_order(
    state: GameState, kind: str, target_id, label: str, cost: dict[str, int],
    extra: dict | None = None, final_energy: float = 0, top: dict | None = None, output: dict | None = None,
) -> tuple[AutocraftFailure | None, dict | None]:
    """Оформляет заказ: план (_plan_order), проверка свободного слота заказов и ИЗЪЯТИЕ
    со склада всего нужного в резерв заказа (order["reserved"]) - с этого момента это
    сырьё физически принадлежит заказу, никакой другой процесс (клик, другой заказ,
    автоматизатор, потребление построек) его не перехватит. Склад показывает только
    то, что осталось. kind/target_id/extra - что выполнить по готовности (тот же набор,
    что у _ORDER_RETRY), либо kind="recipe" - заказ «рецепт ×N» (top), результат
    которого просто выдаётся на склад. final_energy - энергия, которую возьмёт само
    действие при выполнении (её заказ ждёт перед финалом). output - итог заказа на
    склад: под него должно быть место при оформлении и во время работы (_process_orders)."""
    failure, plan = _order_failure(state, cost, top, output)
    if failure is not None:
        return failure, None
    reserved: dict[str, float] = {}
    for resource_id, amount in plan["take"].items():
        state.resources[resource_id] -= amount
        reserved[resource_id] = amount
    order = {
        "id": state.next_order_id, "kind": kind, "target_id": target_id, "label": label,
        "reserved": reserved, "steps": plan["steps"],
        "energy_reserve": max(plan["energy_reserve"], final_energy),
        "final_energy": final_energy,
        "status": None, "waits_energy": False,
        "output": {r: a for r, a in (output or {}).items() if storage_capacity(state, r) is not None},
        # итоговые ресурсы целиком (output выше - только те, что упираются в склад): по ним
        # финал заказа «рецепт ×N» отличает результат от остатков (report.orders_finished)
        "results": list(output or {}),
    }
    if extra:
        order.update(extra)
    state.next_order_id += 1
    state.orders.append(order)
    return None, order


def order_recipe(state: GameState, key: str, qty: int) -> ActionResult:
    """Заказ «рецепт ×N» (кнопка «Заказать» в карточке рецепта): автокрафт сделает
    недостающие ингредиенты и сам рецепт N раз, результат - на склад. key - id действия
    или "energy_recipe:<id>" - рецепт преобразователя (grid_recipe_key), тогда N - число
    партий, или "ritual_recipe:<id>" - ритуал (проводят только сборщики алтаря), или
    "replicator:<ресурс>" - репликация N единиц (нужна «Автоматизация» репликатора).
    Ход не тратит - работа идёт в advance_turn (_process_orders). Отказывает
    сразу, если рецепт не открыт, его механизм не автоматизирован, или не хватает сырья,
    которое автокрафт делать не умеет; энергию заказ ждёт."""
    qty = max(1, int(qty))
    name, error = _order_target_error(state, key)
    if error is not None:
        return ActionResult(False, error)
    failure, spec = _recipe_order_spec(state, key, qty)
    if failure is None:
        failure, order = _place_order(
            state, "recipe", key, spec["label"], spec["ingredients"], extra={"qty": qty}, top=spec["top"],
            output=recipe_order_output(state, key, qty),
        )
        if order is not None:
            return ActionResult(True, f"Заказ оформлен: {spec['label']}.", pending=True)
    if failure.reason == "no_recipe":
        return ActionResult(
            False, f"Нельзя заказать «{name}». Не хватает: {_format_shortfall(failure.missing)}.{cycle_note(failure.missing)}",
        )
    return ActionResult(False, f"Нельзя заказать «{name}»: {describe_autocraft_failure(failure)}.")


def _order_target_error(state: GameState, key: str) -> tuple[str | None, str | None]:
    """(имя рецепта, текст отказа или None): есть ли такой рецепт, открыт ли он и годится
    ли для заказа вообще."""
    kind, target_id = parse_grid_recipe_key(key)
    if kind == "energy_recipe":
        if target_id not in ENERGY_RECIPES:
            return None, "Такого рецепта нет."
        name = ENERGY_RECIPES[target_id]["name"]
        if not is_energy_recipe_unlocked(target_id, state):
            return name, f"«{name}» ещё не открыт."
        return name, None
    if kind == "ritual_recipe":
        if target_id not in RITUAL_RECIPES:
            return None, "Такого ритуала нет."
        ritual = RITUAL_RECIPES[target_id]
        if ritual.get("requires_tech") and ritual["requires_tech"] not in state.techs:
            return ritual["name"], f"Ритуал «{ritual['name']}» ещё не открыт."
        if not is_ritual_recipe_deterministic(target_id):
            return ritual["name"], f"Ритуал «{ritual['name']}» заказать нельзя - у него случайный результат."
        return ritual["name"], None
    if kind == "replicator":
        if target_id not in RESOURCE_REPLICATION_COST:
            return None, "Этот ресурс репликатор сделать не может."
        return f"Репликация: {RESOURCE_NAMES[target_id]}", None
    if kind == "building":
        # Крафт постройки сразу ставит её (build()), на склад ничего не кладёт -
        # заказу «×N» нечего выдать.
        name = BUILDINGS[target_id]["name"] if target_id in BUILDINGS else None
        return name, "Постройки не заказываются - собери её в механизме."
    if target_id not in MANUAL_ACTIONS:
        return None, "Такого рецепта нет."
    action = MANUAL_ACTIONS[target_id]
    if not is_manual_action_unlocked(target_id, state):
        return action["name"], f"«{action['name']}» ещё не открыт."
    if not action["cost"] or not is_manual_action_deterministic(target_id):
        return action["name"], f"«{action['name']}» заказать нельзя - у него случайный результат или нет цены."
    if not action["gain"]:
        # Только spawn_species (платная ловля особи) - заказу нечего выдать на склад.
        return action["name"], f"«{action['name']}» заказать нельзя - оно ничего не кладёт на склад."
    return action["name"], None


def _recipe_order_spec(state: GameState, key: str, qty: int) -> tuple[AutocraftFailure | None, dict | None]:
    """Что заказать для «рецепт ×qty»: {"top" (шаг самого рецепта, см. _plan_order),
    "ingredients" (его сырьё на все qty прокруток, без энергии), "label"} - или отказ,
    если механизм рецепта не автоматизирован/нет процессора."""
    kind, target_id = parse_grid_recipe_key(key)
    if kind == "energy_recipe":
        recipe = ENERGY_RECIPES[target_id]
        name = recipe["name"]
        resource_id = next(iter(recipe["produces"]))
        machine = recipe.get("machine")
        assembled = location_assembler_capacity(state, CONVERTER_LOCATION_ID) != 0
        if not assembled and not any(
            energy_converter_capacity(state, b) > 0 for b in automated_converter_types(state, machine)
        ):
            return AutocraftFailure(resource_id, "not_automated", extra=_converter_hint(machine)), None
        top = {
            "kind": "energy", "recipe_id": target_id, "resource_id": resource_id,
            "fires_total": qty, "batches": True,
            # Сборщик преобразователя платит порог разом, слоты копят энергию сами.
            "energy_per_fire": recipe["threshold"] if assembled else 0,
        }
        ingredients = {r: v * qty for r, v in recipe.get("consumes", {}).items()}
    elif kind == "ritual_recipe":
        ritual = RITUAL_RECIPES[target_id]
        name = ritual["name"]
        resource_id = next(iter(ritual["produces"]))
        failure = _ritual_autocraft_failure(state, target_id, resource_id)
        if failure is not None:
            return failure, None
        cost = _ritual_full_cost(ritual)
        top = {
            "kind": "ritual", "recipe_id": target_id, "resource_id": resource_id, "fires_total": qty,
            "energy_per_fire": cost.get(ENERGY_RESOURCE_ID, 0),
        }
        ingredients = {r: v * qty for r, v in cost.items() if r != ENERGY_RESOURCE_ID}
    elif kind == "replicator":
        resource_id = target_id
        name = f"Репликация: {RESOURCE_NAMES[resource_id]}"
        building_id = _autocraft_replicator_building(state)
        if building_id is None:
            return AutocraftFailure(resource_id, "not_automated", extra=_replicator_hint()), None
        matter = replicator_matter(building_id)
        top = {"kind": "replicate", "resource_id": resource_id, "fires_total": qty, "energy_per_fire": 0}
        ingredients = {matter: replication_matter_cost(resource_id, qty, matter)}
    else:
        action = MANUAL_ACTIONS[target_id]
        name = action["name"]
        cost, gain = effective_manual_action_stats(target_id, state)
        resource_id = next(iter(gain))
        if location_autocraft_capacity(state, action.get("location")) == 0:
            return AutocraftFailure(resource_id, "not_automated", extra=_automation_hint(action.get("location"))), None
        top = {
            "kind": "long" if manual_action_duration(target_id, state) > 1 else "action",
            "action_id": target_id, "resource_id": resource_id, "fires_total": qty,
            "energy_per_fire": cost.get(ENERGY_RESOURCE_ID, 0),
        }
        ingredients = {r: v * qty for r, v in cost.items() if r != ENERGY_RESOURCE_ID}
    if compute_capacity(state) <= 0:
        return AutocraftFailure(resource_id, "no_processor"), None
    label = f"«{name}» ×{qty}" if qty > 1 else f"«{name}»"
    if kind == "ritual_recipe":
        label = "ритуал " + label
    return None, {"top": top, "ingredients": ingredients, "label": label}


def check_order(state: GameState, key: str, qty: int) -> AutocraftFailure | None:
    """Можно ли прямо сейчас заказать рецепт ×qty (key - как у order_recipe) - тот же
    расчёт, без изменений state (для карточки рецепта в UI)."""
    kind, target_id = parse_grid_recipe_key(key)
    if kind == "energy_recipe":
        if not is_energy_recipe_unlocked(target_id, state):
            return AutocraftFailure(next(iter(ENERGY_RECIPES[target_id]["produces"])), "tech_locked")
    elif kind == "ritual_recipe":
        ritual = RITUAL_RECIPES[target_id]
        resource_id = next(iter(ritual.get("produces") or {}), target_id)
        if ritual.get("requires_tech") and ritual["requires_tech"] not in state.techs:
            return AutocraftFailure(resource_id, "tech_locked")
        if not is_ritual_recipe_deterministic(target_id):
            return AutocraftFailure(resource_id, "random_result")
    elif kind == "replicator":
        if target_id not in RESOURCE_REPLICATION_COST:
            return AutocraftFailure(target_id, "no_recipe")
    elif kind == "building":
        return AutocraftFailure(target_id, "no_recipe")  # см. _order_target_error
    elif not MANUAL_ACTIONS[target_id]["gain"]:
        # Только spawn_species - см. _order_target_error.
        return AutocraftFailure(MANUAL_ACTIONS[target_id].get("spawn_species") or target_id, "no_output")
    elif not is_manual_action_unlocked(target_id, state):
        return AutocraftFailure(next(iter(MANUAL_ACTIONS[target_id]["gain"])), "tech_locked")
    failure, spec = _recipe_order_spec(state, key, qty)
    if failure is not None:
        return failure
    return _order_failure(state, spec["ingredients"], spec["top"], recipe_order_output(state, key, qty))[0]


def _direct_shortfall(state: GameState, cost: dict[str, int]) -> dict[str, int | float]:
    """Прямая недостача: чего и сколько не хватает в state.resources ровно по
    cost, без анализа рецептов. Используется, когда автокрафт вообще не в игре
    (нет процессора или выключен галочкой) - в этом случае разница между "базовым"
    и "промежуточным" ресурсом не имеет смысла, всё придётся нести/делать руками
    ровно в тех количествах, что указаны в cost."""
    missing: dict[str, int | float] = {}
    for resource_id, amount in cost.items():
        have = state.resources.get(resource_id, 0)
        if have < amount:
            missing[resource_id] = amount - have
    return missing


def _format_shortfall(missing: dict[str, int | float]) -> str:
    parts = []
    for resource_id, amount in missing.items():
        if RESOURCE_FRACTIONAL.get(resource_id, False):
            amount = round(amount, 2)
            if amount == int(amount):
                amount = int(amount)
        else:
            amount = math.ceil(amount)  # защита от дрейфа float на промежуточных умножениях/делениях
        parts.append(f"{RESOURCE_NAMES[resource_id]} {amount}")
    return ", ".join(parts)


def autocraft_eta_turns(state: GameState, fires: int | float, action_id: str | None = None) -> int | None:
    """Через сколько ходов автокрафт сделает fires прокруток рецепта action_id: сборщики
    - мгновенно в пределах их лимита на ход (длительность рецепта не важна), иначе
    «Автоматизация» механизма - как игрок (1 крафт за ход, многоходовой рецепт -
    fires * длительность). None - уложится в ход заказа (или скорость не ограничена)."""
    if fires <= 0 or action_id is None:
        return None
    location = MANUAL_ACTIONS[action_id].get("location")
    assemblers = location_assembler_capacity(state, location)
    if assemblers is None:
        return None
    duration = manual_action_duration(action_id, state)
    mechanism = 1 if mechanism_automated(state, location) else 0
    if assemblers > 0:
        per_turn = assemblers + (mechanism if duration <= 1 else 0)
        return None if fires <= per_turn else -(-fires // per_turn)
    if not mechanism:
        return None
    if duration > 1:
        return int(fires) * duration
    return None if fires <= 1 else int(-(-fires // 1))


def converter_eta_turns(state: GameState, recipe_id: str, batches: int | float) -> int | None:
    """Через сколько ходов автокрафт сделает batches партий крафта за энергию: сборщики
    преобразователя - в пределах их лимита на ход, иначе слоты автоматизированных
    преобразователей, где каждая партия копит порог не быстрее max_energy_per_turn и не
    быстрее притока энергии. Оценка, не обещание: энергию делят все потребители. None -
    уложится в ход заказа (или скорость не ограничена)."""
    if batches <= 0:
        return None
    assemblers = location_assembler_capacity(state, CONVERTER_LOCATION_ID)
    if assemblers is None:
        return None
    if assemblers > 0:
        return None if batches <= assemblers else int(-(-batches // assemblers))
    recipe = ENERGY_RECIPES[recipe_id]
    types = [
        b for b in automated_converter_types(state, recipe.get("machine"))
        if energy_converter_capacity(state, b) > 0
    ]
    if not types:
        return None
    slots = sum(energy_converter_capacity(state, b) for b in types)
    per_turn = energy_converter_max_energy_per_turn(state, types[0])
    batch_turns = 1 if not per_turn else max(1, math.ceil(recipe["threshold"] / per_turn))
    turns = int(-(-batches // slots)) * batch_turns
    deficit = recipe["threshold"] * batches - state.resources.get(ENERGY_RESOURCE_ID, 0)
    turns = max(turns, energy_wait_eta_turns(state, deficit) or 0)
    return None if turns <= 1 else turns


def ritual_eta_turns(state: GameState, recipe_id: str, fires: int | float) -> int | None:
    """Через сколько ходов сборщики алтаря проведут fires ритуалов (их лимит на ход,
    длительность ритуала не важна). None - уложится в ход заказа, скорость не
    ограничена или сборщиков нет вовсе (тогда заказ и не оформится)."""
    if fires <= 0:
        return None
    per_turn = building_assembler_capacity(state, RITUAL_RECIPES[recipe_id]["requires_building"])
    if not per_turn or fires <= per_turn:
        return None
    return int(-(-fires // per_turn))


def replicator_eta_turns(state: GameState, resource_id: str, units: int | float) -> int | None:
    """Через сколько ходов автоматизированный репликатор сделает units единиц: шаг заказа
    ведёт ОДНУ репликацию на все единицы, её скорость - лимит материи за ход. None -
    уложится в ход заказа, лимита нет или автоматизации нет вовсе."""
    building_id = _autocraft_replicator_building(state)
    if units <= 0 or building_id is None:
        return None
    turns = replication_turns(state, building_id, replication_matter_cost(resource_id, units))
    return turns if turns > 1 else None


def order_eta_turns(state: GameState, key: str, qty: int) -> int | None:
    """Через сколько ходов будет готов заказ «рецепт ×qty» (key - как у order_recipe), если
    оформить его сейчас. None - уложится в ход заказа или заказ не оформится. В отличие от
    оценок одного шага (autocraft_eta_turns/converter_eta_turns/ritual_eta_turns) шаги
    через ОДИН механизм складываются - они делят его лимит на ход (сборщики,
    «Автоматизация», слоты преобразователей), - и учитывается очередь: заказы, оформленные
    раньше, берут этот лимит первыми, а многоходовой крафт игрока занимает механизм до
    своего конца. Разные механизмы работают параллельно - срок по самому загруженному.
    Оценка, не обещание: энергию делят все потребители, передача между механизмами
    может добавить ход."""
    failure, spec = _recipe_order_spec(state, key, qty)
    if failure is not None:
        return None
    failure, plan = _plan_order(state, spec["ingredients"], spec["top"])
    if failure is not None:
        return None
    turns, _ = _orders_eta(state, _capacity_load(state, plan["steps"]), state.orders)
    return turns if turns > 1 else None


def cost_eta_turns(
    state: GameState, cost: dict[str, int], kind: str | None = None, target_id=None, scale: int | None = None,
) -> int | None:
    """То же для заказа, который оформит клик (окно подтверждения, см. _pay_or_defer):
    срок всех шагов докрафта плюс энергия самого действия, а у многоходового ручного
    действия - ещё и его собственная длительность после заказа. None - уложится в ход
    клика, заказ не оформится или срок не оценить (энергии неоткуда взяться)."""
    failure, plan = _plan_order(state, {r: a for r, a in cost.items() if r != ENERGY_RESOURCE_ID})
    if failure is not None:
        return None
    own = _capacity_load(state, plan["steps"])
    if cost.get(ENERGY_RESOURCE_ID):
        own.setdefault(("energy",), {"energy": 0})["energy"] += cost[ENERGY_RESOURCE_ID]
    turns, known = _orders_eta(state, own, state.orders)
    if not known:
        return None
    turns = max(1, turns)
    if kind == "manual" and target_id in MANUAL_ACTIONS:
        duration = manual_action_duration(target_id, state)
        if duration > 1:
            turns += duration - 1  # само действие стартует в ход готовности заказа
    return turns if turns > 1 else None


def running_order_eta_turns(state: GameState, order: dict) -> int | None:
    """Через сколько ходов доделается уже идущий заказ (панель «Заказы»): его оставшиеся
    шаги (с учётом того, что уже набрали его партии) и заказы ВПЕРЕДИ него в очереди.
    1 - в ближайший ход. None - срок не оценить: заказ стоит без места на складе или
    ждёт энергию, которой неоткуда взяться."""
    if _storage_shortfall(state, order.get("output")) is not None:
        return None
    index = state.orders.index(order)
    own = _capacity_load(state, order["steps"], order["id"])
    if order.get("final_energy"):
        own.setdefault(("energy",), {"energy": 0})["energy"] += order["final_energy"]
    turns, known = _orders_eta(state, own, state.orders[:index], own_reserve=order.get("energy_reserve", 0))
    if not known:
        return None
    turns = max(1, turns)
    if order["kind"] == "manual" and order["target_id"] in MANUAL_ACTIONS:
        duration = manual_action_duration(order["target_id"], state)
        if duration > 1:
            turns += duration - 1
    return turns


def _orders_eta(state: GameState, own: dict, ahead_orders, own_reserve: float = 0) -> tuple[int, bool]:
    """(ходов, оценка известна): нагрузка own (см. _capacity_load) на каждый механизм плюс
    то, что на тех же механизмах ждут заказы ahead_orders; срок - по самому загруженному.
    Известна = не упирается в энергию, которой неоткуда взяться. own_reserve - энергия,
    которую копит сам этот (уже идущий) заказ: преобразователям она не отдаётся, но его
    собственным шагам - доступна."""
    ahead = _capacity_load(state, [step for order in ahead_orders for step in order["steps"]])
    energy_ahead = sum(o.get("final_energy", 0) for o in ahead_orders)
    if energy_ahead and ("energy",) in own:
        ahead.setdefault(("energy",), {"energy": 0})["energy"] += energy_ahead
    worst, known = 0, True
    for cap_key, load in own.items():
        for field_name, value in ahead.get(cap_key, {}).items():
            if field_name == "long":
                load[field_name] = load[field_name] or value
            elif field_name == "longest":
                load[field_name] = max(load[field_name], value)
            else:
                load[field_name] = load[field_name] + value
        turns = _capacity_turns(state, cap_key, load, own_reserve)
        if turns is None:
            known = False
            continue
        worst = max(worst, turns)
    return worst, known


def _capacity_load(state: GameState, steps: list[dict], order_id=None) -> dict:
    """Сколько работы шаги (ещё не сделанные прокрутки) задают каждому механизму:
    ("loc", локация) - крафты и ходы партий, ("conv", machine) - партии преобразователей и их
    энергия, ("altar", постройка) - ритуалы, ("repl", None) - ходы репликаций, ("energy",) -
    энергия в цене самих крафтов. order_id - шаги уже идущего заказа: то, что успели набрать
    его партии, из работы вычитается. Для order_eta_turns/running_order_eta_turns."""
    load: dict = {}
    for step in steps:
        remaining = step["fires_total"] - step["fires_done"]
        if remaining <= 0:
            continue
        if order_id is not None:
            _subtract_running_progress(state, load, step, order_id)
        if step["kind"] == "energy":
            recipe = ENERGY_RECIPES[step["recipe_id"]]
            per_batch = _energy_step_per_fire(step)
            batches = -(-remaining // per_batch) if per_batch > 0 else remaining
            types = [b for b in automated_converter_types(state, recipe.get("machine")) if energy_converter_capacity(state, b) > 0]
            per_turn = energy_converter_max_energy_per_turn(state, types[0]) if types else None
            batch_turns = max(1, math.ceil(recipe["threshold"] / per_turn)) if per_turn else 1
            # Рецепт с machine идёт только на своём типе - у него свои слоты.
            entry = load.setdefault(("conv", recipe.get("machine")), {"batches": 0, "slot_turns": 0, "energy": 0})
            entry["batches"] += batches
            entry["slot_turns"] += batches * batch_turns
            entry["energy"] += recipe["threshold"] * batches
        elif step["kind"] == "ritual":
            building_id = RITUAL_RECIPES[step["recipe_id"]]["requires_building"]
            load.setdefault(("altar", building_id), {"fires": 0})["fires"] += remaining
            energy = _ritual_full_cost(RITUAL_RECIPES[step["recipe_id"]]).get(ENERGY_RESOURCE_ID, 0)
            if energy:
                load.setdefault(("energy",), {"energy": 0})["energy"] += energy * remaining
        elif step["kind"] == "replicate":
            building_id = _autocraft_replicator_building(state)
            turns = replication_turns(state, building_id, replication_matter_cost(step["resource_id"], remaining)) \
                if building_id else 1
            entry = load.setdefault(("repl", None), {"slot_turns": 0, "longest": 0})
            entry["slot_turns"] += turns
            entry["longest"] = max(entry["longest"], turns)
        else:
            duration = manual_action_duration(step["action_id"], state)
            entry = load.setdefault(
                ("loc", MANUAL_ACTIONS[step["action_id"]].get("location")), {"fires": 0, "mech_turns": 0, "long": False},
            )
            entry["fires"] += remaining
            entry["mech_turns"] += remaining * max(1, duration)
            entry["long"] = entry["long"] or duration > 1
            energy = effective_manual_action_stats(step["action_id"], state)[0].get(ENERGY_RESOURCE_ID, 0)
            if energy:
                load.setdefault(("energy",), {"energy": 0})["energy"] += energy * remaining
    return load


def _subtract_running_progress(state: GameState, load: dict, step: dict, order_id) -> None:
    """Партии заказа order_id, уже идущие по шагу step, часть работы сделали: ходы
    многоходовой партии и накопленную энергию преобразователя из нагрузки вычитаем
    (оставшиеся прокрутки шага их ещё включают)."""
    if step["kind"] == "long":
        job = state.manual_jobs.get(step["action_id"])
        if job is not None and job.get("order_id") == order_id:
            location = MANUAL_ACTIONS[step["action_id"]].get("location")
            entry = load.setdefault(("loc", location), {"fires": 0, "mech_turns": 0, "long": False})
            entry["mech_turns"] -= job["turns_done"]
    elif step["kind"] == "energy":
        recipe = ENERGY_RECIPES[step["recipe_id"]]
        entry = load.setdefault(("conv", recipe.get("machine")), {"batches": 0, "slot_turns": 0, "energy": 0})
        for job in state.energy_jobs:
            if job.get("order_id") == order_id and job.get("order_resource") == step["resource_id"]:
                per_turn = energy_converter_max_energy_per_turn(state, job["building_id"])
                entry["energy"] -= job["progress"]
                if per_turn:
                    entry["slot_turns"] -= job["progress"] / per_turn


def _capacity_turns(state: GameState, cap_key: tuple, load: dict, own_reserve: float = 0) -> int | None:
    """Сколько ходов механизм cap_key будет занят работой load (см. _capacity_load) -
    по тем же правилам, что _advance_order_step/_advance_energy_step/_advance_ritual_step."""
    if cap_key[0] == "energy":
        # Энергию крафтам даёт сеть: запас + чистый приток; неоткуда взяться - срок не оценить.
        return energy_wait_eta_turns(state, load["energy"] - state.resources.get(ENERGY_RESOURCE_ID, 0))
    if cap_key[0] == "altar":
        per_turn = building_assembler_capacity(state, cap_key[1])
        return int(-(-load["fires"] // per_turn)) if per_turn else 0
    if cap_key[0] == "repl":
        # Репликации делят слоты автоматизированных репликаторов; репликации игрока
        # занимают свой слот, пока не доработают.
        types = [b for b in replicator_building_types(state) if has_autocraft_upgrade(state, b)]
        slots = sum(replicator_slot_capacity(state, b) for b in types)
        if slots <= 0:
            return 0
        slot_turns = load["slot_turns"] + sum(
            replication_turns(state, j["building_id"], j["cost"] - j["progress"])
            for j in state.replicator_jobs if j["building_id"] in types and j.get("order_id") is None
        )
        return max(load["longest"], int(-(-slot_turns // slots)))
    if cap_key[0] == "loc":
        location = cap_key[1]
        assemblers = location_assembler_capacity(state, location)
        if assemblers is None:
            return 0
        mechanism = mechanism_automated(state, location)
        if assemblers > 0:
            per_turn = assemblers + (1 if mechanism and not load["long"] else 0)
            return int(-(-load["fires"] // per_turn))
        if not mechanism:
            return 0
        # Многоходовой крафт игрока занимает механизм для партий заказа, пока не доиграет
        # (мгновенные рецепты механизм делает и рядом с ним, как и сам игрок).
        busy = sum(
            job["turns_total"] - job["turns_done"] for action_id, job in state.manual_jobs.items()
            if job.get("order_id") is None and MANUAL_ACTIONS[action_id].get("location") == location
        ) if load["long"] else 0
        return max(0, math.ceil(load["mech_turns"])) + busy
    # Преобразователи: сборщик - партии за ход, иначе слоты автоматизированных типов;
    # и в любом случае - не быстрее, чем накопится энергия на все партии.
    stock = state.resources.get(ENERGY_RESOURCE_ID, 0) - (energy_reserved(state) - own_reserve)
    energy_turns = energy_wait_eta_turns(state, load["energy"] - max(0, stock)) or 0
    assemblers = location_assembler_capacity(state, CONVERTER_LOCATION_ID)
    if assemblers is None:
        return energy_turns
    if assemblers > 0:
        return max(energy_turns, int(-(-load["batches"] // assemblers)))
    types = [b for b in automated_converter_types(state, cap_key[1]) if energy_converter_capacity(state, b) > 0]
    slots = sum(energy_converter_capacity(state, b) for b in types)
    if slots <= 0:
        return energy_turns
    slot_turns = max(0, load["slot_turns"])
    # Крафт игрока занимает слот, пока не наберёт свой порог.
    for job in state.energy_jobs:
        if job.get("order_id") is None and job.get("building_id") in types:
            per_turn = energy_converter_max_energy_per_turn(state, job["building_id"])
            left = ENERGY_RECIPES[job["recipe_id"]]["threshold"] - job["progress"]
            slot_turns += max(1, math.ceil(left / per_turn)) if per_turn else 1
    return max(energy_turns, math.ceil(slot_turns / slots))


def _preview_node(
    state: GameState, resource_id: str, amount: int | float,
    working: dict[str, int | float], graph: _AutocraftGraph,
) -> dict:
    """Один узел дерева предпросмотра автокрафта (см. autocraft_preview) - сколько
    нужно resource_id, сколько из этого возьмётся из уже имеющегося запаса
    (working - рабочая копия state.resources, обедняется по ходу обхода, чтобы
    несколько веток одного cost не задваивали разбор одного и того же сырья), и -
    если остатка не хватает - каким рецептом и сколько раз (fires) он докрафчивается,
    с результатом в children. Рецепт - из дерева заказа (graph, см. _order_route):
    тот же выбор, что сделает сам заказ. "turns" - оценка (см. autocraft_eta_turns),
    растянется ли ИМЕННО этот шаг цепочки на несколько ходов - None, если он
    выполнится мгновенно. Шаг на преобразователе - узел с "energy_recipe_id" и числом
    партий в fires; ритуал - узел с "ritual_recipe_id" и числом ритуалов в fires;
    репликация - узел с "replicator_id" (тип репликатора) и единицами ресурса в fires.
    "missing" - сколько не хватает ресурса, который автокрафт сделать не может (сырьё
    или рецепт ему недоступен)."""
    stock_before = working.get(resource_id, 0)
    taken = min(amount, stock_before)
    working[resource_id] = stock_before - taken
    node = {
        "resource_id": resource_id, "needed": amount, "stock_before": stock_before,
        "taken": taken, "action_id": None, "energy_recipe_id": None, "ritual_recipe_id": None,
        "replicator_id": None, "fires": 0, "turns": None, "missing": 0, "children": [],
    }
    deficit = amount - taken
    if deficit <= 0:
        return node
    kind = graph.kind_of.get(resource_id, "none")
    if kind == "none":
        if resource_id == ENERGY_RESOURCE_ID:
            # Энергию автокрафт не делает, а ждёт (см. energy_wait_note) - сколько
            # примерно ходов копить недостающее при текущем притоке.
            node["energy_wait"] = True
            node["turns"] = energy_wait_eta_turns(state, deficit)
        else:
            node["missing"] = deficit
        return node
    ingredients: list[tuple[str, float]] = []
    if kind == "replicate":
        building_id = graph.replicator_of[resource_id]
        matter = replicator_matter(building_id)
        units = deficit if RESOURCE_FRACTIONAL.get(resource_id, False) else math.ceil(deficit - 1e-9)
        node.update(replicator_id=building_id, fires=units, turns=replicator_eta_turns(state, resource_id, units))
        ingredients = [(matter, replication_matter_cost(resource_id, units, matter))]
    elif kind == "ritual":
        ritual_id = graph.ritual_of[resource_id]
        fires = -(-deficit // RITUAL_RECIPES[ritual_id]["produces"][resource_id])
        node.update(ritual_recipe_id=ritual_id, fires=fires, turns=ritual_eta_turns(state, ritual_id, fires))
        ingredients = [(i, a * fires) for i, a in _ritual_full_cost(RITUAL_RECIPES[ritual_id]).items()]
    elif kind == "energy":
        recipe_id = ENERGY_RECIPE_FOR_RESOURCE[resource_id]
        energy_recipe = ENERGY_RECIPES[recipe_id]
        per_batch = _guaranteed_amount(energy_recipe["produces"][resource_id])
        batches = -(-deficit // per_batch) if per_batch > 0 else 1
        node.update(energy_recipe_id=recipe_id, fires=batches, turns=converter_eta_turns(state, recipe_id, batches))
        ingredients = [(i, a * batches) for i, a in energy_recipe.get("consumes", {}).items()]
        # Порог энергии: сборщик платит его разом, слоты копят по ходам (см.
        # _advance_energy_step) - в обоих случаях недостачу игрок ждёт, как у рецепта
        # с энергией в цене (узел energy_wait с оценкой ходов).
        ingredients.append((ENERGY_RESOURCE_ID, energy_recipe["threshold"] * batches))
    else:
        action_id = graph.action_of[resource_id]
        effective_cost, effective_gain = graph.stats_of[resource_id]
        fires = -(-deficit // effective_gain[resource_id])  # округление вверх, как в заказе
        node.update(action_id=action_id, fires=fires, turns=autocraft_eta_turns(state, fires, action_id))
        ingredients = [(i, a * fires) for i, a in effective_cost.items()]
        # Побочный выход этих прокруток (как в заказе, см. _aggregate_autocraft_demand) -
        # соседний узел возьмёт его, а не будет крафтить второй раз.
        for out_id, out_amount in effective_gain.items():
            if out_id != resource_id and not _is_range_amount(out_amount):
                working[out_id] = working.get(out_id, 0) + out_amount * fires
    for ing_id, ing_amount in ingredients:
        node["children"].append(_preview_node(state, ing_id, ing_amount, working, graph))
    return node


def autocraft_preview(state: GameState, cost: dict[str, int]) -> dict | None:
    """Предпросмотр того, что реально будет потрачено, если запустить действие с
    этой ценой ПРЯМО СЕЙЧАС - включая всё, что попутно докрафтит автокрафт по
    цепочке рецептов, а не только верхний cost. Используется UI для окна
    подтверждения перед запуском действия (см. CLAUDE.md, "Подтверждение
    автокрафта") - игрок видит, сколько и чего в итоге спишется, прежде чем
    соглашаться.

    None, если подтверждение не нужно - действие либо выполнится МГНОВЕННО
    безо всякого докрафта (весь cost уже покрыт наличным запасом), либо
    автокрафт сейчас в принципе не может быть задействован (нет процессора -
    та же развилка, что в _pay_or_defer для "no_processor").

    Это ЧИСТЫЙ пересчёт количества по тому же дереву рецептов, что у заказа
    (_order_route: только рецепты, доступные автокрафту, тот же маршрут через
    преобразователь), а не попытка оформить заказ - мощность/слоты/энергию не
    проверяет: это "сколько и чего", а "получится ли" - autocraft_failure, окно
    показывает оба ответа вместе (иначе кнопка «Запустить» обещала бы заказ, от
    которого _place_order тут же откажется). То, что автокрафт сделать не может, -
    лист с "missing", а не докрафт."""
    if compute_capacity(state) <= 0:
        return None
    if all(state.resources.get(r, 0) >= amount for r, amount in cost.items()):
        return None
    working = dict(state.resources)
    # Маршрут - по предметам: энергию заказ ждёт, на выбор рецептов она не влияет.
    graph = _order_route(state, {r: a for r, a in cost.items() if r != ENERGY_RESOURCE_ID})[0]
    nodes = [_preview_node(state, resource_id, amount, working, graph) for resource_id, amount in cost.items()]
    return {"nodes": nodes}


def is_already_ordered(state: GameState, kind: str | None, target_id) -> bool:
    """Идёт ли уже заказ на это действие (kind/target_id - как у _pay_or_defer)."""
    return any(o["kind"] == kind and o["target_id"] == target_id for o in state.orders)


def autocraft_failure(
    state: GameState, cost: dict[str, int], kind: str | None = None, target_id=None, scale: int | None = None,
) -> AutocraftFailure | None:
    """Откажет ли _pay_or_defer этой цене прямо сейчас (без изменений state): None -
    хватает запаса, действие уже заказано (kind/target_id - как у _pay_or_defer) или
    заказ оформится; иначе - та же причина, с которой откажет _place_order."""
    if all(state.resources.get(r, 0) >= amount for r, amount in cost.items()):
        return None
    if is_already_ordered(state, kind, target_id):
        return None
    return _order_failure(state, cost, output=action_order_output(state, kind, target_id, scale))[0]


def _pay_or_defer(
    state: GameState, cost: dict[str, int], action_desc: str, kind: str, target_id: str | None,
    retry_extra: dict | None = None, output: dict | None = None,
) -> ActionResult | None:
    """Общий хвост для действий с ценой. Три исхода:
    - None - всего хватает, cost списан, вызывающий продолжает применять эффект сейчас;
    - ActionResult(True, pending=True) - не хватает, но автокрафт это сделает (или не
      хватает только энергии, и она притечёт): оформлен ЗАКАЗ (state.orders) - всё нужное
      изъято со склада в его резерв, действие выполнится само по готовности
      (_process_orders). Повторный клик по тому же действию, пока заказ идёт, нового
      не оформляет и хода не тратит (отказ);
    - ActionResult(False, ...) - настоящая ошибка, ничего не изменилось.

    retry_extra - доп. поля для повторного выполнения (например, "scale" ручного
    действия, см. do_manual_action), иначе действие по готовности откатилось бы на
    параметры по умолчанию. output - что действие положит на склад (action_order_output):
    без места под него заказ не оформляется."""
    if all(state.resources.get(r, 0) >= amount for r, amount in cost.items()):
        for r, amount in cost.items():
            state.resources[r] -= amount
        return None
    if is_already_ordered(state, kind, target_id):
        return ActionResult(False, f"{action_desc}: уже заказано, ждёт выполнения.")
    failure, order = _place_order(
        state, kind, target_id, action_desc, cost, extra=retry_extra,
        final_energy=cost.get(ENERGY_RESOURCE_ID, 0), output=output,
    )
    if order is not None:
        if not order["steps"]:
            order["waits_energy"] = True
            return ActionResult(True, _energy_wait_message(state, cost, action_desc), pending=True)
        return ActionResult(
            True, f"Заказ оформлен: {action_desc} - недостающее сделает автокрафт, действие выполнится само.",
            pending=True,
        )
    if failure.reason == "no_processor":
        # Автокрафта в игре ещё нет - звучит как обычная нехватка при ручном крафте.
        message = f"Не хватает ресурсов на {action_desc}."
        shortfall = _direct_shortfall(state, cost)
        if shortfall:
            message += f" Не хватает: {_format_shortfall(shortfall)}."
        return ActionResult(False, message)
    # failure.missing - то, что автокрафт сделать не может (сырьё и ресурсы, чей рецепт
    # ему недоступен), уже пересчитанное по тому же дереву, что и заказ.
    if failure.reason == "no_recipe":
        return ActionResult(
            False,
            f"Не хватает ресурсов на {action_desc}. Не хватает: {_format_shortfall(failure.missing)}.{cycle_note(failure.missing)}",
        )
    message = f"Не хватает ресурсов на {action_desc}: {describe_autocraft_failure(failure)}."
    if failure.missing:
        message += f" Не хватает сырья: {_format_shortfall(failure.missing)}."
    return ActionResult(False, message)


def _energy_wait_message(state: GameState, cost: dict[str, int], action_desc: str) -> str:
    need = cost.get(ENERGY_RESOURCE_ID)
    have = state.resources.get(ENERGY_RESOURCE_ID, 0)
    note = energy_wait_note(state, need or 0)
    return (
        f"{action_desc} ждёт энергию (нужно {_format_amount(need or 0)}, есть {_format_amount(have)}) - "
        f"выполнится само, когда её накопится достаточно." + (f" Сейчас {note}." if note else "")
    )


def _matching_effects(building_id: str, techs: set[str]) -> list[dict]:
    effects = []
    for tech_id, tech in TECHS.items():
        if tech_id not in techs:
            continue
        for effect in tech.get("effects", []):
            if effect["building"] in (building_id, "*"):
                effects.append(effect)
    return effects


def _purchased_upgrade_value(purchased: list[str], upgrade: dict) -> float | None:
    """Эффективное value ОДНОГО апгрейда с учётом того, сколько раз он реально
    куплен (purchased.count) - None, если не куплен вовсе (0 раз). Обычный (не
    repeatable) апгрейд не может быть куплен больше одного раза (см.
    purchase_building_upgrade/purchase_farm_instance_upgrade) - value как есть.
    repeatable-апгрейд СТАКАЕТСЯ (value * количество покупок) - тем самым одна
    запись в content.json заменяет N вручную прописанных дискретных тиров
    ("+1 слот", купленный трижды, это +3 слота, а не "сильнейшее из трёх
    одинаковых +1"). Смешивать repeatable и обычные тиры ОДНОГО типа
    (см. _apply_building_upgrades/_farm_upgrade_multiplier - "среди купленных
    действует только сильнейшее") можно, но отвечает за осмысленность автор
    контента - движок это не запрещает."""
    times = purchased.count(upgrade["id"])
    if times <= 0:
        return None
    if upgrade.get("repeatable"):
        return upgrade["value"] * times
    return upgrade["value"]


def repeatable_upgrade_cost(upgrade: dict, times_purchased: int) -> dict[str, int]:
    """Цена СЛЕДУЮЩЕЙ покупки repeatable-апгрейда - базовая cost, умноженная на
    cost_growth в степени "сколько раз уже куплен" (0-я покупка - по базовой
    цене, 1-я - cost_growth^1 раз дороже и т.д.), округлённая по правилам ресурса
    (см. _round_amounts). Для обычного (не repeatable) апгрейда, times_purchased
    всегда 0, а cost_growth не задан (по умолчанию 1) - возвращает базовую cost
    как есть, так что эту же функцию можно звать безусловно."""
    growth = upgrade.get("cost_growth", 1)
    if growth == 1 or times_purchased <= 0:
        return dict(upgrade["cost"])
    return _round_amounts({r: v * (growth ** times_purchased) for r, v in upgrade["cost"].items()})


def _apply_building_upgrades(
    building_id: str, purchased: list[str], produces: dict[str, float], consumes: dict[str, float],
    scale_override: float | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Общий множительный хвост для effective_building_stats (produces/consumes
    ПОСТРОЙКИ) и effective_manual_action_stats (gain/cost ЗАВИСИМОГО ручного
    действия, см. requires_building) - три жёстко зашитых типа улучшений
    (BUILDINGS[building_id]["upgrades"]), не универсальный конструктор:
    - "scale" домножает СРАЗУ ВСЕ produces И consumes (весь рецепт целиком, тот же
      множитель для каждого ресурса) на value - ЛЮБОЕ положительное число, не
      обязательно целое (было ограничение "целое от 2" - снято, см. ниже про
      прогрессии).
    - "energy_efficiency" домножает ТОЛЬКО consumes[энергия] (обычно <1 - снижает трату).
    - "generation_efficiency" домножает ТОЛЬКО produces[энергия] (обычно >1 - для
      генераторов; автор контента сам следит, что добавляет такое только генераторам).

    Среди купленных улучшений ОДНОГО типа действует только САМОЕ СИЛЬНОЕ - value
    остальных купленных того же типа просто игнорируется, они НЕ перемножаются и
    НЕ складываются. "Сильнее" = больше для scale/generation_efficiency (растут
    вверх от 1), но МЕНЬШЕ для energy_efficiency (снижение сильнее, чем ближе к 0).
    Так была ЗАДУМАНА поддержка прогрессий вида "1:1 -> 2:2 -> 3:3 -> 4:4": каждый
    уровень задаёт свой АБСОЛЮТНЫЙ целевой множитель ОТНОСИТЕЛЬНО БАЗЫ (2, 3, 4 -
    и всё, без единой дроби), а не домножается на предыдущий уровень. Раньше
    (перемножение) для шага "3:3 -> 4:4" при уже купленном x3 требовалось бы купить
    ещё value=4/3=1.3333... - у такой дроби нет ТОЧНОГО представления в float, и с
    ростом уже накопленного множителя (5:5->6:6 требует 6/5=1.2, 9:9->10:10 -
    10/9=1.111...) относительная погрешность округления растёт, а _round_amounts
    (math.ceil) на самой границе мог посчитать на единицу больше или меньше
    задуманного. При "действует только сильнейшее" каждый уровень абсолютен и
    точен сам по себе - переполнения дробей просто неоткуда взяться. Купить более
    слабый уровень ПОСЛЕ более сильного не ошибка - просто не даёт отдельного
    эффекта, он уже перекрыт более сильным (как и с тирами инструментов).

    scale_override - "Ползунок масштаба ручного действия" (см. CLAUDE.md,
    do_manual_action/effective_manual_action_stats): если задан, ЗАМЕНЯЕТ собой
    scale, вычисленный из purchased (купленный scale-апгрейд тем самым задаёт
    только ВЕРХНЮЮ ГРАНИЦУ, а не обязательный множитель) - energy_efficiency/
    generation_efficiency при этом по-прежнему считаются как обычно, от
    purchased, и НЕ переопределяются (у них нет понятия "ползунка" - это
    свойство самой постройки/технологии, а не выбор игрока за клик)."""
    if not purchased and scale_override is None:
        return produces, consumes
    scale = 1
    energy_efficiency = 1
    generation_efficiency = 1
    for upgrade in BUILDINGS[building_id].get("upgrades", []):
        value = _purchased_upgrade_value(purchased, upgrade)
        if value is None:
            continue
        if upgrade["type"] == "scale":
            scale = max(scale, value)
        elif upgrade["type"] == "energy_efficiency":
            energy_efficiency = min(energy_efficiency, value)
        elif upgrade["type"] == "generation_efficiency":
            generation_efficiency = max(generation_efficiency, value)
    if scale_override is not None:
        scale = scale_override
    if scale != 1:
        # produces может содержать случайный диапазон (см. _is_range_amount) -
        # _scale_amount домножает ОБЕ границы, а не только число, consumes
        # диапазонов не бывает (см. CLAUDE.md), но _scale_amount работает и с
        # обычным числом точно так же, как раньше "v * scale".
        produces = {r: _scale_amount(v, scale) for r, v in produces.items()}
        consumes = {r: _scale_amount(v, scale) for r, v in consumes.items()}
    if energy_efficiency != 1 and ENERGY_RESOURCE_ID in consumes:
        consumes[ENERGY_RESOURCE_ID] = _scale_amount(consumes[ENERGY_RESOURCE_ID], energy_efficiency)
    if generation_efficiency != 1 and ENERGY_RESOURCE_ID in produces:
        produces[ENERGY_RESOURCE_ID] = _scale_amount(produces[ENERGY_RESOURCE_ID], generation_efficiency)
    return produces, consumes


def _round_amounts(amounts: dict[str, float]) -> dict[str, int | float]:
    """Целые ресурсы (RESOURCE_FRACTIONAL[id] по умолчанию False - см. content.json
    resources.*.fractional) всегда округляются В БОЛЬШУЮ сторону (math.ceil), и в
    produces/gain, и в consumes/cost, без исключений - по прямому пожеланию, дробный
    остаток от умножения на множитель улучшения должен работать против игрока
    (полновесная трата, но не полновесная выгода), а не в его пользу. Дробные
    ресурсы (например, энергия - см. ENERGY_RESOURCE_ID) не трогаются вовсе.
    Значение-диапазон (см. _is_range_amount) округляется по ОБЕИМ границам той же
    самой формулой - сам бросок (см. _roll_range_amounts) происходит позже."""
    def _round_one(r: str, v):
        if RESOURCE_FRACTIONAL.get(r, False):
            return v
        if _is_range_amount(v):
            return {"min": math.ceil(v["min"]), "max": math.ceil(v["max"])}
        return math.ceil(v)

    return {r: _round_one(r, v) for r, v in amounts.items()}


def effective_building_stats(
    building_id: str, techs: set[str], building_upgrades: dict[str, list[str]] | None = None,
    matching_effects: list[dict] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Базовые produces/consumes постройки + бонусы от изученных апгрейд-технологий
    (аддитивные эффекты, см. _matching_effects) + множители от купленных улучшений
    ЭТОЙ постройки (см. _apply_building_upgrades). building_upgrades - обычно
    state.building_upgrades целиком (не только этой постройки) - None/пусто
    эквивалентно "ничего не куплено". Множители улучшений применяются К ИТОГУ ПОСЛЕ
    тех-эффектов, а не к голой базе - порядок "что куплено раньше, технология или
    улучшение" на результат не влияет. Итог округляется по правилам ресурса
    (см. _round_amounts) - именно здесь, а не в advance_turn/UI, чтобы ВСЕ
    потребители этой функции (реальное производство, UI, превью улучшений)
    видели одни и те же, уже округлённые числа. Значение любого ресурса в
    produces (не в consumes - см. content_validation.py) может быть диапазоном
    {"min","max"} вместо плоского числа (случайное produce, равномерное
    распределение) - здесь он только округляется по границам (_round_amounts) и
    сдвигается/домножается тех-эффектами/апгрейдами как целое, САМ БРОСОК
    (_roll_range_amounts) делает вызывающий (advance_turn) непосредственно перед
    начислением, не эта функция - иначе повторные вызовы (например, из UI-превью)
    давали бы каждый раз новое случайное число вместо стабильного диапазона.

    matching_effects - _matching_effects(building_id, techs), если вызывающий
    (advance_turn) уже посчитал его сам ради effective_breakdown_chance
    (обеим функциям нужен один и тот же O(число техов) обход) - None (по
    умолчанию) считает внутри, как раньше, ничего не меняя для остальных
    вызывающих (web_ui.py, тесты)."""
    base = BUILDINGS[building_id]
    produces = dict(base["produces"])
    consumes = dict(base["consumes"])
    if matching_effects is None:
        matching_effects = _matching_effects(building_id, techs)
    for effect in matching_effects:
        for r, amount in effect.get("produces", {}).items():
            produces[r] = _add_amount(produces.get(r, 0), amount)
        for r, amount in effect.get("consumes", {}).items():
            consumes[r] = consumes.get(r, 0) + amount

    purchased = (building_upgrades or {}).get(building_id, [])
    produces, consumes = _apply_building_upgrades(building_id, purchased, produces, consumes)
    return _round_amounts(produces), _round_amounts(consumes)


def effective_manual_action_stats(
    action_id: str, state: GameState, building_upgrades: dict[str, list[str]] | None = None,
    chosen_scale: int | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Базовые cost/gain ручного действия + множители от купленных улучшений
    постройки, от которой действие зависит (action_mechanism_building - свой
    requires_building действия или унаследованный от его локации, см.
    _apply_building_upgrades). Именно так улучшения "механизма, который сам по
    себе ничего не производит" (пустые produces/consumes у постройки) реально влияют
    на игру: постройка открывает ЛОКАЦИЮ с ручными действиями (см. requires_building
    у локаций), а уже ИХ cost/gain улучшает апгрейд постройки. Действие без
    requires_building возвращает свой cost/gain как есть (округлёнными по правилам
    ресурса - см. _round_amounts, на случай если автор контента сам вписал в
    content.json дробное число для целочисленного ресурса).
    building_upgrades - как у effective_building_stats: по умолчанию (None) берётся
    state.building_upgrades целиком, но можно подставить ГИПОТЕТИЧЕСКИЙ словарь
    (например, с одним ещё не купленным улучшением) - для превью "было/станет"
    в tools/web_ui.py:building_upgrades_view, не трогая настоящий state.
    chosen_scale - "Ползунок масштаба ручного действия" (см. CLAUDE.md, do_manual_action):
    None (по умолчанию) - действует КУПЛЕННЫЙ scale-апгрейд целиком, как раньше
    (используется автокрафтом/превью улучшений - там нет и не должно быть понятия
    "выбор игрока за клик"); целое число - домножает cost/gain РОВНО на это число
    вместо купленного максимума (вызывающий - do_manual_action - обязан сам зажать
    его в [1, mechanism_scale_cap(...)] ДО вызова, здесь никакой проверки границ
    нет). gain может содержать диапазон {"min","max"} (см.
    effective_building_stats) - роняет бросок на do_manual_action, не сюда, по
    той же причине. tool_scaling - необязательное generic-поле {"tool": id}
    (см. CLAUDE.md): домножает ТОЛЬКО gain (cost не трогает) на
    ТЕКУЩИЙ тир указанного инструмента (tool_tier, дефолт тира без инструмента
    = 1, то есть без какого-либо инструмента множитель нейтрален) - применяется
    ДО апгрейдов постройки, но т.к. оба шага чисто мультипликативные, порядок
    между ними математически не важен."""
    if building_upgrades is None:
        building_upgrades = state.building_upgrades
    action = MANUAL_ACTIONS[action_id]
    gain = dict(action["gain"])
    cost = dict(action["cost"])
    tool_scaling = action.get("tool_scaling")
    if tool_scaling:
        multiplier = tool_tier(state, tool_scaling["tool"])
        if multiplier != 1:
            gain = {r: _scale_amount(v, multiplier) for r, v in gain.items()}
    building_id = action_mechanism_building(action)
    if building_id:
        purchased = building_upgrades.get(building_id, [])
        gain, cost = _apply_building_upgrades(building_id, purchased, gain, cost, scale_override=chosen_scale)
    return _round_amounts(cost), _round_amounts(gain)


def manual_action_duration(
    action_id: str, state: GameState, building_upgrades: dict[str, list[str]] | None = None,
) -> int:
    """Реальная длительность (в ходах) многоходового действия
    (manual_actions.*.duration_turns) с учётом купленных апгрейдов типа
    "action_duration_reduction" постройки, от которой действие зависит
    (requires_building) - тот же принцип, что effective_manual_action_stats
    (действие без requires_building или с duration_turns <= 1 возвращается как
    есть, апгрейд такого действия не касается). Среди нескольких купленных
    апгрейдов этого типа действует только САМОЕ СИЛЬНОЕ (наибольшая доля
    сокращения) - как у scale/generation_efficiency, не перемножаются.
    Округление - ceil (та же логика, что _round_amounts: дробный остаток
    работает НЕ в пользу игрока), минимум 1 ход."""
    duration = MANUAL_ACTIONS[action_id].get("duration_turns") or 1
    if duration <= 1:
        return duration
    building_id = action_mechanism_building(MANUAL_ACTIONS[action_id])
    if not building_id:
        return duration
    if building_upgrades is None:
        building_upgrades = state.building_upgrades
    purchased = building_upgrades.get(building_id, [])
    reduction = 0.0
    for upgrade in building_upgrade_defs(building_id):
        if upgrade["type"] != "action_duration_reduction":
            continue
        value = _purchased_upgrade_value(purchased, upgrade)
        if value is not None:
            reduction = max(reduction, value)
    if reduction <= 0:
        return duration
    return max(1, math.ceil(duration * (1 - reduction)))


def mechanism_scale_cap(building_id: str, state: GameState) -> int:
    """Наибольший ЦЕЛЫЙ уровень scale-улучшения, купленного для этой постройки -
    1, если ни одного scale-улучшения не куплено (или у постройки вообще нет
    upgrades). Это ВЕРХНЯЯ ГРАНИЦА "ползунка масштаба" ручных действий, зависящих
    от неё через requires_building (см. do_manual_action) - "Ползунок масштаба
    ручного действия" в CLAUDE.md: раньше купленный scale-апгрейд был ОБЯЗАТЕЛЬНЫМ
    множителем для КАЖДОГО действия на этом механизме сразу; теперь он лишь
    открывает диапазон, а КОНКРЕТНОЕ действие (клик) выбирает игрок ползунком от 1
    до этого предела - так на одном и том же механизме можно гнать дешёвый ресурс
    большими партиями, а дорогой - по одному, не будучи обязанным делать сразу
    много и того, и другого.

    Логика выбора "самого сильного среди купленных" - ТА ЖЕ, что в
    _apply_building_upgrades, продублирована здесь для одного этого типа
    (scale), потому что здесь не нужны produces/consumes, только само число.

    scale САМ упгрейд может быть НЕцелым (content.json это разрешает - см.
    "Улучшения построек" в CLAUDE.md, ради прогрессий вроде "0.5"), но ползунок -
    дискретный и целочисленный по прямому пожеланию ("именно дискретный ползунок
    целого количества ресурса в пропорции, а не поле куда игрок пишет число") -
    предлагать позицию вроде "2.5" в целочисленном ползунке нечем, поэтому
    округляем ВНИЗ (math.floor)."""
    cap = 1
    purchased = state.building_upgrades.get(building_id, [])
    for upgrade in BUILDINGS[building_id].get("upgrades", []):
        if upgrade["type"] != "scale":
            continue
        # _purchased_upgrade_value - как в _apply_building_upgrades: repeatable-scale,
        # купленный N раз, это value * N, иначе ползунок не доставал бы до масштаба,
        # который автокрафт и клик без ползунка уже применяют.
        value = _purchased_upgrade_value(purchased, upgrade)
        if value is not None:
            cap = max(cap, value)
    return math.floor(cap)


def effective_breakdown_chance(
    building_id: str, techs: set[str], matching_effects: list[dict] | None = None,
    purchased: list[str] | None = None,
) -> float:
    """matching_effects - см. effective_building_stats: тот же переиспользуемый
    результат _matching_effects, если вызывающий уже посчитал его сам.
    purchased - купленные улучшения ЭТОЙ постройки (state.building_upgrades[bid]) -
    если среди них есть тип "breakdown_immunity", поломка отключена ПОЛНОСТЬЮ
    (0.0), независимо от базового значения и тех-эффектов - апгрейд абсолютен,
    а не ещё один аддитивный/мультипликативный модификатор."""
    if purchased and any(
        u["id"] in purchased and u["type"] == "breakdown_immunity"
        for u in BUILDINGS[building_id].get("upgrades", [])
    ):
        return 0.0
    chance = BUILDINGS[building_id].get("breakdown_chance", 0.0)
    if matching_effects is None:
        matching_effects = _matching_effects(building_id, techs)
    for effect in matching_effects:
        chance += effect.get("breakdown_delta", 0.0)
    return max(0.0, min(1.0, chance))


def working_count(state: GameState, building_id: str) -> int:
    return state.buildings.get(building_id, 0) - state.broken.get(building_id, 0)


def is_building_active_now(building_id: str, daytime: bool) -> bool:
    """Разрешает ли время суток этой постройке сейчас работать - постройки без
    active_period активны всегда. Общая проверка для advance_turn (реально
    решает, произвести ли что-то) и energy_breakdown/theoretical_rates (показать
    честный прогноз на ЭТОТ ход, а не теоретический максимум без учёта времени)."""
    active_period = BUILDINGS[building_id].get("active_period")
    if active_period == "day" and not daytime:
        return False
    if active_period == "night" and daytime:
        return False
    return True


def is_disableable_building(building_id: str) -> bool:
    """Постройка, у которой в меню "Механизмы" есть ползунок включено/выключено -
    производит И потребляет что-то пассивно КАЖДЫЙ ход (оба словаря непустые),
    без похода игрока к ручному действию. Бесплатные генераторы (пустой
    consumes - "не бесплатно" не выполнено) и постройки-"механизмы" совсем без
    produces/consumes (см. CLAUDE.md, requires_building) под ползунок не
    попадают - там нечего выключать."""
    building = BUILDINGS[building_id]
    return bool(building.get("produces")) and bool(building.get("consumes"))


def building_disabled_count(state: GameState, building_id: str) -> int:
    """Сколько РАБОТАЮЩИХ (не сломанных) копий этой постройки игрок вручную
    выключил ползунком (state.building_disabled) - зажато сверху текущим
    working_count, чтобы поломка/разбор уже выключенных копий не оставляла в
    счётчике "фантомные" выключенные экземпляры сверх реально существующих."""
    return min(state.building_disabled.get(building_id, 0), working_count(state, building_id))


def building_active_count(state: GameState, building_id: str) -> int:
    """Сколько РАБОТАЮЩИХ копий этой постройки реально участвуют в пассивном
    produces/consumes СЕЙЧАС (working_count за вычетом выключенных ползунком).
    Используется ТОЛЬКО там, где считается само производство/потребление
    (advance_turn/energy_breakdown/theoretical_rates) - НЕ для гейтов вроде
    requires_building/soul_collector/lab_tier_capacity: выключенная ползунком
    копия всё ещё физически построена и не сломана, ползунок влияет только на
    то, работает ли она в ЭТОТ ход."""
    return working_count(state, building_id) - building_disabled_count(state, building_id)


def set_building_active_count(state: GameState, building_id: str, active_count: int) -> ActionResult:
    """Ползунок меню "Механизмы": задаёт, сколько из РАБОТАЮЩИХ копий постройки
    должны производить/потреблять начиная со следующего advance_turn - остальные
    просто простаивают (не тратят consumes, не дают produces, не могут
    сломаться, тот же принцип, что и active_period вне своего времени суток).
    Значение зажимается в [0, working_count] - выходить за диапазон не ошибка,
    просто округляется. Не тратит ход, как toggle_autocraft/set_energy_priority."""
    if building_id not in BUILDINGS:
        return ActionResult(False, "Неизвестная постройка.")
    if not is_disableable_building(building_id):
        return ActionResult(False, "У этой постройки нечего включать/выключать.")
    working = working_count(state, building_id)
    if working <= 0:
        return ActionResult(False, "Нет ни одной работающей копии этой постройки.")
    active_count = max(0, min(active_count, working))
    state.building_disabled[building_id] = working - active_count
    name = BUILDINGS[building_id]["name"]
    return ActionResult(True, f"«{name}»: включено {active_count} из {working}.")


def action_mechanism_building(action: dict) -> str | None:
    """Постройка-механизм, от которой зависит ручное действие: его СОБСТВЕННЫЙ
    requires_building, а если его нет - requires_building ЛОКАЦИИ, в которой
    действие лежит (action["location"], только сама локация, не рекурсивно по
    parent_location). Так рецепты внутри механизма (печь с сеткой, открытая
    постройкой) автоматически получают и гейт "механизм построен и не сломан", и
    улучшения этой постройки (_apply_building_upgrades/manual_action_duration/
    mechanism_scale_cap) - без того, чтобы автор контента прописывал постройку
    каждому рецепту вручную. Собственный requires_building действия побеждает
    (например, заклёпка на верстаке зависит от прецизионной мастерской).
    ЕДИНСТВЕННАЯ точка этого правила в движке - все чтения "постройки действия"
    идут через неё; content_validation.py держит его копию (держать в паре)."""
    own = action.get("requires_building")
    if own:
        return own
    location_id = action.get("location")
    if location_id and location_id in LOCATIONS:
        return LOCATIONS[location_id].get("requires_building") or None
    return None


def _action_building_ok(action: dict, state: GameState) -> bool:
    """Постройка-механизм ручного действия (action_mechanism_building - своя или
    унаследованная от локации) - необязательный доп. гейт: нужна хотя бы
    одна РАБОТАЮЩАЯ (не сломанная целиком) копия конкретной постройки, а не просто
    "когда-то построена". Общий для клика игрока (is_manual_action_unlocked) и
    автокрафта (_aggregate_autocraft_demand, _process_autocraft_jobs)."""
    req_building = action_mechanism_building(action)
    return not req_building or working_count(state, req_building) > 0


def is_manual_action_unlocked(action_id: str, state: GameState) -> bool:
    action = MANUAL_ACTIONS[action_id]
    req_tech = action["requires_tech"]
    if req_tech and req_tech not in state.techs:
        return False
    if not _action_building_ok(action, state):
        return False
    # requires_tool_tiers - необязательный доп. гейт: для КАЖДОГО перечисленного
    # инструмента текущий тир должен входить в явно отмеченный список тиров (галочки
    # в редакторе, а не порог "начиная с") - см. "Хардкод id"/раздел про инструменты
    # в CLAUDE.md. Несколько инструментов в одном действии - через И, как и с
    # requires_tech/requires_building. Инструмент, не упомянутый в словаре вовсе,
    # ничего не ограничивает.
    for tool_id, allowed_tiers in action.get("requires_tool_tiers", {}).items():
        if tool_tier(state, tool_id) not in allowed_tiers:
            return False
    return True


def is_building_unlocked(building_id: str, state: GameState) -> bool:
    building = BUILDINGS[building_id]
    req_tech = building["requires_tech"]
    if req_tech and req_tech not in state.techs:
        return False
    # requires_building - необязательный доп. гейт, тот же принцип, что у ручных
    # действий (is_manual_action_unlocked): нужна хотя бы одна РАБОТАЮЩАЯ (не
    # сломанная целиком) копия конкретной постройки, а не просто "когда-то
    # построена". Сломай единственную копию - постройка снова скрывается из
    # каталога строительства, как и с технологией.
    req_building = building.get("requires_building")
    if req_building and working_count(state, req_building) <= 0:
        return False
    return True


def is_tech_available(tech_id: str, state: GameState) -> bool:
    if tech_id in state.techs:
        return False
    return all(req in state.techs for req in TECHS[tech_id]["requires"])


def available_manual_actions(state: GameState) -> list[str]:
    return [a for a in MANUAL_ACTIONS if is_manual_action_unlocked(a, state)]


def is_location_unlocked(location_id: str, state: GameState) -> bool:
    loc = LOCATIONS[location_id]
    req = loc.get("requires_tech")
    if req and req not in state.techs:
        return False
    # requires_building - необязательный доп. гейт, тот же принцип, что у построек/
    # ручных действий (is_building_unlocked/is_manual_action_unlocked): нужна хотя бы
    # одна РАБОТАЮЩАЯ (не сломанная целиком) копия конкретной постройки. Так постройка-
    # "механизм" (см. CLAUDE.md), которая сама по себе ничего не производит, может
    # открывать СВОЮ локацию с ручными действиями внутри - сломай единственную копию,
    # и локация со всем её содержимым снова скрывается, как и с технологией.
    req_building = loc.get("requires_building")
    if req_building and working_count(state, req_building) <= 0:
        return False
    return True


def available_locations(state: GameState) -> list[str]:
    return [l for l in LOCATIONS if is_location_unlocked(l, state)]


def location_children(parent_id: str | None, state: GameState) -> list[str]:
    """Дочерние локации данной - локации, вложенное меню которых образует полноценное
    дерево (см. LOCATIONS[...]["parent_location"], необязательное поле). parent_id=None -
    локации корневого уровня (без parent_location или с ним, равным null) - ровно
    так же, как action без location лежит в корне (см. manual_actions_at ниже).
    Открытость технологией (is_location_unlocked) проверяется как обычно - неоткрытая
    дочерняя локация просто не появляется в списке, даже если сама технология родителя
    уже изучена (родительство не даёт технологию автоматически, это независимые гейты)."""
    return [
        lid for lid in LOCATIONS
        if LOCATIONS[lid].get("parent_location") == parent_id and is_location_unlocked(lid, state)
    ]


def manual_actions_at(location_id: str | None, state: GameState) -> list[str]:
    """Доступные (открытые технологией) ручные действия конкретной локации.
    location_id=None - действия без своей локации, лежащие в КОРНЕ дерева локаций
    (необязательное поле MANUAL_ACTIONS[...]["location"])."""
    return [
        a for a in available_manual_actions(state)
        if MANUAL_ACTIONS[a].get("location") == location_id
    ]


def location_crafting(location_id: str) -> dict | None:
    """locations.*.crafting ({"width","height"}) - делает локацию механизмом-
    верстаком (см. CLAUDE.md "Крафт на сетке"). None у обычной локации-списка."""
    return LOCATIONS[location_id].get("crafting")


def location_stacks(location_id) -> bool:
    """crafting.stacks - механизм-сборщик: форма не важна, в ячейке лежит СТОПКА
    (ресурс + количество), рецепт узнаётся по набору стопок, который обязан ровно
    совпасть с ценой (см. _normalize_stacks). Узоров у рецептов такой локации нет."""
    if location_id is None or location_id not in LOCATIONS:
        return False
    return bool((location_crafting(location_id) or {}).get("stacks"))


def action_pattern(action_id: str) -> dict | None:
    """manual_actions.*.pattern ({"rows","key"}) - узор рецепта на сетке. None у
    действия, которое остаётся обычной карточкой даже внутри локации-механизма
    (см. CLAUDE.md, пример - плавка, тратящая прочность молота)."""
    return MANUAL_ACTIONS[action_id].get("pattern")


def is_grid_action(action_id: str) -> bool:
    """Действие собирается в модалке механизма: у него есть узор, либо оно живёт в
    сборщике (location_stacks) - там рецептом служит сама цена, узор не нужен."""
    return action_pattern(action_id) is not None or location_stacks(MANUAL_ACTIONS[action_id].get("location"))


def building_pattern(building_id: str) -> dict | None:
    """buildings.*.pattern - узор постройки на сетке её механизма (buildings.*.location),
    слой над cost, как у ручного действия. None в сборщике (там узоров нет)."""
    return BUILDINGS[building_id].get("pattern")


def building_recipe_cost(building_id: str, state: GameState) -> dict:
    """Что спишет следующий крафт постройки: цена текущего этапа у многоэтапной
    (current_construction_stage), иначе cost - то же, что платит build()."""
    stage = current_construction_stage(building_id, state)
    return stage["cost"] if stage else BUILDINGS[building_id]["cost"]


def grid_buildings_at(location_id: str, state: GameState) -> list[str]:
    """Постройки, которые игрок может собрать в механизме этой локации прямо сейчас
    (buildings.*.location + гейт is_building_unlocked). Лимит max_count НЕ отсекает:
    раскладка всё равно узнаётся, а build() объясняет, почему больше нельзя."""
    if location_crafting(location_id) is None:
        return []
    stacks = location_stacks(location_id)
    return [
        bid for bid, b in BUILDINGS.items()
        if b.get("location") == location_id and is_building_unlocked(bid, state)
        and (stacks or building_pattern(bid) is not None)
    ]


def _pattern_shape(pattern: dict) -> tuple[tuple[str | None, ...], ...]:
    """Нормализованная форма узора рецепта - копия tools/content_validation.py:
    pattern_shape (держать в паре: game/* намеренно не импортирует tools/*, а
    tools/content_validation.py намеренно не импортирует game/*, см. CLAUDE.md
    "Хардкод id"). Обрезка пустых краёв - та же причина, что там: рецепт 2x2
    должен ловиться в любом углу сетки 3x3, а не только у самого края."""
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


def _normalize_grid(cells: list, width: int, height: int) -> tuple[tuple[str | None, ...], ...]:
    """Раскладка игрока (плоский список длиной width*height: id ресурса или None)
    в ту же нормализованную форму, что _pattern_shape/pattern_shape - обрезка
    пустых краёв РОВНО той же логикой, иначе рецепт, разложенный не в углу (0,0),
    никогда бы не совпал с узором из контента."""
    grid = [[cells[y * width + x] for x in range(width)] for y in range(height)]
    rows_used = [y for y in range(height) if any(c is not None for c in grid[y])]
    cols_used = [x for x in range(width) if any(grid[y][x] is not None for y in range(height))]
    if not rows_used or not cols_used:
        return ()
    top, bottom = rows_used[0], rows_used[-1]
    left, right = cols_used[0], cols_used[-1]
    return tuple(
        tuple(grid[y][x] for x in range(left, right + 1))
        for y in range(top, bottom + 1)
    )


def grid_actions_at(location_id: str, state: GameState) -> list[str]:
    """Открытые игроку сеточные действия этой локации (та же гейт-логика, что
    manual_actions_at, плюс фильтр "у действия вообще есть узор")."""
    return [a for a in manual_actions_at(location_id, state) if is_grid_action(a)]


def energy_recipe_pattern(recipe_id: str) -> dict | None:
    """energy_recipes.*.pattern - узор рецепта крафта за энергию в сетке
    преобразователя (CONVERTER_LOCATION_ID), слой НАД consumes ровно так же, как
    manual_actions.*.pattern над cost: узор решает, КАКОЙ рецепт собран, а
    списывает start_energy_recipe всё равно consumes (валидатор требует их
    точного совпадения). Автокрафт и автоматизатор узор не читают вовсе."""
    return ENERGY_RECIPES[recipe_id].get("pattern")


def grid_energy_recipes_at(location_id: str, state: GameState) -> list[str]:
    """Открытые рецепты крафта за энергию с узором, которые собираются в сетке
    ЭТОЙ локации - только у преобразователя (у energy_recipes нет своего поля
    location: все они запускаются из CONVERTER_LOCATION_ID) и только если у него
    есть сетка (locations.converter.crafting)."""
    if location_id != CONVERTER_LOCATION_ID or location_crafting(location_id) is None:
        return []
    return [rid for rid in available_energy_recipes(state) if energy_recipe_pattern(rid) is not None]


# state.pinned_recipes хранит строки: id сеточного ручного действия как есть (так
# было с самого начала - старые сохранения не мигрируются) или "energy_recipe:<id>"
# для рецепта крафта за энергию - id двух разделов content.json друг от друга
# не зависят и могут совпасть, префикс не даёт им перепутаться.
ENERGY_RECIPE_KEY_PREFIX = "energy_recipe:"
# Ритуал (ritual_recipes) - не сеточный рецепт, но заказ «×N» адресует его тем же ключом.
RITUAL_RECIPE_KEY_PREFIX = "ritual_recipe:"
# Репликация ресурса (resources.*.replication_cost): target_id - id РЕСУРСА. Ключ
# заказа «×N» (N - единицы ресурса) и приоритета в state.action_priority.
REPLICATOR_KEY_PREFIX = "replicator:"
# Постройка, собираемая в механизме (buildings.*.location) - сеточный рецепт, но не
# заказ и не цель автоматизатора: крафт сразу ставит постройку (build()).
BUILDING_KEY_PREFIX = "building:"


def grid_recipe_key(kind: str, target_id: str) -> str:
    """Ключ рецепта (kind - из AUTOMATION_TARGET_KINDS, "replicator" или "building") в
    state.pinned_recipes, в заказах «рецепт ×N», в state.action_priority и в ответах HTTP."""
    if kind == "energy_recipe":
        return ENERGY_RECIPE_KEY_PREFIX + target_id
    if kind == "ritual_recipe":
        return RITUAL_RECIPE_KEY_PREFIX + target_id
    if kind == "replicator":
        return REPLICATOR_KEY_PREFIX + target_id
    if kind == "building":
        return BUILDING_KEY_PREFIX + target_id
    return target_id


def parse_grid_recipe_key(key: str) -> tuple[str, str]:
    """Обратное к grid_recipe_key: (kind, target_id). Существование id не проверяет."""
    if key.startswith(ENERGY_RECIPE_KEY_PREFIX):
        return "energy_recipe", key[len(ENERGY_RECIPE_KEY_PREFIX):]
    if key.startswith(RITUAL_RECIPE_KEY_PREFIX):
        return "ritual_recipe", key[len(RITUAL_RECIPE_KEY_PREFIX):]
    if key.startswith(REPLICATOR_KEY_PREFIX):
        return "replicator", key[len(REPLICATOR_KEY_PREFIX):]
    if key.startswith(BUILDING_KEY_PREFIX):
        return "building", key[len(BUILDING_KEY_PREFIX):]
    return "manual_action", key


def _shape_matches(pattern: dict, mirror: bool, normalized: tuple) -> bool:
    shape = _pattern_shape(pattern)
    if shape == normalized:
        return True
    return mirror and tuple(tuple(reversed(row)) for row in shape) == normalized


def _normalize_stacks(cells: list) -> dict[str, float]:
    """Раскладка сборщика (location_stacks) - ячейка None или {"id", "amount"} (голый
    id считается стопкой из 1) - в набор ресурс -> сумма по всем ячейкам: форма и
    порядок не важны. Ячейка с неизвестным ресурсом или неположительным количеством
    делает раскладку непригодной ({}): раскладка приходит из тела HTTP-запроса."""
    total: dict[str, float] = {}
    for cell in cells:
        if cell is None:
            continue
        if isinstance(cell, str):
            rid, amount = cell, 1
        elif isinstance(cell, dict):
            rid, amount = cell.get("id"), cell.get("amount")
        else:
            return {}
        if (rid not in RESOURCE_NAMES or isinstance(amount, bool)
                or not isinstance(amount, (int, float)) or amount <= 0):
            return {}
        total[rid] = total.get(rid, 0) + amount
    return total


def stack_recipe_cost(cost: dict) -> dict:
    """Какие стопки нужно разложить в сборщике под эту цену: всё, кроме энергии - она,
    как и в узоре, в ячейку не кладётся и списывается сверх (см. check_grid_pattern)."""
    return {rid: amount for rid, amount in cost.items() if rid != ENERGY_RESOURCE_ID}


def _stacks_match(cost: dict, laid: dict[str, float]) -> bool:
    need = stack_recipe_cost(cost)
    return bool(need) and set(need) == set(laid) and all(abs(laid[r] - need[r]) < 1e-9 for r in need)


def match_grid_target(state: GameState, location_id: str, placed: list) -> tuple[str, str] | None:
    """Принимает раскладку игрока на сетке локации-механизма (список длиной
    width*height: id ресурса в ячейке или None) и возвращает собранный рецепт
    как (kind, id) - kind из AUTOMATION_TARGET_KINDS или "building": сеточное
    ручное действие, рецепт крафта за энергию (только в преобразователе, см.
    grid_energy_recipes_at) или постройка (buildings.*.location). None - если
    сетки у локации нет, раскладка пуста или не совпала ни с одним узором
    ОТКРЫТОГО рецепта этой локации (иначе игрок мог бы собрать рецепт технологии,
    которую ещё не исследовал). Матчит только рецепты ЭТОЙ локации - сетка
    соседнего механизма своих рецептов сюда не приносит. Учитывает pattern_mirror -
    зеркальный по горизонтали узор тоже засчитывается.

    Сборщик (location_stacks): ячейка - стопка {"id", "amount"}, форма не важна,
    набор стопок обязан РОВНО совпасть с ценой рецепта из контента (у постройки -
    с ценой текущего этапа) без энергии; иначе одинаковые по составу рецепты
    (дерево + камень у половины построек) было бы не различить."""
    crafting = location_crafting(location_id) if location_id in LOCATIONS else None
    if crafting is None or len(placed) != crafting["width"] * crafting["height"]:
        return None
    if location_stacks(location_id):
        laid = _normalize_stacks(placed)
        if not laid:
            return None
        for action_id in grid_actions_at(location_id, state):
            if _stacks_match(MANUAL_ACTIONS[action_id].get("cost") or {}, laid):
                return "manual_action", action_id
        for building_id in grid_buildings_at(location_id, state):
            if _stacks_match(building_recipe_cost(building_id, state), laid):
                return "building", building_id
        return None
    if any(cell is not None and not isinstance(cell, str) for cell in placed):
        return None
    normalized = _normalize_grid(placed, crafting["width"], crafting["height"])
    if not normalized:
        return None
    for action_id in grid_actions_at(location_id, state):
        if _shape_matches(action_pattern(action_id), bool(MANUAL_ACTIONS[action_id].get("pattern_mirror")), normalized):
            return "manual_action", action_id
    for recipe_id in grid_energy_recipes_at(location_id, state):
        if _shape_matches(energy_recipe_pattern(recipe_id), bool(ENERGY_RECIPES[recipe_id].get("pattern_mirror")), normalized):
            return "energy_recipe", recipe_id
    for building_id in grid_buildings_at(location_id, state):
        if _shape_matches(building_pattern(building_id), bool(BUILDINGS[building_id].get("pattern_mirror")), normalized):
            return "building", building_id
    return None


def match_grid(state: GameState, location_id: str, placed: list) -> str | None:
    """То же, что match_grid_target, но только сеточные ручные действия: id
    действия или None (в том числе когда раскладка собрала рецепт за энергию)."""
    target = match_grid_target(state, location_id, placed)
    return target[1] if target and target[0] == "manual_action" else None


def current_time(state: GameState) -> dict:
    """Игровое время: (state.turn - 1) ходов по TIME['hours_per_turn'] часов с начала
    партии. День считается с 1, час - в диапазоне [0, 24). Отдельного поля в GameState
    не заводим - время полностью выводится из номера хода и настроек контента."""
    hours_per_turn = TIME["hours_per_turn"]
    total_hours = (state.turn - 1) * hours_per_turn
    day, hour = divmod(total_hours, 24)
    return {"day": day + 1, "hour": hour, "is_day": is_daytime_hour(hour)}


def is_daytime_hour(hour: int) -> bool:
    return TIME["day_start_hour"] <= hour < TIME["day_end_hour"]


def is_daytime(state: GameState) -> bool:
    return current_time(state)["is_day"]


def available_buildings(state: GameState) -> list[str]:
    return [b for b in BUILDINGS if is_building_unlocked(b, state)]


def available_techs(state: GameState) -> list[str]:
    return [t for t in TECHS if is_tech_available(t, state)]


def tech_unlocks(tech_id: str) -> dict[str, list[str]]:
    """Что открывает технология - вычисляется на лету из существующих ссылок
    requires_tech у построек/локаций/действий/добычи/тиров инструментов, а не
    хранится отдельным полем (чтобы контент нельзя было рассинхронизировать)."""
    return {
        "buildings": [b for b, d in BUILDINGS.items() if d.get("requires_tech") == tech_id],
        "locations": [l for l, d in LOCATIONS.items() if d.get("requires_tech") == tech_id],
        "manual_actions": [a for a, d in MANUAL_ACTIONS.items() if d.get("requires_tech") == tech_id],
        "mining": [r for r, d in MINING_INFO.items() if d.get("requires_tech") == tech_id],
        "tools": [
            t["name"] for tool in TOOLS.values() for t in tool["tiers"] if t.get("requires_tech") == tech_id
        ],
        "energy_recipes": [r for r, d in ENERGY_RECIPES.items() if d.get("requires_tech") == tech_id],
    }


def _mining_researched(resource_id: str, state: GameState) -> bool:
    """Гейт СПАВНА руды на поле шахты - только технология. Тир инструмента
    решает не появление руды на поле, а возможность её сломать (см.
    hit_mine_field) - недоступная по тиру руда всё равно видна на поле (с
    замочком), просто не поддаётся удару, пока кирка не подрастёт."""
    requires_tech = MINING_INFO[resource_id]["requires_tech"]
    return not requires_tech or requires_tech in state.techs


def _researched_ores(state: GameState) -> list[str]:
    return [rid for rid in MINING_INFO if _mining_researched(rid, state)]


def mine_field_size() -> int:
    return MINE_FIELD["size"]


def _dig_tool_id() -> str:
    return MINE_FIELD["dig_tool"]


def pickaxe_area(state: GameState) -> int:
    """Сторона N x N зоны удара - тир инструмента MINE_FIELD['dig_tool']."""
    tool_id = _dig_tool_id()
    tier = tool_tier(state, tool_id)
    return TOOLS[tool_id]["tiers"][tier - 1].get("mining_area", 1)


def pickaxe_power(state: GameState) -> int:
    """Урон по прочности клетки за один удар - тот же тир, что и pickaxe_area."""
    tool_id = _dig_tool_id()
    tier = tool_tier(state, tool_id)
    return TOOLS[tool_id]["tiers"][tier - 1].get("mining_power", 1)


def _mine_field_idx(size: int, r: int, c: int) -> int:
    return r * size + c


def _mine_field_neighbors(size: int, r: int, c: int, *, orthogonal_only: bool) -> list[tuple[int, int]]:
    deltas = (
        [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if orthogonal_only
        else [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)]
    )
    out = []
    for dr, dc in deltas:
        nr, nc = r + dr, c + dc
        if 0 <= nr < size and 0 <= nc < size:
            out.append((nr, nc))
    return out


def _reveal_around(field: dict, r: int, c: int) -> None:
    """Открывает клетку (r, c) и радиус 1 (все 8 соседей) вокруг неё - именно
    так поле "рассеивает туман" вокруг каждой сломанной клетки."""
    size = field["size"]
    cells = field["cells"]
    cells[_mine_field_idx(size, r, c)]["revealed"] = True
    for nr, nc in _mine_field_neighbors(size, r, c, orthogonal_only=False):
        cells[_mine_field_idx(size, nr, nc)]["revealed"] = True


def _has_orthogonal_broken_neighbor(field: dict, r: int, c: int) -> bool:
    """Ортогональная (НЕ диагональная) смежность со сломанной клеткой - именно
    она решает, можно ли ЛОМАТЬ клетку (r, c) в одиночку. Диагональный сосед
    сломанной клетки при этом уже ВИДЕН (см. _reveal_around, радиус 8-связный),
    просто не ломается сам по себе, пока к нему не подойти сбоку."""
    size = field["size"]
    cells = field["cells"]
    return any(
        cells[_mine_field_idx(size, nr, nc)]["broken"]
        for nr, nc in _mine_field_neighbors(size, r, c, orthogonal_only=True)
    )


def _roll_field_cluster_counts(state: GameState) -> dict[str, int]:
    """Состав поля - взвешенная лотерея, а не гарантированный спавн ВСЕХ
    исследованных видов. cluster_budget (общий на всё поле, не растёт с числом
    видов руды) разыгрывается между исследованными видами С ПОВТОРАМИ, вес
    каждого - 1/rarity (та же семантика rarity, что раньше: чем больше число,
    тем реже руда). Сколько раз вид выпал в бюджете - столько у него кластеров
    на этом поле; вид, которому ни разу не повезло, в этот раз просто не
    заспавнится вовсе - именно этого и не хватало (при большом числе видов
    руды старая формула гарантировала минимум 1 кластер КАЖДОМУ, что не
    масштабируется и убивает азарт "что тут вообще есть")."""
    researched = _researched_ores(state)
    if not researched:
        return {}
    weights = [1 / MINING_INFO[rid]["rarity"] for rid in researched]
    picks = random.choices(researched, weights=weights, k=MINE_FIELD["cluster_budget"])
    return dict(Counter(picks))


def _generate_mine_field(state: GameState) -> dict:
    """Новое поле шахты: вся сетка - порода, центр уже пройден (игрок как бы
    появляется там). Состав руды решает _roll_field_cluster_counts (см. её
    докстринг) - тирный гейт кирки тут ни при чём (см. hit_mine_field), спавн
    зависит только от requires_tech (см. _researched_ores) и результата
    лотереи. Каждый доставшийся кластер кладётся случайным блужданием по
    свободным клеткам, размер - field_cluster_size (min-max клеток)."""
    size = mine_field_size()
    # variant_count - чисто косметический разброс внешнего вида ПОРОДЫ (не руды):
    # rock_icon + rock_icon_variants образуют общий пул спрайтов, index в него
    # кладётся один раз при генерации клетки и просто ездит с ней (см.
    # mine_field_view/web_ui.html: mineFieldCellEl) - никакого влияния на геймплей,
    # только чтобы поле не выглядело одним и тем же тайлом 400 раз подряд.
    variant_count = 1 + len(MINE_FIELD.get("rock_icon_variants", []))
    cells = [
        {
            "resource": None,
            "hp": MINE_FIELD["rock_toughness"],
            "hp_max": MINE_FIELD["rock_toughness"],
            "broken": False,
            "revealed": False,
            "icon_variant": random.randrange(variant_count),
        }
        for _ in range(size * size)
    ]
    center = size // 2
    center_idx = _mine_field_idx(size, center, center)

    def free_orthogonal_neighbors(r: int, c: int) -> list[tuple[int, int]]:
        return [
            (nr, nc)
            for nr, nc in _mine_field_neighbors(size, r, c, orthogonal_only=True)
            if cells[_mine_field_idx(size, nr, nc)]["resource"] is None and _mine_field_idx(size, nr, nc) != center_idx
        ]

    for resource_id, cluster_count in _roll_field_cluster_counts(state).items():
        info = MINING_INFO[resource_id]
        cluster_min, cluster_max = info["field_cluster_size"]
        for _ in range(cluster_count):
            free_cells = [
                (r, c)
                for r in range(size)
                for c in range(size)
                if cells[_mine_field_idx(size, r, c)]["resource"] is None and _mine_field_idx(size, r, c) != center_idx
            ]
            if not free_cells:
                break
            start = random.choice(free_cells)
            target_size = random.randint(cluster_min, cluster_max)
            visited = [start]
            cells[_mine_field_idx(size, *start)]["resource"] = resource_id
            frontier = start
            for _ in range(target_size - 1):
                options = free_orthogonal_neighbors(*frontier)
                if not options:
                    found = False
                    for prior in reversed(visited):
                        options = free_orthogonal_neighbors(*prior)
                        if options:
                            frontier = prior
                            found = True
                            break
                    if not found:
                        break
                nxt = random.choice(options)
                cells[_mine_field_idx(size, *nxt)]["resource"] = resource_id
                visited.append(nxt)
                frontier = nxt
            for (r, c) in visited:
                cell = cells[_mine_field_idx(size, r, c)]
                cell["hp"] = info["toughness"]
                cell["hp_max"] = info["toughness"]

    cells[center_idx]["broken"] = True
    cells[center_idx]["hp"] = 0
    field = {"size": size, "cells": cells}
    _reveal_around(field, center, center)
    return field


def _ensure_mine_field(state: GameState) -> dict:
    """Идемпотентно заводит поле при первом обращении - тот же приём, что
    _ensure_farm_instances, только тут слот один на всю игру (как active_encounter),
    а не словарь экземпляров."""
    if state.active_mine_field is None:
        state.active_mine_field = _generate_mine_field(state)
    return state.active_mine_field


def new_mine_field(state: GameState) -> ActionResult:
    """"Обновить шахту" - полностью выбрасывает текущее поле (недокопанный
    остаток теряется без возврата) и генерирует новое. Как и весь этот блок -
    обычный клик без хода, ничего не стоит (по образцу insert_soul)."""
    state.active_mine_field = _generate_mine_field(state)
    return ActionResult(True, "Заложен новый забой.")


def _mine_field_area_coords(field: dict, area: int, r: int, c: int) -> list[tuple[int, int]]:
    size = field["size"]
    offset = (area - 1) // 2
    coords = []
    for rr in range(r - offset, r - offset + area):
        for cc in range(c - offset, c - offset + area):
            if 0 <= rr < size and 0 <= cc < size:
                coords.append((rr, cc))
    return coords


def hit_mine_field(state: GameState, r: int, c: int) -> ActionResult:
    """Один удар киркой, нацеленный на клетку (r, c) - реально задевает всю
    площадь N x N вокруг неё (pickaxe_area), а не только саму клетку. Область
    ломается ЦЕЛИКОМ, если хотя бы одна её клетка уже сломана или ортогонально
    граничит со сломанной (_has_orthogonal_broken_neighbor) - то есть можно
    "нырнуть" целым блоком в туман, если этот блок хоть где-то касается уже
    пройденного, а не обязательно бить строго по одной клетке за раз. Если
    область вообще не достижима - клик ничего не делает (ActionResult(False)).

    Руда не по тиру кирки (required_tier выше текущего) НЕ ломается, но всё
    равно открывается (видна дальше, с замочком в mine_field_view) - это
    единственная защита от порчи недоступной руды широким ударом: если бы
    клетка портилась без выдачи ресурса, широкая кирка могла бы уничтожать
    ещё не открытую редкую руду впустую.

    Прочность кирки (см. tool_is_broken/_consume_tool_durability) тратится
    РОВНО ОДИН РАЗ ЗА УДАР (за сам клик; холостой удар - только по руде выше тира
    кирки или по выбитым клеткам - отказ без прочности и хода), а не за каждый сломанный этим ударом
    блок - кирка с площадью N x N (pickaxe_area) изнашивается МЕДЛЕННЕЕ в
    пересчёте на блок, чем кирка с площадью 1x1, это и есть часть награды за
    её прокачку (прямое пожелание, 2026-09-16). Сломанная кирка не бьёт вовсе -
    удар отклоняется ДО того, как что-либо потрачено/сломано.

    Если этот удар доломал ПОСЛЕДНЮЮ небитую клетку поля (все cells["broken"]
    == True), забой автоматически перекладывается новым (_generate_mine_field,
    тот же эффект, что ручная new_mine_field) ПРЯМО В ЭТОМ ЖЕ вызове - прямое
    пожелание, 2026-09-16, "не заставлять игрока самого жать «Обновить шахту»
    на пустом поле". Клетка, оставшаяся НЕбитой из-за недостаточного тира кирки
    (any_locked), блокирует автообновление - это и есть "выкопана ПОЛНОСТЬЮ", а
    не "выкопано всё, что доступно сейчас"."""
    field = _ensure_mine_field(state)
    size = field["size"]
    if not (0 <= r < size and 0 <= c < size):
        return ActionResult(False, "Клетка вне поля.")

    dig_tool_id = _dig_tool_id()
    if tool_is_broken(state, dig_tool_id):
        return ActionResult(False, f"«{TOOLS[dig_tool_id]['name']}» сломана - нужно сначала её починить.")

    area = pickaxe_area(state)
    coords = _mine_field_area_coords(field, area, r, c)
    reachable = any(
        field["cells"][_mine_field_idx(size, rr, cc)]["broken"] or _has_orthogonal_broken_neighbor(field, rr, cc)
        for rr, cc in coords
    )
    if not reachable:
        return ActionResult(False, "Слишком глубоко в тумане - нужно сначала прорубиться ближе.")

    luck = _enchant_effect_value(state, dig_tool_id, "yield_multiplier", 1.0)
    power = pickaxe_power(state)
    gained: dict[str, int] = {}
    any_locked = False
    struck = False  # удар пришёлся хоть по одной клетке (иначе - холостой)
    for rr, cc in coords:
        cell = field["cells"][_mine_field_idx(size, rr, cc)]
        if cell["broken"]:
            continue
        resource_id = cell["resource"]
        if resource_id is not None:
            info = MINING_INFO[resource_id]
            if tool_tier(state, info["required_tool"]) < info["required_tier"]:
                cell["revealed"] = True
                any_locked = True
                continue
        cell["revealed"] = True
        cell["hp"] -= power
        struck = True
        if cell["hp"] <= 0:
            cell["broken"] = True
            _reveal_around(field, rr, cc)
            if resource_id is not None:
                info = MINING_INFO[resource_id]
                # Тир кирки решает ТОЛЬКО площадь/урон удара (pickaxe_area/
                # _power) и порог доступности клетки (required_tier выше) -
                # выход с клетки НЕ масштабируется тиром, всегда ровно
                # base_yield * зачарование "Удача" (округление вверх, как и
                # везде у целочисленных ресурсов). Раньше домножался на
                # (tool_tier - required_tier + 1) - убрано по прямому
                # пожеланию, 2026-09-16: "1 клетка должна давать 1 порцию
                # руды, а не расти с прокачкой кирки".
                gained[resource_id] = gained.get(resource_id, 0) + math.ceil(info["base_yield"] * luck)
            else:
                rock_id = MINE_FIELD["rock_resource"]
                gained[rock_id] = gained.get(rock_id, 0) + MINE_FIELD["rock_yield"]

    if not struck:
        # Под ударом только руда выше тира кирки (или уже выбитые клетки): кирка не
        # коснулась породы - ни прочности, ни хода (отказ). Руда при этом видна.
        if any_locked:
            return ActionResult(False, "Тут пока не по зубам этой кирке - нужна кирка выше тиром.")
        return ActionResult(False, "Тут уже всё выбито.")
    _consume_tool_durability(state, dig_tool_id)

    for resource_id, amount in gained.items():
        _add_resource(state.resources, resource_id, amount)

    if not gained:
        message = "Порода устояла."
    else:
        parts = ", ".join(f"+{amount} {RESOURCE_NAMES[rid]}" for rid, amount in gained.items())
        message = f"Добыто: {parts}."

    if all(cell["broken"] for cell in field["cells"]):
        state.active_mine_field = _generate_mine_field(state)
        message += " Забой полностью пройден - заложен новый."
    return ActionResult(True, message)


def scanner_tier(state: GameState) -> int:
    return tool_tier(state, MINE_FIELD["scanner_tool"])


def _scanner_sense_radius(tier: int) -> int | None:
    """Наибольший разблокированный радиус подсветки руды сквозь туман
    (mine_field.scanner_radius_tiers - список {tier, radius}). Среди порогов,
    которым текущий тир щупа уже соответствует, действует САМЫЙ БОЛЬШОЙ
    radius, а не сумма и не первый по списку - тот же принцип "сильнейший
    из купленного", что у building upgrades одного типа. None - ни один
    порог ещё не разблокирован (радиусной подсветки нет вовсе)."""
    best = None
    for entry in MINE_FIELD.get("scanner_radius_tiers", []):
        if tier >= entry["tier"] and (best is None or entry["radius"] > best):
            best = entry["radius"]
    return best


def mine_field_view(state: GameState) -> dict:
    """Сериализуемый вид поля шахты для UI: по клетке - либо просто "в тумане",
    либо {broken, damage_fraction?, resource?, locked?}, плюс текущие
    характеристики кирки и справочник всех видов добычи (для модалки-
    справочника руд, независимо от того, что сейчас реально на поле).

    Щуп (mine_field.scanner_tool) - ЧИСТО ИНФОРМАЦИОННЫЙ инструмент, тир 1
    (дефолтный для всех) ничего не даёт - см. CLAUDE.md про тир 1 = "не
    откалиброван". Дальше пороги независимы друг от друга:
      >= scanner_types_tier       - какие виды руды вообще есть на поле (без
                                     повторов, catalog[].present_on_field)
      >= scanner_counts_tier      - сколько именно КЛЕТОК этой руды на поле
                                     (catalog[].field_count) - только руда,
                                     порода (rock_resource) сюда не входит по
                                     прямому пожеланию, 2026-09-16
      scanner_radius_tiers        - НЕ одиночный порог, а список {tier,
                                     radius}: начиная с любого из этих тиров
                                     руда в РАДИУСЕ radius клеток (Чебышёв, та
                                     же метрика, что у _reveal_around) от
                                     любой уже ОТКРЫТОЙ (revealed) клетки
                                     светится сквозь туман тем же
                                     sensed_resource, что и у полного
                                     сканирования ниже - сама клетка руды
                                     остаётся в тумане, просто уже видна
                                     "где-то рядом копать" (см.
                                     _scanner_sense_radius).
      >= scanner_reveal_all_tier  - клетки руды видны ДАЖЕ в тумане ПОВСЮДУ
                                     (cells[].sensed_resource, без ограничения
                                     радиусом - полностью перекрывает
                                     scanner_radius_tiers, финальный эффект
                                     щупа), сама клетка при этом остаётся
                                     нетронутой fog-of-war логикой (revealed
                                     всё ещё False, ломать нельзя, пока не
                                     дойдёшь обычным путём)."""
    field = _ensure_mine_field(state)
    size = field["size"]
    tier = scanner_tier(state)
    reveal_all = tier >= MINE_FIELD["scanner_reveal_all_tier"]
    sense_radius = None if reveal_all else _scanner_sense_radius(tier)
    revealed_coords = None
    if sense_radius is not None:
        revealed_coords = [divmod(idx, size) for idx, c in enumerate(field["cells"]) if c["revealed"]]

    cells_view = []
    for idx, cell in enumerate(field["cells"]):
        if not cell["revealed"]:
            entry: dict = {"revealed": False}
            if cell["resource"] is not None:
                if reveal_all:
                    entry["sensed_resource"] = cell["resource"]
                elif sense_radius is not None:
                    r, c = divmod(idx, size)
                    if any(max(abs(r - rr), abs(c - cc)) <= sense_radius for rr, cc in revealed_coords):
                        entry["sensed_resource"] = cell["resource"]
            cells_view.append(entry)
            continue
        entry = {"revealed": True, "broken": cell["broken"]}
        if not cell["broken"] and cell["hp"] < cell["hp_max"]:
            entry["damage_fraction"] = 1 - cell["hp"] / cell["hp_max"]
        if cell["resource"] is not None and not cell["broken"]:
            info = MINING_INFO[cell["resource"]]
            entry["resource"] = cell["resource"]
            entry["locked"] = tool_tier(state, info["required_tool"]) < info["required_tier"]
        elif not cell["broken"]:
            entry["icon_variant"] = cell.get("icon_variant", 0)
        cells_view.append(entry)

    show_types = tier >= MINE_FIELD["scanner_types_tier"]
    show_counts = tier >= MINE_FIELD["scanner_counts_tier"]
    field_counts: dict[str, int] = {}
    if show_types:
        for cell in field["cells"]:
            if cell["resource"] is not None:
                field_counts[cell["resource"]] = field_counts.get(cell["resource"], 0) + 1
    catalog = []
    for rid, info in MINING_INFO.items():
        entry = {
            "id": rid,
            "name": RESOURCE_NAMES[rid],
            "required_tier": info["required_tier"],
            "rarity": info["rarity"],
            "field_cluster_size": info["field_cluster_size"],
            "researched": _mining_researched(rid, state),
        }
        if show_types:
            entry["present_on_field"] = rid in field_counts
        if show_counts:
            entry["field_count"] = field_counts.get(rid, 0)
        catalog.append(entry)

    return {
        "size": field["size"],
        "cells": cells_view,
        "pickaxe": {
            "tool_id": _dig_tool_id(),
            "tier": tool_tier(state, _dig_tool_id()),
            "area": pickaxe_area(state),
            "power": pickaxe_power(state),
            "broken": tool_is_broken(state, _dig_tool_id()),
        },
        "scanner_tier": tier,
        "ore_catalog": catalog,
        "rock_resource": MINE_FIELD["rock_resource"],
        "rock_icon": MINE_FIELD.get("rock_icon"),
        "rock_icons": [MINE_FIELD.get("rock_icon")] + list(MINE_FIELD.get("rock_icon_variants", [])),
        "rock_broken_icon": MINE_FIELD.get("rock_broken_icon"),
    }


def tool_tier(state: GameState, tool_id: str) -> int:
    """Текущий тир инструмента - 1 (стартовый, бесплатный тир), если игрок ещё ни
    разу не улучшал именно этот инструмент (см. state.tool_tiers)."""
    return state.tool_tiers.get(tool_id, 1)


def tool_tier_name(state: GameState, tool_id: str) -> str:
    return TOOLS[tool_id]["tiers"][tool_tier(state, tool_id) - 1]["name"]


def next_tool_tier_info(state: GameState, tool_id: str) -> dict | None:
    tiers = TOOLS[tool_id]["tiers"]
    next_index = tool_tier(state, tool_id)  # 0-индексация: следующий тир лежит по текущему тиру
    if next_index >= len(tiers):
        return None
    return tiers[next_index]


def upgradable_tools(state: GameState) -> list[str]:
    """Инструменты, у которых есть следующий тир и для него уже открыта нужная
    технология (если она указана) - используется UI, чтобы не показывать кнопку
    апгрейда, которую всё равно нельзя нажать."""
    result = []
    for tool_id in TOOLS:
        next_info = next_tool_tier_info(state, tool_id)
        if next_info is not None and (not next_info["requires_tech"] or next_info["requires_tech"] in state.techs):
            result.append(tool_id)
    return result


def upgrade_tool(state: GameState, tool_id: str) -> ActionResult:
    tool_name = TOOLS[tool_id]["name"]
    next_info = next_tool_tier_info(state, tool_id)
    if next_info is None:
        return ActionResult(False, f"«{tool_name}» уже максимального тира.")
    if next_info["requires_tech"] and next_info["requires_tech"] not in state.techs:
        return ActionResult(False, f"Для такого инструмента «{tool_name}» не хватает технологии.")
    deferred = _pay_or_defer(state, next_info["cost"], f"улучшение «{tool_name}»", "upgrade_tool", tool_id)
    if deferred is not None:
        return deferred
    state.tool_tiers[tool_id] = tool_tier(state, tool_id) + 1
    # Новый тир - новая полная прочность (см. tool_durability_remaining): ключ
    # просто убирается, а не выставляется в max_durability явно, чтобы функция
    # ниже сама резолвила его лениво (тот же принцип, что и сам tool_tiers).
    state.tool_durability.pop(tool_id, None)
    return ActionResult(True, f"«{tool_name}» улучшен: {next_info['name']} (тир {state.tool_tiers[tool_id]}).")


def _current_tool_tier_def(state: GameState, tool_id: str) -> dict:
    return TOOLS[tool_id]["tiers"][tool_tier(state, tool_id) - 1]


def tool_durability_remaining(state: GameState, tool_id: str) -> int | None:
    """Остаток прочности инструмента на ТЕКУЩЕМ тире - None, если у тира вовсе
    нет max_durability (инструмент не изнашивается, как топор/кирка/меч без
    этого поля). См. content.json tools.*.tiers[].max_durability. Зачарование
    "Прочность" (effect.type == durability_multiplier, универсальное - см.
    enchantment_pool) домножает МАКСИМУМ - уже потраченный остаток (state.
    tool_durability, если он есть) не пересчитывается задним числом, растёт
    только "потолок" для следующего полного ремонта/дефолта."""
    max_durability = _current_tool_tier_def(state, tool_id).get("max_durability")
    if max_durability is None:
        return None
    effective_max = math.ceil(max_durability * _enchant_effect_value(state, tool_id, "durability_multiplier", 1.0))
    return state.tool_durability.get(tool_id, effective_max)


def tool_max_durability(state: GameState, tool_id: str) -> int | None:
    """Полная прочность ТЕКУЩЕГО тира с зачарованием «Прочность» (None - не изнашивается)."""
    max_durability = _current_tool_tier_def(state, tool_id).get("max_durability")
    if max_durability is None:
        return None
    return math.ceil(max_durability * _enchant_effect_value(state, tool_id, "durability_multiplier", 1.0))


def tool_needs_repair(state: GameState, tool_id: str) -> bool:
    """Потрачена ли хоть часть прочности - тогда инструмент можно чинить (в любой
    момент, за полную цену ремонта тира), не дожидаясь, пока он сломается."""
    remaining = tool_durability_remaining(state, tool_id)
    return remaining is not None and remaining < tool_max_durability(state, tool_id)


def tool_is_broken(state: GameState, tool_id: str) -> bool:
    """Инструмент исчерпал прочность (нужен repair_tool, прежде чем его снова
    можно использовать для ремонта поломки, требующей этот инструмент) -
    ЛОЖЬ у инструментов без max_durability вовсе (см. tool_durability_remaining)."""
    remaining = tool_durability_remaining(state, tool_id)
    return remaining is not None and remaining <= 0


def tool_usable_for_repair(state: GameState, tool_id: str) -> bool:
    """"Нужен инструмент X" (breakdown_types.*.required_tools) = игрок им
    просто ВЛАДЕЕТ (тир >= 2, т.е. не "кулаки"/стартовый тир) И этот
    конкретный инструмент сейчас не сломан сам (см. tool_is_broken)."""
    return tool_tier(state, tool_id) >= 2 and not tool_is_broken(state, tool_id)


def _consume_tool_durability(state: GameState, tool_id: str) -> None:
    remaining = tool_durability_remaining(state, tool_id)
    if remaining is None:
        return  # инструмент не изнашивается вовсе
    state.tool_durability[tool_id] = max(0, remaining - 1)


def repair_tool(state: GameState, tool_id: str) -> ActionResult:
    """Платный ремонт инструмента - в любой момент, как только потрачена хоть часть
    прочности (tool_needs_repair), за полную цену ремонта тира: перед долгой вылазкой
    не нужно доламывать кирку до нуля. Тир НЕ теряется, прочность сбрасывается на
    максимум текущего тира. Цена - tiers[тир-1]["repair_cost"] (обязателен в
    контенте, если у тира задан max_durability - см. content_validation.py)."""
    tool_name = TOOLS[tool_id]["name"]
    if not tool_needs_repair(state, tool_id):
        return ActionResult(False, f"«{tool_name}» не нуждается в ремонте.")
    cost = _current_tool_tier_def(state, tool_id).get("repair_cost", {})
    deferred = _pay_or_defer(state, cost, f"ремонт инструмента «{tool_name}»", "repair_tool", tool_id)
    if deferred is not None:
        return deferred
    state.tool_durability.pop(tool_id, None)
    return ActionResult(True, f"«{tool_name}» отремонтирован.")


def tool_enchant_level(state: GameState, tool_id: str, enchant_id: str) -> int:
    """Текущий уровень зачарования enchant_id на ЭТОЙ линейке инструмента - 0,
    если оно не применено (см. state.tool_enchantments/enchant_tool)."""
    return state.tool_enchantments.get(tool_id, {}).get(enchant_id, 0)


def _enchant_effect_value(state: GameState, tool_id: str, effect_type: str, default: float) -> float:
    """Значение эффекта effect_type, действующее на tool_id ПРЯМО СЕЙЧАС - максимум
    среди применённых зачарований этого инструмента, чей content.json enchantments.*.
    effect.type совпадает (обычно ровно одно совпадение, т.к. каждый effect.type
    закреплён за одним зачарованием, но код не полагается на это специально - тот
    же принцип "сильнейшее побеждает", что у building upgrades), иначе default
    (нейтральное значение для этого эффекта - 1.0 для множителей, 0 для добавок)."""
    best = None
    for enchant_id, level in state.tool_enchantments.get(tool_id, {}).items():
        enchant = ENCHANTMENTS.get(enchant_id)
        if not enchant or enchant.get("effect", {}).get("type") != effect_type:
            continue
        levels = enchant.get("levels", [])
        if not (1 <= level <= len(levels)):
            continue
        value = levels[level - 1].get("value", default)
        if best is None or value > best:
            best = value
    return default if best is None else best


def enchantment_pool(tool_id: str) -> list[dict]:
    """Все ТИПЫ зачарований, доступные ДЛЯ ЭТОГО инструмента - те, чей
    applies_to_kind совпадает с tools.*.enchant_kind этой линейки, плюс
    универсальные (applies_to_kind is None, например "Прочность"). Пусто, если у
    инструмента вовсе нет enchant_kind (не зачаровываемый) - см. content.json
    tools.*.enchant_kind. Это и есть "барабан" трёх магических кругов
    (enchant_tool) - порядок совпадает с ENCHANTMENTS в контенте. ОДНА запись на
    enchant_id (не на уровень - см. enchant_tool: совпадение всех трёх кругов
    решает только ТИП, конкретный уровень значения тут не участвует, поэтому
    уровни зачарования сюда не разворачиваются)."""
    kind = TOOLS.get(tool_id, {}).get("enchant_kind")
    if kind is None:
        return []
    pool = []
    for enchant_id, enchant in ENCHANTMENTS.items():
        applies_to_kind = enchant.get("applies_to_kind")
        if applies_to_kind is not None and applies_to_kind != kind:
            continue
        if not enchant.get("levels"):
            continue
        pool.append({"enchant_id": enchant_id})
    return pool


def combat_gear_owned(state: GameState, tool_id: str) -> bool:
    """Есть ли у игрока сам предмет линейки. У оружия и брони тир 1 - «предмета нет»
    («Кулаки», «Не выковано», «Без брони»): предмет появляется со 2-го тира, поэтому
    зачаровать его раньше нельзя и зачарования на нём не действуют. Остальные
    инструменты (кирка) - настоящий инструмент уже с тира 1."""
    tool = TOOLS.get(tool_id, {})
    if not (tool.get("weapon") or tool.get("armor")) or len(tool.get("tiers", [])) <= 1:
        return True
    return tool_tier(state, tool_id) >= 2


def enchantable_tools() -> list[str]:
    """Инструменты, у которых вообще есть enchant_kind (т.е. их можно понести на
    стол зачарований) - используется UI для списка выбора. Чистая функция от
    контента - не завязана на конкретное состояние игрока (ЛЮБОЙ игрок видит
    один и тот же список зачаровываемых линеек, владеет он ими пока или нет)."""
    return [tool_id for tool_id in TOOLS if TOOLS[tool_id].get("enchant_kind") is not None]


def _enchanting_table_config(state: GameState) -> dict | None:
    """Конфиг (buildings.*.enchanting_table) первой РАБОТАЮЩЕЙ постройки-стола
    зачарований - generic-флаг, НЕ хардкод id (тот же принцип, что genetics_lab/
    breeding_station). None, если ни одна копия ни одного такого стола не
    работает - тогда enchant_tool недоступен вовсе."""
    for building_id, building in BUILDINGS.items():
        config = building.get("enchanting_table")
        if config and working_count(state, building_id) > 0:
            return config
    return None


def enchanting_table_available(state: GameState) -> bool:
    return _enchanting_table_config(state) is not None


def enchanting_table_cost(state: GameState) -> dict[str, int]:
    """Цена ОДНОЙ попытки зачарования прямо сейчас - пустой словарь, если стол
    зачарований вовсе недоступен (см. enchanting_table_available)."""
    config = _enchanting_table_config(state)
    return dict(config["cost"]) if config else {}


def enchanting_table_strip_on_fail(state: GameState) -> bool:
    """buildings.*.enchanting_table.strip_on_fail - настройка КОНТЕНТА (правит
    только автор через редактор, см. content_editor.html: openEnchantingTableSettingsModal),
    НЕ игрок - в отличие от большинства toggle_*, здесь нет соответствующего
    API-эндпоинта в web_ui.py. True (дефолт, отсутствие ключа) - старое
    поведение: провал попытки СНИМАЕТ с инструмента все зачарования разом.
    False смягчает это до "провал просто ничего не даёт". True и когда стола
    вовсе нет (enchant_tool такому вызову всё равно откажет раньше)."""
    config = _enchanting_table_config(state)
    return bool(config.get("strip_on_fail", True)) if config else True


def _roll_enchant_circles(pool: list[dict]) -> list[dict]:
    """Три круга - три НЕЗАВИСИМЫХ равновероятных броска по всему барабану
    (enchantment_pool), без корреляции между ними (в отличие от демо-мокапа,
    который специально подыгрывал совпадениям ради наглядности - здесь честная
    механика). Совпадение всех трёх (один и тот же ТИП зачарования, без учёта
    уровня - см. enchant_tool) - удача, которую игрок видит РЕДКО, если барабан
    большой, но не исчезающе редко: типов в барабане меньше, чем было бы
    (тип x уровень) комбинаций, поэтому шанс совпадения заметно выше."""
    return [random.choice(pool) for _ in range(3)]


def enchant_tool(state: GameState, tool_id: str) -> ActionResult:
    """Одна попытка зачарования - списывает цену ЦЕЛИКОМ (buildings.*.
    enchanting_table.cost, обычно книга зачарований) и СРАЗУ решает исход (тот
    же принцип, что claim_quest_reward/_roll_loot_case_prize - сервер бросает
    кости один раз, ActionResult.enchant несёт уже готовый результат для
    анимации, а не семя для клиента). Успех (все 3 круга показали ОДИНАКОВЫЙ
    enchant_id - барабан хранит только ТИПЫ, см. enchantment_pool, конкретный
    уровень в розыгрыше не участвует) поднимает уровень ЭТОГО зачарования на
    инструменте РОВНО НА ОДИН (не был - становится 1, был 1 - становится 2 и
    т.д. - см. tool_enchant_level/_enchant_effect_value), не выше максимального
    уровня из content.json enchantments.*.levels; успех на уже максимальном
    уровне просто ничего не меняет (потолок, не откат). Провал при
    buildings.*.enchanting_table.strip_on_fail (дефолт, старое поведение -
    настройка КОНТЕНТА, правит автор через редактор, не игрок, см.
    enchanting_table_strip_on_fail) СНИМАЕТ с инструмента ВСЕ его зачарования
    разом; с выключенным флагом провал просто ничего не даёт - зачарования
    остаются как были. Не тратит ход - это манипуляция с предметом, как
    insert_soul/repair_tool."""
    if tool_id not in TOOLS:
        return ActionResult(False, "Неизвестный инструмент.")
    tool_name = TOOLS[tool_id]["name"]
    config = _enchanting_table_config(state)
    if config is None:
        return ActionResult(False, "Нет работающего стола зачарований.")
    pool = enchantment_pool(tool_id)
    if not pool:
        return ActionResult(False, f"«{tool_name}» нельзя зачаровать.")
    if not combat_gear_owned(state, tool_id):
        return ActionResult(False, f"«{tool_name}»: сначала выкуй - пока зачаровывать нечего.")
    cost = config.get("cost", {})
    missing = _direct_shortfall(state, cost)
    if missing:
        return ActionResult(False, f"Не хватает ресурсов: {_format_shortfall(missing)}.")
    for resource_id, amount in cost.items():
        state.resources[resource_id] -= amount

    circles = _roll_enchant_circles(pool)
    first = circles[0]
    success = all(c["enchant_id"] == first["enchant_id"] for c in circles)
    circles_out = [{"enchant_id": c["enchant_id"]} for c in circles]

    if success:
        enchant_id = first["enchant_id"]
        enchant_name = ENCHANTMENTS[enchant_id]["name"]
        max_level = len(ENCHANTMENTS[enchant_id].get("levels", []))
        applied = state.tool_enchantments.setdefault(tool_id, {})
        current_level = applied.get(enchant_id, 0)
        new_level = min(current_level + 1, max_level) if max_level else current_level
        applied[enchant_id] = new_level
        if new_level > current_level:
            message = f"«{tool_name}» зачарован: {enchant_name} {new_level}."
        else:
            message = f"«{tool_name}»: {enchant_name} уже на максимальном уровне."
        result = {"tool_id": tool_id, "circles": circles_out, "success": True,
                  "applied": {"enchant_id": enchant_id, "level": new_level}}
    else:
        had_any = bool(state.tool_enchantments.get(tool_id))
        if config.get("strip_on_fail", True):
            state.tool_enchantments.pop(tool_id, None)
            message = (
                f"Неудача - с «{tool_name}» сняты все зачарования." if had_any
                else f"Неудача зачарования «{tool_name}»."
            )
        else:
            message = f"Неудача зачарования «{tool_name}» - зачарования сохранены."
        result = {"tool_id": tool_id, "circles": circles_out, "success": False, "applied": None}
    return ActionResult(True, message, enchant=result)


def skill_level(state: GameState, skill_id: str) -> int:
    """Текущий уровень линейки навыка - позиция по накопленному XP в
    content.json["skills"][skill_id]["levels"] (тот же принцип "тир = позиция",
    что у tool_tier, только выводится из XP, а не хранится напрямую): levels[0] -
    уровень 1 (стартовый, xp_required не проверяется), levels[i] - уровень i+1,
    достигается при xp >= levels[i]["xp_required"]. 1, если линейки нет в
    контенте вовсе или у неё нет уровней."""
    levels = SKILLS.get(skill_id, {}).get("levels", [])
    xp = state.skill_xp.get(skill_id, 0)
    level = 1
    for i, lvl in enumerate(levels):
        if i == 0:
            continue
        if xp >= lvl["xp_required"]:
            level = i + 1
    return level


def skill_level_effects(state: GameState, skill_id: str) -> dict:
    """Свободный словарь "effects" ТЕКУЩЕГО уровня линейки - как и у улучшений
    построек, каждый уровень задаёт АБСОЛЮТНЫЙ эффект (не добавку к предыдущему).
    Пустой словарь, если у линейки нет уровней вовсе - вызывающая механика сама
    решает разумное значение по умолчанию для отсутствующего ключа (см.
    attack_encounter: effects.get("damage", ...))."""
    levels = SKILLS.get(skill_id, {}).get("levels", [])
    if not levels:
        return {}
    return levels[skill_level(state, skill_id) - 1].get("effects", {})


def next_skill_level_info(state: GameState, skill_id: str) -> dict | None:
    levels = SKILLS.get(skill_id, {}).get("levels", [])
    level = skill_level(state, skill_id)
    if level >= len(levels):
        return None
    return levels[level]


def add_skill_xp(state: GameState, skill_id: str, amount: int) -> None:
    """Единая точка начисления опыта - используется любой механикой, которая
    решила прокачивать игрока через какую-то свою линейку (сейчас - только бой,
    см. COMBAT_SKILL_ID/attack_encounter)."""
    if amount <= 0:
        return
    state.skill_xp[skill_id] = state.skill_xp.get(skill_id, 0) + amount


def skills_view(state: GameState) -> list[dict]:
    """Все линейки навыков разом, для отдельного меню "Навыки" - полностью
    generic (перечисляет ВСЁ, что есть в content.json["skills"]), новую линейку
    не нужно отдельно регистрировать тут."""
    result = []
    for skill_id, skill in SKILLS.items():
        level = skill_level(state, skill_id)
        levels = skill.get("levels", [])
        next_info = next_skill_level_info(state, skill_id)
        result.append({
            "id": skill_id,
            "name": skill["name"],
            "description": skill.get("description", ""),
            "level": level,
            "max_level": len(levels),
            "xp": state.skill_xp.get(skill_id, 0),
            "effects": levels[level - 1].get("effects", {}) if levels else {},
            "next_xp_required": next_info["xp_required"] if next_info else None,
        })
    return result


def _weapon_bonuses(state: GameState) -> dict:
    """Сводка боевых бонусов от ВСЕХ владеемых линеек оружия сразу - оружие это
    любой tools.*, помеченный weapon=true (generic-поле, как и у остальных
    инструментов ни на что конкретное не завязано). Тир каждой линейки читается
    как обычно (tool_tier), но применяется НЕ через upgrade_tool-прогрессию урона
    напрямую, а через опциональные боевые поля НА КОНКРЕТНОМ тире:
    damage_multiplier/instant_kill_max_hp/loot_multiplier/soul_chance_multiplier,
    плюс ТРИ зачарования этой ЖЕ линейки (см. _enchant_effect_value): "Острота"
    (damage_bonus, ДОЛЯ добавки - domножает damage_multiplier ИМЕННО этого
    оружия, ПРЕЖДЕ чем идти в максимум по линейкам), "Добыча" (drop_chance_multiplier -
    домножает И soul_chance_multiplier, И новое поле loot_chance_multiplier ИМЕННО
    этого оружия) и "Некромантия" (soul_chance_multiplier - ДОПОЛНИТЕЛЬНО к
    "Добыче" домножает ТОЛЬКО шанс души, не трогая loot_chance_multiplier - оба
    зачарования на одном мече стакаются перемножением). Сломанная линейка (tool_is_broken)
    вообще пропускается - её бонусы (в т.ч. от зачарований) не участвуют, пока
    её не починят, но сам удар при этом не блокируется (в отличие от кирки) -
    останется хотя бы "голыми руками". Среди ОСТАЛЬНЫХ владеемых линеек оружия
    действует максимум по каждому полю независимо (тот же принцип "сильнейшее
    побеждает", что у building upgrades) - так уникальный меч-ваншот (свой
    tool_id с ОДНИМ тиром) и обычный прогрессивный меч работают одновременно,
    не мешая друг другу: у одного из них может быть выше damage_multiplier, у
    другого - instant_kill_max_hp."""
    damage_multiplier = 1.0
    instant_kill_max_hp = 0
    loot_multiplier = 1.0
    loot_chance_multiplier = 1.0
    soul_chance_multiplier = 1.0
    for tool_id, tool in TOOLS.items():
        if not tool.get("weapon") or tool_is_broken(state, tool_id):
            continue
        tiers = tool.get("tiers", [])
        index = tool_tier(state, tool_id) - 1
        if not (0 <= index < len(tiers)):
            continue
        tier = tiers[index]
        # Зачарования невыкованного оружия (тир 1) не действуют - combat_gear_owned.
        owned = combat_gear_owned(state, tool_id)
        sharpness = _enchant_effect_value(state, tool_id, "damage_bonus", 0.0) if owned else 0.0
        looting = _enchant_effect_value(state, tool_id, "drop_chance_multiplier", 1.0) if owned else 1.0
        necromancy = _enchant_effect_value(state, tool_id, "soul_chance_multiplier", 1.0) if owned else 1.0
        damage_multiplier = max(damage_multiplier, tier.get("damage_multiplier", 1.0) * (1 + sharpness))
        instant_kill_max_hp = max(instant_kill_max_hp, tier.get("instant_kill_max_hp", 0))
        loot_multiplier = max(loot_multiplier, tier.get("loot_multiplier", 1.0))
        loot_chance_multiplier = max(loot_chance_multiplier, looting)
        soul_chance_multiplier = max(
            soul_chance_multiplier, tier.get("soul_chance_multiplier", 1.0) * looting * necromancy
        )
    return {
        "damage_multiplier": damage_multiplier,
        "instant_kill_max_hp": instant_kill_max_hp,
        "loot_multiplier": loot_multiplier,
        "loot_chance_multiplier": loot_chance_multiplier,
        "soul_chance_multiplier": soul_chance_multiplier,
    }


def _armor_bonuses(state: GameState) -> dict:
    """Зеркало _weapon_bonuses для защиты - броня это любой tools.*, помеченный
    armor=true, с полями max_hp_bonus (добавка к максимуму HP игрока) и
    damage_reduction (плоское вычитание из входящего урона моба, см.
    attack_encounter), плюс ДВА зачарования этой ЖЕ линейки: "Защита"
    (armor_defense_bonus - добавка к damage_reduction ИМЕННО этой брони) и
    "Живучесть" (max_hp_bonus_add - добавка к max_hp_bonus ИМЕННО этой брони).
    Сломанная линейка пропускается целиком (та же логика, что у оружия). Среди
    ОСТАЛЬНЫХ владеемых линеек брони действует максимум по каждому полю
    независимо - тот же принцип "сильнейшее побеждает", что у оружия."""
    max_hp_bonus = 0
    damage_reduction = 0
    for tool_id, tool in TOOLS.items():
        if not tool.get("armor") or tool_is_broken(state, tool_id):
            continue
        tiers = tool.get("tiers", [])
        index = tool_tier(state, tool_id) - 1
        if not (0 <= index < len(tiers)):
            continue
        tier = tiers[index]
        owned = combat_gear_owned(state, tool_id)
        protection = _enchant_effect_value(state, tool_id, "armor_defense_bonus", 0.0) if owned else 0.0
        vitality = _enchant_effect_value(state, tool_id, "max_hp_bonus_add", 0.0) if owned else 0.0
        max_hp_bonus = max(max_hp_bonus, tier.get("max_hp_bonus", 0) + vitality)
        damage_reduction = max(damage_reduction, tier.get("damage_reduction", 0) + protection)
    return {"max_hp_bonus": max_hp_bonus, "damage_reduction": damage_reduction}


def armor_damage_reduction(state: GameState) -> float:
    """Итоговое плоское снижение ответного удара всей бронёй (с зачарованиями) -
    публичная обёртка над _armor_bonuses для UI (чип «броня −N» в схватке)."""
    return _armor_bonuses(state)["damage_reduction"]


def _consume_weapon_durability(state: GameState) -> None:
    """Прочность ВСЕХ владеемых линеек оружия (weapon=true) тратится за КАЖДЫЙ
    удар (attack_encounter), не только за победный - зеркало прочности кирки,
    но без понятия "площади": оружие тут ровно одно "попадание" за раз, так что
    масштабировать нечего. Уже сломанная линия просто остаётся на 0 (см.
    _consume_tool_durability) - лишний вызов на неё безвреден."""
    for tool_id, tool in TOOLS.items():
        if tool.get("weapon"):
            _consume_tool_durability(state, tool_id)


def _consume_armor_durability(state: GameState) -> None:
    """Зеркало _consume_weapon_durability для брони - тратится, когда броня
    реально "поучаствовала" (игрок получил ответный удар моба, см.
    attack_encounter), а не на каждый удар игрока по мобу."""
    for tool_id, tool in TOOLS.items():
        if tool.get("armor"):
            _consume_tool_durability(state, tool_id)


def player_max_hp(state: GameState) -> int:
    """Максимум HP игрока = база (content.json["combat"].player_base_hp) +
    бонус боевого навыка (skills.combat.levels[].effects.max_hp - в отличие
    от effects["damage"], который ЗАМЕНЯЕТ базовый урон на текущем уровне,
    max_hp АДДИТИВЕН к базе игрока) + лучшая владеемая броня (_armor_bonuses)."""
    base = COMBAT_SETTINGS.get("player_base_hp", 20)
    skill_bonus = skill_level_effects(state, COMBAT_SKILL_ID).get("max_hp", 0)
    return base + skill_bonus + _armor_bonuses(state)["max_hp_bonus"]


def current_player_hp(state: GameState) -> int:
    """Текущее HP игрока - НИКОГДА не читай state.player_hp напрямую (тот же
    принцип, что tool_tier у state.tool_tiers): None разворачивается в максимум
    (свежая игра/старый сейв без этого поля), значение клампится в
    [0, player_max_hp(state)] на случай, если максимум мог измениться."""
    hp = state.player_hp
    if hp is None:
        hp = player_max_hp(state)
    return max(0, min(hp, player_max_hp(state)))


def is_wounded(state: GameState) -> bool:
    """Действует ли сейчас дебафф "Ранен" (см. attack_encounter - выставляется
    при поражении в схватке, снимает урон игрока, блокирует новую схватку при
    0 HP). Тот же принцип сравнения, что deadline_turn у боссов."""
    return state.wounded_until_turn is not None and state.turn <= state.wounded_until_turn


def unlocked_attack_moves(state: GameState) -> set[str]:
    """Приёмы боя (content.json["combat_moves"]), доступные игроку ПРЯМО СЕЙЧАС -
    в отличие от _weapon_bonuses (где считается только числовое поле САМОГО
    текущего тира, без накопления), разблокировка приёма КУМУЛЯТИВНА по тирам
    одной линейки: дойдя до тира N, игрок сохраняет все unlocks_moves тиров
    1..N этой линейки (естественная семантика "выучил и не разучился"), плюс,
    как и остальные боевые бонусы, объединение по ВСЕМ владеемым линейкам
    оружия сразу."""
    moves: set[str] = set()
    for tool_id, tool in TOOLS.items():
        if not tool.get("weapon"):
            continue
        tiers = tool.get("tiers", [])
        current = tool_tier(state, tool_id)
        for tier in tiers[:current]:
            moves.update(tier.get("unlocks_moves", []))
    return moves


def combat_moves_view(state: GameState) -> list[dict]:
    """Приёмы боя с текущим статусом относительно игрока - разблокирован ли
    (unlocked_attack_moves) и сколько ходов осталось до восстановления
    (state.move_cooldowns) - для кнопок приёмов в панели схватки. Поля режима
    (mode/damage_multiplier/percent/execute_threshold_percent) отдаются как есть
    (могут отсутствовать в зависимости от mode, см. _move_damage) - фронтенд сам
    решает, как показать конкретную формулу в подсказке (см. tools/web_ui.html:
    makeAttackButton)."""
    unlocked = unlocked_attack_moves(state)
    return [
        {
            "id": mid,
            "name": move["name"],
            "description": move.get("description", ""),
            "mode": move.get("mode", "multiplier"),
            "damage_multiplier": move.get("damage_multiplier"),
            "percent": move.get("percent"),
            "execute_threshold_percent": move.get("execute_threshold_percent"),
            "cooldown_turns": move.get("cooldown_turns", 0),
            "unlocked": mid in unlocked,
            "cooldown_remaining": state.move_cooldowns.get(mid, 0),
        }
        for mid, move in COMBAT_MOVES.items()
    ]


def is_mob_unlocked(mob_id: str, state: GameState) -> bool:
    mob = MOBS[mob_id]
    req_tech = mob.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return False
    req_building = mob.get("requires_building")
    if req_building and working_count(state, req_building) <= 0:
        return False
    return True


def mobs_at(location_id: str, state: GameState) -> list[str]:
    """Мобы И боссы конкретной локации-"Охоты" (mobs.*.location), открытые прямо
    сейчас - как manual_actions_at, но своё, отдельное от них пространство id."""
    return [mid for mid in MOBS if MOBS[mid].get("location") == location_id and is_mob_unlocked(mid, state)]


def huntable_mobs_at(location_id: str, state: GameState) -> list[str]:
    return [mid for mid in mobs_at(location_id, state) if not MOBS[mid].get("is_boss")]


def summonable_bosses_at(location_id: str, state: GameState) -> list[str]:
    return [mid for mid in mobs_at(location_id, state) if MOBS[mid].get("is_boss")]


def can_search_for_mob(location_id: str, state: GameState) -> bool:
    return bool(huntable_mobs_at(location_id, state))


def search_mob_any(state: GameState, location_id: str) -> ActionResult:
    """Единая кнопка поиска цели в локации-"Охоте" - рандом по rarity (чем
    МЕНЬШЕ - тем чаще, тот же принцип, что вес кластеров руды на поле шахты,
    см. _generate_mine_field), активна ВСЕГДА только ОДНА цель на
    ВСЮ игру (state.active_encounter, не по локациям) - новый поиск заменяет
    предыдущую находку целиком, даже недобитую, и даже если это был призванный
    босс (уже потраченный на призыв предмет не возвращается - см. summon_boss).
    При 0 HP (см. current_player_hp) отказывает - см. attack_encounter/
    is_wounded про то, откуда берётся 0 HP и как из него выйти."""
    if current_player_hp(state) <= 0:
        return ActionResult(False, "Нужно сперва восстановить здоровье.")
    candidates = huntable_mobs_at(location_id, state)
    if not candidates:
        return ActionResult(False, "Тут пока некого искать.")
    weights = [1 / MOBS[mid]["rarity"] for mid in candidates]
    mob_id = random.choices(candidates, weights=weights, k=1)[0]
    hp = MOBS[mob_id]["hp"]
    state.active_encounter = {"mob_id": mob_id, "remaining_hp": hp}
    return ActionResult(True, f"Вы наткнулись на: {MOBS[mob_id]['name']} (HP: {hp}).")


def summon_boss(state: GameState, boss_id: str) -> ActionResult:
    """Призыв босса - тратит summon_cost (обычная цена ресурсами, через
    _pay_or_defer - как и любая другая цена, может докрафтиться автокрафтом/уйти
    в очередь). Заменяет активную схватку целиком, как и search_mob_any.
    mobs.*.turn_limit (необязателен, есть только у боссов - см.
    content_validation.py) даёт схватке дедлайн: если общий счётчик хода
    (state.turn) перевалит за него раньше, чем босс убит, схватка обрывается
    сама (см. _process_combat в advance_turn) - "не добил вовремя, босс сбежал".
    При 0 HP отказывает, как search_mob_any - тем же сообщением, ДО списания
    summon_cost (чтобы не платить за призыв, который тут же откажет)."""
    mob = MOBS.get(boss_id)
    if mob is None or not mob.get("is_boss"):
        return ActionResult(False, "Такого босса не существует.")
    if not is_mob_unlocked(boss_id, state):
        return ActionResult(False, "Этот босс ещё не открыт.")
    if current_player_hp(state) <= 0:
        return ActionResult(False, "Нужно сперва восстановить здоровье.")
    deferred = _pay_or_defer(state, mob.get("summon_cost", {}), f"призыв «{mob['name']}»", "summon_boss", boss_id)
    if deferred is not None:
        return deferred
    hp = mob["hp"]
    state.active_encounter = {"mob_id": boss_id, "remaining_hp": hp}
    turn_limit = mob.get("turn_limit")
    if turn_limit:
        state.active_encounter["deadline_turn"] = state.turn + turn_limit
    return ActionResult(True, f"Призван: {mob['name']} (HP: {hp}).")


def abandon_encounter(state: GameState) -> ActionResult:
    """Добровольно бросить текущую схватку без нового поиска/призыва - бесплатно,
    не тратит ход (управление, как toggle_pin), но и не возвращает уже
    потраченный на призыв предмет - прогресс/цена всё равно теряются, как при
    замене на новую цель."""
    if state.active_encounter is None:
        return ActionResult(False, "Сейчас никого нет.")
    state.active_encounter = None
    return ActionResult(True, "Схватка прекращена.")


def qte_damage_bonus() -> float:
    """Добавка к урону за попадание в QTE-кольцо (content.json["combat"].qte_damage_bonus,
    доля: 0.2 = +20%). 0, если не задана - тогда QTE чистая косметика."""
    return COMBAT_SETTINGS.get("qte_damage_bonus", 0.0)


def _ceil_damage(value: float) -> int:
    """Урон вверх до целого, но без артефактов плавающей точки: 10 * 1.1 =
    11.000000000000002 должно дать 11, а не 12."""
    return max(1, math.ceil(round(value, 6)))


def _move_damage(state: GameState, mob: dict, remaining_hp: int, move: dict | None, qte_hit: bool = False) -> tuple[int, bool]:
    """Считает урон одного удара - move=None это обычная атака (mode "multiplier"
    с коэффициентом 1.0, т.е. чистый расчёт по навыку/оружию). Каждый mode - свой,
    полностью независимый способ посчитать число, при этом ЛЮБОЙ из них всё ещё
    может быть перебит instant_kill_max_hp оружия (это проверяется отдельно в
    attack_encounter, не здесь):
      - "multiplier" (по умолчанию, если move не задан) - обычный расчёт (навык *
        лучший damage_multiplier оружия), домноженный на move["damage_multiplier"].
      - "percent_max_hp" - move["percent"] от МАКСИМАЛЬНОГО HP цели (mob["hp"]) -
        не зависит ни от навыка, ни от оружия: одинаково страшен что мелкой добыче,
        что огромному боссу, в постоянных числах.
      - "percent_remaining_hp" - move["percent"] от ОСТАВШЕГОСЯ прямо сейчас HP -
        подряд применённый несколько раз даёт убывающий урон (снимает долю от уже
        уменьшившегося остатка, не от исходного максимума).
      - "execute" - если remaining_hp уже не выше move["execute_threshold_percent"]
        от максимума, добивает цель ЦЕЛИКОМ (второй элемент кортежа - forced_kill=True,
        трактуется вызывающим как мгновенное убийство, наравне с оружейным
        instant_kill_max_hp); иначе - обычный расчёт по навыку/оружию, домноженный на
        необязательный move["damage_multiplier"] (по умолчанию 1.0, т.е. как без приёма).
    Возвращает (damage, forced_kill) - damage всегда >= 1 (кроме forced_kill, где damage
    - весь remaining_hp, чтобы вызов "снял remaining - damage" тоже давал 0 без спецкейса).
    Дебафф "Ранен" (is_wounded) домножает ИТОГОВЫЙ урон игрока НА ЛЮБОЙ mode (в т.ч.
    forced_kill - добивание раненым игроком всё ещё добивание, просто урон обычной
    ветки перед этим уже снижен) - единственная точка, куда встроен этот множитель,
    как и остальные бонусы урона. Туда же - бонус за попадание в QTE-кольцо (qte_hit,
    qte_damage_bonus): тоже на ЛЮБОЙ mode, кроме добивания ниже порога (цель и так умирает).
    Попадание решает клиент (одиночная игра, тот же уровень доверия, что у click_delay_ms)."""
    bonuses = _weapon_bonuses(state)
    base_damage = skill_level_effects(state, COMBAT_SKILL_ID).get("damage", 1)
    scale = COMBAT_SETTINGS.get("wounded_damage_multiplier", 1.0) if is_wounded(state) else 1.0
    if qte_hit:
        scale *= 1 + qte_damage_bonus()
    mode = move.get("mode", "multiplier") if move else "multiplier"
    if mode == "percent_max_hp":
        return _ceil_damage(move["percent"] * mob["hp"] * scale), False
    if mode == "percent_remaining_hp":
        return _ceil_damage(move["percent"] * remaining_hp * scale), False
    if mode == "execute" and remaining_hp <= move["execute_threshold_percent"] * mob["hp"]:
        return remaining_hp, True
    move_multiplier = 1.0
    if move is not None:
        move_multiplier = move.get("damage_multiplier", 1.0) if mode == "execute" else move["damage_multiplier"]
    return _ceil_damage(base_damage * bonuses["damage_multiplier"] * move_multiplier * scale), False


def attack_encounter(state: GameState, move_id: str | None = None, qte_hit: bool = False) -> ActionResult:
    """Атака активной цели (state.active_encounter) - урон снимает HP, лут/опыт/
    душа выдаются ОДНИМ пакетом при финальном убийстве, не по частям за удар (см.
    CLAUDE.md). Расчёт урона одного удара - см. _move_damage (несколько независимых
    "режимов" приёма, не только домножение обычного урона). ЛЮБОЕ владеемое оружие с
    instant_kill_max_hp >= remaining_hp убивает цель мгновенно, независимо от
    расчётного урона ЛЮБОГО режима (в т.ч. добивающего приёма - тут они просто не
    нужны одновременно, но не мешают друг другу). Приём, которого ещё нет (не открыт
    тиром оружия) или который ещё не восстановился (state.move_cooldowns), отклоняется
    без применения (без траты хода, как любая другая непрошедшая валидация) - кулдаун
    приёма проставляется ТОЛЬКО при реально состоявшемся ударе, после чего тикает в
    advance_turn/_process_combat вместе с дедлайном босса, каждый ход, а не только в бою."""
    encounter = state.active_encounter
    if encounter is None:
        return ActionResult(False, "Нет активной цели - сначала найди моба или призови босса.")
    move = None
    if move_id is not None:
        move = COMBAT_MOVES.get(move_id)
        if move is None or move_id not in unlocked_attack_moves(state):
            return ActionResult(False, "Этот приём ещё не изучен.")
        cooldown_left = state.move_cooldowns.get(move_id, 0)
        if cooldown_left > 0:
            return ActionResult(False, f"«{move['name']}» ещё не восстановился: {cooldown_left} х.")
    mob = MOBS[encounter["mob_id"]]
    remaining = encounter["remaining_hp"]
    if move is not None:
        cooldown_turns = move.get("cooldown_turns", 0)
        if cooldown_turns > 0:
            state.move_cooldowns[move_id] = cooldown_turns
    outcome = _strike(state, mob, remaining, move, qte_hit)
    bonuses = outcome["bonuses"]
    damage, instant, killed = outcome["damage"], outcome["instant_kill"], outcome["killed"]
    base_info = {"mob_id": encounter["mob_id"], "move_id": move_id, "forced_kill": outcome["forced_kill"], "qte_hit": bool(qte_hit)}
    if not killed:
        encounter["remaining_hp"] = remaining - damage
        retaliation = outcome["retaliation"]
        player_hp_after = outcome["player_hp_after"]
        state.player_hp = player_hp_after
        combat_info = {
            **base_info,
            "damage": damage, "killed": False, "instant_kill": False,
            "remaining_hp": encounter["remaining_hp"], "max_hp": mob["hp"],
            "player_damage_taken": retaliation, "armor_absorbed": outcome["armor_absorbed"],
            "player_hp": player_hp_after, "player_max_hp": player_max_hp(state), "defeated": False,
        }
        if player_hp_after <= 0:
            state.active_encounter = None
            wounded_turns = COMBAT_SETTINGS.get("wounded_turns", 0)
            state.wounded_until_turn = state.turn + wounded_turns
            combat_info["defeated"] = True
            combat_info["wounded_turns"] = wounded_turns
            return ActionResult(
                True,
                f"{mob['name']} чуть не прикончил тебя - пришлось отступить ни с чем. "
                f"Ранен на {wounded_turns} х.",
                combat=combat_info,
            )
        return ActionResult(
            True,
            f"Урон: -{damage} HP. Осталось: {encounter['remaining_hp']}/{mob['hp']}."
            + (f" В ответ: -{retaliation} HP." if retaliation > 0 else ""),
            combat=combat_info,
        )

    loot = dict(mob.get("loot", {}))
    loot_chance = mob.get("loot_chance")
    if loot_chance:
        boosted_chance = {
            r: min(1.0, chance * bonuses["loot_chance_multiplier"]) for r, chance in loot_chance.items()
        }
        loot = _roll_chance_amounts(loot, boosted_chance)
    if bonuses["loot_multiplier"] != 1.0:
        loot = _round_amounts({r: v * bonuses["loot_multiplier"] for r, v in loot.items()})
    _gain(state.resources, loot)

    soul_id = mob.get("soul_id")
    got_soul = False
    if soul_id:
        chance = min(1.0, mob.get("soul_chance", 0) * bonuses["soul_chance_multiplier"])
        if random.random() < chance:
            _add_resource(state.resources, soul_id, 1)
            got_soul = True

    level_before = skill_level(state, COMBAT_SKILL_ID)
    xp_reward = mob.get("xp_reward", 0)
    add_skill_xp(state, COMBAT_SKILL_ID, xp_reward)
    state.active_encounter = None
    message = f"Убит: {mob['name']}."
    if got_soul:
        message += f" Выбита душа: {RESOURCE_NAMES.get(soul_id, soul_id)}!"
    return ActionResult(
        True, message,
        combat={
            **base_info,
            "damage": damage, "killed": True, "instant_kill": instant, "remaining_hp": 0,
            "max_hp": mob["hp"], "player_damage_taken": 0, "armor_absorbed": 0,
            "player_hp": current_player_hp(state), "player_max_hp": player_max_hp(state), "defeated": False,
            # Добыча одним пакетом - для экрана «Добыча» в UI (web_ui.html: huntShowResult),
            # чтобы не выводить её обратно из разницы складов.
            "loot": {r: a for r, a in loot.items() if a > 0},
            "soul": soul_id if got_soul else None,
            "xp": xp_reward,
            "skill_level_before": level_before,
            "skill_level_after": skill_level(state, COMBAT_SKILL_ID),
        },
    )


def _strike(state: GameState, mob: dict, remaining: int, move: dict | None, qte_hit: bool = False) -> dict:
    """Один удар по цели БЕЗ последствий для схватки - ЕДИНАЯ точка расчёта для
    attack_encounter и прогноза attack_preview (иначе прогноз на кнопке приёма и
    реальный удар могли бы тихо разойтись). Меняет в state ТОЛЬКО state.tool_durability
    (износ оружия за удар и брони за ответку) - именно в этом порядке, что и раньше:
    урон и бонусы оружия считаются ДО износа, ответка - ПОСЛЕ износа брони (сломавшаяся
    на этом ударе броня его уже не смягчает). Ответка - только если моб пережил удар
    (убивающий удар, в т.ч. instant_kill, её не получает). Броня
    (_armor_bonuses.damage_reduction) снижает входящий урон плоским вычитанием, до 0
    включительно - сильная броня может полностью аннулировать слабого моба, зеркало
    instant_kill_max_hp у оружия."""
    bonuses = _weapon_bonuses(state)
    damage, forced_kill = _move_damage(state, mob, remaining, move, qte_hit)
    instant = forced_kill or remaining <= bonuses["instant_kill_max_hp"]
    killed = instant or damage >= remaining
    _consume_weapon_durability(state)
    outcome = {
        "bonuses": bonuses, "damage": damage, "forced_kill": forced_kill, "instant_kill": instant,
        "killed": killed, "retaliation": 0, "armor_absorbed": 0, "player_hp_after": current_player_hp(state),
    }
    if not killed:
        _consume_armor_durability(state)
        raw = mob.get("damage", 0)
        retaliation = max(0, raw - _armor_bonuses(state)["damage_reduction"])
        outcome["retaliation"] = retaliation
        outcome["armor_absorbed"] = raw - retaliation
        outcome["player_hp_after"] = max(0, current_player_hp(state) - retaliation)
    return outcome


def attack_preview(state: GameState, move_id: str | None = None, qte_hit: bool = False) -> dict | None:
    """Точный прогноз удара по активной цели (для штриховки на полосах HP и числа на
    кнопке приёма, см. tools/web_ui.py: hunting_view) - тот же _strike, что у
    attack_encounter, на state с ВОЗВРАЩЁННОЙ после расчёта прочностью (урон детерминирован:
    в _move_damage нет RNG). None - нет схватки или приём не изучен; перезарядку НЕ
    проверяет (прогноз показывает, что приём сделает, когда восстановится). qte_hit -
    прогноз для удара с попаданием в QTE-кольцо (qte_damage_bonus)."""
    encounter = state.active_encounter
    if encounter is None:
        return None
    move = None
    if move_id is not None:
        move = COMBAT_MOVES.get(move_id)
        if move is None or move_id not in unlocked_attack_moves(state):
            return None
    mob = MOBS[encounter["mob_id"]]
    remaining = encounter["remaining_hp"]
    saved = dict(state.tool_durability)
    try:
        outcome = _strike(state, mob, remaining, move, qte_hit)
    finally:
        state.tool_durability.clear()
        state.tool_durability.update(saved)
    killed = outcome["killed"]
    return {
        "damage": remaining if killed else outcome["damage"],
        "hp_after": 0 if killed else remaining - outcome["damage"],
        "killed": killed, "instant_kill": outcome["instant_kill"], "forced_kill": outcome["forced_kill"],
        "retaliation": outcome["retaliation"], "player_hp_after": outcome["player_hp_after"],
        "defeated": not killed and outcome["player_hp_after"] <= 0,
    }


def soul_collector_capacity(state: GameState, building_id: str) -> int:
    """Суммарно душ можно держать во ВСЕХ экземплярах синтезатора building_id -
    сумма soul_collector_instance_capacity по каждому физическому экземпляру
    ЭТОГО типа (см. state.soul_collector_instances/_ensure_soul_collector_instances).
    До перехода на per-instance слоты (2026-09-18) это было "per-copy slots *
    число работающих копий + АБСОЛЮТНАЯ добавка сильнейшего апгрейда ТИПА
    постройки" - теперь апгрейды свои у КАЖДОГО экземпляра (зеркало
    farm_instance_slot_capacity), так что сумма даёт то же самое агрегатное
    число, просто честно собранное по экземплярам. Используется только как
    сводная цифра (например, для внешних сумм) - сама вставка/выемка душ
    работает через soul_collector_instance_capacity(state, instance_id)."""
    _ensure_soul_collector_instances(state)
    return sum(
        soul_collector_instance_capacity(state, iid)
        for iid, inst in state.soul_collector_instances.items()
        if inst["building_id"] == building_id
    )


def soul_kind(resource_id: str) -> str | None:
    """Тег вида души (resources.*.soul_kind, например "моб"/"босс") - тот же
    generic-принцип тега, что species_family: группирует души независимо от
    их числа, а не перечисляет id по одному - см. soul_collector_accepts_kind."""
    return RESOURCE_SOUL_KIND.get(resource_id)


def soul_collector_accepts_kind(building_id: str) -> str | None:
    """Тег, который принимает синтезатор building_id (buildings.*.soul_collector.
    accepts_kind) - зеркало breeding_accepts_family: синтезатор принимает ЛЮБУЮ
    душу с совпадающим soul_kind, а не перечисленный вручную список id."""
    return BUILDINGS.get(building_id, {}).get("soul_collector", {}).get("accepts_kind")


def souls_bank_view(state: GameState) -> list[dict]:
    """Все ресурсы-души (ключи SOUL_RECIPES) с их тегом kind и наличием в
    инвентаре - источник банка душ для UI-модалки конкретного синтезатора
    (фронтенд сам фильтрует по soul_collector_accepts_kind нужной постройки,
    тот же приём, что creature_bank + breeding_accepts_family)."""
    return [
        {"id": rid, "kind": soul_kind(rid), "amount": state.resources.get(rid, 0)}
        for rid in SOUL_RECIPES
    ]


def insert_soul(state: GameState, instance_id: str, soul_id: str) -> ActionResult:
    """instance_id - id ФИЗИЧЕСКОГО экземпляра синтезатора (state.soul_collector_instances,
    как farm_instance_id у house_creature), НЕ id типа постройки - см.
    CLAUDE.md/state.py:soul_collector_instances."""
    _ensure_soul_collector_instances(state)
    instance = state.soul_collector_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого синтезатора нет.")
    config = BUILDINGS.get(instance["building_id"], {}).get("soul_collector")
    if not config:
        return ActionResult(False, "Это не синтезатор душ.")
    name = RESOURCE_NAMES.get(soul_id, soul_id)
    accepts_kind = config.get("accepts_kind")
    if accepts_kind is None or soul_kind(soul_id) != accepts_kind:
        return ActionResult(False, f"«{name}» не подходит для этого синтезатора.")
    if len(instance["installed"]) >= soul_collector_instance_capacity(state, instance_id):
        return ActionResult(False, "Нет свободных слотов.")
    if state.resources.get(soul_id, 0) < 1:
        return ActionResult(False, f"Нет в наличии: «{name}».")
    state.resources[soul_id] -= 1
    instance["installed"].append(soul_id)
    return ActionResult(True, f"«{name}» помещена в синтезатор.")


def remove_soul(state: GameState, instance_id: str, index: int) -> ActionResult:
    """Возвращает вставленную душу обратно в инвентарь - не тратит ход, как и
    cancel_energy_job/house_creature. index - позиция в
    state.soul_collector_instances[instance_id]["installed"] (список, не
    фиксированные слоты - см. state.py)."""
    instance = state.soul_collector_instances.get(instance_id)
    installed = instance["installed"] if instance is not None else []
    if not isinstance(index, int) or not (0 <= index < len(installed)):
        return ActionResult(False, "Такой души тут нет.")
    soul_id = installed.pop(index)
    _add_resource(state.resources, soul_id, 1)
    return ActionResult(True, f"«{RESOURCE_NAMES.get(soul_id, soul_id)}» возвращена в инвентарь.")


def reactor_component_role(resource_id: str) -> str | None:
    """Тег роли компонента реактора (resources.*.reactor_component.role,
    например 'fuel'/'moderator'/'coolant'/'absorber') - тот же generic-принцип
    тега, что soul_kind (см. reactor_accepts_role)."""
    return RESOURCE_REACTOR_COMPONENT.get(resource_id, {}).get("role")


def reactor_accepts_role(building_id: str, role: str) -> bool:
    """Принимает ли РЕШЁТКА этой постройки данную роль (buildings.*.reactor.
    accepts_roles) - зеркало soul_collector_accepts_kind, только список тегов
    сразу, не один (реактору обычно нужны сразу все 4 роли)."""
    return role in BUILDINGS.get(building_id, {}).get("reactor", {}).get("accepts_roles", [])


def reactor_component_bank_view(state: GameState) -> list[dict]:
    """Все ресурсы-компоненты реактора (ключи RESOURCE_REACTOR_COMPONENT) с их
    ролью и наличием в инвентаре - источник банка для UI-модалки конкретного
    реактора (фронтенд фильтрует по accepts_roles нужной постройки, тот же
    приём, что souls_bank_view/creature_bank)."""
    return [
        {"id": rid, "role": tag.get("role"), "amount": state.resources.get(rid, 0)}
        for rid, tag in RESOURCE_REACTOR_COMPONENT.items()
    ]


def insert_reactor_component(state: GameState, instance_id: str, cell_index: int, resource_id: str) -> ActionResult:
    """Установка компонента в ячейку решётки - не тратит ход (тот же пул, что
    insert_soul/house_creature). cell_index - позиция в instance["grid"]
    (row-major, см. reactor_grid_dims/_ensure_reactor_grid) - В ОТЛИЧИЕ от
    installed душ (append-only список), решётка ФИКСИРОВАННОЙ формы: индекс
    обязан существовать И быть пустым, не переиспользуем append."""
    _ensure_reactor_instances(state)
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого реактора нет.")
    config = BUILDINGS.get(instance["building_id"], {}).get("reactor")
    if not config:
        return ActionResult(False, "Это не реактор.")
    name = RESOURCE_NAMES.get(resource_id, resource_id)
    role = reactor_component_role(resource_id)
    if role is None or not reactor_accepts_role(instance["building_id"], role):
        return ActionResult(False, f"«{name}» не подходит для этого реактора.")
    _ensure_reactor_grid(state, instance_id)
    grid = instance["grid"]
    if not (0 <= cell_index < len(grid)):
        return ActionResult(False, "Такой ячейки нет в решётке.")
    if grid[cell_index] is not None:
        return ActionResult(False, "Ячейка уже занята.")
    if state.resources.get(resource_id, 0) < 1:
        return ActionResult(False, f"Нет в наличии: «{name}».")
    state.resources[resource_id] -= 1
    component = RESOURCE_REACTOR_COMPONENT[resource_id]
    charge = component.get("charge_max") if role == "fuel" else None
    grid[cell_index] = {"resource_id": resource_id, "charge": charge}
    return ActionResult(True, f"«{name}» установлена в решётку.")


def remove_reactor_component(state: GameState, instance_id: str, cell_index: int) -> ActionResult:
    """Возвращает компонент из ячейки решётки обратно в инвентарь - не тратит
    ход, как remove_soul. Выгоревшее топливо (charge == 0) возвращает НЕ
    исходный ресурс, а его spent_result (resources.*.reactor_component.
    spent_result) - ячейка очищается в обоих случаях. Если в ячейке ещё стоял
    катализатор (cell["catalyst_id"]) - он тоже возвращается в инвентарь
    ЦЕЛИКОМ (не теряется вместе со снятым компонентом), см.
    remove_reactor_catalyst для съёма ОДНОГО катализатора без демонтажа
    самого компонента."""
    instance = state.reactor_instances.get(instance_id)
    grid = instance["grid"] if instance is not None else []
    if not (0 <= cell_index < len(grid)) or grid[cell_index] is None:
        return ActionResult(False, "Тут ничего не установлено.")
    cell = grid[cell_index]
    resource_id = cell["resource_id"]
    catalyst_id = cell.get("catalyst_id")
    if cell.get("charge") == 0:
        returned_id = RESOURCE_REACTOR_COMPONENT.get(resource_id, {}).get("spent_result", resource_id)
    else:
        returned_id = resource_id
    grid[cell_index] = None
    _add_resource(state.resources, returned_id, 1)
    message = f"«{RESOURCE_NAMES.get(returned_id, returned_id)}» возвращена в инвентарь."
    if catalyst_id is not None:
        _add_resource(state.resources, catalyst_id, 1)
        message += f" Катализатор «{RESOURCE_NAMES.get(catalyst_id, catalyst_id)}» снят вместе с ней."
    return ActionResult(True, message)


def reactor_catalyst_bank_view(state: GameState) -> list[dict]:
    """Все ресурсы-катализаторы (ключи RESOURCE_REACTOR_CATALYST) с их
    applies_to и наличием в инвентаре - зеркало reactor_component_bank_view,
    источник банка для UI-модалки выбора катализатора конкретной ячейки
    (фронтенд фильтрует по роли компонента, уже стоящего в этой ячейке)."""
    return [
        {"id": rid, "applies_to": tag.get("applies_to", []), "amount": state.resources.get(rid, 0)}
        for rid, tag in RESOURCE_REACTOR_CATALYST.items()
    ]


def insert_reactor_catalyst(state: GameState, instance_id: str, cell_index: int, resource_id: str) -> ActionResult:
    """Установка катализатора В УЖЕ занятую ячейку решётки - один слот на
    клетку (cell["catalyst_id"]), не тратит ход (тот же пул, что
    insert_reactor_component). Требует, чтобы роль УЖЕ стоящего в клетке
    компонента входила в resources.*.reactor_catalyst.applies_to катализатора
    - в пустую клетку катализатор не ставится (усиливать нечего)."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого реактора нет.")
    grid = instance.get("grid", [])
    if not (0 <= cell_index < len(grid)) or grid[cell_index] is None:
        return ActionResult(False, "Сюда нечего усиливать - клетка пуста.")
    cell = grid[cell_index]
    name = RESOURCE_NAMES.get(resource_id, resource_id)
    catalyst = RESOURCE_REACTOR_CATALYST.get(resource_id)
    if catalyst is None:
        return ActionResult(False, f"«{name}» не катализатор.")
    role = reactor_component_role(cell["resource_id"])
    if role not in catalyst.get("applies_to", []):
        return ActionResult(False, f"«{name}» не подходит для этой роли.")
    if cell.get("catalyst_id") is not None:
        return ActionResult(False, "В этой клетке уже есть катализатор.")
    if state.resources.get(resource_id, 0) < 1:
        return ActionResult(False, f"Нет в наличии: «{name}».")
    state.resources[resource_id] -= 1
    cell["catalyst_id"] = resource_id
    return ActionResult(True, f"«{name}» установлен.")


def remove_reactor_catalyst(state: GameState, instance_id: str, cell_index: int) -> ActionResult:
    """Снятие катализатора обратно в инвентарь БЕЗ демонтажа самого
    компонента - не тратит ход. Катализатор возвращается 1:1 (не выгорает и
    не расходуется сам по себе, в отличие от топлива)."""
    instance = state.reactor_instances.get(instance_id)
    grid = instance["grid"] if instance is not None else []
    if not (0 <= cell_index < len(grid)) or grid[cell_index] is None:
        return ActionResult(False, "Тут ничего не установлено.")
    cell = grid[cell_index]
    catalyst_id = cell.get("catalyst_id")
    if catalyst_id is None:
        return ActionResult(False, "Тут нет катализатора.")
    cell["catalyst_id"] = None
    _add_resource(state.resources, catalyst_id, 1)
    return ActionResult(True, f"«{RESOURCE_NAMES.get(catalyst_id, catalyst_id)}» возвращён в инвентарь.")


def reactor_coolant_fluid_bank_view(state: GameState) -> list[dict]:
    """Все охлаждающие жидкости контента с текущим запасом игрока - зеркало
    reactor_component_bank_view/reactor_catalyst_bank_view. Показываем и те,
    которых пока нет в наличии: игрок должен видеть, что вообще бывает."""
    out = []
    for resource_id, cfg in RESOURCE_REACTOR_COOLANT_FLUID.items():
        out.append({
            "id": resource_id,
            "cooling_mult": cfg.get("cooling_mult", 1.0),
            "units_per_power": cfg.get("units_per_power", 0.0),
            "amount": state.resources.get(resource_id, 0),
        })
    return out


def set_reactor_coolant_fluid(state: GameState, instance_id: str, resource_id: str | None) -> ActionResult:
    """Какую жидкость реактор заливает в свои теплоносители. ОДНА на экземпляр
    (не на зону - бак общий, см. _process_reactors), НЕ тратит ход, как и
    остальные настройки реактора. Сама жидкость не списывается в момент выбора -
    её тратят теплоносители каждый ход, пропорционально прокачке."""
    _ensure_reactor_instances(state)
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого реактора нет.")
    if resource_id is None:
        instance["coolant_fluid"] = None
        return ActionResult(True, "Контур осушён - теплоносители больше ничего не снимают.")
    if resource_id not in RESOURCE_REACTOR_COOLANT_FLUID:
        return ActionResult(False, "Это не охлаждающая жидкость.")
    instance["coolant_fluid"] = resource_id
    return ActionResult(True, f"В контур залито: {RESOURCE_NAMES.get(resource_id, resource_id)}.")


def set_reactor_zone_control(state: GameState, instance_id: str, zone_index: int, depth_pct: int) -> ActionResult:
    """Глубина стержней управления зоны (0-100%) - постоянная настройка игрока,
    НЕ тратит ход (тот же пул, что set_energy_priority/
    set_building_active_count). Демпфирует и heat, и output фьюел-ячеек ЭТОЙ
    зоны - см. _process_reactors."""
    _ensure_reactor_instances(state)
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого реактора нет.")
    _ensure_reactor_zones(state, instance_id)
    zones = instance["zones"]
    if not (0 <= zone_index < len(zones)):
        return ActionResult(False, "Такой зоны нет.")
    zones[zone_index]["control_rod_depth"] = max(0, min(100, int(depth_pct)))
    return ActionResult(True, "Глубина стержней управления обновлена.")


def set_reactor_zone_coolant(state: GameState, instance_id: str, zone_index: int, flow_pct: int) -> ActionResult:
    """Поток охлаждения зоны (0-100%) - зеркало set_reactor_zone_control.
    Резкая перестройка потока (|новое - старое| >= reactor.
    thermal_shock_flow_delta_pct) на уже разогретой зоне (heat/heat_cap выше
    soft_threshold_pct НА МОМЕНТ этого вызова, ДО применения нового потока) -
    "термоудар": зона немедленно (не дожидаясь advance_turn) получает
    единовременную добавку тепла = heat_cap * reactor.
    thermal_shock_heat_bonus_pct. Это НАМЕРЕННО мгновенный эффект самого
    действия игрока (как insert_reactor_component/set_reactor_zone_control -
    не тратит ход), а не что-то, что ждёт следующей обработки хода - дёрнул
    клапан на горячей зоне, тут же почувствовал последствия. Добавка не
    провоцирует бросок аварии тут же (тот считается только в _process_reactors
    в конце хода) - просто поднимает исходную точку heat, с которой начнётся
    очередной ход."""
    _ensure_reactor_instances(state)
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого реактора нет.")
    _ensure_reactor_zones(state, instance_id)
    zones = instance["zones"]
    if not (0 <= zone_index < len(zones)):
        return ActionResult(False, "Такой зоны нет.")
    zone = zones[zone_index]
    old_flow = zone["coolant_flow"]
    zone["prev_coolant_flow"] = old_flow
    new_flow = max(0, min(100, int(flow_pct)))
    zone["coolant_flow"] = new_flow

    config = BUILDINGS.get(instance["building_id"], {}).get("reactor", {})
    delta_threshold = config.get("thermal_shock_flow_delta_pct", 0)
    if delta_threshold and abs(new_flow - old_flow) >= delta_threshold:
        cap = _reactor_zone_heat_cap(state, instance_id, zone_index)
        soft_pct = min(config.get("soft_threshold_pct", 1.0), 1.0)
        if cap > 0 and zone["heat"] / cap > soft_pct:
            zone["heat"] += cap * config.get("thermal_shock_heat_bonus_pct", 0.0)
            return ActionResult(True, "Термоудар! Резкий скачок потока охлаждения на разогретой зоне подстегнул температуру.")
    return ActionResult(True, "Поток охлаждения обновлён.")


def ritual_pedestal_capacity(state: GameState, building_id: str) -> int:
    """Сколько ингредиентов можно разом разложить по внешнему кольцу ритуального
    круга building_id - per-copy база (buildings.*.ritual_circle.ring_pedestals)
    * число РАБОТАЮЩИХ копий, плюс АБСОЛЮТНАЯ добавка от сильнейшего купленного
    апгрейда типа ritual_pedestals (тот же принцип "сильнейшее побеждает", что у
    soul_collector_capacity/automation_slots_capacity - см. _farm_upgrade_multiplier).
    0, если постройка не алтарь вовсе, или если ВСЕ копии сломаны - тот же
    принцип, что у soul_collector_capacity: сломанный алтарь с уже купленным
    апгрейдом не должен оставаться "открытым" для больших рецептов. Центральный
    пьедестал (ritual_recipes.*.center_cost) сюда НЕ входит - он есть у алтаря
    всегда, вне прокачки колец."""
    config = BUILDINGS.get(building_id, {}).get("ritual_circle")
    if not config or working_count(state, building_id) <= 0:
        return 0
    base = config.get("ring_pedestals", 0) * working_count(state, building_id)
    bonus = _farm_upgrade_multiplier(state, building_id, "ritual_pedestals", default=0)
    return int(base + bonus)


def is_ritual_recipe_available(recipe_id: str, state: GameState) -> bool:
    """Рецепт открыт, только если технология исследована (если задана),
    указанная в requires_building постройка-алтарь работает хотя бы одной
    копией, и её текущая ёмкость кольца (ritual_pedestal_capacity) хватает на
    суммарное количество ингредиентов ring_cost - совпадение с рецептом
    проверяется ПО КОЛИЧЕСТВУ, без привязки к конкретному пьедesталу (как
    cost у любого другого рецепта в игре), поэтому расстановка по конкретным
    чашам - дело только клиента (web_ui.html), сервер её не хранит."""
    recipe = RITUAL_RECIPES[recipe_id]
    req_tech = recipe.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return False
    building_id = recipe["requires_building"]
    if working_count(state, building_id) <= 0:
        return False
    needed = sum(recipe.get("ring_cost", {}).values())
    return needed <= ritual_pedestal_capacity(state, building_id)


def available_ritual_recipes(state: GameState) -> list[str]:
    return [r for r in RITUAL_RECIPES if is_ritual_recipe_available(r, state)]


def _ritual_full_cost(recipe: dict) -> dict[str, int]:
    """ring_cost + center_cost (1 шт.) + extra_cost, слитые в один платёж -
    именно это списывается ОДНИМ разом при старте (см. start_ritual). НЕ
    включает extra_consumes_per_turn - тот тикает отдельно каждый ход,
    см. _process_ritual_jobs."""
    cost = dict(recipe.get("ring_cost", {}))
    center = recipe["center_cost"]
    cost[center] = cost.get(center, 0) + 1
    for r, amount in recipe.get("extra_cost", {}).items():
        cost[r] = cost.get(r, 0) + amount
    return cost


def is_ritual_recipe_deterministic(recipe_id: str) -> bool:
    """Та же граница, что is_manual_action_deterministic: автокрафт проводит ритуал,
    только если его результат ГАРАНТИРОВАН - без шанса < 1 и без диапазона в produces.
    extra_consumes_chance значения не имеет: сборщик пропускает расход за ход вместе
    с длительностью."""
    recipe = RITUAL_RECIPES[recipe_id]
    produces = recipe.get("produces") or {}
    if not produces or any(_is_range_amount(v) for v in produces.values()):
        return False
    return all(c >= 1.0 for c in (recipe.get("produces_chance") or {}).values())


def ritual_recipe_for_resource() -> dict[str, str]:
    """resource_id -> ритуал, которым автокрафт делает этот ресурс, если у ресурса нет
    ни обычного рецепта, ни крафта за энергию (см. _discover_autocraft_graph). Только
    детерминированные ритуалы; при нескольких - первый по порядку в content.json.
    Считается на каждый вызов (ритуалов единицы), а не один раз при импорте, как
    ENERGY_RECIPE_FOR_RESOURCE."""
    result: dict[str, str] = {}
    for recipe_id, recipe in RITUAL_RECIPES.items():
        if not is_ritual_recipe_deterministic(recipe_id):
            continue
        for resource_id in recipe["produces"]:
            result.setdefault(resource_id, recipe_id)
    return result


def _ritual_autocraft_failure(state: GameState, recipe_id: str, resource_id: str) -> AutocraftFailure | None:
    """Почему автокрафт не может провести ритуал прямо сейчас (или None): технология,
    работающий алтарь, пьедесталы кольца (те же гейты, что у клика, см.
    is_ritual_recipe_available) и сборщик ритуалов этого алтаря - другого способа
    автоматизировать ритуал для заказов нет."""
    recipe = RITUAL_RECIPES[recipe_id]
    building_id = recipe["requires_building"]
    if recipe.get("requires_tech") and recipe["requires_tech"] not in state.techs:
        return AutocraftFailure(resource_id, "tech_locked")
    if working_count(state, building_id) <= 0:
        return AutocraftFailure(resource_id, "building_missing", building_id)
    need = sum(recipe.get("ring_cost", {}).values())
    have = ritual_pedestal_capacity(state, building_id)
    if need > have:
        return AutocraftFailure(
            resource_id, "ritual_pedestals", building_id, extra={"ritual": recipe["name"], "need": need, "have": have},
        )
    if building_assembler_capacity(state, building_id) == 0:
        return AutocraftFailure(resource_id, "not_automated", extra=_ritual_hint(building_id))
    return None


def start_ritual(state: GameState, recipe_id: str) -> ActionResult:
    """Запускает ритуал: ring_cost + center_cost + extra_cost списываются ОДНИМ
    платежом (через _pay_or_defer - как do_manual_action/start_energy_recipe,
    автокрафт может докрафтить нехватающее сырьё в фоне, если скорости не
    хватает на один ход). duration_turns <= 1 - результат начисляется сразу
    (как мгновенное ручное действие); иначе заводится
    state.ritual_jobs[building_id] - один активный ритуал НА ПОСТРОЙКУ
    (агрегация по типу постройки, как automation_rules, а не по физическому
    экземпляру, как farm_instances/soul_collector_instances) - повторный запуск
    на ту же постройку, пока задача не завершилась, отклоняется без траты
    платежа."""
    if recipe_id not in RITUAL_RECIPES:
        return ActionResult(False, "Такого ритуала не существует.")
    recipe = RITUAL_RECIPES[recipe_id]
    if not is_ritual_recipe_available(recipe_id, state):
        return ActionResult(False, "Этот ритуал ещё не доступен.")
    building_id = recipe["requires_building"]
    if building_id in state.ritual_jobs:
        return ActionResult(False, "На этом алтаре уже идёт другой ритуал.")
    cost = _ritual_full_cost(recipe)
    deferred = _pay_or_defer(state, cost, f"ритуал «{recipe['name']}»", "start_ritual", recipe_id)
    if deferred is not None:
        return deferred
    duration = recipe.get("duration_turns", 1)
    if duration <= 1:
        produces = dict(recipe.get("produces", {}))
        produces_chance = recipe.get("produces_chance")
        if produces_chance:
            produces = _roll_chance_amounts(produces, produces_chance)
        produces = _roll_range_amounts(produces)
        _gain(state.resources, produces)
        return ActionResult(True, f"Ритуал завершён: {recipe['name']}.")
    state.ritual_jobs[building_id] = {
        "recipe_id": recipe_id,
        "turns_total": duration,
        "turns_done": 0,
        "extra_consumes_per_turn": dict(recipe.get("extra_consumes_per_turn", {})),
        "extra_consumes_chance": recipe.get("extra_consumes_chance"),
        "produces": dict(recipe.get("produces", {})),
        "produces_chance": recipe.get("produces_chance"),
        "cost": cost,
    }
    return ActionResult(True, f"Начат ритуал: {recipe['name']} (займётся {duration} х.).")


def cancel_ritual(state: GameState, building_id: str) -> ActionResult:
    """Снимает ритуал немедленно, БЕЗ траты хода - как cancel_manual_job.
    Возвращает игроку весь списанный при старте cost целиком; накопленные
    turns_done и уже потраченные по ходу extra_consumes_per_turn возврату НЕ
    подлежат - тот же принцип, что и у manual_jobs/energy_jobs."""
    if building_id not in state.ritual_jobs:
        return ActionResult(False, "На этом алтаре сейчас ничего не происходит.")
    job = state.ritual_jobs.pop(building_id)
    _gain(state.resources, job["cost"])
    name = RITUAL_RECIPES.get(job["recipe_id"], {}).get("name", job["recipe_id"])
    return ActionResult(True, f"Ритуал «{name}» отменён, затраты возвращены.")


def ritual_jobs_view(state: GameState) -> list[dict]:
    return [
        {"building_id": bid, "recipe_id": j["recipe_id"], "turns_done": j["turns_done"], "turns_total": j["turns_total"]}
        for bid, j in state.ritual_jobs.items()
    ]


def _process_ritual_jobs(state: GameState, report: ProductionReport) -> None:
    """Каждый ход продвигает активные ритуалы (state.ritual_jobs) на один тик.
    Сначала - попытка extra_consumes_per_turn (если задан): шанс на КАЖДЫЙ
    ресурс независимо (extra_consumes_chance, тот же _roll_chance_amounts, что
    cost_chance), затем - хватает ли склада на получившийся набор ЦЕЛИКОМ; не
    хватает - этот ход просто ничего не списывает (ритуал НЕ отменяется и НЕ
    тормозится, тот же принцип, что consumes обычной постройки). turns_done
    тикает БЕЗУСЛОВНО, как у _process_manual_jobs (cost уже списан целиком при
    старте). По достижении turns_total - produces/produces_chance
    разворачиваются РОВНО ОДИН РАЗ, job удаляется."""
    for building_id, job in list(state.ritual_jobs.items()):
        if ritual_job_pause_reason(state, building_id):
            continue  # алтарь сломан - ритуал ждёт ремонта, доп. расход не списывается
        extra = job.get("extra_consumes_per_turn")
        if extra:
            to_consume = extra
            chance = job.get("extra_consumes_chance")
            if chance:
                to_consume = _roll_chance_amounts(extra, chance)
            if to_consume and all(state.resources.get(r, 0) >= amount for r, amount in to_consume.items()):
                for r, amount in to_consume.items():
                    state.resources[r] -= amount
        job["turns_done"] += 1
        if job["turns_done"] < job["turns_total"]:
            continue
        produces = dict(job["produces"])
        if job["produces_chance"]:
            produces = _roll_chance_amounts(produces, job["produces_chance"])
        produces = _roll_range_amounts(produces)
        _gain(state.resources, produces)
        del state.ritual_jobs[building_id]
        report.ritual_completed.append(job["recipe_id"])


# ---------- Плавильня (buildings.*.smeltery) ----------
# Слоты плавки -> тигель (жидкие металлы RESOURCE_MOLTEN + всплывший шлак) -> литьё в
# формы. Три раздела рецептов (MELT_RECIPES/ALLOY_RECIPES/CAST_RECIPES) не привязаны к
# конкретной постройке: их ведёт любая плавильня с жаром не ниже recipe.heat. Состояние -
# state.smelteries[building_id], агрегат по ТИПУ постройки (как ritual_jobs). Автокрафт и
# автоматизатор плавильню не видят (по плану эры 1 их там нет): это чисто ручная механика.
#
# Ключи рецептов плавильни (state.pinned_recipes, справочник источников) - своё
# пространство, НЕ grid_recipe_key: такие рецепты не заказываются и не ставятся в
# автоматизатор, а parse_grid_recipe_key прочитал бы их как неизвестное ручное действие.
SMELTERY_KEY_PREFIXES = {"melt": "melt_recipe:", "alloy": "alloy_recipe:", "cast": "cast_recipe:"}


def smeltery_recipe_key(kind: str, recipe_id: str) -> str:
    return SMELTERY_KEY_PREFIXES[kind] + recipe_id


def parse_smeltery_recipe_key(key: str) -> tuple[str, str] | None:
    """(kind, recipe_id) для ключа рецепта плавильни, None - это не он."""
    for kind, prefix in SMELTERY_KEY_PREFIXES.items():
        if key.startswith(prefix):
            return kind, key[len(prefix):]
    return None


def smeltery_recipes(kind: str) -> dict:
    return {"melt": MELT_RECIPES, "alloy": ALLOY_RECIPES, "cast": CAST_RECIPES}[kind]


def is_smeltery(building_id: str) -> bool:
    return bool(BUILDINGS.get(building_id, {}).get("smeltery"))


def is_molten(resource_id: str) -> bool:
    return resource_id in RESOURCE_MOLTEN


def smeltery_heat(state: GameState, building_id: str) -> int:
    """Жар плавильни: buildings.*.smeltery.heat + АБСОЛЮТНАЯ добавка сильнейшего
    купленного апгрейда smeltery_heat (как ritual_pedestals). От числа копий не зависит.
    Рецепт с heat выше - недоступен (руда «не плавится»)."""
    config = BUILDINGS.get(building_id, {}).get("smeltery")
    if not config:
        return 0
    return int(config.get("heat", 1) + _farm_upgrade_multiplier(state, building_id, "smeltery_heat", default=0))


def _smeltery_scaled(state: GameState, building_id: str, field_name: str, upgrade_type: str) -> int:
    """База на копию * РАБОТАЮЩИЕ копии + добавка апгрейда - тот же принцип, что
    ritual_pedestal_capacity. 0, если плавильня не построена или сломана целиком."""
    config = BUILDINGS.get(building_id, {}).get("smeltery")
    working = working_count(state, building_id)
    if not config or working <= 0:
        return 0
    return int(config.get(field_name, 1) * working + _farm_upgrade_multiplier(state, building_id, upgrade_type, default=0))


def smeltery_slot_capacity(state: GameState, building_id: str) -> int:
    """Сколько порций сырья плавится одновременно (слоты «Плавка»)."""
    return _smeltery_scaled(state, building_id, "slots", "smeltery_slots")


def smeltery_tank_capacity(state: GameState, building_id: str) -> int:
    """Объём тигля в единицах: общий на ВСЕ металлы и шлак сразу."""
    return _smeltery_scaled(state, building_id, "capacity", "smeltery_capacity")


def _smeltery(state: GameState, building_id: str) -> dict:
    sm = state.smelteries.get(building_id)
    if sm is None:
        sm = state.smelteries[building_id] = {"slots": [], "tank": {}, "cast": None}
    return sm


def smeltery_tank_used(state: GameState, building_id: str) -> int:
    return sum((state.smelteries.get(building_id) or {}).get("tank", {}).values())


def _smeltery_unusable(state: GameState, building_id: str) -> str | None:
    """Почему плавильней сейчас нельзя работать (None - можно)."""
    if not is_smeltery(building_id):
        return "Это не плавильня."
    if state.buildings.get(building_id, 0) <= 0:
        return "Плавильня не построена."
    if working_count(state, building_id) <= 0:
        return f"«{BUILDINGS[building_id]['name']}» сломана - сначала почини её."
    return None


def _smeltery_recipe_lock(state: GameState, kind: str, recipe_id: str, building_id: str | None) -> str | None:
    """Почему рецепт плавильни недоступен (None - доступен): нет такого, не изучена
    технология, у плавильни building_id не хватает жара (литью жар не нужен)."""
    recipe = smeltery_recipes(kind).get(recipe_id)
    if recipe is None:
        return "Такого рецепта плавильни нет."
    tech = recipe.get("requires_tech")
    if tech and tech not in state.techs:
        return f"Рецепт «{recipe['name']}» ещё не открыт: нужна технология «{TECHS.get(tech, {}).get('name', tech)}»."
    if kind != "cast" and building_id is not None:
        heat = smeltery_heat(state, building_id)
        if recipe.get("heat", 1) > heat:
            return f"Не хватает жара: «{recipe['name']}» нужен жар {recipe.get('heat', 1)}, у «{BUILDINGS[building_id]['name']}» - {heat}."
    return None


def is_smeltery_recipe_available(state: GameState, kind: str, recipe_id: str, building_id: str | None = None) -> bool:
    return _smeltery_recipe_lock(state, kind, recipe_id, building_id) is None


def melt_main_input(recipe: dict) -> str | None:
    """Главное сырьё рецепта плавки - первое в input (топливо пишут следом): его
    иконка в слоте, по нему клик по плитке склада выбирает рецепт."""
    return next(iter(recipe.get("input") or {}), None)


def best_melt_recipe_for(state: GameState, building_id: str, resource_id: str) -> str | None:
    """Рецепт, которым плавильня building_id плавит resource_id при клике по плитке
    склада: среди доступных ей (жар, технология), где resource_id - главное сырьё, -
    самый «горячий» (в горне выход лучше, чем в яме), при равенстве - первый по content.json."""
    best = None
    for rid, recipe in MELT_RECIPES.items():
        if melt_main_input(recipe) != resource_id or not is_smeltery_recipe_available(state, "melt", rid, building_id):
            continue
        if best is None or recipe.get("heat", 1) > MELT_RECIPES[best].get("heat", 1):
            best = rid
    return best


def _missing_text(cost: dict, have: dict) -> str | None:
    missing = [(r, a - have.get(r, 0)) for r, a in cost.items() if have.get(r, 0) < a]
    if not missing:
        return None
    return ", ".join(f"{RESOURCE_NAMES.get(r, r)} {_format_amount(n)}" for r, n in missing)


def smeltery_free_slot(state: GameState, building_id: str) -> int | None:
    """Индекс первого свободного слота в пределах текущей ёмкости (None - все заняты)."""
    slots = (state.smelteries.get(building_id) or {}).get("slots", [])
    for i in range(smeltery_slot_capacity(state, building_id)):
        if i >= len(slots) or slots[i] is None:
            return i
    return None


def melt_block_reason(state: GameState, building_id: str, recipe_id: str) -> str | None:
    """Почему load_melt сейчас откажет (None - загрузит). Без побочных эффектов - её же
    зовёт модалка плавильни, чтобы погасить кнопку заранее и сказать почему."""
    reason = _smeltery_unusable(state, building_id) or _smeltery_recipe_lock(state, "melt", recipe_id, building_id)
    if reason:
        return reason
    if smeltery_free_slot(state, building_id) is None:
        return "Все слоты плавки заняты - дождись, пока порция стечёт в тигель."
    missing = _missing_text(MELT_RECIPES[recipe_id].get("input") or {}, state.resources)
    return f"Не хватает: {missing}." if missing else None


def alloy_block_reason(state: GameState, building_id: str, recipe_id: str) -> str | None:
    """Почему alloy_smeltery сейчас откажет (None - сплавит)."""
    reason = _smeltery_unusable(state, building_id) or _smeltery_recipe_lock(state, "alloy", recipe_id, building_id)
    if reason:
        return reason
    recipe = ALLOY_RECIPES[recipe_id]
    missing = _missing_text(recipe.get("input") or {}, (state.smelteries.get(building_id) or {}).get("tank", {}))
    if missing:
        return f"Не хватает в тигле: {missing}."
    after = smeltery_tank_used(state, building_id) - sum(recipe["input"].values()) + sum(recipe.get("output", {}).values())
    capacity = smeltery_tank_capacity(state, building_id)
    if after > capacity:
        return f"Сплав не влезет в тигель: будет {after} из {capacity}."
    return None


def cast_block_reason(state: GameState, building_id: str, recipe_id: str) -> str | None:
    """Почему pour_cast сейчас откажет (None - зальёт)."""
    reason = _smeltery_unusable(state, building_id) or _smeltery_recipe_lock(state, "cast", recipe_id, building_id)
    if reason:
        return reason
    recipe = CAST_RECIPES[recipe_id]
    sm = state.smelteries.get(building_id) or {}
    if sm.get("cast"):
        return "Литейный стол занят - отливка ещё остывает."
    form = recipe.get("form")
    if form and state.resources.get(form, 0) < 1:
        return f"Нет формы: {RESOURCE_NAMES.get(form, form)}."
    metal, amount = recipe["metal"], recipe.get("amount", 1)
    have = sm.get("tank", {}).get(metal, 0)
    if have < amount:
        return f"В тигле мало металла: нужно {RESOURCE_NAMES.get(metal, metal)} {amount}, есть {have}."
    return None


def load_melt(state: GameState, building_id: str, recipe_id: str) -> ActionResult:
    """Загрузка порции в слот плавки (тратит ход, как клик): input списывается целиком
    сразу, дальше слот сам тикает turns ходов (_process_smelteries) и стекает в тигль."""
    reason = melt_block_reason(state, building_id, recipe_id)
    if reason:
        return ActionResult(False, reason)
    recipe = MELT_RECIPES[recipe_id]
    index = smeltery_free_slot(state, building_id)
    cost = dict(recipe.get("input") or {})
    for r, amount in cost.items():
        state.resources[r] -= amount
    sm = _smeltery(state, building_id)
    while len(sm["slots"]) <= index:
        sm["slots"].append(None)
    sm["slots"][index] = {"recipe_id": recipe_id, "turns_done": 0, "cost": cost}
    return ActionResult(True, f"В плавку: {recipe['name']} ({recipe.get('turns', 1)} х.).")


def cancel_melt_slot(state: GameState, building_id: str, index: int) -> ActionResult:
    """Вынуть порцию из слота - БЕЗ траты хода, всё списанное при загрузке возвращается
    (тот же принцип, что cancel_manual_job); накопленные ходы плавки теряются."""
    slots = (state.smelteries.get(building_id) or {}).get("slots", [])
    if not isinstance(index, int) or not (0 <= index < len(slots)) or slots[index] is None:
        return ActionResult(False, "Этот слот пуст.")
    slot = slots[index]
    slots[index] = None
    _gain(state.resources, {r: a for r, a in slot.get("cost", {}).items() if r in RESOURCE_NAMES})
    name = MELT_RECIPES.get(slot["recipe_id"], {}).get("name", slot["recipe_id"])
    return ActionResult(True, f"Вынуто из плавки: {name} - сырьё вернулось на склад.")


def skim_smeltery(state: GameState, building_id: str) -> ActionResult:
    """Снять шлак: всё НЕ-металлическое из тигля - на склад. Не тратит ход."""
    tank = (state.smelteries.get(building_id) or {}).get("tank", {})
    dross = {r: a for r, a in tank.items() if not is_molten(r)}
    if not dross:
        return ActionResult(False, "Шлака в тигле нет.")
    for r in dross:
        del tank[r]
    _gain(state.resources, {r: a for r, a in dross.items() if r in RESOURCE_NAMES})
    return ActionResult(True, "Шлак снят: " + ", ".join(f"{RESOURCE_NAMES.get(r, r)} {a}" for r, a in dross.items()) + ".")


def alloy_smeltery(state: GameState, building_id: str, recipe_id: str) -> ActionResult:
    """Сплав по кнопке (тратит ход): input из тигля -> output в тот же тигль, результат
    должен влезть. Сплав с hidden открывается этой же пробой (state.discovered_alloys)."""
    reason = alloy_block_reason(state, building_id, recipe_id)
    if reason:
        return ActionResult(False, reason)
    recipe = ALLOY_RECIPES[recipe_id]
    tank = _smeltery(state, building_id)["tank"]
    for r, amount in recipe["input"].items():
        tank[r] -= amount
        if tank[r] <= 0:
            del tank[r]
    for r, amount in recipe.get("output", {}).items():
        tank[r] = tank.get(r, 0) + amount
    if recipe.get("hidden") and recipe_id not in state.discovered_alloys:
        state.discovered_alloys.add(recipe_id)
        return ActionResult(True, f"Открыт новый сплав: {recipe['name']}!")
    return ActionResult(True, f"Сплав: {recipe['name']}.")


def pour_cast(state: GameState, building_id: str, recipe_id: str) -> ActionResult:
    """Залить металл из тигля в форму (тратит ход). Форма (recipe.form) - предмет
    склада, который нужен, но НЕ расходуется. turns <= 1 - отливка сразу на склад, иначе
    литейный стол занят, пока она остывает (одна отливка на плавильню)."""
    reason = cast_block_reason(state, building_id, recipe_id)
    if reason:
        return ActionResult(False, reason)
    recipe = CAST_RECIPES[recipe_id]
    sm = _smeltery(state, building_id)
    metal, amount = recipe["metal"], recipe.get("amount", 1)
    sm["tank"][metal] -= amount
    if sm["tank"][metal] <= 0:
        del sm["tank"][metal]
    turns = recipe.get("turns", 1)
    if turns <= 1:
        _gain(state.resources, recipe.get("output", {}))
        return ActionResult(True, f"Отлито: {recipe['name']}.")
    sm["cast"] = {"recipe_id": recipe_id, "turns_done": 0, "turns_total": turns}
    return ActionResult(True, f"Залито: {recipe['name']} (остынет за {turns} х.).")


def _process_smelteries(state: GameState, report: ProductionReport) -> None:
    """Ход плавилен: отливка на литейном столе остывает (готова - на склад), каждый
    занятый слот в пределах ёмкости плавится +1 ход; доплавленная порция стекает в
    тигль, если её output влезает целиком, иначе слот ждёт места (не пропадает).
    Сломанная целиком плавильня стоит: не плавит и не остужает."""
    for building_id, sm in state.smelteries.items():
        if not is_smeltery(building_id) or working_count(state, building_id) <= 0:
            continue
        cast = sm.get("cast")
        if cast:
            cast["turns_done"] += 1
            if cast["turns_done"] >= cast["turns_total"]:
                sm["cast"] = None
                recipe = CAST_RECIPES.get(cast["recipe_id"])
                if recipe:
                    _gain(state.resources, recipe.get("output", {}))
                    report.smeltery_events.append({
                        "building_id": building_id, "kind": "cast_done", "recipe_id": cast["recipe_id"],
                        "output": dict(recipe.get("output", {})),
                    })
        slot_capacity = smeltery_slot_capacity(state, building_id)
        tank_capacity = smeltery_tank_capacity(state, building_id)
        for i, slot in enumerate(sm["slots"]):
            if slot is None or i >= slot_capacity:
                continue
            recipe = MELT_RECIPES.get(slot["recipe_id"])
            if recipe is None:
                continue
            turns = recipe.get("turns", 1)
            if slot["turns_done"] < turns:
                slot["turns_done"] += 1
            if slot["turns_done"] < turns:
                continue
            output = recipe.get("output") or {}
            if smeltery_tank_used(state, building_id) + sum(output.values()) > tank_capacity:
                if not slot.get("waiting"):
                    slot["waiting"] = True
                    report.smeltery_events.append({
                        "building_id": building_id, "kind": "waiting", "slot": i, "recipe_id": slot["recipe_id"],
                    })
                continue
            for r, amount in output.items():
                sm["tank"][r] = sm["tank"].get(r, 0) + amount
            sm["slots"][i] = None
            report.smeltery_events.append({
                "building_id": building_id, "kind": "melted", "slot": i, "recipe_id": slot["recipe_id"],
                "output": dict(output),
            })


def _drop_smeltery(state: GameState, building_id: str) -> None:
    """Плавильни больше нет (исчезла из контента): сырьё из
    слотов возвращается на склад, металл в тигле и недоостывшая отливка пропадают."""
    sm = state.smelteries.pop(building_id, None) or {}
    for slot in sm.get("slots", []):
        if slot:
            _gain(state.resources, {r: a for r, a in slot.get("cost", {}).items() if r in RESOURCE_NAMES})


# Операторы условий автоматизации (state.automation_rules) - держать в паре с
# CLAUDE.md/UI (web_ui.html, content_editor.html не участвует - правила это
# state, не content).
AUTOMATION_CONDITION_OPS = (">=", "<=")


def automation_slots_capacity(state: GameState, building_id: str) -> int:
    """Сколько правил автоматизации можно разом держать в building_id - тот же
    принцип, что soul_collector_capacity: per-copy база (buildings.*.
    automation_hub.slots) * число РАБОТАЮЩИХ копий, плюс АБСОЛЮТНАЯ добавка от
    сильнейшего купленного апгрейда типа automation_slots (см.
    _farm_upgrade_multiplier). 0, если постройка не автоматизатор вовсе, или
    если ВСЕ копии сломаны - бонус от апгрейда гейтится тем же working_count,
    что и база, иначе сломанная постройка продолжала бы держать настроенные
    правила "живыми". process_automation_rules сам поломку не проверяет и
    полагается на capacity == 0, ровно как _process_soul_collectors."""
    config = BUILDINGS.get(building_id, {}).get("automation_hub")
    if not config or working_count(state, building_id) <= 0:
        return 0
    base = config.get("slots", 0) * working_count(state, building_id)
    bonus = _farm_upgrade_multiplier(state, building_id, "automation_slots", default=0)
    return int(base + bonus)


def is_automation_eligible_action(action_id: str) -> bool:
    """Действие можно поставить в правило автоматизации, только если оно
    детерминировано (см. is_manual_action_deterministic - та же граница, что у
    автокрафта: без вероятностного cost_chance/gain_chance и без диапазона в
    gain). Многоходовые действия (duration_turns > 1) РАЗРЕШЕНЫ - do_manual_action
    сам не даст запустить вторую копию одного и того же action_id, пока первая не
    завершилась (state.manual_jobs - одна активная задача НА действие ГЛОБАЛЬНО,
    не на правило/постройку), так что process_automation_rules просто получит
    ActionResult(False, "уже выполняется") и молча пропустит ход для этого
    правила - без крашей и без постановки в очередь. По прямому решению это
    ЦЕНА автоматизации многоходового действия: если слот занят (в т.ч. занят
    самим игроком вручную), освободить его для повторного авто-запуска можно
    только явной отменой (cancel_manual_job) - автоматизация сама ничего не
    отменяет и не форсирует."""
    action = MANUAL_ACTIONS.get(action_id)
    if action is None:
        return False
    return is_manual_action_deterministic(action_id)


def is_energy_recipe_unlocked(recipe_id: str, state: GameState) -> bool:
    recipe = ENERGY_RECIPES[recipe_id]
    req_tech = recipe.get("requires_tech")
    return not req_tech or req_tech in state.techs


def is_automation_eligible_energy_recipe(recipe_id: str) -> bool:
    """Рецепт крафта за энергию можно поставить в правило автоматизации без
    каких-либо доп. ограничений - в отличие от ручных действий, тут нет
    cost_chance/gain_chance (шанс потребления/получения ресурса вообще не
    существует у energy_recipes); диапазон в produces (см. _is_range_amount)
    НЕ исключает рецепт - process_automation_rules не занимается плановой
    бухгалтерией автокрафта, просто вызывает start_energy_recipe РОВНО ОДИН
    раз за срабатывание, а бросок диапазона происходит уже ВНУТРИ
    _process_energy_jobs при завершении job'а, как и для любого другого
    способа запуска этого рецепта (клик игрока/автокрафт)."""
    return recipe_id in ENERGY_RECIPES


# Ритуал - цель без ограничений, как крафт за энергию: правило просто зовёт
# start_ritual (как клик игрока), шанс/диапазон produces бросается уже там.
AUTOMATION_TARGET_KINDS = ("manual_action", "energy_recipe", "ritual_recipe")
# Вид заказа (_ORDER_RETRY), который оформляет срабатывание правила, если сырья не хватает.
_AUTOMATION_ORDER_KIND = {"manual_action": "manual", "energy_recipe": "start_energy_recipe", "ritual_recipe": "start_ritual"}


def _is_automation_target_eligible(kind: str, target_id: str) -> bool:
    if kind == "manual_action":
        return is_automation_eligible_action(target_id)
    if kind == "energy_recipe":
        return is_automation_eligible_energy_recipe(target_id)
    if kind == "ritual_recipe":
        return target_id in RITUAL_RECIPES
    return False


def _automation_target_name(kind: str, target_id: str) -> str:
    table = {"manual_action": MANUAL_ACTIONS, "energy_recipe": ENERGY_RECIPES, "ritual_recipe": RITUAL_RECIPES}[kind]
    return table[target_id]["name"]


def automation_eligible_targets() -> list[dict]:
    """Все цели (ручное действие ИЛИ рецепт крафта за энергию), которые в
    принципе можно поставить в правило (см. is_automation_eligible_action/
    is_automation_eligible_energy_recipe) - НЕ требует, чтобы цель уже была
    открыта (правило можно настроить заранее, см. process_automation_rules).
    Для UI-пикера выбора цели правила."""
    targets = [
        {"kind": "manual_action", "id": action_id, "name": action["name"]}
        for action_id, action in MANUAL_ACTIONS.items()
        if is_automation_eligible_action(action_id)
    ]
    targets += [
        {"kind": "energy_recipe", "id": recipe_id, "name": recipe["name"]}
        for recipe_id, recipe in ENERGY_RECIPES.items()
    ]
    targets += [
        {"kind": "ritual_recipe", "id": recipe_id, "name": recipe["name"]}
        for recipe_id, recipe in RITUAL_RECIPES.items()
    ]
    return targets


def _automation_condition_met(condition: dict, state: GameState) -> bool:
    amount = state.resources.get(condition["resource"], 0)
    if condition["op"] == ">=":
        return amount >= condition["amount"]
    return amount <= condition["amount"]


def _validate_automation_conditions(conditions: list[dict]) -> str | None:
    for condition in conditions:
        resource_id = condition.get("resource")
        # SPECIES исключены - id вида переиспользует id ресурса только ради
        # имени/иконки (см. content.py:SPECIES), особи живут в state.creatures,
        # не копятся стопкой в state.resources - условие на такой id никогда
        # не могло бы стать истинным по-настоящему.
        if resource_id not in RESOURCE_NAMES or resource_id in SPECIES:
            return "Неизвестный ресурс в условии."
        if condition.get("op") not in AUTOMATION_CONDITION_OPS:
            return "Неверный оператор условия."
        amount = condition.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
            return "Неверное количество в условии."
    return None


def add_automation_rule(
    state: GameState, building_id: str, kind: str, target_id: str, conditions: list[dict],
) -> ActionResult:
    """Не тратит ход - настройка правила, как insert_soul. kind - "manual_action",
    "energy_recipe" или "ritual_recipe" (AUTOMATION_TARGET_KINDS), target_id - id действия/рецепта
    внутри этого kind. conditions - список {"resource","op","amount"}, проверяемых
    через И (пустой список = правило срабатывает всегда, см.
    process_automation_rules)."""
    if not BUILDINGS.get(building_id, {}).get("automation_hub"):
        return ActionResult(False, "У этой постройки нет автоматизатора.")
    if kind not in AUTOMATION_TARGET_KINDS or not _is_automation_target_eligible(kind, target_id):
        return ActionResult(False, "Это нельзя поставить в автоматизацию.")
    error = _validate_automation_conditions(conditions)
    if error:
        return ActionResult(False, error)
    rules = state.automation_rules.setdefault(building_id, [])
    if len(rules) >= automation_slots_capacity(state, building_id):
        return ActionResult(False, "Нет свободных слотов автоматизации.")
    rules.append({"kind": kind, "target_id": target_id, "conditions": [dict(c) for c in conditions]})
    return ActionResult(True, f"Правило добавлено: «{_automation_target_name(kind, target_id)}».")


def update_automation_rule(
    state: GameState, building_id: str, index: int, kind: str, target_id: str, conditions: list[dict],
) -> ActionResult:
    """Правит правило НА МЕСТЕ (не меняя его позицию в списке) - не тратит ход."""
    rules = state.automation_rules.get(building_id, [])
    if not isinstance(index, int) or not (0 <= index < len(rules)):
        return ActionResult(False, "Такого правила тут нет.")
    if kind not in AUTOMATION_TARGET_KINDS or not _is_automation_target_eligible(kind, target_id):
        return ActionResult(False, "Это нельзя поставить в автоматизацию.")
    error = _validate_automation_conditions(conditions)
    if error:
        return ActionResult(False, error)
    rules[index] = {"kind": kind, "target_id": target_id, "conditions": [dict(c) for c in conditions]}
    return ActionResult(True, f"Правило обновлено: «{_automation_target_name(kind, target_id)}».")


def remove_automation_rule(state: GameState, building_id: str, index: int) -> ActionResult:
    """Не тратит ход, как remove_soul."""
    rules = state.automation_rules.get(building_id, [])
    if not isinstance(index, int) or not (0 <= index < len(rules)):
        return ActionResult(False, "Такого правила тут нет.")
    rules.pop(index)
    return ActionResult(True, "Правило удалено.")


def process_automation_rules(state: GameState, report: ProductionReport, daytime: bool) -> None:
    """Каждый ход, ПОСЛЕ _process_manual_jobs и ДО 2-го прохода _process_orders (см.
    advance_turn), чтобы правило видело уже готовую продукцию этого хода
    (постройки/фермы/синтезаторы/энергокрафт), но срабатывало до финального
    клэмпа энергии/склада. Для каждой постройки с настроенными правилами -
    первые automation_slots_capacity(...) правил её списка (лишние, если
    слотов не хватает, просто не участвуют, тот же принцип, что soul_slots),
    если постройка сейчас активна (active_period) и ВСЕ условия правила
    истинны (И, пустой список условий = всегда, см. _automation_condition_met).
    Правило на ещё не открытое действие молча пропускается - можно настроить
    заранее, сработает само после открытия. Несколько правил делят общий
    ресурс в порядке списка - тот же осознанный компромисс "кто раньше встал,
    тот раньше взял", что и везде в advance_turn для разделяемых ресурсов.
    Нехватка стороннего ресурса (не входящего в условие) просто не даёт
    правилу сработать в этот ход - do_manual_action/start_energy_recipe сами
    вернут ok=False, без ошибок и без записи в report. Многоходовое действие
    (duration_turns > 1, см. is_automation_eligible_action) при срабатывании
    только СТАРТУЕТ job в state.manual_jobs (report.automation_fired получит
    "Начато: ...") - само начисление gain произойдёт позже, в
    _process_manual_jobs, уже без пометки, что job запустила именно
    автоматизация. Пока job активен, do_manual_action отказывает ЛЮБОЙ попытке
    запустить это же action_id повторно (в т.ч. следующим тикам этого же
    правила) - правило просто не срабатывает, пока слот не освободится сам (по
    завершении) или явной cancel_manual_job.

    Рецепт крафта за энергию (kind == "energy_recipe") работает СИММЕТРИЧНО:
    start_energy_recipe сам ищет свободный преобразователь подходящего типа
    (recipe.machine, если задан) и отказывает (ok=False), если все заняты -
    правило в этом случае просто не срабатывает, освободить слот раньше
    завершения можно только явной cancel_energy_job. В отличие от manual_action,
    ограничение "один активный джоб на весь action_id" тут вообще не действует -
    несколько job'ов ОДНОГО recipe_id могут идти параллельно в разных
    преобразователях (см. start_energy_recipe/_pick_converter_building_with_free_slot),
    а один вызов start_energy_recipe занимает РОВНО ОДИН свободный
    преобразователь за раз. Поэтому правило без условий на рецепт с несколькими
    свободными преобразователями заполнит их ВСЕ, но по одному за ход
    (следующий свободный слот подхватится на следующем срабатывании правила,
    не в тот же ход).

    Ритуал (kind == "ritual_recipe") - как клик «Начать ритуал» (start_ritual):
    правило молча ждёт, пока ритуал не станет доступен (технология, алтарь,
    пьедесталы); многоходовой занимает алтарь, и правило не срабатывает, пока
    ритуал не закончится (_process_ritual_jobs идёт раньше - завершившийся в этот
    ход ритуал перезапускается сразу)."""
    for building_id, rules in state.automation_rules.items():
        if not rules:
            continue
        capacity = automation_slots_capacity(state, building_id)
        if capacity <= 0:
            continue
        if not is_building_active_now(building_id, daytime):
            continue
        for rule in rules[:capacity]:
            kind = rule["kind"]
            target_id = rule["target_id"]
            if kind == "manual_action":
                if target_id not in MANUAL_ACTIONS or not is_manual_action_unlocked(target_id, state):
                    continue
            elif kind == "energy_recipe":
                if target_id not in ENERGY_RECIPES or not is_energy_recipe_unlocked(target_id, state):
                    continue
            elif kind == "ritual_recipe":
                if target_id not in RITUAL_RECIPES or not is_ritual_recipe_available(target_id, state):
                    continue
            else:
                continue
            if not all(_automation_condition_met(c, state) for c in rule["conditions"]):
                continue
            # Прошлое срабатывание уже оформило заказ (не хватало сырья или энергии) -
            # ждём его: повторный вызов ничего не сделал бы, только писал бы в журнал
            # «уже заказано» каждый ход.
            order_kind = _AUTOMATION_ORDER_KIND[kind]
            if any(o["kind"] == order_kind and o["target_id"] == target_id for o in state.orders):
                continue
            if kind == "manual_action":
                result = do_manual_action(state, target_id)
            elif kind == "energy_recipe":
                result = start_energy_recipe(state, target_id)
            else:
                result = start_ritual(state, target_id)
            if result.ok:
                report.automation_fired.append({
                    "building_id": building_id, "kind": kind, "target_id": target_id, "message": result.message,
                })


def storage_capacity(state: GameState, resource_id: str) -> int | None:
    """Максимум resource_id, который можно хранить одновременно - base_capacity
    (content.json resources[].storage) + capacity_add КАЖДОГО купленного тира
    расширения (state.storage_tier, тиры покупаются строго по порядку, как тиры
    инструментов). None - у ресурса вообще не настроена вместимость склада
    (resources[].storage отсутствует) - хранится без ограничений, как было для
    ВСЕХ ресурсов до этой механики. Обрезается в конце advance_turn той же
    моделью, что energy_capacity: излишек сверх вместимости сгорает В КОНЦЕ ХОДА,
    а не блокирует производство/автокрафт В ТЕЧЕНИЕ хода - см. CLAUDE.md, "Склад".
    Именно поэтому автокрафт эту вместимость вообще не проверяет (ни здесь, ни в
    _ensure_available/autocraft_preview): промежуточный докрафт целиком укладывается
    в один ход и обычно тут же расходуется дальше по цепочке, до того как
    случится обрезка - "хранится в процессоре, а не на складе", а не настоящая
    отдельная бухгалтерия.

    Тир с флагом "infinite" (см. content_editor: чекбокс "Делает вместимость
    безграничной") вместо очередного capacity_add снимает ограничение СОВСЕМ -
    как только он куплен, возвращается None, тот же результат, что у ресурса
    вовсе без storage. Это НЕ отдельная "числом побольше" механика: единственный
    реальный потолок ресурса (MAX_RESOURCE_AMOUNT) уже применяется везде в
    _add_resource независимо от storage, так что "бесконечный" склад на практике
    и есть "очень большой и постоянно поддерживаемый" - тем же кодом, что и
    остальные ресурсы без вместимости. Тиры ПОСЛЕ infinite (если такие есть в
    контенте) при подсчёте уже не разбираются - once куплен infinite, любой
    дальнейший тир того же ресурса ни на что не влияет."""
    config = RESOURCE_STORAGE.get(resource_id)
    if config is None:
        return None
    tier = state.storage_tier.get(resource_id, 0)
    capacity = config["base_capacity"]
    for upgrade in config["tiers"][:tier]:
        if upgrade.get("infinite"):
            return None
        capacity += upgrade["capacity_add"]
    return capacity


def next_storage_tier_info(state: GameState, resource_id: str) -> dict | None:
    """Следующий ещё не купленный тир расширения склада resource_id - None, если у
    ресурса вообще нет storage, или он уже на максимальном тире."""
    config = RESOURCE_STORAGE.get(resource_id)
    if config is None:
        return None
    tiers = config["tiers"]
    tier = state.storage_tier.get(resource_id, 0)
    if tier >= len(tiers):
        return None
    return tiers[tier]


def upgradable_storage(state: GameState) -> list[str]:
    """Ресурсы, у которых есть следующий тир расширения склада и для него уже
    открыта нужная технология (если она указана) - как upgradable_tools, чтобы UI
    не показывал кнопку расширения, которую всё равно нельзя нажать."""
    result = []
    for resource_id in RESOURCE_STORAGE:
        next_info = next_storage_tier_info(state, resource_id)
        if next_info is not None and (not next_info.get("requires_tech") or next_info["requires_tech"] in state.techs):
            result.append(resource_id)
    return result


def upgrade_storage(state: GameState, resource_id: str) -> ActionResult:
    name = RESOURCE_NAMES.get(resource_id, resource_id)
    next_info = next_storage_tier_info(state, resource_id)
    if next_info is None:
        return ActionResult(False, f"Склад «{name}» уже максимальной вместимости.")
    req_tech = next_info.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return ActionResult(False, f"Для расширения склада «{name}» не хватает технологии.")
    deferred = _pay_or_defer(state, next_info["cost"], f"расширение склада «{name}»", "storage_upgrade", resource_id)
    if deferred is not None:
        return deferred
    state.storage_tier[resource_id] = state.storage_tier.get(resource_id, 0) + 1
    new_capacity = storage_capacity(state, resource_id)
    capacity_text = "теперь без ограничений" if new_capacity is None else f"теперь {new_capacity}"
    return ActionResult(True, f"Склад «{name}» расширен: вместимость {capacity_text}.")


def building_upgrade_defs(building_id: str) -> list[dict]:
    return BUILDINGS[building_id].get("upgrades", [])


def is_building_upgrade_purchased(state: GameState, building_id: str, upgrade_id: str) -> bool:
    return upgrade_id in state.building_upgrades.get(building_id, [])


def _instance_has_upgrade_type(building_id: str, instance: dict, upgrade_type: str) -> bool:
    """Куплен ли у ЭТОГО экземпляра фермы (instance["upgrades"]) хоть один
    апгрейд заданного типа - используется для per-instance-эффектов, которые
    у ферм всегда индивидуальны (breeding_immortality и, теперь,
    breakdown_immunity - см. advance_turn), в отличие от type-wide
    state.building_upgrades обычных построек."""
    types_by_id = {u["id"]: u["type"] for u in building_upgrade_defs(building_id)}
    return any(types_by_id.get(uid) == upgrade_type for uid in instance["upgrades"])


def building_upgrade_purchase_count(state: GameState, building_id: str, upgrade_id: str) -> int:
    """Сколько раз ИМЕННО ЭТОТ апгрейд уже куплен - для repeatable-апгрейдов
    может быть больше 1 (state.building_upgrades допускает повторяющиеся id в
    списке, см. purchase_building_upgrade); для обычного - 0 или 1."""
    return state.building_upgrades.get(building_id, []).count(upgrade_id)


def building_upgrade_type_purchased_anywhere(state: GameState, upgrade_type: str) -> bool:
    """Куплен ли хоть один апгрейд заданного ТИПА (buildings.*.upgrades[].type,
    например "autocraft" - "Автоматизация") хоть у ОДНОЙ постройки в игре - в
    отличие от building_upgrade_purchased/building_upgrade_level_at_least (квестовое
    условие ссылается на КОНКРЕТНУЮ постройку+id улучшения), это условие "куплено ли
    улучшение такого-то типа хоть где-нибудь" - не важно, у какой именно постройки и
    под каким id (см. CONDITION_TYPES: "Купить улучшение типа у любого механизма")."""
    for building_id, purchased in state.building_upgrades.items():
        if not purchased:
            continue
        types_by_id = {u["id"]: u["type"] for u in building_upgrade_defs(building_id)}
        if any(types_by_id.get(uid) == upgrade_type for uid in purchased):
            return True
    return False


def farm_instance_upgrade_purchase_count(state: GameState, instance_id: str, upgrade_id: str) -> int:
    instance = state.farm_instances.get(instance_id)
    return instance["upgrades"].count(upgrade_id) if instance is not None else 0


def soul_collector_instance_upgrade_purchase_count(state: GameState, instance_id: str, upgrade_id: str) -> int:
    instance = state.soul_collector_instances.get(instance_id)
    return instance["upgrades"].count(upgrade_id) if instance is not None else 0


def building_upgrade_prerequisite(building_id: str, upgrade_id: str) -> dict | None:
    """Улучшение НЕПОСРЕДСТВЕННО ПЕРЕД данным СРЕДИ улучшений ТОГО ЖЕ ТИПА этой
    постройки (по порядку в content.json["buildings"][id]["upgrades"]) - следующий
    уровень прогрессии (например, "scale_3") нельзя купить, пока не куплен
    предыдущий ("scale_2"), см. purchase_building_upgrade. None, если этот
    апгрейд - первый в своей прогрессии (или единственный своего типа у
    постройки) - предшественника нет, покупать можно сразу.

    Реализовано ПОСЛЕДОВАТЕЛЬНО по позиции в списке (простой вариант, прямо
    разрешённый вместо отдельного поля "зависит от улучшения X" в content.json) -
    порядок в списке И ТАК уже значим для scale-прогрессий (тир = позиция, тот же
    принцип, что у tools[tool_id]["tiers"]), так что реордер апгрейдов в редакторе
    задним числом настолько же опасен, как и там же - меняет, что является
    предпосылкой чего."""
    defs = building_upgrade_defs(building_id)
    target = next((u for u in defs if u["id"] == upgrade_id), None)
    if target is None:
        return None
    prerequisite = None
    for u in defs:
        if u["id"] == upgrade_id:
            break
        if u["type"] == target["type"]:
            prerequisite = u
    return prerequisite


def purchase_building_upgrade(state: GameState, building_id: str, upgrade_id: str) -> ActionResult:
    """Покупка ОДНОГО улучшения ОДНОГО типа постройки (не копии) - действует сразу на
    ВСЕ текущие и будущие рабочие копии этой постройки, как эффект технологии, только
    покупается напрямую в каталоге строительства, а не через дерево технологий (см.
    effective_building_stats). Не требует РАБОЧЕЙ копии - достаточно хотя бы одной
    построенной, пусть даже сейчас сломанной, т.к. улучшение относится к типу
    постройки, а не к конкретному экземпляру."""
    building_name = BUILDINGS[building_id]["name"]
    upgrade = next((u for u in building_upgrade_defs(building_id) if u["id"] == upgrade_id), None)
    if upgrade is None:
        return ActionResult(False, "Такого улучшения не существует.")
    times = building_upgrade_purchase_count(state, building_id, upgrade_id)
    repeatable = upgrade.get("repeatable", False)
    if not repeatable and times > 0:
        return ActionResult(False, "Уже куплено.")
    max_purchases = upgrade.get("max_purchases")
    if repeatable and max_purchases is not None and times >= max_purchases:
        return ActionResult(False, "Куплено максимальное число раз.")
    if state.buildings.get(building_id, 0) <= 0:
        return ActionResult(False, f"Нужно сначала построить «{building_name}».")
    req_tech = upgrade.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return ActionResult(False, "Не выполнены условия для покупки улучшения.")
    prerequisite = building_upgrade_prerequisite(building_id, upgrade_id)
    if prerequisite is not None and not is_building_upgrade_purchased(state, building_id, prerequisite["id"]):
        return ActionResult(False, f"Сначала нужно купить «{prerequisite['name']}».")
    cost = repeatable_upgrade_cost(upgrade, times)
    deferred = _pay_or_defer(
        state, cost, f"улучшение «{upgrade['name']}» для «{building_name}»",
        "building_upgrade", f"{building_id}:{upgrade_id}",
    )
    if deferred is not None:
        return deferred
    state.building_upgrades.setdefault(building_id, []).append(upgrade_id)
    suffix = f" (куплено {times + 1}-й раз)" if repeatable else ""
    return ActionResult(True, f"Улучшение «{upgrade['name']}» куплено для «{building_name}»{suffix}.")


def purchase_farm_instance_upgrade(state: GameState, instance_id: str, upgrade_id: str) -> ActionResult:
    """Аналог purchase_building_upgrade, но для ОДНОГО экземпляра фермы
    (state.farm_instances[instance_id]["upgrades"]) - см. CLAUDE.md, "Пасеки:
    индивидуальные экземпляры". Апгрейды типа "environment_emulation" (одна
    промышленная пасека эмулирует РОВНО одну среду разом) - особый случай: НЕ
    участвуют в цепочке "предыдущий уровень того же типа" (building_upgrade_prerequisite
    для них не вызывается - все 6 в content.json одного типа, но друг другу не
    предки), а взаимоисключающие - покупка новой среды удаляет из upgrades
    экземпляра любую ранее купленную среду (замена, старая просто теряется,
    цена новой платится полностью). Ход тратит вызывающий - как у улучшения обычной
    постройки (tools/web_ui.py: dispatch_action)."""
    _ensure_farm_instances(state)
    instance = state.farm_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такой пасеки нет.")
    building_id = instance["building_id"]
    building_name = BUILDINGS[building_id]["name"]
    defs = building_upgrade_defs(building_id)
    upgrade = next((u for u in defs if u["id"] == upgrade_id), None)
    if upgrade is None:
        return ActionResult(False, "Такого улучшения не существует.")
    times = farm_instance_upgrade_purchase_count(state, instance_id, upgrade_id)
    repeatable = upgrade.get("repeatable", False)
    if not repeatable and times > 0:
        return ActionResult(False, "Уже куплено.")
    max_purchases = upgrade.get("max_purchases")
    if repeatable and max_purchases is not None and times >= max_purchases:
        return ActionResult(False, "Куплено максимальное число раз.")
    req_tech = upgrade.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return ActionResult(False, "Не выполнены условия для покупки улучшения.")
    is_environment = upgrade["type"] == "environment_emulation"
    if not is_environment:
        prerequisite = building_upgrade_prerequisite(building_id, upgrade_id)
        if prerequisite is not None and prerequisite["id"] not in instance["upgrades"]:
            return ActionResult(False, f"Сначала нужно купить «{prerequisite['name']}».")
    cost = repeatable_upgrade_cost(upgrade, times)
    deferred = _pay_or_defer(
        state, cost, f"улучшение «{upgrade['name']}» для «{building_name}»",
        "farm_instance_upgrade", f"{instance_id}:{upgrade_id}",
    )
    if deferred is not None:
        return deferred
    if is_environment:
        env_ids = {u["id"] for u in defs if u["type"] == "environment_emulation"}
        instance["upgrades"] = [uid for uid in instance["upgrades"] if uid not in env_ids]
    instance["upgrades"].append(upgrade_id)
    suffix = f" (куплено {times + 1}-й раз)" if repeatable else ""
    return ActionResult(True, f"Улучшение «{upgrade['name']}» куплено для «{building_name}»{suffix}.")


def purchase_soul_collector_instance_upgrade(state: GameState, instance_id: str, upgrade_id: str) -> ActionResult:
    """Аналог purchase_farm_instance_upgrade, но для ОДНОГО экземпляра
    синтезатора душ (state.soul_collector_instances[instance_id]["upgrades"]) -
    см. CLAUDE.md/state.py:soul_collector_instances. Упрощённая версия (без
    ветки environment_emulation - этого типа апгрейдов у soul_collector построек
    не бывает, взаимоисключающие среды - только у ферм)."""
    _ensure_soul_collector_instances(state)
    instance = state.soul_collector_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого синтезатора нет.")
    building_id = instance["building_id"]
    building_name = BUILDINGS[building_id]["name"]
    defs = building_upgrade_defs(building_id)
    upgrade = next((u for u in defs if u["id"] == upgrade_id), None)
    if upgrade is None:
        return ActionResult(False, "Такого улучшения не существует.")
    times = soul_collector_instance_upgrade_purchase_count(state, instance_id, upgrade_id)
    repeatable = upgrade.get("repeatable", False)
    if not repeatable and times > 0:
        return ActionResult(False, "Уже куплено.")
    max_purchases = upgrade.get("max_purchases")
    if repeatable and max_purchases is not None and times >= max_purchases:
        return ActionResult(False, "Куплено максимальное число раз.")
    req_tech = upgrade.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return ActionResult(False, "Не выполнены условия для покупки улучшения.")
    prerequisite = building_upgrade_prerequisite(building_id, upgrade_id)
    if prerequisite is not None and prerequisite["id"] not in instance["upgrades"]:
        return ActionResult(False, f"Сначала нужно купить «{prerequisite['name']}».")
    cost = repeatable_upgrade_cost(upgrade, times)
    deferred = _pay_or_defer(
        state, cost, f"улучшение «{upgrade['name']}» для «{building_name}»",
        "soul_instance_upgrade", f"{instance_id}:{upgrade_id}",
    )
    if deferred is not None:
        return deferred
    instance["upgrades"].append(upgrade_id)
    suffix = f" (куплено {times + 1}-й раз)" if repeatable else ""
    return ActionResult(True, f"Улучшение «{upgrade['name']}» куплено для «{building_name}»{suffix}.")


def reactor_instance_upgrade_purchase_count(state: GameState, instance_id: str, upgrade_id: str) -> int:
    instance = state.reactor_instances.get(instance_id)
    return instance["upgrades"].count(upgrade_id) if instance is not None else 0


def purchase_reactor_instance_upgrade(state: GameState, instance_id: str, upgrade_id: str) -> ActionResult:
    """Аналог purchase_farm_instance_upgrade, но для ОДНОГО экземпляра реактора
    (state.reactor_instances[instance_id]["upgrades"]). "reactor_zoning" -
    взаимоисключающий выбор (зеркало environment_emulation у ферм - покупка
    нового уровня зонирования заменяет предыдущий купленный того же типа на
    ЭТОМ экземпляре, а не участвует в цепочке "предыдущий уровень" через
    building_upgrade_prerequisite). "reactor_grid_size" - обычная позиционная
    прогрессия (как scale), prerequisite-цепочка работает как обычно. После
    успешной покупки СРАЗУ доращивает решётку/зоны (_ensure_reactor_grid/
    _ensure_reactor_zones) - новая ёмкость видна немедленно, не дожидаясь
    следующего advance_turn. Ход тратит вызывающий - как у улучшения обычной
    постройки (tools/web_ui.py: dispatch_action)."""
    _ensure_reactor_instances(state)
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return ActionResult(False, "Такого реактора нет.")
    building_id = instance["building_id"]
    building_name = BUILDINGS[building_id]["name"]
    defs = building_upgrade_defs(building_id)
    upgrade = next((u for u in defs if u["id"] == upgrade_id), None)
    if upgrade is None:
        return ActionResult(False, "Такого улучшения не существует.")
    times = reactor_instance_upgrade_purchase_count(state, instance_id, upgrade_id)
    repeatable = upgrade.get("repeatable", False)
    if not repeatable and times > 0:
        return ActionResult(False, "Уже куплено.")
    max_purchases = upgrade.get("max_purchases")
    if repeatable and max_purchases is not None and times >= max_purchases:
        return ActionResult(False, "Куплено максимальное число раз.")
    req_tech = upgrade.get("requires_tech")
    if req_tech and req_tech not in state.techs:
        return ActionResult(False, "Не выполнены условия для покупки улучшения.")
    is_zoning = upgrade["type"] == "reactor_zoning"
    if not is_zoning:
        prerequisite = building_upgrade_prerequisite(building_id, upgrade_id)
        if prerequisite is not None and prerequisite["id"] not in instance["upgrades"]:
            return ActionResult(False, f"Сначала нужно купить «{prerequisite['name']}».")
    cost = repeatable_upgrade_cost(upgrade, times)
    deferred = _pay_or_defer(
        state, cost, f"улучшение «{upgrade['name']}» для «{building_name}»",
        "reactor_instance_upgrade", f"{instance_id}:{upgrade_id}",
    )
    if deferred is not None:
        return deferred
    if is_zoning:
        zoning_ids = {u["id"] for u in defs if u["type"] == "reactor_zoning"}
        instance["upgrades"] = [uid for uid in instance["upgrades"] if uid not in zoning_ids]
    instance["upgrades"].append(upgrade_id)
    _ensure_reactor_grid(state, instance_id)
    _ensure_reactor_zones(state, instance_id)
    suffix = f" (куплено {times + 1}-й раз)" if repeatable else ""
    return ActionResult(True, f"Улучшение «{upgrade['name']}» куплено для «{building_name}»{suffix}.")


def broken_buildings(state: GameState) -> list[str]:
    return [b for b, count in state.broken.items() if count > 0]


def _roll_breakdown_type(building_id: str) -> str | None:
    """Вид поломки, случайно выбранный из buildings.*.breakdown_types этой
    постройки (равновероятно) - None, если список не задан/пуст (старое
    поведение: поломка без типа, ремонт не требует никакого инструмента)."""
    types = BUILDINGS[building_id].get("breakdown_types")
    return random.choice(types) if types else None


def breakdown_type_required_tools(type_id: str | None) -> list[str]:
    if type_id is None:
        return []
    return BREAKDOWN_TYPES.get(type_id, {}).get("required_tools", [])


def _is_breakdown_type_fixable(state: GameState, type_id: str | None) -> bool:
    """Можно ли ПРЯМО СЕЙЧАС починить поломку этого вида - True для None (без
    типа, инструмент не нужен), иначе требует владения (и исправности) ВСЕХ
    инструментов из required_tools сразу (см. tool_usable_for_repair)."""
    return all(tool_usable_for_repair(state, t) for t in breakdown_type_required_tools(type_id))


def _broken_type_entries(state: GameState, building_id: str) -> list[str | None]:
    """Список видов поломки на каждую СЕЙЧАС сломанную копию building_id (не
    для ферм - см. repairable_count/repair) - state.broken_types[bid],
    добитый/обрезанный до длины state.broken[bid] значениями None (защита от
    старых сохранений и от контента, у которого breakdown_types появился
    только что - в обоих случаях считаем недостающие поломки "без типа")."""
    count = state.broken.get(building_id, 0)
    types = state.broken_types.get(building_id, [])
    if len(types) < count:
        types = types + [None] * (count - len(types))
    else:
        types = types[:count]
    return types


def repairable_count(state: GameState, building_id: str) -> int:
    """Сколько из СЕЙЧАС сломанных копий этой постройки можно починить прямо
    сейчас (хватает нужных инструментов на их вид поломки) - используется и
    ценой ремонта (repair_cost_total), и самим repair()."""
    if _uses_physical_instances(building_id):
        _ensure_physical_instances(state)
        return sum(
            1 for inst in _physical_instances(state, building_id).values()
            if inst["building_id"] == building_id and inst["broken"]
            and _is_breakdown_type_fixable(state, inst.get("broken_type"))
        )
    return sum(1 for t in _broken_type_entries(state, building_id) if _is_breakdown_type_fixable(state, t))


def repair_cost_total(state: GameState, building_id: str) -> dict[str, int]:
    """Цена ремонта ПРЯМО СЕЙЧАС - только за копии, которые реально можно
    починить (см. repairable_count), не за все сломанные - копии, чей вид
    поломки требует недостающий инструмент, в эту цену не входят вовсе."""
    fixable = repairable_count(state, building_id)
    per_unit = BUILDINGS[building_id]["repair_cost"]
    return {r: amount * fixable for r, amount in per_unit.items()}


def repair_blocked_types(state: GameState, building_id: str) -> list[tuple[str | None, int]]:
    """Виды поломки СЕЙЧАС сломанной постройки, которые repair() не может
    починить (не хватает инструмента) - список (type_id, сколько сломанных
    копий этого вида) для подсказки в UI рядом с кнопкой ремонта (см.
    tools/web_ui.py:repair_blocked_view). Порядок - по первому появлению вида."""
    if _uses_physical_instances(building_id):
        _ensure_physical_instances(state)
        types = [
            inst.get("broken_type") for inst in _physical_instances(state, building_id).values()
            if inst["building_id"] == building_id and inst["broken"]
        ]
    else:
        types = _broken_type_entries(state, building_id)
    counts: dict[str | None, int] = {}
    for t in types:
        if not _is_breakdown_type_fixable(state, t):
            counts[t] = counts.get(t, 0) + 1
    return list(counts.items())


def energy_breakdown(state: GameState, include_surplus_only: bool = True) -> tuple[int, int, int]:
    """(запас, генерация/ход, потребление/ход) энергии в ЭТОТ ХОД - всё, что работает
    само (passive_flows): постройки, реакторы, особи, души, ритуалы. Постройки не в
    своё время суток (солнечные панели ночью) или выключенные ползунком "Механизмы"
    не считаются, иначе цифры обещали бы больше, чем даст следующий advance_turn.
    include_surplus_only=False - без потребления построек «только на излишках»
    (energy_surplus_only): ожиданию энергии они уступают, мешать ему не должны.
    Преобразователи и репликаторы берут только излишки - в потребление не входят."""
    produced, consumed = passive_flows(state, include_surplus_only)
    return (
        state.resources.get(ENERGY_RESOURCE_ID, 0),
        produced.get(ENERGY_RESOURCE_ID, 0),
        consumed.get(ENERGY_RESOURCE_ID, 0),
    )


def passive_flows(state: GameState, include_surplus_only: bool = True) -> tuple[dict[str, float], dict[str, float]]:
    """Сколько ресурса за ход ПРОИЗВОДИТ и ПОТРЕБЛЯЕТ всё, что работает само, если
    всем хватает сырья: постройки, заселённые особи (с бонусом среды), вставленные
    души, реакторы (энергия и жидкость контура по прогнозу хода, тем же расчётом,
    что _process_reactors) и доп. расход идущих ритуалов. Шанс и диапазон - средним
    ожидаемым, округление - как при настоящем начислении. Для отображения
    (theoretical_rates/energy_breakdown), не обещание."""
    daytime = is_daytime(state)
    produced: dict[str, float] = {}
    consumed: dict[str, float] = {}

    def add(target: dict, resource_id: str, amount: float) -> None:
        if amount:
            target[resource_id] = target.get(resource_id, 0) + amount

    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0 or not is_building_active_now(building_id, daytime):
            continue
        if not include_surplus_only and BUILDINGS[building_id].get("energy_surplus_only"):
            continue
        active = building_active_count(state, building_id)
        if active <= 0:
            continue
        produces, consumes = effective_building_stats(building_id, state.techs, state.building_upgrades)
        # Диапазон (см. _is_range_amount) - серединой, прогноз не бросает заново.
        for r, amount in produces.items():
            add(produced, r, _amount_estimate(amount) * active)
        for r, amount in consumes.items():
            add(consumed, r, amount * active)

    # Особи - как _process_habitat.
    for creature in state.creatures.values():
        farm_id = creature["farm_instance_id"]
        instance = state.farm_instances.get(farm_id) if farm_id is not None else None
        if instance is None or instance["broken"]:
            continue
        phenotype = creature_phenotype(creature)
        if not _is_active_period_now(phenotype["active_period"], daytime):
            continue
        species = SPECIES.get(phenotype["species_id"], {})
        for r, amount in species.get("consumes", {}).items():
            add(consumed, r, amount)
        output_mult = phenotype["output_multiplier"] * _farm_instance_upgrade_multiplier(state, farm_id, "breeding_output")
        for r, amount in species.get("produces", {}).items():
            scaled = _amount_estimate(amount) * output_mult
            add(produced, r, scaled if RESOURCE_FRACTIONAL.get(r, False) else math.ceil(scaled))
        active_env = _instance_environment_id(state, farm_id)
        bonus = species.get("bonus_resource")
        if bonus and active_env is not None and phenotype["environment_id"] == active_env:
            amount = species.get("bonus_amount", 0)
            if not RESOURCE_FRACTIONAL.get(bonus, False):
                amount = math.ceil(amount)
            add(produced, bonus, amount * species.get("bonus_chance", 0))

    # Души - как _process_soul_collectors.
    for instance_id, instance in state.soul_collector_instances.items():
        if not instance.get("installed") or not is_building_active_now(instance["building_id"], daytime):
            continue
        capacity = soul_collector_instance_capacity(state, instance_id)
        efficiency = _soul_instance_upgrade_multiplier(state, instance_id, "soul_generation_efficiency", default=1.0)
        for soul_id in instance["installed"][:capacity]:
            recipe = SOUL_RECIPES.get(soul_id)
            if not recipe:
                continue
            produces = dict(recipe.get("produces", {}))
            if efficiency != 1.0:
                produces = _round_amounts({r: _scale_amount(v, efficiency) for r, v in produces.items()})
            chances = recipe.get("produces_chance") or {}
            for r, amount in produces.items():
                add(produced, r, _amount_estimate(amount) * chances.get(r, 1.0))

    # Реакторы - прогноз хода; жидкость контура общая на все реакторы по порядку.
    fluid_left: dict[str, float] = {}
    for instance_id, instance in state.reactor_instances.items():
        if instance["broken"] or not is_building_active_now(instance["building_id"], daytime):
            continue
        fluid_id = instance.get("coolant_fluid")
        stock = fluid_left.get(fluid_id, state.resources.get(fluid_id, 0)) if fluid_id else 0
        zones = _reactor_zone_forecast(state, instance_id, fluid_stock=stock) or []
        add(produced, ENERGY_RESOURCE_ID, sum(z["next_output"] for z in zones))
        used = sum(z["fluid_used"] for z in zones)
        if fluid_id:
            fluid_left[fluid_id] = stock - used
            add(consumed, fluid_id, used)

    # Доп. расход идущих ритуалов - как _process_ritual_jobs.
    for job in state.ritual_jobs.values():
        chances = job.get("extra_consumes_chance") or {}
        for r, amount in (job.get("extra_consumes_per_turn") or {}).items():
            add(consumed, r, amount * chances.get(r, 1.0))
    return produced, consumed


def energy_capacity(state: GameState) -> int:
    """Максимальный запас энергии, который можно накопить - сумма ёмкости
    (поле energy_storage) всех РАБОТАЮЩИХ построек-хранилищ. Без хранилищ равна 0:
    энергия по-прежнему может тратиться на обслуживание других механизмов в течение
    хода, но не переносится на следующий - в отличие от остальных ресурсов."""
    capacity = 0
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        capacity += BUILDINGS[building_id].get("energy_storage", 0) * working
    return capacity


def energy_converter_building_types(state: GameState) -> list[str]:
    """id всех РАБОТАЮЩИХ типов построек с полем energy_converter (порядок - как в
    BUILDINGS/content.json) - несколько разных ТИПОВ построек могут вести крафт за
    энергию одновременно (например, "Преобразователь" и "Генератор материи"), у
    каждого своя, независимая пропускная способность (см.
    energy_converter_max_energy_per_turn) и свой пул слотов (energy_converter_capacity)."""
    return [
        bid for bid in BUILDINGS
        if BUILDINGS[bid].get("energy_converter") and working_count(state, bid) > 0
    ]


def energy_converter_capacity(state: GameState, building_id: str | None = None) -> int:
    """Сколько крафтов за энергию можно вести ОДНОВРЕМЕННО. С конкретным building_id -
    слоты ТОЛЬКО этого типа построек (РАБОТАЮЩИЕ копии, по одному слоту на копию).
    Без building_id (None) - сумма слотов ПО ВСЕМ типам с energy_converter разом
    (нужно там, где важно только "есть ли вообще хоть один свободный слот где-нибудь" -
    например, _ensure_available решает, годится ли рецепт БЕЗ привязки к типу
    (machine=null) вообще хоть куда-то). Считается так же, как compute_capacity/
    energy_capacity."""
    if building_id is not None:
        if not BUILDINGS.get(building_id, {}).get("energy_converter"):
            return 0
        return working_count(state, building_id)
    capacity = 0
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        if BUILDINGS[building_id].get("energy_converter"):
            capacity += working
    return capacity


def energy_converter_max_energy_per_turn(state: GameState, building_id: str) -> int | None:
    """Потолок энергии за ход НА ОДИН преобразователь ТИПА building_id (одну
    работающую копию) - это ЛИЧНЫЙ лимит каждого крафта в state.energy_jobs,
    ведущегося именно в постройке этого типа, не общий на все типы сразу (иначе
    первый в списке крафт мог бы забрать весь бюджет и оставить второй без энергии
    даже при двух свободных преобразователях - это свело бы "параллельно" обратно
    к "последовательно" с более широким общим лимитом). БАЗОВОЕ значение -
    buildings[building_id]["energy_converter"]["max_energy_per_turn"], ПЛЮС бонус
    от купленных улучшений ЭТОЙ ЖЕ постройки типа "energy_converter_capacity" (см.
    _farm_upgrade_multiplier - тот же принцип "среди купленных одного типа действует
    только самое сильное", value каждого уровня - АБСОЛЮТНАЯ добавка к базе, уровни
    не складываются друг с другом). Раньше пропускную способность растили отдельной
    постройкой-усилителем (generic-поле energy_converter_boost) - убрано в пользу
    апгрейдов, чтобы автор контента гибко назначал число уровней и их цену прямо в
    редакторе, без отдельной постройки-костыля. None у базового значения = лимита
    нет вообще (заберёт сколько есть в общем пуле) - бонус тогда ни на что не
    влияет, складывать его не с чем. building_id, у которого вообще нет работающих
    копий или нет поля energy_converter, - 0 (не None), чтобы не путать "лимит есть,
    но нулевой" с "лимита нет вовсе" - но вызывающий обычно и так не спрашивает про
    несуществующий/непостроенный тип."""
    if working_count(state, building_id) <= 0:
        return 0
    conv = BUILDINGS[building_id].get("energy_converter")
    if not conv:
        return 0
    base = conv.get("max_energy_per_turn")
    if base is None:
        return None
    bonus = _farm_upgrade_multiplier(state, building_id, "energy_converter_capacity", default=0)
    return base + bonus


def available_energy_recipes(state: GameState) -> list[str]:
    """Рецепты крафта за энергию, доступные для запуска игроком/автокрафтом прямо
    сейчас - открытые технологией (requires_tech рецепта, если он указан)."""
    return [rid for rid in ENERGY_RECIPES if is_energy_recipe_unlocked(rid, state)]


def energy_jobs_view(state: GameState) -> list[dict]:
    """Активные крафты за энергию для UI (панель "Крафт из энергии" рядом с
    Автокрафтом) - рецепт, прогресс и порог каждой ведущейся сейчас задачи.
    "index" - позиция в state.energy_jobs (см. cancel_energy_job) - state.energy_jobs
    это СПИСОК (несколько задач могут вести один и тот же recipe_id параллельно в
    разных преобразователях), поэтому адресовать задачу можно только по позиции,
    не по id рецепта."""
    jobs = []
    for index, job in enumerate(state.energy_jobs):
        recipe = ENERGY_RECIPES[job["recipe_id"]]
        jobs.append({
            "index": index,
            "recipe_id": job["recipe_id"],
            "name": recipe["name"],
            "progress": job["progress"],
            "threshold": recipe["threshold"],
            "produces": dict(recipe.get("produces", {})),
            "building_id": job.get("building_id"),
        })
    return jobs


def cancel_energy_job(state: GameState, index: int) -> ActionResult:
    """Останавливает крафт за энергию немедленно, БЕЗ траты хода, и освобождает
    преобразователь - чтобы игрок, запустивший слишком дорогой по энергии рецепт, не
    был обязан ждать его окончания. Вся энергия, что уже вложена в job["progress"],
    НЕ возвращается (она уже потрачена по-настоящему - см. _process_energy_jobs).
    Материальная цена рецепта (consumes, списана при запуске) возвращается целиком,
    как cost у cancel_manual_job; партия заказа возвращает её в резерв своего заказа -
    иначе заказ перезапускал бы партию без сырья и висел молча."""
    if not isinstance(index, int) or not (0 <= index < len(state.energy_jobs)):
        return ActionResult(False, "Такого крафта за энергию уже нет.")
    job = state.energy_jobs.pop(index)
    recipe = ENERGY_RECIPES[job["recipe_id"]]
    # "cost" - материалы, списанные при запуске (нет у рецепта без consumes).
    refund = job.get("cost", {})
    order = _find_order(state, job.get("order_id")) if job.get("order_id") is not None else None
    _gain(order["reserved"] if order is not None else state.resources, refund)
    suffix = " Материалы возвращены." if refund else ""
    return ActionResult(True, f"Крафт за энергию «{recipe['name']}» отменён.{suffix}")


def start_energy_recipe(state: GameState, recipe_id: str) -> ActionResult:
    """Запускает рецепт вручную (локация "Преобразователь", CONVERTER_LOCATION_ID) в
    свободном энергетическом преобразователе. Один преобразователь ведёт РОВНО один
    рецепт за раз - если все преобразователи заняты, новый крафт запустить нельзя,
    пока какой-то из них не закончит текущий (см. _process_energy_jobs). Второй
    построенный преобразователь даёт второй одновременный слот - тогда можно вести
    два разных рецепта параллельно.

    Если у рецепта задан machine (content.json energy_recipes.*.machine), он может
    вестись ТОЛЬКО в постройке этого типа; иначе подходит любой тип с energy_converter,
    первый найденный со свободным слотом (см. _pick_converter_building_with_free_slot -
    игрок не выбирает конкретный тип сам, тот же принцип "первый попавшийся", что
    раньше был у единственного типа). Материальная цена рецепта (consumes, если
    задана) оплачивается через _pay_or_defer - значит автокрафт/очередь отложенных
    действий работают и здесь, как у любой другой цены с ресурсами; энергия при
    этом НЕ оплачивается заранее - она копится в job["progress"] по ходам
    (см. _process_energy_jobs)."""
    if recipe_id not in ENERGY_RECIPES:
        return ActionResult(False, "Такого рецепта не существует.")
    recipe = ENERGY_RECIPES[recipe_id]
    if recipe_id not in available_energy_recipes(state):
        return ActionResult(False, "Этот рецепт ещё не изучен.")
    machine = recipe.get("machine")
    building_id = _pick_converter_building_with_free_slot(state, state.energy_jobs, machine)
    if building_id is None:
        if energy_converter_capacity(state, machine) <= 0:
            name = BUILDINGS[machine]["name"] if machine else None
            return ActionResult(
                False,
                f"Нет ни одной работающей постройки «{name}»." if name
                else "Нет ни одного работающего энергетического преобразователя.",
            )
        return ActionResult(False, "Все преобразователи этого типа заняты - дождись завершения текущего крафта.")
    consumes = recipe.get("consumes", {})
    if consumes:
        deferred = _pay_or_defer(state, consumes, f"крафт «{recipe['name']}»", "start_energy_recipe", recipe_id)
        if deferred is not None:
            return deferred
    job = {"recipe_id": recipe_id, "progress": 0, "building_id": building_id}
    if consumes:
        job["cost"] = dict(consumes)  # вернуть при отмене (cancel_energy_job)
    state.energy_jobs.append(job)
    return ActionResult(True, f"Запущен крафт за энергию: {recipe['name']}.")


# ==================== Репликатор (buildings.*.replicator) ====================
# Постройка с полем replicator {matter, max_matter_per_turn, slots} делает ЛЮБОЙ
# ресурс с ценой репликации (resources.*.replication_cost - материи за ОДНУ единицу,
# может быть дробной) из одной и той же материи - это не рецепт "вход -> выход", а
# одна валюта на много выходов. Игрок выбирает ресурс и количество, материя
# списывается ЦЕЛИКОМ при запуске (как cost многоходового действия), затем
# репликация перерабатывает её не быстрее лимита за ход и выдаёт ресурс по мере
# переработки (_process_replicator_jobs). Автокрафт делает ресурс репликатором,
# только если у репликатора куплена «Автоматизация» (_autocraft_replicator_building).


def replicator_building_types(state: GameState) -> list[str]:
    """id РАБОТАЮЩИХ типов построек с полем replicator (порядок - как в content.json)."""
    return [bid for bid in BUILDINGS if BUILDINGS[bid].get("replicator") and working_count(state, bid) > 0]


def replicator_matter(building_id: str) -> str | None:
    """Ресурс-материя, из которой реплицирует постройка (replicator.matter)."""
    return (BUILDINGS.get(building_id, {}).get("replicator") or {}).get("matter")


def replicable_resources() -> list[str]:
    """Ресурсы с ценой репликации, в порядке content.json."""
    return [rid for rid in RESOURCE_NAMES if rid in RESOURCE_REPLICATION_COST]


def replication_matter_cost(resource_id: str, qty: int | float, matter_id: str | None = None) -> float:
    """Сколько материи стоят qty единиц resource_id (qty * replication_cost). Дробная
    материя (как энергия) - как есть; целая - вверх (тяжелее для игрока, см. _round_amounts)."""
    cost = qty * RESOURCE_REPLICATION_COST[resource_id]
    if matter_id is not None and not RESOURCE_FRACTIONAL.get(matter_id, False):
        return math.ceil(cost - 1e-9)
    return round(cost, 9)


def replicator_slot_capacity(state: GameState, building_id: str) -> int:
    """Сколько репликаций тип ведёт одновременно: replicator.slots (по умолчанию 1) на
    КАЖДУЮ работающую копию."""
    spec = BUILDINGS.get(building_id, {}).get("replicator")
    if not spec:
        return 0
    return working_count(state, building_id) * int(spec.get("slots") or 1)


def replicator_max_matter_per_turn(state: GameState, building_id: str) -> float | None:
    """Лимит переработки материи за ход ОДНОЙ репликации в постройке этого типа: база
    replicator.max_matter_per_turn плюс апгрейды ТОЙ ЖЕ постройки типа
    replicator_throughput (как energy_converter_max_energy_per_turn: value - абсолютная
    добавка, среди купленных действует сильнейший, repeatable складываются). None - без
    лимита; 0 - постройки нет или не работает."""
    spec = BUILDINGS.get(building_id, {}).get("replicator")
    if not spec or working_count(state, building_id) <= 0:
        return 0
    base = spec.get("max_matter_per_turn")
    if base is None:
        return None
    return base + _farm_upgrade_multiplier(state, building_id, "replicator_throughput", default=0)


def replication_turns(state: GameState, building_id: str, matter_cost: float) -> int:
    """За сколько ходов ОДНА репликация переработает matter_cost материи (минимум 1)."""
    per_turn = replicator_max_matter_per_turn(state, building_id)
    if not per_turn or matter_cost <= 0:
        return 1
    return max(1, math.ceil(matter_cost / per_turn - 1e-9))


def _pick_replicator_with_free_slot(state: GameState, automated_only: bool = False) -> str | None:
    """Первый тип репликатора со свободным слотом (порядок content.json). automated_only -
    только с «Автоматизацией» (для заказов автокрафта)."""
    for building_id in replicator_building_types(state):
        if automated_only and not has_autocraft_upgrade(state, building_id):
            continue
        running = sum(1 for j in state.replicator_jobs if j["building_id"] == building_id)
        if running < replicator_slot_capacity(state, building_id):
            return building_id
    return None


def replication_storage_free(state: GameState, resource_id: str) -> float | None:
    """Сколько ещё единиц resource_id можно заказать репликатору вручную, чтобы всё
    поместилось на склад (вместимость минус запас минус ещё не выданное идущими
    репликациями игрока) - иначе излишек сгорел бы в конце хода вместе с уплаченной
    материей. None - вместимость не ограничена."""
    cap = storage_capacity(state, resource_id)
    if cap is None:
        return None
    pending = sum(
        j["qty"] - j["delivered"] for j in state.replicator_jobs
        if j["resource_id"] == resource_id and j.get("order_id") is None
    )
    return cap - state.resources.get(resource_id, 0) - pending


def _new_replicator_job(
    state: GameState, building_id: str, resource_id: str, qty: int | float, cost: float, **extra,
) -> dict:
    job = {
        "id": max((j["id"] for j in state.replicator_jobs), default=0) + 1,
        "building_id": building_id, "resource_id": resource_id, "qty": qty,
        "cost": cost, "progress": 0, "delivered": 0,
    }
    job.update(extra)
    state.replicator_jobs.append(job)
    return job


def replicate(state: GameState, resource_id: str, qty) -> ActionResult:
    """Запускает репликацию qty единиц resource_id в свободном репликаторе: материя
    (replicator.matter первого типа со свободным слотом) списывается целиком сразу,
    ресурс приходит по мере переработки (_process_replicator_jobs; уложилось в лимит
    хода - придёт в этот же ход). Ход тратит вызывающий (как запуск крафта за энергию).
    Материю автокрафт не докрафчивает - её только копят генераторы."""
    if resource_id not in RESOURCE_REPLICATION_COST:
        return ActionResult(False, "Этот ресурс репликатор сделать не может.")
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return ActionResult(False, "Количество должно быть целым числом.")
    if qty < 1:
        return ActionResult(False, "Количество должно быть не меньше 1.")
    qty = min(qty, MAX_RESOURCE_AMOUNT)
    if not replicator_building_types(state):
        return ActionResult(False, "Нет ни одного работающего репликатора.")
    building_id = _pick_replicator_with_free_slot(state)
    if building_id is None:
        return ActionResult(False, "Все репликаторы заняты - дождись конца текущей репликации или отмени её.")
    free = replication_storage_free(state, resource_id)
    if free is not None and qty > free:
        return ActionResult(
            False, f"На складе нет места: «{RESOURCE_NAMES[resource_id]}» поместится ещё {_format_amount(max(0, free))}.",
        )
    matter = replicator_matter(building_id)
    cost = replication_matter_cost(resource_id, qty, matter)
    have = state.resources.get(matter, 0)
    if have + 1e-9 < cost:
        return ActionResult(False, f"Не хватает материи. Не хватает: {_format_shortfall({matter: cost - have})}.")
    state.resources[matter] = max(0, round(have - cost, 9))
    _new_replicator_job(state, building_id, resource_id, qty, cost)
    turns = replication_turns(state, building_id, cost)
    message = (
        f"Репликация запущена: {RESOURCE_NAMES[resource_id]} ×{qty} "
        f"за {_format_amount(cost)} ({RESOURCE_NAMES.get(matter, matter)})"
    )
    message += f" - займёт {turns} х." if turns > 1 else "."
    return ActionResult(True, message)


def _process_replicator_jobs(state: GameState, report: ProductionReport) -> None:
    """Каждая репликация (state.replicator_jobs, по порядку запуска) перерабатывает не
    больше лимита своего типа за ход (replicator_max_matter_per_turn) и выдаёт столько
    ресурса, сколько уже переработано (партия заказа - в его резерв, см.
    _deliver_job_output). Материя списана при запуске - поэтому энергия и чужие
    потребители тут ни при чём. Репликаций типа больше, чем его слотов (копия
    сломалась), - лишние ждут, по порядку запуска."""
    if not state.replicator_jobs:
        return
    busy: dict[str, int] = {}
    remaining = []
    for job in state.replicator_jobs:
        building_id = job["building_id"]
        if busy.get(building_id, 0) >= replicator_slot_capacity(state, building_id):
            remaining.append(job)
            continue
        busy[building_id] = busy.get(building_id, 0) + 1
        per_turn = replicator_max_matter_per_turn(state, building_id)
        left = job["cost"] - job["progress"]
        job["progress"] = round(job["progress"] + (left if per_turn is None else min(per_turn, left)), 9)
        if job["cost"] <= 0 or job["progress"] >= job["cost"] - 1e-9:
            ready = job["qty"]
        else:
            ready = int(job["qty"] * job["progress"] / job["cost"] + 1e-9)
        fresh = ready - job["delivered"]
        if fresh > 0:
            job["delivered"] = ready
            _deliver_job_output(state, job, {job["resource_id"]: fresh})
            report.replicated[job["resource_id"]] = report.replicated.get(job["resource_id"], 0) + fresh
        if job["delivered"] < job["qty"]:
            remaining.append(job)
    state.replicator_jobs = remaining


def cancel_replicator_job(state: GameState, job_id) -> ActionResult:
    """Останавливает репликацию немедленно, БЕЗ траты хода, и возвращает НЕпереработанную
    материю (как cancel_manual_job возвращает cost; уже выданный ресурс остаётся у
    игрока). Партия заказа возвращает материю в резерв своего заказа - заказ перезапустит
    остаток сам."""
    job = next((j for j in state.replicator_jobs if j["id"] == job_id), None)
    if job is None:
        return ActionResult(False, "Такой репликации уже нет.")
    state.replicator_jobs.remove(job)
    matter = replicator_matter(job["building_id"])
    refund = max(0, job["cost"] - job["progress"])
    if matter is not None and not RESOURCE_FRACTIONAL.get(matter, False):
        refund = math.floor(refund + 1e-9)
    if matter is not None and refund > 0:
        order = _find_order(state, job.get("order_id")) if job.get("order_id") is not None else None
        _add_resource(order["reserved"] if order is not None else state.resources, matter, round(refund, 9))
    name = RESOURCE_NAMES.get(job["resource_id"], job["resource_id"])
    return ActionResult(True, f"Репликация «{name}» отменена - вернулось {_format_amount(refund)} материи.")


def replicator_jobs_view(state: GameState) -> list[dict]:
    """Идущие репликации для UI (модалка репликатора): сколько выдано из скольких,
    переработано материи из скольких, лимит за ход и оценка оставшихся ходов."""
    view = []
    for job in state.replicator_jobs:
        per_turn = replicator_max_matter_per_turn(state, job["building_id"])
        view.append({
            "id": job["id"], "building_id": job["building_id"], "resource_id": job["resource_id"],
            "qty": job["qty"], "delivered": job["delivered"],
            "cost": _format_amount(job["cost"]), "progress": _format_amount(job["progress"]),
            "per_turn": None if per_turn is None else _format_amount(per_turn),
            "turns_left": replication_turns(state, job["building_id"], job["cost"] - job["progress"]),
            "order": job.get("order_id") is not None,
        })
    return view


def _phenotype_allele(trait_id: str, pair: list[str]) -> str:
    """Выражающаяся (доминантная) аллель пары по признаку trait_id - если обе
    доминантны или обе рецессивны, побеждает первая (детерминированно; порядок
    в паре сам по себе смысла не несёт, кроме этого правила)."""
    alleles = trait_alleles(trait_id)
    a, b = pair[0], pair[1]
    if alleles.get(b, {}).get("dominant", False) and not alleles.get(a, {}).get("dominant", False):
        return b
    return a


def _genotype_pair(genotype: dict, trait_id: str) -> list[str]:
    """Пара аллелей признака trait_id из генотипа особи - с запасным вариантом для
    признака, добавленного ПОСЛЕ того, как в игре уже могли быть особи (сейчас -
    "environment"): старое сохранение без этого ключа получает детерминированную
    гомозиготную пару из доминантной (или первой по порядку) аллели вместо
    падения с KeyError."""
    pair = genotype.get(trait_id)
    if pair is not None:
        return pair
    alleles = trait_alleles(trait_id)
    fallback = next((aid for aid, a in alleles.items() if a.get("dominant")), next(iter(alleles), None))
    return [fallback, fallback]


def creature_phenotype(creature: dict) -> dict:
    """Выражающиеся значения всех 5 признаков особи - {"species_id", "lifespan",
    "output_multiplier", "active_period", "environment_id"}."""
    genotype = creature["genotype"]
    species_id = _phenotype_allele("species", genotype["species"])
    lifespan_id = _phenotype_allele("lifespan", genotype["lifespan"])
    output_id = _phenotype_allele("output_multiplier", genotype["output_multiplier"])
    active_id = _phenotype_allele("active_period", genotype["active_period"])
    environment_id = _phenotype_allele("environment", _genotype_pair(genotype, "environment"))
    return {
        "species_id": species_id,
        "lifespan": trait_alleles("lifespan").get(lifespan_id, {}).get("value", 1),
        "output_multiplier": trait_alleles("output_multiplier").get(output_id, {}).get("value", 1),
        "active_period": active_id,
        "environment_id": environment_id,
    }


def species_gene_pool(species_id: str, trait_id: str) -> dict[str, float]:
    """allele_id -> вес: какие аллели признака trait_id несёт от природы особь
    вида species_id (species.*.gene_pool) - у каждого вида свой набор генов
    (прямое пожелание, 2026-09-27: раньше любая дикая особь могла нести ЛЮБУЮ
    аллель, и лучшие гены собирались с самых обычных пчёл; теперь ради гена
    нужно добыть вид, который его носит). Пул читают дикий улов и мутация
    (_roll_wild_genotype/_cross_genotype); синтез - нет, он берёт уже изученное.
    Аллели, которых нет в genetic_traits, и веса <= 0 отбрасываются. Пула нет
    (валидатор его требует, так что только старый/недописанный контент) -
    доминантные аллели признака, «обычная особь»."""
    alleles = trait_alleles(trait_id)
    pool = (SPECIES.get(species_id, {}).get("gene_pool") or {}).get(trait_id) or {}
    result = {aid: weight for aid, weight in pool.items() if aid in alleles and weight > 0}
    if result:
        return result
    return {aid: 1 for aid, a in alleles.items() if a.get("dominant")} or {aid: 1 for aid in alleles}


def _roll_pool_allele(species_id: str, trait_id: str) -> str:
    pool = species_gene_pool(species_id, trait_id)
    ids = list(pool)
    return random.choices(ids, weights=[pool[a] for a in ids], k=1)[0]


def _roll_wild_genotype(species_id: str) -> dict:
    """Дикая особь ГОМОЗИГОТНА по построению на каждый признак (обе аллели пары
    одинаковы, взвешенно розыгранные по пулу генов её вида - species_gene_pool) -
    без иллюзии скрытых генов у только что пойманной особи; разнообразие
    появляется через скрещивание/мутацию."""
    genotype: dict[str, list[str]] = {}
    for trait_id in GENOTYPE_TRAIT_IDS:
        if trait_id == "species":
            genotype[trait_id] = [species_id, species_id]
            continue
        chosen = _roll_pool_allele(species_id, trait_id)
        genotype[trait_id] = [chosen, chosen]
    return genotype


def _farm_upgrade_multiplier(state: GameState, building_id: str, upgrade_type: str, default: float = 1.0) -> float:
    """Среди КУПЛЕННЫХ апгрейдов building_id заданного upgrade_type действует
    только САМОЕ БОЛЬШОЕ value - тот же принцип "сильнейшее побеждает", что у
    _apply_building_upgrades, но для типов апгрейдов вне трёх жёстко зашитых там
    (не про produces/consumes постройки целиком - фермы про особей внутри них,
    energy_converter_capacity про лимит энергии/ход, см.
    energy_converter_max_energy_per_turn). Название по историческому первому
    применению (фермы), но не привязано к ним."""
    purchased = state.building_upgrades.get(building_id, [])
    values = [
        _purchased_upgrade_value(purchased, u)
        for u in BUILDINGS.get(building_id, {}).get("upgrades", [])
        if u["type"] == upgrade_type
    ]
    values = [v for v in values if v is not None]
    return max(values) if values else default


def _farm_has_upgrade(state: GameState, building_id: str, upgrade_type: str) -> bool:
    purchased = state.building_upgrades.get(building_id, [])
    defs = {u["id"]: u for u in BUILDINGS.get(building_id, {}).get("upgrades", [])}
    return any(uid in defs and defs[uid]["type"] == upgrade_type for uid in purchased)


def _farm_instance_upgrade_multiplier(state: GameState, instance_id: str, upgrade_type: str, default: float = 1.0) -> float:
    """Тот же принцип "сильнейшее купленное побеждает", что и у
    _farm_upgrade_multiplier, но апгрейды читаются с ОДНОГО экземпляра фермы
    (state.farm_instances[instance_id]["upgrades"]), а не со всего типа постройки
    (state.building_upgrades[building_id]) - см. CLAUDE.md, "Пасеки: индивидуальные
    экземпляры"."""
    instance = state.farm_instances.get(instance_id)
    if instance is None:
        return default
    purchased = instance["upgrades"]
    values = [
        _purchased_upgrade_value(purchased, u)
        for u in BUILDINGS.get(instance["building_id"], {}).get("upgrades", [])
        if u["type"] == upgrade_type
    ]
    values = [v for v in values if v is not None]
    return max(values) if values else default


def _farm_instance_has_upgrade(state: GameState, instance_id: str, upgrade_type: str) -> bool:
    instance = state.farm_instances.get(instance_id)
    if instance is None:
        return False
    defs = {u["id"]: u for u in BUILDINGS.get(instance["building_id"], {}).get("upgrades", [])}
    return any(uid in defs and defs[uid]["type"] == upgrade_type for uid in instance["upgrades"])


def _soul_instance_upgrade_multiplier(state: GameState, instance_id: str, upgrade_type: str, default: float = 1.0) -> float:
    """Зеркало _farm_instance_upgrade_multiplier для экземпляров синтезатора
    душ (state.soul_collector_instances[instance_id]["upgrades"])."""
    instance = state.soul_collector_instances.get(instance_id)
    if instance is None:
        return default
    purchased = instance["upgrades"]
    values = [
        _purchased_upgrade_value(purchased, u)
        for u in BUILDINGS.get(instance["building_id"], {}).get("upgrades", [])
        if u["type"] == upgrade_type
    ]
    values = [v for v in values if v is not None]
    return max(values) if values else default


def _instance_environment_id(state: GameState, instance_id: str) -> str | None:
    """id среды (аллели genetic_traits.environment), которую эмулирует ЭТОТ
    экземпляр фермы прямо сейчас - value единственного (по дизайну; апгрейды типа
    environment_emulation взаимоисключающие, см. purchase_farm_instance_upgrade)
    купленного апгрейда типа environment_emulation, или None, если такого нет."""
    instance = state.farm_instances.get(instance_id)
    if instance is None:
        return None
    defs = {u["id"]: u for u in BUILDINGS.get(instance["building_id"], {}).get("upgrades", [])}
    for uid in instance["upgrades"]:
        upgrade = defs.get(uid)
        if upgrade and upgrade["type"] == "environment_emulation":
            return upgrade["value"]
    return None


def _register_known_alleles(state: GameState, genotype: dict) -> None:
    for trait_id, pair in genotype.items():
        known = state.known_alleles.setdefault(trait_id, [])
        for allele_id in pair:
            if allele_id not in known:
                known.append(allele_id)


def spawn_creature(state: GameState, genotype: dict, identified: bool) -> str:
    """Единая точка создания особи (дикий улов/мутация/синтез) - генерирует id
    (state.next_creature_id), кладёт в state.creatures, регистрирует
    фенотип-вид в species_discovered (коллекционные достижения) и, если особь
    сразу опознана (синтез), пополняет known_alleles."""
    creature_id = f"creature_{state.next_creature_id}"
    state.next_creature_id += 1
    species_id = _phenotype_allele("species", genotype["species"])
    state.creatures[creature_id] = {
        "species_id": species_id, "genotype": genotype,
        "farm_instance_id": None, "turns_alive": 0, "identified": identified,
    }
    state.species_discovered.add(species_id)
    if identified:
        _register_known_alleles(state, genotype)
    return creature_id


def genetics_lab_available(state: GameState) -> bool:
    """Есть ли работающая копия постройки с generic-полем genetics_lab (НЕ
    хардкод id - тот же принцип, что energy_converter/breeding_slots)."""
    return any(BUILDINGS[b].get("genetics_lab") and working_count(state, b) > 0 for b in state.buildings)


def species_family(species_id: str) -> str | None:
    """Тег "семейства" вида (species.*.family, например "bee"/"flower") -
    свободная строка, не фиксированный enum (тот же принцип, что id сред у
    environment_emulation): новый вид того же семейства ничего не требует от
    построек - достаточно проставить ему тот же family, чтобы он стал
    поселяем в уже существующих фермах этого семейства."""
    return SPECIES.get(species_id, {}).get("family")


def breeding_accepts_family(building_id: str) -> str | None:
    """Семейство видов, которое можно поселить в ферме этого типа
    (buildings.*.breeding_accepts_family) - необязательное generic-
    ограничение: поле отсутствует значит без ограничений (любой вид можно
    поселить, старое поведение) - иначе пришлось бы задним числом трогать
    любой старый контент/сохранение с фермой без этого поля. Сравнение идёт
    по ОДНОМУ общему тегу семейства (species_family), а не по списку id видов -
    так добавление нового вида того же семейства (например, ещё одной породы
    пчёл) не требует правки уже существующих построек. Тот же приём (тег на
    ресурсе + тег на постройке вместо списка id) использует soul_collector -
    см. soul_kind/soul_collector_accepts_kind."""
    return BUILDINGS.get(building_id, {}).get("breeding_accepts_family")


def house_creature(state: GameState, creature_id: str, farm_instance_id: str) -> ActionResult:
    """Переселяет особь из банка в свободный слот КОНКРЕТНОГО ЭКЗЕМПЛЯРА фермы -
    БЕЗ траты хода (управление уже своим имуществом, как cancel_energy_job/
    toggle_autocraft)."""
    _ensure_farm_instances(state)
    creature = state.creatures.get(creature_id)
    if creature is None:
        return ActionResult(False, "Такой особи нет.")
    if creature["farm_instance_id"] is not None:
        return ActionResult(False, "Особь уже поселена в ферму.")
    instance = state.farm_instances.get(farm_instance_id)
    if instance is None or instance["broken"]:
        return ActionResult(False, "Это не ферма.")
    building_id = instance["building_id"]
    accepts_family = breeding_accepts_family(building_id)
    name = RESOURCE_NAMES.get(creature["species_id"], creature["species_id"])
    if accepts_family is not None and species_family(creature["species_id"]) != accepts_family:
        return ActionResult(False, f"«{name}» не подходит для «{BUILDINGS[building_id]['name']}».")
    if farm_instance_slots_used(state, farm_instance_id) >= farm_instance_slot_capacity(state, farm_instance_id):
        return ActionResult(False, "Все слоты этой фермы заняты.")
    # Возраст - свойство особи: переселение его не обнуляет (иначе «выселить и
    # заселить обратно» давало бы бессмертие без улучшения breeding_immortality).
    creature["farm_instance_id"] = farm_instance_id
    return ActionResult(True, f"«{name}» поселён(а) в «{BUILDINGS[building_id]['name']}».")


def release_creature(state: GameState, creature_id: str) -> ActionResult:
    """Выселяет особь обратно в банк. Не тратит ход."""
    creature = state.creatures.get(creature_id)
    if creature is None or creature["farm_instance_id"] is None:
        return ActionResult(False, "Эта особь не в ферме.")
    creature["farm_instance_id"] = None
    name = RESOURCE_NAMES.get(creature["species_id"], creature["species_id"])
    return ActionResult(True, f"«{name}» возвращён(а) в запас.")


def breeding_station_available(state: GameState) -> bool:
    """Есть ли работающая копия постройки-МЕХАНИЗМА с generic-полем
    breeding_station (НЕ хардкод id - тот же принцип, что genetics_lab) - именно
    она открывает саму ВОЗМОЖНОСТЬ скрещивания (см. breed_creatures) и несёт
    апгрейды типа breeding_mutation_chance (см. _breeding_mutation_multiplier).
    Фермы (breeding_slots > 0, apiary/electric_apiary) тут ни при чём - они
    только расселяют уже существующих особей и растят их breeding_output/
    breeding_immortality; где именно поселены (или не поселены - банк тоже
    годится) родители, для скрещивания не важно."""
    return any(BUILDINGS[b].get("breeding_station") and working_count(state, b) > 0 for b in state.buildings)


def _breeding_mutation_multiplier(state: GameState) -> float:
    """Множитель шанса мутации от улучшений ЛЮБОЙ РАБОТАЮЩЕЙ копии механизма
    скрещивания (BUILDINGS[bid]["breeding_station"]) - если построено несколько
    ТИПОВ такого механизма разом, действует самый сильный купленный апгрейд
    breeding_mutation_chance среди них (тот же принцип "сильнейшее побеждает",
    что _farm_upgrade_multiplier внутри одной постройки)."""
    best = 1.0
    for building_id, count in state.buildings.items():
        if count <= 0 or not BUILDINGS[building_id].get("breeding_station"):
            continue
        if working_count(state, building_id) <= 0:
            continue
        best = max(best, _farm_upgrade_multiplier(state, building_id, "breeding_mutation_chance"))
    return best


def _cross_genotype(state: GameState, pair_a: dict, pair_b: dict) -> tuple[dict, bool]:
    """Признаки lifespan/output_multiplier/active_period - обычное менделевское
    расщепление (по одной случайной аллели от каждого родителя независимо, без
    сцепления между признаками). Признак "species" решается ОТДЕЛЬНО и ВСЕГДА
    даёт ЧИСТОКРОВНОГО потомка (обе аллели пары одинаковы) - либо мутировавший
    третий вид, либо вид ОДНОГО из родителей, но никогда смесь (по прямому
    решению: "какая-то пчела в любом случае должна получиться", а не подвешенная
    гетерозигота, которую фенотип разрешает только в момент показа).

    Виды родителей берутся через ФЕНОТИП (_phenotype_allele), а не "нулевую"
    аллель пары напрямую - на новых особях это одно и то же (после этой же
    функции пара всегда гомозиготна), но старые сохранения могли донести
    гетерозиготную пару вида из версии игры до этого изменения, и для них
    правильный "вид, которым родитель СЕЙЧАС является" - именно фенотип.

    Для найденной по frozenset({species_a, species_b}) (см. MUTATIONS_BY_PAIR)
    группы ОТКРЫТЫХ (requires_tech/requires_building) мутаций - ОДИН общий
    random.random() размечает отрезок [0, 1): каждой мутации по порядку
    достаётся кусок длиной entry["chance"] * _breeding_mutation_multiplier(...),
    а весь остаток (1 - сумма шансов) делится ПОПОЛАМ между "чистокровный вид
    родителя A" и "чистокровный вид родителя Б" - т.е. на пару без единого
    применимого рецепта мутации родительские виды достаются игроку с шансом 50/50
    каждый, а не совпадают в 100% случаев с одним и тем же родителем. Если сумма
    шансов всех применимых мутаций >= 1, ветка "поровну между родителями"
    математически недостижима - это ожидаемо (мутация в такой конфигурации
    гарантирована), а не ошибка.

    Мутация приносит гены НОВОГО вида (как в Forestry): одна аллель каждой пары -
    из его пула (species_gene_pool), вторая - от ОДНОГО из родителей (монетка
    одна на весь геном). Иначе вид, который добывается только мутацией, никогда
    не дал бы своих генов. Аллель нового вида стоит в паре первой - при равной
    доминантности выражается она (_phenotype_allele).

    После основной генотипной части - отдельный шанс (genetics.environment_mutation_chance)
    ПОЛНОСТЬЮ переиграть пару признака "environment" заново (по пулу вида потомка,
    как у дикой особи - _roll_wild_genotype), поверх уже отыгранного наследования
    выше - редкая "мутация среды обитания", не связанная с мутацией вида."""
    species_a = _phenotype_allele("species", pair_a["species"])
    species_b = _phenotype_allele("species", pair_b["species"])
    mult = _breeding_mutation_multiplier(state)
    candidates = []
    for mutation in MUTATIONS_BY_PAIR.get(frozenset({species_a, species_b}), []):
        if mutation.get("requires_tech") and mutation["requires_tech"] not in state.techs:
            continue
        if mutation.get("requires_building") and working_count(state, mutation["requires_building"]) <= 0:
            continue
        candidates.append(mutation)

    roll = random.random()
    cursor = 0.0
    mutated_species = None
    for mutation in candidates:
        cursor += mutation["chance"] * mult
        if roll < cursor:
            outcomes = mutation["outcomes"]
            weights = [o["weight"] for o in outcomes]
            mutated_species = random.choices(outcomes, weights=weights, k=1)[0]["species_id"]
            break
    if mutated_species is not None:
        child_species = mutated_species
    else:
        child_species = species_a if roll < cursor + (1 - cursor) / 2 else species_b

    child: dict[str, list[str]] = {"species": [child_species, child_species]}
    kept_parent = random.choice((pair_a, pair_b)) if mutated_species is not None else None
    for trait_id in GENOTYPE_TRAIT_IDS:
        if trait_id == "species":
            continue
        if kept_parent is not None:
            child[trait_id] = [
                _roll_pool_allele(mutated_species, trait_id), random.choice(_genotype_pair(kept_parent, trait_id)),
            ]
        else:
            child[trait_id] = [
                random.choice(_genotype_pair(pair_a, trait_id)), random.choice(_genotype_pair(pair_b, trait_id)),
            ]

    if random.random() < GENETICS_SETTINGS.get("environment_mutation_chance", 0.0):
        chosen = _roll_pool_allele(child_species, "environment")
        child["environment"] = [chosen, chosen]
    return child, mutated_species is not None


def breed_creatures(state: GameState, parent_a_id: str, parent_b_id: str) -> ActionResult:
    """Скрещивает двух РАЗНЫХ особей игрока (из банка или уже поселённых в
    ЛЮБУЮ ферму - неважно, в одну и ту же или в разные) - требует работающий
    механизм скрещивания (breeding_station_available), но НЕ привязано к
    конкретной ферме: сама ферма (apiary/electric_apiary) для скрещивания
    участия не принимает, только расселяет готовых особей. Скрещивание ВСЕГДА
    бесплатно, кроме самих родителей (списываются целиком) - никакой доп. цены
    сверху (раньше была настраиваемая content.json["genetics"]["breeding_cost"],
    убрана по прямому решению). Ребёнок добавляется в банк НЕОПОЗНАННЫМ. Тратит
    ход. Родители ДОЛЖНЫ быть одного семейства (species_family) - скрещивать,
    например, пчелу с цветком нельзя: family - это как раз generic-тег,
    отделяющий один "вид существ" от другого (см. breeding_accepts_family), а
    ни одна мутация в контенте не пересекает семейства (MUTATIONS_BY_PAIR
    всегда bee×bee/flower×flower и т.п.) - смешение семейств никогда не имело
    смысла, только не было запрещено явно."""
    if not breeding_station_available(state):
        return ActionResult(False, "Нужна работающая станция скрещивания.")
    if parent_a_id == parent_b_id or parent_a_id not in state.creatures or parent_b_id not in state.creatures:
        return ActionResult(False, "Нужны две разные существующие особи.")
    family_a = species_family(state.creatures[parent_a_id]["species_id"])
    family_b = species_family(state.creatures[parent_b_id]["species_id"])
    if family_a != family_b:
        return ActionResult(False, "Можно скрещивать только особей одного семейства.")
    parent_a = state.creatures.pop(parent_a_id)
    parent_b = state.creatures.pop(parent_b_id)
    genotype, mutated = _cross_genotype(state, parent_a["genotype"], parent_b["genotype"])
    child_id = spawn_creature(state, genotype, identified=False)
    child_species_id = state.creatures[child_id]["species_id"]
    name = RESOURCE_NAMES.get(child_species_id, "?")
    # Структурные данные для центрального визуального эффекта в UI (сведение
    # родителей -> вспышка -> материализация потомка) - тот же принцип, что
    # ActionResult.prize/combat/enchant: message уже содержит то же самое
    # текстом, но фронтенду нужны id видов (для иконок), а не парсинг строки.
    breed_info = {
        "mutated": mutated,
        "parent_a_species_id": parent_a["species_id"],
        "parent_a_name": RESOURCE_NAMES.get(parent_a["species_id"], "?"),
        "parent_b_species_id": parent_b["species_id"],
        "parent_b_name": RESOURCE_NAMES.get(parent_b["species_id"], "?"),
        "child_species_id": child_species_id,
        "child_name": name,
    }
    if mutated:
        return ActionResult(True, f"Мутация! Скрещивание дало новый вид: «{name}».", breed=breed_info)
    return ActionResult(True, f"Скрещивание удалось: детёныш вида «{name}» (без мутации).", breed=breed_info)


def analyze_creature(state: GameState, creature_id: str) -> ActionResult:
    """Мгновенно опознаёт геном особи (требует работающую генетическую
    лабораторию) - не тратит ход, как house_creature/release_creature. Это
    ЕДИНСТВЕННЫЙ способ опознать особь - автоматического опознания по времени
    в ферме нет (по прямому решению: до анализа виден только вид, всё
    остальное - "вслепую", даже если особь годами живёт в ферме)."""
    if not genetics_lab_available(state):
        return ActionResult(False, "Нужна работающая генетическая лаборатория.")
    creature = state.creatures.get(creature_id)
    if creature is None:
        return ActionResult(False, "Такой особи нет.")
    if creature["identified"]:
        return ActionResult(False, "Геном уже опознан.")
    creature["identified"] = True
    _register_known_alleles(state, creature["genotype"])
    return ActionResult(True, "Геном опознан.")


def analyze_creatures(state: GameState, creature_ids: list[str]) -> ActionResult:
    """"Опознать всех" в модалке фермы - analyze_creature по каждой особи списка
    (его собирает клиент по своему последнему VIEW). Уже опознанные и
    исчезнувшие особи молча пропускаются, а не валят весь пакет. Ход не тратит,
    как и одиночный анализ."""
    if not genetics_lab_available(state):
        return ActionResult(False, "Нужна работающая генетическая лаборатория.")
    count = sum(
        1 for cid in creature_ids
        if cid in state.creatures and not state.creatures[cid]["identified"]
        and analyze_creature(state, cid).ok
    )
    if not count:
        return ActionResult(False, "Опознавать некого - все особи уже опознаны.")
    return ActionResult(True, f"Опознано особей: {count}.")


def synthesize_creature(state: GameState, chosen_alleles: dict[str, str]) -> ActionResult:
    """Генетический шаблон (Gendustry) - штампует ГОМОЗИГОТНУЮ (чистокровную по
    всем 4 признакам) особь с ВЫБРАННЫМ игроком геномом, минуя RNG. Каждая
    выбранная аллель должна быть уже ИЗУЧЕНА (state.known_alleles, см.
    analyze_creature/_register_known_alleles) - иначе игрок мог бы синтезировать
    признаки, которые никогда не видел. Цена - content.json["genetics"]
    ["synthesis_cost"], фиксированная на весь синтез (НЕ масштабируется по
    редкости выбранных аллелей - осознанное упрощение v1). Тратит ход."""
    if not genetics_lab_available(state):
        return ActionResult(False, "Нужна работающая генетическая лаборатория.")
    if set(chosen_alleles) != set(GENOTYPE_TRAIT_IDS):
        return ActionResult(False, "Нужно выбрать аллель для каждого признака.")
    for trait_id, allele_id in chosen_alleles.items():
        if allele_id not in state.known_alleles.get(trait_id, []):
            return ActionResult(False, f"Аллель «{allele_id}» ещё не изучена.")
    cost = dict(GENETICS_SETTINGS.get("synthesis_cost", {}))
    target_id = ":".join(f"{t}={a}" for t, a in sorted(chosen_alleles.items()))
    deferred = _pay_or_defer(
        state, cost, "синтез особи", "synthesize", target_id,
        retry_extra={"chosen_alleles": dict(chosen_alleles)},
    )
    if deferred is not None:
        return deferred
    genotype = {t: [a, a] for t, a in chosen_alleles.items()}
    child_id = spawn_creature(state, genotype, identified=True)
    name = RESOURCE_NAMES.get(state.creatures[child_id]["species_id"], "?")
    return ActionResult(True, f"Синтезирована особь: {name}.")


def creature_view(state: GameState, creature_id: str) -> dict:
    """Пока особь не опознана (analyze_creature), наружу отдаётся ТОЛЬКО её вид
    (species_id/name, и так виден в заголовке "ВИД ПЧЕЛЫ №n") - весь фенотип
    (produces/consumes/lifespan/output_multiplier/active_period/environment_id) и
    генотип скрыты полностью, а не только генотип, как было раньше. Игрок всё
    равно может поселить неопознанную особь в ферму и получать от неё продукцию
    "вслепую" (house_creature identified не проверяет) - просто не видит, что
    именно она производит и когда умрёт, пока не закажет анализ.

    entry["produces"] - сырой produces вида (как в content.json, БЕЗ множителей;
    показ в UI - без чисел, см. CLAUDE.md). entry["bonus_produce"] - None, если
    особь не поселена в экземпляр фермы с активной ПОДХОДЯЩЕЙ (совпадающей с её
    геном) эмуляцией среды, иначе {"resource_id", "chance"} бонусной продукции
    этой среды (см. _process_habitat - тот же расчёт)."""
    creature = state.creatures[creature_id]
    species_id = _phenotype_allele("species", creature["genotype"]["species"])
    entry = {
        "id": creature_id,
        "species_id": species_id,
        "family": species_family(species_id),
        "name": RESOURCE_NAMES.get(species_id, species_id),
        "identified": creature["identified"],
        "farm_instance_id": creature["farm_instance_id"],
        "genotype": None,
    }
    if not creature["identified"]:
        return entry
    phenotype = creature_phenotype(creature)
    species = SPECIES.get(phenotype["species_id"], {})
    entry["turns_alive"] = creature["turns_alive"]
    entry["lifespan"] = phenotype["lifespan"]
    entry["output_multiplier"] = phenotype["output_multiplier"]
    entry["active_period"] = phenotype["active_period"]
    entry["environment_id"] = phenotype["environment_id"]
    entry["produces"] = dict(species.get("produces", {}))
    entry["consumes"] = dict(species.get("consumes", {}))
    entry["bonus_produce"] = None
    instance_id = creature["farm_instance_id"]
    if instance_id is not None:
        active_env = _instance_environment_id(state, instance_id)
        if active_env is not None and phenotype["environment_id"] == active_env:
            # Бонус среды живёт на ВИДЕ (species.*.bonus_resource), как в _process_habitat.
            resource = species.get("bonus_resource")
            if resource:
                entry["bonus_produce"] = {"resource_id": resource, "chance": species.get("bonus_chance", 0)}
    entry["genotype"] = {trait_id: _allele_pair_view(creature, trait_id) for trait_id in GENOTYPE_TRAIT_IDS}
    return entry


def _allele_pair_view(creature: dict, trait_id: str) -> list[dict]:
    """Пара аллелей признака для UI. expressed - какая из двух проявлена (тот же
    _phenotype_allele, что у фенотипа), чтобы клиент не повторял правило
    доминантности; у одинаковой пары помечена первая."""
    pair = _genotype_pair(creature["genotype"], trait_id)
    expressed_index = pair.index(_phenotype_allele(trait_id, pair))
    alleles = trait_alleles(trait_id)
    return [
        {
            "id": aid,
            "name": alleles.get(aid, {}).get("name", aid),
            "dominant": alleles.get(aid, {}).get("dominant", False),
            "expressed": index == expressed_index,
        }
        for index, aid in enumerate(pair)
    ]


def creatures_view(state: GameState, *, farm_instance_id: str | None = None, unhoused_only: bool = False) -> list[dict]:
    ids = []
    for cid, c in state.creatures.items():
        if unhoused_only and c["farm_instance_id"] is not None:
            continue
        if farm_instance_id is not None and c["farm_instance_id"] != farm_instance_id:
            continue
        ids.append(cid)
    return [creature_view(state, cid) for cid in ids]


def farms_view(state: GameState) -> list[dict]:
    """Один элемент на КАЖДЫЙ физический экземпляр постройки-фермы (не на тип, как
    раньше) - см. state.farm_instances/_ensure_farm_instances. "name" нумерует
    экземпляры одного типа по порядку встречи в state.farm_instances ("Пасека №1",
    "Пасека №2", ...) - у РАЗНЫХ типов нумерация своя, независимая."""
    _ensure_farm_instances(state)
    counts_by_type: dict[str, int] = {}
    result = []
    for instance_id, instance in state.farm_instances.items():
        building_id = instance["building_id"]
        counts_by_type[building_id] = counts_by_type.get(building_id, 0) + 1
        result.append({
            "id": instance_id,
            "building_id": building_id,
            "name": f"{BUILDINGS[building_id]['name']} №{counts_by_type[building_id]}",
            "working": not instance["broken"],
            "slot_capacity": farm_instance_slot_capacity(state, instance_id),
            "slots_used": farm_instance_slots_used(state, instance_id),
            "accepts_family": breeding_accepts_family(building_id),
            "creatures": creatures_view(state, farm_instance_id=instance_id),
        })
    return result


def soul_collector_instances_view(state: GameState) -> list[dict]:
    """Зеркало farms_view для синтезаторов душ - один элемент на КАЖДЫЙ
    физический экземпляр (см. state.soul_collector_instances/
    _ensure_soul_collector_instances), а не на тип постройки, как было до
    2026-09-18. "name" нумерует экземпляры одного типа по порядку встречи
    ("Синтезатор душ №1", "Синтезатор душ №2", ...), у разных типов своя
    независимая нумерация - тот же принцип, что у farms_view."""
    _ensure_soul_collector_instances(state)
    counts_by_type: dict[str, int] = {}
    result = []
    for instance_id, instance in state.soul_collector_instances.items():
        building_id = instance["building_id"]
        counts_by_type[building_id] = counts_by_type.get(building_id, 0) + 1
        result.append({
            "id": instance_id,
            "building_id": building_id,
            "name": f"{BUILDINGS[building_id]['name']} №{counts_by_type[building_id]}",
            "working": not instance["broken"],
            "capacity": soul_collector_instance_capacity(state, instance_id),
            "accepts_kind": soul_collector_accepts_kind(building_id),
            "installed": [
                {"id": rid, "index": i}
                for i, rid in enumerate(instance["installed"])
            ],
        })
    return result


def reactor_instances_view(state: GameState) -> list[dict]:
    """Зеркало soul_collector_instances_view для реакторов - один элемент на
    КАЖДЫЙ физический экземпляр (см. state.reactor_instances/
    _ensure_reactor_instances). Решётка отдаётся ПЛОСКИМ списком ячеек
    (row-major, как хранится в state) + отдельно width/height - сама раскладка
    в 2D-сетку остаётся на стороне UI (см. tools/web_ui.html:renderReactorGrid,
    ближайший прецедент - renderMineFieldGrid)."""
    _ensure_reactor_instances(state)
    counts_by_type: dict[str, int] = {}
    result = []
    for instance_id, instance in state.reactor_instances.items():
        building_id = instance["building_id"]
        counts_by_type[building_id] = counts_by_type.get(building_id, 0) + 1
        width, height = reactor_grid_dims(state, instance_id)
        config = BUILDINGS.get(building_id, {}).get("reactor", {})
        layout = _reactor_zone_layout(width, height, len(instance["zones"]))
        result.append({
            "id": instance_id,
            "building_id": building_id,
            "name": f"{BUILDINGS[building_id]['name']} №{counts_by_type[building_id]}",
            "working": not instance["broken"],
            "width": width,
            "height": height,
            "accepts_roles": config.get("accepts_roles", []),
            "telemetry": reactor_telemetry_available(state, instance_id),
            "projection": reactor_zone_projection(state, instance_id),
            "coolant_fluid": instance.get("coolant_fluid"),
            "coolant_fluid_name": RESOURCE_NAMES.get(instance.get("coolant_fluid"))
                if instance.get("coolant_fluid") else None,
            "coolant_fluid_stock": state.resources.get(instance.get("coolant_fluid"), 0)
                if instance.get("coolant_fluid") else 0,
            "grid": [
                None if cell is None else {
                    "resource_id": cell["resource_id"],
                    "name": RESOURCE_NAMES.get(cell["resource_id"], cell["resource_id"]),
                    "role": reactor_component_role(cell["resource_id"]),
                    "charge": cell.get("charge"),
                    "zone_index": layout[i] if i < len(layout) else None,
                    "catalyst_id": cell.get("catalyst_id"),
                    "catalyst_name": RESOURCE_NAMES.get(cell["catalyst_id"]) if cell.get("catalyst_id") else None,
                }
                for i, cell in enumerate(instance["grid"])
            ],
            "zones": [
                {
                    "index": i,
                    "heat": z["heat"],
                    "heat_cap": _reactor_zone_heat_cap(state, instance_id, i, layout),
                    "control_rod_depth": z["control_rod_depth"],
                    "coolant_flow": z["coolant_flow"],
                }
                for i, z in enumerate(instance["zones"])
            ],
        })
    return result


def known_alleles_view(state: GameState) -> dict[str, list[dict]]:
    # "value" - сырое числовое значение аллели (lifespan в ходах,
    # output_multiplier как множитель), если оно у признака вообще есть
    # (species/active_period/environment его не имеют - None); нужно фронту
    # для подсказки в паджере синтеза (см. renderSynthesizeSection), сам
    # known_alleles_view уже фильтрует по уже ИЗУЧЕННЫМ аллелям, так что
    # раскрывать значение тут не проблема - игрок и так его видел при опознании.
    return {
        trait_id: [
            {
                "id": aid,
                "name": trait_alleles(trait_id).get(aid, {}).get("name", aid),
                "value": trait_alleles(trait_id).get(aid, {}).get("value"),
            }
            for aid in state.known_alleles.get(trait_id, [])
        ]
        for trait_id in GENOTYPE_TRAIT_IDS
    }


def species_catalog_view(state: GameState) -> list[dict]:
    """Каталог ВСЕХ видов из content.json["species"] для "книги знаний" о
    скрещивании - в отличие от known_alleles_view (только уже встреченные игроком
    аллели), тут отдаются все виды разом, независимо от того, встречал ли игрок
    их вообще - это книга-справочник, а не журнал находок.

    Для каждого вида "recipes" - все пары родителей (из MUTATIONS_BY_PAIR,
    неупорядоченно), которые могут его дать при скрещивании, с ТЕКУЩИМ
    эффективным шансом (тот же _breeding_mutation_multiplier(state), что
    реально участвует в _cross_genotype, и та же доля от outcome["weight"]
    среди исходов ОДНОЙ мутации). "locked" - true, если requires_tech/
    requires_building этой мутации сейчас не выполнены (тех не изучен или
    механизм не работает) - рецепт всё равно показывается (это справочник "как
    получить", а не "что доступно прямо сейчас"), просто помечается.

    "breedable" - можно ли получить вид ТОЛЬКО скрещиванием (species_id
    встречается как исход хотя бы одной мутации) - на фронте только такие виды
    кликабельны как родитель (переход к его собственному рецепту), "базовые"
    виды (ловятся в дикой природе - см. manual_actions.*.spawn_species) кликать
    некуда, для них рецептов не существует.

    "gene_pool" - {признак: {аллель: доля 0..1}} по species_gene_pool: какие гены
    носит вид от природы и как часто (дикий улов, аллель нового вида при мутации)."""
    bred_species_ids = {outcome["species_id"] for mutation in MUTATIONS for outcome in mutation["outcomes"]}
    mult = _breeding_mutation_multiplier(state)
    catalog = []
    for species_id in SPECIES:
        recipes = []
        for mutation in MUTATIONS:
            total_weight = sum(o["weight"] for o in mutation["outcomes"])
            if total_weight <= 0:
                continue
            for outcome in mutation["outcomes"]:
                if outcome["species_id"] != species_id:
                    continue
                req_tech = mutation.get("requires_tech")
                req_building = mutation.get("requires_building")
                locked = (
                    (bool(req_tech) and req_tech not in state.techs)
                    or (bool(req_building) and working_count(state, req_building) <= 0)
                )
                recipes.append({
                    "parent_a": mutation["parent_a"],
                    "parent_b": mutation["parent_b"],
                    "chance": mutation["chance"] * mult * (outcome["weight"] / total_weight),
                    "requires_tech": req_tech,
                    "requires_building": req_building,
                    "locked": locked,
                })
        gene_pool = {}
        for trait_id in GENOTYPE_TRAIT_IDS:
            if trait_id == "species":
                continue
            pool = species_gene_pool(species_id, trait_id)
            total = sum(pool.values())
            gene_pool[trait_id] = {aid: weight / total for aid, weight in pool.items()}
        catalog.append({
            "id": species_id,
            "description": SPECIES[species_id].get("description", ""),
            "family": SPECIES[species_id].get("family"),
            "produces": dict(SPECIES[species_id].get("produces", {})),
            "consumes": dict(SPECIES[species_id].get("consumes", {})),
            "breedable": species_id in bred_species_ids,
            "recipes": recipes,
            "gene_pool": gene_pool,
        })
    return catalog


def toggle_pin(state: GameState, resource_id: str) -> ActionResult:
    # resource_id приходит из HTTP-запроса (см. /api/pin в tools/web_ui.py) - в отличие
    # от остальных ActionResult-функций, это единственная точка входа, куда невалидный
    # id может попасть не из собственного же UI, а из произвольного тела запроса.
    if resource_id not in RESOURCE_NAMES:
        return ActionResult(False, "Неизвестный ресурс.")
    if resource_id == ENERGY_RESOURCE_ID:
        return ActionResult(False, "Энергия и так всегда видна на панели.")
    if resource_id in state.pinned:
        state.pinned.remove(resource_id)
        return ActionResult(True, f"Откреплено: {RESOURCE_NAMES[resource_id]}.")
    if len(state.pinned) >= MAX_PINNED:
        return ActionResult(False, f"Уже закреплено максимум {MAX_PINNED} - сначала открепи что-нибудь.")
    state.pinned.append(resource_id)
    return ActionResult(True, f"Закреплено: {RESOURCE_NAMES[resource_id]}.")


# Любой другой способ получить ресурс из справочника источников (ритуал, постройка,
# шахта, лут...) закрепляется ключом "source:<ресурс>:<вид>:<id способа>". Какие
# способы вообще есть, знает только справочник (tools/web_ui.py: resource_sources) -
# он и проверяет ключ перед toggle_pinned_recipe; движок проверяет лишь ресурс.
SOURCE_PIN_KEY_PREFIX = "source:"


def source_pin_key(resource_id: str, kind: str, ident: str) -> str:
    return f"{SOURCE_PIN_KEY_PREFIX}{resource_id}:{kind}:{ident}"


def parse_source_pin_key(key: str) -> tuple[str, str, str] | None:
    """(resource_id, kind, ident) для ключа source_pin_key, None - это не он."""
    if not key.startswith(SOURCE_PIN_KEY_PREFIX):
        return None
    parts = key[len(SOURCE_PIN_KEY_PREFIX):].split(":", 2)
    return tuple(parts) if len(parts) == 3 and all(parts) else None


def toggle_pinned_recipe(state: GameState, action_id: str) -> ActionResult:
    """Звезда в карточке справочника источников (state.pinned_recipes) - как
    toggle_pin, не тратит ход. action_id приходит из тела HTTP-запроса (см.
    /api/pin_recipe в tools/web_ui.py), поэтому проверяется явно, а не
    предполагается валидным. Открепление возможно для ЛЮБОГО закреплённого id,
    даже если контент с тех пор удалил само действие (см. CLAUDE.md "Удаление
    контента ломает сохранения") - иначе игрок не смог бы вычистить сохранение."""
    if not isinstance(action_id, str):
        return ActionResult(False, "Это не рецепт.")
    if action_id in state.pinned_recipes:
        state.pinned_recipes.remove(action_id)
        return ActionResult(True, "Рецепт откреплён.")
    source = parse_source_pin_key(action_id)
    if source is not None:
        if source[0] not in RESOURCE_NAMES:
            return ActionResult(False, "Такого ресурса нет.")
        state.pinned_recipes.append(action_id)
        return ActionResult(True, f"Закреплено: {RESOURCE_NAMES[source[0]]}.")
    # Рецепт плавильни (плавка/сплав/литьё) - своё пространство ключей, см. SMELTERY_KEY_PREFIXES.
    smelt = parse_smeltery_recipe_key(action_id)
    if smelt is not None:
        recipe = smeltery_recipes(smelt[0]).get(smelt[1])
        if recipe is None:
            return ActionResult(False, "Такого рецепта плавильни нет.")
        state.pinned_recipes.append(action_id)
        return ActionResult(True, f"Закреплено: {recipe['name']}.")
    # action_id - ключ сеточного рецепта (grid_recipe_key): id ручного действия
    # или "energy_recipe:<id>" для рецепта преобразователя с узором.
    kind, target_id = parse_grid_recipe_key(action_id)
    if kind == "energy_recipe":
        if target_id not in ENERGY_RECIPES or energy_recipe_pattern(target_id) is None:
            return ActionResult(False, "Это не сеточный рецепт.")
        name = ENERGY_RECIPES[target_id]["name"]
    elif kind == "building":
        if target_id not in BUILDINGS or not BUILDINGS[target_id].get("location"):
            return ActionResult(False, "Эту постройку нигде не собрать.")
        name = BUILDINGS[target_id]["name"]
    else:
        if kind != "manual_action" or target_id not in MANUAL_ACTIONS or not is_grid_action(target_id):
            return ActionResult(False, "Это не сеточный рецепт.")
        name = MANUAL_ACTIONS[target_id]["name"]
    state.pinned_recipes.append(action_id)
    return ActionResult(True, f"Закреплено: {name}.")


def _busy_manual_job_in_location(manual_jobs: dict[str, dict], action_id: str) -> str | None:
    """action_id многоходовой задачи, которая сейчас занимает локацию действия
    action_id (это может быть и оно само) - None, если локация свободна. Правило
    "одна многоходовая задача на локацию" (точное совпадение location, не
    рекурсивно по parent_location) - общее для клика игрока (do_manual_action) и
    автокрафта (_aggregate_autocraft_demand/_commit_autocraft_plan)."""
    location = MANUAL_ACTIONS[action_id].get("location")
    for other_id in manual_jobs:
        if MANUAL_ACTIONS[other_id].get("location") == location:
            return other_id
    return None


def _new_manual_job(
    action_id: str, duration: int, cost: dict, gain: dict, autocraft: bool = False,
    order_id: int | None = None, order_resource: str | None = None,
) -> dict:
    """Запись state.manual_jobs[action_id] - cost уже списан вызывающим. autocraft -
    задачу запустил автокрафт, а не игрок: по завершении она не засчитывается в
    action_performed_count (как и мгновенные прокрутки автокрафта), а UI помечает
    её в панели "Долгие действия"."""
    action = MANUAL_ACTIONS[action_id]
    job = {
        "turns_total": duration, "turns_done": 0,
        "gain": dict(gain), "gain_chance": action.get("gain_chance"),
        "spawn_species": action.get("spawn_species"),
        "cost": dict(cost),  # для возврата при досрочной отмене, см. cancel_manual_job
    }
    if autocraft:
        job["autocraft"] = True
    if order_id is not None:
        # Партия заказа: по завершении результат идёт в резерв заказа, не на склад.
        job["order_id"] = order_id
        job["order_resource"] = order_resource
    return job


def do_manual_action(state: GameState, action_id: str, scale: int | None = None) -> ActionResult:
    """scale - на сколько "партий" ЭТОГО действия хочет игрок за один клик, ТОЛЬКО
    если действие зависит от постройки-"механизма" через requires_building И у неё
    куплен хотя бы один scale-апгрейд (см. mechanism_scale_cap) - иначе игнорируется.
    None (по умолчанию, как и раньше) значит "автомат" - реализуется ПОЛНЫМ
    купленным масштабом, как всегда было. Явное значение зажимается в [1,
    mechanism_scale_cap] на месте (а не отклоняется ошибкой) - см. "Ползунок
    масштаба ручного действия" в CLAUDE.md про то, почему апгрейд теперь задаёт
    только ВЕРХНЮЮ ГРАНИЦУ, а не обязательный множитель.

    cost_chance/gain_chance (см. is_manual_action_deterministic) применяются здесь,
    ПОСЛЕ effective_manual_action_stats - каждый ресурс независимо "выживает" в эту
    ОДНУ попытку с вероятностью chance (по умолчанию 1.0, как раньше). Цена бросается
    ДО оплаты (списывается/докрафчивается уже РЕЗУЛЬТАТ броска - не удался бросок,
    ресурс в эту попытку вообще не требуется), выход - ПОСЛЕ успешной оплаты. Бросок
    заново на КАЖДЫЙ вызов, включая повторную попытку отложенного действия
    (_PENDING_RETRY["manual"]) - каждая попытка независима.

    Многоходовое действие (duration > 1) дополнительно занимает СВОЮ ЛОКАЦИЮ
    целиком (action["location"], сравнение по значению - None тоже валидная
    "локация", корень дерева): пока в ней уже тикает любой другой многоходовой
    job, запустить ещё один (другим action_id) нельзя, даже если оба действия
    формально доступны игроку одновременно. Проверка НЕ рекурсивная - дочерние
    локации (parent_location) под ограничение не подпадают, только точное
    совпадение location (так что, например, охота в подлокации шахты спокойно
    идёт параллельно с многоходовой добычей в самой шахте)."""
    action = MANUAL_ACTIONS[action_id]
    if not is_manual_action_unlocked(action_id, state):
        return ActionResult(False, "Это действие ещё не открыто.")
    # uses_tool_durability - необязательное generic-поле {"tool": id} (тот же
    # приём, что tool_scaling): действие тратит 1 прочность выбранного
    # инструмента РОВНО ОДИН РАЗ за выполнение, сразу после успешной оплаты cost
    # (тот же момент, что и у добычи руды - см. do_dig/_consume_tool_durability),
    # а НЕ по завершении многоходовой задачи (duration_turns) - износ идёт от
    # самого запуска действия, а не от получения gain. Сломанный инструмент (см.
    # tool_is_broken) блокирует запуск целиком ДО оплаты cost - тот же принцип,
    # что у шахтёрской кирки. Инструмент без max_durability вовсе (см.
    # tool_durability_remaining) этим полем не ограничивает ничего - тратить
    # у него нечего, tool_is_broken для него всегда ЛОЖЬ.
    uses_tool_durability = action.get("uses_tool_durability")
    if uses_tool_durability:
        use_tool_id = uses_tool_durability["tool"]
        if tool_is_broken(state, use_tool_id):
            return ActionResult(False, f"«{TOOLS[use_tool_id]['name']}» сломан(а) - нужно сначала его/её починить.")
    # manual_action_duration уже учитывает купленный апгрейд типа
    # action_duration_reduction (см. CLAUDE.md) - настолько сильное сокращение,
    # что итог <= 1, делает действие фактически мгновенным (ветка ниже).
    duration = manual_action_duration(action_id, state)
    if duration > 1:
        other_id = _busy_manual_job_in_location(state.manual_jobs, action_id)
        if other_id == action_id:
            return ActionResult(False, f"«{action['name']}» уже выполняется.")
        if other_id is not None:
            return ActionResult(False, f"В этой локации уже выполняется другое действие: «{MANUAL_ACTIONS[other_id]['name']}».")
    resolved_scale = None
    building_id = action_mechanism_building(action)
    if building_id and scale is not None:
        cap = mechanism_scale_cap(building_id, state)
        resolved_scale = max(1, min(int(scale), cap))
    cost, gain = effective_manual_action_stats(action_id, state, chosen_scale=resolved_scale)
    cost_chance = action.get("cost_chance")
    if cost_chance:
        cost = _roll_chance_amounts(cost, cost_chance)
    deferred = _pay_or_defer(
        state, cost, f"«{action['name']}»", "manual", action_id,
        retry_extra={"scale": resolved_scale} if resolved_scale is not None else None,
        output={r: _amount_upper(v) for r, v in gain.items()},
    )
    if deferred is not None:
        return deferred
    if uses_tool_durability:
        _consume_tool_durability(state, uses_tool_durability["tool"])
    if duration > 1:
        state.manual_jobs[action_id] = _new_manual_job(action_id, duration, cost, gain)
        return ActionResult(True, f"Начато: {action['name']} (займётся {duration} х.).")
    gain_chance = action.get("gain_chance")
    if gain_chance:
        gain = _roll_chance_amounts(gain, gain_chance)
    # Случайный диапазон gain (см. _is_range_amount/_roll_range_amounts) -
    # разворачивается в конкретное число ПОСЛЕДНИМ, после шанса, ровно на эту
    # попытку - is_manual_action_deterministic не даёт таким действиям попасть в
    # автокрафт, так что здесь всегда обычный клик игрока.
    gain = _roll_range_amounts(gain)
    _gain(state.resources, gain)
    spawn_species = action.get("spawn_species")
    if spawn_species:
        # Ловля дикой особи (см. "Хардкод id" - НЕ привязано к конкретному
        # действию, любое manual_actions с этим полем создаёт особь после
        # успешной оплаты) - одна особь на клик, gain (если тоже задан) и
        # spawn_species применяются НЕЗАВИСИМО друг от друга.
        spawn_creature(state, _roll_wild_genotype(spawn_species), identified=False)
    state.action_performed_count[action_id] = state.action_performed_count.get(action_id, 0) + 1
    return ActionResult(True, f"Выполнено: {action['name']}.")


def building_construction_stages(building_id: str) -> list[dict]:
    """Этапы многоэтапного строительства (BUILDINGS[id]["construction_stages"]) -
    пусто у обычной одноэтапной постройки (см. build())."""
    return BUILDINGS[building_id].get("construction_stages") or []


def current_construction_stage(building_id: str, state: GameState) -> dict | None:
    """Для многоэтапной постройки - следующий неоплаченный этап: {"index" (с 0),
    "number" (с 1, для UI), "total", "cost", "description"}. None для обычной
    одноэтапной постройки - вызывающий должен в этом случае работать с building["cost"]
    напрямую, как раньше."""
    stages = building_construction_stages(building_id)
    if not stages:
        return None
    index = state.construction_progress.get(building_id, 0)
    stage = stages[index]
    return {
        "index": index, "number": index + 1, "total": len(stages),
        "cost": stage["cost"], "description": stage["description"],
    }


def build(state: GameState, building_id: str) -> ActionResult:
    building = BUILDINGS[building_id]
    if not is_building_unlocked(building_id, state):
        return ActionResult(False, "Эта постройка ещё не открыта.")
    max_count = building.get("max_count")
    if max_count is not None and state.buildings.get(building_id, 0) >= max_count:
        return ActionResult(False, f"Нельзя построить больше {max_count} шт. этой постройки.")

    stage = current_construction_stage(building_id, state)
    if stage is None:
        deferred = _pay_or_defer(state, building["cost"], f"постройку «{building['name']}»", "build", building_id)
        if deferred is not None:
            return deferred
        state.buildings[building_id] = state.buildings.get(building_id, 0) + 1
        _ensure_physical_instances(state)
        return ActionResult(True, f"Построено: {building['name']}.")

    # Многоэтапное строительство: каждый вызов build() оплачивает РОВНО один
    # следующий этап (см. current_construction_stage) - постройка появляется в
    # state.buildings (и начинает работать) только после последнего. Каждая новая
    # стадия - НЕ отдельная постройка, а часть той же самой (один и тот же building_id).
    # Счётчик "Стадия N из M" показываем игроку, только когда стадий больше одной -
    # у списка из ровно одного этапа (см. content_validation.py - это осознанно
    # разрешённая форма, не только >=2) счётчик "1 из 1" ничего не говорит игроку.
    stage_suffix = f" (Стадия {stage['number']} из {stage['total']})" if stage["total"] > 1 else ""
    action_desc = f"постройку «{building['name']}»: {stage['description']}{stage_suffix}"
    deferred = _pay_or_defer(state, stage["cost"], action_desc, "build", building_id)
    if deferred is not None:
        return deferred
    if stage["number"] >= stage["total"]:
        state.construction_progress.pop(building_id, None)
        state.buildings[building_id] = state.buildings.get(building_id, 0) + 1
        _ensure_physical_instances(state)
        return ActionResult(True, f"Построено: {building['name']}.")
    state.construction_progress[building_id] = stage["index"] + 1
    # Эта ветка (прогресс продвинулся, но постройка ещё не готова) достижима, только
    # когда number < total, а значит total > 1 и stage_suffix точно не пуст.
    return ActionResult(True, f"Стадия {stage['number']} из {stage['total']} постройки «{building['name']}» готова: {stage['description']}.")


def tech_research_turns(tech_id: str, state: GameState) -> int:
    """Реальная длительность исследования технологии в ходах - тот же общий принцип,
    что manual_action_duration, но сокращение устроено иначе: апгрейд типа
    "research_speed" вычитает АБСОЛЮТНОЕ число ходов (не долю, как
    action_duration_reduction), и берётся не с постройки, от которой действие
    зависит (requires_building - у технологии такого поля нет), а с ЛЮБОЙ
    РАБОТАЮЩЕЙ лаборатории (BUILDINGS с полем lab_tier), чей lab_tier РАВЕН
    (не >=, как у lab_tier_capacity) тиру самой технологии - "лаборатория той
    же эры". Тир 1 не имеет своей лаборатории (см. lab_tier_capacity) -
    сократить тир-1 технологии нельзя. Среди нескольких подходящих лабораторий
    и нескольких купленных апгрейдов этого типа действует только самое сильное
    сокращение (тот же принцип "сильнейшее побеждает", см.
    _apply_building_upgrades/_farm_upgrade_multiplier). Минимум 1 ход, как и у
    manual_action_duration - отсутствие research_turns в контенте (как и
    duration_turns у ручных действий) значит "1 ход", т.е. исследование
    завершается сразу же, без прохода через state.active_research вовсе (см.
    research())."""
    duration = TECHS[tech_id].get("research_turns") or 1
    if duration <= 1:
        return duration
    tier = TECHS[tech_id].get("tier", 1)
    reduction = 0
    for building_id, data in BUILDINGS.items():
        if data.get("lab_tier") != tier or working_count(state, building_id) <= 0:
            continue
        reduction = max(reduction, _farm_upgrade_multiplier(state, building_id, "research_speed", default=0))
    return max(1, duration - reduction)


_HYP_N, _HYP_E, _HYP_S, _HYP_W = 1, 2, 4, 8
# (dr, dc) на каждый бит направления - используется и при прокладке пути, и при
# переводе "клетка A -> соседняя клетка B" обратно в биты.
_HYP_DIR_DELTAS = [(_HYP_N, -1, 0), (_HYP_E, 0, 1), (_HYP_S, 1, 0), (_HYP_W, 0, -1)]


def _hyp_rotate_cw(mask: int) -> int:
    """N->E->S->W->N - см. rotateCW в web_ui.html, ОБЕ реализации обязаны
    совпадать бит в бит (клиент вращает локально, сервер только генерирует
    initial_masks, см. generate_hypothesis_puzzle)."""
    return ((mask << 1) | (mask >> 3)) & 0b1111


def hypothesis_puzzle_grid(tech_id: str) -> tuple[int, int]:
    """(rows, cols) головоломки этапа 1 ("Гипотеза") - чистая функция ТОЛЬКО от
    TECHS[tech_id]["tier"], без нового поля в content.json (формула, не
    авторский баланс - см. CLAUDE.md). rows фиксирован, cols растёт с тиром и
    насыщается на 9, чтобы модалка не разрасталась бесконечно на дальних тирах."""
    tier = TECHS[tech_id].get("tier", 1)
    return 3, min(9, 3 + tier)


def generate_hypothesis_puzzle(tech_id: str) -> dict:
    """Детерминированная генерация головоломки этапа 1 - ЧИСТАЯ функция tech_id
    (тот же tech_id => тот же результат на любом перезапуске процесса:
    random.Random(str) хэширует строку сам, в отличие от встроенного hash(),
    не зависит от PYTHONHASHSEED). Возвращает {rows, cols, start_idx, end_idx,
    initial_masks, move_budget} - БЕЗ решённых масок: клиент сам прогоняет BFS
    по initial_masks после локальных поворотов (см. openHypothesisModal в
    web_ui.html), сервер не валидирует решение вообще (см. docstring
    solve_hypothesis - однопользовательская игра без анти-чита).

    Алгоритм: 0) строка старта и строка финиша выбираются СЛУЧАЙНО (0..rows-1
    каждая, независимо друг от друга - могут и совпасть) - только строка,
    НЕ столбец: столбец старта всегда 0 (левый край, там же в UI сидит
    "Импульс"), столбец финиша всегда cols-1 (правый край, "Инсайт") - это
    сохраняет расклад "значок слева/справа от сетки со штырьком внутрь", а
    ВАРИАТИВНОСТЬ даёт разная строка входа/выхода на каждую технологию,
    вместо одной и той же пары (1,0)/(0,cols-1) всегда. 1) прокладывается
    ПРОСТОЙ путь (без ветвлений/повторов) от стартовой клетки к финишной
    рандомизированным DFS с бэктрекингом ("recursive backtracker") -
    гарантированно завершается, т.к. в
    конечном связном графе сетки простой путь между любыми двумя клетками
    существует всегда, а DFS с бэктрекингом полон. 2) Клетки НЕ на пути - чисто
    декоративный мусор со случайной маской. 3) Каждая клетка ПУТИ скремблится
    поворотом решённой маски на 1 или 3 шага (гарантированно не совпадает с
    решённой - см. _hyp_rotate_cw), после чего расстояние обратно до решения
    считается прямой симуляцией (не формулой - меньше риска алгебраической
    ошибки), а не берётся "на веру" из числа шагов скрамбла."""
    rows, cols = hypothesis_puzzle_grid(tech_id)
    rng = random.Random(tech_id)  # ЛОКАЛЬНЫЙ Random - НЕ модульный random.*,
    # иначе результат зависел бы от порядка вызовов между технологиями, а не
    # только от tech_id.

    def idx(r, c):
        return r * cols + c

    start_r, start_c = rng.randrange(rows), 0
    end_r, end_c = rng.randrange(rows), cols - 1

    def grid_neighbors(r, c):
        result = []
        for _bit, dr, dc in _HYP_DIR_DELTAS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                result.append((nr, nc))
        return result

    visited = {(start_r, start_c)}
    path = [(start_r, start_c)]

    def carve(r, c):
        if (r, c) == (end_r, end_c):
            return True
        # Смещаем порядок обхода к соседям, СОКРАЩАЮЩИМ манхэттенское расстояние
        # до финиша (в 85% случаев) - без этого случайный DFS-бэктрекер обычным
        # образом склонен извилисто обходить БОЛЬШУЮ часть сетки, прежде чем
        # наткнуться на финиш (стандартное свойство "recursive backtracker" для
        # генерации лабиринтов), что для маленькой сетки этапа 1 даёт сильно
        # завышенную (в разы больше ожидаемой по тиру) сложность. Обход всё
        # равно ПОЛНЫЙ (closer+other вместе покрывают всех соседей) - гарантия
        # нахождения пути при бэктрекинге не теряется, меняется только порядок
        # перебора, то есть ТИПИЧНАЯ форма получившегося пути.
        cur_dist = abs(r - end_r) + abs(c - end_c)
        options = grid_neighbors(r, c)
        closer = [n for n in options if abs(n[0] - end_r) + abs(n[1] - end_c) < cur_dist]
        other = [n for n in options if n not in closer]
        rng.shuffle(closer)
        rng.shuffle(other)
        ordered = closer + other if rng.random() < 0.85 else other + closer
        for nr, nc in ordered:
            if (nr, nc) in visited:
                continue
            visited.add((nr, nc))
            path.append((nr, nc))
            if carve(nr, nc):
                return True
            path.pop()
            visited.remove((nr, nc))
        return False

    carve(start_r, start_c)

    def direction_bit(a, b):
        ar, ac = a
        br, bc = b
        for bit, dr, dc in _HYP_DIR_DELTAS:
            if ar + dr == br and ac + dc == bc:
                return bit
        raise AssertionError("соседние клетки пути должны быть смежны по сетке")

    solved_masks = {}
    for i, cell in enumerate(path):
        mask = 0
        if i == 0:
            mask |= _HYP_W  # виртуальный порт "Импульса"
        else:
            entry_bit = direction_bit(path[i - 1], cell)
            mask |= {_HYP_N: _HYP_S, _HYP_E: _HYP_W, _HYP_S: _HYP_N, _HYP_W: _HYP_E}[entry_bit]
        if i == len(path) - 1:
            mask |= _HYP_E  # виртуальный порт "Инсайта"
        else:
            mask |= direction_bit(cell, path[i + 1])
        solved_masks[cell] = mask

    path_set = set(path)
    initial_masks = [0] * (rows * cols)
    min_clicks = 0
    for r in range(rows):
        for c in range(cols):
            if (r, c) in path_set:
                solved = solved_masks[(r, c)]
                turns = rng.choice([1, 3])
                mask = solved
                for _ in range(turns):
                    mask = _hyp_rotate_cw(mask)
                dist = 0
                probe = mask
                while probe != solved:
                    probe = _hyp_rotate_cw(probe)
                    dist += 1
                min_clicks += dist
                initial_masks[idx(r, c)] = mask
            else:
                base = (_HYP_N | _HYP_S) if rng.choice(["straight", "corner"]) == "straight" else (_HYP_N | _HYP_E)
                mask = base
                for _ in range(rng.randint(0, 3)):
                    mask = _hyp_rotate_cw(mask)
                initial_masks[idx(r, c)] = mask

    return {
        "rows": rows,
        "cols": cols,
        "start_idx": idx(start_r, start_c),
        "end_idx": idx(end_r, end_c),
        "initial_masks": initial_masks,
        "move_budget": min_clicks + 2,  # небольшой бесплатный запас, как в макете
    }


def solve_hypothesis(state: GameState, tech_id: str) -> ActionResult:
    """Отмечает головоломку этапа 1 решённой - НЕ тратит ход (тот же принцип,
    что claim_quest_reward/insert_soul/remove_soul). НЕ принимает и НЕ проверяет
    состояние головоломки от клиента - клиент уже сам прогнал BFS локально и
    прислал только факт победы (однопользовательская игра без анти-чита, см.
    CLAUDE.md про клиентский cooldown). Повторный вызов на уже решённой
    технологии - явный отказ, как research() на уже изученную."""
    if tech_id not in TECHS:
        return ActionResult(False, "Неизвестная технология.")
    if tech_id in state.solved_hypotheses:
        return ActionResult(False, "Уже решено.")
    state.solved_hypotheses.add(tech_id)
    return ActionResult(True, f"Гипотеза «{TECHS[tech_id]['name']}» подтверждена.")


def research(state: GameState, tech_id: str) -> ActionResult:
    """Запускает исследование технологии - как и раньше, ОДНИМ вызовом списывает
    tech["cost"] целиком, но теперь может не завершиться тут же: если
    tech_research_turns > 1, взамен заводится state.active_research (ГЛОБАЛЬНЫЙ
    слот, один на всю игру - как active_encounter/veins, НЕ как manual_jobs,
    у которого своя задача на каждый action_id) и тикает в _process_research
    каждый ход, пока не наберёт нужное число ходов.

    Переключиться на ДРУГОЕ исследование можно В ЛЮБОЙ момент, одним и тем же
    вызовом - если слот занят чем-то ещё, старое исследование тихо отменяется
    (cancel_research, cost возвращается целиком) и тут же стартует новое, без
    отдельного явного шага "сначала отмените". Порядок намеренный: отмена
    происходит ТОЛЬКО ПОСЛЕ того, как cost НОВОЙ технологии уже успешно списан
    (после is_tech_available/lab_tier_capacity/_pay_or_defer) - иначе неудачная
    попытка переключения (не хватило условий/ресурсов на новую) впустую стёрла
    бы прогресс старой. Повторный клик на ТУ ЖЕ технологию, что уже идёт, ничего
    не переигрывает - явный отказ, как и раньше."""
    if tech_id in state.techs:
        return ActionResult(False, "Уже изучено.")
    if state.active_research is not None and state.active_research["tech_id"] == tech_id:
        return ActionResult(False, "Уже исследуется.")
    if not is_tech_available(tech_id, state):
        return ActionResult(False, "Не выполнены условия для исследования.")
    if tech_id not in state.solved_hypotheses:
        return ActionResult(False, "Сначала нужно решить гипотезу.")
    tech = TECHS[tech_id]
    tier = tech.get("tier", 1)
    # Тир 1 доступен всегда без лаборатории; тир N>1 требует хотя бы одну РАБОТАЮЩУЮ
    # лабораторию тира >= N (см. lab_tier_capacity). Это НЕ часть is_tech_available -
    # намеренно: узел в дереве технологий остаётся видимым и "доступным" по requires
    # сразу, как только выполнены обычные условия, а не прячется отдельно, но попытка
    # исследовать без лаборатории должна явно объяснить игроку, чего не хватает.
    if tier > lab_tier_capacity(state):
        return ActionResult(False, f"Нужна лаборатория тира {tier} - она ещё не построена.")
    deferred = _pay_or_defer(state, tech["cost"], f"исследование «{tech['name']}»", "tech", tech_id)
    if deferred is not None:
        return deferred
    switch_prefix = ""
    if state.active_research is not None:
        cancelled_name = TECHS[state.active_research["tech_id"]]["name"]
        cancel_research(state)
        switch_prefix = f"«{cancelled_name}» отменено, затраты возвращены. "
    duration = tech_research_turns(tech_id, state)
    if duration <= 1:
        state.techs.add(tech_id)
        return ActionResult(True, f"{switch_prefix}Изучено: {tech['name']}.")
    state.active_research = {
        "tech_id": tech_id, "turns_total": duration, "turns_done": 0, "cost": dict(tech["cost"]),
    }
    return ActionResult(True, f"{switch_prefix}Начато исследование «{tech['name']}» (займётся {duration} х.).")


def cancel_research(state: GameState) -> ActionResult:
    """Снимает активное исследование немедленно, БЕЗ траты хода - как
    cancel_manual_job. cost уже списан целиком в момент старта (см. research()),
    поэтому отмена возвращает его игроку через _gain; уже накопленные тики
    (turns_done) возврату не подлежат сами по себе - тот же принцип, что у
    cancel_manual_job (тикали "бесплатно", ничего не расходовали)."""
    job = state.active_research
    if job is None:
        return ActionResult(False, "Сейчас ничего не исследуется.")
    tech = TECHS[job["tech_id"]]
    state.active_research = None
    _gain(state.resources, job.get("cost", tech["cost"]))
    return ActionResult(True, f"Исследование «{tech['name']}» отменено, затраты возвращены.")


def repair(state: GameState, building_id: str) -> ActionResult:
    """Чинит СРАЗУ все сломанные копии building_id, ЧЬЁ ТЕКУЩЕЕ required_tools
    полностью удовлетворено (см. repairable_count) - копии с недостающим
    инструментом остаются сломанными, оплата и сообщение отражают только
    реально починенную часть. За каждый РАЗЛИЧНЫЙ инструмент, использованный
    хотя бы одной починенной копией в этом вызове, списывается ровно 1
    прочность (см. _consume_tool_durability) - не за каждую копию отдельно."""
    broken = state.broken.get(building_id, 0)
    if broken <= 0:
        return ActionResult(False, "Тут нечего чинить.")
    fixable = repairable_count(state, building_id)
    if fixable <= 0:
        return ActionResult(False, "Не хватает инструмента для этого вида поломки.")
    cost = repair_cost_total(state, building_id)
    name = BUILDINGS[building_id]["name"]
    deferred = _pay_or_defer(state, cost, f"ремонт «{name}»", "repair", building_id)
    if deferred is not None:
        return deferred
    used_tools: set[str] = set()
    if _uses_physical_instances(building_id):
        _ensure_physical_instances(state)
        instances = _physical_instances(state, building_id)
        fixed = 0
        for instance in instances.values():
            if instance["building_id"] != building_id or not instance["broken"]:
                continue
            broken_type = instance.get("broken_type")
            if not _is_breakdown_type_fixable(state, broken_type):
                continue
            used_tools.update(breakdown_type_required_tools(broken_type))
            instance["broken"] = False
            instance["broken_type"] = None
            fixed += 1
        state.broken[building_id] = sum(
            1 for inst in instances.values()
            if inst["building_id"] == building_id and inst["broken"]
        )
    else:
        remaining_types = []
        fixed = 0
        for t in _broken_type_entries(state, building_id):
            if _is_breakdown_type_fixable(state, t):
                used_tools.update(breakdown_type_required_tools(t))
                fixed += 1
            else:
                remaining_types.append(t)
        state.broken_types[building_id] = remaining_types
        state.broken[building_id] = len(remaining_types)
    for tool_id in used_tools:
        _consume_tool_durability(state, tool_id)
    remaining_broken = state.broken.get(building_id, 0)
    suffix = f" Осталось сломано: {remaining_broken} шт. (не хватает инструмента)." if remaining_broken else ""
    return ActionResult(True, f"Отремонтировано: {name} ({fixed} шт.).{suffix}")


def resolve_action_cost(state: GameState, action_type: str, action_id: str, scale: int | None = None) -> dict[str, int] | None:
    """Цена, которую ПРЯМО СЕЙЧАС заплатит действие этого типа/id, если его
    выполнить, - та же цена, что реально использует _pay_or_defer внутри
    соответствующей функции (do_manual_action/build/research/repair/
    upgrade_tool/purchase_building_upgrade/upgrade_storage), без побочных эффектов и без
    повторных проверок "можно ли" (это дело вызывающего - UI и так не
    показывает недоступные действия). None, если для action_type/action_id
    понятие цены не определено (неизвестный тип, постройка уже на максимальном
    тире и т.п.). Используется ТОЛЬКО окном предпросмотра автокрафта перед
    подтверждением (см. tools/web_ui.py:autocraft_preview_view) - сам dispatch
    действия эту функцию не вызывает, там цена и так уже под рукой по месту."""
    if action_type == "manual":
        action = MANUAL_ACTIONS[action_id]
        building_id = action_mechanism_building(action)
        resolved_scale = None
        if building_id and scale is not None:
            cap = mechanism_scale_cap(building_id, state)
            resolved_scale = max(1, min(int(scale), cap))
        cost, _ = effective_manual_action_stats(action_id, state, chosen_scale=resolved_scale)
        return cost
    if action_type == "build":
        if action_id not in BUILDINGS:
            return None
        stage = current_construction_stage(action_id, state)
        return dict(stage["cost"]) if stage else dict(BUILDINGS[action_id]["cost"])
    if action_type == "tech":
        return dict(TECHS[action_id]["cost"]) if action_id in TECHS else None
    if action_type == "repair":
        return repair_cost_total(state, action_id) if action_id in BUILDINGS else None
    if action_type == "upgrade_tool":
        next_info = next_tool_tier_info(state, action_id) if action_id in TOOLS else None
        return dict(next_info["cost"]) if next_info else None
    if action_type == "repair_tool":
        if action_id not in TOOLS or not tool_needs_repair(state, action_id):
            return None
        return dict(_current_tool_tier_def(state, action_id).get("repair_cost", {}))
    if action_type == "building_upgrade":
        building_id, _, upgrade_id = action_id.partition(":")
        upgrade = next((u for u in building_upgrade_defs(building_id) if u["id"] == upgrade_id), None)
        if upgrade is None:
            return None
        return repeatable_upgrade_cost(upgrade, building_upgrade_purchase_count(state, building_id, upgrade_id))
    if action_type == "farm_instance_upgrade":
        instance_id, _, upgrade_id = action_id.partition(":")
        instance = state.farm_instances.get(instance_id)
        if instance is None:
            return None
        upgrade = next((u for u in building_upgrade_defs(instance["building_id"]) if u["id"] == upgrade_id), None)
        if upgrade is None:
            return None
        return repeatable_upgrade_cost(upgrade, farm_instance_upgrade_purchase_count(state, instance_id, upgrade_id))
    if action_type == "soul_instance_upgrade":
        instance_id, _, upgrade_id = action_id.partition(":")
        instance = state.soul_collector_instances.get(instance_id)
        if instance is None:
            return None
        upgrade = next((u for u in building_upgrade_defs(instance["building_id"]) if u["id"] == upgrade_id), None)
        if upgrade is None:
            return None
        return repeatable_upgrade_cost(upgrade, soul_collector_instance_upgrade_purchase_count(state, instance_id, upgrade_id))
    if action_type == "reactor_instance_upgrade":
        instance_id, _, upgrade_id = action_id.partition(":")
        instance = state.reactor_instances.get(instance_id)
        if instance is None:
            return None
        upgrade = next((u for u in building_upgrade_defs(instance["building_id"]) if u["id"] == upgrade_id), None)
        if upgrade is None:
            return None
        return repeatable_upgrade_cost(upgrade, reactor_instance_upgrade_purchase_count(state, instance_id, upgrade_id))
    if action_type == "storage_upgrade":
        next_info = next_storage_tier_info(state, action_id)
        return dict(next_info["cost"]) if next_info else None
    if action_type == "start_energy_recipe":
        # Только материальная цена (consumes), если она у рецепта есть - энергия
        # (threshold) списывается ПОСТЕПЕННО по ходам из общего пула
        # (_process_energy_jobs), а не мгновенно при запуске, поэтому её здесь нет.
        recipe = ENERGY_RECIPES.get(action_id)
        return dict(recipe.get("consumes", {})) if recipe else None
    if action_type == "start_ritual":
        # Полная разовая цена (ring_cost + center_cost + extra_cost) - без
        # extra_consumes_per_turn, который списывается постепенно по ходам
        # (_process_ritual_jobs), а не мгновенно при запуске.
        recipe = RITUAL_RECIPES.get(action_id)
        return _ritual_full_cost(recipe) if recipe else None
    if action_type == "synthesize":
        return dict(GENETICS_SETTINGS.get("synthesis_cost", {}))
    if action_type == "summon_boss":
        return dict(MOBS[action_id].get("summon_cost", {})) if action_id in MOBS else None
    return None


@dataclass
class ProductionReport:
    ran: dict[str, tuple[int, int]] = field(default_factory=dict)  # building -> (запущено, простаивало)
    newly_broken: dict[str, int] = field(default_factory=dict)  # building -> сколько сломалось в этот ход
    crafted: dict[str, int] = field(default_factory=dict)  # recipe_id -> сколько партий крафта за энергию завершилось
    autocraft_completed: list[str] = field(default_factory=list)  # resource_id фоновых задач, завершившихся в этот ход
    manual_completed: list[str] = field(default_factory=list)  # action_id многоходовых ручных действий, завершившихся в этот ход
    pending_finished: list[str] = field(default_factory=list)  # label отложенных действий, завершившихся в этот ход
    # те же заказы подробно (_report_order_finished): что выдано на склад - для уведомления в UI
    orders_finished: list[dict] = field(default_factory=list)
    habitat_died: dict[str, int] = field(default_factory=dict)  # species_id -> сколько особей умерло от старости в этот ход
    # building_id (тип энергетической машины) -> на сколько энергии её активным
    # job'ам не хватило в этот ход (сумма по всем её job'ам: сколько они МОГЛИ БЫ
    # взять по своему личному лимиту, если бы пул не иссяк, минус сколько реально
    # взяли) - см. _process_energy_jobs. Тип без активных job'ов в этот ход, или
    # которому хватило пула целиком, сюда не попадает вовсе (0 подразумевается).
    # Используется UI (панель "Приоритеты") как живая подсказка "не хватило
    # пропускной способности" - не сохраняется в save.json, ProductionReport
    # существует только в пределах одного advance_turn.
    energy_shortfall: dict[str, int | float] = field(default_factory=dict)
    # Имя босса, чья схватка оборвалась сама - босс "сбежал", потому что игрок не
    # уложился в mobs.*.turn_limit (см. summon_boss/_process_combat) - None в
    # любой другой ход. Ход, на котором игрок САМ добивает/бросает схватку
    # (attack_encounter/abandon_encounter), сюда не попадает - это только про
    # автоматический обрыв по таймеру.
    boss_fled: str | None = None
    # Правила автоматизации (state.automation_rules), сработавшие в этот ход -
    # см. process_automation_rules. Каждый элемент - {"building_id", "kind"
    # ("manual_action"|"energy_recipe"), "target_id", "message"} (message - то
    # же ActionResult.message, что получил бы игрок за обычный клик/запуск
    # рецепта) - для UI-обратной связи в логе хода.
    automation_fired: list[dict] = field(default_factory=list)
    # tech_id технологии, чьё исследование (state.active_research) завершилось
    # именно в этот ход - см. _process_research. None в любой другой ход
    # (включая ход, где research() сама мгновенно завершила тир-1/безапгрейдную
    # технологию без прохода через active_research вовсе).
    research_completed: str | None = None
    # recipe_id ритуалов (ritual_recipes), завершившихся именно в этот ход - см.
    # _process_ritual_jobs. Тот же принцип, что manual_completed.
    ritual_completed: list[str] = field(default_factory=list)
    # resource_id -> сколько единиц выдали репликаторы в этот ход (на склад и в
    # резервы заказов) - см. _process_replicator_jobs.
    replicated: dict[str, int | float] = field(default_factory=dict)
    # Аварии реакторных зон (перегрев зоны выше 100% ёмкости - см.
    # _process_reactors/_roll_reactor_accident), сработавшие именно в этот ход.
    # Каждый элемент - {"instance_id","building_id","zone_index","result","message"} -
    # тот же принцип, что automation_fired (лог для UI, не сохраняется в save.json).
    reactor_accidents: list[dict] = field(default_factory=list)
    # События плавилен за ход (см. _process_smelteries) - {"building_id", "kind"
    # ("melted" | "waiting" | "cast_done"), "recipe_id", "slot"?, "output"?}: для лога и
    # эффектов модалки плавильни (капли в тигель, вспышка готовой отливки).
    smeltery_events: list[dict] = field(default_factory=list)


def _is_active_period_now(active_period: str, daytime: bool) -> bool:
    """Как is_building_active_now, но берёт значение признака напрямую (фенотип
    особи), а не читает его из BUILDINGS. "always" (и любое другое неизвестное
    значение) - активна всегда."""
    if active_period == "day" and not daytime:
        return False
    if active_period == "night" and daytime:
        return False
    return True


def _process_habitat(state: GameState, report: ProductionReport) -> None:
    """Каждый ход, до крафтов за энергию/автокрафта: КАЖДАЯ поселённая особь
    (farm_instance_id не None, её КОНКРЕТНЫЙ экземпляр фермы существует и не
    сломан - см. state.farm_instances/_ensure_farm_instances) пытается
    произвести produces своего вида за consumes - ровно как обычная постройка
    (нехватка consumes НЕ убивает, просто не производит в этот ход). produces
    домножается на фенотип-множитель output_multiplier И на апгрейд ЭТОГО
    экземпляра фермы breeding_output (сильнейший купленный ИМЕННО на нём, не на
    всём типе - см. _farm_instance_upgrade_multiplier), округляется вверх для
    нефракционных ресурсов. active_period фенотипа (день/ночь/всегда) работает
    как у построек (is_building_active_now) - вне своего времени особь просто
    простаивает, не растит turns_alive и не может умереть в этот ход.

    Сразу следом (внутри affordable) - бонусная продукция промышленной пасеки:
    если экземпляр эмулирует среду (_instance_environment_id), совпадающую с
    геном ЭТОЙ особи (phenotype["environment_id"]) - т.е. среда "благоприятная" -
    с вероятностью bonus_chance начисляется bonus_amount её bonus_resource.
    Эти три поля живут на species[species_id] (вкладка «Виды»), НЕ на аллели
    среды - какая среда благоприятна, решает генотип (общий для всех видов
    признак "environment"), а ЧТО именно за это дают - решает вид: два разных
    вида в ОДНОЙ и той же благоприятной среде дают РАЗНЫЕ бонусные ресурсы.
    Бонус независим от обычной продукции вида (свой отдельный бросок, не
    участвует в output_multiplier).

    Иначе turns_alive растёт на 1 (само по себе не опознаёт особь - единственный
    способ опознания это analyze_creature, автоматического опознания по времени
    нет). При достижении lifespan (фенотип) особь умирает от старости и
    убирается из state.creatures ЦЕЛИКОМ - ЕСЛИ у её экземпляра фермы нет
    апгрейда breeding_immortality (по прямому уточнению игрока - это фича
    апгрейда ПРОДВИНУТОЙ фермы, не ген; без него смертны ВСЕ особи независимо
    от генотипа)."""
    if not state.creatures:
        return
    daytime = is_daytime(state)
    to_remove = []
    for creature_id, creature in state.creatures.items():
        farm_id = creature["farm_instance_id"]
        if farm_id is None:
            continue
        instance = state.farm_instances.get(farm_id)
        if instance is None or instance["broken"]:
            continue
        phenotype = creature_phenotype(creature)
        if not _is_active_period_now(phenotype["active_period"], daytime):
            continue
        species = SPECIES.get(phenotype["species_id"], {})
        consumes = species.get("consumes", {})
        affordable = all(state.resources.get(r, 0) >= amount for r, amount in consumes.items())
        if affordable:
            for r, amount in consumes.items():
                state.resources[r] -= amount
            output_mult = phenotype["output_multiplier"] * _farm_instance_upgrade_multiplier(state, farm_id, "breeding_output")
            for r, amount in species.get("produces", {}).items():
                scaled = amount * output_mult
                if not RESOURCE_FRACTIONAL.get(r, False):
                    scaled = math.ceil(scaled)
                _add_resource(state.resources, r, scaled)
            active_env = _instance_environment_id(state, farm_id)
            if active_env is not None and phenotype["environment_id"] == active_env:
                resource = species.get("bonus_resource")
                if resource and random.random() < species.get("bonus_chance", 0):
                    amount = species.get("bonus_amount", 0)
                    if not RESOURCE_FRACTIONAL.get(resource, False):
                        amount = math.ceil(amount)
                    _add_resource(state.resources, resource, amount)
        creature["turns_alive"] += 1
        if not _farm_instance_has_upgrade(state, farm_id, "breeding_immortality") and creature["turns_alive"] >= phenotype["lifespan"]:
            report.habitat_died[phenotype["species_id"]] = report.habitat_died.get(phenotype["species_id"], 0) + 1
            to_remove.append(creature_id)
    for creature_id in to_remove:
        del state.creatures[creature_id]


def _process_soul_collectors(state: GameState, report: ProductionReport, daytime: bool) -> None:
    """Каждый ход, рядом с _process_habitat: КАЖДАЯ вставленная душа (в пределах
    ТЕКУЩЕЙ capacity - см. soul_collector_capacity) производит по своему
    soul_recipes[soul_id] ровно как gain/gain_chance ручного действия (гарантированная
    часть - produces, шансовая - produces_chance, см. _roll_chance_amounts). Душа
    НЕ расходуется и не портится - работает бесконечно, пока не сломана постройка
    (active_period/is_building_active_now той же постройки тоже уважается) или
    пока игрок сам её не вынет (remove_soul). Продукция домножается на сильнейший
    купленный апгрейд типа soul_generation_efficiency ИМЕННО ЭТОГО ЭКЗЕМПЛЯРА
    (тот же принцип, что breeding_output у ферм - см.
    _soul_instance_upgrade_multiplier), округляется по правилам ресурса (см.
    _round_amounts). produces может быть диапазоном (см. _is_range_amount/
    _roll_range_amounts) - бросается отдельно для КАЖДОЙ вставленной души,
    независимо от остальных. Сломанный экземпляр (instance["broken"]) сам по
    себе ничего не гейтит тут явно - его capacity уже 0 (см.
    soul_collector_instance_capacity), поэтому installed[:capacity] естественно
    пропускает все его души."""
    for instance_id, instance in state.soul_collector_instances.items():
        installed = instance["installed"]
        if not installed:
            continue
        building_id = instance["building_id"]
        if not is_building_active_now(building_id, daytime):
            continue
        capacity = soul_collector_instance_capacity(state, instance_id)
        efficiency = _soul_instance_upgrade_multiplier(state, instance_id, "soul_generation_efficiency", default=1.0)
        for soul_id in installed[:capacity]:
            recipe = SOUL_RECIPES.get(soul_id)
            if not recipe:
                continue
            produces = dict(recipe.get("produces", {}))
            chances = recipe.get("produces_chance")
            if chances:
                produces = _roll_chance_amounts(produces, chances)
            if efficiency != 1.0:
                produces = _round_amounts({r: _scale_amount(v, efficiency) for r, v in produces.items()})
            # produces может содержать диапазон (см. _is_range_amount) -
            # разворачивается в конкретное число последним, за эту душу отдельно
            # (каждая душа в слоте бросает НЕЗАВИСИМО от других).
            produces = _roll_range_amounts(produces)
            _gain(state.resources, produces)


def _reactor_cell_catalyst(grid: list[dict | None], cell_index: int) -> dict | None:
    """Катализатор УСТАНОВЛЕННЫЙ в данной ячейке (cell["catalyst_id"]), если
    есть - тот же generic-принцип тега, что RESOURCE_REACTOR_COMPONENT, только
    отдельный слот поверх основного компонента (см. insert_reactor_catalyst)."""
    if not (0 <= cell_index < len(grid)):
        return None
    cell = grid[cell_index]
    if cell is None:
        return None
    catalyst_id = cell.get("catalyst_id")
    if catalyst_id is None:
        return None
    return RESOURCE_REACTOR_CATALYST.get(catalyst_id)


def _reactor_malus_offsets(config: dict | None) -> tuple[tuple[int, int], ...]:
    """Окно, в котором катализатор штрафует чужие ячейки.

    reactor.catalyst_malus_radius (радиус Чебышёва) - если задан, штраф накрывает
    ВЕСЬ квадрат (2r+1)x(2r+1) вокруг катализатора, включая диагонали. Отсутствие
    поля = прежнее поведение, только 4 ортогональные клетки.

    Почему это важно: при штрафе только по ортогонали шахматная раскладка делает
    его бесплатным - у замедлителя в шахматке все ортогональные соседи это
    топливо, а плоский бонус замедлителя прибавляется ПОСЛЕ множителей и потому
    штрафом не задевается вовсе. Диагональное окно ломает этот трюк: соседние по
    диагонали клетки в шахматке - той же роли, и "закатализировать всё" начинает
    бить само по себе."""
    radius = int((config or {}).get("catalyst_malus_radius", 0) or 0)
    if radius <= 0:
        return ((1, 0), (-1, 0), (0, 1), (0, -1))
    return tuple((dr, dc)
                 for dr in range(-radius, radius + 1)
                 for dc in range(-radius, radius + 1)
                 if dr or dc)


def _reactor_effective_component(grid: list[dict | None], width: int, height: int, cell_index: int,
                                 config: dict | None = None) -> dict:
    """heat/output/power/burnup_per_turn ОДНОЙ занятой ячейки решётки С УЧЁТОМ
    катализатора - собственного (self_stat/self_pct: "heat_output" растит heat
    И output топливу, "burnup" снижает расход заряда топливу, "power" растит
    power немодератору-роли; "field" собственные характеристики НЕ трогает -
    он усиливает то, как ячейка радиирует на соседей-топливо, см.
    _reactor_fuel_adjacency_bonus) и ШТРАФА от катализаторов СОСЕДЕЙ
    (neighbor_malus_pct - действует ОДИНАКОВО на heat/output/power, какая бы
    роль у этой ячейки ни была, стакается МУЛЬТИПЛИКАТИВНО по всем
    закатализированным соседям разом; окно соседства ДЛЯ ШТРАФА задаётся
    reactor.catalyst_malus_radius, см. _reactor_malus_offsets - оно НЕ обязано
    совпадать с окном ролевого соседства, которое всегда строго ортогональное).
    ЧИСТАЯ функция - не мутирует grid, не
    путать с _reactor_fuel_adjacency_bonus (та считает бонус СОСЕДСТВА МЕЖДУ
    РОЛЯМИ - moderator/absorber/reflector/shield/fuel - эта же считает "личные"
    характеристики ячейки ДО того, как их читает бонус соседства)."""
    cell = grid[cell_index]
    base = RESOURCE_REACTOR_COMPONENT.get(cell["resource_id"], {})
    heat = base.get("heat", 0.0)
    output = base.get("output", 0.0)
    power = base.get("power", 0.0)
    burnup = base.get("burnup_per_turn", 0.0)

    catalyst = _reactor_cell_catalyst(grid, cell_index)
    if catalyst is not None:
        self_stat = catalyst.get("self_stat")
        pct = catalyst.get("self_pct", 0.0)
        if self_stat == "heat_output":
            # Тепловая сторона может расти МЕДЛЕННЕЕ выходной: self_pct_heat.
            # При равных (дефолт - отсутствие поля) катализатор ratio-нейтрален,
            # то есть в сборке, упёртой в охлаждение, не даёт РОВНО НИЧЕГО - её
            # всё равно приходится додушивать рычагом ровно на столько же.
            # Именно поэтому форсаж был мёртв на всём первом тире.
            heat *= 1 + catalyst.get("self_pct_heat", pct)
            output *= 1 + pct
        elif self_stat == "burnup":
            burnup *= max(0.0, 1 - pct)
        elif self_stat == "power":
            power *= 1 + pct

    malus_mult = 1.0
    row, col = divmod(cell_index, width)
    for dr, dc in _reactor_malus_offsets(config):
        nr, nc = row + dr, col + dc
        if not (0 <= nr < height and 0 <= nc < width):
            continue
        neighbor_catalyst = _reactor_cell_catalyst(grid, nr * width + nc)
        if neighbor_catalyst is not None:
            malus_mult *= max(0.0, 1 - neighbor_catalyst.get("neighbor_malus_pct", 0.0))

    return {
        "heat": heat * malus_mult,
        "output": output * malus_mult,
        "power": power * malus_mult,
        "burnup_per_turn": burnup,
    }


def _reactor_fuel_adjacency_bonus(
    grid: list[dict | None], width: int, height: int, cell_index: int, config: dict,
) -> tuple[float, float, float]:
    """Бонус СОСЕДСТВА для ОДНОЙ fuel-ячейки - считает 4 ортогональных соседа:
    каждый соседний fuel ускоряет ОБА множителя (heat растёт БЫСТРЕЕ output -
    цепная реакция всегда невыгодна по чистому отношению риск/выгода без
    компенсации замедлителями), каждый moderator даёт ПЛОСКИЙ бонус к output
    (пропорционально его resources.*.reactor_component.power, не растит heat),
    каждый absorber снижает ОБА множителя, но РАЗНЫМИ коэффициентами
    (absorber_adjacency_reduction для тепла, absorber_adjacency_output_reduction
    для выхода - при равных он был бы строго хуже щита, см. ниже),
    каждый shield снижает ТОЛЬКО heat_mult (тем же способом, что absorber, но
    output_mult не трогает вовсе - "щит без потери выработки", зеркало
    moderator: тот усиливает output не трогая heat, этот гасит heat не трогая
    output), каждый reflector УМНОЖАЕТ output_mult (а не складывает, как
    moderator) на (1 + power * reflector_adjacency_output_mult_step) И heat_mult
    на (1 + power * reflector_adjacency_heat_mult_step) - несколько отражателей
    вокруг одной ячейки перемножаются между собой, а не складываются; тепловая
    цена отражателя принципиальна - без неё он бесплатный множитель, который
    всегда выгодно ставить максимум - возвращает (heat_mult, output_mult,
    flat_output_bonus). heat_mult не опускается ниже fuel_min_heat_mult -
    ТЕПЛОВОГО ПОЛА стержня (output_mult может обнулиться поглотителями, но
    reflector его дальше только умножает, никогда не компенсирует поглотитель
    обратно в плюс). Коэффициенты -
    content-driven (buildings.*.reactor.*_adjacency_*), не хардкод.
    Теплоноситель (coolant) в соседстве не участвует вовсе - его эффект
    другой: суммарная мощность охлаждения ВСЕЙ зоны, см. _process_reactors."""
    heat_mult = 1.0
    output_mult = 1.0
    flat_output_bonus = 0.0
    row, col = divmod(cell_index, width)
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = row + dr, col + dc
        if not (0 <= nr < height and 0 <= nc < width):
            continue
        n_index = nr * width + nc
        neighbor = grid[n_index]
        if neighbor is None:
            continue
        role = reactor_component_role(neighbor["resource_id"])
        if role == "fuel":
            heat_step = config.get("fuel_adjacency_heat_step", 0.0)
            output_step = config.get("fuel_adjacency_output_step", 0.0)
            field_catalyst = _reactor_cell_catalyst(grid, n_index)
            if field_catalyst is not None and field_catalyst.get("self_stat") == "field":
                # Соседство топлива с топливом само по себе НЕВЫГОДНО: тепла оно
                # добавляет больше, чем выхода (fuel_adjacency_heat_step >
                # fuel_adjacency_output_step). Поэтому катализатор, множащий обе
                # стороны одинаково, лишь усиливает заведомо плохую сделку и не
                # окупается НИКОГДА. self_pct_heat позволяет усилить выходную
                # сторону сильнее тепловой - тогда поле получает собственный
                # смысл: оно локально ЛЕГАЛИЗУЕТ плотную набивку топлива там, где
                # без него она была бы убыточна. Отсутствие поля = обе стороны
                # множатся одинаково, ровно как раньше.
                pct = field_catalyst.get("self_pct", 0.0)
                heat_step *= 1 + field_catalyst.get("self_pct_heat", pct)
                output_step *= 1 + pct
            heat_mult += heat_step
            output_mult += output_step
            continue
        power = _reactor_effective_component(grid, width, height, n_index, config)["power"]
        if role == "moderator":
            flat_output_bonus += config.get("moderator_adjacency_output_bonus", 0.0) * power
        elif role == "absorber":
            # Тепло и выход режутся РАЗНЫМИ коэффициентами: при одинаковых
            # (старое поведение) поглотитель математически всегда строго хуже
            # щита - тот гасит тепло НЕ трогая выход, - и потому был мёртвым
            # контентом. absorber_adjacency_output_reduction по умолчанию равен
            # absorber_adjacency_reduction, т.е. старый контент без нового поля
            # работает ровно как раньше.
            heat_reduction = config.get("absorber_adjacency_reduction", 0.0)
            output_reduction = config.get("absorber_adjacency_output_reduction", heat_reduction)
            heat_mult -= heat_reduction * power
            output_mult -= output_reduction * power
        elif role == "shield":
            heat_mult -= config.get("shield_adjacency_reduction", 0.0) * power
        elif role == "reflector":
            # Отражатель множит и выход, и ТЕПЛО: без платы теплом он был бы
            # бесплатным множителем, который всегда выгодно ставить максимум, -
            # ровно это и схлопывало все схемы в одну "шахматку с отражателями".
            # reflector_adjacency_heat_mult_step по умолчанию 0.0, т.е. старый
            # контент без нового поля работает ровно как раньше.
            output_mult *= 1 + config.get("reflector_adjacency_output_mult_step", 0.0) * power
            heat_mult *= 1 + config.get("reflector_adjacency_heat_mult_step", 0.0) * power
    # ТЕПЛОВОЙ ПОЛ: соседи могут ПРИГЛУШИТЬ нагрев стержня, но не отменить его.
    # Без пола щиты/поглотители складывались линейно и спокойно уводили heat_mult
    # в РОВНЫЙ НОЛЬ (4 улучшенных радиатора или 2 улучшенных поглотителя), причём
    # щит при этом не трогает выработку вовсе - то есть с открытием продвинутых
    # компонентов стержень становился абсолютно холодным при 100% выхода, и вся
    # система охлаждения (теплоносители + расход жидкости) обесценивалась разом.
    # С полом тепло всегда остаётся положительным, а раз оно нигде не убывает
    # само (см. _reactor_zone_next_state), снимать его теплоносителем приходится
    # на ЛЮБОМ тире компонентов - продвинутые дают не отказ от охлаждения, а
    # больший потолок числа стержней. 0.0 по умолчанию = ровно старое поведение.
    min_heat_mult = max(0.0, config.get("fuel_min_heat_mult", 0.0))
    return (max(min_heat_mult, heat_mult), max(0.0, output_mult), flat_output_bonus)


def _roll_reactor_accident(entries: list[dict]) -> dict | None:
    """Буквальная копия _roll_loot_case_prize (см. её докстринг) над
    {"result", "weight", "message"} вместо {"resource", "amount", "weight"} -
    "result" тут закрытый хардкод-enum движка (_apply_reactor_accident_result),
    не ссылка на ресурс."""
    total_weight = sum(e.get("weight", 1) for e in entries)
    if total_weight <= 0:
        return None
    roll = random.uniform(0, total_weight)
    upto = 0.0
    for entry in entries:
        upto += entry.get("weight", 1)
        if roll <= upto:
            return entry
    return entries[-1]  # подстраховка на случай погрешности плавающей точки


def _apply_reactor_accident_result(state: GameState, instance_id: str, zone_index: int, layout: list[int], result: str) -> None:
    """Применяет ИСХОД аварии (result - закрытый хардкод-enum, интерпретируется
    ТОЛЬКО тут, контент лишь ссылается на него по имени, см.
    tools/content_validation.py:_REACTOR_ACCIDENT_RESULTS):
    - "destroy_one_fuel_cell": одна СЛУЧАЙНАЯ fuel-ячейка ЭТОЙ зоны очищается
      (без возврата ресурса игроку - авария, не разбор, в отличие от
      remove_reactor_component);
    - "destroy_zone_grid": ВСЕ ячейки ЭТОЙ зоны очищаются целиком.
    Неизвестный result (гипотетический рассинхрон с content_validation.py) -
    молча ничего не делает, без падения."""
    instance = state.reactor_instances[instance_id]
    grid = instance["grid"]
    zone_cells = [i for i, z in enumerate(layout) if z == zone_index and i < len(grid)]
    if result == "destroy_zone_grid":
        for i in zone_cells:
            grid[i] = None
    elif result == "destroy_one_fuel_cell":
        fuel_cells = [
            i for i in zone_cells
            if grid[i] is not None and reactor_component_role(grid[i]["resource_id"]) == "fuel"
        ]
        if fuel_cells:
            grid[random.choice(fuel_cells)] = None


def _reactor_zone_raw_totals(
    grid: list[dict | None], width: int, height: int, layout: list[int], zone_count: int, config: dict,
) -> tuple[list[float], list[float], list[int], list[float]]:
    """Сырые heat/output/охлаждение КАЖДОЙ зоны по ТЕКУЩЕМУ состоянию решётки -
    ЧИСТАЯ функция (не списывает burnup, не трогает state) - используется и
    реальной обработкой хода (_process_reactors, которая burnup списывает
    ОТДЕЛЬНЫМ проходом сразу после вызова этой функции), и прогнозом телеметрии
    (reactor_zone_projection) - расчёт один и тот же, прогноз не может разойтись
    с тем, что реально произойдёт в конце хода. Возвращает (raw_heat,
    raw_output, zone_cell_count, coolant_power) - по списку на зону."""
    raw_heat = [0.0] * zone_count
    raw_output = [0.0] * zone_count
    zone_cell_count = [0] * zone_count
    coolant_power = [0.0] * zone_count
    for cell_index, cell in enumerate(grid):
        if cell_index >= len(layout):
            break  # решётка успела вырасти позже последнего пересчёта зон - лишние ячейки временно вне зон
        zone_idx = layout[cell_index]
        zone_cell_count[zone_idx] += 1
        if cell is None:
            continue
        role = reactor_component_role(cell["resource_id"])
        if role == "coolant":
            coolant_power[zone_idx] += _reactor_effective_component(grid, width, height, cell_index, config)["power"]
            continue
        if role != "fuel":
            continue
        charge = cell.get("charge")
        if charge is not None and charge <= 0:
            continue  # выгорело - простаивает, ждёт замены
        eff = _reactor_effective_component(grid, width, height, cell_index, config)
        heat_mult, output_mult, flat_bonus = _reactor_fuel_adjacency_bonus(grid, width, height, cell_index, config)
        raw_heat[zone_idx] += eff["heat"] * heat_mult
        raw_output[zone_idx] += eff["output"] * output_mult + flat_bonus
    return raw_heat, raw_output, zone_cell_count, coolant_power


def reactor_coolant_fluid_config(resource_id: str | None) -> dict | None:
    """Настройки охлаждающей ЖИДКОСТИ (resources.*.reactor_coolant_fluid) - тот
    же generic-принцип тега, что soul_kind/reactor_component: новую жидкость
    достаточно завести тегом в контенте, движок её подхватит сам."""
    if resource_id is None:
        return None
    return RESOURCE_REACTOR_COOLANT_FLUID.get(resource_id)


def _reactor_coolant_plan(
    fluid_id: str | None, raw_coolant_power: float, flow_pct: float, available: float,
) -> tuple[float, float, float]:
    """Сколько жидкости ЗАПРОСИТ зона за ход и какая мощность охлаждения из
    этого реально получится - ЧИСТАЯ функция (ничего не списывает), поэтому её
    одинаково используют и реальный ход, и прогноз телеметрии.

    Возвращает (effective_power, units_needed, units_used):
    * units_needed = мощность теплоносителей x доля потока x units_per_power
      жидкости - расход зависит от ПРОКАЧКИ, а не от того, сколько тепла реально
      снялось (гонять пустой контур так же накладно);
    * если жидкости в запасе меньше нужного, охлаждение срабатывает ЧАСТИЧНО,
      пропорционально доступному - это мягче, чем обнулять его целиком, и даёт
      игроку ход-другой заметить, что бак пуст;
    * жидкость не выбрана или неизвестна -> охлаждения нет вовсе (теплоносители
      в решётке без жидкости - просто занятые клетки).
    """
    if raw_coolant_power <= 0:
        return 0.0, 0.0, 0.0
    if not RESOURCE_REACTOR_COOLANT_FLUID:
        # В контенте вообще нет охлаждающих жидкостей - значит механика расходника
        # не заведена, и теплоносители работают как раньше, без топлива для себя.
        # Это делает всю фичу ОПТ-ИН через контент и не ломает ни старые
        # сохранения, ни чужой контент без неё.
        return raw_coolant_power, 0.0, 0.0
    cfg = reactor_coolant_fluid_config(fluid_id)
    if cfg is None:
        return 0.0, 0.0, 0.0
    frac = max(0.0, min(float(flow_pct), 100.0)) / 100.0
    needed = raw_coolant_power * frac * cfg.get("units_per_power", 0.0)
    if needed <= 0:
        return raw_coolant_power * cfg.get("cooling_mult", 1.0), 0.0, 0.0
    used = min(needed, max(0.0, available))
    ratio = used / needed
    return raw_coolant_power * cfg.get("cooling_mult", 1.0) * ratio, needed, used


def _reactor_zone_next_state(
    zone: dict, raw_heat: float, raw_output: float, coolant_power: float,
    cell_count: int, heat_cap_per_cell: float, soft_pct: float,
    heat_damp_exponent: float = 1.0,
) -> tuple[float, float, float, bool]:
    """ОДИН шаг хода ОДНОЙ зоны - демпфирует raw_heat/raw_output рычагом
    control_rod_depth (`эффективное = сырое * (1 - depth/100*0.9)` - даже на
    100% остаётся фон, полностью заглушить нельзя одним рычагом), вычитает из
    накопленного зоной тепла охлаждение (coolant_power * coolant_flow/100),
    выше soft_pct от ёмкости зоны (cell_count * heat_cap_per_cell) линейно
    гасит output. ЧИСТАЯ функция (не мутирует zone/state, не бросает
    аварию/не сбрасывает heat - это решает вызывающий) - используется и
    реальной обработкой хода, и прогнозом телеметрии. Возвращает (new_heat,
    ratio, effective_output, would_trip) - would_trip = ratio достигла 100%
    (сам факт срабатывания детерминирован, в отличие от того, КАКАЯ авария
    выпадет - см. _roll_reactor_accident).

    heat_damp_exponent (reactor.control_rod_heat_damp_exponent) - НАСКОЛЬКО
    сильнее рычаг душит тепло, чем выработку: тепло множится на damp в этой
    степени, выход - всегда на сам damp. При 1.0 (дефолт) оба падают ровно
    одинаково, и тогда погружение - чистая потеря темпа без всякой отдачи.
    При значении больше 1 глубина УЛУЧШАЕТ отношение тепла к выходу, то есть
    освобождает тепловой запас, который можно обменять на клетки под топливо
    или на экономию охлаждающей жидкости - только так рычаг становится
    предметом выбора, а не аварийным тормозом."""
    damp = 1 - (zone.get("control_rod_depth", 0) / 100) * 0.9
    effective_heat = raw_heat * (damp ** heat_damp_exponent if heat_damp_exponent != 1.0 else damp)
    effective_output = raw_output * damp
    removed = coolant_power * (zone.get("coolant_flow", 100) / 100)
    new_heat = max(0.0, zone["heat"] + effective_heat - removed)

    cap = cell_count * heat_cap_per_cell
    ratio = new_heat / cap if cap > 0 else 0.0
    if ratio > soft_pct:
        span = max(1.0 - soft_pct, 1e-9)
        falloff = min(1.0, (ratio - soft_pct) / span)
        effective_output *= max(0.0, 1.0 - falloff)
    would_trip = cap > 0 and ratio >= 1.0
    return new_heat, ratio, effective_output, would_trip


def reactor_zone_projection(state: GameState, instance_id: str) -> list[dict] | None:
    """Прогноз на СЛЕДУЮЩИЙ ход по каждой зоне реактора - ТОЛЬКО если у
    экземпляра куплена телеметрия (reactor_telemetry_available), иначе None
    (нет смысла отдавать данные, которые UI всё равно не покажет). Полностью
    детерминирован при условии, что решётка/рычаги/поток не изменятся и
    постройка останется активна ещё один ход - переиспользует ТЕ ЖЕ самые
    _reactor_zone_raw_totals/_reactor_zone_next_state, что и реальная
    обработка хода (_process_reactors), поэтому прогноз не может разойтись с
    тем, что случится по факту. НЕ мутирует state (burnup/heat/аварийный
    бросок остаются на _process_reactors) - чистое чтение."""
    if not reactor_telemetry_available(state, instance_id):
        return None
    return _reactor_zone_forecast(state, instance_id)


def _reactor_zone_forecast(state: GameState, instance_id: str, fluid_stock: float | None = None) -> list[dict] | None:
    """Прогноз следующего хода по зонам (см. reactor_zone_projection) без проверки
    телеметрии - для подсчёта притока энергии и расхода жидкости (passive_flows).
    fluid_stock - сколько жидкости достанется этому реактору (по умолчанию весь склад)."""
    instance = state.reactor_instances.get(instance_id)
    if instance is None:
        return None
    config = BUILDINGS.get(instance["building_id"], {}).get("reactor")
    if not config:
        return None
    _ensure_reactor_grid(state, instance_id)
    _ensure_reactor_zones(state, instance_id)
    grid = instance["grid"]
    zones = instance["zones"]
    width, height = reactor_grid_dims(state, instance_id)
    layout = _reactor_zone_layout(width, height, len(zones))
    raw_heat, raw_output, zone_cell_count, coolant_power = _reactor_zone_raw_totals(
        grid, width, height, layout, len(zones), config,
    )
    heat_cap_per_cell = config.get("heat_cap_per_cell", 0.0)
    soft_pct = min(config.get("soft_threshold_pct", 1.0), 1.0)

    fluid_id = instance.get("coolant_fluid")
    stock = fluid_stock if fluid_stock is not None else (state.resources.get(fluid_id, 0) if fluid_id else 0)
    result = []
    for zone_idx, zone in enumerate(zones):
        # Тот же расчёт жидкости, что и в реальном ходу (включая порядок зон по
        # общему пулу), иначе прогноз разошёлся бы с фактом при нехватке бака.
        eff_power, needed, used = _reactor_coolant_plan(
            fluid_id, coolant_power[zone_idx], zone.get("coolant_flow", 100), stock,
        )
        stock -= used
        new_heat, ratio, effective_output, would_trip = _reactor_zone_next_state(
            zone, raw_heat[zone_idx], raw_output[zone_idx], eff_power,
            zone_cell_count[zone_idx], heat_cap_per_cell, soft_pct,
            config.get("control_rod_heat_damp_exponent", 1.0),
        )
        result.append({
            "zone_index": zone_idx,
            "next_heat": 0.0 if would_trip else new_heat,
            "next_ratio": 0.0 if would_trip else ratio,
            "next_output": effective_output,
            "will_trip": would_trip,
            "fluid_needed": needed,
            "fluid_used": used,
            "fluid_short": needed - used > 1e-9,
        })
    return result


def _process_reactors(state: GameState, report: ProductionReport, daytime: bool) -> None:
    """Каждый ход, сразу после _process_soul_collectors (тот же принцип - обе
    работают ПО ЭКЗЕМПЛЯРУ и производят раньше _process_energy_jobs/*, чтобы их
    выход был доступен энергоконвертерам в тот же ход, как и у обычных
    генераторов): для КАЖДОЙ рабочей (не сломанной, is_building_active_now)
    решётки - сырые heat/output каждой активной fuel-ячейки (адjacency-бонус,
    см. _reactor_fuel_adjacency_bonus, суммируются _reactor_zone_raw_totals)
    демпфируются рычагом control_rod_depth и охлаждением ЭТОЙ зоны
    (_reactor_zone_next_state - та же функция, что использует прогноз
    телеметрии, см. reactor_zone_projection). При достижении 100% ёмкости зоны -
    бросок по таблице аварий (accident_table) и сброс накопленного тепла зоны в
    0 независимо от того, произошла ли авария вообще (порог 100% сам по себе -
    момент разрядки, не только точка входа в рулетку). Топливо расходует
    burnup_per_turn НЕЗАВИСИМО от глубины стержней (демпинг снижает выход
    реакции, но не защищает сам стержень от выгорания) - выгоревшая
    (charge <= 0) ячейка простаивает (не производит, не тратит заряд), пока
    игрок не заменит её вручную (remove_reactor_component/
    insert_reactor_component). Суммарный эффективный output ВСЕХ зон одного
    экземпляра идёт в общий пул энергии ОДНИМ начислением (энергия дробная -
    ceil не нужен, в отличие от большинства остальных ресурсов)."""
    for instance_id, instance in state.reactor_instances.items():
        building_id = instance["building_id"]
        if instance["broken"] or not is_building_active_now(building_id, daytime):
            continue
        config = BUILDINGS.get(building_id, {}).get("reactor")
        if not config:
            continue
        _ensure_reactor_grid(state, instance_id)
        _ensure_reactor_zones(state, instance_id)
        grid = instance["grid"]
        zones = instance["zones"]
        width, height = reactor_grid_dims(state, instance_id)
        layout = _reactor_zone_layout(width, height, len(zones))
        heat_cap_per_cell = config.get("heat_cap_per_cell", 0.0)
        soft_pct = min(config.get("soft_threshold_pct", 1.0), 1.0)

        raw_heat, raw_output, zone_cell_count, coolant_power = _reactor_zone_raw_totals(
            grid, width, height, layout, len(zones), config,
        )

        # Burnup - ОТДЕЛЬНЫЙ проход, ПОСЛЕ сбора raw-тоталов (те читают charge
        # до его списания в этом ходу) - та же граница "активна fuel-ячейка",
        # что и в _reactor_zone_raw_totals, продублирована намеренно: список
        # активных ячеек там не сохраняется наружу, чтобы не плодить лишнее
        # состояние ради одного прохода.
        burnup_factor = config.get("control_rod_burnup_factor", 0.0)
        for cell_index, cell in enumerate(grid):
            if cell is None:
                continue
            if reactor_component_role(cell["resource_id"]) != "fuel":
                continue
            charge = cell.get("charge")
            if charge is None or charge <= 0:
                continue
            burnup = _reactor_effective_component(grid, width, height, cell_index, config)["burnup_per_turn"]
            if burnup_factor:
                # Стержни ПРИТОРМАЖИВАЮТ реакцию, а значит и расход топлива:
                # при factor=1 заряд тает ровно в том же демпфированном темпе,
                # в каком зона выдаёт энергию, поэтому глубина перестаёт быть
                # чистой потерей и становится дросселем "медленнее, но дольше".
                # factor=0 (или отсутствие поля) = прежнее поведение, когда
                # заряд тратился независимо от рычага.
                zone_idx = layout[cell_index] if cell_index < len(layout) else 0
                damp = 1 - (zones[zone_idx].get("control_rod_depth", 0) / 100) * 0.9
                burnup *= (1 - burnup_factor) + burnup_factor * damp
            cell["charge"] = max(0, charge - burnup)

        fluid_id = instance.get("coolant_fluid")
        total_output = 0.0
        for zone_idx, zone in enumerate(zones):
            # Жидкость - ОБЩИЙ пул на все зоны: зоны берут её по порядку, тот же
            # осознанный компромисс "кто раньше встал, тот раньше взял", что и
            # везде в advance_turn для разделяемых ресурсов.
            eff_power, _needed, used = _reactor_coolant_plan(
                fluid_id, coolant_power[zone_idx], zone.get("coolant_flow", 100),
                state.resources.get(fluid_id, 0) if fluid_id else 0,
            )
            if used > 0:
                _add_resource(state.resources, fluid_id, -used)
            new_heat, ratio, effective_output, would_trip = _reactor_zone_next_state(
                zone, raw_heat[zone_idx], raw_output[zone_idx], eff_power,
                zone_cell_count[zone_idx], heat_cap_per_cell, soft_pct,
                config.get("control_rod_heat_damp_exponent", 1.0),
            )
            zone["heat"] = new_heat
            if would_trip:
                accident_table = REACTOR_ACCIDENTS.get(config.get("accident_table"), {})
                entry = _roll_reactor_accident(accident_table.get("entries", []))
                if entry:
                    _apply_reactor_accident_result(state, instance_id, zone_idx, layout, entry.get("result"))
                    report.reactor_accidents.append({
                        "instance_id": instance_id, "building_id": building_id,
                        "zone_index": zone_idx, "result": entry.get("result"),
                        "message": entry.get("message", ""),
                    })
                zone["heat"] = 0.0
            total_output += effective_output

        if total_output:
            _add_resource(state.resources, ENERGY_RESOURCE_ID, total_output)


def energy_reserved(state: GameState) -> float:
    """Сколько накопленной энергии НЕ отдавать преобразователям: её копят заказы,
    которым нужна энергия (order["energy_reserve"] - самый большой разовый платёж их
    плана). Иначе преобразователь, который тратит энергию последним в ходу, каждый ход
    съедал бы то, что не дотянуло до платежа, и ожидание не кончилось бы (прямое
    пожелание: преобразователь берёт только излишки и не тормозит остальные процессы)."""
    return sum(order.get("energy_reserve", 0) for order in state.orders)


def _process_energy_jobs(state: GameState, report: ProductionReport) -> None:
    """Каждый работающий крафт за энергию (state.energy_jobs) тянет из ОБЩЕГО пула
    энергии СВОЙ ЛИЧНЫЙ лимит за ход (energy_converter_max_energy_per_turn - лимит
    ОДНОГО преобразователя ЕГО ТИПА (job["building_id"]), не общий на все типы
    разом), пока не наберёт свой порог - ИМЕННО так два одновременных крафта на
    двух преобразователях (в т.ч. РАЗНЫХ типов) идут параллельно (каждый получает
    полный личный лимит, если в общем пуле хватает на обоих), а не делят один
    суммарный лимит, из-за чего первый забирал бы всё себе. В отличие от старого
    energy_craft, партия ОДНОРАЗОВАЯ: как только рецепт готов, он выдаёт produces
    и УБИРАЕТСЯ из списка (освобождая преобразователь для следующего рецепта) -
    остаток энергии сверх порога у завершённой задачи не переносится.

    Порядок обработки - по ТИПАМ (не по отдельным job'ам): типы с БОЛЬШИМ
    energy_priority (см. CLAUDE.md, "Приоритеты") тянут из общего пула ПЕРВЫМИ,
    при равном приоритете - в порядке BUILDINGS/content.json (детерминированно,
    не порядок вставки в state.energy_jobs). Внутри одного типа job'ы
    по-прежнему обрабатываются в порядке запуска (FIFO) - это решает только споры
    ЗА САМ общий пул, если энергии не хватает на всех разом. Каждый job тянет НЕ
    БОЛЬШЕ, чем ему реально нужно до завершения (min с threshold - progress) -
    излишек сверх этого остаётся в пуле для следующего job'а в тот же ход, а не
    расходуется впустую. report.energy_shortfall копит по каждому ТИПУ, на
    сколько энергии его job'ам не хватило из-за исчерпания пула (не из-за
    собственного лимита - если тип получил ровно свой полный личный лимит,
    это НЕ shortfall, так и задумано)."""
    if not state.energy_jobs:
        return
    # Сохранения до появления нескольких типов энергетических машин не хранили
    # building_id у job'а (тип был один-единственный) - при первой же обработке
    # после загрузки такого сохранения приписываем такой job ПЕРВОМУ работающему
    # типу с energy_converter, чтобы не падать и не терять задачу молча.
    if any(job.get("building_id") is None for job in state.energy_jobs):
        fallback_types = energy_converter_building_types(state)
        fallback = fallback_types[0] if fallback_types else None
        for job in state.energy_jobs:
            if job.get("building_id") is None:
                job["building_id"] = fallback

    by_type: dict[str, list[dict]] = {}
    for job in state.energy_jobs:
        by_type.setdefault(job["building_id"], []).append(job)

    building_order = {bid: i for i, bid in enumerate(BUILDINGS)}
    ordered_types = sorted(
        by_type.keys(), key=lambda bid: (-energy_priority(state, bid), building_order.get(bid, 0)),
    )

    stock = state.resources.get(ENERGY_RESOURCE_ID, 0)
    # То, что копят ожидающие энергию (energy_reserved), преобразователям не отдаём.
    reserved = min(stock, energy_reserved(state))
    available = stock - reserved
    remaining_jobs = []
    for building_id in ordered_types:
        per_job_cap = energy_converter_max_energy_per_turn(state, building_id)
        for job in by_type[building_id]:
            recipe = ENERGY_RECIPES[job["recipe_id"]]
            needed = recipe["threshold"] - job["progress"]
            desired = needed if per_job_cap is None else min(per_job_cap, needed)
            draw = min(available, desired)
            if draw > 0:
                available -= draw
                job["progress"] += draw
            shortfall = desired - draw
            if shortfall > 0:
                report.energy_shortfall[building_id] = report.energy_shortfall.get(building_id, 0) + shortfall
            if job["progress"] >= recipe["threshold"]:
                # produces рецепта может быть диапазоном (см. _is_range_amount) -
                # бросается отдельно для каждого завершившегося job'а.
                _deliver_job_output(state, job, _roll_range_amounts(recipe.get("produces", {})))
                report.crafted[job["recipe_id"]] = report.crafted.get(job["recipe_id"], 0) + 1
            else:
                remaining_jobs.append(job)
    state.resources[ENERGY_RESOURCE_ID] = available + reserved
    state.energy_jobs = remaining_jobs


def _deliver_job_output(state: GameState, job: dict, produced: dict) -> None:
    """Результат партии (многоходового действия, крафта за энергию или репликации): партия
    заказа (job["order_id"]) кладёт его в резерв заказа и двигает его шаг, остальные - на
    склад. Шаг многоходового действия считает партии, шаги крафта за энергию и
    репликации - единицы ресурса."""
    order = _find_order(state, job.get("order_id")) if job.get("order_id") is not None else None
    if order is None:
        _gain(state.resources, produced)
        return
    _gain(order["reserved"], produced)
    for step in order["steps"]:
        if step["resource_id"] == job.get("order_resource") and step["fires_done"] < step["fires_total"]:
            if step["kind"] == "long":
                step["fires_done"] += 1
            elif step["kind"] == "replicate":
                step["fires_done"] += produced.get(step["resource_id"], 0)
            else:
                step["fires_done"] += _energy_step_progress(step, produced)
            break


def _pay_from_order(state: GameState, order: dict, cost: dict, times: int) -> None:
    """Списывает cost*times: предметы - из резерва заказа, энергию - со склада."""
    for ing_id, amount in cost.items():
        pool = state.resources if ing_id == ENERGY_RESOURCE_ID else order["reserved"]
        pool[ing_id] = pool.get(ing_id, 0) - amount * times
        if pool[ing_id] < 0:
            pool[ing_id] = 0  # только дрейф float у дробной энергии


def _affordable_times(state: GameState, order: dict, cost: dict, limit) -> int:
    """Сколько раз (не больше limit) заказ может оплатить cost прямо сейчас."""
    times = limit
    for ing_id, amount in cost.items():
        if amount <= 0:
            continue
        pool = state.resources if ing_id == ENERGY_RESOURCE_ID else order["reserved"]
        have = pool.get(ing_id, 0)
        if RESOURCE_FRACTIONAL.get(ing_id, False):
            have += 1e-9  # дрейф float у дробных (энергия): 1.0 // 0.1 == 9.0
        times = min(times, int(have // amount))
    return max(0, int(times))


def _short_of_energy(state: GameState, order: dict, cost: dict) -> bool:
    """Упирается ли оплата ОДНОЙ прокрутки именно в энергию (а не в предметы)."""
    items_ok = all(order["reserved"].get(r, 0) >= a for r, a in cost.items() if r != ENERGY_RESOURCE_ID)
    return items_ok and state.resources.get(ENERGY_RESOURCE_ID, 0) < cost.get(ENERGY_RESOURCE_ID, 0)


def _advance_order_step(state: GameState, order: dict, step: dict, budgets: dict) -> tuple[str | None, bool]:
    """Двигает один шаг заказа в этом ходу. Возвращает (причина простоя для игрока или
    None, простой из-за энергии). Ингредиенты берутся из резерва заказа, энергия - со
    склада; результат - в резерв. Сначала работают сборщики локации (мгновенно, при
    любой длительности рецепта, в пределах их лимита на ход), затем - «Автоматизация»
    механизма (как игрок: 1 крафт за ход, многоходовой рецепт - партией своей
    длительности). budgets - остатки лимитов на этот ход: (локация, "asm"/"mech")."""
    if step["kind"] == "energy":
        return _advance_energy_step(state, order, step, budgets)
    if step["kind"] == "ritual":
        return _advance_ritual_step(state, order, step, budgets)
    if step["kind"] == "replicate":
        return _advance_replicate_step(state, order, step)

    action_id = step["action_id"]
    action = MANUAL_ACTIONS[action_id]
    location = action.get("location")
    if not is_manual_action_unlocked(action_id, state):
        building = action_mechanism_building(action)
        name = BUILDINGS[building]["name"] if building in BUILDINGS else action["name"]
        return f"механизм «{name}» не работает", False
    asm_key, mech_key = (location, "asm"), (location, "mech")
    if asm_key not in budgets:
        budgets[asm_key] = location_assembler_capacity(state, location)
        budgets[mech_key] = 1 if mechanism_automated(state, location) else 0
    if location_autocraft_capacity(state, location) == 0:
        return f"нет автоматизации: «{LOCATIONS.get(location, {}).get('name', location)}»", False
    cost, gain = effective_manual_action_stats(action_id, state)
    remaining = step["fires_total"] - step["fires_done"]

    # 1) Сборщики: мгновенно, длительность рецепта не важна.
    assemblers = budgets[asm_key]
    if assemblers != 0 and remaining > 0:
        limit = remaining if assemblers is None else min(remaining, assemblers)
        times = _affordable_times(state, order, cost, limit)
        if times > 0:
            _pay_from_order(state, order, cost, times)
            for out_id, amount in gain.items():
                _add_resource(order["reserved"], out_id, amount * times)
            step["fires_done"] += times
            remaining -= times
            if assemblers is not None:
                budgets[asm_key] = assemblers - times
        elif _short_of_energy(state, order, cost):
            return "ждёт энергию", True
    if remaining <= 0 or budgets[mech_key] <= 0:
        return None, False  # готово, либо лимит на этот ход исчерпан

    # 2) «Автоматизация» механизма: как игрок у механизма.
    if step["kind"] == "long":
        job = state.manual_jobs.get(action_id)
        if job is not None and job.get("order_id") == order["id"]:
            return None, False  # партия заказа уже идёт
        busy = _busy_manual_job_in_location(state.manual_jobs, action_id)
        if busy is not None:
            return f"механизм занят: «{MANUAL_ACTIONS[busy]['name']}»", False
        if _affordable_times(state, order, cost, 1) < 1:
            return ("ждёт энергию", True) if _short_of_energy(state, order, cost) else (None, False)
        _pay_from_order(state, order, cost, 1)
        state.manual_jobs[action_id] = _new_manual_job(
            action_id, manual_action_duration(action_id, state), cost, gain,
            autocraft=True, order_id=order["id"], order_resource=step["resource_id"],
        )
        budgets[mech_key] -= 1
        return None, False
    if _affordable_times(state, order, cost, 1) < 1:
        return ("ждёт энергию", True) if _short_of_energy(state, order, cost) else (None, False)
    _pay_from_order(state, order, cost, 1)
    for out_id, amount in gain.items():
        _add_resource(order["reserved"], out_id, amount)
    step["fires_done"] += 1
    budgets[mech_key] -= 1
    return None, False


def _advance_energy_step(state: GameState, order: dict, step: dict, budgets: dict) -> tuple[str | None, bool]:
    """Шаг «крафт за энергию». Сборщик локации преобразователя - мгновенно: платит
    порог энергии рецепта разом со склада и выдаёт результат в этот же ход (в пределах
    своего лимита). Иначе - партии в слотах автоматизированных преобразователей, как
    игрок (энергия копится по ходам, см. _process_energy_jobs)."""
    recipe = ENERGY_RECIPES[step["recipe_id"]]
    machine = recipe.get("machine")
    consumes = recipe.get("consumes", {})
    per_batch = _energy_step_per_fire(step)
    asm_key = (CONVERTER_LOCATION_ID, "asm")
    if asm_key not in budgets:
        budgets[asm_key] = location_assembler_capacity(state, CONVERTER_LOCATION_ID)
    batch_cost = dict(consumes)
    batch_cost[ENERGY_RESOURCE_ID] = batch_cost.get(ENERGY_RESOURCE_ID, 0) + recipe["threshold"]
    batches_this_call = 0
    while step["fires_done"] < step["fires_total"] and budgets[asm_key] != 0:
        if per_batch <= 0 and batches_this_call >= 1:
            break  # выход партии не гарантирован - не больше одной за раз
        if _affordable_times(state, order, batch_cost, 1) < 1:
            if _short_of_energy(state, order, batch_cost):
                return "ждёт энергию", True
            return None, False
        _pay_from_order(state, order, batch_cost, 1)
        produced = _roll_range_amounts(recipe.get("produces", {}))
        _gain(order["reserved"], produced)
        step["fires_done"] += _energy_step_progress(step, produced)
        batches_this_call += 1
        if budgets[asm_key] is not None:
            budgets[asm_key] -= 1
    if step["fires_done"] >= step["fires_total"]:
        return None, False

    running = [
        j for j in state.energy_jobs
        if j.get("order_id") == order["id"] and j.get("order_resource") == step["resource_id"]
    ]
    remaining = step["fires_total"] - step["fires_done"] - len(running) * per_batch
    while remaining > 0 and (per_batch > 0 or not running):
        building_id = _pick_converter_building_with_free_slot(state, state.energy_jobs, machine, automated_only=True)
        if building_id is None:
            if not automated_converter_types(state, machine) and location_assembler_capacity(state, CONVERTER_LOCATION_ID) == 0:
                return "нет автоматизации преобразователя", False
            return None, False  # слоты заняты или лимит сборщиков исчерпан - очередь
        if _affordable_times(state, order, consumes, 1) < 1:
            return None, False  # ингредиенты ещё делаются нижними шагами
        _pay_from_order(state, order, consumes, 1)
        job = {"recipe_id": step["recipe_id"], "progress": 0, "building_id": building_id,
               "order_id": order["id"], "order_resource": step["resource_id"]}
        if consumes:
            job["cost"] = dict(consumes)  # вернуть в резерв при отмене (cancel_energy_job)
        state.energy_jobs.append(job)
        running.append(job)
        if per_batch <= 0:
            break
        remaining -= per_batch
    return None, False


def _advance_ritual_step(state: GameState, order: dict, step: dict, budgets: dict) -> tuple[str | None, bool]:
    """Шаг «ритуал»: его проводят только сборщики алтаря - мгновенно, в пределах их
    лимита на ход, без хода у самого алтаря (state.ritual_jobs не занимается, алтарь
    свободен для игрока и автоматизатора) и без extra_consumes_per_turn - сборщик
    пропускает его вместе с длительностью ритуала. Ритуал детерминирован (иначе он не
    попал бы в заказ), так что шаг считает сами ритуалы."""
    ritual = RITUAL_RECIPES[step["recipe_id"]]
    building_id = ritual["requires_building"]
    altar_name = BUILDINGS.get(building_id, {}).get("name", building_id)
    if not is_ritual_recipe_available(step["recipe_id"], state):
        return f"алтарь «{altar_name}» не работает или не хватает пьедесталов", False
    asm_key = (("building", building_id), "asm")  # не пересекается с ключами локаций
    if asm_key not in budgets:
        budgets[asm_key] = building_assembler_capacity(state, building_id)
    assemblers = budgets[asm_key]
    if assemblers == 0:
        if building_assembler_capacity(state, building_id) == 0:
            return f"нет сборщика ритуалов: «{altar_name}»", False
        return None, False  # лимит сборщиков на этот ход исчерпан
    cost = _ritual_full_cost(ritual)
    remaining = step["fires_total"] - step["fires_done"]
    limit = remaining if assemblers is None else min(remaining, assemblers)
    times = _affordable_times(state, order, cost, limit)
    if times <= 0:
        return ("ждёт энергию", True) if _short_of_energy(state, order, cost) else (None, False)
    _pay_from_order(state, order, cost, times)
    for out_id, amount in ritual["produces"].items():
        _add_resource(order["reserved"], out_id, amount * times)
    step["fires_done"] += times
    if assemblers is not None:
        budgets[asm_key] = assemblers - times
    return None, False


def _advance_replicate_step(state: GameState, order: dict, step: dict) -> tuple[str | None, bool]:
    """Шаг «репликация»: как игрок у репликатора - в свободном слоте репликатора с
    «Автоматизацией» запускается ОДНА репликация на все ещё не заказанные единицы,
    материя списывается из резерва заказа, ресурс приходит в резерв по мере переработки
    (_process_replicator_jobs, шаг считает единицы)."""
    resource_id = step["resource_id"]
    running = sum(
        j["qty"] - j["delivered"] for j in state.replicator_jobs
        if j.get("order_id") == order["id"] and j.get("order_resource") == resource_id
    )
    remaining = step["fires_total"] - step["fires_done"] - running
    if remaining <= 0:
        return None, False  # всё уже реплицируется
    building_id = _pick_replicator_with_free_slot(state, automated_only=True)
    if building_id is None:
        if _autocraft_replicator_building(state) is None:
            return "нет автоматизации репликатора", False
        return None, False  # слоты заняты - очередь
    matter = replicator_matter(building_id)
    have = order["reserved"].get(matter, 0)
    units = remaining
    cost = replication_matter_cost(resource_id, units, matter)
    if have + 1e-9 < cost:
        # материи в резерве меньше, чем нужно на остаток (например, её вернула отменённая
        # репликация частично) - запускаем, на сколько хватает
        price = RESOURCE_REPLICATION_COST[resource_id]
        units = int((have + 1e-9) // price)
        while units > 0 and replication_matter_cost(resource_id, units, matter) > have + 1e-9:
            units -= 1
        if units <= 0:
            return f"не хватает материи: «{RESOURCE_NAMES.get(matter, matter)}»", False
        cost = replication_matter_cost(resource_id, units, matter)
    order["reserved"][matter] = max(0, round(have - cost, 9))
    _new_replicator_job(state, building_id, resource_id, units, cost, order_id=order["id"], order_resource=resource_id)
    return None, False


def _process_orders(state: GameState, report: ProductionReport, budgets: dict) -> None:
    """Каждый ход двигает заказы (state.orders) по порядку оформления: шаги - сырьё
    раньше потребителя, так что сделанное нижним шагом тут же идёт верхнему. budgets -
    лимит мгновенных крафтов каждой локации НА ЭТОТ ХОД (общий на все заказы и на оба
    прохода в advance_turn). Когда все шаги готовы и партии доиграли - финал
    (_finalize_order). У каждого заказа запоминается причина простоя (order["status"]),
    чтобы он никогда не висел молча."""
    for order in list(state.orders):
        if order not in state.orders:
            continue
        # Место на складе под итог кончилось (прямое пожелание) - заказ стоит, пока его
        # не освободят; уже идущие партии доигрывают в резерв.
        shortfall = _storage_shortfall(state, order.get("output"))
        if shortfall is not None:
            resource_id, need, free = shortfall
            order["status"] = (
                f"на складе нет места под «{RESOURCE_NAMES.get(resource_id, resource_id)}»: "
                f"освободи ещё {_format_amount(need - free)}"
            )
            order["waits_energy"] = False
            continue
        status, waits_energy = None, False
        for step in order["steps"]:
            if step["fires_done"] >= step["fires_total"]:
                continue
            reason, energy = _advance_order_step(state, order, step, budgets)
            if reason and status is None:
                status, waits_energy = reason, energy
        running = any(j.get("order_id") == order["id"] for j in state.manual_jobs.values()) or any(
            j.get("order_id") == order["id"] for j in state.energy_jobs + state.replicator_jobs
        )
        order["status"], order["waits_energy"] = status, waits_energy
        if not running and all(st["fires_done"] >= st["fires_total"] for st in order["steps"]):
            _finalize_order(state, order, report)
        if order["waits_energy"] and order in state.orders:
            note = energy_wait_note(state, order.get("energy_reserve", 0))
            order["status"] = f"ждёт энергию: {note}" if note else "ждёт энергию"


def _finalize_order(state: GameState, order: dict, report: ProductionReport) -> None:
    """Все шаги готовы: заказ «рецепт ×N» выдаёт резерв (результат и остатки) на склад;
    заказ действия дожидается своей энергии, возвращает резерв на склад и выполняет
    действие (_ORDER_RETRY). Не получилось (действие стало невозможным) - резерв
    забирается обратно, заказ остаётся с причиной в статусе и пробует снова каждый ход
    (игрок видит причину и может отменить)."""
    if order["kind"] == "recipe":
        state.orders.remove(order)
        reserved = order["reserved"]
        _gain(state.resources, reserved)
        report.pending_finished.append(order["label"])
        # результат - итоговые ресурсы рецепта; у заказа из старого сохранения без
        # "results" - всё, что ушло на склад
        results = order.get("results") or list(reserved)
        _report_order_finished(report, order, {r: reserved[r] for r in results if reserved.get(r, 0) > 0})
        return
    if state.resources.get(ENERGY_RESOURCE_ID, 0) < order.get("final_energy", 0):
        order["status"], order["waits_energy"] = "ждёт энергию", True
        return
    index = state.orders.index(order)
    state.orders.remove(order)
    reserved = order["reserved"]
    _gain(state.resources, reserved)
    before = dict(state.resources)
    result = _ORDER_RETRY[order["kind"]](state, order)
    if result.ok and not result.pending:
        report.pending_finished.append(order["label"])
        gained = {r: a - before.get(r, 0) for r, a in state.resources.items() if a > before.get(r, 0)}
        _report_order_finished(report, order, gained)
        return
    if result.ok:
        return  # действие оформило себе новый заказ (например, цена выросла)
    back = {}
    for resource_id, amount in reserved.items():
        amount = min(amount, state.resources.get(resource_id, 0))
        if amount > 0:
            state.resources[resource_id] -= amount
            back[resource_id] = amount
    order["reserved"] = back
    order["status"], order["waits_energy"] = result.message, False
    state.orders.insert(index, order)


def _report_order_finished(report: ProductionReport, order: dict, gained: dict) -> None:
    """Запись в report.orders_finished: UI показывает по ней, что заказ готов, - иначе
    заказ, выполненный в тот же ход, исчезал бы молча (прогресс в панели «Заказы» он
    не успевает показать). autocraft - были ли у заказа шаги автокрафта: заказ клика,
    который ждал ТОЛЬКО энергию, - не автокрафт, а отложенный клик."""
    report.orders_finished.append({
        "id": order["id"], "kind": order["kind"], "target_id": order["target_id"],
        "label": order["label"], "gained": gained, "autocraft": bool(order["steps"]),
    })


def orders_view(state: GameState) -> list[dict]:
    """Заказы для UI (панель «Заказы»): шаги с прогрессом, что в резерве, причина
    простоя, ждёт ли энергию, оценка срока (running_order_eta_turns)."""
    view = []
    for order in state.orders:
        steps = []
        for step in order["steps"]:
            running = any(
                j.get("order_id") == order["id"] and j.get("order_resource") == step["resource_id"]
                for j in list(state.manual_jobs.values()) + state.energy_jobs + state.replicator_jobs
            )
            steps.append({
                "resource_id": step["resource_id"], "kind": step["kind"],
                "done": step["fires_done"], "total": step["fires_total"], "running": running,
                # шаг заказа «рецепт преобразователя ×N» считает партии, а не единицы
                "batches_of": step["recipe_id"] if step.get("batches") else None,
            })
        view.append({
            "id": order["id"], "kind": order["kind"], "label": order["label"],
            "status": order.get("status"), "waits_energy": bool(order.get("waits_energy")),
            "reserved": {r: a for r, a in order["reserved"].items() if a > 0},
            "steps": steps,
            "eta_turns": running_order_eta_turns(state, order),
        })
    return view


def _process_manual_jobs(state: GameState, report: ProductionReport) -> None:
    """Каждый ход продвигает многоходовые ручные действия (state.manual_jobs,
    manual_actions.*.duration_turns) на один тик - в отличие от _process_autocraft_jobs,
    тиканье не ограничено никаким бюджетом/сырьём (cost уже списан целиком при старте,
    см. do_manual_action), поэтому просто +1 turns_done каждому job'у безусловно. Когда
    turns_done доходит до turns_total - gain_chance/диапазон разворачиваются РОВНО ОДИН
    РАЗ прямо здесь (тот же момент, что produces у _process_energy_jobs), job удаляется."""
    for action_id, job in list(state.manual_jobs.items()):
        if manual_job_pause_reason(state, action_id):
            continue  # механизм сломан - задача ждёт ремонта
        job["turns_done"] += 1
        if job["turns_done"] < job["turns_total"]:
            continue
        gain = job["gain"]
        if job["gain_chance"]:
            gain = _roll_chance_amounts(gain, job["gain_chance"])
        gain = _roll_range_amounts(gain)
        _deliver_job_output(state, job, gain)
        if job["spawn_species"]:
            spawn_creature(state, _roll_wild_genotype(job["spawn_species"]), identified=False)
        if not job.get("autocraft"):  # партия автокрафта - не "игрок выполнил действие"
            state.action_performed_count[action_id] = state.action_performed_count.get(action_id, 0) + 1
        del state.manual_jobs[action_id]
        report.manual_completed.append(action_id)


def manual_job_pause_reason(state: GameState, action_id: str) -> str | None:
    """Почему многоходовая задача стоит (None - идёт): механизм рецепта
    (action_mechanism_building) сломан. Такая задача не тикает, пока его не починят."""
    building_id = action_mechanism_building(MANUAL_ACTIONS.get(action_id, {}))
    if building_id and working_count(state, building_id) <= 0:
        return f"на паузе: «{BUILDINGS[building_id]['name']}» не работает"
    return None


def ritual_job_pause_reason(state: GameState, building_id: str) -> str | None:
    """Почему ритуал стоит (None - идёт): алтарь сломан."""
    if working_count(state, building_id) <= 0:
        return f"на паузе: «{BUILDINGS.get(building_id, {}).get('name', building_id)}» не работает"
    return None


def research_pause_reason(state: GameState) -> str | None:
    """Почему исследование стоит (None - идёт): нет работающей лаборатории его тира."""
    job = state.active_research
    if job is None:
        return None
    tier = TECHS[job["tech_id"]].get("tier", 1)
    if tier > lab_tier_capacity(state):
        return f"на паузе: нужна работающая лаборатория тира {tier}"
    return None


def manual_jobs_view(state: GameState) -> list[dict]:
    """Список активных многоходовых ручных задач для UI - какое действие, сколько ходов
    прошло/нужно всего (см. autocraft_jobs_view - тот же принцип, только без ETA: тиканье
    здесь не зависит ни от какой пропускной способности, ровно 1 ход/тик). "autocraft" -
    партию запустил автокрафт, а не игрок (см. _new_manual_job). "paused" - почему
    задача стоит (manual_job_pause_reason) или None."""
    return [
        {
            "action_id": aid, "turns_done": j["turns_done"], "turns_total": j["turns_total"],
            "autocraft": bool(j.get("autocraft")), "paused": manual_job_pause_reason(state, aid),
        }
        for aid, j in state.manual_jobs.items()
    ]


def _process_research(state: GameState, report: ProductionReport) -> None:
    """Тикает активное исследование (state.active_research, см. research()) на один
    ход - в отличие от _process_manual_jobs, слот ГЛОБАЛЬНО один на всю игру (как
    active_encounter/veins), поэтому здесь нечего перебирать: не больше одной
    записи. cost уже списан целиком в момент старта - тиканье не ограничено никаким
    сырьём/бюджетом, просто +1 turns_done безусловно."""
    job = state.active_research
    if job is None or research_pause_reason(state):
        return
    job["turns_done"] += 1
    if job["turns_done"] < job["turns_total"]:
        return
    state.techs.add(job["tech_id"])
    report.research_completed = job["tech_id"]
    state.active_research = None


def cancel_manual_job(state: GameState, action_id: str) -> ActionResult:
    """Снимает многоходовую ручную задачу немедленно, БЕЗ траты хода - как
    cancel_autocraft_job/cancel_energy_job. В отличие от energy_jobs (где прогресс
    "потрачен по-настоящему" и не привязан к одной конкретной оплате), у manual_jobs
    cost списан ОДНИМ платежом целиком в момент старта именно ЭТОГО job'а (см.
    do_manual_action) - поэтому отмена до завершения возвращает его игроку через _gain
    (с тем же потолком MAX_RESOURCE_AMOUNT, что и любое начисление). Прокрутки/тики,
    которые job уже сделал (turns_done), возврату не подлежат сами по себе - просто
    потому что тикали "бесплатно" и ничего не расходовали, тут нечего возвращать."""
    if action_id not in state.manual_jobs:
        return ActionResult(False, "Такой задачи уже нет.")
    job = state.manual_jobs.pop(action_id)
    # Партия заказа возвращает цену в резерв заказа (иначе он ждал бы её вечно).
    order = _find_order(state, job.get("order_id")) if job.get("order_id") is not None else None
    _gain(order["reserved"] if order is not None else state.resources, job["cost"])
    return ActionResult(True, f"«{MANUAL_ACTIONS[action_id]['name']}» отменено, затраты возвращены.")


# Каждый retry принимает ВЕСЬ pending-словарь (не только target_id) - так у него
# есть доступ к retry_extra-полям, запомненным _enqueue_pending (например, "scale"
# у ручного действия, см. do_manual_action) - без этого повторная попытка молча
# откатилась бы на параметры по умолчанию, теряя исходный выбор игрока.
_ORDER_RETRY = {
    "manual": lambda state, pending: do_manual_action(state, pending["target_id"], pending.get("scale")),
    "build": lambda state, pending: build(state, pending["target_id"]),
    "tech": lambda state, pending: research(state, pending["target_id"]),
    "repair": lambda state, pending: repair(state, pending["target_id"]),
    "upgrade_tool": lambda state, pending: upgrade_tool(state, pending["target_id"]),
    "repair_tool": lambda state, pending: repair_tool(state, pending["target_id"]),
    "building_upgrade": lambda state, pending: purchase_building_upgrade(state, *pending["target_id"].split(":", 1)),
    "farm_instance_upgrade": lambda state, pending: purchase_farm_instance_upgrade(state, *pending["target_id"].split(":", 1)),
    "reactor_instance_upgrade": lambda state, pending: purchase_reactor_instance_upgrade(state, *pending["target_id"].split(":", 1)),
    "soul_instance_upgrade": lambda state, pending: purchase_soul_collector_instance_upgrade(state, *pending["target_id"].split(":", 1)),
    "storage_upgrade": lambda state, pending: upgrade_storage(state, pending["target_id"]),
    "start_energy_recipe": lambda state, pending: start_energy_recipe(state, pending["target_id"]),
    "start_ritual": lambda state, pending: start_ritual(state, pending["target_id"]),
    "synthesize": lambda state, pending: synthesize_creature(state, pending["chosen_alleles"]),
    "summon_boss": lambda state, pending: summon_boss(state, pending["target_id"]),
}


def _process_combat(state: GameState, report: ProductionReport) -> None:
    """Тикает кулдауны приёмов боя (state.move_cooldowns, см. attack_encounter),
    реген HP игрока и дебафф "Ранен", и проверяет дедлайн активного босса
    (mobs.*.turn_limit, см. summon_boss) - вызывается из advance_turn ПОСЛЕ
    state.turn += 1, чтобы сверяться уже с НОВЫМ номером хода. Кулдауны/реген
    тикают КАЖДЫЙ ход, а не только во время боя - тот же принцип, что и
    остальные счётчики хода в advance_turn."""
    for move_id in list(state.move_cooldowns):
        remaining = state.move_cooldowns[move_id] - 1
        if remaining <= 0:
            del state.move_cooldowns[move_id]
        else:
            state.move_cooldowns[move_id] = remaining

    # Реген HP - ТОЛЬКО вне схватки (по прямому пожеланию: тянуть бой, уходя
    # в другую локацию, не должно бесплатно чинить игрока) - см. player_max_hp/
    # current_player_hp. "Ранен" сам снимается, как только ход перевалил за
    # wounded_until_turn - гигиена сейва, тот же принцип, что удаление
    # истёкших move_cooldowns (без этого число просто вечно лежало бы в
    # save.json без дальнейшего смысла).
    if state.active_encounter is None:
        regen = COMBAT_SETTINGS.get("hp_regen_per_turn", 0)
        if regen:
            state.player_hp = min(player_max_hp(state), current_player_hp(state) + regen)
    if state.wounded_until_turn is not None and state.turn > state.wounded_until_turn:
        state.wounded_until_turn = None

    encounter = state.active_encounter
    if encounter is None:
        return
    deadline = encounter.get("deadline_turn")
    if deadline is not None and state.turn > deadline:
        report.boss_fled = MOBS[encounter["mob_id"]]["name"]
        state.active_encounter = None


def advance_turn(state: GameState) -> ProductionReport:
    """Применяет автопроизводство работающих построек, катает шанс поломки, увеличивает счётчик хода.

    Энергия обрабатывается в два прохода: сперва все постройки, которые её НЕ потребляют
    (включая генераторы - их выработка идёт в общий пул этого хода), и только потом те,
    что её тратят. Иначе порядок построек в state.buildings решал бы, кому достанется
    энергия, произведённая в этот же ход: потребитель, вставленный в словарь раньше своего
    генератора, никогда не получал бы свежей выработки.

    Сразу после этих двух проходов - фермы (_process_habitat, см. state.creatures):
    каждая поселённая особь производит/потребляет по своему ФЕНОТИПУ (домноженному
    на апгрейды её фермы), независимо от построек - она привязана к КОНКРЕТНОМУ
    ЭКЗЕМПЛЯРУ фермы (farm_instance_id, см. state.farm_instances), а не к типу
    постройки и не к какой-то одной "общей" копии.

    Следом - синтезаторы душ (_process_soul_collectors, см.
    state.soul_collector_instances): каждая вставленная душа (в пределах текущей
    capacity ИМЕННО ЭТОГО экземпляра) производит по своему soul_recipes[soul_id],
    независимо от обычных produces/consumes постройки.

    Следом за ними - реакторы (_process_reactors, см. state.reactor_instances):
    решётка ячеек каждого экземпляра считается по зонам (тепло/выход/аварии,
    см. докстринг самой функции), суммарный выход идёт в тот же общий пул
    энергии этого хода, что и обычные генераторы.

    Дальше - заказы автокрафта (_process_orders, см. state.orders: 1-й проход ДО тика
    многоходовых, чтобы партия заказа тикала в ход запуска, как клик), многоходовые
    действия, ритуалы, исследования, проход заказов ДО автоматизатора (освободившийся
    механизм достаётся заказу, а не правилу), автоматизатор и ещё проход заказов: шаги
    двигаются в пределах лимитов сборщиков/«Автоматизации» на ход, а действия, чьи
    заказы готовы, сами выполняются в этот же ход, без участия игрока.

    ПОСЛЕДНИМИ из потребителей энергии идут крафты за энергию (_process_energy_jobs, см.
    state.energy_jobs - рецепты в преобразователях): им достаётся только ОСТАТОК после
    построек, автокрафта, отложенных действий и автоматизатора (прямое пожелание -
    крафт за энергию не должен тормозить остальные процессы). Сразу за ними -
    3-й проход заказов: то, что ждало доделанную в этот ход партию преобразователя,
    завершается в этот же ход, а не следующим (иначе цепочка через преобразователь
    теряла бы ход на каждом звене).

    В конце запас энергии обрезается до ёмкости построенных хранилищ (energy_capacity) -
    без них она равна 0, и всё, что не потратили за ход, на следующий не переносится.

    Последним, уже ПОСЛЕ state.turn += 1 - _process_combat: тикают кулдауны приёмов
    боя (независимо от того, идёт ли бой прямо сейчас) и проверяется дедлайн активного
    босса (mobs.*.turn_limit) - не уложились вовремя, босс сбегает сам (report.boss_fled)."""
    report = ProductionReport()

    _ensure_physical_instances(state)
    daytime = is_daytime(state)
    # Считаем effective_building_stats РОВНО ОДИН раз на тип постройки за ход и
    # переиспользуем в process() - без кэша она бы считалась дважды (тут же ниже,
    # при разбиении на energy_consumers/others, и внутри process()), что при
    # большом числе построек заметно на профиле (см. CLAUDE.md, "Известные
    # ограничения"). effects_cache - тот же принцип для _matching_effects
    # (O(число техов) на постройку): без него effective_building_stats и
    # effective_breakdown_chance пересчитывали бы его КАЖДАЯ СВОЯ, хотя это
    # один и тот же обход TECHS для одного building_id/state.techs.
    stats_cache: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
    effects_cache: dict[str, list[dict]] = {}

    def process(building_id: str, working: int) -> None:
        if not is_building_active_now(building_id, daytime):
            # Не время суток для этой постройки - она просто простаивает: не потребляет,
            # не производит и не может сломаться (не работает - нечему и ломаться).
            report.ran[building_id] = (0, working)
            return

        # Копии, выключенные ползунком "Механизмы" (state.building_disabled) - тот
        # же принцип, что и active_period выше: не потребляют, не производят и не
        # могут сломаться в этот ход (см. building_disabled_count/set_building_active_count).
        active = working - min(state.building_disabled.get(building_id, 0), working)
        if active <= 0:
            report.ran[building_id] = (0, working)
            return

        produces, consumes = stats_cache[building_id]
        runnable = active
        for r, amount in consumes.items():
            if amount <= 0:
                continue
            runnable = min(runnable, state.resources.get(r, 0) // amount)
        runnable = max(0, runnable)
        if runnable > 0:
            for r, amount in consumes.items():
                state.resources[r] = state.resources.get(r, 0) - amount * runnable
            # Диапазон produces (см. _is_range_amount) бросается РОВНО ОДИН РАЗ
            # на этот ход для ЭТОГО типа постройки целиком (не по отдельной
            # копии) - см. _roll_range_amounts.
            for r, amount in _roll_range_amounts(produces).items():
                _add_resource(state.resources, r, amount * runnable)
        idle = working - runnable
        if runnable > 0 or idle > 0:
            report.ran[building_id] = (runnable, idle)

        chance = effective_breakdown_chance(
            building_id, state.techs, effects_cache[building_id], state.building_upgrades.get(building_id, []),
        )
        if chance <= 0:
            return
        if _uses_physical_instances(building_id):
            # Поломка катается ПО ЭКЗЕМПЛЯРУ (не по абстрактному "working" числу) -
            # иначе не получилось бы отметить, КАКАЯ именно копия сломалась, а
            # состояние экземпляра (broken) обязано совпадать с агрегатом
            # state.broken[bid] (см. _ensure_physical_instances). Пересчёт агрегата
            # ТОЧНЫЙ (не инкремент), т.к. этот проход - единственное место, где он
            # вообще может вырасти для ферм/синтезаторов душ. breakdown_immunity -
            # ВСЕГДА per-instance (см. _instance_has_upgrade_type,
            # purchase_farm_instance_upgrade/purchase_soul_collector_instance_upgrade) -
            # иммунный экземпляр просто не участвует в ролле вовсе, независимо от
            # общего chance этого типа построек.
            instances = [
                (iid, inst) for iid, inst in _physical_instances(state, building_id).items()
                if inst["building_id"] == building_id
            ]
            newly_broken = 0
            for instance_id, instance in instances:
                if instance["broken"] or _instance_has_upgrade_type(building_id, instance, "breakdown_immunity"):
                    continue
                # Не работает - не ломается: пустой экземпляр или тип без сырья на ход.
                if runnable <= 0 or not _instance_in_use(state, instance_id, instance):
                    continue
                if random.random() < chance:
                    instance["broken"] = True
                    instance["broken_type"] = _roll_breakdown_type(building_id)
                    newly_broken += 1
            if newly_broken:
                report.newly_broken[building_id] = newly_broken
            state.broken[building_id] = sum(1 for _, inst in instances if inst["broken"])
        else:
            # Ломаться могут только копии, которые в этот ход отработали: простаивающая
            # без сырья - как выключенная ползунком или не в своё время суток.
            newly_broken = sum(1 for _ in range(runnable) if random.random() < chance)
            if newly_broken:
                state.broken[building_id] = state.broken.get(building_id, 0) + newly_broken
                report.newly_broken[building_id] = newly_broken
                types_list = state.broken_types.setdefault(building_id, [])
                types_list.extend(_roll_breakdown_type(building_id) for _ in range(newly_broken))

    energy_consumers = []
    surplus_consumers = []  # buildings.*.energy_surplus_only - см. проход перед преобразователями
    others = []
    for building_id, count in state.buildings.items():
        if count <= 0:
            continue
        working = count - state.broken.get(building_id, 0)
        if working <= 0:
            continue
        effects_cache[building_id] = _matching_effects(building_id, state.techs)
        stats_cache[building_id] = effective_building_stats(
            building_id, state.techs, state.building_upgrades, effects_cache[building_id],
        )
        if stats_cache[building_id][1].get(ENERGY_RESOURCE_ID, 0) > 0:
            if BUILDINGS[building_id].get("energy_surplus_only"):
                surplus_consumers.append((building_id, working))
            else:
                energy_consumers.append((building_id, working))
        else:
            others.append((building_id, working))

    for building_id, working in others:
        process(building_id, working)
    for building_id, working in energy_consumers:
        process(building_id, working)

    _process_habitat(state, report)
    _process_soul_collectors(state, report, daytime)
    _process_reactors(state, report, daytime)
    # Лимит мгновенных крафтов каждой локации на этот ход - общий на все проходы.
    order_budgets: dict = {}
    # Заказы - ДО тика многоходовых действий: партия, которую заказ запускает на
    # механизме, тикает уже в этот ход, как у клика игрока (3 партии по 3 хода = 9).
    _process_orders(state, report, order_budgets)
    _process_manual_jobs(state, report)
    # Репликации - сразу за многоходовыми: партия, запущенная заказом в 1-м проходе,
    # перерабатывает материю уже в этот ход (как партия механизма).
    _process_replicator_jobs(state, report)
    _process_ritual_jobs(state, report)
    _process_smelteries(state, report)
    _process_research(state, report)
    # Механизм, освободившийся в этот ход (доиграла партия), первым получает заказ, а не
    # правило автоматизатора: иначе правило без условий на многоходовой рецепт того же
    # механизма перезапускало бы себя раньше заказа каждый раз, и заказ ждал бы вечно.
    if state.orders:
        _process_orders(state, report, order_budgets)
    process_automation_rules(state, report, daytime)
    # Второй проход - до преобразователей: подхватить доделанные партии и заказы,
    # оформленные автоматизатором в этот ход.
    if state.orders:
        _process_orders(state, report, order_budgets)
    # Постройки «только на излишках энергии» (buildings.*.energy_surplus_only, например
    # генератор жидкой материи) - предпоследние потребители (прямое пожелание): после
    # построек, заказов, долгих действий и автоматизатора, но ДО преобразователей.
    # Как и преобразователи, не трогают энергию, которую копят заказы (energy_reserved),
    # иначе ожидание энергии не кончалось бы. Между собой - в порядке content.json.
    if surplus_consumers:
        building_order = {bid: i for i, bid in enumerate(BUILDINGS)}
        stock = state.resources.get(ENERGY_RESOURCE_ID, 0)
        reserved = min(stock, energy_reserved(state))
        state.resources[ENERGY_RESOURCE_ID] = stock - reserved
        for building_id, working in sorted(surplus_consumers, key=lambda item: building_order.get(item[0], 0)):
            process(building_id, working)
        state.resources[ENERGY_RESOURCE_ID] = state.resources.get(ENERGY_RESOURCE_ID, 0) + reserved
    # Преобразователи - ПОСЛЕДНИЕ потребители энергии в ходу (прямое пожелание):
    # берут только остаток после построек, заказов и автоматизатора, крафт за
    # энергию не должен тормозить остальные процессы.
    _process_energy_jobs(state, report)
    # Третий проход: подхватить то, что преобразователи доделали в этот же ход.
    if state.orders:
        _process_orders(state, report, order_budgets)

    cap = energy_capacity(state)
    if state.resources.get(ENERGY_RESOURCE_ID, 0) > cap:
        state.resources[ENERGY_RESOURCE_ID] = cap

    # Вместимость склада (см. storage_capacity) - та же модель, что у энергии: сгорает
    # только ИЗЛИШЕК сверх вместимости, и только В КОНЦЕ хода (не мешает производству/
    # автокрафту в течение самого хода). Ресурсы без настроенного storage (RESOURCE_STORAGE)
    # этот цикл не трогает вовсе - они по-прежнему без ограничений, как раньше. cap is None
    # значит то же самое, но для ресурса, у которого storage ЕСТЬ, только купленный тир
    # снял ограничение совсем (infinite) - клампать нечем, пропускаем так же.
    for resource_id in RESOURCE_STORAGE:
        cap = storage_capacity(state, resource_id)
        if cap is not None and state.resources.get(resource_id, 0) > cap:
            state.resources[resource_id] = cap

    state.turn += 1
    _process_combat(state, report)
    return report


def theoretical_rates(state: GameState) -> dict[str, float]:
    """Сколько ресурса в ход прибывает (плюс) или убывает (минус) от всего, что
    работает само, если всем хватает сырья (для отображения) - см. passive_flows.
    Постройки не в своё время суток (active_period) или выключенные ползунком
    "Механизмы" (building_active_count) гарантированно простаивают и не считаются."""
    produced, consumed = passive_flows(state)
    rates = dict(produced)
    for r, amount in consumed.items():
        rates[r] = rates.get(r, 0) - amount
    return {r: round(v, 9) for r, v in rates.items()}


def all_techs_researched(state: GameState) -> bool:
    return all(t in state.techs for t in TECHS)


def _condition_met(condition: dict, state: GameState) -> bool:
    """Тип условия квеста (content.json["quests"][...]["conditions"], проверяются
    через И - см. check_quests)."""
    kind = condition["type"]
    if kind == "resource_at_least":
        return state.resources.get(condition["resource"], 0) >= condition["amount"]
    if kind == "building_count_at_least":
        return state.buildings.get(condition["building"], 0) >= condition["amount"]
    if kind == "total_buildings_at_least":
        return sum(state.buildings.values()) >= condition["amount"]
    if kind == "tech_researched":
        return condition["tech"] in state.techs
    if kind == "tech_count_at_least":
        return len(state.techs) >= condition["amount"]
    if kind == "all_techs_researched":
        return all_techs_researched(state)
    if kind == "turn_at_least":
        return state.turn >= condition["amount"]
    if kind == "tool_tier_at_least":
        return tool_tier(state, condition["tool"]) >= condition["amount"]
    if kind == "species_discovered_at_least":
        return len(state.species_discovered) >= condition["amount"]
    if kind == "species_discovered":
        return condition["species"] in state.species_discovered
    if kind == "manual_action_performed_at_least":
        return state.action_performed_count.get(condition["action"], 0) >= condition["amount"]
    if kind == "building_upgrade_purchased":
        return is_building_upgrade_purchased(state, condition["building"], condition["upgrade"])
    if kind == "storage_tier_at_least":
        return state.storage_tier.get(condition["resource"], 0) >= condition["amount"]
    if kind == "building_upgrade_level_at_least":
        return building_upgrade_purchase_count(state, condition["building"], condition["upgrade"]) >= condition["amount"]
    if kind == "building_upgrade_type_purchased_any":
        return building_upgrade_type_purchased_anywhere(state, condition["upgrade_type"])
    return False


def is_quest_available(quest_id: str, state: GameState) -> bool:
    """Ровно как is_tech_available, только требует ДРУГИЕ КВЕСТЫ (quests.*.requires),
    а не технологии - квест, у которого хоть один prerequisite ещё не выполнен,
    даже не начинает проверяться check_quests (граф квестбука открывается волнами
    "один за другим", как дерево технологий). Не проверяет сами conditions - это
    отдельный, следующий шаг в check_quests."""
    if quest_id in state.completed_quests:
        return False
    return all(r in state.completed_quests for r in QUESTS[quest_id].get("requires", []))


def check_quests(state: GameState) -> list[str]:
    """Проверяет условия ещё не выполненных квестов и отмечает выполненные (условия
    внутри одного квеста - через И, см. _condition_met). Квест, чьи prerequisite-
    квесты (requires) ещё не все выполнены, вообще не проверяется - см.
    is_quest_available. Выполнение НЕ значит "награда получена" - тот отдельный
    шаг, см. claim_quest_reward. Раз условия истинны, квест остаётся выполненным
    навсегда (не переоценивается заново, даже если условие потом перестало бы быть
    истинным). Возвращает id, выполненные именно на этом вызове - чтобы интерфейс
    мог показать уведомление."""
    newly_completed = []
    for qid, quest in QUESTS.items():
        if not is_quest_available(qid, state):
            continue
        conditions = quest.get("conditions", [])
        if conditions and all(_condition_met(c, state) for c in conditions):
            state.completed_quests[qid] = state.turn
            newly_completed.append(qid)
    return newly_completed


def _roll_loot_case_prize(case: dict) -> dict | None:
    """Классический лутбокс: барабан из нескольких возможных призов
    (content.json loot_cases.*.prizes, каждый - {"resource", "amount", "weight"}),
    из которого выпадает РОВНО один - вероятность приза пропорциональна его weight
    среди суммы весов ВСЕХ призов кейса. None, если у кейса вообще нет призов
    (весь суммарный вес - 0 или список пуст) - контент так делать не должен
    (см. content_validation.py), но engine на это не полагается."""
    prizes = case.get("prizes", [])
    total_weight = sum(p.get("weight", 1) for p in prizes)
    if total_weight <= 0:
        return None
    roll = random.uniform(0, total_weight)
    upto = 0.0
    for prize in prizes:
        upto += prize.get("weight", 1)
        if roll <= upto:
            return prize
    return prizes[-1]  # подстраховка на случай погрешности плавающей точки


def claim_quest_reward(state: GameState, quest_id: str) -> ActionResult:
    """Забрать кейс с лутом за выполненный квест - управление имуществом, НЕ тратит
    ход (тот же принцип, что insert_soul/remove_soul/house_creature). Кейс
    открывается СРАЗУ при клике: барабан призов (_roll_loot_case_prize) решает,
    какой РОВНО ОДИН приз выпадает - отдельного предмета "невскрытый кейс" в
    инвентаре не заводится. Выпавший приз кладётся в ActionResult.prize (см. его
    докстринг) - UI анимирует прокрутку барабана до этого РЕАЛЬНОГО результата."""
    quest = QUESTS.get(quest_id)
    if quest is None:
        return ActionResult(False, "Неизвестный квест.")
    if quest_id not in state.completed_quests:
        return ActionResult(False, "Квест ещё не выполнен.")
    if quest_id in state.claimed_quests:
        return ActionResult(False, "Награда уже получена.")
    case = LOOT_CASES.get(quest["reward_case"])
    if case is None:
        return ActionResult(False, "У этого квеста нет доступной награды.")
    prize = _roll_loot_case_prize(case)
    if prize is None:
        return ActionResult(False, f"«{case['name']}» пуст.")
    _add_resource(state.resources, prize["resource"], prize["amount"])
    state.claimed_quests.add(quest_id)
    name = RESOURCE_NAMES.get(prize["resource"], prize["resource"])
    won = {"resource": prize["resource"], "amount": prize["amount"]}
    return ActionResult(True, f"Открыт «{case['name']}»: {name} ×{prize['amount']}.", prize=won)


def record_history_entry(
    state: GameState,
    action_kind: str | None,
    action_id: str | None,
    action_label: str | None,
    production_report: ProductionReport,
    newly_completed_quests: list[str],
) -> None:
    """Кладёт в state.history снимок только что завершившегося хода - для статистики
    за произвольный диапазон ходов. Звать один раз за ход, сразу после advance_turn
    (и, если нужно, check_quests) - до этого turn уже увеличен advance_turn'ом,
    поэтому здесь используется state.turn - 1, номер СОВЕРШИВШЕГОСЯ хода."""
    if not state.history and state.turn - 1 == 1:
        # точка отсчёта "с нуля" - только для только что начатой игры (не для старых
        # сохранений без истории, у которых уже накоплен прогресс до этого хода:
        # для них взять несуществующее "было пусто" было бы неверно).
        state.history.append({
            "turn": 0, "resources": {}, "buildings": {}, "techs_count": 0,
            "new_techs": [], "new_buildings": {}, "repairs": 0,
            "quests_completed": [], "breakdowns": 0, "action": None,
        })
    state.history.append({
        "turn": state.turn - 1,
        "resources": dict(state.resources),
        "buildings": dict(state.buildings),
        "techs_count": len(state.techs),
        "new_techs": [action_id] if action_kind == "tech" else [],
        "new_buildings": {action_id: 1} if action_kind == "build" else {},
        "repairs": 1 if action_kind == "repair" else 0,
        "quests_completed": list(newly_completed_quests),
        "breakdowns": sum(production_report.newly_broken.values()),
        "action": action_label,
    })
    state.history[:] = state.history[-HISTORY_MAX_ENTRIES:]


def compute_stats(state: GameState, start_turn: int, end_turn: int) -> dict | None:
    """Расширенная статистика по диапазону ходов [start_turn, end_turn] (включительно).
    None, если в этом диапазоне нет ни одной записи истории."""
    by_turn = {h["turn"]: h for h in state.history}
    records = [by_turn[t] for t in range(start_turn, end_turn + 1) if t in by_turn]
    if not records:
        return None

    baseline = by_turn.get(start_turn - 1)
    prev_resources = baseline["resources"] if baseline else records[0]["resources"]

    gained: dict[str, int] = {}
    spent: dict[str, int] = {}
    for rec in records:
        cur = rec["resources"]
        for rid in set(prev_resources) | set(cur):
            delta = cur.get(rid, 0) - prev_resources.get(rid, 0)
            if delta > 0:
                gained[rid] = gained.get(rid, 0) + delta
            elif delta < 0:
                spent[rid] = spent.get(rid, 0) - delta
        prev_resources = cur

    techs_researched = [t for rec in records for t in rec["new_techs"]]
    buildings_built: dict[str, int] = {}
    for rec in records:
        for bid, n in rec["new_buildings"].items():
            buildings_built[bid] = buildings_built.get(bid, 0) + n
    # .get(...) - не rec[...]: у истории из старого сейва (до переименования
    # "achievements" -> "quests_completed") этого ключа не будет, и это нормально -
    # такие записи просто не покажут выполненные квесты для СВОЕГО диапазона ходов,
    # без падения (та же терпимость к устаревшим сохранениям, что и везде в игре).
    quests_completed = [q for rec in records for q in rec.get("quests_completed", [])]

    all_resource_ids = set(gained) | set(spent)
    return {
        "start_turn": start_turn,
        "end_turn": end_turn,
        "turns_recorded": len(records),
        "resource_gained": gained,
        "resource_spent": spent,
        "resource_net": {rid: gained.get(rid, 0) - spent.get(rid, 0) for rid in all_resource_ids},
        "techs_researched": techs_researched,
        "buildings_built": buildings_built,
        "quests_completed": quests_completed,
        "total_breakdowns": sum(rec["breakdowns"] for rec in records),
        "total_repairs": sum(rec["repairs"] for rec in records),
        "turns_skipped": sum(1 for rec in records if rec["action"] is None),
    }
