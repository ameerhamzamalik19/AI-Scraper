from crawler.content_processor import ContentProcessor
from processors.chunker import EnhancedChunker


def test_listing_detection_preserves_article_body():
    cards = ''.join(
        f'<div class="card"><h3>Related article {index}</h3>'
        f'<p>Additional chess topic {index} and strategy.</p></div>'
        for index in range(1, 6)
    )
    html = f'''
    <html>
      <body>
        <main>
          <article>
            <h1>How to Play Chess</h1>
            <h2>Move the Pieces</h2>
            <p>Each chess piece moves in a different way across the board.</p>
            <p>Players alternate turns and capture opposing pieces.</p>
          </article>
          {cards}
        </main>
      </body>
    </html>
    '''

    result = ContentProcessor.process_html(
        html,
        source_url='https://example.com/chess',
        page_title='How to Play Chess',
    )

    section_text = '\n'.join(
        section['content']
        for section in result['document_structure']['sections']
    )

    assert result['page_type'] == 'listing'
    assert 'Each chess piece moves' in section_text
    assert 'Related article 1' in section_text


def test_json_ld_product_survives_as_queryable_entity_chunk():
    html = '''
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Product","name":"Atlas Pro",
       "description":"A durable analytics platform.","sku":"ATLAS-1",
       "offers":{"price":"99.00","priceCurrency":"USD"}}
      </script>
    </head><body><main><h1>Catalog</h1><p>Products for teams.</p></main></body></html>
    '''

    result = ContentProcessor.process_html(
        html,
        source_url='https://example.com/catalog',
        page_title='Catalog',
    )
    structure = result['document_structure']
    chunks = EnhancedChunker.chunk_structure({
        'page_title': structure['page_title'],
        'source_url': structure['source_url'],
        'main_content': structure,
        'structured_data': structure['structured_data'],
    })

    product_chunks = [chunk for chunk in chunks if chunk['entity_type'] == 'product']
    assert product_chunks
    assert any('Atlas Pro' in chunk['content'] and '99.00' in chunk['content'] for chunk in product_chunks)