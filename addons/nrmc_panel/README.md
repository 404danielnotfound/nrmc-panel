# NR-MC 控制台

NR-MC 系列 WiFi 新風控制器的管理面板（Home Assistant Add-on）。

## 功能

- **裝置總覽**：管理已加入的設備，可重新命名、移除、查看連線狀態
- **網路掃描**：搜尋指定網段內的 NR-MC 設備，一鍵加入清單
- **設備測試**：查看連線狀態與 IO 狀態，控制風機檔位（一檔／二檔／關閉）
- **AT 指令測試**：除錯用的終端機，可直接對設備送出 AT 指令

## 硬體互鎖

NR-MC2201-WIFI 的 DO1 與 DO2 構成**硬體互鎖組**：

- 開啟 DO1 → 裝置自動關閉 DO2
- 開啟 DO2 → 裝置自動關閉 DO1

這個互鎖在設備端就已經實作，介面只負責顯示真實狀態，不會自己去關另一個通道。
因此可以安全地把 DO1 當「一檔」、DO2 當「二檔」，控制排風機的兩段速。

## 安裝

在 Home Assistant 中：

1. **設定 → 附加元件 → 附加元件商店 → ⋮ → Repositories**
2. 新增本倉庫網址
3. 在附加元件商店找到「NR-MC 控制台」並安裝
4. 啟動

> **重要**：安裝後請到「網路」分頁開啟 **主機網路**，
> 否則容器在隔離網段中，掃描不到區網內的設備。

## 設定選項

| 選項 | 預設 | 說明 |
|------|------|------|
| `at_port` | `12345` | AT over TCP 埠號 |
| `scan_subnet` | （空） | 掃描網段，留空則自動偵測本機網段 |
| `log_level` | `info` | 日誌層級 |
| `ping_timeout_ms` | `300` | ping 逾時時間 |
| `http_port` | `8080` | 容器內部 HTTP 埠號 |

## 技術架構

單一容器，三個部分：

| 元件 | 技術 | 說明 |
|------|------|------|
| 前端 | Flutter Web | 編譯成靜態檔，由 Nginx 提供 |
| 後端 | Python + Flask | REST API + WebSocket 狀態推播 |
| 網頁伺服器 | Nginx | 對外提供靜態檔，把 `/api/` 轉發給 Flask |

## 開發

```bash
# 後端測試
cd backend && pytest

# 前端建置
cd frontend && flutter build web --release

# 建置容器映像
./build.ps1 -Test    # Windows
docker build -t nrmc-panel .
```

## 授權

MIT
