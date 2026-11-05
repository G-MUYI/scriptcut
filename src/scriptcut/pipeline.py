"""各阶段的 Python API（CLI 和 MCP Server 都调这里）。每个函数返回可 JSON 序列化的 dict。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

from . import assets as assets_mod
from . import config
from . import project as proj
from . import timeline as tl_mod
from . import timing as tmod
from .text import read_script
from .util import ScriptCutError, read_json, rel, write_json

STAGES = ["check", "gen", "voice", "timeline", "cues", "audio", "stills", "render", "jianying", "deliver"]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load(project_dir) -> proj.Project:
    return proj.load(project_dir)


# ---------------------------------------------------------------- 初始化 / 校验

def init(project_dir: str, script: str | None = None, style: str = "whiteboard", title: str = "", force: bool = False) -> dict:
    d = Path(project_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("input", "assets", "materials", "scenes"):
        (d / sub).mkdir(exist_ok=True)
    text = ""
    if script:
        sp = Path(script)
        text = read_script(sp) if sp.exists() else script
        (d / "input" / "script.txt").write_text(text, encoding="utf-8")
    elif not (d / "input" / "script.txt").exists():
        (d / "input" / "script.txt").write_text("", encoding="utf-8")
    else:
        text = (d / "input" / "script.txt").read_text(encoding="utf-8")
    proj.load_style(style, d)  # 校验风格存在
    pf = d / "project.yaml"
    if force or not pf.exists():
        tpl = (config.TEMPLATES_DIR / "project.yaml").read_text(encoding="utf-8")
        pf.write_text(tpl.replace("{title}", title or d.name).replace("{style}", style), encoding="utf-8")
    sbf = d / "storyboard.yaml"
    if force or not sbf.exists():
        tpl = (config.TEMPLATES_DIR / "storyboard.yaml").read_text(encoding="utf-8")
        sbf.write_text(tpl.replace("{title}", title or d.name).replace("{script}", text), encoding="utf-8")
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("build/\nout/\n.env\n", encoding="utf-8")
    return {"project": str(d), "chars": len(text), "style": style,
            "next": "按分镜 skill 编辑 storyboard.yaml（或让 Agent 生成），然后运行 scriptcut check"}


def check(project_dir) -> dict:
    p = load(project_dir)
    errs, warns = proj.validate(p)
    res = {"ok": not errs, "errors": errs, "warnings": warns, **proj.summary(p)}
    write_json(p.build / "report" / "check.json", res)
    return res


def _require_valid(p: proj.Project) -> None:
    errs, _ = proj.validate(p)
    if errs:
        raise ScriptCutError("分镜校验未通过（先运行 check 修好）：\n  " + "\n  ".join(errs[:20]))


# ---------------------------------------------------------------- 素材

def prompts(project_dir) -> dict:
    p = load(project_dir)
    out = assets_mod.export_prompts(p)
    items = assets_mod.prompts(p)
    return {"file": rel(out, p.dir), "count": len(items), "missing": [i["id"] for i in items if not i["exists"]]}


def gen(project_dir, only: list[str] | None = None, force: bool = False, provider: str | None = None,
        allow_placeholders: bool = False) -> dict:
    """AI 出图。没接入出图 API 时返回 ok=false + error_code=need_image_api（附填写说明），提醒用户先接入自己的 API。"""
    p = load(project_dir)
    _require_valid(p)
    return assets_mod.generate(p, only, force, provider, progress=_log, allow_placeholders=allow_placeholders)


def image_api(project_dir=None, test: bool = False) -> dict:
    """查看出图 API 配置（不显示 Key）；test=True 时用当前配置出一张测试图。"""
    from . import imagegen
    p = load(project_dir) if project_dir and (Path(project_dir) / "storyboard.yaml").exists() else None
    if p is None:
        config.load_env(Path(project_dir)) if project_dir else config.load_env()
    if test:
        out = (p.build if p else Path.cwd()) / "image_api_test.png"
        return imagegen.test(p, out)
    st = imagegen.settings(p)
    res = {"ok": True, **{k: v for k, v in st.items() if k != "command"}, "presets": {k: v["label"] for k, v in imagegen.PRESETS.items()}}
    if not st["configured"]:
        res["message"] = imagegen.GUIDE
    return res


def assets(project_dir) -> dict:
    p = load(project_dir)
    st = assets_mod.assets_status(p)
    return {"elements": st["elements"], "placeholders": st["placeholders"]}


# ---------------------------------------------------------------- 配音 / 时间轴

def voice(project_dir, force: bool = False, method: str = "auto") -> dict:
    from . import voice as voice_mod
    p = load(project_dir)
    _require_valid(p)
    return voice_mod.run(p, force, method, progress=_log)


def timeline(project_dir) -> dict:
    p = load(project_dir)
    _require_valid(p)
    return tl_mod.build(p)


def cues(project_dir) -> dict:
    from .render import canvas
    p = load(project_dir)
    return canvas.collect_cues(p)


def audio(project_dir) -> dict:
    from .audio import mix
    p = load(project_dir)
    return mix.build(p)


# ---------------------------------------------------------------- 出片

def stills(project_dir, at: list[float] | None = None, shot: str | None = None, marks: bool = False) -> dict:
    from .render import canvas
    p = load(project_dir)
    return canvas.stills(p, at, shot, marks)


def render(project_dir, workers: int | None = None, t0: float = 0.0, t1: float | None = None, blur: int | None = None,
           out: str | None = None) -> dict:
    from .render import canvas
    p = load(project_dir)
    return canvas.render(p, workers, t0, t1, blur, out, progress=_log)


def jianying(project_dir, mode: str = "editable", name: str | None = None, draft_dir: str | None = None,
             force: bool = False, preview_at: list[float] | None = None) -> dict:
    from .jianying import builder
    p = load(project_dir)
    if preview_at:  # 只看已生成草稿的某几个时刻
        return builder.preview(p, preview_at, name, Path(draft_dir) if draft_dir else None)
    return builder.build(p, mode, name, Path(draft_dir) if draft_dir else None, force)


def sheet(project_dir) -> dict:
    from . import sheet as sheet_mod
    p = load(project_dir)
    return sheet_mod.export(p)


def deliver(project_dir) -> dict:
    p = load(project_dir)
    name = p.name
    dst = p.out / name
    dst.mkdir(parents=True, exist_ok=True)
    files = {}
    pairs = [(p.out / f"{name}.mp4", f"{name}.mp4"), (p.build / "render" / "video.mp4", "画面_无声.mp4"),
             (p.build / "subs.srt", "字幕.srt"), (p.build / "audio" / "master.wav", "混音_master.wav"),
             (p.build / "audio" / "stem_vo.wav", "分轨_配音.wav"), (p.build / "audio" / "stem_sfx.wav", "分轨_音效.wav"),
             (p.build / "audio" / "stem_music.wav", "分轨_配乐.wav"), (p.build / "prompts.md", "素材提示词.md")]
    for src, nm in pairs:
        if src.exists():
            shutil.copy2(src, dst / nm)
            files[nm] = rel(dst / nm, p.dir)
    if (p.build / "jianying" / "draft_preview.jpg").exists():
        shutil.copy2(p.build / "jianying" / "draft_preview.jpg", dst / "剪映草稿预览.jpg")
        files["剪映草稿预览.jpg"] = rel(dst / "剪映草稿预览.jpg", p.dir)
    for sheet_file in (p.build / "stills").glob("sheet_*.jpg") if (p.build / "stills").exists() else []:
        shutil.copy2(sheet_file, dst / f"总览_{sheet_file.name}")
        files[f"总览_{sheet_file.name}"] = rel(dst / f"总览_{sheet_file.name}", p.dir)
    rep_tl = read_json(p.build / "report" / "timeline.json", {}) or {}
    rep_jy = read_json(p.build / "report" / "jianying.json", {}) or {}
    facts = [f for f in p.facts if str(f.get("status", "未核实")) != "一致"]
    manifest = {"title": p.title, "duration": rep_tl.get("duration"), "timing": rep_tl.get("timing"), "files": files,
                "jianying_draft": rep_jy.get("draft"), "warnings": rep_tl.get("warnings", []),
                "placeholders": rep_tl.get("placeholders", []), "facts_to_verify": facts}
    write_json(dst / "manifest.json", manifest)
    lines = [f"# {p.title} · 交付清单", "", f"- 时长：{manifest['duration']} 秒（时间轴来源：{manifest['timing']}）"]
    if manifest["jianying_draft"]:
        lines.append(f"- 剪映草稿（可编辑）：{manifest['jianying_draft']}")
        n = rep_jy.get("native") or {}
        if n:
            lines.append(f"  - 原生对象：图片 {n.get('image', 0)}、文本 {n.get('text', 0)}、关键帧 {n.get('keyframes', 0)}、"
                         f"独立音效 {n.get('sfx', 0)} 条、代码画面片段 {n.get('scene_clips', 0)} 段")
        lines += [f"  - {x}" for x in rep_jy.get("notes", [])]
    lines += ["", "## 文件"] + [f"- {k}" for k in files]
    if manifest["placeholders"]:
        lines += ["", "## 仍是占位图的素材", "- " + "、".join(manifest["placeholders"])]
    if facts:
        lines += ["", "## 发布前需要核实的说法"] + [f"- {f.get('claim')}（{f.get('status', '未核实')}）" for f in facts]
    if manifest["warnings"]:
        lines += ["", "## 时间轴提示"] + [f"- {w}" for w in manifest["warnings"]]
    (dst / "交付说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"dir": rel(dst, p.dir), **manifest}


# ---------------------------------------------------------------- 状态 / 一键

def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def status(project_dir) -> dict:
    p = load(project_dir)
    errs, warns = proj.validate(p)
    b = p.build
    src_m = max(_mtime(p.dir / "storyboard.yaml"), _mtime(p.dir / "project.yaml"), _mtime(p.dir / "input" / "script.txt"))
    tm = tmod.load(p)
    tl_m, tm_m = _mtime(b / "timeline.json"), _mtime(b / "timing.json")
    scenes_m = max([_mtime(f) for f in (p.dir / "scenes").glob("*.js")] or [0])
    st = {
        "valid": not errs, "errors": errs[:10], "warnings": len(warns),
        "ai_missing": [e.id for e in p.elements.values() if e.needs_ai and assets_mod._ai_file(p, e.id) is None],
        "timing": tm.source if tm else None,
        "timeline": "missing" if not tl_m else ("stale" if tl_m < max(src_m, tm_m) else "ok"),
        "audio": "missing" if not (b / "audio" / "master.wav").exists() else (
            "stale" if _mtime(b / "audio" / "master.wav") < tl_m else "ok"),
        "video": rel(p.out / f"{p.name}.mp4", p.dir) if (p.out / f"{p.name}.mp4").exists() else None,
        "video_stale": bool((p.out / f"{p.name}.mp4").exists() and _mtime(p.out / f"{p.name}.mp4") < max(tl_m, scenes_m)),
        "jianying": (read_json(b / "report" / "jianying.json", {}) or {}).get("draft"),
    }
    nxt = []
    if errs:
        nxt.append("check（修分镜错误）")
    else:
        if st["ai_missing"]:
            from . import imagegen
            if imagegen.settings(p)["configured"]:
                nxt.append(f"gen（还有 {len(st['ai_missing'])} 个 AI 素材，出图 API：{imagegen.settings(p)['label']}）")
            else:
                nxt.append(f"提醒用户接入自己的出图 API（还有 {len(st['ai_missing'])} 个 AI 素材；scriptcut image-api 查看填写方法），"
                           "或手动出图放进 assets/，或明确选择先用占位图")
        if not tm or tm.source == "estimate":
            nxt.append("voice（配音 / 对齐）")
        if st["timeline"] != "ok":
            nxt.append("timeline")
        if st["audio"] != "ok":
            nxt.append("audio")
        backend = p.cfg["render"].get("backend", "jianying")
        jy = read_json(b / "report" / "jianying.json", {}) or {}
        jy_stale = not jy or _mtime(b / "report" / "jianying.json") < tl_m
        if backend in ("jianying", "both") and jy_stale:
            nxt.append("jianying（生成可编辑剪映草稿，看 build/jianying/draft_preview.jpg 自检）")
        if backend in ("canvas", "both") and (not st["video"] or st["video_stale"]):
            nxt.append("stills → render（MP4）")
    st["next"] = nxt or ["deliver"]
    return st


def run(project_dir, backend: str | None = None, until: str | None = None, skip_gen: bool = False,
        workers: int | None = None, allow_placeholders: bool = False) -> dict:
    """一键：check → gen → voice → timeline → cues → audio → (stills → render) / jianying → deliver。

    出图这一步没接入出图 API 时会停下来（ok=false, error_code=need_image_api），提醒用户接入自己的 API；
    明确要先用占位图继续时传 allow_placeholders=True。"""
    p = load(project_dir)
    backend = backend or p.cfg["render"].get("backend", "jianying")
    done = {}

    def step(name, fn, *a, **k):
        _log(f"▶ {name}")
        done[name] = fn(*a, **k)
        return until == name

    c = check(project_dir)
    done["check"] = {"errors": c["errors"], "warnings": len(c["warnings"])}
    if c["errors"]:
        raise ScriptCutError("分镜校验未通过：\n  " + "\n  ".join(c["errors"][:20]))
    if until == "check":
        return done
    if not skip_gen:
        g = gen(project_dir, allow_placeholders=allow_placeholders)
        done["gen"] = g
        if not g.get("ok", True):
            done["ok"] = False
            done["stopped_at"] = "gen"
            done["error_code"] = g.get("error_code")
            done["error"] = g.get("error")
            return done
        if until == "gen":
            return done
    if step("voice", voice, project_dir):
        return done
    if step("timeline", timeline, project_dir):
        return done
    if any(s.scene for s in p.shots):  # 自定义画面声明的音效提示点（两个后端都需要）
        step("cues", cues, project_dir)
        step("timeline", timeline, project_dir)
    if step("audio", audio, project_dir):
        return done
    if backend in ("canvas", "both"):
        if step("stills", stills, project_dir):
            return done
        if step("render", render, project_dir, workers):
            return done
    if backend in ("jianying", "both"):
        if step("jianying", jianying, project_dir, "editable"):
            return done
    step("deliver", deliver, project_dir)
    return done


def styles() -> dict:
    return {"styles": proj.list_styles()}


def doctor() -> dict:
    import importlib.util as iu
    res = {"python": sys.version.split()[0], "ffmpeg": bool(config.which("ffmpeg")), "ffprobe": bool(config.which("ffprobe"))}
    for mod in ("playwright", "pyJianYingDraft", "edge_tts", "docx", "openpyxl", "mcp"):
        res[mod] = iu.find_spec(mod) is not None
    try:
        res["font"] = str(config.find_font())
    except FileNotFoundError as e:
        res["font"] = None
        res["font_error"] = str(e)
    config.load_env()
    res["fish_key"] = bool(config.env("FISH_API_KEY"))
    from . import imagegen
    ia = imagegen.settings()
    res["image_api"] = ia["label"] if ia["configured"] else None
    res["jianying_dir"] = str(config.jianying_drafts_dir())
    if res["playwright"]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                b.close()
            res["chromium"] = True
        except Exception as e:  # 没装浏览器 / 缺系统库
            res["chromium"] = False
            res["chromium_error"] = str(e).splitlines()[0][:200]
    hints = []
    if not res["ffmpeg"]:
        hints.append("安装 ffmpeg 并加入 PATH")
    if not res["playwright"]:
        hints.append("Canvas 出片：pip install playwright && playwright install chromium")
    elif not res.get("chromium"):
        hints.append("执行 playwright install chromium（Linux 还需 playwright install-deps）")
    if not res["pyJianYingDraft"]:
        hints.append("剪映草稿：pip install pyJianYingDraft==0.3.0")
    if not res["image_api"]:
        hints.append("出图：还没接入出图 API（豆包 Seedream / 通义万相 / 硅基流动 / OpenAI 兼容 / 命令行），运行 scriptcut image-api 查看填写方法")
    if not res["edge_tts"] and not res["fish_key"]:
        hints.append("配音：pip install edge-tts（免费），或在 .env 配置 FISH_API_KEY")
    res["hints"] = hints
    res["ok"] = res["ffmpeg"] and res["ffprobe"] and bool(res["font"])
    return res


def schema() -> dict:
    """给 Agent 的速查：风格、版式、入场预设、音效、待机动效。"""
    from .audio.sfx import DESCRIPTIONS
    from .motion import PRESETS
    return {"presets": {k: v.desc for k, v in PRESETS.items()} | {"slide_auto": "按所在一侧自动选左/右划入，居中从下往上"},
            "sfx": DESCRIPTIONS, "idle": list(proj.IDLE_KINDS), "kinds": list(proj.KINDS),
            "styles": proj.list_styles(), "docs": "docs/schema.md"}


def dump_yaml(data) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
