"""Optional Supabase Auth for Gradio Desktop; local legacy login remains when unconfigured."""
import os
import threading
from core import authenticate as local_auth
from supabase_auth import config, login, refresh, storage_key, admin_user_id, AuthError

_lock=threading.Lock()
_verified={}
_tokens={}

def authenticate(username,password):
    if config() is None:
        return local_auth(username,password)
    try:
        r=login(username,password)
        key=storage_key(r['user']['id'])
        with _lock:
            _verified[username.strip().lower()]=key
            _tokens[key]={'access_token':r.get('access_token',''),'refresh_token':r.get('refresh_token')}
        return True
    except (AuthError,ValueError,KeyError,TypeError):
        return False

def account_name(username):
    if config() is None: return username
    with _lock: return _verified.get((username or '').strip().lower())

def is_admin(username):
    if config() is None: return username=='owner'
    return account_name(username)==('sb:'+admin_user_id())


def bearer_for(username):
    if config() is None: return None
    with _lock:
        session=_tokens.get(username)
    if not session: return None
    # Supabase tokens typically expire after an hour. Refresh as necessary
    # using the provider's refresh endpoint, never persisting passwords.
    import base64,json,time
    token=session.get('access_token')
    try:
        chunks=token.split('.')
        encoded=chunks[1]+'='*((-len(chunks[1]))%4)
        payload=json.loads(base64.urlsafe_b64decode(encoded))
        if isinstance(payload.get('exp'),(int,float)) and payload['exp'] > time.time()+120:
            return token
    except (ValueError,KeyError,IndexError,TypeError): pass
    if not session.get('refresh_token'):
        raise AuthError('Сессия Supabase закончилась. Войди заново.')
    renewed=refresh(session['refresh_token'])
    with _lock:
        _tokens[username]={'access_token':renewed['access_token'],
            'refresh_token':renewed.get('refresh_token',session['refresh_token'])}
    return renewed['access_token']
