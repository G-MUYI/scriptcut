"""Canvas 出片：画面是时间 t 的纯函数，所以把时间轴切成若干段多进程并行渲染，再无损拼接、合成音轨。

- stills：每镜「元素全部落定」时刻各取一帧拼成总览图（全片渲染前先自检挤字、遮挡、空画面）
- cues：在浏览器里跑一遍自定义画面的 cues()，导出音效提示点 → build/scene_cues.json
- render：逐帧渲染（可选运动模糊子采样）→ build/render/video.mp4 → 合成 build/audio/master.wav → out/<名字>.mp4
"""

from __future__ import annotations

import base64
import io
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import assets as assets_mod
from ..project import Project, load
from ..util import ScriptCutError, key_hash, read_json, rel, write_json
from .server import serve


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScriptCutError("Canvas 出片需要 Playwright：pip install playwright && playwright install chromium") from e
    return sync_playwright


def open_page(browser, url: str, query: str = ""):
    page = browser.new_page(viewport={"width": 540, "height": 960}, device_scale_factor=1)
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{url}/index.html{query}")
    page.wait_for_function("window.__ready === true || !!window.__error", timeout=120000)
    err = page.evaluate("window.__error || null")
    if err or errors:
        raise ScriptCutError(f"页面错误：{err or errors}")
    page._sc_errors = errors
    return page


def grab(page, t: float, fmt: str = "png") -> bytes:
    mime, q = ("image/jpeg", 0.95) if fmt == "jpeg" else ("image/png", None)
    js = ("async ([t, m, q]) => { await window.seek(t); return document.getElementById('stage').toDataURL(m, q); }")
    url = page.evaluate(js, [t, mime, q])
    if page._sc_errors:
        raise ScriptCutError(f"t={t:.3f} 页面错误：{page._sc_errors}")
    return base64.b64decode(url[url.index(",") + 1:])


def _check_timeline(p: Project) -> dict:
    tl = read_json(p.build / "timeline.json")
    if not tl:
        raise ScriptCutError("还没有 build/timeline.json：先运行 timeline")
    return tl


# ---------------------------------------------------------------- 视频素材抽帧

def prepare_frames(p: Project, tl: dict) -> dict:
    """视频素材预先抽成 jpg 序列（Chromium 无头版不一定能解 H.264，抽帧也保证逐帧确定）。"""
    index = {}
    for s in tl["shots"]:
        for it in s["items"]:
            if it["kind"] != "video":
                continue
            dur = it["end"] - it["appear"]
            key = key_hash([it["src"], it["clip_in"], dur, it["w"], it["h"], tl["fps"]])
            d = p.build / "frames" / f"{it['el']}_{key}"
            if not (d / "done").exists():
                d.mkdir(parents=True, exist_ok=True)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(it["clip_in"]), "-t", f"{dur + 0.2:.3f}",
                                "-i", str(p.dir / it["src"]), "-vf", f"fps={tl['fps']},scale={it['w']}:{it['h']}",
                                "-q:v", "3", str(d / "%05d.jpg")], check=True)
                (d / "done").write_text("ok")
            index[f"{s['id']}/{it['el']}"] = {"dir": rel(d, p.dir), "count": len(list(d.glob("*.jpg"))), "fps": tl["fps"]}
    write_json(p.build / "frames" / "index.json", index)
    return index


# ---------------------------------------------------------------- 渲染

def _segment(job):
    project_dir, idx, f0, f1, fps, crf, blur, fmt, seg_dir = job[:9]
    extra = job[9] if len(job) > 9 else ""
    out_path = job[10] if len(job) > 10 else None
    p = load(project_dir)
    font = assets_mod.font_path(p)
    path = Path(out_path) if out_path else Path(seg_dir) / f"seg_{idx:02d}.mp4"
    codec = "mjpeg" if fmt == "jpeg" else "png"
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", codec, "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-r", str(fps), str(path)],
        stdin=subprocess.PIPE)
    args = ["--font-render-hinting=none", "--force-color-profile=srgb"]
    with serve(p.dir, font) as url, _playwright()() as pw:
        browser = pw.chromium.launch(args=args)
        page = open_page(browser, url, f"?blur={blur}{extra}")
        f, retries = f0, 0
        while f < f1:
            try:
                png = grab(page, f / fps, fmt)
            except ScriptCutError:
                raise
            except Exception:  # 浏览器偶发崩溃（内存紧张时）：重开浏览器从当前帧继续，画面是纯函数所以结果不变
                retries += 1
                if retries > 3:
                    raise
                try:
                    browser.close()
                except Exception:
                    pass
                browser = pw.chromium.launch(args=args)
                page = open_page(browser, url, f"?blur={blur}{extra}")
                continue
            enc.stdin.write(png)
            f += 1
        browser.close()
    enc.stdin.close()
    if enc.wait() != 0:
        raise ScriptCutError(f"第 {idx} 段编码失败")
    return idx, f1 - f0


