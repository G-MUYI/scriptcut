import importlib.util
import shutil
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sky-blue"

SCRIPT = "你好，今天聊聊天空。天空为什么是蓝色？答案是散射。"

STORYBOARD = """
title: 测试
script: input/script.txt
elements:
  C01: {role: character, accent: 明黄, accent_part: 卫衣, name: 女生, prompt: 一个挥手的女生}
  W01: {kind: word, text: 天空, accent: 天蓝}
  W02: {kind: word, text: 散射, hl: 散射, accent: 大红}
shots:
  - id: S00
    duration: 1.0
    chapter: {n: "01", title: 开场}
  - id: S01
    text: 你好，今天聊聊天空。
    layout: person_word
    red: [天空]
    items:
      - {el: C01, trigger: 你好}
      - {el: W01, trigger: 天空}
  - id: S02
    text: 天空为什么是蓝色？
    layout: hero
    items:
      - {el: W01, trigger: 天空, shake: true}
  - id: S03
    text: 答案是散射。
    layout: hero
    sfx: [{name: ding, at: 散射}]
    items:
      - {el: W02, trigger: 答案}
"""


def make_project(tmp: Path, storyboard: str = STORYBOARD, script: str = SCRIPT, project_yaml: str = "") -> Path:
    d = tmp / "proj"
    (d / "input").mkdir(parents=True, exist_ok=True)
    (d / "input" / "script.txt").write_text(script, encoding="utf-8")
    (d / "storyboard.yaml").write_text(textwrap.dedent(storyboard), encoding="utf-8")
    (d / "project.yaml").write_text(project_yaml or "style: whiteboard\nvoice: {source: none}\n", encoding="utf-8")
    return d


@pytest.fixture
def proj(tmp_path):
    return make_project(tmp_path)


def has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def chromium_ok() -> bool:
    if not has("playwright"):
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
