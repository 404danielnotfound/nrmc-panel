# Docker 建置腳本（Windows PowerShell 版）。
#
# 用途：讓你不需要記住複雜的 docker 指令，一鍵建置 Add-on 映像。
#
# 用法：
#     .\build.ps1              # 建置映像
#     .\build.ps1 -Push        # 建置並推送到 GitHub Container Registry
#     .\build.ps1 -Test        # 建置後啟動容器做本機測試
#
# 前置條件：
#     1. 已安裝 Docker Desktop 並已啟動
#     2. 已執行 .\setup.ps1（建立 .env 檔案）
param(
    [switch]$Push,
    [switch]$Test,
    [string]$Tag = "nrmc-panel:1.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  NR-MC 控制台 Add-on —— Docker 建置" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# --- 檢查 Docker 是否可用 ---
Write-Host "[1/4] 檢查 Docker..." -ForegroundColor Yellow
try {
    $dockerInfo = docker info --format "{{.ServerVersion}}" 2>$null
    if (-not $dockerInfo) {
        throw "Docker 引擎沒有回應"
    }
    Write-Host "      Docker 引擎版本: $dockerInfo" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "  X 找不到可用的 Docker" -ForegroundColor Red
    Write-Host ""
    Write-Host "  請先安裝 Docker Desktop：" -ForegroundColor Yellow
    Write-Host "    1. 下載: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
    Write-Host "    2. 安裝後重新啟動電腦" -ForegroundColor Yellow
    Write-Host "    3. 啟動 Docker Desktop，等右下角圖示變成綠色" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# --- 檢查 .env ---
Write-Host "[2/4] 檢查設定檔..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Write-Host "      X 找不到 .env" -ForegroundColor Red
    Write-Host "      請先執行 .\setup.ps1" -ForegroundColor Yellow
    exit 1
}
Write-Host "      .env 已就緒" -ForegroundColor Green

# --- 修正 s6 腳本換行符 ---
# ★ 為什麼需要這一步？
#   s6 腳本的 shebang 在 CRLF 換行下會讓容器啟動失敗。
#   OneDrive 同步或 git checkout 會把 LF 還原成 CRLF，
#   所以每次建置前都要重新修正。
#   （Dockerfile 裡也有 sed 作為最後一道防線，但先修好比較乾淨。）
if (Test-Path "fix_line_endings.py") {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        python "fix_line_endings.py" | Out-Null
        Write-Host "      s6 腳本換行符已修正（LF）" -ForegroundColor Green
    }
}

# --- 建置 ---
Write-Host "[3/4] 建置映像（首次會較久，可能 10-20 分鐘）..." -ForegroundColor Yellow
Write-Host "      映像名稱: $Tag" -ForegroundColor Gray

docker build -t $Tag .
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  X 建置失敗" -ForegroundColor Red
    Write-Host "  請把上面的錯誤訊息貼給我" -ForegroundColor Yellow
    exit 1
}
Write-Host "      建置成功" -ForegroundColor Green

