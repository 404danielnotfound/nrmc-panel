/// WebSocket 連線的底層實作（VM / IO 版本）。
///
/// ★ 為什麼需要這支檔案？
///   `package:web` 只在瀏覽器環境可用，
///   Flutter 測試跑在 VM 上會編譯失敗。
///   這支檔案改用 `dart:io` 的 WebSocket，
///   讓單元測試可以在 VM 上正常跑。
///
/// 正式部署時前端是編譯成 JavaScript 在瀏覽器執行，
/// 會走 ws_transport_web.dart，不會用到這支。
library;

import 'dart:async';
import 'dart:io';

/// 建立一個 WebSocket 連線。
///
/// ★ 注意：dart:io 的 WebSocket.connect 回傳 Future，
///   所以這裡不能同步拿到 socket。
///   解法是先用 _PendingSocket 包住，事件綁定等連線建立後再補上。
WebSocketHandle createWebSocket(String url) {
  return _WebSocketHandle(url);
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
  _WebSocketHandle(this._url);

  final String _url;
  WebSocket? _socket;
  WebSocketEvents? _events;
  Timer? _connectTimer;

  @override
  void bind(WebSocketEvents events) {
    _events = events;

    try {
      final future = WebSocket.connect(_url);
      future.then(
        (socket) {
          // 綁定事件之前可能已經被 close() 了
          if (_events == null) {
            socket.close();
            return;
          }
          _socket = socket;
          _attach(socket, events);
        },
        // ★ dart:io 的 Future.then 的 onError 必須接受 (Object, StackTrace)
        onError: (Object error, StackTrace stack) => events.onError(),
      );

      // ★ 連線逾時保護
      //   某些環境（例如被防火牆丟包的目標）會讓連線停在 pending 狀態，
      //   既不成功也不失敗。沒有逾時的話，狀態會永遠卡在 connecting。
      _connectTimer = Timer(const Duration(seconds: 10), () {
        if (_socket == null && _events != null) {
          events.onError();
        }
      });
    } catch (_) {
      events.onError();
    }
  }

  void _attach(WebSocket socket, WebSocketEvents events) {
    _connectTimer?.cancel();
    _connectTimer = null;

    socket.listen(
      (dynamic message) {
        if (message is String) {
          events.onMessage(message);
        }
      },
      onDone: events.onClose,
      onError: (Object _) => events.onError(),
      cancelOnError: true,
    );
    events.onOpen();
  }

  @override
  void send(String message) {
    final socket = _socket;
    if (socket == null) return;
    try {
      socket.add(message);
    } catch (_) {
      // 送出失敗代表連線已斷
    }
  }

  @override
  void close() {
    _connectTimer?.cancel();
    _connectTimer = null;
    _events = null;
    final socket = _socket;
    _socket = null;
    try {
      socket?.close();
    } catch (_) {
      // 關閉失敗也沒關係
    }
  }
}
