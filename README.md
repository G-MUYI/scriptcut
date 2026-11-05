# ScriptCut · 文案驱动的自动剪辑工作流

> 一份口播文案进去，一份**可编辑的剪映草稿**出来：配好音、对好节奏、元素随口播入场、带音效配乐，打开剪映就能继续精修（也可以直接出 MP4）。
> 为 AI Agent 设计：**豆包、WorkBuddy、CodeBuddy、Trae、通义灵码、扣子**等国内 Agent，以及 Claude Code、Codex、Cursor、Gemini CLI 和任何 MCP 客户端都能调用。

![三段真实视频截图示例](docs/images/video-screenshots-sheet.jpg)
<sub>主示例 `examples/video-screenshots`：从“识别真正的合规平台”上、下篇及 `11.3.mp4` 各提取 3 张截图，配合介绍文案、字幕与音效配乐，生成可编辑剪映草稿。截图已随仓库提供，无需出图 API。</sub>

## 它做什么

```
文案 ──▶ 分镜 storyboard.yaml ──▶ 配音（TTS 或录音）──▶ 逐字对齐 ──▶ 主时间轴
                 ▲                                                      │
           Agent 按 Skill 拆                     ┌──────────────────────┼───────────────────────┐
                                                 ▼                      ▼                       ▼
                                     素材：AI 出图 / 截图 / 视频    音效 + 配乐（代码合成）   自定义 Canvas 画面
                                                 └──────────────────────┼───────────────────────┘
                                                         ┌──────────────┴──────────────┐
                                                         ▼                             ▼
                                         可编辑剪映草稿（默认，带预览图）      Canvas 逐帧出片 → MP4（可选）
```

三条贯穿始终的规则：
1. **文案一字不改**——所有镜头文字拼起来必须与原稿逐字一致，数字读法只在 TTS 里转换。
2. **配音是主时钟**——每个元素在「念到某几个字」时入场，换镜点跟着下一句开口走，镜头多少不改变总时长。
3. **先看图再交付**——每镜落定画面拼成预览图（剪映草稿也有按剪映规则合成的预览图），Agent 自己看图修版式。
4. **出图用你自己的 API**——走到出图这一步会停下来提醒你接入（豆包 Seedream / 通义万相 / 硅基流动 / OpenAI 兼容 / 自己的脚本），不会偷偷用占位图交付。

## 特性

- **三种配音来源，同一种逐字时间轴**：逐句 TTS（edge-tts 免费 / Fish Audio）、Fish 识别对齐录音、离线静音对齐（ffmpeg + 动态规划，多组阈值自动择优）。
- **20 种 MG 入场预设**（过冲、挤压、甩入、盖章、生长……）+ 落定后的缓慢放大 / 上下浮动 + 震屏，两种后端效果一致。
- **顶部逐字标题**（念到哪个字亮到哪个字，关键词标红）/ **底部描边字幕** / **章节卡** / **常驻栏目条**。
- **25 种程序化音效 + 程序化配乐**，冲击类按波形峰值对齐，配乐自动避让人声，两遍 loudnorm 到 -14 LUFS。
- **自定义 Canvas 画面**：图表、粒子、地图、品牌片头——写一个 `draw(ctx, S, api)` 纯函数，动作挂在 `S.kw('词')` 上。
- **自检**：分镜校验（逐字覆盖、触发词、版式、预设）、遮挡 / 拥挤 / 开头空画面警告、静帧总览、事实核查清单。
- **可编辑剪映草稿**：图片元素是原始素材 + 关键帧动画（「替换素材」保留位置和动画）；文字元素、逐字标题、字幕、章节卡、栏目条都是剪映原生文本；配音、每条音效、配乐独立分轨。
- **国内外 Agent 通用**：每个命令 `--json`、幂等可重跑、`status` 给出下一步；3 个 Agent Skills、`AGENTS.md`、MCP Server（stdio + 带口令的 HTTP，豆包「自定义连接器」可直接填 URL）、`install-agent` 一键安装。

## 快速开始

```bash
git clone <本仓库> scriptcut && cd scriptcut
pip install -e ".[all]"            # 只要核心：pip install -e . ；按需 .[render] .[jianying] .[tts] .[mcp]
playwright install chromium        # MP4 出片 / 代码画面需要（Linux 另执行 playwright install-deps）
scriptcut doctor                   # 检查 ffmpeg / 浏览器 / 中文字体 / 剪映库 / 出图 API

# 跑通主示例（真实视频截图，不需要任何 Key）
scriptcut run examples/video-screenshots           # check → gen → voice → timeline → cues → audio → 剪映草稿 → deliver
# 剪映草稿：Windows/macOS 自动放进剪映草稿目录（其他系统在 build/jianying/）；预览图 build/jianying/draft_preview.jpg
scriptcut render examples/video-screenshots        # 可选：输出 MP4 成片
# 交付包在 examples/video-screenshots/out/ 下；截图来源与重新提取方法见该示例 README
```

