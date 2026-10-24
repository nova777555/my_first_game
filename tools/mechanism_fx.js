"use strict";
// Оформление модалки механизма (locations.*.appearance): фон-текстура сцены,
// пиксельный прогресс с направлением заливки, варианты движения и частиц.
// Общий код игры (tools/web_ui.html) и превью «Оформление» в редакторе
// (tools/content_editor.html) - оба сервера отдают этот файл по /mechanism_fx.js,
// поэтому превью и игра не расходятся. Стили анимаций живут здесь же
// (mechFxInjectStyles), а не в CSS страниц - по той же причине.
//
// id вариантов (ключи MECH_FX_MOTIONS/MECH_FX_PARTICLES/MECH_FX_FILLS) продублированы
// в tools/content_validation.py (MECHANISM_MOTIONS/MECHANISM_PARTICLES/
// MECHANISM_PROGRESS_FILLS) - держать в паре; совпадение проверяет
// tests/test_content_validation.py. Новый вариант = новая запись здесь + её CSS/режим
// ниже + id в валидаторе; выбирается он потом в редакторе без правки кода.

// origin - точка вращения/сжатия (для молота - рукоять в левом нижнем углу, как у кирки).
const MECH_FX_MOTIONS = {
  strike:  { name: "Удар (молот)", origin: "18% 82%", hint: "замах и удар; рукоять рисуй в левом нижнем углу" },
  saw:     { name: "Пила", origin: "50% 50%", hint: "частое движение туда-обратно" },
  press:   { name: "Пресс", origin: "50% 100%", hint: "сжатие сверху вниз" },
  spin:    { name: "Вращение (шестерня)", origin: "50% 50%", hint: "крутится вокруг центра" },
  flicker: { name: "Пламя", origin: "50% 100%", hint: "дрожит и вспыхивает" },
  pulse:   { name: "Пульс (энергия)", origin: "50% 50%", hint: "светится и пульсирует" },
};

// mode: radial (разлёт во все стороны) | fountain (вверх и падает по дуге) |
// rise (всплывает, растёт и тает) | spiral (поднимается по спирали, мерцает).
// Скорости - px/с, ускорение gravity - px/с², длительности - мс.
const MECH_FX_PARTICLES = {
  sparks:   { name: "Искры (кузня)", colors: ["#fff4c9", "#ffd27a", "#ffa53a", "#ff6a1f"], count: 16, size: [2, 4],
              shape: "square", glow: true, mode: "fountain", speed: [150, 270], spread: 80, gravity: 560, duration: [650, 950] },
  sawdust:  { name: "Опилки", colors: ["#ecd3a0", "#c69a6d", "#9a6b3f"], count: 20, size: [2, 4],
              shape: "square", mode: "fountain", speed: [90, 210], spread: 110, gravity: 620, duration: [700, 1000], spin: true },
  shards:   { name: "Осколки камня", colors: ["#c4c8ce", "#8b8f94", "#5f6266"], count: 12, size: [3, 6],
              shape: "square", mode: "fountain", speed: [130, 240], spread: 90, gravity: 820, duration: [600, 850], spin: true },
  drops:    { name: "Капли", colors: ["#b8e4ff", "#5ab4ee", "#2f7fc0"], count: 12, size: [3, 5],
              shape: "round", mode: "fountain", speed: [130, 230], spread: 55, gravity: 720, duration: [650, 900] },
  steam:    { name: "Пар", colors: ["rgba(236,241,246,.8)", "rgba(206,214,222,.65)"], count: 9, size: [12, 22],
              shape: "round", blur: 2, mode: "rise", rise: [60, 110], grow: 2.2, duration: [1100, 1600] },
  smoke:    { name: "Дым", colors: ["rgba(120,120,126,.75)", "rgba(86,86,92,.7)", "rgba(150,146,138,.6)"], count: 9, size: [12, 22],
              shape: "round", blur: 2, mode: "rise", rise: [50, 95], grow: 2.4, duration: [1300, 1900] },
  magic:    { name: "Магия", colors: ["#f0d4ff", "#b57bff", "#7ee8ff", "#ffffff"], count: 14, size: [3, 5],
              shape: "square", glow: true, mode: "spiral", rise: [60, 110], duration: [900, 1300] },
  electric: { name: "Разряды", colors: ["#f0fcff", "#8fe6ff", "#4cc4ff"], count: 10, size: [2, 3], length: [14, 26],
              shape: "streak", glow: true, mode: "radial", dist: [36, 72], duration: [220, 380] },
};

// Направление заливки пиксельного прогресса (clip-path, см. mechFxClip).
const MECH_FX_FILLS = {
  right: "слева направо",
  left: "справа налево",
  up: "снизу вверх",
  down: "сверху вниз",
};

// Модельки слотов свободной расстановки (appearance.layout.cells[].shape и
// output.shape): w/h - пропорции коробки к size слота (бак вытянут вверх).
const MECH_FX_SLOTS = {
  square: { name: "Квадрат", w: 1, h: 1 },
  round: { name: "Чаша (круг)", w: 1, h: 1 },
  tank: { name: "Бак для жидкости", w: 0.8, h: 1.3 },
};

// Как ингредиенты улетают в механизм при крафте (appearance.gather) - в любой
// расстановке, и сеткой, и свободной.
const MECH_FX_GATHERS = {
  dash: { name: "Рывок", hint: "все разом отскакивают назад и летят в результат" },
  vortex: { name: "Воронка (как ритуальный круг)", hint: "по очереди затягиваются по спирали, по прилёту - ударная волна" },
  arc: { name: "Дугой", hint: "по очереди перелетают дугой, как в котёл" },
};

