import os
import sys

from game import engine
from game.content import (
    BUILDINGS, ENERGY_RECIPES, LOCATIONS, MANUAL_ACTIONS, QUESTS, RESOURCE_NAMES,
    RESOURCE_ORDER, TECHS, TOOLS,
)
from game.state import GameState

if sys.platform == "win32":
    # На Windows Python 3.6+ и так использует UTF-8 для stdin/stdout, когда они
    # подключены к НАСТОЯЩЕЙ консоли (PEP 528) - но при перенаправлении/пайпе
    # (например, интерактивный запуск через Git Bash/MSYS, где stdin для Python
    # выглядит как обычный пайп, а не консоль) откатывается на ANSI-кодировку
    # системы (обычно cp1251). Из-за этого кириллица, приходящая по такому пайпу
    # (например, имя нового сохранения), декодируется неверно и портится ещё на
    # входе - до того, как дойти до safe_save_name/state.save(). Принудительный
    # UTF-8 чинит именно этот случай и ничего не ломает в обычной консоли (там
    # ввод/вывод и так уже UTF-8).
    for _stream in (sys.stdin, sys.stdout):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

# Именованные сохранения (папка saves/) - та же папка, где лежит и постоянное
# автосохранение (SAVE_PATH, файл autosave.json внутри неё) - раньше автосейв
# лежал прямо в корне репозитория отдельно от saves/, что было неочевидно и
# путало игрока при поиске файлов сохранений.
SAVES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves")
SAVE_PATH = os.path.join(SAVES_DIR, "autosave.json")
# Старое место автосохранения (до переноса в saves/) - если оно ещё лежит в
# корне у игрока, при первом запуске переносим его на новое место, а не
# тихо теряем прогресс, подсовывая вместо него новую пустую игру.
_LEGACY_SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "save.json")


def _migrate_legacy_save() -> None:
    if os.path.exists(SAVE_PATH) or not os.path.exists(_LEGACY_SAVE_PATH):
        return
    os.makedirs(SAVES_DIR, exist_ok=True)
    os.replace(_LEGACY_SAVE_PATH, SAVE_PATH)


_UNSAFE_SAVE_NAME_CHARS = set('\\/:*?"<>|') | {chr(c) for c in range(32)}

NAME_COL = max(len(name) for name in RESOURCE_NAMES.values()) + 2
PINNABLE_RESOURCES = [r for r in RESOURCE_ORDER if r != engine.ENERGY_RESOURCE_ID]


def format_cost(cost: dict[str, int]) -> str:
    if not cost:
        return "бесплатно"
    return ", ".join(f"{RESOURCE_NAMES[r]} {amount}" for r, amount in cost.items())


def format_gain(gain: dict[str, int]) -> str:
    return ", ".join(f"+{amount} {RESOURCE_NAMES[r]}" for r, amount in gain.items())


def format_upkeep(produces: dict[str, int], consumes: dict[str, int]) -> str:
    parts = [f"+{amount} {RESOURCE_NAMES[r]}" for r, amount in produces.items()]
    parts += [f"-{amount} {RESOURCE_NAMES[r]}" for r, amount in consumes.items()]
    return ", ".join(parts)


def format_special_abilities(building: dict) -> str:
    notes = []
    compute_power = building.get("compute_power", 0)
    if compute_power:
        notes.append(f"+{compute_power} вычислит. мощности")
    speed = building.get("autocraft_speed", 0)
    if speed:
        notes.append(f"+{speed} скорости автокрафта")
    slots = building.get("autocraft_slots", 0)
    if slots:
        notes.append(f"+{slots} слот(ов) автокрафта")
    unlocks = building.get("unlocks_autocraft", [])
    if unlocks:
        notes.append(f"открывает автокрафт: {', '.join(RESOURCE_NAMES[r] for r in unlocks)}")
    converter = building.get("energy_converter")
    if converter:
        cap = f"до {converter['max_energy_per_turn']} энергии/ход" if converter.get("max_energy_per_turn") else "без ограничения энергии/ход"
        notes.append(f"энергетический преобразователь ({cap}) - рецепт выбирается в «Преобразователе»")
    return ", ".join(notes)


def format_deltas(before: dict[str, int], after: dict[str, int]):
    deltas = {
        r: after.get(r, 0) - before.get(r, 0)
        for r in RESOURCE_ORDER
        if after.get(r, 0) != before.get(r, 0)
    }
    if not deltas:
        return None
    return ", ".join(f"{'+' if amount > 0 else ''}{amount} {RESOURCE_NAMES[r]}" for r, amount in deltas.items())


