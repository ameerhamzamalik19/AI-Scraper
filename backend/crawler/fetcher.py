# fetcher.py
import httpx
import asyncio
import multiprocessing
import queue as _queue
import threading
import time
from playwright.sync_api import sync_playwright
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
import re
import random
import os
import json
from bs4 import BeautifulSoup
from config import crawler_settings
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stealth plugin loading (unchanged)
# ---------------------------------------------------------------------------
STEALTH_AVAILABLE = False
STEALTH_SYNC_FUNC = None

try:
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True
    STEALTH_SYNC_FUNC = stealth_sync
    logger.info("✅ playwright-stealth loaded (stealth_sync)")
except ImportError:
    try:
        from playwright_stealth import Stealth
        STEALTH_AVAILABLE = True
        logger.info("✅ playwright-stealth loaded (Stealth class)")
    except ImportError:
        STEALTH_AVAILABLE = False
        logger.warning("playwright-stealth not installed. Install with: pip install playwright-stealth")

try:
    import brotli
    BROTLI_AVAILABLE = True
except ImportError:
    BROTLI_AVAILABLE = False
    logger.warning("brotli not installed. Install with: pip install brotli")


# ---------------------------------------------------------------------------
# Browser pool configuration
# ---------------------------------------------------------------------------
PLAYWRIGHT_NUM_PROCESSES = int(os.getenv("PLAYWRIGHT_PROCESSES", "0")) or 2
PLAYWRIGHT_CONTEXTS_PER_PROCESS = int(os.getenv("PLAYWRIGHT_CONTEXTS_PER_PROCESS", "4"))
PLAYWRIGHT_PAGES_PER_CONTEXT = int(os.getenv("PLAYWRIGHT_PAGES_PER_CONTEXT", "200"))
PLAYWRIGHT_NAV_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_NAV_TIMEOUT_MS", "20000"))
PLAYWRIGHT_CONTENT_WAIT_MS = int(os.getenv("PLAYWRIGHT_CONTENT_WAIT_MS", "3000"))


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------
def _apply_stealth_to_page(page) -> None:
    if not STEALTH_AVAILABLE:
        return
    try:
        if STEALTH_SYNC_FUNC:
            STEALTH_SYNC_FUNC(page)
            return
        from playwright_stealth import Stealth
        stealth = Stealth()
        if hasattr(stealth, 'apply_stealth_sync'):
            stealth.apply_stealth_sync(page)
        elif hasattr(stealth, 'stealth'):
            stealth.stealth(page)
        elif callable(stealth):
            stealth(page)
    except Exception as e:
        logger.warning(f"⚠️ Failed to apply stealth: {e}")


def _detect_cloudflare_challenge(html: str) -> bool:
    if not html:
        return False
    html_lower = html.lower()
    indicators = [
        'cf-browser-verification', 'challenge-platform', 'turnstile.api',
        'cloudflare-challenge', 'cf-chl-widget', 'cf_captcha', 'captcha-bypass',
        'checking your browser', 'please wait while your request is being verified',
        'verify you are human', 'cf_clearance', 'just a moment', 'cf-ray',
        'data-cf-beacon', 'cf-turnstile',
    ]
    return any(i in html_lower for i in indicators)


def _decode_content(content: bytes, content_type: str = "") -> str:
    if not content:
        return ""
    encoding = None
    if 'charset=' in content_type:
        m = re.search(r'charset=([^\s;]+)', content_type)
        if m:
            encoding = m.group(1).strip('"\'').lower()
    if not encoding:
        try:
            head = content[:2048].decode('latin-1', errors='ignore')
            m = re.search(r'<meta[^>]*charset=["\']?([^"\' >]+)', head, re.IGNORECASE)
            if m:
                encoding = m.group(1).strip('"\'').lower()
        except Exception:
            pass
    if encoding:
        if encoding in ('utf8', 'utf-8'):
            encoding = 'utf-8'
        elif encoding in ('iso-8859-1', 'latin1', 'latin-1'):
            encoding = 'latin-1'
    try:
        if encoding:
            return content.decode(encoding, errors='replace')
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError:
            return content.decode('latin-1', errors='replace')
    except (LookupError, UnicodeDecodeError):
        return content.decode('utf-8', errors='replace')


