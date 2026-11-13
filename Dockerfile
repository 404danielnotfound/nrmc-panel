# syntax=docker/dockerfile:1
# ★ BASE_IMAGE 必須宣告在第一個 FROM 之前，
#   否則第二個 FROM 讀不到（Docker 的 ARG 作用域規則）。
ARG BASE_IMAGE=ghcr.io/home-assistant/amd64-base:3.19
# ★ Flutter 版本鎖定：必須跟本機開發版本一致（Dart >=3.13.5）。
#   cirruslabs/flutter:stable 目前只到 Dart 3.12，會導致 pub get 失敗，
#   所以改從官方下載指定版本，不再依賴 cirruslabs 的 stable 標籤。
ARG FLUTTER_VERSION=3.47.7
#
# NR-MC 控制台 Add-on —— 多階段建置
#
# 階段說明：
#   stage 1 (flutter) : 編譯 Flutter Web 靜態檔
#   stage 2 (runtime) : 最終映像，只含 Nginx + Python + 前端產物
#
# ★ 為什麼要分兩段？
#   Flutter SDK 有 1GB 以上，編譯完就可以丟掉。
#   最終映像只留執行時需要的東西，體積從數 GB 降到數百 MB。
#   HA 主機的資源有限，映像小一點很重要。

# =====================================================================
# Stage 1: 編譯 Flutter Web
#
# ★ 為什麼不用 cirruslabs/flutter:stable？
#   該映像目前只到 Dart 3.12，但 pubspec 要求 Dart ^3.13.5，
#   導致 `flutter pub get` 直接失敗。
#   改從官方 release 下載指定版本（FLUTTER_VERSION），
#   保證跟本機開發版本一致，且不受第三方映像更新延遲影響。
#
# ★ 為什麼要自己編譯而不是把 build/web 塞進映像？
#   1. build/web 是編譯產物，不該進版控（.gitignore 已排除）
#   2. 保證映像裡的前端一定跟當下的原始碼一致
#      （不會用到開發機上殘留的舊版產物）
#   3. CI 上每次建置都是從原始碼重新編譯，可重現
# =====================================================================
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS flutter

# FROM 之後要重新宣告才能在該 stage 使用（Docker 作用域規則）
ARG FLUTTER_VERSION

ENV DEBIAN_FRONTEND=noninteractive \
    FLUTTER_ROOT=/opt/flutter \
    PATH="/opt/flutter/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        unzip \
        xz-utils \
        zip \
        libglu1-mesa \
    && rm -rf /var/lib/apt/lists/*

# 下載官方指定版本的 Flutter SDK
RUN curl -fsSL -o /tmp/flutter.tar.xz \
        "https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_${FLUTTER_VERSION}-stable.tar.xz" \
    && mkdir -p /opt \
    && tar -xf /tmp/flutter.tar.xz -C /opt \
    && rm -f /tmp/flutter.tar.xz \
    && git config --global --add safe.directory /opt/flutter \
    && flutter --version \
    && flutter precache --web \
    && flutter config --no-analytics

WORKDIR /app

# 先只複製 pubspec 與 lock 檔，讓 Docker 快取套件下載結果
COPY frontend/pubspec.yaml frontend/pubspec.lock ./frontend/
WORKDIR /app/frontend
RUN flutter pub get

# 再複製其餘原始碼並編譯
# ★ --base-href 的 Ingress 陷阱：
#   Flutter 預設產出 <base href="/">，瀏覽器會去 HA 根目錄抓
#   flutter_bootstrap.js / main.dart.js，在 Ingress 底下一定 404，
#   頁面就會一片空白（容器日誌只會看到 GET /index.html 200，
#   完全沒有 JS 請求，因為請求根本沒打到容器）。
#   解法：編譯後把 base 改成 "./"，讓所有資源都用相對路徑，
#   不管是 Ingress 子路徑還是直接連 http://ip:8099 都能載入。
COPY frontend/ ./
RUN flutter build web --release --base-href=/ \
    && sed -i 's|<base href="/">|<base href="./">|' build/web/index.html \
    && grep -q '<base href="./">' build/web/index.html \
    && test -f build/web/index.html \
    && test -f build/web/flutter_bootstrap.js \
    && echo "=== Flutter build 完成（base href 已改為相對路徑） ===" \
    && ls -la build/web

