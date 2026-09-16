# Все данные игры живут в content.json - ресурсы, ручные действия, постройки,
# технологии, добыча, кирка. Этот файл просто загружает их и раскладывает по тем же
# именам, что были раньше, так что engine.py и main.py вообще не заметили миграции.
#
# Редактировать контент можно и вручную в content.json, и через GUI-редактор:
#   python tools/content_editor.py
from __future__ import annotations

import json
from pathlib import Path

_DATA_PATH = Path(__file__).with_name("content.json")

with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _DATA = json.load(_f)

RESOURCE_NAMES: dict[str, str] = {r["id"]: r["name"] for r in _DATA["resources"]}
RESOURCE_ORDER: list[str] = [r["id"] for r in _DATA["resources"]]
# Пользовательская пиксельная иконка ресурса ({"grid": [...], "palette": {...}}),
# если её нарисовали в редакторе контента. Раздача UI ищет тут, а если нет - берёт
# свою встроенную иконку по умолчанию.
RESOURCE_ICONS: dict[str, dict] = {r["id"]: r["icon"] for r in _DATA["resources"] if r.get("icon")}
# Дробный ли ресурс (можно копить/тратить нецелым числом, как энергию) или целый
# (значение по умолчанию, если поле не указано - весь контент до этого поля был
# фактически целочисленным). См. game/engine.py:_round_amounts - целые ресурсы
# ВСЕГДА округляются в большую сторону при расчёте эффективных produces/consumes
# (после умножения на множитель улучшения постройки), дробные не трогаются.
RESOURCE_FRACTIONAL: dict[str, bool] = {r["id"]: bool(r.get("fractional", False)) for r in _DATA["resources"]}
# Тег "вида" души (resources.*.soul_kind, например "моб"/"босс") - тот же
# generic-принцип, что у species.*.family (см. game/engine.py:soul_kind/
# soul_collector_accepts_kind): группирует души независимо от их числа, так
# что новый вид моба/босса сам попадёт в нужный синтезатор без правки построек.
RESOURCE_SOUL_KIND: dict[str, str] = {r["id"]: r["soul_kind"] for r in _DATA["resources"] if r.get("soul_kind")}
# Настройка вместимости склада (необязательная, resources[].storage) - только у
# ресурсов, для которых её явно задал автор контента; отсутствие ключа = ресурс
# хранится без ограничений, как было для ВСЕХ ресурсов до этой механики. См.
# game/engine.py:storage_capacity/next_storage_tier_info.
RESOURCE_STORAGE: dict[str, dict] = {r["id"]: r["storage"] for r in _DATA["resources"] if r.get("storage")}
MANUAL_ACTIONS: dict = _DATA["manual_actions"]
BUILDINGS: dict = _DATA["buildings"]
TECHS: dict = _DATA["techs"]
MINING_INFO: dict = _DATA["mining_info"]
# Настройки самого поля шахты (fog-of-war сетка) - необязательный верхнеуровневый
# блок, тот же принцип, что GENETICS_SETTINGS/COMBAT_SETTINGS. size - сторона
# квадратного поля; dig_tool - какой инструмент (tools.*) даёт зону/урон удара
# (mining_area/mining_power НА ТИРЕ этого инструмента, см. game/engine.py:
# pickaxe_area/pickaxe_power); rock_* - параметры пустой породы (раньше это было
# ручное действие gather_stone, теперь просто "то, что не руда" на поле).
MINE_FIELD: dict = _DATA.get("mine_field", {})
# Инструменты (было "кирка" - единственный жёстко зашитый инструмент; теперь любое
# число инструментов, каждый со своим списком тиров той же формы, что раньше был у
# кирки: {name, cost, requires_tech, icon}). tool_id -> {"name": ..., "tiers": [...]}.
TOOLS: dict = _DATA["tools"]
# Зачарования (стол зачарований) - id -> {"name", "description", "applies_to_kind",
# "effect": {"type"}, "levels": [{"value"}, ...]}. applies_to_kind - тег, который
# должен совпадать с tools.*.enchant_kind (null = универсальное зачарование,
# доступно ЛЮБОМУ инструменту с enchant_kind, например "Прочность") - тот же
# generic-принцип тега, что RESOURCE_SOUL_KIND/soul_collector.accepts_kind. См.
# game/engine.py: enchantment_pool/enchant_tool/tool_enchant_level.
ENCHANTMENTS: dict = _DATA.get("enchantments", {})
LOCATIONS: dict = _DATA["locations"]
TIME: dict = _DATA["time"]
# Рецепты крафта за энергию (см. buildings.*.energy_converter) - НЕ привязаны к
# конкретной постройке: любая работающая копия ЛЮБОЙ постройки с полем energy_converter
# может вести один из этих рецептов, выбранный игроком (локация "Механизмы") или
# автокрафтом (game/engine.py:start_energy_recipe/ENERGY_RECIPE_FOR_RESOURCE).
ENERGY_RECIPES: dict = _DATA.get("energy_recipes", {})
# Виды для разведения (фермы) - ключ = id уже существующего ресурса (тот же
# принцип, что у mining_info: словарь ДОБАВЛЯЕТ метаданные существующему ресурсу,
# а не заводит отдельное пространство id). Признак "вид" - один из 5 генетических
# признаков особи (см. GENETIC_TRAITS ниже и game/engine.py:GENOTYPE_TRAIT_IDS) -
# "dominant"/"weight" тут играют ту же роль, что у аллелей остальных признаков.
SPECIES: dict = _DATA.get("species", {})
# Глобальные признаки генотипа, НЕ привязанные к виду (lifespan/output_multiplier/
# active_period/environment - ровно 4 ключа, id и число жёстко зашиты в
# game/engine.py:GENOTYPE_TRAIT_IDS) - {trait_id: {"name", "alleles": [{"id","name",
# "dominant", "weight", "value"?}, ...]}}. См. game/engine.py:trait_alleles/_phenotype_allele.
GENETIC_TRAITS: dict = _DATA.get("genetic_traits", {})
# Мутации - список (не словарь: пара родителей не обязана быть уникальным id)
# {"parent_a","parent_b","chance","requires_tech","requires_building","outcomes"} -
# шанс, что скрещивание двух РАЗНЫХ видов даст потомка ТРЕТЬЕГО вида вместо
# обычного наследования. См. game/engine.py:MUTATIONS_BY_PAIR/_cross_genotype.
MUTATIONS: list = _DATA.get("mutations", [])
# Цены на разведение (breeding_cost - доп. цена сверх двух родителей за
# скрещивание, synthesis_cost - цена генетического шаблона), необязательный
# верхнеуровневый раздел. См. game/engine.py:breed_creatures/synthesize_creature.
GENETICS_SETTINGS: dict = _DATA.get("genetics", {})
# Глобальные настройки боя (player_base_hp/hp_regen_per_turn/wounded_turns/
# wounded_damage_multiplier) - тот же необязательный верхнеуровневый раздел,
# что GENETICS_SETTINGS. См. game/engine.py:player_max_hp/current_player_hp/
# is_wounded/attack_encounter.
COMBAT_SETTINGS: dict = _DATA.get("combat", {})
# Мобы и боссы (is_boss=true) для механики "Охота" (см. locations.*.hunting,
# game/engine.py:search_mob_any/summon_boss/attack_encounter) - id ссылается на
# СВОЁ пространство (не на resources, в отличие от mining_info/species): мобы -
# не ресурсы, а цели схватки. soul_id/soul_chance - только у НЕ-боссов (шанс
# выбить душу мечом при убийстве); душа босса добывается только крафтом
# (обычный manual_actions рецепт из лута босса), поэтому у boss-записей этих
# полей нет.
MOBS: dict = _DATA.get("mobs", {})
# Приёмы боя (id -> {"name", "description", "damage_multiplier", "cooldown_turns"}) -
# разблокируются НЕ этим словарём, а tools.*.tiers[].unlocks_moves (текущий тир
# владеемого оружия, кумулятивно по всем тирам линейки до текущего включительно -
# см. game/engine.py:unlocked_attack_moves). damage_multiplier домножает уже
# посчитанный обычный урон (навык * лучший damage_multiplier оружия), cooldown_turns -
# сколько ходов приём недоступен после применения (game/state.py:move_cooldowns).
COMBAT_MOVES: dict = _DATA.get("combat_moves", {})
# Навыки прокачки - ПРОИЗВОЛЬНОЕ число линеек (см. CLAUDE.md, тот же принцип, что
# у TOOLS), каждая копит XP независимо (game/state.py:skill_xp) и даёт уровень
# по порогу (game/engine.py:skill_level). "effects" каждого уровня - свободный
# словарь, который трактует уже конкретная механика (например, бой читает
# effects["damage"]) - сама система навыков не знает и не должна знать, что
# именно прокачивает каждая линейка.
SKILLS: dict = _DATA.get("skills", {})
# Продукция синтезатора душ (buildings.*.soul_collector) - soul_id (ресурс,
# выпадающий с моба/крафтящийся из лута босса) -> {"produces", "produces_chance"}
# ровно в том же смысле, что gain/gain_chance ручного действия (см.
# game/engine.py:_roll_chance_amounts) - позволяет задать и гарантированную, и
# шансовую продукцию одной и той же душе одновременно.
SOUL_RECIPES: dict = _DATA.get("soul_recipes", {})
# Кейсы с лутом (награда за квесты, см. QUESTS ниже) - id -> {"name", "description",
# "prizes"}. "prizes" - список {"resource", "amount", "weight"}: классический
# лутбокс-барабан, из которого при открытии выпадает РОВНО один приз (вероятность
# приза пропорциональна его weight среди суммы весов, см.
# game/engine.py:_roll_loot_case_prize). Отдельное от QUESTS пространство id -
# один кейс МОЖЕТ быть наградой сразу нескольких квестов.
LOOT_CASES: dict = _DATA.get("loot_cases", {})
# Квестбук - граф квестов с наградой: id -> {"name", "description", "era",
# "requires", "conditions", "reward_case"}. "era" - чисто визуальная группировка
# в колонки графа (тот же смысл, что "tier" у TECHS), "requires" - список id
# ДРУГИХ квестов, которые должны быть выполнены раньше (см.
# game/engine.py:is_quest_available/check_quests) - квест с непустым requires
# недостижим, пока не выполнены все квесты из списка, поэтому граф открывается
# волнами "один за другим", как дерево технологий. Единственная награда - кейс
# с лутом (LOOT_CASES), который игрок открывает сам отдельным кликом после
# выполнения условий (см. claim_quest_reward).
QUESTS: dict = _DATA.get("quests", {})
# Оформление эр квестбука - id (строка, тот же номер, что QUESTS[...]["era"]) ->
# {"name", "color"} ("color" - '#rrggbb', цвет вкладки/фона графа этой эры).
# Необязательный раздел - у era без записи здесь UI сам подставляет дефолтное
# имя "Эра N" и цикличную палитру (см. tools/content_editor.html:
# tierBandColorEditor, tools/web_ui.html:tierBandColor) - этот словарь тут
# только прокидывается клиенту (web_ui.py), сама игровая логика era не касается.
ERAS: dict = _DATA.get("eras", {})
# Виды поломки построек (необязательный раздел) - id -> {"name", "description",
# "required_tools"}. buildings.*.breakdown_types ссылается на эти id (список,
# пусто/нет ключа = поломка без типа, старое поведение). required_tools -
# список id из TOOLS, которыми ДОЛЖЕН владеть игрок (tool_tier >= 2), чтобы
# починить поломку этого вида - см. game/engine.py:_is_breakdown_type_fixable.
BREAKDOWN_TYPES: dict = _DATA.get("breakdown_types", {})
UI: dict = _DATA.get("ui", {})
# Задержка (в мс), которую клиент выдерживает после клика по кнопке действия, если
# у самого действия нет собственного click_delay_ms (см. buildings/manual_actions/
# mining_info) - чисто UI-защита от автокликера, в игровую логику не входит.
DEFAULT_ACTION_DELAY_MS: int = UI.get("default_action_delay_ms", 400)
# Глобальные звуки, не привязанные к конкретному ручному действию (фон, открытие
# лутбокса, клик по кнопке) - та же файловая форма, что manual_actions.*.sound
# (имя файла в tools/sounds/, null/отсутствие ключа = используется встроенный
# синтезированный звук по умолчанию, кроме background_music - там по умолчанию
# музыки просто нет). Ключи жёстко зашиты в tools/web_ui.py/web_ui.html.
UI_SOUNDS: dict = UI.get("sounds", {})
