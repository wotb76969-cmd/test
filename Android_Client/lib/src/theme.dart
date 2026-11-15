import 'package:material_ui/material_ui.dart';

/// Chelsy AI brand palette (shared with Desktop and Windows Client).
class ChelsyColors {
  static const background = Color(0xFF0B0C20);
  static const panel = Color(0xFF11132B);
  static const panel2 = Color(0xFF161937);
  static const stroke = Color(0xFF262A52);
  static const violet = Color(0xFF6D4AFF);
  static const lavender = Color(0xFFA78BFA);
  static const teal = Color(0xFF21D9DA);
  static const text = Color(0xFFECEBFA);
  static const muted = Color(0xFFA3A6C8);
  static const userBubble = Color(0xFF231D4F);
  static const danger = Color(0xFFFF8A65);
}

ThemeData chelsyTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: ChelsyColors.violet,
    brightness: Brightness.dark,
  ).copyWith(
    primary: ChelsyColors.violet,
    secondary: ChelsyColors.teal,
    tertiary: ChelsyColors.lavender,
    surface: ChelsyColors.panel,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: ChelsyColors.background,
    appBarTheme: const AppBarTheme(
      backgroundColor: ChelsyColors.background,
      foregroundColor: ChelsyColors.text,
      elevation: 0,
      centerTitle: false,
    ),
    drawerTheme: const DrawerThemeData(backgroundColor: ChelsyColors.panel),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: ChelsyColors.panel2,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: ChelsyColors.stroke),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: ChelsyColors.stroke),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: ChelsyColors.violet, width: 1.6),
      ),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
  );
}
