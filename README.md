# NR-MC 控制台 Add-on（nrmc-panel-addon）

NR-MC 系列 WiFi 新風控制器的 Home Assistant Add-on 管理面板。

## 內容

- `nrmc_panel/` — Add-on 定義（`config.yaml`、`build.yaml`、nginx 設定、s6 開機腳本）
- `backend/` — Python + Flask 後端（REST API + WebSocket 狀態推播）
- `frontend/` — Flutter Web 前端原始碼（編譯成靜態檔，由 Nginx 提供）
- `Dockerfile` — 多階段建置（Flutter 編譯 → HA base 執行映像）
- `build.ps1` / `setup.ps1` / `fix_line_endings.py` — Windows 建置與設定腳本
- `.github/workflows/build.yml` — GitHub Actions 多架構建置

## 快速開始（Windows）

```powershell
.\setup.ps1       # 產生 .env（含 GHCR 登入資訊，不會進版控）
.\build.ps1 -Test # 建置並本機測試 http://127.0.0.1:8099
```

## 安裝到 Home Assistant

1. 設定 → 附加元件 → 附加元件商店 → ⋮ → Repositories，新增本倉庫網址
2. 找到「NR-MC 控制台」並安裝、啟動
3. 到「網路」分頁開啟 **主機網路**，否則掃描不到區網設備

詳細說明請看 `nrmc_panel/README.md`。

## 注意

- `.env` 含 Token，絕對不要 commit（已在 `.gitignore` 排除）
- s6 開機腳本（`nrmc_panel/rootfs/etc/services.d/*/run`）必須保持 LF 換行，
  `setup.ps1` / `build.ps1` 會自動檢查，`Dockerfile` 建置時也會修正
