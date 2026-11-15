"""NR-MC 控制台後端套件。

以純文字模式設計，讓 HA Add-on 容器內的 Python 與 Windows 開發環境
使用完全相同的程式碼。

主要模組：
    at_client     : AT over TCP 通訊客戶端
    mock_device   : Mock AT 設備模擬器（開發與測試用）
    scanner       : 網段掃描
    device_store  : 設備清單持久化
    api           : REST API 路由
    ws            : WebSocket 推播
    app           : Flask 應用進入點
"""

__version__ = "1.0.4"
