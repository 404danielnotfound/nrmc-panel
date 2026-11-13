"""網段掃描模組的測試。

使用 Mock 模式（指定 targets 清單）來驗證掃描流程，
不需要真實網路。
"""
from __future__ import annotations

import pytest

from nrmc_panel import scanner


# ----------------------------------------------------------------------
# IP 換算工具
# ----------------------------------------------------------------------

def test_ip_to_int_roundtrip():
    """IP 與整數之間的換算要一致。"""
    assert scanner._ip_to_int("192.168.30.250") == 0xC0A81EFA
    assert scanner._int_to_ip(scanner._ip_to_int("192.168.30.250")) == \
        "192.168.30.250"


def test_ip_to_int_handles_three_segments():
    """三段式的 IP 也要能換算。"""
    assert scanner._int_to_ip(scanner._ip_to_int("192.168.30")) == "192.168.30.0"


def test_parse_cidr_returns_prefix_and_host_bits():
    """由 IP 與遮罩推導網段。"""
    prefix, bits = scanner.parse_cidr("192.168.30.250", "255.255.255.0")
    assert prefix == "192.168.30"
    assert bits == 8


def test_parse_cidr_handles_16():
    """/16 遮罩應推導出 16 個主機位元。

    前綴在遇到第一個 0 位元組時截斷，因此 /16 只會給到 "192.168"。
    """
    prefix, bits = scanner.parse_cidr("192.168.30.250", "255.255.0.0")
    assert prefix == "192.168"
    assert bits == 16


@pytest.mark.parametrize("subnet,expect_prefix,expect_bits", [
    ("192.168.30", "192.168.30", 8),
    ("192.168.30.0", "192.168.30", 8),
    ("192.168.30.0/24", "192.168.30", 8),
    ("192.168.0.0/16", "192.168.0", 16),
])
def test_parse_subnet_variants(subnet, expect_prefix, expect_bits):
    """支援多種網段寫法。"""
    prefix, bits = scanner._parse_subnet(subnet)
    assert prefix == expect_prefix
    assert bits == expect_bits


# ----------------------------------------------------------------------
# OUI 判定
# ----------------------------------------------------------------------

@pytest.mark.parametrize("mac,expected", [
    ("4E:26:09:11:22:33", True),
    ("4E:14:01:AA:BB:CC", True),
    ("4e-26-09-11-22-33", True),
    ("00:11:22:33:44:55", False),
    ("", False),
])
def test_is_oui_known(mac, expected):
    """已知廠商 OUI 判定。"""
    assert scanner.is_oui_known(mac) is expected


# ----------------------------------------------------------------------
# Mock 模式掃描
# ----------------------------------------------------------------------

def test_discover_with_targets_finds_mock_device(mock_device):
    """指定 targets 時應能找到 Mock 設備。"""
    host, port = mock_device
    results = scanner.discover(targets=[host], at_port=port)
    assert len(results) == 1
    assert results[0]["ip"] == host
    assert results[0]["name"] == "TEST_2201"
    assert results[0]["model"] == "NR-MC2201-WIFI"
    assert results[0]["is_device"] is True


def test_discover_with_targets_multiple_devices(mock_device, mock_device_4401):
    """多台設備都能被掃到（使用多個候選 port）。

    兩台 Mock 都在 127.0.0.1，但 port 不同，
    掃描結果應該是兩筆各自獨立的記錄。
    """
    host1, port1 = mock_device
    host2, port2 = mock_device_4401
    results = scanner.discover(
        targets=[host1],
        candidate_ports=(port1, port2),
    )
    names = {r["name"] for r in results}
    assert names == {"TEST_2201", "TEST_4401"}
    assert all(r["is_device"] for r in results)
    assert {r["at_port"] for r in results} == {port1, port2}


def test_discover_reports_correct_at_port(mock_device):
    """應記錄實際回應的 port。"""
    host, port = mock_device
    results = scanner.discover(targets=[host], candidate_ports=(port,))
    assert results[0]["at_port"] == port


def test_discover_reports_progress(mock_device):
    """掃描應該回報進度。"""
    host, port = mock_device
    calls: list[tuple[int, int]] = []
    scanner.discover(targets=[host], at_port=port,
                     progress=lambda done, total: calls.append((done, total)))
    assert calls, "沒有收到任何進度回呼"


def test_discover_calls_log_callback(mock_device):
    """掃描應該輸出日誌。"""
    host, port = mock_device
    messages: list[str] = []
    scanner.discover(targets=[host], at_port=port, log=messages.append)
    assert messages
    assert any("Mock 模式" in m for m in messages)


def test_discover_unknown_host_returns_empty_result():
    """查詢不到的主機應該回空名稱而非例外。"""
    results = scanner.discover(targets=["127.0.0.1"], at_port=1)
    assert len(results) == 1
    assert results[0]["name"] == ""
    assert results[0]["is_device"] is False


def test_discover_empty_targets():
    """空清單不應該出錯。"""
    results = scanner.discover(targets=[])
    assert results == []


def test_query_at_name_returns_name_and_model(mock_device):
    """單獨查詢名稱與型號。"""
    host, port = mock_device
    name, model = scanner.query_at_name(host, port)
    assert name == "TEST_2201"
    assert model == "NR-MC2201-WIFI"


def test_query_at_name_handles_failure():
    """連線失敗時回空字串。"""
    name, model = scanner.query_at_name("127.0.0.1", 1, timeout=1.0)
    assert name == ""
    assert model == ""


# ----------------------------------------------------------------------
# 真實網路探測（僅驗證不崩潰）
# ----------------------------------------------------------------------

def test_read_arp_returns_dict():
    """讀 ARP 表格不應拋例外。"""
    table = scanner.read_arp()
    assert isinstance(table, dict)


def test_get_local_ip_returns_valid_ipv4():
    """取得本機 IP 應回合法 IPv4。"""
    ip = scanner.get_local_ip()
    assert ip.count(".") == 3
    assert all(0 <= int(p) <= 255 for p in ip.split("."))


def test_tcp_probe_detects_open_port(mock_device):
    """TCP 探測應該找出開啟的 port。"""
    host, port = mock_device
    open_ports = scanner.tcp_probe(host, [port], timeout=1.0)
    assert port in open_ports


def test_tcp_probe_is_only_a_prefilter(mock_device):
    """tcp_probe 只是握手過濾，不代表上面真的有 NR-MC 設備。

    真正的確認由 query_at_name 完成（送出 AT 指令並解析回應）。
    """
    host, port = mock_device
    # 對一個沒有服務的 port，query_at_name 應該回空結果
    name, model = scanner.query_at_name(host, port + 1, timeout=1.0)
    assert name == ""
    assert model == ""


def test_query_at_name_confirms_real_device(mock_device):
    """query_at_name 才是權威的裝置確認方式。"""
    host, port = mock_device
    name, model = scanner.query_at_name(host, port)
    assert name == "TEST_2201"
    assert model == "NR-MC2201-WIFI"
