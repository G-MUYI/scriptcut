"""一键接入各家 Agent：安装 Skills（SKILL.md）+ 写入 MCP 配置，或打包给只能上传技能 / 填连接器 URL 的平台。

scriptcut install-agent <名字> [--global] [--project 目录]
  workbuddy  腾讯 WorkBuddy：skills → .workbuddy/skills（--global：~/.workbuddy/skills）；MCP → .workbuddy/mcp.json
  codebuddy  腾讯 CodeBuddy：skills → .codebuddy/skills（--global：~/.codebuddy/skills）；打印 MCP 配置片段
  trae       字节 Trae / Trae CN：skills → .trae/skills（--global：~/.trae/skills）；打印 MCP 配置片段
  lingma     阿里 通义灵码：skills → .lingma/skills（--global：~/.lingma/skills）；打印 MCP 配置片段
  qoder      阿里 Qoder：skills → .qoder/skills（--global：~/.qoder/skills）；打印 MCP 配置片段
  doubao     豆包 / 豆包工作：打包 dist/agent-skills/*.zip（在「技能」里导入）+ 打印「自定义连接器」填写方法
  coze       扣子 / Coze：打印 HTTP MCP 接入方法（与豆包连接器同一套）
  agents     通用 .agents/skills（Codex、Gemini CLI、Cursor、Copilot、Qwen Code 等）
  claude     Claude Code：.claude/skills
  all        以上全部可本地安装的目录
各平台的目录约定会变化：装完在 Agent 里问一句「你有哪些技能」确认已加载。
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

from .config import PKG, REPO
from .util import ScriptCutError

SKILLS_SRC = PKG / "skills"

TARGETS = {
    "workbuddy": {"label": "腾讯 WorkBuddy", "dir": ".workbuddy/skills", "mcp": ".workbuddy/mcp.json"},
    "codebuddy": {"label": "腾讯 CodeBuddy", "dir": ".codebuddy/skills", "mcp": None},
    "trae": {"label": "字节 Trae / Trae CN", "dir": ".trae/skills", "mcp": None},
    "lingma": {"label": "阿里 通义灵码", "dir": ".lingma/skills", "mcp": None},
    "qoder": {"label": "阿里 Qoder", "dir": ".qoder/skills", "mcp": None},
    "agents": {"label": "通用 .agents/skills（Codex / Gemini CLI / Cursor / Copilot / Qwen Code）", "dir": ".agents/skills", "mcp": None},
    "claude": {"label": "Claude Code", "dir": ".claude/skills", "mcp": None},
}


def mcp_stdio_entry() -> dict:
    """本机 stdio MCP 配置：用当前 Python 的绝对路径（WorkBuddy 等在 Windows 上要求绝对路径）。"""
    entry = {"command": sys.executable, "args": ["-m", "scriptcut.mcp_server"], "env": {"PYTHONUTF8": "1"}}
    src = PKG.parent
    if (src / "scriptcut").is_dir() and src.name == "src":  # 源码运行（未 pip install）
        entry["env"]["PYTHONPATH"] = str(src)
    return entry


def skill_names() -> list[str]:
    return sorted(p.name for p in SKILLS_SRC.iterdir() if (p / "SKILL.md").exists())


def copy_skills(dst_root: Path) -> list[str]:
    out = []
    dst_root.mkdir(parents=True, exist_ok=True)
    for name in skill_names():
        dst = dst_root / name
        if dst.resolve() == (SKILLS_SRC / name).resolve():
            continue
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(SKILLS_SRC / name, dst)
        out.append(str(dst))
    return out


def merge_mcp(path: Path) -> str:
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            raise ScriptCutError(f"{path} 不是合法 JSON，先手动修好：{e}") from e
    data.setdefault("mcpServers", {})["scriptcut"] = mcp_stdio_entry()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def pack_zips(out_dir: Path) -> list[str]:
    """每个 skill 打成一个 zip（zip 里是 <名字>/SKILL.md），给豆包等「上传技能」的平台用。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for name in skill_names():
        z = out_dir / f"{name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted((SKILLS_SRC / name).rglob("*")):
                if f.is_file():
                    zf.write(f, f"{name}/{f.relative_to(SKILLS_SRC / name).as_posix()}")
        files.append(str(z))
    return files


HTTP_GUIDE = """在本机启动 HTTP 版 MCP（口令自己设一个长一点的随机串）：
  scriptcut-mcp --http --host 0.0.0.0 --port 8765 --token <你的口令> --root <放视频项目的目录>
连接器 / MCP 填写：
  服务器名称：scriptcut
  传输类型：HTTP（Streamable HTTP）
  服务器 URL：http://<这台电脑的地址>:8765/mcp
  自定义 Headers：Authorization = Bearer <你的口令>
如果平台从云端访问、连不到你电脑的局域网地址，需要内网穿透（cpolar / 花生壳 / frp / cloudflared 任选），
把穿透得到的 https 地址 + /mcp 填进去；对外暴露时务必带 --token 和 --root。"""


def install(agent: str, project: Path | None = None, global_: bool = False) -> dict:
    agent = agent.lower()
    base = Path.home() if global_ else (Path(project) if project else Path.cwd()).resolve()
    res: dict = {"agent": agent, "installed": [], "mcp_config": None, "next": []}
    if agent in ("doubao", "coze"):
        if agent == "doubao":
            res["installed"] = pack_zips((Path(project) if project else REPO) / "dist" / "agent-skills")
            res["next"].append("豆包电脑版 / 豆包工作 →「技能」→ 新建 / 导入技能，逐个上传上面的 zip（或把 SKILL.md 内容粘贴进去）")
            res["next"].append("如果豆包处于「本地电脑」工作模式、能在你电脑上执行命令：装好技能即可，它会直接调用 scriptcut 命令")
            res["next"].append("否则用「自定义连接器」走 MCP：\n" + HTTP_GUIDE)
        else:
            res["next"].append("扣子 / Coze：在智能体或工作流里添加 MCP 服务（HTTP），填写方法：\n" + HTTP_GUIDE)
            res["next"].append("把 skills/scriptcut-video/SKILL.md 的内容放进智能体的「人设与回复逻辑」")
        res["mcp_http"] = {"mcpServers": {"scriptcut": {"type": "http", "url": "http://<地址>:8765/mcp",
                                                        "headers": {"Authorization": "Bearer <你的口令>"}}}}
        return res
    names = list(TARGETS) if agent == "all" else [agent]
    for n in names:
        t = TARGETS.get(n)
        if t is None:
            raise ScriptCutError(f"不认识的 Agent：{agent}（可选：{'、'.join(list(TARGETS) + ['doubao', 'coze', 'all'])}）")
        res["installed"] += copy_skills(base / t["dir"])
        if t["mcp"]:
            res["mcp_config"] = merge_mcp(base / t["mcp"])
    res["mcp_stdio"] = {"mcpServers": {"scriptcut": mcp_stdio_entry()}}
    if agent == "workbuddy":
        res["next"].append("彻底退出并重启 WorkBuddy →「连接器 → 自定义连接器」里找到 scriptcut，点「信任」启用（状态变绿即成功）")
    elif agent != "all":
        res["next"].append(f"在 {TARGETS[agent]['label']} 的 MCP 设置里粘贴 mcp_stdio 配置（可选：不配 MCP 时 Agent 也能按技能直接跑 scriptcut 命令）")
    res["next"].append("重启 Agent 后问一句「你有哪些技能」，确认出现 scriptcut-video / scriptcut-storyboard / scriptcut-scenes")
    return res
