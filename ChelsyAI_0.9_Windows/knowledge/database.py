"""KnowledgeDatabase — documents, versions, sources, research jobs (SQLite).

Scopes: ``'shared'`` (owner-curated common base) or a user storage key
(personal base). Every query filters by scope, so one user never reads
another user's personal knowledge. Updates never silently delete knowledge:
the previous version is archived into ``kb_versions`` first.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import core


def now():
    return datetime.now(timezone.utc).isoformat()


def content_hash(text):
    normalized = re.sub(r'\s+', ' ', text or '').strip().casefold()
    return hashlib.sha256(normalized.encode('utf-8', 'replace')).hexdigest()


def readable_scopes(username):
    return ('shared', username) if username else ('shared',)


class KnowledgeDatabase:
    # ------------------------------------------------------------ documents
    def upsert_document(self, scope, url, title, body, *, canonical_url=None, published='', lang='',
                        topic='', trust=0.5, flags=None, indexer=None):
        """Store or update a document. Returns (doc_id, action).

        action: created | updated | unchanged | duplicate
        """
        canonical_url = canonical_url or url
        digest = content_hash(body)
        domain = (urlsplit(canonical_url).hostname or '').lower()
        stamp = now()
        flags_json = json.dumps(sorted(set(flags or [])), ensure_ascii=False)
        with core.DB_LOCK, core.db() as conn:
            row = conn.execute('SELECT id, content_hash, version, title, body, fetched, published FROM kb_documents '
                               'WHERE scope=? AND canonical_url=?', (scope, canonical_url)).fetchone()
            if row is None:
                dup = conn.execute("SELECT id FROM kb_documents WHERE scope=? AND content_hash=? AND status='active'",
                                   (scope, digest)).fetchone()
                if dup:
                    return dup[0], 'duplicate'
                cur = conn.execute(
                    'INSERT INTO kb_documents(scope,url,canonical_url,domain,title,topic,lang,published,fetched,updated,'
                    'content_hash,body,trust,flags,status,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,\'active\',1)',
                    (scope, url, canonical_url, domain, title[:250], topic[:200], lang[:5], published[:40], stamp,
                     stamp, digest, body, float(trust), flags_json))
                doc_id, action = cur.lastrowid, 'created'
            else:
                doc_id, old_hash, version, old_title, old_body, old_fetched, old_pub = row
                if old_hash == digest:
                    conn.execute("UPDATE kb_documents SET fetched=?, trust=?, status='active' WHERE id=?",
                                 (stamp, float(trust), doc_id))
                    return doc_id, 'unchanged'
                conn.execute('INSERT OR IGNORE INTO kb_versions(doc_id,version,title,content_hash,body,fetched,published,archived) '
                             'VALUES(?,?,?,?,?,?,?,?)', (doc_id, version, old_title, old_hash, old_body, old_fetched,
                                                          old_pub or '', stamp))
                conn.execute('UPDATE kb_documents SET url=?, title=?, topic=?, lang=?, published=?, fetched=?, updated=?, '
                             "content_hash=?, body=?, trust=?, flags=?, status='active', version=? WHERE id=?",
                             (url, title[:250], topic[:200], lang[:5], published[:40] or old_pub or '', stamp, stamp,
                              digest, body, float(trust), flags_json, version + 1, doc_id))
                action = 'updated'
        if indexer is not None:
            indexer.index_document(doc_id, body)
        return doc_id, action

    def get_document(self, doc_id, username=None):
        with core.DB_LOCK, core.db() as conn:
            row = conn.execute('SELECT id,scope,url,title,topic,lang,published,fetched,updated,trust,flags,status,version,body,domain '
                               'FROM kb_documents WHERE id=?', (int(doc_id),)).fetchone()
        if not row or (username is not None and row[1] not in readable_scopes(username)):
            return None
        keys = ('id', 'scope', 'url', 'title', 'topic', 'lang', 'published', 'fetched', 'updated', 'trust', 'flags',
                'status', 'version', 'body', 'domain')
        item = dict(zip(keys, row))
        item['flags'] = json.loads(item['flags'] or '[]')
        return item

    def list_documents(self, username, query='', scope=None, limit=50):
        scopes = readable_scopes(username) if scope is None else ((scope,) if scope in readable_scopes(username) else ())
        if not scopes:
            return []
        sql = ('SELECT id,scope,url,title,topic,published,fetched,trust,version,domain,flags FROM kb_documents '
               "WHERE status='active' AND scope IN (%s)" % ','.join('?' * len(scopes)))
        args = list(scopes)
        if query:
            like = '%' + query[:100].replace('%', '').replace('_', '') + '%'
            sql += ' AND (title LIKE ? OR topic LIKE ? OR url LIKE ?)'
            args += [like, like, like]
        sql += ' ORDER BY fetched DESC LIMIT ?'
        args.append(int(limit))
        keys = ('id', 'scope', 'url', 'title', 'topic', 'published', 'fetched', 'trust', 'version', 'domain', 'flags')
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(zip(keys, row))
            item['flags'] = json.loads(item['flags'] or '[]')
            item['personal'] = item['scope'] != 'shared'
            out.append(item)
        return out

    def archive_document(self, doc_id, scope):
        """Controlled removal: the document is hidden, its text stays in kb_versions."""
        with core.DB_LOCK, core.db() as conn:
            row = conn.execute('SELECT version,title,content_hash,body,fetched,published FROM kb_documents WHERE id=? AND scope=?',
                               (int(doc_id), scope)).fetchone()
            if not row:
                return False
            conn.execute('INSERT OR IGNORE INTO kb_versions(doc_id,version,title,content_hash,body,fetched,published,archived) '
                         'VALUES(?,?,?,?,?,?,?,?)', (int(doc_id),) + tuple(row) + (now(),))
            conn.execute("UPDATE kb_documents SET status='archived' WHERE id=?", (int(doc_id),))
        return True

    def versions(self, doc_id):
        with core.DB_LOCK, core.db() as conn:
            return [dict(zip(('version', 'title', 'fetched', 'archived'), r)) for r in conn.execute(
                'SELECT version,title,fetched,archived FROM kb_versions WHERE doc_id=? ORDER BY version DESC', (int(doc_id),))]

    def stale_documents(self, older_than_days=14, limit=10):
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute("SELECT id,scope,url FROM kb_documents WHERE status='active' AND "
                                "julianday('now') - julianday(fetched) > ? ORDER BY fetched LIMIT ?",
                                (float(older_than_days), int(limit))).fetchall()
        return [dict(zip(('id', 'scope', 'url'), r)) for r in rows]

    def stats(self, username=None):
        scopes = readable_scopes(username)
        with core.DB_LOCK, core.db() as conn:
            q = ','.join('?' * len(scopes))
            docs = conn.execute(f"SELECT COUNT(*) FROM kb_documents WHERE status='active' AND scope IN ({q})", scopes).fetchone()[0]
            chunks = conn.execute(f"SELECT COUNT(*) FROM kb_chunks c JOIN kb_documents d ON d.id=c.doc_id "
                                  f"WHERE d.status='active' AND d.scope IN ({q})", scopes).fetchone()[0]
            embedded = conn.execute(f"SELECT COUNT(*) FROM kb_chunks c JOIN kb_documents d ON d.id=c.doc_id "
                                    f"WHERE d.status='active' AND c.embedding IS NOT NULL AND d.scope IN ({q})", scopes).fetchone()[0]
            conflicts = conn.execute('SELECT COUNT(*) FROM kb_conflicts WHERE resolved=0').fetchone()[0]
        return {'documents': docs, 'chunks': chunks, 'embedded_chunks': embedded, 'open_conflicts': conflicts}

    # ------------------------------------------------------------ allowlist
    def allow_source(self, domain, added_by, trust=0.7, note=''):
        domain = (domain or '').strip().lower().lstrip('.')
        domain = re.sub(r'^https?://', '', domain).split('/', 1)[0]
        if not re.fullmatch(r'[a-z0-9.-]{3,253}', domain) or '.' not in domain:
            raise ValueError('Укажи домен, например docs.python.org')
        with core.DB_LOCK, core.db() as conn:
            conn.execute('INSERT INTO kb_sources(domain,added_by,trust,created,note) VALUES(?,?,?,?,?) '
                         'ON CONFLICT(domain) DO UPDATE SET trust=excluded.trust, note=excluded.note',
                         (domain, added_by, max(0.0, min(1.0, float(trust))), now(), note[:200]))
        return domain

    def remove_source(self, domain):
        with core.DB_LOCK, core.db() as conn:
            conn.execute('DELETE FROM kb_sources WHERE domain=?', (domain.strip().lower(),))

    def sources(self):
        with core.DB_LOCK, core.db() as conn:
            return [dict(zip(('domain', 'trust', 'note', 'created'), r)) for r in
                    conn.execute('SELECT domain,trust,note,created FROM kb_sources ORDER BY domain')]

    def source_trust(self, host):
        host = (host or '').lower()
        with core.DB_LOCK, core.db() as conn:
            for domain, trust in conn.execute('SELECT domain,trust FROM kb_sources'):
                if host == domain or host.endswith('.' + domain):
                    return trust
        return None

    # ------------------------------------------------------------ conflicts
    def add_conflict(self, doc_a, doc_b, claim_a, claim_b):
        a, b = sorted((int(doc_a), int(doc_b)))
        with core.DB_LOCK, core.db() as conn:
            conn.execute('INSERT OR IGNORE INTO kb_conflicts(doc_a,doc_b,claim_a,claim_b,detected) VALUES(?,?,?,?,?)',
                         (a, b, claim_a[:500], claim_b[:500], now()))

    def conflicts(self, username, limit=30):
        scopes = readable_scopes(username)
        q = ','.join('?' * len(scopes))
        sql = (f'SELECT c.id,c.claim_a,c.claim_b,a.url,b.url,a.title,b.title,c.detected FROM kb_conflicts c '
               f'JOIN kb_documents a ON a.id=c.doc_a JOIN kb_documents b ON b.id=c.doc_b '
               f'WHERE c.resolved=0 AND a.scope IN ({q}) AND b.scope IN ({q}) ORDER BY c.id DESC LIMIT ?')
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute(sql, list(scopes) + list(scopes) + [int(limit)]).fetchall()
        keys = ('id', 'claim_a', 'claim_b', 'url_a', 'url_b', 'title_a', 'title_b', 'detected')
        return [dict(zip(keys, r)) for r in rows]

    # ------------------------------------------------------------ research
    def create_job(self, username, topic, kind, params):
        job_id = str(uuid.uuid4())
        stamp = now()
        with core.DB_LOCK, core.db() as conn:
            conn.execute('INSERT INTO research_jobs(id,username,topic,kind,status,progress,message,params,created,updated) '
                         "VALUES(?,?,?,?,'queued',0,'В очереди',?,?,?)",
                         (job_id, username, topic[:300], kind, json.dumps(params, ensure_ascii=False), stamp, stamp))
        return job_id

    def update_job(self, job_id, **fields):
        allowed = {'status', 'progress', 'message'}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sets['updated'] = now()
        with core.DB_LOCK, core.db() as conn:
            conn.execute('UPDATE research_jobs SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?',
                         list(sets.values()) + [job_id])

    def get_job(self, job_id, username=None):
        with core.DB_LOCK, core.db() as conn:
            row = conn.execute('SELECT id,username,topic,kind,status,progress,message,params,created,updated '
                               'FROM research_jobs WHERE id=?', (job_id,)).fetchone()
        if not row or (username is not None and row[1] != username):
            return None
        keys = ('id', 'username', 'topic', 'kind', 'status', 'progress', 'message', 'params', 'created', 'updated')
        job = dict(zip(keys, row))
        job['params'] = json.loads(job['params'] or '{}')
        return job

    def jobs(self, username, limit=20):
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute('SELECT id FROM research_jobs WHERE username=? ORDER BY created DESC LIMIT ?',
                                (username, int(limit))).fetchall()
        return [self.get_job(r[0]) for r in rows]

    def next_queued_job(self):
        with core.DB_LOCK, core.db() as conn:
            row = conn.execute("SELECT id FROM research_jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        return self.get_job(row[0]) if row else None

    def active_jobs(self, username):
        with core.DB_LOCK, core.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM research_jobs WHERE username=? AND status IN ('queued','running')",
                                (username,)).fetchone()[0]

    def add_item(self, job_id, url, **fields):
        cols = ['title', 'published', 'excerpt', 'body', 'content_hash', 'trust', 'flags', 'status', 'note', 'lang', 'topic']
        values = {k: fields[k] for k in cols if k in fields}
        if 'flags' in values and not isinstance(values['flags'], str):
            values['flags'] = json.dumps(values['flags'], ensure_ascii=False)
        with core.DB_LOCK, core.db() as conn:
            conn.execute('INSERT OR IGNORE INTO research_items(job_id,url) VALUES(?,?)', (job_id, url))
            if values:
                conn.execute('UPDATE research_items SET ' + ', '.join(f'{k}=?' for k in values) +
                             ' WHERE job_id=? AND url=?', list(values.values()) + [job_id, url])
            return conn.execute('SELECT id FROM research_items WHERE job_id=? AND url=?', (job_id, url)).fetchone()[0]

    def items(self, job_id):
        keys = ('id', 'url', 'title', 'published', 'excerpt', 'trust', 'flags', 'status', 'doc_id', 'note', 'lang', 'topic')
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute('SELECT ' + ','.join(keys) + ' FROM research_items WHERE job_id=? ORDER BY trust DESC, id',
                                (job_id,)).fetchall()
        out = []
        for r in rows:
            item = dict(zip(keys, r))
            item['flags'] = json.loads(item['flags'] or '[]')
            out.append(item)
        return out

    def item_full(self, item_id):
        keys = ('id', 'job_id', 'url', 'title', 'published', 'body', 'trust', 'flags', 'status', 'lang', 'topic')
        with core.DB_LOCK, core.db() as conn:
            row = conn.execute('SELECT ' + ','.join(keys) + ' FROM research_items WHERE id=?', (int(item_id),)).fetchone()
        if not row:
            return None
        item = dict(zip(keys, row))
        item['flags'] = json.loads(item['flags'] or '[]')
        return item

    def set_item_status(self, item_id, status, doc_id=None, note=None):
        with core.DB_LOCK, core.db() as conn:
            conn.execute('UPDATE research_items SET status=?, doc_id=COALESCE(?, doc_id), note=COALESCE(?, note) WHERE id=?',
                         (status, doc_id, note, int(item_id)))

    # ------------------------------------------------------------ schedules
    def add_schedule(self, username, topic, interval_minutes, target_scope, auto_accept=False):
        sid = str(uuid.uuid4())
        with core.DB_LOCK, core.db() as conn:
            conn.execute('INSERT INTO research_schedule(id,username,topic,interval_minutes,enabled,auto_accept,target_scope,created) '
                         'VALUES(?,?,?,?,1,?,?,?)', (sid, username, topic[:300], max(60, min(int(interval_minutes), 10080)),
                                                     1 if auto_accept else 0, target_scope, now()))
        return sid

    def schedules(self, username=None):
        sql = 'SELECT id,username,topic,interval_minutes,enabled,auto_accept,target_scope,last_run FROM research_schedule'
        args = []
        if username is not None:
            sql += ' WHERE username=?'
            args.append(username)
        keys = ('id', 'username', 'topic', 'interval_minutes', 'enabled', 'auto_accept', 'target_scope', 'last_run')
        with core.DB_LOCK, core.db() as conn:
            return [dict(zip(keys, r)) for r in conn.execute(sql, args)]

    def set_schedule(self, schedule_id, username, enabled=None, last_run=None, delete=False):
        with core.DB_LOCK, core.db() as conn:
            if delete:
                conn.execute('DELETE FROM research_schedule WHERE id=? AND username=?', (schedule_id, username))
                return
            if enabled is not None:
                conn.execute('UPDATE research_schedule SET enabled=? WHERE id=? AND username=?',
                             (1 if enabled else 0, schedule_id, username))
            if last_run is not None:
                conn.execute('UPDATE research_schedule SET last_run=? WHERE id=?', (last_run, schedule_id))
