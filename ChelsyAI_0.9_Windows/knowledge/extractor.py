"""ContentExtractor — HTML cleanup, main-content detection and metadata.

The extracted text is DATA. Instruction-like fragments ("ignore previous
instructions", "system prompt" …) are flagged and neutralised before they
can reach the language model; personal contact data of third parties
(e-mails, phone numbers) is redacted before storage.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

SKIP = {'script', 'style', 'svg', 'noscript', 'template', 'iframe', 'canvas', 'form', 'button', 'select', 'object', 'embed'}
BOILERPLATE = {'nav', 'footer', 'header', 'aside'}
BLOCK = {'p', 'div', 'section', 'article', 'main', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
         'pre', 'blockquote', 'table', 'tr', 'br', 'hr', 'dd', 'dt', 'figcaption'}
VOID = {'br', 'hr', 'img', 'meta', 'link', 'input', 'source', 'wbr', 'area', 'base', 'col', 'track'}
DATE_META = ('article:published_time', 'og:published_time', 'datepublished', 'date', 'dc.date', 'dc.date.issued',
             'pubdate', 'publish-date', 'sailthru.date', 'parsely-pub-date', 'citation_publication_date')
MODIFIED_META = ('article:modified_time', 'og:updated_time', 'datemodified', 'last-modified', 'dc.date.modified')

INJECTION = re.compile(
    r'(ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?)'
    r'|disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions'
    r'|you\s+are\s+now\s+(?:a|an|in)\b|new\s+instructions\s*:|system\s+prompt|developer\s+mode'
    r'|jailbreak|act\s+as\s+(?:an?\s+)?(?:unfiltered|dan)\b'
    r'|игнорируй\s+(?:все\s+)?(?:предыдущие|прошлые|прежние)\s+(?:инструкции|указания)'
    r'|забудь\s+(?:все\s+)?(?:предыдущие|прошлые)\s+(?:инструкции|указания)|системн\w+\s+промпт'
    r'|теперь\s+ты\s+(?:—|-)?\s*\w+\s+без\s+ограничений|новые\s+инструкции\s*:)', re.I)
EMAIL = re.compile(r'\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+\b')
PHONE = re.compile(r'(?<![\w/])(?:\+\d{1,3}[\s\-]?)?(?:\(\d{2,4}\)[\s\-]?)?\d{3}[\s\-]\d{2}[\s\-]?\d{2,4}(?![\w/])')
STOP = set('''и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж
тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда
можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем
хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это также который которые
the a an and or of to in is are was were be been for on with as by at from that this it its into than then there their
not but can will would should could you your we our they them he she his her i me my do does did have has had so if about
also more most other some such only over any each which who what when where how why all may using use used new one two'''.split())


@dataclass
class Extracted:
    title: str = ''
    text: str = ''
    description: str = ''
    lang: str = ''
    published: str = ''
    modified: str = ''
    canonical: str = ''
    links: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    topic: str = ''


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.skip = 0
        self.boiler = 0
        self.in_title = False
        self.in_jsonld = False
        self.jsonld = []
        self.title = []
        self.meta = {}
        self.lang = ''
        self.canonical = ''
        self.links = []
        self.times = []
        self.main_depth = 0
        self.all_text = []
        self.main_text = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'html' and a.get('lang'):
            self.lang = a['lang'][:10].lower()
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or a.get('itemprop') or a.get('http-equiv') or '').lower()
            if key and a.get('content'):
                self.meta.setdefault(key, a['content'][:500])
            return
        if tag == 'link' and 'canonical' in a.get('rel', '').lower() and a.get('href'):
            self.canonical = a['href'][:900]
            return
        if tag == 'script' and 'ld+json' in a.get('type', '').lower():
            self.in_jsonld = True
        if tag == 'time' and a.get('datetime'):
            self.times.append(a['datetime'][:40])
        if tag in VOID:
            if tag == 'br':
                self._newline()
            return
        self.stack.append(tag)
        if tag in SKIP:
            self.skip += 1
        if tag in BOILERPLATE:
            self.boiler += 1
        if tag in ('main', 'article') or a.get('role') == 'main':
            self.main_depth += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'a' and a.get('href') and not self.skip:
            self.links.append(a['href'][:900])
        if tag in BLOCK:
            self._newline()
        if tag == 'li':
            self._emit('• ')

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_jsonld = False
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            if top in SKIP:
                self.skip = max(0, self.skip - 1)
            if top in BOILERPLATE:
                self.boiler = max(0, self.boiler - 1)
            if top in ('main', 'article'):
                self.main_depth = max(0, self.main_depth - 1)
            if top == 'title':
                self.in_title = False
            if top in BLOCK:
                self._newline()
            if top == tag:
                break

    def _emit(self, text):
        self.all_text.append(text)
        if self.main_depth:
            self.main_text.append(text)

    def _newline(self):
        self._emit('\n')

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld.append(data)
            return
        if self.in_title:
            self.title.append(data.strip())
            return
        if self.skip or (self.boiler and not self.main_depth):
            return
        if data.strip():
            self._emit(data)


def _clean(parts):
    text = html.unescape(''.join(parts)).replace('\xa0', ' ')
    lines = []
    for line in text.split('\n'):
        line = re.sub(r'[ \t\r\f\v]+', ' ', line).strip()
        if line and line != '•':
            lines.append(line)
    out, seen = [], Counter()
    for line in lines:
        seen[line] += 1
        if seen[line] <= 2:          # drop repeated menus / cookie banners
            out.append(line)
    return '\n'.join(out)


def normalize_date(value):
    """ISO date (YYYY-MM-DD or full timestamp) or ''."""
    value = (value or '').strip()
    if not value:
        return ''
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?', value)
    if m:
        try:
            datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return m.group(0).replace(' ', 'T')
        except ValueError:
            return ''
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', value)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
        except ValueError:
            return ''
    return ''


def neutralize_injection(text):
    """Return (clean_text, found) with instruction-like sentences replaced."""
    found = False

    def repl(_m):
        nonlocal found
        found = True
        return '[фрагмент похож на инструкцию для ИИ и удалён]'
    sentences = re.split(r'(?<=[.!?\n])\s+', text)
    cleaned = [repl(None) if INJECTION.search(s) else s for s in sentences]
    return ' '.join(cleaned) if found else text, found


def redact_personal_data(text):
    text = EMAIL.sub('[e-mail скрыт]', text)
    return PHONE.sub('[телефон скрыт]', text)


def keywords(text, limit=8):
    words = [w.casefold() for w in re.findall(r'[^\W\d_]{4,}', text, re.UNICODE)]
    counts = Counter(w for w in words if w not in STOP)
    return [w for w, _ in counts.most_common(limit)]


def guess_language(text):
    cyr = len(re.findall(r'[А-Яа-яЁё]', text[:5000]))
    lat = len(re.findall(r'[A-Za-z]', text[:5000]))
    if cyr + lat < 50:
        return ''
    return 'ru' if cyr > lat else 'en'


class ContentExtractor:
    max_text = 60_000

    def extract(self, document, url='', content_type='text/html'):
        result = Extracted()
        if content_type == 'text/plain':
            result.text = _clean([document[:self.max_text * 2]])
            result.title = (result.text.split('\n', 1)[0] or url)[:200]
        else:
            parser = _Parser()
            try:
                parser.feed(document)
                parser.close()
            except Exception:       # malformed HTML must not stop research
                pass
            main = _clean(parser.main_text)
            full = _clean(parser.all_text)
            result.text = main if len(main) >= max(400, len(full) * 0.25) else full
            meta = parser.meta
            result.title = (meta.get('og:title') or ' '.join(t for t in parser.title if t) or url)[:250]
            result.description = meta.get('description') or meta.get('og:description') or ''
            result.lang = parser.lang[:2] if parser.lang else ''
            result.canonical = parser.canonical
            result.links = parser.links[:300]
            ld_pub, ld_mod = '', ''
            for raw in parser.jsonld:
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                for item in (data if isinstance(data, list) else [data]):
                    if isinstance(item, dict):
                        graph = item.get('@graph') if isinstance(item.get('@graph'), list) else [item]
                        for node in graph:
                            if isinstance(node, dict):
                                ld_pub = ld_pub or str(node.get('datePublished', ''))
                                ld_mod = ld_mod or str(node.get('dateModified', ''))
            published = ld_pub or next((meta[k] for k in DATE_META if k in meta), '') or (parser.times[0] if parser.times else '')
            modified = ld_mod or next((meta[k] for k in MODIFIED_META if k in meta), '')
            result.published = normalize_date(published)
            result.modified = normalize_date(modified)
        text = result.text[:self.max_text]
        text, injected = neutralize_injection(text)
        if injected:
            result.flags.append('prompt-injection')
        redacted = redact_personal_data(text)
        if redacted != text:
            result.flags.append('pii-redacted')
        result.text = redacted
        result.title, title_injected = neutralize_injection(result.title)
        if title_injected and 'prompt-injection' not in result.flags:
            result.flags.append('prompt-injection')
        result.description = redact_personal_data(neutralize_injection(result.description)[0])[:500]
        result.lang = result.lang or guess_language(result.text)
        merged = list(dict.fromkeys(keywords(result.title, 3) + keywords(result.text[:8000], 6)))
        result.topic = ', '.join(merged[:6])
        return result