def print_status(state: GameState) -> None:
    time_info = engine.current_time(state)
    day_night = "день" if time_info["is_day"] else "ночь"
    print(f"\n=== Ход {state.turn} | День {time_info['day']}, {time_info['hour']:02d}:00 ({day_night}) ===")
    if state.save_name:
        print(f"(сохранение: {state.save_name})")

    stock, gen, cons = engine.energy_breakdown(state)
    capacity = engine.energy_capacity(state)
    free = gen - cons
    sign = "+" if free >= 0 else ""
    stock_text = f"{stock}/{capacity}" if capacity else f"{stock} (без хранилищ - не копится)"
    print(
        f"Энергия: запас {stock_text}  |  генерация +{gen}/ход  |  потребление -{cons}/ход  |  свободно {sign}{free}/ход"
    )
    compute_capacity = engine.compute_capacity(state)
    print(f"Вычислительная мощность процессоров: {compute_capacity}")
    autocraft_status = ""
    if compute_capacity > 0:
        label = "включён" if state.autocraft_enabled else "выключен (рецепты только руками)"
        autocraft_status = f" | {label}"
    print(
        f"Автокрафт{autocraft_status}: скорость {engine.autocraft_speed_capacity(state)} операций/ход, "
        f"слоты {len(state.autocraft_jobs)}/{engine.autocraft_slot_capacity(state)}"
    )
    for job in engine.autocraft_jobs_view(state):
        eta = "нет скорости" if job["eta_turns"] is None else f"~{job['eta_turns']} ход(ов)"
        print(f"  Докрафтывается: {RESOURCE_NAMES[job['resource_id']]} {job['fires_done']}/{job['fires_total']} ({eta})")
    for pending in state.pending_actions:
        print(f"  Ждёт автокрафта: {pending['label']}")

    converter_capacity = engine.energy_converter_capacity(state)
    if converter_capacity > 0:
        print(f"Энергетические преобразователи: занято {len(state.energy_jobs)}/{converter_capacity}")
        for job in engine.energy_jobs_view(state):
            print(f"  Крафт из энергии: {job['name']} {job['progress']}/{job['threshold']}")

    for tool_id, tool in TOOLS.items():
        tier = engine.tool_tier(state, tool_id)
        print(f"{tool['name']}: {engine.tool_tier_name(state, tool_id)} (тир {tier} из {len(tool['tiers'])})")
    if state.active_mine_field:
        broken = sum(1 for c in state.active_mine_field["cells"] if c["broken"])
        total = len(state.active_mine_field["cells"])
        print(f"Поле шахты: пройдено {broken}/{total} клеток (консольная версия не поддерживает добычу - см. web_ui)")

    rates = engine.theoretical_rates(state)
    if state.pinned:
        print("Ресурсы на панели:")
        for r in state.pinned:
            amount = state.resources.get(r, 0)
            rate = rates.get(r, 0)
            rate_text = f"  ({'+' if rate > 0 else ''}{rate}/ход)" if rate != 0 else ""
            print(f"  {RESOURCE_NAMES[r].ljust(NAME_COL)}{str(amount).rjust(6)}{rate_text}")
    else:
        print("Нет закреплённых ресурсов на панели - загляни в Инвентарь.")

    if state.buildings:
        print("Постройки:")
        for b, count in state.buildings.items():
            if count <= 0:
                continue
            broken = state.broken.get(b, 0)
            broken_text = f"  (сломано: {broken})" if broken else ""
            print(f"  {BUILDINGS[b]['name']} x{count}{broken_text}")
    else:
        print("Построек пока нет.")

    print(f"Изучено технологий: {len(state.techs)}/{len(TECHS)}")


