// ScriptCut Canvas 引擎：读 build/timeline.json，画出第 T 秒的画面（纯函数，由 window.seek(T) 驱动）。
// 图层（下 → 上）：背景 → 自定义画面 draw → 元素 → 自定义画面 overlay → 顶部逐字标题 → 底部字幕 → HUD
// URL 参数：?blur=N 运动模糊子采样；&shutter=0.6 快门；&subs=0 不画字幕；&play=1 交互预览
import * as api from './api.js';

const params = new URLSearchParams(location.search);
const SAMPLES = Math.max(1, parseInt(params.get('blur') || '1', 10));
const SHUTTER = parseFloat(params.get('shutter') || '0.6');
const SUBS = params.get('subs') !== '0';
// ?only=scene：只画背景 + 自定义画面（剪映可编辑草稿里，其余图层是剪映原生的元素 / 文字）
const ONLY_SCENE = params.get('only') === 'scene';

const canvas = document.getElementById('stage');
let ctx, sub, sctx, TL, W, H, FPS;
const images = new Map();      // src → HTMLImageElement
const frameCache = new Map();  // url → HTMLImageElement（视频帧）
let FRAMES = {};               // "镜头/元素" → {dir, count, fps}
const scenes = {};             // 名字 → 模块

function loadImage(src) {
  if (images.has(src)) return images.get(src);
  const p = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => { imagesSync.set(src, img); resolve(img); };
    img.onerror = () => reject(new Error(`图片加载失败: ${src}`));
    img.src = '/' + src.replace(/^\/+/, '');
  });
  images.set(src, p);
  return p;
}

async function boot() {
  TL = await (await fetch('/build/timeline.json', { cache: 'no-store' })).json();
  try { FRAMES = await (await fetch('/build/frames/index.json', { cache: 'no-store' })).json(); } catch (e) { FRAMES = {}; }
  W = TL.width; H = TL.height; FPS = TL.fps;
  canvas.width = W; canvas.height = H;
  ctx = canvas.getContext('2d', { willReadFrequently: true });
  sub = document.createElement('canvas'); sub.width = W; sub.height = H;
  sctx = sub.getContext('2d', { willReadFrequently: true });

  const face = new FontFace(api.FONT, `url(${TL.font.url})`);
  await face.load();
  document.fonts.add(face);

  const srcs = new Set([TL.background_image]);
  for (const s of TL.shots) for (const it of s.items) if (it.kind === 'image') srcs.add(it.src);
  for (const name of new Set(TL.shots.map((s) => s.scene).filter(Boolean))) {
    const mod = await import(`/scenes/${name}.js`);
    scenes[name] = mod.default || mod;
    for (const pre of scenes[name].preload || []) srcs.add(pre);
    if (scenes[name].setup) await scenes[name].setup({ TL, api, load: loadImage });
  }
  await Promise.all([...srcs].filter(Boolean).map(loadImage));
  BG = await loadImage(TL.background_image);

  window.DURATION = TL.duration;
  window.FPS = FPS;
  window.TIMELINE = TL;
  window.seek = seek;
  window.collectCues = collectCues;
  window.__ready = true;
  if (params.get('play')) interactive();
}

let BG = null;

// ---------------------------------------------------------------- 镜头上下文（给自定义画面）

function shotAt(T) {
  for (const s of TL.shots) if (T < s.end) return s;
  return TL.shots[TL.shots.length - 1];
}

