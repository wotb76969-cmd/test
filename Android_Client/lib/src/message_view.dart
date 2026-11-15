import 'package:material_ui/material_ui.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import 'logic.dart';
import 'theme.dart';

/// One chat message: user bubble or Chelsy's Markdown answer with actions.
class MessageView extends StatelessWidget {
  const MessageView({
    super.key,
    required this.message,
    this.onEdit,
    this.onRegenerate,
    this.onRate,
  });

  final ChatMessage message;
  final VoidCallback? onEdit;
  final VoidCallback? onRegenerate;
  final void Function(int rating)? onRate;

  Future<void> _openLink(BuildContext context, String? href) async {
    if (!isSafeLink(href)) return;
    await launchUrl(Uri.parse(href!), mode: LaunchMode.externalApplication);
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final maxWidth = width > 900 ? 760.0 : width * 0.9;
    if (message.fromUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxWidth * 0.85),
          child: GestureDetector(
            onLongPress: onEdit,
            child: Container(
              margin: const EdgeInsets.symmetric(vertical: 6),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: ChelsyColors.userBubble,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFF3A2F7D)),
              ),
              child: SelectableText(message.content, style: const TextStyle(fontSize: 15, height: 1.45)),
            ),
          ),
        ),
      );
    }
    // Built explicitly (not fromTheme): flutter_markdown_plus still targets
    // the legacy flutter/material ThemeData type.
    const body = TextStyle(fontSize: 15, height: 1.55, color: ChelsyColors.text);
    final style = MarkdownStyleSheet(
      p: body,
      listBullet: body,
      tableBody: body,
      tableHead: body.copyWith(fontWeight: FontWeight.w600),
      strong: const TextStyle(fontWeight: FontWeight.w700),
      em: const TextStyle(fontStyle: FontStyle.italic),
      h1: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: ChelsyColors.text),
      h2: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700, color: ChelsyColors.text),
      h3: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: ChelsyColors.text),
      blockSpacing: 10,
      codeblockPadding: const EdgeInsets.all(12),
      horizontalRuleDecoration: const BoxDecoration(border: Border(top: BorderSide(color: ChelsyColors.stroke))),
      code: const TextStyle(fontFamily: 'monospace', fontSize: 13, backgroundColor: Color(0xFF1A1D40)),
      codeblockDecoration: BoxDecoration(
        color: const Color(0xFF0E1029),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: ChelsyColors.stroke),
      ),
      a: const TextStyle(color: ChelsyColors.lavender, decoration: TextDecoration.underline),
      blockquoteDecoration: BoxDecoration(
        color: ChelsyColors.panel2,
        borderRadius: BorderRadius.circular(8),
      ),
    );
    return Align(
      alignment: Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(9),
              child: Image.asset('chelsy_logo.png', width: 28, height: 28, cacheWidth: 84),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                MarkdownBody(
                  data: message.content.isEmpty ? '…' : message.content,
                  selectable: true,
                  styleSheet: style,
                  onTapLink: (text, href, title) => _openLink(context, href),
                ),
                if (!message.pending)
                  Row(children: [
                    _Action(icon: Icons.copy_rounded, label: 'Копировать', onTap: () {
                      Clipboard.setData(ClipboardData(text: message.content));
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Ответ скопирован')));
                    }),
                    if (onRegenerate != null) _Action(icon: Icons.refresh_rounded, label: 'Повторить', onTap: onRegenerate!),
                    if (onRate != null && message.id != null) ...[
                      _Action(
                          icon: message.rating > 0 ? Icons.thumb_up_alt : Icons.thumb_up_alt_outlined,
                          label: 'Хороший ответ',
                          onTap: () => onRate!(1)),
                      _Action(
                          icon: message.rating < 0 ? Icons.thumb_down_alt : Icons.thumb_down_alt_outlined,
                          label: 'Плохой ответ',
                          onTap: () => onRate!(-1)),
                    ],
                  ]),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Action extends StatelessWidget {
  const _Action({required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => IconButton(
        tooltip: label,
        visualDensity: VisualDensity.compact,
        iconSize: 18,
        color: ChelsyColors.muted,
        onPressed: onTap,
        icon: Icon(icon),
      );
}
