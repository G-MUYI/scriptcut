"""混音：配音 + 音效（按 timeline.cues 摆放，冲击类按峰值对齐）+ 配乐（配音时自动压低）→ 两遍 loudnorm。

输出 build/audio/：stem_vo.wav / stem_sfx.wav / stem_music.wav / mix.wav / master.wav（默认 -14 LUFS），
以及 sfx/<名字>.wav 音效库（剪映草稿逐条引用）。视频原声（element.volume > 0 的视频素材）也在这里混进去。
"""

from __future__ import annotations


import numpy as np

from ..project import Project
from ..util import SR, ScriptCutError, decode_audio, loudness, loudnorm, read_json, rel, write_audio, write_json
from . import music as music_mod
from . import sfx as sfx_mod


def place(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0, align: str = "start"):
    """单声道 sig 放进立体声 buf 的 at 秒处。align：start 开头对齐 / peak 峰值对齐 / end 结尾对齐。"""
    if align == "peak":
        at -= sfx_mod.peak_offset(sig)
    elif align == "end":
        at -= len(sig) / SR
    i = int(round(at * SR))
    if i >= buf.shape[1] or i + len(sig) <= 0 or len(sig) == 0:
        return
    if i < 0:
        sig, i = sig[-i:], 0
    n = min(len(sig), buf.shape[1] - i)
    l, r = np.cos((pan + 1) * np.pi / 4) * 1.414, np.sin((pan + 1) * np.pi / 4) * 1.414
    buf[0, i:i + n] += sig[:n] * gain * l
    buf[1, i:i + n] += sig[:n] * gain * r


def _movavg(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x]))
    y = (c[n:] - c[:-n]) / n
    pad = n // 2
    return np.concatenate([np.full(pad, y[0]), y, np.full(len(x) - len(y) - pad, y[-1])])


def build(p: Project) -> dict:
    tl = read_json(p.build / "timeline.json")
    if not tl:
        raise ScriptCutError("还没有 build/timeline.json：先运行 timeline")
    total = float(tl["duration"])
    N = int(total * SR)
    out = p.build / "audio"
    out.mkdir(parents=True, exist_ok=True)
    acfg = p.cfg.get("audio", {})
    sfx_cfg = {**p.style.get("sfx", {}), **(acfg.get("sfx") or {})}
    mus_cfg = {**p.style.get("music", {}), **(acfg.get("music") or {})}

    vo = np.zeros((2, N), dtype=np.float32)
    if tl.get("voice") and (p.dir / tl["voice"]).exists():
        place(vo, decode_audio(p.dir / tl["voice"]), 0.0)
    # 视频素材原声
    for s in tl["shots"]:
        for it in s["items"]:
            if it["kind"] == "video" and it.get("volume", 0) > 0:
                x = decode_audio(p.dir / it["src"])
                i0 = int(float(it.get("clip_in", 0)) * SR)
                seg = x[i0:i0 + int((it["end"] - it["appear"]) * SR)]
                place(vo, seg, it["appear"], float(it["volume"]))

    sfx = np.zeros((2, N), dtype=np.float32)
    master_gain = float(sfx_cfg.get("master", 0.6))
    gains = sfx_cfg.get("gain", {}) or {}
    used = set()
    if sfx_cfg.get("enabled", True):
        for c in tl["cues"]:
            name = c["name"]
            if name not in sfx_mod.SFX:
                continue
            used.add(name)
            g = master_gain * float(gains.get(name, 0.7)) * float(c.get("gain", 1.0))
            pan = ((hash(round(c["t"], 2)) % 7) - 3) * 0.05
            place(sfx, sfx_mod.get(name), c["t"], g, pan, "peak" if name in sfx_mod.PEAK_ALIGN else "start")
    sfx_mod.build_library(out / "sfx", sorted(used) or None)

    mus = np.zeros((2, N), dtype=np.float32)
    if mus_cfg.get("enabled"):
        if mus_cfg.get("file"):
            x = decode_audio(p.path(mus_cfg["file"]), channels=2)
            reps = int(np.ceil(N / max(1, x.shape[1])))
            mus = np.tile(x, (1, reps))[:, :N].astype(np.float32)
        else:
            mus = music_mod.bed(total, mus_cfg.get("mood", "bright"), float(mus_cfg.get("bpm", 104))).astype(np.float32)
        env = _movavg(np.abs(vo[0]).astype(np.float64), int(0.25 * SR))
        env = env / (env.max() + 1e-9)
        mus *= (1 - float(mus_cfg.get("duck", 0.65)) * np.clip(env * 6, 0, 1)).astype(np.float32)
        mus *= float(mus_cfg.get("gain", 0.6))
    write_audio(out / "stem_vo.wav", vo)
    write_audio(out / "stem_sfx.wav", sfx)
    write_audio(out / "stem_music.wav", mus)
    mix = vo + sfx + mus
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 1.0:
        mix /= peak
    write_audio(out / "mix.wav", mix)
    target = float(acfg.get("loudness", -14))
    master = loudnorm(out / "mix.wav", out / "master.wav", I=target)
    res = {"master": rel(master, p.dir), "duration": round(total, 2), "cues": len(tl["cues"]), "sfx_used": sorted(used),
           "music": bool(mus_cfg.get("enabled")), "lufs": {"master": loudness(master), "voice": loudness(out / "stem_vo.wav"),
                                                             "sfx": loudness(out / "stem_sfx.wav")}}
    write_json(p.build / "report" / "audio.json", res)
    return res
