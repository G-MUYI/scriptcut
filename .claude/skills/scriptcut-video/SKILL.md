---
name: scriptcut-video
description: 把一份口播文案（txt/md/docx 或直接粘贴）自动做成可编辑的剪映草稿（也可出 MP4 成片）：拆分镜 → 出图（用户自己的出图 API）→ 配音 → 逐字对齐 → 音效配乐 → 剪映草稿 → 交付。用户说「把这篇文案做成视频」「做个科普 / 解说 / 口播视频」「生成剪映草稿」「自动剪辑」时使用。
---

# ScriptCut 自动剪辑总控

## 两种调用方式（看你有哪种能力）
- **能执行命令**（WorkBuddy、CodeBuddy、Trae、通义灵码、豆包「本地电脑」模式、Claude Code、Codex、Cursor……）：
  运行 `scriptcut <命令> <项目目录> --json`（没安装成包时：`python -m scriptcut ...`，需在仓库目录且 `PYTHONPATH=src`）。
  stdout 是一行 JSON：`ok` / `result` / `error_code` / `error`。
- **只能调工具**（豆包自定义连接器、扣子、Dify 等通过 MCP 接入）：用同名的 `scriptcut_*` 工具
  （`scriptcut_init`、`scriptcut_check`、`scriptcut_gen`……）。没有文件权限时：
  用 `scriptcut_write_file` 写 storyboard.yaml、`scriptcut_read_file` 读文件、`scriptcut_put_asset` 放图片、`scriptcut_view_image` 看预览图。

下文写的是命令；工具版把 `scriptcut gen <项目>` 换成 `scriptcut_gen(project=<项目>)` 即可。

## 核心规则（违反就会返工）
1. **文案一字不改**：`input/script.txt` 是唯一文字来源；改分镜，不改文案。数字读法只在 TTS 里转换，上屏不变。
2. **配音是主时钟**：换镜点、元素入场、标题逐字、音效全部挂在「口播的某个字」上。
3. **出图用用户自己的 API**：到出图这一步必须先提醒用户接入（见第 3 步），不要擅自用占位图跑完全程。
4. **交付物默认是可编辑的剪映草稿**：图片、文字、标题、字幕都是剪映原生对象，动画是关键帧，音效逐条分轨。
5. 密钥只放 `.env`，不要写进 yaml、代码或回复里。

## 0. 环境
`scriptcut doctor --json`：缺 ffmpeg / 字体 / pyJianYingDraft 就按 `hints` 处理。`image_api` 为空说明还没接出图 API（第 3 步会处理）。

## 1. 建项目
```bash
scriptcut init <项目目录> --script <文案文件或文字> --style <风格> --title <标题> --json
```
| 风格 | 画幅 | 适合 |
|---|---|---|
| `whiteboard` | 9:16 | 知识科普：白底手绘素材 + 顶部逐字标题（默认） |
| `explainer` | 9:16 | 新闻 / 财经 / 时政解说：点阵底 + 底部字幕 + 章节卡 + 栏目条 + 轻配乐 |
| `dark-hud` | 16:9 | 品牌 / 科技短片：深色网格，重点画面可写代码动画 |
拿不准风格、目标时长时问用户一次。中文约 4.5~5 字/秒；明显超出目标时长时先告诉用户、建议删改哪几段，**等用户确认，不要自己改文案**。

## 2. 拆分镜 → 按 `scriptcut-storyboard` 技能写 `storyboard.yaml`
循环 `scriptcut check <项目> --json` 直到 `errors` 为空；`warnings` 能改就改。
把分镜摘要（镜头数、需要 AI 出图的素材数、预估时长）发给用户确认。

## 3. 出图 —— 必须提醒用户接入自己的出图 API
1. `scriptcut image-api <项目> --json` 看是否已接入（不会显示 Key）。
2. **没接入**（或 `scriptcut gen` 返回 `error_code: need_image_api`）：**停下来，把返回的 `error` 原文转告用户**，请用户选：
   - 接入自己的出图 API：在 `.env` 填 `IMAGE_PROVIDER` + `IMAGE_API_KEY`（+ `IMAGE_MODEL`）。支持
     火山方舟 · 豆包 Seedream（`ark`）、阿里百炼 · 通义万相（`dashscope`）、硅基流动（`siliconflow`）、
     OpenAI 或任意 OpenAI 兼容接口 / 中转（`openai` + `IMAGE_API_BASE`）、自己的出图脚本（`command` + `SCRIPTCUT_IMAGE_CMD`）。
     填好后 `scriptcut image-api <项目> --test --json` 测一张，再 `scriptcut gen <项目> --json`。
   - 自己出图：按 `build/prompts.md` 的提示词出图，保存为 `assets/<编号>.png`（或发给你，用 `scriptcut_put_asset` 放进去）。
   - 先用占位图：用户**明确同意**后才 `scriptcut gen <项目> --allow-placeholders --json`；草稿里之后可在剪映「替换素材」。
   绝不把用户的 Key 写进文件以外的地方，也不要替用户去注册任何服务。
