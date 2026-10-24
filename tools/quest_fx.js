"use strict";
// Квестбук: граф квестов в игре (tools/web_ui.html: openQuestsModal) и на вкладке
// «Квесты» → «Граф» в редакторе (tools/content_editor.html). Оба сервера отдают этот
// файл по /quest_fx.js - редактор рисует узлы, связи, фон и карточку эры тем же кодом,
// что игра, поэтому превью = то, что увидит игрок.
//
// Из контента (всё необязательно, умолчания живут только здесь):
//   eras.*.name/color/subtitle/icon      - карточка эры (questFxEraItem);
//   eras.*.backdrop {pattern, tile, opacity} - фон графа (questFxApplyBackdrop);
//   quests.*.icon/accent/style/ribbon    - вид узла (questFxNode);
//   quests.*.pos                         - место узла; без pos - авторасстановка
//                                          по глубине требований (questFxLayout).
//
// id вариантов (ключи QUEST_FX_STYLES/QUEST_FX_BACKDROPS) продублированы в
// tools/content_validation.py (QUEST_STYLES/QUEST_BACKDROP_PATTERNS) - держать в паре;
// совпадение проверяет tests/test_content_validation.py.

// Вид узла. w/h - размер карточки, icon - сторона значка внутри.
const QUEST_FX_STYLES = {
  normal:    { name: "Обычный", w: 190, h: 58, icon: 32 },
  milestone: { name: "Веха", w: 208, h: 76, icon: 44 },
};

// Узор фона графа эры. tile - пиксельная плитка eras.*.backdrop.tile (без неё - каменная).
const QUEST_FX_BACKDROPS = {
  grid: { name: "Чертёж" },
  dots: { name: "Точки" },
  tile: { name: "Плитка" },
  none: { name: "Пусто" },
};

const QUEST_FX_STATUS_LABELS = { locked: "Закрыт", available: "В работе", completed: "Награда ждёт", claimed: "Получено" };
const QUEST_FX_GOLD = "#f4c430";
const QUEST_FX_DEFAULT_OPACITY = 0.5;
// Точек экрана на пиксель плитки фона.
const QUEST_FX_TILE_PX = 4;
// Авторасстановка узлов без pos: отступы поля, шаг колонки (глубина требований) и строки.
const QUEST_FX_PAD_X = 40, QUEST_FX_PAD_Y = 44, QUEST_FX_COL_STEP = 250, QUEST_FX_ROW_STEP = 110;

// Значок квеста без своего quests.*.icon. ICONS.quest в игре и DEFAULT_ICONS.quest в
// редакторе ссылаются сюда.
const QUEST_FX_DEFAULT_ICON = { grid: [
  "..AAAAAA..", ".AAAAAAAA.", "AABBBBBBAA", "AABBBBBBAA",
  "AABAAAABAA", "AABBBBBBAA", "AABAAAABAA", ".AAAAAAAA.", "..AAAAAA..",
], palette: { A: "#e3c98a", B: "#8a5a34" } };
// Лутбокс (награда квеста) - своей иконки у loot_cases нет.
const QUEST_FX_CHEST_ICON = { grid: [
  "..AAAAAAAA..", ".ABBBBBBBBA.", "ABBCBBBBCBBA", "AAAAAAAAAAAA", "ADDDDEEDDDDA",
  "ABBCBEEBCBBA", "ABBCBBBBCBBA", "ABBCBBBBCBBA", "AAAAAAAAAAAA",
], palette: { A: "#3a2414", B: "#9a6638", C: "#c8903a", D: "#6b4423", E: "#f4c430" } };
const QUEST_FX_LOCK_ICON = { grid: ["..AAA..", ".A...A.", ".A...A.", "BBBBBBB", "BBBCBBB", "BBBCBBB", "BBBBBBB"],
  palette: { A: "#7c8a94", B: "#56636d", C: "#1a1f24" } };
const QUEST_FX_DEFAULT_TILE = { grid: ["AAABAAAA", "ABBAAACA", "AAAAABBA", "CAAAAAAA", "AAABAAAB", "ABBAACAA", "AAAAAAAB", "BAACAAAA"],
  palette: { A: "#1b2127", B: "#262e36", C: "#313a44" } };

// ---- мелочи ----

function questFxEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function questFxSvg(tag, attrs) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  return e;
}

