# api/routes/retrieval_pipeline.py
from typing import Any, Dict, List, Optional, Tuple
try:
    from ollama import Client
except ImportError:
    Client = None
from database_sync import execute_query
from utils.embedding_service import get_embedding
import os
import logging
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4
from api.routes.query_plan import (
    QueryAnalyzer,
    QueryIntent,
    QueryPlan,
    QueryPlanValidationError,
    QueryScope,
)
from api.routes.retrieval_queries import (
    AVAILABLE_CATEGORIES_QUERY,
    CHUNK_STATS_QUERY,
    COUNT_ENTITY_QUERY,
    COUNT_ORDINAL_COLLECTION_QUERY,
    COUNT_PAGES_QUERY,
    EMBEDDING_SAMPLE_QUERY,
    PRODUCT_RECORDS_QUERY,
    build_entity_filter_clauses,
    build_entity_list_query,
    build_keyword_query,
    build_vector_query,
)
from database_sync import execute_one, execute_update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INFORMATIONAL_FALLBACK = "I don't have enough information about that in the available content."


# ============================================
# CONFIGURATION
# ============================================

CATEGORY_BOOST = {
    'main_content': 1.0,
    'header_nav': 0.8,
    'footer': 0.7,
    'sidebar': 0.5,
    'excluded': 0.0,
    'ui_summary': 0.6,
}

ENTITY_BOOST = {
    'product': 1.3,
    'article_body': 1.2,
    'faq': 1.4,
    'code_example': 1.1,
    'table': 1.1,
    'card': 1.0,
    'image': 1.05,
    'content': 1.0,
    'navigation': 0.5,
    'footer': 0.4,
}

EXCLUDED_CATEGORIES = ['excluded', 'filter', 'modal', 'cookie']

POSITIONAL_QUERY_PATTERNS = {
    'first': re.compile(r'\b(top|first|number\s*1|no\.\s*1|highest[- ]ranked|beginning)\b', re.I),
    'last': re.compile(r'\b(last|bottom|lowest[- ]ranked|final)\b', re.I),
}

# ------------------------------------------------------------------
# ORDINAL INTENT DETECTION
# ------------------------------------------------------------------
#
# Ordinal questions come in many surface forms. The patterns below
# recognize the common ones across any page with a ranked or numbered
# collection. The goal is to route to the deterministic ordinal path
# whenever the user names a position, so we don't rely on vector
# similarity to find "the Nth item" (which it's bad at, because the
# number contributes almost nothing to the embedding).
#
# Surface forms covered by "nth":
#   - "number N"              -> number 5
#   - "no. N" / "no N"        -> no. 5
#   - "#N"                    -> #5
#   - "item N"                -> item 5
#   - "rank N" / "ranked N"   -> rank 5
#   - "ranked number N"       -> ranked number 5
#   - "position N"            -> position 5
#   - "spot N"                -> spot 5
#   - "place N"               -> place 5
#   - "Nth" (ordinal numeral) -> 5th, 12th, 100th
#   - word forms              -> second, third, ..., twentieth
#
# "first" and "last" catch superlative phrasings. Bare "top" is not
# included because "top blogs" usually refers to the whole list, not
# its first element.
#
# Groups as seen by Python's Match.group():
#   1 = digits after a prefix ("number 5", "#5", "item 5", ...)
#   2 = digits preceding st/nd/rd/th ("5th")
#   3 = word form ("second", "twelfth", ...)
ORDINAL_PATTERNS = {
    "first": re.compile(
        r"\b("
        r"number\s*one|number\s*1|#\s*1|no\.?\s*1|rank(?:ed)?\s*1|top\s*1|"
        r"first|1st|highest[-\s]ranked|best|most\s+popular|leading|"
        r"top[-\s]ranked|top[-\s]rated"
        r")\b",
        re.I,
    ),
    "last": re.compile(
        r"\b(last|final|bottom|lowest[-\s]ranked|worst|least\s+popular)\b",
        re.I,
    ),
    "nth": re.compile(
        r"\b(?:"
        # Prefix words followed by an optional "number" and digits.
        r"(?:rank(?:ed)?|position|spot|place|item|number|no\.?|#)"
        r"\s+(?:number\s+)?(\d+)"
        # Digits with ordinal suffix.
        r"|(\d+)(?:st|nd|rd|th)"
        # Spelled-out ordinals.
        r"|(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|"
        r"seventeenth|eighteenth|nineteenth|twentieth)"
        r")\b",
        re.I,
    ),
}

# Spelled-out ordinals to their integer value. Kept separate from the
# regex so the mapping is easy to extend.
NTH_WORD_TO_INDEX = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}

# Entities that cannot meaningfully be counted.
UNCOUNTABLE_ENTITIES = {"content", "unknown", None, "summary", "section", "mixed"}

# Retrieval confidence thresholds
MIN_SIMILARITY_THRESHOLD = 0.1
MIN_RRF_SCORE_THRESHOLD = 0.02
MIN_SCORE_GAP = 0.05

# Phrases that mean "the generator had nothing useful to say".
UNHELPFUL_MARKERS = (
    "i don't have enough information",
    "i don't have that information",
    "i don't have enough specific information",
    "i couldn't find",
    "i can't find",
    "i'm not sure",
)

# Minimum number of ordinal chunks required before we bother building a
# ranked-list block for the LLM. Below this, ordinal resolution is cheap.
MIN_ORDINAL_FOR_RANKED_BLOCK = 5

# Entity types that almost never represent the user's intended collection
# when a larger one exists on the same page. Used as a tiebreaker when
# multiple collections share the same (chat_id, source_url) namespace.
# Nav/UI lists get pushed to the bottom; real content collections win.
COLLECTION_PRIORITY = (
    # Higher = preferred. Sorted descending in the SQL ordering.
    ('text', 'content'),
    ('table', 'table'),
    ('table', 'country'),
    ('table', 'product'),
    ('mixed', 'card'),
    ('list', 'list'),
    ('list_item', 'list_item'),
    ('table_row', 'table_row'),
    ('table_row', 'country'),
    ('table_row', 'product'),
)

_retrieval_log_lock = Lock()
_retrieval_log_dir = Path(
    os.getenv(
        "RETRIEVAL_LOG_DIR",
        Path(__file__).resolve().parents[2] / "retrieval_logs",
    )
)


# ============================================================
# CHAT STATE PERSISTENCE
# ============================================================

def load_chat_state(chat_id: str) -> Dict[str, Any]:
    try:
        row = execute_one(
            "SELECT active_source_url, active_entity, active_entity_attributes, last_answer "
            "FROM chat_state WHERE chat_id = %s",
            (chat_id,),
        )
    except Exception:
        logger.exception("Failed to load chat_state for %s", chat_id)
        row = None
    return dict(row) if row else {
        "active_source_url": None,
        "active_entity": None,
        "active_entity_attributes": [],
        "last_answer": None,
    }


def save_chat_state(chat_id: str, state: Dict[str, Any]) -> None:
    try:
        execute_update(
            """
            INSERT INTO chat_state (chat_id, active_source_url, active_entity,
                                    active_entity_attributes, last_answer, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                active_source_url = EXCLUDED.active_source_url,
                active_entity = EXCLUDED.active_entity,
                active_entity_attributes = EXCLUDED.active_entity_attributes,
                last_answer = EXCLUDED.last_answer,
                updated_at = NOW()
            """,
            (
                chat_id,
                state.get("active_source_url"),
                state.get("active_entity"),
                json.dumps(state.get("active_entity_attributes", [])),
                json.dumps(state.get("last_answer")),
            ),
        )
    except Exception:
        logger.exception("Failed to save chat_state for %s", chat_id)


