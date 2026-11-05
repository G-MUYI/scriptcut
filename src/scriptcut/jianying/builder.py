"""timeline.json → 可编辑的剪映草稿（pyJianYingDraft 0.3.0）。

editable 模式（默认）——草稿里每样东西都是剪映原生对象，打开就能改：
  背景          图片片段（可替换）
  章节卡        底图 + 原生文本（PART / 序号 / 标题）
  图片 / 视频元素 原始素材片段 + 关键帧（入场动画、落定后缓慢放大 / 上下浮动、震屏都是关键帧，可在剪映里调整或删掉；
                「替换素材」会保留位置和动画——占位图就这样换成正式素材）
  文字元素      原生文本（字、颜色、字号、字体都可改；关键词高亮是同一段文本里的分段颜色）+ 关键帧
  顶部逐字标题  原生文本 + 「逐字显影」入场动画，关键词标红
  底部字幕      原生文本（可批量改样式）
  栏目条        原生文本
  配音 / 每条音效 / 配乐  各自独立的音频片段（音效一条一个，可单独挪动、换掉、调音量）
  自定义代码画面 只有这一类是渲染好的视频片段（只含代码画面本身，元素和文字仍是原生的）
flat 模式——Canvas 成片画面（不可编辑）+ 可编辑字幕 + 配音 / 配乐 / 音效分轨，适合只想在剪映里加字幕、调音量的场景。

安全规则（来自原型踩坑）：
- 剪映打开过的草稿会加密 draft_content.json 并生成 Timelines/，之后不能再用明文改写；
  所以只覆盖「本工具生成（有 scriptcut.json 标记）且没有 Timelines/」的草稿，其余同名草稿一律报错（--force 例外）。
- 配音统一用 wav（mp3 带封面图时剪映会当成视频解析）。
- 草稿引用素材的绝对路径：生成后不要移动项目目录（或重新生成）。
"""

from __future__ import annotations

import json
import math
import random
import shutil
from pathlib import Path

from PIL import Image, ImageFont

from .. import assets as assets_mod
from .. import config
from ..audio import sfx as sfx_mod
from ..project import Project
from ..util import ScriptCutError, probe_video, read_json, wav_duration, write_json

US = 1_000_000
MARKER = "scriptcut.json"
EM_PER_SIZE = 0.0060   # 剪映字号 → 像素：字高 ≈ 字号 × 缩放 × 画布短边 × 0.006（用剪映渲染结果校准）
TOL = {"KFTypePositionX": 0.0015, "KFTypePositionY": 0.0010, "KFTypeScaleX": 0.004, "KFTypeScaleY": 0.004,
       "KFTypeRotation": 0.3, "KFTypeAlpha": 0.01}


def _draft():
    try:
        import pyJianYingDraft as draft
    except ImportError as e:
        raise ScriptCutError("生成剪映草稿需要 pyJianYingDraft：pip install pyJianYingDraft==0.3.0") from e
    return draft


def _us(t: float) -> int:
    return int(round(t * US))


