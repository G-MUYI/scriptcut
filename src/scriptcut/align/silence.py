"""离线配音对齐：把文案逐字对齐到配音时间轴（不依赖语音识别，只用 ffmpeg 静音检测）

来源：「小Lin说式新闻解说」原型的 align 模块，原样保留算法与测试。

做法：
1. ffmpeg silencedetect 切出语音段，段间停顿是候选断句点；
2. 文案按「读音音节数」计重量（汉字 1；数字按中文读法，2397 → 两千三百九十七 = 7；英文按字母/元音组估算），
   标点处是候选断句点；
3. 动态规划把停顿顺序吸附到标点上：相邻两个吸附点之间的语速越接近全片平均语速越好，
   跳过有停顿的标点、跳过不在标点处的停顿都要付代价；
4. 吸附点之间按音节在「去掉停顿的语音时间」上线性插值，得到每个字的开口 / 收尾时间。

时间单位为秒，均相对配音文件开头；放进时间轴的偏移由剪辑计划处理。
"""
import bisect
import math
import re
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# 断句标点强度：句末 > 句中 > 顿号
STRONG = 3
MEDIUM = 2
WEAK = 1
PUNCT_STRENGTH = {**{c: STRONG for c in "。！？!?\n"}, **{c: MEDIUM for c in "，；：,;:…—"}, "、": WEAK}
SKIP_PUNCT_COST = {STRONG: 1.5, MEDIUM: 0.4, WEAK: 0.1}  # 有标点却没停顿
ASSIGN_COST = {STRONG: 0.0, MEDIUM: 0.1, WEAK: 0.3}     # 停顿落在弱标点上略不自然
RATE_COST = 4.0  # 吸附点之间语速偏离平均语速：RATE_COST * ln(语速比)² * min(音节数, 20) / 10
SUBTITLE_DROP = "“”‘’\"'「」『』"                    # 字幕里直接删掉的引号
SUBTITLE_SPACE = "，。！？；：、,.!?;:…—"            # 字幕句中间的标点换成空格，句首句尾的删掉
# 字幕换行没有分词，靠以下规则挑断点：断在空格处 > 「的了着」之后 / 介词连词或常见词之前 > 其他位置
BREAK_AFTER = "的了着"
BREAK_BEFORE = "在把被从让和与向给对将跟比而但却并"
BREAK_BEFORE_WORDS = ("一个", "这个", "那个", "这样", "那样", "因为", "所以", "但是", "而是", "就是", "还是", "已经",
                      "最后", "最终", "同时", "然后", "如果", "虽然", "为什么", "一同", "持续", "重新", "变得", "产生")
NO_BREAK_WORDS = ("现在", "正在", "存在", "实在", "所在", "面对", "相对", "绝对", "针对", "反对", "而且", "然而", "因而",
                  "从而", "进而", "不但", "目的", "的确", "为了", "对于", "和平", "于是")
CLOSE_QUOTES = "”’》）)」』"
OPEN_BRACKETS = "《（(「『"
CLOSE_BRACKETS = "》）)」』"

_DIGITS = "零一二三四五六七八九"
_TOKEN = re.compile(r"(\d+(?:\.\d+)?%?)|([A-Za-z]+)|(.)", re.S)


# ---------------------------------------------------------------- 读音音节数

def _read_group(g: int) -> str:
    """1..9999 的中文读法"""
    s, zero = "", False
    for unit_val, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        d = g // unit_val % 10
        if d == 0:
            zero = bool(s)
            continue
        if zero:
            s += "零"
            zero = False
        s += _DIGITS[d] + unit
    return s


def read_int(n: int) -> str:
    """整数的中文读法（只用来数音节）：2397 → 二千三百九十七，10 → 十，105 → 一百零五"""
    if n == 0:
        return "零"
    groups = []
    while n:
        groups.append(n % 10000)
        n //= 10000
    out, zero = "", False
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            zero = True
            continue
        if out and (g < 1000 or zero):
            out += "零"
        out += _read_group(g) + ("", "万", "亿", "万亿")[gi]
        zero = False
    return out[1:] if out.startswith("一十") else out


