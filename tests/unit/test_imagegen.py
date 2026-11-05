"""出图：没接 API 时必须提醒；OpenAI 兼容（方舟 Seedream / 硅基流动）与通义万相异步接口用本地假服务验证。"""
import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from scriptcut import imagegen, pipeline

from conftest import EXAMPLE

ENV_KEYS = ["IMAGE_PROVIDER", "IMAGE_API_KEY", "IMAGE_API_BASE", "IMAGE_MODEL", "IMAGE_SIZE", "IMAGE_EXTRA_JSON",
            "SCRIPTCUT_IMAGE_CMD"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _png(color=(255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color)
    img.paste((200, 30, 30), (20, 20, 44, 44))
    img.save(buf, "PNG")
    return buf.getvalue()


class Fake(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        Fake.calls.append((self.path, body, self.headers.get("Authorization"), self.headers.get("X-DashScope-Async")))
        if self.path.endswith("/images/generations"):
            if "siliconflow" in body.get("model", ""):
                return self._json({"images": [{"url": f"http://127.0.0.1:{self.server.server_port}/img.png"}]})
            return self._json({"data": [{"b64_json": base64.b64encode(_png()).decode()}]})
        if self.path.endswith("/image-synthesis"):
            return self._json({"output": {"task_id": "t1", "task_status": "PENDING"}})
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path.endswith("/tasks/t1"):
            return self._json({"output": {"task_status": "SUCCEEDED",
                                          "results": [{"url": f"http://127.0.0.1:{self.server.server_port}/img.png"}]}})
        if self.path.endswith("/img.png"):
            data = _png()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def server():
    Fake.calls = []
    s = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def _project_missing(tmp_path, ids=("C01", "P02")):
    import shutil
    d = tmp_path / "p"
    shutil.copytree(EXAMPLE, d, ignore=shutil.ignore_patterns("build", "out"))
    for i in ids:
        (d / "assets" / f"{i}.png").unlink(missing_ok=True)
    return d


def test_need_image_api_reminder(tmp_path):
    d = _project_missing(tmp_path)
    res = pipeline.gen(d)
    assert res["ok"] is False and res["error_code"] == "need_image_api"
    assert "出图 API" in res["error"] and "IMAGE_PROVIDER=ark" in res["error"] and set(res["todo"]) == {"C01", "P02"}
    # run 会停在 gen，不会静默用占位图继续
    r = pipeline.run(d, backend="jianying")
    assert r["stopped_at"] == "gen" and r["error_code"] == "need_image_api" and "voice" not in r
    # 明确选择占位图才继续
    ok = pipeline.gen(d, allow_placeholders=True)
    assert ok["ok"] and ok["action_required"] == "manual_images"


def test_no_reminder_when_images_ready(tmp_path):
    d = _project_missing(tmp_path, ids=())
    res = pipeline.gen(d)
    assert res["ok"] and res["generated"] == 0


def test_ark_openai_compatible(tmp_path, server, monkeypatch):
    d = _project_missing(tmp_path)
    monkeypatch.setenv("IMAGE_PROVIDER", "ark")
    monkeypatch.setenv("IMAGE_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE_API_BASE", server)
    res = pipeline.gen(d)
    assert res["ok"] and res["generated"] == 2 and res["provider"] == "ark"
    path, body, auth, _ = Fake.calls[0]
    assert path == "/images/generations" and auth == "Bearer test-key"
    assert body["model"] == imagegen.PRESETS["ark"]["model"] and body["watermark"] is False
    img = Image.open(d / "assets" / "C01.png")
    assert img.mode == "RGBA" and img.getchannel("A").getextrema()[0] == 0   # 白底被抠掉


def test_siliconflow_images_shape(tmp_path, server, monkeypatch):
    d = _project_missing(tmp_path, ids=("P02",))
    monkeypatch.setenv("IMAGE_PROVIDER", "siliconflow")
    monkeypatch.setenv("IMAGE_API_KEY", "k")
    monkeypatch.setenv("IMAGE_API_BASE", server)
    assert pipeline.gen(d)["generated"] == 1


def test_dashscope_async(tmp_path, server, monkeypatch):
    d = _project_missing(tmp_path, ids=("P02",))
    monkeypatch.setenv("IMAGE_PROVIDER", "dashscope")
    monkeypatch.setenv("IMAGE_API_KEY", "k")
    monkeypatch.setenv("IMAGE_API_BASE", server)
    monkeypatch.setattr(imagegen.time, "sleep", lambda s: None)
    res = pipeline.gen(d)
    assert res["ok"] and res["generated"] == 1
    assert Fake.calls[0][3] == "enable" and Fake.calls[0][1]["input"]["prompt"]


def test_settings_hide_key(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "ark")
    monkeypatch.setenv("IMAGE_API_KEY", "secret-123")
    st = imagegen.settings()
    assert st["configured"] and "secret-123" not in json.dumps(st)
