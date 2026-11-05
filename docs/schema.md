# 数据契约

ScriptCut 的每个阶段只通过文件交接，任何 Agent / 脚本都可以在中间插手（改 yaml、换素材、换配音）后从下一阶段继续。

```
input/script.txt ─┐
storyboard.yaml  ─┼─ check ─ voice ─▶ build/timing.json ─┐
project.yaml     ─┘                                      ├─ timeline ─▶ build/timeline.json ─┬─ audio ─▶ build/audio/master.wav
assets/ materials/ ── gen / prepare ─▶ build/assets/ ────┘                                    ├─ stills / render ─▶ out/<名字>.mp4
scenes/*.js ── cues ─▶ build/scene_cues.json ──────────────────(回到 timeline)                └─ jianying ─▶ 剪映草稿
```

## 项目目录

| 路径 | 说明 |
|---|---|
| `project.yaml` | 设置（可省略，全部有默认值） |
| `storyboard.yaml` | 分镜 + 素材库（Agent 主要编辑的文件） |
| `input/script.txt` | 文案，唯一文字来源，一字不改 |
| `input/voice.(wav/mp3/m4a/…)` | 可选：录好的配音；有它就走对齐，没有就走 TTS |
| `assets/<ID>.png` | AI 生成或手动放入的元素图（透明底最好；白底会自动抠） |
| `materials/` | 用户提供的截图、视频 |
| `scenes/<名字>.js` | 可选：自定义 Canvas 画面 |
| `fonts/` | 可选：项目专用字体（优先查找） |
| `build/` | 中间产物（可删，全部可重建） |
| `out/` | 成片与交付包 |

## project.yaml

```yaml
title: 标题
script: input/script.txt
style: whiteboard              # 内置风格名，或项目内 yaml 路径（可写 extends: whiteboard 继承）
style_overrides: {}            # 深合并到风格上（palette / music / caption …）
canvas: {width: 720, height: 1280}   # 可选：改画布，风格坐标按比例缩放
character: {description: "", ref: ""}   # 主角外观 / 参考图
voice:
  source: auto                 # auto | tts | file | none
  file: ""                     # 录音路径（默认 input/voice.*）
  align: auto                  # auto | asr | silence | estimate
  tts: {provider: auto, voice: "", speed: 1.0, model: "", verify: false}
  replace: {}                  # 读法替换，只影响 TTS
  gaps: {}                     # 覆盖风格的 voice_gaps（lead / sentence / segment / chapter_before / chapter_after / tail）
audio:
  loudness: -14
  music: {enabled: true, mood: bright|calm|tense, bpm: 104, gain: 0.6, duck: 0.65, file: ""}
  sfx: {enabled: true, master: 0.6, gain: {pop: 0.7}}
render: {backend: jianying|canvas|both, workers: 4, motion_blur: 1, crf: 18, format: png|jpeg}   # 默认 jianying：可编辑剪映草稿
image_gen: {provider: auto|ark|dashscope|siliconflow|openai|command|manual, knockout: true, concurrency: 2}
# 出图 API 的 Key / 模型 / 地址只写在 .env：IMAGE_PROVIDER IMAGE_API_KEY IMAGE_MODEL IMAGE_API_BASE IMAGE_SIZE IMAGE_EXTRA_JSON SCRIPTCUT_IMAGE_CMD
output: {name: ""}             # 成片文件名（默认目录名）
```

## storyboard.yaml

```yaml
title: 标题
script: input/script.txt
elements:
  <ID>:
    kind: image | video | word         # 默认 image；有 src 且是视频则 video
    role: character|big|hero|icon|small|word|media   # 决定默认大小、入场、是否可出画
    name: 简称
    prompt: 画什么（AI 出图）
    accent: 点缀色名（风格 palette）
    accent_part: 点缀色涂在哪
    label: 图上手写标注（≤4 字）
    text: 文字内容（word）
    hl: text 中用点缀色的部分
    src: materials/xx.png|mp4          # 用户素材
    fit: contain | cover               # 放进盒子版式时
    clip_in: 0                         # 视频截取起点（秒）
    volume: 0                          # 视频原声音量
    ref: null | true | false           # 是否挂人物参考图（null = 人物角色自动）
    knockout: null | true | false      # 出图后是否抠白底
shots:
  - id: S01
    seg: 段落名                         # 换段时配音停顿更长
    text: 本镜口播原文                   # 无口播镜头不写 text，写 duration
    duration: 0
    chapter: {n: "01", title: "标题|换行", short: "角标短名"}   # 章节卡
    layout: 版式名
    red: [标红词]
    cap: ["手动|断句"]
    marks: {名字: 原文词}               # 给自定义画面 / 静帧自检用的时间点
    scene: 自定义画面名
    sfx: [{name: ding, at: 原文词 | 秒数, offset: 0, gain: 1}]
    note: 备注
    items:
      - el: <ID>
        trigger: 原文片段（念到时入场；不写 = 镜头开始）
        preset: 入场预设
        slot: 1 / pos: [x, y] / size: 等效边长 / box: [w, h]
        grow: right|left|up|down
        delay: 0
        shake: false
        bleed: false                    # 允许出画
        over: <ID>                      # 叠在本镜另一个元素上
        sfx: 音效名 | ""                 # 覆盖 / 关闭预设音效
        idle: auto|zoom|float|none
facts:
  - {claim: 说法, value: 文案数值, verified: 核实值, status: 一致|有出入|未核实, source: 来源, url: 网址}
```

