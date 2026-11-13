/// NR-MC 控制台 —— 主程式入口。
///
/// 這是一個 Home Assistant Add-on 的前端，提供：
///   1. 裝置總覽 —— 管理已加入的設備
///   2. 網路掃描 —— 搜尋網段內的 NR-MC 設備
///   3. 設備測試 —— 查看連線狀態、控制風機檔位
///
/// ★ Ingress 路徑處理
/// ---------------------------------------------------------------------------
/// Home Assistant 的 Ingress 會在 URL 前加上
/// `/api/hassio_ingress/<token>`（token 已綁定本 Add-on），因此 API 呼叫不能寫死路徑。
/// 這裡在啟動時從瀏覽器網址推導出 base path，交給 ApiClient 使用。
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'pages/devices_page.dart';
import 'pages/scan_page.dart';
import 'pages/test_page.dart';
import 'state/app_state.dart';

void main() {
  runApp(const NrmcPanelApp());
}

class NrmcPanelApp extends StatelessWidget {
  const NrmcPanelApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => AppState()
        ..startPolling()
        ..startWebSocket(),
      child: MaterialApp(
        title: 'NR-MC 控制台',
        debugShowCheckedModeBanner: false,
        theme: _buildTheme(Brightness.light),
        darkTheme: _buildTheme(Brightness.dark),
        home: const HomeShell(),
      ),
    );
  }

  ThemeData _buildTheme(Brightness brightness) {
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF0288D1),
      brightness: brightness,
    );
    return ThemeData(
      colorScheme: scheme,
      useMaterial3: true,
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        surfaceTintColor: scheme.surfaceTint,
        elevation: 0,
        centerTitle: false,
      ),
      cardTheme: const CardThemeData(margin: EdgeInsets.zero),
      inputDecorationTheme: const InputDecorationTheme(
        border: OutlineInputBorder(),
        isDense: true,
      ),
    );
  }
}

/// 主框架：頂部 AppBar + 底部導覽列。
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const _pages = [
    DevicesPage(),
    ScanPage(),
    TestPage(),
  ];

  static const _titles = ['裝置總覽', '網路掃描', '設備測試'];
  static const _icons = [
    Icons.devices_other,
    Icons.radar,
    Icons.wind_power,
  ];

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Icon(Icons.air, color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 10),
            const Text('NR-MC 控制台'),
          ],
        ),
        actions: [
          // 顯示後端連線狀態
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(child: _BackendBadge(app: app)),
          ),
        ],
      ),
      body: SafeArea(
        child: IndexedStack(index: _index, children: _pages),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (var i = 0; i < _pages.length; i++)
            NavigationDestination(
              icon: Icon(_icons[i]),
              label: _titles[i],
            ),
        ],
      ),
    );
  }
}

/// 後端連線指示燈。
class _BackendBadge extends StatelessWidget {
  const _BackendBadge({required this.app});

  final AppState app;

  @override
  Widget build(BuildContext context) {
    // deviceError 為空代表上次輪詢成功
    final ok = app.deviceError.isEmpty && app.deviceState != LoadState.error;
    return Tooltip(
      message: ok ? '後端連線正常' : '後端連線異常：${app.deviceError}',
      child: Icon(
        ok ? Icons.cloud_done : Icons.cloud_off,
        size: 20,
        color: ok
            ? Colors.green.shade600
            : Theme.of(context).colorScheme.error,
      ),
    );
  }
}
