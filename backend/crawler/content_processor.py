from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional, Set
import re
from urllib.parse import urlparse
import logging
import json
import hashlib
import trafilatura
from readability import Document

logger = logging.getLogger(__name__)


class ContentProcessor:
    """
    Universal content extraction using trafilatura + readability.
    No more fragile DOM-based UI detection.
    """
    
    # Tags with NO visible content - safe to remove
    NON_CONTENT_TAGS = {
        'script', 'style', 'noscript', 'iframe', 'svg',
        'meta', 'link', 'head', 'template'
    }

    @staticmethod
    def _count_cards(soup: BeautifulSoup) -> int:
        """Count product/item cards OR any generic repeated data containers."""
        # Existing e-commerce selectors
        card_selectors = [
            '[data-product]', '[data-item]', '[data-model]',
            '.product-card', '.item-card', '.pd-item',
            '.product-item', '[class*="product-card"]',
            '[class*="item-card"]', '[class*="grid-item"]'
        ]
        for selector in card_selectors:
            found = soup.select(selector)
            if len(found) > 2:
                logger.debug(f"🃏 Card selector '{selector}' matched {len(found)} elements")
                return len(found)

        # ✅ NEW: Generic repeated container detection
        # Find the most-repeated non-trivial class on block elements
        from collections import Counter
        class_counts = Counter()
        for el in soup.find_all(['div', 'li', 'article', 'section', 'tr'], class_=True):
            for cls in el.get('class', []):
                # Skip generic layout/utility classes
                if cls in {'col', 'row', 'container', 'wrapper', 'flex', 'grid',
                        'active', 'hidden', 'clearfix', 'pull-left', 'pull-right'}:
                    continue
                if len(cls) < 3:
                    continue
                # Only count elements with actual text content
                if len(el.get_text(strip=True)) > 20:
                    class_counts[cls] += 1

        if class_counts:
            top_class, top_count = class_counts.most_common(1)[0]
            if top_count >= 5:
                logger.debug(f"🃏 Generic repeated container detected: '.{top_class}' × {top_count}")
                return top_count

        return 0

    @classmethod
    def _classify_page_type(cls, url: str, soup: BeautifulSoup) -> str:
        path = urlparse(url).path.lower()
        logger.debug(f"🔍 Classifying page type for path: '{path}'")

        listing_url_signals = [
            '/all-', '/explore-', '/shop/', '/category/',
            '/products/', '/collection/', '/search',
            '/smartphones', '/tablets', '/tvs', '/monitors',
            '/appliances', '/watches', '/earbuds',
            # ✅ NEW: generic listing path patterns
            '/pages/', '/list', '/index', '/browse',
            '/directory', '/catalog', '/items', '/entries',
        ]

        detail_url_signals = [
            '/buy', r'-sm-[a-z]\d+', r'/[a-z]+-\d{3,}',
            '/detail', '/product/', '/item/',
            '/p/', '/dp/'
        ]

        for signal in listing_url_signals:
            if signal in path:
                card_count = cls._count_cards(soup)
                logger.debug(f"🔍 Listing URL signal '{signal}' matched — card count: {card_count}")
                if card_count > 2:
                    logger.info(f"📋 Page classified as LISTING (signal='{signal}', cards={card_count})")
                    return 'listing'

        for signal in detail_url_signals:
            if re.search(signal, path, re.I):
                logger.info(f"🔎 Page classified as DETAIL (signal='{signal}')")
                return 'detail'

        # DOM-only card detection (no URL signal needed)
        card_count = cls._count_cards(soup)
        if card_count > 4:
            logger.info(f"📋 Page classified as LISTING via DOM only (cards={card_count})")
            return 'listing'

        logger.info(f"📰 Page classified as ARTICLE (default, no matching signals, cards={card_count})")
        return 'article'
    
    @classmethod
    def _extract_cards(cls, soup: BeautifulSoup, url: str, page_title: str) -> List[Dict]:
        """
        Extract cards from listing pages — works for both e-commerce
        and generic repeated data containers (country lists, team stats, etc.)
        """
        from collections import Counter

        # First try specific e-commerce selectors (existing logic)
        card_selectors = [
            '[data-product]', '[data-item]', '[data-model]',
            '.product-card', '.item-card', '.pd-item',
            '.product-item', '[class*="product-card"]',
            '[class*="item-card"]', 'li.product', 'div.product'
        ]

        card_elements = []
        matched_selector = None
        for selector in card_selectors:
            found = soup.select(selector)
            if len(found) > 2:
                card_elements = found
                matched_selector = selector
                break

        # ✅ NEW: Fall back to most-repeated container class
        if not card_elements:
            class_counts = Counter()
            candidates = {}
            for el in soup.find_all(['div', 'li', 'article', 'section'], class_=True):
                for cls in el.get('class', []):
                    if cls in {'col', 'row', 'container', 'wrapper', 'flex', 'grid',
                            'active', 'hidden', 'clearfix', 'pull-left', 'pull-right'}:
                        continue
                    if len(cls) < 3:
                        continue
                    if len(el.get_text(strip=True)) > 20:
                        class_counts[cls] += 1
                        if cls not in candidates:
                            candidates[cls] = []
                        candidates[cls].append(el)

            if class_counts:
                top_class, top_count = class_counts.most_common(1)[0]
                if top_count >= 5:
                    card_elements = candidates[top_class]
                    matched_selector = f'.{top_class} (auto-detected)'
                    logger.info(f"🃏 Auto-detected card container: '.{top_class}' × {top_count}")

        if not card_elements:
            logger.warning(f"🃏 No card elements found for listing page: {url}")
            return []

        logger.debug(f"🃏 Using selector '{matched_selector}', found {len(card_elements)} raw cards")

        sections = []
        seen_names = set()
        skipped_no_name = 0
        skipped_duplicate = 0

        for i, card in enumerate(card_elements):
            # Try to get a heading/name
            name_el = (
                card.select_one('h1, h2, h3, h4, h5, h6') or
                card.select_one('[class*="title"], [class*="name"], [class*="model"]') or
                card.select_one('a')
            )

            name = name_el.get_text(strip=True) if name_el else ''

            # ✅ NEW: If no name element found, use first meaningful text line
            if not name:
                lines = [l.strip() for l in card.get_text('\n', strip=True).split('\n') if l.strip()]
                name = lines[0] if lines else ''

            if not name:
                skipped_no_name += 1
                continue
            if name.lower() in seen_names:
                skipped_duplicate += 1
                continue
            seen_names.add(name.lower())

            # Get ALL text from the card (not just specific fields)
            # This works generically for countries, NHL teams, films, etc.
            card_text = card.get_text(' ', strip=True)
            
            # Try specific fields if they exist (e-commerce style)
            price_el = card.select_one('[class*="price"], [data-price], .price')
            desc_el = card.select_one('[class*="desc"], [class*="summary"], p')
            link_el = card.select_one('a[href]')

            price = price_el.get_text(strip=True) if price_el else ''
            desc = desc_el.get_text(strip=True) if desc_el else ''
            href = link_el.get('href', '') if link_el else ''

            # Build content: prefer structured if we have named fields,
            # otherwise use full card text (better for data-dense cards)
            if price or (desc and desc != name):
                content_parts = [f"Name: {name}"]
                if price:
                    content_parts.append(f"Price: {price}")
                if desc and desc.lower() != name.lower():
                    content_parts.append(f"Description: {desc}")
                if href:
                    full_url = href if href.startswith('http') else \
                        f"{urlparse(url).scheme}://{urlparse(url).netloc}{href}"
                    content_parts.append(f"URL: {full_url}")
                content = '\n'.join(content_parts)
            else:
                # ✅ Generic: just use all the card's text — works for country/stats cards
                content = card_text
                if href:
                    full_url = href if href.startswith('http') else \
                        f"{urlparse(url).scheme}://{urlparse(url).netloc}{href}"
                    content += f"\nURL: {full_url}"

            logger.debug(f"🃏 Card #{i}: '{name}' ({len(content)} chars)")

            sections.append({
                'heading': name,
                'heading_path': [page_title, name],
                'content': f"[{page_title} > {name}]\n\n{content}",
                'source_url': href or url,
                'chunk_category': 'main_content',
                'page_type': 'card'
            })

        logger.info(
            f"🃏 Card extraction: {len(sections)} kept, "
            f"{skipped_no_name} no-name, {skipped_duplicate} duplicate"
        )
        return sections

    @classmethod
    def _extract_article_content(cls, html: str, url: str, page_title: str) -> List[Dict]:
        """
        Extract content from article, product detail, documentation,
        and homepage pages using Trafilatura + Readability fallback.
        Returns list of section dicts compatible with EnhancedChunker.
        """
        logger.debug(f"📰 Starting article extraction for: {url} (html length: {len(html)} chars)")
        
        # Primary: Trafilatura
        # favor_recall=True keeps more content (better for product pages)
        # include_tables=True preserves spec tables
        content = trafilatura.extract(
            html,
            url=url,
            include_tables=True,
            include_comments=False,
            include_formatting=True,
            favor_recall=True,
            no_fallback=False
        )
        
        if content:
            word_count = len(content.split())
            logger.debug(f"✅ Trafilatura extracted {word_count} words ({len(content)} chars) from {url}")
            if word_count < 50:
                logger.warning(
                    f"⚠️  Trafilatura output too short ({word_count} words < 50 threshold) — "
                    f"content preview: '{content[:200]}'"
                )
        else:
            logger.warning(f"❌ Trafilatura returned None for {url}")
        
        # Fallback: Mozilla Readability via readability-lxml
        if not content or len(content.split()) < 50:
            logger.info(f"🔄 Trying Readability fallback for {url}")
            try:
                doc = Document(html)
                readable_html = doc.summary()
                readable_text = BeautifulSoup(
                    readable_html, 'html.parser'
                ).get_text(separator='\n', strip=True)
                
                readability_words = len(readable_text.split())
                logger.debug(
                    f"📖 Readability extracted {readability_words} words "
                    f"(title: '{doc.title()}') from {url}"
                )
                
                if readability_words >= 10:
                    content = readable_text
                    logger.info(f"✅ Readability fallback succeeded: {readability_words} words for {url}")
                else:
                    logger.warning(
                        f"❌ Readability also too short ({readability_words} words < 50) — "
                        f"preview: '{readable_text[:200]}'"
                    )
            except Exception as e:
                logger.warning(f"❌ Readability fallback raised exception for {url}: {e}")
                content = None
        
        # Nothing extractable — page is genuinely content-light
        if not content or len(content.split()) < 20:
            final_words = len(content.split()) if content else 0
            logger.warning(
                f"🚫 No usable content after all extractors for {url} "
                f"(final word count: {final_words}, threshold: 20). "
                f"Page may be JS-rendered, paywalled, or truly content-light."
            )
            return []
        
        # Clean up whitespace
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        
        total_words = len(content.split())
        logger.debug(f"🧹 Content cleaned: {total_words} words remaining, splitting into sections...")
        
        # Split into sections by double newlines and heading detection
        sections = cls._split_into_sections(content, page_title, url)
        logger.debug(f"✂️  Split into {len(sections)} sections for {url}")
        return sections

    @classmethod
    def _split_into_sections(cls, content: str, page_title: str, url: str) -> List[Dict]:
        """
        Split extracted text into sections using heading detection.
        Trafilatura preserves markdown-style headings (# Heading).
        Falls back to paragraph splitting if no headings found.
        """
        sections = []
        current_heading = page_title
        heading_path = [page_title]
        current_content = []
        seen_hashes = set()
        headings_found = 0
        lines_processed = 0
        lines_skipped_empty = 0
        
        def flush():
            nonlocal current_content
            if not current_content:
                return
            text = '\n\n'.join(current_content).strip()
            word_count = len(text.split())
            if word_count < 10:
                logger.debug(
                    f"✂️  Flush skipped: too short ({word_count} words < 10) "
                    f"under heading '{current_heading}' — content: '{text[:100]}'"
                )
                current_content = []
                return
            h = hashlib.md5(text.encode()).hexdigest()
            if h in seen_hashes:
                logger.debug(f"✂️  Flush skipped: duplicate content under heading '{current_heading}'")
                current_content = []
                return
            seen_hashes.add(h)
            path = list(dict.fromkeys(heading_path))  # dedup preserving order
            sections.append({
                'heading': current_heading,
                'heading_path': path,
                'content': f"[{' > '.join(path)}]\n\n{text}",
                'source_url': url,
                'chunk_category': 'main_content'
            })
            logger.debug(
                f"✂️  Flushed section '{current_heading}': {word_count} words, "
                f"path depth={len(path)}"
            )
            current_content = []
        
        lines = content.split('\n')
        logger.debug(f"✂️  Splitting {len(lines)} lines from content")
        
        for line in lines:
            lines_processed += 1
            stripped = line.strip()
            if not stripped:
                lines_skipped_empty += 1
                continue
            
            # Trafilatura uses markdown headings
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            if heading_match:
                flush()
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip()
                logger.debug(f"✂️  Heading detected (H{level}): '{text}'")
                headings_found += 1
                current_heading = text
                heading_path = heading_path[:level]
                if not heading_path or heading_path[-1] != text:
                    heading_path.append(text)
                continue
            
            current_content.append(stripped)
        
        flush()
        
        logger.debug(
            f"✂️  Line processing complete: {lines_processed} total, "
            f"{lines_skipped_empty} empty, {headings_found} headings found"
        )
        
        # If no sections created (no headings found), split by paragraph blocks
        if not sections and content.strip():
            paragraphs = content.split('\n\n')
            logger.info(
                f"✂️  No sections from heading detection — "
                f"falling back to paragraph splitting ({len(paragraphs)} paragraphs)"
            )
            para_kept = 0
            para_skipped_short = 0
            para_skipped_dupe = 0
            for para in paragraphs:
                para = para.strip()
                word_count = len(para.split())
                if word_count < 10:
                    para_skipped_short += 1
                    logger.debug(f"✂️  Paragraph skipped: too short ({word_count} words) — '{para[:80]}'")
                    continue
                h = hashlib.md5(para.encode()).hexdigest()
                if h in seen_hashes:
                    para_skipped_dupe += 1
                    logger.debug(f"✂️  Paragraph skipped: duplicate")
                    continue
                seen_hashes.add(h)
                sections.append({
                    'heading': page_title,
                    'heading_path': [page_title],
                    'content': f"[{page_title}]\n\n{para}",
                    'source_url': url,
                    'chunk_category': 'main_content'
                })
                para_kept += 1
            
            logger.info(
                f"✂️  Paragraph fallback result: {para_kept} kept, "
                f"{para_skipped_short} too short, {para_skipped_dupe} duplicates"
            )
        
        return sections

    @staticmethod
    def _resolve_title(soup: BeautifulSoup, page_title: Optional[str], source_url: Optional[str]) -> str:
        """Resolve the best available page title from multiple sources."""
        if page_title and page_title.strip():
            logger.debug(f"🏷️  Title resolved from argument: '{page_title.strip()}'")
            return page_title.strip()
        title_tag = soup.find('title')
        if title_tag:
            text = title_tag.get_text(strip=True)
            if text:
                logger.debug(f"🏷️  Title resolved from <title> tag: '{text}'")
                return text
        h1 = soup.find('h1')
        if h1:
            text = h1.get_text(strip=True)
            if text:
                logger.debug(f"🏷️  Title resolved from <h1>: '{text}'")
                return text
        og_title = soup.find('meta', property='og:title')
        if og_title:
            text = og_title.get('content', '').strip()
            if text:
                logger.debug(f"🏷️  Title resolved from og:title meta: '{text}'")
                return text
        logger.warning(f"🏷️  No title found, falling back to URL: '{source_url}'")
        return source_url or 'Untitled Page'

    @staticmethod
    def _extract_metadata(soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """Extract metadata from meta tags."""
        metadata = {
            'url': url,
            'domain': urlparse(url).netloc if url else '',
            'path': urlparse(url).path if url else '',
            'title': None,
            'description': None,
            'og_title': None,
            'og_description': None
        }
        
        title_tag = soup.find('title')
        if title_tag:
            metadata['title'] = title_tag.get_text(strip=True)
        
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            metadata['description'] = meta_desc.get('content', '').strip()
        
        og_title = soup.find('meta', property='og:title')
        if og_title:
            metadata['og_title'] = og_title.get('content', '').strip()
        
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            metadata['og_description'] = og_desc.get('content', '').strip()
        
        return metadata

    @staticmethod
    def _empty_result(page_title: Optional[str], source_url: Optional[str]) -> Dict[str, Any]:
        """Return empty result structure."""
        title = page_title or 'Untitled'
        logger.warning(f"⚠️  Returning empty result for '{source_url}' (title: '{title}')")
        return {
            'page_title': title,
            'source_url': source_url or '',
            'page_type': 'empty',
            'main_content': {
                'all_text': '',
                'sections': [],
                'headings': [],
                'paragraphs': [],
                'lists': [],
                'tables': [],
                'has_content': False
            },
            'ui_summary': [],
            'ui_regions': {},
            'metadata': {'url': source_url or ''},
            'document_structure': {
                'page_title': title,
                'source_url': source_url or '',
                'sections': [],
                'tables': [],
                'cards': [],
                'paragraphs': [],
            },
            'is_first_page': False,
            'has_content': False,
            'text_stats': {
                'total_chars': 0,
                'total_words': 0,
                'section_count': 0,
            }
        }

    @classmethod
    def process_html(
        cls,
        html: str,
        source_url: Optional[str] = None,
        page_title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Universal content extraction.
        Routes to card extraction or article extraction based on page type.
        Compatible with existing EnhancedChunker input format.
        """
        if not html:
            logger.error(f"❌ process_html called with empty HTML for url='{source_url}'")
            return cls._empty_result(page_title, source_url)
        
        html_len = len(html)
        html_words = len(html.split())
        logger.info(f"🌐 process_html START: url='{source_url}' | html={html_len} chars | ~{html_words} tokens")
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Log basic DOM stats before any processing
        all_tags = len(soup.find_all(True))
        div_count = len(soup.find_all('div'))
        p_count = len(soup.find_all('p'))
        h_count = len(soup.find_all(['h1','h2','h3','h4','h5','h6']))
        script_count = len(soup.find_all('script'))
        logger.debug(
            f"🌐 DOM snapshot: {all_tags} total tags | "
            f"{div_count} divs | {p_count} <p> | {h_count} headings | "
            f"{script_count} scripts (will be ignored by extractors)"
        )
        
        # Resolve title
        title = cls._resolve_title(soup, page_title, source_url)
        
        # Classify page type
        page_type = cls._classify_page_type(source_url or '', soup)
        logger.info(f"🏷️  Page: title='{title}' | type='{page_type}' | url='{source_url}'")
        
        sections = []
        
        if page_type == 'listing':
            # Extract product cards as individual sections
            logger.info(f"📋 Running card extraction for listing: {source_url}")
            sections = cls._extract_cards(soup, source_url or '', title)
            
            # If card extraction found nothing, fall back to article extraction
            if not sections:
                logger.warning(
                    f"📋 Listing page but no cards extracted for {source_url} — "
                    f"falling back to article extraction"
                )
                sections = cls._extract_article_content(html, source_url or '', title)
                page_type = 'article'
            else:
                logger.info(f"📋 Extracted {len(sections)} cards from listing page: {source_url}")
        
        else:
            # Article, product detail, documentation, homepage
            logger.info(f"📰 Running article extraction for {page_type}: {source_url}")
            sections = cls._extract_article_content(html, source_url or '', title)
            if sections:
                logger.info(f"📄 Extracted {len(sections)} sections from {page_type} page: {source_url}")
            else:
                logger.warning(f"📄 Zero sections extracted from {page_type} page: {source_url}")
        
        has_content = bool(sections)
        
        # Build all_text from sections
        all_text = '\n\n'.join(
            s['content'].split(']\n\n', 1)[-1] if ']\n\n' in s.get('content', '') else s.get('content', '')
            for s in sections
        )
        
        # Summary log for the whole call
        logger.info(
            f"🌐 process_html END: url='{source_url}' | "
            f"page_type='{page_type}' | sections={len(sections)} | "
            f"has_content={has_content} | all_text={len(all_text)} chars"
        )
        
        if not has_content:
            logger.warning(
                f"🚨 PIPELINE ALERT: No content produced for '{source_url}'. "
                f"Downstream chunker will receive empty sections — 0 chunks will be created. "
                f"HTML was {html_len} chars. Check extractor logs above for root cause."
            )
        
        # Build document_structure in the shape EnhancedChunker expects
        document_structure = {
            'page_title': title,
            'source_url': source_url or '',
            'sections': sections,
            'tables': [],
            'cards': [],
            'paragraphs': [],
        }
        
        metadata = cls._extract_metadata(soup, source_url or '')
        metadata['page_type'] = page_type
        
        return {
            'page_title': title,
            'source_url': source_url or '',
            'page_type': page_type,
            'main_content': {
                'all_text': all_text,
                'sections': sections,
                'headings': [],
                'paragraphs': [],
                'lists': [],
                'tables': [],
                'has_content': has_content
            },
            'ui_summary': [],
            'ui_regions': {},
            'metadata': metadata,
            'document_structure': document_structure,
            'is_first_page': False,
            'has_content': has_content,
            'text_stats': {
                'total_chars': len(all_text),
                'total_words': len(all_text.split()),
                'section_count': len(sections),
            }
        }

    # ============================================================
    # Legacy methods kept for compatibility (no longer used)
    # ============================================================
    
    @staticmethod
    def clear_boilerplate_cache(domain: Optional[str] = None):
        """No-op: boilerplate cache is no longer used."""
        logger.info(f"🧹 Boilerplate cache is no longer used (domain: {domain})")
    
    @staticmethod
    def get_all_text(html: str) -> str:
        """Ultra-simple method: Just get ALL visible text."""
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text
    
    @staticmethod
    def to_markdown(structure: Dict[str, Any]) -> str:
        """Convert extracted content to markdown."""
        lines = []
        
        title = structure.get('page_title', 'Untitled')
        lines.append(f"# {title}\n")
        
        if structure.get('source_url'):
            lines.append(f"**Source:** {structure['source_url']}\n")
        
        for section in structure.get('sections', []):
            heading = section.get('heading', '')
            if heading:
                lines.append(f"## {heading}\n")
            lines.append(section.get('content', ''))
            lines.append('')
        
        if structure.get('all_text') and not structure.get('sections'):
            lines.append("\n")
            lines.append("## Full Content\n")
            lines.append(structure['all_text'])
        
        return '\n\n'.join(filter(None, lines))