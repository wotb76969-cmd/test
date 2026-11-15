"""SourceFetcher — safe download of public web documents.

Protections:
* only http/https on ports 80/443, no credentials in URLs;
* every hostname is resolved once and ALL addresses must be globally routable
  (blocks localhost, LAN, link-local, CGNAT/Tailscale, multicast, metadata IPs);
* the TCP connection is pinned to the validated IP (no DNS-rebinding window),
  TLS still verifies the certificate for the original hostname;
* redirects are followed manually (max 4) and every hop is re-validated;
* robots.txt is honoured (RFC 9309) with per-host crawl-delay and a global
  per-minute budget; response size and content types are limited;
* nothing downloaded is ever executed.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = 'ChelsyAIResearchBot/0.9.3 (+private research assistant; respects robots.txt)'
ROBOTS_AGENT = 'ChelsyAIResearchBot'
ALLOWED_TYPES = ('text/html', 'application/xhtml+xml', 'text/plain')
BLOCKED_SUFFIXES = ('.localhost', '.local', '.internal', '.lan', '.home', '.home.arpa', '.corp', '.intranet', '.ts.net')
BINARY_EXT = ('.zip', '.pdf', '.png', '.gif', '.jpg', '.jpeg', '.webp', '.svg', '.css', '.js', '.mp4', '.mp3',
              '.exe', '.msi', '.apk', '.dmg', '.iso', '.tar', '.gz', '.7z', '.rar', '.woff', '.woff2', '.ico')


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    headers: dict = field(default_factory=dict)


def normalize_url(value, base=None):
    """Canonical http(s) URL without fragment/credentials, or None."""
    try:
        joined = urljoin(base, value) if base else value
        url = urldefrag(joined.strip())[0]
        parts = urlsplit(url)
    except (ValueError, TypeError, AttributeError):
        return None
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port not in (None, 80, 443):
        return None
    host = parts.hostname.rstrip('.').lower()
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    netloc = host if port in (None, 80 if parts.scheme == 'http' else 443) else f'{host}:{port}'
    if ':' in host and not host.startswith('['):
        netloc = f'[{host}]'
    path = parts.path or '/'
    if path.lower().endswith(BINARY_EXT):
        return None
    result = urlunsplit((parts.scheme, netloc, path, parts.query, ''))
    return result if len(result) < 900 else None


def ip_is_public(value):
    try:
        ip = ipaddress.ip_address(value.split('%', 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 6 and ip.sixtofour:
        ip = ip.sixtofour
    return bool(ip.is_global and not ip.is_multicast and not ip.is_reserved
                and not ip.is_private and not ip.is_loopback and not ip.is_link_local)


def host_is_blocked(host):
    host = host.rstrip('.').lower()
    if host == 'localhost' or host.endswith(BLOCKED_SUFFIXES):
        return True
    if '.' not in host and ':' not in host:
        return True          # single-label intranet names
    try:
        ipaddress.ip_address(host.strip('[]'))
        return not ip_is_public(host.strip('[]'))
    except ValueError:
        return False


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class SourceFetcher:
    def __init__(self, resolver=None, max_bytes=2_000_000, timeout=12, min_delay=3.0,
                 per_minute=20, user_agent=USER_AGENT, transport=None):
        self.resolver = resolver or socket.getaddrinfo
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.min_delay = min_delay
        self.per_minute = per_minute
        self.user_agent = user_agent
        self.transport = transport          # tests: callable(url, ip, headers) -> (status, headers, body)
        self._robots = {}
        self._last = {}
        self._window = []
        self._lock = threading.Lock()
        self._ssl = ssl.create_default_context()

    # ------------------------------------------------------------- validation
    def check_url(self, url):
        normalized = normalize_url(url)
        if not normalized:
            raise FetchError('Нужен публичный http(s) URL на порту 80/443 без логина и пароля.')
        parts = urlsplit(normalized)
        host = parts.hostname
        if host_is_blocked(host):
            raise FetchError('Адрес ведёт в локальную или частную сеть — запрещено.')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        try:
            infos = self.resolver(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise FetchError('Не удалось определить адрес сайта.') from exc
        ips = sorted({info[4][0] for info in infos})
        if not ips or not all(ip_is_public(ip) for ip in ips):
            raise FetchError('Сайт указывает на частный или служебный IP — запрещено.')
        return normalized, host, port, ips

    # ------------------------------------------------------------- throttling
    def _throttle(self, host, delay):
        while True:
            with self._lock:
                now = time.monotonic()
                self._window = [t for t in self._window if now - t < 60]
                wait_host = self._last.get(host, -1e9) + delay - now
                wait_global = (self._window[0] + 60 - now) if len(self._window) >= self.per_minute else 0
                wait = max(wait_host, wait_global, 0)
                if wait <= 0:
                    self._last[host] = now
                    self._window.append(now)
                    return
            time.sleep(min(wait, 5))

    # ------------------------------------------------------------- transport
    def _raw(self, url, accept):
        normalized, host, port, ips = self.check_url(url)
        parts = urlsplit(normalized)
        path = parts.path + ('?' + parts.query if parts.query else '')
        headers = {'User-Agent': self.user_agent, 'Accept': accept, 'Accept-Encoding': 'identity',
                   'Accept-Language': 'ru,en;q=0.8', 'Host': host}
        if self.transport is not None:
            return (normalized,) + tuple(self.transport(normalized, ips[0], headers))
        if parts.scheme == 'https':
            conn = _PinnedHTTPSConnection(host, ips[0], port, self.timeout, self._ssl)
        else:
            conn = _PinnedHTTPConnection(host, ips[0], port, self.timeout)
        try:
            conn.request('GET', path or '/', headers=headers)
            resp = conn.getresponse()
            body = resp.read(self.max_bytes + 1)
            return normalized, resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchError(f'Сетевая ошибка ({type(exc).__name__}).') from exc
        finally:
            conn.close()

    def _get(self, url, accept='text/html,application/xhtml+xml,text/plain;q=0.8'):
        current = url
        for _ in range(5):
            normalized, status, headers, body = self._raw(current, accept)
            if status in (301, 302, 303, 307, 308) and headers.get('location'):
                nxt = normalize_url(headers['location'], normalized)
                if not nxt:
                    raise FetchError('Небезопасное перенаправление.')
                current = nxt
                continue
            return normalized, status, headers, body
        raise FetchError('Слишком много перенаправлений.')

    # ------------------------------------------------------------- robots
    def robots(self, url):
        """(allowed, crawl_delay) according to robots.txt of the URL's origin."""
        parts = urlsplit(normalize_url(url) or '')
        if not parts.hostname:
            return False, self.min_delay
        origin = f'{parts.scheme}://{parts.netloc}'
        now = time.monotonic()
        with self._lock:
            cached = self._robots.get(origin)
        if not cached or now - cached[1] > 3600:
            parser = RobotFileParser()
            try:
                _, status, _, body = self._get(origin + '/robots.txt', accept='text/plain')
                if status == 200:
                    parser.parse(body[:500_000].decode('utf-8', 'replace').splitlines())
                    state = parser
                elif status in (401, 403):
                    state = 'deny'
                elif 400 <= status < 500:
                    state = 'allow'      # RFC 9309: no robots.txt -> crawling permitted
                else:
                    state = 'deny'       # 5xx: assume complete disallow
            except FetchError:
                state = 'deny'
            cached = (state, now)
            with self._lock:
                self._robots[origin] = cached
        state = cached[0]
        if state == 'allow':
            return True, self.min_delay
        if state == 'deny':
            return False, self.min_delay
        delay = state.crawl_delay(ROBOTS_AGENT) or state.crawl_delay('*') or self.min_delay
        return state.can_fetch(ROBOTS_AGENT, url), max(self.min_delay, min(60.0, float(delay)))

    # ------------------------------------------------------------- public API
    def fetch(self, url, respect_robots=True):
        normalized = normalize_url(url)
        if not normalized:
            raise FetchError('Некорректный или неподдерживаемый URL.')
        delay = self.min_delay
        if respect_robots:
            allowed, delay = self.robots(normalized)
            if not allowed:
                raise FetchError('robots.txt запрещает доступ к этой странице.')
        self._throttle(urlsplit(normalized).hostname, delay)
        final, status, headers, body = self._get(normalized)
        if status != 200:
            raise FetchError(f'Сайт ответил HTTP {status}.')
        if len(body) > self.max_bytes:
            raise FetchError('Страница слишком большая.')
        ctype = headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if ctype not in ALLOWED_TYPES:
            raise FetchError(f'Неподдерживаемый тип содержимого: {ctype or "неизвестно"}.')
        charset = 'utf-8'
        m = re.search(r'charset=([\w\-]+)', headers.get('content-type', ''), re.I)
        if m:
            charset = m.group(1)
        else:
            sniff = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', body[:4096], re.I)
            if sniff:
                charset = sniff.group(1).decode('ascii', 'ignore')
        try:
            text = body.decode(charset, errors='replace')
        except LookupError:
            text = body.decode('utf-8', errors='replace')
        return FetchResult(normalized, final, status, ctype, text, headers)
