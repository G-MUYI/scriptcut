"""程序化配乐床：和弦 pad + 低音 + 拨弦琶音 + 轻鼓，固定随机种子可复现。

mood：bright（大调流行循环）/ calm（同和弦、更稀疏）/ tense（小调脉冲 + 心跳底鼓）。
配音时自动压低（ducking 在 mix.py），开头淡入、结尾淡出。也可以在 project.yaml 指定 audio.music.file 用自己的音乐。
"""

from __future__ import annotations

import numpy as np

SR = 48000

CHORDS = {
    "bright": [[261.63, 329.63, 392.00], [196.00, 246.94, 293.66], [220.00, 261.63, 329.63], [174.61, 220.00, 261.63]],
    "calm": [[261.63, 329.63, 392.00], [220.00, 261.63, 329.63], [174.61, 220.00, 261.63], [196.00, 246.94, 293.66]],
    "tense": [[220.00, 261.63, 329.63], [174.61, 220.00, 261.63], [196.00, 246.94, 293.66], [164.81, 207.65, 246.94]],
}


def _t(d):
    return np.arange(max(1, int(d * SR))) / SR


def _place(buf, sig, at, gain=1.0, pan=0.0):
    i = int(round(at * SR))
    if i >= buf.shape[1] or i + len(sig) <= 0:
        return
    if i < 0:
        sig, i = sig[-i:], 0
    n = min(len(sig), buf.shape[1] - i)
    l, r = np.cos((pan + 1) * np.pi / 4) * 1.414, np.sin((pan + 1) * np.pi / 4) * 1.414
    buf[0, i:i + n] += sig[:n] * gain * l
    buf[1, i:i + n] += sig[:n] * gain * r


def bed(total: float, mood: str = "bright", bpm: float = 104, seed: int = 20261105) -> np.ndarray:
    rng = np.random.default_rng(seed)
    chords = CHORDS.get(mood, CHORDS["bright"])
    beat = 60 / bpm
    bar = beat * 4
    out = np.zeros((2, int(total * SR) + 2 * SR))
    sparse = mood == "calm"
    tense = mood == "tense"
    for b in range(int(total / bar) + 2):
        ch = chords[(b // 2) % 4]
        t0 = b * bar
        t = _t(bar)
        env = np.minimum(1, t / 0.08) * np.exp(-t * 0.6)
        pad = sum(np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t + 0.5) for f in ch) / 3
        _place(out, pad * env * (0.08 if tense else 0.10), t0)
        tb = _t(beat * 2)
        bass = np.sin(2 * np.pi * ch[0] / 2 * tb) * np.exp(-tb * 2.5) * 0.22
        _place(out, bass, t0)
        _place(out, bass, t0 + beat * 2)
        steps = 4 if sparse else 8
        for k in range(steps):
            f = ch[k % 3] * (2 if k % 4 >= 2 else 1)
            tp = _t(0.35)
            pl = (np.sin(2 * np.pi * f * tp) + 0.4 * np.sin(4 * np.pi * f * tp)) * np.exp(-tp * 11) * 0.06
            _place(out, pl, t0 + k * bar / steps, pan=0.35 if k % 2 else -0.35)
        for k in range(4):
            tk = _t(0.25)
            if k % 2 == 0 or tense:
                fk = 40 + 80 * np.exp(-tk * 30)
                _place(out, np.sin(2 * np.pi * np.cumsum(fk) / SR) * np.exp(-tk * 14) * (0.2 if sparse else 0.28),
                       t0 + k * beat)
            if not sparse:
                th = _t(0.05)
                hat = np.diff(rng.standard_normal(len(th) + 1)) * np.exp(-th * 90) * 0.035
                _place(out, hat, t0 + k * beat + beat / 2, pan=0.2)
    out = out[:, :int(total * SR)]
    fi, fo = min(out.shape[1], int(0.6 * SR)), min(out.shape[1], int(2.0 * SR))
    out[:, :fi] *= np.linspace(0, 1, fi)
    out[:, out.shape[1] - fo:] *= np.linspace(1, 0, fo)
    return out
