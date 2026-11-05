"""素材：AI 出图提示词、出图（可插拔）、抠白底、文字素材本地渲染、占位图、章节卡、背景。

元素图的来源优先级：element.src（用户素材）> assets/<ID>.png（AI 生成或手动放入）> 占位图。
所有元素最终统一成 build/assets/el/<ID>.png（裁掉透明留白），Canvas 和剪映两个后端用同一份。

出图由用户接入自己的 API（火山方舟 Seedream / 通义万相 / 硅基流动 / OpenAI 兼容 / 命令行），见 imagegen.py。
"""

from __future__ import annotations

import colorsys
import io
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config
from .project import IMAGE_EXT, PERSON_ROLES, Element, Project
from .util import key_hash, probe_video, read_json, rel, write_json

_CJK_GAP = re.compile(r"(?<=[　-鿿＀-￯])\s+(?=[　-鿿＀-￯])")


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def accent_hex(p: Project, name: str, default: str = "#D2412F") -> str:
    return p.style.get("palette", {}).get(name, {}).get("hex", default)


def font_path(p: Project) -> Path:
    return config.find_font(p.style.get("font", {}).get("files"), [p.dir / "fonts"])


# ---------------------------------------------------------------- 提示词

def uses_ref(p: Project, e: Element) -> bool:
    ref = p.cfg.get("character", {}).get("ref")
    if not ref or not p.path(ref).exists():
        return False
    return bool(e.ref) if e.ref is not None else e.role in PERSON_ROLES


def build_prompt(p: Project, e: Element) -> str:
    sp = p.style.get("image_prompt", {})
    pal = p.style.get("palette", {})
    color = pal.get(e.accent, {"hex": "#333333"})
    if e.role == "word":
        rest = e.text.replace(e.hl, "", 1) if e.hl else ""
        if e.hl and rest.strip():
            colors = sp.get("word_color_split", "").format(hl=e.hl, rest=rest, color_name=e.accent, hex=color["hex"])
        else:
            colors = sp.get("word_color_all", "").format(color_name=e.accent, hex=color["hex"])
        parts = [sp.get("word_prompt", "").format(text=e.text, n=len(e.text.replace(" ", ""))), colors]
        if e.prompt:
            parts.append(e.prompt.rstrip("。") + "。")
        parts.append(sp.get("word_style_prompt", ""))
    else:
        parts = [e.prompt.rstrip("。") + "。"]
        if e.label:
            parts.append(sp.get("label_prompt", "").format(label=e.label))
        if e.role in PERSON_ROLES and p.cfg.get("character", {}).get("description") and e.ref is not False:
            parts.append(sp.get("character_prompt", "").format(character=p.cfg["character"]["description"]))
        if e.accent:
            parts.append(sp.get("accent_prompt", "").format(color_name=e.accent, hex=color["hex"],
                                                            accent_part=e.accent_part or "主体的关键部位"))
        parts.append(sp.get("style_prompt", ""))
    return _CJK_GAP.sub("", "".join(x for x in parts if x))


def prompts(p: Project) -> list[dict]:
    usage: dict[str, list[str]] = {}
    for s in p.shots:
        for it in s.items:
            usage.setdefault(it.el, []).append(s.id)
    out = []
    for eid, e in p.elements.items():
        if not e.needs_ai:
            continue
        out.append({"id": eid, "name": e.name, "role": e.role, "accent": e.accent, "ref": uses_ref(p, e),
                    "shots": usage.get(eid, []), "exists": _ai_file(p, eid) is not None, "prompt": build_prompt(p, e)})
    return out


def export_prompts(p: Project) -> Path:
    items = prompts(p)
    lines = [f"# {p.title} · 素材提示词清单", "", f"共 {len(items)} 个 AI 素材，比例 {p.style.get('image_prompt', {}).get('ratio', '1:1')}，"
             "透明底（或纯白底，出图后自动抠白）。生成后保存为 `assets/<编号>.png`。", ""]
    for it in items:
        ref = "（挂人物参考图）" if it["ref"] else ""
        done = "✅ 已有" if it["exists"] else "⬜ 待生成"
        lines += [f"## {it['id']} {it['name']}{ref} {done}", f"- 出现镜头：{'、'.join(it['shots'])}", "", it["prompt"], ""]
    out = p.build / "prompts.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    write_json(p.build / "prompts.json", items)
    return out


