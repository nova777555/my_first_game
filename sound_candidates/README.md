# Кандидаты на звуки и фоновую музыку — что искал и почему

Всё найдено на **opengameart.org** (community-библиотека игровых ассетов).
Ничего не сгенерировано и не куплено — только свободные лицензии. У каждого
файла в имени указана лицензия:

- **CC0** — общественное достояние, можно использовать без каких-либо условий
  и без указания авторства. Таких большинство — брал их в приоритете.
- **CC-BY / OGA-BY** — свободно, но требует указания автора при использовании
  (обычно достаточно строчки в титрах/README игры). Брал только когда для
  темы не нашлось равноценного CC0-варианта.

Ничего никуда не встроено в игру — это just сырьё на прослушку. Что выберешь,
то и положим в `tools/sounds/` под правильным именем и пропишем в
`content.json` (`manual_actions.*.sound` / `ui.sounds`).

## Список того, что искал (по темам игры)

Составлял по тому, какие действия/постройки есть в `content.json` и что им
логически подойдёт:

1. **Фоновая музыка** — что-то спокойное, зацикленное, без слов, под
   crafting/idle-жанр (тема самого проекта — "Майнкрафт на минималках").
2. **Рубка дерева** (`gather_wood`) — удар топора по дереву, треск, падение.
3. **Кирка / шахта** (`gather_stone`, поиск и добыча жилы) — удар киркой,
   раскалывание камня.
