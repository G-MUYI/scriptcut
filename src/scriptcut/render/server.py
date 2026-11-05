"""本地静态服务（ES Module 不能走 file://）：/ → 项目目录，/__engine__/ → 内置引擎，/__font__/main.* → 字体。"""

from __future__ import annotations

import contextlib
import http.server
import mimetypes
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..config import WEB_DIR

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("font/collection", ".ttc")


def _handler(project_dir: Path, font: Path):
    project_dir = project_dir.resolve()
    web = WEB_DIR.resolve()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html"):
                f = web / "index.html"
            elif path.startswith("/__engine__/"):
                f = (web / path[len("/__engine__/"):]).resolve()
                if web not in f.parents:
                    return self._err(403)
            elif path.startswith("/__font__/"):
                f = font
            else:
                f = (project_dir / path.lstrip("/")).resolve()
                if project_dir not in f.parents:
                    return self._err(403)
            if not f.is_file():
                return self._err(404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _err(self, code):
            self.send_response(code)
            self.end_headers()

    return H


@contextlib.contextmanager
def serve(project_dir: Path, font: Path, port: int = 0):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler(Path(project_dir), Path(font)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