def store_retrieved_chunks(
    chat_id: str,
    user_question: str,
    standalone_question: str,
    chunks: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> None:
    try:
        _retrieval_log_dir.mkdir(parents=True, exist_ok=True)
        log_path = _retrieval_log_dir / f"chat_{chat_id}.txt"
        with _retrieval_log_lock, log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("\n" + "=" * 100 + "\n")
            log_file.write(f"Retrieved at: {datetime.now(timezone.utc).isoformat()}\n")
            log_file.write(f"Chat ID: {chat_id}\n")
            log_file.write(f"Original question: {user_question}\n")
            log_file.write(f"Standalone question: {standalone_question}\n")
            log_file.write(f"Retrieval metadata: {json.dumps(metadata, default=str, sort_keys=True)}\n")
            log_file.write(f"Retrieved chunks: {len(chunks)}\n\n")

            for index, chunk in enumerate(chunks, start=1):
                log_file.write(f"--- Chunk {index} ---\n")
                for field in (
                    "chunk_id", "document_id", "source_url", "chunk_category",
                    "entity_type", "section_title", "heading_path", "similarity",
                    "adjusted_similarity", "rrf_score", "vector_rank", "keyword_rank",
                    "ordinal_index", "is_anchor",
                ):
                    if field in chunk:
                        log_file.write(f"{field}: {chunk[field]}\n")
                log_file.write("content:\n")
                log_file.write(str(chunk.get("content", "")))
                log_file.write("\n\n")
    except Exception:
        logger.exception("Failed to store retrieved chunks for chat %s", chat_id)


def format_generation_error(error: Exception) -> str:
    error_text = str(error)
    retry_match = re.search(r"try again in ([^.]+)", error_text, re.IGNORECASE)

    if "429" in error_text or "rate_limit" in error_text.lower() or "rate limit" in error_text.lower():
        retry_text = f" Please try again in {retry_match.group(1)}." if retry_match else " Please try again shortly."
        return f"The AI service is temporarily rate-limited.{retry_text}"

    logger.exception("LLM generation failed")
    return "I couldn't generate an answer right now. Please try again later."


SYSTEM_PROMPT = """You are a helpful assistant for a specific website. You have access to content scraped from that site.

## HOW TO RESPOND:

**For greetings and small talk** (hi, how are you, thanks, etc.):
- Respond naturally and briefly like a human would
- Don't mention the website or context
- Keep it to 1-2 sentences max

**For questions clearly about the website content:**
- Answer using ONLY the provided context
- Synthesize naturally â€” never say "based on the context" or "the chunks say"
- If the context doesn't answer it, say: "I don't have that information available."

**For questions that mix general knowledge + website content:**
- Use the context as your primary source
- You may fill in basic, universally-known facts (definitions, common concepts) to make the answer flow naturally
- Never speculate or fabricate specific details, numbers, or claims not in the context

**For ranked-list questions** (which is #1, most popular, first, last, Nth, "item N"):
- If a "Ranked list" block is present in the user message, treat it as the authoritative answer key for ordering.
- Answer directly with the item at the requested position. Do NOT say you lack information if the ranked list contains an answer.
- If a specific item's chunk is provided with fields like "Rank: 5" or "Mentions: 8,923", answer with those exact values.

## RULES:
1. Never mention "chunks," "context," "scraped content," or internal workings
2. Never cite sources or reference numbers
3. Be concise and direct
4. For factual questions about the site's topic, stick to the context
5. When genuinely uncertain, say so simply â€” don't over-hedge
6. Write like a knowledgeable human, not a research paper"""


def build_user_prompt(
    question: str,
    context_chunks: List[Dict[str, Any]],
    chat_state: Optional[Dict[str, Any]] = None,
    ranked_list_block: Optional[str] = None,
) -> str:
    context_parts = []
    for chunk in context_chunks:
        content = chunk.get('content', '').strip()
        if content:
            context_parts.append(content)

    context_text = "\n\n---\n\n".join(context_parts)

    topic_line = ""
    if chat_state and chat_state.get("active_source_url"):
        entity_part = (
            f" â€” entity: {chat_state['active_entity']}"
            if chat_state.get("active_entity") else ""
        )
        topic_line = (
            f"\n(The user has been asking about: "
            f"{chat_state['active_source_url']}{entity_part}.)\n"
        )

    # When a ranked list is present, restructure the whole prompt so the
    # list is the FIRST thing the model reads. Small models tend to ignore
    # instructions buried in long context; putting the answer key up front
    # and repeating the ordinal instructions makes the behavior reliable.
    if ranked_list_block:
        return f"""The user is asking a ranked-list question. The ranked list below is the authoritative answer key.

{ranked_list_block}
{topic_line}
Here are additional context excerpts from the same page (for descriptive detail, not ordering):

{context_text}

The user asked: {question}

Answer the question using the ranked list above as the source of truth for ordering, and the context excerpts for descriptive detail.
- For "which is #1 / most popular / first / top / highest ranked", answer with item 1 of the ranked list.
- For "second / third / Nth / item N", answer with item N of the ranked list.
- For "last / bottom / lowest", answer with the final item.
- Do NOT say you lack information if the ranked list contains the answer.

Your answer:"""

    return f"""Here is the information you have access to:
{topic_line}
{context_text}

The user asked: {question}

Write a natural, conversational answer using ONLY this information.
- Synthesize the information into flowing prose
- Don't mention that you're using context or sources
- Write like you already know this
- Be direct and helpful
- If the context chunk contains labeled fields like "Rank: 5" or "Mentions: 8,923", answer with those exact values.
- If you don't have enough information, simply say: "I don't have enough information about that in the available content."
- If the information is only weakly related, say: "I don't have enough specific information about that."

Your answer:"""


def _positional_query_kind(query_text: str) -> Optional[str]:
    for kind, pattern in POSITIONAL_QUERY_PATTERNS.items():
        if pattern.search(query_text or ''):
            return kind
    return None


def _apply_positional_order(
    chunks: List[Dict[str, Any]],
    query_text: str,
    source_url_filter: Optional[str],
) -> List[Dict[str, Any]]:
    kind = _positional_query_kind(query_text)
    if not kind or not source_url_filter or not chunks:
        return chunks

    content_chunks = [
        chunk for chunk in chunks
        if chunk.get('entity_type') not in {'summary', 'image', 'image_description'}
        and not chunk.get('is_anchor')
    ]
    if not content_chunks:
        return chunks

    def page_position(chunk: Dict[str, Any]) -> int:
        oi = chunk.get('ordinal_index')
        if oi is not None:
            return int(oi)
        position = chunk.get('position_in_page')
        if position is None or position <= 0:
            position = chunk.get('chunk_index', 0)
        return int(position)

    reverse = kind == 'last'
    ordered = sorted(content_chunks, key=page_position, reverse=reverse)
    selected = ordered[0]
    remainder = [chunk for chunk in chunks if chunk is not selected]
    return [selected] + remainder


# ============================================================
# ORDINAL INTENT
# ============================================================

def detect_ordinal_intent(query: str) -> Optional[Tuple[str, int]]:
    """
    Returns (kind, index). kind in {'first', 'last', 'nth'}.
    index is 1-based for 'nth', -1 for 'last'.

    Recognizes the common surface forms across many page types, not
    just one website. See ORDINAL_PATTERNS for the full list.
    """
    if not query:
        return None
    if ORDINAL_PATTERNS["first"].search(query):
        return ("first", 1)
    if ORDINAL_PATTERNS["last"].search(query):
        return ("last", -1)
    m = ORDINAL_PATTERNS["nth"].search(query)
    if m:
        # Groups: 1 = digits after prefix ("number 5"),
        #         2 = digits in "5th",
        #         3 = word form ("second", "twelfth", ...).
        for group in (m.group(1), m.group(2)):
            if group:
                try:
                    n = int(group)
                    if n >= 1:
                        return ("nth", n)
                except (TypeError, ValueError):
                    continue
        word = (m.group(3) or "").lower()
        if word and word in NTH_WORD_TO_INDEX:
            return ("nth", NTH_WORD_TO_INDEX[word])
    return None


def _dominant_collection(
    chat_id: str,
    source_url: str,
) -> Optional[Dict[str, Any]]:
    """
    Find the dominant ordinal-bearing collection for a page.

    A single page can host multiple collections (e.g. a 50-item ranked list
    plus a 6-item nav list). They share the same (chat_id, source_url)
    namespace and therefore collide on ordinal_index. This picks the one
    the user most likely means: the one with the most distinct ordinal
    values, tie-broken by chunk_type/entity_type priority so real content
    beats incidental nav widgets.
    """
    rows = execute_query(
        """
        SELECT chunk_type,
               entity_type,
               COUNT(DISTINCT ordinal_index) AS span,
               COUNT(*) AS n
        FROM chunks
        WHERE chat_id = %s
          AND source_url = %s
          AND ordinal_index IS NOT NULL
        GROUP BY chunk_type, entity_type
        ORDER BY span DESC, n DESC
        """,
        (chat_id, source_url),
    )
    if not rows:
        return None

    # Prefer the widest-span collection; among ties, use COLLECTION_PRIORITY.
    priority = {pair: i for i, pair in enumerate(COLLECTION_PRIORITY)}
    def sort_key(r):
        span = int(r.get("span") or 0)
        n = int(r.get("n") or 0)
        prio = priority.get((r.get("chunk_type"), r.get("entity_type")), 999)
        return (-span, -n, prio)

    ranked = sorted(rows, key=sort_key)
    best = ranked[0]
    if int(best.get("span") or 0) < MIN_ORDINAL_FOR_RANKED_BLOCK:
        # Not a real collection — too few distinct positions.
        return None
    return {
        "chunk_type": best.get("chunk_type"),
        "entity_type": best.get("entity_type"),
        "span": int(best.get("span") or 0),
        "count": int(best.get("n") or 0),
    }


def _count_ordinal_chunks(chat_id: str, source_url: str) -> int:
    """
    Return how many distinct ordinal positions exist in the *dominant*
    collection for this page. Falls back to the raw count when no dominant
    collection can be identified.
    """
    dom = _dominant_collection(chat_id, source_url)
    if dom:
        return dom["span"]
    row = execute_one(
        """
        SELECT COUNT(DISTINCT ordinal_index) AS n
        FROM chunks
        WHERE chat_id = %s
          AND source_url = %s
          AND ordinal_index IS NOT NULL
        """,
        (chat_id, source_url),
    )
    return int(row["n"] or 0) if row else 0


def fetch_ordinal_chunk(
    chat_id: str,
    source_url: str,
    kind: str,
    index: int,
) -> Optional[Dict[str, Any]]:
    """
    Fetch the chunk at the requested ordinal position on a given page.

    Scope is restricted to the dominant collection for the page so that
    a 50-item ranked list is preferred over a 6-item nav list that happens
    to share the same ordinal_index namespace. Returns None if no ordinal
    collection was detected for the page.
    """
    dom = _dominant_collection(chat_id, source_url)
    if not dom:
        # Fall back to any ordinal chunk — better than nothing.
        dom_clause = ""
        dom_params: List[Any] = []
    else:
        dom_clause = " AND c.chunk_type = %s AND c.entity_type = %s"
        dom_params = [dom["chunk_type"], dom["entity_type"]]

    if kind == "last":
        sql = f"""
            SELECT c.id AS chunk_id, c.document_id, c.source_url, c.content,
                   c.entity_type, c.chunk_category, c.chunk_index,
                   c.position_in_page, c.ordinal_index, c.heading_path
            FROM chunks c
            WHERE c.chat_id = %s
              AND c.source_url = %s
              AND c.ordinal_index IS NOT NULL
              {dom_clause}
            ORDER BY c.ordinal_index DESC
            LIMIT 1
        """
        params = [chat_id, source_url, *dom_params]
    else:
        sql = f"""
            SELECT c.id AS chunk_id, c.document_id, c.source_url, c.content,
                   c.entity_type, c.chunk_category, c.chunk_index,
                   c.position_in_page, c.ordinal_index, c.heading_path
            FROM chunks c
            WHERE c.chat_id = %s
              AND c.source_url = %s
              AND c.ordinal_index = %s
              {dom_clause}
            LIMIT 1
        """
        params = [chat_id, source_url, max(0, index - 1), *dom_params]

    row = execute_one(sql, tuple(params))
    return dict(row) if row else None


def _build_ranked_list_block(
    chat_id: str,
    source_url_filter: Optional[str],
    max_items: int = 60,
) -> Optional[str]:
    """
    Build a numbered list of the page's ordinal items, in order, so the
    LLM can answer superlative/ordinal questions deterministically.

    Uses only the dominant collection so nav widgets don't pollute the
    list. Returns None if the page doesn't have enough ordinal chunks.
    """
    if not source_url_filter:
        return None

    dom = _dominant_collection(chat_id, source_url_filter)
    if not dom or dom["span"] < MIN_ORDINAL_FOR_RANKED_BLOCK:
        return None

    rows = execute_query(
        """
        SELECT ordinal_index, heading_path, content
        FROM chunks
        WHERE chat_id = %s
          AND source_url = %s
          AND ordinal_index IS NOT NULL
          AND chunk_type = %s
          AND entity_type = %s
        ORDER BY ordinal_index ASC
        LIMIT %s
        """,
        (
            chat_id,
            source_url_filter,
            dom["chunk_type"],
            dom["entity_type"],
            max_items,
        ),
    )
    if not rows:
        return None

    lines = []
    for r in rows:
        # Prefer the deepest heading_path entry as the item label.
        path = r.get("heading_path") or []
        label = ""
        if isinstance(path, list) and path:
            label = str(path[-1]).strip()
        if not label:
            # Fall back to the first line of content
            first_line = (r.get("content") or "").strip().split("\n", 1)[0]
            label = first_line[:80].strip()
        ordinal = r.get("ordinal_index")
        lines.append(f"{int(ordinal) + 1}. {label}")

    return (
        "Ranked list on this page (visual order, top to bottom):\n"
        + "\n".join(lines)
    )


# ============================================================
# PAGE ANCHORS
# ============================================================

def augment_with_page_anchors(
    chunks: List[Dict[str, Any]],
    chat_id: str,
    source_url_filter: Optional[str],
    max_anchors: int = 3,
) -> List[Dict[str, Any]]:
    """
    Prepend page shape anchors (summary + top ordinal items) so the
    generator always has context. Anchors are tagged `is_anchor=True`
    and have NULL similarity so they don't affect the confidence gate.

    Anchors are drawn from the dominant collection only, matching the
    behaviour of fetch_ordinal_chunk / _build_ranked_list_block.
    """
    if not source_url_filter:
        return chunks

    dom = _dominant_collection(chat_id, source_url_filter)

    try:
        if dom:
            anchor_rows = execute_query(
                """
                SELECT c.id AS chunk_id, c.document_id, c.source_url, c.content,
                       c.entity_type, c.chunk_category, c.chunk_index,
                       c.ordinal_index,
                       NULL::float AS similarity,
                       NULL::float AS adjusted_similarity
                FROM chunks c
                WHERE c.chat_id = %s
                  AND c.source_url = %s
                  AND (
                        c.entity_type = 'summary'
                     OR (c.ordinal_index IS NOT NULL
                         AND c.chunk_type = %s
                         AND c.entity_type = %s)
                  )
                ORDER BY
                  CASE c.entity_type
                    WHEN 'summary' THEN 0
                    ELSE 1
                  END,
                  COALESCE(c.ordinal_index, 0) ASC,
                  c.chunk_index ASC
                LIMIT %s
                """,
                (
                    chat_id,
                    source_url_filter,
                    dom["chunk_type"],
                    dom["entity_type"],
                    max_anchors,
                ),
            )
        else:
            anchor_rows = execute_query(
                """
                SELECT c.id AS chunk_id, c.document_id, c.source_url, c.content,
                       c.entity_type, c.chunk_category, c.chunk_index,
                       c.ordinal_index,
                       NULL::float AS similarity,
                       NULL::float AS adjusted_similarity
                FROM chunks c
                WHERE c.chat_id = %s
                  AND c.source_url = %s
                  AND (
                        c.entity_type = 'summary'
                     OR c.ordinal_index IS NOT NULL
                  )
                ORDER BY
                  CASE c.entity_type
                    WHEN 'summary' THEN 0
                    ELSE 1
                  END,
                  COALESCE(c.ordinal_index, 0) ASC,
                  c.chunk_index ASC
                LIMIT %s
                """,
                (chat_id, source_url_filter, max_anchors),
            )
    except Exception:
        logger.exception("Failed to fetch page anchors for %s", source_url_filter)
        return chunks

    anchor_ids = {row["chunk_id"] for row in anchor_rows}
    existing_ids = {c["chunk_id"] for c in chunks}
    additions = []
    for r in anchor_rows:
        if r["chunk_id"] in existing_ids:
            continue
        d = dict(r)
        d["is_anchor"] = True
        additions.append(d)
    return additions + chunks


# ============================================================
# CLARIFICATION FALLBACK
# ============================================================

def build_clarification(
    user_question: str,
    chunks: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    chat_state: Dict[str, Any],
) -> str:
    if not chunks:
        anchor = chat_state.get("active_source_url") or "the site"
        entity = chat_state.get("active_entity") or "content"
        return (
            f"I couldn't find anything about that in {anchor}. "
            f"The page covers {entity}-related content. "
            f"Try rephrasing, or ask about a specific item on the page."
        )

    top = chunks[0]
    preview = (top.get("content") or "").strip().replace("\n", " ")[:180]
    source = top.get("source_url") or chat_state.get("active_source_url") or "the page"
    return (
        f"I'm not fully sure what you meant. The closest thing I found is:\n\n"
        f"\"{preview}â€¦\"\n\n"
        f"from {source}. Did you mean to ask something about that?"
    )


def _is_unhelpful(answer: str) -> bool:
    """Detect responses where the generator effectively gave up."""
    if not answer:
        return True
    lowered = answer.lower()
    return any(marker in lowered for marker in UNHELPFUL_MARKERS)


def create_standalone_question(
    user_question: str,
    chat_history: List[Dict[str, Any]] = None,
    chat_state: Optional[Dict[str, Any]] = None,
) -> str:
    """Rewrite follow-up questions preserving all distinctive entities."""
    history_messages = [
        f"{message['role']}: {message['content']}"
        for message in (chat_history or [])
        if message.get("role") in {"user", "assistant"}
        and message.get("content")
    ]

    if not history_messages and not chat_state:
        return user_question

    state_text = ""
    if chat_state:
        state_text = (
            f"\nActive topic (from earlier turns):\n"
            f"- source_url: {chat_state.get('active_source_url')}\n"
            f"- entity: {chat_state.get('active_entity')}\n"
            f"- attributes: {chat_state.get('active_entity_attributes')}\n"
            f"- last answer: {chat_state.get('last_answer')}\n"
        )

    history_text = "\n".join(history_messages)

    prompt = f"""Conversation history:
{history_text}
{state_text}
Latest user question: {user_question}

Rewrite the latest user question as a standalone question.
CRITICAL RULES:
1. Preserve ALL distinctive nouns, names, and entities.
2. If the question refers to something from the active topic ("it", "that one",
   "the first one", "the same site"), resolve it using the active topic.
3. If no URL is mentioned but the active topic has a source_url, keep the
   source_url in the rewrite ONLY when the question is clearly about that source.
4. Keep technical terms intact.
5. Return only the rewritten question, with no explanation.

Rewritten question:"""

    try:
        client = Client(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + os.getenv("OLLAMA_API_KEY")}
        )

        response = client.chat(
            model="gpt-oss:20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Rewrite follow-up questions into standalone questions "
                        "preserving all entities and proper nouns. Return only "
                        "the question."
                    ),
                },
                {"role": "user", "content": prompt}
            ],
            stream=False
        )

        standalone_question = response['message']['content'].strip()

        print(f"đź“ť ORIGINAL: {user_question}")
        print(f"đź“ť REWRITTEN: {standalone_question}")

        return standalone_question or user_question
    except Exception as e:
        logger.warning("Standalone question generation failed: %s", e)
        return user_question


