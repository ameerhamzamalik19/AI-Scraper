# crawler/semantic_extractor.py
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup, Tag
import json
import re
from utils.content_classifier import ContentType

@dataclass
class SemanticUnit:
    type: str  # 'structured', 'heading', 'paragraph', 'list', 'table', 'code', 'card'
    content: str
    metadata: Dict[str, Any]
    position: float  # 0.0 to 1.0
    heading_path: List[str]
    section_title: Optional[str] = None
    entity_type: Optional[str] = None

class SemanticExtractor:
    def __init__(self):
        self.heading_path = []
    
    def extract(self, soup: BeautifulSoup, content_type: ContentType) -> List[SemanticUnit]:
        units = []
        
        # Always extract structured data (Schema.org - golden source)
        units.extend(self._extract_schema_org(soup))
        
        # Extract metadata
        units.append(self._extract_metadata(soup))
        
        # Build heading hierarchy
        heading_tree = self._build_heading_tree(soup)
        units.extend(heading_tree)
        
        # Type-specific extraction
        if content_type == ContentType.ECOMMERCE:
            units.extend(self._extract_products(soup, heading_tree))
        elif content_type == ContentType.DOCUMENTATION:
            units.extend(self._extract_doc_sections(soup, heading_tree))
        elif content_type == ContentType.ARTICLE:
            units.extend(self._extract_article_body(soup, heading_tree))
        elif content_type == ContentType.CARD_LISTING:
            units.extend(self._extract_cards(soup, heading_tree))
        elif content_type == ContentType.DATA_TABLE:
            units.extend(self._extract_tables(soup, heading_tree))
        
        # Fallback: extract all paragraphs if nothing else found
        if not units:
            units.extend(self._extract_all_content(soup, heading_tree))
        
        # Post-process: assign positions
        total_height = len(units)
        for i, unit in enumerate(units):
            unit.position = i / max(total_height - 1, 1)
        
        return units
    
    def _extract_schema_org(self, soup: BeautifulSoup) -> List[SemanticUnit]:
        """Extract JSON-LD structured data"""
        units = []
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if data:
                    units.append(SemanticUnit(
                        type='structured',
                        content=json.dumps(data, indent=2),
                        metadata={'source': 'schema_org', 'type': data.get('@type')},
                        position=0.0,
                        heading_path=[],
                        entity_type=data.get('@type', 'structured').lower()
                    ))
            except:
                pass
        return units
    
    def _extract_metadata(self, soup: BeautifulSoup) -> SemanticUnit:
        """Extract page metadata"""
        metadata = {
            'title': soup.title.string if soup.title else '',
            'description': '',
            'og_title': '',
            'og_description': '',
            'canonical_url': '',
        }
        
        # Open Graph
        for meta in soup.find_all('meta'):
            if meta.get('name') == 'description':
                metadata['description'] = meta.get('content', '')
            if meta.get('property') == 'og:title':
                metadata['og_title'] = meta.get('content', '')
            if meta.get('property') == 'og:description':
                metadata['og_description'] = meta.get('content', '')
        
        # Canonical
        canonical = soup.find('link', rel='canonical')
        if canonical:
            metadata['canonical_url'] = canonical.get('href', '')
        
        return SemanticUnit(
            type='metadata',
            content=json.dumps(metadata),
            metadata=metadata,
            position=0.0,
            heading_path=[]
        )
    
    def _build_heading_tree(self, soup: BeautifulSoup) -> List[SemanticUnit]:
        """Build hierarchy from H1-H6 tags"""
        units = []
        self.heading_path = []
        
        for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            text = heading.get_text(strip=True)
            if not text:
                continue
            level = int(heading.name[1])
            
            # Maintain heading path
            while len(self.heading_path) >= level:
                self.heading_path.pop()
            self.heading_path.append(text)
            
            # If this heading has content immediately after it, capture it
            next_sibling = heading.find_next_sibling()
            content = []
            while next_sibling and next_sibling.name not in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                if next_sibling.name and next_sibling.name not in ['div', 'nav', 'header', 'footer']:
                    content.append(next_sibling.get_text(strip=True))
                next_sibling = next_sibling.find_next_sibling()
            
            units.append(SemanticUnit(
                type='heading',
                content='\n'.join([text] + content[:3]),  # First 3 paragraphs
                metadata={'level': level, 'full_path': ' > '.join(self.heading_path)},
                position=0.0,
                heading_path=self.heading_path.copy(),
                section_title=self.heading_path[-1] if self.heading_path else None
            ))
        
        return units
    
    def _extract_products(self, soup: BeautifulSoup, heading_tree: List[SemanticUnit]) -> List[SemanticUnit]:
        """Extract product entities"""
        products = []
        current_heading = heading_tree[-1].heading_path[-1] if heading_tree else ''
        
        # Look for product containers
        for product in soup.select('[class*="product"], [itemtype*="Product"], [class*="item"]'):
            # Try to find name, price, description
            name = self._find_element_text(product, ['[class*="name"]', '[itemprop="name"]', 'h3', 'h4'])
            price = self._find_element_text(product, ['[class*="price"]', '[itemprop="price"]', '.price'])
            desc = self._find_element_text(product, ['[class*="desc"]', '[itemprop="description"]', '.description'])
            
            if name:
                content = f"Product: {name}\n"
                if price:
                    content += f"Price: {price}\n"
                if desc:
                    content += f"Description: {desc}\n"
                
                products.append(SemanticUnit(
                    type='product',
                    content=content,
                    metadata={'name': name, 'price': price, 'has_structured_data': True},
                    position=0.0,
                    heading_path=heading_tree[-1].heading_path if heading_tree else [],
                    section_title=current_heading,
                    entity_type='product'
                ))
        
        return products
    
    def _extract_doc_sections(self, soup: BeautifulSoup, heading_tree: List[SemanticUnit]) -> List[SemanticUnit]:
        """Extract documentation sections with code blocks"""
        units = []
        for code_block in soup.select('pre code, .highlight, .code-block'):
            code = code_block.get_text()
            # Find surrounding explanation
            parent = code_block.parent
            explanation = []
            prev = parent.find_previous_sibling()
            while prev and prev.name not in ['h2', 'h3', 'h4']:
                if prev.name in ['p', 'div']:
                    explanation.append(prev.get_text(strip=True))
                prev = prev.find_previous_sibling()
            
            content = f"Code:\n{code}\n"
            if explanation:
                content = f"Explanation:\n{''.join(reversed(explanation))}\n\n{content}"
            
            units.append(SemanticUnit(
                type='code',
                content=content,
                metadata={'language': self._detect_code_language(code_block)},
                position=0.0,
                heading_path=heading_tree[-1].heading_path if heading_tree else [],
                section_title=heading_tree[-1].heading_path[-1] if heading_tree else None,
                entity_type='code_example'
            ))
        return units
    
    def _extract_article_body(self, soup: BeautifulSoup, heading_tree: List[SemanticUnit]) -> List[SemanticUnit]:
        """Extract article content"""
        # Try to find main article content
        article = soup.find('article') or soup.find('main') or soup.find('body')
        if article:
            text = article.get_text(strip=True, separator='\n\n')
            return [SemanticUnit(
                type='article_body',
                content=text,
                metadata={'source': 'article_tag'},
                position=0.0,
                heading_path=heading_tree[-1].heading_path if heading_tree else [],
                section_title=heading_tree[-1].heading_path[-1] if heading_tree else None,
                entity_type='article_body'
            )]
        return []
    
    def _extract_cards(self, soup: BeautifulSoup, heading_tree: List[SemanticUnit]) -> List[SemanticUnit]:
        """Extract card-style content"""
        cards = []
        for card in soup.select('[class*="card"], [class*="item"], article'):
            # Try to find title and description
            title = self._find_element_text(card, ['h2', 'h3', 'h4', '[class*="title"]'])
            desc = self._find_element_text(card, ['p', '[class*="desc"]', '[class*="content"]'])
            
            if title or desc:
                content = f"{title}\n{desc}" if title and desc else title or desc
                cards.append(SemanticUnit(
                    type='card',
                    content=content,
                    metadata={'title': title},
                    position=0.0,
                    heading_path=heading_tree[-1].heading_path if heading_tree else [],
                    section_title=heading_tree[-1].heading_path[-1] if heading_tree else None,
                    entity_type='card'
                ))
        return cards
    
    def _extract_tables(self, soup: BeautifulSoup, heading_tree: List[SemanticUnit]) -> List[SemanticUnit]:
        """Extract data tables"""
        units = []
        for table in soup.find_all('table'):
            # Get headers
            headers = []
            for th in table.find_all('th'):
                headers.append(th.get_text(strip=True))
            
            if not headers:
                # Try first row as headers
                first_row = table.find('tr')
                if first_row:
                    headers = [td.get_text(strip=True) for td in first_row.find_all('td')]
                    rows = table.find_all('tr')[1:]
                else:
                    rows = []
            else:
                rows = table.find_all('tr')[1:] if len(table.find_all('tr')) > 1 else []
            
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                if cells:
                    row_content = " | ".join([f"{h}: {c}" if h else c for h, c in zip(headers, cells)])
                    units.append(SemanticUnit(
                        type='table_row',
                        content=row_content,
                        metadata={'headers': headers},
                        position=0.0,
                        heading_path=heading_tree[-1].heading_path if heading_tree else [],
                        section_title=heading_tree[-1].heading_path[-1] if heading_tree else None,
                        entity_type='table'
                    ))
        return units
    
    def _extract_all_content(self, soup: BeautifulSoup, heading_tree: List[SemanticUnit]) -> List[SemanticUnit]:
        """Fallback: extract all content as paragraphs"""
        units = []
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if text and len(text) > 50:  # Skip short paragraphs
                units.append(SemanticUnit(
                    type='paragraph',
                    content=text,
                    metadata={},
                    position=0.0,
                    heading_path=heading_tree[-1].heading_path if heading_tree else [],
                    section_title=heading_tree[-1].heading_path[-1] if heading_tree else None,
                    entity_type='paragraph'
                ))
        return units
    
    def _find_element_text(self, element, selectors: List[str]) -> str:
        """Find text using multiple selectors"""
        for selector in selectors:
            found = element.select_one(selector)
            if found:
                return found.get_text(strip=True)
        return ''
    
    def _detect_code_language(self, code_block) -> str:
        """Detect programming language from code block"""
        classes = code_block.get('class', [])
        for cls in classes:
            if cls.startswith('language-'):
                return cls.replace('language-', '')
            if cls.startswith('lang-'):
                return cls.replace('lang-', '')
        return 'unknown'