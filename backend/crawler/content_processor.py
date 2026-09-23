from bs4 import BeautifulSoup, NavigableString, Tag
from typing import Dict, Any, List, Optional, Set, Tuple
import re
from urllib.parse import urlparse
import logging
import json
import hashlib
import os
from collections import Counter, defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


class ContentProcessor:
    """
    ONE AND ONLY HTML extraction stage - DOM-based only.

    Architecture:
    - No page-level classification. A page is almost never one shape.
    - Every meaningful DOM block is classified individually (article text,
      table, list, card grid, product detail, howto step, image, json-ld).
    - Each block is routed to the appropriate extractor.
    - All results merge into one document_structure.

    Universal collection detection:
    - After extraction, we run a collection detector that looks at the whole
      document and asks "does this page contain a ranked/numbered list of
      sibling items?" — whether they are <li>, <tr>, card <div>s, or sibling
      <section>s under a shared parent.
    - Detected items get an `ordinal_index` (0-based, document order) written
      into their dict. Non-collection items get `ordinal_index = None`.
    - Each detected collection occupies its own ordinal *namespace*, so
      different collections on the same page never collide. A 52-item
      section collection and a 5-item nav list on the same page will not
      both claim ordinal 0.
    - Downstream (chunker, retrieval) uses `ordinal_index` as the universal
      signal for "this is item N of a list" without caring about the
      underlying DOM shape or entity_type.

    Responsibilities:
    - Remove non-content tags (script, style, iframe)
    - Preserve and parse JSON-LD, SVG text, data-* attributes
    - Extract ALL visible content from DOM (no filtering by length)
    - Preserve structure (headings, sections, tables, lists, cards)
    - No Trafilatura/Readability - pure DOM extraction
    - Populate structured fields (tables, lists, cards, media)
    - Create dedicated chunks for contentful media assets only
    - Assign ordinal_index to every item in a detected collection
    - Merge adjacent label/value pairs (e.g. "Rank" / "1" -> "Rank: 1")
      so stat-block pages keep their structured data
    - Deduplicate sections by leaf heading, keeping the richest variant
      when the same entity is rendered in multiple places (e.g. mobile
      + desktop layouts, main list + related widget).

    Does NOT:
    - Filter by length (keep "Price: $99", "Status: Active")
    - Remove content based on heuristics
    - Apply chunking logic
    - Classify the page as a single type
    """

    # Tags with NO visible content - safe to remove
    NON_CONTENT_TAGS = {
        'script', 'style', 'noscript', 'iframe',
        'meta', 'link', 'head', 'template'
    }
    # SVG kept for text extraction

    # Media chunk creation thresholds — must match EnhancedChunker
    MIN_IMAGE_DESC_CHARS = 80
    DECORATIVE_IMAGE_SIGNALS = (
        'icon', 'emoji', 'logo', 'sprite', 'favicon',
        'spacer', 'pixel', 'blank.', 'transparent.',
        'confetti', 'arrow', 'bullet', 'chevron', 'divider',
    )

    # Headings that are really just boilerplate labels — ignore them
    # when picking the "nearest heading" for a table/list.
    NON_HEADING_PATTERNS = (
        'updated every', 'last updated', 'learn more', 'read more',
        'sign up', 'subscribe', 'follow us', 'share this',
        'posted on', 'published on', 'written by',
    )

    # Minimum words a candidate content root must contain to be selected.
    CONTENT_ROOT_MIN_WORDS = 200

    # Minimum chars a section must contain to be emitted.
    MIN_SECTION_CHARS = 30

    # Minimum chars for a single text node to be kept during section
    # extraction. Set to 1 so single-character numeric values like "1"
    # aren't dropped. The whole-section MIN_SECTION_CHARS filter is what
    # actually prevents emitting a section made of nothing but fragments.
    MIN_TEXT_BLOCK_CHARS = 1

    # Tags whose entire subtree is *always* extracted by other passes.
    # Text nodes inside them are never appended to a section.
    # <table> and <ul>/<ol> are NOT in this set — they're handled
    # conditionally in _inside_other_extractor_subtree, because tables
    # can be layout wrappers and lists can be nav widgets.
    ALWAYS_OWNED_SUBTREES = frozenset({
        'svg', 'script', 'style', 'noscript', 'iframe',
    })

    # Minimum direct <li> children for a <ul>/<ol> subtree to be treated
    # as "owned" by the list extractor. Below this, we let the section
    # walker descend into it — it's a nav list or a stat list, not a
    # content list.
    MIN_LIST_ITEMS_FOR_OWNERSHIP = 5

    # A short non-word fragment (like "0", "-", "•") inside an unclassed
    # <div> with no content-bearing children is almost always a
    # decorative icon placeholder. We skip those so they don't break
    # the label/value pairing of nearby stats.
    MAX_DECORATION_FRAGMENT_CHARS = 3

    # Lookahead window for the label/value merger. When a numeric or
    # label fragment is followed by a short run of noise before its
    # partner (e.g. "1", "0", "Rank"), the merger looks up to this many
    # positions ahead to find the matching partner.
    LABEL_VALUE_LOOKAHEAD = 2

    # Minimum items in a repeating sibling group before it is considered
    # a "card grid" rather than a random list of blocks.
    MIN_REPEATED_SIBLINGS_FOR_GRID = 4

    # Minimum ratio of sibling tag frequency to total siblings for a
    # repeating structure to be considered a card grid.
    REPEATED_SIBLING_RATIO = 0.6

    # ------------------------------------------------------------------
    # LABEL / VALUE PAIRING
    # ------------------------------------------------------------------
    # When a page renders structured stats as separate short text fragments
    # (e.g. <span>1</span><span>Rank</span>), the section extractor sees
    # them as two adjacent lines. We merge them into "Label: value"
    # so the chunk carries the relationship, not just the raw fragments.
    #
    # These regexes are deliberately tight so prose isn't mangled:
    #   - numeric: matches "1", "15,239", "3.14", "-5", "50%"
    #   - label:   matches short alphabetic strings up to 30 chars
    LABEL_VALUE_NUMERIC = re.compile(r'^-?[\d,]+(?:\.\d+)?%?$')
    LABEL_VALUE_LABEL = re.compile(r"^[A-Za-z][A-Za-z' \-]{0,29}$")

    # Matches a "Label: value" line inside section content. Used by the
    # deduplication pass to score how structured a section is.
    LABEL_VALUE_LINE = re.compile(
        r"(?m)^[A-Za-z][A-Za-z' \-]{1,25}:\s*-?[\d,]+(?:\.\d+)?%?\s*$"
    )

    # ------------------------------------------------------------------
    # COLLECTION DETECTION
    # ------------------------------------------------------------------
    # A "collection" is a set of sibling items that share a parent and
    # represent a ranked or numbered series (top N, list of X, etc.).
    #
    # Thresholds below control how aggressive we are at treating
    # extracted data as a collection vs. as individual pieces.
    #
    # NOTE on MIN_LIST_ITEMS_FOR_COLLECTION: raised from 5 to 8. A 5- or
    # 6-item <ul> is almost always a nav widget, a sidebar "related links"
    # list, or a footer menu. Treating those as ordinal collections causes
    # them to collide with the page's real collection (e.g. a 50-item
    # ranked list rendered as sibling sections). If you have a page whose
    # only collection is a 5-item top-5 list, drop this back to 5.
    MIN_SECTION_SIBLINGS_FOR_COLLECTION = 10
    MIN_LIST_ITEMS_FOR_COLLECTION = 8
    MIN_TABLE_ROWS_FOR_COLLECTION = 3
    MIN_CARDS_FOR_COLLECTION = 4

    # Debug logging
    DEBUG_ENABLED = True
    DEBUG_LOG_PATH = "/app/debug_content_processing.log"

    # ============================================================
    # Debug + utilities
    # ============================================================

    @classmethod
    def _debug_log(cls, message: str, data: Any = None):
        if not cls.DEBUG_ENABLED:
            return
        timestamp = datetime.now().isoformat()
        log_entry = f"\n{'='*80}\n[{timestamp}] {message}\n"
        if data is not None:
            if isinstance(data, str):
                log_entry += data
            elif isinstance(data, (dict, list)):
                try:
                    log_entry += json.dumps(data, indent=2, default=str)
                except Exception:
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
    def _cell_text(cell) -> str:
        if cell is None:
            return ""
        text = cell.get_text(separator=' ', strip=True)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _row_text(row) -> str:
        if row is None:
            return ""
        lines: List[str] = []
        for cell in row.find_all(['td', 'th'], recursive=False):
            text = ContentProcessor._cell_text(cell)
            if text:
                lines.append(text)
        if not lines:
            text = ContentProcessor._cell_text(row)
            return text
        return "\n".join(lines)

    @staticmethod
    def _is_junk(text: str) -> bool:
        if not text:
            return True
        cleaned = text.strip().lower()
        if not cleaned:
            return True
        if len(cleaned) <= 1:
            return True
        if all(c in '.,;:!?()[]{}"\' \n\t' for c in text):
            return True
        return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r'\s+', ' ', text or '').strip().lower()

    @staticmethod
    def _section_key(section: Dict[str, Any]) -> str:
        content = section.get('content', '')
        return re.sub(r'\s+', ' ', content).strip().lower()

    @classmethod
    def _merge_sections(cls, *section_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

    @classmethod
    def _dedupe_sections_by_leaf(
        cls, sections: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Deduplicate sections that share the same leaf heading.

        Some pages render the same logical entity more than once — for
        example a mobile layout and a desktop layout, or a primary list
        plus a "related items" widget. When that happens, one variant
        usually carries more structured data (numeric labels, key/value
        pairs) than the other. We keep the richest variant and drop the
        rest, preserving the original emission order.

        The leaf heading is the last element of `heading_path`. Sections
        without a heading_path are left untouched (they can't be keyed).
        """
        if not sections:
            return sections

        def leaf(section: Dict[str, Any]) -> str:
            path = section.get("heading_path") or []
            if not path:
                return ""
            return str(path[-1]).strip()

        def richness(section: Dict[str, Any]) -> Tuple[int, int, int]:
            content = section.get("content") or ""
            # Number of "Label: value" lines — the strongest signal that
            # this variant carries structured stats.
            pairs = len(cls.LABEL_VALUE_LINE.findall(content))
            # Word count — longer content usually means more complete.
            words = len(content.split())
            # Path depth — prefer the deeper variant when richness ties,
            # because a nested path usually means the entity is in its
            # canonical position, not a sidebar echo.
            depth = len(section.get("heading_path") or [])
            return (pairs, words, depth)

        # Track the winner per leaf heading.
        best_by_leaf: Dict[str, Dict[str, Any]] = {}
        # Sections with no leaf heading get a synthetic unique key so
        # they're never compared against each other.
        unkeyed: List[Dict[str, Any]] = []

        for s in sections:
            k = leaf(s)
            if not k:
                unkeyed.append(s)
                continue
            cur = best_by_leaf.get(k)
            if cur is None or richness(s) > richness(cur):
                best_by_leaf[k] = s

        # Preserve emission order: walk the original list and keep a
        # section only if it is the winner for its leaf.
        winners = {id(v) for v in best_by_leaf.values()}
        result: List[Dict[str, Any]] = []
        for s in sections:
            k = leaf(s)
            if not k or id(s) in winners:
                result.append(s)
        # Unkeyed sections are already included above by the `not k`
        # branch; the explicit list is kept for clarity in future edits.
        return result

    @classmethod
    def _looks_like_heading(cls, text: str) -> bool:
        if not text:
            return False
        if len(text) > 120:
            return False
        lower = text.lower()
        if any(bad in lower for bad in cls.NON_HEADING_PATTERNS):
            return False
        if re.match(r'^\d{4}-\d{2}-\d{2}', text):
            return False
        if re.match(r'^[A-Z][a-z]+ \d{1,2}, \d{4}', text):
            return False
        return True

    @classmethod
    def _nearest_heading(cls, element) -> str:
        if element is None:
            return ""
        for prev in element.find_all_previous(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            text = prev.get_text(strip=True)
            if text and cls._looks_like_heading(text):
                return text
        return ""

    @classmethod
    def _merge_label_value_pairs(cls, blocks: List[str]) -> List[str]:
        """
        Collapse adjacent (label, value) or (value, label) text fragments
        into single "Label: value" strings.

        The section walker produces a flat list of text fragments in
        document order. Stat-block pages render as a sequence like:

            "1", "Rank", "15,239", "Mentions"

        The merger turns that into:

            "Rank: 1", "Mentions: 15,239"

        so the chunk carries the relationship, not the fragments.

        Some pages put a small amount of noise between a value and its
        label (e.g. a decorative "0" from an icon placeholder):

            "1", "0", "Rank", "15,239", "Mentions"

        To handle that, the merger looks ahead up to LABEL_VALUE_LOOKAHEAD
        positions for a matching partner, skipping intermediate fragments
        that form no valid pair. Fragments that are skipped over as noise
        are discarded.

        Merges only when one side is a bare number and the other side is
        a short alphabetic label. Long prose and mixed strings pass
        through unchanged. This is deliberately conservative so it can't
        mangle paragraphs.
        """
        if not blocks:
            return blocks

        # Precompute the type of each block once so the loop is cheap.
        kinds: List[str] = []  # "num", "label", or "other"
        for b in blocks:
            s = b.strip()
            if cls.LABEL_VALUE_NUMERIC.match(s):
                kinds.append("num")
            elif cls.LABEL_VALUE_LABEL.match(s):
                kinds.append("label")
            else:
                kinds.append("other")

        merged: List[str] = []
        i = 0
        n = len(blocks)
        while i < n:
            cur = blocks[i].strip()
            cur_kind = kinds[i]

            # Only fragments that could form a pair are worth looking
            # ahead for. Pure noise ("other") just passes through.
            if cur_kind == "other":
                merged.append(cur)
                i += 1
                continue

            paired = False
            lookahead_end = min(i + 1 + cls.LABEL_VALUE_LOOKAHEAD, n)
            for j in range(i + 1, lookahead_end):
                nxt = blocks[j].strip()
                nxt_kind = kinds[j]

                # value then label  ->  "Label: value"
                if cur_kind == "num" and nxt_kind == "label":
                    merged.append(f"{nxt}: {cur}")
                    i = j + 1
                    paired = True
                    break
                # label then value  ->  "Label: value"
                if cur_kind == "label" and nxt_kind == "num":
                    merged.append(f"{cur}: {nxt}")
                    i = j + 1
                    paired = True
                    break

                # If we hit a fragment that itself looks like a strong
                # boundary (a long prose line), stop looking — we don't
                # want to bridge across paragraphs.
                if nxt_kind == "other" and len(nxt) > 40:
                    break

            if not paired:
                merged.append(cur)
                i += 1

        return merged

    # ============================================================
    # STRUCTURED DATA EXTRACTION
    # ============================================================

    @classmethod
    def _extract_json_ld(cls, soup: BeautifulSoup) -> Optional[str]:
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
            except Exception:
                pass
        return '\n\n'.join(json_ld_parts) if json_ld_parts else None

    @classmethod
    def _extract_json_ld_entities(cls, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []

        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
                return
            if not isinstance(value, dict):
                return
            graph = value.get("@graph")
            if graph:
                collect(graph)
            type_value = value.get("@type", "")
            types = type_value if isinstance(type_value, list) else [type_value]
            normalized_types = {str(item).lower() for item in types}
            if "product" in normalized_types or any("product" in item for item in normalized_types):
                entities.append({
                    "entity_type": "product",
                    "name": value.get("name") or value.get("headline") or "",
                    "description": value.get("description") or "",
                    "sku": value.get("sku") or value.get("mpn") or "",
                    "url": value.get("url") or "",
                    "price": (value.get("offers") or {}).get("price", "")
                    if isinstance(value.get("offers"), dict) else "",
                    "currency": (value.get("offers") or {}).get("priceCurrency", "")
                    if isinstance(value.get("offers"), dict) else "",
                })

        for script in soup.find_all('script', type='application/ld+json'):
            try:
                collect(json.loads(script.string or ""))
            except Exception:
                continue
        return [entity for entity in entities if entity.get("name") or entity.get("description")]

    @classmethod
    def _format_json_ld(cls, data: dict) -> str:
        parts: List[str] = []
        type_ = data.get('@type', '')
        if isinstance(type_, list):
            type_ = ', '.join(str(t) for t in type_)
        if type_:
            parts.append(f"Type: {type_}")

        for field in ['name', 'headline', 'title', 'description', 'about', 'abstract']:
            value = data.get(field)
            if value:
                parts.append(f"{field.title()}: {value}")

        type_lower = (type_ or '').lower()

        if 'product' in type_lower:
            cls._format_product_ld(data, parts)
        elif 'recipe' in type_lower:
            cls._format_recipe_ld(data, parts)
        elif 'faqpage' in type_lower:
            cls._format_faq_ld(data, parts)
        elif 'howto' in type_lower:
            cls._format_howto_ld(data, parts)
        elif 'event' in type_lower:
            cls._format_event_ld(data, parts)
        elif 'review' in type_lower:
            cls._format_review_ld(data, parts)
        elif any(t in type_lower for t in ('article', 'newsarticle', 'blogposting')):
            cls._format_article_ld(data, parts)
        elif any(t in type_lower for t in ('person', 'organization')):
            cls._format_person_org_ld(data, parts)
        else:
            cls._format_generic_ld(data, parts)

        if 'offers' in data and 'product' not in type_lower and 'event' not in type_lower:
            cls._append_offers(data.get('offers'), parts)

        return '\n'.join(parts) if parts else ''

    @staticmethod
    def _append_offers(offers, parts: List[str]) -> None:
        if not offers:
            return
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list):
            return
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            price = offer.get('price')
            currency = offer.get('priceCurrency', '')
            availability = offer.get('availability', '')
            line = "Price:"
            if price is not None:
                line += f" {price}"
            if currency:
                line += f" {currency}"
            if availability:
                line += f" ({availability})"
            parts.append(line.strip())

    @classmethod
    def _format_product_ld(cls, data: dict, parts: List[str]) -> None:
        brand = data.get('brand')
        if isinstance(brand, dict) and brand.get('name'):
            parts.append(f"Brand: {brand['name']}")
        elif isinstance(brand, str):
            parts.append(f"Brand: {brand}")
        for field in ('sku', 'gtin', 'mpn'):
            if data.get(field):
                parts.append(f"{field.upper()}: {data[field]}")
        rating = data.get('aggregateRating')
        if isinstance(rating, dict):
            rv = rating.get('ratingValue')
            rc = rating.get('reviewCount') or rating.get('ratingCount')
            if rv:
                parts.append(f"Rating: {rv}" + (f" ({rc} reviews)" if rc else ""))
        if data.get('url'):
            parts.append(f"URL: {data['url']}")
        image = data.get('image')
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get('url')
        if image:
            parts.append(f"Image: {image}")
        cls._append_offers(data.get('offers'), parts)

    @classmethod
    def _format_article_ld(cls, data: dict, parts: List[str]) -> None:
        author = data.get('author')
        if isinstance(author, dict):
            author = author.get('name')
        elif isinstance(author, list):
            author = ", ".join(a.get('name', '') for a in author if isinstance(a, dict))
        if author:
            parts.append(f"Author: {author}")
        publisher = data.get('publisher')
        if isinstance(publisher, dict):
            publisher = publisher.get('name')
        if publisher:
            parts.append(f"Publisher: {publisher}")
        for field in ('datePublished', 'dateModified', 'articleSection'):
            if data.get(field):
                parts.append(f"{field}: {data[field]}")
        if data.get('wordCount'):
            parts.append(f"WordCount: {data['wordCount']}")
        keywords = data.get('keywords')
        if isinstance(keywords, list):
            keywords = ', '.join(str(k) for k in keywords)
        if keywords:
            parts.append(f"Keywords: {keywords}")

    @classmethod
    def _format_faq_ld(cls, data: dict, parts: List[str]) -> None:
        main_entity = data.get('mainEntity') or []
        if isinstance(main_entity, dict):
            main_entity = [main_entity]
        for item in main_entity:
            if not isinstance(item, dict):
                continue
            q = item.get('name', '')
            accepted = item.get('acceptedAnswer') or {}
            if isinstance(accepted, dict):
                a = accepted.get('text', '')
            else:
                a = str(accepted)
            if q:
                parts.append(f"Q: {q}")
            if a:
                parts.append(f"A: {a}")

    @classmethod
    def _format_howto_ld(cls, data: dict, parts: List[str]) -> None:
        if data.get('totalTime'):
            parts.append(f"TotalTime: {data['totalTime']}")
        steps = data.get('step') or []
        if isinstance(steps, dict):
            steps = [steps]
        for i, step in enumerate(steps, 1):
            if isinstance(step, dict):
                name = step.get('name') or step.get('text') or ''
                if name:
                    parts.append(f"Step {i}: {name}")

    @classmethod
    def _format_recipe_ld(cls, data: dict, parts: List[str]) -> None:
        for field in ('prepTime', 'cookTime', 'totalTime', 'recipeYield'):
            if data.get(field):
                parts.append(f"{field}: {data[field]}")
        ingredients = data.get('recipeIngredient') or data.get('ingredients') or []
        if isinstance(ingredients, list) and ingredients:
            parts.append("Ingredients:")
            for ing in ingredients:
                parts.append(f"  - {ing}")
        instructions = data.get('recipeInstructions') or []
        if isinstance(instructions, dict):
            instructions = [instructions]
        if isinstance(instructions, list) and instructions:
            parts.append("Instructions:")
            for i, step in enumerate(instructions, 1):
                if isinstance(step, dict):
                    text = step.get('text') or step.get('name') or ''
                else:
                    text = str(step)
                if text:
                    parts.append(f"  {i}. {text}")

    @classmethod
    def _format_event_ld(cls, data: dict, parts: List[str]) -> None:
        for field in ('startDate', 'endDate', 'eventStatus', 'eventAttendanceMode'):
            if data.get(field):
                parts.append(f"{field}: {data[field]}")
        location = data.get('location')
        if isinstance(location, dict):
            loc_name = location.get('name')
            if loc_name:
                parts.append(f"Location: {loc_name}")
        organizer = data.get('organizer')
        if isinstance(organizer, dict):
            org_name = organizer.get('name')
            if org_name:
                parts.append(f"Organizer: {org_name}")
        cls._append_offers(data.get('offers'), parts)

    @classmethod
    def _format_review_ld(cls, data: dict, parts: List[str]) -> None:
        item = data.get('itemReviewed')
        if isinstance(item, dict):
            if item.get('name'):
                parts.append(f"ItemReviewed: {item['name']}")
        elif isinstance(item, str):
            parts.append(f"ItemReviewed: {item}")
        author = data.get('author')
        if isinstance(author, dict):
            author = author.get('name')
        if author:
            parts.append(f"Author: {author}")
        rating = data.get('reviewRating')
        if isinstance(rating, dict) and rating.get('ratingValue'):
            parts.append(f"Rating: {rating['ratingValue']}")
        if data.get('reviewBody'):
            parts.append(f"Review: {data['reviewBody']}")

    @classmethod
    def _format_person_org_ld(cls, data: dict, parts: List[str]) -> None:
        if data.get('url'):
            parts.append(f"URL: {data['url']}")
        same_as = data.get('sameAs')
        if isinstance(same_as, list):
            for link in same_as[:5]:
                parts.append(f"SameAs: {link}")
        elif isinstance(same_as, str):
            parts.append(f"SameAs: {same_as}")

    @classmethod
    def _format_generic_ld(cls, data: dict, parts: List[str], _depth: int = 0) -> None:
        if _depth > 3 or not isinstance(data, dict):
            return
        skip_keys = {'@context', '@id', 'potentialAction', 'mainEntityOfPage'}
        for key, value in data.items():
            if key in skip_keys:
                continue
            if key in ('name', 'headline', 'title', 'description', 'about', 'abstract'):
                continue
            if isinstance(value, (str, int, float)):
                s = str(value)
                if 0 < len(s) <= 500 and s.strip():
                    parts.append(f"{key}: {s}")
            elif isinstance(value, dict):
                cls._format_generic_ld(value, parts, _depth + 1)
            elif isinstance(value, list):
                for item in value[:10]:
                    if isinstance(item, (str, int, float)):
                        s = str(item)
                        if 0 < len(s) <= 500 and s.strip():
                            parts.append(f"{key}: {s}")
                    elif isinstance(item, dict):
                        cls._format_generic_ld(item, parts, _depth + 1)

    @classmethod
    def _extract_svg_text(cls, soup: BeautifulSoup) -> Optional[str]:
        svg_texts = []
        for svg in soup.find_all('svg'):
            text = svg.get_text(separator=' ', strip=True)
            if text:
                svg_texts.append(text)
            title = svg.find('title')
            if title:
                svg_texts.append(f"SVG Title: {title.get_text(strip=True)}")
            desc = svg.find('desc')
            if desc:
                svg_texts.append(f"SVG Description: {desc.get_text(strip=True)}")
        return '\n'.join(svg_texts) if svg_texts else None

    @classmethod
    def _extract_data_attributes(cls, soup: BeautifulSoup) -> Dict[str, Any]:
        data_attrs = {}
        try:
            for element in soup.find_all():
                if hasattr(element, 'attrs'):
                    for key, value in element.attrs.items():
                        if key.startswith('data-'):
                            if key not in data_attrs:
                                data_attrs[key] = []
                            if isinstance(value, list):
                                data_attrs[key].extend(str(v) for v in value if v is not None)
                            else:
                                data_attrs[key].append(str(value))
        except Exception as e:
            logger.warning(f"Error extracting data attributes: {e}")
        return data_attrs

    # ============================================================
    # TABLE EXTRACTION
    # ============================================================

    @classmethod
    def _is_data_table(cls, table) -> bool:
        import statistics

        rows = table.find_all('tr')
        if len(rows) < 2:
            return False

        row_cell_counts = []
        for tr in rows:
            n = len(tr.find_all(['td', 'th'], recursive=False))
            if n > 0:
                row_cell_counts.append(n)
        if not row_cell_counts:
            return False
        if len(row_cell_counts) < 2:
            return False

        try:
            mode_cols = statistics.mode(row_cell_counts)
        except statistics.StatisticsError:
            return False
        consistency = row_cell_counts.count(mode_cols) / len(row_cell_counts)
        if consistency < 0.6:
            return False

        if mode_cols < 2 or mode_cols > 20:
            return False

        cells = table.find_all(['td', 'th'])
        cell_texts = [c.get_text(strip=True) for c in cells]
        cell_texts = [t for t in cell_texts if t]
        if not cell_texts:
            return False

        median_len = statistics.median(len(t) for t in cell_texts)
        max_len = max(len(t) for t in cell_texts)
        if median_len > 120:
            return False
        if max_len > 1500:
            return False

        block_tags = table.find_all(
            ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'article', 'section', 'img'],
            recursive=True,
        )
        block_count = len(block_tags)
        cell_count = len(cells)
        if cell_count > 0 and block_count / cell_count > 0.5:
            return False

        return True

    @classmethod
    def _extract_tables_structured(cls, soup: BeautifulSoup) -> List[Dict]:
        tables = []
        for table in soup.find_all('table')[:100]:
            if not cls._is_data_table(table):
                continue
            try:
                headers: List[str] = []
                rows_data: List[List[str]] = []
                row_texts: List[str] = []
                headers_from_first_row = False

                thead = table.find('thead')
                if thead:
                    for th in thead.find_all(['th', 'td']):
                        headers.append(cls._cell_text(th))
                else:
                    first_row = table.find('tr')
                    if first_row:
                        for th in first_row.find_all(['th', 'td']):
                            headers.append(cls._cell_text(th))
                        headers_from_first_row = True

                for idx, tr in enumerate(table.find_all('tr')):
                    if headers_from_first_row and idx == 0:
                        continue

                    row_text = cls._row_text(tr)
                    if row_text and not cls._is_junk(row_text):
                        row_texts.append(row_text)

                    cells = []
                    for td in tr.find_all(['td', 'th']):
                        cells.append(cls._cell_text(td))
                    if cells and any(c.strip() for c in cells):
                        rows_data.append(cells)

                if headers or rows_data:
                    heading = cls._nearest_heading(table)
                    tables.append({
                        'headers': headers,
                        'rows': rows_data,
                        'row_texts': row_texts,
                        'row_count': len(rows_data),
                        'col_count': len(headers),
                        'heading': heading,
                        'heading_path': [heading] if heading else [],
                    })
            except Exception:
                pass
        return tables

    # ============================================================
    # LIST EXTRACTION
    # ============================================================

    @classmethod
    def _extract_lists_structured(cls, soup: BeautifulSoup) -> List[Dict]:
        lists = []
        for list_elem in soup.find_all(['ul', 'ol']):
            try:
                items = []
                for li in list_elem.find_all('li', recursive=False):
                    text = cls._cell_text(li)
                    if text:
                        items.append(text)
                if items:
                    heading = cls._nearest_heading(list_elem)
                    lists.append({
                        'type': 'ordered' if list_elem.name == 'ol' else 'unordered',
                        'items': items,
                        'item_count': len(items),
                        'heading': heading,
                        'heading_path': [heading] if heading else [],
                    })
            except Exception:
                pass
        return lists

    # ============================================================
    # SECTION EXTRACTION
    # ============================================================

    CONTENT_ROOT_SELECTORS = (
        'main',
        'article',
        '[role="main"]',
        '#content', '#main', '#main-content',
        '#article-body', '#post-body', '#entry-content', '#page-content',
        '.content', '.main-content', '.post-content',
        '.article-body', '.entry-content', '.post-body',
        '.container',
    )

    @classmethod
    def _find_content_root(cls, soup: BeautifulSoup):
        for selector in cls.CONTENT_ROOT_SELECTORS:
            try:
                candidates = soup.select(selector)
            except Exception:
                continue
            for el in candidates:
                text = el.get_text(separator=' ', strip=True)
                if len(text.split()) >= cls.CONTENT_ROOT_MIN_WORDS:
                    return el
        body = soup.find('body')
        return body if body else soup

    @staticmethod
    def _in_skipped_region(element) -> bool:
        SKIP_ANCESTORS = {'nav', 'header', 'footer', 'aside'}
        SKIP_ROLES = {'navigation', 'banner', 'contentinfo'}
        for ancestor in element.parents:
            if getattr(ancestor, 'name', None) in SKIP_ANCESTORS:
                return True
            if getattr(ancestor, 'attrs', None):
                role = ancestor.attrs.get('role')
                if role in SKIP_ROLES:
                    return True
        return False

    @classmethod
    def _inside_other_extractor_subtree(cls, node) -> bool:
        """
        True if the given text node lives inside a subtree that is
        extracted by a different pass (table, list, svg, script, style).

        The check for <table> and <ul>/<ol> is conditional:

        - A <table> subtree is "owned" only if that specific table looks
          like a real data table (passes _is_data_table). Layout tables
          — very common on marketing pages and legacy sites — fall
          through to the section walker, which extracts their text into
          the enclosing section.
        - A <ul>/<ol> subtree is "owned" only if it has at least
          MIN_LIST_ITEMS_FOR_OWNERSHIP direct <li> children. Nav widgets
          and small stat lists fall through.

        <svg>, <script>, <style>, <noscript>, and <iframe> are always
        owned.
        """
        parent = node.parent if isinstance(node, NavigableString) else node
        if parent is None:
            return False
        for ancestor in parent.parents:
            name = getattr(ancestor, 'name', None)
            if name is None:
                continue

            # Always-owned subtrees.
            if name in ContentProcessor.ALWAYS_OWNED_SUBTREES:
                return True

            # Tables: only owned if this specific table is a data table.
            if name == 'table':
                try:
                    if ContentProcessor._is_data_table(ancestor):
                        return True
                except Exception:
                    pass
                # Layout table — keep walking up.
                continue

            # Lists: only owned if large enough to be a content list.
            if name in ('ul', 'ol'):
                try:
                    li_children = ancestor.find_all('li', recursive=False)
                    if len(li_children) >= ContentProcessor.MIN_LIST_ITEMS_FOR_OWNERSHIP:
                        return True
                except Exception:
                    pass
                # Nav or stat list — keep walking up.
                continue

        return False

    @staticmethod
    def _is_decorative_fragment(text: str, parent) -> bool:
        """
        True if a short, non-word fragment (like "0", "-", "•") sits in an
        unclassed <div> with no content-bearing children. These are icon
        placeholders or hidden counters; keeping them breaks the
        label/value pairing of nearby stats.
        """
        if parent is None:
            return False
        if getattr(parent, 'name', None) != 'div':
            return False
        if parent.get('class'):
            return False
        if len(text) > ContentProcessor.MAX_DECORATION_FRAGMENT_CHARS:
            return False
        if text.isalpha():
            return False
        # If the div has content-bearing children, this isn't a leaf
        # decoration — it's a layout wrapper and the text may be real.
        if parent.find(['p', 'span', 'a', 'img', 'svg', 'ul', 'ol', 'table']):
            return False
        return True

    @staticmethod
    def _has_table_descendant(element) -> bool:
        for t in element.find_all('table'):
            try:
                if ContentProcessor._is_data_table(t):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _has_list_descendant(element) -> bool:
        for lst in element.find_all(['ul', 'ol']):
            items = lst.find_all('li', recursive=False)
            if len(items) >= 3:
                return True
        return False

    @staticmethod
    def _has_card_descendant(element) -> bool:
        return ContentProcessor._detect_card_group_in(element) is not None

    @classmethod
    def _detect_card_group_in(cls, element) -> Optional[List]:
        for container in element.find_all(['div', 'ul', 'section'], recursive=True):
            children = [c for c in container.children if hasattr(c, 'name') and c.name]
            if len(children) < cls.MIN_REPEATED_SIBLINGS_FOR_GRID:
                continue
            sigs = Counter()
            for c in children:
                classes = ' '.join(sorted(c.get('class', []) or []))
                sigs[(c.name, classes)] += 1
            if not sigs:
                continue
            top_sig, top_count = sigs.most_common(1)[0]
            if top_count < cls.MIN_REPEATED_SIBLINGS_FOR_GRID:
                continue
            if top_count / len(children) < cls.REPEATED_SIBLING_RATIO:
                continue
            matches = [c for c in children
                       if (c.name, ' '.join(sorted(c.get('class', []) or []))) == top_sig]
            if cls._validate_card_container(matches):
                return matches
        return None

    # ============================================================
    # CARD EXTRACTION
    # ============================================================

    GENERIC_LAYOUT_CLASSES = {
        'col', 'row', 'container', 'wrapper', 'flex', 'grid',
        'active', 'hidden', 'clearfix', 'pull-left', 'pull-right',
        'col-md-4', 'col-sm-6', 'col-lg-3', 'col-xs-12',
        'flex-item', 'grid-item', 'swiper-slide', 'slick-slide',
        'item', 'card', 'block', 'module', 'widget',
        'entry', 'post',
    }

    @classmethod
    def _validate_card_container(cls, elements: List) -> bool:
        if not elements:
            return False
        valid = 0
        checked = min(len(elements), 10)
        for el in elements[:checked]:
            text = el.get_text(strip=True)
            if len(text) < 10:
                continue
            has_link = el.find('a', href=True) is not None
            has_heading = el.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']) is not None
            has_price = bool(re.search(r'[$£€]\s?\d', text))
            if has_link or has_heading or has_price:
                valid += 1
        return valid >= max(1, checked // 2)

    @classmethod
    def _extract_cards_structured(cls, soup: BeautifulSoup) -> List[Dict]:
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

        for card in card_elements[:200]:
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
                        'entity_type': 'product' if price or 'product' in str(card.get('class', '')).lower() else 'card',
                        'is_product': bool(price or 'product' in str(card.get('class', '')).lower()),
                        'text': card.get_text(separator=' ', strip=True)
                    })
            except Exception:
                pass
        return cards

    @classmethod
    def _card_element_to_section(
        cls, card, url: str, page_title: str,
    ) -> Optional[Dict[str, Any]]:
        name_el = (
            card.select_one('h1, h2, h3, h4, h5, h6') or
            card.select_one('[class*="title"], [class*="name"], [class*="model"]') or
            card.select_one('a')
        )
        name = name_el.get_text(strip=True) if name_el else ''
        if not name:
            lines = [l.strip() for l in card.get_text('\n', strip=True).split('\n') if l.strip()]
            name = lines[0] if lines else 'Card'

        card_text = cls._cell_text(card)
        price_el = card.select_one('[class*="price"], [data-price], .price')
        desc_el = card.select_one('[class*="desc"], [class*="summary"], p')
        link_el = card.select_one('a[href]')

        price = price_el.get_text(strip=True) if price_el else ''
        desc = desc_el.get_text(strip=True) if desc_el else ''
        href = link_el.get('href', '') if link_el else ''

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
            card_text_without_fields = card_text
            for part in content_parts[1:]:
                card_text_without_fields = card_text_without_fields.replace(
                    part.split(':', 1)[-1].strip(), ''
                ).strip()
            if card_text_without_fields and len(card_text_without_fields) > 10:
                content_parts.append(f"Details: {card_text_without_fields}")
            content = '\n'.join(content_parts)
        else:
            content = card_text
            if href:
                full_url = href if href.startswith('http') else \
                    f"{urlparse(url).scheme}://{urlparse(url).netloc}{href}"
                content += f"\nURL: {full_url}"

        if cls._is_junk(content):
            return None

        return {
            'heading': name,
            'heading_path': [page_title, name],
            'content': f"[{page_title} > {name}]\n\n{content}",
            'source_url': href or url,
            'chunk_category': 'main_content',
            'page_type': 'card',
        }

    # ============================================================
    # SECTION-BASED EXTRACTION
    # ============================================================

    @classmethod
    def _extract_sections_by_heading(cls, soup: BeautifulSoup, url: str, page_title: str) -> List[Dict]:
        """
        Walk every text node in the content root in document order and
        accumulate runs of text into sections delimited by headings.

        This is a text-node walk, not an element walk. It captures short
        fragments (single digits, labels, prices) that the previous
        element-based extractor dropped because they lived inside divs
        that the extractor skipped.

        Boundaries:
        - An <h1>..<h6> starts a new section.
        - Everything else appends to the current section.

        Skips:
        - Text nodes inside nav/header/footer/aside (chrome).
        - Text nodes inside subtrees owned by other extractors — but
          table/list ownership is conditional (see
          _inside_other_extractor_subtree) so layout tables and short
          nav lists still contribute their text.
        - Short non-word fragments inside unclassed leaf <div>s
          (decorative counters).
        """
        content_root = cls._find_content_root(soup)
        sections: List[Dict[str, Any]] = []
        seen_hashes: Set[str] = set()

        current_heading = page_title
        heading_path = [page_title]
        current_content: List[str] = []

        def flush():
            nonlocal current_content
            if not current_content:
                return
            # Merge adjacent (label, value) or (value, label) fragments
            # into "Label: value" pairs BEFORE joining.
            current_content = cls._merge_label_value_pairs(current_content)
            text = '\n'.join(current_content).strip()
            if not text or cls._is_junk(text):
                current_content = []
                return
            if len(text) < cls.MIN_SECTION_CHARS:
                current_content = []
                return
            norm = cls._normalize_text(text)
            h = hashlib.md5(norm.encode('utf-8')).hexdigest()
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
                'chunk_category': 'main_content',
            })
            current_content = []

        for node in content_root.descendants:
            if not isinstance(node, NavigableString):
                continue
            parent = node.parent
            if parent is None:
                continue

            # Skip chrome.
            if cls._in_skipped_region(parent):
                continue

            # Skip subtrees owned by other extractors (conditional for
            # tables and lists — see the helper).
            if cls._inside_other_extractor_subtree(node):
                continue

            parent_name = getattr(parent, 'name', None)

            # Headings set the current path and start a new section.
            if parent_name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                heading_text = parent.get_text(strip=True)
                if heading_text and cls._looks_like_heading(heading_text):
                    flush()
                    current_heading = heading_text
                    level = int(parent_name[1])
                    heading_path = heading_path[:level - 1] + [current_heading]
                # Do not append the heading's own text as content.
                continue

            text = str(node).strip()
            if not text:
                continue
            if len(text) < cls.MIN_TEXT_BLOCK_CHARS:
                continue

            # Skip decorative short fragments ("0", "-", "•") in
            # unclassed leaf divs. These break label/value pairing.
            if cls._is_decorative_fragment(text, parent):
                continue

            current_content.append(text)

        flush()
        return sections

    @classmethod
    def _extract_cards_from_sections(
        cls, soup: BeautifulSoup, url: str, page_title: str,
    ) -> List[Dict]:
        content_root = cls._find_content_root(soup)
        sections: List[Dict] = []
        seen_keys: Set[str] = set()

        for container in content_root.find_all(['div', 'ul', 'section', 'main', 'article']):
            if cls._in_skipped_region(container):
                continue
            card_group = cls._detect_card_group_in(container)
            if not card_group:
                continue
            for card in card_group[:200]:
                section = cls._card_element_to_section(card, url, page_title)
                if not section:
                    continue
                key = cls._section_key(section)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                sections.append(section)
        return sections

    # ============================================================
    # MEDIA CHUNK CREATION
    # ============================================================

    @classmethod
    def _create_media_chunks(
        cls, media_assets: List[Dict], page_title: str, url: str
    ) -> List[Dict]:
        chunks: List[Dict] = []
        for asset in media_assets:
            if asset.get('media_type') != 'image':
                continue
            if not asset.get('was_analyzed'):
                continue
            desc = (asset.get('description') or '').strip()
            if len(desc) < cls.MIN_IMAGE_DESC_CHARS:
                continue
            src = (asset.get('source_url') or '').lower()
            if any(sig in src for sig in cls.DECORATIVE_IMAGE_SIGNALS):
                continue
            if desc.lower().startswith('image asset:') and len(desc) < 150:
                continue

            structured = asset.get('structured_data') or {}
            has_entities = bool(structured.get('entities'))
            visible_text = (structured.get('visible_text') or '').strip()
            has_visible_text = len(visible_text) >= 20

            lower_desc = desc.lower()
            looks_like_flat_shape = any(
                phrase in lower_desc for phrase in (
                    'solid ', 'uniform ', 'single color', 'single-color',
                    'plain ', 'blank ', 'no text', 'no visible text',
                    'solid color', 'solid-colour', 'monochrome block',
                )
            ) and not has_visible_text and not has_entities

            if looks_like_flat_shape:
                continue
            if not has_entities and not has_visible_text:
                continue

            parts = [f"[Image from {page_title}]", f"Description: {desc}"]
            if visible_text:
                parts.append(f"Visible text: {visible_text}")
            if structured.get('entities'):
                entity_text = " | ".join(
                    f"{e.get('name', '')} ({e.get('type', '')})"
                    for e in structured.get('entities', [])[:5]
                )
                if entity_text:
                    parts.append(f"Entities: {entity_text}")
            if asset.get('alt_text'):
                parts.append(f"Alt text: {asset['alt_text']}")
            if asset.get('caption'):
                parts.append(f"Caption: {asset['caption']}")
            if asset.get('section_heading'):
                parts.append(f"Section: {asset['section_heading']}")
            content = "\n".join(parts)

            chunks.append({
                'heading': 'Image',
                'heading_path': [page_title, 'Image'],
                'content': content,
                'source_url': asset.get('source_url') or url,
                'chunk_category': 'media',
                'chunk_type': 'image_description',
                'entity_type': 'image',
            })
        return chunks

    # ============================================================
    # HOW-TO CONTENT EXTRACTION
    # ============================================================

    @classmethod
    def _extract_howto_content(cls, soup: BeautifulSoup, url: str, page_title: str) -> List[Dict]:
        cls._debug_log("📋 EXTRACT_HOWTO START", {'url': url, 'page_title': page_title})

        sections = []
        main_content = (
            soup.find('div', {'id': 'main-content'}) or
            soup.find('div', {'class': 'main-content'}) or
            soup.find('div', {'id': 'article-body'}) or
            soup.find('div', {'class': 'article-body'}) or
            soup.find('article') or
            soup.find('main') or
            soup
        )

        steps_section = main_content.find('div', {'class': 'steps'})
        if steps_section:
            main_content = steps_section

        part_headers = main_content.find_all(['h2', 'h3'], class_=re.compile(r'part|step|Part|Step', re.I))
        if not part_headers:
            part_headers = main_content.find_all(['h2', 'h3'])

        if part_headers:
            for heading in part_headers:
                section_title = heading.get_text(strip=True)
                if not section_title:
                    continue
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
                        'chunk_type': 'section',
                    })

        cls._debug_log("📋 EXTRACT_HOWTO RESULT", {
            'sections_count': len(sections),
            'sample': sections[0] if sections else None,
        })
        return sections

    @staticmethod
    def _extract_table_text(table) -> Optional[str]:
        try:
            parts = []
            headers = []
            thead = table.find('thead')
            if thead:
                for th in thead.find_all(['th', 'td']):
                    headers.append(ContentProcessor._cell_text(th))
            else:
                first_row = table.find('tr')
                if first_row:
                    for th in first_row.find_all(['th', 'td']):
                        headers.append(ContentProcessor._cell_text(th))
            if headers:
                parts.append("Headers: " + ", ".join(headers))
            rows = table.find_all('tr')
            for row in rows:
                cells = []
                for td in row.find_all(['td', 'th']):
                    cells.append(ContentProcessor._cell_text(td))
                if cells:
                    parts.append(", ".join(cells))
            return "\n".join(parts) if parts else None
        except Exception:
            return None

    # ============================================================
    # TITLE / METADATA / MISC
    # ============================================================

    @staticmethod
    def _resolve_title(soup: BeautifulSoup, page_title: Optional[str], source_url: Optional[str]) -> str:
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
        metadata = {
            'url': url,
            'domain': urlparse(url).netloc if url else '',
            'path': urlparse(url).path if url else '',
            'title': None,
            'description': None,
            'og_title': None,
            'og_description': None,
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

    # ============================================================
    # SPA SHELL DETECTION
    # ============================================================

    @staticmethod
    def _detect_spa_shell(soup: BeautifulSoup) -> bool:
        for mount_id in ('root', 'app', '__next', '__nuxt'):
            el = soup.find(id=mount_id)
            if el is not None and not el.get_text(strip=True):
                return True
        if soup.find(attrs={'data-reactroot': True}) is not None:
            body = soup.find('body')
            if body and len(body.get_text(strip=True).split()) < 100:
                return True
        body = soup.find('body')
        if body:
            word_count = len(body.get_text(separator=' ', strip=True).split())
            script_count = len(soup.find_all('script'))
            if word_count < 50 and script_count > 5:
                return True
        return False

    @staticmethod
    def _empty_result(page_title: Optional[str], source_url: Optional[str],
                      page_type: str = 'empty') -> Dict[str, Any]:
        title = page_title or 'Untitled'
        return {
            'page_title': title,
            'source_url': source_url or '',
            'page_type': page_type,
            'main_content': {
                'all_text': '',
                'sections': [],
                'headings': [],
                'paragraphs': [],
                'lists': [],
                'tables': [],
                'cards': [],
                'has_content': False,
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
                'lists': [],
                'paragraphs': [],
                'has_ordinal_collection': False,
            },
            'is_first_page': False,
            'has_content': False,
            'text_stats': {
                'total_chars': 0,
                'total_words': 0,
                'section_count': 0,
            },
        }

    # ============================================================
    # UNIVERSAL COLLECTION DETECTION
    # ============================================================

    @classmethod
    def _content_hash(cls, item: Dict[str, Any]) -> str:
        """Stable hash for matching items across the two detection passes."""
        text = (
            item.get("content")
            or item.get("text")
            or (item.get("name", "") + " " + item.get("description", ""))
            or json.dumps(item, sort_keys=True, default=str)
        )
        return hashlib.md5(cls._normalize_text(str(text)).encode("utf-8")).hexdigest()

    @classmethod
    def _detect_ordinal_collections(
        cls, structure: Dict[str, Any]
    ) -> Tuple[Dict[str, int], Dict[str, Any]]:
        """
        Detect ranked/numbered collections anywhere in the structure.

        Each detected collection gets its own *ordinal namespace*: the
        first collection starts at ordinal 0, the next at
        (previous_offset + previous_size), and so on. This prevents two
        collections on the same page (e.g. a 50-item ranked section list
        plus a 6-item nav list) from both claiming ordinal 0.

        Detection order determines the offsets. Current order:
            1. Sibling sections under a shared heading parent
            2. Lists (largest first)
            3. Tables (largest first)
            4. Cards

        Returns:
            ordinal_map: content_hash -> ordinal_index (0-based, global).
            report: diagnostics describing what was detected.
        """
        ordinal_map: Dict[str, int] = {}
        report: Dict[str, Any] = {
            "sections_collection": None,
            "list_collections": [],
            "table_collections": [],
            "card_collections": [],
            "collection_offsets": [],
        }

        main_content = structure.get("main_content", {}) or {}
        page_title = structure.get("page_title", "") or ""

        # Global cursor. Every collection advances this by its size so the
        # next collection starts where the previous one left off.
        next_offset = 0

        def assign(collection_name: str, item_hashes: List[str], size: int) -> int:
            """
            Assign sequential ordinals to the given item hashes, starting at
            the current global cursor. Returns the number of items that were
            newly assigned (i.e. not already present in ordinal_map).
            """
            nonlocal next_offset
            added = 0
            for idx, h in enumerate(item_hashes):
                if not h:
                    continue
                if h not in ordinal_map:
                    ordinal_map[h] = next_offset + idx
                    added += 1
            # Advance the cursor regardless of hash collisions so that a
            # later collection never reuses a range already claimed.
            report["collection_offsets"].append({
                "collection": collection_name,
                "base": next_offset,
                "size": size,
                "added": added,
            })
            next_offset += size
            return added

        # ------------------------------------------------------------
        # 1. Sibling sections under the same parent heading_path[0].
        #    This covers pages that render ranked lists as <h2> + body
        #    blocks instead of <ul>/<ol>/<table>.
        # ------------------------------------------------------------
        sections = main_content.get("sections", []) or []
        by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for s in sections:
            path = s.get("heading_path") or []
            parent = path[0] if path else None
            if parent:
                by_parent[parent].append(s)

        # Filter to the page-title group if it exists; else take the largest.
        best_parent = None
        best_group: List[Dict[str, Any]] = []
        for parent, group in by_parent.items():
            if parent == page_title and len(group) >= cls.MIN_SECTION_SIBLINGS_FOR_COLLECTION:
                best_parent, best_group = parent, group
                break
            if len(group) > len(best_group):
                best_parent, best_group = parent, group

        if best_parent and len(best_group) >= cls.MIN_SECTION_SIBLINGS_FOR_COLLECTION:
            # Order by chunk_index-equivalent if present, else by original order.
            try:
                ordered = sorted(
                    best_group,
                    key=lambda s: (
                        s.get("position_in_page")
                        if s.get("position_in_page") is not None
                        else s.get("_order", 0)
                    ),
                )
            except Exception:
                ordered = best_group

            item_hashes = [cls._content_hash(s) for s in ordered]
            assign("sections", item_hashes, len(ordered))

            report["sections_collection"] = {
                "parent": best_parent,
                "size": len(ordered),
            }

        # ------------------------------------------------------------
        # 2. Lists with enough items to be considered a collection.
        #    Sort by size descending so the biggest list gets the earliest
        #    offsets within the list category. Each list is still its own
        #    namespace because we advance the global cursor per list.
        # ------------------------------------------------------------
        lists = main_content.get("lists", []) or []
        indexed_lists = [
            (li, lst) for li, lst in enumerate(lists)
            if len(lst.get("items") or []) >= cls.MIN_LIST_ITEMS_FOR_COLLECTION
        ]
        indexed_lists.sort(key=lambda pair: len(pair[1].get("items") or []), reverse=True)

        for li, lst in indexed_lists:
            items = lst.get("items", []) or []
            item_hashes = [cls._content_hash({"content": str(item)}) for item in items]
            added = assign(f"list[{li}]", item_hashes, len(items))
            if added:
                report["list_collections"].append({
                    "list_index": li,
                    "size": len(items),
                    "added": added,
                })

        # ------------------------------------------------------------
        # 3. Tables. A table with >= N rows is a collection. Sort largest
        #    first for the same reason as lists.
        # ------------------------------------------------------------
        tables = main_content.get("tables", []) or []
        indexed_tables = [
            (ti, tbl) for ti, tbl in enumerate(tables)
            if len(tbl.get("row_texts") or tbl.get("rows") or []) >= cls.MIN_TABLE_ROWS_FOR_COLLECTION
        ]
        indexed_tables.sort(
            key=lambda pair: len(pair[1].get("row_texts") or pair[1].get("rows") or []),
            reverse=True,
        )

        for ti, tbl in indexed_tables:
            rows = tbl.get("row_texts") or tbl.get("rows") or []
            item_hashes = []
            for row in rows:
                row_text = str(row) if not isinstance(row, list) else " ".join(str(c) for c in row)
                item_hashes.append(cls._content_hash({"content": row_text}))
            added = assign(f"table[{ti}]", item_hashes, len(rows))
            if added:
                report["table_collections"].append({
                    "table_index": ti,
                    "size": len(rows),
                    "added": added,
                })

        # ------------------------------------------------------------
        # 4. Cards. A group of cards is a collection.
        # ------------------------------------------------------------
        cards = main_content.get("cards", []) or []
        if len(cards) >= cls.MIN_CARDS_FOR_COLLECTION:
            item_hashes = [cls._content_hash(card) for card in cards]
            added = assign("cards", item_hashes, len(cards))
            if added:
                report["card_collections"].append({
                    "size": len(cards),
                    "added": added,
                })

        return ordinal_map, report

    @classmethod
    def _apply_ordinal_map(
        cls,
        structure: Dict[str, Any],
        ordinal_map: Dict[str, int],
    ) -> None:
        """
        Attach `ordinal_index` to every item in the structure whose
        content_hash is present in the map. Items not in the map get
        `ordinal_index = None`.

        Also writes the parallel arrays consumed by EnhancedChunker:
            lists[i]["item_ordinal_indices"]  -> per-item ordinal (or None)
            tables[i]["row_ordinal_indices"]  -> per-row ordinal (or None)

        Runs even when ordinal_map is empty so downstream code always sees
        the parallel arrays (with None values) rather than missing keys.
        """
        main_content = structure.get("main_content", {}) or {}

        for collection_key in ("sections", "lists", "tables", "cards"):
            items = main_content.get(collection_key) or []
            for item in items:
                if isinstance(item, dict):
                    h = cls._content_hash(item)
                    item["ordinal_index"] = ordinal_map.get(h)

        # Lists: propagate to each item string (wrapped as dict) too.
        for lst in main_content.get("lists", []) or []:
            items = lst.get("items") or []
            ordinal_indices = []
            for item in items:
                h = cls._content_hash({"content": str(item)})
                ordinal_indices.append(ordinal_map.get(h))
            # Store parallel array so the chunker can read it.
            lst["item_ordinal_indices"] = ordinal_indices

        # Tables: parallel array of row ordinals.
        for tbl in main_content.get("tables", []) or []:
            rows = tbl.get("row_texts") or tbl.get("rows") or []
            ordinal_indices = []
            for row in rows:
                row_text = str(row) if not isinstance(row, list) else " ".join(str(c) for c in row)
                h = cls._content_hash({"content": row_text})
                ordinal_indices.append(ordinal_map.get(h))
            tbl["row_ordinal_indices"] = ordinal_indices

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
        try:
            with open(cls.DEBUG_LOG_PATH, 'w', encoding='utf-8') as f:
                f.write("=== CONTENT PROCESSING DEBUG LOG ===\n")
                f.write(f"Started: {datetime.now().isoformat()}\n")
                f.write(f"URL: {source_url}\n")
                f.write(f"HTML Length: {len(html)}\n")
                f.write(f"{'='*80}\n\n")
        except Exception:
            pass

        cls._debug_log("🌐 PROCESS_HTML START", {
            'source_url': source_url,
            'page_title': page_title,
            'html_length': len(html),
        })

        if not html:
            logger.error(f"❌ process_html called with empty HTML for url='{source_url}'")
            return cls._empty_result(page_title, source_url)

        html_len = len(html)
        logger.info(f"🌐 process_html START: url='{source_url}' | html={html_len} chars")

        soup = BeautifulSoup(html, 'html.parser')

        if cls._detect_spa_shell(soup):
            logger.warning(f"🚧 SPA shell detected for url='{source_url}' — needs JS hydration")
            return cls._empty_result(page_title, source_url, page_type='spa_shell')

        json_ld_text = cls._extract_json_ld(soup)
        json_ld_entities = cls._extract_json_ld_entities(soup)
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

        for tag in soup(['script', 'style', 'noscript', 'iframe']):
            tag.decompose()

        title = cls._resolve_title(soup, page_title, source_url)
        metadata = cls._extract_metadata(soup, source_url or '')

        prose_sections = cls._extract_sections_by_heading(
            soup, source_url or '', title
        )
        howto_sections = cls._extract_howto_content(
            soup, source_url or '', title
        )
        card_sections = cls._extract_cards_from_sections(
            soup, source_url or '', title
        )

        merged_sections = cls._merge_sections(
            prose_sections, howto_sections, card_sections
        )

        # Deduplicate by leaf heading: when the same entity is emitted
        # more than once (mobile + desktop, main + related widget), keep
        # the richest variant.
        sections = cls._dedupe_sections_by_leaf(merged_sections)

        # Preserve original extraction order for the collection detector.
        for i, s in enumerate(sections):
            s.setdefault("_order", i)

        structured_tables = cls._extract_tables_structured(soup)
        structured_lists = cls._extract_lists_structured(soup)
        structured_cards = cls._extract_cards_structured(soup)

        meta_desc = (metadata.get('description') or '').strip()
        if meta_desc and len(meta_desc) >= 60:
            joined_sections = '\n\n'.join(s.get('content', '') for s in sections)
            if meta_desc not in joined_sections:
                sections.append({
                    'heading': 'Page Description',
                    'heading_path': [title, 'Page Description'],
                    'content': f"[{title} > Page Description]\n\n{meta_desc}",
                    'source_url': source_url or '',
                    'chunk_category': 'main_content',
                    'chunk_type': 'section',
                    '_order': len(sections),
                })

        has_content = (
            bool(sections)
            or bool(structured_tables)
            or bool(structured_lists)
            or bool(structured_cards)
        )

        sections_text = '\n\n'.join(
            s['content'].split(']\n\n', 1)[-1] if ']\n\n' in s.get('content', '') else s.get('content', '')
            for s in sections
        )
        all_text = sections_text
        if structured_text:
            all_text = f"{structured_text}\n\n{all_text}"

        total_words = len(all_text.split())

        # ------------------------------------------------------------
        # UNIVERSAL COLLECTION DETECTION
        # ------------------------------------------------------------
        partial_structure = {
            "page_title": title,
            "source_url": source_url or "",
            "main_content": {
                "sections": sections,
                "tables": structured_tables,
                "lists": structured_lists,
                "cards": structured_cards,
            },
        }

        ordinal_map, collection_report = cls._detect_ordinal_collections(partial_structure)
        cls._apply_ordinal_map(partial_structure, ordinal_map)

        has_ordinal_collection = bool(ordinal_map)

        cls._debug_log("🧭 COLLECTION DETECTION", {
            "collection_report": collection_report,
            "ordinal_map_size": len(ordinal_map),
        })

        logger.info(
            f"🌐 process_html END: url='{source_url}' | "
            f"prose_sections={len(prose_sections)} | "
            f"howto_sections={len(howto_sections)} | "
            f"card_sections={len(card_sections)} | "
            f"merged_sections={len(merged_sections)} | "
            f"deduped_sections={len(sections)} | "
            f"tables={len(structured_tables)} | "
            f"lists={len(structured_lists)} | "
            f"cards={len(structured_cards)} | "
            f"ordinal_items={len(ordinal_map)} | "
            f"all_text={len(all_text)} chars | words={total_words}"
        )

        document_structure = {
            'page_title': title,
            'source_url': source_url or '',
            'sections': sections,
            'tables': structured_tables,
            'cards': structured_cards,
            'lists': structured_lists,
            'paragraphs': [],
            'has_ordinal_collection': has_ordinal_collection,
            'collection_report': collection_report,
            'structured_data': {
                'json_ld': json_ld_text,
                'entities': json_ld_entities,
                'svg_text': svg_text,
                'data_attributes': data_attrs,
            },
        }

        metadata['has_structured_data'] = bool(json_ld_text or svg_text or data_attrs)
        metadata['has_ordinal_collection'] = has_ordinal_collection

        result = {
            'page_title': title,
            'source_url': source_url or '',
            'page_type': 'sectioned',
            'main_content': {
                'all_text': all_text,
                'sections': sections,
                'headings': [],
                'paragraphs': [],
                'lists': structured_lists,
                'tables': structured_tables,
                'cards': structured_cards,
                'has_content': has_content,
                'has_ordinal_collection': has_ordinal_collection,
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
                'card_count': len(structured_cards),
                'ordinal_item_count': len(ordinal_map),
            },
        }

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