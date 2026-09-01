import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EnhancedChunker:
    """Structure-aware chunker that preserves headings and semantic blocks."""

    TARGET_TOKENS = 600
    MAX_TOKENS = 800
    MIN_CHUNK_WORDS = 20
    PARAGRAPH_OVERLAP_RATIO = 0.12

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
    def dedupe_preserve_order(items: List[str]) -> List[str]:
        seen = set()
        deduped: List[str] = []
        for item in items:
            norm = EnhancedChunker.normalize_text(item)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            deduped.append(norm)
        return deduped

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

        relevance = min(1.0, (0.35 * min(heading_depth / 4, 1.0)) + (0.45 * min(words / 200, 1.0)) + (0.2 * (1.0 if has_table or has_list or has_images else 0.0)))
        quality = min(1.0, (0.5 * min(words / 160, 1.0)) + (0.2 if has_table else 0.0) + (0.2 if has_list else 0.0) + (0.1 if content.strip() else 0.0))

        chunk["content_structure"]["relevance_score"] = round(relevance, 4)
        chunk["content_structure"]["quality_score"] = round(quality, 4)
        return chunk

    @staticmethod
    def _paragraph_overlap(text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        if len(sentences) <= 1:
            return text
        overlap_count = max(1, min(2, len(sentences) // 5))
        overlap = " ".join(sentences[-overlap_count:])
        return overlap

    @staticmethod
    def _deduplicate_faq_content(content: str) -> str:
        """Remove duplicate FAQ question/answer pairs."""
        lines = content.split('\n')
        
        faq_patterns = [
            r'^Q\s*[:.]',
            r'^Question\s*[:.]',
            r'^\d+\s*[.)]\s*',
            r'^[A-Z]\s*[.)]\s*',
            r'^What\s|^Where\s|^How\s|^Why\s|^When\s|^Can\s|^Does\s|^Is\s|^Are\s',
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
            
            is_question = False
            question_text = stripped
            
            for pattern in faq_patterns:
                match = re.search(pattern, stripped, re.I)
                if match:
                    question_text = re.sub(pattern, '', stripped, flags=re.I).strip()
                    is_question = True
                    break
            
            if is_question and question_text:
                normalized = re.sub(r'[^\w\s]', '', question_text).lower().strip()
                
                if normalized in seen_questions:
                    i += 1
                    while i < len(lines):
                        next_line = lines[i].strip()
                        if not next_line:
                            i += 1
                            break
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
        """Remove UI structural text that adds no semantic value."""
        patterns = [
            r'\[popover:\]',
            r'Expand all\s*Collapse all',
            r'\(Annual subscription-auto renews\)',
            r'Price does not include tax',
            r'Buy now\s*Try for free\s*See trial terms',
            r'\$[\d,]+\s*(user/month|per user|/month)',
            r'See trial terms\s*\d*',
        ]
        
        for pattern in patterns:
            content = re.sub(pattern, '', content, flags=re.I)
        
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        return content.strip()

    @staticmethod
    def _deduplicate_chunks(chunks: List[Dict]) -> List[Dict]:
        """Remove duplicate chunks using content signatures."""
        seen = set()
        deduped = []
        
        for chunk in chunks:
            content = chunk.get('content', '')
            if not content:
                continue
            
            # Create normalized signature
            normalized = re.sub(r'\s+', ' ', content)
            normalized = re.sub(r'[^\w\s]', '', normalized)
            normalized = normalized.lower().strip()
            
            # Use first 300 chars as signature
            signature = normalized[:300]
            
            if signature and signature not in seen:
                seen.add(signature)
                deduped.append(chunk)
        
        return deduped

    @staticmethod
    def _split_by_paragraphs(content: str, heading_path: List[str]) -> List[Dict]:
        """Split long content into paragraph-level chunks."""
        paragraphs = content.split('\n\n')
        chunks = []
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if EnhancedChunker.token_count(para) < EnhancedChunker.MIN_CHUNK_WORDS:
                continue
            
            chunks.append({
                'content': para,
                'heading_path': heading_path,
                'chunk_type': 'text',
                'token_count': EnhancedChunker.token_count(para),
                'source_url': '',
                'page_title': heading_path[0] if heading_path else '',
                'content_structure': {
                    'has_table': False,
                    'has_list': False,
                    'has_images': False,
                    'has_code': False,
                    'relevance_score': 0.0,
                    'quality_score': 0.0,
                },
                'chunk_hash': hashlib.sha256(para.encode('utf-8')).hexdigest(),
            })
        
        return chunks

    @staticmethod
    def _format_table_as_text(table: Dict) -> str:
        """Convert table to readable text format."""
        parts = []
        
        headers = table.get('headers', [])
        if headers:
            parts.append("Headers: " + ", ".join(headers))
        
        rows = table.get('rows', [])
        for row in rows[:20]:  # Limit to 20 rows
            parts.append(", ".join(row))
        
        if len(rows) > 20:
            parts.append(f"... and {len(rows) - 20} more rows")
        
        summary = table.get('summary', '')
        if summary:
            parts.append("Summary: " + summary)
        
        return "\n".join(parts) if parts else ""

    @staticmethod
    def chunk_structure(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Structure-aware chunking that preserves ALL content."""
        
        chunks: List[Dict[str, Any]] = []
        default_title = structure.get("page_title") or structure.get("metadata", {}).get("title") or "Untitled Page"
        chunk_index: int = 0
        
        # Process sections
        for section in structure.get("sections", []):
            content = EnhancedChunker.normalize_text(section.get("content", ""))
            if not content:
                continue
            
            # 🆕 Clean structural text
            content = EnhancedChunker._clean_structural_text(content)
            
            # 🆕 Deduplicate FAQ content
            content = EnhancedChunker._deduplicate_faq_content(content)
            
            if EnhancedChunker.token_count(content) < EnhancedChunker.MIN_CHUNK_WORDS:
                continue
            
            heading_path = EnhancedChunker.build_heading_path(section, default_title)
            
            # 🆕 Split long sections intelligently
            if EnhancedChunker.token_count(content) > EnhancedChunker.MAX_TOKENS:
                sub_chunks = EnhancedChunker._split_by_paragraphs(content, heading_path)
                for sub in sub_chunks:
                    sub['chunk_index'] = chunk_index
                    chunks.append(EnhancedChunker.score_chunk(sub))
                    chunk_index += 1
            else:
                chunk = {
                    "content": content,
                    "heading_path": heading_path,
                    "chunk_type": "section",
                    "token_count": EnhancedChunker.token_count(content),
                    "source_url": section.get("source_url") or structure.get("source_url") or "",
                    "page_title": structure.get("page_title") or default_title,
                    "content_structure": {
                        "has_table": False,
                        "has_list": bool(section.get("content", "").lower().find("-") != -1 or "1." in section.get("content", "")),
                        "has_images": False,
                        "has_code": False,
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "chunk_index": chunk_index,
                }
                chunks.append(EnhancedChunker.score_chunk(chunk))
                chunk_index += 1
        
        # Process tables
        for table in structure.get("tables", []) or []:
            table_text = EnhancedChunker._format_table_as_text(table)
            if table_text and EnhancedChunker.token_count(table_text) >= EnhancedChunker.MIN_CHUNK_WORDS:
                chunk = {
                    "content": table_text,
                    "heading_path": table.get("heading_path") or [default_title],
                    "chunk_type": "table",
                    "token_count": EnhancedChunker.token_count(table_text),
                    "source_url": structure.get("source_url") or "",
                    "page_title": structure.get("page_title") or default_title,
                    "content_structure": {
                        "has_table": True,
                        "has_list": False,
                        "has_images": False,
                        "has_code": False,
                        "table_headers": table.get("headers", []),
                        "table_rows": table.get("rows", [])[:10],
                        "caption": table.get("caption"),
                        "relevance_score": 0.0,
                        "quality_score": 0.0,
                    },
                    "chunk_hash": hashlib.sha256(table_text.encode("utf-8")).hexdigest(),
                    "chunk_index": chunk_index
                }
                chunk_index += 1
                chunks.append(EnhancedChunker.score_chunk(chunk))
        
        # Process cards
        for card in structure.get("cards", []) or []:
            body = EnhancedChunker.normalize_text(card.get("description") or "")
            if not body or EnhancedChunker.token_count(body) < EnhancedChunker.MIN_CHUNK_WORDS:
                continue
            
            chunk = {
                "content": body,
                "heading_path": card.get("heading_path") or [default_title],
                "chunk_type": "card",
                "token_count": EnhancedChunker.token_count(body),
                "source_url": structure.get("source_url") or "",
                "page_title": structure.get("page_title") or default_title,
                "content_structure": {
                    "has_table": False,
                    "has_list": False,
                    "has_images": False,
                    "has_code": False,
                    "card_title": card.get("title"),
                    "relevance_score": 0.0,
                    "quality_score": 0.0,
                },
                "chunk_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "chunk_index": chunk_index
            }
            chunk_index += 1
            chunks.append(EnhancedChunker.score_chunk(chunk))
        
        # 🆕 Final deduplication
        chunks = EnhancedChunker._deduplicate_chunks(chunks)
        
        # Filter out chunks that are too short after dedup
        chunks = [c for c in chunks if EnhancedChunker.token_count(c.get('content', '')) >= EnhancedChunker.MIN_CHUNK_WORDS]
        
        logger.info("Created %s structure-aware chunks", len(chunks))
        return chunks
