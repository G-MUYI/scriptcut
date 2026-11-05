"""文本 / 分镜校验 / 估算时间 / 动效预设 / 音效 / 识别匹配 —— 不依赖外部服务。"""
import numpy as np
import pytest

from scriptcut import project as proj_mod
from scriptcut import timing as tmod
from scriptcut.align.asr import asr_chars, match
from scriptcut.audio import sfx
from scriptcut.motion import PRESETS, Placement, frames_px, sample
from scriptcut.text import cn_number, sentence_spans, spoken

from conftest import EXAMPLE, make_project


# ---------------------------------------------------------------- 文本

@pytest.mark.parametrize("raw, said", [
    ("增长5.4%", "增长百分之五点四"),
    ("2026年", "二零二六年"),
    ("2个人", "两个人"),
    ("2397元", "两千三百九十七元"),
    ("1,263块", "一千二百六十三块"),
    ("等了30毫秒", "等了三十毫秒"),
    ("“散射”——就是", "散射，就是"),
])
def test_spoken(raw, said):
    assert spoken(raw) == said


def test_cn_number_liang():
    assert cn_number("2000") == "两千" and cn_number("22") == "二十二" and cn_number("200000000") == "两亿"


def test_sentence_spans_cover_script():
    s = "你好。今天聊聊天空，为什么是蓝色的？答案是“散射”。尾巴"
    spans = sentence_spans(s)
    assert "".join(s[a:b] for a, b in spans) == s
    assert s[spans[2][0]:spans[2][1]] == "答案是“散射”。"


# ---------------------------------------------------------------- 分镜

def test_example_project_valid():
    p = proj_mod.load(EXAMPLE)
    errs, _ = proj_mod.validate(p)
    assert errs == []
    assert p.W == 1080 and p.H == 1920
    assert "紫" in p.style["palette"]  # style_overrides 生效


def test_validate_catches_mismatch_and_bad_trigger(tmp_path):
    sb = """
    elements:
      W01: {kind: word, text: 天空, accent: 不存在的色}
    shots:
      - id: S01
        text: 你好，今天聊聊天。
        items:
          - {el: W01, trigger: 月亮}
          - {el: X99}
    """
    p = proj_mod.load(make_project(tmp_path, sb, "你好，今天聊聊天空。"))
    errs, _ = proj_mod.validate(p)
    joined = "\n".join(errs)
    assert "与原稿不一致" in joined
    assert "trigger「月亮」" in joined
    assert "素材库里没有 X99" in joined
    assert "未知点缀色" in joined


def test_yaml_on_key_reported(tmp_path):
    sb = """
    elements:
      P01: {role: hero, prompt: 火锅, accent: 大红, accent_part: 汤}
      P02: {role: hero, prompt: 红叉, accent: 大红, accent_part: 叉}
    shots:
      - id: S01
        text: 火锅。
        items:
          - {el: P01, trigger: 火锅}
          - {el: P02, on: P01, trigger: 火锅}
    """
    p = proj_mod.load(make_project(tmp_path, sb, "火锅。"))
    errs, _ = proj_mod.validate(p)
    assert any("over" in e for e in errs)


def test_canvas_override_scales_layouts(tmp_path):
    d = make_project(tmp_path, project_yaml="style: whiteboard\ncanvas: {width: 720, height: 1280}\n")
    p = proj_mod.load(d)
    assert p.W == 720 and p.style["layouts"]["hero"][0][:2] == [360, pytest.approx(1060 * 1280 / 1920)]


# ---------------------------------------------------------------- 时间

def test_estimate_inserts_silent_slot_and_chapter_gap(proj):
    p = proj_mod.load(proj)
    tm = tmod.estimate(p)
    g = tmod.gaps(p)
    assert tm.slots["S00"] == [pytest.approx(g["lead"]), pytest.approx(g["lead"] + 1.0)]
    assert tm.start[0] >= tm.slots["S00"][1]
    assert all(a <= b for a, b in zip(tm.start, tm.start[1:]))
    assert len(tm.start) == len(p.script) == len(tm.end)


# ---------------------------------------------------------------- 动效 / 音效

@pytest.mark.parametrize("name", sorted(PRESETS))
def test_presets_land_exactly(name):
    fr = frames_px(sample(PRESETS[name], Placement()), 1080, 1920)
    assert fr[-1] == [0, 0, 1, 1, 0, 1, 0]
    assert all(len(f) == 7 for f in fr)


def test_sfx_library_finite_and_deterministic():
    for name in sfx.SFX:
        y = sfx.get(name)
        assert np.isfinite(y).all() and np.abs(y).max() > 0.1
    a = sfx.SFX["whoosh"]()
    b = sfx.SFX["whoosh"]()
    assert np.array_equal(a, b)


# ---------------------------------------------------------------- 识别匹配（来自知识科普原型的测试）

def _segs(*items):
    return [{"text": t, "start": a, "end": b} for t, a, b in items]


def test_asr_exact_match():
    script = "你好，世界。"
    st, en, ratio = match(script, _segs(("你好", 0.0, 0.4), ("世界", 0.6, 1.0)), 1.2)
    assert ratio == 1.0
    assert st[0] == 0.0 and st[1] == pytest.approx(0.2)
    assert st[2] == st[3] == pytest.approx(0.6)   # 逗号取下一个字
    assert en[1] == pytest.approx(0.4)


def test_asr_digits_written_differently():
    st, _, ratio = match("存10万块吧", _segs(("存十万块吧", 0.0, 1.0)), 1.0)
    assert 0.2 <= st[1] < st[2] < st[3] <= 0.6 and ratio < 1.0


def test_asr_missing_words_interpolated():
    st, _, ratio = match("一二三四五六", _segs(("一二", 0.0, 0.4), ("五六", 1.0, 1.4)), 1.5)
    assert all(a <= b for a, b in zip(st, st[1:])) and 0.2 < st[2] < 1.0
    assert ratio == pytest.approx(4 / 6)


def test_asr_chars_skip_punctuation():
    assert [c for c, _, _ in asr_chars(_segs(("你好，", 0.0, 0.4)))] == ["你", "好"]
