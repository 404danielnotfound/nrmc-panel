/// 網路掃描頁。
///
/// 可指定網段搜尋 NR-MC 設備，也可輸入明確的主機清單（Mock 模式）。
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

class ScanPage extends StatelessWidget {
  const ScanPage({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: '網路掃描',
            subtitle: '搜尋指定網段內的 NR-MC 新風控制器',
            trailing: app.scan.results.isEmpty
                ? null
                : TextButton.icon(
                    onPressed: app.clearScan,
                    icon: const Icon(Icons.clear_all, size: 18),
                    label: const Text('清除結果'),
                  ),
          ),
          const SizedBox(height: 16),
          _ScanForm(),
          const SizedBox(height: 16),
          if (app.scanState == LoadState.error && app.scanError.isNotEmpty)
            ErrorBar(message: app.scanError),
          const SizedBox(height: 16),
          _ScanProgress(),
          const SizedBox(height: 16),
          _ScanResults(),
        ],
      ),
    );
  }
}

/// 掃描表單。
///
/// ★ 必須是 StatefulWidget 且 TextEditingController 要持久保存。
///   若在 build() 內直接 new TextEditingController，每次 rebuild 都會
///   建立新的 controller，導致輸入的文字被清掉（而且游標位置也會跑掉）。
class _ScanForm extends StatefulWidget {
  const _ScanForm();

  @override
  State<_ScanForm> createState() => _ScanFormState();
}

class _ScanFormState extends State<_ScanForm> {
  late final TextEditingController _subnetCtrl;
  late final TextEditingController _portCtrl;
  late final TextEditingController _targetsCtrl;
  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    // 先建立空的 controller，實際初始值在 didChangeDependencies 設定。
    // ★ 為什麼不放在 initState？
    //   initState 時 Provider 的祖先可能還不可用（Widget 是在
    //   IndexedStack 內被建立），這時 context.read 會拿到錯誤的值。
    //   didChangeDependencies 保證 Provider 已經就緒。
    _subnetCtrl = TextEditingController();
    _portCtrl = TextEditingController();
    _targetsCtrl = TextEditingController();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_initialized) return;
    _initialized = true;
    final app = context.read<AppState>();
    _subnetCtrl.text = app.subnetInput;
    _portCtrl.text = app.portInput;
    _targetsCtrl.text = app.mockTargets;
  }

  @override
  void dispose() {
    _subnetCtrl.dispose();
    _portCtrl.dispose();
    _targetsCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final running = app.scan.running;

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                flex: 2,
                child: TextField(
                  controller: _subnetCtrl,
                  enabled: !running,
                  decoration: const InputDecoration(
                    labelText: '網段',
                    hintText: '192.168.30（留空則自動偵測本機網段）',
                    border: OutlineInputBorder(),
                  ),
                  onChanged: (v) => app.subnetInput = v,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _portCtrl,
                  enabled: !running,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'AT port',
                    border: OutlineInputBorder(),
                  ),
                  onChanged: (v) => app.portInput = v,
                ),
              ),
              const SizedBox(width: 12),
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: FilledButton.icon(
                  // ★ 直接從 controller 讀取目前輸入值，而不是只依賴
                  //   onChanged 同步到 app state。
                  //   兩者並用可以確保「按下掃描」時用的一定是畫面上
                  //   看到的內容，不會因為任何一次狀態同步延遲而掃錯目標。
                  onPressed: running
                      ? null
                      : () {
                          app.subnetInput = _subnetCtrl.text;
                          app.portInput = _portCtrl.text;
                          app.mockTargets = _targetsCtrl.text;
                          app.startScan();
                        },
                  icon: running
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.radar),
                  label: Text(running ? '掃描中...' : '開始掃描'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            childrenPadding: EdgeInsets.zero,
            title: Text(
              'Mock 模式（直接指定主機）',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
            ),
            subtitle: const Text(
              '輸入逗號分隔的主機清單，會跳過 ping 探測直接查詢 AT。'
              '開發測試時使用。',
            ),
            children: [
              const SizedBox(height: 8),
              TextField(
                controller: _targetsCtrl,
                enabled: !running,
                decoration: const InputDecoration(
                  labelText: '主機清單',
                  hintText: '127.0.0.1, 192.168.30.250',
                  border: OutlineInputBorder(),
                ),
                onChanged: (v) => app.mockTargets = v,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ScanProgress extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    if (!app.scan.running &&
        app.scan.message == '尚未開始掃描') {
      return const SizedBox.shrink();
    }

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  app.scan.message,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
              if (app.scan.total > 0)
                Text(
                  '${app.scan.progress} / ${app.scan.total}',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          LinearProgressIndicator(
            value: app.scan.running ? app.scan.fraction : 1.0,
            minHeight: 6,
            borderRadius: BorderRadius.circular(3),
          ),
        ],
      ),
    );
  }
}

class _ScanResults extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final results = app.scan.results;
    if (results.isEmpty) return const SizedBox.shrink();

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: '掃描結果',
            subtitle: '找到 ${results.where((r) => r.isDevice).length} 台設備'
                '（共 ${results.length} 個有回應的主機）',
          ),
          const SizedBox(height: 12),
          ...results.map((r) => _ResultRow(result: r)),
        ],
      ),
    );
  }
}

class _ResultRow extends StatelessWidget {
  const _ResultRow({required this.result});

  final ScanResult result;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final alreadyAdded = app.devices.any((Device d) =>
        d.host == result.ip && d.port == result.atPort);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(
            result.isDevice ? Icons.check_circle : Icons.circle_outlined,
            size: 18,
            color: result.isDevice
                ? Colors.green.shade700
                : Theme.of(context).colorScheme.outline,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  result.displayName,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                Text(
                  '${result.ip}:${result.atPort}'
                  '${result.model.isNotEmpty ? '  ·  ${result.model}' : ''}',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                ),
              ],
            ),
          ),
          if (alreadyAdded)
            const Chip(
              label: Text('已在清單'),
              visualDensity: VisualDensity.compact,
            )
          else
            FilledButton.tonal(
              onPressed: () async {
                final messenger = ScaffoldMessenger.of(context);
                final err = await app.addDevice(
                  host: result.ip,
                  port: result.atPort,
                );
                messenger.showSnackBar(
                  SnackBar(
                    content: Text(err == null
                        ? '已加入 ${result.displayName}'
                        : '加入失敗：$err'),
                  ),
                );
              },
              child: const Text('加入清單'),
            ),
        ],
      ),
    );
  }
}
