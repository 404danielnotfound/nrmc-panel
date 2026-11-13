/// 前端 widget 測試。
///
/// 驗證三個頁面能正確渲染，以及互鎖相關的 UI 邏輯。
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:nrmc_panel/api/api_client.dart';
import 'package:nrmc_panel/pages/devices_page.dart';
import 'package:nrmc_panel/pages/scan_page.dart';
import 'package:nrmc_panel/pages/test_page.dart';
import 'package:nrmc_panel/state/app_state.dart';
import 'package:provider/provider.dart';

/// 建立一個回傳固定 JSON 的 mock HTTP 客戶端。
///
/// handler 會收到 (path, body)，方便依請求內容決定回應。
MockClient _mockClient(String Function(String path, String body) handler) {
  return MockClient((http.Request req) async {
    final body = handler(req.url.path, req.body);
    return http.Response(
      body.isEmpty ? '{}' : body,
      200,
      headers: {'content-type': 'application/json; charset=utf-8'},
    );
  });
}

/// 產生一個假設備的 JSON。
String _deviceJson({
  int id = 1,
  String host = '127.0.0.1',
  int port = 12345,
  String name = 'EXFAN_2201',
  bool do1 = false,
  bool do2 = false,
  bool connected = true,
}) {
  return '''
  {
    "id": $id,
    "host": "$host",
    "port": $port,
    "name": "$name",
    "model": "NR-MC2201-WIFI",
    "mac": "",
    "connected": $connected,
    "state": ${_doStateJson(do1: do1, do2: do2, connected: connected)}
  }
  ''';
}

/// 產生只有 IO 狀態的 JSON（對應 /do 回應裡的 "state" 欄位）。
String _doStateJson({
  bool do1 = false,
  bool do2 = false,
  bool connected = true,
}) {
  return '''
  {
    "connected": $connected,
    "name": "EXFAN_2201",
    "model": "NR-MC2201-WIFI",
    "firmware": "NR-MC2201-WIFI-VER30.3",
    "do": {"1": $do1, "2": $do2},
    "di": {"1": false, "2": false},
    "ai": {},
    "error": ""
  }
  ''';
}

/// 把頁面包在 Provider 中。
Widget _wrap(Widget page, AppState state) {
  return ChangeNotifierProvider<AppState>.value(
    value: state,
    child: MaterialApp(home: Scaffold(body: page)),
  );
}

/// 建立一個已載入設備清單的 AppState。
Future<AppState> _loadedState(
  String Function(String path, String body) handler,
) async {
  final api = ApiClient(
    client: _mockClient(handler),
    baseUrlOverride: 'http://test.local',
  );
  final state = AppState(api: api);
  await state.refreshDevices();
  return state;
}

