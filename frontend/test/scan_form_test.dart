/// 掃描表單的欄位初始值測試。
///
/// 驗證：
///   1. AT port 欄位初始值為 12345（不是空字串）
///   2. 網段欄位初始值為空（留空代表自動偵測）
///   3. 按下「開始掃描」時用的是畫面上實際的輸入值
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:nrmc_panel/api/api_client.dart';
import 'package:nrmc_panel/pages/scan_page.dart';
import 'package:nrmc_panel/state/app_state.dart';
import 'package:provider/provider.dart';

void main() {
  /// 建立一個 AppState，並記錄送出的掃描請求。
  Future<(AppState, List<http.Request>)> buildState() async {
    final requests = <http.Request>[];
    final client = MockClient((http.Request req) async {
      requests.add(req);
      if (req.url.path == '/api/scan/status') {
        return http.Response(
          '{"running": false, "progress": 0, "total": 0, '
          '"message": "完成", "results": []}',
          200,
        );
      }
      return http.Response('{"ok": true}', 200);
    });
    final state = AppState(
      api: ApiClient(client: client, baseUrlOverride: 'http://test.local'),
    );
    return (state, requests);
  }

  testWidgets('★ AT port 欄位初始值應為 12345', (tester) async {
    final (state, _) = await buildState();

    await tester.pumpWidget(
      ChangeNotifierProvider<AppState>.value(
        value: state,
        child: MaterialApp(home: Scaffold(body: ScanPage())),
      ),
    );
    await tester.pump();

    // 找到 AT port 的輸入框
    final portField = find.widgetWithText(TextField, 'AT port');
    expect(portField, findsOneWidget);

    final textField = tester.widget<TextField>(portField);
    final controller = textField.controller;
    expect(controller, isNotNull);
    expect(
      controller!.text,
      '12345',
      reason: 'AT port 欄位初始值應為 12345，不能是空字串',
    );
  });

  testWidgets('網段欄位初始值為空', (tester) async {
    final (state, _) = await buildState();

    await tester.pumpWidget(
      ChangeNotifierProvider<AppState>.value(
        value: state,
        child: MaterialApp(home: Scaffold(body: ScanPage())),
      ),
    );
    await tester.pump();

    final subnetField = find.widgetWithText(TextField, '網段');
    final textField = tester.widget<TextField>(subnetField);
    expect(textField.controller!.text, '');
  });

  testWidgets('★ 按下開始掃描時使用畫面上實際的輸入值', (tester) async {
    final (state, requests) = await buildState();
    addTearDown(state.dispose);

    await tester.pumpWidget(
      ChangeNotifierProvider<AppState>.value(
        value: state,
        child: MaterialApp(home: Scaffold(body: ScanPage())),
      ),
    );
    await tester.pump();

    // 展開 Mock 模式
    await tester.tap(find.text('Mock 模式（直接指定主機）'));
    await tester.pumpAndSettle();

    // 在主機清單欄位輸入
    final targetsField = find.widgetWithText(TextField, '主機清單');
    expect(targetsField, findsOneWidget);
    await tester.enterText(targetsField, '127.0.0.1');
    await tester.pump();

    // 按下開始掃描
    await tester.tap(find.text('開始掃描'));
    await tester.pump();

    // 應該送出 targets
    final startReq = requests.firstWhere((r) => r.url.path == '/api/scan/start');
    expect(startReq.body, contains('127.0.0.1'));
    expect(startReq.body, contains('targets'));
    // port 也應該帶上去
    expect(startReq.body, contains('12345'));

    // 停止掃描輪詢，避免測試結束後仍有 pending timer
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(milliseconds: 600));
  });
}
