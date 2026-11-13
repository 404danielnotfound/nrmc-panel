/// 設備測試頁。
///
/// 顯示設備連線狀態，並提供風機檔位控制（一檔／二檔／關閉）。
///
/// ★ 互鎖的處理
/// ---------------------------------------------------------------------------
/// NR-MC2201-WIFI 的 DO1 與 DO2 構成硬體互鎖組：
///   開 DO1 → 裝置自動關閉 DO2
///   開 DO2 → 裝置自動關閉 DO1
///
/// 這個互鎖是在設備端硬體完成的，前端「不需要」自己先關另一個通道。
/// 但前端仍要做兩件事：
///   1. 送出開啟指令後，以回讀的狀態為準更新畫面
///      （因為硬體互鎖會改變另一個通道的狀態）
///   2. 顯示互鎖提示，讓使用者理解為什麼另一檔會自動關閉
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

class TestPage extends StatefulWidget {
  const TestPage({super.key});

  @override
  State<TestPage> createState() => _TestPageState();
}

class _TestPageState extends State<TestPage> {
  int? _selectedId;
  final TextEditingController _atCtrl = TextEditingController(text: 'AT');
  String? _atResponse;
  bool _atBusy = false;

  @override
  void dispose() {
    _atCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();

    if (app.devices.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(20),
        child: EmptyHint(
          icon: Icons.wind_power,
          title: '尚無可測試的設備',
          message: '請先到「裝置總覽」新增設備，或到「網路掃描」搜尋並加入。',
        ),
      );
    }

    // 預設選第一台
    final firstDevice = app.devices.first;
    final selectedId = _selectedId ?? firstDevice.id;
    final device = app.devices.firstWhere(
      (Device d) => d.id == selectedId,
      orElse: () => firstDevice,
    );

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: '設備測試',
            subtitle: '檢查連線狀態並控制風機檔位',
          ),
          const SizedBox(height: 16),
          _DeviceSelector(
            devices: app.devices,
            selectedId: device.id,
            onChanged: (id) => setState(() => _selectedId = id),
          ),
          const SizedBox(height: 16),
          _StatusPanel(device: device),
          const SizedBox(height: 16),
          _FanControl(device: device),
          const SizedBox(height: 16),
          _AtCommandPanel(
            controller: _atCtrl,
            busy: _atBusy,
            response: _atResponse,
            device: device,
            onSend: _sendAt,
          ),
        ],
      ),
    );
  }

  Future<void> _sendAt(Device device, String command) async {
    setState(() {
      _atBusy = true;
      _atResponse = null;
    });
    final app = context.read<AppState>();
    final res = await app.sendAt(
      deviceId: device.id,
      command: command,
    );
    if (!mounted) return;
    setState(() {
      _atBusy = false;
      _atResponse = res ?? '';
    });
  }
}

// ----------------------------------------------------------------------
// 設備選擇
// ----------------------------------------------------------------------
class _DeviceSelector extends StatelessWidget {
  const _DeviceSelector({
    required this.devices,
    required this.selectedId,
    required this.onChanged,
  });

  final List<Device> devices;
  final int selectedId;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    return InfoCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int>(
          value: selectedId,
          isExpanded: true,
          onChanged: (v) {
            if (v != null) onChanged(v);
          },
          items: devices
              .map((d) => DropdownMenuItem<int>(
                    value: d.id,
                    child: Row(
                      children: [
                        Text(d.displayName),
                        const SizedBox(width: 8),
                        Text(
                          '(${d.host}:${d.port})',
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                color: Theme.of(context)
                                    .colorScheme
                                    .onSurfaceVariant,
                              ),
                        ),
                      ],
                    ),
                  ))
              .toList(),
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------
// 狀態面板
// ----------------------------------------------------------------------
class _StatusPanel extends StatelessWidget {
  const _StatusPanel({required this.device});

  final Device device;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final busy = app.busyDeviceId == device.id;

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: '連線狀態',
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                ConnectionBadge(connected: device.connected),
                const SizedBox(width: 8),
                IconButton.filledTonal(
                  onPressed: busy ? null : () => app.loadState(device.id),
                  icon: busy
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.refresh),
                  tooltip: '重新查詢',
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          _InfoRow(label: '名稱', value: device.displayName),
          _InfoRow(label: '位址', value: '${device.host}:${device.port}'),
          _InfoRow(
            label: '型號',
            value: device.model.isEmpty ? '（未知）' : device.model,
          ),
          _InfoRow(
            label: '韌體',
            value: device.state['firmware']?.toString() ?? '（未知）',
          ),
          if (device.error.isNotEmpty) ...[
            const SizedBox(height: 10),
            ErrorBar(message: device.error),
          ],
          const SizedBox(height: 14),
          _DiRow(states: device.diStates),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 64,
            child: Text(
              label,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
            ),
          ),
        ],
      ),
    );
  }
}

class _DiRow extends StatelessWidget {
  const _DiRow({required this.states});

  final Map<int, bool> states;

  @override
  Widget build(BuildContext context) {
    if (states.isEmpty) return const SizedBox.shrink();
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'DI 輸入',
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: scheme.onSurfaceVariant,
              ),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 8,
          children: states.entries
              .map((e) => Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 9, vertical: 3),
                    decoration: BoxDecoration(
                      color: e.value
                          ? const Color(0xFFE3F9E5)
                          : scheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(
                      'DI${e.key} ${e.value ? "ON" : "OFF"}',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: e.value
                            ? const Color(0xFF1B5E20)
                            : scheme.onSurfaceVariant,
                      ),
                    ),
                  ))
              .toList(),
        ),
      ],
    );
  }
}

