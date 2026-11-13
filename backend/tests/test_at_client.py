"""AT over TCP 客戶端的測試。

全部使用 Mock 設備，不需要實體硬體。
"""
from __future__ import annotations

import pytest

from nrmc_panel.at_client import AtError, AtTcpClient


# ----------------------------------------------------------------------
# 連線層
# ----------------------------------------------------------------------

def test_connect_to_mock_succeeds(client):
    """應該能連線到 Mock 設備。"""
    assert client.is_connected


def test_connect_to_closed_port_returns_false():
    """連線到未開啟的 port 應回 False（不拋例外）。"""
    c = AtTcpClient("127.0.0.1", 1, timeout=1.0)
    assert c.connect() is False
    assert c.is_connected is False


def test_close_is_idempotent(mock_device):
    """close() 可重複呼叫。"""
    host, port = mock_device
    c = AtTcpClient(host, port)
    c.connect()
    c.close()
    c.close()
    assert not c.is_connected


def test_send_without_connect_raises(mock_device):
    """未連線時 send() 應拋 AtError。"""
    host, port = mock_device
    c = AtTcpClient(host, port)
    with pytest.raises(AtError):
        c.send("AT")


# ----------------------------------------------------------------------
# 基本指令
# ----------------------------------------------------------------------

def test_test_connection_returns_ok(client):
    """AT 應回 OK。"""
    res = client.test_connection()
    assert res.ok
    assert res.response == "OK"
    assert "通訊正常" in res.detail


def test_get_name(client):
    """讀取名稱。"""
    res = client.get_name()
    assert res.ok
    assert res.detail == "TEST_2201"


def test_set_name(client):
    """設定名稱後可讀回。"""
    assert client.set_name("NEW_NAME").ok
    assert client.get_name().detail == "NEW_NAME"


def test_get_device_info(client):
    """讀取型號與韌體版本。"""
    res = client.get_device_info()
    assert res.ok
    assert res.parsed["UT"] == "NR-MC2201-WIFI"
    assert "VER30.3" in res.parsed["FV"]


def test_get_model(client):
    """model 欄位應為型號字串。"""
    assert client.get_model().detail == "NR-MC2201-WIFI"


def test_get_firmware(client):
    """韌體版本應含 VER。"""
    assert "VER" in client.get_firmware().detail


def test_get_wifi_status(client):
    """WiFi 狀態查詢應回覆可解析的描述。"""
    res = client.get_wifi_status()
    assert res.ok
    assert "AP" in res.detail


def test_get_ip(client):
    """IP 查詢。"""
    res = client.get_ip()
    assert res.ok
    assert "192.168.30.250" in res.detail


# ----------------------------------------------------------------------
# IO 指令
# ----------------------------------------------------------------------

def test_get_do_states_parses_two_channels(client):
    """2201 應解析出 2 個 DO 通道。"""
    res = client.get_do_states()
    assert res.ok
    assert res.parsed_do == {1: False, 2: False}


def test_set_do_on_and_read_back(client):
    """寫入 DO1=1 後回讀應一致。"""
    res = client.set_do(1, True)
    assert res.ok
    assert "回讀確認一致" in res.detail
    assert client.get_do_states().parsed_do[1] is True


def test_set_do_off(client):
    """寫入 DO1=0 應關閉。"""
    client.set_do(1, True)
    client.set_do(1, False)
    assert client.get_do_states().parsed_do[1] is False


def test_set_do_all_channels(client):
    """一次開啟所有通道。"""
    res = client.set_do_all(True)
    assert res.ok
    states = client.get_do_states().parsed_do
    assert all(states.values())


def test_do_interlock_verified_through_client(client):
    """互鎖：開 DO2 後開 DO1，DO2 應自動關閉。"""
    client.set_do(2, True)
    assert client.get_do_states().parsed_do[2] is True

    client.set_do(1, True)
    states = client.get_do_states().parsed_do
    assert states[1] is True
    assert states[2] is False, "互鎖失效：開 DO1 時 DO2 仍為開"


def test_get_di_states(client):
    """讀取 DI 通道。"""
    res = client.get_di_states()
    assert res.ok
    assert res.parsed_di == {1: False, 2: False}


def test_get_ai_values_on_2201_fails_gracefully(client):
    """2201 沒有 AI，應該回傳失敗而不是拋例外。"""
    res = client.get_ai_values()
    assert not res.ok
    assert "無 AI 通道" in res.detail


def test_ai_works_on_4401(mock_device_4401):
    """4401 有 AI 通道。"""
    host, port = mock_device_4401
    c = AtTcpClient(host, port, timeout=5.0)
    assert c.connect()
    try:
        res = c.get_ai_values()
        assert res.ok
        assert len(res.parsed_ai) == 4
    finally:
        c.close()


def test_4401_do_has_four_channels(mock_device_4401):
    """4401 應有 4 個 DO 通道。"""
    host, port = mock_device_4401
    c = AtTcpClient(host, port, timeout=5.0)
    assert c.connect()
    try:
        res = c.get_do_states()
        assert len(res.parsed_do) == 4
    finally:
        c.close()


# ----------------------------------------------------------------------
# 綜合查詢
# ----------------------------------------------------------------------

def test_poll_states_connected(client):
    """poll_states 應回傳完整狀態。"""
    state = client.poll_states()
    assert state["connected"] is True
    assert state["model"] == "NR-MC2201-WIFI"
    assert state["name"] == "TEST_2201"
    assert state["do"] == {"1": False, "2": False}
    assert state["error"] == ""


def test_poll_states_disconnected():
    """連線失敗時 poll_states 應回報錯誤而不是拋例外。"""
    c = AtTcpClient("127.0.0.1", 1, timeout=1.0)
    state = c.poll_states()
    assert state["connected"] is False
    assert state["error"] != ""


def test_at_result_to_dict_is_json_serializable(client):
    """AtResult.to_dict() 應可 JSON 序列化。"""
    import json

    res = client.get_do_states()
    json.dumps(res.to_dict())   # 不應拋例外
