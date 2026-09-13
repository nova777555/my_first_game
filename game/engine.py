# Правила игры. Ничего не печатает и ничего не читает с клавиатуры -
# только принимает GameState и возвращает результат. Это позволяет
# позже приделать любой интерфейс (консоль, браузер) не трогая этот файл.
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .content import (
    BREAKDOWN_TYPES, BUILDINGS, COMBAT_MOVES, ENERGY_RECIPES, GENETIC_TRAITS,
    GENETICS_SETTINGS, LOCATIONS, LOOT_CASES, MANUAL_ACTIONS, MINING_INFO, MOBS,
    MUTATIONS, QUESTS, RESOURCE_FRACTIONAL, RESOURCE_NAMES, RESOURCE_SOUL_KIND,
    RESOURCE_STORAGE, SKILLS, SOUL_RECIPES, SPECIES, TECHS, TIME, TOOLS,
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
    контента (manual_actions.*.priority, 0, если не указано)."""
    if action_id in state.action_priority:
        return state.action_priority[action_id]
    return MANUAL_ACTIONS[action_id].get("priority", 0)


def set_action_priority(state: GameState, action_id: str, priority: int | None) -> ActionResult:
    """Переопределяет приоритет ОДНОГО действия для ЭТОГО сохранения (не трогает
    content.json - у каждого игрока свой выбор). priority=None сбрасывает
    переопределение, возвращая действие к значению по умолчанию из контента.
    Как toggle_pin/toggle_autocraft - настройка, не тратит ход."""
    if action_id not in MANUAL_ACTIONS:
        return ActionResult(False, "Неизвестное действие.")
    name = MANUAL_ACTIONS[action_id]["name"]
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
# каждую попытку. Ресурс, у которого ЕСТЬ только вероятностный рецепт, для _ensure_available/
# _raw_shortfall неотличим от обычного сырья без рецепта вообще ("no_recipe") - автокрафт
# честно напишет "не хватает", а не будет пытаться докрафтить его сам.
# Действия с duration_turns > 1 (см. state.manual_jobs/_process_manual_jobs) тоже
# исключены - их "прокрутка" физически растянута на несколько ходов и не укладывается
# в бухгалтерию fires/autocraft_speed_capacity (рассчитанную на мгновенный результат
# каждой попытки), а на action_id уже и так допустима только ОДНА активная задача.
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
def resource_recipe(state: GameState) -> dict[str, str]:
    recipe: dict[str, str] = {}
    best_priority: dict[str, int] = {}
    for action_id, action in MANUAL_ACTIONS.items():
        if not action["cost"]:
            continue
        if action.get("excluded_from_autocraft"):
            continue
        if not is_manual_action_deterministic(action_id):
            continue
        if (action.get("duration_turns") or 1) > 1:
            continue
        priority = action_priority(state, action_id)
        for resource_id in action["gain"]:
            if resource_id not in recipe or priority > best_priority[resource_id]:
                recipe[resource_id] = action_id
                best_priority[resource_id] = priority
    return recipe


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
# на content.json["genetic_traits"] (общий набор аллелей на всю игру, НЕ зависящий
# от вида - ровно как в Forestry срок жизни пчелы отделён от её вида).
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
    # True - действие ушло в очередь автокрафта (state.pending_actions) и ЕЩЁ НЕ
    # применилось по-настоящему, хотя ok=True (игроку это подано как принятое, а не
    # провалившееся действие). Нужно отдельно от ok, чтобы _process_pending_actions
    # мог отличить "уже готово, можно снять с очереди" от "всё ещё ждёт" - оба
    # варианта retry() возвращают ok=True, но означают разное.
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


@dataclass
class AutocraftFailure:
    """Почему автокрафт resource_id не завершился мгновенно - конкретная причина
    нужна, чтобы игрок видел не общее "не хватает ресурсов", а что именно чинить:
    построить нужную постройку, доисследовать технологию, нарастить процессор/скорость
    или подождать. "in_progress" - не настоящая ошибка, а сигнал "запущено в фоне,
    доделается само" (см. _pay_or_defer); отличать от остальных (реальных) причин
    нужно и в ensure_and_pay, и везде, где вызывается describe_autocraft_failure.
    "no_processor" - отдельный случай: пока в игре нет вообще ни одной работающей
    постройки с compute_power (compute_capacity(state) == 0), автокрафт как система
    ещё не "появился" для игрока. "autocraft_disabled" - игрок сам выключил галочку
    "Автокрафт" в UI (state.autocraft_enabled=False), хотя процессор(ы) есть - в
    обоих случаях _pay_or_defer намеренно не показывает описание из
    _AUTOCRAFT_REASON_TEXT, а формирует голое "не хватает ресурсов на X.",
    неотличимое от обычной нехватки при ручном крафте (см. _ensure_available).
    Ресурс, который производится ТОЛЬКО крафтом за энергию (не обычным рецептом),
    тоже может дать "in_progress"/"not_unlocked" - см. ENERGY_RECIPE_FOR_RESOURCE в
    _ensure_available: автокрафт САМ запускает нужный рецепт в свободном
    энергетическом преобразователе (если он есть), а если все заняты - просто ждёт
    своей очереди, без отдельной фоновой задачи/слота автокрафта."""
    resource_id: str
    reason: str  # "no_processor" | "autocraft_disabled" | "no_recipe" | "not_unlocked" | "tech_locked" | "insufficient_compute" | "no_free_slots" | "in_progress" | "missing_energy_recipe_material"


_AUTOCRAFT_REASON_TEXT = {
    "no_processor": "автокрафт пока недоступен - в игре ещё нет ни одного работающего процессора",
    "autocraft_disabled": "автокрафт выключен галочкой в интерфейсе - рецепты делаются только руками",
    "no_recipe": "«{name}» - сырьё, автокрафтить его не из чего",
    "not_unlocked": "автокрафт «{name}» ещё не открыт - нужна постройка, которая его включает",
    "tech_locked": "рецепт «{name}» ещё не изучен",
    "insufficient_compute": "не хватает вычислительной мощности процессора для автокрафта «{name}»",
    "no_free_slots": "не хватает свободных слотов автокрафта, чтобы начать докрафт «{name}» в фоне",
    "in_progress": "«{name}» уже докрафтывается в фоне",
    "missing_energy_recipe_material": "для «{name}» не хватает сырья на крафт за энергию - автокрафт не докрафчивает его сам",
}


def describe_autocraft_failure(failure: AutocraftFailure) -> str:
    name = RESOURCE_NAMES.get(failure.resource_id, failure.resource_id)
    template = _AUTOCRAFT_REASON_TEXT[failure.reason]
    return template.format(name=name)


def _add_resource(resources: dict[str, int], resource_id: str, amount: int) -> None:
    """Прибавляет amount к resources[resource_id], но не выше MAX_RESOURCE_AMOUNT -
    единственная точка, через которую должно идти любое НАЧИСЛЕНИЕ (не списание)
    ресурса, чтобы потолок применялся одинаково везде: ручные действия, автопроизводство
    построек, автокрафт, крафт за энергию, добыча жил."""
    resources[resource_id] = min(MAX_RESOURCE_AMOUNT, resources.get(resource_id, 0) + amount)


def _gain(resources: dict[str, int], amounts: dict[str, int]) -> None:
    for r, amount in amounts.items():
        _add_resource(resources, r, amount)


def is_autocraft_unlocked(resource_id: str, state: GameState) -> bool:
    """Автокрафт по умолчанию закрыт для ВСЕХ ресурсов - конкретная постройка должна
    явно перечислить его в своём unlocks_autocraft (и при этом работать, не быть
    сломанной целиком), иначе игрок обязан делать этот ресурс руками."""
    for building_id in state.buildings:
        if working_count(state, building_id) <= 0:
            continue
        if resource_id in BUILDINGS[building_id].get("unlocks_autocraft", []):
            return True
    return False


def lab_tier_capacity(state: GameState) -> int:
    """Наивысший тир среди РАБОТАЮЩИХ построек с полем lab_tier - в отличие от
    compute_power/energy_storage, тиры лабораторий НЕ складываются (лаборатория
    тира 2 + лаборатория тира 2 не дают тир 4), берётся максимум. Тир 1 всегда
    доступен без единой лаборатории - см. research()."""
    tiers = [BUILDINGS[b].get("lab_tier", 0) for b in state.buildings if working_count(state, b) > 0]
    return max([1, *tiers])


def compute_capacity(state: GameState) -> int:
    """Суммарная вычислительная мощность работающих построек-процессоров (поле
    compute_power) - потолок на то, сколько раз ВСЕГО можно "прокрутить" один
    автокрафт-рецепт за один запрос (ensure_and_pay), вне зависимости от того, сколько
    ходов это займёт - если рецепт не влезает сюда даже теоретически, ждать бессмысленно,
    фоновая задача не поможет. Считается так же, как energy_capacity."""
    capacity = 0
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        capacity += BUILDINGS[building_id].get("compute_power", 0) * working
    return capacity


def _is_farm_building(building_id: str) -> bool:
    return BUILDINGS.get(building_id, {}).get("breeding_slots", 0) > 0


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


# Базовые скорость и число слотов автокрафта БЕЗ единой профильной постройки - без этой
# базы автокрафт был бы бесполезен даже для мелких партий, пока игрок не построит первый
# ускоритель/диспетчер. Подобраны так, чтобы мелкие партии (единицы прокруток) по-прежнему
# решались мгновенно, а вот сколько-нибудь крупные требовали построек на скорость/слоты.
BASE_AUTOCRAFT_SPEED = 1
BASE_AUTOCRAFT_SLOTS = 1


def autocraft_speed_capacity(state: GameState) -> int:
    """Сколько прокруток (операций) автокрафт суммарно может выполнить ЗА ОДИН ход -
    в отличие от compute_capacity (потолок РАЗМЕРА запроса безотносительно времени), это
    ПРОПУСКНАЯ СПОСОБНОСТЬ: если прокруток нужно больше, чем есть скорости, лишнее не
    проваливается, а растягивается на несколько ходов фоновой задачей (см. state.autocraft_jobs
    и _process_autocraft_jobs) - отсюда и смысл строить более скоростные механизмы вместо
    того, чтобы просто ждать."""
    capacity = BASE_AUTOCRAFT_SPEED
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        capacity += BUILDINGS[building_id].get("autocraft_speed", 0) * working
    return capacity


def autocraft_slot_capacity(state: GameState) -> int:
    """Сколько фоновых автокрафт-задач может выполняться ОДНОВРЕМЕННО - по одной на
    КАЖДЫЙ ресурс в цепочке докрафта, включая промежуточные (не только на верхний
    запрошенный). БАЗА - BASE_AUTOCRAFT_SLOTS без единой постройки, плюс autocraft_slots
    работающих построек (в контенте это, как правило, уникальная дорогая постройка с
    max_count, а не то, что стоит строить десятками)."""
    capacity = BASE_AUTOCRAFT_SLOTS
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0:
            continue
        capacity += BUILDINGS[building_id].get("autocraft_slots", 0) * working
    return capacity


def autocraft_jobs_view(state: GameState) -> list[dict]:
    """Список активных фоновых автокрафт-задач для UI (панель "Автокрафт"): какой
    ресурс, сколько прокруток сделано/нужно всего и через сколько ходов доделается
    при ТЕКУЩЕЙ скорости (None - никогда, если скорость сейчас нулевая)."""
    speed = autocraft_speed_capacity(state)
    jobs = []
    for resource_id, job in state.autocraft_jobs.items():
        remaining = job["fires_total"] - job["fires_done"]
        jobs.append({
            "resource_id": resource_id,
            "fires_done": job["fires_done"],
            "fires_total": job["fires_total"],
            "eta_turns": None if speed <= 0 else -(-remaining // speed),
        })
    return jobs


def cancel_autocraft_job(state: GameState, resource_id: str) -> ActionResult:
    """Снимает фоновую автокрафт-задачу с очереди немедленно, БЕЗ траты хода - чтобы
    игрок, запустивший что-то слишком долгое (например, не хватает скорости на много
    ходов вперёд), не был заложником уже принятого решения и мог освободить слот под
    другой ресурс. Прокрутки, которые задача УЖЕ успела сделать (fires_done из
    fires_total), возврату не подлежат - соответствующие ингредиенты и результат уже
    честно обменяны на предыдущих ходах (см. _process_autocraft_jobs: расход и выпуск
    происходят СРАЗУ по факту прокрутки, а не резервируются заранее на весь fires_total) -
    отменяются только ЕЩЁ НЕ сделанные прокрутки, поэтому возвращать здесь нечего.
    Действие, которое всё ещё ждёт этот ресурс (state.pending_actions), само по себе
    не отменяется - если ресурс ему по-прежнему нужен, обычный ретрай на следующем ходу
    заново поставит задачу в очередь (если останется свободный слот)."""
    if resource_id not in state.autocraft_jobs:
        return ActionResult(False, "Такой фоновой задачи уже нет.")
    del state.autocraft_jobs[resource_id]
    return ActionResult(True, f"Автокрафт «{RESOURCE_NAMES[resource_id]}» отменён.")


def _pick_converter_building_with_free_slot(
    state: GameState, energy_jobs: list[dict], machine: str | None,
) -> str | None:
    """Первый ТИП постройки с energy_converter, у которого есть свободный слот
    прямо сейчас (с учётом уже идущих energy_jobs - может быть как state.energy_jobs,
    так и рабочая копия из _ensure_available) - None, если такого типа нет. machine
    (см. content.json energy_recipes.*.machine) сужает перебор до ОДНОГО конкретного
    типа, если задан; иначе перебираются все типы в порядке BUILDINGS/content.json."""
    candidates = [machine] if machine else energy_converter_building_types(state)
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

    __slots__ = ("ingredient_first", "preorder", "kind_of", "action_of", "stats_of")

    def __init__(self):
        self.ingredient_first: list[str] = []  # топологический порядок: сырьё раньше того, кто его ест
        self.preorder: dict[str, int] = {}  # порядок ПЕРВОГО обнаружения (родитель раньше своих ингредиентов) -
        # нужен только для того, чтобы при НЕСКОЛЬКИХ одновременных настоящих ошибках выбрать
        # ту же самую, что показал бы старый рекурсивный код (см. ensure_and_pay)
        self.kind_of: dict[str, str] = {}  # "action" | "energy" | "none" (сырьё/цикл/энергорецепт без обычного)
        self.action_of: dict[str, str] = {}  # только для kind="action"
        self.stats_of: dict[str, tuple[dict[str, int], dict[str, int]]] = {}  # только для kind="action"


def _discover_autocraft_graph(state: GameState, resource_ids, recipe: dict[str, str]) -> _AutocraftGraph:
    """Обходит дерево рецептов ОТ resource_ids ВНИЗ до сырья, заходя в КАЖДЫЙ resource_id
    ровно один раз (не один раз на каждого потребителя) - структура рецепта (какое
    действие, из чего) от количества не зависит, поэтому её достаточно узнать один раз,
    даже если один и тот же ресурс нужен пяти разным веткам разом. Цикл в рецептах
    (resource_id зависит сам от себя через цепочку) определяется классическим DFS
    3-цветным обходом (серый = сейчас на пути обхода, чёрный = уже разобран целиком) -
    НЕ тем же самым, что "уже разобран где-то в дереве": ромб (общий ресурс у двух
    РАЗНЫХ веток) - это нормально и не цикл, серым он бывает только пока раскрывается
    его СОБСТВЕННОЕ поддерево, а не пока на него просто ссылаются."""
    graph = _AutocraftGraph()
    color: dict[str, int] = {}  # 0/нет записи = не тронут, 1 = серый (на пути), 2 = чёрный (готов)

    def visit(resource_id: str) -> None:
        if color.get(resource_id, 0) == 2:
            return
        if color.get(resource_id, 0) == 1:
            graph.kind_of[resource_id] = "none"  # цикл - как будто рецепта нет (см. старую семантику)
            return
        graph.preorder[resource_id] = len(graph.preorder)
        color[resource_id] = 1
        action_id = recipe.get(resource_id)
        if action_id is None:
            graph.kind_of[resource_id] = "energy" if resource_id in ENERGY_RECIPE_FOR_RESOURCE else "none"
        else:
            graph.kind_of[resource_id] = "action"
            graph.action_of[resource_id] = action_id
            effective_cost, effective_gain = effective_manual_action_stats(action_id, state)
            graph.stats_of[resource_id] = (effective_cost, effective_gain)
            for ing_id in effective_cost:
                visit(ing_id)
        color[resource_id] = 2
        graph.ingredient_first.append(resource_id)

    for resource_id in resource_ids:
        visit(resource_id)
    return graph


def _aggregate_autocraft_demand(
    state: GameState, cost: dict[str, int], graph: _AutocraftGraph, jobs: dict[str, dict],
) -> tuple[dict[str, AutocraftFailure], dict[str, float], dict[str, float], dict[str, float], dict[str, str]]:
    """Считает СУММАРНЫЙ спрос на каждый resource_id (от исходного cost плюс от ВСЕХ
    рецептов дерева, которым он нужен как ингредиент), и одновременно решает всё, что
    решаемо БЕЗ знания статуса ингредиентов - открыт ли рецепт/технология/хватает ли
    мощности процессора, не идёт ли по нему уже фоновая задача. Идёт по
    graph.ingredient_first В ОБРАТНОМ порядке (потребитель раньше своего сырья) - к
    тому моменту, как дошли до resource_id, весь спрос НА НЕГО от всех потребителей уже
    посчитан, поэтому fires считается сразу верно и ОДИН раз, без пересчёта задним числом.

    Критично, что "годится ли resource_id для автокрафта вообще" (не_открыт/технология/
    цикл/нет рецепта) проверяется ЗДЕСЬ, ПЕРЕД тем как на его ингредиенты будет пущен
    спрос - а не после, как можно было бы подумать глядя на порядок обхода. Если бы
    проверка ждала своей очереди по ingredient_first (сырьё раньше), сначала пришлось бы
    разбираться с сырьём НЕ_ОТКРЫТОГО родителя - и игрок увидел бы "не хватает сырья",
    хотя настоящая причина - "этот рецепт ещё не открыт", как и было у старого
    рекурсивного кода (там проверка стояла ДО рекурсии в ингредиенты).

    Найденные ошибки НЕ прерывают проход немедленно - копятся в failures (resource_id ->
    AutocraftFailure), а resource_id просто становится тупиком (спрос на его ингредиенты
    не идёт дальше, как будто у него kind="none"). Так проход остаётся за ОДИН раз по
    графу (см. модуль) - другой вариант, вернуться немедленно при первой же находке, дал
    бы более простой код, но привязал бы "какая именно ошибка попадёт игроку" к порядку
    ОБХОДА (потребитель раньше своего сырья), а не к порядку исходного cost и порядку
    ингредиентов В РЕЦЕПТЕ - именно так вела себя старая рекурсия (см. ensure_and_pay,
    где из failures выбирается ошибка с наименьшим graph.preorder - порядком ПЕРВОГО
    обнаружения, который как раз и совпадает со старым рекурсивным порядком).

    resolved_of[resource_id] - что Phase 3 (_commit_autocraft_plan) должна с ним
    сделать: "ok" (запаса хватает, ничего не менять), "existing_job" (уже фоновая
    задача, при необходимости продлена ЗДЕСЬ же), "energy_pending" (гейты пройдены,
    сама попытка запуска/проверки материала - в Phase 3, т.к. конкурирует за
    свободные преобразователи с другими resource_id по порядку обработки),
    "action_pending" (гейты и fires посчитаны, финальное "мгновенно или фоновая
    задача" зависит ещё и от готовности ингредиентов - решается в Phase 3)."""
    total_needed: dict[str, float] = dict(cost)
    fires_map: dict[str, float] = {}
    gain_per_fire_map: dict[str, float] = {}
    resolved_of: dict[str, str] = {}
    failures: dict[str, AutocraftFailure] = {}
    for resource_id in reversed(graph.ingredient_first):
        needed = total_needed.get(resource_id, 0)
        have = state.resources.get(resource_id, 0)
        if needed <= have:
            resolved_of[resource_id] = "ok"
            continue
        if not state.autocraft_enabled:
            failures[resource_id] = AutocraftFailure(resource_id, "autocraft_disabled")
            continue
        if compute_capacity(state) <= 0:
            failures[resource_id] = AutocraftFailure(resource_id, "no_processor")
            continue

        kind = graph.kind_of[resource_id]
        if kind == "none":
            failures[resource_id] = AutocraftFailure(resource_id, "no_recipe")  # сырьё без рецепта (или цикл)
            continue

        if kind == "energy":
            recipe_id = ENERGY_RECIPE_FOR_RESOURCE[resource_id]
            energy_recipe = ENERGY_RECIPES[recipe_id]
            machine = energy_recipe.get("machine")
            if energy_converter_capacity(state, machine) <= 0:
                failures[resource_id] = AutocraftFailure(resource_id, "not_unlocked")
                continue
            if energy_recipe.get("requires_tech") and energy_recipe["requires_tech"] not in state.techs:
                failures[resource_id] = AutocraftFailure(resource_id, "tech_locked")
                continue
            resolved_of[resource_id] = "energy_pending"  # не имеет своих ингредиентов - спрос дальше не идёт
            continue

        # kind == "action"
        if not is_autocraft_unlocked(resource_id, state):
            failures[resource_id] = AutocraftFailure(resource_id, "not_unlocked")
            continue
        action_id = graph.action_of[resource_id]
        action = MANUAL_ACTIONS[action_id]
        if action["requires_tech"] and action["requires_tech"] not in state.techs:
            failures[resource_id] = AutocraftFailure(resource_id, "tech_locked")
            continue
        effective_cost, effective_gain = graph.stats_of[resource_id]
        gain_per_fire = effective_gain[resource_id]
        fires = -(-(needed - have) // gain_per_fire)  # округление вверх

        # Мощность процессора - количество потребляемых рецептом предметов за ОДНУ
        # прокрутку (сложный рецепт "дороже" простого) - жёсткий потолок на размер
        # запроса (не на скорость): если суммарной мощности не хватает даже
        # теоретически, ждать бессмысленно, фоновая задача тут не поможет.
        compute_per_fire = sum(effective_cost.values())
        if fires * compute_per_fire > compute_capacity(state):
            failures[resource_id] = AutocraftFailure(resource_id, "insufficient_compute")
            continue

        # Пока по ресурсу уже идёт фоновая задача, ВЕСЬ его дальнейший выпуск идёт
        # ТОЛЬКО через неё - продлеваем при нехватке, но не докрафчиваем "хвост" отдельным
        # мгновенным путём в обход задачи, и НЕ пускаем спрос дальше на её ингредиенты
        # (она сама заберёт их из наличия по ходу выполнения, см. _process_autocraft_jobs).
        existing = jobs.get(resource_id)
        if existing is not None:
            remaining = existing["fires_total"] - existing["fires_done"]
            projected = have + remaining * gain_per_fire
            if projected < needed:
                existing["fires_total"] += -(-(needed - projected) // gain_per_fire)
            resolved_of[resource_id] = "existing_job"
            continue

        fires_map[resource_id] = fires
        gain_per_fire_map[resource_id] = gain_per_fire
        resolved_of[resource_id] = "action_pending"
        for ing_id, ing_amount in effective_cost.items():
            total_needed[ing_id] = total_needed.get(ing_id, 0) + ing_amount * fires
    return failures, total_needed, fires_map, gain_per_fire_map, resolved_of


def _commit_autocraft_plan(
    state: GameState, graph: _AutocraftGraph, resolved_of: dict[str, str], fires_map: dict[str, float],
    gain_per_fire_map: dict[str, float], resources: dict[str, int], jobs: dict[str, dict], slots: dict[str, int],
    energy_jobs: list[dict],
) -> tuple[AutocraftFailure | None, dict[str, str]]:
    """Второй проход по графу, ПО graph.ingredient_first КАК ЕСТЬ (сырьё раньше того,
    кто его ест) - все гейты (не_открыт/технология/мощность/цикл) уже решены заранее в
    _aggregate_autocraft_demand, здесь только то, что ЗАВИСИТ от статуса ингредиентов
    или порядка обработки:
    - "energy_pending": попытка запуска рецепта конкурирует за свободные преобразователи
      с другими energy_pending resource_id - порядок обработки решает, кому какой достанется;
    - "action_pending": если хоть один ингредиент застрял в фоновой задаче (child_blocked),
      этот рецепт тоже откладывается целиком - его прокрутка не может случиться раньше,
      чем подвезут сырьё. К этому моменту КАЖДЫЙ ингредиент уже обработан
      (ingredient_first), поэтому статус известен точно;
    - тот же порядок (сырьё раньше) даёт фоновым задачам ПОПАДАТЬ в jobs (dict, порядок
      вставки = порядок обработки в _process_autocraft_jobs) в правильной
      последовательности - глубокие (сырьевые) задачи первыми, чтобы успеть подвезти
      сырьё вышестоящим задачам В ТОТ ЖЕ ход (см. CLAUDE.md, _process_autocraft_jobs).
    Возвращает первую настоящую (не "in_progress") ошибку, если она где-то встретилась -
    вызывающий должен ничего не коммитить в state в этом случае (resources/jobs/energy_jobs
    здесь - рабочие копии). status_of - как resolved_of, но "energy_pending"/"action_pending"
    уже разрешены в "ok"/"in_progress"."""
    status_of: dict[str, str] = {}
    for resource_id in graph.ingredient_first:
        resolution = resolved_of.get(resource_id, "ok")
        if resolution in ("ok", "existing_job"):
            status_of[resource_id] = "ok" if resolution == "ok" else "in_progress"
            continue

        if resolution == "energy_pending":
            recipe_id = ENERGY_RECIPE_FOR_RESOURCE[resource_id]
            energy_recipe = ENERGY_RECIPES[recipe_id]
            machine = energy_recipe.get("machine")
            already_running = any(
                j["recipe_id"] == recipe_id and (machine is None or j.get("building_id") == machine)
                for j in energy_jobs
            )
            if not already_running:
                building_id = _pick_converter_building_with_free_slot(state, energy_jobs, machine)
                if building_id is not None:
                    # Рецепт крафта за энергию может нести материальную цену (consumes) в
                    # дополнение к порогу энергии - автокрафт её САМ не докрафчивает
                    # (осознанное упрощение, см. CLAUDE.md), требует, чтобы уже была в наличии.
                    consumes = energy_recipe.get("consumes", {})
                    if not all(resources.get(r, 0) >= amt for r, amt in consumes.items()):
                        return AutocraftFailure(resource_id, "missing_energy_recipe_material"), status_of
                    for r, amt in consumes.items():
                        resources[r] -= amt
                    energy_jobs.append({"recipe_id": recipe_id, "progress": 0, "building_id": building_id})
            status_of[resource_id] = "in_progress"  # партия за энергию всегда растянута на ходы, никогда мгновенно
            continue

        # resolution == "action_pending"
        action_id = graph.action_of[resource_id]
        effective_cost, _ = graph.stats_of[resource_id]
        fires = fires_map[resource_id]
        gain_per_fire = gain_per_fire_map[resource_id]

        child_blocked = any(status_of.get(ing_id) == "in_progress" for ing_id in effective_cost)
        needs_job = child_blocked or fires > autocraft_speed_capacity(state)

        if needs_job:
            if slots["used"] >= autocraft_slot_capacity(state):
                return AutocraftFailure(resource_id, "no_free_slots"), status_of
            jobs[resource_id] = {"action_id": action_id, "fires_total": fires, "fires_done": 0}
            slots["used"] += 1
            status_of[resource_id] = "in_progress"
            continue

        for ing_id, ing_amount in effective_cost.items():
            resources[ing_id] -= ing_amount * fires
        _add_resource(resources, resource_id, fires * gain_per_fire)
        status_of[resource_id] = "ok"

    return None, status_of


def ensure_and_pay(state: GameState, cost: dict[str, int]) -> AutocraftFailure | None:
    """Списывает cost с state.resources. При нехватке чего-либо сперва пытается
    автоматически докрафтить недостающее из более простого сырья (рекурсивно по дереву
    рецептов), если для ресурса открыт автокрафт и хватает мощности процессора. Три исхода:
    - всё нашлось (мгновенно, в пределах скорости автокрафта за этот ход) -> cost
      списывается, возвращается None;
    - часть цепочки не влезает в скорость за один ход -> заводятся/продлеваются фоновые
      задачи в state.autocraft_jobs (уже добытое мгновенно по пути ОСЕДАЕТ в ресурсах -
      это реальный прогресс), cost верхнего действия НЕ списывается, возвращается
      AutocraftFailure(..., "in_progress") - вызывающий должен поставить действие в
      очередь (см. _pay_or_defer), а не считать его провалившимся;
    - настоящая ошибка -> ничего не меняется (ни resources, ни autocraft_jobs, ни
      energy_jobs), возвращается причина.

    Реализация - два прохода по дереву рецептов ВСЕХ resource_id из cost СРАЗУ (а не по
    отдельному дереву на каждый), а не наивная рекурсия "нужно - произвёл - тут же
    потратил": _discover_autocraft_graph строит структуру (что из чего) один раз на
    resource_id, _aggregate_autocraft_demand суммирует спрос на каждый ресурс от ВСЕХ
    потребителей разом, _commit_autocraft_plan одним проходом заводит фоновые задачи/
    мгновенно производит. Это принципиально важно для "ромбов" в дереве рецептов
    (несколько рецептов делят один и тот же ресурс ниже по цепочке, скажем, "шестерня"
    и "пластина" из одного "слитка") - наивная рекурсия production-and-consume-immediately
    заставляла бы каждую ветку заново выводить с нуля весь путь до общего ресурса, потому
    что к её очереди он уже был потрачен предыдущей веткой (тот же слиток был сделан и
    тут же расходован на шестерню, для пластины его как бы и не было) - на самоподобном
    дереве из N уровней "ромбов" это давало ЧИСТУЮ ЭКСПОНЕНТУ по числу вызовов (подтверждено
    профилированием, 2026-09-10 - см. CLAUDE.md). Агрегация спроса ДО производства убирает
    экспоненту полностью: каждый resource_id разбирается ровно один раз, сложность
    пропорциональна размеру самого дерева рецептов (вершины + рёбра), а не числу путей до
    общего ресурса. Побочный эффект (не баг, аккуратно сохранён specifically) - раньше
    compute_power/autocraft_speed проверялись НЕЗАВИСИМО на fires каждой отдельной ветки,
    так что несколько веток, СОВМЕСТНО делящих ресурс, могли в СУММЕ превысить лимит
    процессора/скорости за один и тот же вызов, хотя каждая по отдельности укладывалась -
    теперь проверка идёт на УЖЕ АГРЕГИРОВАННЫЙ fires, как и задумано лимитом."""
    working_resources = dict(state.resources)
    working_jobs = {rid: dict(job) for rid, job in state.autocraft_jobs.items()}
    working_energy_jobs = [dict(job) for job in state.energy_jobs]
    slots = {"used": len(working_jobs)}
    recipe = resource_recipe(state)

    graph = _discover_autocraft_graph(state, cost.keys(), recipe)
    failures, total_needed, fires_map, gain_per_fire_map, resolved_of = _aggregate_autocraft_demand(
        state, cost, graph, working_jobs,
    )
    if failures:
        # При нескольких одновременных настоящих ошибках сразу - та, что попала бы игроку
        # от старого рекурсивного кода: он спускался по cost в порядке ключей и по
        # ингредиентам в порядке рецепта, останавливаясь на ПЕРВОЙ найденной - то же самое
        # даёт наименьший graph.preorder (порядок первого обнаружения при обходе).
        best_id = min(failures, key=lambda rid: graph.preorder[rid])
        return failures[best_id]  # настоящая ошибка - state.resources/autocraft_jobs/energy_jobs не трогаем
    hard_failure, status_of = _commit_autocraft_plan(
        state, graph, resolved_of, fires_map, gain_per_fire_map,
        working_resources, working_jobs, slots, working_energy_jobs,
    )
    if hard_failure is not None:
        return hard_failure  # настоящая ошибка - state.resources/autocraft_jobs/energy_jobs не трогаем

    queued: AutocraftFailure | None = None
    for resource_id in cost:
        if status_of.get(resource_id) == "in_progress":
            queued = AutocraftFailure(resource_id, "in_progress")
            break

    if queued is not None:
        state.resources = working_resources
        state.autocraft_jobs = working_jobs
        state.energy_jobs = working_energy_jobs
        return queued

    for resource_id, amount in cost.items():
        working_resources[resource_id] -= amount
    state.resources = working_resources
    state.autocraft_jobs = working_jobs
    state.energy_jobs = working_energy_jobs
    return None


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


def _accumulate_raw_shortfall(
    state: GameState, resource_id: str, amount: int | float, visiting: frozenset[str],
    working: dict[str, int | float], missing: dict[str, int | float], recipe: dict[str, str],
) -> None:
    have = working.get(resource_id, 0)
    if have >= amount:
        working[resource_id] = have - amount
        return
    deficit = amount - have
    working[resource_id] = 0
    action_id = None if resource_id in visiting else recipe.get(resource_id)
    if action_id is None:
        # Лист дерева рецептов (настоящее сырьё, цикл рецептов или ресурс, который
        # добывается только крафтом за энергию) - дальше раскладывать некуда,
        # именно это и показываем игроку.
        missing[resource_id] = missing.get(resource_id, 0) + deficit
        return
    effective_cost, effective_gain = effective_manual_action_stats(action_id, state)
    gain_per_fire = effective_gain.get(resource_id, 0)
    if gain_per_fire <= 0:
        missing[resource_id] = missing.get(resource_id, 0) + deficit
        return
    fires = -(-deficit // gain_per_fire)  # округление вверх, как в _ensure_available
    next_visiting = visiting | {resource_id}
    for ing_id, ing_amount in effective_cost.items():
        _accumulate_raw_shortfall(state, ing_id, ing_amount * fires, next_visiting, working, missing, recipe)
    # Что этот рецепт произведёт сверх покрытого deficit, оседает в working как
    # излишек - следующая ветка того же cost может утилизировать его, не требуя
    # для себя лишнего сырья по новой (та же идея, что "продолжает работать на
    # рабочей копии", см. _ensure_available).
    working[resource_id] = working.get(resource_id, 0) + fires * gain_per_fire - deficit


def _raw_shortfall(state: GameState, cost: dict[str, int]) -> dict[str, int | float]:
    """Недостача, пересчитанная в БАЗОВЫЕ ресурсы - рекурсивно по цепочке рецептов
    (resource_recipe) до листьев, тем же способом, каким её на самом деле обошёл бы
    автокрафт (_ensure_available), но БЕЗ проверок разблокировки/технологии/
    мощности процессора - это чистый пересчёт количества, а не повторная попытка
    автокрафта. Используется, когда автокрафт в игре задействован (есть процессор
    и галочка включена): игроку интереснее узнать, скольких дров не хватает, а не
    скольких досок - доски автокрафт всё равно попытается сделать сам из дров.
    Конкретную причину, ПОЧЕМУ автокрафт застрял (не открыт/не изучено/не хватает
    мощности и т.п.), по-прежнему объясняет отдельно describe_autocraft_failure -
    эта функция только про "сколько и чего", не про "почему"."""
    working: dict[str, int | float] = dict(state.resources)
    missing: dict[str, int | float] = {}
    recipe = resource_recipe(state)
    for resource_id, amount in cost.items():
        _accumulate_raw_shortfall(state, resource_id, amount, frozenset(), working, missing, recipe)
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


def autocraft_eta_turns(state: GameState, fires: int | float) -> int | None:
    """Через сколько ходов завершатся fires прокруток автокрафт-рецепта при ТЕКУЩЕЙ
    скорости (autocraft_speed_capacity) - None, если fires укладывается в один ход
    (мгновенно: фоновая задача вообще не понадобится, см. _ensure_available:
    needs_job = fires > autocraft_speed_capacity(state)). То же округление вверх,
    что и в autocraft_jobs_view для уже идущих задач - здесь используется ДО того,
    как игрок согласится на действие (см. autocraft_preview), чтобы окно
    подтверждения могло заранее предупредить "это растянется на N ходов"."""
    if fires <= 0:
        return None
    speed = autocraft_speed_capacity(state)
    if fires <= speed:
        return None
    return -(-fires // speed)  # округление вверх


def _preview_node(
    state: GameState, resource_id: str, amount: int | float, visiting: frozenset[str],
    working: dict[str, int | float], recipe: dict[str, str],
) -> dict:
    """Один узел дерева предпросмотра автокрафта (см. autocraft_preview) - сколько
    нужно resource_id, сколько из этого возьмётся из уже имеющегося запаса
    (working - рабочая копия state.resources, обедняется по ходу обхода, как и в
    _accumulate_raw_shortfall, чтобы несколько веток одного cost не задваивали
    разбор одного и того же сырья), и - если остатка не хватает - каким рецептом
    и сколько раз (fires) он докрафчивается, с результатом в children. "turns" -
    оценка (см. autocraft_eta_turns), растянется ли ИМЕННО этот шаг цепочки на
    несколько ходов - None, если он выполнится мгновенно."""
    stock_before = working.get(resource_id, 0)
    taken = min(amount, stock_before)
    working[resource_id] = stock_before - taken
    node = {
        "resource_id": resource_id, "needed": amount, "stock_before": stock_before,
        "taken": taken, "action_id": None, "fires": 0, "turns": None, "children": [],
    }
    deficit = amount - taken
    if deficit <= 0 or resource_id in visiting:
        return node
    action_id = recipe.get(resource_id)
    if action_id is None:
        return node  # сырьё (или ресурс только из крафта за энергию) - дальше раскладывать некуда
    effective_cost, effective_gain = effective_manual_action_stats(action_id, state)
    gain_per_fire = effective_gain.get(resource_id, 0)
    if gain_per_fire <= 0:
        return node
    fires = -(-deficit // gain_per_fire)  # округление вверх, как в _ensure_available
    node["action_id"] = action_id
    node["fires"] = fires
    node["turns"] = autocraft_eta_turns(state, fires)
    next_visiting = visiting | {resource_id}
    for ing_id, ing_amount in effective_cost.items():
        node["children"].append(_preview_node(state, ing_id, ing_amount * fires, next_visiting, working, recipe))
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
    автокрафт сейчас в принципе не может быть задействован (нет процессора
    или выключен галочкой - state.autocraft_enabled/compute_capacity, та же
    развилка, что в _pay_or_defer для "no_processor"/"autocraft_disabled") -
    в обоих случаях показывать окно "сколько потратится" бессмысленно: в
    первом случае это и так ровно cost, без докрафта; во втором действие всё
    равно тут же провалится с голым "не хватает ресурсов", как обычно.

    Это ЧИСТЫЙ пересчёт количества (та же арифметика, что в _ensure_available),
    а не повторная попытка автокрафта - НЕ проверяет разблокировку/технологию/
    мощность процессора/скорость/свободные слоты. Если что-то из этого реально
    заблокирует автокрафт, настоящий ensure_and_pay (вызванный уже ПОСЛЕ
    подтверждения) откажет отдельно, со своим обычным сообщением - эта функция
    только про "сколько и чего", не про "получится ли" (та же философия, что
    уже была у _raw_shortfall)."""
    if not (state.autocraft_enabled and compute_capacity(state) > 0):
        return None
    if all(state.resources.get(r, 0) >= amount for r, amount in cost.items()):
        return None
    working = dict(state.resources)
    recipe = resource_recipe(state)
    nodes = [_preview_node(state, resource_id, amount, frozenset(), working, recipe) for resource_id, amount in cost.items()]
    return {"nodes": nodes}


def _pay_or_defer(
    state: GameState, cost: dict[str, int], action_desc: str, kind: str, target_id: str | None,
    retry_extra: dict | None = None,
) -> ActionResult | None:
    """Общий хвост для действий с ценой. Три исхода:
    - None - оплата прошла мгновенно, вызывающий продолжает применять эффект действия сейчас;
    - ActionResult(True, ...) - не хватило скорости автокрафта за один ход, действие
      поставлено в очередь (state.pending_actions) и само завершится, когда фоновые
      задачи докрафтят нужное (см. _process_pending_actions в advance_turn);
    - ActionResult(False, ...) - настоящая ошибка, действие не выполнено и не поставлено в очередь.

    retry_extra - необязательные ДОПОЛНИТЕЛЬНЫЕ поля, которые нужно запомнить в
    state.pending_actions вместе с kind/target_id/label, чтобы retry (см.
    _PENDING_RETRY/_process_pending_actions) мог повторить действие ТОЧНО с теми же
    параметрами, что были при первой попытке - например, "scale" у ручного действия
    с выбранным игроком масштабом (см. do_manual_action): без этого отложенное
    действие при повторной попытке молча откатилось бы на масштаб по умолчанию."""
    failure = ensure_and_pay(state, cost)
    if failure is None:
        return None
    if failure.reason == "in_progress":
        _enqueue_pending(state, kind, target_id, action_desc, retry_extra)
        return ActionResult(
            True, f"Автокрафт для {action_desc} запущен в фоне - действие завершится само, когда ресурсов хватит.",
            pending=True,
        )
    if failure.reason in ("no_processor", "autocraft_disabled"):
        # Ни процессора нет, ни игрок сам выключил галочку "Автокрафт" - в обоих
        # случаях сообщение должно звучать в точности как обычная нехватка при
        # ручном крафте, без единого намёка на автокрафт/процессор/память (см.
        # AutocraftFailure). Недостачу при этом считаем ПРЯМО по cost - без
        # автокрафта промежуточных ресурсов не бывает, всё несётся руками.
        message = f"Не хватает ресурсов на {action_desc}."
        shortfall = _direct_shortfall(state, cost)
        if shortfall:
            message += f" Не хватает: {_format_shortfall(shortfall)}."
        return ActionResult(False, message)
    message = f"Не хватает ресурсов на {action_desc}: {describe_autocraft_failure(failure)}."
    # Автокрафт задействован (есть процессор, галочка включена) - показываем
    # недостачу в БАЗОВЫХ ресурсах (см. _raw_shortfall), а не в том промежуточном
    # ресурсе, на котором конкретно споткнулась цепочка.
    shortfall = _raw_shortfall(state, cost)
    if shortfall:
        message += f" Не хватает сырья: {_format_shortfall(shortfall)}."
    return ActionResult(False, message)


def _enqueue_pending(
    state: GameState, kind: str, target_id: str | None, label: str, extra: dict | None = None,
) -> None:
    for pending in state.pending_actions:
        if pending["kind"] == kind and pending["target_id"] == target_id:
            return  # уже в очереди - не дублируем
    entry = {"kind": kind, "target_id": target_id, "label": label}
    if extra:
        entry.update(extra)
    state.pending_actions.append(entry)


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
    постройки, от которой действие зависит (MANUAL_ACTIONS[id]["requires_building"] -
    см. _apply_building_upgrades). Именно так улучшения "механизма, который сам по
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
    (см. CLAUDE.md): домножает ТОЛЬКО gain (cost не трогает, по аналогии с тем,
    как тир инструмента в шахте домножает base_yield, а не cost добычи) на
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
    building_id = action.get("requires_building")
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
    building_id = MANUAL_ACTIONS[action_id].get("requires_building")
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
        if upgrade["id"] in purchased and upgrade["type"] == "scale":
            cap = max(cap, upgrade["value"])
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


def is_manual_action_unlocked(action_id: str, state: GameState) -> bool:
    action = MANUAL_ACTIONS[action_id]
    req_tech = action["requires_tech"]
    if req_tech and req_tech not in state.techs:
        return False
    # requires_building - необязательный доп. гейт: нужна хотя бы одна РАБОТАЮЩАЯ
    # (не сломанная целиком) копия конкретной постройки, а не просто "когда-то построена".
    req_building = action.get("requires_building")
    if req_building and working_count(state, req_building) <= 0:
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


def is_mining_unlocked(resource_id: str, state: GameState) -> bool:
    info = MINING_INFO[resource_id]
    if info["requires_tech"] and info["requires_tech"] not in state.techs:
        return False
    # Добыча руды сознательно НЕ переведена на requires_tool_tiers (галочки на
    # конкретные тиры) - по смыслу это как было, порог "начиная с тира N", просто
    # теперь ссылается на инструмент по id (required_tool), а не жёстко на кирку.
    return tool_tier(state, info["required_tool"]) >= info["required_tier"]


def available_mining(state: GameState) -> list[str]:
    return [r for r in MINING_INFO if is_mining_unlocked(r, state)]


def mining_yield_per_turn(resource_id: str, state: GameState) -> int:
    """Скорость добычи руды из активной жилы за один клик - зависит ТОЛЬКО от
    тира требуемого инструмента (в будущем сюда может добавиться навык копания).
    Качество жилы на скорость НЕ влияет - оно домножает только итоговый размер
    жилы в момент находки, см. _pick_vein_quality_tier/search_vein_any."""
    info = MINING_INFO[resource_id]
    tier_bonus = tool_tier(state, info["required_tool"]) - info["required_tier"] + 1
    return info["base_yield"] * max(1, tier_bonus)


def _pick_vein_quality_tier(resource_id: str, state: GameState) -> int | None:
    """Взвешенно выбирает индекс тира качества для только что найденной жилы
    resource_id - НЕ ту же кандидатскую формулу, что у выбора самой руды (там
    rarity, тут явный weight), но тот же инструмент random.choices. В пул попадают
    только тиры, чей min_tool_tier не выше ТЕКУЩЕГО тира требуемого инструмента -
    чем лучше инструмент, тем больше хороших тиров качества участвует в розыгрыше
    (и тем выше их относительный вес после нормализации). Возвращает None, если у
    ресурса вообще нет quality_tiers - тогда search_vein_any не трогает базовый
    случайный размер жилы (множитель 1, старое поведение)."""
    quality_tiers = MINING_INFO[resource_id].get("quality_tiers")
    if not quality_tiers:
        return None
    current_tier = tool_tier(state, MINING_INFO[resource_id]["required_tool"])
    eligible = [
        (i, q) for i, q in enumerate(quality_tiers) if current_tier >= q.get("min_tool_tier", 1)
    ]
    if not eligible:
        # Защитный случай (не должен наступать при валидном контенте - валидатор
        # требует хотя бы один тир с min_tool_tier <= required_tier) - берём первый
        # тир списка, чтобы не падать.
        eligible = [(0, quality_tiers[0])]
    weights = [q["weight"] for _, q in eligible]
    chosen_index, _ = random.choices(eligible, weights=weights, k=1)[0]
    return chosen_index


def can_search_for_vein(state: GameState) -> bool:
    return bool(available_mining(state))


def search_vein_any(state: GameState) -> ActionResult:
    """Единая кнопка поиска жилы в шахте. Активна ВСЕГДА только одна жила: новый
    поиск полностью заменяет предыдущую находку (даже неисчерпанную) - предыдущая
    жила просто теряется, если её не докопали. Шанс на каждую руду (по rarity, чем
    МЕНЬШЕ rarity - тем чаще) считается заново из ВСЕХ разведанных руд (доступных
    текущим тиром нужного инструмента и технологией) при КАЖДОМ поиске, включая те, что уже
    находились раньше - поиск может повторно найти тот же ресурс сколько угодно
    раз подряд. Это осознанно: если бы уже найденные руды исключались из выбора
    (как раньше), редкая руда гарантированно находилась бы, как только все более
    частые руды уже были найдены хотя бы по разу - шанс наткнуться на неё должен
    оставаться прежним при любом отдельном поиске, а не расти по мере "исчерпания"
    остальных вариантов. Качество жилы (если у руды заданы quality_tiers)
    домножает БАЗОВЫЙ случайный запас (size_multiplier, округление вверх - руда
    целочисленный ресурс) - на скорость добычи за ход качество не влияет,
    только на итоговый запас."""
    candidates = available_mining(state)
    if not candidates:
        return ActionResult(False, "Искать пока нечего: ни одна руда ещё не разведана.")
    weights = [1 / MINING_INFO[rid]["rarity"] for rid in candidates]
    resource_id = random.choices(candidates, weights=weights, k=1)[0]
    size = random.randint(*MINING_INFO[resource_id]["vein_size_range"])
    quality_index = _pick_vein_quality_tier(resource_id, state)
    if quality_index is None:
        state.veins = {resource_id: size}
        state.vein_quality = {}
        return ActionResult(True, f"Найдена жила: {RESOURCE_NAMES[resource_id]} (запас: {size}).")
    quality_tiers = MINING_INFO[resource_id]["quality_tiers"]
    size = math.ceil(size * quality_tiers[quality_index]["size_multiplier"])
    state.veins = {resource_id: size}
    state.vein_quality = {resource_id: quality_index}
    quality_name = quality_tiers[quality_index]["name"]
    return ActionResult(
        True, f"Найдена жила: {RESOURCE_NAMES[resource_id]} ({quality_name}, запас: {size})."
    )


def mine_vein(state: GameState, resource_id: str) -> ActionResult:
    if not is_mining_unlocked(resource_id, state):
        return ActionResult(False, "Для этого ресурса ещё не хватает технологии или инструмента нужного тира.")
    remaining = state.veins.get(resource_id)
    if remaining is None:
        return ActionResult(False, "Сначала нужно найти жилу.")
    name = RESOURCE_NAMES[resource_id]
    extracted = min(remaining, mining_yield_per_turn(resource_id, state))
    _add_resource(state.resources, resource_id, extracted)
    remaining -= extracted
    if remaining <= 0:
        del state.veins[resource_id]
        note = " Жила истощена, нужно искать новую."
    else:
        state.veins[resource_id] = remaining
        note = f" (осталось в жиле: {remaining})"
    return ActionResult(True, f"Добыто: +{extracted} {name}.{note}")


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
    этого поля). См. content.json tools.*.tiers[].max_durability."""
    max_durability = _current_tool_tier_def(state, tool_id).get("max_durability")
    if max_durability is None:
        return None
    return state.tool_durability.get(tool_id, max_durability)


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
    """Платный ремонт инструмента, исчерпавшего прочность (см. tool_is_broken) -
    тир НЕ теряется (в отличие от построек, тут нечего "разбирать" - см.
    CLAUDE.md/договорённость с игроком), прочность просто сбрасывается на
    максимум текущего тира. Цена - tiers[тир-1]["repair_cost"] (обязателен в
    контенте, если у тира задан max_durability - см. content_validation.py)."""
    tool_name = TOOLS[tool_id]["name"]
    if not tool_is_broken(state, tool_id):
        return ActionResult(False, f"«{tool_name}» не нуждается в ремонте.")
    cost = _current_tool_tier_def(state, tool_id).get("repair_cost", {})
    deferred = _pay_or_defer(state, cost, f"ремонт инструмента «{tool_name}»", "repair_tool", tool_id)
    if deferred is not None:
        return deferred
    state.tool_durability.pop(tool_id, None)
    return ActionResult(True, f"«{tool_name}» отремонтирован.")


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
    damage_multiplier/instant_kill_max_hp/loot_multiplier/soul_chance_multiplier.
    Среди ВСЕХ владеемых линеек оружия действует максимум по каждому полю
    независимо (тот же принцип "сильнейшее побеждает", что у building upgrades) -
    так уникальный меч-ваншот (свой tool_id с ОДНИМ тиром) и обычный прогрессивный
    меч работают одновременно, не мешая друг другу: у одного из них может быть
    выше damage_multiplier, у другого - instant_kill_max_hp."""
    damage_multiplier = 1.0
    instant_kill_max_hp = 0
    loot_multiplier = 1.0
    soul_chance_multiplier = 1.0
    for tool_id, tool in TOOLS.items():
        if not tool.get("weapon"):
            continue
        tiers = tool.get("tiers", [])
        index = tool_tier(state, tool_id) - 1
        if not (0 <= index < len(tiers)):
            continue
        tier = tiers[index]
        damage_multiplier = max(damage_multiplier, tier.get("damage_multiplier", 1.0))
        instant_kill_max_hp = max(instant_kill_max_hp, tier.get("instant_kill_max_hp", 0))
        loot_multiplier = max(loot_multiplier, tier.get("loot_multiplier", 1.0))
        soul_chance_multiplier = max(soul_chance_multiplier, tier.get("soul_chance_multiplier", 1.0))
    return {
        "damage_multiplier": damage_multiplier,
        "instant_kill_max_hp": instant_kill_max_hp,
        "loot_multiplier": loot_multiplier,
        "soul_chance_multiplier": soul_chance_multiplier,
    }


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
    """Единая кнопка поиска цели в локации-"Охоте" - зеркало search_vein_any:
    рандом по rarity (чем МЕНЬШЕ - тем чаще), активна ВСЕГДА только ОДНА цель на
    ВСЮ игру (state.active_encounter, не по локациям) - новый поиск заменяет
    предыдущую находку целиком, даже недобитую, и даже если это был призванный
    босс (уже потраченный на призыв предмет не возвращается - см. summon_boss)."""
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
    сама (см. _process_combat в advance_turn) - "не добил вовремя, босс сбежал"."""
    mob = MOBS.get(boss_id)
    if mob is None or not mob.get("is_boss"):
        return ActionResult(False, "Такого босса не существует.")
    if not is_mob_unlocked(boss_id, state):
        return ActionResult(False, "Этот босс ещё не открыт.")
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


def _move_damage(state: GameState, mob: dict, remaining_hp: int, move: dict | None) -> tuple[int, bool]:
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
    - весь remaining_hp, чтобы вызов "снял remaining - damage" тоже давал 0 без спецкейса)."""
    bonuses = _weapon_bonuses(state)
    base_damage = skill_level_effects(state, COMBAT_SKILL_ID).get("damage", 1)
    mode = move.get("mode", "multiplier") if move else "multiplier"
    if mode == "percent_max_hp":
        return max(1, math.ceil(move["percent"] * mob["hp"])), False
    if mode == "percent_remaining_hp":
        return max(1, math.ceil(move["percent"] * remaining_hp)), False
    if mode == "execute" and remaining_hp <= move["execute_threshold_percent"] * mob["hp"]:
        return remaining_hp, True
    move_multiplier = 1.0
    if move is not None:
        move_multiplier = move.get("damage_multiplier", 1.0) if mode == "execute" else move["damage_multiplier"]
    return max(1, math.ceil(base_damage * bonuses["damage_multiplier"] * move_multiplier)), False


def attack_encounter(state: GameState, move_id: str | None = None) -> ActionResult:
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
    bonuses = _weapon_bonuses(state)
    remaining = encounter["remaining_hp"]
    damage, forced_kill = _move_damage(state, mob, remaining, move)
    instant = forced_kill or remaining <= bonuses["instant_kill_max_hp"]
    killed = instant or damage >= remaining
    if move is not None:
        cooldown_turns = move.get("cooldown_turns", 0)
        if cooldown_turns > 0:
            state.move_cooldowns[move_id] = cooldown_turns
    if not killed:
        encounter["remaining_hp"] = remaining - damage
        return ActionResult(
            True, f"Урон: -{damage} HP. Осталось: {encounter['remaining_hp']}/{mob['hp']}.",
            combat={"damage": damage, "killed": False, "instant_kill": False,
                    "remaining_hp": encounter["remaining_hp"], "max_hp": mob["hp"]},
        )

    loot = dict(mob.get("loot", {}))
    loot_chance = mob.get("loot_chance")
    if loot_chance:
        loot = _roll_chance_amounts(loot, loot_chance)
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

    add_skill_xp(state, COMBAT_SKILL_ID, mob.get("xp_reward", 0))
    state.active_encounter = None
    message = f"Убит: {mob['name']}."
    if got_soul:
        message += f" Выбита душа: {RESOURCE_NAMES.get(soul_id, soul_id)}!"
    return ActionResult(
        True, message,
        combat={"damage": damage, "killed": True, "instant_kill": instant, "remaining_hp": 0, "max_hp": mob["hp"]},
    )


def soul_collector_capacity(state: GameState, building_id: str) -> int:
    """Сколько душ можно разом держать в синтезаторе building_id - per-copy slots
    (buildings.*.soul_collector.slots) * число РАБОТАЮЩИХ копий, плюс АБСОЛЮТНАЯ
    добавка от сильнейшего купленного апгрейда типа soul_collector_slots (тот же
    принцип "сильнейшее побеждает", что у energy_converter_max_energy_per_turn -
    см. _farm_upgrade_multiplier). 0, если постройка не синтезатор душ вовсе,
    или если ВСЕ копии сломаны - бонус от апгрейда гейтится тем же working_count,
    что и база, иначе сломанная постройка с уже купленным апгрейдом слотов
    продолжала бы бесконечно производить ресурсы (см. _process_soul_collectors,
    которая сама поломку не проверяет и полагается на capacity == 0)."""
    config = BUILDINGS.get(building_id, {}).get("soul_collector")
    if not config or working_count(state, building_id) <= 0:
        return 0
    base = config.get("slots", 0) * working_count(state, building_id)
    bonus = _farm_upgrade_multiplier(state, building_id, "soul_collector_slots", default=0)
    return int(base + bonus)


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


def insert_soul(state: GameState, building_id: str, soul_id: str) -> ActionResult:
    config = BUILDINGS.get(building_id, {}).get("soul_collector")
    if not config:
        return ActionResult(False, "Это не синтезатор душ.")
    name = RESOURCE_NAMES.get(soul_id, soul_id)
    accepts_kind = config.get("accepts_kind")
    if accepts_kind is None or soul_kind(soul_id) != accepts_kind:
        return ActionResult(False, f"«{name}» не подходит для этого синтезатора.")
    installed = state.soul_slots.setdefault(building_id, [])
    if len(installed) >= soul_collector_capacity(state, building_id):
        return ActionResult(False, "Нет свободных слотов.")
    if state.resources.get(soul_id, 0) < 1:
        return ActionResult(False, f"Нет в наличии: «{name}».")
    state.resources[soul_id] -= 1
    installed.append(soul_id)
    return ActionResult(True, f"«{name}» помещена в синтезатор.")


def remove_soul(state: GameState, building_id: str, index: int) -> ActionResult:
    """Возвращает вставленную душу обратно в инвентарь - не тратит ход, как и
    cancel_energy_job/house_creature. index - позиция в state.soul_slots[building_id]
    (список, не фиксированные слоты - см. state.py)."""
    installed = state.soul_slots.get(building_id, [])
    if not isinstance(index, int) or not (0 <= index < len(installed)):
        return ActionResult(False, "Такой души тут нет.")
    soul_id = installed.pop(index)
    _add_resource(state.resources, soul_id, 1)
    return ActionResult(True, f"«{RESOURCE_NAMES.get(soul_id, soul_id)}» возвращена в инвентарь.")


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


AUTOMATION_TARGET_KINDS = ("manual_action", "energy_recipe")


def _is_automation_target_eligible(kind: str, target_id: str) -> bool:
    if kind == "manual_action":
        return is_automation_eligible_action(target_id)
    if kind == "energy_recipe":
        return is_automation_eligible_energy_recipe(target_id)
    return False


def _automation_target_name(kind: str, target_id: str) -> str:
    return MANUAL_ACTIONS[target_id]["name"] if kind == "manual_action" else ENERGY_RECIPES[target_id]["name"]


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
    """Не тратит ход - настройка правила, как insert_soul. kind - "manual_action"
    или "energy_recipe" (AUTOMATION_TARGET_KINDS), target_id - id действия/рецепта
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
    """Каждый ход, ПОСЛЕ _process_manual_jobs и ДО _process_pending_actions (см.
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
    не в тот же ход)."""
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
            else:
                continue
            if not all(_automation_condition_met(c, state) for c in rule["conditions"]):
                continue
            result = (
                do_manual_action(state, target_id) if kind == "manual_action"
                else start_energy_recipe(state, target_id)
            )
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
    отдельная бухгалтерия."""
    config = RESOURCE_STORAGE.get(resource_id)
    if config is None:
        return None
    tier = state.storage_tier.get(resource_id, 0)
    capacity = config["base_capacity"]
    for upgrade in config["tiers"][:tier]:
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
    return ActionResult(True, f"Склад «{name}» расширен: вместимость теперь {storage_capacity(state, resource_id)}.")


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


def farm_instance_upgrade_purchase_count(state: GameState, instance_id: str, upgrade_id: str) -> int:
    instance = state.farm_instances.get(instance_id)
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
    цена новой платится полностью). Не тратит ход - управление имуществом, как
    house_creature/release_creature."""
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
    if _is_farm_building(building_id):
        _ensure_farm_instances(state)
        return sum(
            1 for inst in state.farm_instances.values()
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
    if _is_farm_building(building_id):
        _ensure_farm_instances(state)
        types = [
            inst.get("broken_type") for inst in state.farm_instances.values()
            if inst["building_id"] == building_id and inst["broken"]
        ]
    else:
        types = _broken_type_entries(state, building_id)
    counts: dict[str | None, int] = {}
    for t in types:
        if not _is_breakdown_type_fixable(state, t):
            counts[t] = counts.get(t, 0) + 1
    return list(counts.items())


def energy_breakdown(state: GameState) -> tuple[int, int, int]:
    """(запас, генерация/ход, потребление/ход) энергии по текущему парку РАБОТАЮЩИХ построек
    В ЭТОТ ХОД - постройки, которых сейчас не время суток (например, солнечные панели
    ночью) или которых игрок выключил ползунком "Механизмы" (building_active_count),
    в генерацию/потребление не считаются, иначе показанные цифры обещали бы
    больше, чем реально будет произведено следующим advance_turn."""
    daytime = is_daytime(state)
    generation = 0
    consumption = 0
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0 or not is_building_active_now(building_id, daytime):
            continue
        active = building_active_count(state, building_id)
        if active <= 0:
            continue
        produces, consumes = effective_building_stats(building_id, state.techs, state.building_upgrades)
        # _amount_estimate - это прогноз "на следующий ход", а не сам момент
        # начисления, поэтому диапазон (см. _is_range_amount) показывается своей
        # серединой, а не бросается заново (см. _roll_range_amounts).
        generation += _amount_estimate(produces.get(ENERGY_RESOURCE_ID, 0)) * active
        consumption += consumes.get(ENERGY_RESOURCE_ID, 0) * active
    return state.resources.get(ENERGY_RESOURCE_ID, 0), generation, consumption


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
    НЕ возвращается (она уже потрачена по-настоящему - см. _process_energy_jobs);
    возвращать, помимо неё, нечего - крафт за энергию не резервирует никаких других
    ресурсов при запуске (см. start_energy_recipe)."""
    if not isinstance(index, int) or not (0 <= index < len(state.energy_jobs)):
        return ActionResult(False, "Такого крафта за энергию уже нет.")
    job = state.energy_jobs.pop(index)
    name = ENERGY_RECIPES[job["recipe_id"]]["name"]
    return ActionResult(True, f"Крафт за энергию «{name}» отменён.")


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
    state.energy_jobs.append({"recipe_id": recipe_id, "progress": 0, "building_id": building_id})
    return ActionResult(True, f"Запущен крафт за энергию: {recipe['name']}.")


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


def _roll_wild_genotype(species_id: str) -> dict:
    """Дикая особь ГОМОЗИГОТНА по построению на каждый признак (обе аллели пары
    одинаковы, взвешенно розыгранные по weight) - без иллюзии скрытых генов у
    только что пойманной особи; разнообразие появляется через скрещивание/мутацию."""
    genotype: dict[str, list[str]] = {}
    for trait_id in GENOTYPE_TRAIT_IDS:
        if trait_id == "species":
            genotype[trait_id] = [species_id, species_id]
            continue
        alleles = trait_alleles(trait_id)
        ids = list(alleles.keys())
        weights = [alleles[a].get("weight", 1) for a in ids]
        chosen = random.choices(ids, weights=weights, k=1)[0]
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
    creature["farm_instance_id"] = farm_instance_id
    creature["turns_alive"] = 0
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

    После основной генотипной части - отдельный шанс (genetics.environment_mutation_chance)
    ПОЛНОСТЬЮ переиграть пару признака "environment" заново (взвешенно по weight,
    как у дикой особи - _roll_wild_genotype), поверх уже отыгранного обычного
    менделевского наследования от родителей выше - редкая "мутация среды обитания",
    не связанная с мутацией вида."""
    child: dict[str, list[str]] = {}
    for trait_id in GENOTYPE_TRAIT_IDS:
        if trait_id == "species":
            continue
        child[trait_id] = [
            random.choice(_genotype_pair(pair_a, trait_id)), random.choice(_genotype_pair(pair_b, trait_id)),
        ]

    if random.random() < GENETICS_SETTINGS.get("environment_mutation_chance", 0.0):
        alleles = trait_alleles("environment")
        ids = list(alleles.keys())
        weights = [alleles[a].get("weight", 1) for a in ids]
        chosen = random.choices(ids, weights=weights, k=1)[0]
        child["environment"] = [chosen, chosen]

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
    for mutation in candidates:
        cursor += mutation["chance"] * mult
        if roll < cursor:
            outcomes = mutation["outcomes"]
            weights = [o["weight"] for o in outcomes]
            mutated_species = random.choices(outcomes, weights=weights, k=1)[0]["species_id"]
            child["species"] = [mutated_species, mutated_species]
            return child, True

    child_species = species_a if roll < cursor + (1 - cursor) / 2 else species_b
    child["species"] = [child_species, child_species]
    return child, False


def breed_creatures(state: GameState, parent_a_id: str, parent_b_id: str) -> ActionResult:
    """Скрещивает двух РАЗНЫХ особей игрока (из банка или уже поселённых в
    ЛЮБУЮ ферму - неважно, в одну и ту же или в разные) - требует работающий
    механизм скрещивания (breeding_station_available), но НЕ привязано к
    конкретной ферме: сама ферма (apiary/electric_apiary) для скрещивания
    участия не принимает, только расселяет готовых особей. Скрещивание ВСЕГДА
    бесплатно, кроме самих родителей (списываются целиком) - никакой доп. цены
    сверху (раньше была настраиваемая content.json["genetics"]["breeding_cost"],
    убрана по прямому решению). Ребёнок добавляется в банк НЕОПОЗНАННЫМ. Тратит
    ход."""
    if not breeding_station_available(state):
        return ActionResult(False, "Нужна работающая станция скрещивания.")
    if parent_a_id == parent_b_id or parent_a_id not in state.creatures or parent_b_id not in state.creatures:
        return ActionResult(False, "Нужны две разные существующие особи.")
    parent_a = state.creatures.pop(parent_a_id)
    parent_b = state.creatures.pop(parent_b_id)
    genotype, mutated = _cross_genotype(state, parent_a["genotype"], parent_b["genotype"])
    child_id = spawn_creature(state, genotype, identified=False)
    name = RESOURCE_NAMES.get(state.creatures[child_id]["species_id"], "?")
    if mutated:
        return ActionResult(True, f"Мутация! Скрещивание дало новый вид: «{name}».")
    return ActionResult(True, f"Скрещивание удалось: детёныш вида «{name}» (без мутации).")


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
            allele = trait_alleles("environment").get(active_env, {})
            resource = allele.get("bonus_resource")
            if resource:
                entry["bonus_produce"] = {"resource_id": resource, "chance": allele.get("bonus_chance", 0)}
    entry["genotype"] = {
        trait_id: [
            {
                "id": aid,
                "name": trait_alleles(trait_id).get(aid, {}).get("name", aid),
                "dominant": trait_alleles(trait_id).get(aid, {}).get("dominant", False),
            }
            for aid in _genotype_pair(creature["genotype"], trait_id)
        ]
        for trait_id in GENOTYPE_TRAIT_IDS
    }
    return entry


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


def known_alleles_view(state: GameState) -> dict[str, list[dict]]:
    return {
        trait_id: [
            {"id": aid, "name": trait_alleles(trait_id).get(aid, {}).get("name", aid)}
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
    некуда, для них рецептов не существует."""
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
        catalog.append({
            "id": species_id,
            "description": SPECIES[species_id].get("description", ""),
            "breedable": species_id in bred_species_ids,
            "recipes": recipes,
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


def toggle_autocraft(state: GameState) -> ActionResult:
    """Галочка "Автокрафт" в UI (видна игроку, только пока compute_capacity(state) > 0 -
    см. tools/web_ui.py:build_view). Не расходует ход - как и toggle_pin, это чистая
    настройка, а не игровое действие."""
    state.autocraft_enabled = not state.autocraft_enabled
    if state.autocraft_enabled:
        return ActionResult(True, "Автокрафт включён.")
    return ActionResult(True, "Автокрафт выключен - рецепты теперь только руками.")


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
    (_PENDING_RETRY["manual"]) - каждая попытка независима."""
    action = MANUAL_ACTIONS[action_id]
    if not is_manual_action_unlocked(action_id, state):
        return ActionResult(False, "Это действие ещё не открыто.")
    # manual_action_duration уже учитывает купленный апгрейд типа
    # action_duration_reduction (см. CLAUDE.md) - настолько сильное сокращение,
    # что итог <= 1, делает действие фактически мгновенным (ветка ниже).
    duration = manual_action_duration(action_id, state)
    if duration > 1 and action_id in state.manual_jobs:
        return ActionResult(False, f"«{action['name']}» уже выполняется.")
    resolved_scale = None
    building_id = action.get("requires_building")
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
    )
    if deferred is not None:
        return deferred
    if duration > 1:
        state.manual_jobs[action_id] = {
            "turns_total": duration, "turns_done": 0,
            "gain": gain, "gain_chance": action.get("gain_chance"),
            "spawn_species": action.get("spawn_species"),
            "cost": cost,  # для возврата при досрочной отмене, см. cancel_manual_job
        }
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
        _ensure_farm_instances(state)
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
        _ensure_farm_instances(state)
        return ActionResult(True, f"Построено: {building['name']}.")
    state.construction_progress[building_id] = stage["index"] + 1
    # Эта ветка (прогресс продвинулся, но постройка ещё не готова) достижима, только
    # когда number < total, а значит total > 1 и stage_suffix точно не пуст.
    return ActionResult(True, f"Стадия {stage['number']} из {stage['total']} постройки «{building['name']}» готова: {stage['description']}.")


def research(state: GameState, tech_id: str) -> ActionResult:
    if tech_id in state.techs:
        return ActionResult(False, "Уже изучено.")
    if not is_tech_available(tech_id, state):
        return ActionResult(False, "Не выполнены условия для исследования.")
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
    state.techs.add(tech_id)
    return ActionResult(True, f"Изучено: {tech['name']}.")


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
    if _is_farm_building(building_id):
        _ensure_farm_instances(state)
        fixed = 0
        for instance in state.farm_instances.values():
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
            1 for inst in state.farm_instances.values()
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
        building_id = action.get("requires_building")
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
        if action_id not in TOOLS or not tool_is_broken(state, action_id):
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
    if action_type == "storage_upgrade":
        next_info = next_storage_tier_info(state, action_id)
        return dict(next_info["cost"]) if next_info else None
    if action_type == "start_energy_recipe":
        # Только материальная цена (consumes), если она у рецепта есть - энергия
        # (threshold) списывается ПОСТЕПЕННО по ходам из общего пула
        # (_process_energy_jobs), а не мгновенно при запуске, поэтому её здесь нет.
        recipe = ENERGY_RECIPES.get(action_id)
        return dict(recipe.get("consumes", {})) if recipe else None
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
    геном ЭТОЙ особи (phenotype["environment_id"]), с вероятностью bonus_chance
    аллели среды начисляется bonus_amount её bonus_resource - независимо от
    обычной продукции вида (свой бросок, не участвует в output_multiplier).

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
                allele = trait_alleles("environment").get(active_env, {})
                resource = allele.get("bonus_resource")
                if resource and random.random() < allele.get("bonus_chance", 0):
                    amount = allele.get("bonus_amount", 0)
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
    купленный апгрейд типа soul_generation_efficiency ЭТОЙ постройки (тот же
    принцип, что breeding_output у ферм - см. _farm_upgrade_multiplier),
    округляется по правилам ресурса (см. _round_amounts). produces может быть
    диапазоном (см. _is_range_amount/_roll_range_amounts) - бросается отдельно
    для КАЖДОЙ вставленной души, независимо от остальных."""
    for building_id, installed in state.soul_slots.items():
        if not installed:
            continue
        if not is_building_active_now(building_id, daytime):
            continue
        capacity = soul_collector_capacity(state, building_id)
        efficiency = _farm_upgrade_multiplier(state, building_id, "soul_generation_efficiency", default=1.0)
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

    available = state.resources.get(ENERGY_RESOURCE_ID, 0)
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
                _gain(state.resources, _roll_range_amounts(recipe.get("produces", {})))
                report.crafted[job["recipe_id"]] = report.crafted.get(job["recipe_id"], 0) + 1
            else:
                remaining_jobs.append(job)
    state.resources[ENERGY_RESOURCE_ID] = available
    state.energy_jobs = remaining_jobs


def _process_autocraft_jobs(state: GameState, report: ProductionReport) -> None:
    """Каждый ход фоновые автокрафт-задачи (state.autocraft_jobs) вместе выполняют до
    autocraft_speed_capacity(state) прокруток - общий бюджет на ВСЕ задачи разом, как и с
    вычислительной мощностью. Порядок задач в словаре - порядок обработки: задачи глубже
    в цепочке заводятся раньше (см. _ensure_available), поэтому обрабатываются первыми и
    успевают подвезти сырьё "вышестоящим" задачам в этот же ход. Прокрутка ограничена ещё
    и реально доступным сейчас сырьём - если его не хватает, задача просто простаивает
    этот ход (как обычная постройка без ресурсов), а не падает с ошибкой."""
    budget = autocraft_speed_capacity(state)
    for resource_id, job in list(state.autocraft_jobs.items()):
        if budget <= 0:
            break
        # Эффективные cost/gain (см. effective_manual_action_stats) - ТЕ ЖЕ числа,
        # что использовались при заведении задачи в _ensure_available, а не сырой
        # MANUAL_ACTIONS[...] - иначе улучшение постройки, купленное ПОСЛЕ того, как
        # задача уже встала в очередь, задним числом поменяло бы её бухгалтерию.
        cost, gain = effective_manual_action_stats(job["action_id"], state)
        take = min(budget, job["fires_total"] - job["fires_done"])
        for ing_id, amount in cost.items():
            if amount <= 0:
                continue
            take = min(take, state.resources.get(ing_id, 0) // amount)
        take = max(0, take)
        if take <= 0:
            continue
        for ing_id, amount in cost.items():
            state.resources[ing_id] -= amount * take
        for out_id, amount in gain.items():
            _add_resource(state.resources, out_id, amount * take)
        job["fires_done"] += take
        budget -= take
        if job["fires_done"] >= job["fires_total"]:
            del state.autocraft_jobs[resource_id]
            report.autocraft_completed.append(resource_id)


def _process_manual_jobs(state: GameState, report: ProductionReport) -> None:
    """Каждый ход продвигает многоходовые ручные действия (state.manual_jobs,
    manual_actions.*.duration_turns) на один тик - в отличие от _process_autocraft_jobs,
    тиканье не ограничено никаким бюджетом/сырьём (cost уже списан целиком при старте,
    см. do_manual_action), поэтому просто +1 turns_done каждому job'у безусловно. Когда
    turns_done доходит до turns_total - gain_chance/диапазон разворачиваются РОВНО ОДИН
    РАЗ прямо здесь (тот же момент, что produces у _process_energy_jobs), job удаляется."""
    for action_id, job in list(state.manual_jobs.items()):
        job["turns_done"] += 1
        if job["turns_done"] < job["turns_total"]:
            continue
        gain = job["gain"]
        if job["gain_chance"]:
            gain = _roll_chance_amounts(gain, job["gain_chance"])
        gain = _roll_range_amounts(gain)
        _gain(state.resources, gain)
        if job["spawn_species"]:
            spawn_creature(state, _roll_wild_genotype(job["spawn_species"]), identified=False)
        del state.manual_jobs[action_id]
        report.manual_completed.append(action_id)


def manual_jobs_view(state: GameState) -> list[dict]:
    """Список активных многоходовых ручных задач для UI - какое действие, сколько ходов
    прошло/нужно всего (см. autocraft_jobs_view - тот же принцип, только без ETA: тиканье
    здесь не зависит ни от какой пропускной способности, ровно 1 ход/тик)."""
    return [
        {"action_id": aid, "turns_done": j["turns_done"], "turns_total": j["turns_total"]}
        for aid, j in state.manual_jobs.items()
    ]


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
    _gain(state.resources, job["cost"])
    return ActionResult(True, f"«{MANUAL_ACTIONS[action_id]['name']}» отменено, затраты возвращены.")


# Каждый retry принимает ВЕСЬ pending-словарь (не только target_id) - так у него
# есть доступ к retry_extra-полям, запомненным _enqueue_pending (например, "scale"
# у ручного действия, см. do_manual_action) - без этого повторная попытка молча
# откатилась бы на параметры по умолчанию, теряя исходный выбор игрока.
_PENDING_RETRY = {
    "manual": lambda state, pending: do_manual_action(state, pending["target_id"], pending.get("scale")),
    "build": lambda state, pending: build(state, pending["target_id"]),
    "tech": lambda state, pending: research(state, pending["target_id"]),
    "repair": lambda state, pending: repair(state, pending["target_id"]),
    "upgrade_tool": lambda state, pending: upgrade_tool(state, pending["target_id"]),
    "building_upgrade": lambda state, pending: purchase_building_upgrade(state, *pending["target_id"].split(":", 1)),
    "farm_instance_upgrade": lambda state, pending: purchase_farm_instance_upgrade(state, *pending["target_id"].split(":", 1)),
    "storage_upgrade": lambda state, pending: upgrade_storage(state, pending["target_id"]),
    "start_energy_recipe": lambda state, pending: start_energy_recipe(state, pending["target_id"]),
    "synthesize": lambda state, pending: synthesize_creature(state, pending["chosen_alleles"]),
    "summon_boss": lambda state, pending: summon_boss(state, pending["target_id"]),
}


def _process_pending_actions(state: GameState, report: ProductionReport) -> None:
    """Пытается заново провести каждое отложенное действие (заведённое _pay_or_defer,
    пока не хватало скорости автокрафта). Если к этому ходу фоновые задачи уже подвезли
    всё нужное, действие само завершится (эффект применится, как будто игрок только что
    успешно его провёл) - иначе просто остаётся в очереди ещё на ход."""
    still_pending = []
    for pending in state.pending_actions:
        retry = _PENDING_RETRY[pending["kind"]]
        result = retry(state, pending)
        if result.ok and not result.pending:
            report.pending_finished.append(pending["label"])
        else:
            still_pending.append(pending)
    state.pending_actions = still_pending


def _process_combat(state: GameState, report: ProductionReport) -> None:
    """Тикает кулдауны приёмов боя (state.move_cooldowns, см. attack_encounter) и
    проверяет дедлайн активного босса (mobs.*.turn_limit, см. summon_boss) - вызывается
    из advance_turn ПОСЛЕ state.turn += 1, чтобы сверяться уже с НОВЫМ номером хода.
    Кулдауны тикают КАЖДЫЙ ход, а не только во время боя - тот же принцип, что и
    остальные счётчики хода в advance_turn."""
    for move_id in list(state.move_cooldowns):
        remaining = state.move_cooldowns[move_id] - 1
        if remaining <= 0:
            del state.move_cooldowns[move_id]
        else:
            state.move_cooldowns[move_id] = remaining

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

    Следом - синтезаторы душ (_process_soul_collectors, см. state.soul_slots):
    каждая вставленная душа (в пределах текущей capacity постройки) производит по
    своему soul_recipes[soul_id], независимо от обычных produces/consumes постройки.

    Третьим проходом, уже после этих двух, идут активные крафты за энергию
    (_process_energy_jobs, см. state.energy_jobs - список запущенных в энергетических
    преобразователях рецептов) - им достаётся то, что осталось после построек с
    обычным consumes.energy, чтобы "накопление про запас" не отбирало энергию у
    построек с жёсткой потребностью.

    Дальше - фоновые автокрафт-задачи (_process_autocraft_jobs, см. state.autocraft_jobs)
    и отложенные из-за них действия (_process_pending_actions, см. state.pending_actions):
    задачи продвигаются в пределах autocraft_speed_capacity, а действия, которые их
    дождались, сами завершаются в этот же ход, без участия игрока.

    В конце запас энергии обрезается до ёмкости построенных хранилищ (energy_capacity) -
    без них она равна 0, и всё, что не потратили за ход, на следующий не переносится.

    Последним, уже ПОСЛЕ state.turn += 1 - _process_combat: тикают кулдауны приёмов
    боя (независимо от того, идёт ли бой прямо сейчас) и проверяется дедлайн активного
    босса (mobs.*.turn_limit) - не уложились вовремя, босс сбегает сам (report.boss_fled)."""
    report = ProductionReport()

    _ensure_farm_instances(state)
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
        if _is_farm_building(building_id):
            # Поломка катается ПО ЭКЗЕМПЛЯРУ (не по абстрактному "working" числу) -
            # иначе не получилось бы отметить, КАКАЯ именно копия сломалась, а
            # состояние экземпляра (broken) обязано совпадать с агрегатом
            # state.broken[bid] (см. _ensure_farm_instances). Пересчёт агрегата
            # ТОЧНЫЙ (не инкремент), т.к. этот проход - единственное место, где он
            # вообще может вырасти для фермы. breakdown_immunity у ферм - ВСЕГДА
            # per-instance (см. _instance_has_upgrade_type, purchase_farm_instance_upgrade) -
            # иммунный экземпляр просто не участвует в ролле вовсе, независимо от
            # общего chance этого типа построек.
            instances = [inst for inst in state.farm_instances.values() if inst["building_id"] == building_id]
            newly_broken = 0
            for instance in instances:
                if instance["broken"] or _instance_has_upgrade_type(building_id, instance, "breakdown_immunity"):
                    continue
                if random.random() < chance:
                    instance["broken"] = True
                    instance["broken_type"] = _roll_breakdown_type(building_id)
                    newly_broken += 1
            if newly_broken:
                report.newly_broken[building_id] = newly_broken
            state.broken[building_id] = sum(1 for inst in instances if inst["broken"])
        else:
            newly_broken = sum(1 for _ in range(active) if random.random() < chance)
            if newly_broken:
                state.broken[building_id] = state.broken.get(building_id, 0) + newly_broken
                report.newly_broken[building_id] = newly_broken
                types_list = state.broken_types.setdefault(building_id, [])
                types_list.extend(_roll_breakdown_type(building_id) for _ in range(newly_broken))

    energy_consumers = []
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
            energy_consumers.append((building_id, working))
        else:
            others.append((building_id, working))

    for building_id, working in others:
        process(building_id, working)
    for building_id, working in energy_consumers:
        process(building_id, working)

    _process_habitat(state, report)
    _process_soul_collectors(state, report, daytime)
    _process_energy_jobs(state, report)
    _process_autocraft_jobs(state, report)
    _process_manual_jobs(state, report)
    process_automation_rules(state, report, daytime)
    _process_pending_actions(state, report)

    cap = energy_capacity(state)
    if state.resources.get(ENERGY_RESOURCE_ID, 0) > cap:
        state.resources[ENERGY_RESOURCE_ID] = cap

    # Вместимость склада (см. storage_capacity) - та же модель, что у энергии: сгорает
    # только ИЗЛИШЕК сверх вместимости, и только В КОНЦЕ хода (не мешает производству/
    # автокрафту в течение самого хода). Ресурсы без настроенного storage (RESOURCE_STORAGE)
    # этот цикл не трогает вовсе - они по-прежнему без ограничений, как раньше.
    for resource_id in RESOURCE_STORAGE:
        cap = storage_capacity(state, resource_id)
        if state.resources.get(resource_id, 0) > cap:
            state.resources[resource_id] = cap

    state.turn += 1
    _process_combat(state, report)
    return report


def theoretical_rates(state: GameState) -> dict[str, int]:
    """Сколько ресурса в ход даёт текущий парк построек, если всем хватает сырья (для
    отображения) - с учётом того, что постройки не в своё время суток (active_period)
    или выключенные ползунком "Механизмы" (building_active_count) в этот ход
    гарантированно простаивают, а не только "может не хватить сырья"."""
    daytime = is_daytime(state)
    rates: dict[str, int] = {}
    for building_id in state.buildings:
        working = working_count(state, building_id)
        if working <= 0 or not is_building_active_now(building_id, daytime):
            continue
        working = building_active_count(state, building_id)
        if working <= 0:
            continue
        produces, consumes = effective_building_stats(building_id, state.techs, state.building_upgrades)
        for r, amount in produces.items():
            rates[r] = rates.get(r, 0) + _amount_estimate(amount) * working
        for r, amount in consumes.items():
            rates[r] = rates.get(r, 0) - amount * working
    return rates


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
