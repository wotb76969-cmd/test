"""Opt-in Chelsy cloud history/memory bridge.

Supabase PostgREST requests carry the actual user's access token and public
project key: RLS remains the authorization boundary. The host never impersonates
users with a service-role key, and never overwrites/deletes old local records.
"""
from __future__ import annotations
import json
import sqlite3
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from core import DB_PATH, DATA, db, DB_LOCK
from supabase_auth import config, storage_key

class SyncError(RuntimeError): pass


def _call(table, token, *, method='GET', params=None, rows=None, prefer=None):
    cfg = config()
    if cfg is None: raise SyncError('Supabase not configured')
    url, key = cfg
    suffix = ('?' + urlencode(params, doseq=True)) if params else ''
    headers = {'apikey':key, 'Authorization':'Bearer '+token,
               'Accept':'application/json', 'Cache-Control':'no-store'}
    raw = None
    if rows is not None:
        raw=json.dumps(rows,ensure_ascii=False).encode('utf-8')
        headers['Content-Type']='application/json'
    if prefer: headers['Prefer']=prefer
    request=Request(url+'/rest/v1/'+table+suffix,headers=headers,data=raw,method=method)
    try:
        with build_opener().open(request,timeout=25) as response:
            body=response.read()
            return json.loads(body) if body else []
    except HTTPError as exc:
        raise SyncError('Cloud sync HTTP %d: check schema, RLS and account session' % exc.code) from exc
    except (URLError,TimeoutError,ValueError) as exc:
        raise SyncError('Cloud service unavailable or returned invalid data') from exc


def _init(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS cloud_imported (username TEXT NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,cloud_id))')
    conn.execute('CREATE TABLE IF NOT EXISTS cloud_origin (username TEXT NOT NULL, local_id INTEGER NOT NULL, cloud_id TEXT NOT NULL, PRIMARY KEY(username,local_id))')


def pull(uid, token):
    """Import cloud messages into this user's local conversation, idempotently.

    Pages are at most 500 records. The beta caps initial sync to 5,000 rows.
    """
    username=storage_key(uid)
    imported=0
    for offset in range(0,5000,500):
        rows=_call('chelsy_ai_messages',token,params={
            'select':'id,legacy_id,role,content,created_at',
            'user_id':'eq.'+uid,'order':'created_at.asc,id.asc',
            'limit':'500','offset':str(offset)})
        if not isinstance(rows,list):raise SyncError('Unexpected cloud history response')
        with DB_LOCK,db() as conn:
            _init(conn)
            for entry in rows:
                cloud_id,legacy_id=entry.get('id'),entry.get('legacy_id')
                role,content=entry.get('role'),entry.get('content')
                created=entry.get('created_at')
                if not isinstance(cloud_id,str) or role not in ('user','assistant') or not isinstance(content,str):continue
                if len(content)>100_000:continue
                if conn.execute('SELECT 1 FROM cloud_imported WHERE username=? AND cloud_id=?',(username,cloud_id)).fetchone():continue
                exists=False
                if isinstance(legacy_id,str) and legacy_id.startswith('host:'):
                    try:
                        local_id=int(legacy_id[5:])
                        exists=bool(conn.execute('SELECT 1 FROM messages WHERE id=? AND username=?',(local_id,username)).fetchone())
                    except ValueError:pass
                if not exists:
                    cur=conn.execute('INSERT INTO messages(username,role,content,created) VALUES(?,?,?,?)',
                                    (username,role,content,created if isinstance(created,str) else ''))
                    conn.execute('INSERT OR IGNORE INTO cloud_origin(username,local_id,cloud_id) VALUES(?,?,?)',(username,cur.lastrowid,cloud_id))
                    imported+=1
                conn.execute('INSERT OR IGNORE INTO cloud_imported(username,cloud_id) VALUES(?,?)',(username,cloud_id))
        if len(rows)<500:break
    return imported


