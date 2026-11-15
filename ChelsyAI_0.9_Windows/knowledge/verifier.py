"""SourceVerifier — reliability, freshness, corroboration and contradictions.

All scores are heuristics and are shown to the user as such ("оценка", not
"истина"). Contradictions are reported as *possible* conflicts for review.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import core

REPUTABLE_SUFFIXES = ('.gov', '.edu', '.int', '.gov.ru', '.edu.ru', '.ac.uk', '.europa.eu')
REPUTABLE_HOST_HINTS = ('docs.', 'developer.', 'wikipedia.org', 'python.org', 'mozilla.org', 'arxiv.org',
                        'nature.com', 'who.int', 'readthedocs.io', 'github.com', 'microsoft.com', 'android.com',
                        'flutter.dev', 'fastapi.tiangolo.com', 'postgresql.org', 'sqlite.org', 'kernel.org')
LOW_QUALITY_HINTS = ('blogspot.', 'pinterest.', 'quora.', 'answers.', 'forum', 'tiktok.', 'vk.com/wall')
_NUMBER_CLAIM = re.compile(r'([^.!?\n]{12,220}?)\b(\d[\d\s.,]*\d|\d)\s*(%|процент\w*|млн|млрд|тыс\w*|кг|км|°C|года?|лет|years?|million|billion|percent)?', re.I)
_WORD = re.compile(r'[^\W\d_]{4,}', re.UNICODE)


def parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], '%Y-%m-%d')
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SourceVerifier:
    def __init__(self, database=None):
        from .database import KnowledgeDatabase
        self.database = database or KnowledgeDatabase()

    def freshness(self, published, now=None):
        dt = parse_date(published)
        if dt is None:
            return 'unknown', None
        now = now or datetime.now(timezone.utc)
        days = max(0, (now - dt).days)
        return ('fresh' if days <= 30 else 'recent' if days <= 365 else 'old'), days

    def score(self, url, extracted, now=None):
        """Return (trust 0..1, reasons list)."""
        host = (urlsplit(url).hostname or '').lower()
        score, reasons = 0.5, []
        allow = self.database.source_trust(host)
        if allow is not None:
            score = max(score, allow)
            reasons.append('источник в списке разрешённых')
        if url.startswith('https://'):
            score += 0.05
        else:
            score -= 0.1
            reasons.append('нет HTTPS')
        if host.endswith(REPUTABLE_SUFFIXES) or any(h in host for h in REPUTABLE_HOST_HINTS):
            score += 0.15
            reasons.append('официальная документация / авторитетный домен')
        if any(h in host for h in LOW_QUALITY_HINTS):
            score -= 0.15
            reasons.append('пользовательский контент')
        state, days = self.freshness(extracted.published or extracted.modified, now)
        if state == 'fresh':
            score += 0.05
        elif state == 'old' and days and days > 3 * 365:
            score -= 0.1
            reasons.append('материал старше трёх лет')
        elif state == 'unknown':
            reasons.append('дата публикации не найдена')
        if len(extracted.text) < 400:
            score -= 0.15
            reasons.append('мало текста')
        if 'prompt-injection' in extracted.flags:
            score -= 0.3
            reasons.append('страница содержит инструкции для ИИ (удалены)')
        return round(max(0.0, min(1.0, score)), 2), reasons

    # ---------------------------------------------------------------- claims
    def claims(self, text, limit=60):
        out = []
        for m in _NUMBER_CLAIM.finditer(text[:30000]):
            subject = [w.casefold() for w in _WORD.findall(m.group(1))][-4:]
            if len(subject) >= 2:
                number = re.sub(r'[\s,]', '', m.group(2)).rstrip('.')
                out.append((tuple(subject), number, (m.group(1) + m.group(2) + (m.group(3) or '')).strip()))
            if len(out) >= limit:
                break
        return out

    def find_conflicts(self, doc_id, scope, topic_words=None, limit_docs=25):
        """Compare numeric claims of a document with documents on the same topic."""
        doc = self.database.get_document(doc_id)
        if not doc:
            return []
        mine = self.claims(doc['body'])
        if not mine:
            return []
        words = topic_words or [w for w in (doc['topic'] or '').split(', ') if w][:3]
        if not words:
            return []
        like = ' OR '.join('topic LIKE ?' for _ in words)
        with core.DB_LOCK, core.db() as conn:
            others = conn.execute(
                f"SELECT id, body, domain FROM kb_documents WHERE id<>? AND status='active' AND scope IN ('shared', ?) "
                f"AND domain<>? AND ({like}) LIMIT ?",
                [doc_id, scope, doc['domain']] + ['%' + w + '%' for w in words] + [limit_docs]).fetchall()
        found = []
        index = {}
        for subject, number, sentence in mine:
            index.setdefault(subject, []).append((number, sentence))
        for other_id, body, _ in others:
            for subject, number, sentence in self.claims(body):
                for my_number, my_sentence in index.get(subject, []):
                    if my_number != number and _numbers_differ(my_number, number):
                        self.database.add_conflict(doc_id, other_id, my_sentence, sentence)
                        found.append((other_id, my_sentence, sentence))
        return found

    def corroboration(self, results):
        """Number of independent domains among retrieved fragments."""
        return len({r.get('domain') for r in results if r.get('domain')})


def _numbers_differ(a, b):
    try:
        x, y = float(a.replace(',', '.')), float(b.replace(',', '.'))
    except ValueError:
        return a != b
    if 1900 <= x <= 2100 and 1900 <= y <= 2100:
        return False       # different years usually describe different events
    return abs(x - y) > max(abs(x), abs(y)) * 0.05
