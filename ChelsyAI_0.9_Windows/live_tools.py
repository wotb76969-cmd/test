"""Live information adapters. No arbitrary URL fetches; bounded endpoints only."""
import json
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_OPENER = build_opener(ProxyHandler({}), NoRedirect())
_MAX_RESPONSE = 240_000


def fetch_json(base, parameters, headers=None):
    """Read JSON from a fixed, developer-selected HTTPS endpoint."""
    url = base + '?' + urlencode(parameters)
    request = Request(url, headers={'User-Agent': 'ChelsyAI/0.9.3 (personal assistant)', 'Accept':'application/json', **(headers or {})})
    with _OPENER.open(request, timeout=9) as response:
        content = response.read(_MAX_RESPONSE + 1)
        if len(content) > _MAX_RESPONSE:
            raise ValueError('Ответ сервиса слишком большой')
        return json.loads(content.decode('utf-8'))


def is_weather(question):
    return bool(re.search(r'\b(погод[аыуеой]|температур[аыеуой]|weather|temperature|прогноз погод[ыа])\b', question, re.I))


def weather_location(question):
    # Require an explicit place: do NOT infer user's location, city, or GPS.
    q = question.strip().rstrip(' ?!.,')
    for pattern in (r'\b(?:в|во|для|по)\s+([\w\-]+(?:[ -][\w\-]+){0,2})', r'\bin\s+([A-Za-z][A-Za-z -]{1,50})'):
        m = re.search(pattern, q, re.I)
        if m:
            city = re.split(r'\s+(?:сегодня|завтра|сейчас|на сегодня|на завтра|today|tomorrow|now)\b', m.group(1), maxsplit=1, flags=re.I)[0]
            if city.strip().lower() not in {'общем','целом','этом','городе','моем','моём','доме'}:
                return city.strip()[:60]
    return None


def weather(question):
    city = weather_location(question)
    if not city:
        return 'Уточни город, пожалуйста. Например: «Какая погода в Москве?» — я не определяю твоё местоположение автоматически.', []
    locations = fetch_json('https://geocoding-api.open-meteo.com/v1/search', {'name': city, 'count': 1, 'language': 'ru'})
    results = locations.get('results') or []
    if not results:
        return f'Не удалось найти город «{city}». Напиши название ещё раз, желательно с указанием страны.', []
    item = results[0]
    forecast = fetch_json('https://api.open-meteo.com/v1/forecast', {
        'latitude': item['latitude'], 'longitude': item['longitude'],
        'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m',
        'timezone': 'auto', 'forecast_days': 1,
    })
    current = forecast.get('current') or {}
    if current.get('temperature_2m') is None:
        return 'Погодный сервис не вернул температуру. Попробуй чуть позже.', []
    code = current.get('weather_code')
    descriptions = {0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'пасмурно',45:'туман',48:'изморозь',51:'слабая морось',53:'морось',55:'сильная морось',61:'небольшой дождь',63:'дождь',65:'сильный дождь',71:'небольшой снег',73:'снег',75:'сильный снег',80:'ливневый дождь',81:'ливень',82:'сильный ливень',95:'гроза'}
    name = item.get('name', city)
    country = item.get('country', '')
    stamp = current.get('time', 'время неизвестно')
    result = f'**{name}, {country}** — сейчас **{current["temperature_2m"]} °C**, {descriptions.get(code,"условия уточняются")}.\n\n'
    result += f'Ощущается как {current.get("apparent_temperature","?")} °C; влажность {current.get("relative_humidity_2m","?")}%, ветер {current.get("wind_speed_10m","?")} км/ч.\n'
    result += f'\nДанные на {stamp} (местное время города). Источник: [Open-Meteo](https://open-meteo.com/).'
    return result, [{'url':'https://open-meteo.com/', 'title':'Open-Meteo', 'excerpt': str(current), 'kind':'weather'}]


_RESEARCH = re.compile(
    r'^(?:пожалуйста[,\s]+)?(?:изучи|исследуй|проштудируй|проиндексируй|прочитай\s+и\s+запомни)\b'
    r'|^(?:please\s+)?(?:study|research|index|learn\s+from)\s+(?:the\s+)?(?:docs|documentation|site|website|page|source|https?://)', re.I)
_NEWS = re.compile(r'(?:новост|что\s+(?:произошло|случилось|нового)|последние\s+событи|headlines|\bnews\b|what\s+happened)', re.I)
_WEB = re.compile(
    r'(?:найди|поищи|проверь\s+в\s+(?:интернете|сети)|загугли|поиск\s+в\s+интернете|в\s+интернете|в\s+сети|'
    r'последн(?:ий|яя|ее|ие|юю)\s+(?:верси|релиз|курс)|свеж(?:ие|ая|ий)|сегодня|сейчас|на\s+данный\s+момент|актуальн|'
    r'курс\s+(?:доллара|евро|рубля|биткоин)|сколько\s+стоит|цена\s+на|в\s+20[2-9]\d\s+году|'
    r'latest|recent|current(?:ly)?\b|search\s+online|look\s+up|today|right\s+now|price\s+of)', re.I)
