import hashlib
import json
import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ContentProcessorOld:
    """Extract semantically useful sections from HTML while preserving structure."""

    MAX_HTML_BYTES = 15 * 1024 * 1024
    MIN_PARAGRAPH_CHARS = 20
    MIN_MEANINGFUL_WORDS = 6

    BOILERPLATE_TAGS = {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
        "template",
    }

    BOILERPLATE_SELECTORS = [
        "nav",
        ".nav",
        ".navigation",
        ".menu",
        ".sidebar",
        ".side-bar",
        ".widget",
        ".ad",
        ".advertisement",
        ".social",
        ".share",
        ".share-buttons",
        ".footer",
        ".footer-content",
        ".cookie",
        ".cookie-banner",
        ".newsletter",
        ".subscribe",
        ".signup",
        ".comments",
        ".comment-section",
        ".related",
        ".recommended",
        ".breadcrumb",
        ".pagination",
        ".search",
        ".filter",
        ".tag-cloud",
        ".tags",
        ".popular-posts",
        "header",
        "aside",
        "form",
    ]

    @staticmethod
    def normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def text_word_count(text: str) -> int:
        return len(re.findall(r"\b\w+\b", (text or "").lower()))

    @staticmethod
    def is_likely_boilerplate(text: str) -> bool:
        normalized = ContentProcessor.normalize_whitespace(text)
        if not normalized:
            return True
        if len(re.findall(r"\b(?:home|about|contact|privacy|terms|login|signup|subscribe|follow|share|menu|navigation|search|filter|tags|copyright|all rights reserved)\b", normalized.lower())) > 0 and len(normalized.split()) < 18:
            return True
        return False

    @staticmethod
    def _strip_unwanted_nodes(soup: BeautifulSoup) -> None:
        for tag in soup.find_all(True):
            tag_name = getattr(tag, "name", None)
            if tag_name is None:
                continue

            if tag_name in ContentProcessor.BOILERPLATE_TAGS:
                tag.decompose()
                continue

            if tag_name in {"nav", "header", "footer", "aside", "form"}:
                tag.decompose()
                continue

            attrs = getattr(tag, "attrs", None) or {}
            if isinstance(attrs, dict) and "class" in attrs:
                class_names = " ".join(tag.get("class", []) or []).lower()
                for selector in [
                    "nav",
                    "navigation",
                    "sidebar",
                    "footer",
                    "cookie",
                    "newsletter",
                    "subscribe",
                    "social",
                    "share",
                    "pagination",
                    "comment",
                    "related",
                    "tags",
                ]:
                    if selector in class_names:
                        tag.decompose()
                        break

    @staticmethod
    def get_metadata(soup: BeautifulSoup) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "title": None,
            "description": None,
            "author": None,
            "published_time": None,
            "modified_time": None,
            "keywords": [],
            "canonical_url": None,
            "language": None,
            "og_title": None,
            "og_description": None,
            "og_type": None,
            "og_image": None,
        }

        title_tag = soup.find("title")
        if title_tag:
            metadata["title"] = ContentProcessor.normalize_whitespace(title_tag.get_text(" ", strip=True))

        meta_description = soup.find("meta", attrs={"name": "description"})
        if meta_description:
            metadata["description"] = (meta_description.get("content") or "").strip()

        meta_keywords = soup.find("meta", attrs={"name": "keywords"})
        if meta_keywords:
            keywords = meta_keywords.get("content", "")
            metadata["keywords"] = [k.strip() for k in keywords.split(",") if k.strip()]

        author = soup.find("meta", attrs={"name": "author"})
        if author:
            metadata["author"] = (author.get("content") or "").strip()

        for key in ["article:published_time", "publish-date", "datePublished"]:
            meta = soup.find("meta", attrs={"property": key})
            if meta is None:
                meta = soup.find("meta", attrs={"name": key})
            if meta:
                metadata["published_time"] = (meta.get("content") or "").strip()
                break

        for key in ["article:modified_time", "modified_time"]:
            meta = soup.find("meta", attrs={"property": key})
            if meta is None:
                meta = soup.find("meta", attrs={"name": key})
            if meta:
                metadata["modified_time"] = (meta.get("content") or "").strip()
                break

        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title:
            metadata["og_title"] = (og_title.get("content") or "").strip()

        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc:
            metadata["og_description"] = (og_desc.get("content") or "").strip()

        og_type = soup.find("meta", attrs={"property": "og:type"})
        if og_type:
            metadata["og_type"] = (og_type.get("content") or "").strip()

        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image:
            metadata["og_image"] = (og_image.get("content") or "").strip()

        html_tag = soup.find("html")
        if html_tag:
            metadata["language"] = html_tag.get("lang") or "en"

        canonical = soup.find("link", attrs={"rel": "canonical"})
        if canonical:
            href = canonical.get("href")
            metadata["canonical_url"] = href.strip() if href else None

        return metadata

    @staticmethod
    def extract_headings(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        headings: List[Dict[str, Any]] = []
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = ContentProcessor.normalize_whitespace(tag.get_text(" ", strip=True))
            if not text or ContentProcessor.is_likely_boilerplate(text):
                continue
            headings.append({
                "level": int(tag.name[1]),
                "text": text,
                "id": tag.get("id") or "",
                "tag": tag.name,
            })
        return headings

    @staticmethod
    def extract_paragraphs(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        paragraphs: List[Dict[str, Any]] = []
        for p in soup.find_all("p"):
            text = ContentProcessor.normalize_whitespace(p.get_text(" ", strip=True))
            if not text or len(text) < ContentProcessor.MIN_PARAGRAPH_CHARS:
                continue
            if ContentProcessor.is_likely_boilerplate(text):
                continue
            if ContentProcessor.text_word_count(text) < ContentProcessor.MIN_MEANINGFUL_WORDS:
                continue
            paragraphs.append({
                "text": text,
                "tag": "p",
                "heading_path": [],
                "context": "",
            })
        return paragraphs

    @staticmethod
    def extract_lists(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        lists: List[Dict[str, Any]] = []
        for ul in soup.find_all(["ul", "ol"]):
            items = []
            for li in ul.find_all("li"):
                item_text = ContentProcessor.normalize_whitespace(li.get_text(" ", strip=True))
                if item_text and not ContentProcessor.is_likely_boilerplate(item_text):
                    items.append(item_text)
            if not items:
                continue
            lists.append({
                "type": ul.name,
                "items": items,
                "heading_path": [],
            })
        return lists

    @staticmethod
    def extract_tables(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        tables: List[Dict[str, Any]] = []
        for table in soup.find_all("table"):
            caption = ""
            caption_tag = table.find("caption")
            if caption_tag:
                caption = ContentProcessor.normalize_whitespace(caption_tag.get_text(" ", strip=True))

            headers: List[str] = []
            header_row = table.find("thead") or table.find("tr")
            if header_row:
                headers = [
                    ContentProcessor.normalize_whitespace(th.get_text(" ", strip=True))
                    for th in header_row.find_all(["th", "td"])
                    if ContentProcessor.normalize_whitespace(th.get_text(" ", strip=True))
                ]

            rows: List[List[str]] = []
            for tr in table.find_all("tr"):
                row = [
                    ContentProcessor.normalize_whitespace(td.get_text(" ", strip=True))
                    for td in tr.find_all(["td", "th"])
                    if ContentProcessor.normalize_whitespace(td.get_text(" ", strip=True))
                ]
                if row:
                    rows.append(row)

            if not rows and not headers:
                continue

            summary_text = "Table" + (f": {caption}" if caption else "") + ". " + " | ".join("/".join(row) for row in rows[:5])
            tables.append({
                "caption": caption,
                "headers": headers,
                "rows": rows,
                "summary": ContentProcessor.normalize_whitespace(summary_text),
                "heading_path": [],
            })
        return tables

    @staticmethod
    def extract_cards(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        cards: List[Dict[str, Any]] = []
        for candidate in soup.select(
            ".card, .feature, .panel, .tile, .spotlight, .stat, .project, .service, .testimonial, article, .entry"
        ):
            title = ""
            title_tag = candidate.find(["h2", "h3", "h4", "h5", "h6", "strong"])
            if title_tag:
                title = ContentProcessor.normalize_whitespace(title_tag.get_text(" ", strip=True))

            text_parts = []
            for element in candidate.find_all(["p", "li", "span"]):
                text = ContentProcessor.normalize_whitespace(element.get_text(" ", strip=True))
                if text and not ContentProcessor.is_likely_boilerplate(text):
                    text_parts.append(text)

            body = " ".join(text_parts)
            if not body and not title:
                continue
            if ContentProcessor.text_word_count(body) < 5 and not title:
                continue

            card = {
                "title": title,
                "description": body[:500],
                "heading_path": [],
                "metadata": {
                    "classes": candidate.get("class", []),
                },
            }
            cards.append(card)
        return cards

    @staticmethod
    def extract_sections(soup: BeautifulSoup, source_url: str, page_title: str) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        for section in soup.find_all(["main", "article", "section"], recursive=True):
            heading = ""
            heading_tag = section.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            if heading_tag:
                heading = ContentProcessor.normalize_whitespace(heading_tag.get_text(" ", strip=True))

            content = []
            for tag in section.find_all(["p", "ul", "ol", "table"]):
                text = ContentProcessor.normalize_whitespace(tag.get_text(" ", strip=True))
                if text and not ContentProcessor.is_likely_boilerplate(text):
                    content.append(text)

            body = " ".join(content)
            if not body:
                continue
            if ContentProcessor.text_word_count(body) < 10:
                continue

            section_data = {
                "id": section.get("id") or "",
                "heading": heading or section.get("data-title") or page_title,
                "heading_path": [page_title, heading] if heading else [page_title],
                "content": body[:4000],
                "type": "section",
                "source_url": source_url,
                "page_title": page_title,
            }
            sections.append(section_data)
        return sections

    @staticmethod
    def extract_image_context(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        image_contexts: List[Dict[str, Any]] = []
        for img in soup.find_all("img"):
            alt = (img.get("alt") or "").strip()
            if not alt:
                continue
            parent = img.parent
            nearby = []
            if parent:
                for node in parent.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "span"]):
                    text = ContentProcessor.normalize_whitespace(node.get_text(" ", strip=True))
                    if text and text not in nearby:
                        nearby.append(text)
            image_contexts.append({
                "alt_text": alt,
                "nearby_text": " ".join(nearby[:5]),
            })
        return image_contexts

    @staticmethod
    def _score_relevance(section: Dict[str, Any]) -> float:
        heading_count = len(section.get("heading_path", []) or [])
        content_len = len(section.get("content", "") or "")
        density = ContentProcessor.text_word_count(section.get("content", "")) / max(1, len(section.get("content", "")))
        return min(1.0, (0.5 * heading_count / 5) + (0.4 * min(content_len / 2500, 1.0)) + (0.1 * min(density * 1000, 1.0)))

    @staticmethod
    def _score_quality(section: Dict[str, Any]) -> float:
        words = ContentProcessor.text_word_count(section.get("content", ""))
        has_structure = bool(section.get("heading_path")) or bool(section.get("tables")) or bool(section.get("cards"))
        return min(1.0, (0.5 * min(words / 150, 1.0)) + (0.3 if has_structure else 0.0) + (0.2 if len(section.get("content", "")) > 200 else 0.0))

    @staticmethod
    def process_html(html: str, source_url: Optional[str] = None, page_title: Optional[str] = None) -> Dict[str, Any]:
        if not html:
            raise ValueError("HTML content is empty")

        if len(html.encode("utf-8")) > ContentProcessor.MAX_HTML_BYTES:
            raise ValueError(f"HTML exceeds maximum supported size of {ContentProcessor.MAX_HTML_BYTES} bytes")

        soup = BeautifulSoup(html, "html.parser")
        ContentProcessor._strip_unwanted_nodes(soup)

        metadata = ContentProcessor.get_metadata(soup)
        title = page_title or metadata.get("title") or "Untitled Page"
        headings = ContentProcessor.extract_headings(soup)
        sections = ContentProcessor.extract_sections(soup, source_url or "", title)
        paragraphs = ContentProcessor.extract_paragraphs(soup)
        tables = ContentProcessor.extract_tables(soup)
        lists_data = ContentProcessor.extract_lists(soup)
        cards = ContentProcessor.extract_cards(soup)
        image_context = ContentProcessor.extract_image_context(soup)

        for section in sections:
            section["relevance_score"] = ContentProcessor._score_relevance(section)
            section["quality_score"] = ContentProcessor._score_quality(section)

        for table in tables:
            table["relevance_score"] = 0.95
            table["quality_score"] = 0.9

        for card in cards:
            card["relevance_score"] = 0.85
            card["quality_score"] = 0.8

        result = {
            "metadata": metadata,
            "page_title": title,
            "source_url": source_url or metadata.get("canonical_url") or "",
            "headings": headings,
            "sections": sections,
            "paragraphs": paragraphs,
            "tables": tables,
            "lists": lists_data,
            "cards": cards,
            "image_context": image_context,
            "text_density": {
                "paragraph_count": len(paragraphs),
                "section_count": len(sections),
                "table_count": len(tables),
                "card_count": len(cards),
            },
        }

        logger.info(
            "Processed HTML: headings=%s sections=%s paragraphs=%s tables=%s cards=%s images=%s",
            len(headings),
            len(sections),
            len(paragraphs),
            len(tables),
            len(cards),
            len(image_context),
        )
        return result

    @staticmethod
    def to_markdown(structure: Dict[str, Any]) -> str:
        lines: List[str] = []
        title = structure.get("page_title") or structure.get("metadata", {}).get("title") or "Document"
        lines.append(f"# {title}")

        for heading in structure.get("headings", []):
            level = heading.get("level", 1)
            prefix = "#" * min(level, 6)
            lines.append(f"{prefix} {heading.get('text', '')}")

        for section in structure.get("sections", []):
            heading_text = section.get("heading") or section.get("heading_path", ["Section"])[-1]
            lines.append(f"\n## {heading_text}\n")
            lines.append(section.get("content", ""))

        for table in structure.get("tables", []):
            lines.append(f"\n### {table.get('caption') or 'Table'}\n")
            if table.get("headers"):
                lines.append("| " + " | ".join(table["headers"]) + " |")
                lines.append("| " + " | ".join(["---"] * len(table["headers"])) + " |")
            for row in table.get("rows", [])[:10]:
                lines.append("| " + " | ".join(row) + " |")

        for card in structure.get("cards", []):
            title_text = card.get("title") or "Card"
            lines.append(f"\n## {title_text}\n")
            if card.get("description"):
                lines.append(card["description"])

        return "\n".join(part for part in lines if part is not None).strip()

    @staticmethod
    def hash_structure(structure: Dict[str, Any]) -> str:
        payload = json.dumps(structure, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
