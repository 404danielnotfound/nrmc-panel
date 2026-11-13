"""NR-MC 控制台後端 API。

Flask 應用，提供：
    GET    /api/health               健康檢查
    GET    /api/devices              列出設備
    POST   /api/devices              新增設備
    DELETE /api/devices/<id>         移除設備
    PATCH  /api/devices/<id>         更新設備名稱
    GET    /api/devices/<id>/state   讀取設備連線與 IO 狀態
    POST   /api/devices/<id>/do      寫入 DO 通道
    POST   /api/devices/<id>/all     全部通道開／關
    POST   /api/scan/start           開始網段掃描
    GET    /api/scan/status          查詢掃描進度與結果
    POST   /api/at                   送出任意 AT 指令（除錯用）

架構說明：
    - 掃描在背景 thread 執行，狀態存於記憶體供輪詢
    - 每次狀態變更會透過 WebSocket 推播
    - 同步阻塞是 OK 的：這是獨立 Add-on 容器，不會卡住 Home Assistant
"""
from __future__ import annotations

import os
import threading
import time

from flask import Flask, jsonify, request

from . import scanner
from .at_client import AtError, AtTcpClient
from .device_store import DeviceStore
from .ws import StateBroadcaster, build_status_event, register_ws_routes

# 讀取 Add-on 的 options（由 s6 run 腳本透過環境變數注入）
AT_PORT = int(os.environ.get("NRMC_AT_PORT", "12345"))
SCAN_SUBNET = os.environ.get("NRMC_SCAN_SUBNET", "")
PING_TIMEOUT = int(os.environ.get("NRMC_PING_TIMEOUT", "300"))
LOG_LEVEL = os.environ.get("NRMC_LOG_LEVEL", "info")

# 掃描狀態（記憶體內，供輪詢查詢）
_scan_state: dict = {
    "running": False,
    "progress": 0,
    "total": 0,
    "message": "尚未開始掃描",
    "results": [],
    "started_at": None,
    "finished_at": None,
}
_scan_lock = threading.Lock()
_scan_thread: threading.Thread | None = None