def tech_item_text(tech_id: str, state: GameState) -> str:
    tech = TECHS[tech_id]
    unlocks = engine.tech_unlocks(tech_id)
    names = [BUILDINGS[b]["name"] for b in unlocks["buildings"]]
    names += [f"локация «{LOCATIONS[l]['name']}»" for l in unlocks["locations"]]
    names += [MANUAL_ACTIONS[a]["name"] for a in unlocks["manual_actions"]]
    names += [f"добыча «{RESOURCE_NAMES[r]}»" for r in unlocks["mining"]]
    names += unlocks["tools"]
    names += [f"рецепт за энергию «{ENERGY_RECIPES[r]['name']}»" for r in unlocks["energy_recipes"]]
    unlock_text = f" Открывает: {', '.join(names)}." if names else ""
    tier = tech.get("tier", 1)
    # Веб-версия показывает это прямо бейджем в дереве технологий (tier/lab_built) -
    # в консоли до этого пункт выглядел как обычный доступный тех до самой попытки
    # исследовать, и только тогда падал с "нужна лаборатория тира N" (engine.research).
    # Показываем то же требование сразу в списке, чтобы не приходилось гадать заранее.
    lab_note = f" [нужна лаборатория тира {tier}]" if tier > engine.lab_tier_capacity(state) else ""
    return f"{tech['name']} (цена: {format_cost(tech['cost'])}) - {tech['description']}{unlock_text}{lab_note}"


def category_items(state: GameState, kind: str):
    items = []
    if kind == "build":
        for building_id in engine.available_buildings(state):
            building = BUILDINGS[building_id]
            owned = state.buildings.get(building_id, 0)
            max_count = building.get("max_count")
            if max_count is not None:
                owned_note = f", построено: {owned}/{max_count}"
            elif owned:
                owned_note = f", уже построено: {owned}"
            else:
                owned_note = ""
            stage = engine.current_construction_stage(building_id, state)
            if stage is not None:
                cost = stage["cost"]
                if stage["total"] > 1:
                    stage_note = f" [Стадия {stage['number']} из {stage['total']}: {stage['description']}]"
                else:
                    stage_note = f" [{stage['description']}]"
            else:
                cost = building["cost"]
                stage_note = ""
            produces, consumes = engine.effective_building_stats(building_id, state.techs)
            upkeep = format_upkeep(produces, consumes)
            upkeep_note = f" | содержание/ход: {upkeep}" if upkeep else ""
            special = format_special_abilities(building)
            special_note = f" | {special}" if special else ""
            desc_note = f" - {building['description']}" if building.get("description") else ""
            text = f"{building['name']} (цена: {format_cost(cost)}{owned_note}){stage_note}{desc_note}{upkeep_note}{special_note}"
            items.append((building_id, text))
    elif kind == "tech":
        for tech_id in engine.available_techs(state):
            items.append((tech_id, tech_item_text(tech_id, state)))
    elif kind == "repair":
        for building_id in engine.broken_buildings(state):
            cost = engine.repair_cost_total(state, building_id)
            broken = state.broken[building_id]
            text = f"{BUILDINGS[building_id]['name']} - сломано {broken} шт. (цена ремонта: {format_cost(cost)})"
            items.append((building_id, text))
    return items


def build_categories(state: GameState):
    categories = []
    for kind, label in [
        ("build", "Построить"),
        ("tech", "Исследовать"),
    ]:
        categories.append((kind, label, category_items(state, kind)))
    repair_items = category_items(state, "repair")
    if repair_items:
        categories.append(("repair", "Ремонт", repair_items))
    return categories


def run_action(state: GameState, kind: str, item_id) -> tuple[engine.ActionResult, str, object]:
    """Возвращает (результат, нормализованный action_kind, action_id) - вторые два для истории ходов."""
    if kind == "build":
        return engine.build(state, item_id), "build", item_id
    if kind == "tech":
        return engine.research(state, item_id), "tech", item_id
    return engine.repair(state, item_id), "repair", item_id


def location_action_items(state: GameState, location_id: str | None):
    """Пункты меню локации: её ручные действия (location_id=None - действия без
    своей локации, лежащие в корне дерева) плюс - только для локации-шахты
    (engine.MINE_LOCATION_ID) - добыча уже найденных жил и единая кнопка поиска
    новой, и только для локации-преобразователя (engine.CONVERTER_LOCATION_ID,
    вложена в "Механизмы", см. CLAUDE.md) - список рецептов крафта за энергию.
    Апгрейд инструментов с локациями больше НЕ связан - см. manage_tools (общий
    пункт меню, не завязанный ни на одну локацию)."""
    items = []
    for action_id in engine.manual_actions_at(location_id, state):
        action = MANUAL_ACTIONS[action_id]
        text = f"{action['name']} (цена: {format_cost(action['cost'])}) -> {format_gain(action['gain'])}"
        items.append((("manual", action_id), text))

    # Шахта (engine.MINE_LOCATION_ID) - fog-of-war сетка, есть только в web_ui
    # (клик по клетке поля, не сводится к пункту меню) - консольная версия
    # официально не поддерживается как играбельная, см. CLAUDE.md.
    if location_id == engine.CONVERTER_LOCATION_ID:
        capacity = engine.energy_converter_capacity(state)
        busy = len(state.energy_jobs)
        for recipe_id in engine.available_energy_recipes(state):
            recipe = ENERGY_RECIPES[recipe_id]
            already_running = any(j["recipe_id"] == recipe_id for j in state.energy_jobs)
            gain = format_gain(recipe.get("produces", {}))
            note = ""
            if capacity <= 0:
                note = " [нет ни одного преобразователя]"
            elif busy >= capacity:
                note = " [все преобразователи заняты]"
            elif already_running:
                note = " [уже выполняется]"
            text = f"Запустить: {recipe['name']} ({recipe['threshold']} энергии -> {gain}){note}"
            items.append((("start_energy_recipe", recipe_id), text))
    return items


