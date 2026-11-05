"""配音阶段：TTS 逐句合成 或 录音对齐 → build/voice/voice.wav + build/timing.json。

配音是全片的主时钟：换镜点、元素入场、标题逐字、音效全部跟着这里的逐字时间走。

TTS（voice.source = tts）
  provider: edge（免费，无需 Key）| fish（Fish Audio，需 FISH_API_KEY + FISH_VOICE_ID）| auto（有 Fish Key 用 fish）
  逐句合成 → 裁首尾静音 → 按停顿规则拼接 → 句内用静音吸附算法分配到字。
  build/voice/manifest.json 记录每句合成时的读法 / 音色 / 语速：文本变了才重合成，不会误用旧音频。
  fish + verify: true 时，逐句 ASR 回听，相似度 < 0.9 自动重合成（最多 3 次，留最好的一条）。

录音（voice.source = file，或 input/voice.* 存在）
  align: asr（fish.audio 识别 + 逐字匹配，最准）| silence（离线：ffmpeg 静音 + 动态规划吸附标点）| auto
  开头的无口播镜头（duration）会在配音前补静音。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import difflib
import re
import time
from pathlib import Path

import numpy as np
import requests

from . import config
from . import timing as tmod
from .align import asr as asr_mod
from .align.silence import align_text
from .project import Project
from .text import sentence_spans, spoken
from .util import (SR, ScriptCutError, decode_audio, detect_silences, probe_duration, read_json, rel, trim_silence,
                   write_audio, write_json)


# ---------------------------------------------------------------- TTS 提供方

class EdgeTTS:
    name = "edge"

    def __init__(self, voice: str = "", speed: float = 1.0):
        import importlib.util
        if importlib.util.find_spec("edge_tts") is None:
            raise ScriptCutError("edge TTS 需要 edge-tts：pip install edge-tts")
        self.voice = voice or "zh-CN-YunxiNeural"
        self.speed = speed

    def ident(self) -> dict:
        return {"provider": self.name, "voice": self.voice, "speed": self.speed}

    def synth(self, text: str, out: Path) -> Path:
        import edge_tts
        rate = f"{round((self.speed - 1) * 100):+d}%"

        async def run():
            await edge_tts.Communicate(text, self.voice, rate=rate).save(str(out))

        err = None
        for attempt in range(3):
            try:  # 在独立线程里跑事件循环：MCP Server 等调用方自己可能已有事件循环
                with concurrent.futures.ThreadPoolExecutor(1) as ex:
                    ex.submit(asyncio.run, run()).result(timeout=120)
                if out.exists() and out.stat().st_size > 0:
                    return out
            except Exception as e:  # 网络抖动重试
                err = e
            time.sleep(1 + attempt * 2)
        raise ScriptCutError(f"edge-tts 合成失败：{err}")


class FishTTS:
    name = "fish"
    URL = "https://api.fish.audio/v1/tts"

    def __init__(self, voice: str = "", speed: float = 1.0, model: str = ""):
        self.key = config.env("FISH_API_KEY")
        self.voice = voice or config.env("FISH_VOICE_ID")
        self.model = model or config.env("FISH_MODEL", "s1")
        self.speed = speed
        if not self.key:
            raise ScriptCutError("fish TTS 需要 .env 里的 FISH_API_KEY（没有 Key 可把 provider 改成 edge）")
        if not self.voice:
            raise ScriptCutError("fish TTS 需要音色：project.yaml 的 voice.tts.voice 或 .env 的 FISH_VOICE_ID")

    def ident(self) -> dict:
        return {"provider": self.name, "voice": self.voice, "speed": self.speed, "model": self.model}

    def synth(self, text: str, out: Path) -> Path:
        body = {"text": text, "reference_id": self.voice, "format": "mp3", "mp3_bitrate": 192,
                "normalize": True, "latency": "normal"}
        if self.speed != 1.0:
            body["prosody"] = {"speed": self.speed}
        err = ""
        for attempt in range(4):
            try:
                r = requests.post(self.URL, json=body, timeout=180, proxies=config.proxies(),
                                  headers={"Authorization": f"Bearer {self.key}", "model": self.model})
                if r.status_code == 200:
                    out.write_bytes(r.content)
                    return out
                err = f"HTTP {r.status_code}: {r.text[:200]}"
            except requests.RequestException as e:
                err = str(e)
            time.sleep(2 + attempt * 3)
        raise ScriptCutError(f"Fish TTS 失败：{err}")


def get_provider(p: Project):
    cfg = p.cfg["voice"]["tts"]
    name = cfg.get("provider", "auto")
    if name == "auto":
        name = "fish" if (config.env("FISH_API_KEY") and (cfg.get("voice") or config.env("FISH_VOICE_ID"))) else "edge"
    if name == "edge":
        v = str(cfg.get("voice", "") or "")
        return EdgeTTS(v if "Neural" in v else "", float(cfg.get("speed", 1.0)))  # Fish 的音色 ID 不能给 edge 用
    if name == "fish":
        return FishTTS(cfg.get("voice", ""), float(cfg.get("speed", 1.0)), cfg.get("model", ""))
    raise ScriptCutError(f"未知 TTS provider：{name}（可选 edge / fish / auto）")


def _norm(s: str) -> str:
    return re.sub(r"[^一-鿿A-Za-z0-9]", "", s).upper()


# ---------------------------------------------------------------- 句内对齐

def _clip_chars(text: str, wav: Path, dur: float, cps: float) -> tuple[list[float], list[float], str]:
    """句子片段内逐字时间：静音吸附（逗号处的真实停顿做锚点）；失败退回按音节均分。"""
    try:
        sil = detect_silences(wav, -38.0, 0.08)
        a = align_text(text, sil, dur)
        return a.char_start, a.char_end, "silence"
    except Exception:
        d, st, en = tmod.estimate_clip(text, cps)
        k = dur / d if d else 1
        return [x * k for x in st], [x * k for x in en], "weights"


# ---------------------------------------------------------------- TTS

def run_tts(p: Project, force: bool = False, progress=print) -> dict:
    provider = get_provider(p)
    vdir = p.build / "voice"
    (vdir / "clips").mkdir(parents=True, exist_ok=True)
    manifest = read_json(vdir / "manifest.json", {}) or {}
    spans = sentence_spans(p.script)
    replace = p.cfg["voice"].get("replace", {}) or {}
    cps = float(p.style.get("timing", {}).get("cps", 5.0))
    verify = bool(p.cfg["voice"]["tts"].get("verify")) and provider.name == "fish"
    clips, wavs, notes = [], [], []
    made = 0
    for k, (a, b) in enumerate(spans):
        copy = p.script[a:b]
        say = copy
        for src, dst in replace.items():
            say = say.replace(src, dst)
        say = spoken(say)
        key = f"{k + 1:03d}"
        ident = {"spoken": say, **provider.ident()}
        raw = vdir / "clips" / f"{key}.mp3"
        wav = vdir / "clips" / f"{key}.wav"
        if force or manifest.get(key) != ident or not raw.exists():
            provider.synth(say, raw)
            made += 1
            if verify:
                sim = difflib.SequenceMatcher(None, _norm(say), _norm(asr_mod.text_only(raw))).ratio()
                tries = 0
                while sim < 0.9 and tries < 3:
                    alt = raw.with_name(f"{key}_alt.mp3")
                    provider.synth(say, alt)
                    s2 = difflib.SequenceMatcher(None, _norm(say), _norm(asr_mod.text_only(alt))).ratio()
                    if s2 > sim:
                        alt.replace(raw)
                        sim = s2
                    else:
                        alt.unlink(missing_ok=True)
                    tries += 1
                if sim < 0.9:
                    notes.append(f"第 {k + 1} 句 ASR 回听相似度 {sim:.2f}，可能漏字：{copy}")
            manifest[key] = ident
            write_json(vdir / "manifest.json", manifest)
            progress(f"  合成 {key}: {copy}")
        x = trim_silence(decode_audio(raw))
        write_audio(wav, x)
        dur = len(x) / SR
        st, en, how = _clip_chars(copy, wav, dur, cps)
        clips.append((dur, st, en))
        wavs.append(x)
        if how != "silence":
            notes.append(f"第 {k + 1} 句句内按音节均分（没检测到可用停顿）")
    tm, placements = tmod.sequence(p, spans, clips, f"tts:{provider.name}")
    total = int(round(tm.duration * SR))
    buf = np.zeros(total, dtype=np.float32)
    for k, t0 in placements:
        i = int(round(t0 * SR))
        x = wavs[k][: max(0, total - i)]
        buf[i:i + len(x)] += x
    voice = vdir / "voice.wav"
    write_audio(voice, buf)
    tm.audio = rel(voice, p.dir)
    tm.report = {"sentences": len(spans), "synthesized": made, **provider.ident(), "notes": notes}
    tm.save(p)
    return {"timing": rel(p.build / "timing.json", p.dir), "voice": tm.audio, "duration": tm.duration,
            "sentences": len(spans), "synthesized": made, "provider": provider.name, "notes": notes}


# ---------------------------------------------------------------- 录音对齐

def run_align(p: Project, method: str = "auto", audio: Path | None = None) -> dict:
    src = Path(audio) if audio else p.voice_file()
    if not src or not src.exists():
        raise ScriptCutError("没有配音文件：放到 input/voice.mp3（或 wav/m4a），或在 project.yaml 写 voice.file")
    if method in ("auto", "", None):
        method = p.cfg["voice"].get("align", "auto")
    if method == "auto":
        method = "asr" if config.env("FISH_API_KEY") else "silence"
    lead_silent = []
    for s in p.shots:
        if not s.silent:
            break
        lead_silent.append(s)
    mid_silent = [s.id for s in p.shots if s.silent and s not in lead_silent]
    offset = sum(s.duration for s in lead_silent)
    vdir = p.build / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    x = decode_audio(src)
    dur = len(x) / SR
    report: dict = {"method": method, "source_file": rel(src, p.dir)}
    ratio = None
    if method == "asr":
        data = asr_mod.transcribe(src, p.build / "asr.json")
        segs = asr_mod.normalize_segments(data, dur)
        st, en, ratio = asr_mod.match(p.script, segs, dur)
        report["match_ratio"] = round(ratio, 3)
        if ratio < 0.85:
            report["warning"] = "匹配率偏低：确认配音和文案是同一版（多读、漏读、改词都会拉低匹配率）"
    elif method == "silence":
        # 真人录音停顿长，TTS / 语速快的录音停顿短：几组静音阈值都试一遍，取「句末没停顿 + 语速异常」最少的一组
        full = probe_duration(src)
        best = None
        for noise_db, min_sil in ((-40.0, 0.2), (-38.0, 0.12), (-35.0, 0.08)):
            try:
                a = align_text(p.script, detect_silences(src, noise_db, min_sil), full)
            except (ValueError, RuntimeError):
                continue
            r = a.report()
            bad = len(r["skipped_sentence_ends"]) + 2 * len(r["rate_outliers"]) + 0.2 * len(r["skipped_gaps"])
            if best is None or bad < best[0]:
                best = (bad, a, r, (noise_db, min_sil))
        if best is None:
            raise ScriptCutError("静音对齐失败：配音里检测不到停顿，改用 --method asr 或 estimate")
        _, a, r, params = best
        st, en = a.char_start, a.char_end
        report.update(r)
        report["silence_params"] = {"noise_db": params[0], "min_silence": params[1]}
    elif method == "estimate":
        tm = tmod.estimate(p)
        k = dur / max(tm.duration, 1e-6)
        st, en = [t * k for t in tm.start], [t * k for t in tm.end]
    else:
        raise ScriptCutError(f"未知对齐方法 {method}（asr / silence / estimate / auto）")
    voice = vdir / "voice.wav"
    pad = np.zeros(int(round(offset * SR)), dtype=np.float32)
    write_audio(voice, np.concatenate([pad, x]))
    slots, t = {}, 0.0
    for s in lead_silent:
        slots[s.id] = [round(t, 3), round(t + s.duration, 3)]
        t += s.duration
    if mid_silent:
        report["ignored_silent_shots"] = mid_silent
        report["warning_silent"] = "录音模式下只支持开头的无口播镜头；中间的无口播镜头已忽略（改用 TTS 模式可任意插入）"
    tail = float(p.style.get("timing", {}).get("tail", 0.5))
    tm = tmod.Timing(script=p.script, start=[round(v + offset, 4) for v in st], end=[round(v + offset, 4) for v in en],
                     duration=round(offset + dur + tail, 3), source=method, audio=rel(voice, p.dir), slots=slots,
                     match_ratio=ratio, report=report)
    tm.save(p)
    return {"timing": rel(p.build / "timing.json", p.dir), "voice": tm.audio, "duration": tm.duration, **report}


def run(p: Project, force: bool = False, method: str = "auto", progress=print) -> dict:
    src = p.voice_source()
    if src == "file":
        return run_align(p, method)
    if src == "tts":
        return run_tts(p, force, progress)
    if src == "none":
        tm = tmod.estimate(p)
        tm.save(p)
        return {"timing": rel(p.build / "timing.json", p.dir), "voice": "", "duration": tm.duration, "method": "estimate"}
    raise ScriptCutError(f"未知 voice.source：{src}（auto / tts / file / none）")
