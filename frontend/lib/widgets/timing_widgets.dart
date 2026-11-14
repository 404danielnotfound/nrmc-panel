/// 耗時瀑布圖 / 操作時間線元件。
///
/// ★ 這個元件存在的理由
/// ---------------------------------------------------------------------------
/// 使用者反映「按下去要 5~10 秒風機才動作」。要找出瓶頸在哪一段，
/// 必須把一次操作的時間拆開來看：
///
///     建立 TCP 連線  22ms
///     AT+STACH1=1   53ms
///     AT+STACH0?   440ms   ← 慢這裡
///     ───────────────
///     總計         515ms
///
/// 瀑布圖用「色塊寬度 = 該步驟耗時」呈現，
/// 最慢的一段標紅，一眼就能看出瓶頸。
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import 'common.dart';

/// 操作狀態的中文說明。
String opPhaseLabel(OpPhase phase) {
  switch (phase) {
    case OpPhase.idle:
      return '閒置';
    case OpPhase.sending:
      return '送出中';
    case OpPhase.verifying:
      return '回讀確認中';
    case OpPhase.confirmed:
      return '已確認';
    case OpPhase.failed:
      return '失敗';
  }
}

/// 操作狀態的顏色。
Color opPhaseColor(OpPhase phase, ColorScheme scheme) {
  switch (phase) {
    case OpPhase.idle:
      return scheme.onSurfaceVariant;
    case OpPhase.sending:
      return scheme.primary;
    case OpPhase.verifying:
      return scheme.tertiary;
    case OpPhase.confirmed:
      return Colors.green.shade700;
    case OpPhase.failed:
      return scheme.error;
  }
}

/// 操作狀態的圖示。
IconData opPhaseIcon(OpPhase phase) {
  switch (phase) {
    case OpPhase.idle:
      return Icons.circle_outlined;
    case OpPhase.sending:
      return Icons.upload;
    case OpPhase.verifying:
      return Icons.sync;
    case OpPhase.confirmed:
      return Icons.check_circle;
    case OpPhase.failed:
      return Icons.error;
  }
}

/// 單一操作的耗時瀑布圖。
///
/// [timings] 是後端回傳的逐段耗時。
/// [totalMs] 是這次操作的總耗時（含 HTTP 往返等非 AT 的時間）。
class TimingWaterfall extends StatelessWidget {
  const TimingWaterfall({
    super.key,
    required this.timings,
    this.totalMs,
    this.compact = false,
  });

  final List<OperationTiming> timings;

  /// 總耗時；為 null 時由 timings 加總。
  final double? totalMs;

  /// 精簡模式（只用於窄空間）。
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    if (timings.isEmpty) {
      return Text(
        '（沒有耗時資料）',
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
              color: scheme.onSurfaceVariant,
            ),
      );
    }

    final total = totalMs ?? timings.fold<double>(0.0, (s, t) => s + t.ms);
    // 比例尺：讓最慢的一段佔滿寬度，其他段按比例。
    final maxMs = timings.fold<double>(0.0, (m, t) => t.ms > m ? t.ms : m);
    final slowest = timings.firstWhere(
      (t) => t.ms == maxMs,
      orElse: () => timings.first,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final t in timings) ...[
          _TimingRow(
            timing: t,
            maxMs: maxMs,
            isSlowest: identical(t, slowest),
            compact: compact,
          ),
          const SizedBox(height: 4),
        ],
        const Divider(height: 12),
        Row(
          children: [
            Icon(Icons.timer_outlined, size: 15, color: scheme.onSurfaceVariant),
            const SizedBox(width: 6),
            Text(
              '總計 ${total.toStringAsFixed(0)} ms',
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
            ),
            const Spacer(),
            if (slowest.ms > 0)
              Text(
                '瓶頸：${slowest.label} (${slowest.ms.toStringAsFixed(0)}ms)',
                style: TextStyle(
                  fontSize: 11,
                  color: scheme.error,
                  fontWeight: FontWeight.w600,
                ),
              ),
          ],
        ),
      ],
    );
  }
}

/// 瀑布圖中的一列。
class _TimingRow extends StatelessWidget {
  const _TimingRow({
    required this.timing,
    required this.maxMs,
    required this.isSlowest,
    required this.compact,
  });

  final OperationTiming timing;
  final double maxMs;
  final bool isSlowest;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    // 色塊顏色：最慢的標紅，其餘用藍色系；複用的連線用綠色。
    final Color barColor;
    if (timing.reused) {
      barColor = Colors.green.shade600;
    } else if (isSlowest) {
      barColor = scheme.error;
    } else if (!timing.ok) {
      barColor = scheme.errorContainer;
    } else {
      barColor = scheme.primary;
    }

