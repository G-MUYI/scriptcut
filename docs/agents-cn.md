# 接入国内 Agent（豆包 / WorkBuddy / CodeBuddy / Trae / 通义灵码 / 扣子……）

ScriptCut 给 Agent 提供三层接口，国内平台按能力选：

| 平台 | 推荐接法 | 一键命令 |
|---|---|---|
| **腾讯 WorkBuddy** | 技能（SKILL.md）+ 本机 MCP（stdio） | `scriptcut install-agent workbuddy`（项目级）或 `--global` |
| **腾讯 CodeBuddy** | 技能 + 命令行（可选 MCP） | `scriptcut install-agent codebuddy` |
| **豆包 / 豆包工作**（电脑版、App、飞书内） | 技能（上传 zip）+「本地电脑」模式跑命令；或「自定义连接器」走 HTTP MCP | `scriptcut install-agent doubao` |
| **字节 Trae / Trae CN** | 技能 + 命令行（可选 MCP） | `scriptcut install-agent trae` |
| **阿里 通义灵码 / Qoder** | 技能 + 命令行（可选 MCP） | `scriptcut install-agent lingma` / `qoder` |
| **扣子 Coze / 飞书 Aily / Dify** | HTTP MCP（填 URL） | `scriptcut install-agent coze`（打印填写方法） |
| **Kimi CLI / Qwen Code 等命令行 Agent** | 通用 `.agents/skills` + 命令行，或 stdio MCP | `scriptcut install-agent agents` |

仓库里已经放好了 `.agents/skills/`、`.workbuddy/skills/`、`.codebuddy/skills/`、`.claude/skills/`——
**直接用这些 Agent 打开本仓库目录就能用**；要在别的目录 / 全局用，再跑 `install-agent`。

> 各平台的技能目录约定还在快速变化（Trae、灵码、Qoder 的路径来自社区文章）。装完重启 Agent，问一句「你有哪些技能」，
> 能看到 `scriptcut-video`、`scriptcut-storyboard`、`scriptcut-scenes` 就说明加载成功；看不到就在平台的「技能 → 导入」里手动导入
> `dist/agent-skills/*.zip`（`scriptcut install-agent doubao` 会生成这些 zip，任何平台都能用）。

先装好 ScriptCut（Windows 建议用 Python 3.11+，装完重开终端）：
```bash
pip install -e ".[all]"
scriptcut doctor        # 检查 ffmpeg / 字体 / 剪映库 / 出图 API
```

---

## 腾讯 WorkBuddy

```bash
scriptcut install-agent workbuddy --project <你的工作目录>   # 或 --global 装到 ~/.workbuddy
```
它会：
1. 复制三个技能到 `<目录>/.workbuddy/skills/`（全局：`~/.workbuddy/skills/`，Windows：`%USERPROFILE%\.workbuddy\skills\`）；
2. 把 `scriptcut` 写进 `<目录>/.workbuddy/mcp.json`（全局：`~/.workbuddy/mcp.json`）。命令用的是**当前 Python 的绝对路径**——
   WorkBuddy 在 Windows 上要求绝对路径；文件名是 `mcp.json`，不是 `.mcp.json`。

然后**彻底退出并重启 WorkBuddy** →「连接器 → 自定义连接器」里找到 scriptcut → 点「信任」，状态变绿即可。
对它说：「用 scriptcut 把 D:\文案\通胀.docx 做成竖屏科普视频，生成剪映草稿」。

## 腾讯 CodeBuddy

```bash
scriptcut install-agent codebuddy --project <项目目录>   # → .codebuddy/skills/（--global → ~/.codebuddy/skills/）
```
CodeBuddy 能直接执行命令，装技能就够了；想用 MCP，在 CodeBuddy 的 MCP 设置里粘贴命令输出的 `mcp_stdio` 配置。

## 豆包 / 豆包工作

```bash
scriptcut install-agent doubao      # 生成 dist/agent-skills/scriptcut-*.zip，并打印连接器填写方法
```

**方式 A：技能 +「本地电脑」模式（推荐）**
1. 豆包电脑版 →「技能」→ 新建 / 导入技能，逐个上传 `dist/agent-skills/` 下的三个 zip（或把 SKILL.md 内容粘贴进去）。
2. 进入「工作任务」模式、选「本地电脑」，让它在你的电脑上执行 `scriptcut` 命令。

**方式 B：自定义连接器（HTTP MCP）**——豆包不能执行本地命令时用
1. 在电脑上启动 HTTP 版 MCP（口令自己设一个长随机串）：
   ```bash
   scriptcut serve --host 0.0.0.0 --port 8765 --token <你的口令> --root D:/videos
   ```
   `--root` 限定它只能访问这个目录下的项目。
2. 豆包 →「技能 · 连接器 · 伙伴」→ 新建 → 新建自定义连接器：
   - 服务器名称：`scriptcut`
   - 传输类型：HTTP
   - 服务器 URL：`http://<电脑地址>:8765/mcp`
   - 自定义 Headers：`Authorization` = `Bearer <你的口令>`
