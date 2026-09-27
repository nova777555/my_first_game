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

const MECH_FX_TEXTURE_PX = 3;        // css-пикселей на пиксель текстуры фона
const MECH_FX_DEFAULT_DIM = 0.55;    // затемнение текстуры, если texture_dim не задан
const MECH_FX_PROGRESS_SIZE = 50;    // длинная сторона пиксельного прогресса, px

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

@media (prefers-reduced-motion: reduce) {
  .mfx-art.mfx-running, [class*="mfx-hit-"] { animation: none !important; }
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