def number_syllables(token: str, next_char: str = "") -> int:
    """数字串的读音音节数：4 位整数后接「年」按逐位读（2026年）；小数点读「点」、小数逐位读；% 读「百分之」"""
    pct = token.endswith("%")
    num = token.rstrip("%")
    whole, _, frac = num.partition(".")
    if not frac and not pct and len(whole) == 4 and next_char == "年":
        n = 4
    else:
        n = len(read_int(int(whole))) + (1 + len(frac) if frac else 0)
    return n + (3 if pct else 0)


def latin_syllables(word: str) -> int:
    """英文的读音音节数估算：短缩写逐字母（LV、GDP），其余按元音组（CELINE → 2）"""
    if len(word) <= 3 or (word.isupper() and len(word) <= 4):
        return len(word)
    groups = re.findall(r"[aeiouy]+", word.lower())
    n = len(groups) - (1 if word.lower().endswith("e") and len(groups) > 1 else 0)
    return max(1, n)


def char_weights(text: str) -> List[float]:
    """每个字符的读音重量：汉字 1，数字/英文按整串音节数平摊到各字符，其余 0"""
    w = [0.0] * len(text)
    for m in _TOKEN.finditer(text):
        num, latin, ch = m.groups()
        i, j = m.span()
        if num:
            nxt = text[j] if j < len(text) else ""
            each = number_syllables(num, nxt) / (j - i)
        elif latin:
            each = latin_syllables(latin) / (j - i)
        else:
            each = 1.0 if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" else 0.0
        for k in range(i, j):
            w[k] = each
    return w


# ---------------------------------------------------------------- 断句点

@dataclass
class Break:
    pos: int        # 文本下标：断句点之后第一个字符
    weight: float   # 断句点之前的累计音节
    strength: int


def find_breaks(text: str, weights: Sequence[float]) -> List[Break]:
    """候选断句点：连续的零重量字符（标点、引号、空白）合并成一个，强度取其中最强的标点。
    断在最后一个标点之后、再跳过闭引号和空白：闭引号留在上一句，开引号归下一句"""
    out, cum, i, n = [], 0.0, 0, len(text)
    while i < n:
        if weights[i] > 0:
            cum += weights[i]
            i += 1
            continue
        j, strength, last = i, 0, -1
        while j < n and weights[j] == 0:
            if text[j] in PUNCT_STRENGTH:
                strength = max(strength, PUNCT_STRENGTH[text[j]])
                last = j
            j += 1
        if strength and 0 < cum:
            pos = last + 1
            while pos < j and (text[pos] in CLOSE_QUOTES or (text[pos].isspace() and text[pos] != "\n")):
                pos += 1
            out.append(Break(pos, cum, strength))
        i = j
    return out


def quote_depth(text: str) -> List[int]:
    """out[k] = text[:k] 里未闭合的引号 / 书名号层数。双引号在已打开时一律视作闭合（兼容写成同向的 “…“）"""
    depth, out, in_dq = 0, [], False
    for ch in text:
        out.append(depth)
        if ch in "“”\"":
            if ch == "”" or in_dq:
                depth, in_dq = max(0, depth - 1), False
            else:
                depth, in_dq = depth + 1, True
        elif ch in OPEN_BRACKETS:
            depth += 1
        elif ch in CLOSE_BRACKETS:
            depth = max(0, depth - 1)
    out.append(depth)
    return out


# ---------------------------------------------------------------- 语音时间轴

