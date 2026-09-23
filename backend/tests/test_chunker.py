from processors.chunker import EnhancedChunker


def test_chunk_is_saved_to_txt(tmp_path):
    original_output_dir = EnhancedChunker.CHUNK_OUTPUT_DIR
    EnhancedChunker.CHUNK_OUTPUT_DIR = str(tmp_path)
    try:
        file_path = EnhancedChunker.save_chunk_to_txt(
            content='A saved chunk.',
            document_id='document-1',
            chunk_id='chunk-1',
            chunk_index=3,
            source_url='https://example.com/products/widget',
        )
    finally:
        EnhancedChunker.CHUNK_OUTPUT_DIR = original_output_dir

    expected_filename = 'chunk_https_example.com_products_widget_chunk-1.txt'
    assert file_path.endswith(f'example.com\\{expected_filename}')
    assert (tmp_path / 'example.com' / expected_filename).read_text(encoding='utf-8') == 'A saved chunk.'


def test_chunker_creates_structure_aware_chunks():
    structure = {
        'title': 'Example Product',
        'source_url': 'https://example.com',
        'page_title': 'Example Product',
        'headings': [
            {'level': 1, 'text': 'Example Product', 'id': 'page-title'},
            {'level': 2, 'text': 'Overview', 'id': 'overview'},
            {'level': 2, 'text': 'Metrics', 'id': 'metrics'},
        ],
        'sections': [
            {
                'id': 'overview',
                'heading': 'Overview',
                'heading_path': ['Example Product', 'Overview'],
                'content': 'We build software for teams that want faster delivery and reduce project risk across critical launches. Our flagship project is a customer portal for enterprise operations, analytics, and onboarding workflows.',
                'type': 'section',
            },
            {
                'id': 'metrics',
                'heading': 'Metrics',
                'heading_path': ['Example Product', 'Metrics'],
                'content': 'Quarterly results show 12 projects and $2.4M in revenue, reflecting strong growth in product adoption and service delivery.',
                'type': 'section',
            },
        ],
        'cards': [
            {
                'title': 'Projects',
                'description': 'Our flagship project is a customer portal for enterprise operations, analytics, and onboarding workflows.',
                'heading_path': ['Example Product', 'Overview'],
            }
        ],
        'tables': [
            {
                'caption': 'Quarterly results',
                'headers': ['Metric', 'Value'],
                'rows': [['Projects', '12'], ['Revenue', '$2.4M']],
                'heading_path': ['Example Product', 'Metrics'],
            }
        ],
        'paragraphs': [
            'We build software for teams that want faster delivery and reduce project risk across critical launches.',
            'Our flagship project is a customer portal for enterprise operations, analytics, and onboarding workflows.',
            'Quarterly results show 12 projects and $2.4M in revenue, reflecting strong growth in product adoption and service delivery.',
        ],
        'metadata': {'title': 'Example Product'}
    }

    chunks = EnhancedChunker().chunk_structure(structure)

    assert chunks
    assert all('content' in chunk for chunk in chunks)
    assert all('heading_path' in chunk for chunk in chunks)
    assert all('chunk_type' in chunk for chunk in chunks)
    assert all('content_structure' in chunk for chunk in chunks)
    assert all(chunk['token_count'] <= 800 for chunk in chunks)
    assert any(chunk['chunk_type'] == 'table' for chunk in chunks)
    assert any(chunk['chunk_type'] in {'section', 'card'} for chunk in chunks)


def test_list_chunks_preserve_item_position():
    structure = {
        'page_title': 'Ranked Blogs',
        'source_url': 'https://example.com/rankings',
        'main_content': {
            'lists': [{
                'type': 'ordered',
                'heading': 'Best Blogs',
                'items': [
                    'Billboard is the first ranked blog with detailed coverage.',
                    'Business Insider is the second ranked blog with business news.',
                ],
            }],
        },
    }

    chunks = EnhancedChunker.chunk_structure(structure)
    item_chunks = [chunk for chunk in chunks if chunk['chunk_type'] == 'list_item']

    assert item_chunks[0]['content'].startswith('List item 1 of 2')
    assert item_chunks[0]['position'] == 1
    assert item_chunks[1]['position'] == 2


def test_country_table_rows_are_queryable_entities():
    chunks = EnhancedChunker.chunk_structure({
        'page_title': 'Countries of the World',
        'source_url': 'https://example.com/countries',
        'main_content': {
            'tables': [{
                'heading': 'Countries',
                'headers': ['Country', 'Capital', 'Population'],
                'row_texts': ['Colombia\nBogota\n50000000', 'Canada\nOttawa\n38000000'],
            }],
        },
    })

    country_chunks = [chunk for chunk in chunks if chunk['entity_type'] == 'country']
    assert len(country_chunks) == 2
    assert 'Colombia' in country_chunks[0]['content']


def test_country_cards_are_queryable_entities():
    chunks = EnhancedChunker.chunk_structure({
        'page_title': 'Countries of the World',
        'source_url': 'https://example.com/countries',
        'main_content': {
            'sections': [{
                'heading': 'Pakistan',
                'heading_path': ['Countries of the World', 'Pakistan'],
                'page_type': 'card',
                'content': '[Countries of the World > Pakistan]\n\n'
                           'Capital: Islamabad\n'
                           'Population: 184404791\n'
                           'Area (km2): 803940.0',
            }],
        },
    })

    country_chunks = [chunk for chunk in chunks if chunk['entity_type'] == 'country']
    assert len(country_chunks) == 1
    assert 'Pakistan' in country_chunks[0]['content']
