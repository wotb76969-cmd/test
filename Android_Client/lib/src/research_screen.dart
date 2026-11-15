import 'dart:async';

import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import 'api.dart';
import 'logic.dart';
import 'theme.dart';

/// Start/stop research jobs and decide which found materials go to the
/// personal knowledge base.
class ResearchScreen extends StatefulWidget {
  const ResearchScreen({super.key, required this.api});
  final HostApi api;

  @override
  State<ResearchScreen> createState() => _ResearchScreenState();
}

class _ResearchScreenState extends State<ResearchScreen> {
  final _topic = TextEditingController();
  List<Map<String, dynamic>> _jobs = [];
  Map<String, dynamic>? _selected;
  List<Map<String, dynamic>> _items = [];
  List<Map<String, dynamic>> _journal = [];
  Timer? _timer;
  String? _error;

  static const _learnIcon = {
    'learned': '📚',
    'added': '➕',
    'auto-job': '🔎',
    'auto-done': '✅',
    'skipped': '⏸',
    'limit': '⛔',
    'error': '⚠️',
  };

  static const _status = {
    'queued': 'в очереди',
    'running': 'идёт',
    'done': 'готово',
    'error': 'ошибка',
    'stopped': 'остановлено',
    'fetched': 'на проверке',
    'accepted': 'в базе',
    'rejected': 'отклонено',
    'duplicate': 'дубликат',
    'skipped': 'пропущено',
  };

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _topic.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final jobs = await widget.api.researchJobs();
      final journal = await widget.api.learningJournal();
      List<Map<String, dynamic>> items = _items;
      if (_selected != null) {
        final data = await widget.api.researchJob(_selected!['id'] as String);
        items = (data['items'] as List? ?? []).whereType<Map<String, dynamic>>().toList();
        _selected = data['job'] as Map<String, dynamic>?;
      }
      if (mounted) {
        setState(() {
          _jobs = jobs;
          _journal = journal;
          _items = items;
          _error = null;
        });
      }
    } on HostException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _start() async {
    final topic = _topic.text.trim();
    if (topic.length < 3) return;
    try {
      await widget.api.startResearch(topic);
      _topic.clear();
      await _refresh();
    } on HostException catch (e) {
      setState(() => _error = e.message);
    }
  }

  Future<void> _review(Map<String, dynamic> item, String action) async {
    try {
      await widget.api.reviewItem(item['id'] as int, action);
      await _refresh();
    } on HostException catch (e) {
      setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Исследования')),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(14), children: [
          const Text(
            'Chelsy читает открытые источники с соблюдением robots.txt. Материалы попадают в базу знаний только после твоего решения. Это пополнение базы знаний, а не переобучение модели.',
            style: TextStyle(color: ChelsyColors.muted, fontSize: 13),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _topic,
            decoration: InputDecoration(
              labelText: 'Тема или ссылка',
              hintText: 'документация FastAPI или https://…',
              suffixIcon: IconButton(icon: const Icon(Icons.play_arrow_rounded), tooltip: 'Начать', onPressed: _start),
            ),
            onSubmitted: (_) => _start(),
          ),
          if (_error != null)
            Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: const TextStyle(color: ChelsyColors.danger))),
          const SizedBox(height: 14),
          ..._jobs.map((j) => Card(
                color: ChelsyColors.panel,
                child: ListTile(
                  selected: _selected?['id'] == j['id'],
                  title: Text((j['topic'] ?? '') as String, maxLines: 2, overflow: TextOverflow.ellipsis),
                  subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const SizedBox(height: 4),
                    LinearProgressIndicator(value: ((j['progress'] ?? 0) as num).toDouble(), color: ChelsyColors.teal),
                    const SizedBox(height: 4),
                    Text('${_status[j['status']] ?? j['status']} · ${j['message'] ?? ''}', style: const TextStyle(fontSize: 12)),
                  ]),
                  trailing: (j['status'] == 'running' || j['status'] == 'queued')
                      ? IconButton(
                          tooltip: 'Остановить',
                          icon: const Icon(Icons.stop_circle_outlined),
                          onPressed: () async {
                            await widget.api.stopResearch(j['id'] as String);
                            await _refresh();
                          })
                      : null,
                  onTap: () {
                    setState(() {
                      _selected = j;
                      _items = [];
                    });
                    _refresh();
                  },
                ),
              )),
          if (_journal.isNotEmpty) ...[
            const SizedBox(height: 16),
            const Text('Автоизучение — что Chelsy изучила сама', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            ..._journal.take(15).map((e) => ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: Text(_learnIcon[e['event']] ?? '•', style: const TextStyle(fontSize: 18)),
                  title: Text((e['title'] ?? '') as String, maxLines: 2, overflow: TextOverflow.ellipsis),
                  subtitle: Text(
                      '${((e['created'] ?? '') as String).replaceFirst('T', ' ').padRight(16).substring(0, 16)} · ${e['detail'] ?? ''}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 12, color: ChelsyColors.muted)),
                )),
          ],
          if (_selected != null) ...[
            const SizedBox(height: 12),
            const Text('Найденные материалы', style: TextStyle(fontWeight: FontWeight.w600)),
            ..._items.map((i) => Card(
                  color: ChelsyColors.panel2,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text((i['title'] ?? i['url']) as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 4),
                      Text(
                          '${_status[i['status']] ?? i['status']} · доверие ${((i['trust'] ?? 0) as num).toStringAsFixed(2)}'
                          ' · ${((i['published'] ?? '') as String).isEmpty ? 'дата неизвестна' : (i['published'] as String).substring(0, 10)}',
                          style: const TextStyle(fontSize: 12, color: ChelsyColors.muted)),
                      if (((i['note'] ?? '') as String).isNotEmpty)
                        Text(i['note'] as String, style: const TextStyle(fontSize: 12, color: ChelsyColors.muted)),
                      Row(children: [
                        TextButton.icon(
                          onPressed: isSafeLink(i['url'] as String?)
                              ? () => launchUrl(Uri.parse(i['url'] as String), mode: LaunchMode.externalApplication)
                              : null,
                          icon: const Icon(Icons.open_in_new, size: 16),
                          label: const Text('Источник'),
                        ),
                        const Spacer(),
                        if (i['status'] == 'fetched') ...[
                          TextButton(onPressed: () => _review(i, 'reject'), child: const Text('Отклонить')),
                          FilledButton(onPressed: () => _review(i, 'accept'), child: const Text('В базу')),
                        ],
                      ]),
                    ]),
                  ),
                )),
          ],
        ]),
      ),
    );
  }
}