def create_app(store: DeviceStore | None = None,
               broadcaster: StateBroadcaster | None = None) -> Flask:
    """建立並設定 Flask 應用。"""
    app = Flask(__name__)
    app.json.ensure_ascii = False

    # ★ 注意：不可寫成 `store or DeviceStore()`。
    # DeviceStore 定義了 __len__，空清單的 store 布林值為 False，
    # 會導致傳入的 store 被忽略而建立一個新的（讀到舊的預設檔案）。
    app.store = store if store is not None else DeviceStore()
    app.broadcaster = (
        broadcaster if broadcaster is not None else StateBroadcaster()
    )
    register_ws_routes(app, app.broadcaster)

    # --------------------------------------------------------------
    # 內部工具
    # --------------------------------------------------------------

    def _client_for(device: dict) -> AtTcpClient:
        """依設備記錄建立 AT 客戶端。"""
        return AtTcpClient(
            device.get("host", ""),
            int(device.get("port", AT_PORT)),
            timeout=5.0,
        )

    def _push_state(device: dict, state: dict) -> None:
        """推播設備狀態變更。"""
        app.broadcaster.broadcast(build_status_event(device, state))

    # --------------------------------------------------------------
    # 健康檢查
    # --------------------------------------------------------------

    @app.route("/api/health")
    def health():
        """健康檢查。"""
        return jsonify({
            "ok": True,
            "version": "1.0.0",
            "device_count": len(app.store),
            "ws_clients": app.broadcaster.client_count(),
            "at_port": AT_PORT,
        })

    # --------------------------------------------------------------
    # 設備清單
    # --------------------------------------------------------------

    @app.route("/api/devices", methods=["GET"])
    def list_devices():
        """列出所有設備（含最後已知狀態快取）。"""
        devices = app.store.list()
        for d in devices:
            d.setdefault("state", {})
            d.setdefault("connected", None)
        return jsonify({"devices": devices})

    @app.route("/api/devices", methods=["POST"])
    def add_device():
        """新增設備到清單（會先驗證連線）。"""
        data = request.get_json(silent=True) or {}
        host = str(data.get("host", "")).strip()
        try:
            port = int(data.get("port", AT_PORT))
        except (TypeError, ValueError):
            return jsonify({"error": "port 必須是數字"}), 400

        if not host:
            return jsonify({"error": "host 必填"}), 400

        existing = app.store.exists(host, port)
        if existing:
            return jsonify({
                "error": f"此設備已存在（id={existing['id']}）",
                "device": existing,
            }), 409

        # 驗證連線並帶出名稱與型號
        name = str(data.get("name", "")).strip()
        model = ""
        mac = ""
        connected = False
        error = ""

        client = AtTcpClient(host, port, timeout=5.0)
        try:
            if client.connect():
                connected = True
                if not name:
                    name = client.get_name().detail
                model = client.get_model().detail
            else:
                error = f"無法連線到 {host}:{port}"
        except AtError as exc:
            error = str(exc)
        finally:
            client.close()

        if not connected:
            return jsonify({"error": error or "連線失敗",
                            "connected": False}), 502

        record = app.store.add(
            host=host, port=port,
            name=name or host,
            model=model,
            mac=mac,
        )
        record["connected"] = True
        return jsonify({"ok": True, "device": record}), 201

    @app.route("/api/devices/<int:device_id>", methods=["DELETE"])
    def remove_device(device_id: int):
        """移除設備。"""
        if app.store.remove(device_id):
            return jsonify({"ok": True})
        return jsonify({"error": "找不到此設備"}), 404

    @app.route("/api/devices/<int:device_id>", methods=["PATCH"])
    def patch_device(device_id: int):
        """更新設備的顯示名稱等欄位。"""
        data = request.get_json(silent=True) or {}
        allowed = {k: v for k, v in data.items() if k in ("name",)}
        if not allowed:
            return jsonify({"error": "沒有可更新的欄位"}), 400
        record = app.store.update(device_id, **allowed)
        if not record:
            return jsonify({"error": "找不到此設備"}), 404
        return jsonify({"ok": True, "device": record})

    # --------------------------------------------------------------
    # 設備狀態與控制
    # --------------------------------------------------------------

    @app.route("/api/devices/<int:device_id>/state", methods=["GET"])
    def device_state(device_id: int):
        """讀取設備的連線狀態與 IO 狀態。"""
        device = app.store.get(device_id)
        if not device:
            return jsonify({"error": "找不到此設備"}), 404

        client = _client_for(device)
        try:
            state = client.poll_states()
        except AtError as exc:
            return jsonify({"error": str(exc)}), 502
        finally:
            client.close()

        # 快取狀態到清單中，供列表頁快速顯示
        app.store.update(device_id, state=state, connected=state["connected"])
        _push_state(device, state)

        return jsonify({
            "device": {**device, "state": state,
                       "connected": state["connected"]},
        })

    @app.route("/api/devices/<int:device_id>/do", methods=["POST"])
    def set_do(device_id: int):
        """寫入單一 DO 通道。

        Body: {"channel": 1, "on": true}
        """
        device = app.store.get(device_id)
        if not device:
            return jsonify({"error": "找不到此設備"}), 404

        data = request.get_json(silent=True) or {}
        try:
            channel = int(data.get("channel", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "channel 必須是數字"}), 400
        on = bool(data.get("on", True))

        client = _client_for(device)
        try:
            if not client.connect():
                return jsonify({"error": f"無法連線到 {device.get('host')}"
                                           f":{device.get('port')}"}), 502
            res = client.set_do(channel, on)
            if not res.ok:
                return jsonify({
                    "error": res.detail or "指令失敗",
                    "response": res.response,
                }), 502
            # 回讀完整狀態一併回傳，讓前端可以立即更新
            state = client.poll_states()
        except AtError as exc:
            return jsonify({"error": str(exc)}), 502
        finally:
            client.close()

        app.store.update(device_id, state=state, connected=state["connected"])
        _push_state(device, state)

        return jsonify({"ok": True, "detail": res.detail, "state": state})

    @app.route("/api/devices/<int:device_id>/all", methods=["POST"])
    def set_do_all(device_id: int):
        """一次設定所有 DO 通道。

        Body: {"on": true}
        """
        device = app.store.get(device_id)
        if not device:
            return jsonify({"error": "找不到此設備"}), 404

        data = request.get_json(silent=True) or {}
        on = bool(data.get("on", True))

        client = _client_for(device)
        try:
            if not client.connect():
                return jsonify({"error": f"無法連線到 {device.get('host')}"
                                           f":{device.get('port')}"}), 502
            res = client.set_do_all(on)
            if not res.ok:
                return jsonify({"error": res.detail or "指令失敗"}), 502
            state = client.poll_states()
        except AtError as exc:
            return jsonify({"error": str(exc)}), 502
        finally:
            client.close()

        app.store.update(device_id, state=state, connected=state["connected"])
        _push_state(device, state)
        return jsonify({"ok": True, "detail": res.detail, "state": state})

    # --------------------------------------------------------------
    # 網段掃描
    # --------------------------------------------------------------

    def _run_scan(subnet: str, at_port: int, timeout_ms: int,
                  targets: list[str] | None) -> None:
        """背景執行掃描。"""
        with _scan_lock:
            _scan_state.update({
                "running": True,
                "progress": 0,
                "total": 0,
                "message": "掃描中...",
                "results": [],
                "started_at": time.time(),
                "finished_at": None,
            })

        def progress(done: int, total: int) -> None:
            with _scan_lock:
                _scan_state["progress"] = done
                _scan_state["total"] = total

        def log(msg: str) -> None:
            app.logger.info("[scan] %s", msg)

        try:
            results = scanner.discover(
                subnet=subnet,
                at_port=at_port,
                timeout_ms=timeout_ms,
                progress=progress,
                log=log,
                targets=targets,
            )
            with _scan_lock:
                _scan_state["results"] = results
                _scan_state["message"] = (
                    f"掃描完成，找到 "
                    f"{sum(1 for r in results if r['is_device'])} 台設備"
                )
                _scan_state["finished_at"] = time.time()
            app.broadcaster.broadcast({
                "type": "scan_finished",
                "results": results,
            })
        except Exception as exc:  # noqa: BLE001
            with _scan_lock:
                _scan_state["message"] = f"掃描錯誤：{exc}"
                _scan_state["finished_at"] = time.time()
            app.logger.exception("掃描發生錯誤")
        finally:
            with _scan_lock:
                _scan_state["running"] = False

    @app.route("/api/scan/start", methods=["POST"])
    def start_scan():
        """開始網段掃描（背景執行）。"""
        global _scan_thread

        with _scan_lock:
            if _scan_state["running"]:
                return jsonify({"error": "掃描已在進行中"}), 409

        data = request.get_json(silent=True) or {}
        subnet = str(data.get("subnet", "") or SCAN_SUBNET)
        try:
            at_port = int(data.get("port", AT_PORT))
        except (TypeError, ValueError):
            return jsonify({"error": "port 必須是數字"}), 400
        try:
            timeout_ms = int(data.get("timeout_ms", PING_TIMEOUT))
        except (TypeError, ValueError):
            timeout_ms = PING_TIMEOUT

        # ★ Mock 模式：指定 targets 時直接查詢，不做 ping 探測
        targets = data.get("targets")
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        elif targets is not None and not isinstance(targets, list):
            targets = None

        _scan_thread = threading.Thread(
            target=_run_scan,
            args=(subnet, at_port, timeout_ms, targets),
            daemon=True,
        )
        _scan_thread.start()
        return jsonify({"ok": True, "subnet": subnet, "port": at_port})

    @app.route("/api/scan/status", methods=["GET"])
    def scan_status():
        """查詢掃描進度與結果。"""
        with _scan_lock:
            return jsonify(dict(_scan_state))

    # --------------------------------------------------------------
    # AT 指令測試
    # --------------------------------------------------------------

    @app.route("/api/at", methods=["POST"])
    def send_at():
        """送出任意 AT 指令（除錯用）。

        Body: {"id": 1, "command": "AT+NAME=?"}
        或    {"host": "192.168.30.250", "port": 12345, "command": "AT"}
        """
        data = request.get_json(silent=True) or {}
        command = str(data.get("command", "")).strip()
        if not command:
            return jsonify({"error": "command 必填"}), 400

        device_id = data.get("id")
        if device_id is not None:
            device = app.store.get(int(device_id))
            if not device:
                return jsonify({"error": "找不到此設備"}), 404
            client = _client_for(device)
        else:
            # 沒指定設備時，直接用 request 的 host/port
            host = str(data.get("host", "")).strip()
            if not host:
                return jsonify({"error": "需要 id 或 host"}), 400
            try:
                port = int(data.get("port", AT_PORT))
            except (TypeError, ValueError):
                return jsonify({"error": "port 必須是數字"}), 400
            client = AtTcpClient(host, port, timeout=5.0)

        try:
            if not client.connect():
                return jsonify({"error": "無法連線"}), 502
            response = client.send(command)
        except AtError as exc:
            return jsonify({"error": str(exc)}), 502
        finally:
            client.close()

        return jsonify({"ok": True, "command": command, "response": response})

    return app


# ----------------------------------------------------------------------
# 進入點
# ----------------------------------------------------------------------
def main() -> None:
    """啟動後端伺服器。"""
    import logging

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    app = create_app()
    port = int(os.environ.get("NRMC_HTTP_PORT", "8080"))
    app.logger.info("NR-MC 控制台後端啟動，監聽 0.0.0.0:%s", port)
    app.logger.info("AT port=%s 掃描網段=%s", AT_PORT, SCAN_SUBNET or "(自動)")
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
