/// 共用的小工具元件。
library;

import 'package:flutter/material.dart';

import '../api/ws_client.dart';

/// WebSocket 即時推播狀態指示燈。
///
/// ★ 這個指示燈的意義：
///   「即時」= WebSocket 已連線，狀態變更會馬上反映到畫面
///   「輪詢」= WebSocket 沒連上，畫面靠 5 秒輪詢更新
///   ★ 兩種模式功能都正常，只是體感不同。
class LiveBadge extends StatelessWidget {
  const LiveBadge({super.key, required this.status, this.eventCount = 0});

  final WsStatus status;
  final int eventCount;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    late final Color bg;
    late final Color fg;
    late final String text;
    late final IconData icon;

    switch (status) {
      case WsStatus.connected:
        bg = const Color(0xFFE3F9E5);
        fg = const Color(0xFF1B5E20);
        text = '即時';
        icon = Icons.bolt;
        break;
      case WsStatus.connecting:
        bg = scheme.surfaceContainerHighest;
        fg = scheme.onSurfaceVariant;
        text = '連線中';
        icon = Icons.sync;
        break;
      case WsStatus.disconnected:
        bg = const Color(0xFFFFF4E5);
        fg = const Color(0xFFE65100);
        text = '輪詢';
        icon = Icons.schedule;
        break;
      case WsStatus.idle:
        bg = scheme.surfaceContainerHighest;
        fg = scheme.onSurfaceVariant;
        text = '輪詢';
        icon = Icons.schedule;
        break;
    }

    return Tooltip(
      message: switch (status) {
        WsStatus.connected =>
          'WebSocket 已連線，狀態變更會即時更新（已收到 $eventCount 次推播）',
        WsStatus.connecting => '正在建立 WebSocket 連線…',
        WsStatus.disconnected =>
          'WebSocket 未連上，改用 5 秒輪詢更新（功能正常）',
        WsStatus.idle => '尚未啟動 WebSocket，使用 5 秒輪詢更新',
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14, color: fg),
            const SizedBox(width: 4),
            Text(
              text,
              style: TextStyle(
                color: fg,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 連線狀態指示燈。
class ConnectionBadge extends StatelessWidget {
  const ConnectionBadge({super.key, required this.connected, this.label});

  final bool? connected;
  final String? label;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    late final Color bg;
    late final Color fg;
    late final String text;

    if (connected == null) {
      bg = scheme.surfaceContainerHighest;
      fg = scheme.onSurfaceVariant;
      text = label ?? '未查詢';
    } else if (connected!) {
      bg = const Color(0xFFE3F9E5);
      fg = const Color(0xFF1B5E20);
      text = label ?? '已連線';
    } else {
      bg = const Color(0xFFFFEBEE);
      fg = const Color(0xFFB71C1C);
      text = label ?? '離線';
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: fg,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// 資訊卡片。
class InfoCard extends StatelessWidget {
  const InfoCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
  });

  final Widget child;
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 0,
      color: Theme.of(context).colorScheme.surfaceContainerLow,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(
          color: Theme.of(context).colorScheme.outlineVariant,
        ),
      ),
      child: Padding(padding: padding, child: child),
    );
  }
}

/// 帶標題的區塊。
class SectionHeader extends StatelessWidget {
  const SectionHeader({super.key, required this.title, this.subtitle, this.trailing});

  final String title;
  final String? subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
              ),
              if (subtitle != null) ...[
                const SizedBox(height: 2),
                Text(
                  subtitle!,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                ),
              ],
            ],
          ),
        ),
        if (trailing case final Widget widget) widget,
      ],
    );
  }
}

/// 空狀態提示。
class EmptyHint extends StatelessWidget {
  const EmptyHint({super.key, required this.icon, required this.title, this.message});

  final IconData icon;
  final String title;
  final String? message;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 56, color: scheme.outline),
          const SizedBox(height: 12),
          Text(
            title,
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  color: scheme.onSurfaceVariant,
                ),
          ),
          if (message != null) ...[
            const SizedBox(height: 6),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Text(
                message!,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: scheme.outline,
                    ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 錯誤訊息列。
class ErrorBar extends StatelessWidget {
  const ErrorBar({super.key, required this.message, this.onDismiss});

  final String message;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: scheme.errorContainer,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(Icons.error_outline, size: 18, color: scheme.onErrorContainer),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: scheme.onErrorContainer, fontSize: 13),
            ),
          ),
          if (onDismiss != null)
            IconButton(
              icon: const Icon(Icons.close, size: 16),
              color: scheme.onErrorContainer,
              onPressed: onDismiss,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
            ),
        ],
      ),
    );
  }
}
