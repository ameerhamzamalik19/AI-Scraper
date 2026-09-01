from processors.chunker import EnhancedChunker


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
