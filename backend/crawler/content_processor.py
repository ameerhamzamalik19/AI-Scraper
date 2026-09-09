from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional, Set
import re
from urllib.parse import urlparse
import logging
import json
import hashlib
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class ContentProcessor:
    """
    ONE AND ONLY HTML extraction stage - DOM-based only.
    
    Responsibilities:
    - Remove non-content tags (script, style, iframe)
    - Preserve and parse JSON-LD, SVG text, data-* attributes
    - Extract ALL visible content from DOM (no filtering by length)
    - Preserve structure (headings, sections, tables, lists, cards)
    - No Trafilatura/Readability - pure DOM extraction
    - Populate structured fields (tables, lists, cards, media)
    - Create dedicated chunks for media assets
    
    Does NOT:
    - Filter by length (keep "Price: $99", "Status: Active")
    - Remove content based on heuristics
    - Apply chunking logic
    """
    
    # Tags with NO visible content - safe to remove
    NON_CONTENT_TAGS = {
        'script', 'style', 'noscript', 'iframe',
        'meta', 'link', 'head', 'template'
    }
    # SVG kept for text extraction

    # Debug logging
    DEBUG_ENABLED = True
    DEBUG_LOG_PATH = "/app/debug_content_processing.log"
    
    @classmethod
    def _debug_log(cls, message: str, data: Any = None):
        """Write debug information to a log file."""
        if not cls.DEBUG_ENABLED:
            return
        
        timestamp = datetime.now().isoformat()
        log_entry = f"\n{'='*80}\n[{timestamp}] {message}\n"
        
        if data is not None:
            if isinstance(data, str):
                log_entry += data
            elif isinstance(data, dict) or isinstance(data, list):
                try:
                    log_entry += json.dumps(data, indent=2, default=str)
                except:
                    log_entry += str(data)
            else:
                log_entry += str(data)
        
        log_entry += f"\n{'='*80}\n"
        
        try:
            with open(cls.DEBUG_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"Failed to write debug log: {e}")
        
        logger.debug(log_entry[:500] + "..." if len(log_entry) > 500 else log_entry)

    @staticmethod
    def _is_junk(text: str) -> bool:
        """Check if text is clear junk/boilerplate."""
        if not text:
            return True
        cleaned = text.strip().lower()
        if not cleaned:
            return True
        # Single characters are junk
        if len(cleaned) <= 1:
            return True
        # Text that's just punctuation
        if all(c in '.,;:!?()[]{}"\' \n\t' for c in text):
            return True
        return False

    @staticmethod
    def _section_key(section: Dict[str, Any]) -> str:
        """Return a normalized key for exact section de-duplication."""
        content = section.get('content', '')
        return re.sub(r'\s+', ' ', content).strip().lower()

    @classmethod
    def _merge_sections(cls, *section_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge extracted candidates while preserving the first occurrence."""
        merged = []
        seen = set()
        for sections in section_groups:
            for section in sections or []:
                key = cls._section_key(section)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(section)
        return merged

    @staticmethod
    def _count_cards(soup: BeautifulSoup) -> int:
        """Count product/item cards OR any generic repeated data containers."""
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

        # Generic repeated container detection
        from collections import Counter
        class_counts = Counter()
        for el in soup.find_all(['div', 'li', 'article', 'section', 'tr'], class_=True):
            for cls in el.get('class', []):
                if cls in {'col', 'row', 'container', 'wrapper', 'flex', 'grid',
                        'active', 'hidden', 'clearfix', 'pull-left', 'pull-right'}:
                    continue
                if len(cls) < 3:
                    continue
                if len(el.get_text(strip=True)) > 20:
                    class_counts[cls] += 1

        if class_counts:
            top_class, top_count = class_counts.most_common(1)[0]
            if top_count >= 5:
                logger.debug(f"🃏 Generic repeated container detected: '.{top_class}' × {top_count}'")
                return top_count

        return 0

    @classmethod
    def _classify_page_type(cls, url: str, soup: BeautifulSoup) -> str:
        path = urlparse(url).path.lower()
        logger.debug(f"🔍 Classifying page type for path: '{path}'")

        # Check for wikiHow/how-to pages
        if any(indicator in path for indicator in ['wikihow', 'how-to', 'instructables', 'step']):
            steps = soup.find_all(class_=re.compile(r'step|Step|method|part|section', re.I))
            if len(steps) > 3:
                logger.info(f"📋 Page classified as HOWTO (steps={len(steps)})")
                return 'howto'

        listing_url_signals = [
            '/all-', '/explore-', '/shop/', '/category/',
            '/products/', '/collection/', '/search',
            '/smartphones', '/tablets', '/tvs', '/monitors',
            '/appliances', '/watches', '/earbuds',
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

        card_count = cls._count_cards(soup)
        if card_count > 4:
            logger.info(f"📋 Page classified as LISTING via DOM only (cards={card_count})")
            return 'listing'

        logger.info(f"📰 Page classified as ARTICLE (default, no matching signals, cards={card_count})")
        return 'article'
    
    # ============================================================
    # STRUCTURED DATA EXTRACTION
    # ============================================================
    
    @classmethod
    def _extract_json_ld(cls, soup: BeautifulSoup) -> Optional[str]:
        """Extract and format JSON-LD structured data."""
        json_ld_parts = []
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if data:
                    if isinstance(data, dict):
                        formatted = cls._format_json_ld(data)
                        if formatted:
                            json_ld_parts.append(formatted)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                formatted = cls._format_json_ld(item)
                                if formatted:
                                    json_ld_parts.append(formatted)
            except:
                pass
        return '\n\n'.join(json_ld_parts) if json_ld_parts else None

    @staticmethod
    def _format_json_ld(data: dict) -> str:
        """Format JSON-LD data as readable text."""
        parts = []
        
        type_ = data.get('@type', '')
        if type_:
            parts.append(f"Type: {type_}")
        
        for field in ['name', 'headline', 'title', 'description', 'about', 'abstract']:
            if data.get(field):
                parts.append(f"{field.title()}: {data[field]}")
        
        # Extract properties
        properties = data.get('properties', {})
        if properties:
            parts.append("Properties:")
            for key, value in properties.items():
                parts.append(f"  {key}: {value}")
        
        # Extract offers/price
        offers = data.get('offers', {})
        if offers:
            if isinstance(offers, dict):
                parts.append(f"Price: {offers.get('price', '')} {offers.get('priceCurrency', '')}")
            elif isinstance(offers, list):
                for offer in offers:
                    parts.append(f"Price: {offer.get('price', '')} {offer.get('priceCurrency', '')}")
        
        return '\n'.join(parts) if parts else ''

    @classmethod
    def _extract_svg_text(cls, soup: BeautifulSoup) -> Optional[str]:
        """Extract text from SVG elements."""
        svg_texts = []
        for svg in soup.find_all('svg'):
            # Get all text from SVG
            text = svg.get_text(separator=' ', strip=True)
            if text:
                svg_texts.append(text)
            # Also check for title/desc
            title = svg.find('title')
            if title:
                svg_texts.append(f"SVG Title: {title.get_text(strip=True)}")
            desc = svg.find('desc')
            if desc:
                svg_texts.append(f"SVG Description: {desc.get_text(strip=True)}")
        return '\n'.join(svg_texts) if svg_texts else None


    @classmethod
    def _extract_data_attributes(cls, soup: BeautifulSoup) -> Dict[str, Any]:
        """
        Extract all data attributes from the page.
        """
        data_attrs = {}
        
        try:
            # Fix: Safely handle both tag objects and string tag names
            for element in soup.find_all():
                # Check if element has attributes (it's a Tag, not a string)
                if hasattr(element, 'attrs'):
                    for key, value in element.attrs.items():
                        if key.startswith('data-'):
                            # Store data attributes with their values
                            if key not in data_attrs:
                                data_attrs[key] = []
                            # Store the value, converting to string if needed
                            if isinstance(value, list):
                                data_attrs[key].extend(str(v) for v in value if v is not None)
                            else:
                                data_attrs[key].append(str(value))
        except Exception as e:
            logger.warning(f"Error extracting data attributes: {e}")
        
        return data_attrs

    # ============================================================
    # STRUCTURED FIELD EXTRACTION
    # ============================================================
    
    @classmethod
    def _extract_tables_structured(cls, soup: BeautifulSoup) -> List[Dict]:
        """Extract ALL tables as structured data."""
        tables = []
        for table in soup.find_all('table')[:100]:
            try:
                headers = []
                rows_data = []
                
                thead = table.find('thead')
                if thead:
                    for th in thead.find_all(['th', 'td']):
                        headers.append(th.get_text(strip=True))
                else:
                    first_row = table.find('tr')
                    if first_row:
                        for th in first_row.find_all(['th', 'td']):
                            headers.append(th.get_text(strip=True))
                
                for tr in table.find_all('tr'):
                    cells = []
                    for td in tr.find_all(['td', 'th']):
                        cells.append(td.get_text(strip=True))
                    if cells:
                        rows_data.append(cells)
                
                if headers or rows_data:
                    tables.append({
                        'headers': headers,
                        'rows': rows_data,
                        'row_count': len(rows_data),
                        'col_count': len(headers)
                    })
            except:
                pass
        return tables

    @classmethod
    def _extract_lists_structured(cls, soup: BeautifulSoup) -> List[Dict]:
        """Extract ALL lists as structured data."""
        lists = []
        for list_elem in soup.find_all(['ul', 'ol']):
            try:
                items = []
                for li in list_elem.find_all('li', recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        items.append(text)
                if items:
                    lists.append({
                        'type': 'ordered' if list_elem.name == 'ol' else 'unordered',
                        'items': items,
                        'item_count': len(items)
                    })
            except:
                pass
        return lists

    @classmethod
    def _extract_cards_structured(cls, soup: BeautifulSoup) -> List[Dict]:
        """Extract cards as structured data."""
        cards = []
        card_selectors = [
            '[data-product]', '[data-item]', '[data-model]',
            '.product-card', '.item-card', '.pd-item',
            '.product-item', '[class*="product-card"]',
            '[class*="item-card"]', '[class*="grid-item"]'
        ]
        
        card_elements = []
        for selector in card_selectors:
            found = soup.select(selector)
            if len(found) > 2:
                card_elements = found
                break
        
        for card in card_elements[:100]:
            try:
                name_el = card.select_one('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="name"]')
                name = name_el.get_text(strip=True) if name_el else ''
                
                desc_el = card.select_one('[class*="desc"], [class*="summary"], p')
                desc = desc_el.get_text(strip=True) if desc_el else ''
                
                price_el = card.select_one('[class*="price"], [data-price]')
                price = price_el.get_text(strip=True) if price_el else ''
                
                if name or desc:
                    cards.append({
                        'name': name,
                        'description': desc,
                        'price': price,
                        'text': card.get_text(separator=' ', strip=True)
                    })
            except:
                pass
        return cards

    # ============================================================
    # MEDIA CHUNK CREATION
    # ============================================================
    
    @classmethod
    def _create_media_chunks(cls, media_assets: List[Dict], page_title: str, url: str) -> List[Dict]:
        """Create dedicated chunks for media assets."""
        chunks = []
        for i, asset in enumerate(media_assets):
            if asset.get('media_type') == 'image' and asset.get('description'):
                content = f"[Image: {asset.get('source_url', '')}]\n"
                content += f"Description: {asset.get('description', '')}\n"
                if asset.get('alt_text'):
                    content += f"Alt Text: {asset.get('alt_text', '')}\n"
                if asset.get('caption'):
                    content += f"Caption: {asset.get('caption', '')}\n"
                if asset.get('section_heading'):
                    content += f"Section: {asset.get('section_heading', '')}"
                
                chunks.append({
                    'heading': f"Image {i+1}",
                    'heading_path': [page_title, f"Image {i+1}"],
                    'content': content,
                    'source_url': url,
                    'chunk_category': 'media',
                    'chunk_type': 'image',
                    'entity_type': 'image'
                })
            
            elif asset.get('media_type') == 'table' and asset.get('description'):
                content = f"[Table: {asset.get('source_url', '')}]\n"
                content += f"Description: {asset.get('description', '')}\n"
                if asset.get('table_headers'):
                    content += f"Headers: {', '.join(asset.get('table_headers', []))}\n"
                if asset.get('table_rows'):
                    content += "Data:\n"
                    for row in asset.get('table_rows', [])[:20]:
                        content += f"  {', '.join(row)}\n"
                
                chunks.append({
                    'heading': f"Table {i+1}",
                    'heading_path': [page_title, f"Table {i+1}"],
                    'content': content,
                    'source_url': url,
                    'chunk_category': 'media',
                    'chunk_type': 'table',
                    'entity_type': 'table'
                })
        
        return chunks

    # ============================================================
    # CARD EXTRACTION
    # ============================================================
    
    @classmethod
    def _extract_cards(cls, soup: BeautifulSoup, url: str, page_title: str) -> List[Dict]:
        """
        Extract cards from listing pages.
        PRESERVES ALL cards - even without names.
        """
        cls._debug_log(f"🃏 EXTRACT_CARDS START", {
            'url': url,
            'page_title': page_title
        })
        
        from collections import Counter

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

        if not card_elements:
            class_counts = Counter()
            candidates = {}
            for el in soup.find_all(['div', 'li', 'article', 'section'], class_=True):
                for class_name in el.get('class', []):
                    if class_name in {'col', 'row', 'container', 'wrapper', 'flex', 'grid',
                            'active', 'hidden', 'clearfix', 'pull-left', 'pull-right'}:
                        continue
                    if len(class_name) < 3:
                        continue
                    if len(el.get_text(strip=True)) > 20:
                        class_counts[class_name] += 1
                        if class_name not in candidates:
                            candidates[class_name] = []
                        candidates[class_name].append(el)

            if class_counts:
                top_class, top_count = class_counts.most_common(1)[0]
                if top_count >= 5:
                    card_elements = candidates[top_class]
                    matched_selector = f'.{top_class} (auto-detected)'
                    logger.info(f"🃏 Auto-detected card container: '.{top_class}' × {top_count}'")

        if not card_elements:
            logger.warning(f"🃏 No card elements found for listing page: {url}")
            return []

        sections = []
        seen_names = set()
        total_kept = 0
        skipped_junk = 0

        for i, card in enumerate(card_elements):
            # Try to get a name, but don't skip cards without one
            name_el = (
                card.select_one('h1, h2, h3, h4, h5, h6') or
                card.select_one('[class*="title"], [class*="name"], [class*="model"]') or
                card.select_one('a')
            )

            name = name_el.get_text(strip=True) if name_el else ''

            # If no name, use first line of text or "Card {i}"
            if not name:
                lines = [l.strip() for l in card.get_text('\n', strip=True).split('\n') if l.strip()]
                name = lines[0] if lines else f"Card {i+1}"

            # Handle duplicates with variants
            base_name = name
            counter = 1
            while name.lower() in seen_names:
                name = f"{base_name} {counter}"
                counter += 1
            seen_names.add(name.lower())

            card_text = card.get_text(' ', strip=True)
            
            price_el = card.select_one('[class*="price"], [data-price], .price')
            desc_el = card.select_one('[class*="desc"], [class*="summary"], p')
            link_el = card.select_one('a[href]')

            price = price_el.get_text(strip=True) if price_el else ''
            desc = desc_el.get_text(strip=True) if desc_el else ''
            href = link_el.get('href', '') if link_el else ''

            # Build content - preserve ALL available information
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
                # Add any additional text that wasn't captured
                card_text_without_fields = card_text
                for part in content_parts[1:]:
                    card_text_without_fields = card_text_without_fields.replace(part.split(':', 1)[-1].strip(), '').strip()
                if card_text_without_fields and len(card_text_without_fields) > 10:
                    content_parts.append(f"Details: {card_text_without_fields}")
                content = '\n'.join(content_parts)
            else:
                # Use ALL card text
                content = card_text
                if href:
                    full_url = href if href.startswith('http') else \
                        f"{urlparse(url).scheme}://{urlparse(url).netloc}{href}"
                    content += f"\nURL: {full_url}"

            # Only skip if clearly junk
            if cls._is_junk(content):
                skipped_junk += 1
                continue

            sections.append({
                'heading': name,
                'heading_path': [page_title, name],
                'content': f"[{page_title} > {name}]\n\n{content}",
                'source_url': href or url,
                'chunk_category': 'main_content',
                'page_type': 'card'
            })
            total_kept += 1

        cls._debug_log(f"🃏 EXTRACT_CARDS RESULT", {
            'total_cards': len(card_elements),
            'kept': total_kept,
            'skipped_junk': skipped_junk
        })
        
        logger.info(f"🃏 Card extraction: {total_kept} cards kept, {skipped_junk} junk skipped")
        return sections

    # ============================================================
    # HOW-TO CONTENT EXTRACTION
    # ============================================================
    
    @classmethod
    def _extract_howto_content(cls, soup: BeautifulSoup, url: str, page_title: str) -> List[Dict]:
        """
        Extract structured how-to content (wikiHow, Instructables, etc.)
        Preserves step-by-step instructions with headings.
        """
        cls._debug_log(f"📋 EXTRACT_HOWTO START", {
            'url': url,
            'page_title': page_title
        })
        
        sections = []
        
        # Find the main content container - wikiHow specific
        main_content = (
            soup.find('div', {'id': 'main-content'}) or
            soup.find('div', {'class': 'main-content'}) or
            soup.find('div', {'id': 'article-body'}) or
            soup.find('div', {'class': 'article-body'}) or
            soup.find('article') or
            soup.find('main') or
            soup
        )
        
        # For wikiHow, find the "Steps" section specifically
        steps_section = main_content.find('div', {'class': 'steps'})
        if steps_section:
            main_content = steps_section
        
        # Find all parts (Part 1, Part 2, etc.)
        part_headers = main_content.find_all(['h2', 'h3'], class_=re.compile(r'part|step|Part|Step', re.I))
        
        if not part_headers:
            # Fallback: any h2/h3 in the main content
            part_headers = main_content.find_all(['h2', 'h3'])
        
        if part_headers:
            for heading in part_headers:
                section_title = heading.get_text(strip=True)
                if not section_title:
                    continue
                
                # Get content until the next part heading
                content_parts = []
                next_elem = heading.find_next_sibling()
                while next_elem and next_elem.name not in ['h2', 'h3']:
                    if next_elem.name == 'p':
                        text = next_elem.get_text(strip=True)
                        if text and len(text) > 5:
                            content_parts.append(text)
                    elif next_elem.name in ['ul', 'ol']:
                        items = []
                        for li in next_elem.find_all('li', recursive=False):
                            li_text = li.get_text(strip=True)
                            if li_text:
                                items.append(f"• {li_text}")
                        if items:
                            content_parts.append('\n'.join(items))
                    elif next_elem.name == 'div' and next_elem.find_all(['p', 'li']):
                        text = next_elem.get_text(separator='\n', strip=True)
                        if text:
                            content_parts.append(text)
                    elif next_elem.name == 'table':
                        table_text = cls._extract_table_text(next_elem)
                        if table_text:
                            content_parts.append(table_text)
                    next_elem = next_elem.find_next_sibling()
                
                if content_parts:
                    sections.append({
                        'heading': section_title,
                        'heading_path': [page_title, section_title],
                        'content': f"[{page_title} > {section_title}]\n\n" + '\n\n'.join(content_parts),
                        'source_url': url,
                        'chunk_category': 'main_content',
                        'chunk_type': 'section'
                    })
        
        # If no sections found, fall back to full DOM extraction
        if not sections:
            sections = cls._extract_full_dom(soup, url, page_title)
        
        cls._debug_log(f"📋 EXTRACT_HOWTO RESULT", {
            'sections_count': len(sections),
            'sample': sections[0] if sections else None
        })
        
        return sections

    # ============================================================
    # FULL DOM EXTRACTION WITH DEDUPLICATION
    # ============================================================
    
    @classmethod
    def _extract_full_dom(cls, soup: BeautifulSoup, url: str, page_title: str) -> List[Dict]:
        """Extract ALL content from DOM without duplication."""
        sections = []
        current_heading = page_title
        heading_path = [page_title]
        current_content = []
        seen_hashes = set()
        headings_found = 0
        
        # Track processed elements to avoid duplication
        processed_elements = set()
        
        def get_element_id(el) -> str:
            """Create a unique ID for an element to track processing."""
            if el.get('id'):
                return f"id:{el['id']}"
            return str(id(el))
        
        def flush():
            nonlocal current_content
            if not current_content:
                return
            text = '\n\n'.join(current_content).strip()
            if not text or cls._is_junk(text):
                current_content = []
                return
            h = hashlib.md5(text.encode()).hexdigest()
            if h in seen_hashes:
                current_content = []
                return
            seen_hashes.add(h)
            path = list(dict.fromkeys(heading_path))
            sections.append({
                'heading': current_heading,
                'heading_path': path,
                'content': f"[{' > '.join(path)}]\n\n{text}",
                'source_url': url,
                'chunk_category': 'main_content'
            })
            current_content = []
        
        # Process only top-level content elements
        for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'table', 'div', 'section']):
            # Skip if this element is inside a parent that will be processed
            parent = element.parent
            skip = False
            while parent:
                if parent.name in ['div', 'section', 'article', 'main'] and parent.get('id'):
                    # If parent has an ID and will be processed separately, skip child
                    skip = True
                    break
                if parent.name in ['div', 'section', 'article', 'main']:
                    parent_text = parent.get_text(strip=True)
                    if len(parent_text) > 500 and len(parent.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])) > 0:
                        skip = True
                        break
                parent = parent.parent
            
            if skip:
                continue
            
            if element.find_parent(['nav', 'header', 'footer', 'aside']):
                continue
            
            el_id = get_element_id(element)
            if el_id in processed_elements:
                continue
            processed_elements.add(el_id)
            
            if element.name.startswith('h'):
                flush()
                heading_text = element.get_text(strip=True)
                if heading_text:
                    headings_found += 1
                    current_heading = heading_text
                    level = int(element.name[1])
                    heading_path = heading_path[:level - 1] + [current_heading]
            else:
                text = element.get_text(separator='\n', strip=True)
                if text and len(text) > 5:
                    current_content.append(text)
        
        flush()
        return sections

    @staticmethod
    def _extract_table_text(table) -> Optional[str]:
        """Extract table as structured text."""
        try:
            parts = []
            headers = []
            
            thead = table.find('thead')
            if thead:
                for th in thead.find_all(['th', 'td']):
                    headers.append(th.get_text(strip=True))
            else:
                first_row = table.find('tr')
                if first_row:
                    for th in first_row.find_all(['th', 'td']):
                        headers.append(th.get_text(strip=True))
            
            if headers:
                parts.append("Headers: " + ", ".join(headers))
            
            rows = table.find_all('tr')
            for row in rows:
                cells = []
                for td in row.find_all(['td', 'th']):
                    cells.append(td.get_text(strip=True))
                if cells:
                    parts.append(", ".join(cells))
            
            return "\n".join(parts) if parts else None
        except:
            return None

    @staticmethod
    def _resolve_title(soup: BeautifulSoup, page_title: Optional[str], source_url: Optional[str]) -> str:
        """Resolve the best available page title from multiple sources."""
        if page_title and page_title.strip():
            return page_title.strip()
        title_tag = soup.find('title')
        if title_tag:
            text = title_tag.get_text(strip=True)
            if text:
                return text
        h1 = soup.find('h1')
        if h1:
            text = h1.get_text(strip=True)
            if text:
                return text
        og_title = soup.find('meta', property='og:title')
        if og_title:
            text = og_title.get('content', '').strip()
            if text:
                return text
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

    # ============================================================
    # MAIN PROCESSING ENTRY POINT
    # ============================================================
    
    @classmethod
    def process_html(
        cls,
        html: str,
        source_url: Optional[str] = None,
        page_title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Universal content extraction using pure DOM extraction.
        No Trafilatura/Readability - extracts ALL visible content.
        """
        # Clear debug log on each run
        try:
            with open(cls.DEBUG_LOG_PATH, 'w', encoding='utf-8') as f:
                f.write(f"=== CONTENT PROCESSING DEBUG LOG ===\n")
                f.write(f"Started: {datetime.now().isoformat()}\n")
                f.write(f"URL: {source_url}\n")
                f.write(f"HTML Length: {len(html)}\n")
                f.write(f"{'='*80}\n\n")
        except:
            pass
        
        cls._debug_log(f"🌐 PROCESS_HTML START", {
            'source_url': source_url,
            'page_title': page_title,
            'html_length': len(html),
            'html_preview': html[:1000] + "..." if len(html) > 1000 else html
        })
        
        if not html:
            logger.error(f"❌ process_html called with empty HTML for url='{source_url}'")
            return cls._empty_result(page_title, source_url)
        
        html_len = len(html)
        logger.info(f"🌐 process_html START: url='{source_url}' | html={html_len} chars")
        
        # STEP 1: Parse HTML
        soup = BeautifulSoup(html, 'html.parser')
        cls._debug_log(f"📄 HTML PARSED", {
            'total_elements': len(soup.find_all(True)),
            'div_count': len(soup.find_all('div')),
            'p_count': len(soup.find_all('p')),
            'heading_count': len(soup.find_all(['h1','h2','h3','h4','h5','h6']))
        })
        
        # STEP 2: Extract structured data BEFORE removing tags
        json_ld_text = cls._extract_json_ld(soup)
        svg_text = cls._extract_svg_text(soup)
        data_attrs = cls._extract_data_attributes(soup)
        
        structured_text_parts = []
        if json_ld_text:
            structured_text_parts.append(f"Structured Data (JSON-LD):\n{json_ld_text}")
        if svg_text:
            structured_text_parts.append(f"SVG Content:\n{svg_text}")
        if data_attrs:
            structured_text_parts.append(f"Data Attributes:\n{data_attrs}")
        structured_text = '\n\n'.join(structured_text_parts) if structured_text_parts else ''
        
        # STEP 3: Remove non-content tags
        for tag in soup(['script', 'style', 'noscript', 'iframe']):
            tag.decompose()
        # SVG is NOT removed - we already extracted text and keep for visual context
        
        cls._debug_log(f"🗑️ NON-CONTENT TAGS REMOVED", {
            'removed_tags': ['script', 'style', 'noscript', 'iframe'],
            'remaining_text_length': len(soup.get_text())
        })
        
        # Resolve title
        title = cls._resolve_title(soup, page_title, source_url)
        cls._debug_log(f"🏷️ TITLE RESOLVED", {'title': title})
        
        # Classify page type
        page_type = cls._classify_page_type(source_url or '', soup)
        cls._debug_log(f"📋 PAGE CLASSIFIED", {'page_type': page_type})
        logger.info(f"🏷️ Page: title='{title}' | type='{page_type}' | url='{source_url}'")
        
        sections = []
        structured_tables = []
        structured_lists = []
        structured_cards = []
        
        dom_sections = cls._extract_full_dom(soup, source_url or '', title)

        if page_type == 'listing':
            logger.info(f"📋 Running card extraction for listing: {source_url}")
            card_sections = cls._extract_cards(soup, source_url or '', title)
            structured_cards = cls._extract_cards_structured(soup)

            sections = cls._merge_sections(dom_sections, card_sections)
            logger.info(f"📋 Extracted {len(card_sections)} cards from listing page: {source_url}")
            cls._debug_log(f"📋 CARDS EXTRACTED", {
                'count': len(card_sections),
                'sample': card_sections[0] if card_sections else None
            })
                
        elif page_type == 'howto':
            logger.info(f"📋 Running how-to extraction for: {source_url}")
            howto_sections = cls._extract_howto_content(soup, source_url or '', title)
            sections = cls._merge_sections(dom_sections, howto_sections)
            logger.info(f"📋 Extracted {len(howto_sections)} how-to sections from: {source_url}")
                
        else:
            logger.info(f"📰 Running DOM extraction for {page_type}: {source_url}")
            sections = dom_sections
            if sections:
                logger.info(f"📄 Extracted {len(sections)} sections from {page_type} page: {source_url}")
                cls._debug_log(f"📄 SECTIONS EXTRACTED", {
                    'count': len(sections),
                    'sample': sections[0] if sections else None
                })
            else:
                logger.warning(f"📄 Zero sections extracted from {page_type} page: {source_url}")
        
        # Extract structured data from DOM regardless of page type
        if not structured_tables:
            structured_tables = cls._extract_tables_structured(soup)
        if not structured_lists:
            structured_lists = cls._extract_lists_structured(soup)
        if not structured_cards:
            structured_cards = cls._extract_cards_structured(soup)
        
        has_content = bool(sections)
        
        # Build all_text from sections + structured data
        sections_text = '\n\n'.join(
            s['content'].split(']\n\n', 1)[-1] if ']\n\n' in s.get('content', '') else s.get('content', '')
            for s in sections
        )
        
        all_text = sections_text
        if structured_text:
            all_text = f"{structured_text}\n\n{all_text}"
        
        total_words = len(all_text.split())
        cls._debug_log(f"📊 FINAL STATS", {
            'sections_count': len(sections),
            'structured_tables': len(structured_tables),
            'structured_lists': len(structured_lists),
            'structured_cards': len(structured_cards),
            'all_text_length': len(all_text),
            'total_words': total_words,
            'has_content': has_content,
            'page_type': page_type
        })
        
        logger.info(f"🌐 process_html END: url='{source_url}' | sections={len(sections)} | all_text={len(all_text)} chars | words={total_words}")
        
        if not has_content:
            logger.warning(f"🚨 No content produced for '{source_url}'. HTML was {html_len} chars.")
        
        # Build document_structure with populated fields
        document_structure = {
            'page_title': title,
            'source_url': source_url or '',
            'sections': sections,
            'tables': structured_tables,
            'cards': structured_cards,
            'lists': structured_lists,
            'paragraphs': [],
            'structured_data': {
                'json_ld': json_ld_text,
                'svg_text': svg_text,
                'data_attributes': data_attrs
            }
        }
        
        metadata = cls._extract_metadata(soup, source_url or '')
        metadata['page_type'] = page_type
        metadata['has_structured_data'] = bool(json_ld_text or svg_text or data_attrs)
        
        result = {
            'page_title': title,
            'source_url': source_url or '',
            'page_type': page_type,
            'main_content': {
                'all_text': all_text,
                'sections': sections,
                'headings': [],
                'paragraphs': [],
                'lists': structured_lists,
                'tables': structured_tables,
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
                'table_count': len(structured_tables),
                'list_count': len(structured_lists),
                'card_count': len(structured_cards)
            }
        }
        
        cls._debug_log(f"✅ PROCESS_HTML COMPLETE", {
            'result_keys': list(result.keys()),
            'has_content': has_content,
            'section_count': len(sections),
            'total_chars': len(all_text)
        })
        
        return result

    # ============================================================
    # Legacy methods kept for compatibility
    # ============================================================
    
    @staticmethod
    def clear_boilerplate_cache(domain: Optional[str] = None):
        logger.info(f"🧹 Boilerplate cache is no longer used (domain: {domain})")
    
    @staticmethod
    def get_all_text(html: str) -> str:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text
    
    @staticmethod
    def to_markdown(structure: Dict[str, Any]) -> str:
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