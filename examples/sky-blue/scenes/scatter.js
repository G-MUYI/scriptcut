// 自定义画面示例：一束阳光（彩色光点）穿过大气，撞上空气分子后蓝色光点四散。
// 契约：export default { draw(ctx, S, api), overlay?, cues?(S, api), hide?: [...], preload?: [...] }
// S.t 是镜头内秒数；S.kw('词') 是这个词开口的镜头内时刻——所有动作都挂在口播的字上。

const COLORS = ['#E5484D', '#F28C28', '#F2C230', '#4CB35E', '#2F80ED', '#3D5AFE', '#8E6CCF'];

export default {
  draw(ctx, S, api) {
    const { W, H, t } = S;
    const r = api.rng(7);
    const tBeam = S.kw('光线'), tHit = S.kw('撞上'), tMol = S.kw('空气分子');

    // 大气层：浅蓝色带，随「大气」淡入
    const a = api.fin(t, S.kw('大气'), 0.6);
    const g = ctx.createLinearGradient(0, 520, 0, 1500);
    g.addColorStop(0, api.hexA('#4A90D9', 0.10 * a));
    g.addColorStop(1, api.hexA('#4A90D9', 0.02 * a));
    ctx.fillStyle = g;
    ctx.fillRect(0, 520, W, 980);

    // 空气分子：灰色小圈，「无数」时铺开
    const molA = api.fin(t, S.kw('无数'), 0.5);
    const mols = [];
    for (let i = 0; i < 34; i++) mols.push([80 + r() * (W - 160), 600 + r() * 820, 10 + r() * 10]);
    ctx.lineWidth = 5;
    ctx.strokeStyle = api.hexA('#555555', 0.7 * molA);
    for (const [x, y, rad] of mols) {
      const wob = api.noise1(t * 0.8 + x, 3) * 6;
      ctx.beginPath();
      ctx.arc(x + wob, y - wob, rad * api.outBack(molA), 0, Math.PI * 2);
      ctx.stroke();
    }

    // 光点：从左上角射入；撞上之后蓝 / 紫光点改向四散，红橙黄绿继续前进
    const n = 70;
    for (let i = 0; i < n; i++) {
      const c = COLORS[i % 7];
      const born = tBeam + (i / n) * 2.2;
      const life = t - born;
      if (life < 0) continue;
      const speed = 620;
      let x = -40 + life * speed * 0.82, y = 420 + life * speed * 0.57;
      const hitAt = Math.max(tHit, born + 0.5);
      const scatter = (i % 7 >= 4) && t > hitAt;
      if (scatter) {
        const dt = t - hitAt;
        const x0 = -40 + (hitAt - born) * speed * 0.82, y0 = 420 + (hitAt - born) * speed * 0.57;
        const ang = r() * Math.PI * 2;
        x = x0 + Math.cos(ang) * dt * 480;
        y = y0 + Math.sin(ang) * dt * 480;
      }
      if (x > W + 40 || y > H + 40 || x < -60 || y < -60) continue;
      ctx.fillStyle = c;
      ctx.beginPath();
      ctx.arc(x, y, scatter ? 11 : 9, 0, Math.PI * 2);
      ctx.fill();
    }

    // 「空气分子」时放大其中一个分子
    const z = api.pop(t, tMol, 0.5);
    if (z > 0) {
      api.at(ctx, W / 2, 980, z, () => {
        ctx.lineWidth = 10;
        ctx.strokeStyle = '#333';
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath(); ctx.arc(-46, 0, 56, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.arc(46, 0, 56, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        api.text(ctx, 'N₂ / O₂', 0, 110, { size: 44, color: '#555' });
      });
    }
  },

  // 声明音效提示点（镜头内秒数）；scriptcut cues 会收集到 build/scene_cues.json，音频阶段自动混进去
  cues(S) {
    return [
      { t: S.kw('光线'), name: 'whoosh' },
      { t: S.kw('撞上'), name: 'cascade', gain: 0.8 },
    ];
  },
};
