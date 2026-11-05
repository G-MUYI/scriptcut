"""端到端（离线）：估算时间 → 时间轴 → 混音 → 静帧 / 出片 → 剪映草稿。缺可选依赖的步骤自动跳过。"""
import json

import pytest

from scriptcut import pipeline
from scriptcut.cli import main as cli

from conftest import chromium_ok, has, needs_ffmpeg

CHROMIUM = chromium_ok()


@needs_ffmpeg
def test_timeline_and_audio(proj):
    pipeline.voice(proj)  # voice.source: none → 估算
    rep = pipeline.timeline(proj)
    tl = json.loads((proj / "build" / "timeline.json").read_text(encoding="utf-8"))
    assert rep["shots"] == 4 and tl["timing_source"] == "estimate"
    shots = {s["id"]: s for s in tl["shots"]}
    # 章节卡占满整屏、垫在最底层
    assert shots["S00"]["items"][0]["chapter"] and shots["S00"]["items"][0]["w"] == 1080
    # 元素在 trigger 开口时入场，且落在镜头时间窗内
    for s in tl["shots"]:
        for it in s["items"]:
            assert s["start"] <= it["appear"] < s["end"]
    s1 = shots["S01"]
    w01 = next(i for i in s1["items"] if i["el"] == "W01")
    k = s1["text"].index("天空")
    assert w01["appear"] == pytest.approx(s1["chars"][k][0], abs=1e-3)
    assert shots["S02"]["shakes"]
    assert any(c["name"] == "ding" for c in tl["cues"]) and any(c["name"] == "boom" for c in tl["cues"])
    assert tl["captions"] and all(c["start"] < c["end"] for c in tl["captions"])
    assert (proj / "build" / "subs.srt").read_text(encoding="utf-8").startswith("1\n")
    res = pipeline.audio(proj)
    assert (proj / "build" / "audio" / "master.wav").exists()
    assert res["lufs"]["master"] == pytest.approx(-14, abs=1.0)