void main() {
  group('AppState', () {
    test('載入設備清單', () async {
      final state = await _loadedState((path, body) {
        if (path == '/api/devices') {
          return '{"devices": [${_deviceJson()}]}';
        }
        return '{}';
      });

      expect(state.devices, hasLength(1));
      expect(state.devices.first.name, 'EXFAN_2201');
      expect(state.deviceState, LoadState.ready);
      expect(state.deviceError, isEmpty);
    });

    test('解析 DO 通道狀態', () async {
      final state = await _loadedState((path, body) {
        if (path == '/api/devices') {
          return '{"devices": [${_deviceJson(do1: true, do2: false)}]}';
        }
        return '{}';
      });

      final doStates = state.devices.first.doStates;
      expect(doStates[1], isTrue);
      expect(doStates[2], isFalse);
    });

    test('切換檔位後回讀狀態反映互鎖結果', () async {
      // 後端模擬硬體互鎖：開一檔會關掉二檔，反之亦然
      var do1 = false;
      var do2 = false;
      final state = await _loadedState((path, body) {
        if (path == '/api/devices') {
          return '{"devices": [${_deviceJson(do1: do1, do2: do2)}]}';
        }
        if (path.endsWith('/do')) {
          // 依實際請求的通道與值更新，再套用互鎖
          final req = jsonDecode(body) as Map<String, dynamic>;
          final channel = req['channel'] as int;
          final on = req['on'] == true;
          if (channel == 1) {
            do1 = on;
            if (on) do2 = false;
          } else {
            do2 = on;
            if (on) do1 = false;
          }
          return '{"ok": true, "state": ${_doStateJson(do1: do1, do2: do2)}}';
        }
        return '{}';
      });

      // 開一檔
      await state.setDo(1, channel: 1, on: true);
      expect(state.devices.first.doStates[1], isTrue);
      expect(state.devices.first.doStates[2], isFalse);

      // 切到二檔 → 一檔應自動關閉
      await state.setDo(1, channel: 2, on: true);
      final doStates = state.devices.first.doStates;
      expect(doStates[2], isTrue);
      expect(doStates[1], isFalse, reason: '互鎖失效');
    });

    test('API 錯誤會被記錄而不是拋出', () async {
      final api = ApiClient(
        client: MockClient((http.Request req) async {
          return http.Response('{"error": "找不到此設備"}', 404);
        }),
        baseUrlOverride: 'http://test.local',
      );
      final state = AppState(api: api);

      await state.refreshDevices();

      expect(state.deviceState, LoadState.error);
      expect(state.deviceError, contains('找不到此設備'));
    });
  });

  group('掃描參數', () {
    test('★ 輸入主機清單後會送出 targets（Mock 模式）', () async {
      // 記錄送出的請求內容
      String? sentBody;
      String? sentPath;
      final api = ApiClient(
        client: MockClient((http.Request req) async {
          sentPath = req.url.path;
          sentBody = req.body;
          if (req.url.path == '/api/scan/status') {
            return http.Response(
              '{"running": false, "progress": 1, "total": 1, '
              '"message": "完成", "results": []}',
              200,
            );
          }
          return http.Response('{"ok": true}', 200);
        }),
        baseUrlOverride: 'http://test.local',
      );
      final state = AppState(api: api);

      // 模擬使用者輸入主機清單（這正是 TextField onChanged 做的事）
      state.mockTargets = '127.0.0.1, 192.168.30.250';
      state.portInput = '12345';

      await state.startScan();

      expect(sentPath, '/api/scan/start');
      expect(sentBody, isNotNull);
      final body = jsonDecode(sentBody!) as Map<String, dynamic>;
      expect(body['targets'], ['127.0.0.1', '192.168.30.250']);
      expect(body['port'], 12345);
    });

    test('主機清單留空時不送 targets（改走真實網段掃描）', () async {
      String? sentBody;
      final api = ApiClient(
        client: MockClient((http.Request req) async {
          if (req.url.path == '/api/scan/start') {
            sentBody = req.body;
            return http.Response('{"ok": true}', 200);
          }
          return http.Response(
            '{"running": false, "progress": 0, "total": 0, '
            '"message": "完成", "results": []}',
            200,
          );
        }),
        baseUrlOverride: 'http://test.local',
      );
      final state = AppState(api: api);

      state.mockTargets = '';
      await state.startScan();

      final body = jsonDecode(sentBody!) as Map<String, dynamic>;
      expect(body.containsKey('targets'), isFalse);
    });

    test('主機清單只有空白與逗號時視為未填', () async {
      String? sentBody;
      final api = ApiClient(
        client: MockClient((http.Request req) async {
          if (req.url.path == '/api/scan/start') {
            sentBody = req.body;
            return http.Response('{"ok": true}', 200);
          }
          return http.Response(
            '{"running": false, "progress": 0, "total": 0, '
            '"message": "完成", "results": []}',
            200,
          );
        }),
        baseUrlOverride: 'http://test.local',
      );
      final state = AppState(api: api);

      state.mockTargets = ' , , ';
      await state.startScan();

      final body = jsonDecode(sentBody!) as Map<String, dynamic>;
      expect(body.containsKey('targets'), isFalse);
    });
  });

  group('頁面渲染', () {
    testWidgets('裝置總覽頁顯示設備名稱', (tester) async {
      final state = await _loadedState((path, body) {
        if (path == '/api/devices') {
          return '{"devices": [${_deviceJson()}]}';
        }
        return '{}';
      });

      await tester.pumpWidget(_wrap(const DevicesPage(), state));
      await tester.pump();

      expect(find.text('EXFAN_2201'), findsOneWidget);
      expect(find.text('裝置總覽'), findsOneWidget);
    });

    testWidgets('裝置總覽頁在沒有設備時顯示空狀態', (tester) async {
      final state = await _loadedState((path, body) => '{"devices": []}');

      await tester.pumpWidget(_wrap(const DevicesPage(), state));
      await tester.pump();

      expect(find.text('尚未加入任何設備'), findsOneWidget);
    });

    testWidgets('測試頁顯示檔位按鈕與互鎖說明', (tester) async {
      final state = await _loadedState((path, body) {
        if (path == '/api/devices') {
          return '{"devices": [${_deviceJson()}]}';
        }
        return '{}';
      });

      await tester.pumpWidget(_wrap(const TestPage(), state));
      await tester.pump();

      expect(find.text('風機檔位'), findsOneWidget);
      expect(find.text('一檔'), findsOneWidget);
      expect(find.text('二檔'), findsOneWidget);
      expect(find.text('關閉'), findsOneWidget);
      expect(find.textContaining('硬體互鎖'), findsOneWidget);
    });

    testWidgets('掃描頁顯示表單與進度區塊', (tester) async {
      final state = await _loadedState((path, body) => '{}');

      await tester.pumpWidget(_wrap(const ScanPage(), state));
      await tester.pump();

      expect(find.text('網路掃描'), findsOneWidget);
      expect(find.text('開始掃描'), findsOneWidget);
      expect(find.text('網段'), findsOneWidget);
    });
  });
}