# =====================================================================
# Stage 2: 最終執行映像
#
# ★ 為什麼用 ghcr.io/home-assistant/*-base 而不是 nginx:alpine？
#   Home Assistant 官方維護的 base image 已經內建 s6-overlay v3，
#   而且「/init」就是 s6 的啟動入口，Supervisor 預期這個行為。
#   自己用 alpine 的 s6-overlay 套件容易踩到路徑不同的坑。
#   官方 base 也已經有 nginx、python3、curl，省掉很多安裝步驟。
#
# ★ BASE_IMAGE 在檔案開頭宣告（第一個 FROM 之前），
#   不同 CPU 架構要用不同的 base image。HA 的 Supervisor 建置時
#   會透過 build_from 自動傳入對應的 ARG。
#   本機手動 docker build 時則預設用 amd64。
# =====================================================================
FROM ${BASE_IMAGE} AS runtime

LABEL \
    io.hass.name="NR-MC 控制台" \
    io.hass.description="NR-MC 系列 WiFi 新風控制器管理面板" \
    io.hass.type="addon" \
    io.hass.version="1.0.2"

# 官方 base 已含 s6-overlay、nginx、python3、curl。
# 這裡只需要補上時區資料與 pip。
COPY backend/requirements.txt /tmp/requirements.txt

RUN apk add --no-cache \
        nginx \
        tzdata \
        py3-pip \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        -r /tmp/requirements.txt \
    && rm -rf /root/.cache /tmp/requirements.txt

# 時區設定（日誌顯示用）
ENV TZ=Asia/Taipei

# Nginx 設定
COPY nrmc_panel/nginx/nginx.conf /etc/nginx/nginx.conf

# Python 後端
WORKDIR /app
COPY backend/nrmc_panel/ ./nrmc_panel/
COPY backend/entrypoint.py ./

# Flutter Web 產物（來自 stage 1）
COPY --from=flutter /app/frontend/build/web/ /usr/share/nginx/html/

# s6 開機腳本：啟動 nginx 與 Flask
COPY nrmc_panel/rootfs/ /

# ★ 權限與換行符修正（Windows 開發者必看）
#   1. chmod +x：Windows 檔案系統沒有 Unix 執行權限欄位，
#      git 也無法保留，所以必須在 Dockerfile 裡設定。
#   2. 刪除 CRLF：Linux 的 shebang 要求第一行完全正確，
#      若檔案是 CRLF 換行，會導致
#      「bad interpreter: /usr/bin/with-contenv^M」而啟動失敗。
RUN chmod +x \
        /etc/services.d/nginx/run \
        /etc/services.d/backend/run \
    && find /etc/services.d -type f -name 'run' -exec sed -i 's/\r$//' {} \; \
    && echo "=== s6 腳本權限與換行符修正完成 ==="

# 資料目錄（設備清單會存在這裡）
RUN mkdir -p /data && chmod 755 /data

# 檢查：確認前端與後端都在
RUN test -f /usr/share/nginx/html/index.html \
    && test -f /app/nrmc_panel/app.py \
    && echo "=== Runtime 映像檢查通過 ==="

EXPOSE 8099

# 健康檢查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8099/api/health || exit 1

# ★ 不要寫 CMD 或 ENTRYPOINT：沿用官方 base 內建的 ENTRYPOINT ["/init"]。
#   若再加 CMD ["/init"]，實際執行的會是 "/init /init"，
#   裡層的 /init 因不是 PID 1 會直接報
#   "s6-overlay-suexec: fatal: can only run as pid 1" 並以 exit 100 退出。
