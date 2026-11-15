import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart';

import 'logic.dart';

class HostException implements Exception {
  HostException(this.message, {this.status});
  final String message;
  final int? status;
  bool get offline => status == null;
  @override
  String toString() => message;
}

String newRequestId() {
  final r = Random.secure();
  final b = List<int>.generate(16, (_) => r.nextInt(256));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  final h = b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
  return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
}

/// Talks to Chelsy Host API v2. The device never talks to Ollama directly.
class HostApi {
  HostApi(String host, {http.Client? client})
      : base = validateHostUrl(host),
        _client = client ?? http.Client();

  final String base;
  final http.Client _client;

  Future<String> _token() async {
    final auth = Supabase.instance.client.auth;
    var session = auth.currentSession;
    if (session == null) throw HostException('Сначала войди в аккаунт', status: 401);
    if (session.isExpired) {
      session = (await auth.refreshSession()).session;
    }
    if (session == null) throw HostException('Сессия истекла — войди снова', status: 401);
    return session.accessToken;
  }

  Future<Map<String, dynamic>> _call(String method, String path, [Map<String, dynamic>? body]) async {
    final headers = {'Authorization': 'Bearer ${await _token()}', 'Accept': 'application/json'};
    if (body != null) headers['Content-Type'] = 'application/json';
    final request = http.Request(method, Uri.parse('$base$path'))..headers.addAll(headers);
    if (body != null) request.body = jsonEncode(body);
    http.StreamedResponse response;
    try {
      response = await _client.send(request).timeout(const Duration(seconds: 30));
    } on SocketException {
      throw HostException('Хост недоступен. Проверь, что ноутбук включён и Tailscale подключён.');
    } on TimeoutException {
      throw HostException('Хост не отвечает. Попробуй позже.');
    } on http.ClientException {
      throw HostException('Нет соединения с хостом.');
    }
    final text = await response.stream.bytesToString();
    Map<String, dynamic> data = {};
    if (text.isNotEmpty) {
      final decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) data = decoded;
    }
    if (response.statusCode >= 400) {
      throw HostException((data['message'] ?? data['error'] ?? 'Ошибка ${response.statusCode}').toString(),
          status: response.statusCode);
    }
    return data;
  }

  Future<Map<String, dynamic>> status() => _call('GET', '/api/v2/status');

  Future<List<Conversation>> conversations({String query = ''}) async {
    final q = query.isEmpty ? '' : '?q=${Uri.encodeQueryComponent(query)}';
    final data = await _call('GET', '/api/v2/conversations$q');
    return (data['conversations'] as List? ?? [])
        .whereType<Map<String, dynamic>>()
        .map(Conversation.fromJson)
        .toList();
  }

  Future<List<ChatMessage>> messages(String id) async {
    final data = await _call('GET', '/api/v2/conversations/$id/messages');
    return (data['messages'] as List? ?? []).whereType<Map<String, dynamic>>().map(ChatMessage.fromJson).toList();
  }

  Future<void> updateConversation(String id, Map<String, dynamic> fields) =>
      _call('POST', '/api/v2/conversations/$id', fields);

  Future<void> deleteConversation(String id) => _call('DELETE', '/api/v2/conversations/$id');

  Future<void> rate(int messageId, int rating) => _call('POST', '/api/v2/messages/$messageId/rating', {'rating': rating});

  Future<void> stop(String requestId) => _call('POST', '/api/v2/chat/stop', {'request_id': requestId});

  /// Autonomous learning journal (what Chelsy studied on her own).
  Future<List<Map<String, dynamic>>> learningJournal() async =>
      ((await _call('GET', '/api/v2/learning?limit=30'))['journal'] as List? ?? [])
          .whereType<Map<String, dynamic>>()
          .toList();

  Future<Map<String, dynamic>> sync() => _call('GET', '/api/v2/sync');

  Future<List<Map<String, dynamic>>> researchJobs() async =>
      ((await _call('GET', '/api/v2/research'))['jobs'] as List? ?? []).whereType<Map<String, dynamic>>().toList();

  Future<String> startResearch(String topic, {int maxPages = 8}) async =>
      (await _call('POST', '/api/v2/research', {'topic': topic, 'max_pages': maxPages}))['id'] as String;

  Future<Map<String, dynamic>> researchJob(String id) => _call('GET', '/api/v2/research/$id');

  Future<void> stopResearch(String id) => _call('POST', '/api/v2/research/$id/stop', {'stop': true});

  Future<void> reviewItem(int id, String action) =>
      _call('POST', '/api/v2/research/items/$id', {'action': action, 'scope': 'personal'});

  /// Streams NDJSON events: start, status, sources, delta, replace, done, error.
  Stream<Map<String, dynamic>> chat({
    required String message,
    String? conversationId,
    String? model,
    bool web = true,
    bool regenerate = false,
    int? editMessageId,
    required String requestId,
  }) async* {
    final body = <String, dynamic>{
      'message': message,
      'conversation_id': conversationId,
      'model': model,
      'web': web,
      'stream': true,
      'request_id': requestId,
    };
    if (regenerate) body['regenerate'] = true;
    if (editMessageId != null) body['edit_message_id'] = editMessageId;
    final request = http.Request('POST', Uri.parse('$base/api/v2/chat'))
      ..headers.addAll({
        'Authorization': 'Bearer ${await _token()}',
        'Accept': 'application/x-ndjson',
        'Content-Type': 'application/json',
      })
      ..body = jsonEncode(body);
    http.StreamedResponse response;
    try {
      response = await _client.send(request).timeout(const Duration(seconds: 45));
    } on SocketException {
      throw HostException('Хост недоступен. Проверь Tailscale и что ноутбук включён.');
    } on TimeoutException {
      throw HostException('Хост не отвечает.');
    } on http.ClientException {
      throw HostException('Нет соединения с хостом.');
    }
    if (response.statusCode >= 400) {
      final text = await response.stream.bytesToString();
      String message = 'Ошибка ${response.statusCode}';
      try {
        final data = jsonDecode(text);
        if (data is Map) message = (data['message'] ?? data['error'] ?? message).toString();
      } on FormatException {
        // keep generic message
      }
      throw HostException(message, status: response.statusCode);
    }
    yield* decodeNdjson(response.stream.timeout(const Duration(minutes: 5)));
  }
}
