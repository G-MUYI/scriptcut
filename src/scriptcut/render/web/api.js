// 给自定义画面（project/scenes/*.js）用的工具：缓动、时间窗、文字、圆角矩形、确定性随机数。
// 约定：画面是时间 t 的纯函数——不读真实时钟、不用 Math.random()，这样并行渲染、重复渲染逐像素一致。

export const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, k) => a + (b - a) * k;
export const prog = (t, a, b) => clamp((t - a) / Math.max(1e-6, b - a));

// 缓动（输入 0..1）
export const linear = (x) => x;
export const inCubic = (x) => x * x * x;
export const outCubic = (x) => 1 - (1 - x) ** 3;
export const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2);
export const outQuint = (x) => 1 - (1 - x) ** 5;
export const outExpo = (x) => (x >= 1 ? 1 : 1 - 2 ** (-10 * x));
export const inExpo = (x) => (x <= 0 ? 0 : 2 ** (10 * x - 10));
export const outBack = (x, s = 1.70158) => 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2;
export const outElastic = (x, p = 0.3) => (x <= 0 || x >= 1 ? +(x >= 1) : 2 ** (-10 * x) * Math.sin(((x - p / 4) * 2 * Math.PI) / p) + 1);
export const inOutSine = (x) => -(Math.cos(Math.PI * x) - 1) / 2;
// 阻尼弹簧：从 1 衰减振荡到 0（t 单位秒）
export const spring = (t, freq = 3, damping = 6) => Math.exp(-damping * t) * Math.cos(2 * Math.PI * freq * t);

// 时间窗：从 at 开始、dur 秒内 0→1（默认 outCubic）
export const fin = (t, at, dur = 0.4, ease = outCubic) => ease(prog(t, at, at + dur));
// 弹出：0→1 带过冲
export const pop = (t, at, dur = 0.45) => (t < at ? 0 : Math.max(0, outBack(prog(t, at, at + dur), 1.6)));
// 淡入淡出包络：[a, b] 内为 1，前后各 f 秒过渡
export const window_ = (t, a, b, f = 0.25) => Math.min(prog(t, a - f, a), 1 - prog(t, b, b + f));

export function rr(ctx, x, y, w, h, r) {
  const k = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + k, y);
  ctx.arcTo(x + w, y, x + w, y + h, k);
  ctx.arcTo(x + w, y + h, x, y + h, k);
  ctx.arcTo(x, y + h, x, y, k);
  ctx.arcTo(x, y, x + w, y, k);
  ctx.closePath();
}

export const FONT = 'SCMain';

// 文字：{size, weight, color, align, baseline, alpha, stroke, strokeColor, font, ls(字距)}
export function text(ctx, str, x, y, o = {}) {
  const size = o.size || 48;
  ctx.save();
  ctx.globalAlpha *= o.alpha ?? 1;
  ctx.font = `${o.weight || 800} ${size}px ${o.font || FONT}, sans-serif`;
  ctx.textAlign = o.align || 'center';
  ctx.textBaseline = o.baseline || 'middle';
  if (o.ls) ctx.letterSpacing = `${o.ls}px`;
  if (o.stroke) {
    ctx.lineJoin = 'round';
    ctx.lineWidth = o.stroke;
    ctx.strokeStyle = o.strokeColor || '#000';
    ctx.strokeText(str, x, y);
  }
  ctx.fillStyle = o.color || '#111';
  ctx.fillText(str, x, y);
  ctx.restore();
}

export function measure(ctx, str, size = 48, weight = 800, font = FONT) {
  ctx.save();
  ctx.font = `${weight} ${size}px ${font}, sans-serif`;
  const w = ctx.measureText(str).width;
  ctx.restore();
  return w;
}

// 在 (x, y) 处以 scale / rotate / alpha 画 fn（坐标原点移到 x, y）
export function at(ctx, x, y, s, fn, rot = 0, alpha = 1) {
  if (s <= 0 || alpha <= 0) return;
  ctx.save();
  ctx.translate(x, y);
  if (rot) ctx.rotate(rot);
  ctx.scale(s, s);
  ctx.globalAlpha *= alpha;
  fn();
  ctx.restore();
}

// 确定性随机数（mulberry32）
export function rng(seed = 1) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// 平滑一维噪声（确定性）
export function noise1(x, seed = 0) {
  const h = (n) => { const s = Math.sin(n * 127.1 + seed * 311.7) * 43758.5453; return s - Math.floor(s); };
  const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f);
  return lerp(h(i), h(i + 1), u) * 2 - 1;
}

export function hexA(hex, a) {
  const h = hex.replace('#', '');
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
}