class SpeechTimeline:
    """去掉停顿后的「语音时间」与音频时间互转"""

    def __init__(self, silences: Sequence[Tuple[float, float]], duration: float):
        segs, t = [], 0.0
        for a, b in sorted(silences):
            a, b = max(0.0, a), min(duration, b)
            if a > t:
                segs.append((t, a))
            t = max(t, b)
        if t < duration:
            segs.append((t, duration))
        if not segs:
            raise ValueError("配音里没有检测到语音")
        self.segments = segs
        self.starts, acc = [], 0.0  # 每个语音段开头对应的语音时间
        for a, b in segs:
            self.starts.append(acc)
            acc += b - a
        self.total = acc
        # 段间停顿（不含开头、结尾静音）：(音频开始, 音频结束, 停顿处的语音时间)
        self.gaps = [(segs[k][1], segs[k + 1][0], self.starts[k + 1]) for k in range(len(segs) - 1)]

    def to_audio(self, s: float, after_gap: bool) -> float:
        """语音时间 → 音频时间；s 恰好落在停顿上时，after_gap=True 取停顿结束、否则取停顿开始"""
        s = min(max(s, 0.0), self.total)
        k = (bisect.bisect_right if after_gap else bisect.bisect_left)(self.starts, s) - 1
        k = min(max(k, 0), len(self.segments) - 1)
        return self.segments[k][0] + (s - self.starts[k])


# ---------------------------------------------------------------- 对齐

@dataclass
class AlignedLine:
    text: str
    start: float  # 秒
    end: float


@dataclass
class Anchor:
    gap: int        # 停顿序号（SpeechTimeline.gaps 下标）
    pos: int        # 吸附到的文本下标
    rate: float     # 与上一个吸附点之间的语速（音节/秒）


@dataclass
class Alignment:
    text: str
    char_start: List[float]
    char_end: List[float]
    duration: float
    anchors: List[Anchor] = field(default_factory=list)
    skipped_gaps: List[Tuple[float, float]] = field(default_factory=list)   # 没落在标点上的停顿
    skipped_breaks: List[int] = field(default_factory=list)                 # 句末标点却没停顿（文本下标）
    mean_rate: float = 0.0

    def span(self, i: int, j: int) -> Optional[Tuple[float, float]]:
        """text[i:j] 的 (开口, 收尾) 时间；区间里没有发音字符返回 None"""
        w = char_weights(self.text[i:j])
        ks = [i + k for k, x in enumerate(w) if x > 0]
        if not ks:
            return None
        return self.char_start[ks[0]], self.char_end[ks[-1]]

    def find(self, word: str, start: int = 0, end: Optional[int] = None) -> Optional[int]:
        """在 text[start:end] 里找 word，忽略标点和空白差异；返回 text 下标"""
        end = len(self.text) if end is None else end
        k = self.text.find(word, start, end)
        if k >= 0:
            return k
        key = _normalize(word)[0]
        if not key:
            return None
        norm, idx = _normalize(self.text[start:end])
        k = norm.find(key)
        return start + idx[k] if k >= 0 else None

    def lines(self, max_chars: int = 18) -> List[AlignedLine]:
        """切成字幕句：句末 / 句中标点处断开；超过 max_chars 个字的句子先在顿号处断开，仍超长的在句内换行（\n）。
        字幕文字去掉标点和引号，见 clean_subtitle"""
        breaks = find_breaks(self.text, char_weights(self.text))
        depth = quote_depth(self.text)
        cuts = {b.pos for b in breaks if b.strength >= MEDIUM}
        weak = {b.pos for b in breaks if b.strength == WEAK and depth[b.pos] == 0}  # 引号里的顿号不断
        bounds = sorted(cuts | {0, len(self.text)})
        out = []
        for a, b in zip(bounds, bounds[1:]):
            for i, j in _split_long(self.text, a, b, weak, max_chars):
                t = wrap_subtitle(clean_subtitle(self.text[i:j]), max_chars)
                sp = self.span(i, j)
                if t and sp:
                    out.append(AlignedLine(t, round(sp[0], 3), round(sp[1], 3)))
        return out

    def report(self, max_chars: int = 18) -> dict:
        return {
            "duration": round(self.duration, 3),
            "mean_rate": round(self.mean_rate, 2),
            "anchors": len(self.anchors),
            "skipped_gaps": [(round(a, 2), round(b, 2)) for a, b in self.skipped_gaps],
            "skipped_sentence_ends": [_context(self.text, p) for p in self.skipped_breaks],
            "rate_outliers": [{"at": _context(self.text, a.pos), "rate": round(a.rate, 2)} for a in self.anchors
                              if self.mean_rate and abs(math.log(a.rate / self.mean_rate)) > math.log(1.6)],
            "wrapped_lines": [x.text.replace("\n", " / ") for x in self.lines(max_chars) if "\n" in x.text],
        }


