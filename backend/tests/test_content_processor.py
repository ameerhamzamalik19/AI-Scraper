from bs4 import BeautifulSoup

from processors.chunker import EnhancedChunker
from processors.content_processor_old import ContentProcessor


def test_content_processor_handles_tags_without_attrs():
    soup = BeautifulSoup('<div><span>hello</span></div>', 'html.parser')
    target = soup.find('span')
    target.attrs = None

    ContentProcessor._strip_unwanted_nodes(soup)

    assert 'hello' in soup.get_text(" ", strip=True)


def test_chunker_handles_structured_paragraph_dicts():
    structure = {
        "page_title": "Example Product",
        "paragraphs": [
            {"text": "We build software for teams that want faster delivery and reduce project risk across critical launches by combining product strategy, engineering systems, and operational excellence in a single delivery model."},
            {"text": "Our flagship product is a customer portal for enterprise operations, analytics, and onboarding workflows, designed to bring together teams, data, and process automation into a single reliable platform."},
        ],
        "sections": [],
        "tables": [],
        "cards": [],
    }

    chunks = EnhancedChunker.chunk_structure(structure)

    assert chunks
    assert all(isinstance(chunk["content"], str) for chunk in chunks)


def test_content_processor_extracts_headings_tables_and_cards():
    html = '''
    <html>
      <head>
        <title>Example Product</title>
        <meta name="description" content="Example site description" />
      </head>
      <body>
        <main>
          <h1 id="page-title">Example Product</h1>
          <section class="content">
            <h2 id="overview">Overview</h2>
            <p>We build software for teams that want faster delivery and reduce project risk across critical launches.</p>
            <div class="card">
              <h3>Projects</h3>
              <p>Our flagship project is a customer portal for enterprise operations, analytics, and onboarding workflows.</p>
            </div>
            <div class="card">
              <h3>Services</h3>
              <p>We provide design, engineering, support, and migration services for product teams and digital operations.</p>
            </div>
          </section>
          <section>
            <h2>Metrics</h2>
            <table>
              <caption>Quarterly results</caption>
              <thead><tr><th>Metric</th><th>Value</th></tr></thead>
              <tbody>
                <tr><td>Projects</td><td>12</td></tr>
                <tr><td>Revenue</td><td>$2.4M</td></tr>
              </tbody>
            </table>
          </section>
        </main>
      </body>
    </html>
    '''

    result = ContentProcessor.process_html(html, source_url='https://example.com', page_title='Example Product')

    assert result['metadata']['title'] == 'Example Product'
    assert len(result['headings']) >= 2
    assert len(result['tables']) >= 1
    assert len(result['cards']) >= 2
    assert result['sections']
