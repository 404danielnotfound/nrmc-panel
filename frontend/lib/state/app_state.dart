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

/// 一個操作（送出指令）的狀態機。
///
/// ★ 為什麼需要狀態機？
///   使用者反映「按下後 5~10 秒才動作、有時候按了好像沒有用」。
///   根本原因是：按下到結果回來之間沒有任何視覺回饋，
///   使用者不知道指令是否已送出，因此會再按一次。
///   有了明確的階段狀態，按鈕就能顯示
///   「送出中 → 等待回讀 → 已確認」，並在進行中擋下重複點擊。
enum OpPhase {
  /// 閒置。
  idle,

  /// 指令已送出，等待回覆。
  sending,

  /// 已收到回覆，正在回讀確認。
  verifying,

  /// 已確認成功。
  confirmed,

  /// 失敗。
  failed,
}

/// 一次操作的完整紀錄（含逐段耗時）。
class OperationRecord {
  OperationRecord({
    required this.label,
    required this.startedAt,
    this.phase = OpPhase.sending,
    this.timings = const [],
    this.message = '',
    this.error = '',
  });

  /// 操作描述，例如「一檔」。
  final String label;

  /// 開始時間。
  final DateTime startedAt;

  /// 目前階段。
  OpPhase phase;

  /// 逐段耗時。
  List<OperationTiming> timings;

  /// 結果訊息。
  String message;

  /// 錯誤訊息。
  String error;

  /// 總耗時（毫秒）。
  double get totalMs => timings.fold(0.0, (sum, t) => sum + t.ms);

  /// 從開始到現在的經過時間（毫秒）。
  double get elapsedMs =>
      DateTime.now().difference(startedAt).inMicroseconds / 1000.0;

  /// 最慢的一段（瓶頸）。
  OperationTiming? get slowest {
    if (timings.isEmpty) return null;
    final sorted = [...timings]..sort((a, b) => b.ms.compareTo(a.ms));
    return sorted.first;
  }
}

class AppState extends ChangeNotifier {
  AppState({ApiClient? api}) : api = api ?? ApiClient();

  final ApiClient api;

  // -- 設備 -------------------------------------------------------------
  List<Device> devices = [];
  LoadState deviceState = LoadState.idle;
  String deviceError = '';

  /// 正在操作中的設備 id（用來停用按鈕、顯示 loading）。
  int? busyDeviceId;

  // -- 操作狀態機與耗時紀錄 ---------------------------------------------

  /// 每台設備目前的操作階段（null = 閒置）。
  final Map<int, OpPhase> _opPhases = {};

  /// 每台設備最近一次操作的紀錄。
  final Map<int, OperationRecord> _opRecords = {};

  /// 全域操作紀錄（最新在前），最多保留 20 筆。
  final List<OperationRecord> operationLog = [];

  /// 取得某台設備目前的操作階段。
  OpPhase opPhaseOf(int deviceId) => _opPhases[deviceId] ?? OpPhase.idle;

  /// 取得某台設備最近一次操作紀錄。
  OperationRecord? opRecordOf(int deviceId) => _opRecords[deviceId];

  /// 某台設備是否正在進行操作（用來擋重複點擊）。
  bool isOpBusy(int deviceId) =>
      opPhaseOf(deviceId) == OpPhase.sending ||
      opPhaseOf(deviceId) == OpPhase.verifying;

  void _beginOp(int deviceId, String label) {
    final rec = OperationRecord(
      label: label,
      startedAt: DateTime.now(),
      phase: OpPhase.sending,
    );
    _opRecords[deviceId] = rec;
    _opPhases[deviceId] = OpPhase.sending;
    operationLog.insert(0, rec);
    if (operationLog.length > 20) {
      operationLog.removeRange(20, operationLog.length);
    }
    busyDeviceId = deviceId;
    _safeNotify();
  }

  void _endOp(int deviceId, OpPhase phase,
      {List<OperationTiming> timings = const [],
      String message = '',
      String error = ''}) {
    _opPhases[deviceId] = phase;
    final rec = _opRecords[deviceId];
    if (rec != null) {
      rec.phase = phase;
      rec.timings = timings;
      rec.message = message;
      rec.error = error;
    }
    busyDeviceId = null;
    _safeNotify();
  }

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
  ///
  /// ★ P1-4：進行中的重複點擊直接忽略。
  ///   使用者看到風機慢會再按一次，後端也會回 409，
  ///   這裡在發出前就先擋掉，避免送出無意義的請求。
  Future<String?> setDo(int id, {required int channel, required bool on}) async {
    if (isOpBusy(id)) {
      return '上一個指令還在執行中，請稍候';
    }
    _beginOp(id, channel == 0 ? '全部通道' : 'DO$channel');
    try {
      final result = await api.setDo(id, channel: channel, on: on);
      _mergeState(id, result.state);
      _endOp(
        id,
        OpPhase.confirmed,
        timings: result.timings,
        message: on ? '已開啟' : '已關閉',
      );
      return null;
    } on ApiException catch (e) {
      _endOp(id, OpPhase.failed, error: e.message);
      return e.message;
    } catch (e) {
      _endOp(id, OpPhase.failed, error: e.toString());
      return e.toString();
    }
  }

  /// 全部通道開／關。
  Future<String?> setAllDo(int id, {required bool on}) async {
    if (isOpBusy(id)) {
      return '上一個指令還在執行中，請稍候';
    }
    _beginOp(id, on ? '全部開啟' : '全部關閉');
    try {
      final result = await api.setAllDo(id, on: on);
      _mergeState(id, result.state);
      _endOp(
        id,
        OpPhase.confirmed,
        timings: result.timings,
        message: on ? '已開啟' : '已關閉',
      );
      return null;
    } on ApiException catch (e) {
      _endOp(id, OpPhase.failed, error: e.message);
      return e.message;
    } catch (e) {
      _endOp(id, OpPhase.failed, error: e.toString());
      return e.toString();
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
  ///
  /// 回傳 (回覆內容, 逐段耗時)。失敗時回傳 (錯誤訊息, 空耗時)。
  Future<(String, List<OperationTiming>)> sendAt({
    int? deviceId,
    String? host,
    int? port,
    required String command,
  }) async {
    try {
      final result = await api.sendAt(
        deviceId: deviceId,
        host: host,
        port: port,
        command: command,
      );
      final response = result.state['response']?.toString() ?? '';
      return (response, result.timings);
    } on ApiException catch (e) {
      return (e.message, const <OperationTiming>[]);
    } catch (e) {
      return (e.toString(), const <OperationTiming>[]);
    }
  }
}
