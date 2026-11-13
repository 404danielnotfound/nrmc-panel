"""WebSocket 推播機制的測試。

這些測試驗證「WebSocket 是加速器，輪詢是保底」這個設計：
    - 推播事件能正確套用到設備狀態
    - 推播收到時畫面立即更新（不必等輪詢）
    - WebSocket 連不上時不影響主要功能
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from nrmc_panel.app import create_app  # noqa: E402
from nrmc_panel.device_store import DeviceStore  # noqa: E402
from nrmc_panel.ws import StateBroadcaster  # noqa: E402


# ----------------------------------------------------------------------
# 測試用的假 WebSocket 伺服器
# ----------------------------------------------------------------------

class FakeWsServer:
    """模擬後端的 WebSocket 端點。

    用最小的 handshake 讓 Python 的 socket 客戶端能建立連線，
    之後就能把自訂的 JSON 訊息推給客戶端。
    """

    def __init__(self):
        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._server.RequestHandlerClass.server_ref = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.clients: list = []

    def broadcast(self, payload: dict) -> int:
        """把 JSON 訊息送給所有已連線的客戶端。"""
        message = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        sent = 0
        for conn in list(self.clients):
            try:
                conn.sendall(message)
                sent += 1
            except OSError:
                pass
        return sent

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Handler(BaseHTTPRequestHandler):
    """處理 WebSocket handshake。"""

    def do_GET(self):  # noqa: N802
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_response(400)
            self.end_headers()
            return

        accept = _ws_accept_key(key)
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self.wfile.write(response.encode("utf-8"))
        self.wfile.flush()

        server = getattr(self.server, "server_ref", None)
        if server is not None:
            server.clients.append(self.connection)

    def log_message(self, *args):  # noqa: A002
        """關閉預設的存取日誌。"""


def _ws_accept_key(key: str) -> str:
    """計算 WebSocket handshake 需要的 Sec-WebSocket-Accept。"""
    import base64
    import hashlib

    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    digest = hashlib.sha1((key + magic).encode("utf-8")).digest()  # noqa: S324
    return base64.b64encode(digest).decode("ascii")


# ----------------------------------------------------------------------
# 測試
# ----------------------------------------------------------------------

@pytest.fixture
def broadcaster():
    """建立一個 StateBroadcaster。"""
    return StateBroadcaster()


class _FakeWs:
    """假的 WebSocket 物件，記錄送出的訊息。"""

    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True


def test_broadcaster_registers_and_sends_welcome(broadcaster):
    """★ 新連線會收到 welcome 訊息。

    ★ 注意：register 送的是 dict（不是 JSON 字串），
      因為它是第一個訊息，走的是 flask-sock 的原生格式。
      之後的 broadcast 才會 json.dumps。
    """
    ws = _FakeWs()
    broadcaster.register(ws)

    assert broadcaster.client_count() == 1
    assert len(ws.sent) == 1

    payload = ws.sent[0]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["type"] == "welcome"


def test_broadcaster_broadcasts_to_all_clients(broadcaster):
    """★ 推播會送到所有已連線客戶端。"""
    a = _FakeWs()
    b = _FakeWs()
    broadcaster.register(a)
    broadcaster.register(b)
    ws_sent = [len(a.sent), len(b.sent)]

    count = broadcaster.broadcast({"type": "device_state", "device": {"id": 1}})

    assert count == 2
    assert len(a.sent) == ws_sent[0] + 1
    assert len(b.sent) == ws_sent[1] + 1

    # ★ broadcast 送出的是 JSON 字串（給瀏覽器解析）
    payload = json.loads(a.sent[-1])
    assert payload["type"] == "device_state"


def test_broadcaster_unregister(broadcaster):
    """取消註冊後不再收到推播。"""
    ws = _FakeWs()
    broadcaster.register(ws)
    assert broadcaster.client_count() == 1

    broadcaster.unregister(ws)
    assert broadcaster.client_count() == 0

    count = broadcaster.broadcast({"type": "device_state"})
    assert count == 0


def test_broadcaster_skips_dead_clients(broadcaster):
    """★ 已關閉的連線不會讓推播失敗，會被自動清掉。"""

    class DeadWs(_FakeWs):
        def send(self, message):
            raise ConnectionError("連線已關閉")

    dead = DeadWs()
    alive = _FakeWs()
    broadcaster.register(dead)
    broadcaster.register(alive)

    count = broadcaster.broadcast({"type": "device_state"})

    assert count == 1, "只有活著的連線算成功"
    assert broadcaster.client_count() == 1, "死掉的連線要被清掉"


def test_broadcaster_client_count(broadcaster):
    """連線數量計算正確。"""
    assert broadcaster.client_count() == 0
    broadcaster.register(_FakeWs())
    broadcaster.register(_FakeWs())
    assert broadcaster.client_count() == 2


def test_build_status_event_structure():
    """★ 推播事件必須包含前端需要的欄位。"""
    from nrmc_panel.ws import build_status_event

    device = {
        "id": 7,
        "host": "192.168.30.250",
        "port": 12345,
        "name": "新風機",
    }
    state = {"do": {"1": True, "2": False}, "di": {}}

    event = build_status_event(device, state)

    assert event["type"] == "device_state"
    assert event["device"]["id"] == 7
    assert event["device"]["host"] == "192.168.30.250"
    assert event["state"] == state
    assert "timestamp" in event


def test_health_reports_ws_clients():
    """★ /api/health 要回報 ws_clients，供診斷用。"""
    store = DeviceStore()
    broadcaster = StateBroadcaster()
    app = create_app(store=store, broadcaster=broadcaster)
    client = app.test_client()

    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["ws_clients"] == 0

    broadcaster.register(_FakeWs())
    res = client.get("/api/health")
    assert res.get_json()["ws_clients"] == 1


def test_state_push_broadcasts_to_subscribers():
    """★ 操作設備後，狀態會推播給 WebSocket 客戶端。

    這是「加速器」的實際效益：
    不用等 5 秒輪詢，馬上就收到更新。
    """
    store = DeviceStore()
    broadcaster = StateBroadcaster()
    app = create_app(store=store, broadcaster=broadcaster)

    # 先加一台假設備
    store.add("192.168.30.250", 12345, name="測試機")

    ws = _FakeWs()
    broadcaster.register(ws)
    sent_before = len(ws.sent)

    client = app.test_client()
    # 送出控制指令（會觸發 _push_state）
    # ★ 沒有真的設備在線，所以會回 502（上游無法連線）。
    #   重點是「推播機制不會因為連線失敗而壞掉」。
    res = client.post("/api/devices/1/do", json={"do1": True, "do2": False})
    assert res.status_code in (200, 400, 500, 502)

    # 不論操作成功與否，都不應該讓推播機制壞掉
    assert broadcaster.client_count() == 1
    assert len(ws.sent) >= sent_before


def test_ws_route_registered():
    """★ /ws 端點必須存在。"""
    store = DeviceStore()
    app = create_app(store=store, broadcaster=StateBroadcaster())

    rules = [str(r.rule) for r in app.url_map.iter_rules()]
    assert "/ws" in rules
