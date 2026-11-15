"""AutoLearner — autonomous study of internet resources (RAG, never model weights).

Chelsy learns on her own in three situations:

1. **From her own searches.** After an answer that used a real web/news
   search, the source pages are read in the background (robots.txt, rate
   limits, SSRF protection) and reliable ones are stored in the user's
   personal knowledge base. Next time the answer comes from local data,
   even offline.
2. **Curiosity.** When a factual question finds nothing in the knowledge
   base, Chelsy researches the topic in the background (only if the user
   allowed internet search for that message and a search API is set up).
3. **Periodic study and refresh** of owner-approved sources (already in
   ResearchScheduler: schedules + re-reading stale documents).

Safety rules: only pages with trust >= ``min_trust`` and without
prompt-injection markers are stored automatically; everything else waits
in "Исследования" for review. Daily limits per user. Every step is written
to the learning journal (``learning_log``) visible in the UI and the API.
Host-wide settings live in user_settings under the reserved user ``*``.
"""
from __future__ import annotations

import re

import core
from .scheduler import ResearchError

HOST_KEY = '*'
DEFAULTS = {
    'enabled': True,          # master switch
    'from_search': True,      # store sources of real web searches
    'curiosity': True,        # research unknown topics in the background
    'daily_jobs': 20,         # automatic jobs per user per day
    'pages_per_job': 3,
    'min_trust': 0.5,       # https page without red flags = 0.55; http, user content, injection < 0.5
}
_SMALLTALK = re.compile(r'^(привет|здравств|как дела|спасибо|пока|hi|hello|thanks|ок|ok)\b', re.I)
_WORD = re.compile(r'[^\W\d_]{3,}', re.UNICODE)


def settings():
    stored = core.get_setting(HOST_KEY, 'autolearn', {}) or {}
    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    merged['daily_jobs'] = max(0, min(int(merged['daily_jobs']), 200))
    merged['pages_per_job'] = max(1, min(int(merged['pages_per_job']), 10))
    merged['min_trust'] = max(0.3, min(float(merged['min_trust']), 1.0))
    for key in ('enabled', 'from_search', 'curiosity'):
        merged[key] = bool(merged[key])
    return merged


def save_settings(values):
    current = settings()
    current.update({k: v for k, v in (values or {}).items() if k in DEFAULTS})
    core.set_setting(HOST_KEY, 'autolearn', current)
    return settings()


class AutoLearner:
    def __init__(self, database, scheduler, searcher=None):
        self.db = database
        self.scheduler = scheduler
        self.searcher = searcher

    def _budget_left(self, username, cfg):
        return self.db.events_today(username) < cfg['daily_jobs']

    def _submit(self, username, topic, reason, cfg, urls=None):
        if not self._budget_left(username, cfg):
            self.db.log_event(username, 'limit', topic[:200], '', 'дневной лимит автоизучения исчерпан')
            return None
        if self.db.active_jobs(username) >= 1:
            return None            # never compete with the user's own research
        try:
            job = self.scheduler.submit(username, topic, target_scope=username, max_pages=cfg['pages_per_job'],
                                        auto_accept=True, urls=urls, min_trust=cfg['min_trust'], autolearn=True)
        except ResearchError as exc:
            self.db.log_event(username, 'error', topic[:200], '', str(exc)[:300])
            return None
        self.db.log_event(username, 'auto-job', topic[:200], (urls or [''])[0], reason)
        return job

    def after_answer(self, username, question, route, *, live_sources=(), local_results=(), searched=False,
                     web_enabled=True):
        """Called by the engine after each answer. Only enqueues work; returns the job id or None."""
        cfg = settings()
        if not cfg['enabled'] or not username:
            return None
        if searched and live_sources and cfg['from_search']:
            known = {r.get('url') for r in local_results}
            urls = [s['url'] for s in live_sources if s.get('url', '').startswith('https://') and s['url'] not in known]
            if urls:
                return self._submit(username, question[:200], 'изучаю источники своего поиска', cfg,
                                    urls=urls[:cfg['pages_per_job']])
            return None
        if (route == 'local' and not local_results and cfg['curiosity'] and web_enabled
                and self.searcher is not None and self.searcher.configured()):
            words = _WORD.findall(question or '')
            if len(words) >= 3 and not _SMALLTALK.search(question.strip()) and ('?' in question or len(words) >= 5):
                return self._submit(username, question[:200], 'в базе знаний ничего не нашлось — изучаю тему', cfg)
        return None