### 做自己的视频

**交给 Agent（推荐）**：用 WorkBuddy / CodeBuddy / Trae / 通义灵码 / Claude Code / Codex / Cursor 打开本仓库（技能已放在各自目录），
或用 `scriptcut install-agent <名字>` 装到别处 / 豆包（见 [docs/agents-cn.md](docs/agents-cn.md)），然后说：

> 用 scriptcut 把 ~/文案/通胀.docx 做成 9:16 科普视频，生成可编辑的剪映草稿，先给我看分镜摘要。

Agent 会按 `scriptcut-video` 技能：建项目 → 拆分镜 → 校验 → **提醒你接入出图 API** 并出图 → 配音 → 生成剪映草稿 → 看预览图修版式 → 交付说明。

**手动**：
```bash
scriptcut init projects/inflation --script ~/文案/通胀.docx --style whiteboard
# 编辑 projects/inflation/storyboard.yaml（格式见 docs/schema.md 和 scriptcut-storyboard skill）
scriptcut check    projects/inflation
scriptcut image-api projects/inflation --test   # 确认你的出图 API 可用（.env 里配置，见下文）
scriptcut gen      projects/inflation      # 出图 → assets/<编号>.png（没接 API 会停下提醒）
scriptcut voice    projects/inflation      # 有 input/voice.mp3 就对齐录音，否则 TTS
scriptcut timeline projects/inflation
scriptcut audio    projects/inflation
scriptcut jianying projects/inflation      # 可编辑剪映草稿 + build/jianying/draft_preview.jpg
scriptcut render   projects/inflation      # 可选：MP4 成片
scriptcut deliver  projects/inflation
```

## 内置风格

| 风格 | 画幅 | 画面 | 来源原型 |
|---|---|---|---|
| `whiteboard` | 9:16 | 白底手绘条漫素材、顶部逐字标题、元素随口播入场、无配乐 | 知识科普 |
| `explainer` | 9:16 | 浅色点阵底、扁平图标与截图、底部描边字幕、深色章节卡、栏目条、轻配乐 | 时政科普解说 |
| `dark-hud` | 16:9 | 深色网格、发光强调色、版式化大字、紧张配乐，配合自定义 Canvas 画面 | 品牌广告片 |

自定义风格：复制 `src/scriptcut/styles/*.yaml` 到项目里改，`style: ./my-style.yaml`（可写 `extends: whiteboard` 只覆盖差异）。

## 命令一览

| 命令 | 作用 | 产物 |
|---|---|---|
| `doctor` / `styles` / `schema` | 环境检查 / 风格列表 / 预设与音效速查 | — |
| `init` | 新建项目 | `project.yaml` `storyboard.yaml` `input/script.txt` |
| `check` | 校验分镜 + 预估时长 | `build/report/check.json` |
| `image-api` | 出图 API 状态与填写方法 / `--test` 出测试图 | — |
| `prompts` / `gen` | AI 出图提示词 / 用你的出图 API 出图（没接会提醒） | `build/prompts.md` `assets/*.png` |
| `voice` | TTS 或录音对齐 | `build/voice/voice.wav` `build/timing.json` |
| `timeline` | 主时间轴 + 字幕 | `build/timeline.json` `build/subs.srt` |
| `cues` | 采集自定义画面的音效点 | `build/scene_cues.json` |
| `audio` | 混音与响度 | `build/audio/master.wav` + 分轨 |
| `stills` | 静帧总览自检 | `build/stills/*.jpg` |
| `render` | Canvas 并行出片 | `out/<名字>.mp4` |
| `jianying` | 可编辑剪映草稿 + 预览图（`--at` 看指定时刻） | 剪映草稿目录 / `build/jianying/` |
| `sheet` | 分镜审阅表 | `build/分镜表.xlsx` |
| `deliver` | 交付包 + 交付说明 | `out/<名字>/` |
| `status` / `run` / `preview` | 状态与下一步 / 一键 / 浏览器拖动预览 | — |
| `install-agent` / `serve` | 给各家 Agent 装技能 + MCP / 启动 HTTP 版 MCP | — |

所有命令都支持 `--json`（stdout 一行 JSON，进度在 stderr）。

## 外部服务

