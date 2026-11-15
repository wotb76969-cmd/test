import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'logic.dart';
import 'theme.dart';

/// Sign-in with the Chelsy Group account (Supabase Auth). The password goes
/// only to Supabase over HTTPS; it is never stored on the device.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onSignedIn});
  final VoidCallback onSignedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _host = TextEditingController();
  bool _busy = false;
  bool _hide = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((p) {
      if (mounted) setState(() => _host.text = p.getString('chelsy_host') ?? '');
    });
  }

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _host.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    String host;
    try {
      host = validateHostUrl(_host.text);
    } on FormatException catch (e) {
      setState(() => _error = e.message);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await Supabase.instance.client.auth.signInWithPassword(email: _email.text.trim(), password: _password.text);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('chelsy_host', host);
      _password.clear();
      widget.onSignedIn();
    } on AuthException catch (e) {
      setState(() => _error = e.message.toLowerCase().contains('invalid')
          ? 'Неверный email или пароль.'
          : e.message.toLowerCase().contains('confirm')
              ? 'Сначала подтверди email по письму.'
              : 'Не удалось войти: ${e.message}');
    } catch (_) {
      setState(() => _error = 'Нет соединения с сервером аккаунтов. Проверь интернет.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: AutofillGroup(
                child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Center(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(24),
                      child: Image.asset('chelsy_logo.png', width: 88, height: 88, cacheWidth: 264),
                    ),
                  ),
                  const SizedBox(height: 14),
                  const Text('Chelsy AI', textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 4),
                  const Text('Войди аккаунтом Chelsy Group', textAlign: TextAlign.center,
                      style: TextStyle(color: ChelsyColors.muted)),
                  const SizedBox(height: 26),
                  TextField(
                    controller: _host,
                    enabled: !_busy,
                    keyboardType: TextInputType.url,
                    autocorrect: false,
                    decoration: const InputDecoration(
                      labelText: 'Адрес Chelsy Host',
                      hintText: 'https://laptop.tailnet.ts.net',
                      prefixIcon: Icon(Icons.dns_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _email,
                    enabled: !_busy,
                    keyboardType: TextInputType.emailAddress,
                    autofillHints: const [AutofillHints.email],
                    autocorrect: false,
                    decoration: const InputDecoration(labelText: 'Email', prefixIcon: Icon(Icons.alternate_email)),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _password,
                    enabled: !_busy,
                    obscureText: _hide,
                    autofillHints: const [AutofillHints.password],
                    onSubmitted: (_) => _submit(),
                    decoration: InputDecoration(
                      labelText: 'Пароль',
                      prefixIcon: const Icon(Icons.lock_outline),
                      suffixIcon: IconButton(
                        tooltip: _hide ? 'Показать пароль' : 'Скрыть пароль',
                        icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                        onPressed: () => setState(() => _hide = !_hide),
                      ),
                    ),
                  ),
                  const SizedBox(height: 20),
                  FilledButton(
                    onPressed: _busy ? null : _submit,
                    style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(50)),
                    child: _busy
                        ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Text('Войти', style: TextStyle(fontSize: 16)),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 14),
                    Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: ChelsyColors.danger)),
                  ],
                  const SizedBox(height: 20),
                  const Text(
                    'Ноутбук с Chelsy Host доступен через Tailscale. Пароль передаётся только в Supabase Auth и не сохраняется на телефоне.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: ChelsyColors.muted, fontSize: 12),
                  ),
                ]),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