const MECH_FX_TEXTURE_PX = 3;        // css-пикселей на пиксель текстуры фона
const MECH_FX_DEFAULT_DIM = 0.55;    // затемнение текстуры, если texture_dim не задан
const MECH_FX_PROGRESS_SIZE = 50;    // длинная сторона пиксельного прогресса, px

// Свободная расстановка (appearance.layout): центры элементов (x, y) хранятся в
// координатах холста редактора; в игре сцена - только занятая область с полями
// (mechFxLayoutGeometry: bounds). Держать в паре с tools/content_validation.py
// (MECHANISM_LAYOUT_W/H, MECHANISM_SLOT_SIZE_RANGE).
const MECH_FX_LAYOUT_W = 560;
const MECH_FX_LAYOUT_H = 400;
const MECH_FX_CELL_SIZE = 78;        // сторона ячейки по умолчанию (как .cg-cell игры)
const MECH_FX_OUTPUT_SIZE = 92;      // сторона слота результата (как .cg-output)
const MECH_FX_SLOT_SIZE_RANGE = [40, 140];
const MECH_FX_PROGRESS_BOX = 56;     // место под прогресс-бар (стрелка или картинка)
const MECH_FX_LAYOUT_PAD = 18;       // поля сцены вокруг занятой области
const MECH_FX_DASH_MS = 280;         // полёт «рывком» (прежний эффект модалки механизма)

function mechFxReducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function mechFxRand(range) {
  return range[0] + Math.random() * (range[1] - range[0]);
}

// ---- стили: один <style> на страницу, вставляется при первом обращении ----

