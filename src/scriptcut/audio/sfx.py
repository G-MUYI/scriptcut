"""程序化音效库：正弦扫频 + 滤波噪声 + 包络，全部 numpy 合成，无版权、风格统一、可复现。

每个音效用自己名字派生的随机种子，生成结果与调用顺序无关。
冲击类（thud / stamp / impact / boom / slam）放置时按「实测峰值」对齐事件时刻（align=peak），
其余按开头对齐。来源：知识科普原型 sfx_synth + 解说视频原型 audio.py 的音色，合并整理。
"""

from __future__ import annotations

import wave
import zlib
from pathlib import Path
from typing import Callable, Dict

import numpy as np

SR = 48000


def _rng(name: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(name.encode()))


def _t(d: float) -> np.ndarray:
    return np.arange(max(1, int(SR * d))) / SR


def _env(d: float, attack: float, decay: float) -> np.ndarray:
    t = _t(d)
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / decay)


def _sweep(f0: float, f1: float, d: float, curve: float = 6.0) -> np.ndarray:
    t = _t(d)
    f = f1 + (f0 - f1) * np.exp(-curve * t / d)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _movavg(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x]))
    y = (c[n:] - c[:-n]) / n
    pad = n // 2
    return np.concatenate([np.full(pad, y[0]), y, np.full(len(x) - len(y) - pad, y[-1])])


def _lowpass(x: np.ndarray, cutoff) -> np.ndarray:
    """一阶低通，cutoff 可以是逐采样数组。"""
    cutoff = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x, dtype=float)
    acc = 0.0
    for i in range(x.size):
        acc = (1 - a[i]) * x[i] + a[i] * acc
        y[i] = acc
    return y


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return _lowpass(x, hi) - _lowpass(x, lo)


def _noise(name: str, d: float) -> np.ndarray:
    return _rng(name).standard_normal(len(_t(d)))


def _fade(x: np.ndarray, ms: float = 8) -> np.ndarray:
    n = min(len(x), int(ms / 1000 * SR))
    if n > 0:
        x = x.copy()
        x[-n:] *= np.linspace(1, 0, n)
    return x


# ---------------------------------------------------------------- 音色

def pop():
    d = 0.16
    return _sweep(1400, 320, d, 9) * _env(d, 0.002, 0.035)


def pop_soft():
    d = 0.18
    return _sweep(900, 260, d, 7) * _env(d, 0.004, 0.045) * 0.7


def boing():
    d = 0.45
    t = _t(d)
    f = 220 + 160 * np.exp(-6 * t) * np.cos(2 * np.pi * 9 * t)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(d, 0.003, 0.14)


def thud():
    d = 0.35
    body = _sweep(160, 45, d, 5) * _env(d, 0.001, 0.09)
    click = _lowpass(_noise("thud", d), 2500) * _env(d, 0.0005, 0.012) * 3
    return body + click


def whoosh(d: float = 0.32):
    t = _t(d)
    shape = np.sin(np.pi * (t / d) ** 0.6) ** 2
    cutoff = 400 + 3800 * np.sin(np.pi * t / d) ** 2
    return _lowpass(_noise("whoosh", d), cutoff) * shape * 4


def swish():
    d = 0.22
    t = _t(d)
    return _lowpass(_noise("swish", d), 1500 + 5000 * (t / d)) * np.sin(np.pi * t / d) ** 3 * 3.5


def swing():
    d = 0.7
    t = _t(d)
    tone = np.sin(2 * np.pi * np.cumsum(520 + 90 * np.sin(2 * np.pi * 1.6 * t)) / SR)
    return (tone * 0.35 + whoosh(d) * 0.6) * np.exp(-t / 0.35)


def ding():
    d = 0.6
    t = _t(d)
    tone = sum(a * np.sin(2 * np.pi * f * t) for f, a in [(1760, 1.0), (2637, 0.45), (3520, 0.2)])
    return tone * _env(d, 0.002, 0.16)


def scribble(d: float = 0.6):
    """笔尖在纸上的摩擦声：长短不一的笔画包络 + 带通噪声。"""
    rng = _rng("scribble")
    n = int(SR * d)
    env, speed = np.zeros(n), np.zeros(n)
    pos = 0
    while pos < n:
        length = int(SR * rng.uniform(0.05, 0.16))
        seg = np.arange(min(length, n - pos)) / max(length, 1)
        shape = np.minimum(1, seg / rng.uniform(0.04, 0.15)) * (1 - seg) ** rng.uniform(0.3, 1.2)
        env[pos:pos + seg.size] = shape * rng.uniform(0.55, 1.0)
        speed[pos:pos + seg.size] = np.sin(np.pi * seg) * rng.uniform(0.5, 1.0)
        pos += seg.size + int(SR * rng.uniform(0.008, 0.045))
    noise = rng.standard_normal(n)
    hiss = _band(noise, 700, 2600) * (1 - speed * 0.7) + _band(noise, 2600, 7500) * (0.35 + speed * 0.65)
    edge = np.minimum(1, np.arange(n) / (SR * 0.01)) * np.minimum(1, (n - np.arange(n)) / (SR * 0.04))
    return hiss * env * edge * 1.2