def run_location_item(state: GameState, item_id) -> tuple[engine.ActionResult, str, object]:
    kind, payload = item_id
    if kind == "manual":
        return engine.do_manual_action(state, payload), "manual", payload
    return engine.start_energy_recipe(state, payload), "start_energy_recipe", payload


def location_entry_count(state: GameState, location_id: str | None) -> int:
    """Сколько пунктов покажет эта локация, если в неё зайти - вложенные локации
    плюс собственные действия (включая шахту/механизмы-спецпункты) - используется и
    для счётчика в списке локаций уровнем выше, и для счётчика на самом верхнем
    пункте меню "Локации (N)" (там location_id=None - корень)."""
    return len(engine.location_children(location_id, state)) + len(location_action_items(state, location_id))


def choose_location_and_run(state: GameState) -> tuple[engine.ActionResult, str, object] | None:
    """Свободная навигация по ПОЛНОЦЕННОМУ ДЕРЕВУ локаций (LOCATIONS[...]
    ["parent_location"]) - сам переход по локациям хода не тратит, тратит только
    выбор конкретного действия. Внутри одной локации могут одновременно быть и
    вложенные локации, и собственные действия. "b" - на шаг назад, "r" - сразу в
    корень (доступны, пока мы не в корне). None - игрок вышел, ничего не сделав."""
    path: list[str] = []  # id локаций от корня до текущей; [] = сейчас в корне
    while True:
        current_id = path[-1] if path else None
        children = engine.location_children(current_id, state)
        items = location_action_items(state, current_id)
        entries: list[tuple[str, bool, object]] = (
            [(f"[Локация] {LOCATIONS[lid]['name']} ({location_entry_count(state, lid)})", True, lid) for lid in children]
            + [(text, False, item_id) for item_id, text in items]
        )

        header = " > ".join(LOCATIONS[lid]["name"] for lid in path) if path else "Локации"
        print(f"\n-- {header} --")
        if not entries:
            print("Здесь пока нечего делать.")
        for i, (label, _, _) in enumerate(entries, start=1):
            print(f" {i}. {label}")
        if path:
            print(" b. Назад")
            print(" r. В начало")
        print(" 0. Выйти из локаций")

        choice = input("> ").strip().lower()
        if choice in ("0", ""):
            return None
        if choice == "b" and path:
            path.pop()
            continue
        if choice == "r" and path:
            path = []
            continue
        try:
            index = int(choice) - 1
        except ValueError:
            print("Не понял выбор, попробуй ещё раз.")
            continue
        if index < 0 or index >= len(entries):
            print("Не понял выбор, попробуй ещё раз.")
            continue

        _, is_location, payload = entries[index]
        if is_location:
            path.append(payload)
            continue
        return run_location_item(state, payload)


def open_inventory(state: GameState) -> None:
    """Полный список ресурсов + переключение, что закреплено на главной панели. Ход не тратит."""
    while True:
        print("\n-- Инвентарь --")
        print(f"Закреплено на панели: {len(state.pinned)}/{engine.MAX_PINNED}")
        stock, gen, cons = engine.energy_breakdown(state)
        capacity = engine.energy_capacity(state)
        energy_label = RESOURCE_NAMES.get(engine.ENERGY_RESOURCE_ID, "Энергия")
        cap_note = f", ёмкость хранилищ {capacity}" if capacity else ", без хранилищ - не копится"
        print(
            f"      {energy_label.ljust(NAME_COL)}{str(stock).rjust(6)}"
            f"  (всегда на панели, +{gen}/-{cons} за ход{cap_note})"
        )
        for i, r in enumerate(PINNABLE_RESOURCES, start=1):
            mark = "x" if r in state.pinned else " "
            amount = state.resources.get(r, 0)
            print(f" [{mark}] {i:>2}. {RESOURCE_NAMES[r].ljust(NAME_COL)}{str(amount).rjust(6)}")
        print(" 0. Назад")

        choice = input("> ").strip().lower()
        if choice in ("0", ""):
            return
        try:
            index = int(choice) - 1
        except ValueError:
            print("Не понял выбор, попробуй ещё раз.")
            continue
        if index < 0 or index >= len(PINNABLE_RESOURCES):
            print("Не понял выбор, попробуй ещё раз.")
            continue
        result = engine.toggle_pin(state, PINNABLE_RESOURCES[index])
        print(result.message)