function mechFxInjectStyles() {
  if (document.getElementById("mechFxStyles")) return;
  const origins = Object.entries(MECH_FX_MOTIONS)
    .map(([id, m]) => `.mfx-m-${id}, .mfx-hit-${id} { transform-origin: ${m.origin}; }`).join("\n");
  const style = document.createElement("style");
  style.id = "mechFxStyles";
  style.textContent = `
.mfx-art { position: relative; display: flex; align-items: center; justify-content: center; }
.mfx-art > svg, .mfx-fill > svg { display: block; flex-shrink: 0; }
.mfx-base { transition: filter .25s; }
.mfx-art.mfx-active .mfx-base { filter: brightness(.3) saturate(.35); }
.mfx-fill {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  filter: drop-shadow(0 0 4px rgba(255,225,160,.55)); opacity: 0; transition: opacity .25s;
}
.mfx-art.mfx-active .mfx-fill { opacity: 1; }
.mfx-p { position: absolute; pointer-events: none; will-change: transform, opacity; }
${origins}

.mfx-running.mfx-m-strike { animation: mfx-loop-strike 1.6s ease-in-out infinite; }
.mfx-hit-strike { animation: mfx-hit-strike .5s ease-out; }
@keyframes mfx-loop-strike {
  0% { transform: rotate(0); } 48% { transform: rotate(-32deg); } 60% { transform: rotate(10deg); }
  68% { transform: rotate(-3deg); } 76%, 100% { transform: rotate(0); }
}
@keyframes mfx-hit-strike {
  0% { transform: rotate(0); } 35% { transform: rotate(-40deg); } 60% { transform: rotate(14deg); }
  78% { transform: rotate(-4deg); } 100% { transform: rotate(0); }
}

.mfx-running.mfx-m-saw { animation: mfx-loop-saw .14s linear infinite alternate; }
.mfx-hit-saw { animation: mfx-hit-saw .42s linear; }
@keyframes mfx-loop-saw { from { transform: translateX(-3px); } to { transform: translateX(3px); } }
@keyframes mfx-hit-saw {
  0%, 100% { transform: none; } 15% { transform: translateX(-7px); } 32% { transform: translateX(7px); }
  50% { transform: translateX(-6px); } 68% { transform: translateX(6px); } 85% { transform: translateX(-3px); }
}

.mfx-running.mfx-m-press { animation: mfx-loop-press 1.2s ease-in-out infinite; }
.mfx-hit-press { animation: mfx-hit-press .45s ease-out; }
@keyframes mfx-loop-press {
  0%, 100% { transform: none; } 45% { transform: translateY(-4px); }
  60% { transform: translateY(2px) scale(1.06, .88); } 75% { transform: none; }
}
@keyframes mfx-hit-press {
  0%, 100% { transform: none; } 30% { transform: translateY(-7px) scaleY(1.06); }
  55% { transform: translateY(3px) scale(1.12, .78); } 80% { transform: scale(.97, 1.04); }
}

.mfx-running.mfx-m-spin { animation: mfx-loop-spin 2.4s linear infinite; }
.mfx-hit-spin { animation: mfx-hit-spin .5s cubic-bezier(.2,.8,.3,1); }
@keyframes mfx-loop-spin { to { transform: rotate(360deg); } }
@keyframes mfx-hit-spin { from { transform: rotate(0); } to { transform: rotate(180deg); } }

.mfx-running.mfx-m-flicker { animation: mfx-loop-flicker .9s steps(1, end) infinite; }
.mfx-hit-flicker { animation: mfx-hit-flicker .45s ease-out; }
@keyframes mfx-loop-flicker {
  0% { transform: none; filter: brightness(1); } 25% { transform: scale(1.05, .96); filter: brightness(1.18); }
  50% { transform: scale(.97, 1.05); filter: brightness(.92); } 75% { transform: scale(1.03, .98); filter: brightness(1.22); }
}
@keyframes mfx-hit-flicker {
  0%, 100% { transform: none; filter: none; } 30% { transform: scale(1.25); filter: brightness(1.8); }
}

.mfx-running.mfx-m-pulse { animation: mfx-loop-pulse 1.4s ease-in-out infinite; }
.mfx-hit-pulse { animation: mfx-hit-pulse .45s ease-out; }
@keyframes mfx-loop-pulse {
  0%, 100% { transform: none; filter: drop-shadow(0 0 0 rgba(140,220,255,0)); }
  50% { transform: scale(1.05); filter: drop-shadow(0 0 7px rgba(140,220,255,.85)); }
}
@keyframes mfx-hit-pulse {
  0%, 100% { transform: none; filter: none; }
  35% { transform: scale(1.22); filter: brightness(1.5) drop-shadow(0 0 10px rgba(140,220,255,.95)); }
}

/* ---- модельки слотов (mechFxDecorateSlot); цвета состояний (занята, призрак) - у страницы ---- */
.mfx-slot.mfx-slot-round { border-radius: 50%; }
.mfx-slot.mfx-slot-tank { border-radius: 16px / 12px; }
.mfx-deco { position: absolute; pointer-events: none; }
.mfx-rim { inset: 6px; border-radius: 50%; border: 1px dashed rgba(240,160,48,.35); }
.mfx-tank-inner { inset: 9px 6px 9px 6px; border-radius: 8px / 6px; overflow: hidden; }
.mfx-tank-liquid { left: 0; right: 0; bottom: 0; transition: height .3s ease-out; }
.mfx-tank-wave {
  position: absolute; left: -50%; width: 200%; top: -3px; height: 7px; border-radius: 50%;
  animation: mfx-wave 2.6s ease-in-out infinite alternate;
}
.mfx-tank-bubble {
  position: absolute; bottom: 0; width: 4px; height: 4px; border-radius: 50%;
  background: rgba(255,255,255,.6); animation: mfx-bubble 2.4s linear infinite;
}
.mfx-tank-glass { top: 12px; bottom: 14px; left: 9px; width: 5px; border-radius: 3px; background: linear-gradient(rgba(255,255,255,.26), rgba(255,255,255,.04)); }
.mfx-tank-ticks { top: 18px; bottom: 18px; right: 8px; width: 5px; background: repeating-linear-gradient(to bottom, rgba(255,255,255,.2) 0 1px, transparent 1px 9px); }
.mfx-tank-cap { left: 3px; right: 3px; height: 5px; border-radius: 3px; background: linear-gradient(#66707b, #2d3238); }
.mfx-tank-cap.top { top: 2px; }
.mfx-tank-cap.bottom { bottom: 2px; }
.mfx-slot-mini .mfx-tank-inner { inset: 5px 3px; }
.mfx-slot-mini .mfx-tank-cap { height: 3px; left: 2px; right: 2px; top: 1px; }
.mfx-slot-mini .mfx-tank-cap.bottom { top: auto; bottom: 1px; }
.mfx-slot-mini .mfx-rim { inset: 3px; }
@keyframes mfx-wave { from { transform: translateX(-12%); } to { transform: translateX(12%); } }
@keyframes mfx-bubble {
  0% { transform: translateY(0); opacity: 0; } 15% { opacity: .9; }
  100% { transform: translateY(-46px); opacity: 0; }
}

/* ---- каналы от ячеек к механизму (layout.links) ---- */
.mfx-links { position: absolute; left: 0; top: 0; pointer-events: none; overflow: visible; }
.mfx-link { fill: none; stroke: rgba(240,160,48,.3); stroke-width: 2; stroke-dasharray: 5 6; }
.mfx-link.mfx-lit {
  stroke: #ffd27a; stroke-width: 3; filter: drop-shadow(0 0 4px rgba(255,200,90,.9));
  animation: mfx-link-flow .45s linear infinite;
}
@keyframes mfx-link-flow { to { stroke-dashoffset: -22; } }

/* ---- прогресс кольцом вокруг результата (layout.progress = "ring") ---- */
.mfx-ring > svg { position: absolute; inset: 0; overflow: visible; }
.mfx-ring-base { fill: none; stroke: rgba(240,160,48,.22); stroke-width: 3; }
.mfx-ring-fill {
  fill: none; stroke: #ffc861; stroke-width: 4; stroke-linecap: round;
  filter: drop-shadow(0 0 4px rgba(255,200,90,.8)); opacity: 0; transition: opacity .25s;
}
.mfx-art.mfx-active .mfx-ring-fill { opacity: 1; }
.mfx-running.mfx-ring { animation: mfx-ring-breathe 1.8s ease-in-out infinite; }
@keyframes mfx-ring-breathe { 0%, 100% { filter: none; } 50% { filter: drop-shadow(0 0 8px rgba(255,200,90,.7)); } }
.mfx-ring-hit { animation: mfx-ring-hit .5s ease-out; }
@keyframes mfx-ring-hit {
  0%, 100% { transform: none; filter: none; }
  35% { transform: scale(1.1); filter: brightness(1.6) drop-shadow(0 0 10px rgba(255,210,120,.95)); }
}

/* ---- сбор ингредиентов (mechFxGather) и ударная волна ---- */
.mfx-fly {
  position: absolute; display: flex; align-items: center; justify-content: center;
  filter: drop-shadow(0 0 6px rgba(255,190,80,.8)); will-change: transform; pointer-events: none;
}
.mfx-shock {
  position: absolute; border-radius: 50%; pointer-events: none;
  border: 2px solid rgba(255,222,160,.85); box-shadow: 0 0 18px 4px rgba(255,190,90,.45), inset 0 0 14px rgba(255,210,130,.4);
}

@media (prefers-reduced-motion: reduce) {
  .mfx-art.mfx-running, [class*="mfx-hit-"], .mfx-ring-hit, .mfx-tank-wave, .mfx-tank-bubble, .mfx-link.mfx-lit { animation: none !important; }
}
`;
  document.head.appendChild(style);
}

// ---- пиксельная графика ----

