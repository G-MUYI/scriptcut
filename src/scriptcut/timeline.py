"""主时间轴（build/timeline.json）：把分镜 + 逐字时间 + 素材尺寸算成两个渲染后端都能直接用的绝对时间轴。

规则（来自四个原型的共同经验）：
- 配音是主时钟：换镜点 = 下一镜第一个字的开口时间 − cut_lead；全片 = 配音 + 收尾。镜头多少不影响总时长。
- 元素在 trigger 那几个字念出时入场；入场太晚（展示不足 min_show）就提前入场，而不是推迟换镜。
- 镜头开头超过 blank_warn 秒没有元素会提示「开头空画面」。
- 元素位置在这里一次算好（版式槽位 → 收进内容区 / 允许出画），并按真实可见像素检查遮挡与拥挤。
- 音效提示点：入场预设自带音效（同一时刻只留一个）+ 镜头里手写的 sfx + 章节卡 + 自定义画面导出的 cues。

结构见 docs/schema.md。
"""

from __future__ import annotations

import math
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from . import assets as assets_mod
from . import captions as cap_mod
from . import timing as tmod
from .align.silence import Alignment
from .motion import PRESETS, Placement, frames_px, sample
from .project import Project
from .util import key_hash, read_json, rel, to_srt, write_json

MASK_K = 4


def _fit(p: Project, pos, w, h):
    x0, y0, x1, y1 = p.style["content_area"]
    x = min(max(pos[0], x0 + w / 2), x1 - w / 2) if w <= x1 - x0 else (x0 + x1) / 2
    y = min(max(pos[1], y0 + h / 2), y1 - h / 2) if h <= y1 - y0 else (y0 + y1) / 2
    return x, y


def _fit_bleed(p: Project, pos, w, h):
    """允许出画（人物）：可以从左右、下边出画，但顶部不进标题区，且至少保留 bleed_keep 在画面内。"""
    _, y0, _, _ = p.style["content_area"]
    keep = p.style.get("bleed_keep", 0.6)
    x = min(max(pos[0], w * (keep - 0.5)), p.W - w * (keep - 0.5))
    y = max(pos[1], y0 + h / 2)
    y = min(y, p.H + h * (0.5 - keep))
    return x, y