def _normalize(s: str) -> Tuple[str, List[int]]:
    """去掉零重量字符（标点/空白），返回 (新串, 新串每个字符在原串的下标)"""
    w = char_weights(s)
    idx = [k for k, x in enumerate(w) if x > 0]
    return "".join(s[k] for k in idx), idx


def _context(text: str, pos: int, width: int = 8) -> str:
    return f"{text[max(0, pos - width):pos]}|{text[pos:pos + width]}".replace("\n", "⏎")


def display_len(s: str) -> float:
    """字幕显示宽度：半角字符（英文、数字、空格、%）按半个字算"""
    return sum(0.5 if ord(ch) < 0x2000 else 1.0 for ch in s if ch != "\n")


def clean_subtitle(raw: str) -> str:
    """字幕文字：删引号；句中标点换成一个空格，句首句尾的删掉；书名号保留。数字里的小数点、% 不动"""
    out = []
    for ch, x in zip(raw, char_weights(raw)):
        if x > 0:
            out.append(ch)
        elif ch in SUBTITLE_DROP:
            continue
        elif ch in SUBTITLE_SPACE or ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip()


def _break_bonus(text: str, k: int) -> float:
    """在 text[k] 之前断行的好坏：越小越好"""
    a, b = text[k - 1], text[k]
    if " " in (a, b):
        return -3.0
    if text[k - 1:k + 1] in NO_BREAK_WORDS:
        return 6.0
    if any(text.startswith(w, k) for w in BREAK_BEFORE_WORDS):
        return -2.0
    if a in BREAK_AFTER:
        return -2.0
    if b in BREAK_BEFORE:
        return -1.5
    return 6.0  # 没有依据的位置，大概率断在词中间


def wrap_subtitle(text: str, max_chars: int = 18) -> str:
    """超过 max_chars 宽的字幕换行：每行尽量不超过 max_chars，断点按 _break_bonus 挑，其次看两行是否均衡；
    不断开英文 / 数字串和书名号。断开后仍超长的部分继续断"""
    total = display_len(text)
    if total <= max_chars:
        return text
    depth = quote_depth(text)
    best, best_score = None, None
    for k in range(1, len(text)):
        a, b = text[k - 1], text[k]
        if depth[k] or (a.isascii() and b.isascii() and " " not in (a, b)):
            continue
        left = display_len(text[:k].rstrip())
        right = total - left
        if left < 3 or right < 3:
            continue
        score = 0.5 * abs(left - total / 2) + _break_bonus(text, k) + (10.0 if max(left, right) > max_chars else 0.0)
        if best_score is None or score < best_score:
            best, best_score = k, score
    if best is None:
        return text
    head, tail = text[:best].rstrip(), text[best:].lstrip()
    return wrap_subtitle(head, max_chars) + "\n" + wrap_subtitle(tail, max_chars)


def _split_long(text: str, a: int, b: int, weak: set, max_chars: int) -> List[Tuple[int, int]]:
    n = display_len(_normalize(text[a:b])[0])
    inner = sorted(p for p in weak if a < p < b)
    if n <= max_chars or not inner:
        return [(a, b)]
    mid = min(inner, key=lambda p: abs(display_len(_normalize(text[a:p])[0]) - n / 2))
    return _split_long(text, a, mid, weak, max_chars) + _split_long(text, mid, b, weak, max_chars)


def _gap_skip_cost(gap: Tuple[float, float, float]) -> float:
    return 0.8 + 3.0 * (gap[1] - gap[0])


