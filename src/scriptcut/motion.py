"""MG 入场动画预设（与渲染后端无关）：按缓动曲线逐帧采样出位移 / 缩放 / 旋转 / 透明度 / 模糊。

Canvas 后端把采样帧写进 timeline.json，由浏览器逐帧插值；剪映后端把同样的采样帧写成关键帧
（剪映关键帧之间是线性插值，逐帧采样后曲线形状完全由这里决定，能还原 AE 式的极速冲出、过冲回弹、挤压拉伸、二次摆动）。

坐标约定：x 右正、y 上正，单位是「半个画布」宽 / 高（与 pyJianYingDraft 一致）；旋转为顺时针角度。
来源：知识科普原型的 motion 模块。
"""

import math
from dataclasses import dataclass, field
from typing import Callable

from . import easing as ez

FPS = 30
US = 1_000_000


@dataclass
class Placement:
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    # 元素显示高度（半画布高单位），摆动预设用来计算顶部支点
    height: float = 0.4
    # 画布高 / 宽（竖屏 16/9，横屏 9/16），把半画布高单位换算成半画布宽单位
    aspect: float = 16 / 9


@dataclass
class Frame:
    dx: float = 0.0
    dy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    alpha: float = 1.0
    blur: float = 0.0  # 高斯模糊半径（像素，1080 宽画布），只在本地逐帧渲染时生效


@dataclass
class Preset:
    name: str
    duration: float
    fn: Callable[[float, float, Placement], Frame]
    sfx: str
    desc: str = ""
    tags: list[str] = field(default_factory=list)
    sfx_offset: float = 0.0  # 音效相对入场起点的延迟，比如砸落要对准落地瞬间


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _fade(t: float, dur: float = 0.08) -> float:
    return _clamp01(t / dur)


def pop(p: float, t: float, pl: Placement) -> Frame:
    s = max(0.0, ez.out_back(p, 1.6))
    # 过冲阶段横向略胖、纵向略瘦，回落时反过来，形成轻微果冻感
    wobble = ez.spring(t, freq=4.0, damping=10.0) * 0.06 * (1 - p)
    return Frame(sx=s * (1 + wobble), sy=s * (1 - wobble), alpha=_fade(t))


def elastic(p: float, t: float, pl: Placement) -> Frame:
    s = max(0.0, ez.out_elastic(p, 0.35))
    return Frame(sx=s, sy=s, alpha=_fade(t))


def slam(p: float, t: float, pl: Placement) -> Frame:
    fall = 0.22  # 下落占比：前 22% 时间极速砸下，其余做落地挤压回弹
    if p < fall:
        q = ez.in_cubic(p / fall)
        stretch = 0.25 * q
        return Frame(dy=0.45 * (1 - q), sx=1 - stretch * 0.5, sy=1 + stretch, alpha=_fade(t, 0.04))
    total = t / p  # 预设总时长（p>=fall>0）
    land_t = (p - fall) * total
    squash = ez.spring(land_t, freq=3.2, damping=7.0) * 0.3
    return Frame(sx=1 + squash, sy=1 - squash)


def whip(p: float, t: float, pl: Placement, side: int = -1) -> Frame:
    # 指数减速给极速感，混入少量 back 曲线让它冲过目标点再弹回
    q = ez.out_back(p, 1.2) * 0.35 + ez.out_expo(p) * 0.65
    dx = side * 1.4 * (1 - q)
    speed = abs(ez.out_expo(min(1, p + 0.02)) - ez.out_expo(p)) * 25
    stretch = min(0.35, speed)
    lean = -side * 18 * ez.spring(t, freq=2.2, damping=5.5) * (1 - _clamp01(p * 1.2)) - side * 14 * (1 - p) ** 3
    return Frame(dx=dx, sx=1 + stretch, sy=1 - stretch * 0.4, rot=lean, alpha=_fade(t, 0.05))


def whip_right(p: float, t: float, pl: Placement) -> Frame:
    return whip(p, t, pl, side=1)


def swing(p: float, t: float, pl: Placement) -> Frame:
    # 绕顶部中点摆动：中心随旋转画弧，像挂起来的牌子；弹簧按 1.4 秒的节奏压缩进预设时长，末尾乘 (1-p⁴) 保证停稳
    angle = -32 * ez.spring(p * 1.4, freq=1.6, damping=3.2) * (1 - p ** 4)
    r = pl.height * pl.scale / 2
    a = math.radians(angle)
    # 顺时针旋转 a 后，中心相对顶部支点的偏移；r 是半画布高单位，x 要换算成半画布宽（×H/W）
    dx = -r * math.sin(a) * pl.aspect
    dy = -r * (math.cos(a) - 1)
    drop = 0.25 * (1 - ez.out_back(_clamp01(p * 3), 1.4))
    return Frame(dx=dx, dy=dy + drop, rot=angle, alpha=_fade(t))


