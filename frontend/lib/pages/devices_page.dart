/// 設備總覽頁。
///
/// 顯示所有已加入的設備，可新增、移除、重新命名、查看連線狀態。
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

class DevicesPage extends StatelessWidget {
  const DevicesPage({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();

    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: '裝置總覽',
            subtitle: '共 ${app.devices.length} 台設備'
                '${app.deviceError.isNotEmpty ? '  ·  ${app.deviceError}' : ''}',
            trailing: Wrap(
              spacing: 8,
              children: [
                LiveBadge(status: app.wsStatus, eventCount: app.wsEventCount),
                FilledButton.tonalIcon(
                  onPressed: () => _showAddDialog(context),
                  icon: const Icon(Icons.add),
                  label: const Text('新增設備'),
                ),
                IconButton.filledTonal(
                  onPressed: () => app.refreshDevices(),
                  icon: const Icon(Icons.refresh),
                  tooltip: '重新整理',
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          Expanded(
            child: app.devices.isEmpty
                ? const EmptyHint(
                    icon: Icons.devices_other,
                    title: '尚未加入任何設備',
                    message: '使用右上角的「新增設備」手動輸入 IP，'
                        '或切換到「網路掃描」頁自動搜尋。',
                  )
                : ListView.separated(
                    itemCount: app.devices.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (context, i) =>
                        _DeviceTile(device: app.devices[i]),
                  ),
          ),
        ],
      ),
    );
  }
}

class _DeviceTile extends StatelessWidget {
  const _DeviceTile({required this.device});

  final Device device;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final busy = app.busyDeviceId == device.id;
    final doStates = device.doStates;

    return InfoCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.air,
                size: 22,
                color: Theme.of(context).colorScheme.primary,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      device.displayName,
                      style: Theme.of(context).textTheme.titleSmall?.copyWith(
                            fontWeight: FontWeight.w700,
                          ),
                    ),
                    Text(
                      '${device.host}:${device.port}'
                      '${device.model.isNotEmpty ? '  ·  ${device.model}' : ''}',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color:
                                Theme.of(context).colorScheme.onSurfaceVariant,
                          ),
                    ),
                  ],
                ),
              ),
              if (busy)
                const Padding(
                  padding: EdgeInsets.only(right: 10),
                  child: SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
              ConnectionBadge(connected: device.connected),
              PopupMenuButton<String>(
                onSelected: (value) => _handleMenu(context, value),
                itemBuilder: (_) => const [
                  PopupMenuItem(value: 'state', child: Text('刷新狀態')),
                  PopupMenuItem(value: 'rename', child: Text('重新命名')),
                  PopupMenuItem(value: 'remove', child: Text('移除設備')),
                ],
              ),
            ],
          ),
          if (device.error.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              device.error,
              style: TextStyle(
                color: Theme.of(context).colorScheme.error,
                fontSize: 12,
              ),
            ),
          ],
          if (doStates.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              children: doStates.entries
                  .map((e) => _DoChip(channel: e.key, on: e.value))
                  .toList(),
            ),
          ],
        ],
      ),
    );
  }

  void _handleMenu(BuildContext context, String action) {
    final app = context.read<AppState>();
    switch (action) {
      case 'state':
        app.loadState(device.id);
      case 'rename':
        _showRenameDialog(context, device);
      case 'remove':
        _confirmRemove(context, device);
    }
  }

  Future<void> _confirmRemove(BuildContext context, Device device) async {
    final app = context.read<AppState>();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('移除設備'),
        content: Text('確定要移除「${device.displayName}」嗎？\n'
            '這只會從清單移除，不會改變設備本身的設定。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('移除'),
          ),
        ],
      ),
    );
    if (ok == true) {
      final err = await app.removeDevice(device.id);
      if (err != null && context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('移除失敗：$err')));
      }
    }
  }

  Future<void> _showRenameDialog(BuildContext context, Device device) async {
    final controller = TextEditingController(text: device.name);
    final app = context.read<AppState>();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('重新命名'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: '顯示名稱',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('儲存'),
          ),
        ],
      ),
    );
    if (name != null && name.isNotEmpty) {
      final err = await app.renameDevice(device.id, name);
      if (err != null && context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('更新失敗：$err')));
      }
    }
  }
}

class _DoChip extends StatelessWidget {
  const _DoChip({required this.channel, required this.on});

  final int channel;
  final bool on;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
        color: on ? scheme.primaryContainer : scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        'DO$channel ${on ? "ON" : "OFF"}',
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: on ? scheme.onPrimaryContainer : scheme.onSurfaceVariant,
        ),
      ),
    );
  }
}

/// 新增設備對話框。
Future<void> _showAddDialog(BuildContext context) async {
  final hostCtrl = TextEditingController();
  final portCtrl = TextEditingController(text: '12345');
  final nameCtrl = TextEditingController();
  final formKey = GlobalKey<FormState>();

  final result = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('新增設備'),
      content: Form(
        key: formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextFormField(
              controller: hostCtrl,
              autofocus: true,
              decoration: const InputDecoration(
                labelText: 'IP 位址',
                hintText: '192.168.30.250',
                border: OutlineInputBorder(),
              ),
              validator: (v) {
                if (v == null || v.trim().isEmpty) return '請輸入 IP 位址';
                return null;
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: portCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'AT over TCP port',
                helperText: '預設 12345',
                border: OutlineInputBorder(),
              ),
              validator: (v) {
                final n = int.tryParse(v?.trim() ?? '');
                if (n == null || n < 1 || n > 65535) return 'port 不合法';
                return null;
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: nameCtrl,
              decoration: const InputDecoration(
                labelText: '名稱（留空則自動讀取）',
                border: OutlineInputBorder(),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx, false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: () {
            if (formKey.currentState?.validate() ?? false) {
              Navigator.pop(ctx, true);
            }
          },
          child: const Text('新增'),
        ),
      ],
    ),
  );

  if (result == true && context.mounted) {
    final app = context.read<AppState>();
    final messenger = ScaffoldMessenger.of(context);
    final err = await app.addDevice(
      host: hostCtrl.text.trim(),
      port: int.parse(portCtrl.text.trim()),
      name: nameCtrl.text.trim(),
    );
    if (err != null) {
      messenger.showSnackBar(SnackBar(content: Text('新增失敗：$err')));
    }
  }
}