# --- 推送到 registry ---
if ($Push) {
    Write-Host "[4/4] 推送到 GitHub Container Registry..." -ForegroundColor Yellow
    $envContent = Get-Content ".env" | Where-Object { $_ -match "^GHCR_USER=" }
    $ghcrUser = ($envContent -split "=", 2)[1].Trim()

    if (-not $ghcrUser) {
        Write-Host "      X .env 缺少 GHCR_USER" -ForegroundColor Red
        exit 1
    }

    # ★ HA 的 config.yaml 用 {arch} 佔位符，所以每個架構要推一個 tag：
    #     ghcr.io/<user>/nrmc-panel-amd64
    #     ghcr.io/<user>/nrmc-panel-aarch64
    #     ...
    #   Supervisor 會依 HA 主機的 CPU 架構自動挑對應的映像。
    $archList = @("amd64", "aarch64", "armv7", "armhf", "i386")
    $baseRemote = "ghcr.io/$ghcrUser/nrmc-panel"

    $ghcrToken = (Get-Content ".env" | Where-Object { $_ -match "^GHCR_TOKEN=" } | ForEach-Object { ($_ -split "=", 2)[1].Trim() })
    Write-Host "      登入 ghcr.io..." -ForegroundColor Yellow
    $ghcrToken | docker login ghcr.io -u $ghcrUser --password-stdin
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      X 登入失敗，請檢查 Token" -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "      推送各架構映像（只建置目前電腦的架構）..." -ForegroundColor Yellow
    Write-Host "      ★ 提示：只有你電腦的架構會有映像，" -ForegroundColor Gray
    Write-Host "        其他架構會由 GitHub Actions 自動建置" -ForegroundColor Gray
    Write-Host ""

    # 先取得目前電腦的架構
    $hostArch = docker info --format "{{.Architecture}}"
    Write-Host "      目前電腦架構: $hostArch" -ForegroundColor Gray

    # ★ HA 拉的是「版本號」tag：ghcr.io/<user>/nrmc-panel-<arch>:<version>
    #   version 來自 .env 的 VERSION（setup.ps1 寫入），跟 config.yaml 的 version 必須一致。
    #   舊寫法只推了 :latest，HA 照 version 去拉就會 404 manifest unknown。
    $versionLine = (Get-Content ".env" | Where-Object { $_ -match "^VERSION=" } | ForEach-Object { ($_ -split "=", 2)[1].Trim() })
    if (-not $versionLine) { $versionLine = "1.0.1" }

    foreach ($arch in $archList) {
        if ($arch -ne $hostArch) { continue }

        foreach ($tagSuffix in @($versionLine, "latest")) {
            $remoteTag = "$baseRemote-$arch`:$tagSuffix"
            docker tag $Tag $remoteTag
            Write-Host "      推送 $remoteTag" -ForegroundColor Gray
            docker push $remoteTag
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      X 推送失敗" -ForegroundColor Red
                exit 1
            }
        }
    }

    # 額外推一個不帶架構的 tag，方便手動 docker pull
    docker tag $Tag "$baseRemote:latest"
    docker push "$baseRemote:latest" | Out-Null

    Write-Host ""
    Write-Host "      推送完成" -ForegroundColor Green
    Write-Host ""
    Write-Host "      ★ 下一步：到 https://github.com/packages" -ForegroundColor Yellow
    Write-Host "        把 nrmc-panel 這個 package 的可見性改成 Public" -ForegroundColor Yellow
    Write-Host "        （不改成公開的話，HA 會說找不到映像）" -ForegroundColor Yellow
} else {
    Write-Host "[4/4] 略過推送（加 -Push 可推送）" -ForegroundColor Gray
}

# --- 本機測試 ---
# ★ 測試容器會保持運行（不會自動刪除），讓你有時間開瀏覽器驗證。
#   看完後再手動執行 docker rm -f nrmc-test 停止。
if ($Test) {
    Write-Host ""
    Write-Host "      啟動測試容器..." -ForegroundColor Yellow
    docker rm -f nrmc-test 2>$null | Out-Null
    docker run -d --name nrmc-test -p 8099:8099 $Tag | Out-Null
    Write-Host "      等待服務啟動（約 12 秒）..." -ForegroundColor Gray
    Start-Sleep -Seconds 12
    Write-Host ""
    Write-Host "      容器狀態:" -ForegroundColor Cyan
    docker ps --filter "name=nrmc-test" --format "{{.Names}} {{.Status}} {{.Ports}}"
    Write-Host ""
    Write-Host "      測試網址: http://127.0.0.1:8099" -ForegroundColor Cyan
    Write-Host "      健康檢查:" -ForegroundColor Cyan
    curl.exe -s http://127.0.0.1:8099/api/health
    Write-Host ""
    Write-Host ""
    Write-Host "      用瀏覽器開 http://127.0.0.1:8099 驗證介面" -ForegroundColor Cyan
    Write-Host "      看日誌: docker logs nrmc-test --tail 50" -ForegroundColor Gray
    Write-Host "      停止測試容器: docker rm -f nrmc-test" -ForegroundColor Gray
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  完成" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
