# Override: Windows Client (priority surface)

Inherits `../MASTER.md`. Code: `ChelsyAI_0.9_Windows/client_gui.py` (Tkinter/ttk, standard library only).

- No web views, no third-party GUI toolkits or fonts: the installer must stay light and Ollama-free.
- Styling only through the `ttk.Style` in `_style()` and the colour constants at the top of the file.
- DPI-aware (`SetProcessDpiAwareness(1)`); check 100 %, 125 %, 150 % scaling and windows down to
  760 × 520 — bottom controls are packed first so they never disappear.
- Keyboard: Enter sends, Shift+Enter newline; every button reachable with Tab; visible focus colour
  `turquoise`.
- Markdown is rendered by `_insert_markdown` (headings, bold, inline code, code blocks, https links).
- Offline: status shows "Хост недоступен — показана сохранённая копия" from `client_cache.py`.
- Verify: `python -m unittest test_client_gui093` (needs Tk; runs on the Windows CI job) plus the full
  suite.
