"""
將 s6 開機腳本轉成 Linux 需要的 LF 換行符。

★ 為什麼需要這個腳本？
   s6 腳本的第一行是 shebang：
       #!/usr/bin/with-contenv bashio
   Linux 要求這行完全正確。如果檔案是 Windows 的 CRLF 換行，
   shebang 會變成：
       #!/usr/bin/with-contenv bashio^M
   容器啟動時會直接失敗：
       bad interpreter: /usr/bin/with-contenv^M: no such file or directory

   ★ 而且這個問題會「無聲復發」：
     - OneDrive 同步會還原 CRLF
     - git 在 Windows 上 checkout 預設會轉成 CRLF
   所以除了 Dockerfile 裡的 sed 修正（最後一道防線），
   每次部署前都應該跑一次這個腳本。

用法：
    python fix_line_endings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
S6_DIR = ROOT / "addons" / "nrmc_panel" / "rootfs" / "etc" / "services.d"


def fix_file(path: Path) -> bool:
    """把單一檔案轉成 LF。回傳是否有修改。"""
    data = path.read_bytes()
    if b"\r\n" not in data:
        return False

    # 統一轉成 LF，並移除可能殘留的 CR
    fixed = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    path.write_bytes(fixed)
    return True


def main() -> int:
    """主流程。"""
    if not S6_DIR.is_dir():
        print(f"S6 directory not found: {S6_DIR}")
        return 1

    scripts = sorted(p for p in S6_DIR.rglob("run") if p.is_file())
    if not scripts:
        print(f"No 'run' scripts found under {S6_DIR}")
        return 1

    # ★ 輸出用 ASCII，避免 Windows 主控台 cp1252 無法印中文而中斷
    #   （中斷會導致只修到一半的檔案）
    changed = 0
    for script in scripts:
        if fix_file(script):
            print(f"  fixed: {script.name} (CRLF -> LF)")
            changed += 1
        else:
            print(f"  ok:    {script.name} (already LF)")

    if changed:
        print(f"\nDone. Fixed {changed} file(s).")
    else:
        print("\nAll files already use LF. Nothing to do.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