// ----------------------------------------------------------------------
// 風機控制
// ----------------------------------------------------------------------
class _FanControl extends StatelessWidget {
  const _FanControl({required this.device});

  final Device device;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final busy = app.busyDeviceId == device.id;
    final doStates = device.doStates;
    final do1 = doStates[1] ?? false;
    final do2 = doStates[2] ?? false;

    // 目前檔位
    final String current;
    if (do1 && !do2) {
      current = '一檔';
    } else if (do2 && !do1) {
      current = '二檔';
    } else if (do1 && do2) {
      current = '異常（兩檔同時開啟）';
    } else {
      current = '關閉';
    }

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: '風機檔位',
            subtitle: '目前：$current',
          ),
          const SizedBox(height: 6),
          Text(
            'DO1 與 DO2 為硬體互鎖，同時只能開啟其中一檔。'
            '切換檔位時裝置會自動關閉另一檔。',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: _SpeedButton(
                  label: '一檔',
                  sublabel: 'DO1',
                  icon: Icons.air,
                  active: do1 && !do2,
                  busy: busy,
                  onPressed: () => _set(context, 1, !do1),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _SpeedButton(
                  label: '二檔',
                  sublabel: 'DO2',
                  icon: Icons.air,
                  active: do2 && !do1,
                  busy: busy,
                  onPressed: () => _set(context, 2, !do2),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _SpeedButton(
                  label: '關閉',
                  sublabel: '全部',
                  icon: Icons.power_settings_new,
                  active: !do1 && !do2,
                  busy: busy,
                  onPressed: () => _setAll(context, false),
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: busy ? null : () => _setAll(context, true),
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('全部開啟'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: busy ? null : () => app.loadState(device.id),
                  icon: const Icon(Icons.sync),
                  label: const Text('同步狀態'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _set(BuildContext context, int channel, bool on) async {
    final app = context.read<AppState>();
    final messenger = ScaffoldMessenger.of(context);
    final err = await app.setDo(device.id, channel: channel, on: on);
    if (err != null) {
      messenger.showSnackBar(SnackBar(content: Text('操作失敗：$err')));
    }
  }

  Future<void> _setAll(BuildContext context, bool on) async {
    final app = context.read<AppState>();
    final messenger = ScaffoldMessenger.of(context);
    final err = await app.setAllDo(device.id, on: on);
    if (err != null) {
      messenger.showSnackBar(SnackBar(content: Text('操作失敗：$err')));
    }
  }
}

class _SpeedButton extends StatelessWidget {
  const _SpeedButton({
    required this.label,
    required this.sublabel,
    required this.icon,
    required this.active,
    required this.busy,
    required this.onPressed,
  });

  final String label;
  final String sublabel;
  final IconData icon;
  final bool active;
  final bool busy;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return InkWell(
      onTap: busy ? null : onPressed,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 18),
        decoration: BoxDecoration(
          color: active ? scheme.primaryContainer : scheme.surfaceContainerLow,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: active ? scheme.primary : scheme.outlineVariant,
            width: active ? 2 : 1,
          ),
        ),
        child: Column(
          children: [
            if (busy)
              SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: active ? scheme.onPrimaryContainer : scheme.primary,
                ),
              )
            else
              Icon(
                icon,
                size: 26,
                color: active ? scheme.onPrimaryContainer : scheme.onSurfaceVariant,
              ),
            const SizedBox(height: 8),
            Text(
              label,
              style: TextStyle(
                fontWeight: FontWeight.w700,
                fontSize: 15,
                color: active ? scheme.onPrimaryContainer : scheme.onSurface,
              ),
            ),
            Text(
              sublabel,
              style: TextStyle(
                fontSize: 11,
                color: active
                    ? scheme.onPrimaryContainer.withValues(alpha: 0.75)
                    : scheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------
// AT 指令測試
// ----------------------------------------------------------------------
class _AtCommandPanel extends StatelessWidget {
  const _AtCommandPanel({
    required this.controller,
    required this.busy,
    required this.response,
    required this.device,
    required this.onSend,
  });

  final TextEditingController controller;
  final bool busy;
  final String? response;
  final Device device;
  final Future<void> Function(Device, String) onSend;

  static const _presets = [
    'AT',
    'AT+NAME=?',
    'AT+DEVICEINFO=?',
    'AT+STACH0?',
    'AT+OCCH0',
    'AT+WSLK?',
  ];

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            title: 'AT 指令測試',
            subtitle: '除錯用，直接對設備送出 AT 指令',
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: _presets
                .map((p) => ActionChip(
                      label: Text(p, style: const TextStyle(fontSize: 11)),
                      onPressed: busy ? null : () => controller.text = p,
                      visualDensity: VisualDensity.compact,
                    ))
                .toList(),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: controller,
                  enabled: !busy,
                  decoration: const InputDecoration(
                    labelText: 'AT 指令',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  onSubmitted: (v) => onSend(device, v.trim()),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(
                onPressed: busy ? null : () => onSend(device, controller.text.trim()),
                child: busy
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('送出'),
              ),
            ],
          ),
          if (response != null) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: scheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: SelectableText(
                response!.isEmpty ? '（無回覆）' : response!,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