def _cover_crop(p: Project, info, bw, bh) -> str:
    """cover 适配：按盒子比例裁一张图，两个后端都不用再处理裁切。"""
    src = p.dir / info.src
    out = p.build / "assets" / "fit" / f"{info.id}_{int(bw)}x{int(bh)}.png"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        img = Image.open(src)
        ar, br = img.width / img.height, bw / bh
        if ar > br:
            nw = int(img.height * br)
            img = img.crop(((img.width - nw) // 2, 0, (img.width - nw) // 2 + nw, img.height))
        else:
            nh = int(img.width / br)
            img = img.crop((0, (img.height - nh) // 2, img.width, (img.height - nh) // 2 + nh))
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
    return rel(out, p.dir)


def _mask(p: Project, path: Path, w: int, h: int, pos) -> np.ndarray:
    m = np.zeros((p.H // MASK_K, p.W // MASK_K), bool)
    try:
        img = Image.open(path)
    except Exception:
        return m
    if img.mode == "RGBA":
        a = np.asarray(img.getchannel("A").resize((max(1, w // MASK_K), max(1, h // MASK_K)))) > 40
    else:
        a = np.ones((max(1, h // MASK_K), max(1, w // MASK_K)), bool)
    x0, y0 = int((pos[0] - w / 2) / MASK_K), int((pos[1] - h / 2) / MASK_K)
    xs, ys = max(0, x0), max(0, y0)
    xe, ye = min(m.shape[1], x0 + a.shape[1]), min(m.shape[0], y0 + a.shape[0])
    if xe > xs and ye > ys:
        m[ys:ye, xs:xe] = a[ys - y0:ye - y0, xs - x0:xe - x0]
    return m


def _chapter_meta(p: Project):
    out, n = {}, 0
    for s in p.shots:
        if s.chapter is not None:
            n += 1
            c = s.chapter or {}
            title = str(c.get("title") or "".join(ch for ch in s.text if ch.isalnum()))
            out[s.id] = {"n": str(c.get("n", f"{n:02d}")), "title": title, "short": str(c.get("short", title.replace("|", "")))}
    return out


def build(p: Project) -> dict:
    st = p.style
    tc = {"cps": 5.0, "cut_lead": 0.04, "tail": 0.5, "min_show": 0.6, "blank_warn": 0.8, **st.get("timing", {})}
    tm = tmod.load(p)
    warnings: list[str] = []
    if tm is None:
        tm = tmod.estimate(p)
        warnings.append("还没有配音对齐结果（或文案改过），时间轴按语速估算；运行 voice 后重新生成")
    info = assets_mod.prepare(p)
    W, H, fps = p.W, p.H, p.fps
    total = tm.duration

    # ---------- 镜头时间窗 ----------
    anchors = []
    for k, s in enumerate(p.shots):
        if s.silent:
            slot = tm.slots.get(s.id)
            anchors.append(slot[0] if slot else None)
        else:
            anchors.append(tm.start[s.offset])
    for k in range(len(anchors) - 1, -1, -1):  # 录音模式下中间的无口播镜头没有槽位：贴到下一镜
        if anchors[k] is None:
            anchors[k] = anchors[k + 1] if k + 1 < len(anchors) else total
    cuts = [0.0]
    for k in range(1, len(p.shots)):
        lead = 0.0 if p.shots[k].silent else tc["cut_lead"]
        cuts.append(max(cuts[-1], anchors[k] - lead))
    cuts.append(total)

    chapters = _chapter_meta(p)
    cues: list[dict] = []
    entrance_times: list[float] = []
    dedupe = float(st.get("sfx", {}).get("dedupe", 0.12))
    idle_cfg = st.get("idle", {})
    shots_out = []
    coverage_max = float(st.get("max_coverage", 0.5))

    def add_cue(t, name, gain=1.0, source="", dedup=False):
        if not name:
            return
        if dedup:
            if any(abs(t - x) < dedupe for x in entrance_times):
                return
            entrance_times.append(t)
        cues.append({"t": round(max(0.0, t), 3), "name": name, "gain": gain, "source": source})

    for k, s in enumerate(p.shots):
        start, end = cuts[k], max(cuts[k + 1], cuts[k] + 0.1)
        items_out, masks, placed = [], {}, {}
        shakes = []
        items = list(s.items)
        if s.id in chapters:  # 章节卡：整屏图片垫在最底层
            ch = chapters[s.id]
            card = assets_mod.render_chapter(p, s, ch["n"], ch["title"], p.build / "assets" / f"chapter_{s.id}.png")
            items_out.append({"el": f"_chapter_{s.id}", "kind": "image", "src": rel(card, p.dir), "x": W / 2, "y": H / 2,
                              "w": W, "h": H, "appear": round(start, 3), "end": round(end, 3), "preset": "fade",
                              "anim": {"dur": 0.3, "frames": frames_px(sample(PRESETS["fade"], Placement()), W, H)},
                              "idle": "none", "track": 0, "volume": 0, "clip_in": 0, "chapter": True})
            add_cue(start - 0.05, "whoosh", 0.5, f"{s.id}/chapter")
            add_cue(start + 0.25, "boom", 0.6, f"{s.id}/chapter")
        order = sorted(range(len(items)), key=lambda i: bool(items[i].over))  # 先摆普通元素，叠加元素跟随目标
        appear_at = {}
        for i in order:
            it = items[i]
            e = p.elements.get(it.el)
            a = info.get(it.el)
            if e is None or a is None:
                continue
            src, w0, h0 = a.src, a.w, a.h
            if it.box:
                bw, bh = it.box
                if e.fit == "cover" and a.kind == "image":
                    src = _cover_crop(p, a, bw, bh)
                    w, h = int(bw), int(bh)
                else:
                    kk = (max if e.fit == "cover" else min)(bw / w0, bh / h0)
                    w, h = int(w0 * kk), int(h0 * kk)
            else:
                x0, y0, x1, y1 = st["content_area"]
                maxw, maxh = (W, H * 0.95) if it.bleed else (x1 - x0, y1 - y0)
                kk = (it.size or 400) / math.sqrt(max(1, w0 * h0))
                kk = min(kk, maxw / w0, maxh / h0)
                w, h = max(8, int(w0 * kk)), max(8, int(h0 * kk))
            if it.over:
                pos = placed.get(it.over, (W / 2, H / 2))
            elif it.box:
                pos = it.pos or (W / 2, H / 2)
            elif it.bleed:
                pos = _fit_bleed(p, it.pos or (W / 2, H / 2), w, h)
            else:
                pos = _fit(p, it.pos or (W / 2, H / 2), w, h)
            placed[it.el] = pos
            if s.silent or not it.trigger:
                t = start + it.delay
            else:
                t = tm.start[s.offset + max(0, it.trigger_at)] + it.delay
            t = max(start, t)
            latest = end - tc["min_show"]
            if t > latest:
                t = max(start, latest)
                if end - t < tc["min_show"] - 1e-6:
                    warnings.append(f"{s.id}: 镜头只有 {end - start:.2f}s，「{it.el}」展示不足 {tc['min_show']}s")
            appear_at[i] = t
            pre = PRESETS[it.preset]
            pl = Placement(0, 0, 1, (h / (H / 2)), H / W)
            frames = frames_px(sample(pre, pl, fps), W, H)
            idle = it.idle
            if idle == "auto":
                idle = "none" if (a.kind == "video" or e.role == "media" or it.box) else \
                    ("zoom", "float")[zlib.crc32(f"{s.id}/{it.el}".encode()) % 2]
            sfx = pre.sfx if it.sfx is None else it.sfx
            add_cue(t + pre.sfx_offset, sfx, 1.0, f"{s.id}/{it.el}", dedup=True)
            if it.shake:
                shakes.append(round(t + pre.sfx_offset, 3))
            items_out.append({
                "el": it.el, "kind": a.kind, "src": src, "x": round(pos[0], 2), "y": round(pos[1], 2), "w": w, "h": h,
                "appear": round(t, 3), "end": round(end, 3), "preset": it.preset, "grow": it.grow,
                "anim": {"dur": round((len(frames) - 1) / fps, 4), "frames": frames}, "idle": idle,
                "idle_seed": zlib.crc32(f"{s.id}/{it.el}".encode()) % 1000, "track": i + 1,
                "volume": e.volume, "clip_in": e.clip_in, "placeholder": a.placeholder, "over": it.over or None})
            if a.kind == "image":
                masks[it.el] = _mask(p, p.dir / src, w, h, pos)
        # 遮挡 / 拥挤
        ok_pairs = {(it.el, it.over) for it in items if it.over} | {(it.over, it.el) for it in items if it.over}
        ids = list(masks)
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                ea, eb = ids[x], ids[y]
                if (ea, eb) in ok_pairs:
                    continue
                inter = np.logical_and(masks[ea], masks[eb]).sum()
                small = min(masks[ea].sum(), masks[eb].sum())
                if small and inter / small > 0.08:
                    warnings.append(f"{s.id}: 「{ea}」和「{eb}」可见部分重叠 {inter / small:.0%}")
        cov = float(np.logical_or.reduce(list(masks.values())).mean()) if masks else 0.0
        if cov > coverage_max and not any(i.box for i in items):
            warnings.append(f"{s.id}: 元素占画面 {cov:.0%}，超过上限 {coverage_max:.0%}，会显得挤")
        real = [t for t in appear_at.values()]
        if real and min(real) - start > tc["blank_warn"] and s.id not in chapters and not s.scene:
            warnings.append(f"{s.id}: 开头空画面 {min(real) - start:.1f}s，建议把第一个元素的 trigger 提前到镜头开头几个字")
        # 镜头里手写的音效
        for fx in s.sfx:
            at = fx.get("at", 0)
            if isinstance(at, str):
                kpos = s.text.find(at)
                t = tm.start[s.offset + kpos] if kpos >= 0 else start
            else:
                t = start + float(at)
            add_cue(t + float(fx.get("offset", 0)), fx.get("name"), float(fx.get("gain", 1.0)), f"{s.id}/sfx")
        marks = {}
        for name, w_ in s.marks.items():
            kpos = s.text.find(w_)
            if kpos >= 0:
                marks[name] = round(tm.start[s.offset + kpos], 3)
        shots_out.append({
            "id": s.id, "index": k, "start": round(start, 3), "end": round(end, 3), "text": s.text, "offset": s.offset,
            "seg": s.seg, "layout": s.layout, "scene": s.scene or None, "chapter": chapters.get(s.id),
            "red": s.red, "marks": marks, "shakes": shakes, "coverage": round(cov, 3),
            "chars": [[round(tm.start[i], 3), round(tm.end[i], 3)] for i in range(s.offset, s.offset + len(s.text))],
            "items": sorted(items_out, key=lambda x: x["track"])})

    # ---------- 顶部逐字标题 ----------
    captions_out = []
    cs = st.get("caption", {})
    if cs.get("enabled"):
        spans = [(s.offset, s.offset + len(s.text)) for s in p.shots]
        caps = cap_mod.split_captions(p.script, spans, [s.cap for s in p.shots], max_line=cs.get("max_line", 12))
        red = cap_mod.red_mask(p.script, [(s.offset, s.offset + len(s.text), s.red) for s in p.shots])
        owner_end = []
        for c in caps:
            k = max(i for i, s in enumerate(p.shots) if s.offset <= c.start and not s.silent) if any(
                s.offset <= c.start and not s.silent for s in p.shots) else 0
            owner_end.append(shots_out[k]["end"])
        starts = [tm.start[c.start] for c in caps]
        for n, c in enumerate(caps):
            s0 = max(0.0, starts[n] - 0.02)
            nxt = starts[n + 1] - 0.02 if n + 1 < len(caps) else owner_end[n]
            e0 = min(nxt, owner_end[n])
            if e0 - s0 < 0.05:
                continue
            captions_out.append({"start": round(s0, 3), "end": round(e0, 3),
                                 "lines": [[{"ch": p.script[i], "t": round(tm.start[i], 3), "red": i in red} for i in ln]
                                           for ln in c.lines]})

    # ---------- 底部字幕（始终导出 srt） ----------
    ss = st.get("subtitle", {})
    al = Alignment(p.script, tm.start, tm.end, tm.duration)
    lines = al.lines(int(ss.get("max_chars", 16)))
    subs = []
    for n, ln in enumerate(lines):
        nxt = lines[n + 1].start if n + 1 < len(lines) else total
        subs.append({"start": ln.start, "end": round(min(nxt, ln.end + 0.6), 3), "text": ln.text})
    (p.build / "subs.srt").parent.mkdir(parents=True, exist_ok=True)
    (p.build / "subs.srt").write_text(to_srt([{**x, "text": x["text"]} for x in subs]), encoding="utf-8")

    # ---------- 自定义画面导出的音效 ----------
    for c in read_json(p.build / "scene_cues.json", []) or []:
        add_cue(float(c["t"]), c.get("name") or c.get("kind"), float(c.get("gain", 1.0)), c.get("source", "scene"))
    cues.sort(key=lambda c: c["t"])

    font = assets_mod.font_path(p)
    hud = dict(st.get("hud", {}))
    if hud.get("enabled") and not hud.get("title"):
        hud["title"] = p.title
    tl = {
        "version": 1, "title": p.title, "fps": fps, "width": W, "height": H, "duration": round(total, 3),
        "timing_source": tm.source, "voice": tm.audio or None,
        "style": {"name": st.get("name"), "background": st.get("background", {}), "caption": cs,
                  "subtitle": ss if ss.get("enabled") else {"enabled": False}, "idle": idle_cfg, "hud": hud,
                  "palette": {k_: v["hex"] for k_, v in st.get("palette", {}).items()},
                  "content_area": st["content_area"], "chapter": st.get("chapter", {})},
        "font": {"file": font.name, "url": "/__font__/main" + font.suffix.lower()},
        "chapters": [{"shot": s["id"], "start": s["start"], **s["chapter"]} for s in shots_out if s["chapter"]],
        "shots": shots_out, "captions": captions_out, "subtitles": subs if ss.get("enabled") else [],
        "srt": rel(p.build / "subs.srt", p.dir), "cues": cues,
        "background_image": rel(assets_mod.render_background(p, p.build / "assets" / "background.png"), p.dir),
    }
    tl["hash"] = key_hash({k_: v for k_, v in tl.items() if k_ != "hash"})
    write_json(p.build / "timeline.json", tl, indent=None)
    report = {"duration": tl["duration"], "timing": tm.source, "shots": len(shots_out),
              "items": sum(len(s["items"]) for s in shots_out), "cues": len(cues), "captions": len(captions_out),
              "subtitles": len(subs), "placeholders": sorted({i["el"] for s in shots_out for i in s["items"] if i.get("placeholder")}),
              "warnings": warnings}
    write_json(p.build / "report" / "timeline.json", report)
    return report


def load(p: Project) -> dict | None:
    return read_json(p.build / "timeline.json")
