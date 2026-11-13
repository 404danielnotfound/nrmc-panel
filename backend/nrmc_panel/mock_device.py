"""Mock AT 設備模擬器。

模擬 NR-MC 系列 WiFi 控制器的 AT over TCP 行為，讓整套系統在沒有實體
設備的情況下也能完整測試。

模擬的重點（依據實測量測值）：
    1. 首 byte 延遲：0.05 ~ 0.65 秒（隨機），模擬真實設備的反應速度
    2. 多行回覆分批送達：查詢全部通道時，每行之間隔 20~80ms
    3. 查詢指令沒有 OK 結尾
    4. 硬體互鎖：DO1 與 DO2 互斥，開一個會自動關另一個

啟動方式：
    python -m nrmc_panel.mock_device --port 12345 --name EXFAN_2201
    python -m nrmc_panel.mock_device --port 12345 --model NR-MC4401-WIFI

模擬多台設備（不同 port）：
    python -m nrmc_panel.mock_device --port 12345 --name EXFAN_2201
    python -m nrmc_panel.mock_device --port 12346 --name AT_RECEPTION
"""
from __future__ import annotations

import argparse
import random
import socket
import socketserver
import threading
import time

TERM = "\r\n"

# 支援的型號與其通道數
KNOWN_MODELS = {
    "NR-MC2201-WIFI": {"do": 2, "di": 2, "ai": 0},
    "NR-MC4401-WIFI": {"do": 4, "di": 4, "ai": 4},
    "NR-MC4041-WIFI": {"do": 4, "di": 4, "ai": 0},
    "NR-MC4221-WIFI": {"do": 2, "di": 2, "ai": 0},
}


class MockDeviceState:
    """模擬設備的內部狀態（DO/DI/AI）。"""

    def __init__(self, model: str, name: str, firmware: str = ""):
        spec = KNOWN_MODELS.get(model, {"do": 2, "di": 2, "ai": 0})
        self.model = model
        self.name = name
        self.firmware = firmware or f"{model}-VER30.3"
        self.do_count = spec["do"]
        self.di_count = spec["di"]
        self.ai_count = spec["ai"]
        self.lock = threading.Lock()
        self.do_states: dict[int, bool] = {i: False for i in range(1, self.do_count + 1)}
        self.di_states: dict[int, bool] = {i: False for i in range(1, self.di_count + 1)}
        self.ai_values: dict[int, float] = {i: 0.0 for i in range(1, self.ai_count + 1)}
        # 互鎖組：第 1 組為 DO1/DO2
        self.interlock_groups: list[list[int]] = []
        if self.do_count >= 2:
            self.interlock_groups.append([1, 2])

    def set_do(self, channel: int, on: bool) -> None:
        """寫入 DO，並套用硬體互鎖。"""
        with self.lock:
            if channel == 0:
                for ch in self.do_states:
                    self.do_states[ch] = on
                return
            if channel not in self.do_states:
                return
            self.do_states[channel] = on
            # 硬體互鎖：同組的其他通道自動關閉
            for group in self.interlock_groups:
                if channel in group:
                    for other in group:
                        if other != channel:
                            self.do_states[other] = False