def test_cli_json(proj, capsys):
    assert cli(["check", str(proj), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["result"]["shots"] == 4


@needs_ffmpeg
@pytest.mark.render
@pytest.mark.skipif(not CHROMIUM, reason="需要 Playwright + Chromium")
def test_stills_and_short_render(proj):
    pipeline.voice(proj)
    pipeline.timeline(proj)
    pipeline.audio(proj)
    st = pipeline.stills(proj)
    assert st["files"] and (proj / st["files"][0]).exists()
    r = pipeline.render(proj, workers=1, t0=0, t1=1.0)
    assert (proj / r["video"]).exists() and r["frames"] == 30


@needs_ffmpeg
@pytest.mark.jianying
@pytest.mark.skipif(not has("pyJianYingDraft"), reason="需要 pyJianYingDraft")
def test_jianying_editable(proj, tmp_path):
    """可编辑草稿：图片是原始素材 + 关键帧，文字 / 标题 / 章节都是原生文本，音效逐条分轨；不再有预渲染的透明视频。"""
    pipeline.voice(proj)
    pipeline.timeline(proj)
    pipeline.audio(proj)
    drafts = tmp_path / "drafts"
    res = pipeline.jianying(proj, "editable", "t_draft", str(drafts))
    assert res["mode"] == "editable" and res["preview"] and (proj / res["preview"]).exists()
    content = json.loads((drafts / "t_draft" / "draft_content.json").read_text(encoding="utf-8"))
    tracks = {t["name"]: t for t in content["tracks"]}
    assert {"背景", "章节底图", "章节序号", "章节标题", "图层1", "文字1", "标题", "音效1"} <= set(tracks)   # 测试项目无配音（估算时间）
    assert tracks["文字1"]["type"] == "text" and tracks["标题"]["type"] == "text"
    vids = content["materials"]["videos"]
    assert not any(v["path"].endswith(".mov") for v in vids)          # 没有烘焙的入场视频
    assert all(v["type"] == "photo" for v in vids)                    # 背景 / 章节底图 / 元素图都是图片素材
    # 元素图带关键帧（入场 + 待机），文字元素的关键词是分段颜色
    seg = tracks["图层1"]["segments"][0]
    assert {k["property_type"] for k in seg["common_keyframes"]} & {"KFTypePositionX", "KFTypePositionY", "KFTypeScaleX"}
    texts = {t["id"]: json.loads(t["content"]) for t in content["materials"]["texts"]}
    w02 = next(c for c in texts.values() if c["text"] == "散射")
    assert w02["styles"][0]["fill"]["content"]["solid"]["color"] != [0.0, 0.0, 0.0]
    cap = next(c for c in texts.values() if c["text"].startswith("今天聊聊"))
    assert len(cap["styles"]) >= 2                                    # 「天空」标红
    anims = {a["id"]: a for a in content["materials"]["material_animations"]}
    cap_seg = tracks["标题"]["segments"][0]
    assert any(anims[r]["animations"][0]["name"] == "逐字显影" for r in cap_seg["extra_material_refs"] if r in anims)
    assert len(tracks["音效1"]["segments"]) >= 3
    # 草稿预览：按剪映规则合成指定时刻
    pv = pipeline.jianying(proj, name="t_draft", draft_dir=str(drafts), preview_at=[2.0])
    assert (proj / pv["files"][0]).exists()
    # 再生成一次可以覆盖（本工具生成、未被剪映打开过）
    pipeline.jianying(proj, "editable", "t_draft", str(drafts))
    # 剪映打开过（有 Timelines/）就拒绝覆盖
    (drafts / "t_draft" / "Timelines").mkdir()
    with pytest.raises(Exception, match="打开过"):
        pipeline.jianying(proj, "editable", "t_draft", str(drafts))


def test_draft_keyframe_simplify():
    from scriptcut.jianying.builder import simplify
    pts = [(i / 30, i / 30 * 2) for i in range(31)]                   # 一条直线：只留首尾
    assert simplify(pts, 0.001) == [pts[0], pts[-1]]
    curve = [(i / 30, (i / 30) ** 2) for i in range(31)]
    out = simplify(curve, 0.002)
    assert 2 < len(out) < 31 and out[0] == curve[0] and out[-1] == curve[-1]


def test_install_agent(tmp_path):
    from scriptcut.agents import install
    r = install("workbuddy", tmp_path)
    assert (tmp_path / ".workbuddy" / "skills" / "scriptcut-video" / "SKILL.md").exists()
    cfg = json.loads((tmp_path / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["scriptcut"]["args"] == ["-m", "scriptcut.mcp_server"]
    # 合并而不是覆盖已有配置
    (tmp_path / ".workbuddy" / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    install("workbuddy", tmp_path)
    cfg = json.loads((tmp_path / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))
    assert set(cfg["mcpServers"]) == {"other", "scriptcut"}
    d = install("doubao", tmp_path)
    import zipfile
    zf = next(f for f in d["installed"] if f.endswith("scriptcut-video.zip"))
    assert "scriptcut-video/SKILL.md" in zipfile.ZipFile(zf).namelist()
    assert r["next"]


@pytest.mark.skipif(not has("mcp"), reason="需要 mcp")
def test_mcp_tools_and_sandbox(proj, tmp_path):
    import asyncio

    from scriptcut import mcp_server
    mcp_server.ROOT = proj.parent
    try:
        srv = mcp_server.build_server()

        async def go():
            names = {t.name for t in await srv.list_tools()}
            assert {"scriptcut_write_file", "scriptcut_put_asset", "scriptcut_view_image", "scriptcut_image_api",
                    "scriptcut_jianying", "scriptcut_gen"} <= names
            r = await srv.call_tool("scriptcut_check", {"project": proj.name})
            assert '"ok": true' in str(r)
            r = await srv.call_tool("scriptcut_check", {"project": "/etc"})
            assert "工作目录" in str(r)
            r = await srv.call_tool("scriptcut_write_file", {"project": proj.name, "path": "../x.txt", "content": "x"})
            assert "不允许" in str(r)
        asyncio.run(go())
    finally:
        mcp_server.ROOT = None
