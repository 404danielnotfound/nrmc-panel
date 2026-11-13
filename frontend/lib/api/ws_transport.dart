/// WebSocket 底層實作的条件式匯入。
///
/// Flutter Web 編譯成 JavaScript 時走 `ws_transport_web.dart`（package:web）。
/// 單元測試跑在 VM 上時走 `ws_transport_io.dart`（dart:io）。
///
/// 對上層來說 API 完全相同，看不到這個分流的存在。
library;

export 'ws_transport_io.dart'
    if (dart.library.js_interop) 'ws_transport_web.dart';
