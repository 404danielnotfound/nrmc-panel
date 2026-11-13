/// WebSocket 連線管理。
///
/// ★ 這個類別的角色是「加速器」，不是「必需品」
/// ---------------------------------------------------------------------------
/// 整體的即時更新機制有兩層：
///
///   1. **輪詢（保底）**：每 5 秒問一次後端「現在狀態怎樣？」
///      - 一定會運作，不依賴任何額外設定
///      - 延遲最多 5 秒
///
///   2. **WebSocket（加速）**：後端狀態一變就主動推過來
///      - 延遲幾乎 0
///      - ★ 需要 Home Assistant 的 `ingress_stream: true` 才會被放行
///
/// 所以當 WebSocket 連不上時（例如下面兩種情況）：
///   - 使用者用直接連線的方式開啟（http://ip:8099），沒有 Ingress
///   - Ingress 沒有設定 ingress_stream，或被中間的防火牆擋掉
///
/// 應用程式**完全正常運作**，只是畫面更新會慢最多 5 秒。
///
/// ★ 底層實作為什麼分流？
///   `package:web` 只在瀏覽器環境可用，Flutter 測試跑在 VM 上會編譯失敗。
///   底下用條件式匯入處理，詳見 ws_transport.dart。
library;

import 'dart:async';
import 'dart:convert';

import 'ws_transport.dart';

/// WebSocket 連線狀態。
enum WsStatus {
  /// 尚未嘗試連線。
  idle,

  /// 正在連線。
  connecting,

  /// 已連線，可以接收推播。
  connected,

  /// 連線失敗或已斷線（會自動重試）。
  disconnected,
}

/// WebSocket 狀態推播的客戶端。
///
/// 用法：
/// ```dart
/// final ws = WsClient();
/// ws.connect(
///   url: 'ws://host:8099/ws',
///   onEvent: (event) { ... },
///   onStatus: (status) { ... },
/// );
/// ```
class WsClient {
  WsClient();

  WebSocketHandle? _socket;
  Timer? _reconnectTimer;
  Timer? _heartbeatTimer;

  String? _url;
  void Function(Map<String, dynamic>)? _onEvent;
  void Function(WsStatus)? _onStatus;

  WsStatus _status = WsStatus.idle;
  bool _disposed = false;

  /// 重連的退避時間上限。
  static const Duration _maxBackoff = Duration(seconds: 30);

  int _retryCount = 0;

  /// 目前連線狀態。
  WsStatus get status => _status;

  /// 目前已連線的 WebSocket 數量（永遠是 0 或 1）。
  int get clientCount => _status == WsStatus.connected ? 1 : 0;

  /// 建立連線。
  ///
  /// [url] 必須是完整的 ws:// 或 wss:// 網址。
  /// [onEvent] 收到推播時呼叫。
  /// [onStatus] 連線狀態變化時呼叫。
  void connect({
    required String url,
    required void Function(Map<String, dynamic>) onEvent,
    void Function(WsStatus)? onStatus,
  }) {
    _url = url;
    _onEvent = onEvent;
    _onStatus = onStatus;
    _retryCount = 0;
    _open();
  }

  void _setStatus(WsStatus status) {
    if (_status == status) return;
    _status = status;
    if (!_disposed) {
      _onStatus?.call(status);
    }
  }

  void _open() {
    if (_disposed || _url == null) return;

    _reconnectTimer?.cancel();
    _setStatus(WsStatus.connecting);

    try {
      final socket = createWebSocket(_url!);
      _socket = socket;

      socket.bind(WebSocketEvents(
        onOpen: () {
          if (_disposed) {
            socket.close();
            return;
          }
          _retryCount = 0;
          _setStatus(WsStatus.connected);
          _startHeartbeat();
        },
        onMessage: _handleMessage,
        onError: _scheduleReconnect,
        onClose: _scheduleReconnect,
      ));
    } catch (error) {
      // 建連本身就拋錯（例如網址格式錯誤）→ 重試
      _scheduleReconnect();
    }
  }

  void _handleMessage(String message) {
    Map<String, dynamic> data;
    try {
      final decoded = jsonDecode(message);
      if (decoded is! Map) return;
      data = Map<String, dynamic>.from(decoded);
    } catch (_) {
      return;
    }

    // pong 是心跳回應，不需要通知 UI
    if (data['type'] == 'pong') return;

    _onEvent?.call(data);
  }

  /// 啟動心跳。
  ///
  /// ★ 為什麼需要心跳？
  ///   中間的代理裝置（Ingress、NAT、防火牆）常會把閒置一段時間的
  ///   TCP 連線關掉。心跳讓連線保持活著，並讓後端知道前端還在。
  void _startHeartbeat() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = Timer.periodic(const Duration(seconds: 25), (_) {
      final socket = _socket;
      if (socket == null) return;
      try {
        socket.send(jsonEncode({'type': 'ping'}));
      } catch (_) {
        // 送出失敗代表連線已斷，交由 onError 處理重連
      }
    });
  }

  /// 安排重新連線（指數退避）。
  void _scheduleReconnect() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _socket = null;

    if (_disposed) return;

    _setStatus(WsStatus.disconnected);

    // 指數退避：1s, 2s, 4s, 8s ... 上限 30s
    final seconds =
        (1 << _retryCount.clamp(0, 5)).clamp(1, _maxBackoff.inSeconds);
    _retryCount++;

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(Duration(seconds: seconds), _open);
  }

  /// 中斷連線（不再重試）。
  void disconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;

    final socket = _socket;
    _socket = null;
    try {
      socket?.close();
    } catch (_) {
      // 關閉失敗也沒關係，反正狀態已經清掉
    }

    _setStatus(WsStatus.disconnected);
  }

  /// 釋放資源。
  void dispose() {
    _disposed = true;
    disconnect();
  }
}
