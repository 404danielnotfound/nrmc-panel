/// 後端 API 客戶端。
///
/// 負責與 Flask 後端通訊，並處理 Home Assistant Ingress 特有的路徑問題。
///
/// ★ Ingress 路徑的重要觀念
/// ---------------------------------------------------------------------------
/// 在 Home Assistant 裡，Add-on 不是掛在網域根目錄，而是：
///
///     https://<ha>:8123/api/hassio_ingress/<token>/nrmc_panel/
/// 因此 API 的完整路徑是：
///
///     https://<ha>:8123/api/hassio_ingress/<token>/nrmc_panel/api/devices
///
/// 如果前端寫死 `/api/devices`，在 Ingress 底下會 404。
///
/// 解決方式有兩種：
///   1. 讀取 `X-Ingress-Path` 標頭（Supervisor 會注入）
///   2. 從瀏覽器目前網址推導 base path
///
/// 兩種都做，確保在開發模式（localhost:8080，沒有 Ingress）也能運作。
library;

import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

/// API 呼叫失敗時丟出的例外。
class ApiException implements Exception {
  ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}

/// 一台 NR-MC 設備。
class Device {
  Device({
    required this.id,
    required this.host,
    required this.port,
    required this.name,
    required this.model,
    this.mac = '',
    this.connected,
    this.state = const {},
  });

  final int id;
  final String host;
  final int port;
  final String name;
  final String model;
  final String mac;
  final bool? connected;
  final Map<String, dynamic> state;

  /// 顯示名稱，沒有設定時退回 IP。
  String get displayName => name.isNotEmpty ? name : host;

  /// DO 通道狀態，例如 {1: true, 2: false}。
  Map<int, bool> get doStates {
    final raw = state['do'];
    if (raw is! Map) return const {};
    return raw.map((k, v) => MapEntry(int.parse('$k'), v == true));
  }

  /// DI 通道狀態。
  Map<int, bool> get diStates {
    final raw = state['di'];
    if (raw is! Map) return const {};
    return raw.map((k, v) => MapEntry(int.parse('$k'), v == true));
  }

  /// 錯誤訊息（若有的話）。
  String get error => state['error']?.toString() ?? '';

  factory Device.fromJson(Map<String, dynamic> json) {
    return Device(
      id: json['id'] as int,
      host: json['host']?.toString() ?? '',
      port: json['port'] as int? ?? 12345,
      name: json['name']?.toString() ?? '',
      model: json['model']?.toString() ?? '',
      mac: json['mac']?.toString() ?? '',
      connected: json['connected'] as bool?,
      state: json['state'] is Map
          ? Map<String, dynamic>.from(json['state'] as Map)
          : const {},
    );
  }
}

/// 掃描結果中的一台設備。
class ScanResult {
  ScanResult({
    required this.ip,
    required this.name,
    required this.model,
    required this.isDevice,
    required this.atPort,
    this.mac = '',
  });

  final String ip;
  final String name;
  final String model;
  final bool isDevice;
  final int atPort;
  final String mac;

  String get displayName => name.isNotEmpty ? name : ip;

  factory ScanResult.fromJson(Map<String, dynamic> json) {
    return ScanResult(
      ip: json['ip']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      model: json['model']?.toString() ?? '',
      isDevice: json['is_device'] == true,
      atPort: json['at_port'] as int? ?? 12345,
      mac: json['mac']?.toString() ?? '',
    );
  }
}

/// 掃描進度狀態。
class ScanStatus {
  ScanStatus({
    required this.running,
    required this.progress,
    required this.total,
    required this.message,
    required this.results,
  });

  final bool running;
  final int progress;
  final int total;
  final String message;
  final List<ScanResult> results;

  double get fraction => total > 0 ? progress / total : 0;

  factory ScanStatus.fromJson(Map<String, dynamic> json) {
    final rawResults = json['results'];
    return ScanStatus(
      running: json['running'] == true,
      progress: json['progress'] as int? ?? 0,
      total: json['total'] as int? ?? 0,
      message: json['message']?.toString() ?? '',
      results: rawResults is List
          ? rawResults
              .map((e) => ScanResult.fromJson(Map<String, dynamic>.from(e)))
              .toList()
          : const [],
    );
  }
}

