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

import logging
import os
import threading
import time

from flask import Flask, jsonify, request

from . import scanner
from .at_client import AtError, AtTcpClient
from .device_store import DeviceStore
from .ws import StateBroadcaster, build_status_event, register_ws_routes

_log = logging.getLogger("nrmc_panel")

# 讀取 Add-on 的 options（由 s6 run 腳本透過環境變數注入）
AT_PORT = int(os.environ.get("NRMC_AT_PORT", "12345"))
SCAN_SUBNET = os.environ.get("NRMC_SCAN_SUBNET", "")
PING_TIMEOUT = int(os.environ.get("NRMC_PING_TIMEOUT", "300"))
LOG_LEVEL = os.environ.get("NRMC_LOG_LEVEL", "info")

# ★ DO 互鎖群組：同一群組內的通道互斥，同時只能開啟一個。
#   NR-MC2201-WIFI / NR-MC4401 的 DO1、DO2 對應風機一檔／二檔，
#   必須互斥（否則兩檔同時導通，前端也無法判斷目前檔位）。
#   ★ 設定格式（環境變數 NRMC_DO_INTERLOCK）：
#       群組之間用分號「;」分隔，群組內的通道用逗號「,」分隔。
#       範例：「1,2」        → DO1 與 DO2 互鎖（單一群組，預設值）
#             「1,2;3,4」    → 兩個群組，DO1/DO2 互斥、DO3/DO4 互斥
#             「1,2;3,4;5,6」→ 三個群組
#   留空則不做任何互鎖。
#   ★ 為什麼上位機要主動送互鎖？
#     韌體理論上有硬體互鎖，但實測部分版本不會自動關閉另一路，
#     導致回讀到兩路同時開啟（「異常」狀態，三個按鈕都不高亮）。
#     由上位機明確送出關閉指令，才能保證狀態一致。
DO_INTERLOCK_GROUPS: list[tuple[int, ...]] = []
_raw_interlock = os.environ.get("NRMC_DO_INTERLOCK", "1,2").strip()
if _raw_interlock:
    for _grp in _raw_interlock.split(";"):
        _nums = tuple(
            int(x) for x in _grp.strip().split(",")
            if x.strip().isdigit()
        )
        if len(_nums) > 1:
            DO_INTERLOCK_GROUPS.append(_nums)
        elif _nums:
            _log.warning(
                "NRMC_DO_INTERLOCK 中的 %r 只有單一通道，已忽略（無法互鎖）",
                _grp)
if not DO_INTERLOCK_GROUPS:
    # 設定無效或未設定 → 退回預設：DO1/DO2 互鎖（風機一檔／二檔）
    DO_INTERLOCK_GROUPS = [(1, 2)]
    _log.warning("NRMC_DO_INTERLOCK 未設定或無效，預設啟用 DO1/DO2 互鎖")


def _interlock_for(channel: int) -> tuple[int, ...]:
    """取得該通道所屬的互鎖群組（沒有則回傳空 tuple）。"""
    for group in DO_INTERLOCK_GROUPS:
        if channel in group:
            return group
    return ()


def _all_interlock() -> tuple[int, ...]:
    """取得所有互鎖群組涉及的通道（給「全部開啟／關閉」用）。"""
    result: list[int] = []
    for group in DO_INTERLOCK_GROUPS:
        for ch in group:
            if ch not in result:
                result.append(ch)
    return tuple(result)

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

# ★ P0-5：每台設備一把寫入鎖，防止同一台設備被同時下指令。
#   問題來源：使用者看到風機慢，又按一次 —— 兩次 POST 各自開一條 TCP
#   連線同時送 AT，設備端回應交錯，前端收到亂序結果看起來像「沒反應」。
#   這裡用「非阻塞 try-lock」：拿不到鎖就直接回 409，
#   讓前端可以明確顯示「上一個指令還在執行中」。
_device_io_locks: dict[int, threading.Lock] = {}
_device_io_locks_guard = threading.Lock()


def _io_lock_for(device_id: int) -> threading.Lock:
    """取得（並建立）某台設備的寫入鎖。"""
    with _device_io_locks_guard:
        lock = _device_io_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_io_locks[device_id] = lock
        return lock


# ★ P1-1：持久 TCP 連線池
#   每次 HTTP 請求都新建連線 + 關閉，等於每次都付一次 TCP 三向握手
#   （加上 Linux SYN 重傳的機率成本）。同一台設備連續操作時
#   直接複用既有連線，可省掉整段握手時間。
#   key = (host, port)；閒置超過 IDLE_TIMEOUT 就關掉釋放。
_CONNECTION_POOL: dict[tuple[str, int], AtTcpClient] = {}
_CONNECTION_POOL_LOCK = threading.Lock()
# 連線閒置多久後回收（秒）
CONNECTION_IDLE_TIMEOUT = 60.0


