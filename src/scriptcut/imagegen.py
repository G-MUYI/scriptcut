"""AI 出图：由用户接入自己的出图 API（本项目不内置、不代付任何出图服务）。

在 .env 里配置（也可以运行 `scriptcut image-api` 查看当前状态和填写示例）：
  IMAGE_PROVIDER   ark（火山方舟 · 豆包 Seedream）| siliconflow（硅基流动）| dashscope（阿里云百炼 · 通义万相）
                   | openai（OpenAI 或任意 OpenAI 兼容接口 / 中转）| command（任意命令行工具）| manual（人工出图）
  IMAGE_API_KEY    你的 Key
  IMAGE_MODEL      模型 ID（各平台控制台里复制；不填用下面的默认值）
  IMAGE_API_BASE   接口地址（不填用平台默认）
  IMAGE_SIZE       尺寸，例如 2048x2048（不填用平台默认）
  IMAGE_EXTRA_JSON 额外请求参数（JSON），例如 {"watermark": false}
  SCRIPTCUT_IMAGE_CMD  command 模式的命令模板，变量 {prompt} {prompt_file} {out} {ref} {size} {id}

没配置时 `gen` 不会偷偷用占位图跑完，而是返回 need_image_api，提醒用户先接入（或明确选择人工出图 / 先用占位图）。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from . import config
from .util import ScriptCutError

PRESETS = {
    "ark": {"label": "火山方舟 · 豆包 Seedream（字节）", "base": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-seedream-4-0-250828", "size": "2048x2048", "extra": {"watermark": False, "response_format": "url"},
            "ref_field": "image", "console": "https://console.volcengine.com/ark （开通 Seedream 模型，创建 API Key）"},
    "siliconflow": {"label": "硅基流动 SiliconFlow", "base": "https://api.siliconflow.cn/v1", "model": "Kwai-Kolors/Kolors",
                    "size": "1024x1024", "extra": {"batch_size": 1}, "ref_field": "image",
                    "console": "https://cloud.siliconflow.cn/account/ak"},
    "dashscope": {"label": "阿里云百炼 · 通义万相", "base": "https://dashscope.aliyuncs.com/api/v1", "model": "wan2.2-t2i-flash",
                  "size": "1024*1024", "extra": {}, "ref_field": "",
                  "console": "https://bailian.console.aliyun.com/ （开通通义万相，创建 API Key）"},
    "openai": {"label": "OpenAI / 任意 OpenAI 兼容接口（含各类中转）", "base": "https://api.openai.com/v1", "model": "gpt-image-1",
               "size": "1024x1024", "extra": {}, "ref_field": "", "console": "你的服务商控制台"},
}

GUIDE = """出图这一步需要接入你自己的出图 API（ScriptCut 不内置、不代付出图服务）。

在项目根目录或仓库根目录的 .env 里填写其中一种，然后重新运行 gen（或 `scriptcut image-api --test` 先测一张）：

① 火山方舟 · 豆包 Seedream
   IMAGE_PROVIDER=ark
   IMAGE_API_KEY=你的方舟 API Key
   IMAGE_MODEL=doubao-seedream-4-0-250828      # 以控制台里的模型 ID / 接入点为准
② 阿里云百炼 · 通义万相
   IMAGE_PROVIDER=dashscope
   IMAGE_API_KEY=sk-你的百炼 Key
   IMAGE_MODEL=wan2.2-t2i-flash
③ 硅基流动
   IMAGE_PROVIDER=siliconflow
   IMAGE_API_KEY=sk-你的 Key
   IMAGE_MODEL=Kwai-Kolors/Kolors
④ OpenAI 或任意 OpenAI 兼容接口 / 中转
   IMAGE_PROVIDER=openai
   IMAGE_API_BASE=https://你的接口地址/v1
   IMAGE_API_KEY=你的 Key
   IMAGE_MODEL=模型名
⑤ 已有出图脚本 / 命令行工具（如 LibTV CLI、ComfyUI 脚本）
   IMAGE_PROVIDER=command
   SCRIPTCUT_IMAGE_CMD=python my_gen.py --prompt-file {prompt_file} --out {out}

