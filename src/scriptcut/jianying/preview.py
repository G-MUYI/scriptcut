"""不开剪映也能检查草稿：按剪映的规则把 draft_content.json 的某几个时刻合成成图片。

- 图片 / 视频片段：等比适应画布 × 缩放，按 transform 定位，关键帧线性插值（位置、缩放、旋转、不透明度）
- 文本片段：用本地中文字体按「字号 × 缩放 × 画布短边 × 0.006」估算字高，支持分段颜色、描边、逐字显影入场的近似
- 只用于检查位置 / 时序 / 叠放是否正确；字体、花字、特效以剪映实际显示为准
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..util import ScriptCutError

US = 1_000_000
EM = 0.0060


def _interp(kfs: list, t_us: float, default: float) -> float:
    if not kfs:
        return default
    pts = sorted((k["time_offset"], k["values"][0]) for k in kfs)
    if t_us <= pts[0][0]:
        return pts[0][1]
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if a <= t_us <= b:
            return va + (vb - va) * (t_us - a) / max(1, b - a)
    return pts[-1][1]


class DraftPreview:
    def __init__(self, draft_dir: Path, font_path: str):
        f = Path(draft_dir) / "draft_content.json"
        if not f.exists():
            raise ScriptCutError(f"找不到草稿 {f}")
        self.d = json.loads(f.read_text(encoding="utf-8"))
        self.W, self.H = self.d["canvas_config"]["width"], self.d["canvas_config"]["height"]
        self.short = min(self.W, self.H)
        self.font_path = font_path
        m = self.d["materials"]
        self.videos = {x["id"]: x for x in m["videos"]}
        self.texts = {x["id"]: x for x in m["texts"]}
        self.anims = {x["id"]: x for x in m.get("material_animations", [])}
        self._img: dict[str, Image.Image] = {}

    def _image(self, mat: dict, src_us: int) -> Image.Image | None:
        p = mat["path"]
        if mat.get("type") == "photo":
            if p not in self._img:
                self._img[p] = Image.open(p).convert("RGBA")
            return self._img[p]
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{src_us / US:.3f}", "-i", p, "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        if out.returncode or not out.stdout:
            return None
        import io
        return Image.open(io.BytesIO(out.stdout)).convert("RGBA")

    def _props(self, seg: dict, t_us: float) -> dict:
        c = seg["clip"]
        kf = {k["property_type"]: k["keyframe_list"] for k in seg.get("common_keyframes", [])}
        return {"x": _interp(kf.get("KFTypePositionX"), t_us, c["transform"]["x"]),
                "y": _interp(kf.get("KFTypePositionY"), t_us, c["transform"]["y"]),
                "sx": _interp(kf.get("KFTypeScaleX"), t_us, c["scale"]["x"]),
                "sy": _interp(kf.get("KFTypeScaleY"), t_us, c["scale"]["y"]),
                "rot": _interp(kf.get("KFTypeRotation"), t_us, c.get("rotation", 0.0)),
                "alpha": _interp(kf.get("KFTypeAlpha"), t_us, c.get("alpha", 1.0))}

    def _paste(self, frame: Image.Image, img: Image.Image, pr: dict):
        w, h = max(1, int(img.width * pr["sx"])), max(1, int(img.height * pr["sy"]))
        im = img.resize((w, h), Image.BILINEAR)
        if pr["rot"]:
            im = im.rotate(-pr["rot"], Image.BICUBIC, expand=True)
        if pr["alpha"] < 0.999:
            im.putalpha(im.getchannel("A").point(lambda v: int(v * max(0.0, pr["alpha"]))))
        cx, cy = self.W / 2 + pr["x"] * self.W / 2, self.H / 2 - pr["y"] * self.H / 2
        self._paste_clip(frame, im, cx, cy)

    @staticmethod
    def _paste_clip(frame, im, cx, cy):
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        layer.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), im)
        frame.alpha_composite(layer)

    def _text_image(self, mat: dict, seg: dict, t_us: float) -> Image.Image:
        content = json.loads(mat["content"])
        text = content["text"]
        styles = content["styles"]
        size = max(s.get("size", 8) for s in styles)
        px = max(6, int(size * self.short * EM))
        font = ImageFont.truetype(self.font_path, px)
        colors = [None] * len(text)
        stroke = None
        for st in styles:
            col = tuple(int(c * 255) for c in st["fill"]["content"]["solid"]["color"])
            for i in range(st["range"][0], min(st["range"][1], len(text))):
                colors[i] = col
            if st.get("strokes"):
                stroke = tuple(int(c * 255) for c in st["strokes"][0]["content"]["solid"]["color"])
        # 逐字显影：按入场动画进度显示前 k 个字
        shown = len(text)
        for ref in seg.get("extra_material_refs", []):
            a = self.anims.get(ref)
            for an in (a or {}).get("animations", []):
                if an["type"] == "in" and an["duration"] > 0:
                    q = min(1.0, max(0.0, t_us / an["duration"]))
                    if "逐字" in an["name"] or "打字" in an["name"]:
                        shown = int(round(len(text) * q))
        lines = text.split("\n")
        lh = int(px * 1.3)
        widths = [font.getlength(ln) for ln in lines]
        W = int(max(widths + [1]) + px)
        img = Image.new("RGBA", (W, lh * len(lines) + px // 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        k = 0
        align = mat.get("alignment", 0)
        for row, ln in enumerate(lines):
            x = px / 2 if align == 0 else (W - widths[row]) / 2
            for ch in ln:
                if k < shown:
                    d.text((x, row * lh + px * 0.1), ch, font=font, fill=(colors[k] or (255, 255, 255)) + (255,),
                           stroke_width=max(1, px // 12) if stroke else 0, stroke_fill=stroke)
                x += font.getlength(ch)
                k += 1
            k += 1  # 换行符
        return img

    def frame(self, t: float) -> Image.Image:
        t_us = t * US
        frame = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 255))
        for tr in self.d["tracks"]:
            if tr["type"] not in ("video", "text"):
                continue
            for seg in tr["segments"]:
                a, dur = seg["target_timerange"]["start"], seg["target_timerange"]["duration"]
                if not (a <= t_us < a + dur):
                    continue
                local = t_us - a
                pr = self._props(seg, local)
                if tr["type"] == "video":
                    mat = self.videos[seg["material_id"]]
                    src = (seg.get("source_timerange") or {}).get("start", 0) + local
                    img = self._image(mat, src)
                    if img is None:
                        continue
                    fit = min(self.W / img.width, self.H / img.height)
                    pr2 = dict(pr, sx=pr["sx"] * fit, sy=pr["sy"] * fit)
                    self._paste(frame, img, pr2)
                else:
                    img = self._text_image(self.texts[seg["material_id"]], seg, local)
                    self._paste(frame, img, dict(pr, alpha=1.0))
        return frame.convert("RGB")


def render_frames(draft_dir: Path, times: list[float], font_path: str, out_dir: Path) -> list[Path]:
    pv = DraftPreview(draft_dir, font_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for t in times:
        f = out_dir / f"jy_{t:.2f}.jpg"
        pv.frame(t).save(f, quality=88)
        files.append(f)
    return files