# ============================================================
# HYBRID RETRIEVAL WITH RRF
# ============================================================

def retrieve_relevant_chunks_hybrid(
    query_text: str,
    query_embedding: List[float],
    chat_id: str,
    limit: int = 10,
    rrf_k: int = 60,
    min_similarity: float = MIN_SIMILARITY_THRESHOLD,
    min_rrf_score: float = MIN_RRF_SCORE_THRESHOLD,
    min_score_gap: float = MIN_SCORE_GAP,
    entity_filter: Optional[str] = None,
    source_url_filter: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Hybrid retrieval combining vector similarity and keyword search using RRF.
    """
    if not query_text or not query_embedding:
        return [], {'error': 'No query or embedding provided'}

    vector = "[{}]".format(",".join(str(value) for value in query_embedding))

    entity_clause, entity_params, source_url_clause, source_url_params = build_entity_filter_clauses(
        entity_filter,
        source_url_filter,
    )

    try:
        vector_query = build_vector_query(entity_clause, source_url_clause)
        vector_params = [vector, chat_id] + entity_params + source_url_params + [vector, limit * 3]
        vector_results = execute_query(vector_query, tuple(vector_params))

        keyword_query = build_keyword_query(entity_clause, source_url_clause)
        keyword_params = [query_text, query_text, chat_id] + entity_params + source_url_params + [limit * 3]
        keyword_results = execute_query(keyword_query, tuple(keyword_params))

        print(f"đź”Ť Vector results: {len(vector_results)}, Keyword results: {len(keyword_results)}")

        has_lexical_match = len(keyword_results) > 0

        if not vector_results and not keyword_results:
            return [], {
                'has_results': False,
                'has_lexical_match': False,
                'confidence': 0.0,
                'reason': 'No results from either search method'
            }

        if vector_results and not keyword_results:
            print("âš ď¸Ź Only vector results available - no lexical match")
            for chunk in vector_results:
                category = chunk.get('chunk_category', 'main_content')
                entity_type = chunk.get('entity_type', 'content')
                category_boost = CATEGORY_BOOST.get(category, 0.5)
                entity_boost = ENTITY_BOOST.get(entity_type, 1.0)
                chunk['category_boost'] = category_boost
                chunk['entity_boost'] = entity_boost
                chunk['adjusted_similarity'] = chunk.get('similarity', 0) * category_boost * entity_boost

            vector_results.sort(key=lambda x: x.get('adjusted_similarity', 0), reverse=True)
            vector_results = _apply_positional_order(
                vector_results, query_text, source_url_filter
            )

            top_score = vector_results[0].get('adjusted_similarity', 0) if vector_results else 0

            if top_score < min_similarity:
                print(f"âš ď¸Ź Low confidence: top similarity {top_score:.4f} < {min_similarity}")
                return [], {
                    'has_results': True,
                    'has_lexical_match': False,
                    'confidence': top_score,
                    'reason': f'Low similarity ({top_score:.4f} < {min_similarity})'
                }

            return vector_results[:limit], {
                'has_results': True,
                'has_lexical_match': False,
                'confidence': top_score,
                'reason': 'Vector search only'
            }

        if keyword_results and not vector_results:
            print("đź“ť Only keyword results available")
            for chunk in keyword_results:
                category = chunk.get('chunk_category', 'main_content')
                entity_type = chunk.get('entity_type', 'content')
                category_boost = CATEGORY_BOOST.get(category, 0.5)
                entity_boost = ENTITY_BOOST.get(entity_type, 1.0)
                chunk['category_boost'] = category_boost
                chunk['entity_boost'] = entity_boost
                chunk['adjusted_similarity'] = chunk.get('similarity', 0) * category_boost * entity_boost

            keyword_results.sort(key=lambda x: x.get('adjusted_similarity', 0), reverse=True)
            keyword_results = _apply_positional_order(
                keyword_results, query_text, source_url_filter
            )
            return keyword_results[:limit], {
                'has_results': True,
                'has_lexical_match': True,
                'confidence': 0.8,
                'reason': 'Keyword search only'
            }

        # RRF Fusion
        scores = defaultdict(float)
        chunk_data = {}

        for rank, result in enumerate(vector_results):
            chunk_id = result['chunk_id']
            scores[chunk_id] += 1 / (rrf_k + rank + 1)
            chunk_data[chunk_id] = result
            chunk_data[chunk_id]['vector_rank'] = rank + 1
            chunk_data[chunk_id]['similarity'] = result.get('similarity', 0)

        for rank, result in enumerate(keyword_results):
            chunk_id = result['chunk_id']
            scores[chunk_id] += 1 / (rrf_k + rank + 1)
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = result
            chunk_data[chunk_id]['keyword_rank'] = rank + 1
            if result.get('similarity', 0) > chunk_data[chunk_id].get('similarity', 0):
                chunk_data[chunk_id]['similarity'] = result.get('similarity', 0)

        for chunk in chunk_data.values():
            category = chunk.get('chunk_category', 'main_content')
            entity_type = chunk.get('entity_type', 'content')
            category_boost = CATEGORY_BOOST.get(category, 0.5)
            entity_boost = ENTITY_BOOST.get(entity_type, 1.0)
            chunk['category_boost'] = category_boost
            chunk['entity_boost'] = entity_boost
            chunk['rrf_score'] = scores[chunk['chunk_id']]
            chunk['adjusted_similarity'] = scores[chunk['chunk_id']] * category_boost * entity_boost

        sorted_chunks = sorted(chunk_data.values(), key=lambda x: x['adjusted_similarity'], reverse=True)
        sorted_chunks = _apply_positional_order(
            sorted_chunks, query_text, source_url_filter
        )

        top_score = sorted_chunks[0]['adjusted_similarity'] if sorted_chunks else 0
        second_score = sorted_chunks[1]['adjusted_similarity'] if len(sorted_chunks) > 1 else 0
        score_gap = top_score - second_score

        print(f"đź“Š Top RRF score: {top_score:.4f}, Score gap: {score_gap:.4f}, Lexical match: {has_lexical_match}")

        confidence_reason = []
        confidence_score = 0.0

        if has_lexical_match:
            confidence_score += 0.4
            confidence_reason.append("lexical match")

        if top_score > 0.05:
            confidence_score += 0.3
            confidence_reason.append(f"RRF score {top_score:.3f}")

        if score_gap > min_score_gap:
            confidence_score += 0.2
            confidence_reason.append(f"score gap {score_gap:.3f}")

        if top_score < min_rrf_score:
            print(f"âš ď¸Ź Low confidence: RRF score {top_score:.4f} < {min_rrf_score}")
            return [], {
                'has_results': True,
                'has_lexical_match': has_lexical_match,
                'confidence': top_score,
                'reason': f'Low RRF score ({top_score:.4f} < {min_rrf_score})'
            }

        print(f"âś… Confidence: {confidence_score:.2f} ({', '.join(confidence_reason)})")

        return sorted_chunks[:limit], {
            'has_results': True,
            'has_lexical_match': has_lexical_match,
            'confidence': confidence_score,
            'rrf_score': top_score,
            'score_gap': score_gap,
            'reason': ', '.join(confidence_reason)
        }

    except Exception as e:
        logger.exception("Error in hybrid retrieval: %s", e)
        return [], {'error': str(e)}


# ============================================================
# RETRIEVAL CONFIDENCE GATE
# ============================================================

def should_generate_answer(
    chunks: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    min_confidence: float = 0.1,
) -> Tuple[bool, str]:
    if not chunks:
        return False, "No chunks retrieved"

    confidence = metadata.get('confidence', 0.0)
    has_lexical_match = metadata.get('has_lexical_match', False)

    if has_lexical_match:
        if confidence > min_confidence:
            return True, f"Lexical match with confidence {confidence:.2f}"
        else:
            return False, f"Lexical match but low confidence {confidence:.2f}"

    if confidence < min_confidence:
        return False, f"No lexical match, confidence {confidence:.2f} < {min_confidence}"

    # Skip anchors when computing top score â€” they have no real similarity.
    scored = [
        c for c in chunks
        if not c.get('is_anchor')
        and (c.get('adjusted_similarity') is not None or c.get('similarity') is not None)
    ]
    if scored:
        top_score = max(
            (c.get('adjusted_similarity')
             if c.get('adjusted_similarity') is not None
             else c.get('similarity', 0)) or 0
            for c in scored
        )
    else:
        top_score = 0.0

    if top_score < 0.1:
        return False, f"Top score too low: {top_score:.3f}"

    return True, f"Sufficient confidence: {confidence:.2f}, top score: {top_score:.3f}"


def _structured_evidence(
    plan: QueryPlan,
    chat_id: str,
    request_id: str,
    source_url_filter: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run only allow-listed, parameterized global operations over this chat."""
    started = perf_counter()
    entity = plan.entity or "content"

    if plan.intent == QueryIntent.COUNT:
        if entity == "page":
            logger.info("[PAGE COUNT]")
            rows = execute_query(
                COUNT_PAGES_QUERY,
                (chat_id,),
            )
            result = int(rows[0]["result"] or 0) if rows else 0
        else:
            logger.info("[ELSE]")
            rows = execute_query(
                COUNT_ENTITY_QUERY,
                (chat_id, entity),
            )
            result = int(rows[0]["result"] or 0) if rows else 0

            # Fallback: the entity name might not exist in the DB even
            # though the user is clearly counting items on a page that
            # has a detected ordinal collection (e.g. "how many blogs
            # are listed?" on a top-N list page where chunks were typed
            # as entity_type='content'). Count the dominant collection.
            if result == 0 and source_url_filter:
                rows = execute_query(
                    COUNT_ORDINAL_COLLECTION_QUERY,
                    (
                        chat_id, source_url_filter, source_url_filter,
                        chat_id, source_url_filter, source_url_filter,
                    ),
                )
                ordinal_result = int(rows[0]["result"] or 0) if rows else 0
                if ordinal_result > 0:
                    logger.info(
                        "[STRUCTURED] request_id=%s count fallback used: "
                        "entity=%s returned 0, dominant collection=%s",
                        request_id, entity, ordinal_result,
                    )
                    result = ordinal_result
                    entity = "item"

        logger.info(
            "[STRUCTURED] request_id=%s operation=count entity=%s source_url=%s result=%s complete=true latency_ms=%.1f",
            request_id, entity, source_url_filter, result, (perf_counter() - started) * 1000,
        )
        return [{
            "chunk_id": f"structured-{request_id}",
            "content": f"Verified result: there are {result} {entity}{'' if result == 1 else 's'}.",
            "entity_type": entity,
            "evidence_type": "structured_count",
            "operation": "count",
            "result": result,
            "complete": True,
            "retrieval_strategy": "structured_count",
        }], {
            "has_results": True,
            "confidence": 1.0,
            "complete": True,
            "strategy": "structured_count",
        }

    if plan.intent in {QueryIntent.FILTER, QueryIntent.SORT, QueryIntent.AGGREGATION} and entity == "product":
        rows = execute_query(
            PRODUCT_RECORDS_QUERY,
            (chat_id,),
        )
        records = []
        for row in rows:
            price_match = re.search(r"(?:price\s*:\s*|[$ÂŁâ‚¬])\s*([0-9]+(?:[.,][0-9]{1,2})?)", row.get("content", ""), re.I)
            if not price_match:
                continue
            try:
                price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                continue
            records.append(dict(row, price=price))

        if plan.intent == QueryIntent.FILTER:
            condition = plan.filters.get("price", {})
            threshold = float(condition.get("value", 0))
            operator = condition.get("operator")
            records = [record for record in records if
                       (record["price"] < threshold if operator == "lt" else record["price"] > threshold)]
        elif plan.intent == QueryIntent.SORT:
            records.sort(key=lambda record: record["price"], reverse=plan.sort_order == "desc")

        if plan.intent == QueryIntent.AGGREGATION:
            if not records:
                return [], {"has_results": False, "complete": False, "strategy": "structured_aggregation_unavailable"}
            result = sum(record["price"] for record in records) / len(records)
            evidence = [{
                "chunk_id": f"structured-{request_id}",
                "content": f"Verified average product price: {result:.2f} across {len(records)} products.",
                "operation": "average",
                "result": round(result, 2),
                "complete": True,
                "retrieval_strategy": "structured_aggregation",
            }]
        else:
            evidence = [dict(record, content=f"Name and price evidence:\n{record['content']}",
                             evidence_type="structured_product", complete=True,
                             retrieval_strategy="structured_filter_or_sort") for record in records]
        logger.info(
            "[STRUCTURED] request_id=%s operation=%s entity=product records=%s complete=true latency_ms=%.1f",
            request_id, plan.intent.value, len(evidence), (perf_counter() - started) * 1000,
        )
        return evidence, {
            "has_results": bool(evidence), "confidence": 1.0, "complete": True,
            "strategy": "structured_filter_or_sort",
        }

    if plan.intent in {QueryIntent.LIST, QueryIntent.EXHAUSTIVE_SEARCH} and entity != "content":
        mention_match = re.search(r"\bmention(?:s|ing)?\s+(.+)$", plan.rewritten_query or "", re.I)
        search_terms = mention_match.group(1).strip(" ?.! ") if mention_match else None
        keyword_clause = ""
        keyword_params: List[Any] = []
        if search_terms:
            keyword_clause = " AND c.content_tsv @@ plainto_tsquery('english', %s)"
            keyword_params.append(search_terms)
        rows = execute_query(
            build_entity_list_query(keyword_clause),
            (chat_id, entity, *keyword_params),
        )
        evidence = [dict(row, evidence_type="structured_list", complete=True,
                         retrieval_strategy="structured_list") for row in rows]
        logger.info(
            "[STRUCTURED] request_id=%s operation=list entity=%s records=%s complete=true latency_ms=%.1f",
            request_id, entity, len(evidence), (perf_counter() - started) * 1000,
        )
        return evidence, {
            "has_results": bool(evidence),
            "confidence": 1.0,
            "complete": True,
            "strategy": "structured_list",
        }

    logger.info(
        "[STRUCTURED] request_id=%s operation=unavailable entity=%s complete=false latency_ms=%.1f",
        request_id, entity, (perf_counter() - started) * 1000,
    )
    return [], {"has_results": False, "complete": False, "strategy": "structured_unavailable"}


def validate_answer(answer: str, evidence: List[Dict[str, Any]], request_id: str) -> Tuple[bool, str]:
    """Reject numeric drift for deterministic evidence before returning an answer."""
    structured_counts = [item["result"] for item in evidence if item.get("operation") == "count"]
    if structured_counts:
        expected = str(structured_counts[0])
        if not re.search(rf"(?<!\d){re.escape(expected)}(?!\d)", answer or ""):
            logger.warning("[VALIDATOR] request_id=%s status=failed expected_count=%s", request_id, expected)
            return False, INFORMATIONAL_FALLBACK
    logger.info("[VALIDATOR] request_id=%s status=passed", request_id)
    return True, answer


# ============================================================
# GENERATION
# ============================================================

def generate_response(
    user_question: str,
    context_chunks: List[Dict[str, Any]],
    chat_history: List[Dict[str, Any]] = None,
    chat_state: Optional[Dict[str, Any]] = None,
    ranked_list_block: Optional[str] = None,
) -> str:
    user_prompt = build_user_prompt(
        user_question, context_chunks, chat_state, ranked_list_block,
    )

    history_messages = [
        {
            "role": message["role"],
            "content": message["content"]
        }
        for message in (chat_history or [])
        if message.get("role") in {"user", "assistant"}
        and message.get("content")
    ]

    try:
        client = Client(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + os.getenv("OLLAMA_API_KEY")}
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history_messages,
            {"role": "user", "content": user_prompt}
        ]

        response = client.chat(
            model="gpt-oss:20b",
            messages=messages,
            stream=False,
            options={
                "temperature": 0.2,
                "top_p": 0.85,
                "num_predict": 2048,
            }
        )

        answer = response['message']['content']
        return answer

    except Exception as e:
        return format_generation_error(e)