function mechFxSvg(icon, width, height) {
  const ns = "http://www.w3.org/2000/svg";
  const rows = icon.grid.length, cols = icon.grid[0].length;
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${cols} ${rows}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("shape-rendering", "crispEdges");
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const ch = icon.grid[y][x];
      if (ch === ".") continue;
      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", x);
      rect.setAttribute("y", y);
      rect.setAttribute("width", 1);
      rect.setAttribute("height", 1);
      rect.setAttribute("fill", icon.palette[ch]);
      svg.appendChild(rect);
    }
  }
  return svg;
}

// Иконка, обрезанная по закрашенным пикселям: редактор рисует на холсте 32×32,
// и пустые поля вокруг рисунка заливка проходила бы впустую (стоит на месте).
function mechFxCropIcon(icon) {
  let top = Infinity, bottom = -1, left = Infinity, right = -1;
  icon.grid.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === ".") continue;
      top = Math.min(top, y); bottom = Math.max(bottom, y);
      left = Math.min(left, x); right = Math.max(right, x);
    }
  });
  if (bottom < 0) return icon;
  return { grid: icon.grid.slice(top, bottom + 1).map(row => row.slice(left, right + 1)), palette: icon.palette };
}

// Размер прогресса по пропорциям сетки (длинная сторона = size): иначе у
// неквадратной иконки по краям были бы пустые поля, и заливка на них «стояла» бы.
function mechFxIconBox(icon, size) {
  const rows = icon.grid.length, cols = icon.grid[0].length;
  const k = size / Math.max(rows, cols);
  return [Math.round(cols * k), Math.round(rows * k)];
}

// Фон сцены: плитка текстуры (data-URI SVG) под полупрозрачным затемнением -
// предметы в ячейках должны оставаться читаемыми на любой текстуре.
const MECH_FX_TEXTURE_CACHE = new Map();
function mechFxTextureStyle(appearance) {
  const texture = appearance && appearance.texture;
  if (!texture) return null;
  const dim = typeof appearance.texture_dim === "number" ? appearance.texture_dim : MECH_FX_DEFAULT_DIM;
  const rows = texture.grid.length, cols = texture.grid[0].length;
  const w = cols * MECH_FX_TEXTURE_PX, h = rows * MECH_FX_TEXTURE_PX;
  const key = `${texture.grid.join("\n")}|${JSON.stringify(texture.palette)}`;
  let url = MECH_FX_TEXTURE_CACHE.get(key);
  if (!url) {
    let rects = "";
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const ch = texture.grid[y][x];
        if (ch === ".") continue;
        rects += `<rect x="${x}" y="${y}" width="1" height="1" fill="${texture.palette[ch]}"/>`;
      }
    }
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${cols} ${rows}" width="${w}" height="${h}" shape-rendering="crispEdges">${rects}</svg>`;
    url = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    MECH_FX_TEXTURE_CACHE.set(key, url);
  }
  return {
    backgroundImage: `linear-gradient(rgba(0,0,0,${dim}), rgba(0,0,0,${dim})), ${url}`,
    backgroundSize: `auto, ${w}px ${h}px`,
    backgroundRepeat: "repeat",
  };
}

// ---- прогресс ----

// Пиксельный прогресс: тусклая иконка + яркая копия, открываемая clip-path по
// направлению fill (как стрелка печи в Minecraft, только любой формы).
// Возвращает {art, fill}; art - узор движения (класс mfx-m-<motion>).
function mechFxProgressArt(icon, motion, size = MECH_FX_PROGRESS_SIZE) {
  mechFxInjectStyles();
  icon = mechFxCropIcon(icon);
  const [w, h] = mechFxIconBox(icon, size);
  const art = document.createElement("div");
  art.className = "mfx-art" + (motion && MECH_FX_MOTIONS[motion] ? ` mfx-m-${motion}` : "");
  art.style.width = `${w}px`;
  art.style.height = `${h}px`;
  const base = mechFxSvg(icon, w, h);
  base.classList.add("mfx-base");
  const fill = document.createElement("div");
  fill.className = "mfx-fill";
  fill.appendChild(mechFxSvg(icon, w, h));
  art.append(base, fill);
  return { art, fill };
}

// Обёртка движения вокруг чего угодно (стрелка по умолчанию без своей иконки).
function mechFxMotionWrap(children, motion) {
  mechFxInjectStyles();
  const art = document.createElement("div");
  art.className = "mfx-art" + (motion && MECH_FX_MOTIONS[motion] ? ` mfx-m-${motion}` : "");
  children.forEach(c => art.appendChild(c));
  return art;
}

function mechFxClip(dir, ratio) {
  const rest = `${Math.round((1 - Math.max(0, Math.min(1, ratio))) * 1000) / 10}%`;
  if (dir === "left") return `inset(0 0 0 ${rest})`;
  if (dir === "up") return `inset(${rest} 0 0 0)`;
  if (dir === "down") return `inset(0 0 ${rest} 0)`;
  return `inset(0 ${rest} 0 0)`;
}

// Заливка едет от прошлого значения к новому (двойной rAF - иначе стартовое
// значение ещё не отрисовано и transition не с чего запускать).
function mechFxSetFill(fill, dir, from, to) {
  fill.style.transition = "none";
  fill.style.clipPath = mechFxClip(dir, from);
  if (from === to || mechFxReducedMotion()) { fill.style.clipPath = mechFxClip(dir, to); return; }
  requestAnimationFrame(() => requestAnimationFrame(() => {
    fill.style.transition = "clip-path .45s ease-out, opacity .25s";
    fill.style.clipPath = mechFxClip(dir, to);
  }));
}

// active - прогресс виден (тусклая основа + заливка); running - крутится
// фоновое движение, пока механизм занят долгим крафтом.
function mechFxSetState(art, { active, running }) {
  art.classList.toggle("mfx-active", !!active);
  art.classList.toggle("mfx-running", !!running);
}