def spin_pop(p: float, t: float, pl: Placement) -> Frame:
    s = max(0.0, ez.out_back(p, 1.8))
    return Frame(sx=s, sy=s, rot=-200 * (1 - ez.out_expo(p)), alpha=_fade(t))


def rise_bounce(p: float, t: float, pl: Placement) -> Frame:
    q = ez.out_back(p, 2.0)
    squash = ez.spring(t, freq=3.5, damping=8.0) * 0.12 * (1 - p)
    return Frame(dy=-0.35 * (1 - q), sx=1 - squash, sy=1 + squash, alpha=_fade(t))


def stamp(p: float, t: float, pl: Placement) -> Frame:
    """盖章：从 2.6 倍大小极速缩到原尺寸，略带旋转，落定瞬间轻微过压再回弹。"""
    hit = 0.3  # 前 30% 时间压下
    if p < hit:
        q = ez.in_expo(p / hit)
        s = 2.6 - 1.6 * q
        return Frame(sx=s, sy=s, rot=-12 * (1 - q), alpha=_fade(t, 0.03) * 0.85)
    land_t = (p - hit) * (t / p)
    k = ez.spring(land_t, freq=5.0, damping=12.0) * 0.08
    return Frame(sx=1 - k, sy=1 - k, rot=-1.5 * ez.spring(land_t, 3.0, 10.0))


def _slide_progress(p: float) -> float:
    """快速划入的位移进度：五次方缓出为主，混入少量 back 曲线做 6% 左右的过冲回稳。"""
    return ez.out_quint(p) * 0.75 + ez.out_back(p, 1.1) * 0.25


def slide(p: float, t: float, pl: Placement, axis: str = "x", side: int = -1, dist: float = 1.25) -> Frame:
    """沿一个方向快速划入：起点在画面外侧，速度越快沿运动方向拉伸越多，到位时恢复原比例。"""
    q = _slide_progress(p)
    off = side * dist * (1 - q)
    dp = 0.02
    speed = abs(_slide_progress(min(1.0, p + dp)) - q) / dp * dist
    stretch = min(0.22, speed * 0.035)
    alpha = _fade(t, 0.06)
    if axis == "x":
        return Frame(dx=off, sx=1 + stretch, sy=1 - stretch * 0.35, alpha=alpha)
    return Frame(dy=off, sx=1 - stretch * 0.35, sy=1 + stretch, alpha=alpha)


def slide_left(p: float, t: float, pl: Placement) -> Frame:
    return slide(p, t, pl, "x", -1)


def slide_right(p: float, t: float, pl: Placement) -> Frame:
    return slide(p, t, pl, "x", 1)


def slide_up(p: float, t: float, pl: Placement) -> Frame:
    return slide(p, t, pl, "y", -1, 0.8)


def slide_down(p: float, t: float, pl: Placement) -> Frame:
    return slide(p, t, pl, "y", 1, 0.8)


DROP_FALL = 0.38  # 下落占比


def drop(p: float, t: float, pl: Placement) -> Frame:
    """从上方落下：前段加速下落并纵向拉长，落地后挤压、轻弹一下再落定。"""
    if p < DROP_FALL:
        q = ez.in_cubic(p / DROP_FALL) * 0.35 + (p / DROP_FALL) ** 2 * 0.65
        stretch = 0.14 * q
        return Frame(dy=0.6 * (1 - q), sx=1 - stretch * 0.4, sy=1 + stretch, alpha=_fade(t, 0.06))
    land_t = (p - DROP_FALL) * (t / p)
    k = ez.spring(land_t, freq=3.0, damping=8.0)
    bounce = 0.025 * max(0.0, -k)  # 回弹时离地一点点
    return Frame(dy=bounce, sx=1 + 0.12 * max(0.0, k), sy=1 - 0.12 * max(0.0, k))


ZOOM_HIT = 0.4  # 砸下占比


def zoom_slam(p: float, t: float, pl: Placement) -> Frame:
    """从大到小砸下：2.2 倍大加速压到原尺寸，落定瞬间轻微过压再回弹。"""
    if p < ZOOM_HIT:
        q = ez.in_cubic(p / ZOOM_HIT) * 0.5 + (p / ZOOM_HIT) * 0.5
        s = 2.2 - 1.2 * q
        return Frame(sx=s, sy=s, alpha=_clamp01(p / ZOOM_HIT * 2.5))
    land_t = (p - ZOOM_HIT) * (t / p)
    k = ez.spring(land_t, freq=4.0, damping=10.0) * 0.07
    return Frame(sx=1 - k, sy=1 - k)


