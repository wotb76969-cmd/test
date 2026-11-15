import 'dart:async';
import 'dart:convert';

import 'package:chelsy_ai_client/src/logic.dart';
import 'package:chelsy_ai_client/src/message_view.dart';
import 'package:chelsy_ai_client/src/pulse.dart';
import 'package:chelsy_ai_client/src/theme.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';

void main() {
  group('Host URL validation', () {
    test('accepts Tailscale HTTPS origin', () {
      expect(validateHostUrl(' https://laptop.tailnet.ts.net/ '), 'https://laptop.tailnet.ts.net');
    });
    test('rejects insecure or ambiguous addresses', () {
      for (final bad in [
        'http://laptop.tailnet.ts.net',
        'https://user:pass@laptop.ts.net',
        'https://laptop.ts.net/api',
        'https://laptop.ts.net:8787',
        'https://laptop.ts.net/?x=1',
        'ftp://laptop.ts.net',
        '',
      ]) {
        expect(() => validateHostUrl(bad), throwsFormatException, reason: bad);
      }
    });
  });

  test('NDJSON stream decoding survives chunk boundaries and junk', () async {
    final payload = '${jsonEncode({'type': 'start'})}\n{"type":"del'
        'ta","text":"При"}\n\nnot-json\n${jsonEncode({'type': 'done', 'text': 'Привет'})}\n';
    final bytes = utf8.encode(payload);
    final chunks = Stream<List<int>>.fromIterable([bytes.sublist(0, 7), bytes.sublist(7, 31), bytes.sublist(31)]);
    final events = await decodeNdjson(chunks).toList();
    expect(events.map((e) => e['type']), ['start', 'delta', 'done']);
    expect(events.last['text'], 'Привет');
  });

  test('only https links are opened', () {
    expect(isSafeLink('https://docs.python.org'), isTrue);
    expect(isSafeLink('http://example.org'), isFalse);
    expect(isSafeLink('javascript:alert(1)'), isFalse);
    expect(isSafeLink('intent://evil'), isFalse);
    expect(isSafeLink(null), isFalse);
  });

  test('pulse state mapping', () {
    expect(pulseFromHost('searching'), PulseState.searching);
    expect(pulseFromHost('checking'), PulseState.generating);
    expect(pulseFromHost('offline'), PulseState.offline);
  });

  testWidgets('message views render Markdown answer and user bubble', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: chelsyTheme(),
      home: Scaffold(
        body: ListView(children: [
          MessageView(message: ChatMessage(role: 'user', content: 'Почему небо голубое?')),
          MessageView(
            message: ChatMessage(id: 2, role: 'assistant', content: 'Дело в **рассеянии** света.\n\n```python\nprint(1)\n```'),
            onRegenerate: () {},
            onRate: (_) {},
          ),
        ]),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Почему небо голубое?'), findsOneWidget);
    expect(find.byTooltip('Повторить'), findsOneWidget);
    expect(find.byTooltip('Копировать'), findsOneWidget);
  });

  testWidgets('pulse respects reduced motion and settles', (tester) async {
    await tester.pumpWidget(const MediaQuery(
      data: MediaQueryData(disableAnimations: true),
      child: Directionality(textDirection: TextDirection.ltr, child: ChelsyPulse(state: PulseState.generating)),
    ));
    await tester.pumpAndSettle(const Duration(milliseconds: 100), EnginePhase.sendSemanticsUpdate, const Duration(seconds: 2));
    expect(find.byType(ChelsyPulse), findsOneWidget);
  });
}
