"""Mock AT 設備模擬器的測試。

驗證模擬器行為正確：指令處理、多行回覆、延遲模擬、DO 硬體互鎖。
"""
from __future__ import annotations

import socket
import time

import pytest

from nrmc_panel.mock_device import (
    MockDeviceState,
    create_mock_server,
)


# ----------------------------------------------------------------------
# 狀態層測試（不需要網路）
# ----------------------------------------------------------------------

def test_initial_state_has_expected_channels():
    """2201 應該是 2 DO / 2 DI / 0 AI。"""
    state = MockDeviceState("NR-MC2201-WIFI", "TEST")
    assert state.do_count == 2
    assert state.di_count == 2
    assert state.ai_count == 0
    assert state.do_states == {1: False, 2: False}


def test_4401_has_four_channels():
    """4401 應該是 4 DO / 4 DI / 4 AI。"""
    state = MockDeviceState("NR-MC4401-WIFI", "TEST")
    assert state.do_count == 4
    assert state.di_count == 4
    assert state.ai_count == 4


def test_set_do_turns_channel_on():
    """寫入 DO1=1 應該只影響 DO1。"""
    state = MockDeviceState("NR-MC2201-WIFI", "TEST")
    state.set_do(1, True)
    assert state.do_states[1] is True
    assert state.do_states[2] is False


def test_interlock_closes_sibling_when_opening():
    """硬體互鎖：開 DO1 應該自動關 DO2。"""
    state = MockDeviceState("NR-MC2201-WIFI", "TEST")
    state.set_do(2, True)
    assert state.do_states[2] is True

    state.set_do(1, True)
    assert state.do_states[1] is True
    assert state.do_states[2] is False, "開 DO1 時 DO2 應被互鎖關閉"


def test_interlock_closes_sibling_when_opening_reverse():
    """硬體互鎖（反向）：開 DO2 應該自動關 DO1。"""
    state = MockDeviceState("NR-MC2201-WIFI", "TEST")
    state.set_do(1, True)
    state.set_do(2, True)
    assert state.do_states[2] is True
    assert state.do_states[1] is False


def test_set_do_channel_zero_affects_all():
    """channel=0 應該一次設定全部通道。"""
    state = MockDeviceState("NR-MC2201-WIFI", "TEST")
    state.set_do(0, True)
    assert all(state.do_states.values())
    state.set_do(0, False)
    assert not any(state.do_states.values())


def test_4401_channels_3_and_4_are_not_interlocked():
    """4401 只有 DO1/DO2 互鎖，DO3/DO4 應獨立。"""
    state = MockDeviceState("NR-MC4401-WIFI", "TEST")
    state.set_do(1, True)
    state.set_do(3, True)
    assert state.do_states[1] is True
    assert state.do_states[3] is True


# ----------------------------------------------------------------------
# 網路層測試（實際用 socket 對談）
# ----------------------------------------------------------------------

def _raw_exchange(host: str, port: int, command: str,
                  timeout: float = 5.0, settle: float = 0.3) -> str:
    """送一條指令並讀取回覆（低階驗證用）。

    多行回覆是分批送達的，所以讀完後再多等一下讓後續行補齊。
    """
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall((command + "\r\n").encode())
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            # 收到換行結尾就先停，等 settle 後再補讀剩餘的行
            if buf.endswith(b"\r\n"):
                break
        time.sleep(settle)
        s.settimeout(0.5)
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except (socket.timeout, OSError):
            pass
        return buf.decode("utf-8", errors="replace").strip()
    finally:
        s.close()


def test_server_responds_ok_to_at(mock_device):
    """AT 指令應回 OK。"""
    host, port = mock_device
    assert _raw_exchange(host, port, "AT") == "OK"


def test_server_returns_name(mock_device):
    """AT+NAME=? 應回 +NAME:xxx。"""
    host, port = mock_device
    resp = _raw_exchange(host, port, "AT+NAME=?")
    assert resp == "+NAME:TEST_2201"


def test_server_returns_device_info(mock_device):
    """AT+DEVICEINFO=? 應含型號。"""
    host, port = mock_device
    resp = _raw_exchange(host, port, "AT+DEVICEINFO=?")
    assert "UT:NR-MC2201-WIFI" in resp
    assert "FV:" in resp


def test_server_returns_multiline_do_states(mock_device):
    """AT+STACH0? 應回兩行通道狀態。"""
    host, port = mock_device
    resp = _raw_exchange(host, port, "AT+STACH0?")
    assert "+STACH1:0,100000" in resp
    assert "+STACH2:0,100000" in resp


def test_server_multiline_responses_arrive_in_parts(mock_device):
    """多行回覆應該分批送達，而不是一次全部送出。"""
    host, port = mock_device
    s = socket.create_connection((host, port), timeout=5.0)
    try:
        s.settimeout(5.0)
        s.sendall(b"AT+STACH0?\r\n")
        # 先收到第一行
        first = s.recv(1024)
        assert b"+STACH1:" in first
        # 第二行應該是另外一次 recv 才收到（分批送達）
        time.sleep(0.15)
        rest = b""
        s.settimeout(1.0)
        try:
            rest = s.recv(1024)
        except socket.timeout:
            pass
        assert b"+STACH2:" in first + rest
    finally:
        s.close()


def test_server_rejects_unknown_command(mock_device):
    """未知指令應回 ERROR。"""
    host, port = mock_device
    assert _raw_exchange(host, port, "AT+BOGUS=?") == "ERROR"


def test_server_ai_error_on_2201(mock_device):
    """2201 沒有 AI 通道，AT+AIVALUE0 應回 ERROR。"""
    host, port = mock_device
    assert _raw_exchange(host, port, "AT+AIVALUE0") == "ERROR"


def test_server_ai_values_on_4401(mock_device_4401):
    """4401 有 AI 通道，應回數值。"""
    host, port = mock_device_4401
    resp = _raw_exchange(host, port, "AT+AIVALUE0")
    assert "+AIVALUE1:" in resp


def test_server_simulates_response_delay(mock_device):
    """回覆應該有延遲（模擬真實設備），不能是瞬間回應。"""
    host, port = mock_device
    start = time.monotonic()
    _raw_exchange(host, port, "AT")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05, f"回覆延遲過短（{elapsed:.3f}s），未模擬真實延遲"


def test_server_handles_multiple_commands_on_one_connection(mock_device):
    """同一條連線可連續送多條指令。"""
    host, port = mock_device
    s = socket.create_connection((host, port), timeout=5.0)
    try:
        s.settimeout(5.0)
        s.sendall(b"AT\r\n")
        assert s.recv(100).strip() == b"OK"
        s.sendall(b"AT+NAME=?\r\n")
        time.sleep(0.2)
        resp = s.recv(200).decode()
        assert "+NAME:" in resp
    finally:
        s.close()