def strike():
    d = 0.18
    t = _t(d)
    x = _noise("strike", d)
    return (x - _lowpass(x, 1200)) * np.sin(np.pi * t / d) ** 0.5 * np.exp(-t / 0.08)


def stamp():
    d = 0.3
    body = _sweep(240, 90, d, 7) * _env(d, 0.0008, 0.05)
    slap = _band(_noise("stamp", d), 400, 4000) * _env(d, 0.0005, 0.02) * 2.5
    return body + slap


def tick():
    d = 0.03
    return _band(_noise("tick", d), 2000, 8000) * _env(d, 0.0003, 0.004) * 3


def counter(d: float = 0.9, ticks: int = 18):
    """数字滚动：咔嗒由密到疏（跟随 out_cubic），结尾一声叮。"""
    n = int(SR * d)
    y = np.zeros(n + int(SR * 0.6))
    one = tick()
    for k in range(ticks):
        i = int((1 - (1 - k / ticks) ** (1 / 3)) * n)
        y[i:i + one.size] += one * (0.6 + 0.4 * (1 - k / ticks))
    dg = ding() * 0.5
    y[n:n + dg.size] += dg[: len(y) - n]
    return y


def flip():
    out = np.zeros(len(_t(0.2)))
    for k, at in enumerate((0.0, 0.07)):
        x = _band(_noise(f"flip{k}", 0.03), 1500, 7000) * _env(0.03, 0.0005, 0.006) * 3
        i = int(at * SR)
        out[i:i + len(x)] += x
    return out


def head():
    """换标题：一记短擦 + 高频滴答。"""
    d = 0.24
    t = _t(d)
    sw = _band(_noise("head", d), 600, 4000) * np.sin(np.pi * t / d) ** 2 * 1.6
    tk = np.sin(2 * np.pi * 1800 * t) * _env(d, 0.001, 0.008) * 0.35
    return _fade(sw + tk)


def boom():
    d = 0.9
    t = _t(d)
    f = 45 + 50 * np.exp(-t * 9)
    return np.tanh(1.5 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * np.exp(-t * 4.5) * 0.9


def impact():
    """冲击：低频下扫 + 金属余响 + 碎裂噪声。"""
    d = 1.6
    t = _t(d)
    f = 30 + 90 * np.exp(-t * 6)
    sub = np.tanh(2 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * np.exp(-t * 2.6)
    ring = sum(a * np.sin(2 * np.pi * fr * t) * np.exp(-t * k) for fr, a, k in
               ((523, 0.3, 5), (1307, 0.18, 7), (2093, 0.1, 9)))
    crack = _band(_noise("impact", d), 800, 6000) * _env(d, 0.0005, 0.05) * 1.4
    return _fade(sub + ring + crack, 40)


def riser(d: float = 1.2):
    t = _t(d)
    f = 110 * (8 ** (t / d))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / d) ** 2 * 0.5
    air = _lowpass(_noise("riser", d), 300 + 6000 * (t / d)) * (t / d) ** 1.5 * 2.5
    return _fade(tone + air, 15)


def glitch(d: float = 0.3):
    rng = _rng("glitch")
    n = int(SR * d)
    y = np.zeros(n)
    pos = 0
    while pos < n:
        ln = int(SR * rng.uniform(0.01, 0.04))
        f = rng.uniform(200, 3000)
        t = np.arange(min(ln, n - pos)) / SR
        sq = np.sign(np.sin(2 * np.pi * f * t)) * rng.uniform(0.3, 1.0)
        y[pos:pos + len(t)] = np.round(sq * 4) / 4
        pos += ln + int(SR * rng.uniform(0, 0.02))
    return _fade(y * 0.6)


def shimmer(d: float = 0.8):
    out = np.zeros(len(_t(d + 0.8)))
    for k, f in enumerate([1046.5, 1318.5, 1568.0, 2093.0]):
        t = _t(0.8)
        x = np.sin(2 * np.pi * f * t) * _env(0.8, 0.003, 0.2)
        i = int(k * d / 4 * SR)
        out[i:i + len(x)] += x[: len(out) - i]
    return out * 0.5


def typing(d: float = 1.0, rate: float = 11):
    rng = _rng("typing")
    out = np.zeros(len(_t(d + 0.1)))
    t = 0.0
    while t < d:
        x = _band(rng.standard_normal(int(SR * 0.02)), 1500, 7000) * _env(0.02, 0.0003, 0.004) * rng.uniform(1.5, 3)
        i = int(t * SR)
        out[i:i + len(x)] += x[: len(out) - i]
        t += rng.uniform(0.6, 1.4) / rate
    return out


def error():
    d = 0.5
    t = _t(d)
    x = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (220, 233)) * 0.25
    gate = ((t % 0.18) < 0.12).astype(float)
    return _fade(x * gate * _env(d, 0.002, 0.3))