def render(p: Project, workers: int | None = None, t0: float = 0.0, t1: float | None = None, blur: int | None = None,
           out_name: str | None = None, progress=print) -> dict:
    tl = _check_timeline(p)
    rc = p.cfg["render"]
    workers = int(workers or rc.get("workers", 4))
    blur = int(blur or rc.get("motion_blur", 1))
    crf, fmt = int(rc.get("crf", 18)), rc.get("format", "png")
    prepare_frames(p, tl)
    fps = tl["fps"]
    end = t1 if t1 is not None else tl["duration"]
    F0, F1 = round(t0 * fps), round(end * fps)
    seg_dir = p.build / "render" / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    for old in seg_dir.glob("seg_*.mp4"):
        old.unlink()
    n = max(1, min(workers, (F1 - F0) // 30 or 1))
    bounds = [F0 + (F1 - F0) * i // n for i in range(n + 1)]
    jobs = [(str(p.dir), i, bounds[i], bounds[i + 1], fps, crf, blur, fmt, str(seg_dir)) for i in range(n) if bounds[i + 1] > bounds[i]]
    progress(f"渲染 {F1 - F0} 帧（{(F1 - F0) / fps:.2f}s），{len(jobs)} 个进程，运动模糊 {blur}…")
    st = time.time()
    if len(jobs) == 1:
        _segment(jobs[0])
    else:
        with ProcessPoolExecutor(len(jobs)) as ex:
            for idx, cnt in ex.map(_segment, jobs):
                progress(f"  ✓ 段 {idx}: {cnt} 帧（{time.time() - st:.0f}s）")
    lst = seg_dir / "list.txt"
    lst.write_text("".join(f"file 'seg_{j[1]:02d}.mp4'\n" for j in jobs), encoding="utf-8")
    video = p.build / "render" / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)],
                   check=True)
    p.out.mkdir(parents=True, exist_ok=True)
    name = out_name or (f"{p.name}.mp4" if (t0 == 0 and t1 is None) else f"{p.name}_preview_{t0:g}-{end:g}.mp4")
    out = p.out / name
    audio = p.build / "audio" / "master.wav"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
    if audio.exists():
        cmd += ["-ss", f"{F0 / fps:.3f}", "-t", f"{(F1 - F0) / fps:.3f}", "-i", str(audio),
                "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "256k", "-ar", "48000"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", "-shortest", str(out)]
    subprocess.run(cmd, check=True)
    res = {"video": rel(out, p.dir), "silent_video": rel(video, p.dir), "frames": F1 - F0, "seconds": round((F1 - F0) / fps, 2),
           "with_audio": audio.exists(), "elapsed": round(time.time() - st, 1)}
    write_json(p.build / "report" / "render.json", res)
    return res


# ---------------------------------------------------------------- 静帧自检