| 用途 | 默认（免费） | 你可以接入 | 配置（`.env`） |
|---|---|---|---|
| **AI 出图** | 无（需你接入；或人工出图 / 明确选择占位图） | 火山方舟 · 豆包 Seedream、阿里百炼 · 通义万相、硅基流动、OpenAI / 兼容接口、自己的出图命令 | `IMAGE_PROVIDER` `IMAGE_API_KEY` `IMAGE_MODEL`（`IMAGE_API_BASE` / `SCRIPTCUT_IMAGE_CMD`） |
| 配音 | edge-tts | Fish Audio（音色克隆、ASR 回听校对） | `FISH_API_KEY` `FISH_VOICE_ID` |
| 录音对齐 | 离线静音对齐 | Fish Audio 语音识别 | 同上 |
| 剪辑精修 | — | 剪映专业版（Windows / macOS） | `JIANYING_DRAFT_DIR`（可自动识别） |

出图示例（火山方舟 · 豆包 Seedream）：
```ini
IMAGE_PROVIDER=ark
IMAGE_API_KEY=你的方舟 API Key
IMAGE_MODEL=doubao-seedream-4-0-250828   # 以控制台里的模型 ID / 接入点为准
```

复制 `.env.example` 为 `.env` 填写；`.env` 已在 `.gitignore` 中。

## 接入 Agent

| Agent | 一键 |
|---|---|
| 腾讯 WorkBuddy | `scriptcut install-agent workbuddy [--global]`（技能 + `.workbuddy/mcp.json`） |
| 腾讯 CodeBuddy / 字节 Trae / 阿里 通义灵码 / Qoder | `scriptcut install-agent codebuddy`（或 `trae` / `lingma` / `qoder`，加 `--global` 装到用户目录） |
| 豆包 / 豆包工作 | `scriptcut install-agent doubao`（技能 zip）+ 「本地电脑」模式；或 `scriptcut serve --token …` 走「自定义连接器」 |
| 扣子 / 飞书 Aily / Dify | `scriptcut serve --host 0.0.0.0 --token … --root …`，平台里填 `http://…/mcp` |
| Codex / Gemini CLI / Cursor / Copilot / Qwen Code | 仓库自带 `.agents/skills/`；`install-agent agents --global` |
| Claude Code | 仓库自带 `.claude/skills/` + `CLAUDE.md` |

- **Skills**（权威版本在 `src/scriptcut/skills/`）：`scriptcut-video`（总控）、`scriptcut-storyboard`（拆分镜）、`scriptcut-scenes`（写代码画面）。
- **MCP**：`scriptcut-mcp`（stdio）/ `scriptcut serve`（Streamable HTTP + Bearer 口令 + 工作目录限制），20+ 个 `scriptcut_*` 工具，
  含远程 Agent 用的写分镜 / 读文件 / 上传素材 / 看预览图。
- 详细步骤：[docs/agents-cn.md](docs/agents-cn.md)（国内）、[docs/agents.md](docs/agents.md)（海外 / 工作流引擎）。

## 文档

- [docs/schema.md](docs/schema.md) — 项目目录、`project.yaml`、`storyboard.yaml`、`timing.json`、`timeline.json` 字段
- [docs/agents-cn.md](docs/agents-cn.md) — 豆包 / WorkBuddy / CodeBuddy / Trae / 通义灵码 / 扣子接入，出图 API，剪映草稿说明
- [docs/agents.md](docs/agents.md) — Claude Code / Codex / Gemini CLI / Cursor / MCP 客户端 / 工作流引擎接入
- [docs/design.md](docs/design.md) — 架构取舍，以及从四个原型里沉淀下来的经验与踩坑
- [examples/video-screenshots](examples/video-screenshots) — 主示例：三段真实视频的截图、介绍文案与分镜（横竖屏混排）
- [examples/sky-blue](examples/sky-blue) — 补充示例：天空科普（含两个自定义 Canvas 画面）

## 开发

```bash
pip install -e ".[all,dev]" && playwright install chromium
python -m pytest -q          # 缺 Chromium / pyJianYingDraft 的用例自动跳过
```

## 许可与致谢

代码以 MIT 许可发布。离线对齐算法、MG 入场预设、Canvas 引擎等来自作者此前的几个自动剪辑原型（见 docs/design.md）。
依赖：[pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)（剪映草稿）、[Playwright](https://playwright.dev)、
[edge-tts](https://github.com/rany2/edge-tts)、FFmpeg。字体请使用可商用授权字体（如思源黑体 / Noto Sans CJK，OFL）。
**使用者需自行确保文案、素材、配音音色与生成内容的版权和合规**；涉及金融、医疗等内容时请在发布前做合规审核。
