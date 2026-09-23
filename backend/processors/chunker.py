# processors/chunker.py

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional
from datetime import datetime
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Optional NER + country canonicalization. Both are feature-flagged so
# the module still imports cleanly if the deps aren't installed yet.
# ------------------------------------------------------------------
try:
    from processors.entity_detector import (
        dominant_entity_type,
        detect_entities,
    )
    _NER_ENABLED = True
except Exception as _ner_err:  # pragma: no cover
    logger.warning(f"NER detector unavailable, falling back to heuristics: {_ner_err}")
    _NER_ENABLED = False

    def dominant_entity_type(text: str) -> Optional[str]:  # type: ignore
        return None

    def detect_entities(text: str):  # type: ignore
        return []

try:
    import pycountry
    _PYCOUNTRY_ENABLED = True
except Exception:
    _PYCOUNTRY_ENABLED = False


class EnhancedChunker:
    """
    ONE AND ONLY chunking stage.

    Responsibilities:
    - Take structured content from ContentProcessor
    - Emit small, single-fact chunks for tables, lists, cards
    - Emit larger "container" chunks for grouping / context
    - Validate chunks (only reject truly empty)
    - Add metadata (heading_path, entity_type, chunk_type)

    Universal by design:
    - Driven by DOM shape (has table? has rows? has list items?), not by
      domain or URL patterns.
    - Works for standings tables, price tables, comparison matrices,
      schedules, Wikipedia infoboxes, feature lists, FAQ lists, etc.

    Entity typing is layered:
      1. explicit entity_type from the source object
      2. structural chunk_type  (table/list/card/...)
      3. domain keyword rules   (SKU, add-to-cart, capital+population, ...)
      4. spaCy NER             (GPE->country, PRODUCT->product, NORP->nation, ...)
      5. fallback              ('content')
    """

    TARGET_TOKENS = 600
    MAX_TOKENS = 800

    # Overlap when hard-splitting an oversized sentence.
    SPLIT_OVERLAP_TOKENS = 50

    # Entity types that are single facts. Their short length is a feature,
    # not a weakness, so we do not penalise them for low word count.
    SINGLE_FACT_TYPES = {'table_row', 'list_item', 'card'}

    # Minimum character length for a list item / card field to be promoted
    # to its own chunk. Below this, items stay grouped in their container
    # chunk (keeps navigation lists intact).
    MIN_ITEM_CHARS_FOR_OWN_CHUNK = 10
    MIN_LIST_ITEMS_TO_FORCE_PROMOTION = 10
    # Absolute floor for items in a force-promoted (>=10 item) list.
    # These lists are almost always ranked lists where every entry is a
    # fact; a two-word name like "Y Combinator" must survive as its own
    # chunk so it can carry an ordinal_index.
    MIN_ITEM_CHARS_HARD_FLOOR = 3
    # Minimum description length for an image to justify its own chunk.
    # Very short descriptions are almost always decorative stubs.
    MIN_IMAGE_DESC_CHARS = 80

    # Substrings in image src/url that strongly indicate decoration, not
    # content. Case-insensitive.
    DECORATIVE_IMAGE_SIGNALS = (
        'icon', 'emoji', 'logo', 'sprite', 'favicon',
        'spacer', 'pixel', 'blank.', 'transparent.',
        'confetti', 'arrow', 'bullet', 'chevron', 'divider',
    )

    CATEGORY_WEIGHTS = {
        'main_content': 1.0,
        'header_nav': 0.75,
        'footer': 0.65,
        'sidebar': 0.5,
        'excluded': 0.0,
    }

    ENTITY_BOOSTS = {
        'product': 1.3,
        'article_body': 1.2,
        'code_example': 1.1,
        'table': 1.1,
        'list': 1.05,
        'card': 1.05,
        'content': 1.0,
        'structured_data': 1.3,
        'table_row': 1.4,
        'list_item': 1.25,
        'country': 1.25,
        'nation': 1.15,
        'organization': 1.1,
        'location': 1.0,
        'faq': 1.15,
        'image': 0.85,
        'image_description': 0.85,
        'summary': 0.6,
    }

    EMPTY_PATTERNS = [
        r'^\s*$',
        r'^[.,;:!?]+$',
        r'^\{\{.*\}\}$',
        r'^\[.*\]$',
    ]

    DEBUG_ENABLED = True
    DEBUG_LOG_PATH = "/app/debug_chunking.log"
    CHUNK_OUTPUT_DIR = os.getenv("CHUNK_OUTPUT_DIR", "/app/chunks")

    # Domain keyword rules for entity detection. Compiled once.
    _COUNTRY_KEYWORDS = re.compile(
        r'\bcapital\s*:.*\bpopulation\s*:',
        re.IGNORECASE | re.DOTALL,
    )
    _PRODUCT_KEYWORDS = re.compile(
        r'\b(add to cart|in stock|sku\s*:|buy now|msrp\s*:|price\s*:)',
        re.IGNORECASE,
    )
    _FAQ_KEYWORDS = re.compile(r'\b(faq|frequently asked)\b', re.IGNORECASE)

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
    def is_empty_chunk(text: str) -> bool:
        if not text:
            return True
        if not text.strip():
            return True
        cleaned = text.strip()
        for pattern in EnhancedChunker.EMPTY_PATTERNS:
            if re.match(pattern, cleaned):
                return True
        return False

    @staticmethod
    def token_count(text: str) -> int:
        return len(re.findall(r"\b\w+\b", text or ""))

    @staticmethod
    def normalize_text(text: Any) -> str:
        if isinstance(text, dict):
            text = text.get("text") or text.get("content") or text.get("summary") or ""
        elif isinstance(text, list):
            text = " ".join(str(item) for item in text)
        elif text is None:
            text = ""
        return re.sub(r"\s+", " ", str(text) or "").strip()

    @classmethod
    def save_chunk_to_txt(
        cls,
        content: str,
        document_id: str,
        chunk_id: str,
        chunk_index: int,
        source_url: str = "",
    ) -> str:
        url_name = urlsplit(source_url).hostname or source_url or "unknown_url"
        url_name = re.sub(r"[^A-Za-z0-9.-]+", "_", url_name).strip("._")
        output_dir = os.path.join(cls.CHUNK_OUTPUT_DIR, url_name or "unknown_url")
        os.makedirs(output_dir, exist_ok=True)
        file_url = re.sub(r"[^A-Za-z0-9.-]+", "_", source_url).strip("._")
        file_path = os.path.join(
            output_dir,
            f"chunk_{file_url or 'unknown_url'}_{chunk_id}.txt",
        )
        with open(file_path, "w", encoding="utf-8") as chunk_file:
            chunk_file.write(content)
        return file_path

    # ============================================================
    # Heading path construction
    # ============================================================

    @staticmethod
    def build_heading_path(
        section: Dict[str, Any],
        default_title: str,
        suffix: Optional[str] = None,
    ) -> List[str]:
        heading_path = section.get("heading_path") or []
        if not heading_path:
            heading = section.get("heading") or default_title
            heading_path = (
                [default_title, heading] if heading != default_title else [default_title]
            )
        heading_path = [p for p in heading_path if p]
        if suffix:
            heading_path = heading_path + [suffix]
        return heading_path

    # ============================================================
    # SimHash — near-duplicate detection
    # ============================================================

    @staticmethod
    def _simhash(text: str) -> int:
        if not text:
            return 0
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return 0

        bit_sums = [0] * 64
        for token in tokens:
            h = int.from_bytes(
                hashlib.md5(token.encode("utf-8")).digest()[:8],
                byteorder="big",
                signed=False,
            )
            for i in range(64):
                if (h >> i) & 1:
                    bit_sums[i] += 1
                else:
                    bit_sums[i] -= 1

        simhash = 0
        for i, s in enumerate(bit_sums):
            if s > 0:
                simhash |= (1 << i)
        return simhash

    # ============================================================
    # Scoring
    # ============================================================

    @staticmethod
    def score_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
        content = chunk.get("content", "")
        words = EnhancedChunker.token_count(content)
        heading_depth = len(chunk.get("heading_path", []))

        cs = chunk.setdefault("content_structure", {})
        has_table = bool(cs.get("has_table"))
        has_list = bool(cs.get("has_list"))
        has_images = bool(cs.get("has_images"))
        has_structured_data = bool(cs.get("has_structured_data"))

        category = chunk.get('chunk_category', 'main_content')
        category_weight = EnhancedChunker.CATEGORY_WEIGHTS.get(category, 0.5)

        entity_type = chunk.get('entity_type', 'content')
        entity_boost = EnhancedChunker.ENTITY_BOOSTS.get(entity_type, 1.0)

        structured_boost = 1.0
        if has_table:
            structured_boost += 0.2
        if has_list:
            structured_boost += 0.15
        if has_structured_data:
            structured_boost += 0.25

        if entity_type in EnhancedChunker.SINGLE_FACT_TYPES:
            word_score = 1.0
        else:
            word_score = min(words / 200, 1.0)

        relevance = min(1.0, (
            (0.35 * min(heading_depth / 4, 1.0)) +
            (0.45 * word_score) +
            (0.2 * (1.0 if (has_table or has_list or has_images or has_structured_data) else 0.0))
        )) * category_weight * entity_boost * structured_boost

        quality = min(1.0, (
            (0.5 * min(words / 160, 1.0)) +
            (0.2 if has_table else 0.0) +
            (0.2 if has_list else 0.0) +
            (0.1 if has_structured_data else 0.0) +
            (0.1 if content.strip() else 0.0)
        ))

        cs["relevance_score"] = round(relevance, 4)
        cs["quality_score"] = round(quality, 4)
        chunk["category_weight"] = category_weight
        chunk["entity_boost"] = entity_boost
        chunk["structured_boost"] = round(structured_boost, 3)
        return chunk

    # ============================================================
    # Section splitting by token budget
    # ============================================================

    @classmethod
    def _split_section_by_tokens(
        cls,
        content: str,
        heading_path: List[str],
        max_tokens: int,
    ) -> List[str]:
        if cls.token_count(content) <= max_tokens:
            return [content]

        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        packed: List[str] = []
        buffer: List[str] = []
        buffer_tokens = 0

        def flush_buffer():
            nonlocal buffer, buffer_tokens
            if buffer:
                packed.append("\n\n".join(buffer))
                buffer = []
                buffer_tokens = 0

        for para in paragraphs:
            para_tokens = cls.token_count(para)
            if para_tokens > max_tokens:
                flush_buffer()
                packed.extend(cls._split_long_paragraph(para, max_tokens))
                continue
            if buffer_tokens + para_tokens > max_tokens:
                flush_buffer()
            buffer.append(para)
            buffer_tokens += para_tokens

        flush_buffer()
        return packed if packed else [content]

    @classmethod
    def _split_long_paragraph(cls, paragraph: str, max_tokens: int) -> List[str]:
        sentences = re.split(r'(?<=[.!?])\s+', paragraph)
        packed: List[str] = []
        buffer: List[str] = []
        buffer_tokens = 0

        def flush_buffer():
            nonlocal buffer, buffer_tokens
            if buffer:
                packed.append(" ".join(buffer))
                buffer = []
                buffer_tokens = 0

        for sentence in sentences:
            sent_tokens = cls.token_count(sentence)
            if sent_tokens > max_tokens:
                flush_buffer()
                packed.extend(cls._hard_split_words(sentence, max_tokens))
                continue
            if buffer_tokens + sent_tokens > max_tokens:
                flush_buffer()
            buffer.append(sentence)
            buffer_tokens += sent_tokens

        flush_buffer()
        return packed if packed else [paragraph]

    @classmethod
    def _hard_split_words(cls, text: str, max_tokens: int) -> List[str]:
        words = text.split()
        if not words:
            return [text]
        chunks: List[str] = []
        step = max(1, max_tokens - cls.SPLIT_OVERLAP_TOKENS)
        i = 0
        while i < len(words):
            chunk_words = words[i:i + max_tokens]
            chunks.append(" ".join(chunk_words))
            if i + max_tokens >= len(words):
                break
            i += step
        return chunks

    # ============================================================
    # Formatting helpers
    # ============================================================

    _CURRENCY_PREFIXES = ('$', '£', '€', '¥', '₹')
    _PERCENT_SUFFIX = '%'

    @classmethod
    def _should_attempt_split(cls, value: str) -> bool:
        if not value:
            return False
        stripped = value.strip()
        if stripped and stripped[0] in cls._CURRENCY_PREFIXES:
            return False
        if cls._PERCENT_SUFFIX in stripped:
            return False
        return re.search(r'\d[A-Z]', value) is not None

    @classmethod
    def _split_glued_cells(cls, value: str) -> List[str]:
        if not value:
            return []
        pairs = re.findall(r'(\d[\d,\.]*)\s*([A-Z][A-Za-z ]{1,30})', value)
        if not pairs:
            return [value]

        labeled = [f"{label.strip()}: {num}" for num, label in pairs]

        first_match = re.search(r'\d[\d,\.]*\s*[A-Z][A-Za-z ]{1,30}', value)
        if first_match and first_match.start() > 0:
            leading = value[:first_match.start()].strip(' ,')
            if len(leading) > 1:
                labeled.insert(0, leading)

        return labeled

    @staticmethod
    def _format_table_summary(table: Dict) -> str:
        parts = []

        summary = table.get('summary', '')
        if summary:
            parts.append(f"Table summary: {summary}")

        headers = [str(h).strip() for h in table.get('headers', []) if str(h).strip()]
        if headers:
            parts.append("Columns: " + " | ".join(headers))

        # Prefer row_texts (flattened strings); fall back to rows (cell lists).
        row_texts = table.get('row_texts') or []
        rows = table.get('rows', []) or []
        total_rows = len(row_texts) or len(rows)
        parts.append(f"Rows: {total_rows}")

        if row_texts:
            for i, txt in enumerate(row_texts[:3], 1):
                preview = re.sub(r'\s+', ' ', str(txt)).strip()
                if preview:
                    parts.append(f"  Row {i}: {preview}")
        else:
            for i, row in enumerate(rows[:3], 1):
                cells = [str(c).strip() for c in row if str(c).strip()]
                if cells:
                    parts.append(f"  Row {i}: " + " | ".join(cells))

        if total_rows > 3:
            parts.append(f"  ... ({total_rows - 3} more rows)")

        return "\n".join(parts)

    @classmethod
    def _table_to_row_chunks(
        cls,
        table: Dict,
        default_title: str,
        source_url: str,
        base_heading_path: List[str],
        start_index: int,
    ) -> List[Dict[str, Any]]:
        """
        Convert one table into N row chunks — one per data row.

        Prefers the flattened `row_texts` field emitted by ContentProcessor.
        Falls back to the legacy cell-list path (`rows`) when `row_texts`
        is not present.

        Each emitted row chunk carries `ordinal_index` copied from the
        table's `row_ordinal_indices` parallel array (populated by
        ContentProcessor._apply_ordinal_map). If the array is missing or
        shorter than the row list, ordinal_index is None for the affected
        rows rather than raising.
        """
        chunks: List[Dict[str, Any]] = []
        headers = [str(h).strip() for h in table.get('headers', [])]
        row_texts = table.get('row_texts') or []
        row_entity_type = cls._table_row_entity_type(table, headers, default_title)
        row_ordinals = table.get("row_ordinal_indices") or []

        # ---- Preferred path: use flattened row strings directly. ----
        if row_texts:
            cleaned_rows = [
                str(r).strip() for r in row_texts
                if r and not cls.is_empty_chunk(str(r))
            ]
            if not cleaned_rows:
                return chunks

            total = len(cleaned_rows)
            n_cols = len(headers) if headers else 0

            for i, row_text in enumerate(cleaned_rows, 1):
                content = (
                    f"Table: {default_title} — Row {i} of {total}\n"
                    f"{row_text}"
                )
                ordinal = row_ordinals[i - 1] if (i - 1) < len(row_ordinals) else None
                chunk = {
                    "content": content,
                    "heading_path": base_heading_path + [f"Row {i}"],
                    "chunk_type": "table_row",
                    "chunk_category": "main_content",
                    "entity_type": row_entity_type,
                    "token_count": len(content.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": True,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": row_entity_type != "table_row",
                        "table_headers": headers,
                        "row_index": i,
                        "row_count": total,
                        "col_count": n_cols,
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(content),
                    "chunk_index": start_index + i - 1,
                    "ordinal_index": ordinal,
                }
                chunks.append(cls.score_chunk(chunk))

            return chunks

        # ---- Legacy path: cell lists. ----
        rows = table.get('rows', []) or []
        rows = [r for r in rows if any(str(c).strip() for c in r)]
        if not rows:
            return chunks

        n_cols = max((len(r) for r in rows), default=0)
        if len(headers) < n_cols:
            headers = headers + [
                f"Column {i}" for i in range(len(headers) + 1, n_cols + 1)
            ]

        total = len(rows)
        for i, row in enumerate(rows, 1):
            labeled_lines: List[str] = []
            for j, cell in enumerate(row):
                header = headers[j] if j < len(headers) else f"Column {j + 1}"
                value = str(cell).strip()
                if not value:
                    continue

                if cls._should_attempt_split(value):
                    parts = cls._split_glued_cells(value)
                else:
                    parts = [value]

                if len(parts) == 1 and parts[0] == value:
                    labeled_lines.append(f"{header}: {value}")
                else:
                    for part in parts:
                        if ':' in part:
                            labeled_lines.append(part)
                        else:
                            labeled_lines.append(f"{header}: {part}")

            content = (
                f"Table: {default_title} — Row {i} of {total}\n"
                + "\n".join(labeled_lines)
            )

            ordinal = row_ordinals[i - 1] if (i - 1) < len(row_ordinals) else None
            chunk = {
                "content": content,
                "heading_path": base_heading_path + [f"Row {i}"],
                "chunk_type": "table_row",
                "chunk_category": "main_content",
                "entity_type": row_entity_type,
                "token_count": len(content.split()),
                "source_url": source_url,
                "page_title": default_title,
                "content_structure": {
                    "has_table": True,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "has_structured_data": row_entity_type != "table_row",
                    "table_headers": headers,
                    "row_index": i,
                    "row_count": total,
                    "col_count": n_cols,
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chunk_simhash": cls._simhash(content),
                "chunk_index": start_index + i - 1,
                "ordinal_index": ordinal,
            }
            chunks.append(cls.score_chunk(chunk))

        return chunks

    @staticmethod
    def _format_list_as_text(list_data: Dict) -> str:
        parts = []
        list_type = list_data.get('type', 'unordered')
        items = list_data.get('items', [])
        if not items:
            return ""
        parts.append(f"List type: {list_type}")
        parts.append(f"Items ({len(items)}):")
        for i, item in enumerate(items, 1):
            parts.append(f"  {i}. {item}")
        return "\n".join(parts)

    @staticmethod
    def _format_card_as_text(card: Dict) -> str:
        parts = []
        name = card.get('name', '')
        if name:
            parts.append(f"Card: {name}")
        description = card.get('description', '')
        if description:
            parts.append(f"Description: {description}")
        price = card.get('price', '')
        if price:
            parts.append(f"Price: {price}")
        text = card.get('text', '')
        if text:
            parts.append(f"Text: {text}")
        for key, value in card.items():
            if key not in ['name', 'description', 'price', 'text'] and value:
                parts.append(f"{key}: {value}")
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _format_structured_data_as_text(structured_data: Dict) -> str:
        parts = []
        json_ld = structured_data.get('json_ld', {})
        if json_ld:
            parts.append("Structured Data (Schema.org):")
            if isinstance(json_ld, dict):
                for key, value in json_ld.items():
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                for sub_key, sub_value in item.items():
                                    if sub_value:
                                        parts.append(f"  {sub_key}: {sub_value}")
                            elif value:
                                parts.append(f"  {key}: {value}")
                    elif value:
                        parts.append(f"  {key}: {value}")
            elif isinstance(json_ld, list):
                for item in json_ld:
                    if isinstance(item, dict):
                        for key, value in item.items():
                            if value:
                                parts.append(f"  {key}: {value}")

        svg_text = structured_data.get('svg_text', '')
        if svg_text:
            parts.append(f"SVG Diagram Text: {svg_text}")

        data_attrs = structured_data.get('data_attributes', {})
        if data_attrs:
            parts.append("Data Attributes:")
            for key, value in data_attrs.items():
                parts.append(f"  {key}: {value}")

        return "\n".join(parts) if parts else ""

    # ============================================================
    # Entity detection
    # ============================================================

    @staticmethod
    def _canonicalize_country(text: str) -> Optional[str]:
        """
        Return the ISO country name if `text` names a real sovereign
        country, else None. Used to reject GPE strings that are cities
        or states (e.g. 'Paris', 'Texas').
        """
        if not _PYCOUNTRY_ENABLED or not text:
            return None
        stripped = text.strip()
        if not stripped or len(stripped) > 80:
            return None
        for lookup in (pycountry.countries.get,):
            try:
                match = pycountry.countries.lookup(stripped)
                if match:
                    return getattr(match, "name", None) or stripped
            except LookupError:
                continue
            except Exception:
                pass
        # Fuzzy search fallback
        try:
            results = pycountry.countries.search_fuzzy(stripped)
            if results:
                return getattr(results[0], "name", None) or stripped
        except Exception:
            pass
        return None

    @classmethod
    def _detect_entity_type(
        cls,
        section: Dict[str, Any],
        content_type: Optional[str] = None,
        page_context: Optional[str] = None,
    ) -> str:
        """
        Layered entity classification:
          1. explicit entity_type on the object
          2. structural chunk_type
          3. domain keyword rules (very specific -> they win)
          4. spaCy NER (dominant label)
          5. page_type heuristics / fallback
        """
        # -------- 1. explicit --------
        explicit_entity_type = section.get('entity_type')
        if explicit_entity_type:
            return str(explicit_entity_type)

        chunk_type = section.get('chunk_type', '')
        content = section.get('content', '') or ''
        lower = content.lower()

        # -------- 2. structural --------
        if chunk_type == 'table':
            return 'table'
        if chunk_type == 'list':
            return 'list'
        if chunk_type == 'card':
            return 'card'
        if chunk_type == 'structured_data':
            return 'structured_data'
        if chunk_type in ('image', 'media_chunk'):
            return 'image'

        # -------- 3. domain keyword rules --------
        if cls._COUNTRY_KEYWORDS.search(content):
            return 'country'
        if cls._PRODUCT_KEYWORDS.search(content):
            return 'product'
        if cls._FAQ_KEYWORDS.search(lower):
            return 'faq'
        if chunk_type == 'code' or '```' in content:
            return 'code_example'
        if content.count('?') >= 2:
            q_words = sum(1 for w in ('how', 'what', 'why', 'when', 'where') if w in lower)
            if q_words >= 2:
                return 'faq'
        if any(p in lower for p in ('@type', 'schema.org', 'json-ld')):
            return 'structured_data'

        # -------- 4. NER --------
        if _NER_ENABLED:
            # Bias the NER by prepending page_context (title/URL). Short
            # chunks alone are noisy; page title is a strong prior.
            ner_input = content
            if page_context:
                ner_input = f"{page_context}\n{content}"

            ner_label = dominant_entity_type(ner_input)
            if ner_label == 'country' and _PYCOUNTRY_ENABLED:
                # Confirm the country signal against pycountry to avoid
                # treating every GPE (cities, states) as a country.
                entities = detect_entities(ner_input)
                for ent in entities:
                    if ent.get('label') == 'GPE':
                        canonical = cls._canonicalize_country(ent.get('text', ''))
                        if canonical:
                            return 'country'
                # No confirmed sovereign country — fall through.
            elif ner_label:
                return ner_label

        # -------- 5. page_type heuristics / fallback --------
        if section.get('page_type') == 'card':
            return 'card'

        return 'content'

    # ============================================================
    # Main entry point
    # ============================================================

    @classmethod
    def chunk_structure(
        cls,
        structure: Dict[str, Any],
        content_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            with open(cls.DEBUG_LOG_PATH, 'w', encoding='utf-8') as f:
                f.write("=== CHUNKING DEBUG LOG ===\n")
                f.write(f"Started: {datetime.now().isoformat()}\n")
                f.write(f"{'='*80}\n\n")
        except Exception:
            pass

        cls._debug_log("🧩 CHUNK_STRUCTURE START", {
            'content_type': content_type,
            'structure_keys': list(structure.keys()),
            'page_title': structure.get('page_title'),
            'source_url': structure.get('source_url'),
            'ner_enabled': _NER_ENABLED,
            'pycountry_enabled': _PYCOUNTRY_ENABLED,
        })

        chunks: List[Dict[str, Any]] = []
        default_title = (
            structure.get("page_title")
            or structure.get("metadata", {}).get("title")
            or "Untitled Page"
        )
        chunk_index: int = 0
        source_url = structure.get("source_url") or ""

        # Page context string used to bias NER on every chunk.
        page_context = " ".join(
            part for part in (default_title, source_url) if part
        )

        main_content = structure.get("main_content", {})
        sections_count = len(main_content.get("sections", []))
        tables_count = len(main_content.get("tables", []))
        lists_count = len(main_content.get("lists", []))
        cards_count = len(main_content.get("cards", []))

        # ============================================================
        # 1. SECTIONS
        # ============================================================
        processed_sections = 0
        empty_sections = 0
        split_sections = 0

        for section_position, section in enumerate(
            main_content.get("sections", []), start=1
        ):
            content = section.get("content", "")
            if cls.is_empty_chunk(content):
                empty_sections += 1
                continue

            processed_sections += 1

            base_heading_path = cls.build_heading_path(section, default_title)

            parts = cls._split_section_by_tokens(
                content, base_heading_path, cls.MAX_TOKENS
            )
            if len(parts) > 1:
                split_sections += 1

            entity_type = cls._detect_entity_type(
                section,
                content_type,
                page_context=page_context,
            )

            for part_idx, part_content in enumerate(parts, 1):
                if len(parts) > 1:
                    suffix = f"Part {part_idx} of {len(parts)}"
                else:
                    suffix = None
                heading_path = cls.build_heading_path(
                    section, default_title, suffix=suffix
                )

                chunk = {
                    "content": part_content,
                    "heading_path": heading_path,
                    "chunk_type": section.get("chunk_type", "section"),
                    "chunk_category": "main_content",
                    "entity_type": entity_type,
                    "token_count": len(part_content.split()),
                    "source_url": section.get("source_url") or source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": False,
                        "heading": section.get("heading", ""),
                        "part_index": part_idx if len(parts) > 1 else None,
                        "part_count": len(parts) if len(parts) > 1 else None,
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(part_content.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(part_content),
                    "chunk_index": chunk_index,
                    "position": section_position,
                    "ordinal_index": section.get("ordinal_index"),
                }
                chunks.append(cls.score_chunk(chunk))
                chunk_index += 1

        cls._debug_log("📋 SECTION PROCESSING COMPLETE", {
            'total_sections': sections_count,
            'processed': processed_sections,
            'empty_skipped': empty_sections,
            'sections_split': split_sections,
        })

        # ============================================================
        # 2. TABLES
        # ============================================================
        table_summary_chunks = 0
        table_row_chunks = 0

        for table in main_content.get("tables", []) or []:
            heading_path = cls.build_heading_path(table, default_title)

            summary_text = cls._format_table_summary(table)
            if not cls.is_empty_chunk(summary_text):
                chunk = {
                    "content": summary_text,
                    "heading_path": heading_path,
                    "chunk_type": "table_summary",
                    "chunk_category": "main_content",
                    "entity_type": "table",
                    "token_count": len(summary_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": True,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": False,
                        "table_headers": table.get("headers", []),
                        "row_count": len(table.get("row_texts") or table.get("rows") or []),
                        "col_count": len(table.get("headers", [])),
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(summary_text.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(summary_text),
                    "chunk_index": chunk_index,
                    "ordinal_index": table.get("ordinal_index"),
                }
                chunk_index += 1
                table_summary_chunks += 1
                chunks.append(cls.score_chunk(chunk))

            row_chunks = cls._table_to_row_chunks(
                table,
                default_title,
                source_url,
                heading_path,
                start_index=chunk_index,
            )
            for rc in row_chunks:
                rc["chunk_index"] = chunk_index
                chunk_index += 1
                table_row_chunks += 1
                chunks.append(rc)

        cls._debug_log("📊 TABLE PROCESSING COMPLETE", {
            'total_tables': tables_count,
            'summary_chunks': table_summary_chunks,
            'row_chunks': table_row_chunks,
        })
        logger.info(
            f"📊 Tables: {table_summary_chunks} summary chunks, "
            f"{table_row_chunks} row chunks"
        )

        # ============================================================
        # 3. LISTS
        # ============================================================
        list_chunks_created = 0
        list_item_chunks_created = 0

        for list_data in main_content.get("lists", []) or []:
            items = list_data.get("items", []) or []
            if not items:
                continue

            force_promote = (
                len(items) >= cls.MIN_LIST_ITEMS_TO_FORCE_PROMOTION
                and list_data.get("type") in ("ordered", "unordered")
            )

            list_type = list_data.get("type", "unordered")
            base_heading_path = cls.build_heading_path(list_data, default_title)
            item_ordinals = list_data.get("item_ordinal_indices") or []

            list_text = cls._format_list_as_text(list_data)
            if not cls.is_empty_chunk(list_text):
                chunk = {
                    "content": list_text,
                    "heading_path": base_heading_path,
                    "chunk_type": "list",
                    "chunk_category": "main_content",
                    "entity_type": "list",
                    "token_count": len(list_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": True,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": False,
                        "list_type": list_type,
                        "item_count": len(items),
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(list_text.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(list_text),
                    "chunk_index": chunk_index,
                    "position": chunk_index,
                    "ordinal_index": list_data.get("ordinal_index"),
                }
                chunk_index += 1
                list_chunks_created += 1
                chunks.append(cls.score_chunk(chunk))

            for idx, item in enumerate(items, 1):
                text = str(item).strip()
                # Force-promoted lists (>=10 items) bypass the normal
                # 10-char minimum: every substantive item must become its
                # own chunk so it can carry an ordinal_index. Only the
                # hard floor (3 chars) is enforced.
                if force_promote:
                    if len(text) < cls.MIN_ITEM_CHARS_HARD_FLOOR:
                        continue
                elif len(text) < cls.MIN_ITEM_CHARS_FOR_OWN_CHUNK:
                    continue

                content = f"List item {idx} of {len(items)}\n{text}"
                item_heading = cls.build_heading_path(
                    list_data, default_title, suffix=f"Item {idx}"
                )
                ordinal = item_ordinals[idx - 1] if (idx - 1) < len(item_ordinals) else None
                chunk = {
                    "content": content,
                    "heading_path": item_heading,
                    "chunk_type": "list_item",
                    "chunk_category": "main_content",
                    "entity_type": "list_item",
                    "token_count": len(content.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": True,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": False,
                        "list_type": list_type,
                        "item_index": idx,
                        "item_count": len(items),
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(content),
                    "chunk_index": chunk_index,
                    "position": idx,
                    "ordinal_index": ordinal,
                }
                chunk_index += 1
                list_item_chunks_created += 1
                chunks.append(cls.score_chunk(chunk))

        cls._debug_log("📋 LIST PROCESSING COMPLETE", {
            'total_lists': lists_count,
            'list_chunks': list_chunks_created,
            'list_item_chunks': list_item_chunks_created,
        })
        logger.info(
            f"📋 Lists: {list_chunks_created} list chunks, "
            f"{list_item_chunks_created} item chunks"
        )

        # ============================================================
        # 4. CARDS
        # ============================================================
        card_chunks_created = 0
        for card in main_content.get("cards", []) or []:
            card_text = cls._format_card_as_text(card)
            if cls.is_empty_chunk(card_text):
                continue

            heading_path = cls.build_heading_path(card, default_title)
            explicit = card.get("entity_type")
            if explicit:
                entity_type = explicit
            elif card.get("price") or card.get("is_product"):
                entity_type = "product"
            else:
                # Let NER decide based on the card text + page context.
                entity_type = cls._detect_entity_type(
                    {"content": card_text, "chunk_type": "card"},
                    content_type,
                    page_context=page_context,
                )
                if entity_type == "content":
                    entity_type = "card"

            chunk = {
                "content": card_text,
                "heading_path": heading_path,
                "chunk_type": "card",
                "chunk_category": "main_content",
                "entity_type": entity_type,
                "token_count": len(card_text.split()),
                "source_url": source_url,
                "page_title": default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "has_structured_data": False,
                    "card_name": card.get("name", ""),
                    "is_product": entity_type == "product",
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(card_text.encode("utf-8")).hexdigest(),
                "chunk_simhash": cls._simhash(card_text),
                "chunk_index": chunk_index,
                "ordinal_index": card.get("ordinal_index"),
            }
            chunk_index += 1
            card_chunks_created += 1
            chunks.append(cls.score_chunk(chunk))

        cls._debug_log("🃏 CARD PROCESSING COMPLETE", {
            'total_cards': cards_count,
            'chunks_created': card_chunks_created,
        })
        logger.info(f"🃏 Created {card_chunks_created} card chunks")

        # ============================================================
        # 5. STRUCTURED DATA
        # ============================================================
        structured_data = structure.get("structured_data", {})
        structured_chunks_created = 0

        if structured_data and any(structured_data.values()):
            structured_text = cls._format_structured_data_as_text(structured_data)
            if not cls.is_empty_chunk(structured_text):
                heading_path = [default_title, "Structured Data"]
                chunk = {
                    "content": structured_text,
                    "heading_path": heading_path,
                    "chunk_type": "structured_data",
                    "chunk_category": "main_content",
                    "entity_type": "structured_data",
                    "token_count": len(structured_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": True,
                        "structured_data_keys": list(structured_data.keys()),
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(structured_text.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(structured_text),
                    "chunk_index": chunk_index,
                }
                chunk_index += 1
                structured_chunks_created += 1
                chunks.append(cls.score_chunk(chunk))

            for entity in structured_data.get("entities", []) or []:
                if not isinstance(entity, dict):
                    continue
                fields = [
                    ("Name", entity.get("name")),
                    ("Description", entity.get("description")),
                    ("Price", entity.get("price")),
                    ("Currency", entity.get("currency")),
                    ("SKU", entity.get("sku")),
                    ("URL", entity.get("url")),
                ]
                entity_text = "\n".join(
                    f"{key}: {value}" for key, value in fields if value not in (None, "")
                )
                if cls.is_empty_chunk(entity_text):
                    continue
                entity_type = entity.get("entity_type", "structured_entity")
                chunk = {
                    "content": entity_text,
                    "heading_path": [default_title, entity_type.title()],
                    "chunk_type": "mixed",
                    "chunk_category": "main_content",
                    "entity_type": entity_type,
                    "token_count": len(entity_text.split()),
                    "source_url": entity.get("url") or source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_structured_data": True,
                        "structured_entity": entity_type,
                        "attributes": {
                            key: value for key, value in fields if value not in (None, "")
                        },
                        "relevance_score": 0.0,
                        "quality_score": 1.0,
                    },
                    "chunk_hash": hashlib.sha256(entity_text.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(entity_text),
                    "chunk_index": chunk_index,
                }
                chunk_index += 1
                structured_chunks_created += 1
                chunks.append(cls.score_chunk(chunk))

        logger.info(f"📐 Created {structured_chunks_created} structured data chunks")

        # ============================================================
        # 6. MEDIA CHUNKS
        # ============================================================
        media_chunks = structure.get("media_chunks", [])
        image_chunks_created = 0
        skipped_media = 0

        for media_chunk in media_chunks:
            content = media_chunk.get("content", "")
            if cls.is_empty_chunk(content):
                skipped_media += 1
                continue

            media_type = media_chunk.get("media_type", "image")

            if media_type == "table":
                skipped_media += 1
                continue

            src = (media_chunk.get("source_url") or "").lower()
            if any(sig in src for sig in cls.DECORATIVE_IMAGE_SIGNALS):
                skipped_media += 1
                continue

            desc = (media_chunk.get("description") or "").strip()
            if len(desc) < cls.MIN_IMAGE_DESC_CHARS:
                skipped_media += 1
                continue

            if desc.lower().startswith("image asset:") and len(desc) < 150:
                skipped_media += 1
                continue

            heading_path = [default_title, "Image"]
            chunk = {
                "content": content,
                "heading_path": heading_path,
                "chunk_type": "image_description",
                "chunk_category": "main_content",
                "entity_type": "image_description",
                "token_count": len(content.split()),
                "source_url": media_chunk.get("source_url", source_url),
                "page_title": default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": True,
                    "has_code": False,
                    "has_structured_data": False,
                    "media_type": "image",
                    "alt_text": media_chunk.get("alt_text", ""),
                    "caption": media_chunk.get("caption", ""),
                    "section_heading": media_chunk.get("section_heading", ""),
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chunk_simhash": cls._simhash(content),
                "chunk_index": chunk_index,
            }
            chunk_index += 1
            image_chunks_created += 1
            chunks.append(cls.score_chunk(chunk))

        cls._debug_log("🖼️ MEDIA CHUNK PROCESSING COMPLETE", {
            'total_media_chunks': len(media_chunks),
            'image_chunks_created': image_chunks_created,
            'skipped': skipped_media,
        })
        logger.info(
            f"🖼️ Media: {image_chunks_created} image chunks kept, "
            f"{skipped_media} skipped"
        )

        # ============================================================
        # 7. PRODUCT DATA
        # ============================================================
        product_data = main_content.get("product_data", {}) or {}
        has_product_signal = (
            structure.get("page_type") == "detail"
            or any(c.get("entity_type") == "product" for c in chunks)
        )
        if product_data and has_product_signal:
            product_text = cls._format_product_data(product_data)
            if not cls.is_empty_chunk(product_text):
                heading_path = [default_title, "Product Details"]
                chunk = {
                    "content": product_text,
                    "heading_path": heading_path,
                    "chunk_type": "product",
                    "chunk_category": "main_content",
                    "entity_type": "product",
                    "token_count": len(product_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": False,
                        "has_images": True,
                        "has_code": False,
                        "has_structured_data": False,
                        "is_product": True,
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(product_text.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(product_text),
                    "chunk_index": chunk_index,
                }
                chunk_index += 1
                chunks.append(cls.score_chunk(chunk))

        # ============================================================
        # 8. FALLBACK
        # ============================================================
        if not chunks:
            all_text = main_content.get("all_text", "")
            cls._debug_log("⚠️ FALLBACK: No chunks created, checking all_text", {
                'all_text_length': len(all_text),
            })
            if not cls.is_empty_chunk(all_text):
                heading_path = [default_title]
                # Try NER on the fallback too — it may be the only chance
                # we get to classify a page.
                entity_type = cls._detect_entity_type(
                    {"content": all_text},
                    content_type,
                    page_context=page_context,
                )
                chunk = {
                    "content": all_text,
                    "heading_path": heading_path,
                    "chunk_type": "content",
                    "chunk_category": "main_content",
                    "entity_type": entity_type,
                    "token_count": len(all_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "has_structured_data": False,
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(all_text.encode("utf-8")).hexdigest(),
                    "chunk_simhash": cls._simhash(all_text),
                    "chunk_index": chunk_index,
                }
                chunk_index += 1
                chunks.append(cls.score_chunk(chunk))
                logger.info("✅ Created fallback chunk from all_text")

        # ============================================================
        # 9. PAGE SUMMARY
        # ============================================================
        summary_parts = [f"Page: {default_title}"]
        if source_url:
            summary_parts.append(f"URL: {source_url}")
        summary_parts.append(
            f"Contains: {sections_count} sections, {tables_count} tables, "
            f"{lists_count} lists, {cards_count} cards"
        )
        top_headings = [
            s.get("heading", "") for s in main_content.get("sections", [])[:3]
            if s.get("heading")
        ]
        if top_headings:
            summary_parts.append("Top sections: " + " | ".join(top_headings))

        summary_content = "\n".join(summary_parts)
        if not cls.is_empty_chunk(summary_content):
            heading_path = [default_title]
            chunk = {
                "content": summary_content,
                "heading_path": heading_path,
                "chunk_type": "summary",
                "chunk_category": "main_content",
                "entity_type": "summary",
                "token_count": cls.token_count(summary_content),
                "source_url": source_url,
                "page_title": default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "has_structured_data": False,
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(summary_content.encode("utf-8")).hexdigest(),
                "chunk_simhash": cls._simhash(summary_content),
                "chunk_index": chunk_index,
            }
            chunk_index += 1
            chunks.append(cls.score_chunk(chunk))

        # ============================================================
        # 10. UI SUMMARY chunks
        # ============================================================
        ui_summary = structure.get("ui_summary", [])
        ui_chunks_created = 0

        for summary_chunk in ui_summary:
            content = summary_chunk.get("content", "")
            if cls.is_empty_chunk(content):
                continue

            chunk_category = summary_chunk.get("chunk_category", "footer")
            heading_path = summary_chunk.get("heading_path", [default_title])

            chunk = {
                "content": content,
                "heading_path": heading_path,
                "chunk_type": "summary",
                "chunk_category": chunk_category,
                "entity_type": "summary",
                "token_count": cls.token_count(content),
                "source_url": source_url,
                "page_title": default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "has_structured_data": False,
                    "source_type": summary_chunk.get("source_type", ""),
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chunk_simhash": cls._simhash(content),
                "chunk_index": chunk_index,
            }
            chunk_index += 1
            ui_chunks_created += 1
            chunks.append(cls.score_chunk(chunk))

        logger.info(f"📊 Created {ui_chunks_created} UI summary chunks")

        # ============================================================
        # 11. Information density
        # ============================================================
        for chunk in chunks:
            chunk['information_density'] = cls._calc_information_density(
                chunk.get('content', '')
            )

        # ============================================================
        # 12. Final validation
        # ============================================================
        before_filter = len(chunks)
        chunks = [c for c in chunks if not cls.is_empty_chunk(c.get('content', ''))]
        after_filter = len(chunks)

        # ============================================================
        # 13. Stats
        # ============================================================
        category_counts: Dict[str, int] = {}
        entity_counts: Dict[str, int] = {}
        chunk_type_counts: Dict[str, int] = {}
        total_words = 0
        for chunk in chunks:
            cat = chunk.get('chunk_category', 'unknown')
            category_counts[cat] = category_counts.get(cat, 0) + 1
            entity = chunk.get('entity_type', 'unknown')
            entity_counts[entity] = entity_counts.get(entity, 0) + 1
            ct = chunk.get('chunk_type', 'unknown')
            chunk_type_counts[ct] = chunk_type_counts.get(ct, 0) + 1
            total_words += len(chunk.get('content', '').split())

        cls._debug_log("✅ CHUNK_STRUCTURE COMPLETE", {
            'total_chunks': len(chunks),
            'total_words': total_words,
            'category_counts': category_counts,
            'entity_counts': entity_counts,
            'chunk_type_counts': chunk_type_counts,
        })

        logger.info(f"✅ Created {len(chunks)} chunks ({total_words} words total)")
        logger.info(f"   Entity types: {entity_counts}")
        logger.info(f"   Chunk types: {chunk_type_counts}")

        return chunks

    # ============================================================
    # Misc helpers
    # ============================================================

    @staticmethod
    def _format_product_data(product_data: Dict[str, Any]) -> str:
        parts = []
        name = product_data.get('name')
        if name:
            parts.append(f"Product: {name}")
        price = product_data.get('price')
        if price:
            parts.append(f"Price: {price}")
        description = product_data.get('description')
        if description:
            parts.append(f"Description: {description}")
        specs = product_data.get('specifications', {})
        if specs:
            spec_lines = [f"  {key}: {value}" for key, value in specs.items()]
            if spec_lines:
                parts.append("Specifications:\n" + "\n".join(spec_lines))
        availability = product_data.get('availability')
        if availability:
            parts.append(f"Availability: {availability}")
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _calc_information_density(text: str) -> float:
        link_words = sum(
            1 for word in text.split() if word.startswith(('http://', 'https://'))
        )
        words = len(text.split())
        return words / max(link_words + 1, 1)

    @staticmethod
    def chunk_from_processed_content(
        processed_content: Dict[str, Any],
        document_id: str,
        page_version_id: str,
        content_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        chunks = EnhancedChunker.chunk_structure(processed_content, content_type)
        for chunk in chunks:
            chunk['document_id'] = document_id
            chunk['page_version_id'] = page_version_id
            chunk['embedding_status'] = 'PENDING'
            chunk['embedding'] = None
        return chunks

    @classmethod
    def _table_row_entity_type(
        cls,
        table: Dict[str, Any],
        headers: List[str],
        default_title: str,
    ) -> str:
        """
        Classify repeated tabular records for global entity operations.

        Order:
          1. header/heading keyword rules (cheap, deterministic)
          2. NER over a sample of rows (catches countries/products that
             don't use the exact words we look for)
          3. fallback -> 'table_row'
        """
        searchable = " ".join([
            default_title,
            str(table.get("heading", "")),
            " ".join(headers),
        ]).lower()

        # -------- 1. keyword rules --------
        if "country" in searchable or "countries" in searchable:
            return "country"
        if all(signal in searchable for signal in ("capital", "population")):
            return "country"
        if any(sig in searchable for sig in ("sku", "price", "product", "msrp")):
            return "product"

        # -------- 2. NER on a small sample of rows --------
        if _NER_ENABLED:
            row_texts = table.get("row_texts") or []
            sample_rows = [str(r) for r in row_texts[:5]] or [
                " ".join(str(c) for c in row)
                for row in (table.get("rows") or [])[:5]
            ]
            sample = " ".join([default_title, *sample_rows])
            if sample.strip():
                ner_label = dominant_entity_type(sample)
                if ner_label == 'country' and _PYCOUNTRY_ENABLED:
                    entities = detect_entities(sample)
                    for ent in entities:
                        if ent.get('label') == 'GPE':
                            if cls._canonicalize_country(ent.get('text', '')):
                                return 'country'
                elif ner_label:
                    return ner_label

        # -------- 3. fallback --------
        return "table_row"