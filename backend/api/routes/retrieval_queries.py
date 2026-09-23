"""Parameterized SQL used by the retrieval pipeline.

This module owns query text only. Values remain parameters supplied to
``database_sync.execute_query``; user input must never be interpolated here.

Scoping convention:
    Every retrieval query scopes by ``chunks.chat_id`` directly. The
    ``chat_id``, ``project_id``, and ``source_url`` columns are
    denormalized from ``pages`` at ingestion time, so joining back to
    ``pages`` (or reading ``documents.metadata->>'url'``) is redundant
    and can drift out of sync. Prefer the denormalized columns.
"""

from typing import Any, List, Tuple


# ============================================================
# Counts and metadata
# ============================================================

COUNT_PAGES_QUERY = "SELECT COUNT(*) AS result FROM pages WHERE chat_id = %s"


# Counts distinct content across chunks that share the same entity_type
# within a chat. Scoped optionally by source_url via the caller, which
# appends a `AND ...` fragment via build_entity_filter_clauses.
COUNT_ENTITY_QUERY = """
    SELECT COUNT(DISTINCT c.content) AS result
    FROM chunks c
    WHERE c.chat_id = %s AND c.entity_type = %s
"""

COUNT_ORDINAL_COLLECTION_QUERY = """
    WITH dominant AS (
        SELECT chunk_type, entity_type
        FROM chunks
        WHERE chat_id = %s
          AND (
              %s IS NULL
              OR regexp_replace(COALESCE(source_url, ''), '/+$', '')
                 = regexp_replace(%s, '/+$', '')
          )
          AND ordinal_index IS NOT NULL
        GROUP BY chunk_type, entity_type
        ORDER BY COUNT(DISTINCT ordinal_index) DESC, COUNT(*) DESC
        LIMIT 1
    )
    SELECT COUNT(DISTINCT c.ordinal_index) AS result
    FROM chunks c
    JOIN dominant d
      ON c.chunk_type = d.chunk_type
     AND c.entity_type = d.entity_type
    WHERE c.chat_id = %s
      AND (
          %s IS NULL
          OR regexp_replace(COALESCE(c.source_url, ''), '/+$', '')
             = regexp_replace(%s, '/+$', '')
      )
      AND c.ordinal_index IS NOT NULL
"""

PRODUCT_RECORDS_QUERY = """
    SELECT DISTINCT ON (c.content)
        c.id AS chunk_id,
        c.content,
        c.source_url,
        c.entity_type
    FROM chunks c
    WHERE c.chat_id = %s AND c.entity_type = 'product'
    ORDER BY c.content, c.chunk_index
"""


EMBEDDING_SAMPLE_QUERY = """
    SELECT embedding::text as embedding_sample
    FROM chunks
    WHERE embedding IS NOT NULL
    LIMIT 1
"""


AVAILABLE_CATEGORIES_QUERY = """
    SELECT c.chunk_category, COUNT(*) as count
    FROM chunks c
    WHERE c.embedding_status = 'COMPLETED'
      AND c.embedding IS NOT NULL
      AND c.chat_id = %s
    GROUP BY c.chunk_category
"""


CHUNK_STATS_QUERY = """
    SELECT
        COUNT(*) as total_chunks,
        AVG(c.information_density) as avg_info_density,
        SUM(CASE WHEN c.entity_type = 'product' THEN 1 ELSE 0 END) as product_count,
        SUM(CASE WHEN c.entity_type = 'faq' THEN 1 ELSE 0 END) as faq_count
    FROM chunks c
    WHERE c.embedding_status = 'COMPLETED'
      AND c.embedding IS NOT NULL
      AND c.chat_id = %s
"""


# ============================================================
# Filter clause builders
# ============================================================

