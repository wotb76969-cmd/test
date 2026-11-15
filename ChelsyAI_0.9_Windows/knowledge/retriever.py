"""KnowledgeRetriever — hybrid (FTS5 BM25 + embeddings) search with scope isolation."""
from __future__ import annotations

import math
import re

import core
from .database import readable_scopes
from .indexer import unpack

_TOKEN = re.compile(r'[^\W_]{3,}', re.UNICODE)


def fts_query(text):
    words = list(dict.fromkeys(w.casefold() for w in _TOKEN.findall(text or '')))[:16]
    # Prefix match makes Russian inflections work reasonably ("модели" ~ "модел*").
    terms = ['"%s"*' % (w[:max(4, len(w) - 2)] if len(w) > 5 else w).replace('"', '') for w in words]
    return ' OR '.join(terms)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class KnowledgeRetriever:
    def __init__(self, embedder=None, vector_scan_limit=20_000):
        if embedder is None:
            import ollama_client
            embedder = ollama_client.embed
        self.embedder = embedder
        self.vector_scan_limit = vector_scan_limit

    def retrieve(self, query, username, top=4, per_doc=2, min_trust=0.0):
        scopes = readable_scopes(username)
        q = ','.join('?' * len(scopes))
        ranked = {}
        match = fts_query(query)
        with core.DB_LOCK, core.db() as conn:
            if match:
                try:
                    rows = conn.execute(
                        f"SELECT c.id, bm25(kb_fts) FROM kb_fts JOIN kb_chunks c ON c.id=kb_fts.rowid "
                        f"JOIN kb_documents d ON d.id=c.doc_id WHERE kb_fts MATCH ? AND d.status='active' "
                        f"AND d.scope IN ({q}) AND d.trust>=? ORDER BY bm25(kb_fts) LIMIT 60",
                        [match] + list(scopes) + [min_trust]).fetchall()
                except Exception:
                    rows = []
                for rank, (cid, _) in enumerate(rows):
                    ranked[cid] = ranked.get(cid, 0) + 1.0 / (60 + rank)
            vectors = None
            if self.embedder is not None:
                has = conn.execute(f"SELECT 1 FROM kb_chunks c JOIN kb_documents d ON d.id=c.doc_id WHERE "
                                   f"c.embedding IS NOT NULL AND d.status='active' AND d.scope IN ({q}) LIMIT 1",
                                   list(scopes)).fetchone()
                if has:
                    vectors = conn.execute(
                        f"SELECT c.id, c.embedding FROM kb_chunks c JOIN kb_documents d ON d.id=c.doc_id "
                        f"WHERE c.embedding IS NOT NULL AND d.status='active' AND d.scope IN ({q}) AND d.trust>=? LIMIT ?",
                        list(scopes) + [min_trust, self.vector_scan_limit]).fetchall()
        if vectors:
            qv = self.embedder([query])
            if qv:
                scored = sorted(((cosine(qv[0], unpack(blob)), cid) for cid, blob in vectors), reverse=True)[:60]
                for rank, (sim, cid) in enumerate(scored):
                    if sim > 0.35:
                        ranked[cid] = ranked.get(cid, 0) + 1.0 / (60 + rank)
        if not ranked:
            return []
        best = sorted(ranked.items(), key=lambda x: -x[1])[:40]
        ids = [cid for cid, _ in best]
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute(
                'SELECT c.id, c.text, d.id, d.url, d.title, d.published, d.fetched, d.trust, d.domain, d.scope, d.flags '
                'FROM kb_chunks c JOIN kb_documents d ON d.id=c.doc_id WHERE c.id IN (%s)' % ','.join('?' * len(ids)), ids
            ).fetchall()
        by_id = {r[0]: r for r in rows}
        results, per = [], {}
        for cid, score in best:
            r = by_id.get(cid)
            if not r or r[9] not in scopes:
                continue
            if per.get(r[2], 0) >= per_doc:
                continue
            per[r[2]] = per.get(r[2], 0) + 1
            results.append({'chunk_id': cid, 'text': r[1], 'doc_id': r[2], 'url': r[3], 'title': r[4],
                            'published': r[5], 'fetched': r[6], 'trust': r[7], 'domain': r[8],
                            'personal': r[9] != 'shared', 'score': round(score * (0.6 + 0.4 * r[7]), 6)})
            if len(results) >= top:
                break
        results.sort(key=lambda x: -x['score'])
        return results
