import httpx
import asyncio
from playwright.async_api import async_playwright
from typing import Optional, Dict, Any
from urllib.parse import urlparse
import hashlib
import time

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
                'url': str(response.url),  # Final URL after redirects
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
            
            # Navigate with timeout
            response = await page.goto(url, wait_until='networkidle', timeout=crawler_settings.BROWSER_TIMEOUT * 1000)
            
            # Get HTML after JS execution
            html = await page.content()
            title = await page.title()
            
            # Get response info
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
        """
        Hybrid fetch: Try HTTPX first, fallback to Playwright if needed.
        """
        print(f"🌐 Fetching: {url}")
        
        # Try HTTPX first
        result = await self.fetch_httpx(url)
        
        # If HTTPX succeeded with HTML, return it
        if result.get('success') and result.get('html'):
            print(f"✅ HTTPX success: {url}")
            return result
        
        # If HTTPX failed or didn't get HTML (likely JS-heavy), try Playwright
        print(f"🔄 HTTPX failed, falling back to Playwright: {url}")
        result = await self.fetch_playwright(url)
        
        if result.get('success'):
            print(f"✅ Playwright success: {url}")
        else:
            print(f"❌ Both methods failed: {url}")
        
        return result
    
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