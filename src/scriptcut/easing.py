"""缓动函数。输入 t∈[0,1]，输出进度值（可超出 0~1 以产生过冲）。"""

import math


def linear(t: float) -> float:
    return t


def in_cubic(t: float) -> float:
    return t ** 3


def out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def in_out_cubic(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def in_quart(t: float) -> float:
    return t ** 4


def out_quart(t: float) -> float:
    return 1 - (1 - t) ** 4


def out_quint(t: float) -> float:
    return 1 - (1 - t) ** 5


def in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * t) - 1) / 2


def out_expo(t: float) -> float:
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def in_expo(t: float) -> float:
    return 0.0 if t <= 0 else 2 ** (10 * t - 10)


def out_back(t: float, overshoot: float = 1.70158) -> float:
    c3 = overshoot + 1
    return 1 + c3 * (t - 1) ** 3 + overshoot * (t - 1) ** 2


def out_elastic(t: float, period: float = 0.3) -> float:
    if t <= 0 or t >= 1:
        return float(t >= 1)
    return 2 ** (-10 * t) * math.sin((t - period / 4) * (2 * math.pi) / period) + 1


def spring(t: float, freq: float = 3.0, damping: float = 6.0) -> float:
    """阻尼弹簧：从 1 衰减振荡到 0，用于落地挤压、摆动等二次运动。t 为秒。"""
    return math.exp(-damping * t) * math.cos(2 * math.pi * freq * t)


EASINGS = {
    "linear": linear,
    "in_cubic": in_cubic,
    "out_cubic": out_cubic,
    "in_out_cubic": in_out_cubic,
    "in_quart": in_quart,
    "out_quart": out_quart,
    "out_quint": out_quint,
    "in_out_sine": in_out_sine,
    "out_expo": out_expo,
    "in_expo": in_expo,
    "out_back": out_back,
    "out_elastic": out_elastic,
}
