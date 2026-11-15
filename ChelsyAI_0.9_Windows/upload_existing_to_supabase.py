"""Explicit opt-in one-time archival upload to Supabase with per-user RLS.

Default is a dry run. No destructive changes or service-role key. The user
must authenticate with both local and Supabase accounts.
"""
import os
os.environ['CHELSY_APPDATA'] = '1'
import argparse
import getpass
import json
import sqlite3
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from core import DB_PATH, authenticate
from supabase_auth import login, config


def push(table, rows, token, conf, conflict):
    if not rows: return
    url, public_key = conf
    endpoint = url + '/rest/v1/' + table + '?' + urlencode({'on_conflict':conflict})
    headers={'apikey':public_key,'Authorization':'Bearer '+token,'Content-Type':'application/json',
             'Prefer':'resolution=ignore-duplicates,return=minimal'}
    for start in range(0,len(rows),50):
        req=Request(endpoint, data=json.dumps(rows[start:start+50],ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
        try:
            with urlopen(req,timeout=30) as response:
                if response.status not in (200,201,204):
                    raise RuntimeError('Upload returned HTTP '+str(response.status))
        except HTTPError as e:
            raise RuntimeError('Upload rejected (HTTP %d); verify RLS/schema. No local data was deleted.' % e.code) from e


def main():
    p=argparse.ArgumentParser(description='Safely import legacy Chelsy chat/memory into Supabase tables.')
    p.add_argument('--old-user',required=True,help='e.g. owner')
    p.add_argument('--email',required=True,help='Supabase email account')
    p.add_argument('--upload',action='store_true',help='Actually upload records (default: preview only)')
    a=p.parse_args()
    if not DB_PATH.exists(): raise SystemExit('No existing Chelsy local database found.')
    if not authenticate(a.old_user,getpass.getpass('Local Chelsy password: ')):
        raise SystemExit('Local account password did not match.')
    auth=login(a.email,getpass.getpass('Supabase password: '))
    user_id=auth['user']['id']
    with sqlite3.connect(DB_PATH) as conn:
        msgs=conn.execute('select id,role,content,created from messages where username=? order by id',(a.old_user,)).fetchall()
        memories=conn.execute('select memory_key,value from personal_memory where username=?',(a.old_user,)).fetchall()
    print('Import for Supabase UID:',user_id)
    print('Chat records:',len(msgs),'Memory records:',len(memories))
    if not a.upload:
        print('DRY RUN ONLY. Add --upload to proceed after reviewing schema and RLS.')
        return
    print('Uploading over HTTPS using your own Supabase Auth token...')
    message_rows=[{'user_id':user_id,'legacy_id':a.old_user+':'+str(id_),
                   'role':role,'content':content,'created_at':created} for id_,role,content,created in msgs]
    fact_rows=[{'user_id':user_id,'memory_key':key,'value':value} for key,value in memories]
    push('chelsy_ai_messages',message_rows,auth['access_token'],config(),'user_id,legacy_id')
    push('chelsy_ai_memories',fact_rows,auth['access_token'],config(),'user_id,memory_key')
    print('Upload finished. Source data on this PC was not deleted.')

if __name__=='__main__':main()