3. 豆包的连接器如果从云端访问、连不到你电脑的局域网地址，需要**内网穿透**（cpolar / 花生壳 / frp / cloudflared 任选），
   把穿透得到的 `https://xxx` 加上 `/mcp` 填进去。对外暴露时一定要带 `--token` 和 `--root`。

连接器模式下豆包不能直接读写你电脑上的文件，所以 MCP 额外提供：
`scriptcut_write_file`（写分镜）、`scriptcut_read_file`（读提示词 / 报告）、`scriptcut_put_asset`（把豆包生成或你发来的图放进项目）、
`scriptcut_view_image`（让它看草稿预览图自检）。成片和剪映草稿仍然生成在你自己的电脑上。

## 字节 Trae / 阿里 通义灵码 / Qoder

```bash
scriptcut install-agent trae     # → .trae/skills/
scriptcut install-agent lingma   # → .lingma/skills/
scriptcut install-agent qoder    # → .qoder/skills/
```
加 `--global` 装到用户目录。这几个 IDE 都能执行命令，装技能即可；MCP 可选（在 IDE 的 MCP 设置里粘贴 `mcp_stdio` 配置）。
如果 IDE 不认技能目录，就在「技能 / 规则」里导入 `dist/agent-skills/*.zip`，或把 `AGENTS.md` 加为项目规则。

## 扣子 Coze / 飞书 Aily / Dify

这些平台在云端运行，只能通过 URL 调用：按上面「豆包方式 B」启动 `scriptcut serve` + 内网穿透，在平台里添加 MCP 服务（HTTP）。
把 `src/scriptcut/skills/scriptcut-video/SKILL.md` 的内容放进智能体的人设 / 系统提示词。

---

## 出图：一定要接你自己的出图 API

ScriptCut 不内置、不代付出图服务。Agent 走到出图这一步时，如果没检测到你的出图 API，会**停下来提醒你**（`error_code: need_image_api`），
不会偷偷用占位图跑完。在 `.env` 里填一种：

```ini
# 火山方舟 · 豆包 Seedream
IMAGE_PROVIDER=ark
IMAGE_API_KEY=你的方舟 API Key
IMAGE_MODEL=doubao-seedream-4-0-250828     # 以控制台里的模型 ID / 接入点为准

# 阿里云百炼 · 通义万相
# IMAGE_PROVIDER=dashscope
# IMAGE_API_KEY=sk-xxx
# IMAGE_MODEL=wan2.2-t2i-flash

# 硅基流动
# IMAGE_PROVIDER=siliconflow
# IMAGE_API_KEY=sk-xxx
# IMAGE_MODEL=Kwai-Kolors/Kolors

# OpenAI 或任意 OpenAI 兼容接口 / 中转
# IMAGE_PROVIDER=openai
# IMAGE_API_BASE=https://你的接口/v1
# IMAGE_API_KEY=xxx
# IMAGE_MODEL=模型名

# 自己的出图脚本（LibTV CLI、ComfyUI 脚本……）
# IMAGE_PROVIDER=command
# SCRIPTCUT_IMAGE_CMD=python my_gen.py --prompt-file {prompt_file} --out {out}
```
填好后：`scriptcut image-api <项目> --test` 出一张测试图确认可用。
不想接 API：按 `build/prompts.md` 的提示词在即梦 / 豆包 / 其他工具里出图，存成 `assets/<编号>.png`；
或明确告诉 Agent「先用占位图」，之后在剪映里对占位片段「替换素材」（位置和动画会保留）。

## 剪映草稿：可编辑

默认交付物就是可编辑的剪映草稿（`scriptcut jianying <项目>`）：
- 图片元素是原始素材片段，入场动画、落定后的缓慢放大 / 上下浮动、震屏都是**关键帧**，可在剪映里调整或删除；「替换素材」保留位置和动画；
- 文字元素、顶部逐字标题（剪映「逐字显影」动画，关键词标红）、底部字幕、章节卡文字、栏目条都是**剪映原生文本**，可直接改字、改字体、改颜色；
- 配音、每一条音效、配乐都是独立音频片段；
- 只有自定义代码画面（`scene:`）是渲染好的视频片段，它上面的元素和文字依然可编辑。

生成后会附一张按剪映规则合成的预览图 `build/jianying/draft_preview.jpg`，Agent 会先看图自检再交付。
草稿目录：Windows / macOS 自动找到剪映草稿目录（含在剪映里改过的自定义位置）；也可以 `--draft-dir` 指定，
或在别的电脑上生成后把整个草稿文件夹拷进剪映草稿目录、重启剪映。**剪映打开过的草稿不会被覆盖**，重新生成请换 `--name`。