4. **Металлургия/кузница** (плавка металла, расклёпка заклёпки, "Расплавка
   металла", постройки Печь/Прецизионная мастерская) — удар молота по
   наковальне, металлический лязг, звон инструментов.
5. **Бой мечом** (`attack_encounter`, оружие `sword`/"Клинок Стража") — взмах
   клинка, удар лезвия.
6. **Мобы/боссы** (Волк, Страж леса) — рык, вой, стоны существ.
7. **Пасека/фермы существ** — жужжание пчёл, звуки существ (для будущих
   видов).
8. **Магия/мана/кристаллы** (манацветы, кристаллизация маны, генетическая
   лаборатория, синтезатор душ) — мерцающий "магический" звон.
9. **Энергетика** (Энергетический преобразователь, генераторы, реактор) —
   электрический гул/искра.
10. **Огонь/печь** (Угольный генератор, "Жечь уголь", Печь) — треск костра,
    гул пламени.
11. **UI-награды** (квесты, лутбоксы, левел-ап, находка руды/жилы, монеты) —
    колокольчик-дзынь, "coin pickup", фанфара завершения.

## Структура папки

```
background_music/        - фоновые треки-петли (см. п.1)
wood_chop/                - рубка дерева (п.2)
mining_pickaxe/            - кирка/раскалывание камня (п.3)
metal_smelting_forge/      - кузница/наковальня/металл (п.4)
combat_sword/              - взмах меча (п.5)
monsters/                  - рык/вой существ (п.6)
bees_farm/                 - пчёлы (п.7)
magic_crystal/             - магия/кристаллы (п.8)
electricity_energy/        - электричество/энергия (п.9)
fire_furnace/               - огонь/печь (п.10)
ui_rewards_chimes/          - награды/уведомления (п.11)
extra_cc0_packs/            - большие CC0-паки россыпью (см. ниже) - в них
                              нашлось МНОГО лишнего попутно, не стал резать
                              руками, чтобы не отсеять что-то полезное
                              вслепую - легче прослушать самому
```

## Точечные файлы (по одному-два кандидата на категорию)

| Файл | Лицензия | Источник (opengameart.org/content/...) |
|---|---|---|
| `background_music/minecraft_clone_music_CC0.mp3` | CC0 | minecraft-clone-music — буквально музыка для клона майнкрафта, легла в тему один в один |
| `background_music/quaint_town_loopable_CC0.mp3` | CC0 | quaint-town — спокойный зацикленный трек, автор сам пометил "LOOPABLE" |
| `background_music/peaceful_village_loop_CCBY3_abeth.mp3` | CC-BY 3.0 | peaceful-village-loop — запасной вариант, если два CC0 не понравятся |
| `wood_chop/tree_chop_fall_thud_CC0.ogg` | CC0 | tree-chop-fall-thud |
| `wood_chop/chopping_tree_CCBY3.ogg` | CC-BY 3.0 | chopping-tree — альтернативная рубка |
| `mining_pickaxe/breaking_rock_CC0.ogg` | CC0 | breaking-rock |
| `mining_pickaxe/pick_hitting_rock_CCBY3.wav` | CC-BY 3.0 | mining-pick |
| `metal_smelting_forge/hammer_on_anvil_CC0.ogg` | CC0 | hammer-on-anvil |
| `metal_smelting_forge/blacksmiths_hammer_CC0.wav` | CC0 | blacksmiths-hammer |
| `combat_sword/sword_swing_OGA-BY4.wav` | OGA-BY 4.0 (= атрибуция) | sword-swing |
| `monsters/wolf_monster_growl_CC0.mp3` | CC0 | wolf-monster-sound — под моба "Волк" |
| `monsters/deep_monster_roar_CC0.wav` | CC0 | cc0-deep-monster-roar — под боссов вроде "Стража леса" |
| `bees_farm/bee_pack/bee.wav` | CC-BY 3.0 | single-bee-sound — под "Пасеку" |
| `magic_crystal/shimmer_glitter_magic_CCBY3.flac` | CC-BY 3.0 | shimmer-glitter-magic |
| `electricity_energy/continuous_spark_CC0.wav`, `spark_CC0.wav` | CC0 | electricity-sound-effects-0 |
| `fire_furnace/fire_crackling_CC0.wav` | CC0 | fire-crackling |
| `fire_furnace/fireplace_loop_CC0.wav` | CC0 | fireplace-sound-loop (тяжёлый, 10 МБ — это долгий wav-луп, при выборе стоит перегнать в ogg/mp3) |
| `ui_rewards_chimes/completion_sound_CC0.mp3` | CC0 | completion-sound — под завершение квеста |
| `ui_rewards_chimes/bell_ding1_CC0.wav`, `bell_ding2_CC0.wav` | CC0 | bell-dingschimes |
| `ui_rewards_chimes/plingy_coin_CC0.wav` | CC0 | plingy-coin — под находку ресурса/руды |

## Большие CC0-паки (`extra_cc0_packs/`) — россыпью, на прослушку

Всё внутри — **CC0**, кроме `50_cc0_retro_synth_sfx` (тоже CC0, несмотря на
цифру в названии — это авторское обозначение, не намёк на другую лицензию).

- **`100_cc0_metal_wood_sfx/`** — неожиданно закрыл сразу два пункта списка:
  `hammer_*`/`metal_hit_*`/`metal_sheet_*`/`tools_*` (кузница/наковальня) И
  `wood_hit_*`/`wood_cracking_*`/`wood_falling_*`/`wood_hammer_*` (рубка
  дерева/лесопилка).
- **`80_cc0_rpg_sfx/`** — по чуть-чуть на всё: `item_wood_*`/`item_stone_*`/
  `item_gem_*`/`item_coins_*` (находки/сбор ресурсов), `metal_*`, `blade_*`
  (меч), `creature_roar_*`/`creature_monster_*` (мобы), `book_*`/`lock_*`/
  `chain_*` (на будущее, для UI/квестов).
- **`80_cc0_creature_sfx/`** — звуки существ (рычание/вой/укус/писк) — под
  мобов и, на будущее, под виды в разведении существ.
- **`75_cc0_breaking_falling_hit_sfx/`** — `rock_breaking_*`/`rock_falling_*`
  (жила в шахте), `metal_falling_*`/`metal_hit_*` (постройка сломалась —
  `breakdown_chance`), `glass_*` (на всякий случай).
- **`10_8bit_coin_sounds/`** — 10 вариаций подбора монетки, ретро-стиль,
  под находку ресурса/лутбокс.
- **`50_cc0_retro_synth_sfx/`** — чиптюновые `power_up_*`/`retro_coin_*` —
  альтернатива в ретро-стиле для наград/уровней, если не зайдёт реализм.

## Как поступить дальше

Прослушай, скажи, что нравится (можно по одному файлу на категорию, а не всё
разом) — я:
1. Скопирую выбранные файлы в `tools/sounds/` под именами, разрешёнными
   сервером (`^[A-Za-z0-9_-]+\.(wav|mp3|ogg)$` — раздаёт `web_ui.py`).
2. Пропишу `background_music`/`lootbox_open`/`button_click` в
   `content.json → ui.sounds` и/или `sound` у конкретных `manual_actions`
   через `content_editor.py` (или руками в JSON).
3. Прогоню тесты (`python -m unittest discover tests -v`), чтобы валидатор
   не споткнулся о новые имена файлов.

Эта папка (`sound_candidates/`) — черновик на прослушку, в игру не встроена
и с `game/`/`tools/` не связана; после отбора её можно смело удалить.
