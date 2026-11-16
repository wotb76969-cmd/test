# Override: Android app (priority surface)

Inherits `../MASTER.md`. Code: `Android_Client/lib/` (Flutter 3.50, `package:material_ui`).

- Follow Material 3 / Android conventions (see impeccable `reference/android.md`): navigation drawer on
  phones, permanent side panel from 900 dp, system back gesture, edge-to-edge with SafeArea.
- Touch targets ≥ 48 dp; icon buttons need `tooltip` (TalkBack label).
- Composer pinned to the bottom above the keyboard (`adjustResize`); send/stop toggle in the same spot.
- Long-press own message → edit; message actions (copy, regenerate, 👍/👎) as compact icon buttons.
- Offline: `MaterialBanner` with "Повторить"; read-only local or cloud copy is labelled.
- Theme only through `lib/src/theme.dart` (`ChelsyColors`); `flutter_markdown_plus` needs an explicit
  `MarkdownStyleSheet` (it still uses the legacy `flutter/material` ThemeData type).
- Respect `MediaQuery.disableAnimations` and text scaling; test at 360 × 800 dp.
- Verify: `flutter analyze` and `flutter test` must stay green.