_SMALLTALK = re.compile(
    r'^(?:привет\w*|здравствуй\w*|добр(?:ое|ый)\s+\w+|хай|ку|как\s+(?:дела|ты|жизнь|настроение)\w*|что\s+делаешь|спасибо\w*|'
    r'пока|hi|hello|hey|how\s+are\s+you|thanks?|thank\s+you|good\s+(?:morning|evening|night))\b', re.I)
URL_IN_TEXT = re.compile(r'https?://[^\s<>"\'«»]+', re.I)


def route(question, web_enabled=True):
    """Classify a request: chat | local | weather | news | web | research.

    The route only says what the request NEEDS. Whether a search actually
    happens is decided (and honestly reported) by the intelligence engine.
    """
    q = re.sub(r'\s+', ' ', (question or '')).strip()
    low = q.casefold()
    if is_weather(low):
        return 'weather'
    if _RESEARCH.search(low):
        return 'research'
    if _NEWS.search(low):
        return 'news'
    if _SMALLTALK.search(low) and len(q) < 60:
        return 'chat'
    if _WEB.search(low):
        return 'web'
    return 'local'


def search_intent(question):
    """0.9.2 compatible view of route(): 'weather', 'web' or None."""
    kind = route(question)
    if kind == 'weather':
        return 'weather'
    if kind in ('web', 'news'):
        return 'web'
    return None


_NET = {'value': None, 'stamp': 0.0}


def internet_available(ttl=30.0):
    """Cheap connectivity probe (TCP to the search/weather APIs). Cached."""
    import socket
    import time
    if os.environ.get('CHELSY_OFFLINE') == '1':
        return False
    now = time.monotonic()
    if _NET['value'] is not None and now - _NET['stamp'] < ttl:
        return _NET['value']
    ok = False
    for host in ('api.search.brave.com', 'api.open-meteo.com'):
        try:
            with socket.create_connection((host, 443), timeout=2.5):
                ok = True
                break
        except OSError:
            continue
    _NET['value'], _NET['stamp'] = ok, now
    return ok


def brave_news(question):
    """Fresh news with publication dates via Brave News Search API."""
    key = os.environ.get('CHELSY_BRAVE_SEARCH_KEY', '').strip()
    if not key:
        return [], ('Для поиска свежих новостей нужен ключ Brave Search API. На ноутбуке-хосте '
                    'задай CHELSY_BRAVE_SEARCH_KEY; клиентам ключ не передаётся.')
    freshness = 'pd' if re.search(r'(сегодня|today|сейчас|now)', question, re.I) else 'pw'
    response = fetch_json('https://api.search.brave.com/res/v1/news/search', {
        'q': question[:400], 'count': 8, 'safesearch': 'moderate', 'freshness': freshness,
    }, headers={'X-Subscription-Token': key})
    items = []
    for result in (response.get('results') or [])[:8]:
        url = str(result.get('url', ''))
        if not url.startswith('https://'):
            continue
        items.append({'url': url[:700], 'title': str(result.get('title', ''))[:180],
                      'excerpt': re.sub('<[^>]+>', '', str(result.get('description', '')))[:600],
                      'published': str(result.get('page_age') or result.get('age') or '')[:40],
                      'source': str((result.get('meta_url') or {}).get('hostname', ''))[:120],
                      'kind': 'news'})
    return items, '' if items else 'Поиск новостей не вернул результатов.'


def brave_search(question):
    key = os.environ.get('CHELSY_BRAVE_SEARCH_KEY', '').strip()
    if not key:
        return [], 'Для актуального поиска по всему интернету нужен ключ Brave Search API. На ноутбуке задай CHELSY_BRAVE_SEARCH_KEY; ключ не нужно передавать клиентам.'
    response = fetch_json('https://api.search.brave.com/res/v1/web/search', {
        'q': question[:500], 'count': 5, 'safesearch':'moderate'
    }, headers={'X-Subscription-Token':key})
    items=[]
    for result in (response.get('web') or {}).get('results',[])[:5]:
        url = str(result.get('url',''))
        if not url.startswith('https://'): continue
        items.append({'url':url[:700], 'title':str(result.get('title',''))[:180], 'excerpt':re.sub('<[^>]+>','',str(result.get('description','')))[:850],
                      'published': str(result.get('page_age') or result.get('age') or '')[:40], 'kind':'web'})
    return items, '' if items else 'Поиск не вернул подходящих результатов.'


def chat_greeting(question):
    q = re.sub(r'[!?.,\s]+', ' ', question.casefold()).strip()
    greetings = {'привет':'Привет! 💜 Чем помочь?', 'здравствуй':'Привет! 💜 Чем помочь?', 'здравствуйте':'Здравствуйте! Чем могу помочь?', 'как дела':'Всё в порядке, спасибо! 💜 Я готова помочь с вопросами, кодом или поиском информации. А как у тебя дела?', 'как ты':'Всё хорошо! 💜 Готова помочь. Что обсудим?', 'hello':'Hello! 💜 How can I help?', 'hi':'Hi! 💜 What can I help you with?', 'how are you':'I am ready to help! 💜 How are you doing?'}
    return greetings.get(q)
