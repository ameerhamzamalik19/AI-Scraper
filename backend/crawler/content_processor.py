from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional, Set
import re
from urllib.parse import urlparse
import logging
import json
import os
import hashlib

logger = logging.getLogger(__name__)

class ContentProcessor:
    """
    Content extraction that learns and removes boilerplate (navbar, footer)
    from the first page and strips it from subsequent pages.
    """
    
    # Tags that contain NO visible content - FIXED: Added nav, header, footer
    NON_CONTENT_TAGS = {
        'script', 'style', 'noscript', 'iframe', 'svg',
        'meta', 'link', 'head', 'template',
        'nav', 'header', 'footer'  # 🆕 Added these
    }
    
    # Cache for boilerplate patterns per domain
    _boilerplate_cache: Dict[str, Dict[str, Set[str]]] = {}
    _cache_file = "boilerplate_cache.json"
    
    # Track which domains have been processed as "first page"
    _processed_domains: Set[str] = set()

    @staticmethod
    def _remove_structural_artifacts(soup: BeautifulSoup) -> None:
        """
        Remove UI elements that don't contain meaningful content.
        This removes CONTAINERS, not the content itself.
        Preserves ALL visible text.
        """
        # Remove empty elements (no text, just layout)
        for element in soup.find_all(True):
            if not element.get_text(strip=True):
                if element.name not in ['html', 'body']:
                    element.decompose()
        
        # Remove common UI elements that contain no useful text
        ui_selectors = [
            '.sr-only', '.visually-hidden', '.screen-reader-text',
            '.hidden', '.d-none', '.invisible'
        ]
        for selector in ui_selectors:
            for element in soup.select(selector):
                if not element.get_text(strip=True):
                    element.decompose()

    @staticmethod
    def _remove_duplicate_containers(soup: BeautifulSoup) -> None:
        """
        Remove duplicate containers that appear multiple times on a page.
        Works on any website - carousels, repeated widgets, etc.
        """
        # Find all containers with significant content
        containers = []
        
        for element in soup.find_all(['div', 'section', 'article']):
            text = element.get_text(strip=True)
            if len(text) < 50:  # Skip small elements
                continue
            
            # Get the structure of this element (not content)
            structure = str(element)[:300]
            # Normalize: remove IDs, classes, numbers
            structure = re.sub(r'id="[^"]*"', '', structure)
            structure = re.sub(r'class="[^"]*"', '', structure)
            structure = re.sub(r'\d+', '', structure)
            structure = re.sub(r'\s+', ' ', structure).strip()
            
            # Get content signature
            content_sig = re.sub(r'\d+', '', text)[:150].lower()
            
            containers.append({
                'element': element,
                'structure': structure[:150],
                'content': content_sig,
                'text_len': len(text)
            })
        
        # Find duplicates by structure + content
        seen = set()
        to_remove = []
        
        for container in containers:
            key = f"{container['structure']}|{container['content']}"
            if key in seen:
                to_remove.append(container['element'])
            else:
                seen.add(key)
        
        for element in to_remove:
            element.decompose()
    
    @classmethod
    def _load_cache(cls):
        """Load boilerplate cache from disk."""
        if os.path.exists(cls._cache_file):
            try:
                with open(cls._cache_file, 'r') as f:
                    data = json.load(f)
                    # Convert lists back to sets
                    for domain in data:
                        if 'text_patterns' in data[domain]:
                            data[domain]['text_patterns'] = set(data[domain]['text_patterns'])
                    cls._boilerplate_cache = data
                    logger.info(f"📚 Loaded boilerplate cache for {len(data)} domains")
            except Exception as e:
                logger.warning(f"Failed to load boilerplate cache: {e}")
                cls._boilerplate_cache = {}
    
    @classmethod
    def _save_cache(cls):
        """Save boilerplate cache to disk."""
        try:
            # Convert sets to lists for JSON serialization
            data = {}
            for domain, patterns in cls._boilerplate_cache.items():
                data[domain] = {
                    'text_patterns': list(patterns.get('text_patterns', set())),
                    'selectors': list(patterns.get('selectors', set())),
                }
            with open(cls._cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save boilerplate cache: {e}")
    
    @staticmethod
    def _deduplicate_carousel_content(soup: BeautifulSoup) -> None:
        """
        Remove duplicate carousel/slider content.
        Carousels often have the same content duplicated across slides.
        """
        # Find carousel containers
        carousel_selectors = [
            '.carousel', '.slider', '.slideshow', '.slick-slider',
            '.swiper', '.glide', '.owl-carousel', '.splide',
            '[role="tablist"]', '.tab-pane'
        ]
        
        for selector in carousel_selectors:
            for container in soup.select(selector):
                # Get all child items
                items = container.find_all(True, recursive=True)
                
                # Extract text signatures for each item
                seen_signatures = set()
                to_remove = []
                
                for item in items:
                    text = item.get_text(strip=True)
                    if not text or len(text) < 10:
                        continue
                    
                    # Create signature (first 100 chars, normalized)
                    signature = re.sub(r'\s+', ' ', text)[:150].lower()
                    
                    if signature in seen_signatures:
                        # This is a duplicate - mark for removal
                        to_remove.append(item)
                    else:
                        seen_signatures.add(signature)
                
                # Remove duplicates (keep first occurrence)
                for item in to_remove:
                    item.decompose()

    @classmethod
    def _get_domain(cls, url: str) -> str:
        """Extract domain from URL."""
        if not url:
            return ''
        parsed = urlparse(url)
        return parsed.netloc or parsed.path.split('/')[0]
    
    @classmethod
    def _is_first_page_for_domain(cls, url: str) -> bool:
        """
        Check if this is the first page we're processing for this domain.
        🆕 FIXED: Uses processed_domains set, not just cache.
        """
        domain = cls._get_domain(url)
        if not domain:
            return True
        # Check if we've already processed a page for this domain
        return domain not in cls._processed_domains
    
    @classmethod
    def _learn_boilerplate(cls, soup: BeautifulSoup, url: str):
        """
        Learn boilerplate patterns from the first page.
        Identifies navbar, footer, and other repeated elements.
        """
        domain = cls._get_domain(url)
        if not domain:
            return
        
        # Mark this domain as processed
        cls._processed_domains.add(domain)
        
        text_patterns = set()
        selectors = set()
        
        # Find potential boilerplate elements
        # 1. Navbars
        for nav in soup.find_all(['nav', 'header']):
            text = nav.get_text(separator=' ', strip=True)
            if text and len(text) > 20:
                # Store a sample of the text (first 100 chars) as pattern
                text_patterns.add(text[:100].lower())
                # Store class/id selectors
                if nav.get('class'):
                    selectors.add(f"nav.{'.'.join(nav.get('class'))}")
                if nav.get('id'):
                    selectors.add(f"nav#{nav.get('id')}")
        
        # 2. Footers
        for footer in soup.find_all(['footer']):
            text = footer.get_text(separator=' ', strip=True)
            if text and len(text) > 20:
                text_patterns.add(text[:100].lower())
                if footer.get('class'):
                    selectors.add(f"footer.{'.'.join(footer.get('class'))}")
                if footer.get('id'):
                    selectors.add(f"footer#{footer.get('id')}")
        
        # 3. Common boilerplate selectors
        for selector in [
            '.header', '.footer', '.navbar', '.navigation', '.menu',
            '.sidebar', '.side-bar', '.widget', '.social', '.share',
            '.cookie', '.cookie-banner', '.newsletter', '.subscribe',
            '.copyright', '.legal'
        ]:
            elements = soup.select(selector)
            for el in elements:
                text = el.get_text(separator=' ', strip=True)
                if text and len(text) > 20:
                    text_patterns.add(text[:100].lower())
        
        # 4. Common boilerplate text patterns
        boilerplate_keywords = [
            'copyright', 'all rights reserved', 'privacy policy', 
            'terms of service', 'cookie policy', 'subscribe', 'newsletter',
            'follow us', 'share on', 'sign up', 'login', 'register',
            'contact us', 'about us', 'careers', 'press', 'investor'
        ]
        
        # Find elements containing boilerplate keywords
        for keyword in boilerplate_keywords:
            for el in soup.find_all(string=re.compile(keyword, re.I)):
                parent = el.parent
                if parent:
                    text = parent.get_text(separator=' ', strip=True)
                    if text and len(text) > 20:
                        text_patterns.add(text[:100].lower())
                        if parent.get('class'):
                            selectors.add(f"{parent.name}.{'.'.join(parent.get('class'))}")
        
        # Store in cache
        cls._boilerplate_cache[domain] = {
            'text_patterns': text_patterns,
            'selectors': selectors,
        }
        cls._save_cache()
        
        logger.info(f"✅ Learned {len(text_patterns)} text patterns and {len(selectors)} selectors for {domain}")
    
    @classmethod
    def _remove_boilerplate(cls, soup: BeautifulSoup, url: str):
        """
        Remove boilerplate elements from the page using learned patterns.
        """
        domain = cls._get_domain(url)
        if not domain or domain not in cls._boilerplate_cache:
            return
        
        patterns = cls._boilerplate_cache[domain]
        text_patterns = patterns.get('text_patterns', set())
        selectors = patterns.get('selectors', set())
        
        removed_count = 0
        
        # 1. Remove by text patterns
        for pattern in text_patterns:
            if len(pattern) < 20:  # Skip very short patterns
                continue
            # Find elements containing this pattern
            for element in soup.find_all(True):
                try:
                    text = element.get_text(separator=' ', strip=True).lower()
                    if pattern in text:
                        # Check if this is a big enough element (likely a container)
                        if len(text) > 50:
                            element.decompose()
                            removed_count += 1
                            break
                except Exception:
                    continue
        
        # 2. Remove by selectors
        for selector in selectors:
            for element in soup.select(selector):
                # Only remove if it's not a main content container
                if not element.find_parent(['main', 'article', '.content', '.main-content']):
                    element.decompose()
                    removed_count += 1
        
        # 3. Remove common boilerplate elements by ID/class patterns
        boilerplate_classes = [
            'nav', 'navbar', 'navigation', 'menu', 'header', 'footer',
            'sidebar', 'cookie', 'newsletter', 'subscribe', 'social',
            'share', 'copyright', 'legal', 'terms', 'privacy'
        ]
        
        for class_name in boilerplate_classes:
            for element in soup.find_all(class_=re.compile(class_name, re.I)):
                # Check if it's not in main content
                if not element.find_parent(['main', 'article', '.content']):
                    element.decompose()
                    removed_count += 1
        
        # 4. Remove elements with data attributes that look like boilerplate
        for element in soup.find_all(attrs={'data-testid': True}):
            testid = element.get('data-testid', '').lower()
            if any(word in testid for word in ['nav', 'header', 'footer', 'sidebar']):
                element.decompose()
                removed_count += 1
        
        if removed_count > 0:
            logger.info(f"🧹 Removed {removed_count} boilerplate elements for {domain}")
    
    @staticmethod
    def _resolve_title(
        soup: BeautifulSoup,
        page_title: Optional[str],
        source_url: Optional[str]
    ) -> str:
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

    @classmethod
    def _deduplicate_sections(cls, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Remove duplicate sections while preserving the first occurrence.
        🆕 FIXED: Compares raw content, not wrapped content with heading path.
        """
        def get_raw_content(content: str) -> str:
            """Strip the heading path wrapper to get raw content."""
            if ']\n\n' in content:
                return content.split(']\n\n', 1)[1]
            return content
        
        def get_signature(content: str) -> str:
            """Create a normalized signature for content comparison."""
            # Strip heading path wrapper first
            raw = get_raw_content(content)
            # Remove numbers, punctuation, normalize whitespace
            normalized = re.sub(r'\d+', '', raw)
            normalized = re.sub(r'[^\w\s]', '', normalized)
            normalized = re.sub(r'\s+', ' ', normalized).strip().lower()
            # Use first 500 chars as signature (more robust)
            return normalized[:500]
        
        seen = set()
        deduped = []
        
        for section in sections:
            content = section.get('content', '')
            if not content:
                continue
            
            signature = get_signature(content)
            
            # Skip if we've seen this content before
            if signature in seen:
                logger.debug(f"🧹 Skipping duplicate section: {signature[:50]}...")
                continue
            
            seen.add(signature)
            deduped.append(section)
        
        if len(deduped) < len(sections):
            logger.info(f"🧹 Deduplicated sections: {len(sections)} → {len(deduped)}")
        
        return deduped
    
    @staticmethod
    def process_html(
        html: str,
        source_url: Optional[str] = None,
        page_title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Extract ALL visible text from HTML.
        Preserves all content, only removes structural artifacts.
        """
        if not html:
            return {
                'page_title': page_title or 'Untitled',
                'source_url': source_url or '',
                'all_text': '',
                'headings': [],
                'paragraphs': [],
                'lists': [],
                'tables': [],
                'sections': [],
                'metadata': {'url': source_url or ''},
                'document_structure': {
                    'page_title': page_title or 'Untitled',
                    'source_url': source_url or '',
                    'sections': [],
                    'tables': [],
                    'cards': [],
                    'paragraphs': [],
                },
                'is_first_page': False
            }

        # Load cache on first use
        if not ContentProcessor._boilerplate_cache:
            ContentProcessor._load_cache()

        soup = BeautifulSoup(html, 'html.parser')
        domain = ContentProcessor._get_domain(source_url or '')
        is_first_page = ContentProcessor._is_first_page_for_domain(source_url or '')

        # Remove non-content tags (now includes nav, header, footer)
        for tag in ContentProcessor.NON_CONTENT_TAGS:
            for element in soup.find_all(tag):
                element.decompose()

        # Remove duplicate carousel content
        ContentProcessor._deduplicate_carousel_content(soup)

        # Remove structural artifacts (preserves content)
        ContentProcessor._remove_structural_artifacts(soup)

        ContentProcessor._remove_duplicate_containers(soup)

        # Learn boilerplate from first page, or remove it from subsequent pages
        if is_first_page:
            logger.info(f"📚 First page for {domain} - learning boilerplate patterns")
            ContentProcessor._learn_boilerplate(soup, source_url or '')
        else:
            logger.info(f"🧹 Removing boilerplate from {domain} page")
            ContentProcessor._remove_boilerplate(soup, source_url or '')

        title = ContentProcessor._resolve_title(soup, page_title, source_url)

        # Extract everything
        headings = ContentProcessor._extract_headings(soup)
        paragraphs = ContentProcessor._extract_paragraphs(soup)
        lists = ContentProcessor._extract_lists(soup)
        tables = ContentProcessor._extract_tables(soup)
        sections = ContentProcessor._extract_all_sections(soup, title, source_url)
        
        # Deduplicate sections (preserves first occurrence)
        sections = ContentProcessor._deduplicate_sections(sections)
        
        metadata = ContentProcessor._extract_metadata(soup, source_url or '')

        # Get ALL visible text
        all_text = soup.get_text(separator='\n', strip=True)
        all_text = re.sub(r'\n{3,}', '\n\n', all_text)
        all_text = re.sub(r'[ \t]+', ' ', all_text)

        # Build document_structure
        document_structure = {
            'page_title': title,
            'source_url': source_url or '',
            'sections': sections,
            'tables': tables,
            'cards': [],
            'paragraphs': paragraphs,
        }

        metadata['document_structure'] = document_structure
        metadata['is_first_page'] = is_first_page
        metadata['boilerplate_learned'] = is_first_page

        return {
            'page_title': title,
            'source_url': source_url or '',
            'all_text': all_text,
            'headings': headings,
            'paragraphs': paragraphs,
            'lists': lists,
            'tables': tables,
            'sections': sections,
            'metadata': metadata,
            'document_structure': document_structure,
            'is_first_page': is_first_page,
            'text_stats': {
                'total_chars': len(all_text),
                'total_words': len(all_text.split()),
                'heading_count': len(headings),
                'paragraph_count': len(paragraphs),
                'list_count': len(lists),
                'table_count': len(tables),
                'section_count': len(sections)
            }
        }

    @staticmethod
    def clear_boilerplate_cache(domain: Optional[str] = None):
        """Clear boilerplate cache for a specific domain or all domains."""
        if domain:
            ContentProcessor._boilerplate_cache.pop(domain, None)
            ContentProcessor._processed_domains.discard(domain)
            logger.info(f"🧹 Cleared boilerplate cache for {domain}")
        else:
            ContentProcessor._boilerplate_cache = {}
            ContentProcessor._processed_domains = set()
            if os.path.exists(ContentProcessor._cache_file):
                os.remove(ContentProcessor._cache_file)
            logger.info("🧹 Cleared all boilerplate cache")
        ContentProcessor._save_cache()

    @staticmethod
    def _extract_all_sections(
        soup: BeautifulSoup,
        page_title: str,
        source_url: Optional[str]
    ) -> List[Dict[str, Any]]:
        """
        Extract ALL text grouped by headings with FAQ deduplication.
        🆕 FIXED: Heading path slicing fixed, duplicate content detection improved.
        """
        sections = []
        current_heading = page_title
        heading_path = [page_title]
        current_content = []
        seen_text = set()
        seen_section_hashes = set()  # Track raw content hashes

        def flush_section():
            nonlocal current_content
            if not current_content:
                return
            
            content = '\n\n'.join(current_content)
            
            # Remove FAQ duplicates within this section
            content = ContentProcessor._deduplicate_faq_content(content)
            
            # Remove structural UI text
            content = ContentProcessor._clean_structural_text(content)
            
            # Clean up whitespace
            content = re.sub(r'\n{3,}', '\n\n', content)
            content = re.sub(r'[ \t]+', ' ', content)
            
            if len(content.strip()) < 20:
                current_content = []
                return
            
            # 🆕 FIXED: Hash raw content, NOT wrapped content
            raw_content = content
            content_hash = hashlib.md5(raw_content.encode('utf-8')).hexdigest()
            
            if content_hash in seen_section_hashes:
                current_content = []
                return
            
            seen_section_hashes.add(content_hash)
            
            # Now add heading path wrapper
            heading_context = " > ".join(heading_path) if len(heading_path) > 1 else heading_path[0]
            full_content = f"[{heading_context}]\n\n{content}"
            
            sections.append({
                'heading': current_heading,
                'heading_path': list(heading_path),
                'content': full_content,
                'source_url': source_url or '',
            })
            current_content = []

        for element in soup.find_all(True):
            tag = element.name
            
            if tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                flush_section()
                text = element.get_text(strip=True)
                if text:
                    current_heading = text
                    level = int(tag[1])
                    
                    # 🆕 FIXED: Slice to level-1, NOT level
                    heading_path = heading_path[:level-1]
                    
                    # Pad if needed (shouldn't happen with correct slicing)
                    while len(heading_path) < level:
                        heading_path.append(heading_path[-1] if heading_path else page_title)

                    if not heading_path or heading_path[-1] != text:
                        heading_path.append(text)
            else:
                text = element.get_text(separator=' ', strip=True)
                if text:
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) < 3:
                        continue
                    norm = text.lower()
                    if norm in seen_text:
                        continue
                    seen_text.add(norm)
                    if current_content and current_content[-1] == text:
                        continue
                    current_content.append(text)

        flush_section()

        if not sections:
            all_text = soup.get_text(separator='\n', strip=True)
            if all_text:
                all_text = ContentProcessor._deduplicate_faq_content(all_text)
                sections.append({
                    'heading': page_title,
                    'heading_path': [page_title],
                    'content': all_text,
                    'source_url': source_url or '',
                })

        return sections

    @staticmethod
    def _deduplicate_faq_content(content: str) -> str:
        """
        Remove duplicate FAQ question/answer pairs.
        Works on any FAQ pattern: Q: / A: or numbered questions.
        """
        lines = content.split('\n')
        
        # Detect FAQ patterns
        faq_patterns = [
            r'^Q\s*[:.]',              # Q: or Q.
            r'^Question\s*[:.]',       # Question: or Question.
            r'^\d+\s*[.)]\s*',         # 1. or 1) or 1.
            r'^[A-Z]\s*[.)]\s*',       # A. or A) 
            r'^What\s|^Where\s|^How\s|^Why\s|^When\s|^Can\s|^Does\s|^Is\s|^Are\s',  # Question words
        ]
        
        # Check if this looks like FAQ content
        is_faq = False
        for line in lines[:10]:
            if line.strip():
                for pattern in faq_patterns:
                    if re.search(pattern, line.strip(), re.I):
                        is_faq = True
                        break
            if is_faq:
                break
        
        if not is_faq:
            return content
        
        # Deduplicate questions
        seen_questions = set()
        cleaned_lines = []
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            if not stripped:
                cleaned_lines.append(line)
                i += 1
                continue
            
            # Check if this line is a question
            is_question = False
            question_text = stripped
            
            for pattern in faq_patterns:
                match = re.search(pattern, stripped, re.I)
                if match:
                    question_text = re.sub(pattern, '', stripped, flags=re.I).strip()
                    is_question = True
                    break
            
            if is_question and question_text:
                # Normalize question for dedup
                normalized = re.sub(r'[^\w\s]', '', question_text).lower().strip()
                
                if normalized in seen_questions:
                    # Skip this question AND its answer
                    i += 1
                    # Skip following lines until next question or blank line
                    while i < len(lines):
                        next_line = lines[i].strip()
                        if not next_line:
                            i += 1
                            break
                        # Check if next line is a question
                        is_next_question = False
                        for pattern in faq_patterns:
                            if re.search(pattern, next_line, re.I):
                                is_next_question = True
                                break
                        if is_next_question:
                            break
                        i += 1
                    continue
                
                seen_questions.add(normalized)
            
            cleaned_lines.append(line)
            i += 1
        
        return '\n'.join(cleaned_lines)

    @staticmethod
    def _clean_structural_text(content: str) -> str:
        """
        Remove UI structural text that adds no semantic value.
        Preserves ALL actual content.
        """
        patterns = [
            r'\[popover:\]',
            r'Expand all\s*Collapse all',
            r'\(Annual subscription-auto renews\)',
            r'Price does not include tax',
            r'Buy now\s*Try for free\s*See trial terms',
            r'\$[\d,]+\s*(user/month|per user|/month)',
            r'See trial terms\s*\d*',
            # Additional patterns for carousels
            r'Slide %\{start\} of %\{total\}',
            r'%\{slideTitle\}',
            r'Previous slide\s*Next slide',
            r'VIEW DETAILS\s*>',
            r'LEARN MORE\s*>',
            r'Skip News',
            r'End of News section',
            r'Follow us\s*Share this page',
            r'I want to\.\.\.\s*Expand All\s*\|?\s*Collapse All',
        ]
        
        for pattern in patterns:
            content = re.sub(pattern, '', content, flags=re.I)
        
        # Clean up extra whitespace
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        
        return content.strip()

    @staticmethod
    def _extract_headings(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract all headings with hierarchy."""
        headings = []
        for level in range(1, 7):
            for heading in soup.find_all(f'h{level}'):
                text = heading.get_text(strip=True)
                if text and len(text) > 2:
                    headings.append({
                        'level': level,
                        'text': text,
                        'id': heading.get('id', ''),
                        'classes': ' '.join(heading.get('class', []))
                    })
        return headings
    
    @staticmethod
    def _extract_paragraphs(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract all paragraphs with context."""
        paragraphs = []
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if text and len(text) > 10:
                context = ''
                for parent in p.parents:
                    h = parent.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                    if h:
                        context = h.get_text(strip=True)
                        break
                
                paragraphs.append({
                    'text': text,
                    'context': context,
                    'has_image': bool(p.find('img')),
                    'has_link': bool(p.find('a'))
                })
        return paragraphs
    
    @staticmethod
    def _extract_lists(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract all lists."""
        lists = []
        for list_tag in soup.find_all(['ul', 'ol']):
            items = []
            for li in list_tag.find_all('li', recursive=False):
                text = li.get_text(strip=True)
                if text:
                    items.append(text)
            if items:
                lists.append({
                    'type': 'ordered' if list_tag.name == 'ol' else 'unordered',
                    'items': items
                })
        return lists
    
    @staticmethod
    def _extract_tables(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract all tables."""
        tables = []
        for table in soup.find_all('table'):
            try:
                headers = []
                thead = table.find('thead')
                if thead:
                    for th in thead.find_all('th'):
                        headers.append(th.get_text(strip=True))
                else:
                    first_row = table.find('tr')
                    if first_row:
                        for th in first_row.find_all(['th', 'td']):
                            text = th.get_text(strip=True)
                            if text:
                                headers.append(text)
                
                rows = []
                for tr in table.find_all('tr'):
                    if tr == first_row and not thead:
                        continue
                    row = []
                    for td in tr.find_all(['td', 'th']):
                        text = td.get_text(strip=True)
                        if text:
                            row.append(text)
                    if row:
                        rows.append(row)
                
                if rows:
                    tables.append({
                        'headers': headers,
                        'rows': rows[:20],
                        'row_count': len(rows),
                        'has_headers': bool(headers)
                    })
            except Exception as e:
                logger.warning(f"Error extracting table: {e}")
        
        return tables
    
    @staticmethod
    def _extract_metadata(soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """Extract metadata from meta tags."""
        metadata = {
            'url': url,
            'domain': urlparse(url).netloc if url else '',
            'path': urlparse(url).path if url else '',
            'title': None,
            'description': None,
            'keywords': None,
            'author': None,
            'og_title': None,
            'og_description': None,
            'og_image': None
        }
        
        title_tag = soup.find('title')
        if title_tag:
            metadata['title'] = title_tag.get_text(strip=True)
        
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            metadata['description'] = meta_desc.get('content', '').strip()
        
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            metadata['keywords'] = meta_keywords.get('content', '').strip()
        
        meta_author = soup.find('meta', attrs={'name': 'author'})
        if meta_author:
            metadata['author'] = meta_author.get('content', '').strip()
        
        og_title = soup.find('meta', property='og:title')
        if og_title:
            metadata['og_title'] = og_title.get('content', '').strip()
        
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            metadata['og_description'] = og_desc.get('content', '').strip()
        
        og_image = soup.find('meta', property='og:image')
        if og_image:
            metadata['og_image'] = og_image.get('content', '').strip()
        
        return metadata
    
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
        
        for table in structure.get('tables', []):
            if table.get('headers'):
                lines.append("\n")
                lines.append("| " + " | ".join(table['headers']) + " |")
                lines.append("|" + "|".join(["---"] * len(table['headers'])) + "|")
            for row in table.get('rows', [])[:10]:
                lines.append("| " + " | ".join(row) + " |")
        
        if structure.get('all_text') and not structure.get('sections'):
            lines.append("\n")
            lines.append("## Full Content\n")
            lines.append(structure['all_text'])
        
        return '\n\n'.join(filter(None, lines))
    
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