不想接 API 也可以：按 build/prompts.md 的提示词自己出图（即梦、豆包、MJ 等都行），存成 assets/<编号>.png；
或者先用占位图继续（gen --allow-placeholders / run --allow-placeholders），剪映草稿里再「替换素材」。"""


def settings(p=None) -> dict:
    """当前出图配置（不含 Key 明文）。"""
    cfg = (p.cfg.get("image_gen", {}) if p else {}) or {}
    name = config.env("IMAGE_PROVIDER") or cfg.get("provider", "auto")
    if name in ("", "auto"):
        if config.env("SCRIPTCUT_IMAGE_CMD"):
            name = "command"
        elif config.env("IMAGE_API_KEY") and config.env("IMAGE_API_BASE"):
            base = config.env("IMAGE_API_BASE")
            name = next((k for k, v in PRESETS.items() if v["base"].split("/")[2] in base), "openai")
        elif config.env("IMAGE_API_KEY"):
            name = "ark"  # 只填了 Key 没指定平台：默认按豆包 Seedream 处理
        else:
            name = "none"
    pre = PRESETS.get(name, {})
    st = {"provider": name, "label": pre.get("label", {"command": "自定义命令行", "manual": "人工出图", "none": "未配置"}.get(name, name)),
          "base": config.env("IMAGE_API_BASE") or pre.get("base", ""), "model": config.env("IMAGE_MODEL") or pre.get("model", ""),
          "size": config.env("IMAGE_SIZE") or pre.get("size", ""), "has_key": bool(config.env("IMAGE_API_KEY")),
          "command": config.env("SCRIPTCUT_IMAGE_CMD")}
    if name in PRESETS:
        st["configured"] = st["has_key"]
    elif name == "command":
        st["configured"] = bool(st["command"])
    else:
        st["configured"] = False
    st["manual"] = name == "manual"
    return st


def _extra(pre: dict) -> dict:
    extra = dict(pre.get("extra", {}))
    raw = config.env("IMAGE_EXTRA_JSON")
    if raw:
        try:
            extra.update(json.loads(raw))
        except json.JSONDecodeError as e:
            raise ScriptCutError(f"IMAGE_EXTRA_JSON 不是合法 JSON：{e}") from e
    return extra


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _download(url_or_b64: str, out: Path) -> None:
    if url_or_b64.startswith("http"):
        r = requests.get(url_or_b64, timeout=180)
        r.raise_for_status()
        out.write_bytes(r.content)
    else:
        out.write_bytes(base64.b64decode(url_or_b64.split(",", 1)[-1]))


class OpenAICompat:
    """OpenAI 兼容 /images/generations：方舟 Seedream、硅基流动、OpenAI、各类中转都是这个格式。"""

    def __init__(self, name: str, st: dict):
        self.name = name
        self.pre = PRESETS.get(name, PRESETS["openai"])
        self.base = st["base"].rstrip("/")
        self.key = config.env("IMAGE_API_KEY")
        self.model, self.size = st["model"], st["size"]
        if not (self.base and self.key):
            raise ScriptCutError("出图 API 没配置完整（需要 IMAGE_API_KEY，openai 兼容接口还需要 IMAGE_API_BASE）\n\n" + GUIDE)

    def generate(self, eid: str, prompt: str, ref: Path | None, out: Path) -> None:
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        body = {"model": self.model, "prompt": prompt, "size": self.size, **_extra(self.pre)}
        if self.name == "openai":
            body.setdefault("n", 1)
        if ref is not None:
            if self.pre.get("ref_field"):
                body[self.pre["ref_field"]] = _data_uri(ref)
            elif self.name == "openai":  # OpenAI：带参考图走 /images/edits
                with open(ref, "rb") as f:
                    r = requests.post(f"{self.base}/images/edits", headers={"Authorization": f"Bearer {self.key}"}, timeout=600,
                                      data={"model": self.model, "prompt": prompt, "size": self.size},
                                      files={"image": (ref.name, f, "image/png")})
                if r.status_code == 200:
                    return self._save(r.json(), out)
        r = requests.post(f"{self.base}/images/generations", headers=h, json=body, timeout=600)
        if r.status_code != 200:
            raise ScriptCutError(f"出图失败 {eid}（{self.name} HTTP {r.status_code}）：{r.text[:300]}")
        self._save(r.json(), out)

    @staticmethod
    def _save(data: dict, out: Path) -> None:
        items = data.get("data") or data.get("images") or []
        if not items:
            raise ScriptCutError(f"出图接口没有返回图片：{json.dumps(data, ensure_ascii=False)[:300]}")
        d = items[0]
        _download(d.get("b64_json") or d.get("url") or d.get("b64"), out)


class DashScope:
    """阿里云百炼 · 通义万相：异步任务（提交 → 轮询 → 下载）。"""

    name = "dashscope"

    def __init__(self, st: dict):
        self.base = st["base"].rstrip("/")
        self.key = config.env("IMAGE_API_KEY")
        self.model, self.size = st["model"], st["size"].replace("x", "*")
        if not self.key:
            raise ScriptCutError("通义万相需要 IMAGE_API_KEY\n\n" + GUIDE)

    def generate(self, eid: str, prompt: str, ref: Path | None, out: Path) -> None:
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "X-DashScope-Async": "enable"}
        body = {"model": self.model, "input": {"prompt": prompt}, "parameters": {"size": self.size, "n": 1, **_extra(PRESETS["dashscope"])}}
        r = requests.post(f"{self.base}/services/aigc/text2image/image-synthesis", headers=h, json=body, timeout=60)
        if r.status_code != 200:
            raise ScriptCutError(f"通义万相提交失败 {eid}（HTTP {r.status_code}）：{r.text[:300]}")
        task = r.json()["output"]["task_id"]
        for _ in range(180):
            time.sleep(2)
            q = requests.get(f"{self.base}/tasks/{task}", headers={"Authorization": f"Bearer {self.key}"}, timeout=30).json()
            status = q.get("output", {}).get("task_status")
            if status == "SUCCEEDED":
                _download(q["output"]["results"][0]["url"], out)
                return
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise ScriptCutError(f"通义万相出图失败 {eid}：{json.dumps(q, ensure_ascii=False)[:300]}")
        raise ScriptCutError(f"通义万相出图超时 {eid}")


class Command:
    name = "command"

    def __init__(self, st: dict):
        self.tpl = st["command"]
        self.size = st["size"] or "1024x1024"
        if not self.tpl:
            raise ScriptCutError("command 模式需要 .env 的 SCRIPTCUT_IMAGE_CMD\n\n" + GUIDE)

    def generate(self, eid: str, prompt: str, ref: Path | None, out: Path) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(prompt)
            pf = f.name
        q = shlex.quote
        cmd = self.tpl.format(prompt=q(prompt), prompt_file=q(pf), out=q(str(out)), ref=q(str(ref or "")), size=self.size, id=eid)
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1200)
        if proc.returncode != 0 or not out.exists():
            raise ScriptCutError(f"出图命令失败 {eid}：{(proc.stderr or proc.stdout)[-300:]}")


def provider(p=None, override: str | None = None):
    """→ provider 实例；没配置返回 None（manual / none）。"""
    st = settings(p)
    if override:
        st = dict(st, provider=override)
        pre = PRESETS.get(override, {})
        st["base"] = config.env("IMAGE_API_BASE") or pre.get("base", "")
        st["model"] = config.env("IMAGE_MODEL") or pre.get("model", "")
        st["size"] = config.env("IMAGE_SIZE") or pre.get("size", "")
    name = st["provider"]
    if name in PRESETS:
        if name == "dashscope":
            return DashScope(st)
        return OpenAICompat(name, st)
    if name == "command":
        return Command(st)
    return None


def test(p=None, out: Path | None = None) -> dict:
    """用当前配置出一张测试图，确认 API 可用。"""
    prov = provider(p)
    if prov is None:
        return {"ok": False, "error_code": "need_image_api", "message": GUIDE, **settings(p)}
    out = out or Path.cwd() / "image_api_test.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    prov.generate("TEST", "一个简笔画风格的笑脸太阳，白色背景，只画这一个主体", None, out)
    return {"ok": True, "file": str(out), "seconds": round(time.time() - t0, 1), **{k: v for k, v in settings(p).items() if k != "command"}}