def manage_tools(state: GameState) -> tuple[engine.ActionResult, str] | None:
    """Статус всех инструментов + возможность улучшить любой из них - общий пункт
    меню верхнего уровня (как "Локации"), не завязанный ни на одну локацию (было
    раньше жёстко про апгрейд кирки внутри Шахты, см. game/engine.py:upgrade_tool).
    None - игрок вышел, ничего не улучшив (ход не тратится); (result, tool_id) -
    была реальная попытка улучшения (успешная или нет) - вызывающий решает про
    трату хода и запись в историю, как с любым другим действием."""
    while True:
        print("\n-- Инструменты --")
        for tool_id, tool in TOOLS.items():
            tier = engine.tool_tier(state, tool_id)
            print(f"  {tool['name']}: {engine.tool_tier_name(state, tool_id)} (тир {tier} из {len(tool['tiers'])})")
            # Следующий тир показываем, ПОКА он есть, даже если пока не хватает
            # технологии - чтобы было видно, что дальше есть куда расти и чего это
            # будет стоить, а не только то, что уже прямо сейчас можно купить.
            next_info = engine.next_tool_tier_info(state, tool_id)
            next_tech = next_info["requires_tech"] if next_info else None
            if next_info and next_tech and next_tech not in state.techs:
                print(f"    Дальше: {next_info['name']} (цена: {format_cost(next_info['cost'])}) - "
                      f"нужна технология «{TECHS[next_tech]['name']}»")
        upgradable = engine.upgradable_tools(state)
        if not upgradable:
            print("Улучшать сейчас нечего.")
            input("(Enter, чтобы вернуться) > ")
            return None
        print()
        for i, tool_id in enumerate(upgradable, start=1):
            next_info = engine.next_tool_tier_info(state, tool_id)
            print(f" {i}. Улучшить {TOOLS[tool_id]['name']} -> {next_info['name']} (цена: {format_cost(next_info['cost'])})")
        print(" 0. Назад")

        choice = input("> ").strip().lower()
        if choice in ("0", ""):
            return None
        try:
            index = int(choice) - 1
        except ValueError:
            print("Не понял выбор, попробуй ещё раз.")
            continue
        if index < 0 or index >= len(upgradable):
            print("Не понял выбор, попробуй ещё раз.")
            continue
        tool_id = upgradable[index]
        return engine.upgrade_tool(state, tool_id), tool_id


def manage_background_jobs(state: GameState) -> None:
    """Список фоновых задач (автокрафт + крафт за энергию) с возможностью отменить
    любую из них немедленно, без траты хода - см. engine.cancel_autocraft_job/
    cancel_energy_job. Не тратит ход сам по себе (как и просмотр инвентаря)."""
    while True:
        jobs = []  # [(label, cancel_fn)]
        for job in engine.autocraft_jobs_view(state):
            eta = "нет скорости" if job["eta_turns"] is None else f"~{job['eta_turns']} ход(ов)"
            label = f"Автокрафт: {RESOURCE_NAMES[job['resource_id']]} {job['fires_done']}/{job['fires_total']} ({eta})"
            jobs.append((label, lambda rid=job["resource_id"]: engine.cancel_autocraft_job(state, rid)))
        for job in engine.energy_jobs_view(state):
            label = f"Крафт за энергию: {job['name']} {job['progress']}/{job['threshold']}"
            jobs.append((label, lambda idx=job["index"]: engine.cancel_energy_job(state, idx)))

        print("\n-- Фоновые задачи --")
        if not jobs:
            print("Сейчас ничего не выполняется в фоне.")
            input("(Enter, чтобы вернуться) > ")
            return
        for i, (label, _) in enumerate(jobs, start=1):
            print(f" {i}. {label}")
        print(" 0. Назад")

        choice = input("Отменить номер (0 - назад) > ").strip().lower()
        if choice in ("0", ""):
            return
        try:
            index = int(choice) - 1
        except ValueError:
            print("Не понял выбор, попробуй ещё раз.")
            continue
        if index < 0 or index >= len(jobs):
            print("Не понял выбор, попробуй ещё раз.")
            continue
        _, cancel_fn = jobs[index]
        result = cancel_fn()
        print(result.message)


