"""AT over TCP 通訊客戶端。

移植自 `AT批次設定工具/net_control_page.py` 的 `AtTcpClient` 與 `AtResult`，
移除所有 Tkinter 依賴，保留實測驗證過的 idle-timeout 收發策略。

實測基準（NR-MC2201-WIFI, VER30.3, 192.168.30.250:12345）：
    AT          first_byte=0.647s  total=0.958s  data='OK\\r\\n'
    AT+STACH0?  first_byte=0.083s  total=0.439s  data='+STACH1:0,100000\\r\\n+STACH2:0,100000'
    AT+OCCH0    first_byte=0.057s  total=0.416s  data='+OCCH1:0\\r\\n+OCCH2:0'

關鍵設計：
    1. 查詢指令沒有 OK 結尾，不能靠「看到 OK 就停」判斷結束
    2. 多行回覆是分批送達的，全部收完約需 0.4 秒
    3. 因此採 idle timeout：收到資料後，連續 quiet_ms 沒新資料就視為回覆結束
"""
from __future__ import annotations

import re
import select
import socket
import threading
import time
from dataclasses import dataclass, field

# AT 指令必須以回車換行結尾
TERM = "\r\n"

# 設備 DO / DI 通道數上限（NR-MC2201-WIFI 各 2 個，NR-MC4401-WIFI 各 4 個）
MAX_DO_CHANNELS = 4
MAX_DI_CHANNELS = 4


class AtError(Exception):
    """AT 通訊例外（連線層級的失敗）。"""


@dataclass
class AtResult:
    """AT 指令結果。

    Attributes:
        ok: 指令是否成功
        command: 送出的指令
        response: 設備原始回覆
        detail: 人類可讀的說明
        parsed_do: 解析出的 DO 通道狀態 {通道: 是否開啟}
        parsed_di: 解析出的 DI 通道狀態
        parsed_ai: 解析出的 AI 數值
        parsed: 其他結構化資料
    """

    ok: bool
    command: str
    response: str = ""
    detail: str = ""
    parsed_do: dict = field(default_factory=dict)
    parsed_di: dict = field(default_factory=dict)
    parsed_ai: dict = field(default_factory=dict)
    parsed: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"[{'OK ' if self.ok else 'ERR'}] {self.command} -> "
            f"{self.response or '(無回覆)'} {self.detail}"
        )

    def to_dict(self) -> dict:
        """轉成可 JSON 序列化的字典。"""
        return {
            "ok": self.ok,
            "command": self.command,
            "response": self.response,
            "detail": self.detail,
            "parsed_do": {str(k): v for k, v in self.parsed_do.items()},
            "parsed_di": {str(k): v for k, v in self.parsed_di.items()},
            "parsed_ai": {str(k): v for k, v in self.parsed_ai.items()},
            "parsed": self.parsed,
        }

    def _with_parsed(self, data: dict) -> "AtResult":
        """附加通用結構化資料後回傳自身（供鏈式呼叫使用）。"""
        self.parsed = data
        return self