def build_entity_filter_clauses(
    entity_filter: str | None,
    source_url_filter: str | None,
) -> Tuple[str, List[Any], str, List[Any]]:
    """
    Build the optional WHERE fragments for entity and source-url scoping.

    Returns:
        entity_clause, entity_params, source_url_clause, source_url_params

    The two clauses are designed to be appended to a query that already
    contains ``WHERE c.chat_id = %s ...``. Callers must place the
    ``entity_params`` and ``source_url_params`` in the same order as the
    fragments appear in the SQL text.

    Source-url matching is done against ``chunks.source_url`` — the
    canonical value denormalized from ``pages.url`` at ingestion — and
    is normalized on both sides so trailing slashes do not cause a
    mismatch.
    """
    entity_clause = ""
    entity_params: List[Any] = []
    if entity_filter:
        # Match against chunk content and heading path so the filter works
        # without joining back to documents. Case-insensitive substring.
        entity_clause = """
            AND (
                c.content ILIKE %s
                OR c.section_title ILIKE %s
                OR c.heading_path::text ILIKE %s
            )
        """
        entity_params = [
            f"%{entity_filter}%",
            f"%{entity_filter}%",
            f"%{entity_filter}%",
        ]

    source_url_clause = ""
    source_url_params: List[Any] = []
    if source_url_filter:
        # Normalize both sides so "…/50" and "…/50/" match each other.
        # Uses chunks.source_url, which is populated from pages.url at
        # ingestion time. Falls back to documents.metadata->>'url' only
        # when the denormalized column is NULL (legacy rows ingested
        # before the refactor).
        source_url_clause = """
            AND regexp_replace(
                    COALESCE(c.source_url, ''),
                    '/+$', ''
                ) = regexp_replace(%s, '/+$', '')
        """
        source_url_params = [source_url_filter.rstrip("/") or source_url_filter]

    return entity_clause, entity_params, source_url_clause, source_url_params


# ============================================================
# Vector and keyword queries
# ============================================================

def build_vector_query(entity_clause: str = "", source_url_clause: str = "") -> str:
    """
    Vector similarity search over a single chat's chunks.

    Parameter order (must match the caller in retrieve_relevant_chunks_hybrid):
        %s  query_embedding (as a [v1,v2,...] literal)
        %s  chat_id
        ... entity_params
        ... source_url_params
        %s  query_embedding (again, for ORDER BY)
        %s  limit
    """
    return f"""
        SELECT
            c.id AS chunk_id,
            c.content,
            c.chunk_category,
            c.entity_type,
            c.section_title,
            c.information_density,
            c.heading_path,
            c.chunk_index,
            c.position_in_page,
            c.ordinal_index,
            c.document_id,
            c.source_url,
            1 - (c.embedding <=> %s::halfvec) AS similarity
        FROM chunks c
        WHERE c.embedding_status = 'COMPLETED'
            AND c.embedding IS NOT NULL
            AND c.chat_id = %s
            AND c.chunk_category NOT IN ('excluded', 'filter', 'modal', 'cookie')
            AND COALESCE(c.information_density, 1.0) >= 0.01
            {entity_clause}
            {source_url_clause}
        ORDER BY c.embedding <=> %s::halfvec
        LIMIT %s
    """


def build_keyword_query(entity_clause: str = "", source_url_clause: str = "") -> str:
    """
    Lexical full-text search over a single chat's chunks.

    Parameter order (must match the caller in retrieve_relevant_chunks_hybrid):
        %s  query_text           (for ts_rank)
        %s  query_text           (for the @@ match)
        %s  chat_id
        ... entity_params
        ... source_url_params
        %s  limit
    """
    return f"""
        SELECT
            c.id AS chunk_id,
            c.content,
            c.chunk_category,
            c.entity_type,
            c.section_title,
            c.information_density,
            c.heading_path,
            c.chunk_index,
            c.position_in_page,
            c.ordinal_index,
            c.document_id,
            c.source_url,
            ts_rank(c.content_tsv, plainto_tsquery('english', %s)) AS similarity
        FROM chunks c
        WHERE c.embedding_status = 'COMPLETED'
            AND c.content_tsv IS NOT NULL
            AND c.content_tsv @@ plainto_tsquery('english', %s)
            AND c.chat_id = %s
            AND c.chunk_category NOT IN ('excluded', 'filter', 'modal', 'cookie')
            AND COALESCE(c.information_density, 1.0) >= 0.01
            {entity_clause}
            {source_url_clause}
        ORDER BY similarity DESC
        LIMIT %s
    """


def build_entity_list_query(keyword_clause: str = "") -> str:
    """
    Return distinct chunks of a given entity_type for a chat.

    Parameter order (must match _structured_evidence):
        %s  chat_id
        %s  entity_type
        ... keyword_clause params (if any)
    """
    return f"""
        SELECT DISTINCT ON (c.content)
            c.id AS chunk_id,
            c.content,
            c.entity_type,
            c.document_id,
            c.source_url,
            c.section_title
        FROM chunks c
        WHERE c.chat_id = %s AND c.entity_type = %s{keyword_clause}
        ORDER BY c.content, c.chunk_index
    """