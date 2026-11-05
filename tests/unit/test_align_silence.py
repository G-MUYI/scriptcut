"""配音对齐：读音音节数、断句点、停顿吸附、字幕切句、花字触发词定时（合成静音区间，不依赖音频）"""
import pytest

from scriptcut.align.silence import (MEDIUM, STRONG, WEAK, align_storyboard, align_text, char_weights, find_breaks,
                                        latin_syllables, number_syllables, quote_depth, read_int, script_from_shots,
                                        clean_subtitle, split_sentences, to_srt, wrap_subtitle)


@pytest.mark.parametrize("n, reading", [
    (0, "零"), (7, "七"), (10, "十"), (17, "十七"), (60, "六十"), (105, "一百零五"), (143, "一百四十三"),
    (1010, "一千零一十"), (2397, "二千三百九十七"), (100000, "十万"), (550000, "五十五万"), (100000000, "一亿"),
    (10050000, "一千零五万"), (729_0000_0000, "七百二十九亿"),
])
def test_read_int(n, reading):
    assert read_int(n) == reading


@pytest.mark.parametrize("token, nxt, n", [
    ("2026", "年", 4),       # 二零二六
    ("10", "年", 1),         # 十年
    ("2397", "人", 7),
    ("5.4%", "", 6),         # 百分之五点四
    ("119.3%", "", 10),      # 百分之一百一十九点三
    ("4.931%", "", 8),       # 百分之四点九三一
    ("60%", "", 5),
])
def test_number_syllables(token, nxt, n):
    assert number_syllables(token, nxt) == n


@pytest.mark.parametrize("word, n", [("LV", 2), ("GDP", 3), ("CELINE", 2), ("Macron", 2)])
def test_latin_syllables(word, n):
    assert latin_syllables(word) == n


def test_char_weights_spread_tokens():
    w = char_weights("全法55万人，LV")
    assert w[:2] == [1.0, 1.0]
    assert w[2] == w[3] == 1.5  # 五十五 = 3 音节摊到两个字符
    assert w[6] == 0.0          # 逗号
    assert sum(w) == 2 + 3 + 2 + 2


def test_breaks_strength_and_quotes():
    text = "街头。 “法国怎么了”。原因、结果，完"
    bs = find_breaks(text, char_weights(text))
    assert [(text[b.pos:b.pos + 2], b.strength) for b in bs] == [("“法", STRONG), ("原因", STRONG),
                                                                ("结果", WEAK), ("完", MEDIUM)]


def test_quote_depth_tolerates_same_direction_quotes():
    d = quote_depth("a“bc“d《e》f")
    assert d[2] == 1 and d[5] == 0 and d[7] == 1 and d[9] == 0


# 三句话、语速 5 音节/秒；第二句的逗号处没有停顿
TEXT = "第一句话有六个字。第二句，稍微长一点点。第三句话结束了。"
SILENCES = [(0.0, 0.5), (2.1, 2.6), (4.4, 5.0), (6.4, 7.0)]
DURATION = 7.0


def test_align_snaps_pauses_to_sentence_ends():
    a = align_text(TEXT, SILENCES, DURATION)
    assert a.mean_rate == pytest.approx(5.0)
    assert [TEXT[x.pos] for x in a.anchors] == ["第", "第"]
    assert a.char_start[0] == pytest.approx(0.5)
    assert a.char_start[TEXT.index("第二")] == pytest.approx(2.6)
    assert a.char_start[TEXT.index("稍")] == pytest.approx(3.2)   # 2.6 + 3 音节 / 5
    assert a.char_start[TEXT.index("第三")] == pytest.approx(5.0)
    assert a.char_end[TEXT.index("了")] == pytest.approx(6.4)
    assert a.skipped_gaps == [] and a.skipped_breaks == []


def test_align_skips_breath_pause_inside_clause():
    # 第一句中间多了一个很短的换气停顿，不应吸附到别的标点上
    sil = [(0.0, 0.5), (1.1, 1.36), (2.36, 2.86), (4.66, 5.26), (6.66, 7.2)]
    a = align_text(TEXT, sil, 7.2)
    assert a.char_start[TEXT.index("第二")] == pytest.approx(2.86)
    assert a.char_start[TEXT.index("第三")] == pytest.approx(5.26)
    assert a.skipped_gaps == [pytest.approx((1.1, 1.36))]