    final fraction = maxMs <= 0 ? 0.0 : (timing.ms / maxMs).clamp(0.0, 1.0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            SizedBox(
              width: compact ? 110 : 150,
              child: Text(
                timing.label,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11, fontFamily: 'monospace'),
              ),
            ),
            const SizedBox(width: 6),
            Text(
              '${timing.ms.toStringAsFixed(0)}ms',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w700,
                fontFamily: 'monospace',
                color: isSlowest ? scheme.error : scheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        const SizedBox(height: 2),
        // ★ 色塊寬度 = 該步驟占最慢步驟的比例，視覺化瓶頸。
        LayoutBuilder(
          builder: (context, constraints) {
            return Stack(
              children: [
                Container(
                  height: 8,
                  decoration: BoxDecoration(
                    color: scheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
                Container(
                  height: 8,
                  width: constraints.maxWidth * fraction,
                  decoration: BoxDecoration(
                    color: barColor,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ],
            );
          },
        ),
      ],
    );
  }
}

/// 目前裝置的操作狀態卡（顯示在風機控制區上方）。
///
/// ★ 這是解決「使用者不知道指令是否已送出」的核心元件。
///   按下按鈕後立刻顯示「送出中…」，
///   讓使用者知道指令已經在路上了，不會再按第二次。
class OperationStatusCard extends StatelessWidget {
  const OperationStatusCard({
    super.key,
    required this.deviceId,
    required this.label,
  });

  final int deviceId;
  final String label;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final scheme = Theme.of(context).colorScheme;
    final phase = app.opPhaseOf(deviceId);
    final record = app.opRecordOf(deviceId);

    if (phase == OpPhase.idle || record == null) {
      return const SizedBox.shrink();
    }

    final isBusy = phase == OpPhase.sending || phase == OpPhase.verifying;
    final color = opPhaseColor(phase, scheme);

    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: isBusy ? scheme.primary : color.withValues(alpha: 0.4),
          width: isBusy ? 2 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (isBusy)
                SizedBox(
                  width: 15,
                  height: 15,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: color,
                  ),
                )
              else
                Icon(opPhaseIcon(phase), size: 17, color: color),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '$label — ${opPhaseLabel(phase)}',
                  style: TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 13,
                    color: color,
                  ),
                ),
              ),
              if (record.totalMs > 0)
                Text(
                  '${record.totalMs.toStringAsFixed(0)} ms',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: scheme.onSurfaceVariant,
                    fontFamily: 'monospace',
                  ),
                ),
            ],
          ),
          if (record.error.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              record.error,
              style: TextStyle(fontSize: 12, color: scheme.error),
            ),
          ],
          if (record.message.isNotEmpty && record.error.isEmpty) ...[
            const SizedBox(height: 4),
            Text(
              record.message,
              style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
            ),
          ],
          if (record.timings.isNotEmpty) ...[
            const SizedBox(height: 10),
            TimingWaterfall(
              timings: record.timings,
              totalMs: record.totalMs,
            ),
          ],
        ],
      ),
    );
  }
}

/// 最近操作紀錄（全域時間線）。
///
/// 顯示過去幾次操作的結果與耗時，方便比較
/// 「第一次很慢（要建立連線）」與「後續很快（連線複用）」的差別。
class OperationTimeline extends StatelessWidget {
  const OperationTimeline({super.key, this.limit = 8});

  /// 最多顯示幾筆。
  final int limit;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final records = app.operationLog.take(limit).toList();

    if (records.isEmpty) return const SizedBox.shrink();

    return InfoCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SectionHeader(
            title: '最近操作',
            subtitle: '每次操作的逐段耗時（找出瓶頸）',
          ),
          const SizedBox(height: 12),
          for (final rec in records) ...[
            _TimelineRow(record: rec),
            const SizedBox(height: 8),
          ],
        ],
      ),
    );
  }
}

class _TimelineRow extends StatelessWidget {
  const _TimelineRow({required this.record});

  final OperationRecord record;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = opPhaseColor(record.phase, scheme);
    final time = record.startedAt;
    final hh = time.hour.toString().padLeft(2, '0');
    final mm = time.minute.toString().padLeft(2, '0');
    final ss = time.second.toString().padLeft(2, '0');
    final ms = time.millisecond.toString().padLeft(3, '0');

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(8),
        border: Border(
          left: BorderSide(color: color, width: 3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(opPhaseIcon(record.phase), size: 15, color: color),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  record.label,
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 13,
                  ),
                ),
              ),
              Text(
                '$hh:$mm:$ss.$ms',
                style: TextStyle(
                  fontSize: 11,
                  fontFamily: 'monospace',
                  color: scheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(width: 10),
              Text(
                '${record.totalMs.toStringAsFixed(0)}ms',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  fontFamily: 'monospace',
                  color: record.phase == OpPhase.failed
                      ? scheme.error
                      : scheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
          if (record.error.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              record.error,
              style: TextStyle(fontSize: 11, color: scheme.error),
            ),
          ],
          if (record.timings.isNotEmpty) ...[
            const SizedBox(height: 8),
            TimingWaterfall(timings: record.timings, compact: true),
          ],
        ],
      ),
    );
  }
}
