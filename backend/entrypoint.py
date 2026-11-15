"""Add-on 容器啟動入口。

Home Assistant 的 Supervisor 會把使用者設定透過環境變數傳進來，
這個腳本負責：
    1. 讀取 Add-on options
    2. 轉換成應用程式需要的環境變數
    3. 啟動 Flask 後端（前景執行）

Nginx 由 s6 另行啟動，負責對外提供服務。
"""
from __future__ import annotations

import os
import sys


def read_option(name: str, default: str = "") -> str:
    """讀取 Home Assistant 傳入的 Add-on 選項。

    Supervisor 會把 options.json 的內容以 JSON 字串放進
    `SUPERVISOR_ADDON_OPTIONS` 環境變數。
    """
    raw = os.environ.get("SUPERVISOR_ADDON_OPTIONS", "")
    if not raw:
        return default
    try:
        import json

        options = json.loads(raw)
        value = options.get(name, default)
        # 數字與布林要轉成字串
        if value is None:
            return default
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    except Exception:  # noqa: BLE001
        return default


def main() -> int:
    """設定環境變數並啟動後端。"""
    # --- 讀取 Add-on 設定 ---
    at_port = read_option("at_port", "12345")
    scan_subnet = read_option("scan_subnet", "")
    log_level = read_option("log_level", "info")
    ping_timeout = read_option("ping_timeout_ms", "300")
    http_port = read_option("http_port", "8080")
    # ★ DO 互鎖群組（風機一檔／二檔互斥）
    do_interlock = read_option("do_interlock", "1,2")

    # --- 寫入環境變數給 app.py 讀取 ---
    os.environ["NRMC_AT_PORT"] = at_port
    os.environ["NRMC_SCAN_SUBNET"] = scan_subnet
    os.environ["NRMC_LOG_LEVEL"] = log_level
    os.environ["NRMC_PING_TIMEOUT"] = ping_timeout
    os.environ["NRMC_HTTP_PORT"] = http_port
    os.environ["NRMC_DO_INTERLOCK"] = do_interlock

    print("=" * 60)
    print("NR-MC 控制台後端啟動中")
    print(f"  AT port        : {at_port}")
    print(f"  掃描網段       : {scan_subnet or '(自動偵測本機網段)'}")
    print(f"  日誌層級       : {log_level}")
    print(f"  內部 HTTP port: {http_port}")
    print(f"  DO 互鎖群組    : {do_interlock or '(不啟用)'}")
    print("=" * 60)

    # 確保 /data 存在且可寫（設備清單存這裡）
    data_dir = "/data"
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as exc:
        print(f"[警告] 無法建立 {data_dir}: {exc}")

    from nrmc_panel.app import main as run_app

    run_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