def _client_for(device: dict) -> AtTcpClient:
    """依設備記錄建立 AT 客戶端。"""
    return AtTcpClient(
        device.get("host", ""),
        int(device.get("port", AT_PORT)),
        timeout=5.0,
    )


def _pool_key(device: dict) -> tuple[str, int]:
    return (device.get("host", ""), int(device.get("port", AT_PORT)))


def _get_pooled_client(device: dict) -> tuple[AtTcpClient, bool]:
    """取得可用的 AT 客戶端。

    Returns:
        (client, reused) —— reused=True 表示這是從池子拿的既有連線。
    """
    key = _pool_key(device)
    with _CONNECTION_POOL_LOCK:
        client = _CONNECTION_POOL.get(key)
        if client is not None:
            if client.is_connected:
                client.mark_reused()
                return client, True
            # 連線已斷（設備重開 / 網路中斷）：丟掉重來
            try:
                client.close()
            except Exception:
                pass
            _CONNECTION_POOL.pop(key, None)

    fresh = _client_for(device)
    return fresh, False


def _release_pooled_client(client: AtTcpClient, device: dict,
                           keep: bool) -> None:
    """操作結束後處理連線。

    keep=True  → 放回池子（idle timeout 後自動回收）
    keep=False → 直接關閉
    """
    key = _pool_key(device)
    if not keep:
        try:
            client.close()
        except Exception:
            pass
        with _CONNECTION_POOL_LOCK:
            if _CONNECTION_POOL.get(key) is client:
                _CONNECTION_POOL.pop(key, None)
        return

    with _CONNECTION_POOL_LOCK:
        old = _CONNECTION_POOL.get(key)
        if old is not None and old is not client:
            try:
                old.close()
            except Exception:
                pass
        _CONNECTION_POOL[key] = client


def _close_stale_connections() -> None:
    """回收閒置過久的連線（由背景 thread 定期呼叫）。"""
    now = time.monotonic()
    with _CONNECTION_POOL_LOCK:
        stale = []
        for key, client in list(_CONNECTION_POOL.items()):
            if now - client.last_used_at > CONNECTION_IDLE_TIMEOUT:
                stale.append((key, client))
                _CONNECTION_POOL.pop(key, None)
    for key, client in stale:
        try:
            client.close()
        except Exception:
            pass
    if stale:
        _log.info("[pool] 回收 %d 條閒置連線", len(stale))