// Одноразовое движение (удар молота, рывок пилы...) - на ВНЕШНЕМ узле, чтобы
// не спорить с фоновым циклом на art: трансформации родителя и ребёнка складываются.
function mechFxHit(node, motion) {
  if (!node || !MECH_FX_MOTIONS[motion] || mechFxReducedMotion()) return;
  mechFxInjectStyles();
  const cls = `mfx-hit-${motion}`;
  node.classList.remove(cls);
  void node.offsetWidth;
  node.classList.add(cls);
  const done = (e) => {
    if (e.target !== node) return;
    node.classList.remove(cls);
    node.removeEventListener("animationend", done);
  };
  node.addEventListener("animationend", done);
}

// ---- частицы ----

function mechFxLayer() {
  let layer = document.getElementById("mfxLayer");
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "mfxLayer";
    layer.style.cssText = "position:fixed; inset:0; pointer-events:none; z-index:10000; overflow:hidden;";
    document.body.appendChild(layer);
  }
  return layer;
}

// Выброс частиц в точке (x, y) экрана. preset - id из MECH_FX_PARTICLES или null
// (стандартный разлёт искрами цвета результата - opts.colors). opts.scale < 1 -
// маленький «пшик» (удар по ходу долгого крафта), а не полноценный выброс.
function mechFxEmit(layer, x, y, preset, opts = {}) {
  if (mechFxReducedMotion()) return;
  mechFxInjectStyles();
  layer = layer || mechFxLayer();
  const scale = opts.scale || 1;
  const cfg = MECH_FX_PARTICLES[preset] || {
    colors: opts.colors && opts.colors.length ? opts.colors : ["#f0a030", "#ffd27a"],
    count: 12, size: [4, 7], shape: "square", glow: true, mode: "radial", dist: [50, 84], duration: [420, 620],
  };
  const count = Math.max(3, Math.round(cfg.count * scale));
  const reach = scale < 1 ? 0.65 : 1;
  for (let k = 0; k < count; k++) {
    const color = cfg.colors[k % cfg.colors.length];
    const size = Math.round(mechFxRand(cfg.size));
    const duration = mechFxRand(cfg.duration);
    const node = document.createElement("div");
    node.className = "mfx-p";
    const w = cfg.shape === "streak" ? Math.round(mechFxRand(cfg.length)) : size;
    node.style.cssText = `left:${x - w / 2}px; top:${y - size / 2}px; width:${w}px; height:${size}px; background:${color};`
      + (cfg.shape === "round" ? "border-radius:50%;" : "")
      + (cfg.glow ? `box-shadow:0 0 6px ${color};` : "")
      + (cfg.blur ? `filter:blur(${cfg.blur}px);` : "");
    layer.appendChild(node);
    const frames = mechFxFrames(cfg, k, count, reach, duration);
    const anim = node.animate(frames.keyframes, { duration, easing: frames.easing, fill: "forwards" });
    anim.onfinish = () => node.remove();
  }
}

function mechFxFrames(cfg, k, count, reach, duration) {
  const T = duration / 1000;
  if (cfg.mode === "fountain") {
    const angle = (-90 + (Math.random() - 0.5) * cfg.spread) * Math.PI / 180;
    const v = mechFxRand(cfg.speed) * reach;
    const vx = Math.cos(angle) * v, vy = Math.sin(angle) * v;
    const spin = cfg.spin ? (Math.random() - 0.5) * 1080 : 0;
    const steps = 8, keyframes = [];
    for (let i = 0; i <= steps; i++) {
      const p = i / steps, t = p * T;
      const dx = vx * t, dy = vy * t + cfg.gravity * t * t / 2;
      keyframes.push({
        transform: `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${(spin * p).toFixed(0)}deg)`,
        opacity: p < 0.65 ? 1 : 1 - (p - 0.65) / 0.35,
      });
    }
    return { keyframes, easing: "linear" };
  }
  if (cfg.mode === "rise") {
    const drift = (Math.random() - 0.5) * 50;
    const rise = mechFxRand(cfg.rise) * reach;
    const start = (Math.random() - 0.5) * 24;
    return {
      easing: "ease-out",
      keyframes: [
        { transform: `translate(${start}px, 0) scale(.5)`, opacity: 0 },
        { transform: `translate(${start + drift * 0.3}px, ${-rise * 0.25}px) scale(1)`, opacity: 1, offset: 0.2 },
        { transform: `translate(${start + drift}px, ${-rise}px) scale(${cfg.grow})`, opacity: 0 },
      ],
    };
  }
  if (cfg.mode === "spiral") {
    const rise = mechFxRand(cfg.rise) * reach;
    const phase = (k / count) * Math.PI * 2;
    const steps = 8, keyframes = [];
    for (let i = 0; i <= steps; i++) {
      const p = i / steps;
      const r = 10 + 26 * p;
      keyframes.push({
        transform: `translate(${(Math.cos(phase + p * Math.PI * 3) * r).toFixed(1)}px, ${(-rise * p).toFixed(1)}px)`,
        opacity: i === 0 ? 0 : i === steps ? 0 : (i % 2 ? 1 : 0.35),
      });
    }
    return { keyframes, easing: "linear" };
  }
  // radial
  const angle = (k / count) * Math.PI * 2 + Math.random() * 0.45;
  const dist = mechFxRand(cfg.dist) * reach;
  const rot = cfg.shape === "streak" ? ` rotate(${(angle * 180 / Math.PI).toFixed(0)}deg)` : "";
  return {
    easing: "cubic-bezier(.15,.8,.3,1)",
    keyframes: [
      { transform: `translate(0px, 0px)${rot} scale(1)`, opacity: 1 },
      { transform: `translate(${(Math.cos(angle) * dist).toFixed(1)}px, ${(Math.sin(angle) * dist).toFixed(1)}px)${rot} scale(.3)`, opacity: 0 },
    ],
  };
}

