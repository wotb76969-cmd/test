import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

const supabaseUrl = String.fromEnvironment('SUPABASE_URL');
const supabaseKey = String.fromEnvironment('SUPABASE_KEY');
const violet = Color(0xFFA78BFA);
const background = Color(0xFF0F1124);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (supabaseUrl.isNotEmpty && supabaseKey.isNotEmpty) {
    await Supabase.initialize(url: supabaseUrl, publishableKey: supabaseKey);
  }
  runApp(const ChelsyApp());
}

class ChelsyApp extends StatelessWidget {
  const ChelsyApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'Chelsy AI',
    theme: ThemeData.dark(useMaterial3: true).copyWith(
      scaffoldBackgroundColor: background,
      colorScheme: ColorScheme.fromSeed(seedColor: violet, brightness: Brightness.dark),
      appBarTheme: const AppBarTheme(backgroundColor: background),
    ),
    home: (supabaseUrl.isEmpty || supabaseKey.isEmpty)
      ? const Scaffold(body: Center(child: Padding(padding: EdgeInsets.all(24),
          child: Text('Supabase не настроен. Собери APK с параметрами SUPABASE_URL и SUPABASE_KEY.'))))
      : const ChelsyHome(),
  );
}

class ChelsyHome extends StatefulWidget {
  const ChelsyHome({super.key});
  @override
  State<ChelsyHome> createState() => _ChelsyHomeState();
}

class _ChelsyHomeState extends State<ChelsyHome> {
  final email = TextEditingController();
  final password = TextEditingController();
  final host = TextEditingController();
  final prompt = TextEditingController();
  final scroll = ScrollController();
  final List<Map<String, dynamic>> messages = [];
  bool busy = false;
  String status = 'Введите адрес вашего Chelsy Host';
  String model = 'llama3.2:1b';
  bool get signedIn => Supabase.instance.client.auth.currentSession != null;

  @override
  void initState() { super.initState(); _loadHost(); }
  @override
  void dispose() {
    email.dispose(); password.dispose(); host.dispose(); prompt.dispose(); scroll.dispose();
    super.dispose();
  }

