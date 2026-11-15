import 'package:material_ui/material_ui.dart';

import 'logic.dart';
import 'theme.dart';

/// Chelsy Pulse: a small, cheap status animation (one ring, one dot).
/// Respects the system "remove animations" accessibility setting.
class ChelsyPulse extends StatefulWidget {
  const ChelsyPulse({super.key, required this.state, this.size = 14});
  final PulseState state;
  final double size;

  static Color colorFor(PulseState s) => switch (s) {
        PulseState.ready => ChelsyColors.teal,
        PulseState.generating => ChelsyColors.lavender,
        PulseState.searching => ChelsyColors.teal,
        PulseState.syncing => ChelsyColors.violet,
        PulseState.offline => ChelsyColors.danger,
      };

  static String labelFor(PulseState s) => switch (s) {
        PulseState.ready => 'Готова к работе',
        PulseState.generating => 'Генерирует ответ',
        PulseState.searching => 'Ищет информацию',
        PulseState.syncing => 'Синхронизирует данные',
        PulseState.offline => 'Сервер недоступен',
      };

  @override
  State<ChelsyPulse> createState() => _ChelsyPulseState();
}

class _ChelsyPulseState extends State<ChelsyPulse> with SingleTickerProviderStateMixin {
  late final AnimationController _controller =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1600));

  bool get _active => widget.state != PulseState.ready && widget.state != PulseState.offline;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sync();
  }

  @override
  void didUpdateWidget(covariant ChelsyPulse oldWidget) {
    super.didUpdateWidget(oldWidget);
    _sync();
  }

  void _sync() {
    final reduce = MediaQuery.maybeOf(context)?.disableAnimations ?? false;
    if (_active && !reduce) {
      if (!_controller.isAnimating) _controller.repeat();
    } else {
      _controller.stop();
      _controller.value = 0;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = ChelsyPulse.colorFor(widget.state);
    return Semantics(
      label: ChelsyPulse.labelFor(widget.state),
      child: SizedBox(
        width: widget.size,
        height: widget.size,
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, _) {
            final t = _controller.value;
            return Stack(alignment: Alignment.center, children: [
              if (_active)
                Container(
                  width: widget.size * (0.6 + t * 0.9),
                  height: widget.size * (0.6 + t * 0.9),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: color.withValues(alpha: 1 - t), width: 2),
                  ),
                ),
              Container(
                width: widget.size * 0.55,
                height: widget.size * 0.55,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
            ]);
          },
        ),
      ),
    );
  }
}
