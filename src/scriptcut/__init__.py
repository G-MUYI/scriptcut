"""ScriptCut：文案驱动、配音为主时钟的自动剪辑工作流。

流程：文案 → 分镜（storyboard.yaml）→ 配音（TTS 或录音）→ 逐字对齐（timing.json）
     → 时间轴（timeline.json）→ 素材 / 音效 / 配乐 → Canvas 出片 或 剪映草稿 → 交付。

所有阶段都能通过 CLI（`scriptcut <命令> <项目目录> --json`）、Python API 或 MCP Server 调用。
"""

__version__ = "0.1.0"