def align_text(text: str, silences: Sequence[Tuple[float, float]], duration: float,
               max_gap_skip: int = 4, max_break_skip: int = 12, window: float = 60.0) -> Alignment:
    """纯计算部分：文案 + 静音区间 → 逐字时间。window 是吸附点偏离平均语速位置的最大音节数"""
    weights = char_weights(text)
    total_w = sum(weights)
    if total_w <= 0:
        raise ValueError("文案里没有可发音的字")
    tl = SpeechTimeline(silences, duration)
    r0 = total_w / tl.total
    breaks = find_breaks(text, weights)
    breaks = [b for b in breaks if b.weight < total_w]
    gaps = tl.gaps
    break_w = [b.weight for b in breaks]
    G, B = len(gaps), len(breaks)

    # 节点：(停顿 j, 断句点 c)；起点 (-1, -1)、终点 (G, B)
    def node_w(c):
        return 0.0 if c < 0 else total_w if c >= B else breaks[c].weight

    def node_s(j):
        return 0.0 if j < 0 else tl.total if j >= G else gaps[j][2]

    skip_c = [SKIP_PUNCT_COST[b.strength] for b in breaks]
    skip_c_cum = [0.0]
    for x in skip_c:
        skip_c_cum.append(skip_c_cum[-1] + x)
    skip_g_cum = [0.0]
    for g in gaps:
        skip_g_cum.append(skip_g_cum[-1] + _gap_skip_cost(g))

    best: Dict[Tuple[int, int], Tuple[float, Optional[Tuple[int, int]]]] = {(-1, -1): (0.0, None)}
    by_gap: Dict[int, List[int]] = {-1: [-1]}
    for j in range(G + 1):
        s = node_s(j)
        if j == G:
            cands = [B]
        else:
            lo = bisect.bisect_left(break_w, s * r0 - window)
            hi = bisect.bisect_right(break_w, s * r0 + window)
            cands = list(range(lo, hi))
        by_gap[j] = []
        for c in cands:
            wc = node_w(c)
            top = None
            for jp in range(max(-1, j - max_gap_skip - 1), j):
                for cp in by_gap.get(jp, []):
                    if cp >= c or c - cp > max_break_skip + 1:
                        continue
                    dw, ds = wc - node_w(cp), s - node_s(jp)
                    if dw <= 0 or ds <= 0:
                        continue
                    cost = (best[(jp, cp)][0]
                            + RATE_COST * math.log(dw / ds / r0) ** 2 * min(dw, 20.0) / 10.0
                            + (skip_c_cum[c] - skip_c_cum[cp + 1] if cp + 1 < c else 0.0)
                            + skip_g_cum[j] - skip_g_cum[jp + 1]
                            + (ASSIGN_COST[breaks[c].strength] if c < B else 0.0))
                    if top is None or cost < top[0]:
                        top = (cost, (jp, cp))
            if top is not None:
                best[(j, c)] = top
                by_gap[j].append(c)

    if (G, B) not in best:
        if max_gap_skip < G or max_break_skip < B:
            return align_text(text, silences, duration, max_gap_skip * 2 + 1, max_break_skip * 2 + 1, window * 2)
        raise RuntimeError("对齐失败：停顿和标点无法匹配")

    path, node = [], (G, B)
    while node is not None:
        path.append(node)
        node = best[node][1]
    path.reverse()

    # 吸附点：(音节, 语音时间)，分段线性插值
    knots_w = [node_w(c) for _, c in path]
    knots_s = [node_s(j) for j, _ in path]
    anchors, used_gaps, used_breaks = [], set(), set()
    for (jp, cp), (j, c) in zip(path, path[1:]):
        if 0 <= j < G:
            rate = (node_w(c) - node_w(cp)) / (node_s(j) - node_s(jp))
            anchors.append(Anchor(j, breaks[c].pos, rate))
            used_gaps.add(j)
            used_breaks.add(c)

    def w_to_s(w):
        k = min(max(bisect.bisect_right(knots_w, w) - 1, 0), len(knots_w) - 2)
        w0, w1, s0, s1 = knots_w[k], knots_w[k + 1], knots_s[k], knots_s[k + 1]
        return s0 + (w - w0) / (w1 - w0) * (s1 - s0)

    starts, ends, cum = [], [], 0.0
    for x in weights:
        starts.append(tl.to_audio(w_to_s(cum), after_gap=True))
        cum += x
        ends.append(tl.to_audio(w_to_s(cum), after_gap=False))

    return Alignment(
        text=text, char_start=starts, char_end=ends, duration=duration, anchors=anchors,
        skipped_gaps=[(gaps[j][0], gaps[j][1]) for j in range(G) if j not in used_gaps],
        skipped_breaks=[b.pos for c, b in enumerate(breaks) if c not in used_breaks and b.strength == STRONG],
        mean_rate=r0,
    )


