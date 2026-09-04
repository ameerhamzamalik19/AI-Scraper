import httpx
import asyncio
import multiprocessing
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
import re
import random
from bs4 import BeautifulSoup
from config import crawler_settings
import logging

logger = logging.getLogger(__name__)

# Try to import stealth plugin
STEALTH_AVAILABLE = False
STEALTH_SYNC_FUNC = None

try:
    # Try importing stealth_sync (most common for sync playwright)
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True
    STEALTH_SYNC_FUNC = stealth_sync
    logger.info("✅ playwright-stealth loaded (stealth_sync)")
except ImportError:
    try:
        # Fallback: import Stealth class
        from playwright_stealth import Stealth
        STEALTH_AVAILABLE = True
        logger.info("✅ playwright-stealth loaded (Stealth class)")
        # We'll handle this in the function
    except ImportError:
        STEALTH_AVAILABLE = False
        logger.warning("playwright-stealth not installed. Install with: pip install playwright-stealth")

# Try to import brotli for decompression
try:
    import brotli
    BROTLI_AVAILABLE = True
except ImportError:
    BROTLI_AVAILABLE = False
    logger.warning("brotli not installed. Install with: pip install brotli")


def _apply_stealth_to_page(page) -> None:
    """
    Apply playwright-stealth to a page if available.
    Handles both stealth_sync function and Stealth class.
    """
    if not STEALTH_AVAILABLE:
        return

    try:
        # Try using stealth_sync if available
        if STEALTH_SYNC_FUNC:
            STEALTH_SYNC_FUNC(page)
            logger.debug("✅ Stealth applied via stealth_sync")
            return

        # Fallback: use Stealth class
        from playwright_stealth import Stealth
        stealth = Stealth()
        
        # Try different method names that might exist
        if hasattr(stealth, 'apply_stealth_sync'):
            stealth.apply_stealth_sync(page)
            logger.debug("✅ Stealth applied via Stealth.apply_stealth_sync")
        elif hasattr(stealth, 'stealth'):
            stealth.stealth(page)
            logger.debug("✅ Stealth applied via Stealth.stealth")
        elif callable(stealth):
            stealth(page)
            logger.debug("✅ Stealth applied via Stealth() call")
        else:
            logger.warning("⚠️ Could not find stealth application method")
    except Exception as e:
        logger.warning(f"⚠️ Failed to apply stealth: {e}")


def _detect_cloudflare_challenge(html: str) -> bool:
    """Detect if a Cloudflare challenge page is being shown."""
    if not html:
        return False

    html_lower = html.lower()
    indicators = [
        'cf-browser-verification',
        'challenge-platform',
        'turnstile.api',
        'cloudflare-challenge',
        'cf-chl-widget',
        'cf_captcha',
        'captcha-bypass',
        'checking your browser',
        'please wait while your request is being verified',
        'verify you are human',
        'cf_clearance',
        'just a moment',
        'cf-ray',
        'data-cf-beacon',
        'cf-turnstile',
    ]

    return any(indicator in html_lower for indicator in indicators)


def _decode_content(content: bytes, content_type: str = "") -> str:
    """
    Robust content decoding with multiple fallbacks.
    Returns clean UTF-8 string.
    """
    if not content:
        return ""

    # Step 1: Try to detect encoding from Content-Type header
    encoding = None
    if 'charset=' in content_type:
        match = re.search(r'charset=([^\s;]+)', content_type)
        if match:
            encoding = match.group(1).strip('"\'').lower()

    # Step 2: Try to detect from HTML meta tags
    if not encoding and content:
        try:
            # Decode first 2048 bytes with latin-1 (safe for meta tags)
            head = content[:2048].decode('latin-1', errors='ignore')
            meta_match = re.search(
                r'<meta[^>]*charset=["\']?([^"\' >]+)',
                head,
                re.IGNORECASE
            )
            if meta_match:
                encoding = meta_match.group(1).strip('"\'').lower()
        except Exception:
            pass

    # Step 3: Normalize encoding aliases
    if encoding:
        if encoding in ['utf8', 'utf-8']:
            encoding = 'utf-8'
        elif encoding in ['iso-8859-1', 'latin1', 'latin-1']:
            encoding = 'latin-1'

    # Step 4: Decode with detected encoding or fallback
    try:
        if encoding:
            return content.decode(encoding, errors='replace')
        else:
            # Try UTF-8 first
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                # Fallback to latin-1 (always works)
                return content.decode('latin-1', errors='replace')
    except (LookupError, UnicodeDecodeError):
        # Ultimate fallback
        return content.decode('utf-8', errors='replace')


