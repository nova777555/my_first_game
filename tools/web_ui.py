"""
Веб-обёртка для игры - HTTP API (стандартная библиотека) поверх game/engine.py
+ окно pywebview вместо системного браузера, чтобы игра выглядела отдельным
приложением, а не вкладкой браузера. Сам HTTP-сервер по-прежнему поднимается
и слушает 127.0.0.1:8790 - при желании страницу всё ещё можно открыть вручную
в обычном браузере на том же адресе.

Единственная внешняя зависимость - pywebview (pip install pywebview).

Запуск:
    python tools/web_ui.py

Откроет игру в отдельном окне. Прогресс сохраняется в saves/autosave.json -
тот же файл, что использует консольная версия (main.py), так что партию
можно продолжать в любом из двух интерфейсов.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import time
import traceback
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
    ALLOY_RECIPES, BREAKDOWN_TYPES, BUILDINGS, CAST_RECIPES, COMBAT_MOVES, COMBAT_SETTINGS, DEFAULT_ACTION_DELAY_MS,
    ENCHANTMENTS, ENERGY_RECIPES, ERAS, GENETIC_TRAITS, LOCATIONS, LOOT_CASES, MANUAL_ACTIONS,
    MELT_RECIPES, MINE_FIELD, MINING_INFO, MOBS, MUTATIONS, QUESTS, RESOURCE_ICONS, RESOURCE_MOLTEN,
    RESOURCE_NAMES, RESOURCE_ORDER, RESOURCE_STORAGE, RITUAL_RECIPES, SKILLS, SOUL_RECIPES, SPECIES,
    TECHS, TOOLS, UI_SOUNDS,
)
from game.state import GameState  # noqa: E402

# В exe-сборке ROOT указывает внутрь распакованной папки сборки - сохранения
# должны лежать РЯДОМ С EXE (портативная папка), а не там же, где content.json/html.
# GAME_SAVE_ROOT - песочница вместо настоящей папки: её выставляет tests/fixtures.py,
# иначе тест, дошедший до save_state (например, через craft_grid_view -> run_turn),
# перезаписывал настоящий saves/autosave.json игрока.
if os.environ.get("GAME_SAVE_ROOT"):
    _SAVE_ROOT = Path(os.environ["GAME_SAVE_ROOT"])
elif getattr(sys, "frozen", False):
    _SAVE_ROOT = Path(sys.executable).resolve().parent
else:
    _SAVE_ROOT = ROOT

# Именованные сохранения (папка saves/, рядом с проектом) - "Сохранить как"/
# "Открыть" по явному выбору игрока, а не то, что подхватывается при простом
# запуске интерфейса. Автосохранение (SAVE_PATH) лежит в той же папке, файлом
# autosave.json - раньше лежало отдельно, прямо в корне репозитория, что было
# неочевидно и путало игрока при поиске файлов сохранений.
SAVES_DIR = _SAVE_ROOT / "saves"
SAVE_PATH = SAVES_DIR / "autosave.json"
# Старое место автосохранения (до переноса в saves/) - если оно ещё лежит в
# корне у игрока, при первом запуске переносим его на новое место, а не
# тихо теряем прогресс, подсовывая вместо него новую пустую игру.
_LEGACY_SAVE_PATH = _SAVE_ROOT / "save.json"
HTML_PATH = Path(__file__).with_name("web_ui.html")
# Оформление модалки механизма (locations.*.appearance) - общий JS с превью в
# редакторе, content_editor.py отдаёт тот же файл (см. докстринг mechanism_fx.js).
MECHANISM_FX_PATH = Path(__file__).with_name("mechanism_fx.js")
# Сцена схватки (модалка «Охота») - общий JS с превью «Бой»/«Сцена охоты» в редакторе,
# см. докстринг tools/hunt_fx.js.
HUNT_FX_PATH = Path(__file__).with_name("hunt_fx.js")
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


def _migrate_legacy_save() -> None:
    # __name__-гейт важен: STATE = load_state() ниже выполняется на самом
    # ИМПОРТЕ модуля (в т.ч. `import web_ui` из тестов, до того как тест
    # успевает подменить SAVE_PATH/SAVES_DIR своими tmp-путями) - без этой
    # проверки тесты переносили бы НАСТОЯЩИЙ save.json игрока из корня
    # репозитория. При запуске как скрипта (`python tools/web_ui.py`, в т.ч.
    # с --server-only) __name__ уже "__main__", перенос отрабатывает как надо.
    if __name__ != "__main__":
        return
    if SAVE_PATH.exists() or not _LEGACY_SAVE_PATH.exists():
        return
    SAVES_DIR.mkdir(exist_ok=True)
    _LEGACY_SAVE_PATH.replace(SAVE_PATH)


# Что игрок должен узнать о загрузке автосейва (он был битым и отложен в копию) -
# отдаётся ОДИН раз, первым /api/state (build_view не трогает, см. do_GET).
LOAD_NOTICE: str | None = None


def _set_aside_autosave() -> Path:
    """Откладывает текущий автосейв в копию рядом (не удаляет) - она видна в
    «Открыть» как обычное сохранение, её можно вернуть, когда причина устранена."""
    SAVES_DIR.mkdir(exist_ok=True)
    backup = SAVES_DIR / f"autosave.broken-{time.strftime('%Y%m%d-%H%M%S')}.json"
    SAVE_PATH.replace(backup)
    return backup


def read_save(path: Path) -> GameState:
    """Сохранение с диска - уже очищенное от ссылок на удалённый контент."""
    state = GameState.load(str(path))
    engine.drop_removed_content(state)
    return state


def load_state() -> GameState:
    global LOAD_NOTICE
    _migrate_legacy_save()
    if not SAVE_PATH.exists():
        return GameState()
    try:
        return read_save(SAVE_PATH)
    except Exception as e:  # битый файл не должен мешать запуску: игрок увидит пустое окно
        traceback.print_exc()
        backup = _set_aside_autosave()
        LOAD_NOTICE = (
            f"Автосейв не удалось прочитать ({type(e).__name__}: {e}) - начата новая игра. "
            f"Старый файл не удалён: он лежит в сохранениях под именем «{backup.stem}»."
        )
        return GameState()


def save_state(state: GameState) -> None:
    SAVES_DIR.mkdir(exist_ok=True)
    state.save(str(SAVE_PATH))


def safe_save_name(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    # "autosave" зарезервировано под SAVE_PATH (файл autosave.json в той же
    # SAVES_DIR) - именованное сохранение с этим именем перезаписало бы
    # автосейв (регистр не важен - Windows не различает регистр в путях).
    if not name or name in (".", "..") or _UNSAFE_SAVE_NAME_CHARS.search(name) or name.lower() == "autosave":
        return None
    return name


def list_saves() -> list[dict]:
    """Именованные сохранения в SAVES_DIR - имя (без .json), номер хода и время
    изменения файла (для сортировки "недавние сверху"). Битые/нечитаемые файлы
    просто пропускаются, а не роняют список. autosave.json - не именованное
    сохранение (это SAVE_PATH), в список не входит."""
    if not SAVES_DIR.is_dir():
        return []
    items = []
    for path in SAVES_DIR.glob("*.json"):
        if path.stem.lower() == "autosave":
            continue
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
    return [{"id": r, "name": RESOURCE_NAMES.get(r, r), "amount": a, "jump_to": jump.get(r)} for r, a in cost.items()]


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
    удобнее, чем гадать, почему приоритет "ни на что не влияет".

    Рецепты преобразователя - тем же списком, id = ключ "energy_recipe:<id>" (kind
    "energy_recipe"): приоритет выше обычного рецепта ресурса делает преобразователь
    основным путём (engine.converter_preferred_resources). Так же репликация -
    "replicator:<ресурс>" (kind "replicator", engine.replicator_preferred_resources).
    "is_fallback" - рецепт сейчас запасной путь ресурса: берётся, если основной не
    сможет (engine._order_route)."""
    recipe = engine.resource_recipe(state)
    preferred = engine.converter_preferred_resources(state, recipe)
    # Репликатор основным путём (приоритет "replicator:<ресурс>" выше остальных) делает
    # запасными и обычный рецепт, и преобразователь этого ресурса.
    replicated = engine.replicator_preferred_resources(state, recipe)
    result = []
    for aid, a in MANUAL_ACTIONS.items():
        if not a["cost"]:
            continue
        default_priority = a.get("priority", 0)
        result.append({
            "id": aid, "name": a["name"], "kind": "manual_action",
            "gain": [{"id": r, "name": RESOURCE_NAMES[r]} for r in a["gain"]],
            "priority": engine.action_priority(state, aid),
            "default_priority": default_priority,
            "overridden": aid in state.action_priority,
            "is_active_recipe": any(
                recipe.get(r) == aid and r not in preferred and r not in replicated for r in a["gain"]
            ),
            "is_fallback": any(recipe.get(r) == aid and (r in preferred or r in replicated) for r in a["gain"]),
        })
    for rid, r in ENERGY_RECIPES.items():
        key = engine.grid_recipe_key("energy_recipe", rid)
        own = [res for res in r.get("produces", {}) if engine.ENERGY_RECIPE_FOR_RESOURCE.get(res) == rid]
        result.append({
            "id": key, "name": r["name"], "kind": "energy_recipe",
            "gain": [{"id": res, "name": RESOURCE_NAMES[res]} for res in r.get("produces", {})],
            "priority": engine.action_priority(state, key),
            "default_priority": 0,
            "overridden": key in state.action_priority,
            "is_active_recipe": any((res in preferred or res not in recipe) and res not in replicated for res in own),
            "is_fallback": any((res in recipe and res not in preferred) or res in replicated for res in own),
        })
    # Репликация (resources.*.replication_cost) - только если в игре есть репликатор.
    matters = {(b.get("replicator") or {}).get("matter") for b in BUILDINGS.values() if b.get("replicator")}
    if matters:
        for res in engine.replicable_resources():
            if res in matters:
                continue
            key = engine.grid_recipe_key("replicator", res)
            has_other = res in recipe or res in engine.ENERGY_RECIPE_FOR_RESOURCE
            result.append({
                "id": key, "name": f"Репликация: {RESOURCE_NAMES[res]}", "kind": "replicator",
                "gain": [{"id": res, "name": RESOURCE_NAMES[res]}],
                "priority": engine.action_priority(state, key),
                "default_priority": 0,
                "overridden": key in state.action_priority,
                "is_active_recipe": res in replicated or not has_other,
                "is_fallback": has_other and res not in replicated,
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
    """Ручные действия, зависящие ОТ ЭТОЙ постройки (engine.action_mechanism_building -
    свой requires_building действия или унаследованный от его локации, например все
    рецепты печи-механизма) - обычно действия постройки-"механизма" без единого
    produces/consumes (см. CLAUDE.md) - именно их cost/gain реально меняют улучшения
    такой постройки, а не пустые produces/consumes самой постройки."""
    return [aid for aid, a in MANUAL_ACTIONS.items() if engine.action_mechanism_building(a) == building_id]


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


def _superseded_upgrade_ids(defs: list[dict], purchased_ids: list[str]) -> set[str]:
    """id улучшений, которые нужно СКРЫТЬ из списка отображения - более слабые
    уже купленные версии ТОГО ЖЕ типа, когда куплена более сильная версия
    того же типа (repeatable-апгрейды сюда не попадают - у них нет "версий",
    см. вызывающих). Раньше "самый сильный" искался через max(u["value"]) -
    ломалось на неупорядочиваемых value (например dict {width,height} у
    reactor_grid_size, TypeError на сравнении dict с dict). Вместо сравнения
    value полагаемся на инвариант purchase_building_upgrade/
    building_upgrade_prerequisite: апгрейды одного типа обязаны покупаться
    строго по порядку их перечисления в content.json (следующий уровень
    прогрессии нельзя купить, пока не куплен предыдущий) - поэтому "самый
    сильный купленный" однозначно определяется как ПОСЛЕДНИЙ по порядку в
    defs, а не как обладатель наибольшего value."""
    strongest_purchased_id: dict[str, str] = {}
    for u in defs:
        if u["id"] in purchased_ids and not u.get("repeatable"):
            strongest_purchased_id[u["type"]] = u["id"]
    return {
        u["id"] for u in defs
        if u["id"] in purchased_ids and not u.get("repeatable") and u["id"] != strongest_purchased_id.get(u["type"])
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
    superseded_ids = _superseded_upgrade_ids(defs, purchased_ids)

    upgrades = []
    for u in defs:
        times = purchased_ids.count(u["id"])
        purchased = times > 0
        repeatable = u.get("repeatable", False)
        if u["id"] in superseded_ids:
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
    # Рецепты с узором, когда у преобразователя есть сетка, собираются только
    # раскладкой в его модалке (crafting_view) - карточкой их не показываем, та
    # же граница, что у сеточных ручных действий в build_view.
    grid_recipes = set(engine.grid_energy_recipes_at(engine.CONVERTER_LOCATION_ID, state))
    items = []
    for rid in engine.available_energy_recipes(state):
        if rid in grid_recipes:
            continue
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
    superseded_ids = _superseded_upgrade_ids(regular_defs, purchased_ids)

    upgrades = []
    for u in regular_defs:
        times = purchased_ids.count(u["id"])
        purchased = times > 0
        repeatable = u.get("repeatable", False)
        if u["id"] in superseded_ids:
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
        # Гены вида - в порядке аллелей каталога genetic_traits (он и есть шкала
        # «хуже -> лучше»), а не по доле.
        gene_pool = []
        for trait_id, pool in entry["gene_pool"].items():
            alleles = engine.trait_alleles(trait_id)
            gene_pool.append({
                "trait_id": trait_id,
                "trait_name": GENETIC_TRAITS.get(trait_id, {}).get("name", trait_id),
                "alleles": [
                    {"id": aid, "name": allele.get("name", aid), "chance": pool[aid]}
                    for aid, allele in alleles.items() if aid in pool
                ],
            })
        catalog.append({
            "id": species_id,
            "name": RESOURCE_NAMES.get(species_id, species_id),
            "description": entry["description"],
            "family": entry["family"],
            "produces": cost_list(entry["produces"]),
            "consumes": cost_list(entry["consumes"]),
            "breedable": entry["breedable"],
            "recipes": recipes,
            "gene_pool": gene_pool,
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
    ОДНА запись на тип зачарования, без разбивки по уровню - см.
    enchantment_pool: совпадение всех трёх кругов теперь решает только тип, а
    не тип+уровень). "levels" внутри каждой записи pool - НЕ барабан, а
    прогресс ПО КОНТЕНТУ (enchantments.*.levels) с отметкой reached, для
    панели "Доступно" справа - что уже достигнуто и что можно поднять дальше
    очередным совпадением."""
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
            pool_by_enchant[eid] = {
                "enchant_id": eid, "name": ench["name"], "description": ench.get("description", ""),
                "levels": [
                    {"level": i, "value": lvl.get("value"), "reached": own_applied.get(eid, 0) >= i}
                    for i, lvl in enumerate(ench.get("levels", []), start=1)
                ],
            }
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
        # Настройка контента (buildings.*.enchanting_table.strip_on_fail, правит
        # автор в редакторе), НЕ игрок - в модалке это просто информационная
        # пометка, без переключателя.
        "strip_on_fail": engine.enchanting_table_strip_on_fail(state),
    }


def _combat_gear_view(state: GameState) -> dict:
    """Снаряжение для панели схватки: линейки оружия/брони (tools.*.weapon/armor),
    которые игрок реально выковал - тир 2+ (стартовый тир 1 вроде «Кулаки»/«Не выковано»
    предметом не считается, тот же порог, что engine.tool_usable_for_repair). Прочность -
    как в tools_view; slash_color - цвет разреза этого оружия в сцене (tools.*.tiers[].
    slash_color, необязателен). damage_reduction - итоговое снижение ответки ВСЕЙ бронёй
    (с зачарованиями), для чипа «броня −N»."""
    weapons, armor = [], []
    for tool_id, tool in TOOLS.items():
        if not (tool.get("weapon") or tool.get("armor")):
            continue
        tier = engine.tool_tier(state, tool_id)
        if tier < 2:
            continue
        tier_def = tool["tiers"][tier - 1]
        entry = {
            "tool_id": tool_id, "tier": tier, "name": tier_def.get("name") or tool["name"],
            "max_durability": tier_def.get("max_durability"),
            "durability_remaining": engine.tool_durability_remaining(state, tool_id),
            "is_broken": engine.tool_is_broken(state, tool_id),
        }
        if tool.get("weapon"):
            entry.update(
                damage_multiplier=tier_def.get("damage_multiplier", 1.0),
                instant_kill_max_hp=tier_def.get("instant_kill_max_hp", 0),
                slash_color=tier_def.get("slash_color"),
            )
            weapons.append(entry)
        else:
            entry.update(damage_reduction=tier_def.get("damage_reduction", 0), max_hp_bonus=tier_def.get("max_hp_bonus", 0))
            armor.append(entry)
    return {"weapons": weapons, "armor": armor, "damage_reduction": engine.armor_damage_reduction(state)}


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
            "location_name": LOCATIONS.get(mob.get("location"), {}).get("name", ""),
            "is_boss": bool(mob.get("is_boss")),
            "remaining_hp": state.active_encounter["remaining_hp"],
            "max_hp": mob["hp"],
            "turns_left": max(0, deadline - state.turn) if deadline is not None else None,
            "turn_limit": mob.get("turn_limit"),
            # Визуал схватки - целиком из контента (mobs.*.combat_fx, locations.*.hunt_scene),
            # как есть: недостающие поля клиент сам заполняет значениями по умолчанию
            # (web_ui.html: huntMobFx/huntSceneCfg) - единственное место этих умолчаний.
            "fx": dict(mob.get("combat_fx") or {}),
            "scene": dict(LOCATIONS.get(mob.get("location"), {}).get("hunt_scene") or {}),
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
    if combat_skill is not None:
        levels = SKILLS.get(engine.COMBAT_SKILL_ID, {}).get("levels", [])
        # Эффекты ВСЕХ уровней - экран «Добыча» показывает, что дал новый уровень
        # (урон/HP было -> стало), не зная структуры effects заранее.
        combat_skill = {**combat_skill, "level_effects": [lvl.get("effects", {}) for lvl in levels],
                        "level_xp": [lvl.get("xp_required", 0) for lvl in levels]}
    moves = engine.combat_moves_view(state)
    for entry in moves:
        move = COMBAT_MOVES.get(entry["id"], {})
        entry["fx"] = dict(move.get("fx") or {})
        entry["icon"] = move.get("icon")
    predictions = {}
    attack_prediction = None
    if state.active_encounter:
        # Прогноз удара - тот же расчёт, что у настоящей атаки (engine.attack_preview).
        # "qte" - тот же прогноз при попадании в QTE-кольцо (None, если бонуса нет).
        bonus = engine.qte_damage_bonus() > 0

        def predict(move_id):
            p = engine.attack_preview(state, move_id)
            if p is not None:
                p["qte"] = engine.attack_preview(state, move_id, qte_hit=True) if bonus else None
            return p
        attack_prediction = predict(None)
        predictions = {m["id"]: predict(m["id"]) for m in moves if m["unlocked"]}
    return {
        "active_encounter": encounter, "mobs": entries, "combat_skill": combat_skill,
        "combat_moves": moves,
        "attack_prediction": attack_prediction, "predictions": predictions,
        "gear": _combat_gear_view(state),
        # Каждый удар - ход; сцена двигает небо на столько часов, таймер босса
        # подписан ещё и в часах.
        "hours_per_turn": engine.TIME["hours_per_turn"],
        "qte_damage_bonus": engine.qte_damage_bonus(),
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


def soul_collector_instance_upgrades_view(instance_id: str, state: GameState) -> dict:
    """Как farm_instance_upgrades_view, но апгрейды читаются с ОДНОГО экземпляра
    синтезатора душ (state.soul_collector_instances[instance_id]["upgrades"]) -
    см. CLAUDE.md/state.py:soul_collector_instances. Упрощённая версия без ветки
    environment_emulation (её не бывает у soul_collector построек - см.
    engine.purchase_soul_collector_instance_upgrade)."""
    instance = state.soul_collector_instances[instance_id]
    building_id = instance["building_id"]
    defs = BUILDINGS[building_id].get("upgrades", [])
    purchased_ids = instance["upgrades"]
    hypothetical_current = {building_id: purchased_ids}
    current = _building_upgrade_snapshot(building_id, state, hypothetical_current)
    dependent_action_names = [MANUAL_ACTIONS[aid]["name"] for aid in _dependent_action_ids(building_id)]

    superseded_ids = _superseded_upgrade_ids(defs, purchased_ids)

    upgrades = []
    for u in defs:
        times = purchased_ids.count(u["id"])
        purchased = times > 0
        repeatable = u.get("repeatable", False)
        if u["id"] in superseded_ids:
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
    return {"current": current, "dependent_action_names": dependent_action_names, "upgrades": upgrades}


def soul_collector_entries_view(state: GameState) -> list[dict]:
    """Один элемент на КАЖДЫЙ физический экземпляр синтезатора душ (не на тип,
    как было до 2026-09-18 - см. game/engine.py:soul_collector_instances_view/
    state.soul_collector_instances) для отдельной модалки "Души" (открывается
    кнопкой на карточке конкретной built-постройки, см. web_ui.html:
    openSoulSynthesizerModal) - зеркало farms_view/farm_instance_upgrades_view."""
    result = []
    for entry in engine.soul_collector_instances_view(state):
        result.append({
            **entry,
            "installed": [{**s, "name": RESOURCE_NAMES[s["id"]]} for s in entry["installed"]],
            "upgrades": soul_collector_instance_upgrades_view(entry["id"], state),
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


def reactor_instance_upgrades_view(instance_id: str, state: GameState) -> dict:
    """Как soul_collector_instance_upgrades_view, но БЕЗ _building_upgrade_snapshot/
    preview - апгрейды реактора не трогают produces/consumes рецепта постройки
    (у него их и нет, см. buildings.*.reactor), они меняют размер решётки/число
    зон/телеметрию (см. game/engine.py:reactor_grid_dims/reactor_zone_count).
    "reactor_zoning" - как environment_emulation у ферм: взаимоисключающий
    выбор, показывается ВСЕМИ вариантами сразу, без прогрессии/prerequisite."""
    instance = state.reactor_instances[instance_id]
    building_id = instance["building_id"]
    defs = BUILDINGS[building_id].get("upgrades", [])
    purchased_ids = instance["upgrades"]

    regular_defs = [u for u in defs if u["type"] != "reactor_zoning"]
    superseded_ids = _superseded_upgrade_ids(regular_defs, purchased_ids)

    upgrades = []
    for u in regular_defs:
        times = purchased_ids.count(u["id"])
        purchased = times > 0
        repeatable = u.get("repeatable", False)
        if u["id"] in superseded_ids:
            continue
        prerequisite = engine.building_upgrade_prerequisite(building_id, u["id"])
        if prerequisite is not None and prerequisite["id"] not in purchased_ids:
            continue
        req_tech = u.get("requires_tech")
        tech_locked = bool(req_tech) and req_tech not in state.techs
        max_purchases = u.get("max_purchases")
        at_max = repeatable and max_purchases is not None and times >= max_purchases
        current_cost = engine.repeatable_upgrade_cost(u, times) if repeatable else u["cost"]
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
        })

    zoning_defs = [u for u in defs if u["type"] == "reactor_zoning"]
    zoning_ids = {d["id"] for d in zoning_defs}
    for u in zoning_defs:
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
            "replaces_current_zoning": not purchased and any(uid in zoning_ids for uid in purchased_ids),
        })
    return {"upgrades": upgrades}


def reactor_instances_view(state: GameState) -> list[dict]:
    """Один элемент на КАЖДЫЙ физический экземпляр реактора (см. game/engine.py:
    reactor_instances_view/state.reactor_instances) для отдельной модалки
    "Реактор" (кнопка на карточке конкретной built-постройки, см. web_ui.html:
    openReactorModal) - зеркало soul_collector_entries_view."""
    result = []
    for entry in engine.reactor_instances_view(state):
        result.append({**entry, "upgrades": reactor_instance_upgrades_view(entry["id"], state)})
    return result


def reactor_component_bank_view(state: GameState) -> list[dict]:
    """Банк компонентов реактора в инвентаре - тот же приём, что
    souls_bank_view/creature_bank_view. role - тег resources.*.
    reactor_component.role (см. game/engine.py:reactor_component_role) -
    модалка конкретного реактора фильтрует банк по своему accepts_roles."""
    return [
        {"id": e["id"], "name": RESOURCE_NAMES[e["id"]], "role": e["role"], "amount": e["amount"]}
        for e in engine.reactor_component_bank_view(state)
    ]


def reactor_catalyst_bank_view(state: GameState) -> list[dict]:
    """Банк катализаторов реактора в инвентаре - зеркало
    reactor_component_bank_view. applies_to - тег ролей, которым подходит
    катализатор (см. game/engine.py:reactor_catalyst_bank_view) - модалка
    ячейки фильтрует по роли уже стоящего в ней компонента."""
    return [
        {"id": e["id"], "name": RESOURCE_NAMES[e["id"]], "applies_to": e["applies_to"], "amount": e["amount"]}
        for e in engine.reactor_catalyst_bank_view(state)
    ]


def reactor_coolant_fluid_bank_view(state: GameState) -> list[dict]:
    """Охлаждающие жидкости реактора с запасом игрока - зеркало
    reactor_catalyst_bank_view. cooling_mult/units_per_power отдаются наружу,
    чтобы UI показывал игроку компромисс "снимает больше / тратится быстрее"
    прямо в момент выбора, а не прятал его в справочнике."""
    return [
        {"id": e["id"], "name": RESOURCE_NAMES[e["id"]], "cooling_mult": e["cooling_mult"],
         "units_per_power": e["units_per_power"], "amount": e["amount"]}
        for e in engine.reactor_coolant_fluid_bank_view(state)
    ]


def _ritual_recipe_view(recipe_id: str, state: GameState) -> dict:
    """Один рецепт ритуала для модалки "Ритуалы" - lock_reason различает ТРИ
    независимые причины недоступности (тех/постройка/не хватает пьедesталов),
    чтобы UI мог показать игроку конкретную, а не общую "недоступно".
    ring_cost/center_cost/extra_cost - ОТДЕЛЬНЫЕ списки (не слиты в один, как
    раньше), потому что фронтенд рисует их по-разному: ring_cost разворачивается
    в N физических пьедesталов (по одному на каждую единицу, см. openRitualCircleModal),
    center_cost - единственный особый пьедestal, extra_cost - фоновая цена без
    своего пьедestala вовсе. Всё это списывается ОДНИМ платежом при старте (см.
    engine._ritual_full_cost/start_ritual) - разделение чисто визуальное."""
    recipe = RITUAL_RECIPES[recipe_id]
    available = engine.is_ritual_recipe_available(recipe_id, state)
    lock_reason = None
    if not available:
        req_tech = recipe.get("requires_tech")
        building_id = recipe["requires_building"]
        if req_tech and req_tech not in state.techs:
            lock_reason = "tech"
        elif engine.working_count(state, building_id) <= 0:
            lock_reason = "building"
        else:
            lock_reason = "pedestals"
    ring_cost = recipe.get("ring_cost", {})
    center = recipe["center_cost"]
    return {
        "id": recipe_id,
        "name": recipe["name"],
        "description": recipe.get("description", ""),
        "available": available,
        "lock_reason": lock_reason,
        "ring_total": sum(ring_cost.values()),
        "ring_cost": cost_list(ring_cost, state),
        "center_cost": cost_list({center: 1}, state)[0],
        "extra_cost": cost_list(recipe.get("extra_cost", {}), state),
        "duration_turns": recipe.get("duration_turns", 1),
        "extra_consumes_per_turn": manual_action_cost_list(
            recipe.get("extra_consumes_per_turn", {}), recipe.get("extra_consumes_chance") or {}, state,
        ),
        "produces": manual_action_gain_list(recipe.get("produces", {}), recipe.get("produces_chance") or {}),
        "sound_url": sound_url(recipe.get("sound")),
    }


def ritual_altars_view(state: GameState) -> list[dict]:
    """Все построенные ритуальные алтари (постройки с полем ritual_circle) для
    отдельной модалки "Ритуалы" (см. web_ui.html: openRitualCircleModal) - тот
    же приём разделения, что у soul_collector_entries_view: список ТИПОВ
    построек, не физических копий (capacity уже агрегирует working_count
    внутри engine.ritual_pedestal_capacity)."""
    result = []
    for bid, b in BUILDINGS.items():
        config = b.get("ritual_circle")
        if not config or state.buildings.get(bid, 0) <= 0:
            continue
        active_job = state.ritual_jobs.get(bid)
        result.append({
            "building_id": bid,
            "name": b["name"],
            "capacity": engine.ritual_pedestal_capacity(state, bid),
            "active_job": (
                {
                    "recipe_id": active_job["recipe_id"],
                    "recipe_name": RITUAL_RECIPES.get(active_job["recipe_id"], {}).get("name", active_job["recipe_id"]),
                    "turns_done": active_job["turns_done"],
                    "turns_total": active_job["turns_total"],
                }
                if active_job else None
            ),
            "recipes": [
                _ritual_recipe_view(rid, state)
                for rid, recipe in RITUAL_RECIPES.items()
                if recipe.get("requires_building") == bid
            ],
        })
    return result


def replicator_view(state: GameState, rates: dict | None = None) -> dict | None:
    """Модалка репликатора (web_ui.html: openReplicatorModal): построенные
    репликаторы (buildings.*.replicator) с лимитом материи за ход и слотами, запас
    материи и её приток за ход (генераторы), каталог ресурсов с ценой репликации
    (resources.*.replication_cost) и идущие репликации. None - в контенте нет ни
    одного репликатора. Материя - у первого построенного типа (разные типы с разной
    материей - экзотика контента, каталог у них общий)."""
    types = [bid for bid, b in BUILDINGS.items() if b.get("replicator")]
    if not types:
        return None
    machines = []
    for bid in types:
        count = state.buildings.get(bid, 0)
        if count <= 0:
            continue
        per_turn = engine.replicator_max_matter_per_turn(state, bid)
        machines.append({
            "id": bid, "name": BUILDINGS[bid]["name"], "count": count,
            "working": engine.working_count(state, bid),
            "slots": engine.replicator_slot_capacity(state, bid),
            "busy": sum(1 for j in state.replicator_jobs if j["building_id"] == bid),
            "per_turn": None if per_turn is None else engine._format_amount(per_turn),
            "automated": engine.has_autocraft_upgrade(state, bid),
            "upgrades": building_upgrades_view(bid, state),
        })
    matter = engine.replicator_matter(machines[0]["id"] if machines else types[0])
    catalog = []
    for rid in engine.replicable_resources():
        if rid == matter:
            continue
        free = engine.replication_storage_free(state, rid)
        catalog.append({
            "resource_id": rid, "name": RESOURCE_NAMES[rid],
            "price": engine.RESOURCE_REPLICATION_COST[rid],
            "have": state.resources.get(rid, 0),
            # сколько ещё влезет на склад (null - без ограничения), см. engine.replicate
            "storage_free": None if free is None else max(0, engine._format_amount(free)),
        })
    return {
        "matter": {
            "id": matter, "name": RESOURCE_NAMES.get(matter, matter),
            "amount": engine._format_amount(state.resources.get(matter, 0)),
            "rate": engine._format_amount((rates or {}).get(matter, 0)),
            "fractional": bool(engine.RESOURCE_FRACTIONAL.get(matter, False)),
        },
        "machines": machines,
        "free_slot": engine._pick_replicator_with_free_slot(state) is not None,
        "catalog": catalog,
        "jobs": engine.replicator_jobs_view(state),
    }


def _amounts_view(amounts: dict, have: dict | None = None) -> list[dict]:
    """[{id, name, amount, molten, have?}] - ингредиенты/выход рецепта плавильни."""
    out = []
    for rid, amount in amounts.items():
        item = {"id": rid, "name": RESOURCE_NAMES.get(rid, rid), "amount": amount, "molten": engine.is_molten(rid)}
        if have is not None:
            item["have"] = engine._format_amount(have.get(rid, 0))
        out.append(item)
    return out


def _tank_layers(tank: dict) -> list[dict]:
    """Содержимое тигля слоями СНИЗУ ВВЕРХ: металлы по плотности (тяжёлый тонет), шлак -
    всегда сверху (всплывает). color/density - из resources.*.molten."""
    layers = [
        {
            "id": rid, "name": RESOURCE_NAMES.get(rid, rid), "amount": amount, "molten": engine.is_molten(rid),
            "color": RESOURCE_MOLTEN.get(rid, {}).get("color"),
            "density": RESOURCE_MOLTEN.get(rid, {}).get("density", 0),
        }
        for rid, amount in tank.items() if amount > 0
    ]
    layers.sort(key=lambda l: (not l["molten"], -l["density"]))
    return layers


def smelteries_view(state: GameState) -> list[dict]:
    """Построенные плавильни (buildings.*.smeltery) для модалки «Плавильня» (web_ui.html:
    openSmelteryModal): слоты плавки, слои тигля, отливка на литейном столе и все рецепты,
    доступные ИМЕННО этой плавильне (жар, технология) - каждый с reason, почему прямо
    сейчас его не выполнить (null - можно), теми же проверками, что у самого действия
    (engine.*_block_reason). bank - склад для клика «в слот»: melt_recipe - рецепт, которым
    эта плавильня расплавит ресурс (самый «горячий» из доступных), null - не плавится."""
    result = []
    for bid, b in BUILDINGS.items():
        if not b.get("smeltery") or state.buildings.get(bid, 0) <= 0:
            continue
        sm = state.smelteries.get(bid) or {"slots": [], "tank": {}, "cast": None}
        slot_cap = engine.smeltery_slot_capacity(state, bid)
        tank_cap = engine.smeltery_tank_capacity(state, bid)
        used = engine.smeltery_tank_used(state, bid)
        slots = []
        for i in range(max(slot_cap, len(sm["slots"]))):
            slot = sm["slots"][i] if i < len(sm["slots"]) else None
            if slot is None:
                if i < slot_cap:
                    slots.append(None)
                continue
            recipe = MELT_RECIPES.get(slot["recipe_id"], {})
            turns = recipe.get("turns", 1)
            main = engine.melt_main_input(recipe) or next(iter(slot.get("cost") or {}), None)
            need = sum((recipe.get("output") or {}).values())
            slots.append({
                "index": i, "recipe_id": slot["recipe_id"], "name": recipe.get("name", slot["recipe_id"]),
                "main_id": main, "main_name": RESOURCE_NAMES.get(main, main),
                "main_amount": (slot.get("cost") or {}).get(main, 1),
                "turns_done": slot["turns_done"], "turns_total": turns,
                # доплавилось, но не влезает в тигель; fits - уже влезет, стечёт в след. ход
                "waiting": slot["turns_done"] >= turns, "need": need, "fits": used + need <= tank_cap,
                # слот за пределами ёмкости (копия плавильни сломалась) - стоит
                "paused": i >= slot_cap,
            })
        cast = sm.get("cast")
        cast_view = None
        if cast:
            recipe = CAST_RECIPES.get(cast["recipe_id"], {})
            cast_view = {
                "recipe_id": cast["recipe_id"], "name": recipe.get("name", cast["recipe_id"]),
                "form_id": recipe.get("form"), "metal_id": recipe.get("metal"),
                "output": _amounts_view(recipe.get("output", {})),
                "turns_done": cast["turns_done"], "turns_total": cast["turns_total"],
            }
        melt = []
        for rid, recipe in MELT_RECIPES.items():
            if not engine.is_smeltery_recipe_available(state, "melt", rid, bid):
                continue
            melt.append({
                "key": engine.smeltery_recipe_key("melt", rid), "id": rid, "name": recipe["name"],
                "main_id": engine.melt_main_input(recipe),
                "input": _amounts_view(recipe.get("input", {}), state.resources),
                "output": _amounts_view(recipe.get("output", {})),
                "turns": recipe.get("turns", 1), "heat": recipe.get("heat", 1),
                "reason": engine.melt_block_reason(state, bid, rid),
            })
        alloys = []
        for rid, recipe in ALLOY_RECIPES.items():
            if not engine.is_smeltery_recipe_available(state, "alloy", rid, bid):
                continue
            alloys.append({
                "key": engine.smeltery_recipe_key("alloy", rid), "id": rid, "name": recipe["name"],
                # пропорция скрыта до первой пробы (клиент рисует «???»)
                "hidden": bool(recipe.get("hidden")) and rid not in state.discovered_alloys,
                "input": _amounts_view(recipe.get("input", {}), sm["tank"]),
                "output": _amounts_view(recipe.get("output", {})),
                "batches": min(
                    (sm["tank"].get(r, 0) // a for r, a in recipe.get("input", {}).items() if a > 0), default=0,
                ),
                "reason": engine.alloy_block_reason(state, bid, rid),
            })
        casts = []
        for rid, recipe in CAST_RECIPES.items():
            if not engine.is_smeltery_recipe_available(state, "cast", rid, bid):
                continue
            form = recipe.get("form")
            casts.append({
                "key": engine.smeltery_recipe_key("cast", rid), "id": rid, "name": recipe["name"],
                "form_id": form, "form_name": RESOURCE_NAMES.get(form, form),
                "form_have": state.resources.get(form, 0),
                "metal_id": recipe["metal"], "metal_name": RESOURCE_NAMES.get(recipe["metal"], recipe["metal"]),
                "amount": recipe.get("amount", 1), "output": _amounts_view(recipe.get("output", {})),
                "turns": recipe.get("turns", 1), "reason": engine.cast_block_reason(state, bid, rid),
            })
        forms = []
        for c in casts:
            if c["form_id"] and all(f["id"] != c["form_id"] for f in forms):
                forms.append({"id": c["form_id"], "name": c["form_name"], "have": c["form_have"]})
        bank = []
        for rid in RESOURCE_ORDER:
            amount = state.resources.get(rid, 0)
            if amount <= 0 or rid == engine.ENERGY_RESOURCE_ID:
                continue
            bank.append({
                "id": rid, "name": RESOURCE_NAMES[rid], "amount": engine._format_amount(amount),
                "melt_recipe": engine.best_melt_recipe_for(state, bid, rid),
            })
        result.append({
            "building_id": bid, "name": b["name"], "count": state.buildings.get(bid, 0),
            "working": engine.working_count(state, bid), "heat": engine.smeltery_heat(state, bid),
            "slot_capacity": slot_cap, "capacity": tank_cap, "used": used,
            "slots": slots, "tank": _tank_layers(sm["tank"]),
            "dross": sum(a for r, a in sm["tank"].items() if not engine.is_molten(r)),
            "cast": cast_view, "melt_recipes": melt, "alloy_recipes": alloys, "cast_recipes": casts,
            "forms": forms, "bank": bank,
        })
    return result


def pinned_recipes_view(state: GameState, crafting: list[dict], smelteries: list[dict]) -> list[dict]:
    """Плитки левой колонки «Закреплённые» (web_ui.html: globalPinnedRecipes/
    craftGridPinnedTile) в модалках механизмов, плавильни и склада - по одной на
    state.pinned_recipes, в порядке закрепления. action_id - ключ закрепления (им же
    клиент находит способ в справочнике), gain[0] - ресурс, чью карточку открывает
    клик. enough: True/False - точка «хватает сырья», None - точки нет (у способа
    нет разовой цены: постройка, шахта, лут...). Ключ, который контент больше не
    знает, пропускается (открепить такой можно, но показывать нечего)."""
    grid = {r["action_id"]: dict(r, location_id=e["location_id"], location_name=e["name"])
            for e in crafting for r in e["recipes"]}
    sources_cache: dict[str, list[dict]] = {}
    out = []
    for key in state.pinned_recipes:
        if key in grid:
            out.append(grid[key])
            continue
        smelt = engine.parse_smeltery_recipe_key(key)
        source = engine.parse_source_pin_key(key)
        if smelt is not None:
            tile = _smeltery_pinned_tile(key, smelt, smelteries)
        elif source is not None:
            tile = _source_pinned_tile(state, key, source[0], sources_cache)
        else:
            tile = _locked_grid_pinned_tile(key)
        if tile is not None:
            out.append(tile)
    return out


def _smeltery_pinned_tile(key: str, parsed: tuple[str, str], smelteries: list[dict]) -> dict | None:
    """Рецепт плавильни (ключ engine.smeltery_recipe_key). enough - хоть одна
    построенная плавильня может выполнить его прямо сейчас."""
    kind, rid = parsed
    recipe = engine.smeltery_recipes(kind).get(rid)
    if recipe is None:
        return None
    able = [s for s in smelteries if any(r["key"] == key for r in s[f"{kind}_recipes"])]
    ready = [s for s in able if next(r for r in s[f"{kind}_recipes"] if r["key"] == key)["reason"] is None]
    types = [bid for bid in BUILDINGS if engine.is_smeltery(bid)]
    location_name = (able[0]["name"] if able else BUILDINGS[types[0]]["name"] if types else "Плавильня")
    return {
        "action_id": key, "kind": kind, "name": recipe["name"], "location_name": location_name,
        "gain": _amounts_view(recipe.get("output") or {}), "enough": bool(ready), "pinned": True,
    }


def _locked_grid_pinned_tile(key: str) -> dict | None:
    """Сеточный рецепт, которого нет в crafting_view (не изучена технология, механизм
    не открыт) - по контенту, без реальной цены. Сырьё к нему «не хватает» заведомо."""
    kind, target_id = engine.parse_grid_recipe_key(key)
    if kind == "energy_recipe":
        recipe = ENERGY_RECIPES.get(target_id)
        location_id = engine.CONVERTER_LOCATION_ID
        gain = manual_action_gain_list(recipe.get("produces", {}), {}) if recipe else []
    elif kind == "manual_action":
        recipe = MANUAL_ACTIONS.get(target_id)
        location_id = recipe.get("location") if recipe else None
        gain = manual_action_gain_list(recipe.get("gain", {}), recipe.get("gain_chance", {})) if recipe else []
        if recipe is not None and not engine.is_grid_action(target_id):
            return None
    elif kind == "building":
        if not building_sources(target_id):
            return None
        recipe = BUILDINGS[target_id]
        location_id = recipe["location"]
        gain = [{"id": building_item_id(target_id), "name": recipe["name"], "amount": 1}]
    else:
        return None
    if recipe is None or (kind == "energy_recipe" and not recipe.get("pattern")):
        return None
    return {
        "action_id": key, "kind": kind, "name": recipe["name"], "location_id": location_id,
        "location_name": LOCATIONS.get(location_id, {}).get("name", location_id),
        "gain": gain, "enough": False, "pinned": True, "locked": True,
    }


# Виды источников, у которых cost - разовая цена за раз (точка «хватает сырья» на
# плитке). У постройки cost - это consumes за ход, у остальных цены нет вовсе.
_SOURCE_PIN_COST_KINDS = {"manual", "spawn", "energy", "ritual", "replicator"}


def _source_pinned_tile(state: GameState, key: str, resource_id: str, cache: dict) -> dict | None:
    """Любой другой способ из справочника (ключ engine.source_pin_key)."""
    if resource_id not in RESOURCE_NAMES:
        return None
    if resource_id not in cache:
        cache[resource_id] = resource_sources(resource_id)
    src = next((s for s in cache[resource_id] if s["pin_key"] == key), None)
    if src is None:
        return None
    enough = None
    if src["kind"] in _SOURCE_PIN_COST_KINDS and src.get("cost"):
        enough = all(state.resources.get(c["id"], 0) >= c["amount"] for c in src["cost"])
    return {
        "action_id": key, "kind": "source", "source_kind": src["kind"], "name": src["name"],
        "location_name": None, "gain": [{"id": resource_id, "name": RESOURCE_NAMES[resource_id]}],
        "enough": enough, "pinned": True,
    }


def _automation_target_name(kind: str, target_id: str) -> str:
    names = {"manual_action": MANUAL_ACTIONS, "energy_recipe": ENERGY_RECIPES, "ritual_recipe": RITUAL_RECIPES}.get(kind, {})
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
    RECIPE_BOARD_BUILDING_ID и у ресурса есть рецепт.

    "capacity": None означает ДВЕ разные вещи с одинаковым видом в JSON - у
    ресурса либо вообще нет storage (тогда max_tier == 0), либо storage есть, но
    куплен infinite-тир (см. engine.storage_capacity) - панель их и не различает,
    рисует как одно и то же "без ограничений". "next_infinite" - следующий ещё не
    купленный тир снимет ограничение СОВСЕМ вместо прибавки next_capacity_add
    (тогда next_capacity_add может быть None - см. content_editor: чекбокс
    "Делает вместимость безграничной")."""
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
            "next_capacity_add": next_info.get("capacity_add") if next_info else None,
            "next_infinite": bool(next_info and next_info.get("infinite")),
            "next_cost": cost_list(next_info["cost"], state) if next_info else None,
            "next_tech_name": TECHS[tech_missing]["name"] if tech_missing else None,
            "can_upgrade": resource_id in upgradable,
            "jump_to": jump.get(resource_id),
        })
    return items


def autocraft_view(state: GameState) -> dict:
    """Данные для панели «Заказы»: слоты заказов процессоров и сами заказы (шаги с
    прогрессом, что лежит в резерве, причина простоя, ждёт ли энергию) - см.
    engine.orders_view/_place_order."""
    orders = []
    for order in engine.orders_view(state):
        orders.append({
            **order,
            "steps": [
                {**step, "name": (
                    # заказ «рецепт преобразователя ×N» считает партии - подписываем рецептом
                    ENERGY_RECIPES[step["batches_of"]]["name"] if step.get("batches_of") in ENERGY_RECIPES
                    else f"Репликация: {RESOURCE_NAMES.get(step['resource_id'], step['resource_id'])}"
                    if step["kind"] == "replicate"
                    else RESOURCE_NAMES.get(step["resource_id"], step["resource_id"])
                )}
                for step in order["steps"]
            ],
            "reserved": [
                {"id": rid, "name": RESOURCE_NAMES.get(rid, rid), "amount": amount}
                for rid, amount in order["reserved"].items()
            ],
        })
    return {
        "slot_capacity": engine.autocraft_slot_capacity(state),
        "slots_used": sum(1 for o in state.orders if o["steps"]),
        "orders": orders,
    }


def assembler_view(building_id: str) -> dict | None:
    """Сборщик (buildings.*.autocraft_assembler) - для подписи на карточке постройки."""
    spec = BUILDINGS[building_id].get("autocraft_assembler")
    if not spec:
        return None
    if "building" in spec:  # сборщик ритуалов алтаря
        altar = BUILDINGS.get(spec["building"], {})
        return {"location_name": altar.get("name", spec["building"]), "per_turn": spec.get("per_turn"), "ritual": True}
    location = LOCATIONS.get(spec.get("location"), {})
    return {"location_name": location.get("name", spec.get("location")), "per_turn": spec.get("per_turn"), "ritual": False}


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
            "autocraft": job["autocraft"],
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


def crafting_view(state: GameState) -> list[dict]:
    """Раздел на КАЖДУЮ открытую локацию-верстак (locations.*.crafting) - размер
    сетки, банк доступных ресурсов игрока и сами сеточные рецепты этой локации
    (узор + результат + РЕАЛЬНАЯ цена + признак "хватает сырья"). Считается на
    ВСЕ такие локации разом (тот же приём, что ritual_altars_view/
    reactor_instances_view), а не только на "открытую в модалке прямо сейчас" -
    сервер ничего не знает про состояние клиентской модалки.

    cost в ответе - РЕАЛЬНАЯ (effective_manual_action_stats, chosen_scale=1), а
    не буквальный узор из content.json - см. PLAN_grid_crafting.md, раздел
    "Факты о коде": апгрейды механизма (requires_building) домножают именно
    его, узор при этом определяет, КАКОЙ рецепт собран, а не сколько спишется.
    "enough" - чисто информационная подсветка (сырья хватает прямо сейчас без
    докрафта); сам крафт (см. craft_grid_view) всё равно уходит в обычный
    _pay_or_defer/автокрафт, если сырья не хватает - см. CLAUDE.md "Не ломай
    докрафт" в плане."""
    bank = []
    for rid in RESOURCE_ORDER:
        if rid == engine.ENERGY_RESOURCE_ID or rid in SPECIES:
            continue
        amount = state.resources.get(rid, 0)
        if amount <= 0:
            continue
        bank.append({"id": rid, "name": RESOURCE_NAMES[rid], "amount": amount})

    result = []
    for lid in engine.available_locations(state):
        crafting = engine.location_crafting(lid)
        if crafting is None:
            continue
        stacks = engine.location_stacks(lid)
        recipes = []
        for aid in engine.grid_actions_at(lid, state):
            action = MANUAL_ACTIONS[aid]
            cost, gain = engine.effective_manual_action_stats(aid, state, chosen_scale=1)
            recipes.append({
                # action_id - КЛЮЧ сеточного рецепта (engine.grid_recipe_key): у
                # ручного действия это сам его id, у рецепта крафта за энергию -
                # "energy_recipe:<id>" (см. _energy_grid_recipe_view), у постройки -
                # "building:<id>". Клиент сравнивает только ключи и не разбирает их.
                "action_id": aid,
                "kind": "manual_action",
                "name": action["name"],
                "pattern": action.get("pattern"),
                # Сборщик (crafting.stacks): что разложить стопками - цена из контента
                # без энергии, её же ровно сверяет engine.match_grid_target.
                "stack_cost": _stack_cost_list(action.get("cost") or {}) if stacks else None,
                "pattern_mirror": bool(action.get("pattern_mirror")),
                "cost": manual_action_cost_list(cost, action.get("cost_chance", {}), state),
                "gain": manual_action_gain_list(gain, action.get("gain_chance", {})),
                "enough": all(state.resources.get(rid, 0) >= amt for rid, amt in cost.items()),
                "pinned": aid in state.pinned_recipes,
                "sound_url": sound_url(action.get("sound")),
                # Антиавтокликерная задержка кнопки "Скрафтить" - та же, что у
                # карточки обычного ручного действия (build_view: click_delay_ms),
                # клиент подставляет ui.default_action_delay_ms, если тут None.
                "click_delay_ms": action.get("click_delay_ms"),
                # Реальная длительность (с апгрейдами action_duration_reduction) -
                # >1 значит рецепт запускает многоходовой job (прогресс-бар в
                # модалке механизма), а не отдаёт результат мгновенно.
                "duration_turns": engine.manual_action_duration(aid, state),
            })
        # Многоходовой job, занявший ЭТОТ механизм (одна задача на локацию, см.
        # CLAUDE.md "Многоходовое ручное действие") - прогресс-бар и блокировка
        # кнопки "Скрафтить" в модалке. Ищется по ВСЕМ действиям локации, не
        # только сеточным: обычная карточка-действие той же локации блокирует
        # механизм точно так же (do_manual_action).
        job = None
        for job_aid, running in state.manual_jobs.items():
            if MANUAL_ACTIONS[job_aid].get("location") == lid:
                _, job_gain = engine.effective_manual_action_stats(job_aid, state, chosen_scale=1)
                job = {
                    "action_id": job_aid,
                    "name": MANUAL_ACTIONS[job_aid]["name"],
                    "turns_done": running["turns_done"],
                    "turns_total": running["turns_total"],
                    "gain": manual_action_gain_list(job_gain, MANUAL_ACTIONS[job_aid].get("gain_chance", {})),
                }
                break
        # Преобразователь: рецепты крафта за энергию с узором и все идущие крафты
        # за энергию (несколько преобразователей ведут их параллельно, по одному
        # на копию) - первый из них показывается стрелкой, как job печи.
        recipes.extend(_energy_grid_recipe_view(rid, state) for rid in engine.grid_energy_recipes_at(lid, state))
        recipes.extend(_building_grid_recipe_view(bid, state, stacks) for bid in engine.grid_buildings_at(lid, state))
        energy_jobs = _converter_jobs_view(state) if lid == engine.CONVERTER_LOCATION_ID else []
        if job is None and energy_jobs:
            job = energy_jobs[0]
        result.append({
            "location_id": lid,
            "name": LOCATIONS[lid]["name"],
            "width": crafting["width"],
            "height": crafting["height"],
            # Сборщик: ячейка - стопка (ресурс + количество), форма не важна.
            "stacks": stacks,
            # Крафт пачкой ("Скрафтить ×N") - только у механизма с явным
            # crafting.batch (сейчас - верстак); остальным его позже дадут
            # улучшения. craft_grid_view режет times до 1 сам, не доверяя клиенту.
            "batch": bool(crafting.get("batch")),
            "bank": bank,
            "recipes": recipes,
            "job": job,
            "energy_jobs": energy_jobs,
            # Слоты преобразователей (одна копия - один крафт за раз), None у
            # остальных механизмов.
            "converter_slots": (
                {"busy": len(state.energy_jobs), "capacity": engine.energy_converter_capacity(state)}
                if lid == engine.CONVERTER_LOCATION_ID else None
            ),
            "appearance": mechanism_appearance_view(lid),
        })
    return result


def mechanism_appearance_view(location_id: str) -> dict:
    """Оформление модалки механизма (locations.*.appearance): фон-текстура сцены,
    пиксельный прогресс и направление его заливки, варианты движения и частиц
    (id - tools/mechanism_fx.js). Пустые поля - как раньше: без фона, стрелка,
    стандартные искры. Звук сюда не входит - его подставляет сервер в лог хода
    (mechanism_sound_url), как и звук рецепта."""
    look = (LOCATIONS.get(location_id) or {}).get("appearance") or {}
    return {
        "texture": look.get("texture"),
        "texture_dim": look.get("texture_dim"),
        "progress_icon": look.get("progress_icon"),
        "progress_fill": look.get("progress_fill") or "right",
        "motion": look.get("motion"),
        "particles": look.get("particles"),
    }


def mechanism_sound_url(location_id) -> str | None:
    """Звук механизма (locations.*.appearance.sound) - играет у ЛЮБОГО рецепта
    этой локации без своего звука (сеточного, крафта за энергию, постройки и
    обычной карточки действия) - то же наследование от механизма, что у
    постройки-механизма (engine.action_mechanism_building)."""
    look = (LOCATIONS.get(location_id) or {}).get("appearance") or {}
    return sound_url(look.get("sound"))


def _energy_eta_turns(state: GameState, remaining: float, per_turn) -> int | None:
    """Не меньше скольких ходов ещё копить энергию: не быстрее потолка per_turn энергии
    за ход (energy_converter_max_energy_per_turn) и не быстрее, чем её притечёт в сеть
    (свободный запас + чистый приток, engine.energy_wait_eta_turns) - нижняя оценка:
    энергию делят все преобразователи и заказы. None - нет ни потолка, ни притока."""
    remaining = max(0, remaining)
    by_cap = max(1, -(-remaining // per_turn)) if per_turn else None
    free = state.resources.get(engine.ENERGY_RESOURCE_ID, 0) - engine.energy_reserved(state)
    by_energy = engine.energy_wait_eta_turns(state, remaining - max(0, free))
    if by_cap is None:
        return by_energy or None
    return max(by_cap, by_energy or 0)


def _energy_grid_recipe_view(recipe_id: str, state: GameState) -> dict:
    """Рецепт крафта за энергию в той же форме, что сеточное действие в
    crafting_view (pattern/cost/gain/enough/pinned), плюс то, что нужно шкале
    энергии: threshold, потолок энергии за ход и оценка срока. cost - это
    consumes рецепта (именно их узор и раскладывает), энергия в cost не входит -
    она не предмет, а копится по ходам (см. _process_energy_jobs)."""
    recipe = ENERGY_RECIPES[recipe_id]
    consumes = recipe.get("consumes", {})
    machine = recipe.get("machine")
    key = engine.grid_recipe_key("energy_recipe", recipe_id)
    # Какой тип преобразователя его поведёт (тот же выбор, что у start_energy_recipe);
    # если свободного нет - потолок того типа, что вообще построен, для оценки срока.
    free_building = engine._pick_converter_building_with_free_slot(state, state.energy_jobs, machine)
    building_id = free_building or machine or next(iter(engine.energy_converter_building_types(state)), None)
    per_turn = engine.energy_converter_max_energy_per_turn(state, building_id) if building_id else None
    return {
        "action_id": key,
        "kind": "energy_recipe",
        "recipe_id": recipe_id,
        "name": recipe["name"],
        "pattern": recipe["pattern"],
        "stack_cost": None,
        "pattern_mirror": bool(recipe.get("pattern_mirror")),
        "cost": cost_list(consumes, state),
        "gain": manual_action_gain_list(recipe.get("produces", {}), {}),
        "enough": all(state.resources.get(rid, 0) >= amt for rid, amt in consumes.items()),
        "pinned": key in state.pinned_recipes,
        "sound_url": None,
        "click_delay_ms": None,
        "duration_turns": None,
        "threshold": recipe["threshold"],
        "energy_per_turn": per_turn,
        "eta_turns": _energy_eta_turns(state, recipe["threshold"], per_turn),
        # Свободный преобразователь подходящего типа есть - можно запустить ещё
        # один крафт (даже если этот рецепт уже идёт в другом преобразователе).
        "available": free_building is not None,
        "machine_name": BUILDINGS[machine]["name"] if machine else None,
    }


def _stack_cost_list(cost: dict) -> list[dict]:
    """Стопки для раскладки в сборщике (engine.stack_recipe_cost) - список для клиента."""
    return [{"id": rid, "name": RESOURCE_NAMES[rid], "amount": amt} for rid, amt in engine.stack_recipe_cost(cost).items()]


def building_item_id(building_id: str) -> str:
    """Id постройки там, где клиент ждёт id ресурса: выход рецепта в модалке механизма,
    строка и карточка справочника, закреплённая плитка. Совпадает с ключом рецепта
    (engine.grid_recipe_key("building", ...)) - это разные пространства (ресурсы и
    рецепты), совпадение безвредно. В state.resources такого id не бывает никогда."""
    return engine.grid_recipe_key("building", building_id)


def _building_grid_recipe_view(building_id: str, state: GameState, stacks: bool) -> dict:
    """Постройка, собираемая в механизме (buildings.*.location), в форме сеточного
    рецепта crafting_view: крафт сразу ставит постройку (engine.build), выходом служит
    сама постройка (building_item_id). cost - цена текущего этапа у многоэтапной."""
    b = BUILDINGS[building_id]
    cost = engine.building_recipe_cost(building_id, state)
    stage = engine.current_construction_stage(building_id, state)
    owned = state.buildings.get(building_id, 0)
    max_count = b.get("max_count")
    return {
        "action_id": engine.grid_recipe_key("building", building_id),
        "kind": "building",
        "building_id": building_id,
        "name": b["name"],
        "pattern": None if stacks else b.get("pattern"),
        "stack_cost": _stack_cost_list(cost) if stacks else None,
        "pattern_mirror": bool(b.get("pattern_mirror")),
        "cost": cost_list(cost, state),
        "gain": [{"id": building_item_id(building_id), "name": b["name"], "amount": 1}],
        "enough": all(state.resources.get(rid, 0) >= amt for rid, amt in cost.items()),
        "pinned": engine.grid_recipe_key("building", building_id) in state.pinned_recipes,
        "sound_url": None,
        "click_delay_ms": b.get("click_delay_ms"),
        "duration_turns": None,
        "owned": owned,
        "max_count": max_count,
        # Лимит достигнут - раскладка узнаётся, но «Построить» гаснет (build() и сам откажет).
        "limit_reached": max_count is not None and owned >= max_count,
        "stage_number": stage["number"] if stage else None,
        "stage_total": stage["total"] if stage else None,
        "stage_description": stage["description"] if stage else None,
    }


def _converter_jobs_view(state: GameState) -> list[dict]:
    """Идущие крафты за энергию в форме job'а модалки механизма: action_id - ключ
    рецепта (grid_recipe_key), index - позиция в state.energy_jobs для
    cancel_energy_job, progress/threshold - накопленная энергия."""
    jobs = []
    for job in engine.energy_jobs_view(state):
        building_id = job["building_id"]
        per_turn = engine.energy_converter_max_energy_per_turn(state, building_id) if building_id else None
        jobs.append({
            "kind": "energy_recipe",
            "action_id": engine.grid_recipe_key("energy_recipe", job["recipe_id"]),
            "index": job["index"],
            "name": job["name"],
            "progress": job["progress"],
            "threshold": job["threshold"],
            "energy_per_turn": per_turn,
            "eta_turns": _energy_eta_turns(state, job["threshold"] - job["progress"], per_turn),
            "gain": manual_action_gain_list(job["produces"], {}),
            "building_name": BUILDINGS[building_id]["name"] if building_id else None,
        })
    return jobs


# ---------- Справочник источников ("Откуда берётся", см. PLAN_grid_crafting.md, этап 5) ----------
# Всё в этом разделе - ЧИСТО по контенту (не по state) намеренно: справочник
# отвечает на вопрос "как ВООБЩЕ получить этот ресурс", а не "чем игрок уже
# владеет прямо сейчас" - то же решение, что recipe_board_target в engine.py
# делает НАОБОРОТ (см. его комментарий) для другой задачи (навигация только по
# уже доступному). Поэтому ничего здесь не читает state.techs/state.buildings.

def _amount_display(amount):
    """Число или диапазон {"min","max"} (см. CLAUDE.md "Случайное количество
    produce") - справочник показывает оба вида честно, не притворяясь точным
    числом там, где сам движок его не знает заранее."""
    if isinstance(amount, dict):
        return f"{amount['min']}–{amount['max']}"
    return amount


def _plain_cost_list(cost: dict) -> list[dict]:
    """Как cost_list(cost, state), но без jump_to - клик по ингредиенту в
    карточке источника ведёт к ЕГО СОБСТВЕННЫМ источникам (см.
    web_ui.html: openSourceDetail), а не к карточке действия, поэтому
    навигация jump_to тут не нужна и была бы только лишним полем."""
    return [{"id": rid, "name": RESOURCE_NAMES[rid], "amount": amt} for rid, amt in cost.items()]


def _farm_building_for_species(species_id: str) -> str | None:
    """Постройка-ферма (breeding_slots > 0), которая расселяет эту особь - см.
    CLAUDE.md "Разведение существ": совпадение по семейству (species.*.family +
    buildings.*.breeding_accepts_family), либо ЛЮБАЯ ферма без ограничения по
    семейству вовсе. Первая подходящая по порядку content.json - справочнику
    этого достаточно, полный список ферм игрок и так видит в панели построек."""
    family = SPECIES.get(species_id, {}).get("family")
    for bid, b in BUILDINGS.items():
        if not b.get("breeding_slots"):
            continue
        accepts = b.get("breeding_accepts_family")
        if accepts is None or accepts == family:
            return bid
    return None


def _soul_collector_building_for(soul_id: str) -> str | None:
    kind = engine.soul_kind(soul_id)
    for bid, b in BUILDINGS.items():
        config = b.get("soul_collector")
        if config and config.get("accepts_kind") == kind:
            return bid
    return None


def _breeding_station_building() -> str | None:
    """Постройка-механизм скрещивания (buildings.*.breeding_station) - см.
    CLAUDE.md "Разведение существ": одна такая на всю игру, сама ферма
    (пасека) в скрещивании не участвует. Используется, когда мутация не
    привязана к конкретной постройке (mutations[].requires_building == null -
    подходит ЛЮБАЯ станция)."""
    for bid, b in BUILDINGS.items():
        if b.get("breeding_station"):
            return bid
    return None


def resource_sources(resource_id: str) -> list[dict]:
    """Все известные контенту способы получить resource_id - один элемент на
    каждый источник, по видам из PLAN_grid_crafting.md (этап 5): сеточный
    крафт и обычное ручное действие (manual_actions), постройка (buildings.
    produces), особь на ферме (species.produces), душа в синтезаторе
    (soul_recipes), крафт за энергию (energy_recipes), ритуал
    (ritual_recipes), репликатор (resources.*.replication_cost), шахта
    (mining_info), лут/душа с моба (mobs.loot/soul_id),
    приз лутбокса (loot_cases.prizes, с именем квеста-награды, если есть).
    resource_id вида building_item_id(...) - постройка: её единственный способ -
    рецепт в механизме (building_sources)."""
    if resource_id.startswith(engine.BUILDING_KEY_PREFIX):
        return building_sources(resource_id[len(engine.BUILDING_KEY_PREFIX):])
    sources: list[dict] = []

    for aid, action in MANUAL_ACTIONS.items():
        amount = (action.get("gain") or {}).get(resource_id)
        if not amount:
            continue
        pattern = action.get("pattern")
        grid = engine.is_grid_action(aid)
        entry = {
            "kind": "grid_craft" if grid else "manual",
            "name": action["name"], "action_id": aid, "amount": _amount_display(amount),
            "cost": _plain_cost_list(action.get("cost", {})),
        }
        chance = (action.get("gain_chance") or {}).get(resource_id)
        if chance is not None and chance < 1:
            entry["chance"] = chance
        if grid:
            location_id = action.get("location")
            entry.update(_grid_source_place(location_id))
            # Сам узор - карточка рецепта рисует его сеткой (игрок должен видеть
            # целевую раскладку, а не только список ингредиентов). Размер rows =
            # размер сетки механизма (валидатор: check_action_pattern). В сборщике
            # узора нет - карточка рисует стопки (stack_cost).
            entry["pattern"] = pattern
            entry["pattern_mirror"] = bool(action.get("pattern_mirror"))
            if entry["stacks"]:
                entry["stack_cost"] = _stack_cost_list(action.get("cost") or {})
        sources.append(entry)

    # spawn_species - генетически НЕЗАВИСИМОЕ от gain поле (см. CLAUDE.md,
    # do_manual_action): ловля дикой особи, отдельный проход по MANUAL_ACTIONS,
    # а не ветка внутри цикла выше - id вида не копится в state.resources как
    # обычный ресурс, поэтому пересечение с проверкой gain там было бы лишним.
    for aid, action in MANUAL_ACTIONS.items():
        if action.get("spawn_species") != resource_id:
            continue
        sources.append({
            "kind": "spawn", "name": action["name"], "action_id": aid,
            "location_id": action.get("location"), "amount": 1,
            "cost": _plain_cost_list(action.get("cost", {})),
        })

    for mutation in MUTATIONS:
        for outcome in mutation.get("outcomes", []):
            if outcome.get("species_id") != resource_id:
                continue
            building_id = mutation.get("requires_building") or _breeding_station_building()
            entry = {
                "kind": "breeding",
                "name": f"Скрещивание: {RESOURCE_NAMES[mutation['parent_a']]} × {RESOURCE_NAMES[mutation['parent_b']]}",
                "building_id": building_id, "amount": 1,
                "pin_ident": f"{mutation['parent_a']}+{mutation['parent_b']}",
            }
            chance = mutation.get("chance")
            if chance is not None and chance < 1:
                entry["chance"] = chance
            sources.append(entry)

    for bid, b in BUILDINGS.items():
        amount = (b.get("produces") or {}).get(resource_id)
        if not amount:
            continue
        sources.append({
            "kind": "building", "name": b["name"], "building_id": bid,
            "amount": _amount_display(amount), "cost": _plain_cost_list(b.get("consumes", {})),
        })

    for sid, sp in SPECIES.items():
        amount = (sp.get("produces") or {}).get(resource_id)
        if not amount:
            continue
        farm_id = _farm_building_for_species(sid)
        farm_name = BUILDINGS.get(farm_id, {}).get("name", "Ферма")
        sources.append({
            "kind": "creature", "name": f"{farm_name}: {RESOURCE_NAMES[sid]}",
            "building_id": farm_id, "species_id": sid, "amount": _amount_display(amount),
        })

    for soul_id, recipe in SOUL_RECIPES.items():
        amount = (recipe.get("produces") or {}).get(resource_id)
        if not amount:
            continue
        building_id = _soul_collector_building_for(soul_id)
        building_name = BUILDINGS.get(building_id, {}).get("name", "Синтезатор душ")
        entry = {
            "kind": "soul", "name": f"{building_name}: {RESOURCE_NAMES[soul_id]}",
            "building_id": building_id, "soul_id": soul_id, "amount": _amount_display(amount),
        }
        chance = (recipe.get("produces_chance") or {}).get(resource_id)
        if chance is not None and chance < 1:
            entry["chance"] = chance
        sources.append(entry)

    for rid, recipe in ENERGY_RECIPES.items():
        amount = (recipe.get("produces") or {}).get(resource_id)
        if not amount:
            continue
        entry = {
            "kind": "energy", "name": recipe["name"], "action_id": rid,
            "amount": _amount_display(amount), "cost": _plain_cost_list(recipe.get("consumes", {})),
            "threshold": recipe.get("threshold"),
        }
        pattern = recipe.get("pattern")
        converter_crafting = LOCATIONS.get(engine.CONVERTER_LOCATION_ID, {}).get("crafting")
        if pattern and converter_crafting:
            # Собирается в сетке преобразователя - та же карточка, что у рецепта
            # печи (узор + "Разложить узор"), action_id - ключ сеточного рецепта.
            entry.update({
                "kind": "grid_craft",
                "action_id": engine.grid_recipe_key("energy_recipe", rid),
                "location_id": engine.CONVERTER_LOCATION_ID,
                "location_name": LOCATIONS[engine.CONVERTER_LOCATION_ID]["name"],
                "pattern": pattern,
                "pattern_mirror": bool(recipe.get("pattern_mirror")),
            })
        sources.append(entry)

    for rid, recipe in RITUAL_RECIPES.items():
        amount = (recipe.get("produces") or {}).get(resource_id)
        if not amount:
            continue
        entry = {
            "kind": "ritual", "name": recipe["name"], "action_id": rid,
            "building_id": recipe.get("requires_building"), "amount": _amount_display(amount),
            # кольцо + центр + разовая цена - всё, что списывается при запуске
            "cost": _plain_cost_list(engine._ritual_full_cost(recipe)),
        }
        chance = (recipe.get("produces_chance") or {}).get(resource_id)
        if chance is not None and chance < 1:
            entry["chance"] = chance
        sources.append(entry)

    # Плавильня (buildings.*.smeltery): плавка (сырьё -> металл/шлак в тигель), сплав
    # (металлы тигля -> металл), литьё (металл тигля + форма -> изделие на склад).
    # action_id - ключ engine.smeltery_recipe_key (закрепление звездой); heat - нужный
    # жар: клиент сам выберет построенную плавильню, которой его хватит (VIEW.smelteries).
    smeltery_types = [bid for bid in BUILDINGS if engine.is_smeltery(bid)]

    def _able_smelteries(heat: int) -> list[str]:
        return [bid for bid in smeltery_types if BUILDINGS[bid]["smeltery"].get("heat", 1) >= heat]

    for rid, recipe in MELT_RECIPES.items():
        amount = (recipe.get("output") or {}).get(resource_id)
        if not amount:
            continue
        sources.append({
            "kind": "smelt", "name": recipe["name"], "action_id": engine.smeltery_recipe_key("melt", rid),
            "recipe_id": rid, "amount": amount, "cost": _plain_cost_list(recipe.get("input", {})),
            "outputs": _amounts_view(recipe.get("output", {})), "main_id": engine.melt_main_input(recipe),
            "heat": recipe.get("heat", 1), "turns": recipe.get("turns", 1),
            "building_ids": _able_smelteries(recipe.get("heat", 1)),
        })
    for rid, recipe in ALLOY_RECIPES.items():
        amount = (recipe.get("output") or {}).get(resource_id)
        if not amount:
            continue
        sources.append({
            "kind": "alloy", "name": recipe["name"], "action_id": engine.smeltery_recipe_key("alloy", rid),
            "recipe_id": rid, "amount": amount, "cost": _plain_cost_list(recipe.get("input", {})),
            "heat": recipe.get("heat", 1), "hidden": bool(recipe.get("hidden")),
            "building_ids": _able_smelteries(recipe.get("heat", 1)),
        })
    for rid, recipe in CAST_RECIPES.items():
        amount = (recipe.get("output") or {}).get(resource_id)
        if not amount:
            continue
        form = recipe.get("form")
        sources.append({
            "kind": "cast", "name": recipe["name"], "action_id": engine.smeltery_recipe_key("cast", rid),
            "recipe_id": rid, "amount": amount,
            "cost": _plain_cost_list({recipe["metal"]: recipe.get("amount", 1)}),
            "form": {"id": form, "name": RESOURCE_NAMES.get(form, form)} if form else None,
            "metal_id": recipe["metal"], "turns": recipe.get("turns", 1), "building_ids": smeltery_types,
        })

    # Репликатор: любой ресурс с ценой репликации (resources.*.replication_cost) -
    # одна запись на КАЖДЫЙ тип репликатора; action_id - ключ заказа «Репликация ×N»
    # (engine.grid_recipe_key("replicator", ...)), amount/cost - за ОДНУ единицу.
    price = engine.RESOURCE_REPLICATION_COST.get(resource_id)
    if price:
        for bid, b in BUILDINGS.items():
            matter = (b.get("replicator") or {}).get("matter")
            if not matter or matter == resource_id or matter not in RESOURCE_NAMES:
                continue
            sources.append({
                "kind": "replicator", "name": b["name"], "building_id": bid,
                "action_id": engine.grid_recipe_key("replicator", resource_id),
                "amount": 1, "cost": _plain_cost_list({matter: price}),
            })

    mining = MINING_INFO.get(resource_id)
    if mining:
        sources.append({"kind": "mining", "name": "Шахта", "amount": mining.get("base_yield", 1)})

    for mid, mob in MOBS.items():
        loot_amount = (mob.get("loot") or {}).get(resource_id)
        if loot_amount:
            entry = {"kind": "loot", "name": f"Охота: {mob['name']}", "mob_id": mid, "amount": loot_amount}
            chance = (mob.get("loot_chance") or {}).get(resource_id)
            if chance is not None and chance < 1:
                entry["chance"] = chance
            sources.append(entry)
        if mob.get("soul_id") == resource_id:
            sources.append({
                "kind": "loot", "name": f"Охота: {mob['name']} (душа)", "mob_id": mid,
                "amount": 1, "chance": mob.get("soul_chance", 1.0), "pin_ident": f"{mid}+soul",
            })

    for cid, case in LOOT_CASES.items():
        matched = next((p for p in case.get("prizes", []) if p.get("resource") == resource_id), None)
        if not matched:
            continue
        quest_name = next((q["name"] for q in QUESTS.values() if q.get("reward_case") == cid), None)
        name = f"Лутбокс «{case['name']}»"
        if quest_name:
            name += f" (награда за квест «{quest_name}»)"
        sources.append({"kind": "lootbox", "name": name, "case_id": cid, "amount": matched.get("amount", 1)})

    for src in sources:
        src["pin_key"] = _source_pin_key(resource_id, src)
    return sources


def _grid_source_place(location_id) -> dict:
    """Где собирается сеточный рецепт справочника: механизм, его сетка и режим
    (stacks - сборщик: карточка рисует стопки вместо узора)."""
    crafting = (LOCATIONS.get(location_id) or {}).get("crafting") or {}
    return {
        "location_id": location_id,
        "location_name": LOCATIONS.get(location_id, {}).get("name", location_id),
        "stacks": engine.location_stacks(location_id),
        "grid_width": crafting.get("width"),
        "grid_height": crafting.get("height"),
    }


def building_sources(building_id: str) -> list[dict]:
    """Справочник для постройки (id building_item_id): рецепт в её механизме
    (buildings.*.location) - узор или стопки сборщика. Пусто, если собрать её негде.
    Цена многоэтапной - первого этапа (справочник по контенту, не по state);
    «Разложить» берёт реальный текущий этап из crafting_view."""
    b = BUILDINGS.get(building_id)
    location_id = b.get("location") if b else None
    if location_id not in LOCATIONS or engine.location_crafting(location_id) is None:
        return []
    place = _grid_source_place(location_id)
    if not place["stacks"] and not b.get("pattern"):
        return []
    stages = engine.building_construction_stages(building_id)
    cost = stages[0]["cost"] if stages else b.get("cost") or {}
    key = engine.grid_recipe_key("building", building_id)
    entry = {
        "kind": "grid_craft", "name": b["name"], "action_id": key, "building_recipe": True,
        "amount": 1, "cost": _plain_cost_list(cost), **place,
        "pattern": None if place["stacks"] else b.get("pattern"),
        "pattern_mirror": bool(b.get("pattern_mirror")),
        "stage_total": len(stages) if stages else None,
        "pin_key": key,
    }
    if place["stacks"]:
        entry["stack_cost"] = _stack_cost_list(cost)
    return [entry]


_SMELTERY_SOURCE_KINDS = ("smelt", "alloy", "cast")
# Поле записи resource_sources, которое отличает способ от других того же вида
# (кроме сеточного крафта/плавильни - у них свой ключ, см. _source_pin_key).
_SOURCE_PIN_IDENT_FIELD = {
    "manual": "action_id", "spawn": "action_id", "energy": "action_id", "ritual": "action_id",
    "building": "building_id", "replicator": "building_id", "creature": "species_id",
    "soul": "soul_id", "loot": "mob_id", "lootbox": "case_id",
}


def _source_pin_key(resource_id: str, src: dict) -> str:
    """Ключ закрепления способа (звезда в карточке, state.pinned_recipes). Сеточный
    крафт и плавильня - прежние ключи рецептов (их знают crafting_view/сохранения),
    всё остальное - engine.source_pin_key: ресурс + вид + id способа."""
    if src["kind"] == "grid_craft" or src["kind"] in _SMELTERY_SOURCE_KINDS:
        return src["action_id"]
    ident = src.pop("pin_ident", None) or src.get(_SOURCE_PIN_IDENT_FIELD.get(src["kind"], "")) or src["kind"]
    return engine.source_pin_key(resource_id, src["kind"], str(ident))


def sources_summary_view() -> list[dict]:
    """Один элемент на КАЖДЫЙ ресурс, у которого есть хотя бы один известный
    источник - список для поиска в панели "Справочник источников" (см.
    web_ui.html: openSourcesModal/renderSourcesList). Считается по запросу
    (отдельный эндпоинт /api/sources_summary), а не на каждый build_view -
    незачем пересчитывать это на КАЖДЫЙ клик игрока, справочник по контенту
    не меняется чаще, чем открывается его же панель."""
    items = []
    for rid in RESOURCE_ORDER:
        srcs = resource_sources(rid)
        if not srcs:
            continue
        items.append({"id": rid, "name": RESOURCE_NAMES[rid], "kinds": sorted({s["kind"] for s in srcs})})
    # Постройки - после ресурсов: их тоже ищут в справочнике («как собрать печь»).
    for bid, b in BUILDINGS.items():
        if building_sources(bid):
            items.append({"id": building_item_id(bid), "name": b["name"], "kinds": ["grid_craft"], "building": True})
    return items


def is_known_source_id(resource_id) -> bool:
    """Id, который понимает справочник: ресурс или постройка (building_item_id)."""
    if not isinstance(resource_id, str):
        return False
    if resource_id.startswith(engine.BUILDING_KEY_PREFIX):
        return resource_id[len(engine.BUILDING_KEY_PREFIX):] in BUILDINGS
    return resource_id in RESOURCE_NAMES


def _location_opens_as_button(location_id: str) -> bool:
    """Локация-механизм (сетка крафта или преобразователь), в которой нет НИЧЕГО,
    кроме самого механизма, - в игре это не "комната", куда заходят, а кнопка
    «Открыть: ...» прямо в родительской локации (например, печь в «Механизмах»).
    Считается по КОНТЕНТУ, а не по тому, что открыто игроку сейчас: иначе локация
    переключалась бы между кнопкой и комнатой, когда внутри открывается ещё одно
    действие. Обычное действие без узора (верстак: «Набрать воды») или дочерняя
    локация оставляют её комнатой - у кнопки им негде было бы показаться."""
    if not (engine.location_crafting(location_id) or location_id == engine.CONVERTER_LOCATION_ID):
        return False
    if any(l.get("parent_location") == location_id for l in LOCATIONS.values()):
        return False
    return not any(
        a.get("location") == location_id and not engine.is_grid_action(aid)
        for aid, a in MANUAL_ACTIONS.items()
    )


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
            "autocraft_slots": b.get("autocraft_slots", 0),
            "autocraft_assembler": assembler_view(bid),
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
            # апгрейды КАЖДОГО экземпляра фермы этого типа. То же самое (2026-09-18)
            # теперь и у синтезаторов душ (soul_collector) - их апгрейды переехали
            # на per-instance (см. soul_collector_instance_upgrades_view), кнопка
            # "⚙ Улучшения" тут для них тоже больше не показывается - апгрейды
            # покупаются в самой модалке "Души" (is_soul_collector ниже).
            "upgrades": None if b.get("breeding_slots") or b.get("soul_collector") or b.get("reactor") else building_upgrades_view(bid, state),
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
            # Кнопка "Ритуалы" на карточке - открывает openRitualCircleModal ЭТОЙ
            # built-постройки (VIEW.ritual_altars ниже), тот же приём, что
            # is_soul_collector.
            "is_ritual_circle": bool(b.get("ritual_circle")),
            # Кнопка "Реактор" на карточке - открывает openReactorModal ЭТОЙ
            # built-постройки (VIEW.reactors ниже), тот же приём, что
            # is_soul_collector - апгрейды реактора тоже per-instance (см.
            # upgrades=None выше), покупаются в самой модалке, не через общую
            # "⚙ Улучшения".
            "is_reactor": bool(b.get("reactor")),
            # Кнопка "Репликатор" на карточке - открывает openReplicatorModal
            # (VIEW.replicator ниже), тот же приём, что is_ritual_circle.
            "is_replicator": bool(b.get("replicator")),
            # Кнопка "Плавильня" на карточке - openSmelteryModal ЭТОЙ постройки
            # (VIEW.smelteries ниже), тот же приём, что is_ritual_circle.
            "is_smeltery": bool(b.get("smeltery")),
        })

    manual = []
    for aid in engine.available_manual_actions(state):
        # Сеточное действие (manual_actions.*.pattern) больше не карточка -
        # собирается ТОЛЬКО раскладыванием на сетке верстака (см. VIEW.crafting/
        # crafting_view, CLAUDE.md "Крафт только через раскладывание"). Сам
        # engine.do_manual_action его по-прежнему выполняет как обычно - границу
        # проводит именно это представление, автоматизация и докрафт не задеты.
        if engine.is_grid_action(aid):
            continue
        action = MANUAL_ACTIONS[aid]
        # chosen_scale=1 - cost/gain РОВНО за одну "партию" ("Ползунок масштаба
        # ручного действия", см. CLAUDE.md), а не за купленный максимум, как было
        # раньше (effective_manual_action_stats(aid, state) без chosen_scale).
        # Фронтенд сам домножает эти "единичные" числа на текущее положение
        # ползунка (1..scale_cap) - чистая арифметика, без похода на сервер за
        # каждое движение ползунка. Для действия без requires_building или без
        # купленного scale-апгрейда это ЭКВИВАЛЕНТНО прежнему поведению
        # (scale_cap == 1 всегда, домножение на 1 - no-op).
        building_id = engine.action_mechanism_building(action)
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
            "crafting": engine.location_crafting(lid),
            "as_button": _location_opens_as_button(lid),
            "shows_building_mechanisms": bool(LOCATIONS[lid].get("shows_building_mechanisms")),
        }
        for lid in engine.available_locations(state)
    ]
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
        elif not engine.is_tech_available(tid, state):
            status = "locked"
        elif tid not in state.solved_hypotheses:
            status = "hypothesis_needed"
        else:
            status = "available"
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

    smelteries = smelteries_view(state)
    crafting = crafting_view(state)
    return {
        "turn": state.turn,
        "save_name": state.save_name,
        "time": time_info,
        "resources": state.resources,
        "pinned": state.pinned,
        # Закреплённые сеточные рецепты целиком, включая ещё не открытые игроку
        # (их нет в crafting[].recipes) - звезда в карточке справочника должна
        # показывать ★ и у такого рецепта, иначе повторный клик тихо откреплял бы.
        "pinned_recipes": list(state.pinned_recipes),
        "resource_names": RESOURCE_NAMES,
        # Имена построек для id building_item_id - выход рецепта постройки в модалке
        # механизма и справочник подписывают их тем же RESOURCE_NAME_LOOKUP.
        "building_names": {bid: b["name"] for bid, b in BUILDINGS.items()},
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
            "mine_hit": sound_url(UI_SOUNDS.get("mine_hit")),
        },
        "rates": rates,
        "energy": {"stock": stock, "capacity": capacity, "generation": gen, "consumption": cons},
        # id энергии для клиента (энергия в цене сеточного рецепта сверх узора) -
        # чтобы web_ui.html не держал ещё одну ручную копию хардкод-id.
        "energy_resource_id": engine.ENERGY_RESOURCE_ID,
        "compute_capacity": engine.compute_capacity(state),
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
        "reactors": reactor_instances_view(state),
        "reactor_bank": reactor_component_bank_view(state),
        "reactor_catalyst_bank": reactor_catalyst_bank_view(state),
        "reactor_fluid_bank": reactor_coolant_fluid_bank_view(state),
        "ritual_altars": ritual_altars_view(state),
        "replicator": replicator_view(state, rates),
        "smelteries": smelteries,
        # Плитки левой колонки «Закреплённые» - любой закреплённый способ (сеточный
        # рецепт, плавильня, ритуал, постройка...), см. web_ui.html: globalPinnedRecipes.
        "pinned_tiles": pinned_recipes_view(state, crafting, smelteries),
        # Цвет/плотность жидких металлов (resources.*.molten) - слои тигля и чипы металлов.
        "molten": RESOURCE_MOLTEN,
        "discovered_alloys": sorted(state.discovered_alloys),
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
        "crafting": crafting,
        "tools": tools,
        "enchanting": enchanting_view(state),
        "hunting": hunting,
        "skills": engine.skills_view(state),
        "storage": storage_view(state),
        "techs_researched": len(state.techs),
        "techs_total": len(TECHS),
        "categories": {
            "manual": manual, "tech": tech,
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


def dispatch_action(state: GameState, action_type: str, action_id, scale: int | None = None, qte_hit: bool = False):
    if action_type == "manual":
        return engine.do_manual_action(state, action_id, scale=scale)
    if action_type == "order":
        # action_id - составной "action_id:qty" (кнопка «Заказать ×N» в карточке рецепта).
        recipe_id, _, qty = str(action_id).rpartition(":")
        return engine.order_recipe(state, recipe_id, int(qty or 1))
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
        # qte_hit - игрок попал в QTE-кольцо (web_ui.html: huntTryMove) - бонус к урону
        # combat.qte_damage_bonus, см. engine._move_damage.
        return engine.attack_encounter(state, move_id=action_id, qte_hit=qte_hit)
    if action_type == "summon_boss":
        return engine.summon_boss(state, action_id)
    if action_type == "upgrade_tool":
        return engine.upgrade_tool(state, action_id)
    if action_type == "storage_upgrade":
        return engine.upgrade_storage(state, action_id)
    if action_type == "start_energy_recipe":
        return engine.start_energy_recipe(state, action_id)
    if action_type == "start_ritual":
        return engine.start_ritual(state, action_id)
    if action_type == "replicate":
        # action_id - составной "resource_id:qty" (модалка репликатора), как у "order".
        resource_id, _, qty = str(action_id).rpartition(":")
        return engine.replicate(state, resource_id, int(qty or 1))
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
    if action_type in ("smelt_load", "smelt_alloy", "smelt_pour"):
        # action_id - составной "building_id:recipe_id" (модалка плавильни): у каждой
        # построенной плавильни свой тигель, рецепт без постройки не адресуем.
        building_id, _, recipe_id = str(action_id).partition(":")
        if action_type == "smelt_load":
            return engine.load_melt(state, building_id, recipe_id)
        if action_type == "smelt_alloy":
            return engine.alloy_smeltery(state, building_id, recipe_id)
        return engine.pour_cast(state, building_id, recipe_id)
    raise ValueError(f"неизвестный тип действия: {action_type}")


def _smeltery_event_view(event: dict) -> dict:
    building = BUILDINGS.get(event["building_id"], {}).get("name", event["building_id"])
    kind = event["kind"]
    output = event.get("output") or {}
    if kind == "melted":
        name = MELT_RECIPES.get(event["recipe_id"], {}).get("name", event["recipe_id"])
        message = f"{building}: расплавлено ({name}) - " + ", ".join(
            f"{RESOURCE_NAMES.get(r, r)} +{a}" for r, a in output.items()
        )
    elif kind == "cast_done":
        message = f"{building}: отливка остыла - " + ", ".join(f"{RESOURCE_NAMES.get(r, r)} +{a}" for r, a in output.items())
    else:
        name = MELT_RECIPES.get(event["recipe_id"], {}).get("name", event["recipe_id"])
        message = f"{building}: тигель полон - «{name}» ждёт места (сними шлак или залей металл)"
    return {
        "building_id": event["building_id"], "kind": kind, "slot": event.get("slot"),
        "recipe_id": event.get("recipe_id"), "output": _amounts_view(output), "message": message,
    }


def run_turn(state: GameState, action_type: str, action_id, scale: int | None = None, qte_hit: bool = False) -> dict:
    global _LAST_ENERGY_SHORTFALL
    log = {
        "message": None, "ok": True, "action_deltas": [], "production_deltas": [],
        "idle": [], "broken": [], "quests_completed": [], "autocraft_completed": [],
        "pending_finished": [], "orders_finished": [], "energy_crafted": [], "sound_url": None,
        "manual_completed": [], "manual_completed_ids": [], "energy_completed_keys": [], "combat": None, "boss_fled": None,
        "automation_fired": [], "ritual_completed": [], "breed": None,
        "reactor_accidents": [], "pending": False, "replicated": [], "smeltery": [],
    }

    if action_type != "skip":
        before = dict(state.resources)
        result = dispatch_action(state, action_type, action_id, scale=scale, qte_hit=qte_hit)
        log["message"] = result.message
        log["ok"] = result.ok
        # Действие не выполнено, а поставлено в очередь (ждёт автокрафта/энергии, см.
        # engine._pay_or_defer) - может завершиться уже в advance_turn этого же хода.
        log["pending"] = bool(result.pending)
        if action_type == "manual":
            # Резолвится сервером (а не клиентом по своей копии VIEW), чтобы
            # источником истины оставался content.json - см. manual_actions.*.sound.
            # Без своего звука - звук механизма его локации (appearance.sound).
            action = MANUAL_ACTIONS.get(action_id, {})
            log["sound_url"] = sound_url(action.get("sound")) or mechanism_sound_url(action.get("location"))
        if action_type == "start_ritual":
            # Тот же принцип, что у manual_actions.*.sound выше - звук проигрывается
            # СРАЗУ при клике (через общий doAction в web_ui.html), даже если сам
            # ритуал многоходовой и produces начислится только позже, в
            # _process_ritual_jobs - именно так уже ведут себя многоходовые
            # manual_actions (см. ritual_recipes.*.sound в CLAUDE.md).
            log["sound_url"] = sound_url(RITUAL_RECIPES.get(action_id, {}).get("sound"))
        if action_type == "hunt_attack":
            # Данные удара (урон/killed/instant_kill) для анимации на клиенте -
            # см. game/engine.py:ActionResult.combat/attack_encounter.
            log["combat"] = result.combat
        if action_type == "breed":
            # Данные для центрального визуального эффекта скрещивания (иконки
            # родителей/потомка, мутация ли) - см. game/engine.py:ActionResult.breed.
            log["breed"] = result.breed
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
    # id, а не только имена - модалка механизма (web_ui.html: openCraftGridModal)
    # сопоставляет завершившийся job со своими рецептами, чтобы проиграть эффект
    # готового результата (тот же принцип, что ritual_completed ниже).
    log["manual_completed_ids"] = list(report.manual_completed)
    log["pending_finished"] = list(report.pending_finished)
    # Готовые заказы с тем, что они выдали на склад: тост «Заказ выполнен» и отметка в
    # блоке «Заказать ×N» (web_ui.html: showOrderToast/orderDoneFlash) - заказ, готовый
    # в ход оформления, иначе исчезал бы молча. target_id заказа «рецепт ×N» - ключ
    # рецепта, тот же, что у кнопки «Заказать».
    log["orders_finished"] = [
        {
            "id": e["id"], "kind": e["kind"], "target_id": e["target_id"], "label": e["label"],
            "autocraft": e["autocraft"],
            "gained": [
                {"id": rid, "name": RESOURCE_NAMES.get(rid, rid), "amount": engine._format_amount(amount)}
                for rid, amount in e["gained"].items()
            ],
        }
        for e in report.orders_finished
    ]
    log["energy_crafted"] =[ENERGY_RECIPES[rid]["name"] for rid, count in report.crafted.items() for _ in range(count)]
    # Ключи сеточных рецептов (engine.grid_recipe_key) завершившихся крафтов за
    # энергию - для эффекта готового результата в модалке преобразователя, тот
    # же приём, что manual_completed_ids у печи.
    log["energy_completed_keys"] = [engine.grid_recipe_key("energy_recipe", rid) for rid in report.crafted]
    # Что выдали репликаторы в этот ход - модалка репликатора проигрывает по нему
    # эффект выхода (web_ui.html: openReplicatorModal), лог - строку «Репликатор».
    log["replicated"] = [
        {"resource_id": rid, "name": RESOURCE_NAMES.get(rid, rid), "amount": engine._format_amount(amount)}
        for rid, amount in report.replicated.items()
    ]
    # {id, name, building_id} и не просто имя - клиент (openRitualCircleModal) должен
    # надёжно сопоставить завершившийся ритуал с открытой модалкой конкретного алтаря,
    # чтобы проиграть финальную анимацию, даже если игрок довёл ход каким-то ДРУГИМ
    # кликом, а не самой кнопкой "Начать ритуал" (см. CLAUDE.md про многоходовые задачи).
    log["ritual_completed"] = [
        {"id": rid, "name": RITUAL_RECIPES[rid]["name"], "building_id": RITUAL_RECIPES[rid]["requires_building"]}
        for rid in report.ritual_completed if rid in RITUAL_RECIPES
    ]
    log["automation_fired"] = [
        {"building_name": BUILDINGS[e["building_id"]]["name"], "message": e["message"]}
        for e in report.automation_fired
    ]
    # События плавилен (engine._process_smelteries) - строка журнала + эффекты модалки
    # плавильни (капля в тигель из слота slot, вспышка готовой отливки): building_id -
    # чтобы открытая модалка проигрывала только свои.
    log["smeltery"] = [_smeltery_event_view(e) for e in report.smeltery_events]
    # Аварии реакторных зон, сработавшие именно в этот ход - см. game/engine.py:
    # _process_reactors/ProductionReport.reactor_accidents. instance_id даёт клиенту
    # проиграть эффект аварии ИМЕННО в открытой модалке этого экземпляра (тот же
    # принцип, что ritual_completed выше передаёт building_id для сопоставления).
    log["reactor_accidents"] = [
        {"instance_id": e["instance_id"], "building_name": BUILDINGS[e["building_id"]]["name"],
         "zone_index": e["zone_index"], "message": e["message"]}
        for e in report.reactor_accidents
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


def craft_grid_view(state: GameState, location_id, placed, times) -> dict:
    """POST /api/craft_grid - матчит раскладку игрока (engine.match_grid) и
    выполняет собранное действие "times" раз подряд (каждый раз - отдельный
    ход, см. CLAUDE.md/PLAN_grid_crafting.md "Один клик равен одному ходу"):
    крутим цикл «действие плюс advance_turn» ЗДЕСЬ, одним HTTP-запросом, а не
    отдаём это клиенту - иначе "×N" превратилось бы в N запросов и N полных
    перерисовок интерфейса. Прерывается на первой же неудаче (например,
    нехватка сырья без возможности докрафта) - "times_done" в ответе честно
    говорит, сколько итераций реально прошло.

    Раскладка перепроверяется здесь же, на сервере (см. match_grid) - клиентский
    подсчёт совпадения узора в модалке верстака только для мгновенной реакции,
    доверять ему нельзя (см. план, этап 4).

    times > 1 допустим только у механизма с crafting.batch (см. crafting_view) -
    у остальных молча режется до 1, клиентский ввод "×N" не в счёт."""
    times_requested = max(1, int(times)) if times is not None else 1
    crafting = engine.location_crafting(location_id) if location_id in LOCATIONS else None
    if not (crafting and crafting.get("batch")):
        times_requested = 1
    target = engine.match_grid_target(state, location_id, placed)
    if target is None:
        return {
            "ok": False,
            "message": "Раскладка не совпадает ни с одним открытым рецептом.",
            "action_id": None, "times_requested": times_requested, "times_done": 0,
            "logs": [],
        }
    kind, target_id = target
    # Рецепт крафта за энергию (сетка преобразователя) - обычный запуск, как
    # кнопкой-карточкой: start_energy_recipe списывает consumes и занимает
    # свободный преобразователь, энергия копится по ходам. Пачкой не бывает.
    if kind == "energy_recipe":
        times_requested = 1
        action_type = "start_energy_recipe"
    elif kind == "building":
        # Постройка: крафт сразу ставит её - тот же build(), что был у кнопки
        # каталога (оплата, этапы, max_count, докрафт через заказ).
        action_type = "build"
    else:
        action_type = "manual"
        # Многоходовой рецепт занимает механизм целиком (одна задача на локацию), так
        # что вторая итерация пачки гарантированно упёрлась бы в "уже выполняется" -
        # режем до одного запуска сразу, а не отдаём игроку "Скрафчено 1 из N".
        if engine.manual_action_duration(target_id, state) > 1:
            times_requested = 1
    action_id = engine.grid_recipe_key(kind, target_id)
    logs = []
    waiting = False
    for _ in range(times_requested):
        log = run_turn(state, action_type, target_id)
        # Постройка/крафт за энергию своего звука не имеют - звучит механизм
        # (у ручного действия run_turn уже подставил его сам).
        if not log["sound_url"]:
            log["sound_url"] = mechanism_sound_url(location_id)
        logs.append(log)
        if not log["ok"]:
            break
        # Итерация ушла в очередь (например, ждёт энергию) и не успела завершиться в
        # advance_turn этого же хода - дальше пачку не гоним: очередь держит одно
        # ожидание на действие, остальные итерации молча потерялись бы.
        if log["pending"] and any(
            o["kind"] == action_type and o["target_id"] == target_id for o in state.orders
        ):
            waiting = True
            break
    times_done = sum(1 for entry in logs if entry["ok"]) - (1 if waiting else 0)
    if waiting:
        message = logs[-1]["message"] if times_done == 0 else (
            f"Скрафчено {times_done} из {times_requested}, следующий ждёт: {logs[-1]['message']}"
        )
    elif times_done == times_requested:
        message = logs[-1]["message"]
    elif times_done == 0:
        message = logs[0]["message"]
    else:
        message = f"Скрафчено {times_done} из {times_requested}: {logs[-1]['message']}"
    return {
        "ok": times_done > 0 or waiting,
        "message": message,
        "action_id": action_id,
        "times_requested": times_requested, "times_done": times_done,
        "waiting": waiting,
        "logs": logs,
    }


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
        "energy_recipe_id": node.get("energy_recipe_id"),
        "ritual_recipe_id": node.get("ritual_recipe_id"),
        "replicator_id": node.get("replicator_id"),
        # Шаг на преобразователе - имя рецепта крафта за энергию, fires - партии;
        # ритуал - имя ритуала, fires - число ритуалов; репликация - имя
        # репликатора, fires - единицы ресурса.
        "action_name": (
            MANUAL_ACTIONS[node["action_id"]]["name"] if node["action_id"]
            else f"{ENERGY_RECIPES[node['energy_recipe_id']]['name']} (преобразователь)" if node.get("energy_recipe_id")
            else f"{RITUAL_RECIPES[node['ritual_recipe_id']]['name']} (ритуал)" if node.get("ritual_recipe_id")
            else f"{BUILDINGS[node['replicator_id']]['name']} (репликация)" if node.get("replicator_id")
            else None
        ),
        "fires": node["fires"],
        "turns": node["turns"],
        "energy_wait": bool(node.get("energy_wait")),
        # Сколько не хватает того, что автокрафт сделать не может (сырьё или рецепт ему недоступен).
        "missing": node["missing"],
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
        entry = totals.setdefault(rid, {
            "taken": 0, "needed": 0, "fires": 0, "missing": 0,
            "action_id": None, "energy_recipe_id": None, "ritual_recipe_id": None,
        })
        entry["taken"] += node["taken"]
        entry["needed"] += node["needed"]
        entry["fires"] += node["fires"]
        entry["missing"] += node["missing"]
        # Рецепт у ресурса один на всё дерево (engine.resource_recipe или преобразователь,
        # см. engine._order_route) - нужен для оценки ходов (autocraft_eta_turns/
        # converter_eta_turns).
        entry["action_id"] = entry["action_id"] or node["action_id"]
        entry["energy_recipe_id"] = entry["energy_recipe_id"] or node.get("energy_recipe_id")
        entry["ritual_recipe_id"] = entry["ritual_recipe_id"] or node.get("ritual_recipe_id")
        _collect_preview_totals(node["children"], totals)
    return totals


def _preview_crafts_anything(nodes: list[dict]) -> bool:
    return any(n["fires"] > 0 or _preview_crafts_anything(n["children"]) for n in nodes)


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
    if action_type == "start_ritual":
        return RITUAL_RECIPES[action_id]["name"] if action_id in RITUAL_RECIPES else None
    if action_type == "replicate":
        resource_id = str(action_id).rpartition(":")[0]
        return f"репликация «{RESOURCE_NAMES[resource_id]}»" if resource_id in RESOURCE_NAMES else None
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
    if action_type == "soul_instance_upgrade":
        instance_id, _, upgrade_id = action_id.partition(":")
        instance = state.soul_collector_instances.get(instance_id)
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
    либо весь cost уже покрыт запасом, либо автокрафт сейчас не при делах, либо
    докрафтить нечего, а заказ всё равно не оформится) - в этом случае фронтенд
    должен выполнить действие сразу, без модалки, и сервер сам скажет, чего не хватает.

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
    # "reason" - почему заказ не оформится (не хватает того, что автокрафт не делает,
    # мощность, слоты...): окно показывает её и не даёт нажать «Запустить».
    failure = engine.autocraft_failure(state, cost, action_type, action_id, scale)
    if failure is not None and not _preview_crafts_anything(preview["nodes"]):
        # Докрафтить нечего - подтверждать нечего: действие само ответит, чего не хватает.
        return {"needs_confirm": False}
    return {
        "needs_confirm": True,
        "label": _action_label(state, action_type, action_id),
        "reason": engine.describe_autocraft_failure(failure) if failure else None,
        # общий срок всего заказа (шаги одного механизма складываются, очередь заказов
        # учтена) - строки сводки показывают срок только своего шага
        "eta_turns": None if failure else engine.cost_eta_turns(state, cost, action_type, action_id, scale),
        **_preview_payload(state, preview),
    }


def order_preview_view(state: GameState, action_id: str, qty: int) -> dict:
    """Карточка рецепта, блок «Заказать ×N» (POST /api/order_preview, read-only): можно
    ли заказать (engine.check_order) и если нет - почему, план докрафта (то же дерево,
    что в окне подтверждения) и оценка ходов для самого рецепта. action_id - ключ
    рецепта (engine.grid_recipe_key): id действия или "energy_recipe:<id>" - рецепт
    преобразователя, тогда qty - число партий, или "ritual_recipe:<id>" - ритуал, или
    "replicator:<ресурс>" - репликация qty единиц."""
    kind, target_id = engine.parse_grid_recipe_key(action_id)
    qty = max(1, int(qty))
    if kind == "energy_recipe":
        if target_id not in ENERGY_RECIPES:
            raise KeyError("неизвестный рецепт")
        recipe = ENERGY_RECIPES[target_id]
        total = {rid: amount * qty for rid, amount in recipe.get("consumes", {}).items()}
        # Порог энергии всех партий - в сводке строкой «подождёт энергию», если её мало.
        total[engine.ENERGY_RESOURCE_ID] = total.get(engine.ENERGY_RESOURCE_ID, 0) + recipe["threshold"] * qty
        eta = engine.converter_eta_turns(state, target_id, qty)
    elif kind == "ritual_recipe":
        if target_id not in RITUAL_RECIPES:
            raise KeyError("неизвестный ритуал")
        total = {rid: amount * qty for rid, amount in engine._ritual_full_cost(RITUAL_RECIPES[target_id]).items()}
        eta = engine.ritual_eta_turns(state, target_id, qty)
    elif kind == "replicator":
        # qty - единицы ресурса; материя - у репликатора, которым пойдёт заказ
        # (автоматизированного), иначе у первого репликатора контента.
        replicators = [bid for bid, b in BUILDINGS.items() if b.get("replicator")]
        if target_id not in engine.RESOURCE_REPLICATION_COST or not replicators:
            raise KeyError("этот ресурс не реплицируется")
        matter = engine.replicator_matter(engine._autocraft_replicator_building(state) or replicators[0])
        total = {matter: engine.replication_matter_cost(target_id, qty, matter)}
        eta = engine.replicator_eta_turns(state, target_id, qty)
    elif kind == "building":
        raise KeyError("постройки не заказываются")
    else:
        if target_id not in MANUAL_ACTIONS:
            raise KeyError("неизвестный рецепт")
        cost, _ = engine.effective_manual_action_stats(target_id, state)
        total = {rid: amount * qty for rid, amount in cost.items()}
        eta = engine.autocraft_eta_turns(state, qty, target_id)
    failure = engine.check_order(state, action_id, qty)
    if failure is None:
        # Срок всего заказа: шаги в одном механизме делят его лимит, заказы впереди в
        # очереди берут его первыми (engine.order_eta_turns) - оценка одного шага выше
        # этого не видит и занижала срок.
        eta = engine.order_eta_turns(state, action_id, qty)
    preview = engine.autocraft_preview(state, total) if total else None
    payload = _preview_payload(state, preview) if preview else {"summary": [], "tree": []}
    return {
        "action_id": action_id, "qty": qty,
        "can_order": failure is None,
        "reason": engine.describe_autocraft_failure(failure) if failure else None,
        "eta_turns": eta,
        **payload,
    }


def _preview_payload(state: GameState, preview: dict) -> dict:
    """summary (по строке на ресурс) + tree (полное дерево) из engine.autocraft_preview."""
    totals = _collect_preview_totals(preview["nodes"])
    summary = []
    for rid, t in totals.items():
        stock = state.resources.get(rid, 0)
        # Энергию автокрафт не делает, а ждёт - оценка ходов по текущему притоку.
        energy_wait = rid == engine.ENERGY_RESOURCE_ID and t["needed"] > t["taken"]
        summary.append({
            "resource_id": rid, "name": RESOURCE_NAMES[rid],
            "needed": t["needed"], "taken": t["taken"], "stock": stock, "fires": t["fires"], "missing": t["missing"],
            "percent": (t["taken"] / stock) if stock > 0 else (1.0 if t["taken"] > 0 else None),
            "turns": (
                engine.energy_wait_eta_turns(state, t["needed"] - t["taken"]) if energy_wait
                else engine.converter_eta_turns(state, t["energy_recipe_id"], t["fires"]) if t["energy_recipe_id"]
                else engine.ritual_eta_turns(state, t["ritual_recipe_id"], t["fires"]) if t["ritual_recipe_id"]
                else engine.autocraft_eta_turns(state, t["fires"], t["action_id"])
            ),
            "energy_wait": energy_wait,
        })
    return {
        "summary": summary,
        "tree": [_preview_node_view(n) for n in preview["nodes"]],
    }


STATE = load_state()


# Запросы, меняющие STATE, идут по одному: снимок до запроса и откат после ошибки не
# должны перемешаться с чужим запросом (сервер многопоточный).
_STATE_LOCK = threading.RLock()
# Служебные запросы без изменения партии - без снимка и без очереди.
_UNGUARDED_POSTS = {"/api/heartbeat", "/api/shutdown"}


class Handler(BaseHTTPRequestHandler):
    _status: int | None = None

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._status = status  # после сборки тела: с этого места ответ уже отправляется
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
        elif self.path in ("/mechanism_fx.js", "/hunt_fx.js"):
            body = (MECHANISM_FX_PATH if self.path == "/mechanism_fx.js" else HUNT_FX_PATH).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            global LOAD_NOTICE
            try:
                view = build_view(STATE)
            except Exception as e:
                # Иначе страница осталась бы пустой без объяснений (см. loadState).
                traceback.print_exc()
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return
            if LOAD_NOTICE:
                view["load_notice"], LOAD_NOTICE = LOAD_NOTICE, None
            self._json(200, view)
        elif self.path == "/api/saves":
            self._json(200, {"saves": list_saves()})
        elif self.path == "/api/sources_summary":
            # Чисто по контенту, не зависит от state - см. sources_summary_view.
            self._json(200, {"items": sources_summary_view()})
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
        """Ход применяется целиком или никак: исключение посреди действия (ошибка в
        движке) или отказ-ошибка (4xx) откатывают STATE к снимку до запроса - иначе
        следующий клик продолжил бы с наполовину применённого хода и сохранил его."""
        global STATE
        if self.path in _UNGUARDED_POSTS:
            self._handle_post()
            return
        with _STATE_LOCK:
            snapshot = json.dumps(STATE.to_dict())
            try:
                self._handle_post()
            except Exception as e:
                traceback.print_exc()
                if self._status is not None:
                    return  # ответ уже ушёл (оборвалась связь при отправке) - действие состоялось
                STATE = GameState.from_dict(json.loads(snapshot))
                save_state(STATE)  # на случай, если ход успел записаться до ошибки
                message = f"Ошибка в движке - действие отменено ({type(e).__name__}: {e})."
                payload = {"error": message, "ok": False, "message": message}
                try:
                    payload["state"] = build_view(STATE)
                except Exception:
                    traceback.print_exc()
                self._json(500, payload)
                return
            if self._status is not None and self._status >= 400:
                STATE = GameState.from_dict(json.loads(snapshot))

    def _handle_post(self) -> None:
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
                log = run_turn(STATE, payload.get("type"), payload.get("id"), scale=scale, qte_hit=payload.get("qte") is True)
            except (KeyError, ValueError) as e:
                # Откат хода - в do_POST (ответ 4xx).
                self._json(400, {"error": f"Действие отменено: {type(e).__name__}: {e}"})
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
        elif self.path == "/api/order_preview":
            # Read-only, как /api/autocraft_preview: карточка рецепта спрашивает, можно ли
            # заказать ×N и что докрафтится. Сам заказ - обычный /api/action type="order".
            try:
                view = order_preview_view(STATE, payload.get("action_id"), int(payload.get("qty", 1)))
            except (KeyError, ValueError, TypeError) as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, view)
        elif self.path == "/api/pin":
            result = engine.toggle_pin(STATE, payload.get("resource_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/pin_recipe":
            key = payload.get("action_id")
            source = engine.parse_source_pin_key(key) if isinstance(key, str) else None
            # Ключ способа из справочника движок проверить не может (список способов
            # знает только resource_sources) - сверяем тут; открепить можно любой.
            if (source is not None and key not in STATE.pinned_recipes
                    and (source[0] not in RESOURCE_NAMES
                         or all(s["pin_key"] != key for s in resource_sources(source[0])))):
                result = engine.ActionResult(False, "Такого способа получить ресурс нет.")
            else:
                result = engine.toggle_pinned_recipe(STATE, key)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/craft_grid":
            try:
                times = int(payload.get("times", 1))
            except (TypeError, ValueError) as e:
                self._json(400, {"error": str(e)})
                return
            placed = payload.get("placed")
            if not isinstance(placed, list):
                self._json(400, {"error": "placed должен быть списком"})
                return
            result = craft_grid_view(STATE, payload.get("location_id"), placed, times)
            self._json(200, {**result, "state": build_view(STATE)})
        elif self.path == "/api/sources":
            # Read-only (см. resource_sources) - как /api/autocraft_preview, не
            # трогает STATE/save.json и не отдаёт "state" в ответе, за этим не
            # нужно перерисовывать всю игру.
            resource_id = payload.get("resource_id")
            if not is_known_source_id(resource_id):
                self._json(400, {"error": "неизвестный ресурс"})
                return
            self._json(200, {"resource_id": resource_id, "sources": resource_sources(resource_id)})
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
        elif self.path == "/api/demolish_building":
            # Снос одной копии постройки (см. engine.demolish_building) - без
            # возврата ресурсов, не тратит ход (тот же принцип, что и у
            # set_building_active_count выше - административное действие).
            result = engine.demolish_building(STATE, payload.get("building_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/cancel_job":
            # Отмена задачи (автокрафт/крафт за энергию/долгое ручное действие) - как
            # toggle_pin/toggle_autocraft, НЕ тратит ход (см. game/engine.py:
            # cancel_autocraft_job/cancel_energy_job/cancel_manual_job) - игрок не должен
            # ждать advance_turn, чтобы освободить слот, который сам же занял.
            kind = payload.get("kind")
            if kind == "order":
                try:
                    result = engine.cancel_order(STATE, int(payload.get("order_id")))
                except (TypeError, ValueError):
                    self._json(400, {"error": "order_id должен быть числом"})
                    return
            elif kind == "energy":
                result = engine.cancel_energy_job(STATE, payload.get("index"))
            elif kind == "manual":
                result = engine.cancel_manual_job(STATE, payload.get("action_id"))
            elif kind == "research":
                result = engine.cancel_research(STATE)
            elif kind == "ritual":
                result = engine.cancel_ritual(STATE, payload.get("building_id"))
            elif kind == "replicator":
                result = engine.cancel_replicator_job(STATE, payload.get("job_id"))
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
        elif self.path == "/api/hypothesis_puzzle":
            # Read-only генерация параметров головоломки этапа 1 - POST с JSON-
            # телом (как /api/autocraft_preview), не GET+query - в этом файле
            # все параметризованные вызовы идут через POST. Не трогает
            # STATE/save.json (см. engine.generate_hypothesis_puzzle - чистая
            # функция).
            tech_id = payload.get("tech_id")
            if tech_id not in TECHS:
                self._json(400, {"error": "unknown tech"})
                return
            self._json(200, engine.generate_hypothesis_puzzle(tech_id))
        elif self.path == "/api/solve_hypothesis":
            # Не тратит ход (см. engine.solve_hypothesis) - тот же принцип, что
            # insert_soul/remove_soul ниже. Сервер НЕ проверяет решение самой
            # головоломки - клиент уже прогнал BFS локально (см. CLAUDE.md про
            # отсутствие анти-чита в однопользовательской игре).
            result = engine.solve_hypothesis(STATE, payload.get("tech_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/smelt_cancel_slot":
            # Вынуть порцию из слота плавки - как cancel_manual_job, НЕ тратит ход,
            # сырьё возвращается (engine.cancel_melt_slot).
            try:
                index = int(payload.get("index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "index должен быть целым числом"})
                return
            result = engine.cancel_melt_slot(STATE, payload.get("building_id"), index)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/smelt_skim":
            # Снять шлак из тигля на склад - не тратит ход (engine.skim_smeltery).
            result = engine.skim_smeltery(STATE, payload.get("building_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/insert_soul":
            # Вставка/выемка души - управление имуществом, как house_creature/
            # release_creature, НЕ тратит ход (см. engine.insert_soul/remove_soul).
            # instance_id - id ФИЗИЧЕСКОГО экземпляра синтезатора (как farm_instance_id
            # у house_creature), не id типа постройки - см. state.py:soul_collector_instances.
            result = engine.insert_soul(STATE, payload.get("instance_id"), payload.get("soul_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/remove_soul":
            try:
                index = int(payload.get("index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "index должен быть целым числом"})
                return
            result = engine.remove_soul(STATE, payload.get("instance_id"), index)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/insert_reactor_component":
            # Установка компонента в ячейку решётки реактора - управление
            # имуществом, как insert_soul, НЕ тратит ход (см. engine.
            # insert_reactor_component). instance_id - id ФИЗИЧЕСКОГО экземпляра
            # реактора (как instance_id у insert_soul), cell_index - позиция в
            # решётке (row-major, см. state.py:reactor_instances).
            try:
                cell_index = int(payload.get("cell_index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "cell_index должен быть целым числом"})
                return
            result = engine.insert_reactor_component(STATE, payload.get("instance_id"), cell_index, payload.get("resource_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/remove_reactor_component":
            try:
                cell_index = int(payload.get("cell_index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "cell_index должен быть целым числом"})
                return
            result = engine.remove_reactor_component(STATE, payload.get("instance_id"), cell_index)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/insert_reactor_catalyst":
            # Установка катализатора В УЖЕ занятую ячейку - не тратит ход
            # (см. engine.insert_reactor_catalyst), отдельный слот поверх
            # основного компонента.
            try:
                cell_index = int(payload.get("cell_index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "cell_index должен быть целым числом"})
                return
            result = engine.insert_reactor_catalyst(STATE, payload.get("instance_id"), cell_index, payload.get("resource_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/remove_reactor_catalyst":
            try:
                cell_index = int(payload.get("cell_index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "cell_index должен быть целым числом"})
                return
            result = engine.remove_reactor_catalyst(STATE, payload.get("instance_id"), cell_index)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/set_reactor_coolant_fluid":
            # Какая жидкость залита в контур - настройка экземпляра, НЕ тратит
            # ход (см. engine.set_reactor_coolant_fluid). resource_id = null
            # означает "осушить контур".
            result = engine.set_reactor_coolant_fluid(
                STATE, payload.get("instance_id"), payload.get("resource_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/set_reactor_zone_control":
            # Глубина стержней управления зоны - постоянная настройка игрока,
            # НЕ тратит ход (см. engine.set_reactor_zone_control).
            try:
                zone_index = int(payload.get("zone_index"))
                depth_pct = int(payload.get("depth_pct"))
            except (TypeError, ValueError):
                self._json(400, {"error": "zone_index и depth_pct должны быть целыми числами"})
                return
            result = engine.set_reactor_zone_control(STATE, payload.get("instance_id"), zone_index, depth_pct)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/set_reactor_zone_coolant":
            try:
                zone_index = int(payload.get("zone_index"))
                flow_pct = int(payload.get("flow_pct"))
            except (TypeError, ValueError):
                self._json(400, {"error": "zone_index и flow_pct должны быть целыми числами"})
                return
            result = engine.set_reactor_zone_coolant(STATE, payload.get("instance_id"), zone_index, flow_pct)
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/purchase_reactor_upgrade":
            # Покупка апгрейда ОДНОГО экземпляра реактора - зеркало
            # /api/purchase_farm_upgrade (см. engine.purchase_reactor_instance_upgrade).
            result = engine.purchase_reactor_instance_upgrade(STATE, payload.get("instance_id"), payload.get("upgrade_id"))
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
        elif self.path == "/api/purchase_soul_upgrade":
            # Покупка апгрейда ОДНОГО экземпляра синтезатора душ - зеркало
            # /api/purchase_farm_upgrade (см. engine.purchase_soul_collector_instance_upgrade).
            result = engine.purchase_soul_collector_instance_upgrade(STATE, payload.get("instance_id"), payload.get("upgrade_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/analyze_creature":
            # Мгновенное опознание генома (генетическая лаборатория) - НЕ тратит
            # ход, тем же принципом, что house/release (см. engine.analyze_creature).
            result = engine.analyze_creature(STATE, payload.get("creature_id"))
            save_state(STATE)
            self._json(200, {"ok": result.ok, "message": result.message, "state": build_view(STATE)})
        elif self.path == "/api/reset":
            # keep_copy - «Начать заново» с экрана ошибки: сейв, который не открылся,
            # не затирается, а откладывается в копию.
            if payload.get("keep_copy") and SAVE_PATH.exists():
                _set_aside_autosave()
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
            try:
                loaded = read_save(path)
                build_view(loaded)
            except Exception as e:
                traceback.print_exc()
                self._json(400, {"error": f"Сохранение «{name}» не открывается ({type(e).__name__}: {e})."})
                return
            STATE = loaded
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

    print("Закрой окно игры, когда закончишь - прогресс уже сохранён в saves/autosave.json.")
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