def test_lines_and_srt():
    a = align_text(TEXT, SILENCES, DURATION)
    lines = a.lines()
    assert [x.text for x in lines] == ["第一句话有六个字", "第二句", "稍微长一点点", "第三句话结束了"]
    assert (lines[0].start, lines[0].end) == pytest.approx((0.5, 2.1))
    assert (lines[2].start, lines[2].end) == pytest.approx((3.2, 4.4))
    srt = to_srt(lines[:1])
    assert srt == "1\n00:00:00,500 --> 00:00:02,100\n第一句话有六个字\n"


def test_split_sentences_long_line_breaks_outside_quotes():
    text = "这是退休金少一块、地方学校少一笔、医疗再退一步。锁进“没有本币央行、焊死汇率”的体系里。"
    assert split_sentences(text, max_chars=10) == [
        "这是退休金少一块", "地方学校少一笔", "医疗再退一步", "锁进没有本币央行\n焊死汇率的体系里"]


@pytest.mark.parametrize("raw, text", [
    ("在和平时期的街头。 “", "在和平时期的街头"),
    ("“法国这是怎么了“", "法国这是怎么了"),
    ("巴黎的LV、爱马仕、CELINE被洗劫，", "巴黎的LV 爱马仕 CELINE被洗劫"),
    ("收益率已经冲到4.931%，", "收益率已经冲到4.931%"),
    ("欧元是1992年《马斯特里赫特条约》的产物。", "欧元是1992年《马斯特里赫特条约》的产物"),
])
def test_clean_subtitle(raw, text):
    assert clean_subtitle(raw) == text


@pytest.mark.parametrize("text, wrapped", [
    ("多城宵禁", "多城宵禁"),
    ("工业竞争力在德国的顺差和东欧的廉价劳动力之间被两头挤压", "工业竞争力在德国的顺差和东欧的\n廉价劳动力之间被两头挤压"),
    ("法国10年期国债收益率已经冲到4.931%", "法国10年期国债收益率已经冲到4.931%"),  # 半角按半字宽，未超长
    ("把双方的战争能力锁进一个谁也拿不走钥匙的超国家框架里", "把双方的战争能力锁进\n一个谁也拿不走钥匙的超国家框架里"),
    ("欧元就是用来套住那个统一后变得更强大的德国伙伴的", "欧元就是用来套住那个统一后\n变得更强大的德国伙伴的"),
    ("欧元是1992年《马斯特里赫特条约》的产物", "欧元是1992年\n《马斯特里赫特条约》的产物"),
])
def test_wrap_subtitle(text, wrapped):
    assert wrap_subtitle(text, 18) == wrapped


def test_storyboard_shots_and_beats():
    shots = [{"shot_id": "P01", "narration": ""},
             {"shot_id": "P02", "narration": "第一句话有六个字。"},
             {"shot_id": "P03", "narration": "第二句，稍微长一点点。第三句话结束了。"}]
    text, spans = script_from_shots(shots)
    assert text == "第一句话有六个字。\n第二句，稍微长一点点。第三句话结束了。"
    assert list(spans) == ["P02", "P03"]
    # 换行拼接后与 TEXT 的读音一致，停顿位置照用
    a = align_text(text, SILENCES, DURATION)
    sb = {"beats": [{"scene_id": "P03", "beat_no": 1, "trigger": "一点点"},
                    {"scene_id": "P03", "beat_no": 2, "trigger": "结束"},
                    {"scene_id": "P02", "beat_no": 1, "trigger": "不存在"}]}
    with pytest.warns(UserWarning, match="不存在"):
        r = align_storyboard(sb, a, spans)
    assert r["shots"] == [{"shot_id": "P02", "start": 0.5, "end": 2.1}, {"shot_id": "P03", "start": 2.6, "end": 6.4}]
    assert r["beats"][0]["start"] == pytest.approx(3.8)   # 「稍微长」之后
    assert r["beats"][1]["start"] == pytest.approx(5.8)
    assert r["beats"][2]["start"] is None


def test_find_ignores_punctuation():
    a = align_text(TEXT, SILENCES, DURATION)
    assert a.find("第二句稍微") == TEXT.index("第二句")
    assert a.find("没有") is None