3. **已接入**：`scriptcut gen <项目> --json`，看 `failed` / `errors` / `notes`（多种点缀色、抠底边缘），必要时 `--only C01,P03` 重出。
4. 用户提供的截图 / 视频放 `materials/`，分镜里用 `src:` 引用。

## 4. 配音
`scriptcut voice <项目> --json`
- 有 `input/voice.*`（用户录音）→ 自动对齐：有 `FISH_API_KEY` 用识别对齐，否则离线静音对齐。
- 没有录音 → 逐句 TTS：有 Fish Key + 音色用 Fish，否则 edge-tts（免费）。音色 / 语速在 `project.yaml` 的 `voice.tts`。
- 识别对齐 `match_ratio` < 0.85、或静音对齐的 `skipped_sentence_ends` / `rate_outliers` 不为空 → 告诉用户配音可能和文案不是同一版。

## 5. 时间轴 → 音效配乐
```bash
scriptcut timeline <项目> --json   # 看 warnings：开头空画面、遮挡、拥挤、展示太短 → 改分镜后重跑
scriptcut cues <项目> --json       # 只有用了自定义代码画面（scene）才需要；之后再跑一次 timeline
scriptcut audio <项目> --json      # master 约 -14 LUFS；同时生成剪映草稿要用的逐条音效
```

## 6. 生成可编辑剪映草稿（默认交付物）
```bash
scriptcut jianying <项目> --json
```
- 返回 `draft`（草稿位置）、`native`（原生图片 / 文字 / 关键帧 / 音效条数）、`preview`（草稿预览图）、`notes`、`warnings`。
- **打开 `build/jianying/draft_preview.jpg` 亲自看图**（工具版：`scriptcut_view_image`）：这是按剪映规则合成的每镜画面，
  检查文字是否被裁、元素是否遮挡、每镜是否有视觉重心。有问题改 `storyboard.yaml` → timeline → jianying 重跑。
  单独看某几个时刻：`scriptcut jianying <项目> --at 3.5,12 --json`。
- 草稿目录：Windows / macOS 自动找到剪映草稿目录；找不到时生成在 `build/jianying/`，提醒用户把整个草稿文件夹拷进剪映草稿目录后重启剪映。
- **剪映打开过的草稿不能覆盖**（会被加密）：要重新生成就换 `--name`。
- 可选 MP4 成片：`scriptcut stills <项目>` 看 Canvas 总览图后 `scriptcut render <项目> --json`（需要 Playwright）。

## 7. 交付
`scriptcut deliver <项目> --json` → `out/<名字>/`。回复用户（中文）：
- 剪映草稿位置 + 打开方式；草稿里哪些可以直接改（文字、标题、字幕、关键帧动画、每条音效、配乐）
- 时长、镜头数、时间轴来源（TTS / 识别 / 估算）
- 仍是占位图的素材（剪映里选中 → 替换素材，位置和动画会保留）
- `facts` 里待核实的说法、需要合规确认的表述（收益、监管、医疗功效、「最」「第一」等绝对化用语）

## 一键与状态
- `scriptcut status <项目> --json`：每个阶段是否完成 / 过期，`next` 给出下一步。中途接手先跑它。
- `scriptcut run <项目> --json`：按顺序跑完全部阶段（默认生成剪映草稿）；没接出图 API 时会停在 gen（`stopped_at: gen`），按第 3 步处理后再跑。
- 改了什么就从哪一步往后重跑：分镜 → check → timeline → audio → jianying；文案变了才需要重新 voice。

## 不要做
- 不要为了「对上画面」改文案、删口癖、改错别字。
- 不要在用户没同意的情况下用占位图交付，也不要假装已经出图。
- 不要跳过预览图自检就说画面没问题。
- 不要覆盖用户在剪映里打开过的草稿。
