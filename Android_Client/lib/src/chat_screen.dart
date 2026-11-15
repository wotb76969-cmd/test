import 'dart:async';

import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'api.dart';
import 'local_store.dart';
import 'logic.dart';
import 'message_view.dart';
import 'pulse.dart';
import 'research_screen.dart';
import 'theme.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.onSignedOut});
  final VoidCallback onSignedOut;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with WidgetsBindingObserver {
  final _input = TextEditingController();
  final _search = TextEditingController();
  final _scroll = ScrollController();
  HostApi? _api;
  LocalStore? _store;
  String _readOnlyLabel = 'Хост недоступен — показана история из облака (только чтение).';
  List<Conversation> _conversations = [];
  List<ChatMessage> _messages = [];
  String? _conversationId;
  String _title = 'Новый чат';
  PulseState _pulse = PulseState.ready;
  String _statusText = 'Подключение…';
  bool _generating = false;
  bool _offline = false;
  bool _readOnlyCloud = false;
  String? _requestId;
  List<String> _models = [];
  String? _model;
  bool _web = true;
  Timer? _searchDebounce;
  StreamSubscription<Map<String, dynamic>>? _stream;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _init();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _stream?.cancel();
    _searchDebounce?.cancel();
    _input.dispose();
    _search.dispose();
    _scroll.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refreshStatus();
  }

  Future<void> _init() async {
    final prefs = await SharedPreferences.getInstance();
    final host = prefs.getString('chelsy_host') ?? '';
    _web = prefs.getBool('chelsy_web') ?? true;
    final uid = Supabase.instance.client.auth.currentUser?.id;
    if (uid != null) {
      try {
        _store = await LocalStore.forUser(uid);
      } catch (_) {
        _store = null; // storage unavailable: the app still works online
      }
    }
    try {
      _api = HostApi(host);
    } on FormatException catch (e) {
      _setOffline(e.message);
      return;
    }
    await _refreshStatus();
    await _loadConversations(openLatest: true);
  }

  void _setOffline(String text) {
    if (!mounted) return;
    setState(() {
      _offline = true;
      _pulse = PulseState.offline;
      _statusText = text;
    });
  }

  Future<void> _refreshStatus() async {
    final api = _api;
    if (api == null) return;
    try {
      final status = await api.status();
      final models = (status['models'] as List? ?? []).whereType<String>().toList();
      if (!mounted) return;
      setState(() {
        _offline = false;
        _models = models;
        _model ??= models.contains(status['default_model']) ? status['default_model'] as String : (models.isEmpty ? null : models.first);
        final ollama = status['ollama'] == true;
        _pulse = _generating ? _pulse : (ollama ? PulseState.ready : PulseState.offline);
        _statusText = ollama ? 'Готова к работе' : 'Модель на хосте недоступна';
      });
    } on HostException catch (e) {
      if (e.status == 401) return _signOut();
      _setOffline(e.message);
    }
  }

  Future<void> _loadConversations({bool openLatest = false}) async {
    final api = _api;
    if (api == null) return;
    try {
      final items = await api.conversations(query: _search.text.trim());
      if (_search.text.trim().isEmpty) unawaited(_store?.saveConversations(items));
      if (!mounted) return;
      setState(() => _conversations = items);
      if (openLatest && items.isNotEmpty && _conversationId == null) await _open(items.first);
    } on HostException catch (e) {
      if (e.status == 401) return _signOut();
      _setOffline(e.message);
      final cached = await _store?.loadConversations() ?? [];
      if (cached.isNotEmpty) {
        if (!mounted) return;
        setState(() => _conversations = cached);
        if (openLatest && _conversationId == null) await _openCached(cached.first);
      } else if (openLatest) {
        await _loadCloudHistory();
      }
    }
  }

  /// Host offline: read-only history straight from Supabase (protected by RLS).
  Future<void> _loadCloudHistory() async {
    final user = Supabase.instance.client.auth.currentUser;
    if (user == null) return;
    try {
      final rows = await Supabase.instance.client
          .from('chelsy_ai_messages')
          .select('role,content,created_at')
          .eq('user_id', user.id)
          .order('created_at', ascending: false)
          .limit(200);
      if (!mounted) return;
      setState(() {
        _readOnlyCloud = true;
        _readOnlyLabel = 'Хост недоступен — показана история из облака (только чтение).';
        _title = 'История из облака';
        _messages = rows.reversed.map((r) => ChatMessage.fromJson(Map<String, dynamic>.from(r))).toList();
      });
      _scrollDown();
    } catch (_) {
      // No internet at all: keep the offline banner.
    }
  }

  /// Offline: open the copy stored on this phone (read-only).
  Future<void> _openCached(Conversation c) async {
    final msgs = await _store?.loadMessages(c.id) ?? [];
    if (!mounted) return;
    setState(() {
      _conversationId = c.id;
      _title = c.title;
      _messages = msgs;
      _readOnlyCloud = true;
      _readOnlyLabel = 'Хост недоступен — показана копия, сохранённая на телефоне (только чтение).';
    });
    _scrollDown(jump: true);
  }

  Future<void> _open(Conversation c) async {
    if (_offline) return _openCached(c);
    final api = _api;
    if (api == null || _generating) return;
    setState(() {
      _conversationId = c.id;
      _title = c.title;
      _readOnlyCloud = false;
    });
    try {
      final msgs = await api.messages(c.id);
      unawaited(_store?.saveMessages(c.id, msgs));
      if (!mounted) return;
      setState(() => _messages = msgs);
      _scrollDown(jump: true);
    } on HostException catch (e) {
      if (e.status == 401) return _signOut();
      if (e.offline) {
        _setOffline(e.message);
        return _openCached(c);
      }
      _snack(e.message);
    }
  }

  void _newChat() {
    if (_generating) return;
    setState(() {
      _conversationId = null;
      _messages = [];
      _title = 'Новый чат';
      _readOnlyCloud = false;
    });
  }

  void _scrollDown({bool jump = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      final target = _scroll.position.maxScrollExtent;
      if (jump) {
        _scroll.jumpTo(target);
      } else {
        _scroll.animateTo(target, duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
      }
    });
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _send({String? text, bool regenerate = false, int? editId}) async {
    final api = _api;
    final message = (text ?? _input.text).trim();
    if (api == null || _generating || _offline || _readOnlyCloud) return;
    if (!regenerate && message.isEmpty) return;
    final requestId = newRequestId();
    final answer = ChatMessage(role: 'assistant', content: '', pending: true);
    setState(() {
      if (!regenerate) {
        _input.clear();
        if (editId != null) {
          final idx = _messages.indexWhere((m) => m.id == editId);
          if (idx >= 0) _messages = _messages.sublist(0, idx);
        }
        _messages.add(ChatMessage(role: 'user', content: message));
      } else {
        while (_messages.isNotEmpty && !_messages.last.fromUser) {
          _messages.removeLast();
        }
      }
      _messages.add(answer);
      _generating = true;
      _requestId = requestId;
      _pulse = PulseState.generating;
      _statusText = 'Генерирует ответ…';
    });
    _scrollDown();
    final completer = Completer<void>();
    _stream = api
        .chat(
          message: message,
          conversationId: _conversationId,
          model: _model,
          web: _web,
          regenerate: regenerate,
          editMessageId: editId,
          requestId: requestId,
        )
        .listen((event) {
      if (!mounted) return;
      setState(() {
        switch (event['type']) {
          case 'start':
            _conversationId = (event['conversation_id'] as String?) ?? _conversationId;
          case 'status':
            _pulse = pulseFromHost(event['state'] as String?);
            _statusText = (event['text'] ?? '') as String;
            if (answer.content.isEmpty) answer.content = '_${event['text'] ?? ''}_';
          case 'delta':
            if (answer.content.startsWith('_') && answer.content.endsWith('_')) answer.content = '';
            answer.content += (event['text'] ?? '') as String;
          case 'replace':
            answer.content = (event['text'] ?? '') as String;
          case 'done':
            answer.content = (event['text'] ?? '') as String;
            answer.pending = false;
          case 'error':
            answer.content = '⚠️ ${event['message'] ?? 'Ошибка хоста'}';
            answer.pending = false;
        }
      });
      _scrollDown();
    }, onError: (Object error) {
      if (!mounted) return;
      setState(() {
        answer.content = '⚠️ ${error is HostException ? error.message : 'Соединение с хостом прервано.'}';
        answer.pending = false;
        if (error is HostException && error.offline) {
          _offline = true;
          _pulse = PulseState.offline;
        }
      });
      if (error is HostException && error.status == 401) _signOut();
      completer.complete();
    }, onDone: completer.complete, cancelOnError: true);
    await completer.future;
    if (!mounted) return;
    setState(() {
      _generating = false;
      _requestId = null;
      if (!_offline) {
        _pulse = PulseState.ready;
        _statusText = 'Готова к работе';
      }
    });
    if (_conversationId != null && !_offline) {
      try {
        final msgs = await api.messages(_conversationId!);
        unawaited(_store?.saveMessages(_conversationId!, msgs));
        if (mounted) setState(() => _messages = msgs);
      } on HostException {
        // keep the streamed copy
      }
    }
    _loadConversations();
  }

  Future<void> _stop() async {
    final id = _requestId;
    if (id == null || _api == null) return;
    try {
      await _api!.stop(id);
    } on HostException {
      await _stream?.cancel();   // host unreachable: drop the stream locally
    }
  }

  Future<void> _editMessage(ChatMessage m) async {
    if (m.id == null || _generating) return;
    final controller = TextEditingController(text: m.content);
    final text = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Изменить сообщение'),
        content: TextField(controller: controller, maxLines: 6, minLines: 1, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(context, controller.text), child: const Text('Отправить')),
        ],
      ),
    );
    controller.dispose();
    if (text != null && text.trim().isNotEmpty) await _send(text: text.trim(), editId: m.id);
  }

  Future<void> _rate(ChatMessage m, int rating) async {
    if (m.id == null) return;
    try {
      await _api?.rate(m.id!, rating);
      setState(() => m.rating = rating);
    } on HostException catch (e) {
      _snack(e.message);
    }
  }

  Future<void> _toggleFavorite() async {
    final id = _conversationId;
    if (id == null) return;
    final current = _conversations.where((c) => c.id == id).firstOrNull;
    try {
      await _api?.updateConversation(id, {'favorite': !(current?.favorite ?? false)});
      await _loadConversations();
    } on HostException catch (e) {
      _snack(e.message);
    }
  }

  Future<void> _rename() async {
    final id = _conversationId;
    if (id == null) return;
    final controller = TextEditingController(text: _title);
    final title = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Название чата'),
        content: TextField(controller: controller, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(context, controller.text), child: const Text('Сохранить')),
        ],
      ),
    );
    controller.dispose();
    if (title == null || title.trim().isEmpty) return;
    try {
      await _api?.updateConversation(id, {'title': title.trim()});
      setState(() => _title = title.trim());
      await _loadConversations();
    } on HostException catch (e) {
      _snack(e.message);
    }
  }

  Future<void> _delete() async {
    final id = _conversationId;
    if (id == null) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Удалить чат?'),
        content: const Text('Чат исчезнет из списка на всех устройствах.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Удалить')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await _api?.deleteConversation(id);
      _newChat();
      await _loadConversations();
    } on HostException catch (e) {
      _snack(e.message);
    }
  }

  Future<void> _sync() async {
    setState(() {
      _pulse = PulseState.syncing;
      _statusText = 'Синхронизация…';
    });
    try {
      final r = await _api?.sync();
      _snack('Синхронизировано · получено: ${r?['received'] ?? 0}');
      await _loadConversations();
      if (_conversationId != null) {
        final c = _conversations.where((x) => x.id == _conversationId).firstOrNull;
        if (c != null) await _open(c);
      }
    } on HostException catch (e) {
      _snack(e.message);
    } finally {
      await _refreshStatus();
    }
  }

  Future<void> _signOut() async {
    await _stream?.cancel();
    await _store?.clear(); // no chat copies remain on the phone after sign-out
    await Supabase.instance.client.auth.signOut();
    widget.onSignedOut();
  }

  Widget _sidebar(BuildContext context, {required bool inDrawer}) {
    final favs = _conversations.where((c) => c.favorite).toList();
    final recent = _conversations.where((c) => !c.favorite).toList();
    final email = Supabase.instance.client.auth.currentUser?.email ?? '';
    void close() {
      if (inDrawer) Navigator.of(context).maybePop();
    }

    Widget tile(Conversation c) => ListTile(
          dense: true,
          selected: c.id == _conversationId,
          selectedTileColor: const Color(0xFF2A2160),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          title: Text(c.title, maxLines: 1, overflow: TextOverflow.ellipsis),
          leading: c.favorite ? const Icon(Icons.star_rounded, size: 18, color: Color(0xFFFFD166)) : null,
          onTap: () {
            close();
            _open(c);
          },
        );

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: Image.asset('chelsy_logo.png', width: 40, height: 40, cacheWidth: 120)),
            const SizedBox(width: 10),
            const Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Chelsy AI', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              Text('Android 0.9.3 · beta', style: TextStyle(fontSize: 11, color: ChelsyColors.muted)),
            ]),
          ]),
          const SizedBox(height: 14),
          FilledButton.icon(
            onPressed: () {
              close();
              _newChat();
            },
            icon: const Icon(Icons.add),
            label: const Text('Новый чат'),
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(46)),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _search,
            decoration: const InputDecoration(
                hintText: 'Поиск по истории', prefixIcon: Icon(Icons.search), isDense: true),
            onChanged: (_) {
              _searchDebounce?.cancel();
              _searchDebounce = Timer(const Duration(milliseconds: 350), _loadConversations);
            },
          ),
          const SizedBox(height: 6),
          Expanded(
            child: ListView(children: [
              if (favs.isNotEmpty) const _Section('Избранное'),
              ...favs.map(tile),
              const _Section('Недавние'),
              if (recent.isEmpty) const Padding(
                  padding: EdgeInsets.all(8),
                  child: Text('Пока пусто', style: TextStyle(color: ChelsyColors.muted))),
              ...recent.map(tile),
            ]),
          ),
          const Divider(color: ChelsyColors.stroke),
          ListTile(
            dense: true,
            leading: const Icon(Icons.travel_explore),
            title: const Text('Исследования'),
            onTap: () {
              close();
              if (_api != null) {
                Navigator.of(context).push(MaterialPageRoute(builder: (_) => ResearchScreen(api: _api!)));
              }
            },
          ),
          ListTile(
            dense: true,
            leading: const Icon(Icons.cloud_sync_outlined),
            title: const Text('Синхронизация'),
            onTap: () {
              close();
              _sync();
            },
          ),
          ListTile(
            dense: true,
            leading: CircleAvatar(
              radius: 14,
              backgroundColor: ChelsyColors.violet,
              child: Text(email.isEmpty ? '?' : email[0].toUpperCase(), style: const TextStyle(fontSize: 13)),
            ),
            title: Text(email, maxLines: 1, overflow: TextOverflow.ellipsis),
            trailing: IconButton(tooltip: 'Выйти', icon: const Icon(Icons.logout), onPressed: _signOut),
          ),
        ]),
      ),
    );
  }

  Widget _composer() {
    final disabled = _offline || _readOnlyCloud;
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 4, 10, 8),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            decoration: BoxDecoration(
              color: ChelsyColors.panel2,
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: const Color(0xFF3A3474)),
            ),
            padding: const EdgeInsets.only(left: 14, right: 6),
            child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Expanded(
                child: TextField(
                  controller: _input,
                  enabled: !disabled,
                  minLines: 1,
                  maxLines: 6,
                  textInputAction: TextInputAction.newline,
                  decoration: InputDecoration(
                    hintText: disabled ? 'Хост недоступен' : 'Спроси Chelsy…',
                    border: InputBorder.none,
                    enabledBorder: InputBorder.none,
                    focusedBorder: InputBorder.none,
                    filled: false,
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: _generating
                    ? IconButton.filledTonal(
                        tooltip: 'Остановить', onPressed: _stop, icon: const Icon(Icons.stop_rounded))
                    : IconButton.filled(
                        tooltip: 'Отправить', onPressed: disabled ? null : () => _send(), icon: const Icon(Icons.arrow_upward_rounded)),
              ),
            ]),
          ),
          const SizedBox(height: 6),
          Row(children: [
            if (_models.isNotEmpty)
              Flexible(
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    value: _model,
                    isDense: true,
                    isExpanded: true,
                    style: const TextStyle(fontSize: 13, color: ChelsyColors.muted),
                    items: _models.map((m) => DropdownMenuItem(value: m, child: Text(m))).toList(),
                    onChanged: _generating ? null : (v) => setState(() => _model = v),
                  ),
                ),
              ),
            const SizedBox(width: 8),
            FilterChip(
              label: const Text('Интернет', style: TextStyle(fontSize: 12)),
              avatar: const Icon(Icons.public, size: 16),
              selected: _web,
              onSelected: (v) async {
                setState(() => _web = v);
                (await SharedPreferences.getInstance()).setBool('chelsy_web', v);
              },
            ),
          ]),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final lastAssistant = _messages.lastIndexWhere((m) => !m.fromUser);
    final chat = Column(children: [
      if (_offline || _readOnlyCloud)
        MaterialBanner(
          backgroundColor: const Color(0xFF2A1830),
          content: Text(_readOnlyCloud ? _readOnlyLabel : 'Нет связи с Chelsy Host: $_statusText'),
          actions: [
            TextButton(
                onPressed: () async {
                  await _refreshStatus();
                  if (!_offline) await _loadConversations(openLatest: _readOnlyCloud);
                  if (!_offline) setState(() => _readOnlyCloud = false);
                },
                child: const Text('Повторить')),
          ],
        ),
      Expanded(
        child: _messages.isEmpty
            ? Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    ClipRRect(
                        borderRadius: BorderRadius.circular(22),
                        child: Image.asset('chelsy_logo.png', width: 72, height: 72, cacheWidth: 216)),
                    const SizedBox(height: 14),
                    const Text('Привет! Я Челси 💜', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    const Text('Спроси что угодно, попроси объяснить, помочь с кодом или исследовать тему.',
                        textAlign: TextAlign.center, style: TextStyle(color: ChelsyColors.muted)),
                  ]),
                ),
              )
            : ListView.builder(
                controller: _scroll,
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                itemCount: _messages.length,
                itemBuilder: (context, i) {
                  final m = _messages[i];
                  return MessageView(
                    message: m,
                    onEdit: m.fromUser && !_readOnlyCloud ? () => _editMessage(m) : null,
                    onRegenerate: !m.fromUser && i == lastAssistant && !_generating && !_readOnlyCloud
                        ? () => _send(regenerate: true)
                        : null,
                    onRate: _readOnlyCloud ? null : (r) => _rate(m, r),
                  );
                },
              ),
      ),
      _composer(),
    ]);
    return Scaffold(
      drawer: wide ? null : Drawer(child: _sidebar(context, inDrawer: true)),
      appBar: AppBar(
        title: Text(_title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 17)),
        actions: [
          Tooltip(
            message: _statusText,
            child: Padding(padding: const EdgeInsets.symmetric(horizontal: 8), child: ChelsyPulse(state: _pulse)),
          ),
          IconButton(
            tooltip: 'В избранное',
            onPressed: _conversationId == null ? null : _toggleFavorite,
            icon: Icon(_conversations.any((c) => c.id == _conversationId && c.favorite)
                ? Icons.star_rounded
                : Icons.star_border_rounded),
          ),
          PopupMenuButton<String>(
            onSelected: (v) => switch (v) {
              'rename' => _rename(),
              'delete' => _delete(),
              'sync' => _sync(),
              _ => _signOut(),
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'rename', child: Text('Переименовать')),
              PopupMenuItem(value: 'delete', child: Text('Удалить чат')),
              PopupMenuItem(value: 'sync', child: Text('Синхронизировать')),
              PopupMenuItem(value: 'logout', child: Text('Выйти')),
            ],
          ),
        ],
      ),
      body: wide
          ? Row(children: [
              SizedBox(width: 300, child: Material(color: ChelsyColors.panel, child: _sidebar(context, inDrawer: false))),
              Expanded(child: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 900), child: chat))),
            ])
          : chat,
    );
  }
}

class _Section extends StatelessWidget {
  const _Section(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(8, 12, 8, 4),
        child: Text(text.toUpperCase(),
            style: const TextStyle(fontSize: 11, letterSpacing: 1, fontWeight: FontWeight.w700, color: ChelsyColors.muted)),
      );
}
