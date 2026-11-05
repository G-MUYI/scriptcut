"""逐字时间轴（build/timing.json）：全稿每个字的开口 / 收尾时间（秒，相对 build/voice/voice.wav 开头）。

三种来源产出同一种结构，下游（时间轴、Canvas、剪映）不用关心来源：
  tts:<provider>  逐句合成 → 句子按停顿规则排进时间轴 → 句内用静音吸附算法分配到字
  asr / silence   录好的整段配音 → 识别对齐 / 离线静音对齐
  estimate        没有配音 → 按语速估算（先看节奏和版式）

结构：{"script", "start": [...], "end": [...], "duration", "source", "audio", "slots": {无口播镜头: [s, e]},
       "sentences": [{"i", "j", "start", "end"}], "match_ratio", "report"}
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .align.silence import char_weights
from .project import Project
from .text import sentence_spans
from .util import read_json, write_json

PAUSE = {"，": 0.22, "、": 0.18, "；": 0.3, "：": 0.25, "。": 0.4, "？": 0.4, "！": 0.4, "—": 0.15, "…": 0.3,
         ",": 0.22, ";": 0.3, ":": 0.25, ".": 0.0, "?": 0.4, "!": 0.4}


@dataclass
class Timing:
    script: str
    start: List[float]
    end: List[float]
    duration: float
    source: str
    audio: str = ""
    slots: dict = field(default_factory=dict)
    sentences: list = field(default_factory=list)
    match_ratio: Optional[float] = None
    report: dict = field(default_factory=dict)

    def save(self, p: Project) -> Path:
        return write_json(p.build / "timing.json", asdict(self), indent=None)

    @property
    def estimated(self) -> bool:
        return self.source == "estimate"


def load(p: Project) -> Optional[Timing]:
    """读 build/timing.json；文案改过（不一致）返回 None。"""
    d = read_json(p.build / "timing.json")
    if not d or d.get("script") != p.script:
        return None
    return Timing(**{k: d.get(k) for k in Timing.__dataclass_fields__ if k in d})


def gaps(p: Project) -> dict:
    g = {"lead": 0.3, "sentence": 0.15, "segment": 0.35, "chapter_before": 0.5, "chapter_after": 0.55, "tail": 0.6}
    g.update(p.style.get("voice_gaps", {}))
    g.update(p.cfg["voice"].get("gaps", {}) or {})
    return g


# ---------------------------------------------------------------- 估算

def estimate_clip(text: str, cps: float) -> Tuple[float, List[float], List[float]]:
    """单句估算：音节按 1/cps 秒，标点按停顿表。→ (时长, 逐字开始, 逐字结束)"""
    w = char_weights(text)
    t, st, en = 0.0, [], []
    for c, x in zip(text, w):
        st.append(t)
        if x > 0:
            t += x / cps
        elif c in PAUSE:
            t += PAUSE[c]
        en.append(t)
    return max(t, 0.2), st, en


# ---------------------------------------------------------------- 排句

def _shot_at(p: Project, i: int):
    for s in p.shots:
        if not s.silent and s.offset <= i < s.offset + len(s.text):
            return s
    return None


def sequence(p: Project, spans: Sequence[Tuple[int, int]], clips: Sequence[Tuple[float, List[float], List[float]]],
             source: str, audio: str = "") -> Tuple[Timing, list]:
    """把逐句片段按停顿规则排进时间轴。→ (Timing, placements[(句序号, 开始秒)])，placements 给拼接配音用。

    句间停顿：同镜头 / 同段 sentence，换段 segment，章节镜头前 chapter_before、章节标题句后 chapter_after；
    无口播镜头（duration）按顺序插进时间轴。"""
    g = gaps(p)
    n = len(p.script)
    start: List[Optional[float]] = [None] * n
    end: List[Optional[float]] = [None] * n
    events = [(s.offset, 0, s) for s in p.shots if s.silent] + [(a, 1, k) for k, (a, _) in enumerate(spans)]
    events.sort(key=lambda e: (e[0], e[1]))
    t = g["lead"]
    slots, sentences, placements = {}, [], []
    prev_shot, prev_end_char, first = None, None, True
    for off, kind, obj in events:
        if kind == 0:
            if not first:
                t += g["sentence"]
            slots[obj.id] = [round(t, 3), round(t + obj.duration, 3)]
            t += obj.duration
            first, prev_shot = False, obj
            continue
        k = obj
        a, b = spans[k]
        dur, rs, re_ = clips[k]
        shot = _shot_at(p, a)
        if not first:
            gap = g["sentence"]
            if shot is not None and a == shot.offset:
                if shot.chapter is not None:
                    gap = max(gap, g["chapter_before"])
                elif prev_shot is not None and shot.seg != prev_shot.seg:
                    gap = max(gap, g["segment"])
            if prev_shot is not None and prev_shot.chapter is not None and prev_end_char == prev_shot.offset + len(prev_shot.text):
                gap = max(gap, g["chapter_after"])
            t += gap
        for i in range(a, b):
            start[i] = t + rs[i - a]
            end[i] = t + re_[i - a]
        sentences.append({"i": a, "j": b, "start": round(t, 3), "end": round(t + dur, 3)})
        placements.append((k, t))
        t += dur
        first = False
        prev_shot = _shot_at(p, b - 1) or shot
        prev_end_char = b
    total = t + g["tail"]
    # 不在任何句子里的字（空白）取前后时间
    last = 0.0
    for i in range(n):
        if start[i] is None:
            start[i] = last
            end[i] = last
        last = end[i]
    tm = Timing(script=p.script, start=[round(x, 4) for x in start], end=[round(x, 4) for x in end],
                duration=round(total, 3), source=source, audio=audio, slots=slots, sentences=sentences)
    return tm, placements


def estimate(p: Project) -> Timing:
    cps = float(p.style.get("timing", {}).get("cps", 5.0))
    spans = sentence_spans(p.script)
    clips = [estimate_clip(p.script[a:b], cps) for a, b in spans]
    tm, _ = sequence(p, spans, clips, "estimate")
    tm.report = {"note": f"按 {cps:g} 字/秒估算；拿到配音后运行 voice 重新对齐"}
    return tm


def estimate_total(p: Project) -> float:
    try:
        return estimate(p).duration
    except Exception:  # 校验没通过时也要能给个数
        return len(p.script) / float(p.style.get("timing", {}).get("cps", 5.0))


def load_or_estimate(p: Project) -> Timing:
    return load(p) or estimate(p)