def _hex01(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """去掉线性插值就能还原的关键帧（误差 ≤ tol），让剪映里的关键帧干净可改。首尾一定保留。"""
    if len(points) <= 2:
        return points
    out = [points[0]]
    i = 0
    while i < len(points) - 1:
        j = i + 1
        while j + 1 < len(points):
            (t0, v0), (t1, v1) = points[i], points[j + 1]
            ok = all(abs(v0 + (v1 - v0) * (tk - t0) / max(t1 - t0, 1e-9) - vk) <= tol for tk, vk in points[i + 1:j + 1])
            if not ok:
                break
            j += 1
        out.append(points[j])
        i = j
    return out


def rich_text_class(draft):
    """带分段颜色的文本片段：同一段文字里关键词换色（剪映的文本 styles 按字符区间分段）。"""

    class RichText(draft.TextSegment):
        def __init__(self, text, timerange, runs=None, **kw):
            super().__init__(text, timerange, **kw)
            self.runs = runs or []   # [(起, 止, (r, g, b))]，区间外用 style.color

        def export_material(self):
            ret = super().export_material()
            if not self.runs:
                return ret
            content = json.loads(ret["content"])
            base = content["styles"][0]
            styles, pos = [], 0
            for a, b, color in sorted(self.runs) + [(len(self.text), len(self.text), None)]:
                if a > pos:
                    st = json.loads(json.dumps(base))
                    st["range"] = [pos, a]
                    styles.append(st)
                if b > a and color is not None:
                    st = json.loads(json.dumps(base))
                    st["range"] = [a, b]
                    st["fill"]["content"]["solid"]["color"] = list(color)
                    styles.append(st)
                pos = max(pos, b)
            content["styles"] = styles
            ret["content"] = json.dumps(content, ensure_ascii=False)
            return ret

    return RichText


class Builder:
    def __init__(self, p: Project, tl: dict, name: str, root: Path):
        self.d = _draft()
        self.RichText = rich_text_class(self.d)
        self.p, self.tl, self.name, self.root = p, tl, name, root
        self.W, self.H, self.fps = tl["width"], tl["height"], tl["fps"]
        self.short = min(self.W, self.H)
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.script = None
        self.tracks: dict[str, object] = {}
        self.sfx_busy: list[list[tuple[float, float]]] = []
        jy = p.style.get("jianying", {}) or {}
        self.font = self._font(jy.get("font"))
        self.caption_font = self._font(jy.get("caption_font")) or self.font
        self.subtitle_font = self._font(jy.get("subtitle_font")) or self.font
        self.local_font = str(assets_mod.font_path(p))
        self.stats = {"image": 0, "text": 0, "video": 0, "keyframes": 0, "sfx": 0, "scene_clips": 0}

    # ---------- 基础 ----------

    def _font(self, name):
        if not name:
            return None
        ft = getattr(self.d.FontType, str(name).replace("-", "_").replace(" ", "_"), None)
        if ft is None:
            self.warnings.append(f"剪映字体库里没有「{name}」，改用剪映默认字体")
        return ft

    def create(self, force: bool):
        path = self.root / self.name
        if path.exists() and not force:
            if not (path / MARKER).exists():
                raise ScriptCutError(f"剪映草稿目录里已有同名草稿（不是本工具生成的）：{path}。换个 --name，或加 --force 覆盖")
            if (path / "Timelines").exists():
                raise ScriptCutError(f"草稿 {self.name} 已经在剪映里打开过（会被加密），不能再覆盖。换个 --name 重新生成")
        self.root.mkdir(parents=True, exist_ok=True)
        self.script = self.d.DraftFolder(str(self.root)).create_draft(self.name, self.W, self.H, self.fps, allow_replace=True)
        write_json(path / MARKER, {"generator": "scriptcut", "project": str(self.p.dir), "status": "building"})

    def track(self, kind: str, name: str):
        if name not in self.tracks:
            tt = {"video": self.d.TrackType.video, "audio": self.d.TrackType.audio, "text": self.d.TrackType.text}[kind]
            self.tracks[name] = self.script.append_track(self.d.TrackSpec(tt, name))
        return name

    def norm_x(self, x: float) -> float:
        return (x - self.W / 2) / (self.W / 2)

    def norm_y(self, y: float) -> float:
        return (self.H / 2 - y) / (self.H / 2)

    def img_scale(self, target_w: float, mw: int, mh: int) -> float:
        """剪映把素材等比适应画布后再乘缩放：缩放 = 目标宽 / 适应后的宽。"""
        return target_w / (mw * min(self.W / mw, self.H / mh))

    def text_size(self, px: float) -> float:
        return round(px / (self.short * EM_PER_SIZE), 2)

    # ---------- 关键帧 ----------

    def animate(self, seg, it: dict, dur: float, base_scale: float, shakes: list[float], text: bool = False):
        """入场采样帧 + 落定待机 + 震屏 → 每个属性一条时间曲线 → 精简后写成剪映关键帧（绝对值）。"""
        KF = self.d.KeyframeProperty
        frames = it["anim"]["frames"] or [[0, 0, 1, 1, 0, 1, 0]]
        adur = min(it["anim"]["dur"], dur)
        n = len(frames) - 1
        idle = it.get("idle", "none")
        cfg = self.tl["style"].get("idle", {})
        x0, y0 = it["x"], it["y"]
        grow = it.get("grow") if it["preset"] == "grow" else None

        def at(t):
            if n > 0 and t < it["anim"]["dur"]:
                k = t * self.fps
                i = min(int(k), n - 1)
                a, b = frames[i], frames[i + 1]
                f = [u + (v - u) * (k - i) for u, v in zip(a, b)]
            else:
                f = list(frames[-1])
            dx, dy, sx, sy, rot, alpha = f[:6]
            if grow:  # 方向生长：沿方向从 0 伸展，另一端钉住（剪映里等价于缩放 + 位移关键帧）
                q = 1 - (1 - min(1.0, t / max(it["anim"]["dur"], 1e-3))) ** 3
                q = max(q, 0.002)
                if grow in ("right", "left"):
                    sx = q
                    dx = (-1 if grow == "right" else 1) * it["w"] * (1 - q) / 2
                else:
                    sy = q
                    dy = (1 if grow == "up" else -1) * it["h"] * (1 - q) / 2
            held = t - it["anim"]["dur"]
            zs, fy = 1.0, 0.0
            if held > 0:
                if idle == "zoom":
                    zs = 1 + min(cfg.get("zoom_max", 0.06), cfg.get("zoom_rate", 0.02) * held)
                elif idle == "float":
                    amp, per = cfg.get("float_px", 10), cfg.get("float_period", 2.8)
                    fy = -amp * (1 - math.cos(2 * math.pi * held / per)) / 2
            sh_x = sh_y = 0.0
            for k2, s0 in enumerate(shakes):
                d = it["appear"] + t - s0
                if 0 <= d < 0.28:
                    r = random.Random(int(d * 15) + k2 * 97 + 1)
                    decay = (1 - d / 0.28) ** 2
                    sh_x, sh_y = (r.random() * 2 - 1) * 14 * decay, (r.random() * 2 - 1) * 14 * decay
            return {"KFTypePositionX": self.norm_x(x0 + dx + sh_x), "KFTypePositionY": self.norm_y(y0 + dy + fy + sh_y),
                    "KFTypeScaleX": base_scale * sx * zs, "KFTypeScaleY": base_scale * sy * zs,
                    "KFTypeRotation": rot, "KFTypeAlpha": max(0.0, min(1.0, alpha))}

        times = {round(i / self.fps, 4) for i in range(int(adur * self.fps) + 1)} | {round(adur, 4), 0.0, round(dur, 4)}
        if idle == "float":
            step = cfg.get("float_step", 0.1)
            times |= {round(adur + i * step, 4) for i in range(int((dur - adur) / step) + 1)}
        for s0 in shakes:
            rel_t = s0 - it["appear"]
            if 0 <= rel_t < dur:
                times |= {round(rel_t + k / self.fps, 4) for k in range(-1, 10) if 0 <= rel_t + k / self.fps <= dur}
        times = sorted(t for t in times if 0 <= t <= dur)
        samples = [(t, at(t)) for t in times]
        props = {"KFTypePositionX": KF.position_x, "KFTypePositionY": KF.position_y, "KFTypeScaleX": KF.scale_x,
                 "KFTypeScaleY": KF.scale_y, "KFTypeRotation": KF.rotation}
        if not text:  # 文本片段的不透明度关键帧剪映不生效
            props["KFTypeAlpha"] = KF.alpha
        for key, prop in props.items():
            pts = [(t, v[key]) for t, v in samples]
            if max(v for _, v in pts) - min(v for _, v in pts) <= TOL[key]:
                continue  # 全程不变：只用片段的静态设置
            for t, v in simplify(pts, TOL[key]):
                seg.add_keyframe(prop, _us(t), v)
                self.stats["keyframes"] += 1

    # ---------- 图层 ----------

    def layout_tracks(self):
        """先按叠放顺序建好所有轨道（剪映按轨道顺序叠放：先建的在下）。"""
        shots = self.tl["shots"]
        n_img = n_txt = 0
        for s in shots:
            ki = kt = 0
            for it in s["items"]:
                if it.get("chapter"):
                    continue
                if self._is_word(it):
                    kt += 1
                else:
                    ki += 1
            n_img, n_txt = max(n_img, ki), max(n_txt, kt)
        self.track("video", "背景")
        if any(s.get("chapter") for s in shots):
            self.track("video", "章节底图")
        if any(s.get("scene") for s in shots):
            self.track("video", "代码画面")
        for i in range(n_img):
            self.track("video", f"图层{i + 1}")
        if any(s.get("chapter") for s in shots):  # 章节卡最多三行文字（PART / 序号 / 标题）各一条轨
            for name in ("章节标签", "章节序号", "章节标题"):
                self.track("text", name)
        for i in range(n_txt):
            self.track("text", f"文字{i + 1}")

    def _is_word(self, it: dict) -> bool:
        e = self.p.elements.get(it["el"])
        return bool(e and e.kind == "word")

    def background(self):
        bg = self.p.dir / self.tl["background_image"]
        self.script.add_segment(self.d.VideoSegment(str(bg), self.d.trange(0, _us(self.tl["duration"]))), "背景")
        self.stats["image"] += 1

    def chapter(self, s: dict):
        ch = s["chapter"]
        cs = self.tl["style"].get("chapter", {})
        a, b = s["start"], s["end"]
        tr = self.d.trange(_us(a), _us(b - a))
        bg = assets_mod.render_chapter(self.p, None, ch["n"], ch["title"],
                                       self.p.build / "jianying" / "assets" / f"chapter_{s['id']}.png", with_text=False)
        seg = self.d.VideoSegment(str(bg), tr)
        seg.add_animation(self.d.IntroType.渐显, _us(0.3))
        self.script.add_segment(seg, "章节底图")
        k = self.short / 1080
        npx, tpx = cs.get("number_px", 220) * k, cs.get("title_px", 96) * k
        cy = self.H * 0.42
        acc, fg = _hex01(cs.get("accent", "#F2C230")), _hex01(cs.get("fg", "#FFFFFF"))
        lines = [x for x in ch["title"].split("|") if x]
        items = []
        if ch.get("n"):
            items += [("章节标签", f"PART {ch['n']}", max(24, npx / 6), acc, cy - npx * 0.95),
                      ("章节序号", ch["n"], npx, acc, cy)]
        items.append(("章节标题", "\n".join(lines), tpx, fg, cy + npx * 0.85 + tpx * 1.3 * (len(lines) - 1) / 2))
        for track, text, px, color, y in items:
            seg = self.d.TextSegment(text, tr, font=self.font, style=self.d.TextStyle(
                size=self.text_size(px), bold=True, color=color, align=1, line_spacing=2),
                clip_settings=self.d.ClipSettings(transform_y=self.norm_y(y)))
            seg.add_animation(self.d.TextIntro.渐显, _us(0.4))
            self.script.add_segment(seg, track)
            self.stats["text"] += 1

    def items(self, scene_clips: dict):
        for s in self.tl["shots"]:
            if s.get("chapter"):
                self.chapter(s)
            if s.get("scene"):
                clip = scene_clips.get(s["id"])
                if clip:
                    vm = self.d.VideoMaterial(str(clip))
                    dur = min(vm.duration, _us(s["end"] - s["start"]))
                    self.script.add_segment(self.d.VideoSegment(vm, self.d.trange(_us(s["start"]), dur), volume=0), "代码画面")
                    self.stats["scene_clips"] += 1
            ki = kt = 0
            for it in s["items"]:
                if it.get("chapter"):
                    continue
                t, end = it["appear"], it["end"]
                if end - t < 0.05:
                    continue
                if self._is_word(it):
                    kt += 1
                    self.word(it, f"文字{kt}", s.get("shakes", []))
                    continue
                ki += 1
                tname = f"图层{ki}"
                src = self.p.dir / it["src"]
                if it["kind"] == "video":
                    _, vw, vh = probe_video(src)
                    sc = self.img_scale(it["w"], vw or it["w"], vh or it["h"])
                    seg = self.d.VideoSegment(str(src), self.d.trange(_us(t), _us(end - t)),
                                              source_timerange=self.d.trange(_us(it.get("clip_in", 0)), _us(end - t)),
                                              volume=float(it.get("volume", 0)),
                                              clip_settings=self.d.ClipSettings(scale_x=sc, scale_y=sc,
                                                                                transform_x=self.norm_x(it["x"]),
                                                                                transform_y=self.norm_y(it["y"])))
                    self.stats["video"] += 1
                else:
                    with Image.open(src) as im:
                        mw, mh = im.size
                    sc = self.img_scale(it["w"], mw, mh)
                    seg = self.d.VideoSegment(str(src), self.d.trange(_us(t), _us(end - t)),
                                              clip_settings=self.d.ClipSettings(scale_x=sc, scale_y=sc,
                                                                                transform_x=self.norm_x(it["x"]),
                                                                                transform_y=self.norm_y(it["y"])))
                    self.stats["image"] += 1
                self.animate(seg, it, end - t, sc, s.get("shakes", []))
                self.script.add_segment(seg, tname)

    def word(self, it: dict, track: str, shakes: list[float]):
        """文字元素 → 原生文本：字号按画面上的像素反推，关键词用分段颜色。"""
        e = self.p.elements[it["el"]]
        ws = self.p.style.get("word", {})
        with Image.open(self.p.dir / it["src"]) as im:
            nat_w = im.width
        px = float(ws.get("font_px", 150)) * it["w"] / max(1, nat_w)
        text = e.text.replace("|", "\n")
        lm = int(ws.get("line_max", 8))
        if "|" not in e.text and len(text) > lm:
            cut = -(-len(text) // 2)
            text = text[:cut] + "\n" + text[cut:]
        ink = _hex01(ws.get("ink", "#222222"))
        acc = _hex01(assets_mod.accent_hex(self.p, e.accent, ws.get("ink", "#D2412F")))
        flat = text.replace("\n", "")
        runs = []
        if e.hl and e.hl in flat:
            k0 = flat.find(e.hl)
            # 换行符也占一个字符位置：把扁平下标映射回带换行的下标
            idx = [i for i, c in enumerate(text) if c != "\n"]
            a, b = idx[k0], idx[k0 + len(e.hl) - 1] + 1
            runs = [(a, b, acc)]
            base = ink
        else:
            base = acc if e.accent else ink
        seg = self.RichText(text, self.d.trange(_us(it["appear"]), _us(it["end"] - it["appear"])), runs=runs,
                            font=self.font, style=self.d.TextStyle(size=self.text_size(px), bold=True, color=base, align=1),
                            clip_settings=self.d.ClipSettings(transform_x=self.norm_x(it["x"]), transform_y=self.norm_y(it["y"])))
        self.animate(seg, it, it["end"] - it["appear"], 1.0, shakes, text=True)
        self.script.add_segment(seg, track)
        self.stats["text"] += 1

    def captions(self):
        cs = self.tl["style"].get("caption", {})
        if not cs.get("enabled") or not self.tl["captions"]:
            return
        name = self.track("text", "标题")
        px = float(cs.get("font_px", 62))
        font = ImageFont.truetype(self.local_font, int(px))
        lh = px * float(cs.get("line_height", 1.3))
        ink, red = _hex01(cs.get("ink", "#1C1C1C")), _hex01(cs.get("red", "#C8202E"))
        for c in self.tl["captions"]:
            lines = ["".join(ch["ch"] for ch in ln) for ln in c["lines"]]
            text = "\n".join(lines)
            flags = [ch["red"] for ln in c["lines"] for ch in ln]
            runs, k, pos = [], 0, 0
            for ln in c["lines"]:
                for ch in ln:
                    if flags[k]:
                        runs.append((pos, pos + 1, red))
                    k += 1
                    pos += 1
                pos += 1  # 换行符
            w = max(font.getlength(x) for x in lines)
            h = lh * len(lines)
            cx = self.W / 2 if cs.get("align") == "center" else cs["x"] + w / 2
            cy = cs["y"] + h / 2 + px * 0.15
            seg = self.RichText(text, self.d.trange(_us(c["start"]), _us(c["end"] - c["start"])), runs=runs,
                                font=self.caption_font,
                                style=self.d.TextStyle(size=self.text_size(px), bold=True, color=ink,
                                                       align=1 if cs.get("align") == "center" else 0, line_spacing=2),
                                clip_settings=self.d.ClipSettings(transform_x=self.norm_x(cx), transform_y=self.norm_y(cy)))
            ts = [ch["t"] for ln in c["lines"] for ch in ln]
            reveal = max(0.2, min(c["end"] - c["start"], (max(ts) - c["start"]) + float(cs.get("reveal", 0.12))))
            seg.add_animation(self.d.TextIntro.逐字显影, _us(reveal))
            self.script.add_segment(seg, name)
            self.stats["text"] += 1
        self.notes.append("顶部标题用剪映「逐字显影」入场动画近似逐字出现（时长 = 这句话念完的时长），关键词标红是文本分段颜色")

    def hud(self):
        hud = self.tl["style"].get("hud", {})
        if not hud.get("enabled"):
            return
        k = self.short / 1080
        y = self.H * (0.065 if self.W < self.H else 0.07)
        name = self.track("text", "栏目条")
        spans, a = [], 0.0
        for s in self.tl["shots"]:
            if s.get("chapter"):
                if s["start"] > a:
                    spans.append((a, s["start"]))
                a = s["end"]
        if self.tl["duration"] > a:
            spans.append((a, self.tl["duration"]))
        font = ImageFont.truetype(self.local_font, int(30 * k))
        title = hud.get("title") or self.tl.get("title", "")
        w = font.getlength(title)
        for a, b in spans:
            if b - a < 0.1:
                continue
            seg = self.d.TextSegment(title, self.d.trange(_us(a), _us(b - a)), font=self.font,
                                     style=self.d.TextStyle(size=self.text_size(30 * k), bold=True, color=_hex01(hud.get("color", "#4A5566"))),
                                     clip_settings=self.d.ClipSettings(transform_x=self.norm_x(64 * k + w / 2), transform_y=self.norm_y(y)))
            self.script.add_segment(seg, name)
            self.stats["text"] += 1
        chs = self.tl.get("chapters") or []
        if not chs:
            return
        cname = self.track("text", "章节角标")
        bounds = [c["start"] for c in chs] + [self.tl["duration"]]
        for c, nxt in zip(chs, bounds[1:]):
            shot = next(s for s in self.tl["shots"] if s["id"] == c["shot"])
            a, b = shot["end"], nxt
            if b - a < 0.1:
                continue
            label = f"{c['n']} {c.get('short') or c['title']}".replace("|", "").strip()
            lw = font.getlength(label) * 28 / 30
            seg = self.d.TextSegment(label, self.d.trange(_us(a), _us(b - a)), font=self.font,
                                     style=self.d.TextStyle(size=self.text_size(28 * k), bold=True, color=_hex01(hud.get("accent", "#F28C28"))),
                                     background=self.d.TextBackground(color="#FFFFFF", round_radius=0.5, height=0.4, width=0.3),
                                     clip_settings=self.d.ClipSettings(transform_x=self.norm_x(self.W - 64 * k - lw / 2 - 20 * k),
                                                                       transform_y=self.norm_y(y)))
            self.script.add_segment(seg, cname)
            self.stats["text"] += 1

    def subtitles(self, force: bool = False):
        ss = self.tl["style"].get("subtitle", {})
        subs = self.tl.get("subtitles") or []
        if not subs and force:
            srt = self.p.dir / self.tl["srt"]
            if srt.exists():
                subs = _parse_srt(srt.read_text(encoding="utf-8"))
        if not subs:
            return
        name = self.track("text", "字幕")
        px = float(ss.get("font_px", 54))
        style = self.d.TextStyle(size=self.text_size(px), bold=True, color=_hex01(ss.get("color", "#FFFFFF")), align=1,
                                 auto_wrapping=True, max_line_width=0.86)
        stroke = float(ss.get("stroke", 8))
        border = self.d.TextBorder(color=_hex01(ss.get("stroke_color", "#000000")), width=min(100.0, stroke * 4)) if stroke else None
        y = float(ss.get("y", self.H * 0.88))
        clip = self.d.ClipSettings(transform_y=self.norm_y(y))
        for c in subs:
            if c["end"] - c["start"] < 0.05:
                continue
            self.script.add_segment(self.d.TextSegment(c["text"], self.d.trange(_us(c["start"]), _us(c["end"] - c["start"])),
                                                       font=self.subtitle_font, style=style, clip_settings=clip, border=border), name)
            self.stats["text"] += 1

    def voice(self):
        v = self.tl.get("voice")
        if not v or not (self.p.dir / v).exists():
            self.warnings.append("没有配音：草稿里没有配音轨（先运行 voice）")
            return
        m = self.d.AudioMaterial(str(self.p.dir / v))
        self.script.add_segment(self.d.AudioSegment(m, self.d.trange(0, min(m.duration, _us(self.tl["duration"])))),
                                self.track("audio", "配音"))

    def sfx(self):
        out = self.p.build / "audio" / "sfx"
        cfg = {**self.p.style.get("sfx", {}), **(self.p.cfg.get("audio", {}).get("sfx") or {})}
        if not cfg.get("enabled", True):
            return
        lib = sfx_mod.build_library(out, sorted({c["name"] for c in self.tl["cues"] if c["name"] in sfx_mod.SFX}) or None)
        master, gains = float(cfg.get("master", 0.6)), cfg.get("gain", {}) or {}
        for c in self.tl["cues"]:
            if c["name"] not in lib:
                continue
            path = lib[c["name"]]
            length = wav_duration(path)
            at = c["t"] - (sfx_mod.peak_offset(sfx_mod.get(c["name"])) if c["name"] in sfx_mod.PEAK_ALIGN else 0)
            at = max(0.0, at)
            end = min(at + length, self.tl["duration"])
            if end - at < 0.02:
                continue
            for idx, busy in enumerate(self.sfx_busy):
                if all(end <= a or at >= b for a, b in busy):
                    break
            else:
                idx = len(self.sfx_busy)
                self.sfx_busy.append([])
            self.sfx_busy[idx].append((at, end))
            vol = master * float(gains.get(c["name"], 0.7)) * float(c.get("gain", 1.0))
            self.script.add_segment(self.d.AudioSegment(str(path), self.d.trange(_us(at), _us(end - at)), volume=vol),
                                    self.track("audio", f"音效{idx + 1}"))
            self.stats["sfx"] += 1

    def stem(self, file: Path, name: str, volume: float = 1.0):
        if not file.exists():
            return
        m = self.d.AudioMaterial(str(file))
        self.script.add_segment(self.d.AudioSegment(m, self.d.trange(0, min(m.duration, _us(self.tl["duration"]))), volume=volume),
                                self.track("audio", name))

    def music(self):
        mus = {**self.p.style.get("music", {}), **(self.p.cfg.get("audio", {}).get("music") or {})}
        if not mus.get("enabled"):
            return
        stem = self.p.build / "audio" / "stem_music.wav"
        if not stem.exists():
            self.warnings.append("配乐已开启但还没有混音结果：先运行 audio，草稿里暂无配乐轨")
            return
        self.stem(stem, "配乐")

    def save(self, mode: str):
        self.script.save()
        write_json(self.root / self.name / MARKER, {"generator": "scriptcut", "project": str(self.p.dir), "mode": mode,
                                                     "timeline": self.tl.get("hash")})


def _parse_srt(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        a, b = [x.strip() for x in lines[1].split("-->")]

        def sec(s):
            h, m, rest = s.split(":")
            sx, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(sx) + int(ms) / 1000
        out.append({"start": sec(a), "end": sec(b), "text": "\n".join(lines[2:])})
    return out


def _preview_sheet(p: Project, tl: dict, draft: Path) -> str:
    import io

    from ..render.canvas import _sheet, settle_time
    from ..util import rel
    from .preview import DraftPreview
    pv = DraftPreview(draft, str(assets_mod.font_path(p)))
    imgs = []
    for s in tl["shots"]:
        buf = io.BytesIO()
        t = settle_time(s)
        pv.frame(t).save(buf, "PNG")
        imgs.append((f"{s['id']} {t:.1f}s", buf.getvalue()))
    portrait = tl["height"] > tl["width"]
    cols, scale = (8, 0.25) if portrait else (5, 0.2)
    out = _sheet(imgs, cols, p.build / "jianying" / "draft_preview.jpg", tl["width"], tl["height"], scale)
    return rel(out, p.dir)


def preview(p: Project, times: list[float], name: str | None = None, draft_dir: Path | None = None) -> dict:
    """按剪映规则合成草稿在指定时刻的画面（检查位置 / 时序 / 叠放用）。"""
    from ..util import rel
    from .preview import render_frames
    root = Path(draft_dir) if draft_dir else config.jianying_drafts_dir(p.dir)
    draft = root / (name or f"scriptcut_{p.name}")
    files = render_frames(draft, times, str(assets_mod.font_path(p)), p.build / "jianying" / "preview")
    return {"files": [rel(f, p.dir) for f in files]}


def build(p: Project, mode: str = "editable", name: str | None = None, draft_dir: Path | None = None,
          force: bool = False, preview_sheet: bool = True) -> dict:
    if mode == "layers":
        mode = "editable"
    tl = read_json(p.build / "timeline.json")
    if not tl:
        raise ScriptCutError("还没有 build/timeline.json：先运行 timeline")
    root = Path(draft_dir) if draft_dir else config.jianying_drafts_dir(p.dir)
    name = name or f"scriptcut_{p.name}"
    b = Builder(p, tl, name, root)
    if mode == "flat":
        from ..render import canvas
        video = p.build / "jianying" / f"flat_{tl.get('hash', '')}.mp4"
        if not video.exists():
            canvas.render_clip(p, 0, tl["duration"], video, subs=False)
        b.create(force)
        vm = b.d.VideoMaterial(str(video))
        b.script.add_segment(b.d.VideoSegment(vm, b.d.trange(0, vm.duration), volume=0), b.track("video", "画面"))
        b.subtitles(force=not tl["style"].get("caption", {}).get("enabled"))  # 有顶部逐字标题时它已在画面里
        b.voice()
        b.music()
        b.stem(p.build / "audio" / "stem_sfx.wav", "音效")
        b.notes.append("flat 模式：画面是渲染好的视频（不可逐元素编辑）；字幕、配音、配乐、音效可编辑。要逐元素编辑请用默认的 editable 模式")
    elif mode == "editable":
        clips = {}
        scene_shots = [s for s in tl["shots"] if s.get("scene")]
        if scene_shots:
            from ..render import canvas
            for s in scene_shots:
                out = p.build / "jianying" / "scenes" / f"{s['id']}_{tl.get('hash', '')}.mp4"
                if not out.exists():
                    try:
                        canvas.render_clip(p, s["start"], s["end"], out, subs=False, only_scene=True)
                    except ScriptCutError as e:
                        b.warnings.append(f"{s['id']}: 自定义画面没法预渲染（{e}），草稿里这段只有原生元素")
                        continue
                clips[s["id"]] = out
            b.notes.append(f"{len(clips)} 个自定义代码画面镜头（{'、'.join(clips)}）是渲染好的视频片段；其上的元素和文字仍是原生可编辑的")
        b.create(force)
        b.layout_tracks()
        b.background()
        b.items(clips)
        b.captions()
        b.hud()
        b.subtitles()
        b.voice()
        b.sfx()
        b.music()
        b.notes.append("图片元素的入场 / 待机动画是关键帧：选中片段即可在剪映里调整；「替换素材」会保留位置和动画")
        if any(i.get("placeholder") for s in tl["shots"] for i in s["items"]):
            ph = sorted({i["el"] for s in tl["shots"] for i in s["items"] if i.get("placeholder")})
            b.notes.append(f"仍是占位图的素材：{'、'.join(ph)}——出图后重新生成草稿，或在剪映里对这些片段「替换素材」")
    else:
        raise ScriptCutError(f"未知模式 {mode}（editable / flat）")
    b.save(mode)
    preview = None
    if preview_sheet:  # 不开剪映也能检查：按剪映规则把每镜落定时刻合成成总览图
        try:
            preview = _preview_sheet(p, tl, root / name)
        except Exception as e:  # 预览失败不影响草稿本身
            b.warnings.append(f"草稿预览图生成失败：{e}")
    res = {"draft": str(root / name), "mode": mode, "duration": tl["duration"], "tracks": list(b.tracks),
           "native": b.stats, "preview": preview, "notes": b.notes, "warnings": b.warnings,
           "open": "打开剪映专业版 → 草稿列表里找到这个草稿；草稿引用项目里的素材，不要移动项目目录"
                   + ("" if str(root).find("JianyingPro") >= 0 else "。当前不是剪映草稿目录：把整个草稿文件夹复制到剪映的草稿目录（或用 --draft-dir 指定）后重启剪映")}
    write_json(p.build / "report" / "jianying.json", res)
    return res


def copy_into(src_draft: Path, drafts_root: Path) -> Path:
    """把 build/jianying 里生成的草稿复制进剪映草稿目录（在别的电脑上生成、拷到剪映电脑时用）。"""
    dst = drafts_root / src_draft.name
    shutil.copytree(src_draft, dst, dirs_exist_ok=True)
    return dst
