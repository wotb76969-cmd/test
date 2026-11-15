"""Chelsy AI Windows Client 0.9.3 — entry point.

The client needs NO Ollama: it signs in with Supabase Auth (Chelsy Group
account) and talks to your Chelsy Host over HTTPS (Tailscale Serve).
The GUI lives in client_gui.py; this module stays importable without Tk.
"""
from __future__ import annotations

from client_api import HostClient, HostError, Unauthorized, validate_host  # noqa: F401  (public API)


def main():
    import os
    import sys
    from pathlib import Path
    log_dir = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChelsyAI'
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:      # PyInstaller --windowed
        stream = (log_dir / 'client.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    from client_gui import ChelsyClientApp
    ChelsyClientApp().run()


if __name__ == '__main__':
    main()