class AtTcpClient:
    """透過 TCP 與 NR-MC 設備通訊的 AT 指令客戶端。

    設備端設定需求（AT 指令手冊 V3.2）：
        AT+PROTOCOL=0   資料協議用 AT 指令（不要 MQTT）
        AT+MODEL=0      TCP_SERVER 模式，由設備開 port 等上位機連線
        AT+PORT=<n>     監聽 port（不可用 5000，該 port 內部使用）

    實測（NR-MC2201-WIFI, VER30.3, IP 192.168.30.250, port 12345）：
        - 單一 TCP 連線可連續送多條指令並各自收到回覆
        - 查詢全部通道（AT+STACH0? / AT+OCCH0）會回多行

    用法：
        >>> client = AtTcpClient("192.168.30.250", 12345)
        >>> if client.connect():
        ...     print(client.get_name().detail)
        ...     client.close()
    """

    def __init__(
        self,
        host: str,
        port: int = 12345,
        timeout: float = 3.0,
        quiet_ms: int = 150,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.quiet_ms = quiet_ms
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        # ★ P1-1：最後一次使用時間（連線池用來判斷閒置多久可回收）
        self.last_used_at = time.monotonic()
        # ★ 操作耗時記錄：每次 send / connect 會 append
        #   {"cmd": ..., "ms": ...}，供 /do 回傳 timings 做瓶頸分析。
        self.op_timings: list[dict] = []
        # ★ P0-3：這個型號有沒有 AI 通道？
        #   None = 還不知道（下次會試一次）；True/False = 已確認。
        #   確認沒有之後就不再送 AT+AIVALUE0，省一趟指令。
        self._ai_supported: bool | None = None
        # ★ P1-2：最近一次 DO 回讀的結果。
        #   set_do() 回讀完會把結果放這裡，poll_states() 直接複用，
        #   避免同一次操作送兩次 AT+STACH0?（每次約 440ms）。
        self.last_do_states: dict[int, bool] | None = None

    def clear_timings(self) -> None:
        """清空本輪操作的耗時記錄（每次 HTTP 請求開始時呼叫）。"""
        self.op_timings = []

    # -- 連線 -------------------------------------------------------------

    def connect(self) -> bool:
        """建立 TCP 連線。

        Returns:
            True 表示連線成功，False 表示失敗（不拋例外，方便 UI 處理）。
        """
        t0 = time.monotonic()
        self.close()
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError:
            self._sock = None
            self.op_timings.append({"cmd": "<connect>",
                                     "ms": round((time.monotonic() - t0) * 1000, 1),
                                     "reused": False, "ok": False})
            return False
        s.settimeout(self.timeout)
        # 關閉 Nagle 演算法，降低小封包的延遲（AT 指令都很短）
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s
        self._buf.clear()
        self.last_used_at = time.monotonic()
        self.op_timings.append({"cmd": "<connect>",
                                 "ms": round((time.monotonic() - t0) * 1000, 1),
                                 "reused": False, "ok": True})
        return True

    def mark_reused(self) -> None:
        """連線池命中時記一筆 0ms 的 connect，方便瀑布圖看出省了多少。"""
        self.op_timings.append({"cmd": "<connect>",
                                 "ms": 0.0, "reused": True, "ok": True})

    def close(self) -> None:
        """關閉連線（可重複呼叫）。"""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf.clear()

    @property
    def is_connected(self) -> bool:
        """目前是否處於已連線狀態。"""
        return self._sock is not None

    def __enter__(self) -> "AtTcpClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- 收發 -------------------------------------------------------------

    def _recv_until_quiet(self, quiet_ms: int | None = None,
                          max_wait: float | None = None,
                          early_ok: bool = False) -> str:
        """讀取回覆，直到「閒置」一小段時間就返回。

        這是 AT over TCP 的核心策略。查詢指令沒有 OK 結尾，若等待 OK
        會永遠卡住，因此改用 idle timeout 判斷回覆結束。

        Args:
            early_ok: 寫入類指令（回覆只有單行 OK/ERROR）可提前返回，
                不用等滿 quiet_ms。這是檔位切換最大的加速來源。
        """
        if self._sock is None:
            raise AtError("尚未連線")

        quiet = (self.quiet_ms if quiet_ms is None else quiet_ms) / 1000
        limit = self.timeout if max_wait is None else max_wait
        deadline = time.monotonic() + limit
        idle_deadline = None   # 收到第一個資料後才開始算 idle

        while time.monotonic() < deadline:
            if idle_deadline is None:
                wait = min(0.05, max(0.0, deadline - time.monotonic()))
            else:
                wait = min(0.02, max(0.0, idle_deadline - time.monotonic()))

            try:
                ready, _, _ = select.select([self._sock], [], [], wait)
            except (OSError, ValueError):
                break
            if not ready:
                if idle_deadline is not None and time.monotonic() >= idle_deadline:
                    break
                continue

            try:
                data = self._sock.recv(4096)
            except (socket.timeout, OSError):
                break
            if not data:
                break
            self._buf.extend(data)
            # ★ 寫入類指令加速：回覆只有單行 OK / ERROR，
            #   一收到就直接返回，不用等滿 quiet_ms。
            #   實測這一段省下 ~150ms（一次 quiet 視窗）。
            if early_ok:
                text = bytes(self._buf).decode("utf-8",
                                               errors="replace").strip().upper()
                if text == "OK" or text.startswith("OK\r") \
                        or text.startswith("OK\n") or text == "ERROR" \
                        or text.startswith("ERROR\r") \
                        or text.startswith("ERROR\n"):
                    break
            idle_deadline = time.monotonic() + quiet

        return bytes(self._buf).decode("utf-8", errors="replace").strip()

    def send(self, command: str, quiet_ms: int | None = None,
             max_wait: float | None = None, early_ok: bool = False) -> str:
        """送出一行 AT 指令，回傳回覆文字（可能多行）。

        Args:
            early_ok: 寫入類指令（回覆只有單行 OK/ERROR）收到後立刻返回，
                不用等滿 quiet_ms。查詢類指令必須保持 False。

        Raises:
            AtError: 尚未連線或送出失敗。
        """
        if self._sock is None:
            raise AtError("尚未連線")
        with self._lock:
            self._buf.clear()
            self.last_used_at = time.monotonic()
            t0 = time.monotonic()
            try:
                self._sock.sendall(
                    (command + TERM).encode("utf-8", errors="replace")
                )
            except OSError as exc:
                self.close()
                raise AtError(f"送出失敗：{exc}") from exc
            try:
                return self._recv_until_quiet(quiet_ms, max_wait,
                                              early_ok=early_ok)
            finally:
                ms = round((time.monotonic() - t0) * 1000, 1)
                self.op_timings.append({"cmd": command, "ms": ms})

    # -- 基本指令 ---------------------------------------------------------

    def test_connection(self) -> AtResult:
        """通訊測試（送 AT）。"""
        resp = self.send("AT")
        ok = resp.upper().startswith("OK")
        return AtResult(ok, "AT", resp, "通訊正常" if ok else "未收到 OK")

    def get_name(self) -> AtResult:
        """讀取設備名稱（detail 為純名稱字串，便於 UI 直接顯示）。"""
        resp = self.send("AT+NAME=?")
        m = re.search(r"\+NAME:(.*)", resp)
        name = m.group(1).strip() if m else ""
        return AtResult(bool(name), "AT+NAME=?", resp, name)

    def set_name(self, name: str) -> AtResult:
        """設定設備名稱（最多 16 bytes）。"""
        resp = self.send(f"AT+NAME={name}")
        ok = resp.upper().startswith("OK")
        return AtResult(ok, f"AT+NAME={name}", resp,
                        "設定成功" if ok else "設定失敗")

    def get_device_info(self) -> AtResult:
        """讀取完整設備資訊（含型號與韌體版本）。

        回覆格式：+DEVICEINFO:,UT:NR-MC2201-WIFI,FV:NR-MC2201-WIFI-VER30.3
        """
        resp = self.send("AT+DEVICEINFO=?")
        info: dict[str, str] = {}
        for m in re.finditer(r"([A-Za-z]+):([^,\r\n]*)", resp):
            info[m.group(1).upper()] = m.group(2).strip()
        model = info.get("UT", "")
        return AtResult(
            bool(model),
            "AT+DEVICEINFO=?",
            resp,
            f"型號：{model}" if model else "無法解析",
        )._with_parsed(info)

    def get_model(self) -> AtResult:
        """讀取型號（回傳 detail 為型號字串，便於 UI 直接顯示）。"""
        res = self.get_device_info()
        model = res.parsed.get("UT", "")
        res.detail = model
        return res

    def get_firmware(self) -> AtResult:
        """讀取韌體版本。"""
        res = self.get_device_info()
        fw = res.parsed.get("FV", "")
        res.detail = fw
        return res

    def get_wifi_status(self) -> AtResult:
        """查詢 WiFi 連線狀態。"""
        resp = self.send("AT+WSLK?")
        m = re.search(r"\+WSLK:(\d+),(\d+),(\d+),(\d+)", resp)
        if not m:
            return AtResult(False, "AT+WSLK=?", resp, "無法解析")
        status, rssi = int(m.group(3)), int(m.group(4))
        detail = (f"已關聯 AP，訊號 {rssi}/31" if status == 1
                  else f"尚未關聯 AP，rssi={rssi}/31")
        return AtResult(True, "AT+WSLK=?", resp, detail)

    def get_ip(self) -> AtResult:
        """查詢設備取得的 IP。"""
        resp = self.send("AT+WANN?")
        m = re.search(r"\+WANN:(\d+),([\d.]+),([\d.]+),([\d.]+)", resp)
        if not m:
            return AtResult(False, "AT+WANN=?", resp, "無法解析")
        ip = m.group(2)
        detail = f"IP：{ip}" if ip != "0.0.0.0" else "IP：尚未取得（0.0.0.0）"
        return AtResult(True, "AT+WANN=?", resp, detail)

    # -- IO ---------------------------------------------------------------

    def get_do_states(self, quiet_ms: int | None = None) -> AtResult:
        """讀取所有 DO 通道狀態。

        實測回覆為多行：
            +STACH1:0,100000
            +STACH2:0,100000
        """
        resp = self.send("AT+STACH0?", quiet_ms=quiet_ms)
        states: dict[int, bool] = {}
        for m in re.finditer(r"\+STACH(\d+):(\d+)", resp):
            states[int(m.group(1))] = (m.group(2) == "1")
        res = AtResult(
            bool(states),
            "AT+STACH0?",
            resp,
            f"DO 數量：{len(states)}" if states else "無法解析",
        )
        res.parsed_do = states
        return res

    def set_do(self, channel: int, on: bool,
             verify: bool = True, read_quiet_ms: int = 120) -> AtResult:
        """設定單一 DO 通道。

        Args:
            channel: 通道編號（1 起算）。0 表示全部通道。
            on: True 開啟、False 關閉。
            verify: 是否回讀確認（檔位切換一定要開，確保互鎖結果正確）。
            read_quiet_ms: 回讀查詢的 quiet 視窗。寫入本身用 early_ok
                提前返回，回讀仍需等完整多行回覆。

        寫入後會回讀確認，確保實際狀態與指令一致。

        ★ P1-2：回讀結果會存進 self.last_do_states，
          讓呼叫端（poll_states）不必再送一次 AT+STACH0?。
          舊流程在一次切檔中送兩次 AT+STACH0?（各約 440ms），
          這是切檔路徑上最大的一段浪費。
        """
        cmd = f"AT+STACH{channel}={1 if on else 0}"
        # ★ 寫入只回單行 OK，用 early_ok 不必等滿 quiet 視窗（省 ~150ms）
        resp = self.send(cmd, early_ok=True)
        ok = resp.upper().startswith("OK")
        detail = ""
        if ok and verify:
            back = self.get_do_states(quiet_ms=read_quiet_ms)
            # ★ 把回讀結果記下來，供 poll_states 複用
            self.last_do_states = dict(back.parsed_do)
            if not back.ok:
                detail = "寫入成功，但回讀失敗"
            elif channel == 0:
                detail = "已送出（全部通道）"
            else:
                got = back.parsed_do.get(channel)
                if got is None:
                    detail = "寫入成功，但回讀不到該通道"
                elif got != on:
                    ok = False
                    detail = f"回讀 DO{channel} = {got}，與設定值不符"
                else:
                    detail = "回讀確認一致"
        return AtResult(ok, cmd, resp, detail)

    def set_do_all(self, on: bool) -> AtResult:
        """設定所有 DO 通道（channel=0）。"""
        return self.set_do(0, on)

    def get_di_states(self) -> AtResult:
        """讀取所有 DI 通道狀態（多行）。

        實測回覆：+OCCH1:0 / +OCCH2:1
        """
        resp = self.send("AT+OCCH0")
        states: dict[int, bool] = {}
        for m in re.finditer(r"\+OCCH(\d+):(\d+)", resp):
            states[int(m.group(1))] = (m.group(2) == "1")
        res = AtResult(
            bool(states),
            "AT+OCCH0",
            resp,
            f"DI 數量：{len(states)}" if states else "無法解析",
        )
        res.parsed_di = states
        return res

    def get_ai_values(self) -> AtResult:
        """讀取 AI 模擬量。

        NR-MC2201-WIFI 沒有 AI 通道，設備會回 ERROR，這是正常情況。
        """
        resp = self.send("AT+AIVALUE0")
        if resp.upper().startswith("ERROR"):
            return AtResult(False, "AT+AIVALUE0", resp, "此型號無 AI 通道")
        values: dict[int, float] = {}
        for m in re.finditer(r"\+AIVALUE(\d+):([-\d.]+)", resp):
            values[int(m.group(1))] = float(m.group(2))
        res = AtResult(
            bool(values),
            "AT+AIVALUE0",
            resp,
            f"AI 數量：{len(values)}" if values else "無法解析",
        )
        res.parsed_ai = values
        return res

    # -- 綜合查詢 ---------------------------------------------------------

    def poll_states(self, quick: bool = False,
                    include_di: bool = True) -> dict:
        """一次取得所有可用的 IO 狀態。

        這是 UI 顯示「連線狀態 + DO/DI 狀態」的單一入口。
        任何子查詢失敗都不會中斷整體流程。

        ★ 本方法會自行建立連線（若尚未連線），因此呼叫端不需要
          先呼叫 connect()，讓「取得狀態」成為一個自洽的操作。

        Args:
            quick: True 只回讀 DO + DI（給寫入後的快速刷新用）。
                跳過 AT 測試、名稱／型號／韌體、AI 查詢。
                寫入路徑本來就已經連上且驗證過，不需要再花
                5 條 AT 去問不會變的靜態資訊。
            include_di: False 時連 DI 都不查（只回讀 DO）。
                ★ 這是切檔最快的路徑：DO 回讀本來就要 ~440ms，
                  再加一條 AT+OCCH0 又要 ~440ms，而 DI 狀態在
                  切檔時根本不會變（DI 是外部輸入，不受 DO 影響）。
                  實測這樣可以再省掉約 440ms。

        Returns:
            dict 含 connected / model / name / do / di / ai / error
        """
        result: dict = {
            "connected": False,
            "name": "",
            "model": "",
            "firmware": "",
            "do": {},
            "di": {},
            "ai": {},
            "error": "",
        }

        # 尚未連線時先建立連線
        if not self.is_connected:
            try:
                if not self.connect():
                    result["error"] = f"無法連線到 {self.host}:{self.port}"
                    return result
            except OSError as exc:
                result["error"] = f"無法連線到 {self.host}:{self.port}：{exc}"
                return result

        # ★ quick 模式（寫入後的刷新）：跳過 AT 握手與靜態資訊查詢，
        #   只回讀 DO（+ DI，除非指定不查）。
        #   名稱／型號／韌體在寫入前已經驗證過，不會在一次切檔中改變。
        if quick:
            result["connected"] = True
            # ★ P1-2：set_do() 剛剛已經回讀過 DO 了，直接複用，
            #   不要再送一次 AT+STACH0?（每次約 440ms）。
            if self.last_do_states is not None:
                result["do"] = {str(k): v
                                for k, v in self.last_do_states.items()}
            else:
                try:
                    res = self.get_do_states()
                    result["do"] = {str(k): v for k, v in
                                    res.parsed_do.items()}
                except AtError:
                    pass
            if include_di:
                try:
                    res = self.get_di_states()
                    result["di"] = {str(k): v for k, v in
                                    res.parsed_di.items()}
                except AtError:
                    pass
            # ★ P0-3：已確認沒有 AI 通道的型號，quick 模式直接跳過
            if self._ai_supported:
                try:
                    res = self.get_ai_values()
                    result["ai"] = {str(k): v for k, v in
                                    res.parsed_ai.items()}
                except AtError:
                    pass
            return result

        try:
            test = self.test_connection()
        except AtError as exc:
            result["error"] = str(exc)
            return result

        if not test.ok:
            result["error"] = "通訊測試未收到 OK"
            return result

        result["connected"] = True

        for key, fn in (
            ("name", self.get_name),
        ):
            try:
                result[key] = fn().detail
            except AtError:
                pass

        # ★ P0-2：model 與 firmware 都來自同一份 AT+DEVICEINFO=?
        #   回覆。以前 get_model() 與 get_firmware() 各自送一次指令，
        #   等於同一份資料送了兩趟（每次約 450ms）。這裡只送一次。
        try:
            info = self.get_device_info()
            result["model"] = info.parsed.get("UT", "")
            result["firmware"] = info.parsed.get("FV", "")
        except AtError:
            pass

        for key, fn in (("do", self.get_do_states),
                        ("di", self.get_di_states)):
            try:
                res = fn()
                result[key] = {str(k): v for k, v in
                               (res.parsed_do if key == "do"
                                else res.parsed_di).items()}
            except AtError:
                pass

        # ★ P0-3：AI 通道只有部分型號有（NR-MC4401 有，NR-MC2201 沒有）。
        #   對沒有 AI 的型號送 AT+AIVALUE0 會回 ERROR，純粹浪費一趟。
        #   因此先看型號：型號已知不含 AI 時就不送；
        #   型號未知（第一輪連線時）才試一次並記住結果。
        if self._ai_supported is None or self._ai_supported:
            try:
                res = self.get_ai_values()
                if res.ok:
                    self._ai_supported = True
                    result["ai"] = {str(k): v for k, v in
                                    res.parsed_ai.items()}
                elif self._ai_supported is None:
                    # 首次確認這個型號沒有 AI，之後不再送
                    self._ai_supported = False
            except AtError:
                pass

        return result
