"use strict";
// Сцена схватки: модалка «Охота» в игре (tools/web_ui.html: openHuntingModal) и
// превью «Бой»/«Сцена охоты» в редакторе (tools/content_editor.html). Оба сервера
// отдают этот файл по /hunt_fx.js - превью и игра рисуют одно и то же.
//
// Всё, что зависит от конкретного моба или локации, приходит из контента:
//   mobs.*.combat_fx      - размер в сцене, стиль и цвет ответного удара, цвета частиц,
//                           светящиеся в темноте пиксели, фраза появления, фразы ответки,
//                           свои звуки (huntMobFx заполняет пропуски значениями по умолчанию);
//   combat_moves.*.fx     - стиль и цвет удара приёма, свой звук (huntMoveStyle);
//   tools.*.tiers[].slash_color - цвет разреза оружия;
//   locations.*.hunt_scene - фон, частицы, цвета, смена дня и ночи (huntSceneCfg).
//
// id вариантов (ключи HUNT_ATTACK_STYLES/HUNT_MOVE_STYLES/HUNT_BACKDROPS/HUNT_AMBIENTS)
// продублированы в tools/content_validation.py (HUNT_ATTACK_STYLES/HUNT_MOVE_STYLES/
// HUNT_BACKDROPS/HUNT_AMBIENTS) - держать в паре; совпадение проверяет
// tests/test_content_validation.py. Новый вариант = запись здесь + его отрисовка ниже +
// id в валидаторе; выбирается он потом в редакторе без правки кода.

// Ответный удар моба.
const HUNT_ATTACK_STYLES = {
  claw:  { name: "Когти", color: "#e03a2a", hint: "три разреза поперёк экрана" },
  bite:  { name: "Укус", color: "#e03a2a", hint: "челюсти смыкаются на экране" },
  slam:  { name: "Удар оземь", color: "#c8bea0", hint: "прыжок и ударная волна по земле" },
  spell: { name: "Заклинание", color: "#b070ff", hint: "сгусток летит в экран и взрывается" },
};

// Удар игрока (приём боя). По умолчанию - по mode приёма, см. huntMoveStyle.
const HUNT_MOVE_STYLES = {
  slash:   { name: "Дуга", hint: "размашистый удар оружием" },
  thrust:  { name: "Выпад", hint: "прицел сужается, прямой укол" },
  heavy:   { name: "Сокрушающий", hint: "затемнение, огромная дуга, трещина в земле" },
  shatter: { name: "Раскол", hint: "трещины и осколки по цели", color: "#c58cff" },
  rend:    { name: "Кровопускание", hint: "брызги, стекающие к земле", color: "#ff3a2a" },
  execute: { name: "Добивание", hint: "замедление, серый кадр, золотой крест", color: "#ffd76a" },
};

// Пиктограммы стилей удара - кнопка приёма без своей иконки (combat_moves.*.icon) в игре
// и в редакторе рисуется ими; fists - удар без оружия.
const HUNT_MOVE_GLYPHS = {
  slash: { grid: [".........BB", "........BLB", ".......BLB.", "......BLB..", ".....BLB...", "....BLB....", ".H.BLB.....", "..HLB......", "..GH.......", ".G..H......", "G.........."], palette: { B: "#8a5c1c", L: "#f0a030", H: "#f0a030", G: "#6b4a32" } },
  fists: { grid: [".SSSS..", "SLLLLS.", "SLLLLSS", "SLLLLLS", "SLLLLLS", ".SLLLS.", "..SSS.."], palette: { S: "#6b5a4a", L: "#d8b894" } },
  thrust: { grid: ["....C....", "..CCCCC..", ".C..C..C.", ".C.....C.", "CCC.R.CCC", ".C.....C.", ".C..C..C.", "..CCCCC..", "....C...."], palette: { C: "#f0a030", R: "#ff5a4a" } },
  heavy: { grid: ["HHHHHHHHH", "HLLLLLLLH", "HHHHHHHHH", "....G....", "....G....", "....G....", "....G....", "...GGG..."], palette: { H: "#7d8b96", L: "#e6edf2", G: "#8a5c1c" } },
  shatter: { grid: ["....V....", "...VVV...", "..VV.VV..", ".VV.V.VV.", "VV.V.V.VV", ".VV.V.VV.", "..VV.VV..", "...VVV...", "....V...."], palette: { V: "#c58cff" } },
  rend: { grid: ["....R....", "....R....", "...RRR...", "...RRR...", "..RRRRR..", "..RRLRR..", "..RRRRR..", "...RRR..."], palette: { R: "#d8342a", L: "#ff9a8a" } },
  execute: { grid: ["..SSSSS..", ".SSSSSSS.", "SSSSSSSSS", "SS.SSS.SS", "SS.SSS.SS", "SSSSSSSSS", ".SSS.SSS.", "..SSSSS..", "..S.S.S.."], palette: { S: "#ffd76a" } },
};

// Фон сцены охоты. ground/silhouette/fog - цвета по умолчанию, day_night - идёт ли
// небо по времени игры, ambient - частицы по умолчанию.
const HUNT_BACKDROPS = {
  forest: { name: "Лес", ground: "#1e2a1a", silhouette: "#0b1512", fog: "#94a6ac", day_night: true, ambient: "fireflies" },
  cave:   { name: "Пещера", ground: "#1d1916", silhouette: "#0d0b0a", fog: "#6e5e4e", day_night: false, ambient: "dust" },
  void:   { name: "Пустошь", ground: "#16181e", silhouette: "#0a0b0e", fog: "#6a7088", day_night: true, ambient: "none" },
};

const HUNT_AMBIENTS = {
  fireflies: { name: "Светлячки (ночью)" },
  embers:    { name: "Угольки" },
  snow:      { name: "Снег" },
  dust:      { name: "Пылинки" },
  none:      { name: "Нет" },
};

const HUNT_SIZE_DEFAULT = { mob: 42, boss: 60 };   // % высоты сцены
const HUNT_DEFAULT_SLASH = "#eef4f8";
const HUNT_ONESHOT_COLOR = "#9dffc8";

function _hsHex(h) { const n = parseInt(String(h).slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function _hsRgb(c, a = 1) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }
function _hsMix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
function _hsClamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function _hsLerp(a, b, t) { return a + (b - a) * t; }
function _hsRnd(a, b) { return a + Math.random() * (b - a); }
function _hsPick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
function _hsHsl(rgb) {
  const [r, g, b] = rgb.map(v => v / 255); const mx = Math.max(r, g, b), mn = Math.min(r, g, b); const l = (mx + mn) / 2;
  const s = mx === mn ? 0 : (l > 0.5 ? (mx - mn) / (2 - mx - mn) : (mx - mn) / (mx + mn)); return { s, l };
}
function _hsSeeded(seed) { let s = seed; return () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; }; }

// Спрайт моба из пиксельной иконки контента ({grid, palette}): картинка 1:1, рамка
// непустых пикселей, пиксели-«глаза» и цвета частиц по умолчанию.
function huntSprite(icon) {
  const grid = icon.grid, pal = icon.palette || {}, h = grid.length, w = grid[0].length;
  const canvas = document.createElement("canvas"); canvas.width = w; canvas.height = h;
  const x = canvas.getContext("2d"); let bx0 = w, by0 = h, bx1 = -1, by1 = -1; const counts = {};
  for (let y = 0; y < h; y++) for (let i = 0; i < w; i++) {
    const ch = grid[y][i]; if (ch === "." || !pal[ch]) continue;
    x.fillStyle = pal[ch]; x.fillRect(i, y, 1, 1); counts[ch] = (counts[ch] || 0) + 1;
    bx0 = Math.min(bx0, i); by0 = Math.min(by0, y); bx1 = Math.max(bx1, i); by1 = Math.max(by1, y);
  }
  if (bx1 < 0) { bx0 = 0; by0 = 0; bx1 = w - 1; by1 = h - 1; }
  // «Светятся в темноте» по умолчанию - насыщенные средне-светлые цвета палитры (глаза).
  const autoGlow = Object.keys(pal).filter(k => { const q = _hsHsl(_hsHex(pal[k])); return q.s > 0.55 && q.l > 0.45 && q.l < 0.8; });
  const autoHit = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => pal[k]);
  return { canvas, grid, pal, w, h, bx0, by0, bx1, by1, autoGlow, autoHit };
}

// Настройки моба с умолчаниями - ЕДИНСТВЕННОЕ место умолчаний combat_fx (редактор
// показывает их же как «авто»).
function huntMobFx(fx, isBoss, sprite) {
  fx = fx || {};
  const style = HUNT_ATTACK_STYLES[fx.attack_style] ? fx.attack_style : (isBoss ? "slam" : "claw");
  return {
    size: fx.size || (isBoss ? HUNT_SIZE_DEFAULT.boss : HUNT_SIZE_DEFAULT.mob),
    attack_style: style,
    attack_color: fx.attack_color || HUNT_ATTACK_STYLES[style].color,
    hit_colors: Array.isArray(fx.hit_colors) && fx.hit_colors.length ? fx.hit_colors : (sprite ? sprite.autoHit : ["#cccccc"]),
    glow_keys: Array.isArray(fx.glow_keys) ? fx.glow_keys : (sprite ? sprite.autoGlow : []),
    intro_text: fx.intro_text || "",
    attack_lines: Array.isArray(fx.attack_lines) ? fx.attack_lines.filter(Boolean) : [],
    sounds: fx.sounds || {},
  };
}

