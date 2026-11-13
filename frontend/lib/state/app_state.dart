/// 應用程式狀態管理。
///
/// 使用 ChangeNotifier（Flutter 內建，不需要額外套件）管理：
///   - 設備清單
///   - 掃描狀態
///   - 輪詢計時器
///
/// ★ 為什麼「輪詢」是主要的即時機制？
///   WebSocket 推播更快，但 Home Assistant 的 Ingress 需要
///   `ingress_stream: true` 才會放行 WebSocket。
///   因此這裡以輪詢為主要機制，WebSocket 是可選的加速手段。
///   這樣在純 Nginx + 靜態檔的環境下也能正常運作。
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../api/ws_client.dart';

enum LoadState { idle, loading, ready, error }

class AppState extends ChangeNotifier {
  AppState({ApiClient? api}) : api = api ?? ApiClient();

  final ApiClient api;

  // -- 設備 -------------------------------------------------------------
  List<Device> devices = [];
  LoadState deviceState = LoadState.idle;
  String deviceError = '';

  /// 正在操作中的設備 id（用來停用按鈕、顯示 loading）。
  int? busyDeviceId;

  // -- WebSocket --------------------------------------------------------
  ///
  /// ★ 這是「加速器」，不是必需品。
  ///   連不上時應用程式照常運作，只是更新會慢最多 5 秒（等輪詢）。
  final WsClient ws = WsClient();

  /// WebSocket 連線狀態（供 UI 顯示指示燈）。
  WsStatus wsStatus = WsStatus.idle;

  /// 連續收到推播的次數（診斷用）。
  int wsEventCount = 0;

  // -- 掃描 -------------------------------------------------------------
  ScanStatus scan = ScanStatus(
    running: false,
    progress: 0,
    total: 0,
    message: '尚未開始掃描',
    results: const [],
  );
  LoadState scanState = LoadState.idle;
  String scanError = '';

  /// 掃描的網段輸入值。
  String subnetInput = '';

  /// 掃描的 port 輸入值。
  String portInput = '12345';

  /// ★ Mock 模式：指定要查詢的主機（逗號分隔），留空則做真實網段掃描。
  String mockTargets = '';

  Timer? _pollTimer;
  bool _disposed = false;

  /// 啟動定期輪詢。
  ///
  /// ★ 這是「保底機制」，一定要開。
  ///   即使 WebSocket 完全連不上，畫面也會靠這個更新（最多慢 5 秒）。
  void startPolling({Duration interval = const Duration(seconds: 5)}) {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(interval, (_) => refreshDevices());
  }

  /// 啟動 WebSocket 推播連線。
  ///
  /// ★ 這是「加速機制」，失敗也沒關係。
  ///   - 在 Home Assistant 底下需要 `ingress_stream: true` 才會通
  ///   - 直接連線（http://ip:8099）也會通
  ///   - 完全連不上時，輪詢仍然正常運作
  void startWebSocket() {
    final url = api.wsUrl;
    if (url == null || url.isEmpty) {
      wsStatus = WsStatus.idle;
      return;
    }

    ws.connect(
      url: url,
      onEvent: _handleWsEvent,
      onStatus: (status) {
        wsStatus = status;
        _safeNotify();
      },
    );
  }

  /// 處理 WebSocket 推播。
  void _handleWsEvent(Map<String, dynamic> event) {
    wsEventCount++;

    switch (event['type']) {
      case 'device_state':
        _applyDeviceState(event);
        break;

      case 'scan_finished':
        // 掃描完成：直接更新結果，不用等下一次輪詢
        final results = event['results'];
        if (results is List) {
          scan = ScanStatus(
            running: false,
            progress: 100,
            total: results.length,
            message: '掃描完成',
            results: results
                .whereType<Map>()
                .map((e) => ScanResult.fromJson(Map<String, dynamic>.from(e)))
                .toList(),
          );
          scanState = LoadState.ready;
        }
        break;

      case 'welcome':
      default:
        break;
    }

    _safeNotify();
  }

  /// 把推播的狀態套用到對應的設備上。
  void _applyDeviceState(Map<String, dynamic> event) {
    final deviceInfo = event['device'];
    final state = event['state'];
    if (deviceInfo is! Map || state is! Map) return;

    final id = deviceInfo['id'];
    if (id is! int) return;

    final index = devices.indexWhere((d) => d.id == id);
    if (index < 0) return;

    final old = devices[index];
    devices[index] = Device(
      id: old.id,
      host: old.host,
      port: old.port,
      name: old.name,
      model: old.model,
      mac: old.mac,
      connected: old.connected,
      state: Map<String, dynamic>.from(state),
    );
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _scanTimer?.cancel();
    ws.dispose();
    api.dispose();
    _disposed = true;
    super.dispose();
  }

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  // -- 設備操作 ---------------------------------------------------------

