"""整合測試：完整走查後端 API 與 Mock 設備的協作流程。

對應計劃中的 Phase 6 驗收標準：
    掃描 → 加入清單 → 顯示連線狀態 → 開一檔 → 開二檔（驗證互鎖）→ 關閉
"""
from __future__ import annotations

import threading
import time

import pytest

from nrmc_panel.app import create_app
from nrmc_panel.device_store import DeviceStore
from nrmc_panel.mock_device import create_mock_server
from nrmc_panel.ws import StateBroadcaster


@pytest.fixture
def mock_fan():
    """啟動一台 Mock 新風設備（模擬 NR-MC2201-WIFI）。"""
    server = create_mock_server(
        "127.0.0.1", 0, name="EXFAN_LIVING", model="NR-MC2201-WIFI"
    )
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def client(tmp_path, mock_fan):
    """整合測試用的 Flask 測試 client 與 Mock 設備資訊。"""
    store = DeviceStore(path=tmp_path / "devices.json")
    app = create_app(store=store, broadcaster=StateBroadcaster())
    app.config.update(TESTING=True)
    host, port = mock_fan
    return app.test_client(), host, port


def _wait_for_scan(client, timeout: float = 15.0) -> dict:
    """等待背景掃描結束並回傳狀態。"""
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/scan/status").get_json()
        if not status["running"] and status["finished_at"] is not None:
            return status
        time.sleep(0.1)
    return status


# ----------------------------------------------------------------------
# 完整流程
# ----------------------------------------------------------------------

def test_full_workflow_scan_add_control_interlock(client):
    """★ 完整流程：掃描 → 加入 → 查詢 → 開一檔 → 開二檔 → 關閉。"""
    api, host, port = client

    # --- 步驟 1：掃描 ---
    res = api.post("/api/scan/start", json={"targets": [host], "port": port})
    assert res.status_code == 200

    status = _wait_for_scan(api)
    assert status["running"] is False
    assert len(status["results"]) == 1

    found = status["results"][0]
    assert found["name"] == "EXFAN_LIVING"
    assert found["model"] == "NR-MC2201-WIFI"
    assert found["is_device"] is True
    assert found["at_port"] == port

    # --- 步驟 2：加入清單 ---
    res = api.post("/api/devices", json={
        "host": found["ip"], "port": found["at_port"],
    })
    assert res.status_code == 201
    device_id = res.get_json()["device"]["id"]
    assert res.get_json()["device"]["name"] == "EXFAN_LIVING"

    # --- 步驟 3：顯示連線狀態 ---
    res = api.get(f"/api/devices/{device_id}/state")
    assert res.status_code == 200
    state = res.get_json()["device"]["state"]
    assert state["connected"] is True
    assert state["model"] == "NR-MC2201-WIFI"
    assert state["firmware"] == "NR-MC2201-WIFI-VER30.3"
    assert state["do"] == {"1": False, "2": False}

    # --- 步驟 4：開一檔 ---
    res = api.post(f"/api/devices/{device_id}/do",
                   json={"channel": 1, "on": True})
    assert res.status_code == 200
    state = res.get_json()["state"]
    assert state["do"]["1"] is True
    assert state["do"]["2"] is False

    # --- 步驟 5：開二檔 → 互鎖，自動關掉一檔 ---
    res = api.post(f"/api/devices/{device_id}/do",
                   json={"channel": 2, "on": True})
    assert res.status_code == 200
    state = res.get_json()["state"]
    assert state["do"]["2"] is True
    assert state["do"]["1"] is False, "互鎖失效：開二檔時一檔沒有自動關閉"

    # 再查一次狀態，確認硬體真的維持互鎖
    res = api.get(f"/api/devices/{device_id}/state")
    state = res.get_json()["device"]["state"]
    assert state["do"]["2"] is True
    assert state["do"]["1"] is False

    # --- 步驟 6：關閉 ---
    res = api.post(f"/api/devices/{device_id}/all", json={"on": False})
    assert res.status_code == 200
    state = res.get_json()["state"]
    assert state["do"]["1"] is False
    assert state["do"]["2"] is False

    # --- 步驟 7：清單反映最後狀態 ---
    devices = api.get("/api/devices").get_json()["devices"]
    assert len(devices) == 1
    assert devices[0]["connected"] is True
    assert devices[0]["state"]["do"] == {"1": False, "2": False}


def test_multiple_devices_are_independent(client, mock_fan):
    """多台設備互不影響。"""
    api, host, port = client

    # 啟動第二台 Mock 設備
    server2 = create_mock_server(
        "127.0.0.1", 0, name="EXFAN_BEDROOM", model="NR-MC2201-WIFI"
    )
    host2, port2 = server2.server_address[0], server2.server_address[1]
    threading.Thread(target=server2.serve_forever, daemon=True).start()

    try:
        # 兩台都加入
        id1 = api.post("/api/devices",
                       json={"host": host, "port": port}).get_json()["device"]["id"]
        id2 = api.post("/api/devices",
                       json={"host": host2, "port": port2}).get_json()["device"]["id"]

        # 只控制第一台
        api.post(f"/api/devices/{id1}/do", json={"channel": 1, "on": True})

        # 第二台不應該受影響
        s1 = api.get(f"/api/devices/{id1}/state").get_json()["device"]["state"]
        s2 = api.get(f"/api/devices/{id2}/state").get_json()["device"]["state"]
        assert s1["do"]["1"] is True
        assert s2["do"]["1"] is False
    finally:
        server2.shutdown()
        server2.server_close()


def test_at_command_through_api(client):
    """透過 API 送出 AT 指令。"""
    api, host, port = client

    # 直接用 host/port
    res = api.post("/api/at", json={
        "host": host, "port": port, "command": "AT+NAME=?",
    })
    assert res.status_code == 200
    assert "EXFAN_LIVING" in res.get_json()["response"]

    # 未知指令應回 ERROR
    res = api.post("/api/at", json={
        "host": host, "port": port, "command": "AT+BOGUS",
    })
    assert res.status_code == 200
    assert "ERROR" in res.get_json()["response"]


def test_device_list_survives_restart(client):
    """★ 設備清單要能跨程序重啟保留。"""
    api, host, port = client

    api.post("/api/devices", json={"host": host, "port": port})

    # 模擬重啟：新建 app 但沿用同一個 store 路徑
    store = api.application.store
    store.load()
    assert len(store) == 1
    assert store.list()[0]["name"] == "EXFAN_LIVING"
    assert store.list()[0]["port"] == port