# ============================================================
# HELPERS FOR THE MAIN ENTRY POINT
# ============================================================

def _run_hybrid_retrieval(
    *,
    standalone_question: str,
    chat_id: str,
    source_url_filter: Optional[str],
    entity_filter: Optional[str],
    user_question: str,
    chat_history: Optional[List[Dict[str, Any]]],
    chat_state: Dict[str, Any],
    plan: Optional[QueryPlan],
    is_ordinal_question: bool = False,
    limit: int = 10,
) -> Optional[str]:
    """
    Run hybrid retrieval + generation.

    Returns the generated answer, or None if it should be treated as a
    failure (e.g., no embedding, no chunks, blocked by confidence gate).
    """
    embedding = get_embedding(standalone_question)
    if not embedding:
        return None

    # Entity detection (simple pattern-based)
    entity_patterns = [
        r'~([a-zA-Z0-9_]+)',
        r'([A-Z][a-z]+[A-Z][a-z]+)',
    ]
    detected_entity = entity_filter
    for pattern in entity_patterns:
        matches = re.findall(pattern, standalone_question)
        if matches:
            detected_entity = matches[0]
            print(f"đźŽŻ Detected entity: {detected_entity}")
            break

    chunks, metadata = retrieve_relevant_chunks_hybrid(
        standalone_question,
        embedding,
        chat_id=chat_id,
        limit=limit,
        entity_filter=detected_entity,
        source_url_filter=source_url_filter,
        min_similarity=MIN_SIMILARITY_THRESHOLD,
    )

    store_retrieved_chunks(
        chat_id,
        user_question,
        standalone_question,
        chunks,
        metadata,
    )
    logger.info(
        "[EVIDENCE] type=chunks count=%s complete=false source_count=%s",
        len(chunks),
        len({item.get("source_url") for item in chunks if item.get("source_url")}),
    )

    # Prepend page anchors so the generator always has page shape.
    chunks = augment_with_page_anchors(chunks, chat_id, source_url_filter)

    should_generate, reason = should_generate_answer(chunks, metadata)
    if not should_generate:
        print(f"đźš« Generation blocked: {reason}")
        return None

    if not chunks:
        return None

    print(f"đź“Š Using {len(chunks)} chunks for generation:")
    for i, chunk in enumerate(chunks[:3]):
        entity = chunk.get('entity_type', 'unknown')
        category = chunk.get('chunk_category', 'unknown')
        similarity = chunk.get('adjusted_similarity', chunk.get('similarity', 0)) or 0
        source = chunk.get('source_url', 'unknown')[:50]
        content_preview = chunk.get('content', '')[:80].replace('\n', ' ')
        print(f"  #{i+1}: [{entity}] [{category}] sim:{similarity:.4f} - {content_preview}...")
        print(f"      Source: {source}")

    ranked_block = None
    if is_ordinal_question:
        ranked_block = _build_ranked_list_block(chat_id, source_url_filter)

    response = generate_response(
        user_question, chunks, chat_history, chat_state=chat_state,
        ranked_list_block=ranked_block,
    )
    print("âś… Generated response:", response)

    if not response or not response.strip():
        return None

    valid, response = validate_answer(response, chunks, request_id="hybrid")
    return response


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def answer_user_question(
    user_question: str,
    chat_id: str,
    project_id: str = None,
    page_id: str = None,
    chat_history: List[Dict[str, Any]] = None,
    attempt: int = 1,
    use_hybrid_search: bool = True,
    entity_filter: Optional[str] = None
) -> str:
    """
    Complete RAG pipeline with hybrid retrieval and confidence gating.

    Routing order:
      1. Structured (count / list / filter / aggregate)
      2. Ordinal ("first", "number 1", "most popular", "item N", ...) â€” using
         the universal `ordinal_index` column populated at ingestion, scoped
         to the dominant collection per page.
      3. Hybrid (vector + keyword + RRF)

    If structured or ordinal produce an unhelpful answer, the pipeline falls
    back to hybrid retrieval before giving up.
    """
    if not user_question or not user_question.strip():
        return "Please provide a valid question."

    if attempt > 3:
        return "I'm having trouble processing your question. Please try again later."

    request_id = str(uuid4())
    logger.info("[QUERY] request_id=%s query_received chat_id=%s project_id=%s", request_id, chat_id, project_id)

    try:
        # ---- Load chat state up front ----
        chat_state = load_chat_state(chat_id)

        standalone_question = create_standalone_question(
            user_question,
            chat_history,
            chat_state=chat_state,
        )
        logger.info("[QUERY] request_id=%s normalized_query=%s", request_id, standalone_question[:200])

        plan_started = perf_counter()
        plan = QueryAnalyzer.analyze_with_retry(
            user_question,
            standalone_question,
            max_attempts=3,
        )
        print(
            f"[ANALYZER] request_id={request_id} intent={plan.intent.value} scope={plan.scope.value} entity={plan.entity} filters={plan.filters} exhaustive={plan.exhaustive} semantic_search={plan.semantic_search} structured_search={plan.structured_search} confidence={plan.confidence:.2f} latency_ms={(perf_counter() - plan_started) * 1000:.1f}"
        )
        logger.info("[PLAN] request_id=%s validation=passed", request_id)

        # ---- Handle uncountable counts ----
        #
        # If the analyzer says the user wants a count but the entity is
        # uncountable (content, section, summary, ...), try two things
        # before punting:
        #   1. Inherit the entity from chat state (a prior turn's entity).
        #   2. If a source_url is resolvable and a dominant ordinal
        #      collection exists, count that collection instead.
        #
        # Only ask for clarification if both fail.
        if plan.intent == QueryIntent.COUNT and plan.entity in UNCOUNTABLE_ENTITIES:
            inherited = chat_state.get("active_entity")
            if inherited and inherited not in UNCOUNTABLE_ENTITIES:
                logger.warning(
                    "[ANALYZER] request_id=%s ungrounded_count entity=%s -> inherited=%s",
                    request_id, plan.entity, inherited,
                )
                plan.entity = inherited
                plan.structured_search = True
                plan.semantic_search = False

        # ---- Resolve source_url_filter ----
        source_url_filter = None
        url_match = re.search(r"https?://[^\s]+", standalone_question)
        if url_match:
            source_url_filter = url_match.group(0).rstrip(".,!?)]}")
        elif chat_state.get("active_source_url"):
            source_url_filter = chat_state["active_source_url"]

        # Second pass on the uncountable-count case now that we know the
        # source URL: if the page has a dominant ordinal collection, count
        # that and answer directly. Otherwise, ask for clarification.
        if (
            plan.intent == QueryIntent.COUNT
            and plan.entity in UNCOUNTABLE_ENTITIES
            and source_url_filter
        ):
            dom = _dominant_collection(chat_id, source_url_filter)
            if dom and dom["span"] > 0:
                logger.info(
                    "[ANALYZER] request_id=%s ungrounded_count entity=%s "
                    "-> dominant collection (%s/%s, span=%s)",
                    request_id, plan.entity,
                    dom["chunk_type"], dom["entity_type"], dom["span"],
                )
                plan.entity = "item"
                plan.structured_search = True
                plan.semantic_search = False

        # Final fallback: nothing recoverable, ask the user.
        if plan.intent == QueryIntent.COUNT and plan.entity in UNCOUNTABLE_ENTITIES:
            logger.warning(
                "[ANALYZER] request_id=%s ungrounded_count entity=%s -> clarify",
                request_id, plan.entity,
            )
            return (
                "I want to make sure I count the right thing. "
                "Which items should I count? For example: countries, blogs, "
                "products, or table rows on a specific page."
            )

        # ============================================================
        # 1. Structured path
        # ============================================================
        if plan.structured_search:
            logger.info("[ROUTER] request_id=%s strategy=structured reason=%s", request_id, plan.intent.value)
            chunks, metadata = _structured_evidence(
                plan, chat_id, request_id, source_url_filter=source_url_filter,
            )
            if chunks:
                logger.info("[EVIDENCE] request_id=%s type=structured count=%s complete=%s source_count=%s",
                            request_id, len(chunks), metadata.get("complete", False),
                            len({item.get("source_url") for item in chunks if item.get("source_url")}))
                response = generate_response(
                    user_question, chunks, chat_history, chat_state=chat_state,
                )
                valid, response = validate_answer(response, chunks, request_id)

                # If structured answer was unhelpful, try hybrid before giving up.
                if _is_unhelpful(response):
                    logger.info(
                        "[ROUTER] request_id=%s structured_unhelpful -> hybrid fallback",
                        request_id,
                    )
                    hybrid = _run_hybrid_retrieval(
                        standalone_question=standalone_question,
                        chat_id=chat_id,
                        source_url_filter=source_url_filter,
                        entity_filter=entity_filter,
                        user_question=user_question,
                        chat_history=chat_history,
                        chat_state=chat_state,
                        plan=plan,
                    )
                    if hybrid:
                        response = hybrid

                save_chat_state(chat_id, {
                    "active_source_url": source_url_filter or chat_state.get("active_source_url"),
                    "active_entity": plan.entity or chat_state.get("active_entity"),
                    "active_entity_attributes": chat_state.get("active_entity_attributes", []),
                    "last_answer": {"text": (response or "")[:500]},
                })
                return response

            logger.warning("[ROUTER] request_id=%s structured_unavailable fallback=hybrid", request_id)
            hybrid = _run_hybrid_retrieval(
                standalone_question=standalone_question,
                chat_id=chat_id,
                source_url_filter=source_url_filter,
                entity_filter=entity_filter,
                user_question=user_question,
                chat_history=chat_history,
                chat_state=chat_state,
                plan=plan,
            )
            if hybrid:
                save_chat_state(chat_id, {
                    "active_source_url": source_url_filter or chat_state.get("active_source_url"),
                    "active_entity": plan.entity or chat_state.get("active_entity"),
                    "active_entity_attributes": chat_state.get("active_entity_attributes", []),
                    "last_answer": {"text": hybrid[:500]},
                })
                return hybrid
            clar = build_clarification(user_question, chunks, metadata, chat_state)
            save_chat_state(chat_id, {
                "active_source_url": source_url_filter or chat_state.get("active_source_url"),
                "active_entity": plan.entity or chat_state.get("active_entity"),
                "active_entity_attributes": chat_state.get("active_entity_attributes", []),
                "last_answer": {"text": clar[:500]},
            })
            return clar

        # ============================================================
        # 2. Ordinal path (universal, based on ordinal_index)
        # ============================================================
        ordinal = detect_ordinal_intent(standalone_question)
        if ordinal and source_url_filter:
            kind, index = ordinal
            picked = fetch_ordinal_chunk(chat_id, source_url_filter, kind, index)
            if picked:
                logger.info(
                    "[ROUTER] request_id=%s strategy=ordinal kind=%s index=%s chunk=%s ordinal_index=%s",
                    request_id, kind, index, picked.get("chunk_id"), picked.get("ordinal_index"),
                )
                chunks = [picked]
                metadata = {
                    "has_results": True,
                    "has_lexical_match": True,
                    "confidence": 1.0,
                    "reason": f"ordinal {kind} index={index}",
                }

                ranked_block = _build_ranked_list_block(chat_id, source_url_filter)

                response = generate_response(
                    user_question, chunks, chat_history, chat_state=chat_state,
                    ranked_list_block=ranked_block,
                )
                valid, response = validate_answer(response, chunks, request_id)

                # If the ordinal chunk wasn't enough, fall through to hybrid.
                if _is_unhelpful(response):
                    logger.info(
                        "[ROUTER] request_id=%s ordinal_unhelpful chunk=%s -> hybrid fallback",
                        request_id, picked.get("chunk_id"),
                    )
                    hybrid = _run_hybrid_retrieval(
                        standalone_question=standalone_question,
                        chat_id=chat_id,
                        source_url_filter=source_url_filter,
                        entity_filter=entity_filter,
                        user_question=user_question,
                        chat_history=chat_history,
                        chat_state=chat_state,
                        plan=plan,
                        is_ordinal_question=True,
                    )
                    if hybrid:
                        response = hybrid

                save_chat_state(chat_id, {
                    "active_source_url": source_url_filter,
                    "active_entity": plan.entity or chat_state.get("active_entity"),
                    "active_entity_attributes": chat_state.get("active_entity_attributes", []),
                    "last_answer": {"text": (response or "")[:500]},
                })
                return response
            else:
                logger.info(
                    "[ROUTER] request_id=%s ordinal=%s but no ordinal chunks found for %s",
                    request_id, kind, source_url_filter,
                )

        # ============================================================
        # 3. Hybrid path
        # ============================================================
        logger.info("[ROUTER] request_id=%s strategy=hybrid reason=semantic_or_structured_fallback", request_id)
        hybrid = _run_hybrid_retrieval(
            standalone_question=standalone_question,
            chat_id=chat_id,
            source_url_filter=source_url_filter,
            entity_filter=entity_filter,
            user_question=user_question,
            chat_history=chat_history,
            chat_state=chat_state,
            plan=plan,
            is_ordinal_question=bool(ordinal),
        )
        if hybrid:
            save_chat_state(chat_id, {
                "active_source_url": source_url_filter or chat_state.get("active_source_url"),
                "active_entity": plan.entity or chat_state.get("active_entity"),
                "active_entity_attributes": chat_state.get("active_entity_attributes", []),
                "last_answer": {"text": hybrid[:500]},
            })
            return hybrid

        # Hybrid failed â€” actionable clarification.
        clar = build_clarification(user_question, [], {}, chat_state)
        save_chat_state(chat_id, {
            "active_source_url": source_url_filter or chat_state.get("active_source_url"),
            "active_entity": plan.entity or chat_state.get("active_entity"),
            "active_entity_attributes": chat_state.get("active_entity_attributes", []),
            "last_answer": {"text": clar[:500]},
        })
        return clar

    except Exception as e:
        logger.exception("[PIPELINE] request_id=%s component=retrieval operation=answer", request_id)
        return "I couldn't process that question safely right now. Please try again later."


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def check_embedding_dimension() -> Dict[str, Any]:
    result = execute_query(
        EMBEDDING_SAMPLE_QUERY
    )
    if result and result[0].get('embedding_sample'):
        sample = result[0]['embedding_sample']
        dims = sample.count(',') + 1
        return {'dimension': dims}
    return {'dimension': 0}


def get_available_categories(chat_id: str) -> Dict[str, int]:
    result = execute_query(
        AVAILABLE_CATEGORIES_QUERY,
        (chat_id,)
    )
    return {row['chunk_category']: row['count'] for row in result} if result else {}


def get_chunk_stats(chat_id: str) -> Dict[str, Any]:
    result = execute_query(
        CHUNK_STATS_QUERY,
        (chat_id,)
    )
    return dict(result[0]) if result else {}