"""命令行入口：scriptcut <命令> [项目目录] [选项] [--json]

  doctor                         检查环境（ffmpeg / Playwright / 剪映库 / 字体 / Key）
  styles                         列出内置风格
  schema                         入场预设 / 音效 / 版式速查（给 Agent）
  init     <目录> [--script 文案.txt|docx|直接文字] [--style 风格] [--title 标题]
  check    <目录>                校验分镜（逐字覆盖、trigger、元素、版式……）+ 预估时长
  prompts  <目录>                导出 AI 出图提示词 → build/prompts.md
  image-api [目录] [--test]       查看出图 API 配置（不显示 Key）/ 出一张测试图
  gen      <目录> [--only C01,P03] [--force] [--provider ark|dashscope|siliconflow|openai|command] [--allow-placeholders]
                                 没接出图 API 时会提醒（error_code=need_image_api），不会静默用占位图
  assets   <目录>                准备元素图（文字渲染、占位图、裁边）
  voice    <目录> [--force] [--method asr|silence|estimate]   TTS 或录音对齐 → build/timing.json
  timeline <目录>                生成主时间轴 → build/timeline.json + build/subs.srt
  cues     <目录>                收集自定义画面的音效提示点
  audio    <目录>                音效 + 配乐 + 配音混音 → build/audio/master.wav
  stills   <目录> [--at 1.5,3] [--shot S03] [--marks]          静帧总览自检
  render   <目录> [--workers 4] [--from 0 --to 10] [--blur 12] [--out 名字.mp4]
  jianying <目录> [--mode editable|flat] [--name 草稿名] [--draft-dir 目录] [--force] [--at 3,10 只预览草稿]
  sheet    <目录>                导出分镜审阅表 xlsx
  deliver  <目录>                汇总交付物 → out/<名字>/
  status   <目录>                各阶段状态 + 下一步建议
  run      <目录> [--backend jianying|canvas|both] [--until 阶段] [--skip-gen] [--allow-placeholders]
                                 默认生成可编辑剪映草稿；出图没接 API 时在 gen 停下提醒
  preview  <目录> [--port 8765]  浏览器交互预览（拖动时间轴）
  install-agent <名字> [--global] [--project 目录]   安装技能 + MCP 配置：workbuddy / codebuddy / trae / lingma / qoder /
                                 doubao（打包技能 zip + 连接器说明）/ coze / agents / claude / all
  serve    [--host 0.0.0.0] [--port 8765] [--token 口令] [--root 工作目录]   启动 HTTP 版 MCP（豆包连接器、扣子等填 URL 用）

加 --json：stdout 只输出一行 JSON（{"ok": true, "command": ..., "result": {...}}），进度信息走 stderr，方便 Agent 解析。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from . import pipeline
from .util import ScriptCutError


def _floats(s: str | None):
    return [float(x) for x in s.split(",") if x.strip()] if s else None


def _human(cmd: str, res: dict) -> str:
    if cmd == "check":
        lines = [f"{'✓ 校验通过' if res['ok'] else '✗ 校验未通过'}：{res['shots']} 镜，{res['elements']} 个元素"
                 f"（AI 素材 {res['ai_images']}，缺 {res['ai_missing']}），{res['chars']} 字，预估 {res['estimated_seconds']} 秒"]
        lines += [f"  ✗ {e}" for e in res["errors"]] + [f"  ⚠ {w}" for w in res["warnings"]]
        return "\n".join(lines)
    if cmd == "status":
        return "\n".join([f"{k}: {v}" for k, v in res.items() if k != "next"] + ["下一步：" + " → ".join(res["next"])])
    return pipeline.dump_yaml(res).rstrip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scriptcut", description="文案驱动的自动剪辑工作流（Agent 友好）")
    ap.add_argument("command", choices=["doctor", "styles", "schema", "init", "check", "prompts", "gen", "assets", "voice",
                                        "timeline", "cues", "audio", "stills", "render", "jianying", "sheet", "deliver",
                                        "status", "run", "preview", "image-api", "install-agent", "serve"])
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--json", action="store_true", help="stdout 输出一行 JSON")
    ap.add_argument("--script")
    ap.add_argument("--style", default="whiteboard")
    ap.add_argument("--title", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--provider")
    ap.add_argument("--method", default="auto")
    ap.add_argument("--at")
    ap.add_argument("--shot")
    ap.add_argument("--marks", action="store_true")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float)
    ap.add_argument("--blur", type=int)
    ap.add_argument("--out")
    ap.add_argument("--mode", default="editable", choices=["editable", "layers", "flat"])
    ap.add_argument("--name")
    ap.add_argument("--draft-dir")
    ap.add_argument("--backend", choices=["canvas", "jianying", "both"])
    ap.add_argument("--until", choices=pipeline.STAGES)
    ap.add_argument("--skip-gen", action="store_true")
    ap.add_argument("--allow-placeholders", action="store_true", help="没接出图 API 时先用占位图继续")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default="")
    ap.add_argument("--root", default="")
    ap.add_argument("--global", dest="global_", action="store_true")
    ap.add_argument("--project", dest="target_project")
    a = ap.parse_args(argv)

    cmd, d = a.command, a.project
    try:
        if cmd == "doctor":
            res = pipeline.doctor()
        elif cmd == "styles":
            res = pipeline.styles()
        elif cmd == "schema":
            res = pipeline.schema()
        elif cmd == "init":
            res = pipeline.init(d, a.script, a.style, a.title, a.force)
        elif cmd == "check":
            res = pipeline.check(d)
        elif cmd == "prompts":
            res = pipeline.prompts(d)
        elif cmd == "gen":
            res = pipeline.gen(d, [x for x in a.only.split(",") if x], a.force, a.provider, a.allow_placeholders)
        elif cmd == "image-api":
            res = pipeline.image_api(a.project if a.project != "." else None, a.test)
        elif cmd == "assets":
            res = pipeline.assets(d)
        elif cmd == "voice":
            res = pipeline.voice(d, a.force, a.method)
        elif cmd == "timeline":
            res = pipeline.timeline(d)
        elif cmd == "cues":
            res = pipeline.cues(d)
        elif cmd == "audio":
            res = pipeline.audio(d)
        elif cmd == "stills":
            res = pipeline.stills(d, _floats(a.at), a.shot, a.marks)
        elif cmd == "render":
            res = pipeline.render(d, a.workers, a.t0, a.t1, a.blur, a.out)
        elif cmd == "jianying":
            res = pipeline.jianying(d, a.mode, a.name, a.draft_dir, a.force, _floats(a.at))
        elif cmd == "sheet":
            res = pipeline.sheet(d)
        elif cmd == "deliver":
            res = pipeline.deliver(d)
        elif cmd == "status":
            res = pipeline.status(d)
        elif cmd == "run":
            res = pipeline.run(d, a.backend, a.until, a.skip_gen, a.workers, a.allow_placeholders)
        elif cmd == "preview":
            return _preview(d, a.port)
        elif cmd == "install-agent":
            from .agents import install
            res = install(a.project, a.target_project, a.global_)
        elif cmd == "serve":
            from .mcp_server import main as mcp_main
            args = ["--http", "--host", a.host, "--port", str(a.port)]
            if a.token:
                args += ["--token", a.token]
            if a.root or (a.project not in (".", "")):
                args += ["--root", a.root or a.project]
            mcp_main(args)
            return 0
        else:  # pragma: no cover
            raise ScriptCutError(f"未知命令 {cmd}")
    except ScriptCutError as e:
        if a.json:
            print(json.dumps({"ok": False, "command": cmd, "error": str(e)}, ensure_ascii=False))
        else:
            print(f"✗ {e}", file=sys.stderr)
        return 1
    except Exception as e:  # 意外错误：带上堆栈，方便 Agent 定位
        if a.json:
            print(json.dumps({"ok": False, "command": cmd, "error": f"{type(e).__name__}: {e}",
                              "traceback": traceback.format_exc()[-2000:]}, ensure_ascii=False))
        else:
            traceback.print_exc()
        return 2
    ok = res.get("ok", True) if isinstance(res, dict) else True
    code = res.get("error_code") if isinstance(res, dict) else None
    if a.json:
        out = {"ok": ok, "command": cmd, "result": res}
        if code:  # 需要用户介入（如接入出图 API）：顶层给出 error_code / error，Agent 应把 error 原样转告用户
            out.update(error_code=code, error=res.get("error") or res.get("message"))
        print(json.dumps(out, ensure_ascii=False, default=str))
    else:
        if code == "need_image_api":
            print("⚠ " + (res.get("error") or ""), file=sys.stderr)
        print(_human(cmd, res))
    return 0 if ok else (3 if code else 1)


def _preview(d: str, port: int) -> int:
    import time

    from . import assets as assets_mod
    from .project import load
    from .render.server import serve
    p = load(d)
    if not (p.build / "timeline.json").exists():
        print("先运行 scriptcut timeline", file=sys.stderr)
        return 1
    with serve(p.dir, assets_mod.font_path(p), port) as url:
        print(f"预览：{url}/index.html?play=1   （Ctrl+C 退出；改了分镜重跑 timeline 后刷新页面）")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
