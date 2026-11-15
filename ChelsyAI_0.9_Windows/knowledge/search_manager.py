"""SearchManager — web / news search through the configured search API (Brave).

The API key lives only on the Host (env CHELSY_BRAVE_SEARCH_KEY). Results
are untrusted data. When no key/connection is available the caller gets an
explicit note, never fabricated results.
"""
from __future__ import annotations

import os

import live_tools


class SearchManager:
    def __init__(self, web=None, news=None):
        self._web = web or live_tools.brave_search
        self._news = news or live_tools.brave_news

    @staticmethod
    def configured():
        return bool(os.environ.get('CHELSY_BRAVE_SEARCH_KEY', '').strip())

    def search(self, query, kind='web'):
        """Return (results, note). results: list of {url,title,excerpt,published,kind}."""
        try:
            items, note = (self._news if kind == 'news' else self._web)(query)
        except Exception as exc:        # network/HTTP/JSON problems
            return [], f'Поисковый сервис недоступен ({type(exc).__name__}).'
        return items, note

    def documentation(self, name):
        """Find official documentation pages for a product name."""
        items, note = self.search(f'{name} official documentation', 'web')
        docs = [i for i in items if any(h in i['url'] for h in ('docs', 'documentation', 'readthedocs', 'developer', 'manual', 'tutorial'))]
        return (docs or items), note