## build/timing.json

```json
{"script": "全稿", "start": [每个字的开口秒], "end": [每个字的收尾秒], "duration": 42.3,
 "source": "tts:edge | tts:fish | asr | silence | estimate", "audio": "build/voice/voice.wav",
 "slots": {"S00": [0.3, 1.9]}, "sentences": [{"i": 0, "j": 17, "start": 2.0, "end": 4.6}],
 "match_ratio": 0.97, "report": {}}
```
时间相对 `build/voice/voice.wav` 开头（这条音轨从 0 开始铺满全片）。

## build/timeline.json（两个渲染后端的唯一输入）

```json
{"fps": 30, "width": 1080, "height": 1920, "duration": 42.3, "timing_source": "tts:edge",
 "voice": "build/voice/voice.wav", "background_image": "build/assets/background.png",
 "font": {"file": "NotoSansCJK-Bold.ttc", "url": "/__font__/main.ttc"},
 "style": {"caption": {}, "subtitle": {}, "idle": {}, "hud": {}, "palette": {}, "chapter": {}},
 "chapters": [{"shot": "S00", "start": 0, "n": "01", "title": "", "short": ""}],
 "shots": [{"id": "S01", "start": 1.9, "end": 4.7, "text": "…", "scene": null, "chapter": null,
            "red": [], "marks": {"hit": 9.1}, "shakes": [17.6], "chars": [[开口, 收尾], …],
            "items": [{"el": "C01", "kind": "image", "src": "build/assets/el/C01.png", "x": 540, "y": 1380,
                       "w": 640, "h": 960, "appear": 2.0, "end": 4.7, "preset": "slide_up", "grow": null,
                       "anim": {"dur": 0.43, "frames": [[dx, dy, sx, sy, rot, alpha, blur], …]},
                       "idle": "float", "track": 1, "volume": 0, "clip_in": 0, "placeholder": false}]}],
 "captions": [{"start": 2.0, "end": 4.6, "lines": [[{"ch": "天", "t": 2.3, "red": false}]]}],
 "subtitles": [{"start": 2.0, "end": 4.6, "text": "…"}], "srt": "build/subs.srt",
 "cues": [{"t": 2.1, "name": "whoosh", "gain": 1, "source": "S01/C01"}]}
```
坐标为像素、y 向下，元素 `x/y` 是中心点。`anim.frames` 按 fps 逐帧采样（相对落定位置的偏移），Canvas 引擎逐帧插值，剪映后端逐帧渲成透明 MOV。

## 入场预设

`pop` `elastic` `slam` `whip_left` `whip_right` `swing` `spin_pop` `rise_bounce` `stamp` `grow` `slide_left` `slide_right`
`slide_up` `slide_down` `drop` `zoom_slam` `fade_up` `blur_in` `fade` `cut`，以及 `slide_auto`（按位置自动选方向）。

## 音效库（全部 numpy 合成）

`pop` `pop_soft` `boing` `thud` `whoosh` `swish` `swing` `ding` `scribble` `strike` `stamp` `counter` `tick` `flip` `head`
`boom` `impact` `riser` `glitch` `shimmer` `typing` `error` `success` `paper` `cascade`
（冲击类 `thud` `stamp` `impact` `boom` `strike` 按峰值对齐事件时刻）

## 剪映草稿（editable 模式）里的对象

| timeline 里的东西 | 剪映草稿里 | 可以怎么改 |
|---|---|---|
| 背景 | 图片片段（轨道「背景」） | 替换素材 |
| 章节卡 | 底图 + 原生文本（PART / 序号 / 标题） | 改字、字体、颜色 |
| 图片 / 视频元素 | 素材片段 + 关键帧（位置 / 缩放 / 旋转 / 不透明度），轨道「图层N」 | 调关键帧、替换素材（保留动画） |
| 文字元素（kind: word） | 原生文本 + 关键帧，关键词是分段颜色，轨道「文字N」 | 改字、字体、颜色、动画 |
| 顶部逐字标题 | 原生文本 + 「逐字显影」入场动画，关键词标红，轨道「标题」 | 同上 |
| 底部字幕 / 栏目条 / 章节角标 | 原生文本 | 批量改样式 |
| 自定义代码画面 | 渲染好的视频片段（只含代码画面），轨道「代码画面」 | 替换 / 删除 |
| 配音 / 音效 / 配乐 | 独立音频片段（每条音效一个） | 挪位置、换音效、调音量 |

风格里可选 `jianying: {font: 得意黑, caption_font: …, subtitle_font: …}` 指定剪映字体库里的字体（名字与 pyJianYingDraft 的 FontType 一致；不写用剪映默认字体）。
字号按「像素 ÷ (画布短边 × 0.006)」换算。生成后附 `build/jianying/draft_preview.jpg`：按剪映规则合成的每镜画面，用于不开剪映的自检。

## CLI JSON 输出

每条命令加 `--json`：stdout 只有一行 `{"ok": true, "command": "timeline", "result": {...}}`；失败为
`{"ok": false, "command": ..., "error": "人能读懂的原因"}`，退出码非 0。进度信息只写 stderr。
需要用户介入时带 `error_code`（退出码 3），目前有：`need_image_api`（出图这一步没接入出图 API，`error` 里是给用户看的填写说明）。