// Стиль удара приёма: явный combat_moves.*.fx.style, иначе по mode.
function huntMoveStyle(move) {
  const style = move && move.fx && move.fx.style;
  if (HUNT_MOVE_STYLES[style]) return style;
  const mode = move ? (move.mode || "multiplier") : "multiplier";
  if (mode === "percent_max_hp") return "shatter";
  if (mode === "percent_remaining_hp") return "rend";
  return "slash";
}

function huntSceneCfg(scene) {
  scene = scene || {};
  const key = HUNT_BACKDROPS[scene.backdrop] ? scene.backdrop : "forest";
  const b = HUNT_BACKDROPS[key];
  return {
    backdrop: key,
    ambient: HUNT_AMBIENTS[scene.ambient] ? scene.ambient : b.ambient,
    day_night: typeof scene.day_night === "boolean" ? scene.day_night : b.day_night,
    ground: scene.ground_color || b.ground,
    silhouette: scene.silhouette_color || b.silhouette,
    fog: scene.fog_color || b.fog,
  };
}

// ---------- звук-заглушка (синтез), когда у моба/приёма нет своего файла ----------
let _hsAc = null, _hsNoise = null;
function _hsAudio() {
  try {
    if (!_hsAc) {
      _hsAc = new (window.AudioContext || window.webkitAudioContext)();
      _hsNoise = _hsAc.createBuffer(1, _hsAc.sampleRate, _hsAc.sampleRate);
      const d = _hsNoise.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    }
    if (_hsAc.state === "suspended") _hsAc.resume();
    return _hsAc;
  } catch (e) { return null; }
}
function huntSynth(name, volume = 0.8) {
  if (volume <= 0) return;
  const ac = _hsAudio(); if (!ac) return;
  const master = ac.createGain(); master.gain.value = 0.3 * volume; master.connect(ac.destination);
  const t = ac.currentTime;
  const env = (g, t0, a, peak, d) => { g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(peak, t0 + a); g.gain.exponentialRampToValueAtTime(0.0001, t0 + a + d); };
  const nz = (t0, dur, type, f0, f1, peak) => {
    const s = ac.createBufferSource(); s.buffer = _hsNoise; const f = ac.createBiquadFilter(); f.type = type;
    f.frequency.setValueAtTime(f0, t0); f.frequency.exponentialRampToValueAtTime(f1, t0 + dur); f.Q.value = 1.2;
    const g = ac.createGain(); env(g, t0, 0.01, peak, dur); s.connect(f); f.connect(g); g.connect(master); s.start(t0); s.stop(t0 + dur + 0.05);
  };
  const tone = (t0, dur, type, f0, f1, peak) => {
    const o = ac.createOscillator(); o.type = type; o.frequency.setValueAtTime(f0, t0); o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t0 + dur);
    const g = ac.createGain(); env(g, t0, 0.005, peak, dur); o.connect(g); g.connect(master); o.start(t0); o.stop(t0 + dur + 0.05);
  };
  const S = {
    swing: () => nz(t, 0.16, "bandpass", 900, 3200, 0.5),
    swing_heavy: () => nz(t, 0.26, "bandpass", 500, 1800, 0.55),
    impact: () => { tone(t, 0.2, "sine", 150, 38, 0.8); nz(t, 0.07, "lowpass", 1800, 300, 0.6); },
    impact_heavy: () => { tone(t, 0.4, "sine", 110, 30, 1); nz(t, 0.2, "lowpass", 1800, 200, 0.7); },
    crack: () => { for (let i = 0; i < 4; i++) tone(t + i * 0.03, 0.06, "square", _hsRnd(1400, 2400), 600, 0.12); nz(t, 0.25, "highpass", 3000, 1500, 0.3); },
    claw: () => { for (let i = 0; i < 3; i++) nz(t + i * 0.07, 0.07, "highpass", 2600, 1200, 0.55); tone(t, 0.3, "sawtooth", 95, 60, 0.12); },
    bite: () => { nz(t, 0.08, "bandpass", 1200, 400, 0.7); tone(t + 0.02, 0.18, "square", 180, 70, 0.15); },
    slam: () => { tone(t, 0.6, "sine", 70, 24, 1); nz(t, 0.7, "lowpass", 500, 80, 0.8); },
    spell: () => { tone(t, 0.35, "triangle", 300, 900, 0.25); nz(t + 0.25, 0.4, "bandpass", 3000, 600, 0.4); },
    growl: () => { nz(t, 0.5, "lowpass", 260, 120, 0.25); tone(t, 0.5, "sawtooth", 62, 48, 0.07); },
    rumble: () => { nz(t, 1.4, "lowpass", 160, 60, 0.9); tone(t, 1.2, "sine", 44, 30, 0.6); },
    kill: () => [440, 554, 659, 880].forEach((f, i) => tone(t + i * 0.08, 0.18, "square", f, f, 0.09)),
    soul: () => [880, 1175, 1568].forEach((f, i) => tone(t + i * 0.12, 0.5, "sine", f, f * 1.01, 0.12)),
    level: () => [660, 990, 1320].forEach((f, i) => tone(t + i * 0.1, 0.35, "sine", f, f, 0.25)),
    qte_hit: () => { tone(t, 0.14, "triangle", 1320, 1320, 0.35); tone(t + 0.05, 0.16, "triangle", 1980, 1980, 0.25); },
    qte_miss: () => tone(t, 0.1, "square", 220, 180, 0.08),
    heart: () => { tone(t, 0.12, "sine", 70, 40, 0.7); tone(t + 0.18, 0.1, "sine", 60, 36, 0.5); },
    defeat: () => tone(t, 0.9, "sawtooth", 220, 70, 0.18),
  };
  if (S[name]) S[name]();
}

function huntFxInjectStyles() {
  if (document.getElementById("huntFxStyles")) return;
  const st = document.createElement("style"); st.id = "huntFxStyles";
  st.textContent = `
  .hs-root { position: relative; overflow: hidden; background: #0a0e12; user-select: none; }
  .hs-root canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; transition: filter .6s ease; }
  .hs-root.qte-on { cursor: crosshair; }
  .hs-fx { position: absolute; inset: 0; pointer-events: none; overflow: hidden; z-index: 2; }
  .hs-dmg { position: absolute; transform: translate(-50%, -50%); font-family: Consolas, "Courier New", monospace; font-weight: 700; font-size: 26px; color: #ff6a58;
    white-space: nowrap; text-shadow: 0 2px 0 #000, 0 0 10px rgba(0,0,0,.6); animation: hs-dmg-pop 1.1s cubic-bezier(.2,.8,.3,1) forwards; }
  .hs-dmg.big { font-size: 36px; }
  .hs-dmg.true { color: #c58cff; }
  .hs-dmg.kill { color: #ffd76a; font-size: 40px; }
  .hs-dmg.qte::before { content: "✦ "; font-size: .7em; vertical-align: .15em; }
  .hs-dmg.qte { text-shadow: 0 2px 0 #000, 0 0 14px rgba(255,255,255,.75); }
  .hs-dmg.player { font-size: 22px; color: #ff5040; animation-name: hs-dmg-drop; }
  .hs-dmg.absorb { font-size: 12px; color: #b9c6d2; font-weight: 400; animation-name: hs-dmg-drop; }
  @keyframes hs-dmg-pop { 0% { transform: translate(-50%,-50%) scale(1.9); opacity: 0; } 12% { transform: translate(-50%,-50%) scale(.92); opacity: 1; }
    22% { transform: translate(-50%,-58%) scale(1.05); } 70% { opacity: 1; } 100% { transform: translate(-50%,-150%) scale(1); opacity: 0; } }
  @keyframes hs-dmg-drop { 0% { transform: translate(-50%,-80%) scale(1.5); opacity: 0; } 15% { transform: translate(-50%,-50%) scale(1); opacity: 1; }
    75% { opacity: 1; } 100% { transform: translate(-50%,-10%); opacity: 0; } }
  .hs-callout { position: absolute; left: 50%; top: 38%; transform: translate(-50%, -50%); text-align: center; white-space: nowrap;
    font-family: Consolas, "Courier New", monospace; font-weight: 700; letter-spacing: .22em; font-size: 30px; color: #fff;
    text-shadow: 0 2px 0 #000, 0 0 18px rgba(0,0,0,.8); animation: hs-callout 1.3s ease-out forwards; }
  .hs-callout small { display: block; font-size: 12px; letter-spacing: .12em; font-weight: 400; color: #d6dde4; margin-top: 6px; }
  .hs-callout.qte { color: #fff3c8; font-size: 22px; top: 22%; }
  .hs-callout.exec { color: #ffd76a; }
  .hs-callout.oneshot { color: #9dffc8; }
  .hs-callout.boss { color: #ffb070; font-size: 40px; letter-spacing: .3em; animation-duration: 2.4s; }
  .hs-callout.intro { font-size: 22px; animation-duration: 2s; }
  @keyframes hs-callout { 0% { opacity: 0; transform: translate(-50%,-50%) scale(1.6); } 10% { opacity: 1; transform: translate(-50%,-50%) scale(.96); }
    18% { transform: translate(-50%,-50%) scale(1); } 75% { opacity: 1; } 100% { opacity: 0; transform: translate(-50%,-62%) scale(1); } }
  .hs-flash { position: absolute; inset: 0; transition: opacity .5s; }
  @media (max-width: 560px) { .hs-callout { font-size: 22px; } .hs-callout.boss { font-size: 26px; } }
  `;
  document.head.appendChild(st);
}

