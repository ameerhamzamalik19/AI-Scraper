from crawler.content_processor import ContentProcessor


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