// Пиксельная иконка {grid, palette} -> <svg>, пропорции сохраняются (иконка не обязана быть квадратной).
function questFxIcon(def, size) {
  const d = def && def.grid ? def : QUEST_FX_DEFAULT_ICON;
  const rows = d.grid.length, cols = Math.max(...d.grid.map(r => r.length));
  const s = questFxSvg("svg", { viewBox: `0 0 ${cols} ${rows}`, width: size, height: size, "shape-rendering": "crispEdges" });
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < d.grid[y].length; x++) {
      const ch = d.grid[y][x];
      if (ch === "." || !d.palette[ch]) continue;
      s.appendChild(questFxSvg("rect", { x, y, width: 1, height: 1, fill: d.palette[ch] }));
    }
  }
  return s;
}

const QUEST_FX_TILE_CACHE = new Map();
function questFxTileUrl(tile) {
  const key = `${tile.grid.join("\n")}|${JSON.stringify(tile.palette)}`;
  let url = QUEST_FX_TILE_CACHE.get(key);
  if (!url) {
    const rows = tile.grid.length, cols = tile.grid[0].length;
    let rects = "";
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const ch = tile.grid[y][x];
        if (ch === "." || !tile.palette[ch]) continue;
        rects += `<rect x="${x}" y="${y}" width="1" height="1" fill="${tile.palette[ch]}"/>`;
      }
    }
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${cols} ${rows}" width="${cols * QUEST_FX_TILE_PX}" height="${rows * QUEST_FX_TILE_PX}" shape-rendering="crispEdges">${rects}</svg>`;
    url = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    QUEST_FX_TILE_CACHE.set(key, url);
  }
  return url;
}

function questFxStyle(style) {
  return QUEST_FX_STYLES[style] || QUEST_FX_STYLES.normal;
}

function questFxIsDone(status) {
  return status === "completed" || status === "claimed";
}

// ---- раскладка ----

// items - квесты ОДНОЙ эры: [{id, name, requires, pos, style}]. Узел с pos стоит, где
// его поставили в редакторе; остальные - колонками по глубине требований внутри эры
// (требование на квест другой эры глубину не увеличивает), в колонке - по имени.
// -> {rects: {id: {x, y, w, h}}, width, height}
function questFxLayout(items) {
  const byId = {};
  items.forEach(q => { byId[q.id] = q; });
  const level = {};
  const levelOf = (id, stack) => {
    if (level[id] !== undefined) return level[id];
    if (stack.has(id)) return 0; // цикл - валидатор такого не пропустит, но не зависаем
    stack.add(id);
    const reqs = (byId[id].requires || []).filter(r => byId[r]);
    level[id] = reqs.length ? 1 + Math.max(...reqs.map(r => levelOf(r, stack))) : 0;
    return level[id];
  };
  items.forEach(q => levelOf(q.id, new Set()));

  const rects = {};
  const cols = {};
  items.forEach(q => {
    const s = questFxStyle(q.style);
    if (q.pos && typeof q.pos.x === "number" && typeof q.pos.y === "number") {
      rects[q.id] = { x: q.pos.x, y: q.pos.y, w: s.w, h: s.h };
    } else {
      (cols[level[q.id]] = cols[level[q.id]] || []).push(q);
    }
  });
  Object.entries(cols).forEach(([col, list]) => {
    list.sort((a, b) => String(a.name).localeCompare(String(b.name), "ru"));
    list.forEach((q, row) => {
      const s = questFxStyle(q.style);
      rects[q.id] = { x: QUEST_FX_PAD_X + Number(col) * QUEST_FX_COL_STEP, y: QUEST_FX_PAD_Y + row * QUEST_FX_ROW_STEP, w: s.w, h: s.h };
    });
  });
  let width = 0, height = 0;
  Object.values(rects).forEach(r => { width = Math.max(width, r.x + r.w); height = Math.max(height, r.y + r.h); });
  return { rects, width: width + QUEST_FX_PAD_X, height: height + QUEST_FX_PAD_Y };
}

// ---- связи ----

