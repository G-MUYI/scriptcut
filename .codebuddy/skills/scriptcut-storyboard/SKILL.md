---
name: scriptcut-storyboard
description: 把口播文案拆成 ScriptCut 的分镜文件 storyboard.yaml（镜头 + 素材库 + 触发词 + 标红词 + 版式 + 入场动画），并跑通 scriptcut check。用户要求拆分镜、写分镜脚本、改镜头 / 换元素 / 改标红词时使用。
---

# 拆分镜（storyboard.yaml）

输入：`<项目>/input/script.txt`（docx 先用 `scriptcut init --script x.docx` 转好）。输出：`<项目>/storyboard.yaml`。
写之前先 `scriptcut schema --json` 看当前风格有哪些版式、角色、点缀色、入场预设、音效。完整字段：`docs/schema.md`。

## 硬规则（check 会报错）
1. 所有镜头的 `text` 按顺序拼接 **必须与 script.txt 完全一致**（含标点、空格、错别字、重复字）。只在标点处切。
2. `trigger` / `red` / `marks` / `sfx.at` 必须是**本镜 text 里的原文片段**；数字要加引号（`"1263"`、`"0.8%"`）。
3. 每镜最多 `max_items_per_shot`（默认 4）个 item；`el` 必须在 `elements` 里。
4. 叠加写 `over: 目标编号`，**不要写 `on:`**（YAML 会解析成布尔值）。
5. 无口播镜头（片头、片尾卡）：不写 text，写 `duration: 秒`。
6. AI 手写字素材（`role: word` 且是图片）≤12 字；图上标注 `label` ≤4 字；更长的字用 `kind: word` 本地渲染。

## 切镜头
- 每镜 2~4 秒（约 10~20 字），同一个意思不要拆到两镜；镜头数 ≈ 预估秒数 / 2.7。
- 用 `seg:` 标段落（换段时配音停顿更长）；章节开头的镜头加 `chapter: {n: "01", title: "标题|可换行"}`（自动生成整屏章节卡 + 音效；章节卡镜头不要再放 items）。

## 配元素（每镜优先 2~3 个）
- 先想「这句话画什么最直观」：具体物件 > 图表 > 人物表情 > 文字。
- **第一个元素的 trigger 落在本镜开头 1~4 个字**，否则换镜后是空画面（timeline 会提示）。同镜多个元素的 trigger 按念的先后错开。
- 同一素材多镜复用（人物尤其要复用，省出图量）。
- 元素类型：
  | 写法 | 用途 |
  |---|---|
  | `{role: character/big/hero/icon/small, accent, accent_part, name, prompt}` | AI 出图：prompt 只写「画什么」（主体、动作 / 表情、朝向），风格段和配色句由风格自动拼接，**不要写进 prompt** |
  | `{kind: word, text, hl, accent}` | 本地渲染的大字 / 数字 / 金句（清晰、零成本）；`hl` 是用点缀色的部分 |
  | `{role: word, text, hl, accent}` | AI 画的手写字（条漫风格更统一，但可能写错，≤12 字） |
  | `{src: materials/xx.png, role: media, fit: contain/cover}` | 用户截图、新闻图 |
  | `{src: materials/xx.mp4, clip_in: 3.5, volume: 0}` | 视频片段（`volume` > 0 保留原声） |
- 编号：人物 `C01…`，道具 / 图形 `P01…`，文字 `W01…`，用户素材 `M01…`。
- 点缀色：每张图只有**一个**点缀色（从风格 palette 按语义选），`accent_part` 写清涂在哪；红色只给警示 / 否定。
- 人物：主角用 `role: character`（自动拼 project.yaml 的 `character.description`、挂参考图）；非主角在 prompt 末尾写「不使用人物参考图」并设 `ref: false`。
- 否定 / 打叉 / 圈注：做成独立素材，用 `over:` 叠在目标上（默认 `stamp` 入场，红叉建议 `sfx: strike`）。