# ---------------------------------------------------------------- 出图

def _ai_file(p: Project, eid: str) -> Path | None:
    for ext in (".png", ".webp", ".jpg", ".jpeg"):
        f = p.dir / "assets" / f"{eid}{ext}"
        if f.exists():
            return f
    return None


def knock_out_white(img: Image.Image, tol: int = 18, feather: int = 2) -> Image.Image:
    """把与边缘连通的近白色区域变透明（AI 没出透明底时兜底）。"""
    rgb = np.asarray(img.convert("RGB"), np.int16)
    h, w, _ = rgb.shape
    near_white = (255 - rgb).max(axis=2) <= tol
    k = max(1, max(h, w) // 512)
    small = near_white[::k, ::k]
    bg = np.zeros_like(small)
    bg[0, :], bg[-1, :], bg[:, 0], bg[:, -1] = small[0, :], small[-1, :], small[:, 0], small[:, -1]
    while True:
        grown = bg.copy()
        grown[1:, :] |= bg[:-1, :]
        grown[:-1, :] |= bg[1:, :]
        grown[:, 1:] |= bg[:, :-1]
        grown[:, :-1] |= bg[:, 1:]
        grown &= small
        if (grown == bg).all():
            break
        bg = grown
    pad = bg.copy()
    pad[1:, :] |= bg[:-1, :]
    pad[:-1, :] |= bg[1:, :]
    pad[:, 1:] |= bg[:, :-1]
    pad[:, :-1] |= bg[:, 1:]
    full = np.repeat(np.repeat(pad, k, axis=0), k, axis=1)[:h, :w]
    alpha = Image.fromarray(np.where(full & near_white, 0, 255).astype(np.uint8))
    if feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    return out


def accent_report(img: Image.Image, palette: dict) -> list[str]:
    """饱和像素属于哪些点缀色（占比 > 10%，按占比降序）。"""
    small = img.convert("RGBA").resize((160, max(1, int(160 * img.height / img.width))))
    px = np.asarray(small, np.float32) / 255
    rgb = px[..., :3][px[..., 3] > 0.5]
    if not len(rgb) or not palette:
        return []
    hsv = np.array([colorsys.rgb_to_hsv(*c) for c in rgb])
    sat = (hsv[:, 1] > 0.3) & (hsv[:, 2] > 0.35)
    if sat.sum() < 30:
        return []
    names = list(palette)
    ref = np.array([colorsys.rgb_to_hsv(*(c / 255 for c in _rgb(palette[n]["hex"]))) for n in names])
    d = np.abs(hsv[sat, 0][:, None] - ref[None, :, 0])
    d = np.minimum(d, 1 - d)
    counts = np.bincount(d.argmin(axis=1), minlength=len(names)) / sat.sum()
    return [names[i] for i in np.argsort(-counts) if counts[i] > 0.10]


def generate(p: Project, only: list[str] | None = None, force: bool = False, provider: str | None = None,
             progress=print, allow_placeholders: bool = False) -> dict:
    from . import imagegen
    export_prompts(p)
    todo = [e for e in p.elements.values() if e.needs_ai and (not only or e.id in only)
            and (force or only or _ai_file(p, e.id) is None)]
    st = imagegen.settings(p)
    base = {"todo": [e.id for e in todo], "prompts": rel(p.build / "prompts.md", p.dir), "image_api": st["label"]}
    if not todo:
        return {**base, "ok": True, "generated": 0, "message": "所有 AI 素材都已就绪（assets/ 里已有），不需要出图"}
    prov = imagegen.provider(p, provider) if (provider or st["configured"]) else None
    if prov is None:
        if st["manual"] or allow_placeholders:
            return {**base, "ok": True, "generated": 0, "action_required": "manual_images",
                    "message": f"还有 {len(todo)} 个 AI 素材没有图：按 build/prompts.md 出图后保存为 assets/<编号>.png；"
                               "在此之前草稿和成片里用占位图（剪映里可直接「替换素材」）"}
        return {**base, "ok": False, "error_code": "need_image_api", "generated": 0,
                "error": f"还有 {len(todo)} 个 AI 素材需要出图，但没有接入出图 API。\n\n" + imagegen.GUIDE}
    (p.dir / "assets").mkdir(exist_ok=True)
    ref_rel = p.cfg.get("character", {}).get("ref")
    knock = bool(p.cfg["image_gen"].get("knockout", True))

    def one(e: Element) -> dict:
        out = p.dir / "assets" / f"{e.id}.png"
        raw = out.with_suffix(".raw")
        try:
            prov.generate(e.id, build_prompt(p, e), p.path(ref_rel) if uses_ref(p, e) else None, raw)
            img = Image.open(io.BytesIO(raw.read_bytes()))
            notes = []
            has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
            if has_alpha:
                img = img.convert("RGBA")
                has_alpha = img.getchannel("A").getextrema()[0] < 255
            if not has_alpha and (e.knockout if e.knockout is not None else knock):
                img = knock_out_white(img)
                notes.append("没有透明底，已自动抠白底，请检查边缘")
            img.save(out)
            raw.unlink(missing_ok=True)
            if e.role != "word" and e.accent:
                colors = accent_report(img, p.style.get("palette", {}))
                if len(colors) > 1:
                    notes.append(f"检测到多种点缀色 {'、'.join(colors)}（应只有 {e.accent}）")
            progress(f"  出图 {e.id} {e.name} ✓")
            return {"id": e.id, "ok": True, "notes": notes}
        except Exception as ex:  # 单张失败不影响其他
            progress(f"  出图 {e.id} ✗ {ex}")
            return {"id": e.id, "ok": False, "error": str(ex)}

    with ThreadPoolExecutor(max(1, int(p.cfg["image_gen"].get("concurrency", 2)))) as ex:
        res = list(ex.map(one, todo))
    write_json(p.build / "report" / "gen.json", res)
    failed = [r["id"] for r in res if not r["ok"]]
    return {**base, "ok": not failed, "provider": prov.name, "generated": sum(r["ok"] for r in res), "failed": failed,
            "errors": {r["id"]: r["error"] for r in res if not r["ok"]},
            "notes": {r["id"]: r["notes"] for r in res if r.get("notes")}}


# ---------------------------------------------------------------- 本地渲染

def trim(img: Image.Image) -> Image.Image:
    """裁掉透明留白，尺寸贴合可见内容。"""
    if img.mode != "RGBA":
        return img
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        return img
    pad = int(max(img.size) * 0.02)
    return img.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(img.width, bbox[2] + pad), min(img.height, bbox[3] + pad)))


