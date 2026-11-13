"""WebSocket 狀態推播。

用 flask-sock 提供 /ws 端點，把設備狀態變更即時推給所有前端連線。

為什麼需要：
    - 真實設備的回應延遲可達 0.647 秒，若前端輪詢會感覺到卡頓
    - 多台設備同時變更時，推播比逐台輪詢快得多

為什麼不是主要機制：
    - Ingress 需要 ingress_stream: true 才會放行 WebSocket
    - 因此輪詢（polling）始終保留為保底機制，
      WebSocket 只是加速手段，兩者並存最穩定
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from flask_sock import Sock


class StateBroadcaster:
    """管理 WebSocket 連線並廣播訊息。"""

    def __init__(self):
        self._clients: list[Any] = []
        self._lock = threading.Lock()

    def register(self, ws) -> None:
        """加入一個新的 WebSocket 連線。"""
        with self._lock:
            self._clients.append(ws)
        self._safe_send(ws, {
            "type": "welcome",
            "message": "已連線到 NR-MC 控制台",
        })

    def unregister(self, ws) -> None:
        """移除一個已關閉的 WebSocket 連線。"""
        with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)

    def client_count(self) -> int:
        """目前連線數。"""
        with self._lock:
            return len(self._clients)

    def broadcast(self, payload: dict) -> int:
        """把資料廣播給所有連線。

        Returns:
            成功送出的連線數。
        """
        message = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            clients = list(self._clients)

        sent = 0
        dead: list[Any] = []
        for ws in clients:
            if self._safe_send(ws, message):
                sent += 1
            else:
                dead.append(ws)

        if dead:
            with self._lock:
                for ws in dead:
                    if ws in self._clients:
                        self._clients.remove(ws)
        return sent

    @staticmethod
    def _safe_send(ws, message: str) -> bool:
        """送出訊息，失敗時回傳 False（不拋例外）。"""
        try:
            ws.send(message)
            return True
        except Exception:
            return False


def register_ws_routes(app, broadcaster: StateBroadcaster) -> None:
    """在 Flask 應用上註冊 WebSocket 端點。"""

    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        """WebSocket 端點：推播設備狀態變更。"""
        broadcaster.register(ws)
        try:
            while True:
                # 接收前端訊息（心跳），讓連線保持活著
                # timeout=1 表示最多等 1 秒就回圈檢查
                msg = ws.receive(timeout=1)
                if msg is None:
                    continue
                # 前端可以送 {"type":"ping"}，回應 pong
                try:
                    data = json.loads(msg)
                except (json.JSONDecodeError, TypeError):
                    continue
                if data.get("type") == "ping":
                    broadcaster._safe_send(ws, json.dumps({"type": "pong"}))
        except Exception:
            pass
        finally:
            broadcaster.unregister(ws)


def build_status_event(device: dict, state: dict) -> dict:
    """組裝要推播的狀態事件。"""
    return {
        "type": "device_state",
        "timestamp": time.time(),
        "device": {
            "id": device.get("id"),
            "host": device.get("host"),
            "port": device.get("port"),
            "name": device.get("name"),
        },
        "state": state,
    }