/// 後端 API 客戶端。
class ApiClient {
  ApiClient({http.Client? client, this.baseUrlOverride})
      : _client = client ?? http.Client();

  final http.Client _client;

  /// 開發模式下指定後端位置；正式環境留 null 走 Ingress 路徑推導。
  final String? baseUrlOverride;

  /// 快取的 base path，例如 "/api/hassio_ingress/TOKEN/nrmc_panel"。
  String? _ingressPath;

  /// 設定 Ingress base path（由瀏覽器端注入）。
  set ingressPath(String? value) {
    _ingressPath = (value == null || value.isEmpty) ? null : value;
  }

  /// 取得 API 的 base URL。
  ///
  /// 優先順序：
  ///   1. 明確指定的 override（開發時用）
  ///   2. Ingress 標頭推導出來的路徑 + 目前 origin
  ///   3. 目前頁面的 origin（開發模式：直接連 127.0.0.1:8080）
  String get baseUrl {
    if (baseUrlOverride != null) return baseUrlOverride!;

    final origin = Uri.base.origin;
    final path = _ingressPath ?? _derivePathFromLocation();
    if (path == null || path.isEmpty) return origin;
    return '$origin$path';
  }

  /// 從瀏覽器目前網址推導 Ingress base path。
  ///
  /// 範例：
  ///   http://host:8123/api/hassio_ingress/abc123/nrmc_panel/
  ///   → "/api/hassio_ingress/abc123/nrmc_panel"
  String? _derivePathFromLocation() {
    if (kIsWeb) {
      final segments = Uri.base.pathSegments;
      final index = segments.indexOf('hassio_ingress');
      if (index >= 0 && index + 2 < segments.length) {
        // hassio_ingress/<token>/<slug>
        return '/api/hassio_ingress/${segments[index + 1]}/'
            '${segments[index + 2]}';
      }
    }
    return null;
  }

  Uri _uri(String path, [Map<String, dynamic>? query]) {
    final base = Uri.parse(baseUrl);
    final combined = <String, dynamic>{...?query};
    return base.replace(
      path: '${base.path}$path',
      queryParameters:
          combined.isEmpty ? null : combined.map((k, v) => MapEntry(k, '$v')),
    );
  }

  /// 取得 WebSocket 的完整網址。
  ///
  /// ★ 為什麼要特別換算？
  ///   Ingress 底下瀏覽器網址是 https:// 開頭，但 WebSocket 需要 ws:// 或 wss://。
  ///   同時 Ingress 的路徑前綴（`/api/hassio_ingress/<token>/<slug>`）
  ///   必須保留，不能跟 REST 一樣只取 /api。
  ///
  /// 範例：
  ///   baseUrl = https://ha:8123/api/hassio_ingress/abc/nrmc_panel
  ///   → wsUrl = wss://ha:8123/api/hassio_ingress/abc/nrmc_panel/ws
  String? get wsUrl {
    final base = Uri.parse(baseUrl);

    final scheme = base.scheme == 'https' || base.scheme == 'wss' ? 'wss' : 'ws';

    // ★ base.path 可能是空字串（開發模式：http://127.0.0.1:8080），
    //   那時 WebSocket 路徑就是 /ws，不能因為 path 空就回傳 null。
    final basePath = base.path.endsWith('/')
        ? base.path.substring(0, base.path.length - 1)
        : base.path;
    final path = '$basePath/ws';

    return base.replace(scheme: scheme, path: path, query: null).toString();
  }

  Map<String, String> get _headers => const {
        'Content-Type': 'application/json; charset=utf-8',
      };

