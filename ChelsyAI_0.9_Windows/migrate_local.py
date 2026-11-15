"""Explicit, non-destructive one-account migration after Supabase login.

Run locally on the original host machine, with local user password. Does not
move password hashes or delete the legacy data. Makes backup before writing.
"""
import argparse
import os
os.environ["CHELSY_APPDATA"] = "1"
import shutil
from datetime import datetime, timezone
from pathlib import Path
from core import DB_PATH, USERS_PATH, db, DB_LOCK, authenticate
from supabase_auth import login, storage_key


def copy_account(old, new):
    if old == new:
        raise ValueError('source and destination are identical')
    with DB_LOCK, db() as connection:
        # EXPLICIT COPY ONLY, no deletion and no overwrite of existing cloud-keyed facts.
        # Prevent duplicate import on repeated runs: provenance key for each origin.
        connection.execute('CREATE TABLE IF NOT EXISTS migration_log (source TEXT NOT NULL, destination TEXT NOT NULL, done TEXT NOT NULL, PRIMARY KEY(source,destination))')
        if connection.execute('SELECT 1 FROM migration_log WHERE source=? AND destination=?',(old,new)).fetchone():
            return 0, 0
        mc = connection.execute('INSERT OR IGNORE INTO personal_memory(username,memory_key,value,created) SELECT ?,memory_key,value,created FROM personal_memory WHERE username=?',(new,old)).rowcount
        hc = connection.execute('INSERT INTO messages(username,role,content,created) SELECT ?,role,content,created FROM messages WHERE username=? ORDER BY id',(new,old)).rowcount
        connection.execute('INSERT INTO migration_log(source,destination,done) VALUES (?,?,?)',(old,new,datetime.now(timezone.utc).isoformat()))
    return hc, mc


def main():
    p=argparse.ArgumentParser(description='Move one legacy account into Supabase UID namespace; does not delete old data')
    p.add_argument('--old-user',required=True)
    p.add_argument('--email',required=True)
    args=p.parse_args()
    import getpass
    if not authenticate(args.old_user, getpass.getpass('Existing local Chelsy password: ')):
        raise SystemExit('Incorrect local password. Nothing migrated.')
    result=login(args.email, getpass.getpass('Supabase account password: '))
    dest=storage_key(result['user']['id'])
    if not DB_PATH.exists(): raise SystemExit('Local database does not exist')
    dest_backup=DB_PATH.with_name(DB_PATH.name+'.backup-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    # SQLite backup API handles WAL and live databases more safely than copying DB file.
    import sqlite3
    with sqlite3.connect(DB_PATH) as original, sqlite3.connect(dest_backup) as target:
        original.backup(target)
    num_messages, num_facts = copy_account(args.old_user, dest)
    print('Supabase user ID:',result['user']['id'])
    print('Local history/memory records copied:',num_messages,num_facts)
    print('Backup:',dest_backup)
    print('Original local account is preserved. This does NOT automatically upload to Supabase.')

if __name__=='__main__': main()