def _clean_html(html: str) -> str:
    """Clean HTML by removing null bytes and invalid control characters."""
    if not html:
        return html

    # Remove null bytes
    html = html.replace('\x00', '')
    
    # Remove invalid control characters (except newline, tab, carriage return)
    html = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', html)
    
    return html


def _create_response_dict(
    success: bool,
    html: str,
    title: str,
    url: str,
    headers: Optional[Dict] = None,
    status_code: int = 200,
    method: str = 'unknown',
    cloudflare_bypassed: bool = False,
) -> Dict[str, Any]:
    """Helper to create a consistent response dict."""
    return {
        'success': success,
        'html': html,
        'title': title,
        'status_code': status_code,
        'content_type': 'text/html',
        'response_size': len(html.encode('utf-8')) if html else 0,
        'headers': headers or {},
        'url': url,
        'method': method,
        'cloudflare_bypassed': cloudflare_bypassed,
    }


def _get_random_user_agent() -> str:
    """Get a random realistic user agent."""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    ]
    return random.choice(user_agents)


def _render_with_playwright_context(browser, url: str) -> Dict[str, Any]:
    """Render one URL using the browser kept alive by the worker process."""
    context = browser.new_context(
        user_agent=crawler_settings.USER_AGENT,
        viewport={'width': 1280, 'height': 1024},
        locale='en-US',
        timezone_id='America/New_York',
        extra_http_headers={
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        }
    )
    try:
        page = context.new_page()
        
        # Apply playwright-stealth if available
        _apply_stealth_to_page(page)
        
        # Mask automation indicators (additional layer beyond stealth)
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            window.chrome = { runtime: {} };
        """)
        
        response = page.goto(
            url,
            wait_until='networkidle',
            timeout=crawler_settings.BROWSER_TIMEOUT * 1000
        )
        
        # Wait for content to render (generic)
        try:
            page.wait_for_function(
                """() => {
                    return document.body && 
                           document.body.innerText && 
                           document.body.innerText.trim().length > 100;
                }""",
                timeout=15000
            )
            logger.info(f"✅ Content loaded for {url}")
        except Exception:
            logger.warning(f"⚠️ Content wait timeout for {url}, continuing...")
            pass
        
        # Extra safety wait
        page.wait_for_timeout(2000)
        
        html = page.content()
        html = _clean_html(html)
        
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
            'method': 'playwright'
        }
    except Exception as e:
        logger.error(f"❌ Playwright render error for {url}: {e}")
        return {
            'success': False,
            'error': str(e),
            'method': 'playwright'
        }
    finally:
        context.close()


def _playwright_process_entry(request_connection) -> None:
    """Keep one Playwright browser alive and render URLs sent by the parent."""
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
                ]
            )

            while True:
                request = request_connection.recv()
                if request is None:
                    break

                request_connection.send(_render_with_playwright_context(browser, request))

            browser.close()
    except Exception as e:
        try:
            request_connection.send({
                'success': False,
                'error': str(e),
                'method': 'playwright'
            })
        except (BrokenPipeError, EOFError):
            pass
    finally:
        request_connection.close()


class Fetcher:
    """
    Hybrid fetcher: tries fast HTTPX first, falls back to Playwright
    for JS-rendered pages or when blocking is detected.
    """

    def __init__(self):
        self.http_client: Optional[httpx.AsyncClient] = None
        self.browser = None
        self.playwright = None
        self.use_stealth: bool = getattr(crawler_settings, 'USE_STEALTH', True)
        self.user_agents: List[str] = self._build_user_agent_pool()
        self._site_render_mode: Dict[str, str] = {}
        
        # Persistent browser process
        self.browser_process = None
        self.browser_connection = None

    def _build_user_agent_pool(self) -> List[str]:
        """Build a pool of user agents for rotation."""
        return list({
            crawler_settings.USER_AGENT,
            _get_random_user_agent(),
            _get_random_user_agent(),
            _get_random_user_agent(),
        })

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get (or create) the HTTPX async client."""
        if self.http_client is None or self.http_client.is_closed:
            user_agent = random.choice(self.user_agents)
            self.http_client = httpx.AsyncClient(
                timeout=crawler_settings.REQUEST_TIMEOUT,
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

    async def _rotate_http_client(self) -> None:
        """Close the current HTTPX client and create a fresh one."""
        if self.http_client and not self.http_client.is_closed:
            await self.http_client.aclose()
        self.http_client = None
        self.user_agents = self.user_agents[1:] + [self.user_agents[0]]

    def _detect_login_required(self, html: str, url: str, status_code: int) -> tuple[bool, str]:
        """
        Detect if the page requires login/authentication.
        Uses an HTML parser (BeautifulSoup) for structural matching to avoid ReDoS.
        """
        if not html:
            return False, ""
        
        # ============================================================
        # 1. Input size guard (Issue #6)
        # ============================================================
        MAX_HTML_SIZE = 1_000_000  # 1MB
        if len(html) > MAX_HTML_SIZE:
            html = html[:MAX_HTML_SIZE]
        
        # ============================================================
        # 2. Early status code check (Issue #7)
        # ============================================================
        if status_code in (401, 403):
            return True, f"HTTP {status_code} - Authentication required"
        
        # ============================================================
        # 3. Parse URL for path checking (Issue #5)
        # ============================================================
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(url)
        path_segments = [s for s in parsed_url.path.split('/') if s]
        query = parsed_url.query.lower()
        
        LOGIN_PATH_INDICATORS = {
            'login', 'signin', 'auth', 'log-in', 'sign-in',
            'authenticate', 'session', 'sso', 'oauth', 'oidc', 'saml',
            'account', 'login?redirect'
        }
        
        # Check path segments individually
        for segment in path_segments:
            segment_lower = segment.lower()
            for indicator in LOGIN_PATH_INDICATORS:
                if indicator in segment_lower:
                    return True, f"Login URL path detected: /{segment}"
        
        # Check for redirect=login in query params
        if 'redirect=login' in query or 'returnurl' in query or 'returnurl' in query:
            # Additional check - if it's not a whitelisted domain
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.replace('www.', '')
            if domain not in self.LOGIN_WHITELIST:
                return True, "Login URL query parameter detected"
        
        # ============================================================
        # 4. Whitelist check (Issue #1 & #4 - uses BeautifulSoup)
        # ============================================================
        from urllib.parse import urlparse
        from bs4 import BeautifulSoup
        
        domain = urlparse(url).netloc.replace('www.', '')
        LOGIN_WHITELIST = {
            'samsung.com', 'apple.com', 'microsoft.com', 'amazon.com',
            'ebay.com', 'google.com', 'facebook.com', 'twitter.com',
            'linkedin.com', 'github.com', 'nvidia.com'
        }
        
        if domain in LOGIN_WHITELIST:
            # Use BeautifulSoup for structural parsing
            soup = BeautifulSoup(html, 'html.parser')
            
            # Check for login forms
            for form in soup.find_all('form'):
                # Skip forms with region/country/select
                action = form.get('action', '').lower()
                if 'region' in action or 'country' in action or 'select' in action:
                    continue
                if 'search' in action or 'newsletter' in action:
                    continue
                
                # Check for password field
                password_inputs = form.find_all('input', {'type': 'password'})
                if password_inputs:
                    # Check if it's an actual login (has username field too)
                    username_inputs = form.find_all('input', {'type': 'text', 'name': re.compile(r'user|email|login|username', re.I)})
                    if username_inputs:
                        return True, "Login form detected (username + password fields)"
                    # If no username field, it might be a password change form
                    # Check if the form is on a login path
                    if any('login' in seg or 'auth' in seg for seg in path_segments):
                        return True, "Login form detected on login path"
            
            # Check for login title (only if it's the main title)
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
                title_lower = title.lower()
                # Only detect if the title is primarily about login
                if any(title_lower.startswith(t) for t in ['login', 'sign in', 'log in', 'authentication']):
                    # Sanitize the title (Issue #2)
                    sanitized_title = self._sanitize_string(title)
                    return True, f"Login page title detected: {sanitized_title[:100]}"
            
            # Check heading (only h1/h2 that are prominently about login)
            for heading in soup.find_all(['h1', 'h2']):
                heading_text = heading.get_text().strip().lower()
                if any(t in heading_text for t in ['sign in', 'log in', 'login']):
                    # Make sure it's a prominent heading (not buried in a small section)
                    if len(heading.get_text(strip=True)) < 100:
                        return True, "Login heading detected"
            
            return False, ""
        
        # ============================================================
        # 5. Non-whitelisted domains - Full detection with BeautifulSoup
        # ============================================================
        soup = BeautifulSoup(html, 'html.parser')
        
        # Check for login forms using BeautifulSoup (Issue #1, #4)
        for form in soup.find_all('form'):
            action = form.get('action', '').lower()
            
            # Skip clearly non-login forms
            if 'search' in action or 'region' in action or 'country' in action:
                continue
            if 'newsletter' in action or 'subscribe' in action:
                continue
            
            # Check if form has both password and username fields
            password_inputs = form.find_all('input', {'type': 'password'})
            username_inputs = form.find_all('input', {'type': 'text', 'name': re.compile(r'user|email|login|username', re.I)})
            
            if password_inputs:
                if username_inputs:
                    return True, "Login form detected (username + password fields)"
                # Only password field - could be password reset or login
                # Check if it's on a login path
                for seg in path_segments:
                    if 'login' in seg or 'auth' in seg or 'reset' in seg:
                        return True, "Password form on authentication path"
        
        # Check for login form action
        for form in soup.find_all('form'):
            action = form.get('action', '').lower()
            if 'login' in action or 'signin' in action or 'auth' in action:
                # Only if it's not a search/region form
                if not any(x in action for x in ['search', 'region', 'country']):
                    return True, "Login form action detected"
        
        # Check title (Issue #2 - sanitized)
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
            title_lower = title.lower()
            if any(t in title_lower for t in ['login', 'sign in', 'log in', 'authentication']):
                sanitized_title = self._sanitize_string(title)
                return True, f"Login page title: {sanitized_title[:100]}"
        
        # Check heading (Issue #1 - fixed with BeautifulSoup)
        for heading in soup.find_all(['h1', 'h2', 'h3']):
            heading_text = heading.get_text().strip().lower()
            if any(t in heading_text for t in ['login', 'sign in', 'log in']):
                # Only if it's a dedicated login heading (not a small section)
                if len(heading.get_text(strip=True)) < 100:
                    return True, "Login heading detected"
        
        # Check for access denied messages (Issue #3 - consistent approach)
        html_lower = html.lower()
        if re.search(
            r"access\s+denied|access\s+is\s+denied|you\s+don't\s+have\s+permission"
            r"|not\s+authorized|unauthorized\s+access",
            html_lower
        ):
            return True, "Access denied message detected"
        
        # URL path check for non-whitelisted (Issue #5)
        for segment in path_segments:
            segment_lower = segment.lower()
            if 'login' in segment_lower or 'signin' in segment_lower or 'auth' in segment_lower:
                return True, f"Login URL path detected: /{segment}"
        
        return False, ""

    def _sanitize_string(self, text: str, max_length: int = 100) -> str:
        """
        Sanitize a string to prevent log injection and UI injection.
        (Issue #2)
        """
        if not text:
            return ""
        
        # Remove non-printable characters
        import string
        printable = set(string.printable)
        sanitized = ''.join(c for c in text if c in printable)
        
        # Remove control characters
        import re
        sanitized = re.sub(r'[\x00-\x1f\x7f]', '', sanitized)
        
        # Truncate
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "..."
        
        return sanitized

    def _get_site_key(self, url: str) -> str:
        """Return the domain key used to cache render strategy per website."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    def _set_site_render_mode(self, url: str, mode: str) -> None:
        """Persist the chosen fetch strategy for the whole site."""
        if mode not in {"httpx", "playwright"}:
            return
        site_key = self._get_site_key(url)
        self._site_render_mode[site_key] = mode
        logger.info(f"🧭 Site render strategy for {site_key}: {mode}")

    def _should_use_playwright_for_url(self, url: str) -> bool:
        """Check whether the entire site has already been assigned to Playwright."""
        site_key = self._get_site_key(url)
        return self._site_render_mode.get(site_key) == "playwright"

    def _detect_site_render_mode_for_html(self, url: str, html: Optional[str]) -> str:
        """Decide the site's strategy once and cache it based on the first page."""
        site_key = self._get_site_key(url)
        if site_key in self._site_render_mode:
            return self._site_render_mode[site_key]

        mode = "playwright" if html and self._needs_browser_rendering(html) else "httpx"
        self._set_site_render_mode(url, mode)
        return mode

    def _detect_blocking(
        self, status_code: int, headers: dict, html: Optional[str]
    ) -> tuple[bool, str]:
        """Detect if the request was blocked."""
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

        waf_phrases = [
            'request blocked', 'access denied', 'you have been blocked',
            'ip address blocked', 'suspicious activity', 'automated request',
            'our systems have detected', 'unusual traffic', 'ddos protection',
            'access to this page has been denied',
        ]
        for phrase in waf_phrases:
            if phrase in html_lower:
                return True, f"WAF/block page: '{phrase}'"

        return False, ""

    def _needs_browser_rendering(self, html: Optional[str]) -> bool:
        """Return True if the page is an empty JS app shell that needs Playwright."""
        if not html:
            return False

        soup = BeautifulSoup(html, 'html.parser')
        root = soup.find(id=re.compile(r'^(?:__next|__nuxt|root|app)$'))
        if not root:
            return False

        if root.get_text(' ', strip=True):
            return False

        framework_markers = ('/_next/', '/_nuxt/', '/static/js/', '/assets/js/')
        return any(
            any(marker in (script.get('src') or '') for marker in framework_markers)
            for script in soup.find_all('script', src=True)
        )

    def _fetch_from_browser_process(self, url: str) -> Dict[str, Any]:
        """Send a URL to the persistent Playwright browser process."""
        # Start the browser process if it doesn't exist
        if self.browser_process is None or not self.browser_process.is_alive():
            context = multiprocessing.get_context('spawn')
            parent_connection, child_connection = context.Pipe()
            self.browser_process = context.Process(
                target=_playwright_process_entry,
                args=(child_connection,)
            )
            self.browser_process.start()
            child_connection.close()
            self.browser_connection = parent_connection

        try:
            self.browser_connection.send(url)
            if not self.browser_connection.poll(crawler_settings.BROWSER_TIMEOUT + 30):
                return {
                    'success': False,
                    'error': 'Playwright rendering timed out',
                    'method': 'playwright'
                }
            return self.browser_connection.recv()
        except (BrokenPipeError, EOFError, OSError) as e:
            return {
                'success': False,
                'error': str(e),
                'method': 'playwright'
            }

    async def fetch_playwright(self, url: str) -> Dict[str, Any]:
        """Fetch using the persistent Playwright browser process."""
        return await asyncio.to_thread(self._fetch_from_browser_process, url)

    async def _fetch_with_httpx(self, url: str) -> Dict[str, Any]:
        """
        Fetch via HTTPX with proper encoding handling.
        """
        client = await self._get_http_client()

        try:
            response = await client.get(url)
            content_type = response.headers.get('content-type', '').lower()
            is_html = 'text/html' in content_type or 'application/xhtml+xml' in content_type

            html = None
            if is_html:
                raw_content = response.content

                # Handle Brotli compression if present and not already decompressed
                content_encoding = response.headers.get('content-encoding', '').lower()
                if content_encoding == 'br' and BROTLI_AVAILABLE and raw_content:
                    try:
                        # Check if content is still compressed (starts with 0x0b)
                        if raw_content[:1] == b'\x0b':
                            raw_content = brotli.decompress(raw_content)
                            logger.debug(f"Decompressed Brotli content: {len(raw_content)} bytes")
                    except Exception as e:
                        logger.debug(f"Brotli decompression failed: {e}")

                # Decode content
                html = _decode_content(raw_content, content_type)

                # Clean HTML
                html = _clean_html(html)

                # Log if content looks corrupted
                if html and html.count('\ufffd') > len(html) * 0.05:
                    logger.warning(f"High number of replacement characters in {url}")

            # --- Blocking check ---
            is_blocked, block_reason = self._detect_blocking(
                response.status_code, dict(response.headers), html
            )
            if is_blocked:
                logger.warning(f"🚫 Blocked ({block_reason}): {url}")
                return {
                    'success': False,
                    'error': block_reason,
                    'is_blocked': True,
                    'requires_login': False,
                    'status_code': response.status_code,
                    'method': 'httpx',
                }

            # --- Login check ---
            if is_html and html:
                requires_login, login_reason = self._detect_login_required(
                    html, url, response.status_code
                )
                if requires_login:
                    logger.warning(f"🔐 Login required ({login_reason}): {url}")
                    return {
                        'success': False,
                        'error': login_reason,
                        'requires_login': True,
                        'is_blocked': False,
                        'status_code': response.status_code,
                        'method': 'httpx',
                    }

            # --- JS shell check: mark the whole site for Playwright and use it for all later pages ---
            if response.status_code == 200 and is_html and html:
                render_mode = self._detect_site_render_mode_for_html(url, html)
                if render_mode == "playwright":
                    logger.info(f"🖥️ JS app shell detected for site {self._get_site_key(url)}, switching site to Playwright: {url}")
                    return await self.fetch_playwright(url)

            return {
                'success': response.status_code == 200 and is_html,
                'html': html,
                'status_code': response.status_code,
                'content_type': content_type,
                'response_size': len(response.content),
                'headers': dict(response.headers),
                'url': str(response.url),
                'method': 'httpx',
                'requires_login': False,
                'is_blocked': False,
            }

        except httpx.TimeoutException:
            logger.warning(f"⏱️ HTTPX timeout: {url}")
            return {
                'success': False,
                'error': 'Timeout',
                'requires_login': False,
                'is_blocked': False,
                'method': 'httpx',
            }
        except Exception as e:
            logger.error(f"❌ HTTPX error for {url}: {e}")
            return {
                'success': False,
                'error': str(e),
                'requires_login': False,
                'is_blocked': False,
                'method': 'httpx',
            }

    async def fetch(self, url: str) -> Dict[str, Any]:
        """
        Main fetch entry point.
        Strategy: cache one site-level decision, then reuse it for the rest of the domain.
        """
        logger.info(f"🌐 Fetching: {url}")

        if self._should_use_playwright_for_url(url):
            logger.info(f"♻️ Reusing cached Playwright strategy for site {self._get_site_key(url)}: {url}")
            return await self.fetch_playwright(url)

        # Step 1: HTTPX
        result = await self._fetch_with_httpx(url)

        if result.get('success'):
            return result

        # Step 2: Playwright fallback
        if result.get('is_blocked') or result.get('requires_login'):
            self._set_site_render_mode(url, "playwright")
            logger.info("🔄 Falling back to Playwright for the site...")
            playwright_result = await self.fetch_playwright(url)
            if playwright_result.get('success'):
                return playwright_result

        # Step 3: Rotate UA and retry
        if not result.get('success'):
            logger.info("🔀 Rotating user agent and retrying...")
            await self._rotate_http_client()
            result = await self._fetch_with_httpx(url)

        return result

    def _close_browser_process(self):
        """Stop the persistent browser process after the crawl."""
        try:
            if self.browser_connection and self.browser_process and self.browser_process.is_alive():
                self.browser_connection.send(None)
                self.browser_process.join(timeout=10)
                if self.browser_process.is_alive():
                    self.browser_process.terminate()
                    self.browser_process.join()
        finally:
            if self.browser_connection:
                self.browser_connection.close()
            self.browser_connection = None
            self.browser_process = None

    async def close(self) -> None:
        """Release all async resources."""
        if self.http_client and not self.http_client.is_closed:
            await self.http_client.aclose()
            self.http_client = None

        if self.browser:
            await self.browser.close()
            self.browser = None

        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

        # Clean up browser process
        if self.browser_connection:
            await asyncio.to_thread(self._close_browser_process)