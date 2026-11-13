"""網段掃描模組。

移植自 `AT批次設定工具/device_discovery.py`，移除 Windows 專用程式碼，
保留「ARP 表格優先 + ping 探測」的快速策略。

設計要點：
    1. 掃描要快 —— 用並發 thread pool
    2. 不能太吵 —— 對每個 IP 只探一次 TCP 連線
    3. ARP 表格裡已有近期通訊的主機，直接判定在線，省掉大部分 ping
    4. Mock 模式 —— 可指定直接查詢的目標清單，完全避開真實網路
"""
from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# NR-MC 系列 WiFi 控制器的 MAC OUI（實測 4E:26:09、4E:14:01 都是這個廠商）
KNOWN_OUI_PREFIXES = ("4E:26:09", "4E:14:01")

# 預設掃描的 AT over TCP 監聽 port
DEFAULT_AT_PORT = 12345

# 同時探測的最大數量
MAX_WORKERS = 16


# ----------------------------------------------------------------------
# 網路資訊
# ----------------------------------------------------------------------
def get_local_ip() -> str:
    """取得本機在對外連線時使用的 IP。

    開一個 UDP socket 連到外部位址（不會真的送出封包），
    讓作業系統告訴我們它會用哪個 IP 出流量。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def get_local_netmask() -> str:
    """取得本機的子網遮罩。"""
    try:
        out = subprocess.run(["ip", "addr"], capture_output=True,
                             text=True, timeout=10).stdout
        m = re.search(r"inet\s+\d+\.\d+\.\d+\.\d+/\s*(\d+)", out)
        if m:
            bits = int(m.group(1))
            mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
            return ".".join(str((mask >> s) & 0xFF) for s in (24, 16, 8, 0))
    except Exception:
        pass
    return "255.255.255.0"


def _ip_to_int(ip: str) -> int:
    """把點分十進位轉成整數（支援 1~4 段，缺漏的高位補 0）。"""
    parts = [int(p) for p in ip.split(".")]
    n = 0
    for i, p in enumerate(parts):
        n |= (p & 0xFF) << (8 * (3 - i))
    return n


def _int_to_ip(n: int) -> str:
    """把整數轉回點分十進位。"""
    return ".".join(str((n >> s) & 0xFF) for s in (24, 16, 8, 0))


def parse_cidr(local_ip: str, netmask: str) -> tuple[str, int]:
    """回傳 (網段前綴, 主機位元數)。

    前綴可能是 3 段（/24）或 2 段（/16）等，依遮罩決定。
    """
    try:
        ip_n = _ip_to_int(local_ip)
        mask_n = _ip_to_int(netmask)
        network = ip_n & mask_n
        prefix_parts = []
        for s in (24, 16, 8, 0):
            byte = (mask_n >> s) & 0xFF
            if byte == 0:
                break
            prefix_parts.append(str((network >> s) & 0xFF))
        prefix = ".".join(prefix_parts)
        host_bits = 32 - bin(mask_n).count("1")
        return prefix, host_bits
    except Exception:
        return "192.168.30", 24


# ----------------------------------------------------------------------
# 主機探測
# ----------------------------------------------------------------------
def ping_host(ip: str, timeout_ms: int = 300) -> bool:
    """探測主機是否在線。

    容器（Alpine）提供的是 BusyBox ping，參數與 iputils 略有差異：
        BusyBox:  ping -c 1 -W <秒>
        iputils: ping -c 1 -W <秒>
    兩者在此用法下相容。若系統沒有 ping 指令則視為無法探測。
    """
    try:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_ms / 1000 + 2)
        text = out.stdout.lower()
        if "ttl=" in text:
            return True
        m = re.search(r"received\s*=\s*(\d+)", text)
        if m:
            return int(m.group(1)) > 0
        # BusyBox 輸出格式： "1 packets transmitted, 1 packets received, 0% packet loss"
        m = re.search(r"(\d+)\s+packets\s+received", text)
        if m:
            return int(m.group(1)) > 0
        m = re.search(r"(\d+)% packet loss", text)
        if m:
            return int(m.group(1)) < 100
        return False
    except Exception:
        return False


def read_arp() -> dict[str, str]:
    """讀 ARP 表格，回傳 {ip: mac}。

    容器內使用 `ip neigh`（iproute2 套件）。
    """
    table: dict[str, str] = {}
    try:
        out = subprocess.run(["ip", "neigh"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return table

    for line in out.splitlines():
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
        if not m:
            continue
        ip = m.group(1)
        mm = re.search(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", line)
        if mm:
            table[ip] = mm.group(0).upper()
    return table


def is_oui_known(mac: str) -> bool:
    """判斷 MAC 的 OUI 是否屬於已知的 WiFi 控制器廠商。"""
    if not mac:
        return False
    return mac.upper().replace("-", ":").startswith(KNOWN_OUI_PREFIXES)


def tcp_probe(ip: str, ports=(), timeout: float = 0.25) -> list[int]:
    """快速探測多個 port，回傳「握手成功」的 port 清單。

    這是搜尋流程的前段過濾器，目的是快速排除掉大部分無關的 IP。
    ★ 注意：握手成功**不等於**上面有 NR-MC 設備。真正的確認是由
      `query_at_name` 送出 AT 指令並解析回應來完成。
      因此這裡不做額外的資料往返，避免誤判與額外延遲。

    對「有開但拒絕」的 port，作業系統會立刻回 RST（很快）；
    只有「被防火牆丟包」的 port 才會等到 timeout。
    """
    open_ports: list[int] = []
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            open_ports.append(port)
        except (socket.timeout, OSError):
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass
    return open_ports


def query_at_name(host: str, port: int, timeout: float = 3.0) -> tuple[str, str]:
    """用 AT over TCP 取得設備名稱與型號。

    回傳 (名稱, 型號)；失敗回傳 ("", "")。
    """
    from .at_client import AtTcpClient

    client = AtTcpClient(host, port, timeout=timeout)
    try:
        if not client.connect():
            return "", ""
        client.send("AT")  # 暖機；不回覆也沒關係
        name = client.get_name().detail
        model = client.get_model().detail
        return name, model
    except Exception:
        return "", ""
    finally:
        client.close()


# ----------------------------------------------------------------------
# 網段掃描
# ----------------------------------------------------------------------
def scan_ips(prefix: str, host_bits: int = 8, timeout_ms: int = 300,
             progress=None, arp=None) -> list[str]:
    """掃描網段，回傳有回應的 IP 清單。

    策略（兼顧速度與準確度）：
        1. 先讀 ARP 表格 —— 裡面已有近期通訊過的主機，直接判定為在線
        2. ARP 沒有的 IP，用 ping 逐一探測（數量少時才划算）

    prefix  例："192.168.30" 或 "192.168.30.0"（皆可）
    host_bits  例：24 → 掃 .1 ~ .254

    注意：網段大於 /22（1024 個位址）時，全掃會非常慢。
    這時只掃「前 254 個 + ARP 已知的主機」並在回傳結果中標示。
    """
    mask_n = (0xFFFFFFFF << host_bits) & 0xFFFFFFFF
    base = _ip_to_int(prefix) & mask_n
    count = 1 << host_bits
    if host_bits >= 30:
        targets = [_int_to_ip(base + 1)]
    else:
        # 掃描上限：最多 254 個位址，避免 /16 那種 65534 個的瘋狂掃描
        limit = min(count - 1, 254)
        targets = [_int_to_ip(base + i) for i in range(1, limit)]

    alive: list[str] = []
    done = [0]
    lock = threading.Lock()

    def probe(ip: str) -> str | None:
        ok = ping_host(ip, timeout_ms)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], len(targets))
        return ip if ok else None

    arp = arp if arp is not None else read_arp()
    arp_ips = [ip for ip in targets if ip in arp]
    rest = [ip for ip in targets if ip not in arp]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for ip in pool.map(probe, rest):
            if ip:
                alive.append(ip)

    alive.extend(arp_ips)
    return alive


def _parse_subnet(subnet: str) -> tuple[str, int]:
    """解析使用者輸入的網段字串。

    支援：
        "192.168.30"      → /24
        "192.168.30.0"    → /24
        "192.168.30.0/24" → /24
    """
    text = subnet.strip()
    if "/" in text:
        base, _, bits_s = text.partition("/")
        try:
            bits = int(bits_s)
            host_bits = 32 - bits
            return ".".join(str(int(p) & 0xFF) for p in base.split(".")[:3]), host_bits
        except (ValueError, IndexError):
            pass
    parts = subnet.strip().split(".")
    prefix = ".".join(str(int(p) & 0xFF) for p in parts[:3])
    return prefix, 8


def discover(
    subnet: str = "",
    at_port: int = DEFAULT_AT_PORT,
    timeout_ms: int = 300,
    progress=None,
    log=None,
    targets: list[str] | None = None,
    candidate_ports: tuple[int, ...] | None = None,
) -> list[dict]:
    """完整搜尋流程。

    Args:
        subnet: 指定掃描網段前綴（例如 "192.168.30"）。留空則自動偵測。
        at_port: AT over TCP 監聽 port（主要使用的 port）。
        timeout_ms: ping 逾時時間。
        progress: 進度回呼 (done, total)。
        log: 日誌回呼 (message)。
        targets: ★ Mock 模式 —— 直接指定要查詢的 IP 清單，
                 完全跳過 ping 探測階段。
        candidate_ports: ★ 要嘗試的 port 清單。預設只用 at_port。
                 設定多個 port 可找出使用不同 port 的設備。

    Returns:
        裝置清單，每筆為 dict：
        {ip, mac, name, model, is_device, at_port}
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    ports_to_try = tuple(candidate_ports) if candidate_ports else (at_port,)

    arp = read_arp()

    if targets is not None:
        # ★ Mock 模式：直接查詢指定清單
        say(f"Mock 模式：查詢 {len(targets)} 個指定目標")
        alive = list(targets)
        total = len(alive)
    else:
        if subnet:
            prefix, host_bits = _parse_subnet(subnet)
            say(f"指定掃描網段：{prefix}（主機位元數 {host_bits}）")
        else:
            local_ip = get_local_ip()
            netmask = get_local_netmask()
            prefix, host_bits = parse_cidr(local_ip, netmask)
            say(f"本機 IP：{local_ip}   子網遮罩：{netmask}")
        say(f"掃描範圍：{prefix}.1 ~ {prefix}.{(1 << host_bits) - 2}")
        say(f"ARP 表格：{len(arp)} 筆")
        say("正在掃描有回應的主機 ...")

        alive = scan_ips(prefix, host_bits, timeout_ms, progress, arp)
        say(f"有回應的主機：{len(alive)} 台")
        total = len(alive)

    # AT 名稱查詢並行執行（每台設備約 1 秒，序列做會很慢）
    def probe_device(ip: str) -> list[tuple[str, str, int]]:
        """查詢單一 IP，回傳該 IP 上所有可用的 (名稱, 型號, port)。

        注意：同一個 IP 可能有多個 port 在監聽（例如 Mock 測試時
        多台設備共用 127.0.0.1），因此要回傳清單而非單一結果。
        """
        found: list[tuple[str, str, int]] = []
        for port in ports_to_try:
            n, mo = query_at_name(ip, port)
            if n or mo:
                found.append((n, mo, port))
        return found

    sorted_ips = sorted(alive, key=lambda x: tuple(int(p) for p in x.split(".")))
    probed: dict[str, list[tuple[str, str, int]]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ip, res in zip(sorted_ips, pool.map(probe_device, sorted_ips)):
            probed[ip] = res
            if progress:
                progress(len(probed), total)

    # 掃描過程中 ARP cache 會更新，再讀一次取得較完整的 MAC
    arp = read_arp()

    results: list[dict] = []
    for ip in sorted_ips:
        mac = arp.get(ip, "")
        entries = probed.get(ip, [])
        if not entries:
            # 沒有回應的 IP 也列入，但標記為非設備
            results.append({
                "ip": ip,
                "mac": mac,
                "name": "",
                "model": "",
                "is_device": is_oui_known(mac),
                "at_port": at_port,
            })
            continue
        for name, model, found_port in entries:
            results.append({
                "ip": ip,
                "mac": mac,
                "name": name,
                "model": model,
                "is_device": bool(model) or is_oui_known(mac),
                "at_port": found_port,
            })

    found = sum(1 for r in results if r["is_device"])
    say(f"掃描完成，共找到 {found} 台 NR-MC 設備")
    return results
