"""共用測試 fixture。

提供 Mock AT 設備伺服器，讓測試可以在沒有實體設備的情況下
驗證 AT over TCP 的完整行為。
"""
from __future__ import annotations

import threading

import pytest

from nrmc_panel.mock_device import create_mock_server


@pytest.fixture
def mock_device():
    """啟動一個 Mock AT 設備，回傳 (host, port) tuple。"""
    server = create_mock_server(
        host="127.0.0.1", port=0, name="TEST_2201", model="NR-MC2201-WIFI"
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
def mock_device_4401():
    """啟動一個 4 通道的 Mock AT 設備。"""
    server = create_mock_server(
        host="127.0.0.1", port=0, name="TEST_4401", model="NR-MC4401-WIFI"
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
def client(mock_device):
    """建立一個已連線的 AtTcpClient。"""
    from nrmc_panel.at_client import AtTcpClient

    host, port = mock_device
    c = AtTcpClient(host, port, timeout=5.0)
    assert c.connect(), "無法連線到 Mock 設備"
    try:
        yield c
    finally:
        c.close()
