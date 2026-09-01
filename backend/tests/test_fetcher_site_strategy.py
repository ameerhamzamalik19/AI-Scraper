from crawler.fetcher import Fetcher


def test_site_playwright_decision_is_reused_for_same_domain():
    fetcher = Fetcher()

    domain = "https://example.com"
    fetcher._set_site_render_mode(domain, "playwright")

    assert fetcher._should_use_playwright_for_url("https://example.com/page-1") is True
    assert fetcher._should_use_playwright_for_url("https://example.com/page-2") is True


def test_js_app_shell_marks_site_for_playwright():
    fetcher = Fetcher()
    html = """
    <html><body>
        <div id="__next"></div>
        <script src="/_next/static/js/app.js"></script>
    </body></html>
    """

    mode = fetcher._detect_site_render_mode_for_html("https://example.com/home", html)

    assert mode == "playwright"
    assert fetcher._should_use_playwright_for_url("https://example.com/about") is True
