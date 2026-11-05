"""顶部逐字标题的断句：按逗号分句（短句并入上一句、超长折两行、小数点不断句），支持分镜里手写断句（cap）。

纯文本计算，不依赖渲染；Canvas 引擎和剪映后端都用这里的结果。来源：知识科普原型的 caption_render 模块。
"""

from dataclasses import dataclass

BREAK = set("，。！？；：、,.!?;:—…")  # 分句标点
KEEP = set("？！?!")  # 显示在标题里的标点，其余标点不显示
QUOTES = set("\"'“”‘’「」『』《》()（）【】\n ")


@dataclass
class Caption:
    start: int  # 在全稿中的字符区间 [start, end)
    end: int
    lines: list[list[int]]  # 每行要显示的全稿字符下标


def _is_break(script: str, i: int) -> bool:
    c = script[i]
    if c not in BREAK:
        return False
    # 数字里的小数点、千分位不算分句（0.8%、1,263）
    if c in ".,:" and 0 < i < len(script) - 1 and script[i - 1].isdigit() and script[i + 1].isdigit():
        return False
    return True


def _visible(script: str, i: int) -> bool:
    return not _is_break(script, i) and script[i] not in QUOTES


def _shown(script: str, i: int) -> bool:
    return _visible(script, i) or script[i] in KEEP


def _clauses(script: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """把 script[lo:hi] 按分句标点切开，标点归属前一句。"""
    out, s = [], lo
    for i in range(lo, hi):
        if _is_break(script, i):
            out.append((s, i + 1))
            s = i + 1
    if s < hi:
        out.append((s, hi))
    return [(a, b) for a, b in out if any(_visible(script, k) for k in range(a, b))]


def _auto(script: str, lo: int, hi: int, max_line: int, max_lines: int, merge_short: int) -> list[Caption]:
    units: list[tuple[int, int]] = []
    merged = False
    for a, b in _clauses(script, lo, hi):
        n = sum(_visible(script, k) for k in range(a, b))
        if units and not merged:
            pa, pb = units[-1]
            prev_n = sum(_visible(script, k) for k in range(pa, pb))
            # 「别急」这种短句并入上一句（参考片「听着还行？别急」）；每句最多并一次，句号、分号、破折号后不并
            if n <= merge_short and prev_n + n <= max_line and script[pb - 1] in "，、？！：,?!:":
                units[-1] = (pa, b)
                merged = True
                continue
        units.append((a, b))
        merged = False
    caps = []
    for a, b in units:
        idx = [k for k in range(a, b) if _shown(script, k)]
        chunks = _chunks(idx, max_line * max_lines)
        for j, chunk in enumerate(chunks):
            end = b if j == len(chunks) - 1 else chunks[j + 1][0]
            caps.append(Caption(chunk[0], end, _wrap(chunk, max_line)))
    return caps


def _manual(script: str, lo: int, hi: int, specs: list[str]) -> list[Caption]:
    """按分镜里手写的断句切标题：每项一句，「|」处换行；字必须与文案逐字对应（标点可省略）。"""
    caps, p = [], lo
    for spec in specs:
        lines: list[list[int]] = [[]]
        first = None
        for ch in spec:
            if ch == "|":
                lines.append([])
                continue
            while p < hi and script[p] != ch and not _visible(script, p):
                p += 1
            if p >= hi or script[p] != ch:
                got = script[p] if p < hi else "（镜头结尾）"
                raise ValueError(f"标题断句「{spec}」与文案不一致：期望「{ch}」，文案此处是「{got}」")
            if _shown(script, p):
                lines[-1].append(p)
            first = p if first is None else first
            p += 1
        caps.append(Caption(first if first is not None else p, p, [ln for ln in lines if ln]))
    rest = [k for k in range(p, hi) if _visible(script, k)]
    if rest:
        raise ValueError(f"标题断句漏了文案：「{''.join(script[k] for k in rest)}」")
    # 每句的结束位置延到下一句开始，句末标点归属本句
    for a, b in zip(caps, caps[1:]):
        a.end = b.start
    if caps:
        caps[-1].end = hi
    return caps


def split_captions(script: str, spans: list[tuple[int, int]], manual: list[list[str] | None] | None = None,
                   max_line: int = 12, max_lines: int = 2, merge_short: int = 4) -> list[Caption]:
    """按镜头区间切出标题句。spans 为每个镜头在全稿中的 [start, end)，标题不跨镜头。
    manual[i] 给定时按手写断句切，否则自动按逗号分句切。"""
    manual = manual or [None] * len(spans)
    caps = []
    for (lo, hi), spec in zip(spans, manual):
        caps += _manual(script, lo, hi, spec) if spec else _auto(script, lo, hi, max_line, max_lines, merge_short)
    return caps


def _chunks(idx: list[int], size: int) -> list[list[int]]:
    """超长句均分成若干段，每段不超过 size 个字。"""
    if len(idx) <= size:
        return [idx]
    k = -(-len(idx) // size)
    step = -(-len(idx) // k)
    return [idx[i:i + step] for i in range(0, len(idx), step)]


def _wrap(idx: list[int], max_line: int) -> list[list[int]]:
    if len(idx) <= max_line:
        return [idx]
    cut = -(-len(idx) // 2)  # 两行尽量等长，上行不短于下行
    return [idx[:cut], idx[cut:]]


def red_mask(script: str, spans: list[tuple[int, int, list[str]]]) -> set[int]:
    """每个镜头的关键词在该镜头文案里的所有出现位置（全稿下标）。"""
    out = set()
    for lo, hi, words in spans:
        seg = script[lo:hi]
        for w in words:
            start = seg.find(w)
            while start >= 0:
                out.update(range(lo + start, lo + start + len(w)))
                start = seg.find(w, start + 1)
    return out
