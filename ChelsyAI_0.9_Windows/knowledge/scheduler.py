"""ResearchScheduler — background research jobs with review before storage.

Job kinds
* ``topic`` – search the configured search API and read the top results;
* ``site``  – read a given public URL and pages under the same path;
* ``docs``  – "изучи документацию X": find official docs, then read them.

Found pages are stored as *research items*. The user decides what goes to
the personal or (owner only) shared knowledge base, unless the job was
explicitly created with ``auto_accept``. Periodic schedules run only on
owner-approved (allow-listed) domains. Stale documents are re-checked and
updated as new versions (old text archived, never silently deleted).
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from .database import KnowledgeDatabase, content_hash, now
from .extractor import ContentExtractor
from .fetcher import FetchError, SourceFetcher, normalize_url
from .indexer import KnowledgeIndexer
from .search_manager import SearchManager
from .verifier import SourceVerifier

URL = re.compile(r'https?://[^\s<>"\'«»]+', re.I)
DOCS = re.compile(r'(?:документаци\w*|docs|documentation)\s+(?:по\s+|for\s+|к\s+)?(.{2,60})$', re.I)
MAX_ACTIVE_PER_USER = 2


class ResearchError(Exception):
    pass


class ResearchScheduler:
    def __init__(self, database=None, fetcher=None, extractor=None, verifier=None, indexer=None, search=None,
                 max_pages_limit=25):
        self.db = database or KnowledgeDatabase()
        self.fetcher = fetcher or SourceFetcher()
        self.extractor = extractor or ContentExtractor()
        self.verifier = verifier or SourceVerifier(self.db)
        self._indexer = indexer
        self.search = search or SearchManager()
        self.max_pages_limit = max_pages_limit
        self._stops = {}
        self._lock = threading.Lock()
        self._thread = None
        self._shutdown = threading.Event()
        self._wake = threading.Event()
        self.current = None

    @property
    def indexer(self):
        if self._indexer is None:
            self._indexer = KnowledgeIndexer()
        return self._indexer

    # ---------------------------------------------------------------- submit
    def classify(self, topic):
        url = URL.search(topic or '')
        if url:
            return 'site', url.group(0).rstrip('.,;)')
        m = DOCS.search((topic or '').strip(' ?!.'))
        if m:
            return 'docs', m.group(1).strip()
        cleaned = re.sub(r'^(?:пожалуйста[,\s]+)?(?:изучи|исследуй|проштудируй|проиндексируй|study|research)\s+', '',
                         (topic or '').strip(), flags=re.I)
        return 'topic', cleaned.strip(' ?!.') or topic

    def submit(self, username, topic, *, target_scope=None, max_pages=8, auto_accept=False, is_owner=False,
               allowlist_only=False, news=False, urls=None, min_trust=None, autolearn=False):
        topic = re.sub(r'\s+', ' ', topic or '').strip()[:300]
        if len(topic) < 3:
            raise ResearchError('Опиши тему исследования подробнее.')
        if self.db.active_jobs(username) >= MAX_ACTIVE_PER_USER:
            raise ResearchError('Уже выполняются два исследования. Дождись завершения или останови одно.')
        target_scope = target_scope or username
        if target_scope == 'shared' and not is_owner:
            raise ResearchError('Добавлять материалы в общую базу знаний может только владелец.')
        if target_scope not in ('shared', username):
            raise ResearchError('Недопустимая база знаний.')
        kind, subject = self.classify(topic)
        if urls:
            kind = 'urls'
        params = {'subject': subject, 'max_pages': max(1, min(int(max_pages), self.max_pages_limit)),
                  'target_scope': target_scope, 'auto_accept': bool(auto_accept),
                  'allowlist_only': bool(allowlist_only), 'news': bool(news),
                  'urls': [u for u in (urls or []) if isinstance(u, str)][:10],
                  'min_trust': min_trust, 'autolearn': bool(autolearn)}
        job_id = self.db.create_job(username, topic, kind, params)
        self._wake.set()
        return job_id

    def stop(self, job_id, username):
        job = self.db.get_job(job_id, username)
        if not job:
            return False
        with self._lock:
            event = self._stops.setdefault(job_id, threading.Event())
        event.set()
        if job['status'] == 'queued':
            self.db.update_job(job_id, status='stopped', message='Остановлено пользователем')
        return True

    # ---------------------------------------------------------------- review
    def _own_item(self, item_id, username):
        item = self.db.item_full(item_id)
        if not item:
            return None, None
        job = self.db.get_job(item['job_id'], username)
        return (item, job) if job else (None, None)

    def accept(self, item_id, username, scope=None, is_owner=False):
        item, job = self._own_item(item_id, username)
        if not item:
            raise ResearchError('Материал не найден.')
        scope = scope or username
        if scope == 'shared' and not is_owner:
            raise ResearchError('Общую базу знаний пополняет только владелец.')
        if scope not in ('shared', username):
            raise ResearchError('Недопустимая база знаний.')
        if not item['body']:
            raise ResearchError('У этого результата нет загруженного текста.')
        flags = list(item['flags']) + (['auto-learned'] if job['params'].get('autolearn') else [])
        doc_id, action = self.db.upsert_document(
            scope, item['url'], item['title'] or item['url'], item['body'], published=item['published'],
            lang=item['lang'], topic=item['topic'], trust=item['trust'], flags=flags, indexer=self.indexer)
        if action in ('created', 'updated'):
            self.db.log_event(username, 'learned' if job['params'].get('autolearn') else 'added',
                              item['title'] or item['url'], item['url'],
                              ('новая версия; ' if action == 'updated' else '') +
                              ('общая база' if scope == 'shared' else 'личная база') + f'; надёжность {item["trust"]:.2f}')
        self.db.set_item_status(item_id, 'accepted', doc_id=doc_id,
                                note={'created': 'Добавлено', 'updated': 'Обновлена новая версия',
                                      'unchanged': 'Уже в базе', 'duplicate': 'Дубликат уже сохранённого материала'}[action])
        if action in ('created', 'updated'):
            try:
                self.verifier.find_conflicts(doc_id, scope)
            except Exception:
                pass
        return doc_id, action

    def reject(self, item_id, username):
        item, _ = self._own_item(item_id, username)
        if not item:
            raise ResearchError('Материал не найден.')
        self.db.set_item_status(item_id, 'rejected')

    # ---------------------------------------------------------------- running
    def _stopped(self, job_id):
        with self._lock:
            event = self._stops.get(job_id)
        return bool(event and event.is_set())

    def _allowed_host(self, url, allowlist_only):
        if not allowlist_only:
            return True
        return self.db.source_trust(urlsplit(url).hostname or '') is not None

    def _read(self, job, url, seen_hashes):
        """Fetch + extract + verify one page into a research item. Returns extracted or None."""
        try:
            page = self.fetcher.fetch(url)
        except FetchError as exc:
            self.db.add_item(job['id'], url, status='error', note=str(exc)[:200])
            return None
        extracted = self.extractor.extract(page.text, page.final_url, page.content_type)
        if len(extracted.text) < 200:
            self.db.add_item(job['id'], url, title=extracted.title, status='error', note='Слишком мало текста')
            return None
        digest = content_hash(extracted.text)
        if digest in seen_hashes:
            self.db.add_item(job['id'], url, title=extracted.title, status='duplicate', note='Дубликат другой страницы')
            return None
        seen_hashes.add(digest)
        trust, reasons = self.verifier.score(page.final_url, extracted)
        item_id = self.db.add_item(job['id'], url, title=extracted.title, published=extracted.published or extracted.modified,
                                   excerpt=(extracted.description or extracted.text[:400])[:500], body=extracted.text,
                                   content_hash=digest, trust=trust, flags=extracted.flags, status='fetched',
                                   note='; '.join(reasons)[:300], lang=extracted.lang, topic=extracted.topic)
        min_trust = job['params'].get('min_trust')
        if job['params'].get('auto_accept') and (
                (min_trust is not None and trust < min_trust) or 'prompt-injection' in extracted.flags):
            # Autonomous learning keeps only reliable pages; the rest wait for the user's decision.
            self.db.set_item_status(item_id, 'fetched', note=('Низкая надёжность — нужна проверка; ' + '; '.join(reasons))[:300])
            if job['params'].get('autolearn'):
                self.db.log_event(job['username'], 'skipped', extracted.title, url, f'надёжность {trust:.2f} — оставлено на проверку')
        elif job['params'].get('auto_accept'):
            try:
                self.accept(item_id, job['username'], job['params']['target_scope'],
                            is_owner=job['params']['target_scope'] == 'shared')
            except Exception as exc:
                self.db.set_item_status(item_id, 'fetched', note=f'Автодобавление не выполнено: {exc}'[:300])
        return extracted

    def run_job(self, job):
        job_id, params = job['id'], job['params']
        limit = params['max_pages']
        self.db.update_job(job_id, status='running', progress=0.02, message='Подготовка')
        seen, done = set(), 0
        try:
            if job['kind'] == 'topic':
                self.db.update_job(job_id, message='Поиск источников')
                results, note = self.search.search(params['subject'], 'news' if params.get('news') else 'web')
                if not results:
                    raise ResearchError(note or 'Поиск ничего не нашёл.')
                queue = [r['url'] for r in results]
                crawl_prefix = None
            elif job['kind'] == 'urls':
                queue = list(params.get('urls') or [])
                crawl_prefix = None
            elif job['kind'] == 'docs':
                self.db.update_job(job_id, message='Ищу официальную документацию')
                results, note = self.search.documentation(params['subject'])
                if not results:
                    raise ResearchError(note or 'Документация не найдена. Укажи ссылку на неё.')
                queue = [results[0]['url']]
                crawl_prefix = _prefix(results[0]['url'])
            else:
                start = normalize_url(params['subject'])
                if not start:
                    raise ResearchError('Некорректная ссылка.')
                queue = [start]
                crawl_prefix = _prefix(start)
            visited = set()
            while queue and done < limit:
                if self._stopped(job_id) or self._shutdown.is_set():
                    self.db.update_job(job_id, status='stopped', message=f'Остановлено. Прочитано страниц: {done}')
                    return
                url = normalize_url(queue.pop(0))
                if not url or url in visited:
                    continue
                visited.add(url)
                if not self._allowed_host(url, params.get('allowlist_only')):
                    self.db.add_item(job_id, url, status='skipped', note='Домен не в списке разрешённых источников')
                    continue
                self.db.update_job(job_id, message=f'Читаю {url[:120]}', progress=round(0.05 + 0.9 * done / limit, 3))
                extracted = self._read(job, url, seen)
                if extracted is None:
                    continue
                done += 1
                if crawl_prefix:
                    for href in extracted.links:
                        link = normalize_url(href, url)
                        if link and link.startswith(crawl_prefix) and link not in visited and len(queue) < 200:
                            queue.append(link)
            message = f'Готово: найдено материалов — {done}. Проверь их и добавь нужные в базу знаний.'
            if params.get('auto_accept'):
                items = self.db.items(job_id)
                added = sum(1 for i in items if i['status'] == 'accepted')
                review = sum(1 for i in items if i['status'] == 'fetched')
                message = f'Готово: в базу знаний добавлено — {added}' + (f', ждут проверки — {review}' if review else '') + '.'
            if params.get('autolearn'):
                self.db.log_event(job['username'], 'auto-done', job['topic'][:200], '', message)
            self.db.update_job(job_id, status='done', progress=1.0, message=message)
        except ResearchError as exc:
            self.db.update_job(job_id, status='error', message=str(exc)[:300])
        except Exception as exc:
            self.db.update_job(job_id, status='error', message=f'Внутренняя ошибка ({type(exc).__name__})')

    def run_pending(self, max_jobs=1):
        processed = 0
        while processed < max_jobs:
            job = self.db.next_queued_job()
            if not job:
                break
            if self._stopped(job['id']):
                self.db.update_job(job['id'], status='stopped', message='Остановлено пользователем')
                continue
            self.current = job['id']
            try:
                self.run_job(job)
            finally:
                self.current = None
            processed += 1
        return processed

    # ---------------------------------------------------------------- periodic
    def due_schedules(self, at=None):
        at = at or datetime.now(timezone.utc)
        due = []
        for sched in self.db.schedules():
            if not sched['enabled']:
                continue
            last = sched['last_run']
            try:
                last_dt = datetime.fromisoformat(last) if last else None
            except ValueError:
                last_dt = None
            if last_dt is None or at - last_dt >= timedelta(minutes=sched['interval_minutes']):
                due.append(sched)
        return due

    def run_schedules(self, owner_check=lambda username: False):
        started = []
        for sched in self.due_schedules():
            try:
                job = self.submit(sched['username'], sched['topic'], target_scope=sched['target_scope'],
                                  auto_accept=bool(sched['auto_accept']), is_owner=owner_check(sched['username']),
                                  allowlist_only=True, max_pages=5)
                started.append(job)
            except ResearchError:
                pass
            self.db.set_schedule(sched['id'], sched['username'], last_run=now())
        return started

    def refresh_stale(self, days=14, limit=5):
        """Re-read old documents from allow-listed domains; changes become new versions."""
        updated = 0
        for doc in self.db.stale_documents(days, limit):
            if self.db.source_trust(urlsplit(doc['url']).hostname or '') is None:
                continue
            try:
                page = self.fetcher.fetch(doc['url'])
            except FetchError:
                continue
            ext = self.extractor.extract(page.text, page.final_url, page.content_type)
            if len(ext.text) < 200:
                continue
            trust, _ = self.verifier.score(page.final_url, ext)
            _, action = self.db.upsert_document(doc['scope'], doc['url'], ext.title, ext.text,
                                                published=ext.published or ext.modified, lang=ext.lang,
                                                topic=ext.topic, trust=trust, flags=ext.flags, indexer=self.indexer)
            updated += action == 'updated'
        return updated

    # ---------------------------------------------------------------- thread
    def start(self, owner_check=lambda username: False, interval=60):
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()

        def loop():
            last_periodic = 0.0
            while not self._shutdown.is_set():
                try:
                    self.run_pending(max_jobs=1)
                    if time.monotonic() - last_periodic > interval:
                        last_periodic = time.monotonic()
                        self.run_schedules(owner_check)
                        self.refresh_stale()
                        self.indexer.backfill_embeddings(64)
                except Exception as exc:     # keep the worker alive
                    print('[Research worker]', type(exc).__name__, flush=True)
                self._wake.wait(5)
                self._wake.clear()
        self._thread = threading.Thread(target=loop, name='chelsy-research', daemon=True)
        self._thread.start()

    def shutdown(self):
        self._shutdown.set()
        self._wake.set()


def _prefix(url):
    parts = urlsplit(url)
    path = parts.path if parts.path.endswith('/') else parts.path.rsplit('/', 1)[0] + '/'
    return f'{parts.scheme}://{parts.netloc}{path}'