// Связь выходит снизу и входит сверху, если цель ниже источника (или наоборот), иначе
// идёт между боковыми гранями - с той стороны, где цель.
function questFxEdgeGeom(a, b) {
  if (b.y - (a.y + a.h) >= 18 || a.y - (b.y + b.h) >= 18) {
    const down = b.y > a.y;
    const x1 = a.x + a.w / 2, y1 = down ? a.y + a.h : a.y;
    const x2 = b.x + b.w / 2, y2 = down ? b.y : b.y + b.h;
    const m = (y1 + y2) / 2;
    return { d: `M${x1} ${y1} C${x1} ${m}, ${x2} ${m}, ${x2} ${y2}`, p1: [x1, y1], p2: [x2, y2] };
  }
  const ltr = b.x + b.w / 2 >= a.x + a.w / 2;
  const x1 = ltr ? a.x + a.w : a.x, x2 = ltr ? b.x : b.x + b.w;
  const y1 = a.y + a.h / 2, y2 = b.y + b.h / 2, m = (x1 + x2) / 2;
  return { d: `M${x1} ${y1} C${m} ${y1}, ${m} ${y2}, ${x2} ${y2}`, p1: [x1, y1], p2: [x2, y2] };
}

// Состояние связи по статусам концов: flow - к квесту в работе (бегущие точки), done -
// к выполненному, half - источник выполнен, но цель ждёт других требований, locked - оба закрыты.
function questFxEdgeState(fromStatus, toStatus) {
  if (toStatus === "locked") return questFxIsDone(fromStatus) ? "half" : "locked";
  return toStatus === "available" ? "flow" : "done";
}

// Рисует связи ВНУТРИ эры (requires на квест, которого нет в rects, пропускается - это
// другая эра, для неё над узлом метка). statusOf(id), accentOf(id) - от вызывающего.
// Каждая связь - <g data-from data-to>, группы возвращаются (редактор вешает на них
// клик-удаление и подсветку).
function questFxDrawEdges(svgEl, items, rects, statusOf, accentOf) {
  const groups = [];
  items.forEach(q => (q.requires || []).forEach(rid => {
    const a = rects[rid], b = rects[q.id];
    if (!a || !b) return;
    const g = questFxEdgeGeom(a, b);
    const state = questFxEdgeState(statusOf(rid), statusOf(q.id));
    const grp = questFxSvg("g", { class: `qfx-e qfx-e--${state}` });
    grp.dataset.from = rid;
    grp.dataset.to = q.id;
    grp.style.setProperty("--qfx-acc", accentOf(q.id));
    grp.appendChild(questFxSvg("path", { d: g.d, class: "qfx-e-line" }));
    if (state === "flow") grp.appendChild(questFxSvg("path", { d: g.d, class: "qfx-e-flow" }));
    [g.p1, g.p2].forEach(([cx, cy]) => grp.appendChild(questFxSvg("circle", { cx, cy, r: 3.2, class: "qfx-e-sock" })));
    svgEl.appendChild(grp);
    groups.push({ el: grp, from: rid, to: q.id, d: g.d });
  }));
  return groups;
}

// ---- узел ----

// v = {name, icon, style, ribbon, accent, status, progress: {frac, label} | null,
//      note (строка «после: …» у закрытого), cross: {label, color, title} | null (требование
//      из другой эры), tip: {title, line} | null, selected, tipBelow}
function questFxNode(v) {
  questFxInjectStyles();
  const style = QUEST_FX_STYLES[v.style] ? v.style : "normal";
  const s = QUEST_FX_STYLES[style];
  const n = questFxEl("div", `qfx-n qfx-n--${v.status} qfx-n--${style}${v.selected ? " sel" : ""}${v.tipBelow ? " tip-below" : ""}`);
  n.style.setProperty("--qfx-acc", v.accent || "#f0a030");
  if (style === "milestone") n.appendChild(questFxEl("div", "qfx-n-ribbon", String(v.ribbon || "Веха").toUpperCase()));

  const ic = questFxEl("div", "qfx-n-ic");
  ic.appendChild(questFxIcon(v.icon, s.icon));
  const tx = questFxEl("div", "qfx-n-tx");
  tx.appendChild(questFxEl("div", "qfx-n-name", v.name));
  const sub = questFxEl("div", "qfx-n-sub");
  if (v.status === "available") {
    const bar = questFxEl("div", "qfx-n-bar");
    const fill = questFxEl("i");
    fill.style.width = `${Math.round(Math.max(0, Math.min(1, (v.progress && v.progress.frac) || 0)) * 100)}%`;
    bar.appendChild(fill);
    sub.append(bar, questFxEl("span", "qfx-n-num", (v.progress && v.progress.label) || ""));
  } else if (v.status === "completed") {
    sub.appendChild(questFxEl("span", null, "Награда ждёт"));
  } else if (v.status === "claimed") {
    sub.appendChild(questFxEl("span", null, "✓ Получено"));
  } else {
    sub.append(questFxIcon(QUEST_FX_LOCK_ICON, 9), questFxEl("span", null, v.note || "Закрыт"));
  }
  tx.appendChild(sub);
  n.append(ic, tx);

  if (v.status === "completed") {
    const b = questFxEl("div", "qfx-n-badge");
    b.appendChild(questFxIcon(QUEST_FX_CHEST_ICON, 17));
    n.appendChild(b);
  } else if (v.status === "claimed") {
    n.appendChild(questFxEl("div", "qfx-n-badge qfx-n-badge--ok", "✓"));
  }
  if (v.cross) {
    const chip = questFxEl("div", "qfx-n-cross", v.cross.label);
    chip.style.setProperty("--qfx-c", v.cross.color);
    if (v.cross.title) chip.title = v.cross.title;
    n.appendChild(chip);
  }
  if (v.tip) {
    const tip = questFxEl("div", "qfx-n-tip");
    tip.appendChild(questFxEl("b", null, v.tip.title));
    if (v.tip.line) tip.appendChild(document.createTextNode(v.tip.line));
    n.appendChild(tip);
  }
  return n;
}