def success():
    out = np.zeros(len(_t(0.7)))
    for k, f in enumerate((1046.5, 1568.0)):
        t = _t(0.5)
        x = np.sin(2 * np.pi * f * t) * _env(0.5, 0.002, 0.12)
        i = int(k * 0.09 * SR)
        out[i:i + len(x)] += x[: len(out) - i]
    return out * 0.7


def paper():
    d = 0.16
    t = _t(d)
    return _fade(_band(_noise("paper", d), 1500, 9000) * np.sin(np.pi * t / d) ** 3 * 2.5)


def cascade(d: float = 0.6):
    penta = [523.25, 587.33, 659.25, 783.99, 880.0, 1046.5, 1174.7, 1318.5]
    out = np.zeros(len(_t(d + 0.2)))
    n = int(d * 26)
    for k in range(n):
        t = _t(0.05)
        x = np.sin(2 * np.pi * penta[(k * 3) % len(penta)] * 2 * t) * _env(0.05, 0.001, 0.014) * (1 - 0.5 * k / max(n, 1))
        i = int(k / 26 * SR)
        out[i:i + len(x)] += x[: len(out) - i]
    return out


SFX: Dict[str, Callable[[], np.ndarray]] = {
    "pop": pop, "pop_soft": pop_soft, "boing": boing, "thud": thud, "whoosh": whoosh, "swish": swish,
    "swing": swing, "ding": ding, "scribble": scribble, "strike": strike, "stamp": stamp, "counter": counter,
    "tick": tick, "flip": flip, "head": head, "boom": boom, "impact": impact, "riser": riser, "glitch": glitch,
    "shimmer": shimmer, "typing": typing, "error": error, "success": success, "paper": paper, "cascade": cascade,
}

DESCRIPTIONS = {
    "pop": "弹出（图标、道具）", "pop_soft": "轻弹（淡入、模糊显现）", "boing": "弹性", "thud": "闷砸（大字、重物落地）",
    "whoosh": "划过（滑入、甩入、转场）", "swish": "旋转", "swing": "摆动的牌子", "ding": "提示叮", "scribble": "笔划沙沙（箭头、图表生长）",
    "strike": "划掉 / 打叉", "stamp": "盖章", "counter": "数字滚动", "tick": "滴答", "flip": "翻牌", "head": "换标题",
    "boom": "低频轰（章节卡）", "impact": "冲击（炸点）", "riser": "上扬蓄力", "glitch": "故障", "shimmer": "闪光",
    "typing": "打字", "error": "报错", "success": "成功提示", "paper": "纸片", "cascade": "一串上行音",
}

PEAK_ALIGN = {"thud", "stamp", "impact", "boom", "strike"}


def peak_offset(sig: np.ndarray, win: float = 0.01) -> float:
    """包络峰值距开头的秒数（10ms 滑动平均，不被单个尖峰带偏）。"""
    n = max(1, int(win * SR))
    return float(np.argmax(np.convolve(np.abs(sig), np.ones(n) / n, mode="same"))) / SR


_cache: Dict[str, np.ndarray] = {}


def get(name: str) -> np.ndarray:
    if name not in _cache:
        y = np.asarray(SFX[name](), dtype=np.float64)
        _cache[name] = (y / (np.max(np.abs(y)) + 1e-9) * 0.85).astype(np.float32)
    return _cache[name]


def save_wav(y: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def build_library(out_dir: Path, names=None) -> Dict[str, Path]:
    """把音效写成 wav（剪映草稿用）；已存在的跳过。"""
    out = {}
    for name in names or SFX:
        p = out_dir / f"{name}.wav"
        if not p.exists():
            save_wav(get(name), p)
        out[name] = p
    return out