def show_stats(state: GameState) -> None:
    print("\n-- Статистика --")
    print(f"Сейчас идёт ход {state.turn}. Введи диапазон ходов (например: 1 {max(state.turn - 1, 1)}).")
    raw = input("Диапазон (начало конец) > ").strip()
    parts = raw.split()
    if len(parts) != 2:
        print("Не понял диапазон.")
        return
    try:
        start_turn, end_turn = int(parts[0]), int(parts[1])
    except ValueError:
        print("Диапазон должен быть двумя числами.")
        return

    stats = engine.compute_stats(state, start_turn, end_turn)
    if stats is None:
        print("За этот диапазон нет данных (ещё не наступил или уже не помнится).")
        return

    print(f"\nХоды {stats['start_turn']}-{stats['end_turn']} (записано: {stats['turns_recorded']}, пропущено: {stats['turns_skipped']})")
    if stats["resource_gained"] or stats["resource_spent"]:
        print("Ресурсы:")
        all_ids = set(stats["resource_gained"]) | set(stats["resource_spent"])
        for rid in all_ids:
            gained = stats["resource_gained"].get(rid, 0)
            spent = stats["resource_spent"].get(rid, 0)
            net = stats["resource_net"].get(rid, 0)
            print(f"  {RESOURCE_NAMES[rid]}: получено {gained}, потрачено {spent}, итого {'+' if net>=0 else ''}{net}")
    if stats["techs_researched"]:
        names = ", ".join(TECHS[t]["name"] for t in stats["techs_researched"])
        print(f"Исследовано технологий: {len(stats['techs_researched'])} ({names})")
    if stats["buildings_built"]:
        names = ", ".join(f"{BUILDINGS[b]['name']} x{n}" for b, n in stats["buildings_built"].items())
        print(f"Построено: {names}")
    if stats["quests_completed"]:
        names = ", ".join(QUESTS[q]["name"] for q in stats["quests_completed"])
        print(f"Выполнено квестов: {names}")
    print(f"Поломок: {stats['total_breakdowns']}, ремонтов: {stats['total_repairs']}")
    input("(Enter, чтобы вернуться) > ")


def confirm_reset() -> bool:
    answer = input("Точно обнулить весь прогресс? Это нельзя отменить (да/нет) > ").strip().lower()
    return answer in ("да", "y", "yes")


def safe_save_name(raw: str) -> str | None:
    # Имя приходит от игрока в свободной форме - защищаемся от пустого имени и от
    # символов, недопустимых в именах файлов на Windows (заодно от "..", чтобы
    # нельзя было промахнуться мимо SAVES_DIR). "autosave" зарезервировано под
    # SAVE_PATH (файл autosave.json в той же SAVES_DIR).
    name = raw.strip()
    if (
        not name
        or name in (".", "..")
        or any(ch in _UNSAFE_SAVE_NAME_CHARS for ch in name)
        or name.lower() == "autosave"
    ):
        return None
    return name


def list_saves() -> list[str]:
    if not os.path.isdir(SAVES_DIR):
        return []
    return sorted(
        f[:-5] for f in os.listdir(SAVES_DIR)
        if f.endswith(".json") and f[:-5].lower() != "autosave"
    )