def fade_up(p: float, t: float, pl: Placement) -> Frame:
    """上浮淡入：从略低处缓缓升到位，适合说明性元素。"""
    q = ez.out_cubic(p)
    return Frame(dy=-0.06 * (1 - q), alpha=_clamp01(p * 1.6))


def blur_in(p: float, t: float, pl: Placement) -> Frame:
    """模糊到清晰：轻微缩小 + 去模糊 + 淡入。"""
    q = ez.out_cubic(p)
    s = 1.06 - 0.06 * q
    return Frame(sx=s, sy=s, alpha=_clamp01(p * 1.8), blur=14 * (1 - q))


def fade(p: float, t: float, pl: Placement) -> Frame:
    """纯淡入：章节卡、整屏素材。"""
    return Frame(alpha=ez.out_cubic(p))


def static(p: float, t: float, pl: Placement) -> Frame:
    """无运动：用于方向生长、错峰组里不需要位移的成员等。"""
    return Frame()


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in [
        Preset("pop", 0.4, pop, "pop", "弹出过冲 + 果冻挤压", ["icon"]),
        Preset("elastic", 0.55, elastic, "boing", "弹性缩放", ["icon", "badge"]),
        Preset("slam", 0.5, slam, "thud", "极速砸落 + 落地挤压", ["title", "heavy"], sfx_offset=0.5 * 0.22),
        Preset("whip_left", 0.4, whip, "whoosh", "从左甩入 + 速度拉伸 + 惯性倾斜", ["panel", "character"]),
        Preset("whip_right", 0.4, whip_right, "whoosh", "从右甩入", ["panel", "character"]),
        Preset("swing", 1.0, swing, "swing", "绕顶部挂点摆动落定", ["sign", "tag"]),
        Preset("spin_pop", 0.45, spin_pop, "swish", "旋转弹出", ["badge", "coin"]),
        Preset("rise_bounce", 0.4, rise_bounce, "pop_soft", "上浮回弹", ["bubble", "character"]),
        Preset("stamp", 0.45, stamp, "stamp", "盖章砸下 + 过压回弹", ["mark", "seal"], sfx_offset=0.45 * 0.3),
        Preset("grow", 0.45, static, "scribble", "沿方向生长（需配合 grow 方向）", ["arrow", "bar", "line"]),
        Preset("slide_left", 0.4, slide_left, "whoosh", "从左快速划入 + 速度拉伸 + 过冲回稳", ["character", "panel"]),
        Preset("slide_right", 0.4, slide_right, "whoosh", "从右快速划入", ["character", "panel"]),
        Preset("slide_up", 0.4, slide_up, "whoosh", "从下往上划入", ["panel"]),
        Preset("slide_down", 0.4, slide_down, "whoosh", "从上往下划入", ["panel"]),
        Preset("drop", 0.5, drop, "thud", "从上方落下 + 落地挤压轻弹", ["icon", "heavy"], sfx_offset=0.5 * DROP_FALL),
        Preset("zoom_slam", 0.45, zoom_slam, "thud", "从大到小砸下 + 落地回弹", ["title", "heavy"], sfx_offset=0.45 * ZOOM_HIT),
        Preset("fade_up", 0.35, fade_up, "pop_soft", "上浮淡入", ["icon", "text"]),
        Preset("blur_in", 0.35, blur_in, "pop_soft", "模糊到清晰", ["icon", "text"]),
        Preset("fade", 0.3, fade, "", "淡入（章节卡、整屏素材）", ["card", "media"]),
        Preset("cut", 0.04, static, "", "直接出现，无动画", ["media"]),
    ]
}


def sample(preset: Preset, pl: Placement, fps: int = FPS) -> list[Frame]:
    """逐帧采样（含首尾，共 n+1 帧）；末帧强制精确落定。"""
    n = max(2, round(preset.duration * fps))
    frames = [preset.fn(i / n, i / fps, pl) for i in range(n + 1)]
    frames[-1] = Frame()
    return frames


def frames_px(frames: list[Frame], W: int, H: int) -> list[list[float]]:
    """采样帧 → 像素坐标（y 向下）：[[dx, dy, sx, sy, rot, alpha, blur], ...]，给 Canvas 引擎用。"""
    return [[round(f.dx * W / 2, 2), round(-f.dy * H / 2, 2), round(f.sx, 4), round(f.sy, 4), round(f.rot, 3),
             round(max(0.0, min(1.0, f.alpha)), 4), round(f.blur, 2)] for f in frames]
