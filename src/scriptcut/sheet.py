"""分镜审阅表 xlsx：给人看 / 给客户确认用（分镜 · 素材与提示词 · 数据核实 · 说明）。只导出，不读回——唯一权威来源是 storyboard.yaml。"""

from __future__ import annotations

from . import assets as assets_mod
from . import timing as tmod
from .project import Project
from .util import ScriptCutError, read_json, rel

HEAD_FILL = "D9D9D9"
SEG_FILLS = ["F7F7F7", "E7E7E7"]


def export(p: Project) -> dict:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as e:
        raise ScriptCutError("导出 xlsx 需要 openpyxl：pip install openpyxl") from e
    tl = read_json(p.build / "timeline.json")
    times = {s["id"]: (s["start"], s["end"]) for s in tl["shots"]} if tl else {}
    if not times:
        tm = tmod.load_or_estimate(p)
        for k, s in enumerate(p.shots):
            a = tm.slots.get(s.id, [None])[0] if s.silent else tm.start[s.offset]
            times[s.id] = (a or 0.0, None)
    wb = Workbook()
    wrap = Alignment(wrap_text=True, vertical="top")

    def sheet(ws, headers, widths, rows):
        ws.append(headers)
        for c, w in zip(ws[1], widths):
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor=HEAD_FILL)
            ws.column_dimensions[c.column_letter].width = w
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = wrap
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "分镜"
    rows, seg_idx, last_seg = [], 0, None
    for s in p.shots:
        a, b = times.get(s.id, (0, None))
        items = "；".join(f"{it.el}@{it.trigger or '镜头开始'}（{it.preset}）" for it in s.items)
        rows.append([s.id, s.seg, f"{a:.2f}" + (f"–{b:.2f}" if b else ""), s.text or f"（无口播 {s.duration}s）", s.layout,
                     items, "、".join(s.red), s.scene, (s.chapter or {}).get("title", "") if s.chapter is not None else "", s.note])
    sheet(ws, ["镜头", "段落", "时间(秒)", "文案", "版式", "元素@触发词（入场）", "标红", "自定义画面", "章节", "备注"],
          [7, 10, 13, 40, 12, 46, 16, 12, 16, 20], rows)
    for i, s in enumerate(p.shots, start=2):
        if s.seg != last_seg:
            seg_idx += 1
            last_seg = s.seg
        for c in ws[i]:
            c.fill = PatternFill("solid", fgColor=SEG_FILLS[seg_idx % 2])

    usage: dict[str, list[str]] = {}
    for s in p.shots:
        for it in s.items:
            usage.setdefault(it.el, []).append(s.id)
    rows = []
    for eid, e in p.elements.items():
        src = e.src or (rel(assets_mod._ai_file(p, eid), p.dir) if assets_mod._ai_file(p, eid) else "")
        state = "用户素材" if e.src else ("本地文字" if e.kind == "word" else ("已生成" if src else "待生成"))
        rows.append([eid, e.kind, e.role, e.name, e.accent, e.text, state, src, "、".join(usage.get(eid, [])),
                     assets_mod.build_prompt(p, e) if e.needs_ai else ""])
    sheet(wb.create_sheet("素材与提示词"), ["编号", "类型", "角色", "名称", "点缀色", "文字", "状态", "文件", "出现镜头", "AI 提示词"],
          [7, 8, 10, 16, 8, 14, 9, 24, 16, 80], rows)
    sheet(wb.create_sheet("数据核实"), ["说法", "文案数值", "核实结果", "状态", "来源", "网址"], [36, 14, 14, 10, 24, 40],
          [[f.get("claim", ""), f.get("value", ""), f.get("verified", ""), f.get("status", "未核实"), f.get("source", ""),
            f.get("url", "")] for f in p.facts])
    meta = [["标题", p.title], ["风格", p.style.get("name")], ["画布", f"{p.W}×{p.H} @ {p.fps}fps"], ["字数", len(p.script)],
            ["时间来源", (tl or {}).get("timing_source", "估算")], ["规则", "文案一字不改；改分镜请改 storyboard.yaml，本表只用于审阅"]]
    sheet(wb.create_sheet("说明"), ["项", "值"], [14, 60], meta)
    out = p.build / "分镜表.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"file": rel(out, p.dir), "shots": len(p.shots), "elements": len(p.elements), "facts": len(p.facts)}
