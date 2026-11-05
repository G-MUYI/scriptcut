@AGENTS.md

Claude Code 补充：
- Skills 在 `.claude/skills/`（由 `src/scriptcut/skills/` 同步而来；改 skill 请改 `src/scriptcut/skills/` 再运行 `python scripts/sync_skills.py`）。
- 想用 MCP 方式调用：把 `docs/mcp.example.json` 复制为仓库根目录的 `.mcp.json`（或 `claude mcp add scriptcut -- scriptcut-mcp`）。
- 看预览图时直接用 Read 打开 `build/jianying/draft_preview.jpg` 或 `build/stills/*.jpg`。
