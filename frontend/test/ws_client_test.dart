/// WebSocket 推播機制的測試。
///
/// 驗證「WebSocket 是加速器，輪詢是保底」這個設計：
///   - wsUrl 的路徑換算正確（Ingress 前綴要保留）
///   - 推播事件能正確套用到設備狀態
///   - WebSocket 連不上時不影響主要功能
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:nrmc_panel/api/api_client.dart';
import 'package:nrmc_panel/api/ws_client.dart';

void main() {
  group('ApiClient.wsUrl', () {
    test('開發模式（無 Ingress）應該用根路徑', () {
      final api = ApiClient(baseUrlOverride: 'http://127.0.0.1:8080');
      expect(api.wsUrl, 'ws://127.0.0.1:8080/ws');
    });

    test('★ http 應該轉成 ws', () {
      final api = ApiClient(baseUrlOverride: 'http://192.168.1.10:8099');
      expect(api.wsUrl, 'ws://192.168.1.10:8099/ws');
    });

    test('★ https 應該轉成 wss', () {
      final api = ApiClient(baseUrlOverride: 'https://ha.local:8123');
      expect(api.wsUrl, 'wss://ha.local:8123/ws');
    });

    test('★ Ingress 前綴必須保留，不能只取 /api', () {
      final api = ApiClient(
        baseUrlOverride:
            'https://ha.local:8123/api/hassio_ingress/abc123/nrmc_panel',
      );
      expect(
        api.wsUrl,
        'wss://ha.local:8123/api/hassio_ingress/abc123/nrmc_panel/ws',
      );
    });

    test('尾端斜線不應該產生重複的斜線', () {
      final api = ApiClient(
        baseUrlOverride:
            'https://ha.local:8123/api/hassio_ingress/abc123/nrmc_panel/',
      );
      expect(
        api.wsUrl,
        'wss://ha.local:8123/api/hassio_ingress/abc123/nrmc_panel/ws',
      );
    });
  });

  group('WsClient', () {
    test('★ 初始狀態是 idle', () {
      final ws = WsClient();
      expect(ws.status, WsStatus.idle);
      expect(ws.clientCount, 0);
      ws.dispose();
    });

    test('★ 連線失敗時狀態會變成 disconnected 並自動重試', () async {
      final ws = WsClient();
      final statuses = <WsStatus>[];

      // ★ 用一個保證不會有服務在聽的埠號
      //   （1 是 reserved port，連線會被立即拒絕）
      ws.connect(
        url: 'ws://127.0.0.1:1/ws',
        onEvent: (_) {},
        onStatus: statuses.add,
      );

      // 等待連線嘗試失敗
      await Future<void>.delayed(const Duration(seconds: 3));

      expect(ws.status, WsStatus.disconnected);
      expect(statuses, contains(WsStatus.connecting));
      expect(statuses, contains(WsStatus.disconnected));
      expect(ws.clientCount, 0);

      ws.dispose();
    });

    test('★ dispose 後不再重連', () async {
      final ws = WsClient();
      ws.connect(
        url: 'ws://127.0.0.1:1/ws',
        onEvent: (_) {},
      );

      await Future<void>.delayed(const Duration(milliseconds: 200));
      ws.dispose();

      final statusAfterDispose = ws.status;
      await Future<void>.delayed(const Duration(milliseconds: 500));

      // dispose 後狀態不應該再變（沒有新的重連嘗試）
      expect(ws.status, statusAfterDispose);
    });

    test('disconnect 會把狀態設為 disconnected', () {
      final ws = WsClient();
      ws.connect(
        url: 'ws://127.0.0.1:1/ws',
        onEvent: (_) {},
      );
      ws.disconnect();
      expect(ws.status, WsStatus.disconnected);
      ws.dispose();
    });
  });

  group('Device 狀態', () {
    test('★ doStates 要能從推播的 state 解析出來', () {
      final device = Device(
        id: 1,
        host: '192.168.30.250',
        port: 12345,
        name: '新風機',
        model: 'NR-MC2201-WIFI',
        state: const {
          'do': {1: true, 2: false},
        },
      );

      final doStates = device.doStates;
      expect(doStates[1], isTrue);
      expect(doStates[2], isFalse);
    });

    test('★ 沒有 do 欄位時回傳空 map', () {
      final device = Device(
        id: 1,
        host: '192.168.30.250',
        port: 12345,
        name: '新風機',
        model: 'NR-MC2201-WIFI',
        state: const {},
      );

      expect(device.doStates, isEmpty);
    });
  });
}