def align_audio(text: str, audio_path: str, noise_db: float = -40.0, min_silence: float = 0.2) -> Alignment:
    """文案逐字对齐到配音文件"""
    from ..util import detect_silences, probe_duration
    return align_text(text, detect_silences(audio_path, noise_db, min_silence), probe_duration(audio_path))


def split_sentences(script_text: str, max_chars: int = 18) -> List[str]:
    """按标点把文案切成字幕句（与 Alignment.lines 的切法一致）"""
    a = Alignment(script_text, [0.0] * len(script_text), [0.0] * len(script_text), 0.0)
    w = char_weights(script_text)
    if not any(w):
        return []
    return [line.text for line in a.lines(max_chars)]


def align_script_to_audio(script_text: str, audio_path: str, noise_db: float = -40.0,
                          min_silence: float = 0.2) -> List[AlignedLine]:
    """文案逐句对齐到配音，返回每句起止时间"""
    return align_audio(script_text, audio_path, noise_db, min_silence).lines()


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(lines: Sequence[AlignedLine]) -> str:
    """字幕句 → SRT 文本（配合配音在播放器里核对用）"""
    return "\n".join(f"{k}\n{_srt_time(x.start)} --> {_srt_time(x.end)}\n{x.text}\n" for k, x in enumerate(lines, 1))


# ---------------------------------------------------------------- 分镜表

def script_from_shots(shots: List[dict]) -> Tuple[str, Dict[str, Tuple[int, int]]]:
    """各镜头旁白按顺序用换行拼成全文，返回 (全文, 镜头号 → 旁白在全文里的 [i, j))"""
    parts, spans, pos = [], {}, 0
    for s in shots:
        nar = str(s.get("narration") or "").strip()
        if not nar:
            continue
        if parts:
            parts.append("\n")
            pos += 1
        spans[str(s["shot_id"])] = (pos, pos + len(nar))
        parts.append(nar)
        pos += len(nar)
    return "".join(parts), spans


def align_storyboard(storyboard: dict, alignment: Alignment, spans: Dict[str, Tuple[int, int]]) -> dict:
    """镜头旁白时间 + 花字触发词开口时间。触发词先在本镜头旁白里按顺序找，找不到再往后全文找"""
    shots = []
    for sid, (i, j) in spans.items():
        sp = alignment.span(i, j)
        if sp:
            shots.append({"shot_id": sid, "start": round(sp[0], 3), "end": round(sp[1], 3)})

    beats, cursor = [], {}
    for b in storyboard.get("beats", []):
        sid, trig = str(b.get("scene_id", "")), str(b.get("trigger") or "").strip()
        item = {"scene_id": sid, "beat_no": b.get("beat_no"), "trigger": trig, "start": None, "end": None}
        if trig and sid in spans:
            i, j = spans[sid]
            k = alignment.find(trig, cursor.get(sid, i), j)
            if k is None:
                k = alignment.find(trig, i)
            if k is not None:
                sp = alignment.span(k, k + len(trig))
                if sp:
                    item["start"], item["end"] = round(sp[0], 3), round(sp[1], 3)
                    cursor[sid] = k + len(trig)
        if item["start"] is None:
            warnings.warn(f"花字 {sid}-{b.get('beat_no')} 触发词「{trig}」在旁白里找不到")
        beats.append(item)
    return {"shots": shots, "beats": beats}