  /// 檢查後端是否存活。
  Future<bool> health() async {
    try {
      final res = await _client.get(_uri('/api/health'));
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// 取得設備清單。
  Future<List<Device>> listDevices() async {
    final res = await _client.get(_uri('/api/devices'), headers: _headers);
    _check(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final list = data['devices'] as List? ?? const [];
    return list
        .map((e) => Device.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }

  /// 新增設備（後端會先驗證連線）。
  Future<Device> addDevice({
    required String host,
    required int port,
    String? name,
  }) async {
    final res = await _client.post(
      _uri('/api/devices'),
      headers: _headers,
      body: jsonEncode({
        'host': host,
        'port': port,
        if (name != null && name.isNotEmpty) 'name': name,
      }),
    );
    _check(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    return Device.fromJson(Map<String, dynamic>.from(data['device'] as Map));
  }

  /// 移除設備。
  Future<void> removeDevice(int id) async {
    final res = await _client.delete(_uri('/api/devices/$id'));
    _check(res);
  }

  /// 更新設備名稱。
  Future<void> renameDevice(int id, String name) async {
    final res = await _client.patch(
      _uri('/api/devices/$id'),
      headers: _headers,
      body: jsonEncode({'name': name}),
    );
    _check(res);
  }

  /// 讀取設備狀態。
  Future<Map<String, dynamic>> getState(int id) async {
    final res = await _client.get(_uri('/api/devices/$id/state'));
    _check(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final device = Map<String, dynamic>.from(data['device'] as Map);
    final state = device['state'];
    return state is Map ? Map<String, dynamic>.from(state) : <String, dynamic>{};
  }

  /// 寫入單一 DO 通道。
  Future<Map<String, dynamic>> setDo(
    int id, {
    required int channel,
    required bool on,
  }) async {
    final res = await _client.post(
      _uri('/api/devices/$id/do'),
      headers: _headers,
      body: jsonEncode({'channel': channel, 'on': on}),
    );
    _check(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final state = data['state'];
    return state is Map ? Map<String, dynamic>.from(state) : <String, dynamic>{};
  }

  /// 全部通道開／關。
  Future<Map<String, dynamic>> setAllDo(int id, {required bool on}) async {
    final res = await _client.post(
      _uri('/api/devices/$id/all'),
      headers: _headers,
      body: jsonEncode({'on': on}),
    );
    _check(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final state = data['state'];
    return state is Map ? Map<String, dynamic>.from(state) : <String, dynamic>{};
  }

  /// 開始掃描。
  Future<void> startScan({
    String? subnet,
    int? port,
    List<String>? targets,
  }) async {
    final res = await _client.post(
      _uri('/api/scan/start'),
      headers: _headers,
      body: jsonEncode({
        if (subnet != null && subnet.isNotEmpty) 'subnet': subnet,
        'port': ?port,
        'targets': ?targets,
      }),
    );
    _check(res);
  }

  /// 查詢掃描狀態。
  Future<ScanStatus> getScanStatus() async {
    final res = await _client.get(_uri('/api/scan/status'));
    _check(res);
    return ScanStatus.fromJson(
      jsonDecode(res.body) as Map<String, dynamic>,
    );
  }

  /// 送出任意 AT 指令。
  Future<String> sendAt({
    int? deviceId,
    String? host,
    int? port,
    required String command,
  }) async {
    final res = await _client.post(
      _uri('/api/at'),
      headers: _headers,
      body: jsonEncode({
        'command': command,
        'id': ?deviceId,
        'host': ?host,
        'port': ?port,
      }),
    );
    _check(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    return data['response']?.toString() ?? '';
  }

  void _check(http.Response res) {
    if (res.statusCode >= 200 && res.statusCode < 300) return;

    String message = 'HTTP ${res.statusCode}';
    try {
      final data = jsonDecode(res.body);
      if (data is Map && data['error'] != null) {
        message = data['error'].toString();
      }
    } catch (_) {
      // 回應不是 JSON，就用原始 body
      if (res.body.isNotEmpty && res.body.length < 300) {
        message = res.body;
      }
    }
    throw ApiException(message, statusCode: res.statusCode);
  }

  void dispose() => _client.close();
}