def manage_saves(state: GameState):
    """Экран именованных сохранений (папка saves/) и обнуления прогресса. Возвращает:
    - GameState, если игрок открыл сохранение (вызывающий должен подменить им текущий state);
    - "reset", если игрок подтвердил обнуление прогресса (вызывающий создаёт GameState());
    - None, если ничего не изменилось (сохранили под именем, отменили или просто вышли)."""
    while True:
        saves = list_saves()
        print("\n-- Сохранения --")
        if saves:
            for i, name in enumerate(saves, start=1):
                print(f" {i}. Открыть «{name}»")
        else:
            print(" (пока нет ни одного именованного сохранения)")
        print(" с. Сохранить текущий прогресс под новым именем")
        print(" н. Начать заново (обнулить весь прогресс)")
        print(" 0. Назад")

        choice = input("> ").strip().lower()
        if choice in ("0", ""):
            return None
        if choice in ("с", "c"):  # кириллическая и латинская "с" выглядят одинаково
            name = safe_save_name(input("Имя сохранения > "))
            if name is None:
                print("Недопустимое или пустое имя.")
                continue
            os.makedirs(SAVES_DIR, exist_ok=True)
            state.save_name = name  # "Сохранить как X" - активная партия теперь называется X
            state.save(os.path.join(SAVES_DIR, f"{name}.json"))
            state.save(SAVE_PATH)  # чтобы новое имя сразу отражалось и в текущем автосохранении
            print(f"Сохранено как «{name}».")
            continue
        if choice in ("н", "n"):  # кириллическая и латинская "н"/"n"
            if confirm_reset():
                return "reset"
            print("Отменено.")
            continue
        try:
            index = int(choice) - 1
        except ValueError:
            print("Не понял выбор, попробуй ещё раз.")
            continue
        if not (0 <= index < len(saves)):
            print("Не понял выбор, попробуй ещё раз.")
            continue

        name = saves[index]
        confirm = input(f"Открыть «{name}»? Текущая игра будет заменена им (да/нет) > ").strip().lower()
        if confirm not in ("да", "y", "yes"):
            print("Отменено.")
            continue
        loaded = GameState.load(os.path.join(SAVES_DIR, f"{name}.json"))
        loaded.save_name = name  # имя определяется выбранным слотом, а не тем, что лежало в файле
        print(f"Загружено: «{name}».")
        return loaded