## 版式（`layout`）
每镜一个视觉重心：有人物时人物最大，其余元素明显更小、错落；不要几个元素一样大平均排开。
连续几镜不要同一版式；人物镜头左右交替（`person_l` / `person_r`）。竖屏风格常用：
`person` `person_word` `person_l/r` `person_duo` `hero` `word_hero` `hero_word` `duo_diag` `duo_v/h` `trio_tri` `trio_zig` `row3_hero` `quad`
`media`（截图 / 视频放进大盒子）`media_word` `full`。横屏 `dark-hud`：`hero` `left_word` `right_word` `duo_h` `trio_row` `media` …
微调：item 里 `slot`（用版式第几个位置）、`pos: [x, y]`、`size`（等效边长 √宽×高）、`box: [w, h]`。

## 入场动画（`preset`，不写按角色默认；都在 0.35~0.5 秒内完成）
| 场景 | preset | 自动音效 |
|---|---|---|
| 人物登场 | `slide_auto`（按所在一侧划入） | whoosh |
| 普通图标 | `pop` | pop |
| 大字、重磅数字 | `zoom_slam`（可加 `shake: true` 震屏） | thud |
| 有重量的东西 | `drop` | thud |
| 箭头、曲线、柱状图、光带 | `grow` + `grow: right/up/left/down` | scribble |
| 叠加的红叉、印章 | `stamp` | stamp |
| 温和过渡、说明 | `fade_up` / `blur_in` | pop_soft |
| 牌子、标签 | `swing` | swing |
落定后自动随机「缓慢放大 / 上下浮动」，可写 `idle: zoom / float / none`。同一镜不超过 2 种入场。

## 标题与字幕
- `red: [词]`：顶部逐字标题里标红的关键词（数字、核心概念、反转词，每句 1~2 个）。
- `cap: ["第一句|换行", "第二句"]`：手动断句（字必须与原文一致，标点可省）。不写则按逗号自动断。
- `explainer` 风格用底部字幕（自动从配音生成），不需要 cap。

## 自定义画面（可选）
某镜需要代码动画（图表生长、粒子、地图、数据流…）时写 `scene: 名字` 并按 `scriptcut-scenes` skill 写 `scenes/名字.js`；
用 `marks: {名字: 原文词}` 给代码画面提供时间点。

## 事实核查
文案里的数字、排名、引用写进 `facts:`（`{claim, value, status: 一致/有出入/未核实, source, url}`），交付说明会列出未核实项。

## 模板
```yaml
title: 越存钱越穷
script: input/script.txt
elements:
  C01: {role: character, accent: 明黄, accent_part: 卫衣, name: 女主摊手, prompt: 年轻女生耸肩、双手向两侧摊开，无奈}
  P01: {role: hero, accent: 大红, accent_part: 红汤, name: 火锅, prompt: 一口冒着热气的铜火锅}
  P02: {role: hero, accent: 大红, accent_part: 叉, name: 红叉, prompt: 一个粗犷的手绘大叉，笔触干涩有飞白}
  W01: {kind: word, text: "1263", hl: "6", accent: 嫩绿}
shots:
  - id: S01
    seg: 开场
    text: 利息1263。听着还行？
    layout: person_word
    red: ["1263"]
    items:
      - {el: W01, trigger: 利息, slot: 2}
      - {el: C01, trigger: 听着还行, slot: 1}
  - id: S02
    text: 换来不够一顿像样的火锅。
    layout: hero
    red: [火锅]
    items:
      - {el: P01, trigger: 换来}
      - {el: P02, over: P01, trigger: 火锅, size: 560, sfx: strike}
facts:
  - {claim: 一年定期利率 1.263%, status: 未核实, source: ""}
```

## 完成后
跑 `scriptcut check <项目> --json` 到 `errors` 为空，再 `scriptcut prompts <项目>`。
回复用户：镜头数、元素数（人物 / 文字 / AI 图 / 用户素材）、预估时长、校验结果、需要用户提供的素材。
