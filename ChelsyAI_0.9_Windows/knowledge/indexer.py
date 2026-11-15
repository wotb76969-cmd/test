"""KnowledgeIndexer — chunking, full-text index (FTS5) and embeddings."""
from __future__ import annotations

import array
import re

import core

CHUNK = 1000
OVERLAP = 150


def chunk_text(text, size=CHUNK, overlap=OVERLAP):
    """Split on paragraph/sentence boundaries into ~size character chunks."""
    text = (text or '').strip()
    if not text:
        return []
    pieces = re.split(r'(?<=[.!?…])\s+|\n+', text)
    chunks, current = [], ''
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        while len(piece) > size:              # very long sentence / code line
            head, piece = piece[:size], piece[size - overlap:]
            if current:
                chunks.append(current)
                current = ''
            chunks.append(head)
        if len(current) + len(piece) + 1 > size and current:
            chunks.append(current)
            tail = current[-overlap:]
            current = (tail[tail.find(' ') + 1:] if ' ' in tail else '') + ' ' + piece
        else:
            current = (current + ' ' + piece).strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks


def pack(vector):
    return array.array('f', vector).tobytes()


def unpack(blob):
    values = array.array('f')
    values.frombytes(blob)
    return values


class KnowledgeIndexer:
    def __init__(self, embedder=None, embed_model=None):
        """embedder: callable(list[str]) -> list[vector] | None (default: Ollama)."""
        if embedder is None:
            import ollama_client
            embedder = ollama_client.embed
            embed_model = embed_model or ollama_client.EMBED_MODEL
        self.embedder = embedder
        self.embed_model = embed_model or 'custom'

    def index_document(self, doc_id, body):
        chunks = chunk_text(body)
        with core.DB_LOCK, core.db() as conn:
            old = [r[0] for r in conn.execute('SELECT id FROM kb_chunks WHERE doc_id=?', (doc_id,))]
            for cid in old:
                conn.execute('DELETE FROM kb_fts WHERE rowid=?', (cid,))
            conn.execute('DELETE FROM kb_chunks WHERE doc_id=?', (doc_id,))
            ids = []
            for order, text in enumerate(chunks):
                cid = conn.execute('INSERT INTO kb_chunks(doc_id,ord,text) VALUES(?,?,?)', (doc_id, order, text)).lastrowid
                conn.execute('INSERT INTO kb_fts(rowid,text) VALUES(?,?)', (cid, text))
                ids.append(cid)
        self._embed(list(zip(ids, chunks)))
        return len(chunks)

    def _embed(self, pairs):
        if not pairs or self.embedder is None:
            return 0
        done = 0
        for start in range(0, len(pairs), 16):
            batch = pairs[start:start + 16]
            vectors = self.embedder([text for _, text in batch])
            if not vectors:
                return done           # embedding model not installed: FTS still works
            with core.DB_LOCK, core.db() as conn:
                for (cid, _), vec in zip(batch, vectors):
                    conn.execute('UPDATE kb_chunks SET embedding=?, embed_model=? WHERE id=?',
                                 (pack(vec), self.embed_model, cid))
            done += len(batch)
        return done

    def backfill_embeddings(self, limit=200):
        with core.DB_LOCK, core.db() as conn:
            rows = conn.execute('SELECT id,text FROM kb_chunks WHERE embedding IS NULL LIMIT ?', (int(limit),)).fetchall()
        return self._embed(rows)
