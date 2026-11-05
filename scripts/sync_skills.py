"""同步 / 安装 Agent Skills。

权威版本在 src/scriptcut/skills/（随 pip 包一起发布）。本脚本把它们复制到仓库里各家 Agent 会自动读取的目录：
  .agents/skills（Codex、Gemini CLI、Cursor、Copilot、Qwen Code 等通用）· .claude/skills（Claude Code）
  .workbuddy/skills（腾讯 WorkBuddy）· .codebuddy/skills（腾讯 CodeBuddy）
Windows 上软链接不可靠，所以用复制。安装到其他项目 / 全局请用：scriptcut install-agent <名字> [--global]

用法：python scripts/sync_skills.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scriptcut.agents import copy_skills  # noqa: E402

if __name__ == "__main__":
    for d in (".agents/skills", ".claude/skills", ".workbuddy/skills", ".codebuddy/skills"):
        for f in copy_skills(ROOT / d):
            print("→", f)