class MockAtHandler(socketserver.BaseRequestHandler):
    """處理單一 TCP 連線的 AT 指令。"""

    def handle(self) -> None:
        state = self.server.device_state  # type: ignore[attr-defined]
        self.request.settimeout(30)
        buffer = b""

        try:
            while True:
                try:
                    chunk = self.request.recv(1024)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                buffer += chunk

                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    self._handle_command(line, state)
        except OSError:
            pass

    # -- 指令處理 ---------------------------------------------------------

    def _handle_command(self, cmd: str, state: MockDeviceState) -> None:
        cmd = cmd.strip()
        upper = cmd.upper()

        # 模擬真實設備的處理延遲
        self._delay()

        if upper == "AT":
            self._send("OK")
            return

        # ★ 順序重要：查詢指令「AT+NAME=?」必須先比對，
        # 否則會被下面的「AT+NAME=」給值分支攔截掉。
        if upper == "AT+NAME=?":
            self._send(f"+NAME:{state.name}")
            return

        if upper.startswith("AT+NAME="):
            value = cmd[len("AT+NAME="):].strip()
            if value:
                state.name = value
            self._send("OK")
            return

        if upper == "AT+DEVICEINFO=?":
            self._send(
                f"+DEVICEINFO:,UT:{state.model},FV:{state.firmware}"
            )
            return

        if upper == "AT+WSLK?":
            self._send("+WSLK:1,1,1,25")
            return

        if upper == "AT+WANN?":
            self._send("+WANN:1,192.168.30.250,255.255.255.0,192.168.30.1")
            return

        if upper == "AT+STACH0?":
            with state.lock:
                snapshot = dict(state.do_states)
            # 多行回覆，分批送達
            for ch in sorted(snapshot):
                self._send(f"+STACH{ch}:{1 if snapshot[ch] else 0},100000",
                           delay=0.03)
            return

        if upper.startswith("AT+STACH") and "=" in upper:
            body = cmd[len("AT+STACH"):]
            num_s, _, val_s = body.partition("=")
            try:
                channel = int(num_s)
                on = int(val_s.strip()) != 0
            except ValueError:
                self._send("ERROR")
                return
            if channel != 0 and channel not in state.do_states:
                self._send("ERROR")
                return
            state.set_do(channel, on)
            self._send("OK")
            return

        if upper == "AT+OCCH0":
            with state.lock:
                snapshot = dict(state.di_states)
            for ch in sorted(snapshot):
                self._send(f"+OCCH{ch}:{1 if snapshot[ch] else 0}", delay=0.03)
            return

        if upper == "AT+AIVALUE0":
            if state.ai_count == 0:
                self._send("ERROR")
                return
            with state.lock:
                snapshot = dict(state.ai_values)
            for ch in sorted(snapshot):
                self._send(f"+AIVALUE{ch}:{snapshot[ch]:.2f}", delay=0.03)
            return

        # 未知指令
        self._send("ERROR")

    # -- 傳送輔助 ---------------------------------------------------------

    def _delay(self) -> None:
        """模擬設備的反應延遲。

        實測首 byte 延遲在 0.05~0.65 秒之間波動，這裡取相近的分布，
        讓開發時能及早發現「假設回覆是即時的」那類錯誤。
        """
        time.sleep(random.uniform(0.05, 0.35))

    def _send(self, text: str, delay: float = 0.0) -> None:
        """送出一行回覆。"""
        if delay:
            time.sleep(delay)
        try:
            self.request.sendall((text + TERM).encode("utf-8"))
        except OSError:
            pass


class MockAtServer(socketserver.ThreadingTCPServer):
    """Threading TCP server，承載 Mock 設備。"""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, state: MockDeviceState):
        self.device_state = state
        super().__init__(address, MockAtHandler)


def create_mock_server(
    host: str = "127.0.0.1",
    port: int = 12345,
    name: str = "MOCK_DEVICE",
    model: str = "NR-MC2201-WIFI",
) -> MockAtServer:
    """建立 Mock 設備伺服器（尚未啟動）。"""
    state = MockDeviceState(model=model, name=name)
    return MockAtServer((host, port), state)


def main() -> None:
    parser = argparse.ArgumentParser(description="NR-MC Mock AT 設備模擬器")
    parser.add_argument("--host", default="127.0.0.1", help="監聽位址")
    parser.add_argument("--port", type=int, default=12345, help="監聽 port")
    parser.add_argument("--name", default="MOCK_DEVICE", help="設備名稱")
    parser.add_argument(
        "--model",
        default="NR-MC2201-WIFI",
        choices=list(KNOWN_MODELS),
        help="設備型號",
    )
    args = parser.parse_args()

    server = create_mock_server(args.host, args.port, args.name, args.model)
    state = server.device_state
    print(f"[mock] 啟動 {state.model} 名稱={state.name} "
          f"於 {args.host}:{args.port}")
    print(f"[mock] DO 通道數={state.do_count} DI 通道數={state.di_count} "
          f"AI 通道數={state.ai_count}")
    print("[mock] Ctrl+C 結束")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] 收到中斷訊號，結束")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
