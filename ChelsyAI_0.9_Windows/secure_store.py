"""Protected storage for the client's Supabase refresh token.

Windows: the token is encrypted with DPAPI (CryptProtectData, current user
scope) and written to %LOCALAPPDATA%\\ChelsyAI\\client_session.bin. Only the
same Windows user on the same PC can decrypt it.
Other OSes: nothing is persisted (the user logs in again) — we never write
tokens in plain text. Passwords are never stored anywhere.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENTROPY = b'ChelsyAI-client-0.9.3'


def _path():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChelsyAI' / 'client_session.bin'


def supported():
    return sys.platform == 'win32'


def _blob(data):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, DATA_BLOB


def _crypt(data, protect):
    import ctypes
    blob_in, _keep, DATA_BLOB = _blob(data)
    entropy, _keep2, _ = _blob(ENTROPY)
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, 0x01, ctypes.byref(blob_out))
    if not ok:
        raise OSError('DPAPI operation failed')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def save(token: str) -> bool:
    if not supported() or not token:
        return False
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_crypt(token.encode('utf-8'), True))
    return True


def load():
    if not supported():
        return None
    try:
        return _crypt(_path().read_bytes(), False).decode('utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def clear():
    try:
        _path().unlink()
    except OSError:
        pass