def _start_pool_reaper() -> threading.Thread:
    """啟動背景 thread，定期回收閒置連線。"""
    def loop() -> None:
        while True:
            time.sleep(15)
            try:
                _close_stale_connections()
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True, name="pool-reaper")
    t.start()
    return t


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
            "version": "1.0.4",
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

        try:
            client, reused = _get_pooled_client(device)
            # ★ 先取得 client（記下 reused），再清計時，最後補回 reused
            was_reused = reused
            client.clear_timings()
            if was_reused:
                client.mark_reused()
            if not reused and not client.connect():
                return jsonify({"error": f"無法連線到 {device.get('host')}"
                                           f":{device.get('port')}"}), 502
            state = client.poll_states()
            timings = list(client.op_timings)
        except AtError as exc:
            _release_pooled_client(client, device, keep=False)
            return jsonify({"error": str(exc)}), 502
        finally:
            # ★ P1-1：查詢路徑同樣放回池子，讓後續寫入能複用連線
            _release_pooled_client(client, device, keep=True)

        # 快取狀態到清單中，供列表頁快速顯示
        app.store.update(device_id, state=state, connected=state["connected"])
        _push_state(device, state)

        return jsonify({
            "device": {**device, "state": state,
                       "connected": state["connected"]},
            "timings": timings,
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

        # ★ P0-5：防止同一台設備被重複下指令（非阻塞）
        io_lock = _io_lock_for(device_id)
        if not io_lock.acquire(blocking=False):
            return jsonify({
                "error": "上一個指令還在執行中，請稍候再試",
                "busy": True,
            }), 409
        try:
            client, reused = _get_pooled_client(device)
            # ★ 必須在 _get_pooled_client() 之後清空計時，
            #   否則 mark_reused() 記錄的「連線複用」會被清掉，
            #   瀑布圖就看不出這次是複用還是新建連線。
            #   → 改成：先記下 reused 狀態，清空後再補回。
            was_reused = reused
            client.clear_timings()
            if was_reused:
                client.mark_reused()
            if not reused and not client.connect():
                return jsonify({"error": f"無法連線到 {device.get('host')}"
                                           f":{device.get('port')}"}), 502
            res = client.set_do(channel, on,
                                interlock_channels=_interlock_for(channel))
            if not res.ok:
                # 寫入失敗就不放回池子（連線狀態可能已不可靠）
                _release_pooled_client(client, device, keep=False)
                return jsonify({
                    "error": res.detail or "指令失敗",
                    "response": res.response,
                    "timings": client.op_timings,
                }), 502
            # ★ P0-1 + P1-2：寫入後只回讀 DO（最省的路徑）。
            #   - quick=True：跳過 AT 握手與靜態資訊（名稱/型號/韌體/AI）
            #   - include_di=False：連 DI 都不查
            #     DI 是外部輸入狀態，切檔不會改變它；
            #     而 AT+OCCH0 又要 ~440ms，是切檔路徑上最大的一段浪費。
            #   舊流程在這裡多花 ~3.3 秒，是「按下去要等 5~10 秒」的主因。
            state = client.poll_states(quick=True, include_di=False)
            # ★ include_di=False 時 DI 會是空的，這裡把上一次快取的 DI
            #   合併回來，避免前端顯示的 DI 狀態突然消失。
            #   （DI 是外部輸入，切檔不會改變它，所以沿用舊值是正確的。）
            #   ★ 要從 store 重新取：區域變數 device 是進來時的快照，
            #     未必包含上一次查詢寫入的 state。
            if not state.get("di"):
                fresh = app.store.get(device_id) or {}
                prev = fresh.get("state") or {}
                prev_di = prev.get("di")
                if prev_di:
                    state["di"] = prev_di
            timings = list(client.op_timings)
        except AtError as exc:
            _release_pooled_client(client, device, keep=False)
            return jsonify({"error": str(exc)}), 502
        finally:
            # ★ P1-1：成功的連線放回池子，下次操作直接複用
            _release_pooled_client(client, device, keep=True)
            io_lock.release()

        app.store.update(device_id, state=state, connected=state["connected"])
        _push_state(device, state)

        return jsonify({
            "ok": True,
            "detail": res.detail,
            "state": state,
            "timings": timings,
        })

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

        # ★ P0-5：防止同一台設備被重複下指令（非阻塞）
        io_lock = _io_lock_for(device_id)
        if not io_lock.acquire(blocking=False):
            return jsonify({
                "error": "上一個指令還在執行中，請稍候再試",
                "busy": True,
            }), 409
        try:
            client, reused = _get_pooled_client(device)
            # ★ 先取得 client（會記下 reused），再清計時，最後補回 reused
            was_reused = reused
            client.clear_timings()
            if was_reused:
                client.mark_reused()
            if not reused and not client.connect():
                return jsonify({"error": f"無法連線到 {device.get('host')}"
                                           f":{device.get('port')}"}), 502
            res = client.set_do_all(on, interlock_channels=_all_interlock())
            if not res.ok:
                _release_pooled_client(client, device, keep=False)
                return jsonify({"error": res.detail or "指令失敗"}), 502
            # ★ P0-1 + P1-2：同上，寫入後只回讀 DO
            state = client.poll_states(quick=True, include_di=False)
            # ★ 沿用先前的 DI 快取值（切檔不會改變 DI）
            if not state.get("di"):
                fresh = app.store.get(device_id) or {}
                prev = fresh.get("state") or {}
                prev_di = prev.get("di")
                if prev_di:
                    state["di"] = prev_di
            timings = list(client.op_timings)
        except AtError as exc:
            _release_pooled_client(client, device, keep=False)
            return jsonify({"error": str(exc)}), 502
        finally:
            _release_pooled_client(client, device, keep=True)
            io_lock.release()

        app.store.update(device_id, state=state, connected=state["connected"])
        _push_state(device, state)
        return jsonify({
            "ok": True,
            "detail": res.detail,
            "state": state,
            "timings": timings,
        })

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
        pooled = False
        if device_id is not None:
            device = app.store.get(int(device_id))
            if not device:
                return jsonify({"error": "找不到此設備"}), 404
            # ★ P1-1：AT 測試也走連線池，這樣除錯時看到的耗時
            #   與實際操作一致（都是複用既有連線）。
            client, pooled = _get_pooled_client(device)
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
            # ★ 先取得 client（記下 reused），再清計時，最後補回 reused
            was_pooled = pooled
            client.clear_timings()
            if was_pooled:
                client.mark_reused()
            if not pooled and not client.connect():
                return jsonify({"error": "無法連線"}), 502
            response = client.send(command)
            timings = list(client.op_timings)
        except AtError as exc:
            if device_id is not None:
                _release_pooled_client(client, device, keep=False)
            else:
                client.close()
            return jsonify({"error": str(exc)}), 502
        finally:
            if device_id is not None:
                _release_pooled_client(client, device, keep=True)
            else:
                client.close()

        # ★ P2-4：AT 測試也回傳耗時，讓前端能顯示逐段時間
        return jsonify({
            "ok": True,
            "command": command,
            "response": response,
            "timings": timings,
        })

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
    # ★ P1-1：啟動連線池回收 thread
    _start_pool_reaper()
    app.logger.info("NR-MC 控制台後端啟動，監聽 0.0.0.0:%s", port)
    app.logger.info("AT port=%s 掃描網段=%s", AT_PORT, SCAN_SUBNET or "(自動)")
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