// ---- свободная расстановка слотов (appearance.layout) ----
// layout = {cells: [{x, y, shape?, size?}] (по одной на ячейку сетки, построчно - индекс
// тот же, что у узора), output: {x, y, shape?, size?}, progress: {x, y, angle?} | "ring",
// links?: bool}. Движок про расстановку не знает: это только вид той же сетки width×height.

function mechFxSlotBox(item, base) {
  const shape = item && MECH_FX_SLOTS[item.shape] ? item.shape : "square";
  const size = item && typeof item.size === "number" ? item.size : base;
  return { shape, w: Math.round(size * MECH_FX_SLOTS[shape].w), h: Math.round(size * MECH_FX_SLOTS[shape].h) };
}

// Геометрия расстановки в координатах холста: центры и коробки ячеек, результата и
// прогресса + bounds - занятая область с полями (ровно она и есть сцена в игре).
// count - число ячеек сетки механизма (width×height); недостающие в layout ячейки
// (сетку увеличили, а расстановку не обновили) встают в угол, а не роняют сцену.
function mechFxLayoutGeometry(layout, count) {
  const cells = [];
  for (let i = 0; i < count; i++) {
    const c = (layout.cells || [])[i] || { x: MECH_FX_CELL_SIZE / 2, y: MECH_FX_CELL_SIZE / 2 };
    cells.push({ x: c.x, y: c.y, ...mechFxSlotBox(c, MECH_FX_CELL_SIZE) });
  }
  const o = layout.output || { x: MECH_FX_LAYOUT_W / 2, y: MECH_FX_LAYOUT_H / 2 };
  const output = { x: o.x, y: o.y, ...mechFxSlotBox(o, MECH_FX_OUTPUT_SIZE) };
  let progress;
  if (layout.progress === "ring") {
    progress = { ring: true, x: output.x, y: output.y, angle: 0, d: Math.max(output.w, output.h) + 22 };
  } else {
    const p = layout.progress && typeof layout.progress === "object" ? layout.progress : null;
    const cx = cells.length ? cells.reduce((s, c) => s + c.x, 0) / cells.length : output.x;
    const cy = cells.length ? cells.reduce((s, c) => s + c.y, 0) / cells.length : output.y;
    progress = {
      ring: false,
      x: p ? p.x : (cx + output.x) / 2,
      y: p ? p.y : (cy + output.y) / 2,
      angle: p && typeof p.angle === "number" ? p.angle : 0,
    };
  }
  const boxes = [...cells, output].map(b => [b.x - b.w / 2, b.y - b.h / 2, b.x + b.w / 2, b.y + b.h / 2]);
  const half = progress.ring ? progress.d / 2 : MECH_FX_PROGRESS_BOX / 2;
  boxes.push([progress.x - half, progress.y - half, progress.x + half, progress.y + half]);
  const left = Math.min(...boxes.map(b => b[0])) - MECH_FX_LAYOUT_PAD;
  const top = Math.min(...boxes.map(b => b[1])) - MECH_FX_LAYOUT_PAD;
  const right = Math.max(...boxes.map(b => b[2])) + MECH_FX_LAYOUT_PAD;
  const bottom = Math.max(...boxes.map(b => b[3])) + MECH_FX_LAYOUT_PAD;
  return {
    cells, output, progress, links: !!layout.links,
    bounds: { left, top, width: Math.round(right - left), height: Math.round(bottom - top) },
  };
}

// Коробка элемента (центр x/y, размер w/h) в координатах сцены со сдвигом ox/oy и
// масштабом k - для style.left/top/width/height.
function mechFxPlace(node, item, ox = 0, oy = 0, k = 1) {
  node.style.left = `${Math.round((item.x - item.w / 2 - ox) * k)}px`;
  node.style.top = `${Math.round((item.y - item.h / 2 - oy) * k)}px`;
  node.style.width = `${Math.round(item.w * k)}px`;
  node.style.height = `${Math.round(item.h * k)}px`;
}

// Каналы: линии от каждой ячейки к механизму (прогресс-бар; если прогресс - кольцо,
// то сразу к результату) и от прогресс-бара к результату. data-cell - индекс ячейки,
// по нему mechFxGather подсвечивает канал летящего ингредиента.
function mechFxLinksSvg(geo, ox = 0, oy = 0, k = 1) {
  mechFxInjectStyles();
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "mfx-links");
  svg.setAttribute("width", Math.round(geo.bounds.width * k));
  svg.setAttribute("height", Math.round(geo.bounds.height * k));
  const hub = geo.progress.ring ? geo.output : geo.progress;
  const line = (a, b, tag) => {
    const l = document.createElementNS(ns, "line");
    l.setAttribute("class", "mfx-link");
    l.setAttribute("x1", ((a.x - ox) * k).toFixed(1));
    l.setAttribute("y1", ((a.y - oy) * k).toFixed(1));
    l.setAttribute("x2", ((b.x - ox) * k).toFixed(1));
    l.setAttribute("y2", ((b.y - oy) * k).toFixed(1));
    if (k < 1) l.style.strokeWidth = "1.5";
    l.dataset.cell = tag;
    svg.appendChild(l);
  };
  geo.cells.forEach((c, i) => line(c, hub, String(i)));
  if (!geo.progress.ring) line(geo.progress, geo.output, "out");
  return svg;
}

function mechFxLightLink(svg, tag, ms) {
  const link = svg && svg.querySelector(`.mfx-link[data-cell="${tag}"]`);
  if (!link) return;
  link.classList.add("mfx-lit");
  clearTimeout(link._mfxTimer);
  link._mfxTimer = setTimeout(() => link.classList.remove("mfx-lit"), ms);
}

