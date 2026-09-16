"""
Веб-обёртка для игры - HTTP API (стандартная библиотека) поверх game/engine.py
+ окно pywebview вместо системного браузера, чтобы игра выглядела отдельным
приложением, а не вкладкой браузера. Сам HTTP-сервер по-прежнему поднимается
и слушает 127.0.0.1:8790 - при желании страницу всё ещё можно открыть вручную
в обычном браузере на том же адресе.

Единственная внешняя зависимость - pywebview (pip install pywebview).

Запуск:
    python tools/web_ui.py

Откроет игру в отдельном окне. Прогресс сохраняется в save.json в корне
проекта - тот же файл, что использует консольная версия (main.py), так что
партию можно продолжать в любом из двух интерфейсов.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path

import webview

# В exe-сборке (PyInstaller, --windowed) sys.stdout/sys.stderr бывают None -
# голый print() уронил бы процесс ещё до того, как он успеет что-то показать.
if getattr(sys, "frozen", False):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from game import engine  # noqa: E402
from game.content import (  # noqa: E402
    BREAKDOWN_TYPES, BUILDINGS, COMBAT_SETTINGS, DEFAULT_ACTION_DELAY_MS,
    ENCHANTMENTS, ENERGY_RECIPES, ERAS, GENETIC_TRAITS, LOCATIONS, LOOT_CASES, MANUAL_ACTIONS,
    MINE_FIELD, MINING_INFO, MOBS, QUESTS, RESOURCE_ICONS, RESOURCE_NAMES, RESOURCE_ORDER,
    RESOURCE_STORAGE, SKILLS, SOUL_RECIPES, SPECIES, TECHS, TOOLS, UI_SOUNDS,
)
from game.state import GameState  # noqa: E402

# В exe-сборке ROOT указывает внутрь распакованной папки сборки - сохранения
# должны лежать РЯДОМ С EXE (портативная папка), а не там же, где content.json/html.
if getattr(sys, "frozen", False):
    _SAVE_ROOT = Path(sys.executable).resolve().parent
else:
    _SAVE_ROOT = ROOT

SAVE_PATH = _SAVE_ROOT / "save.json"
# Именованные сохранения (папка saves/, рядом с проектом) - отдельная от постоянного
# автосохранения SAVE_PATH возможность: "Сохранить как"/"Открыть" по явному выбору
# игрока, а не то, что подхватывается при простом запуске интерфейса.
SAVES_DIR = _SAVE_ROOT / "saves"
HTML_PATH = Path(__file__).with_name("web_ui.html")
# Кастомные звуки ручных действий, загруженные через content_editor.py
# (см. manual_actions.*.sound в content.json) - живёт рядом с web_ui.py/
# content_editor.py в tools/, не под _SAVE_ROOT (это контент, а не прогресс игрока).
SOUNDS_DIR = Path(__file__).with_name("sounds")
PORT = 8790

# Держать в паре с tools/content_validation.py:_SOUND_FILENAME_RE (та же форма
# значения manual_actions.*.sound) - content_validation.py намеренно не импортируется
# отсюда, у него другая роль (валидация несохранённого черновика в редакторе), а этот
# файл использует regex, чтобы безопасно раздавать файл по имени (защита от path traversal).
_SOUND_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(wav|mp3|ogg)$")
_SOUND_CONTENT_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"}

# Автоостановка сервера, когда закрыта вкладка/браузер (нужна в exe-сборке без
# консоли - иначе процесс некому было бы остановить, кроме Диспетчера задач).
# web_ui.html шлёт heartbeat раз в 15 сек + сигнал /api/shutdown при закрытии
# вкладки (см. setupAutoShutdown); таймаут ниже - запасной путь на случай, если
# браузер рухнул и sendBeacon не долетел.
_HEARTBEAT_TIMEOUT_S = 45
_heartbeat_lock = threading.Lock()
_last_heartbeat = time.monotonic()


def _show_error(text: str) -> None:
    if getattr(sys, "frozen", False):
        ctypes.windll.user32.MessageBoxW(None, text, "Игра", 0x10)
    else:
        print(text)


def _watchdog(server: HTTPServer) -> None:
    while True:
        time.sleep(5)
        with _heartbeat_lock:
            idle = time.monotonic() - _last_heartbeat
        if idle > _HEARTBEAT_TIMEOUT_S:
            server.shutdown()
            return

# Запрещённые в именах файлов на Windows символы (и управляющие байты) - имя сохранения
# приходит прямо из тела HTTP-запроса, так что это ещё и защита от выхода за пределы
# SAVES_DIR (например, "../../что-то") через слэши/точки.
_UNSAFE_SAVE_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# building_id (тип энергетической машины) -> на сколько энергии её job'ам не
# хватило в ПОСЛЕДНИЙ обработанный ход (см. engine.ProductionReport.energy_shortfall) -
# живая подсказка для панели "Приоритеты" (энергосистема). Обновляется в run_turn
# сразу после advance_turn; НЕ часть save.json (ProductionReport и так существует
# только в пределах одного хода) - просто состояние процесса, сбрасывается при
# рестарте сервера и явно очищается при "Начать заново"/загрузке сохранения.
_LAST_ENERGY_SHORTFALL: dict[str, int | float] = {}


def load_state() -> GameState:
    if SAVE_PATH.exists():
        return GameState.load(str(SAVE_PATH))
    return GameState()


def save_state(state: GameState) -> None:
    state.save(str(SAVE_PATH))


def safe_save_name(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or name in (".", "..") or _UNSAFE_SAVE_NAME_CHARS.search(name):
        return None
    return name


def list_saves() -> list[dict]:
    """Именованные сохранения в SAVES_DIR - имя (без .json), номер хода и время
    изменения файла (для сортировки "недавние сверху"). Битые/нечитаемые файлы
    просто пропускаются, а не роняют список."""
    if not SAVES_DIR.is_dir():
        return []
    items = []
    for path in SAVES_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            turn = data.get("turn", 1)
        except (OSError, json.JSONDecodeError):
            continue
        items.append({"name": path.stem, "turn": turn, "modified": path.stat().st_mtime})
    items.sort(key=lambda item: item["modified"], reverse=True)
    return items


def _recipe_board_jump(state: GameState | None) -> dict[str, str]:
    """resource_id -> action_id для кнопки "Перейти к рецепту" (см.
    engine.RECIPE_BOARD_BUILDING_ID/recipe_board_target) - пусто, пока не работает
    ни одна копия постройки-помощника (или state вообще не передан - см.
    cost_list). Отдельная функция, а не инлайн в cost_list, чтобы одно и то же
    правило гейта не разъезжалось между cost_list и местами, которым нужна сама
    карта (top-level "resource_jump", storage_view)."""
    if state is None or engine.working_count(state, engine.RECIPE_BOARD_BUILDING_ID) <= 0:
        return {}
    return engine.recipe_board_target(state)


def cost_list(cost: dict, state: GameState | None = None) -> list[dict]:
    """id/название/количество для словаря ресурс->количество (цена постройки,
    produces/consumes и т.п.) - плюс jump_to (см. _recipe_board_jump) для кнопки
    "Перейти к рецепту", ЕСЛИ передан state. state можно не передавать там, где
    клик "перейти к рецепту" не имеет смысла (например, генетические черты
    существа в _creature_entry_view) - тогда jump_to всегда None. jump_to
    присутствует в ответе ВСЕГДА (даже когда он None) - фронтенд решает сам, в
    каком месте его вообще стоит рисовать кликабельным (см. costChip в
    web_ui.html), лишнее поле в остальных местах просто игнорируется."""
    jump = _recipe_board_jump(state)
    return [{"id": r, "name": RESOURCE_NAMES[r], "amount": a, "jump_to": jump.get(r)} for r, a in cost.items()]


def manual_action_cost_list(cost: dict, cost_chance: dict, state: GameState) -> list[dict]:
    """Как cost_list(cost, state), но добавляет "chance" (manual_actions[...]
    ["cost_chance"] этого же действия, см. "Шанс потребления/получения" в
    CLAUDE.md) - ТОЛЬКО когда он < 1 (100%), чтобы не засорять ответ для
    подавляющего большинства действий без этой механики - фронтенд рисует шанс
    рядом с чипом, только если поле есть."""
    items = cost_list(cost, state)
    for item in items:
        chance = cost_chance.get(item["id"], 1.0)
        if chance < 1.0:
            item["chance"] = chance
    return items


def manual_action_gain_list(gain: dict, gain_chance: dict) -> list[dict]:
    """Как cost_list, но для ВЫХОДА ручного действия - добавляет "chance", только
    когда он < 1 (100%), см. manual_action_cost_list."""
    items = []
    for r, a in gain.items():
        item = {"id": r, "name": RESOURCE_NAMES[r], "amount": a}
        chance = gain_chance.get(r, 1.0)
        if chance < 1.0:
            item["chance"] = chance
        items.append(item)
    return items


def action_priority_view(state: GameState) -> list[dict]:
    """Все ручные действия с непустым cost (кандидаты на автокрафт-рецепт, см.
    engine.resource_recipe) - для модалки "Приоритеты рецептов": игрок может
    переопределить приоритет по умолчанию (content.json) под своё сохранение.
    Показываются ВСЕ такие действия независимо от того, есть ли у ресурса сейчас
    конкурирующий рецепт - список маленький, а видеть будущие конфликты заранее
    удобнее, чем гадать, почему приоритет "ни на что не влияет"."""
    recipe = engine.resource_recipe(state)
    result = []
    for aid, a in MANUAL_ACTIONS.items():
        if not a["cost"]:
            continue
        default_priority = a.get("priority", 0)
        result.append({
            "id": aid, "name": a["name"],
            "gain": [{"id": r, "name": RESOURCE_NAMES[r]} for r in a["gain"]],
            "priority": engine.action_priority(state, aid),
            "default_priority": default_priority,
            "overridden": aid in state.action_priority,
            "is_active_recipe": any(recipe.get(r) == aid for r in a["gain"]),
        })
    return result


def energy_priority_view(state: GameState) -> list[dict]:
    """Все ТИПЫ построек с полем energy_converter (см. CLAUDE.md, "Энергосистема:
    несколько типов...") - для панели "Приоритеты" (вкладка "Энергосистема"):
    игрок может переопределить приоритет по умолчанию (content.json) под своё
    сохранение, как и с action_priority_view. Показываются ВСЕ такие типы из
    content.json, даже ещё не построенные - тот же принцип, что у action_priority_view
    (видеть будущие конфликты заранее удобнее, чем гадать, откуда взялся приоритет).
    "quota" - energy_converter_max_energy_per_turn(state, bid) (None - без лимита).
    "shortfall_last_turn" - живая подсказка из _LAST_ENERGY_SHORTFALL (0, если типу
    в прошлый ход хватило пула целиком, или у него ещё не было активных job'ов)."""
    result = []
    for bid, b in BUILDINGS.items():
        if not b.get("energy_converter"):
            continue
        result.append({
            "id": bid, "name": b["name"],
            "priority": engine.energy_priority(state, bid),
            "default_priority": b.get("energy_priority", 0),
            "overridden": bid in state.energy_priority,
            "built_count": state.buildings.get(bid, 0),
            "quota": engine.energy_converter_max_energy_per_turn(state, bid),
            "shortfall_last_turn": _LAST_ENERGY_SHORTFALL.get(bid, 0),
        })
    return result


def building_status(state: GameState, building_id: str) -> str:
    count = state.buildings.get(building_id, 0)
    if count <= 0:
        return "none"
    broken = state.broken.get(building_id, 0)
    if broken >= count:
        return "broken"
    if broken > 0:
        return "partial"
    return "ok"


def energy_converter_view(building_id: str) -> dict | None:
    """Метка "это энергетический преобразователь" + его max_energy_per_turn - для
    подсказки над постройкой (buildingTooltip/specialAbilitiesRow). Какой рецепт он
    сейчас ведёт - НЕ свойство постройки (см. energy_jobs_view/panel "Крафт из
    энергии" - активные крафты не привязаны к конкретной копии постройки)."""
    conv = BUILDINGS[building_id].get("energy_converter")
    if not conv:
        return None
    return {"max_energy_per_turn": conv.get("max_energy_per_turn")}


def _dependent_action_ids(building_id: str) -> list[str]:
    """Ручные действия, зависящие ОТ ЭТОЙ постройки (requires_building) - обычно
    действия постройки-"механизма" без единого produces/consumes (см. CLAUDE.md) -
    именно их cost/gain реально меняют улучшения такой постройки, а не пустые
    produces/consumes самой постройки."""
    return [aid for aid, a in MANUAL_ACTIONS.items() if a.get("requires_building") == building_id]


def _building_upgrade_snapshot(building_id: str, state: GameState, building_upgrades: dict) -> dict:
    """produces/consumes ПОСТРОЙКИ + БЫЛО/СТАЛО для ОДНОГО зависимого действия
    (example_action) при заданном (возможно гипотетическом) наборе купленных
    улучшений - общий "срез" для building_upgrades_view.

    example_action - ПЕРВОЕ (по порядку в content.json) зависимое действие, а не
    ВСЕ сразу - по прямому пожеланию: у механизма таких действий может быть
    несколько ДЕСЯТКОВ (см. "Ползунок масштаба ручного действия" в CLAUDE.md про
    сам паттерн), и рисовать полный до/после блок с числами для каждого сделало
    бы модалку нечитаемой простынёй. Один показательный пример достаточен, чтобы
    игрок понял МАСШТАБ эффекта - dependent_action_names (см. building_upgrades_view)
    отдельно перечисляет ВСЕ затронутые действия по именам (без чисел, компактно),
    так что полная картина "что вообще меняется" не теряется."""
    produces, consumes = engine.effective_building_stats(building_id, state.techs, building_upgrades)
    dependent_ids = _dependent_action_ids(building_id)
    example_action = None
    if dependent_ids:
        aid = dependent_ids[0]
        cost, gain = engine.effective_manual_action_stats(aid, state, building_upgrades)
        example_action = {"id": aid, "name": MANUAL_ACTIONS[aid]["name"], "cost": cost_list(cost), "gain": cost_list(gain)}
    return {
        "produces": cost_list(produces), "consumes": cost_list(consumes),
        "example_action": example_action,
    }


def building_upgrades_view(building_id: str, state: GameState) -> dict | None:
    """Улучшения ЭТОГО типа постройки (BUILDINGS[id]["upgrades"], см. game/engine.py:
    purchase_building_upgrade/effective_building_stats/effective_manual_action_stats) -
    для кнопки "Улучшения" в каталоге строительства. None, если у постройки вообще
    нет улучшений (кнопка не показывается). "current" - срез БЫЛО (с уже купленными
    улучшениями, без изменений) - показывается один раз, общий для всех кандидатов.
    Для каждого ещё не купленного улучшения - "preview" (СТАЛО): срез с ЭТИМ одним
    улучшением поверх уже купленных (effective_*_stats с временно расширенным
    списком, реальный state не трогается) - тот же принцип, что "Дальше: ..." в
    панели инструментов. Уже купленные улучшения тоже возвращаются (purchased=True),
    чтобы игрок видел полную картину - НО С ДВУМЯ ИСКЛЮЧЕНИЯМИ (оба по прямому
    пожеланию): более слабые уже купленные уровни ТОГО ЖЕ типа (см. ниже) и
    уровни прогрессии, до которых ещё не дошла очередь (следующий уровень не
    показывается вообще, пока не куплен предыдущий того же типа - см.
    building_upgrade_prerequisite ниже; раньше он показывался, но задизейбленным,
    с подсказкой "сначала купи предыдущее" - теперь просто не занимает место в
    списке)."""
    defs = BUILDINGS[building_id].get("upgrades", [])
    if not defs:
        return None
    purchased_ids = state.building_upgrades.get(building_id, [])
    current = _building_upgrade_snapshot(building_id, state, state.building_upgrades)
    dependent_action_names = [MANUAL_ACTIONS[aid]["name"] for aid in _dependent_action_ids(building_id)]

    # Среди КУПЛЕННЫХ улучшений одного типа в движке и так действует только
    # самое сильное (game/engine.py:_apply_building_upgrades) - показывать в
    # списке ВСЕ ранее купленные, уже перекрытые более сильным уровни того же
    # типа избыточно (по прямому пожеланию). Типов всего три (scale/
    # energy_efficiency/generation_efficiency), так что в купленном виде их
    # остаётся максимум по одному на тип. С учётом того, что покупка теперь
    # обязана идти строго по порядку (building_upgrade_prerequisite) более
    # слабый купленный уровень без более сильного купленного рядом не
    # возникает - фильтр просто отбрасывает "хвост" прогрессии, если игрок
    # уже дошёл дальше.
    # repeatable-апгрейды (см. CLAUDE.md/game/engine.py:_purchased_upgrade_value)
    # НЕ участвуют в "сильнейшее скрывает более слабое той же линейки" - у них
    # нет более слабых "версий", каждая покупка ТОГО ЖЕ id просто добавляет ещё
    # value, поэтому их не за что тут скрывать.
    strongest_purchased_value: dict[str, float] = {}
    for u in defs:
        if u["id"] in purchased_ids and not u.get("repeatable"):
            strongest_purchased_value[u["type"]] = max(strongest_purchased_value.get(u["type"], u["value"]), u["value"])

    upgrades = []
    for u in defs:
        times = purchased_ids.count(u["id"])
        purchased = times > 0
        repeatable = u.get("repeatable", False)
        if purchased and not repeatable and u["value"] < strongest_purchased_value.get(u["type"], u["value"]):
            continue
        # Следующий уровень прогрессии вообще НЕ показываем в списке, пока не
        # куплен предыдущий того же типа (game/engine.py:
        # building_upgrade_prerequisite, purchase_building_upgrade проверяет то
        # же самое на самой покупке) - по прямому пожеланию: раньше такой
        # уровень показывался задизейбленным, с подсказкой "сначала купи
        # предыдущее", но список всё равно "листался вперёд" на уровни,
        # купить которые пока нельзя никак. Это НЕ касается tech_locked ниже -
        # технологически запертый, но уже ДОСТУПНЫЙ по очереди уровень
        # по-прежнему показывается (та же логика, что у дерева технологий -
        # "видно, что есть, даже если купить пока нельзя").
        prerequisite = engine.building_upgrade_prerequisite(building_id, u["id"])
        if prerequisite is not None and not engine.is_building_upgrade_purchased(state, building_id, prerequisite["id"]):
            continue
        req_tech = u.get("requires_tech")
        tech_locked = bool(req_tech) and req_tech not in state.techs
        max_purchases = u.get("max_purchases")
        at_max = repeatable and max_purchases is not None and times >= max_purchases
        current_cost = engine.repeatable_upgrade_cost(u, times) if repeatable else u["cost"]
        can_buy_more = (not at_max) if repeatable else not purchased
        preview = None
        if can_buy_more:
            hypothetical = dict(state.building_upgrades)
            hypothetical[building_id] = [*purchased_ids, u["id"]]
            preview = _building_upgrade_snapshot(building_id, state, hypothetical)
        upgrades.append({
            "id": u["id"], "name": u["name"], "description": u.get("description", ""),
            "type": u["type"], "value": u["value"],
            "cost": cost_list(current_cost, state),
            "purchased": purchased,
            "repeatable": repeatable,
            "times_purchased": times,
            "max_purchases": max_purchases,
            "at_max": at_max,
            "tech_locked": tech_locked,
            "requires_tech_name": TECHS[req_tech]["name"] if tech_locked else None,
            "preview": preview,
        })
    return {"current": current, "dependent_action_names": dependent_action_names, "upgrades": upgrades}


def energy_jobs_view(state: GameState) -> list[dict]:
    jobs = []
    for job in engine.energy_jobs_view(state):
        building_id = job["building_id"]
        jobs.append({
            "index": job["index"],
            "recipe_id": job["recipe_id"],
            "name": job["name"],
            "progress": job["progress"],
            "threshold": job["threshold"],
            "produces": cost_list(job["produces"]),
            "building_id": building_id,
            "building_name": BUILDINGS[building_id]["name"] if building_id else None,
        })
    return jobs


def converter_view(state: GameState) -> list[dict]:
    """Рецепты крафта за энергию, которые можно выбрать в локации "Преобразователь"
    (вложена в "Механизмы") - доступные (открытые технологией, см.
    available_energy_recipes) рецепты, каждый со своей ценой (materials - consumes,
    если есть, plus порог энергии) и отметкой, есть ли сейчас свободный
    преобразователь ПОДХОДЯЩЕГО ТИПА для его запуска (available=False не скрывает
    рецепт, просто кнопка неактивна - все преобразователи этого типа заняты
    ДРУГИМИ рецептами, или подходящего типа вообще нет построенного)."""
    capacity = engine.energy_converter_capacity(state)
    busy = len(state.energy_jobs)
    items = []
    for rid in engine.available_energy_recipes(state):
        recipe = ENERGY_RECIPES[rid]
        machine = recipe.get("machine")
        if machine:
            machine_capacity = engine.energy_converter_capacity(state, machine)
            machine_busy = sum(1 for j in state.energy_jobs if j.get("building_id") == machine)
            available = machine_busy < machine_capacity
            already_running = any(j["recipe_id"] == rid and j.get("building_id") == machine for j in state.energy_jobs)
        else:
            # Без привязки к типу - как раньше: любой свободный слот ЛЮБОГО типа
            # с energy_converter подходит (см. engine.energy_converter_capacity(state)
            # без building_id - сумма слотов по всем типам разом).
            available = busy < capacity
            already_running = any(j["recipe_id"] == rid for j in state.energy_jobs)
        items.append({
            "id": rid,
            "name": recipe["name"],
            "description": recipe.get("description", ""),
            "threshold": recipe["threshold"],
            "consumes": cost_list(recipe.get("consumes", {})),
            "produces": cost_list(recipe.get("produces", {})),
            "machine": machine,
            "machine_name": BUILDINGS[machine]["name"] if machine else None,
            # свободный слот преобразователя ПОДХОДЯЩЕГО ТИПА есть - можно запустить
            # ЕЩЁ ОДИН крафт этого рецепта (даже если он и так где-то уже идёт -
            # у каждого преобразователя свой независимый прогресс, см. start_energy_recipe).
            "available": available,
            "already_running": already_running,
        })
    return items


def _creature_entry_view(entry: dict) -> dict:
    """Пересобирает engine.creature_view(...) под формат чипов (cost_list) для
    produces/consumes - остальные поля (id/species_id/name/identified/
    turns_alive/lifespan/output_multiplier/active_period/environment_id/
    farm_instance_id/genotype) отдаются как есть. produces показывается во
    фронтенде БЕЗ чисел (только иконка+название ресурса - см. CLAUDE.md), так
    что здесь остаётся сырой (не домноженный на output_multiplier/апгрейды)
    produces вида - домноженного "effective_produces" больше нет.
    bonus_produce - {"resource_id","name","chance"} бонусной продукции
    промышленной пасеки (см. engine.creature_view) с добавленным именем
    ресурса, или None, если бонус сейчас не действует. У неопознанной особи
    produces/bonus_produce (как и остальной фенотип) в entry вовсе нет -
    cost_list({}) даёт пустой список, entry.get(...) - None."""
    bonus_produce = entry.get("bonus_produce")
    if bonus_produce is not None:
        bonus_produce = {**bonus_produce, "name": RESOURCE_NAMES[bonus_produce["resource_id"]]}
    return {
        **entry,
        "produces": cost_list(entry.get("produces", {})),
        "consumes": cost_list(entry.get("consumes", {})),
        "bonus_produce": bonus_produce,
    }


def farm_instance_upgrades_view(instance_id: str, state: GameState) -> dict:
    """Как building_upgrades_view, но апгрейды читаются с ОДНОГО экземпляра
    фермы (state.farm_instances[instance_id]["upgrades"]) - см. CLAUDE.md,
    "Пасеки: индивидуальные экземпляры". Апгрейды 3 обычных типов
    (breeding_output/breeding_immortality/energy_efficiency) идут через тот же
    _building_upgrade_snapshot/building_upgrade_prerequisite, что и раньше
    (они принимают произвольный dict {building_id: upgrades}, не обязательно
    state.building_upgrades). Апгрейды типа environment_emulation - особый
    случай: показываются ВСЕ 6 сразу (без прогрессии/prerequisite - они
    взаимоисключающие, не последовательные), preview не строится (смена среды
    не меняет produces/consumes постройки) - вместо этого фиксированная
    подсказка."""
    instance = state.farm_instances[instance_id]
    building_id = instance["building_id"]
    defs = BUILDINGS[building_id].get("upgrades", [])
    purchased_ids = instance["upgrades"]
    hypothetical_current = {building_id: purchased_ids}
    current = _building_upgrade_snapshot(building_id, state, hypothetical_current)
    dependent_action_names = [MANUAL_ACTIONS[aid]["name"] for aid in _dependent_action_ids(building_id)]

    regular_defs = [u for u in defs if u["type"] != "environment_emulation"]
    strongest_purchased_value: dict[str, float] = {}
    for u in regular_defs:
        if u["id"] in purchased_ids and not u.get("repeatable"):
            strongest_purchased_value[u["type"]] = max(strongest_purchased_value.get(u["type"], u["value"]), u["value"])

    upgrades = []
    for u in regular_defs:
        times = purchased_ids.count(u["id"])
        purchased = times > 0
        repeatable = u.get("repeatable", False)
        if purchased and not repeatable and u["value"] < strongest_purchased_value.get(u["type"], u["value"]):
            continue
        prerequisite = engine.building_upgrade_prerequisite(building_id, u["id"])
        if prerequisite is not None and prerequisite["id"] not in purchased_ids:
            continue
        req_tech = u.get("requires_tech")
        tech_locked = bool(req_tech) and req_tech not in state.techs
        max_purchases = u.get("max_purchases")
        at_max = repeatable and max_purchases is not None and times >= max_purchases
        current_cost = engine.repeatable_upgrade_cost(u, times) if repeatable else u["cost"]
        can_buy_more = (not at_max) if repeatable else not purchased
        preview = None
        if can_buy_more:
            hypothetical = {building_id: [*purchased_ids, u["id"]]}
            preview = _building_upgrade_snapshot(building_id, state, hypothetical)
        upgrades.append({
            "id": u["id"], "name": u["name"], "description": u.get("description", ""),
            "type": u["type"], "value": u["value"],
            "cost": cost_list(current_cost, state),
            "purchased": purchased,
            "repeatable": repeatable,
            "times_purchased": times,
            "max_purchases": max_purchases,
            "at_max": at_max,
            "tech_locked": tech_locked,
            "requires_tech_name": TECHS[req_tech]["name"] if tech_locked else None,
            "preview": preview,
        })

    env_defs = [u for u in defs if u["type"] == "environment_emulation"]
    for u in env_defs:
        purchased = u["id"] in purchased_ids
        req_tech = u.get("requires_tech")
        tech_locked = bool(req_tech) and req_tech not in state.techs
        upgrades.append({
            "id": u["id"], "name": u["name"], "description": u.get("description", ""),
            "type": u["type"], "value": u["value"],
            "cost": cost_list(u["cost"], state),
            "purchased": purchased,
            "tech_locked": tech_locked,
            "requires_tech_name": TECHS[req_tech]["name"] if tech_locked else None,
            "preview": None,
            "replaces_current_environment": not purchased and any(
                uid in {d["id"] for d in env_defs} for uid in purchased_ids
            ),
        })
    return {"current": current, "dependent_action_names": dependent_action_names, "upgrades": upgrades}


def farms_view(state: GameState) -> list[dict]:
    """Все физические экземпляры построек-ферм (см. game/engine.py:farms_view/
    state.farm_instances) для модалки "Особи" (открывается с карточки конкретной
    built-постройки-фермы, см. web_ui.html: openFarmTypeModal) - слоты, апгрейды
    ИМЕННО ЭТОГО экземпляра (farm_instance_upgrades_view) и поселённые в него
    особи."""
    farms = []
    for farm in engine.farms_view(state):
        farms.append({
            **farm,
            "creatures": [_creature_entry_view(c) for c in farm["creatures"]],
            "upgrades": farm_instance_upgrades_view(farm["id"], state),
        })
    return farms


def creature_bank_view(state: GameState) -> list[dict]:
    return [_creature_entry_view(c) for c in engine.creatures_view(state, unhoused_only=True)]


def species_catalog_view(state: GameState) -> list[dict]:
    """Форматирует engine.species_catalog_view(state) для книги знаний -
    добавляет человекочитаемые имена (вид/тех/постройка). Родитель в строке
    рецепта кликабелен ВСЕГДА (у каждого вида есть своя страница в этой же
    книге, см. renderSpeciesBookDetail) - переход на "базовый" вид просто
    покажет там пояснение, что рецептов для него нет, ловится в дикой природе.
    Описание вида берётся уже готовым из engine (не через свой
    SPECIES[species_id]) - engine.SPECIES единственный, который тесты
    подменяют на фикстурный контент (см. tests/fixtures.py:
    patched_content/_WEB_UI_NAMES), у web_ui.py своей копии для этого нет."""
    catalog = []
    for entry in engine.species_catalog_view(state):
        species_id = entry["id"]
        recipes = [{
            "parent_a": r["parent_a"],
            "parent_a_name": RESOURCE_NAMES.get(r["parent_a"], r["parent_a"]),
            "parent_b": r["parent_b"],
            "parent_b_name": RESOURCE_NAMES.get(r["parent_b"], r["parent_b"]),
            "chance": r["chance"],
            "requires_tech_name": TECHS[r["requires_tech"]]["name"] if r["requires_tech"] else None,
            "requires_building_name": BUILDINGS[r["requires_building"]]["name"] if r["requires_building"] else None,
            "locked": r["locked"],
        } for r in entry["recipes"]]
        catalog.append({
            "id": species_id,
            "name": RESOURCE_NAMES.get(species_id, species_id),
            "description": entry["description"],
            "breedable": entry["breedable"],
            "recipes": recipes,
        })
    return catalog


def tools_view(state: GameState) -> list[dict]:
    """Статус + цена открытия (разблокировки технологией) и улучшения КАЖДОГО
    инструмента из content.json["tools"] - было раньше жёстко про одну кирку
    (VIEW.pickaxe), теперь генерично для любого числа инструментов. Строго про
    инструменты, БЕЗ добычи - поле шахты показывается отдельным блоком
    (mine_field), по прямому пожеланию не дублировать это здесь.
    next_tier_name/next_cost показываются, ПОКА ЕСТЬ следующий тир, даже если его
    ещё нельзя купить (не хватает технологии) - чтобы игрок видел, что дальше есть
    куда расти и чего это будет стоить, а не только то, что уже доступно прямо
    сейчас (null у обоих - только когда инструмент уже на максимальном тире).
    next_tech_name - название недостающей технологии (null, если для следующего
    тира технология не нужна или уже изучена). can_upgrade - можно ли нажать
    кнопку апгрейда прямо сейчас (совпадает с engine.upgradable_tools).
    max_durability/durability_remaining/is_broken/repair_cost - см. "Прочность
    инструментов ремонта" в CLAUDE.md: null у инструментов без max_durability
    на текущем тире (не изнашиваются вовсе)."""
    upgradable = set(engine.upgradable_tools(state))
    items = []
    for tool_id, tool in TOOLS.items():
        next_info = engine.next_tool_tier_info(state, tool_id)
        next_tech_id = next_info["requires_tech"] if next_info else None
        tech_missing = next_tech_id if next_tech_id and next_tech_id not in state.techs else None
        current_tier = tool["tiers"][engine.tool_tier(state, tool_id) - 1]
        max_durability = current_tier.get("max_durability")
        items.append({
            "id": tool_id,
            "name": tool["name"],
            "tier": engine.tool_tier(state, tool_id),
            "tier_name": engine.tool_tier_name(state, tool_id),
            "max_tier": len(tool["tiers"]),
            "next_tier_name": next_info["name"] if next_info else None,
            "next_cost": cost_list(next_info["cost"], state) if next_info else None,
            "next_tech_name": TECHS[tech_missing]["name"] if tech_missing else None,
            "can_upgrade": tool_id in upgradable,
            "max_durability": max_durability,
            "durability_remaining": engine.tool_durability_remaining(state, tool_id),
            "is_broken": engine.tool_is_broken(state, tool_id),
            "repair_cost": cost_list(current_tier.get("repair_cost", {}), state) if max_durability else None,
        })
    return items


def enchanting_view(state: GameState) -> dict:
    """Данные модалки "Стол зачарований" (см. game/engine.py: enchant_tool/
    enchantment_pool/enchantable_tools). tools - только линейки с enchant_kind,
    каждая со своим статусом прочности (то же, что tools_view) плюс applied
    (уже наложенные зачарования) и pool (барабан - ЧЕМ ИМЕННО рискует игрок,
    сгруппировано по зачарованию с уровнями и отметкой reached, для панели
    "Доступно" справа)."""
    applied_by_tool = state.tool_enchantments
    tools = []
    for tool_id in engine.enchantable_tools():
        tool = TOOLS[tool_id]
        current_tier = tool["tiers"][engine.tool_tier(state, tool_id) - 1]
        max_durability = current_tier.get("max_durability")
        own_applied = applied_by_tool.get(tool_id, {})
        pool_by_enchant: dict[str, dict] = {}
        for entry in engine.enchantment_pool(tool_id):
            eid = entry["enchant_id"]
            ench = ENCHANTMENTS[eid]
            group = pool_by_enchant.setdefault(eid, {
                "enchant_id": eid, "name": ench["name"], "description": ench.get("description", ""),
                "levels": [],
            })
            group["levels"].append({
                "level": entry["level"], "value": entry["value"],
                "reached": own_applied.get(eid, 0) >= entry["level"],
            })
        tools.append({
            "id": tool_id, "name": tool["name"], "kind": tool.get("enchant_kind"),
            "tier": engine.tool_tier(state, tool_id), "tier_name": engine.tool_tier_name(state, tool_id),
            "max_tier": len(tool["tiers"]),
            "max_durability": max_durability,
            "durability_remaining": engine.tool_durability_remaining(state, tool_id),
            "is_broken": engine.tool_is_broken(state, tool_id),
            "applied": [
                {"enchant_id": eid, "name": ENCHANTMENTS[eid]["name"], "level": lvl}
                for eid, lvl in own_applied.items() if eid in ENCHANTMENTS
            ],
            "pool": list(pool_by_enchant.values()),
        })
    return {
        "available": engine.enchanting_table_available(state),
        "cost": cost_list(engine.enchanting_table_cost(state), state),
        "tools": tools,
    }


def hunting_view(state: GameState) -> dict:
    """Данные "Охоты" - глобальная активная схватка (state.active_encounter, ОДНА
    на всю игру, см. CLAUDE.md) + список открытых мобов/боссов КАЖДОЙ локации
    (mobs.*.location - несколько локаций-"Охот" могут существовать одновременно,
    у каждой свой набор). combat_skill - срез engine.skills_view именно по
    COMBAT_SKILL_ID, отдельным полем для удобства фронтенда (не нужно искать по
    списку "skills"). combat_moves - ВСЕ приёмы боя со статусом (unlocked/
    cooldown_remaining, см. engine.combat_moves_view) - независимо от активной
    схватки, панель "Охота" показывает их и без цели (чтобы игрок видел, что
    вообще открыто и когда освободится)."""
    encounter = None
    if state.active_encounter:
        mob = MOBS[state.active_encounter["mob_id"]]
        deadline = state.active_encounter.get("deadline_turn")
        encounter = {
            "mob_id": state.active_encounter["mob_id"],
            "name": mob["name"],
            "location": mob.get("location"),
            "is_boss": bool(mob.get("is_boss")),
            "remaining_hp": state.active_encounter["remaining_hp"],
            "max_hp": mob["hp"],
            "turns_left": max(0, deadline - state.turn) if deadline is not None else None,
        }
    entries = []
    for mid, mob in MOBS.items():
        if not engine.is_mob_unlocked(mid, state):
            continue
        is_boss = bool(mob.get("is_boss"))
        entries.append({
            "id": mid, "name": mob["name"], "description": mob.get("description", ""),
            "location": mob.get("location"), "is_boss": is_boss,
            "hp": mob["hp"], "xp_reward": mob.get("xp_reward", 0),
            "rarity": mob.get("rarity") if not is_boss else None,
            "loot": cost_list(mob.get("loot", {})),
            "summon_cost": cost_list(mob.get("summon_cost", {}), state) if is_boss else None,
            "turn_limit": mob.get("turn_limit") if is_boss else None,
        })
    skills = engine.skills_view(state)
    combat_skill = next((e for e in skills if e["id"] == engine.COMBAT_SKILL_ID), None)
    return {
        "active_encounter": encounter, "mobs": entries, "combat_skill": combat_skill,
        "combat_moves": engine.combat_moves_view(state),
        # HP игрока/дебафф "Ранен" - см. game/engine.py:current_player_hp/
        # player_max_hp/is_wounded. wounded_turns_left - сколько ходов ЕЩЁ
        # действует дебафф (0, если не ранен - is_wounded уже сам сверяет
        # state.turn <= wounded_until_turn, тут просто разница для UI-текста).
        "player_hp": engine.current_player_hp(state),
        "player_max_hp": engine.player_max_hp(state),
        "wounded": engine.is_wounded(state),
        "wounded_turns_left": max(0, state.wounded_until_turn - state.turn) if state.wounded_until_turn is not None else 0,
        # Для подсказки при наведении на бейдж "Ранен" (см. tools/web_ui.html:
        # renderEncounterModalBody) - берём из content.json, а не хардкодим
        # процент на клиенте, чтобы текст не разошёлся с балансом при правке.
        "wounded_damage_multiplier": COMBAT_SETTINGS.get("wounded_damage_multiplier", 1.0),
    }


def soul_collector_entries_view(state: GameState) -> list[dict]:
    """Все построенные синтезаторы душ (по типу постройки - soul_collector, в
    отличие от ферм, не заводит физических экземпляров) для отдельной модалки
    "Души" (открывается кнопкой на карточке конкретной built-постройки, см.
    web_ui.html: openSoulSynthesizerModal) - по прямому пожеланию, 2026-09-13:
    вставка/выемка душ больше не происходит прямо в панели "Производственные
    мощности", тот же приём разделения, что у ферм/банка особей."""
    result = []
    for bid, b in BUILDINGS.items():
        config = b.get("soul_collector")
        if not config or state.buildings.get(bid, 0) <= 0:
            continue
        result.append({
            "building_id": bid,
            "name": b["name"],
            "capacity": engine.soul_collector_capacity(state, bid),
            "accepts_kind": engine.soul_collector_accepts_kind(bid),
            "installed": [
                {"id": rid, "name": RESOURCE_NAMES[rid], "index": i}
                for i, rid in enumerate(state.soul_slots.get(bid, []))
            ],
        })
    return result


def souls_bank_view(state: GameState) -> list[dict]:
    """Банк душ в инвентаре - тот же приём, что creature_bank_view, только
    вместо особей ресурсы-души (все ключи SOUL_RECIPES). kind - тег
    resources.*.soul_kind (см. game/engine.py:soul_kind) - модалка конкретного
    синтезатора фильтрует этот банк по своему accepts_kind, показывая только
    подходящие души (полная аналогия с creature_bank + accepts_family)."""
    return [
        {"id": e["id"], "name": RESOURCE_NAMES[e["id"]], "kind": e["kind"], "amount": e["amount"]}
        for e in engine.souls_bank_view(state)
    ]


def _automation_target_name(kind: str, target_id: str) -> str:
    names = MANUAL_ACTIONS if kind == "manual_action" else ENERGY_RECIPES
    return names.get(target_id, {}).get("name", target_id)


def automation_view(building_id: str, state: GameState) -> dict | None:
    """None у построек без automation_hub (не показываем блок вовсе), зеркало
    soul_collector_view. eligible_targets - вообще ВСЕ действия/рецепты крафта
    за энергию, которые в принципе можно поставить в правило (см.
    engine.automation_eligible_targets), НЕЗАВИСИМО от того, уже открыты они
    игроку или нет - правило можно настроить заранее (см.
    engine.process_automation_rules)."""
    if not BUILDINGS[building_id].get("automation_hub"):
        return None
    rules = state.automation_rules.get(building_id, [])
    return {
        "capacity": engine.automation_slots_capacity(state, building_id),
        "eligible_targets": engine.automation_eligible_targets(),
        # Виды (SPECIES) исключены - id вида переиспользует id ресурса только
        # ради имени/иконки, особи не копятся стопкой в state.resources (см.
        # storage_view), поэтому условие на такой id никогда не имело бы смысла.
        "condition_resources": [
            {"id": rid, "name": RESOURCE_NAMES[rid]} for rid in RESOURCE_ORDER if rid not in SPECIES
        ],
        "rules": [
            {
                "index": i,
                "kind": rule["kind"],
                "target_id": rule["target_id"],
                "target_name": _automation_target_name(rule["kind"], rule["target_id"]),
                "conditions": [
                    {**c, "resource_name": RESOURCE_NAMES.get(c["resource"], c["resource"])}
                    for c in rule["conditions"]
                ],
            }
            for i, rule in enumerate(rules)
        ],
    }


def storage_view(state: GameState) -> list[dict]:
    """Вместимость склада для панели "Склад" (было "Инвентарь") - ОДНА строка на
    КАЖДЫЙ ресурс (включая те, что без настроенной вместимости - "capacity": None
    значит "хранится без ограничений", панель просто не рисует для них дробь/
    кнопку). Энергия сюда не попадает - у неё уже есть отдельная, самостоятельная
    механика запаса/ёмкости (energy_breakdown/energy_capacity), эта панель её не
    дублирует. next_capacity_add/next_cost показываются, ПОКА ЕСТЬ следующий тир,
    даже если его нельзя купить прямо сейчас (не хватает технологии) - тот же
    принцип, что у next_tier_name/next_cost в tools_view. Виды (SPECIES) тоже
    сюда не попадают: id вида переиспользует id ресурса только ради имени/иконки
    (см. content.py: SPECIES) - сами особи не копятся стопкой в state.resources,
    они поштучно живут в state.creatures со своим геномом и показаны в модалке
    "Особи"/банке особей, а не на складе. "jump_to" - см. _recipe_board_jump:
    плитка САМОГО ресурса (не только его next_cost) кликабельна, пока работает
    RECIPE_BOARD_BUILDING_ID и у ресурса есть рецепт."""
    upgradable = set(engine.upgradable_storage(state))
    jump = _recipe_board_jump(state)
    items = []
    for resource_id in RESOURCE_ORDER:
        if resource_id == engine.ENERGY_RESOURCE_ID or resource_id in SPECIES:
            continue
        config = RESOURCE_STORAGE.get(resource_id)
        amount = state.resources.get(resource_id, 0)
        if config is None:
            items.append({
                "id": resource_id, "name": RESOURCE_NAMES[resource_id], "amount": amount,
                "capacity": None, "tier": 0, "max_tier": 0,
                "next_capacity_add": None, "next_cost": None, "next_tech_name": None, "can_upgrade": False,
                "jump_to": jump.get(resource_id),
            })
            continue
        next_info = engine.next_storage_tier_info(state, resource_id)
        next_tech_id = next_info.get("requires_tech") if next_info else None
        tech_missing = next_tech_id if next_tech_id and next_tech_id not in state.techs else None
        items.append({
            "id": resource_id, "name": RESOURCE_NAMES[resource_id], "amount": amount,
            "capacity": engine.storage_capacity(state, resource_id),
            "tier": state.storage_tier.get(resource_id, 0),
            "max_tier": len(config["tiers"]),
            "next_capacity_add": next_info["capacity_add"] if next_info else None,
            "next_cost": cost_list(next_info["cost"], state) if next_info else None,
            "next_tech_name": TECHS[tech_missing]["name"] if tech_missing else None,
            "can_upgrade": resource_id in upgradable,
            "jump_to": jump.get(resource_id),
        })
    return items


def autocraft_view(state: GameState) -> dict:
    """Данные для панели "Автокрафт": пропускная способность (скорость) и число
    слотов автокрафта, активные фоновые задачи (с именем ресурса и прогрессом) и
    действия, ждущие, пока эти задачи докрафтят нужное."""
    jobs = []
    for job in engine.autocraft_jobs_view(state):
        jobs.append({
            "resource_id": job["resource_id"],
            "resource_name": RESOURCE_NAMES[job["resource_id"]],
            "fires_done": job["fires_done"],
            "fires_total": job["fires_total"],
            "eta_turns": job["eta_turns"],
        })
    return {
        "speed_capacity": engine.autocraft_speed_capacity(state),
        "slot_capacity": engine.autocraft_slot_capacity(state),
        "slots_used": len(state.autocraft_jobs),
        "jobs": jobs,
        "pending_actions": [p["label"] for p in state.pending_actions],
    }


def active_research_view(state: GameState) -> dict | None:
    """Данные для панели "Исследование" (state.active_research, см.
    game/engine.py:research/_process_research) - резолвит имя технологии по id,
    как manual_jobs_view резолвит имя действия. None, если сейчас ничего не
    исследуется (или технология мгновенная - тир 1/без research_turns - и уже
    завершилась в самом research())."""
    job = state.active_research
    if job is None:
        return None
    return {
        "tech_id": job["tech_id"],
        "name": TECHS[job["tech_id"]]["name"],
        "turns_done": job["turns_done"],
        "turns_total": job["turns_total"],
    }


def manual_jobs_view(state: GameState) -> list[dict]:
    """Данные для панели "Долгие действия" (manual_actions.*.duration_turns > 1,
    см. game/engine.py:_process_manual_jobs) - резолвит имя действия по id, как
    autocraft_view резолвит имя ресурса."""
    return [
        {
            "action_id": job["action_id"],
            "name": MANUAL_ACTIONS[job["action_id"]]["name"],
            "turns_done": job["turns_done"],
            "turns_total": job["turns_total"],
        }
        for job in engine.manual_jobs_view(state)
    ]


def repair_blocked_view(state: GameState, building_id: str) -> list[dict]:
    """Виды поломки СЕЙЧАС сломанной постройки building_id, которые repair()
    НЕ сможет починить прямо сейчас (не хватает хотя бы одного из
    required_tools) - для подсказки на карточке постройки рядом с кнопкой
    ремонта, см. tools/web_ui.html. Группирует по виду поломки (несколько
    одновременно сломанных копий одного вида - один пункт с count)."""
    result = []
    for type_id, count in engine.repair_blocked_types(state, building_id):
        missing = [
            TOOLS[tid]["name"] for tid in engine.breakdown_type_required_tools(type_id)
            if not engine.tool_usable_for_repair(state, tid)
        ]
        result.append({
            "type_name": BREAKDOWN_TYPES[type_id]["name"] if type_id else "Поломка",
            "count": count,
            "missing_tools": missing,
        })
    return result


def _condition_progress_text(condition: dict, state: GameState) -> str:
    """Человекочитаемая строка "что сделать" + текущий прогресс для ОДНОГО
    условия квеста (game/engine.py: _condition_met) - держать типы в паре с
    ним и с tools/content_validation.py: CONDITION_FIELDS."""
    kind = condition["type"]
    amount = condition.get("amount")
    if kind == "resource_at_least":
        name = RESOURCE_NAMES.get(condition["resource"], condition["resource"])
        current = state.resources.get(condition["resource"], 0)
        return f"{name}: {current}/{amount}"
    if kind == "building_count_at_least":
        name = BUILDINGS.get(condition["building"], {}).get("name", condition["building"])
        current = state.buildings.get(condition["building"], 0)
        return f"Построить «{name}»: {current}/{amount}"
    if kind == "total_buildings_at_least":
        current = sum(state.buildings.values())
        return f"Построек всего: {current}/{amount}"
    if kind == "tech_researched":
        name = TECHS.get(condition["tech"], {}).get("name", condition["tech"])
        done = condition["tech"] in state.techs
        return f"Исследовать «{name}»" + (" (готово)" if done else "")
    if kind == "tech_count_at_least":
        return f"Исследовано технологий: {len(state.techs)}/{amount}"
    if kind == "all_techs_researched":
        done = engine.all_techs_researched(state)
        return "Исследовать все технологии" + (" (готово)" if done else "")
    if kind == "turn_at_least":
        return f"Дожить до хода {amount} (сейчас {state.turn})"
    if kind == "tool_tier_at_least":
        name = TOOLS.get(condition["tool"], {}).get("name", condition["tool"])
        current = engine.tool_tier(state, condition["tool"])
        return f"Тир «{name}»: {current}/{amount}"
    if kind == "species_discovered_at_least":
        return f"Открыто видов существ: {len(state.species_discovered)}/{amount}"
    if kind == "species_discovered":
        name = RESOURCE_NAMES.get(condition["species"], condition["species"])
        done = condition["species"] in state.species_discovered
        return f"Вывести/поймать «{name}»" + (" (готово)" if done else "")
    if kind == "manual_action_performed_at_least":
        name = MANUAL_ACTIONS.get(condition["action"], {}).get("name", condition["action"])
        current = state.action_performed_count.get(condition["action"], 0)
        return f"Выполнить «{name}»: {current}/{amount}"
    if kind == "building_upgrade_purchased":
        building_name = BUILDINGS.get(condition["building"], {}).get("name", condition["building"])
        upgrade_defs = {u["id"]: u for u in BUILDINGS.get(condition["building"], {}).get("upgrades", [])}
        upgrade_name = upgrade_defs.get(condition["upgrade"], {}).get("name", condition["upgrade"])
        done = engine.is_building_upgrade_purchased(state, condition["building"], condition["upgrade"])
        return f"Купить улучшение «{upgrade_name}» для «{building_name}»" + (" (готово)" if done else "")
    return kind


def build_view(state: GameState) -> dict:
    stock, gen, cons = engine.energy_breakdown(state)
    capacity = engine.energy_capacity(state)
    rates = engine.theoretical_rates(state)

    buildings = []
    for bid, b in BUILDINGS.items():
        count = state.buildings.get(bid, 0)
        if count <= 0:
            continue  # панель мощностей показывает только уже построенное, не каталог доступного
        produces, consumes = engine.effective_building_stats(bid, state.techs, state.building_upgrades)
        buildings.append({
            "id": bid,
            "name": b["name"],
            "description": b.get("description", ""),
            "icon_label": b.get("icon_label") or b["name"][:2].upper(),
            # Произвольные теги (buildings.*.tags, назначаются в редакторе
            # контента) - НЕ хардкод, чисто для фильтра по типу постройки в
            # панели "Производственные мощности" (см. web_ui.html: renderBuildings/
            # buildingsTagFilter), движок их вообще не читает.
            "tags": b.get("tags", []),
            "count": count,
            "max_count": b.get("max_count"),
            "broken": state.broken.get(bid, 0),
            # Ремонт теперь прямо здесь, на карточке постройки (кнопки "Ремонт" -
            # см. tools/web_ui.html), а не в отдельной вкладке "Ремонт" - см.
            # CLAUDE.md. repairable - сколько из сломанных копий можно починить
            # ПРЯМО СЕЙЧАС (может быть меньше broken, если у какого-то вида
            # поломки не хватает инструмента - см. repair_blocked).
            "repairable": engine.repairable_count(state, bid),
            "repair_cost": cost_list(engine.repair_cost_total(state, bid), state),
            "repair_blocked": repair_blocked_view(state, bid) if state.broken.get(bid, 0) else [],
            "repair_click_delay_ms": b.get("click_delay_ms"),
            "status": building_status(state, bid),
            "produces": cost_list(produces),
            "consumes": cost_list(consumes),
            # Ползунок "Механизмы" (см. engine.is_disableable_building/
            # set_building_active_count) - только у построек, которые пассивно И
            # производят, И потребляют. working = count - broken (сколько вообще
            # можно включить/выключить), active_count = сколько из них сейчас
            # реально работает (working - выключенные ползунком).
            "disableable": engine.is_disableable_building(bid),
            "working_count": engine.working_count(state, bid),
            "active_count": engine.building_active_count(state, bid),
            "compute_power": b.get("compute_power", 0),
            "autocraft_speed": b.get("autocraft_speed", 0),
            "autocraft_slots": b.get("autocraft_slots", 0),
            "unlocks_autocraft": [RESOURCE_NAMES[r] for r in b.get("unlocks_autocraft", [])],
            "energy_converter": energy_converter_view(bid),
            "automation": automation_view(bid, state),
            # Кнопка "⚙ Улучшения" теперь живёт ТОЛЬКО в панели "Производственные
            # мощности" (см. web_ui.html: renderBuildings) - каталог "Построить"
            # больше не обязан держать штучную постройку на виду после лимита
            # только ради этой кнопки (см. исключение ниже, которое из-за этого
            # снято). Для ферм (breeding_slots>0) - всегда None: их апгрейды
            # теперь на КАЖДЫЙ экземпляр отдельно (см. farm_instance_upgrades_view),
            # кнопка "⚙ Улучшения" тут для них больше не показывается - вместо неё
            # (по прямому пожеланию, 2026-09-10) на карточке этой же постройки
            # показывается кнопка "Особи" (is_farm ниже), открывающая расселение +
            # апгрейды КАЖДОГО экземпляра фермы этого типа.
            "upgrades": None if b.get("breeding_slots") else building_upgrades_view(bid, state),
            # Эти три флага НЕ хардкод id (см. CLAUDE.md) - определяются generic-
            # полями контента, как и сам upgrades=None выше. Дают фронтенду знать,
            # какую доп. кнопку показать на карточке ИМЕННО ЭТОЙ built-постройки
            # в панели "Производственные мощности" (web_ui.html: renderBuildings) -
            # "Особи" (расселение/апгрейды экземпляров фермы), "Скрестить"
            # (единственный механизм скрещивания в игре) или "Синтез" (генетический
            # шаблон + мгновенное опознание).
            "is_farm": bool(b.get("breeding_slots")),
            "is_breeding_station": bool(b.get("breeding_station")),
            "is_genetics_lab": bool(b.get("genetics_lab")),
            # Кнопка "Души" на карточке (см. web_ui.html: renderBuildings) -
            # вставка/выемка живёт в своей модалке (soul_collectors/soul_bank
            # ниже), не тут - тот же приём, что is_farm.
            "is_soul_collector": bool(b.get("soul_collector")),
            # Кнопка "Зачаровать" на карточке (см. web_ui.html: renderBuildings) -
            # открывает общую модалку "Стол зачарований" (enchanting - см.
            # enchanting_view выше), тот же приём, что is_soul_collector.
            "is_enchanting_table": bool(b.get("enchanting_table")),
        })

    manual = []
    for aid in engine.available_manual_actions(state):
        action = MANUAL_ACTIONS[aid]
        # chosen_scale=1 - cost/gain РОВНО за одну "партию" ("Ползунок масштаба
        # ручного действия", см. CLAUDE.md), а не за купленный максимум, как было
        # раньше (effective_manual_action_stats(aid, state) без chosen_scale).
        # Фронтенд сам домножает эти "единичные" числа на текущее положение
        # ползунка (1..scale_cap) - чистая арифметика, без похода на сервер за
        # каждое движение ползунка. Для действия без requires_building или без
        # купленного scale-апгрейда это ЭКВИВАЛЕНТНО прежнему поведению
        # (scale_cap == 1 всегда, домножение на 1 - no-op).
        building_id = action.get("requires_building")
        scale_cap = engine.mechanism_scale_cap(building_id, state) if building_id else 1
        cost, gain = engine.effective_manual_action_stats(aid, state, chosen_scale=1)
        running_job = state.manual_jobs.get(aid)
        # Другой многоходовой job, занявший ЭТУ ЖЕ локацию (см. do_manual_action,
        # "Многоходовое ручное действие" в CLAUDE.md) - блокирует запуск ЭТОГО
        # действия, даже если сам aid ещё ни разу не запускался. Сравнение по
        # duration (не по одному только наличию duration_turns в контенте) - те же
        # правила сокращения длительности (апгрейды), что и в do_manual_action, иначе
        # карточка могла бы разойтись с реальным ответом сервера на клик.
        blocked_by = None
        if running_job is None and engine.manual_action_duration(aid, state) > 1:
            location = action.get("location")
            for other_id, other_job in state.manual_jobs.items():
                if MANUAL_ACTIONS[other_id].get("location") == location:
                    blocked_by = MANUAL_ACTIONS[other_id]["name"]
                    break
        manual.append({
            "id": aid, "name": action["name"], "location": action.get("location"),
            "cost": manual_action_cost_list(cost, action.get("cost_chance", {}), state),
            "gain": manual_action_gain_list(gain, action.get("gain_chance", {})),
            "scale_cap": scale_cap,
            "click_delay_ms": action.get("click_delay_ms"),
            "sound_url": sound_url(action.get("sound")),
            "duration_turns": action.get("duration_turns"),
            "already_running": running_job is not None,
            "blocked_by": blocked_by,
            # Прогресс уже идущего job'а ПРЯМО на карточке действия (см. actionCard в
            # web_ui.html) - дублирует то, что уже есть в панели "Долгие действия"
            # (manual_jobs выше), т.к. панель не всегда на виду (действие может быть
            # в другой локации), а прогресс своего же действия игрок должен видеть,
            # не уходя с карточки.
            "turns_done": running_job["turns_done"] if running_job else None,
            "turns_total": running_job["turns_total"] if running_job else None,
        })
    hunting = hunting_view(state)
    hunting_location_ids = {m["location"] for m in hunting["mobs"]}
    locations = [
        {
            "id": lid, "name": LOCATIONS[lid]["name"],
            "parent_location": LOCATIONS[lid].get("parent_location"),
            "is_mine": lid == engine.MINE_LOCATION_ID,
            "is_converter": lid == engine.CONVERTER_LOCATION_ID,
            "is_hunting": lid in hunting_location_ids,
        }
        for lid in engine.available_locations(state)
    ]
    build = []
    for bid in engine.available_buildings(state):
        owned = state.buildings.get(bid, 0)
        max_count = BUILDINGS[bid].get("max_count")
        # Лимит построек достигнут - убираем карточку из каталога совсем, вместо
        # того чтобы навсегда оставлять там большую неактивную кнопку (прямое
        # пожелание, 2026-09-02) - штучную постройку всё равно видно в панели
        # "Производственные мощности", и кнопка "⚙ Улучшения" (если у построики
        # вообще есть upgrades) теперь тоже открывается оттуда, а не отсюда - так
        # что каталог можно прятать безусловно, без исключения для построек с
        # upgrades (было нужно, пока кнопка жила только здесь).
        if max_count is not None and owned >= max_count:
            continue
        produces, consumes = engine.effective_building_stats(bid, state.techs, state.building_upgrades)
        stage = engine.current_construction_stage(bid, state)
        build.append({
            "id": bid, "name": BUILDINGS[bid]["name"],
            "cost": cost_list(stage["cost"] if stage else BUILDINGS[bid]["cost"], state),
            "stage_number": stage["number"] if stage else None,
            "stage_total": stage["total"] if stage else None,
            "stage_description": stage["description"] if stage else None,
            "description": BUILDINGS[bid].get("description", ""),
            "owned": owned,
            "max_count": max_count,
            "produces": cost_list(produces), "consumes": cost_list(consumes),
            "compute_power": BUILDINGS[bid].get("compute_power", 0),
            "autocraft_speed": BUILDINGS[bid].get("autocraft_speed", 0),
            "autocraft_slots": BUILDINGS[bid].get("autocraft_slots", 0),
            "unlocks_autocraft": [RESOURCE_NAMES[r] for r in BUILDINGS[bid].get("unlocks_autocraft", [])],
            "energy_converter": energy_converter_view(bid),
            "click_delay_ms": BUILDINGS[bid].get("click_delay_ms"),
            "upgrades": None if BUILDINGS[bid].get("breeding_slots") else building_upgrades_view(bid, state),
        })
    tech = [
        {
            "id": tid, "name": TECHS[tid]["name"], "cost": cost_list(TECHS[tid]["cost"], state),
            "research_turns": engine.tech_research_turns(tid, state),
        }
        for tid in engine.available_techs(state)
    ]
    converter = converter_view(state)
    tools = tools_view(state)

    time_info = engine.current_time(state)
    time_info["hours_per_turn"] = engine.TIME["hours_per_turn"]

    lab_tier_capacity = engine.lab_tier_capacity(state)
    tech_tree = []
    for tid, t in TECHS.items():
        unlocks = engine.tech_unlocks(tid)
        if tid in state.techs:
            status = "researched"
        elif engine.is_tech_available(tid, state):
            status = "available"
        else:
            status = "locked"
        tech_tree.append({
            "id": tid,
            "name": t["name"],
            "description": t["description"],
            "cost": cost_list(t["cost"], state),
            "requires": t["requires"],
            "tier": t["tier"],
            "research_turns": engine.tech_research_turns(tid, state),
            "lab_built": t["tier"] <= lab_tier_capacity,
            "status": status,
            "unlocks": {
                "buildings": [{"id": b, "name": BUILDINGS[b]["name"]} for b in unlocks["buildings"]],
                "locations": [{"id": l, "name": LOCATIONS[l]["name"]} for l in unlocks["locations"]],
                "manual_actions": [{"id": a, "name": MANUAL_ACTIONS[a]["name"]} for a in unlocks["manual_actions"]],
                "mining": [{"id": r, "name": RESOURCE_NAMES[r]} for r in unlocks["mining"]],
                "tools": unlocks["tools"],
                "energy_recipes": [{"id": r, "name": ENERGY_RECIPES[r]["name"]} for r in unlocks["energy_recipes"]],
            },
        })

    return {
        "turn": state.turn,
        "save_name": state.save_name,
        "time": time_info,
        "resources": state.resources,
        "pinned": state.pinned,
        "resource_names": RESOURCE_NAMES,
        "resource_order": RESOURCE_ORDER,
        "energy_resource_id": engine.ENERGY_RESOURCE_ID,
        # Постройка-помощник (см. engine.RECIPE_BOARD_BUILDING_ID) - пока не работает
        # ни одна копия, панель "Приоритеты" вообще не открывается (см. renderAll в
        # web_ui.html): без неё игрок не может НИКУДА "перейти" по клику, так что сам
        # выбор приоритета рецепта ему пока негде проверить в деле. resource_jump -
        # та же карта, что кладётся в jump_to у cost_list(..., state)/storage_view,
        # отдельным полем ещё и здесь - нужна местам, которые рисуют голый id ресурса
        # без похода через cost_list (закреплённая панель наверху, см. renderResources).
        "recipe_board_active": engine.working_count(state, engine.RECIPE_BOARD_BUILDING_ID) > 0,
        "resource_jump": _recipe_board_jump(state),
        "default_action_delay_ms": DEFAULT_ACTION_DELAY_MS,
        # Глобальные звуки (см. game/content.py:UI_SOUNDS) - null, если автор
        # контента не загрузил файл для этого слота (клиент тогда использует свой
        # встроенный синтезированный звук по умолчанию, кроме background_music).
        "ui_sounds": {
            "background_music": sound_url(UI_SOUNDS.get("background_music")),
            "lootbox_open": sound_url(UI_SOUNDS.get("lootbox_open")),
            "button_click": sound_url(UI_SOUNDS.get("button_click")),
        },
        "rates": rates,
        "energy": {"stock": stock, "capacity": capacity, "generation": gen, "consumption": cons},
        "compute_capacity": engine.compute_capacity(state),
        "autocraft_enabled": state.autocraft_enabled,
        "autocraft": autocraft_view(state),
        "action_priorities": action_priority_view(state),
        "energy_priorities": energy_priority_view(state),
        "energy_jobs": energy_jobs_view(state),
        "manual_jobs": manual_jobs_view(state),
        "active_research": active_research_view(state),
        "energy_converter_capacity": engine.energy_converter_capacity(state),
        "farms": farms_view(state),
        "creature_bank": creature_bank_view(state),
        "soul_collectors": soul_collector_entries_view(state),
        "soul_bank": souls_bank_view(state),
        "known_alleles": engine.known_alleles_view(state),
        "genetics_lab_available": engine.genetics_lab_available(state),
        "breeding_station_available": engine.breeding_station_available(state),
        "species_catalog": species_catalog_view(state),
        "genetic_trait_names": {"species": "Вид", **{tid: t.get("name", tid) for tid, t in GENETIC_TRAITS.items()}},
        "genetics_cost": {
            "synthesize": cost_list(engine.GENETICS_SETTINGS.get("synthesis_cost", {}), state),
        },
        "buildings": buildings,
        "locations": locations,
        "tools": tools,
        "enchanting": enchanting_view(state),
        "hunting": hunting,
        "skills": engine.skills_view(state),
        "storage": storage_view(state),
        "techs_researched": len(state.techs),
        "techs_total": len(TECHS),
        "categories": {
            "manual": manual, "build": build, "tech": tech,
            "converter": converter,
        },
        "mine_field": engine.mine_field_view(state),
        "tech_tree": tech_tree,
        "icons": {
            "resources": RESOURCE_ICONS,
            "buildings": {bid: b["icon"] for bid, b in BUILDINGS.items() if b.get("icon")},
            "tools": {
                tool_id: {i + 1: t["icon"] for i, t in enumerate(tool["tiers"]) if t.get("icon")}
                for tool_id, tool in TOOLS.items()
            },
            "mobs": {mid: m["icon"] for mid, m in MOBS.items() if m.get("icon")},
            # Иконка ЖИЛЫ на поле шахты - НЕЗАВИСИМА от icons.resources того же id
            # (добытый ресурс может выглядеть иначе, чем сама жила в забое), см.
            # mining_info.*.icon. Отсутствует по умолчанию - клиент сам откатывается
            # на resourceIcon(rid), см. web_ui.html: veinIcon().
            "mining_veins": {rid: info["icon"] for rid, info in MINING_INFO.items() if info.get("icon")},
            "enchantments": {eid: e["icon"] for eid, e in ENCHANTMENTS.items() if e.get("icon")},
        },
        "eras": ERAS,
        "quests": [
            {
                "id": qid,
                "name": q["name"],
                "description": q["description"],
                "era": q["era"],
                "icon": q.get("icon"),
                "bg": q.get("bg"),
                "border": q.get("border"),
                "tooltip_color": q.get("tooltip_color"),
                "requires": q.get("requires", []),
                "conditions": [_condition_progress_text(c, state) for c in q.get("conditions", [])],
                # Тот же принцип status, что у tech_tree ниже (researched/available/
                # locked) - только 4 состояния вместо 3, потому что у квеста есть
                # ещё "награда не забрана" ПОСЛЕ выполнения (claimed/completed), а у
                # исследования технологии отдельного шага "забрать" нет.
                "status": (
                    "claimed" if qid in state.claimed_quests
                    else "completed" if qid in state.completed_quests
                    else "available" if engine.is_quest_available(qid, state)
                    else "locked"
                ),
                "reward_case": {
                    "id": q["reward_case"],
                    "name": LOOT_CASES[q["reward_case"]]["name"],
                    "description": LOOT_CASES[q["reward_case"]]["description"],
                    "prizes": LOOT_CASES[q["reward_case"]].get("prizes", []),
                },
            }
            for qid, q in QUESTS.items()
        ],
    }


def sound_url(filename: str | None) -> str | None:
    return f"/sounds/{filename}" if filename else None


def resource_deltas(before: dict, after: dict) -> list[dict]:
    out = []
    for r in RESOURCE_ORDER:
        d = after.get(r, 0) - before.get(r, 0)
        if d != 0:
            out.append({"id": r, "name": RESOURCE_NAMES[r], "amount": d})
    return out


def dispatch_action(state: GameState, action_type: str, action_id, scale: int | None = None):
    if action_type == "manual":
        return engine.do_manual_action(state, action_id, scale=scale)
    if action_type == "build":
        return engine.build(state, action_id)
    if action_type == "tech":
        return engine.research(state, action_id)
    if action_type == "repair":
        return engine.repair(state, action_id)
    if action_type == "repair_tool":
        return engine.repair_tool(state, action_id)
    if action_type == "hunt_search":
        # action_id - id локации-"Охоты" (mobs.*.location) - несколько таких
        # локаций могут существовать одновременно, в отличие от единственной шахты.
        return engine.search_mob_any(state, action_id)
    if action_type == "hunt_attack":
        # action_id - id приёма боя (content.json["combat_moves"]) или None для
        # обычной атаки без приёма (см. game/engine.py:attack_encounter/unlocked_attack_moves).
        return engine.attack_encounter(state, move_id=action_id)
    if action_type == "summon_boss":
        return engine.summon_boss(state, action_id)
    if action_type == "upgrade_tool":
        return engine.upgrade_tool(state, action_id)
    if action_type == "storage_upgrade":
        return engine.upgrade_storage(state, action_id)
    if action_type == "start_energy_recipe":
        return engine.start_energy_recipe(state, action_id)
    if action_type == "breed":
        # action_id - составной "parent_a_id:parent_b_id" (тот же приём, что
        # "building_id:upgrade_id" у building_upgrade ниже) - никакой фермы тут
        # больше нет, скрещивание не привязано к конкретной постройке (см.
        # game/engine.py:breed_creatures/breeding_station_available).
        parent_a_id, parent_b_id = action_id.split(":", 1)
        return engine.breed_creatures(state, parent_a_id, parent_b_id)
    if action_type == "synthesize":
        # action_id - составной "trait=allele:trait=allele:..." (по одной паре
        # на каждый из 4 признаков, см. game/engine.py:GENOTYPE_TRAIT_IDS).
        chosen_alleles = dict(pair.split("=", 1) for pair in action_id.split(":"))
        return engine.synthesize_creature(state, chosen_alleles)
    if action_type == "building_upgrade":
        # action_id - составной "building_id:upgrade_id" (тот же формат, что
        # target_id у отложенных действий, см. game/engine.py:_PENDING_RETRY) -
        # у одной постройки может быть несколько улучшений, одного id действия
        # недостаточно, чтобы адресовать конкретное.
        building_id, upgrade_id = action_id.split(":", 1)
        return engine.purchase_building_upgrade(state, building_id, upgrade_id)
    raise ValueError(f"неизвестный тип действия: {action_type}")


def run_turn(state: GameState, action_type: str, action_id, scale: int | None = None) -> dict:
    global _LAST_ENERGY_SHORTFALL
    log = {
        "message": None, "ok": True, "action_deltas": [], "production_deltas": [],
        "idle": [], "broken": [], "quests_completed": [], "autocraft_completed": [],
        "pending_finished": [], "energy_crafted": [], "sound_url": None,
        "manual_completed": [], "combat": None, "boss_fled": None,
        "automation_fired": [],
    }

    if action_type != "skip":
        before = dict(state.resources)
        result = dispatch_action(state, action_type, action_id, scale=scale)
        log["message"] = result.message
        log["ok"] = result.ok
        if action_type == "manual":
            # Резолвится сервером (а не клиентом по своей копии VIEW), чтобы
            # источником истины оставался content.json - см. manual_actions.*.sound.
            log["sound_url"] = sound_url(MANUAL_ACTIONS.get(action_id, {}).get("sound"))
        if action_type == "hunt_attack":
            # Данные удара (урон/killed/instant_kill) для анимации на клиенте -
            # см. game/engine.py:ActionResult.combat/attack_encounter.
            log["combat"] = result.combat
        if not result.ok:
            return log
        log["action_deltas"] = resource_deltas(before, state.resources)

    before_turn = dict(state.resources)
    report = engine.advance_turn(state)
    _LAST_ENERGY_SHORTFALL = dict(report.energy_shortfall)
    log["boss_fled"] = report.boss_fled
    log["production_deltas"] = resource_deltas(before_turn, state.resources)
    log["idle"] = [BUILDINGS[b]["name"] for b, (ran, idle) in report.ran.items() if idle > 0]
    log["broken"] = [{"name": BUILDINGS[b]["name"], "count": n} for b, n in report.newly_broken.items()]
    log["autocraft_completed"] = [RESOURCE_NAMES[r] for r in report.autocraft_completed]
    log["manual_completed"] = [MANUAL_ACTIONS[a]["name"] for a in report.manual_completed]
    log["pending_finished"] = list(report.pending_finished)
    log["energy_crafted"] = [ENERGY_RECIPES[rid]["name"] for rid, count in report.crafted.items() for _ in range(count)]
    log["automation_fired"] = [
        {"building_name": BUILDINGS[e["building_id"]]["name"], "message": e["message"]}
        for e in report.automation_fired
    ]

    newly_completed_quests = engine.check_quests(state)
    log["quests_completed"] = [
        {"id": qid, "name": QUESTS[qid]["name"], "description": QUESTS[qid]["description"]}
        for qid in newly_completed_quests
    ]

    engine.record_history_entry(
        state,
        None if action_type == "skip" else action_type,
        None if action_type == "skip" else action_id,
        log["message"],
        report,
        newly_completed_quests,
    )

    save_state(state)
    return log


def build_stats_view(state: GameState, start_turn: int, end_turn: int) -> dict | None:
    stats = engine.compute_stats(state, start_turn, end_turn)
    if stats is None:
        return None
    return {
        **stats,
        "resource_gained": [{"id": r, "name": RESOURCE_NAMES[r], "amount": a} for r, a in stats["resource_gained"].items()],
        "resource_spent": [{"id": r, "name": RESOURCE_NAMES[r], "amount": a} for r, a in stats["resource_spent"].items()],
        "resource_net": [{"id": r, "name": RESOURCE_NAMES[r], "amount": a} for r, a in stats["resource_net"].items()],
        "techs_researched": [{"id": t, "name": TECHS[t]["name"]} for t in stats["techs_researched"]],
        "buildings_built": [{"id": b, "name": BUILDINGS[b]["name"], "amount": n} for b, n in stats["buildings_built"].items()],
        "quests_completed": [{"id": q, "name": QUESTS[q]["name"]} for q in stats["quests_completed"]],
    }


def _preview_node_view(node: dict) -> dict:
    return {
        "resource_id": node["resource_id"],
        "name": RESOURCE_NAMES[node["resource_id"]],
        "needed": node["needed"],
        "stock_before": node["stock_before"],
        "taken": node["taken"],
        "action_id": node["action_id"],
        "action_name": MANUAL_ACTIONS[node["action_id"]]["name"] if node["action_id"] else None,
        "fires": node["fires"],
        "turns": node["turns"],
        "children": [_preview_node_view(c) for c in node["children"]],
    }


def _collect_preview_totals(nodes: list[dict], totals: dict | None = None) -> dict:
    """Суммирует taken/needed/fires по resource_id ПО ВСЕМУ дереву предпросмотра
    (один и тот же ресурс может встретиться в нескольких ветках, например если
    два разных промежуточных продукта оба делаются из дерева) - для компактной
    сводки в окне подтверждения, в отличие от самого дерева, где каждое
    вхождение - отдельный узел со своим местом в цепочке."""
    if totals is None:
        totals = {}
    for node in nodes:
        rid = node["resource_id"]
        entry = totals.setdefault(rid, {"taken": 0, "needed": 0, "fires": 0})
        entry["taken"] += node["taken"]
        entry["needed"] += node["needed"]
        entry["fires"] += node["fires"]
        _collect_preview_totals(node["children"], totals)
    return totals


def _action_label(state: GameState, action_type: str, action_id: str) -> str | None:
    """Человекочитаемое имя действия по (type, id) - только для заголовка окна
    подтверждения автокрафта (см. autocraft_preview_view), чтобы игрок видел, К
    КАКОМУ клику относится модалка, а не только "Подтверждение автокрафта"
    без контекста."""
    if action_type == "manual":
        return MANUAL_ACTIONS[action_id]["name"]
    if action_type in ("build", "repair"):
        return BUILDINGS[action_id]["name"]
    if action_type == "tech":
        return TECHS[action_id]["name"]
    if action_type in ("upgrade_tool", "repair_tool"):
        return TOOLS[action_id]["name"]
    if action_type == "storage_upgrade":
        return f"расширение склада «{RESOURCE_NAMES[action_id]}»"
    if action_type == "start_energy_recipe":
        return ENERGY_RECIPES[action_id]["name"] if action_id in ENERGY_RECIPES else None
    if action_type == "building_upgrade":
        building_id, _, upgrade_id = action_id.partition(":")
        upgrade = next((u for u in BUILDINGS[building_id].get("upgrades", []) if u["id"] == upgrade_id), None)
        return upgrade["name"] if upgrade else None
    if action_type == "farm_instance_upgrade":
        instance_id, _, upgrade_id = action_id.partition(":")
        instance = state.farm_instances.get(instance_id)
        if instance is None:
            return None
        upgrade = next((u for u in BUILDINGS[instance["building_id"]].get("upgrades", []) if u["id"] == upgrade_id), None)
        return upgrade["name"] if upgrade else None
    if action_type == "summon_boss":
        return MOBS[action_id]["name"] if action_id in MOBS else None
    if action_type == "breed":
        return "скрещивание особей"
    if action_type == "synthesize":
        return "синтез особи"
    return None


def autocraft_preview_view(state: GameState, action_type: str, action_id, scale: int | None = None) -> dict:
    """Предпросмотр расхода ресурсов перед запуском действия - для окна
    подтверждения автокрафта (см. CLAUDE.md, "Подтверждение автокрафта").
    {"needs_confirm": False} - подтверждение не нужно (см. engine.autocraft_preview:
    либо весь cost уже покрыт запасом, либо автокрафт сейчас не при делах) - в
    этом случае фронтенд должен выполнить действие сразу, без модалки.

    "summary" - ОДНА строка на resource_id (агрегат по всему дереву, см.
    _collect_preview_totals) для компактного списка в самом окне подтверждения -
    "percent" (0..1) показывает, какую долю ТЕКУЩЕГО запаса этого ресурса
    целиком съедает вся операция (None, если своего запаса нет вообще - взять
    нечего, делить 0/0 бессмысленно, UI рисует для такой строки нейтральную
    метку вместо цветной шкалы). "tree" - полное дерево (по узлу на КАЖДОЕ
    вхождение ресурса в цепочке, не агрегировано) для кнопки "показать дерево
    рецепта". "turns" (и в summary, и в узлах tree) - оценка engine.autocraft_eta_turns:
    None, если этот докрафт уложится в один ход, иначе через сколько ходов он
    завершится при ТЕКУЩЕЙ скорости автокрафта - чтобы игрок видел ДО подтверждения,
    что операция растянется на несколько ходов, а не мгновенна."""
    cost = engine.resolve_action_cost(state, action_type, action_id, scale)
    preview = engine.autocraft_preview(state, cost) if cost else None
    if preview is None:
        return {"needs_confirm": False}
    totals = _collect_preview_totals(preview["nodes"])
    summary = []
    for rid, t in totals.items():
        stock = state.resources.get(rid, 0)
        summary.append({
            "resource_id": rid, "name": RESOURCE_NAMES[rid],
            "needed": t["needed"], "taken": t["taken"], "stock": stock, "fires": t["fires"],
            "percent": (t["taken"] / stock) if stock > 0 else (1.0 if t["taken"] > 0 else None),
            "turns": engine.autocraft_eta_turns(state, t["fires"]),
        })
    return {
        "needs_confirm": True,
        "label": _action_label(state, action_type, action_id),
        "summary": summary,
        "tree": [_preview_node_view(n) for n in preview["nodes"]],
    }


STATE = load_state()


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = HTML_PATH.read_text(encoding="utf-8").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self._json(200, build_view(STATE))
        elif self.path == "/api/saves":
            self._json(200, {"saves": list_saves()})
        elif self.path.startswith("/sounds/"):
            self._serve_sound(self.path[len("/sounds/"):])
        else:
            self._json(404, {"error": "not found"})

    def _serve_sound(self, filename: str) -> None:
        # Санитизация именем-паттерном (не просто "нет .."), чтобы отдавать ТОЛЬКО
        # то, что вообще могло быть загружено через content_editor.py - см. _SOUND_FILENAME_RE.
        if not _SOUND_FILENAME_RE.match(filename):
            self._json(404, {"error": "not found"})
            return
        path = SOUNDS_DIR / filename
        if not path.is_file():
            self._json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _SOUND_CONTENT_TYPES[path.suffix.lower()])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        global STATE, _LAST_ENERGY_SHORTFALL
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return

        if self.path == "/api/action":
            try:
                # scale - "Ползунок масштаба ручного действия" (см. CLAUDE.md) -
                # опционально, только action_type == "manual" его использует
                # (dispatch_action/do_manual_action игнорируют его для остальных
                # типов). int(...) намеренно ВНУТРИ этого try - нечисловое значение
                # от клиента должно давать те же 400, что и остальные bad-payload случаи.
                raw_scale = payload.get("scale")
                scale = int(raw_scale) if raw_scale is not None else None
                log = run_turn(STATE, payload.get("type"), payload.get("id"), scale=scale)
            except (KeyError, ValueError) as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"log": log, "state": build_view(STATE)})
        elif self.path == "/api/autocraft_preview":
            # ЧИСТО read-only (не трогает STATE/save.json, см. engine.autocraft_preview) -
            # фронтенд зовёт это ПЕРЕД реальным /api/action, чтобы решить, показывать
            # ли окно подтверждения (см. CLAUDE.md, "Подтверждение автокрафта").
            try:
                raw_scale = payload.get("scale")
                scale = int(raw_scale) if raw_scale is not None else None
                preview = autocraft_preview_view(STATE, payload.get("type"), payload.get("id"), scale)
            except (KeyError, ValueError) as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, preview)
        elif self.path == "/api/pin":
            result = engine.toggle_pin(STATE, payload.get("resource_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/autocraft_toggle":
            result = engine.toggle_autocraft(STATE)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/set_action_priority":
            # Как toggle_pin/toggle_autocraft - настройка конкретного сохранения,
            # не тратит ход. priority: null сбрасывает переопределение игрока к
            # значению по умолчанию из content.json (manual_actions.*.priority).
            priority = payload.get("priority")
            result = engine.set_action_priority(
                STATE, payload.get("action_id"), int(priority) if priority is not None else None,
            )
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/set_energy_priority":
            # Зеркало /api/set_action_priority, только для приоритета ТИПА
            # энергетической машины при нехватке общего пула энергии на все активные
            # крафты сразу (см. engine.set_energy_priority/_process_energy_jobs).
            priority = payload.get("priority")
            result = engine.set_energy_priority(
                STATE, payload.get("building_id"), int(priority) if priority is not None else None,
            )
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/set_building_active_count":
            # Ползунок меню "Механизмы" (панель "Производственные мощности") -
            # сколько из РАБОТАЮЩИХ копий постройки производят/потребляют, начиная
            # со следующего хода (см. engine.set_building_active_count). Как
            # toggle_pin/set_energy_priority - настройка конкретного сохранения,
            # не тратит ход.
            result = engine.set_building_active_count(
                STATE, payload.get("building_id"), int(payload.get("active_count", 0)),
            )
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/cancel_job":
            # Отмена задачи (автокрафт/крафт за энергию/долгое ручное действие) - как
            # toggle_pin/toggle_autocraft, НЕ тратит ход (см. game/engine.py:
            # cancel_autocraft_job/cancel_energy_job/cancel_manual_job) - игрок не должен
            # ждать advance_turn, чтобы освободить слот, который сам же занял.
            kind = payload.get("kind")
            if kind == "autocraft":
                result = engine.cancel_autocraft_job(STATE, payload.get("resource_id"))
            elif kind == "energy":
                result = engine.cancel_energy_job(STATE, payload.get("index"))
            elif kind == "manual":
                result = engine.cancel_manual_job(STATE, payload.get("action_id"))
            elif kind == "research":
                result = engine.cancel_research(STATE)
            else:
                self._json(400, {"error": "неизвестный тип задачи"})
                return
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/house_creature":
            # Поселение особи из банка в конкретный ЭКЗЕМПЛЯР фермы - как toggle_pin/
            # cancel_job, НЕ тратит ход (управление уже своим имуществом, см.
            # engine.house_creature).
            result = engine.house_creature(STATE, payload.get("creature_id"), payload.get("farm_instance_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/release_creature":
            result = engine.release_creature(STATE, payload.get("creature_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/hunt_abandon":
            # Добровольный отказ от текущей цели - как toggle_pin/cancel_job, НЕ
            # тратит ход (см. engine.abandon_encounter).
            result = engine.abandon_encounter(STATE)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/mining_new_field":
            # "Обновить шахту" - как hunt_abandon/insert_soul, НЕ тратит ход
            # (см. engine.new_mine_field) - клики по полю шахты вообще не должны
            # двигать игровые часы, иначе исследование тумана съедало бы десятки
            # ходов за одно посещение шахты.
            result = engine.new_mine_field(STATE)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/mining_hit_cell":
            try:
                r = int(payload.get("r"))
                c = int(payload.get("c"))
            except (TypeError, ValueError):
                self._json(400, {"error": "r и c должны быть целыми числами"})
                return
            result = engine.hit_mine_field(STATE, r, c)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/insert_soul":
            # Вставка/выемка души - управление имуществом, как house_creature/
            # release_creature, НЕ тратит ход (см. engine.insert_soul/remove_soul).
            result = engine.insert_soul(STATE, payload.get("building_id"), payload.get("soul_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/remove_soul":
            try:
                index = int(payload.get("index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "index должен быть целым числом"})
                return
            result = engine.remove_soul(STATE, payload.get("building_id"), index)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/enchant_tool":
            # Попытка зачарования - управление имуществом, как insert_soul/
            # remove_soul, НЕ тратит ход (см. engine.enchant_tool). result.enchant -
            # уже готовый исход трёх кругов (три символа + успех/провал + что
            # применилось), тот же принцип, что result.prize у лутбокса - сервер
            # решает СРАЗУ, клиент только анимирует раскрытие кругов.
            result = engine.enchant_tool(STATE, payload.get("tool_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "enchant": result.enchant, "state": build_view(STATE)})
        elif self.path == "/api/add_automation_rule":
            # Настройка правила автоматизации - управление имуществом, как
            # insert_soul/remove_soul, НЕ тратит ход (см. engine.add_automation_rule).
            # kind - "manual_action" или "energy_recipe" (engine.AUTOMATION_TARGET_KINDS).
            result = engine.add_automation_rule(
                STATE, payload.get("building_id"), payload.get("kind"), payload.get("target_id"),
                payload.get("conditions") or [],
            )
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/update_automation_rule":
            try:
                index = int(payload.get("index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "index должен быть целым числом"})
                return
            result = engine.update_automation_rule(
                STATE, payload.get("building_id"), index, payload.get("kind"), payload.get("target_id"),
                payload.get("conditions") or [],
            )
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/remove_automation_rule":
            try:
                index = int(payload.get("index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "index должен быть целым числом"})
                return
            result = engine.remove_automation_rule(STATE, payload.get("building_id"), index)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/claim_quest":
            # Забрать кейс с лутом за выполненный квест - управление имуществом, как
            # insert_soul/remove_soul, НЕ тратит ход (см. engine.claim_quest_reward).
            # result.prize - реально выпавший приз барабана (или None при неудаче) -
            # клиент анимирует прокрутку лутбокса до ЭТОГО результата, не подделывая
            # его заново.
            result = engine.claim_quest_reward(STATE, payload.get("quest_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "prize": result.prize, "state": build_view(STATE)})
        elif self.path == "/api/purchase_farm_upgrade":
            # Покупка апгрейда ОДНОГО экземпляра пасеки - как house/release, НЕ
            # тратит ход (управление имуществом, а не игровое действие с рандомом/
            # шансом, см. engine.purchase_farm_instance_upgrade).
            result = engine.purchase_farm_instance_upgrade(STATE, payload.get("instance_id"), payload.get("upgrade_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/analyze_creature":
            # Мгновенное опознание генома (генетическая лаборатория) - НЕ тратит
            # ход, тем же принципом, что house/release (см. engine.analyze_creature).
            result = engine.analyze_creature(STATE, payload.get("creature_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/reset":
            STATE = GameState()
            _LAST_ENERGY_SHORTFALL = {}
            save_state(STATE)
            self._json(200, {"state": build_view(STATE)})
        elif self.path == "/api/stats":
            try:
                start_turn = int(payload.get("start_turn"))
                end_turn = int(payload.get("end_turn"))
            except (TypeError, ValueError):
                self._json(400, {"error": "start_turn и end_turn должны быть целыми числами"})
                return
            stats = build_stats_view(STATE, start_turn, end_turn)
            self._json(200, {"stats": stats})
        elif self.path == "/api/saves/save":
            name = safe_save_name(payload.get("name"))
            if name is None:
                self._json(400, {"error": "Недопустимое имя сохранения."})
                return
            SAVES_DIR.mkdir(exist_ok=True)
            STATE.save_name = name  # "Сохранить как X" - активная партия теперь называется X
            STATE.save(str(SAVES_DIR / f"{name}.json"))
            save_state(STATE)  # чтобы новое имя сразу отражалось и в текущем автосохранении
            self._json(200, {"ok": True, "saves": list_saves(), "state": build_view(STATE)})
        elif self.path == "/api/saves/load":
            name = safe_save_name(payload.get("name"))
            path = SAVES_DIR / f"{name}.json" if name is not None else None
            if path is None or not path.exists():
                self._json(404, {"error": "Такого сохранения нет."})
                return
            STATE = GameState.load(str(path))
            STATE.save_name = name  # имя определяется выбранным слотом, а не тем, что лежало в файле
            _LAST_ENERGY_SHORTFALL = {}  # снимок относится к предыдущей партии, а не к загруженной
            save_state(STATE)  # загруженное сохранение становится текущим автосохранением
            self._json(200, {"state": build_view(STATE)})
        elif self.path == "/api/heartbeat":
            global _last_heartbeat
            with _heartbeat_lock:
                _last_heartbeat = time.monotonic()
            self._json(200, {"ok": True})
        elif self.path == "/api/shutdown":
            # Сначала ответ, потом остановка - и обязательно в НОВОМ потоке:
            # HTTPServer тут однопоточный, serve_forever() и обработка этого
            # запроса выполняются в одном потоке, так что синхронный
            # self.server.shutdown() отсюда же был бы гарантированным deadlock.
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, format: str, *args) -> None:
        pass


class _SingleInstanceHTTPServer(HTTPServer):
    # http.server.HTTPServer включает allow_reuse_address по умолчанию, а на
    # Windows SO_REUSEADDR (в отличие от POSIX) позволяет ВТОРОМУ процессу
    # молча забиндиться на порт, который уже слушает первый - вместо
    # ожидаемого OSError получились бы два конкурирующих сервера, независимо
    # пишущих в один SAVE_PATH. Отключаем, чтобы повторный запуск exe (порт
    # занят) действительно падал в except OSError ниже, а не заводил
    # незаметного двойника.
    allow_reuse_address = False


class _ThreadedTestHTTPServer(ThreadingMixIn, _SingleInstanceHTTPServer):
    # ТОЛЬКО для --server-only (автотесты). Обычный клиент - одна вкладка
    # pywebview, которая шлёт запросы по одному; Playwright же держит несколько
    # параллельных соединений разом (heartbeat-таймер + сам запрос действия +
    # статика) - однопоточный HTTPServer их сериализует и на этом ощутимо
    # стопорится. В штатном режиме (реальный игрок) НЕ используется намеренно:
    # мутация STATE в обработчиках не потокобезопасна, а один игрок и так не
    # шлёт запросы параллельно сам себе.
    daemon_threads = True


def _serve(server: HTTPServer) -> None:
    try:
        server.serve_forever()
    except Exception as e:
        _show_error(f"Игра неожиданно остановилась:\n{e}")


def main() -> None:
    url = f"http://127.0.0.1:{PORT}/"
    server_only = "--server-only" in sys.argv
    server_cls = _ThreadedTestHTTPServer if server_only else _SingleInstanceHTTPServer
    try:
        server = server_cls(("127.0.0.1", PORT), Handler)
    except OSError:
        _show_error(
            f"Не получилось занять порт {PORT} - возможно, игра уже открыта в другом окне.\n"
            f"Если так, просто открой {url} в браузере."
        )
        return

    print(f"Игра запущена: {url}")
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.monotonic()
    threading.Thread(target=_watchdog, args=(server,), daemon=True).start()

    if server_only:
        # Без нативного окна pywebview - для автотестов (Playwright и т.п.),
        # которые подключаются своим браузером к тому же серверу. Открывать
        # ЕЩЁ и pywebview-окно поверх этого бессмысленно (playwright всё равно
        # не умеет им управлять) и просто удваивает время старта - see CLAUDE.md.
        print("Режим без окна: подключайся сам (напр. Playwright) к адресу выше.")
        print("Сервер сам остановится через 45с без heartbeat (см. _watchdog).")
        _serve(server)
        print("Остановлено.")
        return

    print("Закрой окно игры, когда закончишь - прогресс уже сохранён в save.json.")
    # serve_forever() уходит в фоновый поток - webview.start() ниже занимает
    # главный поток сам (нативный GUI-цикл окна обязан работать в главном
    # потоке, иначе пустой экран/падение на некоторых бэкендах Windows).
    threading.Thread(target=_serve, args=(server,), daemon=True).start()

    webview.create_window(
        "Майнкрафт на минималках", url, width=1280, height=800, min_size=(1024, 700), maximized=True
    )
    # private_mode=False - pywebview по умолчанию открывает окно в приватном
    # профиле (True), который стирается при закрытии окна. Без этого localStorage
    # (громкость музыки/звуков, см. web_ui.html: _loadVolume/_applyVolumes) не
    # переживал перезапуск игры, хотя код честно писал в него каждый раз.
    webview.start(private_mode=False)
    # Окно закрыто - сервер в фоновом потоке больше не нужен никому, кроме
    # него самого (daemon=True и так не даст ему помешать выходу процесса,
    # но explicit shutdown() не оставляет висящий поток до сборки мусора).
    server.shutdown()
    print("Остановлено.")


if __name__ == "__main__":
    main()
