import 'dart:convert';

/// Pure logic (no Flutter imports) so it can be unit-tested on the Dart VM.

/// Validates the Chelsy Host address. Only HTTPS (Tailscale Serve) without
/// path, query, credentials or custom port is accepted; plain HTTP is refused.
String validateHostUrl(String raw) {
  final value = raw.trim().replaceFirst(RegExp(r'/+$'), '');
  final uri = Uri.tryParse(value);
  if (uri == null ||
      uri.scheme != 'https' ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      (uri.path.isNotEmpty && uri.path != '/') ||
      uri.hasQuery ||
      uri.hasFragment ||
      (uri.hasPort && uri.port != 443)) {
    throw const FormatException(
        'Укажи HTTPS-адрес Tailscale Serve, например https://laptop.tailnet.ts.net — без пути, порта и параметров.');
  }
  return 'https://${uri.host}';
}

/// Splits a byte stream of newline-delimited JSON into decoded objects.
Stream<Map<String, dynamic>> decodeNdjson(Stream<List<int>> bytes) async* {
  final lines = bytes.transform(utf8.decoder).transform(const LineSplitter());
  await for (final line in lines) {
    final trimmed = line.trim();
    if (trimmed.isEmpty) continue;
    try {
      final value = jsonDecode(trimmed);
      if (value is Map<String, dynamic>) yield value;
    } on FormatException {
      continue;
    }
  }
}

class Conversation {
  Conversation({required this.id, required this.title, required this.updated, required this.favorite});
  final String id;
  final String title;
  final String updated;
  final bool favorite;

  factory Conversation.fromJson(Map<String, dynamic> json) => Conversation(
        id: json['id'] as String,
        title: (json['title'] as String?)?.trim().isNotEmpty == true ? json['title'] as String : 'Без названия',
        updated: (json['updated'] ?? '') as String,
        favorite: json['favorite'] == true,
      );
}

class ChatMessage {
  ChatMessage({this.id, required this.role, required this.content, this.rating = 0, this.pending = false});
  final int? id;
  final String role;
  String content;
  int rating;
  bool pending;

  bool get fromUser => role == 'user';

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
        id: json['id'] is int ? json['id'] as int : null,
        role: json['role'] == 'user' ? 'user' : 'assistant',
        content: (json['content'] ?? '') as String,
        rating: json['rating'] is int ? json['rating'] as int : 0,
      );
}

/// Pulse states shown by the Chelsy Pulse indicator.
enum PulseState { ready, generating, searching, syncing, offline }

PulseState pulseFromHost(String? state) {
  switch (state) {
    case 'searching':
      return PulseState.searching;
    case 'syncing':
      return PulseState.syncing;
    case 'offline':
      return PulseState.offline;
    case 'ready':
      return PulseState.ready;
    default:
      return PulseState.generating;
  }
}

/// Only https links from answers may be opened in the browser.
bool isSafeLink(String? href) {
  final uri = Uri.tryParse(href ?? '');
  return uri != null && uri.scheme == 'https' && uri.host.isNotEmpty;
}
