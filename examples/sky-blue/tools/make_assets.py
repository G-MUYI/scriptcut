"""示例素材：用 Pillow 画简笔「条漫」图标，代替 AI 出图，让示例零 Key 也能完整跑通。

正式项目里这些图由 `scriptcut gen` 调用出图服务生成（或按 build/prompts.md 人工出图），保存为 assets/<编号>.png。
用法（在仓库根目录）：python examples/sky-blue/tools/make_assets.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets"
INK = (42, 42, 42, 255)
GREY = (205, 205, 205, 255)
WHITE = (255, 255, 255, 255)
YELLOW = (242, 194, 48, 255)
SKY = (74, 144, 217, 255)
RED = (210, 65, 47, 255)
ORANGE = (232, 150, 60, 255)
PURPLE = (142, 108, 207, 255)
LW = 14


def canvas(w=800, h=800):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / f"{name}.png")


def person(pose: str):
    img, d = canvas(640, 960)
    cx = 320
    # 身体（黄卫衣）+ 裤子
    d.rounded_rectangle((cx - 150, 430, cx + 150, 760), 70, fill=YELLOW, outline=INK, width=LW)
    d.rectangle((cx - 120, 740, cx + 120, 930), fill=(110, 140, 190, 255), outline=INK, width=LW)
    d.line((cx, 760, cx, 930), fill=INK, width=LW)
    # 头
    d.ellipse((cx - 130, 150, cx + 130, 420), fill=WHITE, outline=INK, width=LW)
    d.chord((cx - 135, 140, cx + 135, 330), 180, 360, fill=INK)  # 头发
    eye_y = 300 if pose != "up" else 270
    for ex in (cx - 50, cx + 50):
        d.ellipse((ex - 12, eye_y - 12, ex + 12, eye_y + 12), fill=INK)
    if pose == "think":
        d.arc((cx - 40, 330, cx + 40, 380), 200, 340, fill=INK, width=10)
        d.line((cx + 140, 520, cx + 60, 400), fill=INK, width=LW + 6)  # 手托腮
        d.ellipse((cx + 30, 370, cx + 90, 430), fill=WHITE, outline=INK, width=10)
        d.text((cx + 120, 60), "?", fill=INK, font_size=200)
    elif pose == "up":
        d.ellipse((cx - 25, 340, cx + 25, 380), outline=INK, width=10)
        d.line((cx - 140, 500, cx - 60, 230), fill=INK, width=LW + 6)  # 手搭额头
        d.line((cx - 60, 230, cx + 40, 210), fill=INK, width=LW + 6)
    else:  # shrug
        d.arc((cx - 40, 340, cx + 40, 390), 200, 340, fill=INK, width=10)
        for s in (-1, 1):
            d.line((cx + s * 150, 520, cx + s * 280, 440), fill=INK, width=LW + 6)
            d.line((cx + s * 280, 440, cx + s * 310, 380), fill=INK, width=LW + 6)
        d.text((cx + 150, 40), "?", fill=INK, font_size=180)
    return img


def sun():
    img, d = canvas()
    c = 400
    for k in range(12):
        a = k * math.pi / 6
        d.line((c + math.cos(a) * 230, c + math.sin(a) * 230, c + math.cos(a) * 330, c + math.sin(a) * 330), fill=INK, width=LW)
    d.ellipse((c - 190, c - 190, c + 190, c + 190), fill=YELLOW, outline=INK, width=LW)
    for ex in (c - 60, c + 60):
        d.ellipse((ex - 16, c - 46, ex + 16, c - 14), fill=INK)
    d.arc((c - 80, c - 40, c + 80, c + 90), 20, 160, fill=INK, width=LW)
    return img


def prism():
    img, d = canvas(1000, 700)
    d.line((40, 330, 380, 360), fill=GREY, width=30)
    d.line((40, 330, 380, 360), fill=INK, width=6)
    d.polygon([(500, 120), (330, 560), (670, 560)], fill=(235, 240, 246, 255), outline=INK)
    d.line([(500, 120), (330, 560), (670, 560), (500, 120)], fill=INK, width=LW, joint="curve")
    cols = [(229, 72, 77), (242, 140, 40), (242, 194, 48), (76, 179, 94), (47, 128, 237), (61, 90, 254), (142, 108, 207)]
    for k, c in enumerate(cols):
        y0 = 380 + k * 10
        d.line((560, y0, 980, 200 + k * 62), fill=c + (255,), width=26)
    return img


def waves(short_only=False):
    img, d = canvas(1000, 640 if not short_only else 420)
    def wave(y, lam, amp, col):
        pts = [(x, y + amp * math.sin(2 * math.pi * x / lam)) for x in range(60, 940, 4)]
        d.line(pts, fill=col, width=LW + 4, joint="curve")
    if short_only:
        wave(210, 90, 60, SKY)
        for x, y, dx, dy in ((160, 80, -60, -50), (820, 90, 60, -50), (500, 360, 0, 60)):
            d.line((x, y, x + dx, y + dy), fill=INK, width=10)
    else:
        wave(170, 520, 70, INK)
        wave(470, 110, 60, SKY)
    return img


def bars():
    img, d = canvas(1000, 700)
    cols = [(229, 72, 77), (242, 140, 40), (242, 194, 48), (76, 179, 94), (47, 128, 237), (61, 90, 254), (142, 108, 207)]
    hs = [430, 470, 500, 480, 420, 330, 150]
    for k, (c, h) in enumerate(zip(cols, hs)):
        x = 80 + k * 125
        fill = PURPLE if k == 6 else GREY
        d.rectangle((x, 620 - h, x + 90, 620), fill=fill, outline=INK, width=10)
    d.line((50, 624, 960, 624), fill=INK, width=LW)
    return img


def eye():
    img, d = canvas(800, 500)
    d.chord((60, 60, 740, 440), 0, 360, fill=WHITE, outline=INK, width=LW)
    d.ellipse((300, 150, 500, 350), fill=PURPLE, outline=INK, width=LW)
    d.ellipse((370, 220, 430, 280), fill=INK)
    return img


def cross():
    img, d = canvas()
    d.line((150, 150, 650, 650), fill=RED, width=70)
    d.line((650, 150, 150, 650), fill=RED, width=70)
    return img


def sky():
    img, d = canvas(900, 700)
    d.rounded_rectangle((30, 30, 870, 670), 80, fill=SKY, outline=INK, width=LW)
    for x, y, r in ((330, 360, 110), (450, 300, 140), (580, 370, 100)):
        d.ellipse((x - r, y - r, x + r, y + r), fill=WHITE, outline=INK, width=10)
    d.rectangle((240, 370, 660, 470), fill=WHITE)
    d.line((230, 470, 670, 470), fill=INK, width=10)
    return img


def sunset():
    img, d = canvas(1000, 700)
    for k, c in enumerate([(240, 128, 75), (247, 160, 90), (252, 200, 120)]):
        d.rounded_rectangle((40, 60 + k * 120, 960, 180 + k * 120), 60, fill=c + (255,), outline=INK, width=8)
    d.pieslice((330, 360, 670, 700), 180, 360, fill=ORANGE, outline=INK, width=LW)
    d.line((30, 530, 970, 530), fill=INK, width=LW)
    return img


if __name__ == "__main__":
    save(person("think"), "C01")
    save(person("up"), "C02")
    save(person("shrug"), "C03")
    save(sky(), "P01")
    save(sun(), "P02")
    save(prism(), "P03")
    save(waves(), "P04")
    save(waves(True), "P05")
    save(bars(), "P07")
    save(eye(), "P08")
    save(cross(), "P09")
    save(sunset(), "P10")
    print(f"示例素材 → {OUT}")
