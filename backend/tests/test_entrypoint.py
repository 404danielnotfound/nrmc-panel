"""Add-on entrypoint 的測試。

驗證 Supervisor 傳入的 options 能被正確解析成應用程式的環境變數。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def entrypoint(monkeypatch):
    """載入 entrypoint 模組。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", json.dumps({}))
    if "entrypoint" in sys.modules:
        del sys.modules["entrypoint"]
    return importlib.import_module("entrypoint")


def test_read_option_with_options_set(entrypoint, monkeypatch):
    """有 options 時正確讀出值。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", json.dumps({
        "at_port": "12345",
        "scan_subnet": "192.168.30",
        "log_level": "debug",
    }))

    assert entrypoint.read_option("at_port") == "12345"
    assert entrypoint.read_option("scan_subnet") == "192.168.30"
    assert entrypoint.read_option("log_level") == "debug"


def test_read_option_missing_key_uses_default(entrypoint, monkeypatch):
    """缺少的 key 回傳預設值。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", json.dumps({"a": "1"}))
    assert entrypoint.read_option("not_exist", "fallback") == "fallback"


def test_read_option_without_env_uses_default(entrypoint, monkeypatch):
    """沒有環境變數時回傳預設值。"""
    monkeypatch.delenv("SUPERVISOR_ADDON_OPTIONS", raising=False)
    assert entrypoint.read_option("at_port", "12345") == "12345"


def test_read_option_handles_invalid_json(entrypoint, monkeypatch):
    """JSON 壞掉時不應該讓容器崩潰。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", "{not valid json")
    assert entrypoint.read_option("at_port", "12345") == "12345"


def test_read_option_converts_booleans(entrypoint, monkeypatch):
    """布林值要轉成字串。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", json.dumps({
        "flag_true": True,
        "flag_false": False,
    }))
    assert entrypoint.read_option("flag_true") == "true"
    assert entrypoint.read_option("flag_false") == "false"


def test_read_option_handles_none_value(entrypoint, monkeypatch):
    """值為 null 時回傳預設值。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", json.dumps({
        "empty": None,
    }))
    assert entrypoint.read_option("empty", "default") == "default"


def test_read_option_converts_numbers(entrypoint, monkeypatch):
    """數字要轉成字串。"""
    monkeypatch.setenv("SUPERVISOR_ADDON_OPTIONS", json.dumps({
        "port": 8080,
    }))
    assert entrypoint.read_option("port") == "8080"
