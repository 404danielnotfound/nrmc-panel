"""Add-on 打包設定的驗證測試。

這些測試在「沒有 Docker 的環境」也能跑，用來確保：
    - config.yaml / build.yaml / repository.yaml 格式正確
    - 必要的欄位都有設定
    - s6 腳本的換行符是 Linux 需要的 LF
    - .gitignore 確實排除了不該進版控的檔案
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent.parent
ADDON_DIR = ROOT / "addons" / "nrmc_panel"


@pytest.fixture(scope="module")
def config():
    """讀取 Add-on 的 config.yaml。"""
    path = ADDON_DIR / "config.yaml"
    assert path.is_file(), f"找不到 {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def build_config():
    """讀取 Add-on 的 build.yaml。"""
    path = ADDON_DIR / "build.yaml"
    assert path.is_file(), f"找不到 {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# config.yaml
# ----------------------------------------------------------------------

def test_config_has_required_fields(config):
    """必要欄位都要有。"""
    for field in ("name", "version", "slug", "description",
                  "arch", "ingress_port", "image"):
        assert field in config, f"config.yaml 缺少 {field}"


def test_config_ingress_entry(config):
    """★ Ingress 入口必須指向前端的 index.html。

    ingress_entry: index.html 讓 HA 的「開啟 Web UI」
    直接載入 Flutter 編譯產物。缺了會看到空白頁或 404。
    """
    assert config.get("ingress") is True, "必須啟用 Ingress"
    assert config.get("ingress_entry") == "index.html", (
        f"ingress_entry 應為 index.html，目前是 {config.get('ingress_entry')}"
    )


def test_config_ingress_settings(config):
    """Ingress 與主機網路必須開啟。"""
    assert config["ingress"] is True, "必須啟用 Ingress"
    assert config["host_network"] is True, (
        "必須開啟主機網路，否則掃描不到區網內的設備"
    )


def test_config_ingress_stream_enabled(config):
    """★ ingress_stream 必須開啟，WebSocket 才會被放行。"""
    assert config.get("ingress_stream") is True, (
        "ingress_stream 必須為 true，否則 WebSocket 推播無法運作"
    )


def test_config_ports_match_ingress_port(config):
    """host_network 模式下不需要做連接埠對應。

    因為 config 已設 host_network: true，容器直接使用主機網路，
    ports 應為空（{}），8099 即為主機的 8099。舊版寫
    "8099/tcp": null 會跟 host_network 衝突。
    """
    assert config.get("host_network") is True
    ports = config.get("ports") or {}
    assert ports == {}, f"host_network 模式下 ports 應為空，目前是 {ports}"


def test_config_options_have_schema(config):
    """★ 每個 option 都必須在 schema 定義型別，否則 HA 會拒絕載入。"""
    options = config["options"]
    schema = config["schema"]
    assert set(options.keys()) == set(schema.keys()), (
        "options 與 schema 的鍵必須完全一致"
    )


def test_config_option_defaults_are_strings(config):
    """★ options 的預設值必須是字串。

    HA 的 Add-on schema 只接受字串型別的預設值，
    數字或布林會導致設定頁面顯示異常。
    """
    for name, value in config["options"].items():
        assert isinstance(value, str), (
            f"option '{name}' 的預設值必須是字串，目前是 "
            f"{type(value).__name__}"
        )


def test_config_at_port_default(config):
    """AT over TCP 預設埠號必須是 12345。"""
    assert config["options"]["at_port"] == "12345"


def test_config_log_level_in_schema_choices(config):
    """log_level 的可選值必須包含 info 與 debug。"""
    schema = config["schema"]["log_level"]
    choices = schema.replace("list(", "").replace(")", "").split("|")
    assert "info" in choices
    assert "debug" in choices


# ----------------------------------------------------------------------
# build.yaml
# ----------------------------------------------------------------------

def test_build_config_covers_all_arch(config, build_config):
    """★ build_from 必須涵蓋 config.yaml 宣告的所有架構。"""
    build_from = build_config["build_from"]
    for arch in config["arch"]:
        assert arch in build_from, f"build_from 缺少 {arch}"


def test_build_config_uses_ha_base_images(build_config):
    """★ base image 必須是 home-assistant 官方維護的。

    官方 base 內建 s6-overlay v3，Supervisor 才會正確啟動容器。
    """
    for arch, image in build_config["build_from"].items():
        assert "home-assistant" in image, (
            f"{arch} 的 base image 必須來自 home-assistant 官方：{image}"
        )


# ----------------------------------------------------------------------
# s6 腳本
# ----------------------------------------------------------------------

@pytest.mark.parametrize("script", [
    "nginx/run",
    "backend/run",
])
def test_s6_scripts_use_lf_line_endings(script):
    """★ s6 腳本必須是 LF 換行。

    Windows 用 CRLF 會讓 Linux 開機時報錯：
        bad interpreter: /usr/bin/with-contenv^M
    """
    path = ADDON_DIR / "rootfs" / "etc" / "services.d" / script
    assert path.is_file(), f"找不到 {path}"
    data = path.read_bytes()
    assert b"\r\n" not in data, (
        f"{script} 含 CRLF 換行符，會導致容器無法啟動"
    )


@pytest.mark.parametrize("script", [
    "nginx/run",
    "backend/run",
])
def test_s6_scripts_have_shebang(script):
    """s6 腳本必須有 with-contenv shebang。"""
    path = ADDON_DIR / "rootfs" / "etc" / "services.d" / script
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!/usr/bin/with-contenv bashio"), (
        f"{script} 的 shebang 應為 #!/usr/bin/with-contenv bashio"
    )


# ----------------------------------------------------------------------
# .gitignore / .dockerignore
# ----------------------------------------------------------------------

def test_gitignore_excludes_build_output():
    """★ frontend/build 必須被排除（40 MB 的編譯產物）。"""
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "frontend/build/" in content


def test_gitignore_excludes_env_file():
    """★ .env 含 Token，絕對不能進版控。"""
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in content


def test_gitignore_excludes_venv():
    """Python venv 不該進版控。"""
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".venv" in content


def test_dockerignore_excludes_frontend_build():
    """★ Docker 建置不能拿到本機的 frontend/build。

    否則會用到開發機上可能是舊版的產物，
    而不是從當下原始碼重新編譯的結果。
    """
    content = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "frontend/build" in content


# ----------------------------------------------------------------------
# Dockerfile
# ----------------------------------------------------------------------

def test_dockerfile_has_healthcheck():
    """Dockerfile 必須有健康檢查。"""
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in content


def test_dockerfile_copies_flutter_build():
    """Dockerfile 必須從 flutter stage 複製前端產物。"""
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--from=flutter" in content
    assert "build/web" in content


def test_dockerfile_fixes_line_endings():
    """★ Dockerfile 必須處理 CRLF 問題。

    Windows 開發者撰寫的 s6 腳本會是 CRLF，
    必須在映像建置時修正。
    """
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "sed -i 's/\\r$//'" in content
    assert "chmod +x" in content