def _clean_html(html: str) -> str:
    if not html:
        return html
    html = html.replace('\x00', '')
    html = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', html)
    return html


def _get_random_user_agent() -> str:
    return random.choice([
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    ])


_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {} };
"""

_CONTENT_WAIT_JS = """
() => {
    const body = document.body;
    if (!body) return false;
    const text = body.innerText || '';
    if (text.trim().length > 200) return true;
    return body.querySelectorAll('img, iframe, article, main').length > 3;
}
"""


# ---------------------------------------------------------------------------
# Browser process: context pool + page-per-request
# ---------------------------------------------------------------------------
def _make_context(browser):
    ctx = browser.new_context(
        user_agent=crawler_settings.USER_AGENT,
        viewport={'width': 1280, 'height': 1024},
        locale='en-US',
        timezone_id='America/New_York',
        extra_http_headers={
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        },
    )
    return ctx


def _render_with_context(context, url: str) -> Dict[str, Any]:
    """Render one URL using a shared context; page is created and closed per call."""
    page = context.new_page()
    try:
        _apply_stealth_to_page(page)
        page.add_init_script(_INIT_SCRIPT)

        response = page.goto(
            url,
            wait_until='domcontentloaded',
            timeout=PLAYWRIGHT_NAV_TIMEOUT_MS,
        )

        # Best-effort wait for 'load' — don't block forever on stubborn sites
        try:
            page.wait_for_load_state('load', timeout=5000)
        except Exception:
            pass

        # Content-aware wait (replaces fixed 2s + networkidle)
        try:
            page.wait_for_function(_CONTENT_WAIT_JS, timeout=PLAYWRIGHT_CONTENT_WAIT_MS, polling=100)
        except Exception:
            pass

        html = _clean_html(page.content())
        title = page.title()
        status_code = response.status if response else 200
        content_type = response.headers.get('content-type', 'text/html') if response else 'text/html'

        return {
            'success': status_code == 200,
            'html': html,
            'title': title,
            'status_code': status_code,
            'content_type': content_type,
            'response_size': len(html.encode('utf-8')),
            'headers': dict(response.headers) if response else {},
            'url': url,
            'method': 'playwright',
        }
    except Exception as e:
        logger.error(f"❌ Playwright render error for {url}: {e}")
        return {'success': False, 'error': str(e), 'method': 'playwright'}
    finally:
        try:
            page.close()
        except Exception:
            pass


def _playwright_process_entry(request_connection, pool_size: int) -> None:
    """
    Owns one Chromium + a fixed pool of reusable contexts.
    Protocol:
      parent -> child: ("fetch", url, slot)  |  ("stop", None, None)
      child  -> parent: result dict
    """
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-site-isolation-trials',
                    '--window-size=1280,1024',
                ],
            )

            contexts = [_make_context(browser) for _ in range(pool_size)]
            page_counts = [0] * pool_size

            while True:
                try:
                    cmd, url, slot = request_connection.recv()
                except EOFError:
                    break

                if cmd == "stop":
                    break

                # Recycle context if it's been used too many times
                if page_counts[slot] >= PLAYWRIGHT_PAGES_PER_CONTEXT:
                    try:
                        contexts[slot].close()
                    except Exception:
                        pass
                    contexts[slot] = _make_context(browser)
                    page_counts[slot] = 0

                result = _render_with_context(contexts[slot], url)
                page_counts[slot] += 1

                try:
                    request_connection.send(result)
                except (BrokenPipeError, EOFError):
                    break

            for ctx in contexts:
                try:
                    ctx.close()
                except Exception:
                    pass
            browser.close()
    except Exception as e:
        try:
            request_connection.send({'success': False, 'error': str(e), 'method': 'playwright'})
        except (BrokenPipeError, EOFError):
            pass
    finally:
        try:
            request_connection.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class Fetcher:
    """
    Hybrid fetcher: HTTPX → Playwright → BrightData → retry with UA rotation.
    Playwright is backed by a pool of browser processes with reusable contexts.
    """

    FORCE_PLAYWRIGHT_DOMAINS = set()

    LOGIN_WHITELIST = {
        'samsung.com', 'apple.com', 'microsoft.com', 'amazon.com',
        'ebay.com', 'google.com', 'facebook.com', 'twitter.com',
        'linkedin.com', 'github.com', 'nvidia.com',
    }

    def __init__(self):
        self.http_client: Optional[httpx.AsyncClient] = None
        self.use_stealth: bool = getattr(crawler_settings, 'USE_STEALTH', True)
        self.user_agents: List[str] = self._build_user_agent_pool()
        self._site_render_mode: Dict[str, str] = {}
        self._site_blocked: Dict[str, bool] = {}

        # BrightData
        self.brightdata_api_key = os.getenv("BRIGHTDATA_API_KEY", "")
        self.brightdata_zone = os.getenv("BRIGHTDATA_ZONE_NAME", "")
        self.brightdata_enabled = bool(self.brightdata_api_key and self.brightdata_zone)
        self.brightdata_used = set()
        self.brightdata_client: Optional[httpx.AsyncClient] = None

        # --- Playwright pool ---
        self._pw_processes: List[multiprocessing.Process] = []
        self._pw_conns: List[Any] = []          # parent-side Pipe ends
        self._pw_locks: List[threading.Lock] = []  # serialize per-process send/recv
        self._pw_slots: List[int] = []          # number of contexts per process
        self._pw_free: List[int] = []           # free slots per process
        self._pw_cond = threading.Condition()
        self._pw_num_processes = PLAYWRIGHT_NUM_PROCESSES
        self._pw_ctx_per_process = PLAYWRIGHT_CONTEXTS_PER_PROCESS
        self._pw_started = False
        self._pw_shutdown = False

        if self.brightdata_enabled:
            logger.info("✅ BrightData Web Unlocker API configured")
        else:
            logger.warning("⚠️ BrightData not configured (set BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE_NAME)")

    # -- UA pool -----------------------------------------------------------
    def _build_user_agent_pool(self) -> List[str]:
        return list({
            crawler_settings.USER_AGENT,
            _get_random_user_agent(),
            _get_random_user_agent(),
            _get_random_user_agent(),
        })

    # -- HTTPX -------------------------------------------------------------
    async def _get_http_client(self) -> httpx.AsyncClient:
        if self.http_client is None or self.http_client.is_closed:
            user_agent = random.choice(self.user_agents)
            self.http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0),
                headers={
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                follow_redirects=True,
                max_redirects=5,
                http2=True,
            )
        return self.http_client

    async def _get_brightdata_client(self) -> httpx.AsyncClient:
        if self.brightdata_client is None or self.brightdata_client.is_closed:
            self.brightdata_client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.brightdata_api_key}",
                },
            )
        return self.brightdata_client

    async def _rotate_http_client(self) -> None:
        if self.http_client and not self.http_client.is_closed:
            await self.http_client.aclose()
        self.http_client = None
        if self.user_agents:
            self.user_agents = self.user_agents[1:] + [self.user_agents[0]]

    # -- Site mode helpers (unchanged) -------------------------------------
    def _is_blocked_site(self, url: str) -> bool:
        return self._site_blocked.get(self._get_site_key(url), False)

    def _mark_site_blocked(self, url: str) -> None:
        site_key = self._get_site_key(url)
        self._site_blocked[site_key] = True
        logger.info(f"🚫 Site marked as blocked: {site_key}")

    def _should_use_brightdata(self, url: str) -> bool:
        if not self.brightdata_enabled:
            return False
        site_key = self._get_site_key(url)
        if site_key in self.brightdata_used:
            return False
        return self._is_blocked_site(url)

    def _sanitize_string(self, text: str, max_length: int = 100) -> str:
        if not text:
            return ""
        import string
        printable = set(string.printable)
        sanitized = ''.join(c for c in text if c in printable)
        sanitized = re.sub(r'[\x00-\x1f\x7f]', '', sanitized)
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "..."
        return sanitized

    def _get_site_key(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    def _set_site_render_mode(self, url: str, mode: str) -> None:
        if mode not in {"httpx", "playwright", "brightdata"}:
            return
        site_key = self._get_site_key(url)
        self._site_render_mode[site_key] = mode
        logger.info(f"🧭 Site render strategy for {site_key}: {mode}")

    def _should_use_playwright_for_url(self, url: str) -> bool:
        site_key = self._get_site_key(url)
        for domain in self.FORCE_PLAYWRIGHT_DOMAINS:
            if domain in site_key:
                return True
        return self._site_render_mode.get(site_key) == "playwright"

    def _should_use_brightdata_for_url(self, url: str) -> bool:
        return self._site_render_mode.get(self._get_site_key(url)) == "brightdata"

    # -- Detection (unchanged) ---------------------------------------------
    def _detect_login_required(self, html: str, url: str, status_code: int) -> Tuple[bool, str]:
        if not html:
            return False, ""
        MAX_HTML_SIZE = 1_000_000
        if len(html) > MAX_HTML_SIZE:
            html = html[:MAX_HTML_SIZE]
        if status_code in (401, 403):
            return True, f"HTTP {status_code} - Authentication required"

        parsed_url = urlparse(url)
        path_segments = [s for s in parsed_url.path.split('/') if s]
        query = parsed_url.query.lower()

        LOGIN_PATH_INDICATORS = {
            'login', 'signin', 'auth', 'log-in', 'sign-in',
            'authenticate', 'session', 'sso', 'oauth', 'oidc', 'saml',
            'account', 'login?redirect',
        }
        for segment in path_segments:
            segment_lower = segment.lower()
            for indicator in LOGIN_PATH_INDICATORS:
                if indicator in segment_lower:
                    return True, f"Login URL path detected: /{segment}"

        if 'redirect=login' in query or 'returnurl' in query:
            domain = parsed_url.netloc.replace('www.', '')
            if domain not in self.LOGIN_WHITELIST:
                return True, "Login URL query parameter detected"

        domain = parsed_url.netloc.replace('www.', '')
        soup = BeautifulSoup(html, 'html.parser')

        if domain in self.LOGIN_WHITELIST:
            for form in soup.find_all('form'):
                action = form.get('action', '').lower()
                if any(x in action for x in ('region', 'country', 'select', 'search', 'newsletter')):
                    continue
                password_inputs = form.find_all('input', {'type': 'password'})
                if password_inputs:
                    username_inputs = form.find_all(
                        'input',
                        {'type': 'text', 'name': re.compile(r'user|email|login|username', re.I)},
                    )
                    if username_inputs:
                        return True, "Login form detected (username + password fields)"
                    if any('login' in seg or 'auth' in seg for seg in path_segments):
                        return True, "Login form detected on login path"
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
                if any(title.lower().startswith(t) for t in ('login', 'sign in', 'log in', 'authentication')):
                    return True, f"Login page title detected: {self._sanitize_string(title)[:100]}"
            for heading in soup.find_all(['h1', 'h2']):
                text = heading.get_text().strip().lower()
                if any(t in text for t in ('sign in', 'log in', 'login')):
                    if len(heading.get_text(strip=True)) < 100:
                        return True, "Login heading detected"
            return False, ""

        for form in soup.find_all('form'):
            action = form.get('action', '').lower()
            if any(x in action for x in ('search', 'region', 'country', 'newsletter', 'subscribe')):
                continue
            password_inputs = form.find_all('input', {'type': 'password'})
            username_inputs = form.find_all(
                'input',
                {'type': 'text', 'name': re.compile(r'user|email|login|username', re.I)},
            )
            if password_inputs:
                if username_inputs:
                    return True, "Login form detected (username + password fields)"
                for seg in path_segments:
                    if any(x in seg for x in ('login', 'auth', 'reset')):
                        return True, "Password form on authentication path"

        for form in soup.find_all('form'):
            action = form.get('action', '').lower()
            if any(x in action for x in ('login', 'signin', 'auth')) and not any(
                x in action for x in ('search', 'region', 'country')
            ):
                return True, "Login form action detected"

        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
            if any(t in title.lower() for t in ('login', 'sign in', 'log in', 'authentication')):
                return True, f"Login page title: {self._sanitize_string(title)[:100]}"

        for heading in soup.find_all(['h1', 'h2', 'h3']):
            text = heading.get_text().strip().lower()
            if any(t in text for t in ('login', 'sign in', 'log in')):
                if len(heading.get_text(strip=True)) < 100:
                    return True, "Login heading detected"

        html_lower = html.lower()
        if re.search(
            r"access\s+denied|access\s+is\s+denied|you\s+don't\s+have\s+permission"
            r"|not\s+authorized|unauthorized\s+access",
            html_lower,
        ):
            return True, "Access denied message detected"

        for segment in path_segments:
            sl = segment.lower()
            if 'login' in sl or 'signin' in sl or 'auth' in sl:
                return True, f"Login URL path detected: /{segment}"

        return False, ""

    def _detect_site_render_mode_for_html(self, url: str, html: Optional[str]) -> str:
        site_key = self._get_site_key(url)
        if site_key in self._site_render_mode:
            return self._site_render_mode[site_key]

        if html and self._needs_browser_rendering(html, url):
            self._set_site_render_mode(url, "playwright")
            return "playwright"

        soup = BeautifulSoup(html or '', 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        content_len = len((soup.find('body') or soup).get_text(strip=True))

        if content_len > 200:
            self._set_site_render_mode(url, "httpx")
            return "httpx"

        logger.info(f"⚠️ Thin content ({content_len} chars), not caching — trying Playwright: {url}")
        return "playwright"

    def _detect_blocking(self, status_code: int, headers: dict, html: Optional[str]) -> Tuple[bool, str]:
        html_lower = html.lower() if html else ""
        if status_code in (401, 403):
            return True, f"HTTP {status_code} - Access Denied"
        if _detect_cloudflare_challenge(html):
            return True, "Cloudflare challenge detected"
        if re.search(r'<[^>]*class=["\'][^"\']*(?:captcha|recaptcha|g-recaptcha)[^"\']*["\']', html or "", re.IGNORECASE):
            return True, "CAPTCHA detected"
        headers_str = str(headers).lower()
        if 'x-ratelimit-remaining: 0' in headers_str or 'retry-after' in headers_str:
            return True, "Rate limited"
        headers_lower = {k.lower(): v.lower() for k, v in headers.items()}
        if headers_lower.get('x-amzn-waf-action') == 'challenge':
            return True, "Amazon WAF challenge detected"
        if headers_lower.get('x-cache') == 'error from cloudfront':
            return True, "CloudFront error - WAF blocked"
        waf_phrases = [
            'request blocked', 'access denied', 'you have been blocked',
            'ip address blocked', 'suspicious activity', 'automated request',
            'our systems have detected', 'unusual traffic', 'ddos protection',
            'access to this page has been denied',
            'waf', 'challenge', 'please verify you are human',
        ]
        for phrase in waf_phrases:
            if phrase in html_lower:
                return True, f"WAF/block page: '{phrase}'"
        return False, ""

    def _needs_browser_rendering(self, html: Optional[str], url: str = "") -> bool:
        if not html:
            return False
        soup = BeautifulSoup(html, 'html.parser')
        root = soup.find(id=re.compile(r'^(?:__next|__nuxt|root|app)$'))
        if root and not root.get_text(' ', strip=True):
            framework_markers = ('/_next/', '/_nuxt/', '/static/js/', '/assets/js/')
            if any(any(m in (s.get('src') or '') for m in framework_markers) for s in soup.find_all('script', src=True)):
                return True

        for tag in soup(['script', 'style', 'noscript', 'meta', 'link', 'header', 'footer', 'nav']):
            tag.decompose()
        body = soup.find('body')
        if not body:
            return False
        visible_text = body.get_text(' ', strip=True)
        visible_text_len = len(visible_text)

        empty_containers = [
            c for c in body.find_all(['table', 'tbody', 'ul', 'ol'])
            if len(c.get_text(strip=True)) < 20
        ]
        if empty_containers and visible_text_len < 500:
            return True

        original_soup = BeautifulSoup(html, 'html.parser')
        inline_scripts = ' '.join(s.get_text() for s in original_soup.find_all('script', src=False))
        ajax_patterns = [
            r'fetch\s*\(', r'\$\.ajax\s*\(', r'\$\.get\s*\(',
            r'XMLHttpRequest', r'axios\.', r'await\s+fetch',
        ]
        ajax_hits = sum(1 for p in ajax_patterns if re.search(p, inline_scripts))
        if visible_text_len < 1000 and ajax_hits >= 2:
            return True
        return False

    # -- BrightData (unchanged) --------------------------------------------
    async def _fetch_with_brightdata(self, url: str) -> Dict[str, Any]:
        logger.info(f"🔓 Fetching with BrightData Web Unlocker: {url}")
        site_key = self._get_site_key(url)
        self.brightdata_used.add(site_key)
        try:
            client = await self._get_brightdata_client()
            payload = {"zone": self.brightdata_zone, "url": url, "format": "raw", "render": "true"}
            country = os.getenv("BRIGHTDATA_COUNTRY", "")
            if country:
                payload["country"] = country
            response = await client.post("https://api.brightdata.com/request", json=payload)
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                is_html = 'text/html' in content_type or 'application/xhtml+xml' in content_type
                if is_html:
                    html = _clean_html(_decode_content(response.content, content_type))
                    if 'brightdata' in html.lower() and 'error' in html.lower():
                        self._set_site_render_mode(url, "playwright")
                        return {'success': False, 'error': 'BrightData returned error page', 'method': 'brightdata'}
                    self._site_blocked[site_key] = False
                    return {
                        'success': True, 'html': html, 'status_code': 200,
                        'content_type': content_type, 'response_size': len(html.encode('utf-8')),
                        'headers': dict(response.headers), 'url': url, 'method': 'brightdata',
                        'brightdata_used': True,
                    }
                content = response.text
                return {
                    'success': True, 'html': content, 'status_code': 200,
                    'content_type': content_type, 'response_size': len(content.encode('utf-8')),
                    'headers': dict(response.headers), 'url': url, 'method': 'brightdata',
                    'brightdata_used': True,
                }
            logger.error(f"❌ BrightData API error: {response.status_code} - {response.text}")
            self._set_site_render_mode(url, "playwright")
            return {
                'success': False, 'error': f'BrightData API error: {response.status_code}',
                'method': 'brightdata', 'status_code': response.status_code,
            }
        except httpx.TimeoutException:
            return {'success': False, 'error': 'BrightData timeout', 'method': 'brightdata'}
        except Exception as e:
            return {'success': False, 'error': str(e), 'method': 'brightdata'}

    # -- Playwright pool management ----------------------------------------
    def _start_browser_processes(self) -> None:
        if self._pw_started:
            return
        ctx = multiprocessing.get_context('spawn')
        for i in range(self._pw_num_processes):
            parent_conn, child_conn = ctx.Pipe()
            proc = ctx.Process(
                target=_playwright_process_entry,
                args=(child_conn, self._pw_ctx_per_process),
                daemon=True,
            )
            proc.start()
            child_conn.close()
            self._pw_processes.append(proc)
            self._pw_conns.append(parent_conn)
            self._pw_locks.append(threading.Lock())
            self._pw_slots.append(self._pw_ctx_per_process)
            self._pw_free.append(self._pw_ctx_per_process)
        self._pw_started = True
        logger.info(
            f"🚀 Playwright pool started: {self._pw_num_processes} process(es) × "
            f"{self._pw_ctx_per_process} contexts = {self._pw_num_processes * self._pw_ctx_per_process} slots"
        )

    def _acquire_slot(self, timeout: float = 60.0) -> Tuple[int, int]:
        """Block until a slot is free. Returns (process_index, slot_index)."""
        deadline = time.monotonic() + timeout
        with self._pw_cond:
            while True:
                for i, free in enumerate(self._pw_free):
                    if free > 0:
                        self._pw_free[i] -= 1
                        slot = self._pw_free[i]  # slot index used for bookkeeping only
                        return i, self._pw_slots[i] - free - 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No Playwright slot available within timeout")
                self._pw_cond.wait(timeout=min(remaining, 1.0))

    def _release_slot(self, proc_idx: int) -> None:
        with self._pw_cond:
            self._pw_free[proc_idx] += 1
            self._pw_cond.notify()

    def _send_recv(self, proc_idx: int, url: str, slot_idx: int) -> Dict[str, Any]:
        conn = self._pw_conns[proc_idx]
        lock = self._pw_locks[proc_idx]
        with lock:
            try:
                conn.send(("fetch", url, slot_idx))
            except (BrokenPipeError, EOFError, OSError) as e:
                return {'success': False, 'error': f'pipe send failed: {e}', 'method': 'playwright'}

            # poll with generous timeout (nav timeout + slack)
            timeout_s = (PLAYWRIGHT_NAV_TIMEOUT_MS / 1000) + 30
            if not conn.poll(timeout_s):
                return {'success': False, 'error': 'Playwright rendering timed out', 'method': 'playwright'}
            try:
                return conn.recv()
            except (BrokenPipeError, EOFError, OSError) as e:
                return {'success': False, 'error': f'pipe recv failed: {e}', 'method': 'playwright'}

    def _restart_process(self, proc_idx: int) -> None:
        """Kill and restart a dead browser process, rebuilding its pipe."""
        logger.warning(f"♻️ Restarting Playwright process {proc_idx}")
        try:
            proc = self._pw_processes[proc_idx]
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
        except Exception:
            pass
        try:
            self._pw_conns[proc_idx].close()
        except Exception:
            pass

        ctx = multiprocessing.get_context('spawn')
        parent_conn, child_conn = ctx.Pipe()
        proc = ctx.Process(
            target=_playwright_process_entry,
            args=(child_conn, self._pw_ctx_per_process),
            daemon=True,
        )
        proc.start()
        child_conn.close()
        self._pw_processes[proc_idx] = proc
        self._pw_conns[proc_idx] = parent_conn
        self._pw_locks[proc_idx] = threading.Lock()
        with self._pw_cond:
            self._pw_free[proc_idx] = self._pw_slots[proc_idx]

    def _fetch_from_browser_pool(self, url: str) -> Dict[str, Any]:
        self._start_browser_processes()
        proc_idx, slot_idx = self._acquire_slot()
        try:
            result = self._send_recv(proc_idx, url, slot_idx)
            # If the process died, restart and retry once
            if not result.get('success') and not self._pw_processes[proc_idx].is_alive():
                self._restart_process(proc_idx)
                proc_idx, slot_idx = self._acquire_slot()
                result = self._send_recv(proc_idx, url, slot_idx)
            return result
        finally:
            self._release_slot(proc_idx)

    async def fetch_playwright(self, url: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._fetch_from_browser_pool, url)

    # -- HTTPX fetch (unchanged logic, small tweak: content-aware path) ----
    async def _fetch_with_httpx(self, url: str) -> Dict[str, Any]:
        client = await self._get_http_client()
        try:
            response = await client.get(url)
            content_type = response.headers.get('content-type', '').lower()
            is_html = 'text/html' in content_type or 'application/xhtml+xml' in content_type

            html = None
            if is_html:
                raw_content = response.content
                content_encoding = response.headers.get('content-encoding', '').lower()
                if content_encoding == 'br' and BROTLI_AVAILABLE and raw_content:
                    try:
                        if raw_content[:1] == b'\x0b':
                            raw_content = brotli.decompress(raw_content)
                    except Exception as e:
                        logger.debug(f"Brotli decompression failed: {e}")
                html = _clean_html(_decode_content(raw_content, content_type))
                if html and html.count('\ufffd') > len(html) * 0.05:
                    logger.warning(f"High number of replacement characters in {url}")

            is_blocked, block_reason = self._detect_blocking(
                response.status_code, dict(response.headers), html
            )
            if is_blocked:
                logger.warning(f"🚫 Blocked ({block_reason}): {url}")
                self._mark_site_blocked(url)
                return {
                    'success': False, 'error': block_reason, 'is_blocked': True,
                    'requires_login': False, 'status_code': response.status_code,
                    'method': 'httpx', 'html': html,
                }

            if is_html and html:
                requires_login, login_reason = self._detect_login_required(html, url, response.status_code)
                if requires_login:
                    logger.warning(f"🔐 Login required ({login_reason}): {url}")
                    return {
                        'success': False, 'error': login_reason, 'requires_login': True,
                        'is_blocked': False, 'status_code': response.status_code,
                        'method': 'httpx', 'html': html,
                    }

            if response.status_code == 200 and is_html and html:
                render_mode = self._detect_site_render_mode_for_html(url, html)
                if render_mode == "playwright":
                    logger.info(f"🖥️ JS app shell detected for {self._get_site_key(url)}, using Playwright: {url}")
                    return await self.fetch_playwright(url)

            return {
                'success': response.status_code == 200 and is_html,
                'html': html, 'status_code': response.status_code,
                'content_type': content_type, 'response_size': len(response.content),
                'headers': dict(response.headers), 'url': str(response.url),
                'method': 'httpx', 'requires_login': False, 'is_blocked': False,
            }
        except httpx.TimeoutException:
            logger.warning(f"⏱️ HTTPX timeout: {url}")
            return {'success': False, 'error': 'Timeout', 'requires_login': False,
                    'is_blocked': False, 'method': 'httpx'}
        except Exception as e:
            logger.error(f"❌ HTTPX error for {url}: {e}")
            return {'success': False, 'error': str(e), 'requires_login': False,
                    'is_blocked': False, 'method': 'httpx'}

    # -- Main entry point --------------------------------------------------
    async def fetch(self, url: str) -> Dict[str, Any]:
        logger.info(f"🌐 Fetching: {url}")

        if self._should_use_playwright_for_url(url):
            logger.info(f"🎯 Using Playwright (cached/forced) for {self._get_site_key(url)}: {url}")
            return await self.fetch_playwright(url)

        result = await self._fetch_with_httpx(url)
        if result.get('success'):
            return result

        logger.info(f"🔄 HTTPX failed, trying Playwright for: {url}")
        playwright_result = await self.fetch_playwright(url)
        if playwright_result.get('success'):
            self._set_site_render_mode(url, "playwright")
            return playwright_result

        if self.brightdata_enabled:
            site_key = self._get_site_key(url)
            if site_key not in self.brightdata_used:
                logger.info(f"🔓 Playwright failed, trying BrightData for: {url}")
                self._set_site_render_mode(url, "brightdata")
                bd_result = await self._fetch_with_brightdata(url)
                if bd_result.get('success'):
                    return bd_result

        logger.info("🔀 Rotating user agent and retrying HTTPX...")
        await self._rotate_http_client()
        return await self._fetch_with_httpx(url)

    # -- Shutdown ----------------------------------------------------------
    def _stop_browser_processes(self) -> None:
        if not self._pw_started:
            return
        for conn in self._pw_conns:
            try:
                conn.send(("stop", None, None))
            except Exception:
                pass
        for proc in self._pw_processes:
            try:
                proc.join(timeout=10)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=5)
            except Exception:
                pass
        for conn in self._pw_conns:
            try:
                conn.close()
            except Exception:
                pass
        self._pw_processes.clear()
        self._pw_conns.clear()
        self._pw_locks.clear()
        self._pw_slots.clear()
        self._pw_free.clear()
        self._pw_started = False

    async def close(self) -> None:
        if self.http_client and not self.http_client.is_closed:
            await self.http_client.aclose()
            self.http_client = None
        if self.brightdata_client and not self.brightdata_client.is_closed:
            await self.brightdata_client.aclose()
            self.brightdata_client = None
        if self._pw_started:
            await asyncio.to_thread(self._stop_browser_processes)


# ---------------------------------------------------------------------------
# Back-compat shim: some modules may import _playwright_process_entry directly
# ---------------------------------------------------------------------------
def _playwright_process_entry_compat(request_connection) -> None:
    _playwright_process_entry(request_connection, PLAYWRIGHT_CONTEXTS_PER_PROCESS)