"""設備清單持久化。

儲存使用者加入的 NR-MC 設備清單。

存放位置：
    Windows 開發環境：backend/data/devices.json
    Add-on 容器內：    /data/devices.json

使用 thread lock 保護，並採「先寫暫存檔再置換」的原子寫入，
避免程序中途被殺掉時留下半個檔案。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path


def default_store_path() -> Path:
    """判斷目前執行環境，決定設備清單的存放位置。"""
    # Add-on 容器內：/data 一定存在且可寫
    container_data = Path("/data")
    if container_data.is_dir() and os.access(container_data, os.W_OK):
        return container_data / "devices.json"
    # 開發環境：backend/data/
    # __file__ = backend/nrmc_panel/device_store.py
    #   .parent        → backend/nrmc_panel
    #   .parent.parent → backend
    backend_root = Path(__file__).resolve().parent.parent
    return backend_root / "data" / "devices.json"


class DeviceStore:
    """設備清單的儲存與管理。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_store_path()
        self._lock = threading.RLock()
        self._devices: list[dict] = []
        self._next_id = 1
        self.load()

    # -- 持久化 -----------------------------------------------------------

    def load(self) -> list[dict]:
        """從磁碟載入設備清單。"""
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._devices = data.get("devices", [])
                self._next_id = data.get("next_id", 1)
                if not self._next_id:
                    self._next_id = max(
                        (d.get("id", 0) for d in self._devices), default=0
                    ) + 1
            except FileNotFoundError:
                self._devices = []
                self._next_id = 1
            except (json.JSONDecodeError, OSError):
                self._devices = []
                self._next_id = 1
            return list(self._devices)

    def save(self) -> None:
        """把設備清單寫回磁碟（原子寫入）。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"devices": self._devices, "next_id": self._next_id},
                ensure_ascii=False,
                indent=2,
            )
            # 先寫暫存檔，再置換，避免中途中斷留下壞檔
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self.path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                shutil.move(tmp_name, str(self.path))
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    # -- CRUD -------------------------------------------------------------

    def list(self) -> list[dict]:
        """列出所有設備。"""
        with self._lock:
            return [dict(d) for d in self._devices]

    def get(self, device_id: int) -> dict | None:
        """取得單一設備。"""
        with self._lock:
            for d in self._devices:
                if d.get("id") == device_id:
                    return dict(d)
        return None

    def exists(self, host: str, port: int) -> dict | None:
        """依 host + port 查詢既有設備。"""
        with self._lock:
            for d in self._devices:
                if d.get("host") == host and d.get("port") == port:
                    return dict(d)
        return None

    def add(self, host: str, port: int, name: str = "",
            model: str = "", mac: str = "") -> dict:
        """新增設備，回傳新增的記錄。"""
        with self._lock:
            device_id = self._next_id
            self._next_id += 1
            record = {
                "id": device_id,
                "host": host,
                "port": port,
                "name": name,
                "model": model,
                "mac": mac,
            }
            self._devices.append(record)
            self.save()
            return dict(record)

    def update(self, device_id: int, **fields) -> dict | None:
        """更新設備欄位。"""
        with self._lock:
            for d in self._devices:
                if d.get("id") == device_id:
                    d.update(fields)
                    self.save()
                    return dict(d)
        return None

    def remove(self, device_id: int) -> bool:
        """移除設備，回傳是否確實移除了。"""
        with self._lock:
            before = len(self._devices)
            self._devices[:] = [
                d for d in self._devices if d.get("id") != device_id
            ]
            changed = len(self._devices) != before
            if changed:
                self.save()
            return changed

    def __len__(self) -> int:
        with self._lock:
            return len(self._devices)

    def __bool__(self) -> bool:
        """永遠回傳 True。

        ★ 這是刻意的：DeviceStore 定義了 __len__，Python 會用長度來
        決定布林值，導致「空的 store」變成 False。若呼叫端寫成
        `store or DeviceStore()`，傳入的 store 就會被無意間忽略。
        定義 __bool__ 回傳 True 可以避免這個陷阱。
        """
        return True
