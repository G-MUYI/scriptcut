"""项目模型：project.yaml（设置）+ storyboard.yaml（分镜与素材库）+ 风格（styles/*.yaml），以及校验。

项目目录约定：
  project.yaml        设置（风格、配音、渲染后端……），可省略，全部有默认值
  storyboard.yaml     分镜 + 素材库（Agent 主要编辑这个文件）
  input/script.txt    文案：唯一文字来源，一字不改
  input/voice.*       （可选）录好的配音
  assets/<ID>.png     AI 生成或手动放入的元素图
  materials/          用户提供的截图 / 视频素材
  scenes/<name>.js    （可选）自定义 Canvas 画面
  build/              生成物；out/ 交付物
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import captions as cap_mod
from . import config
from .motion import PRESETS
from .util import ScriptCutError

PERSON_ROLES = ("character", "big")
IDLE_KINDS = ("auto", "zoom", "float", "none")
KINDS = ("image", "video", "word")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".flac")
WORD_AI_MAX = 12   # AI 画长文字不可靠
LABEL_MAX = 4

DEFAULT_PROJECT: dict[str, Any] = {
    "title": "",
    "script": "input/script.txt",
    "style": "whiteboard",
    "style_overrides": {},
    "character": {"description": "", "ref": ""},
    "voice": {
        "source": "auto",          # auto（有 input/voice.* 用录音，否则 TTS）| tts | file | none（只估算）
        "file": "",
        "align": "auto",           # file 时：auto（有 FISH_API_KEY 用 asr，否则 silence）| asr | silence | estimate
        "tts": {"provider": "auto", "voice": "", "speed": 1.0, "model": "", "verify": False},
        "replace": {},             # 读法替换：{"30ms": "三十毫秒"}，只影响 TTS，不影响上屏
    },
    "audio": {"loudness": -14, "music": {}, "sfx": {}},
    "render": {"backend": "jianying", "workers": 4, "motion_blur": 1, "crf": 18, "format": "png"},
    "image_gen": {"provider": "auto", "size": 1024, "knockout": True, "concurrency": 2},
    "output": {"name": ""},
}


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ---------------------------------------------------------------- 数据结构

@dataclass
class Element:
    id: str
    kind: str = "image"           # image | video | word（本地渲染的文字）
    role: str = "icon"
    name: str = ""
    prompt: str = ""              # AI 出图：只写「画什么」
    accent: str = ""              # 点缀色名（style.palette）
    accent_part: str = ""
    label: str = ""               # 图上手写标注（≤4 字）
    text: str = ""                # word / role: word 时要写的字
    hl: str = ""                  # text 里用点缀色的部分
    src: str = ""                 # 用户提供素材（相对项目目录）
    fit: str = "contain"          # 放进盒子版式时：contain | cover
    clip_in: float = 0.0          # 视频截取起点
    volume: float = 0.0           # 视频原声音量
    ref: bool | None = None       # 是否挂人物参考图（None = 人物角色自动挂）
    knockout: bool | None = None  # 出图后抠白底（None = 跟随 image_gen.knockout）

    @property
    def needs_ai(self) -> bool:
        return self.kind == "image" and not self.src


@dataclass
class Item:
    el: str
    trigger: str = ""
    trigger_at: int = 0           # trigger 在镜头文案中的字符位置（-1 = 找不到）
    preset: str = "pop"
    slot: int | None = None
    pos: tuple[float, float] | None = None
    size: int | None = None
    box: tuple[float, float] | None = None   # 盒子版式的宽高
    grow: str | None = None
    delay: float = 0.0
    shake: bool = False
    bleed: bool = False
    over: str = ""
    sfx: str | None = None        # None = 预设自带；"" = 不要音效
    idle: str = "auto"


@dataclass
class Shot:
    id: str
    text: str
    offset: int = 0
    seg: str = ""
    layout: str = ""
    items: list[Item] = field(default_factory=list)
    red: list[str] = field(default_factory=list)
    cap: list[str] | None = None
    marks: dict[str, str] = field(default_factory=dict)
    scene: str = ""
    sfx: list[dict] = field(default_factory=list)
    chapter: dict | None = None
    duration: float = 0.0         # 无口播镜头的时长
    note: str = ""

    @property
    def silent(self) -> bool:
        return not self.text


@dataclass
class Project:
    dir: Path
    cfg: dict
    style: dict
    title: str
    script: str
    elements: dict[str, Element]
    shots: list[Shot]
    facts: list[dict]
    issues: list[str] = field(default_factory=list)

    @property
    def W(self) -> int:
        return int(self.style["canvas"]["width"])

    @property
    def H(self) -> int:
        return int(self.style["canvas"]["height"])

    @property
    def fps(self) -> int:
        return int(self.style["canvas"]["fps"])

    @property
    def build(self) -> Path:
        return self.dir / "build"

    @property
    def out(self) -> Path:
        return self.dir / "out"

    @property
    def name(self) -> str:
        return self.cfg["output"].get("name") or self.dir.name

    def path(self, rel: str) -> Path:
        return (self.dir / rel).resolve()

    def voice_file(self) -> Path | None:
        f = self.cfg["voice"].get("file")
        if f:
            p = self.path(f)
            return p if p.exists() else None
        for ext in AUDIO_EXT:
            p = self.dir / "input" / f"voice{ext}"
            if p.exists():
                return p
        return None

    def voice_source(self) -> str:
        src = self.cfg["voice"].get("source", "auto")
        if src == "auto":
            return "file" if self.voice_file() else "tts"
        return src


# ---------------------------------------------------------------- 风格

def load_style(name_or_path: str, project_dir: Path | None = None) -> dict:
    cands = []
    if project_dir:
        cands += [project_dir / name_or_path, project_dir / f"{name_or_path}.yaml"]
    cands += [Path(name_or_path), config.STYLES_DIR / f"{name_or_path}.yaml"]
    for p in cands:
        if p.is_file():
            style = yaml.safe_load(p.read_text(encoding="utf-8"))
            base = style.pop("extends", None)
            if base:
                style = deep_merge(load_style(base, project_dir), style)
            return style
    names = sorted(p.stem for p in config.STYLES_DIR.glob("*.yaml"))
    raise ScriptCutError(f"找不到风格 {name_or_path}（内置：{'、'.join(names)}；也可以写项目内 yaml 路径）")


def list_styles() -> list[dict]:
    out = []
    for p in sorted(config.STYLES_DIR.glob("*.yaml")):
        s = yaml.safe_load(p.read_text(encoding="utf-8"))
        out.append({"name": p.stem, "description": s.get("description", ""), "canvas": s.get("canvas"),
                    "layouts": sorted(s.get("layouts", {})), "roles": sorted(s.get("roles", {})),
                    "palette": {k: v.get("meaning", "") for k, v in s.get("palette", {}).items()}})
    return out


def _scale_style(style: dict, W: int, H: int) -> dict:
    """项目改了画布尺寸时，把风格里的像素坐标按比例缩放。"""
    sw, sh = style["canvas"]["width"], style["canvas"]["height"]
    if (sw, sh) == (W, H):
        return style
    kx, ky, k = W / sw, H / sh, min(W / sw, H / sh)
    s = copy.deepcopy(style)
    s["canvas"]["width"], s["canvas"]["height"] = W, H
    x0, y0, x1, y1 = s["content_area"]
    s["content_area"] = [x0 * kx, y0 * ky, x1 * kx, y1 * ky]
    for name, slots in s.get("layouts", {}).items():
        s["layouts"][name] = [[p[0] * kx, p[1] * ky] + ([p[2] * k] if len(p) == 3 else [p[2] * kx, p[3] * ky])
                              for p in slots]
    for r in s.get("roles", {}).values():
        r["size"] = int(r["size"] * k)
    return s


# ---------------------------------------------------------------- 加载

def load(project_dir: Path | str) -> Project:
    d = Path(project_dir).resolve()
    if not d.is_dir():
        raise ScriptCutError(f"项目目录不存在：{d}")
    config.load_env(d)
    pf = d / "project.yaml"
    cfg = deep_merge(DEFAULT_PROJECT, yaml.safe_load(pf.read_text(encoding="utf-8")) if pf.exists() else {})
    sbf = d / "storyboard.yaml"
    if not sbf.exists():
        raise ScriptCutError(f"缺少 {sbf}：先用分镜 skill 生成，或 `scriptcut init` 后编辑")
    raw = yaml.safe_load(sbf.read_text(encoding="utf-8")) or {}
    style = deep_merge(load_style(raw.get("style") or cfg["style"], d), cfg.get("style_overrides"))
    if cfg.get("canvas"):  # 项目改画布：先按比例缩放风格里的坐标，再改帧率等其余字段
        want = {**style["canvas"], **cfg["canvas"]}
        style = _scale_style(style, int(want["width"]), int(want["height"]))
        style["canvas"] = want
    script_rel = raw.get("script") or cfg["script"]
    sp = d / script_rel
    if not sp.exists():
        raise ScriptCutError(f"缺少文案 {sp}")
    script = sp.read_text(encoding="utf-8").strip()
    issues: list[str] = []

    elements: dict[str, Element] = {}
    for eid, e in (raw.get("elements") or {}).items():
        e = e or {}
        if True in e:  # YAML 1.1 把 on 解析成 True
            issues.append(f"{eid}: 出现了键 on（YAML 会当成布尔值），叠加请在镜头 item 里写 over")
        src = str(e.get("src", "") or "")
        kind = e.get("kind") or ("video" if Path(src).suffix.lower() in VIDEO_EXT else "image")
        elements[str(eid)] = Element(
            id=str(eid), kind=kind, role=e.get("role", "word" if kind == "word" else ("media" if src else "icon")),
            name=str(e.get("name", "")), prompt=str(e.get("prompt", "") or ""), accent=str(e.get("accent", "") or ""),
            accent_part=str(e.get("accent_part", "") or ""), label=str(e.get("label", "") or ""),
            text=str(e.get("text", "") or ""), hl=str(e.get("hl", "") or ""), src=src, fit=e.get("fit", "contain"),
            clip_in=float(e.get("clip_in", 0) or 0), volume=float(e.get("volume", 0) or 0), ref=e.get("ref"),
            knockout=e.get("knockout"))

    roles, layouts = style["roles"], style["layouts"]
    shots: list[Shot] = []
    offset = 0
    for k, s in enumerate(raw.get("shots") or []):
        sid = str(s.get("id") or f"S{k + 1:02d}")
        text = str(s.get("text", "") or "")
        layout = s.get("layout") or ("full" if s.get("chapter") else "hero")
        if layout not in layouts:
            issues.append(f"{sid}: 未知版式 {layout}（可选：{'、'.join(layouts)}）")
            layout = next(iter(layouts))
        slots = layouts[layout]
        items: list[Item] = []
        n_slot = 0
        for it in s.get("items") or []:
            if "el" not in it:
                issues.append(f"{sid}: item 缺少 el（素材库编号）：{it}")
                continue
            if True in it:
                issues.append(f"{sid}/{it['el']}: 叠加请写 over: 目标编号（on 会被 YAML 当成布尔值）")
            el = elements.get(str(it["el"]))
            role = el.role if el else "icon"
            r = roles.get(role, roles.get("icon", {"size": 400, "preset": "pop"}))
            over = str(it.get("over", "") or "")
            pos, size, box = None, None, None
            if "pos" in it:
                pos = (float(it["pos"][0]), float(it["pos"][1]))
            elif not over:
                idx = int(it.get("slot", n_slot + 1)) - 1
                if idx >= len(slots):
                    issues.append(f"{sid}: 版式 {layout} 只有 {len(slots)} 个位置，第 {idx + 1} 个元素放不下")
                    idx = len(slots) - 1
                sl = slots[idx]
                pos = (float(sl[0]), float(sl[1]))
                if len(sl) >= 4:
                    box = (float(sl[2]), float(sl[3]))
                elif len(sl) == 3:
                    size = int(sl[2])
            if not over:
                n_slot += 1
            person = role in PERSON_ROLES
            if it.get("size"):
                size = int(it["size"])
            elif person and size:
                size = max(size, int(r["size"]))
            elif not size:
                size = int(r["size"])
            if it.get("box"):
                box = (float(it["box"][0]), float(it["box"][1]))
            preset = it.get("preset") or ("stamp" if over else r.get("preset", "pop"))
            if preset == "slide_auto":
                x = pos[0] if pos else style["canvas"]["width"] / 2
                cx = style["canvas"]["width"] / 2
                preset = "slide_up" if abs(x - cx) < 80 else ("slide_left" if x < cx else "slide_right")
            trig = str(it.get("trigger", "") or "")
            items.append(Item(
                el=str(it["el"]), trigger=trig, trigger_at=text.find(trig) if trig else 0, preset=preset,
                slot=it.get("slot"), pos=pos, size=size, box=box, grow=it.get("grow"), delay=float(it.get("delay", 0)),
                shake=bool(it.get("shake", False)), bleed=bool(it.get("bleed", r.get("bleed", False))), over=over,
                sfx=it.get("sfx"), idle=str(it.get("idle", "none" if over else "auto"))))
        chapter = s.get("chapter")
        if chapter is True:
            chapter = {}
        shots.append(Shot(
            id=sid, text=text, offset=offset, seg=str(s.get("seg", "") or ""), layout=layout, items=items,
            red=[str(w) for w in s.get("red") or []], cap=s.get("cap"),
            marks={str(k2): str(v) for k2, v in (s.get("marks") or {}).items()}, scene=str(s.get("scene", "") or ""),
            sfx=list(s.get("sfx") or []), chapter=chapter, duration=float(s.get("duration", 0) or 0),
            note=str(s.get("note", "") or "")))
        offset += len(text)

    title = raw.get("title") or cfg.get("title") or d.name
    return Project(dir=d, cfg=cfg, style=style, title=title, script=script, elements=elements, shots=shots,
                   facts=list(raw.get("facts") or []), issues=issues)


# ---------------------------------------------------------------- 校验

def validate(p: Project) -> tuple[list[str], list[str]]:
    """→ (errors, warnings)。errors 非空时后续阶段不应继续。"""
    from .audio.sfx import SFX  # 延迟导入，避免循环
    errs, warns = list(p.issues), []
    if not p.shots:
        errs.append("分镜里没有镜头（shots）")
    joined = "".join(s.text for s in p.shots)
    if joined != p.script:
        i = next((k for k, (a, b) in enumerate(zip(joined, p.script)) if a != b), min(len(joined), len(p.script)))
        errs.append(f"分镜文案与原稿不一致（第 {i} 字附近）：分镜「{joined[max(0, i - 8):i + 8]}」/ 原稿「{p.script[max(0, i - 8):i + 8]}」"
                    "——所有镜头 text 按顺序拼接必须与 script.txt 逐字一致（含标点）")
    palette = p.style.get("palette", {})
    max_items = p.style.get("max_items_per_shot", 4)
    ids = set(p.elements)
    used: set[str] = set()
    for eid, e in p.elements.items():
        if e.kind not in KINDS:
            errs.append(f"{eid}: 未知 kind {e.kind}（可选：{'、'.join(KINDS)}）")
        if e.role not in p.style["roles"]:
            warns.append(f"{eid}: 风格里没有角色 {e.role}，按 icon 处理")
        if e.accent and e.accent not in palette:
            errs.append(f"{eid}: 未知点缀色 {e.accent}（可选：{'、'.join(palette)}）")
        if e.kind == "word" or e.role == "word":
            if not e.text:
                errs.append(f"{eid}: 文字素材必须写 text")
            elif e.kind == "image" and not e.src and len(e.text.replace(' ', '')) > WORD_AI_MAX:
                errs.append(f"{eid}: AI 手写字「{e.text}」超过 {WORD_AI_MAX} 字，容易写错，请拆成两个，或改成 kind: word 本地渲染")
            if e.hl and e.hl not in e.text:
                errs.append(f"{eid}: hl「{e.hl}」不在 text「{e.text}」里")
        elif e.needs_ai and not e.prompt:
            errs.append(f"{eid}: 缺少 prompt（画什么）")
        if e.needs_ai and e.role != "word" and e.accent and not e.accent_part:
            warns.append(f"{eid}: 写了 accent 但没写 accent_part（点缀色用在哪）")
        if len(e.label) > LABEL_MAX:
            errs.append(f"{eid}: 标注「{e.label}」超过 {LABEL_MAX} 字；长文字请做成文字素材")
        if e.src and not p.path(e.src).exists():
            warns.append(f"{eid}: 素材文件不存在 {e.src}（先用占位图）")
    sfx_names = set(SFX)
    for s in p.shots:
        if s.silent and s.duration <= 0:
            errs.append(f"{s.id}: 无口播镜头必须写 duration（秒）")
        if s.chapter is not None and s.items:
            warns.append(f"{s.id}: 章节卡镜头里的元素会叠在整屏章节卡上；通常章节卡单独一镜（无口播 duration，或只放章节标题那句话）")
        if len(s.items) > max_items:
            errs.append(f"{s.id}: {len(s.items)} 个元素，超过上限 {max_items}")
        if s.scene and not (p.dir / "scenes" / f"{s.scene}.js").exists():
            errs.append(f"{s.id}: 自定义画面 scenes/{s.scene}.js 不存在")
        shot_ids = [it.el for it in s.items]
        for it in s.items:
            tag = f"{s.id}/{it.el}"
            used.add(it.el)
            if it.el not in ids:
                errs.append(f"{tag}: 素材库里没有 {it.el}")
            if it.trigger and it.trigger_at < 0:
                errs.append(f"{tag}: trigger「{it.trigger}」不在镜头文案「{s.text}」里")
            if it.preset not in PRESETS:
                errs.append(f"{tag}: 未知入场预设 {it.preset}（可选：{'、'.join(sorted(PRESETS))}、slide_auto）")
            if it.preset == "grow" and it.grow not in ("left", "right", "up", "down"):
                errs.append(f"{tag}: grow 预设需要 grow: left/right/up/down")
            if it.idle not in IDLE_KINDS:
                errs.append(f"{tag}: 未知待机动效 {it.idle}（可选：{'、'.join(IDLE_KINDS)}）")
            if it.over and (it.over not in shot_ids or it.over == it.el):
                errs.append(f"{tag}: over「{it.over}」必须是本镜另一个元素")
            if it.sfx and it.sfx not in sfx_names:
                errs.append(f"{tag}: 未知音效 {it.sfx}（可选：{'、'.join(sorted(sfx_names))}）")
        if s.items and not s.silent and not s.scene:
            first = min(it.trigger_at for it in s.items if it.trigger_at >= 0) if any(
                it.trigger_at >= 0 for it in s.items) else 0
            if first > 6:
                warns.append(f"{s.id}: 第一个元素的 trigger 在第 {first} 个字，换镜后可能有空画面")
        for w in s.red:
            if w not in s.text:
                errs.append(f"{s.id}: 标红词「{w}」不在镜头文案中")
        for name, w in s.marks.items():
            if w not in s.text:
                errs.append(f"{s.id}: marks.{name}「{w}」不在镜头文案中")
        for fx in s.sfx:
            if fx.get("name") not in sfx_names:
                errs.append(f"{s.id}: 未知音效 {fx.get('name')}")
            if isinstance(fx.get("at"), str) and fx["at"] not in s.text:
                errs.append(f"{s.id}: 音效 at「{fx['at']}」不在镜头文案中")
    if p.style.get("caption", {}).get("enabled"):
        try:
            cap_mod.split_captions(p.script, [(s.offset, s.offset + len(s.text)) for s in p.shots],
                                   [s.cap for s in p.shots], max_line=p.style["caption"].get("max_line", 12))
        except ValueError as e:
            errs.append(f"标题断句：{e}")
    for eid in sorted(ids - used):
        warns.append(f"{eid}: 素材库里定义了但没有镜头使用")
    ref = p.cfg.get("character", {}).get("ref")
    if ref and not p.path(ref).exists():
        warns.append(f"人物参考图不存在：{ref}")
    return errs, warns


def summary(p: Project) -> dict:
    from . import timing as timing_mod
    est = timing_mod.estimate_total(p)
    n_ai = sum(e.needs_ai for e in p.elements.values())
    return {"title": p.title, "style": p.style.get("name"), "canvas": p.style["canvas"], "shots": len(p.shots),
            "elements": len(p.elements), "ai_images": n_ai,
            "ai_missing": sum(1 for e in p.elements.values() if e.needs_ai and not (p.dir / "assets" / f"{e.id}.png").exists()),
            "chars": len(p.script), "estimated_seconds": round(est, 1),
            "avg_shot_seconds": round(est / max(1, len(p.shots)), 2)}
