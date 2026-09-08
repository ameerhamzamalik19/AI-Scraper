# processors/chunker.py

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional
from datetime import datetime
import os

logger = logging.getLogger(__name__)


class EnhancedChunker:
    """
    ONE AND ONLY chunking stage.
    
    Responsibilities:
    - Take structured content from ContentProcessor
    - ONE section = ONE chunk (preserve grouping)
    - Format tables, lists, products as readable text
    - Validate chunks (only reject truly empty)
    - Add metadata (heading_path, entity_type, etc.)
    
    Does NOT:
    - Clean HTML (already done in ContentProcessor)
    - Filter by length (preserve all content)
    - Remove content based on junk patterns (already done in ContentProcessor)
    """

    TARGET_TOKENS = 600
    MAX_TOKENS = 800

    CATEGORY_WEIGHTS = {
        'main_content': 1.0,
        'header_nav': 0.75,
        'footer': 0.65,
        'sidebar': 0.5,
        'excluded': 0.0
    }

    ENTITY_BOOSTS = {
        'product': 1.3,
        'article_body': 1.2,
        'faq': 1.4,
        'code_example': 1.1,
        'table': 1.1,
        'card': 1.0,
        'image': 1.05,
        'content': 1.0,
    }

    # ✅ Only check for truly empty content - NO cleaning
    EMPTY_PATTERNS = [
        r'^\s*$',           # Empty
        r'^[.,;:!?]+$',     # Only punctuation
        r'^\{\{.*\}\}$',    # Template placeholders (should already be filtered)
        r'^\[.*\]$',        # Empty brackets (should already be filtered)
    ]

    # Debug logging
    DEBUG_ENABLED = True
    DEBUG_LOG_PATH = "/app/debug_chunking.log"

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
        
        # Also log to console
        logger.debug(log_entry[:500] + "..." if len(log_entry) > 500 else log_entry)

    @staticmethod
    def is_empty_chunk(text: str) -> bool:
        """Check if chunk is truly empty (not just short)."""
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

    @staticmethod
    def build_heading_path(section: Dict[str, Any], default_title: str) -> List[str]:
        heading_path = section.get("heading_path") or []
        if not heading_path:
            heading = section.get("heading") or default_title
            heading_path = [default_title, heading] if heading != default_title else [default_title]
        return [p for p in heading_path if p]

    @staticmethod
    def score_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
        content = chunk.get("content", "")
        words = EnhancedChunker.token_count(content)
        heading_depth = len(chunk.get("heading_path", []))
        has_table = bool(chunk.get("content_structure", {}).get("has_table"))
        has_list = bool(chunk.get("content_structure", {}).get("has_list"))
        has_images = bool(chunk.get("content_structure", {}).get("has_images"))

        category = chunk.get('chunk_category', 'main_content')
        category_weight = EnhancedChunker.CATEGORY_WEIGHTS.get(category, 0.5)

        entity_type = chunk.get('entity_type', 'content')
        entity_boost = EnhancedChunker.ENTITY_BOOSTS.get(entity_type, 1.0)

        # Small chunks still get relevance - they contain valuable info
        relevance = min(1.0, (
            (0.35 * min(heading_depth / 4, 1.0)) +
            (0.45 * min(words / 200, 1.0)) +
            (0.2 * (1.0 if has_table or has_list or has_images else 0.0))
        )) * category_weight * entity_boost

        quality = min(1.0, (
            (0.5 * min(words / 160, 1.0)) +
            (0.2 if has_table else 0.0) +
            (0.2 if has_list else 0.0) +
            (0.1 if content.strip() else 0.0)
        ))

        chunk["content_structure"]["relevance_score"] = round(relevance, 4)
        chunk["content_structure"]["quality_score"] = round(quality, 4)
        chunk["category_weight"] = category_weight
        chunk["entity_boost"] = entity_boost
        return chunk

    @staticmethod
    def _format_table_as_text(table: Dict) -> str:
        """Convert table to readable text format - PRESERVE ALL rows."""
        parts = []
        
        headers = table.get('headers', [])
        if headers:
            parts.append("Headers: " + ", ".join(headers))
        
        rows = table.get('rows', [])
        # ✅ Don't truncate - preserve all rows for maximum information
        for row in rows:
            parts.append(", ".join(row))
        
        summary = table.get('summary', '')
        if summary:
            parts.append("Summary: " + summary)
        
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _detect_entity_type(section: Dict[str, Any], content_type: Optional[str] = None) -> str:
        """Detect entity type from section content."""
        content = section.get('content', '').lower()
        
        # Detect product
        if any(word in content for word in ['price', '$', 'buy', 'add to cart', 'in stock', 'sku']):
            return 'product'
        
        # Detect code
        if section.get('chunk_type') == 'code' or '```' in content:
            return 'code_example'
        
        # Detect FAQ
        if '?' in content and any(word in content for word in ['how', 'what', 'why', 'when', 'where']):
            return 'faq'
        
        # Detect table
        if section.get('chunk_type') == 'table':
            return 'table'
        
        # Detect card
        if section.get('chunk_type') == 'card':
            return 'card'
        
        return 'content'

    @classmethod
    def chunk_structure(
        cls, 
        structure: Dict[str, Any], 
        content_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Structure-aware chunking that preserves ALL content.
        
        CRITICAL RULES:
        1. ONE section = ONE chunk (preserve grouping)
        2. NO length-based filtering (keep ALL content)
        3. NO cleaning (content already cleaned by ContentProcessor)
        4. ONLY reject truly empty chunks
        """
        
        # Clear debug log on each run
        try:
            with open(cls.DEBUG_LOG_PATH, 'w', encoding='utf-8') as f:
                f.write(f"=== CHUNKING DEBUG LOG ===\n")
                f.write(f"Started: {datetime.now().isoformat()}\n")
                f.write(f"{'='*80}\n\n")
        except:
            pass

        cls._debug_log(f"🧩 CHUNK_STRUCTURE START", {
            'content_type': content_type,
            'structure_keys': list(structure.keys()),
            'page_title': structure.get('page_title'),
            'source_url': structure.get('source_url')
        })

        chunks: List[Dict[str, Any]] = []
        default_title = structure.get("page_title") or structure.get("metadata", {}).get("title") or "Untitled Page"
        chunk_index: int = 0
        source_url = structure.get("source_url") or ""
        
        # ============================================================
        # 1. Process SECTIONS - ONE SECTION = ONE CHUNK
        # ============================================================
        main_content = structure.get("main_content", {})
        
        sections_count = len(main_content.get("sections", []))
        tables_count = len(main_content.get("tables", []))
        lists_count = len(main_content.get("lists", []))
        
        cls._debug_log(f"📊 INPUT STRUCTURE STATS", {
            'sections_count': sections_count,
            'tables_count': tables_count,
            'lists_count': lists_count,
            'has_all_text': bool(main_content.get("all_text"))
        })

        processed_sections = 0
        empty_sections = 0
        
        for section in main_content.get("sections", []):
            content = section.get("content", "")
            
            # ✅ Only skip if truly empty
            if cls.is_empty_chunk(content):
                empty_sections += 1
                cls._debug_log(f"⏭️ EMPTY SECTION SKIPPED", {
                    'heading': section.get("heading", "No heading"),
                    'content_preview': content[:100] + "..." if len(content) > 100 else content
                })
                continue
            
            processed_sections += 1
            
            # Get heading path
            heading_path = section.get("heading_path", [])
            if not heading_path:
                heading = section.get("heading", default_title)
                heading_path = [default_title, heading] if heading != default_title else [default_title]
            
            # ✅ ONE section = ONE chunk - NO splitting
            entity_type = cls._detect_entity_type(section, content_type)
            
            chunk = {
                "content": content,
                "heading_path": heading_path,
                "chunk_type": section.get("chunk_type", "section"),
                "chunk_category": "main_content",
                "entity_type": entity_type,
                "token_count": len(content.split()),
                "source_url": section.get("source_url") or source_url,
                "page_title": default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "heading": section.get("heading", ""),
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chunk_index": chunk_index,
            }
            
            chunks.append(cls.score_chunk(chunk))
            chunk_index += 1
            
            # Log every 10th section
            if processed_sections % 10 == 0:
                cls._debug_log(f"📝 PROCESSED SECTION #{processed_sections}", {
                    'heading': section.get("heading", "No heading"),
                    'content_length': len(content),
                    'word_count': len(content.split()),
                    'entity_type': entity_type
                })
        
        cls._debug_log(f"📋 SECTION PROCESSING COMPLETE", {
            'total_sections': sections_count,
            'processed': processed_sections,
            'empty_skipped': empty_sections,
            'chunks_created': chunk_index
        })
        
        # ============================================================
        # 2. Process TABLES - ONE TABLE = ONE CHUNK
        # ============================================================
        table_chunks_created = 0
        for table in main_content.get("tables", []) or []:
            table_text = cls._format_table_as_text(table)
            if not cls.is_empty_chunk(table_text):
                chunk = {
                    "content": table_text,
                    "heading_path": table.get("heading_path") or [default_title],
                    "chunk_type": "table",
                    "chunk_category": "main_content",
                    "entity_type": "table",
                    "token_count": len(table_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": True,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "table_headers": table.get("headers", []),
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(table_text.encode("utf-8")).hexdigest(),
                    "chunk_index": chunk_index
                }
                chunk_index += 1
                table_chunks_created += 1
                chunks.append(cls.score_chunk(chunk))
        
        cls._debug_log(f"📊 TABLE PROCESSING COMPLETE", {
            'total_tables': tables_count,
            'chunks_created': table_chunks_created
        })
        
        # ============================================================
        # 3. Process LISTS - ONE LIST = ONE CHUNK
        # ============================================================
        list_chunks_created = 0
        for list_item in main_content.get("lists", []) or []:
            items = list_item.get("items", [])
            if items:
                list_text = f"{list_item.get('type', 'unordered')} list:\n" + "\n".join(f"- {item}" for item in items)
                if not cls.is_empty_chunk(list_text):
                    chunk = {
                        "content": list_text,
                        "heading_path": [default_title],
                        "chunk_type": "list",
                        "chunk_category": "main_content",
                        "entity_type": "content",
                        "token_count": len(list_text.split()),
                        "source_url": source_url,
                        "page_title": default_title,
                        "content_structure": {
                            "has_table": False,
                            "has_list": True,
                            "has_images": False,
                            "has_code": False,
                            "list_type": list_item.get("type"),
                            "relevance_score": 0.0,
                            "quality_score": 0.0,
                        },
                        "chunk_hash": hashlib.sha256(list_text.encode("utf-8")).hexdigest(),
                        "chunk_index": chunk_index
                    }
                    chunk_index += 1
                    list_chunks_created += 1
                    chunks.append(cls.score_chunk(chunk))
        
        cls._debug_log(f"📋 LIST PROCESSING COMPLETE", {
            'total_lists': lists_count,
            'chunks_created': list_chunks_created
        })
        
        # ============================================================
        # 4. Process PRODUCT DATA - ONE PRODUCT = ONE CHUNK
        # ============================================================
        if content_type == "ecommerce":
            product_data = main_content.get("product_data", {})
            if product_data:
                product_text = cls._format_product_data(product_data)
                if not cls.is_empty_chunk(product_text):
                    chunk = {
                        "content": product_text,
                        "heading_path": [default_title, "Product Details"],
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
                            "is_product": True,
                            "relevance_score": 0.0,
                            "quality_score": 0.0,
                        },
                        "chunk_hash": hashlib.sha256(product_text.encode("utf-8")).hexdigest(),
                        "chunk_index": chunk_index
                    }
                    chunk_index += 1
                    chunks.append(cls.score_chunk(chunk))
        
        # ============================================================
        # 5. FALLBACK: If no chunks, use all_text
        # ============================================================
        if not chunks:
            all_text = main_content.get("all_text", "")
            cls._debug_log(f"⚠️ FALLBACK: No chunks created, checking all_text", {
                'all_text_length': len(all_text),
                'all_text_preview': all_text[:500] + "..." if len(all_text) > 500 else all_text
            })
            
            if not cls.is_empty_chunk(all_text):
                chunk = {
                    "content": all_text,
                    "heading_path": [default_title],
                    "chunk_type": "content",
                    "chunk_category": "main_content",
                    "entity_type": "content",
                    "token_count": len(all_text.split()),
                    "source_url": source_url,
                    "page_title": default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(all_text.encode("utf-8")).hexdigest(),
                    "chunk_index": chunk_index
                }
                chunk_index += 1
                chunks.append(cls.score_chunk(chunk))
                cls._debug_log(f"✅ Created fallback chunk from all_text", {
                    'length': len(all_text),
                    'word_count': len(all_text.split())
                })
                logger.info(f"✅ Created fallback chunk from all_text")
            else:
                cls._debug_log(f"❌ all_text is empty, no fallback possible")
        
        # ============================================================
        # 6. Process UI SUMMARY chunks (medium weight)
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
                "entity_type": "content",
                "token_count": cls.token_count(content),
                "source_url": source_url,
                "page_title": default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "source_type": summary_chunk.get("source_type", ""),
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chunk_index": chunk_index
            }
            chunk_index += 1
            ui_chunks_created += 1
            chunks.append(cls.score_chunk(chunk))
        
        cls._debug_log(f"📊 UI SUMMARY PROCESSING COMPLETE", {
            'total_ui_chunks': len(ui_summary),
            'chunks_created': ui_chunks_created
        })
        
        # ============================================================
        # 7. Calculate information density for each chunk
        # ============================================================
        for chunk in chunks:
            chunk['information_density'] = cls._calc_information_density(chunk.get('content', ''))
        
        # ============================================================
        # 8. Final validation - ONLY remove truly empty chunks
        # ============================================================
        before_filter = len(chunks)
        chunks = [c for c in chunks if not cls.is_empty_chunk(c.get('content', ''))]
        after_filter = len(chunks)
        
        cls._debug_log(f"🔍 FINAL FILTERING", {
            'before_filter': before_filter,
            'after_filter': after_filter,
            'removed': before_filter - after_filter
        })
        
        # Log statistics
        category_counts = {}
        entity_counts = {}
        total_words = 0
        for chunk in chunks:
            cat = chunk.get('chunk_category', 'unknown')
            category_counts[cat] = category_counts.get(cat, 0) + 1
            entity = chunk.get('entity_type', 'unknown')
            entity_counts[entity] = entity_counts.get(entity, 0) + 1
            total_words += len(chunk.get('content', '').split())
        
        cls._debug_log(f"✅ CHUNK_STRUCTURE COMPLETE", {
            'total_chunks': len(chunks),
            'total_words': total_words,
            'category_counts': category_counts,
            'entity_counts': entity_counts,
            'sample_chunk': chunks[0] if chunks else None
        })
        
        logger.info(f"✅ Created {len(chunks)} chunks ({total_words} words total)")
        logger.info(f"   Categories: {category_counts}")
        logger.info(f"   Entities: {entity_counts}")
        
        return chunks

    @staticmethod
    def _format_product_data(product_data: Dict[str, Any]) -> str:
        """Format product data into readable text."""
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
            spec_lines = []
            for key, value in specs.items():
                spec_lines.append(f"  {key}: {value}")
            if spec_lines:
                parts.append("Specifications:\n" + "\n".join(spec_lines))
        
        availability = product_data.get('availability')
        if availability:
            parts.append(f"Availability: {availability}")
        
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _calc_information_density(text: str) -> float:
        """Calculate information density: text-to-link ratio."""
        link_words = sum(1 for word in text.split() if word.startswith(('http://', 'https://')))
        words = len(text.split())
        return words / max(link_words + 1, 1)

    @staticmethod
    def chunk_from_processed_content(
        processed_content: Dict[str, Any],
        document_id: str,
        page_version_id: str,
        content_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Convenience method to chunk content from ContentProcessor output."""
        chunks = EnhancedChunker.chunk_structure(processed_content, content_type)
        
        for chunk in chunks:
            chunk['document_id'] = document_id
            chunk['page_version_id'] = page_version_id
            chunk['embedding_status'] = 'PENDING'
            chunk['embedding'] = None
        
        return chunks