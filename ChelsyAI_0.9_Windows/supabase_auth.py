"""Supabase Auth integration with no service-role credentials.

Only a project URL and publishable/anon key are needed. Passwords go to
Supabase Auth HTTPS, NEVER to Chelsy Host. Host verifies bearer tokens
with Supabase /auth/v1/user on each request (safe, slower than cached JWKS).
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

class AuthError(Exception):
    pass


def config():
    settings = {}
    settings_path = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChelsyAI' / 'supabase.json'
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise AuthError('Invalid Supabase configuration file')
    url = os.getenv('CHELSY_SUPABASE_URL', settings.get('url','')).strip().rstrip('/')
    key = os.getenv('CHELSY_SUPABASE_PUBLISHABLE_KEY', settings.get('publishable_key','')).strip()
    if not url and not key:
        return None
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.path not in ('','/') or p.username or p.password or p.query or p.fragment:
        raise AuthError('CHELSY_SUPABASE_URL must be a plain HTTPS origin')
    if not key or '\n' in key or '\r' in key:
        raise AuthError('Missing Supabase publishable key')
    if key.startswith('sb_secret_'):
        raise AuthError('Never use sb_secret or service_role in Desktop or Client')
    return url, key


def call_auth(endpoint, data=None, bearer=None, timeout=12):
    cfg = config()
    if not cfg:
        raise AuthError('Supabase not configured')
    url, key = cfg
    headers = {'apikey': key, 'Accept':'application/json'}
    if bearer: headers['Authorization'] = 'Bearer ' + bearer
    payload = None if data is None else json.dumps(data).encode('utf-8')
    if payload is not None: headers['Content-Type'] = 'application/json'
    req = Request(url + '/auth/v1/' + endpoint, headers=headers, data=payload, method='POST' if payload is not None else 'GET')
    try:
        # System proxy is acceptable for public Supabase URL. TLS must be verified.
        with build_opener().open(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise AuthError('Supabase rejected the request (HTTP %d)' % exc.code) from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise AuthError('Supabase unavailable or invalid response') from exc


def login(email, password):
    if not isinstance(email, str) or not isinstance(password,str) or not email.strip() or not password:
        raise AuthError('Email and password are required')
    response = call_auth('token?grant_type=password', {'email':email.strip(), 'password':password})
    if not response.get('access_token') or not response.get('user',{}).get('id'):
        raise AuthError('No active user session (email confirmation may be required)')
    return response


def refresh(refresh_token):
    return call_auth('token?grant_type=refresh_token', {'refresh_token': refresh_token})


def verify(token):
    if not isinstance(token, str) or len(token) < 20 or len(token) > 10000:
        raise AuthError('Invalid access token')
    result = call_auth('user', bearer=token)
    uid = result.get('id')
    if not isinstance(uid, str) or len(uid) < 10:
        raise AuthError('User identity not available')
    return uid


def storage_key(uid):
    import uuid
    # Storage identity is always the Supabase immutable UUID, never email.
    return 'sb:' + str(uuid.UUID(uid))


def admin_user_id():
    settings_path = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChelsyAI' / 'supabase.json'
    try:
        settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    except (OSError, ValueError):
        settings = {}
    return os.environ.get('CHELSY_ADMIN_USER_ID',settings.get('admin_user_id','')).strip()
