"""Flask 後端 API 的測試。

使用 Flask 測試 client（不需要真的啟動 HTTP 伺服器），
配合 Mock AT 設備驗證完整的 API 行為。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nrmc_panel.app import create_app
from nrmc_panel.device_store import DeviceStore
from nrmc_panel.ws import StateBroadcaster


@pytest.fixture
def store(tmp_path):
    """使用暫存目錄的設備清單，避免污染開發環境資料。"""
    return DeviceStore(path=tmp_path / "devices.json")


@pytest.fixture
def app(store):
    """建立測試用的 Flask 應用。"""
    application = create_app(store=store, broadcaster=StateBroadcaster())
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    """Flask 測試 client。"""
    return app.test_client()


# ----------------------------------------------------------------------
# 健康檢查
# ----------------------------------------------------------------------

def test_health_returns_ok(client):
    """健康檢查應回 ok。"""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["device_count"] == 0


# ----------------------------------------------------------------------
# 設備清單 CRUD
# ----------------------------------------------------------------------

def test_list_devices_empty(client):
    """初始狀態沒有設備。"""
    res = client.get("/api/devices")
    assert res.status_code == 200
    assert res.get_json()["devices"] == []


def test_add_device_connects_and_reads_metadata(client, mock_device):
    """新增設備會驗證連線並帶出名稱與型號。"""
    host, port = mock_device
    res = client.post("/api/devices", json={"host": host, "port": port})
    assert res.status_code == 201
    device = res.get_json()["device"]
    assert device["host"] == host
    assert device["port"] == port
    assert device["name"] == "TEST_2201"
    assert device["model"] == "NR-MC2201-WIFI"


def test_add_device_requires_host(client):
    """沒有 host 應回 400。"""
    res = client.post("/api/devices", json={})
    assert res.status_code == 400


def test_add_device_rejects_unreachable_host(client):
    """連線不到應回 502。"""
    res = client.post("/api/devices", json={"host": "127.0.0.1", "port": 1})
    assert res.status_code == 502
    assert res.get_json()["error"]


def test_add_duplicate_device_returns_409(client, mock_device):
    """重複新增應回 409。"""
    host, port = mock_device
    client.post("/api/devices", json={"host": host, "port": port})
    res = client.post("/api/devices", json={"host": host, "port": port})
    assert res.status_code == 409


def test_list_devices_after_add(client, mock_device):
    """新增後列表應有一筆。"""
    host, port = mock_device
    client.post("/api/devices", json={"host": host, "port": port})
    res = client.get("/api/devices")
    devices = res.get_json()["devices"]
    assert len(devices) == 1
    assert devices[0]["name"] == "TEST_2201"


def test_remove_device(client, mock_device):
    """移除設備。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    res = client.delete(f"/api/devices/{device_id}")
    assert res.status_code == 200
    assert client.get("/api/devices").get_json()["devices"] == []


def test_remove_nonexistent_device_returns_404(client):
    """移除不存在的設備應回 404。"""
    assert client.delete("/api/devices/999").status_code == 404


def test_patch_device_name(client, mock_device):
    """更新設備名稱。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    res = client.patch(f"/api/devices/{device_id}", json={"name": "新名稱"})
    assert res.status_code == 200
    assert res.get_json()["device"]["name"] == "新名稱"


def test_patch_rejects_unknown_fields(client, mock_device):
    """不允許更新 host 等關鍵欄位。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    res = client.patch(f"/api/devices/{device_id}", json={"host": "1.2.3.4"})
    assert res.status_code == 400


# ----------------------------------------------------------------------
# 設備狀態
# ----------------------------------------------------------------------

def test_get_device_state(client, mock_device):
    """讀取設備狀態應回連線資訊與 IO 狀態。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]

    res = client.get(f"/api/devices/{device_id}/state")
    assert res.status_code == 200
    device = res.get_json()["device"]
    assert device["connected"] is True
    assert device["state"]["model"] == "NR-MC2201-WIFI"
    assert device["state"]["do"] == {"1": False, "2": False}


def test_get_state_for_unknown_device(client):
    """查詢不存在的設備應回 404。"""
    assert client.get("/api/devices/999/state").status_code == 404


def test_device_list_caches_state(client, mock_device):
    """讀取狀態後，列表應帶有快取。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    client.get(f"/api/devices/{device_id}/state")

    devices = client.get("/api/devices").get_json()["devices"]
    assert devices[0]["connected"] is True
    assert devices[0]["state"]["model"] == "NR-MC2201-WIFI"


# ----------------------------------------------------------------------
# DO 控制
# ----------------------------------------------------------------------

def test_set_do_on(client, mock_device):
    """開啟 DO1。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]

    res = client.post(f"/api/devices/{device_id}/do",
                      json={"channel": 1, "on": True})
    assert res.status_code == 200
    assert res.get_json()["state"]["do"]["1"] is True


def test_set_do_off(client, mock_device):
    """關閉 DO1。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    client.post(f"/api/devices/{device_id}/do",
                json={"channel": 1, "on": True})

    res = client.post(f"/api/devices/{device_id}/do",
                      json={"channel": 1, "on": False})
    assert res.get_json()["state"]["do"]["1"] is False