def take_turn(state: GameState):
    """Один ход. Возвращает "exit" (игрок сохранился и вышел), "reset" (обнулить
    прогресс - state.save/GameState() выполняет вызывающий main()), "continue",
    либо кортеж ("load", loaded_state) - игрок открыл именованное сохранение
    (main() должен подменить им текущий state)."""
    print_status(state)
    categories = build_categories(state)

    upgradable_tools = engine.upgradable_tools(state)

    print("\nМеню:")
    print(f" 1. Локации ({location_entry_count(state, None)})")
    print(f" 2. Инструменты ({len(upgradable_tools)} можно улучшить)")
    for i, (_, label, items) in enumerate(categories, start=3):
        print(f" {i}. {label} ({len(items)})")
    meta_items = [
        ("inventory", "Инвентарь / закрепить ресурсы"),
        ("stats", "Статистика за диапазон ходов"),
        ("saves", "Сохранения (открыть / сохранить как / начать заново)"),
    ]
    # Пункт появляется только с первого работающего процессора - до этого автокрафт
    # всё равно не может ничего докрафтить (compute_capacity == 0), см. game/engine.py.
    if engine.compute_capacity(state) > 0:
        autocraft_label = "включён" if state.autocraft_enabled else "выключен"
        meta_items.append(("autocraft", f"Автокрафт: {autocraft_label} (переключить)"))
    if state.autocraft_jobs or state.energy_jobs:
        jobs_count = len(state.autocraft_jobs) + len(state.energy_jobs)
        meta_items.append(("jobs", f"Фоновые задачи ({jobs_count}) - отменить"))
    for j, (_, label) in enumerate(meta_items, start=len(categories) + 3):
        print(f" {j}. {label}")
    print(" 0. Пропустить ход (ничего не делать)")
    print(" q. Сохранить и выйти")

    choice = input("> ").strip().lower()
    if choice == "q":
        state.save(SAVE_PATH)
        print("Сохранено. До встречи.")
        return "exit"

    if choice != "0":
        try:
            index = int(choice) - 1
        except ValueError:
            print("Не понял выбор, попробуй ещё раз.")
            return "continue"

        if index == 0:
            before_action = dict(state.resources)
            outcome = choose_location_and_run(state)
            if outcome is None:
                return "continue"
            result, action_kind, action_id = outcome
            print(result.message)
            if not result.ok:
                return "continue"
            action_delta = format_deltas(before_action, state.resources)
            if action_delta:
                print(f"Потрачено/получено (с учётом автокрафта): {action_delta}")
        elif index == 1:
            before_action = dict(state.resources)
            outcome = manage_tools(state)
            if outcome is None:
                return "continue"
            result, tool_id = outcome
            action_kind, action_id = "upgrade_tool", tool_id
            print(result.message)
            if not result.ok:
                return "continue"
            action_delta = format_deltas(before_action, state.resources)
            if action_delta:
                print(f"Потрачено/получено (с учётом автокрафта): {action_delta}")
        else:
            cat_index = index - 2
            meta_index = cat_index - len(categories)
            if 0 <= meta_index < len(meta_items):
                meta_kind, _ = meta_items[meta_index]
                if meta_kind == "inventory":
                    open_inventory(state)
                elif meta_kind == "stats":
                    show_stats(state)
                elif meta_kind == "saves":
                    result = manage_saves(state)
                    if isinstance(result, GameState):
                        return ("load", result)
                    if result == "reset":
                        return "reset"
                elif meta_kind == "autocraft":
                    result = engine.toggle_autocraft(state)
                    print(result.message)
                elif meta_kind == "jobs":
                    manage_background_jobs(state)
                return "continue"
            if cat_index < 0 or cat_index >= len(categories):
                print("Не понял выбор, попробуй ещё раз.")
                return "continue"

            kind, label, items = categories[cat_index]
            print(f"\n-- {label} --")
            for i, (_, text) in enumerate(items, start=1):
                print(f" {i}. {text}")
            print(" 0. Назад")

            sub_choice = input("> ").strip().lower()
            if sub_choice in ("0", ""):
                return "continue"
            if sub_choice == "q":
                state.save(SAVE_PATH)
                print("Сохранено. До встречи.")
                return "exit"
            try:
                sub_index = int(sub_choice) - 1
            except ValueError:
                print("Не понял выбор, попробуй ещё раз.")
                return "continue"
            if sub_index < 0 or sub_index >= len(items):
                print("Не понял выбор, попробуй ещё раз.")
                return "continue"

            item_id, _ = items[sub_index]
            before_action = dict(state.resources)
            result, action_kind, action_id = run_action(state, kind, item_id)
            print(result.message)
            if not result.ok:
                return "continue"

            action_delta = format_deltas(before_action, state.resources)
            if action_delta:
                print(f"Потрачено/получено (с учётом автокрафта): {action_delta}")
    else:
        action_kind, action_id, result = None, None, None

    before_turn = dict(state.resources)
    report = engine.advance_turn(state)
    production_delta = format_deltas(before_turn, state.resources)
    if production_delta:
        print(f"Автопроизводство за ход: {production_delta}")

    idle_buildings = [BUILDINGS[b]["name"] for b, (ran, idle) in report.ran.items() if idle > 0]
    if idle_buildings:
        print(f"Простаивают (не хватает сырья/энергии или не то время суток): {', '.join(idle_buildings)}")

    if report.newly_broken:
        broken_line = ", ".join(f"{BUILDINGS[b]['name']} x{n}" for b, n in report.newly_broken.items())
        print(f"Сломалось: {broken_line}")

    if report.autocraft_completed:
        names = ", ".join(RESOURCE_NAMES[r] for r in report.autocraft_completed)
        print(f"Автокрафт докрафтил: {names}")

    if report.pending_finished:
        print(f"Дождалось автокрафта и завершилось: {', '.join(report.pending_finished)}")

    newly_completed_quests = engine.check_quests(state)
    for qid in newly_completed_quests:
        print(f"🏆 Квест выполнен: {QUESTS[qid]['name']} - {QUESTS[qid]['description']}")

    engine.record_history_entry(
        state, action_kind, action_id,
        result.message if result else None,
        report, newly_completed_quests,
    )

    state.save(SAVE_PATH)

    if engine.all_techs_researched(state):
        print("\nВсе технологии изучены! Дерево прогресса пройдено полностью - можно придумывать следующий тир вместе.")

    return "continue"


def main() -> None:
    _migrate_legacy_save()
    if os.path.exists(SAVE_PATH):
        state = GameState.load(SAVE_PATH)
        print("Сохранение найдено, продолжаем игру.")
    else:
        state = GameState()
        print("Новая игра начата.")

    print("Цель: развивать производственные цепочки и открывать новые, более дешёвые способы получать ресурсы.")

    while True:
        outcome = take_turn(state)
        if isinstance(outcome, tuple):
            kind, payload = outcome
            if kind == "load":
                state = payload
                state.save(SAVE_PATH)  # открытое сохранение становится текущим автосохранением
            continue
        if outcome == "exit":
            break
        if outcome == "reset":
            state = GameState()
            state.save(SAVE_PATH)
            print("Прогресс обнулён. Новая игра начата.")


if __name__ == "__main__":
    main()