function makeS(shot, T) {
  const t = T - shot.start;
  const find = (word, n = 1) => {
    let idx = -1;
    for (let k = 0; k < n; k++) idx = shot.text.indexOf(word, idx + 1);
    return idx;
  };
  const voiced = shot.chars.filter((c, i) => /[\p{L}\p{N}]/u.test(shot.text[i]));
  const voAt = voiced.length ? voiced[0][0] - shot.start : 0;
  const voEnd = voiced.length ? voiced[voiced.length - 1][1] - shot.start : shot.end - shot.start;
  return {
    id: shot.id, index: shot.index, shot, t, T, start: shot.start, end: shot.end, dur: shot.end - shot.start,
    text: shot.text, W, H, fps: FPS, palette: TL.style.palette, voAt, voEnd,
    // 某个词开口时刻（相对镜头开始，秒）；找不到返回 voAt。frac=0.5 取词中间
    kw(word, n = 1, frac = 0) {
      const i = find(word, n);
      if (i < 0) return voAt;
      const a = shot.chars[i][0], b = shot.chars[i + word.length - 1][1];
      return api.lerp(a, b, frac) - shot.start;
    },
    kwEnd(word, n = 1) {
      const i = find(word, n);
      return i < 0 ? voEnd : shot.chars[i + word.length - 1][1] - shot.start;
    },
    mark(name) { return (shot.marks[name] ?? shot.start) - shot.start; },
    item(el) { return shot.items.find((x) => x.el === el); },
    img(src) { return imagesSync.get(src); },
  };
}

const imagesSync = new Map();

// ---------------------------------------------------------------- 元素

function animAt(it, local) {
  const fr = it.anim.frames;
  if (!fr || !fr.length) return [0, 0, 1, 1, 0, 1, 0];
  const x = local * FPS;
  if (x >= fr.length - 1) return fr[fr.length - 1];
  const i = Math.max(0, Math.floor(x)), k = x - i;
  const a = fr[i], b = fr[Math.min(i + 1, fr.length - 1)];
  return a.map((v, j) => v + (b[j] - v) * k);
}

function idleAt(it, local) {
  const cfg = TL.style.idle || {};
  const held = local - it.anim.dur;
  if (held <= 0 || it.idle === 'none') return [1, 0];
  if (it.idle === 'zoom') return [1 + Math.min(cfg.zoom_max ?? 0.06, (cfg.zoom_rate ?? 0.02) * held), 0];
  if (it.idle === 'float') {
    const amp = cfg.float_px ?? 10, per = cfg.float_period ?? 2.8;
    return [1, -amp * (1 - Math.cos((2 * Math.PI * held) / per)) / 2];
  }
  return [1, 0];
}

async function videoFrame(shot, it, local) {
  const f = FRAMES[`${shot.id}/${it.el}`];
  if (!f) return null;
  const n = Math.min(f.count - 1, Math.max(0, Math.floor(local * f.fps)));
  const url = `/${f.dir}/${String(n + 1).padStart(5, '0')}.jpg`;
  if (!frameCache.has(url)) {
    frameCache.set(url, new Promise((res) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = url; }));
    if (frameCache.size > 240) frameCache.delete(frameCache.keys().next().value);
  }
  return frameCache.get(url);
}

async function prefetch(T) {
  const shot = shotAt(T);
  for (const it of shot.items) {
    if (it.kind === 'video' && T >= it.appear) it._frame = await videoFrame(shot, it, T - it.appear);
    if (it.kind === 'image') it._img = await loadImage(it.src);
  }
}

function drawItem(g, shot, it, T) {
  if (T < it.appear || T >= it.end) return;
  const local = T - it.appear;
  const [dx, dy, sx, sy, rot, alpha, blur] = animAt(it, local);
  const [zs, fy] = idleAt(it, local);
  const src = it.kind === 'video' ? it._frame : it._img;
  g.save();
  g.translate(it.x + dx, it.y + dy + fy);
  if (rot) g.rotate((rot * Math.PI) / 180);
  g.scale(sx * zs, sy * zs);
  g.globalAlpha *= alpha;
  if (blur > 0.5) g.filter = `blur(${blur}px)`;
  if (it.preset === 'grow' && it.grow) {
    const p = api.outCubic(api.clamp(local / Math.max(0.01, it.anim.dur)));
    const w = it.w, h = it.h;
    g.beginPath();
    if (it.grow === 'right') g.rect(-w / 2, -h / 2, w * p, h);
    else if (it.grow === 'left') g.rect(w / 2 - w * p, -h / 2, w * p, h);
    else if (it.grow === 'down') g.rect(-w / 2, -h / 2, w, h * p);
    else g.rect(-w / 2, h / 2 - h * p, w, h * p);
    g.clip();
  }
  if (src) g.drawImage(src, -it.w / 2, -it.h / 2, it.w, it.h);
  else { // 视频帧缺失：占位
    g.fillStyle = 'rgba(0,0,0,.35)'; g.fillRect(-it.w / 2, -it.h / 2, it.w, it.h);
    api.text(g, `VIDEO ${it.el}`, 0, 0, { size: 48, color: '#fff' });
  }
  g.restore();
}

