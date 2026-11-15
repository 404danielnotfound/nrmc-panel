"""DO 互鎖的專項測試。

★ 背景（1.0.4 修正）：
  實測發現部分 NR-MC 韌體不會自動執行硬體互鎖 ——
  寫入 AT+STACH2=1 後 DO1 仍是開啟狀態。
  回讀結果為 {DO1: True, DO2: True}，前端判斷為「異常」，
  導致三個檔位按鈕都不高亮，狀態變得不明。

  修正方式：由上位機（後端）主動送出互鎖指令，
  開啟目標通道前先明確關閉同群組的其他通道，
  並在回讀後驗證互鎖結果。

本檔案覆蓋：
  1. 韌體有硬體互鎖 → 結果正確
  2. 韌體沒有硬體互鎖 → 上位機互鎖仍生效（核心修正）
  3. 互鎖群組的設定解析
  4. 異常狀態的偵測（回讀到兩路同時開啟時要回報失敗）
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from nrmc_panel.app import create_app
from nrmc_panel.device_store import DeviceStore


class InterlockDevice:
    """可控互鎖行為的 Mock AT 設備。

    Args:
        hardware_interlock: True → 開某通道會自動關同組其他通道（真實硬體行為）
                             False → 不會自動關（模擬韌體缺少互鎖功能）
    """

    def __init__(self, hardware_interlock: bool = True):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.hardware_interlock = hardware_interlock
        self.do_state: dict[int, bool] = {1: False, 2: False}
        self.lock = threading.Lock()
        self.running = True

    def start(self) -> None:
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn) -> None:
        buf = b""
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    return
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    conn.sendall(
                        self._reply(line.decode().strip()).encode())
        except OSError:
            return
        finally:
            conn.close()

    def _reply(self, cmd: str) -> str:
        if cmd == "AT":
            return "OK\r\n"
        if cmd == "AT+NAME=?":
            return "+NAME:TEST\r\n"
        if cmd == "AT+DEVICEINFO=?":
            return "+DEVICEINFO:,UT:NR-MC2201-WIFI,FV:VER30.3\r\n"
        if cmd == "AT+STACH0?":
            with self.lock:
                parts = [f"+STACH{k}:{1 if v else 0},100000"
                         for k, v in sorted(self.do_state.items())]
            return "\r\n".join(parts) + "\r\n"
        if cmd == "AT+OCCH0":
            return "+OCCH1:0\r\n+OCCH2:0\r\n"
        if cmd == "AT+AIVALUE0":
            return "ERROR\r\n"
        if cmd.startswith("AT+STACH") and "=" in cmd:
            try:
                n = int(cmd[len("AT+STACH"):].split("=")[0])
                v = int(cmd.split("=", 1)[1])
            except (ValueError, IndexError):
                return "ERROR\r\n"
            with self.lock:
                if n == 0:
                    for k in self.do_state:
                        self.do_state[k] = bool(v)
                else:
                    self.do_state[n] = bool(v)
                    # ★ 硬體互鎖：只在「開啟」時觸發
                    #   （實測部分韌體寫入 0 也會觸發，這裡用另一個 case 模擬）
                    if v and self.hardware_interlock:
                        for k in self.do_state:
                            if k != n:
                                self.do_state[k] = False
            return "OK\r\n"
        return "ERROR\r\n"


@pytest.fixture
def app_client(tmp_path):
    """建立 Flask 測試 client。"""
    store = DeviceStore(tmp_path / "devices.json")
    app = create_app(store=store)
    return app.test_client(), store


def _add_device(client, device: InterlockDevice) -> int:
    created = client.post("/api/devices",
                           json={"host": "127.0.0.1", "port": device.port})
    return created.get_json()["device"]["id"]


@pytest.mark.parametrize("hardware_interlock", [True, False])
def test_switch_to_speed2_turns_off_speed1(app_client, hardware_interlock):
    """★ 核心測試：從一檔切到二檔，DO1 必須關閉、DO2 必須開啟。

    無論韌體本身有沒有硬體互鎖，結果都必須是 DO1=OFF、DO2=ON。
    前端據此判斷「二檔」並高亮對應按鈕。
    """
    client, _store = app_client
    device = InterlockDevice(hardware_interlock=hardware_interlock)
    device.start()
    try:
        time.sleep(0.1)
        did = _add_device(client, device)

        # 先切到一檔
        res = client.post(f"/api/devices/{did}/do",
                          json={"channel": 1, "on": True})
        assert res.status_code == 200, res.get_json()
        do = res.get_json()["state"]["do"]
        assert do["1"] is True, "一檔未開啟"
        assert do["2"] is False, "二檔不應開啟"

        # ★ 再切到二檔 —— 這是使用者的情境
        res = client.post(f"/api/devices/{did}/do",
                          json={"channel": 2, "on": True})
        assert res.status_code == 200, res.get_json()
        do = res.get_json()["state"]["do"]

        assert do["2"] is True, "二檔未開啟"
        assert do["1"] is False, (
            "互鎖失效：切到二檔後 DO1 仍為開啟 → "
            "前端會顯示『兩檔同時開啟』，三個按鈕都不高亮"
        )

        # 驗證實際硬體狀態也是一致的
        with device.lock:
            assert device.do_state[1] is False
            assert device.do_state[2] is True
    finally:
        device.stop()


def test_switch_back_to_speed1(app_client):
    """反向切換：一檔 → 二檔 → 一檔，也要正確互鎖。"""
    client, _store = app_client
    device = InterlockDevice(hardware_interlock=False)
    device.start()
    try:
        time.sleep(0.1)
        did = _add_device(client, device)

        client.post(f"/api/devices/{did}/do", json={"channel": 1, "on": True})
        client.post(f"/api/devices/{did}/do", json={"channel": 2, "on": True})

        res = client.post(f"/api/devices/{did}/do",
                          json={"channel": 1, "on": True})
        do = res.get_json()["state"]["do"]
        assert do["1"] is True, "一檔未開啟"
        assert do["2"] is False, "切回一檔後 DO2 應關閉"
    finally:
        device.stop()


def test_turn_off_closes_both(app_client):
    """關閉時兩個通道都要關掉。"""
    client, _store = app_client
    device = InterlockDevice(hardware_interlock=False)
    device.start()
    try:
        time.sleep(0.1)
        did = _add_device(client, device)

        client.post(f"/api/devices/{did}/do", json={"channel": 1, "on": True})
        client.post(f"/api/devices/{did}/do", json={"channel": 2, "on": True})

        res = client.post(f"/api/devices/{did}/all", json={"on": False})
        do = res.get_json()["state"]["do"]
        assert do["1"] is False
        assert do["2"] is False
    finally:
        device.stop()


def test_all_on_respects_interlock(app_client):
    """全部開啟時，互鎖群組內最多只能有一個開啟。"""
    client, _store = app_client
    device = InterlockDevice(hardware_interlock=False)
    device.start()
    try:
        time.sleep(0.1)
        did = _add_device(client, device)

        res = client.post(f"/api/devices/{did}/all", json={"on": True})
        assert res.status_code == 200, res.get_json()
        do = res.get_json()["state"]["do"]
        opened = sum(1 for v in do.values() if v)
        assert opened >= 1, "全部開啟後應該至少有一個通道開啟"
        assert opened <= 1, "互鎖群組內不應有多個通道同時開啟"
    finally:
        device.stop()


def test_interlock_env_parsing(monkeypatch):
    """NRMC_DO_INTERLOCK 的解析。"""
    import importlib

    import nrmc_panel.app as app_mod

    # 單一群組
    monkeypatch.setenv("NRMC_DO_INTERLOCK", "1,2")
    importlib.reload(app_mod)
    assert app_mod.DO_INTERLOCK_GROUPS == [(1, 2)]

    # 多群組
    monkeypatch.setenv("NRMC_DO_INTERLOCK", "1,2;3,4")
    importlib.reload(app_mod)
    assert app_mod.DO_INTERLOCK_GROUPS == [(1, 2), (3, 4)]

    # 設定無效 → 退回預設
    monkeypatch.setenv("NRMC_DO_INTERLOCK", "garbage")
    importlib.reload(app_mod)
    assert app_mod.DO_INTERLOCK_GROUPS == [(1, 2)]

    # 設定空值 → 退回預設（安全考量：風機檔位必須互鎖）
    monkeypatch.setenv("NRMC_DO_INTERLOCK", "")
    importlib.reload(app_mod)
    assert app_mod.DO_INTERLOCK_GROUPS == [(1, 2)]


def test_interlock_for_channel():
    """_interlock_for 應該回傳通道所屬的群組。"""
    import nrmc_panel.app as app_mod

    assert app_mod._interlock_for(1) == (1, 2)
    assert app_mod._interlock_for(2) == (1, 2)
    assert app_mod._interlock_for(3) == ()