// ---- эра ----

// Полоска «по сегменту на квест», сегмент красится статусом квеста.
function questFxPips(statuses, cls) {
  questFxInjectStyles();
  const box = questFxEl("div", `qfx-pips${cls ? " " + cls : ""}`);
  statuses.forEach(st => box.appendChild(questFxEl("i", `qfx-pip ${st}`)));
  return box;
}

// Карточка эры в списке эр. v = {name, subtitle, color, icon, done, total, gifts,
// statuses, active, dim}. tag - "button" в игре, "div" в превью редактора.
function questFxEraItem(v, tag) {
  questFxInjectStyles();
  const e = questFxEl(tag || "button", `qfx-era${v.active ? " active" : ""}${v.dim ? " dim" : ""}`);
  if ((tag || "button") === "button") e.type = "button";
  e.style.setProperty("--qfx-era", v.color);
  const emb = questFxEl("div", "qfx-era-emb");
  emb.appendChild(questFxIcon(v.icon, 28));
  const count = questFxEl("div", "qfx-era-count");
  if (v.gifts) count.appendChild(questFxIcon(QUEST_FX_CHEST_ICON, 13));
  count.appendChild(document.createTextNode(`${v.done}/${v.total}`));
  e.append(emb, questFxEl("div", "qfx-era-name", v.name), count, questFxEl("div", "qfx-era-sub", v.subtitle || ""));
  e.appendChild(questFxPips(v.statuses || [], "qfx-era-pips"));
  return e;
}

// ---- фон графа ----

// Фон эры на элементе el. era = {color, backdrop}. Без backdrop - чертёжная сетка.
function questFxApplyBackdrop(el, era) {
  questFxInjectStyles();
  const b = (era && era.backdrop) || {};
  const pattern = QUEST_FX_BACKDROPS[b.pattern] ? b.pattern : "grid";
  el.classList.add("qfx-bg");
  el.dataset.qfxPattern = pattern;
  el.style.setProperty("--qfx-era", (era && era.color) || "#f0a030");
  el.style.setProperty("--qfx-op", typeof b.opacity === "number" ? b.opacity : QUEST_FX_DEFAULT_OPACITY);
  if (pattern === "tile") {
    const tile = b.tile && b.tile.grid ? b.tile : QUEST_FX_DEFAULT_TILE;
    el.style.setProperty("--qfx-tile", questFxTileUrl(tile));
    el.style.setProperty("--qfx-tw", `${tile.grid[0].length * QUEST_FX_TILE_PX}px`);
    el.style.setProperty("--qfx-th", `${tile.grid.length * QUEST_FX_TILE_PX}px`);
  }
}

// Узор фона едет и масштабируется вместе с графом: view = {zoom, panX, panY} - то же
// состояние, что у панорамы/зума графа в игре.
function questFxBackdropView(el, view) {
  el.style.setProperty("--qfx-px", `${view.panX}px`);
  el.style.setProperty("--qfx-py", `${view.panY}px`);
  el.style.setProperty("--qfx-z", view.zoom);
}

// ---- стили: один <style> на страницу, вставляется при первом обращении ----

