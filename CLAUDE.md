# CLAUDE.md

Guidance for Claude Code в этом репозитории. **Держи этот файл коротким**

**Все ответы — на русском языке**, независимо от языка запроса.

## Что за проект

"Майнкрафт на минималках" — пошаговая idle/incremental игра: руби дерево,
стройте генераторы, исследуй технологии, копай руду в шахте.

Три интерфейса поверх одного `saves/autosave.json`:
- `python tools/web_ui.py` (http://127.0.0.1:8790) — **единственный интерфейс,
  которым реально пользуется игрок.** Логика в `tools/web_ui.py` +
  `tools/web_ui.html`. Открывается в отдельном окне (pywebview), не в
  системном браузере — HTTP-сервер при этом всё равно поднят и на том же
  адресе, страницу можно открыть и вручную в браузере. **Для проверки через
  Playwright** запускай с флагом `--server-only` (`python tools/web_ui.py
  --server-only`, аналогично `content_editor.py`) — это пропускает
  `webview.create_window`/`webview.start()` и сразу отдаёт сервер. Без флага
  playwright всё равно не смог бы управлять нативным окном pywebview и
  пришлось бы ОТДЕЛЬНО открывать страницу его собственным браузером — игра
  визуально запускалась бы дважды (нативное окно + браузер playwright) и
  дольше стартовала (лишняя инициализация WebView2). Сервер сам остановится
  через 45с без heartbeat от закрытой вкладки (`_watchdog`), но это не
  освобождает от явного `TaskStop`/закрытия процесса, если тест упал раньше.
- `python tools/content_editor.py` (http://127.0.0.1:8765) — GUI-редактор
  `content.json`. Тоже отдельное окно pywebview, тот же приём, что у web_ui.
  Есть переносимая exe-сборка (`build_content_editor.ps1` →
  `ContentEditor.exe` в корне репозитория, в git не коммитится) — портативный
  dev-инструмент ДЛЯ ЭТОГО репозитория (редактирует реальный
  `game/content.json`), не то, что отправляется другу.
- `python main.py` — консольная версия. **С 2026-09-02 официально НЕ
  поддерживается как играбельная** (прямое пожелание, не гипотеза): не нужно
  синхронизировать её с web_ui, не нужно гонять её руками для проверки фич,
  не нужно чинить, если она отстала от правки в `engine.py`. `engine.py` при
  этом всё равно обязан оставаться корректным сам по себе — это общий слой.
  Тривиальную правку (пара строк, без отдельного дизайна) в `main.py` можно
  делать заодно, но никогда не обязательно.

Автосохранение живёт в `saves/autosave.json` — в той же папке `saves/`, что и
именованные сохранения ("Сохранить как"/"Открыть", явный выбор игрока в обоих
интерфейсах), просто под зарезервированным именем `autosave` (нельзя создать
именованное сохранение с таким же именем). Раньше автосейв лежал отдельно, в
корне репозитория (`save.json`) — при первом запуске после переноса старый
файл сам переезжает в новое место (`_migrate_legacy_save` в `web_ui.py`/
`main.py`/`content_editor.py`), так что уже начатые партии не теряются.

**Тесты**: `python -m unittest discover tests -v` — все зелёные. Хук
(`.claude/hooks/run_tests_on_core_edit.py`) гоняет их автоматически после
каждой правки `engine.py`/`state.py` — вручную дублировать не нужно. Для
остальных файлов и как финальная страховка перед тем как считать задачу
законченной — гоняй сам.

## Архитектура

```
game/content.json   - ВСЕ данные игры (единственный источник истины для баланса)
game/content.py     - грузит content.json в module-level константы ОДИН РАЗ при импорте
                       (правки content.json на диске не подхватываются без рестарта процесса)
game/state.py       - GameState: dataclass + to_dict/from_dict + save/load
game/engine.py       - вся игровая логика. Не печатает, не читает stdin, не знает про UI.
main.py              - консольный UI поверх engine.py (не поддерживается как играбельный)
tools/web_ui.py      - HTTP API (stdlib, без Flask) поверх engine.py + web_ui.html;
                       окно приложения - pywebview (requirements.txt, единственная
                       внешняя зависимость проекта)
tools/content_editor.py/.html - редактор content.json, НЕ импортирует game/*
tools/content_validation.py    - validate(dict) -> [errors]; тоже НЕ импортирует game/*
```

`game/` ничего не знает о `tools/`. `content_editor.py`/`content_validation.py`
ничего не знают о `game/*` намеренно (см. "Хардкод id" ниже) — не пытайся
"исправить" это импортом, это создаст обратную зависимость tools -> game.
`web_ui.py`/`main.py` — тонкие обёртки, вся логика в `engine.py`.

## content.json: структура

Топ-уровень: `resources`, `locations`, `manual_actions`, `buildings`, `techs`,
`mining_info`, `tools`, `energy_recipes`, `ui`, `time`, а также `mobs`, `skills`,
`soul_recipes` (см. "Охота, навыки, синтезаторы душ" ниже), `quests`/`loot_cases`
(см. "Квестбук и лутбоксы" ниже) и `species`/`genetic_traits`/`mutations`/`genetics`
(см. "Разведение существ (фермы, генетика)" ниже).

Generic-поля построек (есть у ЛЮБОЙ постройки, не завязаны на конкретный id):
`description` (обязательна, валидируется строго), `cost`/`produces`/`consumes`/
`repair_cost`, `breakdown_chance`, `energy_storage` (ёмкость хранилища ОДНОЙ
копии), `active_period` (`null`/`day`/`night`), `compute_power` (лимит РАЗМЕРА
автокрафта), `autocraft_speed` (лимит СКОРОСТИ автокрафта, прокруток/ход),
`autocraft_slots` (доп. одновременные фоновые задачи), `max_count`,
`unlocks_autocraft` (список resource_id), `energy_converter`
(`{max_energy_per_turn}` — делает постройку слотом крафта за энергию),
`soul_collector` (`{slots, accepts_kind}` — делает постройку синтезатором душ, см.
ниже), `automation_hub` (`{slots}` — делает постройку автоматизатором, см.
"Автоматизатор" ниже), `construction_stages` (непустой список этапов вместо
разового `cost`), `upgrades` (список улучшений — тип `scale`/
`energy_efficiency`/`generation_efficiency`/`energy_converter_capacity`/
`breeding_*`/`soul_collector_slots`/`soul_generation_efficiency`/
`automation_slots`, плюс необязательные `repeatable`/`cost_growth`/
`max_purchases` — см. "Repeatable-апгрейды" ниже), `click_delay_ms`, `lab_tier`.

`requires_tech` + `requires_building` — гейт через И (не ИЛИ), есть у построек,
локаций и ручных действий: `requires_building` требует хотя бы одну РАБОТАЮЩУЮ
(не сломанную) копию другой постройки (`engine.working_count`). У построек
допускает цикл (проверяется `find_building_requires_cycle`); у локаций — нет
своего цикл-чекера (разные id-пространства).

`manual_actions.*.location` — необязательное поле; `null` = действие в корне
дерева локаций. Ходить по локациям бесплатно, ход тратит только само действие.

**Крафт на сетке (верстак).** `locations.*.crafting` = `{width, height}` делает
локацию механизмом-верстаком — произвольные `width`/`height` поддержаны от
рождения (настоящий верстак 3×3; строка 2×1 и будущий 9×9 «ужасающий верстак»
не требуют отдельного кода, см. `engine.py: _normalize_grid`/`match_grid`).
`manual_actions.*.pattern` = `{rows: ["AA.", ...], key: {"A": "ore"}}` — узор
рецепта, тот же приём «сетка символов + легенда», что у пиксельных иконок;
необязательный `pattern_mirror: true` засчитывает и зеркальную по горизонтали
раскладку. Наличие этих полей И ЕСТЬ два явных переключателя: локация без
`crafting` работает как раньше (список карточек), действие без `pattern`
внутри механизма тоже остаётся карточкой. Одна ячейка = одна единица ресурса,
и валидатор ТРЕБУЕТ точного совпадения узора с `cost` — два источника истины
тут давали бы тихое расхождение (игрок видит узор, движок списывает `cost`).
Редактор показывает расхождение красной плашкой и даёт кнопку «Записать узор
в cost».

Сеточные действия физически убраны из `VIEW.categories.manual` — граница
«крафт только через раскладывание» живёт в `tools/web_ui.py: build_view`, НЕ
в движке: `do_manual_action` по-прежнему выполняет их как обычно, автоматизация
и докрафт не задеты. Раскладывание и матчинг узора мгновенно считаются на
клиенте (`web_ui.html: craftGridMatchRecipe`) для отклика, но сервер ВСЕГДА
перепроверяет сам (`engine.match_grid`) при реальном крафте
(`POST /api/craft_grid`, крутит цикл «действие + `advance_turn`» `times` раз за
один запрос). Закреплённые рецепты — `state.pinned_recipes`
(`engine.toggle_pinned_recipe`, `POST /api/pin_recipe`) — переживают закрытие
модалки и перезапуск игры; клик по закреплённой плитке кладёт то, что реально
есть на складе (`craftGridPartialLay`), оставляя недостающие ячейки обведёнными
красным, наведение красит сетку призраком заранее, ничего не трогая
(`craftGridApplyHoverToDom`, НЕ через полную пересборку модалки — иначе
пересозданная под курсором кнопка гонялась бы сама с собой по mouseenter/
mouseleave).

Справочник источников (`tools/web_ui.py: resource_sources`/
`sources_summary_view`, `GET /api/sources_summary` + `POST /api/sources`)
отвечает «откуда берётся» по ЛЮБОМУ ресурсу — не только сеточным рецептам:
крафт (сеточный и обычный), ловля дикой особи (`spawn_species`), мутация при
скрещивании, постройка, особь на ферме, душа в синтезаторе, крафт за энергию,
ритуал, шахта, лут с моба (включая шансовую душу), приз лутбокса (с именем
квеста-награды). Чисто по контенту, не по `state` — отвечает на «как вообще
получить», а не «чем я уже владею» (то же решение наоборот, что
`recipe_board_target` в движке, см. его комментарий). Живёт в правой колонке
модалки верстака (`web_ui.html: craftGridBuildSourcesPanel`), с поиском,
фильтром по виду источника и переходом по ингредиентам (история — `sourcesTrail`).

## Карта: тема → где искать в коде

| Тема | Где искать |
|---|---|
| Энергия и хранилища (обрезка в конце хода) | `engine.py`: `ENERGY_RESOURCE_ID`, `energy_capacity`, `is_building_active_now` |
| Шахта: fog-of-war сетка, добыча клеток, щуп-детектор (радиус/типы/счёт/полная подсветка) | `engine.py`: `MINE_LOCATION_ID`, `_generate_mine_field`, `hit_mine_field`, `mine_field_view`, `scanner_tier`, `_scanner_sense_radius`; контент `mine_field` (+`scanner_radius_tiers` - список {tier, radius}, действует самый большой разблокированный, не сумма) + `mining_info`; только в web_ui, у `main.py` нет (см. `tools/web_ui.py`: вызовы `engine.hit_mine_field`/`engine.mine_field_view`) |
| Инструменты и тиры (произвольное число линеек) | `engine.py`: `tool_tier`, `upgrade_tool`, `next_tool_tier_info`, `upgradable_tools`; `web_ui.html: openToolsModal` |
| Дерево технологий, тиры лабораторий | `engine.py`: `tech_unlocks`, `tech_levels`, `lab_tier_capacity`, `research()`; `web_ui.html: renderTechTree/openTechTreeModal` |
| Исследование за ходы (research_turns, апгрейд лаборатории research_speed) | `engine.py`: `tech_research_turns`, `research()`, `_process_research`, `cancel_research`; `state.active_research`; контент `techs.*.research_turns` + `buildings.*.upgrades[].type == "research_speed"`; `web_ui.html: researchProgressRow/renderResearchPanel`, `.tech-node.researching` |
| Улучшения построек (типизированные + repeatable) | `engine.py`: `_apply_building_upgrades`, `_purchased_upgrade_value`, `repeatable_upgrade_cost`, `purchase_building_upgrade`, `building_upgrade_prerequisite`, `effective_building_stats`; `web_ui.html: openBuildingUpgradesModal`; `content_editor.html: openBuildingUpgradesEditorModal` |
| Охота: мобы/боссы, схватка, боевой навык | `engine.py`: `MOBS`, `COMBAT_SKILL_ID`, `search_mob_any`, `summon_boss`, `attack_encounter`, `abandon_encounter`, `_weapon_bonuses`; `state.active_encounter`; `web_ui.py: hunting_view`; `web_ui.html: renderActiveEncounterBlock` |
| Разведение существ: фермы, скрещивание, генетика, синтез | `engine.py`: `GENOTYPE_TRAIT_IDS`, `MUTATIONS_BY_PAIR`, `trait_alleles`, `_cross_genotype`, `breed_creatures`, `spawn_creature`, `analyze_creature`, `synthesize_creature`, `_ensure_farm_instances`, `_process_habitat`; `state.creatures`/`state.farm_instances`/`state.known_alleles`; контент `species`/`genetic_traits`/`mutations`/`genetics`; `web_ui.html: renderFarmSection`, `renderCreatureBankSection`, `openSpeciesBookModal`, `openBreedingModal`, `openGeneticsLabModal` |
| Навыки (generic-прокачка, любое число линеек) | `engine.py`: `skill_level`, `skill_level_effects`, `add_skill_xp`, `skills_view`; `state.skill_xp`; контент `skills`; `web_ui.html: openSkillsModal` |
| Синтезаторы душ (вечный слот-предмет → пассивная продукция) | `engine.py`: `soul_kind`, `soul_collector_accepts_kind`, `soul_collector_capacity`, `insert_soul`, `remove_soul`, `_process_soul_collectors`; `state.soul_slots`; контент `resources.*.soul_kind` + `buildings.*.soul_collector` + `soul_recipes`; `web_ui.html: openSoulSynthesizerModal` |
| Автоматизатор (правила "если ресурс — тогда действие/рецепт энергокрафта", проверяются каждый ход) | `engine.py`: `automation_slots_capacity`, `is_automation_eligible_action`/`is_automation_eligible_energy_recipe`, `automation_eligible_targets`, `add_automation_rule`/`update_automation_rule`/`remove_automation_rule` (принимают `kind`+`target_id`), `process_automation_rules`; `state.automation_rules`; контент `buildings.*.automation_hub` + апгрейд типа `automation_slots`; `web_ui.py: automation_view`; `web_ui.html: openAutomationModal`/`openAutomationRuleEditorModal` |
| Квестбук (вкладка-граф на каждую эру, requires между эрами гейтит без явного ребра), оформление эр/квестов и лутбоксы (кейс = один приз из барабана) | `engine.py`: `is_quest_available`, `_condition_met`, `check_quests`, `_roll_loot_case_prize`, `claim_quest_reward`; `state.completed_quests`/`claimed_quests`; контент `quests` (`era`/`requires`/`icon`/`bg`/`border`) + `eras` (`game/content.py: ERAS`) + `loot_cases`; `web_ui.html: openQuestsModal`/`renderQuestGraph`/`eraTheme`/`buildQuestNode`/`openLootBoxModal`; `content_editor.html: renderQuestGraphView`/`renderEraManager` |
| Repeatable-апгрейды (покупка N раз, цена растёт геометрически) | `engine.py`: `_purchased_upgrade_value`, `repeatable_upgrade_cost`, `building_upgrade_purchase_count`, `farm_instance_upgrade_purchase_count`; контент `upgrades[].repeatable`/`cost_growth`/`max_purchases` |
| Ползунок масштаба ручного действия | `engine.py`: `mechanism_scale_cap`, `effective_manual_action_stats(chosen_scale=...)`; `POST /api/action {scale}` |
| Меню "Механизмы": вкл/выкл N из M копий пассивной постройки (produces И consumes непустые) | `state.building_disabled`; `engine.py`: `is_disableable_building`, `building_active_count`, `set_building_active_count`; `POST /api/set_building_active_count`; `web_ui.html: renderMechanismToggle` |
| Снос постройки (1 копия за вызов, без возврата ресурсов, ход не тратится; сначала сносит сломанную копию, если есть; у ферм/синтезаторов душ/реакторов удаляет весь физический экземпляр, расселяя заселённых особей обратно в банк) | `engine.py: demolish_building`; `POST /api/demolish_building`; `web_ui.html: demolishBuilding` |
| "Механизмы" (постройка без produces/consumes + завязанные действия) | `locations.*.requires_building`, `manual_actions.*.requires_building`, `engine.py: effective_manual_action_stats` |
| Дробные/целые ресурсы | `content.json resources.*.fractional`; `engine.py: _round_amounts`; `content.py: RESOURCE_FRACTIONAL` |
| Приоритет автокрафт-рецептов | `engine.py`: `resource_recipe`, `action_priority`, `set_action_priority`; контент `manual_actions.*.priority`, `state.action_priority` |
| Вероятностный cost/gain у ручных действий | `engine.py`: `_roll_chance_amounts`, `is_manual_action_deterministic`; контент `cost_chance`/`gain_chance` |
| Случайное количество produce (диапазон, равномерное распределение) | `engine.py`: `_is_range_amount`, `_roll_range_amounts`, `_add_amount`/`_scale_amount`; `content_validation.py`: `check_amount_range`; `content_editor.html`: `renderDictAmountInput` |
| Автокрафт (гейты, compute/speed/slots, фон. задачи, партии энергии/многоходовых) | `engine.py`: `ensure_and_pay` (→ `_discover_autocraft_graph`/`_aggregate_autocraft_demand`/`_commit_autocraft_plan`, см. "Известные ограничения"), `_start_energy_batches`, `_busy_manual_job_in_location`/`_new_manual_job`, `compute_capacity`, `autocraft_speed_capacity`, `autocraft_slot_capacity`, `state.autocraft_jobs`/`pending_actions` |
| Сообщение "не хватает ресурсов: ..." | `engine.py`: `_pay_or_defer`, `_direct_shortfall`, `_raw_shortfall`, `_format_shortfall` |
| Галочка "Автокрафт" вкл/выкл | `state.autocraft_enabled`, `engine.toggle_autocraft`; `web_ui.html: #autocraftToggleWrap` |
| Крафт из энергии (преобразователь) | контент `energy_recipes` (+ необязательные `machine`/`consumes`) + `buildings.*.energy_converter`; `engine.py`: `CONVERTER_LOCATION_ID`, `start_energy_recipe`, `_process_energy_jobs`, `energy_converter_capacity`/`_max_energy_per_turn(state, building_id)` |
| Энергосистема: несколько типов машин + приоритеты | `engine.py`: `energy_converter_building_types`, `energy_priority`/`set_energy_priority`, `ProductionReport.energy_shortfall`; контент `buildings.*.energy_priority`; `web_ui.html: openPrioritiesModal` (вкладка "Энергосистема") |
| Многоэтапное строительство | контент `buildings.*.construction_stages`; `engine.py`: `current_construction_stage`, `state.construction_progress` |
| Многоходовые ручные действия | контент `manual_actions.*.duration_turns`; `engine.py`: `do_manual_action`, `_process_manual_jobs`, `cancel_manual_job`; `state.manual_jobs`; `web_ui.html: manualJobProgressRow` (прогресс + отмена и на карточке действия, и в панели «Долгие действия») |
| Задержка после клика (антиавтокликер, клиент-онли) | контент `ui.default_action_delay_ms` + `click_delay_ms`; `web_ui.html`: `tryRunAction`/`applyCooldownVisual` |
| Звук: пресеты, кастомные файлы (действия + ритуалы + 3 глобальных слота), громкость | контент `manual_actions.*.sound`/`ritual_recipes.*.sound` + `ui.sounds` (`background_music`/`lootbox_open`/`button_click`); `web_ui.html`: `SOUND_PRESETS`, `playSound`/`playCustomSound`/`playGlobalSound`, `_sfxVolume`/`_musicVolume`; `web_ui.py`: `sound_url`, `SOUNDS_DIR`; `content_editor.html`: `soundRow` (вызывается и на карточке ручного действия, и на карточке ритуала), вкладка «Настройки» |
| Отмена фоновой задачи | `engine.py`: `cancel_autocraft_job`/`cancel_energy_job`; `web_ui.html: jobCancelButton` |
| Вложенные локации | контент `locations.*.parent_location`; `engine.py: location_children`; `web_ui.html: locationPath` |
| Крафт на сетке (верстак): узор, матчинг, перенос, справочник источников | `engine.py`: `location_crafting`, `action_pattern`, `match_grid`, `_normalize_grid`, `toggle_pinned_recipe`; `state.pinned_recipes`; контент `locations.*.crafting` + `manual_actions.*.pattern`/`pattern_mirror`; валидация - `content_validation.py`: `pattern_shape`, `mirror_shape`, `check_action_pattern`; редактор - `content_editor.html`: `craftingGridRow`, `actionPatternRow`, `openPatternModal`; HTTP - `tools/web_ui.py`: `crafting_view`, `craft_grid_view`, `resource_sources`, `sources_summary_view`, `/api/craft_grid`, `/api/pin_recipe`, `/api/sources`, `/api/sources_summary`; UI - `web_ui.html`: `openCraftGridModal`, `craftGridMatchRecipe`, `craftGridApplyHoverToDom`, `craftGridBuildSourcesPanel` |
| Потолок ресурса (1e9) | `engine.py`: `MAX_RESOURCE_AMOUNT`, `_add_resource` (ЕДИНСТВЕННАЯ точка начисления) |
| Вместимость склада | контент `resources.*.storage`; `engine.py`: `storage_capacity`, `state.storage_tier`, `upgrade_storage`; `web_ui.html: openStorage` |
| Подтверждение автокрафта (модалка "спишется/докрафтится") | `engine.py`: `autocraft_preview`, `resolve_action_cost`; `web_ui.py: autocraft_preview_view`; `web_ui.html: openAutocraftConfirmModal` |
| ETA автокрафта в ходах | `engine.py: autocraft_eta_turns` |
| Обнуление прогресса / именованные сохранения | `web_ui.py`/`main.py`: `SAVES_DIR`, `list_saves`, `safe_save_name`; `GameState.save_name` |

## Критичные инварианты (неочевидны из кода)

- **Энергия не копится без хранилищ**: в конце `advance_turn` запас
  обрезается до `energy_capacity()` (сумма `energy_storage` РАБОТАЮЩИХ
  построек). Ровно тот же паттерн клампа в конце хода — у вместимости склада
  (`storage_capacity`), но НЕ применяется к автокрафту в середине цепочки
  (промежуточные ресурсы оседают и списываются в одном вызове раньше, чем
  наступает конец хода).
- **`advance_turn` — два прохода**: сначала все НЕ-потребители энергии
  (включая генераторов), потом потребители — иначе результат зависел бы от
  порядка вставки в `state.buildings` (dict). Среди потребителей друг с
  другом порядок остаётся "кто раньше встал, тот и забрал" — общий,
  осознанно принятый компромисс для любых разделяемых ресурсов.
- **Время не хранится в `GameState`** — `engine.current_time(state)` считает
  день/час из `state.turn` + `content.json["time"]`.
- **Тир инструмента = позиция в списке** `tools[tool_id]["tiers"]` — как и
  уровень `upgrades[]` постройки, и тир `resources.*.storage.tiers` — реордер
  списка задним числом ломает уже сохранённые ссылки на номер тира в
  существующих `save.json`. Никогда не читай `state.tool_tiers[tool_id]`
  напрямую — только через `engine.tool_tier(state, tool_id)` (дефолт = тир 1).
- **Поиск жилы**: одна попытка = одна руда, шанс по `rarity` ОДИНАКОВ при
  каждом поиске независимо от истории находок (не убывающий пул кандидатов).
  Активна всегда только ОДНА жила — новый поиск заменяет `state.veins`
  целиком, недокопанный остаток теряется.
- **Улучшения построек** (`scale`/`energy_efficiency`/`generation_efficiency`/
  `breeding_*`/`energy_converter_capacity`/`soul_collector_slots`/
  `soul_generation_efficiency`): среди купленных ОДНОГО типа действует только
  самое сильное (не перемножаются) — **ЕСЛИ** апгрейд не помечен
  `repeatable: true`. Repeatable-апгрейд можно купить ЛЮБОЕ число раз подряд
  (до необязательного `max_purchases`), каждая покупка добавляет ещё один
  `value` (см. `_purchased_upgrade_value` — `value * количество_покупок`), а
  цена растёт геометрически: `cost * cost_growth^покупок_уже_сделано` (см.
  `repeatable_upgrade_cost`). Не путать с "несколько РАЗНЫХ апгрейдов одного
  типа" (обычная прогрессия вида "scale x2" → "scale x3", где считается
  максимум, а не сумма) — оба механизма могут сосуществовать у одной
  постройки. Порядок применения: сначала аддитивные тех-эффекты, потом
  мультипликативные улучшения. Покупка следующего уровня прогрессии (позиция
  в списке, НЕ применимо к повторным покупкам ОДНОГО repeatable-апгрейда)
  требует, чтобы предыдущий уровень ТОГО ЖЕ типа уже был куплен.
  `environment_emulation` не может быть `repeatable` (взаимоисключающий выбор,
  не накопление).
- **Дробные ресурсы**: только `resources.*.fractional == true` (в реальном
  контенте — только `energy`) остаются нецелыми. Любой другой ресурс всегда
  округляется В БОЛЬШУЮ сторону (`math.ceil`), и в cost, и в gain, через
  единственную точку `_round_amounts` — это специально НЕ "как выгоднее
  игроку", а "тяжелее для игрока".
- **Несколько типов энергетических машин**: пропускная способность
  (`energy_converter_max_energy_per_turn`) и слоты (`energy_converter_capacity`)
  считаются ПЕР ТИП постройки (по `building_id`), не суммарно — пропускную
  способность наращивают апгрейды типа `energy_converter_capacity`, купленные
  для ТОЙ ЖЕ постройки (не отдельная постройка-усилитель — убрана в пользу
  апгрейдов, см. `_farm_upgrade_multiplier`), значение каждого уровня —
  АБСОЛЮТНАЯ добавка к базе, среди купленных действует только самый сильный
  уровень (не складываются). Квота `0` (не построен/сломан) ≠ `None` (лимита
  нет вовсе) — не путать.
  При нехватке общего пула на несколько типов сразу порядок решает
  `energy_priority`/`set_energy_priority` (больше → тянет первым). Автокрафт
  для рецепта с материальной ценой (`energy_recipes.*.consumes`) НЕ докрафчивает
  её рекурсивно — только ждёт, пока сырьё появится в наличии само.
- **Многоходовое ручное действие** (`manual_actions.*.duration_turns >= 2`) списывает
  `cost` ОДНИМ платежом целиком в момент клика (как `energy_jobs`), затем просто
  тикает (`state.manual_jobs`, одна активная задача НА action_id) `+1 ход` каждый
  `advance_turn` без учёта пропускной способности — `gain`/`gain_chance` разворачиваются
  РОВНО ОДИН РАЗ в момент завершения. Повторный клик, пока задача не закончилась,
  ЗАБЛОКИРОВАН (`ActionResult(False, ...)`, хода не тратит) — и это распространяется на
  ЛЮБОЕ другое многоходовое действие той же локации (`action["location"]`, точное
  совпадение значения, `None` тоже валидная "локация" — корень дерева), не только на
  тот же `action_id`: пока в локации уже тикает один job, запустить в НЕЙ ЖЕ второй
  нельзя (`do_manual_action`). Проверка НЕ рекурсивная по `parent_location` — дочерние
  локации ограничением не связаны (например, многоходовая добыча в шахте не мешает
  охоте в её подлокации). `cancel_manual_job`
  снимает задачу немедленно (без хода) И ВОЗВРАЩАЕТ игроку весь списанный `cost` —
  в этом её поведение НАМЕРЕННО отличается от `cancel_energy_job`/`cancel_autocraft_job`
  (у тех прогресс/прокрутки потрачены по-настоящему на предыдущих ходах, возврату не
  подлежат); накопленные тики (`turns_done`) при отмене всё равно теряются без следа —
  просто потому что сами по себе ничего не стоили. Прогресс виден в двух местах сразу:
  на самой карточке действия (`already_running`/`turns_done`/`turns_total` в
  `build_view`) и в отдельной панели «Долгие действия» (нужна, т.к. действие может
  сейчас идти в другой локации, не видной на экране) — обе точки рендерят один и тот
  же `web_ui.html: manualJobProgressRow`, чтобы не разойтись.
- **Исследование технологий за ходы** (`techs.*.research_turns`, необязательное
  поле — отсутствие/`null`/`1` значит "мгновенно", как раньше) списывает `cost`
  ОДНИМ платежом целиком в момент клика (как многоходовое ручное действие), а
  при `research_turns >= 2` заводит `state.active_research` вместо немедленного
  добавления в `state.techs`. В отличие от `manual_jobs` (своя задача НА
  action_id, несколько РАЗНЫХ действий разных локаций идут параллельно — см. выше
  про ограничение ОДНОЙ локации), слот ГЛОБАЛЬНЫЙ — один
  на всю игру, как `state.active_encounter`/`veins`. Переключиться на ДРУГОЕ
  исследование можно В ЛЮБОЙ момент ОДНИМ кликом — если слот занят чем-то ещё,
  `research()` сам тихо отменяет старое (`cancel_research` — `cost` возвращается
  целиком, накопленные `turns_done` нет, тот же принцип, что `cancel_manual_job`)
  и тут же стартует новое, без отдельного шага "сначала отмените". Порядок
  внутри `research()` намеренный: cost НОВОЙ технологии списывается ПЕРВЫМ
  (после проверки `is_tech_available`/`lab_tier_capacity`), и только ПОСЛЕ
  успешной оплаты отменяется старая — если новую не на что/нечем исследовать,
  старый прогресс остаётся нетронутым, а не стирается впустую. Повторный клик
  на ТУ ЖЕ технологию, что уже идёт, — no-op (явный отказ, ничего не
  переигрывает). Отдельная явная `cancel_research` (кнопка "✕" в UI) по-прежнему
  работает и без переключения — просто освобождает слот. Сокращает время апгрейд построек
  типа `research_speed` (`buildings.*.upgrades[].type`) — АБСОЛЮТНОЕ число ходов
  (не доля, как у `action_duration_reduction`), действует только на техи ТОГО ЖЕ
  тира, что `lab_tier` этой постройки («лаборатория той же эры»), не «лаборатория
  тира N и выше», как у `lab_tier_capacity`. Тир 1 своей лаборатории не имеет
  (см. `lab_tier_capacity`) — тир-1 технологии сократить нельзя в принципе.
- **Автокрафт выключен по умолчанию** для КАЖДОГО ресурса, включается через
  `unlocks_autocraft` работающей постройки; ограничен `compute_power`
  (размер), `autocraft_speed` (скорость/ход), `autocraft_slots` (параллелизм
  фоновых задач). Действие с ХОТЯ БЫ ОДНИМ вероятностным `cost_chance`/
  `gain_chance` < 100% целиком исключено из `resource_recipe` — автокрафт
  видит только детерминированные рецепты. Голое сырьё (пустой `cost`)
  недостижимо для автокрафта в принципе. Действие со случайным диапазоном
  (см. ниже) в `gain` исключено из `resource_recipe` по той же причине.
  **Партии** (с 2026-09-24): многоходовое действие (`manual_action_duration > 1`)
  автокрафт ведёт настоящим `manual_job` (флаг `autocraft`, не растит
  `action_performed_count`) — по одной партии за раз, по тем же правилам «одна
  задача на action_id и на локацию»; крафт за энергию — сразу на ВСЕХ свободных
  преобразователях, сколько нужно (`_start_energy_batches`, диапазон — по `min`).
  Следующую партию запускает повторная попытка отложенного действия, в тот же ход,
  когда закончилась предыдущая. Рецепт поверх партий получает статус `waiting` и
  НЕ заводит свою фоновую задачу (иначе она висела бы вечно: у ресурса с задачей
  спрос не идёт вниз, и следующую партию никто бы не запустил), см.
  `_commit_autocraft_plan`. Скорость/слоты автокрафта партии не тратят.
- **Случайное количество produce** — значение ресурса в `produces`
  (постройки/`energy_recipes`/`soul_recipes`) или `gain` (`manual_actions`)
  может быть объектом `{"min", "max"}` вместо плоского числа — равномерный
  случайный бросок (`_roll_range_amounts`, `random.randint`/`random.uniform`
  для дробных), НЕЗАВИСИМЫЙ от `cost_chance`/`gain_chance` (шанс решает,
  случится ли начисление вообще, диапазон — сколько именно, если да).
  Разрешено ТОЛЬКО на produce-стороне, никогда в `cost`/`consumes`. Бросок
  происходит РОВНО ОДИН РАЗ на явление (клик/ход постройки/завершённый job/
  вставленная душа), в самом конце цепочки — `effective_building_stats`/
  `effective_manual_action_stats` остаются чистыми (без RNG) и лишь
  сдвигают/домножают диапазон целиком (`_add_amount`/`_scale_amount`) тех-
  эффектами и апгрейдами; сам бросок делает вызывающий (`advance_turn`,
  `do_manual_action`, `_process_soul_collectors`, `_process_energy_jobs`)
  непосредственно перед начислением. Постройка с N рабочими копиями бросает
  ОДИН раз за тип и умножает на N (не N независимых бросков); каждая
  вставленная душа синтезатора и каждый завершённый energy-job бросают
  НЕЗАВИСИМО. Действие со случайным `gain` не годится автокрафту (см. выше).
- **Хардкод id (5 констант)**, объявлены в начале `game/engine.py`:
  `ENERGY_RESOURCE_ID`, `MINE_LOCATION_ID`, `CONVERTER_LOCATION_ID`,
  `RECIPE_BOARD_BUILDING_ID`, `COMBAT_SKILL_ID`. Первые три ПРОДУБЛИРОВАНЫ
  ВРУЧНУЮ в `content_editor.html` и `content_validation.py` (JS не
  импортирует Python, `content_validation.py` намеренно не импортирует
  `game/*`) — меняешь id в одном месте, меняй во всех. `RECIPE_BOARD_BUILDING_ID`
  и `COMBAT_SKILL_ID` НЕ защищены — удаление/переименование просто тихо
  отключает фичу (бой останется без множителя урона от навыка), без падения.
  `COMBAT_SKILL_ID` — единственное место, где вообще-generic система `skills`
  (см. ниже) жёстко связана с конкретной механикой (боем); будущая механика
  на прокачке заведёт себе аналогичную константу, саму систему это не тронет.
- **`content_validation.py` намеренно не импортирует `game/*`** — он проверяет
  произвольный несохранённый черновик из редактора, а не состояние диска.
  Общие константы дублируются руками с комментарием "держать в паре с X".
- **Задержка после клика — чисто клиентский UI-эффект** (антиавтокликер),
  сервер её не навязывает и не проверяет — это не защита от недоверенного
  клиента, а защита от собственного автокликера игрока в одиночной игре.
- **Windows**: `main.py` принудительно переключает stdin/stdout в UTF-8 —
  без этого кириллица бьётся при неинтерактивном stdin (Git Bash/MSYS).
- **Автоматизатор** (`buildings.*.automation_hub` + `state.automation_rules`) —
  правило ссылается на цель ДВУХ возможных видов (`rule["kind"]` +
  `rule["target_id"]`, см. `engine.AUTOMATION_TARGET_KINDS`): `"manual_action"`
  (id из `manual_actions`) или `"energy_recipe"` (id из `energy_recipes`) —
  архитектурно разные системы (`state.manual_jobs` vs `state.energy_jobs`), но
  правило и его условия работают с обеими СИММЕТРИЧНО, см.
  `process_automation_rules`.
  - `manual_action`: допускает только детерминированные действия (та же
    граница, что у автокрафта: без вероятностного `cost_chance`/`gain_chance` и
    без диапазона в `gain`, см. `is_automation_eligible_action`), но
    ДЛИТЕЛЬНОСТЬ (`duration_turns`) значения не имеет — многоходовые действия
    РАЗРЕШЕНЫ. `state.manual_jobs` по-прежнему держит только ОДНУ активную
    задачу НА `action_id` ГЛОБАЛЬНО (не на правило/постройку) —
    `do_manual_action` сам отказывает повторному запуску, пока job не
    завершится, `process_automation_rules` в этом случае просто получает
    `ok=False` и молча пропускает ход для этого правила, без очереди и без
    форсированной отмены. Это осознанная ЦЕНА автоматизации многоходового
    действия: если слот занят (в т.ч. занят самим игроком вручную), освободить
    его для повторного авто-запуска можно только явной `cancel_manual_job` —
    автоматика сама ничего не отменяет. Отчёт `report.automation_fired` в этом
    случае содержит сообщение о СТАРТЕ ("Начато: ... (займётся N х.)"), а не о
    реальном получении ресурса — сам `gain` начислится позже, в
    `_process_manual_jobs`, уже без пометки, что job запустила именно
    автоматизация.
  - `energy_recipe`: любой существующий рецепт допустим без доп. ограничений
    (у `energy_recipes` вообще нет `cost_chance`/`gain_chance`, а диапазон в
    `produces` не мешает — бросок происходит уже ВНУТРИ `_process_energy_jobs`
    при завершении, автоматизация просто вызывает `start_energy_recipe` один
    раз за срабатывание). Тот же принцип цены: если все преобразователи
    подходящего типа заняты (в т.ч. рецептом, запущенным вручную),
    `start_energy_recipe` вернёт `ok=False`, правило молча не сработает —
    освободить слот раньше срока можно только `cancel_energy_job`. В отличие
    от `manual_action`, ограничения "один активный job на весь id" тут нет —
    несколько job'ов ОДНОГО `recipe_id` могут идти параллельно в разных
    преобразователях, но один вызов `start_energy_recipe` занимает РОВНО ОДИН
    свободный слот за раз.
  - ОБЩЕЕ для обоих видов: правило на ещё не открытую цель (тех для действия
    ИЛИ для рецепта) можно настроить заранее — `process_automation_rules`
    молча пропускает его, пока цель не откроется, сработает само после этого.
    `_process_manual_jobs`/`_process_energy_jobs` идут РАНЬШЕ
    `process_automation_rules` в `advance_turn` — поэтому правило без условий
    на завершившийся в этот же ход job (действие или рецепт) видит свободный
    слот и перезапускает его СРАЗУ, без простоя в один ход ("бесшовный
    конвейер"). Несколько правил на одну постройку (или на разные постройки)
    делят общий ресурс ПОСЛЕДОВАТЕЛЬНО, в порядке списка — тот же осознанный
    компромисс "кто раньше встал, тот раньше взял", что и везде в
    `advance_turn` для разделяемых ресурсов. Правило НЕ привязано к локации
    постройки-автоматизатора и не обязано быть постройкой того же типа, что
    цель (например, `energy_recipe`-правило не обязано стоять НА
    преобразователе) — можно поставить ЛЮБОЕ подходящее действие/рецепт игры
    целиком — это сознательный выбор в пользу универсальности, а не
    "автоматизация только своей мастерской".

## Охота, навыки, синтезаторы душ

- Мобы/боссы (`mobs`) ссылаются на локацию через `location` — ЛЮБАЯ локация
  может быть "Охотой" (не хардкод, в отличие от `MINE_LOCATION_ID`), просто
  проверяется, ссылается ли на неё хоть один моб.
- Схватка (`state.active_encounter`) ГЛОБАЛЬНАЯ — одна на всю игру, как
  `state.veins`. Новый поиск/призыв заменяет её целиком, недобитая цель
  теряется без возврата затраченного на призыв.
- Обычный моб — рандомная встреча (`search_mob_any`, по `rarity`, как жила),
  душа выбивается ШАНСОМ при убийстве (`soul_id`/`soul_chance`, домножается
  на `soul_chance_multiplier` лучшего владеемого оружия). Босс — ТОЛЬКО
  ручной призыв (`summon_boss`, платит `summon_cost` сразу) — у боссов
  НАМЕРЕННО нет `soul_id`/`soul_chance` (валидатор это требует): душа босса
  добывается только крафтом (обычный `manual_actions` рецепт из лута), без
  нового кода в движке.
- Лут/опыт/душа выдаются ОДНИМ пакетом при финальном убийстве, не по частям
  за удар (в отличие от добычи жилы, где руда капает за каждый удар).
- Оружие — обычные `tools` с `weapon: true`; боевые поля живут НА ТИРЕ
  (`damage_multiplier`/`instant_kill_max_hp`/`loot_multiplier`/
  `soul_chance_multiplier`), не на самом инструменте. Несколько
  линеек-оружия действуют ОДНОВРЕМЕННО — максимум по каждому полю среди ВСЕХ
  владеемых (`_weapon_bonuses`), не только одной "экипированной". Меч-ваншот —
  обычно отдельная линейка из 2 тиров: тир 1 "не выковано" (без полей), тир 2
  сам меч с `instant_kill_max_hp`.
- Синтезатор душ (`buildings.*.soul_collector: {slots, accepts_kind}`) — душа,
  вставленная в слот (`state.soul_slots`), ВЕЧНАЯ (не расходуется), пока
  копия постройки не сломана/не разобрана или игрок сам её не вынул.
  Продукция — `soul_recipes[soul_id]`, та же форма (`produces`/
  `produces_chance`), что `gain`/`gain_chance` ручного действия. Вставка/выемка
  живёт в СВОЕЙ модалке (кнопка "Души" на карточке постройки, см.
  `web_ui.html: openSoulSynthesizerModal`), не прямо в панели построек —
  тот же приём, что "Особи" у ферм.
  `soul_kind` (`resources.*.soul_kind`, например `"моб"`/`"босс"`) — тег вида
  души (`engine.soul_kind`), тот же generic-принцип, что `species.*.family`.
  `accepts_kind` (`engine.soul_collector_accepts_kind`) — ЕДИНЫЙ тег, который
  принимает ЭТА постройка (зеркало `breeding_accepts_family`): синтезатор
  примет ЛЮБУЮ душу с совпадающим `soul_kind`, а не перечисленный вручную
  список id — "разные типы синтезаторов под разные души" получается просто
  разными непересекающимися тегами, без правки построек при добавлении нового
  моба/босса того же тега. Банк подходящих душ в модалке (`VIEW.soul_bank`,
  отфильтрован по `accepts_kind` на клиенте) — полная аналогия с банком особей
  ферм (`creature_bank` + `breeding_accepts_family`).

## Разведение существ (фермы, генетика)

Отдельная подсистема (пасеки/фермы существ, менделевское наследование,
мутации при скрещивании, генетическая лаборатория) — контент `species`
(один вид = одна аллель признака `"species"`), `genetic_traits` (остальные 4
признака: `lifespan`/`output_multiplier`/`active_period`/`environment`, общие
для всех видов), `mutations` (рецепты скрещивания пар видов) и `genetics`
(глобальные настройки: `synthesis_cost`, `environment_mutation_chance`).

- **Особь** (`state.creatures[id]`) — генотип из 5 признаков
  (`engine.GENOTYPE_TRAIT_IDS`), у каждого признака ПАРА аллелей (как в
  реальной генетике: одна от каждого родителя). Выражается (фенотип) через
  `creature_phenotype`/`_phenotype_allele` — при разногласии побеждает
  доминантная аллель, а если обе доминантны или обе рецессивны — первая в
  паре (детерминированно, порядок в паре сам по себе смысла не несёт кроме
  этого правила). Дикая особь (пойманная, не выведенная) ГОМОЗИГОТНА по
  построению на каждый признак (`_roll_wild_genotype`) — скрытых генов у
  только что пойманной особи нет, разнообразие появляется только через
  скрещивание/мутацию.
- **Постройки-фермы** (`buildings.*.breeding_slots > 0`, напр. пасека) имеют
  ИНДИВИДУАЛЬНЫЕ ЭКЗЕМПЛЯРЫ (`state.farm_instances[instance_id]`), НЕ единый
  агрегат по типу — в отличие от большинства остальных построек в игре.
  `state.buildings`/`state.broken` остаются авторитетным источником истины
  (working_count/ремонт/поломка/достижения/requires_building всё ещё считают
  по агрегату); `_ensure_farm_instances` идемпотентно ДОБИРАЕТ недостающие
  экземпляры при каждом обращении (вызывается в начале `farms_view`/
  `house_creature`/`purchase_farm_instance_upgrade`/`build`/`repair` и один раз
  в начале `advance_turn`) — это одновременно и создание новой фермы после
  `build()`, и ленивая миграция старых сохранений, где `state.farm_instances`
  ещё пуст. У КАЖДОГО экземпляра свои купленные апгрейды
  (`instance["upgrades"]`, не `state.building_upgrades[building_id]`) и свой
  слот-лимит (`farm_instance_slot_capacity`) — апгрейд, купленный на одном
  экземпляре, НЕ действует на другую копию той же постройки.
- **Заселение** (`house_creature`/`release_creature`) не тратит ход (как
  `insert_soul`/`toggle_autocraft`) — особь просто числится в
  `creature["farm_instance_id"]` либо `None` (в "банке").
  `_process_habitat` в `advance_turn` (ДО крафтов за энергию/автокрафта)
  прогоняет только ЗАСЕЛЁННЫХ особей: `produces`/`consumes` её вида (как у
  обычной постройки — нехватка `consumes` не убивает, просто нет продукции в
  этот ход), домноженные на `output_multiplier` фенотипа и на апгрейд
  `breeding_output` ИМЕННО этого экземпляра фермы. `active_period` фенотипа
  работает как у построек — вне своего времени особь простаивает (не растит
  `turns_alive`, не может умереть в этот ход). При достижении `lifespan`
  (фенотип) особь умирает и удаляется из `state.creatures` целиком — ЕСЛИ у
  экземпляра фермы нет апгрейда `breeding_immortality` (без него смертны ВСЕ
  особи независимо от генотипа).
- **Скрещивание** (`breed_creatures`/`_cross_genotype`) требует работающий
  механизм-МЕХАНИЗМ (generic-поле `buildings.*.breeding_station`, НЕ хардкод
  id — тот же принцип, что `energy_converter`/`genetics_lab`) — сама ферма
  (пасека) в скрещивании не участвует, только расселяет уже готовых особей.
  Признаки `lifespan`/`output_multiplier`/`active_period`/`environment` —
  обычное менделевское расщепление (случайная аллель от каждого родителя
  независимо, без сцепления между признаками); `environment` дополнительно
  может ПОЛНОСТЬЮ переиграться заново с вероятностью
  `genetics.environment_mutation_chance` — отдельная "мутация среды", не
  связанная с мутацией вида. Признак `species` решается ОТДЕЛЬНО и ВСЕГДА
  даёт чистокровного потомка (никогда не смесь): по `frozenset({вид_A,
  вид_B})` (`MUTATIONS_BY_PAIR`) ищутся ОТКРЫТЫЕ (`requires_tech`/
  `requires_building`) мутации, один общий `random.random()` размечает
  отрезок `[0, 1)` — каждой мутации кусок `chance * _breeding_mutation_multiplier`,
  остаток делится ПОПОЛАМ между чистокровными потомками родителя A и родителя
  Б (а не всегда достаётся одному и тому же). Скрещивание списывает ОБОИХ
  родителей целиком и ВСЕГДА бесплатно сверх этого; ребёнок добавляется в
  банк НЕОПОЗНАННЫМ. Тратит ход.
- **Опознание/синтез**: `analyze_creature` (не тратит ход, требует
  `genetics_lab`) — ЕДИНСТВЕННЫЙ способ узнать полный генотип особи;
  автоматического опознания по времени в ферме нет, неопознанная особь может
  жить и производить "вслепую". `synthesize_creature` штампует гомозиготную
  особь с ВЫБРАННЫМ игроком геномом мимо RNG за фиксированную
  `genetics.synthesis_cost` — каждая выбранная аллель должна быть уже в
  `state.known_alleles` (пополняется `analyze_creature`/при спавне уже
  опознанной особи), иначе игрок мог бы синтезировать признак, который
  никогда не видел.
- Терминология UI: "Пасека" — постройка-ферма (примерный аналог Forestry из
  Minecraft), "Книга видов" (`species_catalog_view`/`openSpeciesBookModal`) —
  справочник ВСЕХ видов и рецептов скрещивания для них независимо от того,
  встречал ли их игрок (в отличие от `known_alleles_view` — только уже
  встреченные аллели).

## Квестбук и лутбоксы

- Квест (`quests`) — `{name, description, era, requires, conditions, reward_case,
  icon?, bg?, border?}`. `requires` ссылается на ДРУГИЕ КВЕСТЫ (не на
  технологии). `era` — ГРУППИРОВКА В ОТДЕЛЬНЫЕ ГРАФЫ: каждая эра — своя вкладка
  со своим независимым графом (см. ниже), НЕ колонка одного общего графа (как
  было раньше и как до сих пор устроено у `techs.*.tier`) и никак не связана с
  технологиями; само число остаётся ключом-позицией (как тир инструмента), а
  ЕГО ОФОРМЛЕНИЕ (имя вкладки, главный цвет) живёт отдельно в top-level
  `eras` — `{"<era>": {"name", "color"}}`, необязательный раздел (`game/
  content.py: ERAS`): у era без записи тут UI сам подставляет дефолт "Эра N" +
  цикличную палитру (`tierBandColor`/`tierBandColorEditor`), тем самым старый
  контент без `eras` продолжает работать как раньше. `content_editor.html`
  держит эту секцию УДОБНОЙ для правки через `renderEraManager` (вкладка
  "Граф" у квестов) — переименование/перекраска, добавление, реордер (кнопки
  ↑/↓ — `swapEras`, меняет местами оформление ДВУХ id И `era` у всех их
  квестов, сам номер остаётся позицией) и удаление (только если ни один квест
  этой era не использует, см. `eraUsageCount`). `icon`/`bg`/`border` —
  необязательные per-квестовые оверрайды узла графа: `icon` подменяет значок
  ТОЛЬКО для статусов `completed`/`claimed` (у `locked`/`available` значок
  означает статус, а не сам квест), `bg`/`border` подменяют цвет узла целиком
  вместо цвета, унаследованного от `era`. Оба редактора рисуют эти поля живым
  превью узла (`content_editor.html` — в самом графе, хотя там нет игрового
  статуса; `web_ui.html: buildQuestNode` — в реальной игре).
- `conditions` — форма `{type, ...}` (унаследована от убранной из игры механики
  достижений — она ЦЕЛИКОМ удалена в пользу квестбука; `CONDITION_TYPES`/
  `renderConditions`/`CONDITION_FIELDS`/`_condition_met` — переиспользуемые
  общие имена без "achievement" в названии). Типы: `resource_at_least`,
  `building_count_at_least`, `total_buildings_at_least`, `tech_researched`,
  `tech_count_at_least`, `all_techs_researched`, `turn_at_least`,
  `tool_tier_at_least`, `species_discovered_at_least`, `species_discovered`
  (конкретный вид, а не счётчик — например «вывести/поймать пчелу»),
  `manual_action_performed_at_least` (действие УСПЕШНО дало результат —
  `state.action_performed_count`, растёт при начислении gain: на мгновенном
  клике сразу, на многоходовом — только по завершении job'а, не на старте),
  `building_upgrade_purchased` (апгрейд куплен хоть раз — `building` +
  `upgrade`, id улучшения уникален только ВНУТРИ постройки), проверяются
  через И.
- `requires` — ГЕЙТ ДОСТУПНОСТИ: `is_quest_available` (буквальная копия
  `is_tech_available`, только сверяется с `state.completed_quests`, а не
  `state.techs`) — квест, у которого хоть один prerequisite ещё не выполнен, не
  проверяется вообще (сами `conditions` не читаются). `check_quests` гоняется
  каждый ход, сначала фильтрует по `is_quest_available`, затем проверяет
  `conditions` и один раз фиксирует выполнение в `state.completed_quests` —
  НАВСЕГДА, повторно не переоценивается, даже если условие потом стало бы
  ложным (потрачен ресурс и т.п.). Это и даёт "волновое" открытие графа: квест B
  с `requires: [A]` физически не может выполниться раньше A.
- Единственная награда квеста — лутбокс (`reward_case`, ссылка на `loot_cases`).
  Игрок забирает её сам отдельным кликом ("Открыть лутбокс") —
  `claim_quest_reward`, НЕ тратит ход (тот же принцип, что `insert_soul`/
  `remove_soul`). Требует `quest_id in state.completed_quests` и ЕЩЁ НЕ
  `state.claimed_quests` — открыть один и тот же кейс дважды нельзя.
- `loot_cases[id].prizes` — БАРАБАН лутбокса: список `{resource, amount, weight}`.
  При открытии выпадает РОВНО ОДИН приз (`_roll_loot_case_prize` —
  `random.uniform(0, сумма_весов)`, вероятность приза пропорциональна его weight
  среди суммы весов ВСЕХ призов этого кейса) — это НЕ то же самое, что
  `produces`/`produces_chance` у `soul_recipes` (там КАЖДЫЙ ресурс начисляется
  независимо, тут выбирается ровно один из списка). `ActionResult.prize`
  (`{resource, amount}` или `None`) — выпавший приз, отдельно от `message`:
  нужен UI, чтобы анимировать прокрутку барабана до РЕАЛЬНОГО результата, а не
  подделывать его заново на клиенте (`web_ui.html: openLootBoxModal`, сервер уже
  начислил ресурс к моменту ответа — анимация чисто косметическая).
- Игровой UI (`web_ui.html: openQuestsModal`) — вкладка на каждую эру
  (`questsActiveEra`, переживает переоткрытие модалки, как `prioritiesActiveTab`
  у приоритетов). Внутри вкладки `renderQuestGraph(era)` рисует граф ТОЛЬКО
  квестов этой эры, колонки — по локальной глубине зависимостей ВНУТРИ эры
  (`computeQuestEraLevels`). `requires` на квест из ДРУГОЙ эры по-прежнему
  реально гейтит доступность (проверяется `is_quest_available` как обычно) —
  просто НЕ рисуется линией, раз тот узел физически не на этой вкладке
  (`drawQuestEdges`: `nodeEls[reqId]` для чужой эры просто не находится, ребро
  тихо пропускается); `renderQuestDetail` вместо этого подписывает такие
  требования текстом с пометкой "(эра N)". Узел — `.quest-node` (НЕ `.tech-node`
  — отдельный класс с декоративной обводкой: скошенный угол через `clip-path` +
  двойная рамка цвета статуса + значок-жетон статуса в углу узла,
  `QUEST_STATUS_ICON`), красится по `status` (`locked`/`available`/`completed`/
  `claimed`, 4 состояния вместо 3 у техов, т.к. есть отдельный шаг "забрать
  награду").
- Редактор контента (`content_editor.html`, вкладка "Квесты", графовый вид) —
  тоже вкладка на каждую эру (`questGraphEra`), каждая — свой независимый канвас
  (`computeQuestPositions(era)`, offset колонок всегда с нуля, НЕ единый граф со
  сквозной нумерацией колонок, как раньше). Перетаскивание узла мышью сохраняет
  позицию в `quests.*.pos` (как `techs.*.pos`), таскание за кружок-хэндл создаёт
  `requires`-связь МЕЖДУ ВИДИМЫМИ (т.е. одноэровыми) узлами (с проверкой цикла,
  `wouldCreateQuestCycle`/`wouldCreateCycle` — не даёт создать цикл через уже
  существующую цепочку, проверка идёт по ВСЕМ квестам, не только текущей эры),
  клик по ребру удаляет связь, двойной клик по узлу открывает карточку в списке.
  Requires НА ДРУГУЮ ЭРУ мышью не потянуть (целевой узел не на этой вкладке) —
  выставляется чекбоксом в карточке списка (вкладка "Список" — там `requires` не
  ограничен эрой). Список квестов и граф технологий держат ПОЛНОСТЬЮ раздельное
  DOM/state (`questGraph` vs `techGraph`, `selectedQuestNode` vs
  `selectedTechNode` и т.д.) — не общий код, а параллельная копия, чтобы
  перетаскивание в одном графе не могло задеть состояние другого.
  `find_requires_cycle` в `content_validation.py` — единственное реально общее
  (не дублированное) звено: он не завязан на техов и не завязан на эры,
  используется и для `techs.*.requires`, и для `quests.*.requires` (цикл через
  requires на квест из другой эры так же запрещён, как и внутри одной).

## Веб-UI (`web_ui.html`): общие паттерны

- Один глобальный `VIEW` = последний ответ сервера; после каждого действия
  полностью заменяется, каждый `renderX()` строит DOM заново (без diffing).
- Все модалки — `div.modal-overlay`; если модалка открыта, `renderAll()`
  пересобирает её заново после действия (иначе апгрейд/покупка прямо из
  модалки не обновили бы её же содержимое).
- `VIEW.turn` — это уже "наступающий" ход (инкрементится в начале
  `advance_turn`); для подписи "что произошло только что" нужно `VIEW.turn - 1`.
- Модалка подтверждения автокрафта показывается ПЕРЕД любым действием,
  которое реально потребует докрафта — единая точка `doAction()`.

## content_editor.html: общие паттерны

- `DATA` — единый мутируемый объект всего content.json в памяти; ничего не
  пишется на диск до явного "Сохранить" (`validate()` → бэкап в
  `game/content_backups/` → запись).
- Все сущности после создания получают неизменяемый id.
- `techSelect`/`buildingRequirementSelect` — кнопка + модалка выбора (граф
  для техов, поиск по списку для построек), не голый `<select>`.
- `renderDictEditor` — общий виджет для словарей ресурс→количество.
- Специфичные для одной подсистемы поля (autocraft/energy_converter/
  requires_tool_tiers/upgrades) живут в отдельных модалках "Настроить", а не
  инлайн в карточке — у построек 25+ полей, инлайн не масштабируется.
- Карточки сущностей свёрнуты по умолчанию (`makeCardCollapsible`); новая
  сущность открывается сразу развёрнутой.
- Порядок сущностей в `content.json` = порядок отображения в игре;
  `orderControls(...)` даёт ↑/↓/+ на вкладках Ресурсы/Постройки/Технологии/
  Локации/Ручные действия/Квесты. НЕ применено к `mining_info` (порядок
  не важен) и к спискам тиров (`tools[...]["tiers"]`, `upgrades[]`,
  `storage.tiers`) — там порядок = номер тира, реордер опасен.

## Тесты

`tests/fixtures.py` — полностью самодостаточный контент, НЕ связан с реальным
`content.json` (кроме намеренного совпадения id шахты). `patched_engine_content()`/
`patched_content()` — контекст-менеджеры для подмены module-level констант на
время теста. `tests/test_content_validation.py::RealContentTests` гоняет
`validate()` на настоящем `game/content.json` — должен быть пуст всегда.
`tests/test_combat_and_souls.py` — навыки/охота/синтезаторы душ/repeatable-
апгрейды (та же фикстура: `hunting_loc`, `MOBS`/`SKILLS`/`SOUL_RECIPES`, тул
`sword`, постройка `soul_synth`, repeatable-апгрейд на `apiary`).

## Известные ограничения

- `state.history` растёт бесконечно, без ротации.
- `effective_building_stats`/`effective_breakdown_chance` пересчитываются с
  нуля на каждый вызов — на РЕАЛЬНОМ content.json (36 построек, 19 техов) это
  ~0.4 мс/ход, замерено профилированием (2026-09-10). `advance_turn` теперь
  считает и саму статистику постройки (`effective_building_stats`), и
  `_matching_effects` (O(число техов), общий для неё и
  `effective_breakdown_chance`) РОВНО ОДИН раз на тип постройки за ход
  (раньше — по два раза каждую, см. код: `stats_cache`/`effects_cache`).
  Несмотря на это, синтетический наихудший случай (300 построек × 3 копии,
  60 техов, каждый тех через wildcard `"building": "*"` бьёт по ВСЕМ
  постройкам, поломки выключены, т.е. все 300 работают каждый ход) всё равно
  даёт ~27 мс/ход и рост, близкий к квадратичному по ширине контента
  (постройки × техи): 50/100/200 построек → 0.6/1.9/7.6 мс/ход (рост x4
  построек дал рост x12 времени). Если контент вырастет на порядок(-и) от
  реального — следующий шаг именно здесь: полноценный кэш
  `effective_building_stats`/`_matching_effects` по ключу
  `(building_id, frozenset(techs))`, инвалидируемый в `research()` (сейчас
  сознательно не сделан — techs меняются редко, но реализация кэша с
  инвалидацией сама по себе источник багов, не стоит того при текущем
  масштабе контента).
- **Автокрафт на реконвергентных цепочках (несколько рецептов делят общее
  сырьё) — ИСПРАВЛЕНО 2026-09-10, архитектурная переделка резолвера.** Раньше
  `_ensure_available` работал наивной рекурсией "нужно → произвёл → тут же
  потратил" без памяти между ветками — если ДВА разных рецепта делили один и
  тот же ресурс ниже по цепочке (например, "шестерня" и "пластина" из одного
  "слитка"), второй рецепт заставал слиток уже потраченным первым и заново
  выводил его с нуля. На самоподобном "дереве из ромбов" это давало ЧИСТУЮ
  ЭКСПОНЕНТУ (подтверждено профилированием): 49151 вызовов / **1.1 секунды**
  на глубине 14.

  Теперь резолвер — три прохода по ДЕРЕВУ РЕЦЕПТОВ ЦЕЛИКОМ, а не рекурсия по
  веткам (`_discover_autocraft_graph` → `_aggregate_autocraft_demand` →
  `_commit_autocraft_plan`, все три вызываются из `ensure_and_pay`):
  1. `_discover_autocraft_graph` — строит структуру (что из чего, через
     какое действие) ОДИН раз на resource_id (не на каждое его появление в
     дереве), 3-цветным DFS с защитой от циклов; даёт топологический порядок
     `ingredient_first` (сырьё раньше того, кто его ест) и `preorder`
     (порядок первого обнаружения — нужен только для приоритета сообщений
     об ошибке, см. ниже).
  2. `_aggregate_autocraft_demand` — идёт по графу В ОБРАТНОМ порядке
     (потребитель раньше своего сырья), СУММИРУЯ спрос на каждый resource_id
     от ВСЕХ его потребителей разом, прежде чем считать fires — поэтому
     каждый узел производится (посчитан) ровно один раз на весь запрос, а не
     по разу на ветку. Здесь же все гейты, не зависящие от готовности
     ингредиентов (не_открыт/технология/цикл/мощность процессора).
  3. `_commit_autocraft_plan` — идёт по графу КАК ЕСТЬ (сырьё раньше), решает
     то, что зависит от статуса ингредиентов (застрял ли кто-то из них в
     фоновой задаче → каскадом откладывает и родителя) и от порядка (слоты
     автокрафта, свободные преобразователи энергии) — тот же порядок, что и
     раньше, даёт вставку фоновых задач в `state.autocraft_jobs` в правильной
     последовательности (глубокие сырьевые задачи первыми, см.
     `_process_autocraft_jobs`).

  Сложность стала пропорциональна размеру самого дерева рецептов (вершины +
  рёбра), а не числу путей до общего ресурса — та же глубина 14 теперь
  **~0.4 мс**, рост от глубины 14 до 40 линейный (регрессия:
  `tests/test_engine.py::AutocraftReconvergentGraphTests`).

  Если находок НЕСКОЛЬКО одновременно (например, сразу два независимых
  ресурса без рецепта) — при старой рекурсии игрок увидел бы ту, что
  встретилась первой в порядке cost → recipe-ингредиенты; чтобы не привязывать
  это к порядку агрегации (обратный, потребитель раньше сырья), находки не
  прерывают проход, а копятся в `failures`, и в конце выбирается та, что имеет
  наименьший `graph.preorder` — тот же самый порядок, что дал бы старый код.

  Побочный эффект (не баг, специально сохранён) — раньше `compute_power`/
  `autocraft_speed` проверялись НЕЗАВИСИМО на fires каждой отдельной ветки,
  так что несколько веток, СОВМЕСТНО делящих ресурс, в СУММЕ могли превысить
  лимит процессора/скорости за один и тот же вызов, хотя каждая по отдельности
  укладывалась — теперь проверка идёт на уже АГРЕГИРОВАННЫЙ fires, как и
  задумано лимитом (то есть заодно и лишний скрытый баг закрыт).

  `_raw_shortfall`/`autocraft_preview` (сообщение "не хватает сырья"/окно
  подтверждения) этим фиксом НЕ затронуты — они реактивные (вызываются один
  раз при неудачном действии или открытии модалки, не каждый ход) и
  используют свой более старый, per-branch алгоритм
  (`_accumulate_raw_shortfall`/`_preview_node`), который частично гасит
  повтор через сохранение остатка округления между вызовами, но при
  экстремально глубокой рёконвергентной цепочке потенциально тоже мог бы
  затормозить при ОТКРЫТИИ модалки/тексте ошибки (не при обычной игре). Если
  когда-нибудь появится жалоба именно на это — тот же трёхпроходный подход
  применим и здесь.
- Удаление контента (например, руды из `mining_info`), пока в `save.json` уже
  есть ссылка на неё (`state.veins[...]`), может уронить движок KeyError —
  обратная совместимость сохранений при удалении контента не гарантируется.

## Как эффективно работать в этом репозитории (заметка для Claude)

- **Отвечай всегда на русском** — это не пожелание, а жёсткое требование
  (см. самое начало файла), даже если запрос/код/ошибка на английском.
- Репозиторий под локальным git (без remote, ничего никуда не запушено) —
  заведён специально как сетка безопасности для рискованных правок. Перед
  рефакторингом `engine.py` делай маленькие атомарные коммиты после КАЖДОГО
  самостоятельного шага (не одним гигантским диффом в конце) — откатить один
  шаг сильно дешевле, чем распутывать одну большую правку.
- `engine.py` — 3700+ строк. НЕ читай его целиком, если не готовишь большой
  рефакторинг архитектуры — сверься с таблицей "Карта: тема → где искать в
  коде" выше, найди нужные имена функций через grep, читай `Read` точечно
  (offset/limit) по найденным диапазонам строк.
- Физически разбивать `engine.py` на несколько файлов обдумано и ОТКЛОНЕНО
  (сессия 2026-09-10): рискованно (циклические импорты между разделами,
  которые делят приватные хелперы вроде `_add_resource`/`ActionResult`) ради
  сомнительной выгоды — точечное чтение по карте выше и так решает проблему
  токенов без риска сломать инварианты. Если контент вырастет НАМНОГО (не
  просто "стало неудобно читать") — можно пересмотреть, но это отдельный
  осознанный разговор с пользователем, не мелкая правка по пути.
- Тесты быстрые (620+, ~3 сек). После правки `engine.py`/`state.py` их
  автоматически гоняет хук (см. выше) — не дублируй руками. Для остальных
  файлов, и в любом случае как финальная проверка перед завершением задачи —
  `python -m unittest discover tests -v` обязателен, отговорок пропустить
  нет.
- При правке горячих путей (`advance_turn`, автокрафт-резолюшн) замеряй ДО и
  ПОСЛЕ (`cProfile`/`time.perf_counter` на синтетическом контенте через
  monkey-patch `game.content`/`game.engine`, как в `tests/fixtures.py`) —
  не оптимизируй вслепую и не добавляй кэш без измерения, что он реально
  нужен (см. "Известные ограничения" выше — конкретные цифры уже есть).
