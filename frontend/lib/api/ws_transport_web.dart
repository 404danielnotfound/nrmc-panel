/// WebSocket 連線的底層實作（Web 版本）。
///
/// ★ 為什麼要分檔？
///   `package:web` 的 WebSocket 只在瀏覽器環境存在，
///   在 Flutter 測試（VM 環境）會編譯失敗。
///   所以用條件式匯入（conditional import）分流：
///     - 網頁版 → 這支檔案，用 package:web
///     - 其他平台 → ws_transport_io.dart，用 dart:io
///
/// 對上層的 `WsClient` 來說，兩邊提供的 API 完全一樣。
library;

import 'dart:js_interop';

import 'package:web/web.dart' as web;

/// 建立一個 WebSocket 連線。
WebSocketHandle createWebSocket(String url) {
  return _WebSocketHandle(web.WebSocket(url));
}

/// WebSocket 的事件回呼。
class WebSocketEvents {
  const WebSocketEvents({
    required this.onOpen,
    required this.onMessage,
    required this.onError,
    required this.onClose,
  });

  final void Function() onOpen;
  final void Function(String message) onMessage;
  final void Function() onError;
  final void Function() onClose;
}

/// WebSocket 連線句柄。
abstract class WebSocketHandle {
  /// 綁定事件處理器。
  void bind(WebSocketEvents events);

  /// 送出文字訊息。
  void send(String message);

  /// 關閉連線。
  void close();
}

class _WebSocketHandle implements WebSocketHandle {
  _WebSocketHandle(this._socket);

  final web.WebSocket _socket;

  @override
  void bind(WebSocketEvents events) {
    _socket.onopen = ((web.Event _) {
      events.onOpen();
    }).toJS;

    _socket.onmessage = ((web.MessageEvent event) {
      final data = event.data;
      if (data.isA<JSString>()) {
        events.onMessage((data as JSString).toDart);
      }
    }).toJS;

    _socket.onerror = ((web.Event _) {
      events.onError();
    }).toJS;

    _socket.onclose = ((web.Event _) {
      events.onClose();
    }).toJS;
  }

  @override
  void send(String message) {
    _socket.send(message.toJS);
  }

  @override
  void close() {
    _socket.close();
  }
}