def push(uid,token):
    username=storage_key(uid)
    with DB_LOCK,db() as conn:
        _init(conn)
        records=conn.execute('SELECT id,role,content,created FROM messages WHERE username=? ORDER BY id DESC LIMIT 5000',(username,)).fetchall()
    # Never echo imported cloud messages back into new host messages.
    with DB_LOCK,db() as conn:
        skipped={row[0] for row in conn.execute('SELECT local_id FROM cloud_origin WHERE username=?',(username,))}
    upload=[]
    for mid,role,content,created in reversed(records):
        if mid in skipped: continue
        upload.append({'user_id':uid,'legacy_id':'host:'+str(mid),'role':role,'content':content,'created_at':created})
    for i in range(0,len(upload),50):
        _call('chelsy_ai_messages',token,method='POST',
              params={'on_conflict':'user_id,legacy_id'},rows=upload[i:i+50],
              prefer='resolution=ignore-duplicates,return=minimal')
    return len(upload)


def sync(uid,token):
    """Pull then push; a failed cloud request never deletes local data."""
    received=pull(uid,token)
    sent=push(uid,token)
    # Historical cloud-origin marking supports future migration and avoids echo loops.
    return {'received':received,'considered_for_upload':sent,'cloud':'ok'}


def save_memory(uid,token,key,value):
    if not (isinstance(key,str) and isinstance(value,str) and 1<=len(key)<=75 and 1<=len(value)<=2000):
        raise ValueError('Invalid memory')
    _call('chelsy_ai_memories',token,method='POST',params={'on_conflict':'user_id,memory_key'},
          rows=[{'user_id':uid,'memory_key':key,'value':value}],
          prefer='resolution=merge-duplicates,return=minimal')


def pull_memories(uid,token):
    rows=_call('chelsy_ai_memories',token,params={'select':'memory_key,value','user_id':'eq.'+uid,'limit':'200'})
    if not isinstance(rows,list):raise SyncError('Invalid memories response')
    username=storage_key(uid)
    count=0
    from core import datetime,timezone
    with DB_LOCK,db() as conn:
        for row in rows:
            key,value=row.get('memory_key'),row.get('value')
            if not (isinstance(key,str) and isinstance(value,str)):continue
            if conn.execute('SELECT 1 FROM pending_memory WHERE username=? AND memory_key=?',(username,key)).fetchone():
                continue  # local offline edit has priority until uploaded
            before=conn.execute('SELECT value FROM personal_memory WHERE username=? AND memory_key=?',(username,key)).fetchone()
            if before is None or before[0]!=value:
                conn.execute('INSERT INTO personal_memory(username,memory_key,value,created) VALUES(?,?,?,?) '
                             'ON CONFLICT(username,memory_key) DO UPDATE SET value=excluded.value,created=excluded.created',
                             (username,key,value,datetime.now(timezone.utc).isoformat()))
                count+=1
    return count


def push_memories(uid,token):
    from core import personal_facts
    username=storage_key(uid)
    facts=personal_facts(username)
    if not facts:return 0
    with DB_LOCK,db() as conn:
        dirty={r[0] for r in conn.execute('SELECT memory_key FROM pending_memory WHERE username=?',(username,))}
    for key,value in facts.items():
        _call('chelsy_ai_memories',token,method='POST',params={'on_conflict':'user_id,memory_key'},
              rows=[{'user_id':uid,'memory_key':key,'value':value}],
              prefer='resolution=merge-duplicates,return=minimal' if key in dirty else 'resolution=ignore-duplicates,return=minimal')
        if key in dirty:
            with DB_LOCK,db() as conn:
                # Do not clear a pending edit that happened during the network request.
                current=conn.execute('SELECT value FROM personal_memory WHERE username=? AND memory_key=?',(username,key)).fetchone()
                if current and current[0]==value:
                    conn.execute('DELETE FROM pending_memory WHERE username=? AND memory_key=?',(username,key))
    return len(facts)


def delete_cloud_memory(uid,token,key):
    _call('chelsy_ai_memories',token,method='DELETE',params={'user_id':'eq.'+uid,'memory_key':'eq.'+key})


def delete_cloud_history(uid,token):
    _call('chelsy_ai_messages',token,method='DELETE',params={'user_id':'eq.'+uid})
