"""開發用靜態伺服器。

把 Flutter build/web 的靜態檔與後端 API 放在同一個 port 提供，
方便在瀏覽器裡測試（模擬 Nginx 的 proxy_pass 行為）。

啟動：
    python dev_server.py --port 5000
"""
from __future__ import annotations

import argparse
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent.parent / "frontend" / "build" / "web"
BACKEND = "127.0.0.1:8080"

import urllib.request
import urllib.error


class DevHandler(SimpleHTTPRequestHandler):
    """靜態檔 + API 代理。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy("GET")
        if self.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy("POST")
        self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            return self._proxy("PATCH")
        self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            return self._proxy("DELETE")
        self.send_error(405)

    def _proxy(self, method: str):
        """把請求轉發到 Flask 後端。"""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        req = urllib.request.Request(
            f"http://{BACKEND}{self.path}",
            data=body,
            method=method,
            headers={
                "Content-Type": self.headers.get(
                    "Content-Type", "application/json"
                ),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header(
                    "Content-Type",
                    resp.headers.get("Content-Type", "application/json"),
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            msg = f'{{"error": "{e}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def end_headers(self):
        # 開發時關閉快取，改完立刻看到效果
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # 靜音


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    if not WEB_ROOT.is_dir():
        raise SystemExit(
            f"找不到前端檔案：{WEB_ROOT}\n"
            "請先執行：cd frontend && flutter build web --release"
        )

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DevHandler)
    print(f"[dev] 靜態檔：{WEB_ROOT}")
    print(f"[dev] API 代理：{BACKEND}")
    print(f"[dev] 開啟 http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