  Future<void> _loadHost() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() => host.text = prefs.getString('chelsy_host') ?? '');
    if (signedIn && host.text.isNotEmpty) _loadHistory();
  }

  String _hostUrl() {
    final raw = host.text.trim().replaceFirst(RegExp(r'/$'), '');
    final uri = Uri.tryParse(raw);
    if (uri == null || uri.scheme != 'https' || uri.host.isEmpty ||
        uri.userInfo.isNotEmpty || uri.path.isNotEmpty || uri.query.isNotEmpty ||
        uri.fragment.isNotEmpty || uri.port != 443) {
      throw Exception('Укажи HTTPS-адрес Tailscale Serve, без пути, порта и параметров.');
    }
    return raw;
  }

  Future<Map<String, dynamic>> _api(String path, {Map<String, dynamic>? body}) async {
    final base = _hostUrl();
    var session = Supabase.instance.client.auth.currentSession;
    if (session == null) throw Exception('Сначала войди через Supabase Auth');
    // Supabase auto-refreshes the session; refresh explicitly if near expiration.
    if (session.isExpired) {
      final refreshed = await Supabase.instance.client.auth.refreshSession();
      session = refreshed.session;
    }
    if (session == null) throw Exception('Сессия закончилась, войди снова');
    final uri = Uri.parse('$base$path');
    final headers = {'Authorization':'Bearer ${session.accessToken}','Accept':'application/json'};
    if (body != null) headers['Content-Type'] = 'application/json';
    final response = await (body == null
      ? http.get(uri,headers:headers)
      : http.post(uri,headers:headers,body:jsonEncode(body)))
        .timeout(const Duration(seconds: 195));
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map<String, dynamic>) throw Exception('Некорректный ответ сервера');
    if (response.statusCode >= 400) {
      throw Exception(decoded['error'] ?? 'HTTP ${response.statusCode}');
    }
    return decoded;
  }

  Future<void> _work(Future<void> Function() action) async {
    if (busy) return;
    setState(() => busy = true);
    try { await action(); }
    catch (error) { if (mounted) setState(() => status = 'Ошибка: $error'); }
    finally { if (mounted) setState(() => busy = false); }
  }

  Future<void> _login() => _work(() async {
    await Supabase.instance.client.auth.signInWithPassword(
      email:email.text.trim(),password:password.text);
    password.clear();
    if (!mounted) return;
    setState(() => status = 'Авторизация успешна');
    await _fetchHistory();
  });

  Future<void> _fetchHistory() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('chelsy_host',_hostUrl());
    Map<String, dynamic> result;
    try {
      result=await _api('/api/history');
    } catch (_) {
      // Read-only cloud history still works when the laptop server is offline.
      final user=Supabase.instance.client.auth.currentUser;
      if (user==null) rethrow;
      final rows=await Supabase.instance.client.from('chelsy_ai_messages')
          .select('role,content').eq('user_id',user.id)
          .order('created_at',ascending:true).limit(500);
      result={'messages':rows,'cloud':'offline-history'};
    }
    if (!mounted) return;
    setState(() {
      messages.clear();
      for (final item in result['messages'] ?? []) {
        if (item is Map) messages.add(Map<String,dynamic>.from(item));
      }
      status = result['cloud']=='offline-history' ? '☁ История из Supabase · Host недоступен' : 'Подключено к Chelsy Host · ${messages.length} сообщений';
    });
    _scrollDown();
  }
  Future<void> _loadHistory() => _work(_fetchHistory);

  Future<void> _sync() => _work(() async {
    final report=await _api('/api/sync');
    await _fetchHistory();
    if (mounted) setState(() => status = '☁ Синхронизировано · получено: ${report['received']}');
  });

  Future<void> _send() => _work(() async {
    final text=prompt.text.trim();
    if (text.isEmpty) return;
    prompt.clear();
    final result=await _api('/api/chat',body:{'message':text,'model':model});
    if (!mounted) return;
    setState(() {
      messages.clear();
      for (final item in result['messages'] ?? []) {
        if (item is Map) messages.add(Map<String,dynamic>.from(item));
      }
      status='Ответ получен · облако: ${result['cloud'] ?? 'локально'}';
    });
    _scrollDown();
  });

  void _scrollDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (scroll.hasClients) scroll.animateTo(scroll.position.maxScrollExtent,
          duration:const Duration(milliseconds:250),curve:Curves.easeOut);
    });
  }

  @override
  Widget build(BuildContext context) {
    final account = signedIn;
    return Scaffold(
      appBar:AppBar(title:Row(children:[Image.asset('chelsy_logo.png',width:32,height:32),const SizedBox(width:8),const Text('Chelsy AI')]),actions:[
        if (account) IconButton(onPressed:busy?null:_sync,icon:const Icon(Icons.cloud_sync_outlined),tooltip:'Синхронизация'),
        if (account) IconButton(onPressed:busy?null:() async {
          await Supabase.instance.client.auth.signOut();
          if (mounted) setState(() {messages.clear();status='Вы вышли из аккаунта';});
        },icon:const Icon(Icons.logout),tooltip:'Выход'),
      ]),
      body:SafeArea(child:Column(children:[
        Padding(padding:const EdgeInsets.fromLTRB(12,8,12,4),child:TextField(
          controller:host,enabled:!busy,
          decoration:const InputDecoration(labelText:'Адрес Host · https://...ts.net',
            prefixIcon:Icon(Icons.dns_outlined),border:OutlineInputBorder()),
        )),
        if (!account) ...[
          Padding(padding:const EdgeInsets.all(12),child:Column(children:[
            TextField(controller:email,keyboardType:TextInputType.emailAddress,
              decoration:const InputDecoration(labelText:'Email Chelsy Group')),
            TextField(controller:password,obscureText:true,
              decoration:const InputDecoration(labelText:'Пароль')),
            const SizedBox(height:12),
            FilledButton(onPressed:busy?null:_login,child:const Text('Войти через Supabase')),
          ])),
        ] else ...[
          Padding(padding:const EdgeInsets.symmetric(horizontal:12),child:Row(children:[
            DropdownButton<String>(value:model,items:const [
              DropdownMenuItem(value:'llama3.2:1b',child:Text('Llama 3.2 · 1B')),
              DropdownMenuItem(value:'qwen3:4b',child:Text('Qwen3 · 4B')),
            ],onChanged:busy?null:(v)=>setState(()=>model=v!)),
            const Spacer(),
            TextButton.icon(onPressed:busy?null:_loadHistory,icon:const Icon(Icons.refresh),label:const Text('История')),
          ])),
          Expanded(child:ListView.builder(controller:scroll,padding:const EdgeInsets.all(12),
            itemCount:messages.length,itemBuilder:(context,i){
              final m=messages[i];final fromUser=m['role']=='user';
              return Align(alignment:fromUser?Alignment.centerRight:Alignment.centerLeft,
                child:ConstrainedBox(constraints:BoxConstraints(maxWidth:MediaQuery.sizeOf(context).width*0.88),
                  child:Card(color:fromUser?const Color(0xFF473A6C):const Color(0xFF20243C),
                    child:Padding(padding:const EdgeInsets.all(12),child:Text('${fromUser?'Вы':'Chelsy AI'}\n${m['content']??''}')))));
            })),
          Padding(padding:const EdgeInsets.fromLTRB(12,4,12,8),child:Row(children:[
            Expanded(child:TextField(controller:prompt,maxLines:4,minLines:1,enabled:!busy,
              decoration:const InputDecoration(hintText:'Напиши Chelsy...',border:OutlineInputBorder()))),
            IconButton.filled(onPressed:busy?null:_send,icon:const Icon(Icons.send)),
          ])),
        ],
        Padding(padding:const EdgeInsets.fromLTRB(12,0,12,8),child:Row(children:[
          if (busy) const Padding(padding:EdgeInsets.only(right:8),child:SizedBox(height:12,width:12,
            child:CircularProgressIndicator(strokeWidth:2))),
          Expanded(child:Text(status,style:const TextStyle(fontSize:12,color:Color(0xFFB7ADD8)))),
        ])),
      ])),
    );
  }
}