// Главный цвет пиксельной иконки - самый частый не тёмный цвет палитры (жидкость
// в баке красится предметом, который в нём лежит).
function mechFxIconMainColor(icon) {
  if (!icon || !icon.grid) return null;
  const counts = {};
  icon.grid.forEach(row => { for (const ch of row) if (ch !== ".") counts[ch] = (counts[ch] || 0) + 1; });
  let best = null, bestCount = -1, fallback = null;
  Object.entries(counts).forEach(([ch, n]) => {
    const color = icon.palette[ch];
    const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(color || ""));
    if (!m) return;
    fallback = fallback || color;
    const hex = m[1].length === 3 ? m[1].split("").map(c => c + c).join("") : m[1];
    const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
    if (0.299 * r + 0.587 * g + 0.114 * b < 60) return;
    if (n > bestCount) { best = color; bestCount = n; }
  });
  return best || fallback;
}

// Смешать #rrggbb (или #rgb) с белым (t > 0) или чёрным (t < 0).
function mechFxShade(hex, t) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const full = m[1].length === 3 ? m[1].split("").map(c => c + c).join("") : m[1];
  const mix = (v) => Math.round(t >= 0 ? v + (255 - v) * t : v * (1 + t));
  const [r, g, b] = [0, 2, 4].map(i => mix(parseInt(full.slice(i, i + 2), 16)));
  return `rgb(${r}, ${g}, ${b})`;
}

// Моделька слота поверх обычной ячейки страницы: класс формы + детали. Вызывать
// ДО того, как страница положит в слот иконку предмета, - детали рисуются под ней.
// opts: {color - цвет жидкости бака, level - 0..1 насколько он полон, mini - слот
// в миниатюре справочника (без пузырьков и делений)}.
function mechFxDecorateSlot(node, shape, opts = {}) {
  mechFxInjectStyles();
  shape = MECH_FX_SLOTS[shape] ? shape : "square";
  node.classList.add("mfx-slot", `mfx-slot-${shape}`);
  if (opts.mini) node.classList.add("mfx-slot-mini");
  const deco = (cls) => {
    const d = document.createElement("div");
    d.className = `mfx-deco ${cls}`;
    node.appendChild(d);
    return d;
  };
  if (shape === "round") deco("mfx-rim");
  if (shape !== "tank") return;
  const inner = deco("mfx-tank-inner");
  const level = Math.max(0, Math.min(1, opts.level || 0));
  if (opts.color && level > 0) {
    const liquid = document.createElement("div");
    liquid.className = "mfx-deco mfx-tank-liquid";
    liquid.style.height = `${Math.round(level * 100)}%`;
    liquid.style.background = `linear-gradient(${mechFxShade(opts.color, 0.18)}, ${mechFxShade(opts.color, -0.35)})`;
    liquid.style.opacity = "0.82";
    const wave = document.createElement("div");
    wave.className = "mfx-tank-wave";
    wave.style.background = mechFxShade(opts.color, 0.45);
    liquid.appendChild(wave);
    if (!opts.mini) {
      [[22, 0], [55, 0.9], [74, 1.7]].forEach(([left, delay]) => {
        const b = document.createElement("div");
        b.className = "mfx-tank-bubble";
        b.style.left = `${left}%`;
        b.style.animationDelay = `${delay}s`;
        liquid.appendChild(b);
      });
    }
    inner.appendChild(liquid);
  }
  deco("mfx-tank-glass");
  if (!opts.mini) deco("mfx-tank-ticks");
  deco("mfx-tank-cap top");
  deco("mfx-tank-cap bottom");
}

