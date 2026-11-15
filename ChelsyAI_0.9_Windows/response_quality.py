"""Small, dependency-free quality checks; no arbitrary web access."""
import re

_RUSSIAN = re.compile(r'[А-Яа-яЁё]')
_LATIN_WORD = re.compile(r'(?<![\w])(?:[A-Za-z]{3,})(?![\w])')
_TECH_WORDS = {
    'api', 'python', 'javascript', 'typescript', 'html', 'css', 'json', 'sql', 'sqlite',
    'supabase', 'ollama', 'flutter', 'android', 'windows', 'linux', 'github', 'http',
    'https', 'url', 'open', 'meteo', 'brave', 'search', 'chelsy', 'chatgpt', 'fastapi',
    'docker', 'git', 'npm', 'pip', 'exe', 'apk', 'web', 'client', 'host', 'token',
}


def requested_language(question):
    """Detect dominant script for a Russian/English exchange (best effort)."""
    cyr = len(_RUSSIAN.findall(question))
    lat = len(re.findall(r'[A-Za-z]', question))
    return 'ru' if cyr > lat and cyr >= 3 else 'en' if lat >= 3 and lat > cyr else None


def is_mixed_russian_answer(question, response):
    """Find substantial stray English prose, ignoring code, URLs and product names."""
    if requested_language(question) != 'ru':
        return False
    # Markdown code and URLs are expected to contain English identifiers.
    cleaned = re.sub(r'```.*?```|`[^`]*`|https?://\S+', ' ', response, flags=re.S | re.I)
    words = [w.casefold() for w in _LATIN_WORD.findall(cleaned)]
    foreign = [w for w in words if w not in _TECH_WORDS]
    return len(foreign) >= 2 and (len(foreign) >= 3 or len(foreign) >= len(words) / 2)


def capability_answer(question, online_search_enabled=False):
    """Honest answers about learning vs browsing; avoid small-model hallucination."""
    q = question.casefold().strip()
    if not re.search(r'(?:самостоятельн|самообуч|самообучен|обучаешь|обучает|изучаешь|изучает|учишься|learn|self.learn)', q):
        return None
    if not re.search(r'(?:интернет|сет[ьи]|сайт|источник|ресурс|знани|данны|information|internet|web|самообуч)', q):
        return None
    if requested_language(question) == 'en':
        return ('I do not retrain my model automatically. I can search the web when a configured '
                'search service is available, and I can save selected source information in a '
                'knowledge base. These are not the same as training my model weights.')
    search = ('Я могу искать актуальную информацию через подключённый поисковый сервис.'
              if online_search_enabled else
              'Для общего поиска в интернете нужно настроить Brave Search API на сервере.')
    return ('Нет, я не переобучаю свою языковую модель самостоятельно. '
            + search + ' Я также могу сохранять сведения из разрешённых источников в базу знаний '
            'и использовать их в ответах. Это поиск и пополнение базы, а не изменение весов модели.')


# ---------------------------------------------------------------- 0.9.3 checks
from dataclasses import dataclass, field

_CODE_OR_URL = re.compile(r'```.*?```|`[^`]*`|https?://\S+|\[[^\]]*\]\([^)]*\)', re.S | re.I)
_CJK = re.compile(r'[぀-ヿ㐀-䶿一-鿿가-힯]')
_WORD = re.compile(r'[^\W\d_]{2,}', re.UNICODE)
_FALSE_SEARCH = re.compile(
    r'(?:я\s+(?:только\s+что\s+)?(?:поискал[аи]?|нашл[аи]|проверил[аи]|загуглил[аи])\s+(?:в\s+)?(?:интернете|сети|google|гугле)'
    r'|по\s+данным\s+(?:моего|последнего)\s+поиска'
    r'|i\s+(?:just\s+)?(?:searched|googled|looked\s+(?:it\s+)?up|browsed)\s+(?:the\s+)?(?:web|internet|online)'
    r'|according\s+to\s+my\s+(?:web\s+)?search)', re.I)


def detect_language(text):
    """'ru', 'en' or None for an answer (code, links and identifiers ignored)."""
    cleaned = _CODE_OR_URL.sub(' ', text or '')
    cyr = len(_RUSSIAN.findall(cleaned))
    lat = len(re.findall(r'[A-Za-z]', cleaned))
    total = cyr + lat
    if total < 12:
        return None
    if cyr / total >= 0.55:
        return 'ru'
    if lat / total >= 0.8:
        return 'en'
    return None


def complexity(question):
    """Rough answer-length budget for the question: 'short' | 'medium' | 'long'."""
    q = (question or '').strip()
    words = len(_WORD.findall(q))
    low = q.casefold()
    if re.search(r'(подробн|пошагов|сравни|план|напиши (код|программ|скрипт|статью|эссе)|explain in detail|step by step|compare|write (a|the) (code|program|script|essay))', low):
        return 'long'
    if words <= 6 and not re.search(r'(почему|как работает|объясни|что такое|why|how does|explain|what is)', low):
        return 'short'
    return 'medium'


def repetition_ratio(text):
    """Share of repeated word 4-grams (0 = none, close to 1 = loops)."""
    words = [w.casefold() for w in _WORD.findall(_CODE_OR_URL.sub(' ', text or ''))]
    if len(words) < 24:
        return 0.0
    grams = [' '.join(words[i:i + 4]) for i in range(len(words) - 3)]
    return 1.0 - len(set(grams)) / len(grams)


@dataclass
class Assessment:
    issues: list = field(default_factory=list)
    serious: bool = False
    language: str | None = None

    def add(self, issue, serious=False):
        self.issues.append(issue)
        self.serious = self.serious or serious


def assess(question, answer, expected_language=None, searched=False):
    """Detect obviously broken answers that justify one controlled regeneration."""
    result = Assessment()
    text = (answer or '').strip()
    expected = expected_language or requested_language(question)
    result.language = detect_language(text)
    if not text:
        result.add('empty', True)
        return result
    plain = _CODE_OR_URL.sub(' ', text)
    if not _CJK.search(question or '') and len(_CJK.findall(plain)) >= 3:
        result.add('foreign-script', True)
    if expected and result.language and result.language != expected:
        result.add('wrong-language', True)
    if expected == 'ru' and is_mixed_russian_answer(question, text):
        result.add('mixed-language', True)
    ratio = repetition_ratio(text)
    if ratio > 0.45:
        result.add('repetition', True)
    elif ratio > 0.25:
        result.add('some-repetition')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 6 and len(set(lines)) <= len(lines) / 2:
        result.add('repeated-lines', True)
    letters = sum(ch.isalpha() for ch in plain)
    if len(plain) > 40 and letters / max(1, len(plain)) < 0.35 and '```' not in text:
        result.add('gibberish', True)
    if re.search(r'\S{90,}', plain):
        result.add('unbroken-token-run', True)
    if not searched and _FALSE_SEARCH.search(text):
        result.add('false-search-claim', True)
    return result


def remove_false_search_claims(text):
    """Last-resort cleanup when the model still claims an online search it never did."""
    return _FALSE_SEARCH.sub('по моим знаниям', text or '')
