import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import 'logic.dart';

/// Local data storage on the phone: an offline copy of the user's own
/// conversations, so history stays readable without the host or internet.
///
/// Files live in the app-private support directory (other apps cannot read
/// it; Android backup and device transfer are disabled in the manifest).
/// One folder per account; deleted on sign-out. No passwords or tokens.
class LocalStore {
  LocalStore(this.root);

  final Directory root;
  static const maxMessages = 300;
  static const maxConversations = 200;
  static final _safeId = RegExp(r'^[0-9a-fA-F-]{36}$');

  static Future<LocalStore> forUser(String userId) async {
    if (!_safeId.hasMatch(userId)) throw ArgumentError('invalid user id');
    final base = await getApplicationSupportDirectory();
    return LocalStore(Directory('${base.path}/chelsy_cache/$userId'));
  }

  File _file(String name) => File('${root.path}/$name');

  Future<void> _write(String name, Object value) async {
    await root.create(recursive: true);
    final tmp = _file('$name.tmp');
    await tmp.writeAsString(jsonEncode(value), flush: true);
    await tmp.rename(_file(name).path); // atomic replace: no half-written cache
  }

  Future<dynamic> _read(String name) async {
    final f = _file(name);
    if (!await f.exists()) return null;
    try {
      return jsonDecode(await f.readAsString());
    } on FormatException {
      return null;
    }
  }

  Future<void> saveConversations(List<Conversation> items) =>
      _write('conversations.json', items.take(maxConversations).map((c) => c.toJson()).toList());

  Future<List<Conversation>> loadConversations() async {
    final data = await _read('conversations.json');
    if (data is! List) return [];
    return data.whereType<Map<String, dynamic>>().map(Conversation.fromJson).toList();
  }

  Future<void> saveMessages(String conversationId, List<ChatMessage> messages) async {
    if (!_safeId.hasMatch(conversationId)) return;
    final tail = messages.where((m) => !m.pending).toList();
    final start = tail.length > maxMessages ? tail.length - maxMessages : 0;
    await _write('c_$conversationId.json', tail.sublist(start).map((m) => m.toJson()).toList());
  }

  Future<List<ChatMessage>> loadMessages(String conversationId) async {
    if (!_safeId.hasMatch(conversationId)) return [];
    final data = await _read('c_$conversationId.json');
    if (data is! List) return [];
    return data.whereType<Map<String, dynamic>>().map(ChatMessage.fromJson).toList();
  }

  Future<void> clear() async {
    if (await root.exists()) await root.delete(recursive: true);
  }
}