function shakeOffset(shot, T) {
  for (let k = 0; k < (shot.shakes || []).length; k++) {
    const t0 = shot.shakes[k], d = T - t0;
    if (d >= 0 && d < 0.28) {
      const r = api.rng(Math.floor(d * 15) + k * 97 + 1);
      const decay = (1 - d / 0.28) ** 2;
      return [(r() * 2 - 1) * 14 * decay, (r() * 2 - 1) * 14 * decay];
    }
  }
  return [0, 0];
}

// ---------------------------------------------------------------- 标题 / 字幕 / HUD

function drawCaption(g, T) {
  const cs = TL.style.caption;
  if (!cs || !cs.enabled) return;
  const c = TL.captions.find((x) => T >= x.start && T < x.end);
  if (!c) return;
  const size = cs.font_px || 62, lh = size * (cs.line_height || 1.3), reveal = cs.reveal || 0.12, mb = cs.blur || 5;
  g.save();
  g.font = `${TL.style.caption.weight || 800} ${size}px ${api.FONT}, sans-serif`;
  g.textBaseline = 'top';
  c.lines.forEach((line, row) => {
    const widths = line.map((ch) => g.measureText(ch.ch).width);
    const total = widths.reduce((a, b) => a + b, 0);
    let x = cs.align === 'center' ? W / 2 - total / 2 : cs.x;
    const y = cs.y + row * lh;
    line.forEach((ch, i) => {
      const p = (T - ch.t) / reveal;
      if (p > 0) {
        const q = api.outCubic(Math.min(1, p));
        g.save();
        g.globalAlpha = q;
        if (q < 1) g.filter = `blur(${mb * (1 - q)}px)`;
        g.fillStyle = ch.red ? cs.red : cs.ink;
        g.fillText(ch.ch, x, y);
        g.restore();
      }
      x += widths[i];
    });
  });
  g.restore();
}

function drawSubtitle(g, T) {
  const ss = TL.style.subtitle;
  if (!SUBS || !ss || !ss.enabled) return;
  const c = TL.subtitles.find((x) => T >= x.start && T < x.end);
  if (!c) return;
  const lines = c.text.split('\n');
  const size = ss.font_px || 54, lh = size * 1.3;
  lines.forEach((ln, i) => {
    api.text(g, ln, W / 2, ss.y - ((lines.length - 1) * lh) / 2 + i * lh,
      { size, color: ss.color || '#fff', stroke: ss.stroke || 0, strokeColor: ss.stroke_color || '#000' });
  });
}

function drawHud(g, shot, T) {
  const hud = TL.style.hud;
  if (!hud || !hud.enabled || shot.chapter) return;
  const k = Math.min(W, H) / 1080;
  const y = Math.round(H * (W < H ? 0.065 : 0.07));
  api.text(g, hud.title || '', 64 * k, y, { size: 30 * k, color: hud.color, align: 'left' });
  const chs = TL.chapters || [];
  if (!chs.length) return;
  let cur = null, done = 0;
  for (const c of chs) if (T >= c.start - 1e-6) { cur = c; done++; }
  if (cur) {
    const label = `${cur.n} ${cur.short || cur.title}`.replace('|', '');
    const w = api.measure(g, label, 28 * k, 800) + 40 * k;
    g.fillStyle = '#fff';
    api.rr(g, W - 64 * k - w, y - 25 * k, w, 50 * k, 25 * k);
    g.fill();
    api.text(g, label, W - 64 * k - w / 2, y, { size: 28 * k, color: hud.accent });
  }
  if (hud.progress) {
    for (let i = 0; i < chs.length; i++) {
      g.fillStyle = i < done ? hud.accent : 'rgba(128,140,155,.35)';
      api.rr(g, 64 * k + i * 46 * k, y + 40 * k, 38 * k, 8 * k, 4 * k);
      g.fill();
    }
  }
}

