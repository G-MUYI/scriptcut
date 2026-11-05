"""文本工具：数字的中文读法（给 TTS 的 spoken）、按句切分（逐句配音）、文案读取。

规则：上屏文字（copy）永远一字不改；spoken 只是给 TTS 的读法，比如「30 毫秒」→「三十毫秒」、「5.4%」→「百分之五点四」。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from .align.silence import read_int

DIG = "零一二三四五六七八九"
SENT_END = "。！？!?\n"
SOFT_BREAK = "，；：,;:"
MEASURE_LIANG = "个位名只条件张台天年月周次倍成种项家人席院辆本份块栋套"  # 「2个」读「两个」


def cn_number(s: str) -> str:
    """'2397' → 两千三百九十七（整数部分用 read_int，千 / 百 / 万 / 亿 前的「二」读「两」；小数逐位读）。"""
    if "." in s:
        a, b = s.split(".", 1)
        return cn_number(a or "0") + "点" + "".join(DIG[int(c)] for c in b if c.isdigit())
    out = read_int(int(s))
    return re.sub(r"(^|[零万亿])二(?=[千百万亿])", lambda m: m.group(1) + "两", out)


def spoken(text: str) -> str:
    """给 TTS 的读法：百分比、年份逐位、「2+量词」读「两」、其余数字转中文；去掉 TTS 会读错的引号与破折号。"""
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 千分位
    t = re.sub(r"(\d+(?:\.\d+)?)%", lambda m: "百分之" + cn_number(m.group(1)), t)
    t = re.sub(r"(?<![\d.])(\d{4})(?=年)", lambda m: "".join(DIG[int(c)] for c in m.group(1)), t)
    t = re.sub(rf"(?<![\d.])2(?=[{MEASURE_LIANG}])", "两", t)
    t = re.sub(r"\d+(?:\.\d+)?", lambda m: cn_number(m.group(0)), t)
    t = t.replace("——", "，").replace("—", "，")
    for q in "“”\"'‘’「」『』":
        t = t.replace(q, "")
    return t


def sentence_spans(script: str, max_len: int = 80) -> List[Tuple[int, int]]:
    """把全稿切成配音句 [(i, j)]：句末标点处切；超长句再在逗号处切。标点归属前一句。"""
    spans, s = [], 0
    for i, c in enumerate(script):
        if c in SENT_END:
            j = i + 1
            while j < len(script) and script[j] in "”’」』）)":
                j += 1
            if j > s:
                spans.append((s, j))
            s = j
    if s < len(script):
        spans.append((s, len(script)))
    spans = [(a, b) for a, b in spans if script[a:b].strip()]
    out = []
    for a, b in spans:
        out += _split_long(script, a, b, max_len)
    return out


def _split_long(script: str, a: int, b: int, max_len: int) -> List[Tuple[int, int]]:
    if b - a <= max_len:
        return [(a, b)]
    cuts = [i + 1 for i in range(a, b - 1) if script[i] in SOFT_BREAK]
    if not cuts:
        return [(a, b)]
    mid = min(cuts, key=lambda p: abs(p - (a + b) / 2))
    return _split_long(script, a, mid, max_len) + _split_long(script, mid, b, max_len)


def read_script(path: Path) -> str:
    """读文案：.txt / .md 原样；.docx 读全部段落。去掉段落换行拼成一段（一字不改）。"""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        try:
            import docx  # python-docx
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("读取 .docx 需要 python-docx：pip install python-docx") from e
        paras = [p.text for p in docx.Document(str(path)).paragraphs]
    else:
        paras = path.read_text(encoding="utf-8").splitlines()
    return "".join(p.strip() for p in paras if p.strip())


def norm_char(c: str) -> str:
    """参与识别匹配的字：汉字、字母、数字（小写）；其余返回空串。"""
    return c.lower() if c.isalnum() else ""
