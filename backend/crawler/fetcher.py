import httpx
import asyncio
from playwright.async_api import async_playwright
from typing import Optional, Dict, Any
from urllib.parse import urlparse
import hashlib
import time
import re
from config import crawler_settings


class Fetcher:
    """
    Hybrid fetcher: HTTPX first, Playwright fallback for JS-heavy sites.
    """
    
    def __init__(self):
        self.http_client = None
        self.browser = None
        self.playwright = None
        
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTPX client"""
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(
                timeout=crawler_settings.REQUEST_TIMEOUT,
                headers={
                    "User-Agent": crawler_settings.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
                max_redirects=5,
            )
        return self.http_client

    def _detect_login_required(self, html: str, url: str, status_code: int) -> tuple[bool, str]:
        """
        Detect if the page requires login/authentication.
        Returns: (is_login_page, reason)
        Uses precise indicators to minimize false positives.
        """
        if not html:
            return False, None
        
        html_lower = html.lower()
        
        # ============================================================
        # PRECISE LOGIN INDICATORS (only when login is actually required)
        # ============================================================
        
        # 1. Login form with password field (most reliable)
        if re.search(r'<form[^>]*>(?:.*?)<input[^>]*type=["\']password["\']', html, re.IGNORECASE):
            return True, "Login form detected (password field)"
        
        # 2. Login-specific action URL
        if re.search(r'<form[^>]*action=["\'].*?(login|signin|auth).*?["\']', html, re.IGNORECASE):
            return True, "Login form action detected"
        
        # 3. Explicit login page title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).lower()
            if re.search(r'\b(login|sign in|log in|signin|authentication)\b', title):
                return True, f"Login page title: {title}"
        
        # 4. Login heading (h1/h2 with login text)
        if re.search(r'<h[1-3][^>]*>.*?(login|sign in|log in|sign in).*?</h[1-3]>', html, re.IGNORECASE):
            return True, "Login heading detected"
        
        # 5. Access denied messages (only when explicit)
        if re.search(r'(access denied|access is denied|you don\'t have permission|not authorized|unauthorized access)', html_lower):
            return True, "Access denied message detected"
        
        # 6. Login URL pattern
        if re.search(r'(/login|/signin|/auth|/log-in|/sign-in)', url.lower()):
            return True, "Login URL path detected"
        
        return False, None

    def _detect_blocking(self, status_code: int, headers: dict, html: str) -> tuple[bool, str]:
        """
        Detect if the request was blocked (Cloudflare, anti-bot, etc.)
        Returns: (is_blocked, reason)
        Only triggers when actual blocking is detected.
        """
        html_lower = html.lower() if html else ""
        
        # ============================================================
        # PRECISE BLOCKING INDICATORS
        # ============================================================
        
        # 1. HTTP status codes that indicate blocking
        if status_code in [401, 403]:
            return True, f"HTTP {status_code} - Access Denied"
        
        # 2. Cloudflare Challenge (only when actually showing challenge)
        cloudflare_indicators = [
            'cf-browser-verification',
            'challenge-platform',
            'turnstile.apit',
            'cloudflare-challenge',
            'cf-chl-widget',
            'cf_captcha',
            'captcha-bypass',
            'security challenge',
            'checking your browser',
            'please wait while your request is being verified',
            'verify you are human',
            'browser check',
            'cf_clearance',
        ]
        
        for indicator in cloudflare_indicators:
            if indicator in html_lower:
                return True, f"Cloudflare challenge detected: {indicator}"
        
        # 3. Generic CAPTCHA (only when actually present)
        if re.search(r'<[^>]*class=["\'].*?(captcha|recaptcha|g-recaptcha).*?["\']', html, re.IGNORECASE):
            return True, "CAPTCHA detected"
        
        # 4. Rate limiting (only when explicit)
        if 'x-ratelimit' in str(headers).lower():
            return True, "Rate limited"
        
        # 5. WAF/Block pages (only when explicit)
        waf_indicators = [
            'request blocked',
            'access denied',
            'you have been blocked',
            'ip address blocked',
            'suspicious activity',
            'automated request',
            'our systems have detected',
            'unusual traffic',
            'ddos protection',
        ]
        
        for indicator in waf_indicators:
            if indicator in html_lower:
                return True, f"WAF/Block page detected: {indicator}"
        
        return False, None

    async def _get_browser(self):
        """Get Playwright browser instance"""
        if self.playwright is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
        return self.browser
    
    async def fetch_httpx(self, url: str) -> Dict[str, Any]:
        """Fetch using HTTPX"""
        client = await self._get_http_client()
        
        try:
            response = await client.get(url)
            
            # Check if we got HTML
            content_type = response.headers.get('content-type', '').lower()
            is_html = 'text/html' in content_type or 'application/xhtml+xml' in content_type
            
            return {
                'success': response.status_code == 200 and is_html,
                'html': response.text if is_html else None,
                'status_code': response.status_code,
                'content_type': content_type,
                'response_size': len(response.content),
                'headers': dict(response.headers),
                'url': str(response.url),
                'method': 'httpx'
            }
        except httpx.TimeoutException:
            return {
                'success': False,
                'error': 'Timeout',
                'method': 'httpx'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'method': 'httpx'
            }
    
    async def fetch_playwright(self, url: str) -> Dict[str, Any]:
        """Fetch using Playwright (JavaScript rendering)"""
        browser = await self._get_browser()
        context = await browser.new_context(
            user_agent=crawler_settings.USER_AGENT,
            viewport={'width': 1280, 'height': 1024}
        )
        
        try:
            page = await context.new_page()
            
            response = await page.goto(url, wait_until='networkidle', timeout=crawler_settings.BROWSER_TIMEOUT * 1000)
            
            html = await page.content()
            title = await page.title()
            
            status_code = response.status if response else 200
            content_type = response.headers.get('content-type', 'text/html') if response else 'text/html'
            
            await context.close()
            
            return {
                'success': status_code == 200,
                'html': html,
                'title': title,
                'status_code': status_code,
                'content_type': content_type,
                'response_size': len(html.encode('utf-8')),
                'headers': response.headers if response else {},
                'url': url,
                'method': 'playwright'
            }
        except Exception as e:
            await context.close()
            return {
                'success': False,
                'error': str(e),
                'method': 'playwright'
            }
    
    async def fetch(self, url: str) -> Dict[str, Any]:
        """Fetch with login and blocking detection"""
        print(f"🌐 Fetching: {url}")
        
        client = await self._get_http_client()
        
        try:
            response = await client.get(url)
            content_type = response.headers.get('content-type', '').lower()
            is_html = 'text/html' in content_type or 'application/xhtml+xml' in content_type
            html = response.text if is_html else None
            
            # ============================================================
            # CHECK FOR BLOCKING (only when actually blocked)
            # ============================================================
            is_blocked, block_reason = self._detect_blocking(
                response.status_code, 
                response.headers, 
                html
            )
            
            if is_blocked:
                print(f"🚫 Blocked: {block_reason}")
                return {
                    'success': False,
                    'error': block_reason,
                    'is_blocked': True,
                    'requires_login': False,
                    'status_code': response.status_code,
                    'method': 'httpx'
                }
            
            # ============================================================
            # CHECK FOR LOGIN (only when actually login is required)
            # ============================================================
            if is_html and html:
                requires_login, login_reason = self._detect_login_required(html, url, response.status_code)
                if requires_login:
                    print(f"🔐 Login required: {login_reason}")
                    return {
                        'success': False,
                        'error': login_reason,
                        'requires_login': True,
                        'is_blocked': False,
                        'status_code': response.status_code,
                        'method': 'httpx'
                    }
            
            # ============================================================
            # SUCCESS
            # ============================================================
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
                'is_blocked': False
            }
            
        except httpx.TimeoutException:
            return {
                'success': False,
                'error': 'Timeout',
                'requires_login': False,
                'is_blocked': False,
                'method': 'httpx'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'requires_login': False,
                'is_blocked': False,
                'method': 'httpx'
            }
    
    async def close(self):
        """Clean up resources"""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        if self.browser:
            await self.browser.close()
            self.browser = None
        
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None