function questFxInjectStyles() {
  if (document.getElementById("questFxStyles")) return;
  const st = document.createElement("style");
  st.id = "questFxStyles";
  st.textContent = `
.qfx-bg { position: relative; background-color: #0d1216; --qfx-era: #f0a030; --qfx-op: .5; --qfx-px: 0px; --qfx-py: 0px; --qfx-z: 1; }
.qfx-bg::before, .qfx-bg::after { content: ""; position: absolute; inset: 0; pointer-events: none; }
.qfx-bg::before { opacity: var(--qfx-op); background-position: var(--qfx-px) var(--qfx-py); }
.qfx-bg[data-qfx-pattern="grid"]::before {
  background-image:
    linear-gradient(color-mix(in srgb, var(--qfx-era) 26%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--qfx-era) 26%, transparent) 1px, transparent 1px),
    linear-gradient(color-mix(in srgb, var(--qfx-era) 10%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--qfx-era) 10%, transparent) 1px, transparent 1px);
  background-size: calc(120px * var(--qfx-z)) calc(120px * var(--qfx-z)), calc(120px * var(--qfx-z)) calc(120px * var(--qfx-z)),
    calc(24px * var(--qfx-z)) calc(24px * var(--qfx-z)), calc(24px * var(--qfx-z)) calc(24px * var(--qfx-z));
}
.qfx-bg[data-qfx-pattern="dots"]::before {
  background-image: radial-gradient(color-mix(in srgb, var(--qfx-era) 45%, transparent) 1px, transparent 1.6px);
  background-size: calc(20px * var(--qfx-z)) calc(20px * var(--qfx-z));
}
.qfx-bg[data-qfx-pattern="tile"]::before {
  background-image: var(--qfx-tile); image-rendering: pixelated;
  background-size: calc(var(--qfx-tw) * var(--qfx-z)) calc(var(--qfx-th) * var(--qfx-z));
}
.qfx-bg::after {
  background:
    radial-gradient(ellipse 75% 45% at 50% 0%, color-mix(in srgb, var(--qfx-era) 10%, transparent), transparent 70%),
    radial-gradient(ellipse 95% 85% at 50% 50%, transparent 55%, rgba(0,0,0,.42));
}

.qfx-n {
  --qfx-acc: #f0a030;
  position: absolute; z-index: 2; width: 190px; height: 58px; box-sizing: border-box;
  display: flex; align-items: center; gap: 10px; padding: 0 12px 0 8px;
  background: color-mix(in srgb, var(--qfx-acc) 6%, #141a20);
  border: 1px solid color-mix(in srgb, var(--qfx-acc) 40%, #28313a);
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; cursor: pointer; user-select: none;
  transition: border-color .15s, background .15s;
}
.qfx-n:hover { border-color: var(--qfx-acc); }
.qfx-n-ic { flex-shrink: 0; width: 40px; height: 40px; display: grid; place-items: center; background: #0e1317; border: 1px solid color-mix(in srgb, var(--qfx-acc) 30%, transparent); }
.qfx-n-ic > svg { display: block; }
.qfx-n-tx { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 6px; }
.qfx-n-name { font-size: 13px; font-weight: 600; color: #e8e6df; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qfx-n-sub { display: flex; align-items: center; gap: 7px; min-width: 0; font-family: Consolas, "Courier New", monospace; font-size: 11px; color: #7f97a1; white-space: nowrap; overflow: hidden; }
.qfx-n-sub > span { overflow: hidden; text-overflow: ellipsis; }
.qfx-n-sub > svg { flex-shrink: 0; }
.qfx-n-bar { flex: 1; min-width: 30px; height: 4px; background: rgba(255,255,255,.08); }
.qfx-n-bar i { display: block; height: 100%; background: var(--qfx-acc); box-shadow: 0 0 6px color-mix(in srgb, var(--qfx-acc) 60%, transparent); }
.qfx-n-num { flex-shrink: 0; color: #c9d4d9; font-variant-numeric: tabular-nums; }
.qfx-n--available { background: color-mix(in srgb, var(--qfx-acc) 11%, #141a20); border-color: var(--qfx-acc); }
.qfx-n--available .qfx-n-ic { animation: qfx-breathe 2.8s ease-in-out infinite; }
@keyframes qfx-breathe { 0%, 100% { box-shadow: 0 0 0 0 transparent; } 50% { box-shadow: 0 0 14px 1px color-mix(in srgb, var(--qfx-acc) 50%, transparent); } }
.qfx-n--completed { background: color-mix(in srgb, ${QUEST_FX_GOLD} 10%, #141a20); border-color: color-mix(in srgb, ${QUEST_FX_GOLD} 80%, transparent); box-shadow: 0 0 22px -4px color-mix(in srgb, ${QUEST_FX_GOLD} 55%, transparent); }
.qfx-n--completed .qfx-n-sub { color: ${QUEST_FX_GOLD}; }
.qfx-n--claimed { background: color-mix(in srgb, var(--qfx-acc) 4%, #12171c); border-color: color-mix(in srgb, var(--qfx-acc) 32%, #28313a); }
.qfx-n--claimed .qfx-n-name { color: #a9b8bf; }
.qfx-n--claimed .qfx-n-sub { color: color-mix(in srgb, var(--qfx-acc) 70%, #7f97a1); }
.qfx-n--locked { background: #131a20; border-color: #2a333c; cursor: default; }
.qfx-n--locked:hover { border-color: #36414b; }
.qfx-n--locked .qfx-n-ic { border-color: #212930; }
.qfx-n--locked .qfx-n-ic > svg { filter: grayscale(1) brightness(.62); }
.qfx-n--locked .qfx-n-name { color: #7a8791; }
.qfx-n--locked .qfx-n-sub { color: #5a6771; }
.qfx-n--milestone { width: 208px; height: 76px; gap: 12px; padding: 0 14px 0 10px; outline: 1px solid color-mix(in srgb, var(--qfx-acc) 32%, transparent); outline-offset: 3px; }
.qfx-n--milestone.qfx-n--locked { outline-color: #1f262d; }
.qfx-n--milestone .qfx-n-ic { width: 54px; height: 54px; }
.qfx-n--milestone .qfx-n-name { font-size: 14px; }
.qfx-n-ribbon {
  position: absolute; top: -10px; left: 12px; padding: 2px 10px 2px 7px;
  font: 700 10px/1.4 Consolas, "Courier New", monospace; letter-spacing: 1.5px;
  background: var(--qfx-acc); color: #10151b; clip-path: polygon(0 0, 100% 0, calc(100% - 6px) 100%, 0 100%);
}
.qfx-n--locked .qfx-n-ribbon { background: #2a333c; color: #7c8a94; }
.qfx-n-badge {
  position: absolute; top: -11px; right: -11px; width: 26px; height: 26px; z-index: 3;
  display: grid; place-items: center; background: #161d24; border: 1px solid ${QUEST_FX_GOLD};
  box-shadow: 0 0 12px rgba(244,196,48,.55); animation: qfx-hop 1.9s ease-in-out infinite;
}
@keyframes qfx-hop { 0%, 58%, 100% { transform: translateY(0); } 68% { transform: translateY(-5px); } 78% { transform: translateY(0); } 86% { transform: translateY(-2px); } }
.qfx-n-badge--ok { width: 20px; height: 20px; top: -8px; right: -8px; border-color: color-mix(in srgb, var(--qfx-acc) 70%, transparent); color: var(--qfx-acc); font: 700 11px Consolas, monospace; animation: none; box-shadow: none; }
.qfx-n.sel::after {
  content: ""; position: absolute; inset: -7px; pointer-events: none;
  background:
    linear-gradient(#5fd0e8, #5fd0e8) left top / 11px 2px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) left top / 2px 11px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) right top / 11px 2px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) right top / 2px 11px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) left bottom / 11px 2px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) left bottom / 2px 11px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) right bottom / 11px 2px no-repeat,
    linear-gradient(#5fd0e8, #5fd0e8) right bottom / 2px 11px no-repeat;
}
.qfx-n--milestone.sel::after { inset: -10px; }
.qfx-n.burst::before { content: ""; position: absolute; inset: -2px; border: 2px solid ${QUEST_FX_GOLD}; pointer-events: none; animation: qfx-burst .9s ease-out forwards; }
@keyframes qfx-burst { from { transform: scale(1); opacity: 1; } to { transform: scale(1.35); opacity: 0; } }
.qfx-n-cross {
  position: absolute; right: 0; bottom: calc(100% + 14px);
  font: 10px Consolas, "Courier New", monospace; padding: 2px 7px; white-space: nowrap;
  border: 1px dashed color-mix(in srgb, var(--qfx-c) 65%, transparent); color: var(--qfx-c); background: #0d1216;
}
.qfx-n--milestone .qfx-n-cross { bottom: calc(100% + 18px); }
.qfx-n-tip {
  position: absolute; bottom: calc(100% + 12px); left: 50%; transform: translateX(-50%); z-index: 6;
  background: #0f141af2; border: 1px solid #2b333b; padding: 6px 10px; pointer-events: none;
  display: none; white-space: nowrap; font-size: 12px; color: #7f97a1;
}
.qfx-n-tip b { display: block; font-size: 13px; color: #e8e6df; }
.qfx-n.tip-below .qfx-n-tip { bottom: auto; top: calc(100% + 12px); }
.qfx-n:hover .qfx-n-tip { display: block; }

.qfx-e { fill: none; }
.qfx-e-line { fill: none; stroke-width: 2; }
.qfx-e--locked .qfx-e-line { stroke: #2b343d; stroke-width: 1.5; stroke-dasharray: 3 5; }
.qfx-e--half .qfx-e-line { stroke: color-mix(in srgb, var(--qfx-acc) 40%, transparent); stroke-width: 1.5; stroke-dasharray: 3 5; }
.qfx-e--done .qfx-e-line { stroke: color-mix(in srgb, var(--qfx-acc) 62%, transparent); }
.qfx-e--flow .qfx-e-line { stroke: color-mix(in srgb, var(--qfx-acc) 40%, transparent); }
.qfx-e-flow { fill: none; stroke: var(--qfx-acc); stroke-width: 2.5; stroke-linecap: round; stroke-dasharray: 1 11; animation: qfx-flow 1.1s linear infinite; }
@keyframes qfx-flow { to { stroke-dashoffset: -12; } }
.qfx-e-sock { fill: #0d1216; stroke-width: 1.5; stroke: color-mix(in srgb, var(--qfx-acc) 70%, transparent); }
.qfx-e--locked .qfx-e-sock { stroke: #313b45; }

.qfx-pips { display: flex; gap: 3px; }
.qfx-pip { flex: 1; height: 5px; background: #252d35; }
.qfx-pip.claimed { background: var(--qfx-era, #f0a030); }
.qfx-pip.completed { background: ${QUEST_FX_GOLD}; animation: qfx-blink 1.6s ease-in-out infinite; }
.qfx-pip.available { background: color-mix(in srgb, var(--qfx-era, #f0a030) 25%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--qfx-era, #f0a030) 75%, transparent); }
@keyframes qfx-blink { 50% { opacity: .45; } }

.qfx-era {
  --qfx-era: #f0a030;
  display: grid; grid-template-columns: 40px minmax(0, 1fr) auto;
  grid-template-areas: "emb name count" "emb sub sub" "pips pips pips";
  column-gap: 10px; row-gap: 3px; align-items: center; width: 100%; box-sizing: border-box;
  padding: 10px; border: 1px solid transparent; background: transparent; color: #e8e6df; text-align: left; cursor: pointer;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 14px;
}
.qfx-era:hover { background: rgba(255,255,255,.03); }
.qfx-era.active { background: color-mix(in srgb, var(--qfx-era) 9%, transparent); border-color: color-mix(in srgb, var(--qfx-era) 45%, transparent); box-shadow: inset 2px 0 0 var(--qfx-era); }
.qfx-era-emb { grid-area: emb; width: 40px; height: 40px; display: grid; place-items: center; background: #10161b; border: 1px solid color-mix(in srgb, var(--qfx-era) 40%, transparent); }
.qfx-era-name { grid-area: name; font: 700 13px Consolas, "Courier New", monospace; color: #c9d4d9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qfx-era.active .qfx-era-name { color: var(--qfx-era); }
.qfx-era-count { grid-area: count; display: flex; gap: 6px; align-items: center; font: 12px Consolas, "Courier New", monospace; color: #7f97a1; font-variant-numeric: tabular-nums; }
.qfx-era-sub { grid-area: sub; font-size: 12px; color: #7f97a1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qfx-era-pips { grid-area: pips; margin-top: 7px; }
.qfx-era.dim .qfx-era-emb > svg { filter: grayscale(1) brightness(.6); }
.qfx-era.dim .qfx-era-name { color: #6d7a83; }

@media (prefers-reduced-motion: reduce) {
  .qfx-n, .qfx-n *, .qfx-e *, .qfx-pip { animation: none !important; transition: none !important; }
}
`;
  document.head.appendChild(st);
}