// ---------------------------------------------------------------- 一帧

function drawScene(g, T) {
  const shot = shotAt(T);
  const sc = shot.scene ? scenes[shot.scene] : null;
  const hide = new Set((sc && sc.hide) || []);
  if (ONLY_SCENE) ['elements', 'caption', 'subtitle', 'hud'].forEach((k) => hide.add(k));
  const S = makeS(shot, T);
  g.save();
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1;
  g.filter = 'none';
  if (!hide.has('background')) g.drawImage(BG, 0, 0, W, H);
  else { g.fillStyle = '#000'; g.fillRect(0, 0, W, H); }
  const [ox, oy] = shakeOffset(shot, T);
  g.translate(ox, oy);
  if (sc && sc.draw) { g.save(); sc.draw(g, S, api); g.restore(); }
  if (!hide.has('elements')) for (const it of shot.items) drawItem(g, shot, it, T);
  if (sc && sc.overlay) { g.save(); sc.overlay(g, S, api); g.restore(); }
  g.setTransform(1, 0, 0, 1, 0, 0);
  if (!hide.has('caption')) drawCaption(g, T);
  if (!hide.has('subtitle')) drawSubtitle(g, T);
  if (!hide.has('hud')) drawHud(g, shot, T);
  g.restore();
}

async function seek(T) {
  T = Math.max(0, Math.min(T, TL.duration - 1e-4));
  if (SAMPLES === 1) {
    await prefetch(T);
    drawScene(ctx, T);
    return;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);
  // 运动模糊：快门区间内均匀取 N 个子帧，用 1/(i+1) 的透明度逐张叠成精确平均
  for (let i = 0; i < SAMPLES; i++) {
    const Ti = Math.max(0, T + ((i + 0.5) / SAMPLES - 0.5) * (SHUTTER / FPS));
    await prefetch(Ti);
    sctx.clearRect(0, 0, W, H);
    drawScene(sctx, Ti);
    ctx.globalAlpha = 1 / (i + 1);
    ctx.drawImage(sub, 0, 0);
  }
  ctx.globalAlpha = 1;
}

// 自定义画面声明的音效提示点 → 绝对时间
function collectCues() {
  const out = [];
  for (const shot of TL.shots) {
    const sc = shot.scene ? scenes[shot.scene] : null;
    if (!sc || !sc.cues) continue;
    const S = makeS(shot, shot.start);
    for (const c of sc.cues(S, api) || []) {
      out.push({ t: +(shot.start + c.t).toFixed(3), name: c.name || c.kind, gain: c.gain ?? 1, source: `${shot.id}/scene` });
    }
  }
  return out;
}

function interactive() {
  document.body.classList.add('interactive');
  const range = document.getElementById('seek'), label = document.getElementById('time');
  range.max = TL.duration;
  let playing = false, t0 = 0, base = 0;
  const show = async (t) => { await seek(t); range.value = t; label.textContent = t.toFixed(2); };
  range.oninput = () => show(+range.value);
  document.getElementById('play').onclick = () => {
    playing = !playing; base = +range.value; t0 = performance.now();
    const tick = async () => { if (!playing) return; const t = base + (performance.now() - t0) / 1000; if (t >= TL.duration) { playing = false; return; } await show(t); requestAnimationFrame(tick); };
    tick();
  };
  show(0);
}

boot().catch((e) => {
  window.__error = String((e && e.stack) || e);
  console.error(e);
});
