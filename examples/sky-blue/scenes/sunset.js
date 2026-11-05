// 自定义画面示例：天色从蓝变橙、太阳下沉、大气层变厚，「散射光了」时蓝色光点被甩出画面。
// hide: ['background'] —— 这个镜头自己画整屏背景。

export default {
  hide: ['background'],

  draw(ctx, S, api) {
    const { W, H, t } = S;
    const dusk = api.fin(t, S.kw('傍晚'), 2.4, api.inOutSine);   // 0 → 1：白天 → 傍晚
    const sky = ctx.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0, mix('#BFE0FF', '#FBD3A8', dusk));
    sky.addColorStop(0.55, mix('#8CC4F5', '#F08A4B', dusk));
    sky.addColorStop(1, mix('#E8F4FF', '#FFC870', dusk));
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, W, H);

    // 太阳沿弧线下沉
    const sx = api.lerp(760, 300, dusk), sy = api.lerp(620, 1320, dusk);
    ctx.fillStyle = mix('#FFE066', '#FF7A3D', dusk);
    ctx.beginPath(); ctx.arc(sx, sy, 110, 0, Math.PI * 2); ctx.fill();

    // 大气层：「更厚」时变厚
    const thick = api.fin(t, S.kw('更厚'), 0.8);
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    ctx.fillRect(0, 1180 - 260 * thick, W, 260 * thick + 200);

    // 地面
    ctx.fillStyle = '#2B2B3A';
    ctx.fillRect(0, 1460, W, H - 1460);

    // 斜穿的阳光（虚线）
    const beam = api.fin(t, S.kw('斜穿'), 0.6);
    if (beam > 0) {
      ctx.save();
      ctx.setLineDash([26, 18]);
      ctx.lineWidth = 8;
      ctx.strokeStyle = 'rgba(255,255,255,0.85)';
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(api.lerp(sx, 960, beam), api.lerp(sy, 1440, beam));
      ctx.stroke();
      ctx.restore();
    }

    // 蓝色光点在半路被甩出去
    const lost = S.kw('散射光了');
    const r = api.rng(11);
    for (let i = 0; i < 26; i++) {
      const k = i / 26;
      const bx = api.lerp(sx, 960, k), by = api.lerp(sy, 1440, k);
      const ang = r() * Math.PI * 2, spd = 300 + r() * 500;
      const dt = Math.max(0, t - lost - k * 0.4);
      const a = beam * (1 - api.prog(dt, 0.6, 1.2));
      if (a <= 0) continue;
      ctx.fillStyle = api.hexA('#2F80ED', a);
      ctx.beginPath();
      ctx.arc(bx + Math.cos(ang) * spd * dt, by + Math.sin(ang) * spd * dt, 12, 0, Math.PI * 2);
      ctx.fill();
    }
  },

  cues(S) {
    return [{ t: S.kw('散射光了'), name: 'whoosh', gain: 0.9 }];
  },
};

function mix(a, b, k) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * k)).join(',')})`;
}
