import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EnhancedChunker:
    """Structure-aware chunker that preserves headings and semantic blocks.
    
    SIMPLIFIED: Takes sections from content processor and creates chunks.
    No aggressive cleaning - content is already cleaned by content processor.
    """

    TARGET_TOKENS = 600
    MAX_TOKENS = 800
    MIN_CHUNK_WORDS = 3  # Very low threshold - only filter completely empty chunks
    PARAGRAPH_OVERLAP_RATIO = 0.12

    # Category weights for retrieval boosting
    CATEGORY_WEIGHTS = {
        'main_content': 1.0,
        'header_nav': 0.75,
        'footer': 0.65,
        'sidebar': 0.5,
        'excluded': 0.0
    }

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
            heading_path = [default_title, section.get("heading") or "Section"]
        return [p for p in heading_path if p]

    @staticmethod
    def score_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
        content = chunk.get("content", "")
        words = EnhancedChunker.token_count(content)
        heading_depth = len(chunk.get("heading_path", []))
        has_table = bool(chunk.get("content_structure", {}).get("has_table"))
        has_list = bool(chunk.get("content_structure", {}).get("has_list"))
        has_images = bool(chunk.get("content_structure", {}).get("has_images"))

        # Category-based boost
        category = chunk.get('chunk_category', 'main_content')
        category_weight = EnhancedChunker.CATEGORY_WEIGHTS.get(category, 0.5)

        relevance = min(1.0, (
            (0.35 * min(heading_depth / 4, 1.0)) + 
            (0.45 * min(words / 200, 1.0)) + 
            (0.2 * (1.0 if has_table or has_list or has_images else 0.0))
        )) * category_weight

        quality = min(1.0, (
            (0.5 * min(words / 160, 1.0)) + 
            (0.2 if has_table else 0.0) + 
            (0.2 if has_list else 0.0) + 
            (0.1 if content.strip() else 0.0)
        ))

        chunk["content_structure"]["relevance_score"] = round(relevance, 4)
        chunk["content_structure"]["quality_score"] = round(quality, 4)
        chunk["category_weight"] = category_weight
        return chunk

    @staticmethod
    def _format_table_as_text(table: Dict) -> str:
        """Convert table to readable text format."""
        parts = []
        
        headers = table.get('headers', [])
        if headers:
            parts.append("Headers: " + ", ".join(headers))
        
        rows = table.get('rows', [])
        for row in rows[:20]:
            parts.append(", ".join(row))
        
        if len(rows) > 20:
            parts.append(f"... and {len(rows) - 20} more rows")
        
        summary = table.get('summary', '')
        if summary:
            parts.append("Summary: " + summary)
        
        return "\n".join(parts) if parts else ""

    @staticmethod
    def chunk_structure(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Structure-aware chunking that preserves ALL content.
        
        SIMPLIFIED: Takes sections from content processor, creates chunks.
        No cleaning - content is already clean from content processor.
        """
        
        chunks: List[Dict[str, Any]] = []
        default_title = structure.get("page_title") or structure.get("metadata", {}).get("title") or "Untitled Page"
        chunk_index: int = 0
        source_url = structure.get("source_url") or ""
        
        # ============================================================
        # 1. Process MAIN CONTENT (full weight)
        # ============================================================
        main_content = structure.get("main_content", {})
        
        # Process sections - this is the main content
        for section in main_content.get("sections", []):
            # Get content directly - already has heading prefix like "[Heading]\n\nContent"
            content = section.get("content", "")
            
            if not content:
                continue
            
            # Count words using simple split (more reliable)
            word_count = len(content.split())
            
            # Skip only if truly empty or just a few words
            if word_count < EnhancedChunker.MIN_CHUNK_WORDS:
                logger.debug(f"⏭️ Skipping section with {word_count} words (below {EnhancedChunker.MIN_CHUNK_WORDS})")
                continue
            
            # Get heading path
            heading_path = section.get("heading_path", [])
            if not heading_path:
                heading = section.get("heading", default_title)
                heading_path = [default_title, heading] if heading != default_title else [default_title]
            
            # Create chunk
            chunk = {
                "content": content,
                "heading_path": heading_path,
                "chunk_type": section.get("chunk_type", "section"),
                "chunk_category": "main_content",
                "token_count": word_count,
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
            
            chunks.append(EnhancedChunker.score_chunk(chunk))
            chunk_index += 1
        
        # Process tables
        for table in main_content.get("tables", []) or []:
            table_text = EnhancedChunker._format_table_as_text(table)
            if table_text:
                word_count = len(table_text.split())
                if word_count >= EnhancedChunker.MIN_CHUNK_WORDS:
                    chunk = {
                        "content": table_text,
                        "heading_path": table.get("heading_path") or [default_title],
                        "chunk_type": "table",
                        "chunk_category": "main_content",
                        "token_count": word_count,
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
                    chunks.append(EnhancedChunker.score_chunk(chunk))
        
        # Process lists
        for list_item in main_content.get("lists", []) or []:
            items = list_item.get("items", [])
            if items:
                list_text = f"{list_item.get('type', 'unordered')} list:\n" + "\n".join(f"- {item}" for item in items)
                word_count = len(list_text.split())
                if word_count >= EnhancedChunker.MIN_CHUNK_WORDS:
                    chunk = {
                        "content": list_text,
                        "heading_path": [default_title],
                        "chunk_type": "list",
                        "chunk_category": "main_content",
                        "token_count": word_count,
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
                    chunks.append(EnhancedChunker.score_chunk(chunk))
        
        # ============================================================
        # 2. FALLBACK: If no chunks, use all_text
        # ============================================================
        if not chunks:
            all_text = main_content.get("all_text", "")
            if all_text:
                word_count = len(all_text.split())
                if word_count >= EnhancedChunker.MIN_CHUNK_WORDS:
                    chunk = {
                        "content": all_text[:5000],  # Limit size
                        "heading_path": [default_title],
                        "chunk_type": "content",
                        "chunk_category": "main_content",
                        "token_count": word_count,
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
                    chunks.append(EnhancedChunker.score_chunk(chunk))
                    logger.info(f"✅ Created fallback chunk from all_text ({word_count} words)")
        
        # ============================================================
        # 3. Process UI SUMMARY chunks (medium weight)
        # ============================================================
        ui_summary = structure.get("ui_summary", [])
        
        for summary_chunk in ui_summary:
            content = summary_chunk.get("content", "")
            if not content:
                continue
            
            chunk_category = summary_chunk.get("chunk_category", "footer")
            heading_path = summary_chunk.get("heading_path", [default_title])
            
            chunk = {
                "content": content,
                "heading_path": heading_path,
                "chunk_type": "summary",
                "chunk_category": chunk_category,
                "token_count": EnhancedChunker.token_count(content),
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
            chunks.append(EnhancedChunker.score_chunk(chunk))
        
        # ============================================================
        # 4. Final filtering
        # ============================================================
        # Filter out chunks that are too short
        chunks = [c for c in chunks if len(c.get('content', '').split()) >= EnhancedChunker.MIN_CHUNK_WORDS]
        
        # Log statistics
        category_counts = {}
        for chunk in chunks:
            cat = chunk.get('chunk_category', 'unknown')
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        logger.info(f"Created {len(chunks)} chunks: {category_counts}")
        
        return chunks

    @staticmethod
    def chunk_from_processed_content(
        processed_content: Dict[str, Any],
        document_id: str,
        page_version_id: str
    ) -> List[Dict[str, Any]]:
        """
        Convenience method to chunk content from ContentProcessor output.
        
        Args:
            processed_content: Output from ContentProcessor.process_html()
            document_id: Document ID for the processed content
            page_version_id: Page version ID
        
        Returns:
            List of chunk dictionaries ready for database insertion
        """
        chunks = EnhancedChunker.chunk_structure(processed_content)
        
        # Add database fields
        for chunk in chunks:
            chunk['document_id'] = document_id
            chunk['page_version_id'] = page_version_id
            chunk['embedding_status'] = 'PENDING'
            chunk['embedding'] = None
        
        return chunks