def _sheet(images: list[tuple[str, bytes]], cols: int, path: Path, W: int, H: int, scale: float) -> Path:
    w, h = int(W * scale), int(H * scale)
    rows = (len(images) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * w, rows * (h + 30)), (28, 28, 28))
    d = ImageDraw.Draw(canvas)
    try:
        from .. import config
        font = ImageFont.truetype(str(config.find_font()), 20)
    except Exception:
        font = ImageFont.load_default()
    for k, (label, png) in enumerate(images):
        im = Image.open(io.BytesIO(png)).convert("RGB").resize((w, h), Image.LANCZOS)
        x, y = (k % cols) * w, (k // cols) * (h + 30)
        canvas.paste(im, (x, y + 30))
        d.text((x + 6, y + 4), label, fill=(255, 200, 120), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, quality=88)
    return path


def settle_time(s: dict) -> float:
    """镜头里所有元素入场动画结束 + 0.3 秒（不超过镜头结束）。"""
    t = max([it["appear"] + it["anim"]["dur"] for it in s["items"]] or [s["start"] + 0.5]) + 0.3
    chars = [c[1] for c in s["chars"]]
    if chars:
        t = max(t, min(chars[-1], s["end"]) - 0.05)
    return min(t, s["end"] - 0.04)


def stills(p: Project, at: list[float] | None = None, shot: str | None = None, marks: bool = False) -> dict:
    tl = _check_timeline(p)
    prepare_frames(p, tl)
    font = assets_mod.font_path(p)
    out_dir = p.build / "stills"
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = tl["width"], tl["height"]
    portrait = H > W
    scale, cols = (0.25, 8) if portrait else (0.2, 5)
    files = []
    with serve(p.dir, font) as url, _playwright()() as pw:
        browser = pw.chromium.launch()
        page = open_page(browser, url)
        if at:
            for t in at:
                f = out_dir / f"t_{t:.2f}.png"
                f.write_bytes(grab(page, t))
                files.append(rel(f, p.dir))
        elif shot:
            s = next((x for x in tl["shots"] if x["id"] == shot), None)
            if not s:
                raise ScriptCutError(f"没有镜头 {shot}")
            ts = [s["start"] + (s["end"] - s["start"]) * k / 8 + 0.02 for k in range(8)]
            imgs = [(f"{t:.2f}s", grab(page, t)) for t in ts]
            files.append(rel(_sheet(imgs, 8 if portrait else 4, out_dir / f"shot_{shot}.jpg", W, H, scale * 1.2), p.dir))
        elif marks:
            pts = []
            for s in tl["shots"]:
                for it in s["items"]:
                    pts.append((f"{s['id']} {it['el']}", it["appear"] + it["anim"]["dur"] + 0.1))
                for name, t in s["marks"].items():
                    pts.append((f"{s['id']} {name}", t + 0.4))
            pts.sort(key=lambda x: x[1])
            imgs = [(f"{lab} {t:.1f}s", grab(page, min(t, tl["duration"] - 0.05))) for lab, t in pts]
            for part in range(0, len(imgs), 24):
                files.append(rel(_sheet(imgs[part:part + 24], cols, out_dir / f"marks_{part // 24 + 1}.jpg", W, H, scale), p.dir))
        else:
            imgs = [(f"{s['id']} {settle_time(s):.1f}s", grab(page, settle_time(s))) for s in tl["shots"]]
            per = cols * 2
            for part in range(0, len(imgs), per):
                files.append(rel(_sheet(imgs[part:part + per], cols, out_dir / f"sheet_{part // per + 1}.jpg", W, H, scale), p.dir))
        browser.close()
    return {"files": files}


def collect_cues(p: Project) -> dict:
    tl = _check_timeline(p)
    if not any(s.get("scene") for s in tl["shots"]):
        write_json(p.build / "scene_cues.json", [])
        return {"cues": 0, "note": "没有自定义画面"}
    prepare_frames(p, tl)
    font = assets_mod.font_path(p)
    with serve(p.dir, font) as url, _playwright()() as pw:
        browser = pw.chromium.launch()
        page = open_page(browser, url)
        cues = page.evaluate("window.collectCues()")
        browser.close()
    write_json(p.build / "scene_cues.json", cues)
    return {"cues": len(cues), "file": rel(p.build / "scene_cues.json", p.dir)}


def render_clip(p: Project, t0: float, t1: float, out: Path, subs: bool = True, blur: int = 1, only_scene: bool = False) -> Path:
    """单进程渲染一小段无声画面。only_scene：只画背景 + 自定义画面（可编辑剪映草稿补代码画面镜头用）。"""
    tl = _check_timeline(p)
    prepare_frames(p, tl)
    fps = tl["fps"]
    out.parent.mkdir(parents=True, exist_ok=True)
    _segment((str(p.dir), 0, round(t0 * fps), round(t1 * fps), fps, int(p.cfg["render"].get("crf", 18)), blur,
              p.cfg["render"].get("format", "png"), str(out.parent), ("" if subs else "&subs=0") + ("&only=scene" if only_scene else ""), str(out)))
    return out