// Небо по часу суток: ключевые кадры (верх, низ, освещённость).
const _HS_SKY = [
  { h: 0, top: "#05080f", bot: "#101a30", amb: 0.28 }, { h: 4.5, top: "#070b16", bot: "#1a2440", amb: 0.3 },
  { h: 6, top: "#26304f", bot: "#c47a55", amb: 0.55 }, { h: 8, top: "#2f5a7a", bot: "#9ab8b8", amb: 0.85 },
  { h: 13, top: "#35698c", bot: "#a8c6c6", amb: 1 }, { h: 17, top: "#3a4f72", bot: "#d59a62", amb: 0.8 },
  { h: 19, top: "#2a2346", bot: "#c65e3a", amb: 0.55 }, { h: 21, top: "#0c1122", bot: "#2a2c4a", amb: 0.34 },
  { h: 24, top: "#05080f", bot: "#101a30", amb: 0.28 },
];

// Сцена. host - пустой элемент нужного размера; всё остальное (canvas, слой цифр)
// сцена строит сама. Хореография - промисы, которые ждут «мирового» времени (оно
// замирает на стоп-кадре удара и замедляется на добивании).
class HuntStage {
  constructor(host, opts = {}) {
    huntFxInjectStyles();
    this.host = host; host.classList.add("hs-root");
    this.canvas = document.createElement("canvas"); this.ctx = this.canvas.getContext("2d");
    this.fxLayer = document.createElement("div"); this.fxLayer.className = "hs-fx";
    host.append(this.canvas, this.fxLayer);
    this.reduceMotion = opts.reduceMotion ?? (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
    this.volume = opts.volume || (() => 0.8);
    this.soundBase = opts.soundBase || "/sounds/";
    this.P = 3; this.OV = 18;
    this.W = 0; this.H = 0; this.dpr = 1;
    this.scene = huntSceneCfg(null); this.sprite = null; this.mobFx = huntMobFx(null, false, null); this.isBoss = false;
    this.tint = document.createElement("canvas"); this.tctx = this.tint.getContext("2d");
    this.targetLabel = "ЦЕЛЬ"; this.targetMode = "hidden";
    this.running = false; this.last = 0;
    this.reset();
    this.hour = this.hourTarget = 12;
    this._onFrame = (now) => this._frame(now);
    this._ro = window.ResizeObserver ? new ResizeObserver(() => this.resize()) : null;
    if (this._ro) this._ro.observe(host);
    this._onPointer = () => { if (this.qte) this.resolveQte(); };
    host.addEventListener("pointerdown", this._onPointer);
    this.resize();
  }

  reset() {
    this.t = 0; this.timeScale = 1; this.freezeUntil = 0; this.shake = 0; this.hurt = 0; this.hurtCol = [224, 60, 48];
    this.darken = 0; this.fogBoost = 0; this.heartbeat = 0;
    this.mob = { alpha: 1, ox: 0, oy: 0, sc: 1, flash: 0, hidden: false, idle: true, tell: 0 };
    this.parts = []; this.slashes = []; this.claws = []; this.bites = []; this.orbs = []; this.cracks = []; this.waves = []; this.rings = [];
    this.timers = []; this.tweens = []; this.leaves = []; this.qte = null;
    this.canvas.style.filter = ""; this.fxLayer.innerHTML = "";
  }

  destroy() { this.stop(); if (this._ro) this._ro.disconnect(); this.host.removeEventListener("pointerdown", this._onPointer); }
  start() { if (this.running) return; this.running = true; this.last = performance.now(); requestAnimationFrame(this._onFrame); }
  stop() { this.running = false; }

  setScene(scene) { this.scene = huntSceneCfg(scene); this._buildLayers(); }
  setMob(icon, fx, isBoss) {
    this.sprite = icon && icon.grid && icon.grid.length ? huntSprite(icon) : null;
    this.isBoss = !!isBoss; this.mobFx = huntMobFx(fx, isBoss, this.sprite);
    if (this.sprite) { this.tint.width = this.sprite.w; this.tint.height = this.sprite.h; }
    this.targetLabel = isBoss ? "БОСС" : "ЦЕЛЬ";
  }
  // Абсолютный час (растёт без обрезки на 24) - так смена суток за бой идёт вперёд.
  setHour(hour, animate) { this.hourTarget = hour; if (!animate) this.hour = hour; }
  setTarget(mode, label) { this.targetMode = mode; if (label) this.targetLabel = label; }

  resize() {
    const r = this.host.getBoundingClientRect(); if (!r.width || !r.height) return;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.W = Math.round(r.width); this.H = Math.round(r.height);
    this.canvas.width = Math.round(this.W * this.dpr); this.canvas.height = Math.round(this.H * this.dpr);
    this.Lw = Math.ceil((this.W + this.OV * 2) / this.P); this.Lh = Math.ceil((this.H + this.OV * 2) / this.P);
    this.L = document.createElement("canvas"); this.L.width = this.Lw; this.L.height = this.Lh; this.lctx = this.L.getContext("2d");
    this._buildLayers();
    if (!this.motes) { this.motes = []; for (let i = 0; i < 28; i++) this.motes.push({ x: Math.random(), y: _hsRnd(0.1, 0.9), ph: _hsRnd(0, 6.28), sp: _hsRnd(0.5, 1.5) }); }
  }

  // ---------- фон ----------
  _groundL() { return Math.round((this.H * 0.74 + this.OV) / this.P); }
  feetY() { return this.H * 0.86; }
  _buildLayers() {
    this.layers = []; if (!this.L) return;
    const gy = this._groundL(), Lw = this.Lw, Lh = this.Lh, kind = this.scene.backdrop;
    const mk = () => { const c = document.createElement("canvas"); c.width = Lw + 20; c.height = Lh; return c; };
    const push = (c, par) => { const t = document.createElement("canvas"); t.width = c.width; t.height = c.height; this.layers.push({ mask: c, tmp: t, par }); };
    if (kind === "forest") {
      [{ seed: 7, base: gy - 26, hMin: 16, hMax: 34, wMul: 0.38, gap: [6, 12], par: 0.2 },
       { seed: 19, base: gy - 8, hMin: 30, hMax: 58, wMul: 0.42, gap: [10, 22], par: 0.45 },
       { seed: 31, base: gy + 2, hMin: 44, hMax: 80, wMul: 0.36, gap: [26, 60], par: 0.8, edgeOnly: true }].forEach(sp => {
        const c = mk(), x = c.getContext("2d"); x.fillStyle = "#fff"; const r = _hsSeeded(sp.seed); let px = -6;
        while (px < c.width + 6) {
          const h = Math.round(_hsLerp(sp.hMin, sp.hMax, r())), half = Math.round(h * sp.wMul / 2) + 1;
          if (!(sp.edgeOnly && Math.abs(px - (Lw / 2 + 10)) < Lw * 0.26)) {
            const top = sp.base - h;
            for (let y = top; y <= sp.base; y++) {
              const k = (y - top) / h, tier = Math.floor(k * 4), local = k * 4 - tier;
              const w = Math.max(0, Math.round(half * (0.25 + 0.75 * k) * (0.55 + 0.45 * local)));
              x.fillRect(px - w, y, w * 2 + 1, 1);
            }
            x.fillRect(px - 1, sp.base, 3, Lh);
          }
          px += Math.round(_hsLerp(sp.gap[0], sp.gap[1], r())) + half;
        }
        push(c, sp.par);
      });
    } else if (kind === "cave") {
      [{ seed: 5, base: gy - 18, top: 0, hMin: 8, hMax: 22, gap: [5, 11], par: 0.2, down: 0.5 },
       { seed: 23, base: gy - 2, top: 0, hMin: 14, hMax: 40, gap: [9, 20], par: 0.45, down: 0.8 },
       { seed: 41, base: Lh, top: 0, hMin: 30, hMax: 60, gap: [30, 70], par: 0.8, edgeOnly: true, down: 1 }].forEach(sp => {
        const c = mk(), x = c.getContext("2d"); x.fillStyle = "#fff"; const r = _hsSeeded(sp.seed); let px = -6;
        const spike = (cx, y0, h, halfW, dir) => { for (let k = 0; k <= h; k++) { const w = Math.max(0, Math.round(halfW * (1 - k / h))); x.fillRect(cx - w, y0 + dir * k, w * 2 + 1, 1); } };
        if (sp.edgeOnly) { // ближние колонны-стены по краям кадра
          [[0, Lw * 0.09], [Lw + 20 - Lw * 0.09, Lw + 20]].forEach(([a, b]) => { for (let y = 0; y < Lh; y++) { const j = Math.round(Math.sin(y * 0.21 + a) * 2); x.fillRect(Math.round(a + (a ? -j : 0)), y, Math.round(b - a + j), 1); } });
        } else {
          x.fillRect(0, 0, c.width, Math.round(sp.down * 6)); // свод
          while (px < c.width + 6) {
            const h = Math.round(_hsLerp(sp.hMin, sp.hMax, r())), half = Math.round(h * 0.22) + 1;
            spike(px, Math.round(sp.down * 6), Math.round(h * sp.down), half, 1);                     // сталактит
            if (r() < 0.7) spike(px + Math.round(half * 1.5), sp.base, Math.round(h * 0.8), half + 1, -1); // сталагмит
            px += Math.round(_hsLerp(sp.gap[0], sp.gap[1], r())) + half;
          }
          x.fillRect(0, sp.base, c.width, Lh);
        }
        push(c, sp.par);
      });
    }
    const r = _hsSeeded(101); this.grass = [];
    for (let i = 0; i < Lw * 0.7; i++) this.grass.push({ x: Math.floor(r() * Lw), y: gy + 1 + Math.floor(r() * (Lh - gy - 2)), h: 1 + Math.floor(r() * 3), ph: r() * 6.28, shade: r() });
  }
  _sky(hr) {
    const h = ((hr % 24) + 24) % 24; let i = 0; while (i < _HS_SKY.length - 2 && _HS_SKY[i + 1].h <= h) i++;
    const a = _HS_SKY[i], b = _HS_SKY[i + 1], t = (h - a.h) / (b.h - a.h);
    return { top: _hsMix(_hsHex(a.top), _hsHex(b.top), t), bot: _hsMix(_hsHex(a.bot), _hsHex(b.bot), t), amb: _hsLerp(a.amb, b.amb, t), h };
  }
  // Освещённость сцены: небо по времени или постоянный полумрак (пещера и т.п.).
  _light() {
    if (this.scene.day_night) return this._sky(this.hour);
    const fog = _hsHex(this.scene.fog), sil = _hsHex(this.scene.silhouette);
    return { top: _hsMix(sil, [0, 0, 0], 0.4), bot: _hsMix(fog, sil, 0.55), amb: 0.42, h: 0, fixed: true };
  }
  _renderL(sky, camX) {
    const x = this.lctx, gy = this._groundL(), Lw = this.Lw, Lh = this.Lh, sc = this.scene;
    for (let y = 0; y < gy + 1; y++) { x.fillStyle = _hsRgb(_hsMix(sky.top, sky.bot, Math.pow(y / gy, 1.6))); x.fillRect(0, y, Lw, 1); }
    const night = sky.fixed ? 0 : _hsClamp((0.6 - sky.amb) / 0.32, 0, 1);
    if (night > 0) {
      const r = _hsSeeded(3);
      for (let i = 0; i < 70; i++) { const sx = Math.floor(r() * Lw), sy = Math.floor(r() * gy * 0.6), tw = 0.5 + 0.5 * Math.sin(this.t * 0.002 + i); x.fillStyle = `rgba(220,230,255,${night * (0.25 + 0.6 * tw * r())})`; x.fillRect(sx, sy, 1, 1); }
    }
    if (!sky.fixed) { // солнце днём, луна ночью - по дуге
      const h = sky.h, isD = h >= 5 && h < 20.5, prog = isD ? (h - 5) / 15.5 : ((h + 24 - 20.5) % 24) / 8.5;
      const bx = _hsLerp(Lw * 0.12, Lw * 0.88, prog), by = gy * 0.62 - Math.sin(prog * Math.PI) * gy * 0.5, R = isD ? 6 : 5;
      x.fillStyle = isD ? "rgba(255,220,150,.18)" : "rgba(190,210,255,.12)";
      for (let k = 3; k >= 1; k--) { x.beginPath(); x.arc(bx, by, R + k * 3, 0, 6.29); x.fill(); }
      x.fillStyle = isD ? "#ffe2a0" : "#dfe6f5";
      for (let yy = -R; yy <= R; yy++) { const w = Math.round(Math.sqrt(R * R - yy * yy)); x.fillRect(Math.round(bx - w), Math.round(by + yy), w * 2, 1); }
      if (!isD) { x.fillStyle = _hsRgb(_hsMix(sky.top, sky.bot, 0.3)); for (let yy = -R; yy <= R; yy++) { const w = Math.round(Math.sqrt(R * R - yy * yy)); x.fillRect(Math.round(bx - w + 3), Math.round(by + yy - 1), w * 2, 1); } }
    }
    const sil = _hsHex(sc.silhouette), fogC = _hsMix(_hsHex(sc.fog), sky.bot, 0.35);
    const cols = [_hsMix(sky.bot, sil, 0.62), _hsMix(sky.bot, sil, 0.84), _hsMix(sil, [0, 0, 0], 0.45)];
    const ground = () => {
      const g0 = _hsMix(_hsHex(sc.ground), sky.bot, 0.12), g1 = _hsMix(_hsHex(sc.ground), [0, 0, 0], 0.6);
      for (let y = gy; y < Lh; y++) { x.fillStyle = _hsRgb(_hsMix(g0, g1, (y - gy) / (Lh - gy))); x.fillRect(0, y, Lw, 1); }
      const gc = _hsHex(sc.ground), hi = _hsMix(gc, [255, 255, 255], 0.14), lo = _hsMix(gc, [0, 0, 0], 0.25);
      this.grass.forEach(g => {
        x.fillStyle = _hsRgb(_hsMix(hi, lo, g.shade));
        if (sc.backdrop === "forest") { const sway = Math.round(Math.sin(this.t * 0.0016 + g.ph) * 0.8); x.fillRect(g.x + sway, g.y - g.h, 1, g.h); }
        else if (g.shade > 0.55) x.fillRect(g.x, g.y, g.h > 2 ? 2 : 1, 1); // камешки
      });
    };
    const fogBand = (i, fy) => {
      const a = (0.1 + 0.08 * night + this.fogBoost * 0.35) * (i === 0 ? 1 : 0.8);
      for (let c = 0; c < Lw; c++) { const off = Math.sin((c + this.t * 0.004 * (i + 1)) * 0.05) * 3 + Math.sin((c - this.t * 0.002) * 0.13) * 2; x.fillStyle = _hsRgb(fogC, a); x.fillRect(c, Math.round(fy + off), 1, 7 + i * 3); }
    };
    if (!this.layers.length) { fogBand(0, gy - 14); ground(); fogBand(1, gy - 4); }
    this.layers.forEach((ly, i) => {
      const t = ly.tmp.getContext("2d"); t.globalCompositeOperation = "source-over"; t.clearRect(0, 0, ly.tmp.width, ly.tmp.height);
      t.fillStyle = _hsRgb(cols[i]); t.fillRect(0, 0, ly.tmp.width, ly.tmp.height); t.globalCompositeOperation = "destination-in"; t.drawImage(ly.mask, 0, 0);
      x.drawImage(ly.tmp, Math.round(-10 + camX * ly.par), 0);
      if (i < 2) fogBand(i, i === 0 ? gy - 20 : gy - 6);
      if (i === 1) ground();
    });
    if (sky.amb < 1) { x.fillStyle = `rgba(8,12,30,${(1 - sky.amb) * (sky.fixed ? 0.3 : 0.42)})`; x.fillRect(0, 0, Lw, Lh); }
    if (!sky.fixed && !night && sky.amb > 0.6 && sc.backdrop === "forest") { // лучи сквозь кроны
      x.globalCompositeOperation = "lighter";
      for (let k = 0; k < 4; k++) { const rx = Lw * (0.2 + k * 0.2) + Math.sin(this.t * 0.0004 + k) * 6; x.fillStyle = `rgba(255,230,170,${0.035 * (sky.amb - 0.6) * 2.5})`; x.beginPath(); x.moveTo(rx, 0); x.lineTo(rx + 10, 0); x.lineTo(rx - 14, gy + 10); x.lineTo(rx - 30, gy + 10); x.fill(); }
      x.globalCompositeOperation = "source-over";
    }
    if (sc.backdrop === "forest") { // ветви по краям - рамка кадра
      x.fillStyle = _hsRgb(_hsMix(sil, [0, 0, 0], 0.7));
      for (let y = 0; y < Lh; y++) { const w1 = 5 + Math.round(Math.sin(y * 0.3) * 1.5) + (y > Lh * 0.8 ? (y - Lh * 0.8) * 0.9 : 0); x.fillRect(0, y, w1, 1); x.fillRect(Lw - w1 + 1, y, w1, 1); }
    }
  }

  // ---------- моб ----------
  mobGeom() {
    const sp = this.sprite; if (!sp) return null;
    const bh = sp.by1 - sp.by0 + 1, bw = sp.bx1 - sp.bx0 + 1;
    const base = Math.max(1, Math.round(this.H * this.mobFx.size / 100 / bh));
    const s = base * this.mob.sc, cx = this.W / 2 + this.mob.ox, fy = this.feetY() + this.mob.oy;
    const dx = cx - ((sp.bx0 + sp.bx1 + 1) / 2) * s, dy = fy - (sp.by1 + 1) * s;
    return { sp, s, base, dx, dy, x0: dx + sp.bx0 * s, y0: dy + sp.by0 * s, x1: dx + (sp.bx1 + 1) * s, y1: dy + (sp.by1 + 1) * s, cx, cy: dy + ((sp.by0 + sp.by1 + 1) / 2) * s, fy, bw, bh };
  }
  _drawMob(sky) {
    const m = this.mob, g = this.mobGeom(); if (!g || m.hidden || m.alpha <= 0) return;
    const ctx = this.ctx, sp = g.sp, tc = this.tctx, breath = m.idle ? Math.sin(this.t * 0.0032) * 0.012 : 0;
    ctx.save(); ctx.globalAlpha = 0.5 * m.alpha; ctx.fillStyle = "#000";
    ctx.beginPath(); ctx.ellipse(g.cx, g.fy - 2, (g.x1 - g.x0) * 0.46, 7 + g.s, 0, 0, 6.29); ctx.fill(); ctx.restore();
    tc.globalCompositeOperation = "source-over"; tc.clearRect(0, 0, sp.w, sp.h); tc.drawImage(sp.canvas, 0, 0);
    tc.globalCompositeOperation = "source-atop";
    const nightA = _hsClamp((1 - sky.amb) * 0.55, 0, 0.5); if (nightA > 0) { tc.fillStyle = `rgba(8,14,36,${nightA})`; tc.fillRect(0, 0, sp.w, sp.h); }
    if (m.flash > 0) { tc.fillStyle = `rgba(255,255,255,${m.flash})`; tc.fillRect(0, 0, sp.w, sp.h); }
    tc.globalCompositeOperation = "source-over";
    const hS = g.s * (1 + breath), vS = g.s * (1 - breath);
    const dx = Math.round(g.cx - ((sp.bx0 + sp.bx1 + 1) / 2) * hS), dy = Math.round(g.fy - (sp.by1 + 1) * vS);
    ctx.save(); ctx.globalAlpha = m.alpha; ctx.imageSmoothingEnabled = false; ctx.drawImage(this.tint, 0, 0, sp.w, sp.h, dx, dy, sp.w * hS, sp.h * vS); ctx.restore();
    const glowA = _hsClamp((0.75 - sky.amb) / 0.4, 0, 1) * m.alpha + m.tell * 0.8, keys = this.mobFx.glow_keys;
    if (glowA > 0.02 && keys.length) {
      ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = _hsClamp(glowA, 0, 1);
      for (let y = 0; y < sp.h; y++) for (let i = 0; i < sp.w; i++) {
        const ch = sp.grid[y][i]; if (!keys.includes(ch)) continue; const col = sp.pal[ch]; if (!col) continue;
        ctx.shadowColor = col; ctx.shadowBlur = 14; ctx.fillStyle = col; ctx.fillRect(dx + i * hS, dy + y * vS, hS, vS);
      }
      ctx.restore();
    }
    this._drawTarget(g);
  }
  _drawTarget(g) {
    if (this.targetMode === "hidden" || this.qte) return;
    const ctx = this.ctx, pulse = 0.5 + 0.5 * Math.sin(this.t * 0.004);
    let col = [95, 208, 232], pad = 14 + pulse * 3, a = 0.55 + pulse * 0.35, label = this.targetLabel;
    if (this.targetMode === "hover") { col = [240, 160, 48]; pad = 8; a = 0.95; }
    if (this.targetMode === "exec") { col = [255, 215, 106]; pad = 6 + pulse * 5; a = 0.7 + pulse * 0.3; }
    const x0 = g.x0 - pad, y0 = g.y0 - pad, x1 = g.x1 + pad, y1 = g.y1 + pad * 0.4, len = 16;
    ctx.save(); ctx.strokeStyle = _hsRgb(col, a * this.mob.alpha); ctx.lineWidth = 2; ctx.shadowColor = _hsRgb(col, 0.7); ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(x0, y0 + len); ctx.lineTo(x0, y0); ctx.lineTo(x0 + len, y0);
    ctx.moveTo(x1 - len, y0); ctx.lineTo(x1, y0); ctx.lineTo(x1, y0 + len);
    ctx.moveTo(x0, y1 - len); ctx.lineTo(x0, y1); ctx.lineTo(x0 + len, y1);
    ctx.moveTo(x1 - len, y1); ctx.lineTo(x1, y1); ctx.lineTo(x1, y1 - len);
    ctx.stroke(); ctx.shadowBlur = 0;
    ctx.font = '10px Consolas, "Courier New", monospace'; ctx.fillStyle = _hsRgb(col, a * this.mob.alpha); ctx.fillText(label, x0 + 2, y0 - 6);
    ctx.restore();
  }

  // ---------- эффекты ----------
  _drawAmbient(sky) {
    const ctx = this.ctx, kind = this.scene.ambient, W = this.W, H = this.H;
    const night = sky.fixed ? 1 : _hsClamp((0.6 - sky.amb) / 0.32, 0, 1);
    if (kind === "none") return;
    this.motes.forEach(p => {
      const bl = 0.5 + 0.5 * Math.sin(this.t * 0.003 + p.ph * 3);
      let x = (p.x + Math.sin(this.t * 0.0003 + p.ph) * 0.02) * W, y = (p.y + Math.sin(this.t * 0.0007 + p.ph * 2) * 0.03) * H;
      if (kind === "embers") { y = H - ((this.t * 0.03 * p.sp + p.y * H) % (H * 1.1)); x += Math.sin(this.t * 0.002 + p.ph) * 12; }
      if (kind === "snow") { y = ((this.t * 0.025 * p.sp + p.y * H) % (H * 1.05)) - 6; x += Math.sin(this.t * 0.0015 + p.ph) * 16; }
      x = Math.round(x); y = Math.round(y);
      if (kind === "fireflies") {
        if (night > 0.1) { ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.fillStyle = `rgba(210,255,140,${night * bl * 0.9})`; ctx.shadowColor = "#cfff7a"; ctx.shadowBlur = 8; ctx.fillRect(x, y, 2, 2); ctx.restore(); }
        else { ctx.fillStyle = `rgba(255,245,220,${0.18 * bl})`; ctx.fillRect(x, y, 2, 2); }
      } else if (kind === "embers") { ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.fillStyle = `rgba(255,${140 + 60 * bl | 0},60,${0.5 + 0.4 * bl})`; ctx.shadowColor = "#ff8a3a"; ctx.shadowBlur = 8; ctx.fillRect(x, y, 2, 2); ctx.restore(); }
      else if (kind === "snow") { ctx.fillStyle = `rgba(235,242,255,${0.55 + 0.3 * bl})`; ctx.fillRect(x, y, p.sp > 1 ? 3 : 2, p.sp > 1 ? 3 : 2); }
      else if (kind === "dust") { ctx.fillStyle = `rgba(230,215,190,${0.12 + 0.12 * bl})`; ctx.fillRect(x, y, 2, 2); }
    });
    if (this.scene.backdrop === "forest") this.leaves.forEach(l => { ctx.fillStyle = l.col; ctx.fillRect(Math.round(l.x), Math.round(l.y), 3, 2); });
  }
  _drawFx() {
    const ctx = this.ctx;
    this.cracks.forEach(c => {
      const a = 1 - c.t / c.life; if (a <= 0) return; ctx.save(); ctx.globalAlpha = a;
      ctx.strokeStyle = c.col; ctx.lineWidth = c.w; ctx.shadowColor = c.glow || "transparent"; ctx.shadowBlur = c.glow ? 10 : 0;
      c.paths.forEach(path => { const n = Math.max(2, Math.ceil(path.length * Math.min(1, c.t / 90))); ctx.beginPath(); path.slice(0, n).forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke(); });
      ctx.restore();
    });
    this.waves.forEach(w => {
      const k = w.t / w.life; if (k >= 1) return; const rx = _hsLerp(w.r0, w.r1, 1 - Math.pow(1 - k, 2));
      ctx.save(); ctx.globalAlpha = (1 - k) * 0.8; ctx.strokeStyle = w.col; ctx.lineWidth = 3 + (1 - k) * 5; ctx.beginPath(); ctx.ellipse(w.x, w.y, rx, rx * w.flat, 0, 0, 6.29); ctx.stroke(); ctx.restore();
    });
    ctx.save();
    this.parts.forEach(p => {
      const a = _hsClamp(1 - p.t / p.life, 0, 1); if (a <= 0) return;
      ctx.globalAlpha = a * (p.alpha || 1); ctx.fillStyle = p.col;
      if (p.glow) { ctx.globalCompositeOperation = "lighter"; ctx.shadowColor = p.col; ctx.shadowBlur = p.glow; } else { ctx.globalCompositeOperation = "source-over"; ctx.shadowBlur = 0; }
      const s = p.size * (p.shrink ? a : 1);
      ctx.fillRect(Math.round(p.x - s / 2), Math.round(p.y - s / 2), Math.max(1, Math.round(s)), Math.max(1, Math.round(p.stretch ? s * 2.4 : s)));
    });
    ctx.restore();
    this.slashes.forEach(sl => this._drawSlash(sl));
    this.orbs.forEach(o => {
      const k = _hsClamp(o.t / o.dur, 0, 1); if (k >= 1) return; const e = k * k;
      const x = _hsLerp(o.x0, this.W / 2, e), y = _hsLerp(o.y0, this.H * 0.55, e), r = _hsLerp(6, Math.min(this.W, this.H) * 0.35, e);
      ctx.save(); ctx.globalCompositeOperation = "lighter"; const gr = ctx.createRadialGradient(x, y, 0, x, y, r);
      gr.addColorStop(0, "rgba(255,255,255,.95)"); gr.addColorStop(0.35, _hsRgb(_hsHex(o.col), 0.9)); gr.addColorStop(1, _hsRgb(_hsHex(o.col), 0));
      ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.29); ctx.fill(); ctx.restore();
    });
    if (this.qte) this._drawQte(this.qte);
    this.rings.forEach(r => { const k = r.t / r.life; if (k >= 1) return; ctx.save(); ctx.globalAlpha = 1 - k; ctx.strokeStyle = r.col; ctx.lineWidth = 3; ctx.shadowColor = r.col; ctx.shadowBlur = 12; ctx.beginPath(); ctx.arc(r.x, r.y, _hsLerp(r.r0, r.r1, k), 0, 6.29); ctx.stroke(); ctx.restore(); });
  }
  _drawSlash(sl) {
    const ctx = this.ctx, p = _hsClamp(sl.t / sl.dur, 0, 1), fade = sl.t > sl.dur ? _hsClamp(1 - (sl.t - sl.dur) / (sl.life - sl.dur), 0, 1) : 1;
    if (fade <= 0) return;
    ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.lineCap = "round"; ctx.shadowColor = sl.glow; ctx.shadowBlur = 16;
    if (sl.kind === "arc") {
      const end = sl.a0 + (sl.a1 - sl.a0) * p, N = 14;
      for (let i = 0; i < N; i++) {
        const k0 = i / N, k1 = (i + 1) / N, aa = _hsLerp(sl.a0, end, k0), ab = _hsLerp(sl.a0, end, k1);
        ctx.strokeStyle = sl.col; ctx.globalAlpha = fade * (0.35 + 0.65 * k1); ctx.lineWidth = Math.sin(k1 * Math.PI) * sl.w * fade + 1;
        ctx.beginPath(); ctx.arc(sl.x, sl.y, sl.r, aa, ab, sl.a1 < sl.a0); ctx.stroke();
      }
    } else if (sl.kind === "line") {
      const x = _hsLerp(sl.x0, sl.x1, p), y = _hsLerp(sl.y0, sl.y1, p), q = Math.max(0, p - 0.45);
      ctx.strokeStyle = sl.col; ctx.globalAlpha = fade; ctx.lineWidth = sl.w * fade + 1; ctx.beginPath(); ctx.moveTo(_hsLerp(sl.x0, sl.x1, q), _hsLerp(sl.y0, sl.y1, q)); ctx.lineTo(x, y); ctx.stroke();
    } else if (sl.kind === "star") {
      const r = _hsLerp(8, sl.r, p); ctx.strokeStyle = sl.col; ctx.globalAlpha = fade; ctx.lineWidth = 3;
      for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4 + 0.3; ctx.beginPath(); ctx.moveTo(sl.x + Math.cos(a) * r * 0.45, sl.y + Math.sin(a) * r * 0.45); ctx.lineTo(sl.x + Math.cos(a) * r, sl.y + Math.sin(a) * r); ctx.stroke(); }
    } else if (sl.kind === "reticle") {
      const r = _hsLerp(90, 14, Math.pow(p, 0.6)); ctx.strokeStyle = sl.col; ctx.globalAlpha = fade; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(sl.x, sl.y, r, 0, 6.29); ctx.stroke();
      [[1, 0], [-1, 0], [0, 1], [0, -1]].forEach(([u, v]) => { ctx.beginPath(); ctx.moveTo(sl.x + u * (r + 4), sl.y + v * (r + 4)); ctx.lineTo(sl.x + u * (r + 18), sl.y + v * (r + 18)); ctx.stroke(); });
    }
    ctx.restore();
  }
  _drawQte(q) {
    const ctx = this.ctx, k = _hsClamp((this.t - q.t0) / q.dur, 0, 1), r = _hsLerp(q.R * 2.3, q.R * 0.15, k); q.r = r;
    ctx.save();
    ctx.setLineDash([6, 6]); ctx.lineDashOffset = -this.t * 0.02; ctx.strokeStyle = "rgba(95,208,232,.9)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(q.x, q.y, q.R, 0, 6.29); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(95,208,232,.18)"; ctx.lineWidth = q.R * q.tol * 2; ctx.beginPath(); ctx.arc(q.x, q.y, q.R, 0, 6.29); ctx.stroke();
    const inZone = Math.abs(r - q.R) <= q.R * q.tol;
    ctx.strokeStyle = inZone ? "#fff3c8" : "#f0a030"; ctx.shadowColor = inZone ? "#fff" : "#f0a030"; ctx.shadowBlur = inZone ? 18 : 10; ctx.lineWidth = inZone ? 4 : 3;
    ctx.beginPath(); ctx.arc(q.x, q.y, r, 0, 6.29); ctx.stroke();
    ctx.restore();
  }
  _drawOverlay(sky) {
    const ctx = this.ctx, W = this.W, H = this.H;
    const vg = ctx.createRadialGradient(W / 2, H * 0.55, Math.min(W, H) * 0.35, W / 2, H * 0.55, Math.max(W, H) * 0.75);
    vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, `rgba(0,0,0,${0.55 + (1 - sky.amb) * 0.2})`); ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);
    const hurt = Math.max(this.hurt, this.heartbeat * 0.55);
    if (hurt > 0.01) {
      const rg = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.25, W / 2, H / 2, Math.max(W, H) * 0.7);
      rg.addColorStop(0, "rgba(0,0,0,0)"); rg.addColorStop(1, _hsRgb(this.hurtCol, hurt * 0.75)); ctx.fillStyle = rg; ctx.fillRect(0, 0, W, H);
    }
    const ac = _hsHex(this.mobFx.attack_color);
    this.claws.forEach(c => {
      const a = c.t < 0 ? 0 : _hsClamp(1 - (c.t - 120) / 700, 0, 1); if (a <= 0) return; const p = _hsClamp(c.t / 110, 0, 1);
      ctx.save(); ctx.globalAlpha = a; const ex = _hsLerp(c.x0, c.x1, p), ey = _hsLerp(c.y0, c.y1, p);
      const nx = -(c.y1 - c.y0), ny = c.x1 - c.x0, nl = Math.hypot(nx, ny), ux = nx / nl, uy = ny / nl, mx = (c.x0 + ex) / 2, my = (c.y0 + ey) / 2;
      ctx.fillStyle = _hsRgb(_hsMix(ac, [0, 0, 0], 0.8), 0.65); ctx.beginPath(); ctx.moveTo(c.x0, c.y0); ctx.lineTo(mx + ux * 9, my + uy * 9); ctx.lineTo(ex, ey); ctx.lineTo(mx - ux * 9, my - uy * 9); ctx.fill();
      ctx.fillStyle = _hsRgb(ac); ctx.shadowColor = _hsRgb(ac); ctx.shadowBlur = 12; ctx.beginPath(); ctx.moveTo(c.x0, c.y0); ctx.lineTo(mx + ux * 4, my + uy * 4); ctx.lineTo(ex, ey); ctx.lineTo(mx - ux * 4, my - uy * 4); ctx.fill();
      ctx.restore();
    });
    this.bites.forEach(b => {
      const close = _hsClamp(b.t / 160, 0, 1), fade = b.t > 260 ? _hsClamp(1 - (b.t - 260) / 420, 0, 1) : 1; if (fade <= 0) return;
      const gap = _hsLerp(H * 0.55, 4, close * close), teeth = 9, tw = W / teeth;
      ctx.save(); ctx.globalAlpha = fade;
      [[-1, H / 2 - gap], [1, H / 2 + gap]].forEach(([dir, edge]) => {
        ctx.fillStyle = _hsRgb(_hsMix(ac, [0, 0, 0], 0.82), 0.92); ctx.fillRect(0, dir < 0 ? edge - H : edge, W, H);
        ctx.fillStyle = "#efe8d8"; ctx.shadowColor = _hsRgb(ac); ctx.shadowBlur = 10;
        for (let i = 0; i < teeth; i++) { const x0 = i * tw + (dir > 0 ? tw / 2 : 0); ctx.beginPath(); ctx.moveTo(x0 + tw * 0.15, edge); ctx.lineTo(x0 + tw * 0.5, edge - dir * tw * 0.55); ctx.lineTo(x0 + tw * 0.85, edge); ctx.fill(); }
      });
      ctx.restore();
    });
    if (this.darken > 0.01) { ctx.fillStyle = `rgba(0,0,0,${this.darken})`; ctx.fillRect(0, 0, W, H); }
  }

  // ---------- цикл ----------
  _update(dt) {
    this.t += dt;
    this.shake = this.shake > 0.3 ? this.shake * Math.pow(0.9, dt / 16) : 0;
    this.hurt = Math.max(0, this.hurt - dt / 600); this.heartbeat = Math.max(0, this.heartbeat - dt / 500);
    this.mob.flash = Math.max(0, this.mob.flash - dt / 140); this.mob.tell = Math.max(0, this.mob.tell - dt / 700);
    this.hour = Math.abs(this.hourTarget - this.hour) > 0.01 ? _hsLerp(this.hour, this.hourTarget, 1 - Math.pow(0.992, dt)) : this.hourTarget;
    this.tweens = this.tweens.filter(tw => {
      tw.t += dt; const k = _hsClamp(tw.t / tw.dur, 0, 1), e = tw.ease ? tw.ease(k) : 1 - Math.pow(1 - k, 3);
      tw.obj[tw.key] = _hsLerp(tw.from, tw.to, e); if (k >= 1) { tw.done && tw.done(); return false; } return true;
    });
    this.timers = this.timers.filter(tm => { tm.left -= dt; if (tm.left <= 0) { tm.fn(); return false; } return true; });
    const step = dt / 16;
    this.parts = this.parts.filter(p => {
      p.t += dt; const d = Math.pow(p.drag || 0.985, step); p.vx *= d; p.vy = p.vy * d + (p.g || 0) * step; p.x += p.vx * step; p.y += p.vy * step;
      if (p.floor && p.y > p.floor) { p.y = p.floor; p.vy *= -0.3; p.vx *= 0.6; } return p.t < p.life;
    });
    ["slashes", "cracks", "waves", "rings"].forEach(k => { this[k] = this[k].filter(s => (s.t += dt) < s.life); });
    this.claws = this.claws.filter(c => (c.t += dt) < 900); this.bites = this.bites.filter(b => (b.t += dt) < 700);
    this.orbs = this.orbs.filter(o => (o.t += dt) < o.dur);
    if (this.scene.backdrop === "forest" && Math.random() < dt * 0.0006) this.leaves.push({ x: _hsRnd(0, this.W), y: -6, vx: _hsRnd(-0.2, 0.2), ph: _hsRnd(0, 6), col: _hsPick(["#6b5a2a", "#8a6a2a", "#4a6a3a", "#7a3a24"]) });
    this.leaves = this.leaves.filter(l => { l.y += dt * 0.03; l.x += Math.sin(this.t * 0.002 + l.ph) * 0.4 + l.vx; return l.y < this.H * 0.9; });
    if (this.qte && this.t - this.qte.t0 > this.qte.dur) this.resolveQte(true);
  }
  _render() {
    if (!this.L) return;
    const ctx = this.ctx, sky = this._light(), sh = this.shake, sx = sh ? _hsRnd(-sh, sh) : 0, sy = sh ? _hsRnd(-sh, sh) * 0.6 : 0;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); ctx.imageSmoothingEnabled = false;
    this._renderL(sky, sx * 0.4);
    ctx.drawImage(this.L, 0, 0, this.Lw, this.Lh, -this.OV + sx, -this.OV + sy, this.Lw * this.P, this.Lh * this.P);
    ctx.save(); ctx.translate(sx, sy); this._drawMob(sky); this._drawAmbient(sky); this._drawFx(); ctx.restore();
    this._drawOverlay(sky);
  }
  _frame(now) {
    if (!this.running) return;
    const real = Math.min(50, now - this.last); this.last = now;
    let dt = real * this.timeScale; if (now < this.freezeUntil) dt = 0;
    this._update(dt); this._render(); requestAnimationFrame(this._onFrame);
  }
  wait(ms) { return new Promise(res => this.timers.push({ left: ms, fn: res })); }
  tween(obj, key, to, dur, ease) {
    return new Promise(res => { this.tweens = this.tweens.filter(t => !(t.obj === obj && t.key === key)); this.tweens.push({ obj, key, from: obj[key], to, dur, ease, t: 0, done: res }); });
  }
  shakeBy(a) { if (!this.reduceMotion) this.shake = Math.max(this.shake, a); }
  hitStop(ms) { if (!this.reduceMotion) this.freezeUntil = performance.now() + ms; }
  pulse() { this.heartbeat = 1; this.sfx("heart"); }

  // ---------- звук ----------
  sfx(name) { huntSynth(name, this.volume()); }
  playFile(file) { const v = this.volume(); if (!file || v <= 0) return; const a = new Audio(this.soundBase + file); a.volume = 0.6 * v; a.play().catch(() => {}); }
  // Звук моба: свой файл (combat_fx.sounds.<kind>) или синтез-заглушка.
  mobSfx(kind, fallback) { const f = this.mobFx.sounds[kind]; if (f) this.playFile(f); else if (fallback) this.sfx(fallback); }

  // ---------- DOM поверх сцены ----------
  damageNumber(text, cls, x, y) {
    const d = document.createElement("div"); d.className = "hs-dmg " + (cls || ""); d.textContent = text;
    d.style.left = (x / this.W * 100) + "%"; d.style.top = (y / this.H * 100) + "%"; this.fxLayer.appendChild(d); setTimeout(() => d.remove(), 1300);
  }
  callout(html, cls, ms = 1400) { const d = document.createElement("div"); d.className = "hs-callout " + (cls || ""); d.innerHTML = html; this.fxLayer.appendChild(d); setTimeout(() => d.remove(), ms); }
  flash(color, ms = 500) {
    if (this.reduceMotion) return; const f = document.createElement("div"); f.className = "hs-flash"; f.style.background = color;
    this.fxLayer.appendChild(f); requestAnimationFrame(() => requestAnimationFrame(() => { f.style.opacity = 0; })); setTimeout(() => f.remove(), ms + 100);
  }
  burst(x, y, n, cols, o = {}) {
    for (let i = 0; i < n; i++) {
      const a = (o.dir != null ? o.dir : _hsRnd(0, 6.28)) + _hsRnd(-(o.spread ?? 3.14), o.spread ?? 3.14), sp = _hsRnd(o.min ?? 1.5, o.max ?? 5);
      this.parts.push({ x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - (o.up || 0), g: o.g ?? 0.18, life: _hsRnd(o.lmin ?? 400, o.lmax ?? 900), t: 0,
        size: _hsRnd(o.smin ?? 2, o.smax ?? 5), col: _hsPick(cols), glow: o.glow, shrink: o.shrink, drag: o.drag, floor: o.floor, stretch: o.stretch });
    }
  }
  _crack(x, y, n, len, col, glow, life, dir) {
    const paths = [];
    for (let i = 0; i < n; i++) {
      let a = dir != null ? dir + _hsRnd(-0.5, 0.5) : _hsRnd(0, 6.28), px = x, py = y; const path = [[px, py]];
      for (let k = 0; k < len; k++) { a += _hsRnd(-0.6, 0.6); const st = _hsRnd(8, 16); px += Math.cos(a) * st; py += Math.sin(a) * st * (dir != null ? 0.25 : 1); path.push([px, py]); }
      paths.push(path);
    }
    this.cracks.push({ paths, col, glow, w: 2, t: 0, life });
  }

  // ---------- хореография ----------
  // Появление цели. title/subtitle - надпись по центру (пусто - без надписи).
  async intro({ title, subtitle } = {}) {
    Object.assign(this.mob, { alpha: 0, ox: 0, oy: 0, sc: 0.82, hidden: false, idle: true });
    this.targetMode = "hidden";
    if (this.isBoss) {
      this.fogBoost = 1; this.darken = 0.55; this.mobSfx("intro", "rumble"); this.shakeBy(6);
      const rum = setInterval(() => this.shakeBy(4), 120); this.tween(this, "darken", 0.1, 1400);
      if (title) this.callout(`${title}${subtitle ? `<small>${subtitle}</small>` : ""}`, "boss", 2400);
      this.tween(this.mob, "sc", 1, 1600); await this.tween(this.mob, "alpha", 1, 1600); clearInterval(rum);
      this.tween(this, "darken", 0, 600); this.tween(this, "fogBoost", 0, 1400);
    } else {
      this.fogBoost = 0.8; if (title) this.callout(`${title}${subtitle ? `<small>${subtitle}</small>` : ""}`, "intro", 1900);
      this.tween(this, "fogBoost", 0, 1300); this.tween(this.mob, "sc", 1, 900); await this.tween(this.mob, "alpha", 1, 900);
      this.mob.tell = 1; this.mobSfx("intro", "growl");
    }
    this.targetMode = "idle";
  }
  // Показать цель сразу, без появления (модалку открыли посреди боя).
  showMob() { Object.assign(this.mob, { alpha: 1, ox: 0, oy: 0, sc: 1, hidden: false, idle: true }); this.fogBoost = 0; this.darken = 0; }

  // Удар игрока до момента попадания. style - HUNT_MOVE_STYLES или «oneshot»/«fists».
  // Возвращает точку попадания - туда же хост ставит цифру урона.
  async strike({ style = "slash", color, qte = false, sound } = {}) {
    const g = this.mobGeom(); if (!g) return { hx: this.W / 2, hy: this.H / 2 };
    const hx = g.cx + _hsRnd(-12, 12), hy = _hsLerp(g.y0, g.y1, 0.42) + _hsRnd(-10, 10), R = Math.max(g.x1 - g.x0, g.y1 - g.y0) * 0.55;
    const main = color || (HUNT_MOVE_STYLES[style] && HUNT_MOVE_STYLES[style].color) || HUNT_DEFAULT_SLASH;
    const swing = (heavy) => { if (sound) this.playFile(sound); else this.sfx(heavy ? "swing_heavy" : "swing"); };
    this.targetMode = "hidden";
    if (style === "thrust") {
      this.slashes.push({ kind: "reticle", x: hx, y: hy, col: "#f0a030", glow: "#f0a030", t: 0, dur: 220, life: 360 });
      swing(false); await this.wait(200);
      this.slashes.push({ kind: "line", x0: this.W * 0.55, y0: this.H + 20, x1: hx, y1: hy, w: 5, col: main, glow: main, t: 0, dur: 70, life: 300 });
      await this.wait(60);
    } else if (style === "heavy") {
      this.tween(this, "darken", 0.35, 160); swing(true); await this.wait(170);
      this.slashes.push({ kind: "arc", x: hx - R * 0.2, y: hy - R * 0.9, r: R * 1.3, a0: -0.2, a1: 2.1, w: 16, col: main, glow: main, t: 0, dur: 110, life: 420 });
      await this.wait(90); this.tween(this, "darken", 0, 300);
    } else if (style === "fists") {
      swing(false); await this.wait(90);
      this.slashes.push({ kind: "star", x: hx, y: hy, r: 42, col: "#fff1d6", glow: "#ffb070", t: 0, dur: 90, life: 260 });
    } else if (style === "execute") {
      if (!this.reduceMotion) { this.timeScale = 0.3; this.canvas.style.filter = "grayscale(.85) contrast(1.15)"; }
      swing(true);
      this.slashes.push({ kind: "line", x0: hx - R, y0: hy - R, x1: hx + R, y1: hy + R, w: 7, col: main, glow: main, t: 0, dur: 150, life: 700 });
      await this.wait(130);
      this.slashes.push({ kind: "line", x0: hx + R, y0: hy - R, x1: hx - R, y1: hy + R, w: 7, col: main, glow: main, t: 0, dur: 150, life: 700 });
      await this.wait(140);
    } else if (style === "oneshot") {
      swing(true); await this.wait(80);
      this.slashes.push({ kind: "line", x0: g.x0 - 60, y0: g.y1 + 10, x1: g.x1 + 60, y1: g.y0 - 10, w: 10, col: main, glow: main, t: 0, dur: 90, life: 800 });
      await this.wait(70);
    } else { // slash / shatter / rend - дуга
      const ltr = Math.random() < 0.5; swing(false); await this.wait(80);
      this.slashes.push({ kind: "arc", x: hx + (ltr ? R * 0.4 : -R * 0.4), y: hy + R * 0.2, r: R, a0: ltr ? -2.6 : -0.5, a1: ltr ? -0.9 : -2.2, w: 11, col: main, glow: main, t: 0, dur: 100, life: 360 });
      await this.wait(70);
    }
    // попадание: стоп-кадр, вспышка, отброс, частицы
    const heavy = style === "heavy" || style === "execute" || style === "oneshot";
    this.hitStop(heavy ? 120 : 70); this.sfx(heavy ? "impact_heavy" : "impact"); this.mobSfx("hit", null);
    this.mob.flash = 1; this.shakeBy(heavy ? 14 : style === "fists" ? 5 : 8);
    this.tween(this.mob, "sc", heavy ? 0.9 : 0.95, 70).then(() => this.tween(this.mob, "sc", 1, 260));
    this.tween(this.mob, "oy", -6, 70).then(() => this.tween(this.mob, "oy", 0, 260));
    const cols = this.mobFx.hit_colors;
    if (style === "shatter") { this.sfx("crack"); this._crack(hx, hy, 6, 7, _hsRgb(_hsMix(_hsHex(main), [255, 255, 255], 0.5)), main, 1800); this.burst(hx, hy, 26, [main, "#e8d8ff"], { glow: 8, shrink: true, g: 0.05, lmax: 800 }); }
    else if (style === "rend") this.burst(hx, hy, 34, [main, _hsRgb(_hsMix(_hsHex(main), [0, 0, 0], 0.4))], { g: 0.28, stretch: true, lmin: 500, lmax: 1200, floor: g.fy, min: 1, max: 4 });
    else if (style === "heavy") { this._crack(g.cx, g.fy - 2, 5, 9, "#0a0604", "#ff7a2a", 3000, Math.random() < 0.5 ? 0 : Math.PI); this.burst(g.cx, g.fy - 4, 40, [this.scene.ground, "#4a3e30", "#8a7a60"], { dir: -Math.PI / 2, spread: 1.4, g: 0.12, min: 2, max: 6, lmin: 500, lmax: 1100, smax: 7 }); }
    else if (style === "execute" || style === "oneshot") this.burst(hx, hy, 40, [main, "#ffffff"], { glow: 10, shrink: true, g: 0.02, min: 2, max: 8 });
    this.burst(hx, hy, qte ? 26 : 16, cols, { g: 0.2, min: 1.5, max: qte ? 7 : 5, floor: g.fy });
    if (qte) { this.rings.push({ x: hx, y: hy, r0: 10, r1: R * 1.4, col: "#fff3c8", t: 0, life: 420 }); this.burst(hx, hy, 14, ["#ffffff", "#fff3c8"], { glow: 10, shrink: true, g: 0, min: 3, max: 9, lmax: 500 }); }
    return { hx, hy };
  }
  // Снять замедление/серый кадр добивания.
  endSlowMo() { this.timeScale = 1; this.canvas.style.filter = ""; }

  // Ответный удар моба по стилю combat_fx.attack_style.
  async retaliate() {
    const g = this.mobGeom(); if (!g) return; const style = this.mobFx.attack_style, col = this.mobFx.attack_color;
    this.mob.idle = false; this.targetMode = "hidden";
    if (style === "slam") {
      this.mobSfx("attack", null); this.sfx("growl");
      await this.tween(this.mob, "oy", -18, 240, k => 1 - Math.pow(1 - k, 2)); await this.tween(this.mob, "oy", 4, 90, k => k * k);
      if (!this.mobFx.sounds.attack) this.sfx("slam");
      this.shakeBy(18); this.hurt = 1; this.hurtCol = [200, 50, 30];
      this.waves.push({ x: g.cx, y: g.fy, r0: 20, r1: this.W * 0.75, flat: 0.16, col, t: 0, life: 700 });
      this.burst(g.cx, g.fy - 4, 50, [this.scene.ground, col, ...this.mobFx.hit_colors.slice(0, 1)], { dir: -Math.PI / 2, spread: 1.6, g: 0.14, min: 2, max: 8, lmax: 1200, smax: 7 });
      this.tween(this.mob, "oy", 0, 300);
    } else if (style === "spell") {
      this.mob.tell = 1; this.mobSfx("attack", "spell");
      await this.tween(this.mob, "sc", 1.06, 200);
      this.orbs.push({ x0: g.cx, y0: g.cy, col, t: 0, dur: 360 }); await this.wait(360);
      this.shakeBy(10); this.hurt = 1; this.hurtCol = _hsHex(col); this.flash(_hsRgb(_hsHex(col), 0.35), 400);
      this.burst(this.W / 2, this.H * 0.55, 40, [col, "#ffffff"], { glow: 10, shrink: true, g: 0, min: 3, max: 10, lmax: 700 });
      this.tween(this.mob, "sc", 1, 300);
    } else { // claw / bite - бросок на экран
      this.mobSfx("attack", null); this.sfx("growl"); this.mob.tell = 1;
      await this.tween(this.mob, "sc", 1.18, 150, k => k * k); this.tween(this.mob, "oy", 14, 150);
      if (!this.mobFx.sounds.attack) this.sfx(style === "bite" ? "bite" : "claw");
      this.shakeBy(10); this.hurt = 0.9; this.hurtCol = [224, 60, 48];
      if (style === "bite") this.bites.push({ t: 0 });
      else { const bx = this.W * 0.5 + _hsRnd(-80, 80), by = this.H * 0.45; for (let i = 0; i < 3; i++) this.claws.push({ x0: bx + 140 + i * 44, y0: by - 130 + i * 8, x1: bx - 110 + i * 44, y1: by + 120 + i * 8, t: -i * 45 }); }
      this.tween(this.mob, "sc", 1, 320); this.tween(this.mob, "oy", 0, 320);
    }
    this.hitStop(60);
    await this.wait(380);
    this.mob.idle = true; this.targetMode = "idle";
  }

  // Смерть: спрайт рассыпается на свои пиксели; soul - душа поднимается из тела.
  async die({ soul = false } = {}) {
    const g = this.mobGeom(); this.targetMode = "hidden"; if (!g) return;
    this.sfx("kill"); this.mobSfx("death", null); this.flash("rgba(255,215,106,.35)");
    const sp = g.sp, s = g.s;
    for (let y = 0; y < sp.h; y++) for (let x = 0; x < sp.w; x++) {
      const ch = sp.grid[y][x]; if (ch === "." || !sp.pal[ch] || Math.random() > 0.55) continue;
      const px = g.dx + x * s + s / 2, py = g.dy + y * s + s / 2;
      this.parts.push({ x: px, y: py, vx: (px - g.cx) * 0.012 + _hsRnd(-0.6, 0.6), vy: _hsRnd(-2.2, -0.4), g: -0.02, drag: 0.97, life: _hsRnd(700, 1500), t: 0, size: s, col: sp.pal[ch], shrink: true });
    }
    this.mob.hidden = true;
    if (soul) {
      for (let i = 0; i < 26; i++) this.timers.push({ left: 300 + i * 30, fn: () => { const k = i / 26; this.parts.push({ x: g.cx + Math.sin(k * 9) * 30 * (1 - k), y: g.cy - k * this.H * 0.5, vx: 0, vy: -0.4, g: 0, life: 700, t: 0, size: 6 - k * 3, col: "#bff4ff", glow: 16, shrink: true }); } });
      this.timers.push({ left: 400, fn: () => this.sfx("soul") });
    }
    this.burst(g.cx, g.fy - 10, 20, ["#ffd76a", "#fff3c8"], { glow: 8, g: -0.03, dir: -Math.PI / 2, spread: 1, min: 1, max: 3, lmin: 800, lmax: 1400, shrink: true });
    await this.wait(soul ? 1300 : 900);
  }
  // Цель уходит в туман (бегство босса / отступление игрока).
  async fadeAway(ms = 1400) {
    this.targetMode = "hidden"; this.mob.idle = false;
    this.tween(this, "fogBoost", 1, ms * 0.65); this.tween(this.mob, "sc", 0.8, ms); this.tween(this.mob, "oy", -30, ms);
    await this.tween(this.mob, "alpha", 0, ms);
  }
  async defeat() {
    this.targetMode = "hidden"; this.sfx("defeat"); this.canvas.style.filter = "grayscale(1) brightness(.65)"; this.hurt = 1;
    await this.tween(this.mob, "alpha", 0, 900);
  }
  // Угрожающая «стойка» между ударами.
  tell() { this.mob.tell = 1; this.tween(this.mob, "oy", 3, 160).then(() => this.tween(this.mob, "oy", 0, 300)); }

  // ---------- QTE: кольцо сжимается к прицелу на цели; попал ли - решает сцена, бонус к урону считает сервер ----------
  startQte(onResolve) {
    const g = this.mobGeom(); if (!g || this.qte) return false;
    const R = Math.min(g.x1 - g.x0, g.y1 - g.y0) * 0.42;
    this.qte = { x: g.cx, y: _hsLerp(g.y0, g.y1, 0.45), R, tol: 0.2, t0: this.t, dur: 1100, onResolve };
    this.host.classList.add("qte-on"); return true;
  }
  resolveQte(timeout = false) {
    const q = this.qte; if (!q) return; this.qte = null; this.host.classList.remove("qte-on");
    const hit = !timeout && q.r != null && Math.abs(q.r - q.R) <= q.R * q.tol;
    this.rings.push({ x: q.x, y: q.y, r0: q.r || q.R, r1: (q.r || q.R) * 1.35, col: hit ? "#9dffc8" : "#ff6a5a", t: 0, life: 260 });
    this.sfx(hit ? "qte_hit" : "qte_miss");
    if (q.onResolve) q.onResolve(hit);
  }
  cancelQte() { this.qte = null; this.host.classList.remove("qte-on"); }
}
