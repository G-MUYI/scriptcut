# 接入各家 Agent

> 国内 Agent（豆包、WorkBuddy、CodeBuddy、Trae、通义灵码、扣子）请看 [agents-cn.md](agents-cn.md)。

ScriptCut 提供三层接口，任选其一：

| 接口 | 适合 | 入口 |
|---|---|---|
| **Agent Skills**（SKILL.md） | 能读写文件、跑命令的编程 Agent | `.agents/skills/`（通用）· `.claude/skills/`（Claude Code） |
| **CLI + `--json`** | 任何能执行 shell 的 Agent / 工作流引擎（n8n、Dify、Coze 自建插件、GitHub Actions） | `scriptcut <命令> <项目> --json` |
| **MCP Server** | 支持 MCP 的客户端（含不能跑 shell 的桌面端 / 云端平台） | `scriptcut-mcp`（stdio）· `scriptcut serve`（HTTP + 口令） |

先安装：
```bash
pip install -e ".[all]"          # 或按需：.[render] .[jianying] .[tts] .[mcp]
playwright install chromium      # Canvas 出片需要；Linux 再执行 playwright install-deps
scriptcut doctor                 # 检查 ffmpeg / 浏览器 / 字体 / Key
```

## Claude Code
- 在仓库里直接打开即可：`CLAUDE.md` → `AGENTS.md`，Skills 在 `.claude/skills/`，输入 `/scriptcut-video` 或直接说「把 xx.docx 做成视频」。
- MCP（可选）：`claude mcp add scriptcut -- scriptcut-mcp`，或把 `docs/mcp.example.json` 复制成 `.mcp.json`。
- 在其他目录使用：`scriptcut install-agent claude --global`（`~/.claude/skills`）、`scriptcut install-agent agents --global`（`~/.agents/skills`）。

## OpenAI Codex（CLI / IDE）
- 读 `AGENTS.md` 和 `.agents/skills/`（项目级）；全局安装：`scriptcut install-agent agents --global`。
- MCP：`~/.codex/config.toml`
  ```toml
  [mcp_servers.scriptcut]
  command = "scriptcut-mcp"
  ```

## Gemini CLI
- 读 `.agents/skills/`（也读 `.gemini/skills/`）和 `AGENTS.md`（可在 `settings.json` 的 `context.fileName` 加上 `AGENTS.md`）。
- MCP：`~/.gemini/settings.json`
  ```json
  {"mcpServers": {"scriptcut": {"command": "scriptcut-mcp"}}}
  ```

## Cursor
- 2.4 起原生支持 Agent Skills：读 `.agents/skills/`、`.cursor/skills/`，兼容 `.claude/skills/`。`AGENTS.md` 作为项目规则。
- MCP：`.cursor/mcp.json`，内容同 `docs/mcp.example.json`（安装成包后 command 可直接写 `scriptcut-mcp`）。

## GitHub Copilot（VS Code Agent 模式）/ Cline / Roo / Trae / Windsurf / OpenCode
- Skills：读 `.agents/skills/` 的直接可用；不支持 Skills 的把 `AGENTS.md` 和 `scriptcut-video/SKILL.md` 加进规则 / 上下文。
- MCP：各自的 MCP 配置里加 `{"command": "scriptcut-mcp"}`。

## Claude Desktop / 其他只支持 MCP 的客户端
```json
{"mcpServers": {"scriptcut": {"command": "scriptcut-mcp", "env": {"PYTHONUTF8": "1"}}}}
```
（没装成包时：`"command": "python", "args": ["-m", "scriptcut.mcp_server"], "env": {"PYTHONPATH": "<仓库>/src"}`）
提示词示例：「用 scriptcut 把 D:/文案/通胀.docx 做成 9:16 科普视频，项目放在 D:/videos/inflation，先拆分镜给我看。」
MCP 模式下分镜由 Agent 直接写 `storyboard.yaml` 文件（需要客户端有文件写入工具），或先 `scriptcut_init` 再让用户确认。

## 工作流引擎（n8n / Dify / 自建后端）
每个阶段是幂等的命令，可以拆成节点：
```bash
scriptcut init  $DIR --script "$TEXT" --style explainer --json
# 由 LLM 节点生成 storyboard.yaml（把 scriptcut-storyboard/SKILL.md 作为系统提示词）
scriptcut check $DIR --json      # errors 非空 → 回给 LLM 修
scriptcut run   $DIR --json            # 默认生成可编辑剪映草稿；--backend both 同时出 MP4
# 返回 error_code=need_image_api 时：通知用户接入出图 API，处理后重试
```
`status` 的 `next` 字段可直接驱动重试 / 分支。

## 给 Agent 的提示词模板
```
你是视频剪辑助理，使用 ScriptCut（规则见 AGENTS.md 与 scriptcut-video skill）。
任务：把 <文案> 做成 <竖屏/横屏> <科普/解说/广告> 视频，风格 <whiteboard/explainer/dark-hud>，目标时长 <N> 秒。
要求：文案一字不改；先给我分镜摘要（镜头数、元素、预估时长）确认后再配音出片；出片前把总览图发给我。
出图：到出图这一步提醒我接入自己的出图 API，不要擅自用占位图。
交付：可编辑剪映草稿 + 预览图（可选 MP4）+ 交付说明（列出占位素材和待核实的说法）。
```
