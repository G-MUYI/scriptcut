# AGENTS.md · ScriptCut

给 AI Agent（WorkBuddy、CodeBuddy、Trae、通义灵码、豆包、Codex、Cursor、Gemini CLI、Copilot、Claude Code……）的项目说明。人类读者请看 README.md。

## 这个仓库是什么
文案驱动的自动剪辑工作流：口播文案 → 分镜 `storyboard.yaml` → 配音（TTS 或录音）→ 逐字对齐 → 主时间轴
→ 素材 / 音效 / 配乐 → **可编辑剪映草稿**（默认）或 **Canvas 代码出片（MP4）** → 交付。全部阶段都是 CLI 命令，带 `--json` 机器可读输出。

## 怎么用（做视频）
1. 读 `scriptcut-video` 技能（总控流程；仓库里在 `.agents/skills/`、`.workbuddy/skills/`、`.codebuddy/skills/`、`.claude/skills/`，权威版本 `src/scriptcut/skills/`），拆分镜读 `scriptcut-storyboard`，写代码画面读 `scriptcut-scenes`。
2. 命令：`scriptcut <命令> <项目目录> --json`（未安装时：`PYTHONPATH=src python -m scriptcut ...`）。
   `doctor` `styles` `schema` `init` `check` `image-api` `prompts` `gen` `voice` `timeline` `cues` `audio` `stills` `render` `jianying` `sheet` `deliver` `status` `run` `preview` `install-agent` `serve`
3. 中途接手：`scriptcut status <项目> --json` 看每个阶段的状态和 `next`。
4. 也可以通过 MCP 调用：`scriptcut-mcp`（stdio）或 `scriptcut serve`（HTTP，豆包连接器 / 扣子），工具名 `scriptcut_*`，见 `docs/agents-cn.md`、`docs/agents.md`。

## 不可违反的规则
- `input/script.txt` 一字不改（含标点、错别字）；所有镜头 `text` 拼接必须与它逐字一致。
- 画面事件挂在口播的字上（`trigger` / `S.kw('词')`），不写死秒数。
- 自定义画面（`scenes/*.js`）必须是时间的纯函数：不用 `Math.random()` / `Date.now()`，随机用 `api.rng(种子)`。
- 出图用用户自己的出图 API：`gen` 返回 `error_code: need_image_api` 时停下，把 `error` 原文转告用户；用户明确同意才用 `--allow-placeholders`。
- 交付前亲自看预览图（剪映草稿：`build/jianying/draft_preview.jpg`；MP4：`stills` 总览图）。
- 密钥只在 `.env`（已 gitignore）；不要写进代码、yaml、日志、提交。
- 不覆盖剪映里打开过的草稿（有 `Timelines/` 目录），换 `--name`。

## 开发（改这个仓库本身）
- Python ≥ 3.10；`pip install -e ".[all,dev]"`；`playwright install chromium`；需要 ffmpeg。
- 测试：`python -m pytest -q`（缺 Chromium / pyJianYingDraft 的用例自动跳过）。改了 Canvas 引擎或剪映后端必须跑全量。
- 代码结构：
  - `src/scriptcut/project.py` 项目模型与校验 · `timing.py` 逐字时间 · `voice.py` TTS / 对齐 · `align/` 静音对齐 + 识别匹配
  - `timeline.py` 主时间轴（版式、入场、标题、字幕、音效提示点）· `assets.py` 提示词 / 出图 / 文字渲染 / 占位图
  - `audio/` 音效库、配乐、混音 · `render/` Canvas 引擎（`web/engine.js`、`web/api.js`）与 Playwright 出片
  - `imagegen.py` 出图 API（ark / dashscope / siliconflow / openai / command）· `jianying/` 可编辑剪映草稿（`builder.py`）与草稿预览（`preview.py`）
  - `pipeline.py` 各阶段 API · `cli.py` 命令行 · `mcp_server.py` MCP（stdio / HTTP）· `agents.py` 各家 Agent 安装
  - `styles/*.yaml` 内置风格 · `skills/` Agent Skills 权威版本（改完运行 `python scripts/sync_skills.py` 同步到仓库里各 Agent 目录）
- 数据契约（timing.json / timeline.json / storyboard.yaml 字段）见 `docs/schema.md`；改字段要同步更新文档和两个渲染后端。
- 代码注释、文档、CLI 输出用中文。