def test_do_interlock_via_api(client, mock_device):
    """API 層的互鎖驗證：開二檔會自動關掉一檔。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]

    # 先開一檔
    res = client.post(f"/api/devices/{device_id}/do",
                      json={"channel": 1, "on": True})
    assert res.get_json()["state"]["do"]["1"] is True
    assert res.get_json()["state"]["do"]["2"] is False

    # 再開二檔 → 一檔應自動關閉
    res = client.post(f"/api/devices/{device_id}/do",
                      json={"channel": 2, "on": True})
    state = res.get_json()["state"]
    assert state["do"]["2"] is True
    assert state["do"]["1"] is False, "互鎖失效：開 DO2 時 DO1 仍為開"


def test_set_do_all_on(client, mock_device):
    """一次開啟全部通道。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]

    res = client.post(f"/api/devices/{device_id}/all", json={"on": True})
    state = res.get_json()["state"]
    # 互鎖：全部開啟時，實際只有互鎖組的最後一個會保持開啟
    assert any(state["do"].values())


def test_set_do_all_off(client, mock_device):
    """一次關閉全部通道。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    client.post(f"/api/devices/{device_id}/do",
                json={"channel": 1, "on": True})

    res = client.post(f"/api/devices/{device_id}/all", json={"on": False})
    state = res.get_json()["state"]
    assert not any(state["do"].values())


def test_set_do_requires_valid_channel(client, mock_device):
    """channel 不是數字應回 400。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]
    res = client.post(f"/api/devices/{device_id}/do",
                      json={"channel": "abc", "on": True})
    assert res.status_code == 400


def test_set_do_on_unknown_device(client):
    """對不存在的設備寫入應回 404。"""
    res = client.post("/api/devices/999/do",
                      json={"channel": 1, "on": True})
    assert res.status_code == 404


# ----------------------------------------------------------------------
# 掃描
# ----------------------------------------------------------------------

def test_scan_status_initial(client):
    """初始掃描狀態。"""
    res = client.get("/api/scan/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["running"] is False
    assert data["results"] == []


def test_scan_start_with_mock_targets(client, mock_device):
    """★ Mock 模式掃描：指定 targets 可找到模擬設備。"""
    host, port = mock_device
    res = client.post("/api/scan/start", json={
        "targets": [host],
        "port": port,
    })
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    # 等待背景掃描完成
    import time
    for _ in range(100):
        status = client.get("/api/scan/status").get_json()
        if not status["running"]:
            break
        time.sleep(0.1)

    assert status["running"] is False
    assert len(status["results"]) == 1
    assert status["results"][0]["name"] == "TEST_2201"
    assert status["results"][0]["model"] == "NR-MC2201-WIFI"


def test_scan_accepts_targets_as_comma_string(client, mock_device):
    """targets 也可以是逗號分隔的字串。"""
    host, port = mock_device
    res = client.post("/api/scan/start", json={
        "targets": host,
        "port": port,
    })
    assert res.status_code == 200


# ----------------------------------------------------------------------
# AT 指令測試
# ----------------------------------------------------------------------

def test_send_at_by_device_id(client, mock_device):
    """以設備 id 送出 AT 指令。"""
    host, port = mock_device
    created = client.post("/api/devices",
                           json={"host": host, "port": port}).get_json()
    device_id = created["device"]["id"]

    res = client.post("/api/at", json={"id": device_id, "command": "AT"})
    assert res.status_code == 200
    assert res.get_json()["response"] == "OK"


def test_send_at_by_host(client, mock_device):
    """以 host/port 送出 AT 指令。"""
    host, port = mock_device
    res = client.post("/api/at", json={
        "host": host, "port": port, "command": "AT+NAME=?",
    })
    assert res.status_code == 200
    assert "TEST_2201" in res.get_json()["response"]


def test_send_at_requires_command(client):
    """沒有 command 應回 400。"""
    res = client.post("/api/at", json={"host": "127.0.0.1"})
    assert res.status_code == 400


def test_send_at_unreachable_returns_502(client):
    """連線不到應回 502。"""
    res = client.post("/api/at", json={
        "host": "127.0.0.1", "port": 1, "command": "AT",
    })
    assert res.status_code == 502


# ----------------------------------------------------------------------
# 持久化
# ----------------------------------------------------------------------

def test_devices_persist_across_app_instances(store, mock_device):
    """設備清單要能跨程序重啟保留。"""
    host, port = mock_device
    app1 = create_app(store=store)
    app1.test_client().post("/api/devices", json={"host": host, "port": port})

    # 重新載入
    store.load()
    assert len(store) == 1
    assert store.list()[0]["name"] == "TEST_2201"


def test_store_writes_valid_json(store, mock_device):
    """存檔內容必須是合法 JSON。"""
    host, port = mock_device
    create_app(store=store).test_client().post(
        "/api/devices", json={"host": host, "port": port}
    )
    with open(store.path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "devices" in data
    assert len(data["devices"]) == 1


def test_default_store_path_points_to_backend_data_dir():
    """★ 開發模式的預設路徑必須是 backend/data/devices.json。

    這個路徑計算很容易多退一層而指到專案根目錄，
    因此固定用測試保護，避免開發資料散落到錯誤的位置。
    """
    from nrmc_panel.device_store import default_store_path

    path = default_store_path()
    # 在 Windows 開發環境下應指向 backend/data/
    # （容器內會是 /data/devices.json，那個分支由環境決定）
    if not Path("/data").is_dir():
        assert path.name == "devices.json"
        assert path.parent.name == "data"
        assert path.parent.parent.name == "backend"
        # ★ 專案根目錄名稱可能隨版本／分支變動（例如 NR-MC4401-WIFI
        #   改名為 nrmc-panel-addon），所以只驗證它是 backend 的上層目錄，
        #   不綁死特定名稱，避免無關的改名讓測試失敗。