// Прогресс кольцом вокруг результата: тусклое кольцо + яркая дуга, которая
// дорастает по часовой стрелке от верхней точки. Возвращает {art, setFill(from, to)}.
function mechFxRingProgress(diameter) {
  mechFxInjectStyles();
  const ns = "http://www.w3.org/2000/svg";
  const art = document.createElement("div");
  art.className = "mfx-art mfx-ring";
  art.style.width = `${diameter}px`;
  art.style.height = `${diameter}px`;
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${diameter} ${diameter}`);
  svg.setAttribute("width", diameter);
  svg.setAttribute("height", diameter);
  const c = diameter / 2, r = c - 3, len = 2 * Math.PI * r;
  const circle = (cls) => {
    const node = document.createElementNS(ns, "circle");
    node.setAttribute("class", cls);
    node.setAttribute("cx", c);
    node.setAttribute("cy", c);
    node.setAttribute("r", r);
    svg.appendChild(node);
    return node;
  };
  circle("mfx-ring-base");
  const fill = circle("mfx-ring-fill");
  fill.setAttribute("transform", `rotate(-90 ${c} ${c})`);
  fill.style.strokeDasharray = `${len}`;
  fill.style.strokeDashoffset = `${len}`;
  art.appendChild(svg);
  const offset = (ratio) => `${(len * (1 - Math.max(0, Math.min(1, ratio)))).toFixed(1)}`;
  const setFill = (from, to) => {
    fill.style.transition = "none";
    fill.style.strokeDashoffset = offset(from);
    if (from === to || mechFxReducedMotion()) { fill.style.strokeDashoffset = offset(to); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      fill.style.transition = "stroke-dashoffset .45s ease-out, opacity .25s";
      fill.style.strokeDashoffset = offset(to);
    }));
  };
  return { art, setFill };
}

// ---- сбор ингредиентов в механизм (appearance.gather) ----

// Кадры полёта одного ингредиента: dx/dy - от центра его ячейки до цели, k - его
// номер из n (у «воронки» и «дуги» летят по очереди), into - исчезает внутри
// (долгий крафт: ингредиенты уходят в прогресс-бар, результата ещё нет).
function mechFxGatherPlan(style, dx, dy, k, n, into) {
  if (style === "vortex") {
    const step = n > 1 ? Math.min(120, 900 / (n - 1)) : 0;
    const r0 = Math.hypot(dx, dy) || 1;
    const a0 = Math.atan2(-dy, -dx);
    const steps = 14, keyframes = [];
    for (let i = 0; i <= steps; i++) {
      const p = i / steps;
      if (p <= 0.15) {
        keyframes.push({ transform: `translate(0px, 0px) rotate(0deg) scale(${(1 + p * 1.2).toFixed(3)})`, opacity: 1, offset: p });
        continue;
      }
      const q = (p - 0.15) / 0.85;
      const radius = r0 * Math.pow(1 - q, 1.35);
      const angle = a0 + q * Math.PI * 1.5;
      const x = dx + radius * Math.cos(angle), y = dy + radius * Math.sin(angle);
      const scale = 1.18 + ((into ? 0.1 : 0.3) - 1.18) * q;
      keyframes.push({
        transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${(q * 300).toFixed(0)}deg) scale(${scale.toFixed(3)})`,
        opacity: i === steps ? (into ? 0 : 0.85) : 1, offset: p,
      });
    }
    return { delay: k * step, duration: 760, easing: "cubic-bezier(.45,0,.85,.75)", keyframes };
  }
  if (style === "arc") {
    const step = n > 1 ? Math.min(80, 600 / (n - 1)) : 0;
    const h = -(50 + 0.25 * Math.hypot(dx, dy));
    const steps = 12, keyframes = [];
    for (let i = 0; i <= steps; i++) {
      const p = i / steps;
      const x = dx * p, y = dy * p + h * 4 * p * (1 - p);
      const scale = 1 + ((into ? 0.2 : 0.5) - 1) * p;
      keyframes.push({
        transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${(220 * p).toFixed(0)}deg) scale(${scale.toFixed(3)})`,
        opacity: i === steps ? (into ? 0 : 0.9) : 1,
      });
    }
    return { delay: k * step, duration: 560, easing: "cubic-bezier(.35,0,.65,1)", keyframes };
  }
  // dash: короткий замах назад и ускорение к цели (все разом, с лёгким разбросом).
  return {
    delay: Math.min(k * 16, 90), duration: MECH_FX_DASH_MS, easing: "linear",
    keyframes: [
      { transform: "translate(0px, 0px) scale(1)", opacity: 1, easing: "ease-out" },
      { transform: `translate(${-dx * 0.06}px, ${-dy * 0.06 - 4}px) scale(1.12)`, opacity: 1, offset: 0.22, easing: "cubic-bezier(.7,0,1,.6)" },
      { transform: `translate(${dx}px, ${dy}px) scale(${into ? 0.15 : 0.35})`, opacity: into ? 0 : 0.9 },
    ],
  };
}

// Ударная волна из точки (x, y) экрана - кольцо до радиуса radius.
function mechFxShockwave(layer, x, y, radius) {
  if (mechFxReducedMotion()) return;
  mechFxInjectStyles();
  layer = layer || mechFxLayer();
  const ring = document.createElement("div");
  ring.className = "mfx-shock";
  ring.style.cssText += `left:${x - radius}px; top:${y - radius}px; width:${radius * 2}px; height:${radius * 2}px;`;
  layer.appendChild(ring);
  const anim = ring.animate([
    { transform: "scale(.12)", opacity: 0.95 },
    { transform: "scale(1)", opacity: 0 },
  ], { duration: 650, easing: "cubic-bezier(.2,.7,.3,1)", fill: "forwards" });
  anim.onfinish = () => ring.remove();
}

// Ингредиенты из ячеек улетают в цель (target - DOMRect результата или прогресс-бара).
// origins: [{rect, index?, make(size) -> узел иконки}]; opts: {into, layer, links -
// svg каналов (подсвечивается канал летящего), shockRadius}. Возвращает, через
// сколько мс долетит последний: эффект результата страница играет по прилёту.
function mechFxGather(origins, target, style, opts = {}) {
  if (mechFxReducedMotion() || !origins.length) return 0;
  mechFxInjectStyles();
  style = MECH_FX_GATHERS[style] ? style : "dash";
  const layer = opts.layer || mechFxLayer();
  const tx = target.left + target.width / 2, ty = target.top + target.height / 2;
  let arrive = 0;
  origins.forEach((o, k) => {
    const r = o.rect;
    const fly = document.createElement("div");
    fly.className = "mfx-fly";
    fly.style.cssText += `left:${r.left}px; top:${r.top}px; width:${r.width}px; height:${r.height}px;`;
    const icon = o.make ? o.make(Math.round(Math.min(r.width, r.height))) : null;
    if (icon) fly.appendChild(icon);
    layer.appendChild(fly);
    const plan = mechFxGatherPlan(style, tx - (r.left + r.width / 2), ty - (r.top + r.height / 2), k, origins.length, !!opts.into);
    arrive = Math.max(arrive, plan.delay + plan.duration);
    if (opts.links && o.index != null) {
      setTimeout(() => mechFxLightLink(opts.links, String(o.index), plan.duration + 150), plan.delay);
    }
    const anim = fly.animate(plan.keyframes, { duration: plan.duration, delay: plan.delay, easing: plan.easing, fill: "both" });
    anim.onfinish = () => fly.remove();
    anim.oncancel = () => fly.remove();
  });
  if (opts.links) setTimeout(() => mechFxLightLink(opts.links, "out", 500), Math.max(0, arrive - 250));
  if (style === "vortex" && !opts.into) setTimeout(() => mechFxShockwave(layer, tx, ty, opts.shockRadius || 160), arrive);
  return arrive;
}
