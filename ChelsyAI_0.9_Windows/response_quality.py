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
