---
name: scriptcut-scenes
description: 为 ScriptCut 项目写自定义 Canvas 画面代码（scenes/*.js）：数据图表生长、粒子、地图、HUD、品牌片头等素材图做不到的动画，画面是时间的纯函数、所有动作挂在口播字上。用户要求「用代码画这一镜」「做动效 / 数据可视化 / 广告片」或分镜里写了 scene 时使用。
---

# 写自定义 Canvas 画面

分镜里 `scene: sunset` 的镜头会加载 `<项目>/scenes/sunset.js`。引擎负责背景、元素、标题、字幕、HUD、出片和音效；
你只写这一镜特有的画面。参考：`examples/sky-blue/scenes/*.js`、`src/scriptcut/render/web/api.js`。

## 契约
```js
export default {
  hide: ['background'],            // 可选：隐藏内置层 background / elements / caption / subtitle / hud
  preload: ['materials/map.png'],  // 可选：额外要用的图片（相对项目目录），用 S.img(路径) 取
  async setup({ TL, api, load }) {}, // 可选：启动时调用一次（预计算点云等）
  draw(ctx, S, api) {},            // 画在背景之上、元素之下
  overlay(ctx, S, api) {},         // 画在元素之上、标题之下
  cues(S, api) { return [{ t: S.kw('爆'), name: 'impact', gain: 1 }]; },  // 音效提示点（镜头内秒数）
};
```
`S`：`t`（镜头内秒）、`T`（全片秒）、`dur`、`W`/`H`、`text`、`palette`、`voAt` / `voEnd`（本镜开口 / 收声）、
`kw('词', 第n次=1, frac=0)` 某词开口时刻、`kwEnd('词')` 收尾时刻、`mark('名字')`（分镜 marks）、`item('W01')`（元素位置尺寸）、`img(src)`。
`api`：`clamp lerp prog`、缓动 `outCubic inOutCubic outBack outElastic outExpo inExpo spring`、
`fin(t, at, dur)` 0→1、`pop(t, at, dur)` 带过冲、`text(ctx, str, x, y, {size, weight, color, align, stroke, strokeColor, alpha, ls})`、
`measure`、`rr` 圆角矩形、`at(ctx, x, y, scale, fn, rot, alpha)`、`rng(seed)` 确定性随机、`noise1(x, seed)`、`hexA(hex, a)`、`FONT`。

## 铁律
1. **纯函数**：画面只由 `S.t` 决定。不用 `Date.now()` / `performance.now()` / `Math.random()` / 累积状态；随机用 `api.rng(固定种子)`。
   并行分段渲染、运动模糊子采样、重复渲染都依赖这一条。
2. **动作挂在字上**：所有时刻写成 `S.kw('词')` 或 `S.mark('名字')` 的偏移，不写死秒数——换配音后自动对齐。
3. **声音跟画面**：每个重要动作在 `cues()` 里声明音效（名字见 `scriptcut schema` 的 sfx）；冲击类（thud / impact / stamp / boom）自动按峰值对齐。
4. **不画字幕区**：避开顶部标题区（风格 `content_area` 的 y0 以上）和底部字幕 / 平台遮挡区（竖屏底部约 1560 以下）。

## 动效语言（从广告与解说原型沉淀）
- **强调来自内容本身的变化**：数据长出来、相机去看它、其余元素退到 30% 亮度——而不是每次都色差砸字 + 冲击波 + 闪白（重复两次就廉价）。
- **砸点前留 0.4 秒绝对静止**：画面、粒子、相机、音乐一起停住再砸，反差最强（音频阶段可在该处少放音效）。
- **颜色语义**：一帧最多一个强调色元素；红 = 风险、绿 = 通过、品牌色 = 主角。
- **文字层级**：英文小标签 → 中文粗标题 → 细字说明；每个画面只用粗 / 细两种字重；画面里不放标点（数字、%、= 除外）。
- **入场 0.35~0.5 秒**，快速划入约 0.12 秒到位、带一点过冲；落定后缓慢呼吸（放大 2%/秒或轻浮动），不要完全静止。
- **镜头之间要连贯**：交界处的元素状态一致（上一镜的线变成下一镜的轴），或者用硬切；不要无意义的叠化。
- **大数字**：用等宽数字逐位翻牌 / 滚动（`counter` 音效），一万以下不加千分位。
- 快速运动（彗星、光点）成片时用 `scriptcut render --blur 8~12` 开运动模糊。

## 在剪映草稿里
可编辑剪映草稿中，自定义画面镜头会渲染成一段只含代码画面本身的视频片段（需要 Playwright），
其上的元素、文字、标题、字幕仍是剪映原生对象。能用素材 + 入场预设表达的镜头就不要写代码画面，用户在剪映里更好改。

## 自检流程
```bash
scriptcut timeline <项目> --json
scriptcut stills <项目> --shot S05 --json     # 这一镜均匀 8 帧，打开图片看
scriptcut stills <项目> --at 12.3,12.8 --json # 关键时刻
scriptcut cues <项目> --json && scriptcut timeline <项目> --json && scriptcut audio <项目> --json
scriptcut render <项目> --from 10 --to 16 --json   # 只渲染这一段预览
scriptcut preview <项目>                           # 浏览器里拖动时间轴实时看
```
页面报错（JS 异常）会让 stills / render 直接失败并带上错误信息：修好再跑。

## 横屏广告片（dark-hud）建议结构
一个贯穿全片的视觉锚点（一根发光线 / 取景框 / 进度条）在每个镜头里变形，导演层的思路：
背景与相机连续、常驻 HUD（章节角标、进度线）、每镜只讲一件事、结尾品牌定格 + 风险提示小字。
片中的行情、数据如是示意要在画面和交付说明里标注「示意」，绝对化表述（最、第一、零风险）交给用户做合规确认。
