import 'package:material_ui/material_ui.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'src/chat_screen.dart';
import 'src/login_screen.dart';
import 'src/secure_storage.dart';
import 'src/theme.dart';

/// Public project settings are injected at build time with --dart-define.
/// Only the Supabase URL and the PUBLISHABLE key are allowed in the APK.
const supabaseUrl = String.fromEnvironment('SUPABASE_URL');
const supabaseKey = String.fromEnvironment('SUPABASE_KEY');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final configured = supabaseUrl.startsWith('https://') && supabaseKey.isNotEmpty && !supabaseKey.startsWith('sb_secret_');
  if (configured) {
    await Supabase.initialize(
      url: supabaseUrl,
      publishableKey: supabaseKey,
      authOptions: FlutterAuthClientOptions(localStorage: SecureSessionStorage()),
    );
  }
  runApp(ChelsyApp(configured: configured));
}

class ChelsyApp extends StatelessWidget {
  const ChelsyApp({super.key, required this.configured});
  final bool configured;

  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        title: 'Chelsy AI',
        theme: chelsyTheme(),
        home: configured
            ? const AuthGate()
            : const Scaffold(
                body: Center(
                  child: Padding(
                    padding: EdgeInsets.all(24),
                    child: Text('Supabase не настроен. Собери APK с параметрами SUPABASE_URL и SUPABASE_KEY.',
                        textAlign: TextAlign.center),
                  ),
                ),
              ),
      );
}

class AuthGate extends StatefulWidget {
  const AuthGate({super.key});
  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  bool get _signedIn => Supabase.instance.client.auth.currentSession != null;

  @override
  Widget build(BuildContext context) => _signedIn
      ? ChatScreen(onSignedOut: () => setState(() {}))
      : LoginScreen(onSignedIn: () => setState(() {}));
}
