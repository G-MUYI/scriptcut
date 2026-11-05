"""MCP Server：把每个阶段暴露成工具，国内外支持 MCP 的 Agent 都能直接调用。

两种传输：
  stdio（本机客户端：WorkBuddy、CodeBuddy、Trae、通义灵码、Kimi CLI、Qwen Code、Claude、Cursor、Codex……）
      scriptcut-mcp
  Streamable HTTP（只能填 URL 的客户端：豆包「自定义连接器」、扣子、飞书 Aily、Dify 等）
      scriptcut-mcp --http --port 8765 --token 你自己设的口令 [--root 允许访问的工作目录]
      地址 http://<主机>:8765/mcp ，请求头 Authorization: Bearer <口令>

远程客户端（豆包等）通常不能直接读写你电脑上的文件，所以额外提供：写分镜 / 读文件 / 上传素材 / 看图 这几个工具。
开了 --root 时，所有 project 路径都相对它解析，且不能越出它（对外暴露时务必设置）。
长任务（voice / render / jianying）会阻塞到完成；结果是一段 JSON。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

from . import pipeline
from .util import ScriptCutError

WRITABLE = ("storyboard.yaml", "project.yaml", "input/script.txt")
READ_LIMIT = 200_000
ROOT: Path | None = None


def _resolve(project: str) -> str:
    """把 project 参数解析成目录；设置了工作目录（--root / SCRIPTCUT_WORKSPACE）时限制在其中。"""
    root = ROOT or (Path(os.environ["SCRIPTCUT_WORKSPACE"]).resolve() if os.environ.get("SCRIPTCUT_WORKSPACE") else None)
    p = Path(project)
    if root is not None:
        p = (root / project).resolve() if not p.is_absolute() else p.resolve()
        if p != root and root not in p.parents:
            raise ScriptCutError(f"项目路径必须在工作目录 {root} 之内")
    return str(p)


def _wrap(fn, *a, **k) -> str:
    try:
        res = fn(*a, **k)
        ok = res.get("ok", True) if isinstance(res, dict) else True
        out = {"ok": ok, "result": res}
        if isinstance(res, dict) and res.get("error_code"):
            # 需要用户介入（如接入出图 API）：Agent 应把 error 原样转告用户，并等用户处理后再继续
            out.update(error_code=res["error_code"], error=res.get("error"),
                       tell_user=True)
        return json.dumps(out, ensure_ascii=False, default=str)
    except ScriptCutError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:  # 让 Agent 看到原因而不是断开连接
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def _server_class():
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer as Server
        from mcp.server.mcpserver.utilities.types import Image
    except ImportError:
        try:  # mcp 1.x
            from mcp.server.fastmcp import FastMCP as Server
            from mcp.server.fastmcp import Image
        except ImportError as e:  # pragma: no cover
            raise SystemExit("需要 MCP SDK：pip install mcp") from e
    return Server, Image


INSTRUCTIONS = """ScriptCut：把口播文案做成视频与可编辑剪映草稿。流程：init → 写 storyboard.yaml（scriptcut_write_file）→ check
→ gen（出图；返回 error_code=need_image_api 时必须停下，把 error 原文转告用户，请用户接入自己的出图 API 或选择占位图）
→ voice → timeline → audio → jianying（可编辑剪映草稿，附预览图）/ render（MP4）→ deliver。
规则：文案一字不改；所有镜头 text 拼接必须与 input/script.txt 逐字一致；中途接手先调 scriptcut_status。"""


def build_server():
    Server, Image = _server_class()
    try:
        mcp = Server("scriptcut", instructions=INSTRUCTIONS)
    except TypeError:  # 旧版 SDK 没有 instructions 参数
        mcp = Server("scriptcut")

    @mcp.tool()
    def scriptcut_doctor() -> str:
        """检查运行环境：ffmpeg、Playwright/Chromium、pyJianYingDraft、edge-tts、中文字体、配音 / 出图 API 是否已接入。首次使用先调用。"""
        return _wrap(pipeline.doctor)

    @mcp.tool()
    def scriptcut_schema() -> str:
        """速查：内置风格（版式、角色、点缀色）、入场动画预设、音效库、待机动效。写分镜前调用。"""
        return _wrap(pipeline.schema)

    @mcp.tool()
    def scriptcut_init(project: str, script: str = "", style: str = "whiteboard", title: str = "") -> str:
        """新建项目目录。script 可以是文案文件路径（txt/md/docx）或直接是文案文字（一字不改地写入 input/script.txt）。"""
        return _wrap(lambda: pipeline.init(_resolve(project), script or None, style, title))

    @mcp.tool()
    def scriptcut_write_file(project: str, path: str, content: str) -> str:
        """写项目里的文本文件（远程 Agent 没有本地文件权限时用）。只允许：storyboard.yaml、project.yaml、
        input/script.txt、scenes/<名字>.js。写完 storyboard.yaml 请调用 scriptcut_check。"""
        def go():
            d = Path(_resolve(project))
            rel_p = path.replace("\\", "/").lstrip("/")
            ok = rel_p in WRITABLE or (rel_p.startswith("scenes/") and rel_p.endswith(".js") and ".." not in rel_p)
            if not ok:
                raise ScriptCutError(f"不允许写 {path}（只能写 {'、'.join(WRITABLE)}、scenes/*.js）")
            f = d / rel_p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
            return {"file": rel_p, "chars": len(content)}
        return _wrap(go)

    @mcp.tool()
    def scriptcut_read_file(project: str, path: str) -> str:
        """读项目里的文本文件，例如 storyboard.yaml、input/script.txt、build/prompts.md、build/report/*.json、out/<名字>/交付说明.md。"""
        def go():
            d = Path(_resolve(project)).resolve()
            f = (d / path).resolve()
            if d not in f.parents:
                raise ScriptCutError("只能读项目目录里的文件")
            if not f.is_file():
                raise ScriptCutError(f"文件不存在：{path}")
            text = f.read_text(encoding="utf-8", errors="replace")
            return {"file": path, "content": text[:READ_LIMIT], "truncated": len(text) > READ_LIMIT}
        return _wrap(go)

    @mcp.tool()
    def scriptcut_put_asset(project: str, element_id: str, url: str = "", base64_data: str = "") -> str:
        """把一张图片放进项目素材 assets/<编号>.png（Agent 自己生成了图、或用户发来了图时用）。url 和 base64_data 二选一。"""
        def go():
            import requests
            from PIL import Image as PILImage
            d = Path(_resolve(project))
            if url:
                r = requests.get(url, timeout=120)
                r.raise_for_status()
                data = r.content
            elif base64_data:
                data = base64.b64decode(base64_data.split(",", 1)[-1])
            else:
                raise ScriptCutError("需要 url 或 base64_data")
            img = PILImage.open(io.BytesIO(data))
            out = d / "assets" / f"{element_id}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return {"file": f"assets/{element_id}.png", "size": img.size,
                    "next": "白底图会在生成时自动抠底；重新运行 timeline / jianying 即可用上"}
        return _wrap(go)

    @mcp.tool()
    def scriptcut_view_image(project: str, path: str, max_width: int = 1600):
        """查看项目里的图片（总览图 build/stills/sheet_1.jpg、剪映草稿预览 build/jianying/draft_preview.jpg 等），用来自检版式。"""
        try:
            from PIL import Image as PILImage
            d = Path(_resolve(project)).resolve()
            f = (d / path).resolve()
            if d not in f.parents or not f.is_file():
                raise ScriptCutError(f"图片不存在：{path}")
            img = PILImage.open(f).convert("RGB")
            if img.width > max_width:
                img = img.resize((max_width, int(img.height * max_width / img.width)))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            return Image(data=buf.getvalue(), format="jpeg")
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    def scriptcut_check(project: str) -> str:
        """校验 storyboard.yaml：镜头文案拼接是否与原稿逐字一致、trigger/标红词是否在文案里、元素/版式/预设是否合法；返回预估时长。"""
        return _wrap(lambda: pipeline.check(_resolve(project)))

    @mcp.tool()
    def scriptcut_status(project: str) -> str:
        """查看各阶段完成情况与下一步建议（哪些素材缺、出图 API 是否接入、时间轴是否过期、是否已生成草稿）。"""
        return _wrap(lambda: pipeline.status(_resolve(project)))

    @mcp.tool()
    def scriptcut_prompts(project: str) -> str:
        """导出 AI 出图提示词清单 build/prompts.md（风格段、点缀色、人物外观已拼好），可用 scriptcut_read_file 读取。"""
        return _wrap(lambda: pipeline.prompts(_resolve(project)))

    @mcp.tool()
    def scriptcut_image_api(project: str = "", test: bool = False) -> str:
        """查看出图 API 是否已接入（不显示 Key）、支持哪些平台及填写方法；test=True 时出一张测试图验证。"""
        return _wrap(lambda: pipeline.image_api(_resolve(project) if project else None, test))

    @mcp.tool()
    def scriptcut_gen(project: str, only: str = "", force: bool = False, provider: str = "", allow_placeholders: bool = False) -> str:
        """AI 出图到 assets/<编号>.png，使用用户自己接入的出图 API（豆包 Seedream / 通义万相 / 硅基流动 / OpenAI 兼容 / 命令行）。
        没接入时返回 error_code=need_image_api：必须把 error 原文转告用户并等待——不要擅自改用占位图；
        只有用户明确同意先用占位图时才传 allow_placeholders=true。only 为逗号分隔的编号。"""
        return _wrap(lambda: pipeline.gen(_resolve(project), [x for x in only.split(",") if x], force, provider or None,
                                          allow_placeholders))

    @mcp.tool()
    def scriptcut_voice(project: str, force: bool = False, method: str = "auto") -> str:
        """配音：没有录音时逐句 TTS（edge 免费 / fish）；有 input/voice.* 时对齐录音（asr / silence）。输出逐字时间 build/timing.json。"""
        return _wrap(lambda: pipeline.voice(_resolve(project), force, method))

    @mcp.tool()
    def scriptcut_timeline(project: str) -> str:
        """生成主时间轴 build/timeline.json + 字幕 build/subs.srt；返回遮挡、拥挤、开头空画面等提示。改了分镜后要重跑。"""
        return _wrap(lambda: pipeline.timeline(_resolve(project)))

    @mcp.tool()
    def scriptcut_cues(project: str) -> str:
        """收集自定义 Canvas 画面（scenes/*.js 的 cues）声明的音效提示点。之后重跑 timeline 再跑 audio。"""
        return _wrap(lambda: pipeline.cues(_resolve(project)))

    @mcp.tool()
    def scriptcut_audio(project: str) -> str:
        """混音：配音 + 程序化音效（按时间轴提示点，冲击类峰值对齐）+ 配乐（自动避让人声）→ build/audio/master.wav（-14 LUFS）。"""
        return _wrap(lambda: pipeline.audio(_resolve(project)))

    @mcp.tool()
    def scriptcut_jianying(project: str, mode: str = "editable", name: str = "", draft_dir: str = "", force: bool = False) -> str:
        """生成可编辑的剪映草稿。editable（默认）：图片 / 文字 / 标题 / 字幕全是剪映原生对象，动画是关键帧，音效逐条分轨，
        并生成 build/jianying/draft_preview.jpg 预览图（用 scriptcut_view_image 查看）；flat：成片画面 + 可编辑字幕 + 分轨音频。"""
        return _wrap(lambda: pipeline.jianying(_resolve(project), mode, name or None, draft_dir or None, force))

    @mcp.tool()
    def scriptcut_stills(project: str, at: str = "", shot: str = "", marks: bool = False) -> str:
        """Canvas 静帧总览图自检（build/stills/*.jpg，需要 Playwright）。at：逗号分隔秒数；shot：某一镜 8 帧；marks：每个元素落定时刻。"""
        ts = [float(x) for x in at.split(",") if x.strip()] or None
        return _wrap(lambda: pipeline.stills(_resolve(project), ts, shot or None, marks))

    @mcp.tool()
    def scriptcut_render(project: str, workers: int = 0, start: float = 0.0, end: float = -1.0, blur: int = 0) -> str:
        """Canvas 逐帧出片 → out/<项目名>.mp4（合成 master.wav）。end<0 表示到结尾；blur=8~12 开运动模糊（慢）。"""
        return _wrap(lambda: pipeline.render(_resolve(project), workers or None, start, None if end < 0 else end, blur or None))

    @mcp.tool()
    def scriptcut_sheet(project: str) -> str:
        """导出分镜审阅表 build/分镜表.xlsx（分镜、素材与提示词、数据核实）。"""
        return _wrap(lambda: pipeline.sheet(_resolve(project)))

    @mcp.tool()
    def scriptcut_deliver(project: str) -> str:
        """汇总交付物到 out/<项目名>/：剪映草稿位置、成片、字幕、分轨、交付说明（含待核实说法、占位素材）。"""
        return _wrap(lambda: pipeline.deliver(_resolve(project)))

    @mcp.tool()
    def scriptcut_run(project: str, backend: str = "", until: str = "", skip_gen: bool = False, allow_placeholders: bool = False) -> str:
        """一键流程：check → gen → voice → timeline → cues → audio → jianying（默认，可编辑草稿）/ render → deliver。
        出图没接 API 时停在 gen（stopped_at=gen, error_code=need_image_api），把 error 转告用户。"""
        return _wrap(lambda: pipeline.run(_resolve(project), backend or None, until or None, skip_gen, None, allow_placeholders))

    return mcp


# ---------------------------------------------------------------- HTTP

class TokenAuth:
    """最简单的 Bearer 口令校验（ASGI 中间件）。对外暴露（内网穿透 / 公网）时务必设置。"""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.token:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            if auth != f"Bearer {self.token}" and headers.get("x-api-key") != self.token:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def http_app(mcp, path: str = "/mcp", host: str = "127.0.0.1"):
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)  # 走内网穿透时 Host 头是外部域名
    except ImportError:  # pragma: no cover
        sec = None
    try:
        return mcp.streamable_http_app(streamable_http_path=path, stateless_http=True, json_response=True,
                                       transport_security=sec, host=host)
    except TypeError:  # mcp 1.x：参数在 settings 上
        mcp.settings.streamable_http_path = path
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True
        return mcp.streamable_http_app()


def main(argv=None) -> None:
    global ROOT
    from . import config
    config.load_env()
    ap = argparse.ArgumentParser(prog="scriptcut-mcp", description="ScriptCut MCP Server")
    ap.add_argument("--http", action="store_true", help="用 Streamable HTTP 传输（豆包连接器等只能填 URL 的客户端）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址；局域网 / 内网穿透用 0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--path", default="/mcp")
    ap.add_argument("--token", default=os.environ.get("SCRIPTCUT_MCP_TOKEN", ""), help="访问口令（Authorization: Bearer <口令>）")
    ap.add_argument("--root", default=os.environ.get("SCRIPTCUT_WORKSPACE", ""), help="只允许访问这个目录下的项目")
    a = ap.parse_args(argv)
    if a.root:
        ROOT = Path(a.root).resolve()
        ROOT.mkdir(parents=True, exist_ok=True)
    mcp = build_server()
    if not a.http:
        print("scriptcut MCP server (stdio) 启动", file=sys.stderr)
        mcp.run()
        return
    if a.host not in ("127.0.0.1", "localhost") and not a.token:
        raise SystemExit("对外监听（--host 非本机）时必须设置 --token（或环境变量 SCRIPTCUT_MCP_TOKEN）")
    import uvicorn
    app = TokenAuth(http_app(mcp, a.path, a.host), a.token)
    print(f"scriptcut MCP server (Streamable HTTP)：http://{a.host}:{a.port}{a.path}"
          f"{'  鉴权：Authorization: Bearer <token>' if a.token else '  （未设置 token，仅限本机）'}"
          f"{'  工作目录：' + str(ROOT) if ROOT else ''}", file=sys.stderr)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
