"""识别对齐：fish.audio 语音识别（带时间戳分段）+ 与原文逐字匹配。

- 识别与原文一致的字直接取识别时间；
- 写法不同的片段（「10万」念成「十万」、错别字）把这段原文均匀铺在对应识别片段的时间范围里；
- 原文有、识别里没有的字在前后已知时间之间插值；
- 标点取下一个字的开口时间。
识别原始结果按音频内容哈希缓存（build/asr.json），重跑不重复计费。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import List, Tuple

import requests

from .. import config
from ..text import norm_char
from ..util import ScriptCutError, md5_file, read_json, write_json

ASR_URL = "https://api.fish.audio/v1/asr"
ASR_MODEL = "transcribe-1-pro"


def transcribe(audio: Path, cache: Path, language: str = "zh") -> dict:
    key = config.env("FISH_API_KEY")
    if not key:
        raise ScriptCutError("缺少 FISH_API_KEY（.env）；没有 Key 时请用 --method silence 离线对齐")
    digest = md5_file(audio)
    data = read_json(cache)
    if data and data.get("audio_md5") == digest:
        return data
    with open(audio, "rb") as f:
        r = requests.post(ASR_URL, headers={"Authorization": f"Bearer {key}", "model": ASR_MODEL},
                          data={"language": language, "ignore_timestamps": "false"},
                          files={"audio": (audio.name, f)}, timeout=600, proxies=config.proxies())
    if r.status_code != 200:
        raise ScriptCutError(f"fish.audio 识别失败（HTTP {r.status_code}）：{r.text[:300]}")
    data = r.json()
    if not data.get("segments"):
        raise ScriptCutError("fish.audio 没有返回时间戳分段（检查 ignore_timestamps 参数或音频内容）")
    data["audio_md5"] = digest
    write_json(cache, data)
    return data


def text_only(audio: Path, language: str = "zh") -> str:
    """只要识别文字（TTS 回听校对用），不缓存。"""
    key = config.env("FISH_API_KEY")
    r = requests.post(ASR_URL, headers={"Authorization": f"Bearer {key}"}, timeout=120, proxies=config.proxies(),
                      files={"audio": (audio.name, audio.read_bytes())}, data={"language": language})
    return r.json().get("text", "") if r.status_code == 200 else ""


def normalize_segments(data: dict, duration: float) -> List[dict]:
    segs = data["segments"]
    # 部分接口返回毫秒
    if segs and max(float(s["end"]) for s in segs) > max(duration, 1) * 10:
        segs = [{**s, "start": float(s["start"]) / 1000, "end": float(s["end"]) / 1000} for s in segs]
    return segs


def asr_chars(segments: List[dict]) -> List[Tuple[str, float, float]]:
    """每段的时间均分给段内每个字 → [(字, 开始, 结束)]"""
    out = []
    for seg in segments:
        chars = [c for c in (norm_char(ch) for ch in seg["text"]) if c]
        if not chars:
            continue
        t0, t1 = float(seg["start"]), float(seg["end"])
        step = (t1 - t0) / len(chars)
        out += [(c, t0 + i * step, t0 + (i + 1) * step) for i, c in enumerate(chars)]
    return out


def match(script: str, segments: List[dict], duration: float) -> Tuple[List[float], List[float], float]:
    """→ (逐字开始, 逐字结束, 匹配率)；长度都等于 len(script)。"""
    heard = asr_chars(segments)
    pos = [i for i, c in enumerate(script) if norm_char(c)]
    want = [norm_char(script[i]) for i in pos]
    got = [c for c, _, _ in heard]
    st: List[float | None] = [None] * len(pos)
    en: List[float | None] = [None] * len(pos)
    matched = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, want, got, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                st[i1 + k], en[i1 + k] = heard[j1 + k][1], heard[j1 + k][2]
            matched += i2 - i1
        elif tag == "replace":
            t0, t1 = heard[j1][1], heard[j2 - 1][2]
            n = i2 - i1
            for k in range(n):
                st[i1 + k] = t0 + (t1 - t0) * k / n
                en[i1 + k] = t0 + (t1 - t0) * (k + 1) / n
    known = [k for k, t in enumerate(st) if t is not None]
    if not known:
        raise ScriptCutError("识别结果与文案完全对不上：请确认音频和文案是同一版")
    for k in range(len(st)):
        if st[k] is not None:
            continue
        prev = max((j for j in known if j < k), default=None)
        nxt = min((j for j in known if j > k), default=None)
        if prev is None:
            st[k] = st[nxt]
        elif nxt is None:
            st[k] = min(duration, st[prev] + 0.2 * (k - prev))
        else:
            st[k] = st[prev] + (st[nxt] - st[prev]) * (k - prev) / (nxt - prev)
    for k in range(1, len(st)):
        st[k] = max(st[k], st[k - 1])
    for k in range(len(en)):
        nxt_start = st[k + 1] if k + 1 < len(st) else duration
        en[k] = min(max(en[k] if en[k] is not None else nxt_start, st[k]), max(nxt_start, st[k]))
    # 展开到全稿（标点：开始取下一个字，结束取上一个字）
    starts, ends = [0.0] * len(script), [0.0] * len(script)
    j, nxt_t = len(pos) - 1, duration
    for i in range(len(script) - 1, -1, -1):
        if j >= 0 and pos[j] == i:
            nxt_t = st[j]
            j -= 1
        starts[i] = nxt_t
    j, prev_t = 0, 0.0
    for i in range(len(script)):
        if j < len(pos) and pos[j] == i:
            prev_t = en[j]
            j += 1
        ends[i] = prev_t
    return starts, ends, matched / max(1, len(pos))