def render_word(p: Project, e: Element, out: Path) -> Path:
    ws = p.style.get("word", {})
    fpx = int(ws.get("font_px", 150))
    font = ImageFont.truetype(str(font_path(p)), fpx)
    ink = _rgb(ws.get("ink", "#222222")) + (255,)
    hl = _rgb(accent_hex(p, e.accent, ws.get("ink", "#D2412F"))) + (255,)
    text = e.text
    lines = [text]
    lm = int(ws.get("line_max", 8))
    if len(text) > lm and "|" not in text:
        cut = -(-len(text) // 2)
        lines = [text[:cut], text[cut:]]
    elif "|" in text:
        lines = text.split("|")
    k0 = text.replace("|", "").find(e.hl) if e.hl else -1
    pad = int(fpx * 0.25)
    lh = int(fpx * 1.18)
    width = max(int(font.getlength(ln)) for ln in lines) + pad * 2
    img = Image.new("RGBA", (max(width, 10), lh * len(lines) + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stroke = int(ws.get("stroke", 0))
    idx = 0
    for row, ln in enumerate(lines):
        x = pad + (width - pad * 2 - int(font.getlength(ln))) / 2
        for c in ln:
            colored = (k0 <= idx < k0 + len(e.hl)) if e.hl else bool(e.accent)
            fill = hl if colored else ink
            d.text((x, pad + row * lh + lh / 2), c, font=font, fill=fill, anchor="lm",
                   stroke_width=stroke, stroke_fill=ink if stroke else None)
            x += font.getlength(c)
            idx += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    trim(img).save(out)
    return out


def render_placeholder(p: Project, e: Element, out: Path) -> Path:
    """素材还没生成时的占位图：灰框 + 编号 + 名称 + 点缀色条，方便先看版式和节奏。"""
    fp = str(font_path(p))
    rgb = _rgb(accent_hex(p, e.accent, "#9AA4B0"))
    if e.role == "word" and e.text:
        font = ImageFont.truetype(fp, 150)
        w = int(font.getlength(e.text)) + 120
        img = Image.new("RGBA", (max(w, 400), 290), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((4, 4, img.width - 5, 285), 40, outline=(180, 180, 180, 255), width=6)
        x = 60
        k = e.text.find(e.hl) if e.hl else -1
        for i, c in enumerate(e.text):
            on = (k <= i < k + len(e.hl)) if e.hl else True
            d.text((x, 145), c, font=font, fill=(rgb + (255,)) if on else (70, 70, 70, 255), anchor="lm")
            x += int(font.getlength(c))
    else:
        ar = 1.0
        if e.role in PERSON_ROLES:
            ar = 1.5
        W0, H0 = 800, int(800 * ar)
        img = Image.new("RGBA", (W0, H0), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((30, 30, W0 - 30, H0 - 30), 60, fill=(238, 240, 243, 255), outline=(160, 166, 175, 255), width=8)
        d.rectangle((30, H0 - 190, W0 - 30, H0 - 110), fill=rgb + (255,))
        d.text((W0 / 2, H0 * 0.36), e.id, font=ImageFont.truetype(fp, 190), fill=(90, 96, 105, 255), anchor="mm")
        label = (e.name or e.prompt or e.src or "")[:10]
        d.text((W0 / 2, H0 * 0.36 + 190), label, font=ImageFont.truetype(fp, 68), fill=(90, 96, 105, 255), anchor="mm")
    d.text((img.width - 24, 20), "待生成", font=ImageFont.truetype(fp, 44), fill=(200, 60, 50, 255), anchor="ra")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_background(p: Project, out: Path) -> Path:
    bg = p.style.get("background", {})
    W, H = p.W, p.H
    img = Image.new("RGB", (W, H), _rgb(bg.get("color", "#FFFFFF")))
    d = ImageDraw.Draw(img)
    pat, col, sp = bg.get("pattern", "none"), _rgb(bg.get("pattern_color", "#DDDDDD")), int(bg.get("spacing", 48))
    if pat == "dots":
        for row, y in enumerate(range(sp // 2, H, sp)):
            for x in range(sp // 2 + (row % 2) * sp // 2, W, sp):
                d.ellipse((x - 2.2, y - 2.2, x + 2.2, y + 2.2), fill=col)
    elif pat == "grid":
        for x in range(0, W, sp):
            d.line((x, 0, x, H), fill=col, width=1)
        for y in range(0, H, sp):
            d.line((0, y, W, y), fill=col, width=1)
    elif pat == "stripes":
        for x in range(-H, W, sp):
            d.line((x, H, x + H, 0), fill=col, width=sp // 3)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_chapter(p: Project, shot, number: str, title: str, out: Path, with_text: bool = True) -> Path:
    cs = p.style.get("chapter", {})
    W, H = p.W, p.H
    fp = str(font_path(p))
    img = Image.new("RGB", (W, H), _rgb(cs.get("bg", "#1E2430")))
    d = ImageDraw.Draw(img)
    grid = tuple(min(255, c + 14) for c in _rgb(cs.get("bg", "#1E2430")))
    for x in range(0, W, 90):
        d.line((x, 0, x, H), fill=grid, width=2)
    for y in range(0, H, 90):
        d.line((0, y, W, y), fill=grid, width=2)
    acc, fg = _rgb(cs.get("accent", "#F2C230")), _rgb(cs.get("fg", "#FFFFFF"))
    cy = H * 0.42
    npx, tpx = int(cs.get("number_px", 220) * min(W, H) / 1080), int(cs.get("title_px", 96) * min(W, H) / 1080)
    lines = [x for x in (title.split("|") if "|" in title else [title]) if x]
    f = ImageFont.truetype(fp, tpx)
    if not with_text:  # 剪映可编辑草稿：底图只画网格和下划线，文字用剪映原生文本
        y = cy + npx * 0.85 + tpx * 1.3 * len(lines)
        tw = max((f.getlength(x) for x in lines), default=0)
        d.rounded_rectangle((W / 2 - tw * 0.3, y - tpx * 0.35, W / 2 + tw * 0.3, y - tpx * 0.35 + 12), 6, fill=acc)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out
    if number:
        d.text((W / 2, cy - npx * 0.95), f"PART {number}", font=ImageFont.truetype(fp, max(24, npx // 6)), fill=acc, anchor="mm")
        d.text((W / 2, cy), number, font=ImageFont.truetype(fp, npx), fill=acc, anchor="mm")
    y = cy + npx * 0.85
    for ln in lines:
        d.text((W / 2, y), ln, font=f, fill=fg, anchor="mm")
        y += tpx * 1.3
    tw = max((f.getlength(x) for x in lines), default=0)
    d.rounded_rectangle((W / 2 - tw * 0.3, y - tpx * 0.35, W / 2 + tw * 0.3, y - tpx * 0.35 + 12), 6, fill=acc)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# ---------------------------------------------------------------- 统一准备

@dataclass
class AssetInfo:
    id: str
    kind: str          # image | video
    src: str           # 相对项目目录：Canvas 和剪映都用它
    w: int
    h: int
    placeholder: bool = False
    duration: float = 0.0


def prepare(p: Project) -> dict[str, AssetInfo]:
    """准备全部元素图（幂等，按源文件签名缓存）→ {ID: AssetInfo}，同时写 build/assets/index.json。"""
    out_dir = p.build / "assets" / "el"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = read_json(p.build / "assets" / "index.json", {}) or {}
    res: dict[str, AssetInfo] = {}
    for eid, e in p.elements.items():
        if e.kind == "video":
            src = p.path(e.src) if e.src else None
            if src and src.exists():
                dur, w, h = probe_video(src)
                res[eid] = AssetInfo(eid, "video", rel(src, p.dir), w, h, False, dur)
                continue
        src_file, placeholder = None, False
        if e.kind == "word":
            sig = {"k": "word", "text": e.text, "hl": e.hl, "accent": accent_hex(p, e.accent, ""), "word": p.style.get("word"),
                   "font": str(font_path(p))}
        else:
            if e.src and p.path(e.src).exists() and p.path(e.src).suffix.lower() in IMAGE_EXT:
                src_file = p.path(e.src)
            else:
                src_file = _ai_file(p, eid)
            if src_file is None:
                placeholder = True
                sig = {"k": "ph", "id": eid, "name": e.name, "role": e.role, "text": e.text, "hl": e.hl, "accent": e.accent}
            else:
                st = src_file.stat()
                sig = {"k": "img", "src": str(src_file), "mtime": st.st_mtime, "size": st.st_size}
        h = key_hash(sig)
        dst = out_dir / f"{eid}.png"
        cached = index.get(eid)
        if not (cached and cached.get("sig") == h and dst.exists()):
            if e.kind == "word":
                render_word(p, e, dst)
            elif placeholder:
                render_placeholder(p, e, dst)
            else:
                img = Image.open(src_file)
                img = trim(img.convert("RGBA")) if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
                img.save(dst)
            with Image.open(dst) as im:
                index[eid] = {"sig": h, "w": im.width, "h": im.height, "placeholder": placeholder}
        info = index[eid]
        res[eid] = AssetInfo(eid, "image", rel(dst, p.dir), info["w"], info["h"], info["placeholder"])
    write_json(p.build / "assets" / "index.json", index)
    return res


def assets_status(p: Project) -> dict:
    res = prepare(p)
    return {"elements": len(res), "placeholders": [k for k, v in res.items() if v.placeholder],
            "assets": {k: asdict(v) for k, v in res.items()}}