  /// 載入設備清單。
  Future<void> refreshDevices() async {
    try {
      final list = await api.listDevices();
      devices = list;
      deviceError = '';
      deviceState = LoadState.ready;
    } on ApiException catch (e) {
      deviceError = e.message;
      deviceState = LoadState.error;
    } catch (e) {
      deviceError = e.toString();
      deviceState = LoadState.error;
    }
    _safeNotify();
  }

  /// 新增設備。
  Future<String?> addDevice({
    required String host,
    required int port,
    String? name,
  }) async {
    busyDeviceId = -1;
    _safeNotify();
    try {
      await api.addDevice(host: host, port: port, name: name);
      await refreshDevices();
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    } finally {
      busyDeviceId = null;
      _safeNotify();
    }
  }

  /// 移除設備。
  Future<String?> removeDevice(int id) async {
    try {
      await api.removeDevice(id);
      await refreshDevices();
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }

  /// 重新命名設備。
  Future<String?> renameDevice(int id, String name) async {
    try {
      await api.renameDevice(id, name);
      await refreshDevices();
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }

  /// 讀取單一設備狀態。
  Future<Map<String, dynamic>?> loadState(int id) async {
    busyDeviceId = id;
    _safeNotify();
    try {
      final state = await api.getState(id);
      _mergeState(id, state);
      return state;
    } on ApiException catch (e) {
      deviceError = e.message;
      return null;
    } finally {
      busyDeviceId = null;
      _safeNotify();
    }
  }

  /// 寫入 DO 通道。
  Future<String?> setDo(int id, {required int channel, required bool on}) async {
    busyDeviceId = id;
    _safeNotify();
    try {
      final state = await api.setDo(id, channel: channel, on: on);
      _mergeState(id, state);
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    } finally {
      busyDeviceId = null;
      _safeNotify();
    }
  }

  /// 全部通道開／關。
  Future<String?> setAllDo(int id, {required bool on}) async {
    busyDeviceId = id;
    _safeNotify();
    try {
      final state = await api.setAllDo(id, on: on);
      _mergeState(id, state);
      return null;
    } catch (e) {
      return e.toString();
    } finally {
      busyDeviceId = null;
      _safeNotify();
    }
  }

  /// 把後端回傳的狀態合併到本機的設備記錄。
  void _mergeState(int id, Map<String, dynamic> state) {
    final index = devices.indexWhere((Device d) => d.id == id);
    if (index < 0) return;
    final old = devices[index];
    devices[index] = Device(
      id: old.id,
      host: old.host,
      port: old.port,
      name: old.name,
      model: old.model,
      mac: old.mac,
      connected: state['connected'] == true,
      state: state,
    );
    _safeNotify();
  }

  // -- 掃描操作 ---------------------------------------------------------

  /// 開始掃描。
  Future<String?> startScan() async {
    scanError = '';
    scanState = LoadState.loading;
    _safeNotify();

    final targets = mockTargets
        .split(',')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();

    try {
      await api.startScan(
        subnet: subnetInput.trim(),
        port: int.tryParse(portInput.trim()),
        targets: targets.isEmpty ? null : targets,
      );
      scanState = LoadState.ready;
      // 開始輪詢掃描進度
      _pollScan();
      return null;
    } on ApiException catch (e) {
      scanError = e.message;
      scanState = LoadState.error;
      _safeNotify();
      return e.message;
    } catch (e) {
      scanError = e.toString();
      scanState = LoadState.error;
      _safeNotify();
      return e.toString();
    }
  }

  Timer? _scanTimer;

  /// 輪詢掃描進度，直到掃描結束。
  void _pollScan() {
    _scanTimer?.cancel();
    _scanTimer = Timer.periodic(const Duration(milliseconds: 500), (t) async {
      try {
        scan = await api.getScanStatus();
        scanState = LoadState.ready;
        if (!scan.running) {
          t.cancel();
          // 掃描完成後，把結果中的新設備自動加入清單
          await refreshDevices();
        }
      } catch (e) {
        t.cancel();
        scanError = e.toString();
        scanState = LoadState.error;
      }
      _safeNotify();
    });
  }

  /// 清除掃描結果。
  void clearScan() {
    scan = ScanStatus(
      running: false,
      progress: 0,
      total: 0,
      message: '尚未開始掃描',
      results: const [],
    );
    scanState = LoadState.idle;
    _safeNotify();
  }

  // -- AT 指令 ----------------------------------------------------------

  /// 送出任意 AT 指令。
  Future<String?> sendAt({
    int? deviceId,
    String? host,
    int? port,
    required String command,
  }) async {
    try {
      return await api.sendAt(
        deviceId: deviceId,
        host: host,
        port: port,
        command: command,
      );
    } on ApiException catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }
}
