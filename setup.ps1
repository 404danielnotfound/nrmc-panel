# 一次性設定腳本（Windows PowerShell 版）。
#
# 用途：帶你逐步完成部署到 Home Assistant 前的準備工作。
#
# 會依序詢問幾個問題，然後產生：
#     .env              —— GitHub Container Registry 的登入資訊
#     .env.example      —— 範例檔（可安全地 commit）
#
# 執行：
#     .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  NR-MC 控制台 —— 部署前設定" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------
# 步驟 1：GitHub 帳號
# ---------------------------------------------------------------
Write-Host "步驟 1/3：GitHub 帳號資訊" -ForegroundColor Yellow
Write-Host ""
Write-Host "  我們需要一個 GitHub 存放區來存放 Add-on 程式碼，" -ForegroundColor Gray
Write-Host "  以及存放 Docker 映像（HA 才能下載）。" -ForegroundColor Gray
Write-Host ""
Write-Host "  請先到 https://github.com 註冊（或登入）一個免費帳號。" -ForegroundColor Cyan
Write-Host ""

$ghcrUser = Read-Host "  請輸入你的 GitHub 使用者名稱"

if ([string]::IsNullOrWhiteSpace($ghcrUser)) {
    Write-Host "  X 不可為空" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------
# 步驟 2：Personal Access Token
# ---------------------------------------------------------------
Write-Host ""
Write-Host "步驟 2/3：建立 GitHub Token（用來推送 Docker 映像）" -ForegroundColor Yellow
Write-Host ""
Write-Host "  請依下列步驟建立一個 Token：" -ForegroundColor Gray
Write-Host ""
Write-Host "    1. 開啟 https://github.com/settings/tokens" -ForegroundColor Cyan
Write-Host '    2. 捲到最下方點 "Generate new token (classic)"' -ForegroundColor Cyan
Write-Host "    3. 勾選以下項目：" -ForegroundColor Cyan
Write-Host "         - write:packages   （推送 Docker 映像需要）" -ForegroundColor Cyan
Write-Host "         - repo             （推送程式碼需要）" -ForegroundColor Cyan
Write-Host "    4. 設定到期日（建議 90 天，到期後重新建立）" -ForegroundColor Cyan
Write-Host '    5. 按下 "Generate token"' -ForegroundColor Cyan
Write-Host "    6. 複製那串字元（ghp_ 開頭），只會顯示一次" -ForegroundColor Cyan
Write-Host ""

$ghcrToken = Read-Host "  請貼上 Token"

if ([string]::IsNullOrWhiteSpace($ghcrToken)) {
    Write-Host "  X 不可為空" -ForegroundColor Red
    exit 1
}

if (-not $ghcrToken.StartsWith("ghp_") -and -not $ghcrToken.StartsWith("github_pat_")) {
    Write-Host "  提醒：Token 看起來不像 GitHub Token，請確認是否貼對" -ForegroundColor Yellow
}

# ---------------------------------------------------------------
# 步驟 3：產生 .env
# ---------------------------------------------------------------
Write-Host ""
Write-Host "步驟 3/3：產生設定檔" -ForegroundColor Yellow

$envContent = @"
# NR-MC 控制台 —— 部署設定
# ★ 這個檔案包含敏感資訊，絕對不要 commit 到 Git！
#   已經在 .gitignore 中排除。

# GitHub 使用者名稱
GHCR_USER=$ghcrUser

# GitHub Personal Access Token
GHCR_TOKEN=$ghcrToken

# 映像名稱（HA 的 config.yaml 用 {arch} 佔位符，各架構自動代入）
IMAGE_NAME=ghcr.io/$ghcrUser/nrmc-panel

# Add-on 版本標籤
VERSION=1.0.0
"@

$envContent | Set-Content -Path ".env" -Encoding UTF8

# 範例檔（不含敏感資訊）
$exampleContent = @"
# 複製成 .env 後填入自己的值
# 產生方式：.\setup.ps1

GHCR_USER=your-github-username
GHCR_TOKEN=ghp_your_token_here
IMAGE_NAME=ghcr.io/your-github-username/nrmc-panel
VERSION=1.0.0
"@
$exampleContent | Set-Content -Path ".env.example" -Encoding UTF8

# --- 同步更新 nrmc_panel/config.yaml 的 image 欄位 ---
$configPath = "nrmc_panel\config.yaml"
if (Test-Path $configPath) {
    $config = Get-Content $configPath -Raw -Encoding UTF8

    # ★ 用寬鬬但明確的 pattern：
    #   image: ghcr.io/<任意帳號>/nrmc-panel-{arch}
    #   （不能寫 [^-\s]+，因為 GitHub 帳號可以包含「-」）
    $config = $config -replace '(?m)^image:\s*ghcr\.io/[^/]+/nrmc-panel-\{arch\}\s*$', "image: ghcr.io/$ghcrUser/nrmc-panel-{arch}"
    $config = $config -replace '(?m)^url:\s*https://github\.com/[^/]+/nrmc-panel\s*$', "url: https://github.com/$ghcrUser/nrmc-panel"

    # ★ 用 UTF-8（無 BOM）寫回，避免 BOM 影響 YAML 解析
    [System.IO.File]::WriteAllText(
        (Resolve-Path $configPath).Path,
        $config,
        (New-Object System.Text.UTF8Encoding($false))
    )

    Write-Host "      nrmc_panel\config.yaml 已更新 image 欄位" -ForegroundColor Green
}

# ★ build.ps1 不需要修改。
#   它已經用「讀取 .env 取得 GHCR_USER」的方式動態決定映像名稱，
#   不會有寫死的帳號名稱。

# --- 修正 s6 腳本換行符（OneDrive / git checkout 會還原 CRLF）---
$fixScript = "fix_line_endings.py"
if (Test-Path $fixScript) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        python $fixScript | Out-Null
        Write-Host "      s6 腳本換行符已檢查（LF）" -ForegroundColor Green
    } else {
        Write-Host "      略過換行符檢查（找不到 python）" -ForegroundColor Yellow
    }
}

Write-Host "      .env 已建立" -ForegroundColor Green
Write-Host "      .env.example 已建立" -ForegroundColor Green

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  設定完成" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  下一步：" -ForegroundColor White
Write-Host "    1. 安裝 Docker Desktop（還沒裝的話）" -ForegroundColor White
Write-Host "       https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
Write-Host "    2. 執行 .\build.ps1 -Test  建置並本機測試" -ForegroundColor White
Write-Host "    3. 詳細說明請看 addons\nrmc_panel\README.md" -ForegroundColor White
Write-Host ""
